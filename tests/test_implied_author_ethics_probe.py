# -*- coding: utf-8 -*-
"""implied_author_ethics_probe 专属测试 — Phelan 三轴伦理(advisory · 2026-06-20)

钉死：
  · asymmetric_screen_ratio ≥0.75 → SCREEN_ASYMMETRY
  · telling_intrusion_rate < floor → TELLING_THIN
  · narratee_address_density < floor → NARRATEE_THIN
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import implied_author_ethics_probe as ie  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, ethics_signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if ethics_signature is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"ethics_signature": ethics_signature},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _mk_manifest(**kwargs):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False)
    f.write(json.dumps(kwargs, ensure_ascii=False))
    f.close()
    return Path(f.name)


def _set_mode(m):
    if m is None:
        os.environ.pop("IMPLIED_AUTHOR_ETHICS_MODE", None)
    else:
        os.environ["IMPLIED_AUTHOR_ETHICS_MODE"] = m


_FILLER = "夜风扫过山脊石阶落满松针他独自向上踏步影子被拉得很长。" * 20


def test_off_returns_skeleton():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("off")
        rep = ie.scan(str(_write(_FILLER)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        rep = ie.scan(str(_write("反派出场。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_screen_asymmetry_when_antagonist_dominates():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        # 反派名字铺满,受害方只一处
        text = (_FILLER + "魔头登场。" * 25 + "魔头大笑。" * 25
                + "魔头出手。" * 25 + "受害者倒下。")
        mf = _mk_manifest(antagonist_cast=["魔头"], victim_cast=["受害者"])
        rep = ie.scan(str(_write(text)), manifest_path=str(mf))
        assert rep["asymmetric_screen_ratio"] >= 0.75
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_SCREEN_ASYMMETRY" in codes
    finally:
        _set_mode(bak)


def test_balanced_screen_ratio_no_violation():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        text = _FILLER + "魔头登场。" * 10 + "受害者反抗。" * 10
        mf = _mk_manifest(antagonist_cast=["魔头"], victim_cast=["受害者"])
        rep = ie.scan(str(_write(text)), manifest_path=str(mf))
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_SCREEN_ASYMMETRY" not in codes
    finally:
        _set_mode(bak)


def test_telling_intrusion_thin_below_floor():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ethics_signature={"intrusion_min": 0.5})
        rep = ie.scan(str(_write(_FILLER)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_TELLING_THIN" in codes
    finally:
        _set_mode(bak)


def test_narratee_address_thin_below_floor():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ethics_signature={"narratee_min": 0.3})
        rep = ie.scan(str(_write(_FILLER)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_NARRATEE_THIN" in codes
    finally:
        _set_mode(bak)


def test_narratee_floor_satisfied_pass():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ethics_signature={"narratee_min": 0.5})
        text = _FILLER + "各位读者请看。诸位看官请听。亲爱的读者你以为。" * 20
        rep = ie.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_NARRATEE_THIN" not in codes
    finally:
        _set_mode(bak)


def test_shadow_mode_no_report():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(ethics_signature={"intrusion_min": 5.0,
                                              "narratee_min": 5.0})
        rep = ie.scan(str(_write(_FILLER)), project_root=proj)
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("IMPLIED_AUTHOR_SCREEN_ASYMMETRY",
              "IMPLIED_AUTHOR_TELLING_THIN",
              "IMPLIED_AUTHOR_NARRATEE_THIN"):
        assert c not in hgs, c


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        os.environ["IMPLIED_AUTHOR_ETHICS_MODE"] = "bogus"
        assert ie._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        rep = ie.scan(str(Path(tempfile.mkdtemp()) / "missing.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_no_antagonist_cast_no_screen_check():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        # 无 manifest → 屏占检测不触发
        rep = ie.scan(str(_write(_FILLER)))
        assert rep["asymmetric_screen_ratio"] is None
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_SCREEN_ASYMMETRY" not in codes
    finally:
        _set_mode(bak)


def test_high_intrusion_pass():
    bak = os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(ethics_signature={"intrusion_min": 0.5})
        text = _FILLER + "不得不说且看他话说回来须知。" * 10
        rep = ie.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "IMPLIED_AUTHOR_TELLING_THIN" not in codes
    finally:
        _set_mode(bak)
