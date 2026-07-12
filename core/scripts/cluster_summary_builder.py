"""把一个故事块的必需产物汇总成唯一 cluster 摘要记录。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chapter_io as cio
import cluster_lookup
import cluster_summary_store
import plot_structure_scanner as structure_scan

try:
    import cross_cluster_pattern_aggregate as pattern_scan
except Exception:  # pragma: no cover - 依赖不可导入时由空遥测显式呈现
    pattern_scan = None

try:
    from cross_cluster_emotion_pattern_aggregate import detect_emotions_for_char
except Exception:  # pragma: no cover
    detect_emotions_for_char = None


class ClusterSummaryBuildError(ValueError):
    """构建摘要所需的必需 cluster 产物不合格。"""


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_TIME_TRANSITIONS = (
    "第二天", "次日", "翌日", "清晨", "上午", "中午", "下午", "傍晚", "深夜",
    "几分钟后", "几小时后", "片刻后", "不久后", "与此同时", "同一时间",
)
_STOPWORDS = {
    "他们", "她们", "我们", "你们", "自己", "一个", "这个", "那个", "什么",
    "没有", "已经", "还是", "只是", "然后", "但是", "因为", "所以", "如果",
}


def _canonical_cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise ClusterSummaryBuildError(f"非法 cluster_id: {value!r}")
    return cluster_id


def _read_utf8(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ClusterSummaryBuildError(f"必需产物不存在: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ClusterSummaryBuildError(f"必需产物必须是 UTF-8 无 BOM: {path}")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ClusterSummaryBuildError(f"必需产物不是合法 UTF-8: {path}: {exc}") from exc


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(_read_utf8(path))
    except json.JSONDecodeError as exc:
        raise ClusterSummaryBuildError(f"必需 JSON 损坏: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ClusterSummaryBuildError(f"必需 JSON 顶层必须是 object: {path}")
    return value


def _require_cluster_match(document: dict, cluster_id: str, label: str) -> None:
    actual = _canonical_cluster_id(document.get("cluster_id"))
    if actual != cluster_id:
        raise ClusterSummaryBuildError(
            f"{label}.cluster_id={actual!r} 与目标 {cluster_id!r} 不一致"
        )


def _draft_paths(project: Path, cluster_id: str) -> tuple[Path, Path]:
    directory = project / "章节" / f"{cluster_id}_draft"
    return directory / f"{cluster_id}_draft.txt", directory / f"{cluster_id}_changes.json"


def _artifact_paths(project: Path, cluster_id: str) -> dict[str, Path]:
    database = project / "_数据库"
    return {
        "summary": database / ".wal" / f"{cluster_id}_summary.json",
        "audit": database / ".audit" / f"{cluster_id}_audit.json",
        "archive": database / ".wal" / f"{cluster_id}_archive.json",
        "state_delta": database / ".wal" / f"{cluster_id}_state_delta.json",
        "entity_stats": database / ".wal" / f"{cluster_id}_entity_stats.json",
        "truth": database / ".judge_reports" / f"{cluster_id}_writer-truth-check.json",
        "judge_rollup": database / ".wal" / f"{cluster_id}_judge_reports_rollup.json",
    }


def _event_cluster(database: Path, cluster_id: str) -> dict:
    document = _read_json(database / "事件簇.json")
    clusters = document.get("clusters")
    if not isinstance(clusters, list):
        raise ClusterSummaryBuildError("事件簇.clusters 必须是数组")
    for record in clusters:
        if not isinstance(record, dict):
            continue
        if cluster_lookup.normalize_cluster_id(record.get("cluster_id")) == cluster_id:
            return record
    raise ClusterSummaryBuildError(f"事件簇.json 中不存在 {cluster_id}")


def _ending_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:160] if lines else ""


def _extract_keywords(text: str, limit: int = 12) -> list[str]:
    compact = "".join(_CJK_RE.findall(text))
    counts = Counter()
    for size in (4, 3, 2):
        for index in range(max(0, len(compact) - size + 1)):
            token = compact[index:index + size]
            if token not in _STOPWORDS and len(set(token)) > 1:
                counts[token] += 1
    return [token for token, count in counts.most_common() if count >= 2][:limit]


def _pattern_metrics(text: str, project: Path) -> dict:
    if pattern_scan is None:
        return {}
    try:
        protagonist = pattern_scan.get_protagonist(project, None)
        catchphrases = pattern_scan.get_catchphrases(project, protagonist)
        cliche_dict = pattern_scan._load_cliche_dict(project)
    except Exception:
        return {}
    metrics = {"wc": len(text)}
    calls = {
        "catchphrase": lambda: pattern_scan.scan_catchphrase(text, catchphrases),
        "cliche_hits": lambda: pattern_scan.scan_cliche_ai_cn(text, cliche_dict),
        "para_protagonist_start": lambda: pattern_scan.scan_paragraph_starts_with_protagonist(text, protagonist),
        "dialogue_tag": lambda: pattern_scan.scan_dialogue_tags(text),
        "body_reaction": lambda: pattern_scan.scan_body_reactions(text),
        "negation_desc": lambda: pattern_scan.scan_negation_descriptions(text),
        "para_first_word_top1_pct": lambda: round(pattern_scan.scan_para_first_word_concentration(text), 3),
        "particle_dist": lambda: pattern_scan.scan_sentence_particle_distribution(text),
        "pronoun_action": lambda: pattern_scan.scan_pronoun_action(text),
        "punctuation": lambda: pattern_scan.scan_punctuation_halfwidth(text),
        "name_density_per_100": lambda: round(pattern_scan.scan_protagonist_name_density(text, protagonist), 2),
        "warmup_hits": lambda: pattern_scan.scan_opening_warmup(text),
        "time_anchor_drops": lambda: pattern_scan.scan_time_anchor_drops(text),
        "modifier_stack_sentences": lambda: pattern_scan.scan_modifier_stack(text),
        "sensory_dist": lambda: pattern_scan.scan_sensory_distribution(text),
        "dialogue_stream_max": lambda: pattern_scan.scan_dialogue_stream_flat(text),
        "paragraph_length_trend": lambda: pattern_scan.scan_paragraph_length_trend(text),
    }
    for field, producer in calls.items():
        try:
            metrics[field] = producer()
        except Exception:
            continue
    for prefix, producer in (
        ("tell", pattern_scan.scan_tell_overuse),
        ("metaphor", pattern_scan.scan_metaphor_overuse),
        ("causal", pattern_scan.scan_causal_heuristic),
    ):
        try:
            count, rate = producer(text)
            metrics[f"{prefix}_count"] = count
            metrics[f"{prefix}_per_1k"] = round(rate, 2)
        except Exception:
            continue
    return metrics


def _idiom_hits(text: str) -> dict:
    if pattern_scan is None:
        return {}
    try:
        return {
            idiom: text.count(idiom)
            for idiom in pattern_scan.IDIOM_COOLDOWN_DICT
            if text.count(idiom)
        }
    except Exception:
        return {}


def _characters(entity_stats: dict, archive: dict) -> tuple[list[str], dict[str, int]]:
    names = []
    mention_counts = {}
    for row in entity_stats.get("known_entities", []):
        if not isinstance(row, dict) or not row.get("appears"):
            continue
        name = row.get("name")
        count = row.get("mention_count")
        if isinstance(name, str) and name:
            names.append(name)
            if isinstance(count, int) and not isinstance(count, bool):
                mention_counts[name] = count
    for row in archive.get("characters", []):
        if isinstance(row, dict):
            name = row.get("name") or row.get("id")
            if isinstance(name, str) and name:
                names.append(name)
    return list(dict.fromkeys(names)), mention_counts


def _character_emotions(text: str, names: list[str]) -> dict:
    if detect_emotions_for_char is None:
        return {}
    result = {}
    for name in names:
        try:
            counts = dict(detect_emotions_for_char(text, name))
        except Exception:
            continue
        if counts:
            result[name] = counts
    return result


def _locations(text: str, database: Path, state_delta: dict) -> list[str]:
    map_data = _read_json(database / "地图.json")
    mentioned = []
    for row in map_data.get("locations", []):
        if not isinstance(row, dict):
            continue
        location_id = row.get("id")
        name = row.get("name")
        if isinstance(name, str) and name and name in text:
            mentioned.append(location_id or name)
    for row in state_delta.get("location_changes", []):
        if isinstance(row, dict) and isinstance(row.get("location_id"), str):
            mentioned.append(row["location_id"])
    return list(dict.fromkeys(mentioned))


def _stress(database: Path, cluster_id: str) -> dict:
    document = _read_json(database / "主角压力档.json")
    for entry in document.get("stress_log", []):
        if isinstance(entry, dict) and entry.get("cluster_id") == cluster_id:
            return entry
    return {}


def _audit_rollup(audit: dict) -> dict:
    issues = []
    for issue in audit.get("issues", []):
        if isinstance(issue, dict):
            issues.append({
                key: issue.get(key)
                for key in ("code", "severity", "gate_level", "dimension")
                if issue.get(key) is not None
            })
    rollup = {
        "verdict": audit.get("verdict"),
        "summary": audit.get("summary") if isinstance(audit.get("summary"), dict) else {},
        "issues": issues,
        "scanner_status": audit.get("scanner_status")
        if isinstance(audit.get("scanner_status"), list) else [],
        "waiver_audit": audit.get("waiver_audit")
        if isinstance(audit.get("waiver_audit"), dict) else {},
    }
    if isinstance(audit.get("persona_drift"), dict):
        rollup["persona_drift"] = audit["persona_drift"]
    return rollup


def _truth_rollup(truth: dict) -> dict:
    return {
        "verdict": truth.get("verdict"),
        "lie_count": truth.get("lie_count", 0),
        "lies_detected": truth.get("lies_detected", []),
        "detected_opening_type": truth.get("detected_opening_type"),
        "declared_ending_type": truth.get("declared_ending_type"),
        "detected_ending_type": truth.get("detected_ending_type"),
        "ending_type_match": truth.get("ending_type_match"),
        "body_cjk_count": truth.get("body_cjk_count"),
    }


def _dedupe_waivers(*groups) -> list[dict]:
    output = []
    seen = set()
    for group in groups:
        for waiver in group if isinstance(group, list) else []:
            if not isinstance(waiver, dict):
                continue
            key = (str(waiver.get("code", "")), str(waiver.get("reason", "")))
            if key in seen:
                continue
            seen.add(key)
            output.append(waiver)
    return output


def build_cluster_record(project_root, cluster) -> dict:
    project = Path(project_root)
    cluster_id = _canonical_cluster_id(cluster)
    database = project / "_数据库"
    draft_path, changes_path = _draft_paths(project, cluster_id)
    paths = _artifact_paths(project, cluster_id)

    draft = _read_utf8(draft_path)
    if not draft.strip():
        raise ClusterSummaryBuildError(f"cluster 正文为空: {draft_path}")
    changes = _read_json(changes_path)
    summary = _read_json(paths["summary"])
    audit = _read_json(paths["audit"])
    archive = _read_json(paths["archive"])
    state_delta = _read_json(paths["state_delta"])
    entity_stats = _read_json(paths["entity_stats"])
    truth = _read_json(paths["truth"])
    judge_rollup = _read_json(paths["judge_rollup"])
    event = _event_cluster(database, cluster_id)
    for label, document in (
        ("summary", summary), ("audit", audit), ("archive", archive), ("state_delta", state_delta),
        ("entity_stats", entity_stats), ("truth", truth),
        ("judge_rollup", judge_rollup),
    ):
        _require_cluster_match(document, cluster_id, label)

    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        raise ClusterSummaryBuildError("cluster changes.self_eval 必须是 object")
    title = summary.get("title")
    summary_text = summary.get("summary")
    scene_summaries = summary.get("scene_summaries")
    key_details = summary.get("key_details")
    emotion = summary.get("emotion")
    anchor_delivery = summary.get("anchor_delivery")
    if not isinstance(title, str) or not title.strip():
        raise ClusterSummaryBuildError("cluster summary.title 必须是非空字符串")
    if not isinstance(summary_text, str) or not summary_text.strip():
        raise ClusterSummaryBuildError("cluster summary.summary 必须是非空字符串")
    for label, value, expected in (
        ("scene_summaries", scene_summaries, list),
        ("key_details", key_details, list),
        ("emotion", emotion, dict),
        ("anchor_delivery", anchor_delivery, dict),
    ):
        if not isinstance(value, expected):
            raise ClusterSummaryBuildError(f"cluster summary.{label} 类型不正确")

    characters, mention_counts = _characters(entity_stats, archive)
    applied_style = self_eval.get("applied_style")
    applied_style = applied_style if isinstance(applied_style, dict) else {}
    storyteller = self_eval.get("storyteller_alignment")
    storyteller = storyteller if isinstance(storyteller, dict) else {}
    archive_throughline = archive.get("throughline_progress")
    event_throughline = event.get("throughline_progress")
    throughline = archive_throughline if isinstance(archive_throughline, dict) else (
        event_throughline if isinstance(event_throughline, dict) else {}
    )
    time_advance = state_delta.get("time_advance")
    time_advance = time_advance if isinstance(time_advance, dict) else {}
    appraisal_beats = summary.get("appraisal_beats")
    appraisal_beats = appraisal_beats if isinstance(appraisal_beats, list) else []
    beat_evidence = structure_scan.scan_beat(project, cluster_id, draft)
    writer_waivers = self_eval.get("waivers")
    rollup_waivers = judge_rollup.get("waivers")

    record = {
        "cluster_id": cluster_id,
        "title": title.strip(),
        "summary": summary_text.strip(),
        "scene_summaries": scene_summaries,
        "key_details": key_details,
        "emotion": emotion,
        "anchor_delivery": anchor_delivery,
        "word_count": cio.count_cjk(draft),
        "text_keyword_set": _extract_keywords(draft),
        "pattern_metrics": _pattern_metrics(draft, project),
        "idiom_hits": _idiom_hits(draft),
        "characters": characters,
        "char_mention_counts": mention_counts,
        "char_emotion_counts": _character_emotions(draft, characters),
        "locations_mentioned": _locations(draft, database, state_delta),
        "ending_type": str(truth.get("detected_ending_type") or applied_style.get("ending_type") or ""),
        "ending_line": _ending_line(draft),
        "time_transition_present": bool(time_advance) or any(
            word in draft for word in _TIME_TRANSITIONS
        ),
        "structure": {
            "narrative_mode": event.get("narrative_mode") or "",
            "scene_count": len(event.get("scene_storyboard") or []),
            "summary_scene_count": len(scene_summaries),
            "appraisal_beats": appraisal_beats,
            "beats_declared": beat_evidence["beats_declared"],
            "beats_addressed": beat_evidence["beats_addressed"],
            "beat_signal_hit": beat_evidence["beat_signal_hit"],
        },
        "throughline_progress": throughline,
        "stress": _stress(database, cluster_id),
        "moves_used": self_eval.get("moves_used")
        if isinstance(self_eval.get("moves_used"), list) else [],
        "position_effect_evals": self_eval.get("position_effect_evals")
        if isinstance(self_eval.get("position_effect_evals"), list) else [],
        "outcome": str(storyteller.get("actual_outcome") or "neutral"),
        "offscreen": {
            "executed": self_eval.get("offscreen_actions_executed")
            if isinstance(self_eval.get("offscreen_actions_executed"), list) else []
        },
        "state_delta": state_delta,
        "relationship_changes": archive.get("relationships")
        if isinstance(archive.get("relationships"), list) else [],
        "item_changes": archive.get("items")
        if isinstance(archive.get("items"), list) else [],
        "locked_facts": archive.get("locked_facts")
        if isinstance(archive.get("locked_facts"), list) else [],
        "audit": _audit_rollup(audit),
        "truth_check": _truth_rollup(truth),
        "judge_reports": judge_rollup.get("reports")
        if isinstance(judge_rollup.get("reports"), list) else [],
        "judge_score": judge_rollup.get("judge_score"),
        "judge_grade": judge_rollup.get("judge_grade"),
        "waivers": _dedupe_waivers(writer_waivers, rollup_waivers),
    }
    return record


def build_cluster_summary(project_root, cluster) -> dict:
    project = Path(project_root)
    cluster_id = _canonical_cluster_id(cluster)
    record = build_cluster_record(project, cluster_id)
    cluster_summary_store.replace_cluster(project, cluster_id, record)
    return {
        "ok": True,
        "cluster_id": cluster_id,
        "title": record["title"],
        "word_count": record["word_count"],
        "cluster_rollup_fields": sum(
            value not in (None, "", [], {}) for value in record.values()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成严格的 cluster 摘要记录")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    try:
        result = build_cluster_summary(args.project, args.cluster)
    except (OSError, ClusterSummaryBuildError, ValueError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(
        f"[OK] {result['cluster_id']} 摘要已写入：{result['title']} · "
        f"{result['word_count']} CJK · {result['cluster_rollup_fields']} 个非空字段"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
