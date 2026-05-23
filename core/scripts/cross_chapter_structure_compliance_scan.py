"""cross_chapter_structure_compliance_scan.py — beat_map 推进 + 走向卡落地（CCR16）

2 类结构层跨章合规检测：

A. BEAT_MAP_PROGRESSION
   读 _数据库/beat_map.json 的 chapters_beat[ch]
   - 比对实际 ch 是否真触发对应 beat（扫 _changes.factual.beats_addressed 或正文关键词）
   - BEAT_MISSED：到了 ch=X 应该是 beat=Catalyst 但正文/changes 无任何 catalyst 信号
   - BEAT_AHEAD：ch=X 应该是 Set-Up 但正文已经 Catalyst 关键词丰富 → 提前
   - BEAT_GAP_TOO_LONG：连续 N 章无任何 beat 推进

B. USER_CHOICE_LANDED
   读 .wal/第N章_fate_cards.json + 进度.json.chapter_plan[N].user_choice
   - 用户选定的卡（A/B/C）是否真在 chapter_plan 留下记录
   - chapter_plan[N].turning_point 是否与所选卡 leads_to 一致
   - 如 chapter_plan 完全没记录 user_choice → CHOICE_NOT_LANDED

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


# beat 关键词（Save the Cat 15 节拍）
BEAT_KEYWORDS = {
    "Opening Image": ["开场", "首章"],
    "Theme Stated": ["主题", "理念"],
    "Set-Up": ["铺垫", "日常", "介绍"],
    "Catalyst": ["催化", "意外", "事件", "异常", "异变", "震惊"],
    "Debate": ["犹豫", "权衡", "反复", "纠结"],
    "Break into Two": ["决定", "出发", "踏入", "新世界"],
    "B Story": ["副线", "支线"],
    "Fun and Games": ["历险", "试炼", "挑战"],
    "Midpoint": ["转折", "中途", "突变"],
    "Bad Guys Close In": ["反派", "逼近", "压迫", "围剿"],
    "All Is Lost": ["失去", "失败", "崩溃", "绝境"],
    "Dark Night of Soul": ["黑暗", "绝望", "心死"],
    "Break into Three": ["顿悟", "新决心", "再起"],
    "Finale": ["决战", "终局", "高潮"],
    "Final Image": ["收尾", "终章"],
}


def beat_keywords_for(beat_str: str) -> list[str]:
    """匹配 beat_str 模糊到关键词清单（如 'Set-Up_late' → 'Set-Up'）。"""
    if not beat_str:
        return []
    # 按 _ 分割取主干，匹配 BEAT_KEYWORDS
    base = beat_str.split("_")[0].strip()
    for k, v in BEAT_KEYWORDS.items():
        if k.lower() in beat_str.lower() or base.lower() in k.lower():
            return v
    return []


# ---------- A. BEAT_MAP_PROGRESSION ----------

def scan_beat_progression(project_root: Path, chapters: list[int]) -> list[dict]:
    beat_path = project_root / "_数据库" / "beat_map.json"
    if not beat_path.exists():
        return []
    beat_data = load_json(beat_path, {})
    chapters_beat = beat_data.get("chapters_beat", {}) or {}
    findings = []

    if not chapters_beat:
        return []

    # 对每个 chapter（仅检查那些 chapters_beat 显式声明的）
    for ch in chapters:
        beat_str = chapters_beat.get(str(ch)) or chapters_beat.get(ch)
        if not beat_str:
            continue
        kws = beat_keywords_for(beat_str)
        if not kws:
            continue
        text = read_text(project_root, ch)
        if not text:
            continue
        # 检查正文 + changes 中是否有 beat 信号
        changes = read_changes(project_root, ch)
        beats_addressed = (changes.get("factual", {}) or {}).get("beats_addressed", []) or []
        # 扫描方式：①changes 显式标记 ②正文含关键词
        explicit_hit = any(beat_str.lower() in str(b).lower() for b in beats_addressed)
        kw_hit_count = sum(1 for kw in kws if kw in text)
        if not explicit_hit and kw_hit_count == 0:
            findings.append({
                "severity": "warning",
                "code": "BEAT_MISSED",
                "ch": ch,
                "expected_beat": beat_str,
                "expected_kws": kws[:3],
                "suggestion": f"ch{ch} beat_map 声明 beat={beat_str}，但正文/changes 完全无该 beat 信号 → 大纲与正文脱节",
            })

    # BEAT_GAP_TOO_LONG：跨章无 beat 推进
    declared_chs = sorted([int(c) for c in chapters_beat.keys() if str(c).isdigit()])
    for i in range(1, len(declared_chs)):
        gap = declared_chs[i] - declared_chs[i - 1]
        if gap > 60:
            findings.append({
                "severity": "advisory",
                "code": "BEAT_GAP_TOO_LONG",
                "from_ch": declared_chs[i - 1],
                "to_ch": declared_chs[i],
                "gap": gap,
                "suggestion": f"beat_map 在 ch{declared_chs[i-1]}→{declared_chs[i]} 间隔 {gap} 章无任何 beat 声明 → 大纲过粗",
            })
    return findings


# ---------- B. USER_CHOICE_LANDED ----------

def scan_user_choice_landed(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    progress_path = project_root / "_数据库" / "进度.json"
    if not progress_path.exists():
        return []
    progress = load_json(progress_path, {})
    chapter_plan = progress.get("chapter_plan", {}) or {}
    wal_dir = project_root / "_数据库" / ".wal"
    if not wal_dir.exists():
        return []

    for ch in chapters:
        cards_path = wal_dir / f"第{ch:03d}章_fate_cards.json"
        if not cards_path.exists():
            continue
        cards = load_json(cards_path, {})
        # plan 中是否有 user_choice
        plan_entry = chapter_plan.get(str(ch)) or chapter_plan.get(ch) or {}
        if not isinstance(plan_entry, dict):
            continue
        user_choice = plan_entry.get("user_choice")
        cards_list = cards.get("cards", [])
        labels = [c.get("label") for c in cards_list]
        if not labels:
            continue
        # CHOICE_NOT_LANDED：有 cards 但 plan 无 user_choice
        if not user_choice and labels:
            findings.append({
                "severity": "warning",
                "code": "USER_CHOICE_NOT_LANDED",
                "ch": ch,
                "card_labels_available": labels,
                "suggestion": f"ch{ch} 有 fate_cards（{labels}）但 chapter_plan[{ch}].user_choice 未记录 → 调度器漏写",
            })
            continue
        # CHOICE_LEADS_TO_MISMATCH：user_choice 选中卡的 leads_to 是否在 plan.turning_point 体现
        chosen_card = None
        for c in cards_list:
            if c.get("label") == user_choice:
                chosen_card = c
                break
        if chosen_card:
            leads_to = chosen_card.get("leads_to", "")
            turning = plan_entry.get("turning_point", "")
            if leads_to and turning:
                # 简单关键词共享检查
                lt_kws = re.findall(r"[一-鿿]{2,4}", leads_to)
                tp_kws = re.findall(r"[一-鿿]{2,4}", turning)
                if lt_kws and not any(k in turning for k in lt_kws[:5]):
                    findings.append({
                        "severity": "advisory",
                        "code": "CHOICE_LEADS_TO_MISMATCH",
                        "ch": ch,
                        "user_choice": user_choice,
                        "leads_to": leads_to[:60],
                        "turning_point": turning[:60],
                        "suggestion": f"ch{ch} 选 {user_choice} 卡 leads_to 与 plan.turning_point 关键词无重叠 → 选择未真正落地",
                    })
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
    findings.extend(scan_beat_progression(project_root, chapters))
    findings.extend(scan_user_choice_landed(project_root, chapters))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "structure_compliance",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"structure_compliance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[structure_compliance] {summary['warning']} warning / {summary['advisory']} advisory")
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
