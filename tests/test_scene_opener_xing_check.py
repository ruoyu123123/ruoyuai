# -*- coding: utf-8 -*-
"""scene_opener_xing_check.py 专属回归测试 (R8 W4 Batch-I · 2026-06-20)。

零依赖·确定性·零 LLM/零联网。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import scene_opener_xing_check as mod  # noqa: E402

_TARGET = _SCRIPTS / "scene_opener_xing_check.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("SCENE_OPENER_XING_MODE", None)
    else:
        os.environ["SCENE_OPENER_XING_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(genre=None, xing_baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if genre:
        obj["genre_tags"] = [genre]
    if xing_baseline is not None:
        obj["scene_opener_profile"] = {"xing_ratio": xing_baseline}
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 长 xing_ok 场景(每个含意象开头·>=30 CJK)
_XING_HEAD = "月光洒在青石板上远山如黛风过松林吹动他的衣袂他缓步走入山门青石阶上落满松针。"
_TAGGED_HEAD = "他很愤怒怒火中烧地拔出剑来心中一凛对方竟然如此狠毒他直接冲了上去抡剑就砍。"
_BARE_HEAD = "他冲过去一拳打中对方然后又一拳再一拳直到对方倒下他停下来喘了口气环顾四周。"

# 起兴丰富的草稿 → 高 xing_ratio (每场景 8+ 复述以充字数)
_XING_RICH = (_XING_HEAD * 8 + "\n\n" + _XING_HEAD * 8 + "\n\n" +
              _XING_HEAD * 8 + "\n\n" + _XING_HEAD * 8)
# tagged 主导(基本没起兴) → 低 xing_ratio
_TAGGED_DOMINANT = (_TAGGED_HEAD * 8 + "\n\n" + _TAGGED_HEAD * 8 + "\n\n" +
                    _BARE_HEAD * 8 + "\n\n" + _BARE_HEAD * 8)


# ── off → 骨架 ──────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(genre="xianxia", xing_baseline=0.6)
        out = mod.scan(_write(_TAGGED_DOMINANT), project_root=proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert "xing_ok" not in out
    finally:
        _set_mode(bak)


# ── 起兴丰富 + xianxia → PASS ───────────────────────────────────────────────
def test_xing_rich_pass():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia", xing_baseline=0.6)
        out = mod.scan(_write(_XING_RICH), project_root=proj)
        assert out["scene_count"] >= 2
        assert out["xing_ratio_actual"] >= 0.4
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── tagged 主导 + xianxia + active → FAIL_MINOR ────────────────────────────
def test_tagged_dominant_fail_minor():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia", xing_baseline=0.6)
        out = mod.scan(_write(_TAGGED_DOMINANT), project_root=proj)
        assert out["scene_count"] >= 2
        assert out["xing_ratio_actual"] < 0.4
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


# ── 现代都市/职场题材 baseline 缺 → skip ────────────────────────────────────
def test_low_xing_genre_skips_without_baseline():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="urban_supernatural")
        out = mod.scan(_write(_TAGGED_DOMINANT), project_root=proj)
        assert "现代/职场类题材" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_low_xing_genre_with_baseline_does_check():
    """都市题材但作者档显式给了 xing_baseline 仍要检查。"""
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="urban_supernatural", xing_baseline=0.5)
        out = mod.scan(_write(_TAGGED_DOMINANT), project_root=proj)
        # 已进入实际检测
        assert "scene_count" in out
    finally:
        _set_mode(bak)


# ── shadow 模式 ──────────────────────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="xianxia", xing_baseline=0.6)
        out = mod.scan(_write(_TAGGED_DOMINANT), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["xing_ratio_actual"] < 0.4
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_single_scene_skipped():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia", xing_baseline=0.6)
        # 无空行分隔的长草稿 → 1 个场景
        body = (_XING_HEAD * 25)
        out = mod.scan(_write(body), project_root=proj)
        assert out["scene_count"] == 1
        assert "场景数 <2" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── 短稿 / 读取失败 ─────────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="xianxia", xing_baseline=0.6)
        out = mod.scan(_write("月光。" * 5), project_root=proj)
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


# ── 辅助 ────────────────────────────────────────────────────────────────────
def test_classify_opener_xing_ok():
    assert mod.classify_opener("月光洒在青石板上他走入庭院") == "xing_ok"


def test_classify_opener_tagged():
    assert mod.classify_opener("他很愤怒地拔出剑") == "tagged_opener"


def test_classify_opener_bare():
    assert mod.classify_opener("他直接冲过去打了一拳又一拳") == "bare_opener"


def test_split_scenes_by_empty_lines():
    text = _XING_HEAD * 2 + "\n\n" + _BARE_HEAD * 2 + "\n\n" + _TAGGED_HEAD * 2
    scenes = mod.split_scenes(text)
    assert len(scenes) >= 3


def test_split_scenes_filter_tiny():
    """太短(<30 CJK)的块被过滤。"""
    text = "短\n\n" + _XING_HEAD * 2
    scenes = mod.split_scenes(text)
    assert len(scenes) == 1


def test_strip_changes_separator():
    raw = "正文。\n---CHANGES---\nlog"
    assert mod._strip_changes(raw) == "正文。"


def test_cjk_count():
    assert mod._cjk_count("月光abc青山") == 4


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("SCENE_OPENER_XING_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_resolve_profile_no_project():
    assert mod._resolve_profile(None) == (None, None)


def test_resolve_profile_from_author():
    proj = _mk_project(genre="xianxia", xing_baseline=0.65)
    b, g = mod._resolve_profile(proj)
    assert b == 0.65 and g == "xianxia"


# ── CLI ─────────────────────────────────────────────────────────────────────
def _run_cli(draft_path, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SCENE_OPENER_XING_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(genre="xianxia", xing_baseline=0.6)
    p = _write(_TAGGED_DOMINANT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_xing_rich():
    proj = _mk_project(genre="xianxia", xing_baseline=0.5)
    p = _write(_XING_RICH)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
