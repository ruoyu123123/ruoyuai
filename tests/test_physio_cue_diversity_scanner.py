#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""physio_cue_diversity_scanner 测试 — 触发/干净/shadow零回归/短草稿skip。纯 stdlib。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import physio_cue_diversity_scanner as mod  # noqa: E402

ENV = "PHYSIO_CUE_DIVERSITY_MODE"

# 面部线索行：眉头/眼神/嘴角/脸色/额头 = 5 facial · 0 nonfacial
FACIAL_LINE = "他眉头紧蹙，眼神冰冷，嘴角抽动，脸色惨白，额头冒汗。"
# 非面部线索行：拳(头)/胸口/呼吸/肠胃 = 4 nonfacial · 0 facial
NONFACIAL_LINE = "他攥紧拳头，胸口起伏，呼吸粗重，肠胃翻搅。"
# 中性填充：不含任何面部/非面部线索词（仅用于把 CJK 顶过 500）
FILLER = "夜里安静极了，墙上的旧钟一下一下地走，窗外的雨丝毫没有要停的意思，巷子空荡。"


def _write(text: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


def _set_mode(value):
    old = os.environ.get(ENV)
    if value is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = value
    return old


def _restore(old):
    if old is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = old


def _facial_heavy_draft() -> str:
    # facial 20(=5×4) / nonfacial 4(=4×1) → ratio 0.83 > 0.55 · total 24 >= 8 · filler 顶过 500 CJK
    return FACIAL_LINE * 4 + NONFACIAL_LINE * 1 + FILLER * 15


def _balanced_clean_draft() -> str:
    # facial 5(=5×1) / nonfacial 16(=4×4) → ratio 0.238 <= 0.55 · total 21 >= 8 → PASS
    return FACIAL_LINE * 1 + NONFACIAL_LINE * 4 + FILLER * 15


def test_active_trigger_facial_bias():
    """面部偏置草稿 active 模式 → FAIL_MINOR + warning。"""
    old = _set_mode("active")
    try:
        path = _write(_facial_heavy_draft())
        rep = mod.scan(path)
        assert rep["mode"] == "active", rep
        assert rep["code"] == "PHYSIO_CUE_FACIAL_BIAS"
        assert rep["gate_level"] == "advisory"
        assert rep["cue_total"] >= 8, rep
        assert rep["facial_ratio"] > mod.FACIAL_RATIO_FLOOR, rep
        assert rep["verdict"] == "FAIL_MINOR", rep
        assert rep["warning"], rep
        assert rep["violations_count"] == 1
        assert rep["violations"][0]["kind"] == "physio_cue_facial_bias"
    finally:
        os.unlink(path)
        _restore(old)


def test_active_clean_passes():
    """非面部线索为主的草稿 → PASS · 无 violations。"""
    old = _set_mode("active")
    try:
        path = _write(_balanced_clean_draft())
        rep = mod.scan(path)
        assert rep["cue_total"] >= 8, rep
        assert rep["facial_ratio"] <= mod.FACIAL_RATIO_FLOOR, rep
        assert rep["verdict"] == "PASS", rep
        assert rep["warning"] is None, rep
        assert rep["violations"] == []
    finally:
        os.unlink(path)
        _restore(old)


def test_shadow_no_report_zero_regression():
    """shadow 模式即便面部偏置命中也不上报（violations 空·verdict PASS·零回归）。"""
    old = _set_mode("shadow")
    try:
        path = _write(_facial_heavy_draft())
        rep = mod.scan(path)
        assert rep["mode"] == "shadow", rep
        assert rep["facial_ratio"] > mod.FACIAL_RATIO_FLOOR, rep  # 确实命中阈值
        assert rep["violations"] == [], rep
        assert rep["verdict"] == "PASS", rep
        assert rep["warning"] is None, rep
        assert rep["violations_count"] == 0
    finally:
        os.unlink(path)
        _restore(old)


def test_short_draft_skipped():
    """草稿 < 500 CJK → skip（note·不判·PASS）。"""
    old = _set_mode("active")
    try:
        path = _write(FACIAL_LINE * 2)  # ~42 CJK < 500
        rep = mod.scan(path)
        assert "note" in rep and "跳过" in rep["note"], rep
        assert rep["verdict"] == "PASS", rep
        assert rep["violations"] == []
    finally:
        os.unlink(path)
        _restore(old)


def test_off_mode_returns_skeleton():
    """off 模式直接返回骨架。"""
    old = _set_mode("off")
    try:
        path = _write(_facial_heavy_draft())
        rep = mod.scan(path)
        assert rep["mode"] == "off", rep
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
        assert "facial_ratio" not in rep  # off 不计算
    finally:
        os.unlink(path)
        _restore(old)


def test_insufficient_samples_not_judged():
    """生理线索样本不足（<8）→ 不判（note·PASS）。"""
    old = _set_mode("active")
    try:
        # 仅 1 行面部(5 facial) + 大量 filler 顶过 500 CJK · total 5 < 8 → 样本不足
        path = _write(FACIAL_LINE * 1 + FILLER * 20)
        rep = mod.scan(path)
        assert rep["cue_total"] < mod.MIN_CUE_SAMPLES, rep
        assert rep["facial_ratio"] is None, rep
        assert "样本不足" in rep.get("note", ""), rep
        assert rep["verdict"] == "PASS", rep
        assert rep["violations"] == []
    finally:
        os.unlink(path)
        _restore(old)
