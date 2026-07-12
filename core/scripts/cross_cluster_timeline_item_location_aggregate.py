#!/usr/bin/env python3
"""跨 cluster 检查时间推进、道具持有链与地点分布。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import atomic_json
import cluster_state_sources as sources


DAY_INDEX = {"周一": 1, "周二": 2, "周三": 3, "周四": 4,
             "周五": 5, "周六": 6, "周日": 7}


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"必需文件不存在: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON 读取失败: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是 object: {path}")
    return value


def load_records(project_root: Path, last_n: int) -> list[dict]:
    records = []
    for cluster_id, summary in sources.iter_completed_clusters(project_root, last_n):
        delta = sources.load_state_delta(project_root, cluster_id)
        archive = sources.load_archive(project_root, cluster_id)
        if not delta:
            raise ValueError(f"完成 cluster 缺 state delta: {cluster_id}")
        if not archive:
            raise ValueError(f"完成 cluster 缺 archive: {cluster_id}")
        draft_path = (project_root / "章节" / f"{cluster_id}_draft"
                      / f"{cluster_id}_draft.txt")
        try:
            draft = draft_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"完成 cluster 草稿读取失败: {draft_path}: {exc}") from exc
        records.append({
            "cluster_id": cluster_id,
            "summary": summary,
            "delta": delta,
            "archive": archive,
            "draft": draft,
        })
    if not records:
        raise ValueError("故事块摘要中没有已完成 cluster")
    return records


def scan_timeline(records: list[dict]) -> list[dict]:
    """按 state delta.time_advance 检查时间停滞与未解释跳跃。"""
    anchors = []
    for record in records:
        advance = record["delta"].get("time_advance")
        if not isinstance(advance, dict):
            raise ValueError(f"{record['cluster_id']} time_advance 必须是 object")
        period = advance.get("period")
        if isinstance(period, str) and period:
            anchors.append((record["cluster_id"], period, advance.get("elapsed")))
    findings = []
    streak = 1
    for index in range(1, len(anchors)):
        if anchors[index][1] == anchors[index - 1][1]:
            streak += 1
            if streak >= 6:
                findings.append({
                    "severity": "advisory",
                    "code": "TIME_FROZEN",
                    "time": anchors[index][1],
                    "consecutive_clusters": [row[0] for row in anchors[index - 5:index + 1]],
                    "suggestion": "连续 6 个 cluster 的时间段完全相同，请核对时间推进是否漏记",
                })
                streak = 1
        else:
            streak = 1
    for index in range(1, len(anchors)):
        previous, current = anchors[index - 1], anchors[index]
        d1 = next((value for key, value in DAY_INDEX.items() if key in previous[1]), None)
        d2 = next((value for key, value in DAY_INDEX.items() if key in current[1]), None)
        if d1 is None or d2 is None:
            continue
        gap = (d2 - d1) % 7
        elapsed = current[2]
        if gap >= 3 and not (isinstance(elapsed, str) and elapsed.strip()):
            findings.append({
                "severity": "advisory",
                "code": "TIME_GAP_UNEXPLAINED",
                "from": {"cluster_id": previous[0], "time": previous[1]},
                "to": {"cluster_id": current[0], "time": current[1]},
                "day_gap": gap,
                "suggestion": "时间跨越至少三天，但 state delta 未记录 elapsed",
            })
    return findings


def scan_item_chain(project_root: Path, records: list[dict]) -> list[dict]:
    """按 archive.items 检查同块双持有与长期无状态记录。"""
    items = _read_object(project_root / "_数据库" / "道具.json").get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("道具.items 必须是 object array")
    history: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for record in records:
        changes = record["archive"].get("items", [])
        if not isinstance(changes, list) or not all(isinstance(item, dict) for item in changes):
            raise ValueError(f"{record['cluster_id']} archive.items 必须是 object array")
        for change in changes:
            item_id = change.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError(f"{record['cluster_id']} archive item 缺 id")
            holder = change.get("holder")
            if holder is not None and not isinstance(holder, str):
                raise ValueError(f"{record['cluster_id']} archive item holder 无效")
            history[item_id].append((record["cluster_id"], holder))
    findings = []
    for item_id, events in history.items():
        per_cluster: dict[str, set[str]] = defaultdict(set)
        for cluster_id, holder in events:
            if holder:
                per_cluster[cluster_id].add(holder)
        for cluster_id, holders in per_cluster.items():
            if len(holders) > 1:
                findings.append({
                    "severity": "advisory",
                    "code": "ITEM_DUPLICATE_HOLDER",
                    "item_id": item_id,
                    "cluster_id": cluster_id,
                    "holders": sorted(holders),
                    "suggestion": "同一 cluster 的 archive 为道具登记了多个持有人",
                })
    if len(records) >= 10:
        known = {item.get("id") for item in items if isinstance(item.get("id"), str)}
        for item_id in sorted(known - set(history)):
            findings.append({
                "severity": "advisory",
                "code": "ITEM_ABANDONED",
                "item_id": item_id,
                "clusters_without_state_change": len(records),
                "suggestion": "该道具在最近 cluster archive 中没有状态记录，请确认是否已退出叙事",
            })
    return findings


def scan_location_distribution(project_root: Path, records: list[dict]) -> list[dict]:
    """按整块草稿中的地点名称统计访问分布与连续停留。"""
    locations = _read_object(project_root / "_数据库" / "地图.json").get("locations")
    if not isinstance(locations, list) or not all(isinstance(item, dict) for item in locations):
        raise ValueError("地图.locations 必须是 object array")
    names = []
    for position, location in enumerate(locations):
        name = location.get("name") or location.get("id")
        if not isinstance(name, str) or not name:
            raise ValueError(f"地图.locations[{position}] 缺 name/id")
        names.append(name)
    hubs = _read_object(project_root / "_数据库" / "枢纽场景.json").get("hubs")
    if not isinstance(hubs, list) or not all(isinstance(item, dict) for item in hubs):
        raise ValueError("枢纽场景.hubs 必须是 object array")
    hub_names = {
        value for hub in hubs for value in (hub.get("label"), hub.get("name"), hub.get("location_id"))
        if isinstance(value, str) and value
    }

    visits: dict[str, set[str]] = defaultdict(set)
    per_cluster: dict[str, set[str]] = {}
    for record in records:
        mentioned = {name for name in names if name in record["draft"]}
        per_cluster[record["cluster_id"]] = mentioned
        for name in mentioned:
            visits[name].add(record["cluster_id"])
    findings = []
    total = len(records)
    for name in names:
        count = len(visits[name])
        if total and count / total >= 0.6:
            findings.append({
                "severity": "advisory",
                "code": "LOCATION_OVERFREQ",
                "location": name,
                "appearance_clusters": count,
                "pct": round(count / total, 2),
                "suggestion": "该地点在最近 cluster 中出现过密，请核对场景变化节奏",
            })
    never = [name for name in names if not visits[name]]
    if never and len(names) >= 5:
        findings.append({
            "severity": "advisory",
            "code": "LOCATION_NEVER_VISITED",
            "locations": never[:8],
            "total_unused": len(never),
            "total_locations": len(names),
            "suggestion": "地图中存在长期未进入正文的地点",
        })

    current = None
    streak: list[str] = []
    for record in records:
        cluster_id = record["cluster_id"]
        non_hub = [name for name in per_cluster[cluster_id] if name not in hub_names]
        if len(non_hub) == 1 and non_hub[0] == current:
            streak.append(cluster_id)
        elif len(non_hub) == 1:
            current = non_hub[0]
            streak = [cluster_id]
        else:
            current = None
            streak = []
        if current and len(streak) == 4:
            findings.append({
                "severity": "advisory",
                "code": "LOCATION_RHYTHM_BROKEN",
                "location": current,
                "consecutive_clusters": list(streak),
                "suggestion": "连续 4 个 cluster 只使用同一非 Hub 地点",
            })
    return findings


def write_report(project_root: Path, records: list[dict], findings: list[dict]) -> Path:
    report = {
        "schema_version": "timeline-item-location.cluster.v1",
        "scan_type": "timeline_item_location",
        "scan_ts": datetime.now(timezone.utc).isoformat(),
        "clusters_scanned": [record["cluster_id"] for record in records],
        "findings": findings,
        "summary": {"advisory": len(findings)},
    }
    output = (project_root / "_数据库" / ".cross_cluster_scan"
              / "timeline_item_location_latest.json")
    atomic_json.atomic_write_json(output, report)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="cluster 时间/道具/地点连续性顾问")
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()
    if args.last_n < 1:
        print("[FATAL] --last-n 必须大于 0", file=sys.stderr)
        return 2
    project_root = Path(args.project).resolve()
    try:
        records = load_records(project_root, args.last_n)
        findings = [
            *scan_timeline(records),
            *scan_item_chain(project_root, records),
            *scan_location_distribution(project_root, records),
        ]
        output = write_report(project_root, records, findings)
    except (OSError, ValueError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(f"[timeline_item_location] advisory={len(findings)} report={output}")
    return 1 if findings else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
