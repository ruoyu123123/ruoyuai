"""feedback 全局规则 frozen 断裂修复回归测试（2026-06-13）。

钉死：gen_writer._collect_feedback_rules / build_manifest._collect_global_feedback_must_read
读开发机 ~/.claude/projects/D--Desktop-ruoyuai/memory/feedback_*.md —— frozen exe 用户机
该路径不存在 → writer 防御层此前整层静默为空。修复 = home miss/为空时 fallback 读随 exe
出货的汇编 core/claude-home/lessons/global_feedback_rules.md（frozen_util 定位）。

守护点：
  · home 路径 miss → fallback 装载汇编文件内容（防御层不再为空）
  · home 有 type=feedback 文件时优先 home（开发机行为零回归）
  · 汇编文件完整性（23 条规则锚 + 机械汇编 header + 无 frontmatter 残留）
  · ruoyu_gui.spec 6b 段整目录收 lessons/*.md（汇编文件自动随 exe 出货）
"""
import contextlib
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "scripts"))
import gen_writer as gw  # noqa: E402
import build_manifest as bm  # noqa: E402

_BUNDLE_FILE = _REPO / "core" / "claude-home" / "lessons" / "global_feedback_rules.md"


@contextlib.contextmanager
def _fake_home(path: Path):
    """把 Path.home()/expanduser('~') 重定向到 tmpdir（Windows 走 USERPROFILE·POSIX 走 HOME）。"""
    saved = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
    os.environ["USERPROFILE"] = str(path)
    os.environ["HOME"] = str(path)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mk_home_memory(home: Path, fname: str, body: str, desc: str = "测试规则摘要") -> Path:
    mem = home / ".claude" / "projects" / "D--Desktop-ruoyuai" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"name: {fname[:-3].replace('_', '-')}\n"
        f"description: {desc}\n"
        "metadata: \n"
        "  node_type: memory\n"
        "  type: feedback\n"
        "---\n\n"
        f"{body}\n"
    )
    p = mem / fname
    p.write_text(text, encoding="utf-8")
    return p


# ---------- 汇编文件完整性 ----------

def test_bundle_file_exists_with_rule_markers():
    """汇编文件必须存在·≥20 条规则锚·header 注明机械汇编/重跑汇编·无 frontmatter 残留。"""
    assert _BUNDLE_FILE.exists(), f"汇编文件缺失: {_BUNDLE_FILE}"
    text = _BUNDLE_FILE.read_text(encoding="utf-8")
    n_markers = text.count("<!-- FEEDBACK_RULE:")
    assert n_markers >= 20, f"规则锚仅 {n_markers} 条（应 ≥20·当前源 23 条）"
    assert text.count("> description:") == n_markers, "description 行数 ≠ 规则锚数"
    assert "机械汇编" in text and "重跑汇编" in text, "header 缺「机械汇编/重跑汇编」说明"
    assert "originSessionId" not in text, "frontmatter 套话框架未剥干净"
    # 抽查一条已知规则实质内容（一段一句·用户定稿原文）
    assert "feedback_one_sentence_per_paragraph.md" in text
    assert "一个句末标点结束符" in text


# ---------- gen_writer fallback ----------

def test_gen_writer_fallback_loads_bundle_when_home_miss():
    """home memory 路径不存在 → fallback 装载汇编文件内容（不再整层为空）。"""
    with tempfile.TemporaryDirectory() as d:
        with _fake_home(Path(d)):
            rules = gw._collect_feedback_rules()
    assert rules, "home miss 时 fallback 仍为空 = frozen 断裂未修"
    assert "全局 feedback 规则汇编" in rules, "fallback 没装载汇编文件 header"
    assert "一个句末标点结束符" in rules, "fallback 缺规则实质内容（一段一句）"


def test_gen_writer_fallback_when_home_dir_empty():
    """home memory 目录存在但无 type=feedback 可抽 → 同样 fallback（为空也算 miss）。"""
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        mem = home / ".claude" / "projects" / "D--Desktop-ruoyuai" / "memory"
        mem.mkdir(parents=True)
        # 放一个非 feedback 类型文件（不含 type: feedback → 被过滤 → chunks 为空）
        (mem / "feedback_not_typed.md").write_text(
            "---\nname: x\nmetadata: \n  type: project\n---\n\n非 feedback 内容\n",
            encoding="utf-8")
        with _fake_home(home):
            rules = gw._collect_feedback_rules()
    assert "全局 feedback 规则汇编" in rules, "home 为空时未走 bundle fallback"


def test_gen_writer_home_priority_over_bundle():
    """home 有 type=feedback 文件 → 优先 home（注入 home 内容·不混汇编 header）。"""
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        _mk_home_memory(home, "feedback_unique_marker_rule.md",
                        "UNIQUE_HOME_RULE_MARKER_8848 禁止某某行为。")
        with _fake_home(home):
            rules = gw._collect_feedback_rules()
    assert "UNIQUE_HOME_RULE_MARKER_8848" in rules, "home 路径优先被破坏"
    assert "来自 memory/feedback_*.md" in rules, "home 路径 header 丢失"
    assert "全局 feedback 规则汇编" not in rules, "home 命中时不应混入 bundle 汇编内容"


# ---------- build_manifest fallback ----------

def test_build_manifest_fallback_digest_from_bundle():
    """home miss → must_read 条目从汇编文件解析 digest（file+desc 成对）。"""
    with tempfile.TemporaryDirectory() as d:
        with _fake_home(Path(d)):
            item = bm._collect_global_feedback_must_read()
    assert item is not None, "home miss 时 must_read 条目为 None = frozen 断裂未修"
    assert "global_feedback_rules.md" in item["path"]
    assert item["priority"] == "P1"
    digest = item["digest"]
    assert len(digest) >= 20, f"digest 仅 {len(digest)} 条（应 ≥20）"
    files = {e["file"] for e in digest}
    assert "feedback_no_token_saving.md" in files
    assert all(e["desc"] for e in digest), "存在空 desc"
    assert all(len(e["desc"]) <= 160 for e in digest), "desc 超 160 截断契约"
    # 全文定位指针必须真实存在
    assert item["files"] and Path(item["files"][0]).exists()


def test_build_manifest_home_priority_over_bundle():
    """home 有带 description 的 feedback 文件 → 走原 v19.3 home digest（零回归）。"""
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        _mk_home_memory(home, "feedback_home_only.md", "正文",
                        desc="HOME_DESC_MARKER_9527")
        with _fake_home(home):
            item = bm._collect_global_feedback_must_read()
    assert item is not None
    assert item["path"] == "全局 MEMORY feedback (跨项目元教训)", "home 命中时 path 应保持原值"
    assert any(e["desc"] == "HOME_DESC_MARKER_9527" for e in item["digest"])


# ---------- 打包契约（源码级防回归） ----------

def test_spec_collects_lessons_md_dir():
    """ruoyu_gui.spec 6b 段整目录收 lessons/*.md → 汇编文件自动随 exe 出货。"""
    spec = (_REPO / "packaging" / "ruoyu_gui.spec").read_text(encoding="utf-8")
    assert 'os.path.join(ROOT, "core", "claude-home", "lessons")' in spec, \
        "spec 不再整目录收 lessons —— global_feedback_rules.md 会掉出 bundle"
    assert 'fn.endswith(".md")' in spec, "spec lessons 段 .md 过滤丢失"


def test_gen_writer_fallback_uses_frozen_util():
    """源码级钉死：fallback 必须走 frozen_util 定位（frozen=_MEIPASS·dev=仓库根）。"""
    src = (_REPO / "core" / "scripts" / "gen_writer.py").read_text(encoding="utf-8")
    assert "_collect_feedback_rules_bundle_fallback" in src
    assert 'resource_path("core", "claude-home", "lessons", "global_feedback_rules.md")' in src


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
