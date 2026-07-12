#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sfs_calibration_probe.py — SFS / av_judge 校准探针

【缺口】av_judge / SFS 指标在 shadow→active 升级前缺少 same-author vs cross-author 校准
门。如果 SFS Δ(same vs cross) < 5 或 ROC-AUC < 0.65：指标本身无法分辨作者风格
和非作者风格——这种状态下任何 active 上报都是噪声。

【探针】
  输入：
    --same-dir   同作者文本对池（≥30 对·dir/<pair>/{a.txt,b.txt}）
    --cross-dir  跨作者文本对池（≥30 对·dir/<pair>/{a.txt,b.txt}）
    --scorer     SFS 评分函数路径（缺省时：真后端→embedding 余弦 / 无真后端→占位
                 char-3gram Jaccard·_placeholder=true）
  指标：
    1) Δ_means      = mean(same_scores) - mean(cross_scores)
    2) IQR_overlap  = same Q1-Q3 与 cross Q1-Q3 区间交集长度 / 联合长度
    3) ROC-AUC      = same-vs-cross 二分类·Mann-Whitney U 等价（确定性）
  判定（advisory）：
    - Δ_means < 5 或 AUC < 0.65 → SFS_POORLY_CALIBRATED_FOR_AUTHOR
    - active 模式作为 av_judge shadow→active 升级前置门（AUC≥0.75 才放行）

【北极星⑤】顾问非法官·全 advisory·env SFS_CALIBRATION_PROBE_MODE 默认 shadow·
  SFS_POORLY_CALIBRATED_FOR_AUTHOR 绝不 hard_gate（探针自身噪声 = 检测无能）。

【默认 scorer】
  未传 --scorer 时的默认 scorer：真后端（EMBED_BACKEND≠hash 或配了 GEN_EMBED__*）→
  embedding_store 余弦（source 非 placeholder）；无真后端 → 现有 char-3gram Jaccard
  （_placeholder=true·让 CI 可跑）。--scorer python_module:func 覆盖通道不受影响，
  优先级最高（显式指定 > 真后端自动升级 > 占位兜底）。

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

MIN_PAIRS = 10  # ≥10 对才有意义（真实使用建议 ≥30·CI 兜底放宽到此下限）
RECOMMENDED_PAIRS = 30
DELTA_MEANS_THRESHOLD = 5.0  # 蓝图阈值
ROC_AUC_THRESHOLD = 0.65      # 蓝图阈值
ROC_AUC_ACTIVE_GATE = 0.75    # av_judge shadow→active 升级门


def _mode() -> str:
    m = (os.environ.get("SFS_CALIBRATION_PROBE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。
    跟 topic_drift_scanner._has_real_embedding_backend 判断逻辑完全一致（各文件各自留一份）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


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


# ============ 真后端默认 scorer ============
# 未传 --scorer 时：真后端 → 本函数（embedding 余弦·非 placeholder）；无真后端 → 上面的
# _placeholder_sfs_score（char-3gram Jaccard·不变）。任何失败（embedding_store 不可用/
# 维度不一致等）静默回退占位（默认安全）。

def _embedding_sfs_score(text_a: str, text_b: str) -> float:
    """真后端默认 SFS scorer：embedding 余弦线性映射到 0-100（同量纲 · 非 placeholder）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import compute_embedding, cosine_similarity
        ea = compute_embedding(text_a)
        eb = compute_embedding(text_b)
        if not ea or not eb or len(ea) != len(eb):
            return _placeholder_sfs_score(text_a, text_b)
        cos = cosine_similarity(ea, eb)
        # cos ∈ [-1,1] → 0-100 线性映射（-1→0 / 0→50 / 1→100）· 待金标准校准
        # （同 sfs_axis_decomposer._real_embedding_axis_scores 换算公式）
        return round(max(0.0, min(100.0, (cos + 1.0) / 2.0 * 100.0)), 4)
    except Exception:
        return _placeholder_sfs_score(text_a, text_b)


def _load_scorer(scorer_spec: Optional[str]) -> Tuple[Callable[[str, str], float], bool]:
    """加载 scorer。spec 形如 'module:func'，显式指定优先级最高。

    未传 spec 时的默认 scorer：真后端 → _embedding_sfs_score（is_placeholder=False）；
    无真后端 → _placeholder_sfs_score（is_placeholder=True）。
    返回 (callable, is_placeholder)。"""
    if not scorer_spec:
        if _has_real_embedding_backend():
            return _embedding_sfs_score, False
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


def _read_pair_texts(pairs: List[Tuple[Path, Path]]) -> List[str]:
    """辅助：读出全部文本对（不做 CJK 过滤，过滤仍只在 _score_pairs 内发生）——
    仅供批量预热 embedding 缓存用，多读几条不影响正确性。"""
    out: List[str] = []
    for a, b in pairs:
        try:
            out.append(a.read_text(encoding="utf-8"))
            out.append(b.read_text(encoding="utf-8"))
        except OSError:
            continue
    return out


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

    # 默认 embedding scorer 对每对文本各 2 次 compute_embedding，_score_pairs 对 same/cross
    # 逐对循环调用 = 真后端下 up to 2*(N_same+N_cross) 次子进程调用。默认 scorer 精确等于
    # _embedding_sfs_score 时才一次性 prefetch 全部文本灌缓存（自定义 --scorer 不保证走
    # embedding_store，不能替它预热）；失败不影响主流程。
    if scorer is _embedding_sfs_score:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from embedding_store import prefetch_embeddings
            prefetch_embeddings(_read_pair_texts(same_pairs) + _read_pair_texts(cross_pairs))
        except Exception:
            pass

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
