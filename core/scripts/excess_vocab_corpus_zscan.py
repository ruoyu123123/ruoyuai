#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""excess_vocab_corpus_zscan.py — LLM 训练偏置词动态语料 z-test（type 级 · 分布对比）

【缺口 · R18 W7 Batch-S·P0 · 2026-06-21】Liang 2024 Science Advances PubMed 5.2B
token + arxiv 2412.11400 ChineseLLM excess vocab + 番茄AI识别公开规范：
  LLM 训练分布与人类网文分布存在系统偏置——某些 token 在 LLM 输出里频次远高于真
  人网文。比单纯黑名单更稳的是【两个语料的 z-test】：
    z (LLM_freq vs Human_freq) → token 的「LLM 偏向度」
    z' (this_text_freq vs Human_freq) → 当前草稿在该 token 上的「LLM 化」程度
  z>+2 且 z'>+1 且 author 该 token 频次 < μ+1σ → EXCESS_VOCAB_SIGNATURE_HIT

【与 R12 metaphor anti-AI 机制不同】
  - R12 metaphor_anti_ai: 检测【AI 调性比喻 anti-pattern】（"如同/犹如" 二元喻）
    本 scanner = type 级 vocab 分布偏置（不依赖句法结构·是分布对比）·正交

【做法 · 确定性占位（零 LLM·零联网）】
  1. 占位词典 core/scripts/lexicons/llm_chinese_corpus_freq.json
                  + human_webnovel_corpus_freq.json（_placeholder=true）
  2. 算每 token 的 z = (llm_freq - mean_human) / std_human
     算 z' = (this_text_freq - mean_human) / std_human
  3. z>+2 且 z'>+1 且 author 该 token <μ+1σ → hit
  4. consolidate_author_profile 增 author_vocab_freq_ecdf SLOW_UPDATE 字段（占位）
     —— 蒸馏 phase-3 写入·SkillOpt 锁死不许动（北极星⑤）

【北极星】②④⑤ cluster 视野·全 advisory·env EXCESS_VOCAB_CORPUS_ZSCAN_MODE
  EXCESS_VOCAB_SIGNATURE_HIT 绝不进 audit_hub.HARD_GATE_CODES。

用法: python excess_vocab_corpus_zscan.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ISSUE_CODE = "EXCESS_VOCAB_SIGNATURE_HIT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

_LEXICON_DIR = Path(__file__).resolve().parent / "lexicons"
_LLM_FREQ_PATH = _LEXICON_DIR / "llm_chinese_corpus_freq.json"
_HUMAN_FREQ_PATH = _LEXICON_DIR / "human_webnovel_corpus_freq.json"

# 阈值（占位保守·待金标准校准）
Z_LLM_BIAS_THRESHOLD = 2.0   # z (LLM vs Human) > 2
Z_TEXT_THRESHOLD = 1.0       # z' (this vs Human) > 1
AUTHOR_MAX_SIGMA = 1.0        # author 该 token <μ+1σ


def _mode() -> str:
    m = (os.environ.get("EXCESS_VOCAB_CORPUS_ZSCAN_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_freq(path: Path):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("freq"), dict):
            return obj["freq"], bool(obj.get("_placeholder", False))
    except (OSError, json.JSONDecodeError):
        pass
    return {}, True


def _mean_std(values):
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mu = sum(values) / n
    var = sum((v - mu) ** 2 for v in values) / n
    return mu, math.sqrt(var)


def _load_author_freq(project_root):
    """读作者档 author_vocab_freq_ecdf SLOW_UPDATE 段·缺则空 dict"""
    if not project_root:
        return {}
    for fname in ("作者风格_FINAL.json", "作者风格.json"):
        p = Path(project_root) / "_数据库" / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        slow = obj.get("slow_update") or obj.get("SLOW_UPDATE") or {}
        afreq = slow.get("author_vocab_freq_ecdf")
        if isinstance(afreq, dict):
            return afreq
    return {}


def _per_million(count, total_cjk):
    if total_cjk <= 0:
        return 0.0
    return count / total_cjk * 1_000_000


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "excess_vocab_corpus_zscan", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
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

    llm_freq, llm_ph = _load_freq(_LLM_FREQ_PATH)
    human_freq, hum_ph = _load_freq(_HUMAN_FREQ_PATH)
    if not llm_freq or not human_freq:
        out["note"] = "占位词典缺·跳过"
        return out
    out["llm_freq_placeholder"] = llm_ph
    out["human_freq_placeholder"] = hum_ph

    # 全 token 集合（两表交集）
    tokens = sorted(set(llm_freq.keys()) & set(human_freq.keys()))
    if not tokens:
        out["note"] = "词典 token 集空·跳过"
        return out
    human_values = [human_freq[t] for t in tokens]
    h_mu, h_sigma = _mean_std(human_values)
    if h_sigma < 1e-6:
        out["note"] = "human σ≈0·跳过"
        return out

    author_freq = _load_author_freq(project_root)
    text_freq_pm = {t: _per_million(text.count(t), cjk) for t in tokens}

    hits = []
    for t in tokens:
        z_llm = (llm_freq[t] - h_mu) / h_sigma
        z_text = (text_freq_pm[t] - h_mu) / h_sigma
        a_freq = author_freq.get(t, 0.0)
        z_author = (a_freq - h_mu) / h_sigma if a_freq else -99.0
        if (z_llm > Z_LLM_BIAS_THRESHOLD
                and z_text > Z_TEXT_THRESHOLD
                and z_author < AUTHOR_MAX_SIGMA):
            hits.append({
                "token": t,
                "z_llm": round(z_llm, 2),
                "z_text": round(z_text, 2),
                "z_author": round(z_author, 2) if a_freq else None,
                "text_count": text.count(t),
            })
    out["hit_count"] = len(hits)
    out["hit_samples"] = hits[:8]

    if hits:
        msg = (f"excess_vocab 命中 {len(hits)} token: "
               + "/".join(h["token"] for h in hits[:6]))
        if mode == "active":
            out["violations"].append({
                "kind": "excess_vocab_signature", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "hit_count": len(hits),
                "_doc": "Liang 2024 + ChineseLLM excess vocab·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] excess_vocab_corpus_zscan: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="LLM 偏置词 z-test·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
