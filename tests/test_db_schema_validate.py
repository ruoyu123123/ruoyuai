# -*- coding: utf-8 -*-
"""数据库 schema 严格只读校验回归网。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import db_schema_validate as dsv  # noqa: E402


# ============ validate_file 严格只读 ============
def _mk_chars_dict(td):
    db = Path(td) / "_数据库"
    db.mkdir(parents=True)
    p = db / "人物卡.json"
    p.write_text(json.dumps(
        {"schema_version": 1, "characters": {"1": {"id": "x", "name": "a", "role": "主"}}},
        ensure_ascii=False), encoding="utf-8")
    return p


def test_validate_type_mismatch_is_error_and_never_writes():
    """collection 类型错误直接报错，文件与目录结构保持不变。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_chars_dict(td)
        before = p.read_bytes()
        errs, warns = dsv.validate_file(p, dsv.SCHEMA_RULES["人物卡"])
        assert any("TYPE_MISMATCH" in e for e in errs), errs
        assert p.read_bytes() == before
        assert not (p.parent / "_backup").exists()


def test_validate_missing_top_key_is_error():
    """稳定消费契约的必填顶层键缺失时阻断。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "人物卡.json"
        p.write_text(json.dumps({"characters": []}), encoding="utf-8")
        errs, _ = dsv.validate_file(p, dsv.SCHEMA_RULES["人物卡"])
        assert any("MISSING_TOP_KEY" in e and "schema_version" in e for e in errs)


def test_validate_item_shape_and_required_fields_are_errors():
    """集合元素必须是对象且包含稳定消费字段。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "人物卡.json"
        p.write_text(json.dumps({"schema_version": 1, "characters": ["bad", {"id": "x"}]}),
                     encoding="utf-8")
        errs, _ = dsv.validate_file(p, dsv.SCHEMA_RULES["人物卡"])
        assert any("ITEM_TYPE_MISMATCH" in e for e in errs)
        assert any("ITEM_MISSING_FIELD" in e and "name" in e and "role" in e for e in errs)


def test_cli_rejects_unknown_write_flag():
    """验证器拒绝任何未声明的写盘参数。"""
    try:
        dsv._parse_args(["book", "--write-changes"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("未声明的写盘参数必须被 argparse 拒绝")


# ============ C19 大势卡结构契约（2026-06-27）============
def _mk_db(td) -> Path:
    db = Path(td) / "_数据库"
    db.mkdir(parents=True)
    return db


def _write_gt(db: Path, card: dict) -> Path:
    p = db / "大势卡.json"
    p.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return p


def test_c19_missing_file_optional():
    """大势卡.json 缺失 → OPTIONAL_MISSING warning·非 error（IP 线性/未到 outline）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        errs, warns = dsv.check_grand_trend_structure(db)
        assert errs == []
        assert any("OPTIONAL_MISSING" in w for w in warns)


def test_c19_bare_scaffold_advisory_not_error():
    """bare scaffold 空骨架（volumes/major_events 皆空）→ advisory·绝不当结构破损（fluid 起步）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_gt(db, {"schema_version": "v27", "volumes": [], "major_events": []})
        errs, warns = dsv.check_grand_trend_structure(db)
        assert errs == [], errs
        assert any("GRAND_TREND_NOT_AUTHORED" in w for w in warns)


def test_c19_well_formed_card_clean():
    """良构 ME 池（id+volume 齐·每卷有 finale·prereq 可解析）→ 0 error。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_gt(db, {"volumes": [{"vol": 1}], "major_events": [
            {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False, "prerequisites": []},
            {"id": "ME-V1-02", "volume": 1, "is_volume_finale": True, "prerequisites": ["ME-V1-01"]},
        ]})
        errs, warns = dsv.check_grand_trend_structure(db)
        assert errs == [], errs


def test_c19_volume_defined_me_pool_empty_error():
    """卷已定义但 ME 池空 → ME_POOL_EMPTY error（emergence 大势无方向·hard）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_gt(db, {"volumes": [{"vol": 1}], "major_events": []})
        errs, _ = dsv.check_grand_trend_structure(db)
        assert any("GRAND_TREND_ME_POOL_EMPTY" in e for e in errs), errs


def test_c19_missing_field_no_finale_dangling_prereq():
    """缺 id / 卷无 finale / prereq 悬空 → 三类 hard error 各命中。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_gt(db, {"volumes": [{"vol": 1}], "major_events": [
            {"id": "ME-V1-01", "volume": 1, "is_volume_finale": False, "prerequisites": ["ME-V9-99"]},
            {"volume": 1, "is_volume_finale": False},  # 缺 id
        ]})
        errs, _ = dsv.check_grand_trend_structure(db)
        assert any("GRAND_TREND_ME_MISSING_FIELD" in e for e in errs)
        assert any("GRAND_TREND_VOLUME_NO_FINALE" in e for e in errs)
        assert any("GRAND_TREND_PREREQ_UNRESOLVED" in e for e in errs)


def test_c19_finale_string_true_accepted():
    """is_volume_finale 字符串 'true' 也认（容忍 LLM 输出）→ 不误报无 finale。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_gt(db, {"volumes": [{"vol": 2}], "major_events": [
            {"id": "ME-V2-01", "volume": 2, "is_volume_finale": "true"},
        ]})
        errs, _ = dsv.check_grand_trend_structure(db)
        assert not any("VOLUME_NO_FINALE" in e for e in errs), errs


# ============ C02 live-consumed 空内容 advisory 背板 ============
def _gt_authored(db: Path):
    """写一张 authored 大势卡（C02 gate=outline 完成·major_events 非空）。"""
    _write_gt(db, {"major_events": [{"id": "ME-V1-01", "volume": 1, "is_volume_finale": True}]})


def test_c02_consumed_but_empty_advisory():
    """live-consumed（world_evolution_engine）子系统内容全空 → advisory warning。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _gt_authored(db)
        (db / "涟漪规则.json").write_text(json.dumps({
            "schema_version": 1,
            "consumption": {"status": "live", "by": ["world_evolution_engine"]},
            "rules": [],
        }, ensure_ascii=False), encoding="utf-8")
        warns = dsv.check_consumed_but_empty(db)
        assert any("CONSUMED_BUT_EMPTY" in w and "涟漪规则" in w for w in warns), warns


def test_c02_gate_skips_pre_outline():
    """大势卡 ME 池空（outline 未完成）→ gate 关·不产 C02 噪声。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_gt(db, {"volumes": [], "major_events": []})  # 未 authored
        (db / "涟漪规则.json").write_text(json.dumps({
            "schema_version": 1,
            "consumption": {"status": "live", "by": ["world_evolution_engine"]},
            "rules": [],
        }, ensure_ascii=False), encoding="utf-8")
        assert dsv.check_consumed_but_empty(db) == []


def test_c02_skips_nonempty_content():
    """live-consumed 但有内容 → 不报 advisory。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _gt_authored(db)
        (db / "世界状态.json").write_text(json.dumps({
            "schema_version": 1,
            "consumption": {"status": "live", "by": ["build_manifest"]},
            "factions": [{"name": "立序派"}],
        }, ensure_ascii=False), encoding="utf-8")
        assert not any("世界状态" in w for w in dsv.check_consumed_but_empty(db))


def test_c02_skips_non_target_consumer():
    """consumption.by 不含目标引擎（world_evolution_engine/build_manifest）→ 不报。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _gt_authored(db)
        (db / "某档.json").write_text(json.dumps({
            "schema_version": 1,
            "consumption": {"status": "live", "by": ["some_other_engine"]},
            "items": [],
        }, ensure_ascii=False), encoding="utf-8")
        assert dsv.check_consumed_but_empty(db) == []


def test_c02_story_destiny_empty_strings_count_as_empty():
    """story_destiny 全空串不算内容（_has_content 递归）→ 空骨架被正确识别为空。"""
    assert dsv._has_content({"final_image": "", "thematic_resolution": ""}) is False
    assert dsv._has_content({"final_image": "末法纪"}) is True
    assert dsv._has_content([]) is False
    assert dsv._has_content([{"x": "y"}]) is True


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
