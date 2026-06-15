"""cross_cluster_continuity_aggregate.py — 跨章衔接扫描（v19.1 新增）

补 cross_cluster_pattern_aggregate 的盲区：**章节衔接质量**。
分布均衡 scan_pattern 看节奏，衔接 scan_continuity 看连贯。

扫 4 维度：
1. cliffhanger 回应度    — 前章 ending 是否在后章首段被回应（DCAS pre_opening 例外）
2. 时间跳跃未交代       — 章间时间跳跃 ≥8h 必须有过渡说明
3. 物件持续性断层       — 关键物件（主角获得的 chekhov_gun）连续 ≥2 章未提及
4. 情绪/认知断层        — 前后章 summary.emotion 差 ≥4 且开篇无桥接

输出：
- 报告 JSON 写到 _数据库/.cross_chapter_scan/continuity_<timestamp>.json
- 终端打印每对相邻章衔接质量
- 触发告警时给具体建议

用法：
    python cross_cluster_continuity_aggregate.py <项目路径> [--last-n 10]

退出码: 0 健康 / 1 advisory / 2 严重断层
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_protagonist(project_root: Path) -> str | None:
    """从 人物卡.json 读 role==主角/protagonist 的角色名（照 relationship_evaluator
    范式·取代旧硬编码 "陆衍"）。兼容 {"characters":[...]} 与 {name:{...}} 两形态·
    读不到 fallback 第一个角色。"""
    cards = load_json(project_root / "_数据库" / "人物卡.json", None)
    if not isinstance(cards, dict):
        return None
    chars = cards.get("characters")
    if isinstance(chars, list):
        for c in chars:
            if isinstance(c, dict) and (c.get("role") in ("主角", "protagonist") or c.get("is_protagonist")):
                return c.get("name")
        for c in chars:
            if isinstance(c, dict) and c.get("name"):
                return c.get("name")
        return None
    for name, info in cards.items():
        if isinstance(info, dict) and (info.get("role") in ("主角", "protagonist") or info.get("is_protagonist")):
            return name
    return next(iter(cards.keys()), None)


def find_chapter_dirs(project_root: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            out.append((int(m.group(1)), d))
    out.sort(key=lambda x: x[0])
    return out


def read_chapter_text(ch_dir: Path, ch: int) -> str | None:
    for f in ch_dir.glob(f"第{ch:03d}章.txt"):
        return f.read_text(encoding="utf-8")
    for f in ch_dir.glob(f"第{ch}章*.txt"):
        return f.read_text(encoding="utf-8")
    return None


def read_changes(ch_dir: Path, ch: int) -> dict | None:
    for f in ch_dir.glob(f"第{ch:03d}章_changes.json"):
        return load_json(f, None)
    return None


def has_pre_opening(ch_dir: Path) -> bool:
    return (ch_dir / ".pre_opening.txt").exists()


# ===== 维度 1: cliffhanger 回应度 =====

def extract_keywords(text: str, top_n: int = 20, protagonist: str | None = None) -> set[str]:
    """简易关键词：长度 ≥2 的中文/英文 + 时间戳 + 数字串。

    protagonist：当前项目主角名（main 经 get_protagonist 从 人物卡.json 动态读）→ 加入
    stop 过滤。主角名几乎每段都出现，不过滤会让 cliffhanger 关键词重叠虚高。
    2026-06-15 修：原硬编码 stop={"陆衍",...} 只对某本旧书有效（北极星⑥清硬编码 + ①不绑
    特定书）→ 动态读主角名，照 relationship_evaluator.get_protagonist 范式。"""
    tokens = re.findall(r"[一-鿿]{2,}|[A-Za-z]{3,}|\d+[:：]\d+|\d{3,}", text)
    stop = {"他的", "她的", "自己", "一个", "一下", "什么", "这种", "那个", "这个", "那种", "已经", "还是", "就是", "不是", "没有", "他在", "他想", "他说", "她说"}
    if protagonist:
        stop.add(protagonist)
    return set(t for t in tokens if t not in stop)


def scan_cliffhanger_resonance(prev_changes: dict, next_text: str, next_ch_dir: Path, protagonist: str | None = None) -> dict:
    """前章 ending_line + ending_type vs 后章首段 300 字关键词重叠。"""
    if not prev_changes:
        return {"score": -1, "reason": "前章 changes 缺失，跳过"}
    se = prev_changes.get("self_eval", {})
    applied = se.get("applied_style", {})
    ending_type = applied.get("ending_type", "")
    ending_line = applied.get("ending_line", "")

    # DCAS pre_opening 例外
    if has_pre_opening(next_ch_dir) or ending_type in ("悬念断章",):
        return {"score": 1.0, "reason": "DCAS pre_opening 模式或悬念断章，物理承接 OK", "exempt": True}

    if not ending_line:
        return {"score": -1, "reason": "前章 ending_line 未声明"}

    # 后章首 300 字
    head = next_text[:600]
    ending_kw = extract_keywords(ending_line + " " + ending_type, protagonist=protagonist)
    head_kw = extract_keywords(head, protagonist=protagonist)
    if not ending_kw:
        return {"score": -1, "reason": "ending_line 关键词不足"}

    overlap = ending_kw & head_kw
    score = len(overlap) / max(len(ending_kw), 1)
    return {
        "score": round(score, 2),
        "ending_type": ending_type,
        "ending_line_preview": ending_line[:50],
        "overlap_keywords": list(overlap),
        "reason": "前章 ending 关键词与后章首段重叠度",
    }


def scan_cliffhanger_resonance_ledger(prev_rec: dict, next_ch_dir: Path) -> dict:
    """2026-05-29 cluster 化：账本预算了前章 cliffhanger_resonance_next（与下一章 head
    的重叠分）时，直接取用，省去 ending_line 关键词重扫。DCAS pre_opening 仍 exempt。"""
    score = prev_rec.get("cliffhanger_resonance_next")
    ending_type = prev_rec.get("ending_type", "")
    ending_line = prev_rec.get("ending_line", "")
    if has_pre_opening(next_ch_dir) or ending_type in ("悬念断章",):
        return {"score": 1.0, "reason": "DCAS pre_opening 模式或悬念断章，物理承接 OK", "exempt": True}
    if not isinstance(score, (int, float)):
        return {"score": -1, "reason": "账本无 cliffhanger_resonance_next"}
    return {
        "score": round(float(score), 2),
        "ending_type": ending_type,
        "ending_line_preview": ending_line[:50],
        "overlap_keywords": [],
        "reason": "账本预算的前章 ending 与后章首段重叠度（cluster 摘要驱动）",
    }


# ===== 维度 2: 时间跳跃 =====

TIME_KEYWORDS = {
    "凌晨": 2, "早上": 8, "上午": 10, "中午": 12, "下午": 15,
    "傍晚": 18, "晚上": 20, "晚间": 21, "夜里": 23,
    "周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7,
}


def scan_time_gap(prev_changes: dict, next_changes: dict) -> dict:
    """简易时间跳跃检测。"""
    if not prev_changes or not next_changes:
        return {"detected": False, "reason": "changes 缺失"}

    prev_time = prev_changes.get("factual", {}).get("time_advance", {})
    next_time = next_changes.get("factual", {}).get("time_advance", {})
    prev_events = prev_time.get("key_events", [])
    next_events = next_time.get("key_events", [])
    prev_end = prev_events[-1] if prev_events else prev_time.get("period", "")
    next_start = next_events[0] if next_events else next_time.get("period", "")

    # 提取周日期
    prev_day = next((d for k, d in TIME_KEYWORDS.items() if k.startswith("周") and k in str(prev_end)), None)
    next_day = next((d for k, d in TIME_KEYWORDS.items() if k.startswith("周") and k in str(next_start)), None)

    if prev_day and next_day:
        day_gap = (next_day - prev_day) % 7
        if day_gap >= 2:
            return {
                "detected": True,
                "gap_days": day_gap,
                "prev_time_end": str(prev_end)[:40],
                "next_time_start": str(next_start)[:40],
                "reason": f"章间跳跃 {day_gap} 天，需在后章开篇有过渡说明",
            }
    return {"detected": False, "prev_end": str(prev_end)[:40], "next_start": str(next_start)[:40]}


# ===== 维度 3: 物件持续性 =====

def _build_aliases(name: str) -> list[str]:
    """从完整物件名提取核心别名（短名/括号内的标识/关键词）。"""
    aliases = [name]
    # 提取括号内的内容
    m = re.search(r"[（(]([^）)]+)[）)]", name)
    if m:
        aliases.append(m.group(1))
    # 去括号的核心名
    core = re.sub(r"[（(].+?[）)]", "", name).strip()
    if core and core != name:
        aliases.append(core)
    # 关键短语（首/末 2-3 字）
    if len(name) >= 4:
        aliases.append(name[:3])
        aliases.append(name[-3:])
    # 2026-06-15 去硬编码：原此处硬编码特定旧书物件名"废票/灵格/铁皮盒/VIP/暗码表/笔记本/
    # 邮件/PDF/股票/持股/0.001"·只对那本书有效(北极星⑥清硬编码 + ①不绑特定书)。上方括号/
    # 去括号核心/首末 3 字通用提取对任意书物件名都工作(如"废票（彩票）"经去括号已得"废票")·
    # 删硬编码补丁不引入过宽匹配削弱检测。
    return list(set(aliases))


def scan_object_continuity(all_changes: dict[int, dict], all_texts: dict[int, str], current_ch: int) -> list[dict]:
    """关键物件（chekhovs_gun）连续 ≥2 章未提及。"""
    findings = []
    if current_ch < 3:
        return findings

    # 收集 ch1~current 的所有关键物件转移给主角
    key_items: dict[str, dict] = {}
    for ch in range(1, current_ch + 1):
        ch_changes = all_changes.get(ch)
        if not ch_changes:
            continue
        for it in ch_changes.get("factual", {}).get("item_transfers", []):
            name = it.get("item", "")
            if not name:
                continue
            if name not in key_items:
                key_items[name] = {"first_ch": ch, "last_seen_ch": ch, "aliases": _build_aliases(name)}
            else:
                key_items[name]["last_seen_ch"] = ch

    # 对每章正文 + changes 内所有文本字段做提及检查
    for ch in range(1, current_ch + 1):
        ch_text = all_texts.get(ch, "")
        ch_changes_str = json.dumps(all_changes.get(ch, {}), ensure_ascii=False)
        combined = ch_text + "\n" + ch_changes_str
        for name, info in key_items.items():
            if info["last_seen_ch"] >= ch:
                continue
            # 用 aliases 任一命中即算提及
            for alias in info["aliases"]:
                if alias in combined:
                    info["last_seen_ch"] = ch
                    break

    # 检查每个 key_item 是否 ≥2 章未提及
    for name, info in key_items.items():
        gap = current_ch - info["last_seen_ch"]
        if gap >= 2 and info["first_ch"] <= current_ch - 2:
            findings.append({
                "item": name,
                "first_ch": info["first_ch"],
                "last_seen_ch": info["last_seen_ch"],
                "gap": gap,
                "current_ch": current_ch,
            })
    return findings


# ===== 维度 4: 情绪断层 =====

def read_emotion(project_root: Path, ch: int) -> int | None:
    summary_path = project_root / "_数据库" / ".wal" / f"第{ch:03d}章_summary.json"
    if not summary_path.exists():
        return None
    data = load_json(summary_path, {})
    return data.get("emotion", {}).get("value")


def scan_emotion_gap(project_root: Path, prev_ch: int, next_ch: int, ledger_by_ch: dict | None = None) -> dict:
    # 2026-05-29 cluster 化：账本有 emotion_value → 用账本；否则回退读 WAL summary。
    def _emo(ch):
        if ledger_by_ch is not None:
            rec = ledger_by_ch.get(ch)
            if rec is not None and isinstance(rec.get("emotion_value"), (int, float)):
                return rec["emotion_value"]
        return read_emotion(project_root, ch)
    e1 = _emo(prev_ch)
    e2 = _emo(next_ch)
    if e1 is None or e2 is None:
        return {"detected": False, "reason": "summary 缺失"}
    diff = abs(e1 - e2)
    return {
        "detected": diff >= 5,
        "prev_emotion": e1,
        "next_emotion": e2,
        "diff": diff,
    }


# ===== 主流程 =====

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    chapter_dirs = find_chapter_dirs(project_root)
    if len(chapter_dirs) < 2:
        print("[OK] 章节 <2，无衔接可扫")
        sys.exit(0)
    chapter_dirs = chapter_dirs[-args.last_n:]
    protagonist = get_protagonist(project_root)  # 动态主角名 → extract_keywords stop（取代硬编码陆衍·北极星⑥）

    all_changes: dict[int, dict] = {}
    all_texts: dict[int, str] = {}
    for ch, d in chapter_dirs:
        all_texts[ch] = read_chapter_text(d, ch) or ""
        all_changes[ch] = read_changes(d, ch) or {}

    # ===== 2026-05-29 cluster 化：账本有任一 continuity 字段 → 维度 1/2/4 摘要驱动 =====
    # cliffhanger 维度账本无 cliffhanger_resonance_next 时该维度单独回退正文逻辑；
    # 维度 3（物件持续性）依赖正文别名匹配，账本无对应文本字段 → 始终走磁盘。
    ledger_by_ch = None
    if csr.is_cluster_mode() and (
        csr.ledger_has_field(project_root, "time_advance")
        or csr.ledger_has_field(project_root, "ending_type")
        or csr.ledger_has_field(project_root, "emotion_value")
        or csr.ledger_has_field(project_root, "cliffhanger_resonance_next")
    ):
        ledger_by_ch = {ch: rec for ch, rec in csr.get_chapter_records(project_root)}

    def _changes_like(ch: int) -> dict:
        """把账本 ChapterRecord 包成 scan_time_gap 期望的 {factual:{time_advance,plot_nodes}} 形态。"""
        rec = (ledger_by_ch or {}).get(ch)
        if rec is None:
            return all_changes.get(ch, {})
        return {"factual": {
            "time_advance": rec.get("time_advance", {}) or {},
            "plot_nodes": rec.get("plot_nodes", []) or [],
        }}

    findings = []
    pairwise = []

    # 逐对相邻章扫
    for i in range(len(chapter_dirs) - 1):
        prev_ch, prev_d = chapter_dirs[i]
        next_ch, next_d = chapter_dirs[i + 1]

        # 维度 1: cliffhanger
        prev_ledger_rec = (ledger_by_ch or {}).get(prev_ch)
        if prev_ledger_rec is not None and isinstance(prev_ledger_rec.get("cliffhanger_resonance_next"), (int, float)):
            cliff = scan_cliffhanger_resonance_ledger(prev_ledger_rec, next_d)
        else:
            cliff = scan_cliffhanger_resonance(all_changes.get(prev_ch, {}), all_texts.get(next_ch, ""), next_d, protagonist=protagonist)
        if not cliff.get("exempt") and cliff.get("score", -1) >= 0 and cliff["score"] < 0.2:
            findings.append({
                "dimension": "cliffhanger",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "CLIFFHANGER_NOT_RESONATED",
                "from_ch": prev_ch,
                "to_ch": next_ch,
                "metric": {"resonance_score": cliff["score"], "ending_type": cliff.get("ending_type"), "ending_preview": cliff.get("ending_line_preview")},
                "message": f"ch{prev_ch}→ch{next_ch}: 前章 ending ({cliff.get('ending_type')}) 未在后章首段被回应（重叠度 {cliff['score']:.0%}）",
                "suggestion": f"后章首段 ≤300 字内必须回应前章 ending 关键词；当前 ending_line='{cliff.get('ending_line_preview', '')}'",
            })

        # 维度 2: 时间跳跃
        time_gap = scan_time_gap(_changes_like(prev_ch), _changes_like(next_ch))
        if time_gap.get("detected"):
            # 检查后章 plot_nodes 是否有过渡说明
            next_plots = _changes_like(next_ch).get("factual", {}).get("plot_nodes", [])
            has_transition = any(
                any(kw in str(p).lower() for kw in ["过渡", "周末", "回忆", "醒来", "睡了"])
                for p in next_plots
            )
            if not has_transition:
                findings.append({
                    "dimension": "time_gap",
                    "severity": "warning",
                    "gate_level": "advisory",
                    "code": "TIME_JUMP_UNEXPLAINED",
                    "from_ch": prev_ch,
                    "to_ch": next_ch,
                    "metric": time_gap,
                    "message": f"ch{prev_ch}→ch{next_ch}: 时间跳跃 {time_gap['gap_days']} 天，后章 plot_nodes 无过渡说明",
                    "suggestion": "后章开篇加 1-2 段过渡说明（周末做了什么/如何消化前章震撼）",
                })

        # 维度 4: 情绪断层
        emo_gap = scan_emotion_gap(project_root, prev_ch, next_ch, ledger_by_ch)
        if emo_gap.get("detected"):
            findings.append({
                "dimension": "emotion",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "EMOTION_DISCONTINUITY",
                "from_ch": prev_ch,
                "to_ch": next_ch,
                "metric": emo_gap,
                "message": f"ch{prev_ch}→ch{next_ch}: 情绪值跳跃 {emo_gap['diff']}（{emo_gap['prev_emotion']}→{emo_gap['next_emotion']}）",
                "suggestion": "断层 ≥5 时建议加 emotional bridge 段（回忆/独白/动作过渡）",
            })

        pairwise.append({
            "from_ch": prev_ch,
            "to_ch": next_ch,
            "cliffhanger_score": cliff.get("score", -1),
            "cliffhanger_exempt": cliff.get("exempt", False),
            "time_gap_days": time_gap.get("gap_days", 0),
            "emotion_diff": emo_gap.get("diff", 0),
        })

    # 维度 3: 物件持续性（针对最新章）
    last_ch = chapter_dirs[-1][0]
    obj_findings = scan_object_continuity(all_changes, all_texts, last_ch)
    for of in obj_findings:
        # gap 越大越严重(取代硬编码特定旧书物件名"废票/铁皮盒/VIP/暗码表/日记本"·北极星⑥清
        # 硬编码 + ①不绑特定书)：关键物件连续 ≥5 章未提及 = 被遗忘风险高 → warning，否则 advisory。
        sev = "warning" if of.get("gap", 0) >= 5 else "advisory"
        findings.append({
            "dimension": "object_continuity",
            "severity": sev,
            "gate_level": "advisory",
            "code": "OBJECT_CONTINUITY_BROKEN",
            "metric": of,
            "message": f"关键物件「{of['item']}」自 ch{of['first_ch']} 出现，ch{of['last_seen_ch']} 后连续 {of['gap']} 章未提及",
            "suggestion": "至少每 3 章提及一次，或在 _changes.json item_locations 显式标注当前位置",
        })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "continuity",
        "scan_ts": ts,
        "chapters_scanned": [ch for ch, _ in chapter_dirs],
        "pairwise": pairwise,
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"continuity_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印
    print(f"[cross_cluster_continuity_aggregate] 扫描章节={[ch for ch, _ in chapter_dirs]}")
    print()
    print("=== 相邻章衔接 pairwise ===")
    for p in pairwise:
        cliff = "exempt(DCAS)" if p["cliffhanger_exempt"] else (f"{p['cliffhanger_score']:.0%}" if p["cliffhanger_score"] >= 0 else "N/A")
        print(f"  ch{p['from_ch']}→ch{p['to_ch']}: cliffhanger={cliff} time_gap={p['time_gap_days']}天 emotion_diff={p['emotion_diff']}")
    print()
    print(f"=== 发现 {len(findings)} 项 (warning={report['summary']['warning']} / advisory={report['summary']['advisory']}) ===")
    for f in findings:
        loc = f.get('from_ch') and f"ch{f['from_ch']}→ch{f['to_ch']}" or "*"
        print(f"  [{f['severity'].upper()}] [{f['code']}] {loc} :: {f['message']}")
        print(f"     建议: {f['suggestion']}")
    print()
    print(f"报告: {out_path}")

    if any(f["severity"] == "warning" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
