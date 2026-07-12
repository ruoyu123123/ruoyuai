"""连续性聚合器的主角过滤与物件别名测试。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import cross_cluster_continuity_aggregate as scanner


def test_extract_keywords_filters_explicit_protagonist_only() -> None:
    keywords = scanner.extract_keywords("重黎 建木 绝顶", protagonist="重黎")
    assert "重黎" not in keywords
    assert "建木" in keywords
    assert "重黎" in scanner.extract_keywords("重黎 建木")


def test_get_protagonist_reads_canonical_character_list() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "_数据库"
        database.mkdir()
        (database / "人物卡.json").write_text(json.dumps({
            "characters": [
                {"id": "C_MAIN", "name": "重黎", "role": "主角"},
                {"id": "C_OTHER", "name": "颛顼", "role": "反派"},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        assert scanner.get_protagonist(root) == "重黎"


def test_aliases_are_derived_without_book_specific_names() -> None:
    aliases = scanner._build_aliases("女娲补天遗石（混沌余烬）")
    assert "女娲补天遗石" in aliases
    assert "混沌余烬" in aliases
    assert "建木枝" in scanner._build_aliases("建木枝")


def test_object_gap_severity_scales_with_cluster_distance() -> None:
    records = [
        {"cluster_id": f"cluster_{n:03d}", "item_changes": []}
        for n in range(1, 8)
    ]
    records[0]["item_changes"] = [{"id": "I_X", "name": "陌生印章"}]
    drafts = {record["cluster_id"]: "首次出现陌生印章。" if index == 0 else "其他场景。"
              for index, record in enumerate(records)}
    findings = scanner.scan_object_continuity(records, drafts)
    assert findings[0]["gap_clusters"] == 6
