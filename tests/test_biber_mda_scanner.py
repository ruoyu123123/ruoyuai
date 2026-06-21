# -*- coding: utf-8 -*-
"""biber_mda_scanner R20 W9 Batch-AA · P1 · Biber MDA 4 维中文映射
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
import biber_mda_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "biber_mda_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("BIBER_MDA_MODE", None)
    else:
        os.environ["BIBER_MDA_MODE"] = m


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
            json.dumps({"biber_mda_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 高 D1(涉入度)·1/2 人称 + 私人动词 + 情态副词密集
_HIGH_D1 = (
    "我觉得你也许并不知道这一切。我记得你说过那句话。我想或许你能明白。"
    "你大概不记得了。我们恐怕没法回头。咱们也许该走了。"
) * 20

# 高 D2(叙事关切)·过去时 + 3 人称 + 行动动词密集
_HIGH_D2 = (
    "他走了过去。她跑了出来。他抓了她的手。她打了他一拳。他喊了一声。"
    "她推了门。他拉了她回去。它扔了那把刀。他踢了石头。"
) * 20

# 中性长文(>500 CJK 但各维标记词都偏少)
_NEUTRAL = "夜里很静。风停了。月亮升上来。星光稀薄。墙后的影子安静。" * 30


def test_off_returns_skeleton():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_HIGH_D1), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "z_scores" not in out
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_HIGH_D1), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_high_d1_drift():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HIGH_D1), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        assert "BIBER_MDA_DRIFT_D1" in codes
        assert out["z_scores"]["D1"] > 1.0
    finally:
        _set_mode(bak)


def test_active_high_d2_drift():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HIGH_D2), _mk_project())
        codes = {f["code"] for f in out.get("flags", [])}
        # D2 应当偏高(叙事关切高密)
        assert "BIBER_MDA_DRIFT_D2" in codes or out["z_scores"]["D2"] > 0
    finally:
        _set_mode(bak)


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("active")
        # 作者档极度宽松基线(mean 设大 std 设大) → 一切 z 在 ±1 内
        baseline = {
            "D1": {"mean": 500.0, "std": 1000.0},
            "D2": {"mean": 500.0, "std": 1000.0},
            "D3": {"mean": 500.0, "std": 1000.0},
            "D4": {"mean": 500.0, "std": 1000.0},
        }
        out = mod.scan(_write(_HIGH_D1), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        codes = {f["code"] for f in out.get("flags", [])}
        # 极宽 → 全 z 应接近 -1 至 0 → 不报
        assert "BIBER_MDA_DRIFT_D1" not in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("我觉得。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("BIBER_MDA_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_count_terms_basic():
    assert mod._count_terms("我我我", ["我"]) == 3


def test_z_function():
    assert mod._z(10.0, 0.0, 2.0) == 5.0
    assert mod._z(0.0, 0.0, 0.0) == 0.0  # std=0 → 0


def test_dim_density_returns_breakdown():
    pk, bd = mod._dim_density("我我我", mod.D1_MARKERS, 3)
    assert "pronouns_1_2" in bd
    assert bd["pronouns_1_2"] == 3


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "BIBER_MDA_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_HIGH_D1)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "biber_mda"
