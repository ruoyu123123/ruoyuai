# 学习系统全维度覆盖矩阵（v22.5）

## 8 大学习维度

| # | 维度 | 学习器 | 状态 |
|---|---|---|---|
| **L1** | 用户行为学习 | `user_experience_learner.py` | ✅ |
| **L2** | 错误模式聚类 | `error_pattern_analyzer.py` | ✅ |
| **L3** | 工具使用学习（死功能）| `dead_feature_detector.py` | ✅ |
| **L4** | 性能学习（manifest 消费率）| `dead_feature_detector.py` | ✅ |
| **L5** | 安全学习（injection 词典自演化）| 路线图 📋 |
| **L6** | 用户痛点学习 | 合并入 L1 | ✅ |
| **L7** | 协作学习（agent 调用图）| 路线图（复用 P6.2 God Log）📋 |
| **L8** | 高评分章节共性学习 | `high_score_pattern_extractor.py` | ✅ |

## 5 大演化层（v22 SE）

| # | 层 | 实施 |
|---|---|---|
| SE1 | Skill versioned evolution | `skill_evolver.py` ✅ |
| SE2 | ERL Heuristics 检索 | `build_manifest._collect_relevant_heuristics()` ✅ |
| SE3 | Meta-prompt Optimizer | `novel-meta-prompt-optimizer` agent ✅ |
| SE4 | 三角共演化 | `evolution_orchestrator.py` ✅ |
| SE5 | 跨项目迁移 | `universal_skill_pool.json` ✅ |

## 统一调度

`learning_hub.py` 统一调度全部学习器 + `/learning-status` 命令

## 路线图：L5 安全学习

业界 LLM-AIOps 共识：安全规则不能静态，要从攻击 pattern 学习。

**目标**：input_sanitizer.py 词典从静态变自演化

**实施思路**：
1. 每次 input_sanitizer 检测到 injection → 记录 `_数据库/.learning/injection_log/`
2. 累积 ≥ 10 条新模式 → 自动 cluster 提取新 pattern
3. 加 confidence 后追加到 INJECTION_PATTERNS（不直接覆盖）
4. 用户审阅后 promote 到主词典

**估算**：2-3 天

## 路线图：L7 协作学习

复用 P6.2 God Log 路线图：

**目标**：分析 agent 调用图 + cascade error pattern + 输出被忽略检测

**实施思路**：
1. 主代理 spawn agent 时记录 metadata 到 `_数据库/.god_log/`
2. agent 完成后记录 output_hash + duration + exit_status
3. cascade_error_detector.py 每周扫：
   - cascade 链路（A → B 错误传播）
   - ignored 输出（A 写文件但下游从未 Read）
   - spawn 失败 pattern

**估算**：1 周

## 周期触发表

| 频率 | 学习器 | 触发位置 |
|---|---|---|
| 每章 | skill_evolver evolve/retire/promote | save-state plan |
| 每章 | evolution_orchestrator | save-state plan（内部判 cycle）|
| 每 10 章 | user_experience_learner | learning_hub --quick |
| 每 20 章 | error_pattern_analyzer | learning_hub |
| 每 30 章 | dead_feature_detector | learning_hub |
| 每 20 章 | high_score_pattern_extractor | learning_hub |
| 用户问时 | learning_hub --status | /learning-status |
| 每 20 章 | meta-prompt-optimizer agent | 主代理 spawn |

## 自动 → 半自动 → 人工 决策树

```
完全自动（系统自决策）:
  skill_evolver evolve / retire （安全）
  high_score_pattern_extractor --update-experience （仅追加 success_patterns）
  user_experience_learner （只读）

半自动（建议给用户）:
  meta-prompt-optimizer （输出建议不直接改 prompt）
  evolution_orchestrator （触发其他工具，不直接改）
  dead_feature_detector （列 deprecation 候选不删）

需用户决策:
  /wizard --update
  /reconcile
  实际删 dead feature
  应用 meta-prompt-optimizer 建议
```

## 学习产物分类

| 产物 | 位置 | 跨项目？ |
|---|---|---|
| 项目级 skill | `_数据库/写作经验.json` | ❌ |
| 项目级 learning 报告 | `_数据库/.learning/` | ❌ |
| 跨项目 patterns | `core/claude-home/universal_skill_pool.json` | ✅ |
| 跨项目元教训 | `MEMORY/` | ✅ |
| 模板 / examples | `core/claude-home/templates/examples/` | ✅ |
