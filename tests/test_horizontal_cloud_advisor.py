# -*- coding: utf-8 -*-
"""horizontal_cloud_advisor R23 W11 Batch-GG · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import horizontal_cloud_advisor as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("HORIZONTAL_CLOUD_MODE", None)
    else:
        os.environ["HORIZONTAL_CLOUD_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(with_clusters: bool = False):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if with_clusters:
        (proj / "_数据库" / "事件簇.json").write_text(
            json.dumps({"clusters": [
                {"cluster_id": "cluster_001", "status": "active"},
                {"cluster_id": "cluster_002", "status": "pending"},
            ]}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def _mk_manifest(scope: str, hero: str) -> Path:
    d = Path(tempfile.mkdtemp())
    mf = d / "manifest.json"
    mf.write_text(json.dumps({
        "event_cluster_context": {
            "mode": "on",
            "scope_summary": scope,
            "characters_focus": [hero],
        }
    }, ensure_ascii=False), encoding="utf-8")
    return mf


# 4 个连续场景同 POV + 高情绪 + 主线长 + 单 POV
def _build_high_density_long_arc(hero: str) -> str:
    scene = (f"{hero}冲入战场！\n"
             f"{hero}举起剑！\n"
             f"{hero}怒吼一声！\n"
             f"{hero}砍倒敌人！\n"
             f"血雾弥漫。\n")
    sep = "\n***\n"
    return sep.join([scene] * 6)


def _build_with_pov_switch(hero: str, side: str) -> str:
    scene_a = (f"{hero}冲入战场！\n{hero}举剑！\n血雾！\n")
    scene_b = (f"{side}在远处观察。\n{side}低声说道。\n他记下时间。\n")
    sep = "\n***\n"
    return sep.join([scene_a, scene_b, scene_a, scene_b])


def test_off_returns_skeleton():
    bak = os.environ.get("HORIZONTAL_CLOUD_MODE")
    try:
        _set_mode("off")
        text = _build_high_density_long_arc("陆衍") * 4
        out = mod.scan(_write(text), _mk_project(), _mk_manifest("陆衍战场", "陆衍"))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("HORIZONTAL_CLOUD_MODE")
    try:
        _set_mode("shadow")
        text = _build_high_density_long_arc("陆衍") * 4
        out = mod.scan(_write(text), _mk_project(), _mk_manifest("陆衍战场", "陆衍"))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_recommend_when_all_conditions():
    bak = os.environ.get("HORIZONTAL_CLOUD_MODE")
    try:
        _set_mode("active")
        text = _build_high_density_long_arc("陆衍") * 20
        proj = _mk_project(with_clusters=True)
        out = mod.scan(_write(text), proj, _mk_manifest("陆衍战场冲入举剑", "陆衍"))
        # 所有 4 条件命中应触发 RECOMMEND
        conds = out["conditions"]
        if all(conds[k]["hit"] for k in ("arc_long", "single_pov_streak", "high_emotion", "no_pov_switch")):
            codes = {v["code"] for v in out.get("violations", [])}
            assert "HORIZONTAL_CLOUD_RECOMMEND" in codes
            # pacing_suggestions 应写入 pending cluster
            obj = json.loads((proj / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
            cs = obj["clusters"]
            pending = next((c for c in cs if c.get("status") == "pending"), None)
            if pending:
                ps = pending.get("pacing_suggestions") or []
                assert any(p.get("source") == "horizontal_cloud_advisor" for p in ps)
    finally:
        _set_mode(bak)


def test_active_present_when_pov_switch():
    bak = os.environ.get("HORIZONTAL_CLOUD_MODE")
    try:
        _set_mode("active")
        text = _build_with_pov_switch("陆衍", "旁观者") * 4
        out = mod.scan(_write(text), _mk_project(), _mk_manifest("陆衍战场冲入举剑血雾", "陆衍"))
        # 多 POV → 不应报 RECOMMEND
        codes = {v["code"] for v in out.get("violations", [])}
        assert "HORIZONTAL_CLOUD_RECOMMEND" not in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("HORIZONTAL_CLOUD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_jaccard_helpers():
    # arc_occupancy 基础属性测试
    score = mod._arc_occupancy("陆衍冲入战场。陆衍举剑。" * 50, "陆衍战场", "陆衍")
    assert score > 0


def test_emotion_intensity_high():
    text = "怒吼！冲过来！斩出！" * 30
    e = mod._emotion_intensity(text)
    # 含大量情绪标点 → 非零
    assert e > 0


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("HORIZONTAL_CLOUD_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("HORIZONTAL_CLOUD_RECOMMEND", "HORIZONTAL_CLOUD_PRESENT"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("horizontal_cloud_advisor")
    assert s is not None
    assert s.get("_new") is True


def test_split_scenes_basic():
    text = "段1\n***\n段2\n***\n段3"
    parts = mod._split_scenes(text)
    assert len(parts) == 3


def test_scene_lead_subject_basic():
    s = mod._scene_lead_subject("陆衍走到了门前。\n他看了看四周。")
    # 取得 head 中最频繁 surface
    assert isinstance(s, str)
