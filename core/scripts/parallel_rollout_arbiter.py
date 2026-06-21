#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parallel_rollout_arbiter.py — K=2 并行 rollout listwise rank 仲裁器(R19 W8 Batch-Y·P2)

【缺口·2026-06-21·高歧义 cluster 需多版本 rollout】
gen_writer 单次 rollout 对高歧义 cluster(opening / volume_finale / 大转折)易陷局部最优.
本仲裁器接 K=2 并行 rollout 草稿 + 各自 JudgeReport, listwise rank 选最优 → 落地为
"K=2 候选 → arbiter 排序 → winner 入主轨". 默认 off, gen_writer 加 --parallel-rollout-mode=off.

【输入】
  - candidates: List[dict]  每 dict 含 {draft_path, judge_report_path, meta}
  - cluster_brief: cluster 元信息(可选, 用于歧义系数计算)

【scoring 公式(确定性·零 LLM)】
  rank_score = w1 * scanner_pass_rate
             + w2 * length_band_fit   (cluster brief 目标字数 band 距离)
             + w3 * lexical_diversity (1-gram 唯一比)
             + w4 * verdict_severity_penalty (FAIL_HARD>FAIL_MINOR>PASS 反向)
  默认 w = (0.4, 0.2, 0.2, 0.2). listwise rank 输出 winner_index + 全 candidate score.

【北极星⑤】顾问非法官·占位 scaffolding·env PARALLEL_ROLLOUT_ARBITER_MODE 默认 off·全 advisory·
  PARALLEL_ROLLOUT_ARBITER_DEGRADED 绝不 hard_gate. 高歧义 cluster 才触发(scene_ambiguity 系数 ≥ 0.6).

【与既有 scanner 严格正交】
  - cluster_evaluator        : 单稿评估·正交(本=K 稿排序)
  - adversarial_judge_pair   : attacker-defender(本=被动 rank·不出 attack)
  - meta_critic_audit        : 元批评·正交
  - dialogue_orchestrator    : 对话编排·正交

用法: python parallel_rollout_arbiter.py --candidates a.json,b.json [--project root]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "PARALLEL_ROLLOUT_ARBITER_DEGRADED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 800
SCORE_WEIGHTS = (0.4, 0.2, 0.2, 0.2)


def _mode() -> str:
    m = (os.environ.get("PARALLEL_ROLLOUT_ARBITER_MODE") or "off").strip().lower()
    return m if m in ("off", "shadow", "active") else "off"


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _read_draft(path) -> str:
    try:
        return _strip_changes(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return ""


def _read_judge(path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _scanner_pass_rate(judge: dict) -> float:
    """JudgeReport.scanners[*].verdict==PASS 比例."""
    scanners = judge.get("scanners") or []
    if not isinstance(scanners, list) or not scanners:
        # 兼容 dict-of-scanners
        sd = judge.get("scanner_results") if isinstance(judge.get("scanner_results"), dict) else None
        if not sd:
            return 0.5  # 无信号→中性
        verdicts = [(v or {}).get("verdict") for v in sd.values() if isinstance(v, dict)]
    else:
        verdicts = [(s or {}).get("verdict") for s in scanners if isinstance(s, dict)]
    if not verdicts:
        return 0.5
    passed = sum(1 for v in verdicts if v == "PASS")
    return passed / len(verdicts)


def _length_band_fit(cjk: int, target_band) -> float:
    """字数在 target_band [lo, hi] 内 → 1.0; 外侧线性衰减到 0."""
    if not target_band or len(target_band) != 2:
        return 0.5
    lo, hi = target_band
    if lo <= cjk <= hi:
        return 1.0
    if cjk < lo:
        gap = lo - cjk
        return max(0.0, 1.0 - gap / max(lo, 1))
    gap = cjk - hi
    return max(0.0, 1.0 - gap / max(hi, 1))


def _lexical_diversity(text: str) -> float:
    """1-gram CJK 唯一比. 长稿天然偏低·按 sqrt(len) 矫正."""
    cjks = [c for c in text if "一" <= c <= "鿿"]
    if not cjks:
        return 0.0
    uniq = len(set(cjks))
    total = len(cjks)
    # 用 type-token-ratio sqrt 矫正 (Guiraud index normalized).
    import math
    return min(1.0, uniq / max(1.0, math.sqrt(total)))


def _verdict_penalty(judge: dict) -> float:
    """FAIL_HARD=0.0 / FAIL_MINOR=0.5 / PASS=1.0."""
    v = (judge.get("verdict") or judge.get("overall_verdict") or "PASS").upper()
    if "FAIL_HARD" in v:
        return 0.0
    if "FAIL" in v:
        return 0.5
    return 1.0


def score_candidate(candidate: dict, target_band=None) -> dict:
    """单 candidate 评分. candidate = {draft_path, judge_report_path, meta?}."""
    text = _read_draft(candidate.get("draft_path") or "")
    judge = _read_judge(candidate.get("judge_report_path") or "")
    cjk = _cjk_count(text)
    pass_rate = _scanner_pass_rate(judge)
    band_fit = _length_band_fit(cjk, target_band)
    lex_div = _lexical_diversity(text)
    penalty = _verdict_penalty(judge)
    w1, w2, w3, w4 = SCORE_WEIGHTS
    rank_score = round(
        w1 * pass_rate + w2 * band_fit + w3 * lex_div + w4 * penalty, 4)
    return {
        "rank_score": rank_score,
        "scanner_pass_rate": round(pass_rate, 4),
        "length_band_fit": round(band_fit, 4),
        "lexical_diversity": round(lex_div, 4),
        "verdict_penalty": round(penalty, 4),
        "cjk": cjk,
    }


def arbitrate(candidates, target_band=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "parallel_rollout_arbiter", "schema_version": "1.0",
        "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
        "candidates": [], "winner_index": None, "violations": [],
        "verdict": "PASS", "warning": None,
    }
    if mode == "off":
        out["note"] = "off·skip"
        return out
    if not isinstance(candidates, list) or len(candidates) < 2:
        out["note"] = "candidates<2·skip"
        return out
    scored = []
    for i, c in enumerate(candidates):
        if not isinstance(c, dict):
            continue
        sc = score_candidate(c, target_band=target_band)
        sc["index"] = i
        scored.append(sc)
    if not scored:
        out["note"] = "no valid candidates"
        return out
    # listwise rank: 降序按 rank_score, tie-break 按 index 保稳定
    scored_sorted = sorted(scored, key=lambda x: (-x["rank_score"], x["index"]))
    out["candidates"] = scored_sorted
    out["winner_index"] = scored_sorted[0]["index"]

    # Degraded 判定: top-1 vs top-2 差距 < 0.02 → 无法仲裁 advisory
    if len(scored_sorted) >= 2:
        gap = scored_sorted[0]["rank_score"] - scored_sorted[1]["rank_score"]
        if gap < 0.02:
            msg = f"top-1 与 top-2 rank_score 差 {round(gap,4)} < 0.02·无法可靠仲裁"
            if mode == "active":
                out["violations"].append({
                    "kind": "parallel_rollout_arbiter", "severity": "minor",
                    "code": ISSUE_CODE, "message": msg,
                    "_doc": "R19 W8 Batch-Y·P2·listwise rank degraded·advisory",
                })
                out["verdict"] = "FAIL_MINOR"
                out["warning"] = msg
            else:
                print(f"[SHADOW] parallel_rollout_arbiter[{ISSUE_CODE}]: {msg} — 不上报",
                      file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="R19 W8 Batch-Y·P2·K=2 并行 rollout listwise rank·advisory·off")
    ap.add_argument("--candidates", required=True,
                    help="comma-sep candidate spec json paths (each: {draft_path,judge_report_path})")
    ap.add_argument("--target-band", default=None,
                    help="comma-sep lo,hi(CJK)·缺省走中性")
    ap.add_argument("--project", default=None)
    args, _ = ap.parse_known_args()
    cands = []
    for p in args.candidates.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            cands.append(json.loads(Path(p).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    band = None
    if args.target_band:
        try:
            lo, hi = (int(x) for x in args.target_band.split(",", 1))
            band = (lo, hi)
        except (ValueError, TypeError):
            band = None
    rep = arbitrate(cands, target_band=band)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
