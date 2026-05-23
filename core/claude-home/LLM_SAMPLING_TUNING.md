# LLM Sampling 参数调优（v21 P11.1）

## 背景

业界 2026 数据：85% production LLM 应用用了 ≥3 参数（temperature/top_p/max_tokens），但多数开发者用默认值不调。

创作类任务的甜区：
- **temperature 0.8-1.0**：创意 + 连贯平衡
- **temperature < 0.5**：太保守，输出公式化
- **temperature > 1.5**：失控，gibberish
- **frequency_penalty 0.5-1.0**：防重复词
- **presence_penalty 0.5-1.0**：鼓励主题多样

## 我们 10 个 agent 推荐参数

| Agent | temperature | top_p | frequency_penalty | presence_penalty | 理由 |
|---|---|---|---|---|---|
| **novel-writer** | **0.85** | 0.9 | 0.6 | 0.5 | 创作主力，需创意但要连贯 |
| novel-writer-精雕 | 0.7 | 0.85 | 0.7 | 0.5 | 精雕模式偏保守 |
| novel-writer-脑洞 | 1.0 | 0.95 | 0.4 | 0.7 | 脑洞模式偏发散 |
| novel-validator-repair | 0.3 | 0.85 | 0.2 | 0.2 | 修复需精确，少创意 |
| novel-voice-keeper | 0.4 | 0.85 | 0.3 | 0.2 | 判断对话风格，需稳定 |
| novel-foreshadower | 0.5 | 0.85 | 0.3 | 0.3 | 伏笔评估偏机械 |
| novel-reflector | 0.6 | 0.9 | 0.5 | 0.4 | 反思需小创意 |
| novel-summarizer | 0.3 | 0.85 | 0.4 | 0.2 | 摘要需精确 |
| novel-outline-planner | 0.9 | 0.92 | 0.6 | 0.6 | 走向卡需创意分叉 |
| novel-researcher | 0.4 | 0.85 | 0.3 | 0.4 | 调研需准确 |
| novel-meta-judge | 0.3 | 0.85 | 0.2 | 0.2 | judge 校准需稳定 |
| novel-chapter-splitter | 0.2 | 0.8 | 0.1 | 0.1 | 截断点评分机械 |

## 参数解释

**temperature**：控制 token 抽样的随机度
- 0 = 贪心（每次选最高概率）→ 完全 deterministic
- 0.7-1.0 = 创作甜区
- 2.0 = 完全混沌

**top_p（nucleus sampling）**：累计概率 P% 的 token 池内抽样
- 0.85-0.95 = 创作类标准
- 0.5 以下 = 太收敛

**frequency_penalty**：按 token 出现频次降权（防词重复）
- 0.5-1.0 = 创作类
- > 1.5 = 强行回避，可能伤句法

**presence_penalty**：曾出现过的 token 一次性降权（鼓励新话题）
- 0.5-1.0 = 创作类
- 与 frequency 协同：先抑现有再促新主题

## 实施方式

### 方式 A：用户层配置

`~/.claude/agents/<agent>.md` frontmatter：
```yaml
---
name: novel-writer
model: claude-sonnet-4-6
temperature: 0.85
---
```

### 方式 B：API 直接调用

```python
client.messages.create(
    model="claude-sonnet-4-6",
    temperature=0.85,
    top_p=0.9,
    # Anthropic 暂不直接支持 frequency/presence penalty，需 prompt 提示
)
```

### 方式 C：prompt 内 directive

agent prompt 顶部：
```
SAMPLING_HINT: temperature=0.85, avoid_repetition=high, encourage_diversity=high
```

## 章节类型动态参数

| 章节类型 | 推荐 temperature 微调 |
|---|---|
| Opening Image / Catalyst | +0.1（开场需冲击） |
| Set-Up / Fun and Games | baseline |
| Midpoint / All Is Lost | -0.05（关键节点需精确） |
| Dark Night of Soul | -0.1（情感戏要稳） |
| Finale | -0.05 |
| 闲笔 / 群戏 | +0.05 |

## 参考

- [LLM Temperature 2026 Guide](https://amitray.com/llm-parameters-temperature-top-p-top-k-guide/)
- [Production Tuning Statistics](https://blog.promptlayer.com/temperature-setting-in-llms/)
- [Frequency vs Presence Penalty](https://www.gradually.ai/en/ai-glossary/temperature-top-p-top-k-frequency-penalty-presence-penalty/)
