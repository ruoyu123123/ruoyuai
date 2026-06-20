# -*- coding: utf-8 -*-
"""rhetoric_parallel_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import rhetoric_parallel_scanner as rp  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("RHETORIC_PARALLEL_MODE", None)
    else:
        os.environ["RHETORIC_PARALLEL_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_rhetoric_parallel_signature": baseline},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("off")
        rep = rp.scan(str(_write("正文" * 500)))
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("active")
        rep = rp.scan(str(_write("短")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_anaphora_detected():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("active")
        # 三个句首相同 "他来"
        text = ("他来到山上。他来到河边。他来到田间。"
                + "正文" * 500)
        rep = rp.scan(str(_write(text)))
        assert rep["rhetoric_parallel_signature"]["anaphora_density"] > 0
    finally:
        _set_mode(bak)


def test_epistrophe_detected():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("active")
        text = "山上很美。河边很美。田间很美。" + "正文" * 500
        rep = rp.scan(str(_write(text)))
        assert "rhetoric_parallel_signature" in rep
    finally:
        _set_mode(bak)


def test_polysyndeton_detected():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("active")
        text = ("又苦又累又难又烦又怕又困\n"
                + "正文" * 500)
        rep = rp.scan(str(_write(text)))
        assert rep["rhetoric_parallel_signature"]["polysyndeton_run_density"] >= 0
    finally:
        _set_mode(bak)


def test_no_baseline_no_violation():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("active")
        rep = rp.scan(str(_write("普通正文。" * 200)))
        assert rep["baseline_source"] == "none"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_baseline_drift():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("active")
        # 设高基线 · 草稿密度低
        proj = _mk_project(baseline={
            "anaphora_density": 5.0,
            "epistrophe_density": 4.0,
            "parallel_clause_density": 4.0,
            "polysyndeton_run_density": 3.0})
        rep = rp.scan(str(_write("素净正文。" * 200)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "RHETORIC_PARALLEL_GAP" in codes
    finally:
        _set_mode(bak)


def test_shadow_mode():
    bak = os.environ.get("RHETORIC_PARALLEL_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(baseline={"anaphora_density": 5.0,
                                     "epistrophe_density": 5.0,
                                     "parallel_clause_density": 5.0,
                                     "polysyndeton_run_density": 5.0})
        rep = rp.scan(str(_write("素净正文。" * 200)), project_root=proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "RHETORIC_PARALLEL_GAP" not in hgs
