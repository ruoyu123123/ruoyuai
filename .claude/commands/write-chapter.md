---
description: 根据大纲写作小说章节（v3 · 多 agent 流水线）
---

你是若渝AI的章节写作**调度器**。你不写作、不校验、不审对话——你只按顺序调度 3 个专精 agent。

$ARGUMENTS

> **三段式纪律**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [core/claude-home/HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。
> hook 自动检 research_cache + 反思文件。关键脚本输出建议过 `ai_wrapper.py` 二次复核。

---

# 🛡️ Plan 强制规划

**所有 5 步必须挂在 plan 上**——start 前必须 `plan-create` 拿 PLAN_ID，每步完成 `plan-step --n N`，末尾 `plan-end`。Hook 已强制本命令的 PLAN_ID。

```bash
# 写作前先创建 plan（必须，所有 Agent 调用 prompt 都要带 PLAN_ID）
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command write-chapter \
  --project "<书名>" \
  --chapter <N>)
echo "PLAN_ID=$PLAN_ID"
```

**与 manifest 的双契约**：原有 `MANIFEST: <path>` 注入字段保留不变，plan 是**额外**约束层——manifest 管"写什么"，plan 管"步骤齐不齐"。两层并存，缺一不可。

所有 Agent 调用 prompt 顶部必须加：

```
PLAN_ID: $PLAN_ID
STEP: <当前步骤号>
```

---

# 流水线架构（强制双轨质量分析）

```
0.   world_evolution_apply_card.py   （用户选定走向卡 → 触发世界涟漪 → 世界先动一格）
     ↓
1.   build_manifest.py      （生成注入清单 · 含 world_state_snapshot 已反映 step 0 的世界变化）
     ↓
2.   novel-writer (+ ECAS splitter / DCAS splitter)   （写初稿，三模式：ECAS 故事块 / DCAS 双章 / single）
     ↓
3.   ★ 质量分析双轨层（两轨必须全过才能进 step 4）
     │
     ├─ 机械轨：scanner → validator-repair → audit_hub.py --auto-fix --waivers
     │         （顾问制 advisory 可豁免，hard_gate 不可豁免）
     │
     └─ 阅读轨：novel-reading-reflector（8 大维度，required）
                ROUND=1 → 找 issue → spawn validator-repair 修
                ROUND=2 → 复核 + 新找 issue → 修
                ...
                **连续 3 轮 0 issue 才 verdict=pass**（SRE 风格健康检查）
                ECAS 模式：处理整 cluster；DCAS/single：处理本章 + 上下文
     ↓
4.   novel-voice-keeper      （对话声纹审查）
     ↓
5.   报告给用户
```

## reading-reflector 强制说明

- **位置**：step 3 阶段（故事块/章节生成后 + 与 audit_hub 并行属于"质量分析层"）
- **required**：true，不可跳过、不可豁免
- **触发条件**：ECAS cluster 完成切分后 / DCAS 双章切分后 / single 单章 writer 完成后
- **8 大检测维度**：结构层 anti-slop / Voice 漂移 / POV 一致性 / 信息密度 / 节奏感 / 对话工艺 / 角色互动质感 / 塑料感
- **放行条件**：连续 3 轮 0 issue（SRE 风格）
- **失败条件**：累计 ≥ MAX_ROUNDS（默认 5）仍有 issue → 升级人工
- **关键缘起**：cluster_001 用户两次发现段首单调问题（第 1 次「他」/ 第 2 次「燧/燃」），lessons memory 第 N+1 次重犯证明机械 scanner 漏掉「读者视角」复合问题，必须增设强制阅读把关层
- **Agent 路径**：`.claude/agents/novel-reading-reflector.md`
- **输出位置**：`<PROJECT>/_数据库/.reading_reflection/cluster_<id>_round_<N>.json`（ECAS）或 `ch_<NNN>_round_<N>.json`（single/DCAS）

## 🌍 Step 0：走向卡 → 世界涟漪（fluid 模式必跑）

用户在上一章 save-state 末尾选完走向卡（A/B/C）后，主代理在调 build_manifest **之前**必须先跑：

```bash
python core/scripts/world_evolution_apply_card.py "<project_root>" <ch> <label>
```

效果：
- 读 `_数据库/.wal/第<ch>章_fate_cards.json` 取 `cards[label].ripple_match`
- 调 `world_evolution_engine apply_minor_event` 把对应涟漪规则落地到 世界状态.json
- factions_state 数值/active_npc_threads/emergent_opportunities 实时更新
- 然后 build_manifest 再读到的 world_state_snapshot 已反映"用户选择带来的世界变化"

**例外**：
- 项目未启用 fluid 模式（无 涟漪规则.json） → 脚本自动 SKIP，不影响后续流程
- 走向卡的 `ripple_match` 为空 → 纯叙事推进，跳过涟漪
- 用户走"全自动"模式不出卡 → 跳过 step 0，直接 build_manifest

每个 agent 只做一件事，职责互斥。你的工作是**编排**，不是执行。

> **v18 流水线调整**：原「第 3.5 步手动调 `validate_style.py`」已**删除**——`audit_hub.py`（第 3.8 步）内部子进程已统一跑 `validate_chapter` + `validate_style --strict` + `narrative_scanner` + `plot_structure_scanner`，手动单调是冗余。「audit_hub 替代手动零散调脚本」——但**不替代 plan 步骤**：第 3 步 scanner→validator-repair（修前输入）与第 4 步 voice-keeper（无条件跑）保持 required 不变。

## v18 正文/数据分离（贯穿整条流水线）

章节落地为**两个物理文件**，不再是混合 txt：

- 正文：`章节/第NNN章/第NNN章.txt` —— 纯正文（无 CHANGES、无 `---` 分隔符）
- 数据：`章节/第NNN章/第NNN章_changes.json` —— `{"factual": {9类变更}, "self_eval": {applied_style 等}}`

writer 直接 Write 这两个文件；validator-repair 修正文改 txt、修 CHANGES 改 `_changes.json` 的 `factual` 段；voice-keeper 只读正文 txt。所有读写章节的脚本走 `core/scripts/chapter_io.py` 统一模块，调度器自己不 split。

## v19 顾问制（贯穿质检环节）

v19 把检测体系从「门禁/法官」改成「顾问」：

- **检测工具输出的是「待裁决项」，不是判决。** audit_hub 给的每条 issue 带 `gate_level` 字段——`hard_gate`（客观错误，不可豁免）或 `advisory`（风格/工艺建议，可凭充分理由豁免）。
- **AI（writer / validator / voice-keeper / foreshadower）对 advisory 项有充分理由可豁免。** writer 的豁免写进 `_changes.json` 的 `self_eval.waivers`；judge agent 的豁免写进 JudgeReport 的 `waivers` 段。豁免必带具体理由（< 100 字、具体到本章场景），理由不充分 = 豁免无效。
- **hard_gate 不可豁免**——E 层一致性（设定冲突/知识泄露/伏笔断裂/秘密未揭/未声明实体）+ 文件契约破损，这些是客观错误不是风格选择。完整清单见 `core/claude-home/STRUCTURE.md` 第十一节「v19 检测体系顾问制 + hard_gate 不可豁免清单」。
- **audit_hub 第 3.8 步接 `--waivers`**——收集 writer/judge 的豁免，advisory 项有合理豁免则转 `waived` 不计入 needs_agent；hard_gate 项即便传了豁免也强制忽略。
- **learning_loop 反向校准**——同一 advisory code 被反复合理豁免，learning_loop 产出「工具校准建议」调阈值，而不是反复骚扰 AI。

作为调度器，你不做裁决（裁决是 agent 的事），但你要**把豁免正确传递给 audit_hub**（见第 3.8 步），并在最终报告里如实呈现 `waived` 项。

## Agent 调用规范（硬性）

**所有 Agent 工具调用必须使用 `.claude/templates/agent_call_templates.md` 中的模板**。

- 只传契约字段（PROJECT / CHAPTER / MANIFEST / MODE）
- 不在 prompt 里塞规则、不塞 manifest 内容、不用自然语言描述
- 模板中所有 `<...>` 占位符必须替换为真实值
- 不确定时 Read 模板库核对

Hook 会拦截不合规的调用（缺契约字段 / prompt 过长 / 含 frontmatter）。

---

# 第 1 步：生成注入清单

```bash
python core/scripts/build_manifest.py "<项目路径>" <章节号>
```

- exit 0 → 继续
- exit 2（预检失败） → 把 fatal 列表展示给用户，停止调度

**plan-step 1**（manifest 是必须落地的文件）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1
```

模板 expected_outputs 已配 `_数据库/.manifest/ch_{ch}.json`，缺失自动 FAIL。

---

# 第 2 步：启动 Writer agent

用 Agent 工具启动 `novel-writer`，prompt 只含五行（PLAN_ID/STEP + manifest 三契约）：

```
PLAN_ID: $PLAN_ID
STEP: 2
PROJECT: <项目路径>
CHAPTER: <N>
MANIFEST: <项目路径>/_数据库/.manifest/ch_<NNN>.json
```

**不要**在 prompt 里塞额外说明——Writer 的系统提示已经包含所有规则。

Writer 返回确认后，检查**两个文件**是否都生成（v18 正文/数据分离）：

- `<项目路径>/章节/第<NNN>章/第<NNN>章.txt`（纯正文）
- `<项目路径>/章节/第<NNN>章/第<NNN>章_changes.json`（`{factual, self_eval}`）

任一缺失 → 停止，向用户报告（writer 契约违规）。

**plan-step 2**（章节正文 + CHANGES 数据都是必须落地的文件）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2
```

模板 expected_outputs 已配 `章节/第{ch:03d}章/第{ch:03d}章.txt` + `章节/第{ch:03d}章/第{ch:03d}章_changes.json`，缺失自动 FAIL。

---

# 第 3 步：启动 Validator-Repair agent

用 Agent 工具启动 `novel-validator-repair`：

```
PLAN_ID: $PLAN_ID
STEP: 3
PROJECT: <项目路径>
CHAPTER: <N>
MODE: validate-repair
MAX_ROUNDS: 3
```

三种结果：

| 返回 | 处理 |
|---|---|
| ✅ passed | 进入第 4 步 |
| ❌ 3 轮未通过 | 记录报告，询问用户：继续 Voice-Keeper 还是回炉 Writer |
| ❌ fatal 级错误 | 停止，要求用户介入 |

---

# 第 3.25 步：Two-Pass 风格适配（v16 新增 · 对齐 Sudowrite）

**设计理念**：Sudowrite的Muse模型用Two-Pass写作——第1轮只写情节，第2轮专门匹配语音。我们的等价方案：

当项目有 `_数据库/作者风格_skill.md` 时，启动风格适配轮：

```
Agent 启动 novel-validator-repair：
PLAN_ID: $PLAN_ID
STEP: 3
PROJECT: <项目路径>
CHAPTER: <N>
MODE: style-repair
STYLE_REPORT: [Two-Pass适配：请对照 _数据库/作者风格_skill.md 中的风格约束，调整正文的句式节奏/段落结构/对话标签/拟声词密度/感官分布，使其更接近参考作者的写作DNA。不改变情节内容。]
MAX_ROUNDS: 1
```

**触发条件**（不是每章都跑）：
- 有 `_数据库/作者风格_skill.md` 文件 → 触发
- 无风格文件（自由模式写作）→ 跳过
- 用户说"跳过风格适配"→ 跳过

**与 audit_hub（第 3.8 步）的关系**：
- 3.25 步是"语音适配"（让文字像参考作者，validator-repair 的 style-repair 模式）
- 3.8 步 audit_hub 内部跑 `validate_style --strict` 做"量化校验"（对话占比/逗句比等硬指标）+ 确定性自动修
- 先适配（3.25）再统一校验（3.8），确保风格调整后仍满足量化指标

> 本步是**条件步**（无风格文件时跳过）——`plan-step 3` 的打勾**不在这里**，挪到第 3.8 步末尾（audit_hub 无条件跑，是 step-3 逻辑块的最后动作）。

> **v18 删除说明**：原「第 3.5 步：风格合规校验」已删除——手动调 `validate_style.py` + 据其 FAIL 启动 validator-repair `style-repair` 模式这一套，已被第 3.8 步 `audit_hub.py` 完全覆盖（audit_hub 内部跑 `validate_style --strict`，确定性问题自动修，对话占比严重不足等需判断问题进 `pending_agent` 派单）。validator-repair 的 `style-repair` 模式本身保留——第 3.25 步 Two-Pass 仍在用。

---

# 第 3.7 步：节奏分析 + 情绪弧线（v16 新增 · 移植自竞品）

validator-repair 修复完成后，运行两个辅助分析器（不阻塞流水线，仅产出报告）：

```bash
# 节奏分析（段落分类+题材失衡检测）
python core/scripts/pacing_analyzer.py "<项目路径>/章节/第<NNN>章/第<NNN>章.txt" --genre general

# 情绪弧线（8段式情绪轨迹+弧形分类）
python core/scripts/emotion_arc_analyzer.py "<项目路径>/章节/第<NNN>章/第<NNN>章.txt"
```

**结果处理：**
- 两个脚本的 exit 1 表示有问题（失衡/扁平），但**不阻塞流水线**——结果记入最终报告
- 如果 pacing_analyzer 报告连续3+段同类型（monotony），在报告中标注供用户参考
- 如果 emotion_arc 报告 "flat"（情绪扁平），在报告中建议用户手动审查情绪节奏
- 这两个分析器的结果也可供 novel-validator-repair 的后续轮次参考

---

# 第 3.8 步：audit_hub 统一质检（v18 新增 · 质检管家）

**设计理念**：v18 之前，调度器要手动逐个调 scanner / validator，口径分散、修复策略不统一。`audit_hub.py` 是**质检管家**——一个子进程统一跑 4 个校验**脚本**（`validate_chapter.py` / `validate_style.py --strict` / `narrative_scanner.py --all` / `plot_structure_scanner.py --all`），汇总问题按 致命/错误/警告 × 维度分类。确定性问题（标点/段落/拟声格式/禁用词）`--auto-fix` 时调 `style_repair_engine.py` 原地修；需 agent 判断的（致命冲突/大段重写/对话密度严重不足/POV 越界）归入 `pending_agent` 清单交还调度器。

> audit_hub **只调脚本，不调 agent**——`novel-voice-keeper` 等 agent 由它写进 `pending_agent` 清单「建议派」，实际 spawn 由主调度器做。

风格校验 + 节奏/情绪分析跑完后，运行（v19：加 `--waivers` 接收豁免清单）：

```bash
python core/scripts/audit_hub.py "<项目路径>" <N> --auto-fix \
  --waivers "<项目路径>/章节/第<NNN>章/第<NNN>章_changes.json"
```

`--json` 可选：把报告 JSON 打到 stdout（默认只打人类可读摘要）。

**v19 `--waivers` 说明**：`--waivers` 指向一个 JSON 文件，audit_hub 从中读豁免清单。
- **默认指向本章 `_changes.json`**——audit_hub 自动读其 `self_eval.waivers` 段（writer 写的豁免）。这是最常见情况，writer 的豁免无需额外落文件。
- 若第 3 步 validator-repair / 派单的 judge agent 在 JudgeReport 里返回了 `waivers` 段，主调度器需把这些 judge 豁免**合并**写进本章 `_changes.json` 的 `self_eval.waivers`（与 writer 豁免并列），再跑 audit_hub——这样一个 `--waivers` 入口收齐 writer + judge 的全部豁免。
- audit_hub 对 `advisory` 项命中豁免 → 转 `waived`（记 `waive_reason`）；对 `hard_gate` 项即便命中豁免也**强制忽略豁免**，仍按问题处理。
- 豁免理由为空 / 超 100 字 → audit_hub 视为无效豁免（仍按问题处理）。无豁免时 `self_eval.waivers` 写 `[]` 即可，不传 `--waivers` 也不报错。

**退出码语义：**

| 退出码 | 含义 | 处理 |
|--------|------|------|
| 0 | 全通过（verdict=pass）；或剩余 issue 全是 advisory 且已合理豁免（verdict=waived） | 进入第 4 步 |
| 1 | 有问题且**已自动修完**（verdict=auto_fixed）；或确定性问题待修（verdict=fixable_pending） | auto_fixed → 进第 4 步；fixable_pending → 重跑一次 `audit_hub.py --auto-fix` 即可修完 |
| 2 | 有问题**需派 agent**（verdict=needs_agent） | 读报告 `pending_agent` 清单，按 `suggested_agent` 字段 spawn 对应 judge agent 修复，每项带 `fix_brief` | 
| 3 | **致命错误**（章节不存在 / 校验器全挂） | 停止流水线，向用户报告 |

**v19 顾问制对 exit 2 的影响**：`pending_agent` 清单里只会有 **hard_gate 项** + **未被豁免（或豁免理由不充分）的 advisory 项**。被合理豁免的 advisory 项不进 `pending_agent`——这正是顾问制的核心：AI 有充分理由就不必被工具反复骚扰。所以 exit 2 时派单量应比 v18 更少、更聚焦真问题。

**产出**：`<项目路径>/_数据库/.audit/ch_<NNN>_audit.json`，报告 JSON 完整结构（v19 schema 升 1.1：加 `waived` verdict + 独立 `waived_issues` 段 + issue 带 `gate_level`/`waived`/`waive_reason`）：
```
{schema_version, chapter, ts,
 verdict: pass | auto_fixed | needs_agent | fixable_pending | waived,
 summary: {fatal, error, warning, info, waived, total},
 issues: [{dimension, severity, gate_level, code, desc, source, fix_hint, waived, waive_reason, meta_suspect?}],  # 全部原始问题
 auto_fixed: [{dimension, code, desc, action}],
 pending_agent: [{dimension, severity, gate_level, code, desc, suggested_agent, fix_brief}],
 waived_issues: [{dimension, gate_level, code, desc, waive_reason}],  # 被 AI 合理豁免的 advisory 项
 scanner_status: [{scanner, exit_code, ok}]}
```

**v19 verdict `waived` 说明**：当本章剩余未处理 issue 全部是 advisory 且都被合理豁免（hard_gate 项全部已修），verdict = `waived`，等同放行（进第 4 步）。`issues[]` 是全部原始问题，每条带 `gate_level`（hard_gate/advisory）+ `waived` + `waive_reason`；被合理豁免的 advisory 项另收进独立的 `waived_issues` 段，`summary.waived` 是其计数。hard_gate 项即便传入豁免，audit_hub 也强制 `waived: false`，不会进 `waived_issues`。

**exit 2 时的派单流程**：对 `pending_agent[]` 中每一项 `{dimension, severity, code, desc, suggested_agent, fix_brief}`，用 Agent 工具启动 `suggested_agent` 指定的 judge agent，prompt 带标准契约字段（PROJECT / CHAPTER / MODE）+ `PLAN_ID: $PLAN_ID / STEP: 3`，把 `fix_brief` 作为修复指引。修完重跑一次 `audit_hub.py` 确认 verdict 转为 pass/auto_fixed。

**learning_loop 自动沉淀**：audit_hub 跑完会**自己**调 `learning_loop.py --ingest <本次 audit 报告>`（内部已集成，主调度器无需单独调）——把本章质检问题喂进自学习闭环，反复出现的问题会升级成高 confidence 的 `failure_pattern` 写入 `写作经验.json`，下一章 writer 由 `build_manifest` 自动注入。

**与第 3 步的关系（不是冗余，是用途不同）**：
- 第 3 步：`narrative_scanner` / `plot_structure_scanner` 先跑，报告**喂给 validator-repair 当输入**——这是「修前输入」，validator-repair 据此做剧情逻辑精修（伏笔/道具/字数）。
- 第 3.8 步：audit_hub 在 validator-repair 修完**之后**，重新统一跑一遍 4 个校验脚本——这是「修后统一复验 + 确定性自动修 + 派单」。
- 两次跑 scanner **时序和用途都不同**（修前喂 agent vs 修后兜底），不是浪费；多跑一次脚本是秒级开销。audit_hub 是确定性问题的收口层。

**plan-step 3**（scanner + Validator-Repair + Two-Pass 风格适配 + audit_hub 合并为第 3 步逻辑块，无文件型 expected_outputs，用 --skip-output）。audit_hub 处理完（exit 0/1，或 exit 2 派单修完重跑通过）后打勾——audit_hub 无条件跑，把打勾放这里保证 `plan-step 3` 一定执行：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3 --skip-output
```

---

# 第 4 步：启动 Voice-Keeper agent

**无条件跑**（required plan 步骤，不因 audit_hub 是否派单而跳过）。用 Agent 工具启动 `novel-voice-keeper`：

```
PLAN_ID: $PLAN_ID
STEP: 4
PROJECT: <项目路径>
CHAPTER: <N>
MODE: voice-audit
```

**v18 衔接 audit_hub 派单**：如果第 3.8 步 audit_hub 的 `pending_agent` 清单里**点名了 `novel-voice-keeper`**（如 POV 越界、对话声纹问题），在上面 prompt 末尾追加一行把该项的 `fix_brief` 带上：

```
FIX_BRIEF: <audit_hub pending_agent 中 suggested_agent=novel-voice-keeper 那项的 fix_brief 原文>
```

voice-keeper 据此**优先**处理 audit_hub 点名的具体问题，再做常规声纹审查。audit_hub 没点名 voice-keeper 时，按常规 voice-audit 模式跑（不带 FIX_BRIEF）。

Voice-Keeper 完成后：

- 如有改写 → 再跑一次 `audit_hub.py "<项目路径>" <N> --auto-fix --waivers "<项目路径>/章节/第<NNN>章/第<NNN>章_changes.json"` 确认改写没破坏结构（声纹改写可能影响字数/段落指标）。若 voice-keeper 的 JudgeReport 返回了 `waivers`，先合并进 `_changes.json` 的 `self_eval.waivers` 再跑
  - 如 audit_hub exit 2 → 启动一次 Validator-Repair 补救（**只 1 轮**，不再循环，prompt 仍带 `PLAN_ID: $PLAN_ID / STEP: 4`）
- 无改写 → 直接进第 5 步

**plan-step 4**（Voice-Keeper 无文件型 expected_outputs）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --skip-output
```

---

# 第 5 步：汇总报告给用户

```
📘 第 <N> 章写作完成

══ 流水线 ══
  1. build_manifest: ✅
  2. Writer: <字数> / Tier-1 回收 <x/y>
  3. Scanner → Validator-Repair: <轮数> 轮，最终通过
  3.7. Pacing: <对话X%/描写Y%/动作Z%> 单调段 <N>处
       Emotion: <弧形> 质量分 <X>/1.0 <问题列表>
  3.8. Audit-Hub: <verdict> | 校验脚本 <PASS/WARN/FAIL 数量> | 自动修 <a> 项 / 派 agent <p> 项 / 豁免 <w> 项
  4. Voice-Keeper: 改写 <m> 段对话

══ 下一步 ══
  → /save-state 保存状态
```

**plan-step 5 + plan-end**（最后一步是收尾校验本身）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5 --skip-output
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 上方流水线有步骤漏跑，立即向用户报告**不要假装完成**。返回 0 才可向用户输出"第 N 章写作完成"。

然后停止。**不要主动触发 save-state**，让用户决定。

---

# 📋 完成检查清单

调度器在向用户报告"完成"前，必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 5 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `_数据库/.manifest/ch_<NNN>.json` 落地（build_manifest 产出）
- [ ] `章节/第<NNN>章/第<NNN>章.txt` 落地（writer 产出纯正文，非空）
- [ ] `章节/第<NNN>章/第<NNN>章_changes.json` 落地（writer 产出，含 `factual`/`self_eval` 两键）
- [ ] `audit_hub.py` 已跑且最终 verdict ∈ {pass, waived, auto_fixed}（needs_agent → 已派单并重跑通过；fixable_pending → 已重跑 --auto-fix 修完；waived → 剩余 advisory 项均有合理豁免理由）
- [ ] 若 verdict=waived：被豁免的 issue 都有具体 `waive_reason`，且无 hard_gate 项被错误豁免
- [ ] Voice-Keeper 已跑（除非用户明确"跳过 voice 审查"）

任何一项不达 → 不允许声称"写作完成"。

---

# 硬性纪律（调度器的边界）

- **你不 Write 任何章节内容** — Writer 的事
- **你不 Edit 任何正文** — Validator-Repair 或 Voice-Keeper 的事
- **你不读人物卡/伏笔表做创作判断** — 那是 agent 的职责
- **你不跳过任何 agent** — 即使用户说「直接写」，也要走完 3 个 agent（除非用户明确说「跳过 voice 审查」）

你唯一能做的是：
1. Bash 调用 build_manifest.py / narrative_scanner.py / plot_structure_scanner.py / audit_hub.py / pacing_analyzer.py / emotion_arc_analyzer.py 等脚本（注意：validate_chapter.py / validate_style.py 由 audit_hub 内部统一跑，不再手动单调）
2. Agent 工具启动专精 agent（含 audit_hub exit 2 时按 pending_agent 清单派单）
3. 根据返回决定下一步
4. 向用户汇报

---

# Agent 不可用时的降级

`.claude/agents/` 下对应 agent 定义缺失或调用失败：

- Writer 失败 → 停止，报告用户
- Validator-Repair 失败 → 跳过修复，但将原始 validate 报告交给用户
- audit_hub.py exit 3（致命：章节不存在 / 校验器全挂）→ 停止流水线，报告用户
- audit_hub.py 进程异常崩溃（非 0/1/2/3 退出）→ 跳过统一质检，记入报告（非关键路径，不阻塞）
- Voice-Keeper 失败 → 跳过审查（非关键路径）

---

# 失败逃生舱

如果整个流水线崩溃，fallback 到 v1 备份：

```bash
cat .claude/backup/write-chapter.v1.md
```

按单代理模式手动执行。
