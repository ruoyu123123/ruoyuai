# -*- coding: utf-8 -*-
"""apply_archive.py 主角力量 tier 回库测试。

钉死 apply_protagonist_power_tier：archivist 读正文判定本块**主角力量 tier 变化** →
archive.protagonist_power_tier_update → 确定性 append 进 角色弧线.json
characters[pid].protagonist_power_tier 序列：
  · series 按 cluster_id 去重·新 cluster append·同 cluster 就地更新 tier/notes
  · 字段对齐 power_progression_scanner schema {cluster_id, tier, notes}·role=protagonist
    让 scanner._protagonist_id 能识别·端到端 scanner 真能读（非"跳过"/"序列过短"）
  · 幂等：re-apply 不重复 append
  · char_id 缺失 → 复用既有 protagonist pid·再缺 → 跳过该条
  · archive 无 protagonist_power_tier_update → 零变更且不建文件
  · dry-run 不写盘
  · 防再孤儿回归锁：producer 函数存在 + 已挂 main 分发 + 角色弧线.json 在 KNOWN_EXTRAS
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import apply_archive as aa  # noqa: E402
import power_progression_scanner as pp  # noqa: E402


def _mk_project(tmp: Path):
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(json.dumps(
        {"schema_version": 1, "characters": [
            {"id": "C_PROT", "name": "伊莱", "role": "守夜人"}]},
        ensure_ascii=False), encoding="utf-8")
    (db / "角色池.json").write_text(json.dumps(
        {"schema_version": 1, "core": [], "emerged": [], "extras": []}, ensure_ascii=False), encoding="utf-8")
    (db / "道具.json").write_text(json.dumps(
        {"schema_version": 1, "items": []}, ensure_ascii=False), encoding="utf-8")
    (db / "关系.json").write_text(json.dumps(
        {"schema_version": 1, "relationships": []}, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": f"cluster_{i:03d}"} for i in range(1, 4)]},
        ensure_ascii=False), encoding="utf-8")
    return db


def _write_archive(db: Path, key, obj):
    p = db / ".wal" / f"cluster_{key}_archive.json"
    payload = dict(obj)
    payload.setdefault("cluster_id", f"cluster_{key}")
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _arc(db):
    return json.loads((db / "角色弧线.json").read_text(encoding="utf-8"))


def _archive_with_tier(update):
    # main() 要求 characters 非空
    return {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}],
            "protagonist_power_tier_update": update}


def test_tier_appends_series_schema_aligned():
    """基础：archive.protagonist_power_tier_update(dict) → 角色弧线.json series·字段对齐 scanner。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1,
             "notes": "觉醒守夜人血脉·初入炼气"}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        arc = _arc(db)
        assert arc["schema_version"] == 1
        chars = arc["characters"]
        assert isinstance(chars, dict)  # scanner 读 dict keyed by pid
        entry = chars["C_PROT"]
        assert entry["role"] == "protagonist"  # scanner._protagonist_id 据此识别
        series = entry["protagonist_power_tier"]
        assert len(series) == 1
        s = series[0]
        for f in ("cluster_id", "tier", "notes"):
            assert f in s, f
        assert s["tier"] == 1 and s["cluster_id"] == "cluster_001"


def test_tier_update_as_list_supported():
    """protagonist_power_tier_update 可为 list（多条）→ 各 append。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier([
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"},
        ]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert len(_arc(db)["characters"]["C_PROT"]["protagonist_power_tier"]) == 1


def test_tier_requires_cluster_id():
    """力量变化缺 cluster_id 时拒绝归档。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "002", _archive_with_tier(
            {"char_id": "C_PROT", "tier": 2, "notes": "突破筑基"}))
        assert aa.main([str(Path(d)), "--cluster", "002"]) == 2
        assert not (db / "角色弧线.json").exists()


def test_tier_idempotent_no_dup():
    """re-apply 同 archive → series 不重复 append。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 再跑
        assert len(_arc(db)["characters"]["C_PROT"]["protagonist_power_tier"]) == 1


def test_tier_same_cluster_updates_in_place():
    """同 cluster_id 再产（tier/notes 改）→ 就地更新·不另起重复条目。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        aa.main([str(Path(d)), "--cluster", "001"])
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 2, "notes": "突破筑基"}))
        aa.main([str(Path(d)), "--cluster", "001"])
        series = _arc(db)["characters"]["C_PROT"]["protagonist_power_tier"]
        assert len(series) == 1, "同 cluster 必须就地更新·不另起重复条目"
        assert series[0]["tier"] == 2 and series[0]["notes"] == "突破筑基"


def test_tier_distinct_clusters_append_separate():
    """不同 cluster_id → 各 append 独立条目·保持时间序。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        aa.main([str(Path(d)), "--cluster", "001"])
        _write_archive(db, "002", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_002", "tier": 2, "notes": "筑基"}))
        aa.main([str(Path(d)), "--cluster", "002"])
        series = _arc(db)["characters"]["C_PROT"]["protagonist_power_tier"]
        assert len(series) == 2
        assert [s["cluster_id"] for s in series] == ["cluster_001", "cluster_002"]


def test_tier_missing_char_id_is_rejected():
    """char_id 缺失时不从既有主角记录推断。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        # cluster_001 显式 char_id 建立 protagonist
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        aa.main([str(Path(d)), "--cluster", "001"])
        # cluster_002 缺 char_id
        _write_archive(db, "002", _archive_with_tier(
            {"cluster_id": "cluster_002", "tier": 2, "notes": "筑基"}))
        assert aa.main([str(Path(d)), "--cluster", "002"]) == 2
        series = _arc(db)["characters"]["C_PROT"]["protagonist_power_tier"]
        assert len(series) == 1


def test_tier_no_char_id_no_existing_rejected():
    """char_id 缺失且无既有记录时仍属合同错误。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2
        assert not (db / "角色弧线.json").exists()


def test_tier_missing_or_invalid_tier_rejected():
    """缺 tier 或 tier 非数值时拒绝归档。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "notes": "无 tier"}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2
        assert not (db / "角色弧线.json").exists()
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": True, "notes": "bool"}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 2
        assert not (db / "角色弧线.json").exists()


def test_no_tier_update_noop_backward_compat():
    """本块无力量层级变化时不创建梯度台账。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "角色弧线.json").exists()


def test_empty_tier_update_noop():
    """protagonist_power_tier_update 显式空 list → no-op·不写盘。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier([]))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "角色弧线.json").exists()


def test_tier_dry_run_no_write():
    """dry-run 不写 角色弧线.json。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        assert aa.main([str(Path(d)), "--cluster", "001", "--dry-run"]) == 0
        assert not (db / "角色弧线.json").exists()


def test_tier_merges_into_existing_skeleton():
    """已存在空骨架 角色弧线.json → append 不覆盖既有结构。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        (db / "角色弧线.json").write_text(json.dumps(
            {"schema_version": 1, "characters": {}}, ensure_ascii=False), encoding="utf-8")
        _write_archive(db, "001", _archive_with_tier(
            {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 1, "notes": "炼气"}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        arc = _arc(db)
        assert arc["schema_version"] == 1
        assert len(arc["characters"]["C_PROT"]["protagonist_power_tier"]) == 1


def test_produced_arc_consumable_by_scanner_monotonic_pass():
    """端到端：producer 落的 角色弧线.json·scanner 真能读（非"跳过"/"过短"）·单调升 PASS。"""
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        os.environ["POWER_PROGRESSION_MODE"] = "active"
        with tempfile.TemporaryDirectory() as d:
            db = _mk_project(Path(d))
            for i, tier in ((1, 1), (2, 2), (3, 3)):
                _write_archive(db, f"{i:03d}", _archive_with_tier(
                    {"char_id": "C_PROT", "cluster_id": f"cluster_{i:03d}",
                     "tier": tier, "notes": f"晋阶{tier}"}))
                aa.main([str(Path(d)), "--cluster", f"{i:03d}"])
            draft = db / ".wal" / "draft.txt"
            draft.write_text("x" * 100, encoding="utf-8")
            rep = pp.scan(str(draft), project_root=str(Path(d)))
            # 不再"跳过"/"序列过短"——scanner 真读到了 producer 落的 tier 序列
            assert "跳过" not in rep.get("note", "")
            assert "过短" not in rep.get("note", "")
            assert rep.get("tier_series_len") == 3
            assert rep.get("protagonist") == "C_PROT"
            assert rep["verdict"] == "PASS"  # 单调升·无倒退/突跳/停滞
    finally:
        if bak is None:
            os.environ.pop("POWER_PROGRESSION_MODE", None)
        else:
            os.environ["POWER_PROGRESSION_MODE"] = bak


def test_produced_arc_scanner_detects_regression():
    """端到端：producer 落 tier 倒退（无重伤 notes）→ scanner 真检出 POWER_TIER_REGRESSION。"""
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        os.environ["POWER_PROGRESSION_MODE"] = "active"
        with tempfile.TemporaryDirectory() as d:
            db = _mk_project(Path(d))
            for i, tier in ((1, 2), (2, 1)):  # 倒退·notes 无重伤标记
                _write_archive(db, f"{i:03d}", _archive_with_tier(
                    {"char_id": "C_PROT", "cluster_id": f"cluster_{i:03d}",
                     "tier": tier, "notes": ""}))
                aa.main([str(Path(d)), "--cluster", f"{i:03d}"])
            draft = db / ".wal" / "draft.txt"
            draft.write_text("x" * 100, encoding="utf-8")
            rep = pp.scan(str(draft), project_root=str(Path(d)))
            codes = [v["code"] for v in rep["violations"]]
            assert "POWER_TIER_REGRESSION" in codes
            assert rep["gate_level"] == "advisory"  # 守北极星⑤·绝不 hard
    finally:
        if bak is None:
            os.environ.pop("POWER_PROGRESSION_MODE", None)
        else:
            os.environ["POWER_PROGRESSION_MODE"] = bak


def test_produced_arc_scanner_respects_injury_marker():
    """端到端：tier 倒退但 notes 带重伤标记 → scanner 不误判（剧情合法跌境）。"""
    bak = os.environ.get("POWER_PROGRESSION_MODE")
    try:
        os.environ["POWER_PROGRESSION_MODE"] = "active"
        with tempfile.TemporaryDirectory() as d:
            db = _mk_project(Path(d))
            _write_archive(db, "001", _archive_with_tier(
                {"char_id": "C_PROT", "cluster_id": "cluster_001", "tier": 3, "notes": "金丹"}))
            aa.main([str(Path(d)), "--cluster", "001"])
            _write_archive(db, "002", _archive_with_tier(
                {"char_id": "C_PROT", "cluster_id": "cluster_002", "tier": 1, "notes": "重伤·丹田碎·跌境"}))
            aa.main([str(Path(d)), "--cluster", "002"])
            draft = db / ".wal" / "draft.txt"
            draft.write_text("x" * 100, encoding="utf-8")
            rep = pp.scan(str(draft), project_root=str(Path(d)))
            codes = [v["code"] for v in rep["violations"]]
            assert "POWER_TIER_REGRESSION" not in codes
    finally:
        if bak is None:
            os.environ.pop("POWER_PROGRESSION_MODE", None)
        else:
            os.environ["POWER_PROGRESSION_MODE"] = bak


# ──── 防再孤儿回归锁（举一反三：这是第 2 个孤儿 scanner·锁 producer 存在）────
def test_producer_exists_and_wired():
    """power_progression scanner 的 producer 必须存在且挂在 apply_archive.main 分发链。"""
    assert hasattr(aa, "apply_protagonist_power_tier"), \
        "power_progression_scanner 的 producer 缺失 → scanner 重新沦为孤儿死码"
    src = (_ROOT / "core" / "scripts" / "apply_archive.py").read_text(encoding="utf-8")
    assert "apply_protagonist_power_tier(db, cid, archive, summary" in src, \
        "apply_protagonist_power_tier 未挂 main 分发 → 永不回库"


def test_tier_codes_never_hard_gate():
    """守 19 码三方一致：三个 power_progression code 绝不在 hard_gate_codes。"""
    reg = json.loads((_ROOT / "core" / "scripts" / "scanner_registry.json").read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("POWER_TIER_REGRESSION", "ESCALATION_ACCELERATION_SPIKE", "PROGRESSION_STALL"):
        assert c not in hgs


def test_arc_in_scaffold_known_extras():
    """producer 惰性建的 角色弧线.json 须在 scaffold KNOWN_EXTRAS 白名单（--strict-extras 不误报）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import scaffold_subsystems as scaf  # noqa: E402
    assert "角色弧线.json" in scaf.KNOWN_EXTRAS
