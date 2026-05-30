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

# 2026-05-29 修：注入 scripts 目录以 import atomic_json（原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json


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


# ───────────────────── 真实项目 schema 兼容层（2026-05-30 北极星③契约修复） ─────────────────────
# 背景（3 真实项目实测）：引擎旧版只认 v21 扁平 schema（标量 stress_level/stress_max/
# stress_threshold_break + persona_violations_tracked.core_traits[]），但 AI 自由生成维度 schema：
#   · 纵尸司：stress_dimensions{guilt:0,fear:0,...}（扁平 int） · breakdown_threshold:80 · stress_history
#   · 诡异：  stress_dimensions{身份暴露:{current,max,trigger_threshold}}（嵌套 dict） · stress_history
#   · 城南：  characters_stress{lin_qi:{current_stress,...}}（多角色） + 也有 v21 标量字段
# 旧版 traits=[] → delta 恒 0 → 永不抽 mental_break；manifest stress_level 缺省 0 → is_high_stress 恒 false。
# 修复（参照 fate_engine accessor 范式）：把 3 套维度形态聚合出统一标量 stress 视图供引擎/manifest 消费，
# 写回时按真实 schema 落字段。纪律：只兼容读取（北极星⑥），不强制改 outline schema · 仍是顾问非硬锁。


def _dim_value(v) -> int:
    """取单维度当前值 · 兼容扁平 int（纵尸司 guilt:0）与嵌套 dict（诡异 身份暴露:{current:N}）。"""
    if isinstance(v, dict):
        return v.get("current", 0) or 0
    if isinstance(v, (int, float)):
        return int(v)
    return 0


def _dim_max(v, default: int = 10) -> int:
    """取单维度上限 · 嵌套 dict 有 max 用 max，否则 default。"""
    if isinstance(v, dict):
        return v.get("max", default) or default
    return default


def _dim_threshold(v):
    """取单维度触发阈 · 嵌套 dict 的 trigger_threshold（无则 None 由全局兜底）。"""
    if isinstance(v, dict):
        return v.get("trigger_threshold")
    return None


def stress_view(stress: dict) -> dict:
    """把任意 stress schema 归一成统一标量视图（供 evaluate + build_manifest 共用）：
      {mode, stress_level, stress_max, stress_threshold_break, traits, dims_present, _dim_keys}
    - v21 标量：直接读 stress_level/stress_max/stress_threshold_break
    - 维度 schema（纵尸司/诡异）：stress_level=max(各维度归一到 0..stress_max 后的值)，
      threshold = 各维度 trigger_threshold/breakdown_threshold 的最贴合者（按峰值维度）。
    """
    dims = stress.get("stress_dimensions")
    if isinstance(dims, dict) and dims:
        # 维度 schema：每维度归一到统一 max（取各维度 max 的众数/默认 10），取峰值维度作为整体压力
        stress_max = 10
        peak_level = 0
        peak_threshold = None
        dim_keys = []
        for k, v in dims.items():
            if k.startswith("_"):
                continue
            dim_keys.append(k)
            cur = _dim_value(v)
            dmax = _dim_max(v, stress.get("stress_max", 10))
            # 归一到统一 stress_max=10 量纲，便于和 v21 threshold/manifest pct 对齐
            norm = round(cur / dmax * 10) if dmax > 0 else cur
            if norm > peak_level:
                peak_level = norm
                peak_threshold = _dim_threshold(v)
        # 阈值：峰值维度 trigger_threshold（已是 0..max 量纲 → 归一）优先，否则 breakdown_threshold 归一，否则默认 8
        threshold = 8
        if peak_threshold is not None:
            # peak_threshold 处于该维度原始量纲；用峰值维度的 max 归一
            threshold = 8 if peak_threshold == 0 else min(10, round(peak_threshold / 10 * 10)) if peak_threshold <= 10 else round(peak_threshold / 100 * 10)
        elif stress.get("breakdown_threshold") is not None:
            bt = stress["breakdown_threshold"]
            threshold = round(bt / 100 * 10) if bt > 10 else bt  # 80(百分量纲)→8
        return {
            "mode": "dimensions",
            "stress_level": peak_level,
            "stress_max": stress_max,
            "stress_threshold_break": threshold or 8,
            "traits": [],  # 维度 schema 无逐 trait 关键词，delta 走维度（暂不自动加，见 evaluate）
            "dims_present": True,
            "_dim_keys": dim_keys,
        }
    # v21 标量 schema（含城南）
    return {
        "mode": "scalar",
        "stress_level": stress.get("stress_level", 0),
        "stress_max": stress.get("stress_max", 10),
        "stress_threshold_break": stress.get("stress_threshold_break", 8),
        "traits": (stress.get("persona_violations_tracked", {}) or {}).get("core_traits", []),
        "dims_present": False,
        "_dim_keys": [],
    }


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

    view = stress_view(stress)
    traits = view["traits"]
    eval_result = evaluate_stress_delta(text, traits)
    delta = eval_result["delta"]

    old = view["stress_level"]
    max_v = view["stress_max"]
    new = max(0, min(max_v, old + delta))
    if view["mode"] == "scalar":
        # v21 标量 schema：delta 落标量 stress_level（含城南）
        stress["stress_level"] = new
    else:
        # 维度 schema（纵尸司/诡异）：无逐 trait 关键词 → text delta=0（traits=[]）；
        # 不擅自往维度写 delta（维度推进由 cluster-save-state 的世界演化/走向卡驱动，北极星②③顾问非法官）。
        # evaluate 在此模式下作用是「把当前聚合 stress 暴露给 manifest 不再恒 0」——只读不改维度值。
        new = old  # 维度模式不在本章自动累加（保持引擎只读维度，不变成硬约束）

    # log（兼容 stress_log / stress_history 两套键名）
    log = stress.get("stress_log")
    if log is None:
        log = stress.get("stress_history")
    if log is None:
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
    threshold = view["stress_threshold_break"]
    # mental_break 仅 v21 标量 schema 自动抽卡（有 mental_break_pool + 标量 reset 语义）；
    # 维度 schema 无 mental_break_pool → draw 自然返回 None，不会误触发（北极星⑤顾问非法官）。
    if new >= threshold:
        pool = stress.get("mental_break_pool", [])
        triggered_card = draw_mental_break_card(pool, new)
        if triggered_card:
            apply_card_to_locked_facts(project_root, ch, triggered_card, stress.get("protagonist") or stress.get("protagonist_id", "?"))
            if view["mode"] == "scalar":
                stress["stress_level"] = 0  # reset（仅标量模式有此语义）
            new = 0
            log.append({
                "ch": ch,
                "change": -old,
                "new_total": 0,
                "trigger_type": "mental_break_triggered",
                "card_id": triggered_card.get("card_id"),
                "card_label": triggered_card.get("label"),
                "_ts": datetime.now().isoformat(timespec="seconds"),
            })

    save_json(stress_path, stress)

    out = {
        "ch": ch,
        "schema_mode": view["mode"],
        "stress_delta": delta,
        "stress_old": old,
        "stress_new": new,
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
