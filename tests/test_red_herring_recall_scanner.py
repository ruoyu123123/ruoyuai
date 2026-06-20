# -*- coding: utf-8 -*-
"""red_herring_recall_scanner.py 专属回归测试（2026-06-20·确定性·零依赖·零 LLM/零联网）。

覆盖：
  · off / shadow / active 三种 mode
  · reveal beat 门控（beat_signal / scene_storyboard.beat_tags / is_reveal_beat flag）
  · 无 red_herrings 声明 → skip
  · _is_unresolved 三态（无 debunked / 已 debunked 在更早 cluster / 已 debunked 在当前）
  · _surface_check（未提及 / 提及但 0 否决 / 提及且 ±60 内有 NEGATION_MARKER）
  · 草稿太短 / 读取失败
  · 多 herring：部分 debunked + 部分 dangling 混合
  · 兜底：伏笔.json / 线索.json 回退
  · CLI subprocess 退出码
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
import red_herring_recall_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "red_herring_recall_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("RED_HERRING_RECALL_MODE", None)
    else:
        os.environ["RED_HERRING_RECALL_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(red_herrings=None, *, file="伏笔表.json"):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if red_herrings is not None:
        (db / file).write_text(json.dumps(
            {"red_herrings": red_herrings}, ensure_ascii=False), encoding="utf-8")
    return proj


def _write_manifest(payload):
    d = Path(tempfile.mkdtemp())
    p = d / "m.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


_REVEAL_MANIFEST = {"cluster_id": "cluster_005", "beat_signal": "climax_reveal"}
_NORMAL_MANIFEST = {"cluster_id": "cluster_005", "beat_signal": "rising_action"}


# ── mode 三态 ──────────────────────────────────────────────────────────────
def test_off_returns_skeleton():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write("正文" * 500), project_root=None, manifest_path=None)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_no_reveal_beat_skips():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([{"id": "h1", "surface_text": "黑衣人"}])
        mp = _write_manifest(_NORMAL_MANIFEST)
        out = mod.scan(_write("正文" * 500), project_root=proj, manifest_path=mp)
        assert "非 reveal" in out["note"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_red_herrings_declared_skips():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([])  # 空列表
        mp = _write_manifest(_REVEAL_MANIFEST)
        out = mod.scan(_write("正文" * 500), project_root=proj, manifest_path=mp)
        assert "无 red_herrings" in out["note"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_active_dangling_triggers_fail_minor():
    """reveal beat + red_herring 未在草稿里被显式否决 → FAIL_MINOR。"""
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([
            {"id": "h1", "surface_text": "黑衣人是凶手", "setup_cluster": "cluster_002"},
        ])
        mp = _write_manifest(_REVEAL_MANIFEST)
        # 草稿里完全没提『黑衣人是凶手』
        draft = "他终于明白了一切。" * 100
        out = mod.scan(_write(draft), project_root=proj, manifest_path=mp)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "RED_HERRING_DANGLING"
        assert out["dangling_count"] == 1
        assert out["dangling_sample"][0]["mentioned"] is False
    finally:
        _set_mode(bak)


def test_active_debunked_in_cluster_passes():
    """red_herring 提及且 ±60 字内含否决标志 → debunked → PASS。"""
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([
            {"id": "h1", "surface_text": "黑衣人是凶手"},
        ])
        mp = _write_manifest(_REVEAL_MANIFEST)
        # 显式否决：『其实黑衣人是凶手并不正确』
        draft = "正文铺垫" * 100 + "其实黑衣人是凶手并非真相，真凶另有其人。" + "结尾" * 100
        out = mod.scan(_write(draft), project_root=proj, manifest_path=mp)
        assert out["verdict"] == "PASS"
        assert out["dangling_count"] == 0
        assert len(out["debunked_in_cluster"]) == 1
    finally:
        _set_mode(bak)


def test_active_mentioned_but_not_debunked():
    """提及但 ±60 字内无否决词 → 仍 dangling。"""
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([
            {"id": "h1", "surface_text": "九字诀"},
        ])
        mp = _write_manifest(_REVEAL_MANIFEST)
        draft = "九字诀这门功法他还在练习。" + "其余正文。" * 100
        out = mod.scan(_write(draft), project_root=proj, manifest_path=mp)
        assert out["dangling_count"] == 1
        assert out["dangling_sample"][0]["mentioned"] is True
        assert out["dangling_sample"][0]["mention_count"] >= 1
    finally:
        _set_mode(bak)


def test_already_debunked_earlier_cluster_skipped():
    """red_herring 已在更早 cluster 被 debunk → 不再算 unresolved。"""
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([
            {"id": "h1", "surface_text": "黑衣人", "debunked_cluster": "cluster_003"},
        ])
        mp = _write_manifest(_REVEAL_MANIFEST)  # cluster_005
        out = mod.scan(_write("正文" * 200), project_root=proj, manifest_path=mp)
        assert out["red_herrings_unresolved"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_mixed_herrings_partial_debunked():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([
            {"id": "h1", "surface_text": "红信封"},  # 草稿里否决
            {"id": "h2", "surface_text": "蓝瓷瓶"},  # dangling
        ])
        mp = _write_manifest(_REVEAL_MANIFEST)
        draft = ("红信封并不是凶器，真凶另有暗号。" * 5 +
                 "其余情节铺垫。" * 80)
        out = mod.scan(_write(draft), project_root=proj, manifest_path=mp)
        assert out["dangling_count"] == 1
        assert len(out["debunked_in_cluster"]) == 1
        assert out["dangling_sample"][0]["id"] == "h2"
    finally:
        _set_mode(bak)


def test_shadow_records_but_no_violation():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project([{"id": "h1", "surface_text": "黑衣人"}])
        mp = _write_manifest(_REVEAL_MANIFEST)
        out = mod.scan(_write("正文" * 300), project_root=proj, manifest_path=mp)
        assert out["mode"] == "shadow"
        assert out["dangling_count"] == 1
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([{"id": "h1", "surface_text": "x"}])
        mp = _write_manifest(_REVEAL_MANIFEST)
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"),
                       project_root=proj, manifest_path=mp)
        assert "草稿读取失败" in out["note"]
    finally:
        _set_mode(bak)


# ── reveal beat 多种触发路径 ─────────────────────────────────────────────
def test_is_reveal_beat_string_signal():
    assert mod._is_reveal_beat({"beat_signal": "reveal"}) is True
    assert mod._is_reveal_beat({"beat_signal": "rising_action"}) is False


def test_is_reveal_beat_list_signal():
    assert mod._is_reveal_beat({"beat_signal": ["midpoint_reveal"]}) is True


def test_is_reveal_beat_storyboard_tags():
    sb = [{"beat_tags": ["twist"]}, {"beats": "rising"}]
    assert mod._is_reveal_beat({"scene_storyboard": sb}) is True


def test_is_reveal_beat_flag_true():
    assert mod._is_reveal_beat({"is_reveal_beat": True}) is True
    assert mod._is_reveal_beat({"is_climax_reveal": True}) is True


def test_is_reveal_beat_chinese_keyword():
    assert mod._is_reveal_beat({"beat_signal": "揭底场"}) is True


def test_is_reveal_beat_empty():
    assert mod._is_reveal_beat({}) is False
    assert mod._is_reveal_beat(None) is False


# ── _is_unresolved ───────────────────────────────────────────────────────
def test_is_unresolved_no_debunked():
    assert mod._is_unresolved({"id": "x"}, "cluster_005") is True


def test_is_unresolved_debunked_earlier_returns_false():
    rh = {"id": "x", "debunked_cluster": "cluster_002"}
    assert mod._is_unresolved(rh, "cluster_005") is False


def test_is_unresolved_debunked_same_or_later_still_unresolved():
    rh = {"id": "x", "debunked_cluster": "cluster_005"}
    # debunked == current → 仍要检查（=本 cluster 是 debunk 触发点）
    assert mod._is_unresolved(rh, "cluster_005") is True


def test_is_unresolved_bad_input():
    assert mod._is_unresolved("not_a_dict", "cluster_001") is False


# ── _surface_check ──────────────────────────────────────────────────────
def test_surface_check_not_mentioned():
    r = mod._surface_check("无关正文。", "稀有词")
    assert r == {"mentioned": False, "debunked": False, "mention_count": 0}


def test_surface_check_mentioned_no_negation():
    r = mod._surface_check("黑衣人出现了。" * 5, "黑衣人")
    assert r["mentioned"] is True and r["debunked"] is False


def test_surface_check_debunked():
    r = mod._surface_check("黑衣人并非真凶。", "黑衣人")
    assert r["debunked"] is True


def test_surface_check_empty_surface():
    r = mod._surface_check("正文", "")
    assert r == {"mentioned": False, "debunked": False, "mention_count": 0}


# ── 兜底文件 ─────────────────────────────────────────────────────────────
def test_read_red_herrings_xian_suo_fallback():
    """伏笔表.json 不存在 → 线索.json 兜底。"""
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("active")
        proj = _mk_project([{"id": "h1", "surface_text": "x"}], file="线索.json")
        mp = _write_manifest(_REVEAL_MANIFEST)
        out = mod.scan(_write("正文" * 300), project_root=proj, manifest_path=mp)
        assert out["red_herrings_unresolved"] == 1
    finally:
        _set_mode(bak)


def test_cluster_id_num_extract():
    assert mod._cluster_id_num("cluster_007") == 7
    assert mod._cluster_id_num("cluster_volume_3_007") == 3  # 第一个数字
    assert mod._cluster_id_num("") == 0
    assert mod._cluster_id_num(None) == 0


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("RED_HERRING_RECALL_MODE")
    try:
        _set_mode("nonsense")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


# ── CLI ──────────────────────────────────────────────────────────────────
def _run_cli(draft, project, manifest, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft),
         "--project", str(project), "--manifest", str(manifest)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "RED_HERRING_RECALL_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_dangling():
    proj = _mk_project([{"id": "h1", "surface_text": "黑衣人"}])
    mp = _write_manifest(_REVEAL_MANIFEST)
    p = _write("普通正文" * 100)
    r = _run_cli(p, proj, mp)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean():
    proj = _mk_project([{"id": "h1", "surface_text": "x"}])
    mp = _write_manifest(_NORMAL_MANIFEST)  # 非 reveal beat
    p = _write("正文" * 300)
    r = _run_cli(p, proj, mp)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"
