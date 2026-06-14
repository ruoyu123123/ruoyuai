"""消融统计基建测试（R3 ABLATION 域 · 2026-06-14）— 守 P0 消融实验纯函数层。

只测**确定性纯函数 + env toggle**（零 LLM / 零网络 / 零 gen-model）。消融驱动 + A1-A5
真机自证（抹 A3 看 post_climax_retention 退化）需 gen-model API → 走
distill_replicate.run_dimension_ablation 的 experiment_gate，本文件**不**覆盖。

覆盖任务书 4 块验收：
  1. seed_level_std — 同 cluster 多 seed 的 seed-level std（≈statistics.stdev 无偏·runs<2→0.0）
  2. effect_significant — 效应是否超 max(k·1σ_seed, probe_noise_floor)（表层/思维分栏门）
  3. estimate_cost — gen_throttle 节流外推墙钟 + over_budget 退路
  4. ABLATE_DIMENSIONS toggle — 关某维注入·其余维不变·默认空零回归（含 ABLATE_RANDOM_FIELD 负对照）

全程 advisory/experiment·绝不进 hard_gate（北极星⑤⑥）。
"""
import os
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import distill_holdout as dh  # noqa: E402


# ============================================================
# 0. import 不触网不调模型（纯统计层纪律）
# ============================================================

def test_import_no_network_no_model():
    """import distill_holdout 只拉 stdlib + distill_track（纯函数）·不触 openai/gen-model。"""
    assert "openai" not in sys.modules, "纯统计层 import 不该拉 openai"
    # 关键纯函数都在
    for fn in ("seed_level_std", "effect_significant", "effect_significance_detail",
               "estimate_cost", "build_ablation_entry"):
        assert hasattr(dh, fn), f"缺纯函数 {fn}"


# ============================================================
# 1. seed_level_std — 同 cluster 多 seed 的 seed-level std（无偏 n-1）
# ============================================================

def test_seed_level_std_matches_stdev_unbiased():
    runs = [72.1, 70.5, 71.8]
    got = dh.seed_level_std(runs)
    assert abs(got - statistics.stdev(runs)) < 1e-9, "应等于无偏样本标准差（n-1）"


def test_seed_level_std_two_runs_ok():
    runs = [60.0, 64.0]
    got = dh.seed_level_std(runs)
    assert abs(got - statistics.stdev(runs)) < 1e-9  # 2 趟即可算（≥2）


def test_seed_level_std_single_run_is_zero():
    assert dh.seed_level_std([71.0]) == 0.0, "runs<2 无法估抖动 → 0.0（调用方退保守）"


def test_seed_level_std_empty_is_zero():
    assert dh.seed_level_std([]) == 0.0


def test_seed_level_std_local_fallback_equivalent(monkeypatch=None):
    """distill_track 不可用（_HAVE_DT=False）时本地兜底须给同一无偏值。"""
    saved = dh._HAVE_DT
    try:
        dh._HAVE_DT = False
        runs = [72.1, 70.5, 71.8]
        assert abs(dh.seed_level_std(runs) - statistics.stdev(runs)) < 1e-9
    finally:
        dh._HAVE_DT = saved


# ============================================================
# 2. effect_significant — 超 max(k·1σ_seed, probe_noise_floor) 门
# ============================================================

def test_effect_significant_true_above_sigma():
    # effect 6.0 > max(1.0×1.2, 0) = 1.2 → True
    assert dh.effect_significant(6.0, 1.2, k=1.0) is True


def test_effect_significant_false_within_sigma():
    # effect 0.8 < max(1.0×1.5, 0) = 1.5 → False
    assert dh.effect_significant(0.8, 1.5) is False


def test_effect_significant_thinking_layer_takes_max():
    """思维层 probe_noise_floor=0.28 > 1σ_seed=0.1 时门取大者 → 小效应被探针噪声压住。"""
    # effect 0.2 · 1σ_seed=0.1（k=1）· probe_floor=0.28 → 门=max(0.1,0.28)=0.28 → 0.2<0.28 → False
    assert dh.effect_significant(0.2, 0.1, probe_noise_floor=0.28) is False
    # 同 1σ_seed 但表层（floor=0）→ 门=0.1 → 0.2>0.1 → True（表层比思维层更易显著·分栏不同门）
    assert dh.effect_significant(0.2, 0.1, probe_noise_floor=0.0) is True


def test_effect_significant_boundary_strict_gt():
    # 恰好等于门 → False（严格 > · 不把临界值算显著）
    assert dh.effect_significant(1.2, 1.2, k=1.0) is False


def test_effect_significant_uses_abs():
    # 负效应（抹掉反而涨）也按绝对值判显著
    assert dh.effect_significant(-6.0, 1.2, k=1.0) is True


def test_effect_significant_none_effect_false():
    assert dh.effect_significant(None, 1.2) is False


def test_effect_threshold_picks_max():
    assert dh.effect_threshold(1.2, probe_noise_floor=0.28, k=1.0) == 1.2  # 1σ 高
    assert dh.effect_threshold(0.1, probe_noise_floor=0.28, k=1.0) == 0.28  # floor 高
    assert dh.effect_threshold(2.0, probe_noise_floor=0.0, k=0.5) == 1.0   # k 折扣


# ============================================================
# 2b. effect_significance_detail — 完整证据（供 ledger entry / 自证）
# ============================================================

def test_significance_detail_effective_dim_when_ablation_degrades():
    """抹掉该维退化（baseline 高 / ablated 低）且超门 → effective_dim·direction degrades。"""
    baseline = [72.0, 71.0, 73.0]   # 含 A3 注入
    ablated = [62.0, 61.0, 63.0]    # 抹 A3 后
    det = dh.effect_significance_detail(baseline, ablated, layer="surface")
    assert det["effect"] == 10.0    # baseline_mean 72 − ablated_mean 62
    assert det["significant"] is True
    assert det["direction"] == "ablation_degrades"
    assert det["verdict"] == "effective_dim"


def test_significance_detail_noise_when_flat():
    """baseline≈ablated（随机字段那种）→ 门内 → noise/dead_dim（防假阳性）。"""
    baseline = [70.0, 71.0, 70.5]
    ablated = [70.2, 70.8, 70.6]
    det = dh.effect_significance_detail(baseline, ablated, layer="surface")
    assert det["significant"] is False
    assert det["verdict"] == "noise"


def test_significance_detail_thinking_floor_suppresses_small_effect():
    """思维层 probe_noise_floor 大于 1σ_seed 时·小效应被探针噪声压成 noise。"""
    baseline = [70.0, 70.1, 69.9]   # 1σ_seed 很小
    ablated = [69.5, 69.6, 69.4]    # 效应 ~0.5
    det = dh.effect_significance_detail(baseline, ablated, layer="thinking",
                                        probe_noise_floor=2.0)
    assert det["effect_threshold_final"] == 2.0  # floor 主导
    assert det["significant"] is False
    assert det["verdict"] == "noise"


def test_significance_detail_inconclusive_when_insufficient():
    det = dh.effect_significance_detail([70.0], [60.0])  # 各 1 趟 → 无 seed std
    assert det["significant"] is False
    assert det["verdict"] == "inconclusive"


# ============================================================
# 3. estimate_cost — gen_throttle 节流外推墙钟 + over_budget
# ============================================================

def test_estimate_cost_core_numbers():
    est = dh.estimate_cost(7, 5, 3, 2, 4.5)
    assert est["conditions"] == 8          # 1 baseline + 7 抹维
    assert est["replicas"] == 120          # 8 × 5 seed × 3 cluster
    assert est["gen_calls"] == 240         # 120 × 2 sub-call
    # 墙钟 = 240 × 4.5 / 3600 = 0.30 小时
    assert abs(est["wall_hours"] - 0.30) < 1e-9
    assert est["over_budget"] is False     # 未传预算 → 不超


def test_estimate_cost_over_budget_when_budget_small():
    est = dh.estimate_cost(7, 5, 3, 2, 4.5, budget_hours=0.1)
    assert est["wall_hours"] > 0.1
    assert est["over_budget"] is True


def test_estimate_cost_not_over_budget_when_budget_large():
    est = dh.estimate_cost(7, 5, 3, 2, 4.5, budget_hours=10.0)
    assert est["over_budget"] is False


def test_estimate_cost_scales_with_dims_and_seeds():
    base = dh.estimate_cost(1, 1, 1, 1, 4.5)
    assert base["conditions"] == 2 and base["replicas"] == 2 and base["gen_calls"] == 2
    more = dh.estimate_cost(3, 2, 2, 2, 4.5)
    assert more["conditions"] == 4 and more["replicas"] == 16 and more["gen_calls"] == 32


# ============================================================
# 3b. build_ablation_entry — kind='ablation' 行（与回归行共存不互污）
# ============================================================

def test_build_ablation_entry_shape():
    e = dh.build_ablation_entry(
        "v3", "A3", "cluster_001",
        baseline_runs=[72.0, 71.0, 73.0], ablated_runs=[62.0, 61.0, 63.0],
        layer="thinking", probe_noise_floor=0.28)
    assert e["kind"] == "ablation", "消融行必带 kind 区分回归行（无 kind）"
    assert e["dimension"] == "A3"
    assert e["layer"] == "thinking"
    assert e["cluster_ref"] == "cluster_001"
    assert e["effect"] == 10.0
    assert e["significant"] is True
    assert e["verdict"] == "effective_dim"
    # baseline/ablated 原分留痕
    assert e["baseline_scores"] == [72.0, 71.0, 73.0]
    assert e["ablated_scores"] == [62.0, 61.0, 63.0]


def test_build_ablation_entry_surface_floor_zero():
    """表层维 probe_noise_floor 缺省 0 → effect_threshold_final == threshold_1sigma。"""
    e = dh.build_ablation_entry(
        "v3", "A1", "cluster_001",
        baseline_runs=[70.0, 71.0, 72.0], ablated_runs=[69.0, 70.0, 71.0],
        layer="surface")
    assert e["probe_noise_floor"] == 0.0
    assert e["effect_threshold_final"] == e["threshold_1sigma"]


def test_build_ablation_entry_ledger_roundtrip_coexists():
    """消融行 append 进同一 ledger 后能读回·与回归行（distill_track.build_entry）共存不互污。"""
    import distill_track as dt
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        ledger = dt.load_ledger(proj)
        # 回归行（无 kind）
        ledger = dt.append_entry(ledger, dt.build_entry("v3", "cluster_001", [70.0, 71.0]))
        # 消融行（kind='ablation'）
        abl = dh.build_ablation_entry("v3", "A3", "cluster_001",
                                      baseline_runs=[72.0, 73.0],
                                      ablated_runs=[62.0, 63.0])
        ledger = dt.append_entry(ledger, abl)
        dt.save_ledger(proj, ledger)
        back = dt.load_ledger(proj)
        kinds = [e.get("kind") for e in back["entries"]]
        assert kinds == [None, "ablation"], "回归行无 kind·消融行 kind=ablation·共存可区分"
        assert back["entries"][1]["verdict"] == "effective_dim"


# ============================================================
# 4. ABLATE_DIMENSIONS toggle（build_manifest · 关某维注入·其余维不变·默认空零回归）
# ============================================================

# 复用同一 author profile（含 A1-A5 五个节奏维 + B1/C1 决策刻画维）做 mock s。
_NR_PROFILE = {
    "narrative_rhythm": {
        "beat_transition_matrix": {"行动->反应": {"count": 9, "prob": 0.6}},  # A1
        "scene_turn_ratio": 0.82,                                            # A2
        "tension_trajectory": {"post_climax_retention": 0.93,                # A3
                               "dominant_emotion_shape": "升-降-升"},
        "hook_type_distribution": ["悬念钩", "情绪钩"],                       # A4
        "hook_payoff_gap_median": 3,                                         # A4
        "propulsion_density": ["每章1推进", "信息增量稳"],                    # A5
    },
    "author_decision_principles": {"B1": "道德留白", "B2": "心理距离冷"},
    "characterization_craft": {"C1": "动作露性格", "C2": "对话藏潜台词"},
}


class _MockScanner:
    """最小 DatabaseScanner 替身：_collect_* 只用到 has_style_profile/load/db/ch。"""
    def __init__(self, db, profile):
        self.db = Path(db)
        self.ch = 1
        self._profile = profile

    def has_style_profile(self):
        return True

    def load(self, name, default=None):
        if name == "作者风格":
            return self._profile
        return default


def _rhythm_directives(monkeyenv):
    """跑 _collect_author_rhythm_signature·返回注入的 directives（None→[]）。"""
    import importlib
    bm = importlib.import_module("build_manifest")
    saved = dict(os.environ)
    try:
        # 关掉 shadow/D2 干扰·确保 active 注入
        os.environ["RHYTHM_INJECT_MODE"] = "active"
        os.environ.pop("D2_TENSION_TYPE_INJECT_MODE", None)
        os.environ.pop("ABLATE_DIMENSIONS", None)
        os.environ.pop("ABLATE_RANDOM_FIELD", None)
        for k, v in monkeyenv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        with tempfile.TemporaryDirectory() as td:
            s = _MockScanner(td, _NR_PROFILE)
            payload = bm._collect_author_rhythm_signature(s)
            return (payload or {}).get("directives", []), payload
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _has(directives, kw):
    return any(kw in d for d in directives)


def test_ablate_empty_zero_regression_all_dims_present():
    """ABLATE_DIMENSIONS 未设/空 → 6 条节奏 directive 全在（零回归快照）。"""
    dirs, _ = _rhythm_directives({})
    assert _has(dirs, "节拍转移主调")          # A1
    assert _has(dirs, "场景价值翻转率")        # A2
    assert _has(dirs, "张力后段保持度")        # A3
    assert _has(dirs, "情绪弧主形态")          # A3
    assert _has(dirs, "钩子类型偏好")          # A4
    assert _has(dirs, "悬念兑现章距中位")      # A4
    assert _has(dirs, "推进密度基线")          # A5
    # 空串也视作零回归
    dirs2, _ = _rhythm_directives({"ABLATE_DIMENSIONS": ""})
    assert _has(dirs2, "张力后段保持度")


def test_ablate_a3_drops_tension_keeps_others():
    """ABLATE_DIMENSIONS=A3 → 不含张力后段保持度/情绪弧·其余维仍在。"""
    dirs, _ = _rhythm_directives({"ABLATE_DIMENSIONS": "A3"})
    assert not _has(dirs, "张力后段保持度"), "A3 抹掉张力后段保持度"
    assert not _has(dirs, "情绪弧主形态"), "情绪弧主形态属 A3 也抹掉"
    # 其余 5 维仍在
    assert _has(dirs, "节拍转移主调")
    assert _has(dirs, "场景价值翻转率")
    assert _has(dirs, "钩子类型偏好")
    assert _has(dirs, "推进密度基线")


def test_ablate_normalizes_case_and_chinese_comma():
    """大小写 / 中文逗号 / 空白混入 → 归一化后正确匹配（a3==A3==' A3 '）。"""
    for val in ("a3", " A3 ", "a3，A1", "A3, A1"):
        dirs, _ = _rhythm_directives({"ABLATE_DIMENSIONS": val})
        assert not _has(dirs, "张力后段保持度"), f"{val!r} 应抹 A3"
        if "A1" in val.upper():
            assert not _has(dirs, "节拍转移主调"), f"{val!r} 含 A1 应抹节拍转移"


def test_ablate_a4_drops_both_hook_directives():
    """A4 跨两条 directive（钩子类型 + 兑现章距）→ 两条都抹·其余在。"""
    dirs, _ = _rhythm_directives({"ABLATE_DIMENSIONS": "A4"})
    assert not _has(dirs, "钩子类型偏好")
    assert not _has(dirs, "悬念兑现章距中位")
    assert _has(dirs, "张力后段保持度")  # A3 不受影响


def test_ablate_random_field_injects_marked_control():
    """ABLATE_RANDOM_FIELD=1 → 注入随机负对照 directive·payload._ablation_random=True。"""
    dirs, payload = _rhythm_directives({"ABLATE_RANDOM_FIELD": "1"})
    assert payload["_ablation_random"] is True
    assert _has(dirs, "消融负对照"), "随机字段注入一条标记 directive"
    # 真实 6 维仍全在（随机字段是额外负对照·不抹真维）
    assert _has(dirs, "张力后段保持度")
    # 未设 → 不注入·_ablation_random=False
    dirs2, payload2 = _rhythm_directives({})
    assert payload2["_ablation_random"] is False
    assert not _has(dirs2, "消融负对照")


def test_ablate_decision_principles_drops_matched_keys():
    """ABLATE_DIMENSIONS=B1,C2 → decision/characterization payload 剔对应键·其余键在。"""
    import importlib
    bm = importlib.import_module("build_manifest")
    saved = dict(os.environ)
    try:
        os.environ["DECISION_INJECT_MODE"] = "active"
        os.environ["ABLATE_DIMENSIONS"] = "B1,C2"
        with tempfile.TemporaryDirectory() as td:
            s = _MockScanner(td, _NR_PROFILE)
            payload = bm._collect_author_decision_principles(s)
        dec = payload["author_decision_principles"]
        cha = payload["characterization_craft"]
        assert "B1" not in dec and "B2" in dec, "B1 抹掉·B2 保留"
        assert "C2" not in cha and "C1" in cha, "C2 抹掉·C1 保留"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_ablate_decision_empty_zero_regression():
    """ABLATE_DIMENSIONS 空 → 决策/刻画键全在（零回归）。"""
    import importlib
    bm = importlib.import_module("build_manifest")
    saved = dict(os.environ)
    try:
        os.environ["DECISION_INJECT_MODE"] = "active"
        os.environ.pop("ABLATE_DIMENSIONS", None)
        with tempfile.TemporaryDirectory() as td:
            s = _MockScanner(td, _NR_PROFILE)
            payload = bm._collect_author_decision_principles(s)
        assert set(payload["author_decision_principles"]) == {"B1", "B2"}
        assert set(payload["characterization_craft"]) == {"C1", "C2"}
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ============================================================
# 5. 北极星⑤⑥：消融码绝不进 hard_gate
# ============================================================

def test_ablation_codes_never_in_hard_gate():
    """消融/留出 advisory 码绝不进 audit_hub.HARD_GATE_CODES（顾问非法官·防越权）。"""
    try:
        import audit_hub
    except Exception:
        return  # audit_hub 不可用则跳过（环境差异·不误失败）
    hg = getattr(audit_hub, "HARD_GATE_CODES", set())
    for code in ("HOLDOUT_SFS_GAP_OVERFIT", "ABLATION_EFFECT", "ABLATE_DIMENSIONS"):
        assert code not in hg, f"{code} 不得进 hard_gate（北极星⑤⑥ advisory only）"
