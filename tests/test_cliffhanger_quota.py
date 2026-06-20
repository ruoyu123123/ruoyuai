# -*- coding: utf-8 -*-
"""R7 W2 Batch-E·CLIFFHANGER_QUOTA_OVER advisory 单测·确定性·零依赖。

覆盖 cross_cluster_engagement_metrics_aggregate.scan_cliffhanger_quota：
  · 空输入 / 全 hook / 占比超阈 / 占比临界 / 连续 streak / 边界 n=3 / 中英大小写归一
  · 辅助 _is_cliffhanger / collect_ending_types
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_engagement_metrics_aggregate as mod  # noqa: E402


# ── _is_cliffhanger ────────────────────────────────────────────────────
def test_is_cliffhanger_positive():
    for t in ("cliffhanger", "Cliffhanger", "悬念", "悬念性", "钩子型", "悬念型"):
        assert mod._is_cliffhanger(t), t


def test_is_cliffhanger_negative():
    for t in ("hook", "scene_end", "收束", "余韵", "钩子", "", None, 123):
        assert not mod._is_cliffhanger(t), t


# ── scan_cliffhanger_quota ─────────────────────────────────────────────
def test_quota_empty_no_findings():
    assert mod.scan_cliffhanger_quota([]) == []


def test_quota_all_hook_no_findings():
    """全 hook 收尾 → 0 cliffhanger → 无 finding。"""
    end_types = [(i, "hook") for i in range(1, 8)]
    assert mod.scan_cliffhanger_quota(end_types) == []


def test_quota_ratio_over_threshold():
    """>25% 章末 cliffhanger → CLIFFHANGER_QUOTA_OVER（metric=cliffhanger_ratio）。"""
    end_types = [(1, "hook"), (2, "cliffhanger"), (3, "hook"), (4, "cliffhanger"),
                 (5, "hook"), (6, "cliffhanger"), (7, "hook"), (8, "scene_end")]
    findings = mod.scan_cliffhanger_quota(end_types)
    codes = [(f["code"], f.get("metric")) for f in findings]
    assert ("CLIFFHANGER_QUOTA_OVER", "cliffhanger_ratio") in codes
    ratio_finding = next(f for f in findings
                         if f.get("metric") == "cliffhanger_ratio")
    assert ratio_finding["severity"] == "advisory"
    assert ratio_finding["ratio"] > 0.25
    assert set(ratio_finding["cliffhanger_chs"]) == {2, 4, 6}


def test_quota_ratio_below_threshold_no_finding():
    """1/8 cliffhanger = 12.5% < 25% → 无 ratio finding。"""
    end_types = [(i, "hook") for i in range(1, 8)] + [(8, "cliffhanger")]
    findings = mod.scan_cliffhanger_quota(end_types)
    metrics = [f.get("metric") for f in findings]
    assert "cliffhanger_ratio" not in metrics


def test_quota_streak_three_fires():
    """连续 3 章 cliffhanger → CLIFFHANGER_QUOTA_OVER (streak)。"""
    end_types = [(1, "hook"), (2, "cliffhanger"), (3, "cliffhanger"),
                 (4, "cliffhanger"), (5, "hook")]
    findings = mod.scan_cliffhanger_quota(end_types)
    streak_finding = next((f for f in findings
                           if f.get("metric") == "consecutive_cliffhanger_streak"),
                          None)
    assert streak_finding is not None
    assert streak_finding["streak"] >= 3
    assert streak_finding["chapter_range"] == [2, 4]


def test_quota_streak_two_no_finding():
    """连续 2 章 → 不触发 streak（threshold=3）。"""
    end_types = [(1, "hook"), (2, "cliffhanger"), (3, "cliffhanger"),
                 (4, "hook"), (5, "cliffhanger")]
    findings = mod.scan_cliffhanger_quota(end_types)
    streak = [f for f in findings
              if f.get("metric") == "consecutive_cliffhanger_streak"]
    assert streak == []


def test_quota_small_n_no_ratio_finding():
    """n=3·即使 100% 也不报 ratio（避免单 cluster 极小样本误报）。"""
    end_types = [(1, "cliffhanger"), (2, "cliffhanger"), (3, "cliffhanger")]
    findings = mod.scan_cliffhanger_quota(end_types)
    metrics = [f.get("metric") for f in findings]
    # streak 会触发，但 ratio 不应该（n<4）
    assert "cliffhanger_ratio" not in metrics


def test_quota_chinese_aliases():
    """中文别名「悬念」/「悬念型」识别。"""
    end_types = [(1, "悬念"), (2, "悬念型"), (3, "钩子型"), (4, "hook"),
                 (5, "hook"), (6, "hook"), (7, "hook"), (8, "hook")]
    findings = mod.scan_cliffhanger_quota(end_types)
    streak_finding = next((f for f in findings
                           if f.get("metric") == "consecutive_cliffhanger_streak"),
                          None)
    assert streak_finding is not None and streak_finding["streak"] >= 3


# ── collect_ending_types ───────────────────────────────────────────────
def test_collect_ending_types_filters_invalid():
    recs = [
        (1, {"ending_type": "hook"}),
        (2, {"ending_type": ""}),
        (3, {"ending_type": "cliffhanger"}),
        (4, {}),
        (5, {"ending_type": 123}),
        (6, {"ending_type": "scene_end"}),
    ]
    out = mod.collect_ending_types(recs)
    assert out == [(1, "hook"), (3, "cliffhanger"), (6, "scene_end")]
