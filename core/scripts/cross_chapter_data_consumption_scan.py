"""cross_chapter_data_consumption_scan.py — v21 数据声明 vs 实际写入对账（CCR1+2+6+11）

合并 4 类同型问题（"声明了什么 vs 实际写出来什么"）：

A. ASPECT_CONTINUITY — 角色烙印 active_aspects 跨章呼应
   每个 active aspect 进入后所有后续章应有体现（_changes.json.factual.aspects_addressed
   或正文匹配 narrative_constraints）；连续 3 章未呼应 → advisory

B. CLOCK_ADDRESSING — urgent clock 暗示埋设
   时钟表中 remaining ≤ 2 的 urgent clock，本章应在 clocks_addressed 出现；
   连续 2 章 urgent 未提 → warning

C. HEART_EVENT_CONSISTENCY — 揭密后保持感
   群像档.json 中 consumed=true 的 heart_event，对应 NPC 在后续 ≥3 章出现时
   行为应反映 reveal 信息（关键字检测：reveal 含的关键词 vs NPC 对话/描述）

D. FATE_DICE_CONSUMPTION — 抽中事件兑现
   事件池.drawn_events_log 中抽中的事件，对应章 _changes.json.factual.fate_dice_consumed
   应记录该 event_id；正文应含 physical_evidence 多数项

输出：_数据库/.cross_chapter_scan/data_consumption_<ts>.json
退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_chapters(project_root: Path, last_n: int = 10) -> list[int]:
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-last_n:] if chs else []


def read_changes(project_root: Path, ch: int) -> dict:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    return load_json(p, {})


def read_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# ---------- A. ASPECT_CONTINUITY ----------

def scan_aspect_continuity(project_root: Path, chapters: list[int]) -> list[dict]:
    aspects_path = project_root / "_数据库" / "角色烙印.json"
    if not aspects_path.exists():
        return []
    data = load_json(aspects_path, {})
    findings = []
    for char_name, char_data in (data.get("characters") or {}).items():
        actives = char_data.get("active_aspects") or []
        for aspect in actives:
            aid = aspect.get("aspect_id")
            label = aspect.get("label", "?")
            acquired_ch = aspect.get("acquired_ch", 0)
            constraints = aspect.get("narrative_constraints", [])
            triggers = aspect.get("emotional_triggers", [])
            # 仅扫 acquired_ch 之后的章
            relevant_chs = [c for c in chapters if c >= acquired_ch]
            if len(relevant_chs) < 3:
                continue
            # 检查每章是否呼应
            no_address_streak = 0
            last_addressed = None
            for ch in relevant_chs:
                changes = read_changes(project_root, ch)
                addressed = (changes.get("factual", {}) or {}).get("aspects_addressed", []) or []
                hit_in_changes = any(a.get("aspect_id") == aid for a in addressed if isinstance(a, dict))
                # fallback：扫正文是否含 narrative_constraints/triggers 关键短语
                hit_in_text = False
                if not hit_in_changes:
                    text = read_text(project_root, ch)
                    keywords = []
                    for c_str in constraints[:3]:
                        keywords.extend(re.findall(r"[一-鿿]{2,4}", c_str)[:3])
                    for t_str in triggers[:3]:
                        keywords.extend(re.findall(r"[一-鿿]{2,4}", t_str)[:2])
                    hit_in_text = any(kw in text for kw in keywords if len(kw) >= 2)
                if hit_in_changes or hit_in_text:
                    no_address_streak = 0
                    last_addressed = ch
                else:
                    no_address_streak += 1
                    if no_address_streak >= 3:
                        findings.append({
                            "severity": "advisory",
                            "code": "ASPECT_NOT_ADDRESSED",
                            "character": char_name,
                            "aspect_id": aid,
                            "label": label,
                            "since_ch": ch - 2,
                            "current_ch": ch,
                            "last_addressed_ch": last_addressed,
                            "suggestion": f"{char_name} aspect「{label}」连续 ≥3 章无呼应，应在动作/触景细节带入",
                        })
                        no_address_streak = 0  # 报后重置避免刷屏
    return findings


# ---------- B. CLOCK_ADDRESSING ----------

def scan_clock_addressing(project_root: Path, chapters: list[int]) -> list[dict]:
    clocks_path = project_root / "_数据库" / "时钟表.json"
    if not clocks_path.exists():
        return []
    data = load_json(clocks_path, {})
    findings = []
    # 找出 urgent clock（remaining ≤ 2）
    urgent_clocks = []
    for c in data.get("clocks", []):
        if c.get("status") != "active":
            continue
        ticks = c.get("ticks", 0)
        max_v = c.get("max", 99)
        if max_v - ticks <= 2:
            urgent_clocks.append(c)
    if not urgent_clocks:
        return []
    # 检查近 2 章是否都未提及
    recent = chapters[-2:] if len(chapters) >= 2 else chapters
    for clock in urgent_clocks:
        cid = clock.get("clock_id")
        addressed_chs = []
        for ch in recent:
            changes = read_changes(project_root, ch)
            addr_list = (changes.get("factual", {}) or {}).get("clocks_addressed", []) or []
            if any(a.get("clock_id") == cid for a in addr_list if isinstance(a, dict)):
                addressed_chs.append(ch)
        if not addressed_chs and len(recent) >= 2:
            findings.append({
                "severity": "warning",
                "code": "URGENT_CLOCK_IGNORED",
                "clock_id": cid,
                "label": clock.get("label"),
                "remaining": clock.get("max", 0) - clock.get("ticks", 0),
                "trigger_on_max": clock.get("trigger_on_max"),
                "checked_chs": recent,
                "suggestion": f"urgent clock「{clock.get('label')}」({cid}) 近 {len(recent)} 章无暗示，下章必埋",
            })
    return findings


# ---------- C. HEART_EVENT_CONSISTENCY ----------

def scan_heart_event_consistency(project_root: Path, chapters: list[int]) -> list[dict]:
    ensemble_path = project_root / "_数据库" / "群像档.json"
    if not ensemble_path.exists():
        return []
    data = load_json(ensemble_path, {})
    findings = []
    for npc, npc_data in (data.get("characters") or {}).items():
        for he in npc_data.get("heart_events", []) or []:
            if not he.get("consumed", False):
                continue
            consumed_at = he.get("consumed_at_ch", 0)
            if not consumed_at:
                continue
            reveal = he.get("reveal", "")
            # 在 reveal 中提取关键 2-3 字短语
            reveal_kws = re.findall(r"[一-鿿]{2,5}", reveal)[:5]
            if not reveal_kws:
                continue
            # 后续 ≥3 章中 NPC 出现的章节
            post_chs = [c for c in chapters if c > consumed_at][:5]
            if len(post_chs) < 3:
                continue
            npc_appearance_chs = []
            kw_hit_chs = []
            for ch in post_chs:
                text = read_text(project_root, ch)
                if npc in text:
                    npc_appearance_chs.append(ch)
                    if any(kw in text for kw in reveal_kws):
                        kw_hit_chs.append(ch)
            if len(npc_appearance_chs) >= 2 and not kw_hit_chs:
                findings.append({
                    "severity": "advisory",
                    "code": "HEART_EVENT_FORGOTTEN",
                    "npc": npc,
                    "event_id": he.get("event_id"),
                    "consumed_at_ch": consumed_at,
                    "post_appearance_chs": npc_appearance_chs,
                    "suggestion": f"{npc} 在 ch{consumed_at} 揭密「{he.get('tier_label')}」后出现 ≥2 章但 reveal 关键词从未再出现 → 角色无保持感",
                })
    return findings


# ---------- D. FATE_DICE_CONSUMPTION ----------

def scan_fate_dice_consumption(project_root: Path, chapters: list[int]) -> list[dict]:
    pool_path = project_root / "_数据库" / "事件池.json"
    if not pool_path.exists():
        return []
    pool = load_json(pool_path, {})
    drawn_log = pool.get("drawn_events_log", [])
    if not drawn_log:
        return []
    events_by_id = {e.get("event_id"): e for e in pool.get("events", [])}
    findings = []
    for d in drawn_log:
        ch = d.get("ch")
        eid = d.get("event_id")
        if ch not in chapters:
            continue
        event_def = events_by_id.get(eid, {})
        evidence = event_def.get("physical_evidence", []) or []
        # 检查 _changes.json.fate_dice_consumed
        changes = read_changes(project_root, ch)
        consumed = (changes.get("factual", {}) or {}).get("fate_dice_consumed")
        text = read_text(project_root, ch)
        # 文本中 evidence 命中数
        evidence_kws = []
        for ev in evidence:
            evidence_kws.extend(re.findall(r"[一-鿿]{2,4}", ev)[:2])
        evidence_hits = sum(1 for kw in evidence_kws if kw in text)
        ratio = evidence_hits / max(1, len(evidence_kws))
        if consumed != eid:
            findings.append({
                "severity": "warning",
                "code": "FATE_DICE_NOT_DECLARED",
                "ch": ch,
                "event_id": eid,
                "label": d.get("label"),
                "suggestion": f"ch{ch} 抽中 {eid} 但 _changes.factual.fate_dice_consumed 未标记为该 id",
            })
        if evidence_kws and ratio < 0.3:
            findings.append({
                "severity": "advisory",
                "code": "FATE_DICE_EVIDENCE_LOW",
                "ch": ch,
                "event_id": eid,
                "evidence_hit_ratio": round(ratio, 2),
                "suggestion": f"ch{ch} 抽中 {eid} 但正文 physical_evidence 命中率仅 {round(ratio*100)}%（应 ≥30%）",
            })
    return findings


# ---------- main ----------

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

    all_findings = []
    all_findings.extend(scan_aspect_continuity(project_root, chapters))
    all_findings.extend(scan_clock_addressing(project_root, chapters))
    all_findings.extend(scan_heart_event_consistency(project_root, chapters))
    all_findings.extend(scan_fate_dice_consumption(project_root, chapters))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in all_findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in all_findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "data_consumption",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": all_findings,
        "summary": summary,
    }
    out_path = out_dir / f"data_consumption_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[data_consumption] {len(chapters)} 章: {summary['warning']} warning / {summary['advisory']} advisory")
    for f in all_findings[:8]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
