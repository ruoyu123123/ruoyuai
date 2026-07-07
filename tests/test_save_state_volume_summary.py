# -*- coding: utf-8 -*-
"""S10 递归卷级层级摘要测试 — 🔴 2026-07-07（Ex3 摘要金字塔 + source 回溯）。

钉死 save_state 的卷级摘要确定性两入口：
  · cmd_detect_volume_boundary：卷边界只用既有信号（大势卡 ME.volume + status=completed
    + completed_by_cluster·唯一维护者=_mark_cluster_me_completed）——某卷 ME 池非空且
    全部 completed 且 故事块摘要.volume_summaries 尚无该卷 → boundary=true；
    无边界 = 零行为变化（故事块摘要 字节不变）。
  · cmd_apply_volume_summary：回库唯一入口——结构键钉死（judge_required_keys 精神）+
    source 必须恰好覆盖本卷全部 cluster_ids（缺=漏源·多=幻觉源·都拒 exit 2 零写入）+
    卷真闭合 + 幂等 upsert（同内容 re-apply 零写盘 churn）。
  · _me_volume_of 与 cluster_emergence_engine._me_volume 双实现口径回归锁。
  · skeleton / plan step7 契约锁。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _me(i, vol=1, status="completed", by=None, finale=False):
    return {"id": f"ME-V{vol}-{i}", "volume": vol, "status": status,
            "completed_by_cluster": by, "is_volume_finale": finale}


def _mk_project(tmp: Path, *, mes=None, summary_doc=None) -> Path:
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(json.dumps(
        {"schema_version": "v27", "major_events": mes or []},
        ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    doc = summary_doc if summary_doc is not None else {
        "schema_version": "v2.cluster", "clusters": []}
    (db / "故事块摘要.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return tmp


# vol1 完整闭合的标准 ME 池（3 个小走向 · 末个 finale）
_CLOSED_V1 = [
    _me(1, by="cluster_001"),
    _me(2, by="cluster_002"),
    _me(3, by="cluster_003", finale=True),
]
_V1_IDS = ["cluster_001", "cluster_002", "cluster_003"]


def _wal_product(root: Path, vol=1, source=None, summary=None, gac="cluster_004", **extra):
    d = {"volume": vol,
         "summary": summary if summary is not None else ("卷一主线推进" + "剧" * 340),
         "source": _V1_IDS if source is None else source,
         "generated_at_cluster": gac}
    d.update(extra)
    p = root / "_数据库" / ".wal" / f"volume_{vol}_summary.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def _ledger(root: Path) -> dict:
    return json.loads((root / "_数据库" / "故事块摘要.json").read_text(encoding="utf-8"))


def _ledger_bytes(root: Path) -> bytes:
    return (root / "_数据库" / "故事块摘要.json").read_bytes()


def _boundary_marker(root: Path, key="cluster_004") -> dict:
    return json.loads((root / "_数据库" / ".wal" / f"{key}_volume_boundary.json")
                      .read_text(encoding="utf-8"))


# ═══════════════════════ skeleton / 口径回归锁 ═══════════════════════

def test_skeleton_has_volume_summaries(tmp_path):
    """故事块摘要 skeleton 必须带 volume_summaries: []（新项目自带载体）。"""
    sk = json.loads((_ROOT / "core" / "claude-home" / "templates" /
                     "subsystem_skeletons.json").read_text(encoding="utf-8"))
    block = sk["skeletons"]["故事块摘要"]
    assert block.get("volume_summaries") == []
    assert "volume_summaries" in str(block.get("_doc", ""))


def test_me_volume_parity_with_emergence_engine(tmp_path):
    """🔴 双实现口径回归锁：save_state._me_volume_of 与 cluster_emergence_engine._me_volume
    对同一输入必须给同一卷号（防两处解析漂移成双口径）。"""
    import cluster_emergence_engine as cee
    samples = [
        {"volume": 2},
        {"volume": "3"},
        {"volume": " 7 "},
        {"id": "ME-V4-2"},
        {"me_id": "ME-v5-1"},
        {"id": "ME-V1-10", "volume": 9},
        {"id": "ME-X"},
        {},
    ]
    for me in samples:
        assert ss._me_volume_of(me) == cee._me_volume(me), f"口径漂移: {me}"


def test_plan_step7_volume_contract(tmp_path):
    """plan 模板 step7 卷边界条件子任务契约锁。"""
    plan = json.loads((_ROOT / "core" / "claude-home" / "plans" /
                       "cluster-save-state.plan.json").read_text(encoding="utf-8"))
    assert plan["total_steps"] == 14
    step7 = next(s for s in plan["steps"] if s["n"] == 7)
    assert step7["must_spawn_agent"] == "novel-summarizer"
    assert any("--detect-volume-boundary" in line for line in step7["scripts"])
    assert "_数据库/.wal/cluster_{key}_volume_boundary.json" in step7["expected_outputs"]
    assert "MODE=volume" in step7["description"]
    assert "--apply-volume-summary" in step7["description"]


# ═══════════════════════ detect：卷边界检测 ═══════════════════════

def test_detect_boundary_positive(tmp_path):
    """vol1 池非空全 completed + 无既有卷摘要 → boundary=true·cluster_ids 升序。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    rc = ss.cmd_detect_volume_boundary(root, "cluster_004")
    assert rc == 0
    marker = _boundary_marker(root)
    assert marker["boundary"] is True
    assert marker["volumes_pending"] == [{"volume": 1, "cluster_ids": _V1_IDS}]
    assert marker["closed_volumes"] == [1]


def test_detect_negative_open_volume_zero_change(tmp_path):
    """vol1 还有 pending ME → boundary=false，且 故事块摘要 字节零变化。"""
    mes = [_me(1, by="cluster_001"), _me(2, status="pending"), _me(3, status="pending", finale=True)]
    root = _mk_project(tmp_path, mes=mes)
    before = _ledger_bytes(root)
    rc = ss.cmd_detect_volume_boundary(root, "cluster_002")
    assert rc == 0
    marker = _boundary_marker(root, "cluster_002")
    assert marker["boundary"] is False
    assert marker["volumes_pending"] == []
    assert _ledger_bytes(root) == before  # 无卷边界=零行为变化


def test_detect_skips_already_summarized_volume(tmp_path):
    """已有 volume_summaries 条目的卷不再 pending（幂等·不重复触发）。"""
    doc = {"schema_version": "v2.cluster", "clusters": [],
           "volume_summaries": [{"volume": 1, "summary": "x", "source": _V1_IDS,
                                 "generated_at_cluster": "cluster_004"}]}
    root = _mk_project(tmp_path, mes=_CLOSED_V1, summary_doc=doc)
    assert ss.cmd_detect_volume_boundary(root, "cluster_004") == 0
    marker = _boundary_marker(root)
    assert marker["boundary"] is False
    assert marker["volumes_summarized"] == [1]


def test_detect_completed_me_without_cluster_registration_not_aggregatable(tmp_path):
    """completed 却缺 completed_by_cluster（源头不全）→ 该卷不可聚合·不误触发。"""
    mes = [_me(1, by="cluster_001"), _me(2, by=None), _me(3, by="cluster_003", finale=True)]
    root = _mk_project(tmp_path, mes=mes)
    assert ss.cmd_detect_volume_boundary(root, "cluster_004") == 0
    assert _boundary_marker(root)["boundary"] is False


def test_detect_missing_ledger_hard_fails(tmp_path):
    """故事块摘要.json 缺失（34 子系统必备）→ exit 2 不降级。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    (root / "_数据库" / "故事块摘要.json").unlink()
    assert ss.cmd_detect_volume_boundary(root, "cluster_004") == 2


# ═══════════════════════ apply：回库唯一入口 ═══════════════════════

def test_apply_happy_path_and_idempotent(tmp_path):
    """合法产物回库成功；同内容 re-apply 幂等（字节零变化·仍 1 条）。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root)
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    vs = _ledger(root)["volume_summaries"]
    assert len(vs) == 1
    entry = vs[0]
    assert entry["volume"] == 1
    assert entry["source"] == _V1_IDS
    assert entry["generated_at_cluster"] == "cluster_004"
    assert entry["applied_at"]
    after_first = _ledger_bytes(root)
    # re-apply 同内容 → 零写盘 churn
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    assert _ledger_bytes(root) == after_first
    assert len(_ledger(root)["volume_summaries"]) == 1


def test_apply_rejects_incomplete_source(tmp_path):
    """source 漏本卷 cluster → 拒绝 exit 2·账本零写入。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root, source=["cluster_001", "cluster_002"])  # 漏 cluster_003
    before = _ledger_bytes(root)
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    assert _ledger_bytes(root) == before
    assert "volume_summaries" not in _ledger(root)


def test_apply_rejects_extra_hallucinated_source(tmp_path):
    """source 多出幻觉 cluster → 拒绝 exit 2。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root, source=_V1_IDS + ["cluster_099"])
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    assert "volume_summaries" not in _ledger(root)


def test_apply_rejects_structural_key_violations(tmp_path):
    """结构键钉死：缺 WAL 产物 / 缺 summary / volume 不一致 / 缺 generated_at_cluster 全拒。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    # 缺 WAL 产物
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    # 缺 summary
    p = _wal_product(root)
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("summary")
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    # volume 与 CLI 参数不一致
    _wal_product(root, vol=1, volume=2)  # extra kw 覆盖 volume 键
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    # 缺合法 generated_at_cluster
    _wal_product(root, gac="")
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    assert "volume_summaries" not in _ledger(root)


def test_apply_rejects_open_volume(tmp_path):
    """卷未闭合（还有 pending ME）→ 即使产物齐全也拒绝（防误触发）。"""
    mes = [_me(1, by="cluster_001"), _me(2, status="pending"), _me(3, status="pending", finale=True)]
    root = _mk_project(tmp_path, mes=mes)
    _wal_product(root, source=["cluster_001"])
    assert ss.cmd_apply_volume_summary(root, 1) == 2
    assert "volume_summaries" not in _ledger(root)


def test_apply_upsert_replaces_changed_content(tmp_path):
    """产物内容变化 → upsert 替换同卷条目（仍 1 条·不累积）。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root, summary="第一版卷摘要" + "剧" * 300)
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    _wal_product(root, summary="第二版卷摘要" + "情" * 300)
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    vs = _ledger(root)["volume_summaries"]
    assert len(vs) == 1
    assert vs[0]["summary"].startswith("第二版卷摘要")


def test_apply_optional_keys_whitelist(tmp_path):
    """可选键白名单透传（emotional_peak/key_turning_points）·未知键不入库。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root, emotional_peak="卷末钟楼坍塌", key_turning_points=["守则反噬", "身份揭底"],
                 hallucinated_field="不该入库")
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    entry = _ledger(root)["volume_summaries"][0]
    assert entry["emotional_peak"] == "卷末钟楼坍塌"
    assert entry["key_turning_points"] == ["守则反噬", "身份揭底"]
    assert "hallucinated_field" not in entry


def test_apply_source_normalization_and_ordering(tmp_path):
    """source 各形态归一（'2'/'cluster_003'/int 序）+ 入库按 cluster 序号升序。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root, source=["cluster_003", "1", "cluster_002"])
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    assert _ledger(root)["volume_summaries"][0]["source"] == _V1_IDS


def test_detect_after_apply_no_longer_pending(tmp_path):
    """端到端幂等闭环：apply 回库后再 detect → 该卷不再 pending（不重复触发）。"""
    root = _mk_project(tmp_path, mes=_CLOSED_V1)
    _wal_product(root)
    assert ss.cmd_apply_volume_summary(root, 1) == 0
    assert ss.cmd_detect_volume_boundary(root, "cluster_004") == 0
    marker = _boundary_marker(root)
    assert marker["boundary"] is False
    assert marker["volumes_summarized"] == [1]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                try:
                    fn(Path(td))
                    print(f"[OK] {name}")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"[FAIL] {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
