"""Audit character knowledge handoffs across story clusters."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup  # noqa: E402
import cluster_summary_reader as csr  # noqa: E402


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def _cluster_index(project_root: Path, cluster_id: str, clusters: list[dict]) -> int:
    wanted = str(cluster_id)
    for index, cluster in enumerate(clusters, start=1):
        if str(cluster.get("cluster_id")) == wanted:
            return index
    return cluster_lookup.cluster_num(cluster_id) or 0


def _keywords(cluster: dict) -> set[str]:
    values: set[str] = set()
    direct = cluster.get("text_keyword_set") or []
    values.update(str(item) for item in direct if item)
    nested = cluster.get("chapters") or {}
    if isinstance(nested, dict):
        for record in nested.values():
            if isinstance(record, dict):
                values.update(str(item) for item in record.get("text_keyword_set") or [] if item)
    return values


def _collect_hints(clusters: list[dict]) -> list[set[str]]:
    return [_keywords(cluster) for cluster in clusters]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)

    cards = _load_json(project_root / "_数据库" / "人物卡.json", {})
    characters = cards.get("characters", []) if isinstance(cards, dict) else []
    clusters = csr.get_clusters(project_root)
    if not characters or not clusters:
        print("[SKIP] character cards or completed clusters are missing")
        raise SystemExit(0)
    hint_sets = _collect_hints(clusters)
    current_index = len(clusters)
    findings: list[dict] = []

    for character in characters:
        if not isinstance(character, dict):
            continue
        name = character.get("name") or character.get("id")
        if not name:
            continue
        will_learn = ((character.get("knowledge") or {}).get("will_learn") or [])
        for item in will_learn:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or item.get("fact") or item.get("what")
            content = str(item.get("fact") or item.get("content") or item.get("what") or item.get("description") or "")
            learn_cluster = item.get("learn_at_cluster")
            due_index = _cluster_index(project_root, learn_cluster, clusters) if learn_cluster else 0
            if not item_id or not due_index:
                continue
            if current_index > due_index:
                findings.append({
                    "severity": "warning",
                    "code": "WILL_LEARN_OVERDUE",
                    "character": name,
                    "item_id": item_id,
                    "learn_at_cluster": learn_cluster,
                    "current_cluster": clusters[-1].get("cluster_id"),
                    "overdue_by_clusters": current_index - due_index,
                    "suggestion": f"{name} 的知识 {item_id} 已超过 {learn_cluster} 未完成",
                })
            window_start = max(1, due_index - 4)
            if window_start <= current_index <= due_index:
                content_terms = re.findall(r"[一-鿿]{3,5}", content)[:3]
                hinted = any(
                    any(term in hint_set or any(term in hint for hint in hint_set) for term in content_terms)
                    for hint_set in hint_sets[window_start - 1:current_index]
                )
                if content_terms and not hinted:
                    findings.append({
                        "severity": "advisory",
                        "code": "WILL_LEARN_NEVER_HINTED",
                        "character": name,
                        "item_id": item_id,
                        "learn_at_cluster": learn_cluster,
                        "suggestion": "知识点临近学习 cluster 仍没有关键词铺垫",
                    })

    summary = {
        "warning": sum(f["severity"] == "warning" for f in findings),
        "advisory": sum(f["severity"] == "advisory" for f in findings),
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "will_learn",
        "scan_ts": ts,
        "clusters_scanned": [str(cluster.get("cluster_id")) for cluster in clusters],
        "characters_scanned": [c.get("name") or c.get("id") for c in characters if isinstance(c, dict)],
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"will_learn_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[will_learn] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
