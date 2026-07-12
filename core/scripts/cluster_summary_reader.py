"""读取并校验唯一的 cluster 摘要账本。"""

from __future__ import annotations

import json
from pathlib import Path

SUMMARY_FILENAME = "故事块摘要.json"
SCHEMA_VERSION = "v2.cluster"

TOP_LEVEL_FIELDS = frozenset({"schema_version", "clusters", "volume_summaries"})
CLUSTER_FIELDS = frozenset({
    "cluster_id", "title", "summary", "scene_summaries", "key_details",
    "emotion", "anchor_delivery", "word_count", "text_keyword_set",
    "pattern_metrics", "idiom_hits", "characters", "char_mention_counts",
    "char_emotion_counts", "locations_mentioned", "ending_type", "ending_line",
    "time_transition_present", "structure", "throughline_progress", "stress",
    "moves_used", "outcome", "offscreen", "state_delta",
    "relationship_changes", "item_changes", "locked_facts", "audit", "truth_check",
    "judge_reports", "judge_score", "judge_grade", "waivers",
})
VOLUME_FIELDS = frozenset({
    "volume", "summary", "source", "generated_at_cluster", "emotional_peak",
    "key_turning_points", "applied_at",
})


class ClusterSummaryError(ValueError):
    """摘要账本违反当前 cluster 合同。"""


def _db_dir(project_root) -> Path:
    root = Path(project_root)
    return root if root.name == "_数据库" else root / "_数据库"


def summary_path(project_root) -> Path:
    return _db_dir(project_root) / SUMMARY_FILENAME


def _require_type(value, expected, where: str) -> None:
    if not isinstance(value, expected):
        label = getattr(expected, "__name__", str(expected))
        raise ClusterSummaryError(f"{where} 必须是 {label}")


def _cluster_num(cluster_id: str) -> int:
    if not isinstance(cluster_id, str):
        raise ClusterSummaryError(f"非法 cluster_id: {cluster_id!r}")
    if not cluster_id.startswith("cluster_"):
        raise ClusterSummaryError(f"非法 cluster_id: {cluster_id!r}")
    number = cluster_id.removeprefix("cluster_")
    if len(number) < 3 or not number.isascii() or not number.isdigit():
        raise ClusterSummaryError(f"非法 cluster_id: {cluster_id!r}")
    return int(number)


def _validate_cluster(record: dict, index: int) -> None:
    where = f"clusters[{index}]"
    _require_type(record, dict, where)
    missing = sorted(CLUSTER_FIELDS - set(record))
    extra = sorted(set(record) - CLUSTER_FIELDS)
    if missing:
        raise ClusterSummaryError(f"{where} 缺少字段: {missing}")
    if extra:
        raise ClusterSummaryError(f"{where} 含未知字段: {extra}")
    _cluster_num(record["cluster_id"])
    for field in ("title", "summary", "ending_type", "ending_line", "outcome"):
        _require_type(record[field], str, f"{where}.{field}")
    for field in (
        "scene_summaries", "key_details", "text_keyword_set", "characters",
        "locations_mentioned", "moves_used",
        "relationship_changes", "item_changes", "locked_facts", "judge_reports",
        "waivers",
    ):
        _require_type(record[field], list, f"{where}.{field}")
    for field in (
        "emotion", "anchor_delivery", "pattern_metrics", "idiom_hits",
        "char_mention_counts", "char_emotion_counts", "structure",
        "throughline_progress", "stress", "offscreen", "state_delta", "audit",
        "truth_check",
    ):
        _require_type(record[field], dict, f"{where}.{field}")
    if not isinstance(record["word_count"], int) or isinstance(record["word_count"], bool):
        raise ClusterSummaryError(f"{where}.word_count 必须是整数")
    if record["word_count"] < 0:
        raise ClusterSummaryError(f"{where}.word_count 不得小于 0")
    _require_type(record["time_transition_present"], bool,
                  f"{where}.time_transition_present")
    if record["judge_score"] is not None and (
        not isinstance(record["judge_score"], (int, float))
        or isinstance(record["judge_score"], bool)
    ):
        raise ClusterSummaryError(f"{where}.judge_score 必须是数值或 null")
    if record["judge_grade"] is not None and not isinstance(record["judge_grade"], str):
        raise ClusterSummaryError(f"{where}.judge_grade 必须是字符串或 null")


def _validate_volume(record: dict, index: int) -> None:
    where = f"volume_summaries[{index}]"
    _require_type(record, dict, where)
    missing = sorted({"volume", "summary", "source", "generated_at_cluster"} - set(record))
    extra = sorted(set(record) - VOLUME_FIELDS)
    if missing:
        raise ClusterSummaryError(f"{where} 缺少字段: {missing}")
    if extra:
        raise ClusterSummaryError(f"{where} 含未知字段: {extra}")
    if not isinstance(record["volume"], int) or isinstance(record["volume"], bool):
        raise ClusterSummaryError(f"{where}.volume 必须是整数")
    if record["volume"] < 1:
        raise ClusterSummaryError(f"{where}.volume 必须是正整数")
    _require_type(record["summary"], str, f"{where}.summary")
    if not record["summary"].strip():
        raise ClusterSummaryError(f"{where}.summary 不能为空")
    _require_type(record["source"], list, f"{where}.source")
    if not record["source"]:
        raise ClusterSummaryError(f"{where}.source 不能为空")
    _cluster_num(record["generated_at_cluster"])
    seen_sources = set()
    for source in record["source"]:
        _cluster_num(source)
        if source in seen_sources:
            raise ClusterSummaryError(f"{where}.source 不得重复")
        seen_sources.add(source)
    for field in ("emotional_peak", "applied_at"):
        if field in record:
            _require_type(record[field], str, f"{where}.{field}")
    if "key_turning_points" in record:
        _require_type(record["key_turning_points"], list,
                      f"{where}.key_turning_points")
        if any(not isinstance(item, str) for item in record["key_turning_points"]):
            raise ClusterSummaryError(f"{where}.key_turning_points 必须是字符串数组")


def validate_summary(data: dict) -> dict:
    _require_type(data, dict, "故事块摘要")
    missing = sorted(TOP_LEVEL_FIELDS - set(data))
    extra = sorted(set(data) - TOP_LEVEL_FIELDS)
    if missing:
        raise ClusterSummaryError(f"故事块摘要缺少字段: {missing}")
    if extra:
        raise ClusterSummaryError(f"故事块摘要含未知字段: {extra}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ClusterSummaryError(
            f"故事块摘要.schema_version 必须是 {SCHEMA_VERSION!r}，"
            f"实得 {data['schema_version']!r}"
        )
    _require_type(data["clusters"], list, "故事块摘要.clusters")
    _require_type(data["volume_summaries"], list, "故事块摘要.volume_summaries")
    seen_clusters = set()
    for index, record in enumerate(data["clusters"]):
        _validate_cluster(record, index)
        cluster_id = record["cluster_id"]
        if cluster_id in seen_clusters:
            raise ClusterSummaryError(f"cluster_id 重复: {cluster_id}")
        seen_clusters.add(cluster_id)
    seen_volumes = set()
    for index, record in enumerate(data["volume_summaries"]):
        _validate_volume(record, index)
        volume = record["volume"]
        if volume in seen_volumes:
            raise ClusterSummaryError(f"volume_summaries.volume 重复: {volume}")
        seen_volumes.add(volume)
    return data


def load_summary(project_root) -> dict:
    path = summary_path(project_root)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ClusterSummaryError(f"必需摘要账本不存在: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ClusterSummaryError(f"摘要账本必须是 UTF-8 无 BOM: {path}")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ClusterSummaryError(f"摘要账本不是合法 UTF-8: {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClusterSummaryError(f"摘要账本 JSON 损坏: {path}: {exc}") from exc
    return validate_summary(data)


def get_clusters(project_root, last_n: int | None = None) -> list[dict]:
    clusters = list(load_summary(project_root)["clusters"])
    clusters.sort(key=lambda record: _cluster_num(record["cluster_id"]))
    if last_n is not None:
        if not isinstance(last_n, int) or isinstance(last_n, bool) or last_n < 1:
            raise ClusterSummaryError("last_n 必须是正整数，且单位固定为 cluster")
        clusters = clusters[-last_n:]
    return clusters


def get_cluster(project_root, cluster_id: str) -> dict:
    number = _cluster_num(cluster_id)
    canonical = f"cluster_{number:03d}"
    for record in get_clusters(project_root):
        if record["cluster_id"] == canonical:
            return record
    raise ClusterSummaryError(f"摘要账本中不存在 {canonical}")


__all__ = [
    "CLUSTER_FIELDS", "ClusterSummaryError", "SCHEMA_VERSION", "SUMMARY_FILENAME",
    "get_cluster", "get_clusters", "load_summary", "summary_path", "validate_summary",
]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="校验并列出 cluster 摘要账本")
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int)
    args = parser.parse_args()
    for row in get_clusters(args.project, args.last_n):
        print(f"{row['cluster_id']}\t{row['title']}\t{row['word_count']} CJK")
