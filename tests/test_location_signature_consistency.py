# -*- coding: utf-8 -*-
"""location_signature_consistency 专属回归(2026-06-20·R8 W4 Batch-J·L37)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import location_signature_consistency as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("LOCATION_SIGNATURE_MODE", None)
    else:
        os.environ["LOCATION_SIGNATURE_MODE"] = m


def _mk_project(locations=None, registry=None, allow_drift=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if locations is not None:
        (proj / "_数据库" / "地图.json").write_text(
            json.dumps([{"name": loc} for loc in locations],
                       ensure_ascii=False), encoding="utf-8")
    if registry is not None:
        (proj / "_数据库" / "location_atmosphere_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    if allow_drift is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"location_atmosphere_override":
                        {"allow_drift_locations": allow_drift}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("off")
        out = mod.aggregate(None)
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_project_skips():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("active")
        out = mod.aggregate(None)
        assert "无项目根" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_registry_skips():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir()
        out = mod.aggregate(proj)
        assert "无 location_atmosphere_registry" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_high_hit_rate_pass():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("active")
        reg = {"酆都殿": {
            "first_cluster": "c1",
            "signature_sensory_motifs": ["smell:腥", "sound:钟声", "touch:寒"],
            "occurrences": 4,
            "hit_history": [
                {"cluster_id": "c1", "hits": []},
                {"cluster_id": "c2", "hits": ["smell:腥", "sound:钟声", "touch:寒"]},
                {"cluster_id": "c3", "hits": ["smell:腥", "sound:钟声", "touch:寒"]},
                {"cluster_id": "c4", "hits": ["smell:腥", "sound:钟声"]},
            ]
        }}
        proj = _mk_project(registry=reg)
        out = mod.aggregate(proj)
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_low_hit_rate_fail():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("active")
        reg = {"酆都殿": {
            "first_cluster": "c1",
            "signature_sensory_motifs": ["smell:腥", "sound:钟声", "touch:寒"],
            "occurrences": 4,
            "hit_history": [
                {"cluster_id": "c1", "hits": []},
                {"cluster_id": "c2", "hits": []},
                {"cluster_id": "c3", "hits": []},
                {"cluster_id": "c4", "hits": []},
            ]
        }}
        proj = _mk_project(registry=reg)
        out = mod.aggregate(proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_allow_drift_skips_location():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("active")
        reg = {"酆都殿": {
            "first_cluster": "c1",
            "signature_sensory_motifs": ["smell:腥"],
            "occurrences": 4,
            "hit_history": [{"cluster_id": f"c{i}", "hits": []}
                            for i in range(1, 5)]
        }}
        proj = _mk_project(registry=reg, allow_drift=["酆都殿"])
        out = mod.aggregate(proj)
        assert out["verdict"] == "PASS"  # 豁免
    finally:
        _set_mode(bak)


def test_shadow_records_no_violation():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("shadow")
        reg = {"x": {"signature_sensory_motifs": ["smell:腥"],
                     "occurrences": 4,
                     "hit_history": [{"cluster_id": f"c{i}", "hits": []}
                                     for i in range(4)]}}
        proj = _mk_project(registry=reg)
        out = mod.aggregate(proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_update_registry_first_pass():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir()
    (proj / "_数据库" / "地图.json").write_text(
        json.dumps([{"name": "酆都殿"}], ensure_ascii=False),
        encoding="utf-8")
    draft = "酆都殿香烟缭绕，钟声远远传来，殿内阴森寒意刺骨。" * 3
    reg = mod.update_registry(proj, "c1", draft)
    assert "酆都殿" in reg
    assert reg["酆都殿"]["occurrences"] == 1
    assert reg["酆都殿"]["signature_sensory_motifs"]


def test_extract_sensory_motifs():
    motifs = mod._extract_sensory_motifs("腥味，钟声，寒意，烛光")
    assert "腥" in motifs["smell"]
    assert "钟声" in motifs["sound"]
    assert "寒" in motifs["touch"]
    assert "烛光" in motifs["light"]


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("LOCATION_SIGNATURE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
