# -*- coding: utf-8 -*-
"""cross_cluster_reader_retention_proxy_aggregate R18 W7 Batch-U·P2 完读率代理回归。

确定性·零依赖。覆盖 off 退出/无章节早返/默认权重/proxy 高 PASS/proxy 低报警/
_hook_score 兜底/_sagging_score 兜底/_cliffhanger_score 兜底/_length_health/
aggregate API/CLI mode shadow 0 退出·registry 未污染 hard_gate_codes。
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
import cross_cluster_reader_retention_proxy_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_reader_retention_proxy_aggregate.py"
_ENV = "READER_RETENTION_PROXY_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _mk_project(chapters=None, prior_findings=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "章节").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    chapters = chapters or {}
    for ch, body in chapters.items():
        d = proj / "章节" / f"第{ch:03d}章"
        d.mkdir(exist_ok=True)
        # 字数对齐 3000-4500 健康区
        (d / "body.txt").write_text(body, encoding="utf-8")
    if prior_findings:
        sd = proj / "_数据库" / ".cross_chapter_scan"
        sd.mkdir(parents=True, exist_ok=True)
        for prefix, payload in prior_findings.items():
            (sd / f"{prefix}_20260621_000000.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return proj


_GOOD_BODY = "字" * 3500  # 健康区
_SHORT_BODY = "字" * 1500


def test_mode_default_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_no_chapters_returns_none():
    proj = _mk_project()
    s, f = mod.aggregate(proj)
    assert s is None
    assert f == []


def test_default_weights_used():
    proj = _mk_project({1: _GOOD_BODY, 2: _GOOD_BODY, 3: _GOOD_BODY})
    s, f = mod.aggregate(proj)
    assert s["weights"]["hook"] == 0.35
    assert "retention_proxy" in s
    # 健康章 + 无 finding → proxy 应较高
    assert s["retention_proxy"] > 0.3


def test_low_proxy_triggers_warn():
    """所有维度都拉低 → proxy < 0.45 报。"""
    findings_payload = {
        "engagement_metrics": {
            "scores_collected": {"hook": [1, 2, 3]},
            "findings": [
                {"code": "HOOK_DECLINE", "severity": "warning"},
                {"code": "HOOK_DECLINE", "severity": "warning"},
                {"code": "CLIFFHANGER_QUOTA_OFF", "severity": "warning"},
                {"code": "CLIFFHANGER_QUOTA_OFF", "severity": "warning"},
                {"code": "CLIFFHANGER_QUOTA_OFF", "severity": "warning"},
                {"code": "CLIFFHANGER_QUOTA_OFF", "severity": "warning"},
                {"code": "CLIFFHANGER_QUOTA_OFF", "severity": "warning"},
            ],
        },
        "sagging_middle": {
            "summary": {"advisory": 5, "warning": 3},
            "findings": [],
        },
    }
    proj = _mk_project({1: _SHORT_BODY, 2: _SHORT_BODY, 3: _SHORT_BODY},
                       prior_findings=findings_payload)
    s, f = mod.aggregate(proj)
    assert s["retention_proxy"] < 0.45
    assert len(f) >= 1
    assert f[0]["code"] == mod.ISSUE_CODE
    assert f[0]["severity"] == "advisory"


def test_hook_score_no_history():
    sd = Path(tempfile.mkdtemp())
    assert mod._hook_score(sd) == 0.5


def test_sagging_score_no_history():
    sd = Path(tempfile.mkdtemp())
    assert mod._sagging_score(sd) == 0.5


def test_cliffhanger_score_no_history():
    sd = Path(tempfile.mkdtemp())
    assert mod._cliffhanger_score(sd) == 0.5


def test_length_health_no_chapters():
    proj = _mk_project()
    assert mod._length_health(proj, [], 5) == 0.5


def test_length_health_full_healthy():
    proj = _mk_project({1: _GOOD_BODY, 2: _GOOD_BODY})
    h = mod._length_health(proj, [1, 2], 5)
    assert h == 1.0


def test_length_health_full_short():
    proj = _mk_project({1: _SHORT_BODY, 2: _SHORT_BODY})
    h = mod._length_health(proj, [1, 2], 5)
    assert h == 0.0


def test_list_chapters_sorted():
    proj = _mk_project({3: "x", 1: "y", 2: "z"})
    chs = mod._list_chapters(proj)
    assert chs == [1, 2, 3]


def test_read_latest_missing_dir():
    sd = Path(tempfile.mkdtemp()) / "noexist"
    assert mod._read_latest(sd, "x") is None


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_shadow_exit_zero():
    proj = _mk_project({1: _GOOD_BODY})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), "--last-n", "5"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr


def test_main_cli_off_skip():
    proj = _mk_project({1: _GOOD_BODY})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "off", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    assert "SKIP" in r.stdout
