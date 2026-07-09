# 开源写作系统挖掘 Round 5（2026-07-09）

> 目标循环第 3 次（连续第 3 次）GitHub 挖掘轮。本轮结论：**0/37 通过北极星四条闸，判定 NO_NEW_CANDIDATES（3-strike 计数器第 3 次命中·连续三轮达成·目标循环 GitHub 挖掘维度终止）**。

## 方法

Workflow 6 全新角度（与 round3/round4 用过的角度均不重叠）：awesome-list 元策展、近30-60天新发布项目、配角智能塑造、喜剧节奏生成、剧集化连载钩子（跨界电视编剧室/条漫工具）、翻译声纹一致性。45 原始候选 → 去重（排除 round1+2+3+4 共67个已覆盖）37 个新候选 → 北极星四问闸 → **37/37 FAIL，无一通过**。

## 已评估候选清单（37个，全部 FAIL，按主要拒因分组）

### A. 叙事结构/收尾控制学术研究（与软牵引原则冲突）
- `adbrei/RENarGen`（NAACL2024）— bookending先锁首尾句再回填，与北极星③"绝不硬锁"正面冲突
- `OpenDFM/ibsen`（ACL2024 IBSEN）— director agent监督多actor朝目标推进+人类实时插话，与现有volume_convergence_anchor+drift_scanner组合重复
- `HAMLET-2025/HAMLET` — 多自主LLM角色实时群聊broadcast道具/环境状态同步，代码未发布(仅论文占位页)，且解决"多并发agent同步"问题若渝AI根本不存在(单writer批处理架构)
- `johnnie193/Open-Theatre`、`WyseOS/fictionx-story-gen` — 同类交互式戏剧/无限故事生成引擎，实时/游戏范式与cluster批处理不兼容

### B. 风格计量学术工具（均被现有Python原生scanner功能超越）
- `computationalstylistics/stylo`（R package）— 滑窗风格计量分类可视化，功能被`cross_scene_voice_drift_scanner.py`+`cross_cluster_style_drift_scanner.py`+`function_word_fingerprint_scanner.py`三个已有scanner覆盖且更精细(Python原生 vs 需R运行时+X11)
- `stylo-explorer/rolling-stylometry-explorer` — Angular前端可视化工具，非机器可消费输出，同上三scanner已覆盖
- `bfelbo/DeepMoji` — 2017年Python2.7/Keras2.0.5/Theano遗留栈，英文Twitter+emoji训练零中文迁移，被Wave6 `emotion-arc` NN scanner覆盖(memory `project-nn-expansion-w6`)
- `tuhinjubcse/SarcasmGeneration-ACL2020`、`DipInfo-Unito/IronicContentGeneration`、`SaveTheRbtz/humor` — 讽刺/幽默生成学术研究，英文语料且与反AI腔调守卫/情绪标点系统重叠

### C. 多智能体角色扮演/持久化仿真（架构范式不兼容）
- `joonspk-research/generative_agents`（斯坦福著名论文）— **grep核实**若渝AI`sandbox_emergence_candidates.py`文档字符串已明确设计K-agent自由互动仿真占位实现(defer到独立multi-agent框架)，架构上是持续运行游戏服务器+Django+Tiled地图，与批处理CLI流水线不兼容
- `AkshitIreddy/Interactive-LLM-Powered-NPCs`、`Neph0s/awesome-llm-role-playing-with-persona`、`thu-coai/CharacterBench` — 角色扮演/persona一致性研究，单轮聊天场景与长篇小说多角色场景不同

### D. 中文AI小说写作工具（同类未见突破）
- `OpenCoven/open-fable`、`wailers9/bragi`、`Narcooo/inkos`、`ydsgangge-ux/StoryCanvas`、`real-Elysia886/inkflow`、`zy-zmc/tianming-novel-ai-writer`、`remacheybn408-boop/ProseForge`、`calesthio/OpenMontage` — 同类AI写作工具，未见超越现有cluster+涟漪+emergence+风格蒸馏组合的机制

### E. 翻译一致性/篇章级MT（问题空间不匹配）
- `longyuewangdcu/GuoFeng-Webnovel` — 网文中译英/德/俄篇章级翻译一致性语料，"一致性"标注对象是跨语言译文质量非中文创作内部声音一致性，问题空间不对口
- `YutongWang1216/DocMTAgent`、`andrewyng/translation-agent`、`darkautism/ai-novel-translation` — 篇章级机器翻译agent，同上问题空间不匹配

### F. 学术评测/摘要研究（benchmark或与已有scanner重复）
- `thu-coai/UNION`（EMNLP2020）— 英文BERT无参考故事合理性判别器，功能被`coherence_scanner.py`覆盖(同源判别式方法论)
- `THU-KEG/StoryAlign`、`ppapalampidi/TRIPOD`、`ppapalampidi/SUMMER`（剧本摘要）、`robertobalestri/ANTS`、`dwlmt/Story-Untangling` — 叙事结构/剧本摘要研究，与narrative_scanner/plot_structure_scanner/beat_map重叠
- `luofuli/DualRL`（IJCAI2019文本风格迁移）、`zacharyhorvitz/Getting-Serious-With-LLMs`、`GAIR-NLP/GPTMan` — 风格迁移/幽默研究，英文栈+与distill-style/ruoyu_style重复
- `clipcurator/vertical-drama-script-formats` — 短剧脚本格式研究，格式层非机制层

## 方法论纪律备忘（三轮汇总）

三轮累计评估 **96 个候选（35+24+37），0 个通过北极星四条闸**。拒因呈现高度一致的收敛模式：
1. **架构粒度不兼容**（最常见）：候选是实时/回合制/多agent持续仿真，若渝AI是cluster批处理一次性生成
2. **功能实质重复**：候选解决的问题若渝AI已有更精细、更贴合中文网文场景的原生实现（且多次被grep源码/引用历史memory实证，非空谈）
3. **语言不匹配**：多个学术风格模型是英文专属训练权重，对中文网文零迁移价值，"抄作业"前提不满足
4. **违反北极星③/⑤**：候选核心机制常是"生成前锁定内容/结局"或"实时否决模型输出"，与"软牵引不硬锁"+"顾问非法官"直接冲突

## 状态

**3-strike 计数器：第 3 次连续命中 NO_NEW_CANDIDATES。达成用户"连续三次都找不到可以借鉴的项目为止"终止条件。**

GitHub 挖掘维度到此为止。若渝AI 现有架构（cluster批处理生成 + 涟漪引擎 + emergence 涌现 + 风格蒸馏 + advisory scanner 顾问制）在与 96 个候选逐一严格对照后，未发现需要引入的新外部机制——这本身是对现有架构成熟度的一次系统性交叉验证。
