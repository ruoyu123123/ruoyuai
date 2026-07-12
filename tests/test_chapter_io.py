"""章节格式层唯一布局与 self_eval 合同回归。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import chapter_io as cio  # noqa: E402


def test_normalize_keeps_only_self_eval():
    source = {
        "self_eval": {"waivers": [{"code": "STYLE_X", "reason": "场景需要"}]},
        "factual": {"locked_facts": ["不得进入格式层"]},
        "schema_version": "old",
    }
    normalized = cio.normalize_changes(source)
    assert set(normalized) == {"self_eval"}
    assert normalized["self_eval"]["waivers"][0]["code"] == "STYLE_X"


def test_normalize_moves_top_level_metadata_into_self_eval():
    normalized = cio.normalize_changes({
        "ecas_metadata": {"cluster_id": "cluster_001"},
        "self_eval": None,
    })
    assert normalized == {"self_eval": {
        "ecas_metadata": {"cluster_id": "cluster_001"},
        "waivers": [],
        "uncertainty_flags": [],
    }}


@pytest.mark.parametrize("value", [None, "bad", [], 3])
def test_normalize_non_object_returns_self_eval_skeleton(value):
    assert cio.normalize_changes(value) == {
        "self_eval": {"waivers": [], "uncertainty_flags": []}}


def test_read_changes_missing_or_invalid_returns_skeleton(tmp_path):
    skeleton = {"self_eval": {"waivers": [], "uncertainty_flags": []}}
    assert cio.read_changes(tmp_path, 1) == skeleton
    path = cio.changes_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert cio.read_changes(tmp_path, 1) == skeleton


def test_write_changes_strips_objective_state(tmp_path):
    path = cio.write_changes(tmp_path, 2, {
        "self_eval": {"waivers": [], "moves_used": []},
        "factual": {"locked_facts": ["禁止"]},
    })
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"self_eval": {
        "waivers": [], "moves_used": [], "uncertainty_flags": []}}


def test_standard_body_roundtrip_and_no_layout_fallback(tmp_path):
    standard = cio.write_body(tmp_path, 3, "正文\n\n")
    assert standard == tmp_path / "章节" / "第003章" / "第003章.txt"
    assert cio.read_body(tmp_path, 3) == "正文"
    (tmp_path / "第004章.txt").write_text("旧布局", encoding="utf-8")
    assert cio.find_body_file(tmp_path, 4) is None
    assert cio.find_chapter_dir(tmp_path, 4) is None


def test_body_does_not_parse_embedded_changes(tmp_path):
    text = "正文\n---CHANGES_FACTUAL---\n旧数据"
    cio.write_body(tmp_path, 5, text)
    assert cio.read_body(tmp_path, 5) == text


def test_missing_body_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cio.read_body(tmp_path, 99)


def test_word_counts():
    assert cio.count_words("你好， world\n") == 8
    assert cio.count_cjk("字㐀abc") == 2


def test_cli_wordcount(tmp_path):
    cio.write_body(tmp_path, 1, "你好，世界。abc")
    result = subprocess.run(
        [sys.executable, str(ROOT / "core" / "scripts" / "chapter_io.py"),
         "wordcount", str(tmp_path), "1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0
    assert "count_cjk)=4" in result.stdout
