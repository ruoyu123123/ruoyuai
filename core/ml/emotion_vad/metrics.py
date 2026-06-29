# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN训练:情绪VAD回归
"""metrics.py — VAD 回归评估指标（**纯 stdlib·零依赖**，train/eval 共用·单一真理源）。

每维报 4 个指标（中文维度情感任务历来用 MAE/RMSE/Pearson；SER 圈爱 CCC）：
  MAE     平均绝对误差
  RMSE    均方根误差
  Pearson 皮尔逊相关 r
  CCC     Concordance Correlation Coefficient（一致性·SOTA 主指标）
"""
from __future__ import annotations

import math


def _pearson(p, g):
    n = len(p)
    if n < 2:
        return float("nan")
    mp, mg = sum(p) / n, sum(g) / n
    cov = sum((p[i] - mp) * (g[i] - mg) for i in range(n))
    vp = sum((x - mp) ** 2 for x in p)
    vg = sum((x - mg) ** 2 for x in g)
    if vp <= 1e-12 or vg <= 1e-12:
        return float("nan")
    return cov / math.sqrt(vp * vg)


def _ccc(p, g):
    n = len(p)
    if n < 2:
        return float("nan")
    mp, mg = sum(p) / n, sum(g) / n
    vp = sum((x - mp) ** 2 for x in p) / n
    vg = sum((x - mg) ** 2 for x in g) / n
    cov = sum((p[i] - mp) * (g[i] - mg) for i in range(n)) / n
    denom = vp + vg + (mp - mg) ** 2
    if denom <= 1e-12:
        return float("nan")
    return 2 * cov / denom


def _mae(p, g):
    n = len(p)
    return sum(abs(p[i] - g[i]) for i in range(n)) / n if n else float("nan")


def _rmse(p, g):
    n = len(p)
    return math.sqrt(sum((p[i] - g[i]) ** 2 for i in range(n)) / n) if n else float("nan")


def vad_metrics(preds: dict, golds: dict) -> dict:
    """preds/golds: {dim_name: [float,...]}（已按掩码过滤·一一对齐）。返回 {dim: {mae,rmse,pearson,ccc,n}}。"""
    out = {}
    for d in preds:
        p, g = preds[d], golds.get(d, [])
        n = min(len(p), len(g))
        p, g = p[:n], g[:n]
        out[d] = {
            "n": n,
            "mae": round(_mae(p, g), 4) if n else None,
            "rmse": round(_rmse(p, g), 4) if n else None,
            "pearson": round(_pearson(p, g), 4) if n else None,
            "ccc": round(_ccc(p, g), 4) if n else None,
        }
    return out
