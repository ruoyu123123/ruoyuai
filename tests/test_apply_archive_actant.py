# -*- coding: utf-8 -*-
"""apply_archive.py actant 回库测试。

钉死 apply_actant_state：archivist 读正文判定本块六位 Greimas actant → archive.cluster_actant_state
→ 确定性写进 cluster_actant_ledger.json clusters[].assignments：
  · char_id → 人物卡 name 解析（命名空间统一到 name·对齐 cast_economy known 集 + scanner 可哈希键）
  · helper/opponent list → 首位代表存单值（scanner ledger schema 单值·list 值会崩 scanner）
  · 幂等：按 cluster_id 去重替换·不重复 append
  · archive 无 cluster_actant_state → 零变更且不建 ledger 文件
  · dry-run 不写盘
  · 端到端：producer 落的 ledger·两 scanner 真能读（非"跳过"）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import apply_archive as aa  # noqa: E402
import actant_drift_scanner as ad  # noqa: E402
import cast_economy_scanner as ce  # noqa: E402


def _mk_project(tmp: Path):
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(json.dumps(
        {"schema_version": 1, "characters": [
            {"id": "C_PROT", "name": "伊莱", "role": "主角"},
            {"id": "C_AMY", "name": "艾米", "role": "青梅"},
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
    payload = dict(obj)
    payload.setdefault("cluster_id", f"cluster_{key}")
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _ledger(db):
    return json.loads((db / "cluster_actant_ledger.json").read_text(encoding="utf-8"))


def _archive_with_actant(state):
    # main() 要求 characters 非空
    return {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}],
            "cluster_actant_state": state}


def _entry(db, cid="cluster_001"):
    for r in _ledger(db)["clusters"]:
        if r.get("cluster_id") == cid:
            return r["assignments"]
    raise AssertionError(f"无 {cid} 条目")


def _draft():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write("占位草稿·scanner 实际读 manifest")
    f.close()
    return Path(f.name)


def _manifest(**kw):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False)
    f.write(json.dumps(kw, ensure_ascii=False))
    f.close()
    return Path(f.name)


# ── 基础：id→name 解析 + 六位写入 assignments ──────────────────────────────
def test_actant_writes_ledger_id_resolved():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant({
            "subject": "C_PROT", "object": "I_WILL", "sender": "C_AMY",
            "receiver": "C_PROT", "helper": ["C_AMY"], "opponent": ["C_GREEN"]}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        a = _entry(db)
        # id → name 解析（人物卡有的解析·没建卡的 I_WILL 原样）
        assert a["subject"] == "伊莱"
        assert a["sender"] == "艾米"
        assert a["receiver"] == "伊莱"
        assert a["helper"] == "艾米"      # list 首位代表·单值
        assert a["opponent"] == "格林"    # list 首位代表·单值
        assert a["object"] == "I_WILL"    # 非人物卡 id → 原样


# ── helper/opponent 多角色取首位代表（scanner ledger 单值·防 list 崩 scanner）──
def test_multi_helper_takes_first_representative():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant({
            "subject": "C_PROT", "opponent": ["C_GREEN", "C_BISHOP"]}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        a = _entry(db)
        assert a["opponent"] == "格林"  # 首位
        assert isinstance(a["opponent"], str), "ledger 值必须可哈希字符串·绝不存 list"


# ── 幂等：re-apply 同 cluster 替换·不重复 ──────────────────────────────────
def test_actant_idempotent_replace():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant({"subject": "C_PROT", "opponent": ["C_GREEN"]}))
        aa.main([str(Path(d)), "--cluster", "001"])
        aa.main([str(Path(d)), "--cluster", "001"])  # 再跑
        assert len(_ledger(db)["clusters"]) == 1
        # 改 archive 再 apply → 同 cluster 就地替换
        _write_archive(db, "001", _archive_with_actant({"subject": "C_PROT", "opponent": ["C_BISHOP"]}))
        aa.main([str(Path(d)), "--cluster", "001"])
        assert len(_ledger(db)["clusters"]) == 1
        assert _entry(db)["opponent"] == "主教"


# ── 不同 cluster → 各 append 独立条目 ──────────────────────────────────────
def test_distinct_clusters_append():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant({"subject": "C_PROT", "helper": ["C_AMY"]}))
        aa.main([str(Path(d)), "--cluster", "001"])
        _write_archive(db, "002", _archive_with_actant({"subject": "C_PROT", "opponent": ["C_AMY"]}))
        aa.main([str(Path(d)), "--cluster", "002"])
        assert len(_ledger(db)["clusters"]) == 2


# ── 无 cluster_actant_state → 零变更且不建文件 ──────────
def test_no_actant_noop_backward_compat():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", {"characters": [{"id": "C_PROT", "name": "伊莱", "tier": "core"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "cluster_actant_ledger.json").exists(), "无 actant 段不应建 ledger"


def test_empty_actant_dict_noop():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant({}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "cluster_actant_ledger.json").exists()


def test_all_null_positions_noop():
    """六位全 null / 空 → 无有效 assignment → no-op·不建文件。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant(
            {"subject": None, "opponent": [], "helper": []}))
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert not (db / "cluster_actant_ledger.json").exists()


# ── dry-run 不写盘 ─────────────────────────────────────────────────────────
def test_actant_dry_run_no_write():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_project(Path(d))
        _write_archive(db, "001", _archive_with_actant({"subject": "C_PROT", "opponent": ["C_GREEN"]}))
        assert aa.main([str(Path(d)), "--cluster", "001", "--dry-run"]) == 0
        assert not (db / "cluster_actant_ledger.json").exists()


# ── 端到端：producer 落的 ledger·actant_drift + cast_economy 真能读（非跳过）──
def test_produced_ledger_consumable_by_scanners():
    """cluster_001 艾米=helper 回库 → cluster_002 manifest 艾米=opponent →
    actant_drift(helper→opponent 无 pivot 漂移) + cast_economy(隐式 role_split) 都用
    producer 落的 ledger 检出（证明 producer→ledger→scanner 链路活·非"跳过"死码）。"""
    ad_bak = os.environ.get("ACTANT_DRIFT_MODE")
    ce_bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        os.environ["ACTANT_DRIFT_MODE"] = "active"
        os.environ["CAST_ECONOMY_MODE"] = "active"
        with tempfile.TemporaryDirectory() as d:
            db = _mk_project(Path(d))
            # cluster_001：艾米 = helper（回库历史台账）
            _write_archive(db, "001", _archive_with_actant({"subject": "C_PROT", "helper": ["C_AMY"]}))
            aa.main([str(Path(d)), "--cluster", "001"])
            assert _entry(db)["helper"] == "艾米"

            # cluster_002 manifest：艾米 翻 opponent·无 pivot
            mf = _manifest(cluster_id="cluster_002", active_cast=["艾米"],
                           cluster_actant_state={"subject": "伊莱", "opponent": "艾米"})
            rep_ad = ad.scan(str(_draft()), project_root=Path(d), manifest_path=str(mf))
            codes_ad = [v["code"] for v in rep_ad["violations"]]
            assert "ACTANT_DRIFT_NO_PIVOT" in codes_ad, "scanner 应据 producer ledger 检出漂移·非跳过"
            assert "跳过" not in rep_ad.get("note", "")

            rep_ce = ce.scan(str(_draft()), project_root=Path(d), manifest_path=str(mf))
            codes_ce = [v["code"] for v in rep_ce["violations"]]
            assert "CAST_ROLE_SPLIT_IMPLICIT" in codes_ce, "cast_economy 应据 producer ledger 检出 role_split"
    finally:
        for k, b in (("ACTANT_DRIFT_MODE", ad_bak), ("CAST_ECONOMY_MODE", ce_bak)):
            if b is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = b


# ── ledger 在 scaffold KNOWN_EXTRAS 白名单（--strict-extras 不误报）──────────
def test_ledger_in_scaffold_known_extras():
    import scaffold_subsystems as scaf  # noqa: E402
    assert "cluster_actant_ledger.json" in scaf.KNOWN_EXTRAS


# ── 守 19 码三方一致：actant/cast code 绝不在 hard_gate_codes ────────────────
def test_actant_cast_codes_never_hard_gate():
    reg = json.loads((_ROOT / "core" / "scripts" / "scanner_registry.json").read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("ACTANT_DRIFT_NO_PIVOT", "ACTANT_VACANCY", "ACTANT_OVERLOADED",
              "CAST_INTRODUCE_BURST", "CAST_COMPOSITE_HINT", "CAST_ROLE_SPLIT_IMPLICIT"):
        assert c not in hgs, c
