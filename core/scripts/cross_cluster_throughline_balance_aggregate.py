"""检查连续 cluster 的四条 Dramatica 叙事线推进均衡度。

读 事件簇.clusters[].throughline_progress 历史，检测：
- 某线连续 ≥ 4 个 cluster 无推进（线沉睡告警）
- 某线占比 < 15%（线被边缘化）
- OS 单线占比 > 60%（其他线被忽略）
- 四线整体覆盖率（每个 cluster 至少推进两条）

Dramatica 4 throughline：OS / MC / IC / RS

输出：_数据库/.cross_cluster_scan/throughline_balance_<ts>.json
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



sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 摘要驱动读取 cluster 记录
import cluster_state_sources as css  # noqa: E402
import atomic_json  # noqa: E402

THROUGHLINES = ["OS", "MC", "IC", "RS"]

# no_progress 哨兵词表：writer/账本里「无推进」有多种写法
# （none / 无 / 未推进 / N/A / - / false 字符串等），未经归一直接 bool(v) 判断
# 会把这些也当成命中，导致 THROUGHLINE_DORMANT 漏报（线明明沉睡却算作推进）。
_NO_PROGRESS_SENTINELS = {
    "", "no_progress", "no", "none", "null", "nil", "n/a", "na", "-", "—",
    "false", "0", "skip", "skipped",
    "无", "未推进", "无推进", "没有推进", "未涉及", "无进展", "未进展", "无变化", "未触及",
}


def _has_progress(v) -> bool:
    """判断某 throughline 在当前 cluster 是否真有推进。

    布尔 True / 非哨兵的非空字符串 / 非空 dict/list → 推进；
    布尔 False / None / 哨兵词（去空白小写归一后命中）→ 无推进。
    """
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() not in _NO_PROGRESS_SENTINELS
    if isinstance(v, (list, dict)):
        return bool(v)
    # 数值：0 视为无推进，其余有推进
    if isinstance(v, (int, float)):
        return v != 0
    return bool(v)


def load_json(p: Path, default=None):
    return atomic_json.load_json(p, default=default)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)

    per_cluster = []
    for cluster_id, _record in css.iter_completed_clusters(project_root, args.last_n):
        tp = css.load_cluster_ledger(project_root, cluster_id).get("throughline_progress") or {}
        progress_map = {line: _has_progress(tp.get(line, False)) for line in THROUGHLINES}
        per_cluster.append((cluster_id, progress_map))

    if not per_cluster:
        print("[SKIP] 无 throughline_progress 记录")
        sys.exit(0)

    findings = []

    # 1. 连续 no_progress 检测
    for t in THROUGHLINES:
        streak = 0
        streak_clusters = []
        for cluster_id, pmap in per_cluster:
            if not pmap[t]:
                streak += 1
                streak_clusters.append(cluster_id)
                if streak >= 4:
                    findings.append({
                        "severity": "warning",
                        "code": "THROUGHLINE_DORMANT",
                        "throughline": t,
                        "consecutive_clusters": streak_clusters[-4:],
                        "suggestion": f"throughline {t} 连续 ≥ 4 个 cluster 无推进 → 应至少推进 1 次（{t} = {{OS: 客观主线, MC: 主角内心, IC: 影响者, RS: 关系本身}}）",
                    })
                    streak = 0
                    streak_clusters = []
            else:
                streak = 0
                streak_clusters = []

    # 2. 整体占比
    total = len(per_cluster)
    counts = Counter()
    for _, pmap in per_cluster:
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
                "suggestion": f"throughline {t} 近 {total} 个 cluster 占比 {round(distribution[t]*100)}% (< 15%) → 边缘化",
            })
    if distribution["OS"] > 0.95 and total >= 5:
        findings.append({
            "severity": "advisory",
            "code": "OS_DOMINANT",
            "pct": distribution["OS"],
            "suggestion": "OS 客观主线推进过密（>95%），应分配更多笔墨给 MC/IC/RS",
        })

    # 3. 每个 cluster 至少推进两条线
    clusters_with_lt2 = []
    for cluster_id, pmap in per_cluster:
        active_count = sum(1 for t in THROUGHLINES if pmap[t])
        if active_count < 2:
            clusters_with_lt2.append(cluster_id)
    if total >= 5 and len(clusters_with_lt2) >= total * 0.4:
        findings.append({
            "severity": "advisory",
            "code": "PER_CLUSTER_COVERAGE_LOW",
            "low_coverage_clusters": clusters_with_lt2,
            "pct": round(len(clusters_with_lt2) / total, 2),
            "suggestion": f"近 {total} 个 cluster 中 {len(clusters_with_lt2)} 个只推进不足两条 throughline（占 {round(len(clusters_with_lt2)/total*100)}%）",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "throughline_balance",
        "scan_ts": ts,
        "clusters_scanned": [c for c, _ in per_cluster],
        "distribution": distribution,
        "per_cluster": [{"cluster_id": c, **{t: pmap[t] for t in THROUGHLINES}} for c, pmap in per_cluster],
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"throughline_balance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[throughline_balance] {len(per_cluster)} cluster distribution: " + ", ".join(f"{t}={distribution[t]:.0%}" for t in THROUGHLINES))
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
