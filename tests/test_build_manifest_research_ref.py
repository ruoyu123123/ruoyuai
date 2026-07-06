#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""research_ref 在事件簇 -> manifest 注入链路中的回归锁。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402


def test_event_cluster_context_carries_research_ref():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "book"
        db = proj / "_数据库"
        db.mkdir(parents=True)
        ref = {
            "cache_path": "_数据库/.research_cache/inspiration_cluster_001_test.md",
            "anchors_used": ["anchor_A", "anchor_B"],
            "research_topics": ["悬疑开场", "时间线误导"],
            "researcher_confidence": 0.88,
        }
        (db / "事件簇.json").write_text(json.dumps({"clusters": [{
            "cluster_id": "cluster_001",
            "status": "in_progress",
            "chapter_range": [1, 3],
            "scope_summary": "开局事件块",
            "scene_storyboard": [{"ch": 1, "summary": "雨巷追问"}],
            "research_ref": ref,
        }]}, ensure_ascii=False), encoding="utf-8")

        ctx = bm._collect_event_cluster_context(bm.DatabaseScanner(proj, 1), 1)

        assert ctx["mode"] == "on"
        assert ctx["research_ref"] == ref

