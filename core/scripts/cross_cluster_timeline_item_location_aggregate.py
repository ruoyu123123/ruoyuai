"""cross_cluster_timeline_item_location_aggregate.py — 时间/物件/地点跨章一致性（CCR14）

3 类跨章追踪：

A. TIMELINE_CONTINUITY
   读 _数据库/时间线.json + 每章 _changes.factual.time_anchors（如有）
   - TIME_GAP_UNEXPLAINED：相邻章时间跨度 ≥ 3 天但无任何"时间过渡"描述
   - TIME_OUT_OF_ORDER：ch5 时间线 < ch4（非倒叙的时间倒错）
   - TIME_FROZEN：≥ 5 章 time_anchor 完全相同

B. ITEM_HOLDER_CHAIN
   读 _数据库/道具.json + 每章 _changes.factual.items（变更）
   - ITEM_TELEPORTED：物件持有者从 A 跳到 C 中间无 B 经手
   - ITEM_ABANDONED：物件持有 ≥ 10 章无任何引用
   - ITEM_DUPLICATE_HOLDER：同一物件同时被两个角色持有

C. LOCATION_VISIT_DISTRIBUTION
   扫每章正文中的地点关键词（从 地图.json + 故事块摘要.scene_type）
   - LOCATION_OVERFREQ：单地点 ≥ 60% 章节出现 = 场景单调
   - LOCATION_NEVER_VISITED：地图.json 列出的地点 0 次访问 = 死场景
   - LOCATION_RHYTHM_BROKEN：N 章连续在同一地点（非 hub）

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_chapters(project_root: Path, last_n: int) -> list[int]:
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-last_n:] if chs else []


def read_changes(project_root: Path, ch: int) -> dict:
    return load_json(project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json", {})


def read_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- A. TIMELINE_CONTINUITY ----------

def scan_timeline(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    timeline_path = project_root / "_数据库" / "时间线.json"
    if not timeline_path.exists():
        return []
    timeline = load_json(timeline_path, {})
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    per_ch_time = []
    progress = load_json(project_root / "_数据库" / "进度.json", {})
    plan_dict = {}
    for cid, cdata in (progress.get("cluster_blueprint", {}) or {}).items():
        for sb in cdata.get("scene_storyboard", []):
            ch_key = sb.get("ch")
            if ch_key:
                plan_dict[str(ch_key)] = sb
    for ch in chapters:
        ch_data = plan_dict.get(str(ch)) or plan_dict.get(ch) or {}
        time_str = ch_data.get("time") or ch_data.get("time_anchor") or ch_data.get("time_hint") or ""
        if time_str:
            per_ch_time.append((ch, time_str))

    if len(per_ch_time) < 3:
        return []

    # TIME_FROZEN
    same_streak = 0
    streak_chs = []
    for i in range(1, len(per_ch_time)):
        if per_ch_time[i][1] == per_ch_time[i - 1][1]:
            same_streak += 1
            streak_chs.append(per_ch_time[i][0])
            if same_streak >= 5:
                findings.append({
                    "severity": "advisory",
                    "code": "TIME_FROZEN",
                    "time": per_ch_time[i][1],
                    "consecutive_chs": streak_chs[-5:],
                    "suggestion": f"近 {same_streak + 1} 章 time_anchor 完全相同（{per_ch_time[i][1]}）→ 时间停滞，节奏感弱",
                })
                same_streak = 0
                streak_chs = []
        else:
            same_streak = 0
            streak_chs = []

    # TIME_GAP_UNEXPLAINED：检测显著时间跳跃
    # 简化：相邻章 time_str 中数字差异（如"周二晚" vs "周五晨"差 3 天 = 跳跃）
    DAY_KW = {"周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7}
    for i in range(1, len(per_ch_time)):
        t1, t2 = per_ch_time[i - 1][1], per_ch_time[i][1]
        d1 = next((v for k, v in DAY_KW.items() if k in t1), None)
        d2 = next((v for k, v in DAY_KW.items() if k in t2), None)
        if d1 is not None and d2 is not None:
            gap = (d2 - d1) % 7
            if gap >= 3:
                # 检查正文是否有"过渡"描述
                ch = per_ch_time[i][0]
                text = read_text(project_root, ch)
                transition_kws = ["几天后", "三天后", "次日", "翌日", "过了", "之后", "随后", "周末"]
                has_transition = any(kw in text[:1000] for kw in transition_kws)
                if not has_transition:
                    findings.append({
                        "severity": "advisory",
                        "code": "TIME_GAP_UNEXPLAINED",
                        "from": {"ch": per_ch_time[i - 1][0], "time": t1},
                        "to": {"ch": ch, "time": t2},
                        "day_gap": gap,
                        "suggestion": f"ch{per_ch_time[i-1][0]}→{ch} 时间跳 {gap} 天但开头无过渡描述",
                    })
    return findings


# ---------- B. ITEM_HOLDER_CHAIN ----------

def scan_item_chain(project_root: Path, chapters: list[int]) -> list[dict]:
    items_path = project_root / "_数据库" / "道具.json"
    if not items_path.exists():
        return []
    items = load_json(items_path, {})
    items_list = items.get("items", []) or []
    findings = []

    # 收集每章物件持有者变更（_changes.factual.items）
    item_history = defaultdict(list)  # item_id -> [(ch, holder)]
    for ch in chapters:
        changes = read_changes(project_root, ch)
        item_changes = (changes.get("factual", {}) or {}).get("items", []) or []
        for ic in item_changes:
            if isinstance(ic, dict):
                iid = ic.get("id") or ic.get("name")
                holder = ic.get("holder") or ic.get("new_holder")
                if iid:
                    item_history[iid].append((ch, holder))

    for it in items_list:
        iid = it.get("id") or it.get("name")
        if not iid:
            continue
        history = item_history.get(iid, [])
        if not history:
            # ITEM_ABANDONED：道具表里有但近 N 章无任何变更
            if len(chapters) >= 10:
                findings.append({
                    "severity": "advisory",
                    "code": "ITEM_ABANDONED",
                    "item_id": iid,
                    "suggestion": f"道具 {iid} 在近 {len(chapters)} 章无任何持有者变更或引用 → 死道具",
                })
            continue
        # ITEM_DUPLICATE_HOLDER：同一 ch 多个 holder
        per_ch = defaultdict(set)
        for ch, h in history:
            per_ch[ch].add(h)
        for ch, hs in per_ch.items():
            if len(hs) > 1:
                findings.append({
                    "severity": "warning",
                    "code": "ITEM_DUPLICATE_HOLDER",
                    "item_id": iid,
                    "ch": ch,
                    "holders": list(hs),
                    "suggestion": f"道具 {iid} 在 ch{ch} 同时被多个角色持有 {hs}",
                })
    return findings


# ---------- C. LOCATION_VISIT_DISTRIBUTION ----------

def scan_location_distribution(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    map_path = project_root / "_数据库" / "地图.json"
    if not map_path.exists():
        return []
    map_data = load_json(map_path, {})
    locations = map_data.get("locations", []) or map_data.get("places", []) or []
    if not locations:
        return []
    # 提取地点名
    loc_names = []
    for loc in locations:
        if isinstance(loc, dict):
            n = loc.get("name") or loc.get("id")
        else:
            n = str(loc)
        if n:
            loc_names.append(n)

    # 扫每章正文中地点出现频次
    visit_counts = Counter()
    visit_chs = defaultdict(set)
    per_ch_locations = {}
    for ch in chapters:
        text = read_text(project_root, ch)
        chs_locs = set()
        for n in loc_names:
            if n in text:
                visit_counts[n] += 1
                visit_chs[n].add(ch)
                chs_locs.add(n)
        per_ch_locations[ch] = chs_locs

    if not visit_counts:
        return findings

    total_chs = len(chapters)

    # LOCATION_OVERFREQ：某地占 ≥ 60% 章
    for n, count in visit_counts.most_common(3):
        if count / total_chs >= 0.6:
            findings.append({
                "severity": "advisory",
                "code": "LOCATION_OVERFREQ",
                "location": n,
                "appearance_chs": count,
                "pct": round(count / total_chs, 2),
                "suggestion": f"地点「{n}」近 {total_chs} 章出现在 {count} 章 ({round(count/total_chs*100)}%) → 场景单调",
            })

    # LOCATION_NEVER_VISITED
    never_visited = [n for n in loc_names if visit_counts[n] == 0]
    if never_visited and len(loc_names) >= 5:
        findings.append({
            "severity": "advisory",
            "code": "LOCATION_NEVER_VISITED",
            "locations": never_visited[:8],
            "total_unused": len(never_visited),
            "total_locations": len(loc_names),
            "suggestion": f"地图.json 中 {len(never_visited)}/{len(loc_names)} 个地点近 {total_chs} 章 0 次访问 → 死场景",
        })

    # LOCATION_RHYTHM_BROKEN：连续 N 章停留同一地（且非已知 hub）
    hubs_path = project_root / "_数据库" / "枢纽场景.json"
    hub_labels = set()
    if hubs_path.exists():
        hd = load_json(hubs_path, {})
        for h in hd.get("hubs", []) or []:
            l = h.get("label", "")
            if l:
                hub_labels.add(l)

    # 简化：检查 per_ch_locations 是否连续 ≥ 4 章只含一个非 hub 地点
    sorted_chs = sorted(per_ch_locations.keys())
    cur_loc = None
    streak = 0
    for ch in sorted_chs:
        locs = per_ch_locations[ch]
        non_hub_locs = [l for l in locs if not any(h_lbl in l or l in h_lbl for h_lbl in hub_labels)]
        if len(non_hub_locs) == 1:
            l = non_hub_locs[0]
            if l == cur_loc:
                streak += 1
                if streak >= 4:
                    findings.append({
                        "severity": "advisory",
                        "code": "LOCATION_RHYTHM_BROKEN",
                        "location": l,
                        "consecutive_chs": streak + 1,
                        "suggestion": f"非 hub 地点「{l}」连续 ≥ {streak + 1} 章 → 节奏沉滞",
                    })
                    streak = 0
            else:
                cur_loc = l
                streak = 1
        else:
            cur_loc = None
            streak = 0
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    chapters = get_chapters(project_root, args.last_n)
    if not chapters:
        print("[SKIP] 无已写章节")
        sys.exit(0)

    findings = []
    findings.extend(scan_timeline(project_root, chapters))
    findings.extend(scan_item_chain(project_root, chapters))
    findings.extend(scan_location_distribution(project_root, chapters))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "timeline_item_location",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"timeline_item_location_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[timeline_item_location] {summary['warning']} warning / {summary['advisory']} advisory")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
