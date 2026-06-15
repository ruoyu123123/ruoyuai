#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token_ledger.py — token 账本汇总（一人公司·BYOK 用户看「烧了多少钱」·2026-06-15）

【缺口】llm_transport L361 抓到 gemini usage 却 L377 直接丢弃（return text,finish），
整个系统【无 token 账本】——BYOK 非技术用户自付 token，一本书跑下来花多少没有可观测口径
（一人公司调研 P0·变现参谋硬前置·critic 实地复核属实）。

【做法】llm_transport._record_token_usage 把每次 gemini 调用 token append 到 jsonl 账本
（env RUOYU_TOKEN_LEDGER 指向·建议 项目/_数据库/.token_ledger.jsonl）。本模块读账本汇总：
调用次数 / 总 prompt·output·cached tokens / 按模型分组 / 成本估算（价格用户传·BYOK 自付）。

【北极星】账本是 advisory 可观测层·不碰写作判断·落盘失败不崩 transport（不影响主轨）。

用法：python token_ledger.py <ledger.jsonl> [--price-per-1m-input X --price-per-1m-output Y]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def default_ledger_path(project_root) -> Path:
    """项目默认账本路径（_数据库/.token_ledger.jsonl）·供调用方设 env RUOYU_TOKEN_LEDGER。"""
    return Path(project_root) / "_数据库" / ".token_ledger.jsonl"


def summarize(ledger_path) -> dict:
    """读 jsonl 账本 → 汇总（调用次数 / 总 token / 按模型分组）。账本缺失 → 全 0（不崩）。"""
    p = Path(ledger_path)
    empty = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0,
             "cached_tokens": 0, "total_tokens": 0, "by_model": {}}
    if not p.exists():
        return empty
    calls = 0
    tot = {"prompt_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "total_tokens": 0}
    by_model: dict = {}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return empty
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue   # 坏行跳过（容错·部分写入不毁整账本）
        calls += 1
        for k in tot:
            tot[k] += int(rec.get(k, 0) or 0)
        m = rec.get("model", "unknown")
        bm = by_model.setdefault(m, {"calls": 0, "prompt_tokens": 0, "output_tokens": 0})
        bm["calls"] += 1
        bm["prompt_tokens"] += int(rec.get("prompt_tokens", 0) or 0)
        bm["output_tokens"] += int(rec.get("output_tokens", 0) or 0)
    return {"calls": calls, **tot, "by_model": by_model}


# 主力模型官方直连参考价（USD per 1M token·2026-06 联网调研 glbgpt+metacto 双源·标准上下文 ≤200K）。
# 🔴 仅【官方直连参考价】——active profile 走第三方中转站（pie-xian/superapi）实际按渠道计费·此表只给
#    BYOK 用户「量级感知」·绝不冒充精确账单（用户 --price 覆盖优先·北极星：不冒充我以为）。
MODEL_PRICE_REFERENCE = {
    "gemini-3.1-pro-preview": {"in_per_1m": 2.00, "out_per_1m": 12.00, "confidence": "high",
                               "source": "Google 官方 ≤200K 标准上下文·2026-06 调研双源"},
    "gemini-3.5-flash": {"in_per_1m": 1.50, "out_per_1m": 9.00, "confidence": "high",
                         "source": "Google 官方·2026-06 调研双源"},
    "gemini-2.5-flash-lite": {"in_per_1m": 0.10, "out_per_1m": 0.40, "confidence": "high",
                              "source": "Google 官方·2026-06 调研"},
}
_PRICE_DISCLAIMER = ("⚠️ 参考价=Google 官方直连 ≤200K 标准上下文价·仅供量级感知；实际走中转站"
                     "（pie-xian 等）以渠道计费为准·>200K 长上下文翻倍·缓存命中更低·--price 可覆盖。")


def resolve_price(model_id) -> "dict | None":
    """按 model 前缀模糊匹配参考价（账本 model 字段维度·如 gemini-3.1-pro-preview-0612 → 命中）。
    精确优先 → 最长前缀匹配（避免 flash 误命中 flash-lite）。未命中返 None（不臆造·北极星）。"""
    if not model_id or not isinstance(model_id, str):
        return None
    m = model_id.strip().lower()
    for k, v in MODEL_PRICE_REFERENCE.items():
        if m == k.lower():
            return v
    best = None
    for k, v in MODEL_PRICE_REFERENCE.items():
        kl = k.lower()
        if m.startswith(kl) or kl.startswith(m):
            if best is None or len(k) > len(best[0]):
                best = (k, v)
    return best[1] if best else None


def estimate_cost(summary: dict, price_per_1m_input: float = 0.0,
                  price_per_1m_output: float = 0.0, model: "str | None" = None) -> dict:
    """按价格估算成本（BYOK 用户自付·价格用户传优先）。两 price 均 0/None + 给 model → 回退
    MODEL_PRICE_REFERENCE 查表（price_source=reference + disclaimer 量级感知）·查不到 → 0（none）。
    已传 price → user 优先（零回归）。cached 不重复计（已含 prompt）。"""
    user_priced = bool(price_per_1m_input or price_per_1m_output)
    price_source = "user"
    disclaimer = None
    if not user_priced:
        ref = resolve_price(model) if model else None
        if ref:
            price_per_1m_input = ref["in_per_1m"]
            price_per_1m_output = ref["out_per_1m"]
            price_source = "reference"
            disclaimer = _PRICE_DISCLAIMER
        else:
            price_source = "none"
    inp = int(summary.get("prompt_tokens", 0) or 0)
    out = int(summary.get("output_tokens", 0) or 0)
    cost_input = inp / 1_000_000 * price_per_1m_input
    cost_output = out / 1_000_000 * price_per_1m_output
    result = {
        "cost_input": round(cost_input, 6),
        "cost_output": round(cost_output, 6),
        "cost_total": round(cost_input + cost_output, 6),
        "price_per_1m_input": price_per_1m_input,
        "price_per_1m_output": price_per_1m_output,
        "price_source": price_source,
    }
    if disclaimer:
        result["disclaimer"] = disclaimer
    return result


def main():
    ap = argparse.ArgumentParser(description="token 账本汇总（BYOK 用户看 token/成本）")
    ap.add_argument("ledger_path")
    ap.add_argument("--price-per-1m-input", type=float, default=0.0)
    ap.add_argument("--price-per-1m-output", type=float, default=0.0)
    ap.add_argument("--use-reference-price", action="store_true",
                    help="无 --price 时用 MODEL_PRICE_REFERENCE 官方参考价（按账本主导 model 查表·量级感知非精确账单）")
    args = ap.parse_args()
    s = summarize(args.ledger_path)
    out = {"summary": s}
    model = None
    if args.use_reference_price and s.get("by_model"):
        # 账本主导 model（calls 最多）查表
        model = max(s["by_model"].items(), key=lambda kv: kv[1].get("calls", 0))[0]
    if args.price_per_1m_input or args.price_per_1m_output or args.use_reference_price:
        out["cost"] = estimate_cost(s, args.price_per_1m_input, args.price_per_1m_output, model=model)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
