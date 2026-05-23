# Claude Extended Thinking 启用建议（v21 P8.1）

## 背景

Claude 3.7+ 引入 **Extended Thinking**：dedicated pre-response reasoning budget，可见 "Thinking" 段。
- 简单 prompt → 自动跳过（无额外成本）
- 复杂 prompt → 自动启用，深思后再回答
- 开发者可设 `thinking_budget` 控制

业界数据：复杂创作类任务启用后**质量提升 8-15%**，但 input token +20-40%。

## 哪些 agent 应启用 Extended Thinking

| Agent | 启用场景 | thinking_budget |
|---|---|---|
| **novel-writer** | 关键章节（critical_chapter=true）| 8000 tokens |
| novel-outline-planner | 复杂走向卡（fluid mode + 多 fate_event active）| 4000 tokens |
| novel-meta-judge | judge 间冲突 ≥ 2 | 4000 tokens |
| novel-foreshadower | Tier-1 伏笔到期章 | 2000 tokens |
| 其他 agent（summarizer/reflector/voice-keeper）| 不启用（任务太简单，浪费）| - |

## 启用方式

如果直接调 Anthropic API：
```python
response = client.messages.create(
    model="claude-opus-4-7",
    thinking={"type": "enabled", "budget_tokens": 8000},
    messages=[...]
)
```

如果用 Agent tool（subagent_type=...）：
- 当前 Agent tool 默认按 prompt 复杂度自动启用 thinking
- 主代理可在 spawn prompt 顶部加 `EXTENDED_THINKING: enabled, budget=8000` 标记
- 实际 thinking 由 agent runtime 决定

## 关键章节判定（已与 MODEL_ROUTING.md 复用）

满足任一 → 启用 Extended Thinking：
- chapter_plan.beat 含 `Catalyst / Midpoint / All Is Lost / Break Into Three / Finale`
- mental_break_triggered
- pending_heart_event_reveals 含 reveal
- volume_first_chapter / volume_last_chapter
- 用户明示 "精雕模式"

## 成本-收益分析

| 章节类型 | 占比 | thinking 启用 | 单章成本 | 质量提升 |
|---|---|---|---|---|
| 关键章节 | ~15% | ✅ Extended | +30% | +10-15% |
| 普通章节 | ~85% | ❌ 普通 | baseline | baseline |
| 月总成本 | | | **+4.5%** | 关键章节质量 ↑ |

## 与 model routing 协同

- 关键章节 = Opus + Extended Thinking
- 普通章节 = Sonnet + 无 Extended
- 简单 agent = Haiku + 无 Extended

## 参考

- [Claude Extended Thinking](https://www.anthropic.com/news/visible-extended-thinking)
- [Claude 2026 features overview](https://suprmind.ai/hub/claude/features/)
