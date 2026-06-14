"""run_dimension_ablation 顶层编排单测（A1-A5 自证消融 · 2026-06-14）。

只测**编排 + 统计判据接线**——_run_one **全程 mock 不实跑 gen-model / 不发 subprocess /
不触网络**（依赖注入是为了可单测）。真跑留给主代理（experiment_gate · 需 gen-model API）。

🔴 零依赖纪律（与 test_ablation_stats.py 同款 idiom）：
  · 不用 pytest fixture（tmp_path/monkeypatch）—— run_tests.py 零依赖 runner 以无参调用
    test_ 函数。改用 tempfile.TemporaryDirectory() + monkeypatch=None 默认参数手动兜底。

覆盖：
  1. 正对照：ablated SFS 明显低于 baseline → effect_detail 反映显著退化
     （读 effect_significance_detail 真实返回字段：significant / direction / verdict）。
  2. 负对照：random_field SFS ≈ baseline → random_field_control=True 时 negative_control_pass=True。
  3. 编排正确：n_seed 个 seed 都调到；某 seed 返回 None 被过滤（n_seed_effective < n_seed）；
     random_field_control=False 时返回里没有 random_field_* 键。
  4. 不触发任何 subprocess / 网络（_run_one 全 mock·断言被调而非真跑）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import distill_replicate as dr  # noqa: E402
import distill_holdout as dh  # noqa: E402


# ============================================================
# mock _run_one 工厂：按 arm 返回固定分布的 SFS（不实跑 gen-model）
# ============================================================

def _make_mock_run_one(*, baseline, ablated, random=None, none_at=None, calls=None):
    """构造 mock _run_one。

    baseline / ablated / random：dict[seed:int -> float|None] 或 list（按 seed 索引）。
    none_at：set of (arm, seed) → 强制返回 None（模拟该 seed 失败被过滤）。
    calls：可选 list，记录每次调用 (arm, seed, ablate_dims, random_field)（断言编排）。
    """
    none_at = none_at or set()

    def _lookup(table, seed):
        if table is None:
            return None
        if isinstance(table, dict):
            return table.get(seed)
        return table[seed] if seed < len(table) else None

    def _run_one(arm, seed, ablate_dims, random_field):
        if calls is not None:
            calls.append((arm, seed, ablate_dims, random_field))
        if (arm, seed) in none_at:
            return None
        if arm == "baseline":
            return _lookup(baseline, seed)
        if arm == "ablated":
            return _lookup(ablated, seed)
        if arm == "random":
            return _lookup(random, seed)
        raise AssertionError(f"未知 arm: {arm}")

    return _run_one


# ============================================================
# 1. 正对照：抹真维退化超门 → significant=True / direction='ablation_degrades'
# ============================================================

def test_positive_control_ablation_degrades_significant():
    with tempfile.TemporaryDirectory() as td:
        # baseline 高分（~0.85）· ablated 明显低（~0.60）→ 抹该维退化
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["源作者参考文本"], _run_one=mock,
        )
    det = res["effect_detail"]
    # 读 effect_significance_detail 真实字段（不臆测）
    assert det["significant"] is True, f"应显著退化: {det}"
    assert det["direction"] == "ablation_degrades", f"方向应为抹掉退化: {det}"
    assert det["verdict"] == "effective_dim", f"应判定有效维: {det}"
    # effect = baseline_mean - ablated_mean > 0（注入侧更高）
    assert det["effect"] > 0
    # 顶层透出
    assert res["kind"] == "ablation"
    assert res["dimension"] == "A3"
    assert res["baseline_sfs"] == [0.85, 0.86, 0.84]
    assert res["ablated_sfs"] == [0.60, 0.62, 0.59]
    assert res["n_seed_effective"] == {"baseline": 3, "ablated": 3}
    # entry 是 build_ablation_entry 产物（kind='ablation' + 显著性证据）
    assert res["entry"]["kind"] == "ablation"
    assert res["entry"]["dimension"] == "A3"
    assert res["entry"]["verdict"] == "effective_dim"


def test_positive_control_detail_matches_holdout_direct():
    """编排算出的 effect_detail 必须与直接调 effect_significance_detail 一致（同一把尺）。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], _run_one=mock,
        )
    direct = dh.effect_significance_detail(
        [0.85, 0.86, 0.84], [0.60, 0.62, 0.59],
        probe_noise_floor=0.0, layer="surface")
    assert res["effect_detail"] == direct, "编排判据须与纯函数直算一致（不偷改量纲）"


# ============================================================
# 2. 负对照：random_field SFS ≈ baseline → negative_control_pass=True
# ============================================================

def test_negative_control_random_field_no_effect_pass():
    with tempfile.TemporaryDirectory() as td:
        # random 与 baseline 同分布（无显著差异）→ 统计层分得清噪声 → negative_control_pass=True
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
            random={0: 0.85, 1: 0.84, 2: 0.86},
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], random_field_control=True, _run_one=mock,
        )
    assert "random_field_sfs" in res
    assert "random_field_detail" in res
    assert "negative_control_pass" in res
    assert res["negative_control_pass"] is True, \
        f"随机字段无差异应通过负对照: {res['random_field_detail']}"
    # 随机 detail 应不显著
    assert res["random_field_detail"]["significant"] is False
    assert res["n_seed_effective"]["random"] == 3


def test_negative_control_random_field_significant_fails():
    """若随机字段竟造成显著差异（统计层分不清噪声）→ negative_control_pass=False（防假阳性）。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
            random={0: 0.40, 1: 0.41, 2: 0.39},  # 随机字段大幅掉分=异常
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], random_field_control=True, _run_one=mock,
        )
    assert res["random_field_detail"]["significant"] is True
    assert res["negative_control_pass"] is False, "随机字段显著退化应判负对照失败"


# ============================================================
# 3. 编排正确性
# ============================================================

def test_all_seeds_invoked_per_arm():
    """n_seed 个 seed 都调到（baseline + ablated 各 n_seed 次·random_field_control 时再 + n_seed）。"""
    calls = []
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84, 3: 0.85},
            ablated={0: 0.60, 1: 0.62, 2: 0.59, 3: 0.61},
            random={0: 0.85, 1: 0.84, 2: 0.86, 3: 0.85},
            calls=calls,
        )
        dr.run_dimension_ablation(
            project=td, cluster_ref=1, skill_version="v3",
            dimension="A3", n_seed=4, ref_texts=["ref"],
            random_field_control=True, _run_one=mock,
        )
    baseline_calls = [c for c in calls if c[0] == "baseline"]
    ablated_calls = [c for c in calls if c[0] == "ablated"]
    random_calls = [c for c in calls if c[0] == "random"]
    assert len(baseline_calls) == 4
    assert len(ablated_calls) == 4
    assert len(random_calls) == 4
    # 每 arm seed 覆盖 0..3
    assert sorted(c[1] for c in baseline_calls) == [0, 1, 2, 3]
    # baseline 的 ablate_dims 为空·random_field=False
    assert all(c[2] == "" and c[3] is False for c in baseline_calls)
    # ablated 的 ablate_dims 为该维·random_field=False
    assert all(c[2] == "A3" and c[3] is False for c in ablated_calls)
    # random 的 random_field=True
    assert all(c[3] is True for c in random_calls)


def test_none_seed_filtered_from_effective():
    """某 seed 返回 None → 被过滤·n_seed_effective < n_seed。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
            none_at={("baseline", 1), ("ablated", 2)},  # 各失败 1 个 seed
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], _run_one=mock,
        )
    assert res["n_seed_effective"]["baseline"] == 2  # 3 - 1 None
    assert res["n_seed_effective"]["ablated"] == 2
    assert res["baseline_sfs"] == [0.85, 0.84]  # seed1 (None) 被过滤
    assert res["ablated_sfs"] == [0.60, 0.62]  # seed2 (None) 被过滤


def test_no_random_keys_when_control_off():
    """random_field_control=False（默认）时返回里没有 random_field_* 键。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], _run_one=mock,  # random_field_control 默认 False
        )
    assert "random_field_sfs" not in res
    assert "random_field_detail" not in res
    assert "negative_control_pass" not in res
    assert "random" not in res["n_seed_effective"]


def test_insufficient_sample_inconclusive():
    """样本各 < 2 → effect_significance_detail 判 inconclusive（没调查没发言权·绝不宣称效应）。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85},  # 仅 1 个有效
            ablated={0: 0.60},
            none_at={("baseline", 1), ("baseline", 2), ("ablated", 1), ("ablated", 2)},
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], _run_one=mock,
        )
    assert res["effect_detail"]["verdict"] == "inconclusive"
    assert res["effect_detail"]["significant"] is False
    assert res["n_seed_effective"] == {"baseline": 1, "ablated": 1}


# ============================================================
# 4. 断点落盘 + 不触 subprocess / 网络
# ============================================================

def test_breakpoint_records_written():
    """每个 SFS 即时 append 落盘 _数据库/.ablation/<dimension>_<arm>.json（断点可查）。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
            none_at={("ablated", 2)},
        )
        dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], _run_one=mock,
        )
        abl_dir = Path(td) / "_数据库" / ".ablation"
        base_rec = abl_dir / "A3_baseline.json"
        abl_rec = abl_dir / "A3_ablated.json"
        assert base_rec.exists(), "baseline 断点文件应落盘"
        assert abl_rec.exists(), "ablated 断点文件应落盘"
        base_data = json.loads(base_rec.read_text(encoding="utf-8"))
        abl_data = json.loads(abl_rec.read_text(encoding="utf-8"))
    assert len(base_data) == 3, "baseline 3 个 seed 都应留痕（含成功）"
    assert len(abl_data) == 3, "ablated 3 个 seed 都应留痕（含 None 失败）"
    # None 的 seed 也落盘（sfs=None·断点可查哪个 seed 挂了）
    none_recs = [r for r in abl_data if r["sfs"] is None]
    assert len(none_recs) == 1 and none_recs[0]["seed"] == 2
    # 成功的 seed 落真分
    ok_recs = [r for r in base_data if r["sfs"] is not None]
    assert {r["seed"] for r in ok_recs} == {0, 1, 2}


def test_mock_path_never_touches_subprocess():
    """_run_one 注入时绝不构造真实 subprocess runner（不发 subprocess·不触网络）。

    手动 patch subprocess.run（不依赖 pytest monkeypatch fixture·零依赖 runner 也能跑）。
    """
    import subprocess
    sentinel = {"called": False}
    orig_run = subprocess.run

    def _boom(*a, **k):
        sentinel["called"] = True
        raise AssertionError("单测绝不允许真发 subprocess！")

    subprocess.run = _boom
    try:
        with tempfile.TemporaryDirectory() as td:
            mock = _make_mock_run_one(
                baseline={0: 0.85, 1: 0.86, 2: 0.84},
                ablated={0: 0.60, 1: 0.62, 2: 0.59},
            )
            res = dr.run_dimension_ablation(
                project=td, cluster_ref="cluster_001",
                skill_version="v3", dimension="A3", n_seed=3,
                ref_texts=["ref"], _run_one=mock,
            )
    finally:
        subprocess.run = orig_run
    assert sentinel["called"] is False, "mock 路径绝不触 subprocess"
    assert res["kind"] == "ablation"


def test_bad_cluster_ref_raises():
    """cluster_ref 无法解析 cluster_id（禁机械拼接）→ ValueError。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(baseline={0: 0.85}, ablated={0: 0.60})
        raised = False
        try:
            dr.run_dimension_ablation(
                project=td, cluster_ref="无数字的标识",
                skill_version="v3", dimension="A3", n_seed=3,
                ref_texts=["ref"], _run_one=mock,
            )
        except ValueError:
            raised = True
    assert raised, "无法解析 cluster_id 应抛 ValueError"


def test_north_star_advisory_note_present():
    """返回必带北极星⑤⑥ advisory 说明（提醒调用方不得据此自动改注入）·且无 hard_gate 字段。"""
    with tempfile.TemporaryDirectory() as td:
        mock = _make_mock_run_one(
            baseline={0: 0.85, 1: 0.86, 2: 0.84},
            ablated={0: 0.60, 1: 0.62, 2: 0.59},
        )
        res = dr.run_dimension_ablation(
            project=td, cluster_ref="cluster_001",
            skill_version="v3", dimension="A3", n_seed=3,
            ref_texts=["ref"], _run_one=mock,
        )
    assert "_north_star_note" in res
    assert "advisory" in res["_north_star_note"]
    # 绝不出现 gate_level / hard_gate（顾问非法官·北极星⑤）
    assert "gate_level" not in res
    assert "hard_gate" not in res
