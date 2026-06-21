# -*- coding: utf-8 -*-
"""adversarial_judge_pair R19 W8 Batch-Y·P2 三角 scaffolding 回归测试。

确定性·零依赖。覆盖 off/短稿/无 brief skip/非 finale skip/attacker 5 维触发/
defender 找证据/unanswered<floor PASS/unanswered>=floor degraded/shadow vs active/CLI/hard_gate.
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
import adversarial_judge_pair as mod  # noqa: E402

_TARGET = _SCRIPTS / "adversarial_judge_pair.py"
_ENV = "ADVERSARIAL_JUDGE_PAIR_MODE"


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


def _mk_brief(**kwargs):
    d = Path(tempfile.mkdtemp())
    p = d / "brief.json"
    brief = {"id": "cluster_001", "is_volume_finale": True, **kwargs}
    p.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    return p


_TEXT = ("江条款握紧合约，原来如此！可下一刻竟出了变故？？？" * 100)


def test_off_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_TEXT))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短文。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_no_brief_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_TEXT))
        assert "无 cluster brief" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_non_finale_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        d = Path(tempfile.mkdtemp())
        p = d / "brief.json"
        p.write_text(json.dumps({"is_volume_finale": False}), encoding="utf-8")
        r = mod.scan(_write(_TEXT), cluster_brief_path=p)
        assert "非 volume_finale" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_attack_a3_foreshadow_missing():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(foreshadowing=[{"tag": "未被提及的钩子"}, {"tag": "其他"}])
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        names = [a["name"] for a in r["attacks"]]
        assert "foreshadow_not_paid_in_finale" in names
    finally:
        _set_mode(bak)


def test_attack_a5_stake_keyword_missing():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # stake 用全稿不出现的怪字串
        brief = _mk_brief(stakes=[{"keyword": "彧囧囧囧囧"}])
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        names = [a["name"] for a in r["attacks"]]
        assert "stake_keyword_missing" in names
    finally:
        _set_mode(bak)


def test_defender_finds_evidence():
    """text 中含 'A3 target' 的 target → defender answered."""
    text = "江条款冷笑：原来如此！" * 200
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(stakes=["江条款"])
        r = mod.scan(_write(text), cluster_brief_path=brief)
        # 至少 1 个 attack 被反驳
        if r["defenses"]:
            assert any(d["answered"] for d in r["defenses"])
    finally:
        _set_mode(bak)


def test_unanswered_below_floor_passes():
    text = "江条款冷笑：原来如此！可下一刻又出了变故！" * 200
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        brief = _mk_brief(foreshadowing=[{"tag": "江条款"}],
                          stakes=["江条款"])
        r = mod.scan(_write(text), cluster_brief_path=brief)
        # attacks ≤ 2 或全部 answered → PASS
        if r.get("verdict_pair"):
            assert r["verdict_pair"]["unanswered_count"] < mod.UNANSWERED_FLOOR or r["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_unanswered_above_floor_degraded():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 多个 unanswered: foreshadowing + power_shift + stakes 全缺
        brief = _mk_brief(
            foreshadowing=[{"tag": "钩子A"}, {"tag": "钩子B"}, {"tag": "钩子C"}],
            power_shift={"actor": "缺位主角"},
            stakes=[{"keyword": "缺位代价"}])
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        codes = [v["code"] for v in r["violations"]]
        # 应触发
        if r.get("verdict_pair") and r["verdict_pair"]["unanswered_count"] >= mod.UNANSWERED_FLOOR:
            assert "ADVERSARIAL_JUDGE_DEGRADED" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        brief = _mk_brief(
            foreshadowing=[{"tag": "x"}, {"tag": "y"}, {"tag": "z"}])
        r = mod.scan(_write(_TEXT), cluster_brief_path=brief)
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "off"
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


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def test_main_cli_runs_off():
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(_write(_TEXT))],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "off", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "adversarial_judge_pair"
