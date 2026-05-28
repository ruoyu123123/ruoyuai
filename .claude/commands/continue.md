---
description: 续写/断点恢复（从中断处继续写作）
---

你是若渝AI，执行断点恢复和续写流程：

$ARGUMENTS

---

# /continue 断点恢复流程

## 第一步：检测项目状态

1. 扫描当前目录下的 `小说_*` 文件夹，确定项目
2. **检测 WAL 预写日志（崩溃恢复优先）**：
   - 检查 `_数据库/.wal/` 目录是否存在 `status: "in_progress"` 的文件
   - 如存在 → 优先处理崩溃恢复：
     - Read 该 wal 文件，获取章节号 N 和 completed_steps
     - 提示用户：「检测到第N章 save-state 中断（完成X步/共11步），正在续跑...」
     - 从 completed_steps 之后的步骤继续执行 save-state
     - 所有步骤完成后删除 wal 文件
   - 如无 → 继续下一步
3. Read `_数据库/进度.json`，获取：
   - `completed`：已完成章数
   - `current`：当前应写的章节号
   - `total_chapters`：总章数（可能已过时，以剧情为准）
   - `last_updated`：上次更新时间
4. 检查最后一章 txt 文件是否存在：
   - 存在 → 说明章节已写完，可能是 save-state 中断
   - 不存在 → 说明写作过程中断

---

## 第二步：判断中断点

```
中断点判断逻辑：

情况A：第N章.txt 不存在，current = N
  → 写作中断，需要重新写第N章
  → 从第一步开始：执行 /write-chapter

情况B：第N章.txt 存在，但 故事块摘要.json 中无第N章记录
  → save-state 中断（章节写完但状态未保存）
  → 从 save-state 开始：执行 /save-state

情况C：第N章.txt 存在，故事块摘要.json 有第N章记录，current = N+1
  → 上一章完整完成，需要写下一章
  → 直接写第 N+1 章

情况D：无 进度.json 或 _数据库 目录
  → 项目未初始化，提示用户执行 /write 或 /outline
```

---

## 第三步：恢复执行

### 情况A — 重写章节

1. Read 进度.json 的 cluster_blueprint[current]，获取本章规划
2. 执行 /write-chapter 流程（通过 Agent 子任务）
3. 写完后继续 save-state → 小势卡片 → 下一章

### 情况B — 补执行 save-state

1. 输出提示：「检测到第N章已写完但状态未保存，正在补存...」
2. 执行 /save-state（完整11步流水线）
3. 展示小势卡片 → 等待用户选择 → 继续下一章

### 情况C — 直接写下一章

1. 输出提示：「上次写到第N章，继续写第N+1章～」
2. 生成本章小势（基于大势+上章结局+记忆）
3. 执行 /write-chapter → /save-state → 小势卡片

### 情况D — 未初始化

1. 输出提示：「还没开始写呢～先用 /write 新建项目，或者 /outline 生成大纲」
2. 等待用户指令

---

## 第四步：恢复后进入正常循环

断点恢复后，自动进入 /write 的第五步逐章循环，不再停顿。

---

## Git 状态检查与补提交

恢复流程开始前，先检查 Git 仓库状态（如项目已 git init）：

```bash
if command -v git >/dev/null 2>&1 && [ -d "小说_书名/.git" ]; then
  # 检查是否有未追踪或未提交的文件
  UNCOMMITTED=$(git -C "小说_书名" status --porcelain 2>/dev/null)
  
  if [ -n "$UNCOMMITTED" ]; then
    echo "⚠️ 检测到未提交的变更："
    echo "$UNCOMMITTED"
    # 输出最近一次 commit，便于判断中断点
    git -C "小说_书名" log -1 --oneline
  fi
fi
```

**处理策略：**

| Git 状态 | WAL 状态 | 判断 | 处理 |
|----------|----------|------|------|
| 工作区干净 | 无 wal | 上次完整结束 | 正常继续写下一章 |
| 有未提交章节txt | 无 wal | save-state 漏了 commit | 执行情况B的 save-state（会触发 第10.5步 commit） |
| 有未提交章节txt | 有 in_progress wal | save-state 中断 | 续跑 save-state，WAL 会引导到第10.5步 |
| 工作区干净 | 有 in_progress wal | WAL 元数据未清理 | 清理 wal 文件，正常继续 |
| 有 .bak 文件残留 | 无 wal | 调和被中断 | 提示用户手动检查 reconcile 状态 |

**关键原则：**
- 不自动 `git reset`/`git checkout` 抹掉用户的修改
- 不自动提交未知来源的变更（只 commit 流水线产出的文件）
- 如有异常状态，优先向用户报告，让用户判断

---

## 状态恢复报告

恢复时输出：

```
📖 恢复写作 — [书名]

  进度：已完成 N/总 章
  上次更新：[日期]
  中断点：[情况A/B/C 的描述]
  恢复操作：[即将执行的操作]

继续写作中...
```

---

## 硬性规则

- ⚠️ 恢复后的写作流程与 /write 完全一致（Agent子任务写章、save-state、小势卡片）
- 不要问用户"要从哪里开始"——自动检测中断点
- 不要重复已完成的工作（如章节已写完就不重写）
- 如果检测到数据不一致（如 txt 存在但进度显示未完成），以文件实际存在为准
