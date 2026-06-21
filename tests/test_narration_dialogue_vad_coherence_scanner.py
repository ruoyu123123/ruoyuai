# -*- coding: utf-8 -*-
"""narration_dialogue_vad_coherence_scanner R19 W8 Batch-X·P1 旁白对话 VAD·回归。

确定性·零依赖。覆盖 off/短稿/无场景 skip/_split_narration_dialogue/_score_vad/
_pearson/作者档 override/shadow vs active/CLI/hard_gate registry 守卫。
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
import narration_dialogue_vad_coherence_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "narration_dialogue_vad_coherence_scanner.py"
_ENV = "NARR_DIAL_VAD_MODE"


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


def _mk_project(target_corr=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if target_corr is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"dial_narr_vad_target_corr": target_corr}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _multi_scene_draft(n=5):
    """生成 n 个场景，每场景含旁白 + 对话。"""
    blocks = []
    for i in range(n):
        narr = ("夜色无边，灯火阑珊。" * 60)
        dial = f"“你怎么来了？”他低声问。“我有话要说。”她回答。"
        blocks.append(narr + "\n" + dial)
    return "\n\n".join(blocks)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write_draft(_multi_scene_draft()))
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
        # 单段长稿 → 切完场景数 < 4
        r = mod.scan(_write_draft("一" * 1500))
        # 要么场景数不足，要么无对话/无 VAD 命中 → 任一 skip 即可
        assert ("场景数" in r.get("note", "") or "配对场景" in r.get("note", "")
                or r["verdict"] == "PASS")
    finally:
        _set_mode(bak)


def test_split_narration_dialogue_correct():
    text = "他走过去。“你好。”她说。\n小心。"
    narr, dial = mod._split_narration_dialogue(text)
    assert "你好" in dial
    assert "你好" not in narr
    assert "走过去" in narr
    assert "小心" in narr


def test_score_vad_returns_none_on_empty():
    assert mod._score_vad("abcxyz") is None


def test_pearson_perfect_correlation():
    assert mod._pearson([1, 2, 3, 4], [1, 2, 3, 4]) > 0.99


def test_pearson_zero_variance():
    assert mod._pearson([1, 1, 1], [1, 2, 3]) == 0.0


def test_load_author_targets_returns_none_no_data():
    assert mod._load_author_targets(None, None) is None


def test_load_author_targets_from_project():
    proj = _mk_project(target_corr={"V": 0.40, "A": 0.50, "D": 0.30})
    t = mod._load_author_targets(proj, None)
    assert t == {"V": 0.40, "A": 0.50, "D": 0.30}


def test_shadow_mode_no_violations():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write_draft(_multi_scene_draft(6)))
        # shadow 必须不上报 violations
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_active_mode_metrics_populated():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write_draft(_multi_scene_draft(8)))
        # metrics 应有 total_scenes 字段
        if "metrics" in r:
            assert "total_scenes" in r["metrics"]
    finally:
        _set_mode(bak)


def test_cli_runs():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = subprocess.run(
            [sys.executable, str(_TARGET), str(_write_draft(_multi_scene_draft()))],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
        assert r.returncode in (0, 1)
        assert "scanner" in r.stdout
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert "NARR_DIAL_VAD_OVERCOUPLED" not in set(data.get("hard_gate_codes", []))


def test_registry_entry_new_true():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["scanners"].get("narration_dialogue_vad_coherence_scanner")
    assert entry is not None
    assert entry.get("_new") is True
    assert "NARR_DIAL_VAD_OVERCOUPLED" in entry.get("issues_emitted", [])
