#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D4 Promise/Progress/Payoff 承诺-兑现缺口 scanner（cluster 级·advisory·默认 shadow·2026-06-14）。

检测 cluster draft 的承诺-兑现完整性：开篇立了 promise（钩子/悬念/承诺信号），结尾有无对应
payoff（兑现/揭示）。钩了不兑现 → PROMISE_PAYOFF_GAP（advisory）。

北极星⑤边界：PROMISE_PAYOFF_GAP 是【读者期待】advisory·与 hard_gate 的 FORESHADOWING_NOT_PAID
（brief 明确要求回收的事实穿帮）严格区分·作者故意延迟揭底（诡秘式）可豁免·绝不进 HARD_GATE_CODES。
env PPP_CAUSALITY_MODE 默认 shadow（过误报闸校准后才挂 scanner 集合·active 才进 issues）。
确定性关键词匹配·无 LLM·exit0 不抛错。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

PROMISE_PAYOFF_GAP = "PROMISE_PAYOFF_GAP"

# 开篇承诺信号（钩子/悬念/承诺·读者被勾起期待）
_PROMISE_CUES = ("竟然", "究竟", "为什么", "到底", "谜", "诡异", "不对劲", "危险", "倒计时",
                 "约定", "发誓", "一定要", "必须", "秘密", "隐藏", "等着")
# 结尾兑现信号（payoff/揭示/兑现）
_PAYOFF_CUES = ("原来", "终于", "真相", "揭", "兑现", "果然", "答案", "解开", "明白了",
                "这才", "破解", "水落石出", "尘埃落定")


def _cjk(s):
    return sum(1 for c in s if "一" <= c <= "鿿")


def _paras(text):
    return [p for p in re.split(r"\n\n+", text) if p.strip() and _cjk(p) >= 2]


def scan_promise_payoff(text: str) -> dict:
    """检测 PPP 完整性·返回 {issues, promise_score, payoff_score}。确定性·无 LLM。"""
    paras = _paras(text)
    if len(paras) < 3:
        return {"issues": [], "promise_score": 0, "payoff_score": 0, "note": "段落不足"}
    head = "".join(paras[:max(1, len(paras) // 4)])      # 开篇 1/4
    tail = "".join(paras[-max(1, len(paras) // 4):])      # 结尾 1/4
    promise_score = sum(1 for c in _PROMISE_CUES if c in head)
    payoff_score = sum(1 for c in _PAYOFF_CUES if c in tail)
    issues = []
    # 开篇强承诺（≥2 钩）但结尾无兑现 → 钩了不兑现（advisory）。
    # 作者故意延迟揭底（payoff 在后续 cluster）是合法 → advisory + 默认 shadow·FP 由 shadow 缓冲。
    if promise_score >= 2 and payoff_score == 0:
        issues.append({
            "code": PROMISE_PAYOFF_GAP, "severity": "warning", "gate_level": "advisory",
            "msg": f"开篇立了 {promise_score} 处承诺/悬念但本 cluster 结尾无兑现信号"
                   "（钩了不兑现·读者期待 advisory·作者有意延迟揭底如诡秘式可豁免）",
        })
    return {"issues": issues, "promise_score": promise_score, "payoff_score": payoff_score}


def main():
    ap = argparse.ArgumentParser(description="D4 Promise/Progress/Payoff scanner（advisory·默认 shadow）")
    ap.add_argument("draft", help="cluster draft 文本文件路径")
    args = ap.parse_args()
    mode = (os.environ.get("PPP_CAUSALITY_MODE") or "shadow").strip().lower()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 防 Windows GBK 崩 exit
    except Exception:
        pass
    p = Path(args.draft)
    if not p.exists():
        print(json.dumps({"issues": [], "_skip": "draft 不存在"}, ensure_ascii=False))
        sys.exit(0)
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        print(json.dumps({"issues": [], "_skip": f"读取失败:{e}"}, ensure_ascii=False))
        sys.exit(0)
    result = scan_promise_payoff(text)
    if mode != "active":   # shadow（默认）：算但不上报（过误报闸校准后才 active）
        result["_shadow"] = True
        result["issues"] = []
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
