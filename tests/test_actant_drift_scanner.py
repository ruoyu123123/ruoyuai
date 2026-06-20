# -*- coding: utf-8 -*-
"""actant_drift_scanner 专属测试 — Greimas 六 actant 漂移(advisory · 2026-06-20)

钉死：
  · helper↔opponent 无 pivot → ACTANT_DRIFT_NO_PIVOT
  · subject/opponent 空 → ACTANT_VACANCY
  · 单角色 ≥3 actant 位 → ACTANT_OVERLOADED
  · pivot 声明豁免
  · multi_role_hero 豁免清单
  · mode=off/shadow/active
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import actant_drift_scanner as ad  # noqa: E402


def _write_draft():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write("占位草稿·本 scanner 实际读 manifest")
    f.close()
    return Path(f.name)


def _mk_project(*, ledger=None, author_signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        (proj / "_数据库" / "cluster_actant_ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    if author_signature is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"actant_signature": author_signature}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _mk_manifest(**kwargs):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False)
    f.write(json.dumps(kwargs, ensure_ascii=False))
    f.close()
    return Path(f.name)


def _set_mode(m):
    if m is None:
        os.environ.pop("ACTANT_DRIFT_MODE", None)
    else:
        os.environ["ACTANT_DRIFT_MODE"] = m


# ── off → 骨架 ──────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("off")
        rep = ad.scan(str(_write_draft()))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
        assert rep["warning"] is None
    finally:
        _set_mode(bak)


# ── 全空 assignments → skip ─────────────────────────────────────────────────
def test_empty_assignments_skips():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001")
        rep = ad.scan(str(_write_draft()), manifest_path=str(mf))
        assert "无 cluster_actant_state" in rep.get("note", "")
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── ① helper→opponent 无 pivot → DRIFT advisory ────────────────────────────
def test_helper_to_opponent_no_pivot_drift():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ledger={"clusters": [{
            "cluster_id": "cluster_001",
            "assignments": {"helper": "张三"}
        }]})
        mf = _mk_manifest(cluster_id="cluster_002",
                          cluster_actant_state={"subject": "李四",
                                                "opponent": "张三"})
        rep = ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "ACTANT_DRIFT_NO_PIVOT" in codes
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── pivot 声明 → 漂移豁免 ──────────────────────────────────────────────────
def test_drift_with_pivot_event_suppressed():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ledger={"clusters": [{
            "cluster_id": "cluster_001",
            "assignments": {"helper": "张三"}
        }]})
        mf = _mk_manifest(cluster_id="cluster_002",
                          cluster_actant_state={"subject": "李四",
                                                "opponent": "张三"},
                          pivot_events=["张三"])
        rep = ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "ACTANT_DRIFT_NO_PIVOT" not in codes
    finally:
        _set_mode(bak)


# ── ② subject/opponent 空 → VACANCY ────────────────────────────────────────
def test_subject_opponent_vacancy():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"helper": "张三"})
        rep = ad.scan(str(_write_draft()), manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "ACTANT_VACANCY" in codes
    finally:
        _set_mode(bak)


# ── ③ 单角色 ≥3 位 → OVERLOAD ──────────────────────────────────────────────
def test_role_overload():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"subject": "主角",
                                                "sender": "主角",
                                                "helper": "主角",
                                                "object": "宝物",
                                                "opponent": "反派"})
        rep = ad.scan(str(_write_draft()), manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "ACTANT_OVERLOADED" in codes
    finally:
        _set_mode(bak)


def test_role_overload_multi_role_hero_exempt():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(author_signature={"allow_multi_role_hero": ["主角"]})
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"subject": "主角",
                                                "sender": "主角",
                                                "helper": "主角",
                                                "object": "宝物",
                                                "opponent": "反派"})
        rep = ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "ACTANT_OVERLOADED" not in codes
    finally:
        _set_mode(bak)


# ── shadow 模式 → 命中只记不判 ────────────────────────────────────────────
def test_shadow_records_no_violation():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(ledger={"clusters": [{
            "cluster_id": "cluster_001",
            "assignments": {"helper": "张三"}
        }]})
        mf = _mk_manifest(cluster_id="cluster_002",
                          cluster_actant_state={"subject": "李四",
                                                "opponent": "张三"})
        rep = ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
        assert rep["verdict"] == "PASS"
        assert rep["warning"] is None
    finally:
        _set_mode(bak)


# ── ledger 写回（仅 active） ───────────────────────────────────────────────
def test_active_writes_ledger_back():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"subject": "主角",
                                                "opponent": "反派"})
        ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        ledger_path = proj / "_数据库" / "cluster_actant_ledger.json"
        assert ledger_path.exists()
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert any(r.get("cluster_id") == "cluster_001"
                   for r in data["clusters"])
    finally:
        _set_mode(bak)


def test_shadow_does_not_write_ledger():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project()
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"subject": "主角",
                                                "opponent": "反派"})
        ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        ledger_path = proj / "_数据库" / "cluster_actant_ledger.json"
        assert not ledger_path.exists()
    finally:
        _set_mode(bak)


# ── ISSUE_CODE 永远 advisory · 绝不进 HARD_GATE_CODES ───────────────────────
def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("ACTANT_DRIFT_NO_PIVOT", "ACTANT_VACANCY", "ACTANT_OVERLOADED"):
        assert c not in hgs, c


# ── _mode 非法回落 ─────────────────────────────────────────────────────────
def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        os.environ["ACTANT_DRIFT_MODE"] = "bogus"
        assert ad._mode() == "shadow"
        os.environ["ACTANT_DRIFT_MODE"] = "ACTIVE"
        assert ad._mode() == "active"
    finally:
        _set_mode(bak)


# ── 兼容 list assignments 不崩 ─────────────────────────────────────────────
def test_assignments_string_normalization():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"subject": "  主角  ",
                                                "opponent": ""})
        rep = ad.scan(str(_write_draft()), manifest_path=str(mf))
        assert rep["assignments"]["subject"] == "主角"
        assert rep["assignments"]["opponent"] is None
    finally:
        _set_mode(bak)


# ── 损坏 ledger 不崩 ───────────────────────────────────────────────────────
def test_corrupted_ledger_safe():
    bak = os.environ.get("ACTANT_DRIFT_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        (proj / "_数据库" / "cluster_actant_ledger.json").write_text(
            "{ bad json", encoding="utf-8")
        mf = _mk_manifest(cluster_id="cluster_001",
                          cluster_actant_state={"subject": "主角",
                                                "opponent": "反派"})
        rep = ad.scan(str(_write_draft()), project_root=proj, manifest_path=str(mf))
        assert rep["verdict"] in ("PASS", "FAIL_MINOR")
    finally:
        _set_mode(bak)
