#!/usr/bin/env python3
"""Apply the selected cluster choice artifact to world evolution.

Canonical entry:
    python core/scripts/world_evolution_apply_card.py <project> \
        --next-key 002 \
        --choice _数据库/.wal/cluster_002_user_choice.json

The selected card is the cluster choice artifact produced by the single
cluster chain. This public CLI accepts no chapter number or A/B/C label, and
it never reads per-chapter fate card files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cluster_lookup as cl  # noqa: E402
import world_evolution_engine as wee  # noqa: E402
from atomic_json import atomic_write_json  # noqa: E402

DB_DIR = "\u6570\u636e\u5e93"
EVENT_CLUSTER_FILE = "\u4e8b\u4ef6\u7c07.json"
WORLD_STATE_FILE = "\u4e16\u754c\u72b6\u6001.json"
RIPPLE_RULES_FILE = "\u6d9f\u6f2a\u89c4\u5219.json"
VALUE_OPTIONS = {"--next-key", "--choice"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    atomic_write_json(path, data)


def _resolve_choice_path(project_root: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else project_root / p


def _extract_brief(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("choice JSON must be an object")
    if isinstance(payload.get("answer"), dict):
        return payload["answer"]
    if isinstance(payload.get("cluster_brief"), dict):
        return payload["cluster_brief"]
    if any(k in payload for k in ("scope_summary", "scene_storyboard", "ripple_match")):
        return payload
    raise ValueError("choice JSON is not a cluster brief: missing answer/cluster_brief/brief body")


def _read_choice(choice_path: Path) -> dict[str, Any]:
    if not choice_path.exists():
        raise FileNotFoundError(f"choice file not found: {choice_path}")
    try:
        payload = _load_json(choice_path)
    except json.JSONDecodeError as exc:
        raise ValueError(f"choice JSON parse failed: {exc}") from exc
    return _extract_brief(payload)


def _project_relative(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def _candidate_file(project_root: Path, cluster_id: str) -> Path:
    return project_root / f"_{DB_DIR}" / ".wal" / f"{cluster_id}_brief_candidates.json"


def _load_candidates(project_root: Path, cluster_id: str) -> tuple[Path | None, list[dict[str, Any]]]:
    path = _candidate_file(project_root, cluster_id)
    if not path.exists():
        return None, []

    payload = _load_json(path)
    if isinstance(payload, dict):
        raw_candidates = payload.get("candidates")
    elif isinstance(payload, list):
        raw_candidates = payload
    else:
        raise ValueError(f"candidate JSON must be an object or array: {path}")

    if not isinstance(raw_candidates, list):
        raise ValueError(f"candidate JSON missing candidates array: {path}")
    if any(not isinstance(item, dict) for item in raw_candidates):
        raise ValueError(f"candidate JSON contains non-object candidate: {path}")
    return path, list(raw_candidates)


def _candidate_rank(raw_cluster_id: Any) -> str:
    match = re.search(r"candidate[_-]?(\d+)", str(raw_cluster_id or ""))
    return f"candidate_{match.group(1)}" if match else "chosen"


def _same_candidate(candidate: dict[str, Any], brief: dict[str, Any]) -> bool:
    if candidate.get("cluster_id") and candidate.get("cluster_id") == brief.get("cluster_id"):
        return True

    paired_fields = ("scope_summary", "ripple_match")
    if all(brief.get(field) for field in paired_fields):
        return all(candidate.get(field) == brief.get(field) for field in paired_fields)

    title = brief.get("title")
    return bool(title and candidate.get("title") == title)


def _choice_metadata(
    project_root: Path,
    cluster_id: str,
    brief: dict[str, Any],
    choice_path: Path,
) -> dict[str, Any]:
    candidates_path, candidates = _load_candidates(project_root, cluster_id)
    selected_index = None
    for idx, candidate in enumerate(candidates, start=1):
        if _same_candidate(candidate, brief):
            selected_index = idx
            break

    choice_key = (
        f"candidate_{selected_index}"
        if selected_index is not None
        else _candidate_rank(brief.get("cluster_id"))
    )
    title = str(brief.get("title") or brief.get("scope_summary") or choice_key)
    metadata: dict[str, Any] = {
        "choice_key": choice_key,
        "choice_title": title,
        "choice_file": _project_relative(project_root, choice_path),
        "selected_cluster_id": brief.get("cluster_id"),
        "candidate_count": len(candidates),
    }
    if candidates_path is not None:
        metadata["candidates_file"] = _project_relative(project_root, candidates_path)
    if selected_index is not None:
        metadata["selected_candidate_index"] = selected_index
    return metadata


def _validate_cluster_match(cluster_id: str, brief: dict[str, Any]) -> None:
    raw = brief.get("cluster_id")
    if raw is None:
        raise ValueError("selected cluster brief missing required cluster_id")
    brief_cluster = cl.normalize_cluster_id(raw)
    if brief_cluster is not None and brief_cluster != cluster_id:
        raise ValueError(
            f"choice cluster mismatch: --next-key={cluster_id}, brief.cluster_id={raw}"
        )


def _selected_brief_for_landing(cluster_id: str, brief: dict[str, Any]) -> dict[str, Any]:
    landed = dict(brief)
    landed["cluster_id"] = cluster_id
    landed["status"] = "in_progress"
    landed.setdefault("_writer_mode", "freestyle")
    landed.pop("estimated_chapters", None)
    landed.pop("chapter_range", None)
    return landed


def _write_cluster_choice(
    project_root: Path,
    cluster_id: str,
    brief: dict[str, Any],
    metadata: dict[str, Any],
    world_result: dict[str, Any],
) -> None:
    db = project_root / f"_{DB_DIR}"
    event_path = db / EVENT_CLUSTER_FILE
    data = _load_json(event_path) if event_path.exists() else {"schema_version": "v23.0"}
    if not isinstance(data, dict):
        raise ValueError(f"{EVENT_CLUSTER_FILE} must be an object")

    clusters = data.setdefault("clusters", [])
    if not isinstance(clusters, list):
        raise ValueError(f"{EVENT_CLUSTER_FILE}.clusters must be an array")

    landed = _selected_brief_for_landing(cluster_id, brief)
    landed["user_choice"] = metadata["choice_key"]
    landed["_user_decision"] = {
        "choice_key": metadata["choice_key"],
        "choice_title": metadata["choice_title"],
        "choice_file": metadata["choice_file"],
        "selected_cluster_id": metadata.get("selected_cluster_id"),
        "candidate_count": metadata["candidate_count"],
    }
    if "candidates_file" in metadata:
        landed["_user_decision"]["candidates_file"] = metadata["candidates_file"]
    if "selected_candidate_index" in metadata:
        landed["_user_decision"]["selected_candidate_index"] = metadata["selected_candidate_index"]
    if landed.get("scope_summary"):
        landed["choice_leads_to"] = landed["scope_summary"]
    landed["world_evolution_card"] = world_result

    replaced = False
    for idx, existing in enumerate(clusters):
        if not isinstance(existing, dict):
            continue
        if cl.normalize_cluster_id(existing.get("cluster_id")) == cluster_id:
            merged = dict(existing)
            merged.update(landed)
            clusters[idx] = merged
            replaced = True
            break
    if not replaced:
        clusters.append(landed)

    _write_json(event_path, data)


def apply_selected_card(project_root: Path, next_key: str, choice_path: Path) -> int:
    cluster_id = cl.normalize_cluster_id(next_key)
    if not cluster_id:
        raise ValueError(f"invalid next cluster id: {next_key}")
    if cluster_id == "cluster_001":
        print("[OK] cluster_001 has no previous user-choice artifact; world evolution card step is complete")
        return 0

    brief = _read_choice(choice_path)
    _validate_cluster_match(cluster_id, brief)
    metadata = _choice_metadata(project_root, cluster_id, brief, choice_path)

    if "ripple_match" not in brief:
        raise ValueError("selected cluster brief missing required ripple_match")

    title = str(brief.get("title") or brief.get("scope_summary") or cluster_id)
    ripple_match = str(brief.get("ripple_match") or "").strip()
    print(f"[selected_cluster_card] {cluster_id}: {title}")

    if not ripple_match:
        raise ValueError("selected cluster brief ripple_match must be non-empty")

    db = project_root / f"_{DB_DIR}"
    world_path = db / WORLD_STATE_FILE
    rules_path = db / RIPPLE_RULES_FILE
    if not world_path.exists() or not rules_path.exists():
        missing = [
            name
            for name, path in (
                (WORLD_STATE_FILE, world_path),
                (RIPPLE_RULES_FILE, rules_path),
            )
            if not path.exists()
        ]
        raise ValueError(
            "world evolution required files missing: " + ", ".join(missing)
        )

    result = wee.apply_minor_event(project_root, cluster_id, ripple_match)
    matched = result.get("matched_rules", [])
    applied = result.get("applied_log", [])

    print(
        f"[apply_minor_event] cluster={cluster_id} "
        f"trigger={ripple_match!r}"
    )
    print(f"  matched_rules: {matched}")
    print(f"  applied_count: {len(applied)}")
    for item in applied[:5]:
        target = item.get("target", "?")
        op = item.get("op", "?")
        if op == "delta":
            print(
                f"    {target}: {item.get('old')} -> {item.get('new')} "
                f"(delta {item.get('delta')}) [{str(item.get('reason', ''))[:30]}]"
            )
        elif op == "add_thread":
            print(f"    + thread {item.get('thread_id')} ({item.get('npc')})")
        elif op == "spawn":
            print(f"    + spawn {target}")
        else:
            print(f"    {op} {target}")

    _write_cluster_choice(
        project_root,
        cluster_id,
        brief,
        metadata,
        {
            "trigger_type": "minor_event",
            "ripple_match": ripple_match,
            "cluster_id": cluster_id,
            "matched_rules": matched,
            "applied_count": len(applied),
        },
    )
    if not matched:
        raise ValueError(
            f"ripple_match={ripple_match!r} matched no rules; check {RIPPLE_RULES_FILE}"
        )
    return 0


def _extra_positionals(argv: list[str]) -> list[str]:
    positionals: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in VALUE_OPTIONS:
            skip_next = True
            continue
        if any(token.startswith(flag + "=") for flag in VALUE_OPTIONS):
            continue
        if token.startswith("-"):
            continue
        positionals.append(token)
    return positionals[1:]


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if "--cluster" in raw_argv or any(item.startswith("--cluster=") for item in raw_argv):
        print(
            "[FATAL] --cluster is not a public world_evolution_apply_card argument; "
            "use --next-key <key> with a cluster user-choice artifact",
            file=sys.stderr,
        )
        return 2
    if _extra_positionals(raw_argv):
        print(
            "[FATAL] legacy single-chapter direction-card CLI is removed; "
            "use <project> --next-key <key> --choice <cluster_user_choice.json>",
            file=sys.stderr,
        )
        return 2

    parser = argparse.ArgumentParser(
        description="Apply a cluster user-choice artifact to world evolution"
    )
    parser.add_argument("project")
    parser.add_argument("--next-key", required=True, help="next cluster key, e.g. 002")
    parser.add_argument("--choice", required=True, help="cluster user choice JSON")
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    project_root = Path(args.project)
    choice_path = _resolve_choice_path(project_root, args.choice)
    try:
        return apply_selected_card(project_root, args.next_key, choice_path)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}")
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
