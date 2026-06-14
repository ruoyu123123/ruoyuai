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


def estimate_cost(summary: dict, price_per_1m_input: float = 0.0,
                  price_per_1m_output: float = 0.0) -> dict:
    """按价格估算成本（BYOK 用户自付·价格用户传·默认 0 不估）。cached 不重复计（已含 prompt）。"""
    inp = int(summary.get("prompt_tokens", 0) or 0)
    out = int(summary.get("output_tokens", 0) or 0)
    cost_input = inp / 1_000_000 * price_per_1m_input
    cost_output = out / 1_000_000 * price_per_1m_output
    return {
        "cost_input": round(cost_input, 6),
        "cost_output": round(cost_output, 6),
        "cost_total": round(cost_input + cost_output, 6),
        "price_per_1m_input": price_per_1m_input,
        "price_per_1m_output": price_per_1m_output,
    }


def main():
    ap = argparse.ArgumentParser(description="token 账本汇总（BYOK 用户看 token/成本）")
    ap.add_argument("ledger_path")
    ap.add_argument("--price-per-1m-input", type=float, default=0.0)
    ap.add_argument("--price-per-1m-output", type=float, default=0.0)
    args = ap.parse_args()
    s = summarize(args.ledger_path)
    out = {"summary": s}
    if args.price_per_1m_input or args.price_per_1m_output:
        out["cost"] = estimate_cost(s, args.price_per_1m_input, args.price_per_1m_output)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
