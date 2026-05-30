"""clock_engine.py — 显式 Clock 进度系统（v21 R1.1 新增）

借鉴 Citizen Sleeper 的 Clocks 机制 + CK3 的 Schemes 进度。**所有"逐渐变化的事"显式建模为 clock**。

设计目标：
- 解决「节奏失控」：clock 满格 → 强制触发，避免主代理凭感觉拖延
- 解决「伏笔忘埋」：foreshadowing due_by 自动注册为 clock，到期前 N 章告警
- 解决「反派步步紧逼无量化」：反派耐心/势力 schemes 都建模为 tick

五个操作：
1. tick <ch>                              - 按 chapter_end 触发所有相关 clock +tick_per_event
2. tick_event <ch> <event_type> <value>   - 按特定事件触发匹配 clock（如 minor_event）
3. list <ch>                              - 列出当前活跃 clock + 距离满格距离
4. spawn <ch> <id> <label> ... (json)     - 动态创建 clock
5. dashboard                              - 全 clock 全景

退出码: 0 健康 / 1 有 clock 满格触发 / 2 致命
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from datetime import datetime
from pathlib import Path

# 2026-05-29 修：注入 scripts 目录以 import atomic_json（原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json
try:
    import cluster_lookup  # 2026-05-30 北极星：since_cluster 反查真实 cluster_id（防御 import）
except Exception:
    cluster_lookup = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-29 修：裸写 → 原子写（atomic_write_json 内部已 mkdir + fsync）
    atomic_json.atomic_write_json(p, data)


def load_clocks(project_root: Path) -> dict:
    return load_json(project_root / "_数据库" / "时钟表.json", None)


def save_clocks(project_root: Path, data: dict):
    save_json(project_root / "_数据库" / "时钟表.json", data)


# ───────────────────── 真实项目 schema 兼容层（2026-05-30 北极星③契约修复） ─────────────────────
# 背景（3 真实项目实测）：引擎旧版只认 v21 扁平 schema（clocks[] · ticks/max/tick_on/status），
# 但 AI 在 outline 阶段自由生成维度 schema，导致引擎静默 no-op（_do_tick 全跳过 / list_active 空）：
#   · 城南：clocks[] · current_segments/max_segments/linked_me（无 status/ticks/tick_on）
#   · 纵尸司：clocks[] · id/current/initial/trigger_at_zero（倒计时 · current 递减到 0 触发）
#   · 诡异：story_clocks[] · id/current/target/trigger_at_target/incremented_by（正计时 · current 升到 target）
# 修复（参照 fate_engine._events / ._event_id 范式）：加小 accessor 把 4 套形态归一到统一视图，
# 让引擎读到真实状态注入 writer。纪律：只兼容读取（北极星⑥别过度复杂），不强制改 outline schema。


def _clock_list(data: dict) -> list[dict]:
    """取 clock 列表 · 键名兼容 clocks（v21/城南/纵尸司） or story_clocks（诡异）。过滤非 dict 占位。"""
    raw = data.get("clocks")
    if not raw:
        raw = data.get("story_clocks") or []
    return [c for c in raw if isinstance(c, dict)]


def _clock_id(c: dict) -> str:
    """取 clock 标识 · clock_id（v21/城南） or id（纵尸司/诡异）。"""
    return c.get("clock_id") or c.get("id") or ""


def _clock_label(c: dict) -> str:
    """取可读标签 · label（v21） or name（纵尸司/诡异） or description（城南）。"""
    return c.get("label") or c.get("name") or c.get("description") or _clock_id(c)


def _clock_progress(c: dict) -> tuple[int, int, str]:
    """归一进度到 (current_ticks, max, direction)：
      · direction="up"   → current_ticks 升到 max 满格触发（v21 ticks/max · 城南 current_segments/max_segments · 诡异 current/target）
      · direction="down" → 倒计时（纵尸司 current/initial · current 递减到 0 触发）：
          为统一「升到 max 满格」语义，映射成 elapsed=initial-current 升到 initial（remaining=current）。
    返回 (ticks, max, direction)。max<=0 兜底 99 防除零。"""
    # 倒计时形态（纵尸司）：有 trigger_at_zero 或（有 initial 且无 max/target/segments）
    has_countdown = ("trigger_at_zero" in c) or (
        "initial" in c and "max" not in c and "max_segments" not in c and "target" not in c
    )
    if has_countdown:
        initial = c.get("initial", c.get("current", 0))
        current = c.get("current", initial)
        max_v = initial if initial > 0 else 99
        elapsed = max_v - current  # current 越小越接近触发 → elapsed 越大越满格
        return (max(0, elapsed), max_v, "down")
    # 正计时形态（v21 / 城南 / 诡异）
    ticks = c.get("ticks")
    if ticks is None:
        ticks = c.get("current_segments")
    if ticks is None:
        ticks = c.get("current", 0)
    max_v = c.get("max")
    if max_v is None:
        max_v = c.get("max_segments")
    if max_v is None:
        max_v = c.get("target", 99)
    if not max_v or max_v <= 0:
        max_v = 99
    return (ticks or 0, max_v, "up")


def _clock_trigger(c: dict) -> str:
    """取满格触发描述 · trigger_on_max（v21） or trigger_at_target（诡异） or trigger_at_zero（纵尸司）。"""
    return c.get("trigger_on_max") or c.get("trigger_at_target") or c.get("trigger_at_zero") or ""


def _clock_is_active(c: dict) -> bool:
    """是否活跃（可 tick / 该注入 writer）。
    v21 有显式 status → 仅 active；维度 schema 无 status 字段 → 视为活跃（除非已标 triggered/abandoned）。"""
    st = c.get("status")
    if st is None:
        return True  # 城南/纵尸司/诡异 无 status → 默认活跃
    return st == "active"


def _match_tick_on(tick_on_list: list[str], event_type: str, value: str = "") -> bool:
    """tick_on 匹配。支持 chapter_end / event_type:<glob_pattern>。"""
    for to in tick_on_list:
        if to == event_type:
            return True
        if ":" in to:
            t, pat = to.split(":", 1)
            if t == event_type and fnmatch.fnmatch(value, pat):
                return True
    return False


def _write_back_progress(c: dict, new_ticks: int, max_v: int, direction: str):
    """把归一后的进度写回 clock 原 schema 的真实字段（兼容 4 套形态）。
    direction=="up"  → 写 ticks 升到的值（按存在的字段：ticks / current_segments / current）
    direction=="down"→ 倒计时：new_ticks 是 elapsed，回写 current = max_v - elapsed（递减）"""
    if direction == "down":
        c["current"] = max(0, max_v - new_ticks)
        return
    if "ticks" in c:
        c["ticks"] = new_ticks
    elif "current_segments" in c:
        c["current_segments"] = new_ticks
    else:
        c["current"] = new_ticks  # 诡异 current/target


def _do_tick(clocks_data: dict, ch: int, event_type: str, event_value: str = "") -> dict:
    """对**显式声明触发条件（tick_on）**的 clock 执行 tick。返回触发（满格）的 clock 列表。

    2026-05-30 北极星③⑤回归修正（batch5 audit-r4 过度修正回退）：
      · v21 有 tick_on → 按 tick_on 匹配推进（chapter_end / event_type:glob）。
      · 维度 schema **无 tick_on**（城南 linked_me / 纵尸司 trigger_at_zero / 诡异 incremented_by）
        → **不再每 chapter_end 机械推进**。这类 clock 由 ME/叙事因果驱动（涟漪效应·事件涌现），
        引擎只 read-only surface 到 list_active/manifest 让 writer 看到真实状态，**不当章节计数驱动器**。
        （batch5 让无 tick_on 的 clock 每章无条件 advance，把 Clock 从 advisory soft-pull 变成机械
         剧情驱动器——3 真实项目全部 tick_on=None 即被全量 force-tick，违反北极星③事件涌现非预设 +
         ⑤引擎顾问非驱动。此处仅修「机械推进」，保留 batch5「surface 真实状态」的正确部分。）

    显式事件触发（tick_event）仍可推进 v21 clock；维度 schema 的推进交给世界演化/走向卡叙事信号
    （目前无 changes 申报消费机制 → 维度 clock 保持不自动推进，等真实叙事信号到位再消费）。
    """
    triggered = []
    ticked = []
    for c in _clock_list(clocks_data):
        if not _clock_is_active(c):
            continue
        tick_on = c.get("tick_on")
        if not tick_on:
            # 无 tick_on：只 surface 不机械推进（北极星③⑤）——不靠章节计数触发 ME。
            continue
        if not _match_tick_on(tick_on, event_type, event_value):
            continue
        old, max_v, direction = _clock_progress(c)
        delta = c.get("tick_per_event", 1)
        new = min(max_v, old + delta)
        _write_back_progress(c, new, max_v, direction)
        ticked.append({
            "clock_id": _clock_id(c),
            "label": _clock_label(c),
            "old": old,
            "new": new,
            "max": max_v,
            "remaining": max_v - new,
        })
        if new >= max_v:
            c["status"] = "triggered"
            c["triggered_at_ch"] = ch
            triggered.append({
                "clock_id": _clock_id(c),
                "label": _clock_label(c),
                "trigger_on_max": _clock_trigger(c),
                "category": c.get("category"),
            })
    return {"ticked": ticked, "triggered": triggered}


def tick_chapter(project_root: Path, ch: int) -> dict:
    """每章末统一 tick（chapter_end 触发的所有 clock）。"""
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    r = _do_tick(data, ch, "chapter_end")
    save_clocks(project_root, data)
    return {"ch": ch, "event": "chapter_end", **r}


def tick_event(project_root: Path, ch: int, event_type: str, event_value: str) -> dict:
    """事件触发（minor_event:<match> / fate_event:<id> / faction_drop:<faction>:<dim>）。"""
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    r = _do_tick(data, ch, event_type, event_value)
    save_clocks(project_root, data)
    return {"ch": ch, "event": f"{event_type}:{event_value}", **r}


def list_active(project_root: Path, ch: int) -> dict:
    """列出当前活跃 clock + 距离满格 + 紧迫度（remaining ≤ 2 → urgent）。"""
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    out = []
    for c in _clock_list(data):
        if not _clock_is_active(c):
            continue
        ticks, max_v, _direction = _clock_progress(c)
        remaining = max_v - ticks
        urgency = "urgent" if remaining <= 2 else ("approaching" if remaining <= max_v * 0.3 else "normal")
        out.append({
            "clock_id": _clock_id(c),
            "label": _clock_label(c),
            "category": c.get("category"),
            "ticks": ticks,
            "max": max_v,
            "remaining": remaining,
            "urgency": urgency,
            "trigger_on_max": _clock_trigger(c),
            "visible_to_protagonist": c.get("visible_to_protagonist", False),
            "_reason": c.get("_reason") or c.get("description", ""),
        })
    out.sort(key=lambda x: (x["remaining"], -x["ticks"]))
    return {"ch": ch, "active_clocks": out, "total_active": len(out)}


def _since_cluster(project_root: Path, ch: int) -> str:
    """反查 ch 所属 cluster_id（北极星铁律：禁用 f"cluster_{ch:03d}" 章号拼接）。反查失败兜底。"""
    if cluster_lookup is not None:
        try:
            cid = cluster_lookup.ch_to_cluster_id(project_root, ch)
            if cid:
                return cid
        except Exception:
            pass
    return f"cluster_{ch:03d}"  # 反查失败兜底（dormant 功能；未来启用前应确保 cluster 已涌现）


def spawn(project_root: Path, ch: int, clock_def: dict) -> dict:
    """动态创建 clock。clock_def 必含 label/max/tick_on/trigger_on_max。"""
    data = load_clocks(project_root)
    if data is None:
        # 初始化文件
        data = {"_schema": "clocks_v21_explicit_progression", "clocks": []}
    clocks = data.setdefault("clocks", [])
    # 自动 ID
    nums = []
    for c in clocks:
        cid = c.get("clock_id", "")
        if cid.startswith("CK_"):
            try:
                nums.append(int(cid[3:]))
            except ValueError:
                pass
    next_num = (max(nums) + 1) if nums else 1
    new = {
        "clock_id": f"CK_{next_num:03d}",
        "label": clock_def.get("label", "未命名"),
        "category": clock_def.get("category", "other"),
        "ticks": clock_def.get("ticks", 0),
        "max": clock_def.get("max", 5),
        "tick_on": clock_def.get("tick_on", ["chapter_end"]),
        "tick_per_event": clock_def.get("tick_per_event", 1),
        "trigger_on_max": clock_def.get("trigger_on_max", ""),
        "visible_to_protagonist": clock_def.get("visible_to_protagonist", False),
        "visible_to_writer": clock_def.get("visible_to_writer", True),
        "since_cluster": _since_cluster(project_root, ch),  # 2026-05-30 北极星：反查真实 cluster_id（非章号拼接）
        "spawned_by": clock_def.get("spawned_by", "manual"),
        "status": "active",
        "_reason": clock_def.get("_reason", ""),
    }
    clocks.append(new)
    save_clocks(project_root, data)
    return {"created": new["clock_id"], "label": new["label"]}


def dashboard(project_root: Path) -> dict:
    data = load_clocks(project_root)
    if data is None:
        return {"error": "时钟表.json 不存在"}
    by_status = {}
    by_category = {}
    by_urgency = {"urgent": 0, "approaching": 0, "normal": 0, "triggered": 0}
    clocks = _clock_list(data)
    for c in clocks:
        s = c.get("status") or ("active" if _clock_is_active(c) else "?")
        by_status[s] = by_status.get(s, 0) + 1
        cat = c.get("category", "?")
        by_category[cat] = by_category.get(cat, 0) + 1
        if s == "triggered":
            by_urgency["triggered"] += 1
        elif _clock_is_active(c):
            ticks, max_v, _direction = _clock_progress(c)
            remaining = max_v - ticks
            if remaining <= 2:
                by_urgency["urgent"] += 1
            elif remaining <= max_v * 0.3:
                by_urgency["approaching"] += 1
            else:
                by_urgency["normal"] += 1
    return {
        "total_clocks": len(clocks),
        "by_status": by_status,
        "by_category": by_category,
        "by_urgency": by_urgency,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["tick", "tick_event", "list", "spawn", "dashboard"])
    ap.add_argument("ch", nargs="?", type=int, default=None)
    ap.add_argument("--event-type", type=str, default=None)
    ap.add_argument("--event-value", type=str, default="")
    ap.add_argument("--json", type=str, default=None, help="spawn 用的 clock_def JSON")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "dashboard":
        print(json.dumps(dashboard(project_root), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch is None:
        print(f"[ERROR] {args.action} 需要 <ch>", file=sys.stderr)
        sys.exit(2)

    if args.action == "tick":
        r = tick_chapter(project_root, args.ch)
    elif args.action == "tick_event":
        if not args.event_type:
            print("[ERROR] tick_event 需要 --event-type", file=sys.stderr)
            sys.exit(2)
        r = tick_event(project_root, args.ch, args.event_type, args.event_value or "")
    elif args.action == "list":
        r = list_active(project_root, args.ch)
    elif args.action == "spawn":
        if not args.json:
            print("[ERROR] spawn 需要 --json '<def>'", file=sys.stderr)
            sys.exit(2)
        clock_def = json.loads(args.json)
        r = spawn(project_root, args.ch, clock_def)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    triggered = r.get("triggered") or []
    if triggered:
        print(f"\n[CLOCK TRIGGERED] {len(triggered)} clocks 满格:", file=sys.stderr)
        for t in triggered:
            print(f"  {t['clock_id']} {t['label']} → {t['trigger_on_max']}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
