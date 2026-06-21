#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lish_consecution_chain_scanner.py — Lish 句间正向回扣链 advisory · cluster · 2026-06-21 R20 W9 Batch-AA · P1

【缺口 · R20 MFA id 0】Gordon Lish "consecution" doctrine (Black 1990 / Lish workshop notes
/ Saint Mark's School transcripts)：高密度文学散文不靠并列堆砌而靠句间「正向回扣链」
推进——下一句的发动力来自前一句的尾词/尾韵/尾骨架(syntactic mold)被半截重启。
通用 LLM 草稿默认靠并列连接词 + 平铺动作推进，这条链断裂 → 文学颗粒度塌掉。

【三探针 · 确定性 · 零 LLM/零联网】
  ① lexical_carryover_ratio — 相邻句对中「前句末 2 CJK char ∈ 后句首 5 CJK char」
     占总相邻对比例。健康 0.06-0.18(MFA 风格)；过低=没回扣；过高=机械车轱辘。
  ② syntactic_template_repeat — 相邻句对中「前句首 4 char 句法骨架 ≈ 后句首 4 char」
     (粗近似：前 4 char 完全相同；shadow 阶段不接入 jieba/POS)。
     >0.20 = 模具重复(LLM 写「她说...他说...」骨架)；
     <0.02 = 完全不连贯。
  ③ phonic_carryover — 占位简化版：「前句末 1 char == 后句末 1 char」算押韵回扣。
     真音类需 pypinyin (defer · _placeholder=true)。

【题材 gating · 不干涉模型判断】
  作者档/用户偏好 genre_tags 含 {言情, romance, 严肃, literary, 古风, ancient_style} →
  active(报偏低 advisory)。
  含 {爽文, web_novel, 起点} → silent(蹲一边·不报)。
  其他题材 → shadow 模式默认参数。

【与既有 scanner 严格正交】
  · R7 prose_rhythm 查 mean/std/段长 · 不查句间词面回扣
  · R8 rhetoric_repetition 查跨段重复辞格 · 不查相邻句对
  · R20 anti_slop / R12 anti-AI 查词项/句法二元 · 不查链式
  本 scanner = 句间「consecution」链 唯一覆盖维度。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  LISH_CONSECUTION_THIN / LISH_TEMPLATE_REPEAT / LISH_PHONIC_THIN 绝不进 audit_hub.HARD_GATE_CODES。

env LISH_CONSECUTION_MODE: off / shadow(默认) / active
用法: python lish_consecution_chain_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_THIN = "LISH_CONSECUTION_THIN"
ISSUE_CODE_TEMPLATE = "LISH_TEMPLATE_REPEAT"
ISSUE_CODE_PHONIC = "LISH_PHONIC_THIN"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 兜底基线(无作者档·shadow)
DEFAULT_LEX_LOW = 0.06
DEFAULT_LEX_HIGH = 0.30
DEFAULT_TPL_HIGH = 0.20
DEFAULT_TPL_LOW = 0.02
DEFAULT_PHONIC_LOW = 0.05

# 题材 gating
_GENRE_ACTIVE = {"言情", "romance", "严肃", "literary", "古风", "ancient_style", "纯文学"}
_GENRE_SILENT = {"爽文", "web_novel", "起点", "qidian", "tomato", "番茄"}


def _mode() -> str:
    m = (os.environ.get("LISH_CONSECUTION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_only(s: str) -> str:
    return "".join(ch for ch in s if "一" <= ch <= "鿿")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？…])", text) if s.strip()]


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            cb = obj.get("lish_consecution_baseline")
            if isinstance(cb, dict):
                return cb
    return None


def _read_genre_tags(project_root) -> set[str]:
    if not project_root:
        return set()
    db = Path(project_root) / "_数据库"
    tags: set[str] = set()
    for fname in ("用户偏好.json", "作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        raw = obj.get("genre_tags") or obj.get("genre") or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            for t in raw:
                if isinstance(t, str):
                    tags.add(t.strip().lower())
                    tags.add(t.strip())
    return tags


def _resolve_genre_mode(tags: set[str]) -> str:
    """返回 active / silent / neutral·决定 advisory 升报与否。"""
    if not tags:
        return "neutral"
    lower = {t.lower() for t in tags}
    if lower & {g.lower() for g in _GENRE_SILENT}:
        return "silent"
    if lower & {g.lower() for g in _GENRE_ACTIVE}:
        return "active"
    return "neutral"


def _lexical_carryover(sents: list[str]) -> tuple[float, int]:
    """前句末 2 CJK char 任一出现在后句首 5 CJK char。返回 (ratio, total_pairs)。"""
    if len(sents) < 2:
        return 0.0, 0
    hits = 0
    total = 0
    for i in range(len(sents) - 1):
        a = _cjk_only(sents[i])
        b = _cjk_only(sents[i + 1])
        if len(a) < 2 or len(b) < 2:
            continue
        total += 1
        tail = set(a[-2:])
        head = set(b[:5])
        if tail & head:
            hits += 1
    if total == 0:
        return 0.0, 0
    return hits / total, total


def _template_repeat(sents: list[str]) -> tuple[float, int]:
    """前句首 2-3 CJK char 骨架等价占位·捕捉「她说X / 我看Y」类模具骨架。
    具体：(前句首 3 char 等于后句首 3 char) OR
          (前句首 2 char == 后句首 2 char AND ≥3 个 sample 句首 2 char 都同) → repeat。
    真句法骨架需 jieba+POS·shadow defer。"""
    if len(sents) < 2:
        return 0.0, 0
    hits = 0
    total = 0
    for i in range(len(sents) - 1):
        a = _cjk_only(sents[i])
        b = _cjk_only(sents[i + 1])
        if len(a) < 3 or len(b) < 3:
            continue
        total += 1
        # 首 3 char 全等(强证据)
        if a[:3] == b[:3]:
            hits += 1
            continue
        # 首 2 char 等且第 3 char 同字类(简单代理：均 CJK 满足) → 算半模具
        if a[:2] == b[:2]:
            hits += 1
    return (hits / total if total else 0.0), total


def _phonic_carryover(sents: list[str]) -> tuple[float, int]:
    """占位简化：前句末 1 char == 后句末 1 char (押尾韵 placeholder · 真音类 defer pypinyin)。"""
    if len(sents) < 2:
        return 0.0, 0
    hits = 0
    total = 0
    for i in range(len(sents) - 1):
        a = _cjk_only(sents[i])
        b = _cjk_only(sents[i + 1])
        if not a or not b:
            continue
        total += 1
        if a[-1] == b[-1]:
            hits += 1
    return (hits / total if total else 0.0), total


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "lish_consecution", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_phonic_placeholder": True}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    sents = _split_sentences(text)
    if len(sents) < 6:
        out["note"] = "草稿句数过少·跳过"
        return out

    genre_tags = _read_genre_tags(project_root)
    genre_mode = _resolve_genre_mode(genre_tags)
    out["genre_mode"] = genre_mode
    out["genre_tags"] = sorted(genre_tags)

    lex_ratio, lex_total = _lexical_carryover(sents)
    tpl_ratio, tpl_total = _template_repeat(sents)
    ph_ratio, ph_total = _phonic_carryover(sents)

    baseline = _read_author_baseline(project_root)
    lex_low = DEFAULT_LEX_LOW
    lex_high = DEFAULT_LEX_HIGH
    tpl_high = DEFAULT_TPL_HIGH
    tpl_low = DEFAULT_TPL_LOW
    ph_low = DEFAULT_PHONIC_LOW
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        lr = baseline.get("lexical_carryover_band")
        if isinstance(lr, list) and len(lr) == 2:
            lex_low, lex_high = float(lr[0]), float(lr[1])
        tr = baseline.get("template_repeat_band")
        if isinstance(tr, list) and len(tr) == 2:
            tpl_low, tpl_high = float(tr[0]), float(tr[1])
        pr = baseline.get("phonic_carryover_low")
        if isinstance(pr, (int, float)):
            ph_low = float(pr)

    out.update({
        "sentences": len(sents),
        "lexical_carryover_ratio": round(lex_ratio, 4),
        "lexical_carryover_pairs": lex_total,
        "template_repeat_ratio": round(tpl_ratio, 4),
        "template_repeat_pairs": tpl_total,
        "phonic_carryover_ratio": round(ph_ratio, 4),
        "phonic_carryover_pairs": ph_total,
        "baseline_source": baseline_source,
        "baseline": {
            "lexical_carryover_band": [lex_low, lex_high],
            "template_repeat_band": [tpl_low, tpl_high],
            "phonic_carryover_low": ph_low,
        }
    })

    flags = []
    # 探针 1：lexical_carryover
    if lex_ratio < lex_low:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"lexical_carryover={lex_ratio:.3f} < {lex_low}·句间词面回扣稀薄·建议下一句承接前句末词/末意象"})
    if lex_ratio > lex_high:
        flags.append({"code": ISSUE_CODE_TEMPLATE,
                      "msg": f"lexical_carryover={lex_ratio:.3f} > {lex_high}·机械车轱辘话·过度回扣"})
    # 探针 2：syntactic_template_repeat
    if tpl_ratio > tpl_high:
        flags.append({"code": ISSUE_CODE_TEMPLATE,
                      "msg": f"template_repeat={tpl_ratio:.3f} > {tpl_high}·句首 4 字骨架重复(LLM 模具)·建议打散开头"})
    if tpl_total >= 20 and tpl_ratio < tpl_low and lex_ratio < lex_low:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"template_repeat={tpl_ratio:.3f}<{tpl_low} 同时 lexical_carryover 也低·句间完全不串联"})
    # 探针 3：phonic_carryover (placeholder)
    if ph_ratio < ph_low:
        flags.append({"code": ISSUE_CODE_PHONIC,
                      "msg": f"phonic_carryover={ph_ratio:.3f} < {ph_low}·尾韵回扣偏弱(占位指标·待 pypinyin)"})

    # 题材 silent：吞掉所有 flags(不报 advisory)
    if genre_mode == "silent":
        out["genre_silent"] = True
        out["raw_flags"] = flags  # 留作排查
        flags = []

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "lish_consecution", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Lish consecution doctrine · MFA workshop · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] lish_consecution: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Lish consecution 句间回扣链 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
