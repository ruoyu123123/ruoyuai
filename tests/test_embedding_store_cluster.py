"""embedding_store 的 cluster 索引合同。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import embedding_store as store  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402


def _write_draft(root: Path, cluster_id: str, text: str) -> Path:
    directory = root / "章节" / f"{cluster_id}_draft"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{cluster_id}_draft.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_store_cluster_embedding_uses_cluster_artifact(tmp_path, monkeypatch):
    text = "林舟推开铁门。" * 80
    _write_draft(tmp_path, "cluster_001", text)
    monkeypatch.setattr(store, "compute_embedding", lambda chunk: [float(len(chunk))])
    monkeypatch.setattr(store, "embedding_method", lambda: "test")

    output = store.store_cluster_embedding(tmp_path, "cluster_001")
    value = json.loads(output.read_text(encoding="utf-8"))
    assert output.name == "cluster_001.json"
    assert value["scope"] == "cluster"
    assert value["cluster_id"] == "cluster_001"
    assert "ch" not in value
    assert value["n_chunks"] == len(value["chunks"])


def test_store_cluster_embedding_rejects_old_ids_and_missing_draft(tmp_path):
    with pytest.raises(ValueError, match="规范"):
        store.store_cluster_embedding(tmp_path, "1")
    with pytest.raises(FileNotFoundError, match="cluster 终稿不存在"):
        store.store_cluster_embedding(tmp_path, "cluster_001")


def test_character_dialogues_read_cluster_drafts(tmp_path):
    write_cluster_summary(tmp_path, [cluster_record("cluster_001")])
    database = tmp_path / "_数据库"
    (database / "人物卡.json").write_text(json.dumps({
        "characters": [{"id": "C_001", "name": "林舟", "name_aliases": []}]
    }, ensure_ascii=False), encoding="utf-8")
    _write_draft(tmp_path, "cluster_001", "林舟说：“今晚出发。”\n林舟抬手关灯。")

    dialogues = store._extract_character_dialogues(tmp_path, "林舟")
    assert dialogues == ["今晚出发。"]
