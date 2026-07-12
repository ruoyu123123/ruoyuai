"""Cluster audit ingestion, waiver calibration, recurrence, and efficacy tracking."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cluster_lookup
import learning_loop_store as store

RECUR_THRESHOLD = 2
CONSECUTIVE_ESCALATE = 2
WAIVER_CALIBRATION_THRESHOLD = 3
EFFICACY_MIN_POST_CLUSTERS = 2
EFFICACY_IMPROVE_TOLERANCE = 0.0

_CODE_TO_CONTROLLED_KEY = {
    "STYLE_段落均长": "para_mean_len",
    "STYLE_对话占比": "dialogue_ratio",
    "STYLE_长段计数": "long_para_per_chapter",
    "STYLE_配额词": "quota_per_word",
    "STYLE_QUOTA_WORD": "quota_per_word",
    "STYLE_LONG_PARA": "long_para_per_chapter",
    "STYLE_PARA_MEAN": "para_mean_len",
    "STYLE_DIALOGUE": "dialogue_ratio",
    "CHAPTER_END_NO_ANCHOR": "chapter_end_weak_anchor_ratio",
    "CHAPTER_END_WEAK_ANCHOR": "chapter_end_weak_anchor_ratio",
    "CHAPTER_END_CLOSURE_ADVISORY": "chapter_end_weak_anchor_ratio",
}
_QUANT_RELAX_PER_WAIVER = 0.02
_QUANT_RELAX_CAP = 0.10


def quantized_delta_hint(code: str, waived_count: int) -> dict | None:
    key = _CODE_TO_CONTROLLED_KEY.get(code)
    if key is None:
        return None
    relax = min(_QUANT_RELAX_CAP, max(0, waived_count) * _QUANT_RELAX_PER_WAIVER)
    return {
        "controlled_key": key,
        "relax_frac": round(relax, 4),
        "basis": f"waived×{waived_count}",
    }


def _read_json(path: Path, artifact: str) -> dict:
    if not path.is_file():
        raise store.ExperienceContractError(f"{artifact} 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise store.ExperienceContractError(f"{artifact} 读取失败: {exc}") from exc
    if not isinstance(data, dict):
        raise store.ExperienceContractError(f"{artifact} 顶层必须是 object")
    return data


def audit_cluster_id(audit: dict) -> str:
    return store.canonical_cluster_id(audit.get("cluster_id"), field="audit.cluster_id")


def cluster_scene_types(project_root: Path, cluster_id: str) -> list[str]:
    cluster_id = store.canonical_cluster_id(cluster_id)
    progress_path = store.db_dir(project_root) / "进度.json"
    if not progress_path.is_file():
        return []
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    blueprint = cluster_lookup.normalize_blueprint(progress)
    cluster = blueprint.get(cluster_id)
    if not isinstance(cluster, dict):
        return []
    result, seen = [], set()
    for scene in cluster.get("scene_storyboard", []) or []:
        if not isinstance(scene, dict):
            continue
        values = scene.get("scene_type") or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def merge_reflection(project_root: Path, reflection_path: Path) -> dict:
    reflection = _read_json(reflection_path, "reflection")
    cluster_id = store.canonical_cluster_id(
        reflection.get("cluster_id"), field="reflection.cluster_id"
    )
    entries = reflection.get("entries")
    if not isinstance(entries, list):
        raise store.ExperienceContractError("reflection.entries 必须是 list")

    experience = store.load_experience(project_root)
    routed = {"success": 0, "failure": 0, "skip": 0}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            routed["skip"] += 1
            continue
        source_cluster = store.canonical_cluster_id(
            raw_entry.get("source_cluster"), field="reflection.entries[].source_cluster"
        )
        if source_cluster != cluster_id:
            raise store.ExperienceContractError(
                f"reflection entry 来源 {source_cluster} 与 {cluster_id} 不一致"
            )
        entry = dict(raw_entry)
        entry.pop("source_cluster")
        entry["source_clusters"] = [cluster_id]
        routed[store.route_entry(experience, entry)] += 1

    store.save_experience(project_root, experience)
    print(f"[merge-reflection] {cluster_id} reflection 已合并 -> 写作经验.json")
    print(
        f"  success_patterns +{routed['success']}  failure_patterns +{routed['failure']}"
        f"  跳过 {routed['skip']}"
    )
    note = reflection.get("note")
    if isinstance(note, str) and note:
        print(f"  reflector note: {note}")
    return {"routed": routed, "cluster_id": cluster_id}


def issue_key(issue: dict) -> str:
    dimension = issue.get("dimension", "unknown")
    code = issue.get("code") or issue.get("check") or issue.get("desc", "")[:20]
    return f"{dimension}::{code}"


def collect_waived_issues(audit: dict) -> list[dict]:
    waived = audit.get("waived_issues")
    if not isinstance(waived, list):
        raise store.ExperienceContractError("cluster audit 必须包含 waived_issues list")
    result = []
    for item in waived:
        if not isinstance(item, dict):
            raise store.ExperienceContractError("waived_issues 项必须是 object")
        code = item.get("code")
        reason = (item.get("waive_reason") or "").strip()
        if not isinstance(code, str) or not code or not reason:
            raise store.ExperienceContractError("waived_issues 必须包含 code 和 waive_reason")
        result.append({
            "code": code,
            "dimension": item.get("dimension", "unknown"),
            "waive_reason": reason[:300],
        })
    return result


def track_waivers(
    experience: dict,
    project_root: Path,
    audit: dict,
    cluster_id: str,
) -> set[str]:
    tracker = experience["_waiver_tracker"]
    scene_types = cluster_scene_types(project_root, cluster_id)
    touched, seen_codes = set(), set()
    for waiver in collect_waived_issues(audit):
        code = waiver["code"]
        if code in seen_codes:
            continue
        seen_codes.add(code)
        record = tracker.setdefault(code, {
            "count": 0,
            "clusters": [],
            "dimension": waiver["dimension"],
            "scene_types": {},
            "reasons": [],
        })
        if cluster_id not in record["clusters"]:
            record["clusters"].append(cluster_id)
            record["clusters"] = store.sort_clusters(record["clusters"])
            record["count"] += 1
        for scene_type in scene_types:
            record["scene_types"][scene_type] = record["scene_types"].get(scene_type, 0) + 1
        reason = waiver["waive_reason"]
        if reason not in record["reasons"]:
            record["reasons"] = (record["reasons"] + [reason])[-5:]
        touched.add(code)

    waiver_audit = audit.get("waiver_audit")
    if not isinstance(waiver_audit, dict):
        raise store.ExperienceContractError("cluster audit 必须包含 waiver_audit object")
    experience["_waiver_audit_ledger"][cluster_id] = {
        "waive_rate": waiver_audit.get("waive_rate", 0.0),
        "advisory_total": waiver_audit.get("advisory_total", 0),
        "advisory_waived": waiver_audit.get("advisory_waived", 0),
        "blanket_suspected": bool(waiver_audit.get("blanket_suspected", False)),
        "orphan_codes": list(waiver_audit.get("orphan_codes", []) or []),
        "repeated_reason_codes": dict(waiver_audit.get("repeated_reason_codes", {}) or {}),
        "ts": store.now_text(),
    }
    return touched


def build_calibration_suggestions(experience: dict, only_codes=None) -> list[dict]:
    suggestions = experience["tool_calibration_suggestions"]
    produced = []
    for code, record in experience["_waiver_tracker"].items():
        if only_codes is not None and code not in only_codes:
            continue
        if record["count"] < WAIVER_CALIBRATION_THRESHOLD:
            continue
        dominant_scene, dominant_count = None, 0
        for scene_type, count in record.get("scene_types", {}).items():
            if count > dominant_count:
                dominant_scene, dominant_count = scene_type, count
        if dominant_scene and dominant_count >= WAIVER_CALIBRATION_THRESHOLD:
            suggestion_type = "add_scene_adaptation"
            suggestion = (
                f"检测项 [{code}] 在「{dominant_scene}」场景连续被合理豁免 {dominant_count} 次，"
                f"建议增加该场景的检测适配。"
            )
            confidence = 0.9
        else:
            suggestion_type = "adjust_threshold"
            suggestion = (
                f"检测项 [{code}] 在 cluster {record['clusters']} 累计被合理豁免 "
                f"{record['count']} 次，建议复核 advisory 阈值或默认 severity。"
            )
            confidence = 0.75
        entry = {
            "code": code,
            "dimension": record.get("dimension", "unknown"),
            "waived_count": record["count"],
            "clusters": list(record["clusters"]),
            "scene_type_hint": dominant_scene if suggestion_type == "add_scene_adaptation" else None,
            "suggestion_type": suggestion_type,
            "suggestion": suggestion,
            "sample_reasons": list(record.get("reasons", [])),
            "confidence": confidence,
            "updated_at": store.now_text(),
        }
        if suggestion_type == "adjust_threshold":
            delta = quantized_delta_hint(code, record["count"])
            if delta is not None:
                entry["quantized_delta"] = delta
        suggestions[:] = [item for item in suggestions if item.get("code") != code]
        suggestions.append(entry)
        produced.append(entry)
    return produced


def recur_rate(count: int, clusters) -> float:
    total = len(set(clusters or []))
    return count / total if total else 0.0


def record_efficacy_baseline(
    experience: dict,
    key: str,
    pattern_id: str,
    recurrence: dict,
) -> None:
    efficacy = experience.setdefault("_efficacy_tracker", {})
    if pattern_id in efficacy:
        return
    all_clusters = store.sort_clusters(recurrence.get("clusters", []))
    baseline_clusters = all_clusters[:RECUR_THRESHOLD]
    baseline_count = len(baseline_clusters)
    efficacy[pattern_id] = {
        "key": key,
        "baseline_count": baseline_count,
        "baseline_clusters": baseline_clusters,
        "baseline_rate": round(recur_rate(baseline_count, baseline_clusters), 4),
        "escalated_at": store.now_text(),
        "status": "monitoring",
    }


def evaluate_efficacy(experience: dict, observed_clusters=None) -> list[dict]:
    observed = set(store.sort_clusters(
        observed_clusters if observed_clusters is not None else experience["_observed_clusters"]
    ))
    recurrence_tracker = experience.get("_recurrence_tracker", {})
    patterns = {
        item.get("id"): item
        for item in experience.get("failure_patterns", [])
        if isinstance(item, dict)
    }
    newly_ineffective = []
    for pattern_id, efficacy in experience.get("_efficacy_tracker", {}).items():
        if efficacy.get("status") in ("effective", "ineffective"):
            continue
        recurrence = recurrence_tracker.get(efficacy.get("key"))
        if not recurrence:
            continue
        baseline = set(efficacy.get("baseline_clusters", []))
        boundary = max((store.cluster_number(value) for value in baseline), default=-1)
        post_recur_clusters = store.sort_clusters(
            value for value in recurrence.get("clusters", [])
            if store.cluster_number(value) > boundary
        )
        post_observed = sum(1 for value in observed if store.cluster_number(value) > boundary)
        efficacy["post_recur_clusters"] = post_recur_clusters
        efficacy["post_observed"] = post_observed
        if post_observed < EFFICACY_MIN_POST_CLUSTERS:
            continue
        post_rate = round(len(post_recur_clusters) / post_observed, 4)
        efficacy["post_rate"] = post_rate
        efficacy["evaluated_at"] = store.now_text()
        baseline_rate = float(efficacy.get("baseline_rate", 0.0))
        threshold = baseline_rate * (1.0 - EFFICACY_IMPROVE_TOLERANCE)
        if post_rate < threshold or (post_rate == 0.0 and baseline_rate > 0):
            efficacy["status"] = "effective"
            continue
        efficacy["status"] = "ineffective"
        efficacy["stopped_at"] = store.now_text()
        pattern = patterns.get(pattern_id)
        if isinstance(pattern, dict):
            pattern["active"] = False
            pattern["efficacy"] = {
                "verdict": "ineffective",
                "baseline_rate": baseline_rate,
                "post_rate": post_rate,
                "post_recur_clusters": post_recur_clusters,
                "note": "约束注入后的复发率未下降，已停止向下一 cluster 注入；可人工复核。",
                "stopped_at": efficacy["stopped_at"],
            }
        newly_ineffective.append({
            "pattern_id": pattern_id,
            "key": efficacy.get("key"),
            "baseline_rate": baseline_rate,
            "post_rate": post_rate,
            "post_recur_clusters": post_recur_clusters,
        })
    return newly_ineffective


def escalate_recurring(experience: dict, only_keys=None) -> list[dict]:
    patterns = experience["failure_patterns"]
    escalated = []
    for key, record in experience["_recurrence_tracker"].items():
        if only_keys is not None and key not in only_keys:
            continue
        if record["count"] < RECUR_THRESHOLD:
            continue
        consecutive = store.has_consecutive_clusters(record["clusters"], CONSECUTIVE_ESCALATE)
        confidence = 0.95 if consecutive else 0.8
        pattern_id = f"recur_{key.replace('::', '_').replace(' ', '')}"
        dimension, code = key.split("::", 1)
        previous = next((item for item in patterns if item.get("id") == pattern_id), None)
        entry = {
            "id": pattern_id,
            "category": "failure",
            "trigger": f"{dimension}维度 [{code}] 反复出现",
            "technique": f"问题样本：{record.get('sample_desc', '')[:60]}",
            "why_works": (
                f"该问题在 cluster {record['clusters']} "
                f"{'连续' if consecutive else '累计'}出现 {record['count']} 次，"
                "下一 cluster writer 应主动规避"
            ),
            "confidence": confidence,
            "source_clusters": list(record["clusters"]),
            "scene_types": [],
            "recurrence": record["count"],
            "severity": "约束升级" if consecutive else "高频警示",
            "_recurrence": record["count"],
        }
        if isinstance(previous, dict) and previous.get("active") is False:
            entry["active"] = False
            if previous.get("efficacy"):
                entry["efficacy"] = previous["efficacy"]
        record_efficacy_baseline(experience, key, pattern_id, record)
        patterns[:] = [item for item in patterns if item.get("id") != pattern_id]
        store.stamp_updated(entry)
        patterns.append(entry)
        escalated.append(entry)
    return escalated


def _update_recurrence(
    tracker: dict,
    issues: list,
    cluster_id: str,
) -> set[str]:
    seen_keys = set()
    for issue in issues:
        if not isinstance(issue, dict):
            raise store.ExperienceContractError("audit issues 项必须是 object")
        severity = (issue.get("severity") or "warning").lower()
        if severity not in ("fatal", "error", "warning") or issue.get("waived") is True:
            continue
        key = issue_key(issue)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        record = tracker.setdefault(key, {
            "count": 0,
            "clusters": [],
            "first_seen": cluster_id,
            "last_seen": cluster_id,
            "dimension": issue.get("dimension", "unknown"),
            "sample_desc": issue.get("desc", ""),
        })
        if cluster_id not in record["clusters"]:
            record["clusters"] = store.sort_clusters(record["clusters"] + [cluster_id])
            record["count"] += 1
        record["first_seen"] = record["clusters"][0]
        record["last_seen"] = record["clusters"][-1]
    return seen_keys


def ingest_audit(project_root: Path, audit_path: Path) -> dict:
    audit = _read_json(audit_path, "cluster audit")
    cluster_id = audit_cluster_id(audit)
    issues = audit.get("issues")
    pending = audit.get("pending_agent")
    if not isinstance(issues, list) or not isinstance(pending, list):
        raise store.ExperienceContractError("cluster audit 必须包含 issues/pending_agent list")

    experience = store.load_experience(project_root)
    seen_keys = _update_recurrence(
        experience["_recurrence_tracker"], issues + pending, cluster_id
    )
    experience["_observed_clusters"] = store.sort_clusters(
        experience["_observed_clusters"] + [cluster_id]
    )
    escalated = escalate_recurring(experience, only_keys=seen_keys)
    waived_codes = track_waivers(experience, project_root, audit, cluster_id)
    calibration = build_calibration_suggestions(experience, only_codes=waived_codes)
    ineffective = evaluate_efficacy(experience)
    store.save_experience(project_root, experience)

    print(
        f"[ingest] {cluster_id} audit 已吸收，{len(seen_keys)} 类问题入复发追踪，"
        f"{len(waived_codes)} 类豁免入校准追踪"
    )
    return {
        "escalated": escalated,
        "calibration": calibration,
        "ineffective": ineffective,
        "cluster_id": cluster_id,
    }


def _validate_report_filename(path: Path, cluster_id: str) -> None:
    if path.name != f"{cluster_id}_audit.json":
        raise store.ExperienceContractError(f"非法 cluster audit 文件名: {path.name}")


def scan_recurring(project_root: Path, reflect_callback=None) -> dict:
    audit_dir = store.db_dir(project_root) / store.AUDIT_DIR
    experience = store.load_experience(project_root)
    experience["_recurrence_tracker"] = {}
    experience["_waiver_tracker"] = {}
    experience["_waiver_audit_ledger"] = {}
    experience["_observed_clusters"] = []
    tracker = experience["_recurrence_tracker"]
    report_files = list(audit_dir.glob("cluster_*_audit.json")) if audit_dir.is_dir() else []
    report_files.sort(key=lambda path: store.cluster_number(
        path.name.removesuffix("_audit.json")
    ))

    meta_signals: dict[str, dict] = {}
    seen_cluster_ids = set()
    for report_path in report_files:
        audit = _read_json(report_path, "cluster audit")
        cluster_id = audit_cluster_id(audit)
        _validate_report_filename(report_path, cluster_id)
        if cluster_id in seen_cluster_ids:
            raise store.ExperienceContractError(f"重复 cluster audit: {cluster_id}")
        seen_cluster_ids.add(cluster_id)
        issues = audit.get("issues")
        pending = audit.get("pending_agent")
        if not isinstance(issues, list) or not isinstance(pending, list):
            raise store.ExperienceContractError("cluster audit 必须包含 issues/pending_agent list")
        track_waivers(experience, project_root, audit, cluster_id)
        _update_recurrence(tracker, issues + pending, cluster_id)
        for issue in issues + pending:
            if issue.get("meta_suspect"):
                key = issue_key(issue)
                signal = meta_signals.setdefault(key, {"clusters": [], "desc": issue.get("desc", "")})
                signal["clusters"] = store.sort_clusters(signal["clusters"] + [cluster_id])

    experience["_observed_clusters"] = store.sort_clusters(seen_cluster_ids)
    meta_problems = []
    data_gap_hints = ("未声明", "未在", "NOT_PAID", "MISSING", "UNDECLARED", "_arc")
    observed = set(experience["_observed_clusters"])
    for key, record in tracker.items():
        if record["count"] < 3 or set(record["clusters"]) != observed:
            continue
        sample = record.get("sample_desc", "")
        if any(hint in key or hint in sample for hint in data_gap_hints):
            continue
        meta_problems.append({
            "key": key,
            "dimension": record.get("dimension"),
            "clusters": list(record["clusters"]),
            "count": record["count"],
            "confidence": "candidate",
            "reason": "全部已观察 cluster 命中同类问题，建议人工复核检测规则。",
            "sample": sample,
        })
    for key, signal in meta_signals.items():
        meta_problems.append({
            "key": key,
            "clusters": signal["clusters"],
            "confidence": "high",
            "reason": "audit_hub 标记 meta_suspect，建议复核检测器。",
            "sample": signal["desc"],
        })

    live_ids = {f"recur_{key.replace('::', '_').replace(' ', '')}" for key in tracker}
    patterns = experience["failure_patterns"]
    stale = [
        item for item in patterns
        if str(item.get("id", "")).startswith("recur_") and item.get("id") not in live_ids
    ]
    if stale:
        patterns[:] = [item for item in patterns if item not in stale]
    stale_efficacy = [
        pattern_id for pattern_id in experience["_efficacy_tracker"]
        if pattern_id not in live_ids
    ]
    for pattern_id in stale_efficacy:
        del experience["_efficacy_tracker"][pattern_id]

    escalated = escalate_recurring(experience)
    experience["tool_calibration_suggestions"] = []
    calibration = build_calibration_suggestions(experience)
    ineffective = evaluate_efficacy(experience)
    experience["_meta_problems"] = meta_problems
    time_prune = store.prune_and_decay(experience)
    store.save_experience(project_root, experience)

    print(
        f"[scan-recurring] 扫描 {len(report_files)} 份 cluster audit，"
        f"{len(tracker)} 类问题、{len(experience['_waiver_tracker'])} 类豁免进入追踪"
    )
    if stale:
        print(f"  清理 {len(stale)} 条已不复现的 recur_* 约束")
    if escalated:
        print(f"WARN {len(escalated)} 类问题命中 cluster 复发阈值")
    if calibration:
        print(f"CALIB {len(calibration)} 类检测项反复被合理豁免")
    if ineffective:
        print(f"EFFICACY {len(ineffective)} 条约束已停止向下一 cluster 注入")

    skill_rewrite = reflect_callback(project_root) if reflect_callback is not None else []
    return {
        "escalated": escalated,
        "meta_problems": meta_problems,
        "calibration": calibration,
        "ineffective": ineffective,
        "time_prune": time_prune,
        "skill_rewrite": skill_rewrite,
    }
