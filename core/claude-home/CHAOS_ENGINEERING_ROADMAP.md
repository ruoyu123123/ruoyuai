# Chaos Engineering 弹性测试路线图（v22.5 L11）

## 背景

业界 arxiv 2511.07865 ChaosEater + arxiv 2505.03096 LLM-MAS 弹性研究：

LLM-MAS（多 agent 系统）必须做 chaos engineering：
- **不等 bug 发生** 而是主动注入故障
- **看系统反应** → 评弹性分
- **修复** 加防御

## 我们能注入的故障

### A. 数据层故障
- manifest 字段缺失（删 active_aspects 看 writer 怎么反应）
- 数据库 JSON 损坏（无效 JSON）
- 路径不存在（章节文件被删）
- 历史章节缺失

### B. agent 调用故障
- spawn agent 超时
- agent 返回错误格式 JSON
- agent 输出超长 / 超短

### C. 语义对抗（semantic fault injection）
- manifest 注入冲突约束（fate_event A 必触发 + clock A 必推迟）
- prev_judge_findings 与 user_preferences 矛盾
- 走向卡的 character_driven 与 active_aspects 冲突

### D. 资源故障
- token budget 超限
- API rate limit
- 网络不通（research 失败）

## ChaosEater 风格自动化

```python
chaos_scenarios = [
    {"id": "manifest_field_missing", "inject": "delete active_aspects", "expected_recovery": "writer 应 graceful skip 该 step"},
    {"id": "judge_invalid_json", "inject": "judge 输出乱码", "expected_recovery": "audit_hub 应记录 error 但不打断主流水线"},
    {"id": "conflicting_constraints", "inject": "fate_event 必触发 + clock 必推迟", "expected_recovery": "writer 应 waive 一项并说明理由"},
    ...
]
```

每个 scenario：
1. **注入**：临时改 manifest / 删文件 / 注入冲突
2. **运行**：spawn 受影响 agent
3. **评估**：实际行为 vs expected_recovery
4. **打分**：resilience_score

## 实施分阶段

### Phase 1（基础，可立即做）— 静态 chaos test
- 写 chaos_test_runner.py
- 5-10 个预定义 scenarios
- 每周末跑一次
- 输出 resilience_report.json

### Phase 2（中期）— LLM 驱动 chaos
- 让 LLM 生成新 scenarios（参考 ChaosEater）
- 自动 cluster failed scenarios → 发现弹性盲区

### Phase 3（长期）— 自动修复
- chaos test 失败 → 自动生成防御代码建议
- 类似 P6.2 God Log + cascade_error_detector 联动

## 与现有系统协同

- `regression_test_learner` 处理「真实历史失败」
- `chaos_test_runner` 处理「假设性未来失败」
- 两者互补：前者防退化 / 后者防新风险

## 紧迫度评估

| 场景 | 是否必要 |
|---|---|
| 项目 < 30 章 | 不必（数据少不出问题）|
| 项目 30-100 章 | 路线图（开始有 cascade 风险）|
| 项目 > 100 章 / production | **必要**（agent 系统复杂度高，主动 chaos 必备）|

## 参考

- [ChaosEater arxiv 2511.07865](https://arxiv.org/abs/2511.07865)
- [LLM-MAS Robustness arxiv 2505.03096](https://arxiv.org/pdf/2505.03096)
- [Building Resilience Into Agents](https://www.cloudnativedeepdive.com/building-resilience-into-agents-and-llms/)
