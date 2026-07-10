# 开源写作系统挖掘 Round 8（2026-07-10）

> 用户新目标循环第 3 次（连续第 3 次）GitHub 挖掘轮。本轮结论：**0/45 通过北极星五问闸，判定 NO_NEW_CANDIDATES（3-strike 计数器第 3 次命中·连续三轮达成·本目标循环 GitHub 挖掘维度终止）**。

## 方法

Workflow 6 全新角度（与 round3-7 正式三振检索累计用过的 30 个角度均不重叠；实体去重覆盖 round1-7 全历史）：角色声纹LoRA微调、2026年新叙事一致性QA benchmark、游戏设计心流/节奏曲线工具、2026年上下文工程实践、低资源场景合成数据增强、人机协作写作UX研究。本次运行干净（0 real error，51/51 agent正常完成），无需重跑。45 原始候选 → 对当时 workflow 排除表（约190个已覆盖实体）去重后仍为45项 → 北极星五问闸（含强制先grep后判断纪律）→ **45/45 FAIL，无一进入对抗怀疑者复核阶段**。round7 故障批漏评30项由后续独立验收补齐，未出现在本轮45项中。

## 已评估候选清单（45个，全部 FAIL，按主要拒因分组）

### A. 角色声纹LoRA/PEFT微调（8个：全部撞上同一个架构级否决——gen-model全局锁定gemini-3.1-pro-preview闭源API，物理上无权重可插LoRA；且现有voice_pack(人物卡.json)+distill-character流水线+style_embed对比学习character task+cross_scene_voice_drift_scanner已构成"prompt层+embedding层+advisory检测层"完整闭环）
- [`weiyifan1023/Neeko`](https://github.com/weiyifan1023/Neeko)（EMNLP2024动态LoRA多角色扮演）— 落地必须另起本地弱模型产生角色台词，直接撞`feedback-writer-forbid-flash-fallback`红线；且原生场景是回合制chatbot逐utterance门控，与cluster批处理整块生成粒度不兼容
- [`yshihao-ai/HyCoRA`](https://github.com/yshihao-ai/HyCoRA)（AAAI2026超网络角色共享/专属LoRA半块）— `core/ml/style_embed`的author/character双任务对比学习已是同构解决方案(DramaCV协议)；权重级前向注入比hard_gate更彻底剥夺模型创作判断空间
- `tml1026/RoleCraft-GLM` — 当前公开 GitHub/论文索引无法唯一核验该来源，原 workflow 产物也未在仓库持久化，先因 provenance 不足直接 FAIL；不再用无法从历史文档证明的“round1/round6 已拒绝”作理由。
- [`StyleTunedLM`](https://arxiv.org/abs/2409.04574)（arXiv:2409.04574）— 架构不兼容：核心机制要求模型权重/梯度访问权限，若渝AI全部创作生成走托管闭源API
- [`WriterLoRA/WriterAgent`](https://arxiv.org/abs/2502.15616)（arXiv:2502.15616，Pastiche小说生成）— `growable_nn_architecture.md`团队自己明确写"LoRA适合>1B模型·102M直接全参数微调更好"，工程判断早已考虑过LoRA又主动放弃
- [`AuthorMix`](https://arxiv.org/abs/2603.23069)（arXiv:2603.23069，层级adapter混合风格迁移）— 全仓搜索lora/adapter关键词，若渝AI自有代码零命中，架构上从根本排除训练/混合LoRA的可能
- `Peaceuai/CharacterGLM微调框架` — 当前公开 GitHub/论文索引无法唯一核验该来源，缺少可审计实现与方法说明，未达到“成熟开源项目或论文实现”的候选门槛，直接 FAIL。
- [`Midi-Tuning`](https://arxiv.org/abs/2402.06967)（arXiv:2402.06967，多轮一致性微调）— `cross_scene_voice_drift_scanner.py`(D4/D8)+`cross_cluster_persona_drift_aggregate.py`(D5人设漂移曲线)已更精细覆盖

### B. 2026年新叙事一致性QA Benchmark（6个：全部撞上locked_fact_cross_scene_scanner.py的双通路一致性检测+ConStory-Checker/FlawedFictions两篇论文已移植落地，多个候选是这两篇姊妹论文的重新包装）
- [`ChronoQA`](https://arxiv.org/abs/2506.05939)（arXiv:2506.05939，实体-事件知识图谱RAG）— ConStory的5类一致性框架已被`tests/test_constory_consistency_gold.py`金fixture逐一翻译落地，与ChronoQA维度几乎完全对应
- [`FictionalQA`](https://arxiv.org/abs/2506.05639)（arXiv:2506.05639）— `locked_fact_cross_scene_scanner.py`的恒定数值hard_gate通路+NLI语义advisory通路已是同一机制族论文（ConStory-Checker/FlawedFictions）的落地实现
- [`STAGE`](https://arxiv.org/abs/2601.08510)（arXiv:2601.08510，全剧本推理benchmark）— knowledge_graph.json+knowledge_graph_update.py已是原生实现，且三层分工（确定性/NLI语义/多跳交reading-reflector）已标注详尽能力边界
- [`Foundations of Global Consistency Checking with Noisy LLM Oracles`](https://arxiv.org/abs/2601.13600)（arXiv:2601.13600）— `locked_fact_cross_scene_scanner.py`(642行，2026-07-07最新)双通路检测+S4/S5候选配对调度策略已从ConStory-Checker/FlawedFictions移植落地并真机反向校准
- [`QUIET`](https://arxiv.org/abs/2605.25955)（arXiv:2605.25955，多空格级联故事完形填空）— 同一套locked_fact_cross_scene_scanner.py四维度精确覆盖候选宣称的全部维度
- [`NarrativeWorldBench`](https://arxiv.org/abs/2606.17391)（arXiv:2606.17391，长时程音频剧世界模型）— `world_evolution_engine.py`+`volume_arc_drift_scanner.py`+`locked_fact_cross_scene_scanner.py`+`cross_cluster_structure_compliance_aggregate.py`四层已覆盖

### C. 游戏设计心流/节奏曲线工具（8个：narrative_rhythm_scanner.py+chronotope_typology_scanner.py+event_density_rhythm_aggregate.py+叙事节拍器.json等多层原生实现已比候选描述的通用游戏设计框架更贴合小说领域颗粒度）
- [`MAQV/Action-Block Framework`](https://doi.org/10.1145/3772318.3790625)（CHI2026开放世界任务节奏解构）— 候选"只打单一标量"的技术描述准确，但"缺多维分类"的推论错误：chronotope_typology_scanner.py(7类场景)已补足
- [`PaceMaker`](https://arxiv.org/abs/2408.15001)（arXiv:2408.15001）— `cluster_emergence_engine.py`的`stakes_delta`+`emergence_transparency.py`的`state_delta_preview`已让用户看到候选强度差异
- [`GameFlow Model`](https://doi.org/10.1145/1077246.1077253)（Sweetser & Wyeth 2005）— `hook_strength_scanner.py`docstring自称"F层读者体验"检测器，候选"缺读者体验顾问层"前提被源码直接证伪
- [`Jenova Chen Flow in Games`](https://www.jenovachen.com/flowingames/Flow_in_games_final.pdf)（MFA论文）— 心流二维框架已拆解到至少7个不同粒度原生scanner，且带正确的"事后聚合→反哺下次涌现"顺序（候选提议顺序反了）
- [`The Level Design Book Pacing章节`](https://book.leveldesignbook.com/process/preproduction/pacing) — 叙事节拍器.json(34子系统之一，docstring自称"pacing metronome")已实现强度-时间曲线+节拍类别+峰谷对照，比候选描述更精细
- `Daniel Cook Skill Atoms` — "一次涌现太多新元素导致认知超载"这一失效模式从未在钟楼弃儿/天灾/雾岛信标/惊悚乐园等真实项目历史bug记录里出现过
- `Jesse Schell Lens of Flow` — `.claude/agents/novel-outline-planner.md`的S11场景三问(2026-07-07新增)已明确借鉴moyin-creator（本身已在排除清单）同源机制
- [`Machinations`](https://machinations.io/)（Joris Dormans仿真工具）— `cluster_emergence_engine.py`的7维启发式打分系统已实现"可解释信号帮助设计者判断"的价值诉求

### D. 2026年上下文工程实践（8个：build_manifest.py的P0/P1/P2三级优先级+manifest_budget.py的STATIC/SEMI_STATIC/DYNAMIC四级缓存分层+_build_cache_layout已是比候选描述更精细的原生实现，且多篇候选论文的核心机制已被此前S1/P0等批次移植落地）
- [`Prompt Poet`](https://github.com/character-ai/prompt-poet)（Character.AI）— must_read P0/P1/P2 + manifest_budget.py SECTION_TIERS + _build_cache_layout四级已比候选单一数值truncation_priority更精细
- [`CacheWeaver`](https://arxiv.org/abs/2606.19667)（arXiv:2606.19667）— `_build_cache_layout()`(L6793-6878)已实现"按STATIC→SEMI_STATIC→DYNAMIC顺序排放命中Anthropic API prefix cache"同一问题空间
- [`12-Factor Agents Factor 3`](https://github.com/humanlayer/12-factor-agents/blob/main/content/factor-03-own-your-context-window.md) — must_read P0/P1/P2三级优先级+locked_facts/到期伏笔动态P0标记已比候选设想的静态规则更精细
- `Google ADK 三层编译视图架构` — grep确认全项目从未有"注入顺序导致风格/一致性bug"的真实观测记录，候选是理论优化而非真实需求
- [`The Instruction Hierarchy`](https://arxiv.org/abs/2404.13208)（OpenAI，arXiv:2404.13208）— "作者档第一权威"模式已在88个文件出现151次，`_authority`字段+`_gate_level_for`唯一裁决函数已是结构化优先级机制
- [`Context Rot`](https://www.trychroma.com/research/context-rot)（Chroma Research）— `gen_writer.py`的`_ctx_reorder_mode()`/`_skill_primacy_mode()`两个已生效(非影子)的P0位置层修复已解决同一lost-in-the-middle问题
- [`DRAGged into Conflicts CONFLICTS benchmark`](https://arxiv.org/abs/2506.08500)（arXiv:2506.08500）— `pre_write_gate.py`(2026-07-07新增)已实现"分类型摆明冲突不额外投票裁决"的同构机制
- [`RCR-Router`](https://arxiv.org/abs/2508.04903)（arXiv:2508.04903，多agent结构化内存路由）— `manifest_budget.py`的T0-T3+META五档分层预算已是候选"硬token预算"机制的更精细实现（来源标注PlotPilot移植）

### E. 低资源场景合成数据增强（7个：SkillOpt训练循环冻结生成模型权重只编辑skill.md文本、全链路无LLM权重级微调，候选前提"数据量不足需要合成数据训练"在此架构下无消费入口）
- [`Self-Alignment with Instruction Backtranslation`](https://arxiv.org/abs/2308.06259)（Humpback，arXiv:2308.06259）— `distill_replicate.py`证实复刻验证协议是内容无关的纯风格统计比对，Humpback要解决的"指令-产出训练对稀缺"问题无消费入口
- [`Source2Synth`](https://arxiv.org/abs/2409.08239)（arXiv:2409.08239）— `skill_opt/`全部核心模块+`distill_holdout.py`已核查，无对应落地土壤
- [`Is Model Collapse Inevitable?`](https://arxiv.org/abs/2404.01413)（arXiv:2404.01413）— 架构/粒度不兼容：论文研究"生成式模型逐代梯度再训练"，若渝AI的gen-model是冻结外部API，SkillOpt只做skill.md文本bounded patch，训练范式完全不同构
- [`LLM2LLM`](https://github.com/SqueezeAILab/LLM2LLM)（SqueezeAILab）— gen-model全局冻结锁定，skill_opt只编辑文本不训练权重；且lessons目录无"无差别扩容稀释风格"真实历史bug记录
- [`AugGPT`](https://arxiv.org/abs/2302.13007) — SFS小样本/高方差校准问题已被"拉更多真实原文章节"方案解决并实证(蛊真人v0-v4回测SFS+13.6~+17.4分)，候选是次优方案
- [`DiaSynth`](https://aclanthology.org/2025.findings-naacl.40/) — `/distill-character` step3(voice-sample-gen)已是Claude抽取真实历史对白+gen-model生成style_samples的完整实现，2026-06-27已上线且有PROCESS-INTEGRITY hard gate
- [`Building a Family of Data Augmentation Models`](https://arxiv.org/abs/2412.04871)（arXiv:2412.04871）— 全仓核实唯一本地训练模型是coherence_binary/emotion_vad/style_embed/surprisal_gpt2，均非LLM权重训练

### F. 人机协作写作UX研究（8个：cluster_emergence_engine.py的2-3候选走向卡+emergence_transparency.py的decision_basis透明化+preference_ranker.py的BPR偏好学习已是候选HCI论文呼吁机制的超额实现，多篇明确溯源到PlotPilot——已在排除清单）
- [`CoAuthor`](https://doi.org/10.1145/3491102.3502030)（CHI2022，Lee et al.）— `user_choice_learner.py`+`preference_ranker.py`已是三重覆盖的原生实现
- `Choice Over Control`（CHI2023，Dang et al.）— 当前摘要不足以唯一还原官方来源，先因 provenance 不足 FAIL；即使只按摘要主张判断，“2-3候选优于单一建议+用户选择”也已由走向卡+BPR实现。
- [`Suggestion Lists vs. Continuous Generation`](https://doi.org/10.1145/3543758.3543947)（Mensch und Computer 2022）— 走向卡机制已是教科书级"list selection"范式，emergence_transparency.py header明确写"借鉴PlotPilot storyline DAG"
- [`Co-Writing with AI, on Human Terms`](https://doi.org/10.1145/3757566)（PACM HCI/CSCW 2025）— cluster-save-state.md第13步"唯一停顿点"已完整实现候选四类干预策略
- [`Guidelines for Human-AI Interaction`](https://doi.org/10.1145/3290605.3300233)（Amershi et al., CHI2019, Microsoft）— G3等准则已在CLAUDE.md主链明文规定+emergence_transparency.py两个专属回归测试覆盖
- [`From Tool to Companion`](https://doi.org/10.1145/3532106.3533506)（DIS2022）— `_build_decision_basis`(约L444-494)已对每个候选统一输出10字段决策依据，是候选呼吁"展示更多因果依据"的满配实现
- [`Luminate`](https://doi.org/10.1145/3613904.3642400)（CHI2024，Suh et al.）— 候选"3张卡片本质雷同"假设前提被证伪：候选卡片从来不是同一prompt重复采样，而是从大势卡ME池预先author的不同方向
- `Holding the Line`（2024，写手对AI协作态度研究）— 当前摘要不足以唯一还原官方来源，先因 provenance 不足 FAIL；即使只按摘要主张判断，`user_choice_learner.py`+`preference_ranker.py`+`direction_card_poetics_scanner.py`也已覆盖“从走向卡选择学用户偏好”机制。

## 方法论纪律执行情况

本轮完整保留 45 个候选的逐项身份：38 项附有唯一确认的官方来源链接，7 项无法唯一确认而保持纯文本，并按 provenance 不足从紧判定，禁止猜链。gate contract 强制先联网核原文、再 Read/Grep 当前源码后判断。本轮进一步印证 round7 发现的模式：多个候选（`ChronoQA`/`FictionalQA`/`STAGE`/`QUIET`/`Suggestion Lists`）本质是此前已挖掘论文（ConStory-Checker/FlawedFictions/PlotPilot）的同一研究脉络延伸或引用。

## 终止后独立抽验（不计入新一轮）

为确认“连续三轮空结果”不是搜索通道自证，额外从全新角度抽验 [`qkaren/Counterfactual-StoryRW`](https://github.com/qkaren/Counterfactual-StoryRW) / [Counterfactual Story Reasoning and Generation](https://arxiv.org/abs/1909.04076)。其真实任务是 ROCStories 五句样本中人工替换第 2 句，再最小改写后 3 句；没有显式因果图或后果传播器。五问均不成立：英文五句样本不贴作者风格、非 cluster、固定反事实干预不是涟漪涌现、句位被硬编码为格式目标、条件重写器直接决定内容。结论 `FAIL`，不计入 round9 或三振统计。

## 状态

**3-strike 计数器：第 3 次连续命中 NO_NEW_CANDIDATES。达成用户"连续三轮搜索都找不到任何值得集成的候选为止"终止条件。**

**本用户目标循环（2026-07-09设定）的 GitHub/论文挖掘维度到此完成**：round6(45候选)+round7(74候选)+round8(45候选)累计 **164 个去重候选**，0 个通过北极星五问闸+对抗复核。加上此前 round3-5 三振循环的 96 个候选，两个三振循环在 **round3-8 共 6 轮、36 个不同搜索角度、260 个候选**中无一值得集成。round1-2 属早期机制移植阶段，确有多项成功落地，不纳入“0通过”统计。由于本循环没有候选通过对抗复核，集成代码改造和一次性新书真实 API 条件均未触发；本次候选集合补核与文档/规则修订另行通过全量回归 `8713 passed, 13 skipped`。
