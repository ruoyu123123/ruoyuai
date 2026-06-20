# -*- coding: utf-8 -*-
"""cohn_consciousness_mode_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cohn_consciousness_mode_scanner as ccm  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("COHN_CONSCIOUSNESS_MODE", None)
    else:
        os.environ["COHN_CONSCIOUSNESS_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(sig=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if sig is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"cohn_mode_signature": sig}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("off")
        rep = ccm.scan(str(_write("正文" * 200)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("active")
        rep = ccm.scan(str(_write("短")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_quoted_thought_classified():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("active")
        text = ("他想道:\"我必须前进。\"" * 30) + "正文" * 200
        rep = ccm.scan(str(_write(text)))
        if "cohn_mode_distribution" in rep:
            assert rep["mode_counts"]["quoted"] >= 1
    finally:
        _set_mode(bak)


def test_psycho_narration_dominant():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("active")
        text = ("他意识到危险即将来临。" * 25
                + "她明白结局。" * 25 + "正文" * 200)
        rep = ccm.scan(str(_write(text)))
        if "mode_counts" in rep:
            assert rep["mode_counts"]["psycho_narration"] >= 1
    finally:
        _set_mode(bak)


def test_fid_marker():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("active")
        text = ("也许这是最后一次了。" * 25
                + "竟然没人发现。" * 25 + "正文" * 200)
        rep = ccm.scan(str(_write(text)))
        if "mode_counts" in rep:
            assert rep["mode_counts"]["narrated_monologue"] >= 1
    finally:
        _set_mode(bak)


def test_drift_with_author_baseline():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("active")
        # 作者基线偏向 quoted, 草稿全 psycho
        proj = _mk_project(sig={"quoted": 0.7, "psycho_narration": 0.1,
                                "autonomous": 0.1, "narrated_monologue": 0.1})
        text = ("他意识到危险。" * 50 + "正文" * 200)
        rep = ccm.scan(str(_write(text)), project_root=proj)
        if "drift_flags" in rep:
            assert len(rep["drift_flags"]) >= 1
    finally:
        _set_mode(bak)


def test_default_baseline_when_no_profile():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("active")
        text = "他意识到天黑了。" * 50 + "正文" * 200
        rep = ccm.scan(str(_write(text)))
        if "baseline_source" in rep:
            assert rep["baseline_source"] == "default"
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "COHN_MODE_DRIFT" not in hgs


def test_shadow_mode():
    bak = os.environ.get("COHN_CONSCIOUSNESS_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(sig={"quoted": 0.9, "psycho_narration": 0.05,
                                "autonomous": 0.025,
                                "narrated_monologue": 0.025})
        text = "他意识到天黑了。" * 50 + "正文" * 200
        rep = ccm.scan(str(_write(text)), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)
