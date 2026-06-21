#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rhetorical_inventory.py — 陈望道 38 格四类修辞清单·R22 W10 Batch-DD·P0 STRONG

【缺口】《修辞学发凡》(陈望道 1932)四类 38 格(材料/意境/词语/章句类)是中文修辞最权威分类，
但若渝原系统只覆盖少数西方比喻 + 个别中文格(顶针/拈连 R22 P1 单独立项)，整张 38 格热图全空白。

【做法 · 确定性 · 零 LLM/零联网】
  · 词典 core/data/chen_wangdao_rhetorical_inventory.json
    - 4 类(material/imagery/wording/syntax) × 各 8-10 格 × 各 8 触发词(_placeholder=true)
    - 真词典需修辞标注语料授权 defer
  · count_figure_hits(text, inventory) → {category: {figure: int}}
  · category_distribution(text) → {category: share}  (share 之和 = 1.0)
  · kl_divergence(p, q) → 对称 KL(自然对数·两侧填 epsilon 防 0)
  · shannon_entropy(dist) → bits

【与既有 scanner 严格正交】
  · zeugma_scanner (拈连·单格深扫) → 本者出现 + 分类层 only
  · anadiplosis_scanner (顶针·单格深扫) → 本者出现 + 分类层 only
  · rhetoric_repetition 等查重复 → 本者查 38 格分布
  本模块 = 38 格分类层唯一覆盖。

【北极星】②④⑤ 作者档第一权威 · cluster 视野 · advisory · 绝不 hard_gate

用法（模块）:
  from rhetorical_inventory import load_inventory, count_figure_hits, category_distribution
"""
from __future__ import annotations

import json
import math
from pathlib import Path

_INVENTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "chen_wangdao_rhetorical_inventory.json"

CATEGORIES = ("material", "imagery", "wording", "syntax")


def load_inventory() -> dict:
    """读取 38 格清单·JSON 缺失 → 极简兜底"""
    try:
        return json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 兜底：每类 2 格 × 2 词
        return {
            "_placeholder": True,
            "figures": {
                "material": {"譬喻": ["像", "如"], "借代": ["巾帼", "丹青"]},
                "imagery": {"夸张": ["万丈", "顷刻"], "比拟": ["拟人", "似乎在"]},
                "wording": {"复叠": ["渐渐", "茫茫"], "省略": ["略", "不赘述"]},
                "syntax": {"反复": ["再", "又"], "对偶": ["相对", "对仗"]},
            },
        }


def count_figure_hits(text: str, inventory: dict | None = None) -> dict:
    """统计文本里 38 格各格命中次数·按四类组织·返回 {category: {figure: int}}"""
    if inventory is None:
        inventory = load_inventory()
    figs = inventory.get("figures") or {}
    out: dict = {}
    for cat in CATEGORIES:
        cat_figs = figs.get(cat) or {}
        bucket: dict = {}
        for fig_name, terms in cat_figs.items():
            n = 0
            for t in terms or []:
                if t:
                    n += text.count(t)
            bucket[fig_name] = n
        out[cat] = bucket
    return out


def category_totals(per_figure: dict) -> dict:
    """按四类求总命中数·{category: int}"""
    return {cat: sum((per_figure.get(cat) or {}).values()) for cat in CATEGORIES}


def category_distribution(text: str, inventory: dict | None = None) -> dict:
    """四类分布·share 之和 = 1.0 (空文本退化为均匀分布)"""
    hits = count_figure_hits(text, inventory)
    totals = category_totals(hits)
    s = sum(totals.values())
    if s <= 0:
        return {c: 0.25 for c in CATEGORIES}
    return {c: totals[c] / s for c in CATEGORIES}


def shannon_entropy(dist: dict) -> float:
    """Shannon 熵（bits）·dist 为 {category: share}"""
    H = 0.0
    for v in dist.values():
        if v > 0:
            H -= v * math.log2(v)
    return round(H, 4)


def kl_divergence(p: dict, q: dict, eps: float = 1e-6) -> float:
    """对称 KL 散度·两侧填 epsilon 防 0·自然对数 nats"""
    total = 0.0
    for c in CATEGORIES:
        pv = max(p.get(c, 0.0), eps)
        qv = max(q.get(c, 0.0), eps)
        total += pv * math.log(pv / qv) + qv * math.log(qv / pv)
    return round(total / 2.0, 4)


def per_kcjk(per_figure: dict, cjk: int) -> dict:
    """按 1000 CJK 归一·{category: {figure: per_kcjk}}"""
    if cjk <= 0:
        return {c: {f: 0.0 for f in (per_figure.get(c) or {})} for c in CATEGORIES}
    out = {}
    for cat in CATEGORIES:
        out[cat] = {f: round(n / (cjk / 1000.0), 3)
                    for f, n in (per_figure.get(cat) or {}).items()}
    return out


def match_subset_by_genre(inventory: dict, genre: str | None) -> list[str]:
    """按题材匹配高频辞格 subset 名列表（build_manifest 注入用）

    返回若干 figure 名 · 题材 prior 占位（弱模型友好）：
      仙侠 / 玄幻 → material.借代 + imagery.夸张 + syntax.排比
      古风 / 历史 → material.引用 + wording.警句 + syntax.对偶
      搞笑 / 都市轻 → material.拈连 + imagery.反语 + wording.飞白
      正剧 / 严肃 → imagery.警策 + wording.警句 + syntax.层递
      默认（unknown） → imagery.比拟 + syntax.排比
    """
    g = (genre or "").lower()
    PRIOR = {
        "xianxia": ["借代", "夸张", "排比"],
        "仙侠": ["借代", "夸张", "排比"],
        "xuanhuan": ["借代", "夸张", "排比"],
        "玄幻": ["借代", "夸张", "排比"],
        "gufeng": ["引用", "警句", "对偶"],
        "古风": ["引用", "警句", "对偶"],
        "historical": ["引用", "警句", "对偶"],
        "历史": ["引用", "警句", "对偶"],
        "comedy": ["拈连", "反语", "飞白"],
        "搞笑": ["拈连", "反语", "飞白"],
        "都市": ["拈连", "反语", "飞白"],
        "serious": ["警策", "警句", "层递"],
        "literary": ["警策", "警句", "层递"],
        "正剧": ["警策", "警句", "层递"],
    }
    return PRIOR.get(g, ["比拟", "排比"])
