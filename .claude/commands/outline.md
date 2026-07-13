---
description: 生成卷级大纲与首个故事块 brief
---

## 创作分工层（agent 亲笔 + 确定性验收）

**分工**：outline 侧全部创意产物（灵感卡 / 卷级大势骨架 / 逐卷 ME 池 / cluster_001 storyboard）由 **`novel-outline-planner`（Claude）亲笔**创作；`gen_creative.py` 等确定性脚本**零 LLM 调用**，只做机器验收、单元调度、合并与落库。

**灵感卡（plan step 3）**：
1. spawn `novel-outline-planner MODE=brainstorm`（读 `RESEARCH_PATH` 调研缓存 + `STYLE_SKILL_PATH` 风格 skill）→ 亲笔写 `_数据库/.wal/inspiration_cards.json`。
2. 跑 `gen_creative.py --mode brainstorm --verify --cards ... --count 3 --research ...` 确定性验收：恰 N 卡、必备键齐、logline≤50 字、core_mechanism≤100 字、volume_skeleton 5-6 卷、source_refs≥1 且**每条 URL 必须真实存在于调研缓存原文**（防凭记忆编造来源）。验收不过 exit 2 = 重 spawn 重写；通过则盖验收章并归一为严格 JSON。

**卷级大纲（plan step 5·scene_jobs 范式）**：
1. 跑 `gen_creative.py --mode volume_arc --project ... --selected-card ... --emit-to-db`：逐单元验收 WAL——全书骨架（`_数据库/.wal/volume_arc_skeleton.json`·story_destiny/volumes/cluster_001/world_seed）+ 逐卷 ME 池（`_数据库/.wal/volume_arc_v<N>.json`·schema 合法的部分产物）。
2. 单元缺失/破损 → 写任务清单 `_数据库/.wal/volume_arc_jobs.json`（unit / 输入材料路径 / 期望产物路径 / 输入 digest / 破损诊断）并 **exit 2=pending**。
3. 主代理按清单逐单元 spawn `novel-outline-planner MODE=volume_arc_unit`（`UNIT: skeleton | v<N>` + `JOBS_MANIFEST` + `OUTPUT_PATH`）亲笔写单元 JSON。
4. 重跑同一命令续跑验收（`_normalize_skeleton` / `_normalize_volume_chunk` 是唯一裁决·破损单元退回 pending）；全部单元合法后**确定性合并**落 `大势卡.json + 事件簇.json`（与一把梭结构等价）。合并时 ME id 跨卷去重校验——重复 id **硬报错不静默覆盖**（撞 id 卷 WAL 隔离为 `.dup_broken`·重跑该卷退回 pending 重写）。中断重跑：已存在且校验合法的单元 WAL 直接复用（幂等续跑）。
- **P3 参考语料结构模式抽取（借鉴 Ex3-NovelWriter Extracting 阶段·确定性零 LLM）**：plan step 5 在 volume_arc 之前有一行**条件脚本**（行首 `? `·口径同 cluster-write 的 style_injector）：`? python core/scripts/reference_pattern_extract.py {project_root}`。当项目选定的风格库存在参考原文（`workspace/styles/<风格名>/原文/*.txt`·与蒸馏链同一路径口径）时，用**纯统计/正则**抽取结构模式落 `workspace/styles/<风格名>/genre_storyline_patterns.json`：章均 CJK 分布、对话占比曲线、场景切换密度（分隔线+转场标志词/千字）、冲突节奏（冲突标志词/千字·章序列）、新专名引入速率（人名启发式·只记数量）、卷级前/中/后三段 pacing 形状；`作者风格.json` 已有的量化指纹**复用不重算**（仅拷数值）。🔴 **版权纪律**：artifact 只含数字和短标签 + `source_ids`（章文件名）+ 每维 `provenance`，**绝不包含任何原文句子**。风格库无原文 → 优雅 skip（exit 0·条件产物不进 expected_outputs）；语料签名未变时幂等复用不重算。**消费端**：`gen_creative --mode volume_arc` 在 artifact 存在时把「参考作品结构基线（advisory·可偏离）」数字化参照写进 `volume_arc_jobs.json` 的 `reference_patterns_block` 供 agent 读——**非硬约束**（北极星⑤：大势卡内容仍由 agent 按灵感卡自由创作，只给结构参照）。

**保持确定性脚本处理的部分**：
- 单元验收 / 任务清单 / 合并去重 / `_metadata` 确定性覆盖 / emit 平铺落库 / world_seed 投影
- 34 个核心子系统 JSON 初始化（scaffold_subsystems）
- cluster_blueprint 的结构字段校验（db_schema_validate）

---

你是一位小说创作链路规划专家。请制定卷级阶段大纲、ME/cluster 池和首个故事块 brief：

$ARGUMENTS

---

## 🛡️ Plan 强制规划

大纲流程全部挂在 plan 上——start 前必须 `plan-create` 拿 PLAN_ID，每步完成 `plan-step --n N`，末尾 `plan-end`。具体 step 数与 required 输出以 `core/claude-home/plans/outline.plan.json` 为准。

```bash
# 框架选择 / 灵感讨论开始前先建 plan
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command outline \
  --project "<书名>" \
  --key v1)
echo "PLAN_ID=$PLAN_ID"
```

`--key` 字段用版本号区分（v1 / v2 / 改版前后）。后续若 `novel-outline-planner` 类 Agent 被调度，prompt 顶部加：

```
PLAN_ID: $PLAN_ID
STEP: <当前步骤号>
```

---

## 第 1 步：框架选择 / 灵感讨论

## 叙事框架选择（对齐 SidekickWriter/好莱坞方法论）

先让用户选择叙事框架，再基于框架生成大纲：

| 框架 | 适用 | 核心节拍 |
|---|---|---|
| **三幕式**（默认）| 通用 | 建立→对抗→解决 |
| **Save the Cat** | 商业网文/电影化 | Opening Image→Theme Stated→Catalyst→Midpoint→All Is Lost→Finale |
| **英雄之旅** | 玄幻/冒险/成长 | 日常→冒险召唤→跨越门槛→试炼→深渊→回归 |
| **雪花法** | 从零构思 | 一句话→一段话→角色摘要→扩展为1页→扩展为4页→首块详化 |
| **自定义** | 用户自己定 | 用户提供框架描述 |

**用户没选时默认三幕式。用户说"Save the Cat"或"雪花法"时切换。**

### Save the Cat 15节拍模板（商业网文适配版）

| 节拍 | 位置 | 网文适配 |
|---|---|---|
| 1. Opening Image | 第1章前半 | 主角日常+世界初见 |
| 2. Theme Stated | 第1章 | 点题——主角"缺什么"或"怕什么" |
| 3. Set-Up | 第1-3章 | 黄金三章——世界观+人设+金手指 |
| 4. Catalyst | 第3-5章 | 打破日常的事件（入门/获能/被迫卷入） |
| 5. Debate | 第5-8章 | 犹豫期——要不要接受冒险 |
| 6. Break into Two | 第8-10章 | 正式踏入新世界 |
| 7. B Story | 第10-15章 | 副线（感情线/友情线/导师线） |
| 8. Fun and Games | 第15-30章 | "承诺"兑现——读者来看的核心爽点 |
| 9. Midpoint | 全书50% | 假胜利或假失败——提升筹码 |
| 10. Bad Guys Close In | 50-75% | 压力升级——外部+内部双重危机 |
| 11. All Is Lost | 75% | 最低谷——主角失去一切 |
| 12. Dark Night of Soul | 75-80% | 反思+觉醒——主角重新找到意义 |
| 13. Break into Three | 80% | 带着新认知反击 |
| 14. Finale | 80-95% | 最终对决+所有线索收束 |
| 15. Final Image | 末章 | 与Opening Image呼应——角色成长可视化 |

### 雪花写作法6步模板

| 步骤 | 输出 | 描述 |
|---|---|---|
| 1 | 一句话 | 全书的一句话概要（25字以内） |
| 2 | 一段话 | 扩展为5句话（背景+3个灾难+结局） |
| 3 | 角色摘要 | 每个主角的：名字/目标/动机/冲突/顿悟/一段话概要 |
| 4 | 一页大纲 | 每段话扩展为一段，约1页 |
| 5 | 四页大纲 | 每段扩展为一页，约4页 |
| 6 | 首块详化 | `cluster_001` scene storyboard + 后续 ME/cluster 池 |

**用户选"雪花法"时，引导用户从第1步开始逐步扩展，不一次性生成完整大纲。**

**plan-step 3.2**（框架选择会写 `_数据库/.wal/outline_choice_framework.json`）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3.2
```

---

## 第 1.5 步：节奏档软提示

本步**仅询问节奏档**作为软提示，**不算章数、不算 event 密度、不锁 chapter_range**。理由：故事块 + 涟漪效应让单卷章数无法预先锁定。

### 输入读取

1. Read `_数据库/用户偏好.json`，找 `workflow_preferences[*].key=rhythm_profile`
2. 缺失 → AskUserQuestion **仅问一个字段**：节奏档（紧凑/标准/厚重/混合）
3. 已读到 → 直接用，不再追问

### 节奏档作软提示（仅 hint，不算公式）

| 档位 | 描述 | 适合 |
|---|---|---|
| **紧凑** | 高密度 event、转折频繁 | 短篇/中篇 |
| **标准** | 主流商业网文节奏 | 中长篇 |
| **厚重** | 慢热铺垫、event 间章数多 | 史诗/严肃文学 |
| **混合**（推荐） | 重 event 拉长 + 轻 event 紧凑 | 大部分长篇 |

### 写入大势卡.json 的 `_metadata`（仅 1 个字段）

```json
{
  "_metadata": {
    "rhythm_profile": "混合",
    "designed_at": "ISO 日期",
    "version": "v23.12"
  }
}
```

**禁止再写** `target_chapter_count` / `volume_count` / `events_per_volume` / `avg_chapters_per_event` / `filler_ratio` —— 下游脚本按 rhythm_profile 单一参数估算弹性区间。

### plan-step 1.5

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3.4
```

---

## 卷级 cluster 数 + writer freestyle 模式（v27）

用户原话：「每卷的故事块数量应该问询用户，然后故事块能切多少章我发现你一开始已经间接限制死了，这是不对的，应该让ai自由发挥」。

本段是 outline plan 内的必跑动作之一 — 决定每卷有多少 cluster（=多少 ME 大势事件），并写入 writer freestyle 偏好。

### 询问每卷 cluster 数

**AskUserQuestion 1**：「《<书名>》第 1 卷你想要几个故事块（cluster）？」
- **选项**（推荐区间 4-12）：
  - `4-5（紧凑短篇向）` — 卷长约 50-100K 字
  - `6-8（标准长度，推荐）` — 卷长约 80-200K 字
  - `9-12（厚重长篇向）` — 卷长约 150-300K 字
  - `Other` — 用户自填数字
- 如果用户回答 N，写入 `_数据库/用户偏好.json.workflow_preferences[]`：
  ```json
  {"key": "cluster_count_per_volume", "value": N, "set_at": "ISO", "user_decided": true}
  ```

**多卷书**：若大纲含 2+ 卷，依次问每卷 cluster 数；用户可一次性给所有卷数字。

### writer freestyle 固定启用

writer 固定走 freestyle：不接收目标章数 + 字数，按 `cluster.scope_summary` 和 `scene_storyboard` 自由发挥；splitter 后期按字数切，末章不够字数从下个 cluster 补料。

写入 `_数据库/用户偏好.json.workflow_preferences[]`：
```json
{"key": "writer_mode", "value": "freestyle", "set_at": "ISO", "user_decided": true}
```

### 1.7.3 反推 ME 数 + 写入大势卡（🔴 v28 卷=阶段触发点）

> **核心心智**：**1 卷 = 1 个阶段（副本/大故事方向）**，下辖 N 个**小故事走向（cluster）**累积构成整阶段。**1 个 cluster ≈ 1 个 ME**，且**每个 ME 只是「本阶段里的一个小走向」，绝不能是「一整个副本/阶段」**。换阶段/换副本 = 换卷（新 vol 号）。详见 CLAUDE.md「🔴 卷=阶段触发点」+ memory `project_volume_phase_structure`。

卷 1 用户答 N → 大势卡 V1 = **把这一个阶段拆成 N 个小走向**（入门/摸规则/试错/转折/危机/高潮/收束…），全部 `volume: 1`、末个 `is_volume_finale: true`（弹性 N±1）。

**写入 `_数据库/大势卡.json`（schema = `major_events_v21_phase`，scaffold 已生成骨架）**：

1. **`volumes[]`** —— 每卷一条阶段定义（用 `_volume_schema`）：
   ```json
   {"vol": 1, "title": "<阶段/副本名>", "volume_core_conflict": "<本阶段核心任务·解决即可收卷>",
    "volume_thread": "<卷线索:串起本卷所有小走向 cluster 的那根线>",
    "volume_finale_signal": "<换卷触发:核心任务解决 + (①力量跃迁/②舞台转移/③反派更迭) 任一>"}
   ```
   `volume_core_conflict` + `volume_thread` **必填**——没有卷线索 = 散沙 cluster。

2. **`major_events[]`** —— 扁平 ME 池（用 `_me_schema`），每个 ME = 1 cluster = 1 小走向：
   ```json
   {"id": "ME-V1-01", "volume": 1, "title": "<小走向>", "is_volume_finale": false,
    "stakes_delta": "<相对前一小走向的筹码/强度增量·try-fail 递增>", "prerequisites": [], "status": "pending"}
   ```
   - **每个 ME 必标 `volume: N`**（`cluster_emergence_engine._me_volume` 据此硬过滤到当前卷·核心任务未解前不跳新卷）。
   - **本卷末个 ME 标 `is_volume_finale: true`**——它对应卷末高烈度转折 cluster（阶段跃迁/真相揭露/反派现身）。
   - `stakes_delta` 让同卷各小走向**强度递增**（避免平铺重复·deepseek 品鉴实证「重复解构套路会疲劳」）。

**禁止**：
- ❌ **把 1 个 ME 写成「一整个副本/阶段」**（=单 cluster 塌缩整阶段·本次系统根治的 bug）
- ❌ 在 cluster brief 写 `chapter_count_estimate` / `chapter_range` / `expected_word_range` / `word_budget`（cluster-first 硬禁 · splitter 切完只回填实际章号）
- ❌ 在 ME 池写「expected_chapters」（章数由 writer + splitter 涌现）

### plan-step 1.7

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3.3
```

---

## 第 2 步：卷级大势大纲生成

本步**只描述大势**，不规划每卷章数。

> **🔴 v28 卷=阶段触发点（authoring 铁律）**：**每卷 = 1 个阶段**（1 副本/1 大故事方向），靠 `volume_core_conflict`（卷核心任务）+ `volume_thread`（卷线索）定义；**卷边界 = 阶段触发点**（核心任务解决 **且** 命中①主角力量/身份跃迁 / ②舞台/地理转移 / ③核心反派或矛盾更迭 任一）。一个阶段下挂 N 个**小故事走向 cluster**（step 1.7 用户定 N），stakes 递增累积构成整阶段——**禁止把一整个副本/阶段塞进单个 cluster/ME**。换阶段/换副本 = 换卷。

| ✅ 写 | ❌ 不写 |
|---|---|
| 每卷 `volume_core_conflict`（核心任务）+ `volume_thread`（卷线索）+ `volume_finale_signal`（换卷触发） | `volumes[].chapter_range: [1, 10]` 死锁区间 |
| 每卷 `volume_arc` / `key_milestones` / `ending_state` | 「本卷预计 N 章」类预测 |
| major_events 池：每 ME=1 小走向·标 `volume:N`·末个 `is_volume_finale`·`stakes_delta` 递增 | events_per_volume 反推章数 / 把整副本写成 1 个 ME |

章数由 writer + splitter 自然涌现，cluster 走向由 ME 触发 + 用户涟漪选择 + `/cluster-save-state` 累积决定；**卷数 = 阶段数**（每换一个大故事方向/副本就 +1 卷）。

请提供：
1. 各卷的阶段目标：`volume_core_conflict`、`volume_thread`、`volume_finale_signal`。
2. `major_events[]`：每个 ME 对应一个小故事走向 cluster，标 `volume`、`stakes_delta`、`prerequisites`、`is_volume_finale`。
3. `cluster_001` 的 `scope_summary`、`scene_storyboard`、`foreshadowing_to_plant`、`research_ref`。
4. 伏笔和线索的埋设/回收时机，以 cluster 或 scene 为单位标注。
5. 节奏控制以卷、cluster、scene 三级描述；禁止生成逐章大纲、预计章数、预计字数或章号范围。

可以使用表格或列表形式，清晰展示阶段、ME 和首块 storyboard。

**Agent 调度规范**：如启动 `novel-outline-planner` 一类 sub-agent 生成大纲，prompt 顶部必须含：

```
PLAN_ID: $PLAN_ID
STEP: 2
PROJECT: <书名>
```

**plan-step 2**（大纲.md 是必须落地的文件，由模板 expected_outputs 校验）：

```bash
# 大纲文档落地后
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2
```

模板已配 `大纲.md`，缺失自动 FAIL。

---

## 第 3 步：数据库初始化

### 3.0 确定性脚手架先行（根治契约债 · 照顾弱模型）

**先跑 scaffold 生成 34 个 schema 正确的空骨架，再往骨架里填创意内容** —— 弱模型只填内容、不碰 schema，杜绝「agent 自由生成 schema → 与 consumer/validator 漂移 → 契约债」：

```bash
# ① 生成 34 个 schema 正确的空骨架（已存在的不覆盖，保护已填内容）
python core/scripts/scaffold_subsystems.py emit "<书名>"
```

- 单一真理源：`core/claude-home/templates/subsystem_skeletons.json`（34 骨架·都满足 db_schema_validate）
- 填充示例参考：`core/claude-home/templates/examples/`（含 `_subsystem_examples/` 9 个高级件示例）
- 然后按下面 1-16 把**创意内容**填进骨架（cluster_001 详化 / 人物 / 大势 / 伏笔等）。
- 🔴 **作者风格必须从风格库复制「两个」文件**（gen_writer 写作时两个都读·**漏 skill = writer 只有量化数字、缺作者笔法+golden 范例 → 跑偏成通用爽文**）：
  - ① `workspace/styles/<风格名>/作者风格_FINAL.json` → `_数据库/作者风格.json`（量化基线）
  - ② `workspace/styles/<风格名>/skill_FINAL.md` → `_数据库/作者风格_skill.md`（**笔法 + golden 范例·必拷·别漏**）
  - ③ 🔴 拷完必须在 `_数据库/作者风格.json` 顶层写 `"style_source": "workspace/styles/<风格名>/skill_FINAL.md"`
    （相对仓库根·learning_loop/snippet_seed/audit_hub/best-of-N 原文池与 SFS/AV 打分全靠它反查——缺失=
    语感种子+择优打分双退化成「退回第一稿」·db_schema_validate 硬校验）
  - 或走 `/distill-style` 蒸馏替换占位（蒸馏会同时产这两件并写 style_source）。

完成填充后、跑 plan-step 3 前，**强制核对**（流程缺步补全，防 Workflow 名义返回掩盖漏文件）：

```bash
# ② 校验 34 件存在 + json 合法（缺/坏 → exit 2 阻断，不许带病进 step 3）
python core/scripts/scaffold_subsystems.py verify "<书名>"
# ③ schema 契约校验（13 核心件 0 error 才算干净）
python core/scripts/db_schema_validate.py "workspace/novels/<书名>"
```

> scaffold 是**底座**不是替代：3.0 给空骨架，下面 1-16 给创意内容，二者配合。手搓 / agent 生成时也以骨架 schema 为准。

# 数据库初始化

大纲确认完成后，自动执行以下操作（**优先用 3.0 scaffold 生成骨架后填充**）：

1. `mkdir -p 小说_书名/_数据库/`（scaffold emit 会自动建目录）
2. Write `_数据库/人物卡.json` — 从大纲中提取主要角色信息（含声音包），结构如下：
```json
{
  "characters": [
    {
      "id": "主角1",
      "name": "",
      "role": "主角/配角/反派",
      "appearance": "",
      "personality": "",
      "voice_pack": {
        "rhythm": "短句偏多/长短交错/偏爱长句",
        "banned_phrases": ["禁用口头禅1", "禁用口头禅2"],
        "catchphrase": ["口头禅1", "口头禅2"],
        "style": "口语化/书面偏多/俚语",
        "style_samples": [
          "示例对话1（展示角色说话方式的典型例子）",
          "示例对话2（另一个场景下的说话方式）"
        ],
        "anti_samples": [
          "反面示例1（这个角色绝对不会这样说话）"
        ],
        "_persona_5layer_optional": "以下 5 层是 distill-character 深度蒸馏的可选字段（对齐 distill-character 命令的 Voice DNA 5 层 Persona schema）",
        "layer_0_hard_rules": {
          "never_say": [],
          "never_do": [],
          "never_attitude": [],
          "knowledge_boundary": []
        },
        "layer_1_identity": {
          "self_concept": "",
          "core_values_ranked": [],
          "life_goal": "",
          "deepest_fear": "",
          "self_deception": ""
        },
        "layer_2_speech": {
          "avg_sentence_chars": 0,
          "common_sentence_structures": [],
          "punctuation_preference": "",
          "unique_words": [],
          "speech_filler_freq": {}
        },
        "layer_3_emotion": {
          "anger_expression": "",
          "care_expression": "",
          "tension_expression": "",
          "conflict_pattern": "",
          "attachment_type": ""
        },
        "layer_4_behavior": {
          "first_reaction_to_conflict": "",
          "decision_tendency": "",
          "interaction_with_superiors": "",
          "alone_behavior": "",
          "stress_regression": ""
        }
      },
      "locked_facts": [],
      "knowledge": {
        "knows": ["该角色知道的信息列表"],
        "doesnt_know": ["该角色不知道的关键信息"],
        "will_learn": [
          {"fact": "将会知道的信息", "learn_at_cluster": 7, "how": "被主角告知"}
        ],
        "_knowledge_state_v19_6": "G7 三层视角隔离 + 认识论控制（可选字段，writer 按 POV 角色过滤上下文）",
        "knowledge_state": [
          {"fact_id": "fact_001", "level": "亲见", "since_cluster": 1, "_doc": "level 四态：亲见/听说/猜测/不知道"},
          {"fact_id": "sc_001", "level": "不知道", "since_cluster": null}
        ]
      },
      "offscreen": {
        "goals": ["角色的长期目标1", "角色的长期目标2"],
        "current_plan": "当前正在执行的计划",
        "actions": [
          {"ch_range": [3, 5], "action": "幕后行动描述", "result": "行动结果", "visible_to_protagonist": false}
        ]
      },
      "growth_arc": [],
      "decision_patterns": [],
      "arc": "",
      "status": "活跃",
      "first_appear_cluster": 1
    }
  ]
}
```
   - `locked_facts`：记录已确立的不可变事实（如眼睛颜色、武器、血统等），写作中禁止矛盾
   - 每当正文中首次确立角色属性时，自动追加到 `locked_facts`
   - `offscreen`：只为重要配角/反派添加，主角和路人不需要。记录角色的幕后目标、计划和行动
   - `growth_arc`：角色成长轨迹，每章蒸馏时自动追加（格式：{ch, state, key_change, trigger}）
   - `decision_patterns`：角色决策模式积累，从重要选择中提取（最多5条）
3. Write `_数据库/世界观.json` — 从大纲中提取设定，支持关键词触发注入，结构如下：
```json
{
  "era": "",
  "location": "",
  "rules": [],
  "factions": [],
  "entries": [
    {
      "id": "条目标识",
      "keywords": ["触发词1", "触发词2", "触发词3"],
      "content": "当触发词出现时注入的详细描述",
      "priority": 8,
      "category": "物件/地点/势力/规则/事件"
    }
  ]
}
```
   - `entries`：关键词触发条目（借鉴 SillyTavern World Info 机制）
   - 每个条目包含触发词列表，当 cluster brief / scene storyboard 中出现匹配关键词时，自动注入对应 content
   - `priority`：优先级（1-10），数值越高越优先注入
   - `category`：条目分类，便于管理和筛选
4. Write `_数据库/伏笔表.json` — 初始化升级版伏笔系统
```json
{
  "promises": [],
  "deadlines": [],
  "pledges": [],
  "secrets": []
}
```
   - **promises**（传统伏笔）：埋设/回收机制，含 Tier 分级
     ```json
     {
       "id": "fs_001", "setup_cluster": 3,
       "description": "主角腰间的玉佩",
       "tier": 1, "due_by": 15,
       "status": "open", "owner": "writer", "payoff_scope": "预期在第 15 章前回收",
       "trigger_condition": {
         "_doc": "因果谓词形式化（CFPG arxiv 2601.07033）",
         "location": "any | <地点ID>",
         "character": "<触发该 payoff 必须在场的角色>",
         "event_type": "object_use | dialogue_reveal | physical_change | other",
         "physical_evidence": "<必须在正文出现的物理细节，如'玉佩裂开'>"
       }
     }
     ```
     - Tier-1：核心伏笔（主线级，必须回收，高优先级注入）
     - Tier-2：支线伏笔（影响剧情走向，建议回收）
     - Tier-3：氛围伏笔（细节装饰，可以不回收）
     - **三态生命周期**：`status` ∈ `open`（已埋未收）/ `suspended`（显式挂起延后·不催收）/ `consumed`（已回收）；`owner` = 负责回收的角色/线索归属（缺省 `writer`）；`payoff_scope` = 预期回收范围描述（可从 due_by 派生·允许空串）。枚举权威 `db_schema_validate.FORESHADOW_STATUS_ENUM`，非法值校验报错
   - **deadlines**（截止期约束）：剧情中出现的时间承诺
     ```json
     {"id": "dl_001", "raised_ch": 5, "description": "三天后比武大会", "deadline_ch": 8, "status": "pending"}
     ```
     - status: pending/triggered/missed（错过则触发剧情债务）
     - 从正文中自动提取"X天后/下个月/不久的将来"等时间锚点
   - **pledges**（承诺约束）：角色立下的誓言/承诺
     ```json
     {"id": "pl_001", "pledger": "主角", "raised_ch": 4, "pledge": "发誓保护小师妹", "status": "active"}
     ```
     - status: active/fulfilled/broken（违背时自动告警）
     - 后续章节中该角色的行为需要校验是否违背
   - **secrets**（秘密揭露追踪）：不能提前泄露的剧情秘密
     ```json
     {"id": "sc_001", "secret": "主角是穿越者", "established_cluster": 1, "reveal_at_cluster": 20, "known_by": ["主角"], "status": "hidden", "epistemic_class": "永不明确"}
     ```
     - status: hidden/leaked/revealed
     - known_by 更新时自动检查是否有人意外知晓
     - **epistemic_class 四级密级**：`公开` / `隐藏` / `延迟` / `永不明确`
       - 公开：所有 POV 角色都能感知（如背景设定）
       - 隐藏：仅 known_by 列表内角色能感知（默认）
       - 延迟：知道但 N 章内不能透露（writer 必须等到 reveal_at_cluster）
       - 永不明确：作者从不直接揭示，靠读者自己拼图
5. Write `_数据库/故事块摘要.json` — 初始化 `{"schema_version": "v2.cluster", "clusters": [], "volume_summaries": []}`。cluster-save-state 经 `cluster_summary_builder.py` 写入完整 cluster 记录，卷边界摘要写入 `volume_summaries`。
6. Write `_数据库/进度.json` — 初始化进度，结构如下：
```json
{
  "schema_version": "v2.cluster",
  "current_cluster": "cluster_001",
  "completed_clusters": [],
  "writer_mode": "freestyle",
  "volumes": [
    {
      "vol": 1,
      "title": "第一卷：破局",
      "core_conflict": "本卷核心冲突",
      "volume_thread": "串起本卷所有 cluster 的卷线索",
      "volume_finale_signal": "换卷触发信号",
      "volume_arc": "本卷人物弧线（起→承→转）",
      "key_milestones": ["里程碑事件1", "里程碑事件2"],
      "ending_state": "本卷末尾的故事状态"
    },
    {
      "vol": 2,
      "title": "第二卷：觉醒",
      "core_conflict": "本卷核心冲突",
      "volume_thread": "串起本卷所有 cluster 的卷线索",
      "volume_finale_signal": "换卷触发信号",
      "volume_arc": "本卷人物弧线",
      "key_milestones": ["里程碑事件"],
      "ending_state": "本卷末尾状态"
    }
  ],
  "cluster_blueprint": {
    "cluster_001": {
      "vol": 1,
      "narrative_mode": "in_medias_res",
      "scope_summary": "本故事块核心矛盾 + 起承转合一句话",
      "foreshadowing_to_plant": ["fs_001"],
      "scene_storyboard": [
        {"ch": 1, "title": "场景标题", "characters": ["角色A", "角色B"], "key_events": ["事件1", "事件2"], "scene_type": ["日常", "悬疑"], "emotion": {"value": 6, "trend": "↘↗", "anchors": {"hook": "开头钩子描述", "conflict": "中段冲突描述", "climax": "高潮/反转描述", "cliffhanger": "章末悬念描述"}}, "goal": "本场景核心目标", "turning_point": "关键转折", "threads_advance": ["线索ID"], "try_fail": "尝试X→失败Y→适应Z", "info_gain": "向读者释放的新信息", "payoff": "兑现的伏笔(可选)", "time_hint": "故事时间", "conflict_stage": "铺垫", "scene_purpose": "本场如何 service 卷核心冲突(一句话·不能只描述画面)", "alignment": "aligned"},
        {"ch": 2, "title": "场景标题", "characters": ["角色A", "角色C"], "key_events": ["事件3", "事件4"], "scene_type": ["战斗", "转折"], "emotion": {"value": -3, "trend": "↘", "anchors": {"hook": "开头钩子描述", "conflict": "中段冲突描述", "climax": "高潮/反转描述", "cliffhanger": "章末悬念描述"}}, "goal": "本场景核心目标", "turning_point": "关键转折", "threads_advance": ["线索ID"], "try_fail": "尝试-失败-适应", "info_gain": "新信息释放", "payoff": null, "time_hint": "故事时间"}
      ]
    }
  },
  "propagation_debt": [],
  "started_at": "ISO日期",
  "last_updated": "ISO日期"
}
```
   - 🔴 **`cluster_blueprint` 必须是 dict**（`cluster_id` → cluster 数据），**禁止初始化为 list**。SC-1 规范形态 + `cluster_lookup._iter_blueprint_ranges` 用 `.items()` 遍历 `cluster_blueprint` 取每个 cluster 的 `chapter_range` / `scene_storyboard[].ch`；写成 list 会让反查整体瘫痪。
   - 🔴 **cluster-first 涌现纪律**：outline 阶段**只详化 `cluster_001`**（含完整 `scene_storyboard` + `scope_summary` + `foreshadowing_to_plant`）。`cluster_002+` 不预设——由 `/cluster-save-state` 末尾涌现。
   - 🔴 **v27 freestyle 不写 `chapter_range`**：cluster 的 `chapter_range` 由 splitter 切完后回填（事件簇.json 为权威源），outline 阶段不预设。`scene_storyboard[].ch` 是 writer 蓝图序号（场景顺序），非物理章号。
   - 🔴 **S11 scene 级大势对齐三问自评（advisory）**：outline-planner 详化 storyboard（cluster_001 与涌现 brief 两个场合）时每 scene 必答三问（①此场景如何推动卷核心冲突 ②是否违反世界观/locked_facts ③`scene_purpose` 必须体现与卷核心任务的关系·不能只描述画面），自评落 `conflict_stage`（铺垫|升级|高潮|转折|尾声）/ `scene_purpose` / `alignment`（aligned|minor-deviation|needs-review）三个**可选** advisory 字段（fluid 纪律·schema 不设 required 不硬锁）。`needs-review` 是 planner 的诚实标记不是失败（北极星⑤ 自评透明化非外部裁决）；`volume_arc_drift_scanner` 把本卷 needs-review 计数/清单汇总进其报告 `alignment_review` 段（只报告不裁决）。权威 schema = `event_cluster_schema.json` 的 `scene_storyboard.items`，合约详见 `.claude/agents/novel-outline-planner.md` ⑧。
   - `narrative_mode`：仅首个 cluster 默认 `"in_medias_res"`（黄金三章倒叙），后续 cluster 默认 `"linear"`。
   - `volumes`：卷/阶段层；每卷都要有 `volume_core_conflict` / `volume_thread` / `volume_finale_signal`
   - `volume_arc`：每卷的人物成长弧线（起承转），写作时由 manifest 注入给 writer，确保 cluster 服务于卷级目标
   - `key_milestones`：卷级关键事件，用于长距召回
   - 每个 cluster 关联到所属卷（`vol` 字段），用于卷级一致性检查
   - `scene_type` 标注本场景主要类型（可多选），用于场景规则注入
   - 后续 cluster-write（build_manifest）根据 `cluster_blueprint[cluster_id].scene_storyboard[].characters` 按需加载人物卡
7. Write `_数据库/场景规则.json` — 初始化场景类型对应的写作规则
```json
{
  "scene_types": {
    "战斗": {
      "style": "短句为主，动作描写>心理描写，节奏紧凑，每段不超过3行",
      "dialogue_ratio": "20-30%",
      "focus": "动作、速度感、力量对比"
    },
    "日常": {
      "style": "对话为主，轻松语气，可以有内心吐槽和幽默",
      "dialogue_ratio": "50-70%",
      "focus": "角色互动、性格展现、生活细节"
    },
    "情感": {
      "style": "细腻描写，内心独白多，节奏放慢",
      "dialogue_ratio": "30-50%",
      "focus": "情绪变化、微表情、环境烘托"
    },
    "悬疑": {
      "style": "暗示>明说，信息碎片化，制造不安感",
      "dialogue_ratio": "40-60%",
      "focus": "线索铺设、氛围营造、读者能看出但角色看不出"
    },
    "转折": {
      "style": "前半平缓后半急转，反差感强烈",
      "dialogue_ratio": "30-50%",
      "focus": "铺垫与反转、情绪落差、关键信息揭露"
    }
  }
}
```

8. Write `_数据库/写作经验.json` — 初始化写作经验库（Learning Loop）
```json
{
  "success_patterns": [],
  "failure_patterns": [],
  "preferences": []
}
```
   - `success_patterns`：从成功写作中提取的有效技巧（格式：{pattern, confidence, observed_in, source}）
   - `failure_patterns`：从问题中提取的避免模式
   - `preferences`：从用户反馈中积累的写作偏好

9. Write `_数据库/用户偏好.json` — 初始化用户偏好档案（借鉴 OpenClaw USER.md）
```json
{
  "style_preferences": [],
  "content_preferences": [],
  "workflow_preferences": [],
  "last_updated": ""
}
```
   - `style_preferences`：用户的写作风格偏好（如"喜欢快节奏"、"对话比例要高"）
   - `content_preferences`：内容偏好（如"不要无脑打脸"、"虐点要克制"）
   - `workflow_preferences`：工作流偏好（如"节奏偏紧"、"走向卡自动选择"、"导出格式偏好"）
   - 从用户的反馈和修正中自动积累，不需要用户手动填写

10. Write `_数据库/地图.json` — 初始化世界地图
```json
{
  "locations": [],
  "character_positions": {},
  "travel_log": []
}
```
   - `locations`：从大纲中提取的主要地点（含连接关系、氛围描述）
   - `character_positions`：各角色的初始位置
   - `travel_log`：角色移动记录（cluster-save-state 自动更新）

11. Write `_数据库/关系.json` — 初始化角色关系系统
```json
{
  "relationships": [],
  "faction_standings": {}
}
```
   - `relationships`：角色间的关系数值（affinity/trust/fear/respect）
   - 从大纲中提取初始关系（如：主角与反派初始 affinity 为负）

12. Write `_数据库/事件表.json` — 初始化事件触发器
```json
{
  "pending_events": [],
  "triggered_events": [],
  "recurring_events": []
}
```
   - 从大纲中提取关键事件及其触发条件

13. Write `_数据库/时间线.json` — 初始化时间系统
14. Write `_数据库/道具.json` — 初始化道具/物品系统（`{"items": []}`）
```json
{
  "current_time": {"day": 1, "period": "morning", "cluster": 1, "season": ""},
  "time_per_cluster": "约半天",
  "npc_schedules": {},
  "world_clock_events": [],
  "time_log": []
}
```
   - 设定时间流速、NPC 日程、关键世界事件时间点

14. Write `_数据库/道具.json` — 初始化道具/物品系统
```json
{
  "items": [],
  "item_locations": {},
  "crafting_recipes": []
}
```
   - `items`：重要物品列表（名称、描述、效果、当前持有者、获得章节）
   - `item_locations`：物品当前位置（谁持有/在哪个地点）
   - `crafting_recipes`：物品组合/升级规则（如有）
   - 与伏笔系统联动：重要物品自动标记为"契诃夫之枪"

15. Write `小说_书名/.gitignore` — 排除临时文件
```
# WAL 预写日志（临时文件，崩溃恢复后自动清理）
_数据库/.wal/

# 备份文件
*.bak
*.backup

# 编辑器临时文件
*.swp
*~
.DS_Store
Thumbs.db

# 蒸馏中间产物（可重生）
_数据库/蒸馏进度/
```

16. Git 仓库由 `init_project.py` 初始化，快照由 plan-end 的 `git_snapshot.py --marker _数据库/.wal/outline_git_snapshot.json` 写入。

**Git 集成硬性规则：**
- Git 不可用、`git init` 失败或 commit 失败都是当前 plan 的硬失败。
- 路径含中文时所有脚本参数必须传路径字符串，不手写未加引号的 `cd`。
- 只在项目目录内操作，不做 push/pull/force/reset --hard 等破坏性操作。
- 只设置本地 user.name/email，不污染用户的全局 git config。

终端输出：「✅ 数据库已初始化（34 个核心子系统 JSON），Git 仓库已建立」

**plan-step 4**（34 子系统 JSON 初始化；模板校验 34 个核心 JSON，缺任意一个即 FAIL）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4
```

模板 expected_outputs 含 `_数据库/人物卡.json`、`_数据库/世界观.json`、`_数据库/进度.json`，缺失自动 FAIL。

---

## plan-end + 大纲完成 Git 快照

当大纲生成完成（大势卡、事件簇、进度和 cluster_001 brief 已写入）后，由 plan step 7 执行 schema 校验和 Git 快照：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 立即向用户报告**不要假装完成**。返回 0 才可向用户输出"大纲生成完成"。

---

# 📋 完成检查清单

向用户报告"大纲完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 13 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `_数据库/大势卡.json` / `_数据库/事件簇.json` / `_数据库/进度.json` 三件套全部落地
- [ ] `_数据库/.wal/outline_git_snapshot.json` 已写入

任何一项不达 → 不允许声称"大纲完成"。

---

# 🌊 唯一大纲模式（cluster-first）

`/outline` 不再提供逐章 strict / hybrid 分支。唯一模式是 cluster-first：

1. **只详化 `cluster_001`**：写 `scope_summary`、scene storyboard、首块 `research_ref` 和必要伏笔。
2. **生成 `_数据库/大势卡.json`**——大事件池（major_events[]），每个 ME 对应一个后续 cluster 候选方向。
   - 每个大事件含 `prerequisites` + `expected_window_after` + `physical_evidence`
   - 不指定章号，由 `/cluster-save-state` / cluster_emergence_engine 按条件涌现触发
3. **生成 `_数据库/角色池.json`**——分 core（永久）+ emerged（涌现）+ extras（一次性）
   - 仅 outline 阶段定 core 3-4 人；其他角色由 writer 即兴 spawn

## 🌊 cluster-first 工作流

```
cluster_N 写作前：
  build_manifest 调 fate_engine / cluster_emergence evaluate cluster_N
  → 输出 active_fate_events（满足 prerequisites + 在 expected_window 内的事件/ME）
  → 注入 manifest.active_fate_events 给 writer
  → writer 在整块草稿中推进 1-2 个事件，写入 cluster_changes.self_eval/waivers

cluster_N 写作后（/cluster-save-state）：
  cluster_summary_builder + archivist + foreshadower 回写账本
  → 把 triggered 事件标 completed_at_cluster=N
  cluster_emergence_engine 基于剩余 ME、用户走向和当前状态涌现 cluster_N+1
  → 检测超 expected_window 未触发事件 → 告警「下个 cluster 必须推进」
```

## 🌊 抗剧情跑偏

cluster-first 核心好处：
- **没有 ch_NNN 死定**——剧情走向影响"何时触发"，不需要改大纲
- **大势仍稳**——final_image / prerequisites 链不变
- **drift 检测**——超 window 未触发自动告警，防忘
- **lazy character**——新角色按需生，不预先列死

---

# 大纲设计要求

## 情节设计原则

1. **明确行动目标**：人物必须有清晰的行动目标，且目标来源于人物自身的欲望/困境，**禁止**以"满足设计要求"为行动目标

2. **制造阻碍**：每个目标都要设置看似难以攻克的障碍，增强戏剧张力

3. **强情绪设计**：围绕人际关系设计冲突
   - 不公、打压、误解
   - 诬陷、背叛、践踏
   - 引发读者强烈情绪共鸣

4. **信息差构建**：
   - 主角与其他角色之间存在信息差
   - 人物保有伪装、秘密、私心
   - 避免被他人轻易察觉

5. **反套路设计**：
   - 打破读者预期
   - 情节走向与常规相反
   - 人物抉择出人意料
   - 人物关系、行为逻辑、规则的反转

## Cluster 节奏分布

- **虐点密集区**（前30%）：快速建立价值洼地
- **转折觉醒区**（30-50%）：情绪缓冲与能力获取
- **实践成长区**（50-80%）：小爽点+反套路
- **升华爽点区**（最后20%）：精神境界碾压

## 伏笔埋设要求

制作伏笔时间表，标注：
- 伏笔内容
- 埋设 cluster / scene
- 回收 cluster / scene
- 回收方式

## 每个 scene 出场角色标注

`cluster_001.scene_storyboard[]` 必须标注每个 scene 的出场角色列表，格式如下：
- **出场角色**：列出本 scene 所有出场角色（含主角、配角、新登场角色）
- **关键事件**：本 scene 核心事件摘要（2-3个关键词）
- **场景类型**：标注本 scene 主要场景类型（可多选：战斗/日常/情感/悬疑/转折）
- 此标注用于后续 cluster-write（build_manifest）按需加载对应人物卡和场景规则，避免全量加载

## 情绪节奏标注

每个 scene 必须标注情绪走向和四锚点：

- **情绪值**：-10 到 +10（负值=虐/压抑，正值=爽/高潮，0=平缓）
- **情绪趋势**：↗上升 / ↘下降 / ↗↘先扬后抑 / ↘↗先抑后扬
- **四锚点**：
  - 🪝 开头钩子：3秒抓住读者的开场（悬念/冲突/反差）
  - ⚔️ 中段冲突：推动情节的核心矛盾
  - 💥 小高潮/反转：本章情绪峰值
  - 🔗 场景钩子：让读者进入下一 scene / 下一章切点的钩子

### 情绪节奏规则（硬性约束）
- 不允许连续 2 个核心 scene 以上情绪值为负（虐后必须给希望）
- 大爽点（情绪值≥8）按卷/cluster 节奏布置在 30%、60%、90% 附近
- 每个 scene 必须有至少一个情绪波动（不能全程平淡）
- scene 结尾必须有承接力；物理章节切点由 splitter 之后决定


## 人物关系变化

按 cluster / scene 标注核心人物关系的变化轨迹：
- 主角 vs 施虐者
- 主角 vs 自我认知
- 主角 vs 世界观

## 世界观条目生成

生成大纲时，为重要的物件、地点、势力、规则创建世界观条目（含触发词）：
- 每个重要设定元素对应一个 entry，包含 2-4 个触发关键词
- 关键词应覆盖该设定的常见提及方式（全称、简称、别名）
- content 字段写入该设定的详细描述（100-200字），供写作时按需注入
- priority 根据重要程度设定：核心设定 9-10，重要设定 6-8，辅助设定 3-5
- 确保每部小说至少生成 8-15 个世界观条目

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
