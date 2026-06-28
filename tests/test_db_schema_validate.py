# -*- coding: utf-8 -*-
"""db_schema_validate auto-migrate 回归网（第四轮 Workflow #9·2026-06-17）。

cluster-save-state step1 热路径每 cluster 跑 `db_schema_validate.py --auto-migrate`（破坏性落盘改写
用户 DB JSON）·此前零行为测试覆盖（唯一测试只跑 auto_migrate=False）。

覆盖：
  · migrate_dict_to_list —— dict→list 转换 + 静默丢非 dict 项（数据损失行为固化）
  · validate_file auto_migrate=False —— dict collection → TYPE_MISMATCH error（不改盘）
  · validate_file auto_migrate=True —— dict→list 落盘 + 备份

零依赖范式（__main__ 自跑）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import db_schema_validate as dsv  # noqa: E402


# ============ migrate_dict_to_list（dict→list + 数据损失）============
def test_migrate_dict_to_list_basic():
    """dict collection → list·数字 key 加 ch 字段。"""
    data = {"characters": {"1": {"name": "a"}, "2": {"name": "b"}}}
    new, mig = dsv.migrate_dict_to_list(data, "characters")
    assert mig is True
    assert isinstance(new["characters"], list)
    assert len(new["characters"]) == 2
    assert new["characters"][0]["ch"] == 1  # 数字 key → ch（排序后第一个）


def test_migrate_dict_to_list_drops_non_dict():
    """🔴 数据损失行为固化：非 dict 值（str/int）被静默丢弃（仅保留 dict 项）。"""
    data = {"characters": {"1": {"name": "a"}, "2": "str", "3": 42, "4": {"name": "b"}}}
    new, mig = dsv.migrate_dict_to_list(data, "characters")
    assert mig is True
    assert len(new["characters"]) == 2  # str/int 两项静默丢


def test_migrate_already_list_noop():
    """已是 list → 不 migrate（mig False）。"""
    data = {"characters": [{"name": "a"}]}
    new, mig = dsv.migrate_dict_to_list(data, "characters")
    assert mig is False


def test_migrate_non_numeric_key_orig_key():
    """非数字 key → _orig_key 保留（不丢）。"""
    data = {"characters": {"主角": {"name": "a"}}}
    new, mig = dsv.migrate_dict_to_list(data, "characters")
    assert mig is True
    assert new["characters"][0]["_orig_key"] == "主角"


# ============ validate_file auto_migrate ============
def _mk_chars_dict(td):
    db = Path(td) / "_数据库"
    db.mkdir(parents=True)
    p = db / "人物卡.json"
    p.write_text(json.dumps(
        {"schema_version": 1, "characters": {"1": {"id": "x", "name": "a", "role": "主"}}},
        ensure_ascii=False), encoding="utf-8")
    return p


def test_validate_auto_migrate_false_type_mismatch():
    """dict collection + auto_migrate=False → TYPE_MISMATCH error·不改盘（mig False）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_chars_dict(td)
        errs, warns, mig = dsv.validate_file(p, dsv.SCHEMA_RULES["人物卡"], auto_migrate=False)
        assert mig is False
        assert any("TYPE_MISMATCH" in e for e in errs), errs
        after = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(after["characters"], dict)  # 未改盘


def test_validate_auto_migrate_true_converts_and_persists():
    """dict collection + auto_migrate=True → dict→list 落盘（破坏性改写）+ 备份。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_chars_dict(td)
        errs, warns, mig = dsv.validate_file(p, dsv.SCHEMA_RULES["人物卡"], auto_migrate=True)
        assert mig is True
        after = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(after["characters"], list)  # 落盘 dict→list
        # 备份生成（_backup/db_schema/<ts>/）
        backup_root = p.parent / "_backup" / "db_schema"
        assert backup_root.exists() and any(backup_root.iterdir())


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
