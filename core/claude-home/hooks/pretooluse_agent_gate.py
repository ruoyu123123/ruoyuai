#!/usr/bin/env python3
"""
PreToolUse Hook: 校验 Agent 工具调用的 prompt 合规性
只拦截 Agent 工具，其他工具直接放行
exit 0 = 放行, exit 2 = 拒绝

全部判定逻辑（规则 1 契约字段 / 2 长度 / 3 frontmatter / 4 规则文本 warn /
5 多步 PLAN_ID / 8 plan 防篡改 / 9 注入模板 warn / 10 ECAS RESEARCH_REF /
11 蒸馏复刻同栈提示 warn）连同关键词常量在共享库
`plan_step_gates.check_agent_injection`（单一真相源）。
本 hook 是薄 wrapper：解析 stdin → 抽 prompt/desc/subagent_type → 算
plan_state(ok/not_found/tampered/...) → 调 check → 打印 warnings → ok ? exit 0 : exit 2。

exit 语义：所有硬规则命中 → exit 2；warn-only（规则 4/9/11）只打印不退出。

规则要点：
- 规则 5/6：多步流水线 Agent 必须含 PLAN_ID/STEP；novel 主链 PLAN_ID 只用于 plan
  绑定/防篡改，不豁免 PROJECT/CLUSTER_ID/MODE。
- 规则 8：PLAN_ID 引用的 plan 防篡改——plan JSON 被旁路篡改（伪造 step 状态
  绕过跳步防御）→ 在 Agent spawn 前拦下。novel 主链校验缺失/异常 fail closed。
- 规则 9：内容级注入模式检测（warn-only，不拦截）。
- 规则 11：复刻须先 spawn Claude agent 写场景稿，终稿只能由 distill_replicate.py
  --claude-scenes-dir 经 gemini 分段润色落盘；不合此纪律时 warn 提示，不拦截。
"""
import json
import os
import sys

# 判定逻辑 + 关键词常量在共享库 plan_step_gates（单一真相源）。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_agent_injection  # noqa: E402


def _compute_plan_state(prompt: str):
    """规则 8 防篡改：prompt 含 PLAN_ID → plan_tracker.verify_plan 结果（"tampered"/...）。

    PLAN_ID 是 novel 主链绑定字段：找不到 / 校验出错 / attestation 非 ok 都交给
    check_agent_injection fail closed。无 PLAN_ID 返回 None，由门库判定是否必需。
    """
    import re
    m = re.search(r"PLAN_ID:\s*(\S+)", prompt or "")
    if not m:
        return None
    try:
        import plan_tracker  # _SCRIPTS 已在 sys.path
        return plan_tracker.verify_plan(m.group(1).strip())
    except Exception:
        return "error"


def main():
    # stdin 必须按 bytes 读、交 json.loads 自动 UTF-8 解码。文本模式在 GBK 控制台
    # 把 UTF-8 载荷读花（CJK plan_id 花字 → verify not_found 误拦）。
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        sys.exit(0)

    if data.get("tool_name", "") != "Agent":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    prompt = tool_input.get("prompt", "")
    desc = tool_input.get("description", "")
    subagent_type = tool_input.get("subagent_type", "") or ""

    if not prompt:
        sys.exit(0)

    # 全部判定走 check_agent_injection（同序判定·首个硬命中即 block）。
    plan_state = _compute_plan_state(prompt)
    result = check_agent_injection(prompt, desc, subagent_type,
                                   plan_state=plan_state)

    # warn-only（规则 4 大段规则文本 / 规则 9 注入模板）：打印不退出。
    for w in result.get("warnings", []) or []:
        print(f"⚠️ [Hook] {w}", file=sys.stderr)

    if result["ok"]:
        sys.exit(0)

    # 硬规则命中 → exit 2
    print(f"❌ [Hook agent_gate] {result['msg']}", file=sys.stderr)
    print(f"   prompt 前 200 字: {prompt[:200]}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
