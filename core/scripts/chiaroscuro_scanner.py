#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chiaroscuro_scanner.py — 光暗意象比 cluster 视野 advisory
(advisory · cluster · 2026-06-20 R12 W6 Batch-Q · P2 · shadow)

【缺口】jonreeve.com Reeve WordNet 跨小说 chiaroscuro ratio 基线 + mwany.org
MWA chiaroscuro + Caravaggio 跨流派谱系：光暗意象比是文学风格签名维度，
LLM 默认偏亮（叙事偏阳光）或偏暗（恐怖滥用），缺与作者档对齐。

【做法 · 确定性词频统计】
  1. 加载 core/data/luminance_lexicon_cn.json（光/暗各 40+ 词占位）
  2. 全文滑动计数·输出 light_density / dark_density / chiaroscuro_ratio
       chiaroscuro_ratio = light / (light + dark)   ∈ [0, 1]
       0.5 = 平衡 · >0.7 偏亮 · <0.3 偏暗
  3. 作者档 luminance_signature.{light_p50, dark_p50, ratio_p50} 第一权威；
     无作者档 → 兜底 ratio band [0.30, 0.70]，密度无 floor。
  4. ratio > band[1] → OVER_BRIGHT advisory
     ratio < band[0] → OVER_DARK advisory
     |ratio - author_p50| > 0.25 → DRIFT_FROM_AUTHOR advisory（仅作者档存在时）

【北极星】②④⑤ cluster 视野·作者档第一权威·advisory shadow·绝不 hard_gate
OVER_BRIGHT/OVER_DARK/DRIFT_FROM_AUTHOR 绝不进 audit_hub.HARD_GATE_CODES。

env CHIAROSCURO_MODE: off / shadow(默认) / active
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODES = ("OVER_BRIGHT", "OVER_DARK", "DRIFT_FROM_AUTHOR")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "luminance_lexicon_cn.json"


def _mode() -> str:
    m = (os.environ.get("CHIAROSCURO_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_lexicon():
    try:
        obj = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    return list(obj.get("light", [])), list(obj.get("dark", []))


def _read_author_signature(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        sig = obj.get("luminance_signature")
        if isinstance(sig, dict):
            return sig
    return None


def _count_terms(text: str, terms) -> int:
    n = 0
    for t in terms:
        if not t:
            continue
        # 简易计数：固定子串
        idx = 0
        while True:
            i = text.find(t, idx)
            if i < 0:
                break
            n += 1
            idx = i + len(t)
    return n


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "chiaroscuro_scanner", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "violations": [],
           "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    light_terms, dark_terms = _load_lexicon()
    if not light_terms or not dark_terms:
        out["note"] = "luminance_lexicon_cn.json 缺失或损坏·跳过"
        return out

    light_n = _count_terms(text, light_terms)
    dark_n = _count_terms(text, dark_terms)
    light_density = round(light_n / (cjk / 1000.0), 4)
    dark_density = round(dark_n / (cjk / 1000.0), 4)
    total = light_n + dark_n
    if total == 0:
        out["note"] = "全文未命中光暗词·跳过"
        out["light_density"] = 0.0
        out["dark_density"] = 0.0
        return out
    ratio = round(light_n / total, 4)
    out["light_density"] = light_density
    out["dark_density"] = dark_density
    out["chiaroscuro_ratio"] = ratio
    out["light_count"] = light_n
    out["dark_count"] = dark_n

    sig = _read_author_signature(project_root)
    band = {"low": 0.30, "high": 0.70, "_source": "fallback"}
    author_p50 = None
    if sig and "ratio_p50" in sig:
        author_p50 = float(sig["ratio_p50"])
        # 作者档 ±0.20 当 band
        band = {"low": max(0.0, author_p50 - 0.20),
                "high": min(1.0, author_p50 + 0.20),
                "_source": "author_profile"}
    out["baseline"] = band
    if author_p50 is not None:
        out["author_p50"] = author_p50

    flags = []
    if ratio > band["high"]:
        flags.append({"code": "OVER_BRIGHT",
                      "msg": f"chiaroscuro_ratio={ratio} > {band['high']:.2f}·光暗失衡偏亮"})
    elif ratio < band["low"]:
        flags.append({"code": "OVER_DARK",
                      "msg": f"chiaroscuro_ratio={ratio} < {band['low']:.2f}·光暗失衡偏暗"})
    if author_p50 is not None and abs(ratio - author_p50) > 0.25:
        flags.append({"code": "DRIFT_FROM_AUTHOR",
                      "msg": f"与作者基线 {author_p50:.2f} 偏离 {abs(ratio - author_p50):.2f}"})
    out["flags"] = flags

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "chiaroscuro", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "chiaroscuro_ratio": ratio, "baseline": band,
                    "_doc": "Reeve WordNet/MWA·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] chiaroscuro: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="光暗意象比 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
