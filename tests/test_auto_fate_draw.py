#!/usr/bin/env python3
"""auto_fate_draw formal producer contract tests."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import auto_fate_draw as afd  # noqa: E402


def _project(events: list[dict]) -> Path:
    root = Path(tempfile.mkdtemp())
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件池.json").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    return root


def test_no_pool_writes_not_required_decision():
    root = Path(tempfile.mkdtemp())
    try:
        out = afd.auto_draw(root, 1, cluster_id="cluster_001")
        assert out["status"] == "not_required"
        assert "事件池" in out["reason"]
        decision = json.loads(afd.fate_draw_decision_path(root, 1).read_text(encoding="utf-8"))
        cluster_decision = json.loads(
            afd.cluster_fate_draw_decision_path(root, "cluster_001").read_text(encoding="utf-8"))
        assert decision["status"] == "not_required"
        assert cluster_decision["cluster_id"] == "cluster_001"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_existing_overlay_writes_not_required_decision_and_validates():
    root = _project([{"event_id": "ev_1", "narrative_seed": "种子"}])
    try:
        overlay = afd.fate_draw_overlay_path(root, 1)
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(json.dumps({"event": {"event_id": "ev_old"}}, ensure_ascii=False), encoding="utf-8")
        out = afd.auto_draw(root, 1)
        assert out["status"] == "not_required"
        assert out["event_id"] == "ev_old"
        decision = json.loads(afd.fate_draw_decision_path(root, 1).read_text(encoding="utf-8"))
        assert decision["status"] == "not_required"
        assert decision["event_id"] == "ev_old"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_draw_success_writes_overlay():
    root = _project([{
        "event_id": "ev_1",
        "label": "暴雨夜袭",
        "narrative_seed": "暴雨把证据冲到门口",
        "physical_evidence": ["泥水"],
    }])
    try:
        out = afd.auto_draw(root, 3)
        assert out["status"] == "drawn"
        overlay = json.loads(afd.fate_draw_overlay_path(root, 3).read_text(encoding="utf-8"))
        assert overlay["event"]["event_id"] == "ev_1"
        assert overlay["event"]["source"] == "fate_dice"
        assert overlay["raw_result"]["drawn"]["event_id"] == "ev_1"
        decision = json.loads(afd.fate_draw_decision_path(root, 3).read_text(encoding="utf-8"))
        assert decision["status"] == "drawn"
        assert decision["event_id"] == "ev_1"
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_malformed_pool_is_hard_error():
    root = Path(tempfile.mkdtemp())
    try:
        db = root / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "事件池.json").write_text("{bad", encoding="utf-8")
        try:
            afd.auto_draw(root, 1)
            assert False, "坏事件池必须硬失败"
        except RuntimeError as exc:
            assert "JSON" in str(exc)
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
