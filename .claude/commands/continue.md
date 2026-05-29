---
description: 续写/断点恢复（从中断处继续写作）
---

你是若渝AI，执行 cluster 级断点恢复和续写流程：

$ARGUMENTS

---

# /continue 断点恢复流程（v26 · cluster mode）

> 🔴 v26：chapter mode（`/write-chapter` / `/save-state`）整套已删除。续写一律以**故事块（cluster）**为粒度恢复，用 `/cluster-write` / `/cluster-save-state` 续跑。无降级、无单章模式。

## 第一步：检测项目状态

1. 扫描 `workspace/novels/{书名}/`，确定项目（含 `_数据库/进度.json`）。
2. **检测 save-state WAL（崩溃恢复优先）**：
   - 检查 `_数据库/.wal/` 是否存在 `cluster_<key>_save_state.json`。
   - 如存在 → Read 它，取 `cluster_key` 与 `completed_steps[]`：
     - 提示用户：「检测到 cluster <key> 的 save-state 中断（完成 X 步 / 共 12 步），正在续跑…」
     - 进入第三步「情况 B：续跑 cluster-save-state」。
   - 如无 → 继续下一步。
3. Read `_数据库/进度.json`，获取：
   - `completed` / `current`：已完成 / 当前进度
   - `cluster_blueprint`：各 cluster 的占位与状态
   - `last_updated`：上次更新时间
4. 检查最近一个 cluster 的草稿与 changes：
   - `章节/cluster_<key>_draft/cluster_<key>_draft.txt`（writer 草稿）
   - `章节/cluster_<key>_draft/cluster_<key>_changes.json`（cluster 级变更）
   - 这两者的存在与否，决定 cluster-write 7 步走到了哪一步。

---

## 第二步：判断中断点（cluster 粒度）

```
中断点判断逻辑（以 cluster <key> 为单位）：

情况 A：cluster_<key>_draft.txt 不存在 / 不完整
  → cluster-write 写作阶段中断（step 1-2 之间）
  → 执行 /cluster-write CLUSTER_ID=<key> 从头重跑（plan WAL 会跳过已完成 step）

情况 A2：cluster_<key>_draft.txt 存在，但章节物理文件（第NNN章/）未切出
  → cluster-write 切章前的质检/伏笔/voice 阶段中断（step 3-5），或 splitter 未跑（step 6）
  → 执行 /cluster-write CLUSTER_ID=<key> 续跑（plan_tracker status 显示停在哪步，从下一步继续）

情况 B：存在 .wal/cluster_<key>_save_state.json（completed_steps 未满 12）
  → cluster-save-state 中断（cluster 已写完但状态未存完）
  → 执行 /cluster-save-state CLUSTER_ID=<key> 续跑（跳过 completed_steps 已完成项）

情况 C：cluster <key> 已切章 + save-state WAL 已 end（无残留 wal）
  → 上一个 cluster 完整完成，需要写下一个 cluster
  → 取涌现出的 cluster_<next_key> brief（事件簇.json.clusters[N+1]），执行 /cluster-write

情况 D：无 进度.json 或 _数据库 目录
  → 项目未初始化，提示用户执行 /write 或 /outline
```

---

## 第三步：恢复执行

### 情况 A / A2 — 续跑 cluster-write

1. 输出提示：「cluster <key> 写作中断，正在续跑 7 步流水线…」
2. 先 `python core/scripts/plan_tracker.py status <plan_id>` 确认停在哪步（或 list 找活跃 plan）。
3. 执行 `/cluster-write CLUSTER_ID=<key>`：调度器按 plan 续跑，已完成 step 不重做。
4. cluster 切章完成后，继续走情况 B 的 `/cluster-save-state`。

### 情况 B — 续跑 cluster-save-state

1. 输出提示：「cluster <key> 已写完但状态未保存完，正在补存 12 步…」
2. 执行 `/cluster-save-state CLUSTER_ID=<key>`：从 WAL `completed_steps` 之后续跑，跑完 `wal-end` 删 WAL。
3. step 11 涌现下个 cluster brief → 展示走向卡 → 等用户选择。

### 情况 C — 写下一个 cluster

1. 输出提示：「上次写完 cluster <key>，继续下一个故事块～」
2. 取已涌现的 `cluster_<next_key>` brief（cluster_002+ 走向卡前先 spawn novel-researcher）。
3. 执行 `/cluster-write CLUSTER_ID=<next_key>` → `/cluster-save-state CLUSTER_ID=<next_key>` → 走向卡。

### 情况 D — 未初始化

1. 输出提示：「还没开始写呢～先用 /write 新建项目，或者 /outline 生成大纲」
2. 等待用户指令。

---

## 第四步：恢复后进入正常循环

断点恢复后，自动进入 /write 第五步的**逐故事块循环**（cluster-write → cluster-save-state → 走向卡），不再额外停顿。

---

## Git 状态检查与补提交

恢复流程开始前，先检查 Git 仓库状态（如项目已 git init）：

```bash
if command -v git >/dev/null 2>&1 && [ -d "workspace/novels/书名/.git" ]; then
  UNCOMMITTED=$(git -C "workspace/novels/书名" status --porcelain 2>/dev/null)
  if [ -n "$UNCOMMITTED" ]; then
    echo "⚠️ 检测到未提交的变更："
    echo "$UNCOMMITTED"
    git -C "workspace/novels/书名" log -1 --oneline
  fi
fi
```

**处理策略：**

| Git 状态 | WAL 状态 | 判断 | 处理 |
|----------|----------|------|------|
| 工作区干净 | 无 wal | 上次完整结束 | 正常继续写下一个 cluster（情况 C） |
| 有未提交章节/草稿 | 无 cluster_<key>_save_state.json wal | cluster-save-state 漏了 commit | 续跑 cluster-save-state（step 10 git-commit-cluster） |
| 有未提交章节/草稿 | 有 cluster_<key>_save_state.json wal | cluster-save-state 中断 | 从 WAL completed_steps 续跑（情况 B） |
| 工作区干净 | 有残留 cluster_<key>_save_state.json wal | WAL 元数据未清理 | 清理 wal 文件，正常继续 |
| 有 .bak 文件残留 | 无 wal | 调和被中断 | 提示用户手动检查 reconcile 状态 |

**关键原则：**
- 不自动 `git reset`/`git checkout` 抹掉用户的修改
- 不自动提交未知来源的变更（只 commit 流水线产出的文件）
- 如有异常状态，优先向用户报告，让用户判断

---

## WAL 损坏兜底

与 cluster-save-state.md「失败逃生舱」对齐：
- 优先从 `cluster_<key>_save_state.json` 的 `completed_steps` 跳过已完成步骤续跑。
- 若 WAL 损坏 → `python core/scripts/wal_recovery.py "<项目路径>" --cluster <key>`。

---

## 状态恢复报告

恢复时输出：

```
📖 恢复写作 — [书名]

  进度：已完成 N 个 cluster
  上次更新：[日期]
  中断点：[情况 A/A2/B/C 的描述 + cluster <key>]
  恢复操作：[即将执行的命令：/cluster-write 或 /cluster-save-state]

继续写作中...
```

---

## 硬性规则

- ⚠️ 恢复后的写作流程与 /write 完全一致（cluster-write → cluster-save-state → 走向卡），禁止主会话直接生成正文。
- 🔴 不降级到 chapter mode：`/write-chapter` / `/save-state` 已整套删除，没有单章续写这回事。
- 不要问用户「要从哪里开始」——自动检测中断点。
- 不要重复已完成的工作（cluster 已切章就不重写，plan WAL / save-state WAL 已记录的步骤跳过）。
- 如果检测到数据不一致（如草稿存在但进度显示未完成），以文件实际存在为准。
