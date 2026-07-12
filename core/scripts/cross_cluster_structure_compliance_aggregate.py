"""检查故事块结构节拍与用户选择的落库一致性。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl
import cluster_summary_reader as csr
from beat_evidence import beat_keywords_for


DB = "_数据库"
EVENT_CLUSTER = "事件簇.json"
def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _declared_beats(record: dict) -> list[str]:
    structure = record.get("structure") or {}
    declared = structure.get("beats_declared") if isinstance(structure, dict) else None
    if not isinstance(declared, list):
        return []
    return [
        str(item).strip() for item in declared
        if isinstance(item, str) and item.strip()
    ]


def _addressed_beats(record: dict) -> tuple[list[str], bool | None]:
    structure = record.get("structure") or {}
    if not isinstance(structure, dict):
        return [], None
    addressed = structure.get("beats_addressed")
    if not isinstance(addressed, list):
        addressed = []
    signal = structure.get("beat_signal_hit")
    return [str(item) for item in addressed], signal if isinstance(signal, bool) else None


def scan_beat_progression(records: list[dict]) -> list[dict]:
    findings: list[dict] = []
    declared_clusters: list[str] = []
    for record in records:
        cluster_id = str(record["cluster_id"])
        beats = _declared_beats(record)
        if not beats:
            continue
        declared_clusters.append(cluster_id)
        addressed, _signal = _addressed_beats(record)
        for beat in beats:
            explicit = any(beat.lower() in item.lower() for item in addressed)
            if not explicit:
                findings.append({
                    "severity": "warning",
                    "gate_level": "advisory",
                    "code": "BEAT_MISSED",
                    "cluster_id": cluster_id,
                    "expected_beat": beat,
                    "expected_kws": beat_keywords_for(beat)[:3],
                })
    positions = {cluster_id: index for index, cluster_id in enumerate(
        str(record["cluster_id"]) for record in records
    )}
    for previous, current in zip(declared_clusters, declared_clusters[1:]):
        gap = positions[current] - positions[previous]
        if gap > 5:
            findings.append({
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "BEAT_GAP_TOO_LONG",
                "from_cluster": previous,
                "to_cluster": current,
                "gap_clusters": gap,
            })
    return findings


def _extract_choice_brief(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("answer"), dict):
        answer = payload["answer"]
        return answer.get("cluster_brief") if isinstance(answer.get("cluster_brief"), dict) else answer
    if isinstance(payload.get("cluster_brief"), dict):
        return payload["cluster_brief"]
    if any(key in payload for key in ("scope_summary", "scene_storyboard", "ripple_match")):
        return payload
    return None


def _iter_choice_artifacts(project_root: Path) -> list[tuple[str, Path, dict]]:
    wal = project_root / DB / ".wal"
    if not wal.exists():
        return []
    choices: list[tuple[str, Path, dict]] = []
    for path in sorted(wal.glob("cluster_*_user_choice.json")):
        cluster_id = cl.normalize_cluster_id(path.stem)
        brief = _extract_choice_brief(load_json(path, {}))
        if cluster_id and isinstance(brief, dict):
            choices.append((cluster_id, path, brief))
    return choices


def _event_clusters(project_root: Path) -> dict[str, dict]:
    data = load_json(project_root / DB / EVENT_CLUSTER, {}) or {}
    entries = data.get("clusters") if isinstance(data, dict) else []
    output: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        cluster_id = cl.normalize_cluster_id(entry.get("cluster_id"))
        if cluster_id:
            output[cluster_id] = entry
    return output


def _field_mismatch(brief: dict, landed: dict) -> list[str]:
    mismatched: list[str] = []
    for field in ("scope_summary", "ripple_match"):
        expected = brief.get(field)
        if expected not in (None, "") and landed.get(field) not in (None, expected):
            mismatched.append(field)
    storyboard = brief.get("scene_storyboard")
    if isinstance(storyboard, list) and storyboard:
        if not isinstance(landed.get("scene_storyboard"), list) or not landed["scene_storyboard"]:
            mismatched.append("scene_storyboard")
    return mismatched


def scan_user_choice_landed(project_root: Path) -> list[dict]:
    findings: list[dict] = []
    choices = _iter_choice_artifacts(project_root)
    if not choices:
        return findings
    landed_clusters = _event_clusters(project_root)
    for cluster_id, path, brief in choices:
        brief_id = cl.normalize_cluster_id(brief.get("cluster_id")) or cluster_id
        if brief_id != cluster_id:
            findings.append({
                "severity": "warning", "gate_level": "advisory",
                "code": "USER_CHOICE_CLUSTER_ID_MISMATCH",
                "cluster_id": cluster_id, "choice_file": str(path),
                "brief_cluster_id": brief.get("cluster_id"),
            })
            continue
        landed = landed_clusters.get(cluster_id)
        if not landed:
            findings.append({
                "severity": "warning", "gate_level": "advisory",
                "code": "USER_CHOICE_NOT_LANDED",
                "cluster_id": cluster_id, "choice_file": str(path),
            })
            continue
        mismatched = _field_mismatch(brief, landed)
        if mismatched:
            findings.append({
                "severity": "advisory", "gate_level": "advisory",
                "code": "USER_CHOICE_LANDED_MISMATCH",
                "cluster_id": cluster_id, "fields": mismatched,
                "choice_file": str(path),
            })
    return findings


def build_report(project_root: Path, last_n: int | None = None) -> dict:
    records = csr.get_clusters(project_root, last_n=last_n)
    if not records:
        raise csr.ClusterSummaryError("故事块摘要没有已完成 cluster")
    findings = scan_beat_progression(records)
    findings.extend(scan_user_choice_landed(project_root))
    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "structure_compliance",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_scanned": [str(record["cluster_id"]) for record in records],
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
        out_dir = Path(args.project) / DB / ".cross_cluster_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"structure_compliance_{stamp}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[structure_compliance] warning={report['summary']['warning']} "
              f"advisory={report['summary']['advisory']}")
        print(f"报告: {out_path}")
        return 2 if report["summary"]["warning"] else 1 if report["summary"]["advisory"] else 0
    except (OSError, ValueError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
