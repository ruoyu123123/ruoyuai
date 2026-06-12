# 🔴 全局 feedback 规则汇编（随 exe 出货）

> 机械汇编自开发机 memory（~/.claude/projects/D--Desktop-ruoyuai/memory/feedback_*.md · 共 23 条）。
> 随 exe 出货：frozen 环境无开发机 memory 路径时，gen_writer/build_manifest fallback 读本文件。
> 更新方式 = 重跑汇编：`python core/scripts/assemble_global_feedback_rules.py`（🔴 不要手改本文件——改源 memory 后重新汇编）。
> 规则为用户定稿原文照搬·汇编只做格式搬运不增删改语义。
>
> 以下是历史用户反馈沉淀的全局禁令/规则，写作时**逐条遵守**。违反 = 出货后被打回 + lesson 复发。

---

<!-- FEEDBACK_RULE: feedback_author_goldstandard_comparison_gate.md -->
## feedback-author-goldstandard-comparison-gate

> description: 🔴 机械质检全过≠仿到位·必拿真作者原文金标准比调性/喜剧·情绪标点(感叹/问号/省略)偏低=喜剧引擎没落地代理信号·新书必拷skill双文件

用户 2026-06-04 要求「拿生成内容和作者原文对比 + 让 gemini 也一起分析哪里仿写不到位」时暴露的系统盲区（gemini + Claude 独立同诊验证）。

**核心教训**：**机械质检全过 ≠ 仿写到位**。《全城公测》cluster_001 audit waived / reflector final_pass / voice A级**全过**，但拿真作者原文金标准一比=**仅仿 3-4 成·写成了赛博惊悚而非市井喜剧**。因为现有质检全查机械维（句长/段长/禁用词/voice 一致性），**从不拿真作者原文比「调性/喜剧到位度」**。北极星：真作者原文=金标准（[[reference-system-validation-method-and-distill-maturity]] 同源）。

**How to apply（决策前置）**：
- 重大风格书每个 cluster 写完，**除机械质检外，必拿真作者原文/golden_passages 比调性**（量化 + 质性双轨）。量化走新建的 `core/scripts/replication_fidelity_check.py`（标点/句段 vs 作者基线·顾问制·已接 cluster-write step 3.1b）；质性走 gemini/agent 读 golden_passages 做"市井喜剧 vs 惊悚"类调性对比。
- 🔴 **情绪标点（感叹/问号/省略号）严重偏低 = 喜剧引擎没落地的可量化代理信号**（实证：跑偏稿 0.2/1.5/0.8 vs 作者 4.9/5.5/4.3 = 24x/3.7x/5x↓；修对后回到带内）。便宜、可验证、第一时间报警。
- **新书必拷作者风格「双文件」**：`作者风格_FINAL.json`→`作者风格.json`（量化）+ `skill_FINAL.md`→`作者风格_skill.md`（笔法+golden）。gen_writer 两个都读，**漏 skill = writer 只有干量化数字、缺笔法 → 跑偏成通用爽文**（cluster_001 头号根因·已修 outline.md + gen_writer 缺 skill 告警）。
- 跨栈对比有价值：让正式写作用的 gen-model（gemini-3.1-pro）也来诊断，比只 Claude 自评更能照出"目标模型仿没仿到"。

适用面：全局，尤其用现成风格库写新书。关联 [[feedback-inverted-modifier-sentence-mold-overuse]]（句法）/ [[feedback-smart-side-characters-no-dumbing-down]]（配角）。详见 `workspace/_temp_research/仿写对比/差距报告_合并.md`。

---

<!-- FEEDBACK_RULE: feedback_build_manifest_cluster_brief_injection_gate.md -->
## feedback-build-manifest-cluster-brief-injection-gate

> description: 🔴 新书必查·build_manifest 注入 cluster brief 取决于 cluster status 在不在 active 白名单·status=active/pending 不在→brief丢失→writer偏短偏离·附负例few-shot绑flash题材prior

写新书走 `/outline → /cluster-write` 时**两个会反复咬人的系统坑**（白事规则 2026-06-06 实战暴露·该书已删·经验留用）：

## 1. build_manifest cluster brief 注入闸（最容易让首块偏短偏离）

`build_manifest.py` 的 `inject_event_cluster_context` 只在 cluster 的 `status ∈ _EVENT_CLUSTER_ACTIVE_STATUSES`（= `in_progress/writer_done/splitter_done/done/进行中/已完成`）时才注入 brief（`scene_storyboard`/`scope_summary`/`narrative_mode`/`climax_hint`）到 writer manifest。注释里**故意排除** candidate 占位态（`pending/已规划/未涌现`）防误命中空 cluster。

**坑**：`/outline` 给 cluster_001 写的 status 可能是 **`active`**——一个**孤儿值**，既不在 active 白名单、也不是 candidate 排除集 → `event_cluster_context.mode="off"` → **storyboard/scope 全没注入** → writer 拿不到 5 幕分镜+硬约束 → 偏短偏离设计（白事规则首稿就这么跑偏的）。注意 `gen_writer.py` 另外**直接读 事件簇.json**（按 cluster_id 匹配·不看 status）拿 brief，所以主题对得上、但 manifest 层的 cluster 视野（audit_hub --mode cluster / reflector 等 consumer）全瞎。

**必做检查**：`/outline` 完成后、`/cluster-write` 之前，验
```python
json.load(open('_数据库/.manifest/ch_001.json'))['event_cluster_context']['mode'] == 'on'
```
若 `off` → 把 事件簇.json 对应 cluster 的 `status` 改成 `in_progress`（canonical 值·build_manifest/emergence/save-state 三方都认），重跑 build_manifest。**根治选项**（未做）：给 `_EVENT_CLUSTER_ACTIVE_STATUSES` 加 `active`（它不是 candidate 占位态·安全）；或让 /outline 直接写 `in_progress`。

## 2. 负例 few-shot 才绑得住 flash 的题材 prior

抽象约束（scope 写「无系统无面板」）压不过 gen-model（gemini-3.5-flash）的题材惯性——规则怪谈无限流 flash 会自动生成系统面板【任务栏】（甚至忠实执行作者 skill 里的 `<!--系统面板密度-->` 注释）。**改成负例 few-shot**（点名具体禁止形态：『绝不写【守则已加载】【团队成员XX已死亡】视网膜黑字/提示音』+ 给正确呈现方式）一次绑住。原则：弱模型「点名禁止的具体形态」比「抽象禁令」管用得多。配合 [[project-genmodel-flash-locked]]：flash 句长偏碎+单 cluster 偏短是天花板，每次 gen_fixer 改写必扫 `[A-Za-z]+` 兜底外文 token 泄漏（of/and 替换 的/和）。

## 3. 走向卡必对抗验证（self-assessment 会漏 blocker）

emergence + outline-planner 产的走向卡，outline-planner 自评常全 PASS 但漏真 blocker。用多 agent 对抗验证（每张卡×多视角·每个 skeptic 尽力反驳）能抓出：复用已消费的 ME / 提前烧未来高潮 / 把主角写成零失误金手指（违 protagonist_fallible）/ 配角软降智。白事规则 cluster_002 选卡时 9-skeptic 验证**推翻了 outline-planner 的自荐**。详见 [[feedback-smart-side-characters-no-dumbing-down]]。

---

<!-- FEEDBACK_RULE: feedback_cluster_distill_v2_charter.md -->
## feedback-cluster-distill-v2-charter

> description: 蒸馏闭环全 6 阶段对齐 cluster 主轨——废 drill 单段、复刻分 chapter/cluster 两档、Article 6 写作端回灌严闭环（不通过不出货）

# 故事块蒸馏 v2 章程（2026-05-26 拍板）

**Rule**：蒸馏闭环的复刻验证**必须**按写作端真正消费的颗粒度（cluster 3-6 章 / 4000-20000 字）。drill 单段模式废弃。最终出货前**必须**做一次"写作端回灌测试"，不通过 = 不出货。

**Why**：
- 写作端早就是 ECAS cluster 整块叙事（gen_writer.py --cluster N --chapter-start X --chapter-end Y），但蒸馏复刻验证还用 1200 字 6-type 段级（opening/battle/psychology/dialogue/description/transition）
- 等于"用单段成绩证明 cluster 写作能跑" — 闭环不闭，跟今日修的 `build_manifest.py:2298 writer_mode="single"` 同一类系统不对齐
- 用户原话「现在删的就一本了」（指 5 本已蒸馏库现剩惊悚乐园） + 决策"废弃 drill"+"Article 6 严闭环必要"

**How to apply**：
- ✅ 改：`distill_replicate.py` 接口（删 --type，加 --chapter-ref + --cluster-ref）/ `style_evaluator.py` 加 cluster mode / `distill-style.plan.json` 7→9 step / `distill-style.md` 文档同步
- ✅ 已蒸馏书惊悚乐园按 v2 升级（不动 v1.x skill，fork 为 skill_FINAL_v2.md）
- ❌ 不改：阶段 1 单章 JSON / 阶段 1.5 cluster_arc 聚合（这两阶段已是 cluster 主轨）
- 引用：[[feedback-single-mode-deprecated]] 写作端 single 废弃；[[feedback-no-investigation-no-voice-universal]] 决策前置

## 三档 → 两档

| 档 | 字数 | 状态 |
|---|---|---|
| ~~drill~~ | ~~1200 字 6-type~~ | ❌ 废 |
| **chapter** | 3000-5000 字 | ✅ v1→v2 中检 |
| **cluster** | 4000-20000 字 | ✅ v2+ 终验 |

收敛递进：chapter 通过 → 升 cluster → 通过才允许 plan_tracker end。

## cluster 复刻 timeout 防御

沿用 `cluster_segmenter` A' 半 cluster 教训（6 章 100% 502 EOF / 3 章 100% 成功）：cluster 复刻拆 **3 段 sub-call**（前半 / 后半 / 衔接段落），单 call ≤ 5000 字 / wall-clock ≤ 10 min。

## cluster 评分维度（style_evaluator --mode cluster）

保留 SFS（句长/段落/标点/功能词/对话占比），新增 6 维：
1. arc 形状拟合度（matched_reagan_shape 一致性）
2. emotion_curve_normalized 偏离度（cosine ≥ 0.7）
3. 衔接模板覆盖度（continuity 6 种 connection_type 命中率 ≥ 70%）
4. 钩子分布（kicker_count_per_chapter 形状一致）
5. 场景概述比（scene_summary_ratio 偏差 ≤ 15%）
6. cluster 内 voice_pack 合规

## Article 6 写作端回灌（严闭环 · 不通过不出货）

阶段 9（新增 plan step）：
- 复刻产物 cluster txt → 灌 gen_writer.py --cluster 用 skill_FINAL 写同 cluster
- 重跑 cluster_arc_aggregator → 与原 cluster_arc 比
- 通过标准：reagan_shape ≤ 1 等价类偏差 / SFS 差 ≤ 5
- 不通过 → distill_finalize_verify.py exit 2 → plan_tracker end 拦截

## 红线

1. ❌ 跳过 chapter / cluster 档直接 plan_tracker end → hook L3 拦
2. ❌ cluster 复刻用 Claude sub-agent 而非 gen-model（沿用 v22.cluster.3 规则 11）
3. ❌ 已蒸馏书做 cluster 重测时改动 v1.x skill 原版（必须 fork 出 skill_FINAL_v2.md）
4. ❌ 章程定型后 drill 模式不可复活（紧急救火走 --legacy-segment-only 一次性 flag + 留 lesson）

## 实施阶段

- **P0** 接口改造：distill_replicate.py + plan template + distill-style.md
- **P1** 评分 + 回灌：style_evaluator.py cluster mode + distill_finalize_verify.py
- **P2** 已蒸馏书升级：惊悚乐园 runbook 命令清单（用户手动跑）

---

<!-- FEEDBACK_RULE: feedback_default_all_subsystems_enabled_for_new_books.md -->
## feedback-default-all-subsystems-enabled-for-new-books

> description: 全局 superset 规则：新书默认开启全部 34 个核心子系统 JSON（hook 强制门禁拦截）·包含 ECAS / 世界演化 / Hub / Clock / Storyteller / Stress / 角色弧线 / 群像 / 事件池 / 知识图谱 / 副线 / 节拍图 / 四线脉络 / 网文基准 等

新书项目 `/outline` 命令完成时必须建齐**全部 34 个核心子系统 JSON**。否则 hook `pretooluse_subsystems_gate.py` 在 plan-end 前 `exit 2` 拦截。

**Why**：用户 2026-05-25 明令：「默认系统应该全面开启，这是需要加入hook步骤检测中的」。此前虽有 `feedback-default-ecas-for-new-books` + `feedback-default-world-evolution-for-new-books` 两条但仅覆盖 ECAS + 世界演化，剩余 14 个高级子系统（Hub/Clock/Storyteller/Stress/角色弧线 等）仍是按需启用——这违反了用户「全面开启」的明确意图。**默认开 = 不问用户，hook 强制；用户明说「轻量模式」才 opt-out**。

**How to apply**：

1. `/outline` 第 3 步 init-databases 必须建完 34 个 JSON，hook 门禁会在 plan-end 拦截缺失
2. 子系统清单（按分类）：

| 分类 | JSON | 何时启用 |
|---|---|---|
| **基础-人物世界（5）** | 人物卡 / 世界观 / 关系 / 地图 / 道具 | 必建 |
| **基础-叙事（7）** | 进度 / 章纲摘要 / 大势卡 / 事件簇（ECAS） / 事件表 / 时间线 / 伏笔表 | 必建 |
| **基础-风格质控（4）** | 作者风格 / 场景规则 / 写作经验 / 用户偏好 | 必建 |
| **基础-世界演化 v20.1（2）** | 世界状态 / 涟漪规则 | 必建（feedback-default-world-evolution）|
| **高级-Hub/Clock/Storyteller/Stress v21（4）** | 枢纽场景 / 时钟表 / 叙事节拍器 / 主角压力档 | **必建**（新规则）|
| **高级-角色弧线 + NPC 动态 v21+（3）** | character_arc_state / 角色行动表 / 群像档 | **必建**（新规则）|
| **高级-fluid 事件池（2）** | 事件池 / 行动判定模板 | **必建**（新规则）|
| **高级-v22 SE3 蒸馏（2）** | 角色池 / 角色烙印 | **必建**（新规则）|
| **高级-v23 长篇工具（5）** | knowledge_graph / subplot_threads / beat_map / 四线脉络 / webnovel_bench_mapping | **必建**（新规则）|

**共 34 个**（5+7+4+2+4+3+2+2+5 = 34）

3. **opt-out 机制**：用户明确说"轻量模式"/"短篇不需要这么多系统" → 主代理在项目根建 `_数据库/.subsystems_bypass.json`（任意 JSON 内容），hook 见此文件即放行
4. **初始化策略**：
   - 基础 18 个：必须有真实内容（人物卡至少有主角，世界观至少有 era/location，等）
   - 高级 16 个：可以是**最小骨架占位**（如 `{"_schema": "hubs_v21", "hubs": [], "_doc": "占位待补"}`），但**文件必须存在**
   - 占位文件可在后续 cluster 推进时按需详化
5. **模板参考**：
   - `core/claude-home/templates/examples/scp_anomaly_bureau/` —— 含 character_arc_state.example.json
   - `core/claude-home/templates/examples/urban_supernatural_business/` —— 含 主角压力档/时钟表/枢纽场景/涟漪规则/群像档/事件池/角色行动表

**Hook 实现**：
- 路径：`core/claude-home/hooks/pretooluse_subsystems_gate.py`
- 触发：`Bash` + 命令含 `plan_tracker.py end <plan_id>` 或 `plan_tracker.py step <plan_id> --n 3|4`
- 条件：plan_id 含 `_outline_` 子串（其他命令不拦）
- 行为：检查 `<project>/_数据库/` 是否含全 34 个 JSON，缺即 exit 2

**取代规则**：本规则是 `[[feedback-default-ecas-for-new-books]]` + `[[feedback-default-world-evolution-for-new-books]]` 的 superset。前两条仍然有效但本条是全集。

**相关规则**：
- [[feedback-no-investigation-no-voice-universal]] 没调查没发言权
- [[feedback-no-micro-task-workaround]] 不绕基建故障
- [[feedback-one-sentence-per-paragraph]] 段落规则

**翻车实例**：《赖活》cluster_001 完成后用户发现项目缺世界演化 → 我提议只补 Tier S 2 个 → 用户驳回「默认系统应该全面开启」→ 升级为 hook 强制 34 个全建。教训：subsystems 不能按 Tier 推荐，要"all or nothing"。

---

<!-- FEEDBACK_RULE: feedback_default_ecas_for_new_books.md -->
## feedback-default-ecas-for-new-books

> description: 全局：新书项目默认启用 ECAS 故事块写作模式（事件簇.json + 用户偏好.ecas_config），不询问，不默认 single

新书项目默认启用 **ECAS 故事块写作模式**。`/outline` 时必须同时建：
- `_数据库/事件簇.json`（schema v23.1，至少 cluster_001 含 scene_storyboard）
- `_数据库/用户偏好.json` 顶层 `ecas_config: {ecas_enabled: true, cluster_word_range: {default_min: 16000, default_max: 22000}, mid_checkpoint_interval: 4500, ...}`

**Why**：用户 2026-05-25 在《赖活》项目明确反馈"我不是强制要求走故事块生成吗，这里为什么又是按章节走了"——/outline 没问 ECAS 偏好默认走 single，是隐性违反用户意图的失误。事后用户实测 ECAS（18000 字一次性产出 + splitter 切多章）比 single（3000 字单章）章节节奏更连贯、伏笔铺设密度更高、writer 自由度更大。

**How to apply**：
- `/outline` 第 3 步 init-13-databases 必须建 事件簇.json（至少占位）+ 用户偏好.json.ecas_config
- 不问"要不要 ECAS"，直接默认开
- 用户明确说"按单章写"或"single 模式" → 才不开
- cluster_001 默认覆盖 ch1-3/4/5 黄金三章一簇（具体几章看 ME 推进节奏）
- ecas_config 默认值：
  ```json
  {
    "ecas_enabled": true,
    "cluster_word_range": {"default_min": 16000, "default_max": 22000, "critical_event_min": 20000, "critical_event_max": 26000},
    "extended_thinking_critical": true,
    "mid_checkpoint_interval": 4500,
    "max_checkpoint_failures": 3,
    "cluster_stop_frequency": "per_cluster",
    "sub_summary_enabled": true,
    "auto_cluster_size_estimation": true
  }
  ```
- 模板见 `core/claude-home/templates/examples/scp_anomaly_bureau/事件簇.example.json` + `用户偏好.example.json`

**相关规则**：
- [[feedback-default-world-evolution-for-new-books]] 新书默认启用世界演化
- [[feedback-no-investigation-no-voice-universal]] 没调查没发言权（/outline 时该问 ECAS 偏好就问）
- [[feedback-one-sentence-per-paragraph]] 一段一句末结束符（ECAS 写作时强制）

**翻车实例**：《赖活》v1 single 模式（4 轮 polish 才放行）→ 用户拒收 → 切 ECAS v2（1 次 polish 通过 + 自然涌现 2 个有戏 NPC + 节奏更克苏鲁）→ 用户认可 ECAS 是正解。

---

<!-- FEEDBACK_RULE: feedback_default_no_step_skipping_for_new_books.md -->
## feedback-default-no-step-skipping-for-new-books

> description: 全局硬规则：禁止主代理跳 plan step / 用 --skip-output 当万能逃避 / 假装 spawn agent 后直接 plan_tracker step。所有 plan template 步骤默认必跑，optional 不等于「可以跳过」而是「场景不适用时跳」

主代理对任何 plan template 的步骤**默认必跑**，禁止以下 4 种跳步行为：

1. **`--skip-output` 万能逃避**：plan_tracker step 命令带 `--skip-output` 时，如果该 step 模板的 `expected_outputs` 非空 → hook 直接 exit 2 拦截
2. **假装 spawn agent**：声称"已 spawn novel-summarizer"后直接 `plan_tracker step --n 5`，但 `_数据库/.wal/第N章_summary.json` 或 `_数据库/.judge_reports/ch_NNN_<agent>.json` 不存在
3. **optional step 当 skippable**：`optional: true` 不等于「可以跳过」，是「场景不适用时跳」。99% 场景适用 → 99% 时间都要跑
4. **plan template 缺 must_spawn_agent / skip_output_allowed 字段** → 默认全部 must spawn / 禁 skip_output

**Why**：用户 2026-05-25 原话：「我功能做出来不是让你跳的」。本次会话主代理多次出现「最小框架/最小步骤」行为：
- save-state step 5/6/7 (summarizer/foreshadower/reflector) 多次直接 `--skip-output` 通过（实际 spawn 了但 plan_tracker 没校验输出）
- 跨章 scanner 15 个里跑 10 个就过
- 「为了效率」省 hook 检测

这违反用户「所有系统功能都要执行」的明确意图。用户做的 34 个子系统 + 12 步 save-state + 串行流水线 = **设计本意就是全跑**，主代理无权裁剪。

**How to apply**：

1. **CLAUDE.md 加全局规则**：「禁止跳步」段（与「没调查没发言权」并列为最高元规则）
2. **plan_tracker.py 升级**：
   - `step_complete()` 即使 `--skip-output` 也要校验 expected_outputs（如非空）。例外：plan template 该 step 显式 `skip_output_allowed: true`
   - `end_plan()` 校验 optional steps：如有 expected_outputs 或 must_spawn_agent 字段 → 必须真存在/真有 JudgeReport
3. **新 hook `pretooluse_plan_step_anti_skip.py`**：
   - 触发：Bash 含 `plan_tracker.py step ... --skip-output`
   - 校验：plan template 该 step 的 `expected_outputs` 非空且 `skip_output_allowed != true` → exit 2 拦截
4. **plan template 全部加字段**：
   - `must_spawn_agent: <agent_name>`（如 step 5 = novel-summarizer）—— end_plan 校验对应 JudgeReport 存在
   - `skip_output_allowed: false`（默认）—— 显式 true 才允许 --skip-output
5. **agent JudgeReport 路径约定**：
   - novel-summarizer → `_数据库/.wal/第NNN章_summary.json` 或 `_数据库/.wal/cluster_<id>_summary.json`
   - novel-foreshadower → `_数据库/.judge_reports/ch_NNN_foreshadower.json` 或 `_数据库/.judge_reports/cluster_<id>_foreshadower.json`
   - novel-reflector → 同上 reflector
   - novel-reading-reflector → `_数据库/.reading_reflection/ch_NNN_round_N.json` 或 `cluster_<id>_round_N.json`
   - novel-voice-checker → `_数据库/.judge_reports/ch_NNN_voice-checker.json`

**唯一豁免情况**：
- 项目级 `_数据库/.subsystems_bypass.json` 存在（用户明示「轻量模式」）—— 此时全 hook 旁路
- 单次 plan 在创建时显式标 `--allow-skip-steps`（保留接口但不推荐）

**相关规则**：
- [[feedback-default-all-subsystems-enabled-for-new-books]] 全系统强制开启
- [[feedback-default-ecas-for-new-books]] ECAS 默认
- [[feedback-default-world-evolution-for-new-books]] 世界演化默认

**翻车实例**：本次会话《赖活》save-state ch1 step 11 plan-end FAIL（缺 002 fate_cards 路径渲染）→ 我手动 patch plan JSON 改 expected_outputs 路径再 reattest 通过 = 实质跳步。正确做法是先修 plan_tracker 的占位符渲染 bug，让 hook 真正拦截。

---

<!-- FEEDBACK_RULE: feedback_default_world_evolution_for_new_books.md -->
## feedback-default-world-evolution-for-new-books

> description: 全局：新书项目默认启用世界演化系统（世界状态.json + 涟漪规则.json + 走向卡→世界涟漪应用链），不询问，不默认 SKIP

新书项目默认启用 **世界演化系统**。`/outline` 时必须同时建：
- `_数据库/世界状态.json`（schema v20.1，含 factions_state / active_npc_threads / consequence_tracker / emergent_opportunities）
- `_数据库/涟漪规则.json`（schema ripple_rules_v20_1，至少为每个 ME 配 1 条 ripple_rule）

**Why**：用户 2026-05-25 明令：「写入系统全局规则，新书默认开启故事块撰写，**默认启用世界演化**」。世界演化是 fluid 涌现叙事的核心 — 走向卡选择 → 触发涟漪 → factions_state 数值/NPC 动作/emergent_opportunities 更新 → 下章 manifest 自然反映用户选择带来的世界变化。不开 = 走向卡选择没有持续后果 = 副本世界变成静态背景板。

**How to apply**：
- `/outline` 第 3 步 init-13-databases 必须建 世界状态.json + 涟漪规则.json
- 不问"要不要世界演化"，直接默认开
- 用户明确说"短篇不需要"或"线性叙事" → 才不开
- 世界状态.json 初始化结构：
  ```json
  {
    "_schema": "world_state_v20_1",
    "_doc": "世界基线 + 演化追踪",
    "factions_state": {
      "<势力名>": {"power": 0, "stability": 0, "visibility": 0, "_doc": "..."}
    },
    "active_npc_threads": [],
    "consequence_tracker": [],
    "emergent_opportunities": [],
    "world_baseline": {"start_ch": 1, "snapshot": "..."}
  }
  ```
- 涟漪规则.json 初始化结构：
  ```json
  {
    "_schema": "ripple_rules_v20_1",
    "ripple_rules": [
      {
        "id": "RR_001",
        "trigger_type": "fate_event | minor_event",
        "trigger_match": "<ME_ID 或 卡片 label>",
        "ripples": [
          {"target": "factions_state.<f>.power", "delta": -2, "reason": "..."},
          {"target": "active_npc_threads", "add_thread": {...}},
          {"target": "emergent_opportunities", "spawn": {...}}
        ]
      }
    ]
  }
  ```
- 模板见 `core/claude-home/templates/examples/urban_supernatural_business/涟漪规则.example.json`
- write-chapter 流水线 step 0 会自动跑 `world_evolution_apply_card.py` → 把用户选择的涟漪落地到世界状态
- save-state step 9 会自动跑 `world_evolution_apply_chapter.py` → 把章末事件涟漪应用

**相关规则**：
- [[feedback-default-ecas-for-new-books]] 新书默认启用 ECAS 故事块
- [[feedback-no-investigation-no-voice-universal]] 没调查没发言权

**翻车实例**：《赖活》cluster_001 完成后跑 world_evolution_apply_card.py 报 `[SKIP] 项目未启用世界演化 (世界状态.json/涟漪规则.json 缺失)` → 走向卡 A 的选择没有触发任何世界涟漪 → cluster_002 manifest 拿不到「主角选 A 后世界发生了什么变化」的反馈 → 退化成线性叙事。

---

<!-- FEEDBACK_RULE: feedback_dialogue_quote_distill_bug.md -->
## feedback-dialogue-quote-distill-bug

> description: "全局：蒸馏作者风格时 LLM 容易把元数据描述里的引号写成「」（鱼眼角引号），但目标作者原文用 \"\" 中文双引号。必须在蒸馏后核验原文真实引号 + 入项目用户偏好.style_preferences.dialogue_quote_style 锁定 + writer/splitter 链路全部校验。"

# 蒸馏对话引号 bug

## 规则
蒸馏作者风格档案（`workspace/styles/<作者>/作者风格.json` + `skill.md`）后，必须用 Python codepoint 校验目标作者原文真实对话引号样式（U+201C/U+201D 中文双引号 ""，U+300C/U+300D 鱼眼角「」，ASCII " 等），不能用 LLM 总结的 markdown/json 字段里的引号当作真实样式——LLM 总结时常把示例用「」包，导致后续 writer agent 学错。

## Why
2026-05-27 案例：用户启动新书 /write 选定蛊真人风格，writer 写 cluster_001_draft.txt 全部用「」做对话。用户当场指出蛊真人原文用的是 ""。复核 `workspace/styles/蛊真人/原文/第001章.txt`：U+201C × 29 / U+201D × 29 / U+300C × 0 / U+300D × 0。蒸馏档案 `作者风格_FINAL.json` 有 629 处「（蒸馏 examples 描述包裹符）/ skill.md 有 38 处。writer 学的是档案里的「」，不是原文 ""，全 cluster 对话格式错。

## How to apply

### 校验时机
- /distill-style step 5（archive 前）必须跑 codepoint 校验，不一致即报警
- /write step 1（风格基线复制到项目 _数据库/）后立刻校验
- /outline step 3 init-34-subsystems 时 fix_quotes.py 兜底批跑（人物卡/作者风格/skill/大纲全扫）

### 校验脚本（参考 workspace/_temp_research/fix_quotes.py）
```python
LEFT_CORNER = chr(0x300C); RIGHT_CORNER = chr(0x300D)
LEFT_DQ = chr(0x201C); RIGHT_DQ = chr(0x201D)
# 1. 扫目标作者原文，统计真实样式
# 2. 扫项目风格档案，对比
# 3. 不一致 → 自动批量替换 + 入用户偏好 style_preferences.dialogue_quote_style 锁定
```

### 项目级锁定
项目 `_数据库/用户偏好.json.style_preferences[]` 加：
```json
{"key": "dialogue_quote_style", "value": "中文双引号 “” (U+201C / U+201D) 仅用作真对话；非对话用其他标点", "user_decided": true}
```
后续 cluster-write step 2 spawn writer 时 build_manifest 应将此约束 inject 到 prompt。

### 引号分用途规则（2026-05-27 用户补充）
- 真对话："..."（U+201C / U+201D 中文双引号 · 唯一用途）
- 内心独白 / 自言自语：「...」（U+300C / U+300D 鱼眼角）或 ——...—— 破折号
- 专有名词 / 招牌 / 案件名：《...》或〈...〉或不加引号
- 引文 / 强调：『...』双角引号
- writer prompt 必须严守"分用途"，不允许全部退化到 ""

### 修复同时要扫的文件清单
- `_数据库/作者风格.json`
- `_数据库/作者风格_skill.md`
- `_数据库/人物卡.json`（voice_pack.style_samples 也含引号）
- `大纲.md`（描述里的对话示例）
- `章节/cluster_<key>_draft/cluster_<key>_draft.txt`（已写的草稿）

## 相关
- [[feedback-no-investigation-no-voice-universal]]：实证支撑——发现引号问题必须 codepoint 验证原文，不能凭 LLM 自报
- [[feedback-dialogue-quote-unicode-distinction]]：拆段脚本必须 codepoint 区分左/右引号

---

<!-- FEEDBACK_RULE: feedback_dialogue_quote_unicode_distinction.md -->
## feedback-dialogue-quote-unicode-distinction

> description: 全局：批量「一段一句末」拆段脚本必须用 Unicode 区分中文左/右双引号（U+201C/U+201D 不能视为同字符）；对话段（含未闭合引号）禁止按句末符拆段

# 对话引号 Unicode 区分硬规则

**用户原话**（2026-05-26）："这是什么情况，为什么对话换行了"我叫赵海，当过兵。\n这是第三个副本。\n"他拍了拍年轻女人的肩膀..."

## 根因

cluster_002 的「一段一句末」批量拆段脚本误把对话内部的句号当作段落分界，导致：

```
"我叫赵海，当过兵。
这是第三个副本。
"他拍了拍年轻女人的肩膀，"她叫小周。
"
```

应该是：
```
"我叫赵海，当过兵。这是第三个副本。"他拍了拍年轻女人的肩膀，"她叫小周。"
```

**Why（具体原因）**：
1. 我用 `raw.startswith(('「', '"', '"', '【', '"'))` 检测对话段——但漏掉了中文智能双引号 `"` (U+201C 开) 和 `"` (U+201D 闭)
2. 即使写了 `'"'` 在源码，Python 源码编码会让两个不同字符看起来一样，但 `count()` 时左右引号不同 → 不能用 `text.count('"')` 当作"任意双引号计数"
3. 209 处 cluster_002 对话被错拆，跨 ch5-ch8 全部受影响

## How to apply

### 批量拆段脚本（一段一句末 / 段长拆 / 多句末拆）必须：

**1. 用 Unicode codepoint 区分**：
```python
OPEN = '“'   # U+201C
CLOSE = '”'  # U+201D
opens = text.count(OPEN) + text.count('「')   # 左引号
closes = text.count(CLOSE) + text.count('」') # 右引号
```

**2. 对话段保护规则**：
```python
# 若段落含未闭合引号 (opens > closes) → 段不拆 + 与后续段合并直到闭合
if opens > closes:
    skip_splitting()
    merge_until_closed()
```

**3. 段内 \n 也要保护**：
- 即使段在 `\n\n` 分界内，若内部 `\n` 单换行切开了未闭合引号，也必须合并
- 修复脚本范例：`merge_broken_dialogue_lines()` (本次修复用的)

### 禁止操作

- ❌ 不要用 `text.count('"')` 当万能双引号计数（U+201C ≠ U+201D 在源码中可能看起来一样）
- ❌ 不要写仅检测 `startswith('「')` 的对话识别（漏 `"` 智能引号 和 `」` 闭合引号开头的接续段）
- ❌ 不要按段长 / 句末符无差别拆所有段，必须先识别 + 跳过对话段

### 调研先行

写批量正文修复脚本前，必须：
1. `python -c "for c in open(file).read(): print(hex(ord(c)))"` 抽样验证实际 Unicode codepoint
2. 用 grep 找一个真实样本观察其结构再写规则
3. 修复后 grep 一遍确认目标已修

## 翻车实例
- 2026-05-26 cluster_002 拆段：209 处对话被错拆 → 用户看 ch5 第一眼就发现
- 根因不是「写代码不细心」，是「**没用 Unicode codepoint 验证假设**」（违反「没调查没发言权」元规则）

---

<!-- FEEDBACK_RULE: feedback_distill_sfs_multi_ref.md -->
## feedback-distill-sfs-multi-ref

> description: 蒸馏 phase-3 SFS 评分必须用多基线（--multi-ref-from-dir），单 ref 评分对高方差作者必失真

phase-3 风格保真评分必须用多基线模式 `style_evaluator.py --multi-ref-from-dir <原文目录> --multi-ref-count 5`，禁止用单 ref（`--ref 第NNN章.txt`）。

**Why**：用户原话「SFS 是机械量化（句长/段长/标点/词频），对蛊真人的「短段独行 + 引号独白 + 主题章型」惩罚过重」。根因 = 单 ref 模式强制对齐**单一章型**指标，但作者不同章型方差极大（对话章 50% vs 战斗章 5% vs 独白章 9%），单 ref 把"对话章 28%" 当唯一目标 → 复刻独白章被骂"对话占比偏低 -14%"。

**实证**（蛊真人 2026-05-27 v0-v4 全版本回测）：
- v0 67.98 → **84.98** (+17.00)
- v1 69.83 → **83.60** (+13.77)
- v2 60.69 → **78.12** (+17.43)
- v3 55.56 → 69.17 (+13.61)
- v4 54.62 → **70.54** (+15.92)

修复后**无任何一版是真 D 级**（v0/v1 是 C+ 近 B-），证明退步**完全是测量失真**。

**How to apply**：
1. phase-3 调 style_evaluator.py 时**必须** `--multi-ref-from-dir <project>/原文 --multi-ref-count 5`（数据 ≥ 60 章默认走）
2. 数据 < 30 章可用单 ref（数据不足）
3. 看 phase-3 SFS 报告时，先检查是否 `multi_baseline: true`，false 则报告不可信
4. 详见 `core/claude-home/lessons/distill-style-lessons.md` L19.1
5. 修复代码：`core/scripts/style_evaluator.py:733+`（v23.13 加 --multi-ref-from-dir / --multi-ref-count / --multi-ref-seed）

**Why not affect 写作**：写作流水线（`/cluster-write` → `gen_writer` → `audit_hub`）**完全不调** `style_evaluator.py`（蒸馏独有）。写作调的是 `validate_style.py`，它的 `_apply_style_overrides()` 自动从 `作者风格.json.quantitative` 校准阈值（dialogue_ratio mean ± 0.15 等），蛊真人风全在带内。hard_gate 只有 `STYLE_单段超长`（蛊真人短段永远不触发）。所以蒸馏 SFS 失真**不影响正式写作**。

关联：[[feedback-no-investigation-no-voice-universal]]（没调查没发言权 — 单 ref 失真本身就是没调研到真实方差）

---

<!-- FEEDBACK_RULE: feedback_fluid_cluster_emergence_not_predesign.md -->
## feedback-fluid-cluster-emergence-not-predesign

> description: 全局设计哲学：事件簇必须 fluid 涌现，不预设详细 N+ cluster。outline 阶段只详化 cluster_001 + 大势卡 ME 池，cluster_002+ 在每个 cluster 完成时基于涟漪+主角 arc+世界状态动态涌现。与 v23.12 fluid 章数同源

事件簇（cluster）不要预设。`/outline` 阶段：
- **只详化 cluster_001**：含完整 scene_storyboard + scope_summary + anchor_props + foreshadowing_to_plant
- **大势卡 ME 池**：保留完整 ME 池（V1-V5 全部 ME）—— 这是"大势牵引"的方向
- **cluster_002~005**：仅占位骨架 `{cluster_id, status: "未涌现", _doc: "等 cluster_001 完成后涌现"}`

每个 cluster 完成时**动态涌现下一个 cluster**：
- `cluster-save-state` 末尾跑 `cluster_emergence_engine.py`
- 输入：当前 cluster 完成后的 世界状态.json + consequence_tracker + 用户走向卡选择 + 涟漪规则触发结果 + character_arc_state（lie_breaking 等阶段变化）
- 输出：扫大势卡剩余 ME，找符合「当前世界状态 + 涟漪后果 + 主角 arc stage」的 ME 子集（1-3 个），生成下一 cluster_brief 候选（2-3 个让用户选）
- 写到 `事件簇.json.clusters[N+1]`（status: "candidate"）

**Why**：用户 2026-05-25 原话：「事件簇会随着故事块的发展而越来越多，因为会通过当前人物的变化和其他的变化，碰撞出各种情况，所以一开始没必要太多事件生成，其他内容也是，有个主要矛盾自然会带出其他内容，有大势牵引着不会跑偏，这就是涟漪效应」。

**Why（实证）**：
- 《赖活》outline 预设 5 cluster + cluster_002 scene_storyboard 4 scene 全部详化
- 结果 writer 实际写出 cluster_002 时偏离 storyboard 大：
  - 计划：陆雨楼留 cluster_003 出场 / 实际：陆雨楼 cluster_001 ch3 已物理出场
  - 计划：FS_011 蛛网戒 ch5 anchor / 实际：连续 3 章 anchor 缺失
  - 计划：陈漾 cluster_002 中段入场 / 实际：cluster_002 ch5 才入场
- 这证明 cluster 详细预设 = writer 写时偏离 = reconcile 修档案 5 处的成本

**fluid 涌现的优势**：
- writer 拿 manifest 时只看 event_cluster_context.current_cluster（涌现产生的，跟当前世界状态对齐）
- 主角 arc 真实推进后才生成下一 cluster brief，不会有「过早设计的 cluster_002 已被 cluster_001 涟漪推翻」问题
- 涟漪规则真正发挥作用（不只是 factions_state 数值变化，还驱动剧情结构）

**How to apply**：

1. **outline.plan.json step 3**：expected_outputs 只要 `事件簇.json.clusters[0]` 完整，clusters[1+] 仅占位骨架即可
2. **新脚本 `core/scripts/cluster_emergence_engine.py`**：
   - CLI: `python cluster_emergence_engine.py <project> emerge --after-cluster <id>`
   - 读：大势卡.json（ME 池）+ 世界状态.json（factions_state/consequence_tracker）+ character_arc_state.json + 涟漪应用日志（world_evolution_apply_*）
   - 算：剩余 ME 中找符合当前 stage 的 candidate（用 lie_cracking → setback ME / lie_broken → recovery ME 等）
   - 写：`事件簇.json.clusters[N+1]` 含 2-3 个 candidate brief（scope_summary + parent_me + scene_storyboard 雏形）
3. **cluster-save-state.plan.json step 11 改造**：
   - 不再 spawn novel-outline-planner 出 fate_cards.json
   - 改 spawn novel-outline-planner MODE=cluster_emergence
   - 输出：`_数据库/.wal/cluster_<id+1>_emergence.json`（含 candidate cluster brief 列表）
4. **novel-outline-planner agent 加 MODE=cluster_emergence**：
   - 输入：CURRENT_CLUSTER + EMERGENCE_CONTEXT_PATH
   - 输出：2-3 个 candidate cluster_brief（不是单章走向卡）
   - 主代理展示给用户选 1 个，写入 事件簇.json

**例外**：
- 短篇项目（用户明示「线性叙事」）→ outline 可预设所有 cluster
- 已确定 cluster 顺序的 IP 改编 → 同上

**相关规则**：
- [[feedback-default-ecas-for-new-books]] ECAS 默认（cluster 写作机制）
- [[feedback-default-world-evolution-for-new-books]] 世界演化（涌现的输入）
- v23.12 fluid 章数（章数也是涌现，与 cluster 涌现同源哲学）
- [[feedback-default-no-step-skipping-for-new-books]] 禁跳步

**翻车实例**：见 Why（实证）段——《赖活》cluster_002 4 scene 全部预设 → writer 偏离 → reconcile 修档案 5 处 + cluster_002 实际产出与 cluster_brief 对不上 1/2。

---

<!-- FEEDBACK_RULE: feedback_full_system_cluster_centric.md -->
## feedback-full-system-cluster-centric

> description: 🔴 v2 cluster 化方案 (2026-05-28) · 全系统从 chapter 单位升级为 cluster 单位 · 检测/数据/脚本/蒸馏全栈 cluster 化 · 用户原话「整个系统为故事块服务，章节只是输出格式」

# 全系统 cluster 化（v2 · 2026-05-28）

## 根本原则（用户原话）

「**chapter 层的检测可以放故事块检测中，我不需要单章节的检测，整个系统是为故事块服务的，章节只是输出内容的格式单位**」

→ 全栈对齐：cluster 是逻辑写作 + 检测 + 数据 + 脚本主单位 · chapter 仅保留在物理产物文件名。

## Why

v22→v24 cluster mode 转型只改了写作单位 + plan template，遗留：
1. 9 个 scanner 全用 chapter 视野 → cluster 草稿（11k-25k）跑出 15 条 advisory 必报（数据维度跟检测维度错配）
2. 12 个数据库 JSON schema 含 _ch 字段污染 → splitter 切完章数变化后 chapter_plan 失效
3. 22 个 cross_chapter_*_scan + 蒸馏 chapter 中检 + 10+ H 类辅助脚本仍 chapter 视野

## 三份方案 + 11 个 task 落地

### Phase A 紧急止血（1 天 · P0）
- ✅ T#11: audit_hub waiver 截断 100→300 + 11 类 chapter 视野 code 黑名单跳过
- 效果：cluster_001 advisory 15→3

### Phase B 底盘升级（8-10 天 · P1）
- ✅ T#13: 9 个 scanner 升维 cluster 视野 + audit_hub 单层调度
  - validate_style 11 处阈值比例化（chapter_words 8000-30000 / dialogue_ratio 0.15-0.70 / long_para 比例化）
  - hook_strength 改造为 cluster 拟切点节奏（不再单章末段）
  - golden_three 改造为 cluster_001 开场强度（虚拟 ch=9000）
  - plot_structure_scanner 4 个 scan：first_cluster_tolerance + secrets epistemic_class 读取 + cluster_beats
  - validate_chapter LONG_MONOLOGUE 阈值 0→5 (cluster 视野)
  - scanner_registry.json 极简单层声明
  - CLUSTER_MODE=1 env 子进程传递管线
- ✅ T#12: 数据库 schema v2 + migration（migrate_data_model_v2.py）
  - 12 个 JSON 字段 _ch → _cluster + scene_index
  - 章纲摘要.json → 故事块摘要.json
  - 进度.chapter_plan → cluster_blueprint 按 cluster 分组
  - 自动 backup _数据库.bak.YYYYMMDD_HHMMSS
- ✅ T#14: 蒸馏 8 phase → 7 phase（删 phase-2 chapter-replica）+ distill_replicate chapter 模式 deprecated

### Phase C 链路重构（10-14 天 · P1）
- ✅ T#15: 写作流程 cluster_id 全链路（gen_writer 读 cluster_blueprint + build_manifest 加 chapter_plan cluster_blueprint 优先 + KNOWN_DBS 加故事块摘要）
- ✅ T#16: 4 个新 cluster-only scanner（cross_scene_voice_drift / foreshadowing_handoff / locked_fact_cross_scene / pov_consistency）· 每个 150-300 行 · scanner_registry 注册
- ✅ T#17: 22 个 cross_chapter scanner 加 CLUSTER_MODE flag + run_cross_cluster_aggregates.py 新入口 alias

### Phase D 收尾联动（6-9 天 · P2）
- ✅ T#18: cluster-write plan v2 + CLAUDE.md 顾问制段更新（cluster 单层架构说明）
- ✅ T#19: validate_chapter 兼容 obtained_cluster / learn_at_cluster
- ✅ T#20: chapter_plan_compliance_scan 读 cluster_blueprint 优先
- ✅ T#21: 全栈回归 + lesson 入库

## How to apply

### 数据模型规范（v2 cluster-centric）

| 字段类型 | 旧（v1）| 新（v2）|
|---|---|---|
| 章号 | `ch: 5` | `cluster: "cluster_001", scene_index: 4` |
| 角色登场 | `first_appear_ch: 1` | `first_appear_cluster: "cluster_001"` |
| 伏笔埋设 | `setup_ch: 3` | `setup_cluster: "cluster_001"`, `setup_scene_index: 5` |
| 秘密揭露 | `reveal_at_ch: 20` | `reveal_at_cluster: "cluster_004"` |
| 物理产物 | `第NNN章.txt` | ✅ 保留（出版习惯）|

### scanner 开发原则

1. 默认 cluster 视野（CLUSTER_MODE=1 时不感知也至少不误报）
2. 物理产物相关（chapter_io / split_cluster_changes / gen_chapter_titles）保留 chapter
3. 用 `IS_CLUSTER_MODE = os.environ.get("CLUSTER_MODE") == "1"` 切阈值
4. scanner_registry.json 注册 `layer: cluster` 或 `cross-cluster`
5. cluster_001 首块自动宽容（PLOT_subplot / PLOT_arc 等容忍）

### 项目 migration 流程

```bash
# dry-run 检查
python core/scripts/migrate_data_model_v2.py <project>  --dry-run

# 实跑（自动 backup）
python core/scripts/migrate_data_model_v2.py <project>

# 回滚
python core/scripts/migrate_data_model_v2.py <project> --rollback _数据库.bak.YYYYMMDD_HHMMSS
```

### 蒸馏新流程（v3 cluster 单轨）

7 phase（删 phase-2 chapter-replica）：
1. preprocess
2. surface-distill-cluster
3. multi-dim-compare-cluster-only
4. correction-reflect
5. cluster-replica（唯一终验）
6. finalize
7. writer-feedback-verify

## 效果（cluster_001 实测）

| | 修前 | 修后 |
|---|---:|---:|
| advisory 误报数 | 15 | 3（真问题）|
| hard_gate 误报 | 3 | 0 |
| waiver 截断警告 | 2 次 | 0 次 |
| cluster 视野真 issue 检测 | 0 类 | 4 类（voice_drift/foreshadowing_handoff/locked_fact/pov）|

## 三份方案文档

- workspace/_temp_research/system_redesign_detection_layer.md（检测层）
- workspace/_temp_research/system_redesign_data_model_cluster_centric.md（数据模型）
- workspace/_temp_research/system_redesign_scripts_and_distill_cluster.md（脚本+蒸馏）

## 相关 lesson

- [[feedback-default-no-step-skipping-for-new-books]]：plan template anti-skip 拦截
- [[feedback-fluid-cluster-emergence-not-predesign]]：cluster fluid 涌现
- [[feedback-v27-writer-freestyle-splitter-word-cut]]：writer freestyle + splitter 字数切
- [[feedback-default-all-subsystems-enabled-for-new-books]]：34 子系统强制开启

---

<!-- FEEDBACK_RULE: feedback_inverted_modifier_sentence_mold_overuse.md -->
## feedback-inverted-modifier-sentence-mold-overuse

> description: 🔴 写作工艺盲区：「前置长定语+的+主语后置」倒装句式模具过用(摸出手机的陆参/愣住的陆参)·机械scanner查不出(只查同主语streak漏查同语法骨架复用)·gen_fixer comprehensive可治

reading-reflector 在《全城公测，就我开了双号》cluster_001 抓到的结构层盲区（2026-06-04·两轮反复点名建议沉淀）：

**问题**：gen-model（gemini-3.1-pro）写作时倾向用同一个**「前置长定语从句 + 的 + 主语后置」**语法模具反复浇句子——如「摸出手机的陆参发现…」「愣住的陆参抬起头…」「端起塑料杯喝完半杯豆浆的陆参站起身…」「没有去接湿巾的陆参…」。单句没问题，连读极单调，是「作文感/塑料感」的一种结构变体。cluster_001 实测 34 次（约 1/9 叙事段）。

**Why 检测盲区**：机械 scanner（`prose_rhythm_scanner` / `feedback_paragraph_head_subject_monotony`）只查**同主语连续段 streak**——这种倒装句把主语后置，段首是定语不是主语，streak=0 全过；但每段用同一**语法骨架**。同主语 streak ≠ 同语法骨架复用。**这是现有 scanner 的真实缺口**（值得未来加「段首句法骨架多样性」探针）。

**How to apply**：
- writer prompt / gen_fixer 注意提示句式骨架多样化，别让「X的[主语]」倒装霸占段首。
- 修复手段实证：`gen_fixer.py --mode comprehensive --report-file <reading-reflector R*.json>` **能治**（cluster_001 实测倒装 64→13、强度副词 极其28→3/瞬间16→7/死死9→3）。⚠️ 而 `--mode validator-repair`（brief 驱动·全文回显）在 13k 草稿上**稳定 no-op**（gen-model 偷懒回显空 changelog·重试无效）——这是已知 gen_fixer 失败模式，遇到改走 comprehensive 或确定性应用 validator 已写明的精确替换。
- 关联：强度副词通胀（极其/瞬间/死死/毫无）+ 否定式倒装对话标签（没有X的陆参）是同一批 gen-model 写作伴生套路，一起治。
- 与 [[feedback-smart-side-characters-no-dumbing-down]] 不同维度：那条管"配角城府"，这条管"句式骨架多样性"。

适用面：全局，尤其 gen-model 长草稿。详见 cluster_001 reflection `_数据库/.reading_reflection/cluster_001_round_1.json`。

---

<!-- FEEDBACK_RULE: feedback_malformed_toolcall_fewshot_poisoning.md -->
## feedback-malformed-toolcall-fewshot-poisoning

> description: "🔴 长会话工具调用畸形(裸invoke/缺前缀)反复出现=Claude Code已知bug#62344「上下文few-shot自我投毒」·非手滑·retry最糟(照抄坏模板)·唯一可靠修复=/clear开新会话·触发=长会话+大文件+多skill+1400行XML式markdown(如/outline skill)"

2026-06-05 用户两次坚持「联网搜索为什么总是指令错误」逼出的根因（我先前只自诊「手滑漏前缀」是治标）。

**根因（web 实锤 · Claude Code issue #62344）**：长会话里**一旦一个畸形工具调用（裸 `<invoke>` 缺 function_calls 包裹/缺 antml 前缀）进入上下文，自回归生成会不断照抄它 → few-shot 自我投毒滚雪球**。触发器：长会话 + 大量连续工具调用 + 读入大文件 + 加载多 skill + **密集 XML 式标记的超长 skill 文件**（issue 点名 1405 行；本项目 /outline skill ≈1400 行 bash heredoc+JSON 示例+result 块，精准踩中）。关联 issue #61133（Opus 4.7 自 2026-05-20 model 签名变更后 tool_use 空块）。

**Why**：这是自回归层面的污染，不是「我不小心」——只要被投毒的坏样例还在上下文，「我盯紧格式」也会被往回拽；**retry 重发是最糟动作**（把坏模板又抄一遍）。

**How to apply**：
- 长会话中工具调用开始**反复**畸形（≥2 次，尤其刚 Read 了超长 skill/大文件之后）→ **别再 retry、别自责手滑**，直接建议用户 `/clear` 开新会话（唯一可靠修复·issue 结论）。
- /clear 前**把当前决策/状态写一份 resume 备忘到磁盘**（本项目产出全在 workspace/·写 `_temp_research/.../启动备忘_resume.md`），新会话读它无缝续跑。
- 预防：长流程中少重复 Read 超长文件；能拆短会话就拆（尤其 /outline、cluster-write 这类加载大 skill 的命令）。
- 单次首发畸形可重写一次（带 antml 前缀）；连续畸形=投毒，立即转 /clear。

来源：[issue #62344](https://github.com/anthropics/claude-code/issues/62344) / [issue #61133](https://github.com/anthropics/claude-code/issues/61133)。关联 [[project-genmodel-flash-locked]]（同期新书启动）。

---

<!-- FEEDBACK_RULE: feedback_no_micro_task_workaround.md -->
## feedback-no-micro-task-workaround

> description: 禁止用「极小任务/拆小颗粒度 agent」绕过基础设施故障（502/限流/超时）。必须严格按 skill/CLAUDE.md 规范的颗粒度走流程

# 禁止「极小任务策略」绕基础设施故障

**规则**：遇到 anycast 502 / API 限流 / 超时等基础设施问题，**禁止**自创"拆小 agent 颗粒度（如 1 章/agent 替代 6 章/cluster agent）"等流程变体作为 workaround。必须严格按 skill 规范的颗粒度走（如 distill-style 是 v22.cluster 6 章/agent，不是 1 章/agent；write-chapter 是 cluster 模式，不是单章模式）。

**Why（2026-05-24 翻车）**：
- distill-style 在 anycast 502 故障下，主代理擅自把 cluster 6 章/agent 改成 1 章/agent 「micro」策略
- 用户即时纠正：「禁止极小任务策略这种情况，要严格按照流程走，这是全局要求」
- 翻车根因 = 把「基础设施故障」当成「任务大小问题」错误归因，进而违反 [[feedback-no-investigation-no-voice-universal]] 决策前 5 问（"我的判断基于实证还是我以为？"）
- 拆小颗粒度会产生不符合 skill schema 的次品数据（缺 cluster continuity / 缺章际衔接 dim 等），污染后续阶段

**How to apply**：
- 遇基础设施故障的正确做法：
  1. 暂停任务、向用户说明故障性质（不假装能绕开）
  2. 让用户决策：等服务恢复 / 切 provider / 暂停 plan
  3. 已落盘的合格产出保留（按 skill schema），不合格的不要硬塞
- 严禁的"创造性 workaround"：
  - 拆小 agent 颗粒度（违反 v22.cluster 主轨）
  - 让主代理直接执行 agent 任务（违反 lessons L4.1 主代理不读正文）
  - 压缩 prompt 字段（违反 hook 契约字段）
  - 跳过 plan_tracker step（违反 v17.2 强制规划）
- 当 skill 颗粒度规则与基础设施现实冲突时：把决策权交给用户，不要 AI 自己改流程
- 这条规则跟 [[feedback-no-token-saving]] 同源——都是"AI 不能为了'看起来在干活'而做妥协"

**适用范围**：全局（distill-style / write-chapter / save-state / outline / 等所有受 skill 规范约束的命令）

---

<!-- FEEDBACK_RULE: feedback_no_screenplay_stage_directions_in_novels.md -->
## feedback-no-screenplay-stage-directions-in-novels

> description: 全局：连续小说章末严禁任何场景过渡（剧本体+文学过渡都禁）·章末是钩子不是收束·POV 不切换是默认·cluster_001 ch4 两次翻车实例

# 连续小说严禁场景过渡（任何形式）

**用户连续两次原话**（2026-05-28，cluster_001 ch4 末段）：
1. 「（镜头拉远，离开陆建国的视角）这个是什么情况，正常小说不应该出现这种东西吧」
2. 「禁止文学过渡，我这是连续的小说，文学过渡有种大结局的感觉」

## 规则（升级版 · 包含两次翻车）

**连续小说的章末是钩子（cliffhanger / suspense / 心理悬念峰值），不是收束（closure / 平静感 / 镜头淡出）。**

任何让读者产生「这里结束了」感觉的过渡都禁——无论是剧本体还是「文学化」形式。

### 🔴 绝对禁用（hard_gate 级）

**剧本体舞台指示**：
- `（镜头拉远）` / `（镜头特写）` / `（推近）` / `（拉远）` / `（切镜）`
- `（旁白：XX）` / `（画外音：XX）` / `（音效：XX）` / `（背景音：XX）`
- `（OS）` / `（V.O.）` / `（CUT TO）` / `（FADE IN）`
- 形如「（XX 的视角）」「（离开 XX 的视角）」「（场景：XX）」的指令式括号

**文学化过渡（同样禁用 · 用户原话「有种大结局的感觉」）**：
- `*` / `***` / `······` / `——————` 等单独成行的分隔符
- 听觉淡出收束（如「脚步声越来越远」「门关上的声音」「一切安静下来」）
- 视觉淡出收束（如「灯一盏一盏熄了」「画面渐暗」）
- 物件全知镜头（POV 切到「桌上空空只有 X」「房间里只剩 Y」）
- 「然后一切安静下来」类终结句
- 「夜深了 / 天亮了」类时间收束句（章末用 = 收束；段中用 = 推进，区分场景）
- 空白段做场景切

**Why**：连续小说每章末读者要的是「下一章会怎样」的渴望，不是「这一章结束了」的满足。任何镜头淡出 / 物件留白 / 时空抽离都在暗示「这是一个完整段落」——破坏 cliffhanger 张力。

## 正确做法（连续小说 cliffhanger 工艺）

### 默认：POV 不切换

主角（或当前 POV 角色）全程在场到章末。Cliffhanger 由 POV 角色的**感知**传达：

- 主角**看见**异常物件 / 现象（但没反应到底）
- 主角**听见**异常声音（但没回头 / 没动）
- 主角**意识到**某件事不对（但没说破）
- 主角**做了一个动作**，让读者意识到他知道了什么（但没揭示）

末段最后一句 = **心理悬念峰值**，不是物理结束句。

### 翻车实例对比（cluster_001 ch4）

❌ **错误 v1**（剧本体）：
```
他转身，离开了办公区。

（镜头拉远，离开陆建国的视角）

第七窗口空着。
[全知镜头切到无人物件描写 + "一切安静下来" 收束]
```

❌ **错误 v2**（文学过渡 · 「大结局感」）：
```
他转身，离开了办公区。

走廊里的脚步声越来越远，最后只剩一截，被一扇关上的门切掉了。

　　　　　*

第七窗口空着。
[听觉淡出 + * 分隔符 + 物件留白 + "一切安静下来"]
```

✅ **正确**（POV 不切 + 感知传达 cliffhanger）：
```
他转身，往门口走。

走到门口，他停下来。

走廊尽头，那扇铁门的门把手转了一下。很轻。

陆建国没回头，也没动。

他看着自己的影子被走廊灯拉得很长，斜斜地搭在前面那段水磨石上。

影子里有一段是动的。不是他的。
[POV 全程不切 · cliffhanger = 影子异常 + 主角的"知道但不说" · 末句即峰值]
```

### 例外情况

- **真章节末 = 卷末 / 全书末**：才允许收束式过渡（这是真的结束）
- **明确换 POV 角色的连续小说**：可以章中段切（如多 POV 阵营文），但必须用「在 [新角色名] 那边」「与此同时 [新角色] 正在 XX」类明确人物切换句，禁用镜头/分隔符过渡
- **场景内时间跳转**：允许（如「下午三点」），但不允许在章末

## How to apply（系统三层防御）

1. **writer prompt** 默认包含：「连续小说·章末禁用任何过渡标记·cliffhanger 由 POV 角色感知传达」
2. **validate_style scanner** 加 banned_patterns（hard_gate）：
   - 剧本体括号指令
   - 章末出现的 `*`/`***`/`······` 单独成行（章中允许）
   - 章末「（一切）安静下来」「天亮了」「（XX）远去」类收束句
3. **项目级** `_数据库/style_scanner_overrides.json.banned_patterns` 强制锁

## 关联

- [[feedback-no-investigation-no-voice-universal]]：第一次翻车是没尽读 reflector；第二次翻车是「凭我以为」文学过渡比剧本体好——还是没调研用户对「过渡」整体的态度
- [[feedback-default-no-step-skipping-for-new-books]]：reflector 报告里「禁用 (镜头XX) 剧本体」我看到了，「推荐 *** 分隔符」我也看到了——但**用户**对「推荐项」从未表态过，我假设了它适用，错。lesson 提的「推荐项」不是用户偏好。

## 决策前 5 问应用（如何不再犯）

下次再遇到「reflector 提议加 X 过渡」时：
1. X 是不是给读者「结束」感觉？是 → 禁
2. 本书是连续小说还是独立短篇？连续 → 加强禁
3. 用户对 X 的态度调研过吗？没 → 问，别假设
4. lesson 的「推荐项」用户表态过吗？没 → 不是定论
5. 不确定 → 用 POV 不切的保守方案，绝对安全

## 章末 cliffhanger 选择规则（第三次反馈强化）

**用户原话**（2026-05-28，cluster_001 ch4 改后第三次反馈）：「最后这段和剧情强相关吗」

修复版（POV 不切 + 感知传达）虽然形式上对了，但**内容空洞**——「影子里有一段是动的，不是他的」装神弄鬼，**没锚定任何已存在剧情**：

- 它技术上算给远期 sc_003（cluster_005 揭示）的 setup，但跨度 4 个 cluster 太远
- 期间无中间回扣，读者会忘
- 风格不搭（《咒怨》式纯灵异 vs 《惊悚乐园》机构 SOP + 黑色幽默）

### 章末 cliffhanger 必须满足

| 条件 | 不满足 → 等于 AI 装神弄鬼 |
|---|---|
| **强锚定到具体伏笔/secret** | 不要"诡异氛围"做钩子（影子/低语/凉风） |
| **优先钩到下一个 cluster**（1-cluster 距离） | 远期伏笔（3+ cluster 后）已经够多了，章末再加 = 不付账 |
| **风格匹配整书设定** | 机构文不要纯灵异/纯恐怖镜头语言 |
| **末句 = 具体、可验证的异常** | 不要抽象的「不对劲」、模糊的「有什么东西」 |

### 翻车实例（cluster_001 ch4 三次迭代）

❌ **v1（剧本体翻车）**：(镜头拉远，离开陆建国的视角)
❌ **v2（文学过渡翻车）**：* 分隔符 + 听觉淡出 + 一切安静下来
❌ **v3（POV 对但内容空）**：影子里有一段是动的，不是他的 ← 装神弄鬼

✅ **v4（最终版）**：
```
他走到接待区。
台面上压着明天的预约表。
第一行：
【P-7Q-202604-002】 / 锦旗失踪案 / 林若昭。
他在第七窗口工作了十一年。
师姐从来没有自己提交过申报。
```
锚定到 cluster_002（与师姐对赌 + B-story 10 年监督） · 1-cluster 距离 · 末句是具体异常事实 · 风格完全匹配公文体。

### 配套：章末 cliffhanger 设计 checklist

写章末前，writer / 主代理必答 4 问：
1. 这个 cliffhanger 锚到哪个**已存在**的伏笔/secret/cluster brief？路径？
2. 它的 payoff 在**几个 cluster 之后**？> 1 cluster 距离 → 加中间回扣或换近期钩子
3. 它的形式（具体物件、具体台词、具体异常事实）符合本书风格吗？
4. 末句是**具体可验证**的还是抽象诡异的？抽象 → 重写

回答不出来 → 这章末就是装神弄鬼，重写。

---

<!-- FEEDBACK_RULE: feedback_no_token_saving.md -->
## feedback-no-token-saving

> description: 全局规则 — 不需要节省 token。脚本/agent 不要主动截断 input/context/output，全量传递

不需要节省 token。脚本/agent 一律**全量传递** input / context / output，不要主动用 `[:N]` 截断。

**Why:** 用户 2026-05-24 明确："我这里不需要节省 token，这是全局要求"。截断会让 AI 见不到完整信息，导致 false positive disagree（实测 ai_wrapper.py 在评 59-cluster JSON 时截到 8000 字符让 AI 误判脚本输出本身被截断）。

**How to apply:**
- 写 LLM call 时**禁止**用 `[:8000]` / `[:2000]` 等截断 input/context
- 写 prompt 拼接时直接传完整内容
- 摘要类输出（summary / preview）保留必要截断（如 UI 展示前 200 字），但传给 LLM 的不截
- 历史已截断的代码需补救：grep `\[:\d{3,}\]` 找出来全部移除
- 例外：单字段超过 LLM 最大上下文（如 Claude 1M / GPT-4o 128K），需分块 → 分块时也不要截断，要么完整传要么分多次

**与 v22.gov.align P0 关系**：本规则比「freeze the harness」更高一级——harness 的 prompt template 一致是结构对齐，token 完整性是内容对齐，两者并存才是真对齐。

---

<!-- FEEDBACK_RULE: feedback_one_sentence_per_paragraph.md -->
## feedback-one-sentence-per-paragraph

> description: 全局写作硬规则——非对话段落必须只有一个句号或其他结束符（。！？……）。看到单段多句立刻拆段。对话独立成段不受此约束（对话段仍可有省略号/破折号尾巴）

非对话段落（叙述/动作/心理/环境描写）**必须只有一个句末标点结束符**（。！？……）。看到单段多句立刻拆。

**Why**：用户 2026-05-25 在 cluster_001 切章后明确反馈"我发现单段多句号，明显可以拆成多段"。短段+一段一句更符合移动阅读节奏，跟项目级段长硬约束（avg 15-30 字 / 单段 ≤120 字 hard_gate）系统对齐。一段一句相当于把段落天然控制在 8-50 字主力区间。

**How to apply**：
- 适用范围：**全局**——所有写作项目，不只是《赖活》
- 强制对象：writer / gen_writer.py / validator-repair / 主代理手动 Edit
- 例外（不强制）：
  - 对话段（角色说话独立成段，已有 `dialogue_独立成段` 规则）
  - 短句独立成段（本来就一句一段）
  - 引用文献/信件正文（如《赖活》卷一信件全文）
- 检测：可在 audit_hub 加 `STYLE_单段多句` 扫描器
- 修复：直接把段内每个 `。` `！` `？` `……` 后面换段（除非是省略号尾巴接同主语短句）

**相关规则**：
- [[feedback-paragraph-length-hard-constraint]] 段长硬约束（avg 15-30 字 / 单段 ≤120 字 hard_gate）
- 项目级 `_数据库/用户偏好.json` 的 `writing_preferences.dialogue_独立成段`

**反例**（违规）：
> 林朝晚把手机屏幕按灭，又按亮，再看一遍。数字没变。他把信放回桌上，站起来，在十平米的出租屋里走了两圈。

**正确**：
> 林朝晚把手机屏幕按灭，又按亮，再看一遍。
>
> 数字没变。
>
> 信放回桌上。
>
> 站起来，在十平米的出租屋里走了两圈。

**当 cluster_001 切章后才补这条规则的教训**：写作规则必须在 cluster 写作前 100% 锁定，cluster_001 已写完才补规则 = 全 cluster 要回修 = token 浪费。下个项目 wizard 阶段必问"一段一句"偏好。

---

<!-- FEEDBACK_RULE: feedback_runtime_self_learning_mape_k.md -->
## feedback-runtime-self-learning-mape-k

> description: "🔁 2026-05-30 运行时自学习层(MAPE-K 4组件)·学运行时报错+脚本自适应+缺步补全·只advisory不改hard_gate·绝不自改脚本逻辑·嵌cluster-save-state step8/9取代`||true`·锚系统级runtime/·联网对标MAPE-K/Reflexion/Saga"

用户原话：「让自学习系统能实现自动学习运行过程中的报错，能让脚本自适应运行过程中的问题，并且能随时监控运行过程中缺失的步骤并且补充上去，同时要联网对标其他自学习自适应系统」。

已落地 **MAPE-K 闭环 4 组件**（权威设计 `core/claude-home/SELF_LEARNING_ARCHITECTURE.md`）：
- **Monitor** = `hooks/posttooluse_runtime_monitor.py`（PostToolUse:Bash 常驻，扫 stderr 真 Traceback→错误指纹→`incidents.jsonl`）
- **Analyze+Learn** = `self_heal_engine.py`（复发计数≥3 recurring/≥5 known→`self_heal_kb.json`+`lessons/runtime_lessons.md`，再现=regression）
- **Adapt** = `adaptive_runner.py`（retry/degrade/escalate + 熔断三态，**取代 `|| true` 静默吞错**）
- **缺步监控** = `step_completion_monitor.py`（Saga：检假完成/失败/未跑，`--auto-heal` 幂等重跑补产出）

数据锚 **系统级** `core/claude-home/runtime/`（跨小说项目）。集成在 `cluster-save-state` step 8/9（每 cluster 跑），非独立层。

**Why**：系统之前有完整自进化层被精简删（「只保证故事块流程」）。这次自学习**只聚焦运行时健壮性**（报错/缺步/脚本自适应），**不是创作自进化**——避免重蹈被删覆辙。它与 [[feedback-default-all-subsystems-enabled-for-new-books]] 里的 learning_loop（学审核 issue）**正交**：learning_loop 学写作质量，这层学运行时 Traceback。

**How to apply**：改自学习相关时——① 只 advisory 不改 hard_gate（[[project-north-star-style-fidelity]] 原则5「不干涉模型判断」）；② **绝不自改脚本逻辑**（Gödel Agent 缺 rollback 是其已知短板，Git 快照当锚点）；③ runtime 数据系统级跨项目，锚 `parents[2]` 不是小说项目根；④ adaptive_runner 取代 `|| true`——失败必记录学习不静默吞（呼应 [[feedback-verify-stderr-not-exitcode]]「别信 exit code/别吞 crash」+ [[feedback-no-micro-task-workaround]]「故障显式降级记录不偷偷绕过」）；⑤ 补跑必须幂等；⑥ 以 cluster 为单位。联网对标已做（MAPE-K/Reflexion/Saga/Circuit Breaker/Tolerant Reader）。

---

<!-- FEEDBACK_RULE: feedback_single_mode_deprecated.md -->
## feedback-single-mode-deprecated

> description: 全局写作硬规则——novel-writer 不允许走 single 模式（v25+），必须 ECAS（≥4章故事块）或 DCAS（双章）；outline 必须把 cluster_001 的 ch1-N 全部填到 chapter_plan

# Single 模式废弃 · 强制 ECAS 默认

**用户原话**（2026-05-26）：「我要的是故事块模式，系统中哪里触发的单章模式，全给我修成故事块的」「我要清理掉单章生成的模式，让单章生成没有生存空间」

**Why**：
- single 模式（单章直写）会绕过 cluster 级伏笔/voice/anchor/scene_storyboard 完整性校验
- 之前翻车：cluster_001 user 选了 ECAS，但 outline 阶段 chapter_plan 只填了 ch1 单条 → novel-writer wrapper agent 按文档第 52 行"chapter_plan 单条→single"判定走 single → 产出绕过 cluster 级倒叙重组（in_medias_res），ch1 字数被压到 2500-5000 范围（应是 13000-22000 cluster 范围）
- 根因 3 处：
  1. **novel-writer.md** 给了 single 模式生存空间（已删 v25+）
  2. **outline plan-step 3** 没强制初始化 cluster_001 的 ch1-N 全占位（已加约束 v25+）
  3. **用户偏好.json** 缺 `ecas_config.ecas_enabled=true` 默认（已修 schema 默认 true）

**How to apply**：
1. **outline 阶段**：初始化 cluster_001 时**必须**同时在 `进度.json.chapter_plan` 写入 ch1-ch_N 全部占位条目（N=`事件簇.clusters[0].chapter_count_estimate`，默认 4）；每条 cluster 字段 == "cluster_001"
2. **novel-writer wrapper**：spawn 前 Step 1 强制 enforce — `len(cluster_chapters) == 1` 且无 `.allow_single_mode.flag` → **fail-fast**，主代理必须先补占位再 retry
3. **用户偏好.json** 必须含 `ecas_config.ecas_enabled = true`（v23 默认；旧项目升级时补）
4. **写章节时**用 `/cluster-write`（cluster mode 推荐）而非 `/write-chapter`（兼容用，单 cluster 单写）
5. **紧急旁路**（罕用）：`touch <PROJECT>/_数据库/.allow_single_mode.flag` 才允许降级，仍强制 DCAS（≥2 章），从不允许真正 single

**禁令**：
- ❌ 不再 spawn novel-writer 仅传 1 章 chapter_plan
- ❌ 不在 outline plan-step 3 完成时只填 ch1 单条
- ❌ 不在 chapter_plan 中放孤立的不带 cluster 字段的 ch 条目

**实证**：本次 cluster_001 ch1 用 single 模式产出 5942 字（target 2500-5000），错过整 cluster 倒叙重组机会，绕过 4 个关键伏笔（fs_003 / fs_005 / fs_006 / fs_008）的 cluster 级埋点；reading-reflector 报 8 issue（包括 RR_006 SAN 锁定伏笔被淹没），就是 cluster 级 context 缺失症状。

---

<!-- FEEDBACK_RULE: feedback_smart_side_characters_no_dumbing_down.md -->
## feedback-smart-side-characters-no-dumbing-down

> description: 🔴 全局写作铁律：所有配角都是聪明人·有算计有城府·绝不降智当傻子衬托主角·信息差喜剧靠主角独有硬信息不靠别人犯蠢

用户原话（2026-06-04 选《我把现实玩成了双开盘》立项时）：「所有人都是聪明人，别把除了主角外的人写成傻子，要有算计和城府」。

**Why**：信息差喜剧（搞笑流核心引擎）最大的翻车模式 = 靠周围人犯蠢制造笑点 → 低级降智堆梗（主编评分时已独立点名要避开的雷区，对标豆瓣批评「丑化降智」）。配角降智会让主角的"领先"廉价化、读者代入崩塌。

**How to apply**：
- 信息差必须来自**主角独有的硬设定信息**（如"只有他知道这世界是双开盘 / 能读后台日志"），**不是别人蠢**。
- 每个配角都要有自己的精明盘算与城府，是有自主意志的活人——主角的唯一优势仅仅是"多一层别人不可能拿到的信息"。
- 因此主角得**装傻不解释、闷头行动**（任索式），而且要**时时被聪明配角的算计 + 真人自主意志将军/打乱盘面**（这正是卡1"游戏脑算计撞上现实真人自主意志"的核心张力）。
- 落地：outline 的 人物卡 / NPC 要写明各自的 agenda 与城府；writer manifest 注入"配角不可降智"hard 约束；character_depth 分布偏「中/高」（参考黄金范本 [[project-wulianzhe-novel-state]] 同类 character_depth_grade 高占比）。
- 与北极星⑤不冲突：这是创作工艺要求，advisory 层面引导 writer，不机械门禁。

适用面：全局，尤其搞笑流 / 信息差 / 无限流题材。关联本书立项见 MEMORY.md 的 双开盘 项目记忆（待建）。

---

<!-- FEEDBACK_RULE: feedback_v27_writer_freestyle_splitter_word_cut.md -->
## feedback-v27-writer-freestyle-splitter-word-cut

> description: 🔴 v27 全局：writer 不知章数+字数自由发挥 / splitter 按字数硬范围 3000-4500/章切 / 末章<3000 退回 pending_tail 等下 cluster 拼

# v27 三件套：writer freestyle + splitter 字数切 + pending_tail 补料

**用户原话（2026-05-27）**：
> 「故事块能切多少章我发现你一开始已经间接限制死了，这是不对的，应该让ai自由发挥，只要不脱离既有事实和大势，然后根据生成内容的字数，按照固定范围字数进行切割（一定程度上要参考最佳切割点），最后一章切出来字数不够就拿下一个故事块生成后的内容来补一些，这个补也是要放在切割的过程中」

**Why**：cluster_001-005 锁死 expected_word_range + estimated_chapters → writer 硬凑/硬塞字数 → cluster_005 round 1 cascade 排比末段拒 final_pass。让 AI 按 scope_summary 自由发挥 + splitter 按字数硬范围切 = 章节边界更自然。

**How to apply**：
1. **writer 默认 freestyle**：`gen_writer.py` 不传 `--chapter-end` / `--target-cjk` → prompt 不暴露目标章数 + 字数（writer 按 scope_summary 自由发挥）
2. **splitter MODE=ecas_freestyle**：按字数硬范围 3000-4500/章自动算 N · 不接受 TARGET_CHAPTERS
3. **末章 pending_tail**：末章 < 3000 退回 `cluster_<key>_pending_tail.txt` · 不切章 · 下 cluster 写完后 prepend 联合切
4. **outline step 1.7 AskUser**：每卷 cluster 数由用户决定（不预设） · 反推 ME 池数量
5. **schema 字段降级**：`expected_word_range` 移出 required（保留 advisory hint） · `estimated_chapters` / `chapter_range` 标 deprecated（splitter 切完自动填）
6. **向后兼容 v26**：`_writer_mode: "locked"` 走旧锁字数路径（显式选才走 · 默认 freestyle）

**实施清单（v27 已改文件）**：
- `core/scripts/gen_writer.py`（CLI optional + build_prompt freestyle 分支 + _infer_cluster_start_ch）
- `core/claude-home/schemas/event_cluster_schema.json`（required 移 ewr + 加 _writer_mode）
- `.claude/commands/outline.md`（step 1.7 AskUser 卷级 cluster 数）
- `.claude/agents/novel-outline-planner.md`（cluster brief 模板加 _writer_mode 默认 freestyle + 删字数预算硬约束段）
- `.claude/agents/novel-chapter-splitter.md`（加 ecas_freestyle 模式 + Step F pending_tail）
- `.claude/commands/cluster-write.md`（step 6 加 v27 freestyle 分支 + pending_tail 检测）
- `CLAUDE.md`（大纲章数 fluid 段升级 + v27 三件套段）

**禁令**：
- ❌ 在 cluster brief 写 `chapter_count_estimate` / `chapter_range` / `expected_word_range` 硬约束字段（v27 已 deprecate）
- ❌ writer 写时预设章数（writer 不应知道目标章数）
- ❌ pending_tail.txt 当独立章节文件（不写 章节/第NNN章/ 目录）
- ❌ splitter 跳过 pending_tail 机制硬切末章（末章 < 3000 必须退回）

**例外**：
- 短篇 / IP 改编已定顺序 / 用户明示 `_writer_mode: "locked"` → 走 v26 兼容模式

详见 lesson [[v27-writer-freestyle-splitter-word-cut-pending-tail]]。

---

<!-- FEEDBACK_RULE: feedback_verify_stderr_not_exitcode.md -->
## feedback-verify-stderr-not-exitcode

> description: 验证 Python 脚本是否真跑通：必须查 stderr 的 Traceback，不能信 exit code 或编排器的 N/N 汇总

验证「若渝AI」脚本/流水线是否真跑通时，**必须捕获并检查 stderr 里的 `Traceback`**，绝不能只看 exit code 或编排器汇总。

**Why（本会话两次翻车实证）**：
1. `run_cross_cluster_aggregates` 编排器只把 `returncode>=2` 记为 error，但 Python 崩溃是 exit=1 → 崩溃被吞、谎报「22/22 跑通」。我信了这个汇总，向用户报「22/22」「12/12」，实际 pattern 等 scanner 在 CLUSTER_MODE 下 100% KeyError 崩。
2. 我用 `python ... 2> $null > $null` 抑制 stderr 又只看「[OK] 账本写入」→ 漏掉 builder 的 `KeyError('stress_by_ch')`（builder 崩了，aggregator 走磁盘 fallback 仍显示 0 崩溃）。是后来的敌对验证 workflow 抓出来的。

**How to apply**：
- 跑脚本验证时：`python x.py ... 2> err.txt`，然后 grep `err.txt` 的 `Traceback|KeyError|AttributeError|TypeError|ValueError`。命中即真崩溃，无论 exit code。
- 别信「全跑通/N/N」这类编排器自报——编排器可能吞 exit=1。
- 真实平台验证：本项目跑在 **Windows PowerShell**，但 Bash(Git Bash POSIX) 验证会漏掉 Windows 专属问题（os.replace 占用、文件锁、中文路径、tmp 并发损坏）。涉及原子写/锁/exit code/路径的结论必须在 PowerShell 复验。
- 敌对验证（独立 agent 读代码 + 实跑复现，默认怀疑）能抓出经验性 crash-check 漏掉的语义/条件性回归——大改后值得跑。

关联 [[project-cluster-lookup-keystone]]：复审 71 问题修复后正是靠敌对验证 + stderr 可见的真实平台复跑，才发现 P0 builder 接线崩溃 + 9 处遗漏的 blueprint list `.items()` 崩。
