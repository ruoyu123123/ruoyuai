"""角色动态聚合器的故事块合同测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import cluster_record, write_cluster_summary


_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "cross_cluster_character_dynamics_aggregate.py"
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _project(count: int = 7) -> tuple[Path, Path, tempfile.TemporaryDirectory]:
    td = tempfile.TemporaryDirectory(prefix="character_dynamics_cluster_")
    project = Path(td.name) / "project"
    db = project / "_数据库"
    db.mkdir(parents=True)
    write_cluster_summary(
        project,
        [cluster_record(f"cluster_{n:03d}") for n in range(1, count + 1)],
    )
    return project, db, td


def _write_json(db: Path, name: str, value: dict) -> None:
    (db / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(project), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=120,
    )


def _codes(findings: list[dict]) -> set[str]:
    return {item["code"] for item in findings}


def test_stress_trend_uses_stress_summary_field() -> None:
    project, db, td = _project(5)
    try:
        _write_json(db, "主角压力档.json", {
            "stress_threshold_break": 10,
            "coping_mechanisms": {},
        })
        from core.scripts.cross_cluster_character_dynamics_aggregate import scan_stress_trend

        clusters = [cluster_record(
            f"cluster_{n:03d}", stress={"new_total": n}
        ) for n in range(1, 5)]
        findings = scan_stress_trend(project, clusters)
        finding = next(item for item in findings if item["code"] == "STRESS_RUNAWAY")
        assert finding["consecutive_clusters"] == [
            "cluster_001", "cluster_002", "cluster_003", "cluster_004"
        ]
    finally:
        td.cleanup()


def test_high_stress_without_break_is_reported_per_cluster() -> None:
    project, db, td = _project(5)
    try:
        _write_json(db, "主角压力档.json", {
            "stress_threshold_break": 10,
            "coping_mechanisms": {},
        })
        from core.scripts.cross_cluster_character_dynamics_aggregate import scan_stress_trend

        clusters = [cluster_record(
            f"cluster_{n:03d}", stress={"new_total": 8}
        ) for n in range(1, 6)]
        findings = scan_stress_trend(project, clusters)
        assert "STRESS_PERMA_HIGH_NO_BREAK" in _codes(findings)
        assert "high_stress_clusters" in next(
            item for item in findings if item["code"] == "STRESS_PERMA_HIGH_NO_BREAK"
        )
    finally:
        td.cleanup()


def test_move_overuse_reads_frequency_per_cluster() -> None:
    project, db, td = _project(3)
    try:
        _write_json(db, "角色行动表.json", {
            "characters": {"主角": {"moves": [{
                "move_id": "MV_TEST", "frequency_per_cluster": 2,
            }]}}
        })
        from core.scripts.cross_cluster_character_dynamics_aggregate import scan_moves_usage

        clusters = [cluster_record(
            "cluster_001",
            moves_used=[{"character": "主角", "move_id": "MV_TEST", "instances": 3}],
        )]
        finding = scan_moves_usage(project, clusters)[0]
        assert finding["code"] == "MOVE_OVERUSE"
        assert finding["cluster_id"] == "cluster_001"
        assert finding["limit"] == 2
    finally:
        td.cleanup()


def test_character_voiceless_and_underused_use_cluster_mentions() -> None:
    project, db, td = _project(5)
    try:
        _write_json(db, "角色行动表.json", {
            "characters": {
                "失声": {"moves": [{"move_id": "MV_A", "frequency_per_cluster": 2}]},
                "单一": {"moves": [
                    {"move_id": "MV_A", "frequency_per_cluster": 2},
                    {"move_id": "MV_B", "frequency_per_cluster": 2},
                    {"move_id": "MV_C", "frequency_per_cluster": 2},
                    {"move_id": "MV_D", "frequency_per_cluster": 2},
                ]},
            }
        })
        from core.scripts.cross_cluster_character_dynamics_aggregate import scan_moves_usage

        silent_clusters = [cluster_record(
            f"cluster_{n:03d}", char_mention_counts={"失声": 1}
        ) for n in range(1, 6)]
        single_clusters = [cluster_record(
            f"cluster_{n + 5:03d}",
            char_mention_counts={"单一": 1},
            moves_used=(
                [{"character": "单一", "move_id": "MV_A", "instances": 1}]
                if n == 1 else []
            ),
        ) for n in range(1, 6)]
        findings = scan_moves_usage(project, silent_clusters + single_clusters)
        assert "CHARACTER_VOICELESS" in _codes(findings)
        assert "MOVES_UNDERUSED" in _codes(findings)
    finally:
        td.cleanup()


def test_cli_writes_cluster_report() -> None:
    project, db, td = _project(2)
    try:
        _write_json(db, "角色行动表.json", {"characters": {}})
        result = _run(project, "--last-n", "1")
        assert result.returncode == 0
        reports = sorted((db / ".cross_cluster_scan").glob("character_dynamics_*.json"))
        assert reports
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert report["clusters_scanned"] == ["cluster_002"]
        assert set(report) == {
            "scan_type", "scan_ts", "clusters_scanned", "findings", "summary"
        }
    finally:
        td.cleanup()


def test_module_imports() -> None:
    from core.scripts import cross_cluster_character_dynamics_aggregate as module

    assert callable(module.build_report)
    assert callable(module.main)
