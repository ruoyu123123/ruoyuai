# -*- coding: utf-8 -*-
"""summary_chapter_alignment_distribution_scanner R20 W9 Batch-AA · P1 · 摘要-章节概念质量分布(cross-cluster)
确定性·零依赖·零 LLM/零联网。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import summary_chapter_alignment_distribution_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "summary_chapter_alignment_distribution_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("SUMMARY_MASS_DISTRIBUTION_MODE", None)
    else:
        os.environ["SUMMARY_MASS_DISTRIBUTION_MODE"] = m


def _mk_project_with_summary(clusters_summary: dict, baseline=None) -> Path:
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(clusters_summary, ensure_ascii=False), encoding="utf-8")
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"slow_update": {"summary_mass_signature_band": baseline}},
                       ensure_ascii=False),
            encoding="utf-8")
    return proj


def test_compute_distribution_evenly():
    clusters = []
    for i in range(8):
        clusters.append({
            "cluster_id": f"cluster_{i+1:03d}",
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "scope_summary": "故事概要" * 50,
        })
    dist = mod.compute_distribution(clusters)
    assert dist["cluster_count"] == 8
    # 各 cluster summary 文字均等 → head/tail/mid share 应近似 0.25 / 0.25 / 0.50
    assert 0.20 <= dist["head_share"] <= 0.30
    assert 0.20 <= dist["tail_share"] <= 0.30


def test_compute_distribution_head_heavy():
    clusters = []
    # 前 2 个 cluster summary 很长·后面短
    for i in range(8):
        text_len = 200 if i < 2 else 10
        clusters.append({
            "cluster_id": f"cluster_{i+1:03d}",
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "scope_summary": "摘要" * text_len,
        })
    dist = mod.compute_distribution(clusters)
    assert dist["head_share"] > 0.40


def test_compute_distribution_tail_heavy():
    clusters = []
    for i in range(8):
        text_len = 10 if i < 6 else 300
        clusters.append({
            "cluster_id": f"cluster_{i+1:03d}",
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "scope_summary": "摘要" * text_len,
        })
    dist = mod.compute_distribution(clusters)
    assert dist["tail_share"] > 0.40


def test_main_skips_few_clusters():
    proj = _mk_project_with_summary({
        "schema_version": "v2.cluster",
        "clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3],
             "scope_summary": "短摘要"}
        ]
    })
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SUMMARY_MASS_DISTRIBUTION_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    assert "SKIP" in r.stdout


def test_main_off_mode():
    proj = _mk_project_with_summary({"schema_version": "v2.cluster", "clusters": []})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SUMMARY_MASS_DISTRIBUTION_MODE": "off",
             "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    assert "[OFF]" in r.stdout


def test_main_head_heavy_active_fires():
    clusters = []
    for i in range(8):
        text_len = 300 if i < 2 else 5
        clusters.append({
            "cluster_id": f"cluster_{i+1:03d}",
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "scope_summary": "摘要" * text_len,
        })
    proj = _mk_project_with_summary({
        "schema_version": "v2.cluster",
        "clusters": clusters
    })
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SUMMARY_MASS_DISTRIBUTION_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    # advisory 命中 → exit 1
    assert r.returncode == 1, r.stdout + "\n" + r.stderr
    # snapshot 文件应已写
    snap = proj / "_数据库" / ".cross_chapter_scan" / "summary_mass_distribution_snapshot.json"
    assert snap.exists()
    s = json.loads(snap.read_text(encoding="utf-8"))
    assert "SUMMARY_FIRST_QUARTER_OVERWEIGHT" in s["advisory_codes"]


def test_main_shadow_silent():
    clusters = []
    for i in range(8):
        text_len = 300 if i < 2 else 5
        clusters.append({
            "cluster_id": f"cluster_{i+1:03d}",
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "scope_summary": "摘要" * text_len,
        })
    proj = _mk_project_with_summary({
        "schema_version": "v2.cluster",
        "clusters": clusters
    })
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SUMMARY_MASS_DISTRIBUTION_MODE": "shadow",
             "PYTHONIOENCODING": "utf-8"})
    # shadow → exit 0 即使命中
    assert r.returncode == 0


def test_author_baseline_overrides_fallback():
    clusters = []
    for i in range(8):
        text_len = 300 if i < 2 else 5
        clusters.append({
            "cluster_id": f"cluster_{i+1:03d}",
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "scope_summary": "摘要" * text_len,
        })
    proj = _mk_project_with_summary({
        "schema_version": "v2.cluster",
        "clusters": clusters
    }, baseline={"head_share_max": 0.99, "tail_share_max": 0.99})
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(proj)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SUMMARY_MASS_DISTRIBUTION_MODE": "active",
             "PYTHONIOENCODING": "utf-8"})
    # 宽 baseline → 不报 → exit 0
    assert r.returncode == 0, r.stdout + "\n" + r.stderr


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("SUMMARY_MASS_DISTRIBUTION_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_cluster_summary_text_collects_all():
    c = {
        "scope_summary": "scope",
        "summary": "sum",
        "sub_summaries": ["s1", {"text": "s2"}, {"summary": "s3"}],
        "chapters": {
            "1": {"sub_summary": "ch1sub"},
            "2": {"summary": "ch2sum"},
        }
    }
    s = mod._cluster_summary_text(c)
    assert "scope" in s
    assert "s1" in s
    assert "s2" in s
    assert "ch1sub" in s
