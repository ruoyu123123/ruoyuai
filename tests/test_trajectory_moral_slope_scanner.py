# -*- coding: utf-8 -*-
"""trajectory_moral_slope_scanner R22 W10 Batch-EE·P1 回归测试"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import trajectory_moral_slope_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "trajectory_moral_slope_scanner.py"
_ENV = "TRAJECTORY_MORAL_SLOPE_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


# 进展型（首尾 ≥600 CJK，每段含 pos 词≥3，4 维 Σ|Δ|≥3）
def _progressivist_draft():
    head = "他在路上走着。一切平凡。" * 80   # 12*80=960 CJK·无 pos/neg
    tail = ("他突破强大。他升迁尊崇。他和好相爱。他脱险化解。" * 80)  # 大量 pos
    return head + "\n\n" + tail


# 衰退型（首段无·尾段大量 neg）
def _declensionist_draft():
    head = "他在路上走着。一切平凡。" * 80
    tail = ("他重创虚弱。他失势落魄。他决裂背叛。他危险绝境。" * 80)
    return head + "\n\n" + tail


# 扁平型（首尾都无 pos/neg）
def _flat_draft():
    return "他在路上走着。" * 200


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_flat_draft()), _mk_project())
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("他成功了。"), _mk_project())
        assert "草稿不足" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_progressivist_label():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_progressivist_draft()), _mk_project())
        assert out["label"] == "clear_progressivist"
        # 仅 AMBIGUOUS 报警·上升型不报
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_declensionist_label():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_declensionist_draft()), _mk_project())
        assert out["label"] == "clear_declensionist"
        # 仅 AMBIGUOUS 报警·下降型不报
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_flat_advisory_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_flat_draft()), _mk_project())
        assert out["label"] == "AMBIGUOUS_FLAT_TRAJECTORY"
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "AMBIGUOUS_FLAT_TRAJECTORY"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_flat_shadow_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_flat_draft()), _mk_project())
        assert out["label"] == "AMBIGUOUS_FLAT_TRAJECTORY"
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_history_appended():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        mod.scan(_write(_flat_draft()), proj, cluster_id="c1")
        hist_file = proj / "_数据库" / "trajectory_moral_slope_history.json"
        assert hist_file.exists()
        data = json.loads(hist_file.read_text(encoding="utf-8"))
        assert len(data["history"]) >= 1
        assert data["history"][0]["cluster"] == "c1"
        assert data["history"][0]["label"] == "AMBIGUOUS_FLAT_TRAJECTORY"
    finally:
        _set_mode(bak)


def test_history_append_invalid_existing():
    """已存在 history 文件 invalid → 不崩溃·重置 history"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        (proj / "_数据库" / "trajectory_moral_slope_history.json").write_text(
            "not json", encoding="utf-8")
        mod.scan(_write(_flat_draft()), proj, cluster_id="c2")
        data = json.loads(
            (proj / "_数据库" / "trajectory_moral_slope_history.json").read_text(encoding="utf-8"))
        assert len(data["history"]) >= 1
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文\n---CHANGES---\nlog") == "正文"


def test_cjk_count():
    assert mod._cjk_count("好abc世界") == 3


def test_slice_cjk_head_and_tail():
    # 50 CJK
    text = "甲" * 50
    head = mod._slice_cjk(text, 10, from_end=False)
    tail = mod._slice_cjk(text, 10, from_end=True)
    assert mod._cjk_count(head) >= 10
    assert mod._cjk_count(tail) >= 10


def test_state_score_zero():
    res = mod._state_score("一段无关文本")
    for dim in ("power", "status", "relation", "threat"):
        assert res[dim]["net"] == 0


def test_classify_thresholds():
    label, _, _ = mod._classify({"power": 0, "status": 0, "relation": 0, "threat": 0})
    assert label == "AMBIGUOUS_FLAT_TRAJECTORY"
    label2, _, _ = mod._classify({"power": 5, "status": 0, "relation": 0, "threat": 0})
    assert label2 == "clear_progressivist"
    label3, _, _ = mod._classify({"power": -5, "status": 0, "relation": 0, "threat": 0})
    assert label3 == "clear_declensionist"
    # net=0 但 abs≥3 → 退到 AMBIGUOUS
    label4, _, _ = mod._classify({"power": 2, "status": -2, "relation": 0, "threat": 0})
    assert label4 == "AMBIGUOUS_FLAT_TRAJECTORY"


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_flat():
    proj = _mk_project()
    r = _run_cli(_write(_flat_draft()), proj)
    assert r.returncode == 1, r.stderr


def test_main_exit_0_on_progressivist():
    proj = _mk_project()
    r = _run_cli(_write(_progressivist_draft()), proj)
    assert r.returncode == 0, r.stderr
