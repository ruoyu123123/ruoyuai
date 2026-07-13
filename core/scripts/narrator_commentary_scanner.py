#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""narrator_commentary_scanner.py — 作者/全知叙述者闯入·evaluative summary 密度
(advisory · cluster · 2026-06-20 R9 W5 Batch-M · L41)

【缺口】R9 联网调研合并 (Booth narrator intrusion + Cohn psycho-narration + implied_author_ethics):
LLM 默认产「全知点评句」过密 (例:「这便是 X 的本质」/「显然，他错了」/「毋庸置疑」)，等于
作者/全知叙述者大量 telling-weight 闯入 → 现代爽文/限知 POV 美感破坏。此前全系统:
  · L25 metalepsis_budget   查 frame-breaking (叙述层↔故事层越界次数)
  · L28 narratee_address    查 narratee 称谓一致性
  · implied_author_ethics   查 telling_intrusion_rate (但用「不得不说/笔者」少量词)
  · 【evaluative summary 句式密度零检测】
本 scanner 补：evaluative_summary_sentence 正则 + 全知点评句密度，三指标:
  ① commentary_density (per 1k CJK)
  ② chapter-end commentary share (末段 evaluative 句占比)
  ③ intra-action commentary (动作 burst 中插入的评价句密度)

与 R8 L25 Metalepsis 严格正交:
  · L25  frame-breaking (叙述层越界·【系统提示】/【作者注】marker)
  · L41  telling-weight (作者/全知点评句密度·无 marker)
内容轴交集仅在「narrator 闯入」语义边界·但形态完全不同（L25 看 marker，L41 看 evaluative
predicate 句式）·零词典重叠。

【做法 · 确定性纯规则正则 + 句末符号切句·零 LLM·零依赖】:
  1. evaluative_predicate 词典 (高确定性 evaluative summary 标志词):
     断言型: 显然|不得不说|毋庸置疑|可以说|众所周知|笔者|读者朋友
     总结型: 这便是|这就是|无疑|果然|正所谓|说到底|归根结底
     断语型: 事实上|实际上|说白了|换言之
  2. 切句·命中 evaluative 标志 → counted as evaluative sentence.
  3. commentary_density = evaluative_count / (cjk_total/1000)
  4. chapter-end share = 末 20% 字符范围内 evaluative_count / 末 20% 句子总数
  5. intra-action: 含动作动词 ('掏出/挥手/转身/扑上去/拔剑') 的段内含 evaluative 句的占比
  6. 作者档 narrator_voice_signature.commentary_target (per_1k 目标) 第一权威·无则通用兜底 0.8

【北极星② / ⑤ 顾问非法官】作者档第一权威·全 advisory，code NARRATOR_COMMENTARY_OVERUSE
绝不进 audit_hub.HARD_GATE_CODES。env NARRATOR_COMMENTARY_MODE: off / shadow(默认) / active。

【F3 AnchoredAI】每条 violation 输出 anchor_span(char_start/char_end/surface_text)，
供 gen_fixer 仅在锚定段做修订（out_of_anchor_edit_ratio 可观察）。

用法：python narrator_commentary_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "NARRATOR_COMMENTARY_OVERUSE"   # ⚠️ advisory 专用

# evaluative summary 词典 (高确定性 telling-weight 词)·精选剔除多义
EVALUATIVE_PREDICATES = re.compile(
    r"显然|不得不说|毋庸置疑|可以说|众所周知|笔者|读者朋友|"
    r"这便是|这就是|无疑|果然|正所谓|说到底|归根结底|"
    r"事实上|实际上|说白了|换言之|不容置疑|理所当然|"
    r"由此可见|由此可知|可见|总而言之|总之"
)

# 动作动词词典 (用于 intra-action commentary 检测)
ACTION_VERBS = re.compile(
    r"掏出|挥手|转身|扑上|拔剑|拔刀|抬手|抬脚|举枪|开火|抡起|"
    r"撞向|奔向|冲向|跃起|落下|劈出|刺出|砸下|挥落|举刀|举剑"
)

SENTENCE_END = re.compile(r"[。！？…]+")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 500


def _mode() -> str:
    m = (os.environ.get("NARRATOR_COMMENTARY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _author_commentary_target(project_root):
    """读作者档 narrator_voice_signature.commentary_target_per_1k·无 → None。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    sig = obj.get("narrator_voice_signature") or {}
    if not isinstance(sig, dict):
        return None
    v = sig.get("commentary_target_per_1k")
    if isinstance(v, (int, float)) and v >= 0:
        return float(v)
    return None


def _split_sentences_with_pos(text):
    """按句末符切句·返回 [(sent_text, start_pos, end_pos), ...]"""
    out = []
    pos = 0
    for m in SENTENCE_END.finditer(text):
        end = m.end()
        sent = text[pos:end].strip()
        if sent:
            out.append((sent, pos, end))
        pos = end
    if pos < len(text):
        tail = text[pos:].strip()
        if tail:
            out.append((tail, pos, len(text)))
    return out


def _scan_evaluative(sentences):
    """对每句检查是否含 evaluative_predicate·返回 [{idx,sent,start,end,hit_term}]"""
    hits = []
    for i, (sent, start, end) in enumerate(sentences):
        m = EVALUATIVE_PREDICATES.search(sent)
        if m:
            hits.append({
                "idx": i,
                "sentence": sent,
                "char_start": start,
                "char_end": end,
                "hit_term": m.group(0),
            })
    return hits


def _chapter_end_share(text, hits, total_cjk):
    """末 20% 范围 evaluative 句占比 (相对末 20% 句子总数)·返回 (share, end_hits, end_sents)"""
    if total_cjk < 500 or not hits:
        return 0.0, 0, 0
    cutoff = int(len(text) * 0.8)
    end_hits = [h for h in hits if h["char_start"] >= cutoff]
    sentences = _split_sentences_with_pos(text)
    end_sents = [s for s in sentences if s[1] >= cutoff]
    if not end_sents:
        return 0.0, 0, 0
    share = len(end_hits) / len(end_sents)
    return share, len(end_hits), len(end_sents)


def _intra_action_density(text, hits):
    """按段落切·统计含动作动词的段内 evaluative 句密度·返回 (intra_density, action_paras)"""
    paragraphs = [p for p in text.split("\n") if p.strip()]
    action_paras = []
    intra_hits = 0
    for p in paragraphs:
        if ACTION_VERBS.search(p):
            action_paras.append(p)
            if EVALUATIVE_PREDICATES.search(p):
                intra_hits += 1
    if not action_paras:
        return 0.0, 0
    return intra_hits / len(action_paras), len(action_paras)


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "narrator_commentary",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
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

    sentences = _split_sentences_with_pos(text)
    hits = _scan_evaluative(sentences)
    density = round(len(hits) / (cjk / 1000.0), 3) if cjk else 0
    end_share, end_hits, end_sents = _chapter_end_share(text, hits, cjk)
    intra_density, action_paras = _intra_action_density(text, hits)

    out["commentary_count"] = len(hits)
    out["commentary_density_per_1k"] = density
    out["chapter_end_commentary_share"] = round(end_share, 3)
    out["chapter_end_evaluative"] = end_hits
    out["chapter_end_sentences"] = end_sents
    out["intra_action_density"] = round(intra_density, 3)
    out["action_paragraph_count"] = action_paras
    out["sample_hits"] = [
        {"sentence": h["sentence"][:60], "hit_term": h["hit_term"],
         "char_start": h["char_start"], "char_end": h["char_end"]}
        for h in hits[:8]
    ]

    target = _author_commentary_target(project_root)
    if target is None:
        target = 0.8  # 通用兜底
        out["target_source"] = "uniform_fallback"
    else:
        out["target_source"] = "author_profile"
    out["commentary_target_per_1k"] = target

    over_density = density > target * 1.5  # 超过目标 1.5×
    over_end = end_share > 0.30           # 末段 >30% 评价句
    over_intra = intra_density > 0.20     # 动作段内 >20%

    msgs = []
    if over_density:
        msgs.append(
            f"commentary_density={density}/千字>目标 {target}*1.5·全知点评闯入过密")
    if over_end:
        msgs.append(
            f"chapter-end commentary share={round(end_share,3)} (>0.30)·章末评价句堆积")
    if over_intra:
        msgs.append(
            f"intra-action commentary={round(intra_density,3)} (>0.20)·动作中插入评价")

    if msgs and hits:
        if mode == "active":
            for h in hits[:8]:
                anchor = {
                    "char_start": h["char_start"],
                    "char_end": h["char_end"],
                    "surface_text": h["sentence"][:200],
                }
                out["violations"].append({
                    "code": ISSUE_CODE,
                    "kind": "narrator_commentary",
                    "severity": "minor",
                    "message": "·".join(msgs[:2]),
                    "hit_term": h["hit_term"],
                    "anchor_span": anchor,
                    "_doc": "F3 AnchoredAI·anchor_span 供 gen_fixer 仅锚定段修订·advisory 绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = "; ".join(msgs)
        else:
            print(f"[SHADOW] narrator_commentary: {'; '.join(msgs)} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="作者/全知叙述者闯入·evaluative summary 密度 (advisory · shadow)")
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
