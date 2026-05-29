---
description: cluster 级状态保存 12 步流水线 · 一次性应用 cluster_changes + 涌现下个 cluster brief
---

你是若渝AI的**故事块状态保存调度器**。你不写作、不审稿——你只按顺序调度 4 个专精 agent + 多个脚本。

$ARGUMENTS

> **三段式纪律**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [core/claude-home/HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。

---

# 🔴 v26 设计哲学

**1 个 cluster = 1 次完整 save-state 流水线**。状态同步单位是 cluster 级，章节只是输出层。

- 数据库一次性应用 `cluster_changes.json`（不再按章 parse-apply N 次）
- Git 1 cluster 1 commit（不再 per chapter）
- 所有 agent (summarizer/foreshadower/reflector/outline-planner) 走 cluster mode
- 末尾涌现下个 cluster 候选 brief 让用户选

**🔴 v26**：chapter mode (`save-state` 单章) 命令/plan/CLI 全部已废弃移除。无降级路径。

---

# 🛡️ Plan 强制规划

```bash
# 写作前先创建 plan
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command cluster-save-state \
  --project "<书名>" \
  --key "<cluster_key>")  # cluster_key 如 "001" / "002"
echo "PLAN_ID=$PLAN_ID"
```

所有 Agent 调用 prompt 顶部必须加：

```
PLAN_ID: $PLAN_ID
STEP: <当前步骤号>
```

---

# 流水线架构（12 步）

```
1.  wal-start + db_schema_validate (--auto-migrate)
2.  parse cluster_changes.json (factual/self_eval)
3.  apply-cluster-changes + writer_truth_check (一次性应用到 13 JSON)
4.  validate_chapter (整 cluster 跑 · hard_gate 校验)
5.  novel-summarizer MODE=cluster (生成 cluster 级摘要)
6.  novel-foreshadower MODE=cluster (整 cluster 伏笔评估)
7.  novel-reflector MODE=cluster (经验沉淀)
8.  wal-merge + learning_loop + judge_reports_archive
9.  cluster-scan + state + drift + evolution (13 个 wrapper 脚本)
10. git-commit-cluster (1 cluster 1 commit)
11. cluster-emergence + novel-outline-planner (涌现下个 cluster brief)
12. wal-end + plan-end
```

---

# 第 1 步：wal-start + schema-validate

```bash
# 开 cluster WAL
WAL_PATH="<项目路径>/_数据库/.wal/cluster_<key>_save_state.json"
mkdir -p "$(dirname "$WAL_PATH")"
echo '{"started_at":"'$(date -Iseconds)'","cluster_key":"<key>","completed_steps":[]}' > "$WAL_PATH"

# 数据库 schema 自检 + 自动迁移（v25+ 字段补全）
python core/scripts/db_schema_validate.py "<项目路径>" --auto-migrate
```

**plan-step 1**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1
```

---

# 第 2 步：parse cluster_changes

读 `章节/cluster_<key>_draft/cluster_changes.json` 的 `factual` / `self_eval` 段（writer 整块产出的 cluster 级单文件草稿元数据）。

> **2026-05-29 复审修复[L18]**：实现真相 —— `save_state.cmd_apply_cluster_changes` 会**展开本 cluster 的 chapter_range，逐章调 `cmd_parse`**，每章落地 `_数据库/.wal/第<N>章_parsed.json`（per-chapter，非单文件）+ 一份 cluster 级 `_数据库/.wal/<key>_apply_cluster.json` 汇总。**不存在** `cluster_<key>_parsed.json` 这个文件（旧文档幻影命名，已删）。
>
> 解析无独立 CLI 命令——第 3 步 `--apply-cluster-changes` 内部一并完成（apply 前必先 parse）。本步只做 plan 记账。

**plan-step 2**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2
```

---

# 第 3 步：apply-cluster-changes + writer-truth-check

一次性应用 cluster_changes.json 的 factual 段到 13 JSON（人物卡/伏笔表/世界状态/时间线/道具/故事块摘要等）+ 检测 writer 撒谎：

```bash
python core/scripts/save_state.py "<项目路径>" --apply-cluster-changes <key>
```

内部会：
- 解析 cluster_changes
- 按章 iterate（cmd_parse + apply_changes）但作为整体事务
- writer_truth_check 跑（writer 声明 X 但正文实际 Y → 报错）

失败处理：
- writer_truth_check FAIL → 停止，要求用户回查正文 vs changes 一致性

**plan-step 3**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3 --skip-output
```

---

# 第 4 步：validate-cluster

对整 cluster 跑 validate_chapter（所有切出的章 + cluster 级 hard_gate 校验）：

```bash
# 内部会展开 chapter_range 逐章跑
python core/scripts/validate_chapter.py "<项目路径>" --cluster <key> 2>&1 | tail -20
```

如有 hard_gate FAIL → 停止流水线，spawn validator-checker 修。否则继续。

**plan-step 4**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --skip-output
```

---

# 第 5 步：novel-summarizer MODE=cluster

```
Agent 启动 novel-summarizer:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

产出：`_数据库/.wal/cluster_<key>_summary.json`

cluster 级摘要（不是单章摘要 · 单章摘要由 splitter 切完后从 cluster 摘要派生）。

**plan-step 5**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5
```

---

# 第 6 步：novel-foreshadower MODE=cluster

```
Agent 启动 novel-foreshadower:
PLAN_ID: $PLAN_ID
STEP: 6
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

整 cluster 伏笔评估：plant + 回收 + 健康度。

JudgeReport: `_数据库/.judge_reports/cluster_<key>_foreshadower.json`

**plan-step 6**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 6 --skip-output
```

---

# 第 7 步：novel-reflector MODE=cluster

```
Agent 启动 novel-reflector:
PLAN_ID: $PLAN_ID
STEP: 7
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

提取整 cluster 写作经验（success/failure 模式）→ 写入 `_数据库/写作经验.json`。

JudgeReport: `_数据库/.wal/cluster_<key>_reflection.json`

**plan-step 7**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7 --skip-output
```

---

# 第 8 步：wal-merge + learning_loop + judge-archive

```bash
# learning_loop 三步链（merge-reflection + ingest + scan-recurring）+ WAL 合并
python core/scripts/save_state.py "<项目路径>" --auto-post-reflect-cluster <key>

# 把本 cluster 所有 JudgeReport 存入 故事块摘要[ch_range].judge_reports[]
python core/scripts/judge_reports_archive.py "<项目路径>" --cluster <key> || true
```

**plan-step 8**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 8 --skip-output
```

---

# 第 9 步：cluster-scan + state + drift + evolution

13 个 wrapper 脚本批量跑：

```bash
python core/scripts/save_state_updates.py "<项目路径>" --cluster <key> --all
python core/scripts/save_state_evaluators.py "<项目路径>" --cluster <key> --all
python core/scripts/run_cross_cluster_aggregates.py "<项目路径>" --cluster <key> --tier full_18
python core/scripts/style_drift_scan.py "<项目路径>" --last-n 10
python core/scripts/character_index.py "<项目路径>" --write
python core/scripts/learning_loop.py "<项目路径>" --scan-recurring
python core/scripts/world_evolution_apply_chapter.py "<项目路径>" --cluster <key>
python core/scripts/skill_evolver.py "<项目路径>" evolve --cluster <key> || true
python core/scripts/skill_evolver.py "<项目路径>" promote || true
python core/scripts/evolution_orchestrator.py "<项目路径>" --cluster <key> || true
python core/scripts/maybe_judge_consensus.py "<项目路径>" --cluster <key>
python core/scripts/audit_dashboard.py "<项目路径>"
python core/scripts/scan_retention.py "<项目路径>" --keep 5
```

任意非关键脚本失败（`|| true` 保护）→ 不阻塞主流水线。

**plan-step 9**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 9 --skip-output
```

---

# 第 10 步：git-commit-cluster

```bash
python core/scripts/save_state.py "<项目路径>" --git-commit-cluster <key>
```

commit msg: `feat(cluster-NNN): N 章 (chX-chY)`

**plan-step 10**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 10 --skip-output
```

---

# 第 11 步：cluster-emergence + outline-planner

涌现下个 cluster 的 2-3 个候选 brief：

```bash
# 引擎产生候选
python core/scripts/cluster_emergence_engine.py "<项目路径>" emerge --after-cluster <key>
```

然后 spawn outline-planner 详化候选：

```
Agent 启动 novel-outline-planner:
PLAN_ID: $PLAN_ID
STEP: 11
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster_emergence
EMERGENCE_CONTEXT_PATH: <项目路径>/_数据库/.wal/cluster_<next_key>_emergence.json
```

产出 2-3 张走向卡（candidate brief），主代理展示给用户选 1 个写入 `事件簇.json.clusters[N+1]`。

**plan-step 11**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 11
```

---

# 第 12 步：wal-end + plan-end

```bash
# 关闭 WAL
WAL_PATH="<项目路径>/_数据库/.wal/cluster_<key>_save_state.json"
# 由 save_state.py 内部自动收尾，主代理可手动 mark 完成
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 12 --skip-output
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 上方有步骤漏跑，向用户报告**不要假装完成**。

---

# 📋 完成检查清单

向用户报告"cluster <key> save-state 完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 12 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `_数据库/.wal/cluster_<key>_save_state.json` 存在
- [ ] `_数据库/.wal/<key>_apply_cluster.json` 存在（step3 apply-cluster-changes 汇总 · 含 writer_truth_check）+ 本 cluster 各章 `第<N>章_parsed.json` 已落地（per-chapter）
- [ ] `_数据库/.wal/cluster_<key>_summary.json` 存在（summarizer 产出）
- [ ] `_数据库/.judge_reports/cluster_<key>_foreshadower.json` 存在
- [ ] Git commit `feat(cluster-NNN): N 章 (chX-chY)` 已落地
- [ ] `_数据库/.wal/cluster_<next_key>_emergence.json` 存在（emergence 产出）
- [ ] 2-3 张走向卡 brief 准备好展示给用户

---

# 硬性纪律

- 🔴 **你不修任何 .py 内部实现** — 出错报告用户
- 🔴 **你不跳任何 step** — required 12 步一个不漏
- 🔴 **agent prompt 只传契约字段** — 不塞规则/不塞 manifest
- 🔴 **v26 不降级 chapter mode** — save-state 单章命令/CLI 已删除

---

# Agent 不可用时的降级

- summarizer/foreshadower/reflector/outline-planner 失败 → 跳过该 agent · 记入报告（不阻塞 git commit）
- writer_truth_check FAIL → 必修 · 停流水线
- validate_chapter hard_gate FAIL → 必修 · 停流水线
- git commit 失败 → 记入日志（不阻塞 emergence）

---

# 失败逃生舱

1. 重跑 plan：从 WAL 里 completed_steps 跳过已完成 → 从下一步开始
2. 若 WAL 损坏 → `python core/scripts/wal_recovery.py "<项目路径>" --cluster <key>`
3. 仍崩 → 报告用户人工介入。**🔴 v26 不降级到 chapter mode**——chapter mode 已彻底删除。
