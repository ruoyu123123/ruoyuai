# Anthropic Prompt Caching 策略（v21 P1）

## 为什么

每章 spawn 8-10 个 agent，每个 prompt 含：
- 系统指令（不变）
- 蒸馏参考 voice_pack/few-shot/cliche 词典（卷间不变）
- manifest JSON 50KB（每章变 30%，70% 不变）
- 本章具体任务（每章新）

如果按"自然顺序"写 prompt，Anthropic API 无法 detect 公共 prefix → 每次都付 100% token 钱。

**正确做法**：按 cache 友好顺序排放（最静态在最前），让 Anthropic 自动 detect 最长 prefix → 命中 cache → **省 60-90% token 成本**。

## Agent prompt 推荐结构

```
═══════════════════════════════════════════════════════
SECTION A: 系统指令          [100% cacheable]
═══════════════════════════════════════════════════════
- 角色身份（你是写作子代理）
- 工具约束（必须用 Write 工具）
- 输出格式（_changes.json schema）
↑↑↑ 跨章/跨项目都不变 ↑↑↑

═══════════════════════════════════════════════════════
SECTION B: STATIC 字段       [99% cacheable]
═══════════════════════════════════════════════════════
- manifest._cache_layout
- manifest.distill_continuity_template
- manifest.distill_voice_packs_reference
- manifest.distill_golden_few_shot
- manifest.position_effect_template
↑↑↑ 跨章几乎不变（仅 distill 重做时变）↑↑↑

═══════════════════════════════════════════════════════
SECTION C: SEMI_STATIC 字段  [70-80% cacheable]
═══════════════════════════════════════════════════════
- manifest.active_fate_events
- manifest.world_state_snapshot
- manifest.active_aspects
- manifest.throughlines
- manifest.ensemble_layer
- manifest.hub_directive
- manifest.character_moves
- manifest.reader_preferences
- manifest.active_relationships
- manifest.faction_standings_snapshot
↑↑↑ 卷内不变，卷间慢变 ↑↑↑

═══════════════════════════════════════════════════════
SECTION D: DYNAMIC 字段      [30% cacheable]
═══════════════════════════════════════════════════════
- manifest.chapter
- manifest.must_read
- manifest.active_clocks
- manifest.storyteller_directive
- manifest.protagonist_stress
- manifest.fate_dice_hint
- manifest.active_offscreen_actions
- manifest.selective_history_retrieval
- manifest.prev_judge_findings
- manifest.will_learn_due_this_ch
- manifest.pending_secrets_to_reveal
- manifest.hard_constraints
- manifest.post_write_checks
↑↑↑ 本章 specific ↑↑↑

═══════════════════════════════════════════════════════
SECTION E: 本章具体任务      [0% cacheable]
═══════════════════════════════════════════════════════
- "请写第 N 章"
- "按上面 manifest 执行"
- manifest._critical_summary  ← 头尾双放（LiM 缓解）
═══════════════════════════════════════════════════════
```

## 主代理 spawn agent 时的 prompt 模板

```
你是 [<agent role>]（SECTION A · 缓存友好）

PROJECT: <path>
CHAPTER: <N>
PLAN_ID: <id>
STEP: <step>

═══ STATIC 参考（跨章稳定，cache 优先）═══
[此处 Read manifest 并按 _cache_layout.STATIC_99_cacheable 顺序输出]

═══ SEMI_STATIC 参考（卷内稳定）═══
[按 _cache_layout.SEMI_STATIC_70_cacheable 顺序输出]

═══ DYNAMIC 输入（本章 specific）═══
[按 _cache_layout.DYNAMIC_30_cacheable 顺序输出]

═══ 本章任务 ═══
请按 manifest._critical_summary 的 critical_reminders 严格执行。
具体任务：[<task description>]
```

## 哪些 agent 应消费 _cache_layout

| Agent | 受益 | 优先级 |
|---|---|---|
| novel-writer | **极高**（prompt 最长 60KB+）| P0 必改造 |
| novel-validator-repair | 高（多次循环）| P0 |
| novel-voice-keeper | 中（每章 1 次）| P1 |
| novel-foreshadower | 中（每章 1 次）| P1 |
| novel-outline-planner | 中（每章 1 次，含 PLANNER_CONTEXT）| P1 |
| novel-reflector | 低（轻量）| P2 |
| novel-summarizer | 低（轻量）| P2 |

## LiM（Lost-in-the-Middle）缓解

业界研究：长 prompt 中间字段会被 LLM 忽略（Lost-in-the-Middle 现象）。

**头尾双放策略**：
- **头部**：`must_read` 中 P0 文件详细列出
- **末尾**：`_critical_summary.critical_reminders` 再次强调（关键词级）

agent prompt 末尾必须附 `manifest._critical_summary` —— 即使 LLM 忽略中间部分，头尾必看。

## 量化效果预估

| 配置 | 每章 input token | 月成本（30 章）|
|---|---|---|
| 老配置（自然顺序，无 cache）| ~60K × 10 agent = 600K | 高基线 |
| **新配置（cache layout）** | STATIC+SEMI 部分缓存命中 → **600K × 30% = 180K 实际付费** | **省 70%** |

> Anthropic 2026 数据：multi-step agent 系统 prompt caching 平均收益 60-90%

## 实施 checklist

- [x] build_manifest.py 输出 `_cache_layout` 字段（v21 P1）
- [x] build_manifest.py 输出 `_critical_summary` 字段（v21 P2-3 LiM）
- [ ] novel-writer.md prompt 顶部加 cache layout 消费说明
- [ ] novel-validator-repair.md 同样改造
- [ ] novel-outline-planner.md 同样改造
- [ ] 文档主代理 spawn prompt 模板按 SECTION A-E 顺序

## 调用 Anthropic API 时（高级）

如果使用 Anthropic SDK 直接调用（而非 Agent tool），可手动加 cache_control：

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "<SECTION A: system instructions>",
                "cache_control": {"type": "ephemeral"}  # 标记为可缓存
            },
            {
                "type": "text",
                "text": "<SECTION B: STATIC reference>",
                "cache_control": {"type": "ephemeral"}
            },
            {
                "type": "text",
                "text": "<SECTION C: SEMI_STATIC ref>"
                # 不加 cache_control，但前 2 段已被缓存
            },
            {
                "type": "text",
                "text": "<SECTION D: DYNAMIC + 本章任务>"
            }
        ]
    }
]
```

Agent tool 使用时无需手动加 cache_control，按 SECTION A-E 顺序排放即可。
