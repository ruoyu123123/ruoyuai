# -*- coding: utf-8 -*-
"""antagonist_rotation_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import antagonist_rotation_scanner as ar  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("ANTAGONIST_ROTATION_MODE", None)
    else:
        os.environ["ANTAGONIST_ROTATION_MODE"] = m


def _mk_project(entries=None, cluster_index=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if entries is not None:
        (proj / "_数据库" / "反派轮替.json").write_text(
            json.dumps({"entries": entries}, ensure_ascii=False),
            encoding="utf-8")
    if cluster_index is not None:
        (proj / "_数据库" / "cluster_index.json").write_text(
            json.dumps({"clusters": cluster_index}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("off")
        rep = ar.scan(project_root=None)
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_ledger_skips():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        rep = ar.scan(project_root=proj)
        assert "反派轮替.json" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_single_entry_no_judgment():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(entries=[
            {"cluster_id": "cluster_001", "antagonist_id": "a1",
             "tier": 1, "motive_type": "权力",
             "power_system_tag": "剑术"}])
        rep = ar.scan(project_root=proj)
        assert "无法判断" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_void_detection():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        entries = [
            {"cluster_id": "cluster_001", "antagonist_id": "a1", "tier": 1,
             "defeat_cluster": "cluster_002",
             "motive_type": "权力", "power_system_tag": "剑"},
            {"cluster_id": "cluster_010", "antagonist_id": "a2", "tier": 2,
             "motive_type": "复仇", "power_system_tag": "刀"}]
        ci = [{"cluster_id": f"cluster_{i:03d}"} for i in range(1, 12)]
        proj = _mk_project(entries=entries, cluster_index=ci)
        rep = ar.scan(project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "ANTAGONIST_ROTATION_VOID" in codes
    finally:
        _set_mode(bak)


def test_tier_downgrade_detected():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        entries = [
            {"cluster_id": "cluster_001", "antagonist_id": "a1", "tier": 5,
             "motive_type": "权力", "power_system_tag": "剑"},
            {"cluster_id": "cluster_005", "antagonist_id": "a2", "tier": 3,
             "motive_type": "复仇", "power_system_tag": "刀"}]
        proj = _mk_project(entries=entries)
        rep = ar.scan(project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "ANTAGONIST_ROTATION_TIER_DOWNGRADE" in codes
    finally:
        _set_mode(bak)


def test_motive_monotone():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        entries = [
            {"cluster_id": "c1", "antagonist_id": "a1", "tier": 1,
             "motive_type": "权力", "power_system_tag": "剑"},
            {"cluster_id": "c2", "antagonist_id": "a2", "tier": 2,
             "motive_type": "权力", "power_system_tag": "刀"}]
        proj = _mk_project(entries=entries)
        rep = ar.scan(project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "ANTAGONIST_ROTATION_MOTIVE_MONOTONE" in codes
    finally:
        _set_mode(bak)


def test_power_system_monotone():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        entries = [
            {"cluster_id": "c1", "antagonist_id": "a1", "tier": 1,
             "motive_type": "权力", "power_system_tag": "剑"},
            {"cluster_id": "c2", "antagonist_id": "a2", "tier": 2,
             "motive_type": "复仇", "power_system_tag": "剑"}]
        proj = _mk_project(entries=entries)
        rep = ar.scan(project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "ANTAGONIST_ROTATION_POWER_MONOTONE" in codes
    finally:
        _set_mode(bak)


def test_healthy_rotation_passes():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("active")
        entries = [
            {"cluster_id": "c1", "antagonist_id": "a1", "tier": 1,
             "motive_type": "权力", "power_system_tag": "剑"},
            {"cluster_id": "c2", "antagonist_id": "a2", "tier": 3,
             "motive_type": "复仇", "power_system_tag": "刀"}]
        proj = _mk_project(entries=entries)
        rep = ar.scan(project_root=proj)
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("ANTAGONIST_ROTATION_MODE")
    try:
        _set_mode("shadow")
        entries = [
            {"cluster_id": "c1", "antagonist_id": "a1", "tier": 5,
             "motive_type": "权力", "power_system_tag": "剑"},
            {"cluster_id": "c2", "antagonist_id": "a2", "tier": 3,
             "motive_type": "权力", "power_system_tag": "剑"}]
        proj = _mk_project(entries=entries)
        rep = ar.scan(project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("ANTAGONIST_ROTATION_VOID", "ANTAGONIST_ROTATION_TIER_DOWNGRADE",
              "ANTAGONIST_ROTATION_MOTIVE_MONOTONE",
              "ANTAGONIST_ROTATION_POWER_MONOTONE"):
        assert c not in hgs
