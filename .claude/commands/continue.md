---
description: 续写/断点恢复（从中断处继续写作）
---

你是若渝AI，执行 cluster 级断点恢复和续写流程：

$ARGUMENTS

---

# /continue 断点恢复流程（cluster-only）

> 🔴 续写只以**故事块（cluster）**为粒度恢复；断点只进入 `/cluster-write` 或 `/cluster-save-state` 的 required plan step。

## 第一步：检测项目状态

> 🔴 **断点权威源纪律**：中断判断的**唯一权威源**是
> `wal_recovery.py`（读 `plan_tracker` 持久态）+ `进度.json.completed_clusters`。
> **禁止**把 `.wal/cluster_<key>_save_state.json` 当主检测信号——该文件由调度器
> shell 直建、内容只作为 cluster 级状态佐证，不承载 plan step 进度），且 save-state 成功后**永不删除**（见 cluster-save-state.md
> 完成清单「存在」）。它只能做**辅助佐证**（看 `status` 字段），不能据「文件存在」
> 判中断（否则已完整完成的项目会被永远误判为「save-state 中断」并重跑）。

1. 扫描 `workspace/novels/{书名}/`，确定项目（含 `_数据库/进度.json`）。
2. **跑权威断点检测（plan_tracker 视图）**：
   ```bash
   python core/scripts/wal_recovery.py "workspace/novels/<书名>"
   ```
   - **exit 0** → 无未完成 plan，所有命令流水线都干净结束 → 进入第二步判「写下一个 cluster」（情况 C）。
   - **exit 1** → 有未完成 plan，输出里 `🔴 中断 [active] <cmd>/...: X/Y 步 (<plan_id>)` 即中断点：
     - `<cmd>` = `cluster-write` → 情况 A/A2（写作中断），记下 `<plan_id>` 与 cluster `<key>`。
     - `<cmd>` = `cluster-save-state` → 情况 B（状态保存中断），记下 `<plan_id>` 与 cluster `<key>`。
     - **续跑步号以 wal_recovery 输出的「续跑: ... --n N」为准**（plan_tracker 已算好 done_count+1）。
3. Read `_数据库/进度.json`（**权威进度源**），获取：
   - `completed_clusters[]`：已完整完成的 cluster 列表（**判「写哪个 cluster」的主依据**）
   - `current_cluster`：当前/下一个待写 cluster_id
   - `cluster_blueprint`：各 cluster 的占位与状态（`status` / `_fluid_emergence_pending` / `scope_summary`）
   - `last_updated`：上次更新时间
4. **辅助佐证（不作主判据）**：如对某 cluster 的完成度存疑，可 Read
   `_数据库/.wal/cluster_<key>_save_state.json` 看 `status` 字段（`"done"` = 该 cluster
   save-state 已完整收尾）。**只看 `status`，不看「文件是否存在」**。
5. 检查 `current_cluster`（或 wal_recovery 报中断的 cluster）的草稿与 changes：
   - `章节/cluster_<key>_draft/cluster_<key>_draft.txt`（writer 草稿）
   - `章节/cluster_<key>_draft/cluster_<key>_changes.json`（cluster 级变更）
   - 物理章节目录 `章节/第NNN章/第NNN章.txt`（零填充三位，splitter 切章产物）
   - 这三者的存在与否，进一步区分 cluster-write 7 步走到了哪一步。

---

## 第二步：判断中断点（cluster 粒度）

```
中断点判断逻辑（以 wal_recovery + 进度.json 为权威，cluster <key> 为单位）：

情况 A：wal_recovery 报 cluster-write 中断 且 cluster_<key>_draft.txt 不存在 / 不完整
  → cluster-write 写作阶段中断（step 1-2 之间）
  → 执行 /cluster-write CLUSTER_ID=<key> 从 plan_tracker 记录的恢复点续跑

情况 A2：wal_recovery 报 cluster-write 中断 且 cluster_<key>_draft.txt 存在，
        但本 cluster 章节物理文件（第NNN章/，零填充三位）未切全
  → cluster-write 切章前的质检/伏笔/voice 阶段中断（step 3-5），或 splitter 未跑（step 6）
  → 执行 /cluster-write CLUSTER_ID=<key> 续跑（按 wal_recovery 报的「续跑 --n N」从下一步继续）

情况 B：wal_recovery 报 cluster-save-state 中断（plan required steps 未完成）
  → cluster-save-state 中断（cluster 已写完但状态未存完）
  → 执行 /cluster-save-state CLUSTER_ID=<key> 续跑（按 wal_recovery 报的「续跑 --n N」续）
  → 🔴 不要因「.wal/cluster_<key>_save_state.json 存在」就判此情况——该文件永久保留，
     无法据存在与否区分中断/完成；以 wal_recovery 的 plan 完成态为准。

情况 C：wal_recovery exit 0（无未完成 plan）
  → 上一个 cluster 完整完成，需要写下一个 cluster
  → 取 进度.json.current_cluster（= 第一个不在 completed_clusters 里的 cluster_id）
  → 若该 cluster 的 brief 仍 _fluid_emergence_pending=true / scope_summary="待涌现"
    → 先走 cluster-save-state 已涌现的 brief，或基于事件簇.json.clusters[N+1] 涌现后再写
  → 执行 /cluster-write CLUSTER_ID=<current_cluster>

情况 D：无 进度.json 或 _数据库 目录
  → 项目未初始化，提示用户执行 /write 或 /outline
```

> **交叉校验**：若 wal_recovery exit 1 报中断的 cluster <key> 已在
> `进度.json.completed_clusters` 里 且 `.wal/cluster_<key>_save_state.json.status == "done"`，
> 说明该 plan 是历史残留 active 态（如 save-state plan 已跑完但未盖 end）——按**情况 C** 处理
> （写下一个 cluster），不要重跑已完成的 save-state。可选 `plan_tracker abort <plan_id>`
> 清理该残留 plan 后再继续。

---

## 第三步：恢复执行

### 情况 A / A2 — 续跑 cluster-write

1. 输出提示：「cluster <key> 写作中断，正在续跑 7 步流水线…」
2. 先 `python core/scripts/plan_tracker.py status <plan_id>` 确认停在哪步（或 list 找活跃 plan）。
3. 执行 `/cluster-write CLUSTER_ID=<key>`：调度器按 plan 续跑，已完成 step 不重做。
4. cluster 切章完成后，继续走情况 B 的 `/cluster-save-state`。

### 情况 B — 续跑 cluster-save-state

1. 输出提示：「cluster <key> 已写完但状态未保存完，正在续跑 cluster-save-state…」
2. 执行 `/cluster-save-state CLUSTER_ID=<key>`：按 `wal_recovery.py` 报的「续跑 --n N」
   从 plan_tracker 记录的下一步续跑（plan_tracker 持久态决定续跑点，不读 `_save_state.json`）。
3. 完成 cluster-emergence required step → 展示走向卡 → 等用户选择。

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

| Git 状态 | plan_tracker 状态（wal_recovery） | 判断 | 处理 |
|----------|----------|------|------|
| 工作区干净 | exit 0（无未完成 plan） | 上次完整结束 | 正常继续写下一个 cluster（情况 C） |
| 有未提交章节/草稿 | cluster-save-state 报中断 | cluster-save-state 中断（可能漏了 commit） | 按 wal_recovery 报的 --n N 续跑（情况 B） |
| 有未提交章节/草稿 | exit 0 | save-state 跑完但 commit 没落地 | 续跑 cluster-save-state 的 git-commit-cluster required step（幂等） |
| 工作区干净 | 报中断但 cluster 已在 completed_clusters | 残留 active plan（已跑完未盖 end） | `plan_tracker abort <plan_id>` 清理，按情况 C 继续 |
| 有 .bak 文件残留 | exit 0 | 旧备份残留 | 提示用户手动确认备份来源 |

> 🔴 注：`.wal/cluster_<key>_save_state.json` **永久保留**，不能据其存在与否判 WAL 状态；
> 上表「plan_tracker 状态」一律以 `wal_recovery.py` 输出为准。

**关键原则：**
- 不自动 `git reset`/`git checkout` 抹掉用户的修改
- 不自动提交未知来源的变更（只 commit 流水线产出的文件）
- 如有异常状态，优先向用户报告，让用户判断

---

## WAL 损坏兜底

与 cluster-save-state.md「失败逃生舱」对齐：
- 续跑步号一律走 `wal_recovery.py` 输出的「续跑: ... --n N」（读 plan_tracker 持久态）。不要从
  `cluster_<key>_save_state.json` 读取或推断 plan step 进度。
- 查特定 cluster 的 plan 中断点 → `python core/scripts/wal_recovery.py "<项目路径>" --cluster <key>`。

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
- 🔴 恢复入口只允许 cluster-only required plan step；章节文件只作为 splitter 输出层佐证。
- 不要问用户「要从哪里开始」——自动检测中断点（wal_recovery + 进度.json 权威）。
- 不要重复已完成的工作；以 plan_tracker 的 first incomplete step 作为恢复点。
- 🔴 不要把 `.wal/cluster_<key>_save_state.json` 文件「存在」当中断信号——它永久保留，
  只看其 `status` 字段做辅助佐证，断点判定以 `wal_recovery` 的 plan 完成态为准。
- 如果检测到数据不一致（如 wal_recovery 报某 cluster save-state 中断但它已在
  `进度.json.completed_clusters` 里），以**进度.json.completed_clusters + 物理章节文件**为准
  （= 该 cluster 已完成，按情况 C 写下一个），残留 active plan 可 abort 清理。

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
