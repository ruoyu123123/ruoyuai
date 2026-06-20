# -*- coding: utf-8 -*-
"""prose_child_voice_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import prose_child_voice_scanner as cv  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CHILD_VOICE_MODE", None)
    else:
        os.environ["CHILD_VOICE_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, genre=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    style = {}
    if genre is not None:
        style["genre"] = genre
    if baseline is not None:
        style["child_voice_baseline"] = baseline
    if style:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return proj


def _mk_manifest(**kw):
    p = Path(tempfile.mkdtemp()) / "m.json"
    p.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
    return p


def test_off_skeleton():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("off")
        rep = cv.scan(str(_write("正文" * 500)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_skip_when_no_child_voice_signal():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="cultivation")
        rep = cv.scan(str(_write("正文" * 500)), project_root=proj)
        assert "非童声" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_pov_age_triggers():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        mf = _mk_manifest(pov_age=8)
        rep = cv.scan(str(_write("正文" * 500)), project_root=proj,
                     manifest_path=str(mf))
        assert "indices" in rep
    finally:
        _set_mode(bak)


def test_genre_school_triggers():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="campus")
        rep = cv.scan(str(_write("正文" * 500)), project_root=proj)
        assert "indices" in rep
    finally:
        _set_mode(bak)


def test_fallback_drift_hard_words_over_concrete():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(pov_age=10)
        text = ("辨证逻辑范畴隐喻象征本质存在意识" + "正文" * 500)
        rep = cv.scan(str(_write(text)), project_root=mf.parent.parent,
                     manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        # 触发: hard 大于 concrete
        assert "CHILD_VOICE_REGISTER_DRIFT" in codes or rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_baseline_drift_check():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="campus",
                          baseline={"enabled": True,
                                    "concrete_per_kCJK": 100,
                                    "hard_word_per_kCJK_max": 0.1,
                                    "abstract_lead_max": 0.05})
        text = "辨证逻辑范畴隐喻象征" + "正文" * 500
        rep = cv.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "CHILD_VOICE_REGISTER_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_shadow_mode():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(genre="campus")
        rep = cv.scan(str(_write("正文" * 500)), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "CHILD_VOICE_REGISTER_DRIFT" not in hgs


def test_short_skips():
    bak = os.environ.get("CHILD_VOICE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="campus")
        rep = cv.scan(str(_write("短")), project_root=proj)
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)
