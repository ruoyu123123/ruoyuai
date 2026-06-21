#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""forecasting_tension_scanner.py — 句级张力梯度预测 advisory · cluster · 2026-06-21 R20 W9 Batch-CC · P2

【缺口 · R20 Q3-Q4 id 16】既有 narrative_rhythm_scanner 抓「张力轨迹后段保持度」
和「节拍单调」(序列级 macro-rhythm)、premature_resolution_scanner 抓「冲突
过快消解」(冲突→消解距离)，但缺『句级张力梯度 micro-pattern』检测——
即句到句的张力涨落是否有 forecasting (前句铺信号→后句放张力) 的因果链。

【做法 · 占位实现 · 真版 SBERT 自相关 defer】
  · 真版：句级 sentence-bert-zh 嵌入 → 句间余弦相似度 → 张力梯度自相关
    (lag-1/2/3) → 张力 forecast horizon 估计。需 sentence-transformers 模型
    授权 + GPU·defer 真依赖入栈。
  · 占位：char Shannon entropy 相邻 200 CJK 块差分 (proxy of textual surprise)。
    高 entropy 段 = 信息密集/紧张, 低 entropy 段 = 信息稀薄/缓和。相邻块
    entropy 差 = 张力梯度代理。
  · 全 cluster 草稿按 200 CJK 切块·算 per-block entropy → diff_series
  · 关键指标:
    - blocks_count
    - entropy_mean / entropy_std (per-block)
    - gradient_pstdev (相邻块 entropy 差的 pstdev)
    - flatline_ratio (绝对 diff < FLAT_THRESHOLD 的相邻对占比)
    - low_gradient_variance = gradient_pstdev < GRAD_VAR_FLOOR (匀速张力·无 forecasting)

【作者档第一权威】
  · 作者风格.json.forecasting_tension_baseline = {gradient_pstdev_min, flatline_ratio_max}
  · 缺 baseline → 兜底 {gradient_pstdev_min: 0.3, flatline_ratio_max: 0.55}

【与既有 scanner 严格正交】
  · narrative_rhythm_scanner 看序列级张力轨迹后段保持度 + 节拍单调(macro)
  · premature_resolution_scanner 看冲突 vs 消解的标志词间距(macro)
  · 本 scanner = 句级张力梯度 micro-pattern 唯一覆盖(entropy proxy)

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  FORECASTING_TENSION_FLAT 绝不进 audit_hub.HARD_GATE_CODES。

env FORECASTING_TENSION_MODE: off / shadow(默认) / active
用法: python forecasting_tension_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

ISSUE_CODE = "FORECASTING_TENSION_FLAT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

BLOCK_SIZE = 200                  # CJK 块大小
FLAT_THRESHOLD = 0.05             # |entropy 差| < 阈值 = 平
DEFAULT_GRADIENT_PSTDEV_MIN = 0.30   # 梯度 pstdev < 阈值 = 匀速
DEFAULT_FLATLINE_RATIO_MAX = 0.55     # 平稳相邻对 > 55% = 张力扁平
MIN_BLOCKS = 5


def _mode() -> str:
    m = (os.environ.get("FORECASTING_TENSION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_only(text: str) -> str:
    return "".join(ch for ch in text if "一" <= ch <= "鿿")


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


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
            mb = obj.get("forecasting_tension_baseline")
            if isinstance(mb, dict):
                return mb
    return None


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    counts = Counter(s)
    ent = 0.0
    for c in counts.values():
        p = c / n
        ent -= p * math.log2(p)
    return round(ent, 4)


def _split_blocks(cjk_text: str, size: int) -> list[str]:
    if not cjk_text:
        return []
    return [cjk_text[i:i + size] for i in range(0, len(cjk_text), size)
            if len(cjk_text[i:i + size]) >= size // 2]


def _gradient_series(entropies: list[float]) -> list[float]:
    return [entropies[i + 1] - entropies[i] for i in range(len(entropies) - 1)]


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "forecasting_tension", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE,
           "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk_text = _cjk_only(text)
    if len(cjk_text) < BLOCK_SIZE * MIN_BLOCKS:
        out["note"] = "草稿太短·跳过"
        return out

    blocks = _split_blocks(cjk_text, BLOCK_SIZE)
    if len(blocks) < MIN_BLOCKS:
        out["note"] = "blocks 不足·跳过"
        return out

    entropies = [_shannon_entropy(b) for b in blocks]
    gradients = _gradient_series(entropies)
    if not gradients:
        out["note"] = "gradient 系列空"
        return out

    entropy_mean = round(statistics.mean(entropies), 4)
    entropy_std = round(statistics.pstdev(entropies), 4) if len(entropies) >= 2 else 0.0
    gradient_pstdev = round(statistics.pstdev(gradients), 4) if len(gradients) >= 2 else 0.0
    flat_count = sum(1 for g in gradients if abs(g) < FLAT_THRESHOLD)
    flatline_ratio = round(flat_count / len(gradients), 4)

    baseline = _read_author_baseline(project_root)
    grad_min = DEFAULT_GRADIENT_PSTDEV_MIN
    flat_max = DEFAULT_FLATLINE_RATIO_MAX
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        gm = baseline.get("gradient_pstdev_min")
        fm = baseline.get("flatline_ratio_max")
        if isinstance(gm, (int, float)):
            grad_min = float(gm)
        if isinstance(fm, (int, float)):
            flat_max = float(fm)

    out.update({
        "cjk_total": len(cjk_text),
        "blocks_count": len(blocks),
        "entropy_mean": entropy_mean,
        "entropy_std": entropy_std,
        "gradient_pstdev": gradient_pstdev,
        "flatline_ratio": flatline_ratio,
        "baseline_source": baseline_source,
        "baseline": {"gradient_pstdev_min": grad_min, "flatline_ratio_max": flat_max},
        "_placeholder": True,
        "_doc_placeholder": "char Shannon entropy proxy·真版 SBERT 自相关 defer",
    })

    flags = []
    if gradient_pstdev < grad_min and flatline_ratio > flat_max:
        flags.append({
            "code": ISSUE_CODE,
            "msg": (f"张力梯度 pstdev={gradient_pstdev} < {grad_min}·"
                    f"flatline_ratio={round(flatline_ratio*100,1)}% > {round(flat_max*100,1)}%·"
                    f"句级张力匀速无 forecasting (entropy proxy)")
        })

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "forecasting_tension_flat", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "句级张力梯度 forecasting (entropy proxy) · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] forecasting_tension: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="句级张力梯度 forecasting advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
