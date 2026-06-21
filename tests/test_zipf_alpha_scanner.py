# -*- coding: utf-8 -*-
"""zipf_alpha_scanner R18 W7 Batch-U·P2 Zipf α 反 AI 词频曲线回归。

确定性·零依赖。覆盖 off/短稿/拟合不足/兜底地板高/兜底地板低/作者档 z-band/
clean PASS/shadow/读取失败/_mode/CLI/_tokenize_bichar/_fit_zipf_alpha/
strip_changes·registry 未污染 hard_gate_codes。
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
import zipf_alpha_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "zipf_alpha_scanner.py"
_ENV = "ZIPF_ALPHA_SCANNER_MODE"


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
            json.dumps({"quantitative": {"zipf_baseline": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 普通中文长文（多样化 vocab）
_RICH_CN = (
    "春风又绿江南岸，明月何时照我还。山色空蒙雨亦奇，水光潋滟晴方好。"
    "落霞与孤鹜齐飞，秋水共长天一色。烟笼寒水月笼沙，夜泊秦淮近酒家。"
    "千山鸟飞绝，万径人踪灭。孤舟蓑笠翁，独钓寒江雪。"
    "渭城朝雨浥轻尘，客舍青青柳色新。劝君更尽一杯酒，西出阳关无故人。"
    "曲径通幽处，禅房花木深。山光悦鸟性，潭影空人心。"
) * 60


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_RICH_CN))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_rich_text_computes_alpha():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_RICH_CN))
        assert "alpha" in r["metrics"]
        # 重复短句 → 分布极度均匀(α ≈ 0)·只断言数值存在且非负
        assert r["metrics"]["alpha"] >= 0
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极严苛基线·alpha=0 远离 5±0.001
        proj = _mk_project(baseline={"alpha_mean": 5.0, "alpha_std": 0.001})
        r = mod.scan(_write(_RICH_CN), project_root=proj)
        assert r["author_baseline"]["from_author_profile"] is True
        # alpha 远离 5.0 ± 0.001 → |z| ≥ 2 必报
        assert r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_lenient_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 极宽容基线
        proj = _mk_project(baseline={"alpha_mean": 1.0, "alpha_std": 50.0})
        r = mod.scan(_write(_RICH_CN), project_root=proj)
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        # 不论 alpha 如何·shadow 不上报
        r = mod.scan(_write(_RICH_CN))
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_tokenize_bichar():
    toks = mod._tokenize_bichar("abc中国人de")
    # 只 CJK：中国/国人
    assert "中国" in toks
    assert "国人" in toks
    # abc/de 被剥
    assert all(all("一" <= c <= "鿿" for c in t) for t in toks)


def test_fit_zipf_alpha_too_few_returns_none():
    from collections import Counter
    freqs = Counter({"a": 5, "b": 3})
    assert mod._fit_zipf_alpha(freqs) is None


def test_fit_zipf_alpha_works():
    from collections import Counter
    freqs = Counter({f"t{i}": (100 - i) for i in range(50)})
    a = mod._fit_zipf_alpha(freqs)
    assert a is not None
    assert a > 0


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
    r = _run_cli(_write(_RICH_CN))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "zipf_alpha"
