# -*- coding: utf-8 -*-
"""antagonist_valence_trajectory R23 W11 Batch-II · P2 · 反派情感重充电监控"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import antagonist_valence_trajectory as mod  # noqa: E402


def _mk_project(antagonists=None, prefs=None, drafts=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "章节").mkdir(parents=True, exist_ok=True)
    if antagonists is not None:
        chars = []
        for a in antagonists:
            chars.append({"name": a["name"], "role": "反派",
                          "redemption_arc_authorized": a.get("authorized", False)})
        (proj / "_数据库" / "人物卡.json").write_text(
            json.dumps({"characters": chars}, ensure_ascii=False), encoding="utf-8")
    if prefs is not None:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    if drafts:
        for cid, text in drafts.items():
            (proj / "章节" / f"cluster_{cid}_draft.txt").write_text(text, encoding="utf-8")
    return proj


def test_no_antagonist_skipped():
    proj = _mk_project()
    rep = mod.scan(proj)
    assert "无反派登记" in rep.get("note", "")
    assert rep["advisories"] == []


def test_authorized_globally_skipped():
    proj = _mk_project(
        antagonists=[{"name": "黑老大"}],
        prefs={"antagonist_redemption_arc_authorized": True},
        drafts={"001": "黑老大温柔地笑了笑" * 10},
    )
    rep = mod.scan(proj)
    assert rep.get("authorized") is True


def test_authorized_per_char_skipped():
    proj = _mk_project(
        antagonists=[{"name": "黑老大", "authorized": True}],
        drafts={"001": "黑老大温柔" * 10},
    )
    rep = mod.scan(proj)
    assert rep.get("authorized") is True


def test_valence_negative_when_lex_negative():
    text = "黑老大残忍冷血暴虐狰狞凶狠嗜血贪婪阴险" * 5
    v = mod.compute_cluster_valence(text, ["黑老大"])
    assert v < 0


def test_valence_positive_when_lex_positive():
    text = "黑老大温柔善良怜悯悔恨懊悔释然微笑温暖宽恕理解" * 5
    v = mod.compute_cluster_valence(text, ["黑老大"])
    assert v > 0


def test_drift_flagged_when_monotone_positive():
    # 5 cluster valence 单调上升 0→4 (>0.3)
    drafts = {
        "001": "黑老大残忍残忍残忍" * 4,                  # 强负
        "002": "黑老大残忍" * 4,                          # 弱负
        "003": "黑老大温柔" * 4,                          # 弱正
        "004": "黑老大温柔善良" * 4,                      # 中正
        "005": "黑老大温柔善良怜悯悔恨" * 4,              # 强正
    }
    proj = _mk_project(antagonists=[{"name": "黑老大"}], drafts=drafts)
    rep = mod.scan(proj)
    codes = {a["code"] for a in rep["advisories"]}
    assert mod.ISSUE_DRIFT in codes


def test_drift_not_flagged_when_oscillating():
    drafts = {
        "001": "黑老大残忍" * 5,
        "002": "黑老大温柔" * 5,
        "003": "黑老大残忍" * 5,
        "004": "黑老大温柔" * 5,
        "005": "黑老大残忍" * 5,
    }
    proj = _mk_project(antagonists=[{"name": "黑老大"}], drafts=drafts)
    rep = mod.scan(proj)
    codes = {a["code"] for a in rep["advisories"]}
    assert mod.ISSUE_DRIFT not in codes


def test_too_few_clusters_not_flagged():
    drafts = {"001": "黑老大温柔" * 5, "002": "黑老大温柔善良" * 5}
    proj = _mk_project(antagonists=[{"name": "黑老大"}], drafts=drafts)
    rep = mod.scan(proj)
    assert rep["advisories"] == []


def test_detect_drift_logic_direct():
    assert mod.detect_drift([0, 0.5, 1.0, 1.5, 2.0]) is not None
    assert mod.detect_drift([0, 0.1, 0.05, 0.2, 0.3]) is None  # 不单调
    assert mod.detect_drift([0, 0.05, 0.1, 0.15, 0.2]) is None  # 单调但 delta<0.3


def test_valence_series_recorded():
    drafts = {"001": "黑老大温柔" * 5, "002": "黑老大残忍" * 5}
    proj = _mk_project(antagonists=[{"name": "黑老大"}], drafts=drafts)
    rep = mod.scan(proj)
    assert len(rep["valence_per_cluster"]) == 2


def test_gate_level_advisory():
    proj = _mk_project(antagonists=[{"name": "黑老大"}],
                       drafts={"001": "黑老大温柔" * 5})
    rep = mod.scan(proj)
    assert rep["gate_level"] == "advisory"


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_DRIFT not in audit_hub.HARD_GATE_CODES


def test_main_cli(tmp_path):
    proj = tmp_path / "proj"
    (proj / "_数据库").mkdir(parents=True)
    (proj / "章节").mkdir(parents=True)
    rep = mod.scan(proj)
    assert rep["scanner"] == "antagonist_valence_trajectory"
