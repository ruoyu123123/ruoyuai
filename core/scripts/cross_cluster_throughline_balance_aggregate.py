"""cross_cluster_throughline_balance_aggregate.py — 4 线均衡度扫（CCR3）

读 _changes.json.factual.throughline_progress 历史，检测：
- 某线连续 ≥ 4 章 no_progress（线沉睡告警）
- 某线占比 < 15%（线被边缘化）
- OS 单线占比 > 60%（其他线被忽略）
- 4 线整体覆盖率（每章至少推 2 条）

Dramatica 4 throughline：OS / MC / IC / RS

输出：_数据库/.cross_chapter_scan/throughline_balance_<ts>.json
退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

THROUGHLINES = ["OS", "MC", "IC", "RS"]


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)

    # 收集每章 throughline_progress
    per_chapter = []  # [(ch, {OS: bool, MC: bool, IC: bool, RS: bool})]

    # ===== 2026-05-29 cluster 化分支：账本有 throughline_progress → 摘要驱动 =====
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster」
    if csr.is_cluster_mode() and csr.ledger_has_field(project_root, "throughline_progress"):
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        for ch, rec in recs:
            tp = rec.get("throughline_progress", {}) or {}
            if not isinstance(tp, dict):
                tp = {}
            progress_map = {}
            for t in THROUGHLINES:
                v = tp.get(t, "no_progress")
                progress_map[t] = bool(v) and v != "no_progress" and v != ""
            per_chapter.append((ch, progress_map))
        if not per_chapter:
            print("[SKIP] cluster 账本无 throughline_progress 记录")
            sys.exit(0)
    else:
        # ===== 原逐章磁盘逻辑（非 cluster 模式 / 账本缺字段 → 零回归）=====
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        recent = chapters[-args.last_n:]
        for ch in recent:
            p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
            changes = load_json(p, {})
            tp = (changes.get("factual", {}) or {}).get("throughline_progress", {}) or {}
            progress_map = {}
            for t in THROUGHLINES:
                v = tp.get(t, "no_progress")
                progress_map[t] = bool(v) and v != "no_progress" and v != ""
            per_chapter.append((ch, progress_map))

    if not per_chapter:
        print("[SKIP] 无 throughline_progress 记录")
        sys.exit(0)

    findings = []

    # 1. 连续 no_progress 检测
    for t in THROUGHLINES:
        streak = 0
        streak_chs = []
        for ch, pmap in per_chapter:
            if not pmap[t]:
                streak += 1
                streak_chs.append(ch)
                if streak >= 4:
                    findings.append({
                        "severity": "warning",
                        "code": "THROUGHLINE_DORMANT",
                        "throughline": t,
                        "consecutive_chs": streak_chs[-4:],
                        "suggestion": f"throughline {t} 连续 ≥ 4 章 no_progress → 应至少推进 1 次（{t} = {{OS: 客观主线, MC: 主角内心, IC: 影响者, RS: 关系本身}}）",
                    })
                    streak = 0
                    streak_chs = []
            else:
                streak = 0
                streak_chs = []

    # 2. 整体占比
    total = len(per_chapter)
    counts = Counter()
    for _, pmap in per_chapter:
        for t in THROUGHLINES:
            if pmap[t]:
                counts[t] += 1
    distribution = {t: round(counts[t] / total, 2) for t in THROUGHLINES}
    for t in THROUGHLINES:
        if distribution[t] < 0.15 and total >= 5:
            findings.append({
                "severity": "advisory",
                "code": "THROUGHLINE_MARGINALIZED",
                "throughline": t,
                "pct": distribution[t],
                "suggestion": f"throughline {t} 近 {total} 章占比 {round(distribution[t]*100)}% (< 15%) → 边缘化",
            })
    if distribution["OS"] > 0.95 and total >= 5:
        findings.append({
            "severity": "advisory",
            "code": "OS_DOMINANT",
            "pct": distribution["OS"],
            "suggestion": "OS 客观主线推进过密（>95%），应分配更多笔墨给 MC/IC/RS",
        })

    # 3. 每章 ≥ 2 条覆盖率
    chs_with_lt2 = []
    for ch, pmap in per_chapter:
        active_count = sum(1 for t in THROUGHLINES if pmap[t])
        if active_count < 2:
            chs_with_lt2.append(ch)
    if len(chs_with_lt2) >= total * 0.4:
        findings.append({
            "severity": "advisory",
            "code": "PER_CHAPTER_COVERAGE_LOW",
            "low_coverage_chs": chs_with_lt2,
            "pct": round(len(chs_with_lt2) / total, 2),
            "suggestion": f"近 {total} 章中 {len(chs_with_lt2)} 章只推进 < 2 条 throughline（占 {round(len(chs_with_lt2)/total*100)}%）→ writer 应每章覆盖 ≥ 2",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "throughline_balance",
        "scan_ts": ts,
        "chapters_scanned": [c for c, _ in per_chapter],
        "distribution": distribution,
        "per_chapter": [{"ch": c, **{t: pmap[t] for t in THROUGHLINES}} for c, pmap in per_chapter],
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"throughline_balance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[throughline_balance] {len(per_chapter)} 章 distribution: " + ", ".join(f"{t}={distribution[t]:.0%}" for t in THROUGHLINES))
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
