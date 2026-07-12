#!/usr/bin/env python3
"""
PostToolUse Hook: 监测 Write/Edit 操作，自动追踪活跃 plan 步骤完成

关键约束
--------
- exit 0 = 放行（默认）；本 hook 永不拦截（exit 2 会破坏所有 Write/Edit）
- 只观察 + 辅助，匹配到则自动 step_complete，匹配失败仅打 stderr
- plan_tracker 不可用/未配置 → 静默放行，不影响主流程

匹配规则
--------
- 对每个活跃 plan 的每个未完成 step：
  * 若 step.expected_outputs 中某个路径与当前写入文件路径匹配 →
    自动调用 step_complete(plan_id, n, output=file_path)
- 路径匹配采用归一化（/ 替换 \）+ 后缀匹配 + 子串匹配（双向兜底）

附加功能（self-correct injection）
--------
- 60 分钟未完成的 plan 输出 stderr 警告（不拦截）
- auto-step 成功后追加「下一步提示」—— 把 next expected_outputs
  反馈给 agent context，形成 self-correct 链式引导
- 停滞检测：plan 已 in_progress 但 >10 分钟无任何 step 完成 →
  输出 drift 提示（不拦截，仅 stderr 提醒 agent 检查）
"""
import json
import sys
import os
import time
from pathlib import Path
from datetime import datetime

# 复用 plan_tracker.py 的核心函数（容错导入）
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from plan_tracker import find_active_plans, step_complete  # type: ignore
except Exception:
    # plan_tracker 模块缺失或损坏：直接放行
    sys.exit(0)


def _normalize(p: str) -> str:
    """路径归一化：反斜杠转正斜杠 + 去除多余分隔符。"""
    if not p:
        return ""
    return p.replace("\\", "/").strip()


def _path_matches(file_path: str, expected: str) -> bool:
    """判断写入文件是否匹配 expected_output。

    规则（任一命中即匹配）：
    1. 归一化后双向后缀匹配（避免绝对/相对路径差异）
    2. expected 是 file_path 的子串
    3. file_path 是 expected 的子串（防止 expected 比实际更长的情况）
    """
    if not file_path or not expected:
        return False
    fp = _normalize(file_path).lower()
    exp = _normalize(expected).lower()
    if not exp:
        return False
    # 后缀匹配
    if fp.endswith(exp) or exp.endswith(fp):
        return True
    # 子串匹配（双向）
    if exp in fp or fp in exp:
        return True
    return False


def _cooldown_ok(plan_id: str, label: str, cooldown_min: int = 10) -> bool:
    """冷却去重：同一 plan + 同一 label 10 分钟内只打印一次。"""
    tmp = Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp")))
    marker = tmp / f"plan_hook_{plan_id}_{label}.ts"
    now = time.time()
    if marker.exists():
        try:
            last = float(marker.read_text().strip())
            if now - last < cooldown_min * 60:
                return False  # 冷却中，不打印
        except Exception:
            pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now))
    except Exception:
        pass
    return True


def _check_timeout(plan: dict) -> None:
    """超过 60 分钟未完成的 plan 打 stderr 警告（同 plan 10 分钟冷却）。"""
    started = plan.get("started_at") or plan.get("created_at")
    if not started:
        return
    try:
        started_dt = datetime.fromisoformat(started.split("+")[0].split("Z")[0])
        age_min = (datetime.now() - started_dt).total_seconds() / 60
        if age_min > 60:
            plan_id = plan.get("id", "unknown")
            if _cooldown_ok(plan_id, "timeout"):
                print(f"⚠️ [Plan] {plan_id} 已开启 {age_min:.0f}min 未完成",
                      file=sys.stderr)
    except Exception:
        return


def _check_drift(plan: dict, threshold_min: int = 10) -> None:
    """停滞检测（同 plan 10 分钟冷却去重）。"""
    last_progress = None
    pending_step_names = []
    for s in plan.get("steps", []):
        st = s.get("status")
        if st == "completed":
            ts = s.get("completed_at") or s.get("started_at")
            if ts and (last_progress is None or ts > last_progress):
                last_progress = ts
        elif s.get("required"):
            pending_step_names.append(f"step {s.get('n')}")
    if not pending_step_names:
        return
    base = last_progress or plan.get("started_at") or plan.get("created_at")
    if not base:
        return
    try:
        base_dt = datetime.fromisoformat(base.split("+")[0].split("Z")[0])
        idle_min = (datetime.now() - base_dt).total_seconds() / 60
        if idle_min < threshold_min:
            return
        if last_progress is None and idle_min < 30:
            return
        plan_id = plan.get("id", "unknown")
        if _cooldown_ok(plan_id, "drift"):
            print(f"📋 [Plan-drift] {plan_id} 停滞 {idle_min:.0f}min，"
                  f"待: {', '.join(pending_step_names[:3])}",
                  file=sys.stderr)
    except Exception:
        return


def _next_step_hint(plan: dict, just_completed_n) -> str:
    """找到刚完成 step 的下一个 required 未完成 step，返回提示字符串。"""
    steps = plan.get("steps", [])
    completed_set = {str(s.get("n")) for s in steps if s.get("status") == "completed"}
    # 找下一个 required 未完成 step
    for s in steps:
        n = s.get("n")
        if str(n) in completed_set:
            continue
        if not s.get("required"):
            continue
        # 取该步 expected_outputs（前 2 个）
        exp = (s.get("expected_outputs") or [])[:2]
        exp_hint = "; ".join(exp) if exp else "(无 expected_outputs)"
        return f"下一步: step {n} ({s.get('name')}), 期望输出: {exp_hint}"
    return ""  # 没有后续 required step


def main():
    # stdin 必须按 bytes 读、交 json.loads 自动 UTF-8 解码：
    # 文本模式在 GBK 控制台会把 UTF-8 载荷读花（与 pretooluse_agent_gate 同约束）。
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        sys.exit(0)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        sys.exit(0)

    tool = data.get("tool_name", "")
    if tool not in ("Write", "Edit"):
        sys.exit(0)  # 只关心 Write/Edit

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    # 扫描所有活跃 plan（容错：异常 = 空列表）
    try:
        active_plans = find_active_plans()
    except Exception as exc:
        print(f"⚠️ [Plan] find_active_plans 异常: {exc}", file=sys.stderr)
        sys.exit(0)

    if not active_plans:
        sys.exit(0)

    for entry in active_plans:
        plan = entry.get("plan", {})
        plan_id = plan.get("id")
        if not plan_id:
            continue
        steps = plan.get("steps", [])
        for step in steps:
            if step.get("status") == "completed":
                continue
            expected = step.get("expected_outputs", []) or []
            matched = False
            for exp in expected:
                if _path_matches(file_path, exp):
                    matched = True
                    break
            if matched:
                # 自动标记 step（skip_output=True：不强制再校验，因为写入正在发生）
                try:
                    step_complete(plan_id, step["n"], output=None, skip_output=True)
                    msg = (f"📋 [Plan] {plan_id} step {step.get('n')} "
                           f"({step.get('name')}) auto-completed")
                    # self-correct: 追加「下一步提示」给 agent context
                    # 重新读取 plan（因为 step_complete 改了文件），以拿到最新状态
                    try:
                        from plan_tracker import get_plan  # type: ignore
                        latest = get_plan(plan_id)
                        hint = _next_step_hint(latest, step["n"])
                        if hint:
                            msg = f"{msg}\n   ↪ {hint}"
                    except Exception:
                        pass  # 取下一步提示失败不影响主流程
                    print(msg, file=sys.stderr)
                except Exception as exc:
                    print(f"⚠️ [Plan] auto-step failed for {plan_id} step {step.get('n')}: {exc}",
                          file=sys.stderr)

        # 超时检测（每个 plan 独立检查）
        _check_timeout(plan)
        # 停滞检测：plan 进度卡死的 self-correct 提示
        _check_drift(plan)

    sys.exit(0)  # 永远不拦截


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)  # PostToolUse 观察层：永远 exit 0（对齐 runtime_monitor/step_reflection·防未捕异常打断主流水线）
