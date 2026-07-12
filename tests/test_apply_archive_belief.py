# -*- coding: utf-8 -*-
"""apply_archive.py 角色信念回库测试。

钉死 apply_belief_updates：archivist 读正文 + scene participants 产 archive.belief_updates →
确定性 append 进 character_belief_ledger.json（SymbolicToM arXiv:2306.00924 信念只沿在场传播）：
  · facts{} 登记 fact 元信息(content/first_revealed_cluster/subject)·first_revealed_cluster 幂等不覆盖
  · characters[char_id].known_facts 按 fact_id 去重 append·已有则更新 can_speak/source（不重复 append）
  · unaware_of：学到即移除（已知不再 unaware）；显式 belief_unaware 标记（保守·learn 优先）
  · 幂等：re-apply 不重复 append、不写盘churn
  · archive 无 belief_updates → 零变更且不建 ledger 文件
  · dry-run 不写盘
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import apply_archive as aa  # noqa: E402


def _mk_project(tmp: Path):
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(json.dumps(
        {"schema_version": 1, "characters": [
            {"id": "C_PROT", "name": "伊莱", "role": "守夜人"},
            {"id": "C_AMY", "name": "艾米", "role": "青梅"},
            {"id": "C_MARTHA", "name": "玛莎修女", "role": "修女"}]},
        ensure_ascii=False), encoding="utf-8")
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


def _ledger(db):
    return json.loads((db / "character_belief_ledger.json").read_text(encoding="utf-8"))


# 最小合法 archive（main() 要求 characters 非空）+ belief 段
def _archive_with_belief(belief_updates, belief_unaware=None):
    obj = {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}],
           "belief_updates": belief_updates}
    if belief_unaware is not None:
        obj["belief_unaware"] = belief_unaware
    return obj


def test_belief_updates_append_known_facts_and_register_fact():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_遗嘱来自未来", "content": "遗嘱是未来的自己写的",
             "learned_at_scene": 0, "source": "witnessed", "can_speak": False,
             "reader_knows": True, "is_red_herring": False, "subject": "C_PROT"},
            {"char_id": "C_AMY", "fact_id": "F_遗嘱来自未来", "content": "遗嘱是未来的自己写的",
             "learned_at_scene": 2, "source": "told_by:C_PROT", "can_speak": True,
             "reader_knows": True, "subject": "C_PROT"}]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        led = _ledger(db)
        # facts{} 登记
        assert "F_遗嘱来自未来" in led["facts"]
        assert led["facts"]["F_遗嘱来自未来"]["first_revealed_cluster"] == "cluster_001"
        assert led["facts"]["F_遗嘱来自未来"]["subject"] == "C_PROT"
        # 两个角色各 1 条 known_facts
        prot_kf = led["characters"]["C_PROT"]["known_facts"]
        amy_kf = led["characters"]["C_AMY"]["known_facts"]
        assert len(prot_kf) == 1 and prot_kf[0]["source"] == "witnessed"
        assert prot_kf[0]["can_speak"] is False
        assert len(amy_kf) == 1 and amy_kf[0]["source"] == "told_by:C_PROT"
        assert amy_kf[0]["learned_at_scene"] == 2


def test_absent_character_not_learned_false_belief():
    """缺席角色不在 belief_updates → 根本不写进 known_facts（自动 false belief）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_真相", "content": "X", "learned_at_scene": 0,
             "source": "witnessed"}]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        led = _ledger(db)
        # 玛莎缺席 → 不出现在 characters / 无 known_facts
        assert "C_MARTHA" not in led["characters"]


def test_belief_updates_idempotent_no_dup_append():
    """re-apply 同 archive → known_facts 不重复 append、facts 不重复登记。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_a", "content": "甲", "learned_at_scene": 0,
             "source": "witnessed", "can_speak": True}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 再跑
        led = _ledger(db)
        assert len(led["characters"]["C_PROT"]["known_facts"]) == 1
        assert len(led["facts"]) == 1


def test_belief_update_same_fact_updates_can_speak_no_dup():
    """同 fact_id 再来一条但 can_speak/source 变 → 原地更新、不新增条目。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_a", "content": "甲", "learned_at_scene": 0,
             "source": "deduced", "can_speak": False}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        # 第二次：同 fact_id·can_speak 翻转·source 变
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_a", "content": "甲", "learned_at_scene": 0,
             "source": "witnessed", "can_speak": True}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        led = _ledger(db)
        kf = led["characters"]["C_PROT"]["known_facts"]
        assert len(kf) == 1, "同 fact_id 不得重复 append"
        assert kf[0]["can_speak"] is True and kf[0]["source"] == "witnessed"


def test_belief_unaware_marked_conservatively():
    """显式 belief_unaware → 标进该 char.unaware_of（去重·幂等）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief(
            [{"char_id": "C_PROT", "fact_id": "F_秘密", "content": "S", "learned_at_scene": 0,
              "source": "witnessed"}],
            belief_unaware=[{"char_id": "C_MARTHA", "fact_id": "F_秘密"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 幂等
        led = _ledger(db)
        assert led["characters"]["C_MARTHA"]["unaware_of"] == ["F_秘密"]


def test_learn_overrides_unaware_mark():
    """既 learn 又被标 unaware 的同一 fact → learn 优先·不进 unaware_of（矛盾保护）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief(
            [{"char_id": "C_AMY", "fact_id": "F_x", "content": "X", "learned_at_scene": 1,
              "source": "witnessed"}],
            belief_unaware=[{"char_id": "C_AMY", "fact_id": "F_x"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        led = _ledger(db)
        assert "F_x" not in led["characters"]["C_AMY"].get("unaware_of", [])
        assert any(k["fact_id"] == "F_x" for k in led["characters"]["C_AMY"]["known_facts"])


def test_learning_removes_prior_unaware():
    """先在 cluster_001 被标 unaware，后续 cluster 学到 → 从 unaware_of 移除（确定性）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        # 事件簇加第二个 cluster 供 cluster_002 回库
        ec = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
        ec["clusters"].append({"cluster_id": "cluster_002"})
        (db / "事件簇.json").write_text(json.dumps(ec, ensure_ascii=False), encoding="utf-8")
        _write_archive(db, "001", _archive_with_belief(
            [], belief_unaware=[{"char_id": "C_MARTHA", "fact_id": "F_迟知"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        assert _ledger(db)["characters"]["C_MARTHA"]["unaware_of"] == ["F_迟知"]
        # cluster_002 玛莎学到了
        _write_archive(db, "002", _archive_with_belief([
            {"char_id": "C_MARTHA", "fact_id": "F_迟知", "content": "迟来的真相",
             "learned_at_scene": 0, "source": "witnessed"}]))
        aa.main([str(Path(d)), "--cluster", "002"])
        led = _ledger(db)
        assert "F_迟知" not in led["characters"]["C_MARTHA"]["unaware_of"]
        assert any(k["fact_id"] == "F_迟知" for k in led["characters"]["C_MARTHA"]["known_facts"])


def test_first_revealed_cluster_stable_across_reapply():
    """同 fact_id 在更晚 cluster 再 witness → facts.first_revealed_cluster 保持首次（不被覆盖）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        ec = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
        ec["clusters"].append({"cluster_id": "cluster_002"})
        (db / "事件簇.json").write_text(json.dumps(ec, ensure_ascii=False), encoding="utf-8")
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_s", "content": "首揭", "learned_at_scene": 0,
             "source": "witnessed"}]))
        aa.main([str(Path(d)), "--cluster", "001"])
        _write_archive(db, "002", _archive_with_belief([
            {"char_id": "C_AMY", "fact_id": "F_s", "content": "首揭", "learned_at_scene": 0,
             "source": "told_by:C_PROT"}]))
        aa.main([str(Path(d)), "--cluster", "002"])
        led = _ledger(db)
        assert led["facts"]["F_s"]["first_revealed_cluster"] == "cluster_001"


def test_no_belief_updates_noop_backward_compat():
    """无可靠信念变化时不创建信念账本。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "character_belief_ledger.json").exists(), "无 belief 段不应建 ledger"


def test_empty_belief_updates_list_noop():
    """belief_updates 显式空数组 → no-op（不报错·不写盘）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief([]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "character_belief_ledger.json").exists()


def test_belief_dry_run_no_write():
    """dry-run 不写 ledger。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_a", "content": "甲", "learned_at_scene": 0,
             "source": "witnessed"}]))
        assert aa.main([str(Path(d)), "--cluster", "001", "--dry-run"]) == 0
        assert not (db / "character_belief_ledger.json").exists()


def test_belief_merges_into_existing_ledger_skeleton():
    """已存在空骨架 ledger（world_seed_init 播种）→ append 不覆盖既有结构。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        (db / "character_belief_ledger.json").write_text(json.dumps(
            {"schema_version": 1, "characters": {}, "facts": {}}, ensure_ascii=False), encoding="utf-8")
        _write_archive(db, "001", _archive_with_belief([
            {"char_id": "C_PROT", "fact_id": "F_a", "content": "甲", "learned_at_scene": 0,
             "source": "witnessed"}]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        led = _ledger(db)
        assert led["schema_version"] == 1
        assert "F_a" in led["facts"]
        assert len(led["characters"]["C_PROT"]["known_facts"]) == 1
