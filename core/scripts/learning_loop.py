#!/usr/bin/env python3
"""Maintain the cluster-native writing-quality learning loop.

The command merges one cluster reflection, ingests one cluster audit, rebuilds
recurrence state from all cluster audits, and attributes persistent style drift
to the active author skill. Stored provenance uses canonical ``cluster_id``
values throughout. Audit waivers remain advisory calibration evidence and never
alter hard-gate policy.

Commands::

    python learning_loop.py <project> --merge-reflection <reflection.json>
    python learning_loop.py <project> --ingest <cluster_audit.json>
    python learning_loop.py <project> --scan-recurring
    python learning_loop.py <project> --reflect-attribution

Exit status 0 means no new attention item, 1 means the run produced an
escalation/calibration/attribution result, and 2 means an artifact contract or
path is invalid.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import atomic_json
import learning_loop_attribution as attribution
import learning_loop_store as store
import learning_loop_tracking as tracking

EXPERIENCE_FILE = store.EXPERIENCE_FILE
AUDIT_DIR = store.AUDIT_DIR
EXPIRY_DAYS = store.EXPIRY_DAYS
DECAY_DAYS = store.DECAY_DAYS
RECUR_THRESHOLD = tracking.RECUR_THRESHOLD
CONSECUTIVE_ESCALATE = tracking.CONSECUTIVE_ESCALATE
WAIVER_CALIBRATION_THRESHOLD = tracking.WAIVER_CALIBRATION_THRESHOLD
EFFICACY_MIN_POST_CLUSTERS = tracking.EFFICACY_MIN_POST_CLUSTERS
EFFICACY_IMPROVE_TOLERANCE = tracking.EFFICACY_IMPROVE_TOLERANCE
_CODE_TO_CONTROLLED_KEY = tracking._CODE_TO_CONTROLLED_KEY
_QUANT_RELAX_PER_WAIVER = tracking._QUANT_RELAX_PER_WAIVER
_QUANT_RELAX_CAP = tracking._QUANT_RELAX_CAP
REFLECT_RECUR_THRESHOLD = attribution.REFLECT_RECUR_THRESHOLD
SEMANTIC_ATTRIB_FLOOR = attribution.SEMANTIC_ATTRIB_FLOOR
_STYLE_CODE_ATTRIB = attribution.STYLE_CODE_ATTRIB

_db_dir = store.db_dir
_experience_path = store.experience_path
_empty_experience = store.empty_experience
load_experience = store.load_experience
save_experience = store.save_experience
_now = store.now_text
_stamp_updated = store.stamp_updated
_route_entry = store.route_entry
_prune_and_decay = store.prune_and_decay
_has_consecutive_clusters = store.has_consecutive_clusters

merge_reflection = tracking.merge_reflection
_issue_key = tracking.issue_key
_cluster_scene_types = tracking.cluster_scene_types
_collect_waived_issues = tracking.collect_waived_issues
_track_waivers = tracking.track_waivers
_build_calibration_suggestions = tracking.build_calibration_suggestions
ingest_audit = tracking.ingest_audit
_recur_rate = tracking.recur_rate
_record_efficacy_baseline = tracking.record_efficacy_baseline
evaluate_efficacy = tracking.evaluate_efficacy
_escalate_recurring = tracking.escalate_recurring
_quantized_delta_hint = tracking.quantized_delta_hint

_reflect_enabled = attribution.reflect_enabled
_resolve_skill_path = attribution.resolve_skill_path
_parse_skill_sections = attribution.parse_skill_sections
_has_real_embedding_backend = attribution.has_real_embedding_backend
_semantic_attribute_to_skill_section = attribution.semantic_attribute_to_skill_section
_attribute_to_skill_section = attribution.attribute_to_skill_section
_collect_live_drift_findings = attribution.collect_live_drift_findings
_collect_persistent_style_failures = attribution.collect_persistent_style_failures
reflect_attribution = attribution.reflect_attribution


def accumulate_pid_state_from_calibration(author_dir, suggestions, cluster_id=None) -> dict | None:
    """Apply quantified advisory calibration evidence to the author PID state."""
    if author_dir is None:
        return None
    try:
        import pid_threshold_tuner as pid
    except Exception:
        return None
    errors, evidence = {}, 0
    for suggestion in suggestions or []:
        delta = suggestion.get("quantized_delta") if isinstance(suggestion, dict) else None
        if not isinstance(delta, dict):
            continue
        key = delta.get("controlled_key")
        if key not in pid._CONTROLLED_KEYS:
            continue
        errors[key] = max(errors.get(key, 0.0), float(delta.get("relax_frac", 0.0)))
        evidence = max(evidence, int(suggestion.get("waived_count", 0)))
    if not errors:
        return None
    state = pid.load_state(Path(author_dir))
    sample_count = max(int(state.get("n_samples", 0)), evidence, pid._MIN_SAMPLES)
    pid.update_controller(state, errors, cluster_id=cluster_id, n_samples=sample_count)
    pid.save_state(Path(author_dir), state)
    return state


def _bridge_pid_state(project_root: Path, audit_path: Path | None) -> None:
    """Feed current cluster calibration suggestions into the active author PID state."""
    style_path = store.db_dir(project_root) / "作者风格.json"
    if not style_path.is_file():
        return
    try:
        style = json.loads(style_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    source = style.get("style_source") if isinstance(style, dict) else None
    if not isinstance(source, str) or not source:
        return
    author_dir = Path(source)
    if not author_dir.is_absolute():
        author_dir = SCRIPT_DIR.parent.parent / author_dir
    author_dir = author_dir.parent if author_dir.is_file() else author_dir
    if not author_dir.is_dir():
        return

    experience = store.load_experience(project_root)
    suggestions = experience.get("tool_calibration_suggestions") or []
    if not suggestions:
        return
    if audit_path is not None:
        audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
        cluster_id = tracking.audit_cluster_id(audit)
    else:
        observed = experience.get("_observed_clusters") or []
        cluster_id = observed[-1] if observed else None
    state = accumulate_pid_state_from_calibration(author_dir, suggestions, cluster_id)
    if state is not None:
        print(
            f"[learning_loop] [pid-bridge] author_dir={author_dir.name} "
            f"n_samples={state.get('n_samples', 0)}"
        )


def scan_recurring(project_root: Path) -> dict:
    return tracking.scan_recurring(project_root, reflect_callback=reflect_attribution)


def _resolve_input(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(0)
    project_root = Path(args[0]).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        raise SystemExit(2)

    try:
        if "--merge-reflection" in args:
            index = args.index("--merge-reflection")
            if index + 1 >= len(args):
                raise store.ExperienceContractError("--merge-reflection 需要 reflection.json 路径")
            merge_reflection(project_root, _resolve_input(project_root, args[index + 1]))
            raise SystemExit(0)
        if "--ingest" in args:
            index = args.index("--ingest")
            if index + 1 >= len(args):
                raise store.ExperienceContractError("--ingest 需要 cluster audit 路径")
            audit_path = _resolve_input(project_root, args[index + 1])
            result = ingest_audit(project_root, audit_path)
            _bridge_pid_state(project_root, audit_path)
            attention = result.get("escalated") or result.get("calibration") or result.get("ineffective")
            raise SystemExit(1 if attention else 0)
        if "--scan-recurring" in args:
            result = scan_recurring(project_root)
            _bridge_pid_state(project_root, None)
            attention = any(result.get(key) for key in (
                "escalated", "meta_problems", "calibration", "ineffective", "skill_rewrite"
            ))
            raise SystemExit(1 if attention else 0)
        if "--reflect-attribution" in args:
            produced = reflect_attribution(project_root)
            raise SystemExit(1 if produced else 0)
        raise store.ExperienceContractError(
            "需指定 --merge-reflection / --ingest / --scan-recurring / --reflect-attribution"
        )
    except store.ExperienceContractError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
