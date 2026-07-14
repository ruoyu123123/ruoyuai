---
description: cluster 级状态保存 required plan 流水线 · 一次性应用 cluster_changes + 涌现下个 cluster brief
---

你是若渝AI的**故事块状态保存调度器**。你不写作、不审稿——你只按顺序调度专精 agent + 多个脚本。

$ARGUMENTS

---

# 🔴 v26 设计哲学

**1 个 cluster = 1 次完整 save-state 流水线**。状态同步单位是 cluster 级，章节只是输出层。

- **状态回库三条数据流分离**：
  - **写作自评流（writer）**：`cluster_changes.json` 的 `self_eval` / `waivers` 只作创作自评和豁免，不承载客观状态。
  - **实体归档流（novel-archivist）**：角色、道具、关系、locked_facts、throughline、信念、反派轮替、力量 tier 和 actant 由正文抽取成 `cluster_<key>_archive.json`，再由 `apply_archive.py` 回库。
  - **运行态增量流（novel-state-tracker）**：时间、地点、Hub 使用、世界事件、机会消费、幕后线响应和已揭 heart events 由正文与当前库梳理成 `cluster_<key>_state_delta.json`，再由确定性脚本校验并回库。
  - 🔴 **硬停**：archive 是 factual 回库的**唯一权威路径**——archivist judge `failure_policy=block`、apply_archive 步无 advisory 前缀，archive 缺出场角色=archivist 失败=错误→硬停（状态缺失不得继续）。
- Git 1 cluster 1 commit（不再 per chapter）
- 所有 agent (archivist/summarizer/foreshadower/reflector/outline-planner) 走 cluster mode
- 末尾涌现下个 cluster 候选 brief 让用户选

**Snapshot / ledger 纪律（对齐 PlotPilot 机制，机制借鉴不搬代码）**：
- `/cluster-save-state` 是状态快照和账本沉淀的唯一入口；writer、splitter 和主会话不承担状态回库职责。
- 快照不另建第二套数据库，统一落到现有 append-only ledger：`故事块摘要.json`、`character_belief_ledger.json`、`反派轮替.json`、`角色弧线.json`、`cluster_actant_ledger.json`、`戏剧问题账本.json` 等。
- 每个 ledger 条目必须能追溯到 `cluster_id`、来源 agent / 脚本和正文证据；无法给证据的内容只能作为 advisory，不得进入 factual 权威层。

**🔴 cluster-only**：状态保存只接受 cluster 级 plan；章节只是 splitter 输出层。

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

# 流水线架构（required steps）

```
1.  schema-validate（db_schema_validate 严格校验 + --report-out 确定性校验报告）
2.  parse cluster_changes.json (self_eval/waivers · 创作自评 · 不含 factual 自报)
3.  validate writer self_eval + cluster writer_truth_check（客观状态零回库）
4.  audit_hub cluster pre-save audit (统一 cluster 审计入口 · hard_gate 校验)
5.  cluster_entity_stats.py 前置确定性统计 → 并行 spawn novel-archivist 与 novel-state-tracker，分别产 archive.json 和 cluster_state_delta.json
6.  apply_archive.py + cluster_state_delta.py 分域确定性回库（任一失败硬停）
7.  novel-summarizer MODE=cluster (cluster 级摘要 + 场景级 Appraisal Beat chain-of-emotion) + S10 卷边界条件子任务 (detect-volume-boundary → MODE=volume 卷级递归摘要 → apply-volume-summary)
8.  novel-foreshadower MODE=cluster (整 cluster 伏笔评估)
9.  novel-reflector MODE=cluster (经验沉淀)
10. wal-merge + learning_loop + JudgeReport cluster 汇总 + build-cluster-summary + 状态回填
11. cluster-scan + state + drift + evolution (wrapper 脚本 + 自学习闭环)
12. git-commit-cluster (1 cluster 1 commit)
13. cluster-emergence + novel-outline-planner (涌现下个 cluster brief)
14. wal-end + plan-end
```

---

# 第 1 步：schema-validate

> step 1 的产物 = `_数据库/.wal/cluster_<key>_schema_validate.json` 确定性校验报告
> （由 `db_schema_validate.py --report-out` 落盘：result / errors_count / warnings_count / 明细 / 时间戳）。
> 校验不过时报告照写、脚本 exit 1 阻断；续跑点只由 plan JSON 的 `steps[].status` 决定。

```bash
# 数据库 schema 严格校验 + 写确定性校验报告（step 1 产物）
python core/scripts/db_schema_validate.py "<项目路径>" \
  --report-out "_数据库/.wal/cluster_<key>_schema_validate.json"
# 34 子系统完整性闸（防 cluster 期间子系统更新漏/坏文件 · 缺或坏 → exit 2 阻断）
python core/scripts/scaffold_subsystems.py verify "<项目路径>"
```

**plan-step 1**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1
```

---

# 第 2 步：parse cluster_changes

读 `章节/cluster_<key>_draft/cluster_<key>_changes.json` 的 `self_eval` / `waivers` 段（writer 整块产出的**创作期自评 / 豁免** + 确定性遥测 · **不含 factual 状态自报**——cluster 级 factual 由第 5 步 archivist 读正文梳理回库，喂 audit 的是创作自评不是 factual）。

本步只确认 canonical `cluster_<key>_changes.json` 已存在；不展开物理章节，不产章级 parsed 文件，也不写任何客观状态。

**plan-step 2**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2
```

---

# 第 3 步：验证 writer self_eval + cluster truth-check

先验证 changes schema 并写自评收据，再直接核对整块正文与 writer 自评：

```bash
python core/scripts/save_state.py "<项目路径>" --apply-cluster-changes <key>
python core/scripts/writer_truth_check.py "<项目路径>" --cluster <key>
```

`--apply-cluster-changes` 只验证 `self_eval`、`waivers` 与确定性写作遥测，产 `_数据库/.wal/cluster_<key>_apply_cluster.json`，其中 `objective_state_applied=false`。`writer_truth_check.py --cluster` 只读 canonical cluster 草稿与 changes，产 `_数据库/.judge_reports/cluster_<key>_writer-truth-check.json`；`verdict != pass` 或 `lie_count != 0` 即阻断。

**plan-step 3**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3 --output "_数据库/.judge_reports/cluster_<key>_writer-truth-check.json"
```

---

# 第 4 步：audit-hub cluster pre-save audit

对整 cluster 跑统一审计入口，所有硬一致性问题都从 `audit_hub.py --mode cluster` 汇总。底层扫描器只由 audit hub 编排，不作为命令文档公开入口。

```bash
python core/scripts/audit_hub.py "<项目路径>" --mode cluster --cluster-id <key> --waivers "<项目路径>/章节/cluster_<key>_draft/cluster_<key>_changes.json"
python core/scripts/scaffold_subsystems.py verify "<项目路径>" --shallow-drift
```

如有 hard_gate FAIL → 停止流水线，spawn validator-checker 修。否则继续。

**plan-step 4**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --output "_数据库/.audit/cluster_<key>_audit.json"
```

---

# 第 5 步：并行状态梳理（archive + state delta）

> 🔴 **两条数据流分离**：本步起的 archivist / summarizer / foreshadower / reflector 同属「**Claude 读正文梳理**」段，与 writer（gen-model）的「写作自评」流互不越权。
> - **写作自评（writer）**：cluster_changes.json 的 self_eval / waivers → 喂 audit（创作自评 / 豁免），**不作 factual 回库权威源**。
> - **状态梳理（Claude archivist）**：读 cluster_draft.txt 正文客观抽取 → archive.json → apply_archive.py 确定性回库角色 / 道具 / 关系 / locked_facts / throughline / 角色信念(belief_ledger) / 反派轮替(反派轮替.json) / 主角力量 tier(角色弧线.json) / 六位 actant 派分(cluster_actant_ledger.json)。

**5a · 前置确定性统计（A15 · spawn 之前跑）**：

```bash
python core/scripts/cluster_entity_stats.py "<项目路径>" --cluster <key>
```

产 `_数据库/.wal/cluster_<key>_entity_stats.json`（moyin collectCharacterStats 范式·零 LLM）：每实体出场次数 / 对白条数估计（引号邻域归属）/ 首现位置 + 未登记新专名候选（2-4 字·频次≥3 防噪）。**代码算客观统计·archivist 只裁决主观归类**——统计是证据基线，统计里频次高的实体在 archive.characters 缺失 = 漏抽信号（压漏报·钟楼弃儿 writer 漏报教训）。

**5b · spawn novel-archivist**（读整 cluster 正文 + 统计证据基线，抽取实体与事实归档，产 archive.json）。

**5c · spawn novel-state-tracker**（读整 cluster 正文 + 时间线/地图/枢纽场景/世界状态/事件池/角色行动表，产独立运行态增量）：

```
Agent 启动 novel-state-tracker:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: cluster_<key>
MODE: cluster
CLUSTER_DRAFT_PATH: <项目路径>/章节/cluster_<key>_draft/cluster_<key>_draft.txt
TIMELINE_PATH: <项目路径>/_数据库/时间线.json
MAP_PATH: <项目路径>/_数据库/地图.json
HUBS_PATH: <项目路径>/_数据库/枢纽场景.json
WORLD_STATE_PATH: <项目路径>/_数据库/世界状态.json
GRAND_TREND_PATH: <项目路径>/_数据库/大势卡.json
ENSEMBLE_PATH: <项目路径>/_数据库/群像档.json
RIPPLE_RULES_PATH: <项目路径>/_数据库/涟漪规则.json
STATE_DELTA_PATH: <项目路径>/_数据库/.wal/cluster_<key>_state_delta.json
RECEIPT_PATH: <项目路径>/_数据库/.wal/cluster_<key>_state_tracker_receipt.json
```

state-tracker 写完 delta 后必须运行 `state_tracker_receipt.py` 生成独立回执；回执绑定 `PLAN_ID`、`STEP=5`、当前 `cluster_id` 与 delta 的 SHA-256。`cluster_<key>_state_delta.json` 是业务产物，不能冒充 Agent 完成证明。

```
Agent 启动 novel-archivist:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: cluster_<key>
MODE: cluster
CLUSTER_DRAFT_PATH: <项目路径>/章节/cluster_<key>_draft/cluster_<key>_draft.txt
ENTITY_STATS_PATH: <项目路径>/_数据库/.wal/cluster_<key>_entity_stats.json
```

产出：`_数据库/.wal/cluster_<key>_archive.json`（characters / items / relationships / locked_facts / throughline_progress / belief_updates / belief_unaware / antagonist_rotation / protagonist_power_tier_update / cluster_actant_state）。

> 🔴 **反派轮替 ledger**：archivist 产出 `antagonist_rotation`——本块**实际出场反派**的轮替条目（antagonist_id 复用角色 id / tier 数值梯度 / faction / motive_type / power_system_tag / defeat_cluster）。非每个 cluster 都有反派；无反派时 archivist 必须以合法空结构表达“本块无反派轮替”，不得省略 required 产物。回库进 `反派轮替.json` append-only ledger 供 `antagonist_rotation_scanner` 消费。

> 🔴 **主角力量 tier 变化 ledger**：archivist 产出 `protagonist_power_tier_update`——本块**主角力量 tier 变化**（char_id 复用角色 id / tier:int 本书叙事梯度炼气1→筑基2… / notes 标突破/跌境）。**只认正文实写的力量变化·非每 cluster 必有**；无变化时必须写合法空结构。回库 append 进 `角色弧线.json` characters[pid].protagonist_power_tier 序列供 `power_progression_scanner` 消费。tier 仅 scanner 内部排序·**绝不暴露给 writer**（同不暴露目标章数的规则）。

> 🔴 **Actant 链 ledger**：archivist 产出 `cluster_actant_state`——本块六位 Greimas actant 派分（subject/object/sender/receiver 单值=角色 id 或 null·helper/opponent=角色 id list）。判不出清晰 actant 时也要写合法空 assignments，作为“本块无清晰 actant 变化”的证据。回库进 `cluster_actant_ledger.json`（clusters[].assignments·id→name 解析·helper/opponent 取首位代表存单值·幂等按 cluster_id 去重）供 `actant_drift_scanner`（功能漂移/关键位空缺/过载）+ `cast_economy_scanner`（隐式 role_split）消费；本 cluster actant 由 `build_manifest` 注入 manifest.cluster_actant_state（ledger 已回库优先·否则 subject=主角/opponent=standing 反派派生）+ manifest.active_cast（scene participants + active_chars·display name）供两 scanner 读。

> 🔴 **硬停**：archivist judge `failure_policy=block`——它是 factual 回库的唯一权威源，失败必须硬停；角色/道具/关系/locked_facts 缺失时不得继续。每个写完的 cluster 必有出场角色。

**plan-step 5**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5
```

---

# 第 6 步：分域确定性回库

把第 5 步 archive.json 幂等回库（无模型）：

```bash
python core/scripts/apply_archive.py "<项目路径>" --cluster <key>
python core/scripts/cluster_state_delta.py "<项目路径>" --cluster <key>
```

把 archive.json 落到 人物卡 / 角色池 / 道具 / 关系 / 事件簇.clusters[].locked_facts + 事件簇.clusters[].throughline_progress + character_belief_ledger.json + 反派轮替.json + 角色弧线.json + cluster_actant_ledger.json（复用已有 id，绝不为同一角色造第二个 id）。

这是 factual 回库的**唯一权威路径**：角色、道具、关系、locked_facts、throughline、角色信念、反派轮替、主角力量 tier 和六位 actant 都以 archivist 读取正文生成的 archive 为源。`apply_antagonist_rotation`、`apply_protagonist_power_tier`、`apply_actant_state` 以 cluster_id 和实体 id 幂等写入。
>
> 🔴 **硬停**：archive 缺出场角色 = archivist 失败 = 错误 → apply_archive exit 2 → plan 硬停。幂等去重保留（re-apply 全已存在 → exit 0 成功）。

**plan-step 6**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 6
```

---

# 第 7 步：novel-summarizer MODE=cluster

```
Agent 启动 novel-summarizer:
PLAN_ID: $PLAN_ID
STEP: 7
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

产出：`_数据库/.wal/cluster_<key>_summary.json`

cluster 级摘要（不是单章摘要 · 单章摘要由 splitter 切完后从 cluster 摘要派生）。

> 🔴 **场景级 Appraisal Beat（chain-of-emotion）**：summarizer 同时产出 `appraisal_beats[]`（读整 cluster 正文 + scene_storyboard 按 Scherer CPM/OCC 评价链把关键情绪拐点反推成结构化 STATE：trigger → appraisal 6 维 → derived_emotion 自然语言 → behavior_externalization + vad_bin）。它**梳理非创作**（禁占位词典浅扫·禁情绪词标签），与 archivist 同属 Claude 读正文梳理段。回填由第 10 步 `--apply-appraisal-beats` 确定性落 `叙事节拍器.json.appraisal_beats`（全 advisory STATE·不进 HARD_GATE_CODES）。

## 🔴 S10 卷边界条件子任务（递归卷级层级摘要 · Ex3 摘要金字塔 + source 回溯）

本步 scripts 先跑卷边界确定性检测（report-only · 恒 exit 0）：

```bash
python core/scripts/save_state.py "<项目路径>" --detect-volume-boundary <key>
```

只用**既有信号**判定（不另立）：某卷的大势卡 ME 池非空且全部 `status=completed`（status 由 step 3 `_mark_cluster_me_completed` 唯一维护·`completed_by_cluster` 给出本卷 cluster_ids）＝ 卷已完结；且 `故事块摘要.volume_summaries` 尚无该卷条目 → `boundary=true`。产物：`_数据库/.wal/cluster_<key>_volume_boundary.json`。

- **`boundary=false`（绝大多数 cluster）→ 零行为变化**，本子任务到此结束。
- **`boundary=true`** → 追加两个条件子步骤（在 MODE=cluster summarizer 完成后执行）：

```
Agent 启动 novel-summarizer:
PLAN_ID: $PLAN_ID
STEP: 7
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: volume
VOLUME_N: <detect 产物 volumes_pending[].volume>
VOLUME_CLUSTER_IDS: <detect 产物 volumes_pending[].cluster_ids · 逗号分隔 · 不得增删>
CLUSTER_SUMMARIES_PATH: _数据库/故事块摘要.json
```

产出 `_数据库/.wal/volume_<N>_summary.json`（聚合本卷 cluster 摘要成 300-500 字卷级摘要；账本缺的 cluster——典型是本卷末块——读 `.wal/<cid>_summary.json` 兜底；**不重读正文**·金字塔纪律），然后确定性回库：

```bash
python core/scripts/save_state.py "<项目路径>" --apply-volume-summary <N>
```

回库唯一入口校验：结构键钉死（volume/summary/source/generated_at_cluster 缺即拒）+ **source 必须恰好覆盖本卷全部 cluster_ids**（缺=漏源·多=幻觉源·都 exit 2 零写入）+ 卷真闭合 + 幂等 upsert（同内容 re-apply 零写盘）。字数 300-500 仅 advisory。漏跑自愈：本次漏掉，下个 cluster 的 detect 会重新亮起该卷。

**plan-step 7**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7
```

---

# 第 8 步：novel-foreshadower MODE=cluster

```
Agent 启动 novel-foreshadower:
PLAN_ID: $PLAN_ID
STEP: 8
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

整 cluster 伏笔评估：plant + 回收 + 健康度。

> 🔴 **戏剧问题账本（PITQ/MDQ）**：foreshadower 同时登记本 cluster 的**戏剧问题**到 JudgeReport 的 `specific_findings.dramatic_questions = {raised:[{qid, question(具体二元PITQ), scope:cluster|volume|series, raised_at_scene, expected_payoff_window}], answered:[{qid, answered_at_scene}]}`（伏笔⊂PITQ 的特例·account 同构）。读者粘性唯一宏观结构缺口：读者追读=想知道核心二元问题的答案。回填由第 10 步 `--apply-dramatic-questions` 确定性落 `戏剧问题账本.json`（只 active cluster·按 qid 幂等去重；缺 JudgeReport 或缺 dramatic_questions 字段即 exit 2；空 raised/answered 表示本块无新增）。

JudgeReport: `_数据库/.judge_reports/cluster_<key>_foreshadower.json`

**plan-step 8**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 8 --output "_数据库/.judge_reports/cluster_<key>_foreshadower.json"
```

---

# 第 9 步：novel-reflector MODE=cluster

```
Agent 启动 novel-reflector:
PLAN_ID: $PLAN_ID
STEP: 9
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster
```

提取整 cluster 写作经验（success/failure 模式）→ 写入 `_数据库/写作经验.json`。

JudgeReport: `_数据库/.wal/cluster_<key>_reflection.json`

**plan-step 9**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 9 --output "_数据库/.wal/cluster_<key>_reflection.json"
```

---

# 第 10 步：wal-merge + learning_loop + judge-archive + build-cluster-summary + data-flywheel

```bash
# learning_loop cluster 三步链（merge-reflection + ingest cluster audit + scan cluster recurrence）+ WAL 合并
python core/scripts/save_state.py "<项目路径>" --auto-post-reflect-cluster <key>

# 聚合本 cluster 的 JudgeReport 与 required 评估信号，落独立 rollup 供摘要构建器消费。
# 经 adaptive_runner 执行；失败时记录 incident 并硬停。
python core/scripts/adaptive_runner.py --label judge_reports_archive --strict -- python core/scripts/judge_reports_archive.py "<项目路径>" --cluster <key>

# 读取正文、状态 delta、archive、truth report 与 judge rollup，写入严格 cluster 摘要账本。
python core/scripts/save_state.py "<项目路径>" --build-cluster-summary <key>

# 把 brief 规划伏笔注册进伏笔表，并按 step 8 foreshadower 报告应用本块 payoff；缺报告或写回失败即阻断
python core/scripts/save_state.py "<项目路径>" --apply-foreshadow-state <key>

# 🔴 场景级 Appraisal Beat（chain-of-emotion）：把 step 7 summarizer 产的 appraisal_beats
# 确定性回填 叙事节拍器.json.appraisal_beats（只 active cluster·幂等；summary/appraisal_beats/叙事节拍器缺失即 exit 2，空数组表示无新增）
python core/scripts/save_state.py "<项目路径>" --apply-appraisal-beats <key>

# 🔴 戏剧问题账本（PITQ/MDQ·读者粘性）：把 step 8 foreshadower JudgeReport 产的
# dramatic_questions 确定性回库 戏剧问题账本.json（只 active cluster·按 qid 幂等去重；缺 JudgeReport 或缺字段即 exit 2，空数组表示无新增）
python core/scripts/save_state.py "<项目路径>" --apply-dramatic-questions <key>
```

**plan-step 10**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 10 --output "_数据库/.wal/cluster_<key>_post_reflect.json"
```

---

# 第 11 步：cluster-scan + state + drift + evolution

wrapper 脚本 + 3 行自学习闭环批量跑：

```bash
python core/scripts/save_state_updates.py "<项目路径>" --cluster <key>
python core/scripts/save_state_evaluators.py "<项目路径>" --cluster <key>
python core/scripts/run_cross_cluster_aggregates.py "<项目路径>" --cluster <key>
python core/scripts/style_drift_scan.py "<项目路径>" --last-n 10
python core/scripts/character_index.py "<项目路径>" --write
python core/scripts/learning_loop.py "<项目路径>" --scan-recurring
python core/scripts/world_evolution_apply_cluster.py "<项目路径>" --cluster <key>
python core/scripts/knowledge_graph_update.py "<项目路径>" --cluster <key>
python core/scripts/subplot_progress_update.py "<项目路径>" --cluster <key>
# 演化类经 adaptive_runner：记录学习 + 熔断（取代非严格继续）
python core/scripts/adaptive_runner.py --label skill_evolver_evolve --strict -- python core/scripts/skill_evolver.py "<项目路径>" evolve --cluster <key>
python core/scripts/adaptive_runner.py --label skill_evolver_promote --strict -- python core/scripts/skill_evolver.py "<项目路径>" promote
python core/scripts/adaptive_runner.py --label evolution_orchestrator --strict -- python core/scripts/evolution_orchestrator.py "<项目路径>" --cluster <key>
python core/scripts/adaptive_runner.py --label maybe_judge_consensus --strict -- python core/scripts/maybe_judge_consensus.py "<项目路径>" --cluster <key>
python core/scripts/scan_retention.py "<项目路径>" --keep 5
# 自学习闭环：学本 cluster 运行时报错 → 沉淀 known lesson → 缺步监控
python core/scripts/self_heal_engine.py --ingest
python core/scripts/self_heal_engine.py --emit-lessons
python core/scripts/step_completion_monitor.py --scan-latest --command cluster-save-state --project "<书名>"
python core/scripts/cluster_post_state_receipt.py "<项目路径>" --cluster <key> --plan-id "$PLAN_ID" --step 11
```

`knowledge_graph_update.py` 与 `subplot_progress_update.py` 是本步正式状态生产者；输入缺失、JSON 损坏或写回失败必须停下修正。演化类脚本经 **adaptive_runner --strict** 执行：把运行失败记录到 `runtime/incidents.jsonl` 供 self_heal_engine 学习，然后以非 0 退出阻断当前 plan，并用熔断器防同一脚本连续崩还盲跑。末尾自学习闭环：`self_heal_engine --ingest` 把本 cluster 累积的运行时报错按指纹复发计数（≥3 recurring / ≥5 known），`--emit-lessons` 把 known 级写入 `lessons/runtime_lessons.md`，`step_completion_monitor` 扫本次 plan 是否有假完成/失败/未跑的缺步。最后由 `cluster_post_state_receipt.py` 校验本 cluster 的状态更新、状态评估、全量跨块扫描、世界演化和 Judge consensus 决策回执，并以 SHA-256 绑定这些产物；普通数据库文件不能充当本步完成证明。

**plan-step 11**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 11 --output "_数据库/.wal/cluster_<key>_post_state_receipt.json"
```

---

# 第 12 步：git-commit-cluster

```bash
python core/scripts/save_state.py "<项目路径>" --git-commit-cluster <key>
```

commit msg: `feat(cluster-NNN): N 章 (chX-chY)`

**plan-step 12**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 12 --output "_数据库/.wal/cluster_<key>_git_commit.json"
```

---

# 第 13 步：cluster-emergence + outline-planner

涌现下个 cluster 的 2-3 个候选 brief：

```bash
# 引擎产生候选
python core/scripts/cluster_emergence_engine.py "<项目路径>" emerge --after-cluster <key>
```

然后 spawn outline-planner 详化候选：

```
Agent 启动 novel-outline-planner:
PLAN_ID: $PLAN_ID
STEP: 13
PROJECT: <项目路径>
CLUSTER_ID: <key>
MODE: cluster_emergence
EMERGENCE_CONTEXT_PATH: <项目路径>/_数据库/.wal/cluster_<next_key>_emergence.json
```

产出 2-3 张走向卡（candidate brief），主代理展示给用户选 1 个写入 `事件簇.json.clusters[N+1]`。

**plan-step 13**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 13
```

---

# 第 14 步：wal-end + plan-end

> 收尾 = 跑 `plan_end_receipt.py` 做 plan 最终校验并落回执，再盖 step 14 + end。
> 本 cluster 的状态完成证明是 `_数据库/.wal/cluster_<key>_post_state_receipt.json`（step 11 产），
> 收尾证明是 `_数据库/.wal/cluster_<key>_save_state_end.json`（step 14 产）。

```bash
# 逐条核对 step 1-13 真完成且 verified_outputs 实体文件仍在；
# 任一步假完成（verified_outputs 为空）或产物丢失 → [FATAL] exit 2，本步不得标完成
python core/scripts/plan_end_receipt.py "<项目路径>" \
  --plan-id "$PLAN_ID" --step 14 --command cluster-save-state
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 14
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 上方有步骤漏跑，向用户报告**不要假装完成**。

---

# 📋 完成检查清单

向用户报告"cluster <key> save-state 完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示所有 required steps 全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `_数据库/.wal/cluster_<key>_apply_cluster.json` 存在且 `objective_state_applied=false`；`_数据库/.judge_reports/cluster_<key>_writer-truth-check.json` 为本 cluster 的 pass 报告
- [ ] `_数据库/.wal/cluster_<key>_entity_stats.json` 存在（step 5 前置 cluster_entity_stats.py 确定性统计 · A15 证据基线）
- [ ] `_数据库/.wal/cluster_<key>_archive.json`、`cluster_<key>_state_delta.json` 与 `cluster_<key>_state_tracker_receipt.json` 均存在；回执绑定 plan/step/delta SHA-256，step 6 已分别回库实体归档与运行态增量
- [ ] `_数据库/.wal/cluster_<key>_summary.json` 存在（summarizer 产出 · 含 appraisal_beats list）+ apply-appraisal-beats 已回填 叙事节拍器.appraisal_beats（空 list 只表示本块无新增）
- [ ] `_数据库/.wal/cluster_<key>_volume_boundary.json` 存在（step 7 detect 产出）；`boundary=true` 时 `故事块摘要.volume_summaries` 已含该卷条目（`--apply-volume-summary` 回库·source 覆盖本卷全部 cluster_ids）
- [ ] `_数据库/.judge_reports/cluster_<key>_foreshadower.json` 存在（含 specific_findings.dramatic_questions）+ apply-dramatic-questions 已回库 戏剧问题账本.json（空 raised/answered 只表示本块无新增）
- [ ] `_数据库/.wal/cluster_<key>_judge_reports_rollup.json` 存在，且已由 `cluster_summary_builder.py` 写入对应 cluster 摘要记录
- [ ] `_数据库/.wal/cluster_<key>_foreshadow_state_receipt.json` 存在；brief 伏笔注册与本块 payoff 已由 required cluster 子命令完成
- [ ] `_数据库/.wal/cluster_<key>_post_state_receipt.json` 存在并绑定 step 11 的状态更新、状态评估、跨块 wrapper、世界演化和 consensus 决策产物
- [ ] Git commit `feat(cluster-NNN): N 章 (chX-chY)` 已落地
- [ ] `_数据库/.wal/cluster_<next_key>_emergence.json` 存在（emergence 产出）
- [ ] 2-3 张走向卡 brief 准备好展示给用户

---

# 硬性纪律

- 🔴 **你不修任何 .py 内部实现** — 出错报告用户
- 🔴 **你不跳任何 step** — required steps 一个不漏
- 🔴 **agent prompt 只传契约字段** — 不塞规则/不塞 manifest
- 🔴 **cluster-only 状态保存** — 状态回库只接受 cluster 级 required plan
- 🔴 **factual 回库硬停** — archivist（block）+ apply_archive（硬停）是状态回库唯一权威路径，缺角色必停

---

# Agent / 脚本不可用时的硬停策略

- summarizer/foreshadower/reflector/outline-planner 失败 → 停止当前 plan，修复后从 plan_tracker 下一步续跑；不得跳过 required agent。
- 🔴 **archivist 失败 → 必停**（factual 回库链断 · failure_policy=block）
- 🔴 **apply_archive 缺角色报错 → 必停**（每个写完的 cluster 必有出场角色）
- writer_truth_check FAIL → 必修 · 停流水线
- audit_hub hard_gate FAIL → 必修 · 停流水线
- git commit 失败 → 停止当前 plan，修复 Git 状态后续跑；不得在未提交状态下继续 emergence。

---

# 失败逃生舱

1. 先跑 `python core/scripts/wal_recovery.py "<项目路径>" --cluster <key>` 或 `python core/scripts/plan_tracker.py status <plan_id>`。
2. 以 plan_tracker 的 first incomplete step 为恢复点；已完成且产物通过 expected_outputs 的 step 不重做。
3. 仍崩 → 报告当前 plan 阻塞证据；恢复点仍是当前 cluster 的 required plan step。

---

# 主代理优化（A 方案 · 跨家族 inline 复审）

主代理（Claude Code 自身）在线时：audit / voice 系列 judge 触发**之前**可主动 spawn 一个 `Agent(claude)` 复审 **finale subcluster** 的 audit / voice 维度，把裁决落回 `_数据库/.wal/claude_verdict_<sha12>_<judge>.json`，subprocess 流水线下一轮 audit_hub / voice scan 自动捡用 → 写入 `outcome.data['cross_family_check']`。

落地 API（同会话 spawn 后调）：

```python
from cross_family_judge_check import save_inline_verdict_for_main_agent
save_inline_verdict_for_main_agent(
    draft_text=cluster_draft,   # 整 cluster 草稿正文
    judge_name="audit",         # 或 "voice"
    verdict="pass",             # 或 "issues"
    reason="<claude 复审简述>",
    project_root=project_root,
)
```

特性：
- **吃 Claude Code 订阅**·零月费·不依赖 BYOK Anthropic key
- 仅 `cluster_finale` 末 sub-cluster 触发，用于补充 audit / voice 元数据。
- 默认 `shadow` 模式；`active` 也只写 outcome 元数据，不替代任何 required agent、脚本或 hard_gate。
- env `CROSS_FAMILY_JUDGE_MODE=off` 可关闭该增强检查。
- 主代理不在线（subprocess only / 批量自动跑）时只记录 reason；不得把它当成 required 步骤的替代产物。

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
