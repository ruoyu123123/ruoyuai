---
description: cluster 级状态保存 14 步流水线 · 一次性应用 cluster_changes + 涌现下个 cluster brief
---

你是若渝AI的**故事块状态保存调度器**。你不写作、不审稿——你只按顺序调度专精 agent + 多个脚本。

$ARGUMENTS

---

# 🔴 v26 设计哲学

**1 个 cluster = 1 次完整 save-state 流水线**。状态同步单位是 cluster 级，章节只是输出层。

- **状态回库两条数据流分离（2026-06-28 审计清理C类 + 不降级收尾）**：
  - **写作自评流（writer · gen-model）**：`cluster_changes.json` 的 `self_eval` / `waivers` 只作创作自评 / 豁免喂 audit，**factual 不再作回库权威源**。
  - **状态梳理流（archivist · Claude 读正文）**：角色 / 道具 / 关系 / locked_facts / throughline / 角色信念(belief_ledger·per-character witness) / 反派轮替(反派轮替.json·长篇反派梯度 ledger) / 主角力量 tier(角色弧线.json·升级流力量梯度) 由 novel-archivist 读 cluster_draft.txt 正文产 archive.json → `apply_archive.py` 确定性回库（北极星⑤：创作=gen-model writer / 状态梳理=Claude archivist · 互不越权）。
  - 🔴 **不降级**：archive 是 factual 回库的**唯一权威路径**——archivist judge `failure_policy=block`、apply_archive 步无 advisory 前缀，archive 缺出场角色=archivist 失败=错误→硬停（不静默放行状态丢失）。
- Git 1 cluster 1 commit（不再 per chapter）
- 所有 agent (archivist/summarizer/foreshadower/reflector/outline-planner) 走 cluster mode
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

# 流水线架构（14 步）

```
1.  wal-start + db_schema_validate (--auto-migrate)
2.  parse cluster_changes.json (self_eval/waivers · 创作自评 · 不含 factual 自报)
3.  apply-cluster-changes + writer_truth_check (非 archive 域 time_advance/location + 撒谎检测)
4.  validate_chapter (整 cluster 跑 · hard_gate 校验)
5.  novel-archivist MODE=cluster (读正文产 archive.json · factual 权威源 · failure_policy=block · 含 belief_updates witness)
6.  apply_archive.py (角色/道具/关系/locked_facts/throughline + 角色信念 belief_ledger 确定性回库 · 不降级硬停)
7.  novel-summarizer MODE=cluster (cluster 级摘要 + 场景级 Appraisal Beat chain-of-emotion)
8.  novel-foreshadower MODE=cluster (整 cluster 伏笔评估)
9.  novel-reflector MODE=cluster (经验沉淀)
10. wal-merge + learning_loop + judge_reports_archive + build-cluster-summary + apply-appraisal-beats + data-flywheel
11. cluster-scan + state + drift + evolution (wrapper 脚本 + 自学习闭环)
12. git-commit-cluster (1 cluster 1 commit)
13. cluster-emergence + novel-outline-planner (涌现下个 cluster brief)
14. wal-end + plan-end
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

> **2026-05-29 复审修复[L18]**：实现真相 —— `save_state.cmd_apply_cluster_changes` 会**展开本 cluster 的 chapter_range，逐章调 `cmd_parse`**，每章落地 `_数据库/.wal/第<N>章_parsed.json`（per-chapter，非单文件）+ 一份 cluster 级 `_数据库/.wal/<key>_apply_cluster.json` 汇总。**不存在** `cluster_<key>_parsed.json` 这个文件（旧文档幻影命名，已删）。
>
> 解析无独立 CLI 命令——第 3 步 `--apply-cluster-changes` 内部一并完成（apply 前必先 parse）。本步只做 plan 记账。

**plan-step 2**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2
```

---

# 第 3 步：apply-cluster-changes + writer-truth-check

一次性处理 cluster_changes.json + 检测 writer 撒谎（writer_truth_check）：

```bash
python core/scripts/save_state.py "<项目路径>" --apply-cluster-changes <key>
```

> 🔴 **模型/可成长闭环自动接入（2026-06-30）**：save_state `main()` 默认开启模型与可成长门控（同 audit_hub·经 `nn_runtime_defaults`·无需手动 export）：
> - **FeatureStore**：NN scanner / save_state 复用 VAD、surprisal、coherence 特征缓存，减少重复推理，统一训练/服务特征口径。
> - **ModelRegistry**：入口同步当前 active/shadow 模型版本与指标，保留运行时治理账本。
> - **DataFlywheel**：第 10 步 auto-post-reflect 后自动收集 paragraph / weak label / waiver / legacy fixer pair / checker brief / gen_fixer report / style repair report / judge report reliability / reading reflection / audit metadata 训练样本。
> - **VAD 情绪模型**：`--apply-appraisal-beats` 步对 appraisal beat 的 valence/arousal 真模型重算（CCC 0.80·advisory·`vad_bin._source=model_va+summarizer_d`·能力不足退 summarizer 启发式不阻断）。

> 🔴 **2026-06-28 审计清理C类**：cluster 级 factual 状态（角色 / 道具 / 关系 / locked_facts / 伏笔）**不再从 writer changes.factual 回库**——这些由第 5/6 步 novel-archivist 读正文产 archive.json → `apply_archive.py` 确定性回库，伏笔由 foreshadower + outline brief 回库。本步 apply 只落地**无替代 producer 的非 archive 域**项（time_advance 时间线 / location_changes 地点 status）+ 跑 writer_truth_check。

内部会：
- 解析 cluster_changes（self_eval/waivers + 残留非 archive 域 factual）
- 按章 iterate（cmd_parse + apply_changes）但作为整体事务 · 只落 time_advance / location_changes 等非 archive 域项
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

# 第 5 步：novel-archivist MODE=cluster（读正文产 archive.json）

> 🔴 **2026-06-28 审计清理C类 · 两条数据流分离**：本步起的 archivist / summarizer / foreshadower / reflector 同属「**Claude 读正文梳理**」段，与 writer（gen-model）的「写作自评」流互不越权。
> - **写作自评（writer）**：cluster_changes.json 的 self_eval / waivers → 喂 audit（创作自评 / 豁免），**不作 factual 回库权威源**。
> - **状态梳理（Claude archivist）**：读 cluster_draft.txt 正文客观抽取 → archive.json → apply_archive.py 确定性回库角色 / 道具 / 关系 / locked_facts / throughline / 角色信念(belief_ledger) / 反派轮替(反派轮替.json) / 主角力量 tier(角色弧线.json) / 六位 actant 派分(cluster_actant_ledger.json)。

spawn novel-archivist（读整 cluster 正文，客观抽取本块新增/变更状态，产 archive.json）：

```
Agent 启动 novel-archivist:
PLAN_ID: $PLAN_ID
STEP: 5
PROJECT: <项目路径>
CLUSTER_ID: cluster_<key>
MODE: cluster
CLUSTER_DRAFT_PATH: <项目路径>/章节/cluster_<key>_draft/cluster_<key>_draft.txt
CLUSTER_CHAPTER_RANGE: <START_CH>-<END_CH>
```

产出：`_数据库/.wal/cluster_<key>_archive.json`（characters / items / relationships / locked_facts / throughline_progress / belief_updates / belief_unaware / antagonist_rotation / protagonist_power_tier_update / cluster_actant_state）。

> 🔴 **2026-06-29 反派轮替ledger接通producer**：archivist 扩产 `antagonist_rotation`——本块**实际出场反派**的轮替条目（antagonist_id 复用角色 id / tier 数值梯度 / faction / motive_type / power_system_tag / defeat_cluster）。引入即报不逐块重复·击败补 defeat_cluster·**非每 cluster 必有反派**（C03 fluid 无反派合法省略）。回库进 `反派轮替.json` append-only ledger 供 `antagonist_rotation_scanner` 消费（此前**零 producer** 死码·全 advisory shadow·绝不 hard_gate）。

> 🔴 **2026-06-29 power_progression接通producer**：archivist 扩产 `protagonist_power_tier_update`——本块**主角力量 tier 变化**（char_id 复用角色 id / tier:int 本书叙事梯度炼气1→筑基2… / notes 标突破/跌境）。**只认正文实写的力量变化·非每 cluster 必有**（铺垫/日常/非升级流题材合法省略·C03 fluid）。回库 append 进 `角色弧线.json` characters[pid].protagonist_power_tier 序列供 `power_progression_scanner` 消费（此前**零 producer** 死码·全 advisory shadow·绝不 hard_gate）。tier 仅 scanner 内部排序·**绝不暴露给 writer**（同 v27 不暴露目标章数）。

> 🔴 **2026-06-29 actant链接通producer**：archivist 扩产 `cluster_actant_state`——本块六位 Greimas actant 派分（subject/object/sender/receiver 单值=角色 id 或 null·helper/opponent=角色 id list）。判不出清晰 actant = 整段省略（C03 fluid 合法跳过）。回库进 `cluster_actant_ledger.json`（clusters[].assignments·id→name 解析·helper/opponent 取首位代表存单值·幂等按 cluster_id 去重）供 `actant_drift_scanner`（功能漂移/关键位空缺/过载）+ `cast_economy_scanner`（隐式 role_split）消费；本 cluster actant 由 `build_manifest` 注入 manifest.cluster_actant_state（ledger 已回库优先·否则 subject=主角/opponent=standing 反派派生）+ manifest.active_cast（scene participants + active_chars·display name）供两 scanner 读（此前 build_manifest **零产**两 key·整条死码）。全 advisory shadow·绝不 hard_gate。

> 🔴 **不降级**：archivist judge `failure_policy=block`——它是 factual 回库的唯一权威源，失败必须硬停（不能 soft 降级让角色/道具/关系/locked_facts 静默丢失）。每个写完的 cluster 必有出场角色。

**plan-step 5**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5
```

---

# 第 6 步：apply-archive（确定性回库）

把第 5 步 archive.json 幂等回库（无模型）：

```bash
python core/scripts/apply_archive.py "<项目路径>" --cluster <key>
```

把 archive.json 落到 人物卡 / 角色池 / 道具 / 关系 / 事件簇.clusters[].locked_facts + 事件簇.clusters[].throughline_progress + character_belief_ledger.json + 反派轮替.json + 角色弧线.json + cluster_actant_ledger.json（复用已有 id，绝不为同一角色造第二个 id）。

> 🔴 这是 factual 回库的**唯一权威路径**：writer 已不自报 factual（gen_writer 已删 factual 自报 · save_state 已停读 writer factual）——角色/道具/关系/locked_facts/throughline/角色信念(belief_ledger)/反派轮替(反派轮替.json)/主角力量 tier(角色弧线.json)/六位 actant(cluster_actant_ledger.json) 的权威源 = archivist 读正文，非 writer changes。`apply_antagonist_rotation` + `apply_protagonist_power_tier` + `apply_actant_state` 确定性 append（前者按 cluster_id+antagonist_id 去重、中者按 pid 的 series cluster_id 去重、后者按 cluster_id 去重替换·幂等·无对应段则 no-op 不建文件）。
>
> 🔴 **不降级硬停**：archive 缺出场角色 = archivist 失败 = 错误 → apply_archive exit 2 → plan 硬停（不再"空 archive → exit 1 当 no-op"静默降级）。幂等去重保留（re-apply 全已存在 → exit 0 成功）。

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

> 🔴 **2026-06-29 场景级 Appraisal Beat（chain-of-emotion）**：summarizer 同时扩产 `appraisal_beats[]`（读整 cluster 正文 + scene_storyboard 按 Scherer CPM/OCC 评价链把关键情绪拐点反推成结构化 STATE：trigger → appraisal 6 维 → derived_emotion 自然语言 → behavior_externalization + vad_bin）。它**梳理非创作**（禁占位词典浅扫·禁情绪词标签），与 archivist 同属 Claude 读正文梳理段。回填由第 10 步 `--apply-appraisal-beats` 确定性落 `叙事节拍器.json.appraisal_beats`（全 advisory STATE·不进 HARD_GATE_CODES）。

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

> 🔴 **2026-06-29 戏剧问题账本（PITQ/MDQ）**：foreshadower 同时登记本 cluster 的**戏剧问题**到 JudgeReport 的 `specific_findings.dramatic_questions = {raised:[{qid, question(具体二元PITQ), scope:cluster|volume|series, raised_at_scene, expected_payoff_window}], answered:[{qid, answered_at_scene}]}`（伏笔⊂PITQ 的特例·account 同构）。读者粘性唯一宏观结构缺口：读者追读=想知道核心二元问题的答案。回填由第 10 步 `--apply-dramatic-questions` 确定性落 `戏剧问题账本.json`（只 active cluster·按 qid 幂等去重·全 advisory STATE·未产则 no-op）。

JudgeReport: `_数据库/.judge_reports/cluster_<key>_foreshadower.json`

**plan-step 8**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 8 --skip-output
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
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 9 --skip-output
```

---

# 第 10 步：wal-merge + learning_loop + judge-archive + build-cluster-summary + data-flywheel

```bash
# learning_loop 三步链（merge-reflection + ingest + scan-recurring）+ WAL 合并
python core/scripts/save_state.py "<项目路径>" --auto-post-reflect-cluster <key>

# 把本 cluster 所有 JudgeReport 存入 故事块摘要[ch_range].judge_reports[]
# 经 adaptive_runner：失败不阻塞但记录报错供 self_heal_engine 学习（取代旧 `|| true` 静默吞错）
python core/scripts/adaptive_runner.py --label judge_reports_archive -- python core/scripts/judge_reports_archive.py "<项目路径>" --cluster <key>

# 把整 cluster 富摘要预算写入 故事块摘要.json 账本（供 step 11 cross_cluster aggregator 复用）
python core/scripts/save_state.py "<项目路径>" --build-cluster-summary <key>

# 🔴 2026-06-29 场景级 Appraisal Beat（chain-of-emotion）：把 step 7 summarizer 产的 appraisal_beats
# 确定性回填 叙事节拍器.json.appraisal_beats（只 active cluster·幂等·全 advisory STATE·summarizer 未产则 no-op）
python core/scripts/save_state.py "<项目路径>" --apply-appraisal-beats <key>

# 🔴 2026-06-29 戏剧问题账本（PITQ/MDQ·读者粘性）：把 step 8 foreshadower JudgeReport 产的
# dramatic_questions 确定性回库 戏剧问题账本.json（只 active cluster·按 qid 幂等去重·全 advisory STATE·未产则 no-op）
python core/scripts/save_state.py "<项目路径>" --apply-dramatic-questions <key>
```

**plan-step 10**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 10 --skip-output
```

---

# 第 11 步：cluster-scan + state + drift + evolution

wrapper 脚本 + 3 行自学习闭环批量跑：

```bash
python core/scripts/save_state_updates.py "<项目路径>" --cluster <key> --all
python core/scripts/save_state_evaluators.py "<项目路径>" --cluster <key> --all
python core/scripts/run_cross_cluster_aggregates.py "<项目路径>" --cluster <key> --tier full_18
python core/scripts/style_drift_scan.py "<项目路径>" --last-n 10
python core/scripts/character_index.py "<项目路径>" --write
python core/scripts/learning_loop.py "<项目路径>" --scan-recurring
python core/scripts/world_evolution_apply_chapter.py "<项目路径>" --cluster <key>
# 演化类经 adaptive_runner：失败不阻塞（degrade 放行）但记录学习 + 熔断（取代旧 `|| true` 静默吞错）
python core/scripts/adaptive_runner.py --label skill_evolver_evolve -- python core/scripts/skill_evolver.py "<项目路径>" evolve --cluster <key>
python core/scripts/adaptive_runner.py --label skill_evolver_promote -- python core/scripts/skill_evolver.py "<项目路径>" promote
python core/scripts/adaptive_runner.py --label evolution_orchestrator -- python core/scripts/evolution_orchestrator.py "<项目路径>" --cluster <key>
python core/scripts/adaptive_runner.py --label maybe_judge_consensus -- python core/scripts/maybe_judge_consensus.py "<项目路径>" --cluster <key>
python core/scripts/audit_dashboard.py "<项目路径>"
python core/scripts/scan_retention.py "<项目路径>" --keep 5
# 🆕 自学习闭环（2026-05-30）：学本 cluster 运行时报错 → 沉淀 known lesson → 缺步监控
python core/scripts/self_heal_engine.py --ingest
python core/scripts/self_heal_engine.py --emit-lessons
python core/scripts/step_completion_monitor.py --scan-latest --command cluster-save-state --project "<书名>"
```

演化类脚本经 **adaptive_runner** 自适应执行：失败仍不阻塞主流水线（degrade 放行），但**记录到 `runtime/incidents.jsonl` 供 self_heal_engine 学习 + 熔断器防同一脚本连续崩还盲跑**（取代旧 `|| true` 静默吞错——报错不再消失）。末尾自学习闭环：`self_heal_engine --ingest` 把本 cluster 累积的运行时报错按指纹复发计数（≥3 recurring / ≥5 known），`--emit-lessons` 把 known 级写入 `lessons/runtime_lessons.md`，`step_completion_monitor` 扫本次 plan 是否有假完成/失败/未跑的缺步。

**plan-step 11**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 11 --skip-output
```

---

# 第 12 步：git-commit-cluster

```bash
python core/scripts/save_state.py "<项目路径>" --git-commit-cluster <key>
```

commit msg: `feat(cluster-NNN): N 章 (chX-chY)`

**plan-step 12**：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 12 --skip-output
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

```bash
# 关闭 WAL
WAL_PATH="<项目路径>/_数据库/.wal/cluster_<key>_save_state.json"
# 由 save_state.py 内部自动收尾，主代理可手动 mark 完成
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 14 --skip-output
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 上方有步骤漏跑，向用户报告**不要假装完成**。

---

# 📋 完成检查清单

向用户报告"cluster <key> save-state 完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 14 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `_数据库/.wal/cluster_<key>_save_state.json` 存在
- [ ] `_数据库/.wal/<key>_apply_cluster.json` 存在（step3 apply-cluster-changes 汇总 · 含 writer_truth_check）+ 本 cluster 各章 `第<N>章_parsed.json` 已落地（per-chapter）
- [ ] `_数据库/.wal/cluster_<key>_archive.json` 存在（step 5 archivist 产出）+ apply_archive 已回库角色/道具/关系/locked_facts/throughline/角色信念(belief_ledger)（step 6）
- [ ] `_数据库/.wal/cluster_<key>_summary.json` 存在（summarizer 产出 · 含 appraisal_beats 段）+ apply-appraisal-beats 已回填 叙事节拍器.appraisal_beats（step 10·无 beat 则 no-op）
- [ ] `_数据库/.judge_reports/cluster_<key>_foreshadower.json` 存在（含 specific_findings.dramatic_questions）+ apply-dramatic-questions 已回库 戏剧问题账本.json（step 10·无 dramatic_questions 则 no-op）
- [ ] Git commit `feat(cluster-NNN): N 章 (chX-chY)` 已落地
- [ ] `_数据库/.wal/cluster_<next_key>_emergence.json` 存在（emergence 产出）
- [ ] 2-3 张走向卡 brief 准备好展示给用户

---

# 硬性纪律

- 🔴 **你不修任何 .py 内部实现** — 出错报告用户
- 🔴 **你不跳任何 step** — required 14 步一个不漏
- 🔴 **agent prompt 只传契约字段** — 不塞规则/不塞 manifest
- 🔴 **v26 不降级 chapter mode** — save-state 单章命令/CLI 已删除
- 🔴 **不降级 factual 回库** — archivist（block）+ apply_archive（硬停）是状态回库唯一权威路径，缺角色必停

---

# Agent 不可用时的降级

- summarizer/foreshadower/reflector/outline-planner 失败 → 跳过该 agent · 记入报告（不阻塞 git commit）
- 🔴 **archivist 失败 → 必停**（factual 回库链断 · failure_policy=block · 不降级）
- 🔴 **apply_archive 缺角色报错 → 必停**（每个写完的 cluster 必有出场角色）
- writer_truth_check FAIL → 必修 · 停流水线
- validate_chapter hard_gate FAIL → 必修 · 停流水线
- git commit 失败 → 记入日志（不阻塞 emergence）

---

# 失败逃生舱

1. 重跑 plan：从 WAL 里 completed_steps 跳过已完成 → 从下一步开始
2. 若 WAL 损坏 → `python core/scripts/wal_recovery.py "<项目路径>" --cluster <key>`
3. 仍崩 → 报告用户人工介入。**🔴 v26 不降级到 chapter mode**——chapter mode 已彻底删除。

---

# 主代理优化（A 方案 · 跨家族 inline 复审 · 2026-06-20）

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
- 仅 `cluster_finale` 末 sub-cluster 触发（北极星② advisory）
- 默认 `shadow` 模式·永不阻断主链
- env `CROSS_FAMILY_JUDGE_MODE=off` 完全关·`active` 也仅做 advisory 落 outcome 元数据
- 主代理不在线（subprocess only / 批量自动跑）→ `maybe_run` 静默 skip + reason 提示

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
