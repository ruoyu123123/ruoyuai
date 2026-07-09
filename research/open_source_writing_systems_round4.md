# 开源写作系统挖掘 Round 4（2026-07-09）

> 目标循环第 2 次 GitHub 挖掘轮。本轮结论：**0/24 通过北极星四条闸，判定 NO_NEW_CANDIDATES（3-strike 计数器第 2 次命中）**。

## 方法

Workflow 6 全新角度（round3 用过的角度不再重复）：作者风格迁移/stylometry、自我修订散文质量循环、中文网文钩子分析、角色声纹一致性、长文benchmark引用链、长写作会话上下文压缩。31 原始候选 → 去重（排除 round1+2+3 共43个已覆盖）24 个新候选 → 北极星四问闸 → **24/24 FAIL**。

## 已评估候选清单（24个，全部 FAIL，按主要拒因分组）

### A. 风格迁移/stylometry 学术实现（纯英文栈 + 已有等价生产能力）
- `zacharyhorvitz/TinyStyler`（EMNLP2024 Findings）— 800M小模型条件rewrite，作者embedding来自LUAR系(英文Reddit/Enron)，零中文能力；且核心机制是"事后整体rewrite"非"生成时注入"，与若渝架构不同源；`ruoyu_style`风格嵌入模型已实装生产
- `jfisher52/StyleRemix`（arXiv:2408.15666）— 作者身份**混淆**(obfuscation)工具非复刻工具，方向与目标相反；预训练LoRA轴基于英文AuthorMix语料
- `nlpsoc/Style-Embeddings`（ACL2022）— 英文RoBERTa-base对比学习内容无关风格向量，与`style_embed_sfs.py`的`embedding_sfs`（自训ruoyu_style·已验证内容无关性）实质重复
- `jaaack-wang/llms-implicit-writing-styles-imitation`（EMNLP2025 Findings，arXiv:2509.14543）— 纯英文评测harness(AA/AV分类器+LIWC)，负面发现型论文(LLM在非正式文体风格模仿差)，无可移植生成/训练能力

### B. 自我修订/迭代精修（已被若渝AI实测证伪的方向）
- `madaan/self-refine` — **若渝AI自己代码`gen_writer.py`第130-135/2503-2507行已明确记录**："self-refine反复迭代会同质化(模型把自己的输出当锚反复收敛到同一坨)"，已主动弃用改走best-of-N+SFS/AV-judge配对择优。这是有实证的、已走过的死路，非未探索方向
- `weiyueli7/llm-review` — 创意写作评审辅助论文机制，与cluster-write step3双轨质检(audit_hub+reflector)+hard_gate_reverify闭环重叠
- `Alex-Gurung/CHIRON`（EMNLP2024 Findings）、`Alex-Gurung/ReasoningNCP` — 角色一致性/推理链检测研究代码，与locked_fact_cross_scene_scanner + reflector维度重叠

### C. 中文网文/开源小说写作工具（同类工具，未见超越现有机制）
- `alienet1109/BookWorld`、`fQwQf/PersonaForge`、`Ckokoski/authorclaw`、`datacrystals/AIStoryWriter`、`FaceDeer/storyteller` — 同类AI小说生成工具，均未见超越cluster+涟漪+emergence+风格蒸馏组合的机制

### D. 长文本benchmark/连贯性研究（评测工具非生成机制，或与已有子系统重复）
- `SimengSun/ChapterBreak`（NAACL2022）— 章节边界预测benchmark，与chapter_splitter最佳切点评分重叠
- `LIFEBench/LIFEBench` — 长文本生成能力评测集，纯benchmark无生成/质检机制可借鉴
- `yangkevin2/doc-story-generation`（DOC，ACL2023，与round3的`facebookresearch/doc-storygen-v2`同论文不同仓库）— 需logit级FUDGE控制，gen-model远程API拿不到，且"生成时锁细节"违反北极星②③
- `kabirahuja2431/FlawedFictions` — 叙事缺陷检测研究，与audit_hub scanner体系重叠
- `jenna-russell/storyscope` — 叙事分析工具，功能被现有narrative_scanner/plot_structure_scanner覆盖

### E. 长文本记忆/上下文管理（方向与若渝AI既定纪律相反）
- `aiwaves-cn/RecurrentGPT` — 段落级自循环长文续写，短时记忆重写无locked_facts兜底、长时记忆靠cosine近似检索，是build_manifest结构化压缩注入的"无schema粗糙版"
- `microsoft/LLMLingua` — 困惑度剪枝压缩上下文，**关键技术洞察**：困惑度剪枝的判据是"可预测=低信息量可删"，但作者风格指纹恰恰寄生在"低困惑度"的口癖/虚词/节奏性重复里，会误删风格指纹；且与`feedback-no-token-saving`(全量传LLM不截断)既定纪律方向相反
- `yelboudouri/RPEval` — 角色扮演对话评测集，与voice_pack/novel-voice-checker场景不同(单轮聊天 vs 长篇小说多角色)

### F. Agent框架/其他
- `haowjy/creative-writing-skills`、`conorbronsdon/avoid-ai-writing`、`NousResearch/autonovel` — 轻量Claude skill/prompt集合或框架demo，功能已被若渝AI现有反AI腔调守卫+scanner体系覆盖

## 方法论纪律备忘

- 本轮多条拒绝理由直接引用若渝AI**自身代码注释和历史memory**作为实证（`gen_writer.py`具体行号引用self-refine弃用理由、`feedback-no-token-saving`/`project-memory-novel-storage-research`引用LLMLingua方向冲突、`project-ml-wave6-content-dual-track`引用ruoyu_style已验证内容无关性），非空泛套话
- 语言不兼容是本轮最常见的新增拒因类型（round3未出现）：多个学术风格迁移模型是英文专属预训练权重，对中文网文场景"从零重训"不满足"抄作业优先"前提
- 一个有价值的负面技术教训被本轮挖出但**不构成借鉴**：LLMLingua式困惑度压缩与风格保真度目标存在系统性冲突（低困惑度≠低价值，反而可能是作者指纹）——这条洞察本身值得记忆，但候选项目本体仍FAIL

## 状态

3-strike 计数器：**第 2 次命中 NO_NEW_CANDIDATES**。再命中 1 次（连续3次）即可终止本目标循环。
