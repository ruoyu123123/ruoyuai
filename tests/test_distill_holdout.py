"""distill_holdout 留出验证测试 — 守护 2026-05-31 防 metric overfit 件。

只测**确定性纯函数层** + tmpdir IO 往返（report 写 / 挂 ledger）。零 LLM / 零网络 /
零 gen-model（score_pair 不实跑 style_evaluator 评分；过拟合判定全用直传 SFS 分）。

覆盖任务书 3 点验收：
  1. 留出切分（split_clusters · 确定性可复现 · explicit_refs / frac / n / too_small）
  2. 落差计算（compute_gap · tuning_mean − holdout_mean · 空组不可比）
  3. 过拟合 advisory 阈值（detect_overfit · 超阈值=overfit · 阈值内=healthy ·
     holdout 更高=holdout_better · 样本不足=insufficient · 永远 advisory 不阻断）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import distill_holdout as dh
import distill_track as dt


# ============================================================
# 1. split_clusters — 确定性留出切分
# ============================================================

def test_split_deterministic_same_seed_same_split():
    pool = [f"cluster_{i:03d}" for i in range(1, 9)]
    a = dh.split_clusters(pool, holdout_frac=0.25, seed=42)
    b = dh.split_clusters(pool, holdout_frac=0.25, seed=42)
    assert a == b, "同池同 seed 必须切出同一结果（可复现）"
    # tuning + holdout 覆盖全池且不交叠
    assert sorted(a["tuning"] + a["holdout"]) == sorted(pool)
    assert set(a["tuning"]) & set(a["holdout"]) == set()


def test_split_frac_count():
    pool = [f"c{i}" for i in range(8)]  # 8 个 · 0.25 → 留出 2
    res = dh.split_clusters(pool, holdout_frac=0.25, seed=1)
    assert len(res["holdout"]) == 2
    assert len(res["tuning"]) == 6
    assert res["strategy"] == "frac"


def test_split_explicit_n_overrides_frac():
    pool = [f"c{i}" for i in range(10)]
    res = dh.split_clusters(pool, holdout_n=3, holdout_frac=0.5, seed=1)
    assert len(res["holdout"]) == 3
    assert res["strategy"] == "explicit_n"


def test_split_explicit_refs_pins_holdout():
    pool = ["cluster_001", "cluster_002", "cluster_003"]
    res = dh.split_clusters(pool, holdout_refs=["cluster_002"], seed=99)
    assert res["holdout"] == ["cluster_002"]
    assert res["tuning"] == ["cluster_001", "cluster_003"]
    assert res["strategy"] == "explicit_refs"


def test_split_explicit_refs_ignores_unknown():
    pool = ["cluster_001", "cluster_002"]
    res = dh.split_clusters(pool, holdout_refs=["cluster_999"])
    assert res["holdout"] == []  # 不在池里 → 忽略（防拼写错钉死空 holdout）
    assert res["tuning"] == ["cluster_001", "cluster_002"]


def test_split_clamps_tuning_never_empty():
    """frac=1.0 也不能把 tuning 抽空（至少留 1 个 tuning）。"""
    pool = ["c1", "c2", "c3"]
    res = dh.split_clusters(pool, holdout_frac=1.0)
    assert len(res["tuning"]) >= 1
    assert len(res["holdout"]) == len(pool) - len(res["tuning"])


def test_split_too_small_pool_no_holdout():
    res = dh.split_clusters(["only_one"])
    assert res["holdout"] == []
    assert res["tuning"] == ["only_one"]
    assert res["strategy"] == "too_small"


def test_split_dedups_repeated_ids():
    res = dh.split_clusters(["c1", "c1", "c2", "c2"], holdout_n=1, seed=3)
    allids = res["tuning"] + res["holdout"]
    assert sorted(allids) == ["c1", "c2"], "重复 id 去重·不得既 tuning 又 holdout"


# ============================================================
# 2. compute_gap — 落差计算
# ============================================================

def test_compute_gap_positive_when_holdout_lower():
    g = dh.compute_gap([72.0, 70.0], [60.0, 62.0])
    assert g["tuning_mean"] == 71.0
    assert g["holdout_mean"] == 61.0
    assert g["gap"] == 10.0  # tuning − holdout = 过拟合方向
    assert g["tuning_n"] == 2 and g["holdout_n"] == 2


def test_compute_gap_negative_when_holdout_higher():
    g = dh.compute_gap([60.0], [70.0])
    assert g["gap"] == -10.0  # holdout 反而更高 → 泛化好


def test_compute_gap_none_when_holdout_empty():
    g = dh.compute_gap([70.0, 72.0], [])
    assert g["holdout_mean"] is None
    assert g["gap"] is None
    assert g["holdout_n"] == 0


def test_compute_gap_none_when_tuning_empty():
    g = dh.compute_gap([], [60.0])
    assert g["tuning_mean"] is None
    assert g["gap"] is None


# ============================================================
# 3. overfit_threshold + detect_overfit — 过拟合 advisory 阈值
# ============================================================

def test_overfit_threshold_floor_when_low_score():
    # tuning_mean 50 · rel 0.08×50=4.0 = floor → 取 floor 4.0
    assert dh.overfit_threshold(50.0, gap_floor=4.0, gap_rel=0.08) == 4.0


def test_overfit_threshold_relative_when_high_score():
    # tuning_mean 90 · rel 0.08×90=7.2 > floor 4 → 取 7.2
    assert abs(dh.overfit_threshold(90.0, gap_floor=4.0, gap_rel=0.08) - 7.2) < 1e-9


def test_overfit_threshold_none_score_falls_to_floor():
    assert dh.overfit_threshold(None, gap_floor=4.0) == 4.0


def test_detect_overfit_beyond_threshold_flags():
    """holdout 大幅低于 tuning（超阈值）→ overfit advisory。"""
    det = dh.detect_overfit([72.0, 70.0], [58.0, 60.0])  # gap=12 · 阈值~5.7
    assert det["applicable"] is True
    assert det["overfit"] is True
    assert det["verdict"] == "overfit"
    assert det["gap"] == 12.0


def test_detect_overfit_within_threshold_is_healthy():
    """holdout 略低于 tuning（阈值内）→ healthy（正常波动·非过拟合·防误报打转）。"""
    det = dh.detect_overfit([72.0, 70.0], [69.0, 68.0])  # gap=2.5 < floor 4
    assert det["overfit"] is False
    assert det["verdict"] == "healthy"


def test_detect_overfit_holdout_higher_is_healthy():
    """holdout 不低于 tuning → 绝不报过拟合（泛化好/holdout_better）。"""
    det = dh.detect_overfit([60.0], [70.0])
    assert det["overfit"] is False
    assert det["verdict"] == "holdout_better"
    det2 = dh.detect_overfit([60.0], [60.0])
    assert det2["verdict"] == "healthy"  # 持平也是健康


def test_detect_overfit_insufficient_when_no_holdout():
    """无 holdout 样本 → insufficient · 不下过拟合结论（没调查没发言权）。"""
    det = dh.detect_overfit([72.0, 70.0], [])
    assert det["applicable"] is False
    assert det["overfit"] is False
    assert det["verdict"] == "insufficient"


def test_detect_overfit_respects_min_holdout_n():
    """min_holdout_n=2 时单个 holdout 样本 → insufficient（要求够样本才判）。"""
    det = dh.detect_overfit([72.0], [50.0], min_holdout_n=2)
    assert det["applicable"] is False
    assert det["verdict"] == "insufficient"


def test_detect_overfit_high_threshold_suppresses_high_score_noise():
    """高分段相对阈值放宽：tuning 95 holdout 89 落差 6 < 0.08×95=7.6 → 不误报。"""
    det = dh.detect_overfit([95.0], [89.0], gap_floor=4.0, gap_rel=0.08)
    assert det["overfit"] is False
    assert det["verdict"] == "healthy"


# ============================================================
# 4. build_holdout_report — mode 行为 + advisory gate_level 永远 advisory
# ============================================================

def test_report_active_surfaces_advisory():
    rep = dh.build_holdout_report([72.0, 70.0], [55.0, 57.0],
                                  skill_version="v3", mode="active")
    assert rep["judged"] is True
    assert len(rep["advisory_issues"]) == 1
    iss = rep["advisory_issues"][0]
    assert iss["code"] == "HOLDOUT_SFS_GAP_OVERFIT"
    assert iss["gate_level"] == "advisory", "永远 advisory · 绝不 hard_gate（北极星⑤）"


def test_report_shadow_hides_advisory_top_level():
    """shadow 模式：算落差但不把 advisory 提顶层（校准期不打扰）。"""
    rep = dh.build_holdout_report([72.0], [50.0], mode="shadow")
    assert rep["advisory_issues"] == []
    assert rep["detection"]["overfit"] is True  # 仍藏在 detection 里
    assert rep["judged"] is True


def test_report_off_not_judged():
    rep = dh.build_holdout_report([72.0], [50.0], mode="off")
    assert rep["judged"] is False
    assert rep["advisory_issues"] == []


def test_report_healthy_no_advisory():
    rep = dh.build_holdout_report([70.0], [69.0], mode="active")
    assert rep["advisory_issues"] == []


# ============================================================
# 5. holdout_mode env — 默认 active
# ============================================================

def test_holdout_mode_default_active():
    saved = os.environ.pop("HOLDOUT_SFS_MODE", None)
    try:
        assert dh.holdout_mode() == "active"
    finally:
        if saved is not None:
            os.environ["HOLDOUT_SFS_MODE"] = saved


def test_holdout_mode_reads_env():
    saved = os.environ.get("HOLDOUT_SFS_MODE")
    try:
        os.environ["HOLDOUT_SFS_MODE"] = "shadow"
        assert dh.holdout_mode() == "shadow"
        os.environ["HOLDOUT_SFS_MODE"] = "OFF"
        assert dh.holdout_mode() == "off"
        os.environ["HOLDOUT_SFS_MODE"] = "garbage"
        assert dh.holdout_mode() == "active"  # 非法 → active
    finally:
        if saved is None:
            os.environ.pop("HOLDOUT_SFS_MODE", None)
        else:
            os.environ["HOLDOUT_SFS_MODE"] = saved


# ============================================================
# 6. IO 往返：cmd_record 写 report + --track 挂 ledger（永不阻断 return 0）
# ============================================================

def _record_args(proj, **over):
    class A:
        project = str(proj)
        skill_version = "v1"
        tuning_sfs = [72.0, 70.0]
        holdout_sfs = [55.0, 57.0]
        tuning_report = []
        holdout_report = []
        tuning_ref = ["cluster_001", "cluster_002"]
        holdout_ref = ["cluster_003"]
        model = "deepseek-v4-pro"
        track = False
        git_sha = "sha0000"
        gap_floor = dh.DEFAULT_GAP_FLOOR
        gap_rel = dh.DEFAULT_GAP_REL
        min_holdout_n = dh.MIN_HOLDOUT_N
    a = A()
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_cmd_record_writes_report_and_returns_zero():
    saved = os.environ.get("HOLDOUT_SFS_MODE")
    os.environ["HOLDOUT_SFS_MODE"] = "active"
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "蛊真人"
            rc = dh.cmd_record(_record_args(proj))
            assert rc == 0, "record 永远 return 0（advisory 不阻断）"
            p = dh.report_path(proj, "v1")
            assert p.exists()
            rep = json.loads(p.read_text(encoding="utf-8"))
            assert rep["detection"]["overfit"] is True
            assert rep["advisory_issues"][0]["gate_level"] == "advisory"
    finally:
        if saved is None:
            os.environ.pop("HOLDOUT_SFS_MODE", None)
        else:
            os.environ["HOLDOUT_SFS_MODE"] = saved


def test_cmd_record_track_appends_holdout_column_to_ledger():
    """--track 把 holdout 落差挂进 distill_track 的同一 ledger（holdout 列可见）。"""
    saved = os.environ.get("HOLDOUT_SFS_MODE")
    os.environ["HOLDOUT_SFS_MODE"] = "active"
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "蛊真人"
            rc = dh.cmd_record(_record_args(proj, track=True))
            assert rc == 0
            led = json.loads(dt.ledger_path(proj).read_text(encoding="utf-8"))
            assert len(led["entries"]) == 1
            e = led["entries"][0]
            assert e["cluster_ref"] == "holdout"
            assert e["sfs_mean"] == 56.0  # holdout 均值入账（泛化真分）
            assert e["holdout"]["overfit"] is True
            assert e["holdout"]["gap"] == 15.0
    finally:
        if saved is None:
            os.environ.pop("HOLDOUT_SFS_MODE", None)
        else:
            os.environ["HOLDOUT_SFS_MODE"] = saved


def test_cmd_record_no_tuning_returns_usage_error():
    """无 tuning 分 → return 1（参数缺失）· 不崩溃。"""
    saved = os.environ.get("HOLDOUT_SFS_MODE")
    os.environ["HOLDOUT_SFS_MODE"] = "active"
    try:
        with tempfile.TemporaryDirectory() as td:
            a = _record_args(Path(td), tuning_sfs=[], tuning_report=[])
            assert dh.cmd_record(a) == 1
    finally:
        if saved is None:
            os.environ.pop("HOLDOUT_SFS_MODE", None)
        else:
            os.environ["HOLDOUT_SFS_MODE"] = saved


def test_cmd_record_off_mode_skips_returns_zero():
    saved = os.environ.get("HOLDOUT_SFS_MODE")
    os.environ["HOLDOUT_SFS_MODE"] = "off"
    try:
        with tempfile.TemporaryDirectory() as td:
            assert dh.cmd_record(_record_args(Path(td))) == 0
    finally:
        if saved is None:
            os.environ.pop("HOLDOUT_SFS_MODE", None)
        else:
            os.environ["HOLDOUT_SFS_MODE"] = saved


def test_cmd_split_returns_zero_and_pins():
    class S:
        cluster = ["cluster_001", "cluster_002", "cluster_003"]
        holdout_frac = 0.25
        holdout_n = None
        holdout_ref = ["cluster_002"]
        seed = 42
        json = False
    assert dh.cmd_split(S()) == 0


# ---- distill_track holdout 列扩展（build_entry 接受 holdout · dashboard 不崩）----

def test_distill_track_build_entry_accepts_holdout():
    e = dt.build_entry("v1", "holdout", [56.0],
                       holdout={"gap": 15.0, "overfit": True, "verdict": "overfit"})
    assert e["holdout"]["overfit"] is True
    # 不传 holdout → 不写该字段（不污染纯 tuning 记录 schema）
    e2 = dt.build_entry("v1", "cluster_001", [70.0])
    assert "holdout" not in e2


def test_distill_track_dashboard_renders_holdout_without_crash():
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        ledger = {"schema_version": 1, "entries": [
            dt.build_entry("v1", "cluster_001", [70.0, 71.0]),
            dt.build_entry("v1", "holdout", [56.0, 55.0],
                           holdout={"gap": 15.0, "overfit": True,
                                    "verdict": "overfit"}),
        ]}
        dt.save_ledger(proj, ledger)

        class D:
            project = str(proj)
            cluster_ref = None
            abs_floor = dt.DEFAULT_ABS_FLOOR
            std_k = dt.DEFAULT_STD_K
        assert dt.cmd_dashboard(D()) == 0
