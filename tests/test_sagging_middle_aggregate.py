"""故事块中段塌陷聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_TARGET = _SCRIPTS / "cross_cluster_sagging_middle_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_sagging_middle_aggregate as scanner  # noqa: E402


def _cluster(
    cluster_id: str,
    *,
    scenes: list[str] | None = None,
    stress_new_total: float | None = None,
    turn: float | None = None,
    hook: float | None = None,
    outcome: str = "neutral",
) -> dict:
    audit_summary: dict = {}
    if turn is not None:
        audit_summary["golden_three"] = {"turn": turn}
    if hook is not None:
        audit_summary["hook_strength"] = {"score": hook}
    stress = (
        {"new_total": stress_new_total}
        if stress_new_total is not None
        else {}
    )
    return cluster_record(
        cluster_id,
        scene_summaries=list(scenes or []),
        stress=stress,
        audit={"summary": audit_summary, "issues": [], "scanner_status": []},
        outcome=outcome,
    )


def _run(
    project: Path, *args: str, mode: str = "shadow"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_TARGET), str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**_ENV, "SAGGING_MIDDLE_MODE": mode},
        timeout=120,
    )


def _latest_report(project: Path) -> Path:
    reports = [
        path
        for path in (project / "_数据库" / ".cross_cluster_scan").glob(
            "sagging_middle_*.json"
        )
        if path.name != "sagging_middle_snapshot.json"
    ]
    return max(reports, key=lambda path: path.name)


def test_middle_slice_takes_selected_sequence_40_to_60_percent() -> None:
    clusters = [_cluster(f"cluster_{index:03d}") for index in range(1, 11)]
    assert [record["cluster_id"] for record in scanner._middle_slice(clusters)] == [
        "cluster_005", "cluster_006", "cluster_007"
    ]


def test_middle_slice_requires_five_clusters() -> None:
    assert scanner._middle_slice([_cluster("cluster_001")]) == []


def test_drive_purpose_comes_from_cluster_scene_summaries() -> None:
    assert scanner._cluster_purpose_tags(_cluster(
        "cluster_001", scenes=["主角揭露幕后真相，并作出抉择"]
    )) == {"reveal", "decision"}
    assert not scanner._cluster_has_drive(_cluster(
        "cluster_001", scenes=["主角再次巡查同一条走廊"]
    ))


def test_reversal_void_reads_current_audit_summary() -> None:
    middle = [
        _cluster("cluster_001", scenes=["重复巡查"], turn=0.2, hook=0.3),
        _cluster("cluster_002", scenes=["继续巡查"], turn=0.3, hook=0.2),
    ]
    result = scanner.detect_reversal_void(middle)
    assert result["hit"] is True
    assert result["turn_average"] == 0.25
    assert result["hook_average"] == 0.25


def test_reversal_void_is_cleared_by_strong_score_or_drive() -> None:
    strong = [_cluster(
        "cluster_001", scenes=["重复巡查"], turn=0.8, hook=0.9
    )]
    drive = [_cluster(
        "cluster_001", scenes=["敌人身份反转"], turn=0.2, hook=0.2
    )]
    assert scanner.detect_reversal_void(strong)["hit"] is False
    assert scanner.detect_reversal_void(drive)["hit"] is False


def test_stakes_flat_uses_cluster_stress_and_scene_purposes() -> None:
    middle = [
        _cluster("cluster_001", scenes=["重复巡查"], stress_new_total=3, outcome="partial"),
        _cluster("cluster_002", scenes=["继续巡查"], stress_new_total=3, outcome="partial"),
        _cluster("cluster_003", scenes=["再次巡查"], stress_new_total=3, outcome="partial"),
    ]
    result = scanner.detect_stakes_flat(middle)
    assert result["hit"] is True
    assert result["stress_points"] == [
        ("cluster_001", 3.0), ("cluster_002", 3.0), ("cluster_003", 3.0)
    ]
    assert result["purpose_tag_count"] == 0


def test_stakes_flat_requires_complete_evidence_and_no_rise() -> None:
    rising = [
        _cluster("cluster_001", scenes=["重复巡查"], stress_new_total=1),
        _cluster("cluster_002", scenes=["危机升级"], stress_new_total=4),
        _cluster("cluster_003", scenes=["身份改变"], stress_new_total=7),
    ]
    incomplete = [
        _cluster("cluster_001", scenes=["重复巡查"]),
        _cluster("cluster_002", scenes=["继续巡查"]),
        _cluster("cluster_003", scenes=["再次巡查"]),
    ]
    assert scanner.detect_stakes_flat(rising)["hit"] is False
    assert scanner.detect_stakes_flat(incomplete)["hit"] is False


def test_purpose_void_tracks_consecutive_clusters() -> None:
    middle = [
        _cluster("cluster_001", scenes=["重复巡查"]),
        _cluster("cluster_002", scenes=["继续巡查"]),
        _cluster("cluster_003", scenes=["再次巡查"]),
    ]
    result = scanner.detect_purpose_void(middle)
    assert result == {
        "hit": True,
        "max_void_streak": 3,
        "void_clusters": ["cluster_003"],
    }


def test_purpose_void_resets_on_drive_cluster() -> None:
    middle = [
        _cluster("cluster_001", scenes=["重复巡查"]),
        _cluster("cluster_002", scenes=["秘密揭晓"]),
        _cluster("cluster_003", scenes=["继续巡查"]),
        _cluster("cluster_004", scenes=["再次巡查"]),
    ]
    assert scanner.detect_purpose_void(middle)["hit"] is False


def test_cli_shadow_writes_cluster_report_and_snapshot(tmp_path: Path) -> None:
    records = [_cluster(
        f"cluster_{index:03d}",
        scenes=["重复巡查"],
        stress_new_total=3,
        turn=0.2,
        hook=0.2,
    ) for index in range(1, 11)]
    write_cluster_summary(tmp_path, records)
    result = _run(tmp_path)
    assert result.returncode == 0
    out_dir = tmp_path / "_数据库" / ".cross_cluster_scan"
    report = json.loads(_latest_report(tmp_path).read_text(encoding="utf-8"))
    snapshot = json.loads(
        (out_dir / "sagging_middle_snapshot.json").read_text(encoding="utf-8")
    )
    assert report["clusters_scanned"] == [
        f"cluster_{index:03d}" for index in range(1, 11)
    ]
    assert report["middle_cluster_ids"] == [
        "cluster_005", "cluster_006", "cluster_007"
    ]
    assert snapshot["needs_midpoint_bomb"] is True


def test_cli_active_returns_warning_for_multiple_signals(tmp_path: Path) -> None:
    records = [_cluster(
        f"cluster_{index:03d}",
        scenes=["重复巡查"],
        stress_new_total=3,
        turn=0.2,
        hook=0.2,
    ) for index in range(1, 11)]
    write_cluster_summary(tmp_path, records)
    assert _run(tmp_path, mode="active").returncode == 2


def test_cli_too_few_clusters_still_writes_required_outputs(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [_cluster("cluster_001")])
    result = _run(tmp_path)
    assert result.returncode == 0
    report_path = _latest_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["middle_formed"] is False
    assert report["findings"] == []


def test_cli_missing_summary_is_fatal(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "[FATAL]" in result.stderr
    assert "必需摘要账本不存在" in result.stderr


def test_cli_rejects_chapter_payload_in_cluster_summary(tmp_path: Path) -> None:
    path = write_cluster_summary(tmp_path, [_cluster("cluster_001")])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["clusters"][0]["chapters"] = {}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "含未知字段" in result.stderr
    assert "chapters" in result.stderr


def test_cli_last_n_is_cluster_window(tmp_path: Path) -> None:
    records = [_cluster(f"cluster_{index:03d}") for index in range(1, 11)]
    write_cluster_summary(tmp_path, records)
    result = _run(tmp_path, "--last-n", "5")
    assert result.returncode == 0
    report_path = _latest_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["clusters_scanned"] == [
        "cluster_006", "cluster_007", "cluster_008", "cluster_009", "cluster_010"
    ]


def test_cli_off_mode_does_not_require_summary(tmp_path: Path) -> None:
    result = _run(tmp_path, mode="off")
    assert result.returncode == 0
    assert "SAGGING_MIDDLE_MODE=off" in result.stdout
