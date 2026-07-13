"""扫描多个完整故事块的风格锚点频率与开场类型分布。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402
import atomic_json  # noqa: E402


def load_json(path: Path, default=None):
    return atomic_json.load_json(path, default=default)


def find_cluster_drafts(
    project_root: Path, clusters: list[dict]
) -> list[tuple[str, Path]]:
    """按摘要顺序返回完整故事块草稿；任一草稿缺失即拒绝扫描。"""
    result = []
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        path = (
            project_root
            / "章节"
            / f"{cluster_id}_draft"
            / f"{cluster_id}_draft.txt"
        )
        if not path.is_file():
            raise FileNotFoundError(f"故事块草稿不存在: {path}")
        result.append((cluster_id, path))
    return result


def scan_anchor_frequency(
    drafts: list[tuple[str, Path]], anchors_to_track: list[str]
) -> dict[str, dict[str, int]]:
    """返回 ``{anchor: {cluster_id: count}}``。"""
    result = {anchor: {} for anchor in anchors_to_track}
    for cluster_id, path in drafts:
        text = path.read_text(encoding="utf-8")
        for anchor in anchors_to_track:
            result[anchor][cluster_id] = text.count(anchor)
    return result


def check_strategy_violations(
    frequency: dict[str, dict[str, int]], anchor_strategy: list[dict]
) -> list[dict]:
    """按作者档中的故事块频率规则检查锚点使用。"""
    violations = []
    for entry in anchor_strategy:
        name = entry.get("元素")
        strategy = entry.get("策略") or ""
        if not name or name not in frequency:
            continue
        counts = frequency[name]
        ordered_ids = list(counts)

        window_match = re.search(
            r"每\s*(\d+)\s*个?故事块不超过\s*(\d+)\s*次", strategy
        )
        if window_match:
            window = int(window_match.group(1))
            limit = int(window_match.group(2))
            for index, cluster_id in enumerate(ordered_ids):
                window_ids = ordered_ids[max(0, index - window + 1):index + 1]
                count = sum(counts[item] for item in window_ids)
                if count <= limit:
                    continue
                violations.append({
                    "anchor": name,
                    "rule": strategy,
                    "clusters": window_ids,
                    "count": count,
                    "limit": limit,
                    "violation": (
                        f"{window_ids[0]} 至 {cluster_id} 共 {count} 次（上限 {limit}）"
                    ),
                    "severity": "strict" if count > limit + 2 else "mild",
                })

        per_cluster_match = re.search(
            r"每(?:个)?故事块\s*(?:<=|≤|不超过)\s*(\d+)\s*次", strategy
        )
        if per_cluster_match:
            limit = int(per_cluster_match.group(1))
            for cluster_id, count in counts.items():
                if count > limit:
                    violations.append({
                        "anchor": name,
                        "rule": strategy,
                        "cluster_id": cluster_id,
                        "count": count,
                        "limit": limit,
                        "violation": f"{cluster_id} 使用 {count} 次（上限 {limit}）",
                        "severity": "mild",
                    })
    return violations


def scan_opening_types_distribution(clusters: list[dict]) -> dict:
    """聚合 truth_check 确认的故事块开场类型。"""
    by_cluster = {}
    for cluster in clusters:
        truth = cluster.get("truth_check") or {}
        opening_type = truth.get("detected_opening_type")
        if isinstance(opening_type, str) and opening_type.strip():
            by_cluster[cluster["cluster_id"]] = opening_type.strip()
    return {
        "cluster_to_type": by_cluster,
        "type_counts": dict(Counter(by_cluster.values())),
        "total": len(by_cluster),
    }


def find_repeated_opening_runs(
    cluster_to_type: dict[str, str], run_length: int = 3
) -> list[dict]:
    ordered = list(cluster_to_type.items())
    findings = []
    for index in range(len(ordered) - run_length + 1):
        window = ordered[index:index + run_length]
        opening_types = {opening_type for _, opening_type in window}
        if len(opening_types) == 1:
            findings.append({
                "clusters": [cluster_id for cluster_id, _ in window],
                "opening_type": window[0][1],
            })
    return findings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        raise SystemExit(2)

    try:
        clusters = csr.get_clusters(project_root, last_n=args.last_n)
        drafts = find_cluster_drafts(project_root, clusters)
    except (csr.ClusterSummaryError, FileNotFoundError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not clusters:
        print("[SKIP] 无已落账故事块")
        raise SystemExit(0)

    style = load_json(project_root / "_数据库" / "作者风格.json", {}) or {}
    strategy = (
        (style.get("cross_cluster_diversity") or {}).get(
            "env_anchor_high_risk_elements"
        )
        or []
    )
    anchors = [item.get("元素") for item in strategy if item.get("元素")]
    frequency = scan_anchor_frequency(drafts, anchors)
    violations = check_strategy_violations(frequency, strategy)
    opening_distribution = scan_opening_types_distribution(clusters)
    opening_runs = find_repeated_opening_runs(
        opening_distribution["cluster_to_type"]
    )

    report = {
        "scan_type": "style_drift",
        "clusters_scanned": [cluster["cluster_id"] for cluster in clusters],
        "anchor_frequency": frequency,
        "anchor_violations": violations,
        "opening_distribution": opening_distribution,
        "opening_repetition": opening_runs,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and (violations or opening_runs):
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
