# -*- coding: utf-8 -*-
"""apply_archive.py 测试 — 🔴 2026-06-28 状态梳理回库（archivist 产 archive → 确定性写库）。

钉死：写作模型不自报 changes，Claude(archivist) 读正文产 archive，本脚本确定性回库：
  · 新角色 → 人物卡建卡 + 角色池按 tier 分类
  · 老角色（id 或名字匹配）→ state_log 追加、不造重复卡
  · 道具/关系 → 按 id/pair 去重写入
  · locked_facts → 事件簇.clusters[].locked_facts
  · 幂等：re-apply 不重复建
  · dry-run 不写盘；空 archive → exit 1
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
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def _load(db, name):
    return json.loads((db / name).read_text(encoding="utf-8"))


def test_new_character_creates_card_and_pools():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_MARTHA", "name": "玛莎修女", "role": "修女",
                            "tier": "extra", "first_ch": 2, "new": True}]})
        rc = aa.main([str(Path(d)), "--cluster", "001"])
        assert rc == 0
        pc = _load(db, "人物卡.json")
        ids = {c["id"] for c in pc["characters"]}
        assert "C_MARTHA" in ids and "C_PROT" in ids
        pool = _load(db, "角色池.json")
        assert any(c["id"] == "C_MARTHA" for c in pool["extras"])


def test_existing_character_by_id_appends_state_no_dup():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core",
                            "state_changes": [{"ch": 3, "change": "确认遗嘱来自未来"}]}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        pc = _load(db, "人物卡.json")
        prot = [c for c in pc["characters"] if c["id"] == "C_PROT"]
        assert len(prot) == 1, "不得为同一角色造重复卡"
        assert any(s["change"] == "确认遗嘱来自未来" for s in prot[0].get("state_log", []))


def test_existing_character_by_name_reuses_id():
    """archivist 漏给 id 但名字匹配 → 按名复用 id，不造孤儿卡。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"name": "伊莱", "tier": "core",
                            "state_changes": [{"ch": 1, "change": "惊醒"}]}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        pc = _load(db, "人物卡.json")
        assert len(pc["characters"]) == 1, f"按名复用 id 不应新增卡, 实际 {pc['characters']}"


def test_new_character_state_changes_on_first_apply():
    """🔴 2026-06-28：新角色首次 apply 即填 state_changes（真实管线每 cluster 只 apply 一次）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_GREEN", "name": "格林", "role": "大执事", "tier": "extra",
                            "first_ch": 3, "new": True,
                            "state_changes": [{"ch": 3, "change": "独掌地下室钥匙"}]}]})
        aa.main([str(Path(d)), "--cluster", "001"])  # 只 apply 一次
        pc = _load(db, "人物卡.json")
        green = [c for c in pc["characters"] if c["id"] == "C_GREEN"][0]
        assert any(s["change"] == "独掌地下室钥匙" for s in green.get("state_log", [])), \
            "新角色首次 apply 应已含 state_changes"


def test_items_and_relationships_and_locked_facts():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_AMY", "name": "艾米", "tier": "extra", "first_ch": 2, "new": True}],
            "items": [{"id": "I_WILL", "name": "羊皮纸遗嘱", "desc": "来自明日", "holder": "C_PROT", "first_ch": 1}],
            "relationships": [{"id": "REL_PROT_AMY", "from": "C_PROT", "to": "C_AMY", "type": "青梅竹马"}],
            "locked_facts": [{"fact": "遗嘱墨迹执行后变淡", "subject": "I_WILL"}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        assert any(i["id"] == "I_WILL" for i in _load(db, "道具.json")["items"])
        assert any(r["id"] == "REL_PROT_AMY" for r in _load(db, "关系.json")["relationships"])
        ec = _load(db, "事件簇.json")
        lf = ec["clusters"][0].get("locked_facts", [])
        assert any("墨迹执行后变淡" in x["fact"] for x in lf)


def test_idempotent_reapply():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_AMY", "name": "艾米", "tier": "extra", "first_ch": 2}],
            "items": [{"id": "I_WILL", "name": "遗嘱"}],
            "relationships": [{"id": "REL_PROT_AMY", "from": "C_PROT", "to": "C_AMY", "type": "青梅竹马"}],
            "locked_facts": [{"fact": "硬事实A", "subject": "X"}]})
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 再跑
        assert len(_load(db, "人物卡.json")["characters"]) == 2  # C_PROT + C_AMY
        assert len(_load(db, "道具.json")["items"]) == 1
        assert len(_load(db, "关系.json")["relationships"]) == 1
        assert len(_load(db, "事件簇.json")["clusters"][0]["locked_facts"]) == 1


def test_dry_run_no_write():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [{"id": "C_NEW", "name": "新", "tier": "extra"}]})
        aa.main([str(Path(d)), "--cluster", "001", "--dry-run"])
        assert len(_load(db, "人物卡.json")["characters"]) == 1  # 只有原 C_PROT


def test_empty_archive_returns_1():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [], "items": [], "relationships": [], "locked_facts": []})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 1


def test_missing_archive_returns_1():
    with tempfile.TemporaryDirectory() as d:
        _mk_project(Path(d))
        assert aa.main([str(Path(d)), "--cluster", "099"]) == 1
