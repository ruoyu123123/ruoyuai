"""Cluster scene/POV diversity scanner tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import cross_cluster_scene_pov_diversity_aggregate as scanner  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _project(path: Path, povs: list[str]) -> None:
    """povs：每个 cluster 的主视角角色名，经 characters[0] 兜底通道流入 _cluster_observations。"""
    clusters = [
        cluster_record(f"cluster_{index:03d}", characters=[pov] if pov else [])
        for index, pov in enumerate(povs, start=1)
    ]
    write_cluster_summary(path, clusters)


def test_last_n_is_cluster_window(tmp_path):
    _project(tmp_path, [f"pov-{index}" for index in range(12)])
    observations = scanner._cluster_observations(tmp_path, 3)
    assert [item["cluster_id"] for item in observations] == ["cluster_010", "cluster_011", "cluster_012"]


def test_pov_overconcentrated_is_advisory(tmp_path):
    _project(tmp_path, ["陆参"] * 6)
    finding = next(item for item in scanner._scan(scanner._cluster_observations(tmp_path, 10))
                   if item["code"] == "POV_OVERCONCENTRATED")
    assert finding["severity"] == "advisory"


def test_source_has_no_mode_or_chapter_fallback():
    source = (ROOT / "core" / "scripts" / "cross_cluster_scene_pov_diversity_aggregate.py").read_text(encoding="utf-8")
    assert "CLUSTER_MODE" not in source
    assert 'project_root / "章节"' not in source


def test_source_has_no_dead_scene_type_detection():
    """scene_type 在 故事块摘要.json 合同下无落盘通道（不在 CLUSTER_FIELDS，嵌套 chapters
    也不存在）——检测逻辑已删，回归锁防止死代码复活。"""
    source = (ROOT / "core" / "scripts" / "cross_cluster_scene_pov_diversity_aggregate.py").read_text(encoding="utf-8")
    assert "SCENE_TYPE_RUN" not in source
    assert "SCENE_TYPE_LOW_DIVERSITY" not in source
    assert "scene_type" not in source
    assert '"chapters"' not in source
