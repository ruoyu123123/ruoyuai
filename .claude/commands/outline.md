---
description: 生成章节大纲
---

> **三段式纪律**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [core/claude-home/HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。
> hook 自动检 research_cache + 反思文件。关键脚本输出建议过 `ai_wrapper.py` 二次复核。

## Gen-Model 抽象层

**关键变化**：大纲中**含创意笔触的字段**（卷 arc 描述 / 大事件 description / hook / cliffhanger 等）应走 gen-model；**结构性字段**（卷骨架 / 章节范围 / 事件 ID / prerequisites 关系 / 角色 ID）仍由 Claude 主代理列。

**新工作流（Step 2 卷级大纲生成）**：
1. 主代理（Claude）按原流程列**结构性骨架**（卷数 / 卷标题 / 章节范围 / key_milestones 事件 ID 列表 / 角色 anchor）
2. 含创意笔触的字段（如 `volume_arc` 段落描述、`major_events[].description` 等）由主代理准备**结构 brief JSON**
3. 调 `gen_creative.py --mode volume_arc`（placeholder，未完整实现；当前可暂用主代理直出 + 后续 fixer 润色作为兜底）
4. 主代理把 gen-model 输出合并回大纲结构，写入 `_数据库/进度.json` 的 volumes 段 + `大纲.md` 的卷描述段

**当前实施状态**：
- `gen_creative.py --mode volume_arc` 是 v2 placeholder（NotImplementedError）
- 临时方案：主代理用 brainstorm mode 间接达成（把卷骨架作为 topic 输入）
- 完整实现待后续迭代

**保持 Claude 处理的部分**：
- 卷骨架结构（卷数 / 章节范围 / event prerequisites 关系）
- cluster_blueprint 的 anchors / try_fail / info_gain / threads_advance 等结构字段
- 34 个核心子系统 JSON 初始化（plan_tracker step 3）

**当前 active gen-model**：`python core/scripts/gen_model.py show`。

---

你是一位小说大纲规划专家。请帮我制定章节大纲：

$ARGUMENTS

---

## 🛡️ Plan 强制规划

大纲流程 4 步全部挂在 plan 上——start 前必须 `plan-create` 拿 PLAN_ID，每步完成 `plan-step --n N`，末尾 `plan-end`。Hook 已强制本命令的 PLAN_ID。

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

## 叙事框架选择（v16 新增 · 对齐 SidekickWriter/好莱坞方法论）

先让用户选择叙事框架，再基于框架生成大纲：

| 框架 | 适用 | 核心节拍 |
|---|---|---|
| **三幕式**（默认）| 通用 | 建立→对抗→解决 |
| **Save the Cat** | 商业网文/电影化 | Opening Image→Theme Stated→Catalyst→Midpoint→All Is Lost→Finale |
| **英雄之旅** | 玄幻/冒险/成长 | 日常→冒险召唤→跨越门槛→试炼→深渊→回归 |
| **雪花法** | 从零构思 | 一句话→一段话→角色摘要→扩展为1页→扩展为4页→逐章展开 |
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
| 6 | 逐章展开 | 每章一行概要→完整cluster_blueprint |

**用户选"雪花法"时，引导用户从第1步开始逐步扩展，不一次性生成完整大纲。**

**plan-step 1**（框架选择属于讨论步骤，无文件型 expected_outputs）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 --skip-output
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
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 --skip-output
```

---

## 🆕 第 1.7 步：卷级 cluster 数 + writer freestyle 模式（v27）

用户原话：「每卷的故事块数量应该问询用户，然后故事块能切多少章我发现你一开始已经间接限制死了，这是不对的，应该让ai自由发挥」。

本步**必跑** — 决定每卷有多少 cluster（=多少 ME 大势事件）+ writer 是否走 freestyle（默认 true）。

### 1.7.1 询问每卷 cluster 数

**AskUserQuestion 1**：「《<书名>》第 1 卷你想要几个故事块（cluster）？」
- **选项**（推荐区间 4-12）：
  - `4-5（紧凑短篇向）` — 卷长约 50-100K 字
  - `6-8（标准长度，推荐）` — 卷长约 80-200K 字
  - `9-12（厚重长篇向）` — 卷长约 150-300K 字
  - `Other` — 用户自填数字
- 如果用户回答 N，写入 `_数据库/用户偏号.json.workflow_preferences[]`：
  ```json
  {"key": "cluster_count_per_volume", "value": N, "set_at": "ISO", "user_decided": true}
  ```

**多卷书**：若大纲含 2+ 卷，依次问每卷 cluster 数；用户可一次性给所有卷数字。

### 1.7.2 writer freestyle 模式确认

**默认开启**（推荐）：writer 不知道目标章数 + 字数 · 按 cluster.scope_summary 自由发挥 · splitter 后期按字数切。

可选问询（用户首次新书时确认 1 次，之后存入用户偏好不再问）：

**AskUserQuestion 2**：「writer 写作模式 · 推荐 v27 freestyle」
- `freestyle（v27 默认 · 推荐）` — writer 自由发挥 · splitter 按字数 3000-4500/章切 · 末章不够字数从下个 cluster 补料
- `locked（v26 兼容 · 旧）` — writer 按预定章数 + 字数硬约束写

写入 `_数据库/用户偏号.json.workflow_preferences[]`：
```json
{"key": "writer_mode", "value": "freestyle", "set_at": "ISO", "user_decided": true}
```

### 1.7.3 反推 ME 数 + 写入大势卡

**1 个 cluster ≈ 1 个 ME**（大势事件）。卷 1 用户答 N → 大势卡 V1 必须含 N+1 个 ME（含 1 个开局 ME + N-1 个推进 ME + 1 个收束 ME 弹性）。

写入 `_数据库/大势卡.json` 的 `volumes[].major_events_pool` 数组：每卷数量 = 用户答的 cluster 数 ± 1 弹性。

**禁止**：
- ❌ 在 cluster brief 写 `chapter_count_estimate` / `chapter_range` / `expected_word_range` 这 3 个字段（v27 已 deprecate · 由 splitter 切完后填）
- ❌ 在大势卡 ME 池里写「expected_chapters」字段（章数由 writer + splitter 涌现）

### plan-step 1.7

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 --skip-output
```

---

## 第 2 步：卷级大势大纲生成

本步**只描述大势**，不规划每卷章数。

| ✅ 写 | ❌ 不写 |
|---|---|
| 每卷 `core_conflict` / `volume_arc` / `key_milestones` / `ending_state` | `volumes[].chapter_range: [1, 10]` 死锁区间 |
| major_events 池含 `expected_window_after` 宽窗（如 `max_chapters: 15-100`） | 「本卷预计 N 章」类预测 |
| 每卷大势主题 + 起承转合 | events_per_volume 反推章数 |

章数由 ME 触发 + 用户涟漪选择**自然涌现**，writer/save-state 累积。

请提供：
1. 各章节的核心事件和情节点
2. 每章的人物关系变化
3. 章节之间的承接和铺垫
4. 节奏控制（高潮、过渡、转折的分布）
5. 伏笔和线索的埋设时机
6. 预估字数和篇幅分配建议

可以使用表格或列表形式，清晰展示整体结构。

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

# 数据库初始化

大纲确认完成后，自动执行以下操作：

1. `mkdir -p 小说_书名/_数据库/`
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
   - 每个条目包含触发词列表，当章节大纲中出现匹配关键词时，自动注入对应 content
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
       "tier": 1, "due_by": 15, "resolved": false,
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
5. Write `_数据库/故事块摘要.json` — 初始化 `{"schema_version": "v2.cluster", "clusters": []}`（cluster 账本主存储 · 每个 cluster 内含 chapters 映射 · 由 cluster-save-state 经 cluster_summary_builder 落库）
6. Write `_数据库/进度.json` — 初始化进度，结构如下：
```json
{
  "total_chapters": 10,
  "completed": 0,
  "current": 1,
  "words_per_chapter": 3000,
  "volumes": [
    {
      "vol": 1,
      "title": "第一卷：破局",
      "core_conflict": "本卷核心冲突",
      "volume_arc": "本卷人物弧线（起→承→转）",
      "key_milestones": ["里程碑事件1", "里程碑事件2"],
      "ending_state": "本卷末尾的故事状态"
    },
    {
      "vol": 2,
      "title": "第二卷：觉醒",
      "core_conflict": "本卷核心冲突",
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
        {"ch": 1, "title": "场景标题", "characters": ["角色A", "角色B"], "key_events": ["事件1", "事件2"], "scene_type": ["日常", "悬疑"], "emotion": {"value": 6, "trend": "↘↗", "anchors": {"hook": "开头钩子描述", "conflict": "中段冲突描述", "climax": "高潮/反转描述", "cliffhanger": "章末悬念描述"}}, "goal": "本场景核心目标", "turning_point": "关键转折", "threads_advance": ["线索ID"], "try_fail": "尝试X→失败Y→适应Z", "info_gain": "向读者释放的新信息", "payoff": "兑现的伏笔(可选)", "time_hint": "故事时间"},
        {"ch": 2, "title": "场景标题", "characters": ["角色A", "角色C"], "key_events": ["事件3", "事件4"], "scene_type": ["战斗", "转折"], "emotion": {"value": -3, "trend": "↘", "anchors": {"hook": "开头钩子描述", "conflict": "中段冲突描述", "climax": "高潮/反转描述", "cliffhanger": "章末悬念描述"}}, "goal": "本场景核心目标", "turning_point": "关键转折", "threads_advance": ["线索ID"], "try_fail": "尝试-失败-适应", "info_gain": "新信息释放", "payoff": null, "time_hint": "故事时间"}
      ]
    }
  },
  "propagation_debt": [],
  "started_at": "ISO日期",
  "last_updated": "ISO日期"
}
```
   - 🔴 **2026-05-29 复审修复[H5]**：`cluster_blueprint` **必须是 dict**（`cluster_id` → cluster 数据），**禁止初始化为 list**。SC-1 规范形态 + `cluster_lookup._iter_blueprint_ranges` 用 `.items()` 遍历 `cluster_blueprint` 取每个 cluster 的 `chapter_range` / `scene_storyboard[].ch`；写成 list 会让反查整体瘫痪（城南项目实测 list(25) 即此 bug）。
   - 🔴 **fluid 涌现纪律**：outline 阶段**只详化 `cluster_001`**（含完整 `scene_storyboard` + `scope_summary` + `foreshadowing_to_plant`）。`cluster_002+` 不预设——由 cluster-save-state step 11 涌现。
   - 🔴 **v27 freestyle 不写 `chapter_range`**：cluster 的 `chapter_range` 由 splitter 切完后回填（事件簇.json 为权威源），outline 阶段不预设。`scene_storyboard[].ch` 是 writer 蓝图序号（场景顺序），非物理章号。
   - `narrative_mode`：仅首个 cluster 默认 `"in_medias_res"`（黄金三章倒叙），后续 cluster 默认 `"linear"`。
   - `volumes`：分卷层（仅在预估 >= 20 章时生成，短篇直接跳过 volumes 字段）
   - `volume_arc`：每卷的人物成长弧线（起承转），写作时注入到章节prompt，确保章节服务于卷级目标
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
   - `workflow_preferences`：工作流偏好（如"每章2000字就够"、"不需要质量检查"）
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
   - `travel_log`：角色移动记录（save-state 自动更新）

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
  "current_time": {"day": 1, "period": "morning", "chapter": 1, "season": ""},
  "time_per_chapter": "约半天",
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

16. 初始化 Git 仓库并提交首次快照：
```bash
# 进入项目目录（路径可能含中文，必须加引号）
cd "小说_书名"

# 检查 git 是否可用（不可用则静默跳过，不阻塞主流程）
if command -v git >/dev/null 2>&1; then
  # 如已存在 .git 目录则跳过 init
  if [ ! -d ".git" ]; then
    git init -b main 2>/dev/null || git init
    # 设置本地身份（避免 commit 失败；不污染全局 config）
    git config user.name "若渝AI" 2>/dev/null
    git config user.email "ruoyuai@local" 2>/dev/null
  fi
  git add .gitignore _数据库/
  git commit -m "chore: 初始化项目 + 34 个数据库文件" 2>&1 | tail -1
fi
```

**Git 集成硬性规则：**
- ⚠️ 所有 git 命令必须用 `command -v git` 预检，无 git 时静默跳过，不阻塞主流程
- ⚠️ 路径含中文，所有 `cd` 必须加双引号
- ⚠️ 只在项目目录内操作（`git -C "小说_书名"` 或 `cd` 后再执行）
- ⚠️ 只设置本地 user.name/email（不污染用户的全局 git config）
- ⚠️ 不做 push/pull/force/reset --hard 等破坏性操作
- ⚠️ 提交失败（如 hook 失败）时在日志记录，但不中断流水线

终端输出：「✅ 数据库已初始化（34 个核心子系统 JSON），Git 仓库已建立」

**plan-step 3**（34 子系统 JSON + Git 初始化为一步；模板已配 3 个核心 JSON 校验，缺任意一个 hook `pretooluse_subsystems_gate` 拦截）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3
```

模板 expected_outputs 含 `_数据库/人物卡.json`、`_数据库/世界观.json`、`_数据库/进度.json`，缺失自动 FAIL。

---

## 第 4 步：plan-end + 大纲完成 Git 提交

# 大纲完成后的 Git 提交

当大纲生成完成（进度.json 的 volumes 和 cluster_blueprint 已写入）后，立即提交：

```bash
if command -v git >/dev/null 2>&1 && [ -d "小说_书名/.git" ]; then
  git -C "小说_书名" add _数据库/进度.json _数据库/故事块摘要.json _数据库/伏笔表.json _数据库/人物卡.json _数据库/世界观.json _数据库/地图.json _数据库/关系.json _数据库/事件表.json _数据库/时间线.json _数据库/道具.json _数据库/场景规则.json _数据库/写作经验.json _数据库/用户偏好.json
  # 如有作者风格文件，也一起纳入
  [ -f "小说_书名/_数据库/作者风格.json" ] && git -C "小说_书名" add _数据库/作者风格.json
  git -C "小说_书名" commit -m "feat: 生成大纲（N 章 / X 卷）" 2>&1 | tail -1
fi
```

commit 信息格式参考：
- 短篇（无分卷）：`feat: 生成大纲（10 章）`
- 长篇（有分卷）：`feat: 生成大纲（50 章 / 3 卷）`

**plan-step 4 + plan-end**（收尾校验本身）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --skip-output
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 立即向用户报告**不要假装完成**。返回 0 才可向用户输出"大纲生成完成"。

---

# 📋 完成检查清单

向用户报告"大纲完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 4 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `大纲.md` 落地（卷级大势文档）
- [ ] `_数据库/人物卡.json` / `_数据库/世界观.json` / `_数据库/进度.json` 三件套全部落地
- [ ] Git 初始化 commit + 大纲 commit 均已提交（git 不可用时本项免）

任何一项不达 → 不允许声称"大纲完成"。

---

# 🌊 模式选择（v20 涌现叙事 新增）

`/outline` 接受 `--mode` 参数控制 cluster_blueprint 的预定程度：

| mode | cluster_blueprint 粒度 | 适合 |
|---|---|---|
| `strict`（默认 · v19）| 全 N 章逐章 title/turning_point/key_events | 短篇/已完整构思/不会跑偏的故事 |
| **`fluid`（v20 新增）** | 仅卷级大势 + 大事件池 | 长篇/会被剧情涌现影响/需抗偏离 |
| `hybrid` | 第一卷 strict + 后续卷 fluid | 长篇但要保证开局质量 |

## 🌊 fluid 模式产物

启用 `--mode fluid` 时：

1. **不生成** 逐章 cluster_blueprint（`cluster_blueprint = {}` — 🔴 复审修复[H5]：空 dict 而非空 list，与 SC-1 规范形态一致）
2. **生成 `_数据库/大势卡.json`**——大事件池（major_events[]）
   - 每个大事件含 `prerequisites` + `expected_window_after` + `physical_evidence`
   - 不指定章号，由 fate_engine.py 按条件涌现触发
3. **生成 `_数据库/角色池.json`**——分 core（永久）+ emerged（涌现）+ extras（一次性）
   - 仅 outline 阶段定 core 3-4 人；其他角色由 writer 即兴 spawn

## 🌊 fluid 模式工作流

```
ch_N 写作前：
  build_manifest 调 fate_engine evaluate ch_N
  → 输出 active_fate_events（满足 prerequisites + 在 expected_window 内的事件）
  → 注入 manifest.active_fate_events 给 writer
  → writer 选 1-2 个事件本章推进，写入 _changes.json.fate_events_triggered

ch_N 写作后（save-state step 9）：
  fate_engine update ch_N
  → 把 triggered 事件标 completed_at_ch=N
  fate_engine drift ch_N
  → 检测超 expected_window 未触发事件 → 告警「下章必须推进」
```

## 🌊 抗剧情跑偏

fluid 模式核心好处：
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

## 章节节奏分布

- **虐点密集区**（前30%）：快速建立价值洼地
- **转折觉醒区**（30-50%）：情绪缓冲与能力获取
- **实践成长区**（50-80%）：小爽点+反套路
- **升华爽点区**（最后20%）：精神境界碾压

## 伏笔埋设要求

制作伏笔时间表，标注：
- 伏笔内容
- 埋设章节
- 回收章节
- 回收方式

## 每章出场角色标注

每章大纲必须标注本章出场角色列表，格式如下：
- **出场角色**：列出本章所有出场角色（含主角、配角、新登场角色）
- **关键事件**：本章核心事件摘要（2-3个关键词）
- **场景类型**：标注本章主要场景类型（可多选：战斗/日常/情感/悬疑/转折）
- 此标注用于后续 cluster-write（build_manifest）按需加载对应人物卡和场景规则，避免全量加载

## 情绪节奏标注

每章大纲必须标注情绪走向和四锚点：

- **情绪值**：-10 到 +10（负值=虐/压抑，正值=爽/高潮，0=平缓）
- **情绪趋势**：↗上升 / ↘下降 / ↗↘先扬后抑 / ↘↗先抑后扬
- **四锚点**：
  - 🪝 开头钩子：3秒抓住读者的开场（悬念/冲突/反差）
  - ⚔️ 中段冲突：推动情节的核心矛盾
  - 💥 小高潮/反转：本章情绪峰值
  - 🔗 章末悬念：让读者翻下一章的钩子

### 情绪节奏规则（硬性约束）
- 不允许连续2章以上情绪值为负（虐后必须给希望）
- 大爽点（情绪值≥8）放在 30%、60%、90% 位置
- 每章必须有至少一个情绪波动（不能全程平淡）
- 章末悬念不能缺失（每章必须有）


## 人物关系变化

每章标注核心人物关系的变化轨迹：
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
