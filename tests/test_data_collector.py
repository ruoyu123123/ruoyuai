# 🔴 2026-06-29 可成长NN架构 · 数据飞轮测试
"""test_data_collector.py — ClusterDataCollector 测试。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "ml" / "flywheel"))
from data_collector import (
    ClusterDataCollector, enabled, _text_hash, _strip_changes,
    _split_paragraphs, CODE_TO_MODEL,
)


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("RUOYU_DATA_FLYWHEEL", "1")
    import data_collector as dc
    monkeypatch.setattr(dc, "_POOL_DIR", tmp_path / "pool")
    monkeypatch.setattr(dc, "_MANIFEST", tmp_path / "pool" / "data_manifest.json")
    yield


def test_enabled_gate(monkeypatch):
    monkeypatch.delenv("RUOYU_DATA_FLYWHEEL", raising=False)
    assert not enabled()
    monkeypatch.setenv("RUOYU_DATA_FLYWHEEL", "1")
    assert enabled()


def test_disabled_returns_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("RUOYU_DATA_FLYWHEEL", raising=False)
    c = ClusterDataCollector(str(tmp_path), "cluster_001")
    result = c.collect()
    assert result["skipped"] is True


def test_text_hash_deterministic():
    h1 = _text_hash("你好世界")
    h2 = _text_hash("你好世界")
    assert h1 == h2
    assert len(h1) == 16


def test_strip_changes():
    text = "正文内容\n---CHANGES---\n变更数据"
    assert _strip_changes(text) == "正文内容"
    text2 = "正文内容\n---CHANGES_FACTUAL---\n变更数据"
    assert _strip_changes(text2) == "正文内容"


def test_split_paragraphs():
    text = "这是一个足够长的中文段落用来测试分段功能的哦\n短短\n另一个足够长的中文段落用来测试分段功能"
    paras = _split_paragraphs(text, min_cjk=6)
    assert len(paras) == 2


def test_collect_paragraphs(tmp_path):
    proj = tmp_path / "novel"
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    draft = draft_dir / "cluster_001_draft.txt"
    draft.write_text(
        "这是第一个段落，有足够多的中文字来通过最小长度过滤器的检测。\n"
        "这是第二个段落，同样也有足够多的中文字来通过最小长度的过滤。\n"
        "短\n",
        encoding="utf-8",
    )
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["paragraphs"] == 2


def test_collect_weak_labels(tmp_path):
    proj = tmp_path / "novel"
    audit_dir = proj / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True)
    report = audit_dir / "cluster_001_audit.json"
    report.write_text(json.dumps({
        "issues": [
            {"code": "SEMANTIC_APHORISM", "paragraph": "这是一段含有格言体AI腔调的文本，需要被检测出来进行分析"},
            {"code": "HOOK_WEAK", "paragraph": "这是一段钩子力度不足的章末文本，需要被检测出来进行分析"},
            {"code": "UNKNOWN_CODE", "paragraph": "不在映射表中的代码"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["weak_labels"] == 2


def test_collect_fix_pairs(tmp_path):
    proj = tmp_path / "novel"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    fixer_log = db / "cluster_001_fixer_log.json"
    fixer_log.write_text(json.dumps({
        "fixes": [
            {"before": "与此同时他感到无比的愤怒和不安的情绪", "after": "他一拳砸在桌上"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["fix_pairs"] == 1


def test_collect_strong_labels(tmp_path):
    proj = tmp_path / "novel"
    ch_dir = proj / "章节" / "cluster_001_draft"
    ch_dir.mkdir(parents=True)
    changes = ch_dir / "cluster_001_changes.json"
    changes.write_text(json.dumps({
        "self_eval": {
            "waivers": [
                {"code": "SEMANTIC_APHORISM",
                 "reason": "作者风格确实如此",
                 "paragraph": "这是一段被豁免的文本，已确认符合作者风格，不需要修改"},
            ],
        },
    }, ensure_ascii=False), encoding="utf-8")
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["strong_labels"] == 1


def test_dedup_same_text(tmp_path):
    proj = tmp_path / "novel"
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    same_text = "这是完全相同的段落文本用来测试去重功能是否正常工作"
    draft = draft_dir / "cluster_001_draft.txt"
    draft.write_text(f"{same_text}\n{same_text}\n", encoding="utf-8")
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["paragraphs"] == 1


def test_manifest_updated(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "cluster_001_draft.txt").write_text(
        "这是一个测试段落，有足够的中文字来通过最小长度过滤器的检查。\n",
        encoding="utf-8",
    )
    c = ClusterDataCollector(str(proj), "cluster_001")
    c.collect()
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["total_records"] >= 1
    assert manifest["last_update"] is not None


def test_code_to_model_coverage():
    assert "SEMANTIC_APHORISM" in CODE_TO_MODEL
    assert CODE_TO_MODEL["SEMANTIC_APHORISM"] == "ai_tone"
    assert CODE_TO_MODEL["HOOK_WEAK"] == "hook_strength"
    assert CODE_TO_MODEL["COHERENCE_BREAK"] == "coherence"


def test_empty_project(tmp_path):
    c = ClusterDataCollector(str(tmp_path / "nonexistent"), "cluster_001")
    result = c.collect()
    assert result["total"] == 0
