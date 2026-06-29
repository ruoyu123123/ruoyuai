#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN信息密度集成
"""surprisal_scanner.py — 信息密度 / surprisal 检测器（advisory · cluster 视野）。

【为什么有这个】
论文实证 (Meister et al. 2021, Giulianelli et al. 2023)：
  · AI 生成文本 surprisal 方差显著偏低（"太顺滑"·信息密度平坦 = AI 腔标志）
  · 相邻段落 surprisal 断崖 = 信息密度突变（拼接感 / 风格漂移）
  · 高潮段 surprisal 反而低 = 该紧张的地方太可预测（张力不足）
  · 连续 N 段单调递减/递增 = 节奏单调（缺乏张弛有度）

本 scanner 基于 GPT-2 预训练模型（经 nn_surprisal_bridge subprocess 桥调用）计算段落级 surprisal，
检测 4 类 advisory issue。bridge 不可用时静默降级（返回空 issue 列表）。

【北极星⑤ 顾问非法官】
  信息密度是创作工艺指标，writer 有理由偏离（如刻意重复/刻意信息轰炸）→ 永远 advisory。
  **绝不进 audit_hub.HARD_GATE_CODES**。

用法：
  python surprisal_scanner.py <draft_path> [--project <project_dir>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ============ Issue codes（全部 advisory·绝不进 HARD_GATE_CODES） ============

SURPRISAL_TOO_FLAT = "SURPRISAL_TOO_FLAT"
SURPRISAL_CLIFF = "SURPRISAL_CLIFF"
SURPRISAL_MONOTONE = "SURPRISAL_MONOTONE"
INFO_DENSITY_IMBALANCE = "INFO_DENSITY_IMBALANCE"

# ============ 阈值（可被项目 overrides 覆盖） ============

# 全 cluster 段间 surprisal 均值的 std < 此值 → 信息密度无波动（AI 腔嫌疑）
FLAT_STD_FLOOR = 0.8

# 相邻段 mean_surprisal 差值 > 此值 → 信息密度断崖
CLIFF_DELTA_CEIL = 4.0

# 连续 N 段 surprisal 单调递减/递增 → 节奏单调
MONOTONE_RUN_LENGTH = 5

# 高潮段 surprisal 低于全篇中位数的比例 → 该紧张的地方太可预测
# （高潮段定义：位于 cluster 后 25% 的段落）
CLIMAX_BELOW_MEDIAN_RATIO = 0.7

# ============ 段落切分 ============

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _strip_changes(text: str) -> str:
    """去掉草稿末尾的 CHANGES 区块（只保留正文）。"""
    for sep in _CHANGES_SEPARATORS:
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def _split_paragraphs(text: str) -> list[str]:
    """按空行切段·去空段·去纯空白段。"""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _load_overrides(project_dir: str | None) -> dict:
    """从项目 _数据库/style_scanner_overrides.json 加载阈值覆盖。"""
    if not project_dir:
        return {}
    p = Path(project_dir) / "_数据库" / "style_scanner_overrides.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("surprisal_scanner", {}) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ============ 核心检测 ============

def _make_issue(code: str, message: str, severity: str = "warning",
                details: dict | None = None) -> dict:
    """构建标准 issue dict（与其他 scanner 一致）。"""
    issue = {
        "code": code,
        "message": message,
        "severity": severity,
        "gate_level": "advisory",
    }
    if details:
        issue["details"] = details
    return issue


def _detect_flat(para_stats: list[dict], threshold: float) -> list[dict]:
    """检测全 cluster 段间 surprisal 方差过低（AI 腔嫌疑）。"""
    means = [p["mean_surprisal"] for p in para_stats
             if p.get("mean_surprisal") is not None]
    if len(means) < 3:
        return []
    overall_mean = sum(means) / len(means)
    variance = sum((m - overall_mean) ** 2 for m in means) / (len(means) - 1)
    inter_std = variance ** 0.5

    if inter_std < threshold:
        return [_make_issue(
            SURPRISAL_TOO_FLAT,
            f"段间 surprisal 方差过低 (std={inter_std:.3f} < {threshold})·"
            f"信息密度几乎无波动·可能 AI 腔（自然文本张弛有度）",
            severity="warning",
            details={"inter_paragraph_std": round(inter_std, 4),
                     "threshold": threshold,
                     "paragraph_count": len(means),
                     "mean_surprisals": [round(m, 3) for m in means]},
        )]
    return []


def _detect_cliff(para_stats: list[dict], threshold: float) -> list[dict]:
    """检测相邻段 surprisal 断崖。"""
    issues = []
    means = [(i, p["mean_surprisal"]) for i, p in enumerate(para_stats)
             if p.get("mean_surprisal") is not None]
    for j in range(1, len(means)):
        idx_prev, val_prev = means[j - 1]
        idx_curr, val_curr = means[j]
        delta = abs(val_curr - val_prev)
        if delta > threshold:
            direction = "骤升" if val_curr > val_prev else "骤降"
            issues.append(_make_issue(
                SURPRISAL_CLIFF,
                f"段 {idx_prev + 1}→{idx_curr + 1} surprisal {direction} "
                f"(Δ={delta:.2f} > {threshold})·信息密度断崖（拼接感/风格漂移）",
                severity="warning",
                details={"paragraph_from": idx_prev + 1,
                         "paragraph_to": idx_curr + 1,
                         "delta": round(delta, 4),
                         "from_mean": round(val_prev, 4),
                         "to_mean": round(val_curr, 4),
                         "threshold": threshold},
            ))
    return issues


def _detect_monotone(para_stats: list[dict], run_length: int) -> list[dict]:
    """检测连续 N 段 surprisal 单调递减/递增。"""
    means = [p["mean_surprisal"] for p in para_stats
             if p.get("mean_surprisal") is not None]
    if len(means) < run_length:
        return []

    issues = []
    # 检测单调递增
    inc_run = 1
    for i in range(1, len(means)):
        if means[i] > means[i - 1]:
            inc_run += 1
            if inc_run >= run_length:
                start = i - run_length + 1
                issues.append(_make_issue(
                    SURPRISAL_MONOTONE,
                    f"段 {start + 1}~{i + 1} surprisal 连续 {run_length} 段单调递增·"
                    f"节奏缺乏张弛（建议穿插节奏变化）",
                    severity="info",
                    details={"start_paragraph": start + 1,
                             "end_paragraph": i + 1,
                             "direction": "increasing",
                             "run_length": run_length,
                             "values": [round(means[k], 3) for k in range(start, i + 1)]},
                ))
                inc_run = 1  # 重置·避免同段重复报
        else:
            inc_run = 1

    # 检测单调递减
    dec_run = 1
    for i in range(1, len(means)):
        if means[i] < means[i - 1]:
            dec_run += 1
            if dec_run >= run_length:
                start = i - run_length + 1
                issues.append(_make_issue(
                    SURPRISAL_MONOTONE,
                    f"段 {start + 1}~{i + 1} surprisal 连续 {run_length} 段单调递减·"
                    f"节奏缺乏张弛（建议穿插节奏变化）",
                    severity="info",
                    details={"start_paragraph": start + 1,
                             "end_paragraph": i + 1,
                             "direction": "decreasing",
                             "run_length": run_length,
                             "values": [round(means[k], 3) for k in range(start, i + 1)]},
                ))
                dec_run = 1
        else:
            dec_run = 1

    return issues


def _detect_climax_imbalance(para_stats: list[dict],
                             ratio_threshold: float) -> list[dict]:
    """检测高潮段 surprisal 反而低（该紧张的地方太可预测）。"""
    means = [p["mean_surprisal"] for p in para_stats
             if p.get("mean_surprisal") is not None]
    if len(means) < 8:
        return []  # 段落太少·无法可靠划分高潮区

    # 高潮区 = 后 25% 段落
    climax_start = int(len(means) * 0.75)
    climax_means = means[climax_start:]
    all_means_sorted = sorted(means)
    median = all_means_sorted[len(all_means_sorted) // 2]

    below_count = sum(1 for m in climax_means if m < median)
    ratio = below_count / len(climax_means) if climax_means else 0

    if ratio > ratio_threshold:
        return [_make_issue(
            INFO_DENSITY_IMBALANCE,
            f"高潮区(后25%) {below_count}/{len(climax_means)} 段 surprisal 低于全篇中位数·"
            f"该紧张的地方太可预测（信息密度应随张力升高）",
            severity="info",
            details={"climax_below_median_ratio": round(ratio, 4),
                     "threshold": ratio_threshold,
                     "median_surprisal": round(median, 4),
                     "climax_means": [round(m, 3) for m in climax_means]},
        )]
    return []


# ============ 公开 API ============

def scan_cluster_surprisal(draft_text: str,
                           project_dir: str | None = None) -> list[dict]:
    """扫描 cluster 草稿的信息密度。

    返回 issue 列表（全部 advisory）。bridge 不可用 → 返回空列表（静默降级）。
    """
    # 导入 bridge（延迟·避免循环 / 早期 import 报错）
    try:
        import nn_surprisal_bridge as bridge
    except ImportError:
        try:
            # fallback: 相对路径
            from pathlib import Path as _P
            sys.path.insert(0, str(_P(__file__).resolve().parent))
            import nn_surprisal_bridge as bridge
        except ImportError:
            return []

    # 去 CHANGES 区块 + 切段
    clean_text = _strip_changes(draft_text)
    paragraphs = _split_paragraphs(clean_text)
    if len(paragraphs) < 3:
        return []  # 太短·无统计意义

    # 调 bridge 批量推理
    ids = [f"para_{i:04d}" for i in range(len(paragraphs))]
    results = bridge.predict_batch(paragraphs, ids=ids)

    # bridge 全部返回 None → 静默降级
    if all(r is None for r in results):
        return []

    # 过滤有效结果（保序·None 条目跳过检测但保留位置信息）
    para_stats: list[dict] = []
    for i, r in enumerate(results):
        if r is not None and r.get("mean_surprisal") is not None:
            para_stats.append({**r, "_para_index": i})

    if len(para_stats) < 3:
        return []

    # 加载项目 overrides
    overrides = _load_overrides(project_dir)
    flat_threshold = overrides.get("flat_std_floor", FLAT_STD_FLOOR)
    cliff_threshold = overrides.get("cliff_delta_ceil", CLIFF_DELTA_CEIL)
    monotone_run = overrides.get("monotone_run_length", MONOTONE_RUN_LENGTH)
    climax_ratio = overrides.get("climax_below_median_ratio", CLIMAX_BELOW_MEDIAN_RATIO)

    # 运行 4 个检测
    issues: list[dict] = []
    issues.extend(_detect_flat(para_stats, flat_threshold))
    issues.extend(_detect_cliff(para_stats, cliff_threshold))
    issues.extend(_detect_monotone(para_stats, monotone_run))
    issues.extend(_detect_climax_imbalance(para_stats, climax_ratio))

    return issues


# ============ CLI ============

def main():
    ap = argparse.ArgumentParser(description="信息密度 surprisal 扫描器（advisory · cluster 视野）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="项目目录（读 overrides）")
    ap.add_argument("--json", action="store_true", default=True, help="JSON 输出（默认）")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    draft_path = Path(args.draft_path)
    if not draft_path.exists():
        print(json.dumps({"error": f"草稿不存在: {draft_path}"}, ensure_ascii=False))
        return 1

    draft_text = draft_path.read_text(encoding="utf-8")
    issues = scan_cluster_surprisal(draft_text, project_dir=args.project)

    report = {
        "scanner": "surprisal_scanner",
        "draft_path": str(draft_path),
        "issue_count": len(issues),
        "issues": issues,
        "gate_level": "advisory",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
