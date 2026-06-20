#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_family_judge_check.py — 跨家族 judge ensemble 抑制 self-preference / family bias
(advisory · shadow · 2026-06-20 A 方案·inline 文件协议·吃 Claude Code 订阅零月费)

【背景】R10 联网调研(arXiv 2604.23178 Judging the Judges May 2026 — Claude
self-preference +11.2pp · Gemini +4.6pp + NeurIPS 2026 Self-Preference Bias +
FutureAGI 2026 三家族 ensemble)：单模型 judge 评分系统性偏向同家族产出
(self-preference bias)。

【2026-06-20 A 方案 inline 文件协议】
本模块**不再直连 Anthropic / 不依赖 BYOK**。
跨家族复审 = 让**主代理 Claude Code**(同一个跑流水线的 Claude 自身)在
spawn judge subprocess 之前，先 spawn 一个 Agent(claude) 复审 finale subcluster 的
audit / voice 维度·结果以 inline 文件落到 `_数据库/.wal/claude_verdict_<sha12>_<judge>.json`。

orchestrator default_judge_dispatch 末端调 maybe_run：
  ① 模式 off / 非 eligible / 非 finale / 无 draft → skip
  ② 查 `_数据库/.wal/claude_verdict_<draft_sha[:12]>_<judge>.json` → 命中即用
     (verdict + reason + source='inline_agent_spawn')
  ③ 未命中 → skip + reason='无主代理 inline verdict·建议主代理 spawn Agent 写 .wal 后重跑'·
     **绝不阻断主链**(advisory)

主代理给 finale subcluster 主动 spawn Agent(claude) 复审后调
`save_inline_verdict_for_main_agent(draft_text, judge_name, verdict, reason, project_root)`
落 .wal 文件·下一次 cluster-save-state step 7 audit/voice 跑到 orchestrator 末端就会被捡用。

env CROSS_FAMILY_JUDGE_MODE: off / shadow(默认) / active（off 显式标 mode=off）。

用法（CLI 兼容保留）：python cross_family_judge_check.py [--judge-name audit|voice] ...
"""
from __future__ import annotations

import argparse
import hashlib
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


def _draft_sha12(draft_text: str) -> str:
    """前 12 位 sha256 = inline verdict 文件名锚·防 draft 改了仍用陈旧裁决。"""
    h = hashlib.sha256(draft_text.encode("utf-8", errors="replace")).hexdigest()
    return h[:12]


def _inline_verdict_path(project_root, draft_text: str, judge_name: str) -> Path:
    """返回 inline 复审 verdict 文件标准路径。"""
    pr = Path(project_root) if project_root else Path(".")
    sha = _draft_sha12(draft_text)
    return pr / "_数据库" / ".wal" / f"claude_verdict_{sha}_{judge_name}.json"


def _try_load_inline_verdict(draft_text: str, judge_name: str,
                             project_root) -> dict | None:
    """读主代理 spawn Agent(claude) 写的 inline verdict 文件。

    路径：<project_root>/_数据库/.wal/claude_verdict_<sha[:12]>_<judge>.json
    匹配条件：文件存在 + draft_sha256_prefix 字段与当前 draft 算出来的 sha12 一致。

    返回 dict {verdict, reason, source='inline_agent_spawn'} 或 None。
    任何 OSError / JSONDecodeError / 字段缺失/类型不对 → None（try-catch 全静默）。
    """
    if not draft_text or not project_root or not judge_name:
        return None
    try:
        p = _inline_verdict_path(project_root, draft_text, judge_name)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(d, dict):
            return None
        sha = _draft_sha12(draft_text)
        if d.get("draft_sha256_prefix") != sha:
            return None
        verdict = d.get("verdict")
        if verdict not in ("pass", "issues"):
            return None
        reason = d.get("reason") or ""
        if not isinstance(reason, str):
            reason = str(reason)
        return {
            "verdict": verdict,
            "reason": reason,
            "source": "inline_agent_spawn",
            "claude_model": d.get("claude_model") or "via-claude-code-agent",
        }
    except Exception:    # noqa: BLE001 — 任何异常静默 skip 主链不阻断
        return None


def save_inline_verdict_for_main_agent(draft_text: str, judge_name: str,
                                       verdict: str, reason: str,
                                       project_root,
                                       claude_model: str =
                                       "via-claude-code-agent") -> bool:
    """主代理（Claude Code 自身）spawn Agent(claude) 复审 finale subcluster 后调本函数
    把裁决落 `_数据库/.wal/claude_verdict_<sha[:12]>_<judge>.json`·下一轮
    orchestrator 调 maybe_run 时自动 _try_load_inline_verdict 捡用。

    返回 True / False（任何 OSError / 目录不存在 → False·绝不抛）。"""
    if not draft_text or not judge_name or not project_root:
        return False
    if verdict not in ("pass", "issues"):
        return False
    try:
        p = _inline_verdict_path(project_root, draft_text, judge_name)
        # 不主动 mkdir：目录应由 outline / save-state scaffolding 已建好。
        # 目录缺失 = 项目结构不完整 → 静默 False（北极星：不干涉主流程）。
        if not p.parent.exists():
            return False
        payload = {
            "draft_sha256_prefix": _draft_sha12(draft_text),
            "judge_name": judge_name,
            "verdict": verdict,
            "reason": reason or "",
            "source": "inline_agent_spawn",
            "claude_model": claude_model,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return True
    except Exception:    # noqa: BLE001
        return False


def maybe_run(*, judge_name=None, is_finale_subcluster=False,
              gemini_verdict=None, draft_text=None, project_root=None,
              author_style=None, cluster_key=None,
              _generate_fn=None) -> dict:
    """主入口（A 方案 inline 文件协议·永不阻断主链）。

    流程：
      ① mode=off / judge_name 不在 ELIGIBLE_JUDGES / 非 finale / 无 draft → skip
      ② 查 inline verdict 文件命中 → 用 inline (source='inline_agent_spawn')
      ③ 未命中 → skip + 提示主代理 spawn Agent 写 .wal 后重跑（绝不阻断 advisory）
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
    inline = _try_load_inline_verdict(draft_text, judge_name, project_root)
    if inline is None:
        out["reason"] = (
            "无主代理 inline verdict·建议主代理 spawn Agent 写 .wal 后重跑")
        return out
    # 命中：标 completed + verdict_pair[1] = claude verdict + 写入 outcome.data
    claude_verdict = inline["verdict"]
    out["status"] = "completed"
    out["verdict_pair"] = [gemini_verdict, claude_verdict]
    out["claude_verdict"] = claude_verdict
    out["claude_reason"] = inline["reason"]
    out["claude_model"] = inline["claude_model"]
    out["source"] = inline["source"]
    # agree / disagree 简单语义：双方一致 = agree·否则 disagree (gemini 缺失视为未知)
    if gemini_verdict in ("pass", "issues"):
        out["agreement"] = "agree" if gemini_verdict == claude_verdict else "disagree"
    else:
        out["agreement"] = "unknown_gemini_verdict"
    out["reason"] = (
        f"inline_agent_spawn 命中·gemini={gemini_verdict} / "
        f"claude={claude_verdict} / {out['agreement']}")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="跨家族 judge ensemble 入口 (A 方案 inline 文件协议)")
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
