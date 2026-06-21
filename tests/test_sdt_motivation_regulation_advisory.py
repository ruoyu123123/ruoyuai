# -*- coding: utf-8 -*-
"""sdt_motivation_regulation_advisory R22 W10 Batch-EE·P1 SDT 调节回归测试

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
import sdt_motivation_regulation_advisory as mod  # noqa: E402

_TARGET = _SCRIPTS / "sdt_motivation_regulation_advisory.py"
_ENV = "SDT_REGULATION_MODE"


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


def _mk_project(characters=None, profile=None, prev_regulation=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    if profile is not None:
        (proj / "作者风格.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    if prev_regulation is not None:
        (db / "sdt_regulation_profile.json").write_text(
            json.dumps({"by_character": prev_regulation}, ensure_ascii=False), encoding="utf-8")
    return proj


# 张三长稿·intrinsic 主导
_INTRINSIC_DRAFT = "张三觉得有趣。他很喜欢。" * 60


# 张三长稿·external 主导（distance=4 跨度≥2）
_EXTERNAL_DRAFT = "张三被迫接受命令。他不得不去赚钱。" * 60


# external 但带 on-page 触发词
_EXTERNAL_W_TRIGGER = "张三被迫接受命令。他不得不去赚钱。觉醒了。" * 60


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_INTRINSIC_DRAFT), proj)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_no_character_card_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_INTRINSIC_DRAFT), _mk_project())
        assert out["verdict"] == "PASS"
        assert out["character_count"] == 0
    finally:
        _set_mode(bak)


def test_override_flag_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           profile={"_sdt_regulation_override": True},
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        assert out["verdict"] == "PASS"
        assert "反类型豁免" in out["note"]
    finally:
        _set_mode(bak)


def test_shadow_drift_no_report():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        # shadow 不上报 violations
        assert out["violations"] == []
        assert out["warning"] is None
        # 但记录 drift
        assert out["drift_count"] >= 1
    finally:
        _set_mode(bak)


def test_active_drift_fail_minor():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "SDT_REGULATION_DRIFT"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_drift_with_on_page_trigger_no_advisory():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        out = mod.scan(_write(_EXTERNAL_W_TRIGGER), proj)
        # 有 on-page trigger → 不报
        assert out["drift_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_distance_distance1_no_drift():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 相邻级（intrinsic↔integrated 距 1）·不触发
        proj = _mk_project(characters=[{"name": "张三"}],
                           prev_regulation={"张三": {"dominant": "intrinsic"}})
        text = "张三本来一直天性如此。我是这种人。" * 60
        out = mod.scan(_write(text), proj)
        # 跨度=1（intrinsic→integrated）不报
        assert out["drift_count"] == 0
    finally:
        _set_mode(bak)


def test_no_prev_profile_no_drift():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write(_EXTERNAL_DRAFT), proj)
        # 无 prev → 仅记录 current·无 drift
        assert out["drift_count"] == 0
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project(characters=[{"name": "张三"}])
        out = mod.scan(_write("张三好奇。"), proj)
        assert out["note"] == "草稿太短·跳过"
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
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_regulation_distance_basic():
    assert mod._regulation_distance("intrinsic", "amotivation") == 5
    assert mod._regulation_distance("intrinsic", "intrinsic") == 0
    assert mod._regulation_distance("unknown", "external") == 0


def test_strip_changes_basic():
    assert mod._strip_changes("正文。\n---CHANGES---\nlog") == "正文。"
    assert mod._strip_changes("正文。\n---CHANGES_FACTUAL---\nlog") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("你好abc世") == 3


def test_dominant_regulation_no_match():
    dom, dist = mod._dominant_regulation("无关文本", "张三")
    assert dom == ""
    assert dist == {}


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[{"name": "张三"}])
    r = _run_cli(_write(_INTRINSIC_DRAFT), proj)
    assert r.returncode == 0, r.stderr


def test_main_exit_1_on_drift():
    proj = _mk_project(characters=[{"name": "张三"}],
                       prev_regulation={"张三": {"dominant": "intrinsic"}})
    r = _run_cli(_write(_EXTERNAL_DRAFT), proj)
    assert r.returncode == 1, r.stderr
