#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rasa_causal_chain_scanner.py — vibhāva-anubhāva 三段闭环 · R25 W13 Batch-MM · P1

【缺口 · Bharata 公式 / CEA framework arxiv 1903.06901 / SemEval 2024】
情感闭环 = vibhāva（诱因 · 外部刺激）→ sthāyī（持续 rasa）→ anubhāva（外显
表征）。三段缺一 = 断链：
  · 【A】sthāyī≠neutral 但 vibhāva=0 — 情感无诱因突生
  · 【B】sthāyī 强烈（raudra/bhayānaka/karuṇā/vīra）但 anubhāva=0 — 只内心
        独白·情感未落地
  · 【C】vibhāva 充分但 sthāyī≠预期 — 因果错位

LLM-judge 真版 defer · 当前用占位规则 fallback（_placeholder=true）。
≥3 命中 → cluster 提示。

【做法 · 确定性 · 零 LLM/零联网（占位规则 fallback）】
  · 段拆 `\n\n` · 每段抽三元组：
    - vibhāva = 外部刺激词（看见/听见/突然/眼前/面前/身后/对方 + 动作）
    - sthāyī = 9 navarasa 词典命中（复用 cluster_rasa_layer 词典理念）
    - anubhāva = 外显反应词（站起/后退/握紧拳/咬牙/转身/颤抖/咆哮 + 表情动作）
  · ≥3 段命中 A/B/C 任一 → cluster 提示
  · 作者档 causal_chain_tolerance.{A,B,C} 允许豁免某类比例（冷峻文风默
    许 B 高比例·辰东冷叙述默许 C）

【三 advisory · 全 advisory shadow】
  · RASA_CAUSAL_BREAK_NO_VIBHAVA    — 【A】≥3 命中·情感无诱因突生
  · RASA_CAUSAL_BREAK_NO_ANUBHAVA   — 【B】≥3 命中·情感未落地
  · RASA_CAUSAL_BREAK_MISMATCH      — 【C】≥3 命中·因果错位
  · RASA_CAUSAL_OK                  — 全无 · info

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  RASA_CAUSAL_* 绝不进 audit_hub.HARD_GATE_CODES。

env RASA_CAUSAL_CHAIN_MODE: off / shadow（默认） / active
用法: python rasa_causal_chain_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_NO_VIBHAVA = "RASA_CAUSAL_BREAK_NO_VIBHAVA"
ISSUE_CODE_NO_ANUBHAVA = "RASA_CAUSAL_BREAK_NO_ANUBHAVA"
ISSUE_CODE_MISMATCH = "RASA_CAUSAL_BREAK_MISMATCH"
ISSUE_CODE_OK = "RASA_CAUSAL_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_VIBHAVA_LEX = {
    "_placeholder": True,
    "_doc": "R25 W13 Batch-MM·vibhāva 诱因词占位",
    "_words": [
        "看见", "看到", "听见", "听到", "突然", "眼前", "面前",
        "身后", "迎面", "瞥见", "察觉", "扑面", "撞见", "撞上",
        "传来", "响起", "袭来", "扑来",
    ],
}

# sthāyī 词典（复用 9 navarasa · 简化）
_STHAYI_STRONG = {"raudra", "bhayanaka", "karuna", "vira"}
_STHAYI_LEX = {
    "_placeholder": True,
    "sringara": ["爱恋", "缠绵", "心动", "钟情"],
    "hasya": ["哈哈", "调笑", "戏谑", "捧腹"],
    "karuna": ["悲痛", "哀伤", "悲泣", "凄然"],
    "raudra": ["怒喝", "震怒", "暴怒", "怒火"],
    "vira": ["凛然", "豪气", "凌云", "无畏"],
    "bhayanaka": ["惊惧", "胆寒", "毛骨", "颤栗"],
    "bibhatsa": ["恶心", "腥臭", "作呕", "腐烂"],
    "adbhuta": ["惊奇", "诧异", "骇然", "震惊"],
    "shanta": ["平静", "安宁", "祥和", "止水"],
}

_ANUBHAVA_LEX = {
    "_placeholder": True,
    "_doc": "R25 W13 Batch-MM·anubhāva 外显反应词占位",
    "_words": [
        "站起", "后退", "握紧拳", "咬牙", "转身", "颤抖", "咆哮",
        "脸色一变", "倒吸一口", "退后一步", "拍案", "拳头一握",
        "瞳孔一缩", "牙关紧咬", "胸口起伏",
    ],
}

CHAIN_BREAK_THRESHOLD = 3   # ≥3 段命中即提示


def _mode() -> str:
    m = (os.environ.get("RASA_CAUSAL_CHAIN_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _split_paragraphs(text: str) -> list:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _hit(text: str, words: list) -> int:
    return sum(1 for w in words if w in text)


def _detect_sthayi(para: str) -> tuple:
    """返回 (label or None, strength: 'strong'|'mild'|None)。"""
    best, best_count = None, 0
    for label, words in _STHAYI_LEX.items():
        if label.startswith("_"):
            continue
        c = sum(para.count(w) for w in words)
        if c > best_count:
            best, best_count = label, c
    if best is None or best_count == 0:
        return (None, None)
    strength = "strong" if best in _STHAYI_STRONG else "mild"
    return (best, strength)


def _load_tolerance(project_root) -> dict:
    """读 _数据库/作者风格.json · causal_chain_tolerance · 作者档第一权威。"""
    default = {"A": 0, "B": 0, "C": 0, "author_owned": False}
    if not project_root:
        return default
    try:
        p = Path(project_root) / "_数据库" / "作者风格.json"
        if not p.exists():
            return default
        prof = json.loads(p.read_text(encoding="utf-8"))
        tol = prof.get("causal_chain_tolerance") or {}
        if tol:
            out = {"author_owned": True}
            for k in ("A", "B", "C"):
                out[k] = int(tol.get(k, 0))
            return out
        return default
    except Exception:
        return default


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "rasa_causal_chain_scanner", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": True,
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 800:
        out["note"] = "草稿太短·跳过"
        return out

    paragraphs = _split_paragraphs(text)
    breaks_a, breaks_b, breaks_c = [], [], []
    triples = []
    for i, p in enumerate(paragraphs):
        v = _hit(p, _VIBHAVA_LEX["_words"])
        sthayi_label, strength = _detect_sthayi(p)
        a = _hit(p, _ANUBHAVA_LEX["_words"])
        # 【A】sthāyī 存在但 vibhāva=0
        if sthayi_label and v == 0:
            breaks_a.append(i)
        # 【B】sthāyī strong 但 anubhāva=0
        if strength == "strong" and a == 0:
            breaks_b.append(i)
        # 【C】vibhāva 充分（≥2）但 sthāyī 缺失
        if v >= 2 and sthayi_label is None:
            breaks_c.append(i)
        triples.append({"para_idx": i, "vibhava": v,
                        "sthayi": sthayi_label, "strength": strength,
                        "anubhava": a})

    tol = _load_tolerance(project_root)
    eff_a = max(0, len(breaks_a) - tol.get("A", 0))
    eff_b = max(0, len(breaks_b) - tol.get("B", 0))
    eff_c = max(0, len(breaks_c) - tol.get("C", 0))

    out.update({
        "cjk": cjk,
        "paragraph_count": len(paragraphs),
        "triples": triples,
        "breaks_A_no_vibhava": breaks_a,
        "breaks_B_no_anubhava": breaks_b,
        "breaks_C_mismatch": breaks_c,
        "effective_breaks": {"A": eff_a, "B": eff_b, "C": eff_c},
        "tolerance": tol,
        "_threshold": CHAIN_BREAK_THRESHOLD,
    })

    flags = []
    if eff_a >= CHAIN_BREAK_THRESHOLD:
        flags.append({"code": ISSUE_CODE_NO_VIBHAVA, "severity": "minor",
                      "msg": f"【A】no_vibhava 断链 ×{eff_a}·情感无诱因突生"})
    if eff_b >= CHAIN_BREAK_THRESHOLD:
        flags.append({"code": ISSUE_CODE_NO_ANUBHAVA, "severity": "minor",
                      "msg": f"【B】no_anubhava 断链 ×{eff_b}·strong sthāyī 未落地"})
    if eff_c >= CHAIN_BREAK_THRESHOLD:
        flags.append({"code": ISSUE_CODE_MISMATCH, "severity": "minor",
                      "msg": f"【C】mismatch 断链 ×{eff_c}·因果错位"})
    if not flags:
        flags.append({"code": ISSUE_CODE_OK, "severity": "info",
                      "msg": (f"三链 A={eff_a} B={eff_b} C={eff_c}"
                              f" < 阈值 {CHAIN_BREAK_THRESHOLD}")})

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "rasa_causal_chain",
                "severity": f["severity"],
                "code": f["code"], "message": f["msg"],
                "_doc": "R25 W13 Batch-MM·Bharata vibhāva-anubhāva·advisory·绝不 hard_gate",
            })
        minor = [f for f in flags if f["severity"] == "minor"]
        out["verdict"] = "FAIL_MINOR" if minor else "PASS"
        out["warning"] = "·".join(f["msg"] for f in minor) or None
    elif mode == "shadow":
        minor = [f for f in flags if f["severity"] == "minor"]
        if minor:
            print("[SHADOW] rasa_causal_chain: "
                  + "·".join(f["msg"] for f in minor) + " — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Rasa 因果链 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
