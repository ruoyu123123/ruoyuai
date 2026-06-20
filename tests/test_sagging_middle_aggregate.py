# -*- coding: utf-8 -*-
"""cross_cluster_sagging_middle_aggregate.py 单测（R7 Batch-D · 2026-06-20）。

确定性·零依赖·零 LLM/零联网。覆盖：
  ① _middle_slice 40-60% 区段
  ② _has_drive_purpose / _cluster_has_drive 五选一推动 purpose
  ③ detect_reversal_void / detect_stakes_flat / detect_purpose_void 三规则
  ④ off / shadow / active CLI exit code
  ⑤ needs_midpoint_bomb snapshot 字段
  ⑥ 边界：cluster 太少跳过
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
import cross_cluster_sagging_middle_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_sagging_middle_aggregate.py"


def _utf8_env(**extra):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(extra)
    return env


def _mk_project(clusters):
    proj = Path(tempfile.mkdtemp(prefix="sagging_"))
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    summary = {"schema_version": "v2.cluster", "clusters": clusters}
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def _cluster(cid, *, chapters=None, ch_range=(1, 3)):
    """造 cluster 摘要·chapters 是 {ch_str: ChapterRecord} dict。"""
    chs = chapters or {str(ch_range[0]): {}}
    return {
        "cluster_id": cid, "title": cid,
        "chapter_range": list(ch_range),
        "cluster_end_ch": ch_range[1],
        "status": "done",
        "chapters": chs,
    }


# ── 单元：_middle_slice ────────────────────────────────────
def test_middle_slice_takes_40_to_60_pct():
    clusters = [{"cluster_id": f"c{i}"} for i in range(10)]
    middle = mod._middle_slice(clusters)
    # 10 cluster · [4:7] = c4, c5, c6
    assert [c["cluster_id"] for c in middle] == ["c4", "c5", "c6"]


def test_middle_slice_too_short_returns_empty():
    assert mod._middle_slice([{"cluster_id": "a"}] * 3) == []


# ── 单元：_has_drive_purpose ───────────────────────────────
def test_has_drive_purpose_via_scene_type():
    assert mod._has_drive_purpose({"scene_type": "reveal_truth"}) is True
    assert mod._has_drive_purpose({"scene_type": "fight_filler"}) is False


def test_has_drive_purpose_via_turning_point():
    assert mod._has_drive_purpose({"turning_point": "主角识破阴谋"}) is True


def test_has_drive_purpose_via_beat_signal_hit():
    assert mod._has_drive_purpose({"beat_signal_hit": True}) is True
    assert mod._has_drive_purpose({"beat_signal_hit": False}) is False


def test_has_drive_purpose_via_beats_addressed():
    assert mod._has_drive_purpose({"beats_addressed": ["escalate"]}) is True
    assert mod._has_drive_purpose({"beats_addressed": ["filler"]}) is False


# ── 单元：detect_reversal_void ─────────────────────────────
def test_reversal_void_triggers_on_low_scores_no_turns():
    middle = [
        _cluster("m1", chapters={"1": {"golden_scores": {"turn": 0.3}, "hook_score": 0.2}}),
        _cluster("m2", chapters={"2": {"golden_scores": {"turn": 0.2}, "hook_score": 0.3}}),
    ]
    rev = mod.detect_reversal_void(middle)
    assert rev["hit"] is True


def test_reversal_void_does_not_trigger_on_high_scores_with_turns():
    middle = [
        _cluster("m1", chapters={"1": {"golden_scores": {"turn": 0.9},
                                       "hook_score": 0.9,
                                       "turning_point": "X 反转"}}),
        _cluster("m2", chapters={"2": {"golden_scores": {"turn": 0.8},
                                       "hook_score": 0.7}}),
    ]
    rev = mod.detect_reversal_void(middle)
    assert rev["hit"] is False


# ── 单元：detect_stakes_flat ───────────────────────────────
def test_stakes_flat_triggers_on_low_variety_no_rise():
    middle = [
        _cluster("m1", chapters={"1": {"stress_total": 3, "scene_type": "fight"}}),
        _cluster("m2", chapters={"2": {"stress_total": 3, "scene_type": "fight"}}),
        _cluster("m3", chapters={"3": {"stress_total": 3, "scene_type": "fight"}}),
    ]
    sta = mod.detect_stakes_flat(middle)
    assert sta["hit"] is True


def test_stakes_flat_does_not_trigger_when_rising():
    middle = [
        _cluster("m1", chapters={"1": {"stress_total": 1, "scene_type": "fight"}}),
        _cluster("m2", chapters={"2": {"stress_total": 5, "scene_type": "reveal"}}),
        _cluster("m3", chapters={"3": {"stress_total": 9, "scene_type": "decision"}}),
    ]
    sta = mod.detect_stakes_flat(middle)
    assert sta["hit"] is False


# ── 单元：detect_purpose_void ──────────────────────────────
def test_purpose_void_triggers_on_3_consecutive_empty():
    middle = [
        _cluster("m1", chapters={"1": {"scene_type": "filler"}}),
        _cluster("m2", chapters={"2": {"scene_type": "filler"}}),
        _cluster("m3", chapters={"3": {"scene_type": "filler"}}),
    ]
    pur = mod.detect_purpose_void(middle)
    assert pur["hit"] is True
    assert pur["max_void_streak"] >= 3


def test_purpose_void_resets_on_drive_hit():
    middle = [
        _cluster("m1", chapters={"1": {"scene_type": "filler"}}),
        _cluster("m2", chapters={"2": {"scene_type": "reveal"}}),
        _cluster("m3", chapters={"3": {"scene_type": "filler"}}),
        _cluster("m4", chapters={"4": {"scene_type": "filler"}}),
    ]
    pur = mod.detect_purpose_void(middle)
    assert pur["hit"] is False  # 最长 streak = 2


# ── CLI：shadow 模式 zero exit ────────────────────────────
def test_cli_shadow_exits_zero():
    clusters = [_cluster(f"c{i}", ch_range=(i, i)) for i in range(10)]
    proj = _mk_project(clusters)
    env = _utf8_env(SAGGING_MIDDLE_MODE="shadow")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0
    snap = proj / "_数据库" / ".cross_chapter_scan" / "sagging_middle_snapshot.json"
    assert snap.exists()
    snap_data = json.loads(snap.read_text(encoding="utf-8"))
    assert "needs_midpoint_bomb" in snap_data


# ── CLI：cluster 太少 skip ──────────────────────────────
def test_cli_too_few_clusters_skip():
    proj = _mk_project([_cluster("c1")])
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=_utf8_env(), encoding="utf-8")
    assert r.returncode == 0


# ── CLI：off 模式 ──────────────────────────────────────
def test_cli_off_mode():
    proj = _mk_project([_cluster(f"c{i}", ch_range=(i, i)) for i in range(10)])
    env = _utf8_env(SAGGING_MIDDLE_MODE="off")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0
