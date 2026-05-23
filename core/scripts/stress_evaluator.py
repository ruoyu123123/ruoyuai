"""stress_evaluator.py — 主角 Stress + Mental Break 卡评估器（v21 R1.3 新增）

借鉴 CK3：违背性格 → stress 累积 → 满 stress_threshold_break 抽 mental_break_card → 永久改写 persona。

每章 save-state 末尾跑：
1. 读 第NNN章.txt 正文
2. 对比 persona_violations_tracked 中每条 trait 的 violation/align 关键词
3. 计算本章 stress delta（+违背 / -符合 / +事件冲击）
4. 更新 stress_level + 写 stress_log
5. 如 stress_level >= stress_threshold_break：
   - 按 weight 加权随机抽一张 mental_break_card
   - 应用 permanent_persona_changes（写入 locked_facts）
   - reset stress_level = 0
   - 输出强烈告警

用法：python stress_evaluator.py <project> [--ch N | --auto]
退出码: 0 健康 / 1 高 stress 警告 / 2 触发 mental_break / 3 致命
"""

from __future__ import annotations

import argparse
import json
import random
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


def save_json(p: Path, data: dict):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_chapter_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def evaluate_stress_delta(text: str, traits: list[dict]) -> dict:
    """扫文本计算 stress 变化。返回 {delta, violations:[], alignments:[]}"""
    delta = 0
    violations = []
    alignments = []
    for trait in traits:
        v_kw = trait.get("violation_keywords", [])
        a_kw = trait.get("align_keywords", [])
        per = trait.get("stress_per_violation", 2)
        v_hits = sum(text.count(k) for k in v_kw)
        a_hits = sum(text.count(k) for k in a_kw)
        if v_hits > 0:
            delta += per * min(v_hits, 3)  # 单 trait 单章 cap 3 命中
            violations.append({"trait": trait["trait"], "hits": v_hits, "stress_added": per * min(v_hits, 3)})
        elif a_hits >= 2:
            delta -= 1  # 持续符合给予 relief
            alignments.append({"trait": trait["trait"], "hits": a_hits, "stress_relief": -1})
    return {"delta": delta, "violations": violations, "alignments": alignments}


def draw_mental_break_card(pool: list[dict], stress_level: int) -> dict:
    """按 weight 加权随机抽一张满足 trigger_min_stress 的卡。"""
    eligible = [c for c in pool if stress_level >= c.get("trigger_min_stress", 8)]
    if not eligible:
        return None
    weights = [c.get("weight", 1) for c in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]


def apply_card_to_locked_facts(project_root: Path, ch: int, card: dict, protagonist: str):
    """把抽到的 mental_break 卡写入 locked_facts."""
    cards_path = project_root / "_数据库" / "事件表.json"
    cards = load_json(cards_path, {"events": []})
    cards.setdefault("events", []).append({
        "id": f"MB_event_{ch}_{card.get('card_id', 'unknown')}",
        "ch": ch,
        "type": "mental_break_triggered",
        "protagonist": protagonist,
        "card": card.get("label"),
        "card_id": card.get("card_id"),
        "permanent_persona_changes": card.get("permanent_persona_changes", []),
        "narrative_effect": card.get("narrative_effect", ""),
        "_doc": "Mental Break 抽卡触发——后续章节必受此卡约束",
    })
    save_json(cards_path, cards)


def evaluate(project_root: Path, ch: int) -> dict:
    stress_path = project_root / "_数据库" / "主角压力档.json"
    stress = load_json(stress_path, None)
    if stress is None:
        return {"error": "主角压力档.json 不存在"}

    text = read_chapter_text(project_root, ch)
    if not text:
        return {"ch": ch, "skipped": "本章无正文"}

    traits = stress.get("persona_violations_tracked", {}).get("core_traits", [])
    eval_result = evaluate_stress_delta(text, traits)
    delta = eval_result["delta"]

    old = stress.get("stress_level", 0)
    new = max(0, min(stress.get("stress_max", 10), old + delta))
    stress["stress_level"] = new

    # log
    log = stress.setdefault("stress_log", [])
    log.append({
        "ch": ch,
        "change": delta,
        "new_total": new,
        "trigger_type": "persona_violation" if delta > 0 else ("persona_align" if delta < 0 else "neutral"),
        "violations": eval_result["violations"],
        "alignments": eval_result["alignments"],
        "_ts": datetime.now().isoformat(timespec="seconds"),
    })

    triggered_card = None
    threshold = stress.get("stress_threshold_break", 8)
    if new >= threshold:
        pool = stress.get("mental_break_pool", [])
        triggered_card = draw_mental_break_card(pool, new)
        if triggered_card:
            apply_card_to_locked_facts(project_root, ch, triggered_card, stress.get("protagonist", "?"))
            stress["stress_level"] = 0  # reset
            log.append({
                "ch": ch,
                "change": -new,
                "new_total": 0,
                "trigger_type": "mental_break_triggered",
                "card_id": triggered_card.get("card_id"),
                "card_label": triggered_card.get("label"),
                "_ts": datetime.now().isoformat(timespec="seconds"),
            })

    save_json(stress_path, stress)

    out = {
        "ch": ch,
        "stress_delta": delta,
        "stress_old": old,
        "stress_new": stress["stress_level"],
        "violations": eval_result["violations"],
        "alignments": eval_result["alignments"],
        "high_stress_warning": new >= threshold * 0.75 and triggered_card is None,
        "mental_break_triggered": triggered_card is not None,
    }
    if triggered_card:
        out["card"] = {
            "id": triggered_card.get("card_id"),
            "label": triggered_card.get("label"),
            "permanent_changes": triggered_card.get("permanent_persona_changes"),
            "narrative_effect": triggered_card.get("narrative_effect"),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    if not (project_root / "_数据库" / "主角压力档.json").exists():
        print("[SKIP] 主角压力档.json 不存在 — 项目未启用 Stress 系统")
        sys.exit(0)

    ch = args.ch
    if ch is None or args.auto:
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        ch = chapters[-1]

    r = evaluate(project_root, ch)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("mental_break_triggered"):
        sys.exit(2)
    if r.get("high_stress_warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
