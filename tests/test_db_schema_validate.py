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
