#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zipf_alpha_scanner.py — Zipf α 词频曲线斜率反 AI 检测(R18 W7 Batch-U·P2)

【缺口·2026-06-21·Liang 2024 Science Advances Zipf + Pangram 2025 detector
+ arxiv 2025-2026 Zipf law LLM detection】

Zipf 定律：词频 ~ 1/rank^α·人类作者中文 α≈1.0(分布"重尾")·
LLM 生成中文 α 显著偏高(分布"瘦尾"·过度集中于高频词·避免长尾低频词)。

【算法】
  - 切分 char-gram·或简化 1-2 char token(零依赖)
  - 取 top-K(默认 200)·log-log fit 直线斜率 α (least squares)
  - 与作者档 quantitative.zipf_baseline{alpha_mean/std} z-band
  - 兜底地板：α > 1.4 → ZIPF_ALPHA_DRIFT (AI 偏高怀疑)

【与既有 scanner 显式去重】
  - excess_vocab_corpus_zscan (R18 P0): type 级 vocab z-test(单词命中)
    本 scanner = 分布形状的斜率(全词频曲线·LLM 整体偏瘦尾)·正交
  - syntactic_diversity: POS n-gram 句法模板
    本 scanner = 词频分布形状·正交

【北极星⑤】顾问非法官·全 advisory·env ZIPF_ALPHA_SCANNER_MODE
  ZIPF_ALPHA_DRIFT 绝不 hard_gate。

用法: python zipf_alpha_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

ISSUE_CODE = "ZIPF_ALPHA_DRIFT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 800
TOP_K = 200
FLOOR_ALPHA_HIGH = 1.4   # 兜底地板·>1.4 视作 AI 偏瘦尾
FLOOR_ALPHA_LOW = 0.6    # <0.6 视作长尾过厚(噪声)


def _mode() -> str:
    m = (os.environ.get("ZIPF_ALPHA_SCANNER_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_baseline(project_root, style_path):
    out = {"alpha_mean": None, "alpha_std": None, "from_author_profile": False}
    data = None
    if style_path and Path(style_path).exists():
        try:
            data = json.loads(Path(style_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        for fname in ("作者风格_FINAL.json", "作者风格.json"):
            p = Path(project_root) / "_数据库" / fname
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    break
                except (OSError, json.JSONDecodeError):
                    continue
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        zb = q.get("zipf_baseline") or {}
        m = zb.get("alpha_mean")
        s = zb.get("alpha_std")
        if isinstance(m, (int, float)):
            out["alpha_mean"] = float(m)
            out["alpha_std"] = float(s) if isinstance(s, (int, float)) else None
            out["from_author_profile"] = True
    return out


def _tokenize_bichar(text: str):
    """零依赖二字 char-gram·过滤非 CJK。"""
    chars = [c for c in text if "一" <= c <= "鿿"]
    return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]


def _fit_zipf_alpha(freqs, top_k=TOP_K):
    """log-log least squares fit·返回 alpha。
    log(f) = -alpha * log(rank) + C → 拟合 -slope=alpha。"""
    sorted_freqs = sorted(freqs.values(), reverse=True)[:top_k]
    if len(sorted_freqs) < 20:
        return None
    xs = [math.log(r + 1) for r in range(len(sorted_freqs))]
    ys = [math.log(f) for f in sorted_freqs]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den
    return -slope


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "zipf_alpha", "schema_version": "1.0",
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
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    tokens = _tokenize_bichar(text)
    freqs = Counter(tokens)
    alpha = _fit_zipf_alpha(freqs)
    if alpha is None:
        out["note"] = "样本不足以拟合"
        return out

    baseline = _load_baseline(project_root, style_path)
    out["author_baseline"] = {
        "from_author_profile": baseline["from_author_profile"],
        "alpha_mean": baseline["alpha_mean"],
    }
    out["metrics"] = {"alpha": round(alpha, 3),
                      "vocab_unique": len(freqs),
                      "tokens": len(tokens)}

    messages = []
    m, s = baseline["alpha_mean"], baseline["alpha_std"]
    if m is not None and s and s > 1e-6:
        z = (alpha - m) / s
        out["metrics"]["alpha_z"] = round(z, 2)
        if abs(z) >= 2.0:
            messages.append(
                f"Zipf α {round(alpha,3)} 偏离作者基线 {round(m,3)}±{round(s,3)} "
                f"{round(z,1)}σ ({'瘦尾(AI 偏高)' if z>0 else '过厚长尾(噪声)'})")
    else:
        if alpha > FLOOR_ALPHA_HIGH:
            messages.append(
                f"Zipf α {round(alpha,3)} > {FLOOR_ALPHA_HIGH}·分布瘦尾·"
                f"可能 AI 训练偏置(高频词过度集中)")
        elif alpha < FLOOR_ALPHA_LOW:
            messages.append(
                f"Zipf α {round(alpha,3)} < {FLOOR_ALPHA_LOW}·分布过厚长尾·"
                f"可能噪声/混杂多源文本")

    if messages:
        msg = " · ".join(messages)
        if mode == "active":
            out["violations"].append({
                "kind": "zipf_alpha_drift", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "metrics": out["metrics"],
                "_doc": "Zipf α 词频斜率·Liang 2024 Sci Adv·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] zipf_alpha: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Zipf α 词频斜率·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.style)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
