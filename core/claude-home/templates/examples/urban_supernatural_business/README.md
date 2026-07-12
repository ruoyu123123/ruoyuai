# 模板示例：都市超自然商战题材

> 适用题材：现代都市 + 隐秘超自然 + 商战权谋（神性股权 / 资本暗战 / 隐秘董事会博弈 / 克苏鲁商业版）

## 适用题材判定

主代理在 `/outline` 阶段，若用户题材含以下任一组合，推荐本模板：

- 「商战 / 股权博弈 / 资本暗战 / 董事会权谋」+「隐秘超自然」
- 「神性 / 灵格金融化 / 契约力量」类金手指
- 「普通职员卷入豪门或财团博弈」+「都市异闻」

## 包含 9 个完整 schema 示例

| 文件 | 范式价值（一句话） |
|---|---|
| `大势卡.example.json` | 神性股权战主线大势池——13 ME 分 4 卷（每卷=1 阶段触发点）涌现叙事，Save the Cat 15 拍贴合商战升级，每卷末 ME 标 is_volume_finale，ME id 格式 `ME-V<卷号>-<序>` |
| `character_arc_state.example.json` | McKee 7 步 + Truby 22 步 + Save the Cat 4 元——重点 `_story_world.thematic_critique`（资本把'天命'金融化的批判）+ moral_argument 双立场辩论，stage 锚定用 `stages_by_cluster` |
| `涟漪规则.example.json` | 小势→世界状态涟漪规则（北极星②）——minor_event / fate_event / auto_tick 三类 trigger，fate_event 引用大势卡 ME id，含 `RR_AUTO_TICK` 每故事块世界推进 |
| `角色行动表.example.json` | PbtA Moves 声明式行动数据——每角色一组 move（trigger + `narrative_template` 框 + consequence + `frequency_per_cluster` 防公式化），`characters{}.moves[]` + `offstage_threads{}` |
| `群像档.example.json` | Stardew / Hades 借鉴——核心配角 `heart_events` 强制揭密 + `schedule` 日常 + `tier_unlocks` 档位语义，揭密节奏不靠 AI 凭感觉 |
| `枢纽场景.example.json` | IF 枢纽场景——3 HUB（家 / 办公室 / 便利店）anchor_props / routines，depart / quest / return / idle 呼吸节奏，避免场景全飘在外 |
| `时钟表.example.json` | 剧情时钟（倒计时压力）——反派耐心 / 伏笔到期 / 阴谋进度显式建模，满格 `trigger_on_max` 引用大势卡 ME id |
| `主角压力档.example.json` | CK3 借鉴——违背性格 +stress，满则 `mental_break` 抽卡永久改写 persona，5 卡覆盖 5 个心理出口 |
| `事件池.example.json` | Wildermyth 命运抽签——`emergent_opportunities` 触发时从池子按 `context_filter` 过滤加权随机抽 1，AI 只写抽中的 `narrative_seed` |

## 与 `scp_anomaly_bureau` 的区别

两套模板都是「现代都市 + 隐秘超自然」，但分属不同子题材，**不要混用**：

| 维度 | urban_supernatural_business（本模板） | scp_anomaly_bureau |
|---|---|---|
| 核心冲突 | 商战权谋 / 股权博弈 / 资本暗战 | SCP 风异常收容 / 体制压迫 / 临时工生存 |
| 组织范式 | 隐秘资本集团 / 董事会 | 国家级异常处置局 / 编制双轨 |
| 主题批判 | 资本对人的异化（天命被金融化） | 体系把「员工当耗材」的劳动关系批判 |
| 力量体系 | 神性股权 / 灵格契约 | 序列途径阶梯 + 禁忌代价 |
| 独有 schema | 涟漪规则 / 时钟表 / 主角压力档 / 事件池 | 世界观异常分级 / 伏笔物件锚定 / 异常历双轨 / ECAS 事件簇 |

一句话：本模板是**商业博弈**，题材偏「资本权谋」选它；偏「打工人在恐怖部门求生」选另一套。

## 占位与约束

- 具体人名保留为示例占位：陆衍（主角）/ 顾沉（招安派反派）/ 林晚秋（卧底副线）/ 老周（情报盟友）/ 陆爸（至亲）/ 南海资本（本土大反派财团）
- 剧情细节、地名和事件内容均为可替换示例
- schema 对齐当前权威骨架 `subsystem_skeletons.json`：`schema_version` + canonical 顶层键 + cluster 为单位的规划锚定

## 如何用

新项目开 `/outline` 时：
1. 主代理识别题材含「都市 + 超自然 + 商战」→ 推荐本模板
2. 用户确认 → 拷贝 9 文件到 `workspace/novels/<book>/_数据库/`，去掉 `.example` 后缀
3. 主代理引导用户改具体名词（势力名 / 角色名 / 地点名 / 事件名）
4. `/outline` 配置偏好（含每卷 cluster 数 AskUser），后续微调走 `/db`

## 设计要点

### 大势卡设计要点（卷=阶段触发点）
- 每卷 = 1 个阶段（副本 / 大方向），有 `volume_core_conflict`（卷核心任务）+ `volume_thread`（卷线索）+ `volume_finale_signal`（换卷触发）
- ME 池 = 本卷内小走向候选，每 ME = 1 cluster = 1 小走向，携 `stakes_delta` try-fail 递增；禁单 cluster 覆盖整卷
- 换卷 = 核心任务解决 + 跃迁信号任一（①力量 / 身份 ②舞台 / 地理 ③反派 / 矛盾）
- 每 ME 必含 `physical_evidence`（writer 写正文必落地）；`downstream_unlocks` 形成 DAG 防孤立
- 章数 fluid：不锁 target_chapter_count，由 cluster 涌现 + splitter 按字数切自然涌现

### character_arc 设计要点
- lie ≠ misbelief；want ≠ desire；need 一定与 ghost 关联
- moral_argument 必有 thesis vs antithesis（与对手方辩论）
- stage 锚定用 `stages_by_cluster`（cluster 为单位，不锁章号）；`*_at_cluster` 锚点同理

### Moves 设计要点
- 每角色 4-6 个 move 够用（过多会冲突 `frequency_per_cluster`）
- `narrative_template` 用 `<占位符>` 而非具体句子（writer 抽取时填充）
- consequence 写双面（「+信息收集」与「对方可能察觉他在打量」）

### 涟漪规则设计要点
- `RR_AUTO_TICK` 必须有（每故事块自动推进世界）
- ripple 的 delta 控制在 ±2-5，避免单次跳变过大
- fate_event 的 `trigger_match` 引用大势卡 ME id（格式 `ME-V<卷号>-<序>`）

### Mental Break 卡设计要点
- 5 张卡覆盖 5 个心理出口（黑化 / 酗酒 / 出家 / 封心 / 嗜杀）
- 不同题材组合不同（治愈系去嗜杀 / 黑深残去出家）
- `permanent_persona_changes` 必明确改哪个 trait
