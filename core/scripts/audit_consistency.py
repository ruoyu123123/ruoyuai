#!/usr/bin/env python3
"""
audit_consistency.py — 章后反向一致性审计（第 4 层防御）

用法：
  python audit_consistency.py <项目路径> [--window N]  # 默认 window=5
  例: python audit_consistency.py "示例书名" --window 10

抓 6 类累积漂移（单章 validate 看不出）：
  1. FORESHADOWING_OVERDUE_PILE — 到期但未回收的伏笔堆积
  2. LOCKED_FACTS_DRIFT — 外貌/物件描写逐章偏离
  3. RELATIONSHIP_INTERACTION_GAP — 关系数值与实际互动方向相反
  4. SUMMARY_DB_DESYNC — 章纲摘要提到的变更未落进对应 JSON
  5. PROPAGATION_DEBT_BACKLOG — 传播债务堆积
  6. BANNED_WORD_TREND — 禁用词系统性出现（单章不到阈值）

输出：
  <项目路径>/_数据库/.audit/audit_<时间戳>.json
  同时打印分级报告到 stdout

退出码：0=清洁, 1=warning, 2=error 或更严重
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写


BANNED_WORDS = [
    "顿时", "紧锁", "显然", "似乎", "此刻", "淡淡",
    "心中一凛", "眼中闪过一丝", "微微挑眉", "仿佛",
    "嘴角勾起一抹", "深吸一口气", "缓缓地说", "沉吟片刻",
    "与此同时", "值得一提的是", "不仅如此", "然而", "事实上",
]

POSITIVE_WORDS = ["笑", "肩", "握手", "并肩", "帮", "救", "靠近", "信任", "同意"]
NEGATIVE_WORDS = ["骂", "瞪", "推", "打", "冷笑", "讽", "怒", "背叛", "怀疑", "拒绝"]


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_file(project_root: Path, ch: int) -> Path | None:
    """v18：统一走 cio.find_body_file（兼容嵌套/平铺多布局）。
    旧实现只 glob 平铺布局，v18 标准嵌套布局 章节/第NNN章/ 定位不到。"""
    return cio.find_body_file(project_root, ch)


class ChapterSnapshot:
    """近 window 章的原始数据快照。
    v18：正文走 cio.read_body（v18 分离稿直读 / 旧混合稿自动剥离 CHANGES），
    CHANGES 走 cio.read_changes（优先 _changes.json / 旧混合稿 legacy 解析）。
    不再各自 split，杜绝 CHANGES JSON 污染正文。"""
    def __init__(self, project_root: Path, chapters: list[int]):
        self.root = project_root
        self.chapters: list[int] = chapters
        self.texts: dict[int, str] = {}
        self.changes: dict[int, dict] = {}
        for ch in chapters:
            try:
                body = cio.read_body(project_root, ch)
            except FileNotFoundError:
                continue
            self.texts[ch] = body
            ch_data = cio.read_changes(project_root, ch).get("factual", {})
            if ch_data:
                self.changes[ch] = ch_data


# ============ 6 类审计 ============

def audit_foreshadowing_pile(db: Path, current_ch: int) -> list[dict]:
    """到期但未回收的伏笔堆积（单章只报本章到期，累积起来要看）。"""
    data = load_json(db / "伏笔表.json", {})
    errs = []
    overdue = []
    for p in data.get("promises", []):
        if p.get("resolved"):
            continue
        due_by = p.get("due_by", 999)
        if due_by < current_ch:
            overdue.append({
                "id": p.get("id"),
                "tier": p.get("tier"),
                "due_by": due_by,
                "overdue_by": current_ch - due_by,
                "desc": p.get("description"),
            })
    tier1_pile = [o for o in overdue if o["tier"] == 1]
    tier2_pile = [o for o in overdue if o["tier"] == 2]
    if tier1_pile:
        errs.append({
            "code": "FORESHADOWING_TIER1_OVERDUE",
            "severity": "error",
            "msg": f"有 {len(tier1_pile)} 条 Tier-1 伏笔过期未回收（最久逾期 {max(o['overdue_by'] for o in tier1_pile)} 章）",
            "items": tier1_pile,
            "fix_hint": "立即在后续章节安排回收，或将 tier 降级为 2/3 并延长 due_by",
        })
    if tier2_pile:
        errs.append({
            "code": "FORESHADOWING_TIER2_OVERDUE",
            "severity": "warning",
            "msg": f"有 {len(tier2_pile)} 条 Tier-2 伏笔过期未回收",
            "items": tier2_pile,
            "fix_hint": "安排回收或延长 due_by",
        })
    # 到期未过期但累积太多（软性预警）
    upcoming = [p for p in data.get("promises", [])
                if not p.get("resolved")
                and current_ch <= p.get("due_by", 999) <= current_ch + 3]
    if len(upcoming) >= 8:
        errs.append({
            "code": "FORESHADOWING_UPCOMING_CROWDED",
            "severity": "warning",
            "msg": f"未来 3 章有 {len(upcoming)} 条伏笔到期，回收压力大",
            "fix_hint": "考虑分散 due_by，或提前在本章开始铺回收",
        })
    return errs


def audit_locked_facts_drift(snap: ChapterSnapshot, db: Path) -> list[dict]:
    """locked_facts 逐章偏移：
    对每个主角，在 window 章范围扫描 locked_facts 中的高辨识关键词。
    如果某关键词在部分章出现、部分章出现「反义词」，视为漂移信号。
    """
    errs = []
    cards = load_json(db / "人物卡.json", {}).get("characters", [])
    OPPOSITES = {
        "左手": "右手", "右手": "左手",
        "黑色": "白色", "白色": "黑色",
        "灰色": "棕色", "棕色": "灰色",
        "短发": "长发", "长发": "短发",
    }
    for c in cards:
        name = c.get("name")
        if not name:
            continue
        for fact in c.get("locked_facts", []):
            for kw, opp in OPPOSITES.items():
                if kw not in fact:
                    continue
                # 扫描 window 章
                kw_chs, opp_chs = [], []
                for ch, body in snap.texts.items():
                    # 必须围绕该角色描写附近出现
                    # 简化：在 body 中查找「name ... kw/opp」的 20 字窗口
                    for m in re.finditer(re.escape(name), body):
                        window = body[max(0, m.start()-20):m.end()+80]
                        if kw in window:
                            kw_chs.append(ch)
                        if opp in window:
                            opp_chs.append(ch)
                # 只要 opp_chs 非空就是漂移
                if opp_chs:
                    errs.append({
                        "code": "LOCKED_FACT_DRIFT",
                        "severity": "error",
                        "msg": f"角色「{name}」locked_fact 含「{kw}」，但第 {opp_chs} 章附近出现「{opp}」",
                        "fix_hint": f"用 /reconcile 审查这些章节的描写，修正为「{kw}」",
                    })
    return errs


def audit_relationship_interaction(snap: ChapterSnapshot, db: Path) -> list[dict]:
    """关系数值与实际互动方向相反：
    对每对出场角色，统计近 window 章里他们共现段落中正/负向词的比例，
    与关系.json 的 affinity 做方向对比。
    """
    errs = []
    rels = load_json(db / "关系.json", {}).get("relationships", [])
    id_map = {}
    for c in load_json(db / "人物卡.json", {}).get("characters", []):
        if c.get("id") and c.get("name"):
            id_map[c["id"]] = c["name"]
    for r in rels:
        a_id, b_id = r.get("from"), r.get("to")
        affinity = r.get("affinity", 0)
        if abs(affinity) < 3:
            continue  # 中性关系不检测
        a = id_map.get(a_id, a_id)
        b = id_map.get(b_id, b_id)
        pos, neg, cooccur = 0, 0, 0
        for body in snap.texts.values():
            for para in body.split("\n\n"):
                if a in para and b in para:
                    cooccur += 1
                    pos += sum(para.count(w) for w in POSITIVE_WORDS)
                    neg += sum(para.count(w) for w in NEGATIVE_WORDS)
        if cooccur < 2:
            continue  # 共现太少，样本不足
        # 方向判定
        actual = pos - neg
        conflict = (affinity >= 5 and actual < -2) or (affinity <= -5 and actual > 2)
        if conflict:
            errs.append({
                "code": "RELATIONSHIP_MISMATCH",
                "severity": "warning",
                "msg": f"{a} ↔ {b}: 关系.json affinity={affinity}，近 {len(snap.chapters)} 章实际互动 pos={pos}/neg={neg}",
                "fix_hint": "用 /reconcile 对齐关系值与互动实情，或更新关系.json",
            })
    return errs


def audit_summary_db_desync(snap: ChapterSnapshot, db: Path) -> list[dict]:
    """章纲摘要提到的变更未落进对应 JSON。
    简化实现：扫描 window 章摘要中的「去/到/进入」+地点名，看地图.json 是否记录了对应角色位置。
    """
    errs = []
    summaries = load_json(db / "章纲摘要.json", {}).get("chapters", [])
    relevant = [s for s in summaries if s.get("ch", s.get("chapter", 0)) in snap.chapters]
    locations = load_json(db / "地图.json", {}).get("locations", [])
    loc_names = {l.get("name") for l in locations if l.get("name")}
    if not loc_names:
        return errs

    for s in relevant:
        summary = s.get("summary", "")
        ch = s.get("ch", s.get("chapter"))
        for loc in loc_names:
            if loc not in summary:
                continue
            # 摘要提到该地点 → 检查该章 CHANGES.location_changes 或 character_movements 是否记录
            changes = snap.changes.get(ch, {})
            mentioned_in_changes = (
                any(loc in str(c) for c in changes.get("location_changes", []))
                or any(loc in str(c) for c in changes.get("character_movements", []))
            )
            if not mentioned_in_changes:
                errs.append({
                    "code": "SUMMARY_LOC_NOT_IN_CHANGES",
                    "severity": "warning",
                    "msg": f"第 {ch} 章摘要提到「{loc}」，但 CHANGES 未记录相关地点/移动",
                    "fix_hint": f"补 CHANGES.character_movements 或 location_changes 再跑 save-state",
                })
    return errs


def audit_propagation_debt(db: Path, current_ch: int) -> list[dict]:
    """传播债务堆积。"""
    errs = []
    progress = load_json(db / "进度.json", {})
    debts = progress.get("propagation_debt", [])
    pending = [d for d in debts if d.get("status") == "pending"]
    if len(pending) >= 5:
        errs.append({
            "code": "PROPAGATION_DEBT_BACKLOG",
            "severity": "error",
            "msg": f"传播债务堆积 {len(pending)} 条 pending，需要清偿",
            "items": pending[:10],
            "fix_hint": "触发 save-state 第 9 步深度维护，或手动 /reconcile 清偿",
        })
    elif len(pending) >= 3:
        errs.append({
            "code": "PROPAGATION_DEBT_WARN",
            "severity": "warning",
            "msg": f"传播债务 {len(pending)} 条 pending",
            "fix_hint": "建议在下次 save-state 深度维护时清偿",
        })
    return errs


def audit_banned_word_trend(snap: ChapterSnapshot) -> list[dict]:
    """禁用词趋势：近 window 章总量统计。单章 1 次不报，但 5 章 3 次就是系统问题。"""
    errs = []
    counter: dict[str, dict[int, int]] = {}
    for w in BANNED_WORDS:
        for ch, body in snap.texts.items():
            cnt = body.count(w)
            if cnt == 0:
                continue
            counter.setdefault(w, {})[ch] = cnt
    for w, per_ch in counter.items():
        total = sum(per_ch.values())
        covered = len(per_ch)
        if total >= 3 and covered >= 2:
            errs.append({
                "code": "BANNED_WORD_TREND",
                "severity": "warning",
                "msg": f"禁用词「{w}」在近 {len(snap.chapters)} 章内累计出现 {total} 次（覆盖 {covered} 章）",
                "per_chapter": per_ch,
                "fix_hint": f"系统性习惯，建议在作者风格.json.anti_patterns 强化；批量修正历史章节",
            })
    return errs


# ============ 主流程 ============

def run_audit(project_root: Path, window: int) -> dict:
    db = project_root / "_数据库"
    progress = load_json(db / "进度.json", {})
    completed = progress.get("completed", 0)
    current = progress.get("current", completed + 1)

    if completed == 0:
        return {
            "project": project_root.name,
            "audited_at": datetime.now().isoformat(timespec="seconds"),
            "window": window,
            "completed": 0,
            "passed": True,
            "errors": [],
            "note": "尚无已完成章节，跳过审计",
        }

    start = max(1, completed - window + 1)
    chapters = list(range(start, completed + 1))
    snap = ChapterSnapshot(project_root, chapters)

    all_errs: list[dict] = []
    all_errs += audit_foreshadowing_pile(db, current)
    all_errs += audit_locked_facts_drift(snap, db)
    all_errs += audit_relationship_interaction(snap, db)
    all_errs += audit_summary_db_desync(snap, db)
    all_errs += audit_propagation_debt(db, current)
    all_errs += audit_banned_word_trend(snap)

    fatal = [e for e in all_errs if e["severity"] == "fatal"]
    errors = [e for e in all_errs if e["severity"] == "error"]
    warnings = [e for e in all_errs if e["severity"] == "warning"]

    return {
        "project": project_root.name,
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "window": window,
        "completed": completed,
        "audited_chapters": chapters,
        "fatal_count": len(fatal),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(fatal) == 0 and len(errors) == 0,
        "errors": all_errs,
    }


def format_report(result: dict) -> str:
    lines = []
    lines.append(f"══ 审计报告: {result['project']} ══")
    if result.get("note"):
        lines.append(result["note"])
        return "\n".join(lines)
    lines.append(f"已完成 {result['completed']} 章 | 窗口最近 {result['window']} 章 | "
                 f"审计章节 {result['audited_chapters']}")
    lines.append(f"致命 {result.get('fatal_count', 0)} | "
                 f"错误 {result.get('error_count', 0)} | "
                 f"警告 {result.get('warning_count', 0)}")
    lines.append("")
    if not result["errors"]:
        lines.append("✅ 清洁，无累积漂移")
        return "\n".join(lines)
    for e in result["errors"]:
        icon = {"fatal": "🔴", "error": "🟠", "warning": "🟡"}.get(e["severity"], "•")
        lines.append(f"{icon} [{e['code']}] {e['msg']}")
        if e.get("fix_hint"):
            lines.append(f"   → {e['fix_hint']}")
        if e.get("items"):
            for it in e["items"][:3]:
                lines.append(f"     · {it}")
            if len(e["items"]) > 3:
                lines.append(f"     · ... 共 {len(e['items'])} 条")
    lines.append("")
    lines.append("❌ 发现累积漂移，建议 /reconcile 处理" if not result["passed"]
                 else "🟡 有警告项，可择期处理")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目路径")
    ap.add_argument("--window", type=int, default=5, help="审计窗口（近 N 章），默认 5")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    result = run_audit(project_root, args.window)
    print(format_report(result))

    out_dir = project_root / "_数据库" / ".audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"audit_{ts}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n📄 机读报告: {out_path.relative_to(project_root)}")

    if result.get("fatal_count", 0) > 0:
        sys.exit(2)
    if not result["passed"]:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
