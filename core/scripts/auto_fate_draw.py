"""auto_fate_draw.py - cluster-write step1 fate-dice state producer.

This script runs before build_manifest in the single creation chain.  It is a
deterministic state producer, not an advisory hint:

- no event pool / no available event / already active fate event: exit 0 with
  a structured decision artifact
- available pool but draw returns no candidate: exit 1
- malformed pool, malformed prior overlay, or draw/write failure: exit 2

When a draw succeeds it writes
`_数据库/.manifest/ch_<NNN>_fate_draw.json`.  build_manifest must then merge that
overlay into `active_fate_events.active` so the writer receives the seed through
the normal manifest path.  Every successful run also writes
`_数据库/.manifest/ch_<NNN>_fate_draw_decision.json`, so cluster-write step1 has a
verifiable output even when the correct decision is "no draw".
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from atomic_json import atomic_write_json
except ImportError:  # pragma: no cover - direct copy outside scripts dir
    def atomic_write_json(target: Path, data: dict, indent: int = 2, ensure_ascii: bool = False) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=ensure_ascii, indent=indent), encoding="utf-8")


def _db(project_root: Path) -> Path:
    return project_root / "_数据库"


def fate_draw_overlay_path(project_root: Path, chapter: int) -> Path:
    return _db(project_root) / ".manifest" / f"ch_{chapter:03d}_fate_draw.json"


def fate_draw_decision_path(project_root: Path, chapter: int) -> Path:
    return _db(project_root) / ".manifest" / f"ch_{chapter:03d}_fate_draw_decision.json"


def cluster_fate_draw_decision_path(project_root: Path, cluster_id: str) -> Path:
    return _db(project_root) / ".manifest" / f"{cluster_id}_fate_draw_decision.json"


def _load_json_strict(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} JSON 解析失败: {exc}") from exc


def _event_id(event: dict) -> str:
    return str(event.get("event_id") or event.get("id") or "")


def _available_events(pool: dict) -> list[dict]:
    events = pool.get("events", pool.get("pool", []))
    if events is None:
        return []
    if not isinstance(events, list):
        raise RuntimeError("事件池.json 的 events/pool 必须是列表")
    bad = [type(e).__name__ for e in events if not isinstance(e, dict)]
    if bad:
        raise RuntimeError(f"事件池.json 含非对象事件条目: {bad[:3]}")
    return [e for e in events if e.get("status", "available") == "available"]


def _overlay_event(overlay: dict) -> dict | None:
    event = overlay.get("event")
    return event if isinstance(event, dict) and _event_id(event) else None


def _manifest_has_active_fate_event(manifest: dict) -> bool:
    active_fate = manifest.get("active_fate_events")
    if isinstance(active_fate, list):
        return bool(active_fate)
    if isinstance(active_fate, dict):
        active = active_fate.get("active")
        return isinstance(active, list) and bool(active)
    return bool(active_fate)


def draw_decision(project_root: Path, chapter: int) -> dict:
    """Return whether step1 must draw, with strict validation for existing state."""
    db = _db(project_root)
    pool_path = db / "事件池.json"
    if not pool_path.exists():
        return {"should_draw": False, "reason": "事件池.json 不存在"}

    pool = _load_json_strict(pool_path)
    if not isinstance(pool, dict):
        raise RuntimeError("事件池.json 顶层必须是对象")
    available = _available_events(pool)
    if not available:
        return {"should_draw": False, "reason": "事件池无 available 事件"}

    overlay_path = fate_draw_overlay_path(project_root, chapter)
    if overlay_path.exists():
        overlay = _load_json_strict(overlay_path)
        if not isinstance(overlay, dict):
            raise RuntimeError(f"{overlay_path} 顶层必须是对象")
        event = _overlay_event(overlay)
        if event:
            return {
                "should_draw": False,
                "reason": "已有 fate_draw overlay",
                "event_id": _event_id(event),
            }
        raise RuntimeError(f"{overlay_path} 存在但缺少有效 event.event_id")

    manifest_path = db / ".manifest" / f"ch_{chapter:03d}.json"
    if manifest_path.exists():
        manifest = _load_json_strict(manifest_path)
        if not isinstance(manifest, dict):
            raise RuntimeError(f"{manifest_path} 顶层必须是对象")
        if _manifest_has_active_fate_event(manifest):
            return {"should_draw": False, "reason": "manifest 已有 active_fate_events"}

    return {
        "should_draw": True,
        "reason": "事件池有 available 事件且本章未绑定 fate event",
        "available_count": len(available),
    }


def _normalize_drawn_event(result: dict) -> dict | None:
    drawn = result.get("drawn")
    if not isinstance(drawn, dict) or not _event_id(drawn):
        return None
    return {
        "id": _event_id(drawn),
        "event_id": _event_id(drawn),
        "title": drawn.get("label") or _event_id(drawn),
        "label": drawn.get("label") or "",
        "category": drawn.get("category") or "",
        "trigger_when": "fate_dice_auto_draw",
        "narrative_seed": drawn.get("narrative_seed") or "",
        "physical_evidence": drawn.get("physical_evidence") or [],
        "priority": 9,
        "source": "fate_dice",
    }


def _decision_payload(chapter: int, status: str, reason: str, extra: dict | None = None) -> dict:
    payload = {
        "_schema": "fate_draw_decision_v1",
        "chapter": chapter,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "producer": "auto_fate_draw.py",
        "status": status,
        "reason": reason,
    }
    if extra:
        payload.update(extra)
    return payload


def _write_decision(project_root: Path, chapter: int, status: str, reason: str,
                    extra: dict | None = None, cluster_id: str | None = None) -> Path:
    payload = _decision_payload(chapter, status, reason, extra)
    path = fate_draw_decision_path(project_root, chapter)
    atomic_write_json(path, payload)
    if cluster_id:
        cluster_path = cluster_fate_draw_decision_path(project_root, cluster_id)
        atomic_write_json(cluster_path, {**payload, "cluster_id": cluster_id, "chapter_decision_path": str(path)})
    return path


def _write_overlay(project_root: Path, chapter: int, event: dict, raw_result: dict) -> Path:
    payload = {
        "_schema": "fate_draw_overlay_v1",
        "chapter": chapter,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "producer": "auto_fate_draw.py",
        "event": event,
        "raw_result": raw_result,
    }
    path = fate_draw_overlay_path(project_root, chapter)
    atomic_write_json(path, payload)
    return path


def auto_draw(project_root: Path, chapter: int, cluster_id: str | None = None) -> dict:
    decision = draw_decision(project_root, chapter)
    if not decision["should_draw"]:
        reason = str(decision["reason"])
        decision_path = _write_decision(
            project_root, chapter, "not_required", reason,
            {k: v for k, v in decision.items() if k != "reason"},
            cluster_id=cluster_id)
        print(f"[auto_fate_draw] decision: not_required - {reason}")
        print(f"[auto_fate_draw] decision artifact: {decision_path}")
        return {"status": "not_required", "decision_path": str(decision_path), **decision}

    from fate_dice import draw

    result = draw(project_root, chapter)
    if result.get("error"):
        raise RuntimeError(str(result["error"]))

    event = _normalize_drawn_event(result)
    if not event:
        _write_decision(
            project_root, chapter, "no_candidate",
            result.get("reason") or "fate_dice.draw 未返回候选",
            {"raw_result": result, **decision},
            cluster_id=cluster_id)
        return {
            "status": "no_candidate",
            "reason": result.get("reason") or "fate_dice.draw 未返回候选",
            "raw_result": result,
            **decision,
        }

    overlay_path = _write_overlay(project_root, chapter, event, result)
    decision_path = _write_decision(
        project_root, chapter, "drawn", f"抽中 {event['event_id']}",
        {"event_id": event["event_id"], "overlay_path": str(overlay_path)},
        cluster_id=cluster_id)
    print(f"[auto_fate_draw] 自动抽到: {event['event_id']} · {event.get('narrative_seed', '')[:60]}")
    print(f"[auto_fate_draw] overlay: {overlay_path}")
    print(f"[auto_fate_draw] decision artifact: {decision_path}")
    return {
        "status": "drawn",
        "drawn": True,
        "event": event,
        "overlay_path": str(overlay_path),
        "decision_path": str(decision_path),
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python auto_fate_draw.py <project_root> <chapter> [--cluster-id cluster_001]", file=sys.stderr)
        return 2
    project_root = Path(sys.argv[1])
    try:
        chapter = int(sys.argv[2])
    except ValueError:
        print(f"chapter 必须是数字: {sys.argv[2]}", file=sys.stderr)
        return 2

    cluster_id = None
    if "--cluster-id" in sys.argv:
        idx = sys.argv.index("--cluster-id")
        if idx + 1 >= len(sys.argv):
            print("--cluster-id 缺少值", file=sys.stderr)
            return 2
        cluster_id = sys.argv[idx + 1]

    try:
        result = auto_draw(project_root, chapter, cluster_id=cluster_id)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[auto_fate_draw] FATAL: {exc}", file=sys.stderr)
        return 2

    if result.get("status") == "no_candidate":
        print(f"[auto_fate_draw] no candidate: {result.get('reason', '')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
