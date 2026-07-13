#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""direction_card_poetics_scanner.py — 走向卡 Choice Poetics 5 维 · R25 W13 Batch-MM · P1

【缺口 · Mawhorter Choice Poetics FDG 2014 + Emily Short / 互动小说设计学】
novel-outline-planner 产 2-3 张走向卡后立即调度。5 维 QA：
  · framing_completeness  — 卡面是否说清前置情境/角色/利害
  · dilemma_density       — 是否构成真两难（≥2 损失向量同时存在）
  · outcome_divergence    — 选项预期后果是否互相错开
  · false_choice_risk     — 是否伪选择（cosmin 命名实异）
  · agency_signal         — 是否提示玩家有真主动权（非系统暗推）

【做法 · 确定性 · 零 LLM/零联网（占位规则）】
  · 读 direction_card.json/dict（cluster_emergence 候选 brief）
  · 每张卡 5 维 0-1 评分
  · 写 _数据库/.direction_card_advisory/<cluster_key>.json
  · mode-gated：默认 advisory_low；interactive_mode/multi_ending_mode 升 normal

【五 advisory · 全 advisory shadow】
  · DIRECTION_CARD_FRAMING_THIN          — framing_completeness < 0.5
  · DIRECTION_CARD_DILEMMA_THIN          — dilemma_density < 0.5
  · DIRECTION_CARD_OUTCOME_CONVERGENT    — outcome_divergence < 0.5
  · DIRECTION_CARD_FALSE_CHOICE_RISK     — false_choice_risk > 0.5
  · DIRECTION_CARD_LOW_AGENCY            — agency_signal < 0.5
  · DIRECTION_CARD_OK                    — 全过·info

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  DIRECTION_CARD_* 绝不进 audit_hub.HARD_GATE_CODES。

env DIRECTION_CARD_POETICS_MODE: off / shadow（默认） / active
用法: python direction_card_poetics_scanner.py <cards_json>
                                 [--project <root>] [--cluster <key>]
                                 [--interactive-mode] [--multi-ending-mode]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_FRAMING_THIN = "DIRECTION_CARD_FRAMING_THIN"
ISSUE_CODE_DILEMMA_THIN = "DIRECTION_CARD_DILEMMA_THIN"
ISSUE_CODE_OUTCOME_CONVERGENT = "DIRECTION_CARD_OUTCOME_CONVERGENT"
ISSUE_CODE_FALSE_CHOICE_RISK = "DIRECTION_CARD_FALSE_CHOICE_RISK"
ISSUE_CODE_LOW_AGENCY = "DIRECTION_CARD_LOW_AGENCY"
ISSUE_CODE_OK = "DIRECTION_CARD_OK"

# 占位词典 · _placeholder=true
_LEX = {
    "_placeholder": True,
    "framing_words": ["情境", "前情", "局势", "处境", "状态",
                      "角色", "人物", "立场", "利害", "代价",
                      "目标", "动机", "障碍"],
    "loss_vectors": ["失去", "牺牲", "放弃", "毁掉", "断绝", "得罪",
                     "受伤", "暴露", "背叛", "切断", "永别"],
    "dilemma_marker": ["两难", "抉择", "进退", "左右为难", "无路可退",
                       "鱼与熊掌", "舍此就彼"],
    "false_choice_hint": ["其实差不多", "无论选哪", "都一样", "殊途同归",
                          "结果都", "无关紧要"],
    "agency_signal": ["你决定", "由你选", "你来定", "自行抉择", "在你",
                      "由读者", "自主权", "主动权"],
}

POETICS_FLOOR = 0.5
FALSE_CHOICE_CEILING = 0.5


def _mode() -> str:
    m = (os.environ.get("DIRECTION_CARD_POETICS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _hit_count(text: str, words: list) -> int:
    return sum(1 for w in words if w in text)


def _score_framing(card_text: str) -> float:
    """framing_completeness 0-1：framing_words 命中数 / 4 上限。"""
    hits = _hit_count(card_text, _LEX["framing_words"])
    return min(1.0, hits / 4.0)


def _score_dilemma(card_text: str) -> float:
    """dilemma_density 0-1：loss_vectors >= 2 → 0.8；dilemma_marker → +0.2。"""
    losses = _hit_count(card_text, _LEX["loss_vectors"])
    marker = 1 if any(w in card_text for w in _LEX["dilemma_marker"]) else 0
    base = min(0.8, 0.4 * losses)
    return min(1.0, base + 0.2 * marker)


def _score_outcome_divergence(cards: list) -> float:
    """outcome_divergence 0-1：候选 brief 文本 Jaccard 反相似度。"""
    if len(cards) < 2:
        return 0.5
    bigrams = []
    for c in cards:
        s = c.get("summary") or c.get("brief") or c.get("text") or ""
        bg = set()
        for i in range(len(s) - 1):
            if "一" <= s[i] <= "鿿" or "一" <= s[i + 1] <= "鿿":
                bg.add(s[i:i + 2])
        bigrams.append(bg)
    # 平均 Jaccard
    pairs, total = 0, 0.0
    for i in range(len(bigrams)):
        for j in range(i + 1, len(bigrams)):
            a, b = bigrams[i], bigrams[j]
            u = a | b
            inter = a & b
            sim = (len(inter) / len(u)) if u else 0.0
            total += sim
            pairs += 1
    avg_sim = total / pairs if pairs else 0.0
    # divergence = 1 - similarity
    return round(max(0.0, min(1.0, 1.0 - avg_sim)), 4)


def _score_false_choice(card_text: str) -> float:
    """false_choice_risk 0-1：false_choice_hint 命中 / 2 上限。"""
    hits = _hit_count(card_text, _LEX["false_choice_hint"])
    return min(1.0, hits / 2.0)


def _score_agency(card_text: str) -> float:
    """agency_signal 0-1：agency_signal 命中 / 2 上限·缺则降。"""
    hits = _hit_count(card_text, _LEX["agency_signal"])
    return min(1.0, hits / 2.0)


def _evaluate_card(card: dict, all_cards: list) -> dict:
    """单卡 5 维评分。"""
    text = (card.get("summary") or card.get("brief") or card.get("text") or "")
    return {
        "card_key": card.get("key") or card.get("id") or "card",
        "framing_completeness": round(_score_framing(text), 4),
        "dilemma_density": round(_score_dilemma(text), 4),
        "outcome_divergence": _score_outcome_divergence(all_cards),
        "false_choice_risk": round(_score_false_choice(text), 4),
        "agency_signal": round(_score_agency(text), 4),
    }


def _load_cards(path: str) -> list:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # 兼容 cluster_emergence 候选 brief schema
        if "candidates" in data:
            return list(data["candidates"])
        if "cards" in data:
            return list(data["cards"])
        # dict 单卡兜底
        return [data]
    return []


def _write_advisory(project_root, cluster_key, payload):
    if not project_root:
        return None
    try:
        d = Path(project_root) / "_数据库" / ".direction_card_advisory"
        d.mkdir(parents=True, exist_ok=True)
        out_p = d / f"{cluster_key or 'cluster_unknown'}.json"
        out_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        return str(out_p)
    except Exception:
        return None


def scan(cards_json_path, project_root=None, cluster_id=None,
         interactive_mode=False, multi_ending_mode=False) -> dict:
    mode = _mode()
    effective_level = ("normal"
                       if (interactive_mode or multi_ending_mode)
                       else "advisory_low")
    out = {
        "scanner": "direction_card_poetics_scanner", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "effective_level": effective_level,
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": _LEX["_placeholder"],
    }
    if mode == "off":
        return out
    try:
        cards = _load_cards(cards_json_path)
    except (OSError, json.JSONDecodeError) as e:
        out["note"] = f"卡片读取失败：{str(e)[:120]}"
        return out
    if not cards:
        out["note"] = "无候选卡·跳过"
        return out

    per_card = [_evaluate_card(c, cards) for c in cards]
    advisories = []
    for s in per_card:
        if s["framing_completeness"] < POETICS_FLOOR:
            advisories.append({"card_key": s["card_key"],
                               "code": ISSUE_CODE_FRAMING_THIN,
                               "severity": "minor",
                               "msg": f"framing_completeness={s['framing_completeness']} < {POETICS_FLOOR}"})
        if s["dilemma_density"] < POETICS_FLOOR:
            advisories.append({"card_key": s["card_key"],
                               "code": ISSUE_CODE_DILEMMA_THIN,
                               "severity": "minor",
                               "msg": f"dilemma_density={s['dilemma_density']} < {POETICS_FLOOR}"})
        if s["outcome_divergence"] < POETICS_FLOOR:
            advisories.append({"card_key": s["card_key"],
                               "code": ISSUE_CODE_OUTCOME_CONVERGENT,
                               "severity": "minor",
                               "msg": f"outcome_divergence={s['outcome_divergence']} < {POETICS_FLOOR}"})
        if s["false_choice_risk"] > FALSE_CHOICE_CEILING:
            advisories.append({"card_key": s["card_key"],
                               "code": ISSUE_CODE_FALSE_CHOICE_RISK,
                               "severity": "minor",
                               "msg": f"false_choice_risk={s['false_choice_risk']} > {FALSE_CHOICE_CEILING}"})
        if s["agency_signal"] < POETICS_FLOOR:
            advisories.append({"card_key": s["card_key"],
                               "code": ISSUE_CODE_LOW_AGENCY,
                               "severity": "minor",
                               "msg": f"agency_signal={s['agency_signal']} < {POETICS_FLOOR}"})

    if not advisories:
        advisories.append({"card_key": "*", "code": ISSUE_CODE_OK,
                           "severity": "info",
                           "msg": f"{len(per_card)} 张卡·5 维全过"})

    out.update({
        "cluster_id": cluster_id,
        "card_count": len(cards),
        "per_card_scores": per_card,
        "advisories": advisories,
    })

    # 落地 advisory json
    payload = {"cluster_id": cluster_id, "per_card_scores": per_card,
               "advisories": advisories, "effective_level": effective_level,
               "_placeholder": True}
    wrote = _write_advisory(project_root, cluster_id, payload)
    if wrote:
        out["advisory_json_path"] = wrote

    if mode == "active":
        for a in advisories:
            out["violations"].append({
                "kind": "direction_card_poetics",
                "severity": a["severity"],
                "code": a["code"], "message": a["msg"],
                "card_key": a["card_key"],
                "_doc": "R25 W13 Batch-MM·Mawhorter Choice Poetics·advisory·绝不 hard_gate",
            })
        minor = [a for a in advisories if a["severity"] == "minor"]
        # advisory_low 模式: 仅 normal 升 FAIL_MINOR
        if minor and effective_level == "normal":
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "·".join(a["msg"] for a in minor[:3])
        elif minor:
            out["verdict"] = "PASS"  # advisory_low · 仅 info
    elif mode == "shadow":
        minor = [a for a in advisories if a["severity"] == "minor"]
        if minor:
            print("[SHADOW] direction_card_poetics: "
                  + "·".join(a["msg"] for a in minor[:3]) + " — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="走向卡 Choice Poetics 5 维 advisory shadow")
    ap.add_argument("cards_json_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    ap.add_argument("--interactive-mode", action="store_true")
    ap.add_argument("--multi-ending-mode", action="store_true")
    args = ap.parse_args()
    rep = scan(args.cards_json_path, args.project, args.cluster,
               args.interactive_mode, args.multi_ending_mode)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
