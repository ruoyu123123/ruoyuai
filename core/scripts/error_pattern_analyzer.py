"""error_pattern_analyzer.py — 错误模式聚类分析（v22.5 L2 / LogSage 风格）

业界 arxiv 2506.03691 LogSage（ByteDance 80%+ E2E precision）思路：
聚合 → cluster by failure mode → label root cause → prioritize by cluster size

我们的错误源：
1. 各 cross_chapter_scan 报告（含 findings 的 code）
2. .audit/ch_*_audit*.json findings
3. .judge_reports/ 中 health_warnings
4. .world_evolution/ apply log 异常
5. .cross_chapter_scan/ 各 scanner 失败

输出：_数据库/.learning/error_patterns_<ts>.json
含 cluster + label + count + prioritized fix suggestions
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_errors(project_root: Path) -> list[dict]:
    """从所有来源聚合 error / warning / finding code"""
    errors = []

    # 1. cross_chapter scan reports
    scan_dir = project_root / "_数据库" / ".cross_chapter_scan"
    if scan_dir.exists():
        for f in scan_dir.glob("*.json"):
            data = load_json(f, {})
            scan_type = data.get("scan_type", "unknown")
            for finding in data.get("findings", []) or []:
                if isinstance(finding, dict):
                    sev = finding.get("severity", "info")
                    if sev in ("warning", "error", "advisory"):
                        errors.append({
                            "source": f"cross_chapter:{scan_type}",
                            "code": finding.get("code", "UNKNOWN"),
                            "severity": sev,
                            "ts": data.get("scan_ts", "?"),
                            "context": str(finding.get("ch") or finding.get("suggestion", ""))[:80],
                        })

    # 2. audit hub reports
    audit_dir = project_root / "_数据库" / ".audit"
    if audit_dir.exists():
        for f in audit_dir.glob("ch_*_audit*.json"):
            data = load_json(f, {})
            for issue in data.get("issues", []) or []:
                if isinstance(issue, dict):
                    errors.append({
                        "source": "audit_hub",
                        "code": issue.get("code", "UNKNOWN"),
                        "severity": issue.get("severity", "info"),
                        "ts": data.get("ts", "?"),
                        "context": f"ch{data.get('chapter','?')}",
                    })

    # 3. judge reports health warnings
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if judge_dir.exists():
        for f in judge_dir.glob("*.json"):
            data = load_json(f, {})
            for hw in data.get("health_warnings", []) or []:
                code = hw.get("code") if isinstance(hw, dict) else "UNKNOWN"
                errors.append({
                    "source": "judge_report",
                    "code": code,
                    "severity": "warning",
                    "ts": data.get("ts", "?"),
                    "context": str(hw)[:80] if isinstance(hw, str) else "",
                })

    return errors


def cluster_by_code(errors: list[dict]) -> dict:
    """按 code 聚类（LogSage 风格 cluster）"""
    clusters = defaultdict(list)
    for e in errors:
        clusters[e["code"]].append(e)
    return dict(clusters)


def prioritize(clusters: dict) -> list[dict]:
    """按 cluster size + severity 排序"""
    prioritized = []
    SEV_WEIGHT = {"fatal": 5, "error": 4, "warning": 3, "advisory": 2, "info": 1}
    for code, items in clusters.items():
        max_sev = max((SEV_WEIGHT.get(e["severity"], 1) for e in items), default=1)
        size = len(items)
        sources = Counter(e["source"] for e in items)
        prioritized.append({
            "code": code,
            "cluster_size": size,
            "max_severity": next((s for s, w in SEV_WEIGHT.items() if w == max_sev), "info"),
            "priority_score": size * max_sev,
            "sources": dict(sources),
            "sample_context": items[0].get("context", "")[:80] if items else "",
        })
    prioritized.sort(key=lambda x: -x["priority_score"])
    return prioritized


def suggest_remediation(top_clusters: list[dict]) -> list[dict]:
    """对 top N 错误模式生成修复建议（启发式）"""
    suggestions = []
    for cluster in top_clusters[:10]:
        code = cluster["code"]
        size = cluster["cluster_size"]
        if size < 3:
            continue  # 太少不优先

        suggestion = {"code": code, "cluster_size": size, "actions": []}
        # 启发式分类
        if "OVERFREQ" in code or "OVERUSE" in code:
            suggestion["actions"].append("→ writer prompt 加更强 frequency 约束 / scanner 阈值调高")
        elif "MISSING" in code or "NOT_ADDRESSED" in code:
            suggestion["actions"].append("→ build_manifest 注入字段加 P0 强调 / writer prompt 加该字段消费纪律")
        elif "DECLINE" in code or "DRIFT" in code:
            suggestion["actions"].append("→ 触发 meta-prompt-optimizer 全面审查")
        elif "DEAD" in code or "NEVER" in code:
            suggestion["actions"].append("→ 触发 dead_feature_detector 标记 deprecation")
        elif "PERSISTENT" in code:
            suggestion["actions"].append("→ skill_evolver 评估是否升级到 hard_gate / 永久禁用规则")
        else:
            suggestion["actions"].append("→ 人工审阅（无自动启发式）")
        suggestions.append(suggestion)
    return suggestions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    errors = collect_errors(project_root)
    clusters = cluster_by_code(errors)
    prioritized = prioritize(clusters)
    suggestions = suggest_remediation(prioritized)

    out = {
        "scan_type": "error_pattern_analyzer",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "total_errors": len(errors),
        "unique_codes": len(clusters),
        "top_10_clusters": prioritized[:10],
        "remediation_suggestions": suggestions,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"error_patterns_{ts}.json"
    save_json(out_path, out)
    print(f"[error_pattern_analyzer] {len(errors)} errors / {len(clusters)} codes / top 10:")
    for p in prioritized[:8]:
        print(f"  [{p['max_severity'].upper()}] {p['code']} × {p['cluster_size']} (score={p['priority_score']})")
    print(f"  报告: {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
