# 自学习 / 自演化系统全套路线图（v22）

## 架构总览

借鉴业界 2026 自演化 agent 共识，本系统采用 **4 层自学习架构**：

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 4: Cross-Project Transfer Learning                     │
│  universal_skill_pool.json（跨项目）                          │
│  ↑ skill_evolver.promote()                                    │
├──────────────────────────────────────────────────────────────┤
│  Layer 3: Multi-Agent Co-Evolution（三角共演化）              │
│  evolution_orchestrator → meta-prompt-optimizer agent         │
│  每 10 章触发，分析 Proposer/Solver/Judge 三角                │
├──────────────────────────────────────────────────────────────┤
│  Layer 2: Skill Evolution（数据层演化）                       │
│  skill_evolver.py（evolve/promote/retire/dashboard）          │
│  写作经验.json 升级为 versioned skill artifacts                │
├──────────────────────────────────────────────────────────────┤
│  Layer 1: ERL Heuristics Retrieval（检索注入）                │
│  build_manifest._collect_relevant_heuristics(top_k=5)         │
│  按 context 检索 top-N，避免 context rot                       │
└──────────────────────────────────────────────────────────────┘
```

## v22 已落地（5 项 SE1-SE5）

### SE1: 写作经验 versioned evolution
- `core/scripts/skill_evolver.py`：evolve/promote/retire/dashboard
- 写作经验.json schema 升级：version / evolution_history / usage_count / last_validated_at / confidence / status

### SE2: ERL Heuristics 检索
- `build_manifest._collect_relevant_heuristics()` 按 context 检索 top-5
- writer manifest 字段 `relevant_heuristics`

### SE3: Meta-Prompt Optimizer agent
- `.claude/agents/novel-meta-prompt-optimizer.md`
- 每 20 章扫 judge_reports + 失败 patterns
- 输出 prompt 改进建议（不直接改 prompt，需用户审）

### SE4: 三角共演化 orchestrator
- `core/scripts/evolution_orchestrator.py` 每 10 章触发
- 分析 Proposer/Solver/Judge 三角
- 自动 cascade 触发 skill_evolver + 建议 meta-prompt-optimizer

### SE5: Universal Skill Pool（跨项目迁移）
- `core/claude-home/universal_skill_pool.json`
- skill_evolver.promote() 把高 usage + 高 confidence pattern 自动升级

## v22 路线图（SE6 文档化）

### SE6: Self-Rewarding + DPO 数据采集

业界 arxiv 2401.10020 思路：LLM 自评 + DPO 训自己

**我们能做的（不需要 fine-tune）**：

#### Phase 1: 数据采集 — 持续累积 preference pairs
- 用户走向卡选择 = explicit preference signal
- judge_consensus 评分差 = implicit quality signal
- 写入 `core/claude-home/dpo_training_data/<project>_<date>.jsonl`

#### Phase 2: prompt-level alignment（不 fine-tune）
- 每 100 章统计 preference pattern
- 转化为 prompt directive
- 不动 base model

#### Phase 3: 真正 fine-tune（高级，路线图）
- 累积 ≥ 5000 条 preference pairs
- 用 DPO 训 Qwen 3 / Llama 3 做 voice keeper
- **不在 Agent tool 范畴**：需要 GPU + 训练框架

### SE7: Recursive Self-Improvement 防失控（重要）

**风险**：meta-prompt-optimizer 自动改 prompt → 改坏 → 越改越差

**已有防御**：
- ✅ meta-prompt-optimizer 设计为「只建议不改」
- ✅ skill_evolver promote 需 ≥5 usage + ≥0.8 confidence
- ✅ git auto commit 保留所有 prompt 历史

**待加防御**：
- ❌ chapter regression suite（每次 evolution 跑 N 个 gold chapter 看分数）
- ❌ 一次只改 1 个 agent prompt（避免连锁失败）

## 集成 save-state plan

```bash
# v22 SE: 每章 save-state step 9 末尾加
python core/scripts/skill_evolver.py {project_root} evolve --ch {ch}

# 每 10 章触发 orchestrator
[ $({ch} % 10) -eq 0 ] && python core/scripts/evolution_orchestrator.py {project_root} --ch {ch}

# 每 20 章主代理 spawn meta-prompt-optimizer agent
```

## 与既有系统的关系

| 既有 | v22 SE 升级 |
|---|---|
| learning_loop | 仍存，喂数据给 skill_evolver |
| reflector agent | 仍跑，输出进 versioned skill artifacts |
| meta-judge | 与 evolution_orchestrator 协同 |
| tool_calibration_suggestions | skill_evolver 周期性产出 |
| MEMORY 跨项目教训 | 与 universal_skill_pool 互补（元教训 vs 可执行 pattern）|

## 关键设计决策

### 1. 「演化」≠「重训」
- 不动 base model weights
- 演化的是：skill artifacts（数据）+ prompt suggestions（文本）+ heuristics 检索（注入）
- 业界叫 **SKILL-based evolution**

### 2. 「单向 → 三角」反馈
- 旧：outline-planner → writer → judge（单向）
- v22：三角互相校准
- 业界叫 **Multi-Agent Evolve**

### 3. 「项目内 → 跨项目」迁移
- 旧：每项目独立
- v22：universal_skill_pool 跨项目共享
- 业界叫 **Cross-Task Experience Learning**

## 参考

- [AutoSkill arxiv 2603.01145](https://arxiv.org/abs/2603.01145)
- [Multi-Agent Evolve arxiv 2510.23595](https://arxiv.org/html/2510.23595v1)
- [Experiential Reflective Learning 2603.24639](https://arxiv.org/abs/2603.24639)
- [Self-Rewarding LM arxiv 2401.10020](https://arxiv.org/pdf/2401.10020)
- [OpenAI Self-Evolving Agents Cookbook](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining)
- [EvoAgentX](https://github.com/EvoAgentX/EvoAgentX)
- [ICLR 2026 Recursive Self-Improvement Workshop](https://openreview.net/pdf?id=OsPQ6zTQXV)
