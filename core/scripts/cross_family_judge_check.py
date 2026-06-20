#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_family_judge_check.py — 跨家族 judge ensemble 抑制 self-preference / family bias
(advisory · shadow · 2026-06-20 A 方案回滚至 inline 文件协议占位)

【背景】R10 联网调研(arXiv 2604.23178 Judging the Judges May 2026 — Claude
self-preference +11.2pp · Gemini +4.6pp + NeurIPS 2026 Self-Preference Bias +
FutureAGI 2026 三家族 ensemble)：单模型 judge 评分系统性偏向同家族产出
(self-preference bias)。

【2026-06-20 A 方案回滚】用户决策：主代理 Claude Code CLI 即唯一入口·
不再维护 BYOK Anthropic 协议直连。本模块降级为占位 stub：
  - 保留 maybe_run 入口契约（status / mode / judge_name / verdict_pair / reason）
  - 永远返回 status='skipped' + reason='BYOK Claude path removed — awaiting inline file
    protocol (phase 2)'
  - 不再调 llm_transport / Anthropic / keyring
  - orchestrator default_judge_dispatch 末端 hook 调用契约不变（永远拿到 skipped）

【下一 phase 设计】跨家族复审改 inline 文件协议（主代理 Claude Code 自身读 draft + judge
prompt 后写 _数据库/.wal/claude_verdict_<sha>_<name>.json·orchestrator 读该文件做
agree/disagree 比对）。在那之前永远 skipped 维持 advisory · shadow 默认不阻断主链。

env CROSS_FAMILY_JUDGE_MODE: off / shadow(默认) / active（off 显式标 mode=off）。

用法（CLI 兼容保留）：python cross_family_judge_check.py [--judge-name audit|voice] ...
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_log = logging.getLogger("ruoyuai.cross_family_judge")

OUT_KEY = "cross_family_check"

ELIGIBLE_JUDGES = {"audit", "voice"}


def _mode() -> str:
    m = (os.environ.get("CROSS_FAMILY_JUDGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def maybe_run(*, judge_name=None, is_finale_subcluster=False,
              gemini_verdict=None, draft_text=None, project_root=None,
              author_style=None, cluster_key=None,
              _generate_fn=None) -> dict:
    """主入口（A 方案回滚后占位 stub）。

    永远返回 status='skipped'（详见模块 docstring）。维持 6 个守门返回理由的可区分性，
    让 orchestrator 与现有调用者契约零回归。
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
    if not draft_text:
        out["reason"] = "无草稿输入"
        return out
    # A 方案：BYOK Claude path 已删 → inline 文件协议尚未实装 → 永远 skipped。
    out["reason"] = (
        "BYOK Claude path removed (2026-06-20) — awaiting inline file protocol (phase 2)")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="跨家族 judge ensemble 入口 (A 方案占位 stub · 永远 skipped)")
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
