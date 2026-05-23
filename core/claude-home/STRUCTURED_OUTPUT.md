# Structured Output（_changes.json schema）应用指南（v21 P9.1）

## 背景

Anthropic Structured Output **2025.11 beta / 2026 GA**：通过 constrained decoding 在 token 生成阶段约束，**模型无法生成不符合 JSON schema 的 token**——「transport-level guarantee」。

我们 writer 输出 `_changes.json` 常有：
- 字段缺失（fate_events_triggered 写漏）
- 类型错（数值写成字符串）
- enum 不一致（ending_type 大小写不规范）

→ save-state apply 失败 / cross_chapter scan 找不到字段

## 解

定义 `core/claude-home/schemas/changes_schema.json`，writer 输出前用 constrained decoding 强制。

## 已生成 schema

`core/claude-home/schemas/changes_schema.json` 含：
- `factual.locked_facts` / `relationships` / `items` / `fate_events_triggered` / `foreshadowing_paid` / `world_state_consumption` / `clocks_addressed` / `aspects_addressed` / `heart_events_revealed` / `throughline_progress` / `fate_dice_consumed` / `chapter_hub`
- `self_eval.applied_style` / `waivers` / `moves_used` / `position_effect_evals` / `stress_evaluation_self` / `storyteller_alignment` / `mckee_truby_alignment`

全面覆盖 v21 R1-R3 + CCR 所有 schema 字段。

## 使用方式

### 方式 A：通过 Anthropic API（直接调用）

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    response_format={
        "type": "json_schema",
        "schema": json.load(open("core/claude-home/schemas/changes_schema.json"))
    },
    messages=[...]
)
# response.content[0].text 一定符合 schema
```

### 方式 B：通过 Agent tool（推荐）

writer agent prompt 顶部加：

```markdown
## 输出 schema 强制（v21 P9.1）

你的 `_changes.json` 输出 **必须** 符合以下 JSON Schema：
[内嵌 changes_schema.json 内容]

任何不符 schema 的字段会被 save-state 拒绝 + 触发 audit error。
所有 _changes.json 必跑 `python -m jsonschema -i 第NNN章_changes.json core/claude-home/schemas/changes_schema.json` 校验。
```

### 方式 C：post-hoc 校验（最低门槛）

save-state step 3 加：
```bash
python -m jsonschema -i 第NNN章_changes.json core/claude-home/schemas/changes_schema.json
```
失败 → 触发 writer 重写或人工修。

## 注意

> Structured Output 是 **transport-level guarantee**，不是 **correctness guarantee**：syntactically valid JSON 仍可含错误内容（如 fate_event_id 引用不存在的 ME_999）。
>
> 仍需语义校验（已有 audit_hub / cross_chapter scanners）。

## 实施 checklist

- [x] Phase 1: changes_schema.json 已生成
- [ ] Phase 2: 加 post-hoc validator 到 save-state step 3
- [ ] Phase 3: writer prompt 内嵌 schema（让 LLM 知道约束）
- [ ] Phase 4: 接入 Anthropic API 真正的 constrained decoding（需直接调 SDK 而非 Agent tool）

## 参考

- [Anthropic Structured Output (2026 GA)](https://docs.anthropic.com/en/docs/build-with-claude/structured-outputs)
- [Structured Generation 2026 Production Guide](https://tianpan.co/blog/2026-03-03-structured-generation-reliable-llm-output)
- [JSON Schema validation in LLM pipelines](https://collinwilkins.com/articles/structured-output)
