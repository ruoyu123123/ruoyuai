# 可成长模型化 Backlog（2026-06-30）

> 目标：把仍靠固定阈值/关键词/线性权重的写作顾问，逐步升级为可训练、可校准、可复用的模型能力。所有输出遵守北极星⑤：风格/工艺一律 advisory，只有客观一致性/格式契约可 hard_gate。

## 已接通的成长底座

- `FeatureStore`：`core/ml/feature_store/feature_cache.py` 批量缓存 VAD / surprisal / coherence，供 scanner 和 save_state 复用。
- `ModelRegistry`：`core/ml/registry/model_registry.py` 记录 active/shadow 模型版本、路径、指标。
- `DataFlywheel`：`core/ml/flywheel/data_collector.py` 在 cluster-save-state auto-post-reflect 后收集 paragraph / weak label / waiver / legacy fixer pair / checker brief / gen_fixer report / style repair report / judge report reliability / reading reflection / audit metadata。
- 默认入口：`core/scripts/nn_runtime_defaults.py` 被 `audit_hub.main()` / `save_state.main()` 调用，默认开启 NN + 可成长闭环门控。

## 2026-06-30 首批新增落地

- `DataFlywheel.CODE_TO_MODEL` 扩展到 12 个模型桶：`ai_tone` / `hook_strength` / `surprisal` / `tension_trajectory` / `coherence` / `emotion_arc` / `style_fidelity` / `dialogue_pragmatics` / `promise_payoff` / `character_trajectory` / `action_mentalizing` / `cognitive_load`。新增覆盖 `SITUATION_MODEL_DIM_DROPOUT`、`CENTERING_ROUGH_SHIFT_OVERLOAD`、`GROUP_DIALOGUE_IMBALANCE`、`REVEAL_TELL_OVERUSE`、`ACTANT_DRIFT_NO_PIVOT`、`ACTION_MENTAL_RATIO_DRIFT` 等规则信号，先让训练池长出数据。
- `antagonist_valence_trajectory.py` 的反派窗口 valence 优先消费 `emotion_vad`/`FeatureStore`，输出 `source=model_vad|lexicon_fallback` 与窗口计数；判定仍是 advisory trend，不改变 hard_gate。
- `situation_model_5dim_scanner.py` 旁挂 `model_coherence_at_boundaries`，用相邻场景 coherence score 做 evidence；5 维 dropout 规则不被模型覆盖。
- `judge_consensus.py` 输出 `calibration_features`（grade/confidence/evidence/persona/uncertainty 标准化特征），给后续 `judge_reliability_calibrator` 训练；`escalate_to_user` 原规则不变。
- `DataFlywheel._collect_judge_reports()` 读取 `_数据库/.judge_reports/cluster_<key>_*.json` 与当前 cluster `chapter_range` 内的 `ch_NNN_*.json`，写入 `training_pool/judge_reliability/judge_reports.jsonl`，沉淀 judge evidence / confidence / persona / uncertainty 特征。
- `DataFlywheel._collect_reading_reflections()` 读取 `_数据库/.reading_reflection/cluster_<key>_round_*.json`，把读者反思 issue 按 dimension 映射到 `dialogue_pragmatics` / `ai_tone` / `surprisal` / `tension_trajectory` 等训练池，并单独记录 `reader_experience` verdict。
- `DataFlywheel._collect_audit_metadata()` 读取 audit report 的 `scanner_status` / `waiver_audit` / `auto_fixed` / `pending_agent`，写入 `scanner_reliability` / `waiver_calibration` / `fix_routing`，不污染正文分类样本。
- `DataFlywheel._collect_checker_briefs()` 读取 `_数据库/.checker_briefs/cluster_<key>_{validator,voice}.json` 与当前 cluster `chapter_range` 内的 `ch_NNN_{validator,voice}.json`：validator/voice violations 按 `code` / `issue` 写入 `ai_tone` / `hook_strength` / `voice_drift` / `coherence` 等内容桶；brief 内嵌 `judge_report` 写入 `judge_reliability`；voice clean A 级 evidence 写入 `voice_drift` 正样本。
- `DataFlywheel._collect_fixer_reports()` 读取 `章节/_quality/fixer_report_*.json`：`voice_changes_per_violation` 形成 before/after 修复 pair；`files_written` / `violations_addressed` / `violations_skipped` / `rejected_blocks` 写入 `fix_routing`；`scanner_results` 写入 `scanner_reliability`，沉淀“修完后 scanner 是否通过”的真实闭环。
- `DataFlywheel._collect_repair_reports()` 按当前 cluster `chapter_range` 读取 `章节/第NNN章/第NNN章.repair.json`：`banned_fixes` → `ai_tone`，`tag_fixes` → `dialogue_pragmatics`，标点/禁词前后指标 → `style_fidelity`，收敛轮次 → `fix_routing`。

## P0：下一批最值得模型化

1. **作者条件化风格保真模型**
   - 当前：`style_evaluator.py` / `replication_fidelity_check.py` 多维固定权重、线性融合。
   - 模型化：以现有 SFS 特征 + 作者原文分布 + 多 seed 复刻结果训练 pairwise/calibration model。
   - 数据：SFS 历史、多 seed 复刻、用户最终采纳、reading-reflector verdict。
   - 输出：`learned_style_fidelity` / `learned_percentile`，保留每维解释。

2. **叙事连贯 / situation model 语义模型**
   - 当前：`coherence_scanner.py` 已有 BERT coherence；`situation_model_5dim_scanner.py`、`centering_theory_focus_scanner.py` 仍大量关键词/实体链规则。
   - 模型化：场景/段落转移模型，输出时间、空间、因果、意图、焦点人物桥接缺口。
   - 数据：COHERENCE_* weak labels、人工补桥 diff、validate/judge 连贯性反馈。

3. **Promise-Payoff 伏笔公平性模型**
   - 当前：`foreshadowing_handoff_scanner.py` / `cross_cluster_foreshadow_rhythm_aggregate.py` 多为字面片段匹配和固定间隔。
   - 模型化：伏笔 setup/reinforce/payoff 语义蕴含模型 + 遗忘风险/回收窗口校准。
   - 数据：伏笔表、foreshadower report、paid/reinforced ledger、突兀揭底反馈。

4. **角色轨迹模型**
   - 当前：persona drift cosine 阈值、actant 翻转规则、moves usage 固定计数。
   - 模型化：角色 voice/action/relation/actant/emotion 多维 trajectory，区分合理成长 vs OOC 漂移。
   - 数据：人物卡、角色行动表、voice-checker、persona_drift ledger、用户“不像角色”反馈。

5. **对白语用 / 群戏调度模型**
   - 当前：群戏点名率、裸拒绝率、buffer 词等规则。
   - 模型化：turn-level speaker attribution naturalness、dispreferred response 缓冲、多人 turn graph。
   - 数据：作者原文对话段、voice fix diff、voice-checker 问题、用户“对白尬”反馈。

## P1：高价值但需更多标签

6. **AI 腔统一探测器**：融合 syntax / subtext / rhetoric / repeat-noun / translationese；需多生成器负样本，当前 `quality_clf` 不上线。
7. **Tension trajectory 模型**：从窗口文本预测张力/缓冲/高潮/收束；服务 narrative_rhythm / plot_structure。
8. **转场 classifier**：替换 scene_seam marker/L1 分布，作者条件化 transition embedding。
9. **中文意合 discourse relation 模型**：显/隐因果、转折、递进是否自然，替代连接词密度。
10. **反馈吸收有效性模型**：判断 previous findings/waiver/lessons 是否被语义修复，而不是关键词命中。

## 数据收集约束

- 新模型训练前先看 `core/ml/flywheel/training_pool/` 每类样本量和来源分布。
- 任何 learned score 首版必须 shadow/advisory，不能替换 hard_gate。
- 如果标签只来自旧规则，必须混入 judge/human/diff 标签，避免把旧规则偏差蒸馏进模型。
- 小样本作者走作者内校准/分位数，不做跨作者一刀切。

---

## 2026-07-01 Tier S 已全部接线落地（9 agent 并行实现 · 22 scanner + 系统级修复 · 7791 测试零回归）

下面 Tier S 清单里列出的全部候选，已在同一 session 内完成实现，不再是"待做"：

- **emotion_vad 组（6 处）**：`dialogue_emotion_contagion_scanner.py` / `cn_emotion_vad_drift_scanner.py` / `narration_dialogue_vad_coherence_scanner.py` / `peak_chills_architecture_scanner.py` / `cross_cluster_ousiometric_emd_scanner.py` / `cross_cluster_emotion_pattern_aggregate.py` —— 全部改为 FeatureStore→`nn_vad_bridge`优先、词典兜底、`source` 字段标注来源。
- **surprisal_gpt2 组（3 处）**：`cross_cluster_hierarchical_position_surprisal_aggregate.py` / `prosodic_pause_anchor_scanner.py` / `zeugma_scanner.py`（后者采用保守的"补充证据"而非替换主判断）。
- **coherence_binary 组（1 处）**：`cross_cluster_continuity_aggregate.py` —— cliffhanger 回应度 + 时间过渡两维接模型；物件持续性维度评估后判定为存在性核对而非连贯性判断，保留确定性。
- **embedding_store/style_embed 组（9 处）**：`revision_homogenization_scanner.py` / `character_belief_ledger_scanner.py` / `agenda_drift_scanner.py` / `volume_arc_drift_scanner.py` / `volume_transition_scanner.py`（仅 hook 覆盖率维度） / `genre_dominance_scanner.py` / `genre_pack_clash_scanner.py` / `intentional_recurrence_scanner.py` / `macguffin_entanglement_scanner.py` —— 全部照 `topic_drift_scanner.py` 模式接 `_has_real_embedding_backend()` 门控 + `compute_embedding`/`cosine_similarity`，默认（无真后端）逐字节零回归。
- **nn_coref_bridge 消费组（3 处）**：`centering_theory_focus_scanner.py` / `pov_consistency_scanner.py` / `expert_blindspot_scanner.py` —— 指代消解统一走 `nn_coref_bridge.resolve_coreferences()`，返回空时 100% 回退各自原有正则逻辑。
- **系统级修复**：①`nn_coref_bridge.py` 的 HanLP 后端补上 subprocess 推理桥（新建 `core/ml/coref/coref_infer.py`），不再在系统 py 里直接 `import hanlp`（已用真实 venv 验证：venv 未装 hanlp → 正确降级 rule，非静默假成功）；②`data_collector.py` 的 `CODE_TO_MODEL` 桶表补全 4 类此前完全未收集训练数据的 issue code（`FORECASTING_TENSION_FLAT`/`FRISSON_*`/`BURST_LEAD_*`→`tension_trajectory`，`EXPERT_BLINDSPOT_*`→`cognitive_load`，`CN_EMOTION_*`/`OUSIOMETRIC_OSCILLATION_DEGRADED`→`emotion_arc`，`SCH_HIERARCHY_INVERTED`→`surprisal`）。

**已知遗留（记录不处理，非本轮范围·2026-07-03 状态更新）**：① ~~HanLP 仍未安装~~ → **已终局处理**：2026-07-03 真装后实锤 HanLP 开源版从未提供本地共指模型（上游不存在，非安装问题），hanlp 后端路径已整体拆除、coref 收敛 rule 单后端（详见"2026-07-01 全量扫描"节系统级条目的终局追记）；② ~~`embedding_store._api_embed()` 未做 L2 归一化~~ → **2026-07-02 已修**+回归测试；③ 语义相似度阈值（`MILESTONE_SEMANTIC_TOUCH_FLOOR=0.5` 等及 2026-07-02/03 新增的一批 0.68-0.75）仍是初始估计值，标注"待金标准校准"，**待 wave-4 embedding 性能层落地+配真后端后一并标定**。

---

## 2026-07-01 全量扫描（8 agent 逐一审查 209 个 scanner/engine/evaluator/aggregate）

覆盖 `core/scripts/` 全部 159 个 `*_scanner.py` + 6 个 `*_engine.py` + 13 个 evaluator/check + 2 个 classifier/analyzer + 29 个 `cross_cluster_*_aggregate.py`。判定标准同北极星⑤：客观契约核对留确定性，模糊语义/审美判断才是候选。**最大发现**：VAD/coherence/surprisal/style_embed/nn_coref 这几个已经训练部署好的模型资产，在全仓远不止已知的 5-6 个接入点——还有约 20 个文件本该调用它们、却仍在用自建占位词典/字符串匹配/n-gram 重叠，属于"零训练成本、纯接线"的最高性价比批次。

### Tier S｜已有训练好的模型资产但未接线（零训练成本·纯工程接线·最高优先级）—— ✅ 已于当日全部实现，见上方"已全部接线落地"小节

**该换 emotion_vad（已部署·meanCCC 0.80）的 6 处**（VAD 被反复重新发明是本次扫描出现频率最高的重复模式）：
`dialogue_emotion_contagion_scanner.py` / `narration_dialogue_vad_coherence_scanner.py` / `peak_chills_architecture_scanner.py` / `cn_emotion_vad_drift_scanner.py` / `cross_cluster_ousiometric_emd_scanner.py` / `cross_cluster_emotion_pattern_aggregate.py` —— 参照 `antagonist_valence_trajectory.py`/`character_vad_ued_scanner.py` 已验证的接入模式（`source=model_vad|lexicon_fallback`）。

**该换 surprisal_gpt2（已部署）的 3 处**：
`cross_cluster_hierarchical_position_surprisal_aggregate.py`（自称"LM-free"实为没接线，本批 ROI 最高单项）/ `prosodic_pause_anchor_scanner.py` / `zeugma_scanner.py`（动宾搭配困惑度尖峰代理选择偏好违反）。

**该换 coherence_binary（已部署）的 1 处**：
`cross_cluster_continuity_aggregate.py`（cliffhanger 回应/物件持续/时间过渡三维，n-gram 重叠对同义改写零容错，可与 P0-2 合并规划）。

**该换 style_embed / embedding_store（已部署·AP 0.887）的 8 处**：
`revision_homogenization_scanner.py`（P0-1 风格保真模型最直接接线口）/ `centering_theory_focus_scanner.py`（实体/共指提取部分）/ `character_belief_ledger_scanner.py` / `agenda_drift_scanner.py`（代码自曝"占位用 char Jaccard 替身"）/ `volume_arc_drift_scanner.py` / `volume_transition_scanner.py`（均直接服务北极星③"大势已定"）/ `genre_dominance_scanner.py`/`genre_pack_clash_scanner.py`（仿 `topic_drift_scanner.py` 已验证模式）/ `intentional_recurrence_scanner.py`（代码自曝"占位简化版"）/ `macguffin_entanglement_scanner.py`。

**该换 nn_coref_bridge（已部署但未调用）的 2 处**：
`pov_consistency_scanner.py` / `expert_blindspot_scanner.py`（`centering_theory_focus_scanner.py` 见上）。

**系统级接线缺口（不是单个 scanner 的问题，已核实代码）**：
`nn_coref_bridge.py` 的 HanLP 后端**没有走已确立的 subprocess 推理桥架构**——其余 4 个 NN 桥（vad/coherence/surprisal/character_network）都通过 `core/ml/.venv/Scripts/python.exe` 子进程调用（系统 Python 3.14 无 torch），但 `_resolve_hanlp()`（`nn_coref_bridge.py:214`）是直接 `import hanlp`，在系统 Python 里必定 `ImportError` 并静默降级 rule。`nn_runtime_defaults.py` 把 `RUOYU_NN_COREF` setdefault 为 `1`，但 `COREF_BACKEND` 这个第二层门控默认仍是 `"rule"` 且不在 `_CREATIVE_NN_GATES` 里——即"看起来默认打开了模型"实际因为内层没跟着切、跑的还是纯规则最近先行词匹配。需要新建 `nn_coref_infer.py` 仿 `nn_vad_bridge.py` 模式接入 venv 子进程，HanLP 后端目前形同虚设。**（2026-07-03 终局追记：subprocess 桥建好后真装 hanlp==2.1.3 实锤——HanLP 开源版从未提供过任何本地共指模型，`pretrained.coref` 模块在其 git 历史里从未存在，唯一共指能力在付费云端 API。该后端永远点不亮，已按清旧码原则整体拆除（`_resolve_hanlp`/`COREF_BACKEND`/`core/ml/coref/` 全删+防复活回归锁），coref 现为 rule 单后端；真中文共指模型转 Tier A 调研项。）**

### Tier A｜需要新训练小模型（数据/复用路径已清晰）

**A1. 叙事句级多分类（建议合并成一个训练项目，一次训练两处受益）**：`duration_mix_scanner.py`（Genette scene/summary/ellipsis/pause/stretch 五分类）+ `cohn_consciousness_mode_scanner.py`（quoted/psycho-narration/autonomous/自由间接引语FID 四分类）——两者都是"句子级叙事修辞模式分类"，当前都靠正则+固定触发词，大量无触发词的真实表达被默认兜底误分类；FID 检测学界无现成开箱模型，可参考 Sims/Bamman、Brooke et al. 词汇风格建模范式自建。

**A2. Theory-of-Mind / 信念嵌套关系抽取**：`cross_character_kth_order_belief_scanner.py`（K=2 嵌套信念断言"A以为B知道X"三元组抽取，当前靠元动词+40字窗口）+ `check_acr_frustration_consistency.py`（BPNSFS 三需求受挫→反应一致性推理，代码自认占位）。

**A3. NLI/语义蕴含（复用现成中文 NLI 数据集 OCNLI/CMNLI）**：`cross_book_invariant_scanner.py` / `cross_cluster_data_consumption_aggregate.py` / `cross_cluster_will_learn_aggregate.py` / `red_herring_recall_scanner.py` / `cross_cluster_meta_quality_aggregate.py`。

**A4. Learning-to-rank / 序列健康度模型**：`cluster_emergence_engine.py`（下个 cluster 候选排序——用户每次"从 candidate 里选 1 个"都在产生零成本隐式偏好标签，当前是手调加权+关键词 bag 重叠，全仓最值得优先做的一项）/ `cross_cluster_reader_retention_proxy_aggregate.py`（双层拍脑袋线性加权从未用真实数据校准）/ `cross_cluster_engagement_metrics_aggregate.py` / `cross_cluster_sagging_middle_aggregate.py`。

**A5. 话语标记/修辞装置的语义检测**：`unreliable_narrator_typology_scanner.py`（hedge detection）/ `discordance_signal_scanner.py`（irony/sarcasm detection）/ `rhetoric_parallel_scanner.py`（中文排比检测，代码已自引 arXiv:2312.00100 F1=0.43 先例）/ `parrhesia_density_scanner.py`（"打脸"trope 语义聚类——网文核心爽点，当前 3 词典严格 AND 逻辑极脆）。

**A6. 结构/beat 语义达成 + 实体状态追踪**：`cross_cluster_structure_compliance_aggregate.py`（beat 关键词表漏判同义改写）/ `object_biography_scanner.py`（物件生命周期相位追踪，类似 ProPara/OpenPI procedural state tracking）/ `cross_cluster_timeline_item_location_aggregate.py`。

**A7. 反派/角色深度、词汇语义扩展类**：`antagonist_fidelity_scanner.py`（反派刻板词典密度，作者自称阈值"待金标准校准"）/ `imageability_scanner.py`/`chapter_title_concreteness_scanner.py`/`world_register_drift_scanner.py`/`prose_child_voice_scanner.py`（词表→embedding 最近邻传播）。

### Tier B｜确认是候选但受众窄/数据稀缺，暂不排期

`chiaroscuro_scanner.py` `chronotope_typology_scanner.py` `cluster_rasa_layer_consistency_scanner.py` `allusion_ledger_scanner.py` `baxter_staging_density_scanner.py` `butler_yearning_4layer_scanner.py` `attribution_mode_scanner.py` `affective_theme_iteration_scanner.py` `acw_drift_scanner.py` `direction_card_poetics_scanner.py` `chapter_title_couplet_scanner.py`（建议直接抄现成对联数据集/模型如清华九歌，非训练新模型）`synesthesia_density_scanner.py` `zero_pronoun_density_scanner.py`（中文零指代消解是成熟子领域，见下方技术参考）`signed_relation_graph_scanner.py` `soundscape_trinity_scanner.py` `strategic_empathy_alignment_scanner.py` `scene_grounding_scanner.py`（已金标准校准零误报，模型化非紧迫）`scene_opener_xing_check.py` `dwell_progression_scanner.py` `frame_tale_consistency_scanner.py` `mystery_pledge_scanner.py` `first_encounter_anchor_scanner.py` `empathic_concern_distress_scanner.py` `failure_segment_prose_density_scanner.py` `cross_book_rank_scarcity_scanner.py` `cross_cluster_character_presence_balance_aggregate.py` `cross_cluster_relationship_trend_aggregate.py` `cross_cluster_scene_pov_diversity_aggregate.py` `cross_cluster_throughline_balance_aggregate.py` `cross_cluster_offscreen_aggregate.py` `cluster_evaluator.py` `style_repair_engine.py`

### 确认应保持确定性（不是候选·避免重复分析）

两类：① **hard_gate/客观契约核对**——文件是否存在、字数是否守恒、锁定事实是否冲突这类天然该是规则：`locked_fact_cross_scene_scanner.py` `dramatic_irony_gap_scanner.py`（消费已结构化的 belief ledger 做集合运算）`dramatic_question_lifecycle_scanner.py` `character_state_drift_scanner.py`（瓶颈是 producer 缺失非模型能力）`cross_cluster_declarative_data_aggregate.py` `cross_cluster_ending_diversity_aggregate.py` `cross_cluster_fate_drift_aggregate.py` `cross_cluster_narrative_debt_ledger_aggregate.py` `cast_economy_scanner.py` `causal_connector_scanner.py` `antagonist_rotation_scanner.py` `actant_drift_scanner.py`；② **良定义统计量**——Zipf α/二阶节奏方差比/Hurst 分形/基尼系数这类本身可精确计算，模型化无收益反损失可解释性：`zipf_alpha_scanner.py` `second_order_rhythm_scanner.py` `prose_rhythm_scanner.py` `world_term_seepage_scanner.py` `repeat_noun_density_scanner.py` `translationese_residual_scanner.py` `temporal_bootstrap_scanner.py`（Tarjan 图算法）`physio_cue_diversity_scanner.py`（已用 5 篇真作者语料校准）`power_progression_scanner.py` `integration_ridge_density_scanner.py` `lish_consecution_chain_scanner.py` `anachronism_scanner.py` `anadiplosis_scanner.py` `narratee_address_scanner.py` `narrative_short_sentence_scanner.py` `resource_ledger_scanner.py` `rhetorical_balance_scanner.py`。另有一批 evaluator（`cross_family_judge_check.py` `relationship_evaluator.py` `save_state_evaluators.py` `stress_evaluator.py`）本身只是薄 wrapper/记账层，真实判断已在别处由 Claude agent 完成并落盘，无判断本体。工程/运行时类（`self_heal_engine.py` `check_logging_standard.py`）不属于创作判断，划出北极星⑤讨论范围。

### 举一反三：系统性缺口（比单个文件更值得优先修）

1. **~~DataFlywheel `CODE_TO_MODEL` 13 桶表本身不完整~~ → 2026-07-02 全仓对账已完成**：AST 静态扫描 `core/scripts/*.py`（含 `"code":` 字面量 / `ISSUE_CODE* = "..."` 常量 / `ISSUE_CODES = (...)`/`{...}` 容器三种emit 惯例）核出 468 个此前从未注册的静态 code，逐一归桶：415 个新增进 13 桶（或复用既有的 `judge_reliability`/`waiver_calibration`/`scanner_reliability`/`reader_experience` 池名），53 个显式收进 `EXCLUDED_FROM_FLYWHEEL`（ledger 记账/文件契约/蒸馏管线自检等非正文判断信号，带理由）。顺手修了 2 个系统性漏洞：① `audit_hub._parse_scanner_json` 用 `f"{scanner.upper()}_{check}"` 拼 code 时 `check` 部分保留小写（如 `SEMANTIC_metaphor_explain`），原先 `.get(code)` 大小写敏感查表导致 8 个已注册的 `SEMANTIC_*` code 从未真正命中过——新增 `resolve_model_for_code()` 统一大小写不敏感查表；② `audit_hub._check_character_arc_drift` 等少数 code 是运行时 f-string 动态拼人名/维度名后缀，新增 `CODE_PREFIX_TO_MODEL` 前缀兜底（`CHARACTER_ARC_DRIFT_`/`BIBER_MDA_DRIFT_`）。另清掉 3 处 naming-drift 死码（`ANADIPLOSIS_OVERUSE`/`UNDERUSE`、`ATTRIBUTION_MODE_DRIFT`、`LLM_GRAMMAR_OVERUSE`——注册的字符串从未被对应 scanner 真实 emit 过，已替换成真实 code）。映射表拆到独立 `core/ml/flywheel/code_to_model_table.py`（`data_collector.py` 只 import 消费），回归锁 `tests/test_code_to_model_coverage.py` 用同款 AST 提取逻辑独立复扫全仓，断言每个静态 code 都被 `CODE_TO_MODEL ∪ CODE_PREFIX_TO_MODEL ∪ EXCLUDED_FROM_FLYWHEEL` 三选一覆盖，以后新 scanner 忘记注册会直接测红。
2. **~~`nn_coref_bridge.py` 的 HanLP 后端未接子进程桥~~ → 2026-07-03 终局：路径整体拆除**——桥接好后真装 hanlp 实锤上游从未提供本地共指模型（详见 Tier S 系统级条目终局追记），"接桥"这个方向本身被证伪。沉淀教训：**引用第三方预训练模型 key 前必须先在真实环境核实该 key 存在**（`dir(pkg.pretrained)` / 官方模型列表），否则整条集成路径都是对着幻觉施工。真中文共指 = Tier A 调研项（开源生态里中文 coref 现成 checkpoint 极稀缺，HanLP 云端付费 API 因联网+付费+违离线架构被否）。
3. **该抄现成方案而非训练模型的机会**：`style_analyzer.py` 的 `classify_speakers`（近 400 行手搓中文说话人黑名单+regex NER，应换 HanLP/LTP/spaCy-zh 成熟中文 NER——注意 HanLP 的 **NER/tok/pos 本地模型是真实存在的**，与 coref 不同，且 venv 现已装好 hanlp==2.1.3 可直接验证）、`zero_pronoun_density_scanner.py`/`lish_consecution_chain_scanner.py`（应换 jieba POS/依存句法或现成中文零指代消解方案而非正则代理）、`chapter_title_couplet_scanner.py`（可抄清华九歌对联数据集/模型）——这几个是"换库"不是"训模型"，成本更低，符合抄作业优先规则。

### 技术参考（本轮联网调研，做 Tier A 前先看这些而非从零设计）

- 中文 NLI：OCNLI（CLUE benchmark）—— [OCNLI: Original Chinese Natural Language Inference](https://www.researchgate.net/publication/347236190_OCNLI_Original_Chinese_Natural_Language_Inference)
- 中文零指代消解：Chen & Ng 经典无监督判别模型 + 2025 新语料 —— [Hierarchical Discourse-Semantic Modeling for Zero Pronoun Resolution in Chinese](https://www.mdpi.com/2504-2289/9/9/234)
- 隐式反馈排序（对应 cluster_emergence_engine）：BPR —— [BPR: Bayesian Personalized Ranking from Implicit Feedback](https://arxiv.org/pdf/1205.2618)
- 读者参与度预测（对应 reader_retention_proxy / engagement_metrics / sagging_middle）：期望-不确定性-惊奇特征预测续读/评论/投票率 —— [Modeling Story Expectations to Understand Engagement: A Generative Framework Using LLMs](https://arxiv.org/pdf/2412.15239)
- 自由间接引语/意识表征分类（对应 cohn_consciousness_mode）：无现成开箱模型需自建，可参考词汇风格建模范式与叙事基准框架 —— [NarraBench](https://arxiv.org/pdf/2510.09869)

---

## 2026-07-02 第二轮全量扫描（164 个非常规命名文件 · 6 agent 并行 · 19 项抽查验真零误差）

上一轮 209 文件只覆盖 `*_scanner/engine/aggregate/evaluator` 命名；本轮扫完 `core/scripts/` 剩余全部 164 个非常规命名文件（`ls | grep -vE` 差集），补齐全仓覆盖。判定分布：TIER_S 30 / TIER_A 7 / TIER_B ~10 / 其余 KEEP_DETERMINISTIC 或 PURE_INFRA。

### Tier S 第二批（30 文件 · 零训练纯接线 · 2026-07-02 已派 8 个实现 agent）

**VAD 组（9）**：`arc_aggregator`（DIRECTION_INTENSITY 词典）/ `narrator_calibrate`（_infer_outcome_heuristic negative_kw）/ `paragraph_engagement_heat_predictor`（_valence 词典）/ `snippet_seed`（_emotion_register）/ `glaser_four_levers`（_hit_lever emotionalize 分支）/ `horizontal_cloud_advisor`（_emotion_intensity 标点密度；另 _scene_lead_subject→coref）/ `dialogue_silence_density`（_emotion_context_match）/ `distill_finalize_verify`（_estimate_emotion）/ `adversarial_judge_pair`（attack A2 情感对位）

**surprisal 组（5）**：`author_brand_perplexity_drift`（char Shannon 熵占位·docstring 自认"真版用小 LM perplexity"）/ `belief_update_alignment`（_surprise_signal_score 惊讶词典）/ `entropy_hotspot_consistency_probe`（_block_entropy 字符熵·docstring 自己在等升级）/ `event_boundary_lc_signal`（_PE_LEX 16 词）/ `manifest_compress`（自称"LLMLingua 风格"却盲切前缀→surprisal 信息量优选截断）

**embedding 组（14）**：`memory_layer`（TF-IDF 三层记忆检索·同义改写查不到）/ `intent_ledger`（2-gram Jaccard）/ `learning_loop`（**仅** _attribute_to_skill_section 一处·其余 90% 是正确设计的确定性控制回路不碰）/ `chapter_end_anchor_scan`（关键词集合交集·hard_gate 维度不动）/ `skill_evolver`（_token_jaccard 经验合并·注：jaccard() 经实现期核实**非死代码**——tests/test_learning_skill_evolver_contract.py 有故意保留的回归钉子调用它，扫描期"零调用点"结论系漏 grep tests/，保留未删）/ `style_injector`（char-bigram 余弦 MMR）/ `subplot_progress_update`（字面 substring 判 subplot 出现·换说法即误标 dormant）/ `trope_tag_canonicalizer`（仅晋升候选加最近邻建议字段·不自动合并）/ `rag_retriever`（retrieve_embedding 死桩 TODO·走统一 embedding_store 非 openai 包）/ `sfs_axis_decomposer`（SHA-256 假分数 _placeholder）/ `sfs_calibration_probe`（char-3gram Jaccard 默认 scorer·--scorer 插件通道不动）/ `choice_consequence_ledger`（_check_resonance 字面命中）/ `deus_ex_solution_audit`（铺垫锚点字面子串计数→叠加语义扫历史摘要）/ `build_manifest`（_collect_relevant_heuristics 关键词重叠→embedding；**连带修 bug**：_collect_selective_history L3446/3467 裸用 embedding 无 _has_real_embedding_backend 门控，hash 假嵌入会静默冒充语义检索）

**coref 组（1+1）**：`character_network_extractor`（_attribute_dialogue 代词发言人抓不到·该文件 _annotate_vad 已半接线）+ `horizontal_cloud_advisor._scene_lead_subject`（并入 VAD 组实现）

### Tier A 增补（7 · 需训练/LLM-judge · 未排期）

`gricean_flouting_density`（Grice 四准则违反·语用维度现有资产不覆盖）/ `check_acr_frustration_consistency`（BPNSFS 6 类反应·A2 确认）/ `user_choice_learner`（对应 A4 learning-to-rank·当前逐维标量均值+朴素 confidence）/ `sdt_motivation_regulation_advisory`（SDT 六级动机·_placeholder）/ `cotton_needle_subtext_advisor`（表面客套 vs 潜台词攻击 discordance·VAD 单读数拆不开双层，同反讽检测难度）/ `covert_foreshadowing_audit`（5 类伏笔隐蔽度·自认"待金标准校准"）/ `cluster_burst_type_predictor`（9 类爆点·自认占位）。

**共性方案**：后三者 + `dialogue_sequence_expansion`（Tier B）同为"固定短语表→离散类目"形态，值得做一个通用 **embedding 零样本最近质心分类工具**（每类目若干典型例句当 prototype）一次覆盖，而非各训各的。

### 系统性发现（第二轮）

1. **CODE_TO_MODEL 桶表全仓性缺口**：6 批独立报告合计 45+ 静态 code 未注册（R8-R25 波次新 scanner 通病，`CODE_TO_MODEL.get(code)` None → continue 静默丢训练信号）；另有动态拼接 code（`CHARACTER_ARC_DRIFT_<角色名>`）静态表天然覆盖不了。**2026-07-02 实施**：全仓 emit 点对账 + 前缀映射 + 回归锁测试（新 scanner code 必须注册或显式进 EXCLUDED 集合，否则测试红）。
2. **`embedding_store._api_embed()` 缺 L2 归一化已修**（2026-07-02 主线程）：API 后端返回裸向量而 `cosine_similarity` 是假设归一的纯点积，其余三后端均归一——上一轮已知遗留，本轮新增 14 个 embedding 消费方后升级为必修，已修+回归测试。
3. **重复发明计数更新**：情绪/意外代理信号手搓 ≥14 处、语义相似度手搓 ≥10 处。时间线解释：多数手搓文件落款早于 nn 桥创建（2026-06-29），属合理技术欠账非重复踩坑；但暴露**新文件缺"先查已有模型资产"检查步**。
4. **🔴 EMBED_BACKEND 默认翻 ruoyu_style 的阻塞项（2026-07-03 实测）**：`ruoyu_style_encode_batch` 每次调用=新起 venv 子进程加载模型，实测冷启动 56.9s / 暖机 23s / 16 条 batch 25.8s（1.6s/条·batch 边际成本极低）。单条 `compute_embedding` 走它 = 每次 23s，scanner 逐段调用完全不可用。**结论：全部 embedding 接线在配真后端前保持诚实降级；翻默认前必须先做 wave-4 性能层**——①磁盘缓存（(backend, text-hash) 键·稳定语料如 prototype/锚点/skill 段落跨 cluster 复用，一次编码终身命中）+②批量 API（`compute_embeddings_batch` 路由 ruoyu_style 一次子进程编 N 条）+③scanner 侧先收集后批量的调用改造（或 venv 常驻 daemon 进程根治子进程冷启动）。数据飞轮端到端已验证真收集（主神验尸官 cluster_001 → 987 条样本落 11 桶·save_state.py:1307 真实挂线）。
5. **反面教材确认（不该模型化·已验证正确保持确定性）**：`author_signature_preservation`（verbatim 保留要字符级精确，embedding 反而误判）/ `chapter_splitter`（北极星④格式层禁语义）/ `learning_loop` 主体 + `pid_threshold_tuner`（确定性控制论回路）/ `distill_holdout`（统计方法论元层）/ `continuity_keywords`（本身就是 coherence 模型的确定性备胎）/ `validate_chapter`/`validate_style`（hard_gate 契约层）/ `cluster_segmenter`（有调研支撑的确定性选择·Zehe 2021 BERT 场景切分 F1 仅 24%）。

---

## 2026-07-03 Wave-3：Tier A 首批落地 + 真集成（4 并发线 · Fable 设计验收 / Sonnet 实现）

探索转向：全仓 373 文件两轮扫完不再扫文件，改做 Tier A 可落地子集 + 端到端集成。

1. **零样本原型分类工具**：新建 `core/scripts/zero_shot_prototype.py`（SetFit/SimpleShot nearest-centroid 范式·质心缓存·`_has_real_embedding_backend` 门控唯一持有点），一次接线 4 个"短语表→离散类目"占位 scanner：`cluster_burst_type_predictor`（9 类爆点）/ `covert_foreshadowing_audit`（5 类隐蔽度）/ `sdt_motivation_regulation_advisory`（SDT 六级）/ `dialogue_sequence_expansion`（CA 三类·并集只补召回）。prototype 例句 3-5 条/类，全标"待金标准校准"。
2. **pairwise 偏好排序器（A4·BPR）**：新建 `core/scripts/preference_ranker.py`（纯 python pairwise logistic·BPR loss 线性判别退化·seed 固定·<8 观察冷启动 None）。关键现实修正：原 `user_choice_learner` 在聚合瞬间丢弃候选完整特征（无 pairwise 原始记录可训），已在 `learn_from_choice()` 唯一时机点新增原始捕获（`用户偏好.json.pairwise_observations`）；`stakes_delta` 契约是 LLM 自由文本非数值（走文本量代理特征）。`cluster_emergence_engine` 只加 `preference_score`/`preference_rank_hint` advisory 字段不改序（北极星⑤+②）。**集成**：`RUOYU_PREF_RANKER` 已进 `nn_runtime_defaults._CREATIVE_NN_GATES`（纯 python 零延迟·不开则观察永不积累），`user_choice_learner` CLI 入口（plan 直调·不经 save_state.main）补挂 `enable_creative_nn_defaults()`。
3. **HanLP coref 后端证伪拆除**（负结果·见上方各处终局追记）。
4. **中文 NLI 蕴含桥**：`IDEA-CCNL/Erlangshen-Roberta-110M-NLI`（CMNLI+OCNLI+SNLI-zh 微调·390MB·hf-mirror 115s 下载成功）落 `core/ml/models/nli/`；`core/ml/nli/nli_infer.py` + `core/scripts/nn_nli_bridge.py`（仿 vad 桥·`RUOYU_NN_NLI` 默认 off）。接线：`cross_book_invariant_scanner`（单批 NLI 给 breach 附 `nli_evidence`·判定不变）+ `writer_truth_check`（仅 uncertain 分支附 `nli_supplement`·三态永不被改写）。踩坑记录：该 checkpoint 必须用模型卡口径（不显式传 token_type_ids·A/B 实测 5/5 vs 4/5）；uncertain 分支的"含 anchor 子串"筛选是逻辑真空（该分支只在全 0 命中时到达），改字符集合重叠度排序。**`RUOYU_NN_NLI` 刻意不进创作默认门控**：subprocess 冷加载 ~16s/次，writer_truth_check 逐 uncertain 声明调用会线性放大，等 wave-4 批量/常驻层再评估。
5. **数据飞轮端到端首跑**（主线程）：`主神验尸官` cluster_001 真实收集 987 条样本落 11 桶（813 段落+11 weak labels+163 audit metadata），`save_state.py:1307` 挂线确认——可成长架构数据侧闭环。

**Wave-4 候选（按本轮实测数据排定）**：① embedding/NLI 性能层（磁盘缓存 + `compute_embeddings_batch` 批量 API + scanner 收集后批调改造，或 venv 常驻 daemon 根治 23s/16s 子进程冷启动）→ 之后才能翻 `EMBED_BACKEND=ruoyu_style` / `RUOYU_NN_NLI` 创作默认；② 语义阈值金标准校准（真后端点亮后用 workspace/styles 作者语料标定 0.5-0.75 一批初值）；③ 真中文共指模型调研（HanLP 路线已证伪）；④ Tier A 剩余（A1 叙事句级分类/A2 ToM/NLI 扩展消费方/A5 话语标记）。

---

## 2026-07-03 Wave-4 已落地：embedding/NLI 性能层（地基主线程 + 7 组 Workflow 并发改造）

**地基**（`embedding_store.py`·commit 4a63e25）：①内存+磁盘缓存（`(method, sha256(text[:8000]))` 键·仅真后端缓存·失败兜底绝不入缓存防污染·`RUOYU_EMBED_CACHE=0` 可关·`RUOYU_EMBED_CACHE_MAX_FILES` 容量护栏）；②`compute_embeddings_batch`（去重→缓存→misses 单次后端批调用：ruoyu_style 走既有 encode_batch 单子进程、API 走原生 list input、mstyle/local 进程内逐条即批量）；③`prefetch_embeddings`（scanner 语义分支开头预热一次，其后既有逐条 `compute_embedding` 全部命中缓存——消费方最小 diff 改造模式）。

**消费方改造**：scanner 与检索器在语义后端可用时批量预取 embedding；`zero_shot_prototype.classify_batch` 合并 prototype 与待分类文本；dialogue/covert/sdt 消费方使用单批推理。

**基准实测（EMBED_BACKEND=ruoyu_style）**：10 段 prefetch 26.1s（vs 改造前逐条 ~230s，**~9x**）；prefetch 后逐条 0.000s；跨进程磁盘命中 0.004s；单条冷未命中仍 ~30s（子进程冷启动物理下限）。

**翻默认决策（数据支撑·暂不翻）**：批量化后每个语义 scanner 每 cluster 仍付 ~25s 一次性子进程成本，全审核 ~20 个语义 scanner ≈ +8 分钟/cluster（修复循环 rescan 对新文本重付）。稳定语料（prototype/锚点/milestone）磁盘缓存终身命中，但正文段落每 cluster 全新。**真正的解锁是 wave-5：venv 常驻 daemon**（stdin/stdout 协议·模型常驻内存·预期 ~0.1s/调用，彻底消掉子进程冷启动）——daemon 落地后翻 `EMBED_BACKEND=ruoyu_style` + `RUOYU_NN_NLI` 进创作默认门控，再做阈值金标准校准。在此之前真语义保持 opt-in（现已实用：配 env 即享批量+缓存性能）。

---

## 2026-07-04 Wave-5 已落地：常驻推理 daemon + 真语义翻进创作默认（campaign 点火时刻）

**架构**（对标 llama.cpp server / MLflow local inference / vLLM sleep mode·纯 stdlib 零新依赖）：
- `core/ml/daemon/model_daemon.py`：venv 侧 ThreadingHTTPServer（127.0.0.1+token），**5 类模型懒加载驻内存**（style_embed author/character、vad、coherence、surprisal、nli——复用各 infer 模块函数不复制逻辑），全局推理锁串行化 GPU，idle 30min 自退（`RUOYU_NN_DAEMON_IDLE_SEC`），发现文件 `core/ml/.cache/daemon/daemon.json` 原子写。
- `core/scripts/nn_daemon_client.py`：系统侧 stdlib urllib 客户端，`enabled()`（`RUOYU_NN_DAEMON`）/`ensure_daemon()`（并发锁防重复拉起）/`infer()`（任何失败返 None）。
- **五桥 daemon-first**：4 个 nn 桥 + `embedding_store.ruoyu_style_encode_batch` 三层降级链 daemon（热 0.04-0.13s）→ 子进程（~25s）→ 启发式，daemon/subprocess 共用同一后处理函数防口径漂移。

**实现期抓修的 3 个真 bug**：① vad/coherence 同名 `model.py` 在常驻同进程撞 `sys.modules` 缓存（第二个加载的 task 拿到第一个的模型类·真机三连跳验证修复）；② HTTP header 大小写鉴权 bug（urllib 自动 capitalize token header，`dict(headers)` 丢大小写不敏感 → 真实鉴权必败·`_get_header_ci` 修复）；③ **daemon 进程风暴**（用户目击"弹一堆命令行"：拉起无失败冷却+部分路径无隐藏窗口标志+测试真拉起不收尾 → 积 24 僵尸进程）——三层根治：`CREATE_NO_WINDOW`（任何路径不弹窗）+ pytest 环境拒绝真拉起（`PYTEST_CURRENT_TEST` 守卫·显式 `RUOYU_NN_DAEMON_ALLOW_SPAWN_IN_TESTS=1` 才放行）+ 拉起失败 300s 冷却，各配回归锁（决定性验证：跑完整套 daemon 测试后系统 daemon 进程数=0）。

**真机基准（2026-07-04）**：daemon 壳启动 0.7s；style_embed 模型加载 24.8s（daemon 生命周期一次）；**热路径 style_embed 10 段 0.042s / vad 0.131s**——对比 Wave-4 每 scanner 每 scan ~25s，热路径 ~600x，"+8 分钟/cluster"顾虑消灭。

**已翻创作默认**（`nn_runtime_defaults`·setdefault 不破测试·显式设置不覆盖）：`RUOYU_NN_DAEMON=1` + `RUOYU_NN_NLI=1` + **`EMBED_BACKEND=ruoyu_style`**（新增字符串值型 `_CREATIVE_ENV_DEFAULTS` 机制）——全仓 20+ embedding 语义接线、NLI 蕴含补判、5 类 NN 桥在创作流程真实点亮。conftest 隔离同步（`EMBED_BACKEND` 进 `_NN_GATES` 防测试泄漏）。

**Wave-6 候选**：① 语义阈值金标准校准（真后端已默认点亮·用 workspace/styles 作者语料标定 0.5-0.75 一批初值——现在是解锁状态）；② daemon 生产观测（首次真实 /cluster-write 全流程下的 daemon 命中率/延迟分布·MAPE-K incidents 有无新指纹）；③ Tier A 剩余（A1 叙事句级分类/A2 ToM/A5 话语标记）；④ 真中文共指模型调研。

---

## 2026-07-04 Wave-6 校准首轮：🔴 重大否定性发现——ruoyu_style 对内容关系在单段粒度下致盲

**测量**（`core/ml/calibration/semantic_threshold_calibrator.py`·3 本书各 150 对×5 类·seed=20260704·daemon 真机·报告 `core/ml/calibration/reports/ruoyu_style_separability_20260704.md`）：
- 内容敏感度探针 AUC(**相邻段 vs 同书跨章≥50**)=**0.5088（纯随机）**——模型分不清"内容真相关"与"同作者但内容无关"；基础可分性 AUC(相邻 vs 跨书)=0.5632；风格信号本身在单段粒度也只有 0.5582。
- **各向异性严重**：跨书完全无关段落对的余弦中位数 0.80、p95=0.99——整个空间挤在高余弦区。
- 唯一边际可用：content_echo 族（300 字摘要代理 vs 正文）AUC=0.6887，但 Youden 点 0.716 的 FPR 仍 0.513。
- **🔴 实务后果**：现有 5 个 content_echo 常量（choice_consequence 0.70 / deus_ex 0.68 / subplot 0.72 / milestone 0.5 / topic_drift）**全部 ≤ 负样本分布中位数 0.73**——真后端默认点亮后这些语义判断"见谁都说相关"，当前形同虚设（advisory 定位所以无实害，但零信息量）。
- 根因假设（有证据）：ruoyu_style 训练粒度 512-768 CJK 累积 chunk，生产 scanner 用 30-150 CJK 单段调用属分布外；content_echo 恰是较长文本侧表现最好，佐证长度假设。

**W6-C 决策（数据支撑·不在坏信号上调参）**：风格任务留 ruoyu_style（本职）；内容任务加 **daemon `content_embed` task（BAAI/bge-small-zh-v1.5·中文检索/相似度专训）**，先用同一 harness 同 seed 复测可分性（达标线 AUC≥0.80/0.70/0.80），达标后 embedding_store 增 `compute_content_embedding` 系 API 并迁移内容族消费方，再按内容后端分布应用阈值。单一全局 EMBED_BACKEND 服不了风格+内容两种语义，架构上拆开。（进行中）

**W6-A 注册表盘点（`core/ml/calibration/threshold_registry.json`·58 entries·回归锁 test_threshold_registry.py）关键修正与盲区**：
- **范围修正**：58 条中 **37 条是关键词密度/统计类阈值（other 族·与 embedding 余弦无关）**——需要"真作者语料统计基线"方法论（分位数带），与 embedding 正负样本对校准分开设计；embedding 余弦族仅 21 条（content_echo 6/content_dedup 6/style_drift 4/zero_shot 5）。
- **两个未标记盲区**（记于 known_blind_spots）：①`zero_shot_prototype` 默认参 `floor=0.5` 被 4 文件 6+ 调用点复用、影响面广；②`belief_update_alignment` 的 0.6/0.4 混合权重与词典分量权重。
- **已有历史实测数据的 10 常量**（dramatic_irony/emotion_granularity/group_dialogue 等注释带"真作者实测区间"）——校准时先挖历史数据，别当白纸重测；其中 `cross_scene_voice_drift.DEFAULT_EMBED_DRIFT_FLOOR=0.3` 已有定量反例（同角色声纹距实测 0.1062 < 0.3 地板）最该优先。
- **应用前置工程**：5 个 embedding 阈值（choice_consequence/deus_ex/subplot/trope/skill_evolver）无数值 env 覆盖需补包装；26/58 被测试钉值（改值须同步测试）；同名异值常量 MIN_SCENES(2 vs 3) 禁按名批处理；sfs 两文件重复 `(cos+1)/2*100` 公式改一处须同步另一处。

**W6-C 落地完成（2026-07-04·commit 992981c + 2e06c47·8267 测试零回归）**：
- **content_embed daemon task + bge 后端**：`core/ml/content_embed/content_infer.py`（BAAI/bge-small-zh-v1.5·CLS pooling·L2 归一·懒加载）+ `model_daemon.py` 挂 content_embed handler。同 harness 同 seed 复测**达标**：AUC(相邻 vs 跨书)=**0.859** / 内容敏感度=**0.763** / content_echo=**0.808**（vs ruoyu_style 全随机 0.51-0.69）。报告 `content_embed_separability_20260704.md`。
- **embedding_store 内容 API**：`content_backend_available()`（文件存在性门控）/ `compute_content_embedding(s)(_batch)`（不可用返 None·**无 hash 兜底**·hash 不是语义）/ `prefetch_content_embeddings()`。
- **13 content 族文件迁移 + 阈值重标**（风格→内容双轨）：content_echo 7（0.68-0.72→0.49-0.52·bge 值域低）、content_dedup 6（memory 0.42/skill_evolver 0.55/trope 0.55/rag `_EMBED_MIN_RELEVANCE` 0.30/build_manifest kw_hits 双点拉伸 `(sim-0.4)/(0.65-0.4)*5`/intent_ledger 新增 `HIGH_DRIFT_THRESHOLD=0.53`）。topic_drift 是 z-score 形态不改数值只换调用面。
- **zero_shot_prototype 切内容后端**（分类本是内容任务）+ 4 消费方零本体改动。
- **conftest 根治**（举一反三·系统性）：`content_backend_available()` 是文件存在性判定非环境变量·本机 bge 俱在时测试默认 True 会真触发子进程（263x 变慢+非确定性）→ `_isolate_nn_gates` 用 `RUOYU_CONTENT_EMBED_CKPT` 哨兵路径默认关闭·单点根治全仓。
- **2 真 bug**：rag_retriever + topic_drift 的 `compute_content_embedding` 返 None → `len(None)` 崩溃守卫（旧 hash 后端恒返向量掩盖了这个）。
- **physio_cue_diversity 统计族校准**（commit 2e06c47）：statbase 155章×3书 + 主线程独立复算证实 `FACIAL_RATIO_FLOOR=0.65` 误伤 **30%** 真作者章节（2026-06-20 手工 5 样本 max=0.566 漏尾部·真实 p95=0.75/max=0.857）→ **0.65→0.78**（p95 上方·仅 catch 最极端 3-5%）。⚠️ statbase 报告表格 `p95=0.6492` 是错的（与其 30.56% 误伤率自相矛盾·真 p95=0.75·验收时揪出）。
- **statistical_threshold_baseline harness**（`core/ml/calibration/statistical_threshold_baseline.py`）：37 条 other 族统计阈值的作者语料分位数基线方法论（与 embedding 族正交）·首批 6 scanner·仅 physio 判"过严"其余过松有余量。
- **注册表 reconcile**：迁移解决 12 处标记（"待金标准校准"→"金标准校准 2026-07-04"）+ physio 2 处 → marker_total 47→33·删 12 已解决 entry·`test_threshold_registry.py` 8 断言回归锁全绿。**教训**：多 entry 可共享同一块注释标记行（如 cross_cluster_character_presence 5 个 Gini 常量共享 [33]）·reconcile 时"删 0-live 文件 entry + 保留 entry 赋该文件全部 live 标记"，别拆成独占。

**Wave-6 续：现成中文模型军火库首战（2026-07-04·当日）**——调研 `workspace/_temp_research/现成中文模型军火库_2026-07-04.md`（5 类核实存在/5 类核实不存在防编造·举一反三揪出"中文人名/说话人识别"被 ≥7 文件重复发明）：
- **✅ zero_shot_prototype 扩类落地**（军火库 3.3·最小工作量·复用已上线基建零新依赖）：`unreliable_narrator_typology_scanner`（8 类 verbal_tic + **other 中性对照类**）+ `parrhesia_density_scanner`（truth_claim 二分类）语义补召回（并集非替换·标 source·内容后端 off 逐字节零回归）。真 bge 端到端验证：正则漏掉的同义改写被补召回、中性句归 other 跳过。**教训**：nearest-centroid 多类分类**必须加中性负类**否则中性句被强分（floor=0.5 过火·真机揪出修复·mock 测不到）。
- **🔴 HanLP MTL 阻塞发现**（军火库头号 ROI 候选去风险测试）：`file.hankcs.com` 连通、模型下载+加载成功（37.5s），但**推理崩溃**——venv 的 **transformers 5.12.1（NLI/VAD/bge/GPT-2 依赖）与 hanlp 2.1.3 硬不兼容**（`batch_encode_plus` 5.x 已移除·HanLP 仍调用）。按不兼容不降级：降 transformers 毁其余 4 模型（禁）·shim 是将就（禁）·唯一干净解 = **HanLP 独立 venv**（`core/ml/.venv_hanlp` + transformers 4.x·daemon 桥架构本就隔离在 venv）。ROI 从"最高·零依赖"降级为"需基础设施·独立一轮"。**教训**：模型存在+能下载 ≠ 能在本环境跑·依赖版本兼容是第三道坎·去风险必须测到**真推理出结果**。

**Wave-6 未尽（下一步）**：① HanLP 独立 venv（解依赖冲突后一次吃满 classify_speakers/zero_pronoun/lish/couplet + 举一反三 7 处人名重复发明）；② `ruoyu_style` 分布外根因（训练 512-768 CJK vs 生产单段 30-150）——可试扩窗或 content 后端替代；③ style_drift 族 4 条 + 各 zero_shot 原型集专属 floor 待金标准校准；④ Erlangshen-Sentiment + discordance 失谐信号（须过 AUC≥0.70）。
