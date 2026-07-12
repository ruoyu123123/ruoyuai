"""Audit declared story data against cluster state artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup  # noqa: E402
import cluster_state_sources as css  # noqa: E402
import cluster_summary_reader as csr  # noqa: E402


def load_json(path: Path, default=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def _extract_ids(values, *keys) -> set[str]:
    result: set[str] = set()
    if not isinstance(values, (list, tuple)):
        return result
    for value in values:
        if isinstance(value, str) and value:
            result.add(value)
        elif isinstance(value, dict):
            for key in keys:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    result.add(candidate)
                    break
    return result


def _cluster_number(cluster_id) -> int:
    return cluster_lookup.cluster_num(cluster_id) or 0


def _observations(project_root: Path, last_n: int = 10) -> list[tuple[str, dict]]:
    observations: list[tuple[str, dict]] = []
    for cluster_id, summary in css.iter_completed_clusters(project_root, last_n):
        merged: dict = dict(summary)
        ledger = css.load_cluster_ledger(project_root, cluster_id)
        if isinstance(ledger, dict):
            merged.update(ledger)
        delta = css.load_state_delta(project_root, cluster_id)
        merged["state_delta"] = delta
        observations.append((cluster_id, merged))
    return observations


def scan_aspect_continuity(project_root: Path, observations: list[tuple[str, dict]]) -> list[dict]:
    data = load_json(project_root / "_数据库" / "角色烙印.json", {})
    findings: list[dict] = []
    if not isinstance(data, dict):
        return findings
    for character, character_data in (data.get("characters") or {}).items():
        for aspect in character_data.get("active_aspects", []) or []:
            aspect_id = aspect.get("aspect_id")
            acquired = aspect.get("acquired_at_cluster") or aspect.get("acquired_cluster")
            relevant = [(cid, record) for cid, record in observations
                        if not acquired or _cluster_number(cid) >= _cluster_number(acquired)]
            if len(relevant) < 3:
                continue
            streak: list[str] = []
            last_addressed = None
            for cluster_id, record in relevant:
                ids = _extract_ids(record.get("aspects_addressed"), "aspect_id", "id")
                ids |= _extract_ids(record.get("aspect_text_hit"), "aspect_id", "id")
                if aspect_id in ids:
                    streak = []
                    last_addressed = cluster_id
                    continue
                streak.append(cluster_id)
                if len(streak) >= 3:
                    findings.append({
                        "severity": "advisory",
                        "code": "ASPECT_NOT_ADDRESSED",
                        "character": character,
                        "aspect_id": aspect_id,
                        "label": aspect.get("label", "?"),
                        "consecutive_clusters": streak[-3:],
                        "last_addressed_cluster": last_addressed,
                    })
                    streak = []
    return findings


def scan_clock_addressing(project_root: Path, observations: list[tuple[str, dict]]) -> list[dict]:
    data = load_json(project_root / "_数据库" / "时钟表.json", {})
    if not isinstance(data, dict) or len(observations) < 2:
        return []
    recent = observations[-2:]
    findings: list[dict] = []
    for clock in data.get("clocks", []) or []:
        if clock.get("status") != "active":
            continue
        remaining = clock.get("max", 99) - clock.get("ticks", 0)
        if remaining > 2:
            continue
        clock_id = clock.get("clock_id")
        addressed = any(
            clock_id in _extract_ids(record.get("clocks_addressed"), "clock_id", "id")
            for _cid, record in recent
        )
        if not addressed:
            findings.append({
                "severity": "warning",
                "code": "URGENT_CLOCK_IGNORED",
                "clock_id": clock_id,
                "remaining": remaining,
                "checked_clusters": [cid for cid, _record in recent],
            })
    return findings


def scan_heart_event_consistency(project_root: Path, observations: list[tuple[str, dict]]) -> list[dict]:
    data = load_json(project_root / "_数据库" / "群像档.json", {})
    if not isinstance(data, dict):
        return []
    findings: list[dict] = []
    for npc, npc_data in (data.get("characters") or {}).items():
        for event in npc_data.get("heart_events", []) or []:
            consumed = event.get("consumed_at_cluster")
            if not event.get("consumed") or not consumed:
                continue
            event_id = event.get("event_id")
            post = [(cid, record) for cid, record in observations if _cluster_number(cid) > _cluster_number(consumed)][:5]
            if len(post) < 3:
                continue
            appearance = [cid for cid, record in post
                          if npc in (record.get("characters") or [])
                          or (record.get("char_mention_counts") or {}).get(npc, 0) > 0]
            repeated = []
            for cid, record in post:
                revealed = (record.get("state_delta") or {}).get("heart_events_revealed", []) or []
                if event_id in _extract_ids(revealed, "event_id", "id"):
                    repeated.append(cid)
            if len(appearance) >= 2 and not repeated:
                findings.append({
                    "severity": "advisory",
                    "code": "HEART_EVENT_FORGOTTEN",
                    "npc": npc,
                    "event_id": event_id,
                    "consumed_at_cluster": consumed,
                    "post_appearance_clusters": appearance,
                })
    return findings


def scan_fate_dice_consumption(project_root: Path, observations: list[tuple[str, dict]]) -> list[dict]:
    pool = load_json(project_root / "_数据库" / "事件池.json", {})
    if not isinstance(pool, dict):
        return []
    events = {event.get("event_id"): event for event in pool.get("events", []) or [] if isinstance(event, dict)}
    by_cluster = {cluster_id: record for cluster_id, record in observations}
    findings: list[dict] = []
    for draw in pool.get("drawn_events_log", []) or []:
        if not isinstance(draw, dict):
            continue
        cluster_id = cluster_lookup.normalize_cluster_id(draw.get("cluster_id"))
        event_id = draw.get("event_id")
        if not cluster_id or cluster_id not in by_cluster or not event_id:
            continue
        triggered = (by_cluster[cluster_id].get("state_delta") or {}).get("fate_events_triggered", []) or []
        entry = next((item for item in triggered if isinstance(item, dict) and item.get("event_id") == event_id), None)
        if entry is None:
            findings.append({
                "severity": "warning",
                "code": "FATE_DICE_NOT_DECLARED",
                "cluster_id": cluster_id,
                "event_id": event_id,
            })
            continue
        evidence_text = str(entry.get("evidence") or "")
        physical = events.get(event_id, {}).get("physical_evidence", []) or []
        tokens = [token for value in physical for token in re.findall(r"[一-鿿]{2,4}", str(value))[:2]]
        ratio = sum(token in evidence_text for token in tokens) / max(1, len(tokens))
        if tokens and ratio < 0.3:
            findings.append({
                "severity": "advisory",
                "code": "FATE_DICE_EVIDENCE_LOW",
                "cluster_id": cluster_id,
                "event_id": event_id,
                "evidence_hit_ratio": round(ratio, 2),
            })
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    observations = _observations(project_root, args.last_n)
    if not observations:
        print("[SKIP] no completed cluster records")
        raise SystemExit(0)
    findings = (
        scan_aspect_continuity(project_root, observations)
        + scan_clock_addressing(project_root, observations)
        + scan_heart_event_consistency(project_root, observations)
        + scan_fate_dice_consumption(project_root, observations)
    )
    summary = {"warning": sum(f["severity"] == "warning" for f in findings),
               "advisory": sum(f["severity"] == "advisory" for f in findings)}
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {"scan_type": "data_consumption", "scan_ts": ts,
              "clusters_scanned": [cid for cid, _record in observations],
              "findings": findings, "summary": summary}
    out_path = out_dir / f"data_consumption_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[data_consumption] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
