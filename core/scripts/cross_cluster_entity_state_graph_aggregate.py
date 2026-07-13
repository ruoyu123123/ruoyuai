"""cross_cluster_entity_state_graph_aggregate.py — 实体状态时间线图矛盾聚合（A10 · 2026-07-07）

出处：Magnet/Atlas arXiv:2607.00918（research/open_source_writing_systems_round2.md A10）——
故事解析成实体状态图后在图上找跨场景矛盾（precision/recall 超纯 LLM）。本聚合器是若渝落地：
**确定性零 LLM**，从既有结构化状态账本重建「实体状态按 cluster 序的时间线图」，检查图上矛盾。
它是「确定性 scanner ↔ LLM judge」之间的结构化中间层。

与 pre_write_gate 的分工（互补不重叠）：
    pre_write_gate  = 写前拦 **下一块 brief**（计划层·blocking 写前拒绝）
    本聚合器        = 写后查 **已写块账本时间线**（事实层·advisory 待裁决项）

时间线数据源（全部实地核查过真实字段·见各 check docstring）：
    · `_数据库/.wal/cluster_<key>_archive.json`（apply_archive 的输入·每 cluster 一份留档）
      characters[]{id,name,status,state_changes} / items[]{id,name,holder} /
      relationships[]{id,from,to,type,note}
    · `_数据库/事件簇.json` clusters[].locked_facts[]{fact,subject}（死亡声明辅助源）
      + clusters[].gate_waivers[]（闪回/模糊生死类叙事声明·豁免复用 pre_write_gate 归一表）
    字段缺失 = 该检查诚实 skip（checks 段留痕 skip 原因·绝不猜）。
    人物卡.json 的 status 是**当前态无 cluster 锚**，无法进时间线——不臆造该通路
    （死亡锚一律取 archive 逐块 status / locked_facts 死亡声明）。

三类图矛盾（全部 code=ENTITY_STATE_GRAPH_CONFLICT · **恒 advisory 绝不 hard_gate**——
图推断有近似性：复活/闪回/亡灵是合法网文叙事，机器只报告待裁决项，北极星⑤）：
    ① dead_reappeared      死亡后在更晚 cluster 的 archive.characters 以 alive 出场
                           （该 cluster brief 有 dead_character 类 gate_waivers 声明 → 豁免）
    ② item_dual_holder     道具在**同一 cluster** archive 内出现 ≥2 个不同持有者
    ③ relationship_regressed 关系 type 回跳到更早状态（A→B→A）且回跳块 archive 无 note 事件支撑

env ENTITY_STATE_GRAPH_MODE：off / shadow（默认·零回归）/ active。
退出码: 0 健康或 shadow / 1 advisory（active 时）——本聚合器无 warning 档，永不 exit 2。

用法：python cross_cluster_entity_state_graph_aggregate.py <project>
（时间线图必须覆盖全书史——死亡在 cluster_001、复活在 cluster_009 也要抓，不设窗口。）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cluster_lookup as cl  # noqa: E402
import atomic_json  # noqa: E402
# 单一真理源复用（禁双口径）：死亡词表 + 豁免 type 归一表与写前 gate 完全同源
from pre_write_gate import _DEAD_STATUS_WORDS, _normalize_waiver_type  # noqa: E402

CODE = "ENTITY_STATE_GRAPH_CONFLICT"

# archivist 合约（.claude/agents/novel-archivist.md）status 枚举 alive/dead/missing/unknown。
# 复活判定只认显式 alive（missing/unknown=生死模糊合法叙事·尸体/回忆被列出常无 status → skip）。
_ALIVE_STATUS_WORDS = {"alive", "存活", "活着", "健在"}
# locked_facts 死亡声明关键词（fact 文本含角色名 + 任一词 → 该 cluster 记死亡锚）
_DEATH_FACT_WORDS = ("已死", "死亡", "身亡", "牺牲", "去世", "殒命", "毙命")


def load_json(p: Path, default=None):
    return atomic_json.load_json(p, default=default)


def _mode() -> str:
    m = (os.environ.get("ENTITY_STATE_GRAPH_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


# ───────────────────────── 时间线图构建 ─────────────────────────

_ARCHIVE_RE = re.compile(r"^cluster_(\d+)_archive\.json$")


def load_archives(project_root: Path) -> list[tuple[int, str, dict]]:
    """按 cluster 序加载全部留档 archive → [(num, cluster_id, archive_dict)]。"""
    wal = project_root / "_数据库" / ".wal"
    out = []
    if not wal.exists():
        return out
    for p in wal.iterdir():
        m = _ARCHIVE_RE.match(p.name)
        if not m:
            continue
        data = load_json(p, None)
        if isinstance(data, dict):
            out.append((int(m.group(1)), f"cluster_{int(m.group(1)):03d}", data))
    out.sort(key=lambda t: t[0])
    return out


def _event_clusters(project_root: Path) -> dict[int, dict]:
    """事件簇.json clusters 按 cluster 号索引（locked_facts 死亡声明 + gate_waivers 豁免源）。"""
    data = load_json(project_root / "_数据库" / "事件簇.json", {}) or {}
    out: dict[int, dict] = {}
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        num = cl.cluster_num(cl.normalize_cluster_id(c.get("cluster_id")))
        if num is not None:
            out[num] = c
    return out


def _is_dead_status(status) -> bool:
    s = str(status or "").strip().lower()
    return bool(s) and (s in _DEAD_STATUS_WORDS or any(w in s for w in _DEAD_STATUS_WORDS))


def _is_alive_status(status) -> bool:
    s = str(status or "").strip().lower()
    return s in _ALIVE_STATUS_WORDS


def _char_key(entry: dict) -> str | None:
    """角色时间线节点 key：优先 id，无 id 按 name（apply_archive 同款按名兜底口径）。"""
    return str(entry.get("id") or "").strip() or str(entry.get("name") or "").strip() or None


def _flashback_waived(ec_cluster: dict | None, char_names: set[str]) -> dict | None:
    """该 cluster brief 是否声明了覆盖此角色的 dead_character 类叙事豁免（闪回/亡灵/模糊生死）。

    复用 pre_write_gate._normalize_waiver_type（单一归一表）；target 与角色 id/name 任一
    包含匹配即认（与 gate 的 _waiver_matches 同宽容度）。返回命中的 waiver dict 或 None。
    """
    if not isinstance(ec_cluster, dict):
        return None
    for w in ec_cluster.get("gate_waivers") or []:
        if not isinstance(w, dict):
            continue
        if _normalize_waiver_type(w.get("type")) != "dead_character":
            continue
        target = str(w.get("target") or "").strip()
        if not target:
            continue
        for name in char_names:
            if name and (target == name or target in name or name in target):
                return w
    return None


# ───────────────────────── ① 死亡后再出场 ─────────────────────────

def scan_dead_reappeared(archives: list, ec_by_num: dict[int, dict]) -> tuple[list, dict]:
    """角色状态时间线：死亡锚（archive status=dead ∪ 事件簇 locked_facts 死亡声明）之后，
    更晚 cluster 的 archive.characters 以显式 alive 状态再出场 → 矛盾。

    两遍算法（时间线图·同 cluster 内 alive 先于 dead 结算——「本块内战死」不误报）：
      pass A 收全书角色 key→names + 逐块 status 事件；pass B 按 (cluster 序, alive<dead)
      走事件流维护死亡锚。显式 alive 出场消费死亡锚（复活既成事实·后续块不重复报）。
    诚实 skip 规则：出场条目无 status 字段（尸体/回忆常被 archivist 列出）→ 不判；
    该 cluster brief 有 dead_character 类 gate_waivers（闪回声明）→ 豁免留痕。
    """
    check = {"name": "dead_reappeared", "status": "ran", "skipped_entries": 0, "waived": []}
    findings = []
    if not any(isinstance(a[2].get("characters"), list) for a in archives):
        check["status"] = "skipped"
        check["skip_reason"] = "全部 archive 缺 characters 字段（archivist 合约破损另有 apply_archive 硬停·此处不越权）"
        return findings, check

    # pass A：全书角色名册 + archive status 事件
    names_of: dict[str, set] = defaultdict(set)
    events: dict[str, list] = defaultdict(list)  # key -> [(num, order, kind, payload)]
    for num, cid, archive in archives:
        chars = archive.get("characters")
        if not isinstance(chars, list):
            check["skipped_entries"] += 1
            continue
        for entry in chars:
            if not isinstance(entry, dict):
                continue
            key = _char_key(entry)
            if key is None:
                continue
            names_of[key] |= {s for s in (str(entry.get("name") or "").strip(),
                                          str(entry.get("id") or "").strip()) if s}
            status = entry.get("status")
            if _is_alive_status(status):
                events[key].append((num, 0, "alive", entry.get("name")))
            elif _is_dead_status(status):
                events[key].append((num, 1, "dead",
                                    f"{cid}_archive.characters.status={status}"))

    # locked_facts 死亡声明（辅助锚·fact 文本按名匹配·同 alive<dead 结算序）
    for num in sorted(ec_by_num):
        for lf in ec_by_num[num].get("locked_facts") or []:
            fact = lf.get("fact") if isinstance(lf, dict) else (lf if isinstance(lf, str) else None)
            if not fact or not any(w in str(fact) for w in _DEATH_FACT_WORDS):
                continue
            for key, names in names_of.items():
                if any(name and len(name) >= 2 and name in str(fact) for name in names):
                    events[key].append((num, 1, "dead", f"事件簇.locked_facts「{str(fact)[:40]}」"))

    # pass B：按时间线走事件流
    for key in sorted(events):
        anchor = None  # (num, source)
        for num, _order, kind, payload in sorted(events[key], key=lambda e: (e[0], e[1])):
            if kind == "alive":
                if anchor is not None and num > anchor[0]:
                    display = payload or (sorted(names_of[key])[0] if names_of[key] else key)
                    item = {
                        "severity": "advisory",
                        "code": CODE,
                        "conflict_type": "dead_reappeared",
                        "entity": key,
                        "name": display,
                        "death_cluster": anchor[0],
                        "death_source": anchor[1],
                        "reappear_cluster": num,
                        "suggestion": (
                            f"角色「{display}」在 cluster_{anchor[0]:03d} 已有死亡记录"
                            f"（{anchor[1]}），却在 cluster_{num:03d} 的 archive.characters 以 "
                            f"status=alive 再出场——若是闪回/亡灵/复活等叙事设计，请在该 cluster "
                            f"brief 的 gate_waivers 声明 dead_character 豁免；否则疑似跨块穿帮"),
                    }
                    waiver = _flashback_waived(ec_by_num.get(num), names_of[key])
                    if waiver is not None:
                        check["waived"].append({**item, "waived_by": {
                            "type": waiver.get("type"), "target": waiver.get("target"),
                            "reason": waiver.get("reason")}})
                    else:
                        findings.append(item)
                    anchor = None  # 复活既成事实·后续块不重复报
            elif kind == "dead" and anchor is None:
                anchor = (num, payload)
    return findings, check


# ───────────────────────── ② 道具同 cluster 双持有者 ─────────────────────────

def scan_item_dual_holder(archives: list) -> tuple[list, dict]:
    """同一 cluster 的 archive.items 内，同一道具（按 id·缺 id 按 name）出现 ≥2 个
    不同非空 holder → 矛盾。holder 字段缺失/空 = 未声明持有 → 诚实不判。"""
    check = {"name": "item_dual_holder", "status": "ran", "skipped_entries": 0}
    findings = []
    saw_items_field = False
    for num, cid, archive in archives:
        items = archive.get("items")
        if not isinstance(items, list):
            check["skipped_entries"] += 1
            continue
        saw_items_field = True
        holders: dict[str, dict] = defaultdict(lambda: {"holders": set(), "name": ""})
        for it in items:
            if not isinstance(it, dict):
                continue
            key = str(it.get("id") or "").strip() or str(it.get("name") or "").strip()
            holder = str(it.get("holder") or "").strip()
            if not key or not holder:
                continue
            holders[key]["holders"].add(holder)
            holders[key]["name"] = holders[key]["name"] or str(it.get("name") or "")
        for key, info in holders.items():
            if len(info["holders"]) > 1:
                findings.append({
                    "severity": "advisory",
                    "code": CODE,
                    "conflict_type": "item_dual_holder",
                    "entity": key,
                    "name": info["name"],
                    "cluster": num,
                    "holders": sorted(info["holders"]),
                    "suggestion": (
                        f"道具「{info['name'] or key}」在 {cid} 的 archive.items 同时记录了 "
                        f"{len(info['holders'])} 个持有者 {sorted(info['holders'])}"
                        f"——同块双持有者疑似穿帮（块内转手应只留最终持有者）"),
                })
    if not saw_items_field:
        check["status"] = "skipped"
        check["skip_reason"] = "全部 archive 缺 items 字段"
    return findings, check


# ───────────────────────── ③ 关系状态回跳无事件支撑 ─────────────────────────

def scan_relationship_regressed(archives: list) -> tuple[list, dict]:
    """(from,to) 边的 type 时间线：T1 → T2 → 回到 T1，且回跳块的 archive 条目
    无 note（事件支撑）→ 矛盾嫌疑。type 字段缺失/空 → 该条诚实 skip。"""
    check = {"name": "relationship_regressed", "status": "ran", "skipped_entries": 0}
    findings = []
    saw_rels_field = False
    # (from,to) -> [(num, type, note)]
    timeline: dict[tuple, list] = defaultdict(list)
    for num, cid, archive in archives:
        rels = archive.get("relationships")
        if not isinstance(rels, list):
            check["skipped_entries"] += 1
            continue
        saw_rels_field = True
        for r in rels:
            if not isinstance(r, dict):
                continue
            frm, to = str(r.get("from") or "").strip(), str(r.get("to") or "").strip()
            rtype = str(r.get("type") or "").strip()
            if not frm or not to or not rtype:
                continue
            timeline[(frm, to)].append((num, rtype, str(r.get("note") or "").strip()))

    for (frm, to), events in sorted(timeline.items()):
        events.sort(key=lambda t: t[0])
        # 压缩连续同 type（同状态多块重申不算变迁）
        states = []
        for num, rtype, note in events:
            if not states or states[-1][1] != rtype:
                states.append((num, rtype, note))
        for i in range(2, len(states)):
            cur_num, cur_type, cur_note = states[i]
            prev_types = {s[1] for s in states[:i - 1]}  # 隔了至少一个不同状态
            if cur_type in prev_types and not cur_note:
                back_to = next(s for s in states[:i - 1] if s[1] == cur_type)
                findings.append({
                    "severity": "advisory",
                    "code": CODE,
                    "conflict_type": "relationship_regressed",
                    "entity": f"{frm}->{to}",
                    "cluster": cur_num,
                    "regressed_to_type": cur_type,
                    "intermediate_type": states[i - 1][1],
                    "first_seen_cluster": back_to[0],
                    "suggestion": (
                        f"关系 {frm}→{to} 的 type 在 cluster_{cur_num:03d} 回跳到"
                        f"「{cur_type}」（cluster_{back_to[0]:03d} 旧状态·中间已变为"
                        f"「{states[i - 1][1]}」），且该块 archive 条目无 note 事件支撑"
                        f"——关系回跳应有正文事件（和解/反目重演）留痕"),
                })
    if not saw_rels_field:
        check["status"] = "skipped"
        check["skip_reason"] = "全部 archive 缺 relationships 字段"
    return findings, check


# ───────────────────────── main ─────────────────────────

def run_scan(project_root: Path) -> dict:
    archives = load_archives(project_root)
    ec_by_num = _event_clusters(project_root)
    report = {
        "scan_type": "entity_state_graph",
        "mode": _mode(),
        "clusters_scanned": [cid for _, cid, _ in archives],
        "checks": [],
        "findings": [],
        "waived": [],
        "summary": {"advisory": 0},
        "_doc": ("A10 Magnet/Atlas 实体状态时间线图矛盾·确定性零 LLM·恒 advisory"
                 "（图推断近似性+复活/闪回合法·北极星⑤只报告不裁决）·"
                 "与 pre_write_gate 分工：gate 拦写前 brief·本聚合器查写后账本时间线"),
    }
    if not archives:
        report["skipped"] = True
        report["skip_reason"] = "无 cluster archive 留档（_数据库/.wal/cluster_*_archive.json）"
        return report

    f1, c1 = scan_dead_reappeared(archives, ec_by_num)
    f2, c2 = scan_item_dual_holder(archives)
    f3, c3 = scan_relationship_regressed(archives)
    report["findings"] = f1 + f2 + f3
    report["waived"] = c1.pop("waived", [])
    report["checks"] = [c1, c2, c3]
    report["summary"]["advisory"] = len(report["findings"])
    return report


def main():
    ap = argparse.ArgumentParser(description="实体状态时间线图矛盾聚合（A10·全书史·无窗口）")
    ap.add_argument("project")
    args = ap.parse_args()

    mode = _mode()
    if mode == "off":
        print("[OFF] ENTITY_STATE_GRAPH_MODE=off")
        sys.exit(0)

    project_root = Path(args.project)
    report = run_scan(project_root)
    if report.get("skipped"):
        print(f"[SKIP] {report['skip_reason']}")
        sys.exit(0)

    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report["scan_ts"] = ts
    out_path = out_dir / f"entity_state_graph_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    n = report["summary"]["advisory"]
    ran = sum(1 for c in report["checks"] if c["status"] == "ran")
    print(f"[entity_state_graph] {len(report['clusters_scanned'])} cluster · "
          f"{ran}/3 检查可用 · {n} advisory · {len(report['waived'])} waived")
    for f in report["findings"][:6]:
        print(f"  [ADVISORY] {f['conflict_type']}: {f.get('suggestion', '')[:80]}")
    for c in report["checks"]:
        if c["status"] == "skipped":
            print(f"  [SKIP-CHECK] {c['name']}: {c.get('skip_reason', '')}")
    print(f"报告: {out_path}")

    if mode == "shadow":
        for f in report["findings"]:
            print(f"[SHADOW] entity_state_graph: {f['conflict_type']} — 不上报", file=sys.stderr)
        sys.exit(0)
    sys.exit(1 if n > 0 else 0)


if __name__ == "__main__":
    main()
