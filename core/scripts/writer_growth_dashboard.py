#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""writer_growth_dashboard.py — 反馈↔词汇多样性轨迹 dashboard (advisory · system layer)
R23 W11 Batch-II · P2 · distributed mentoring 范式

【缺口】系统跑了多个 cluster · 用户偏好/走向卡选择不断累积 · 但无任何 dashboard 追踪
「反馈事件密度 vs 词汇多样性轨迹」的因果。

【做法 · 纯确定性 · 零 LLM/零联网】
  · 遍历 _数据库/故事块摘要.json 已收尾 cluster
  · 每 cluster 算 TTR (Type-Token Ratio) / MTLD (粗略 0.72 阈值版) / hapax_legomena 占比
  · 平行追踪 _数据库/用户偏好.json (style/content/workflow 三大段 entries 总数) 与
    走向卡日志（_数据库/事件簇.json.clusters[].user_choice / pause answer artifacts）数
  · 输出 advisory dashboard：反馈事件 +1 → 词汇多样性 Δ；趋势单调下降 / 不响应 → 标记

【挂点】cluster-save-state step 9 cross-cluster · 系统级 dashboard · 不阻断写作

【北极星 ②④⑤】
  · advisory · 绝不 hard_gate · 永不进 audit_hub.HARD_GATE_CODES
  · 作者档第一权威（dashboard 不替模型决策·仅透明展示因果）

用法: python writer_growth_dashboard.py <project_root> [--out <json_path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def _load(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _cjk_tokens(text: str):
    """简化 token = CJK 字符。"""
    return [c for c in text if "一" <= c <= "鿿"]


def _ttr(tokens) -> float:
    if not tokens:
        return 0.0
    return round(len(set(tokens)) / len(tokens), 4)


def _mtld(tokens, threshold: float = 0.72) -> float:
    """粗略 MTLD：从左到右滑·TTR 跌破 threshold 时记一个 factor·算平均跨度。"""
    if not tokens:
        return 0.0
    n = len(tokens)
    factors = 0
    span = 0
    seen = set()
    count = 0
    for tok in tokens:
        seen.add(tok)
        count += 1
        span += 1
        cur_ttr = len(seen) / count if count else 0
        if cur_ttr < threshold:
            factors += 1
            seen = set()
            count = 0
    if factors == 0:
        return float(n)
    return round(n / factors, 2)


def _hapax_ratio(tokens) -> float:
    if not tokens:
        return 0.0
    freq = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    hap = sum(1 for v in freq.values() if v == 1)
    return round(hap / len(tokens), 4)


def _collect_cluster_drafts(project_root: Path):
    """收集 cluster 的草稿文本：优先读 章节/cluster_<key>_draft.txt"""
    drafts = {}
    chap_dir = project_root / "章节"
    if not chap_dir.exists():
        return drafts
    for f in chap_dir.glob("cluster_*_draft.txt"):
        m = re.match(r"cluster_(\w+)_draft\.txt", f.name)
        if not m:
            continue
        try:
            drafts[m.group(1)] = f.read_text(encoding="utf-8")
        except OSError:
            continue
    return drafts


def _feedback_event_count(project_root: Path) -> int:
    """统计用户偏好.json 中 entries 数 (style/content/workflow 三大段累积) + 简化
    走向卡选择数 (事件簇.json clusters[].user_choice 或 _user_decision 字段)。"""
    pref = _load(project_root / "_数据库" / "用户偏好.json", {}) or {}
    n_pref = 0
    for k in ("style_preferences", "content_preferences", "workflow_preferences"):
        v = pref.get(k)
        if isinstance(v, list):
            n_pref += len(v)
    events = _load(project_root / "_数据库" / "事件簇.json", {}) or {}
    n_choice = 0
    if isinstance(events, dict):
        clusters = events.get("clusters")
        if isinstance(clusters, list):
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                if c.get("user_choice") or c.get("_user_decision"):
                    n_choice += 1
    return n_pref + n_choice


def build_dashboard(project_root) -> dict:
    project_root = Path(project_root)
    drafts = _collect_cluster_drafts(project_root)
    feedback_total = _feedback_event_count(project_root)
    rows = []
    for cid in sorted(drafts):
        text = drafts[cid]
        tokens = _cjk_tokens(text)
        rows.append({
            "cluster_id": cid,
            "cjk": len(tokens),
            "ttr": _ttr(tokens),
            "mtld": _mtld(tokens),
            "hapax_ratio": _hapax_ratio(tokens),
        })

    # advisory：趋势检测（≥3 cluster 时）
    advisories = []
    if len(rows) >= 3:
        ttr_series = [r["ttr"] for r in rows]
        diff = [ttr_series[i] - ttr_series[i - 1] for i in range(1, len(ttr_series))]
        monotone_drop = all(d <= 0 for d in diff)
        if monotone_drop:
            advisories.append({
                "code": "WRITER_GROWTH_VOCAB_DROP",
                "msg": (f"TTR 单调下降 {len(ttr_series)} cluster: "
                        f"{[round(x, 3) for x in ttr_series]}·词汇多样性持续走低"),
            })
        # 反馈密度 vs 多样性响应：feedback 累积但 TTR 末两 cluster 平均 < 首两平均 → 标
        if feedback_total >= 5 and len(rows) >= 4:
            head_mean = sum(ttr_series[:2]) / 2
            tail_mean = sum(ttr_series[-2:]) / 2
            if tail_mean < head_mean - 0.02:
                advisories.append({
                    "code": "WRITER_GROWTH_NO_RESPONSE_TO_FEEDBACK",
                    "msg": (f"反馈事件累计 {feedback_total} 但 TTR 末段均 {round(tail_mean, 3)} "
                            f"< 首段均 {round(head_mean, 3)}·成长未响应反馈"),
                })

    return {
        "schema_version": "1.0",
        "scanner": "writer_growth_dashboard",
        "gate_level": "advisory",
        "_doc": "反馈 vs 词汇多样性轨迹 dashboard·R23 W11 Batch-II·system layer·绝不 hard_gate",
        "project": str(project_root),
        "cluster_count": len(rows),
        "feedback_event_total": feedback_total,
        "rows": rows,
        "advisories": advisories,
    }


def main():
    ap = argparse.ArgumentParser(description="writer growth dashboard (R23 W11 Batch-II)")
    ap.add_argument("project_root")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    rep = build_dashboard(args.project_root)
    s = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(s, encoding="utf-8")
    else:
        print(s)
    sys.exit(0)


if __name__ == "__main__":
    main()
