#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sfs_calibration_probe.py — SFS / av_judge 校准探针(R20 W9 Batch-Z·P0)

【缺口·2026-06-21·R20 STRONG fanfic_voice_mimicry id 5】
av_judge / SFS 指标在 shadow→active 升级前缺少 same-author vs cross-author 校准
门。如果 SFS Δ(same vs cross) < 5 或 ROC-AUC < 0.65：指标本身无法分辨作者风格
和非作者风格——这种状态下任何 active 上报都是噪声。

【探针】
  输入：
    --same-dir   同作者文本对池（≥30 对·dir/<pair>/{a.txt,b.txt}）
    --cross-dir  跨作者文本对池（≥30 对·dir/<pair>/{a.txt,b.txt}）
    --scorer     SFS 评分函数路径（占位 = 确定性 surface 相似度·_placeholder=true）
  指标：
    1) Δ_means      = mean(same_scores) - mean(cross_scores)
    2) IQR_overlap  = same Q1-Q3 与 cross Q1-Q3 区间交集长度 / 联合长度
    3) ROC-AUC      = same-vs-cross 二分类·Mann-Whitney U 等价（确定性）
  判定（advisory）：
    - Δ_means < 5 或 AUC < 0.65 → SFS_POORLY_CALIBRATED_FOR_AUTHOR
    - active 模式作为 av_judge shadow→active 升级前置门（AUC≥0.75 才放行）

【北极星⑤】顾问非法官·全 advisory·env SFS_CALIBRATION_PROBE_MODE 默认 shadow·
  SFS_POORLY_CALIBRATED_FOR_AUTHOR 绝不 hard_gate（探针自身噪声 = 检测无能）。

【占位声明 _placeholder=true】
  真 SFS scorer 应调 av_judge / distill_replicate 的同栈打分函数（OpenAI 兼容
  gen-model）。本探针保持确定性 surface 相似度兜底，让 CI 可跑；带 --scorer
  python_module:func 时切换到真实评分。

用法:
  python sfs_calibration_probe.py \\
      --same-dir <path> --cross-dir <path> [--scorer <module:func>] \\
      [--project <root>] [--out <report.json>]
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

ISSUE_CODE = "SFS_POORLY_CALIBRATED_FOR_AUTHOR"

MIN_PAIRS = 10  # ≥10 对才有意义（R20 蓝图要求 ≥30 真用·CI 兜底放宽）
RECOMMENDED_PAIRS = 30
DELTA_MEANS_THRESHOLD = 5.0  # 蓝图阈值
ROC_AUC_THRESHOLD = 0.65      # 蓝图阈值
ROC_AUC_ACTIVE_GATE = 0.75    # av_judge shadow→active 升级门

# 占位 scorer 标记
_DEFAULT_SCORER_PLACEHOLDER = True


def _mode() -> str:
    m = (os.environ.get("SFS_CALIBRATION_PROBE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


# ============ 确定性占位 scorer（_placeholder=true）============
# 真 scorer 应是 av_judge / distill_replicate 的同栈 SFS。占位用 char-3gram Jaccard
# *100，保持单调可比；不替代真 SFS。

def _char_ngrams(text: str, n: int = 3):
    text = text.strip()
    if len(text) < n:
        return set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _placeholder_sfs_score(text_a: str, text_b: str) -> float:
    """确定性 surface 相似度（_placeholder=true）·返回 0-100。"""
    a = _char_ngrams(text_a, 3)
    b = _char_ngrams(text_b, 3)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return 100.0 * inter / union


def _load_scorer(scorer_spec: Optional[str]) -> Tuple[Callable[[str, str], float], bool]:
    """加载 scorer。spec 形如 'module:func'，缺则用占位。返回 (callable, is_placeholder)。"""
    if not scorer_spec:
        return _placeholder_sfs_score, True
    try:
        mod_name, func_name = scorer_spec.split(":", 1)
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, func_name)
        return fn, False
    except Exception as e:
        print(f"[sfs_calibration_probe] scorer 加载失败({e})·回退占位", file=sys.stderr)
        return _placeholder_sfs_score, True


# ============ 统计指标（确定性）============

def _quartiles(xs: List[float]) -> Tuple[float, float, float]:
    """返回 (Q1, Q2, Q3)·线性插值。空列表返回 0,0,0。"""
    if not xs:
        return 0.0, 0.0, 0.0
    s = sorted(xs)
    n = len(s)

    def q(p: float) -> float:
        if n == 1:
            return s[0]
        pos = p * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return s[lo]
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return q(0.25), q(0.5), q(0.75)


def _iqr_overlap(same: List[float], cross: List[float]) -> float:
    """两组 IQR 区间 [Q1,Q3] 交集长度 / 联合长度。0 = 完全分离·1 = 完全重叠。"""
    if not same or not cross:
        return 1.0  # 数据不足保守视为完全重叠（即「无分辨力」）
    sq1, _, sq3 = _quartiles(same)
    cq1, _, cq3 = _quartiles(cross)
    inter_lo = max(sq1, cq1)
    inter_hi = min(sq3, cq3)
    inter_len = max(0.0, inter_hi - inter_lo)
    union_lo = min(sq1, cq1)
    union_hi = max(sq3, cq3)
    union_len = union_hi - union_lo
    if union_len <= 0:
        return 1.0
    return inter_len / union_len


def _roc_auc_mwu(same: List[float], cross: List[float]) -> float:
    """ROC-AUC via Mann-Whitney U 等价公式·same 当正类（score 应更高）。

    AUC = U / (n_same * n_cross)·tied rank 取 0.5。
    """
    n1 = len(same)
    n0 = len(cross)
    if n1 == 0 or n0 == 0:
        return 0.5
    wins = 0.0
    for s in same:
        for c in cross:
            if s > c:
                wins += 1.0
            elif s == c:
                wins += 0.5
    return wins / (n1 * n0)


# ============ 文本对池加载 ============

def _iter_pair_dir(root: Path) -> List[Tuple[Path, Path]]:
    """root/<pair_name>/{a.txt,b.txt} 或 root/*.txt（两两配对）。"""
    if not root.exists() or not root.is_dir():
        return []
    pairs: List[Tuple[Path, Path]] = []
    # 模式 1：子目录每个含 a.txt + b.txt
    for sub in sorted(root.iterdir()):
        if sub.is_dir():
            a = sub / "a.txt"
            b = sub / "b.txt"
            if a.exists() and b.exists():
                pairs.append((a, b))
    if pairs:
        return pairs
    # 模式 2：root/*.txt 两两顺序配对
    flat = sorted(p for p in root.iterdir() if p.is_file() and p.suffix == ".txt")
    for i in range(0, len(flat) - 1, 2):
        pairs.append((flat[i], flat[i + 1]))
    return pairs


def _score_pairs(pairs: List[Tuple[Path, Path]],
                 scorer: Callable[[str, str], float]) -> List[float]:
    scores: List[float] = []
    for a, b in pairs:
        try:
            ta = a.read_text(encoding="utf-8")
            tb = b.read_text(encoding="utf-8")
        except OSError:
            continue
        if _cjk_count(ta) < 50 or _cjk_count(tb) < 50:
            continue
        try:
            sc = float(scorer(ta, tb))
        except Exception as e:
            print(f"[sfs_calibration_probe] scorer 失败 {a.name}: {e}", file=sys.stderr)
            continue
        scores.append(sc)
    return scores


# ============ 主流程 ============

def probe(same_dir: Path, cross_dir: Path,
          scorer_spec: Optional[str] = None,
          project_root: Optional[Path] = None) -> dict:
    mode = _mode()
    out = {
        "scanner": "sfs_calibration_probe",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "violations": [],
        "verdict": "PASS",
        "warning": None,
        "_placeholder_scorer": True,
    }
    if mode == "off":
        return out

    same_pairs = _iter_pair_dir(same_dir) if same_dir else []
    cross_pairs = _iter_pair_dir(cross_dir) if cross_dir else []
    out["same_pair_count"] = len(same_pairs)
    out["cross_pair_count"] = len(cross_pairs)

    if len(same_pairs) < MIN_PAIRS or len(cross_pairs) < MIN_PAIRS:
        out["note"] = (f"数据不足·same={len(same_pairs)} / cross={len(cross_pairs)}"
                       f"·需 ≥{MIN_PAIRS} 对·跳过")
        return out

    scorer, is_placeholder = _load_scorer(scorer_spec)
    out["_placeholder_scorer"] = is_placeholder

    same_scores = _score_pairs(same_pairs, scorer)
    cross_scores = _score_pairs(cross_pairs, scorer)
    out["same_score_count"] = len(same_scores)
    out["cross_score_count"] = len(cross_scores)

    if len(same_scores) < MIN_PAIRS or len(cross_scores) < MIN_PAIRS:
        out["note"] = "评分后样本不足·跳过"
        return out

    mean_same = sum(same_scores) / len(same_scores)
    mean_cross = sum(cross_scores) / len(cross_scores)
    delta_means = mean_same - mean_cross
    iqr_overlap = _iqr_overlap(same_scores, cross_scores)
    auc = _roc_auc_mwu(same_scores, cross_scores)

    out["metrics"] = {
        "mean_same": round(mean_same, 4),
        "mean_cross": round(mean_cross, 4),
        "delta_means": round(delta_means, 4),
        "iqr_overlap": round(iqr_overlap, 4),
        "roc_auc": round(auc, 4),
        "delta_threshold": DELTA_MEANS_THRESHOLD,
        "auc_threshold": ROC_AUC_THRESHOLD,
        "active_gate_auc": ROC_AUC_ACTIVE_GATE,
    }

    poorly_calibrated = (delta_means < DELTA_MEANS_THRESHOLD) or (auc < ROC_AUC_THRESHOLD)
    out["poorly_calibrated"] = poorly_calibrated
    out["can_promote_to_active"] = auc >= ROC_AUC_ACTIVE_GATE
    out["sample_count_recommended_met"] = (
        len(same_scores) >= RECOMMENDED_PAIRS and len(cross_scores) >= RECOMMENDED_PAIRS
    )

    if poorly_calibrated:
        msg = (f"SFS 校准不足·Δ={delta_means:.2f}<{DELTA_MEANS_THRESHOLD}"
               f" 或 AUC={auc:.3f}<{ROC_AUC_THRESHOLD}·shadow→active 升级被阻")
        if mode == "active":
            out["violations"].append({
                "kind": "sfs_poorly_calibrated", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "metrics": out["metrics"],
                "_doc": ("av_judge / SFS 自身校准不足·shadow 才能继续·active 暂停"
                         "·advisory·绝不 hard_gate"),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] sfs_calibration_probe: {msg} — 不上报", file=sys.stderr)

    return out


def main():
    ap = argparse.ArgumentParser(description="SFS / av_judge 校准探针·advisory·shadow")
    ap.add_argument("--same-dir", required=True,
                    help="同作者文本对池目录（≥30 对 a.txt/b.txt）")
    ap.add_argument("--cross-dir", required=True,
                    help="跨作者文本对池目录（≥30 对 a.txt/b.txt）")
    ap.add_argument("--scorer", default=None,
                    help="评分函数 spec 'module:func'·缺则占位 surface 相似度")
    ap.add_argument("--project", default=None)
    ap.add_argument("--out", default=None, help="JSON 报告输出路径·默认 stdout")
    args = ap.parse_args()

    rep = probe(Path(args.same_dir), Path(args.cross_dir), args.scorer,
                Path(args.project) if args.project else None)
    payload = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
    print(payload)
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
