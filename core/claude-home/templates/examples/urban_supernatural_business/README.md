# 模板示例：都市超自然商战题材

> 从某 SCP/隐秘组织博弈类已退役项目提取，通用化为可复用模板。
> 适用题材：现代都市 + 隐秘超自然 + 商战权谋（如 SCP / 克苏鲁商业版 / 隐秘组织博弈）

## 包含 9 个完整 schema 示例

| 文件 | 系统 | 说明 |
|---|---|---|
| `大势卡.example.json` | v20 fate_engine | 13 个 ME 完整范例（卷一-卷十） |
| `涟漪规则.example.json` | v20.1 world_evolution | 7 条 ripple_rules（minor/fate/auto_tick）|
| `character_arc_McKee_Truby.example.json` | v21 R3.1 | 主角+IC 完整 McKee 7 步 + Truby ghost + moral_argument |
| `角色行动表.example.json` | v21 R2.2 PbtA | 主角/反派/盟友 3 角色 Moves 完整清单 |
| `群像档.example.json` | v21 R1.5 Stardew | 4 NPC heart_events + schedule + tier_unlocks |
| `枢纽场景.example.json` | v21 R2.1 IF | 3 hubs（家/办公室/常去地）anchor_props/routines |
| `时钟表.example.json` | v21 R1.1 Citizen Sleeper | 3 clocks（反派耐心+伏笔到期+阴谋进度）|
| `主角压力档.example.json` | v21 R1.3 CK3 | 5 张 mental_break 卡完整定义 |
| `事件池.example.json` | v21 R1.4 Wildermyth | 6 事件命运抽签池 + context_filter |

## 通用化已处理

- 具体名词改为占位符或保留为示例值
- `_doc` 字段补充「如何套用本题材到其他作品」说明
- schema 字段保持原样（直接复用 v21 全 schema）

## 如何用

新项目开 `/outline` 时：
1. 主代理识别题材含「都市+超自然」或「商战」→ 推荐本模板
2. 用户确认 → 拷贝 9 文件到 `workspace/novels/<book>/_数据库/`
3. 主代理引导用户改具体名词（势力名/角色名/地点名/事件名）
4. `/outline` step 1.7 配置偏好（含每卷 cluster 数 AskUser），后续微调走 `/db`

## 经验沉淀（来自实际项目运行）

### 大势卡设计要点
- 13 个 ME 跨 10 卷分布合理（卷一 1-2 个 / 卷二-八 各 1-2 / 卷九-十 收束）
- 每个 ME 必含 `physical_evidence`（writer 写正文必落地）
- `expected_window_after.max_chapters` 用 30-50 章（避免过窄）
- `downstream_unlocks` 形成 DAG，防孤立 ME

### 涟漪规则设计要点
- `RR_AUTO_TICK` 必须有（每章自动推进世界）
- ripple 的 delta 控制在 ±2-5，避免单次跳变过大
- `add_thread` 必带 `expected_complete_ch`（给 evaluate_completion 用）
- `spawn emergent_opportunities.expires_chapters` 设 4-8 章

### character_arc_state v2.0 设计要点
- **lie ≠ misbelief**：lie 是主角自欺，misbelief 是错误认知
- **want ≠ desire**：want 是表层目标（普通生活），desire 是 want 的具象形式
- **need 一定与 ghost 关联**（ghost = 创伤导致主角看不见 need）
- **moral_argument** 必有 thesis vs antithesis（与 IC 辩论）

### Moves 设计要点
- 每角色 4-6 个 Moves 够用（多了会冲突 frequency_per_chapter）
- `narrative_template` 用 `<占位符>` 而非具体句子（writer 抽取时填充）
- consequence 写双面（"+ 信息收集"和"对方可能察觉到他在打量"）

### Mental Break 卡设计要点
- 5 张卡覆盖 5 个心理出口（黑化/酗酒/出家/封心/嗜杀）
- 不同题材组合不同（治愈系去嗜杀 / 黑深残去出家）
- `permanent_persona_changes` 必明确改哪个 trait

## 与 /outline 协同

用户跑 `/outline`（step 1.7 偏好问询）时主代理可：
- 题材选「都市超自然商战」→ 自动建议「使用本模板」
- 自动跳过部分通用问（已模板化）
- 仅问题材-specific（具体势力名 / 主角姓名 / 时间背景年代 / 每卷 cluster 数）

## 来源致谢

模板内容来自一个 v21 时代退役项目的实战运行数据，已脱敏。
项目已退役，经验沉淀为本模板供后续创作复用。
