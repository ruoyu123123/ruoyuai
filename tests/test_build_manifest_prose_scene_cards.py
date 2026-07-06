#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_scene_cards 回归锁。

借鉴 moyin-creator 的 storyboard/scene card 思路，但在 ruoyuai 中只作为小说正文执行卡：
卡片化场景目标、阻力、转折、感官锚点；不透传 camera/shot/visual prompt 等影视字段。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402


def _write(db: Path, name: str, obj):
    db.mkdir(parents=True, exist_ok=True)
    (db / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_collect_prose_scene_cards_builds_prose_first_cards():
    cluster = {
        "cluster_id": "cluster_001",
        "scene_storyboard": [
            {
                "title": "钟楼试探",
                "ch": 1,
                "characters": ["池迟", "老钟"],
                "location": "废弃钟楼",
                "scene_goal": "套出钥匙下落",
                "conflict": "老钟只肯说半句",
                "disaster": "钟声暴露了池迟的位置",
                "key_events": ["池迟递出假证物", "老钟看向封死的楼梯"],
                "sensory_anchor": "潮湿铁锈味",
                "camera": "close-up",
                "shot": "wide shot",
                "visualPrompt": "cinematic camera angle",
            }
        ],
    }

    out = bm._collect_prose_scene_cards(cluster)

    assert out is not None
    card = out["cards"][0]
    assert card["title"] == "钟楼试探"
    assert card["scene_goal"] == "套出钥匙下落"
    assert card["pressure"] == "老钟只肯说半句"
    assert card["turn"] == "钟声暴露了池迟的位置"
    assert card["dramatic_question"] == "能否在老钟只肯说半句之下完成：套出钥匙下落？"
    assert card["sensory_anchors"] == ["潮湿铁锈味"]

    blob = json.dumps(out, ensure_ascii=False).lower()
    assert "camera" not in blob
    assert "close-up" not in blob
    assert "shot" not in blob
    assert "visualprompt" not in blob
    assert "镜头" not in blob


def test_collect_prose_scene_cards_default_safe_without_storyboard():
    assert bm._collect_prose_scene_cards(None) is None
    assert bm._collect_prose_scene_cards({}) is None
    assert bm._collect_prose_scene_cards({"cluster_id": "cluster_001"}) is None
    assert bm._collect_prose_scene_cards({"scene_storyboard": []}) is None
    assert bm._collect_prose_scene_cards({"scene_storyboard": [{}]}) is None


def test_event_cluster_context_carries_prose_scene_cards():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "book"
        db = proj / "_数据库"
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001",
            "status": "in_progress",
            "chapter_range": [1, 3],
            "scope_summary": "开局事件块",
            "scene_storyboard": [
                {
                    "title": "雨巷追问",
                    "ch": 1,
                    "focal_character": "池迟",
                    "scene_goal": "逼问同伴为何撒谎",
                    "conflict": "对方用旧案转移话题",
                    "key_events": ["伞沿滴水", "同伴说出错位时间"],
                }
            ],
        }]})

        ctx = bm._collect_event_cluster_context(bm.DatabaseScanner(proj, 1), 1)

        assert ctx.get("mode") == "on"
        cards = ctx.get("prose_scene_cards")
        assert cards is not None
        first = cards["cards"][0]
        assert first["title"] == "雨巷追问"
        assert first["focal_character"] == "池迟"
        assert first["dramatic_question"] == "能否在对方用旧案转移话题之下完成：逼问同伴为何撒谎？"


def test_subsystem_skeletons_document_prose_scene_card_fields():
    path = _ROOT / "core" / "claude-home" / "templates" / "subsystem_skeletons.json"
    skeletons = json.loads(path.read_text(encoding="utf-8"))

    hint = skeletons["skeletons"]["事件簇"]["_cluster_brief_schema_hint"]["prose_scene_card_fields"]

    assert "build_manifest._collect_prose_scene_cards" in hint["_doc"]
    assert hint["title"]
    assert hint["scene_title"]
    assert hint["dramatic_question"]
    assert hint["sensory_anchors"]
    assert "camera" in hint["_forbidden_visual_fields"]
    assert "shot" in hint["_forbidden_visual_fields"]
