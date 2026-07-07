#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-07 二轮移植 A13 切点前瞻熵（Spoiler Alert arXiv:2604.09854）
"""nn_forecast_entropy_bridge.py — 切点「前瞻熵」代理指标桥（复用 surprisal 通路·零新模型能力）。

【出处】Spoiler Alert (arXiv:2604.09854)：张力 = 读者对后续的不可预测度，
用 LM 对「接下来发生什么」的续写分布熵度量悬念。

【🔴 诚实声明：这是代理指标，不是真前瞻熵】
真前瞻熵需要 LM 生成能力（采样 k 条续写算分布熵）或 next-token logits。实地核查
（2026-07-07）：venv 侧 `core/ml/surprisal/surprisal_infer.py` 与 daemon
`core/ml/daemon/model_daemon.py` 的 `_TASK_HANDLERS` **只暴露文本聚合 surprisal 打分**
（mean/std/max/min/skew/kurt/token_count），无 generate / 无 logits。按 A13 决策路径 (b)：
**用「切点后真实下文的条件 surprisal」当前瞻熵代理**——切点处真实下文越出人意料
（给定切点前文条件下每 token 信息量越大）≈ 钩子悬念越强。同一 GPT-2 模型、零新能力。

【数学口径（严格·非拍脑袋）】对因果 LM，前缀 token 的 surprisal 不受后文影响，且
  total_bits(text) = mean_surprisal × token_count（surprisal_infer 的 mean 即逐 token 均值）。
故下文的条件总信息量 = total_bits(前文尾 + 下文头) − total_bits(前文尾)，
条件每 token 信息量 = 上式 / (token_count 差)。仅有的近似误差是拼接边界处的分词合并
（中文 char-level tokenizer 影响极小）与 mean 的 round(4) 舍入（结果对负值钳 0）。

【默认安全铁律（北极星⑤·与 nn_surprisal_bridge 同款纪律）】任一情况 → 该条返回 None：
  · nn_surprisal_bridge.enabled() 为 False（RUOYU_NN_SURPRISAL != "1" 或 venv/脚本缺）
  · 前文尾 / 下文头为空白
  · 底层任一侧打分失败（None）/ token 计数差 ≤ 0 / 数值缺失 / 任何异常
绝不抛异常、绝不崩调用方——消费侧（hook_strength_scanner）只把结果当观测列。

【输出契约】forecast_entropy_batch(pre_tails, post_heads) → list[dict|None] 与输入一一对应；
  命中元素 = {"forecast_entropy_bits_per_token": float,   # 条件每 token 信息量（bits·钳 ≥0）
              "continuation_bits_total": float,            # 下文条件总信息量（bits）
              "continuation_token_count": int,             # 下文 token 数（计数差）
              "context_token_count": int,                  # 前文尾 token 数
              "source": "surprisal_proxy"}                 # 诚实标注代理来源
阈值/校准：本桥不设任何阈值；金标准真机标定后若入权重再登记 threshold_registry（待真机标定）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nn_surprisal_bridge as _sb  # noqa: E402  复用既有 surprisal 通路（daemon-first + subprocess 回退）


def enabled() -> bool:
    """门控与底层 surprisal 通路完全同一：RUOYU_NN_SURPRISAL=1 且 venv+推理脚本齐备。
    本桥不设独立 env——它没有任何新模型能力，可用性 = surprisal 通路可用性。"""
    try:
        return bool(_sb.enabled())
    except Exception:  # noqa: BLE001 绝不崩调用方
        return False


def _pair_result(tail_stats: "dict | None", full_stats: "dict | None") -> "dict | None":
    """由（前文尾、前文尾+下文头）两侧聚合 surprisal 算条件信息量。任何缺口 → None。"""
    if not isinstance(tail_stats, dict) or not isinstance(full_stats, dict):
        return None
    try:
        mean_t = float(tail_stats["mean_surprisal"])
        n_t = int(tail_stats["token_count"])
        mean_f = float(full_stats["mean_surprisal"])
        n_f = int(full_stats["token_count"])
    except (KeyError, TypeError, ValueError):
        return None
    cont_tokens = n_f - n_t
    if n_t <= 0 or cont_tokens <= 0:
        return None
    cont_bits = mean_f * n_f - mean_t * n_t
    # mean 是 round(4) 后回传·极端下钳负为 0（surprisal 恒正·负值只能来自舍入）
    per_token = max(0.0, cont_bits / cont_tokens)
    return {
        "forecast_entropy_bits_per_token": round(per_token, 4),
        "continuation_bits_total": round(cont_bits, 4),
        "continuation_token_count": cont_tokens,
        "context_token_count": n_t,
        "source": "surprisal_proxy",
    }


def forecast_entropy_batch(pre_tails: "list[str]", post_heads: "list[str]",
                           timeout: "float | None" = None) -> "list[dict | None]":
    """批量计算切点前瞻熵代理。保序一一对应；任何不可用/失败 → 该条 None（绝不抛）。

    每个切点两次打分（前文尾、前文尾+下文头）合并成一个 surprisal 批次，
    daemon 命中时整批 ~0.1s 级。
    """
    n = len(pre_tails)
    none_list: "list[dict | None]" = [None] * n
    if n == 0:
        return []
    if len(post_heads) != n:
        print(f"[nn_forecast_entropy_bridge] 输入长度失配 {n}!={len(post_heads)}·全 None",
              file=sys.stderr)
        return none_list
    if not enabled():
        return none_list

    # 预检：空白前文/下文的切点不进批次（诚实 None·不浪费推理）
    valid_idx: "list[int]" = []
    texts: "list[str]" = []
    ids: "list[str]" = []
    for i, (pre, post) in enumerate(zip(pre_tails, post_heads)):
        pre_s, post_s = str(pre or ""), str(post or "")
        if not pre_s.strip() or not post_s.strip():
            continue
        valid_idx.append(i)
        texts.extend([pre_s, pre_s + post_s])
        ids.extend([f"fc_{i}_tail", f"fc_{i}_full"])
    if not valid_idx:
        return none_list

    try:
        scored = _sb.predict_batch(texts, ids=ids, timeout=timeout)
    except Exception as e:  # noqa: BLE001 底层桥契约本不抛·双保险
        print(f"[nn_forecast_entropy_bridge] 底层桥异常·全 None：{type(e).__name__}: {str(e)[:120]}",
              file=sys.stderr)
        return none_list
    if not isinstance(scored, list) or len(scored) != len(texts):
        return none_list

    out = list(none_list)
    for k, i in enumerate(valid_idx):
        out[i] = _pair_result(scored[2 * k], scored[2 * k + 1])
    return out


def forecast_entropy_one(pre_tail: str, post_head: str,
                         timeout: "float | None" = None) -> "dict | None":
    """单切点便捷封装（内部走批量）。失败 → None。"""
    return forecast_entropy_batch([pre_tail], [post_head], timeout=timeout)[0]


def main():
    """CLI 自测：python nn_forecast_entropy_bridge.py "前文尾" "下文头" """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    pre = args[0] if len(args) >= 1 else "他推开门，屋里一片漆黑，桌上的茶还冒着热气。"
    post = args[1] if len(args) >= 2 else "椅子上坐着一个人，正对着门口，一动不动。"
    print(f"enabled={enabled()}")
    print(json.dumps({"pre": pre, "post": post,
                      "result": forecast_entropy_one(pre, post)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
