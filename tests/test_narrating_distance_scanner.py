# -*- coding: utf-8 -*-
"""narrating_distance_scanner 专属测试 — 五分级时距(advisory · 2026-06-20)

钉死：
  · concurrent / recent / distant / posthumous / atemporal 锚词桶检测
  · declared 远距 + 实际 concurrent → DISTANCE_FLATTENED
  · 无 declared + 锚词混杂 + hindsight=0 → DISTANCE_TENSE_DRIFT
  · R7 firstperson_retro 是其子集（共存零冲突）
  · mode=off/shadow/active
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import narrating_distance_scanner as nd  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, narrating_distance=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if narrating_distance is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"narrating_distance": narrating_distance},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _mk_manifest(**kwargs):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False)
    f.write(json.dumps(kwargs, ensure_ascii=False))
    f.close()
    return Path(f.name)


def _set_mode(m):
    if m is None:
        os.environ.pop("NARRATING_DISTANCE_MODE", None)
    else:
        os.environ["NARRATING_DISTANCE_MODE"] = m


# 大概 600 CJK·分别造五种锚词主导的草稿
_BASE = "夜风扫过山脊石阶落满松针他独自向上踏步影子被拉得很长。" * 30  # ~720 CJK 基底


def test_off_returns_skeleton():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("off")
        rep = nd.scan(str(_write(_BASE)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        rep = nd.scan(str(_write("此刻他想起多年前。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_flattened_when_declared_distant_but_concurrent():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(narrating_distance="distant")
        text = _BASE + ("此刻他走在街上。" * 8 + "现在他抬头。" * 8)
        rep = nd.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "DISTANCE_FLATTENED" in codes
        assert rep["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_pass_when_declared_distant_with_hindsight():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(narrating_distance="distant")
        text = (_BASE + "回想起来当年事。" * 6 + "多年前那一年。" * 6
                + "如今想来后来才明白。" * 6 + "事后回想多年之后。" * 4)
        rep = nd.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "DISTANCE_FLATTENED" not in codes
    finally:
        _set_mode(bak)


def test_tense_drift_no_declared_mixed_anchors():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        # concurrent 1 + recent 1 → top1=top2=1 比 <1.5 + 无 hindsight
        text = _BASE + "此刻他走着。刚才他停下。"
        rep = nd.scan(str(_write(text)))
        codes = [v["code"] for v in rep["violations"]]
        assert "DISTANCE_TENSE_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_no_declared_clean_dominant_anchor_pass():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        # 现在词大量主导
        text = _BASE + "此刻他走着。" * 10 + "现在他抬头。" * 10
        rep = nd.scan(str(_write(text)))
        assert "DISTANCE_TENSE_DRIFT" not in [v["code"] for v in rep["violations"]]
    finally:
        _set_mode(bak)


def test_declared_via_manifest_overrides_author():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(narrating_distance="concurrent")
        mf = _mk_manifest(declared_narrating_distance="distant")
        text = _BASE + "此刻他走着。" * 10 + "现在他抬头。" * 10
        rep = nd.scan(str(_write(text)), project_root=proj, manifest_path=str(mf))
        assert rep["declared_narrating_distance"] == "distant"
        assert "DISTANCE_FLATTENED" in [v["code"] for v in rep["violations"]]
    finally:
        _set_mode(bak)


def test_shadow_records_no_report():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(narrating_distance="distant")
        text = _BASE + "此刻他走着。" * 10 + "现在他抬头。" * 10
        rep = nd.scan(str(_write(text)), project_root=proj)
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("DISTANCE_TENSE_DRIFT", "DISTANCE_FLATTENED"):
        assert c not in hgs, c


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        os.environ["NARRATING_DISTANCE_MODE"] = "bogus"
        assert nd._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("active")
        rep = nd.scan(str(Path(tempfile.mkdtemp()) / "missing.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_posthumous_anchors_detected():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("shadow")
        text = _BASE + "他死后后世传到后人百年之后多年以后人们。" * 6
        rep = nd.scan(str(_write(text)))
        assert rep["tense_buckets"]["posthumous"]["count"] > 0
    finally:
        _set_mode(bak)


def test_atemporal_anchors_detected():
    bak = os.environ.get("NARRATING_DISTANCE_MODE")
    try:
        _set_mode("shadow")
        text = _BASE + "古时上古远古时代传说中相传不知何时。" * 6
        rep = nd.scan(str(_write(text)))
        assert rep["tense_buckets"]["atemporal"]["count"] > 0
    finally:
        _set_mode(bak)
