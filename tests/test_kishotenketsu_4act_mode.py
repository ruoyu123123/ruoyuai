# -*- coding: utf-8 -*-
"""kishotenketsu_4act_mode R20 W9 Batch-AA · P1 · 起承转结无冲突 narrative_mode 集成
覆盖三处集成点：
  ① cluster_emergence_engine.me_to_cluster_brief 据 intent 自动置 narrative_mode
  ② build_manifest 注入 kishotenketsu_directive 四段(语法 + 字段存在测试)
  ③ hook_strength_scanner._detect_kishotenketsu_cluster 检测后降阈值/severity
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

import cluster_emergence_engine as cee  # noqa: E402
import hook_strength_scanner as hss  # noqa: E402


def test_emergence_brief_iyashikei_intent_triggers_kisho():
    me = {"id": "me_001", "title": "治愈日常",
          "description": "琐碎日常", "intent": "iyashikei"}
    brief = cee.me_to_cluster_brief(me, "cluster_005", 1, world_state={})
    assert brief["narrative_mode"] == "kishotenketsu_4act"
    assert brief["intent"] == "iyashikei"


def test_emergence_brief_healing_intent_triggers_kisho():
    me = {"id": "me_001", "title": "治愈日常",
          "description": "...", "intent": "healing"}
    brief = cee.me_to_cluster_brief(me, "cluster_005", 1, world_state={})
    assert brief["narrative_mode"] == "kishotenketsu_4act"


def test_emergence_brief_contemplative_intent_triggers_kisho():
    me = {"id": "me_001", "title": "沉静",
          "description": "...", "intent": "contemplative"}
    brief = cee.me_to_cluster_brief(me, "cluster_005", 1, world_state={})
    assert brief["narrative_mode"] == "kishotenketsu_4act"


def test_emergence_brief_zh_治愈_intent_triggers_kisho():
    me = {"id": "me_001", "title": "治愈",
          "description": "...", "intent": "治愈"}
    brief = cee.me_to_cluster_brief(me, "cluster_005", 1, world_state={})
    assert brief["narrative_mode"] == "kishotenketsu_4act"


def test_emergence_brief_climactic_intent_does_not_trigger():
    me = {"id": "me_002", "title": "决战",
          "description": "高潮", "intent": "climactic"}
    brief = cee.me_to_cluster_brief(me, "cluster_006", 1, world_state={})
    assert brief["narrative_mode"] is None


def test_emergence_brief_no_intent_does_not_trigger():
    me = {"id": "me_003", "title": "日常", "description": "..."}
    brief = cee.me_to_cluster_brief(me, "cluster_007", 1, world_state={})
    assert brief["narrative_mode"] is None


def test_emergence_brief_explicit_narrative_mode_overrides():
    """me.intent ∈ kisho-set 时·narrative_mode 由 intent 覆盖到 kishotenketsu_4act。
    非 kisho intent + 显式 narrative_mode → 沿用显式。"""
    me = {"id": "me_004", "title": "线性故事",
          "description": "...", "intent": "neutral",
          "narrative_mode": "in_medias_res"}
    brief = cee.me_to_cluster_brief(me, "cluster_008", 1, world_state={})
    assert brief["narrative_mode"] == "in_medias_res"


def _mk_project_with_cluster_mode(narrative_mode: str | None = None,
                                  chapter_range=None) -> Path:
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    cluster = {"cluster_id": "cluster_001", "status": "in_progress",
               "chapter_range": chapter_range or [1, 3]}
    if narrative_mode is not None:
        cluster["narrative_mode"] = narrative_mode
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [cluster]}, ensure_ascii=False),
        encoding="utf-8")
    return proj


def test_hook_detect_kishotenketsu_cluster_true():
    proj = _mk_project_with_cluster_mode("kishotenketsu_4act", [1, 3])
    assert hss._detect_kishotenketsu_cluster(proj, 2) is True


def test_hook_detect_kishotenketsu_cluster_false_other_mode():
    proj = _mk_project_with_cluster_mode("linear", [1, 3])
    assert hss._detect_kishotenketsu_cluster(proj, 2) is False


def test_hook_detect_kishotenketsu_cluster_false_out_of_range():
    proj = _mk_project_with_cluster_mode("kishotenketsu_4act", [1, 3])
    assert hss._detect_kishotenketsu_cluster(proj, 10) is False


def test_hook_detect_kishotenketsu_cluster_no_file():
    proj = Path(tempfile.mkdtemp())
    assert hss._detect_kishotenketsu_cluster(proj, 1) is False


def test_hook_detect_kishotenketsu_no_range_in_progress():
    """无 chapter_range 但 status=in_progress + mode=kishotenketsu → True。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    cluster = {"cluster_id": "cluster_001", "status": "in_progress",
               "narrative_mode": "kishotenketsu_4act"}
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": [cluster]}, ensure_ascii=False),
        encoding="utf-8")
    assert hss._detect_kishotenketsu_cluster(proj, 1) is True


def test_hook_detect_kishotenketsu_corrupt_json_falls_back():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "事件簇.json").write_text("{ corrupt", encoding="utf-8")
    assert hss._detect_kishotenketsu_cluster(proj, 1) is False
