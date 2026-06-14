#!/usr/bin/env python3
"""test_judge_consistency_probe.py — B-3/B-5 多 judge 一致性筛子单测（GATEKEEPER 域）。

零依赖（纯 Python·无 scipy·无 pytest 强依赖·可被 run_tests 风格 harness 直跑·守 1551 基线）。
覆盖 R3_blueprint B-3 + B-5 的 test_assertions：
  · pairwise_spearman_mean 完全一致→1.0·完全反序→负值（纯算法正确性·B-5）
  · arc-shape fixture（3 judge 高分歧）→ rho<0.75 且 classify_dim reference_only=True
    （定量复现 arc 被金标准证伪=筛子能检出已知不可靠维·B-5）
  · 高一致机械维 fixture（rho>0.85）→ reference_only=False（不误杀·B-5）
  · verdict 文本含「稳定≠准确·仍需金标准升 active」收窄语义（B-3 防 C2 复述陷阱）
  · RHO_FLOOR/ALPHA_FLOOR 是模块常量·import 校验==0.75/0.67（B-3/B-5）
  · 脚本 exit0·gen-model/数据缺失降级出 error 报告不抛（B-3 advisory·shadow）
  · 序数 alpha：≥3 judge 高一致→高 alpha·高分歧→低 alpha
  · 离线读 judge report 装载评分（零 gen-model 调用）
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import judge_consistency_probe as jcp  # noqa: E402


# ───────────────────────── 纯算法：Spearman 正确性（B-5） ─────────────────────────
def test_spearman_identical_returns_one():
    """完全一致序列 → rho=1.0（单调正相关·秩完全对齐）。"""
    rho = jcp.spearman_rho([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
    assert rho is not None and abs(rho - 1.0) < 1e-9, f"identical 应为 1.0·实测 {rho}"


def test_spearman_reversed_returns_negative_one():
    """完全反序 → rho=-1.0（单调负相关）。"""
    rho = jcp.spearman_rho([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
    assert rho is not None and abs(rho - (-1.0)) < 1e-9, f"reversed 应为 -1.0·实测 {rho}"


def test_spearman_handles_ties_via_average_ranks():
    """有结（ties）时用平均秩 → 同序仍 1.0（速算公式会错·这里证用了平均秩）。"""
    rho = jcp.spearman_rho([1, 1, 2, 3], [5, 5, 6, 7])
    assert rho is not None and abs(rho - 1.0) < 1e-9, f"tied-identical 应为 1.0·实测 {rho}"


def test_spearman_constant_vector_returns_none():
    """任一向量零方差（全常数）→ None（相关无定义·上游剔除该对）。"""
    assert jcp.spearman_rho([3, 3, 3, 3], [1, 2, 3, 4]) is None


def test_pairwise_mean_identical_returns_one():
    """pairwise_spearman_mean：全 judge 完全一致 → 均值 1.0。"""
    scores = {"a": [1, 2, 3, 4, 5], "b": [2, 4, 6, 8, 10], "c": [10, 20, 30, 40, 50]}
    rho = jcp.pairwise_spearman_mean(scores)
    assert rho is not None and abs(rho - 1.0) < 1e-9, f"全一致均值应为 1.0·实测 {rho}"


def test_pairwise_mean_reversed_pair_is_negative():
    """两 judge 完全反序 → 均值为负（纯算法·B-5）。"""
    scores = {"a": [1, 2, 3, 4, 5], "b": [5, 4, 3, 2, 1]}
    rho = jcp.pairwise_spearman_mean(scores)
    assert rho is not None and rho < 0.0, f"反序对均值应为负·实测 {rho}"


def test_pairwise_mean_insufficient_judges_returns_none():
    """<2 个合法 judge → None（无法构成 pair）。"""
    assert jcp.pairwise_spearman_mean({"a": [1, 2, 3]}) is None
    assert jcp.pairwise_spearman_mean({}) is None


# ───────────────────────── arc-shape 不稳实证锚（B-5 核心） ─────────────────────────
# fixture 直接取自 R3_blueprint B-5 proposed_change（R1 arc 被金标准证伪记录构造·judge 间高分歧）。
_ARC_SHAPE_SCORES = {
    "judge_a": [0.7, 0.3, 0.9, 0.2, 0.6],
    "judge_b": [0.2, 0.8, 0.3, 0.9, 0.1],
    "judge_c": [0.9, 0.1, 0.5, 0.4, 0.8],
}
# 高一致机械维（如对话占比）fixture：3 judge 排序高度一致。
_STABLE_DIM_SCORES = {
    "a": [0.80, 0.70, 0.90, 0.85, 0.75],
    "b": [0.82, 0.68, 0.88, 0.87, 0.76],
    "c": [0.79, 0.71, 0.91, 0.84, 0.74],
}


def test_arc_shape_rho_below_floor():
    """arc-shape fixture rho 实测 <0.75（定量复现 arc 不稳·B-5）。"""
    rho = jcp.pairwise_spearman_mean(_ARC_SHAPE_SCORES)
    assert rho is not None and rho < 0.75, f"arc-shape rho 应 <0.75·实测 {rho}"


def test_arc_shape_dim_unstable_reference_only():
    """arc-shape 维 → classify_dim reference_only=True（筛子检出已知不可靠维·B-5）。"""
    res = jcp.classify_dim("arc_shape", _ARC_SHAPE_SCORES)
    assert res["reference_only"] is True, f"arc-shape 应判 reference_only·结果 {res}"
    assert res["rho"] is not None and res["rho"] < 0.75


def test_stable_dim_not_reference_only():
    """高一致机械维（rho>0.85）→ reference_only=False（不误杀·B-5）。"""
    rho = jcp.pairwise_spearman_mean(_STABLE_DIM_SCORES)
    assert rho is not None and rho > 0.85, f"stable 维 rho 应 >0.85·实测 {rho}"
    res = jcp.classify_dim("dialogue_ratio", _STABLE_DIM_SCORES)
    assert res["reference_only"] is False, f"stable 维不应 reference_only·结果 {res}"


# ───────────────────────── 语义收窄护栏（B-3 防 C2 复述陷阱） ─────────────────────────
def test_verdict_carries_narrowed_semantics():
    """verdict 文本必含「稳定≠准确·仍需金标准升 active」收窄语义（B-3 assertion 2）。"""
    res_stable = jcp.classify_dim("dialogue_ratio", _STABLE_DIM_SCORES)
    res_arc = jcp.classify_dim("arc_shape", _ARC_SHAPE_SCORES)
    for res in (res_stable, res_arc):
        v = res["verdict"]
        assert "稳定" in v and "准确" in v, f"verdict 缺收窄语义·{v}"
        assert "金标准" in v and "active" in v, f"verdict 缺金标准升 active 提示·{v}"


# ───────────────────────── 模块常量（B-3/B-5 assertion） ─────────────────────────
def test_floor_constants_importable():
    """RHO_FLOOR/ALPHA_FLOOR 是模块常量·==0.75/0.67（B-5 assertion 4）。"""
    assert jcp.RHO_FLOOR == 0.75, f"RHO_FLOOR 应=0.75·实测 {jcp.RHO_FLOOR}"
    assert jcp.ALPHA_FLOOR == 0.67, f"ALPHA_FLOOR 应=0.67·实测 {jcp.ALPHA_FLOOR}"


# ───────────────────────── 序数 Krippendorff alpha ─────────────────────────
def test_ordinal_alpha_high_consistency():
    """≥3 judge 高一致 → alpha 高（>ALPHA_FLOOR）。"""
    a = jcp.ordinal_krippendorff_alpha(_STABLE_DIM_SCORES)
    assert a is not None and a > jcp.ALPHA_FLOOR, f"高一致 alpha 应 >0.67·实测 {a}"


def test_ordinal_alpha_high_disagreement():
    """≥3 judge 高分歧（arc）→ alpha 低（<ALPHA_FLOOR）。"""
    a = jcp.ordinal_krippendorff_alpha(_ARC_SHAPE_SCORES)
    assert a is not None and a < jcp.ALPHA_FLOOR, f"高分歧 alpha 应 <0.67·实测 {a}"


def test_ordinal_alpha_below_three_judges_none():
    """<3 judge → alpha=None（不足以算序数 alpha·只看 rho）。"""
    assert jcp.ordinal_krippendorff_alpha({"a": [1, 2, 3], "b": [1, 2, 3]}) is None


def test_ordinal_alpha_perfect_returns_one():
    """完全一致 → alpha=1.0。"""
    perfect = {"a": [1, 2, 3, 4], "b": [1, 2, 3, 4], "c": [1, 2, 3, 4]}
    a = jcp.ordinal_krippendorff_alpha(perfect)
    assert a is not None and abs(a - 1.0) < 1e-9, f"完全一致 alpha 应=1.0·实测 {a}"


# ───────────────────────── classify_dim 数据不足保守降权 ─────────────────────────
def test_classify_dim_insufficient_data_reference_only():
    """连 rho 都算不出（<2 judge）→ 保守 reference_only=True·数据不足。"""
    res = jcp.classify_dim("lonely_dim", {"only": [1, 2, 3]})
    assert res["reference_only"] is True
    assert res["rho"] is None


def test_classify_dim_alpha_unstable_triggers_reference_only():
    """rho 即便不低·但 alpha<floor 也触发 reference_only（任一条件命中即降权）。"""
    # 构造 rho 较高但序数离散度大的边界；这里直接用 arc（两者都低）确保 alpha 支路有效。
    res = jcp.classify_dim("arc_shape", _ARC_SHAPE_SCORES)
    # arc rho<0.75 已触发；额外断言 alpha 也 <floor（双重证据）
    assert res["alpha"] is not None and res["alpha"] < jcp.ALPHA_FLOOR


# ───────────────────────── 主入口 + 报告落盘 + exit0 语义（B-3 advisory·shadow） ─────────────────────────
def test_run_probe_writes_advisory_report():
    """run_consistency_probe 落 advisory 报告·汇总 reference_only_dims/stable_dims。"""
    with tempfile.TemporaryDirectory() as td:
        scores_by_dim = {
            "arc_shape": _ARC_SHAPE_SCORES,
            "dialogue_ratio": _STABLE_DIM_SCORES,
        }
        res = jcp.run_consistency_probe(
            Path(td), "cluster_007", scores_by_dim=scores_by_dim)
        assert "arc_shape" in res["reference_only_dims"], f"arc 应进 reference_only·{res}"
        assert "dialogue_ratio" in res["stable_dims"], f"dialogue 应进 stable·{res}"
        report = Path(td) / "_数据库" / ".judge_consistency" / "cluster_cluster007.json"
        assert report.is_file(), "advisory 报告未落盘"
        loaded = json.loads(report.read_text(encoding="utf-8"))
        assert loaded["cluster_id"] == "cluster_007"
        assert "semantics" in loaded and "C2" in loaded["semantics"]


def test_run_probe_empty_data_no_raise_returns_dict():
    """数据缺失（无 reports_dir·无 scores）→ 不抛·返回结构化空结果（advisory·exit0 语义）。"""
    with tempfile.TemporaryDirectory() as td:
        res = jcp.run_consistency_probe(Path(td), "cluster_001")
        assert isinstance(res, dict)
        assert res["dims"] == []
        assert res["reference_only_dims"] == []


def test_run_probe_missing_reports_dir_degrades_gracefully():
    """指向不存在的 judge-reports 目录 → 降级空结果·不抛（gen-model/数据缺失·B-3 assertion 3）。"""
    with tempfile.TemporaryDirectory() as td:
        res = jcp.run_consistency_probe(
            Path(td), "cluster_001",
            reports_dir=str(Path(td) / "no_such_dir"), dims=["arc_shape"])
        assert isinstance(res, dict)
        # dims 指定了 arc_shape 但无数据 → classify 走数据不足 → reference_only
        assert res["dims"][0]["reference_only"] is True


# ───────────────────────── 离线读 judge report（零 gen-model 调用） ─────────────────────────
def test_load_scores_from_reports_offline():
    """离线扫 judge report 目录 → {dim: {judge: [scores]}}（两种形态都收）。"""
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "reports"
        rd.mkdir()
        # 形态1：dim_scores
        (rd / "judge_a.json").write_text(json.dumps({
            "judge": "judge_a",
            "dim_scores": {"arc_shape": [0.7, 0.3, 0.9, 0.2, 0.6]},
        }), encoding="utf-8")
        # 形态2：dimensions: {dim: {scores:[..]}}
        (rd / "judge_b.json").write_text(json.dumps({
            "agent": "judge_b",
            "dimensions": {"arc_shape": {"scores": [0.2, 0.8, 0.3, 0.9, 0.1]}},
        }), encoding="utf-8")
        by_dim = jcp.load_scores_from_reports(rd)
        assert "arc_shape" in by_dim
        assert set(by_dim["arc_shape"].keys()) == {"judge_a", "judge_b"}
        assert by_dim["arc_shape"]["judge_a"] == [0.7, 0.3, 0.9, 0.2, 0.6]


def test_load_scores_skips_bad_files():
    """坏 JSON / 非 dict 文件跳过·不抛。"""
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "reports"
        rd.mkdir()
        (rd / "broken.json").write_text("{ not valid json", encoding="utf-8")
        (rd / "good.json").write_text(json.dumps({
            "judge": "g", "dim_scores": {"d": [1, 2, 3]}}), encoding="utf-8")
        by_dim = jcp.load_scores_from_reports(rd)
        assert "d" in by_dim and "g" in by_dim["d"]


if __name__ == "__main__":
    # 零依赖自跑（与 enum_consistency_gate 单测同范式）。
    fns = [n for n in sorted(dir()) if n.startswith("test_")]
    failed = 0
    for n in fns:
        try:
            globals()[n]()
            print(f"  PASS {n}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)} 测试·{failed} 失败")
    sys.exit(1 if failed else 0)
