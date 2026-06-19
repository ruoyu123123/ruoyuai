# -*- coding: utf-8 -*-
"""anachronism_scanner.py 专属回归测试（2026-06-20·确定性·零依赖·零 LLM/零联网）。

覆盖任务要求 5 个核心分支：
  ① 有 setting_era（古代）+ 现代词 + active → FAIL_MINOR（命中上报）
  ② 无 setting_era → skip（PASS·note·北极星②）
  ③ 古风干净草稿（无现代词）→ PASS
  ④ shadow 模式高密度命中也只记不判（零回归）
  ⑤ off → 返回骨架（不读草稿/不门控）

外加：辅助函数 _strip_changes / _cjk_count / _read_setting_era 全分支、
非古代 era 跳过、_mode 非法回落、main() CLI subprocess 退出码。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import anachronism_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "anachronism_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ANACHRONISM_MODE", None)
    else:
        os.environ["ANACHRONISM_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(era="__none__", *, file="世界观.json", key="era", raw=None):
    """造带 _数据库/<file> 的项目目录。
    era='__none__' → 不写 era 字段；raw 给原始对象覆盖整个 JSON。
    """
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if raw is not None:
        obj = raw
    elif era == "__none__":
        obj = {"location": "", "rules": []}
    else:
        obj = {key: era, "location": "", "rules": []}
    (proj / "_数据库" / file).write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 现代词密集草稿（古代背景里却出现 手机/电话/汽车/电脑）·>500 CJK
_MODERN_DRAFT = "他掏出手机，拨通电话，跳上汽车，打开电脑下山。" * 30
# 干净古风草稿（无任何现代词）·>500 CJK
_ANCIENT_CLEAN = "他提剑走进山门，青石阶上落满松针。师兄在丹房前煮茶，炉火映着他的眉眼。" * 25


# ── ⑤ off → 骨架 ─────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(era="古代")
        out = mod.scan(_write(_MODERN_DRAFT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
        # off 直返：不读草稿，不门控，无 per_1k / setting_era / note
        assert "per_1k" not in out and "setting_era" not in out
    finally:
        _set_mode(bak)


# ── ① 古代 + 现代词 + active → FAIL_MINOR ────────────────────────────────────
def test_active_ancient_with_modern_terms_fail_minor():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(era="古代")
        out = mod.scan(_write(_MODERN_DRAFT), project_root=proj)
        assert out["setting_era"] == "古代"
        assert out["anachronism_count"] > 0 and out["per_1k"] > 0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"] and out["violations"][0]["severity"] == "minor"
        assert out["violations"][0]["setting_era"] == "古代"
        assert out["gate_level"] == "advisory"  # 永远 advisory
        # 分类计数应抓到 网络数码 + 现代器物
        cc = out["category_counts"]
        assert cc.get("网络数码", 0) > 0 and cc.get("现代器物", 0) > 0
    finally:
        _set_mode(bak)


# ── ② 无 setting_era → skip（PASS·note）─────────────────────────────────────
def test_no_setting_era_skips_with_note():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(era="__none__")  # 世界观.json 无 era 字段
        out = mod.scan(_write(_MODERN_DRAFT), project_root=proj)
        assert out["setting_era"] is None
        assert "无 setting_era" in out.get("note", "")
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
        # 未进入检测：不算 per_1k
        assert "per_1k" not in out
    finally:
        _set_mode(bak)


def test_no_project_skips():
    """project_root=None（无门控信息）→ 同样 skip。"""
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_MODERN_DRAFT), project_root=None)
        assert out["setting_era"] is None
        assert "无 setting_era" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ③ 古风干净草稿 → PASS ────────────────────────────────────────────────────
def test_ancient_clean_draft_pass():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(era="古风修真")
        out = mod.scan(_write(_ANCIENT_CLEAN), project_root=proj)
        assert out["setting_era"] == "古风修真"
        assert out["anachronism_count"] == 0
        assert out["per_1k"] == 0
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ④ shadow 高密度命中只记不判 ─────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(era="架空古风")
        out = mod.scan(_write(_MODERN_DRAFT), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["anachronism_count"] > 0   # 确实命中
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None         # shadow 不上报
    finally:
        _set_mode(bak)


# ── 非古代 era → skip ────────────────────────────────────────────────────────
def test_modern_era_skips():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(era="现代都市")
        out = mod.scan(_write(_MODERN_DRAFT), project_root=proj)
        assert out["setting_era"] == "现代都市"
        assert "非古代/古风背景" in out.get("note", "")
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_scifi_era_skips():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(era="未来科幻")
        out = mod.scan(_write(_MODERN_DRAFT), project_root=proj)
        assert "非古代/古风背景" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── scan 短稿 / 读取失败 ─────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(era="古代")
        out = mod.scan(_write("他掏出手机。" * 5), project_root=proj)  # 远不足 500
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "per_1k" not in out
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── _strip_changes ───────────────────────────────────────────────────────────
def test_strip_changes_factual_separator():
    raw = "古风正文。\n\n---CHANGES_FACTUAL---\n{\"foo\": 1}"
    assert mod._strip_changes(raw) == "古风正文。"


def test_strip_changes_plain_separator():
    raw = "另一段。   \n---CHANGES---\nlog"
    assert mod._strip_changes(raw) == "另一段。"


def test_strip_changes_no_separator():
    raw = "纯正文无分隔符。"
    assert mod._strip_changes(raw) == raw


# ── _cjk_count ───────────────────────────────────────────────────────────────
def test_cjk_count_only_cjk():
    assert mod._cjk_count("你好世界中abc123，。") == 5


def test_cjk_count_empty():
    assert mod._cjk_count("") == 0


# ── _read_setting_era：全分支 ────────────────────────────────────────────────
def test_read_era_none_project():
    assert mod._read_setting_era(None) is None


def test_read_era_from_worldview_era_key():
    proj = _mk_project(era="大唐王朝")
    assert mod._read_setting_era(proj) == "大唐王朝"


def test_read_era_from_setting_era_key():
    proj = _mk_project(era="上古洪荒", key="setting_era")
    assert mod._read_setting_era(proj) == "上古洪荒"


def test_read_era_file_missing():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._read_setting_era(proj) is None


def test_read_era_falls_back_to_author_profile():
    """世界观.json 无 era → 退到作者风格.json 的 setting_era。"""
    proj = _mk_project(era="__none__")  # 世界观.json 无 era
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"setting_era": "仙侠"}, ensure_ascii=False), encoding="utf-8")
    assert mod._read_setting_era(proj) == "仙侠"


def test_read_era_bad_json_skips_to_next():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "世界观.json").write_text("{ bad json", encoding="utf-8")
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"era": "玄幻"}, ensure_ascii=False), encoding="utf-8")
    assert mod._read_setting_era(proj) == "玄幻"


def test_read_era_top_not_dict():
    proj = _mk_project(raw=[1, 2, 3])
    assert mod._read_setting_era(proj) is None


def test_read_era_empty_string_is_none():
    """era 字段是空串（骨架默认）→ 视为无 era。"""
    proj = _mk_project(era="")
    assert mod._read_setting_era(proj) is None


# ── _is_ancient_era ──────────────────────────────────────────────────────────
def test_is_ancient_era_positive():
    for e in ["古代", "古风修真", "仙侠", "玄幻", "武侠", "大明", "上古洪荒", "架空古风"]:
        assert mod._is_ancient_era(e), e


def test_is_ancient_era_negative():
    for e in ["现代都市", "未来科幻", "民国", "", None]:
        assert not mod._is_ancient_era(e), e


# ── detect_anachronisms ──────────────────────────────────────────────────────
def test_detect_returns_sorted_categorized_hits():
    hits = mod.detect_anachronisms("先有汽车后有手机。")
    assert [h["term"] for h in hits] == ["汽车", "手机"]  # 按 pos 排序
    cats = {h["category"] for h in hits}
    assert "现代器物" in cats and "网络数码" in cats


def test_detect_clean_no_hits():
    assert mod.detect_anachronisms("他提剑走进山门，青石阶上落满松针。") == []


# ── _mode 非法回落 ──────────────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")  # 大小写不敏感
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_shadow_when_unset():
    bak = os.environ.get("ANACHRONISM_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── main() CLI subprocess 退出码 ────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ANACHRONISM_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(era="古代")
    p = _write(_MODERN_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean_ancient():
    proj = _mk_project(era="古风")
    p = _write(_ANCIENT_CLEAN)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"
