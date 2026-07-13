#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chronotope_typology_scanner.py — Bakhtin 6+1 时空体场景类型分布
(advisory · cluster · 2026-06-20 R9 W5 Batch-M · L38)

【缺口】R9 联网调研 (Bakhtin Dialogic Imagination 1981 + chronotope_spatial):
Bakhtin 时空体 (chronotope) 理论·叙事场景按时空体分 6+1 型:
  road / threshold / castle / salon / town / square / idyll (+ instance_dungeon 升级位)
LLM 默认产「都市内景」+「室内对话」→ chronotope 分布塌缩=单调感。此前全系统:
  · scene_grounding   只查感官接地有无
  · scene_seam        查衔接手法分布
  · location_signature查地点感官指纹一致性
  · 【chronotope 时空体类型分布零检测】

本 scanner 补 scene-level chronotope tagging + cluster 级 distribution_entropy +
monotony_streak (同型连续 ≥3 → advisory)·writer prompt 建议每 cluster ≥1 threshold
转折锚点 (Bakhtin 强调 threshold chronotope 是关键转折)。

【做法 · 确定性纯规则·零 LLM·零依赖】
  1. 七型 chronotope 关键词词典 (高确定性·宁可漏报):
     road              路途/驿道/官道/赶路/旅店/客栈/驿站/马背上/车厢里
     threshold         门槛/玄关/门口/桥头/边境/关口/入口处/界碑/分水岭
     castle            城堡/王宫/朝堂/殿宇/塔楼/王座/雕梁
     salon             厅堂/书房/客厅/茶馆/酒楼包厢/雅间/会客
     town              街市/巷弄/胡同/坊间/街角/小铺/作坊
     square            广场/集市/法场/校场/演武场/朝会
     idyll             田园/院落/小院/茅屋/竹林/溪边/稻田/农舍
     instance_dungeon  副本/秘境/试炼塔/异空间/秘境/迷宫
  2. 按段落空行切场景 (与 scene_seam_scanner 切法对齐)
  3. 每场景前 200 字内统计七型命中·取 top1 命中型为 scene chronotope tag
     若无命中 → "untagged"
  4. cluster 级指标:
     - distribution_entropy = Shannon H over 7 tags (归一·log2(7))
     - monotony_streak = 同型连续场景数 (max streak)
  5. 阈值:
     - monotony_streak ≥ 3 → advisory CHRONOTOPE_MONOTONY
     - distribution_entropy < 0.35 (归一) → advisory CHRONOTOPE_LOW_DIVERSITY
  6. 作者档 chronotope_signature.allowed_monotony 豁免 (室内沙龙剧/独角戏)

【与正交 scanner 区分】
  · scene_seam        (衔接手法·how transitions 写法)
  · scene_grounding   (感官接地有无)
  · location_signature(地点指纹·地点字段值)
  · L38               (时空体型·空间×时间×叙事功能聚类 = scene-level type label)

【北极星② / ⑤ 顾问非法官】作者档第一权威·全 advisory·codes
CHRONOTOPE_MONOTONY / CHRONOTOPE_LOW_DIVERSITY 绝不进 audit_hub.HARD_GATE_CODES。
env CHRONOTOPE_TYPOLOGY_MODE: off / shadow(默认) / active。

用法：python chronotope_typology_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

CODE_MONOTONY = "CHRONOTOPE_MONOTONY"
CODE_LOW_DIVERSITY = "CHRONOTOPE_LOW_DIVERSITY"

CHRONOTOPE_DICTS = {
    "road": re.compile(
        r"路途|驿道|官道|赶路|旅店|客栈|驿站|马背上|车厢里|长亭|码头|渡口|"
        r"列车上|公路上|沿途|风尘仆仆"),
    "threshold": re.compile(
        r"门槛|玄关|门口|桥头|边境|关口|入口处|界碑|分水岭|台阶上|门洞|关隘|"
        r"踏入|跨过|止步于|站在门外"),
    "castle": re.compile(
        r"城堡|王宫|朝堂|殿宇|塔楼|王座|雕梁|金銮|殿内|宫门|内殿|龙椅|"
        r"宫廷|官署|府衙"),
    "salon": re.compile(
        r"厅堂|书房|客厅|茶馆|酒楼包厢|雅间|会客|内室|起居室|榻边|案几旁|"
        r"沙发上|茶室"),
    "town": re.compile(
        r"街市|巷弄|胡同|坊间|街角|小铺|作坊|集贸|店铺|铺面|招幌|"
        r"市集|闹市|当铺"),
    "square": re.compile(
        r"广场|集市|法场|校场|演武场|朝会|公众场合|聚众|人群中央|"
        r"操场|大会场|露天集"),
    "idyll": re.compile(
        r"田园|院落|小院|茅屋|竹林|溪边|稻田|农舍|乡野|村口|田埂|"
        r"瓜棚|菜畦|篱笆"),
    "instance_dungeon": re.compile(
        r"副本|秘境|试炼塔|异空间|迷宫|地宫|遗迹密室|位面|"
        r"秘库|塔层"),
}

SCENE_SEPARATOR = re.compile(r"\n\s*\n")
SCENE_HEAD_WINDOW = 200    # 取场景前 200 字打 tag

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 500
MONOTONY_FLOOR = 3
ENTROPY_FLOOR = 0.35   # 归一 Shannon (除以 log2(7))


def _mode() -> str:
    m = (os.environ.get("CHRONOTOPE_TYPOLOGY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _author_allowed_monotony(project_root):
    """作者档 chronotope_signature.allowed_monotony (bool)·允许室内剧独角戏豁免。"""
    if not project_root:
        return False
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    sig = (obj or {}).get("chronotope_signature") if isinstance(obj, dict) else None
    if not isinstance(sig, dict):
        return False
    return bool(sig.get("allowed_monotony", False))


def _split_scenes(text):
    """按段落空行切场景·返回 scene 字符串列表 (非空)"""
    scenes = [s.strip() for s in SCENE_SEPARATOR.split(text) if s.strip()]
    return scenes


def tag_scene_chronotope(scene_text):
    """给场景前 SCENE_HEAD_WINDOW 字打 chronotope tag·返回 (tag, hit_counts)"""
    head = scene_text[:SCENE_HEAD_WINDOW]
    counts = {}
    for tag, rx in CHRONOTOPE_DICTS.items():
        c = len(rx.findall(head))
        if c > 0:
            counts[tag] = c
    if not counts:
        return "untagged", counts
    # 取最高 count·并列时按词典顺序优先 (threshold/road/castle 关键转折优先)
    priority = list(CHRONOTOPE_DICTS.keys())
    top_count = max(counts.values())
    top_tags = [t for t in priority if counts.get(t) == top_count]
    return top_tags[0], counts


def shannon_entropy_norm(distribution):
    """归一 Shannon (除以 log2(K))·K=7 chronotype 类型."""
    total = sum(distribution.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for v in distribution.values():
        if v <= 0:
            continue
        p = v / total
        h -= p * math.log2(p)
    k = len(CHRONOTOPE_DICTS)
    return h / math.log2(k) if k > 1 else 0.0


def max_streak(tags):
    """同型连续 streak 最大值."""
    if not tags:
        return 0, None
    best = 1
    best_tag = tags[0]
    cur = 1
    cur_tag = tags[0]
    for t in tags[1:]:
        if t == cur_tag:
            cur += 1
            if cur > best:
                best = cur
                best_tag = cur_tag
        else:
            cur = 1
            cur_tag = t
    return best, best_tag


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "chronotope_typology",
        "schema_version": "1.0",
        "mode": mode,
        "code": CODE_MONOTONY,
        "gate_level": "advisory",
        "verdict": "PASS",
        "violations": [],
        "warning": None,
    }
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    scenes = _split_scenes(text)
    if len(scenes) < 2:
        out["note"] = "场景数太少·跳过"
        return out

    scene_tags = []
    scene_details = []
    for i, sc in enumerate(scenes):
        tag, counts = tag_scene_chronotope(sc)
        scene_tags.append(tag)
        scene_details.append({"idx": i, "tag": tag, "head_count": counts,
                              "head_preview": sc[:60]})

    dist = {}
    for t in scene_tags:
        dist[t] = dist.get(t, 0) + 1
    out["scene_tags"] = scene_tags
    out["scene_count"] = len(scene_tags)
    out["distribution"] = dist
    out["distribution_entropy"] = round(shannon_entropy_norm(
        {k: v for k, v in dist.items() if k != "untagged"}), 3)

    streak, streak_tag = max_streak(scene_tags)
    out["monotony_streak"] = streak
    out["monotony_streak_tag"] = streak_tag

    has_threshold = "threshold" in dist
    out["has_threshold_anchor"] = has_threshold

    msgs = []
    codes_hit = []
    allowed_monotony = _author_allowed_monotony(project_root)
    if streak >= MONOTONY_FLOOR and not allowed_monotony:
        msgs.append(
            f"chronotope monotony_streak={streak} ({streak_tag}) ≥ {MONOTONY_FLOOR}"
            f"·同型场景连续堆积")
        codes_hit.append(CODE_MONOTONY)
    if (out["distribution_entropy"] < ENTROPY_FLOOR and len(scenes) >= 4
            and not allowed_monotony):
        msgs.append(
            f"chronotope distribution_entropy={out['distribution_entropy']} (归一·<{ENTROPY_FLOOR})"
            f"·时空体类型分布塌缩")
        codes_hit.append(CODE_LOW_DIVERSITY)

    if codes_hit:
        if mode == "active":
            for code in codes_hit:
                out["violations"].append({
                    "code": code,
                    "kind": "chronotope_typology",
                    "severity": "minor",
                    "message": " · ".join(msgs[:2]),
                    "scene_tags": scene_tags,
                    "distribution": dist,
                    "monotony_streak": streak,
                    "monotony_streak_tag": streak_tag,
                    "_doc": (
                        "Bakhtin 6+1 时空体分布·advisory·建议每 cluster ≥1 threshold "
                        "转折锚点·绝不 hard_gate"),
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = " | ".join(msgs)
        else:
            print(f"[SHADOW] chronotope_typology: {' | '.join(msgs)} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Bakhtin 时空体 6+1 型场景分布 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
