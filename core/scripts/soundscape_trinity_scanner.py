#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soundscape_trinity_scanner.py — Schafer 声景三分类 advisory · cluster · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 asmr_slow id 11】R. Murray Schafer《The Soundscape》声景三分类：
keynote（基调）+ signal（信号）+ soundmark（地标）。LLM 草稿默认声学维度
塌成「单声道」（要么全静，要么只描视觉），缺少声景层次。

【做法 · 确定性占位 · 真句级 gen-model 三分类 defer】
  · 词典 core/data/soundscape_lexicon_zh.json 三桶（_placeholder=true）
  · 句级扫描：每句若含某桶词项 → 计入该桶
  · 桶密度 per kCJK + 信息熵（Shannon over 3 buckets）

【三 advisory】（CLUSTER_MODE shadow）
  · SOUNDSCAPE_THIN — 总 sound_per_kcjk < 1.5（声学维度极弱）
  · SOUNDSCAPE_MONOTONE — entropy < 0.8（单声道·三类不均衡）
  · SOUNDMARK_ABSENT — soundmark 桶 hits == 0 且 cluster CJK > 8000（地标声完全缺位）

【consolidate 接口】
  · author_soundscape_baseline = { keynote_per_kcjk, signal_per_kcjk,
                                    soundmark_per_kcjk, entropy_band: [low, high] }
  · 真聚合 defer · 占位接口已 reserved

【北极星】②④⑤ advisory shadow · 绝不 hard_gate
  SOUNDSCAPE_THIN / SOUNDSCAPE_MONOTONE / SOUNDMARK_ABSENT 永不进 audit_hub.HARD_GATE_CODES。

env SOUNDSCAPE_TRINITY_MODE: off / shadow(默认) / active
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_THIN = "SOUNDSCAPE_THIN"
ISSUE_CODE_MONOTONE = "SOUNDSCAPE_MONOTONE"
ISSUE_CODE_NO_MARK = "SOUNDMARK_ABSENT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "soundscape_lexicon_zh.json"

DEFAULT_SOUND_PER_KCJK_LOW = 1.5
DEFAULT_ENTROPY_LOW = 0.8
DEFAULT_LARGE_CLUSTER_CJK = 8000


def _mode() -> str:
    m = (os.environ.get("SOUNDSCAPE_TRINITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_lexicon() -> dict:
    try:
        return json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "_placeholder": True,
            "buckets": {
                "keynote": ["风声", "雨声"],
                "signal": ["铃声", "枪声"],
                "soundmark": ["钟楼", "梆子"],
            }
        }


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = Path(project_root) / "_数据库" / fname
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("author_soundscape_baseline"), dict):
                return obj["author_soundscape_baseline"]
    return None


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？…])", text) if s.strip()]


def _count_hits(text: str, terms: list[str]) -> int:
    return sum(text.count(t) for t in terms if t)


def _entropy(values: list[float]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    h = 0.0
    for v in values:
        if v <= 0:
            continue
        p = v / total
        h -= p * math.log(p, 2)
    return h


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "soundscape_trinity", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "_classifier_placeholder": True}
    if mode == "off":
        return out
    # CLUSTER_MODE 守门 · 与既有 cluster scanner 一致
    if os.environ.get("CLUSTER_MODE") != "1":
        # cluster-only · 非 cluster 阶段不报
        out["note"] = "非 CLUSTER_MODE·跳过"
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    lex = _load_lexicon()
    buckets = lex.get("buckets") or {}
    hits = {bn: _count_hits(text, terms) for bn, terms in buckets.items()}
    total_hits = sum(hits.values())
    per_kcjk = round(total_hits / (cjk / 1000.0), 3) if cjk else 0.0
    bucket_per_kcjk = {bn: round(h / (cjk / 1000.0), 3) for bn, h in hits.items()}
    entropy = round(_entropy([float(h) for h in hits.values()]), 4)

    sents = _split_sentences(text)
    # 句级三分类占位：每句归一桶（命中最多的桶 / 无命中=none）
    per_sentence = {bn: 0 for bn in hits}
    none_sentence = 0
    for s in sents:
        sc = {bn: _count_hits(s, terms) for bn, terms in buckets.items()}
        top = max(sc.items(), key=lambda kv: kv[1]) if sc else None
        if top and top[1] > 0:
            per_sentence[top[0]] += 1
        else:
            none_sentence += 1
    sent_total = len(sents)
    sentence_share = {bn: round(v / sent_total, 3) if sent_total else 0.0
                      for bn, v in per_sentence.items()}

    baseline = _read_author_baseline(project_root)
    thin_low = DEFAULT_SOUND_PER_KCJK_LOW
    entropy_low = DEFAULT_ENTROPY_LOW
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("sound_per_kcjk_low"), (int, float)):
            thin_low = float(baseline["sound_per_kcjk_low"])
        eb = baseline.get("entropy_band")
        if isinstance(eb, list) and len(eb) == 2:
            entropy_low = float(eb[0])

    out.update({
        "cjk": cjk,
        "sentences_total": sent_total,
        "bucket_hits": hits,
        "bucket_per_kcjk": bucket_per_kcjk,
        "sound_per_kcjk": per_kcjk,
        "entropy": entropy,
        "sentence_share": sentence_share,
        "sentence_none_count": none_sentence,
        "baseline_source": baseline_source,
        "baseline": {
            "sound_per_kcjk_low": thin_low,
            "entropy_low": entropy_low,
        }
    })

    flags = []
    if per_kcjk < thin_low:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"sound_per_kcjk={per_kcjk} < {thin_low}·声学维度稀薄·建议补声景"})
    if total_hits >= 4 and entropy < entropy_low:
        flags.append({"code": ISSUE_CODE_MONOTONE,
                      "msg": f"entropy={entropy} < {entropy_low}·三类声景不均衡·"
                             f"shares={sentence_share}"})
    if cjk >= DEFAULT_LARGE_CLUSTER_CJK and hits.get("soundmark", 0) == 0:
        flags.append({"code": ISSUE_CODE_NO_MARK,
                      "msg": f"cluster CJK={cjk}·soundmark 桶 hits=0·地标声完全缺位"})

    out["flags"] = flags
    if flags and mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "soundscape_trinity", "severity": "minor",
                "code": f["code"], "message": f["msg"],
                "_doc": "Schafer Soundscape trinity · advisory · 绝不 hard_gate"})
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = "·".join(f["msg"] for f in flags)
    elif flags:
        print(f"[SHADOW] soundscape_trinity: {'·'.join(f['msg'] for f in flags)} — 不上报",
              file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Schafer soundscape trinity scanner (CLUSTER_MODE shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
