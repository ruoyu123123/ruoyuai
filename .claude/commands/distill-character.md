---
description: 从已写故事块蒸馏角色 Voice DNA，并用 Claude 草稿 + gemini 润色生成 voice_pack。
---

# /distill-character

$ARGUMENTS

## Plan

```bash
python core/scripts/plan_tracker.py create --command distill-character --project "<小说项目路径>" --key "<角色 id>"
```

步骤以 `core/claude-home/plans/distill-character.plan.json` 为唯一来源，7 步全部 required。

## 流程

1. 读取人物卡与故事块摘要，定位角色出场 cluster；读取对应 cluster 草稿与 archive，提取对白、动作和内心活动。每条写 `from_cluster`，`material.json` 写 `appearance_clusters`，至少覆盖两个不同故事块。
2. 分析五层 Voice DNA：边界、身份、语言指纹、情感模式、行为与认知规则。该档是创作顾问，不机械覆盖模型判断。
3. spawn `novel-replica-writer`，模式为 `voice-sample-draft`。Agent 按 material 与 Voice DNA 亲笔写 `sample_*.txt`，同时写 `agent_report.json`。
4. `voice_sample_polisher.py` 逐样本调用 gemini，只润色 `text`，保持 kind、语义和 `from_clusters`，生成 `voice_samples.json`。
5. `voice_pack_merger.py` 确定性合并 Voice DNA 与样本到人物卡，并写 merge receipt。
6. `distill_character_verify.py --strict` 硬校验同栈 provenance、样本非空和每条至少两个来源 cluster；声音相似度只作 advisory。
7. `git_snapshot.py` 写 required Git marker。

## Replica writer 输入

```text
PLAN_ID: <plan id>
STEP: 3
PROJECT: <小说项目路径>
MODE: voice-sample-draft
CHARACTER_ID: <角色 id>
MATERIAL_PATH: <material.json>
VOICE_DNA_PATH: <voice_dna.json>
OUTPUT_DIR: <Claude 草稿目录>
```

每个 `sample_*.txt` 只含一个 JSON object：

```json
{
  "kind": "style",
  "text": "角色样本",
  "from_clusters": ["cluster_003", "cluster_007"],
  "dim": "语言维度"
}
```

anti 样本用 `kind: "anti"` 并写 `violates`。style 与 anti 至少各一条。Agent 不得写最终 `voice_samples.json`。

## 验收

- `material.json` 与 Voice DNA 存在且角色唯一。
- Agent 回执符合 `novel-replica-writer.receipt.v1`，PLAN_ID/STEP 与本 plan 一致。
- `voice_samples.json` 记录 Claude 草稿 + gemini 润色 provenance。
- 人物卡 voice_pack、merge receipt、strict verify 报告和 Git marker 全部存在。

任何 required 输入、Agent 回执、模型调用或报告缺失都必须停止，不得用空样本或旧字段继续。
