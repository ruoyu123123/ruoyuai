# -*- coding: utf-8 -*-
"""mid_chapter_micro_cliffhanger_cadence_scanner R20 W9 Batch-CC · P2 · 章内 hook 间距分布
确定性·零依赖·零 LLM/零联网。
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
import mid_chapter_micro_cliffhanger_cadence_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "mid_chapter_micro_cliffhanger_cadence_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("MID_CHAPTER_CLIFF_CADENCE_MODE", None)
    else:
        os.environ["MID_CHAPTER_CLIFF_CADENCE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"micro_cliffhanger_cadence_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 适中 hook 间距 (每段 ~500-1500 CJK 一个 hook)
_NORMAL_CADENCE = "\n".join([
    "他走进房间。" + "墙上的钟在走。" * 50 + "他突然不知道下一步该怎么办。",
    "她抬头看了一眼。" + "窗外天色暗了。" * 50 + "那人说话竟然带着哭腔。",
    "走廊里有脚步声。" + "他靠在墙上。" * 50 + "没想到来的是个陌生人。",
] * 3)

# 密集 hook (间距过密 · 连珠炮)
_HIGH_DENSITY = ("不知道为什么。竟然是他。没想到来的居然不是。真相竟然是这样。"
                 "怎么会这样。是谁告诉的他。" * 80)

# 稀疏 hook (间距过疏 · 中段冷场)
# 4 个 hook · 中间用纯描写 1500 CJK 间隔 → distances 全 > 3000 不可能, 控制为 > LOW_DENSITY_MAX (3000)
_FILLER = "他坐着。她也坐着。墙是白色。地板是木质。窗户是关着。" * 200  # ~6000 CJK 填充
_LOW_DENSITY = (
    "他不知道发生了什么。"
    + _FILLER
    + "竟然如此。"
    + _FILLER
    + "没想到这里有人。"
    + _FILLER
    + "真相是这样。"
)

_VERY_SHORT = "他不知道。"


def test_off_returns_skeleton():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_NORMAL_CADENCE), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "hook_count" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_VERY_SHORT), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HIGH_DENSITY), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_high_density_detected():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HIGH_DENSITY), _mk_project())
        if out.get("hook_count", 0) >= mod.MIN_HOOKS:
            assert out["intervals_below_min"] > 0
    finally:
        _set_mode(bak)


def test_low_density_detected():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LOW_DENSITY), _mk_project())
        if out.get("hook_count", 0) >= mod.MIN_HOOKS:
            assert out["intervals_above_max"] > 0
    finally:
        _set_mode(bak)


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("active")
        baseline = {"mean_distance": 100.0, "std_distance": 100000.0}
        out = mod.scan(_write(_HIGH_DENSITY), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        # 极宽 std → z 接近 0 → 不报
        codes = {f["code"] for f in out.get("flags", [])}
        assert mod.ISSUE_CODE not in codes
    finally:
        _set_mode(bak)


def test_too_few_hooks_skipped():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("active")
        # 含够字数但无 hook 命中
        out = mod.scan(_write("他走路。她走路。" * 500), _mk_project())
        assert out.get("hook_count", 0) < mod.MIN_HOOKS
        assert "样本不足" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_collect_hook_positions_sorted():
    pos = mod._collect_hook_positions("竟然如此。不知道为什么。")
    assert all(pos[i][0] <= pos[i + 1][0] for i in range(len(pos) - 1))


def test_adjacent_distances_empty_when_one():
    assert mod._adjacent_distances([(0, "a")]) == []


def test_z_function():
    assert mod._z(10.0, 0.0, 2.0) == 5.0
    assert mod._z(0.0, 0.0, 0.0) == 0.0


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    # 北极星守卫
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_CODE not in audit_hub.HARD_GATE_CODES


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "MID_CHAPTER_CLIFF_CADENCE_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_NORMAL_CADENCE)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "mid_chapter_micro_cliffhanger_cadence"
