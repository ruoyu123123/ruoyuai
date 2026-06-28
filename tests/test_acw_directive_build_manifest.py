# -*- coding: utf-8 -*-
"""ACW directive 集成测试 (R20 W9 Batch-CC · P2)
覆盖：env ACW_MODE=off/shadow/active 时 build_manifest._collect_event_cluster_context
是否注入 ACW_DIRECTIVE 字段。
确定性·零依赖·零 LLM/零联网。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import build_manifest as bm  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("ACW_MODE", None)
    else:
        os.environ["ACW_MODE"] = m


def _mk_project_with_cluster() -> Path:
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    # 🔴 2026-06-27 C15: active cluster 必须带非空 brief（真实 cluster 总有 scope_summary +
    # scene_storyboard）·否则 build_manifest C15 注入契约校验会判 contract_violation。
    # 本 fixture 测的是 ACW directive 注入·补最小 brief 让 cluster 合法（mode=on 才有 ACW_DIRECTIVE）。
    cluster = {"cluster_id": "cluster_001", "status": "in_progress",
               "chapter_range": [1, 3], "scope_summary": "测试用故事块简述",
               "scene_storyboard": [{"ch": 1, "title": "场景", "key_events": ["事件"]}]}
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [cluster]}, ensure_ascii=False),
        encoding="utf-8")
    return proj


class _Scanner:
    def __init__(self, root):
        self.root = root
    def load(self, name, default=None):
        p = self.root / "_数据库" / f"{name}.json"
        if not p.exists():
            return default if default is not None else {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default if default is not None else {}
    def user_preferences_v21(self):
        return {}


def test_acw_directive_off_returns_none():
    bak = os.environ.get("ACW_MODE")
    try:
        _set_mode("off")
        proj = _mk_project_with_cluster()
        sc = _Scanner(proj)
        out = bm._collect_event_cluster_context(sc, 1)
        assert out["mode"] == "on"
        assert out.get("ACW_DIRECTIVE") is None
    finally:
        _set_mode(bak)


def test_acw_directive_shadow_returns_none():
    bak = os.environ.get("ACW_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_cluster()
        sc = _Scanner(proj)
        out = bm._collect_event_cluster_context(sc, 1)
        assert out["mode"] == "on"
        assert out.get("ACW_DIRECTIVE") is None  # shadow 不注入
    finally:
        _set_mode(bak)


def test_acw_directive_active_returns_string():
    bak = os.environ.get("ACW_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_cluster()
        sc = _Scanner(proj)
        out = bm._collect_event_cluster_context(sc, 1)
        assert out["mode"] == "on"
        d = out.get("ACW_DIRECTIVE")
        assert isinstance(d, str)
        assert "ACW" in d
        assert "Activity-Centric" in d
        # 含「中心活动」「主语」核心关键词
        assert "中心活动" in d
        assert "主语" in d
    finally:
        _set_mode(bak)


def test_acw_directive_default_is_shadow():
    bak = os.environ.get("ACW_MODE")
    try:
        _set_mode(None)  # 不设 env
        proj = _mk_project_with_cluster()
        sc = _Scanner(proj)
        out = bm._collect_event_cluster_context(sc, 1)
        # 默认 shadow → 不注入
        assert out.get("ACW_DIRECTIVE") is None
    finally:
        _set_mode(bak)


def test_acw_directive_field_always_present():
    """注入字段名 ACW_DIRECTIVE 必须存在(值可为 None)·writer 模板期待该 key。"""
    bak = os.environ.get("ACW_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_cluster()
        sc = _Scanner(proj)
        out = bm._collect_event_cluster_context(sc, 1)
        assert "ACW_DIRECTIVE" in out  # key 必须存在
    finally:
        _set_mode(bak)
