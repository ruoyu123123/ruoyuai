# -*- coding: utf-8 -*-
"""gricean_flouting_density.py 专属回归测试 (R8 W4 Batch-G·L21·2026-06-20)。

零依赖·确定性·零 LLM/零联网。覆盖核心 6 分支：
  ① 高 flouting 对话 (Quality/Quantity/Relation/Manner 全部 trip) + active → PASS
  ② 低 flouting (准则全合作) + active → FAIL_MINOR
  ③ 无作者档 → 通用兜底阈值 0.15
  ④ 作者档 dialogue_flouting_profile 覆盖
  ⑤ shadow 即使触发也只记不判
  ⑥ off → 骨架
  + 四子检测器分别全分支 / extract_dialogue_turns / 短稿 / main CLI。
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
import gricean_flouting_density as mod  # noqa: E402

_TARGET = _SCRIPTS / "gricean_flouting_density.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("GRICEAN_FLOUTING_MODE", None)
    else:
        os.environ["GRICEAN_FLOUTING_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(expected_min=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if expected_min is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"dialogue_flouting_profile": {"expected_flouting_min": expected_min}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 高 flouting 对话草稿 (4 子项混合·>500 CJK)
def _hi_flout_body():
    lines = []
    for _ in range(20):
        lines.extend([
            '他说：“也许吧。”',         # Manner
            '她答：“才怪。”',           # Quality + Quantity (短)
            '他又说：“说回正事。”',     # Relation
            '她回：“反正你不懂。”',     # Relation (open deflect)
            '他低声说：“这事一言难尽，到时候看。”',  # Manner
            '她回过头：“我看你是糊涂了。”',          # Quality (我看未必类)
        ])
    return "\n".join(lines)


# 低 flouting 对话 (准则全合作 · 字数适中 4-80 · 无含混反讽跳题·避开 reopen markers)
def _lo_flout_body():
    lines = []
    for _ in range(20):
        lines.extend([
            '他说：“我今天到了学校。”',
            '她答：“你吃过饭了吗。”',
            '他点头：“早就吃过了。”',
            '她笑：“这真是太好了。”',
            '他说：“我下午要去图书馆。”',
            '她答：“我在家里等你回来。”',
        ])
    return "\n".join(lines)


_HI_FLOUT = _hi_flout_body()
_LO_FLOUT = _lo_flout_body()


# ── ⑥ off → 骨架 ─────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_LO_FLOUT), project_root=None)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "flouting_per_dialogue_turn" not in out
    finally:
        _set_mode(bak)


# ── ① 高 flouting + active → PASS ────────────────────────────────────────────
def test_active_high_flouting_passes():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HI_FLOUT), project_root=None)
        assert out["dialogue_turn_total"] >= 4
        assert out["flouting_per_dialogue_turn"] >= 0.15
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
        # 四子项至少 3 个有命中
        cats = [out["quality_flouts"], out["quantity_flouts"],
                out["relation_flouts"], out["manner_flouts"]]
        assert sum(1 for c in cats if c > 0) >= 3
    finally:
        _set_mode(bak)


# ── ② 低 flouting + active → FAIL_MINOR ─────────────────────────────────────
def test_active_low_flouting_fails_minor():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LO_FLOUT), project_root=None)
        assert out["flouting_per_dialogue_turn"] < 0.15
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"]
        assert out["violations"][0]["severity"] == "minor"
        assert out["gate_level"] == "advisory"
        assert out["expected_floor_source"] == "default_fallback"
    finally:
        _set_mode(bak)


# ── ③ 作者档基线覆盖 → 使用作者档 floor ──────────────────────────────────────
def test_author_profile_floor_overrides():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        # 用 LO_FLOUT (低密度) + 高 floor 0.5 → 强迫 FAIL, 验证 author_profile floor 生效
        proj = _mk_project(expected_min=0.5)
        out = mod.scan(_write(_LO_FLOUT), project_root=proj)
        assert out["expected_floor_source"] == "author_profile"
        assert out["expected_flouting_min"] == 0.5
        assert out["verdict"] == "FAIL_MINOR"
        # 反向：HI_FLOUT (高密度·ratio=1.0) + 高 floor 0.5 → PASS,
        # 证明 author_profile floor 真的生效 (默认 floor=0.15 在 HI_FLOUT 下早就 PASS·必须用 0.5 才能验证生效)
        proj2 = _mk_project(expected_min=0.5)
        out2 = mod.scan(_write(_HI_FLOUT), project_root=proj2)
        assert out2["expected_floor_source"] == "author_profile"
        assert out2["expected_flouting_min"] == 0.5
        assert out2["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_profile_floor_zero_relaxes():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        # floor=0.0 → 全 PASS
        proj = _mk_project(expected_min=0.0)
        out = mod.scan(_write(_LO_FLOUT), project_root=proj)
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── ⑤ shadow 不上报 ────────────────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LO_FLOUT), project_root=None)
        assert out["mode"] == "shadow"
        assert out["flouting_per_dialogue_turn"] < 0.15
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 对话 turn 数太少 → skip ─────────────────────────────────────────────────
def test_too_few_turns_skipped():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        text = '他说：“嗯哼好的。”' + ("这是一段填充正文内容。" * 100)  # 1 turn · cjk≥500
        out = mod.scan(_write(text), project_root=None)
        assert out["dialogue_turn_total"] < 4
        assert "对话 turn 数太少" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。"), project_root=None)
        assert "草稿太短" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── _mode 非法回落 ──────────────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("GRICEAN_FLOUTING_MODE")
    try:
        _set_mode("nonsense")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── _strip_changes / _cjk_count ─────────────────────────────────────────────
def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("你好abc") == 2


# ── _read_expected_flouting_min 全分支 ──────────────────────────────────────
def test_read_floor_none_project():
    assert mod._read_expected_flouting_min(None) is None


def test_read_floor_file_missing():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._read_expected_flouting_min(proj) is None


def test_read_floor_bad_json():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ bad", encoding="utf-8")
    assert mod._read_expected_flouting_min(proj) is None


def test_read_floor_top_not_dict():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("[1]", encoding="utf-8")
    assert mod._read_expected_flouting_min(proj) is None


def test_read_floor_field_bad_value():
    proj = _mk_project(expected_min="not_a_number")
    assert mod._read_expected_flouting_min(proj) is None


# ── extract_dialogue_turns / classify_turn 子检测器 ─────────────────────────
def test_extract_paired_quotes():
    turns = mod.extract_dialogue_turns('他说：“你好。” 她答：“再见。”')
    assert turns == ["你好。", "再见。"]


def test_extract_single_quotes_fallback():
    turns = mod.extract_dialogue_turns("‘你好。’")
    assert turns == ["你好。"]


def test_extract_empty():
    assert mod.extract_dialogue_turns("无对话纯叙述。") == []


def test_classify_quality_irony():
    c = mod.classify_turn("才怪。")
    assert c["quality"] is True


def test_classify_quantity_too_short():
    c = mod.classify_turn("嗯。")
    assert c["quantity"] is True
    assert c["cjk_len"] < mod.QUANTITY_TOO_SHORT


def test_classify_quantity_too_long():
    long_turn = "我" * 100
    c = mod.classify_turn(long_turn)
    assert c["quantity"] is True


def test_classify_relation_jump():
    c = mod.classify_turn("说回正事。")
    assert c["relation"] is True


def test_classify_relation_open_deflect():
    c = mod.classify_turn("反正你不懂。")
    assert c["relation"] is True


def test_classify_manner_hedge():
    c = mod.classify_turn("再说吧。")
    assert c["manner"] is True


def test_classify_no_flout():
    c = mod.classify_turn("我下午去图书馆借书。")
    assert c["any_flout"] is False


# ── main() CLI subprocess ───────────────────────────────────────────────────
def _run_cli(draft_path, mode="active", project=None):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "GRICEAN_FLOUTING_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    p = _write(_LO_FLOUT)
    r = _run_cli(p)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_pass():
    p = _write(_HI_FLOUT)
    r = _run_cli(p)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None
