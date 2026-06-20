# -*- coding: utf-8 -*-
"""first_encounter_anchor_scanner.py 测试 (R8 W4 Batch-H · L23 · 2026-06-20)。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import first_encounter_anchor_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "first_encounter_anchor_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("FIRST_ENCOUNTER_ANCHOR_MODE", None)
    else:
        os.environ["FIRST_ENCOUNTER_ANCHOR_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, genre=None, entries=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if genre:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"genre_tags": [genre]}, ensure_ascii=False), encoding="utf-8")
    if entries is not None:
        (proj / "_数据库" / "世界观.json").write_text(
            json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    return proj


def _write_manifest(proj, targets):
    md = proj / "_数据库" / ".manifest"
    md.mkdir(parents=True, exist_ok=True)
    mp = md / "ch_001.json"
    mp.write_text(json.dumps({"first_encounter_targets": targets},
                             ensure_ascii=False), encoding="utf-8")
    return mp


# 草稿：3 个 target·全 label-first (无感官铺垫)
_LABEL_FIRST_DRAFT = (
    "他推开门走进去。这就是魂线。他从未见过这种东西。" * 30
    + "村口有一块石碑。它叫做引魂石。村里人都不靠近。" * 30
    + "山顶上飘着一团雾。这是雾神。它已存在千年。" * 30
)
# 草稿：3 个 target·全有感官锚定
_SENSORY_FIRST_DRAFT = (
    "他推开门走进去，看见空中飘着一缕半透明的丝线。一动不动·古怪极了。"
    "后来他才知道，那就是魂线。" * 20
    + "村口立着一块石头。表面斑驳·像被无数手指摸过。他从未见过这种石碑。"
    "村里人说，那是引魂石。" * 20
    + "山顶上有团雾。陌生而浓重，飘来一股从未闻过的甜腥味。"
    "村人称之为雾神。" * 20
)


# ── 模板匹配单元 ────────────────────────────────────────────────────────────
def test_is_label_first_positive():
    # "这就是魂线" → label-first
    txt = "他推门进去。这就是魂线。" * 2
    r = mod._is_label_first(txt, "魂线")
    assert r["found"] is True
    assert r["label_first"] is True
    assert r["has_sensory_anchor"] is False


def test_is_label_first_with_sensory_anchor():
    # 前置有 "看见" → 体感锚定
    txt = "他看见空中飘着一缕丝线，古怪极了。后来才知道，那叫魂线。"
    r = mod._is_label_first(txt, "魂线")
    assert r["found"] is True
    assert r["has_sensory_anchor"] is True


def test_is_label_first_target_not_found():
    r = mod._is_label_first("一段无关文本。", "魂线")
    assert r.get("found") is False


# ── 草稿扫描场景 ────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("off")
        proj = _mk_project()
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert "targets_count" not in out
    finally:
        _set_mode(bak)


def test_label_first_draft_triggers_active():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        _write_manifest(proj, ["魂线", "引魂石", "雾神"])
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj,
                       manifest_path=proj / "_数据库" / ".manifest" / "ch_001.json")
        assert out["targets_source"] == "manifest"
        assert out["targets_found"] >= 2
        assert out["label_first_count"] >= 2
        assert out["verdict"] == "FAIL_MINOR"
        assert "label-first 偏高" in (out["warning"] or "")
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_sensory_first_draft_passes():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        _write_manifest(proj, ["魂线", "引魂石", "雾神"])
        out = mod.scan(_write(_SENSORY_FIRST_DRAFT), project_root=proj,
                       manifest_path=proj / "_数据库" / ".manifest" / "ch_001.json")
        # 有感官锚定·label_first_count 应为 0 或小数
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
    finally:
        _set_mode(bak)


# ── 题材豁免 ────────────────────────────────────────────────────────────────
def test_horror_game_genre_exempted():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="horror_game")
        _write_manifest(proj, ["魂线", "引魂石"])
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj,
                       manifest_path=proj / "_数据库" / ".manifest" / "ch_001.json")
        assert "题材豁免" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_rule_anomaly_genre_exempted():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="rule_anomaly")
        _write_manifest(proj, ["魂线"])
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj,
                       manifest_path=proj / "_数据库" / ".manifest" / "ch_001.json")
        assert out["verdict"] == "PASS"
        assert "题材豁免" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── manifest 缺·走 worldview fallback ───────────────────────────────────────
def test_worldview_fallback_when_no_manifest():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(entries=[
            {"id": "e1", "title": "魂线", "keywords": ["魂线"]},
            {"id": "e2", "title": "引魂石", "keywords": ["引魂石"]},
            {"id": "e3", "title": "雾神", "keywords": ["雾神"]},
        ])
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj,
                       manifest_path=None)
        assert out["targets_source"] == "worldview_fallback"
        assert out["targets_count"] >= 3
    finally:
        _set_mode(bak)


# ── 无 target → skip ────────────────────────────────────────────────────────
def test_no_target_skips():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()  # 无 worldview / manifest
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj)
        assert "无 first_encounter_targets" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── shadow 不上报 ───────────────────────────────────────────────────────────
def test_shadow_records_no_violation():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project()
        _write_manifest(proj, ["魂线", "引魂石", "雾神"])
        out = mod.scan(_write(_LABEL_FIRST_DRAFT), project_root=proj,
                       manifest_path=proj / "_数据库" / ".manifest" / "ch_001.json")
        assert out["mode"] == "shadow"
        assert out["violations"] == []
        assert out["verdict"] == "PASS"
        # shadow 模式仍计算指标
        assert out.get("label_first_count", 0) >= 2
    finally:
        _set_mode(bak)


# ── 短稿 / 读失败 ────────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write("他走进屋子。" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("FIRST_ENCOUNTER_ANCHOR_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI subprocess ──────────────────────────────────────────────────────────
def _run_cli(draft_path, project, manifest=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)]
    if manifest:
        cmd += ["--manifest", str(manifest)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "FIRST_ENCOUNTER_ANCHOR_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project()
    mp = _write_manifest(proj, ["魂线", "引魂石", "雾神"])
    p = _write(_LABEL_FIRST_DRAFT)
    r = _run_cli(p, proj, mp)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_clean():
    proj = _mk_project()
    mp = _write_manifest(proj, ["魂线", "引魂石", "雾神"])
    p = _write(_SENSORY_FIRST_DRAFT)
    r = _run_cli(p, proj, mp)
    assert r.returncode == 0, r.stderr
