#!/usr/bin/env python3
"""PreToolUse Hook: 新书项目全系统强制开启门禁（v23.13 用户硬规则 2026-05-25）

🔴 opt-out 持久性是【设计如此·非 bug】：项目 `_数据库/.subsystems_bypass.json` 存在即旁路（轻量
模式）。**刻意无自动过期**——本地单用户工具·opt-out 由用户显式创建/删除·若按 mtime 自动失效会在
用户写书中途突然重新拦截（更糟）。要重新启用全系统校验，删该文件即可。

v2 cluster 化（2026-05-28）：本 hook 校验的 34 个 JSON 文件包含 v2 schema 字段
（cluster_blueprint / 故事块摘要 等），逻辑不变·仅文件存在性校验，schema 不挑剔。
hook 不感知 cluster vs chapter——它只看「文件是否存在」。

触发条件：Bash 工具调用，命令含 `plan_tracker.py` 且关联 outline plan（命令名=outline 或 plan_id 含 outline）

逻辑：
1. 解析 plan_id（end / step --n 4 / 任何 outline plan 操作）
2. 从 plan JSON 取 project_root
3. 检查 `<project>/_数据库/` 是否含全部 34 个必建 JSON
4. 全齐 → exit 0 放行
5. 缺 → exit 2 阻断 + 列出缺哪些 + 提示怎么补

**新书默认全系统开启规则**（feedback-default-all-subsystems-enabled-for-new-books）：
ECAS / 世界演化 / Hub / Clock / Storyteller / Stress / character_arc / 群像 / 事件池 / NPC 行动表 / 角色池烙印 /
knowledge_graph / subplot / beat_map / 四线脉络 / webnovel_bench —— 全部默认开

【约束】
- exit 0 = 放行 / exit 2 = 拒绝
- 只在 outline plan 关联命令拦（cluster-write / cluster-save-state 不拦——那些时点 manifest 已经判过）
- plan 找不到 / 解析失败 → 放行（防御性）
- 用户显式说"轻量模式" → 通过 `_数据库/.subsystems_bypass.json` 单文件存在即放行
"""
import json
import os
import re
import sys
from pathlib import Path

# 🔴 2026-06-27 C16：判定逻辑 + 34 必建 JSON 清单抽到共享库 plan_step_gates（北极星⑥消重复）。
# 本 hook 改薄 wrapper：解析 stdin → 算 db_dir → 调 check_subsystems → ok?exit0:exit2。
_SCRIPTS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from plan_step_gates import check_subsystems, ALL_REQUIRED  # noqa: E402,F401


def main():
    try:
        payload = json.loads(sys.stdin.read())
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

    # 只对 end / step --n 3 (init-13-databases) / step --n 4 (plan-end) 拦
    # cluster-save-state / cluster-write / reconcile 等其他命令 plan 完全放行
    if op == "step":
        n_match = re.search(r"--n\s+(\d+)", command)
        if not n_match:
            sys.exit(0)
        n = int(n_match.group(1))
        # step 3 init-13-databases / step 4 plan-end
        if n not in (3, 4):
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
        sys.exit(0)  # 找不到 plan → 放行

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    project_name = plan.get("project", "")
    if not project_name:
        sys.exit(0)

    # 推算 project _数据库/ 路径
    db_candidates = [
        project_dir / "workspace" / "novels" / project_name / "_数据库",
        project_dir / "workspace" / "styles" / project_name / "_数据库",
        plan_file.parent.parent,  # plan 文件在 _数据库/.plans/，往上两级回到 _数据库/
    ]
    db_dir = next((d for d in db_candidates if d.exists() and d.is_dir()), None)
    if not db_dir:
        sys.exit(0)

    # 🔴 C16：判定下沉到 check_subsystems（含 .subsystems_bypass.json 旁路 + 34 文件清单）。
    result = check_subsystems(db_dir)
    if result["ok"]:
        sys.exit(0)

    # 缺失 → exit 2 阻断（保持原 hook exit 语义）
    print(f"❌ [subsystems_gate] {project_name}: {result['msg']}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
