# -*- coding: utf-8 -*-
"""build_manifest actant/cast 注入测试 — 🔴 2026-06-29 actant链接通producer。

钉死 build_manifest 不再**零产** cluster_actant_state / active_cast（此前 actant_drift_scanner +
cast_economy_scanner 整条死码）：
  · _collect_active_cast：scene participants + active_chars → display name 集（id→name 解析·去重排序）
  · _collect_cluster_actant_state：① ledger 已回库本 cluster 条目优先；② 派生 subject=主角 /
    opponent=standing 反派；helper/opponent 经 _norm_actant_multi（1→str / 2+→list 适配双 scanner）
  · 默认安全：无可派生 → []/None（scanner 见空自跳过·无 actant 旧书零行为变化）
  · 主入口 build_manifest() 真注入这两 key（非孤儿）
  · 端到端：注入后的 manifest·两 scanner 真能读（非"跳过"）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
import actant_drift_scanner as ad  # noqa: E402
import cast_economy_scanner as ce  # noqa: E402


def _mk_project(tmp: Path, *, arc=True, antagonist=True, ledger=None, storyboard=True):
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(json.dumps(
        {"characters": [
            {"id": "C_PROT", "name": "伊莱", "role": "主角"},
            {"id": "C_AMY", "name": "艾米", "role": "青梅"},
            {"id": "C_GREEN", "name": "格林", "role": "大执事"}]},
        ensure_ascii=False), encoding="utf-8")
    sb = ([{"ch": 0, "participants": ["C_PROT", "C_AMY", "C_GREEN"]}] if storyboard else [])
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "scene_storyboard": sb,
                       "chapter_range": [1, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    if arc:
        (db / "角色弧线.json").write_text(json.dumps(
            {"characters": {"C_PROT": {"role": "protagonist"}}}, ensure_ascii=False), encoding="utf-8")
    if antagonist:
        (db / "反派轮替.json").write_text(json.dumps(
            {"schema_version": 1, "entries": [
                {"cluster_id": "cluster_001", "antagonist_id": "C_GREEN", "tier": 2}]},
            ensure_ascii=False), encoding="utf-8")
    if ledger is not None:
        (db / "cluster_actant_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return tmp


def _scanner(proj, ch=1):
    return bm.DatabaseScanner(proj, ch)


# ── _collect_active_cast：participants + active_chars → 解析 name 集 ─────────
def test_active_cast_resolves_names_from_participants():
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        s = _scanner(proj)
        cast = bm._collect_active_cast(s, ["伊莱"], "cluster_001")
        assert cast == ["伊莱", "格林", "艾米"] or set(cast) == {"伊莱", "格林", "艾米"}
        # id 已解析成 name（无裸 C_*）
        assert all(not c.startswith("C_") for c in cast)


def test_active_cast_empty_when_no_data():
    """无 storyboard participants 且 active_chars 空 → []（scanner 自跳过·向后兼容）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), storyboard=False)
        s = _scanner(proj)
        assert bm._collect_active_cast(s, [], "cluster_001") == []


# ── _collect_cluster_actant_state 派生：subject=主角·opponent=standing 反派 ──
def test_actant_state_derive_subject_opponent():
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        s = _scanner(proj)
        state = bm._collect_cluster_actant_state(s, "cluster_001")
        assert state["subject"] == "伊莱"
        assert state["opponent"] == "格林"  # standing 反派·单值 str


def test_actant_state_derive_subject_only_no_antagonist():
    """无反派轮替 → 只派生 subject·opponent 缺位（合法·vacancy 是 advisory 信号）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), antagonist=False)
        s = _scanner(proj)
        state = bm._collect_cluster_actant_state(s, "cluster_001")
        assert state == {"subject": "伊莱"}


def test_actant_state_none_when_no_protagonist_no_antagonist():
    """默认安全：无主角无反派 → None（不注入·无 actant 旧书零行为变化）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), arc=False, antagonist=False)
        # 人物卡 去掉主角 role
        (Path(d) / "_数据库" / "人物卡.json").write_text(json.dumps(
            {"characters": [{"id": "C_AMY", "name": "艾米", "role": "青梅"}]},
            ensure_ascii=False), encoding="utf-8")
        s = _scanner(proj)
        assert bm._collect_cluster_actant_state(s, "cluster_001") is None


# ── _collect_cluster_actant_state ledger 优先（re-audit 路径）─────────────────
def test_actant_state_ledger_entry_priority():
    """ledger 已回库本 cluster 条目 → 用它（不走派生）·归一回 1→str。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), ledger={"clusters": [
            {"cluster_id": "cluster_001",
             "assignments": {"subject": "伊莱", "helper": "艾米", "opponent": "格林"}}]})
        s = _scanner(proj)
        state = bm._collect_cluster_actant_state(s, "cluster_001")
        assert state["subject"] == "伊莱"
        assert state["helper"] == "艾米"
        assert state["opponent"] == "格林"


# ── _norm_actant_multi：0→None / 1→str / 2+→list（去重保序）────────────────
def test_norm_actant_multi():
    assert bm._norm_actant_multi([]) is None
    assert bm._norm_actant_multi([None, ""]) is None
    assert bm._norm_actant_multi(["甲"]) == "甲"
    assert bm._norm_actant_multi(["甲", "乙"]) == ["甲", "乙"]
    assert bm._norm_actant_multi(["甲", "甲", "乙"]) == ["甲", "乙"]  # 去重保序


# ── 主入口 build_manifest() 把两 key 接进 manifest 返回字典（防再孤儿·回归锁）──
def test_build_manifest_wires_keys_into_return():
    """源级反孤儿锁：final manifest 返回字典必须接 _collect_active_cast + _collect_cluster_actant_state
    两 collector（此前 build_manifest 零产这两 key → 两 scanner 整条死码）。防有人误删注入行回退死码。"""
    src = (_ROOT / "core" / "scripts" / "build_manifest.py").read_text(encoding="utf-8")
    assert '"active_cast": _collect_active_cast(' in src, "build_manifest 必须把 active_cast 接进 manifest（防孤儿）"
    assert '"cluster_actant_state": _collect_cluster_actant_state(' in src, \
        "build_manifest 必须把 cluster_actant_state 接进 manifest（防孤儿）"


# ── 端到端：注入后的 manifest·两 scanner 真能读（非跳过）────────────────────
def test_injected_manifest_consumable_by_scanners():
    ad_bak = os.environ.get("ACTANT_DRIFT_MODE")
    ce_bak = os.environ.get("CAST_ECONOMY_MODE")
    try:
        os.environ["ACTANT_DRIFT_MODE"] = "active"
        os.environ["CAST_ECONOMY_MODE"] = "active"
        with tempfile.TemporaryDirectory() as d:
            proj = _mk_project(Path(d))
            s = _scanner(proj)
            cast = bm._collect_active_cast(s, ["伊莱"], "cluster_001")
            state = bm._collect_cluster_actant_state(s, "cluster_001")
            # 落成 manifest json 喂 scanner
            mf = Path(d) / "manifest.json"
            mf.write_text(json.dumps(
                {"cluster_id": "cluster_001", "active_cast": cast,
                 "cluster_actant_state": state}, ensure_ascii=False), encoding="utf-8")
            draft = Path(d) / "draft.txt"
            draft.write_text("占位", encoding="utf-8")
            rep_ad = ad.scan(str(draft), project_root=proj, manifest_path=str(mf))
            # subject/opponent 都派生出来 → 非"无 cluster_actant_state 跳过"
            assert "无 cluster_actant_state" not in rep_ad.get("note", "")
            rep_ce = ce.scan(str(draft), project_root=proj, manifest_path=str(mf))
            assert "无 active_cast" not in rep_ce.get("note", ""), "active_cast 非空·cast_economy 不跳过"
    finally:
        for k, b in (("ACTANT_DRIFT_MODE", ad_bak), ("CAST_ECONOMY_MODE", ce_bak)):
            if b is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = b
