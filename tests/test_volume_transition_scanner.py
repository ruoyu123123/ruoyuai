#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""volume_transition_scanner 测试 — 三规则（钩零命中/cast硬重置/卷首空开）+ shadow + 边界。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))

import volume_transition_scanner as mod  # noqa: E402

ENV = "VOLUME_TRANSITION_MODE"


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


def _mk_project(shijianji=None, ledger=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps(shijianji or {"clusters": []}, ensure_ascii=False), encoding="utf-8")
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(ledger or {"clusters": []}, ensure_ascii=False), encoding="utf-8")
    return proj


def test_off_skeleton():
    old = _set_mode("off")
    try:
        proj = _mk_project()
        rep = mod.scan(proj)
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
        assert rep["issues"] == []
    finally:
        _restore(old)


def test_no_db_skips():
    old = _set_mode("active")
    try:
        proj = Path(tempfile.mkdtemp())  # 无 _数据库
        rep = mod.scan(proj)
        assert "无 _数据库" in rep.get("note", "")
        assert rep["issues"] == []
    finally:
        _restore(old)


def test_single_volume_skips():
    """只有一个卷·无过渡点·skip。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"]},
            ]})
        rep = mod.scan(proj)
        assert "卷数 < 2" in rep.get("note", "")
        assert rep["issues"] == []
    finally:
        _restore(old)


def test_hook_miss_triggers():
    """规则①：上卷末 hook_text 关键词在下卷首零兑现 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "林尘大战王虎",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒，吞噬星辰"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],   # 共享 cast 排除规则②触发
                 "scope_summary": "他在山间打坐修炼",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HOOK_MISS_CODE in codes, rep
        assert rep["close_hook_coverage"] < mod.HOOK_COVERAGE_FLOOR
    finally:
        _restore(old)


def test_hard_reset_triggers():
    """规则②：上下卷 cast overlap=0 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "决战古城"},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["陌生甲", "陌生乙"],   # 完全不重叠
                 "scope_summary": "新地点新人物",
                 "scene_storyboard": [{"summary": "陌生甲来到新的城里寻人"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HARD_RESET_CODE in codes, rep
        assert rep["cast_overlap_ratio"] == 0
    finally:
        _restore(old)


def test_clean_transition_passes():
    """完整 hook + cast 共享 + 新 setting 锚词 → PASS。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘", "王虎"],
                 "scope_summary": "黑龙苏醒在北境",
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙北境苏醒吞噬"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘", "新弟子"],  # 林尘延续
                 "scope_summary": "黑龙北境苏醒，林尘抵达新的城关",
                 "scene_storyboard": [{"summary": "林尘抵达新的城关，黑龙的影子掠过北境"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.HOOK_MISS_CODE not in codes
        assert mod.HARD_RESET_CODE not in codes
        # 有新 cast(新弟子) + setting 锚词「新的」「抵达」 → 规则③不触发
        assert mod.EMPTY_OPEN_CODE not in codes
    finally:
        _restore(old)


def test_shadow_records_no_issue():
    """shadow 高违规 → issues 不暴露 + 不上 verdict。"""
    old = _set_mode("shadow")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"],
                 "volume_transition_hooks": {
                     "close": {"hook_text": "黑龙将在北境苏醒"}
                 }},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["陌生甲"], "scope_summary": "他在山间打坐",
                 "scene_storyboard": [{"summary": "他在山间打坐修炼"}]},
            ]})
        rep = mod.scan(proj)
        assert rep["mode"] == "shadow"
        assert rep["issues"] == []   # shadow 不上报
        assert rep["warning"] is None
    finally:
        _restore(old)


def test_empty_open_triggers():
    """规则③：卷首 scene1 无新 cast 且无 setting 锚词 → 触发。"""
    old = _set_mode("active")
    try:
        proj = _mk_project(shijianji={
            "clusters": [
                {"cluster_id": "c1", "volume": 1, "status": "done",
                 "chapter_range": [1, 3], "cast": ["林尘"]},
                {"cluster_id": "c2", "volume": 2,
                 "cast": ["林尘"],  # 与上卷完全重合
                 "scope_summary": "林尘睡了一觉醒来吃了饭",
                 "scene_storyboard": [{"summary": "林尘睡了一觉醒来吃了饭"}]},
            ]})
        rep = mod.scan(proj)
        codes = [i["code"] for i in rep["issues"]]
        assert mod.EMPTY_OPEN_CODE in codes, rep
    finally:
        _restore(old)
