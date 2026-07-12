"""Audit world-state extremes, ripple activity, and storyteller alignment."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value


def scan_faction_trends(project_root: Path) -> list[dict]:
    world = _load_json(project_root / "_数据库" / "世界状态.json", {})
    factions = world.get("factions_state", {}) if isinstance(world, dict) else {}
    if not isinstance(factions, dict) or not factions:
        return []
    if not (project_root / "_数据库" / ".world_evolution").exists():
        return []
    findings: list[dict] = []
    for faction, state in factions.items():
        if not isinstance(state, dict):
            continue
        for dimension in ("power", "stability", "wealth"):
            value = state.get(dimension)
            if not isinstance(value, (int, float)):
                continue
            if value <= 5:
                findings.append({
                    "severity": "advisory",
                    "code": "FACTION_NEAR_ZERO",
                    "faction": faction,
                    "dimension": dimension,
                    "current_value": value,
                })
            if value >= 95:
                findings.append({
                    "severity": "advisory",
                    "code": "FACTION_NEAR_MAX",
                    "faction": faction,
                    "dimension": dimension,
                    "current_value": value,
                })
    return findings


def scan_ripple_dead_abused(project_root: Path) -> list[dict]:
    rules = _load_json(project_root / "_数据库" / "涟漪规则.json", {})
    world = _load_json(project_root / "_数据库" / "世界状态.json", {})
    if not isinstance(rules, dict) or not isinstance(world, dict):
        return []
    rule_defs = rules.get("ripple_rules", []) or []
    log = world.get("world_ticks_log", []) or []
    counts = Counter(
        rule_id
        for entry in log
        if isinstance(entry, dict)
        for rule_id in entry.get("matched_rules", []) or []
        if isinstance(rule_id, str)
    )
    findings: list[dict] = []
    for rule in rule_defs:
        if not isinstance(rule, dict) or rule.get("trigger_type") == "auto_tick":
            continue
        rule_id = rule.get("id")
        if rule_id and counts.get(rule_id, 0) == 0:
            findings.append({"severity": "advisory", "code": "RIPPLE_DEAD", "rule_id": rule_id})
    for rule_id, count in counts.items():
        if count >= 5:
            findings.append({"severity": "advisory", "code": "RIPPLE_ABUSED", "rule_id": rule_id, "trigger_count": count})
    return findings


def scan_storyteller_alignment(project_root: Path) -> list[dict]:
    clusters = csr.get_clusters(project_root)
    outcomes = [cluster.get("outcome") for cluster in clusters if cluster.get("outcome")]
    if len(outcomes) < 5:
        return []
    counts = Counter(outcomes[-10:])
    total = sum(counts.values())
    pacer = _load_json(project_root / "_数据库" / "叙事节拍器.json", {})
    recommendation = pacer.get("narrator_recommendation", {}) if isinstance(pacer, dict) else {}
    next_target = recommendation.get("next_cluster_target_outcome", "auto") if isinstance(recommendation, dict) else "auto"
    findings: list[dict] = []
    setback_pct = counts.get("setback", 0) / total
    if next_target == "setback" and setback_pct < 0.15:
        findings.append({
            "severity": "advisory",
            "code": "STORYTELLER_TARGET_LIKELY_TO_BE_IGNORED",
            "current_setback_pct": round(setback_pct, 2),
            "next_target": next_target,
        })
    win_pct = counts.get("win", 0) / total
    if win_pct > 0.85:
        findings.append({"severity": "advisory", "code": "ALL_WIN_NO_SETBACK", "win_pct": round(win_pct, 2)})
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    findings = (
        scan_faction_trends(project_root)
        + scan_ripple_dead_abused(project_root)
        + scan_storyteller_alignment(project_root)
    )
    summary = {
        "warning": sum(f["severity"] == "warning" for f in findings),
        "advisory": sum(f["severity"] == "advisory" for f in findings),
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {"scan_type": "world_dynamics", "scan_ts": ts, "findings": findings, "summary": summary}
    out_path = out_dir / f"world_dynamics_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[world_dynamics] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
