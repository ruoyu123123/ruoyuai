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


# 34 个必建 JSON（基础 18 + 高级 16）
REQUIRED_DB_FILES = {
    # 基础 18 个（v23 出来时已经在 init-13-databases 中)
    "基础-人物世界": [
        "人物卡.json", "世界观.json", "关系.json", "地图.json", "道具.json",
    ],
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 校验 故事块摘要.json
    "基础-叙事": [
        "进度.json", "故事块摘要.json", "大势卡.json", "事件簇.json", "事件表.json", "时间线.json", "伏笔表.json",
    ],
    "基础-风格质控": [
        "作者风格.json", "场景规则.json", "写作经验.json", "用户偏好.json",
    ],
    "基础-世界演化（v20.1 R 系列）": [
        "世界状态.json", "涟漪规则.json",
    ],
    # 高级 16 个（v21+ R 系列 + v22 SE3 蒸馏 + v23 fluid）
    "高级-Hub/Clock/Storyteller/Stress（v21 R 系列）": [
        "枢纽场景.json", "时钟表.json", "叙事节拍器.json", "主角压力档.json",
    ],
    "高级-角色弧线 + NPC 动态（v21+）": [
        "character_arc_state.json", "角色行动表.json", "群像档.json",
    ],
    "高级-fluid 事件池 + 行动判定": [
        "事件池.json", "行动判定模板.json",
    ],
    "高级-v22 SE3 蒸馏专用（角色蒸馏 + 烙印）": [
        "角色池.json", "角色烙印.json",
    ],
    "高级-v23 长篇叙事工具": [
        "knowledge_graph.json", "subplot_threads.json", "beat_map.json", "四线脉络.json", "webnovel_bench_mapping.json",
    ],
}

# 展开为单一清单
ALL_REQUIRED = []
for category, files in REQUIRED_DB_FILES.items():
    ALL_REQUIRED.extend(files)


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

    # 旁路：用户说"轻量模式"建一个 .subsystems_bypass.json 即跳过
    bypass = db_dir / ".subsystems_bypass.json"
    if bypass.exists():
        sys.exit(0)

    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 校验 故事块摘要.json
    missing = [f for f in ALL_REQUIRED if not (db_dir / f).exists()]
    if not missing:
        sys.exit(0)  # 全齐放行

    # 缺失 → exit 2 阻断
    print(f"❌ [Hook subsystems_gate] 新书全系统强制开启检测失败", file=sys.stderr)
    print(f"   项目: {project_name}", file=sys.stderr)
    print(f"   缺失 {len(missing)}/{len(ALL_REQUIRED)} 个必建 JSON:", file=sys.stderr)
    for category, files in REQUIRED_DB_FILES.items():
        cat_missing = [f for f in files if f in missing]
        if cat_missing:
            print(f"     [{category}]", file=sys.stderr)
            for f in cat_missing:
                print(f"       - {f}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"   📝 解决方案（feedback-default-all-subsystems-enabled-for-new-books）:", file=sys.stderr)
    print(f"     A.（推荐·一条命令）python core/scripts/scaffold_subsystems.py emit \"{project_name}\"", file=sys.stderr)
    print(f"        → 生成 {len(ALL_REQUIRED)} 个 schema 正确空骨架（不覆盖已填），再填创意内容；", file=sys.stderr)
    print(f"        框架: core/claude-home/templates/subsystem_skeletons.json · 示例: templates/examples/（含 _subsystem_examples/）", file=sys.stderr)
    print(f"        填完跑 verify: python core/scripts/scaffold_subsystems.py verify \"{project_name}\"", file=sys.stderr)
    print(f"     B. 用户明确要轻量模式 → touch {bypass} 即可跳过（项目级 opt-out）", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
