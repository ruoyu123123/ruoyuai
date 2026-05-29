---
description: 当用户提供参考小说链接/文件/路径，想把该作者的写作风格作为后续写作基线时使用；产出可复用的风格 SKILL.md 注入 writer agent
allowed-tools: [WebFetch, WebSearch, Bash, Read, Write, Edit, Grep, Glob]
---

你是一位写作风格分析专家。请从以下参考小说中**闭环蒸馏**出作者的完整写作风格：

$ARGUMENTS

> **三段式纪律**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [core/claude-home/HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。
> hook 自动检 research_cache 存在 + 反思文件，缺失提示补救（不破坏主流程）。
> 关键脚本输出建议过 `ai_wrapper.py` 二次复核（避免规则误判）。

---

# 🚨 Step 1 必做项（cluster 颗粒度规则）

颗粒度规则：**故事块自适应（3-6 章 / 4000-20000 字）**，由 `cluster_segmenter.py` 切分。不要按章号取模 / 固定 N 章窗口。

1. **第一动作**：Read `workspace/styles/<书名>/cluster_index.json`
   - **场景 A**（重蒸已蒸馏书）：存在 → 直接复用 `clusters[]`（含 strong 边界）
   - **场景 B**（新蒸馏 / 全清重做）：不存在 → 跑 `python core/scripts/cluster_segmenter.py --project workspace/styles/<书名>`
   - ⚠️ 场景 B 首轮 cluster_index 的 `boundary_reason_distribution` 必然 `strong=0` 全是 `max_chapters`——**这是设计，不是 bug**（segmenter 是 retroactive 工具，依赖 continuity 数据识别 strong）
2. **Agent 调度循环（A' 半 cluster 模式 · 2026-05-24 实证）**：
   每个 cluster 拆为 **3 个 sub-agent**（防 stream idle / session limit 超时）：
   ```
   FOR cluster in cluster_index.clusters:
     half = cluster.chapters_count // 2  # 通常 3
     # Agent 1: 前半单章 JSON（ch_a ~ ch_a+half-1）
     # Agent 2: 后半单章 JSON（ch_a+half ~ ch_b）
     # Agent 3: 整 cluster 衔接 JSON（基于已落盘的单章 JSON + 原文）
   ```
   - **单 agent 目标耗时 ≤10 min**（实测 3-8 min）
   - **并行数 ≤3**（防配额打满）
   - 每完成 1 章立即 Write（防超时丢失）
   - 衔接 agent 在前两个 agent 完成后启动（依赖单章 JSON）
   - **失败重试代价 = 1/3 cluster**（而非整 cluster）
   
   **为什么不用单 cluster 1 agent**（历史教训 2026-05-24）：
   - Claude Code sub-agent stream idle timeout 5 min + wall-clock ~25 min 硬阈值
   - 6 章 agent 实测 100% 失败率（6/6 全 502 EOF）
   - 3 章 agent 实测 100% 成功率（8/8 全通过，3-8 min）
   
3. **单 cluster agent** 任务：读 ≤3 章原文 → 产单章 JSON × 3（前半/后半各一波）；衔接 agent 读全 cluster 单章 JSON → 产 cluster 衔接 JSON × 1
4. **Agent prompt 必带契约字段**：`PLAN_ID` + `STEP` + `CLUSTER_ID` + `CHAPTER_RANGE`
5. **场景 B 必做的 retro-refine**：表层蒸馏全部完成后**重跑** segmenter → cluster_index 升级到含 strong 边界 → 再跑 arc_aggregator
6. **后置聚合**：`python core/scripts/arc_aggregator.py --project ... --all-clusters` 产 cluster_arc（主轨）+ 副轨 fixed10

业界依据：LumberChunker (EMNLP 2024) +7.37% DCG@20 / MARCUS 事件中心 / Multi-Agent TV Arcs 自然终结

---

## 衔接分析 JSON Schema（continuity）

每个 3 章 agent 必须输出到 `_数据库/衔接分析/ch{N}_{N+2}_continuity.json`：

```json
{
  "chapter_range": "ch{N}-{N+2}",
  "transitions": [
    {
      "from": <N>, "to": <N+1>,
      "connection_type": "直接承接|信息炸弹→静默回响|时间跳跃|空间跳转|情绪落差|悬念承接新视角|其他",
      "method_detail": "<具体衔接手法>",
      "time_gap": "<同一时空|几分钟后|几小时后|几天后|时间跳跃X>",
      "space_change": "<同一地点|相邻空间|跨城|跨场景>",
      "pov_change": "<视角延续|视角切换至XX>",
      "emotional_carry_over": "<情绪延续/对冲/落差类型>"
    },
    { "from": <N+1>, "to": <N+2>, ... }
  ],
  "opening_type_sequence": [<3 个开头类型>],
  "ending_type_sequence": [<3 个章末类型>],
  "consecutive_repeat_flags": { "openings": "...", "endings": "..." },
  "foreshadowing": {
    "planted": [{"chapter": <>, "item": ""}],
    "resolved": [{"chapter": <>, "item": "", "planted_in": ""}],
    "carry_over": [{"item": ""}]
  },
  "character_continuity": [{"character": "", "first_appear_in": "", "arc_progress": ""}],
  "G_dimension_proposals": [
    {
      "_doc": "自学习升级字段：agent 觉得现有 35+ 维度没覆盖但本章观察到的现象，结构化提议。dimension_evolver.py 会聚合跨章提议 → 通过双门槛升级到 auto_evolved_dimensions.json",
      "proposed_dim_name": "<提议的 dim 名，如 dim50_metaphor_compression_ratio>",
      "observation": "<本章观察的具体现象 < 100 字>",
      "current_dims_missing": "<现有哪些维度本应覆盖但没覆盖到 / 与已有 dim 的区别>",
      "value_assessment": "high|mid|low",
      "estimated_appearance_rate": "<估计每章/每 N 章出现 / 仅特定章型>",
      "suggested_extraction_method": "<怎么自动检测，如正则/字数/比例/标签>",
      "category_proposal": "B7_self_discovered"
    }
  ],
  "character_emotion_delta": [
    {
      "character": "<角色名>",
      "chapter_changes": [
        {
          "chapter": <N>,
          "actor_emotion": {"label": "警觉|愤怒|决断|...", "delta": <-1.0 to 1.0>},
          "experiencer_emotion": {"label": "恐惧|迷茫|压力|...", "delta": <-1.0 to 1.0>},
          "trigger_event": "<本章触发该角色情感变化的具体事件>",
          "stage_progress": "<本章末该角色 arc stage，如「迷茫 → 入局」>"
        }
      ]
    }
  ],
  "running_motifs": [{"motif": "", "frequency": <int>, "across_chapters": []}],
  "voice_pack_observations": [{"character": "", "dialogue_avg_len": <int>, "signature_phrases": [], "consistency_grade": "A|B|C"}],
  "pacing_curve": "<3 章整体节奏曲线>",
  "narrative_continuity_template": "<可复用衔接组合模板，用于教 AI 写连续章节>"
}
```

## 聚合时新增"衔接模板节"

每 30 章聚合时（10 个 continuity JSON），主代理必须额外做：
- 衔接类型分布统计（直接承接/信息炸弹回响/时间跳跃/... 各占多少）
- 高频伏笔回收延迟（最常 N 章后回收）
- 跨章符号热点（最常出现的意象 / 道具 / 短语）
- narrative_continuity_template 入库（让复刻阶段 AI 写连续 3-5 章时有套路可循）

---

# 程序化量化 + 闭环蒸馏

## 程序化工具（必须使用）

**以下 Python 脚本是蒸馏流程的核心基础设施，禁止跳过：**

| 工具 | 路径 | 用途 | 调用时机 |
|------|------|------|----------|
| **style_analyzer.py** | `core/scripts/style_analyzer.py` | 精确统计句长/段落/标点/功能词/对话占比 | 阶段1每章分析、阶段3量化对比 |
| **style_evaluator.py** | `core/scripts/style_evaluator.py` | 原文 vs 复刻的 SFS 评分（0-100） | 阶段3对比、收敛判定 |
| **validate_style.py** | `core/scripts/validate_style.py` | 风格合规校验（PASS/WARN/FAIL） | 阶段2复刻后校验 |

**调用示例**：
```bash
# 阶段1：子代理分析单章后跑精确统计
python core/scripts/style_analyzer.py "原文/第1章.txt" --output "_数据库/蒸馏进度/ch01_metrics.json"

# 阶段3：对比原文和复刻样本（🔴 v23.13 起默认走多基线 · 防单 ref 失真）
python core/scripts/style_evaluator.py \
  --gen "workspace/styles/<书名>/复刻测试/v0/test1_opening.txt" \
  --multi-ref-from-dir "workspace/styles/<书名>/原文" \
  --multi-ref-count 5 \
  --output "workspace/styles/<书名>/对比报告/eval_v0.json"

# 阶段2：复刻后校验
python core/scripts/validate_style.py "风格库/复刻测试/v0/test1_opening.txt" --strict
```

**⚠️ phase-3 评分硬约束**（v23.13 · 2026-05-27 加 · lessons L19.1）：
- **数据 ≥ 60 章时**：必须用 `--multi-ref-from-dir` 多基线模式（单 ref 评分对高方差作者必失真）
- **数据 < 30 章早期**：可用 `--ref <single>` 单基线（数据不足以构建有意义区间）
- **特意验证某章型**：单 ref 模式 OK（如要测"开头章"复刻效果，单 ref 用 ch001）
- **违反后果**：v0/v1 真分 84/83 被误评为 67/69，浪费 phase-4 修正反思（蛊真人 2026-05-27 翻车案）

**LLM 自报 vs 程序化统计的分工**：
- 句长/段落/标点/功能词/对话占比/禁用词 → **必须用 Python 脚本**（LLM 误差 30-50%）
- 叙事结构/情绪节奏/对话风格/角色声纹 → LLM 定性分析

---

# 闭环蒸馏核心原则

**单次蒸馏 = 无用功。** 读完原文产出一个 JSON 就结束，那只是表层提取，无法学到作者真正的能力。

**真正的蒸馏 = 蒸馏 → 复刻 → 对比 → 反思 → 再蒸馏 → 再复刻 …… 直到差距收敛。**

历史教训（《精神病院的隔壁是仙门》v10-v14 真实经历）：
- v10 单次蒸馏 → 写 Ch01 → 发现冷过头（清光配额词丢三九味）→ v11
- v11 + Ch01-05 → 发现模板化（5 章首句全是"沧梧市梅雨季的第X天"）→ v12
- v12 + Ch06 → 发现隐性差距（缺战斗/情绪爆发/方括号设定/家庭锚点）→ v13
- v13 深读对比 → 发现深层差距（设定投放章组合/对话占比 40% vs 70%/抒情段切碎/【祂】密度）→ v14

**每一轮升级都来自"复刻一段→和原文对比→反思差距"。如果没有复刻测试，永远停留在 v10。**

---

# 闭环蒸馏总览（v2 章程 · 8 阶段）

> **v2 章程**（2026-05-26 拍板 · memory `feedback_cluster_distill_v2_charter`）：
> - 复刻闭环对齐 cluster 主轨 —— **废除 drill 单段 1200 字 6-type 模式**（opening/battle/psychology/dialogue/description/transition）
> - 复刻只剩**两档**：chapter（3000-5000 字 v1→v2 中检）+ cluster（4000-20000 字 v2+ 终验）
> - **Article 6 严闭环**：出货前必须做写作端回灌验证（用 skill_FINAL 灌 gen_writer.py 写同 cluster → arc/SFS 对比），不通过 = 不出货

```
[阶段 1] 表层蒸馏（cluster 主轨 · 不变）
   ↓ 输出：skill v0 + cluster 单章 JSON + 衔接分析
[阶段 2] chapter 复刻中检（v2 改 · 替代 drill 单段）—— 用 distill_replicate.py --mode chapter 复刻 1-2 个参考章
   ↓ 输出：chapter 复刻样本（3000-5000 字）
[阶段 3] 多维度对比扫描 —— 抽 2-3 章原文 + style_evaluator SFS 评分
   ↓ 输出：差距报告
[阶段 4] 修正反思 —— 差距维度生成新约束 → skill v1
   ↓ 输出：skill v1 + lessons_learned
[阶段 5] cluster 终验复刻（v2 新增 · 主推）—— 用 distill_replicate.py --mode cluster 复刻 1-2 个完整故事块
   ↓ 输出：cluster 复刻样本（4000-20000 字 · sub-call 拆分防 timeout）
   ↓ 终止条件：连续 2 轮无新差距 + cluster SFS ≥ 80
[阶段 6] 出货 —— _FINAL 四件套 + git commit
   ↓ 输出：作者风格_FINAL.json + skill_FINAL.md + distillation_log.md
[阶段 7] 写作端回灌严闭环（v2 新增 · 严 · Article 6）—— skill_FINAL 灌 gen_writer.py 写同 cluster → arc/SFS 对比
   ↓ 不通过 → distill_finalize_verify.py exit 2 → plan end 拦截
   ↓ 输出：writer_feedback_verify.json
```

**单次蒸馏 ≤ 阶段 1 = 错误的蒸馏。完整蒸馏必须跑完阶段 7。**

**⚠️ plan 强制规划下的硬约束**：
- 阶段 1 完成后**强制执行 `plan_tracker.py step --n 2`**，但 `plan_tracker.py end` **必须 7 步（plan step 1..7）全部完成才允许**（否则 exit 2）；其中 **step 7 含写作端回灌严闭环**——出货前先过 `distill_finalize_verify.py --strict`，回灌 exit 0 才落 step 7。
- 跳过任何 required 步骤直接调 `plan-end` → 脚本拦截 → 禁止声称"蒸馏完成"。
- **新增阶段 7 写作端回灌**是 v2 章程 Article 6 的严闭环 —— 即使阶段 6 _FINAL 文件齐全，回灌测试不通过 `distill_finalize_verify.py` 也会让 plan end 拦在出货前。
- 这是从命令调度层兜底，防止 Agent 跑完阶段 1 表层蒸馏就交差，也防止 skill 在 Claude 上"看着像"但 gen-model 写不出。

---

# 风格蒸馏流程

## 📁 文件路径规范

**本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第二节「风格库结构」**。

- 风格项目根：`<REPO_ROOT>/workspace/styles/{书名}/`
- 单章蒸馏：`蒸馏进度/ch{N}.json`
- 衔接分析：`衔接分析/ch{N}_{N+2}_continuity.json`
- 复刻测试：`复刻测试/v{X}_round{Y}/{type}_replica.txt`
- 对比报告：`对比报告/eval_v{X}_{type}.json`
- 终版交付：`作者风格_FINAL.json` + `skill_FINAL.md` + `distillation_log.md` + `README.md`

**禁止**：用旧路径 `风格库/...` 或 `_数据库/蒸馏进度/...`。

---

## 🛡️ Plan 强制规划（防跳阶段）

**核心问题**：`/distill-style` 是 **6 阶段闭环**——阶段 1 只是"表层蒸馏"，跳过阶段 2-6 = 蒸馏失败。历史上多次出现"跑完阶段 1 就声称完成"的事故（典型如《BookC》蒸馏只产出 skill v0 就 commit）。Plan 强制规划层从命令调度层兜底，防止跳阶段交付。

### 开工前强制生成 plan（必须）

```bash
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command distill-style \
  --project "<书名>" \
  --key "v<version>_<round>")
echo "PLAN_ID=$PLAN_ID"
```

- `--project` 必填：取风格库书名（如 `BookC`），脚本会自动解析到 `workspace/styles/<书名>/`
- `--key` 示例：`v3_round1` / `v3.1_round2`（用于区分同一本书多次蒸馏）
- 输出的 `PLAN_ID` 必须保存到环境变量供后续阶段使用

### plan-step 阶段映射（铁律 · v2 章程 8 step）

| 阶段编号 | 对应文档章节 | plan step n | expected_outputs |
|---|---|---|---|
| 阶段 0 | 读经验库 / 预处理（**必读 cluster_index.json**）| `--n 1` | 无（用 `--skip-output`） |
| 阶段 1 | 表层蒸馏（cluster agent + cluster 衔接 + arc 聚合 + skill v0）| `--n 2` | `workspace/styles/<书名>/作者风格.json` |
| 阶段 2 | chapter 复刻中检（`distill_replicate.py --mode chapter`）| `--n 3` | `复刻测试/.../chapter_replica.txt` |
| 阶段 3 | 多维度对比扫描 + SFS 评分（chapter SFS / cluster mode 6 维）| `--n 4` | `对比报告/distillation_compare_v{N}.json` |
| 阶段 4 | 修正反思 → skill v{N+1} | `--n 5` | 无（用 `--skip-output`，skill 升级是 Edit/Write） |
| 阶段 5 | cluster 终验复刻（`distill_replicate.py --mode cluster`）| `--n 6` | `复刻测试/.../cluster_<id>_replica.txt` |
| 阶段 6 | 出货（_FINAL 四件套 + git commit）·**出货前必先过阶段 7 回灌门槛** | `--n 7` | `作者风格_FINAL.json` + `skill_FINAL.md` + `distillation_log.md` |
| 阶段 7 | 写作端回灌严闭环（`distill_finalize_verify.py --strict`）·**并入 step 7 出货门槛，不单独占 plan step**（plan 仅 7 步） | 含于 `--n 7`（回灌 exit 0 才落 step 7） | `对比报告/writer_feedback_verify.json` |

每阶段尾必须执行：
```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n <阶段号> [--output <文件>|--skip-output]
```

### plan-end 兜底检查（出货前最后一道闸 · v2 阶段 7 是真闸）

阶段 7 写作端回灌验证完成后**必须**：
```bash
python core/scripts/plan_tracker.py end "$PLAN_ID"
# exit 0  → 所有 7 required 步骤通过（step 7 含阶段 7 回灌验证），允许声称"蒸馏完成"
# exit 2  → 任一 required 步骤未跑 / 阶段 7 验证失败，禁止声称完成
```

**特别强调（红线 · v2 章程）**：
- ❌ **阶段 1 完成 ≠ 命令完成**。阶段 2-7（chapter 复刻 → 对比 → 修正 → cluster 复刻 → 出货 → 写作端回灌）每个都必须 step。
- ❌ **阶段 6 _FINAL 文件齐全 ≠ 出货完成**。阶段 7 `distill_finalize_verify.py` 必须 exit 0 才算真出货（v2 Article 6 严闭环）。
- ❌ drill 单段模式（旧 --type opening|battle|...）**已废弃**，仅 `--legacy-segment-only` 紧急救火可用（留 lesson）。
- ❌ 跳过任何 required 步骤直接调 `plan-end` → 脚本返回 exit 2，**禁止声称蒸馏完成**。
- ❌ `lessons L2.7`：300 章上限触发分批时，必须先 `plan_tracker.py abort` 旧 plan，再为新批次 create 新 plan，禁止跨批次复用 plan_id。

### 与 PreToolUse Hook 的协作

Hook 已强制要求所有蒸馏 Agent 子代理 prompt 必须含 `PLAN_ID` 字段。所有蒸馏 Agent 调用模板**必须**注入：
- `PLAN_ID: $PLAN_ID`
- `STEP: <当前阶段号>`
- cluster 调度额外加：`CLUSTER_ID: <auto_xxx>` + `CHAPTER_RANGE: <ch_a-ch_b>`

缺字段 → hook L3 直接 exit 2 拦截。

---

## ⛔ 单次批次硬上限：300 章（必须遵守）

**铁律**：单次 `/distill-style` 命令最多处理 **300 章**（推荐 200-250 章）。

**为什么**：
- 单次会话失败成本随章数线性上升
- 闭环验证（阶段 2-6）发现问题时，已蒸馏数据可能要全部重做
- 容易触发账户 API 配额限额
- 主代理上下文积累过快

**实操流程**：
1. 阶段 0 预处理时检测用户提供章数 N
2. **plan-create 检查**：如果检测到本次输入 N > 300，先扫描有无未结案的旧 plan：
   ```bash
   python core/scripts/plan_tracker.py list --active
   # 若有同 project 的 active plan → 必须先 abort 再重建（避免跨批次复用 plan_id）
   python core/scripts/plan_tracker.py abort "<old_plan_id>" --reason "split-300-cap"
   ```
3. 如果 N > 300：**主动询问分批策略**，不要默认全跑
   - 标准方案：第 1 次 Ch1-250 → 验证收敛 → 第 2 次 Ch251-500（增量）→ 以此类推
   - 大书 2000+ 章建议分 8-10 个会话
   - **每批必须独立 plan_id**：`--key "batch1_v0"` / `--key "batch2_v0"`
4. 每完成一批先跑阶段 2-6 闭环验证再启动下一批
5. 如果 N ≤ 300：直接执行

**经验依据**：
- `core/claude-home/lessons/distill-style-lessons.md` 的 **L2.7**（单批次硬上限）：本次蒸馏 200 章前后耗时数小时 + 触发 1 次 API 限额 + 5 次救援。300 章已是单会话上限。
- 阶段 0 必读：`Read <REPO_ROOT>/core/claude-home/lessons/distill-style-lessons.md` 全文，把 L2.7 等红线（⛔）类教训转成本次蒸馏的"避坑清单"。

---

## 阶段 0：读经验库（必读 · 自学习入口）

**开工前必须读取**全局蒸馏经验库 **+ 自学习升级的维度池**：

### 维度池注入（必读）

```bash
# 读取自学习升级的维度（agent 蒸馏单章时必须把这些维度也分析）
cat core/claude-home/auto_evolved_dimensions.json 2>/dev/null
# 字段 dimensions[] 中 status=active 的维度 → 注入本次蒸馏 prompt 的 B7 段
```

**子代理收到 brief 时**，brief 末尾会有「B7 自学习追加维度池」段，列出已升级的 N 个维度（如 cand_001 / cand_002 ...）+ 触发指南。子代理在 B7 段必须像分析 B1-B6 一样分析这些维度。

### lessons MD 经验库

- 路径：`<REPO_ROOT>/core/claude-home/lessons/distill-style-lessons.md`
- 用途：跨项目通用教训库（调度层 / Agent 执行 / 数据质量 / 方法论 / 评估工具）
- 阅读重点：
  - L1.x 调度层 → 避免 hook 拦截 / 工作目录污染 / 配额耗尽
  - L2.x Agent 执行 → 避免 turn 末尾跳步 / 过度优化 / 救援冗余
  - L3.x 数据质量 → JSON 转义 / 文件命名规范
  - L4.x 方法论 → 基线 vs 任务 / 章型亚型 / 双侧区间 / 颗粒度
  - L5.x 评估工具盲点（Future work）

**主代理执行流程**：
1. 阶段 0 第一步 Read distill-style-lessons.md
2. 把红线（⛔）类教训转成本次蒸馏的"避坑清单"，写入临时变量
3. 在阶段 1 子代理 prompt 中提示「参考经验库 L1.1/L2.1/L4.1 等条目」

子代理在收到调度时**也应该读取**经验库中相关条目（按 prompt 提示）。

### 阶段 0 完成标记（plan-step 1）

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 --skip-output
# 阶段 0 无具体产出文件（只是预处理），用 --skip-output 跳过文件校验
```

---

## 阶段 1（原"第一步"）：表层蒸馏

### 主轨流程（cluster 故事块自适应蒸馏）

```
1. Read workspace/styles/<书名>/cluster_index.json
   - 不存在 → python core/scripts/cluster_segmenter.py --project workspace/styles/<书名>
2. FOR cluster in cluster_index.clusters:
   spawn Agent({
     description: "蒸馏 cluster_<id> (ch_a-ch_b)",
     prompt: f"""你是写作风格分析专家。
       PLAN_ID: $PLAN_ID
       STEP: 2
       CLUSTER_ID: {cluster.cluster_id}
       CHAPTER_RANGE: ch{a}-ch{b}
       任务：连续蒸馏该 cluster 的 N 章（N = cluster.chapters_count，3-6 章）。
       独立 Read N 章正文，独立跑 N 次 style_analyzer.py。
       输出：
       - 蒸馏进度/ch{a}.json ... ch{b}.json（单章独立 N 个）
       - 衔接分析/cluster_{cluster.cluster_id}_continuity.json（cluster 内章际衔接）
       cluster 边界原因（来自 cluster_index）：{cluster.boundary_reason}
       [当前作者 skill 摘要（如有）]
     """
   })
3. 并行配额 ≤20 agent / 波（L1.4），分波启动
4. 每完成约 10 cluster → 调 arc_aggregator.py --all-clusters 增量聚合 arc
5. 全部 cluster 完成 → skill v0 聚合
```

### 子代理的职责

每个子代理独立完成以下工作：
1. 用 Read 工具读取指定行范围的章节内容
2. **先用 Bash 跑 `python core/scripts/style_analyzer.py` 获取精确量化数据**
3. 基于精确数据 + LLM 定性分析，完成 15 维度风格分析
4. 对比当前作者skill（如已有），标注"新发现"或"与基线一致"
5. 返回结构化 JSON 结果给主代理（量化数据来自脚本，定性分析来自 LLM）

### 迭代升级机制

```
作者skill迭代流程：

初始状态：无skill文件
第1-30章蒸馏完成（10 个 3 章 agent）→ 生成 v1 skill（基线）
第31-60章蒸馏完成 → 对比v1，发现新特征 → 生成 v2 skill
第61-90章蒸馏完成 → 对比v2，精化/修正 → 生成 v3 skill
...
每 30 章迭代一次，skill越来越精准

后续子代理收到的prompt中包含当前最新skill摘要，
这样它可以标注"与已知风格一致"或"发现新特征：XXX"
```

### Agent 子任务的蒸馏 prompt 模板

```
你是写作风格分析专家。

任务：分析第{N}章的写作风格。

步骤：
1. 用 Read 工具读取文件 {file_path}，offset={start_line}, limit={line_count}
2. 阅读全文后，从以下维度分析风格特征

当前作者skill摘要（供对比）：
{current_skill_summary 或 "暂无，这是早期章节"}

═══ A. 定量统计（精确数字，不要模糊描述）═══

1. 句长分布：平均句长X字，标准差Y，最短Z字，最长W字
2. 段落长度：平均每段X句，最短Y句，最长Z句
3. 对话占比：本章对话占全文X%（按字数计）
4. 标点密度：
   - 逗号/句号比：X:1
   - 省略号频率：每1000字X次
   - 感叹号频率：每1000字X次
   - 问号频率：每1000字X次
   - 破折号频率：每1000字X次
5. 功能词指纹（每1000字出现次数）：
   - "的"：X次 | "了"：X次 | "着"：X次
   - "却"：X次 | "便"：X次 | "竟"：X次
   - "倒"：X次 | "只"：X次 | "又"：X次
   - "不过"：X次 | "只是"：X次 | "毕竟"：X次
6. 章节字数：本章共X字

═══ B. 定性分析（15维度，每项1-2句）═══

1. 句式节奏（长短交替规律/节奏型态）
2. 段落结构（段间过渡方式）
3. 开头方式（本章第一段的类型）
4. 对话风格（标签习惯/口语化程度）
5. 描写密度（环境描写频率和长度）
6. 感官偏好（视觉/听觉/触觉/嗅觉哪个多）
7. 动作描写（简洁型/细腻型/电影镜头型）
8. 心理描写（直接内心/行为暗示/比例）
9. 情绪节奏（本章情绪走向/转折方式）
10. 用词特征（口语化程度/修辞手法）
11. 悬念手法（如何制造悬念/章末钩子类型）
12. 角色引入（新角色如何出场）
13. 信息投放（世界观信息如何融入正文）
14. 节奏控制（快慢段落比例和切换）
15. 独特标识（区别于其他作者的最显著特征）

═══ B2. 跨章对比维度 ═══

16. 开头类型标签（从以下选一：场景型/静场定格/动作切入/冷事实三连击/纯对话开场/时间地点两字段/拟声定格/钩子回音式/人物内心吐槽/心理铺陈/动作承接/其他）
17. 开头焦点元素（本章开头用了什么具体物件/感官做焦点？如"灯光""雨声""咖啡味"，列1-2个关键词）
18. 章末类型标签（从以下选一：信息炸弹/拟声硬收/独立短句/动作留白/对话悬念/场景硬收/回环呼应/其他）
19. 场景过渡清单（列出本章所有场景切换点的过渡方式，格式：[{位置:"段落N", 方式:"拟声切场/动作切场/时间锚点/对话引入/感官跳切/空间锚点"}]）
20. 环境锚点清单（列出本章用作氛围的具体感官元素，格式：[{元素:"灯管闪烁", 感官:"视觉", 出现次数:3}]，捕获重复使用的元素）
21. 角色对话长度（本章每个说话角色的平均单句字数和最长单句字数，用于 voice_pack 合规检查）

═══ B3. 描写技法维度 ═══

22. 人物引入技法（本章有新角色出场时，从以下选一或多：路人群像视角观察/动作先行/装备侧写/对话展现/对比反差/背景信息通过他人对话传递/其他。描述具体手法）
23. 环境描写技法（本章用了什么环境描写手法：两字锚点定场/感官主导单点深入/对话间接展现环境/最小化具象1-2句收笔/氛围暗示不明说/其他。列出每处环境描写的位置和手法）
24. 战斗描写技法（如本章有战斗/冲突：三拍公式（拟声→长句→短句定性）/镜头切换（仰视→全景→特写）/动作分解为连续短段/冷却间隔（战后日常动作降温）/其他。无战斗标null）
25. 心理描写技法（本章怎么写心理：身体外显（胃液翻滚/攥拳/呼吸粗重）/排比吐槽式宣泄（脏话+列举）/第三人称冷评（叙述者旁白概括）/行为暗示（笑容消失/嘴唇抿起）/他人视角吐槽/其他。标注具体段落）
26. 与上章衔接类型（从以下选一：直接承接同一时空/信息炸弹→静默回响/时间跳跃/空间跳转/情绪落差/悬念承接新视角/其他）
27. 与上章衔接手法（具体描述：如"上章末尾尖叫声→本章开头角色听到叫声回应"或"上章末信息炸弹'炽天使'→本章开头'房间陷入了短暂安静'用静默消化冲击"）

═══ B4. 叙事工艺维度 ═══

28. 场景vs概述比例（本章中，多少比例是"实时场景"——有对话/动作/实时展开，多少是"概述"——叙述者压缩时间跳过事件？估算百分比。AI 典型问题：100%场景0%概述，而好作者通常 70%场景/30%概述）
29. 钩子清单（本章中设置了几个"钩子"——让读者想继续看下去的悬念点？列出每个钩子的位置和类型：信息悬念/角色秘密/危机预告/伏笔抛出/反转预告。统计钩子总数和位置分布：开头/中段/结尾各几个）
30. 留白/潜台词实例（本章有哪些"没说出口"的内容？对话中角色真正想表达的和字面意思不同的地方？作者克制不写、让读者自己领会的地方？列出具体段落位置。AI典型问题：什么都说透，无留白）
31. 时间操控（本章覆盖了多长的故事时间？是否有时间压缩——用一句话跳过几小时/几天？是否有时间展开——用整章写5分钟的事？标注：实际故事时间 / 章节字数 → 时间密度比）
32. 信息差管理（本章中，读者知道但角色不知道的信息有哪些？角色知道但读者不知道的信息有哪些？作者怎样利用这种信息差制造张力或期待感？）
33. 情绪节拍图（画出本章的情绪走向：标注 3-5 个关键情绪节拍点——每个节拍标注位置百分比和情绪方向。如"10%低调开场→35%紧张升级→60%高潮爆发→80%短暂缓和→95%章末炸弹"）
34. 叙事距离变化（本章中，叙述者和角色的"心理距离"是否变化？哪些段落是"贴近"角色意识的——能感受到角色的思维/感官？哪些段落是"拉远"的——叙述者在客观描述？距离变化的节奏是什么？）
35. 期待感构建（本章用了什么手法让读者"想继续看"？从以下选：信息差——读者知道危险角色不知道 / 许诺——暗示后面会有好看的 / 谜题——抛出问题不给答案 / 人物困境——角色陷入两难 / 升级期待——读者期待角色变强。列出每种手法的具体位置）

═══ B5. 作者区分度维度 ═══

**筛选标准：以下维度经过与 B1-B4 全部35个维度去重，确认无冗余覆盖，且每个维度都有高作者区分度（不同作者在这个维度上差异显著）。**

36. 词汇丰富度（由 style_analyzer.py 自动计算，不需要 LLM 估算。包含 Type-Token Ratio 和 Hapax Ratio。文体学研究证明这是区分不同作者的最有效特征之一——每个作者的词汇库大小和用词重复率差异巨大）
37. 冲突密度（本章有几个明确的冲突/对抗/矛盾点？不是模糊的"紧张感"，而是角色之间的实际对立。统计数量。注意：dim14"节奏控制"是宏观快慢，本维度是微观冲突计数——两者互补不冗余）
38. 配角戏份比（本章中，主角的对话/叙事占比 vs 配角占比。估算百分比。有些作者是"主角独角戏型"90%+主角，有些是"群像型"主角只占40%——这是高区分度维度）
39. 幽默/喜剧密度（本章有几处明确的幽默点——让读者笑/会心一击的地方？什么类型的幽默：吐槽式/反差式/荒诞式/冷幽默/口癖喜剧/自嘲式？统计数量和类型。注意：有些作者完全不幽默，有些每章5+处——区分度极高）
40. 爽点/满足点密度（本章有几个让读者感到"满足"的时刻——角色达成目标/打脸成功/谜题揭晓/能力展示/关系推进？注意：与dim29"钩子"方向相反——钩子是制造悬念让人"想看"，爽点是兑现承诺让人"满足"。两者互补）
41. 多线交织度（本章同时推进了几条情节线？是单线推进（A→A→A）还是多线交叉（A→B→A→C→A）？切换了几次？有些作者擅长单线深入，有些擅长ABAB双线交叉——区分度高）

**⚠️ 冗余关系标注（避免子代理重复分析）：**
- dim9"情绪节奏" 与 dim33"情绪节拍图"：dim33是dim9的量化升级版，分析时以dim33为准，dim9可简写"参见dim33"
- dim11"悬念手法" 与 dim29"钩子清单"：dim29是dim11的细化版，分析时以dim29为准
- dim35"期待感构建" 与 dim32"信息差管理"：有交叉但方向不同——dim32关注信息不对称，dim35关注读者心理预期。两个都要分析但注意不重复

═══ B6. 叙事指纹维度 ═══

**这是经过4路并行搜索 → 与B1-B5全部41维度去重 → 区分度+通用性双重过滤后的最终补充。**

42. 叙事技巧指纹（本章使用了哪些高级叙事技巧？从以下选多个：一笔两用——一个细节/动作同时推进两条线或完成两个功能 / 视角欺骗/不可靠叙述——叙述者或角色的视角有意误导读者 / 对比锚点——先建立参照物再反转 / 延迟交付——承诺一个信息但故意推迟给出。每个技巧标注具体段落。作者区分度极高——有些作者全书0次，有些平均每章2-3次）
46. 场景结构质量（本章的场景是否有明确的Goal→Conflict→Disaster结构？场景后是否有Reaction→Dilemma→Decision的Sequel？还是AI式的"事件发生→下一个事件"无结构连接？评分：A=完整Scene-Sequel / B=有Goal和Conflict但无Sequel / C=事件堆砌无结构 / D=流水账）
47. 人物丰满度指标（本章的角色塑造质量：谎言-欲望-需求三层是否体现？有无硬币式正反面展示？压力下选择是否定义了人格？角色锚点是否出现？从以下评分：A=丰满立体 / B=有基本面但缺深度 / C=功能性角色无性格 / D=面具化角色可互换。标注具体证据段落）
48. 对话质量指标（本章对话的写作质量：对话是否推进剧情而非解释设定？是否有动作节拍穿插？是否有潜台词/筹码交换？角色语气是否可区分？评分：A=自然有层次 / B=流畅但偏直白 / C=信息交换式 / D=说教式。标注问题段落）
43. 角色行为循环（本章的角色是否有重复出现的签名行为模式？如"每次紧张就摸衣领""每次撒谎就喝水""每次高兴就骂人"。不是签名动作——那是外表特征，而是行为-情绪的固定映射。作者区分度高——好作者给每个角色2-3个行为循环，AI倾向于只写签名动作）
44. 核心梗贯穿度（本章是否提及/推进了本书的核心卖点/核心梗？如果连续3+章完全没有涉及核心梗，标记为"偏离警告"。不同于伏笔——核心梗是贯穿全书的主线卖点，伏笔是支线悬念。高区分度——好作者每章至少1处呼应核心梗，AI容易写着写着偏离）
45. 主角存在感（本章主角是否出场并主动推动了剧情？主角对话占全章对话的百分比？如果主角连续2章缺席或纯被动，标记为"存在感危机"。与dim38配角戏份互补——dim38看配角有多少，本维度看主角是否足够）

**⚠️ 可选扩展包（题材特有，不入核心维度，按需启用）：**
- [升级文] 升级节奏：每N章需有等级/实力突破，连续50章无进阶即失败
- [幻想文] 金手指存在感：核心能力/道具是否持续存在感，连续5章未提及即失败
- [有反派的文] 反派设计质量：反派是否有独立动机和逻辑，还是纯工具人
- 以上扩展包在蒸馏时根据题材自动判断是否启用，不默认开启

═══ C. 黄金段落提取（原文摘录，每段50-150字）═══

从本章中提取最能代表作者风格的原文段落（如有）：
- golden_action：最佳动作/打斗描写段落（如有）
- golden_dialogue：最佳对话段落（如有）
- golden_description：最佳环境/氛围描写段落（如有）
- golden_psychology：最佳心理/情绪描写段落（如有）
- golden_opening：章节开头段落（必提取）
- golden_ending：章节结尾段落（必提取）
- golden_transition：最佳场景转换段落（如有）
- golden_character_intro：最佳人物引入段落（新角色出场时的介绍手法，如有）
- golden_connection：最佳章际衔接段落（本章开头承接上章的手法，如有）
- golden_battle_cooldown：战斗后冷却间隔段落（战后角色的日常动作/台词降温，如有）

每类只提取1段，没有就标null。必须是原文摘录，不要改写。

═══ D. 反模式检测 ═══

记录本章中作者"从不做的事"：
- never_sentence_patterns：作者从不使用的句式结构
- never_transitions：作者从不使用的过渡方式
- never_dialogue_tags：作者从不使用的对话标签
- never_words：本章中完全没出现的常见网文用词

═══ E. 与AI默认输出的对比 ═══

标注该作者与AI默认写作模式的差异（参考 `core/scripts/style_analyzer.py` 禁用词规则库 + `CLAUDE.md` 反 AI 腔调守卫节）：
- sentence_variance：作者的句长标准差 vs AI默认（AI通常std<5）
- paragraph_variance：作者的段落长度变化 vs AI默认（AI通常均匀3-5句）
- emotion_method：作者表达情绪的方式 vs AI默认（AI倾向直接描述）
- transition_method：作者转场方式 vs AI默认（AI倾向用连接词）
- dialogue_after：对话后作者怎么接 vs AI默认（AI倾向跟心理活动）
- ending_method：作者章节结尾方式 vs AI默认（AI倾向总结感悟）
- conflict_rhythm：作者冲突节奏 vs AI默认（AI倾向匀速推进）

每项标注：作者的做法 + 与AI的差异程度（大/中/小/无差异）

═══ G. 维度自学习提议（可选但鼓励）═══

如果本章观察到现有 35+ 维度（B1-B6）**未覆盖**但**值得长期跟踪**的现象，请填写本段：

**G1 dimension_proposals 字段（结构化）**：

```json
"G_dimension_proposals": [
  {
    "proposed_dim_name": "<dim 名，如 dim50_metaphor_compression_ratio>",
    "observation": "<本章具体现象 < 100 字>",
    "current_dims_missing": "<现有 dim X 部分覆盖但缺 Y，或完全未覆盖>",
    "value_assessment": "high|mid|low",
    "_doc_value": "high=明确值得新增、mid=值得补充但非紧急、low=仅作记录",
    "estimated_appearance_rate": "<每章 / 每 N 章 / 仅 X 章型>",
    "suggested_extraction_method": "<怎么自动检测，如正则/字数比例/章型标签>",
    "category_proposal": "B7_self_discovered"
  }
]
```

**触发指南**：
- 看到「现有 dim 应覆盖但精度不够」时填 mid（如 dim14 节奏控制覆盖但没拆「微观节奏」）
- 看到「全新现象，35 dim 都没覆盖」时填 high（如「物理代价化超能力三件套」）
- 看到「孤例无价值」时直接不填（不要把无意义观察硬塞 G 段）

**为什么需要**：F 段是自由文本难聚合，G 段是结构化 → `dimension_evolver.py` 跨章聚合 → 满足
「≥ N 章 + value 主要为 high/mid」 → 自动升级到 `auto_evolved_dimensions.json` → 下次蒸馏 prompt 自动注入。

业界依据（Round 1 调研 · `.research_cache/inspiration_self_evolving_distill_2026-05-24.md`）：
LLM-based interpretable feature generation (arxiv 2409.07132) workflow B 半自动模式；
Voyager skill library 渐进 skill 添加范式；
EvolveR (arxiv 2510.16079) offline self-distillation 闭环。

═══ F. 对比标注 ═══

- 如有当前skill摘要，标注哪些特征"与基线一致"，哪些是"新发现"
- 如发现风格与前期明显不同，标注为"风格演变"

输出格式：纯JSON，不要多余文字。
```

### 验证标准

**主轨标准**：
- Agent 调用次数 = `len(cluster_index.clusters)`（典型 200 章 → 30-40 cluster）
- 主代理上下文中不能出现章节正文
- 每完成 ~10 cluster 看到一次 skill 文件更新 + arc_aggregator 增量聚合
- 最终 analyzed_chapters = 总章节数（全量）
- `蒸馏进度/` 目录下有每章的独立分析 JSON（颗粒度严格保留）
- `衔接分析/` 目录下有每 cluster 一个的 continuity JSON
- `arc_templates/` 目录下有每 cluster 一个的 `cluster_arc_<id>.json` + 副轨 `arc_<NNN>.json` × ⌈N/10⌉


## 阶段 1.5：故事块 arc 聚合（双轨）

### 为什么需要 + 颗粒度选型依据

写作端早就是 ECAS **故事块模式**（`gen_writer.py --cluster N --chapter-start X --chapter-end Y`，cluster 长度 2-6 章 / 4000-20000 字），蒸馏端却卡在 3 章固定 continuity 颗粒度——**两端错位**导致蒸馏的"3 章衔接模板"对 cluster 写作没用。

调研：**业界 2024-2025 SOTA 全面采用可变长度故事块**：

| SOTA | 颗粒度 | 关键依据 |
|---|---|---|
| LumberChunker (EMNLP 2024 · arXiv 2406.17526) | LLM 检测语义边界的 variable-length chunk | 比 fixed-N **+7.37% DCG@20** |
| MARCUS (arXiv 2510.18201, 2025) | event-centric 跨整本书 | actor/experiencer 双视角时间序列 |
| Multi-Agent TV Arcs (arXiv 2503.04817, 2025) | arc 跨任意 episode，按情节单元自然终结 | "avoiding artificial segmentation" |
| Three Stage Narrative (arXiv 2511.11857, 2025) | sliding window + Ward 聚类 6 弧形 | 后置分析，不切分 |

**结论：双轨**——cluster 主轨（情节单元结构 + arc 形状） + 章节副轨（局部句段节奏）。固定 N 章已被 EMNLP 2024 实证落后。完整调研见 `.research_cache/inspiration_cluster_distill_2026-05-24.md`。

### 双轨产出

```
workspace/styles/<书名>/
├── cluster_index.json                           # cluster_segmenter 产出，按情节单元切分章节
├── arc_templates/
│   ├── cluster_arc_<cluster_id>.json            # ★ 主轨：每 cluster 一个 arc
│   ├── arc_<NNN>.json                           # 副轨：fixed10（兼容向后/章节级节奏）
│   └── arc_summary.json                         # 全书统计（primary_track 字段自动识别）
└── character_arcs/
    └── <角色>_emotion_arc.json                   # 角色情感弧（MARCUS 范式）
```

### 执行流程

#### 已蒸馏书（5 本现有书）—— retroactive 切分 + 4 维同步

```bash
# Step A：按情节单元自动切 cluster（启发式：connection_type + 字数/章数硬约束）
python core/scripts/cluster_segmenter.py --project workspace/styles/<书名>
# 输出 cluster_index.json（5 章 / 14K 字均长 ≈ ECAS schema 推荐）

# Step B：每个 cluster 聚合 arc（含 Sudowrite 1-11 dial + mid_checkpoint 张力）
python core/scripts/arc_aggregator.py --project workspace/styles/<书名> --all-clusters

# Step C：聚合 character arc（MARCUS 范式 + Stanford 6-component 重要度）
python core/scripts/character_arc_aggregator.py --project workspace/styles/<书名> --min-appearances 5

# Step D：全书 summary
python core/scripts/arc_aggregator.py --project workspace/styles/<书名> --mode summary

# === 仿写真实度 4 维同步 ===

# Step E：章节标题命名风格指纹（业界空白领域 · 我们做即 SOTA）
python core/scripts/title_style_distiller.py --project workspace/styles/<书名>
# 输出 title_style.json：长度/tier/结构/高频字/per-tier 黄金示例
# gen_chapter_titles 会自动读此文件做 per-book 校准（覆盖默认 80/15/5）

# Step F：角色命名规范指纹（含网文化指数 · Round 1 D 调研）
python core/scripts/naming_convention_distiller.py --project workspace/styles/<书名>
# 输出 naming_convention.json：主文化/音节频次/网文大姓+字库匹配度
```

#### 新蒸馏书 —— 阶段 1 每完成 1 个 cluster 触发

**新蒸馏直接按 cluster 调度，每完成 1 个 cluster 触发**：

```bash
python core/scripts/arc_aggregator.py --project workspace/styles/<书名> --cluster <cluster_id>
```

cluster 边界来源（按优先级）：
1. 大纲已含 ECAS cluster_brief → 直接用 cluster_id + chapter_range
2. 无大纲 cluster → 蒸馏完成后跑 cluster_segmenter 一次性切

### 🔁 2 轮 0 issue 收敛循环（SRE 风格）

参考 reading-reflector 的 3 轮 clean 模式（`.claude/agents/novel-reading-reflector.md` MAX_ROUNDS=5 / 3 轮 clean pass）。蒸馏阶段 1.5 采用 **2 轮 0 issue** 终止：

```python
round_n = 0
prev_issues = []
while round_n < MAX_ROUNDS:    # 默认 MAX_ROUNDS = 5
    round_n += 1
    # 1. 跑 cluster_segmenter + arc_aggregator --all-clusters + character_arc_aggregator
    # 2. 扫产出 issue（cluster 字数越界 / arc shape 全 Unknown / character arc missing / 等）
    issues = scan_arc_quality(project)
    if not issues:
        consecutive_clean += 1
        if consecutive_clean >= 2:
            break   # ✅ 2 轮 0 issue pass
    else:
        consecutive_clean = 0
        # 3. 修：调启发式参数、补字段映射、纠正路径
        apply_fixes(issues)
        prev_issues = issues
else:
    escalate_human()   # 超 5 轮仍有 issue → 升级人工
```

**质量检查清单**（每轮 scan_arc_quality 必查）：
- ❌ cluster_arc emotion_curve 全部 0.25（说明 dim33/39/40 字段未命中）
- ❌ cluster_arc matched_reagan_shape == "Unknown"
- ❌ cluster 字数越界（< 4000 或 > 20000）
- ❌ character_arc 全部 fallback 估算（actor/experiencer corr=1.0 或 -1.0）
- ❌ cluster_arc 总数 ≠ cluster_index.clusters 总数
- ❌ arc_summary primary_track ≠ "cluster"

### arc 主轨 schema（cluster 模式）

`workspace/styles/<书名>/arc_templates/cluster_arc_<cluster_id>.json`：

### arc 副轨 schema（fixed10 模式）

`workspace/styles/<书名>/arc_templates/arc_<NNN>.json`（NNN = 该 arc 末章号）—— 字段与主轨基本一致，仅多 `mode: "fixed10"` 区分。

### 双轨共用字段 schema

```json
{
  "arc_id": "arc_010",
  "chapter_range": "ch1-10",
  "emotion_curve_normalized": [0.3, 0.4, 0.5, 0.7, 0.6, 0.4, 0.5, 0.8, 0.9, 0.4],
  "pacing_labels": ["慢", "中", "中", "快", "中", "慢", "中", "快", "快", "慢"],
  "scene_summary_ratio_per_chapter": [0.8, 0.75, 0.7, 0.85, 0.75, 0.4, 0.7, 0.9, 0.9, 0.5],
  "event_density_per_chapter": [1, 2, 1, 3, 2, 1, 2, 4, 5, 1],
  "kicker_count_per_chapter": [2, 3, 2, 4, 3, 2, 3, 5, 6, 2],
  "climax_chapter_index": 8,
  "arc_structure_label": "低开-缓上-小爆-消化-再上-大高潮-收尾",
  "matched_reagan_shape": "Rags-to-Riches",
  "matched_reagan_shape_confidence": 0.78,
  "foreshadowing_planted_in_arc": 12,
  "foreshadowing_resolved_in_arc": 3,
  "character_arc_summary_in_arc": [
    {"character": "HeroC", "stage_from": "迷茫", "stage_to": "入局者", "key_turning_chapter": 8}
  ],
  "_metadata": {
    "distill_date": "2026-05-23",
    "skill_version": "v3.3",
    "source_continuity_files": ["ch1_3_continuity.json", "ch4_6_continuity.json", "ch7_9_continuity.json"],
    "aggregator_version": "v22.1"
  }
}
```

### 字段计算方法

- `emotion_curve_normalized`：从 `continuity.pacing_curve` 解析快慢标签 → 数值映射（快=0.8 / 中=0.5 / 慢=0.3），叠加单章 `dim33 情绪节拍图` + `dim39 幽默密度` + `dim40 爽点密度` → 归一到 0-1
- `pacing_labels`：直接从 `continuity.pacing_curve` 抽取（"中快/快/慢/平稳" → "中/快/慢/慢"）
- `scene_summary_ratio_per_chapter`：从单章 metrics.json 的 `dim28 场景vs概述比例`
- `event_density_per_chapter`：单章 `dim37 冲突密度` + `dim29 钩子总数` 加权
- `kicker_count_per_chapter`：单章 `dim29 钩子总数`
- `climax_chapter_index`：emotion_curve_normalized 的 argmax
- `arc_structure_label`：根据 emotion_curve 形状描述（如「先升后降」「双峰」「U 型」）
- `matched_reagan_shape`：拟合 6 形状选 TOP1 + 置信度（升/降/谷/峰/W/M）
- `character_arc_summary_in_arc`：从 continuity.character_continuity 聚合主要角色的 stage_from/to

### 执行流程

```bash
# 阶段 1 每完成 10 章后，主代理调（不阻塞阶段 1 继续）：
python core/scripts/arc_aggregator.py \
  --project "workspace/styles/<书名>" \
  --arc-end-chapter <N>          # 如 N=10 → 聚合 ch1-10 → arc_010.json
```

主代理可以在阶段 1 的「每 30 章 skill 升级」节点之后**并行**调 3 次（聚合 arc_010 / arc_020 / arc_030），不影响阶段 1 推进。

### 已蒸馏书的迁移路径（**关键**）

已蒸馏完成的 5 个风格库（BookC / BookB / 饲养全人类 / 没钱修什么仙 / BookA）**不用重蒸单章**，只需补跑 arc_aggregator：

```bash
# 对每本已蒸馏书的每个 10 章窗口跑一次
for end in 10 20 30 ... <总章数>; do
  python core/scripts/arc_aggregator.py \
    --project "workspace/styles/<书名>" \
    --arc-end-chapter $end
done

# 最后生成全书 arc summary
python core/scripts/arc_aggregator.py \
  --project "workspace/styles/<书名>" \
  --mode summary
```

### plan_tracker 加 step 2.5

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2 --skip-output   # 阶段 1 完成
# 阶段 1.5（arc 聚合）作为 step 2.5（**不写入 plan 模板的 required 步骤**，仅记录运行）
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2 --output "arc_templates/arc_010.json"  # 多次调，每次 output 不同
```

**注**：阶段 1.5 不计入 plan 模板的 required 步骤（保留向后兼容），但建议跑——不跑则方案 2 完整收益失效。

---

## 第二步：深度风格分析

基于逐章蒸馏的汇总结果，进行多维度综合分析：

### 2.1 定量统计汇总（从所有章节的数据取平均/中位数）
- 句子平均长度：X字（标准差Y）
- 段落平均长度：X句
- 对话占比：X%（标准差Y%）
- 章节平均字数：X字
- 标点密度汇总：逗号/句号比、省略号频率、感叹号频率
- 功能词指纹（全书平均，每1000字）：的/了/着/却/便/竟/倒/只/又/不过/只是/毕竟

### 2.2 叙事结构
- 章节开头模式分布（悬念型X%/场景型Y%/对话型Z%/回忆型W%）
- 章节结尾模式分布（钩子型/情绪型/转折型）
- 叙事视角（第一人称/第三人称限制/全知）

### 2.3 语言特征
- 长短句交替规律（具体模式描述）
- 词汇复杂度（口语化程度 1-10）
- 修辞手法偏好及频率
- 高频词汇 TOP 30
- 作者从不使用的词（通过全书缺失检测）

### 2.4 对话风格
- 对话标签习惯分布（"说道"X% / 动作代替Y% / 无标签Z%）
- 角色对话区分度
- 对话中的动作/表情插入频率
- 内心独白的使用方式

### 2.5 描写偏好
- 环境描写密度（每章平均X处，每处平均Y句）
- 感官描写偏好比例（视觉X%/听觉Y%/触觉Z%/嗅觉W%/味觉V%）
- 动作描写风格
- 心理描写比例和方式

### 2.6 节奏与情绪
- 情绪曲线模式
- 高潮频率
- 悬念设置方式
- 转折手法

### 2.7 角色塑造
- 角色引入方式分布
- 性格展现手法
- 角色成长节奏
- 配角塑造深度

### 2.8 跨章多样性分析（反同质化核心）

**这是蒸馏最容易遗漏的维度。** 逐章分析只看单章特征，跨章分析看的是连续章节之间的变化模式。

从所有章节的 B2 维度数据中提取：

#### 2.8.1 开头类型连续性检测
- 统计所有章节的`开头类型标签`序列
- 检测是否存在连续2章用同一类型的情况（正常作者几乎不会）
- 统计各类型的实际分布比例
- 检测是否存在连续3章用相同`开头焦点元素`的情况
- **输出**：开头类型分布 + 连续重复率 + 反重复规则

#### 2.8.2 章末类型连续性检测
- 统计所有章节的`章末类型标签`序列
- 检测连续重复
- **输出**：章末类型分布 + 连续重复率

#### 2.8.3 环境锚点多样性检测
- 从所有章节的`环境锚点清单`中汇总
- 统计每个环境元素在全书中出现的总次数和连续章节出现情况
- 识别"AI陷阱元素"——如果某个元素（如"灯光闪烁"）在超过30%的章节中出现，标记为高危重复元素
- **输出**：高频环境元素排名 + 每元素的跨章连续出现率 + 多样性建议

#### 2.8.4 场景过渡方式多样性检测
- 从所有章节的`场景过渡清单`中汇总各过渡方式的使用频率
- 检测是否过度依赖单一过渡方式（如拟声切场占比超过50%）
- **输出**：过渡方式分布 + 每章过渡方式种类数均值

#### 2.8.5 角色对话长度 vs voice_pack 合规
- 对比每个角色的实际对话长度与其 voice_pack 定义
- 标记不合规的角色（如定义"三字以内"但实际平均15字）

#### 2.8.6 描写技法分布（从 B3 维度聚合）

从所有章节的 B3 维度数据中汇总：

**人物引入技法分布：**
- 统计各种引入技法（路人视角/动作先行/装备侧写/对话展现/对比反差/背景通过他人对话）的使用频率
- 识别作者最常用和最少用的引入方式
- **输出**：技法分布 + 作者偏好排名 + 黄金示例

**环境描写技法分布：**
- 统计各技法使用频率，计算每章环境描写的平均处数和平均句数
- 识别作者是"两字锚点型"（极简）还是"感官铺陈型"（密集）
- **输出**：技法分布 + 密度模式 + 最常用锚点词

**战斗描写技法分析：**
- 从有战斗的章节中提取公式模式（如三拍公式的使用率、镜头切换频率）
- 识别是否有"冷却间隔"模式（战后日常动作降温）
- **输出**：战斗节拍公式 + 冷却间隔模式

**心理描写技法分布：**
- 统计身体外显 vs 直接描述 vs 排比宣泄 vs 他人视角吐槽的比例
- 识别作者的心理描写核心策略
- **输出**：技法分布 + 核心策略 + 禁忌（如"从不写'他感到愤怒'"）

#### 2.8.7 章际衔接模式分析（从 B3 dim26-27 聚合）

从所有章节的衔接类型数据中提取：

**衔接类型分布：**
| 类型 | 统计频率 | 典型场景 |
|---|---|---|
| 直接承接 | X% | 连续动作章之间 |
| 信息炸弹→静默回响 | X% | 大揭露后的消化章 |
| 时间跳跃 | X% | 日常章之间 |
| 空间跳转 | X% | 切换POV/场景 |
| 情绪落差 | X% | 高潮→低谷 |
| 悬念承接→新视角 | X% | 命令→执行者出场 |

**衔接规则推导：**
- 连续动作章（战斗/追逐/高潮）之间是否总用"直接承接"？
- 大信息炸弹后是否总用"静默回响"开场？
- 时间跳跃是否只在日常章出现？
- 连续N章用同一衔接类型的最大值是多少？
- **输出**：衔接类型分布 + 场景关联规则 + 连续使用上限

#### 2.8.8 叙事工艺模式聚合（从 B4 维度聚合）

**场景vs概述比例分布：**
- 统计所有章节的场景/概述比例，计算全书平均值
- 识别作者的时间操控习惯：高密度章（1章=5分钟）vs 低密度章（1章=1周）的分布
- **输出**：场景占比均值 + 时间密度比分布 + 日常章vs高潮章的差异

**钩子密度和位置分布：**
- 统计每章钩子数量的均值、位置分布（开头/中段/结尾各占多少比例）
- 识别作者的钩子策略：是"章末集中爆炸型"还是"全章均匀分布型"
- **输出**：钩子数均值 + 位置分布 + 最常用钩子类型排名

**留白频率：**
- 统计有明确留白/潜台词的章节比例
- 识别留白的常见位置和类型（对话留白/情感留白/信息留白）
- **输出**：留白章比例 + 留白类型分布

**情绪节拍模式：**
- 汇总所有章节的情绪节拍图，识别最常见的情绪曲线模板
- 如"低开→中升→高潮→缓收"出现占比多少
- **输出**：TOP3 情绪曲线模板 + 高潮位置均值（通常在章节的第几个百分点）

**信息差使用频率：**
- 统计使用信息差（读者知/角色不知 或 反向）的章节比例
- **输出**：使用频率 + 最常见的信息差类型

**叙事距离变化模式：**
- 识别作者是"恒定距离型"还是"动态调节型"
- 如果是动态型，统计贴近/拉远的切换频率和触发条件
- **输出**：距离模式 + 切换频率

#### 2.8.9 作者区分度特征聚合（从 B5 维度聚合）

**词汇丰富度基线（程序化 · style_analyzer.py 自动计算）：**
- 全书 TTR 均值和标准差
- Hapax Ratio 均值
- 与常见网文作者的 TTR 对比（高/中/低）
- **输出**：TTR 基线值 + 校验阈值（偏离原作均值超过 15% 时 WARN）

**冲突密度分布：**
- 每章冲突数的均值、日常章vs高潮章差异
- **输出**：冲突数均值 + 场景类型关联

**配角戏份模式：**
- 主角对话占比均值——识别"独角戏型"(>80%)还是"群像型"(<50%)
- **输出**：主角占比均值 + 模式标签

**幽默密度和类型：**
- 每章幽默点均值 + 类型分布（吐槽/反差/冷幽默/口癖/自嘲）
- 零幽默章比例
- **输出**：幽默均值 + 类型偏好 + 密度模式

**爽点密度：**
- 每章满足点均值
- 与钩子密度(2.8.8)的比例关系（悬念:满足 = X:Y）
- **输出**：爽点均值 + 悬念/满足比

**多线交织模式：**
- 单线章vs多线章的比例
- 多线章平均情节线数
- **输出**：交织模式 + 线数均值

**最终输出**：生成 `cross_chapter_diversity` 对象（含跨章多样性 + 描写技法分布 + 章际衔接模式 + 叙事工艺模式 + 作者区分度特征），包含上述所有检测结果和自动推导的规则。这些规则必须写入最终的 skill 文件。

### 2.9 黄金段落库（从全书蒸馏中精选）

从所有章节提取的黄金段落中，精选最具代表性的段落，按场景类型分类：

| 类型 | 数量 | 用途 |
|------|------|------|
| golden_action | 5-8段 | 写打斗/动作场景时参考 |
| golden_dialogue | 5-8段 | 写对话场景时参考 |
| golden_description | 5-8段 | 写环境/氛围时参考 |
| golden_psychology | 5-8段 | 写心理/情绪时参考 |
| golden_opening | 5-8段 | 写章节开头时参考（**每种开头类型至少1段**） |
| golden_ending | 5-8段 | 写章节结尾时参考（**每种结尾类型至少1段**） |
| golden_transition | 5-8段 | 写场景转换时参考 |
| golden_character_intro | 3-5段 | 写新角色出场时参考 |
| golden_connection | 3-5段 | 写章际衔接时参考（每种衔接类型至少1段） |
| golden_battle_cooldown | 2-3段 | 战后冷却间隔参考 |

每类从全书中选出最能代表作者风格的段落，标注来源章节号。
**要求**：golden_opening 和 golden_connection 必须覆盖多种类型，不能全是同一种开头/衔接。

### 2.9 反模式库（该作者绝对不做的事）

从全书蒸馏中汇总的"从不做"清单：
- never_sentence_patterns：从不使用的句式结构列表
- never_transitions：从不使用的过渡方式列表
- never_dialogue_tags：从不使用的对话标签列表
- never_words：全书从未出现的常见网文用词列表

---

## 第三步：生成风格档案

将分析结果结构化为 JSON：

```json
{
  "source": "参考小说名称或链接",
  "total_chapters": 2033,
  "analyzed_chapters": 2033,
  "total_words_analyzed": 6000000,
  "quantitative": {
    "sentence_length": {"mean": 15, "std": 7, "min": 3, "max": 45},
    "paragraph_length": {"mean_sentences": 4, "std": 2},
    "dialogue_ratio": {"mean": 0.45, "std": 0.12},
    "chapter_words": {"mean": 3000, "std": 500},
    "punctuation_density_per_1000": {
      "comma_period_ratio": 2.3,
      "ellipsis": 1.8,
      "exclamation": 0.5,
      "question": 1.2,
      "dash": 0.3
    },
    "function_word_fingerprint_per_1000": {
      "的": 28.5, "了": 15.2, "着": 3.1,
      "却": 2.8, "便": 1.9, "竟": 0.8,
      "倒": 1.2, "只": 4.5, "又": 3.7,
      "不过": 1.1, "只是": 2.0, "毕竟": 0.6
    }
  },
  "style_profile": {
    "narrative": {
      "pov": "第三人称限制视角",
      "chapter_opening_distribution": {"悬念型": 0.4, "场景型": 0.2, "对话型": 0.3, "回忆型": 0.1},
      "chapter_ending_distribution": {"钩子型": 0.6, "情绪型": 0.2, "转折型": 0.2},
      "sentence_rhythm": "短句连发为主，偶尔长句展开描写"
    },
    "dialogue": {
      "tag_distribution": {"动作代替": 0.5, "无标签": 0.3, "说道类": 0.2},
      "differentiation": "高（每个角色有独特说话方式）",
      "action_inserts": "频繁（每2-3句对话插入一个动作）",
      "inner_monologue": "少（通过行为暗示而非直接写内心）"
    },
    "description": {
      "env_density": "低（每章1-2处，每处2-3句）",
      "sensory_distribution": {"视觉": 0.5, "听觉": 0.2, "触觉": 0.15, "嗅觉": 0.1, "味觉": 0.05},
      "action_style": "电影镜头型（快速切换，动作分解）",
      "psychology_ratio": 0.15
    },
    "pacing": {
      "emotion_pattern": "先虐后爽，快速切换",
      "climax_frequency": "每3章一个小高潮",
      "suspense_method": "信息差 + 时间压力",
      "transition_style": "突然反转（无过渡）"
    },
    "vocabulary": {
      "complexity": 4,
      "high_freq_words": ["前30个高频词"],
      "signature_phrases": ["作者标志性表达方式"],
      "rhetoric_preference": {"比喻": "少", "反问": "多", "排比": "无"}
    },
    "character": {
      "intro_style": "动作引入",
      "personality_method": "行为展现而非描述",
      "growth_pace": "渐进式",
      "supporting_depth": "中等"
    }
  },
  "golden_passages": {
    "action": ["5-8段最佳动作/打斗描写（标注来源章节）"],
    "dialogue": ["5-8段最佳对话段落"],
    "description": ["5-8段最佳环境/氛围描写"],
    "psychology": ["5-8段最佳心理/情绪描写"],
    "opening": ["5-8段最佳章节开头"],
    "ending": ["5-8段最佳章节结尾"],
    "transition": ["5-8段最佳场景转换"]
  },
  "anti_patterns": {
    "never_sentence_patterns": ["作者从不使用的句式结构"],
    "never_transitions": ["作者从不使用的过渡方式"],
    "never_dialogue_tags": ["作者从不使用的对话标签"],
    "never_words": ["全书从未出现的常见网文用词"],
    "anti_samples": ["AI生成的反面教材段落（该作者绝对不会这样写）"]
  },
  "cross_chapter_diversity": {
    "opening_type_distribution": {"场景型": 0.15, "静场定格": 0.12, "动作切入": 0.12, "...": "..."},
    "opening_consecutive_repeat_rate": 0.0,
    "ending_type_distribution": {"信息炸弹": 0.35, "拟声硬收": 0.20, "...": "..."},
    "ending_consecutive_repeat_rate": 0.06,
    "high_risk_env_anchors": ["出现频率过高的环境元素，标记为AI陷阱"],
    "transition_method_distribution": {"拟声切场": 0.30, "动作切场": 0.25, "对话引入": 0.20, "...": "..."},
    "anti_repetition_rules": [
      "从跨章数据自动推导的反重复规则，如：'连续章开头类型不重复'、'灯光元素每3章最多出现1次'"
    ],
    "writing_techniques": {
      "character_intro": {"路人视角": 0.40, "动作先行": 0.25, "装备侧写": 0.15, "对比反差": 0.20},
      "env_description": {"density": "极简型_每章1-2处每处2-3句", "preferred": ["两字锚点", "感官单点深入", "对话间接展现"]},
      "battle": {"formula": "三拍：拟声→长句→短句定性", "camera": true, "cooldown": true},
      "psychology": {"core": "身体外显", "explosion": "排比吐槽式_每N章1次", "forbidden": "从不写'他感到XX'"}
    },
    "chapter_connection": {
      "types": {"直接承接": 0.42, "信息炸弹回响": 0.17, "空间跳转": 0.17, "情绪落差": 0.08, "时间跳跃": 0.08, "悬念新视角": 0.08},
      "rules": [
        "连续动作章之间用直接承接",
        "大信息炸弹后用静默/疲惫开场",
        "时间跳跃仅在日常章之间",
        "空间跳转时用独立短句锚点定场"
      ]
    },
    "narrative_craft": {
      "scene_vs_summary_ratio": {"scene_pct": 0.75, "summary_pct": 0.25, "note": "AI默认100%场景，需要强制加入概述段"},
      "hooks_per_chapter": {"mean": 3.5, "position_distribution": {"opening": 0.25, "middle": 0.35, "ending": 0.40}},
      "subtext_frequency": {"chapters_with_subtext_pct": 0.60, "preferred_type": "对话留白"},
      "time_density": {"climax_chapter": "1章=10分钟", "daily_chapter": "1章=6小时"},
      "info_asymmetry_usage_pct": 0.50,
      "emotion_beat_templates": [
        {"pattern": "低开→中升→高潮→章末炸弹", "frequency": 0.40},
        {"pattern": "紧张开场→缓和→二次高潮→留白", "frequency": 0.30},
        {"pattern": "平稳日常→突发→快收", "frequency": 0.20}
      ],
      "climax_position_mean_pct": 0.72,
      "narrative_distance": {"mode": "动态调节型", "switches_per_chapter": 4}
    }
  },
  "writing_rules": [
    "从风格中提炼的硬性写作规则（如：'对话不用说道'、'环境描写不超过3句'）"
  ],
  "style_evolution": {
    "has_evolution": true,
    "phases": [
      {
        "range": "第1-200章",
        "description": "早期风格特征描述",
        "key_changes": ["变化点1", "变化点2"]
      },
      {
        "range": "第201-800章",
        "description": "中期风格特征描述",
        "key_changes": ["变化点"]
      },
      {
        "range": "第801-2033章",
        "description": "后期风格特征描述（成熟期）",
        "key_changes": ["变化点"]
      }
    ],
    "summary": "一句话概括风格演变轨迹"
  }
}
```

Write `_数据库/作者风格.json`

同时保存到全局风格库（跨项目复用）：
- `mkdir -p 风格库/`
- Write `风格库/[小说名].json` — 与 `_数据库/作者风格.json` 内容相同
- 风格库路径为项目根目录下的 `风格库/`，所有项目共享

**⚠️ Skill 文件存放位置：**
- 全局风格库：`风格库/[小说名]_skill.md`
- 项目内副本：`_数据库/作者风格_skill.md`（写作时 novel-writer 从此路径读取）
- 两份内容相同，项目内副本在 `/write` 初始化时从风格库复制
- novel-writer 的 manifest.must_read 中会包含 `_数据库/作者风格_skill.md` 的路径

---

## 第三步半：生成作者写作 Skill 文件

将蒸馏结果封装为一个可复用的"作者写作 Skill"文件（参考 OpenClaw persona-distiller 的 SKILL.md 格式）：

Write `风格库/[小说名]_skill.md`：

```markdown
---
name: [小说名]-style
description: 基于[小说名]蒸馏的写作风格 Skill
source: [参考小说名/链接]
analyzed: [分析字数]字 / [章节数]章
---

# 写作风格 Skill：[小说名]风格

## 身份
你是一位模仿[作者名/小说名]风格的写作者。你的文字必须读起来像是该作者写的。

## 硬性规则（不可违反）
[从 writing_rules 中提取的所有规则，每条一行]

## 定量约束（精确数字）
- 句子平均长度：[X]字（标准差[Y]，允许范围[X-Y]到[X+Y]）
- 段落平均长度：[X]句
- 对话占比：[X]%（允许范围[X-10]%到[X+10]%）
- 逗号/句号比：[X]:1
- 省略号频率：每1000字[X]次
- 章节字数：[X]字（允许范围[X-500]到[X+500]）

## 功能词指纹（每1000字目标频率）
- "的"：[X]次 | "了"：[X]次 | "着"：[X]次
- "却"：[X]次 | "便"：[X]次 | "竟"：[X]次
- 偏离超过50%时需要调整

## 叙事约束
- 视角：[第几人称]
- 章节开头：[分布描述]
- 章节结尾：[分布描述]
- 句式节奏：[具体模式]

## 对话约束
- 对话标签分布：动作代替[X]% / 无标签[Y]% / 说道类[Z]%
- 角色区分度：[高/中/低]
- 动作插入频率：[描述]

## 描写约束
- 环境描写：每章最多[X]处，每处不超过[X]句
- 感官分布：视觉[X]%/听觉[Y]%/触觉[Z]%/嗅觉[W]%
- 心理描写：[方式]

## 节奏约束
- 高潮频率：[描述]
- 悬念方式：[描述]
- 情绪模式：[描述]

## 跨章多样性约束（从跨章聚合数据自动生成）

**⚠️ 此节必须包含——缺失此节的 skill 会导致 AI 生成的连续章节严重同质化。**

### 开头/结尾反重复（从 cross_chapter_diversity 数据生成）
- 开头类型分布：[从 opening_type_distribution 填入，如 "场景型15%/静场定格12%/动作切入12%/..."]
- 开头连续重复率：[填入实测数据，如 "0%——连续章绝对不重复"]
- 结尾类型分布：[从 ending_type_distribution 填入]
- 结尾连续重复率：[填入实测数据]
- **执行规则**：开写前检查 manifest.recent_openings，选择与前2-3章完全不同的类型

### 拟声词使用（从逐章统计聚合）
- 每章拟声词频次：均值[X]次，[Y]%章节为0次
- 日常/对话章：[X-Y]次，战斗/高潮章：[X-Y]次
- **禁止每章都堆拟声词做过渡——原作[Y]%章节完全不用拟声**

### 环境感官分布模式（从逐章感官数据聚合）
- 模式类型：[集中爆发型/均匀分布型]
- 高危重复元素：[从 high_risk_env_anchors 填入，如 "灯光——超过30%章节出现"]
- **规则**：每章选1-2种感官做主力，连续章主力感官不同；高危元素连续出现不超过2章

### 场景过渡方式分布（从逐章过渡清单聚合）
- 过渡方式分布：[从 transition_method_distribution 填入]
- 最常用过渡：[填入，如 "省略号独立段——不是拟声词"]
- **规则**：每章至少用2种不同过渡方式

### 角色对话长度合规
- 各角色实测平均单句字数：[从聚合数据填入]
- **voice_pack 执行规则**：角色对话长度必须严格匹配定义；同一吐槽/梗全书只用1次

## 描写技法约束（从 B3 维度聚合数据生成）

**⚠️ 此节必须包含——缺失此节的 skill 会导致 AI 用默认的"叙述者全知描写"替代作者的个性化技法。**

### 人物引入
- 作者偏好的引入方式：[从聚合数据填入，如 "路人群像视角观察 40% / 动作先行 25% / 装备侧写 15% / 对比反差 20%"]
- 禁忌：[如 "从不用叙述者全知视角直接介绍角色的身高年龄性格"]
- 核心规则：[如 "新角色出场先写动作/外观一个细节，不做全面介绍；背景信息通过其他角色的对话传递"]

### 环境描写
- 密度模式：[如 "极简型——每章 1-2 处，每处 2-3 句"]
- 偏好技法：[如 "两字锚点定场（'老城区外。'）+ 感官单点深入（只用一种感官具象化）"]
- 禁忌：[如 "从不连续 3 句铺排视觉环境；从不用排比渲染天空/风/落叶"]
- 环境通过对话传递：[如 "角色抱怨冷/热/味道来侧面展现环境，不靠叙述者铺陈"]

### 战斗/冲突描写
- 节拍公式：[如 "三拍：拟声独立段 → 长句具象动作 → 短句定性判断"]
- 镜头切换：[如 "地面仰视 → 全景落地 → 特写武器/表情"]
- 动作分解：[如 "重要动作拆成 5-9 个连续独立短段"]
- 冷却间隔：[如 "战后插入一个日常动作降温（掏烟/擦刀/喝水），不直接切下一场"]
- 禁忌：[如 "从不用'电光火石之间'等时间修饰词；从不在战斗中插入长段心理分析"]

### 心理描写
- 核心策略：[如 "身体外显为主（胃液翻滚/攥拳/呼吸粗重），禁止'他感到XX'"]
- 情绪爆发：[如 "每 N 章允许 1 次排比式内心独白（脏话+列举式回顾），限 1 段"]
- 吐槽路线：[如 "内心独白走'代入他人视角吐槽自己'路线，不走主角直陈感受"]
- 禁忌：[如 "从不写'一种名为XX的情绪涌上心头'；从不用三句排比描写情绪"]

## 章际衔接约束（从 B3 dim26-27 聚合数据生成）

**⚠️ 此节控制章与章之间的过渡方式——缺失会导致每章都像独立短篇，缺乏连续阅读的流畅感。**

### 衔接类型分布
[从聚合数据填入，如：]
| 类型 | 频率 | 适用场景 |
|---|---|---|
| 直接承接（同一时空） | X% | 连续动作/战斗章之间 |
| 信息炸弹→静默回响 | X% | 重大揭露后 |
| 时间跳跃 | X% | 仅日常章之间 |
| 空间跳转 | X% | 切换 POV 或场景 |
| 情绪落差 | X% | 高潮→日常 |
| 悬念承接→新视角 | X% | 命令→执行者出场 |

### 衔接规则
- [从聚合数据推导，如 "连续动作章之间必须用直接承接——上章末尾的动作/声音在下章开头被角色感知到"]
- [如 "大信息炸弹后的下一章用静默/疲惫/消化式开场"]
- [如 "时间跳跃只允许在日常→日常的章节之间使用"]
- [如 "空间跳转时用独立短句锚点（'XX市，某地。'）定场"]

## 叙事工艺约束（从 B4 维度聚合数据生成）

**⚠️ 此节覆盖AI最容易犯错的叙事层面——不是"写什么"而是"怎么讲"。**

### 场景vs概述
- 全书场景占比均值：[X]%（AI默认100%场景，好作者通常60-80%）
- 何时用概述：[如 "日常过渡用概述压缩——'接下来三天，许遥照常值夜班。'一句跳过72小时"]
- 何时用场景：[如 "所有对话/冲突/情感爆发都用实时场景展开"]
- **规则**：每章至少有1处概述段压缩时间——不要把所有事都写成实时场景

### 钩子密度和位置
- 每章钩子均值：[X]个（AI默认0或1，好作者通常3-5个）
- 位置分布：开头[X]% / 中段[X]% / 结尾[X]%
- 最常用钩子类型：[如 "信息悬念40%/角色秘密25%/危机预告20%/伏笔抛出15%"]
- **规则**：每章至少3个钩子，不能全堆在章末——中段至少1个

### 留白/潜台词
- 作者的留白频率：[如 "约60%的章节有至少1处明确留白"]
- 留白类型偏好：[如 "对话留白最多——角色说一半停住，用省略号或动作替代"]
- **规则**：每章至少1处"没有写出来但读者能感受到"的内容——对话不要把意思全说透，情感爆发点反而克制

### 时间操控
- 时间密度比分布：[如 "高潮章：1章=10分钟，日常章：1章=6小时"]
- 压缩手法：[如 "用一句过渡句跳过——'第二天早上。''三天后。'"]
- 展开手法：[如 "关键5分钟展开为整章——每个动作拆成独立短段"]
- **规则**：不是每章都要覆盖同样长的故事时间——根据内容重要性动态调节

### 信息差管理
- 使用频率：[如 "约50%章节使用读者-角色信息差"]
- 主要类型：[如 "读者知道危险但角色不知道（制造紧张）占60%"]
- **规则**：不要一次性把所有信息倒给读者——让读者比角色多知道一点点来制造期待和紧张

### 情绪节拍模板
- TOP3 情绪曲线模板：[如：]
  1. 低开→中升→高潮爆发→章末炸弹（占40%）
  2. 紧张开场→短暂缓和→二次高潮→留白收束（占30%）
  3. 平稳日常→突发事件→快速收尾（占20%）
- 高潮位置均值：[如 "通常在章节的65-80%位置"]
- **规则**：每章必须有至少1个情绪高点，位置不能总在同一处

### 叙事距离
- 模式：[如 "动态调节型——日常段拉远（客观叙述），关键时刻贴近（角色内心感受）"]
- 切换频率：[如 "每章3-5次距离切换"]
- **规则**：不要全章保持同一距离——紧张时贴近角色感官，过渡时拉远用概述

## 禁用词（该作者从不使用）
[列表]

## 禁用句式（该作者从不这样写）
[never_sentence_patterns 列表]

## 禁用过渡（该作者从不这样转场）
[never_transitions 列表]

## 禁用对话标签
[never_dialogue_tags 列表]

## 标志性表达（该作者的招牌）
[列表]

## 黄金段落库（按场景类型分类的原文参考）

### 动作/打斗场景参考
[golden_action 段落，每段标注来源章节]

### 对话场景参考
[golden_dialogue 段落]

### 环境/氛围描写参考
[golden_description 段落]

### 心理/情绪描写参考
[golden_psychology 段落]

### 章节开头参考
[golden_opening 段落]

### 章节结尾参考
[golden_ending 段落]

### 场景转换参考
[golden_transition 段落]

## 反面示例（绝对不要写成这样）
[anti_samples 段落]

## 风格演变说明（如有）
[style_evolution.summary]
```

这个 Skill 文件可以：
- 直接作为 Agent 子任务的系统指令前缀注入
- 跨项目复用（从风格库加载）
- 被用户手动编辑微调

---

## 第三步 ¾：增量更新与版本管理

**核心原则：蒸馏结果支持增量追加，不必每次从零开始。**

### 增量蒸馏场景

- 用户后续提供了同一作者的新章节/新作品
- 写作过程中发现风格偏移，需要补充蒸馏
- 从风格库加载旧档案后，追加新数据精化

### 增量更新流程

```
检测是否已有 _数据库/作者风格.json：
├── 不存在 → 全新蒸馏（正常流程）
└── 已存在 → 增量模式：
    1. Read 现有 作者风格.json
    2. 对比 analyzed_chapters 字段，确定哪些章节是新的
    3. 只对新章节启动子代理蒸馏
    4. 将新结果与旧数据合并：
       - quantitative：加权平均（新旧章节数为权重）
       - golden_passages：新段落与旧段落合并，每类保留最佳8段
       - anti_patterns：去重追加
       - writing_rules：去重追加
       - style_evolution：追加新阶段
    5. 更新版本号
```

### 版本管理

在 作者风格.json 中维护版本信息：

```json
{
  "version": {
    "current": 3,
    "history": [
      {"v": 1, "chapters": "1-200", "date": "2026-01-10", "total_words": 600000},
      {"v": 2, "chapters": "1-800", "date": "2026-01-15", "total_words": 2400000},
      {"v": 3, "chapters": "1-2033", "date": "2026-01-20", "total_words": 6000000}
    ]
  },
  "analyzed_chapters": 2033,
  "last_update": "2026-01-20"
}
```

### 合并规则

| 字段类型 | 合并策略 |
|----------|----------|
| 数值统计（mean/std） | 加权平均（按章节数） |
| 数组（golden_passages） | 合并后按质量排序，保留 top N |
| 数组（anti_patterns） | 去重追加 |
| 数组（writing_rules） | 去重追加，冲突时以新版为准 |
| 分布（chapter_opening_distribution） | 重新计算全量分布 |
| 演变（style_evolution） | 追加新阶段，不修改旧阶段 |

### Skill 文件同步

增量更新 作者风格.json 后，同步更新 `风格库/[小说名]_skill.md`：
- 覆盖写入（Skill 文件始终反映最新状态）
- 在文件头部 frontmatter 中更新 `analyzed` 字段

---

### 阶段 1 完成标记（plan-step 2）

阶段 1 全部完成（全书蒸馏 → 作者风格.json 落盘 → skill.md 生成 → 增量合并）后，必须执行：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2 \
  --output "workspace/styles/<书名>/作者风格.json"
# 脚本会校验 作者风格.json 是否真的存在，不存在直接 FileNotFoundError
```

**⚠️ 红线**：阶段 1 完成 ≠ 命令完成！必须继续阶段 2-6 才能声称蒸馏完成。

---

## 阶段 2：复刻测试（v14 新增 · 核心闭环起点）

**目的**：用阶段 1 产出的 skill v0 复刻 3 个测试段，与原文同类型段落对比，暴露 skill 表层规则在实际写作中的失效点。

### 测试场景设计（5 段；含 cluster 级 test5）

1. **复刻一个章节开头**（1500-2500 字）—— 测试开章类型多样性、首句钩子、群众视角、异象抛出
2. **复刻一个对白场景**（2000-3000 字）—— 测试对话占比、角色声纹差异、对话标签、外显反差喜剧
3. **复刻一个章末场景**（1000-1500 字）—— 测试章末类型、单段硬收、信息炸弹、钩子节流
4. **复刻3个连续章节开头**（每段300-500字）—— **跨章多样性核心验证**
   - 在 prompt 中明确告知第1个开头是"ch5"，第2个是"ch6"，第3个是"ch7"
   - 3 个开头必须使用完全不同的开头类型和焦点元素
   - 如果 3 个开头出现相同类型或相同焦点元素（如都用灯光/走廊/窗户），**skill 的跨章约束不合格，必须修正**
   - 同时检查拟声词用量：3 个开头的拟声词总数应与原作同等长度的拟声词分布一致
5. **复刻一个完整故事块 cluster**（14000-16000 字，4-6 章）—— **cluster 级仿写核心验证**
   - 参照原作某个具体 cluster（从 `cluster_index.json` 任选 1 个 chapter_range 5 章左右的）
   - 用 `gen_writer.py --cluster` 模式生成完整 cluster_draft → splitter 切章
   - **测核心维度**：cluster arc 形状（应符合原作 Reagan 6 形状）/ Sudowrite tension dial 1-11 曲线对齐 / mid_checkpoint 张力一致 / Stanford 6 主角画像匹配 / 章际衔接套用 `narrative_continuity_template`
   - 这段是**最难复刻的部分**——单段精彩易，整 cluster 节奏对齐难
   - 业界依据（Round 1 调研）：LumberChunker (EMNLP 2024) 实测 variable-length cluster 比单段更能暴露风格漂移

### 每段复刻样本自动 AI 复核

每段生成后**立即**调 `ai_wrapper.py` 让 gen-model 二次复核「这段仿写是否符合作者风格」（与人工 SFS 评分形成 hybrid pipeline · 业界共识 rules + LLM judge = 78.5% vs LLM-only 66.2%）：

```bash
# input 路径必须 = gen_writer 真实产出（章节/cluster_NNN_draft/cluster_NNN_draft.txt）
for i in 1 2 3 4 5; do
  CID=$(printf '%03d' $i)
  python core/scripts/ai_wrapper.py \
    --input "$TEST_ROOT/章节/cluster_${CID}_draft/cluster_${CID}_draft.txt" \
    --task "复刻测试：判断本段是否符合作者风格。检查 1) 句长/段长是否符合 skill 量化基线 2) 章首/章末类型是否在作者偏好分布 3) 对话占比/拟声密度/禁用词命中 4) 是否有 AI 套话/塑料感。给出 agreement (agree/disagree/partial) + 具体修正建议。" \
    --context-file "workspace/styles/<书名>/skill_FINAL.md" \
    --profile-lock "$TEST_ROOT/_gen_model_profile_locked.json"
done

# test5（cluster 级）额外加 cluster arc 对照（同样 --profile-lock）
python core/scripts/ai_wrapper.py \
  --input "$TEST_ROOT/cluster_arc_replica.json" \
  --task "cluster 级仿写：对照原作 cluster_arc，判断复刻 cluster 的 Reagan shape / Sudowrite dial / 主角 Stanford 6 维曲线是否对齐。" \
  --context-file "workspace/styles/<书名>/arc_templates/cluster_arc_<参照 id>.json" \
  --profile-lock "$TEST_ROOT/_gen_model_profile_locked.json"
```

主代理收到 `.ai_review.json` 后：
- `agreement=disagree` 且 `confidence > 0.7` → **必须重写本段**（升 round Y+1）
- `agreement=partial` → 局部修正后进入阶段 3
- `agreement=agree` → 直接进入阶段 3 量化对比

### test1-5 全部走 gen_writer.py 统一 pipeline

**业界依据**（`.research_cache/inspiration_tool_model_alignment_2026-05-24.md` · Round 1 调研 17 来源）：

> **核心原则**：「**freeze the harness, swap only the model**」（Arize · 业界共识）

「蒸馏仿写测试 ≠ 正式写作」是典型 **training-serving skew**（Evidently AI 教科书定义），LLM 时代被 **prompt sensitivity** 放大——arxiv 2509.01790 实证 **prompt 微差可致 76 准确率点波动**。修法只能是基础设施层（共享 fixture + 统一 harness），不是 prompt 层。

**必须冻结的 5 件套**（缺一即翻车）：

| # | 冻结项 | 我们的实现 |
|---|---|---|
| 1 | **prompt template** | 5 段全走 `gen_writer.py` 的内部 prompt（含 7 项硬铁律 + 元 anti-slop） |
| 2 | **tool pipeline** | 同一调用链 build_manifest → gen_writer → splitter |
| 3 | **fixture project** | Step A 公共准备 · `_数据库/作者风格.json` + `事件簇.json` + `进度.json` 最小化 fixture |
| 4 | **scoring & judge** | `style_evaluator.py` + `ai_wrapper.py` 同一 judge profile |
| 5 | **model profile** | Step A 锁到 `_gen_model_profile_locked.json` · 所有 round 强制用同 profile |

参考实现：Arize Acme SDK fictional fixture repo + EleutherAI lm-evaluation-harness。

**对齐做法**：5 段全部走 `gen_writer.py --cluster` 同套架构（与小说写作流水线相同），只用 `--target-cjk` 区分字数。

### Step A: 公共准备 — 最小化测试项目（5 段共用）

```bash
TEST_ROOT="workspace/styles/<书名>/复刻测试/v{N}_round{Y}"
mkdir -p "$TEST_ROOT/_数据库" "$TEST_ROOT/章节"

# 1. 复制 skill（gen_writer 的核心 input）
cp "workspace/styles/<书名>/作者风格_FINAL.json" "$TEST_ROOT/_数据库/作者风格.json"

# 2. 锁定 gen-model profile（用 Python 直接序列化 JSON · gen_model show 是文本不是 JSON）
python -c "
import json, sys, pathlib
sys.path.insert(0, 'core/scripts')
from gen_model_loader import GenModelLoader, GenModelConfigError
from datetime import datetime
try:
    loader = GenModelLoader()
    p = loader.get_active_profile()
    lock = {
        'profile_name': p.name,
        'model': p.model,
        'base_url': p.base_url,
        'temperature': p.temperature,
        'locked_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        '_doc': 'profile lock · ai_wrapper 必须验证 active == locked',
    }
    pathlib.Path('$TEST_ROOT/_gen_model_profile_locked.json').write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[OK] profile locked: {p.name}')
except GenModelConfigError as e:
    pathlib.Path('$TEST_ROOT/_gen_model_profile_locked.json').write_text(json.dumps({'_no_active_profile': True, 'error': str(e)}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[WARN] no active profile（lock 文件含 _no_active_profile=true，ai_wrapper 见此跳过校验）')
"

# 3. fixture project 加 4 个核心 JSON（防 build_manifest 28 字段 fallback）
#    业界依据：Arize Acme SDK 完整 fictional fixture · 必须含所有 collector 期望的字段
python -c "
import json, pathlib
# 3a. 人物卡.json — schema 必须是 {'characters': [{id, name, role}, ...]} 列表
#     不是之前的 {name: {...}} dict！build_manifest 期望列表 schema
style_root = 'workspace/styles/<书名>'
ca_dir = pathlib.Path(style_root) / 'character_arcs'
characters_list = []
if ca_dir.exists():
    arcs = []
    for f in ca_dir.glob('*_emotion_arc.json'):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            s6 = d.get('stanford_6_component', {})
            arcs.append((d, s6.get('overall_importance', 0)))
        except: pass
    arcs.sort(key=lambda x: -x[1])
    for idx, (d, imp) in enumerate(arcs[:3], 1):
        name = d.get('character', f'char_{idx}')
        characters_list.append({
            'id': f'CHAR_{idx:03d}',
            'name': name,
            'role': '主角' if d.get('stanford_6_component', {}).get('tier') == 'protagonist' else '配角',
            'importance': imp,
            'avg_actor_intensity': d.get('average_actor_intensity'),
            'avg_experiencer_intensity': d.get('average_experiencer_intensity'),
            'voice_pack': {'dialogue_avg_chars': 15, 'placeholder': '从原作 voice_pack 继承'},
            '_doc': 'fixture',
        })
if not characters_list:
    characters_list = [
        {'id': 'CHAR_001', 'name': '测试主角', 'role': '主角', 'voice_pack': {'dialogue_avg_chars': 15, 'placeholder': '通用主角'}, '_doc': 'fallback'},
        {'id': 'CHAR_002', 'name': '测试配角', 'role': '配角', 'voice_pack': {'dialogue_avg_chars': 12, 'placeholder': '通用配角'}, '_doc': 'fallback'},
    ]
pathlib.Path('$TEST_ROOT/_数据库/人物卡.json').write_text(json.dumps({'characters': characters_list}, ensure_ascii=False, indent=2), encoding='utf-8')

# 3b. 世界观.json — 从蒸馏库 meta 继承 + 占位
import json as _j
style_meta = _j.loads(open(f'{style_root}/作者风格_FINAL.json', encoding='utf-8').read()).get('meta', {})
world = {
    'work_genre': style_meta.get('work', '未知') + ' · 复刻测试',
    'setting_keywords': ['沿用原作设定 · 复刻测试不引入新设定'],
    '_doc': 'fixture · 复刻测试 · 不引入新世界观（避免污染评估）',
}
pathlib.Path('$TEST_ROOT/_数据库/世界观.json').write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding='utf-8')

# 3c. 用户偏好.json — 从原作风格反推 storyteller_profile（防 narrative_pacing 走默认）
prefs = {
    'narrative_pacing': {
        'storyteller_profile': 'cassandra',   # 默认均衡型，避免 randy 激进/phoebe 偏 win 偏倚
        'happy_vs_dark_ratio': 0.5,
        '_doc': '复刻测试默认均衡 profile；正式项目按用户实际偏好设',
    },
    'interactive_mode': {'fate_cards_count': 2, 'fully_auto': True},
    'ecas_config': {'critical_events_use_opus': []},
    'audit_mode': 'standard',
    '_doc': 'fixture · 复刻测试 default preferences',
}
pathlib.Path('$TEST_ROOT/_数据库/用户偏好.json').write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding='utf-8')

# 3d. 场景规则.json — 通用场景类型规则
scene_rules = {
    'scenes': [
        {'type': 'opening', 'rules': '章首钩子 / 感官开场 / 避免环境长铺垫'},
        {'type': 'dialogue', 'rules': '对话占比 ≥ 60% / 动作标签优先 / 禁情绪标签'},
        {'type': 'ending', 'rules': '章末类型五选一：信息炸弹/拟声/独立短句/动作留白/章题回扣'},
        {'type': 'cluster', 'rules': 'arc 形状对齐原作 / mid_checkpoint 张力一致 / 章际衔接落 template'},
    ],
    '_doc': 'fixture · 通用场景规则',
}
pathlib.Path('$TEST_ROOT/_数据库/场景规则.json').write_text(json.dumps(scene_rules, ensure_ascii=False, indent=2), encoding='utf-8')

# 3e-3h. 加 4 个剩余核心 JSON 空 schema 占位
#       不准备会被 build_manifest 28 collector 当中余下的 fallback，缺写作上下文
pathlib.Path('$TEST_ROOT/_数据库/伏笔表.json').write_text(json.dumps({'foreshadows': [], 'active_pledges': [], 'hidden_secrets': []}, ensure_ascii=False, indent=2), encoding='utf-8')
pathlib.Path('$TEST_ROOT/_数据库/故事块摘要.json').write_text(json.dumps({'chapters': []}, ensure_ascii=False, indent=2), encoding='utf-8')
pathlib.Path('$TEST_ROOT/_数据库/写作经验.json').write_text(json.dumps({'success_patterns': [], 'failure_patterns': [], 'preferences': []}, ensure_ascii=False, indent=2), encoding='utf-8')
pathlib.Path('$TEST_ROOT/_数据库/关系.json').write_text(json.dumps({'relationships': []}, ensure_ascii=False, indent=2), encoding='utf-8')

print('[Step A 3] fixture project 准备完毕：作者风格 + 人物卡(列表) + 世界观 + 用户偏好 + 场景规则 + 伏笔表 + 故事块摘要 + 写作经验 + 关系 + (后续) 事件簇/进度')
"

# 3i. Step A 末尾跑 style_injector 预生成 .style_directive/ch_001.json
#     防 build_manifest 读不到 style_directive 字段
mkdir -p "$TEST_ROOT/_数据库/.style_directive"
for ch in 1 2 3 4 5; do
  python core/scripts/style_injector.py "$TEST_ROOT" "$ch" 2>/dev/null || \
    echo "{}" > "$TEST_ROOT/_数据库/.style_directive/ch_$(printf '%03d' $ch).json"
done
echo "[Step A 4] style_directive 预生成（ch_001 - ch_005）"

# 4. 建 5 个 test 对应的 cluster 占位（cluster_id 必须 int · gen_writer --cluster 期望 int）
python -c "
import json, pathlib
clusters = [
    {'cluster_id': 1, 'cluster_name': 'test1_opening',        'estimated_chapters': 1, 'expected_word_range': {'min': 1500, 'max': 2500, 'unit': 'CJK_chars'}, 'scope_summary': '复刻测试 1: 章节开头'},
    {'cluster_id': 2, 'cluster_name': 'test2_dialogue',       'estimated_chapters': 1, 'expected_word_range': {'min': 2000, 'max': 3000, 'unit': 'CJK_chars'}, 'scope_summary': '复刻测试 2: 对白场景'},
    {'cluster_id': 3, 'cluster_name': 'test3_ending',         'estimated_chapters': 1, 'expected_word_range': {'min': 1000, 'max': 1500, 'unit': 'CJK_chars'}, 'scope_summary': '复刻测试 3: 章末场景'},
    {'cluster_id': 4, 'cluster_name': 'test4_three_openings', 'estimated_chapters': 3, 'expected_word_range': {'min': 900,  'max': 1500, 'unit': 'CJK_chars'}, 'scope_summary': '复刻测试 4: 连续 3 章首（每段 300-500 字）'},
    {'cluster_id': 5, 'cluster_name': 'test5_full_cluster',   'estimated_chapters': 5, 'expected_word_range': {'min': 13000, 'max': 16000, 'unit': 'CJK_chars'}, 'scope_summary': '复刻测试 5: 完整 cluster · 参照 <REF_CLUSTER_ID>'},
]
for c in clusters:
    c['status'] = 'pending'
    c['mid_checkpoints'] = [int(c['expected_word_range']['max'] / 3), int(c['expected_word_range']['max'] * 2 / 3)]
import pathlib
pathlib.Path('$TEST_ROOT/_数据库/事件簇.json').write_text(json.dumps({'schema_version': 'v23.0', 'clusters': clusters}, ensure_ascii=False, indent=2), encoding='utf-8')
pathlib.Path('$TEST_ROOT/_数据库/进度.json').write_text(json.dumps({'cluster_blueprint': []}, ensure_ascii=False, indent=2), encoding='utf-8')
"
```

### Step B: 5 段 gen_writer.py 调用（与小说写作同一 pipeline）

```bash
# test1 章节开头（single 模式，cluster=1）
python core/scripts/gen_writer.py \
  --project "$TEST_ROOT" \
  --cluster 1 \
  --chapter-start 1 --chapter-end 1 \
  --target-cjk 1500-2500

# test2 对白场景（cluster=2）
python core/scripts/gen_writer.py \
  --project "$TEST_ROOT" \
  --cluster 2 \
  --chapter-start 1 --chapter-end 1 \
  --target-cjk 2000-3000

# test3 章末场景（cluster=3）
python core/scripts/gen_writer.py \
  --project "$TEST_ROOT" \
  --cluster 3 \
  --chapter-start 1 --chapter-end 1 \
  --target-cjk 1000-1500

# test4 3 连续章首（cluster=4 · ECAS-lite，多章窗口但每章字数小）
python core/scripts/gen_writer.py \
  --project "$TEST_ROOT" \
  --cluster 4 \
  --chapter-start 5 --chapter-end 7 \
  --target-cjk 900-1500
# 主代理需在 prompt 中明确告知"只写 3 个开头各 300-500 字"（通过 cluster.scope_summary 注入）

# test5 完整 cluster（cluster=5 · ECAS 模式 · 参照 cluster_index 中某个 cluster）
# 注意：test5 需要先跑下方「test5 cluster 复刻执行节」的 Step 1-2 选好参照 cluster_id
# 才能跑这个 gen_writer 调用
python core/scripts/gen_writer.py \
  --project "$TEST_ROOT" \
  --cluster 5 \
  --chapter-start 1 --chapter-end 5 \
  --target-cjk 13000-16000

# 产出: $TEST_ROOT/章节/cluster_001_draft/cluster_001_draft.txt
#       $TEST_ROOT/章节/cluster_002_draft/cluster_002_draft.txt
#       $TEST_ROOT/章节/cluster_003_draft/cluster_003_draft.txt
#       $TEST_ROOT/章节/cluster_004_draft/cluster_004_draft.txt
#       $TEST_ROOT/章节/cluster_005_draft/cluster_005_draft.txt
```

**对齐验收要点**：
- ✅ 5 段都走 `gen_writer.py`，复用同一 manifest 注入 / 同一 7 项硬铁律 / 同一 gen-model profile
- ✅ profile 锁定到 `_gen_model_profile_locked.json`（每轮 round 必须用同 profile）
- ✅ 测试目录的 _数据库/作者风格.json 直接复用 skill_FINAL（不偷换 prompt）
- ❌ **禁止**：spawn 临时 prompt agent / 手写 prompt 跳过 gen_writer / 不锁 profile

### Step C: 后置完整流水线（与 write-chapter 对齐）

write-chapter 正式流水线 step 3-4 含**双轨质量分析 + voice-keeper**，蒸馏测试缺这些 → 评分失真。Step C 补齐对齐：

```bash
# === C1 (Gap 4 · test5 splitter) · ECAS pipeline 必跑 ===
# test5 多章 cluster_draft 切章（test1-4 单章不需要 · chapter_splitter.py 脚本只支持 DCAS 双章 · 多章必 spawn agent）
# Agent({
#   description: "蒸馏测试 splitter test5",
#   subagent_type: "novel-chapter-splitter",
#   prompt: "
#     PROJECT: $TEST_ROOT
#     DRAFT: $TEST_ROOT/章节/cluster_005_draft/cluster_005_draft.txt
#     CLUSTER_RANGE: 1-5
#   "
# })
# splitter 切完所有章节后 → 主代理必须调 gen_chapter_titles.py
python core/scripts/gen_chapter_titles.py \
  --project "$TEST_ROOT" \
  --chapters 1-5
# 产出: $TEST_ROOT/章节/第NNN章/第NNN章.txt（5 章独立 · 含网文化标题）

# === C2 (audit_hub 7 scanner) · 5 段都跑 ===
# audit_hub 接口: <project> <ch:int>（章节号不是 cluster_id！）
# test1-4 是 single 模式 cluster_draft 即正文 → 复制到 第NNN章.txt 让 audit_hub 识别
# test5 splitter 切完已有 第001-005章.txt → 直接跑
for cid in 1 2 3 4 5; do
  CID=$(printf '%03d' $cid)
  DRAFT="$TEST_ROOT/章节/cluster_${CID}_draft/cluster_${CID}_draft.txt"
  # 把 cluster_draft 复制成 audit_hub 期望的章节路径（test5 splitter 已建好则跳）
  CHAPTER_DIR="$TEST_ROOT/章节/第${CID}章"
  if [ -f "$DRAFT" ] && [ ! -f "$CHAPTER_DIR/第${CID}章.txt" ]; then
    mkdir -p "$CHAPTER_DIR"
    cp "$DRAFT" "$CHAPTER_DIR/第${CID}章.txt"
    # 同步建空 changes.json（audit_hub 期望两文件齐全）
    echo '{"factual": {}, "self_eval": {}}' > "$CHAPTER_DIR/第${CID}章_changes.json"
  fi
  python core/scripts/audit_hub.py "$TEST_ROOT" "$cid" \
    --waivers "$TEST_ROOT/_数据库/.审计豁免.json" 2>&1 | tail -5 || \
    echo "[align] audit_hub ch $cid 跑完"
done
# 输出: $TEST_ROOT/_数据库/.judge_reports/ch_NNN_audit-hub.json
# 含 7 scanner（validate_chapter/style/narrative/plot/hook/golden/semantic）+ character_arc_drift

# === C3 (Gap 3 · voice-keeper) · test2_dialogue 必跑（含丰富对话） ===
# 主代理必须执行（非注释）以下 Agent spawn — 不可跳过
```

**【主代理必跑 C3】** spawn `novel-voice-checker` agent，prompt 严格按下方契约（**PLAN_ID/STEP/PROJECT/CHAPTER/MANIFEST 五行不可缺**）：

```
PLAN_ID: <plan_id>
STEP: 3
PROJECT: <TEST_ROOT>
CHAPTER: 2
MANIFEST: <TEST_ROOT>/_数据库/.manifest/ch_002.json
任务：审 cluster_002_draft（test2_dialogue 段）所有角色对话声纹是否符合 _数据库/人物卡.json 的 voice_pack。
检测到 OOC 对话 → 输出 voice fix brief JSON。
PROFILE_LOCK: <TEST_ROOT>/_gen_model_profile_locked.json
```

agent 输出 voice fix brief → 主代理调 `gen_fixer.py --mode voice-fix --brief <brief.json>` 修复。

```bash
# === C4 (Gap 6 · reading-reflector 8 维) · test5 cluster 必跑 ===
# 主代理必须执行（非注释）以下 Agent spawn — 不可跳过
```

**【主代理必跑 C4】** spawn `novel-reading-reflector` agent（必须循环到连续 3 轮 0 issue 才 pass）：

```
PROJECT: <TEST_ROOT>
CHAPTERS: 1,2,3,4,5
ROUND: 1
MAX_ROUNDS: 5
PROFILE_LOCK: <TEST_ROOT>/_gen_model_profile_locked.json
任务：8 大维度审 test5 cluster 切完的 5 章（段首单调/句式重复/voice 漂移/POV 一致性/信息密度/节奏感/对话工艺/塑料感）。
连续 3 轮 0 issue 才 verdict=pass。
```

```bash
# === C5 (Gap 5 · profile lock 在 ai_wrapper 强制 · 已在阶段 2 ai_wrapper 调用全部实现) ===
# 阶段 2 所有 ai_wrapper 调用必须加：
#   --profile-lock "$TEST_ROOT/_gen_model_profile_locked.json"
# Step C2 audit_hub 完成后，主代理同时调 ai_wrapper 复核 audit_hub 输出（防 audit_hub 误判）
for cid in 1 2 3 4 5; do
  CID=$(printf '%03d' $cid)
  AUDIT="$TEST_ROOT/_数据库/.judge_reports/ch_${CID}_audit-hub.json"
  [ -f "$AUDIT" ] && python core/scripts/ai_wrapper.py \
    --input "$AUDIT" \
    --task "复核 audit_hub 输出。判断 hard_gate/advisory issues 是否真实问题还是 false positive。" \
    --context-file "$TEST_ROOT/章节/第${CID}章/第${CID}章.txt" \
    --profile-lock "$TEST_ROOT/_gen_model_profile_locked.json"
done
```

**Step C 对齐验收**：
- ✅ Gap 2 audit_hub 7 scanner 跑（与 production step 3 机械轨对齐）
- ✅ Gap 3 voice-keeper 在 test2 强制（与 production step 4 对齐）
- ✅ Gap 4 test5 splitter 跑（与 ECAS pipeline 对齐）
- ✅ Gap 6 reading-reflector 8 维 ≥ 3 轮 0 issue（与 production 阅读轨对齐）
- ✅ Gap 5 ai_wrapper --profile-lock 强制（违反 → exit · 不 fallback）

### test5 cluster 复刻 — 在 Step A/B 之上补 ref 参照 + 评估

**注意**：test5 的 gen_writer 调用已在 Step B 完成（`--cluster 5`），本节只补充 test5 特有的「参照 cluster 选取 + ref 文本拼接 + cluster 级评估」。

```bash
# Step 1: 从 cluster_index.json 选参照 cluster（5 章左右），并把它的 cluster_id 写回 Step A 的 test5 cluster.scope_summary 的 <REF_CLUSTER_ID> 占位
CLUSTER_ID=$(python -c "
import json, random
ci = json.load(open('workspace/styles/<书名>/cluster_index.json', encoding='utf-8'))
mid_clusters = [c for c in ci['clusters'] if 4 <= c['chapters_count'] <= 6 and 12000 <= c['estimated_words'] <= 16000]
if mid_clusters:
    print(random.choice(mid_clusters)['cluster_id'])
")
REF_ARC="workspace/styles/<书名>/arc_templates/cluster_arc_${CLUSTER_ID}.json"

# Step 2: 把 REF_CLUSTER_ID 更新进 Step A 已建好的 _数据库/事件簇.json 的 test5 cluster.scope_summary
python -c "
import json, pathlib
ci_path = pathlib.Path('$TEST_ROOT/_数据库/事件簇.json')
ci = json.loads(ci_path.read_text(encoding='utf-8'))
for c in ci['clusters']:
    if c['cluster_id'] == 5:
        c['scope_summary'] = '复刻测试 5: 完整 cluster · 参照 $CLUSTER_ID'
        c['_ref_cluster'] = '$CLUSTER_ID'
        break
ci_path.write_text(json.dumps(ci, ensure_ascii=False, indent=2), encoding='utf-8')
"

# Step 3: gen_writer 调用 → 已在 Step B 完成（python gen_writer.py --cluster 5 ...）

# Step 4: 拼原参照 cluster 的章节文本（Step B 之后跑）
REF_TEXT_CONCAT="$TEST_ROOT/_ref_concat.txt"
TEST_DIR="$TEST_ROOT"  # 兼容下方变量名
python -c "
import json, pathlib
ref = json.load(open('$REF_ARC', encoding='utf-8'))
range_str = ref['chapter_range']  # 形如 'ch3-7'
import re
m = re.match(r'ch(\d+)-(\d+)', range_str)
start, end = int(m.group(1)), int(m.group(2))
# 拼接原参照 cluster 的章节文本
texts = []
for ch in range(start, end + 1):
    for pat in [f'第{ch}章.txt', f'ch{ch}.txt']:
        f = pathlib.Path(f'workspace/styles/<书名>/原文/{pat}')  # 用户原作存放位置
        if f.exists():
            texts.append(f.read_text(encoding='utf-8'))
            break
pathlib.Path('$REF_TEXT_CONCAT').write_text('\n\n'.join(texts), encoding='utf-8')
"
python core/scripts/style_evaluator.py \
  --ref "$REF_TEXT_CONCAT" \
  --gen "$TEST_DIR/章节/cluster_005_draft/cluster_005_draft.txt" \
  --output "$TEST_DIR/eval_cluster.json"

# Step 6: 跑 ai_wrapper 复核 cluster 整体（高层叙事 / arc 形状 / 节奏对齐）
python core/scripts/ai_wrapper.py \
  --input "$TEST_DIR/eval_cluster.json" \
  --task "cluster 级仿写对照评估。检查复刻 cluster (14000 字) vs 原参照 cluster 在 arc 形状/张力曲线/章际衔接/主角行动节奏的对齐度。" \
  --context-file "$REF_ARC"
# 产出: $TEST_DIR/eval_cluster.json.ai_review.json
```

**test5 验收门槛**：
- `reagan_shape_match=true` 或 emotion_curve cosine_sim > 0.7 → PASS
- 否则 → cluster 节奏未对齐，必须升 skill 加 cluster 级约束
- 这是阶段 5 复刻循环的最严苛终止条件——cluster 不像 = 整本仿写不像

### 阶段 2 完成标记（plan-step 3）

```bash
# 复刻样本生成后必须验证文件落盘
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3 \
  --output "workspace/styles/<书名>/复刻测试/v{N}_round{Y}/test1_opening.txt"
# 也可用 --skip-output 跳过文件校验（如果复刻目录结构与模板不一致）
```

---

## 阶段 3：多维度对比扫描（v14 新增）

**目的**：把复刻样本与原文同类型段落做 **20+ 维度量化对比**，找出 skill v{N} 还没覆盖的差距。

### 对比源（每轮必须抽 2-3 章原文做基线）

- 早期章（卷一首章）
- 中期章（卷一中段或卷二）
- 晚期章（卷尾/大结局前）
- 抽样要点：3 章须包含 1 个"对话密集章"和 1 个"动作章"

### 20+ 维度扫描清单

每个维度独立计算原文 vs 复刻样本的数值，差距 ≥X% 标红。

```
═══ A. 量化维度（用 Bash + Python 自动算）═══
1. 中文字符数
2. 段落总数
3. 段落平均长度（中文字符）
4. 段落长度分布：≤5字 / 6-15 / 16-30 / 31-50 / 50+
5. 句长分布：mean / std / min / max
6. 对话占比（% by 字数，不是行数）
7. 拟声词独立成段数（带破折号）
8. 配额词命中：突然/下一刻/下意识/莫名/似乎/仿佛/顿时/微微（每词次数）
9. 禁用词（Critical 重灾区）命中
10. 方括号设定名词数量【XX】
11. 群戏发言人数（独立角色开口数）

═══ B. 结构维度（用 Read + 人工判定）═══
12. 章首类型（场景型/静场对白型/动作型/时间地点两字段/拟声定格 五选一）
13. 章末类型（信息炸弹/独立短句/拟声锚点/动作留白/章题回扣 五选一）
14. 章节衔接类型（续场/夜跳/日跳/周跳/月跳/POV切换跳/倒叙跳）
15. 极长句数量（50+字单句）
16. 数节拍数量（'一秒，两秒，三秒'或'铛——铛——'）

═══ C. 叙事维度（用 Grep + 人工判定）═══
17. 主角情绪爆发段数（≥3 段排比内心独白）
18. 主角主动驱动情节段数（主动调查/提问/试探/反向行动）
19. 旁白吐槽段数（叙述者代入他人视角吐槽）
20. 神性代词【祂】使用次数
21. 主角家庭/关系网提及次数
22. 超自然事件的外部观察者数量
23. 市井夸张比喻数量
24. 真品牌喜剧数量

═══ D. 节奏维度（用人工判定）═══
25. 小高潮数量（事件/对白/反转）
26. 大场面密度（每章是否有 1 个画面级反差或冲突）
27. 信息密度变化（紧/松交替）

═══ E. 跨章多样性维度（用复刻测试第4段验证）═══
28. 连续3开头类型重复率（test4 的 3 个开头是否类型各不相同）
29. 连续3开头焦点元素重复率（是否用了相同的环境元素如灯/走廊/窗）
30. 拟声词密度合理性（3 个开头的拟声词总量 vs 原作同长度文本的拟声词量）
31. 感官主力轮换（3 个开头是否各用不同感官做氛围主力）
32. 过渡方式多样性（test1-3 中使用的过渡方式种类数，≥3 种为 PASS）

═══ F. 描写技法维度（对比复刻样本 vs 原文的技法差异）═══
33. 人物引入方式（复刻中新角色是否用了作者偏好的引入技法——路人视角/动作先行/装备侧写——还是 AI 默认的"叙述者全知介绍"）
34. 环境描写密度和技法（复刻的环境描写是否与原作同密度？是否用了两字锚点/感官单点深入？还是 AI 默认的"三句排比铺陈"）
35. 战斗描写节拍（如有战斗：是否遵循原作的三拍公式？是否有镜头切换和冷却间隔？还是 AI 默认的"动作+形容词堆砌"）
36. 心理描写方式（复刻的心理描写是否用了身体外显/吐槽式内心独白？还是 AI 默认的"他感到一阵XX涌上心头"）
37. 章际衔接手法（test4 的3个连续开头之间是否体现了与原作一致的衔接类型——直接承接/静默回响/空间跳转等——还是每章都像独立短篇没有承接关系）

═══ G. 叙事工艺维度（AI最易暴露的高层维度）═══
38. 场景vs概述比例（复刻样本中概述段占比——AI通常为0%，原作通常20-30%）
39. 钩子密度（复刻样本每千字有几个钩子？位置是否分散在开头/中段/结尾？还是AI默认全堆章末）
40. 留白质量（复刻样本是否有对话潜台词——角色话语的字面意思和真实意图不同？还是AI式的"把意思全说透"）
41. 时间操控（复刻样本是否有概述式时间压缩？还是AI式的"每件事都实时展开"）
42. 信息差运用（复刻样本是否营造了读者-角色信息差？还是一次性倒完所有信息）
43. 情绪曲线（复刻样本的情绪走向是否有明确高低起伏？高潮位置是否与原作模式一致？还是AI式的"匀速推进"）
44. 叙事距离（复刻样本的叙述者距离是否随场景动态调节？还是AI式的"全程恒定第三人称"）

═══ H. cluster 级评估维度（仅 test5 适用 · 独立 H1-H8 编号 · 最严苛终止条件）═══
H1. **Reagan 6 形状匹配**：复刻 cluster 拟合的 Reagan shape 是否与原参照 cluster 一致（不一致 → cluster arc 形状漂移，必须修）
H2. **emotion_curve cosine 相似度**：复刻 cluster 的 emotion_curve_normalized 与原 cluster 的余弦相似度，**目标 ≥ 0.7**
H3. **Sudowrite tension dial 对齐度**：复刻 cluster 的 1-11 dial 序列与原序列的 L1 距离均值，**目标 ≤ 1.5**
H4. **mid_checkpoint 张力对齐**：每 3000 字 checkpoint 的期望张力 vs 实际，偏差 > 0.2 标 FAIL
H5. **章际衔接套用度**：复刻 cluster 内章间衔接是否落到 `narrative_continuity_template.three_chapter_templates` 中至少 1 个模板（不落 = 章际衔接是 AI 默认而非作者风格）
H6. **主角 Stanford 6 维匹配**：复刻 cluster 中主角的 A_agency / I_interiority 等 6 维 vs 原作主角 arc，偏差 > 0.15 任一维度 → FAIL
H7. **climax 位置匹配**：复刻 cluster 的 climax_chapter_index 应在原 cluster ± 1 章范围内
H8. **cluster 字数对齐**：复刻 cluster 字数应在原 cluster ± 15% 内
H9. **前/中/后段独立打分**（Round 1 调研关键警告 · 防 generic prose 漂移）：把复刻 cluster 切成前 1/3、中 1/3、后 1/3 三段，每段独立跑 ai_wrapper 评分。**任一段 disagreement → 该段标 drift_detected**。业界共识：consistency 比 imitation 更难，LLM 几百字后会漂回 generic prose，必须切段监控。来源：EQ-Bench Longform 14 维 + WebNovelBench 8 维 + LongWriter 6 维（详见 `.research_cache/inspiration_cluster_imitation_eval_2026-05-24.md`）。
```

**H 类自动化计算**：直接用 `arc_aggregator.py --cluster auto_001` 对复刻样本跑一次，与原参照 cluster_arc JSON 对比即可（阶段 2 test5 末尾的 cluster_arc_replica.json 已含部分对比，阶段 3 把这些指标转成 standard distillation_compare 字段）。

**业界依据（Round 1 调研）**：MARCUS 2025 (arXiv 2510.18201) 把角色 arc 做成事件中心时间序列；LumberChunker EMNLP 2024 实测 cluster 级评估比段级 +7.37% 检索增益；Reagan 2016 (EPJ DS) 6 形状已成 narrative analytics 标准。

### 输出对比报告

```
启动 1 个对比分析子代理：

Agent({
  description: "蒸馏对比扫描 v{N}",
  prompt: "
    PLAN_ID: $PLAN_ID           # 必填：父 plan id
    STEP: 4                      # 必填：当前阶段 3 → step 4
    DISTILL_COMPARE: v{N}
    REFERENCE_CHAPTERS: [3 章原文路径+行号]
    REPLICA_FILES: 风格库/复刻测试/v{N}/test*.txt
    SKILL: 风格库/[小说名]_skill.md
    
    任务：
    1. 先读 _gen_model_profile_locked.json 确认所有 5 段都用同一 gen-model profile 生成；profile 不同 → 评估失真，强制 abort 重跑该 round
    2. 先跑 style_evaluator.py 获取精确量化对比：
       python core/scripts/style_evaluator.py --ref [原文章节] --gen [复刻文件] --output 风格库/对比报告/eval_v{N}.json
       这会自动计算 A 类全部量化维度（句长JSD/段落分布/对话占比/标点密度/功能词指纹等）并输出 SFS 评分
    3. 基于 eval_v{N}.json 的精确数据 + Read 抽样判断 B/C/D 类定性维度
    4. 输出 distillation_compare_v{N}.json（合并程序化数据+LLM定性判断 + 工具模型对齐元数据）：
       {
         'version': N,
         'gen_model_profile_locked': {    # 工具模型对齐
           'profile_name': '<active profile>',
           'model': '<model id>',
           'base_url': '<provider>',
           'temperature': <num>,
           'locked_at': '<ISO>'
         },
         'dimensions': [
           {'id': 1, 'name': '中文字符', 'reference_mean': X, 'replica_mean': Y, 'gap_pct': Z, 'flagged': bool},
           ...
         ],
         'flagged_count': X,
         'new_gaps_found': [
           {'dim': 'XX', 'gap_description': '...', 'fix_suggestion': '...'}
         ],
         'converged': bool  # 连续 2 轮 flagged_count ≤ 2 时为 true
       }
    
    **profile 锁定铁律**：
    - 同一 v{N} 内所有 round Y 必须用同一 profile（gen_writer.py 调用前主代理验证）
    - 跨 v{N} 升级 skill 时若换 profile → 必须在 distillation_log.md 显式标注「换 profile 重蒸 v{N}_round1」
    - 防止「换 profile 错把 skill 升级 / 实际是 profile 差异」
    
    flag 阈值（gap_pct ≥ 20% 标红，gap_pct ≥ 50% 列为 critical）
  "
})

落盘：风格库/对比报告/distillation_compare_v{N}.json
```

### 阶段 3 完成标记（plan-step 4）

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 \
  --output "workspace/styles/<书名>/对比报告/distillation_compare_v{N}.json"
# 对比报告必须真的存在；脚本会自动校验
```

---

## 阶段 4：修正反思（v14 新增 · 核心升级点）

**目的**：把阶段 3 发现的差距沉淀为新的 skill 约束 + lessons_learned 条目。

### 修正三件事

```
1. 升级 skill v{N} → v{N+1}：
   - 加入新发现的硬约束（如：'对话占比 ≥60%'）
   - 修正错误的硬规则（如：v10 把'章末单段'做成硬规则，v12 改为分布概率）
   - 调整配额词上限/下限
   - 追加禁用词或反模板红线
   - 追加 must_have_per_chapter 项

2. 追加 lessons_learned.json：
   - 每个差距 → 一条 L 级规范
   - 注明 level (critical/high/medium) + 触发条件 + 修复策略 + 范式样例

3. 追加 distillation_log.md（蒸馏迭代历史）：
   ## v{N} → v{N+1} (2026-XX-XX)
   - 复刻测试输出位置：风格库/复刻测试/v{N}/
   - 对比报告：风格库/对比报告/distillation_compare_v{N}.json
   - 发现的新差距：N 个
   - 升级要点：
     1. ...
     2. ...
   - 收敛状态：N/2 轮无新差距
```

### 执行（主代理直接操作 Edit/Write）

主代理读对比报告 → 生成 skill v{N+1} + 追加 lessons + 写 distillation_log → commit。

### 阶段 4 完成标记（plan-step 5）

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5 --skip-output
# 阶段 4 是 Edit/Write 升级 skill，没有"新增"文件可校验，用 --skip-output
```

---

## 阶段 5：复刻循环（v14 新增 · 终止条件）

**循环规则**：

```
WHILE NOT converged:
  执行阶段 2 (用最新 skill 复刻 3 个测试段)
  执行阶段 3 (对比 → 找差距)
  执行阶段 4 (修正 → 升级 skill)
  
  converged 判定（v15 升级 · 双重标准）：
    - **程序化标准**：style_evaluator.py 的 SFS_quick 分数 ≥ 88 且连续 2 轮变化 < 2 分
    - **定性标准**：连续 2 轮新发现的 critical/high 级维度 ≤ 1
    - 两个标准**同时满足**才算收敛
    - 或：迭代次数 ≥ 8（保底退出，v15 从 5 提升到 8 因为实践证明 5 轮不够）
END

报告：
  - 最终版本 vN
  - 总迭代轮数
  - **SFS_quick 分数变化曲线**（v15 新增）
  - 收敛点的关键发现
```

### 何时强制退出？

- 用户主动叫停（"够了"/"先停"）
- 迭代 ≥8 轮（v15 提升上限，因为 v10-实践证明 5 轮不够）
- **SFS_quick ≥ 90 且所有单项 ≥ 80**（程序化验证达到 90% 目标）
- 对比报告显示**所有量化维度差距 < 10%**（已经非常接近原作者）

### 阶段 5 完成标记（plan-step 6）

```bash
# 复刻循环退出后（收敛或保底）必须打点
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 6 --skip-output
# 循环过程只是反复执行阶段 2-4，没有"新增"独立文件，用 --skip-output
```

---

## 阶段 6：出货（原"第四步：输出蒸馏报告"升级版）

```
🧬 闭环蒸馏完成！

📖 参考：[小说名] · 全{N}章 · 约{M}万字
🎨 风格摘要：[一句话描述]
🔁 闭环迭代：{X} 轮（v0 → v{X}）

收敛状态：
  · 最后一轮 SFS_quick 分数：{score}/100（v15 新增 · 程序化评分）
  · 最后一轮 flagged 维度数：{N}
  · 关键收敛点：[列出最后 1-2 轮的关键修正]

⚠️ 出货前必须执行（v15 新增）：
  1. 同步 skill.md — 确保 skill.md 版本号与 JSON version.current 一致
  2. 最终校验 — 对最后一轮复刻样本跑 `python core/scripts/validate_style.py --strict`
  3. 基线锁定 — 对原文代表章节跑 `python core/scripts/style_analyzer.py` 生成参考基线

终版风格档案：
  · 项目内：_数据库/作者风格.json
  · 全局风格库：风格库/[小说名].json + [小说名]_skill.md
  · 蒸馏迭代历史：风格库/distillation_log_[小说名].md
  · 复刻测试样本：风格库/复刻测试/v0~v{X}/
  · 对比报告：风格库/对比报告/distillation_compare_v0~v{X}.json
  · 经验沉淀：项目/_数据库/lessons_learned.json (新增 L 条目)

核心能力（已通过 {X} 轮闭环验证）：
  · 章节开头多样化（10 种类型分布 · 连续重复率 {X}%）
  · 章节结尾多样化（5+ 种类型分布 · 连续重复率 {X}%）
  · 衔接节奏多样化（≥4 种）
  · 对话占比 ≥{X}%
  · 主角情绪爆发独白能力
  · 群戏密度 {X} 人/章
  · 方括号设定名词密度 {X}/章
  · 拟声词密度 均值{X}/章（{Y}%章节为0 · 不滥用不矫枉过正）
  · 配额词使用（不冷过头，不滥用）
  · 主角家庭/关系网情感锚点
  · 设定密集投放章组合能力
  · 超自然事件外部观察者投影
  · 抒情段中长段叙事流

跨章多样性指标（v16 新增）：
  · 开头连续重复率：{X}%（原作实测 {Y}%）
  · 结尾连续重复率：{X}%
  · 高危环境元素：{列表}（出现频率>30%的元素，AI写作时需轮换）
  · 感官分布模式：{集中爆发型/均匀分布型}
  · 拟声词零值章比例：{X}%（用于校准 AI 的拟声词使用频率）
  · 主力过渡方式：{方式}（非拟声词的，如省略号独立段）

现在可以开始写作了！未来写章时，每写完一章可重新跑阶段 3 局部扫描，发现新差距随时升级。
```

---

## 阶段 6.5：经验沉淀

**阶段 6 出货完成后自动触发**，从本次蒸馏的 distillation_log + lessons_learned 中提取跨项目通用教训，追加到全局经验库。

### 触发方式

主代理在阶段 6 完成后自动 spawn lessons-extractor agent（不需要用户手动调用）。

```python
Agent({
  description: "提取蒸馏教训到经验库",
  prompt: <见 <REPO_ROOT>/core/claude-home/lessons/EXTRACTOR_PROMPT.md>,
  subagent_type: "general-purpose",
  run_in_background: true
})
```

### Extractor 的职责

1. 读取**现有经验库**（去重基础）：`<REPO_ROOT>/core/claude-home/lessons/distill-style-lessons.md`
2. 扫描本次蒸馏的 `distillation_log.md` 和所有 `lessons_learned_*.md`
3. 提取**跨项目通用**教训（排除项目特定信息：角色名 / 术语 / 数值）
4. 按四要素格式追加：现象 → 影响 → 修复 → 预防
5. 严格去重：相同根因不重复，仅补充细节

### 防失控规则

- Extractor 仅能 Edit 经验库，不能 Write 新文件覆盖
- 没有 git commit 权限（已经在阶段 6 commit）
- Extractor 失败**不阻塞**蒸馏主流程（视为非关键任务，仅记录日志）

### 输出

- 更新 `distill-style-lessons.md`：新增 N 条 / 补充 M 条
- 终端报告：「✅ Lessons 自学习提取完成 → 新增 N / 补充 M / 跳过 K」

### 与阶段 0 的闭环

阶段 0「读经验库」+ 阶段 6.5「自学习追加」形成闭环——
- 每次蒸馏前先读历史教训避坑
- 每次蒸馏后把新发现沉淀给后续项目

这是 `/distill-style` 命令的"自学习"能力。

---

## 阶段 6.7：维度自演化（dimension_evolver）

### 为什么

阶段 6.5「lessons 沉淀」只把教训写到 MD 文档（人工读取），不能让**下次蒸馏的 prompt 自动加新维度**。

蒸馏 agent 在 F 段（已有）+ G 段**早就在主动提议新维度**，但没有跨章统筹机制：
- 实测BookC前 50 章中 **32 章（64%）F 段含「建议新增…」「建议建立…」「建议区分…」**
- 这些提议自生自灭，没被吸收

阶段 6.7 是**自我升级闭环**——蒸馏完成后扫所有提议 → 聚合统筹 → 通过双门槛 → 升级 `auto_evolved_dimensions.json` → 下次蒸馏 prompt 自动加新维度（B7 段）。

### 业界依据（Round 1 调研 · 34 来源）

详见 `.research_cache/inspiration_self_evolving_distill_2026-05-24.md`：

- **LLM-based feature generation (arxiv 2409.07132)** — 两种 workflow（A 全自动 / B 半自动），对应我们 F→G→evolver 路线
- **Voyager skill library (arxiv 2305.16291)** — 渐进 skill 添加 + 跨样本验证范式
- **EvolveR (arxiv 2510.16079)** — Offline Self-Distillation + Online Interaction 闭环
- **GEPA (ICLR 2026)** — Pareto frontier 多候选并存（保留多 schema 版本不单线进化）
- **constraint self-bypass 警告** — agent 知道 prompt 规则会绕，**必须架构层防御**

业界共识范式：**bottom-up discovery（自由提议）+ top-down stabilization（严控升级）双阶段**。

### 触发时机

阶段 6 出货完成后自动触发，与阶段 6.5 并行（**不阻塞**主流程）：

```bash
# Step A：单项目扫描（出本书候选维度）
python core/scripts/dimension_evolver.py --project "<workspace/styles/<书名>>" --scan
# → 输出 .dimension_evolution/candidates_<ts>.json

# Step B：跨项目扫描（出 universal 候选）
python core/scripts/dimension_evolver.py --all-projects --scan
# → 输出 core/claude-home/universal_candidates_<ts>.json
```

### 候选 → promote 流程

**1) 自动模式（推荐 universal 候选，跨 ≥ 2 项目验证）**：

```bash
python core/scripts/dimension_evolver.py --all-projects --promote-universal
# 一次最多升 3 维度（防 schema 膨胀），写入 auto_evolved_dimensions.json
```

**2) 半自动模式（单项目候选，需主代理审）**：

主代理读 candidates JSON → 选定 `cand_NNN` → 调：
```bash
python core/scripts/dimension_evolver.py --project "<path>" --promote cand_001
```

### 防失控（防 SE7 风险）

借鉴 Round 1 调研：constraint self-bypass + Voyager + Schema Registry backward compatibility：

| 防御层 | 机制 |
|---|---|
| 一次上限 | `DEFAULT_MAX_PROMOTE_PER_RUN = 3`（单次最多 3 维度入池） |
| 跨样本验证 | universal 升级需 **≥ 2 个项目** 都验证（防单书噪声） |
| 双门槛 | candidate 出炉需 **≥ 2 章** 提议 + **≥ 20% valid** ratio |
| 版本化 | `auto_evolved_dimensions.json.dimensions[].version` 字段 |
| 重名拒绝 | 已注册的 dim_name 拒绝重复升 |
| Git 历史 | 注册表每次更新自动 commit（与现有架构对齐） |
| 旁路防御 | 升级写入 JSON 文件（不直接改 prompt）+ prompt 读取时是「池」（agent 不能改池只能用） |
| Regression test（未来） | 升级后用已蒸馏样本回归验证（参考 Confluent Schema Registry backward compatibility） |

### 自我升级闭环（与阶段 0 闭合）

阶段 0「读经验库」+ 阶段 6.5「lessons 沉淀」+ **阶段 6.7「维度池升级」** = **三段式自学习闭环**：

```
[蒸馏 N] agent 在 F/G 段提议 N 个新维度
   ↓ 阶段 6.7
dimension_evolver 聚合 → 通过双门槛 → auto_evolved_dimensions.json 加 K 个
   ↓ 阶段 0 (下次)
[蒸馏 N+1] prompt 自动注入 auto_evolved_dimensions（B7 段） → agent 蒸馏新维度
   ↓ 阶段 6.7 (下次)
继续提议 → 继续升级 → schema 自我进化
```

### 主代理执行

阶段 6 完成（plan-step 7）后：
1. spawn lessons-extractor agent（阶段 6.5，已有）
2. 调 `dimension_evolver.py --project <path> --scan`（阶段 6.7）
3. 若产出 candidates ≥ 1：主代理 Read candidates JSON，按 high_value 排序展示给用户
4. 用户/主代理选择 cand_id → 调 `--promote <cand_id>`
5. 终端报告：「✅ 维度自演化：扫到 N 候选，升级 M 入注册表」

### 与既有 SE1-SE5 的关系

| 已有 SE | 维度自演化的区别 |
|---|---|
| SE1 skill_evolver | 写**作经验**侧（写完章节后从 judge 提取 pattern）·  本机制是**蒸馏**侧（蒸馏完后从 agent F/G 段提取维度） |
| SE2 ERL Heuristics | manifest 检索注入 · 本机制升级 distill prompt schema 本身 |
| SE3 meta-prompt-optimizer | 改 writer/outline-planner prompt · 本机制升级 distill prompt 字段池 |
| SE4 三角共演化 | Proposer/Solver/Judge · 本机制 distill agent → evolver → promote |
| SE5 universal_skill_pool | 跨项目 pattern 池 · 本机制 universal_distill_dimensions 跨项目维度池 |

四者**互补不冲突**——SE1-SE5 优化「写得更好」，本机制优化「蒸馏得更准」。

---

## 阶段 7：Git 自动提交

蒸馏完成后，将风格档案纳入 Git 版本管理（仅当项目已初始化 Git）：

```bash
if command -v git >/dev/null 2>&1 && [ -d "小说_书名/.git" ]; then
  git -C "小说_书名" add _数据库/作者风格.json
  # 读取 version.current 和 analyzed_chapters 填入 commit message
  VERSION="v3"       # 从 作者风格.json 的 version.current 读取
  CHAPTERS="2033"    # 从 analyzed_chapters 字段读取
  git -C "小说_书名" commit -m "feat: 蒸馏作者风格 ${VERSION}（${CHAPTERS} 章）" 2>&1 | tail -1
fi
```

- 初次蒸馏：`feat: 蒸馏作者风格 v1（200 章）`
- 增量蒸馏：`feat: 蒸馏作者风格 v3（2033 章）`
- 全新覆盖蒸馏：`feat: 重新蒸馏作者风格 v1（[小说名]）`
- **闭环迭代**：`feat: 闭环蒸馏 v3→v4（差距收敛于 N 维度）`

注意：全局风格库（`风格库/[小说名].json`）在项目目录外，不纳入项目 Git 管理。

---

## 阶段 8：Plan 闭环验证（强制兜底）

阶段 6 出货 + 阶段 7 Git commit 落地后，必须执行最后三步（回灌门槛 → step 7 标记 → plan-end）：

### ① 写作端回灌严闭环（Article 6 · 不通过不出货）

```bash
python core/scripts/distill_finalize_verify.py \
  --project "workspace/styles/<书名>" \
  --skill "workspace/styles/<书名>/skill_FINAL.md" \
  --cluster-id cluster_001 \
  --output "workspace/styles/<书名>/对比报告/writer_feedback_verify.json" \
  --strict
# exit 2（verdict != PASS）→ 禁止落 step 7，回头修 skill 重新蒸馏；exit 0 才继续
```

### ② 阶段 6 出货完成标记（plan-step 7 · 回灌 exit 0 后才落）

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 7 \
  --output "workspace/styles/<书名>/作者风格_FINAL.json"
# 也可追加校验 skill_FINAL.md / distillation_log.md / writer_feedback_verify.json
```

### plan-end 终极闸门

```bash
python core/scripts/plan_tracker.py end "$PLAN_ID"
RC=$?
if [ $RC -eq 0 ]; then
  echo "✅ 蒸馏闭环验证通过，允许声称完成"
else
  echo "❌ exit 2 → 有 required 步骤未完成，禁止声称蒸馏完成！"
  python core/scripts/plan_tracker.py status "$PLAN_ID"
  # 必须回头补未完成阶段，禁止跳过
fi
```

**如果 exit 2**：
- 不要硬声称完成，老老实实把 status 输出贴给用户
- 根据 `missing_steps` 列表回头补对应阶段
- 重新调 `plan-end` 直到 exit 0

---

## 📋 完成检查清单（Plan 强制版）

阶段 6 出货前必须逐项确认（缺一不可）：

- [ ] `plan_tracker.py status $PLAN_ID` 显示阶段 1/2/3/4/5/6/7 全部 `[x] completed`
- [ ] SFS 多基线均值 ≥ 88（A 级）OR 在 distillation_log 中写明未达 A 级的原因
- [ ] `作者风格_FINAL.json` / `skill_FINAL.md` / `lessons_learned_FINAL.md` / `distillation_log.md` 四件套齐全（绝对路径在 `workspace/styles/<书名>/`）
- [ ] Git commit 已落地（`git log --oneline -1` 能看到本次蒸馏 commit）
- [ ] `plan_tracker.py end $PLAN_ID` → exit 0
- [ ] 经验沉淀 lessons-extractor 已 spawn（阶段 6.5，可后台跑）

**全部打勾才允许向用户报"蒸馏完成"。任何一项未完成 = 红线一（闭环意识）违规，绩效 3.25。**

---

## 使用场景

### 基础用法
- `/distill-style https://xxx.com/book/123` — 从链接闭环蒸馏（自动跑完阶段 1-6）
- `/distill-style D:\小说\斗破苍穹.txt` — 从本地文件闭环蒸馏
- `/distill-style` 然后粘贴内容 — 从粘贴文本闭环蒸馏

### v14 闭环模式参数（可选）
- `/distill-style D:\小说\xxx.txt --quick` — **快速模式**：跳过阶段 5 循环，只跑 1 轮闭环（适合先看初版效果）
- `/distill-style D:\小说\xxx.txt --max-rounds=5` — 限制闭环最多 5 轮（默认 3 轮）
- `/distill-style D:\小说\xxx.txt --resume` — **续蒸馏**：从上次 distillation_log.md 末尾继续闭环
- `/distill-style --refine 风格库/[小说名].json` — **精化模式**：跳过阶段 1，直接从阶段 2 复刻测试开始（已有 skill 但想强化）

### 写作中触发的微型闭环
- 写到中途发现 skill 不够用，用 `/distill-style --micro-refine [小说名] --against [当前章节文件]` 跑单轮微型闭环
- 这会用当前章节作为复刻对照基线，发现差距后局部更新 skill

### 关键文件
- `风格库/[小说名].json` — 终版风格档案
- `风格库/[小说名]_skill.md` — 终版 Skill（agent 加载用）
- `风格库/复刻测试/v0~vN/` — 历史复刻样本
- `风格库/对比报告/distillation_compare_v0~vN.json` — 历史对比数据
- `风格库/distillation_log_[小说名].md` — 蒸馏迭代历史（人类可读）
- `项目/_数据库/lessons_learned.json` — 经验沉淀（项目级，跨章节用）

---

## ⚠️ 关键差异：v14 闭环 vs 旧版单次蒸馏

| 维度 | v10 单次蒸馏 | v14 闭环蒸馏 | v16 跨章多样性 |
|------|-------------|-------------|--------------|
| 流程长度 | 1 步 | 6 阶段 | 6 阶段 + 跨章聚合 |
| 分析维度 | 15 维度 | 20+ 维度 | **32 维度**（+6 跨章 B2 + 5 跨章对比 E） |
| 复刻测试 | 无 | 3 段 | **4 段**（+连续3章开头多样性测试） |
| 跨章分析 | ❌ | ❌ | ✅ 每 30 章自动聚合跨章数据 + 衔接模板 |
| Skill 跨章节 | ❌ | ❌ | ✅ 自动生成"跨章多样性约束"节 |
| 开头重复检测 | ❌ | ❌ | ✅ 连续重复率实测 |
| 拟声词校准 | 只有下限 | 只有下限 | ✅ 上限+零值率+分布模式 |
| 感官模式 | 单章分布 | 单章分布 | ✅ 集中爆发型/均匀型自动识别 |
| 环境锚点 | ❌ | ❌ | ✅ 高危重复元素自动标记 |
| 验证器 | 无 | validate_style 12项 | validate_style **含拟声上限** |

**v16 解决的核心问题：之前的蒸馏只看单章，写出来连续5章开头都是"走廊灯闪"。现在自动检测并约束跨章多样性。**

---

**目标：让 AI 写出来的小说，读起来像是参考作者写的——不是模仿，而是内化其风格DNA**

**实现路径：单次蒸馏永远做不到。只有闭环迭代 + 复刻对比 + 修正反思 才能真正逼近作者能力。**
