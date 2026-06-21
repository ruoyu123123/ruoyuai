# -*- coding: utf-8 -*-
"""dialogue_emotion_contagion_scanner R19 W8 Batch-X·P1 情感传染+Gottman·回归。

确定性·零依赖。覆盖 off/短稿/场景数不足/_max_lagged_corr/_is_conflict_scene/
_detect_gottman_sequence/compute_dialogue_contagion_signature/作者档 override/
shadow vs active/CLI/hard_gate registry 守卫。
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
import dialogue_emotion_contagion_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "dialogue_emotion_contagion_scanner.py"
_ENV = "DIALOGUE_CONTAGION_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(names=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if names:
        (proj / "_数据库" / "角色池.json").write_text(
            json.dumps({"emerged_characters": [{"name": n} for n in names]},
                       ensure_ascii=False), encoding="utf-8")
    if baseline:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"dialogue_contagion_signature": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _dialogue_draft():
    """两个场景·小王 vs 李雷 双人主导对话。"""
    s1 = ("夜色无边，灯火阑珊。" * 30 + "\n"
          + "“你怎么来了？”小王问道。" * 4 + "\n"
          + "“我有话要说。”李雷答道。" * 4)
    s2 = ("第二天清晨。" * 30 + "\n"
          + "“说吧。”小王说道。" * 4 + "\n"
          + "“我恨你。”李雷说道。" * 4)
    return s1 + "\n\n" + s2


def _conflict_scene_text():
    """冲突场景·含 4 阶段标志词。"""
    return ("两人吵架不止·相互翻脸·“你总是这样指责我！”小王怒道。"
            "“哼，蔑视谁呢。”李雷讥讽道。"
            "“不是我的错，你才是！”小王反驳。"
            "李雷转身不说话，沉默以对。" * 6)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write_draft(_dialogue_draft()))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write_draft("短。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_too_few_scenes_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 单段长稿 → 切完场景数 < MIN_SCENES
        r = mod.scan(_write_draft("一" * 900))
        assert "场景数" in r.get("note", "") or r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_max_lagged_corr_aligned():
    xs = [1, 2, 3, 4, 5, 6]
    ys = [1, 2, 3, 4, 5, 6]
    r = mod._max_lagged_corr(xs, ys)
    assert r is not None
    assert abs(r) > 0.9


def test_max_lagged_corr_too_short():
    assert mod._max_lagged_corr([1], [1]) is None


def test_is_conflict_scene_detects():
    txt = "他们吵架翻脸怒目相对·激烈争执·彼此怒恨。"
    assert mod._is_conflict_scene(txt)


def test_is_conflict_scene_neutral():
    assert not mod._is_conflict_scene("阳光明媚·散步聊天。")


def test_detect_gottman_sequence_order():
    txt = "你总是这样·哼·不是我·沉默以对·懒得回。"
    seq = mod._detect_gottman_sequence(txt)
    stages = [s for s, _ in seq]
    assert "criticism" in stages
    assert "contempt" in stages
    assert "stonewalling" in stages


def test_detect_gottman_sequence_empty():
    seq = mod._detect_gottman_sequence("天气真好。")
    assert seq == []


def test_load_characters_no_project():
    assert mod._load_characters(None) == set()


def test_load_characters_from_project():
    proj = _mk_project(names=["小王", "李雷"])
    s = mod._load_characters(proj)
    assert "小王" in s
    assert "李雷" in s


def test_compute_signature_empty_paths():
    sig = mod.compute_dialogue_contagion_signature([])
    assert sig["sync_window_baseline"]["sample_n"] == 0
    assert sig["gottman_cascade_n"] == 0


def test_compute_signature_with_data():
    p = _write_draft(_dialogue_draft() + "\n\n" + _conflict_scene_text())
    sig = mod.compute_dialogue_contagion_signature(
        [str(p)], names={"小王", "李雷"})
    assert "sync_window_baseline" in sig
    assert "gottman_cascade_freq" in sig


def test_shadow_mode_no_violations():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(names=["小王", "李雷"])
        r = mod.scan(_write_draft(_dialogue_draft()), project_root=proj)
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_cli_runs():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = subprocess.run(
            [sys.executable, str(_TARGET), str(_write_draft(_dialogue_draft()))],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
        assert r.returncode in (0, 1)
        assert "scanner" in r.stdout
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert "DIALOGUE_CONTAGION_ABNORMAL" not in set(data.get("hard_gate_codes", []))


def test_registry_entry_new_true():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["scanners"].get("dialogue_emotion_contagion_scanner")
    assert entry is not None
    assert entry.get("_new") is True
    assert "DIALOGUE_CONTAGION_ABNORMAL" in entry.get("issues_emitted", [])
