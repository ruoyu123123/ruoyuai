<!-- 当前文件只保留 cluster 风格蒸馏的可执行教训；plan 契约以 plans/*.plan.json 为准。 -->

# 蒸馏系统经验库（自学习追加式）

> 跨项目通用教训沉淀。每次 `/distill-style` 完成后由 lessons-extractor agent 自动提取并追加。

## 元数据

- **当前主轨**：v22.cluster 故事块自适应蒸馏（LumberChunker EMNLP 2024 + MARCUS 2025）
- **追加规则**：每条教训必须有「现象 - 影响 - 修复 - 预防」四要素
- **去重规则**：先读现有库，相同根因不重复追加（可补充细节）
- **优先级**：⛔ 红线 / ⚠️ 重要 / 💡 提示

---

## v22.cluster 迁移警示

**主代理 Step 1 必做项**：
1. 读 `workspace/styles/<书名>/cluster_index.json`
   - **场景 A**：存在则直接复用（含 strong 边界）
   - **场景 B**：不存在则跑 `cluster_segmenter.py`（首轮 strong=0 是设计非 bug）
2. 调度循环 = `FOR cluster in cluster_index.clusters`
3. 场景 B 额外：表层蒸馏完成后重跑 segmenter 做 retro-refine

---

## 1. 调度层

### L1.1 ⛔ Agent prompt 必须传契约字段
- `PROJECT / CHAPTER / MODE / MANIFEST / PLAN_ID / STEP` 缺一被 hook exit 2

### L1.2 ⛔ 不要传 team_name 除非先 TeamCreate

### L1.3 ⚠️ 工作目录永远用绝对路径（用子 shell `(cd path && cmd)`）

### L1.4 💡 配额规划
- 配额 < 单阶段成本 x 1.5 时主动暂停；短/长周期重置策略不同

---

## 2. Agent 执行层

### L2.1 ⛔ Continuity 空架子先占位（抗 turn 截断）
- 先 Write 空架子 continuity - 写单章 JSON - 最后 Edit 回填

### L2.2 ⛔ 3 轮内收手（v1 出稿 - v2 修关键 - v3 微调）

### L2.4 ⚠️ Write 一次性写入 > Edit 增量构建

### L2.7 ⛔ 单次蒸馏批次硬上限 300 章（推荐 200-250）

### L2.8 ⛔ Cluster 故事块自适应切分
- 业界：LumberChunker +7.37% DCG@20 / MARCUS event-centric
- 调度 = `FOR cluster in cluster_index.clusters`，不用章数取模

---

## 3. 数据质量

### L3.1 ⛔ JSON 转义错误前置检测
- 写完立即 `python -c "import json; json.load(open(...))"` 自验
- 高频模式：正号嵌入数值 / 双引号嵌套 / 方括号术语
- metrics 损坏立即救援；主 JSON 记录缺失清单不阻塞聚合

---

## 4. 方法论（蒸馏算法）

### L4.1 ⛔ 基线区间不是必填堆砌
- ref 实际值才是目标，区间是防偏离红线
- 每条 CC 约束标注 semantic: 防偏离红线 | 目标值 | 下限保护

### L4.2 ⛔ 章型亚型必须显式拆分（方差 >30% 自动拆）

### L4.3 ⛔ 功能词双向约束（下限保护 + 上限保护）

### L4.4 ⛔ CC 约束必须双侧区间

### L4.7 💡 RCA 分离协议
- A 类（skill）/ B 类（单样本噪声）/ C 类（工具盲点）
- C 类不阻塞收敛，只进 future work

### L4.8 ⚠️ 闭环复刻三轮迭代法
- Round 1 识别共性 / Round 2 目标 SFS>=80 / Round 3 目标 SFS>=90
- 未达标触发 RCA 分离，不强行 round4

### L4.9 ⚠️ 闪光点保留：禁止动上一轮 PASS 维度的约束

### L4.10 ⛔ Rollback 纪律：SFS 跌>=3 分自动 rollback；保留>=3 版快照

### L4.11 ⛔ 短样本句长保护（极短<=15% + 中段>=25% + 长段<=20%）

### L4.12 ⚠️ 链式动作检测模式多样性而非次数

### L4.13 ⚠️ 标点密度 6 类 x 章型全覆盖

### L4.14 ⚠️ 禁用淡化表述，改为卷型差异化

### L4.17 ⚠️ 骨架稳定性
- 连续 3+ 版核心指纹延续率 100% + |delta| < 10% = 已收敛

### L4.18 ⚠️ 免重测三条件（骨架延续 + 漂移<10% + 无颠覆章型）

---

## 5. 评估工具盲点（Future work）

- L5.1 短样本对话下限
- L5.2 speaker_count 去标签识别
- L5.5 单 ref 噪声（已由 L19.1 修复）

---

## 6. 通用红线

- RL-G1: 全量遇限额等重置+救援，不改采样
- RL-G2: 量化数据真跑 style_analyzer；黄金段落逐字摘录
- RL-G3: N 章/agent 输出 N 个独立 JSON
- RL-G4: 完成必须有数据证据

---

## 7. 元教训

- 每条：现象 - 影响 - 修复 - 预防
- 新经验先 grep 去重
- 仅提取跨项目通用教训

---

## 8. Plan 强制规划层

> 详见 CLAUDE.md「Plan 强制规划」。本节仅保留实操翻车教训。

### L8.3 💡 hook 关键词用词组不用单字（中文单字都是高频字）

### L8.4 ⚠️ 续跑点唯一真相源 = plan JSON；`.wal/` 只是产物区不是断点机制
- `wal_recovery.py` 读 `_数据库/.plans/<plan_id>.json` 的 `steps[].status` 算**第一个未完成 step**。
- `.wal/` 存的是各 step 的产物与回执（summary / archive / state_delta / receipt…），**不承载 step 进度**。
- 禁止据任何 `.wal/` 文件判断中断/完成（cluster-save-state step 1 产物 = `cluster_<key>_schema_validate.json` 校验报告；历史项目可能遗留 0 字节 `cluster_<key>_save_state.json` 空标记，一律忽略）。

### L8.5 ⛔ PostToolUse hook 严禁拦截（永远 exit 0；拦截只交 PreToolUse）

---

## 9. 蒸馏-写作落地层

### L9.1 ⛔ 蒸馏库写完不等于被用
- novel-writer 只读 skill_FINAL.md，不读 35 维度
- 修复：style_injector.py 把蒸馏指纹转译为可执行指令注入 manifest

### L9.2 ⚠️ writer 申报必须可被 validator 验真（substring/grep 配套）

### L9.3 💡 轮拿用权重避免（x0.1）不完全排除

### L9.4 ⚠️ 蒸馏维度审计：写作时未使用 = 浪费

---

## 10. AI 监督分权层

### L10.1 ⛔ Writer 自评不可喂给 Judge（拆 FACTUAL + SELF_EVAL）
### L10.2 ⚠️ JudgeReport 含 confidence + reasoning trace（<0.7 触发再审）
### L10.3 ⚠️ 关键决策 >=2 judge 共识
### L10.4 ⛔ Judge 之间不互相读报告（并行启动，主代理仲裁）
### L10.5 💡 Meta-Judge 定期审计（连续 5 章全 A/D 触发 spike）

---

## 11. 业界对照录

### L11.1 ⛔ Memory Drift（65% 失败归因）-> RAG 集成 build_manifest
### L11.2 ⛔ locked_facts 字段（角色可机器校验属性）
### L11.3 ⚠️ 状态同步 _version 字段（乐观并发）
### L11.4 ⚠️ JudgeReport schema_version 字段
### L11.5 💡 rollback_to_chapter.py

---

## 12. SCORE 框架

### L12.1 ⚠️ State Tracking + Hybrid Retrieval（state_tracker + rag_retriever）
### L12.2 ⚠️ Character Index（character_index.json）
### L12.3 ⚠️ Story Bible 自动提取（story_bible_extractor.py）
### L12.4 ⚠️ 14 维评分（JudgeReport.score_14dim）

---

## 13. 调研先行

### L13.1 ⛔ 灵感/走向卡前必须联网（novel-researcher agent）
### L13.2 ⚠️ 双管齐下：CLAUDE.md 硬规则 + RESEARCH_REF 缺失检测
### L13.4 💡 缓存 24h 内同主题复用
### L13.5 💡 不外发 locked_facts / 章节正文

---

## 15. 段/场景级扫描

### L15.1 ⚠️ GMC 场景扫描（缺>=2 要素 = summary 非 story）
### L15.2 ⚠️ MRU 动机-反应顺序（对话早于身体反应报警）
### L15.3 ⚠️ Orphan Objects（出现 1 次 = 深化或砍）
### L15.4 ⚠️ Micro-tension（低张力段 > 1/3 报警）
### L15.5 💡 POV 距离梯度（连续>=3 段同距离 = 单调）

---

## 16. 情节结构层

### L16.1 ⛔ 15-beat 映射（beat_map.json）
### L16.2 ⛔ Try-Fail Cycle（连续 3 章无 fail 报警）
### L16.3 ⛔ Midpoint Reversal（中点+-5 章 zone）
### L16.4 ⛔ Information Asymmetry（knowledge_graph.json）
### L16.5 ⚠️ 角色弧光四段（Lie/Want/Need/Truth）
### L16.6 ⚠️ 多线沉睡检测（>5 章触发激活）

---

## 17. 端到端验证

### L17.1 ⛔ 纯算法逻辑用脚本不用 agent（splitter 教训）
### L17.2 ⛔ 检测器必须按 scene_type 豁免分流（噪音稀释真信号）
### L17.5 💡 每加 3-5 功能跑一次真实验证

---

## 18. cluster 复刻闭环

### L18.1 ⛔ 复刻必须与正式写作同栈
- Claude agent 按 skill 写场景稿，`distill_replicate.py` 调 gemini 分段润色并落终稿

### L18.2 ⛔ SFS 非单调收敛 = 选峰值版本
- 连续 2 轮下降即停；新增约束不动已有约束

### L18.3 💡 agent 必须 Read 原始 eval JSON，不盲信简报

### L18.5 💡 FINAL = 峰值版本，非最后一版

---

## 19. style_evaluator 测量层

### L19.1 ⛔ 单 ref 严重失真，必须 multi-ref
- 蛊真人回测：单 ref 54-67 -> multi-ref 70-85（+13-17 分）
- phase-3 必须 `--multi-ref-from-dir <原文> --multi-ref-count 5`

### L19.2 ⚠️ SFS vs 主观背离时：先 multi-ref 排除失真再改 skill

### L19.3 💡 写作端不调 style_evaluator；validate_style 自动校准
- hard_gate 只有 STYLE_单段超长，其余 14 项 advisory

---

## 20. 全书分批蒸馏 + Workflow

### L20.1 ⛔ brief schema 对齐 consumer key 命名（grep 所有 consumer 再设计）
### L20.2 ⛔ Workflow result 不验落盘（必须事后扫描 json.load + BOM）
### L20.4 ⚠️ 用 Write 工具写 JSON（禁 Bash/PowerShell 防 BOM）
### L20.7 跨脚本章号命名统一（双命名兼容）
### L20.8 ⚠️ 管道后 $? 取最后一段 exit（须单独跑或读 verdict 字段）

---

**范围**：cluster 风格蒸馏、同栈复刻、SFS 收敛与出货验证。
