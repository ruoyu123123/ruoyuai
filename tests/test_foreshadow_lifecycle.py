# -*- coding: utf-8 -*-
"""伏笔生命周期三态枚举回归锁（2026-07-06 P1 · 借鉴 PlotPilot foreshadow registry）。

契约（本文件钉死）：
  1. 伏笔表.promises 三态 status ∈ {open, suspended, consumed}（枚举权威
     db_schema_validate.FORESHADOW_STATUS_ENUM）+ owner（缺省 writer）+ payoff_scope（可空串）。
  2. 迁移器挂 db_schema_validate --auto-migrate：resolved True→consumed / False→open 且删
     resolved；resolved_at_ch→consumed_at_ch、_resolved_by→_consumed_by；历史 status 字符串
     （planted/paid/...）归一进三态；幂等（再跑零变化）。
  3. 枚举白名单：非法/缺失 status → FORESHADOW_STATUS_INVALID error（含 --post-edit 手改路径）。
  4. 消费方只读 status：due_foreshadowing 里 consumed 跳过、suspended 不催收只计数、open 走到期。
  5. payoff 必须引用 open 项：foreshadowing_handoff_scanner 对 changes 声明的 payoff 指向非 open
     条目产 FORESHADOWING_PAYOFF_TARGET_NOT_OPEN（advisory·绝不 hard_gate）。
  6. secrets[] 的 hidden/revealed 语义（明暗线隔离）不受本枚举影响。
  7. 🔴 2026-07-07 S3 类级契约（PlotPilot reducer 范式）：终态唯一通路 =
     save_state._apply_foreshadower_payoffs 写入层硬校验——terminal 转移须目标 status ∈
     {open, suspended} 且携正文证据（evidence/span/reason 非空），不合格逐条拒绝进 WAL；
     suspended 被 terminal 回收仍为警示后照常落账（本文件第 5 节钉死）。
     专项测试见 tests/test_terminal_state_contract.py。

纯确定性（0 gen-model 调用）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import db_schema_validate as dsv  # noqa: E402
import build_manifest as bm  # noqa: E402
import foreshadowing_handoff_scanner as fhs  # noqa: E402
import save_state as ss  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_db(td) -> Path:
    db = Path(td) / "_数据库"
    db.mkdir(parents=True)
    return db


def _write_fs(db: Path, promises, secrets=None) -> Path:
    p = db / "伏笔表.json"
    p.write_text(json.dumps({
        "schema_version": "v27", "promises": promises,
        "deadlines": [], "pledges": [], "secrets": secrets or [],
    }, ensure_ascii=False), encoding="utf-8")
    return p


def _read_fs(db: Path) -> dict:
    return json.loads((db / "伏笔表.json").read_text(encoding="utf-8"))


# ═══════════ 1. 迁移器：resolved → 三态 status（幂等）═══════════

def test_migrate_resolved_true_to_consumed_and_false_to_open():
    """resolved True→consumed / False→open；resolved 字段删除（不留兼容读）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [
            {"id": "fs_1", "setup_cluster": "cluster_001", "tier": 1, "resolved": True},
            {"id": "fs_2", "setup_cluster": "cluster_001", "tier": 2, "resolved": False},
        ])
        errs, warns, mig = dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        assert mig is True and errs == [], (errs, warns)
        pmap = {p["id"]: p for p in _read_fs(db)["promises"]}
        assert pmap["fs_1"]["status"] == "consumed"
        assert pmap["fs_2"]["status"] == "open"
        assert "resolved" not in pmap["fs_1"] and "resolved" not in pmap["fs_2"]


def test_migrate_fills_owner_and_payoff_scope():
    """owner 缺省 writer；payoff_scope 从 due_by 族派生（无到期信息 → 空串·允许）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [
            {"id": "fs_due", "resolved": False, "due_by": 15},
            {"id": "fs_dbc", "resolved": False, "due_by_cluster": "cluster_005"},
            {"id": "fs_pend", "resolved": False, "setup_cluster": "cluster_002",
             "due_by_pending_resolution": True, "due_by_ch_offset": 20},
            {"id": "fs_none", "resolved": False},
            {"id": "fs_owned", "resolved": False, "owner": "林越"},
        ])
        dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        pmap = {p["id"]: p for p in _read_fs(db)["promises"]}
        for pid in pmap:
            assert pmap[pid]["status"] in dsv.FORESHADOW_STATUS_ENUM
        assert pmap["fs_due"]["owner"] == "writer"
        assert "15" in pmap["fs_due"]["payoff_scope"]
        assert "cluster_005" in pmap["fs_dbc"]["payoff_scope"]
        assert "cluster_002" in pmap["fs_pend"]["payoff_scope"] and "20" in pmap["fs_pend"]["payoff_scope"]
        assert pmap["fs_none"]["payoff_scope"] == ""  # 允许空串
        assert pmap["fs_owned"]["owner"] == "林越"  # 已有 owner 不覆盖


def test_migrate_legacy_status_strings_and_field_renames():
    """历史 status 字符串归一（planted→open / paid→consumed）；resolved_at_ch/_resolved_by 更名。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [
            {"id": "fs_p", "status": "planted"},
            {"id": "fs_paid", "status": "paid", "resolved_at_ch": 9, "_resolved_by": "foreshadower"},
            {"id": "fs_susp", "status": "suspended"},
        ])
        errs, warns, mig = dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        assert mig is True and errs == [], (errs, warns)
        pmap = {p["id"]: p for p in _read_fs(db)["promises"]}
        assert pmap["fs_p"]["status"] == "open"
        assert pmap["fs_paid"]["status"] == "consumed"
        assert pmap["fs_paid"]["consumed_at_ch"] == 9
        assert pmap["fs_paid"]["_consumed_by"] == "foreshadower"
        assert "resolved_at_ch" not in pmap["fs_paid"] and "_resolved_by" not in pmap["fs_paid"]
        assert pmap["fs_susp"]["status"] == "suspended"  # 已是三态·不动


def test_migrate_idempotent_second_run_no_change():
    """幂等：迁移后再跑 --auto-migrate 零变化（不再落盘、不再报 MIGRATED）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [{"id": "fs_1", "resolved": True, "due_by": 30}])
        _, _, mig1 = dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        assert mig1 is True
        snapshot = (db / "伏笔表.json").read_text(encoding="utf-8")
        errs2, warns2, mig2 = dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        assert mig2 is False and errs2 == []
        assert not any("MIGRATED" in w for w in warns2)
        assert (db / "伏笔表.json").read_text(encoding="utf-8") == snapshot


def test_migrate_does_not_touch_secrets():
    """secrets[] 的 hidden/revealed 语义（明暗线隔离机制）不被 promises 迁移误改。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        secrets = [{"id": "SEC_1", "secret": "校长是怪谈本体", "status": "hidden",
                    "reveal_at_cluster": "cluster_009"}]
        _write_fs(db, [{"id": "fs_1", "resolved": False}], secrets=secrets)
        dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        after = _read_fs(db)
        assert after["secrets"] == secrets  # 一字不动


# ═══════════ 2. 枚举白名单校验 ═══════════

def test_invalid_status_reports_error():
    """非法 status（不在三态枚举）→ FORESHADOW_STATUS_INVALID error。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [{"id": "fs_bad", "status": "half_done"}])
        errs, _, _ = dsv.check_foreshadow_lifecycle(db, auto_migrate=False)
        assert any("FORESHADOW_STATUS_INVALID" in e and "fs_bad" in e for e in errs), errs


def test_missing_status_without_migrate_reports_error():
    """缺 status 且未跑 --auto-migrate → 同样报错（required 不降 advisory）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [{"id": "fs_legacy", "resolved": False}])
        errs, _, _ = dsv.check_foreshadow_lifecycle(db, auto_migrate=False)
        assert any("FORESHADOW_STATUS_INVALID" in e for e in errs), errs


def test_valid_tristate_clean():
    """三态合法值 → 0 error。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        _write_fs(db, [
            {"id": "a", "status": "open", "owner": "writer", "payoff_scope": ""},
            {"id": "b", "status": "suspended", "owner": "writer", "payoff_scope": ""},
            {"id": "c", "status": "consumed", "owner": "writer", "payoff_scope": ""},
        ])
        errs, _, mig = dsv.check_foreshadow_lifecycle(db, auto_migrate=True)
        assert errs == [] and mig is False


def test_post_edit_revalidate_catches_invalid_status():
    """/db 手改把 status 改成非法值 → --post-edit 重校验 exit 2（手改不静默迁移）。"""
    with tempfile.TemporaryDirectory() as td:
        db = _mk_db(td)
        p = _write_fs(db, [{"id": "fs_x", "status": "resolved"}])  # 手改残留旧口径
        assert dsv.revalidate_after_manual(p) == 2


# ═══════════ 3. 消费方：due_foreshadowing 三态呈现 ═══════════

def _mk_due_project(td, promises) -> Path:
    proj = Path(td) / "book"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    _write_fs(db, promises)
    return proj


def test_due_foreshadowing_tristate():
    """open+到期 → 进 tier due；suspended → 不催收只计数；consumed → 完全跳过。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_due_project(td, [
            {"id": "fs_open", "status": "open", "tier": 1, "due_by": 3},
            {"id": "fs_susp", "status": "suspended", "tier": 1, "due_by": 3},
            {"id": "fs_done", "status": "consumed", "tier": 1, "due_by": 3},
            {"id": "fs_open_t2", "status": "open", "tier": 2, "due_by": 3},
        ])
        s = bm.DatabaseScanner(proj, 5)  # 当前 ch=5 > due_by=3 → 到期
        due = s.due_foreshadowing()
        t1_ids = [p["id"] for p in due["promises_tier1_due"]]
        t2_ids = [p["id"] for p in due["promises_tier2_due"]]
        assert t1_ids == ["fs_open"]
        assert t2_ids == ["fs_open_t2"]
        assert due["promises_suspended_count"] == 1
        blob = json.dumps(due, ensure_ascii=False)
        assert "fs_done" not in blob and "fs_susp" not in blob


def test_due_foreshadowing_open_not_yet_due():
    """open 但未到期 → 不进 due 列表（到期判定不受生命周期改动影响）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_due_project(td, [
            {"id": "fs_future", "status": "open", "tier": 1, "due_by": 30},
        ])
        due = bm.DatabaseScanner(proj, 5).due_foreshadowing()
        assert due["promises_tier1_due"] == []
        assert due["promises_suspended_count"] == 0


# ═══════════ 4. payoff 必须引用 open 项（handoff scanner advisory）═══════════

def _mk_scan_project(td, promises, payoff_actions) -> Path:
    proj = Path(td) / "book"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    _write_fs(db, promises)
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 3],
         "foreshadowing_to_plant": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "cluster_001_draft.txt").write_text("正文占位。", encoding="utf-8")
    (draft_dir / "cluster_001_changes.json").write_text(json.dumps({
        "factual": {"foreshadowing_actions": payoff_actions},
    }, ensure_ascii=False), encoding="utf-8")
    return proj


def test_payoff_target_not_open_advisory():
    """payoff 指向 consumed/suspended 条目 → FORESHADOWING_PAYOFF_TARGET_NOT_OPEN advisory。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_scan_project(td, [
            {"id": "fs_done", "setup_cluster": "cluster_009", "status": "consumed"},
            {"id": "fs_susp", "setup_cluster": "cluster_009", "status": "suspended"},
        ], [
            {"category": "promise", "type": "payoff", "id": "fs_done"},
            {"category": "promise", "type": "payoff", "id": "fs_susp"},
        ])
        rep = fhs.scan(proj, "cluster_001")
        issue = next((i for i in rep["issues"]
                      if i["code"] == "FORESHADOWING_PAYOFF_TARGET_NOT_OPEN"), None)
        assert issue is not None, rep["issues"]
        assert issue["gate_level"] == "advisory"  # 绝不 hard_gate（北极星⑤）
        assert issue["count"] == 2
        assert {it["id"] for it in issue["items"]} == {"fs_done", "fs_susp"}


def test_payoff_target_open_or_unknown_no_issue():
    """payoff 指向 open 条目 / 不在伏笔表的 id（brief 未注册·scanner 时点常态）→ 不报。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_scan_project(td, [
            {"id": "fs_open", "setup_cluster": "cluster_009", "status": "open"},
        ], [
            {"category": "promise", "type": "payoff", "id": "fs_open"},
            {"category": "promise", "type": "payoff", "id": "fs_not_registered_yet"},
        ])
        rep = fhs.scan(proj, "cluster_001")
        assert not any(i["code"] == "FORESHADOWING_PAYOFF_TARGET_NOT_OPEN"
                       for i in rep["issues"]), rep["issues"]


# ═══════════ 5. 落账处：suspended 被 terminal 回收 → 照常转 consumed ═══════════

def test_suspended_terminal_payoff_transitions_to_consumed():
    """suspended（未回收态）被 foreshadower terminal 回收 → 转 consumed（advisory 警示不拦）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "book"
        db = proj / "_数据库"
        (db / ".judge_reports").mkdir(parents=True)
        _write_fs(db, [{"id": "fs_s", "setup_cluster": "cluster_001", "status": "suspended"}])
        (db / "事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]}]}, ensure_ascii=False),
            encoding="utf-8")
        (db / ".judge_reports" / "cluster_001_foreshadower.json").write_text(json.dumps({
            "judge_id": "foreshadower", "cluster_id": "cluster_001",
            "specific_findings": {"payoff_scores": [
                {"fs_id": "fs_s", "score": 5, "terminal": True, "verdict": "paid_terminal",
                 # S3 契约：终态转移须携正文证据（reason=foreshadower schema 的正文凭证字段）
                 "reason": "尸检报告正面点破旧伤来源·核心承诺兑现"}]},
        }, ensure_ascii=False), encoding="utf-8")
        ss._apply_foreshadower_payoffs(proj, "cluster_001")
        p = _read_fs(db)["promises"][0]
        assert p["status"] == "consumed"
        assert p["consumed_at_ch"] == 3 and p["_consumed_by"] == "foreshadower"


if __name__ == "__main__":
    import traceback
    g = dict(globals())
    fails = 0
    for n in sorted(g):
        if n.startswith("test_"):
            try:
                g[n]()
                print("OK", n)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", n, e)
                traceback.print_exc()
    sys.exit(1 if fails else 0)
