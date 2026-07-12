"""style_drift_scan 的 cluster-native 回归。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import style_drift_scan as scanner  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _draft(project: Path, cluster_id: str, text: str) -> Path:
    path = project / "章节" / f"{cluster_id}_draft" / f"{cluster_id}_draft.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_json_returns_default_for_missing_or_broken(tmp_path: Path) -> None:
    assert scanner.load_json(tmp_path / "missing.json", {}) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    assert scanner.load_json(broken, []) == []


def test_find_cluster_drafts_follows_summary_order(tmp_path: Path) -> None:
    clusters = [cluster_record("cluster_001"), cluster_record("cluster_002")]
    first = _draft(tmp_path, "cluster_001", "一")
    second = _draft(tmp_path, "cluster_002", "二")

    assert scanner.find_cluster_drafts(tmp_path, clusters) == [
        ("cluster_001", first),
        ("cluster_002", second),
    ]


def test_find_cluster_drafts_rejects_missing_draft(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scanner.find_cluster_drafts(tmp_path, [cluster_record("cluster_001")])


def test_scan_anchor_frequency_counts_complete_cluster(tmp_path: Path) -> None:
    first = _draft(tmp_path, "cluster_001", "绯红月光照下，绯红月光又亮了一次。")
    second = _draft(tmp_path, "cluster_002", "没有锚点。")

    result = scanner.scan_anchor_frequency(
        [("cluster_001", first), ("cluster_002", second)], ["绯红月光"]
    )

    assert result == {"绯红月光": {"cluster_001": 2, "cluster_002": 0}}


def test_cluster_window_strategy_and_severity() -> None:
    frequency = {
        "绯红月光": {
            "cluster_001": 1,
            "cluster_002": 1,
            "cluster_003": 1,
        }
    }
    strategy = [{"元素": "绯红月光", "策略": "每3个故事块不超过2次"}]

    violations = scanner.check_strategy_violations(frequency, strategy)

    assert violations
    assert violations[-1]["clusters"] == [
        "cluster_001", "cluster_002", "cluster_003"
    ]
    assert violations[-1]["severity"] == "mild"


def test_per_cluster_strategy() -> None:
    frequency = {"颤抖": {"cluster_001": 1, "cluster_002": 3}}
    strategy = [{"元素": "颤抖", "策略": "每个故事块不超过1次"}]

    violations = scanner.check_strategy_violations(frequency, strategy)

    assert len(violations) == 1
    assert violations[0]["cluster_id"] == "cluster_002"


def test_chapter_worded_rule_is_not_accepted() -> None:
    frequency = {"颤抖": {"cluster_001": 9}}
    strategy = [{"元素": "颤抖", "策略": "每章不超过1次"}]
    assert scanner.check_strategy_violations(frequency, strategy) == []


def test_opening_distribution_uses_truth_check() -> None:
    clusters = [
        cluster_record(
            "cluster_001", truth_check={"detected_opening_type": "动作"}
        ),
        cluster_record(
            "cluster_002", truth_check={"detected_opening_type": "动作"}
        ),
        cluster_record(
            "cluster_003", truth_check={"detected_opening_type": "对话"}
        ),
    ]

    result = scanner.scan_opening_types_distribution(clusters)

    assert result["total"] == 3
    assert result["type_counts"] == {"动作": 2, "对话": 1}
    assert result["cluster_to_type"]["cluster_003"] == "对话"


def test_repeated_opening_runs_are_cluster_sequences() -> None:
    runs = scanner.find_repeated_opening_runs({
        "cluster_001": "动作",
        "cluster_002": "动作",
        "cluster_003": "动作",
        "cluster_004": "对话",
    })
    assert runs == [{
        "clusters": ["cluster_001", "cluster_002", "cluster_003"],
        "opening_type": "动作",
    }]


def test_cli_emits_cluster_report(tmp_path: Path, capsys) -> None:
    clusters = [cluster_record(
        "cluster_001", truth_check={"detected_opening_type": "动作"}
    )]
    write_cluster_summary(tmp_path, clusters)
    _draft(tmp_path, "cluster_001", "月光落下。")
    (tmp_path / "_数据库" / "作者风格.json").write_text(
        json.dumps({"cross_cluster_diversity": {
            "env_anchor_high_risk_elements": []
        }}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        scanner.main([str(tmp_path)])

    assert exc.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["clusters_scanned"] == ["cluster_001"]
    assert "chapters_scanned" not in report
