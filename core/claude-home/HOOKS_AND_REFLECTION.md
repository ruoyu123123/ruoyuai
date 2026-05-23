# v22.gov 三段式纪律 + Hooks 防御层

**适用范围**：所有 plan_tracker 管控的 6 个命令（save-state / distill-style / check-quality / write-chapter / outline / reconcile），以及未来新增的多步命令。

## 核心原则

> 每个 plan-step 必须遵守「**研 → 干 → 反思**」三段式。
> 没有研究不能干，没有反思不算完。

---

## 三段式定义

### 1️⃣ 研（Research，必须在 step 前）

**目的**：避免「我以为」。每个有方向性判断的 step，开干前必须有 `.research_cache/<topic>.md` 调研产物。

**触发方式**：
- 主代理在 spawn 任何蒸馏/写作/审核 Agent 前，**必须**先 spawn `novel-researcher` 写 research_cache
- 调研类型按场景：业界数据 → 联网 WebSearch / 实地数据 → Grep/Read 项目文件 / 用户偏好 → AskUserQuestion

**hook 防御**：
- `core/claude-home/hooks/pretooluse_step_research.py`
- 触发条件：Bash 命令含 `plan_tracker.py step ... --n N`
- 检测 plan JSON 中 `steps[i].research_ref` 字段对应的文件是否存在
- 文件不存在 → exit 2 拦截 + 提示「请先 spawn novel-researcher」
- 旧 plan（无 research_ref 字段）→ 放行（向后兼容）

**plan 模板加 research_ref 示例**：
```json
{
  "n": 2,
  "name": "extract_voice_dna",
  "expected_outputs": ["voice_dna.json"],
  "research_ref": ".research_cache/inspiration_voice_dna_<date>.md"
}
```

---

### 2️⃣ 干（Act，主要工作）

**目的**：完成 step 的核心目标，产出 expected_outputs。

**纪律**：
- 调用脚本前，**优先**用 `ai_wrapper.py` 包装关键脚本的输出（参考下方 §AI 二次复核层）
- 脚本输出落地后立刻 `plan_tracker.py step <plan_id> --n N --output <file>`
- 失败时立刻 `plan_tracker.py abort <plan_id> --reason <desc>`（防 plan 僵尸）

---

### 3️⃣ 反思（Reflect，step 完成后必须）

**目的**：把这一步学到的「非显然信息」沉淀，给后续 step / 后续会话 / 后续项目用。

**文件路径**：`.reflections/<plan_id>_step_<n>.md`

**最小模板**：
```markdown
# Step N 反思 · plan_id=<id>

## 本步骤做了什么
<具体执行了什么操作 / 调用了什么工具 / 产出了什么文件>

## 学到了什么
<这一步揭示了什么新信息 / 哪些假设被验证或推翻>

## 下次怎么做更好
<如果重做，会改进什么 / 遇到的坑 / 给后续 step 的提示>
```

**hook 提醒**：
- `core/claude-home/hooks/posttooluse_step_reflection.py`
- 触发条件：Bash 命令含 `plan_tracker.py step ... --n N` 完成后
- 检测 `.reflections/<plan_id>_step_<n>.md` 是否存在
- 不存在 → stderr 提示「请补写反思」（**不阻断**，PostToolUse 永不 exit 2）
- 主代理收到提示后应立即 Write 反思文件

---

## AI 二次复核层（脚本输出 AI 分析包装）

**问题**：纯规则脚本（cluster_segmenter / arc_aggregator / naming_convention 等）会因「关键词漏掉」「阈值偏」「字段映射错」做出错误判断。

**方案**：每个核心脚本的关键输出经过 `ai_wrapper.py` 复核。

**使用方法**：
```bash
# 1. 跑原脚本
python core/scripts/cluster_segmenter.py --project workspace/styles/<书> > /dev/null
# 产出 cluster_index.json

# 2. AI 复核
python core/scripts/ai_wrapper.py \
  --input workspace/styles/<书>/cluster_index.json \
  --task "已蒸馏书的情节单元切分。检查 cluster 数 / 平均章数 / 平均字数 / 切割原因分布是否合理。" \
  --context-file workspace/styles/<书>/作者风格_FINAL.json
# 产出 cluster_index.json.ai_review.json
# 含 agreement / confidence / override_recommendation / reasoning
```

**输出 JSON 结构**：
```json
{
  "original_path": "...",
  "ai_review_available": true|false,
  "agreement": "agree|disagree|partial|skipped",
  "confidence": 0-1,
  "override_recommendation": null | {...},
  "reasoning": "AI 推理过程 < 300 字"
}
```

**主代理使用规则**：
- `agreement=disagree` 且 `confidence > 0.7` → 应该重新审 / 调脚本参数 / 主代理审 override_recommendation
- `agreement=partial` → 部分采纳 override_recommendation
- `agreement=agree` → 直接采纳原脚本结果
- `agreement=skipped`（gen-model 不可用）→ 透传原结果，不阻塞

**应该接入 ai_wrapper 的核心脚本（v22.gov P1）**：
1. `cluster_segmenter.py` — 切分决策可能错
2. `arc_aggregator.py` — emotion_curve / shape 拟合可能误判
3. `character_arc_aggregator.py` — Stanford 6 维计算可能粗糙
4. `naming_convention_distiller.py` — 文化倾向判定可能错
5. `title_style_distiller.py` — tier 分布可能不准
6. `dimension_evolver.py` — 候选维度可能含噪
7. `audit_hub.py` — issue 列表可能有误报
8. `validate_style.py` — strict 检查可能误判
9. `style_evaluator.py` — SFS 分数可能粗
10. `chapter_splitter.py` — 切章点可能不自然
11. `gen_chapter_titles.py` — 标题可能 OOC
12. `learning_loop.py` — pattern 提取可能错

**P2 范围（未实现 · 文档化 TODO）**：剩余 100+ 脚本按需接入，优先级靠 system_health_audit.py 监测。

---

## 业界依据

详见 `.research_cache/inspiration_hooks_reflect_aiwrap_*.md`（Round 1 调研）：

- **ReAct + Reflexion** 范式（Yao 2022 + Shinn 2023）：迭代「行动 → 反思」是 SOTA
- **Voyager skill library**（NeurIPS 2023）：工具结果 LLM 二次验证 + 渐进 skill 添加
- **LLM-as-judge** 用于 deterministic output 复核（业界 hybrid pipeline 标准）
- **Claude Code hooks** 官方推荐 PreToolUse / PostToolUse / SessionStart / Stop 4 类 + matcher 精准 gating

---

## 与既有架构的关系

| 层 | 模块 | 作用 |
|---|---|---|
| L1 防误判 | ai_wrapper.py | 脚本输出 AI 复核 |
| L2 防跳步 | pretooluse_step_research.py | step 前查 research_cache |
| L3 防失忆 | posttooluse_step_reflection.py | step 后强制反思 |
| L4 plan 追踪 | plan_tracker.py | 跨命令 step 状态 |
| L5 失败模式累积 | learning_loop.py | 跨 step pattern 沉淀 |
| L6 维度演化 | dimension_evolver.py | 蒸馏 schema 自升级（v22.evolve） |

六层互补，**共同实现 SRE 风格「3 轮 0 issue 收敛」目标**。

---

## 引用方式

6 个核心 plan 命令文档头部都应加：

```markdown
> **三段式纪律（v22.gov）**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。
> hook 自动检查 research_cache 存在 + 反思文件，缺失提示补救。
```
