# -*- coding: utf-8 -*-
"""作者金标准对比闸 replication_fidelity_check 回归（2026-06-04）。"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "scripts"))
import replication_fidelity_check as rf


def test_metrics_basic():
    t = "他睁开眼。\n\n他看了看四周，又挑了挑眉，慢悠悠地走了几步。\n\n卧槽这也行？！"
    m = rf._metrics(t)
    assert m["cjk"] > 0
    assert 0 < m["single_para_ratio"] <= 1
    assert m["excl_k"] >= 0 and m["ques_k"] >= 0
    assert m["para_mean"] > 0


def test_author_baseline_reads(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    (db / "作者风格.json").write_text(json.dumps({
        "quantitative": {
            "sentence_length": {"mean": 32.2},
            "paragraph_length_chars": {"mean": 35.6},
            "single_sentence_para_ratio": 0.79,
            "punctuation_density_per_1000": {"comma": {"mean": 59.9}, "exclamation": {"mean": 4.9}},
        }
    }, ensure_ascii=False), encoding="utf-8")
    b = rf._author_baseline(tmp_path)
    assert b is not None
    assert abs(b["sentence_mean"] - 32.2) < 0.01
    assert abs(b["excl_k"] - 4.9) < 0.01


def test_author_baseline_missing_returns_none(tmp_path):
    assert rf._author_baseline(tmp_path) is None


def test_comedy_punct_low_is_flaggable():
    """情绪标点严重偏低(感叹 0.2 vs 作者 4.9)落在 band 外 → 应可被标记为 comedy_engine。"""
    lo, hi = rf._BANDS["excl_k"]
    ratio = 0.2 / 4.9
    assert ratio < lo, "极低情绪标点应落 band 外"
    assert "excl_k" in rf._COMEDY_PUNCT
    assert "ques_k" in rf._COMEDY_PUNCT and "ellipsis_k" in rf._COMEDY_PUNCT


def test_in_band_not_flagged():
    """贴合作者的维度(段长 34.6 vs 35.6)落在 band 内 → 不标记。"""
    lo, hi = rf._BANDS["para_mean"]
    ratio = 34.6 / 35.6
    assert lo <= ratio <= hi
