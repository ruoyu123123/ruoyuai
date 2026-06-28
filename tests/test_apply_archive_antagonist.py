# -*- coding: utf-8 -*-
"""apply_archive.py 反派轮替回库测试 — 🔴 2026-06-29 反派轮替ledger接通producer。

钉死 apply_antagonist_rotation：archivist 读正文判定本块**实际出场反派** → archive.antagonist_rotation
→ 确定性 append 进 反派轮替.json append-only ledger（此前零 producer·scanner 死码）：
  · entries 按 (cluster_id, antagonist_id) 去重·新键 append·同键就地更新可变字段（含 defeat_cluster）
  · 字段对齐 antagonist_rotation_scanner schema {cluster_id, antagonist_id, tier, faction,
    motive_type, power_system_tag, defeat_cluster}（端到端 scanner 真能读·非"跳过"）
  · 幂等：re-apply 不重复 append
  · C03 fluid / 向后兼容：archive 无 antagonist_rotation → no-op 不报错、不建 ledger 文件
  · dry-run 不写盘
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import apply_archive as aa  # noqa: E402
import antagonist_rotation_scanner as ar  # noqa: E402


def _mk_project(tmp: Path):
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(json.dumps(
        {"schema_version": 1, "characters": [
            {"id": "C_PROT", "name": "伊莱", "role": "守夜人"},
            {"id": "C_GREEN", "name": "格林", "role": "大执事"},
            {"id": "C_BISHOP", "name": "主教", "role": "幕后黑手"}]},
        ensure_ascii=False), encoding="utf-8")
    (db / "角色池.json").write_text(json.dumps(
        {"schema_version": 1, "core": [], "emerged": [], "extras": []}, ensure_ascii=False), encoding="utf-8")
    (db / "道具.json").write_text(json.dumps(
        {"schema_version": 1, "items": []}, ensure_ascii=False), encoding="utf-8")
    (db / "关系.json").write_text(json.dumps(
        {"schema_version": 1, "relationships": []}, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001"}, {"cluster_id": "cluster_002"},
                      {"cluster_id": "cluster_003"}]}, ensure_ascii=False), encoding="utf-8")
    return db


def _write_archive(db: Path, key, obj):
    p = db / ".wal" / f"cluster_{key}_archive.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def _ledger(db):
    return json.loads((db / "反派轮替.json").read_text(encoding="utf-8"))


def _archive_with_rotation(rotations):
    # main() 要求 characters 非空
    return {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}],
            "antagonist_rotation": rotations}


def test_rotation_appends_entry_schema_aligned():
    """基础：archive.antagonist_rotation → 反派轮替.json entries·字段对齐 scanner schema。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_rotation([
            {"antagonist_id": "C_GREEN", "cluster_id": "cluster_001", "tier": 2,
             "faction": "孤儿院", "motive_type": "贪婪", "power_system_tag": "凡人权术",
             "defeat_cluster": "cluster_001"}]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        led = _ledger(db)
        assert led["schema_version"] == 1
        ents = led["entries"]
        assert len(ents) == 1
        e = ents[0]
        # scanner 读的 7 字段全在
        for f in ("cluster_id", "antagonist_id", "tier", "faction",
                  "motive_type", "power_system_tag", "defeat_cluster"):
            assert f in e, f
        assert e["antagonist_id"] == "C_GREEN" and e["tier"] == 2


def test_rotation_default_cluster_id_from_current():
    """archivist 漏给 cluster_id → 默认当前块。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "002", _archive_with_rotation([
            {"antagonist_id": "C_GREEN", "tier": 2, "motive_type": "贪婪",
             "power_system_tag": "权术"}]))
        assert aa.main([str(Path(d)), "--cluster", "002"]) == 0
        assert _ledger(db)["entries"][0]["cluster_id"] == "cluster_002"


def test_rotation_idempotent_no_dup():
    """re-apply 同 archive → entries 不重复 append。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_rotation([
            {"antagonist_id": "C_GREEN", "cluster_id": "cluster_001", "tier": 2,
             "motive_type": "贪婪", "power_system_tag": "权术"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 再跑
        assert len(_ledger(db)["entries"]) == 1


def test_rotation_defeat_updates_in_place():
    """既有反派后续块被击败 → 复用引入 cluster_id 报 → 就地补 defeat_cluster·不另起重复条目。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        # cluster_001 引入 C_BISHOP（未击败）
        _write_archive(db, "001", _archive_with_rotation([
            {"antagonist_id": "C_BISHOP", "cluster_id": "cluster_001", "tier": 4,
             "motive_type": "信念", "power_system_tag": "术法"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        assert "defeat_cluster" not in _ledger(db)["entries"][0]
        # cluster_003 击败 → 复用引入 cluster_001 + defeat_cluster=cluster_003
        _write_archive(db, "003", _archive_with_rotation([
            {"antagonist_id": "C_BISHOP", "cluster_id": "cluster_001", "tier": 4,
             "motive_type": "信念", "power_system_tag": "术法",
             "defeat_cluster": "cluster_003"}]))
        aa.main([str(Path(d)), "--cluster", "003"])
        ents = _ledger(db)["entries"]
        assert len(ents) == 1, "defeat 必须就地更新·不另起重复条目"
        assert ents[0]["defeat_cluster"] == "cluster_003"


def test_rotation_distinct_antagonists_append_separate():
    """不同 (cluster_id, antagonist_id) → 各 append 独立条目。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_rotation([
            {"antagonist_id": "C_GREEN", "cluster_id": "cluster_001", "tier": 2,
             "motive_type": "贪婪", "power_system_tag": "权术"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        _write_archive(db, "002", _archive_with_rotation([
            {"antagonist_id": "C_BISHOP", "cluster_id": "cluster_002", "tier": 4,
             "motive_type": "信念", "power_system_tag": "术法"}]))
        aa.main([str(Path(d)), "--cluster", "002"])
        assert len(_ledger(db)["entries"]) == 2


def test_rotation_entry_missing_id_skipped():
    """缺 antagonist_id 的脏条目跳过·不入库。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_rotation([
            {"cluster_id": "cluster_001", "tier": 2, "motive_type": "贪婪"}]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        # 唯一脏条目被跳过 → 无有效 append → 不建文件
        assert not (db / "反派轮替.json").exists()


def test_no_rotation_noop_backward_compat():
    """archive 无 antagonist_rotation（C03 fluid 无反派 / 旧数据）→ no-op·不报错·不建文件。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "反派轮替.json").exists(), "无反派段不应建 ledger"


def test_empty_rotation_list_noop():
    """antagonist_rotation 显式空数组 → no-op·不写盘。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_rotation([]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "反派轮替.json").exists()


def test_rotation_dry_run_no_write():
    """dry-run 不写 ledger。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_rotation([
            {"antagonist_id": "C_GREEN", "cluster_id": "cluster_001", "tier": 2,
             "motive_type": "贪婪", "power_system_tag": "权术"}]))
        assert aa.main([str(Path(d)), "--cluster", "001", "--dry-run"]) == 0
        assert not (db / "反派轮替.json").exists()


def test_rotation_merges_into_existing_skeleton():
    """已存在空骨架 ledger → append 不覆盖既有结构。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        (db / "反派轮替.json").write_text(json.dumps(
            {"schema_version": 1, "entries": []}, ensure_ascii=False), encoding="utf-8")
        _write_archive(db, "001", _archive_with_rotation([
            {"antagonist_id": "C_GREEN", "cluster_id": "cluster_001", "tier": 2,
             "motive_type": "贪婪", "power_system_tag": "权术"}]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        led = _ledger(db)
        assert led["schema_version"] == 1
        assert len(led["entries"]) == 1


def test_produced_ledger_consumable_by_scanner():
    """端到端：producer 落的 ledger·scanner 真能读（非"跳过"）·entries_count 对·验 schema 对齐。"""
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        os.environ["ANTAGONIST_ROTATION_MODE"] = "active"
        with tempfile.TemporaryDirectory() as d:
            db = _mk_project(Path(d))
            # cluster_index 供 scanner void 检测
            (db / "cluster_index.json").write_text(json.dumps(
                {"clusters": [{"cluster_id": f"cluster_{i:03d}"} for i in range(1, 4)]},
                ensure_ascii=False), encoding="utf-8")
            _write_archive(db, "001", _archive_with_rotation([
                {"antagonist_id": "C_GREEN", "cluster_id": "cluster_001", "tier": 2,
                 "motive_type": "贪婪", "power_system_tag": "凡人权术"}]))
            aa.main([str(Path(d)), "--cluster", "001"])
            _write_archive(db, "002", _archive_with_rotation([
                {"antagonist_id": "C_BISHOP", "cluster_id": "cluster_002", "tier": 4,
                 "motive_type": "信念", "power_system_tag": "术法"}]))
            aa.main([str(Path(d)), "--cluster", "002"])
            rep = ar.scan(project_root=str(Path(d)))
            # 不再是"跳过"——scanner 真读到了 producer 落的 ledger
            assert "跳过" not in rep.get("note", "")
            assert rep.get("entries_count") == 2
            # tier 升、motive/power 不同类 → 健康轮替 PASS
            assert rep["verdict"] == "PASS"
    finally:
        if bak is None:
            os.environ.pop("ANTAGONIST_ROTATION_MODE", None)
        else:
            os.environ["ANTAGONIST_ROTATION_MODE"] = bak


def test_rotation_codes_never_hard_gate():
    """守 19 码三方一致：四个 rotation code 绝不在 hard_gate_codes。"""
    reg = json.loads((_ROOT / "core" / "scripts" / "scanner_registry.json").read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("ANTAGONIST_ROTATION_VOID", "ANTAGONIST_ROTATION_TIER_DOWNGRADE",
              "ANTAGONIST_ROTATION_MOTIVE_MONOTONE", "ANTAGONIST_ROTATION_POWER_MONOTONE"):
        assert c not in hgs


def test_ledger_in_scaffold_known_extras():
    """producer 惰性建的 反派轮替.json 须在 scaffold KNOWN_EXTRAS 白名单（--strict-extras 不误报）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import scaffold_subsystems as scaf  # noqa: E402
    assert "反派轮替.json" in scaf.KNOWN_EXTRAS
