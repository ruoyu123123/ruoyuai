# 开源写作系统调研 · 第二轮（本地深挖 + 联网增量）

Date: 2026-07-07
方法：4 路并行——①PlotPilot 实现级深挖 ②moyin/AI_NovelGenerator/LongWriter/Ex3 剩余机制 ③联网新系统/论文 ④联网检测/评估/工艺。全部条目带代码 `文件:行号` 或已抓取 URL 证据。首轮（open_source_writing_systems.md）已移植项不再收录。
边界不变：任何采纳只能落 `/write -> /outline -> /cluster-write -> /cluster-save-state -> 走向卡 -> /export` 的 required step/子步骤或既有 scanner/engine/agent 增强，禁旁路。

## ⚠️ PlotPilot 活代码警示（引用其行号前必读）

PlotPilot 自身废码双轨遍地（daemon 三代/checkpoint 三套/多处名实不符）：`autopilot_daemon.py`+`engine/application/*` 是废弃兼容层（真实现在 `engine/runtime/`）；`error_classifier.py` 未接运行时；`BeatCardPromptRenderer` 生产调用仅测试命中；triples 向量索引惰性建、未建时静默返回空；`StreamACScanner` 实为正则回退；voice drift 的 cosine 非真余弦。移植以下文标注行号的活代码为准。

## S 级候选（高价值 · 正对已知痛点 · 成本低中）

| # | 机制 | 来源/证据 | 若渝缺口 | 落点 |
|---|---|---|---|---|
| S1 | **manifest 分层 token 预算**：T0 强制约束≤20%（「过多强制内容→注意力坍塌，AI 从写故事变成满足约束」）·T0 硬上限 40%·T3 长程记忆最低保障 5%·超预算截断留压缩日志 | PlotPilot `context_budget_allocator.py:112-118,296-357`；`context_budget_policy.py:32-77` | build_manifest 是撑爆才裁的粗粒度（67k→29k 车），无 tier/priority 预算、无约束占比上限、无可审计压缩日志 | build_manifest 各注入段声明 (tier,priority,max_tokens)，压缩决策写 manifest 元数据 |
| S2 | **禁令→正向行为协议**（生成端）：「否定指令在 Self-Attention 中激活被禁 token」·9 类转换含情绪降级档（大哭→沉默/暴怒→说话变慢变清楚）·每条 1 正 1 反示例 | PlotPilot `positive_framing_rules.py:1-39,113-116`；`rule_parser.py:43-83` | 反 AI 腔守卫是负向禁用词清单直注 writer prompt | gen_writer 通用兜底段改正向协议（作者档仍第一权威）；负向词表保留给 scanner——检测负向/生成正向各取所长 |
| S3 | **LLM 禁直接闭合终态类级契约**：UPDATE_DEBT_PROGRESS 携 status∈{resolved,abandoned} → ValueError("LLM cannot directly close narrative debts") | PlotPilot `domain/evolution/reducer.py:97-108` | 伏笔批量误标 resolved 翻过车（个案已修），缺类级契约 | apply_archive reducer：终态转移必须由带 payoff 证据（open 项引用+正文 span）的专门动作触发，抽取动作只许 progress 级 |
| S4 | **高熵段=一致性错误定位器**：一致性错误集中在叙事中段+token 熵高文本段，特定错误类型共现 | ConStory-Checker arXiv:2603.05890 | nn_surprisal 与一致性栈互不通气 | surprisal 输出当一致性深查（NLI/judge）的扫描优先级权重——两套既有能力零新模型连线 |
| S5 | **合成 plot hole 反向校准（测漏报）**：三幕分解→抽命题→反事实改写→拼接；前沿模型 1200-4000 词检测近随机 | FlawedFictions arXiv:2504.11900 | 金标准只测误报（真作者原文），漏报率从未量化——一致性是 19 hard_gate 命根 | 校准脚本体系镜像补全：真 cluster 注入受控反事实→测三层一致性栈各自召回→得检测能力地图 |
| S6 | **WritingPreferenceBench 600 中文创意写作偏好对**（语法/事实/长度配平·专业标注）+ 结论：短结构化推理 GenRM 81.8% vs 打分头 52.7% | arXiv:2510.14616（HF 数据集公开） | BPR 排序器无外部校准集 | BPR 外部校准/回归集；AV-judge 保持生成式带短理由输出 |
| S7 | **judge 换序双跑一致性协议**：长文本配对评判位置偏差严重（GPT-4o-mini 换序不一致率 78.7%·创意写作平均准确率仅 0.578） | LongJudgeBench arXiv:2606.01629 | AV-judge 配对评长 cluster（10-25k CJK 正在暴露区间），是否已换序双跑未验证 | AV-judge 校准协议必补项：换序双跑不一致即弃票（防假信号污染数据飞轮） |
| S8 | **DDPO deviation 双嵌入多样性分**：候选间语义/风格分开算 pairwise 距离 | arXiv:2503.17126（Midjourney+NYU） | best-of-N 无「候选是否已坍缩同模式」检测 | BPR 选优加 deviation advisory 特征——与 W6 风格(ruoyu_style)/内容(bge)双轨完全同构，零新模型 |
| S9 | **非对称长度得分**：偏短罚 2 倍斜率/超长罚 3 倍斜率的连续 0-100 分 | LongWriter `evaluation/eval_length.py:5-9` | 长度带只能拒绝不能排序；gemini 偏短顽疾 | best-of-N 排序特征 + 飞轮 reward 特征（一行公式） |
| S10 | **递归卷级层级摘要+source 回溯**：摘要金字塔（>1600 字再聚一层·每层记 source 下层索引） | Ex3 `Extracting/get_summary.py:11-25,189-256` | 故事块摘要单层平铺；远卷历史靠 manifest_compress 截断（丢信息 vs 换粒度） | summarizer 卷边界触发聚合 volume_summaries[]（带 source）；build_manifest 当前卷用 cluster 摘要/历史卷用卷摘要 |
| S11 | **scene 级大势对齐三问+自评**：每镜头必答「如何推动核心冲突(铺垫→升级→高潮→转折→尾声)/是否违世界观/purpose 必须体现与故事核心关系」+ alignment 三值自评 | moyin `shot-calibration-stages.ts:188-202` | scene 只有 goal，无 conflict_stage/alignment 自评——北极星③在 scene 粒度的空白 | outline+涌现 brief 的 planner 合约三问；volume_arc_drift 消费 needs-review 汇总 |

## A 级候选（价值中高 · 按需排期）

| # | 机制 | 来源 | 落点 |
|---|---|---|---|
| A1 | **三元组混合检索双轨**：SPO 图（confidence 分级+首现章+provenance）+四路混合召回（实体一度关系 SQL/触发词→谓词映射/语义只做召回正文回结构库/近窗）+内联溯源格式化 | PlotPilot `triple.py:21-45` `context_budget_allocator.py:1388-1677` | save-state 落三元组账本子系统（required·不留惰性旁路）+ build_manifest hybrid 注入；给 ITEM_HOLDER/locked_fact scanner 提供确定性证据源。工作量中-大 |
| A2 | **写前 Evolution Gate+声明式豁免**：ending_state×下一章大纲校验（死人复活 blocking/重复事件/POV 泄露）·[TimeSkip][AmbiguousFate] 写在大纲即 bypass·gate 报告注入生成 context | PlotPilot `gate_service.py:47-139` | 一致性栈全是写后——穿帮从「写完 20k 字再修」提前到「写前拦」；brief 增声明豁免字段 |
| A3 | **前块结尾偏重注入+开头回响契约**：N-1 章末 2000 字完整保留·「前章悬念钩子/情感余韵/场景状态在本章前三句必须有回响」 | PlotPilot `recent_chapter_context.py:7-73` + 模板 | build_manifest 注入上一 cluster 草稿末尾原文段 + writer 回响指令（advisory）——跨 cluster 断裂感真实痛点 |
| A4 | **编辑手记（结构槽坍缩为自然语言）**：200-400 字手记替代 8 个结构化 T0 槽·软措辞「如果合适可以推进，不必强求」 | PlotPilot `context_budget_allocator.py:475-489` | build_manifest 双视图：伤疤/债务/前情合成散文手记给 writer，结构块保留给 scanner |
| A5 | **「被忽略未解决冲突」推进性审核**：未解决冲突清单进审核输入，要求点名被冷落该推进的线 | AI_NG `consistency_checker.py:7-25` | reading-reflector/validator 注入 subplot_threads+未消费 ME，输出 neglected_threads advisory——断线盲区 |
| A6 | **检索三段式**：结构化 query 扩展（实体×属性组合）→时间距离防复读（≤2 章禁引/3-5 章需改写 40%/>5 章可引）→用途标注重组（情节燃料/人物维度/世界碎片/叙事技法） | AI_NG `prompt_definitions.py:61-158` `chapter.py:176-216` | build_manifest 检索子步升级——MMR 治结果间冗余但不治近块复读 |
| A7 | **辨识锚点分层+角色负面事实清单**：辨识标记层必填≥2-3 条精确到位置（最强锚点）+ 角色绝不应出现特征的反向清单 | moyin `script.ts:43-81` `character-calibrator.ts:1033-1068` | 人物卡 schema 增 recognition_anchors[]/negative_facts[]（「不会武功/不识字」类反向锚防跨块漂移） |
| A8 | **StoryScope 结构层 AI tell**：304 叙事结构特征纯结构 F1=93.2%——AI 倾向主题明示/单线干净情节/道德单义；人类倾向道德模糊抉择+时间复杂度 | arXiv:2604.03136（UMD+DeepMind） | top-30 特征适配子集进 reading-reflector rubric + outline 软提示；须先跑作者金标准基线（网文常主题直给） |
| A9 | **盲审 N 修 N 选 1**：同质化根因是「修订者看到收敛信号」——critique 具体化+修订隔离即保多样性（词汇多样性/语义新颖性双最佳） | LLM Review arXiv:2601.08003 | best-of-N 升级：N 候选各接各的 judge brief 隔离修一轮→BPR 选优（全走既有 gen_fixer+BPR） |
| A10 | **Atlas 图矛盾检测 + Magnet 目标停滞检测**：故事解析成场景节点+实体关系图→图上找跨场景状态矛盾（precision/recall 超纯 LLM）；15 步无进展自动换目标 | arXiv:2607.00918（2026-07） | 图矛盾=确定性 scanner 与 LLM judge 之间的结构化中间层→cross-cluster 审计 advisory；停滞检测→emergence「卷核心任务 N cluster 无推进」advisory 信号 |
| A11 | **DeepLore 多维门控级联检索**：keyword 粗召回→层级预聚类→小模型摘要筛→按时代/地点/场景/角色门控激活 | github.com/pixelnull/sillytavern-DeepLore | build_manifest 子系统注入按「当前 scene 地点/在场角色/时间线」维度门控（34 子系统越写越厚时的注入密度解法） |
| A12 | **LongBench-Write 6 维 rubric**（长度显式剥离质量分）+缺维度即整体作废重试 | LongWriter `evaluation/judge.txt:1-31` | distill 复刻验证多维化（Breadth&Depth 直击 gemini 偏短的质面）+judge JSON 缺维重试通用模式 |
| A13 | **Spoiler Alert 切点前瞻熵**：LM 对「接下来发生什么」采样续写算分布熵=悬念本体 | arXiv:2604.09854 | hook_strength 的 NN 增强特征（daemon 驻留 LM 采样 k 条短续写·只进切点评分参考侧） |
| A14 | **近期活跃实体 LRU 兜底注入**：注入名单=storyboard 白名单 ∪ 近 2-3 cluster 活跃实体简表 | Ex3 `Entity_info.py:159-163` | build_manifest 增 recently_active_entities 段——freestyle 带出计划外配角时前置防漂移（现在只能靠 UNKNOWN_CHARACTER 事后拦） |
| A15 | **确定性统计先行喂 archivist**：出场场次/对白占比/新名词频次先算好连证据样本注入 agent prompt | moyin `character-calibrator.ts:146-345` | archivist 前置 cluster_entity_stats 子步（压漏报——钟楼弃儿 writer 漏报靠 brief 兜底的教训） |

## B 级候选（观察/低优先）

- **WebNovelBench 中文网文百分位**（arXiv:2505.14818·EACL 2026）：8 维 LLM-judge→PCA→相对人类作品分布百分位——SFS 的正交外部锚，防蒸馏贴指纹但整体质量塌陷。落 distill 复刻旁证 advisory。
- **MCTS 走向前瞻**（Narrative Studio arXiv:2504.02426）：候选卡打分加浅层 lookahead（模拟成本高需廉价模型）。
- **WriteHERE 异质递归规划**（arXiv:2503.08275）：唯一真「超出 plan-then-write」范式，但与 plan_tracker 审计架构张力大——先读代码不落地。
- **StoryWriter 事件条件化历史压缩**（arXiv:2506.16445）：按当前要写事件反选历史片段，与 DeepLore 门控互补。
- **NovelForge @DSL 引用语法**（988★）：brief 里显式书写 manifest 引用表达式；整体架构（Electron）不引。
- **beat 卡字段增强**（PlotPilot emotion_beat_card：delta/emotion_gap/forbidden_drift/感官锚点四态轮换——注意 PlotPilot 侧未证实生产激活，只抄字段设计）；**走向卡悬念类型/情感迁移/颠覆强度字段**（AI_NG `prompt_definitions.py:273-284`）。
- **场景化反 AI 密度配额**（PlotPilot allowlist：battle 1.0/horror 1.5/revelation 2.0 per 1000）；**伏笔 TTL/预算化注入排序+阶段×动作矩阵**（自动 abandon 必须 advisory 化）；**信息密度/推进比例 scanner**（6 类信息点≥5/千字·需 statbase 校准）；**UNEVALUATED=-1 哨兵**；**记忆原子 candidate/confirmed 两态**；**错误分类学种子表**；**reveal_budget/title_promise 治理契约**。
- **DivEye surprisal 高阶特征**（arXiv:2509.18880）：surprisal 序列导数/burstiness-of-surprisal 加列。
- **CharacterBench sparse 维度主动探测**（arXiv:2412.11912）：voice-checker 对道德底线/知识边界类 sparse 字段定位场景针对性评。
- **voice_pack facts NLI 蕴含特征**（persona drift 文献法）：既有 110M 桥零新模型。
- **平台节奏档 rhythm_profile**（免费流 3 章微高潮 vs 付费流卷级高潮间距）——证据仅从业者共识，低权重注入。
- **AgentWrite steps 熔断**：storyboard 场景数 sanity 上限（顺手级）。
- **M4 双约束（含输出预算）分批器**（moyin `batch-processor.ts:26-326`）：批量 judge/fixer 任务防输出截断。
- **content-hash 防向量重复**（AI-Novel-Writing-Assistant）：嵌入索引重建幂等（core/ml 侧）。

## 不建议清单（四路汇总·择要）

PlotPilot daemon/SSE/SQLite 单写者全家桶、三套 checkpoint（Git 快照已是锚点·北极星⑥）、流式中途干预+logit_bias（其自认中文 tokenizer 碎片化不可用——反向实证支持若渝不做）、七维加权总分 enforce 拦截（法官化违北极星⑤）、章审五维 LLM 兜底（倒退）、正则 action extractor（Claude archivist 档次更高）、prompt 防注入校验器、空实现的共现关系升级、zipper-merge；AgentWrite per-scene 字数预算（违 v27 freestyle）、「下一章目录」注入（违 fluid 涌现）、Ex3 嵌入谷底切分（弱于 7 维切点评分）、Ex3 递归展开生成（预设式违北极星②·只抄摘要方向不抄生成方向）、扁平实体库/自由文本状态文档（倒退）；Agents' Room（已全面超越）、AIStoryWriter/AI-writer 等小库、SillyTavern-MemoryBooks（已有更强形态）、通用对话记忆论文（结构化状态库路线已更优）、Zipf 检测（无严肃支撑）、DDPO 全量后训练（不训基座·只抄度量）、CharacterJudge 本地部署（域错配）、平台留存数据自动调优（数据不公开+合规红线）。

## 方向性结论

1. 本地四库的**架构层已被榨干**，第二轮的产出全在**实现细节层**（prompt 工程/预算治理/契约防御），且以 PlotPilot 最富矿——但其废码双轨遍地，反向印证北极星⑥。
2. 联网增量最大方向是**叙事一致性学术**（Atlas 图检测/高熵定位/合成负样本），全部能接进既有检测/校准/飞轮栈。
3. **编排范式层无颠覆**：没有任何系统在架构上领先若渝；WriteHERE/MCTS 是仅有的两个真新范式，均张力大观察即可。
4. **中文网文垂直技术层无增量**（平台材料全是市场/合规向），唯一学术锚是 WebNovelBench。
5. 评估侧三个「协议级」发现值得优先内化：judge 换序双跑、短结构化理由优于长 CoT、质量与长度双轨分离。
