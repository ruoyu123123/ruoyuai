# -*- coding: utf-8 -*-
"""style_source 硬校验回归锁（2026-07-08 验证书真机实证）。

作者风格.json 有 quantitative 实载荷但缺 style_source → error（SFS/AV 打分静默双退化根因）；
骨架占位（无实载荷）不要求——老项目/新骨架零影响。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import db_schema_validate as dsv  # noqa: E402


def _mk(tmp_path, payload):
    db = tmp_path / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "作者风格.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return db


def test_payload_without_style_source_is_error(tmp_path):
    db = _mk(tmp_path, {"quantitative": {"sentence_len_mean": 28.0}})
    errs = dsv.check_style_source(db)
    assert len(errs) == 1 and "style_source" in errs[0]


def test_payload_with_style_source_passes(tmp_path):
    db = _mk(tmp_path, {"quantitative": {"sentence_len_mean": 28.0},
                        "style_source": "workspace/styles/主神大道/skill_FINAL.md"})
    assert dsv.check_style_source(db) == []


def test_skeleton_placeholder_not_required(tmp_path):
    """骨架占位（quantitative 空/只 _doc）不要求 style_source——scaffold 新书零影响。"""
    assert dsv.check_style_source(_mk(tmp_path, {"quantitative": {}})) == []
    assert dsv.check_style_source(_mk(tmp_path, {"quantitative": {"_doc": "占位"}})) == []
    assert dsv.check_style_source(_mk(tmp_path, {})) == []


def test_missing_file_or_bad_json_no_double_report(tmp_path):
    db = tmp_path / "_数据库"
    db.mkdir()
    assert dsv.check_style_source(db) == []
    (db / "作者风格.json").write_text("{broken", encoding="utf-8")
    assert dsv.check_style_source(db) == []


def test_blank_style_source_is_error(tmp_path):
    db = _mk(tmp_path, {"quantitative": {"x": 1}, "style_source": "  "})
    assert len(dsv.check_style_source(db)) == 1
