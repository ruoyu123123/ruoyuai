# -*- coding: utf-8 -*-
"""plan_end_receipt.py 回归锁：收尾 step 的可验证 plan 最终校验。

cluster-write step7 / cluster-save-state step14 是「plan 最终校验」步。旧模板把它们的
expected_output 设成裸目录 `_数据库/.wal`（零校验 · 触发 PostToolUse hook 越权）。改为跑
plan_end_receipt.py 逐条核对前置 required step 真完成（status=completed 且 verified_outputs
实体文件仍在）→ 写收尾回执。本测试锁死假完成/产物丢失/前置未完成都必须 [FATAL]。
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import plan_end_receipt as per
import plan_tracker


def _install(monkeypatch, project: Path, plan: dict):
    monkeypatch.setattr(plan_tracker, "verify_plan", lambda pid: "ok")
    monkeypatch.setattr(plan_tracker, "get_plan", lambda pid: plan)
    monkeypatch.setattr(plan_tracker, "resolve_project_root", lambda p: project)
    monkeypatch.setattr(plan_tracker, "cluster_id_from_key",
                        lambda k: f"cluster_{k}" if k else None)


def _plan(project: Path, steps):
    return {
        "id": "PID", "command": "cluster-write", "project": str(project),
        "cluster_id": "cluster_001", "key": "001", "steps": steps,
    }


def test_happy_path_writes_receipt(tmp_path, monkeypatch):
    out = tmp_path / "_数据库" / ".wal" / "cluster_001_pre_write_gate.json"
    out.parent.mkdir(parents=True)
    out.write_text("{}", encoding="utf-8")
    plan = _plan(tmp_path, [
        {"n": 1, "name": "manifest", "required": True, "status": "completed",
         "verified_outputs": [str(out)]},
        {"n": 7, "name": "plan-end", "required": True, "status": "pending"},
    ])
    _install(monkeypatch, tmp_path, plan)
    receipt = per.build_receipt(tmp_path, "PID", "7", "cluster-write")
    assert receipt["completed"] is True
    assert receipt["cluster_id"] == "cluster_001"
    assert receipt["verified_required_steps"] == 1
    written = per.write_receipt(tmp_path, receipt)
    assert written.name == "cluster_001_write_end.json"
    assert written.exists()


def test_fake_completion_empty_verified_outputs_fatal(tmp_path, monkeypatch):
    """step status=completed 但 verified_outputs 为空（skip_output 假完成指纹）→ FATAL。"""
    plan = _plan(tmp_path, [
        {"n": 1, "name": "manifest", "required": True, "status": "completed",
         "verified_outputs": []},
        {"n": 7, "name": "plan-end", "required": True, "status": "pending"},
    ])
    _install(monkeypatch, tmp_path, plan)
    with pytest.raises(ValueError, match="假完成"):
        per.build_receipt(tmp_path, "PID", "7", "cluster-write")


def test_missing_verified_file_fatal(tmp_path, monkeypatch):
    """verified_outputs 指的实体文件已丢失 → FATAL。"""
    plan = _plan(tmp_path, [
        {"n": 1, "name": "manifest", "required": True, "status": "completed",
         "verified_outputs": [str(tmp_path / "_数据库" / ".wal" / "gone.json")]},
        {"n": 7, "name": "plan-end", "required": True, "status": "pending"},
    ])
    _install(monkeypatch, tmp_path, plan)
    with pytest.raises(ValueError, match="已丢失"):
        per.build_receipt(tmp_path, "PID", "7", "cluster-write")


def test_prior_required_step_not_completed_fatal(tmp_path, monkeypatch):
    """前置 required step 未完成 → 收尾 step 不得跳过 → FATAL。"""
    plan = _plan(tmp_path, [
        {"n": 1, "name": "manifest", "required": True, "status": "pending",
         "verified_outputs": []},
        {"n": 7, "name": "plan-end", "required": True, "status": "pending"},
    ])
    _install(monkeypatch, tmp_path, plan)
    with pytest.raises(ValueError, match="未完成"):
        per.build_receipt(tmp_path, "PID", "7", "cluster-write")


def test_no_prior_required_step_fatal(tmp_path, monkeypatch):
    """收尾步之前无任何 required step → 空转校验 → FATAL。"""
    plan = _plan(tmp_path, [
        {"n": 7, "name": "plan-end", "required": True, "status": "pending"},
    ])
    _install(monkeypatch, tmp_path, plan)
    with pytest.raises(ValueError, match="空转"):
        per.build_receipt(tmp_path, "PID", "7", "cluster-write")


def test_tampered_plan_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_tracker, "verify_plan", lambda pid: "tampered")
    with pytest.raises(ValueError, match="attestation"):
        per.build_receipt(tmp_path, "PID", "7", "cluster-write")


def test_save_state_command_suffix(tmp_path, monkeypatch):
    out = tmp_path / "_数据库" / ".wal" / "cluster_001_post_state_receipt.json"
    out.parent.mkdir(parents=True)
    out.write_text("{}", encoding="utf-8")
    plan = _plan(tmp_path, [
        {"n": 11, "name": "scan", "required": True, "status": "completed",
         "verified_outputs": [str(out)]},
        {"n": 14, "name": "wal-end", "required": True, "status": "pending"},
    ])
    plan["command"] = "cluster-save-state"
    _install(monkeypatch, tmp_path, plan)
    receipt = per.build_receipt(tmp_path, "PID", "14", "cluster-save-state")
    written = per.write_receipt(tmp_path, receipt)
    assert written.name == "cluster_001_save_state_end.json"


def test_command_mismatch_rejected(tmp_path, monkeypatch):
    plan = _plan(tmp_path, [
        {"n": 7, "name": "plan-end", "required": True, "status": "pending"},
    ])  # command=cluster-write
    _install(monkeypatch, tmp_path, plan)
    with pytest.raises(ValueError, match="不是 cluster-save-state"):
        per.build_receipt(tmp_path, "PID", "14", "cluster-save-state")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
