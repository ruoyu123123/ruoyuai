# 开源写作系统挖掘 Round 7（2026-07-10）

> 用户新目标循环第 2 次 GitHub 挖掘轮。本轮经故障候选集合补核后共有 **74 个去重候选**：73 个在北极星五问闸失败，`MemAlign` 作为唯一边界项进入独立怀疑者复核后被 `REFUTE`。最终 **0/74 值得集成**，判定 NO_NEW_CANDIDATES（3-strike 计数器第 2 次命中）。

## 方法与运行事故说明

Workflow 6 全新角度（与 round3-6 正式三振检索累计用过的 24 个角度均不重叠；实体去重覆盖 round1-6 全历史）：读者反馈信号挖掘（评论/弹幕/追读率转结构化特征）、LLM-as-judge 校准/去偏见研究、游戏学术界程序化内容生成(PCG)约束求解式生成、diff/patch增量编辑工具、中文网文平台推荐算法学术论文、长度感知/事后长度预测（非生成时长度控制）。

**本轮第一次运行（task `w1qd1j9cx` / workflow `wf_a89719df-1bc`）撞上基础设施故障**：6 个 search agent 成功发现 46 个候选，但 46 个 gate agent 全部报 `403 account is banned`，所以这 46 项的 gate 结果均为空，不能算真实评估。

服务恢复后用同一脚本重跑（task `w6bpm4ocn` / workflow `wf_cb9dac3d-2cf`），50/50 agent 正常完成；但 search agent 是随机检索，干净重跑实际得到的是**另一批 44 个候选**，不是“原 46 项去重成 44 项”。两批结果只有 6 个精确 URL 重叠；按论文标题、arXiv、repo 别名归一后，干净重跑覆盖故障批 14 项，另有 `WebNovelBench` 和 `CFPG` 已被 round2/更早移植批次覆盖，仍留下 30 个未过闸候选。

2026-07-10 独立验收按“保留成功 search 产物、合并所有尝试、全历史去重、只补下游 gate”的正确恢复方式补齐这 30 项：29 项直接 FAIL；`MLflow MemAlign` 因五问表面可适配而进入独立怀疑者，最终因无人类 judge 标签生产链、自评自学偏置、partial-memory 降级和隐藏 guideline 影响 required 阅读闸而被 `REFUTE`。因此本轮最终口径为 **74 个去重候选，0 个通过五问闸+对抗复核**。

## 干净重跑已评估候选（44个，全部 FAIL）

### A. 读者反馈信号挖掘（6个：架构前提不成立——若渝AI是无外部读者反馈的单机私有写作助手，候选依赖的真实弹幕/评论时间戳数据源在系统任何环节都不存在）
- [`nmarinsek/burst_detection`](https://github.com/nmarinsek/burst_detection)（Kleinberg突发检测算法）— 候选把`cluster_burst_type_predictor.py`注释"真版用蒸馏聚类"（文本语义聚类任务）误读成"缺峰值定位"（时序频率突增检测任务），二者除了都提到"弹幕"外无技术关联；且该缺口早被更优的`zero_shot_prototype`嵌入分类器(2026-07-03上线)实质取代
- [`artkulak/twitch-stream-highlights-detection`](https://github.com/artkulak/twitch-stream-highlights-detection) — 英文游戏直播"消息速率+表情密度"病毒热度公式，与"贴近特定作者风格"方向南辕北辙；训练数据源（视频秒级时间轴）与cluster（万字级静态文本块）无可操作映射；若渝AI没有任何弹幕/评论数据采集渠道，是纯假设前提
- [`Dynamic Scene Segmentation and Sentiment Analysis for Danmaku`](https://doi.org/10.3390/app15084435)（MDPI论文）— 同样撞上"无真实弹幕数据源"的架构前提问题，`mass_reactor_scanner.py`已覆盖叙事技法层面的"众人反应段"检测（与候选的"真实观众数据"是完全不同的两件事）
- [`The Long Tail, Not the Front Page`](https://arxiv.org/abs/2606.11654)（arXiv:2606.11654 冷启动highlight预测）— `paragraph_engagement_heat_predictor.py`(2026-06-21已生产挂载)用5个Loewenstein信息缺口特征对任意新草稿打段落热度分，天生冷启动不需要历史数据，方法论骨架与候选完全一致且已实装
- [`Big data meets storytelling`](https://link.springer.com/article/10.1007/s13278-024-01224-x)（fanfiction热度预测）— `cross_cluster_reader_retention_proxy_aggregate.py`已是"完读率事后数据不存在→用hook/sagging/cliffhanger/length四个现有信号合成retention proxy"的原生实现
- [`Data-driven Methods of Extracting Text Structure`](https://arxiv.org/abs/2509.12999)（含なろう章节书签语料，arXiv:2509.12999）— `dramatic_question_lifecycle_scanner.py`(PITQ/MDQ生命周期)+`cross_cluster_reader_retention_proxy_aggregate.py`已覆盖同类问题域

### B. LLM-as-judge 校准/去偏见研究（7个：judge_consensus.py/av_judge.py/pid_threshold_tuner.py/cross_family_judge_check.py/flawed_fiction_maker.py等已构成多层次校准体系，且多篇候选论文本身已被本系统此前调研批次挖掘并部分落地过）
- [`tatsu-lab/alpaca_eval`](https://github.com/tatsu-lab/alpaca_eval)（长度控制胜率）— `parallel_rollout_arbiter.py`第93-104行`_length_band_fit`已用cluster自身分位数校准做长度中性化，候选前提"现有链路测不出长度刷分"被源码证伪
- [`EmertonData/glide`](https://github.com/EmertonData/glide)（Prediction-Powered Inference）— 若渝AI已有`sfs_calibration_probe.py`/`statistical_threshold_baseline.py`/`semantic_threshold_calibrator.py`/`pid_threshold_tuner.py`四套协同阈值校准机制
- [`BruceSheng1202/Analyzing_Uncertainty_of_LLM-as-a-Judge`](https://github.com/BruceSheng1202/Analyzing_Uncertainty_of_LLM-as-a-Judge)（EMNLP'25 Conformal Prediction）— `pid_threshold_tuner.py`已有advisory-only+物理隔离hard_gate框架，且候选目标字段`escalate_to_user`在全代码库零下游消费者
- [`Who Drifted: the System or the Judge?`](https://arxiv.org/abs/2606.15474)（arXiv:2606.15474 anytime-valid e-process）— `av_judge.py`+`learning_loop.py`已覆盖judge漂移三态归因的诉求
- [`UW-Madison-Lee-Lab/LLM-judge-reporting`](https://github.com/UW-Madison-Lee-Lab/LLM-judge-reporting) — `flawed_fiction_maker.py`+`flawed_fiction_runner.py`（本身已在历史排除清单里，2026-07-07落地）用15个受控反事实注入样本测出精确召回矩阵，比候选方法更细粒度
- [`sksoumik/llm-as-judge`](https://github.com/sksoumik/llm-as-judge)（TMLR，去偏策略评测）— **该论文arXiv:2604.23178已被若渝AI团队完整挖掘并部分落地**：R10批次(2026-06-19/20)直接产出`cross_family_judge_check.py`，文件头注释逐字引用该论文
- [`The Geometry of LLM-as-Judge`](https://arxiv.org/abs/2606.03043)（arXiv:2606.03043）— `judge_consensus.py`+`replication_fidelity_check.py`已覆盖"判官集体一致但偏离人类标准"这一真实观测到的历史bug（v28北极星⑤校准段记录的惊悚乐园句长事故）

### C. 游戏学术界PCG约束满足式生成（8个：cluster_emergence_engine.py的启发式多维打分+前置链DAG校验+preference_ranker.py的BPR学习排序器，已是"形式化候选打分但绝不淘汰"设计的更弱、更贴合北极星的原生实现；全仓确认零ASP/SAT/CSP求解器技术栈）
- [`mkremins/spleenwort`](https://github.com/mkremins/spleenwort)（Answer Set Programming引导故事生成）— `cluster_emergence_engine.py`的`select_candidate_mes()`已用7维加权规则打分，主动放弃机械解算只做只读展示
- [`sgware/sabre`](https://github.com/sgware/sabre)（Narrative Planner）— `character_belief_ledger_scanner.py`(OmniToM 7维)+`cross_character_kth_order_belief_scanner.py`(K=2嵌套信念)已覆盖候选的心智理论诉求
- [`chrisamaphone/interactive-lp`](https://github.com/chrisamaphone/interactive-lp)（Ceptre线性逻辑语言）— 候选引用的"近期真机bug"(commit 484b114)被误读，该bug是LLM自由生成把ripples写成裸字符串的数据类型问题，与线性逻辑资源建模无关
- [`Siler & Ware AIIDE 2025`](https://doi.org/10.1609/aiide.v21i1.36817)（ASP+Theory of Mind叙事规划）— `cluster_emergence_engine.py`+4个belief/motivation scanner已双重覆盖候选论文的两半机制
- [`Breault et al.`](https://doi.org/10.1016/j.entcom.2021.100422)（CONAN程序化任务生成）— `plot_structure_scanner.py`(H1-H10)+`beat_map_update.py`+`consolidate_author_profile.py`已覆盖，且候选的"通用理论模板"与作者风格档第一权威原则存在张力
- `Edirlei Soares de Lima et al.`（遗传算法分支任务生成）— `_score_one_me`+`preference_ranker.py`+`arc_aggregator.match_reagan_shape()`+`volume_arc_drift_scanner.py`四重覆盖，且遗传算法的"选择压力=淘汰机制"正面违反北极星⑤
- [`jediahkatz/you-only-randomize-once`](https://github.com/jediahkatz/you-only-randomize-once)（约束式PCG统计属性塑形，FDG 2024）— 全仓确认零CSP/SAT求解器依赖，无宿主可挂载该技巧
- [`Senanayake & Ware`](https://doi.org/10.1145/3723498.3723815)（LLM作叙事规划启发式，FDG 2025）— "LLM只做启发式打分、经典搜索做分支探索"的分工范式已实装（只是搜索算法换成确定性规则枚举+硬剪枝，启发式换成BPR学习排序器）

### D. diff/patch增量编辑工具（8个：gen_fixer.py的整篇覆写+CJK字数守恒±30%容差是刻意设计而非未实现的缺陷，patch_applier.py已是更精细的结构化JSON patch机制）
- [`Plug-and-Play Dramaturge`](https://arxiv.org/abs/2510.05188)（arXiv:2510.05188）— **同一篇论文同一组数字**：`audit_hub_hierarchical_planner.py`docstring原话逐字引用该arXiv编号+性能数字，三阶段完全对应
- [`Aider-AI/aider`](https://github.com/Aider-AI/aider)（多格式自适应编辑引擎）— 核查14份真实fixer_report_*.json后确认候选核心论据不成立
- [`OpenAI apply_patch/V4A`](https://developers.openai.com/api/docs/guides/tools-apply-patch) — 候选描述的"按行号定位会因生成长度不同失效"这个失效模式在gen_fixer.py真实实现里根本不存在（该脚本从不用行号做定位式补丁）
- [`google/diff-match-patch`](https://github.com/google/diff-match-patch) — gen_fixer.py的整篇覆写+自然衔接是刻意设计（system prompt明写"读起来像作者本人改的，不是AI补丁"），不是缺失diff机制
- [`CriticMarkup`](https://github.com/CriticMarkup/CriticMarkup-toolkit) — `patch_applier.py`已是带4级模糊anchor匹配+段落/行两级定位+IMMUTABLE关键词硬保护的更精细实现
- [`vipulraheja/coedit`](https://github.com/vipulraheja/coedit)（指令+局部片段编辑）— validator-repair/voice-fix两模式已是"违规清单当编辑指令+定位段落精准修复"的同构实现
- [`To Diff or Not to Diff?`](https://arxiv.org/abs/2604.27296)（arXiv:2604.27296）— gen_fixer.py走的是"全量context+全量输出+CJK守恒±30%"路径，且git log确认从未为此补过lesson建议的路径
- [`CLFEC`](https://arxiv.org/abs/2602.23845)（段落级中文写作语言学+事实纠错，arXiv:2602.23845）— `novel-validator-checker.md`的violations[]数组已用line_start/line_end统一收纳hard_gate和advisory两类错误

### E. 中文网文平台推荐算法学术论文（8个：cross_cluster_reader_retention_proxy_aggregate.py + preference_ranker.py + user_choice_learner.py已构成完整的"事后代理指标+隐式偏好学习"体系，且与feedback_reader_growth_compliance_redline"数据流单向不让流量反向覆盖作者风格"的红线存在方向性冲突）
- `NovelNet`（Tencent RecSys 2022）— `cross_cluster_reader_retention_proxy_aggregate.py`已覆盖"完读率是事后数据"的诚实声明+四信号合成代理
- `IURO`（Tencent RecSys 2023 Best Short Paper）— 同上，且已在`run_cross_cluster_aggregates.py`主流水线注册，非孤儿
- `RLUR`（Kuaishou WWW 2023，强化学习留存）— 全仓确认无RL/MDP/policy gradient机制，若渝AI的"大势/涟漪"是确定性规则+LLM叙事解读+人工走向卡选择的软牵引，非训练策略网络
- [`PrefRec`](https://doi.org/10.1145/3580305.3599473)（Kuaishou KDD 2023，人类偏好强化学习）— `preference_ranker.py`已是纯Python BPR pairwise排序器，专为cluster_emergence候选选1场景设计
- `SAQRec`（Kuaishou+人大 CIKM 2024，问卷反馈对齐）— `preference_ranker.py`+`user_choice_learner.py`已覆盖"隐式代理不足以代表真实满意度"问题域
- [`ReCODE`](https://doi.org/10.1145/3626772.3657936)（人大 SIGIR 2024，神经ODE重复消费建模）— `motif_recurrence_ledger.py`（跨cluster母题循环账本）已覆盖候选类比的"叙事元素重现/间隔建模"
- `KuaiSim`（Kuaishou NeurIPS 2023基准）— `cross_cluster_reader_retention_proxy_aggregate.py`+`cross_cluster_engagement_metrics_aggregate.py`已是成熟接线实现
- `Qidian-Webnovel Corpus`（多语言读者反应数据集）— `prose_rhythm_scanner.py`第26-28行明文记录的真实翻车（跨作者百分位校准会把scanner往通用网文均值拽、反噬北极星，惊悚乐园句长31离群点被误判实证案例）+ `feedback-reader-growth-compliance-redline`红线条目"数据流单向"与候选诉求方向冲突

### F. 长度感知/事后长度预测（7个：chapter_splitter.py的score_split_point()7维确定性评分+_best_anchor_split()已是成熟原生实现，且v17.8曾把splitter做成LLM agent又主动回退为纯Python确定性脚本的历史决策，与本类全部候选"给切点判断加ML/语义模型"的方向直接同构冲突）
- [`segment-any-text/wtpsplit`](https://github.com/segment-any-text/wtpsplit)（SaT语义分段）— 候选对现状描述（等距锚点+距离惩罚+末章上溢回退）与源码逐字吻合，非编造，但目标问题已有确定性7维评分覆盖
- [`Toward General Semantic Chunking`](https://arxiv.org/abs/2602.23370)（arXiv:2602.23370）— `coherence_infer.py`同架构类别的判别式分类器已存在，但只喂给`coherence_scanner.py`做事后advisory审计，未接入splitter（有意的架构边界）
- [`deepcharles/ruptures`](https://github.com/deepcharles/ruptures)（PELT变点检测）— `topic_drift_scanner.py`(embedding余弦drift)+`hook_strength_scanner.py`的A13切点前瞻熵已覆盖候选的隐含目标
- [`joaodsmarques/LumberChunker`](https://github.com/joaodsmarques/LumberChunker) — `.claude/agents/novel-chapter-splitter.md`第11行原文明确记载splitter历史上真做成过LLM agent(v17.8)后被判定"过度设计"主动回退纯Python，候选提议正是逆转这个已验证的架构决策
- [`sbu-dsl/chapter-captor`](https://github.com/sbu-dsl/chapter-captor) — `score_split_point()`的7维regex评分+4项惩罚已完整实现，docstring自述"v17.8过度设计"的设计修正史
- `Heinonen ACL 1998`（动态规划最优分段）— `_best_anchor_split()`的"距离锚点惩罚项"已是候选论文核心机制（quality score与距离惩罚放入同一目标函数），只是搜索方式是逐锚点贪心非全局DP
- [`PODTILE`](https://doi.org/10.1145/3627673.3680081)（Spotify Research，播客章节+标题联合预测）— `core/ml/LEARNABLE_BACKLOG.md`第175行**明确点名chapter_splitter**作为"反面教材确认(不该模型化)"条目，理由原文"北极星④格式层禁语义"，与候选提议完全同构

## 故障运行候选集合补闸（30个）

### G. 读者反馈信号（8个，全部 FAIL）
- [`GOLEM-lab/Qidian-Webnovel-sentiment`](https://github.com/GOLEM-lab/Qidian-Webnovel-sentiment) — 评论情感不等于作者风格；原生单位是书/章/段落标注，当前主链没有真实评论 producer。把其信号喂 SkillOpt 会让流量反向塑形，违反数据流单向红线。
- [`zhou-xingxing/graduate_work`](https://github.com/zhou-xingxing/graduate_work) — 秒级直播弹幕聚合与万字 cluster 静态正文不兼容，且无任何弹幕采集入口；不读涟漪/大势。
- [`hougesen/twitch-highlight-finder`](https://github.com/hougesen/twitch-highlight-finder) — Twitch WebSocket、表情和消息频率依赖持续直播基础设施，既不贴作者风格，也没有 cluster 宿主数据。
- [`zhangchaodesign/friction`](https://github.com/zhangchaodesign/friction) — HCI 反思界面尊重人类决定，但输入是句/段级外部反馈；若渝没有该反馈 producer，且已有结构化 validator brief + `gen_fixer` 定位修复闭环。
- [`SEthanMilne/FanficReadeR`](https://github.com/SEthanMilne/FanficReadeR) — AO3 work/chapter 爬虫把平台 hits/kudos/comments 当核心信号，非 cluster、非涟漪；章号还会变成反馈主键。
- [`sebp/scikit-survival`](https://github.com/sebp/scikit-survival) + [MOOC time-to-event](https://arxiv.org/abs/1407.7143) — 是通用生存分析库与教育流失论文的拼接，不是小说现成实现；若用于 SkillOpt reward 会优化留存而非作者风格。
- [`SocratesClub/Review-Helpfulness-Prediction`](https://github.com/SocratesClub/Review-Helpfulness-Prediction) — 手机商品评论 helpfulness 特征与小说作者风格、cluster 和世界因果均无关，也无真实评论入口。
- [`An Analysis of Reader Engagement in Literary Fiction`](https://arxiv.org/abs/2306.04043) — 23 名读者、2 篇短篇的句级眼动实验不能迁移成 cluster required 信号；用跨读者通用反应校准作者 scanner 会覆盖作者自身基线。

### H. Judge 校准（7个：6 FAIL + 1 对抗复核 REFUTE）
- [`MLflow MemAlign`](https://mlflow.org/blog/memalign)（[v3.9.0 转换源码](https://github.com/mlflow/mlflow/blob/v3.9.0/mlflow/genai/judges/optimizers/dspy_utils.py#L361-L452)）— **初筛边界项 -> 独立怀疑者 `REFUTE`**。官方实现硬要求与 judge 同名、`source_type=HUMAN` 且带 `feedback/value` 的 assessment；自然语言 rationale 强烈建议但不是硬要求。若渝现有 feedback、走向选择、writer self-eval 和 judge reports 均不形成这种人类评估记录。拿 judge 自评分充 HUMAN 会形成偏置自证；同时 semantic guideline 无优先级全量注入、trace 缺失时 partial memory 继续运行，接到 Reading-Reflector 的 required clean gate 会把隐藏历史偏好变成返修/硬停标准，违反北极星⑤和不降级纪律。
- [`RAND Judge Reliability Harness`](https://github.com/RANDCorporation/judge-reliability-harness) — 是跨 dataset 的离线可靠性 validation suite，不是 cluster 主链能力；其反事实、重采样、position swap、cross-family 子集已有原生覆盖。
- [`EQ-bench/longform-writing-bench`](https://github.com/EQ-bench/longform-writing-bench) — 固定 13 steps 和 8 个 1000-word chapters，按章生成/判分，直接违反 cluster 与章节纯格式边界；通用创意 rubric 还会重设作者标尺。
- [`SprocketLab/CARE`](https://github.com/SprocketLab/CARE) — 需跨大量 items x judges 拟合 confounder，公开 pipeline 仍用 human-eval validation 调参；不能在首个 cluster required 闭环成立，替换 consensus 还会改变放行标准。
- [`bias-bounded-evaluation`](https://github.com/penfever/bias-bounded-evaluation) — 对分数 shrink 后加高斯噪声只让偏差更难辨识，不证明 judge 更正确；dataset 级随机扰动不应进入创作裁决。
- [`Diagnosing the Reliability of LLM-as-a-Judge via IRT`](https://arxiv.org/abs/2602.00521) — 依赖跨 prompt variation 的 pooled GRM 与 human alignment，仅适合离线诊断；不直接改善作者风格或单 cluster 结果。
- [`MetricMatch`](https://arxiv.org/abs/2606.15029) — 价值是降低人工可靠性估计成本，仍需跨 dataset 选子集交给昂贵标注；放入主链会外部阻塞，不阻塞又违反 required/no-fallback。

### I. PCG / 约束求解（4个，全部 FAIL）
- [`potassco/clingo`](https://github.com/potassco/clingo) — 必须预编码 facts/actions/goals/constraints，再只返回满足约束的 answer sets；会把涟漪软涌现和用户走向选择改成硬可行性裁决。
- [`icaros-usc/pyribs`](https://github.com/icaros-usc/pyribs) — MAP-Elites/GridArchive 每格保留 objective 更高 elite，机械淘汰与顾问制相冲突；现有 `preference_ranker` 已只做 advisory 排序证据。
- [`RoleModel/RoleModelVis`](https://cdn.aaai.org/ojs/12492/12492-52-16019-1-2-20201228.pdf) — 用 abductive logic + event calculus + Clingo 预设角色、作者目标和 vignette；只有论文/Java 可视化原型，无公共代码或许可证。
- [`TropeTwist`](https://arxiv.org/abs/2204.09672) — 以手工 trope root graph、graph grammar 和 constrained MAP-Elites 生成固定叙事图，既污染作者风格又是预设图搜索；论文自称 preliminary 且无公共实现。

### J. diff / edit（3个，全部 FAIL）
- [`Cascaded Code Editing`](https://arxiv.org/abs/2604.19201) — 大模型产 edit sketch、小模型应用到整文件，核心价值是省 token；迁移小说会让第二模型重写正文并增加风格漂移，与“不抠 token”和 `gen_fixer` 整稿自然修订相冲突。
- [`XtraGPT`](https://arxiv.org/abs/2505.11336) — 面向英文论文 section/paragraph 的规范性重写，本地 Qwen SFT 与孤立 router 还带 `allow_fallback: true`，不符合 Gemini 单链、cluster 和不降级规则。
- [`RewriteLM`](https://arxiv.org/abs/2305.15685) — 英文通用 cross-sentence rewrite 的 SFT+reward+RL，不是指定作者风格、cluster 或涟漪；公开子目录也没有完整模型实现/权重。

### K. 平台/参与度模型（4个，全部 FAIL）
- [`Modeling Story Expectations to Understand Engagement`](https://arxiv.org/abs/2412.15239) — 以匿名大型在线媒体平台的章级继续阅读、评论和投票为真值，调用 GPT 生成多条 imagined/potential continuations，再提取期望、不确定性和惊讶特征；方法仍以章节参与度解释为中心，不读世界状态/涟漪/ME，会把章节和流量指标升为核心创作单位。
- [`The Secret to Popular Chinese Web Novels`](https://drops.dagstuhl.de/entities/document/10.4230/OASIcs.LDK.2019.24) — 跨作者热门网文统计会把单作者指纹拉向平台均值；`prose_rhythm_scanner` 已有惊悚乐园句长被通用均值误伤的真实反例。
- [`bytedance/HLLM`](https://github.com/bytedance/HLLM) — 仓库同时包含推荐模型 HLLM 与 [`HLLM-Creator`](https://github.com/bytedance/HLLM/blob/main/HLLM_CREATOR_README.md)；后者用 User/Item/Creative LLM、用户聚类和 user-ad matching pruning 为抖音搜索广告生成个性化标题。这里的 cluster 是用户群而非小说 cluster，既不学习作者风格，也不读取涟漪/世界状态，且以权重训练和匹配剪枝直接塑形内容。
- [`D2Q`](https://arxiv.org/abs/2206.06003) — 快手 watch-time 去偏依赖真实视频曝光/观看事件，当前无同类数据；映射到小说会把章节停留时长变成核心信号。

### L. 语义分段 / 排版（4个，全部 FAIL）
- [`chschock/textsplit`](https://github.com/chschock/textsplit) — 文档主题分段以 embedding/DP coherence 重新定义边界，违反 splitter 只做确定性格式切割的北极星④。
- [`usrlocalben/justified`](https://github.com/usrlocalben/justified) — 只是简化 Knuth-Plass 行断行与两端对齐，连章节/cluster 切分都不是，主链无增量。
- [`NLTK TextTiling`](https://www.nltk.org/api/nltk.tokenize.texttiling.html) — 按 lexical co-occurrence 和 subtopic shift 划 section；中文参数另需校准，且语义模型不得接管 chapter splitter。
- [`literarylab/scene_segmentation`](https://github.com/literarylab/scene_segmentation) — 英文 20 世纪美国 romance、BERT/USE 六句窗口，README 明称 work in progress、语料不可公开；无中文网文迁移证据且违反格式层边界。

## 方法论纪律执行情况

本轮文档保留 74 个候选的可审计身份：66 项附有唯一确认的官方 GitHub、arXiv、DOI 或出版社链接；另 8 项因简称歧义或原始 workflow 产物未在仓库持久化而保留纯文本，未猜测链接。所有 gate 都要求先联网核原始材料，再 Read/Grep 当前仓库。补闸阶段还独立解析了两次 workflow 持久化结果，而不是相信“重跑成功”汇总。延续 round6 的两条纪律：
1. **五问闸gate阶段强制先grep后判断**：无一候选是"应该没有"式的凭空判断。
2. **候选自称的"缺口"核实是否有真实观测支撑**：多个候选（Kleinberg突发检测/twitch highlight/多篇RecSys论文）的核心论据被源码或架构前提直接证伪——若渝AI没有对应的真实数据采集渠道，候选描述的失效场景是从未发生的假设，不满足"真实观测到的行为缺陷"门槛。

本轮新增一个值得记录的模式：多个候选（`Plug-and-Play Dramaturge`/`sksoumik/llm-as-judge`）不是"功能碰巧重复"，而是**同一篇论文/同一个arXiv编号已经被若渝AI此前的调研批次（R10批次2026-06-19/20、S5移植2026-07-07）挖掘并部分落地过**——说明本系统的历史调研覆盖面已经相当扎实，round7撞见的许多"新候选"实际是旧发现的重新包装再发现。

本轮新增的防复发纪律：**下游 gate 因基础设施失败时，必须保留成功 search 的精确候选集合并从该集合续跑；若不得不重新搜索，最终评估集合必须取所有尝试的并集再做实体去重。随机重搜不能替代原候选集合。**

## 状态

**3-strike 计数器：第 2 次命中 NO_NEW_CANDIDATES（本用户目标循环独立计数，与 round6 共享同一计数序列）。补核后结论不变：74 个去重候选仍无一通过五问闸+对抗复核。**

下一轮（round8）须使用与 round3-7 累计30个正式搜索角度都不重叠的全新角度。再命中1次即达成"连续三轮无新候选"终止条件。
