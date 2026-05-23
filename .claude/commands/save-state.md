---
description: 章节完成后保存写作状态（v2 · 多 agent 流水线调度器）
---

你是若渝AI的状态保存**调度器**。你不解析、不更新、不生成摘要——你只按顺序调度脚本和 agent。

$ARGUMENTS

## 【v19.2 新增】启动前 WAL 恢复检查（必跑）

step 1 之前，主代理必须先跑：

```bash
python core/scripts/wal_recovery.py "<项目路径或项目名>"
```

- exit 0：无未完成 plan，正常进 step 1
- exit 1：有未完成 plan（save-state/write-chapter 上次崩溃中断）
  - 报告会列出每个未完成 plan 的中断点 step
  - **主代理须问用户**：「上次 ch{N} {cmd} 在 step {X} 中断，要 (a) 从 step {X+1} 续跑 (b) 重置重头 (c) abort 该 plan？」
  - 不能直接 step 1 覆盖——会产生双写

仅在 wal_recovery 报"无未完成 plan"或用户选择"重置/abort"后，才进入 step 1。

---

## 🛡️ Plan 强制规划（v17.2 新增 · Phase 3.1）

**每次调用 `/save-state <ch>` 必须先生成 plan 实例，否则后续 Agent 调用会被 pretooluse hook 拦截 (exit 2)。**

### 开头：plan create（流水线第 0 步，绝对前置）

```bash
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command save-state \
  --project "<书名>" \
  --chapter <ch>)
echo "PLAN_ID=$PLAN_ID"
```

- `<书名>` 取自 `<项目路径>` 末段（如 `workspace/novels/我家娘子是大佬` → `我家娘子是大佬`）
- `<ch>` 即本次保存的章节号 N
- 返回的 `$PLAN_ID` 必须在整个流水线生命周期内复用

### 中间：每步完成立即 plan-step

```bash
# 不带验证输出（脚本/agent 内部已写文件）
python core/scripts/plan_tracker.py step "$PLAN_ID" --n <step_num>

# 带 expected_outputs 验证（plan 模板里定义了 expected_outputs 的步骤）
python core/scripts/plan_tracker.py step "$PLAN_ID" --n <step_num> --output "_数据库/.wal/第<ch>章_summary.json"
```

步骤号严格对齐 `core/claude-home/plans/save-state.plan.json` 模板的 `n` 字段（1-12）。

### 末尾：plan-end（流水线终点验收）

```bash
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

- exit 0 → 所有 required 步骤已 completed → 流水线交付
- exit 2 → 列出未完成的 required 步骤 → **整个 save-state 视为失败**，必须修复后重跑

### Agent 调用强制注入字段

所有 Agent 工具调用 prompt 内**必须包含**：

```
PLAN_ID: $PLAN_ID
STEP: <对应步骤号>
```

否则 `core/claude-home/hooks/pretooluse_agent_gate.py` 拦截 exit 2。

### 兼容性

- 旧项目（无 `_数据库/.plans/` 目录）：plan_tracker.py 自动创建
- 旧 WAL（`_数据库/.wal/`）保留不变，与 plan 共存不冲突
- 用户手动调用 `/save-state` 时**必须先 plan create**——不能跳过

---

# 流水线架构

```
save_state.py --wal-start        （WAL 开启）
     ↓
save_state.py --parse            （v18：读 第NNN章_changes.json 的 factual 段；旧稿走分隔符兜底）
     ↓ 失败则用 AI agent 兜底
save_state.py --apply-changes    （落地 CHANGES 到 13 个 JSON · 机械）
     ↓
validate_chapter.py              （一致性校验 · hard_gate 复核）
     ↓
novel-summarizer agent           （200 字摘要 + 情绪评分）
     ↓
novel-foreshadower agent         （伏笔评估 · 可选）
     ↓
novel-reflector agent            （写作反思 · 可选）
     ↓
合并 .wal/ 到正式数据库         （summary 合并 + learning_loop.py --merge-reflection）
     ↓
[每 5 章] audit_consistency.py   （累积漂移审计）
     ↓
save_state.py --git-commit       （快照）
     ↓
save_state.py --report           （报告）
     ↓
novel-outline-planner agent      （下一章走向卡片）
     ↓
展示卡片给用户
```

## Agent 调用规范（硬性）

**所有 Agent 工具调用必须使用 `.claude/templates/agent_call_templates.md` 中的模板**。

- 只传契约字段（PROJECT / CHAPTER / MODE 等）
- 不在 prompt 里塞规则或数据内容
- 模板中所有 `<...>` 占位符必须替换为真实值
- Hook 会拦截不合规调用

## v19 顾问制（save-state 环节的衔接）

v19 把检测体系改成「顾问制」：检测工具输出「待裁决项」而非判决，AI 对 `advisory` 项有充分理由可豁免，`hard_gate` 项（E 层一致性 + 文件契约破损）不可豁免。完整说明见 `core/claude-home/STRUCTURE.md` 第十一节「v19 检测体系顾问制 + hard_gate 不可豁免清单」。

save-state 是写后流水线，顾问制对它的衔接有两处：

1. **第 6 步 Foreshadower** 输出的 JudgeReport 含 `waivers` 段——foreshadower 对 advisory 类发现（chekhov 候选、健康度预警）可豁免，但 Tier-1 未回收 / 秘密未揭这类 hard_gate 发现不可豁免（这也是第 4 步 `validate_chapter` 的 hard_gate 项）。
2. **豁免统计的入口在 write-chapter，不在 save-state**——write-chapter 第 3.8 步的 `audit_hub` 跑完会自动调 `learning_loop.py --ingest`，由它统计 `waivers`：同一 advisory code 在同类章节被反复合理豁免，learning_loop 产出「工具校准建议」写入 `写作经验.json` 的 `tool_calibration_suggestions` 段，反向校准工具阈值，而不是反复让 AI 豁免。save-state 第 8 步的 `learning_loop --merge-reflection` **不重复做 waivers 统计**，只合并 reflection。

作为调度器你不做裁决，但要在第 11 步报告里如实呈现 foreshadower 的 waivers 项（被豁免的发现 + 理由）。

---

# 第 1 步：开启 WAL

```bash
python core/scripts/save_state.py "<项目路径>" --wal-start <N>
```

完成后立即打勾（带 expected_outputs 验证）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 \
  --output "_数据库/.wal/第<N>章_save_state.json"
```

---

# 第 2 步：解析 CHANGES（v18 正文/数据分离）

```bash
python core/scripts/save_state.py "<项目路径>" --parse <N>
```

**v18 变更**：章节正文和 CHANGES 已是两个物理文件——CHANGES 由 `novel-writer` 直接写成结构化 `章节/第<NNN>章/第<NNN>章_changes.json`（顶层 `{factual, self_eval}`）。`--parse` 通过 `core/scripts/chapter_io.py` 的 `read_changes()` 读取，**策略 1/2 退化为旧稿兼容路径**：

- **v18 主路径**：`_changes.json` 存在 → `read_changes()` 直接读 `factual` 段（已是合法 JSON，无需修复）。9 类字段名由 writer 契约保证一致。
- **策略 1（旧稿严格解析）**：`_changes.json` 不存在但章节 txt 是旧的混合格式 → `read_changes()` 从 txt 的 `---CHANGES_FACTUAL---` / `---CHANGES---` 到 `---END---` 之间解析 JSON。
- **策略 2（旧稿宽松修复）**：旧混合 txt 解析失败时尝试修复尾逗号 `,}`、单引号、字段名模糊匹配。

> 建议：旧项目先跑 `python core/scripts/chapter_io.py migrate "<项目路径>" <N>` 把旧混合 txt 一次性拆成 txt + `_changes.json`，之后 `--parse` 走 v18 主路径。

exit 结果：
- exit 0（v18 主路径 / 旧稿策略 1 或 2 成功）→ 继续
- exit 1（`_changes.json` 缺失且旧稿策略 1-2 都失败）→ **启动 Agent 兜底**：
  ```
  用 general-purpose agent，prompt:
  PROJECT: <项目路径>
  CHAPTER: <N>
  MODE: changes-fallback-parse
  PLAN_ID: $PLAN_ID
  STEP: 2
  任务：优先 Read 章节/第<NNN>章/第<NNN>章_changes.json；若不存在则 Read 章节正文
  txt 找 ---CHANGES--- 到 ---END--- 之间的内容。修复 JSON 格式错误，确保 9 个 factual
  字段名完全正确，写入 _数据库/.wal/第<N>章_parsed.json（结构含 factual 段）
  ```

完成后打勾（兜底产物作为输出证据）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2 \
  --output "_数据库/.wal/第<N>章_parsed.json"
```

---

# 第 3 步：应用 CHANGES（结构化更新）

```bash
python core/scripts/save_state.py "<项目路径>" --apply-changes <N>
```

脚本负责：伏笔表 / 进度 / 地图 / 时间线 / 道具 的机械更新。

如有 warnings（引用了不存在的 id 等）→ 输出给用户，但不阻塞流水线。

完成后打勾：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3
```

---

# 第 4 步：一致性校验（v19：hard_gate 复核）

```bash
python core/scripts/validate_chapter.py "<项目路径>" <N>
```

`validate_chapter` 产出的问题以 **hard_gate 项为主**（E 层一致性 + 文件契约：`LOCKED_FACT_CONFLICT` / `FUTURE_KNOWLEDGE_LEAK` / `FORESHADOWING_NOT_PAID` / `SECRET_NOT_REVEALED` / `UNKNOWN_CHARACTER_DETECTED` / `CHANGES_MISSING` 等，完整清单见 STRUCTURE.md 第十一节）。这些是**客观错误，不可豁免**——v19 顾问制对它们不适用。

- exit 0 → 继续
- exit 1/2 → 命中 hard_gate 类错误，说明 write-chapter 阶段没修干净（不该在 save-state 时才发现），停止并报告。**不要在这里靠豁免放行**——hard_gate 不可豁免。

校验通过后打勾：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4
```

---

# 第 5 步：启动 Summarizer agent

```
用 Agent 工具启动 novel-summarizer：
PROJECT: <项目路径>
CHAPTER: <N>
MODE: summarize
PLAN_ID: $PLAN_ID
STEP: 5
```

完成后检查 `_数据库/.wal/第<N>章_summary.json` 是否生成，然后打勾（带 expected_outputs 验证）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5 \
  --output "_数据库/.wal/第<N>章_summary.json"
```

---

# 第 5.5 步：声纹跨章统计收集（v16 新增 · 移植自外部工艺库声纹矩阵）

用 Bash 运行 style_analyzer.py 提取本章每个角色的对话特征，追加到声纹追踪文件：

```bash
python -c "
import json, re, sys
from pathlib import Path
sys.path.insert(0, 'core/scripts')
import chapter_io

root = Path('<项目路径>')
ch = <N>

# 读取章节正文（v18：走 chapter_io 统一读取，自动剥离旧混合 txt 的 CHANGES 段）
try:
    body = chapter_io.read_body(str(root), ch)
except FileNotFoundError:
    sys.exit(0)

# 读取人物卡获取已知角色名
cards = json.loads((root/'_数据库/人物卡.json').read_text(encoding='utf-8')).get('characters', []) if (root/'_数据库/人物卡.json').exists() else []
names = {c.get('name') for c in cards if c.get('name')}

# 提取每个角色的对话行
char_dialogues = {}
for name in names:
    pattern = rf'[""「]([^""」]+)[""」][^，。]*{re.escape(name)}|{re.escape(name)}[^，。]*[""「]([^""」]+)[""」]'
    for m in re.finditer(pattern, body):
        text = m.group(1) or m.group(2) or ''
        if text:
            char_dialogues.setdefault(name, []).append(text)

# 计算每个角色的对话统计
stats = {}
for name, lines in char_dialogues.items():
    if not lines: continue
    lengths = [len(l) for l in lines]
    stats[name] = {
        'chapter': ch,
        'line_count': len(lines),
        'avg_length': round(sum(lengths)/len(lengths), 1),
        'max_length': max(lengths),
        'min_length': min(lengths),
    }

# 追加到声纹追踪文件
vp_path = root / '_数据库' / '.audit' / 'voiceprint_tracking.json'
vp_path.parent.mkdir(parents=True, exist_ok=True)
existing = json.loads(vp_path.read_text(encoding='utf-8')) if vp_path.exists() else {'chapters': {}}
existing['chapters'][str(ch)] = stats
vp_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'声纹统计: {len(stats)} 个角色')
"
```

此步骤不阻塞流水线——仅收集数据。数据用于：
- audit_consistency.py 的跨章声纹漂移检测
- /check-quality 的角色一致性报告
- novel-voice-keeper 的历史参考

---

# 第 6 步（条件必选）：启动 Foreshadower agent

**触发条件（v16 升级：从"可选"改为"条件必选"）：**
- 本章 `第<NNN>章_changes.json` 的 `factual.foreshadowing_actions` 非空 → **必须触发**
- 第 4 步 validate_chapter 曾报出 FORESHADOWING_NOT_PAID 错误（即使已被 validator-repair 修复）→ **必须触发**（修复的伏笔回收质量需要审查）
- 以上两个条件都不满足 → 跳过

```
Agent 启动 novel-foreshadower：
PROJECT: <项目路径>
CHAPTER: <N>
MODE: foreshadow-review
PLAN_ID: $PLAN_ID
STEP: 6
```

产出 `_数据库/.wal/第<N>章_foreshadow_review.json`（JSON 建议）。
不阻塞流水线——即使评分低也继续，由用户决定是否重写。

触发了就打勾，未触发就跳过（plan 第 6 步标 optional）：

```bash
# 触发情况
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 6 \
  --output "_数据库/.wal/第<N>章_foreshadow_review.json"

# 未触发：可不调用 step，plan 模板 optional 步骤不阻塞 end
# 若想留显式审计痕迹，可用 --skip-output 走 step 但不验证文件
```

---

# 第 7 步（可选）：启动 Reflector agent

```
Agent 启动 novel-reflector：
PROJECT: <项目路径>
CHAPTER: <N>
MODE: reflect
PLAN_ID: $PLAN_ID
STEP: 7
```

产出 `_数据库/.wal/第<N>章_reflection.json`（可能为空列表）。

完成后打勾（可选步骤，未触发可跳过）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7 \
  --output "_数据库/.wal/第<N>章_reflection.json"
```

---

# 第 8 步：合并 WAL 到正式数据库

用 Bash 完成以下合并（确定性操作）：

```bash
# 合并 summary 到 章纲摘要.json
python -c "
import json
from pathlib import Path
root = Path('<项目路径>')
summary = json.loads((root/'_数据库/.wal/第<N>章_summary.json').read_text(encoding='utf-8'))
db = root/'_数据库/章纲摘要.json'
d = json.loads(db.read_text(encoding='utf-8')) if db.exists() else {'chapters': []}
d.setdefault('chapters', []).append(summary)
db.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
"

# 合并 reflection 到 写作经验.json —— v18：调 learning_loop.py，不再手工合并
# learning_loop 会把 reflection 的 entries 按 category 分流进
# success_patterns / failure_patterns，字段自动对齐
# （第 7 步 Reflector 未触发 / reflection.json 不存在时跳过本行）
python core/scripts/learning_loop.py "<项目路径>" --merge-reflection "<项目路径>/_数据库/.wal/第<N>章_reflection.json"

# 合并 foreshadow_review 的建议（如用户接受）
```

> **learning_loop.py 退出码**：0=正常合并 / 1=检测到复发问题已升级约束（不阻塞，记入报告）。
> 写作经验.json 权威结构：`{success_patterns:[], failure_patterns:[], preferences:[], _recurrence_tracker:{}}`。
> `failure_pattern` 中 `confidence >= 0.5` 的会被 `build_manifest.experience_entries()` 自动注入下一章 writer。
> reflector（agent）只负责**产出** reflection.json；learning_loop（脚本）负责**收口**——统一字段 + 复发升级 + 合并入库。两者协同不替代。

合并完成后打勾：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 8
```

---

# 第 9 步：每 5 章深度维护

```bash
# 检查 completed % 5 == 0
python -c "
import json
d = json.loads(open('<项目路径>/_数据库/进度.json', encoding='utf-8').read())
exit(0 if d.get('completed', 0) % 5 == 0 else 1)
"

# 如果 exit 0：触发审计（v16：窗口边界保护）
python core/scripts/audit_consistency.py "<项目路径>" --window 5
# 注意：audit_consistency.py 内部会 clamp window 到可用章节数（min(5, completed)），
# 前 5 章不会因为窗口越界而崩溃

# v16 新增：同时触发风格漂移检测
python core/scripts/style_drift_tracker.py "<项目路径>" --window 5
# 检测最近 5 章的风格指标（对话占比/逗句比/拟声词数/段落均长）是否偏离基线
# exit 0 → 无漂移
# exit 1 → WARN（漂移但在容忍范围内，记录到报告）
# exit 2 → 严重漂移，建议用 /distill-style --micro-refine 重新校准

# v18 新增：跨章复发问题扫描（learning_loop 自学习闭环）
python core/scripts/learning_loop.py "<项目路径>" --scan-recurring
# 扫最近若干章的 audit 报告，把反复出现的同类问题升级为高 confidence failure_pattern
# exit 0 → 正常 / exit 1 → 检测到复发问题已升级约束（不阻塞，记入报告）
```

- audit exit 0/1 → 继续
- audit exit 2（发现 error 或 fatal）→ **阻塞流水线**，展示审计报告给用户，**自动建议 /reconcile**（不再靠用户记得手动调用）
- drift exit 2 → 追加到报告中，建议 /distill-style --micro-refine
- learning_loop --scan-recurring exit 1 → 追加到报告中（复发问题已自动升级约束，下章 writer 会收到更强约束）

审计通过（或非 5 倍数章节跳过审计）后打勾：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 9
```

---

# 第 10 步：Git 快照

```bash
python core/scripts/save_state.py "<项目路径>" --git-commit <N>
```

提交成功后打勾：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 10
```

---

# 第 11 步：报告 + 走向卡片

```bash
python core/scripts/save_state.py "<项目路径>" --report <N>
```

然后启动 Outline-Planner：

```
Agent 启动 novel-outline-planner：
PROJECT: <项目路径>
CURRENT_CHAPTER: <N>
MODE: plan-next
PLAN_ID: $PLAN_ID
STEP: 11
```

### 【v19.2 新增】Meta-Judge 触发（每 10 章）

**条件**：`N % 10 == 0` 时（ch10/ch20/ch30/...），在 outline-planner 之后**额外**启动 novel-meta-judge：

```
Agent 启动 novel-meta-judge：
PROJECT: <项目路径>
RANGE: <N-9>-<N>
MODE: routine
PLAN_ID: $PLAN_ID
STEP: 11
```

Meta-judge 会扫描 章纲摘要.json[N-9..N].judge_reports[]，输出：
- 每 judge 的 a_rate / mean_confidence / drift_signal
- judge 间一致性矩阵
- 漂移警报 + 校准建议

报告存到 `_数据库/.meta_judge/meta_judge_ch{N-9}_to_ch{N}.json`，**不阻塞 plan**——只是元质量信号。

**前提**：judge_reports_archive.py 已在 step 9 跑过（v19.2 起自动），章纲摘要 judge_reports 字段有数据。

展示 2-3 张走向卡片给用户，等待选择。

### 【v19.4 新增】audit_dashboard 摘要

走向卡片之前，先输出一段 audit_dashboard 摘要给用户：

```bash
python core/scripts/audit_dashboard.py "<项目路径>" | tail -25
```

包含：
- 各章 grade 矩阵（audit-hub / judge_reports）
- 最新 5 个跨章扫描结果 + warning/advisory 计数
- Top 5 复发问题
- 工具校准建议状态
- 元健康度（高复发 / 已升级数）

这是用户对本章质量与全书趋势的**唯一聚合视图**。


走向卡片产出后打勾：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 11 \
  --output "_数据库/.cards/第<N>章_next.json"
```

---

# 第 12 步：关闭 WAL + plan 最终校验

```bash
python core/scripts/save_state.py "<项目路径>" --wal-end <N>
```

WAL 关闭后打勾本步：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 12
```

**然后立刻执行 plan-end —— 这是整条流水线的最终验收：**

```bash
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

- exit 0 → 所有 required 步骤已 completed → save-state 整体交付成功
- exit 2 → 列出未完成的 required 步骤 → **整条流水线视为失败**，必须排查未打勾的步骤
- 任何遗漏的 required 步骤都会在这里暴露——这是闭环的最后一道闸门

**WAL 恢复机制（v16 文档化 · Issue 13）：**
如果流水线在第 1-12 步之间崩溃，`/continue` 命令会检查 `_数据库/.wal/` 目录：
- 存在 `wal_start_chN.json` 但无 `wal_end_chN.json` → 说明第 N 章 save-state 未完成
- `/continue` 读取 `wal_start_chN.json` 中的 `completed_steps` 字段，从下一步恢复
- 已完成的步骤不会重新执行（幂等保证）
- 如果 `completed_steps` 为空或文件损坏 → 从第 1 步重跑整个 save-state

---

# 硬性纪律（调度器的边界）

- **你不解析 CHANGES** — save_state.py --parse 的事
- **你不更新任何 _数据库/*.json** — 脚本或 agent 的事
- **你不写摘要/反思/卡片** — 专精 agent 的事
- **你不跳步**（除标注为可选的 6/7 步）
- **你不在失败时沉默** — 每个失败都要报告给用户

你能做的：
1. Bash 调用脚本
2. Agent 工具启动专精 agent
3. 根据返回决定下一步
4. 合并 WAL 到正式库（纯数据合并，无创造）
5. 向用户展示卡片、审计、报告

---

# 失败逃生舱

完整流水线崩溃时 fallback 到 v1：

```bash
cat .claude/backup/save-state.v1.md
```

---

## 📋 完成检查清单（v17.2 · Phase 3.1）

执行结束前确认（任何一项 NO → 流水线未交付）：

- [ ] `plan create` 已在开头执行，拿到 `$PLAN_ID`
- [ ] `plan step` 已对 required 步骤（1, 2, 3, 4, 8, 9, 10, 11, 12）全部打勾
- [ ] `plan end "$PLAN_ID"` 已执行且 exit 0
- [ ] `plan status "$PLAN_ID"` 显示所有 required 步骤为 `completed`
- [ ] 13 个 `_数据库/*.json` 已被本次 `--apply-changes` 落地
- [ ] `_数据库/.cards/第<N>章_next.json` 已生成（第 11 步产物）
- [ ] `_数据库/.wal/第<N>章_save_state.json` 标记 `wal_end` 时间戳（第 12 步）
- [ ] git commit 成功（第 10 步）

所有 Agent 调用 prompt 必须包含 `PLAN_ID: $PLAN_ID` 和 `STEP: <对应步骤号>` —— 缺失会被 pretooluse hook 拦截 exit 2。
