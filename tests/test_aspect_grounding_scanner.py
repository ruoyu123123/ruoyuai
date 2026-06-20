# -*- coding: utf-8 -*-
"""aspect_grounding_scanner R13 W6 Batch-R · P1 STRONG · 中文体貌首次落地·
确定性·零依赖·零 LLM/零联网。

覆盖核心分支：
  ① off → 骨架
  ② shadow 默认 + 高密度 bare le streak → 仅 stderr 不上报
  ③ active + bare le streak ≥4 + no bounding → FAIL_MINOR (ASPECT_GROUNDING_THIN)
  ④ active + bounding 表达 → bare le streak 重置 → PASS
  ⑤ active + background marker 稀薄 → ASPECT_GROUNDING_THIN
  ⑥ active + background marker 滥用 → ASPECT_GROUNDING_OVERUSE
  ⑦ active + retro 段内 prospective 过用 → OVERUSE
  ⑧ active + guo 经验体滥用 → OVERUSE
  ⑨ 作者档 aspect_baseline 第一权威覆盖兜底
  ⑩ 短稿 / 草稿读取失败
  ⑪ _mode 非法回落
  ⑫ main() CLI subprocess 退出码
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
import aspect_grounding_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "aspect_grounding_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ASPECT_GROUNDING_MODE", None)
    else:
        os.environ["ASPECT_GROUNDING_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"aspect_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 多条 bare 「了。」结句无 bounding（连续）·>500 CJK
_BARE_LE_DRAFT = (
    "他走了。她笑了。雪停了。他坐下了。她抬头了。他开口了。她低声了。" * 30
)

# 含 bounding 表达（完成补语 / 时间量词 / 终点）·>500 CJK
_BOUNDED_DRAFT = (
    "他走完了。三天后他到了。她走光了。他抵达了。" * 30
)

# 富背景标记（着/在/正/方/犹）·>500 CJK
_BG_RICH_DRAFT = (
    "他正走着，雨在下着。她方才听到。他犹豫不决。兀自在念叨着。" * 30
)

# 背景稀薄（无任何背景标记）·>500 CJK
_BG_THIN_DRAFT = (
    "他抬头。她说话。雪停。他坐下。她抬头。他开口。她低声。" * 30
)

# 高密度 prospective + retro 锚·>500 CJK
_RETRO_PROSPECTIVE_DRAFT = (
    "当年他将要离开，那时她就要嫁人。即将远行，将会归来。"
    "记得那时他就要去远方，将会有命运的安排。" * 30
)

# guo 过度使用·>500 CJK
_GUO_HEAVY_DRAFT = (
    "他来过。她见过。他听过。她说过。他想过。她梦过。" * 30
)


# ───── ① off → 骨架 ─────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_BARE_LE_DRAFT), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "bare_le_streak_max" not in out
    finally:
        _set_mode(bak)


# ───── ② shadow 默认 + bare le streak → 仅 stderr 不上报 ────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_BARE_LE_DRAFT), _mk_project())
        assert out["mode"] == "shadow"
        assert out["bare_le_streak_max"] >= 4   # 计算出确实命中
        assert out["violations"] == [] and out["warning"] is None
    finally:
        _set_mode(bak)


# ───── ③ active + bare le streak → FAIL_MINOR ──────────────────────────────
def test_active_bare_le_streak_fail_minor():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BARE_LE_DRAFT), _mk_project())
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        codes = {f["code"] for f in out.get("flags", [])}
        assert "ASPECT_GROUNDING_THIN" in codes
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ───── ④ active + bounded → streak 重置 → 不触发 streak 维度 ────────────────
def test_active_bounded_no_streak_flag():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BOUNDED_DRAFT), _mk_project())
        # 含 bounding → bare_le_streak_max 应该明显减少
        assert out["bare_le_streak_max"] < 4
        # 该维度未触发（可能其他维度触发，但 streak msg 不存在）
        streak_flags = [f for f in out.get("flags", [])
                        if "结句无 bounding" in f["msg"]]
        assert streak_flags == []
    finally:
        _set_mode(bak)


# ───── ⑤ active + background marker 稀薄 → THIN ────────────────────────────
def test_active_background_thin():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BG_THIN_DRAFT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        # 至少含 THIN（背景稀薄）或 streak（bare le）
        assert "ASPECT_GROUNDING_THIN" in codes
        assert out["background_per_1k"] < 1.5
    finally:
        _set_mode(bak)


# ───── ⑥ active + background marker 滥用 → OVERUSE ─────────────────────────
def test_active_background_overuse():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BG_RICH_DRAFT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "ASPECT_GROUNDING_OVERUSE" in codes
        assert out["background_per_1k"] > 9.0
    finally:
        _set_mode(bak)


# ───── ⑦ active + retro 段 prospective 过用 → OVERUSE ──────────────────────
def test_active_retro_prospective_overuse():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_RETRO_PROSPECTIVE_DRAFT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "ASPECT_GROUNDING_OVERUSE" in codes
    finally:
        _set_mode(bak)


# ───── ⑧ active + guo 滥用 → OVERUSE ────────────────────────────────────────
def test_active_guo_overuse():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_GUO_HEAVY_DRAFT), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "ASPECT_GROUNDING_OVERUSE" in codes
        assert out["guo_per_1k"] > 5.5
    finally:
        _set_mode(bak)


# ───── ⑨ 作者档 aspect_baseline 第一权威 ────────────────────────────────────
def test_author_baseline_overrides_fallback():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        # 作者档极度宽松 → 不触发 OVERUSE
        baseline = {"background_per_1k_band": [0.0, 500.0],
                    "prospective_per_1k_max": 500.0,
                    "guo_per_1k_max": 500.0,
                    "bare_le_streak_threshold": 9999}
        out = mod.scan(_write(_BG_RICH_DRAFT), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        assert out["baseline"]["background_per_1k_band"] == [0.0, 500.0]
        codes = {f["code"] for f in out.get("flags", [])}
        assert "ASPECT_GROUNDING_OVERUSE" not in codes
    finally:
        _set_mode(bak)


# ───── ⑩ 短稿 / 读取失败 ────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他走了。" * 5), _mk_project())
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ───── ⑪ _mode 非法回落 ───────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("OFF")
        assert mod._mode() == "off"
    finally:
        _set_mode(bak)


def test_mode_default_shadow_when_unset():
    bak = os.environ.get("ASPECT_GROUNDING_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ───── 辅助函数 ─────────────────────────────────────────────────────────────
def test_is_bare_le_sentence_basic():
    assert mod._is_bare_le_sentence("他走了。") is True
    assert mod._is_bare_le_sentence("他到了完。") is False     # 含完成补语
    assert mod._is_bare_le_sentence("三天后他走了。") is False  # 含时间量词
    assert mod._is_bare_le_sentence("她坐下。") is False        # 不以了结尾
    assert mod._is_bare_le_sentence("") is False


def test_bare_le_streaks():
    sents = ["他走了。", "她笑了。", "雪停了。", "他坐下了。", "她抬头。", "他开口了。"]
    streak, total = mod._bare_le_streaks(sents)
    assert streak == 4    # 前 4 句连续 bare le
    assert total == 5


def test_strip_changes_factual_separator():
    raw = "正文。\n\n---CHANGES_FACTUAL---\n{\"foo\":1}"
    assert mod._strip_changes(raw) == "正文。"


def test_cjk_count_basic():
    assert mod._cjk_count("你好abc世界") == 4


def test_split_sentences():
    sents = mod._split_sentences("他走了。她笑了。雪停了。")
    assert len(sents) == 3


def test_count_markers_basic():
    assert mod._count_markers("他着她着他", ("着",)) == 2


def test_retro_segments_collected():
    segs = mod._retro_segments("当年他将要走。彼时她已离去。" * 20)
    assert segs   # 至少抓到一段


def test_read_author_baseline_none_when_no_project():
    assert mod._read_author_baseline(None) is None


def test_read_author_baseline_bad_json_returns_none():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ bad json", encoding="utf-8")
    assert mod._read_author_baseline(proj) is None


# ───── ⑫ main() CLI subprocess 退出码 ───────────────────────────────────────
def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ASPECT_GROUNDING_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project()
    p = _write(_BARE_LE_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None
    assert rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_with_author_baseline_relaxed():
    baseline = {"background_per_1k_band": [0.0, 50.0],
                "prospective_per_1k_max": 50.0,
                "guo_per_1k_max": 50.0,
                "bare_le_streak_threshold": 999}
    proj = _mk_project(baseline=baseline)
    p = _write(_BARE_LE_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"
