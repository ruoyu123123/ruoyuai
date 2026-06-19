"""auto_fate_draw.py — 事件池自动抽签(写作前)

G3 调研发现: 事件池.json 有候选事件但 fate_dice draw 从不自动触发,
build_manifest 只给主代理一句"想埋钩子可手动调"提示,实际主代理几乎不会主动 draw。
结果: 涟漪涌现的素材通道半瘫。

机制:
1. 读 build_manifest 产出的 manifest (active_fate_events 字段)
2. 如果为空且事件池有候选 → 自动 draw 1 条
3. 写入 manifest.active_fate_events (narrative_seed)

接入点: cluster-write step1 build_manifest 之前
exit 0: advisory · 不阻断 · draw 结果进 manifest

北极星②: 涟漪素材应自动流动不靠主代理记得手动调

用法:
  python core/scripts/auto_fate_draw.py <project_root> <chapter>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def should_draw(project_root: Path, chapter: int) -> bool:
    """检查是否应自动 draw: 事件池有候选 + 本章无 active_fate_events。"""
    db = project_root / "_数据库"

    # 事件池有候选?
    pool_path = db / "事件池.json"
    if not pool_path.exists():
        return False
    try:
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    events = pool.get("events", pool.get("pool", []))
    available = [e for e in events if e.get("status", "available") == "available"]
    if not available:
        return False

    # 本章 manifest 有无 active_fate_events?
    manifest_path = db / ".manifest" / f"ch_{chapter:03d}.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            active = m.get("active_fate_events", [])
            if active:
                return False  # 已有,不重复 draw
        except Exception:
            pass

    return True


def auto_draw(project_root: Path, chapter: int) -> dict:
    """如果条件满足,自动 fate_dice draw 1 条。"""
    if not should_draw(project_root, chapter):
        return {"drawn": False, "reason": "无需 draw"}

    try:
        from fate_dice import draw
        result = draw(project_root, chapter)
        if result.get("event_id"):
            print(f"[auto_fate_draw] 自动抽到: {result.get('event_id')} · {result.get('narrative_seed', '')[:60]}")
            return {"drawn": True, "event": result}
        return {"drawn": False, "reason": "draw 返回空"}
    except Exception as e:
        print(f"[auto_fate_draw] draw 失败(不阻断): {e}", file=sys.stderr)
        return {"drawn": False, "reason": str(e)}


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python auto_fate_draw.py <project_root> <chapter>", file=sys.stderr)
        return 2
    project_root = Path(sys.argv[1])
    try:
        chapter = int(sys.argv[2])
    except ValueError:
        print(f"chapter 必须是数字: {sys.argv[2]}", file=sys.stderr)
        return 2
    auto_draw(project_root, chapter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
