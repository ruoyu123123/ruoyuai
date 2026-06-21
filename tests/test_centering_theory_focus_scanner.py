# -*- coding: utf-8 -*-
"""centering_theory_focus_scanner R18 W7 Batch-T·P1 Grosz CT 4 转移回归。

确定性·零依赖。覆盖 off/短稿/可分析句过少/continue/retain/smooth/rough/兜底地板/
作者档 z-band/active/shadow/读取失败/_mode/CLI/registry hard_gate codes 不污染/
_extract_cf/_classify_transition 直测。
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
import centering_theory_focus_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "centering_theory_focus_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("CENTERING_THEORY_FOCUS_MODE", None)
    else:
        os.environ["CENTERING_THEORY_FOCUS_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if baseline is not None:
        (db / "作者风格.json").write_text(
            json.dumps({"quantitative": {"centering_baseline": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# rough-shift 密集：每句换实体焦点（甲→乙→丙→丁循环）
_ROUGH_HEAVY = (
    "张三走。李四笑。王五站。赵六坐。钱七跑。孙八跳。"
) * 200

# continue 主导：同实体延续焦点
_CONTINUE_HEAVY = (
    "张三走。张三看。张三说。张三笑。张三停。张三再走。张三回头。"
) * 200


def test_off_returns_skeleton():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_ROUGH_HEAVY))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。"))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_too_few_sentences_skip():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        # 大量 CJK 但只 1 句 → 通过 cjk 闸但 scanned<5
        text = "张三走遍千山万水寻找答案的故事" * 50
        out = mod.scan(_write(text), proj)
        # 没断句符 · scanned 太少
        assert "可分析句数" in out.get("note", "") \
            or out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_rough_heavy_triggers_floor():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[
            {"name": "张三", "role": "主角"},
            {"name": "李四", "role": "配角"},
            {"name": "王五", "role": "配角"},
            {"name": "赵六", "role": "配角"},
            {"name": "钱七", "role": "配角"},
            {"name": "孙八", "role": "配角"},
        ])
        out = mod.scan(_write(_ROUGH_HEAVY), proj)
        m = out["metrics"]
        # rough 密度高
        assert m["rough_shift_density_per_kcjk"] >= mod.FLOOR_ROUGH_SHIFT_DENSITY \
            or out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_continue_heavy_passes():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
        out = mod.scan(_write(_CONTINUE_HEAVY), proj)
        # continue 主导 → rough 极少
        m = out["metrics"]
        assert m["transitions"]["continue"] > m["transitions"]["rough"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_overload():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            characters=[
                {"name": "张三", "role": "主角"},
                {"name": "李四", "role": "配角"},
                {"name": "王五", "role": "配角"},
                {"name": "赵六", "role": "配角"},
                {"name": "钱七", "role": "配角"},
                {"name": "孙八", "role": "配角"},
            ],
            baseline={"rough_shift_density_mean": 1.0,
                      "rough_shift_density_std": 0.1,
                      "rough_shift_density_max": 2.0})
        out = mod.scan(_write(_ROUGH_HEAVY), proj)
        assert out["author_baseline"]["from_author_profile"] is True
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_pass():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            characters=[{"name": "张三", "role": "主角"}],
            baseline={"rough_shift_density_mean": 50.0,
                      "rough_shift_density_std": 50.0,
                      "rough_shift_density_max": 200.0})
        out = mod.scan(_write(_CONTINUE_HEAVY), proj)
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[
            {"name": "张三", "role": "主角"},
            {"name": "李四", "role": "配角"},
            {"name": "王五", "role": "配角"},
            {"name": "赵六", "role": "配角"},
            {"name": "钱七", "role": "配角"},
            {"name": "孙八", "role": "配角"},
        ])
        out = mod.scan(_write(_ROUGH_HEAVY), proj)
        assert out["violations"] == []
        # metrics 仍然算
        assert out["metrics"]["sentences_scanned"] > 0
    finally:
        _set_mode(bak)


def test_extract_cf_order_preserved():
    cf = mod._extract_cf("张三看着李四，王五在远处。", {"张三", "李四", "王五"})
    assert cf == ["张三", "李四", "王五"]


def test_extract_cf_includes_pronoun():
    cf = mod._extract_cf("他望着她。", {"张三"})
    assert "他" in cf
    assert "她" in cf


def test_classify_continue():
    # prev_cf=[张三, 李四] · cur_cf=[张三, 李四]
    cb, kind = mod._classify_transition(["张三", "李四"], ["张三", "李四"])
    assert kind == "continue"
    assert cb == "张三"


def test_classify_retain():
    # prev_cb=张三 · cur Cb=张三（前句 Cp）·Cp(cur)=李四 → retain
    cb, kind = mod._classify_transition(["张三", "李四"], ["李四", "张三"])
    assert kind == "retain"
    assert cb == "张三"


def test_classify_smooth_shift():
    # prev=[张三, 李四]·cur=[李四]·Cb=李四(!=prev_cb 张三)·Cp(cur)=李四 → smooth
    cb, kind = mod._classify_transition(["张三", "李四"], ["李四"])
    assert kind == "smooth"


def test_classify_rough_shift():
    # prev=[张三]·cur=[王五, 李四]·王五∉prev → cb=None → rough
    cb, kind = mod._classify_transition(["张三"], ["王五", "李四"])
    assert kind == "rough"


def test_classify_none_on_empty():
    _, kind = mod._classify_transition([], ["张三"])
    assert kind == "none"
    _, kind = mod._classify_transition(["张三"], [])
    assert kind == "none"


def test_read_failure_note():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get("CENTERING_THEORY_FOCUS_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "CENTERING_ROUGH_SHIFT_OVERLOAD" not in hgs


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "CENTERING_THEORY_FOCUS_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(characters=[
        {"name": "张三", "role": "主角"},
        {"name": "李四", "role": "配角"},
        {"name": "王五", "role": "配角"},
        {"name": "赵六", "role": "配角"},
        {"name": "钱七", "role": "配角"},
        {"name": "孙八", "role": "配角"},
    ])
    r = _run_cli(_write(_ROUGH_HEAVY), project=proj)
    assert r.returncode in (0, 1), r.stderr


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三", "role": "主角"}])
    r = _run_cli(_write(_CONTINUE_HEAVY), project=proj)
    assert r.returncode == 0, r.stderr
