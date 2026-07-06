# 模板示例：SCP 风 / 异常局 / 多身体同步 / 隐秘组织

> 从《我在异常局当临时工》项目退役提取，通用化为可复用模板。
> 适用题材：SCP 风异常收容 + 国家级隐秘部门 + 编制内/编外临时工体制 + 多身体并行金手指 + 序列力量阶梯 + 神秘学诡秘氛围

## 适用题材判定

主代理在 `/outline` 阶段，若用户题材含以下任一组合，推荐本模板：

- 「异常局 / 收容 / SCP / 应急处置部门」+「编外临时工 / 编制」
- 「多身体 / 分身 / 多线程意识 / 并行同步」类金手指
- 「序列 / 途径 / 阶梯式神秘学力量」+「禁忌代价」
- 「BookC风格 / 谨慎主角 / 隐世势力博弈」

## 包含 8 个完整 schema 示例

| 文件 | 范式价值（一句话） |
|---|---|
| `世界观.example.json` | SCP 风隐秘部门世界观范式——异常分级(A/B/C/D) + 收容体系 + 编制内 vs 编外临时工双轨体制 + 序列力量阶梯 + 隐世势力地图 + 内部派系隐藏 |
| `人物卡.example.json` | **全套最有价值**——多身体同步主角 schema（能力/记忆共享但每身体体验独立，业界少见）+ voice_pack 5 层 persona 完整结构 + MENTOR_NPC 体制内话术 catchphrase（讲古即暗喻）+ knowledge.will_learn 认识论时间线 |
| `大势卡.example.json` | 影类异常事变 + 多身体觉醒主线大势池——18 ME 涌现叙事，每个分身觉醒绑定一个剧情转折（金手指放量=剧情代价非数值刷），Save the Cat 15 拍贴合 |
| `伏笔表.example.json` | 物件锚定伏笔（Chekhov gun）范式——每条 promise 必带可触摸具象物件（笔迹同源/影残/电子表凉度/银圆环/指纹机异常），而非抽象暗示 |
| `character_arc_state.example.json` | McKee 7 步 + Truby 22 步 + Save the Cat 4 元实战填充——重点是 `_story_world.thematic_critique`（异常局=把"员工是耗材"赤裸化的隐秘批判）+ moral_argument 双立场辩论 |
| `用户偏好.example.json` | narrative_style 段（third_person_limited POV 贴主角，wizard 必问三件）+ calendar 段（异常历双轨纪年，对外公历对内异常历 OFFSET 换算）+ ECAS v23 config |
| `事件簇.example.json` | ECAS v23 cluster brief 范式——只留 1 个完整 cluster 结构 + 全字段说明（原 70KB 不全量），含 scene_storyboard 分镜 + research_ref 严格 enforce |
| `角色行动表.example.json` | offscreen / PbtA Moves 声明式行动数据范式——narrative_template 框 + anti_template 禁写法 + 三档 consequence + 过用/失声警报；招牌 move = 多身体分流 |

## 与 `urban_supernatural_business` 的区别

两套模板都是「现代都市 + 隐秘超自然」，但分属不同子题材，**不要混用**：

| 维度 | urban_supernatural_business | scp_anomaly_bureau（本模板） |
|---|---|---|
| 核心冲突 | 商战权谋 / 股权博弈 / 资本暗战 | SCP 风异常收容 / 体制压迫 / 临时工生存 |
| 组织范式 | 隐秘资本集团 / 董事会 | 国家级异常处置局 / 编制双轨 |
| 主题批判 | 资本对人的异化 | 体系把"员工当耗材"的劳动关系批判 |
| 力量体系 | 神性股权 / 契约 | 序列途径阶梯 + 禁忌代价 |
| 金手指 | （无独特金手指范式） | **多身体并行同步**（最独特，能力共享体验独立）|
| 角色基线 | 商战谋略型 | 谨慎冷峻型（HeroC复刻 / 诡秘风）|
| 独有 schema | 涟漪规则 / 时钟表 / 主角压力档 / 事件池 | 世界观异常分级 / 伏笔物件锚定 / 异常历双轨 / ECAS 事件簇 |

一句话：`urban_supernatural_business` 是**商业博弈**，`scp_anomaly_bureau` 是 **SCP 收容 + 临时工体制**。题材若偏「打工人在恐怖部门求生」选本模板；偏「资本权谋」选另一套。

## 通用化已处理

- 具体人名 → 占位符：`PROTAGONIST`（主角）/ `DEUTERAGONIST`（直属上级兼后期盟友）/ `MENTOR_NPC`（老资格导师）/ `AUTHORITY_NPC`（体制内复杂上级 false_hero）/ `PRIME_ANTAGONIST`（幕后大反派）
- 具体剧情细节 / 地名 / 伏笔内容 → 通用示例（保留"物件锚定"结构，内容改通用）
- 题材通用设定保留：异常分级 / 多身体同步机制 / voice_pack 5 层 / 异常历双轨制 / 序列代价 / ECAS 簇结构 / Chekhov 物件锚定
- 每文件顶部 `_doc` 注明退役来源 + 适用题材 + 占位符说明 + 套用方法

## 如何用

新项目开 `/outline` 时：
1. 主代理识别题材含「异常局 / SCP / 多身体 / 序列体系」→ 推荐本模板
2. 用户确认 → 拷贝 8 文件到 `workspace/novels/<book>/_数据库/`，去掉 `.example` 后缀
3. 主代理引导用户替换 5 个占位符为具体角色名 + 改具体年代/城市/途径名/伏笔内容
4. `/outline` step 1.7 配置偏好（narrative_style 三件 + calendar OFFSET + 每卷 cluster 数 AskUser），后续微调走 `/db`

## 经验沉淀（来自实际项目运行）

### 多身体同步 schema 设计要点（本模板最大价值）
- **共享 vs 独立必须分开声明**：能力/记忆共享、体验/位置/即时情绪独立——写成"提线木偶"或"主体全知"是最常见的坍塌
- 分身行动后主体靠"记忆同步回传"事后知道 → 这是 cliffhanger 的结构性来源（章末"他没买过的消费记录"）
- 金手指放量绑剧情节点（每个分身觉醒 = 一个 ME 转折），不是随意刷数值

### voice_pack 5 层设计要点
- catchphrase 必须 `{phrase, scene, frequency}` 三字段——纯数组会被 writer 锁第一个 + voice-keeper 只查命中不查分布（catchphrase 单一化陷阱）
- 每角色配 anti_patterns（禁哪些违背 persona 的写法）

### 伏笔物件锚定要点
- `physical_evidence` 必须是可触摸具象物件（笔迹/影残/凉度/圆环），不是抽象心理暗示
- `FORESHADOWING_NOT_PAID` 是 hard_gate 不可豁免——plant 时落地物件，payoff 时回收物件

### 异常历双轨纪年要点
- 正文叙事一律用内部异常历，仅对普通人掩护对话用公历
- `offset_years` = 公历年 - 异常历年（固定差值），save-state 时间线双轨都记

### ECAS 事件簇要点
- 禁止 `expected_word_range` / `word_budget`：writer 自由产整块 cluster，splitter 后续按 3000-4500 CJK/章切分
- `research_ref` v23.1 起 4 处阻断点严格 enforce，不能复用 wizard 单次调研

## 来源致谢

模板内容来自项目《我在异常局当临时工》v22.5/v23 实战运行数据。
项目退役前提取，经验沉淀为本模板供后续 SCP/异常局/多身体题材创作复用。
