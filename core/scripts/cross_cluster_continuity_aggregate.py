"""检查相邻故事块的悬念、时间、物件与情绪连续性。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr
from atomic_json import load_json
from continuity_keywords import extract_keywords


SCENE_WINDOW_CHARS = 600
TIME_KEYWORDS = {
    "周一": 1, "周二": 2, "周三": 3, "周四": 4,
    "周五": 5, "周六": 6, "周日": 7,
}
TRANSITION_KEYWORDS = ("过渡", "周末", "回忆", "醒来", "睡了", "翌日", "后来")


def _read_json(path: Path, default=None):
    return load_json(path, default=default)


def get_protagonist(project_root: Path) -> str | None:
    """读取 canonical 人物卡中的主角名称。"""
    cards = _read_json(project_root / "_数据库" / "人物卡.json", {}) or {}
    characters = cards.get("characters")
    if not isinstance(characters, list):
        return None
    for character in characters:
        if isinstance(character, dict) and (
            character.get("role") in ("主角", "protagonist")
            or character.get("is_protagonist") is True
        ):
            name = character.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def _cluster_draft_path(project_root: Path, cluster_id: str) -> Path:
    key = cluster_id.removeprefix("cluster_")
    return project_root / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt"


def _drafts(project_root: Path, clusters: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for record in clusters:
        cluster_id = str(record["cluster_id"])
        path = _cluster_draft_path(project_root, cluster_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise FileNotFoundError(f"cluster 终稿不存在: {path}") from exc
        if not text.strip():
            raise ValueError(f"cluster 终稿为空: {path}")
        result[cluster_id] = text
    return result


def scan_cliffhanger_resonance(
    previous: dict,
    next_draft: str,
    protagonist: str | None = None,
) -> dict:
    ending_type = str(previous.get("ending_type") or "")
    ending_line = str(previous.get("ending_line") or "")
    if ending_type == "悬念断章":
        return {"score": 1.0, "exempt": True, "source": "summary+draft"}
    if not ending_line:
        return {"score": -1.0, "source": "summary+draft", "reason": "ending_line 缺失"}
    ending_keywords = extract_keywords(ending_line + " " + ending_type, protagonist=protagonist)
    next_keywords = extract_keywords(next_draft[:SCENE_WINDOW_CHARS], protagonist=protagonist)
    if not ending_keywords:
        return {"score": -1.0, "source": "summary+draft", "reason": "ending_line 无有效关键词"}
    overlap = ending_keywords & next_keywords
    return {
        "score": round(len(overlap) / len(ending_keywords), 2),
        "ending_type": ending_type,
        "ending_line_preview": ending_line[:80],
        "overlap_keywords": sorted(overlap),
        "source": "summary+draft",
    }


def _time_marker(value: object) -> tuple[str, int] | None:
    text = str(value or "")
    for marker, number in TIME_KEYWORDS.items():
        if marker in text:
            return ("weekday", number)
    match = re.search(r"第\s*(\d+)\s*天", text)
    if match:
        return ("day", int(match.group(1)))
    return None


def scan_time_gap(previous_state: dict, next_state: dict) -> dict:
    previous_time = (previous_state or {}).get("time_advance") or {}
    next_time = (next_state or {}).get("time_advance") or {}
    if not isinstance(previous_time, dict) or not isinstance(next_time, dict):
        return {"detected": False}
    for key in ("gap_days", "elapsed_days", "days_elapsed"):
        value = next_time.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 2:
            return {"detected": True, "gap_days": value}
    previous_end = previous_time.get("period") or ((previous_time.get("key_events") or [""])[-1])
    next_start = next_time.get("period") or ((next_time.get("key_events") or [""])[0])
    previous_marker = _time_marker(previous_end)
    next_marker = _time_marker(next_start)
    if previous_marker and next_marker and previous_marker[0] == next_marker[0]:
        gap = (next_marker[1] - previous_marker[1]) % (7 if previous_marker[0] == "weekday" else 10**9)
        if gap >= 2:
            return {
                "detected": True,
                "gap_days": gap,
                "previous_end": str(previous_end)[:60],
                "next_start": str(next_start)[:60],
            }
    return {"detected": False, "previous_end": str(previous_end)[:60],
            "next_start": str(next_start)[:60]}


def check_time_transition(next_record: dict, next_draft: str) -> dict:
    if next_record.get("time_transition_present") is True:
        return {"has_transition": True, "source": "summary"}
    return {
        "has_transition": any(word in next_draft[:SCENE_WINDOW_CHARS] for word in TRANSITION_KEYWORDS),
        "source": "draft",
    }


def _build_aliases(name: str) -> list[str]:
    aliases = {name}
    match = re.search(r"[（(]([^）)]+)[）)]", name)
    if match:
        aliases.add(match.group(1))
    core = re.sub(r"[（(].+?[）)]", "", name).strip()
    if core:
        aliases.add(core)
    if len(name) >= 4:
        aliases.update((name[:3], name[-3:]))
    return sorted(alias for alias in aliases if alias)


def _item_identity(item: dict) -> tuple[str, list[str]] | None:
    item_id = item.get("id")
    name = item.get("name")
    if not isinstance(item_id, str) or not isinstance(name, str) or not name:
        return None
    return item_id, _build_aliases(name)


def scan_object_continuity(clusters: list[dict], drafts: dict[str, str]) -> list[dict]:
    known: dict[str, dict] = {}
    for index, record in enumerate(clusters):
        cluster_id = str(record["cluster_id"])
        text = drafts.get(cluster_id, "")
        for item in record.get("item_changes") or []:
            if not isinstance(item, dict):
                continue
            identity = _item_identity(item)
            if identity and identity[0] not in known:
                known[identity[0]] = {
                    "name": item["name"],
                    "aliases": identity[1],
                    "first_index": index,
                    "last_index": index,
                    "last_cluster_id": cluster_id,
                }
        for info in known.values():
            if any(alias in text for alias in info["aliases"]):
                info["last_index"] = index
                info["last_cluster_id"] = cluster_id

    current_index = len(clusters) - 1
    findings = []
    for info in known.values():
        gap = current_index - info["last_index"]
        if gap >= 2 and info["first_index"] <= current_index - 2:
            findings.append({
                "item": info["name"],
                "first_cluster": clusters[info["first_index"]]["cluster_id"],
                "last_seen_cluster": info["last_cluster_id"],
                "gap_clusters": gap,
                "source": "cluster_draft",
            })
    return findings


def _emotion_value(emotion: object) -> float | None:
    if not isinstance(emotion, dict):
        return None
    for key in ("value", "valence", "score", "intensity"):
        value = emotion.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def scan_emotion_gap(previous: dict, current: dict) -> dict:
    first = _emotion_value(previous.get("emotion"))
    second = _emotion_value(current.get("emotion"))
    if first is None or second is None:
        return {"detected": False}
    difference = abs(first - second)
    return {
        "detected": difference >= 4,
        "previous_emotion": first,
        "current_emotion": second,
        "diff": difference,
    }


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    clusters = csr.get_clusters(project_root, last_n=last_n)
    if not clusters:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    drafts = _drafts(project_root, clusters)
    protagonist = get_protagonist(project_root)
    findings: list[dict] = []
    pairwise: list[dict] = []

    for previous, current in zip(clusters, clusters[1:]):
        previous_id = str(previous["cluster_id"])
        current_id = str(current["cluster_id"])
        cliffhanger = scan_cliffhanger_resonance(previous, drafts[current_id], protagonist)
        if not cliffhanger.get("exempt") and 0 <= cliffhanger.get("score", -1) < 0.2:
            findings.append({
                "dimension": "cliffhanger",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "CLIFFHANGER_NOT_RESONATED",
                "from_cluster": previous_id,
                "to_cluster": current_id,
                "metric": cliffhanger,
            })

        time_gap = scan_time_gap(previous.get("state_delta") or {}, current.get("state_delta") or {})
        transition = check_time_transition(current, drafts[current_id])
        if time_gap.get("detected") and not transition["has_transition"]:
            findings.append({
                "dimension": "time_gap",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "TIME_JUMP_UNEXPLAINED",
                "from_cluster": previous_id,
                "to_cluster": current_id,
                "metric": {**time_gap, "transition_source": transition["source"]},
            })

        emotion = scan_emotion_gap(previous, current)
        if emotion.get("detected"):
            findings.append({
                "dimension": "emotion",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "EMOTION_DISCONTINUITY",
                "from_cluster": previous_id,
                "to_cluster": current_id,
                "metric": emotion,
            })

        pairwise.append({
            "from_cluster": previous_id,
            "to_cluster": current_id,
            "cliffhanger_score": cliffhanger.get("score", -1),
            "cliffhanger_exempt": bool(cliffhanger.get("exempt")),
            "time_gap_days": time_gap.get("gap_days", 0),
            "time_transition": transition["has_transition"],
            "emotion_diff": emotion.get("diff", 0),
        })

    for item in scan_object_continuity(clusters, drafts):
        severity = "warning" if item["gap_clusters"] >= 5 else "advisory"
        findings.append({
            "dimension": "object_continuity",
            "severity": severity,
            "gate_level": "advisory",
            "code": "OBJECT_CONTINUITY_BROKEN",
            "metric": item,
        })

    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "continuity",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_scanned": [str(record["cluster_id"]) for record in clusters],
        "pairwise": pairwise,
        "findings": findings,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=None)
    args = parser.parse_args()
    try:
        report = build_report(Path(args.project), last_n=args.last_n)
        out_dir = Path(args.project) / "_数据库" / ".cross_cluster_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"continuity_{stamp}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[continuity] clusters={len(report['clusters_scanned'])} "
              f"warning={report['summary']['warning']} advisory={report['summary']['advisory']}")
        print(f"报告: {out_path}")
        return 1 if report["summary"]["warning"] else 0
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
