# -*- coding: utf-8 -*-
"""direction_card_poetics_scanner · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import direction_card_poetics_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "direction_card_poetics_scanner.py"
_ENV = "DIRECTION_CARD_POETICS_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write_cards(cards):
    d = Path(tempfile.mkdtemp())
    p = d / "cards.json"
    p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return p


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


_GOOD_CARDS = [
    {"key": "card_a",
     "summary": ("情境：主角处境危险·人物·立场对立·利害关乎生死。"
                 "两难抉择：必须失去同伴或牺牲信仰·进退两难。"
                 "由你决定·自主权在你手中·你来定。"
                 "选项 A 走北线·避开敌人·路途凶险。")},
    {"key": "card_b",
     "summary": ("局势：另一方向南线·与卡 A 完全不同的处境。"
                 "若选此·将得罪盟友·失去重要资源·暴露身份。"
                 "由读者抉择·主动权在你·自行决定。"
                 "选项 B 南线另寻盟主·斡旋谈判·风险伪装。")},
]

_BAD_CARDS = [
    {"key": "card_a", "summary": "都一样·结果都·无论选哪。"},
    {"key": "card_b", "summary": "都一样·结果都·无论选哪。"},
]


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write_cards(_GOOD_CARDS), _mk_project(),
                       cluster_id="cluster_005")
        assert out["mode"] == "off"
        assert "per_card_scores" not in out
    finally:
        _set_mode(bak)


def test_good_cards_pass():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write_cards(_GOOD_CARDS), _mk_project(),
                       cluster_id="cluster_005",
                       interactive_mode=True)
        # 至少 framing / dilemma / agency 都过
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_FRAMING_THIN not in codes
        assert mod.ISSUE_CODE_LOW_AGENCY not in codes
    finally:
        _set_mode(bak)


def test_bad_cards_flagged():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write_cards(_BAD_CARDS), _mk_project(),
                       cluster_id="cluster_005",
                       interactive_mode=True)
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_FALSE_CHOICE_RISK in codes
        assert mod.ISSUE_CODE_FRAMING_THIN in codes
    finally:
        _set_mode(bak)


def test_advisory_low_no_fail_minor():
    """默认 advisory_low · 即便 minor 也不升 FAIL_MINOR。"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 不传 interactive/multi_ending → advisory_low
        out = mod.scan(_write_cards(_BAD_CARDS), _mk_project(),
                       cluster_id="cluster_005")
        assert out["effective_level"] == "advisory_low"
        # violations 仍记录但 verdict 仍 PASS
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_interactive_mode_upgrades():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write_cards(_BAD_CARDS), _mk_project(),
                       cluster_id="cluster_005",
                       interactive_mode=True)
        assert out["effective_level"] == "normal"
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_advisory_json_written():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write_cards(_GOOD_CARDS), proj,
                       cluster_id="cluster_005")
        assert "advisory_json_path" in out
        p = Path(out["advisory_json_path"])
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["cluster_id"] == "cluster_005"
        assert data["_placeholder"] is True
    finally:
        _set_mode(bak)


def test_empty_cards():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write_cards([]), _mk_project(),
                       cluster_id="cluster_005")
        assert "无候选卡" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_score_framing():
    s = mod._score_framing("情境·人物·立场·利害")
    assert s > 0.5
    s2 = mod._score_framing("")
    assert s2 == 0.0


def test_score_dilemma():
    s = mod._score_dilemma("两难·失去·牺牲·背叛")
    assert s > 0.5


def test_score_outcome_divergence():
    cards = [{"summary": "走北线避敌"}, {"summary": "走南线斡旋"}]
    s = mod._score_outcome_divergence(cards)
    assert s > 0.5


def test_score_false_choice():
    s = mod._score_false_choice("都一样·结果都·殊途同归")
    assert s > 0.5


def test_score_agency():
    s = mod._score_agency("你决定·由你选·自主权")
    assert s > 0.5


def test_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write_cards(_BAD_CARDS), _mk_project(),
                       cluster_id="cluster_005")
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_FRAMING_THIN, mod.ISSUE_CODE_DILEMMA_THIN,
              mod.ISSUE_CODE_OUTCOME_CONVERGENT,
              mod.ISSUE_CODE_FALSE_CHOICE_RISK,
              mod.ISSUE_CODE_LOW_AGENCY, mod.ISSUE_CODE_OK):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_returns_json():
    p = _write_cards(_GOOD_CARDS)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "direction_card_poetics_scanner"


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
