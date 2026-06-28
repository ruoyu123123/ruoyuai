#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件 P0 (But-Therefore因果连接器+Swain场景骨架注入 + causal_connector_scanner) +
环境 P0 (location_atmosphere feed-forward 回喂) 回归锁。🔴 2026-06-29

覆盖：
  ① _collect_scene_causal_skeleton：标注 storyboard → 注入 but/therefore 指令；无字段 → None（默认安全）。
  ② build_manifest event_cluster_context 透传 scene_causal_skeleton（端到端）。
  ③ causal_connector_scanner：and_then 平铺 → WEAK_CAUSAL_LINK(active)；未标注 → skip；but/therefore → PASS。
  ④ _collect_location_atmosphere：registry 命中 → 注入；无 registry / allow_drift → None（默认安全 + 豁免）。
  ⑤ 北极星⑤：WEAK_CAUSAL_LINK 绝不进 audit_hub.HARD_GATE_CODES。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import causal_connector_scanner as ccs  # noqa: E402


def _write(db: Path, name: str, obj):
    db.mkdir(parents=True, exist_ok=True)
    (db / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ════════════════════ ① _collect_scene_causal_skeleton ════════════════════

def test_scene_causal_skeleton_injects_but_therefore_directive():
    """标注了 scene_type/link_to_prev/result_type/Swain beats → 注入 but/therefore 衔接指令。"""
    cluster = {
        "cluster_id": "cluster_001",
        "scene_storyboard": [
            {"title": "灾难开场", "scene_type": "proactive_scene",
             "goal": "逃出育新中学", "conflict": "门锁死", "disaster": "走廊塌方",
             "result_type": "no_and"},
            {"title": "反应", "scene_type": "reactive_sequel", "link_to_prev": "but",
             "reaction": "恐惧", "dilemma": "走还是留", "decision": "留下找钥匙"},
            {"title": "推进", "scene_type": "proactive_scene", "link_to_prev": "therefore",
             "goal": "找钥匙", "result_type": "yes_but"},
        ],
    }
    out = bm._collect_scene_causal_skeleton(cluster)
    assert out is not None, "标注 storyboard 应注入"
    assert "scenes" in out and len(out["scenes"]) == 3
    # link_to_prev / scene_type / Swain beats 都透传
    s1 = out["scenes"][1]
    assert s1.get("link_to_prev") == "but"
    assert s1.get("scene_type") == "reactive_sequel"
    assert s1["beats"].get("decision") == "留下找钥匙"
    # 指令含 but/therefore + and_then 平铺告诫 + try-fail
    d = out["directive"]
    assert "but" in d and "therefore" in d and "and_then" in d
    assert "yes" in d  # result_type 禁纯 yes 指引
    assert out["_doc"].startswith("🔴 2026-06-29")


def test_scene_causal_skeleton_default_safe_no_fields():
    """旧 storyboard（无任何 Swain/But-Therefore 字段）→ None（默认安全·向后兼容·零行为变化）。"""
    cluster = {
        "cluster_id": "cluster_001",
        "scene_storyboard": [
            {"title": "场景A", "characters": ["主角"]},
            {"title": "场景B", "characters": ["主角"]},
        ],
    }
    assert bm._collect_scene_causal_skeleton(cluster) is None
    # 空 / 缺 storyboard 也安全
    assert bm._collect_scene_causal_skeleton({"cluster_id": "c"}) is None
    assert bm._collect_scene_causal_skeleton({}) is None
    assert bm._collect_scene_causal_skeleton(None) is None


def test_scene_causal_skeleton_nested_proactive_reactive_dict():
    """Swain beats 嵌 proactive{}/reactive{} 子 dict 也认。"""
    cluster = {"scene_storyboard": [
        {"title": "s0", "proactive": {"goal": "夺旗", "conflict": "守军强"}},
        {"title": "s1", "link_to_prev": "but", "reactive": {"decision": "改走暗道"}},
    ]}
    out = bm._collect_scene_causal_skeleton(cluster)
    assert out is not None
    assert out["scenes"][0]["beats"]["goal"] == "夺旗"
    assert out["scenes"][1]["beats"]["decision"] == "改走暗道"


# ════════════════════ ② 端到端 event_cluster_context 透传 ════════════════════

def test_event_cluster_context_carries_scene_causal_skeleton():
    """build_manifest._collect_event_cluster_context 把 scene_causal_skeleton 透传进 manifest。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "book"
        db = proj / "_数据库"
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001", "status": "in_progress", "chapter_range": [1, 4],
            "scope_summary": "开局",
            "scene_storyboard": [
                {"title": "开场", "scene_type": "proactive_scene", "goal": "活下去"},
                {"title": "转", "scene_type": "reactive_sequel", "link_to_prev": "and_then"},
            ],
        }]})
        s = bm.DatabaseScanner(proj, 1)
        ctx = bm._collect_event_cluster_context(s, 1)
        assert ctx.get("mode") == "on"
        sk = ctx.get("scene_causal_skeleton")
        assert sk is not None and "directive" in sk


# ════════════════════ ③ causal_connector_scanner ════════════════════

def _scanner_project(tmp: Path, storyboard) -> Path:
    proj = tmp / "scan_book"
    _write(proj / "_数据库", "事件簇", {"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 4],
         "scene_storyboard": storyboard}]})
    return proj


def test_causal_connector_flags_and_then_flat():
    """连续 and_then 平铺 → WEAK_CAUSAL_LINK（active 模式）。"""
    storyboard = [
        {"title": "s0", "scene_type": "proactive_scene", "goal": "g"},
        {"title": "s1", "scene_type": "proactive_scene", "link_to_prev": "and_then"},
        {"title": "s2", "scene_type": "proactive_scene", "link_to_prev": "and_then"},
        {"title": "s3", "scene_type": "proactive_scene", "link_to_prev": "and_then"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        proj = _scanner_project(Path(tmp), storyboard)
        os.environ["CAUSAL_CONNECTOR_MODE"] = "active"
        try:
            rep = ccs.scan(str(proj), "cluster_001")
        finally:
            os.environ.pop("CAUSAL_CONNECTOR_MODE", None)
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["violations_count"] >= 1
        assert rep["violations"][0]["code"] == "WEAK_CAUSAL_LINK"
        assert rep["gate_level"] == "advisory"


def test_causal_connector_passes_but_therefore():
    """全 but/therefore 强衔接 → PASS（无弱衔接）。"""
    storyboard = [
        {"title": "s0", "scene_type": "proactive_scene", "goal": "g"},
        {"title": "s1", "scene_type": "reactive_sequel", "link_to_prev": "but"},
        {"title": "s2", "scene_type": "proactive_scene", "link_to_prev": "therefore"},
        {"title": "s3", "scene_type": "reactive_sequel", "link_to_prev": "but"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        proj = _scanner_project(Path(tmp), storyboard)
        os.environ["CAUSAL_CONNECTOR_MODE"] = "active"
        try:
            rep = ccs.scan(str(proj), "cluster_001")
        finally:
            os.environ.pop("CAUSAL_CONNECTOR_MODE", None)
        assert rep["verdict"] == "PASS"
        assert rep["violations_count"] == 0


def test_causal_connector_skips_unannotated_storyboard():
    """旧 storyboard（无标注）→ skip·不报（默认安全·向后兼容）。"""
    storyboard = [{"title": "s0"}, {"title": "s1"}, {"title": "s2"}]
    with tempfile.TemporaryDirectory() as tmp:
        proj = _scanner_project(Path(tmp), storyboard)
        os.environ["CAUSAL_CONNECTOR_MODE"] = "active"
        try:
            rep = ccs.scan(str(proj), "cluster_001")
        finally:
            os.environ.pop("CAUSAL_CONNECTOR_MODE", None)
        assert rep["verdict"] == "PASS"
        assert rep["violations_count"] == 0
        assert rep.get("per_cluster") == []


def test_causal_connector_off_mode_noop():
    """off 模式直接返回 PASS·不读盘。"""
    os.environ["CAUSAL_CONNECTOR_MODE"] = "off"
    try:
        rep = ccs.scan("/nonexistent/path", "cluster_001")
    finally:
        os.environ.pop("CAUSAL_CONNECTOR_MODE", None)
    assert rep["verdict"] == "PASS" and rep["mode"] == "off"


# ════════════════════ ④ _collect_location_atmosphere ════════════════════

def test_location_atmosphere_injects_signature_via_hub_locations():
    """registry 命中当前 cluster.hub_locations 的地点 → 注入签名感官。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "env_book"
        db = proj / "_数据库"
        _write(db, "location_atmosphere_registry", {
            "育新中学": {"signature_sensory_motifs": ["smell:消毒水", "sound:回声", "light:惨白"],
                       "occurrences": 3}})
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001", "chapter_range": [1, 4],
            "hub_locations": ["育新中学"], "scene_storyboard": []}]})
        s = bm.DatabaseScanner(proj, 1)
        out = bm._collect_location_atmosphere(s, 1)
        assert out is not None, "命中地点应注入"
        assert out["locations"][0]["location"] == "育新中学"
        assert "smell:消毒水" in out["locations"][0]["signature_sensory_motifs"]
        assert "素材池" in out["directive"]
        assert out["_doc"].startswith("🔴 2026-06-29")


def test_location_atmosphere_via_character_positions():
    """registry 命中 character_positions 的地点 → 注入。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "env_book2"
        db = proj / "_数据库"
        _write(db, "location_atmosphere_registry", {
            "古井院": {"signature_sensory_motifs": ["touch:阴凉", "smell:霉"], "occurrences": 2}})
        _write(db, "人物卡", {"characters": [{"id": "c1", "name": "林川", "role": "主角"}]})
        _write(db, "地图", {"character_positions": {"林川": "古井院"}})
        s = bm.DatabaseScanner(proj, 1)
        out = bm._collect_location_atmosphere(s, 1)
        assert out is not None
        assert out["locations"][0]["location"] == "古井院"


def test_location_atmosphere_default_safe_no_registry():
    """无 location_atmosphere_registry.json → None（默认安全·向后兼容旧书·零行为变化）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "no_reg_book"
        (proj / "_数据库").mkdir(parents=True)
        s = bm.DatabaseScanner(proj, 1)
        assert bm._collect_location_atmosphere(s, 1) is None


def test_location_atmosphere_respects_allow_drift():
    """作者档 allow_drift_locations 豁免该地点（季节/灾后合法漂移）→ 不注入。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "drift_book"
        db = proj / "_数据库"
        _write(db, "location_atmosphere_registry", {
            "海港": {"signature_sensory_motifs": ["sound:海浪", "smell:腥"], "occurrences": 4}})
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001", "chapter_range": [1, 4],
            "hub_locations": ["海港"], "scene_storyboard": []}]})
        _write(db, "作者风格", {
            "location_atmosphere_override": {"allow_drift_locations": ["海港"]}})
        s = bm.DatabaseScanner(proj, 1)
        assert bm._collect_location_atmosphere(s, 1) is None


def test_location_atmosphere_no_match_returns_none():
    """registry 有地点但与当前场景候选不匹配 → None（不误注入）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "nomatch_book"
        db = proj / "_数据库"
        _write(db, "location_atmosphere_registry", {
            "天枢阁": {"signature_sensory_motifs": ["light:金光"], "occurrences": 3}})
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001", "chapter_range": [1, 4],
            "hub_locations": ["乱葬岗"], "scene_storyboard": []}]})
        s = bm.DatabaseScanner(proj, 1)
        assert bm._collect_location_atmosphere(s, 1) is None


# ════════════════════ ⑤ 北极星⑤ hard_gate 不变量 ════════════════════

def test_weak_causal_link_not_in_hard_gate_codes():
    """WEAK_CAUSAL_LINK 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤·只查 and-then 平铺不卡比例）。"""
    import audit_hub
    assert "WEAK_CAUSAL_LINK" not in audit_hub.HARD_GATE_CODES
    assert ccs.ISSUE_CODE == "WEAK_CAUSAL_LINK"
    assert audit_hub._gate_level_for("WEAK_CAUSAL_LINK", "error") == "advisory"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
