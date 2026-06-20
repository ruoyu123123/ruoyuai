#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""antagonist_fidelity_scanner 测试 — flat anti-pattern 触发 / 干净 / shadow / 短稿 / 样本不足。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import antagonist_fidelity_scanner as mod  # noqa: E402

ENV = "ANTAGONIST_FIDELITY_MODE"


def _set_mode(v):
    old = os.environ.get(ENV)
    if v is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = v
    return old


def _restore(old):
    if old is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = old


def _write(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


FILLER = "夜里安静极了，墙上的旧钟一下一下地走，窗外的雨丝毫没有要停的意思，巷子空荡。" * 25


def test_off_returns_skeleton():
    old = _set_mode("off")
    try:
        p = _write(FILLER)
        rep = mod.scan(p)
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        os.unlink(p)
        _restore(old)


def test_short_draft_skipped():
    old = _set_mode("active")
    try:
        p = _write("短。")
        rep = mod.scan(p)
        assert rep["note"] == "草稿太短·跳过"
    finally:
        os.unlink(p)
        _restore(old)


def test_clean_draft_passes():
    """无反派 anti-pattern → PASS。"""
    old = _set_mode("active")
    try:
        clean = "他望着对方，淡淡地说了一句话。对方沉默良久，端起茶杯。" * 30
        p = _write(clean + FILLER)
        rep = mod.scan(p)
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
        assert rep["flat_antagonist_count"] == 0
    finally:
        os.unlink(p)
        _restore(old)


def test_flat_antagonist_triggers():
    """反派 anti-pattern 高密度 → active 触发。"""
    old = _set_mode("active")
    try:
        # 多处 anti-pattern + filler 让 per_1k > 1.0 且 cjk > 500
        bad = ("他冷哼一声，狞笑着站起。对方嘲讽道：你以为呢。狂笑一声响彻全场。"
               "他不屑地嗤笑了一下。咆哮道：去死吧。怒吼一声。") * 4
        p = _write(bad + FILLER)
        rep = mod.scan(p)
        assert rep["flat_antagonist_count"] >= mod.MIN_FLAT_HITS
        assert rep["per_1k"] > mod.FLAT_FLOOR_PER_1K, rep
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["warning"]
        assert rep["violations"][0]["kind"] == "antagonist_fidelity_flat"
    finally:
        os.unlink(p)
        _restore(old)


def test_low_count_not_judged():
    """命中数 < MIN_FLAT_HITS → 不判（PASS + note）。"""
    old = _set_mode("active")
    try:
        # 只一处 anti-pattern + 大量 filler
        only_one = "他冷哼一声。" + FILLER
        p = _write(only_one)
        rep = mod.scan(p)
        assert rep["flat_antagonist_count"] < mod.MIN_FLAT_HITS
        assert "样本不足" in rep.get("note", "")
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
    finally:
        os.unlink(p)
        _restore(old)


def test_shadow_records_no_violation():
    old = _set_mode("shadow")
    try:
        bad = ("他冷哼一声，狞笑着站起。对方嘲讽道：你以为呢。狂笑一声响彻全场。"
               "他不屑地嗤笑了一下。咆哮道：去死吧。怒吼一声。") * 4
        p = _write(bad + FILLER)
        rep = mod.scan(p)
        assert rep["mode"] == "shadow"
        assert rep["flat_antagonist_count"] >= mod.MIN_FLAT_HITS
        assert rep["violations"] == []
        assert rep["warning"] is None
    finally:
        os.unlink(p)
        _restore(old)


def test_registered_antagonists_reported():
    """读人物卡的 role=反派 角色名进 report。"""
    old = _set_mode("active")
    try:
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        (proj / "_数据库" / "人物卡.json").write_text(json.dumps({
            "characters": [
                {"name": "林尘", "role": "主角"},
                {"name": "黑袍", "role": "反派"},
                {"name": "魔尊", "role": "antagonist"},
            ]}, ensure_ascii=False), encoding="utf-8")
        p = _write(FILLER)
        rep = mod.scan(p, project_root=str(proj))
        assert rep["registered_antagonists"] == ["黑袍", "魔尊"]
    finally:
        os.unlink(p)
        _restore(old)
