#!/usr/bin/env python3
"""PostToolUse Hook: 观察 Write/Edit 产物与活跃 plan 的对应关系。

关键约束
--------
- exit 0 = 放行（永不拦截；exit 2 会打断所有 Write/Edit）
- 只观察 + 提示；**绝不越权替主代理写 plan 状态**
- plan_tracker 不可用/未配置 → 静默放行

匹配规则（精确文件级）
--------
写入路径与 step.expected_outputs 的匹配只认「归一化后完全相等」或「按 `/` 边界对齐的
路径后缀」。禁止目录前缀命中与子串命中：`_数据库/.wal` 这类目录形态的 expected 不会被
`_数据库/.wal/xxx.json` 的写入命中，同一次写入也不会同时点亮无关 step。

step 状态写入纪律
--------
- required 且 `skip_output_allowed != true` 的 step（= 全部主链 step）：hook **一律不自动完成**，
  只打「产物已落盘，请主代理跑 plan_tracker step --n N 收口」提示。与 PreToolUse 防跳步守卫
  (`pretooluse_plan_step_anti_skip.py`) 同一口径——前门拦跳步，后门不得放行。
- 其余 step（未标 required 或模板显式 `skip_output_allowed: true`）：先自查 expected_outputs
  全部落盘，再走**正常** `step_complete`（不传 skip_output，不跳校验）；任何一个产物缺失
  或 step_complete 抛错 → 只打 stderr 提示，不写 plan 状态。

附加观察
--------
- 60 分钟未完成的 plan 打 stderr 警告（不拦截）
- 命中后追加「下一步提示」：把 next required step 的 expected_outputs 回灌 agent context
- 停滞检测：plan in_progress 但 >10 分钟无 step 完成 → drift 提示（不拦截）
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
    from plan_tracker import (  # type: ignore
        find_active_plans, step_complete, resolve_project_root,
    )
except Exception:
    # plan_tracker 模块缺失或损坏：直接放行
    sys.exit(0)


def _normalize(p: str) -> str:
    """路径归一化：反斜杠转正斜杠 + 折叠重复分隔符 + 去尾部分隔符。"""
    if not p:
        return ""
    out = str(p).replace("\\", "/").strip()
    while "//" in out:
        out = out.replace("//", "/")
    while out.endswith("/") and len(out) > 1:
        out = out[:-1]
    return out


def _is_dir_shaped(expected: str) -> bool:
    """expected 是否目录形态（尾部 `/` 或 `\\`）。目录形态一律不参与匹配。"""
    raw = str(expected or "").strip()
    return raw.endswith("/") or raw.endswith("\\")


def _path_matches(file_path: str, expected: str) -> bool:
    """写入文件是否**精确**命中 expected_output（文件级，不做目录前缀/子串匹配）。

    命中条件（归一化 + 大小写不敏感，Windows 路径语义）：
      1. 完全相等；
      2. 写入路径以 `/` 边界对齐的方式以 expected 结尾（expected 为项目相对路径）；
      3. expected 以 `/` 边界对齐的方式以写入路径结尾（expected 为绝对路径的兜底）。

    `_数据库/.wal`（目录）绝不会被 `_数据库/.wal/cluster_001_x.json`（文件）命中——
    任何写入 .wal/ 的文件都点亮 wal-end step 正是被根治的越权 bug。
    """
    if not file_path or not expected:
        return False
    if _is_dir_shaped(expected):
        return False
    fp = _normalize(file_path).lower()
    exp = _normalize(expected).lower()
    if not fp or not exp:
        return False
    if fp == exp:
        return True
    if fp.endswith("/" + exp):
        return True
    if exp.endswith("/" + fp):
        return True
    return False


def _is_locked_step(step: dict) -> bool:
    """required 且未显式允许 skip_output → hook 不得自动完成（尊重防跳步守卫）。"""
    if not step.get("required"):
        return False
    return step.get("skip_output_allowed") is not True


def _missing_outputs(plan: dict, step: dict) -> list:
    """自查 expected_outputs 落盘情况（相对路径按 plan.project 解析）。"""
    expected = step.get("expected_outputs") or []
    try:
        root = resolve_project_root(plan.get("project")) if plan.get("project") else None
    except Exception:
        root = None
    missing = []
    for raw in expected:
        if not raw:
            continue
        p = Path(str(raw))
        if not p.is_absolute() and root is not None:
            p = root / str(raw)
        try:
            if not p.exists():
                missing.append(str(raw))
        except OSError:
            missing.append(str(raw))
    return missing


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


def _next_step_hint(plan: dict) -> str:
    """下一个 required 未完成 step 的提示字符串（含其 expected_outputs 前 2 个）。"""
    for s in plan.get("steps", []):
        if s.get("status") == "completed":
            continue
        if not s.get("required"):
            continue
        exp = (s.get("expected_outputs") or [])[:2]
        exp_hint = "; ".join(exp) if exp else "(无 expected_outputs)"
        return f"下一步: step {s.get('n')} ({s.get('name')}), 期望输出: {exp_hint}"
    return ""  # 没有后续 required step


def _observe_step(plan: dict, plan_id: str, step: dict, file_path: str) -> None:
    """一个 step 被写入命中后的处理：主链 step 只提示，非主链 step 走正常 step_complete。"""
    n = step.get("n")
    name = step.get("name")
    if _is_locked_step(step):
        # 主链 required step：状态由主代理跑 plan_tracker step 收口（走完整 expected_outputs 校验）
        print(f"📋 [Plan] {plan_id} step {n} ({name}) 的 expected_output 已落盘: {file_path}\n"
              f"   ↪ hook 不代跑收口：请主代理执行 "
              f"`python core/scripts/plan_tracker.py step {plan_id} --n {n}`",
              file=sys.stderr)
        return

    missing = _missing_outputs(plan, step)
    if missing:
        print(f"📋 [Plan] {plan_id} step {n} ({name}) 产物未齐，未自动收口。缺: {missing[:3]}",
              file=sys.stderr)
        return

    try:
        # 正常 step_complete（不传 skip_output → expected_outputs 全量校验照跑）
        step_complete(plan_id, n, output=None)
    except Exception as exc:
        print(f"⚠️ [Plan] {plan_id} step {n} 自动收口失败（未改状态）: {exc}",
              file=sys.stderr)
        return

    msg = f"📋 [Plan] {plan_id} step {n} ({name}) auto-completed"
    try:
        from plan_tracker import get_plan  # type: ignore
        hint = _next_step_hint(get_plan(plan_id))
        if hint:
            msg = f"{msg}\n   ↪ {hint}"
    except Exception:
        pass  # 取下一步提示失败不影响主流程
    print(msg, file=sys.stderr)


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
    if not isinstance(data, dict):
        sys.exit(0)

    tool = data.get("tool_name", "")
    if tool not in ("Write", "Edit"):
        sys.exit(0)  # 只关心 Write/Edit

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)
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
        for step in plan.get("steps", []):
            if step.get("status") == "completed":
                continue
            expected = step.get("expected_outputs", []) or []
            if not any(_path_matches(file_path, exp) for exp in expected):
                continue
            _observe_step(plan, plan_id, step, file_path)

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
