# God Log + Cascade Error Detection 路线图（v21 P6.2）

## 背景

业界 2026 共识（MAST taxonomy）：multi-agent 失败 14 模式：
- **specification 41.8%** — agent 任务定义不清
- **inter-agent misalignment 36.9%** — agent 间消费契约错位
- **verification failures 21.3%** — 缺少 agent 间验证

OWASP ASI08（Cascading Failure in Agentic AI）2026 新增——agent 不验证上游就消费 → 错误层层放大。

## 我们现状

- ✅ `plan_tracker` 记录 step 状态 + attestation
- ✅ `judge_consensus` 多 agent 验证
- ✅ agent prompt 含 PLAN_ID / STEP / MANIFEST 契约字段
- ❌ **agent spawn 调用 metadata 不完整**（无 input hash / output hash / duration / 调用图）
- ❌ **cascade entry point 未识别**（哪些 agent 输出最易被下游不验证消费？）
- ❌ **God Log 缺失**——出错时无法回溯到精确触发点

## God Log 设计

每次主代理 spawn agent，记录：
```json
{
  "timestamp": "2026-05-16T14:00:00",
  "session_id": "<session>",
  "spawn_id": "agent_spawn_<uuid>",
  "agent_type": "novel-writer",
  "parent_action": "save-state step 11",
  "plan_id": "<plan>",
  "step": 11,
  "input": {
    "prompt_hash": "<sha256>",
    "prompt_token_estimate": 15000,
    "manifest_ref": "_数据库/.manifest/ch_005_compressed.json",
    "manifest_hash": "<sha256>",
    "external_inputs": ["走向卡 user_choice=B", "PLANNER_CONTEXT"]
  },
  "execution": {
    "started_at": "...",
    "completed_at": "...",
    "duration_ms": 45000,
    "exit_status": "ok | error | timeout",
    "tool_calls_count": 12,
    "tools_used": ["Read", "Write", "Bash"]
  },
  "output": {
    "output_hash": "<sha256>",
    "output_files_written": ["第005章.txt", "第005章_changes.json"],
    "self_eval_waivers": 2,
    "judge_health_warnings": 0,
    "reasoning_trace_summary": "...500 字..."
  },
  "downstream_consumers": ["audit_hub", "novel-validator-repair"]
}
```

写入 `_数据库/.god_log/<date>/spawn_<spawn_id>.json`。

## Cascade Entry Point 识别

每个 agent 列出**其输出最易被下游不验证消费的 3 项**：

```
novel-writer:
  - 第NNN章.txt → 被 validator-repair / voice-keeper 消费
    cascade_risk: HIGH（validator 不重读全文，只信 changes）
  - _changes.json.factual.locked_facts → 被 save-state 应用到 34 子系统 JSON
    cascade_risk: HIGH（save-state 不验证 locked_facts 合理性，直接 merge）
  - _changes.json.self_eval.waivers → 被 audit_hub 接受为放行
    cascade_risk: MEDIUM（audit_hub 检查 hard_gate 不可豁免，但 advisory 全收）

novel-summarizer:
  - 故事块摘要 → 被 outline-planner / RAG 消费
    cascade_risk: HIGH（无人验证摘要 vs 正文是否一致 → 已被 CCR15 cover）
```

## Cascade Error Detector 实施

```python
# core/scripts/cascade_error_detector.py
def detect():
    # 1. 读 God Log 最近 N spawn
    # 2. 构建 spawn DAG（parent → children）
    # 3. 对每条 edge 检查：
    #    a. downstream agent 是否 Read 了 upstream 输出？
    #    b. downstream 是否对 upstream 关键字段做了 schema/range 验证？
    #    c. downstream 是否引用了 upstream 输出原文？
    # 4. 缺失验证 → 标 CASCADE_RISK
```

## 实施 checklist

- [ ] Phase 1: God Log 写入（主代理 spawn 时 hook）
- [ ] Phase 2: cascade_entry_points.json 清单
- [ ] Phase 3: cascade_error_detector.py 跨章扫
- [ ] Phase 4: agent prompt 加 "verify upstream" 纪律

## 紧迫度

- 当前章数 < 50 → 低紧迫
- 章数 > 100 + 多 agent 失败 → 必须实施
- production 部署 → 必须

## 参考

- [MAST Taxonomy 2026](https://www.augmentcode.com/guides/why-multi-agent-llm-systems-fail-and-how-to-fix-them)
- [OWASP ASI08 Cascading Failure 2026](https://adversa.ai/blog/cascading-failures-in-agentic-ai-complete-owasp-asi08-security-guide-2026/)
- [Multi-Agent Production Failure Playbook](https://cogentinfo.com/resources/when-ai-agents-collide-multi-agent-orchestration-failure-playbook-for-2026)
