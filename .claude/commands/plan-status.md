---
description: 查看 plan 强制规划状态（活跃/历史/详情）
---

你是一位 plan 追踪助手。请按下方协议执行 plan 状态查询：

$ARGUMENTS

---

# Plan 状态查询

依托 `core/scripts/plan_tracker.py`，向用户展示当前活跃 plan 与历史 plan 概览。

## 参数路由

| 调用形式 | 行为 |
|---------|------|
| `/plan-status` | 列出**活跃** plan（默认） |
| `/plan-status --all` | 列出**全部** plan（含 DONE / ABORT） |
| `/plan-status <plan_id>` | 显示指定 plan 的**完整步骤详情** |

`<plan_id>` 支持前缀匹配（≥12 字符的尾段唯一即可）。

---

## 第一步：参数解析

1. Bash 读取 `$ARGUMENTS`：
   - 为空 → 模式 A（活跃列表）
   - `--all` → 模式 B（全量列表）
   - 其他字符串 → 模式 C（详情查询，把 `$ARGUMENTS` 当 plan_id 处理）

2. 工作目录预检：
   ```bash
   command -v python >/dev/null 2>&1 || { echo "[ERR] python 不可用"; exit 1; }
   test -f <REPO_ROOT>/core/scripts/plan_tracker.py || { echo "[ERR] plan_tracker.py 缺失"; exit 1; }
   ```

---

## 第二步：执行查询

### 模式 A · 活跃列表（默认）

```bash
python <REPO_ROOT>/core/scripts/plan_tracker.py list --active
```

### 模式 B · 全量列表（--all）

```bash
python <REPO_ROOT>/core/scripts/plan_tracker.py list
```

### 模式 C · 详情查询（plan_id）

```bash
python <REPO_ROOT>/core/scripts/plan_tracker.py status "<plan_id>"
```

---

## 第三步：格式化输出

### 列表视图（模式 A / B）

把 `list` 的输出**重排为表格**，列：

| 列 | 说明 | 取值示例 |
|----|------|---------|
| flag | 状态标记 | `🟢 ACTIVE` / `✅ DONE` / `🚫 ABORT` |
| plan_id (尾段) | 去掉 `project_key_command_` 前缀，仅显示时间戳 | `20260513T214530123` |
| command | 命令名 | `save-state` |
| project | 项目/风格名 | `BookB` |
| chapter | 章节号（无则 `-`） | `42` |
| age | 距 `started_at` 的分钟数 | `12 min` |

**`age` 计算**：读取 `runtime_plans_dir` 下的 plan.json，取 `started_at`，与当前时间差。

**⚠️ 超时警示规则**：
- 活跃 plan 且 `age > 60 min` → 在 flag 列追加 `⚠️ STALE`
- 提示用户：「此 plan 已 N 分钟未结束。常见原因：(a) Agent 中途崩溃未 abort；(b) 主代理忘记调 `end`。建议 `/plan-status <id>` 查看详情或手动 `abort`。」

输出示例：

```
📋 活跃 Plan 列表（共 2 个）

| 状态 | plan_id 尾段     | command      | project          | chapter | age      |
|------|------------------|--------------|------------------|---------|----------|
| 🟢   | T214530123       | save-state   | BookB   | 42      | 3 min    |
| 🟢⚠️ | T203012001       | distill-style| 某书             | -       | 78 min   |

⚠️ STALE plan 检测到：T203012001 已 78 分钟未结束。
   操作建议：python plan_tracker.py status <完整id>  /  abort <完整id> --reason "..."
```

---

### 详情视图（模式 C）

把 `status` 的输出**增强为结构化报告**：

```
🧭 Plan 详情

ID       : BookB_ch42_save-state_20260513T214530123
Command  : save-state
Project  : BookB
Chapter  : 42
Progress : 8/12 (66%)
Started  : 2026-05-13 21:45:30
Age      : 12 min

步骤清单：
┌──┬──────────────────────────────┬────────────┬─────────────────────────┐
│ #│ 步骤名                       │ 状态       │ verified_outputs        │
├──┼──────────────────────────────┼────────────┼─────────────────────────┤
│ 1│ load_context                 │ ✅ 完成    │ (skip-output)           │
│ 2│ extract_voice_dna            │ ✅ 完成    │ voice_dna_ch42.json     │
│ 3│ lock_facts                   │ ✅ 完成    │ 人物卡.json (锁定+1)    │
│ ...                                                                   │
│ 9│ git_snapshot                 │ ⏳ 进行中  │ -                       │
│10│ render_card                  │ ⏸ 待执行  │ -                       │
│11│ wal_finalize                 │ ⏸ 待执行  │ -                       │
│12│ end_plan                     │ ⏸ 待执行  │ -                       │
└──┴──────────────────────────────┴────────────┴─────────────────────────┘

状态图标：✅ completed | ⏳ in_progress | ⏸ pending | ❌ failed | ⏭ skipped | 🚫 aborted

⚠️ 失败步骤详情（若有）：
   - 第 N 步「<name>」：<error 字段>
```

**字段映射**：
- `[x]` → `✅ 完成`
- `[~]` → `⏳ 进行中`
- `[!]` → `❌ 失败`
- `[-]` → `⏭ 跳过`
- `[A]` → `🚫 中止`
- `[ ]` → `⏸ 待执行`

**输出验证显示**：
- 步骤 `verified_outputs` 为空 → 显示 `-`（待完成）或 `(skip-output)`（已跳过校验）
- 多个输出 → 用 `;` 分隔，超过 2 个时显示 `<首项> 等 N 个`

---

## 第四步：摘要 + 建议

每次查询末尾输出：

```
─────────────────────────────────────────
📊 摘要
- 总 plan 数：N（活跃 X / 完成 Y / 中止 Z）
- 涉及命令：save-state(3) distill-style(1) ...
- 涉及项目：BookB(2) ...

💡 建议
- 若有 STALE → 建议 abort 或继续完成
- 若有 FAILED 步骤 → 查看 error 字段、修复后重跑命令（plan_tracker 会幂等）
- 若全部 DONE → 无需操作
```

---

## 错误处理

| 场景 | 行为 |
|------|------|
| `plan_tracker.py` 不存在 | 报错并提示基建未部署 |
| plan_id 找不到 | 提示用户先 `/plan-status` 列表确认 ID |
| plan_id 多于一个前缀匹配 | 列出候选要求用户给完整 id |
| 全局/项目目录都无 plan | 输出 `(no plans)` 友好提示，不报错 |

---

## 设计原则

- **只读不改**：本命令绝不修改任何 plan 状态。如需 abort，引导用户直接 `python plan_tracker.py abort <id> --reason "..."`
- **容错优先**：plan_tracker 内部已抗损坏 JSON / 缺失目录；本命令再加一层 try/except，永不让用户看到 traceback
- **绝对路径**：所有 Bash 命令用 `<REPO_ROOT>/...` 绝对路径，不依赖 `cd`
- **路径权威**：本命令产出无文件（纯查询），无路径冲突风险

---

**关联文档**：
- `core/scripts/plan_tracker.py` — 底层 API
- `core/claude-home/plans/*.plan.json` — 6 个命令模板
- `core/claude-home/lessons/distill-style-lessons.md` §八 — Plan 教训
- `CLAUDE.md` 「Plan 强制规划」章节 — 顶层契约说明
