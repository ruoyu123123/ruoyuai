"""跨故事块连续性聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary


_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_continuity_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_continuity_aggregate as scanner


def _project(records: list[dict], drafts: dict[str, str]) -> tuple[Path, Path, tempfile.TemporaryDirectory]:
    td = tempfile.TemporaryDirectory(prefix="continuity_cluster_")
    project = Path(td.name) / "project"
    db = project / "_数据库"
    db.mkdir(parents=True)
    write_cluster_summary(project, records)
    for cluster_id, text in drafts.items():
        key = cluster_id.removeprefix("cluster_")
        folder = project / "章节" / f"cluster_{key}_draft"
        folder.mkdir(parents=True)
        (folder / f"cluster_{key}_draft.txt").write_text(text, encoding="utf-8")
    (db / "人物卡.json").write_text(json.dumps({
        "characters": [{"id": "C_MAIN", "name": "重黎", "role": "主角"}]
    }, ensure_ascii=False), encoding="utf-8")
    return project, db, td


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(project), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=120,
    )


def test_cliffhanger_resonance_reads_summary_and_next_draft() -> None:
    previous = cluster_record(
        "cluster_001", ending_type="危机钩", ending_line="断剑上的血迹属于师父"
    )
    result = scanner.scan_cliffhanger_resonance(
        previous, "断剑还在滴血，师父的名字被人从石碑上刮掉。", protagonist="重黎"
    )
    assert result["score"] > 0
    assert result["source"] == "summary+draft"


def test_declared_suspense_cut_is_exempt() -> None:
    previous = cluster_record(
        "cluster_001", ending_type="悬念断章", ending_line="门后究竟是谁"
    )
    result = scanner.scan_cliffhanger_resonance(previous, "新的场景。")
    assert result["exempt"] is True
    assert result["score"] == 1.0


def test_time_gap_uses_state_delta_and_transition_summary() -> None:
    gap = scanner.scan_time_gap(
        {"time_advance": {"period": "周一夜里"}},
        {"time_advance": {"period": "周四清晨"}},
    )
    assert gap["detected"] is True
    assert gap["gap_days"] == 3
    assert scanner.check_time_transition(
        cluster_record("cluster_002", time_transition_present=True), "直接进入事件。"
    )["has_transition"] is True


def test_object_continuity_uses_item_changes_and_cluster_drafts() -> None:
    clusters = [
        cluster_record("cluster_001", item_changes=[{
            "id": "I_STONE", "name": "补天石（混沌余烬）", "first_cluster": "cluster_001"
        }]),
        cluster_record("cluster_002"),
        cluster_record("cluster_003"),
    ]
    drafts = {
        "cluster_001": "重黎捡起补天石。",
        "cluster_002": "众人进入山谷。",
        "cluster_003": "风雪封住归路。",
    }
    findings = scanner.scan_object_continuity(clusters, drafts)
    assert findings[0]["item"] == "补天石（混沌余烬）"
    assert findings[0]["gap_clusters"] == 2
    assert findings[0]["last_seen_cluster"] == "cluster_001"


def test_emotion_gap_reads_top_level_emotion() -> None:
    result = scanner.scan_emotion_gap(
        cluster_record("cluster_001", emotion={"value": 1}),
        cluster_record("cluster_002", emotion={"value": 7}),
    )
    assert result == {
        "detected": True, "previous_emotion": 1.0,
        "current_emotion": 7.0, "diff": 6.0,
    }


def test_build_report_emits_cluster_pairwise_fields() -> None:
    records = [
        cluster_record(
            "cluster_001", ending_type="危机钩", ending_line="黑门内传来哭声",
            emotion={"value": 1},
        ),
        cluster_record(
            "cluster_002", emotion={"value": 2}, time_transition_present=True,
        ),
    ]
    project, _db, td = _project(records, {
        "cluster_001": "重黎停在黑门外。",
        "cluster_002": "黑门内的哭声骤然停了。",
    })
    try:
        report = scanner.build_report(project)
        assert report["clusters_scanned"] == ["cluster_001", "cluster_002"]
        assert report["pairwise"][0]["from_cluster"] == "cluster_001"
        assert report["pairwise"][0]["to_cluster"] == "cluster_002"
        assert set(report) == {
            "scan_type", "scan_ts", "clusters_scanned", "pairwise", "findings", "summary"
        }
    finally:
        td.cleanup()


def test_cli_writes_report_and_honors_cluster_window() -> None:
    records = [cluster_record(f"cluster_{n:03d}") for n in range(1, 4)]
    drafts = {f"cluster_{n:03d}": f"第{n}个故事块正文。" for n in range(1, 4)}
    project, db, td = _project(records, drafts)
    try:
        result = _run(project, "--last-n", "2")
        assert result.returncode == 0
        reports = sorted((db / ".cross_cluster_scan").glob("continuity_*.json"))
        assert reports
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert report["clusters_scanned"] == ["cluster_002", "cluster_003"]
    finally:
        td.cleanup()


def test_missing_cluster_draft_is_fatal() -> None:
    records = [cluster_record("cluster_001"), cluster_record("cluster_002")]
    project, _db, td = _project(records, {"cluster_001": "正文。"})
    try:
        result = _run(project)
        assert result.returncode == 2
        assert "cluster 终稿不存在" in result.stderr
    finally:
        td.cleanup()
