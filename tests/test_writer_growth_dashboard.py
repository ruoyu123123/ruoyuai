# -*- coding: utf-8 -*-
"""writer_growth_dashboard R23 W11 Batch-II · P2 · 反馈 vs 多样性轨迹"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import writer_growth_dashboard as mod  # noqa: E402


def _mk_project(prefs=None, events=None, drafts=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "章节").mkdir(parents=True, exist_ok=True)
    if prefs is not None:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    if events is not None:
        (proj / "_数据库" / "事件簇.json").write_text(
            json.dumps(events, ensure_ascii=False), encoding="utf-8")
    if drafts:
        for cid, text in drafts.items():
            (proj / "章节" / f"cluster_{cid}_draft.txt").write_text(text, encoding="utf-8")
    return proj


def test_empty_project_runs():
    proj = _mk_project()
    rep = mod.build_dashboard(proj)
    assert rep["cluster_count"] == 0
    assert rep["feedback_event_total"] == 0


def test_ttr_basic():
    text = "他走她跑我笑"  # 6 字全唯一 → ttr=1.0
    tokens = mod._cjk_tokens(text)
    assert mod._ttr(tokens) == 1.0


def test_mtld_returns_float():
    # 全唯一 token 不会触发 factor，应返回 token 数（float）
    out = mod._mtld(list("abcdefghij"))
    assert out >= 1.0


def test_hapax_ratio_basic():
    tokens = list("aabc")
    # b/c hapax (出现1次) → 2/4 = 0.5
    assert abs(mod._hapax_ratio(tokens) - 0.5) < 0.01


def test_feedback_event_count_aggregates():
    prefs = {
        "style_preferences": [{"key": "a", "value": 1}, {"key": "b", "value": 2}],
        "content_preferences": [{"key": "c"}],
        "workflow_preferences": [],
    }
    events = {
        "clusters": [
            {"cluster_id": "001", "user_choice": "走A"},
            {"cluster_id": "002", "_user_decision": "走B"},
            {"cluster_id": "003"},
        ]
    }
    proj = _mk_project(prefs=prefs, events=events)
    n = mod._feedback_event_count(proj)
    assert n == 3 + 2  # prefs=3·user_choice=2


def test_three_clusters_monotone_drop_flagged():
    # 制造 TTR 单调下降：重复度递增
    drafts = {
        "001": "他走她笑我哭你来" * 5,                       # 高 TTR
        "002": "他他他走走走她她她" * 5,                     # 中
        "003": "他他他他他他他他他" * 5,                     # 低
    }
    proj = _mk_project(drafts=drafts)
    rep = mod.build_dashboard(proj)
    codes = {a["code"] for a in rep["advisories"]}
    assert "WRITER_GROWTH_VOCAB_DROP" in codes


def test_no_response_flagged_when_feedback_high():
    drafts = {
        "001": "他走她笑我哭你来啊吧" * 5,    # 高 TTR (10 唯一字符)
        "002": "他走她笑我哭你来啊吧" * 5,    # 同
        "003": "他他他他他他他他他他" * 5,     # 低 (只 1 个 unique)
        "004": "他他他他他他他他他他" * 5,     # 低
    }
    prefs = {"style_preferences": [{"k": i} for i in range(6)]}
    proj = _mk_project(prefs=prefs, drafts=drafts)
    rep = mod.build_dashboard(proj)
    codes = {a["code"] for a in rep["advisories"]}
    assert "WRITER_GROWTH_NO_RESPONSE_TO_FEEDBACK" in codes or "WRITER_GROWTH_VOCAB_DROP" in codes


def test_too_few_clusters_no_advisory():
    drafts = {"001": "一些字符" * 5, "002": "另外内容" * 5}
    proj = _mk_project(drafts=drafts)
    rep = mod.build_dashboard(proj)
    assert rep["advisories"] == []


def test_main_cli_writes_out(tmp_path):
    proj = tmp_path / "proj"
    (proj / "_数据库").mkdir(parents=True)
    (proj / "章节").mkdir(parents=True)
    (proj / "章节" / "cluster_001_draft.txt").write_text("内容内容" * 50, encoding="utf-8")
    out = tmp_path / "report.json"
    rep = mod.build_dashboard(proj)
    out.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["scanner"] == "writer_growth_dashboard"


def test_gate_level_advisory():
    proj = _mk_project()
    rep = mod.build_dashboard(proj)
    assert rep["gate_level"] == "advisory"


def test_rows_shape():
    drafts = {"001": "你好世界写作" * 30}
    proj = _mk_project(drafts=drafts)
    rep = mod.build_dashboard(proj)
    assert len(rep["rows"]) == 1
    row = rep["rows"][0]
    for k in ("cluster_id", "cjk", "ttr", "mtld", "hapax_ratio"):
        assert k in row


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert "WRITER_GROWTH_VOCAB_DROP" not in audit_hub.HARD_GATE_CODES
    assert "WRITER_GROWTH_NO_RESPONSE_TO_FEEDBACK" not in audit_hub.HARD_GATE_CODES
