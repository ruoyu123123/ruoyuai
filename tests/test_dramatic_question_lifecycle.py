# -*- coding: utf-8 -*-
"""dramatic_question_lifecycle_scanner + manifest 注入 + emergence 软牵引 专属测试
🔴 2026-06-29 戏剧问题账本(PITQ/MDQ) · 读者粘性 P0

钉死：
  · compute_open_questions：raised−answered·累计 cutoff·按 qid 去重取最早
  · NO_OPEN_DRAMATIC_QUESTION（cluster 无 open PITQ）
  · DRAMATIC_QUESTION_STALE（悬挂 ≥N cluster）
  · OPEN_CLOSE_IMBALANCE（闭合率低 + 积压·Zeigarnik 反面）
  · 默认安全：无账本/无项目/无 cid → 零检测零注入
  · mode off/shadow/active · 永远 advisory · 三 code 绝不 hard_gate
  · build_manifest._collect_open_dramatic_questions 注入 + 紧迫度排序 + 默认安全
  · emergence _open_questions_keywords 软牵引 + _score_one_me 加分不硬筛
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import dramatic_question_lifecycle_scanner as dq  # noqa: E402
import build_manifest as bm  # noqa: E402
import cluster_emergence_engine as ce  # noqa: E402
import audit_hub  # noqa: E402


def _mk(ledger: dict) -> Path:
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "戏剧问题账本.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


def _q(qid, question="某个核心问题", window="3-6 cluster", scope="cluster", gap_type=None):
    d = {"qid": qid, "question": question, "scope": scope,
         "raised_at_scene": "0", "expected_payoff_window": window}
    if gap_type is not None:
        d["gap_type"] = gap_type
    return d


# ───────────────────────── compute_open_questions ─────────────────────────

def test_compute_open_basic():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1"), _q("Q2")], "answered": []},
        "cluster_002": {"raised": [_q("Q3")], "answered": [{"qid": "Q2", "answered_at_scene": "1"}]},
    }}
    m = dq.compute_open_questions(led, 2)
    open_ids = {q["qid"] for q in m["open"]}
    assert open_ids == {"Q1", "Q3"}, open_ids
    assert m["raised_unique"] == 3
    assert m["answered_unique"] == 1
    assert abs(m["close_ratio"] - 1 / 3) < 0.01


def test_compute_open_cumulative_cutoff():
    """target_num 之后 cluster 的 raised/answered 不参与（cluster 视野累计）。"""
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_005": {"raised": [_q("Q9")], "answered": [{"qid": "Q1", "answered_at_scene": "0"}]},
    }}
    m = dq.compute_open_questions(led, 2)  # cutoff at 2 → cluster_005 excluded
    open_ids = {q["qid"] for q in m["open"]}
    assert open_ids == {"Q1"}, open_ids  # Q1 still open (answered only at cluster_005>2)
    assert m["raised_unique"] == 1


def test_compute_open_dedup_earliest_raise():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_003": {"raised": [_q("Q1")], "answered": []},  # same qid raised again
    }}
    m = dq.compute_open_questions(led, 5)
    assert m["raised_unique"] == 1
    q = m["open"][0]
    assert q["raised_at_num"] == 1  # earliest
    assert q["staleness"] == 4  # 5 - 1


# ───────────────────────── 三类 issue code ─────────────────────────

def test_no_open_dramatic_question():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_002": {"raised": [], "answered": [{"qid": "Q1", "answered_at_scene": "1"}]},
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_002")
    codes = [v["code"] for v in r["violations"]]
    assert "NO_OPEN_DRAMATIC_QUESTION" in codes
    assert r["verdict"] == "FAIL_MINOR"


def test_dramatic_question_stale():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_010": {"raised": [], "answered": []},
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_010", stale_n=8)  # staleness 9 >= 8
    codes = [v["code"] for v in r["violations"]]
    assert "DRAMATIC_QUESTION_STALE" in codes
    stale_v = next(v for v in r["violations"] if v["code"] == "DRAMATIC_QUESTION_STALE")
    assert stale_v["stale_count"] == 1


def test_stale_n_configurable():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_004": {"raised": [], "answered": []},  # staleness 3
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    # default stale_n=8 → not stale
    r = dq.scan(str(proj), cluster_id="cluster_004")
    assert "DRAMATIC_QUESTION_STALE" not in [v["code"] for v in r["violations"]]
    # stale_n=2 → stale
    r2 = dq.scan(str(proj), cluster_id="cluster_004", stale_n=2)
    assert "DRAMATIC_QUESTION_STALE" in [v["code"] for v in r2["violations"]]


def test_open_close_imbalance():
    clusters = {}
    for i in range(1, 9):  # 8 raised
        clusters[f"cluster_{i:03d}"] = {"raised": [_q(f"Q{i}", window="5-9 cluster")], "answered": []}
    clusters["cluster_002"]["answered"] = [{"qid": "Q1", "answered_at_scene": "1"}]  # only 1 answered
    proj = _mk({"clusters": clusters})
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_008", stale_n=99)  # high stale_n to isolate imbalance
    codes = [v["code"] for v in r["violations"]]
    assert "OPEN_CLOSE_IMBALANCE" in codes
    assert r["metrics"]["close_ratio"] < 0.2


def test_healthy_no_violation():
    """开坑及时闭合 + 有 open 拉力 → 零 violation。"""
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1"), _q("Q2")], "answered": []},
        "cluster_002": {"raised": [_q("Q3")], "answered": [{"qid": "Q1", "answered_at_scene": "0"}]},
        "cluster_003": {"raised": [_q("Q4")], "answered": [{"qid": "Q2", "answered_at_scene": "0"}]},
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_003")
    assert r["verdict"] == "PASS"
    assert not r["violations"]


# ───────────────────────── 默认安全 + mode ─────────────────────────

def test_default_safe_no_ledger():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_003")
    assert r["verdict"] == "PASS"
    assert not r["violations"]
    assert "默认安全" in (r.get("note") or "")


def test_default_safe_no_project():
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(None, cluster_id="cluster_003")
    assert r["verdict"] == "PASS"
    assert not r["violations"]


def test_unregistered_cluster_no_false_positive():
    """账本里 raised 全为空（未登记）→ 不误报 NO_OPEN。"""
    led = {"clusters": {"cluster_001": {"raised": [], "answered": []}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_001")
    assert r["verdict"] == "PASS"
    assert not r["violations"]


def test_mode_off():
    led = {"clusters": {"cluster_001": {"raised": [_q("Q1")], "answered": [{"qid": "Q1"}]}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "off"
    r = dq.scan(str(proj), cluster_id="cluster_001")
    assert r["verdict"] == "PASS"
    assert not r["violations"]


def test_mode_shadow_records_but_not_surfaced():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_002": {"raised": [], "answered": [{"qid": "Q1"}]},
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "shadow"
    r = dq.scan(str(proj), cluster_id="cluster_002")
    assert r["verdict"] == "PASS"  # shadow never surfaces
    assert not r["violations"]
    assert r.get("violations_count", 0) >= 1  # but counted


def test_codes_never_hard_gate():
    """三个 code 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤）。"""
    for code in ("NO_OPEN_DRAMATIC_QUESTION", "DRAMATIC_QUESTION_STALE", "OPEN_CLOSE_IMBALANCE"):
        assert code not in audit_hub.HARD_GATE_CODES
        assert audit_hub._gate_level_for(code, "error") == "advisory"


def test_always_advisory_gate_level():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_002": {"raised": [], "answered": [{"qid": "Q1"}]},
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_002")
    assert r["gate_level"] == "advisory"


# ───────────────────────── build_manifest 注入 ─────────────────────────

class _FakeScanner:
    def __init__(self, root):
        self.root = Path(root)


def test_manifest_inject_open_questions():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1", "甲是凶手吗", "5-9 cluster")], "answered": []},
        "cluster_002": {"raised": [_q("Q2", "钥匙开什么门", "1-2 cluster")], "answered": []},
    }}
    proj = _mk(led)
    inj = bm._collect_open_dramatic_questions(_FakeScanner(proj), "cluster_002")
    assert inj is not None
    assert inj["gate_level"] == "advisory"
    assert inj["open_count"] == 2
    # 紧迫度排序：Q2 死线=2+2=4 < Q1 死线=1+9=10 → Q2 最前
    assert inj["open_questions"][0]["qid"] == "Q2"
    assert "directive" in inj


def test_manifest_default_safe_no_ledger():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert bm._collect_open_dramatic_questions(_FakeScanner(proj), "cluster_002") is None


def test_manifest_default_safe_no_cid():
    led = {"clusters": {"cluster_001": {"raised": [_q("Q1")], "answered": []}}}
    proj = _mk(led)
    assert bm._collect_open_dramatic_questions(_FakeScanner(proj), None) is None


def test_manifest_no_open_returns_none():
    """全部已闭合 → 不注入（零行为变化）。"""
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1")], "answered": []},
        "cluster_002": {"raised": [], "answered": [{"qid": "Q1"}]},
    }}
    proj = _mk(led)
    assert bm._collect_open_dramatic_questions(_FakeScanner(proj), "cluster_002") is None


def test_parse_payoff_deadline():
    assert bm._parse_payoff_deadline("3-6 cluster", 2) == 8  # 2+6
    assert bm._parse_payoff_deadline("cluster_011", 2) == 11  # absolute
    assert bm._parse_payoff_deadline("", 2) == 10 ** 6  # unparseable
    assert bm._parse_payoff_deadline("2 cluster", 5) == 7  # 5+2


# ───────────────────────── emergence 软牵引 ─────────────────────────

def test_emergence_open_keywords():
    led = {"clusters": {
        "cluster_001": {"raised": [_q("Q1", "陈临是不是叛徒")], "answered": []},
        "cluster_002": {"raised": [_q("Q2", "宝藏在哪里")], "answered": [{"qid": "Q1"}]},
    }}
    proj = _mk(led)
    kw = ce._open_questions_keywords(str(proj), 2)
    # Q1 answered → only Q2 ("宝藏在哪里") open
    assert any("宝藏" in k or "哪里" in k for k in kw)
    assert not any("叛徒" in k for k in kw)  # Q1 closed


def test_emergence_keywords_default_safe():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert ce._open_questions_keywords(str(proj), 3) == set()
    assert ce._open_questions_keywords(None, 3) == set()


def test_emergence_soft_pull_bonus_not_hard_filter():
    led = {"clusters": {"cluster_001": {"raised": [_q("Q1", "围绕危机牵引展开")], "answered": []}}}
    proj = _mk(led)
    kw = ce._open_questions_keywords(str(proj), 1)
    me_hit = {"id": "ME1", "description": "围绕危机牵引展开新冲突"}
    me_miss = {"id": "ME2", "description": "平静的日常过场"}
    s_hit, rs_hit = ce._score_one_me(me_hit, 0, set(), [], set(), [], None, kw)
    s_miss, rs_miss = ce._score_one_me(me_miss, 0, set(), [], set(), [], None, kw)
    assert s_hit > s_miss  # 软加分
    assert any("悬置" in r for r in rs_hit)
    # 不硬筛：miss 不被 -100 剔除（仍 >= 0）
    assert s_miss >= 0


def test_emergence_soft_pull_capped():
    """开放问题加分封顶 24（不盖过其它信号·北极星③大势已定）。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥")], "answered": []}}}
    proj = _mk(led)
    kw = ce._open_questions_keywords(str(proj), 1)
    me = {"id": "ME1", "description": "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"}
    score, _ = ce._score_one_me(me, 0, set(), [], set(), [], None, kw)
    assert score <= 24


# ───────────────── 🔴 2026-06-29 Sternberg 读者知识缺口三态 gap_type ─────────────────

def test_compute_open_captures_gap_type():
    """compute_open_questions 透传合法 gap_type·非法/缺省 → None。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", gap_type="suspense"), _q("Q2", gap_type="curiosity"),
        _q("Q3", gap_type="garbage"), _q("Q4")], "answered": []}}}
    m = dq.compute_open_questions(led, 1)
    by = {q["qid"]: q.get("gap_type") for q in m["open"]}
    assert by["Q1"] == "suspense" and by["Q2"] == "curiosity"
    assert by["Q3"] is None and by["Q4"] is None  # 非法 + 缺省 → None
    assert m["gap_type_dist"] == {"suspense": 1, "curiosity": 1}


def test_single_gap_type_monotone_fires():
    """≥3 个 open 问题全 suspense → SINGLE_GAP_TYPE_MONOTONE（缺三态混合）。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", gap_type="suspense"), _q("Q2", gap_type="suspense"),
        _q("Q3", gap_type="suspense")], "answered": []}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_001", stale_n=99)
    v = next((v for v in r["violations"] if v["code"] == "SINGLE_GAP_TYPE_MONOTONE"), None)
    assert v is not None
    assert v["gap_type"] == "suspense"
    assert set(v["missing_gap_types"]) == {"curiosity", "surprise"}
    assert v["typed_open_count"] == 3


def test_three_state_mix_no_monotone():
    """三态混合（suspense/curiosity/surprise）→ 不报单调（PASS 维度）。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", gap_type="suspense"), _q("Q2", gap_type="curiosity"),
        _q("Q3", gap_type="surprise")], "answered": []}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_001", stale_n=99)
    assert "SINGLE_GAP_TYPE_MONOTONE" not in [v["code"] for v in r["violations"]]


def test_two_typed_below_threshold_no_monotone():
    """仅 2 个带 gap_type 的 open 问题（< MIN=3）→ 不报（单一缺口对少量问题是自然的）。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", gap_type="suspense"), _q("Q2", gap_type="suspense")], "answered": []}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_001", stale_n=99)
    assert "SINGLE_GAP_TYPE_MONOTONE" not in [v["code"] for v in r["violations"]]


def test_untyped_questions_not_counted_default_safe():
    """旧账本 open 问题无 gap_type → 不计入·绝不报单调（默认安全·慢热单一缺口合法）。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1"), _q("Q2"), _q("Q3"), _q("Q4")], "answered": []}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_001", stale_n=99)
    assert "SINGLE_GAP_TYPE_MONOTONE" not in [v["code"] for v in r["violations"]]
    assert r["metrics"]["gap_type_dist"] == {}


def test_answered_excluded_from_monotone():
    """已闭合的问题不算 open → 不参与单调判定。"""
    led = {"clusters": {
        "cluster_001": {"raised": [
            _q("Q1", gap_type="suspense"), _q("Q2", gap_type="suspense"),
            _q("Q3", gap_type="suspense")], "answered": []},
        "cluster_002": {"raised": [], "answered": [
            {"qid": "Q2", "answered_at_scene": "0"}, {"qid": "Q3", "answered_at_scene": "0"}]},
    }}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "active"
    r = dq.scan(str(proj), cluster_id="cluster_002", stale_n=99)
    # 只剩 Q1 open（1 个 < MIN=3）→ 不报单调
    assert "SINGLE_GAP_TYPE_MONOTONE" not in [v["code"] for v in r["violations"]]


def test_gap_monotone_never_hard_gate():
    """SINGLE_GAP_TYPE_MONOTONE 绝不进 HARD_GATE_CODES（北极星⑤）。"""
    assert "SINGLE_GAP_TYPE_MONOTONE" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("SINGLE_GAP_TYPE_MONOTONE", "error") == "advisory"


def test_gap_monotone_shadow_not_surfaced():
    """shadow 模式下单调被计数但不上报（沿用三态门控）。"""
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", gap_type="curiosity"), _q("Q2", gap_type="curiosity"),
        _q("Q3", gap_type="curiosity")], "answered": []}}}
    proj = _mk(led)
    os.environ["DRAMATIC_QUESTION_LIFECYCLE_MODE"] = "shadow"
    r = dq.scan(str(proj), cluster_id="cluster_001", stale_n=99)
    assert r["verdict"] == "PASS"
    assert not r["violations"]
    assert r.get("violations_count", 0) >= 1


# ───────────────── build_manifest gap_type 分布注入 ─────────────────

def test_manifest_injects_gap_type_distribution():
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", "甲是凶手吗", gap_type="suspense"),
        _q("Q2", "宝藏哪来的", gap_type="curiosity")], "answered": []}}}
    proj = _mk(led)
    inj = bm._collect_open_dramatic_questions(_FakeScanner(proj), "cluster_001")
    assert inj is not None
    assert inj["gap_type_distribution"] == {"suspense": 1, "curiosity": 1}
    # 混合 → directive 不含单调软提示
    assert "维度单一" not in inj["directive"]


def test_manifest_monotone_soft_note_in_directive():
    led = {"clusters": {"cluster_001": {"raised": [
        _q("Q1", gap_type="suspense"), _q("Q2", gap_type="suspense"),
        _q("Q3", gap_type="suspense")], "answered": []}}}
    proj = _mk(led)
    inj = bm._collect_open_dramatic_questions(_FakeScanner(proj), "cluster_001")
    assert inj["gap_type_distribution"] == {"suspense": 3}
    assert "维度单一" in inj["directive"]  # 软提示三态混合（不替 writer 选）


def test_manifest_no_gap_type_empty_distribution():
    """旧账本无 gap_type → 分布空·directive 无单调提示（默认安全·向后兼容）。"""
    led = {"clusters": {"cluster_001": {"raised": [_q("Q1"), _q("Q2")], "answered": []}}}
    proj = _mk(led)
    inj = bm._collect_open_dramatic_questions(_FakeScanner(proj), "cluster_001")
    assert inj["gap_type_distribution"] == {}
    assert "维度单一" not in inj["directive"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
