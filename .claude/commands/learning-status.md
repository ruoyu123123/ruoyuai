---
description: 系统学习状态全景（v22.5 L9）
---

# 🧠 学习状态全景

$ARGUMENTS

## 何时调用

- 用户问「系统最近学到了什么」「我用得舒服吗」
- 多日没用回归后想看系统学习进展
- 准备 /wizard 重新校准前看看实际偏好

## 执行

```bash
# 查看现状（不重跑）
python core/scripts/learning_hub.py <project> --status

# 重新跑全部学习器
python core/scripts/learning_hub.py <project>

# 仅跑快学习器
python core/scripts/learning_hub.py <project> --quick
```

## 输出含

### L1+L6 用户行为 + 痛点
- 走向卡 A/B/C 选择分布
- 平均章节间隔（推测写作时段）
- 痛点信号（重复 reconcile / 高 retry / wizard 偏离）

### L2 错误聚类
- 累积 error 总数
- unique error codes
- Top 3 高频错误模式（priority_score 排序）

### L3+L4 死功能 + manifest 字段消费率
- 死 ripple_rules / fate_events / mental_break 卡 / manifest 字段
- 建议 deprecation 清单

### L8 高分章节共性
- ≥ 阈值章节数 + 提取的句法/节奏 pattern
- 自动追加到 写作经验.success_patterns

### SE1+SE4 自演化层
- skill_evolver evolve/retire/promote 状态
- 三角共演化报告

## 学习闭环可视化

```
用户行为 → user_experience_learner → 发现痛点
   ↓
错误日志 → error_pattern_analyzer → 高频问题 cluster
   ↓
工具使用 → dead_feature_detector → 死功能标记
   ↓
高分章节 → high_score_pattern_extractor → 追加成功 pattern
   ↓
skill_evolver → versioned skill artifacts
   ↓
evolution_orchestrator → 三角校准
   ↓
meta-prompt-optimizer → prompt 改进建议（用户审）
   ↓
universal_skill_pool → 跨项目沉淀
```

## 触发频率建议

| 学习器 | 频率 | 谁触发 |
|---|---|---|
| skill_evolver | 每章 | save-state plan 自动 |
| evolution_orchestrator | 每 10 章 | save-state plan 自动 |
| user_experience_learner | 每 10 章 / 用户问时 | learning_hub |
| error_pattern_analyzer | 每 20 章 | learning_hub |
| dead_feature_detector | 每 30 章 | learning_hub |
| high_score_pattern_extractor | 每 20 章 | learning_hub |
| meta-prompt-optimizer | 每 20 章 | 主代理 spawn |

## 输出后建议

如发现：
- ⚠️ 痛点 ≥ 3 → 提示用户跑 /wizard 重新校准
- ⚠️ 死功能 ≥ 5 → 建议 reconcile 删 / 改
- ⚠️ 错误 top cluster 大 → 触发 meta-prompt-optimizer
- ⚠️ 高分章节 pattern 提取 → 写作经验已自动追加
