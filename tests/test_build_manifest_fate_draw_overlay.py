#!/usr/bin/env python3
"""build_manifest merges auto_fate_draw overlay into active_fate_events."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_manifest as bm  # noqa: E402


class _Scanner:
    def __init__(self, root: Path, cluster_id: str = "cluster_001"):
        self.root = root
        self.cluster_id = cluster_id

    def _current_cluster_id(self):
        return self.cluster_id


def _write_pool(root: Path):
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件池.json").write_text(
        json.dumps({"events": [{"event_id": "ev_1", "status": "available"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_decision(root: Path, chapter: int, status: str = "drawn"):
    path = root / "_数据库" / ".manifest" / f"ch_{chapter:03d}_fate_draw_decision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "_schema": "fate_draw_decision_v1",
            "chapter": chapter,
            "producer": "auto_fate_draw.py",
            "status": status,
            "reason": "测试",
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_overlay_merges_without_dashishi():
    root = Path(tempfile.mkdtemp())
    try:
        overlay_dir = root / "_数据库" / ".manifest"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "ch_001_fate_draw.json").write_text(
            json.dumps({"event": {"event_id": "ev_1", "narrative_seed": "钩子"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        out = bm._collect_active_fate_events(_Scanner(root), 1)
        assert out["mode"] == "fluid"
        assert out["active"][0]["event_id"] == "ev_1"
        assert out["active"][0]["source"] == "fate_dice"
        assert "命运抽签" in out["_note"]
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_bad_overlay_is_hard_error():
    root = Path(tempfile.mkdtemp())
    try:
        overlay_dir = root / "_数据库" / ".manifest"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "ch_001_fate_draw.json").write_text(
            json.dumps({"event": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            bm._collect_active_fate_events(_Scanner(root), 1)
            assert False, "坏 overlay 必须硬失败"
        except RuntimeError as exc:
            assert "active_fate_events" in str(exc)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_major_event_guidance_uses_current_cluster_not_chapter_argument():
    root = Path(tempfile.mkdtemp())
    try:
        db = root / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "大势卡.json").write_text(json.dumps({"major_events": [
            {"id": "ME-V1-01", "title": "导火索", "status": "completed",
             "completed_at_cluster": "cluster_001", "prerequisites": [],
             "expected_window_after": None},
            {"id": "ME-V1-02", "title": "反击", "status": "pending",
             "prerequisites": ["ME-V1-01"],
             "expected_window_after": {"event": "ME-V1-01", "max_clusters": 1}},
        ]}, ensure_ascii=False), encoding="utf-8")
        out = bm._collect_active_fate_events(_Scanner(root, "cluster_003"), 99)
        assert out["active"][0]["id"] == "ME-V1-02"
        assert out["overdue"][0]["current_cluster"] == "cluster_003"
        assert out["total_pending"] == 1
        assert "total_scheduled" not in out
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_fate_dice_hint_requires_decision_when_pool_exists():
    root = Path(tempfile.mkdtemp())
    try:
        _write_pool(root)
        try:
            bm._collect_fate_dice_hint(_Scanner(root), 1)
            assert False, "事件池存在时缺 decision 必须硬失败"
        except RuntimeError as exc:
            assert "decision artifact" in str(exc)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_fate_dice_hint_accepts_not_required_decision():
    root = Path(tempfile.mkdtemp())
    try:
        _write_pool(root)
        _write_decision(root, 1, status="not_required")
        out = bm._collect_fate_dice_hint(_Scanner(root), 1)
        assert out["mode"] == "not_required"
        assert out["decision"]["status"] == "not_required"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_overlay_requires_drawn_decision():
    root = Path(tempfile.mkdtemp())
    try:
        _write_pool(root)
        _write_decision(root, 1, status="not_required")
        overlay_dir = root / "_数据库" / ".manifest"
        (overlay_dir / "ch_001_fate_draw.json").write_text(
            json.dumps({"event": {"event_id": "ev_1"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            bm._collect_fate_dice_hint(_Scanner(root), 1)
            assert False, "overlay 存在但 decision 非 drawn 必须硬失败"
        except RuntimeError as exc:
            assert "status" in str(exc)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for name in sorted(globals()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                print(f"[OK] {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                import traceback
                print(f"[FAIL] {name}: {exc}")
                traceback.print_exc()
    raise SystemExit(1 if fails else 0)
