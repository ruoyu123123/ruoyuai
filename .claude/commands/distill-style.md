---
description: 当用户提供参考小说链接/文件/路径，想把该作者的写作风格作为后续写作基线时使用；产出可复用的风格 SKILL.md 注入 writer agent
allowed-tools: [WebFetch, WebSearch, Bash, Read, Write, Edit, Grep, Glob]
---

你是一位写作风格分析专家。请从以下参考小说中**闭环蒸馏**出作者的完整写作风格：

$ARGUMENTS

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
      "_doc": "agent 对现有维度未覆盖现象的结构化提议，由 SkillOpt 训练循环消费",
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
| **distill_rubric.py** | `core/scripts/distill_rubric.py` | A12 LongBench-Write 六维质量 rubric（Relevance/Accuracy/Coherence/Clarity/Breadth&Depth/ReadingExperience 各 1-5 · 聚合 (mean-1)×25 归一 0-100 · **长度剥离**：judge 明示不考虑字数达标） | 阶段 2/5 复刻时经 `distill_replicate.py` 自动触发（env `DISTILL_RUBRIC_MODE=on` 才跑 · 默认 off）· 结果写 replica `.meta.json["rubric_sixdim"]` 与 SFS 并列 · **advisory 旁证观测，不改任何闸门判据（SFS 仍是唯一出货闸）**；judge JSON 缺任一维 → 整体作废重试 ≤3 次，全失败诚实记 `rubric_unavailable` 不伪造分 |

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
[阶段 2] 复刻中检（v29 同栈：先 spawn Claude agent 按 skill 写复刻场景稿 → distill_replicate.py --mode cluster --claude-scenes-dir <scenes> 做 gemini 分段润色落盘）
   ↓ 输出：cluster 抽样复刻样本（中检 · 阶段 5 做完整终验）
[阶段 3] 多维度对比扫描 —— 抽 2-3 章原文 + style_evaluator SFS 评分
   ↓ 输出：差距报告
[阶段 4] 修正反思 —— 差距维度生成新约束 → skill v1
   ↓ 输出：skill v1 + lessons_learned
[阶段 5] cluster 终验复刻（主推 · v29 同栈）—— 先 spawn Claude agent 写复刻场景稿，再 distill_replicate.py --mode cluster --claude-scenes-dir <scenes> 润色复刻 1-2 个完整故事块
   ↓ 输出：cluster 复刻样本（4000-20000 字 · 按 Claude 场景稿分段润色 · 段级守恒带 [0.85,1.30]）
   ↓ 旁证（可选）：env DISTILL_RUBRIC_MODE=on 时 meta.json 附 LongBench-Write 六维 rubric（advisory · 长度剥离 · 不进判据）
   ↓ 终止条件：连续 2 轮无新差距 + cluster SFS ≥ 80（SFS 是唯一出货闸 · 六维 rubric 不参与）
[阶段 6] 出货 —— _FINAL 四件套 + git commit
   ↓ 输出：作者风格_FINAL.json + skill_FINAL.md + distillation_log.md
[阶段 7] 写作端回灌严闭环（v2 新增 · 严 · Article 6 · v29 同栈）—— 先 spawn Claude agent 按 skill_FINAL 写复刻场景稿，distill_finalize_verify.py 内部调 distill_replicate.py --claude-scenes-dir 润色复刻同 cluster → arc/SFS 对比
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
| 阶段 0 | 读经验库 / 预处理（**必读 cluster_index.json**）| `--n 1` | `workspace/styles/<书名>/.plan_markers/stage0_preflight.json` |
| 阶段 1 | 表层蒸馏（cluster agent + cluster 衔接 + arc 聚合 + skill v0）| `--n 2` | `workspace/styles/<书名>/作者风格.json` |
| 阶段 2 | 复刻中检（v29 同栈：Claude 场景稿 + `distill_replicate.py --mode cluster --claude-scenes-dir`）| `--n 3` | `复刻测试/.../cluster_<id>_replica.txt` |
| 阶段 3 | 多维度对比扫描 + SFS 评分（chapter SFS / cluster mode 6 维）| `--n 4` | `对比报告/distillation_compare_v{N}.json` |
| 阶段 4 | 修正反思 → skill v{N+1} | `--n 5` | `workspace/styles/<书名>/.plan_markers/stage4_reflection.json` + `skill_v{N+1}.md` |
| 阶段 5 | cluster 终验复刻（v29 同栈：Claude 场景稿 + `distill_replicate.py --mode cluster --claude-scenes-dir`）| `--n 6` | `复刻测试/.../cluster_<id>_replica.txt` |
| 阶段 6 | 出货（_FINAL 四件套 + git commit）·**出货前必先过阶段 7 回灌门槛** | `--n 7` | `作者风格_FINAL.json` + `skill_FINAL.md` + `distillation_log.md` |
| 阶段 7 | 写作端回灌严闭环（v29 同栈：Claude 场景稿 + `distill_finalize_verify.py --claude-scenes-dir --strict`）·**并入 step 7 出货门槛，不单独占 plan step**（plan 仅 7 步） | 含于 `--n 7`（回灌 exit 0 才落 step 7） | `对比报告/writer_feedback_verify.json` |

每阶段尾必须执行：
```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n <阶段号> --output <required_output>
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
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 \
  --output "workspace/styles/<书名>/.plan_markers/stage0_preflight.json"
# 阶段 0 必须写入 marker，记录 lessons 已读、cluster_index 状态和批次范围；不允许跳过文件校验
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
6. **skill v0 聚合后跑 `consolidate_author_profile.py`（照顾弱模型·确定性规整 consumer 字段·必跑）**：
   `python core/scripts/consolidate_author_profile.py --project workspace/styles/<书名>`
   从单章 metrics/dim **确定性聚合** quantitative + narrative_craft/fingerprint + cross_chapter_diversity 的 consumer 标准 schema（含别名键 mean/intra_chapter_std_mean/single_sentence_para_ratio_mean/chapter_words 等）。**不靠综合 agent 自由写 schema**——agent 只产创意（golden/core_style_signature/风格标签），数值字段由脚本保证标准，build_manifest(D1/D3/D5)/validate_style(段长band)/skill_contract_table 一定能读。**根因**：agent 自由 schema 与 consumer 期望键不符 = 契约债，强模型尚且乱、弱模型必崩 → 数值确定性化。
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

**为什么需要**：F 段是自由文本难聚合，G 段是结构化 → SkillOpt 训练循环消费（每条提议作为 optimizer context 输入，由 `/distill-style-skillopt` 训练闭环升级 skill）。

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


---

## 后续阶段概述（详见脚本 · 此处不展开）

| 阶段 | 脚本 | 产出 |
|---|---|---|
| 1.5 arc聚合 | `cluster_segmenter.py` + `arc_aggregator.py` | `cluster_index.json` / `arc_templates/` |
| 2 cluster复刻中检 | v29 同栈（Claude 场景稿 + `distill_replicate.py --claude-scenes-dir`） | `复刻测试/vN_roundM/` |
| 3 SFS对比 | `distill_replicate.py` + `style_evaluator.py --multi-ref-from-dir` | SFS score + 对比报告 |
| 4 修正 | 主代理 Edit skill | `skill_vN+1.md` |
| 5 cluster终验 | v29 同栈（Claude 场景稿 + `distill_replicate.py --claude-scenes-dir`） | `cluster_<id>_replica.txt` |
| 6 出货+回灌 | `distill_finalize_verify.py --strict` | `作者风格_FINAL.json` + `skill_FINAL.md` |
| 6.5 教训沉淀 | 主代理直接总结沉淀到 `distill-style-lessons.md` | 更新经验库 |

**详细维度说明**已程序化到 `core/scripts/style_profile_extractor.py` / `consolidate_author_profile.py`。
---
