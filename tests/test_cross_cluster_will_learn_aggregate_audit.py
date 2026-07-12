"""Cluster will-learn scanner tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "core" / "scripts" / "cross_cluster_will_learn_aggregate.py"

sys.path.insert(0, str(ROOT / "tests"))
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _project(path: Path, current: int, learn_at: str, keywords: list[str] | None = None) -> None:
    database = path / "_数据库"
    database.mkdir(parents=True)
    clusters = [
        cluster_record(f"cluster_{index:03d}",
                       text_keyword_set=list(keywords or []) if index == current else [])
        for index in range(1, current + 1)
    ]
    write_cluster_summary(path, clusters)
    (database / "人物卡.json").write_text(json.dumps({"characters": [{
        "name": "陆参", "knowledge": {"will_learn": [{
            "id": "secret", "fact": "太子的真实身份", "learn_at_cluster": learn_at,
        }]},
    }]}), encoding="utf-8")


def _run(path: Path) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(SCANNER), str(path)], capture_output=True, text=True)
    report_path = sorted((path / "_数据库" / ".cross_cluster_scan").glob("will_learn_*.json"))[-1]
    return result.returncode, json.loads(report_path.read_text(encoding="utf-8"))


def test_overdue_is_cluster_delta(tmp_path):
    _project(tmp_path, current=3, learn_at="cluster_002")
    code, report = _run(tmp_path)
    finding = next(item for item in report["findings"] if item["code"] == "WILL_LEARN_OVERDUE")
    assert code == 2
    assert finding["overdue_by_clusters"] == 1


def test_near_due_without_hint_is_advisory(tmp_path):
    _project(tmp_path, current=2, learn_at="cluster_003")
    code, report = _run(tmp_path)
    assert code == 1
    assert "WILL_LEARN_NEVER_HINTED" in {item["code"] for item in report["findings"]}


def test_keyword_hint_suppresses_advisory(tmp_path):
    _project(tmp_path, current=2, learn_at="cluster_003", keywords=["太子的真实身份"])
    code, report = _run(tmp_path)
    assert code == 0
    assert report["findings"] == []


def test_source_has_no_mode_or_chapter_fallback():
    source = SCANNER.read_text(encoding="utf-8")
    assert "CLUSTER_MODE" not in source
    assert 'project_root / "章节"' not in source
