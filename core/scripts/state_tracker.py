"""state_tracker.py — 符号事实图（v17.6 D1 / SCORE 框架启发）

arxiv 2503.23512 SCORE 框架核心组件：Dynamic State Tracking。
扫描历史 ch1..N 的 CHANGES_FACTUAL，维护角色/物件当前持有状态符号图。

事实图结构：
{
  "snapshot_at_ch": N,
  "characters": {
    "克莱": {
      "current_location": "黗火塔塔顶值班室",
      "carrying_items": ["铜钥匙7", "前任日记"],
      "alive": True,
      "last_appearance_ch": N,
      "key_actions_history": [{"ch":1, "action":"上船"}, {"ch":3, "action":"登塔"}]
    }
  },
  "items": {
    "铜钥匙7": {"holder": "克莱", "obtained_at_ch": 1, "location": "克莱衣兜"},
    ...
  },
  "locations_visited": [{"ch":1, "by":"克莱", "place":"索伦敦港口"}, ...]
}

用法：
    python state_tracker.py <项目路径> <章节号> [--write]
    python state_tracker.py <项目路径> --validate <章节号>   # 检查 ch 内容与事实图冲突
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写
import cluster_lookup  # noqa: E402  2026-05-29 修：obtained_cluster 是 cluster_id 不是章号


def load_json(p, default=None):
    if not Path(p).exists():
        return default
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_chapter(project_root: Path, ch: int):
    """读章节正文 + 双段 CHANGES，返回 (body, factual, self_eval)。
    v18：统一走 chapter_io —— 正文走 read_body()，CHANGES 走 read_changes()
    （优先读 _changes.json，旧混合 txt 自动解析）。找不到正文返回 (None, None, None)。"""
    try:
        body = cio.read_body(project_root, ch)
    except FileNotFoundError:
        return None, None, None
    changes = cio.read_changes(project_root, ch)
    return body, changes.get("factual", {}), changes.get("self_eval", {})


def build_state_at_chapter(project_root: Path, target_ch: int) -> dict:
    """从 ch1 一直累积到 target_ch 的事实图。"""
    state = {
        "snapshot_at_ch": target_ch,
        "characters": {},
        "items": {},
        "locations_visited": [],
    }
    # 加载人物卡的 locked_facts 作为初始状态
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    for c in cards:
        state["characters"][c.get("name", "")] = {
            "first_appearance_ch": c.get("first_appearance_ch"),
            "current_location": None,
            "carrying_items": [],
            "alive": True,
            "last_appearance_ch": None,
            "key_actions_history": [],
            "locked_facts": c.get("locked_facts", {}),
        }
    # 加载道具初始状态
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 obtained_cluster
    # 2026-05-29 修：obtained_cluster 是 cluster_id（如 cluster_002），原代码把抽出的
    # cluster 序号（2）直接和章号 target_ch 比是量纲错误。改用 cluster_id_to_range 取
    # 该 cluster 起始章 lo，用 lo <= target_ch 判断道具所属 cluster 是否在 target_ch 前已开始。
    # 反查不到 range 时保守默认包含（道具已在库即已设定），并记入 fallback 标记。
    items = load_json(project_root / "_数据库" / "道具.json", {}).get("items", [])
    for it in items:
        oc = it.get("obtained_cluster", "cluster_999")
        _rng = cluster_lookup.cluster_id_to_range(project_root, oc)
        if _rng is not None:
            include = _rng[0] <= target_ch
            range_fallback = False
        else:
            # 反查不到 cluster 章范围（fluid v27 章数未回填等）：保守默认包含
            include = True
            range_fallback = True
        if include:
            state["items"][it.get("name", "")] = {
                "_obtained_cluster_range_fallback": range_fallback,
                "holder": it.get("holder", "unknown"),
                "obtained_at_cluster": it.get("obtained_cluster"),
                "type": it.get("type"),
                "chekhov": it.get("chekhov", False),
            }
    # 累积每章 CHANGES_FACTUAL
    for ch in range(1, target_ch + 1):
        body, factual, _ = parse_chapter(project_root, ch)
        if not factual:
            continue
        # character_movements
        for m in factual.get("character_movements", []) or []:
            name = m.get("character") or m.get("name")
            if not name:
                continue
            if name not in state["characters"]:
                state["characters"][name] = {"first_appearance_ch": ch, "carrying_items": [],
                                              "alive": True, "key_actions_history": []}
            if m.get("to"):
                state["characters"][name]["current_location"] = m["to"]
            state["characters"][name]["last_appearance_ch"] = ch
        # item_transfers
        for t in factual.get("item_transfers", []) or []:
            name = t.get("name") or t.get("item")
            if not name:
                continue
            if name not in state["items"]:
                state["items"][name] = {"obtained_at_ch": ch}
            state["items"][name]["holder"] = t.get("to", state["items"][name].get("holder"))
            # 更新拿到人的 carrying_items
            new_holder = t.get("to", "")
            if new_holder and new_holder in state["characters"]:
                if name not in state["characters"][new_holder].get("carrying_items", []):
                    state["characters"][new_holder].setdefault("carrying_items", []).append(name)
            old_holder = t.get("from", "")
            if old_holder and old_holder in state["characters"]:
                if name in state["characters"][old_holder].get("carrying_items", []):
                    state["characters"][old_holder]["carrying_items"].remove(name)
        # 出场角色更新 last_appearance_ch
        for name in factual.get("active_characters", []) or factual.get("characters_present", []) or []:
            if name in state["characters"]:
                state["characters"][name]["last_appearance_ch"] = ch
            else:
                state["characters"][name] = {"first_appearance_ch": ch, "last_appearance_ch": ch,
                                              "carrying_items": [], "alive": True}
        # locations
        for loc in factual.get("location_changes", []) or []:
            place = loc.get("name") or loc.get("location_id")
            if place:
                state["locations_visited"].append({"ch": ch, "place": place,
                                                    "description": loc.get("description", "")})
    return state


def validate_chapter_vs_state(project_root: Path, ch: int) -> list[dict]:
    """读 ch 章节正文，对照 ch-1 的事实图，检测冲突。"""
    # 取 ch-1 的事实图作为"应有状态"
    expected = build_state_at_chapter(project_root, ch - 1)
    body, factual, _ = parse_chapter(project_root, ch)
    if not body:
        return [{"severity": "fatal", "msg": f"找不到 ch{ch} 章节文件"}]
    issues = []
    # 检查 1：人物卡角色不在场但章节正文写他做了什么
    for name, info in expected["characters"].items():
        if info.get("alive") is False:
            # 已死亡角色不应出现
            if name in body:
                issues.append({
                    "severity": "error",
                    "msg": f"已死亡角色「{name}」出现在 ch{ch} 正文",
                })
    # 检查 2：道具持有者矛盾
    declared_present = factual.get("active_characters", []) or factual.get("characters_present", []) if factual else []
    for item_name, item_info in expected["items"].items():
        holder = item_info.get("holder", "")
        if not holder or holder == "unknown":
            continue
        # 章节里如果某角色"找该道具"——可能与"该角色已持有该道具"矛盾
        # 简单 heuristic：item_name + "找" / "丢失" 出现 + holder 在正文出现
        if item_name in body and holder in body:
            for kw in ["找不到", "丢失", "不见了", "失去"]:
                pattern = item_name + ".*" + kw
                import re
                if re.search(pattern, body[:5000]):
                    issues.append({
                        "severity": "warning",
                        "msg": f"道具「{item_name}」持有者是「{holder}」，但 ch{ch} 似乎有「{kw}」叙述",
                    })
                    break
    return issues


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    if "--validate" in args:
        idx = args.index("--validate")
        ch = int(args[idx + 1])
        issues = validate_chapter_vs_state(project_root, ch)
        print(f"[State Validate] ch{ch}")
        if not issues:
            print(f"  ✅ 无冲突")
            sys.exit(0)
        for iss in issues:
            print(f"  [{iss['severity']}] {iss['msg']}")
        sys.exit(1 if any(i["severity"] in ("error", "fatal") for i in issues) else 0)
    ch = int(args[1])
    write = "--write" in args
    state = build_state_at_chapter(project_root, ch)
    if write:
        out_dir = project_root / "_数据库" / ".state_graph"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"ch_{ch:03d}.json"
        out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] state_graph 已写入: {out}")
    print(f"[State Snapshot at ch{ch}]")
    print(f"  characters: {len(state['characters'])}")
    for name, info in list(state["characters"].items())[:5]:
        loc = info.get("current_location", "?")
        items = info.get("carrying_items", [])
        last = info.get("last_appearance_ch", "?")
        print(f"    {name}: location={loc}, items={items}, last_ch={last}")
    print(f"  items: {len(state['items'])}")
    for name, info in list(state["items"].items())[:5]:
        print(f"    {name}: holder={info.get('holder')}")
    print(f"  locations_visited: {len(state['locations_visited'])}")


if __name__ == "__main__":
    main()
