#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_family_judge_check.py — 跨家族 judge ensemble 抑制 self-preference / family bias
(advisory · shadow · 2026-06-20 R10 W6 Batch-O · L60 P1)

【缺口】R10 联网调研(arXiv 2604.23178 Judging the Judges May 2026 — Claude
self-preference +11.2pp · Gemini +4.6pp + NeurIPS 2026 Self-Preference Bias +
FutureAGI 2026 三家族 ensemble)：单模型 judge 评分系统性偏向同家族产出
(self-preference bias)。此前 judge_runner 全程 gemini 一家族 → 风险。

【做法 · 程序逻辑(脱离主 judge_runner 不破坏既有契约)】：
  1. 仅 audit + voice 两个最高权重 judge + 仅 cluster_finale 末 sub-cluster
     触发(每 cluster 至多 +2 LLM call)。
  2. 第二家族 spawn 一个 Claude judge(BYOK 凭 Claude key 在 keyring/env 时
     启用·缺 → 静默 skip 返回 status=skipped)。
  3. 输出 cross_family_check 字段：
     {status:'agree'|'disagree'|'skipped',
      verdict_pair:[g_verdict, c_verdict], reason}
  4. disagree 且 gemini=pass / claude=issues → 提示 advisory hint 写入下
     cluster build_manifest 提示段。
  5. 缓存到 _数据库/.judge/cross_family_calibration.json baseline。

【北极星② / ⑤】纯 advisory · shadow 默认 · 永不阻断主 judge · 绝不 hard_gate。
  env CROSS_FAMILY_JUDGE_MODE: off / shadow(默认) / active。

本模块为程序入口·judge_runner 集成时只需 import + 调 maybe_run() 即可。

用法：python cross_family_judge_check.py [--draft <path>] [--project <root>]
       [--judge-name audit|voice] [--gemini-verdict pass|issues]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

OUT_KEY = "cross_family_check"

ELIGIBLE_JUDGES = {"audit", "voice"}


def _mode() -> str:
    m = (os.environ.get("CROSS_FAMILY_JUDGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _has_claude_key() -> bool:
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "RUOYU_CLAUDE_KEY"):
        if os.environ.get(k):
            return True
    # 也接受 keyring 抽象(可选 import)
    try:
        from secrets_store import get as ks_get  # type: ignore
        v = ks_get("ruoyuai-claude")
        if v:
            return True
    except Exception:
        return False
    return False


def maybe_run(*, judge_name=None, is_finale_subcluster=False,
              gemini_verdict=None, draft_text=None, project_root=None,
              author_style=None) -> dict:
    """主入口 - judge_runner 应在 audit/voice judge 跑完后调用。

    输出永远 dict(status: skipped/agree/disagree)。
    主流程不读 status 也能正常工作(advisory 性质)。
    """
    mode = _mode()
    out = {"status": "skipped", "mode": mode, "judge_name": judge_name,
           "verdict_pair": [gemini_verdict, None], "reason": None}
    if mode == "off":
        out["reason"] = "mode=off"
        return out
    if judge_name not in ELIGIBLE_JUDGES:
        out["reason"] = f"judge {judge_name} 不在 ensemble eligible 清单"
        return out
    if not is_finale_subcluster:
        out["reason"] = "非 cluster_finale 末 sub-cluster · 不触发"
        return out
    if not _has_claude_key():
        out["reason"] = "无 Claude BYOK key · 静默 skip(北极星② 不阻断)"
        return out
    if not draft_text:
        out["reason"] = "无草稿输入"
        return out

    # 真实 LLM 调用应走 llm_transport (Claude 协议)。
    # 当前 shadow 阶段 stub: 返回与 gemini 同 verdict 做 baseline 校准记录占位。
    # 真集成时替换：claude_verdict = call_claude_judge(draft_text, judge_name, ...)
    claude_verdict = _stub_claude_call(draft_text, judge_name)
    out["verdict_pair"] = [gemini_verdict, claude_verdict]
    out["status"] = "agree" if (
        gemini_verdict and claude_verdict
        and gemini_verdict == claude_verdict) else "disagree"
    out["reason"] = ("verdict 一致" if out["status"] == "agree"
                     else f"verdict 分歧 g={gemini_verdict} vs c={claude_verdict}")
    out["build_manifest_hint"] = None
    if out["status"] == "disagree" and gemini_verdict == "pass" \
            and claude_verdict in ("issues", "fail"):
        out["build_manifest_hint"] = (
            f"[cross_family_check advisory] 上 cluster {judge_name} judge "
            f"Claude 家族复审报 issues · gemini 报 pass · 下 cluster 注意复检")
    # baseline calibration cache
    if project_root:
        try:
            db = Path(project_root) / "_数据库" / ".judge"
            db.mkdir(parents=True, exist_ok=True)
            cache = db / "cross_family_calibration.json"
            calib = {}
            if cache.exists():
                try:
                    calib = json.loads(cache.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    calib = {}
            agg = calib.setdefault(judge_name, {"agree": 0, "disagree": 0})
            if out["status"] == "agree":
                agg["agree"] += 1
            elif out["status"] == "disagree":
                agg["disagree"] += 1
            cache.write_text(json.dumps(calib, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            out["calibration_cache"] = str(cache)
        except OSError:
            pass
    return out


def _stub_claude_call(draft_text, judge_name):
    """shadow 阶段 stub — 返回固定 'pass' 标识尚未真实调 Claude。
    真集成时替换为 llm_transport.call(provider='claude', ...)。
    """
    if not draft_text:
        return None
    return "pass"


def main():
    ap = argparse.ArgumentParser(
        description="跨家族 judge ensemble shadow 入口 (advisory)")
    ap.add_argument("--draft", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--judge-name", choices=sorted(ELIGIBLE_JUDGES),
                    default="audit")
    ap.add_argument("--gemini-verdict", default="pass")
    ap.add_argument("--is-finale", action="store_true")
    args = ap.parse_args()
    draft_text = None
    if args.draft and Path(args.draft).exists():
        draft_text = Path(args.draft).read_text(encoding="utf-8")
    rep = maybe_run(judge_name=args.judge_name,
                    is_finale_subcluster=args.is_finale,
                    gemini_verdict=args.gemini_verdict,
                    draft_text=draft_text,
                    project_root=args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
