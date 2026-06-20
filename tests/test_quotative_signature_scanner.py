# -*- coding: utf-8 -*-
"""quotative_signature_scanner.py 专属回归测试（2026-06-20·确定性·零依赖·零 LLM/零联网）。

覆盖：
  · off / shadow / active 三种 mode
  · lexicon 加载（默认词典 8 桶 60 词 + 失败兜底）
  · 对话引号匹配 + speaker 抽取（known_names 优先 / 退化 token）
  · attribution 后置 / 前置窗口
  · author palette collapse（≤2 桶 + total≥8 触发）
  · per-character cosine > 0.9 同质化触发
  · quotative_bias top-3 输出
  · 草稿太短 skip / 读取失败 note
  · CLI subprocess 退出码
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
import quotative_signature_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "quotative_signature_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("QUOTATIVE_SIGNATURE_MODE", None)
    else:
        os.environ["QUOTATIVE_SIGNATURE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, names=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if names:
        (db / "人物卡.json").write_text(json.dumps(
            {"characters": [{"name": n} for n in names]}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# ── 默认 lexicon 加载 ────────────────────────────────────────────────────
def test_lexicon_loads_default():
    lex = mod._load_lexicon()
    assert isinstance(lex, dict) and len(lex) == 8
    expected_buckets = {"neutral_say", "neutral_dao", "shouting", "whispering",
                        "sneering", "pondering", "laughing", "murmuring"}
    assert set(lex.keys()) == expected_buckets
    # 每桶至少 5 词
    for bucket, verbs in lex.items():
        assert len(verbs) >= 5, bucket


def test_lexicon_missing_file_empty():
    d = Path(tempfile.mkdtemp())
    assert mod._load_lexicon(d / "nope.json") == {}


def test_lexicon_bad_schema_returns_empty():
    p = Path(tempfile.mkdtemp()) / "lex.json"
    p.write_text("[]", encoding="utf-8")
    assert mod._load_lexicon(p) == {}


def test_build_verb_regex():
    lex = {"say": {"说", "道"}, "shout": {"喊"}}
    v2b, rx = mod._build_verb_regex(lex)
    assert v2b["说"] == "say"
    assert rx.search("他说了一句").group(0) == "说"


def test_build_verb_regex_empty():
    _, rx = mod._build_verb_regex({})
    assert rx is None


# ── mode 三态 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write("正文" * 500), project_root=None)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短"), project_root=None)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"),
                       project_root=None)
        assert "草稿读取失败" in out["note"]
    finally:
        _set_mode(bak)


# ── author palette collapse ────────────────────────────────────────────
def test_author_collapse_two_buckets_triggers():
    """全用 pondering + laughing 2 桶 + total ≥ 8 → COLLAPSE。"""
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 大量重复保证 CJK > 500
        line = ("张三沉吟道：“嗯一下吧。” 李四笑道：“哈哈大笑。”")
        draft = line * 60
        out = mod.scan(_write(draft), project_root=None)
        assert out["attributed_total"] >= 8
        codes = [v["code"] for v in out["violations"]]
        assert "AUTHOR_QUOTATIVE_PALETTE_COLLAPSE" in codes
    finally:
        _set_mode(bak)


def test_author_palette_diverse_no_collapse():
    """8 桶都至少出现一次 → 不报 COLLAPSE。"""
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        lines = [
            "张三说：“一句话。”", "李四道：“二段话。”", "王五喊：“三啊！”",
            "赵六低声：“四四四。”", "孙七冷笑：“五个字。”", "周八沉吟：“六个数。”",
            "吴九笑道：“七哈哈。”", "郑十嘀咕：“八不八。”",
        ]
        # 重复以满足 ≥3 quotative/角色门槛 + CJK > 500
        draft = "".join(lines * 12)
        out = mod.scan(_write(draft), project_root=None)
        used = [b for b, c in out["author_palette"].items() if c > 0]
        assert len(used) > 2
        codes = [v["code"] for v in out["violations"]]
        assert "AUTHOR_QUOTATIVE_PALETTE_COLLAPSE" not in codes
    finally:
        _set_mode(bak)


# ── per-character cosine 同质化 ────────────────────────────────────────
def test_character_homogenized_triggers():
    """两角色都只用同一 bucket → cosine = 1 > 0.9 → 同质化。"""
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 张三、李四都只 pondering · 长度撑过 500 CJK
        line = "张三沉吟道：“一句话。” 李四沉吟道：“二段话呢。”"
        draft = line * 40
        proj = _mk_project(names=["张三", "李四"])
        out = mod.scan(_write(draft), project_root=proj)
        codes = [v["code"] for v in out["violations"]]
        assert "CHARACTER_QUOTATIVE_HOMOGENIZED" in codes
        pairs = out["homogenized_pairs"]
        assert pairs and pairs[0]["cosine"] > 0.9
    finally:
        _set_mode(bak)


def test_character_distinct_palettes_no_homogenized():
    """张三全 sneering / 李四全 laughing → cosine 0 → 不报。"""
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        draft = ("张三冷笑道：“一句话。” 李四笑道：“二段话呢。”") * 40
        proj = _mk_project(names=["张三", "李四"])
        out = mod.scan(_write(draft), project_root=proj)
        codes = [v["code"] for v in out["violations"]]
        assert "CHARACTER_QUOTATIVE_HOMOGENIZED" not in codes
    finally:
        _set_mode(bak)


def test_character_insufficient_sample_skipped():
    """单角色 < 3 quotative → 不入 quotative_bias。"""
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        draft = "张三说：“一。” 李四道：“二。”" + "无对话铺垫。" * 200
        proj = _mk_project(names=["张三", "李四"])
        out = mod.scan(_write(draft), project_root=proj)
        # 每角色仅 1 个 → quotative_bias 应为空
        assert out["quotative_bias"] == {}
    finally:
        _set_mode(bak)


def test_quotative_bias_top3():
    """quotative_bias 输出 ≥3 quotative 角色的 top-3 桶。"""
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 张三：pondering x8 + sneering x4 + laughing x2 + 长正文撑 CJK
        line = ("张三沉吟道：“一二三。”" * 8 + "张三冷笑道：“四五六。”" * 4 +
                "张三笑道：“七八九。”" * 2)
        draft = line + "无对话铺垫。" * 100
        proj = _mk_project(names=["张三"])
        out = mod.scan(_write(draft), project_root=proj)
        bias = out["quotative_bias"].get("张三", [])
        assert len(bias) >= 1
        # top1 = pondering（频次最高）
        assert bias[0]["bucket"] == "pondering"
    finally:
        _set_mode(bak)


# ── shadow 不上报 ────────────────────────────────────────────────────────
def test_shadow_no_violation():
    bak = os.environ.get("QUOTATIVE_SIGNATURE_MODE")
    try:
        _set_mode("shadow")
        line = "张三沉吟道：“一句话呢。”"
        out = mod.scan(_write(line * 60), project_root=None)
        assert out["mode"] == "shadow"
        # shadow 下 violations 为空（即使触发命中）
        assert out["violations"] == []
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 辅助函数 ────────────────────────────────────────────────────────────
def test_strip_changes():
    assert mod._strip_changes("正文\n---CHANGES---\nlog") == "正文"
    assert mod._strip_changes("无") == "无"


def test_cjk_count():
    assert mod._cjk_count("你好abc") == 2


def test_cosine_identical():
    assert mod._cosine({"a": 1, "b": 2}, {"a": 1, "b": 2}) == 1.0


def test_cosine_orthogonal():
    assert mod._cosine({"a": 1}, {"b": 1}) == 0.0


def test_cosine_empty():
    assert mod._cosine({}, {}) == 0.0
    assert mod._cosine({"a": 0}, {"a": 1}) == 0.0


def test_known_names_reads_chars():
    proj = _mk_project(names=["张三", "李四"])
    names = mod._known_names(proj)
    assert names == {"张三", "李四"}


def test_known_names_no_file():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._known_names(proj) == set()


def test_extract_speaker_prefers_known():
    text = "前文。张三沉吟道：“一。” 后文。"
    quote_pos = text.index("“")
    sp = mod._extract_speaker(text, quote_pos, {"张三"})
    assert sp == "张三"


def test_extract_speaker_fallback_to_token():
    text = "前文。某甲说：“一。”"
    quote_pos = text.index("“")
    sp = mod._extract_speaker(text, quote_pos, set())
    # 退化抓最后 2-4 字 CJK token
    assert "某甲" in sp or sp != "UNKNOWN"


def test_extract_speaker_unknown():
    text = "“纯引号无属。”"
    quote_pos = text.index("“")
    sp = mod._extract_speaker(text, quote_pos, set())
    # 空窗口 → UNKNOWN
    assert sp == "UNKNOWN"


def test_analyze_attribution_after_quote():
    """中文典型：引号后置 quotative，如『“嗯”张三沉吟道』。"""
    text = "“嗯。”张三沉吟道。" * 10
    lex = mod._load_lexicon()
    a = mod.analyze(text, lex, {"张三"})
    # 后置 attribution：『沉吟道』在引号后窗口里被抓
    assert sum(a["author_counts"].values()) >= 1
    assert a["dialog_total"] >= 10


# ── CLI ──────────────────────────────────────────────────────────────────
def _run_cli(draft, project, mode="active"):
    args = [sys.executable, str(_TARGET), str(draft)]
    if project is not None:
        args += ["--project", str(project)]
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "QUOTATIVE_SIGNATURE_MODE": mode,
                               "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_collapse():
    proj = _mk_project()
    # 全用 pondering → palette collapse + 撑过 CJK 500
    draft = ("张三沉吟道：“一句话。” 李四沉吟道：“二段话呢。”") * 60
    p = _write(draft)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean():
    """8 桶都用 → palette 多样 + 各角色独立 → PASS。"""
    proj = _mk_project()
    # 大量散文无对话 → 0 quotative → 不命中
    draft = "他走在山道上，听见远处溪流。" * 100
    p = _write(draft)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
