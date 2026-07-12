"""Cluster emotion-pattern scanner tests."""

from __future__ import annotations

import json
import sys
import types
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import cross_cluster_emotion_pattern_aggregate as scanner  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _project(project: Path, observations: list[dict[str, int]], character: str = "陆参") -> None:
    clusters = [
        cluster_record(f"cluster_{index:03d}", char_emotion_counts={character: counts})
        for index, counts in enumerate(observations, start=1)
    ]
    write_cluster_summary(project, clusters)


def _run(project: Path, character: str = "陆参") -> dict:
    saved = sys.argv
    sys.argv = ["emotion", str(project), "--characters", character]
    try:
        try:
            scanner.main()
        except SystemExit:
            pass
    finally:
        sys.argv = saved
    path = sorted((project / "_数据库" / ".cross_cluster_scan").glob("emotion_pattern_*.json"))[-1]
    return json.loads(path.read_text(encoding="utf-8"))


def test_scattered_dominance_does_not_report_run(tmp_path):
    values = [{"calm": 3} if index % 2 else {"anxious": 3} for index in range(1, 14)]
    _project(tmp_path, values)
    report = _run(tmp_path)
    assert "EMOTION_RUN_TOO_LONG" not in {item["code"] for item in report["findings"]}


def test_six_consecutive_clusters_report(tmp_path):
    _project(tmp_path, [{"calm": 3}] * 6)
    report = _run(tmp_path)
    finding = next(item for item in report["findings"] if item["code"] == "EMOTION_RUN_TOO_LONG")
    assert finding["consecutive_clusters"] == [f"cluster_{index:03d}" for index in range(1, 7)]
    assert finding["severity"] == "advisory"


def test_model_valence_classifies_nearest_emotion(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", types.SimpleNamespace(
        predict_batch=lambda texts: [{"valence": 0.95} for _ in texts]
    ))
    detail = scanner.detect_emotions_for_char_detail("陆参笑了。陆参转身。", "陆参")
    assert detail["source"] == "model_vad"
    assert detail["counts"]["joyful"] == 2


def test_missing_model_uses_lexicon(monkeypatch):
    baseline = scanner.detect_emotions_for_char("陆参紧张得手心都是汗。", "陆参")
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", types.SimpleNamespace(
        predict_batch=lambda texts: [None for _ in texts]
    ))
    detail = scanner.detect_emotions_for_char_detail("陆参紧张得手心都是汗。", "陆参")
    assert detail["counts"] == baseline
    assert detail["source"] == "lexicon_fallback"


def test_no_mentions_returns_empty_counter():
    detail = scanner.detect_emotions_for_char_detail("平静的一段话。", "陆参")
    assert detail["source"] == "none"
    assert detail["counts"] == Counter()


def test_source_has_no_mode_or_disk_fallback():
    source = (ROOT / "core" / "scripts" / "cross_cluster_emotion_pattern_aggregate.py").read_text(encoding="utf-8")
    assert "CLUSTER_MODE" not in source
    assert 'project_root / "章节"' not in source
