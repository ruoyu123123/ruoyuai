#!/usr/bin/env python3
"""test_enum_consistency_gate.py — G3-ENUMKAPPA 枚举维标注一致性前置闸单测。

零依赖（纯 Python·无 pytest 强依赖·可被 run_tests 风格 harness 直跑）。
覆盖 R3_blueprint D2-5-g3 的 6 条 test_assertions：
  · fleiss_kappa 全一致→1.0 / 全随机→≈0
  · run_enum_kappa_gate 高一致→should_inject=True·mode_ratio≈1.0
  · 分歧大→should_inject=False
  · 单 cluster 有效点<3→low_confidence=True·should_inject=False
  · exit0 不抛错（advisory）·报告落盘 .enum_consistency/
  · align_by_pct_bucket N 次点数不齐（4点 vs 3点）按 10% 格对齐
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import enum_consistency_gate as g3  # noqa: E402


# ───────────────────────── 测试工具 ─────────────────────────
def _surface(points: list[dict]) -> dict:
    """构造一份 judge .data（含 qualitative_dims.dim51_张力曲线 点列表）。"""
    return {"qualitative_dims": {"dim51_张力曲线": points}}


def _mock_judge(samples: list[dict]):
    """生成可注入的 judge_fn：每次调用按序返回 samples 里的一份（含 .data 属性的对象）。"""
    seq = list(samples)
    idx = {"i": 0}

    class _Outcome:
        def __init__(self, data):
            self.data = data

    def _fn(agent_name, project_root, *, params=None, **kw):
        i = idx["i"]
        idx["i"] = i + 1
        return _Outcome(seq[i] if i < len(seq) else {})

    return _fn


# ───────────────────────── fleiss_kappa ─────────────────────────
def test_fleiss_kappa_all_agree_returns_one():
    """3 raters 全一致（每 item 同类）→ 1.0。"""
    rows = [{"suspense": 3}, {"suspense": 3}, {"suspense": 3}]
    k = g3.fleiss_kappa(rows)
    assert k == 1.0, f"全一致应 1.0，得 {k}"


def test_fleiss_kappa_all_agree_mixed_categories_returns_one():
    """每 item 内部全一致但 item 间类别不同 → 仍 1.0（item 内一致是 Fleiss 满分）。"""
    rows = [{"suspense": 3}, {"curiosity": 3}, {"surprise": 3}]
    k = g3.fleiss_kappa(rows)
    assert k == 1.0, f"item 内全一致应 1.0，得 {k}"


def test_fleiss_kappa_random_returns_near_zero():
    """构造平衡随机分布（2 类 3 raters·2 极端 + 6 分裂·p_j=0.5）→ kappa≈0。"""
    rows = (
        [{"a": 3}, {"b": 3}]               # 2 个极端一致 item
        + [{"a": 2, "b": 1}] * 3           # 3 个 a 多数
        + [{"a": 1, "b": 2}] * 3           # 3 个 b 多数
    )
    k = g3.fleiss_kappa(rows)
    assert abs(k) < 0.05, f"平衡随机应≈0，得 {k}"


def test_fleiss_kappa_empty_returns_zero():
    """空/无有效 item → 0.0（保守·不放行注入）。"""
    assert g3.fleiss_kappa([]) == 0.0
    assert g3.fleiss_kappa([{}, {"x": 0}]) == 0.0


def test_fleiss_kappa_partial_agreement_in_range():
    """部分一致 → kappa 落 (0,1) 开区间（合理性·非边界值）。

    构造平衡类别（p_a=p_b=0.5·P_e=0.5）+ 多数 item 高度一致（3-0）少数分裂（2-1），
    观测一致 0.778 > 期望 0.5 → kappa=0.556（区别于全偏一类时 P_e 高致负 kappa）。
    """
    rows = [{"a": 3}, {"b": 3}, {"a": 3}, {"b": 3}, {"a": 2, "b": 1}, {"b": 2, "a": 1}]
    k = g3.fleiss_kappa(rows)
    assert 0.0 < k < 1.0, f"部分一致应落 (0,1)，得 {k}"


# ───────────────────────── align_by_pct_bucket ─────────────────────────
def test_align_by_pct_bucket_unequal_point_counts():
    """N 次点数不齐（4 点 vs 3 点）按 10% 格对齐：同格字段值累计成 {category:count}。"""
    s1 = _surface([
        {"pct": 5, "tension_type": "suspense"},   # 格 0
        {"pct": 35, "tension_type": "curiosity"},  # 格 3
        {"pct": 65, "tension_type": "surprise"},   # 格 6
        {"pct": 95, "tension_type": "suspense"},   # 格 9
    ])
    s2 = _surface([
        {"pct": 8, "tension_type": "suspense"},    # 格 0（与 s1 第一点对齐）
        {"pct": 38, "tension_type": "curiosity"},  # 格 3
        {"pct": 92, "tension_type": "surprise"},   # 格 9（与 s1 第4点不同类·测对齐）
    ])
    items = g3.align_by_pct_bucket([s1, s2], "dim51_张力曲线", "tension_type",
                                   bucket_fn=lambda x: x)
    # 格 0：suspense×2 ；格 3：curiosity×2 ；格 6：surprise×1 ；格 9：suspense×1 + surprise×1
    assert {"suspense": 2} in items, f"格0应 suspense×2，得 {items}"
    assert {"curiosity": 2} in items, f"格3应 curiosity×2，得 {items}"
    assert {"surprise": 1} in items, f"格6应 surprise×1，得 {items}"
    g9 = next((it for it in items if it.get("suspense") == 1 and it.get("surprise") == 1), None)
    assert g9 is not None, f"格9应 suspense×1+surprise×1，得 {items}"


def test_align_drops_invalid_categories():
    """归一为 未分类/其他/空 的点剔除（不计入分母·保守）。"""
    s1 = _surface([
        {"pct": 10, "tension_type": "suspense"},
        {"pct": 50, "tension_type": "未分类"},   # bucket_fn=身份 → 直接剔除
        {"pct": 90, "tension_type": ""},          # 空 → 剔除
    ])
    items = g3.align_by_pct_bucket([s1], "dim51_张力曲线", "tension_type",
                                   bucket_fn=lambda x: x)
    assert items == [{"suspense": 1}], f"仅有效点保留，得 {items}"


def test_align_uses_real_bucket_fn():
    """用真 consolidate._tension_type_bucket：中文描述映射到三桶。"""
    from consolidate_author_profile import _tension_type_bucket
    s1 = _surface([
        {"pct": 10, "tension_type": "高压开头·倒计时炸弹"},   # → suspense
        {"pct": 50, "tension_type": "抛谜想知道为什么"},       # → curiosity
        {"pct": 90, "tension_type": "结尾反转打脸"},           # → surprise
    ])
    items = g3.align_by_pct_bucket([s1], "dim51_张力曲线", "tension_type",
                                   bucket_fn=_tension_type_bucket)
    cats = {c for it in items for c in it}
    assert cats == {"suspense", "curiosity", "surprise"}, f"真 bucket 映射三桶，得 {items}"


# ───────────────────────── run_enum_kappa_gate ─────────────────────────
def test_gate_high_agreement_injects():
    """3 份高度一致 dim51（全 suspense·各点）→ should_inject=True·mode_ratio≈1.0。"""
    pts = [
        {"pct": 10, "tension_type": "suspense"},
        {"pct": 50, "tension_type": "suspense"},
        {"pct": 90, "tension_type": "suspense"},
    ]
    judge = _mock_judge([_surface(pts), _surface(pts), _surface(pts)])
    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_001", n_samples=3, judge_fn=judge)
    assert res["should_inject"] is True, f"高一致应注入，得 {res}"
    assert res["low_confidence"] is False, f"高一致非 low_confidence，得 {res}"
    assert abs(res["mode_ratio"] - 1.0) < 1e-6, f"mode_ratio 应≈1.0，得 {res['mode_ratio']}"


def test_gate_high_disagreement_no_inject():
    """3 份分歧大（同 pct 格 suspense/curiosity/surprise 各一）→ should_inject=False。"""
    judge = _mock_judge([
        _surface([{"pct": 10, "tension_type": "suspense"},
                  {"pct": 50, "tension_type": "suspense"},
                  {"pct": 90, "tension_type": "suspense"}]),
        _surface([{"pct": 10, "tension_type": "curiosity"},
                  {"pct": 50, "tension_type": "curiosity"},
                  {"pct": 90, "tension_type": "curiosity"}]),
        _surface([{"pct": 10, "tension_type": "surprise"},
                  {"pct": 50, "tension_type": "surprise"},
                  {"pct": 90, "tension_type": "surprise"}]),
    ])
    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_002", n_samples=3, judge_fn=judge)
    assert res["should_inject"] is False, f"分歧大不应注入，得 {res}"
    assert res["low_confidence"] is False, f"有 ≥3 有效点·非 low_confidence，得 {res}"
    # 每格 3 类各一 → 众数占比 1/3·kappa 应为负或≈0（远低于阈）
    assert res["mode_ratio"] < 0.6, f"分歧大众数占比应 <0.6，得 {res['mode_ratio']}"
    assert res["fleiss_kappa"] < 0.67, f"分歧大 kappa 应 <0.67，得 {res['fleiss_kappa']}"


def test_gate_low_confidence_when_few_valid_points():
    """单 cluster 有效 type 点 <3 → low_confidence=True·should_inject=False。"""
    # 每份只 1 个有效点（其余无 tension_type 字段→未分类剔除）→ 跨格有效点不足 3
    judge = _mock_judge([
        _surface([{"pct": 10, "tension_type": "suspense"},
                  {"pct": 50}, {"pct": 90}]),
        _surface([{"pct": 12}, {"pct": 50}, {"pct": 90}]),
        _surface([{"pct": 11}, {"pct": 50}, {"pct": 90}]),
    ])
    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_003", n_samples=3, judge_fn=judge)
    assert res["low_confidence"] is True, f"有效点<3 应 low_confidence，得 {res}"
    assert res["should_inject"] is False, f"low_confidence 不注入，得 {res}"


def test_gate_missing_tension_type_field_low_confidence():
    """旧 surface 完全无 tension_type 字段（real 数据形态）→ 全未分类 → low_confidence。"""
    old_pts = [{"pct": 10, "tension": "8", "valence": "-"},
               {"pct": 50, "tension": "6", "valence": "-"},
               {"pct": 90, "tension": "9", "valence": "-"}]
    judge = _mock_judge([_surface(old_pts), _surface(old_pts), _surface(old_pts)])
    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_004", n_samples=3, judge_fn=judge)
    assert res["low_confidence"] is True, f"无 tension_type 应 low_confidence，得 {res}"
    assert res["should_inject"] is False


def test_gate_exit0_no_throw_and_report_written():
    """exit0 不抛错（advisory）+ 报告落盘 _数据库/.enum_consistency/<cluster>_g3.json。"""
    pts = [{"pct": 10, "tension_type": "suspense"},
           {"pct": 50, "tension_type": "suspense"},
           {"pct": 90, "tension_type": "suspense"}]
    judge = _mock_judge([_surface(pts)] * 3)
    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_005", n_samples=3, judge_fn=judge)
        report = Path(td) / "_数据库" / ".enum_consistency" / "cluster005_g3.json"
        assert report.exists(), f"报告应落盘，未见 {report}"
        loaded = json.loads(report.read_text(encoding="utf-8"))
        assert loaded["cluster_id"] == "cluster_005"
        assert "should_inject" in loaded and "fleiss_kappa" in loaded
    assert isinstance(res, dict)


def test_gate_never_throws_on_broken_judge():
    """judge_fn 抛异常 → 闸吞掉不抛·返回保守结果（北极星⑤·advisory）。"""
    def _broken(agent_name, project_root, *, params=None, **kw):
        raise RuntimeError("judge 炸了（模拟中转站 520）")

    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_006", n_samples=3, judge_fn=_broken)
    assert res["should_inject"] is False, "judge 全炸应保守不注入"
    assert res["low_confidence"] is True
    # 全空 sample → 有效点 0 < 3 → 走 low_confidence 分支（非 error 分支也可接受）
    assert isinstance(res.get("total_valid_points", 0), int)


def test_gate_return_keys_contract():
    """返回值四契约键齐全（should_inject/mode_ratio/fleiss_kappa/low_confidence）。"""
    pts = [{"pct": 10, "tension_type": "suspense"},
           {"pct": 50, "tension_type": "suspense"},
           {"pct": 90, "tension_type": "suspense"}]
    judge = _mock_judge([_surface(pts)] * 3)
    with tempfile.TemporaryDirectory() as td:
        res = g3.run_enum_kappa_gate(Path(td), "cluster_007", n_samples=3, judge_fn=judge)
    for k in ("should_inject", "mode_ratio", "fleiss_kappa", "low_confidence"):
        assert k in res, f"契约键 {k} 缺失，得 {res.keys()}"


if __name__ == "__main__":
    fns = sorted(n for n in dir() if n.startswith("test_"))
    failed = 0
    for n in fns:
        try:
            globals()[n]()
            print(f"  [OK] {n}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns)} 测试·{failed} 失败")
    sys.exit(1 if failed else 0)
