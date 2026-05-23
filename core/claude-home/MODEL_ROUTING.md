# Agent Model Routing 矩阵（v21 P7.1）

## 背景

业界 2026 共识：multi-agent 系统按 agent 类型分配 model tier，**省 40-50% 总成本**。
- Router/Worker 用 Haiku
- Expert（生成/判断关键章节）用 Sonnet
- 极少特殊场景用 Opus

## 我们 10 个 agent × 推荐 model tier

| Agent | 推荐 Model | 原因 |
|---|---|---|
| **novel-writer** | Sonnet 4.6 / Opus 4.7（关键章节） | 创意生成质量直接决定章节品质，必须 flagship |
| novel-validator-repair | Sonnet 4.6 | 局部精修，需要理解上下文 |
| novel-voice-keeper | Sonnet 4.6 | 对话声纹判断需要细粒度 |
| novel-foreshadower | Haiku 4.5 | 伏笔评估偏机械（due_by 检查 + 关键词），cheap 够用 |
| novel-reflector | Haiku 4.5 | 反思 entry ≤ 800 token，cheap 够 |
| novel-summarizer | Haiku 4.5 | 200 字摘要，cheap 够 |
| novel-outline-planner | Sonnet 4.6 | 走向卡决策需结合多 manifest 字段做创意权衡 |
| novel-researcher | Haiku 4.5 | WebSearch + 整理调研结果，cheap 够 |
| novel-meta-judge | Sonnet 4.6 | judge 间一致性分析需要 reasoning |
| novel-chapter-splitter | Haiku 4.5 | DCAS 截断点评分，机械可 cheap |

## 预估收益

| 场景 | 老配置（全 Sonnet） | 新配置（routed） | 省 |
|---|---|---|---|
| 每章 8 agent spawn | 8 × Sonnet | writer/validator/voice-keeper/outline-planner/meta-judge=Sonnet (5) + foreshadower/reflector/summarizer=Haiku (3) | **~30%** |
| 月成本（30 章）| baseline | 70% | **30% 节省** |
| 关键章节 | + 1 Opus writer | × 5% 章节 | 总体 +2% 成本 / 关键质量 ↑ |

## 实施方式

### 方式 A：用户层手动切换

用户开始项目时配置 `~/.claude/agents/<agent>.md` 顶部 `model:` 字段：
```yaml
---
name: novel-summarizer
model: claude-haiku-4-5-20251001
---
```

### 方式 B：动态 routing（高级）

主代理 spawn agent 时根据 chapter 重要度动态选 model：
```python
spawn_model = "opus" if is_critical_chapter else "sonnet"
spawn(name="novel-writer", model=spawn_model, ...)
```

## 关键章节判定（用 Opus 4.7）

满足以下任一即用 Opus（成本 ↑3-5x，但质量提升明显）：
- chapter_plan.beat 含 `Catalyst / Midpoint / All Is Lost / Break Into Three / Finale`
- mental_break_triggered
- pending_heart_event_reveals 含 reveal
- volume_first_chapter（卷首章）
- volume_last_chapter（卷末章）
- 用户明示 "精雕模式"

## 不推荐 Haiku 的场景

| Agent | 为什么不用 Haiku |
|---|---|
| novel-writer | 创意/文笔质量直接相关，Haiku 比 Sonnet 平均下降 10-15%（业界 benchmark）|
| novel-outline-planner | 走向卡决策结合 12 项 manifest 字段做创意权衡，需 reasoning |
| novel-meta-judge | judge 间一致性 + 漂移检测需要中级 reasoning |

## 多 provider fallback（可选）

如果用户想脱离 Anthropic：
- writer: Qwen 3 / DeepSeek V3.2 (中文场景 competitive)
- summarizer/reflector: 本地 Llama 3 / GLM-4-Flash
- 经济收益：相比纯 Anthropic 再省 60-80%

## 参考

- [AI Agent Cost Optimization 80% Guide 2026](https://paxrel.com/blog-ai-agent-cost-optimization)
- [Model Routing 40-50% Savings](https://www.mindstudio.ai/blog/what-is-ai-model-router-optimize-cost-llm-providers)
- [Llama 3.3 70B vs Sonnet pricing](https://www.clarifai.com/blog/top-cost-efficient-small-models)
