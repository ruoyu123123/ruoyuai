# v23 Layer 0+1 异质监督层 — 盲点 + 卡死救援

## 背景

用户反馈（2026-05-24）：「**经常遇到生成过程中必须我来发现问题才能解决问题，AI 自己无法发现问题，并且这一次遇到问题但是没办法自己解决**」。

业界数据（已联网调研）：
- **Self-Correction Blind Spot 64.5%**（arxiv 2507.02778, 2025）—— LLM 改不了自己同样的错
- **检测了不拦截 = 等于没检测**（Antigravity / Wink arxiv 2602.17037 / BerriAI 共识）
- **sibling supervisor 是破盲点的最简解**（VIGIL arxiv 2512.07094）

实地诊断（2026-05-24 grep）：v22.5 已有 14 套自学习机制，但**精准缺**：
- 没有运行时卡死拦截（retry 计数散在 user_experience / canary / drift_monitor 三处，全是事后统计）
- 没有屏蔽内部 context 的异质 reader（reading-reflector 共享 manifest + 风格库 = 集体盲）
- 没有逃生通道（AI 反复说"已修复"没人按停止键）

## 新增组件

### Layer 0 · `core/scripts/stuck_loop_guard.py`

运行时强制中断 —— **纯规则**不调 LLM，避免被同源 prompt 污染。

4 类信号：

| 信号 | 触发条件 | 数据源 |
|---|---|---|
| `RETRY_THRASHING` | 同章节 audit + judge 报告 ≥ 3 份 | `.audit/` + `.judge_reports/` |
| `UNCHANGED_FINDING` | 相邻两次 audit 的 issue code 重叠率 ≥ 80% | `.audit/ch_NNN_audit*.json` |
| `WRITER_OUTPUT_LOOP` | 同章节相邻两版 .txt 4-gram Jaccard > 0.85 | `章节/第NNN章/*.txt` |
| `SCRIPT_REPEAT_FAIL` | 同脚本同错误连续 ≥ 2 次（由外部 `--record-error` 上报） | `_数据库/.stuck_state.json` |

**命中 → 写 `_数据库/.escalations/escalation_<ts>.json` + exit 2 + 打印 STOP 到 stderr**。

CLI：
```bash
python core/scripts/stuck_loop_guard.py <project>            # 扫所有活跃章
python core/scripts/stuck_loop_guard.py <project> --ch N     # 单章
python core/scripts/stuck_loop_guard.py <project> --status   # 只读
python core/scripts/stuck_loop_guard.py <project> --reset    # 手动 unstick
python core/scripts/stuck_loop_guard.py <project> --record-error <script> <code>
```

### Layer 1 · `.claude/agents/novel-adversarial-reader.md`

异质 sibling agent —— **故意屏蔽**所有主系统 context（manifest / 风格库 / cluster_blueprint / 写作经验 / 历史 audit / reflection），只读章节正文，以网文老读者视角挑刺。

**严格屏蔽清单**（agent md 第二节硬约束，违反 → `context_contamination: true` 报告作废）。

10 类 issue type（敌对读者用词，不用术语）：
`无聊 / 出戏 / 看不懂 / 看着烦 / 塑料感 / 假 / AI味 / 无效信息 / 前后不搭 / 人物不像人`

5 档 verdict：`drop_book / skip_skim / force_finish / ok_read / want_more`

**输出**：`_数据库/.audit/ch_<NNN>_adversarial.json`

### Layer 1 · `core/scripts/adversarial_blindspot_scan.py`

对比 adversarial reader vs 内部检测器（audit_hub + reading-reflector + judge）的 issue 集合。

**Diff 矩阵**：

| 状态 | 含义 | 严重度 |
|---|---|---|
| adversarial 抓到 ∧ 内部全没抓到 | **集体盲点** | warning（≥2 type） / advisory（1 type） |
| adversarial verdict=drop_book ∧ 内部全 pass | **红色警报：系统性盲点** | red_alert（exit 2） |
| 同 type 在 ≥3 章反复盲 | **系统盲区** | 自动标 `is_systemic: true` |

**输出**：`_数据库/.learning/adversarial_blindspots_<ts>.json`

## 接入流水线

### 已自动接入

`learning_hub.py` 的 `LEARNERS_FULL` 加了：
- `stuck_loop_guard.py {project}`
- `adversarial_blindspot_scan.py {project}`

`LEARNERS_QUICK` 也加了 `stuck_loop_guard`（卡死信号最痛，必跑）。

### 主代理手动调度（约定）

audit_hub 是同步脚本调度器，不能直接 spawn agent。**主代理在每章 audit_hub 跑完后，必须额外 spawn `novel-adversarial-reader` agent**，spawn 模板：

```
Agent({
  description: "敌对读者吐槽 ch<N>",  // 注意：不含 "writer/validator/voice/写作/正文" 等关键词避免 hook 误拦
  subagent_type: "novel-adversarial-reader",
  prompt: "PROJECT: <project>\nCHAPTER: <N>\nMODE: adversarial_reader"
})
```

agent 写完 `_数据库/.audit/ch_<NNN>_adversarial.json` 后，主代理跑：

```bash
python core/scripts/adversarial_blindspot_scan.py <project> --ch <N>
```

exit 2 → 红色警报 → **触发 stuck_loop_guard 的 escalation 通道**（主代理调 `stuck_loop_guard.py --record-error blindspot RED_ALERT`）。

### 与 hook 的兼容性

- `novel-adversarial-reader` description **故意不含**写作/蒸馏关键词，所以 PreToolUse Agent hook 不会要求 PLAN_ID / RESEARCH_REF
- 它不是多步流水线 agent，无需 plan_tracker
- 它**只 Read 章节正文一个文件**，输入 prompt 仅三行，绕开"塞满 context"嫌疑

## 用法场景

### 场景 1：主代理观察到 audit 反复跑同一章

调 `stuck_loop_guard.py <project> --ch N`。命中 → exit 2 → 弹 escalation 报告 → 主代理停手把球扔回用户。

### 场景 2：用户说"这章读着像 AI 写的"但 audit 全 pass

spawn `novel-adversarial-reader` ch<N> → 跑 `adversarial_blindspot_scan.py --ch N` → 如 red_alert 说明用户判断对，audit 规则不够。把 blindspot_types 喂给 `error_pattern_analyzer` 找 root cause。

### 场景 3：脚本反复报错

主代理在 try/except 里调 `stuck_loop_guard.py --record-error <script> <code>` 累积。下次跑全量 hub 时信号 4 触发 escalation。

## 边界 & 反模式

- ❌ **不要**让 adversarial-reader 读 manifest / 风格库 / cluster_blueprint —— 这会破坏整个设计的核心价值（它的价值在于"看不见 AI 的辩护"）
- ❌ **不要**让 stuck_loop_guard 调 LLM 判断"是否真的卡死" —— LLM 会被它要监督的同源 prompt 污染
- ❌ **不要**为了 verdict 好看放宽阈值 —— reward_hacking_detector 会抓到
- ✅ stuck_loop_guard.py 内任一 detector 异常 → 跳过该 detector，绝不让本守卫本身成新故障点（防御性 exit 0）

## Layer 2+3 · Counterfactual Judge（已实施）

### `.claude/agents/novel-counterfactual-judge.md`

双重 preset stance：
- 帽子 1 · 反事实包装：被告知"这是匿名投稿，不知作者"
- 帽子 2 · 严厉外审编辑（persona=harsh_critic，兼容 judge_consensus P2-6）

**严格屏蔽清单**：manifest / 风格库 / cluster_blueprint / 写作经验 / .audit / .judge_reports
**唯一输入**：章节正文 + ≤200 字脱敏 SYNOPSIS（主代理负责剥技术信号）

5 维度评分 + Grade A/B/C/D + buy_or_reject 4 档 + kill_shot 一句话。

**输出**：`_数据库/.judge_reports/ch_<NNN>_counterfactual.json`

### `core/scripts/counterfactual_judge_diff.py`

3 类 self-protection 信号：

| 信号 | 触发 | severity |
|---|---|---|
| `SCORE_PROTECTION_GAP` | 内部 score - cf score ≥ 1.0 / 1.5 / 2.5 | advisory / warning / fatal |
| `GRADE_PROTECTION_GAP` | grade 差 ≥ 2 档（A→C 等） | fatal |
| `FATAL_BLINDSPOT` | cf 报 fatal 但内部全没抓 | fatal |

**跨章趋势**：≥ 60% 章节内部宽松 ≥ 1.0 分 → 系统性宽松，建议 judge prompt 加严标。

**输出**：`_数据库/.learning/counterfactual_judge_diff_<ts>.json`，退出码 0/1/2

### 主代理调度约定

```
# 每章 audit 跑完后额外 spawn
Agent({
  description: "反事实盲审 ch<N>",
  subagent_type: "novel-counterfactual-judge",
  prompt: "PROJECT: <p>\nCHAPTER: <N>\nMODE: counterfactual_judge\nSYNOPSIS: <≤200 字脱敏简介>"
})

# Agent 写完报告后跑 diff
python core/scripts/counterfactual_judge_diff.py <project> --ch <N>

# 顺便喂给 judge_consensus 触发 persona_dissent_severity 报警
python core/scripts/judge_consensus.py merge \
  _数据库/.judge_reports/ch_<NNN>_consensus.json \
  _数据库/.judge_reports/ch_<NNN>_counterfactual.json
```

### SYNOPSIS 脱敏规则（主代理责任）

主代理生成 SYNOPSIS 时**必须抹掉**：
- `cluster_id` / `scene_type` / `anchor_hit` 等技术字段名
- 任何对"上一轮 / 上一次 / 反思" 的引用
- 任何 manifest 字段语言

抹不干净 → counterfactual 嗅到"AI 写的"气息 → `context_contamination: true` → 报告作废。

---

## Layer 4 · GEPA Pareto 前沿（已实施）

### `core/scripts/gepa_prompt_optimizer.py`

业界依据：GEPA (ICLR 2026 Oral, arxiv 2507.19457)。

**核心思路**：现有 meta-prompt-optimizer 把所有建议**合并进同一文档**——单一最优策略丢失多样性。GEPA 保留候选池，每个候选可能在不同 scene_type 上最优 → 都该入 Pareto 前沿。

**算法**：
1. 扫所有项目 `.evolution/prompt_suggestions_*.json` → 抽出候选
2. 给每个候选标注 (target_agent, covered_scenes, proxy_metric_by_scene)
3. 代理 metric = 候选生命周期内 judge 分数（按 scene_type 分组）
4. Pareto 前沿 = 每个 (agent, scene) 维度 top K 候选 → 至少在一组上 top → 入前沿
5. 反思 = 前沿候选共同改动的 hot section / change_type / signal

**输出**：
- `core/claude-home/.gepa/candidates_<ts>.json`
- `core/claude-home/.gepa/pareto_frontier_<ts>.json`
- `core/claude-home/.gepa/recommendations_<ts>.json`

**CLI**：
```bash
python core/scripts/gepa_prompt_optimizer.py                   # 扫所有项目
python core/scripts/gepa_prompt_optimizer.py --project <path>  # 单项目
python core/scripts/gepa_prompt_optimizer.py --recommend 战斗  # 查推荐
python core/scripts/gepa_prompt_optimizer.py --status          # 最新 snapshot
```

退出码：0 / 1 候选池过窄（< 3）/ 2 候选全退化

### 何时调用

- 主代理跑 meta-prompt-optimizer agent 累积 ≥ 3 次后跑一次 GEPA 抽 Pareto 前沿
- 用户问"prompt 演化怎么样了" → `gepa --status`
- 写新章前可选 `gepa --recommend <scene_type>` 看是否有针对该场景的前沿候选

### 接入 learning_hub

`LEARNERS_FULL` 已加 `gepa_prompt_optimizer.py --project {project}`，跑全量 learning hub 时自动包含。

---

## 完整 5 层架构图

```
              用户痛点 = AI 看不见问题 + 看见了改不掉
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         [Layer 0]      [Layer 1]    [Layer 2+3]
       stuck_loop_     adversarial   counterfactual
       guard.py        -reader       -judge
       (纯规则中断)    (异质读者)    (盲审编辑)
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                       [Layer 4]
                  gepa_prompt_optimizer.py
                  (Pareto 前沿候选演化)
                            ▼
              所有结果 → learning_hub.py 统一调度
              问题汇总 → escalations/ 弹给人
```

每层用不同手段攻击同一个核心问题（Self-Correction Blind Spot 64.5%）：
- Layer 0：**纯规则**不调 LLM，避免污染
- Layer 1：**异质读者**屏蔽内部 context
- Layer 2+3：**异质评审**被骗以为是匿名投稿
- Layer 4：**保留候选多样性**而不是单一最优覆盖

---

## 后续可加（真的可选）

- **Process Reward Model**：评 writer 每一步中间产出而不只是最终章节（cross_chapter_meta_quality_scan 接近，但需改成 inline 评估）
- **DSPy signature 化**：把 prompt 改成声明式 signature，自动 compile 优化（工作量大，**不推荐**——你的 prompt 是中文契约，改 signature 边际收益低）
- **跨项目 universal skill pool 自动联通**：SkillOpt 训练循环已替代 dimension_evolver，跨项目 skill 迁移待 P3 长期项

## 测试快查

```bash
python core/scripts/stuck_loop_guard.py --help
python core/scripts/adversarial_blindspot_scan.py --help
# 跑一个真实项目（如 workspace/novels/xxx）看输出格式
python core/scripts/stuck_loop_guard.py workspace/novels/xxx --status
```

## 业界来源

- [Self-Correction Bench (arxiv 2507.02778) — 64.5% blind spot rate](https://arxiv.org/abs/2507.02778)
- [VIGIL: Reflective Runtime for Self-Healing Agents (arxiv 2512.07094)](https://arxiv.org/pdf/2512.07094)
- [Counterfactual Debating (arxiv 2406.11514)](https://arxiv.org/pdf/2406.11514)
- [SPC: Self-Play Critic (arxiv 2504.19162)](https://arxiv.org/pdf/2504.19162)
- [Wink: Recovering from Misbehaviors (arxiv 2602.17037)](https://arxiv.org/pdf/2602.17037)
- [BerriAI/self-improving-agent — human-approved diff loop](https://github.com/BerriAI/self-improving-agent)
