# -*- coding: utf-8 -*-
"""paratactic_implicit_logic_scanner R18 W7 Batch-T·P1 中文意合 vs 形合回归。

确定性·零依赖。覆盖 off/短稿/单字连词/多字连词/隐含因果链/作者档 z-band/
通用兜底/active/shadow/读取失败/_mode/CLI/registry 未污染 hard_gate_codes。
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
import paratactic_implicit_logic_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "paratactic_implicit_logic_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("PARATAXIS_IMPLICIT_LOGIC_MODE", None)
    else:
        os.environ["PARATAXIS_IMPLICIT_LOGIC_MODE"] = m


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
            json.dumps({"quantitative": {"paratactic_baseline": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 形合化（hypotaxis）洪流：因为...所以... 配对成串 + 显式连词密集
_HYPOTAXIS_HEAVY = (
    "因为风很大，所以他冷。虽然他很累，但是他不愿停。"
    "因为路远，所以他走得慢。虽然天黑，但是他还要赶路。"
    "因为思念，所以他难眠。虽然困倦，但是辗转反侧。"
) * 30

# 中文意合（parataxis）：极少连词 · 短句承接
_PARATAXIS_CLEAN = (
    "他走着。风很大。天黑了。脚下打滑。他扶住墙。喘气。再走。看见门。推开。"
    "屋里没人。点灯。灯灭。换火柴。又点。桌上一封信。拆开。看完。沉默。"
) * 30


def test_off_returns_skeleton():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_HYPOTAXIS_HEAVY))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。"))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_hypotaxis_heavy_triggers_floor():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HYPOTAXIS_HEAVY))
        # 形合洪流 → 连词密度超 25/kCJK · implicit_ratio 偏低 < 0.30
        m = out["metrics"]
        assert m["connective_density_per_kcjk"] >= mod.CEIL_CONNECTIVE_DENSITY \
            or (m["implicit_ratio"] is not None
                and m["implicit_ratio"] < mod.FLOOR_IMPLICIT_RATIO)
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_parataxis_clean_passes():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PARATAXIS_CLEAN))
        # 无显式连词 · implicit_ratio 高 → 全 PASS
        assert out["verdict"] == "PASS"
        assert out["metrics"]["multi_connective_hits"] == 0
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_overuse():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("active")
        # 作者基线：implicit_ratio 0.80±0.02 · z<-2 报
        proj = _mk_project(baseline={
            "connective_density_mean": 5.0,
            "connective_density_std": 1.0,
            "implicit_ratio_mean": 0.80,
            "implicit_ratio_std": 0.02,
        })
        out = mod.scan(_write(_HYPOTAXIS_HEAVY), project_root=proj)
        assert out["author_baseline"]["from_author_profile"] is True
        # z 高 → 报 connective density 偏高
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_pass():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("active")
        # 作者基线极宽容（mean 极高 + std 巨大·z<2 不报）
        proj = _mk_project(baseline={
            "connective_density_mean": 500.0,
            "connective_density_std": 500.0,
            "implicit_ratio_mean": 0.05,
            "implicit_ratio_std": 0.50,
        })
        out = mod.scan(_write(_HYPOTAXIS_HEAVY), project_root=proj)
        assert out["author_baseline"]["from_author_profile"] is True
        # 极宽容下不报
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HYPOTAXIS_HEAVY))
        assert out["violations"] == []
        assert out["warning"] is None
        # metrics 仍然计算
        assert out["metrics"]["connective_density_per_kcjk"] > 0
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\n{}") == "正文。"
    assert mod._strip_changes("正文。\n---CHANGES---\nyaml") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("你好abc世界") == 4


def test_count_connectives_multi_only():
    multi, single = mod._count_connectives("因为，所以。然而虽然。")
    assert multi >= 4


def test_count_connectives_single_leading():
    # 单字连词仅在句首/逗号后
    multi, single = mod._count_connectives("。故而离去。便走，则止。")
    # 故/便/则 都在标点后
    assert single >= 2


def test_implicit_chain_ratio_explicit_dominant():
    text = "因为风大，所以他冷。虽然他累，但是他走。"
    imp, exp, ratio = mod._implicit_chain_ratio(text)
    assert exp >= imp


def test_implicit_chain_ratio_implicit_dominant():
    text = "他走着，看见门，推开。屋里黑，点灯。"
    imp, exp, ratio = mod._implicit_chain_ratio(text)
    assert imp > exp


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "PARATAXIS_OFF_AUTHOR_BAND" not in hgs


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PARATAXIS_IMPLICIT_LOGIC_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    r = _run_cli(_write(_HYPOTAXIS_HEAVY))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_clean():
    r = _run_cli(_write(_PARATAXIS_CLEAN))
    assert r.returncode == 0, r.stderr
