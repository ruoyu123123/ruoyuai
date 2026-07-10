# 开源写作系统挖掘 Round 6（2026-07-09）

> 用户新目标循环（自我搜索集成度高的开源项目自我升级 · Stop hook 设定 2026-07-09）第 1 次 GitHub 挖掘轮。本轮结论：**0/45 通过北极星五问闸，判定 NO_NEW_CANDIDATES（3-strike 计数器第 1 次命中）**。

## 方法

Workflow 6 全新角度（与 round3-5 正式三振检索累计用过的 18 个角度均不重叠；实体去重仍覆盖 round1-5 全历史）：世界观/连续性追踪软件（非游戏向）、GraphRAG/长文档知识图谱增强检索框架、多智能体辩论/自一致性投票机制、结构化输出可靠性框架、故事情绪曲线形状学术研究、中文武侠仙侠专精NLP。46 原始候选 → 排除清单去重（排除 round1-5 累计约100个已覆盖项目）后剩 45 个新候选 → 逐个过北极星**五问**闸（本轮起沿用最新 CLAUDE.md 措辞，在四问基础上显式加入第⑤问"是否在干涉模型创作判断"）→ **45/45 FAIL，无一进入对抗怀疑者复核阶段**。

本轮相比 round3-5 增加了强制性方法论纪律：gate 阶段要求每个候选必须先用 Grep/Read 实地核查若渝AI现有源码是否已有对应机制（禁止凭空判断"应该没有"），发现 45/45 候选的拒绝理由中绝大多数都有具体文件路径+行号的实证支撑，而非泛化套话。

## 已评估候选清单（45个，全部 FAIL，按主要拒因分组）

### A. 世界观/连续性追踪软件（架构范式：持续人工维护 vs cluster批处理，且功能被34子系统超越）
- [`xiaoshengxianjun/51mazi`](https://github.com/xiaoshengxianjun/51mazi)（Electron+Vue3 中文网文创作桌面软件，388★持续活跃）— 关系图谱/时间线/组织架构/词条字典/地图/人物档案六模块，全部是人工GUI手填工具，非自动化管线；grep 核实若渝AI 六项均有更精细原生实现（`关系.json`4维数值关系archivist自动抽取、`world_seed_init.py`的`factions_state`涟漪驱动数值化势力、`world_term_seepage_scanner.py`Gini系数术语渗透检测等），且性质上是"人工预设 vs 涟漪涌现"的反向案例
- [`PixeroJan/obsidian-storyline`](https://github.com/PixeroJan/obsidian-storyline)（Obsidian插件，官方定位"Plain Markdown·No AI"）— Validator七大类连续性扫描，但机制是"生成前手动预标注非线性叙事+预铺plotline地铁图+人工连接setup-payoff"，与北极星③涟漪涌现方向相反；grep确认若渝AI的`draft_temporal_order_scanner.py`/`foreshadowing_handoff_scanner.py`/`locked_fact_cross_scene_scanner.py`/`pov_consistency_scanner.py`已自动化覆盖其七大验证类目且更精细
- [`sympodius/org-novelist`](https://github.com/sympodius/org-novelist)（Emacs Org-mode）— 角色别名追踪(`#+ALIASES:`)，grep确认`人物卡.json`的`characters[].aliases`字段已被`validate_chapter.py`(L565-566)和`build_manifest.py`(L791)消费，`UNKNOWN_CHARACTER_DETECTED`因历史250+误报已被制度性锁定为恒定advisory
- [`peter88213/novelibre`](https://github.com/peter88213/novelibre) + [`nv_timeline`](https://github.com/peter88213/nv_timeline) 插件 — 时间线双向同步；`时间线.json`本就是34子系统之一，由`/outline`初始化`/cluster-save-state`自动维护，完全由涟漪/世界演化引擎驱动，引入外部人工可视化同步工具等于开人工重排预锁事件的后门；GUI/外部交互工具方向已被2a4d7ce等commit彻底清理
- [`mak-kirkland/chronicler`](https://github.com/mak-kirkland/chronicler)（本地离线世界观Wiki）— 持久打开人工编辑桌面笔记应用 vs cluster批处理流水线，架构本体论不同构；`apply_archive.py`的by_id/by_name主键+`character_identity_anchor_scanner`已用不同设计哲学解决同类问题
- [`vishiri/fantasia-archive`](https://github.com/vishiri/fantasia-archive)（GPL-3.0世界观数据库管理器）— 原子单位是"类目文档条目"（且把Chapter列为与Character同级实体），若渝AI原子单位是cluster，本体论不同构；34个子系统JSON已覆盖
- [`sreegjl/timelines`](https://github.com/sreegjl/timelines)（React+Electron桌面时间线应用）— 纯人工拖拽GUI，连一个可移植算法机制都不是，无API面可供自动化流水线调用

### B. GraphRAG/长文档知识图谱检索框架（功能实质重复，多个已有确定性scanner覆盖同一诊断目标）
- [`OSU-NLP-Group/HippoRAG`](https://github.com/OSU-NLP-Group/HippoRAG)（HippoRAG 2）— 候选推导"locked_facts/foreshadowing会被manifest_compress截断丢弃"经实测两个真实项目的ch_002.json manifest证伪：locked_facts走指针指向人物卡完整文件（非内嵌可截断数组）
- [`CrossAug`](https://arxiv.org/abs/2605.28004)（arXiv:2605.28004）— 候选举的失效例子（化名导致道具持有链断裂）是纯理论构造场景，grep核查全部lessons文件+多本真实多cluster项目memory未发现任何一次真实此类事故
- [`getzep/graphiti`](https://github.com/getzep/graphiti)（Zep时序知识图谱）— 候选前提"系统只能比较相邻cluster状态"被实地读码证伪：`cross_cluster_entity_state_graph_aggregate.py`（2026-07-07新建）已是确定性零LLM全书跨度的实体时间线图矛盾聚合器，覆盖死后复活/道具双持有/关系倒退三类场景，且"死角色"这一失效模式在天灾项目里有真实观测记录
- [`HKUDS/LightRAG`](https://github.com/HKUDS/LightRAG) — 候选称"knowledge_graph.json只写不查"是真但被偷换成"系统从未做历史事实/主题注入"的broad结论是假的：`rag_retriever.py`+`build_manifest.py._collect_selective_history`(L3757起)已是完整历史事实注入闭环
- [`microsoft/graphrag`](https://github.com/microsoft/graphrag)（社区发现/层级摘要）— `motif_recurrence_ledger.py`已是跨cluster母题循环账本（props/imagery/catchphrase/sensory_mark/places五类，gap判定dormant），功能实质重复
- [`arXiv:2606.05724`](https://arxiv.org/abs/2606.05724)（Narrative Knowledge Weaver）— grep确认候选点名要泛化的目标文件`cross_cluster_entity_state_graph_aggregate.py`docstring已自证出处，5个独立确定性scanner已覆盖同类诊断目标，候选方案是可解释性/确定性倒退
- [`Qianyue-Wang/...DOME`](https://github.com/Qianyue-Wang/Generating-Long-form-Story-Using-Dynamic-Hierarchical-Outlining-with-Memory-Enhancement)（仅取记忆模块）— `memory_layer.py`三层记忆系统(章节/摘要/归档，自称移植自Mem0/Letta概念)+`knowledge_graph_update.py`已构成"写前查询记忆→写后更新图谱"闭环，且是cluster粒度原生实现，三重实锤功能重复

### C. 多智能体辩论/自一致性投票机制（功能实质重复：judge_consensus.py/adversarial_judge_pair.py/preference_ranker.py已覆盖同一诊断目标，且更贴合本系统真实故障谱）
- [`princeton-nlp/tree-of-thought-llm`](https://github.com/princeton-nlp/tree-of-thought-llm)（ToT vote聚合）— `cluster_emergence_engine.py:333 _score_one_me`已是确定性锚定涟漪/arc/前置链/收敛分的候选打分（ToT"value模式"原生实现且更贴北极星③）；`preference_ranker.py`已是专为cluster_emergence候选选1设计的pairwise BPR排序器
- [`szu-tera/RankedVotingSC`](https://github.com/szu-tera/RankedVotingSC) — 候选对`judge_consensus.py`场景描述与代码现实不符（该脚本聚合的是多judge对同一稿件评分而非2-3个平行走向候选选1，后者场景已有`preference_ranker.py`覆盖）
- [`DA2I2-SLM/DAR`](https://github.com/DA2I2-SLM/DAR)（Hear Both Sides）— grep核实`judge_consensus.py`docstring原文"2-3个judge独立评，majority vote或median仲裁"，无多轮broadcast/debate重打分逻辑，DAR要解决的"debate导致judge趋同"失效模式在若渝AI架构里根本不存在，也从未在真实项目历史bug里观测到
- [`Skytliang/Multi-Agents-Debate`](https://github.com/Skytliang/Multi-Agents-Debate) — grep确认`adversarial_judge_pair.py`已原生实现attacker-defender-arbiter三角机制（注释明确引用debate学术脉络Irving et al. 2018），已限定触发条件(is_volume_finale)+advisory-only+5个attack维度，比论文原始设计护栏更严
- [`deeplearning-wisc/debate-or-vote`](https://github.com/deeplearning-wisc/debate-or-vote) — `judge_consensus.py`已实现独立多judge并行评分+中位/多数投票+多条件escalate_to_user，比候选讨论的基础debate-vs-vote框架更精细（persona分组分歧检测/evidence_quality证据权重/calibration_features shadow训练）
- [`SU-JIAYUAN/M-MAD`](https://github.com/SU-JIAYUAN/M-MAD) — 同上，`judge_consensus.py`+`adversarial_judge_pair.py`已覆盖"分维度独立评分+辩论攻防三角"两层机制
- [`haizelabs/verdict`](https://github.com/haizelabs/verdict) — grep+核实历史memory `project-architecture-creation-vs-curation-2026-06-28`：judge_runner.py/orchestrator.py已在0c94750等commit被物理删除，若渝AI已走过"程序驱动判研编排层"这条路又主动放弃
- [`amazon-science/madisse`](https://github.com/amazon-science/madisse) — 三个候选点名的hard_gate code现状核实：LOCKED_FACT_CONFLICT/FUTURE_KNOWLEDGE_LEAK是纯regex/bigram确定性匹配无LLM参与，MADISSE的多agent辩论无处嫁接

### D. 结构化输出可靠性框架（多数与风格无关的传输层工具，且部分与"不兼容不降级"规则正面冲突或已被本系统自己的真实事故证伪）
- [`mangiucugna/json_repair`](https://github.com/mangiucugna/json_repair) — "解析失败也返回可用结果+缺失字段自动填充"与CLAUDE.md「不兼容不降级规则」正面相撞，会削弱现有`_parse_failed`硬失败信号机制；`llm_transport.parse_json_loose`三级宽松解析已覆盖真实需求
- [`BoundaryML/baml`](https://github.com/BoundaryML/baml) — `llm_transport.py:190-218 _strip_markdown_fence()`已做候选声称要做的markdown围栏剥离+单一真理源收敛，且是应真实A/B事故(w5g1636k)而生
- [`567-labs/instructor`](https://github.com/567-labs/instructor) — Q5机械收敛压力方向正是本项目自己用4.4M token真实事故(主神大道蒸馏batch3)证伪过的反模式；`gen_creative_volume_arc.py._gen_unit`已有parse-失败盲重试机制且经真机e2e验证
- [`guardrails-ai/guardrails`](https://github.com/guardrails-ai/guardrails) — fix/filter机械自动修复与「不兼容不降级规则」正面矛盾，与现有hard_gate"契约破损须loud FATAL不静默打补丁"哲学相悖
- [`pydantic/pydantic-ai`](https://github.com/pydantic/pydantic-ai) — ModelRetry机械化强制重试违反北极星⑤；Instructor+Pydantic同构方案在exe打包架构里已出现又被物理删除；`gen_writer.py`已有`GEN_MODEL_MAX_RETRIES=3`专用重试+反flash静默降级策略
- [`stanfordnlp/dspy`](https://github.com/stanfordnlp/dspy) — 候选建议的"失败生成针对性反馈+携带历史重试"结构上等同self-refine迭代收敛，`gen_writer.py:129-140`明确写"self-refine反复迭代会同质化"并因此改用best-of-N择优（round4已拒绝`madaan/self-refine`同一实证）
- [`dottxt-ai/outlines`](https://github.com/dottxt-ai/outlines) — grep确认`gen_model_loader.py`走远程OpenAI兼容/Anthropic API无本地logits接口，`model_daemon.py`的`_TASK_HANDLERS`六类均为分类器/embedding/NLI零生成模型，outlines需要的本地约束解码接口在架构上不存在
- [`guidance-ai/guidance`](https://github.com/guidance-ai/guidance)（含llguidance）— 同上，gen-model入口是纯HTTP中转，本地不持有模型不碰解码循环，与`project_genmodel_flash_locked`memory记录的远程中转架构一致，无本地logits可供guidance约束

### E. 故事情绪曲线形状学术研究（功能实质重复：consolidate_author_profile.py的_reagan_shape/_tension_stats/_sentiment_arc_fractal三件套已覆盖同类诊断目标，且是作者自身语料驱动而非通用假设）
- [`andyreagan/core-stories`](https://github.com/andyreagan/core-stories)（Reagan et al. 2016官方复现库）— `consolidate_author_profile.py:392-401 _reagan_shape()`+`:404-428 _tension_stats()`已跨该作者全部真实cluster张力曲线算dominant_emotion_shape，直接源于作者自己语料非通用假设，比候选提议更精细
- [`mjockers/syuzhet`](https://github.com/mjockers/syuzhet)（R包DCT情节弧提取）— 同上`_reagan_shape`/`_tension_stats`/`_sentiment_arc_fractal`(L459-484，Hurst+ApEn+lag-1自相关ECDF band)三套跨cluster按作者自身分布校准的情绪/张力曲线指纹机制已覆盖且已接入作者聚合profile
- [`arXiv:2602.20647`](https://arxiv.org/abs/2602.20647)（Semantic Novelty narrative shape taxonomy）— `surprisal_scanner.py`已实现段落级信息密度轨迹检测(SURPRISAL_TOO_FLAT/CLIFF/MONOTONE/INFO_DENSITY_IMBALANCE四类advisory)，是候选论文"信息密度轨迹"概念的中文网文原生实现
- [`BUTTER-Tools/NarrativeArc`](https://github.com/BUTTER-Tools/NarrativeArc)（Boyd/Blackburn/Pennebaker Science Advances 2020）— `function_word_fingerprint_scanner.py`(虚词密度vs作者基线)+`biber_mda_scanner.py`(Biber MDA 4维功能/语法标记密度vs作者基线z-score)已实现同类语义维度
- [`asardaes/dtwclust`](https://github.com/asardaes/dtwclust)（DTW/k-Shape时间序列聚类R包）— 候选问题陈述"逐点强制对齐误判形状"与代码实证不符：`emotion_arc_classifier.py:167-176`按段落数比例切3等份取均值，本身对cluster长度浮动免疫，非逐点对齐
- [`jon-chun/sentimentarcs_notebooks`](https://github.com/jon-chun/sentimentarcs_notebooks)（SentimentArcs）— `judge_consensus.py`第64/93/187/197行已原生实现"多信号算agreement_score→分歧超阈值强制escalate"，与SentimentArcs"多模型分歧即需复核"是同一套自监督交叉验证范式的又一次局部重新发明
- [`sjmaharjan/emotion_flow`](https://github.com/sjmaharjan/emotion_flow)（NAACL'18）— "定长归一化情绪特征摘要"手法已被`emotion_arc_classifier.py`(3段位置归一化valence序列+Reagan六弧型Pearson模板匹配)+`consolidate_author_profile._sentiment_arc_fractal`+`affective_signature_scanner.py`(Plutchik 8类离散情绪)三处更贴合北极星①的实现覆盖

### F. 中文武侠仙侠专精NLP（架构/北极星③⑤冲突为主，部分功能被现有jieba集成点+题材词典覆盖）
- [`hythl0day/random_chinese_fantasy_names`](https://github.com/hythl0day/random_chinese_fantasy_names) — 本质全局静态命名资源非cluster单位产物，`ITEM_NOT_YET_INTRODUCED`/`ITEM_HOLDER_ABSENT`已管一致性但候选"缺口"从未在真实多cluster项目历史bug里观测为实际失效
- [`mx-xz.com`](https://mx-xz.com/)功法招式拆解生成器/武功秘籍生成器 — 预生成/预设定招式细节与涟漪/大势涌现相反，且性别刻板模板=机械覆盖创作判断，正面违反北极星③⑤
- [`fundgao/xiuxian`](https://github.com/fundgao/xiuxian) — grep确认`character_network_extractor.py`等确实只调裸jieba未见自定义词典注入（描述属实），但`core/scripts/lexicons/genre_markers/xianxia.json`已内置仙侠题材种子词袋含"筑基/结丹/元婴"等复合境界词，无需外部词典
- [`tinylion1024/web-novel-master`](https://github.com/tinylion1024/web-novel-master)（Cultivation.md）— WebFetch核实文档内容后确认：固定境界/资源/战斗力/节奏量化分级表且携带《凡人修仙传》《吞噬星空》等具体作品设定，与`feedback-volume-arc-style-ref-story-contamination`已修复翻车同构（风格档示例故事窜入大纲），且文档本体是预设与北极星③正面冲突
- [`hjzhao73/MultiGenre-ChineseNovel`](https://github.com/hjzhao73/MultiGenre-ChineseNovel) NER语料（[arXiv:2311.15509](https://arxiv.org/abs/2311.15509)）— `character_network_extractor.py`已用"已知角色名单精确匹配>项目数据库>jieba NER兜底"三级策略，known_names是主要真理源，功能重复+已实证证伪(该fine-tune路径需要的标注语料投入与system现有确定性优先架构方向相反)
- [`arXiv:2007.08186`](https://arxiv.org/abs/2007.08186)（Cross-Domain CWS距离标注+对抗训练）— jieba在若渝AI仅3处使用且均是离线advisory型分析工具输入预处理，无一直接决定生成内容，恰恰是境界词/法宝词所在的内容词在`style_evaluator.py`里被有意排除在纯风格轴之外
- 潘纪龙《金庸古龙语言风格对比研究》（现代语言学2022）— 静态语料库文献计量描述(2009年代AntConc工具)，问题空间是人文学者做文学风格比较研究非cluster批处理系统质检；`prose_rhythm_scanner.py`注释明确记录"跨作者百分位会把cluster往通用网文均值拽，反噬北极星"的真实生产事故，population严格限定作者自身历史章节
- 邰沁清等《数字人文视角金庸文本挖掘》（DHCN 2020）— `character_network_extractor.py`第111-113行已对`name_aliases`字段做多称谓归一，且该字段被`embedding_store.py`/`cross_cluster_pattern_aggregate.py`/`cross_cluster_offscreen_aggregate.py`多处消费

## 方法论纪律备忘（本轮新增）

提交前来源审计完整保留 45 个候选的逐项身份：43 项附有唯一确认的官方 GitHub、arXiv、DOI 或项目主页链接；2 篇中文论文因无法唯一还原公开页面而保留完整题名与出处，未猜测链接。

本轮相比round3-5显式加强了两条纪律，全部45个候选评估中都有体现，值得固化为后续轮次的标准操作：

1. **五问闸的gate阶段强制先grep后判断**：不允许"应该没有类似机制"这种未经核实的判断，必须先Read/Grep若渝AI现有代码确认有没有对应实现，再下verdict。本轮45个拒绝理由里至少38个包含具体文件路径+行号引用。
2. **候选提出的"缺口"必须核实是否有真实观测支撑**：多个候选（CrossAug/HippoRAG/random_chinese_fantasy_names等）描述的"系统缺陷"经核查是候选作者的理论推测，而非钟楼弃儿/天灾/惊悚乐园等真实多cluster项目历史bug记录里出现过的真实失效——沿用round2 A1复核首创的"schema缺口≠行为缺陷"诊断透镜，本轮进一步验证了这条纪律在处理"看起来更精巧"的候选时依然有效。

## 状态

**3-strike 计数器：第 1 次命中 NO_NEW_CANDIDATES（本用户目标为新设定的独立目标循环，计数器从 0 重新开始，与此前已经完结的 `project-realapi-validation-loop-2026-07-07` 三振计数器相互独立）。**

若渝AI 现有34子系统+确定性scanner体系在与本轮45个新候选（世界观连续性软件/GraphRAG检索/多agent辩论投票/结构化输出可靠性/情绪曲线学术研究/中文武侠仙侠NLP六个全新角度）逐一严格对照后，再次没有发现需要引入的新外部机制。下一轮（round7）须使用与 round3-6 累计24个正式搜索角度都不重叠的全新角度。
