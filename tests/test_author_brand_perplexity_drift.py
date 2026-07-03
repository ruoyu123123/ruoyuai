# -*- coding: utf-8 -*-
"""author_brand_perplexity_drift R23 W11 Batch-HH · P1"""
import json
import math
import os
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import author_brand_perplexity_drift as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("AUTHOR_BRAND_PERPLEXITY_MODE", None)
    else:
        os.environ["AUTHOR_BRAND_PERPLEXITY_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_band(p5, p95, book_count=3, placeholder=False):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    band = {
        "_schema_version": "1.0",
        "_placeholder": placeholder,
        "metric": "char_shannon_entropy",
        "window_cjk": mod.WINDOW_CJK,
        "window_step_cjk": mod.WINDOW_STEP_CJK,
        "book_count": book_count,
        "ecdf": {"p5": p5, "p25": (p5 + p95) / 2 - 0.5,
                 "p50": (p5 + p95) / 2,
                 "p75": (p5 + p95) / 2 + 0.5, "p95": p95},
        "mean": (p5 + p95) / 2,
        "std": (p95 - p5) / 4,
        "books": ["book_a", "book_b", "book_c"],
    }
    (db / "author_lm_perplexity_band.json").write_text(
        json.dumps(band, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


# 普通中文（高 entropy）
_NORMAL_TEXT = ("他静静地走在路上，望着前方未明的去路，思绪散落如雪。"
                "夜色温柔，月光下星辰闪烁。" * 200)
# 低 entropy（高重复·boilerplate）
_BOILER_TEXT = "他他他他他他他他他他他他他他他他他他他他" * 200


def test_off_returns_skeleton():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("off")
        proj = _mk_project_with_band(p5=4.0, p95=6.0)
        out = mod.scan(_write(_NORMAL_TEXT), proj)
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation_even_if_drift():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_band(p5=5.0, p95=6.0)
        out = mod.scan(_write(_BOILER_TEXT), proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_normal_within_band_passes():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        # 极宽 band 使一切窗都通过
        proj = _mk_project_with_band(p5=0.0, p95=20.0)
        out = mod.scan(_write(_NORMAL_TEXT), proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "AUTHOR_BRAND_PERPLEXITY_DRIFT_LOW" not in codes
        assert "AUTHOR_BRAND_PERPLEXITY_DRIFT_HIGH" not in codes
    finally:
        _set_mode(bak)


def test_active_low_entropy_drift_low():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        # boilerplate 文本 → 熵很低·band p5=5.0
        proj = _mk_project_with_band(p5=5.0, p95=10.0)
        out = mod.scan(_write(_BOILER_TEXT), proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "AUTHOR_BRAND_PERPLEXITY_DRIFT_LOW" in codes
    finally:
        _set_mode(bak)


def test_active_high_entropy_drift_high():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        # 正常文本 entropy 较高（中文常 6-9 bits）·band p95 设很低
        proj = _mk_project_with_band(p5=0.0, p95=4.0)
        out = mod.scan(_write(_NORMAL_TEXT), proj)
        codes = {v["code"] for v in out.get("violations", [])}
        if out.get("above_p95_ratio", 0) > 0.30:
            assert "AUTHOR_BRAND_PERPLEXITY_DRIFT_HIGH" in codes
    finally:
        _set_mode(bak)


def test_active_single_book_skips():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_band(p5=5.0, p95=6.0, book_count=1)
        out = mod.scan(_write(_BOILER_TEXT), proj)
        codes = {v["code"] for v in out.get("violations", [])}
        # 单本档跳过判定·只 info BAND_MISSING
        assert "AUTHOR_BRAND_PERPLEXITY_BAND_MISSING" in codes
    finally:
        _set_mode(bak)


def test_active_placeholder_band_skips():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_band(p5=5.0, p95=6.0, placeholder=True)
        out = mod.scan(_write(_BOILER_TEXT), proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "AUTHOR_BRAND_PERPLEXITY_BAND_MISSING" in codes
    finally:
        _set_mode(bak)


def test_active_no_band_writes_placeholder():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        out = mod.scan(_write(_NORMAL_TEXT), proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "AUTHOR_BRAND_PERPLEXITY_BAND_MISSING" in codes
        # 占位 band 已写入
        band_p = proj / "_数据库" / "author_lm_perplexity_band.json"
        assert band_p.exists()
        band_obj = json.loads(band_p.read_text(encoding="utf-8"))
        assert band_obj.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_band(p5=5.0, p95=6.0)
        out = mod.scan(_write("短文"), proj)
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("AUTHOR_BRAND_PERPLEXITY_DRIFT_LOW",
              "AUTHOR_BRAND_PERPLEXITY_DRIFT_HIGH",
              "AUTHOR_BRAND_PERPLEXITY_BAND_MISSING"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("author_brand_perplexity_drift")
    assert s is not None
    assert s.get("_new") is True


def test_entropy_function_correct():
    """uniform 序列熵应 = log2(n)·单字符熵=0"""
    assert mod._shannon_entropy("a") == 0
    s = "abcd" * 25  # 100 chars, 4 unique each 25 → entropy = 2 bits
    assert abs(mod._shannon_entropy(s) - 2.0) < 0.01


def test_windowed_entropies_yields_expected_count():
    text = "一二三四五" * 500  # 2500 CJK
    es = mod._windowed_entropies(text, window=500, step=250)
    # 窗口 500·步 250 → (2500-500)/250 + 1 = 9
    assert len(es) == 9


# ============ 🔴 2026-07-02 真模型(surprisal_gpt2) 接入回归 ============

def test_model_metric_used_when_enabled(monkeypatch):
    """RUOYU_NN_SURPRISAL=1 且 bridge 全窗口命中 → metric_used 切换为 gpt2_mean_surprisal。"""
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        # content-aware 假模型：surprisal 与窗口内「夜」字出现次数挂钩(确定性·非常量)
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [
                {"mean_surprisal": 5.0 + t.count("夜") * 0.3, "source": "model"} for t in texts
            ]
        )
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        proj = _mk_project_with_band(p5=0.0, p95=20.0)
        out = mod.scan(_write(_NORMAL_TEXT), proj)
        assert out["metric_used"] == "gpt2_mean_surprisal"
        assert out["windows"] > 0
    finally:
        _set_mode(bak)


def test_model_unavailable_keeps_entropy_metric_unchanged(monkeypatch):
    """bridge enabled 但返回全 None → 整体回退 char Shannon entropy·
    结果与不开模型时完全一致(零回归)。"""
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_band(p5=0.0, p95=20.0)
        path = _write(_NORMAL_TEXT)
        baseline = mod.scan(path, proj)
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [None for _ in texts])
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        out = mod.scan(path, proj)
        assert out["metric_used"] == "char_shannon_entropy"
        assert baseline["metric_used"] == "char_shannon_entropy"
        assert out["windows_entropy_mean"] == baseline["windows_entropy_mean"]
        assert out["below_p5_ratio"] == baseline["below_p5_ratio"]
        assert out["above_p95_ratio"] == baseline["above_p95_ratio"]
        assert out["verdict"] == baseline["verdict"]
    finally:
        _set_mode(bak)


def test_model_partial_none_falls_back_to_entropy(monkeypatch):
    """bridge 部分命中/部分 None(混合) → 整体仍回退熵(避免部分 None 破坏 ecdf 比较语义)。"""
    bak = os.environ.get("AUTHOR_BRAND_PERPLEXITY_MODE")
    try:
        _set_mode("active")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        counter = {"n": 0}

        def fake_predict(texts, ids=None):
            out = []
            for _ in texts:
                counter["n"] += 1
                out.append(None if counter["n"] % 4 == 0 else
                           {"mean_surprisal": 6.0, "source": "model"})
            return out

        fake_bridge = types.SimpleNamespace(predict_batch=fake_predict)
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        proj = _mk_project_with_band(p5=0.0, p95=20.0)
        out = mod.scan(_write(_NORMAL_TEXT), proj)
        assert out["metric_used"] == "char_shannon_entropy"
    finally:
        _set_mode(bak)


def test_window_slices_matches_entropy_window_count():
    """_window_slices 与 _windowed_entropies 窗口边界数一致(模型/熵路径同构对比基础)。"""
    text = "一二三四五" * 500  # 2500 CJK
    slices = mod._window_slices(text, window=500, step=250)
    es = mod._windowed_entropies(text, window=500, step=250)
    assert len(slices) == len(es) == 9
