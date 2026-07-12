#!/usr/bin/env python3
"""PreToolUse Hook: 新书项目全系统强制开启门禁（用户硬规则）

本 hook 校验的 34 个 JSON 文件包含 cluster schema 字段（cluster_blueprint /
故事块摘要 等）。outline 早期 scaffold 步只查存在性，最终 plan-end/end 追加
--content 等价载荷验收，防止空货架进入写作主链。
hook 不感知 cluster vs chapter——它只看「文件是否存在」。

触发条件：Bash 工具调用，命令含 `plan_tracker.py` 且关联 outline plan（命令名=outline 或 plan_id 含 outline）

逻辑：
1. 解析 plan_id（end / step --n 4 / 任何 outline plan 操作）
2. 从 plan JSON 取 project_root
3. 检查 `<project>/_数据库/` 是否含全部 34 个必建 JSON
4. outline plan-end/end 追加 content_check=True，载荷空也 exit 2
5. 全齐且内容合格 → exit 0；否则 exit 2 阻断 + 列出修复建议

**新书默认全系统开启规则**（feedback-default-all-subsystems-enabled-for-new-books）：
ECAS / 世界演化 / Hub / Clock / Storyteller / Stress / character_arc / 群像 / 事件池 / NPC 行动表 / 角色池烙印 /
knowledge_graph / subplot / beat_map / 四线脉络 / webnovel_bench —— 全部默认开

【约束】
- exit 0 = 放行 / exit 2 = 拒绝
- 只在 outline plan 关联命令拦（cluster-write / cluster-save-state 不拦——那些时点 manifest 已经判过）
- outline 相关 plan 找不到 / 解析失败 / 项目数据库目录不存在 → exit 2
"""
import json
import os
import re
import sys
from pathlib import Path

# 判定逻辑 + 34 必建 JSON 清单在共享库 plan_step_gates（单一真相源）。
# 本 hook 是薄 wrapper：解析 stdin → 算 db_dir → 调 check_subsystems → ok?exit0:exit2。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_subsystems, ALL_REQUIRED  # noqa: E402,F401


def main():
    try:
        # stdin 按 bytes 读·json 自动 UTF-8 解码（文本模式在 GBK 控制台会把载荷读花）
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")

    # 只钩 plan_tracker 操作（end / step）
    if "plan_tracker" not in command:
        sys.exit(0)

    # 必须是 outline 关联 plan：plan_id 含 "outline" 子串
    m = re.search(r"plan_tracker\.py\s+(end|step|abort|status)\s+[\"']?([\w\-]+)[\"']?", command)
    if not m:
        sys.exit(0)

    op, plan_id = m.group(1), m.group(2)

    # 只检查 outline plan（plan_id 含 _outline_）
    if "_outline_" not in plan_id:
        sys.exit(0)

    # 只对 end 和 step 3/4/7 拦（outline plan：scaffold=step 4 · plan-end=step 7）。
    # content check 只在最终校验阶段（plan-end / end）启用，避免刚建空骨架时误拦。
    # cluster-save-state / cluster-write 等其他命令 plan 完全放行
    step_n = None
    content_check = (op == "end")
    if op == "step":
        n_match = re.search(r"--n\s+(\d+)", command)
        if not n_match:
            sys.exit(0)
        step_n = int(n_match.group(1))
        if step_n not in (3, 4, 7):
            sys.exit(0)

    # 找 plan JSON 取 project
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    candidates = [
        project_dir / "core" / "claude-home" / ".plans" / f"{plan_id}.json",
        project_dir / "_数据库" / ".plans" / f"{plan_id}.json",
        project_dir / ".plans" / f"{plan_id}.json",
        *list(project_dir.glob(f"workspace/**/_数据库/.plans/{plan_id}.json")),
        *list(project_dir.glob(f"workspace/**/.plans/{plan_id}.json")),
    ]
    plan_file = next((c for c in candidates if c.exists()), None)
    if not plan_file:
        print(f"❌ [subsystems_gate] 找不到 outline plan: {plan_id}", file=sys.stderr)
        sys.exit(2)

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"❌ [subsystems_gate] outline plan 解析失败: {plan_file}: {exc}", file=sys.stderr)
        sys.exit(2)

    project_name = plan.get("project", "")
    if not project_name:
        print(f"❌ [subsystems_gate] outline plan 缺 project 字段: {plan_file}", file=sys.stderr)
        sys.exit(2)

    if op == "step" and step_n is not None:
        target_step = next((s for s in plan.get("steps", []) if s.get("n") == step_n), {})
        step_name = str(target_step.get("name", "")).lower()
        content_check = "plan-end" in step_name or step_n == 7

    # 推算 project _数据库/ 路径
    db_candidates = [
        project_dir / "workspace" / "novels" / project_name / "_数据库",
        project_dir / "workspace" / "styles" / project_name / "_数据库",
        plan_file.parent.parent,  # plan 文件在 _数据库/.plans/，往上两级回到 _数据库/
    ]
    db_dir = next((d for d in db_candidates if d.exists() and d.is_dir()), None)
    if not db_dir:
        print(f"❌ [subsystems_gate] 找不到项目数据库目录: {project_name}", file=sys.stderr)
        sys.exit(2)

    # 判定走 check_subsystems（34 文件清单 + plan-end 载荷内容）。
    result = check_subsystems(db_dir, content_check=content_check)
    if result["ok"]:
        sys.exit(0)

    # 缺失 → exit 2 阻断
    print(f"❌ [subsystems_gate] {project_name}: {result['msg']}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
