# -*- coding: utf-8 -*-
"""抽取载荷终态剥离与确定性终态转移测试。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import save_state as ss  # noqa: E402
import apply_archive as aa  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_project(tmp: Path, fs: dict | None = None) -> Path:
    """创建伏笔终态测试所需的最小 cluster 状态库。"""
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / ".judge_reports").mkdir(parents=True, exist_ok=True)
    if fs is None:
        fs = {"promises": [], "deadlines": [], "pledges": [], "secrets": []}
    (db / "伏笔表.json").write_text(json.dumps(fs, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001"}]},
        ensure_ascii=False), encoding="utf-8")
    return tmp


def _write_foreshadower(tmp: Path, cid: str, payoff_scores: list) -> None:
    (tmp / "_数据库" / ".judge_reports" / f"{cid}_foreshadower.json").write_text(
        json.dumps({"judge_id": "foreshadower", "cluster_id": cid,
                    "specific_findings": {"payoff_scores": payoff_scores}}, ensure_ascii=False),
        encoding="utf-8")


def _read_fs(tmp: Path) -> dict:
    return json.loads((tmp / "_数据库" / "伏笔表.json").read_text(encoding="utf-8"))


def _contract(tmp: Path, cid: str = "cluster_001") -> dict | None:
    p = tmp / "_数据库" / ".wal" / f"{cid}_terminal_contract.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _mk_archive_project(tmp: Path):
    """apply_archive 最小项目（照 test_apply_archive.py 脚手架）。"""
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(json.dumps(
        {"schema_version": 1, "characters": []}, ensure_ascii=False), encoding="utf-8")
    (db / "角色池.json").write_text(json.dumps(
        {"schema_version": 1, "core": [], "emerged": [], "extras": []},
        ensure_ascii=False), encoding="utf-8")
    (db / "道具.json").write_text(json.dumps(
        {"schema_version": 1, "items": []}, ensure_ascii=False), encoding="utf-8")
    (db / "关系.json").write_text(json.dumps(
        {"schema_version": 1, "relationships": []}, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001"}]}, ensure_ascii=False), encoding="utf-8")
    return db


def _write_archive(db: Path, key, obj):
    payload = dict(obj)
    payload.setdefault("cluster_id", f"cluster_{key}")
    (db / ".wal" / f"cluster_{key}_archive.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ═══════════ 抽取类载荷禁携终态：archivist archive ═══════════

def test_archive_terminal_payload_stripped_warned_counted(capsys):
    """archive 携伏笔/戏剧问题终态声明 → 剥离 + stderr 警告 + WAL 计数·合法域照常回库。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_archive_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_X", "name": "X", "tier": "extra", "new": True,
                            "first_cluster": "cluster_001"}],
            "foreshadowing_updates": [{"id": "fs_1", "status": "consumed",
                                       "consumed_at_cluster": "cluster_001",
                                       "_consumed_by": "archivist"}],
            "dramatic_questions": {"answered": [{"qid": "DQ_B"}]},
        })
        rc = aa.main([str(Path(d)), "--cluster", "001"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "terminal_state_stripped=2" in err, err
        # 合法域（角色）照常回库
        pc = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
        assert any(c["id"] == "C_X" for c in pc["characters"])
        # WAL 留痕：sections.archive_strip
        doc = json.loads((db / ".wal" / "cluster_001_terminal_contract.json")
                         .read_text(encoding="utf-8"))
        assert doc["sections"]["archive_strip"]["terminal_state_stripped"] == 2


def test_archive_character_alive_dead_status_not_stripped(capsys):
    """剥离集不误伤：角色 status(dead) 不在 TERMINAL_STATE_VALUES → 0 剥离·状态照常落卡。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_archive_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_D", "name": "D", "tier": "extra", "new": True,
                            "first_cluster": "cluster_001",
                            "status": "dead"}]})
        assert aa.main([str(Path(d)), "--cluster", "001"]) == 0
        assert "terminal_state_stripped" not in capsys.readouterr().err
        pc = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
        assert pc["characters"][0]["status"] == "dead"
        assert not (db / ".wal" / "cluster_001_terminal_contract.json").exists()


def test_archive_dry_run_strips_warns_but_no_wal(capsys):
    """dry-run：仍警告（可见性）但不写契约 WAL（dry 不落盘一致性）。"""
    with tempfile.TemporaryDirectory() as d:
        db = _mk_archive_project(Path(d))
        _write_archive(db, "001", {
            "characters": [{"id": "C_X", "name": "X", "tier": "extra",
                            "first_cluster": "cluster_001"}],
            "foreshadowing_updates": [{"id": "fs_1", "status": "consumed"}]})
        assert aa.main([str(Path(d)), "--cluster", "001", "--dry-run"]) == 0
        assert "terminal_state_stripped=1" in capsys.readouterr().err
        assert not (db / ".wal" / "cluster_001_terminal_contract.json").exists()


# ═══════════ B. 终态唯一通路：_apply_foreshadower_payoffs 写入层硬校验 ═══════════

def test_payoff_target_missing_rejected_others_still_apply(capsys):
    """终态转移目标缺失（孤儿 payoff）→ 拒绝该条 + 警告·其余条目照常落库（不整体崩）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_ok", "setup_cluster": "cluster_001", "status": "open"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_ghost", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "evidence": "正文有一段像是兑现"},
            {"fs_id": "fs_ok", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "reason": "核心承诺当场兑现"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")  # 不抛异常
        assert _read_fs(tmp)["promises"][0]["status"] == "consumed"
        err = capsys.readouterr().err
        assert "fs_ghost" in err and "target_missing" in err, err
        sec = _contract(tmp)["sections"]["foreshadower_payoff"]
        assert sec["checked"] == 2 and sec["terminal_applied"] == 1
        assert sec["rejections"] == [{"fs_id": "fs_ghost", "code": "target_missing",
                                      "note": "伏笔表不存在该条目（孤儿 payoff）"}]


def test_payoff_target_not_open_rejected(capsys):
    """目标 status 非 open/suspended 时写入层硬拒。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_legacy", "setup_cluster": "cluster_001", "status": "planted"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_legacy", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "reason": "有正文证据"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        p = _read_fs(tmp)["promises"][0]
        assert p["status"] == "planted" and "consumed_at_cluster" not in p
        assert "target_not_open" in capsys.readouterr().err
        sec = _contract(tmp)["sections"]["foreshadower_payoff"]
        assert sec["rejections"][0]["code"] == "target_not_open"


def test_payoff_evidence_missing_rejected(capsys):
    """终态转移缺正文证据（evidence/span/reason 全空）→ 拒绝·目标仍 open。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_ne", "setup_cluster": "cluster_001", "status": "open"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_ne", "score": 5, "terminal": True, "verdict": "paid_terminal"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        assert _read_fs(tmp)["promises"][0]["status"] == "open"
        assert "evidence_missing" in capsys.readouterr().err
        sec = _contract(tmp)["sections"]["foreshadower_payoff"]
        assert sec["terminal_applied"] == 0
        assert sec["rejections"][0]["code"] == "evidence_missing"


def test_payoff_with_evidence_or_span_applies():
    """合法 payoff 携 evidence 或 span 时写入 consumed cluster。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_a", "setup_cluster": "cluster_001", "status": "open"},
                         {"id": "fs_b", "setup_cluster": "cluster_001", "status": "open"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_a", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "evidence": "第3章遗嘱墨迹当场变淡·核心承诺兑现"},
            {"fs_id": "fs_b", "score": 4, "terminal": True, "verdict": "paid_terminal",
             "span": "ch3:『墨迹淡去』段"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        pmap = {p["id"]: p for p in _read_fs(tmp)["promises"]}
        for pid in ("fs_a", "fs_b"):
            assert pmap[pid]["status"] == "consumed"
            assert pmap[pid]["consumed_at_cluster"] == "cluster_001"
            assert pmap[pid]["_consumed_by"] == "foreshadower"
        sec = _contract(tmp)["sections"]["foreshadower_payoff"]
        assert sec["terminal_applied"] == 2 and sec["rejections"] == []


def test_payoff_reason_counts_as_evidence():
    """foreshadower 现行 schema 的 reason（正文凭证字段）计为证据 → 不误拒存量口径。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_r", "setup_cluster": "cluster_001", "status": "open"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_r", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "reason": "登记册正式成立·正文第3章点破"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        assert _read_fs(tmp)["promises"][0]["status"] == "consumed"


def test_payoff_suspended_terminal_with_evidence_still_consumes():
    """suspended 目标携有效证据时允许 terminal 回收并记录警示。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_s", "setup_cluster": "cluster_001", "status": "suspended"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_s", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "reason": "挂起的旧伤线被尸检报告正面点破"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        p = _read_fs(tmp)["promises"][0]
        assert p["status"] == "consumed" and p["consumed_at_cluster"] == "cluster_001"
        assert _contract(tmp)["sections"]["foreshadower_payoff"]["rejections"] == []


def test_payoff_already_consumed_idempotent_not_rejected():
    """已 consumed 再收 terminal payoff → 幂等静默跳过（不后移·不算拒绝·re-apply 常态）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), fs={
            "promises": [{"id": "fs_c", "setup_cluster": "cluster_001", "status": "consumed",
                          "consumed_at_cluster": "cluster_001",
                          "_consumed_by": "foreshadower"}],
            "deadlines": [], "pledges": [], "secrets": []})
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_c", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "reason": "再次声明兑现"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        p = _read_fs(tmp)["promises"][0]
        assert p["status"] == "consumed"
        assert p["consumed_at_cluster"] == "cluster_001"
        sec = _contract(tmp)["sections"]["foreshadower_payoff"]
        assert sec["terminal_applied"] == 0 and sec["rejections"] == []


# ═══════════ B. 终态唯一通路：cmd_apply_dramatic_questions answered 硬校验 ═══════════

def _mk_dq_project(tmp: Path, cid: str, dq: dict, ledger: dict) -> Path:
    db = tmp / "_数据库"
    (db / ".judge_reports").mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    (db / ".judge_reports" / f"{cid}_foreshadower.json").write_text(json.dumps(
        {"judge_id": "foreshadower", "cluster_id": cid,
         "specific_findings": {"payoff_scores": [], "dramatic_questions": dq}},
        ensure_ascii=False), encoding="utf-8")
    (db / "戏剧问题账本.json").write_text(json.dumps(ledger, ensure_ascii=False),
                                      encoding="utf-8")
    return tmp


def test_dramatic_answered_already_closed_elsewhere_rejected(capsys):
    """qid 已在其他 cluster 闭合（非 open）→ 拒绝重复终态转移 + 警告 + WAL·不整体崩。"""
    with tempfile.TemporaryDirectory() as d:
        ledger = {"schema_version": 1, "clusters": {
            "cluster_001": {"raised": [{"qid": "DQ_X", "question": "q", "scope": "volume",
                                        "raised_at_scene": 0, "expected_payoff_window": ""}],
                            "answered": [{"qid": "DQ_X", "answered_at_scene": 2}]}}}
        tmp = _mk_dq_project(Path(d), "cluster_002",
                             {"raised": [], "answered": [{"qid": "DQ_X", "answered_at_scene": 1}]},
                             ledger)
        assert ss.cmd_apply_dramatic_questions(tmp, "002") == 0
        led = json.loads((tmp / "_数据库" / "戏剧问题账本.json").read_text(encoding="utf-8"))
        # cluster_001 的闭合记录不动；cluster_002 未登记重复闭合
        assert len(led["clusters"]["cluster_001"]["answered"]) == 1
        c2 = led["clusters"].get("cluster_002")
        assert not c2 or c2.get("answered") in (None, [])
        assert "拒绝重复" in capsys.readouterr().err
        sec = _contract(tmp, "cluster_002")["sections"]["dramatic_questions"]
        assert sec["answered_applied"] == 0
        assert sec["rejections"][0] == {"qid": "DQ_X", "code": "target_not_open",
                                        "note": "已在 cluster_001 闭合·拒绝重复终态转移"}


def test_dramatic_answered_missing_scene_evidence_rejected(capsys):
    """answered 缺 answered_at_scene（正文位置证据）→ 拒绝该条·合法条目照常登记。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_dq_project(Path(d), "cluster_001",
                             {"raised": [], "answered": [
                                 {"qid": "DQ_NO_EVID"},
                                 {"qid": "DQ_OK", "answered_at_scene": 3}]},
                             {"schema_version": 1, "clusters": {}})
        assert ss.cmd_apply_dramatic_questions(tmp, "001") == 0
        led = json.loads((tmp / "_数据库" / "戏剧问题账本.json").read_text(encoding="utf-8"))
        ans = led["clusters"]["cluster_001"]["answered"]
        assert [a["qid"] for a in ans] == ["DQ_OK"]
        assert "evidence_missing" not in {a.get("qid") for a in ans}
        assert "DQ_NO_EVID" in capsys.readouterr().err
        sec = _contract(tmp)["sections"]["dramatic_questions"]
        assert sec["answered_applied"] == 1
        assert sec["rejections"][0]["qid"] == "DQ_NO_EVID"
        assert sec["rejections"][0]["code"] == "evidence_missing"


# ═══════════ C. 回归锁：入库层源码必含类级契约关键代码 ═══════════

def test_regression_lock_contract_code_present_in_intake_layer():
    """grep 断言：契约实现被未来清理误删时此锁先红（防复发）。"""
    ss_src = (_SCRIPTS / "save_state.py").read_text(encoding="utf-8")
    common_src = (_SCRIPTS / "save_state_common.py").read_text(encoding="utf-8")
    aa_src = (_SCRIPTS / "apply_archive.py").read_text(encoding="utf-8")
    assert 'TERMINAL_STATE_VALUES = frozenset({"consumed", "resolved", "answered"})' in common_src
    assert "def strip_terminal_state_payload" in common_src
    assert "def record_terminal_contract" in common_src
    assert "from save_state_common import" in ss_src
    # 伏笔与戏剧问题终态由确定性写入层校验。
    contract_sources = ss_src + common_src + aa_src
    for marker in ("target_missing", "target_not_open", "evidence_missing",
                   "terminal_state_stripped", "_terminal_contract.json"):
        assert marker in contract_sources, marker
    # apply_archive 消费同一实现（不另立口径）
    assert "from save_state_common import record_terminal_contract, strip_terminal_state_payload" in aa_src
    assert "strip_terminal_state_payload(archive)" in aa_src
    assert "terminal_state_stripped" in aa_src
    # 剥离集边界：不误伤 secrets 明暗线 / ME completed / 角色 alive/dead
    assert "revealed" not in ss.TERMINAL_STATE_VALUES
    assert "completed" not in ss.TERMINAL_STATE_VALUES
    assert "dead" not in ss.TERMINAL_STATE_VALUES
    # 纪律：契约拒绝码不进 hard_gate 清单（不新增 hard_gate code）
    import audit_hub
    for code in ("TARGET_MISSING", "TARGET_NOT_OPEN", "EVIDENCE_MISSING",
                 "TERMINAL_STATE_STRIPPED"):
        assert code not in audit_hub.HARD_GATE_CODES


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
