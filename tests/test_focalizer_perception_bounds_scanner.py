#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""focalizer_perception_bounds_scanner 测试 — 三规则触发 / 干净 / shadow / 短稿。零依赖确定性。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import focalizer_perception_bounds_scanner as mod  # noqa: E402

ENV = "FOCALIZER_PERCEPTION_BOUNDS_MODE"


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


def _mk_project(*, protag="林尘", others=("王虎", "孙明")):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    chars = [{"name": protag, "role": "主角"}]
    for n in others:
        chars.append({"name": n, "role": "配角"})
    (proj / "_数据库" / "人物卡.json").write_text(
        json.dumps({"characters": chars}, ensure_ascii=False), encoding="utf-8")
    return proj


FILLER = "夜里安静极了，墙上的旧钟一下一下地走，窗外的雨丝毫没有要停的意思，巷子空荡。" * 25


def test_off_returns_skeleton():
    old = _set_mode("off")
    try:
        p = _write(FILLER)
        rep = mod.scan(p)
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
    finally:
        os.unlink(p)
        _restore(old)


def test_short_draft_skipped():
    old = _set_mode("active")
    try:
        p = _write("短稿。")
        rep = mod.scan(p)
        assert rep["note"] == "草稿太短·跳过"
        assert rep["verdict"] == "PASS"
    finally:
        os.unlink(p)
        _restore(old)


def test_clean_focalizer_passes():
    """无三规则违例 → PASS。"""
    old = _set_mode("active")
    try:
        proj = _mk_project()
        clean = "他握紧剑柄，听见远处的钟声。" * 30
        p = _write(clean + FILLER)
        rep = mod.scan(p, project_root=proj)
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
        assert rep["self_invisible_per_1k"] == 0
        assert rep["others_inner_per_1k"] == 0
    finally:
        os.unlink(p)
        _restore(old)


def test_self_invisible_violation_triggers():
    """规则①：自体不可见词高密度 → active 触发 FAIL_MINOR。"""
    old = _set_mode("active")
    try:
        proj = _mk_project()
        # 自体不可见词高密度：每行 1 处·重复 8 次 ≈ 8 处 self_invisible·>0.5/千字
        bad = "他看见自己的眼神空洞，脸上挂着自己的脸色。" * 8
        p = _write(bad + FILLER)
        rep = mod.scan(p, project_root=proj)
        assert rep["self_invisible_per_1k"] > mod.SELF_INVISIBLE_FLOOR_PER_1K, rep
        assert rep["verdict"] == "FAIL_MINOR", rep
        assert rep["warning"], rep
        assert any("自体不可见" in v["message"] for v in rep["violations"])
    finally:
        os.unlink(p)
        _restore(old)


def test_others_inner_violation_triggers():
    """规则②：他人内心动词高密度 → active 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project()
        # 王虎/孙明 是注册他人·内心动词紧邻
        bad = "王虎心想这事不简单。孙明暗道时机已到。王虎暗忖必须出手。" * 8
        p = _write(bad + FILLER)
        rep = mod.scan(p, project_root=proj)
        assert rep["others_inner_per_1k"] > mod.OTHERS_INNER_FLOOR_PER_1K, rep
        assert rep["verdict"] == "FAIL_MINOR"
        assert any("他人内心" in v["message"] for v in rep["violations"])
    finally:
        os.unlink(p)
        _restore(old)


def test_spatial_absence_violation_triggers():
    """规则③：空间不在场标志命中 ≥ 2 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project()
        bad = "与此同时，在远处，烽火点燃了山头。同一时刻，远处的城里，刺客已动身。"
        p = _write(bad + FILLER)
        rep = mod.scan(p, project_root=proj)
        assert rep["spatial_absence_count"] >= mod.SPATIAL_ABSENCE_HIT_FLOOR, rep
        assert rep["verdict"] == "FAIL_MINOR"
        assert any("空间不在场" in v["message"] for v in rep["violations"])
    finally:
        os.unlink(p)
        _restore(old)


def test_shadow_records_no_violation():
    """shadow 高密度命中 → 不上报 violation。"""
    old = _set_mode("shadow")
    try:
        proj = _mk_project()
        bad = "他看见自己的眼神空洞，自己的脸色苍白。" * 10
        p = _write(bad + FILLER)
        rep = mod.scan(p, project_root=proj)
        assert rep["mode"] == "shadow"
        assert rep["self_invisible_per_1k"] > 0, rep
        assert rep["violations"] == []
        assert rep["warning"] is None
        assert rep["verdict"] == "PASS"
    finally:
        os.unlink(p)
        _restore(old)


def test_focalizer_self_inner_not_misflagged():
    """聚焦人自己的心想不算他人内心越界（防止主角被误抓）。"""
    old = _set_mode("active")
    try:
        proj = _mk_project()
        clean = "林尘心想这事必须办妥。林尘心想王虎肯定会反对。" * 10
        p = _write(clean + FILLER)
        rep = mod.scan(p, project_root=proj)
        # 林尘自己 心想 不计入 others_inner
        assert rep["others_inner_per_1k"] == 0, rep
    finally:
        os.unlink(p)
        _restore(old)


def test_unregistered_name_ignored():
    """未注册角色名前缀『时候/地方』+ 想 不应被误抓为 others_inner。"""
    old = _set_mode("active")
    try:
        proj = _mk_project()
        # 时候/地方 不在 character set·OTHERS_INNER 应过滤
        clean = "他到的时候想了想，找了个地方想了一下。" * 20
        p = _write(clean + FILLER)
        rep = mod.scan(p, project_root=proj)
        assert rep["others_inner_per_1k"] == 0, rep
    finally:
        os.unlink(p)
        _restore(old)
