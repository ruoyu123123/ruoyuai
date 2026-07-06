#!/usr/bin/env python3
"""Cross-cluster structural compliance aggregate.

Checks two structure-level signals:
1. Beat-map progression: declared beats should leave a signal in chapter text or
   per-chapter changes.
2. Cluster user-choice landing: a cluster user-choice artifact must be reflected
   in the canonical event-cluster ledger.

The user-choice check is cluster-only. It never reads per-chapter fate card files
and never expects per-scene user_choice fields.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import cluster_lookup as cl  # noqa: E402
import cluster_summary_reader as csr  # noqa: E402

DB = "_\u6570\u636e\u5e93"
CHAPTERS = "\u7ae0\u8282"
EVENT_CLUSTER = "\u4e8b\u4ef6\u7c07.json"
PROGRESS = "\u8fdb\u5ea6.json"
CHAPTER_RE = re.compile(r"\u7b2c(\d+)\u7ae0")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")
IS_CLUSTER_MODE = os.environ.get("CLUSTER_MODE") == "1"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_chapters(project_root: Path, last_n: int) -> list[int]:
    chapter_dir = project_root / CHAPTERS
    if not chapter_dir.exists():
        return []
    chs: list[int] = []
    for item in chapter_dir.glob("\u7b2c*\u7ae0"):
        m = CHAPTER_RE.match(item.name)
        if m:
            chs.append(int(m.group(1)))
    chs = sorted(set(chs))
    return chs[-last_n:] if chs else []


def _chapter_path(project_root: Path, ch: int) -> Path:
    return project_root / CHAPTERS / f"\u7b2c{ch:03d}\u7ae0"


def read_changes(project_root: Path, ch: int) -> dict:
    return load_json(
        _chapter_path(project_root, ch) / f"\u7b2c{ch:03d}\u7ae0_changes.json",
        {},
    )


def read_text(project_root: Path, ch: int) -> str:
    path = _chapter_path(project_root, ch) / f"\u7b2c{ch:03d}\u7ae0.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


BEAT_KEYWORDS = {
    "Opening Image": ["\u5f00\u573a", "\u9996\u7ae0"],
    "Theme Stated": ["\u4e3b\u9898", "\u7406\u5ff5"],
    "Set-Up": ["\u94fa\u57ab", "\u65e5\u5e38", "\u4ecb\u7ecd"],
    "Catalyst": ["\u50ac\u5316", "\u610f\u5916", "\u4e8b\u4ef6", "\u5f02\u5e38", "\u5f02\u53d8", "\u9707\u60ca"],
    "Debate": ["\u72b9\u8c6b", "\u6743\u8861", "\u53cd\u590d", "\u7ea0\u7ed3"],
    "Break into Two": ["\u51b3\u5b9a", "\u51fa\u53d1", "\u8e0f\u5165", "\u65b0\u4e16\u754c"],
    "B Story": ["\u526f\u7ebf", "\u652f\u7ebf"],
    "Fun and Games": ["\u5386\u9669", "\u8bd5\u70bc", "\u6311\u6218"],
    "Midpoint": ["\u8f6c\u6298", "\u4e2d\u70b9", "\u7a81\u53d8"],
    "Bad Guys Close In": ["\u53cd\u6d3e", "\u903c\u8fd1", "\u538b\u8feb", "\u56f4\u527f"],
    "All Is Lost": ["\u5931\u53bb", "\u5931\u8d25", "\u5d29\u6e83", "\u7edd\u5883"],
    "Dark Night of Soul": ["\u9ed1\u6697", "\u7edd\u671b", "\u5fc3\u6b7b"],
    "Break into Three": ["\u987f\u609f", "\u65b0\u51b3\u5fc3", "\u518d\u8d77"],
    "Finale": ["\u51b3\u6218", "\u7ec8\u5c40", "\u9ad8\u6f6e"],
    "Final Image": ["\u6536\u5c3e", "\u7ec8\u7ae0"],
}


def beat_keywords_for(beat_str: str | None) -> list[str]:
    if not beat_str:
        return []
    base = beat_str.split("_", 1)[0].strip().lower()
    lowered = beat_str.lower()
    for key, words in BEAT_KEYWORDS.items():
        if key.lower() in lowered or base in key.lower():
            return words
    return []


def scan_beat_progression(project_root: Path, chapters: list[int]) -> list[dict]:
    beat_path = project_root / DB / "beat_map.json"
    beat_data = load_json(beat_path, {}) or {}
    chapters_beat = beat_data.get("chapters_beat", {}) or {}
    if not chapters_beat:
        return []

    findings: list[dict] = []
    for ch in chapters:
        beat = chapters_beat.get(str(ch)) or chapters_beat.get(ch)
        keywords = beat_keywords_for(beat)
        if not beat or not keywords:
            continue
        text = read_text(project_root, ch)
        if not text:
            continue
        changes = read_changes(project_root, ch)
        beats_addressed = (changes.get("factual", {}) or {}).get("beats_addressed", []) or []
        explicit_hit = any(str(beat).lower() in str(item).lower() for item in beats_addressed)
        keyword_hit = any(word in text for word in keywords)
        if not explicit_hit and not keyword_hit:
            findings.append({
                "severity": "warning",
                "code": "BEAT_MISSED",
                "ch": ch,
                "expected_beat": beat,
                "expected_kws": keywords[:3],
                "suggestion": f"ch{ch} beat_map declares {beat}, but text/changes contain no matching signal",
            })

    declared = sorted(int(k) for k in chapters_beat.keys() if str(k).isdigit())
    for prev, cur in zip(declared, declared[1:]):
        gap = cur - prev
        if gap > 60:
            findings.append({
                "severity": "advisory",
                "code": "BEAT_GAP_TOO_LONG",
                "from_ch": prev,
                "to_ch": cur,
                "gap": gap,
                "suggestion": f"beat_map has a {gap}-chapter gap between ch{prev} and ch{cur}",
            })
    return findings


def _extract_choice_brief(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("answer"), dict):
        return payload["answer"]
    if isinstance(payload.get("cluster_brief"), dict):
        return payload["cluster_brief"]
    if any(k in payload for k in ("scope_summary", "scene_storyboard", "ripple_match")):
        return payload
    return None


def _iter_choice_artifacts(project_root: Path) -> list[tuple[str, Path, dict]]:
    wal = project_root / DB / ".wal"
    if not wal.exists():
        return []
    out: list[tuple[str, Path, dict]] = []
    for path in sorted(wal.glob("cluster_*_user_choice.json")):
        cid = cl.normalize_cluster_id(path.stem)
        data = load_json(path, {})
        brief = _extract_choice_brief(data)
        if cid and isinstance(brief, dict):
            out.append((cid, path, brief))
    return out


def _load_event_clusters(project_root: Path) -> dict[str, dict]:
    data = load_json(project_root / DB / EVENT_CLUSTER, {}) or {}
    clusters = data.get("clusters") if isinstance(data, dict) else []
    out: dict[str, dict] = {}
    if isinstance(clusters, list):
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            cid = cl.normalize_cluster_id(cluster.get("cluster_id"))
            if cid:
                out[cid] = cluster
    return out


def _cluster_field_mismatch(brief: dict, landed: dict) -> list[str]:
    mismatched: list[str] = []
    for field in ("scope_summary", "ripple_match"):
        expected = brief.get(field)
        if expected in (None, ""):
            continue
        if landed.get(field) not in (None, expected):
            mismatched.append(field)
    brief_scenes = brief.get("scene_storyboard")
    landed_scenes = landed.get("scene_storyboard")
    if isinstance(brief_scenes, list) and brief_scenes:
        if not isinstance(landed_scenes, list) or not landed_scenes:
            mismatched.append("scene_storyboard")
    return mismatched


def scan_user_choice_landed(project_root: Path, chapters: list[int]) -> list[dict]:
    del chapters  # cluster choice artifacts are cluster-scoped, not chapter-scoped.
    findings: list[dict] = []
    choices = _iter_choice_artifacts(project_root)
    if not choices:
        return []

    event_clusters = _load_event_clusters(project_root)
    for cid, path, brief in choices:
        brief_cid = cl.normalize_cluster_id(brief.get("cluster_id")) or cid
        if brief_cid != cid:
            findings.append({
                "severity": "warning",
                "code": "USER_CHOICE_CLUSTER_ID_MISMATCH",
                "cluster_id": cid,
                "choice_file": str(path),
                "brief_cluster_id": brief.get("cluster_id"),
                "suggestion": f"{path.name} declares {brief.get('cluster_id')}, expected {cid}",
            })
            continue
        landed = event_clusters.get(cid)
        if not landed:
            findings.append({
                "severity": "warning",
                "code": "USER_CHOICE_NOT_LANDED",
                "cluster_id": cid,
                "choice_file": str(path),
                "suggestion": f"{path.name} exists but {EVENT_CLUSTER}.clusters has no {cid}",
            })
            continue
        mismatched = _cluster_field_mismatch(brief, landed)
        if mismatched:
            findings.append({
                "severity": "advisory",
                "code": "USER_CHOICE_LANDED_MISMATCH",
                "cluster_id": cid,
                "fields": mismatched,
                "choice_file": str(path),
                "suggestion": f"{cid} is landed, but fields differ from the selected brief: {mismatched}",
            })
    return findings


def scan_beat_progression_ledger(recs: list) -> list[dict]:
    findings: list[dict] = []
    declared: list[int] = []
    for ch, rec in recs:
        beat = rec.get("beat")
        if not beat:
            continue
        declared.append(ch)
        signal_hit = rec.get("beat_signal_hit") is True
        beats_addressed = rec.get("beats_addressed") or []
        explicit_hit = any(str(beat).lower() in str(item).lower() for item in beats_addressed)
        if not signal_hit and not explicit_hit:
            findings.append({
                "severity": "warning",
                "code": "BEAT_MISSED",
                "ch": ch,
                "expected_beat": beat,
                "expected_kws": beat_keywords_for(beat)[:3],
                "suggestion": f"ch{ch} beat_map declares {beat}, but ledger reports no signal",
            })

    declared = sorted(declared)
    for prev, cur in zip(declared, declared[1:]):
        gap = cur - prev
        if gap > 60:
            findings.append({
                "severity": "advisory",
                "code": "BEAT_GAP_TOO_LONG",
                "from_ch": prev,
                "to_ch": cur,
                "gap": gap,
                "suggestion": f"beat_map has a {gap}-chapter gap between ch{prev} and ch{cur}",
            })
    return findings


def scan_user_choice_landed_ledger(recs: list) -> list[dict]:
    findings: list[dict] = []
    for ch, rec in recs:
        user_choice = rec.get("user_choice")
        if not user_choice:
            continue
        leads_to = rec.get("choice_leads_to") or ""
        turning = rec.get("turning_point") or ""
        if leads_to and turning:
            keywords = CJK_RE.findall(leads_to)
            if keywords and not any(keyword in turning for keyword in keywords[:5]):
                findings.append({
                    "severity": "advisory",
                    "code": "CHOICE_LEADS_TO_MISMATCH",
                    "ch": ch,
                    "user_choice": user_choice,
                    "leads_to": leads_to[:60],
                    "turning_point": turning[:60],
                    "suggestion": f"ledger choice {user_choice} at ch{ch} does not overlap turning_point",
                })
    return findings


def _emit_report(project_root: Path, chapters: list[int], findings: list[dict]) -> None:
    out_dir = project_root / DB / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "structure_compliance",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"structure_compliance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[structure_compliance] {summary['warning']} warning / {summary['advisory']} advisory")
    for finding in findings[:6]:
        print(f"  [{finding['severity'].upper()}] {finding.get('code')}: {finding.get('suggestion', '')[:80]}")
    print(f"report: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()

    project_root = Path(args.project)
    use_ledger = csr.is_cluster_mode() and (
        csr.ledger_has_field(project_root, "beat")
        or csr.ledger_has_field(project_root, "user_choice")
    )
    if use_ledger:
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        if not recs:
            print("[SKIP] cluster ledger has no chapter records")
            sys.exit(0)
        chapters = sorted({ch for ch, _ in recs})
        findings = scan_beat_progression_ledger(recs)
        findings.extend(scan_user_choice_landed_ledger(recs))
        _emit_report(project_root, chapters, findings)
        return

    chapters = get_chapters(project_root, args.last_n)
    if not chapters:
        print("[SKIP] no written chapters")
        sys.exit(0)

    findings = scan_beat_progression(project_root, chapters)
    findings.extend(scan_user_choice_landed(project_root, chapters))
    _emit_report(project_root, chapters, findings)


if __name__ == "__main__":
    main()
