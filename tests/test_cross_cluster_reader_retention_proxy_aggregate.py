"""故事块留存代理聚合器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
_TARGET = _SCRIPTS / "cross_cluster_reader_retention_proxy_aggregate.py"
_ENV = "READER_RETENTION_PROXY_MODE"

sys.path.insert(0, str(_SCRIPTS))
import cluster_summary_reader as csr  # noqa: E402
import cross_cluster_reader_retention_proxy_aggregate as scanner  # noqa: E402


def _record(
    index: int,
    *,
    hook: float = 0.8,
    ending_type: str = "场景收束",
    word_count: int = 12_000,
    scenes: list[str] | None = None,
    stress: float = 3,
) -> dict:
    return cluster_record(
        f"cluster_{index:03d}",
        audit={
            "summary": {"hook_strength": {"score": hook}},
            "issues": [],
            "scanner_status": [],
        },
        ending_type=ending_type,
        word_count=word_count,
        scene_summaries=list(scenes or ["主角作出抉择并推动局势升级"]),
        stress={"new_total": stress},
    )


def _run(project: Path, *args: str, mode: str = "shadow") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_TARGET), str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )


def test_mode_defaults_to_shadow(monkeypatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert scanner._mode() == "shadow"


def test_high_quality_cluster_sequence_scores_above_floor() -> None:
    records = [_record(index) for index in range(1, 5)]
    summary, findings = scanner.aggregate(records)
    assert summary["clusters_examined"] == [
        "cluster_001", "cluster_002", "cluster_003", "cluster_004"
    ]
    assert summary["hook_norm"] == 0.8
    assert summary["length_health"] == 1.0
    assert summary["retention_proxy"] > scanner.RETENTION_LOW_FLOOR
    assert findings == []


def test_low_cluster_signals_trigger_advisory() -> None:
    records = [
        _record(
            index,
            hook=0.1,
            ending_type="悬念断章",
            word_count=4_000,
            scenes=["主角继续重复巡查"],
            stress=3,
        )
        for index in range(1, 11)
    ]
    summary, findings = scanner.aggregate(records)
    assert summary["retention_proxy"] < scanner.RETENTION_LOW_FLOOR
    assert summary["sagging_inverse"] < 1.0
    assert summary["cliffhanger"] == 0.0
    assert findings[0]["code"] == scanner.ISSUE_CODE
    assert findings[0]["gate_level"] == "advisory"


def test_hook_score_normalizes_ten_point_scale() -> None:
    records = [_record(1, hook=8.0), _record(2, hook=6.0)]
    assert scanner._hook_score(records) == pytest.approx(0.7)


def test_missing_hook_telemetry_is_fatal() -> None:
    record = _record(1)
    record["audit"] = {"summary": {}, "issues": [], "scanner_status": []}
    with pytest.raises(csr.ClusterSummaryError, match="hook_strength"):
        scanner.aggregate([record])


def test_missing_ending_type_is_fatal() -> None:
    record = _record(1, ending_type="")
    with pytest.raises(csr.ClusterSummaryError, match="ending_type"):
        scanner.aggregate([record])


def test_length_health_uses_cluster_word_count() -> None:
    records = [_record(1, word_count=10_000), _record(2, word_count=25_000),
               _record(3, word_count=9_999), _record(4, word_count=25_001)]
    assert scanner._length_health(records) == 0.5


def test_weights_contract_is_strict() -> None:
    with pytest.raises(ValueError, match="weights 必须完整包含"):
        scanner.aggregate([_record(1)], {"hook": 1.0})


def test_cli_shadow_writes_cluster_report_and_honors_window(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [_record(index) for index in range(1, 5)])
    result = _run(tmp_path, "--last-n", "2")
    assert result.returncode == 0, result.stderr
    reports = sorted((tmp_path / "_数据库" / ".cross_cluster_scan").glob(
        "reader_retention_proxy_*.json"
    ))
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report["clusters_scanned"] == ["cluster_003", "cluster_004"]
    assert report["summary"]["clusters_examined"] == ["cluster_003", "cluster_004"]


def test_cli_missing_summary_is_fatal(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "必需摘要账本不存在" in result.stderr


def test_cli_off_does_not_require_summary(tmp_path: Path) -> None:
    result = _run(tmp_path, mode="off")
    assert result.returncode == 0
    assert "SKIP" in result.stdout


def test_issue_is_not_hard_gate() -> None:
    registry = json.loads((_SCRIPTS / "scanner_registry.json").read_text(encoding="utf-8"))
    assert scanner.ISSUE_CODE not in set(registry.get("hard_gate_codes", []))
