# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN信息密度集成
"""test_surprisal_scanner.py — surprisal 信息密度 scanner 单测。

覆盖：
  1. bridge 的 env 门控（RUOYU_NN_SURPRISAL 未设 → 返回 None）
  2. bridge 的 env 门控（RUOYU_NN_SURPRISAL=1 但 venv 不存在 → 返回 None）
  3. scanner 的 4 个检测逻辑（mock bridge 返回假数据）
  4. scanner 在 bridge 全部返回 None 时静默降级
  5. surprisal_infer 的统计量计算（纯函数·无 torch 依赖）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# 确保项目路径可 import
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "core" / "ml" / "surprisal"))


# ============ 1. surprisal_infer 统计量纯函数测试（无 torch 依赖） ============

class TestComputeStats:
    """测试 _compute_stats 纯函数。"""

    def test_empty(self):
        from surprisal_infer import _compute_stats
        result = _compute_stats([])
        assert result["mean_surprisal"] is None
        assert result["token_count"] == 0

    def test_single_value(self):
        from surprisal_infer import _compute_stats
        result = _compute_stats([5.0])
        assert result["mean_surprisal"] == 5.0
        assert result["std_surprisal"] == 0.0
        assert result["token_count"] == 1

    def test_known_values(self):
        from surprisal_infer import _compute_stats
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        result = _compute_stats(values)
        assert result["token_count"] == 8
        assert abs(result["mean_surprisal"] - 5.0) < 0.01
        assert result["max_surprisal"] == 9.0
        assert result["min_surprisal"] == 2.0
        assert result["std_surprisal"] > 0

    def test_uniform_values(self):
        from surprisal_infer import _compute_stats
        result = _compute_stats([3.0, 3.0, 3.0, 3.0])
        assert result["mean_surprisal"] == 3.0
        assert result["std_surprisal"] == 0.0
        assert result["skewness"] == 0.0
        assert result["kurtosis"] == 0.0


# ============ 2. nn_surprisal_bridge env 门控测试 ============

class TestBridgeEnvGate:
    """测试 bridge 的 env 门控。"""

    def test_default_off_returns_none(self):
        """RUOYU_NN_SURPRISAL 未设 → predict_batch 返回全 None。"""
        import nn_surprisal_bridge as bridge
        env = {k: v for k, v in os.environ.items() if k != "RUOYU_NN_SURPRISAL"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = bridge.predict_batch(["测试文本", "另一段"])
            assert result == [None, None]

    def test_enabled_false_without_env(self):
        """RUOYU_NN_SURPRISAL 未设 → enabled() 返回 False。"""
        import nn_surprisal_bridge as bridge
        env = {k: v for k, v in os.environ.items() if k != "RUOYU_NN_SURPRISAL"}
        with mock.patch.dict(os.environ, env, clear=True):
            assert bridge.enabled() is False

    def test_enabled_false_no_venv(self):
        """RUOYU_NN_SURPRISAL=1 但 venv python 不存在 → enabled() 返回 False。"""
        import nn_surprisal_bridge as bridge
        with mock.patch.dict(os.environ, {"RUOYU_NN_SURPRISAL": "1"}):
            with mock.patch.object(bridge, "_VENV_PY", Path("/nonexistent/python.exe")):
                with mock.patch.object(bridge, "_VENV_PY_POSIX", Path("/nonexistent/python")):
                    assert bridge.enabled() is False

    def test_predict_batch_empty_input(self):
        """空输入 → 空列表。"""
        import nn_surprisal_bridge as bridge
        assert bridge.predict_batch([]) == []

    def test_predict_one_delegates_to_batch(self):
        """predict_one 封装 predict_batch。"""
        import nn_surprisal_bridge as bridge
        env = {k: v for k, v in os.environ.items() if k != "RUOYU_NN_SURPRISAL"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = bridge.predict_one("测试")
            assert result is None

    def test_ids_length_mismatch(self):
        """ids 长度与 texts 不匹配 → 全 None。"""
        import nn_surprisal_bridge as bridge
        with mock.patch.dict(os.environ, {"RUOYU_NN_SURPRISAL": "1"}):
            with mock.patch.object(bridge, "_venv_python", return_value=Path("/fake/python")):
                with mock.patch.object(bridge, "_SURPRISAL_INFER",
                                       Path(__file__)):  # 存在的文件
                    result = bridge.predict_batch(
                        ["a", "b"], ids=["only_one"])
                    assert result == [None, None]


# ============ 3. scanner 检测逻辑测试（mock bridge） ============

def _mock_para_stats(means: list[float]) -> list[dict | None]:
    """构造 mock bridge 返回值。"""
    return [
        {"id": f"para_{i:04d}", "mean_surprisal": m,
         "std_surprisal": 1.0, "max_surprisal": m + 2,
         "min_surprisal": m - 1, "skewness": 0.5,
         "kurtosis": 1.0, "token_count": 30, "source": "model"}
        for i, m in enumerate(means)
    ]


class TestScannerDetection:
    """测试 scanner 的 4 个检测规则（mock bridge）。"""

    def test_flat_detection(self):
        """段间 std 过低 → SURPRISAL_TOO_FLAT。"""
        from surprisal_scanner import _detect_flat
        # 全部 mean_surprisal 非常接近 → std 很低
        para_stats = [{"mean_surprisal": 5.0 + i * 0.01} for i in range(10)]
        issues = _detect_flat(para_stats, threshold=0.8)
        assert len(issues) == 1
        assert issues[0]["code"] == "SURPRISAL_TOO_FLAT"
        assert issues[0]["gate_level"] == "advisory"

    def test_flat_no_issue(self):
        """段间 std 正常 → 无 issue。"""
        from surprisal_scanner import _detect_flat
        para_stats = [{"mean_surprisal": m} for m in [3.0, 7.0, 4.0, 8.0, 2.0]]
        issues = _detect_flat(para_stats, threshold=0.8)
        assert len(issues) == 0

    def test_cliff_detection(self):
        """相邻段 delta 过大 → SURPRISAL_CLIFF。"""
        from surprisal_scanner import _detect_cliff
        para_stats = [
            {"mean_surprisal": 5.0},
            {"mean_surprisal": 5.2},
            {"mean_surprisal": 10.0},  # delta = 4.8 > 4.0
            {"mean_surprisal": 5.5},   # delta = 4.5 > 4.0
        ]
        issues = _detect_cliff(para_stats, threshold=4.0)
        assert len(issues) == 2
        assert all(i["code"] == "SURPRISAL_CLIFF" for i in issues)
        assert all(i["gate_level"] == "advisory" for i in issues)

    def test_cliff_no_issue(self):
        """相邻段 delta 正常 → 无 issue。"""
        from surprisal_scanner import _detect_cliff
        para_stats = [{"mean_surprisal": m} for m in [5.0, 5.5, 6.0, 5.8]]
        issues = _detect_cliff(para_stats, threshold=4.0)
        assert len(issues) == 0

    def test_monotone_increasing(self):
        """连续 5 段单调递增 → SURPRISAL_MONOTONE。"""
        from surprisal_scanner import _detect_monotone
        para_stats = [{"mean_surprisal": float(i)} for i in range(6)]
        issues = _detect_monotone(para_stats, run_length=5)
        assert len(issues) >= 1
        assert issues[0]["code"] == "SURPRISAL_MONOTONE"
        assert issues[0]["details"]["direction"] == "increasing"

    def test_monotone_decreasing(self):
        """连续 5 段单调递减 → SURPRISAL_MONOTONE。"""
        from surprisal_scanner import _detect_monotone
        para_stats = [{"mean_surprisal": 10.0 - i} for i in range(6)]
        issues = _detect_monotone(para_stats, run_length=5)
        assert len(issues) >= 1
        assert issues[0]["code"] == "SURPRISAL_MONOTONE"
        assert issues[0]["details"]["direction"] == "decreasing"

    def test_monotone_no_issue(self):
        """波动正常 → 无 MONOTONE。"""
        from surprisal_scanner import _detect_monotone
        para_stats = [{"mean_surprisal": m} for m in [5, 3, 7, 4, 6, 2, 8]]
        issues = _detect_monotone(para_stats, run_length=5)
        assert len(issues) == 0

    def test_climax_imbalance(self):
        """高潮区 surprisal 反低 → INFO_DENSITY_IMBALANCE。"""
        from surprisal_scanner import _detect_climax_imbalance
        # 前 75% 高·后 25% 低 → 高潮区太可预测
        means = [8.0] * 9 + [2.0] * 3  # 12 段·后 3 段（25%）全低于中位数 8.0
        para_stats = [{"mean_surprisal": m} for m in means]
        issues = _detect_climax_imbalance(para_stats, ratio_threshold=0.7)
        assert len(issues) == 1
        assert issues[0]["code"] == "INFO_DENSITY_IMBALANCE"

    def test_climax_no_issue(self):
        """高潮区 surprisal 正常 → 无 issue。"""
        from surprisal_scanner import _detect_climax_imbalance
        # 后 25% surprisal 高于中位数
        means = [3.0] * 9 + [8.0] * 3
        para_stats = [{"mean_surprisal": m} for m in means]
        issues = _detect_climax_imbalance(para_stats, ratio_threshold=0.7)
        assert len(issues) == 0

    def test_too_few_paragraphs(self):
        """段落太少 → 无 issue。"""
        from surprisal_scanner import _detect_climax_imbalance
        para_stats = [{"mean_surprisal": 5.0}] * 5
        issues = _detect_climax_imbalance(para_stats, ratio_threshold=0.7)
        assert len(issues) == 0


# ============ 4. scanner 集成测试（mock bridge） ============

class TestScannerIntegration:
    """测试 scan_cluster_surprisal 完整流程（mock bridge）。"""

    def test_bridge_all_none_silent_degrade(self):
        """bridge 全部返回 None → 静默降级·返回空列表。"""
        from surprisal_scanner import scan_cluster_surprisal
        draft = "\n\n".join([f"第{i}段正文内容" for i in range(10)])
        with mock.patch("nn_surprisal_bridge.predict_batch",
                         return_value=[None] * 10):
            issues = scan_cluster_surprisal(draft)
            assert issues == []

    def test_bridge_partial_none(self):
        """bridge 部分返回 None → 跳过 None 条目·不崩。"""
        from surprisal_scanner import scan_cluster_surprisal
        draft = "\n\n".join([f"第{i}段正文内容" for i in range(10)])
        results = _mock_para_stats([5.0, 5.1, 5.05, 5.08, 5.02, 5.0, 5.1, 5.0, 5.05, 5.02])
        results[3] = None  # 第 4 段失败
        results[7] = None  # 第 8 段失败
        with mock.patch("nn_surprisal_bridge.predict_batch",
                         return_value=results):
            issues = scan_cluster_surprisal(draft)
            # 不崩即通过·issue 取决于数据
            assert isinstance(issues, list)
            assert all(i["gate_level"] == "advisory" for i in issues)

    def test_flat_cluster_detected(self):
        """全 cluster 方差过低 → 检出 SURPRISAL_TOO_FLAT。"""
        from surprisal_scanner import scan_cluster_surprisal
        draft = "\n\n".join([f"第{i}段正文内容填充字数" for i in range(10)])
        # 全部 mean_surprisal 极接近
        results = _mock_para_stats([5.0 + i * 0.01 for i in range(10)])
        with mock.patch("nn_surprisal_bridge.predict_batch",
                         return_value=results):
            issues = scan_cluster_surprisal(draft)
            flat_issues = [i for i in issues if i["code"] == "SURPRISAL_TOO_FLAT"]
            assert len(flat_issues) >= 1

    def test_draft_too_short(self):
        """草稿不足 3 段 → 返回空。"""
        from surprisal_scanner import scan_cluster_surprisal
        draft = "只有一段"
        issues = scan_cluster_surprisal(draft)
        assert issues == []

    def test_changes_stripped(self):
        """CHANGES 区块被正确去除。"""
        from surprisal_scanner import _strip_changes
        text = "正文内容\n\n---CHANGES_FACTUAL---\n一些变更记录"
        assert _strip_changes(text) == "正文内容"

    def test_all_advisory_gate_level(self):
        """所有 issue 的 gate_level 都是 advisory。"""
        from surprisal_scanner import scan_cluster_surprisal
        draft = "\n\n".join([f"第{i}段内容" for i in range(12)])
        # 构造同时触发 flat + climax 的数据
        means = [5.0] * 9 + [2.0] * 3
        results = _mock_para_stats(means)
        with mock.patch("nn_surprisal_bridge.predict_batch",
                         return_value=results):
            issues = scan_cluster_surprisal(draft)
            for issue in issues:
                assert issue["gate_level"] == "advisory", \
                    f"issue {issue['code']} gate_level 应为 advisory"


# ============ 5. 段落切分测试 ============

class TestParagraphSplit:

    def test_split_basic(self):
        from surprisal_scanner import _split_paragraphs
        text = "第一段\n\n第二段\n\n第三段"
        assert _split_paragraphs(text) == ["第一段", "第二段", "第三段"]

    def test_split_empty_lines(self):
        from surprisal_scanner import _split_paragraphs
        text = "第一段\n\n\n\n第二段"
        result = _split_paragraphs(text)
        assert len(result) == 2

    def test_split_no_empty(self):
        from surprisal_scanner import _split_paragraphs
        text = "连续文本没有空行分隔"
        result = _split_paragraphs(text)
        assert len(result) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
