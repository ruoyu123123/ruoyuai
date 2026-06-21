#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sfs_axis_decomposer.py — FicSim 12-axis disentangled SFS 分解器 · shadow-only · 2026-06-21 R20 W9 Batch-BB · P2

【缺口 · R20 fanfic_voice id 3】FicSim (Reagan 2025) 论文揭示：单值 SFS（Style
Fidelity Score）混淆 plot/theme/voice 多维相似度，可能 90 分但 voice 维度只 40。
12 轴分解定位「真 voice 复刻」vs「只是 plot 相似」。

【12 轴 · 当前 placeholder】
  · 4 av_judge traits（已存在 voice/pace/imagery/syntax → 复用 av_judge.AV_DIMS）
  · 8 sentence-embedding cosine 代理（_placeholder）：
      Plot / CharacterStates / Relationship / Theme / Time / ToneTags / Fandom / Author

【做法 · shadow-only · 部署前一次性比对 · distill_replicate --axis-decompose】
  · 输入：author_text + replica_text + (optional) av_verdicts dict
  · 输出：{ axis_name: { score: 0-100, source: "av_judge"|"embed_placeholder", _placeholder: bool } }
  · 衍生：SFS_PLOT_CONFOUND_RISK — Author/Voice < 50 但 Plot > 70 → 风险
         AXIS_ALL_HIGH_PASS — 全轴 ≥ 60 PASS
  · 嵌入器接口预留 `compute_embedding_axes(author, replica)` → 返回 placeholder 50 ± 噪声
    真嵌入实现 defer（need sentence-transformers + 中文 SBERT 模型）。

【北极星】②④⑤ shadow-only · 永不进 hard_gate · 不改 active SFS 报警链
  仅作为 distill_replicate 部署前一次性诊断 · audit_hub 不调度。

env SFS_AXIS_DECOMPOSE_MODE: off / shadow(默认)
用法: python sfs_axis_decomposer.py --author <p1> --replica <p2> [--av-json <p3>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

EMBED_AXES = ("Plot", "CharacterStates", "Relationship", "Theme",
              "Time", "ToneTags", "Fandom", "Author")
AV_AXES = ("voice", "pace", "imagery", "syntax")

ISSUE_CODE_CONFOUND = "SFS_PLOT_CONFOUND_RISK"
ISSUE_CODE_ALL_HIGH = "AXIS_ALL_HIGH_PASS"

DEFAULT_PLOT_HIGH = 70.0
DEFAULT_VOICE_LOW = 50.0
DEFAULT_ALL_PASS_LOW = 60.0


def _mode() -> str:
    m = (os.environ.get("SFS_AXIS_DECOMPOSE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow") else "shadow"


def _deterministic_seed(author: str, replica: str, axis: str) -> int:
    """从输入算可复现 seed → 占位嵌入器输出稳定，便于测试。"""
    h = hashlib.sha256()
    h.update(author.encode("utf-8", errors="ignore"))
    h.update(b"||")
    h.update(replica.encode("utf-8", errors="ignore"))
    h.update(b"||")
    h.update(axis.encode("utf-8"))
    return int.from_bytes(h.digest()[:4], "big")


def compute_embedding_axes(author: str, replica: str) -> dict:
    """占位嵌入轴 · 真 SBERT 实现 defer。
    用 sha256 派生 deterministic 0-100 分数 (基线 50 ± 25) · 便于测试稳定。"""
    out = {}
    for ax in EMBED_AXES:
        seed = _deterministic_seed(author, replica, ax)
        # 0..50 范围抖动 + 25 基线 → [25, 75]
        raw = (seed % 50) + 25.0
        out[ax] = {
            "score": round(raw, 1),
            "source": "embed_placeholder",
            "_placeholder": True,
        }
    return out


def merge_av_axes(av_verdicts: dict | None) -> dict:
    """av_judge 4 维 → 0-100 SFS 同标尺。av_judge 默认 verdict={voice:int(1-5),...}。
    1-5 → (n-1)/4*100 线性映射。"""
    out = {}
    if not isinstance(av_verdicts, dict):
        return {ax: {"score": None, "source": "av_missing", "_placeholder": False} for ax in AV_AXES}
    for ax in AV_AXES:
        raw = av_verdicts.get(ax)
        score = None
        if isinstance(raw, (int, float)):
            score = max(0.0, min(100.0, (float(raw) - 1.0) / 4.0 * 100.0))
        elif isinstance(raw, dict) and isinstance(raw.get("score"), (int, float)):
            v = float(raw["score"])
            # 0-100 已是百分制 / 1-5 → 1-5 量纲
            score = v if v > 5 else max(0.0, min(100.0, (v - 1.0) / 4.0 * 100.0))
        out[ax] = {"score": round(score, 1) if score is not None else None,
                   "source": "av_judge", "_placeholder": False}
    return out


def decompose(author_text: str, replica_text: str, av_verdicts: dict | None = None) -> dict:
    """主分解 API · shadow-only。"""
    embed_axes = compute_embedding_axes(author_text or "", replica_text or "")
    av_axes = merge_av_axes(av_verdicts)
    axes = {}
    axes.update(av_axes)
    axes.update(embed_axes)

    voice_score = axes.get("voice", {}).get("score")
    author_score = axes.get("Author", {}).get("score")
    plot_score = axes.get("Plot", {}).get("score")

    flags = []
    high_v = max([s for s in (voice_score, author_score) if s is not None], default=None)
    if plot_score is not None and plot_score > DEFAULT_PLOT_HIGH:
        low_v = min([s for s in (voice_score, author_score) if s is not None],
                    default=None)
        if low_v is not None and low_v < DEFAULT_VOICE_LOW:
            flags.append({
                "code": ISSUE_CODE_CONFOUND,
                "msg": f"Plot={plot_score} > {DEFAULT_PLOT_HIGH} 但 Voice/Author 最低={low_v} < {DEFAULT_VOICE_LOW}·"
                       f"plot 相似拉高总 SFS·voice 维并未仿到"
            })

    valid_scores = [d["score"] for d in axes.values() if isinstance(d.get("score"), (int, float))]
    if valid_scores and all(s >= DEFAULT_ALL_PASS_LOW for s in valid_scores):
        flags.append({
            "code": ISSUE_CODE_ALL_HIGH,
            "msg": f"全 {len(valid_scores)} 轴 ≥ {DEFAULT_ALL_PASS_LOW}·12 轴均衡 PASS"
        })

    return {
        "scanner": "sfs_axis_decomposer",
        "schema_version": "1.0",
        "mode": _mode(),
        "gate_level": "advisory",
        "axes": axes,
        "n_av_axes": len(AV_AXES),
        "n_embed_axes": len(EMBED_AXES),
        "flags": flags,
        "violations": [],
        "warning": None,
        "verdict": "PASS",
        "_embedding_placeholder": True,
        "_doc": "FicSim 12-axis · shadow-only · 部署前诊断 · 真 SBERT defer",
    }


def main():
    ap = argparse.ArgumentParser(description="FicSim 12-axis SFS decomposer (shadow-only)")
    ap.add_argument("--author", required=True, help="作者原文路径")
    ap.add_argument("--replica", required=True, help="复刻文本路径")
    ap.add_argument("--av-json", default=None, help="av_judge verdict json (可选)")
    args = ap.parse_args()

    if _mode() == "off":
        print(json.dumps({"scanner": "sfs_axis_decomposer", "mode": "off",
                          "verdict": "PASS"}, ensure_ascii=False))
        sys.exit(0)

    try:
        a = Path(args.author).read_text(encoding="utf-8")
        r = Path(args.replica).read_text(encoding="utf-8")
    except OSError as e:
        print(json.dumps({"error": f"读取失败：{e}"}, ensure_ascii=False))
        sys.exit(2)

    av = None
    if args.av_json:
        try:
            av = json.loads(Path(args.av_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            av = None

    rep = decompose(a, r, av)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
