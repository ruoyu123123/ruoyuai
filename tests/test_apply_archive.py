# -*- coding: utf-8 -*-
"""novel-archivist 的 cluster 归档确定性回库测试。

钉死：写作模型不自报 changes，Claude(archivist) 读正文产 archive，本脚本确定性回库：
  · 新角色 → 人物卡建卡 + 角色池按 tier 分类
  · 老角色按稳定 id 更新 state_log
  · 道具/关系 → 按 id/pair 去重写入
  · locked_facts → 事件簇.clusters[].locked_facts
  · throughline_progress（OS/MC/IC/RS）→ 事件簇.clusters[].throughline_progress
  · 幂等：re-apply 不重复建
  · dry-run 不写盘
  · archive 合同破损或文件缺失 → exit 2
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import apply_archive as aa  # noqa: E402


def _mk_project(tmp: Path, with_prot=True):
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    chars = [{"id": "C_PROT", "name": "伊莱", "role": "守夜人"}] if with_prot else []
    (db / "人物卡.json").write_text(json.dumps(
        {"schema_version": 1, "characters": chars}, ensure_ascii=False), encoding="utf-8")
    (db / "角色池.json").write_text(json.dumps(
        {"schema_version": 1, "core": [], "emerged": [], "extras": []}, ensure_ascii=False), encoding="utf-8")
    (db / "道具.json").write_text(json.dumps(
        {"schema_version": 1, "items": []}, ensure_ascii=False), encoding="utf-8")
    (db / "关系.json").write_text(json.dumps(
        {"schema_version": 1, "relationships": []}, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001"}]}, ensure_ascii=False), encoding="utf-8")
    return db


def _write_archive(db: Path, key, obj):
    p = db / ".wal" / f"cluster_{key}_archive.json"
    payload = dict(obj)
    payload.setdefault("cluster_id", f"cluster_{key}")
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _load(db, name):
    return json.loads((db / name).read_text(encoding="utf-8"))


def test_new_character_creates_card_and_pools():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_MARTHA", "name": "玛莎修女", "role": "修女",
                            "tier": "extra", "first_cluster": "cluster_001", "new": True}]})
        rc = aa.main([str(Path(d)), "--cluster", "001"])
        assert rc == 0
        pc = _load(db, "人物卡.json")
        ids = {c["id"] for c in pc["characters"]}
        assert "C_MARTHA" in ids and "C_PROT" in ids
        martha = next(c for c in pc["characters"] if c["id"] == "C_MARTHA")
        assert martha["first_appearance_cluster"] == "cluster_001"
        assert "first_appearance_ch" not in martha
        pool = _load(db, "角色池.json")
        pooled = next(c for c in pool["extras"] if c["id"] == "C_MARTHA")
        assert pooled["first_cluster"] == "cluster_001"
        assert "ch" not in pooled


def test_existing_character_by_id_appends_state_no_dup():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core",
                            "state_changes": [{"changed_at_cluster": "cluster_001",
                                               "change": "确认遗嘱来自未来"}]}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        pc = _load(db, "人物卡.json")
        prot = [c for c in pc["characters"] if c["id"] == "C_PROT"]
        assert len(prot) == 1, "不得为同一角色造重复卡"
        state = next(s for s in prot[0].get("state_log", [])
                     if s["change"] == "确认遗嘱来自未来")
        assert state["changed_at_cluster"] == "cluster_001"
        assert "ch" not in state


def test_character_without_id_is_rejected():
    """角色缺稳定 id 时拒绝整份归档。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"name": "伊莱", "tier": "core",
                            "state_changes": [{"changed_at_cluster": "cluster_001",
                                               "change": "惊醒"}]}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2


def test_new_character_state_changes_on_first_apply():
    """新角色首次回库时立即保存本块状态变化。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_GREEN", "name": "格林", "role": "大执事", "tier": "extra",
                            "first_cluster": "cluster_001", "new": True,
                            "state_changes": [{"changed_at_cluster": "cluster_001",
                                               "change": "独掌地下室钥匙"}]}]})
        aa.main([str(Path(d)), "--cluster", "001"])  # 只 apply 一次
        pc = _load(db, "人物卡.json")
        green = [c for c in pc["characters"] if c["id"] == "C_GREEN"][0]
        assert any(s["change"] == "独掌地下室钥匙" for s in green.get("state_log", [])), \
            "新角色首次 apply 应已含 state_changes"


def test_items_and_relationships_and_locked_facts():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_AMY", "name": "艾米", "tier": "extra",
                            "first_cluster": "cluster_001", "new": True}],
            "items": [{"id": "I_WILL", "name": "羊皮纸遗嘱", "desc": "来自明日",
                       "holder": "C_PROT", "first_cluster": "cluster_001"}],
            "relationships": [{"id": "REL_PROT_AMY", "from": "C_PROT", "to": "C_AMY", "type": "青梅竹马"}],
            "locked_facts": [{"fact": "遗嘱墨迹执行后变淡", "subject": "I_WILL"}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        item = next(i for i in _load(db, "道具.json")["items"] if i["id"] == "I_WILL")
        assert item["first_appearance_cluster"] == "cluster_001"
        assert "first_appearance_ch" not in item
        assert any(r["id"] == "REL_PROT_AMY" for r in _load(db, "关系.json")["relationships"])
        ec = _load(db, "事件簇.json")
        lf = ec["clusters"][0].get("locked_facts", [])
        assert any("墨迹执行后变淡" in x["fact"] for x in lf)


def test_idempotent_reapply():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_AMY", "name": "艾米", "tier": "extra",
                            "first_cluster": "cluster_001"}],
            "items": [{"id": "I_WILL", "name": "遗嘱", "first_cluster": "cluster_001"}],
            "relationships": [{"id": "REL_PROT_AMY", "from": "C_PROT", "to": "C_AMY", "type": "青梅竹马"}],
            "locked_facts": [{"fact": "硬事实A", "subject": "X"}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 再跑
        assert len(_load(db, "人物卡.json")["characters"]) == 2  # C_PROT + C_AMY
        assert len(_load(db, "道具.json")["items"]) == 1
        assert len(_load(db, "关系.json")["relationships"]) == 1
        assert len(_load(db, "事件簇.json")["clusters"][0]["locked_facts"]) == 1


def test_relationship_evolution_updates_in_place():
    """回归锁：同一对角色关系演进（敌意→联盟）不能被 add-only 丢弃——
    稳定 REL_* id 重发带新 type → 就地更新，不新增、不静默跳过。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_AMY", "name": "艾米", "tier": "extra",
                            "first_cluster": "cluster_001"}],
            "relationships": [{"id": "REL_PROT_AMY", "from": "C_PROT", "to": "C_AMY",
                               "type": "青梅竹马", "note": "初识"}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        # 关系演进：同 id/同 pair 带新 type/note 重发
        _write_archive(db, "001", {
            "characters": [{"id": "C_AMY", "name": "艾米", "tier": "extra",
                            "first_cluster": "cluster_001"}],
            "relationships": [{"id": "REL_PROT_AMY", "from": "C_PROT", "to": "C_AMY",
                               "type": "反目成仇", "note": "背叛后决裂"}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        rels = _load(db, "关系.json")["relationships"]
        assert len(rels) == 1  # 不新增
        assert rels[0]["type"] == "反目成仇"  # 就地演进
        assert rels[0]["note"] == "背叛后决裂"


def test_dry_run_no_write():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [{"id": "C_NEW", "name": "新", "tier": "extra",
                                                       "first_cluster": "cluster_001"}]})
        aa.main([str(Path(d)), "--cluster", "001", "--dry-run"])
        assert len(_load(db, "人物卡.json")["characters"]) == 1


def test_empty_archive_returns_error():
    """characters 为空表示状态梳理未完成。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [], "items": [], "relationships": [], "locked_facts": []})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2


def test_missing_archive_returns_error():
    """required archive 缺失时返回 2。"""
    with tempfile.TemporaryDirectory() as d:
        _mk_project(Path(d))
        assert aa.main([str(Path(d)), "--cluster", "099"]) == 2


def test_archive_cluster_id_must_match_command():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "cluster_id": "cluster_002",
            "characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}],
        })
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2


def test_chapter_state_fields_are_rejected():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{
                "id": "C_NEW",
                "name": "新角色",
                "tier": "extra",
                "new": True,
                "first_ch": 1,
                "state_changes": [{"ch": 1, "change": "登场"}],
            }],
        })
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2


def test_malformed_archive_json_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        (db / ".wal" / "cluster_001_archive.json").write_text("{", encoding="utf-8")
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2


def test_characters_only_archive_succeeds():
    """只有角色变化而无其他状态域时仍是合法归档。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_X", "name": "X", "tier": "extra", "new": True,
                            "first_cluster": "cluster_001"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0


def test_throughline_progress_written_to_event_cluster():
    """四条叙事线的 bool 状态写入当前事件簇。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_X", "name": "X", "tier": "extra", "new": True,
                            "first_cluster": "cluster_001"}],
            "throughline_progress": {"OS": True, "MC": True, "IC": False, "RS": False}})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        ec = _load(db, "事件簇.json")
        tp = ec["clusters"][0].get("throughline_progress")
        assert tp == {"OS": True, "MC": True, "IC": False, "RS": False}, tp


def test_no_throughline_no_crash_and_no_field():
    """archive 无 throughline_progress → 不写字段、不崩（advisory 遥测·缺失 = DORMANT）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_X", "name": "X", "tier": "extra", "new": True,
                            "first_cluster": "cluster_001"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        ec = _load(db, "事件簇.json")
        assert "throughline_progress" not in ec["clusters"][0]
