"""cluster 历史检索测试。"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from cluster_summary_fixtures import cluster_record, write_cluster_summary

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import rag_retriever as rr  # noqa: E402


def _write_draft(root: Path, cluster_id: str, body: str) -> None:
    key = cluster_id.removeprefix("cluster_")
    folder = root / "章节" / f"cluster_{key}_draft"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"cluster_{key}_draft.txt").write_text(body, encoding="utf-8")


def _write_events(root: Path, briefs: list[dict]) -> None:
    database = root / "_数据库"
    database.mkdir(parents=True, exist_ok=True)
    (database / "事件簇.json").write_text(
        json.dumps({"clusters": briefs}, ensure_ascii=False), encoding="utf-8"
    )


def _project(root: Path, history: list[tuple[str, str]], current: dict) -> None:
    records = []
    for cluster_id, summary in history:
        records.append(cluster_record(cluster_id, summary=summary))
        _write_draft(root, cluster_id, summary + "。这是完整故事块正文。")
    write_cluster_summary(root, records)
    _write_events(root, [current])


def _current(cluster_id: str = "cluster_003", scope: str = "剑修在剑冢追查剑意失控") -> dict:
    return {
        "cluster_id": cluster_id,
        "scope_summary": scope,
        "characters_focus": ["剑修"],
        "anchor_props": ["断剑"],
        "scene_storyboard": [{
            "characters": ["剑修"], "location": "剑冢", "focal_character": "剑修",
        }],
    }


def _char_freq_embedding(text: str, dim: int = 64) -> list[float]:
    vector = [0.0] * dim
    for char in text:
        vector[ord(char) % dim] += 1.0
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector] if norm else vector


def test_chinese_tokens_and_tfidf_helpers() -> None:
    tokens = rr._chinese_tokens("剑光闪过")
    assert "剑光" in tokens
    assert "剑光闪" in tokens
    assert "剑光闪过" in tokens
    assert rr._chinese_tokens("。，！？") == []
    vectors, frequencies = rr._tfidf_vectors(["剑光剑光", "剑光闪过"])
    assert len(vectors) == 2
    assert frequencies["剑光"] == 2
    assert abs(rr._cosine(vectors[0], vectors[0]) - 1.0) < 1e-9
    assert rr._cosine({}, vectors[0]) == 0.0


def test_snippet_uses_complete_lines() -> None:
    text = "第一行内容\n第二行内容\n第三行非常长非常长非常长"
    assert rr._snippet(text, length=12) == "第一行内容\n第二行内容"
    assert rr._snippet("这是一整段超过限制的文字", length=4) == "这是一整"


def test_expand_query_uses_current_cluster_brief(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [])
    _write_events(tmp_path, [_current("cluster_001")])
    groups = rr.expand_query_from_brief(tmp_path, "cluster_001")
    assert groups
    assert any("剑修" in group for group in groups)
    assert any("断剑" in group for group in groups)


def test_retrieve_tfidf_ranks_relevant_cluster(tmp_path: Path) -> None:
    _project(tmp_path, [
        ("cluster_001", "剑修在剑冢淬炼剑心，剑光横空"),
        ("cluster_002", "厨房里炖着汤水，柴米油盐气息温暖"),
    ], _current())
    results = rr.retrieve_tfidf(tmp_path, "cluster_003", top_k=2, use_mmr=False)
    assert results
    assert results[0]["cluster_id"] == "cluster_001"
    assert set(results[0]) >= {"cluster_id", "score", "snippet", "usage_hint"}
    assert "chapter" not in results[0]


def test_retrieval_excludes_current_and_future_clusters(tmp_path: Path) -> None:
    _project(tmp_path, [
        ("cluster_001", "剑光剑意剑冢淬炼"),
        ("cluster_003", "剑光剑意剑冢淬炼"),
        ("cluster_005", "剑光剑意剑冢淬炼"),
    ], _current())
    results = rr.retrieve_tfidf(tmp_path, "cluster_003", top_k=5, use_mmr=False)
    assert {row["cluster_id"] for row in results} == {"cluster_001"}


def test_retrieval_uses_top_level_cluster_summary_for_snippet(tmp_path: Path) -> None:
    _project(tmp_path, [
        ("cluster_001", "剑光剑意剑冢决战的关键故事块"),
    ], _current("cluster_002", "剑光剑意剑冢"))
    results = rr.retrieve_tfidf(tmp_path, "cluster_002", use_mmr=False)
    assert results[0]["cluster_id"] == "cluster_001"
    assert "剑光" in results[0]["snippet"]


def test_no_historical_cluster_returns_empty(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [])
    assert rr.retrieve_tfidf(tmp_path, "cluster_001") == []


def test_missing_current_brief_is_fatal(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [cluster_record("cluster_001")])
    _write_draft(tmp_path, "cluster_001", "历史故事块正文")
    _write_events(tmp_path, [])
    with pytest.raises(ValueError, match="缺少当前 brief"):
        rr.retrieve_tfidf(tmp_path, "cluster_002")


def test_missing_historical_draft_is_fatal(tmp_path: Path) -> None:
    write_cluster_summary(tmp_path, [cluster_record("cluster_001")])
    _write_events(tmp_path, [_current("cluster_002")])
    with pytest.raises(FileNotFoundError, match="cluster 终稿不存在"):
        rr.retrieve_tfidf(tmp_path, "cluster_002")


def test_accepts_string_project_path(tmp_path: Path) -> None:
    _project(tmp_path, [("cluster_001", "剑光剑意剑冢")], _current("cluster_002"))
    results = rr.retrieve_tfidf(str(tmp_path), "cluster_002", use_mmr=False)
    assert results[0]["cluster_id"] == "cluster_001"


def test_embedding_mode_requires_backend(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, [("cluster_001", "剑光剑意剑冢")], _current("cluster_002"))
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    with pytest.raises(RuntimeError, match="后端未就绪"):
        rr.retrieve_embedding(tmp_path, "cluster_002", use_mmr=False)


def test_embedding_mode_uses_semantic_backend(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, [
        ("cluster_001", "剑修在剑冢淬炼剑心剑意剑光浩荡"),
        ("cluster_002", "厨房里炖着汤水柴米油盐生活温暖"),
    ], _current())
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    prefetched: list[list[str]] = []
    monkeypatch.setattr(
        embedding_store,
        "prefetch_content_embeddings",
        lambda texts: prefetched.append(list(texts)),
    )
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _char_freq_embedding)
    results = rr.retrieve_embedding(tmp_path, "cluster_003", top_k=2, use_mmr=False)
    assert len(prefetched) == 1
    assert len(prefetched[0]) == 3
    assert results
    assert all(row["mode"] == "embedding" for row in results)


def test_embedding_dimension_mismatch_is_fatal(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, [("cluster_001", "剑光剑意剑冢淬炼")], _current("cluster_002"))
    import embedding_store
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings", lambda texts: None)

    def _mixed(text: str) -> list[float]:
        return [0.1] * (8 if "追查" in text else 64)

    monkeypatch.setattr(embedding_store, "compute_content_embedding", _mixed)
    with pytest.raises(RuntimeError, match="维度不一致"):
        rr.retrieve_embedding(tmp_path, "cluster_002", use_mmr=False)


def test_main_usage_error_exits_two(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["rag_retriever.py"])
    with pytest.raises(SystemExit) as exc:
        rr.main()
    assert exc.value.code == 2


def test_main_prints_cluster_results(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path, [("cluster_001", "剑光剑意剑冢淬炼")], _current("cluster_002"))
    monkeypatch.setattr(sys, "argv", [
        "rag_retriever.py", str(tmp_path), "cluster_002", "--top-k", "2", "--no-mmr",
    ])
    output = io.StringIO()
    with redirect_stdout(output):
        rr.main()
    parsed = json.loads(output.getvalue())
    assert parsed[0]["cluster_id"] == "cluster_001"
