"""plot_structure_scanner 的 cluster-native 路由与文本扫描回归。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plot_structure_scanner as scanner  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _beats(*names: str) -> list[dict]:
    return [{"beat": name} for name in names]


def test_scan_beat_reads_only_requested_cluster(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "_数据库" / "beat_map.json",
        {
            "cluster_beats": {
                "cluster_001": _beats("开场钩子", "催化剂"),
                "cluster_002": _beats("Midpoint", "坏人逼近"),
            }
        },
    )

    report = scanner.scan_beat(
        tmp_path, "cluster_002", "局势突然反转，坏人正在逼近。"
    )

    assert report["cluster_id"] == "cluster_002"
    assert report["beats_declared"] == ["Midpoint", "坏人逼近"]
    assert report["beats_declared_count"] == 2
    assert report["beats_addressed"] == ["Midpoint", "坏人逼近"]
    assert report["beat_signal_hit"] is True
    assert report["warning"] is None


def test_scan_beat_missing_target_never_borrows_other_cluster(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "_数据库" / "beat_map.json",
        {"cluster_beats": {"cluster_001": _beats("开场钩子")}},
    )

    report = scanner.scan_beat(tmp_path, "cluster_002", "正文")

    assert report["cluster_id"] == "cluster_002"
    assert report["beats_declared"] == []
    assert report["beats_addressed"] == []
    assert report["beat_signal_hit"] is False
    assert report["beats_declared_count"] == 0
    assert report["warning"]


def test_try_fail_uses_complete_cluster_draft(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "_数据库" / "人物卡.json",
        {"characters": [{"name": "林澈", "role": "主角"}]},
    )

    report = scanner.scan_try_fail(
        tmp_path,
        "cluster_003",
        "林澈试着推开门，却被铁链挡住。后来他终于打开了侧门。",
    )

    assert report["protagonist"] == "林澈"
    assert report["tries_estimate"] >= 1
    assert report["fails"] >= 1
    assert report["successes"] >= 1
    assert report["warning"] is None


def test_midpoint_check_only_activates_for_declared_beat(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "_数据库" / "beat_map.json",
        {
            "cluster_beats": {
                "cluster_001": _beats("建置"),
                "cluster_002": _beats("Midpoint"),
            }
        },
    )

    inactive = scanner.scan_midpoint(tmp_path, "cluster_001", "原来如此。")
    active = scanner.scan_midpoint(
        tmp_path, "cluster_002", "他没想到真相竟然完全相反。"
    )

    assert inactive["status"] == "n/a"
    assert inactive["warning"] is None
    assert active["status"] == "active"
    assert active["reversal_keyword_count"] >= 2
    assert active["warning"] is None


def test_character_arc_reads_stages_by_cluster(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "_数据库" / "人物卡.json",
        {"characters": [{"name": "林澈", "role": "主角"}]},
    )
    _write_json(
        tmp_path / "_数据库" / "character_arc_state.json",
        {
            "characters": {
                "林澈": {
                    "lie": "只能独行",
                    "want": "逃出去",
                    "need": "学会信任",
                    "truth": "合作不是软弱",
                    "stages_by_cluster": {"cluster_002": "pressure"},
                }
            }
        },
    )

    report = scanner.scan_character_arc(tmp_path, "cluster_002")

    assert report["current_stage"] == "pressure"
    assert report["warning"] is None


def test_subplot_sleep_uses_cluster_distance(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "_数据库" / "subplot_threads.json",
        {
            "threads": [
                {
                    "id": "thread_a",
                    "name": "失踪案",
                    "current_status": "active",
                    "last_advanced_cluster": "cluster_001",
                }
            ]
        },
    )

    report = scanner.scan_subplot_threads(tmp_path, "cluster_007")

    assert report["sleeping_threads_count"] == 1
    assert report["sleeping_threads"][0]["clusters_silent"] == 6


def test_cli_requires_cluster_id_and_draft(tmp_path: Path, capsys) -> None:
    draft = tmp_path / "cluster_001_draft.txt"
    draft.write_text("短草稿", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        scanner.main([str(tmp_path), "cluster_001", "--draft", str(draft), "--checks", "beat"])

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["cluster_id"] == "cluster_001"
    assert payload["scanner"] == "plot_structure_scanner"
    assert "chapter" not in payload
