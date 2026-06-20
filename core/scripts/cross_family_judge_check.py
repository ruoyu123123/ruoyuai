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

【R10 W6 真实化(2026-06-20)】stub→真 Anthropic /v1/messages 调用(经 llm_transport
新增 anthropic 协议)·BYOK key 三级解析(keyring SERVICE_CLAUDE → env ANTHROPIC_API_KEY/
CLAUDE_API_KEY/RUOYU_CLAUDE_KEY → None)·任何 transport 异常静默 skip(北极星② 不阻断)·
prompt 复用 judge_runner 同款 agent .md(单一真理源·硬契约 1 作者档第一权威)·
重试边界压到 1 轮(advisory 不重金·硬契约 2)。

模型默认 claude-opus-4-5(env CLAUDE_JUDGE_MODEL 可覆盖给 sonnet/haiku 省成本)。

用法：python cross_family_judge_check.py [--draft <path>] [--project <root>]
       [--judge-name audit|voice] [--gemini-verdict pass|issues]
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

# 短名 → judge_runner AGENT_SPECS 全名（复用 .md 单一真理源）
_JUDGE_AGENT_MAP = {
    "audit": "novel-validator-checker",
    "voice": "novel-voice-checker",
}


def _mode() -> str:
    m = (os.environ.get("CROSS_FAMILY_JUDGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _resolve_claude_key() -> str | None:
    """三级优先解析 Claude BYOK key（与 gen-model 主 key 隔离）：
      1. keyring SERVICE_CLAUDE / 'judge'（分发版主路径 · DPAPI 加密）
      2. env ANTHROPIC_API_KEY → CLAUDE_API_KEY → RUOYU_CLAUDE_KEY
      3. None → 静默 skip
    任何 keyring 故障吞 None（绝不冒泡·维持 maybe_run 静默 skip 契约）。
    """
    # 1) keyring
    try:
        import secrets_store
        if hasattr(secrets_store, "get_claude_key"):
            k = secrets_store.get_claude_key()
            if k and str(k).strip():
                return str(k).strip()
    except Exception:
        pass
    # 2) env
    for ename in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "RUOYU_CLAUDE_KEY"):
        v = (os.environ.get(ename) or "").strip()
        if v:
            return v
    return None


def _has_claude_key() -> bool:
    return _resolve_claude_key() is not None


def _build_claude_profile(api_key: str):
    """动态构造 Anthropic Profile（无需 .env GEN__claude__ 配置·跨家族独立）。"""
    from gen_model_loader import Profile
    model = (os.environ.get("CLAUDE_JUDGE_MODEL")
             or "claude-opus-4-5-20250929").strip()
    base_url = (os.environ.get("ANTHROPIC_BASE_URL")
                or "https://api.anthropic.com").strip().rstrip("/")
    return Profile(
        name="__claude_judge__", model=model, base_url=base_url,
        api_key=api_key, temperature=0.3, max_tokens=16000,
        protocol="anthropic",
    )


def _build_system_for_judge(judge_name: str, project_root: Path | None) -> str:
    """复用 judge_runner.load_agent_system_prompt + build_author_profile_block·
    单一真理源（.claude/agents/<full>.md）+ 作者档第一权威（硬契约 1）。"""
    import judge_runner as jr
    full = _JUDGE_AGENT_MAP[judge_name]
    parts = [jr.ADAPTER_HEADER, jr.load_agent_system_prompt(full)]
    if project_root is not None:
        block = jr.build_author_profile_block(Path(project_root))
        if block:
            parts.append(block)
        else:
            parts.append(jr.AUTHOR_PROFILE_MISSING_GUARD)
    return "\n\n".join(parts)


def _build_user_for_judge(draft_text: str, judge_name: str) -> str:
    """跨家族复审 user prompt：内容 = cluster 草稿 + 输出契约（与 judge_runner
    assemble_user_prompt 同款形态·但只附草稿一个文件·judge_runner 复杂多文件代读不复现）。"""
    return (
        f"# 跨家族复审任务\n"
        f"职责类型：{judge_name}（与 Gemini 家族 judge 同款判断·此处 Claude 复审用于抑制 self-preference bias）\n\n"
        f"# 输入契约\nJUDGE_TYPE: {judge_name}\n\n"
        f"【文件: cluster_draft.txt】\n{draft_text}\n\n"
        f"现在执行你的职责，输出最终 JSON（```json 围栏包裹）。"
        f"必须含顶层字段 'verdict'（取值 'pass' 或 'issues'）。"
    )


def _call_claude_judge(draft_text: str, judge_name: str,
                      project_root: Path | None,
                      _generate_fn=None) -> str | None:
    """调 Claude 跨家族复审 → 归一 'pass' / 'issues' / None（None = 调用/解析失败）。

    任何异常静默 skip（北极星② 不阻断主 judge）。重试边界压到 1 轮（advisory·成本敏感）。
    """
    if not draft_text:
        return None
    key = _resolve_claude_key()
    if not key:
        return None
    try:
        import llm_transport as lt
        profile = _build_claude_profile(key)
        system = _build_system_for_judge(judge_name, project_root)
        user = _build_user_for_judge(draft_text, judge_name)
        gen = _generate_fn or lt.generate
        result = gen(
            [profile], system, user,
            max_tokens=profile.max_tokens,
            temperature=profile.temperature,
            response_format_json=True,
            retry=lt.RetryPolicy(max_retries=1, base_delay=1.0, max_cont_rounds=1),
            label=f"cross_family:{judge_name}",
        )
        data = lt.parse_json_loose(result.text)
        if not isinstance(data, dict) or data.get("_parse_failed"):
            return None
        v = data.get("verdict")
        if isinstance(v, str):
            v_norm = v.strip().lower()
            if v_norm in ("pass", "ok", "agree"):
                return "pass"
            if v_norm in ("issues", "fail", "block", "disagree"):
                return "issues"
        # 兜底：有 violations 列表 → issues·否则 pass
        vs = data.get("violations")
        if isinstance(vs, list):
            return "issues" if len(vs) > 0 else "pass"
        return None
    except Exception as exc:    # noqa: BLE001 — 绝不让 Claude 调用炸主链
        _log.debug("cross_family Claude 调用失败 · 静默 skip: %s: %s",
                   type(exc).__name__, str(exc)[:200])
        return None


def maybe_run(*, judge_name=None, is_finale_subcluster=False,
              gemini_verdict=None, draft_text=None, project_root=None,
              author_style=None, cluster_key=None,
              _generate_fn=None) -> dict:
    """主入口 - judge_runner / orchestrator 应在 audit/voice judge 跑完后调用。

    输出永远 dict(status: skipped/agree/disagree)。
    主流程不读 status 也能正常工作(advisory 性质)。

    Args:
      _generate_fn: 测试注入点（签名同 lt.generate）。
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

    # 真 Claude 调用（任何异常 → None → 标 skipped）。
    claude_verdict = _call_claude_judge(
        draft_text, judge_name,
        Path(project_root) if project_root else None,
        _generate_fn=_generate_fn,
    )
    if claude_verdict is None:
        out["reason"] = "Claude 调用失败/解析失败 · 静默降级 skip(北极星② 不阻断)"
        return out

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


def main():
    ap = argparse.ArgumentParser(
        description="跨家族 judge ensemble 入口 (advisory · BYOK Claude)")
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
