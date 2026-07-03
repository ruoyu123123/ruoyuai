#!/usr/bin/env python3
"""test_preference_ranker.py — pairwise 偏好排序器(BPR)回归测试。

覆盖：真实 candidate brief schema 特征抽取 / 冷启动 / 训练确定性 / pairwise 方向正确性
(chosen 类特征得分 > rejected 类) / 持久化 round-trip / annotate_candidates 门控行为。

candidate brief 字段来自 cluster_emergence_engine.py:me_to_cluster_brief（实地读码，非臆造）。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import preference_ranker as pr  # noqa: E402


# ───────────────────── 特征抽取（真实 schema） ─────────────────────

# 与 cluster_emergence_engine.me_to_cluster_brief() 的真实返回结构对齐（字段名/取值一致）。
_REAL_CANDIDATE = {
    "cluster_id": "cluster_002_candidate_1",
    "parent_me": "ME-V1-05",
    "scope_summary": "[CANDIDATE 1] 围绕 ME「反派现身」展开。" + "细节描写"*8,
    "_emergence_score": 85,
    "_emergence_reasons": ["vol1 连续（与当前推进卷一致）", "呼应最近涟漪后果（重叠：玄铁令）"],
    "status": "candidate",
    "ME_to_advance": ["ME-V1-05"],
    "volume": 1,
    "is_volume_finale": True,
    "stakes_delta": "从暗中试探到正面对抗，压力陡增",
    "intent": None,
    "narrative_mode": "linear",
    "belief_update_intent": "update",
    "event_boundary_sharpness": "sharp",
    "scene_storyboard": [],
    "anchor_props": [],
    "foreshadowing_to_plant": [],
    "premise_blend_card": {"blend_type": ""},
    "research_ref": {},
}


def test_extract_features_matches_real_schema():
    feats = pr.extract_features(_REAL_CANDIDATE)
    assert feats["is_volume_finale"] == 1.0
    assert feats["belief_update_intent_update"] == 1.0
    assert feats["event_boundary_sharp"] == 1.0
    assert feats["narrative_mode_linear"] == 1.0
    assert feats["volume"] == 1.0
    assert feats["emergence_score"] == 85.0
    assert feats["emergence_reason_count"] == 2.0
    assert feats["scene_count"] == 0.0
    assert feats["anchor_prop_count"] == 0.0
    assert feats["foreshadowing_count"] == 0.0
    assert feats["me_count"] == 1.0
    assert feats["stakes_delta_present"] == 1.0
    assert feats["stakes_delta_length"] == float(len("从暗中试探到正面对抗，压力陡增"))
    assert feats["scope_length"] == float(len(_REAL_CANDIDATE["scope_summary"]))
    # intent=None → 不应产生任何 intent_* onehot key
    assert not any(k.startswith("intent_") for k in feats)
    # history_keywords 未传 → 该特征恒 0（不影响其余特征的纯函数性）
    assert feats["history_keyword_overlap"] == 0.0


def test_extract_features_non_finale_variant():
    """narrative_mode/intent 取不同值、is_volume_finale=False → 对应 onehot/binary 特征切换。"""
    cand = dict(_REAL_CANDIDATE)
    cand.update({
        "is_volume_finale": False,
        "narrative_mode": "kishotenketsu_4act",
        "intent": "healing",
        "belief_update_intent": None,
        "event_boundary_sharpness": "default",
        "stakes_delta": "",
    })
    feats = pr.extract_features(cand)
    assert feats["is_volume_finale"] == 0.0
    assert feats["belief_update_intent_update"] == 0.0
    assert feats["event_boundary_sharp"] == 0.0
    assert feats.get("narrative_mode_kishotenketsu_4act") == 1.0
    assert "narrative_mode_linear" not in feats
    assert feats.get("intent_healing") == 1.0
    assert feats["stakes_delta_present"] == 0.0
    assert feats["stakes_delta_length"] == 0.0


def test_extract_features_non_dict_input_safe():
    assert pr.extract_features(None) == {}
    assert pr.extract_features("not a dict") == {}


def test_candidate_text_and_history_keywords():
    text = pr.candidate_text(_REAL_CANDIDATE)
    assert "反派现身" in text or "压力陡增" in text
    obs = [{"chosen_text": "反派现身正面对抗"}, {"chosen_text": "无关内容"}, {"not_chosen_text": "x"}]
    kw = pr.build_history_keywords(obs)
    assert isinstance(kw, frozenset)
    assert len(kw) > 0


# ───────────────────── 训练：冷启动 / 确定性 / 方向正确性 ─────────────────────

def _mk_candidate(stakes_text: str, finale: bool = False) -> dict:
    return {
        "cluster_id": "x", "scope_summary": "场景描述",
        "stakes_delta": stakes_text, "is_volume_finale": finale,
        "narrative_mode": "linear", "intent": None,
        "belief_update_intent": None, "event_boundary_sharpness": "default",
        "_emergence_score": 10, "_emergence_reasons": [],
        "volume": 1, "scene_storyboard": [], "anchor_props": [],
        "foreshadowing_to_plant": [], "ME_to_advance": ["ME-1"],
    }


_LONG_STAKES = "从暗中试探到正面对抗，压力陡增，主角必须在信息差耗尽前做出抉择"
_SHORT_STAKES = ""


def _synthetic_observations(n: int) -> list:
    """构造「用户总选 stakes_delta 描述更详细/更长」的偏好——n 条 choice 记录，
    每条 chosen 都是长 stakes_delta、rejected 都是空 stakes_delta，其余字段全部相同
    （让 stakes_delta_length/stakes_delta_present 成为唯一系统性区分信号，训练信号干净）。
    """
    obs = []
    for i in range(n):
        chosen = _mk_candidate(_LONG_STAKES + str(i))
        rejected = _mk_candidate(_SHORT_STAKES)
        obs.append({
            "chosen_features": pr.extract_features(chosen),
            "chosen_text": pr.candidate_text(chosen),
            "rejected_features": [pr.extract_features(rejected)],
            "source_cluster": f"cluster_{i:03d}",
            "ts": "2026-07-03T00:00:00",
        })
    return obs


def test_cold_start_below_threshold_returns_none():
    obs = _synthetic_observations(pr.COLD_START_MIN_OBSERVATIONS - 1)
    assert pr.train(obs) is None


def test_cold_start_boundary_at_threshold_trains():
    obs = _synthetic_observations(pr.COLD_START_MIN_OBSERVATIONS)
    weights = pr.train(obs)
    assert weights is not None
    assert isinstance(weights, dict) and weights


def test_train_empty_or_missing_observations_returns_none():
    assert pr.train([]) is None
    assert pr.train(None) is None


def test_train_deterministic_same_input_same_weights():
    obs = _synthetic_observations(10)
    w1 = pr.train(obs)
    w2 = pr.train(obs)
    assert w1 == w2, "同输入两次 train() 权重必须逐位一致（固定 seed·纯 python 无并行源）"


def test_train_learns_chosen_over_rejected_direction():
    """核心正确性：pairwise 训练后，held-out「长 stakes_delta」候选打分应高于
    「短/空 stakes_delta」候选（用户历史总选详细描述 → 排序器学到该方向）。"""
    obs = _synthetic_observations(12)
    weights = pr.train(obs)
    assert weights is not None

    held_out_chosen_style = _mk_candidate("非常详尽的强度增量描述：主角从被动挨打转为主动出击")
    held_out_rejected_style = _mk_candidate("")

    s_chosen = pr.score(held_out_chosen_style, weights)
    s_rejected = pr.score(held_out_rejected_style, weights)
    assert s_chosen is not None and s_rejected is not None
    assert s_chosen > s_rejected, (
        f"chosen 类特征应得分更高：chosen={s_chosen} rejected={s_rejected} weights={weights}")


def test_score_none_when_weights_missing_or_empty():
    cand = _mk_candidate(_LONG_STAKES)
    assert pr.score(cand, None) is None
    assert pr.score(cand, {}) is None


# ───────────────────── 持久化 round-trip ─────────────────────

def test_save_load_round_trip():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        weights = {"stakes_delta_length": 0.01, "is_volume_finale": 0.3}
        path = pr.save(root, weights, n_observations=12)
        assert path.exists()
        assert path.name == pr.PERSIST_FILENAME

        loaded = pr.load(root)
        assert loaded is not None
        assert loaded["weights"] == weights
        assert loaded["n_observations"] == 12
        assert loaded["_schema"] == pr.SCHEMA
        assert set(loaded["feature_names"]) == set(weights.keys())
        assert "trained_at" in loaded


def test_load_missing_file_returns_none():
    with tempfile.TemporaryDirectory() as d:
        assert pr.load(Path(d)) is None


def test_load_corrupt_file_returns_none():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        p = pr.persist_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not valid json", encoding="utf-8")
        assert pr.load(root) is None


def test_load_no_weights_key_returns_none():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        p = pr.persist_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"_schema": pr.SCHEMA}), encoding="utf-8")
        assert pr.load(root) is None


# ───────────────────── env 门控 + annotate_candidates ─────────────────────

def test_enabled_default_off():
    import os
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ.pop(pr.ENV_FLAG, None)
        assert pr.enabled() is False
        os.environ[pr.ENV_FLAG] = "0"
        assert pr.enabled() is False
        os.environ[pr.ENV_FLAG] = "1"
        assert pr.enabled() is True
    finally:
        if bak is None:
            os.environ.pop(pr.ENV_FLAG, None)
        else:
            os.environ[pr.ENV_FLAG] = bak


def test_annotate_candidates_gate_off_is_noop():
    import os
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ.pop(pr.ENV_FLAG, None)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            briefs = [_mk_candidate(_LONG_STAKES), _mk_candidate(_SHORT_STAKES)]
            snapshot = json.dumps(briefs, sort_keys=True)
            n = pr.annotate_candidates(root, briefs)
            assert n == 0
            assert json.dumps(briefs, sort_keys=True) == snapshot, "门控关闭时 candidates 必须逐字节不变"
    finally:
        if bak is None:
            os.environ.pop(pr.ENV_FLAG, None)
        else:
            os.environ[pr.ENV_FLAG] = bak


def test_annotate_candidates_no_weights_file_is_noop():
    import os
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ[pr.ENV_FLAG] = "1"
        with tempfile.TemporaryDirectory() as d:
            briefs = [_mk_candidate(_LONG_STAKES)]
            n = pr.annotate_candidates(Path(d), briefs)
            assert n == 0
            assert "preference_score" not in briefs[0]
    finally:
        if bak is None:
            os.environ.pop(pr.ENV_FLAG, None)
        else:
            os.environ[pr.ENV_FLAG] = bak


def test_annotate_candidates_adds_fields_without_reordering_list():
    import os
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ[pr.ENV_FLAG] = "1"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr.save(root, {"stakes_delta_length": 0.02}, n_observations=10)
            briefs = [_mk_candidate(_SHORT_STAKES), _mk_candidate(_LONG_STAKES)]
            original_ids = [id(b) for b in briefs]
            n = pr.annotate_candidates(root, briefs)
            assert n == 2
            # 列表本身顺序/元素身份不变（只加字段·不重排/不替换元素）
            assert [id(b) for b in briefs] == original_ids
            for b in briefs:
                assert isinstance(b["preference_score"], float)
                assert isinstance(b["preference_rank_hint"], int)
            # 长 stakes_delta 权重为正 → 应排名靠前（rank_hint 更小）
            assert briefs[1]["preference_rank_hint"] < briefs[0]["preference_rank_hint"]
    finally:
        if bak is None:
            os.environ.pop(pr.ENV_FLAG, None)
        else:
            os.environ[pr.ENV_FLAG] = bak


# ───────────────────── 零依赖 runner（与仓库其余 test_cluster_emergence_engine_audit.py 同范式） ─────────────────────

def _run_all():
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"[OK] {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
