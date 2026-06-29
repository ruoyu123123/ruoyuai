#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""coherence_scanner.py — 段落连贯性检测器（advisory · cluster 视野）。

【为什么有这个】
连贯性（coherence）= 相邻文本块在逻辑/语义上是否自然衔接。业界做法（next-sentence-prediction /
sentence-ordering / entity-grid 连贯性建模）：用一个判别模型给「para_b 是否自然承接 para_a」打分
（[0,1]·越高越连贯）。本 scanner 经 nn_coherence_bridge subprocess 桥批量调该模型，对 cluster
草稿的相邻段落对打分，检测 3 类 advisory issue：
  · COHERENCE_BREAK       相邻段连贯性 < 0.3 → 段落间逻辑断裂（跳脱/拼接感）
  · COHERENCE_LOW_OVERALL 全篇平均连贯性 < 0.5 → 整体散乱（叙事链条松垮）
  · COHERENCE_UNSTABLE    连贯性方差 > 0.04（std > 0.2）→ 质量忽高忽低（衔接不稳）

bridge 不可用时（env 门控未开 / 无 checkpoint / 推理失败）静默降级，返回空 issue 列表——
若渝必须「无 NN 也能跑」（北极星⑤·零回归）。

【北极星⑤ 顾问非法官】
  连贯性是叙事工艺指标，writer 有理由偏离（如刻意的场景跳切 / 倒叙 / 蒙太奇）→ 永远 advisory。
  **绝不进 audit_hub.HARD_GATE_CODES**。

【bridge 契约（nn_coherence_bridge）】
  enabled() -> bool
  predict_pairs(pairs: list[tuple[str, str]], timeout: float | None = None) -> list[dict | None]
    · 入参 pairs = [(para_a, para_b), ...]，输出与之一一对应保序
    · 命中模型 = {"coherence_score": float ∈ [0,1], "source": "model"}（越高越连贯）
    · 不可用 / 非模型 / 失败 = None（逐条）

用法：
  python coherence_scanner.py <draft_path> [--project <project_dir>] [--windows] [--window-size 6]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ============ Issue codes（全部 advisory·绝不进 HARD_GATE_CODES） ============

COHERENCE_BREAK = "COHERENCE_BREAK"
COHERENCE_LOW_OVERALL = "COHERENCE_LOW_OVERALL"
COHERENCE_UNSTABLE = "COHERENCE_UNSTABLE"

# ============ 阈值（可被项目 overrides 覆盖） ============

# 相邻段（或窗口）连贯性 < 此值 → 逻辑断裂
COHERENCE_BREAK_FLOOR = 0.3

# 全篇平均连贯性 < 此值 → 整体散乱
COHERENCE_LOW_OVERALL_FLOOR = 0.5

# 连贯性方差 > 此值（≈ std 0.2）→ 衔接质量忽高忽低
COHERENCE_VARIANCE_CEIL = 0.04

# 聚合类规则（LOW_OVERALL / UNSTABLE）至少需要这么多个有效评分点才有统计意义
_MIN_POINTS_FOR_AGGREGATE = 3

# details 里段落预览的截断长度
_SNIPPET_LEN = 30

_OVERRIDES_KEY = "coherence_scanner"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


# ============ 文本预处理 ============

def _strip_changes(text: str) -> str:
    """去掉草稿末尾的 CHANGES 区块（只保留正文）。"""
    for sep in _CHANGES_SEPARATORS:
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def _split_paragraphs(text: str) -> list[str]:
    """按 \\n 切段·strip·过滤空行（cluster 草稿一行一段·与 prose_rhythm_scanner 同口径）。"""
    return [line.strip() for line in text.split("\n") if line.strip()]


def _snippet(para: str, n: int = _SNIPPET_LEN) -> str:
    s = para.strip().replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ============ 项目阈值覆盖 ============

def _load_overrides(project_dir: str | None) -> dict:
    """从项目 _数据库/style_scanner_overrides.json 读 coherence_scanner 阈值覆盖。"""
    if not project_dir:
        return {}
    p = Path(project_dir) / "_数据库" / "style_scanner_overrides.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get(_OVERRIDES_KEY, {}) if isinstance(data, dict) else {}


def _resolve_thresholds(project_dir: str | None) -> dict:
    ov = _load_overrides(project_dir)
    return {
        "break_floor": ov.get("break_floor", COHERENCE_BREAK_FLOOR),
        "low_overall_floor": ov.get("low_overall_floor", COHERENCE_LOW_OVERALL_FLOOR),
        "variance_ceil": ov.get("variance_ceil", COHERENCE_VARIANCE_CEIL),
    }


# ============ issue 构建 ============

def _make_issue(code: str, message: str, severity: str = "warning",
                details: dict | None = None) -> dict:
    """构建标准 issue dict（与其他 scanner 一致·gate_level 恒 advisory）。"""
    return {
        "code": code,
        "gate_level": "advisory",
        "message": message,
        "severity": severity,
        "details": details or {},
    }


# ============ bridge 接入 ============

def _load_bridge():
    """延迟导入 nn_coherence_bridge。失败 → None（调用方静默降级）。

    返回 (predict_pairs, enabled) 或 None。
    """
    try:
        from nn_coherence_bridge import predict_pairs, enabled
    except ImportError:
        return None
    return predict_pairs, enabled


def _extract_coherence(r: dict | None) -> float | None:
    """从 bridge 单条结果取 coherence_score。非模型/缺字段 → None。"""
    if not isinstance(r, dict):
        return None
    c = r.get("coherence_score")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return float(c)
    return None


def _adjacent_pair_coherences(paragraphs: list[str], predict_pairs) -> list[float | None]:
    """对相邻段落对 (p_i, p_{i+1}) 批量打分。返回长度 len-1 的 coherence 列表（None=该对不可用）。"""
    n = len(paragraphs)
    if n < 2:
        return []
    pairs = [(paragraphs[i], paragraphs[i + 1]) for i in range(n - 1)]
    try:
        results = predict_pairs(pairs)
    except Exception as e:  # noqa: BLE001 bridge 任何意外 → 全降级（绝不崩主流水线）
        print(f"[coherence_scanner] predict_pairs 异常·降级：{type(e).__name__}: {str(e)[:120]}",
              file=sys.stderr)
        return [None] * len(pairs)
    if not isinstance(results, list) or len(results) != len(pairs):
        print(f"[coherence_scanner] predict_pairs 输出失配·降级", file=sys.stderr)
        return [None] * len(pairs)
    return [_extract_coherence(r) for r in results]


# ============ 检测：相邻段落对 ============

def _detect_break_pairs(paragraphs: list[str], coherences: list[float | None],
                        floor: float) -> list[dict]:
    """相邻段连贯性 < floor → 逐对报 COHERENCE_BREAK。"""
    issues = []
    for i, c in enumerate(coherences):
        if c is None or c >= floor:
            continue
        issues.append(_make_issue(
            COHERENCE_BREAK,
            f"段 {i + 1}→{i + 2} 连贯性 {c:.3f} < {floor}·段落间逻辑断裂"
            f"（跳脱/拼接感·检查是否缺过渡或场景硬切）",
            severity="warning",
            details={
                "paragraph_from": i + 1,
                "paragraph_to": i + 2,
                "coherence": round(c, 4),
                "threshold": floor,
                "from_head": _snippet(paragraphs[i]),
                "to_head": _snippet(paragraphs[i + 1]),
            },
        ))
    return issues


def _detect_low_overall(coherences: list[float | None], floor: float) -> list[dict]:
    """全篇平均连贯性 < floor → 报 COHERENCE_LOW_OVERALL。"""
    valid = [c for c in coherences if c is not None]
    if len(valid) < _MIN_POINTS_FOR_AGGREGATE:
        return []
    avg = statistics.mean(valid)
    if avg >= floor:
        return []
    return [_make_issue(
        COHERENCE_LOW_OVERALL,
        f"全篇平均连贯性 {avg:.3f} < {floor}（{len(valid)} 个相邻段对）·"
        f"整体散乱·叙事链条松垮（段落间因果/时间/话题衔接弱）",
        severity="warning",
        details={
            "avg_coherence": round(avg, 4),
            "threshold": floor,
            "pair_count": len(valid),
            "min_coherence": round(min(valid), 4),
            "max_coherence": round(max(valid), 4),
        },
    )]


def _detect_unstable(coherences: list[float | None], variance_ceil: float) -> list[dict]:
    """连贯性方差 > variance_ceil → 报 COHERENCE_UNSTABLE。"""
    valid = [c for c in coherences if c is not None]
    if len(valid) < _MIN_POINTS_FOR_AGGREGATE:
        return []
    var = statistics.pvariance(valid)
    if var <= variance_ceil:
        return []
    std = var ** 0.5
    return [_make_issue(
        COHERENCE_UNSTABLE,
        f"连贯性方差 {var:.4f} > {variance_ceil}（std={std:.3f}）·"
        f"衔接质量忽高忽低（部分段落衔接自然·部分突兀）",
        severity="info",
        details={
            "variance": round(var, 5),
            "std": round(std, 4),
            "threshold": variance_ceil,
            "pair_count": len(valid),
            "avg_coherence": round(statistics.mean(valid), 4),
        },
    )]


# ============ 公开 API：相邻段落对 ============

def scan_coherence(draft_text: str, project_dir: str) -> list[dict]:
    """扫描 cluster 草稿相邻段落对的连贯性。

    返回 issue 列表（全部 advisory）。bridge 未启用 / 不可用 → 返回空列表（静默降级）。
    """
    bridge = _load_bridge()
    if bridge is None:
        return []
    predict_pairs, enabled = bridge
    if not enabled():
        return []

    paragraphs = _split_paragraphs(_strip_changes(draft_text))
    if len(paragraphs) < 2:
        return []  # 不足一对·无可检测

    coherences = _adjacent_pair_coherences(paragraphs, predict_pairs)
    if not coherences or all(c is None for c in coherences):
        return []  # bridge 全降级

    th = _resolve_thresholds(project_dir)
    issues: list[dict] = []
    issues.extend(_detect_break_pairs(paragraphs, coherences, th["break_floor"]))
    issues.extend(_detect_low_overall(coherences, th["low_overall_floor"]))
    issues.extend(_detect_unstable(coherences, th["variance_ceil"]))
    return issues


# ============ 公开 API：滑动窗口 ============

def _window_coherences(coherences: list[float | None],
                       n_paragraphs: int, window_size: int) -> list[tuple[int, float]]:
    """把相邻对 coherence 聚合成「窗口连贯性」：窗口 = 连续 window_size 段，
    其连贯性 = 窗口内部 (window_size-1) 个相邻对 coherence 的均值（跳过 None）。

    返回 [(window_start_paragraph_index, window_mean_coherence), ...]（0-based start）。
    """
    out: list[tuple[int, float]] = []
    # 窗口起点 s ∈ [0, n - window_size]；内部对索引 = [s, s+window_size-2]
    for s in range(0, n_paragraphs - window_size + 1):
        inner = [c for c in coherences[s:s + window_size - 1] if c is not None]
        if not inner:
            continue
        out.append((s, statistics.mean(inner)))
    return out


def scan_coherence_windows(draft_text: str, project_dir: str,
                           window_size: int = 6) -> list[dict]:
    """扫描 cluster 草稿的滑动窗口连贯性（5-8 段一窗·比逐对更平滑的整体视图）。

    窗口连贯性 = 窗口内相邻段对 coherence 的均值。检测同 3 类 issue（窗口粒度）。
    返回 issue 列表（全部 advisory）。bridge 未启用 / 不可用 → 空列表。
    """
    bridge = _load_bridge()
    if bridge is None:
        return []
    predict_pairs, enabled = bridge
    if not enabled():
        return []

    window_size = max(2, int(window_size))
    paragraphs = _split_paragraphs(_strip_changes(draft_text))
    n = len(paragraphs)
    if n < window_size:
        return []  # 不足一个完整窗口

    coherences = _adjacent_pair_coherences(paragraphs, predict_pairs)
    if not coherences or all(c is None for c in coherences):
        return []

    windows = _window_coherences(coherences, n, window_size)
    if not windows:
        return []

    th = _resolve_thresholds(project_dir)
    break_floor = th["break_floor"]
    low_floor = th["low_overall_floor"]
    var_ceil = th["variance_ceil"]
    window_means = [m for _, m in windows]

    issues: list[dict] = []

    # 规则1（窗口粒度）：低连贯窗口
    for s, m in windows:
        if m >= break_floor:
            continue
        p_from, p_to = s + 1, s + window_size
        issues.append(_make_issue(
            COHERENCE_BREAK,
            f"窗口 段{p_from}~{p_to}（{window_size}段）平均连贯性 {m:.3f} < {break_floor}·"
            f"该段落区间整体散乱（局部叙事跳脱）",
            severity="warning",
            details={
                "window_from_paragraph": p_from,
                "window_to_paragraph": p_to,
                "window_size": window_size,
                "window_coherence": round(m, 4),
                "threshold": break_floor,
                "head": _snippet(paragraphs[s]),
            },
        ))

    # 规则2：全篇窗口平均连贯性偏低
    if len(window_means) >= 1:
        avg = statistics.mean(window_means)
        if avg < low_floor:
            issues.append(_make_issue(
                COHERENCE_LOW_OVERALL,
                f"全篇窗口平均连贯性 {avg:.3f} < {low_floor}（{len(window_means)} 个 {window_size} 段窗口）·"
                f"整体散乱",
                severity="warning",
                details={
                    "avg_window_coherence": round(avg, 4),
                    "threshold": low_floor,
                    "window_count": len(window_means),
                    "window_size": window_size,
                    "min_window_coherence": round(min(window_means), 4),
                    "max_window_coherence": round(max(window_means), 4),
                },
            ))

    # 规则3：窗口连贯性方差过大
    if len(window_means) >= 2:
        var = statistics.pvariance(window_means)
        if var > var_ceil:
            std = var ** 0.5
            issues.append(_make_issue(
                COHERENCE_UNSTABLE,
                f"窗口连贯性方差 {var:.4f} > {var_ceil}（std={std:.3f}）·"
                f"不同段落区间衔接质量不均",
                severity="info",
                details={
                    "variance": round(var, 5),
                    "std": round(std, 4),
                    "threshold": var_ceil,
                    "window_count": len(window_means),
                    "window_size": window_size,
                    "avg_window_coherence": round(statistics.mean(window_means), 4),
                },
            ))

    return issues


# ============ CLI ============

def main() -> int:
    ap = argparse.ArgumentParser(
        description="段落连贯性扫描器（advisory · cluster 视野 · NN coherence bridge）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default="", help="项目目录（读 style_scanner_overrides.json）")
    ap.add_argument("--windows", action="store_true",
                    help="同时跑滑动窗口扫描（默认只跑相邻段对）")
    ap.add_argument("--window-size", type=int, default=6, help="窗口段数（默认 6·推荐 5-8）")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    draft_path = Path(args.draft_path)
    if not draft_path.exists():
        print(json.dumps({"error": f"草稿不存在: {draft_path}"}, ensure_ascii=False))
        return 2

    # 自测诊断：bridge 是否就绪（NN 默认 off → 0 issue 是正常的）
    bridge = _load_bridge()
    if bridge is None:
        print("[coherence_scanner] nn_coherence_bridge 不可导入·静默降级（0 issue）", file=sys.stderr)
        bridge_enabled = False
    else:
        try:
            bridge_enabled = bool(bridge[1]())
        except Exception as e:  # noqa: BLE001
            print(f"[coherence_scanner] enabled() 异常：{type(e).__name__}: {e}", file=sys.stderr)
            bridge_enabled = False
    print(f"[coherence_scanner] bridge_enabled={bridge_enabled}", file=sys.stderr)

    draft_text = draft_path.read_text(encoding="utf-8")
    pair_issues = scan_coherence(draft_text, args.project)
    window_issues = scan_coherence_windows(draft_text, args.project, args.window_size) \
        if args.windows else []

    report = {
        "scanner": "coherence_scanner",
        "draft_path": str(draft_path),
        "bridge_enabled": bridge_enabled,
        "gate_level": "advisory",
        "pair_issue_count": len(pair_issues),
        "pair_issues": pair_issues,
    }
    if args.windows:
        report["window_size"] = max(2, int(args.window_size))
        report["window_issue_count"] = len(window_issues)
        report["window_issues"] = window_issues

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if (pair_issues or window_issues) else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
