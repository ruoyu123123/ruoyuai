"""Deterministic tests for cross_cluster_structure_compliance_aggregate."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_structure_compliance_aggregate as mod  # noqa: E402

DB = "_\u6570\u636e\u5e93"
CHAPTERS = "\u7ae0\u8282"
EVENT_CLUSTER = "\u4e8b\u4ef6\u7c07.json"


def _mk_db(root: Path) -> Path:
    db = root / DB
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    return db


def _mk_chapter(root: Path, ch: int, text: str = "", changes: dict | None = None) -> None:
    chapter_dir = root / CHAPTERS / f"\u7b2c{ch:03d}\u7ae0"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    if text:
        (chapter_dir / f"\u7b2c{ch:03d}\u7ae0.txt").write_text(text, encoding="utf-8")
    if changes is not None:
        (chapter_dir / f"\u7b2c{ch:03d}\u7ae0_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False),
            encoding="utf-8",
        )


def _write_event_clusters(root: Path, clusters: list[dict]) -> None:
    db = _mk_db(root)
    (db / EVENT_CLUSTER).write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_choice(root: Path, key: str, brief: dict, wrapper: str = "answer") -> Path:
    db = _mk_db(root)
    payload = brief if wrapper == "direct" else {wrapper: brief}
    path = db / ".wal" / f"cluster_{key}_user_choice.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_beat_keywords_for_exact_suffix_and_unknown():
    assert mod.beat_keywords_for("Catalyst") == [
        "\u50ac\u5316", "\u610f\u5916", "\u4e8b\u4ef6", "\u5f02\u5e38", "\u5f02\u53d8", "\u9707\u60ca"
    ]
    assert mod.beat_keywords_for("Set-Up_late") == ["\u94fa\u57ab", "\u65e5\u5e38", "\u4ecb\u7ecd"]
    assert mod.beat_keywords_for("") == []
    assert mod.beat_keywords_for(None) == []
    assert mod.beat_keywords_for("ZZZ_NotABeat") == []


def test_scan_beat_progression_missed_when_no_signal():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"5": "Catalyst"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        _mk_chapter(root, 5, text="plain scene without related signal", changes={"factual": {"beats_addressed": []}})

        findings = mod.scan_beat_progression(root, [5])
        assert len(findings) == 1
        assert findings[0]["code"] == "BEAT_MISSED"
        assert findings[0]["severity"] == "warning"
        assert findings[0]["ch"] == 5


def test_scan_beat_progression_keyword_hit_no_finding():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"5": "Catalyst"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        _mk_chapter(root, 5, text="\u4e00\u573a\u610f\u5916\u8ba9\u6240\u6709\u4eba\u9707\u60ca", changes={"factual": {"beats_addressed": []}})

        assert mod.scan_beat_progression(root, [5]) == []


def test_scan_beat_progression_explicit_changes_hit():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"7": "Midpoint"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        _mk_chapter(root, 7, text="plain text", changes={"factual": {"beats_addressed": ["Midpoint reached"]}})

        assert mod.scan_beat_progression(root, [7]) == []


def test_scan_beat_progression_gap_too_long():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"1": "Opening Image", "62": "Catalyst", "122": "Midpoint"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        findings = mod.scan_beat_progression(root, [1, 62, 122])
        gaps = [f for f in findings if f["code"] == "BEAT_GAP_TOO_LONG"]
        assert len(gaps) == 1
        assert gaps[0]["from_ch"] == 1
        assert gaps[0]["to_ch"] == 62
        assert gaps[0]["gap"] == 61


def test_scan_beat_progression_no_beat_map():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root)
        assert mod.scan_beat_progression(root, [1, 2, 3]) == []


def test_ledger_beat_signal_hit_none_treated_as_missed():
    findings = mod.scan_beat_progression_ledger([
        (5, {"beat": "Catalyst", "beat_signal_hit": None, "beats_addressed": []})
    ])
    assert [f for f in findings if f["code"] == "BEAT_MISSED"][0]["ch"] == 5


def test_ledger_beat_signal_hit_true_not_missed():
    findings = mod.scan_beat_progression_ledger([
        (5, {"beat": "Catalyst", "beat_signal_hit": True, "beats_addressed": []})
    ])
    assert [f for f in findings if f["code"] == "BEAT_MISSED"] == []


def test_ledger_beat_explicit_addressed_overrides_false_signal():
    findings = mod.scan_beat_progression_ledger([
        (8, {"beat": "Midpoint", "beat_signal_hit": False, "beats_addressed": ["Midpoint done"]})
    ])
    assert [f for f in findings if f["code"] == "BEAT_MISSED"] == []


def test_ledger_beat_gap_too_long():
    findings = mod.scan_beat_progression_ledger([
        (1, {"beat": "Opening Image", "beat_signal_hit": True}),
        (70, {"beat": "Catalyst", "beat_signal_hit": True}),
        (200, {"beat": "Midpoint", "beat_signal_hit": True}),
    ])
    gaps = [f for f in findings if f["code"] == "BEAT_GAP_TOO_LONG"]
    assert {(g["from_ch"], g["to_ch"]) for g in gaps} == {(1, 70), (70, 200)}


def test_ledger_beat_skips_records_without_beat():
    assert mod.scan_beat_progression_ledger([
        (3, {"summary": "no beat"}),
        (4, {"beat": "", "beat_signal_hit": None}),
    ]) == []


def test_user_choice_not_landed_warning_from_cluster_artifact():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root)
        _write_event_clusters(root, [{"cluster_id": "cluster_001", "status": "done"}])
        _write_choice(root, "002", {
            "cluster_id": "cluster_002",
            "scope_summary": "selected scope",
            "ripple_match": "minor ripple",
        })

        findings = mod.scan_user_choice_landed(root, [5])
        assert len(findings) == 1
        assert findings[0]["code"] == "USER_CHOICE_NOT_LANDED"
        assert findings[0]["cluster_id"] == "cluster_002"


def test_user_choice_cluster_id_mismatch_warning():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root)
        _write_event_clusters(root, [])
        _write_choice(root, "002", {
            "cluster_id": "cluster_003",
            "scope_summary": "wrong id",
            "ripple_match": "minor ripple",
        })

        findings = mod.scan_user_choice_landed(root, [])
        assert len(findings) == 1
        assert findings[0]["code"] == "USER_CHOICE_CLUSTER_ID_MISMATCH"


def test_user_choice_landed_mismatch_advisory():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_choice(root, "002", {
            "cluster_id": "cluster_002",
            "scope_summary": "selected scope",
            "ripple_match": "minor ripple",
            "scene_storyboard": [{"scene_idx": 0, "summary": "start"}],
        })
        _write_event_clusters(root, [{
            "cluster_id": "cluster_002",
            "scope_summary": "different scope",
            "ripple_match": "minor ripple",
            "status": "in_progress",
        }])

        findings = mod.scan_user_choice_landed(root, [])
        assert len(findings) == 1
        assert findings[0]["code"] == "USER_CHOICE_LANDED_MISMATCH"
        assert set(findings[0]["fields"]) == {"scope_summary", "scene_storyboard"}


def test_user_choice_landed_clean_no_finding():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        brief = {
            "cluster_id": "cluster_002_candidate_1",
            "scope_summary": "selected scope",
            "ripple_match": "minor ripple",
            "scene_storyboard": [{"scene_idx": 0, "summary": "start"}],
        }
        _write_choice(root, "002", brief)
        _write_event_clusters(root, [{
            "cluster_id": "cluster_002",
            "scope_summary": "selected scope",
            "ripple_match": "minor ripple",
            "scene_storyboard": [{"ch": 6, "scene_idx": 0, "summary": "start"}],
            "status": "in_progress",
        }])

        assert mod.scan_user_choice_landed(root, []) == []


def test_user_choice_no_artifact_no_finding():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root)
        _write_event_clusters(root, [])
        assert mod.scan_user_choice_landed(root, [1, 2]) == []


def test_ledger_choice_mismatch():
    findings = mod.scan_user_choice_landed_ledger([
        (5, {
            "user_choice": "A",
            "choice_leads_to": "\u9ed1\u9f99\u89c9\u9192\u541e\u566c\u661f\u8fb0",
            "turning_point": "\u4e3b\u89d2\u79bb\u5f00\u57ce\u5e02",
        })
    ])
    assert len(findings) == 1
    assert findings[0]["code"] == "CHOICE_LEADS_TO_MISMATCH"


def test_ledger_choice_overlap_and_missing_skipped():
    findings = mod.scan_user_choice_landed_ledger([
        (5, {
            "user_choice": "B",
            "choice_leads_to": "\u79bb\u5f00\u57ce\u5e02\u8e0f\u4e0a\u65c5\u9014",
            "turning_point": "\u4e3b\u89d2\u51b3\u5b9a\u79bb\u5f00\u57ce\u5e02",
        }),
        (6, {"choice_leads_to": "x", "turning_point": "x"}),
        (7, {"user_choice": "A", "choice_leads_to": "x"}),
    ])
    assert findings == []


def test_load_json_missing_and_corrupt():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert mod.load_json(root / "missing.json", default={"x": 1}) == {"x": 1}
        bad = root / "bad.json"
        bad.write_text("{ bad", encoding="utf-8")
        assert mod.load_json(bad, default=[]) == []
        good = root / "good.json"
        good.write_text(json.dumps({"a": [1, 2]}), encoding="utf-8")
        assert mod.load_json(good) == {"a": [1, 2]}


def test_get_chapters_sorted_last_n():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert mod.get_chapters(root, 5) == []
        for ch in [3, 1, 12, 7]:
            (root / CHAPTERS / f"\u7b2c{ch:03d}\u7ae0").mkdir(parents=True, exist_ok=True)
        (root / CHAPTERS / "draft").mkdir(parents=True, exist_ok=True)
        assert mod.get_chapters(root, 10) == [1, 3, 7, 12]
        assert mod.get_chapters(root, 2) == [7, 12]
