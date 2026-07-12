"""cluster 三层记忆检索测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import memory_layer as mod  # noqa: E402


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_draft(root: Path, cluster_id: str, text: str) -> None:
    key = cluster_id.removeprefix("cluster_")
    folder = root / "章节" / f"cluster_{key}_draft"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"cluster_{key}_draft.txt").write_text(text, encoding="utf-8")


def _project(root: Path, summaries: list[str]) -> list[dict]:
    records = [
        cluster_record(
            f"cluster_{index:03d}",
            summary=summary,
            emotion={"value": index},
        )
        for index, summary in enumerate(summaries, start=1)
    ]
    write_cluster_summary(root, records)
    for record in records:
        _write_draft(root, record["cluster_id"], f"{record['summary']}。完整故事块正文。")
    return records


def test_tokens_and_tfidf_helpers() -> None:
    tokens = mod._tokens("许遥的父亲在码头失踪")
    assert "许遥" in tokens
    assert mod._cosine({"a": 1}, {"a": 1}) == 1.0
    assert mod._cosine({"a": 1}, {"b": 1}) == 0.0
    vector = mod._tfidf_vec("许遥父亲", {"许遥": 2.0})
    assert vector["许遥"] > 0


def test_constructor_requires_canonical_cluster_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cluster_NNN"):
        mod.MemoryLayer(tmp_path, "1")


def test_recent_cluster_memory_reads_complete_cluster_drafts(tmp_path: Path) -> None:
    _project(tmp_path, ["第一块", "第二块", "第三块", "第四块"])
    layer = mod.MemoryLayer(tmp_path, "cluster_005")
    rows = layer._load_cluster_memory(window=2)
    assert [row["cluster_id"] for row in rows] == ["cluster_003", "cluster_004"]
    assert all(row["layer"] == "cluster" for row in rows)
    assert "完整故事块正文" in rows[-1]["content"]


def test_missing_cluster_draft_is_fatal(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [cluster_record("cluster_001")])
    layer = mod.MemoryLayer(tmp_path, "cluster_002")
    with pytest.raises(FileNotFoundError, match="cluster 终稿不存在"):
        layer._load_cluster_memory()


def test_summary_memory_uses_top_level_cluster_records(tmp_path: Path) -> None:
    _project(tmp_path, ["许遥登场", "父亲失踪", "当前块不应进入历史"])
    rows = mod.MemoryLayer(tmp_path, "cluster_003")._load_summary_memory()
    assert [row["cluster_id"] for row in rows] == ["cluster_001", "cluster_002"]
    assert rows[0] == {
        "layer": "summary",
        "cluster_id": "cluster_001",
        "content": "许遥登场",
        "emotion": {"value": 1},
    }


def test_archive_memory_extracts_cluster_sourced_character_state(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [])
    database = tmp_path / "_数据库"
    _write_json(database / "人物卡.json", {"characters": [{
        "name": "许遥",
        "locked_facts": ["父亲失踪", "会武术", "怕水", "不注入的第四条"],
        "growth_arc": [
            {"state": "懦弱", "_source_cluster": "cluster_001"},
            {"state": "觉醒", "_source_cluster": "cluster_004"},
        ],
    }]})
    _write_json(database / "世界观.json", {"entries": [{
        "id": "云霄宗",
        "keywords": ["仙门", "御剑", "丹药", "灵脉", "护山阵", "第六项"],
    }]})
    rows = mod.MemoryLayer(tmp_path, "cluster_005")._load_archive_memory()
    character = next(row for row in rows if row["source"] == "人物卡")
    world = next(row for row in rows if row["source"] == "世界观")
    assert "父亲失踪; 会武术; 怕水" in character["content"]
    assert "最新状态cluster_004: 觉醒" in character["content"]
    assert "不注入的第四条" not in character["content"]
    assert "第六项" not in world["content"]


def test_search_tfidf_ranks_relevant_cluster(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, [
        "许遥的父亲在码头失踪了",
        "云霄宗弟子御剑飞行修炼",
        "许遥决定去码头寻找父亲下落",
    ])
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    results = mod.MemoryLayer(tmp_path, "cluster_004").search("许遥的父亲", top_k=2)
    assert results
    assert results[0]["cluster_id"] in {"cluster_001", "cluster_003"}
    assert results == sorted(results, key=lambda row: -row["score"])
    assert all(len(row["content"]) <= 200 for row in results)


def test_search_empty_project_returns_empty(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [])
    assert mod.MemoryLayer(tmp_path, "cluster_001").search("任意查询") == []


def test_semantic_search_prefetches_cluster_memories_once(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, ["许遥的父亲失踪了", "云霄宗弟子御剑飞行修炼"])
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        embedding_store,
        "prefetch_content_embeddings",
        lambda texts: calls.append(list(texts)),
    )

    def _embed(text: str) -> list[float]:
        return [1.0, 0.0] if "许遥" in text or "阿光" in text else [0.0, 1.0]

    monkeypatch.setattr(embedding_store, "compute_content_embedding", _embed)
    results = mod.MemoryLayer(tmp_path, "cluster_003").search("阿光他爹没", top_k=2)
    assert len(calls) == 1
    assert any(row.get("method") == "semantic" for row in results)
    assert "许遥的父亲失踪了" in results[0]["content"]


def test_semantic_encoder_failure_is_fatal(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, ["许遥的父亲在码头失踪了"])
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings", lambda texts: None)
    monkeypatch.setattr(
        embedding_store,
        "compute_content_embedding",
        lambda text: (_ for _ in ()).throw(RuntimeError("编码失败")),
    )
    with pytest.raises(RuntimeError, match="语义编码失败"):
        mod.MemoryLayer(tmp_path, "cluster_002").search("许遥的父亲")


def test_extract_entities_is_sorted(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [])
    database = tmp_path / "_数据库"
    _write_json(database / "人物卡.json", {"characters": [{"name": "许遥"}, {"name": "林婉"}]})
    _write_json(database / "世界观.json", {"entries": [{"keywords": ["云霄宗", "山"]}]})
    _write_json(database / "道具.json", {"items": [{"name": "断水剑"}]})
    rows = mod.MemoryLayer(tmp_path, "cluster_001").extract_entities(
        "许遥许遥许遥在云霄宗拿到断水剑，林婉旁观。"
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["许遥"]["count"] == 3
    assert by_name["断水剑"]["type"] == "item"
    assert "山" not in by_name
    assert [row["count"] for row in rows] == sorted(
        (row["count"] for row in rows), reverse=True
    )


def test_build_and_stats_report_cluster_counts(tmp_path: Path) -> None:
    _project(tmp_path, ["第一块", "第二块"])
    _write_json(tmp_path / "_数据库" / "人物卡.json", {
        "characters": [{"name": "许遥", "locked_facts": ["父亲失踪"]}],
    })
    layer = mod.MemoryLayer(tmp_path, "cluster_003")
    expected = {
        "cluster_count": 2,
        "summary_count": 2,
        "archive_count": 1,
        "total": 5,
    }
    assert layer.build() == expected
    assert layer.stats() == expected
