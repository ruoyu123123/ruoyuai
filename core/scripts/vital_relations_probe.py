#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vital_relations_probe.py — CBT 7 类生命关系密度子模块 · R22 W10 Batch-FF · P2

【缺口】Fauconnier&Turner 概念整合(CBT) 的 vital_relations 是判断 blend 是否真整合
(emergent meaning) 还是只堆词的关键诊断维度·若渝原系统零检测。

【做法 · 确定性 · 零 LLM/零联网】
  · 7 类显式语言标记 lexicon(_placeholder=true·真词典 defer):
    - identity (同一性)        : 就是 / 即 / 等于 / 也是 / 便是 / 本是
    - causation (因果性)        : 因 / 故 / 所以 / 因为 / 才会 / 由于 / 因此
    - time (时间性)             : 过去 / 现在 / 未来 / 当年 / 此刻 / 之前 / 之后
    - change (变化性)           : 变成 / 化作 / 化为 / 演变 / 转 / 蜕变 / 沦为
    - part_whole (部分-整体)    : 乃 / 属于 / 之一 / 之中 / 一部分 / 组成 / 由…构成
    - intentionality (意图性)   : 想 / 欲 / 意图 / 想要 / 打算 / 立志 / 决意
    - analogy (类比性)          : 像 / 如同 / 仿佛 / 犹如 / 好比 / 之于 / 正如

  · 输入：cluster 草稿文本 + (可选)input_space_A/B 的 signature_lexemes
  · 输出：
      - vital_relations_density        每 1k CJK 标记 hit 数
      - relation_distribution          7 类 share
      - top_compressed_pairs           top-K(A_token ∈ A space, B_token ∈ B space)
                                       通过 relation marker 共现窗口 = 一句内
      - signature_lexemes_hits         A/B 各自命中数

【与既有 scanner 严格正交】
  · rhetorical_balance        → 38 格分布层
  · semantic_slop / scene_grounding → 与 blend 整合不相关
  · vital_relations_probe(本) → 仅判 CBT 整合度

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  作为子模块嵌 premise_blend_card 检测·CLI 也直跑(便于回测)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_analyzer as sa  # noqa: E402

# 7 类 vital_relations 占位 lexicon
VITAL_RELATIONS_LEXICON: dict[str, list[str]] = {
    "identity":      ["就是", "即", "等于", "也是", "便是", "本是", "无非", "即是"],
    "causation":     ["因为", "所以", "因此", "由于", "故", "才会", "于是", "导致"],
    "time":          ["过去", "现在", "未来", "当年", "此刻", "之前", "之后", "如今"],
    "change":        ["变成", "化作", "化为", "演变", "蜕变", "沦为", "转化", "化身"],
    "part_whole":    ["乃", "属于", "之一", "之中", "一部分", "组成", "构成", "之内"],
    "intentionality":["想", "欲", "意图", "想要", "打算", "立志", "决意", "意在"],
    "analogy":       ["像", "如同", "仿佛", "犹如", "好比", "正如", "如", "似"],
}
VITAL_RELATIONS_LEXICON["_placeholder"] = True  # type: ignore

CATEGORIES = ("identity", "causation", "time", "change", "part_whole", "intentionality", "analogy")


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def count_relation_hits(text: str) -> dict[str, int]:
    """逐类 hit 计数·返回 {cat: int}"""
    out: dict[str, int] = {}
    for cat in CATEGORIES:
        terms = VITAL_RELATIONS_LEXICON.get(cat) or []
        n = 0
        for t in terms:
            if t:
                n += text.count(t)
        out[cat] = n
    return out


def relation_distribution(hits: dict[str, int]) -> dict[str, float]:
    """7 类 share·空文本均匀"""
    s = sum(hits.get(c, 0) for c in CATEGORIES)
    if s <= 0:
        return {c: round(1.0 / len(CATEGORIES), 4) for c in CATEGORIES}
    return {c: round(hits[c] / s, 4) for c in CATEGORIES}


def _find_cooccurrence(text: str, a_tokens: list[str], b_tokens: list[str],
                       max_pairs: int = 8) -> list[dict]:
    """在每一句内找 (A_token, B_token) 共现·上限 max_pairs"""
    sents = sa.split_sentences(text)
    pairs: list[dict] = []
    seen: set = set()
    for s in sents:
        a_hits = [a for a in a_tokens if a and a in s]
        b_hits = [b for b in b_tokens if b and b in s]
        if not (a_hits and b_hits):
            continue
        # 任一 A × B 笛卡儿乘积·去重
        for a in a_hits:
            for b in b_hits:
                if a == b:
                    continue
                key = (a, b)
                if key in seen:
                    continue
                seen.add(key)
                # 判断本句中是否有任一 vital_relation marker
                rel_marker = None
                for cat in CATEGORIES:
                    for t in VITAL_RELATIONS_LEXICON.get(cat) or []:
                        if t and t in s:
                            rel_marker = (cat, t)
                            break
                    if rel_marker:
                        break
                pairs.append({"a": a, "b": b, "relation": rel_marker[0] if rel_marker else None,
                              "marker": rel_marker[1] if rel_marker else None,
                              "sentence": s[:80]})
                if len(pairs) >= max_pairs:
                    return pairs
    return pairs


def probe(text: str, input_a_lexemes: list[str] | None = None,
          input_b_lexemes: list[str] | None = None,
          max_pairs: int = 8) -> dict:
    """运行 7 类标记 + 跨 input space 共现统计·零 LLM 纯确定性"""
    cjk = _cjk_count(text)
    hits = count_relation_hits(text)
    total = sum(hits.get(c, 0) for c in CATEGORIES)
    per_kcjk = round(total / (cjk / 1000.0), 3) if cjk else 0.0
    dist = relation_distribution(hits)
    a_lex = list(input_a_lexemes or [])
    b_lex = list(input_b_lexemes or [])
    a_hits_count = sum(text.count(t) for t in a_lex if t)
    b_hits_count = sum(text.count(t) for t in b_lex if t)
    compressed_pairs = _find_cooccurrence(text, a_lex, b_lex, max_pairs=max_pairs) if a_lex and b_lex else []
    return {
        "schema_version": "1.0",
        "cjk": cjk,
        "lexicon_placeholder": True,
        "hits_by_category": hits,
        "total_hits": total,
        "vital_relations_density": per_kcjk,
        "relation_distribution": dist,
        "signature_lexemes_hits": {"a_total": a_hits_count, "b_total": b_hits_count},
        "top_compressed_pairs": compressed_pairs,
    }


def main():
    ap = argparse.ArgumentParser(description="CBT 7 类 vital_relations 探针")
    ap.add_argument("draft_path")
    ap.add_argument("--a-lexemes", default=None, help="逗号分隔 input space A 签名词")
    ap.add_argument("--b-lexemes", default=None, help="逗号分隔 input space B 签名词")
    args = ap.parse_args()
    try:
        text = Path(args.draft_path).read_text(encoding="utf-8")
    except OSError as e:
        print(json.dumps({"error": f"读失败: {e}"}, ensure_ascii=False))
        sys.exit(2)
    a_lex = [x.strip() for x in (args.a_lexemes or "").split(",") if x.strip()]
    b_lex = [x.strip() for x in (args.b_lexemes or "").split(",") if x.strip()]
    rep = probe(text, a_lex or None, b_lex or None)
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
