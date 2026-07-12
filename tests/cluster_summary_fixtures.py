"""严格 cluster 摘要合同的测试夹具。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


_DEFAULT_RECORD = {
    "cluster_id": "cluster_001",
    "title": "测试故事块",
    "summary": "测试摘要",
    "scene_summaries": [],
    "key_details": [],
    "emotion": {},
    "anchor_delivery": {},
    "word_count": 0,
    "text_keyword_set": [],
    "pattern_metrics": {},
    "idiom_hits": {},
    "characters": [],
    "char_mention_counts": {},
    "char_emotion_counts": {},
    "locations_mentioned": [],
    "ending_type": "",
    "ending_line": "",
    "time_transition_present": False,
    "structure": {
        "narrative_mode": "",
        "scene_count": 0,
        "summary_scene_count": 0,
        "appraisal_beats": [],
        "beats_declared": [],
        "beats_addressed": [],
        "beat_signal_hit": False,
    },
    "throughline_progress": {},
    "stress": {},
    "moves_used": [],
    "position_effect_evals": [],
    "outcome": "",
    "offscreen": {},
    "state_delta": {},
    "relationship_changes": [],
    "item_changes": [],
    "locked_facts": [],
    "audit": {},
    "truth_check": {},
    "judge_reports": [],
    "judge_score": None,
    "judge_grade": None,
    "waivers": [],
}


def cluster_record(cluster_id: str = "cluster_001", **overrides) -> dict:
    record = deepcopy(_DEFAULT_RECORD)
    record["cluster_id"] = cluster_id
    record.update(overrides)
    return record


def write_cluster_summary(project_root: Path, clusters: list[dict]) -> Path:
    path = Path(project_root) / "_数据库" / "故事块摘要.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": "v2.cluster",
            "clusters": clusters,
            "volume_summaries": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
