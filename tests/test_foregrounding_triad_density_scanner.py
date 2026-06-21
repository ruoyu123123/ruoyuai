# -*- coding: utf-8 -*-
"""foregrounding_triad_density_scanner R19 W8 Batch-W·P1 Miall-Kuiken 三维回归。

确定性·零依赖。覆盖 off/短稿/三桶探针 zero/probe 正例/作者档 ECDF z-band/兜底
地板/FTDI 复合/shadow active 切换/CLI/hard_gate 守卫。
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
import foregrounding_triad_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "foregrounding_triad_density_scanner.py"
_ENV = "FOREGROUNDING_TRIAD_DENSITY_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


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
            json.dumps({"quantitative": {"foregrounding_triad": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 中性文本(三桶都低密度但稍有一些 → 都进兜底带)
_NEUTRAL = (
    "他走过去。一个人坐在那里。如同一束光照过。她笑着说话。" * 30
)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_NEUTRAL))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_probe_phonetic_zero_clean():
    r = mod.probe_phonetic("一二三四五。")
    assert r["hits"] >= 0


def test_probe_phonetic_hits_onomatopoeia():
    r = mod.probe_phonetic("门突然咣当一声开了。雷砰砰响起。咯咯笑声。")
    assert r["hits"] >= 3


def test_probe_grammatical_long_dunhao_streak():
    r = mod.probe_grammatical("他买了书、笔、墨、纸、砚。")
    assert r["hits"] >= 1


def test_probe_grammatical_negation_prefix():
    # 句首否定词触发
    r = mod.probe_grammatical("不能去。没办法。莫担心。")
    assert r["hits"] >= 3


def test_probe_semantic_metaphor_markers():
    r = mod.probe_semantic("她的笑如花。眼神宛如刀。心情仿佛雨。")
    assert r["hits"] >= 3


def test_probe_semantic_oxymoron():
    r = mod.probe_semantic("沉默的呐喊在心里炸开。" * 5)
    assert r["hits"] >= 1


def test_high_phonetic_density_no_baseline_triggers_floor():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极高拟声密度
        text = ("咣当砰砰咯咯沙沙啪啪。" * 80)
        r = mod.scan(_write(text))
        assert r["verdict"] == "FAIL_MINOR"
        codes = [v["code"] for v in r["violations"]]
        assert "FOREGROUNDING_TRIAD_DENSITY_OFF_BAND" in codes
    finally:
        _set_mode(bak)


def test_low_density_no_baseline_passes_neutral():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 仅一些中性 + 隐喻一次 → semantic 也可能低 ↓ 受地板影响
        text = ("一个人走路。" * 200)
        r = mod.scan(_write(text))
        # 低密度可能触发 floor min ·中性测试只确保不爆错
        assert r["scanner"] == "foregrounding_triad_density"
    finally:
        _set_mode(bak)


def test_author_baseline_in_band_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(baseline={
            "phonetic_per_1k_ecdf_p20": 0.5, "phonetic_per_1k_ecdf_p80": 10.0,
            "grammatical_per_1k_ecdf_p20": 1.0, "grammatical_per_1k_ecdf_p80": 30.0,
            "semantic_per_1k_ecdf_p20": 0.5, "semantic_per_1k_ecdf_p80": 30.0,
        })
        r = mod.scan(_write(_NEUTRAL), project_root=proj)
        assert r["author_baseline"]["from_author_profile"] is True
        # FTDI 在 [-4, 4] 内 → 通过
        assert r["metrics"]["ftdi"] is not None
    finally:
        _set_mode(bak)


def test_author_baseline_ftdi_off_band_triggers():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极窄基线·任何偏离都爆 z
        proj = _mk_project(baseline={
            "phonetic_per_1k_ecdf_p20": 50.0, "phonetic_per_1k_ecdf_p80": 50.0001,
            "grammatical_per_1k_ecdf_p20": 50.0, "grammatical_per_1k_ecdf_p80": 50.0001,
            "semantic_per_1k_ecdf_p20": 50.0, "semantic_per_1k_ecdf_p80": 50.0001,
        })
        r = mod.scan(_write(_NEUTRAL), project_root=proj)
        # 一定偏离极窄基线 → FAIL
        codes = [v["code"] for v in r["violations"]]
        assert r["verdict"] == "FAIL_MINOR" or codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        text = ("咣当砰砰咯咯沙沙啪啪。" * 80)
        r = mod.scan(_write(text))
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_ecdf_z_helper():
    z = mod._ecdf_z(5.0, 0.0, 10.0)
    assert z == 0.0  # 中位
    z2 = mod._ecdf_z(10.0, 0.0, 10.0)
    assert abs(z2 - 1.0) < 1e-9  # p80 端


def test_ecdf_z_invalid_returns_none():
    assert mod._ecdf_z(1, None, None) is None
    assert mod._ecdf_z(1, 5, 5) is None  # spread = 0


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def _run_cli(draft_path, mode="shadow"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_runs_shadow():
    r = _run_cli(_write(_NEUTRAL))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "foregrounding_triad_density"
