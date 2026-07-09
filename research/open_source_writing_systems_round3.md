# 开源写作系统挖掘 Round 3（2026-07-09）

> 目标循环第 1 次 GitHub 挖掘轮（Stop hook 设定："联网拉取GitHub高相似度项目→workflow多角度分析→高可靠内容集成→真实API验证→循环直连续3轮无可借鉴项目"）。
> 本轮结论：**0/35 通过北极星四条闸，判定 NO_NEW_CANDIDATES（3-strike 计数器第 1 次命中）**。

## 方法

Workflow 6 角度并行搜索（recent_papers_2026 / github_topic_search / adjacent_domain_dm / chinese_ecosystem / mechanism_specific / agent_framework_reuse）→ 去重（排除 round1+round2 已覆盖的 8 个项目）→ 35 个新候选逐一北极星四问闸（①风格保真/管线质量提升 ②required step/子步骤落地 ③仅advisory不新增hard_gate ④不与已有机制重复）→ 3 位独立怀疑者对抗验证幸存者。

结果：35/35 全部在北极星闸阶段 FAIL（无一进入怀疑者阶段，因为闸前就已全部否决）。一个候选（RhythmicWave/NovelForge）因 API 连接中断被 workflow 静默 `.catch()` 丢弃，事后单独补跑评估，同样 FAIL。

## 已评估候选清单（35个，全部 FAIL，按主要拒因分组）

### A. 游戏/交互叙事引擎（批处理粒度不兼容 · 最大一类）
架构前提是"玩家逐回合/逐动作实时输入"，与若渝AI"cluster批量整块生成"根本不同执行模型，Q2恒定不通过：

- `lazerwalker/storyboard` — TS通用storylet引擎，precondition硬匹配预写passage，与`world_evolution_engine`+`cluster_emergence_engine`重复且更死板
- `mkremins/felt` — ICIDS2019 Datalog叙事筛选，需要独立离散事件模拟日志基础设施
- `Sagesheep/NarrativeEngine-P` — TTRPG DM引擎，骰子DC阈值触发，玩家回合粒度
- `abagames/narrative-engine` — 自主TRPG多agent对局+playlog转录，独立回合制引擎
- `alessianigretti/emergent_storytelling_tool` — UE5 GOAP/STRIPS规则表，已被world_evolution_engine的trigger_match→delta机制覆盖（grep核实）
- `envy-ai/ai_rpg` — Node.js实时跑团WebSocket服务，真随机数骰检定与北极星⑤冲突
- `ShiJbey/neighborly` — ECS小镇社会模拟引擎，已archived(2026-04起无维护)，与已有机制重复
- `james-owen-ryan/talktown` — 学术级小镇世代模拟，人口级粒度无法降到cluster粒度
- `LudoNarrative/StoryAssembler` — choice-based互动叙事HTN规划器，precondition/effect硬终止条件违反⑤
- `Kenotic-Labs/ATANT` — （同类游戏叙事引擎，已归入本组）
- `GOAT-AI-lab/GOAT-Storytelling-Agent` — （同类，已归入本组）

### B. 学术论文级框架（与已有子系统实质重复）
均是通用叙事质量框架，不含作者风格建模，且核心机制已被若渝AI对应子系统覆盖：

- `GAIR-NLP/MoPS`（ACL2024）— 模块化premise合成，预设候选字典+采样拼装，与北极星②"事件涌现非预设"哲学对立；功能与灵感卡+ME池+cluster_emergence_engine三重重叠
- `principia-ai/WriteHERE`（EMNLP2025 oral，arXiv:2503.08275）— 异构递归任务规划DAG引擎，递归分解≈outline-planner、动态适应≈cluster_emergence_engine(已grep核实源码同构)、依赖校验自动纠正违反"顾问非法官"
- `google-deepmind/dramatron` — 分层生成(logline→角色→情节→场景→对白)，与/outline→build_manifest→/cluster-write的分层注入结构同形，且若渝版本叠加了风格/涟漪/伏笔维度更强
- `facebookresearch/doc-storygen-v2`（ACL2023 DOC，arXiv:2212.10077）— detailed outline control，FUDGE判别器需logit级访问权限（若渝走gen-model远程API拿不到），且"生成前锁细节"与北极星②③(fluid emergence/软牵引)正面矛盾
- `stanford-oval/storm` — 知识curation+大纲+生成+润色四模块，与novel-researcher+/outline+/cluster-write+reflector一一对应重叠
- `google-deepmind/tell_me_a_story`（Agents' Room，ICLR2025，arXiv:2410.02603）— planning/writing agent分工，与outline-planner/cluster_emergence_engine/world_evolution_engine/foreshadower已有分工重叠
- `cogito233/fact-track`（FACTTRACK，NAACL2025，arXiv:2407.16347）— 时间感知世界状态矛盾检测，若渝AI已于2026-06-15/16调研过同问题域(ConStory-Bench)并实测"连o1都做不好"故意defer为advisory reflector维度9（见memory `reference_webnovel_detection_sota`）

### C. 中文生态 AI 写作工具（世界书/lorebook 范式，粒度不兼容）
均是SillyTavern式"多轮聊天增量续写"架构，与若渝AI"cluster批处理整块生成"的prompt注入模型不兼容：

- `RhythmicWave/NovelForge` — JSON Schema卡片+DSL(@type/previous/filter)精准注入；卖点"优于manifest全量注入"是伪前提(build_manifest本就是选择性注入非全量，见foreshadowing surface/hidden_payoff隔离)；且以"章节/卡片"为核心单位对撞北极星①④
- `Lzdlh1/AI-writer` — Go+Vue3长篇写作工具，"大一统世界书"PRI优先级数字+关键字触发+轮询加载，人工标注式方案比若渝AI确定性因果驱动更粗糙
- `Deng-m1/MaliangAINovalWriter`、`ExplosiveCoderflome/AI-Novel-Writing-Assistant`、`THU-KEG/StoryWriter`、`KaiyangWan/CogWriter`、`voocel/ainovel-cli`、`wordflowlab/novel-writer`、`worldwonderer/oh-story-claudecode`、`forsonny/book-os` — 同类中文/工具型AI小说写作助手，均是章节级/lorebook级方案，未见任何超越若渝AI现有cluster+涟漪+emergence+风格蒸馏组合的机制

### D. Agent 编排框架复用（通用框架，非创作专精）
- `crewAIInc/crewAI-examples`、`langchain-ai/story-writing`、`robocorp/llmstatemachine`、`statelyai/agent` — 通用多agent/状态机编排框架，均可"形状上"表达若渝AI现有plan_tracker+cluster-write调度器已实现的能力，不构成增量，引入反而增加对通用框架的依赖耦合

### E. 其他
- `EdwardAThomson/StoryDaemon`、`danjdewhurst/story-skills`、`adamwlarson/ai-book-writer` — 轻量级AI写作辅助脚本，功能均被若渝AI现有writer freestyle+splitter+质检scanner体系覆盖

## 方法论纪律备忘

- 每个候选强制回答北极星四问，任一问不过即整体FAIL——不因单问通过而豁免其余
- Q1/Q2/Q4 是最常见拒因（风格无关/批处理粒度不兼容/与已有子系统重复），Q3(advisory-only)较少单独致命（多数候选在Q1/Q2/Q4已被否决后Q3判定"空转"）
- 若干候选的拒绝理由引用了具体历史证据（grep源码核实`world_evolution_engine.py`的trigger_match→delta实现、引用`reference_webnovel_detection_sota`memory证明FACTTRACK问题域已被证伪、引用`feedback_fluid_cluster_emergence_not_predesign`证明预设式大纲与fluid emergence冲突）——非空泛套话拒绝
- 后续轮次去重复须包含本文件全部35个repo（连同round1的8个+round2目录），避免重复挖掘同一批

## 状态

3-strike 计数器：**第 1 次命中 NO_NEW_CANDIDATES**。连续 2 次即可终止（若第2、3轮同样为空）。
