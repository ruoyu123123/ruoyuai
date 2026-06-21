# -*- coding: utf-8 -*-
"""paywall_transition_gradient_scanner R18 W7 Batch-S·P0 入V双峰钩回归测试

确定性·零依赖。覆盖 15+ case：off/无用户偏好/非 paywall cluster/有递增+有 reveal/
非递增/无 reveal/active/shadow/短稿/读取失败/_mode/CLI 退出码/分隔符/_cjk_count。
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
import paywall_transition_gradient_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "paywall_transition_gradient_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("PAYWALL_TRANSITION_GRADIENT_MODE", None)
    else:
        os.environ["PAYWALL_TRANSITION_GRADIENT_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(pref_value=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    prefs = {"workflow_preferences": []}
    if pref_value is not None:
        prefs["workflow_preferences"].append({
            "key": "paywall_transition_cluster_id", "value": pref_value})
    (db / "用户偏好.json").write_text(json.dumps(prefs, ensure_ascii=False),
                                     encoding="utf-8")
    return proj


_SEP = "\n━━━━━━━━━━━━\n"

# 三个场景·stake 递增·末场带 reveal
_GOOD_DRAFT = (
    "他走进酒馆。窗外微风。" * 50   # scene 1·低 stake
    + _SEP
    + "陷阱已经布好。危险渐近。" * 50   # scene 2·中 stake
    + _SEP
    + "生死陷阱伏杀绝境。原来真相是阴谋。竟然如此。" * 50  # scene 3·高 stake + reveal
)

# 末场 stake 下降·无 reveal
_BAD_DRAFT = (
    "生死性命危险陷阱伏杀。" * 50    # scene 1·高 stake
    + _SEP
    + "陷阱危险。" * 50              # scene 2·中
    + _SEP
    + "他走进酒馆。窗外微风。" * 50  # scene 3·低 stake·无 reveal
)


def test_off_returns_skeleton():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("off")
        proj = _mk_project(pref_value="cluster_001")
        out = mod.scan(_write(_GOOD_DRAFT), proj, cluster_id="cluster_001")
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_no_user_pref_skip():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write(_GOOD_DRAFT), proj, cluster_id="cluster_001")
        assert out["is_paywall_transition_cluster"] is False
        assert "跳过" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_non_paywall_cluster_skip():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pref_value="cluster_002")
        out = mod.scan(_write(_GOOD_DRAFT), proj, cluster_id="cluster_001")
        assert out["is_paywall_transition_cluster"] is False
    finally:
        _set_mode(bak)


def test_good_draft_passes_active():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pref_value="cluster_001")
        out = mod.scan(_write(_GOOD_DRAFT), proj, cluster_id="cluster_001")
        assert out["is_paywall_transition_cluster"] is True
        assert out["verdict"] == "PASS"
        assert out["mega_reveal_hits_last_scene"] >= 1
    finally:
        _set_mode(bak)


def test_bad_draft_active_fail():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pref_value="cluster_001")
        out = mod.scan(_write(_BAD_DRAFT), proj, cluster_id="cluster_001")
        assert out["is_paywall_transition_cluster"] is True
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["violations"][0]["code"] == "BRIDGE_PAYWALL_HOOK_GRADIENT_OFF"
    finally:
        _set_mode(bak)


def test_bad_draft_shadow_no_report():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(pref_value="cluster_001")
        out = mod.scan(_write(_BAD_DRAFT), proj, cluster_id="cluster_001")
        assert out["violations"] == []
        assert out["warning"] is None
    finally:
        _set_mode(bak)


def test_pref_list_form():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pref_value=["cluster_001", "cluster_005"])
        out = mod.scan(_write(_GOOD_DRAFT), proj, cluster_id="cluster_005")
        assert out["is_paywall_transition_cluster"] is True
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(pref_value="cluster_001")
        out = mod.scan(_write("陷阱。" * 10), proj, cluster_id="cluster_001")
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_mode_default_shadow():
    bak = os.environ.get("PAYWALL_TRANSITION_GRADIENT_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_factual():
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("你好abc") == 2


def test_is_paywall_no_project():
    assert mod._is_paywall_cluster(None, "cluster_001") is False


def test_score_zero_keywords():
    s, h = mod._score("纯文字无关键词。" * 100, ("nonexistent_kw",))
    assert h == 0
    assert s == 0.0


def test_split_scenes_falls_back_to_single():
    parts = mod._split_scenes("纯文本无分隔。" * 20)
    assert len(parts) == 1


def test_cluster_id_blank_returns_false():
    assert mod._is_paywall_cluster(_mk_project(pref_value="cluster_001"), "") is False


def _run_cli(draft_path, project=None, cluster_id="", mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    if cluster_id:
        cmd += ["--cluster-id", cluster_id]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PAYWALL_TRANSITION_GRADIENT_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(pref_value="cluster_001")
    r = _run_cli(_write(_BAD_DRAFT), proj, "cluster_001")
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


def test_main_exit_0_on_clean():
    proj = _mk_project(pref_value="cluster_001")
    r = _run_cli(_write(_GOOD_DRAFT), proj, "cluster_001")
    assert r.returncode == 0, r.stderr
