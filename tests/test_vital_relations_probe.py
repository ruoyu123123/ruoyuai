# -*- coding: utf-8 -*-
"""vital_relations_probe R22 W10 Batch-FF · P2 · CBT 7 类标记探针"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import vital_relations_probe as vrp  # noqa: E402


def test_count_relation_hits_basic():
    text = "他就是英雄。因为信念坚定。过去的事就让它过去吧。"
    hits = vrp.count_relation_hits(text)
    # identity（就是）/ causation（因为）/ time（过去）
    assert hits["identity"] >= 1
    assert hits["causation"] >= 1
    assert hits["time"] >= 1


def test_relation_distribution_sums_to_one():
    hits = {"identity": 2, "causation": 1, "time": 1,
            "change": 0, "part_whole": 0, "intentionality": 0, "analogy": 0}
    dist = vrp.relation_distribution(hits)
    s = sum(dist.values())
    assert abs(s - 1.0) < 0.01


def test_empty_text_uniform_distribution():
    hits = {c: 0 for c in vrp.CATEGORIES}
    dist = vrp.relation_distribution(hits)
    # 空 → 均匀
    for c in vrp.CATEGORIES:
        assert abs(dist[c] - 1.0 / len(vrp.CATEGORIES)) < 0.01


def test_probe_returns_schema():
    text = "他就是英雄。因为信念坚定。过去化作此刻。"
    out = vrp.probe(text)
    assert out["schema_version"] == "1.0"
    assert out["total_hits"] >= 3
    assert "relation_distribution" in out
    assert "top_compressed_pairs" in out
    assert out["lexicon_placeholder"] is True


def test_probe_with_input_spaces_cooccurrence():
    text = "战士就是诗人。诗人因为战场而流泪。" * 5
    out = vrp.probe(text, input_a_lexemes=["战士", "战场"], input_b_lexemes=["诗人", "流泪"])
    # 应找到 (战士, 诗人) 共现
    pairs = out["top_compressed_pairs"]
    assert any(p["a"] in ("战士", "战场") and p["b"] in ("诗人", "流泪") for p in pairs)


def test_probe_density_per_kcjk():
    text = "他就是英雄。" * 100
    out = vrp.probe(text)
    assert out["vital_relations_density"] > 0


def test_probe_no_input_spaces_returns_empty_pairs():
    text = "他就是英雄。"
    out = vrp.probe(text)
    assert out["top_compressed_pairs"] == []


def test_lexicon_marked_placeholder():
    assert vrp.VITAL_RELATIONS_LEXICON.get("_placeholder") is True


def test_cli_runs(tmp_path=None):
    import subprocess, os
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text("他就是英雄。因为信念。过去变成现在。", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "vital_relations_probe.py"), str(p)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["schema_version"] == "1.0"


def test_cli_with_lexemes():
    import subprocess, os
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text("战士就是诗人。诗人因为战场而流泪。" * 5, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "vital_relations_probe.py"), str(p),
         "--a-lexemes", "战士,战场", "--b-lexemes", "诗人,流泪"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert rep["signature_lexemes_hits"]["a_total"] > 0
    assert rep["signature_lexemes_hits"]["b_total"] > 0


def test_categories_complete():
    expected = ("identity", "causation", "time", "change", "part_whole",
                "intentionality", "analogy")
    for c in expected:
        assert c in vrp.CATEGORIES
