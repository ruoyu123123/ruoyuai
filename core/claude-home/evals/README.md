# Plan 命令 Evals（P1-2，借鉴 skill-creator 2.0）

## 目的

6 个 plan 强制命令的**行为契约**文档化 —— 每个命令的代表性用户场景 + 应触发的
plan 步骤 + 应产出的 artifacts。

## 与既有体系的关系

- **plan_tracker**：定义「步数 / required_steps / expected_outputs」（执行期硬约束）
- **evals**（本目录）：定义「用户场景 → 应跑通的端到端 trace」（场景期 spec）

两者互补：plan_tracker 保证「步必须走完」，evals 保证「常见用户场景下确实走通了」。

## 文件结构

```
core/claude-home/evals/
├── README.md                  # 本文件
├── save-state.evals.json
├── distill-style.evals.json
├── check-quality.evals.json
├── write-chapter.evals.json
├── outline.evals.json
└── reconcile.evals.json
```

## evals.json schema

```json
{
  "command": "check-quality",
  "version": 1,
  "evals": [
    {
      "id": "qa-1",
      "scenario": "1-2 句话描述用户现场",
      "user_prompt": "用户实际会输入的 prompt 范例",
      "fixture": {
        "project": "...",
        "chapter": 5,
        "prereqs": ["第5章.txt 已写", "..."]
      },
      "expected_trace": [
        "plan_tracker create check-quality",
        "anti_slop_scan 跑（含语义层）",
        "LLM 评估给评级 A/B/C/D",
        "plan-end 成功 verdict=pass|waived"
      ],
      "expected_artifacts": [
        "_数据库/.qa/第5章_antislop.json",
        "_数据库/.qa/第5章_llm_eval.json"
      ],
      "tolerance": {
        "advisory_waivable": true,
        "hard_gate_blocking": true
      }
    }
  ]
}
```

## 字段说明

| 字段 | 含义 |
|---|---|
| `id` | eval 唯一标识（命令内唯一） |
| `scenario` | 用户场景简述 |
| `user_prompt` | 用户的真实 prompt 范例（含具体命令调用） |
| `fixture` | 跑此 eval 所需的前置环境（project / chapter / 已存在的 artifacts） |
| `expected_trace` | 应触发的 plan 步骤序列（人类可读） |
| `expected_artifacts` | 应产出的文件路径（相对 project_root） |
| `tolerance` | 允许的偏差（如 advisory 可豁免、hard_gate 必拦） |

## 自动化运行

本目录的 evals 目前是**纸面契约**（spec docs）。后续可加 `core/scripts/eval_runner.py`
自动跑：基于 fixture 准备环境 → 模拟 user_prompt → 跑命令 → 检查 plan_tracker.end
ok + expected_artifacts 都存在 → 报告 pass/fail。

当前不强制自动化 —— 这些文档本身就是「跑通常见场景必须满足」的 ground truth，
人工 review 与 plan_tracker 模板演进同步即可。

## 维护纪律

- **plan 模板改 → evals 同步审一遍**：模板加新 step / 改 expected_outputs，
  对应命令的 evals 至少 1 个 entry 要更新 `expected_trace` / `expected_artifacts`
- **新增命令进入 plan 强制规划层 → 必须添 evals 文件**
- 单文件 evals ≤ 5 个 entry（代表性场景，不追求穷举）
