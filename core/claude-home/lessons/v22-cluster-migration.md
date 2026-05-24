# v22.cluster 蒸馏迁移指南

> **状态**：MANDATORY · 适用于所有 `/distill-style` 新蒸馏
> **创建**：2026-05-24（BookA重蒸 Step 1 翻车后强制写入）
> **v1.1 修订**：2026-05-24（二次翻车后补「两种场景」caveat）
> **触发场景**：主代理跑 `/distill-style` 时 Step 1 必读本文件
> **路径权威**：`core/claude-home/lessons/v22-cluster-migration.md`

---

## 🚨 0 · 关键 caveat：cluster_index 有两种产生场景（v1.1 必读）

**`cluster_segmenter.py` 的设计意图是 retroactive（事后回溯切分）工具**——脚本顶部 docstring 自述「**已蒸馏书的 retroactive cluster 切分**」，依赖前一轮蒸馏产出的 `衔接分析/*.json` 的 `connection_type` 字段识别 strong 边界。

### 0.1 两种场景对照

| 场景 | 触发 | cluster_index 来源 | strong 边界数 | 质量 |
|---|---|---|---|---|
| **场景 A · 复用** | 重蒸已蒸馏书 | 上轮蒸馏完成时跑的 segmenter（含 continuity 数据）| **多**（典型 5-15） | v22.cluster.2 等级 |
| **场景 B · 裸切** | 新书首次蒸馏 / 全清后重蒸 | 蒸馏开始前裸跑 segmenter（无 continuity 数据）| **0**（全 max_chapters）| 首轮自然态 |

### 0.2 场景 B 不是"质量缺陷"，是"必经阶段"

新蒸馏的 cluster_index 首轮**必然**无 strong 边界——没有 continuity 数据，segmenter 物理上无法识别语义边界。这不是 bug，是设计。正确流程：

```
新蒸馏起点
  ↓
裸跑 segmenter → 首轮 cluster_index（max_chapters 主导，6 章固定窗口）  ← 场景 B
  ↓
按首轮 cluster_index 跑表层蒸馏（spawn cluster agent × N）
  ↓
表层蒸馏完成（衔接分析/ 全部 continuity JSON 就绪）
  ↓
重跑 segmenter → 最终 cluster_index（含 strong 边界）  ← 升级到 v22.cluster.2 等级
  ↓
arc_aggregator --all-clusters（基于最终 cluster_index 聚合 arc）
```

### 0.3 翻车防御（v1.1 新增）

❌ 错的反应：「裸切出 34 cluster 无 strong 边界 → 这是降级 → 必须找回原 cluster_index 或换方案」
✅ 对的反应：「场景 B 自然态 · 6 章固定窗口 ≈ ECAS schema 推荐 5.41 章/cluster · 直接开干 · 蒸完后重跑 segmenter retro-refine」

### 0.4 二次翻车实例（2026-05-24 · BookA v6 全清重做）

- 用户全清BookA所有蒸馏产物（含 v22.cluster.2 的 cluster_index）
- 主代理重跑 segmenter，得到 34 cluster · strong 0 · max_chapters 33
- 主代理误判这是"降级 cluster_index 质量缺陷"，停下来报警
- 实际上是场景 B 的自然首轮态——本指南 v1.0 没区分两种场景导致误判
- 修复（v1.1）：明确区分两种场景 + 首轮无 strong 边界是必经阶段

---

## 一、为什么必须迁移（背景）

### 1.1 v17 → v22.cluster 演进史

| 版本 | 颗粒度 | 上线 | 状态 |
|---|---|---|---|
| v16 及前 | 1 章 / agent | 2026-05 前 | DEPRECATED |
| **v17** | **固定 3 章窗口** + 末组合并 | 2026-05-13 | **DEPRECATED v22.cluster** |
| **v22.cluster** | **故事块自适应 3-6 章 / 4000-20000 字** | 2026-05-19 | **当前主轨** |

### 1.2 v17 三章固定窗口的问题（推动 v22.cluster 重构的根因）

- **两端颗粒度错位**：写作端 ECAS 早已 cluster 模式（`gen_writer.py --cluster N`），蒸馏端卡在 3 章固定 → 蒸馏的"3 章衔接模板"对 cluster 写作没用，浪费 30%+ 蒸馏维度
- **章节边界刻意化**：固定 N 章导致 cluster 内部强行拆分 / 跨 cluster 边界硬合并，违反情节单元的自然终结
- **业界已实证落后**：LumberChunker (EMNLP 2024, arXiv 2406.17526) variable-length 比 fixed-N **+7.37% DCG@20**

### 1.3 业界依据（v22.cluster 引入时的调研锚点）

| SOTA | 颗粒度 | 关键依据 |
|---|---|---|
| LumberChunker (EMNLP 2024) | LLM 检测语义边界的 variable-length chunk | 比 fixed-N **+7.37% DCG@20** |
| MARCUS (arXiv 2510.18201, 2025) | event-centric 跨整本书 | actor/experiencer 双视角时间序列 |
| Multi-Agent TV Arcs (arXiv 2503.04817, 2025) | arc 跨任意 episode，按情节单元自然终结 | "avoiding artificial segmentation" |
| Three Stage Narrative (arXiv 2511.11857, 2025) | sliding window + Ward 聚类 6 弧形 | 后置分析，不切分 |

---

## 二、规则对照表

| 项 | v17 老规则（弃） | v22.cluster 新规则 |
|---|---|---|
| **颗粒度** | 固定 3 章/窗口 | **自适应 3-6 章 / 4000-20000 字** |
| **切分器** | 章号取模 `step 3` | **`cluster_segmenter.py`**（语义边界 + max_chapters 硬上限） |
| **Agent 数（200 章估）** | ~67 | **~37**（节省 45%） |
| **单 agent 任务** | 读 3 章 + 1 衔接 | 读 N 章 + 1 cluster 衔接（N=3-6） |
| **末组合并** | L2.6 手动并入前窗口 | **由 segmenter 自动处理**（max_chapters / end_of_book 边界） |
| **衔接 JSON 路径** | `衔接分析/ch{N}_{N+2}_continuity.json` | `衔接分析/cluster_<id>_continuity.json` |
| **arc 聚合** | ❌（v17 无） | `arc_aggregator.py` 主轨 cluster + 副轨 fixed10 |
| **角色情感弧** | ❌ | `character_arc_aggregator.py` (MARCUS 范式) |
| **plan_tracker 契约字段** | `PLAN_ID` + `STEP` | `PLAN_ID` + `STEP` + **`CLUSTER_ID`** + **`CHAPTER_RANGE`** |
| **调度循环** | `FOR batch_start = 1 to N step 3` | **`FOR cluster in cluster_index.clusters`** |
| **聚合频率** | 每 30 章一次 | 每 ~10 cluster 一次（增量 arc_aggregator）|

---

## 三、主代理 Step 1 SOP（防翻车流程）

### 3.1 第一动作：检查 cluster_index.json（v1.1 修订 · 区分两种场景）

```bash
# 路径
INDEX="workspace/styles/<书名>/cluster_index.json"

# 检查
if [ -f "$INDEX" ]; then
  echo "✅ 场景 A（复用）：cluster_index.json 已存在 → 直接复用"
  # Read 它，验证 schema_version 是 v22.cluster.* 系列
  # 期待该索引含 strong 边界（来自上一轮蒸馏产出 continuity 数据）
else
  echo "⚠️ 场景 B（裸切）：不存在 → 跑 cluster_segmenter 产首轮索引"
  python core/scripts/cluster_segmenter.py --project workspace/styles/<书名>
  # 首轮 cluster_index 必然 strong=0，全 max_chapters 主导 — 这是设计，不是 bug
  # 蒸馏完表层后必须重跑 segmenter 做 retro-refine（见 §3.4）
fi
```

### 3.4 蒸完后 retro-refine（v1.1 新增 · 场景 B 必做）

如果首轮 cluster_index 是裸切（strong=0），表层蒸馏完成后**必须**重跑 segmenter 升级索引：

```bash
# 表层蒸馏全部完成（衔接分析/ 已有全部 continuity JSON）后：
python core/scripts/cluster_segmenter.py --project workspace/styles/<书名>
# 此时 segmenter 能读到 continuity，自动产 strong 边界
# 输出的 cluster_index 升级到 v22.cluster.2 等级
# 然后跑 arc_aggregator --all-clusters 基于最终 cluster_index 聚合 arc
```

⚠️ 不做 retro-refine 的代价：arc_aggregator 基于裸切 cluster 聚合，无法识别 POV 切换 / 时间跳跃等语义边界 → 写作端 cluster 模式使用蒸馏产物时颗粒度仍然错位 = v22.cluster 重构白做。

### 3.2 第二动作：解析 clusters 列表

cluster_index.json 关键字段：

```json
{
  "schema_version": "v22.cluster.2",
  "work": "<书名>",
  "total_chapters": 200,
  "total_clusters": 37,
  "average_chapters_per_cluster": 5.41,
  "average_words_per_cluster": 16216,
  "boundary_reason_distribution": {"strong": 10, "max_chapters": 26, "end_of_book": 1},
  "clusters": [
    {
      "cluster_id": "auto_001",
      "chapter_range": [1, 4],
      "chapters_count": 4,
      "estimated_words": 12000,
      "boundary_reason": "strong:悬念承接新视角",
      "scenes_estimated": 5
    },
    ...
  ]
}
```

### 3.3 第三动作：按 cluster 调度 agent

```python
for cluster in cluster_index["clusters"]:
    a, b = cluster["chapter_range"]
    cluster_id = cluster["cluster_id"]
    spawn_Agent(
        description=f"蒸馏 {cluster_id} (ch{a}-{b})",
        prompt=f"""你是写作风格分析专家。

PLAN_ID: {plan_id}
STEP: 2
CLUSTER_ID: {cluster_id}
CHAPTER_RANGE: ch{a}-ch{b}

任务：蒸馏该 cluster 的 {b-a+1} 章。
- Read ch{a}.txt ... ch{b}.txt
- 独立跑 N 次 style_analyzer.py
- 输出 N 个单章 JSON 到 蒸馏进度/
- 输出 1 个 cluster 衔接 JSON 到 衔接分析/cluster_{cluster_id}_continuity.json

cluster 边界原因：{cluster["boundary_reason"]}
"""
    )
# 并行配额 ≤20/波（L1.4）
```

### 3.4 第四动作：增量 arc 聚合

每完成约 10 cluster，主代理（不是 agent）调：

```bash
python core/scripts/arc_aggregator.py \
  --project workspace/styles/<书名> \
  --all-clusters    # 增量聚合所有已蒸馏 cluster
```

### 3.5 第五动作：character arc 聚合（全部 cluster 完成后）

```bash
python core/scripts/character_arc_aggregator.py \
  --project workspace/styles/<书名> \
  --min-appearances 5
```

---

## 四、双轨产物清单（v22.cluster 完整）

```
workspace/styles/<书名>/
├── cluster_index.json                              # cluster_segmenter 产出
├── 蒸馏进度/
│   └── ch{N}.json × 总章数                         # 单章 JSON（颗粒度严格保留）
├── 衔接分析/
│   └── cluster_<id>_continuity.json × cluster数    # cluster 内章际衔接
├── arc_templates/
│   ├── cluster_arc_<id>.json × cluster数           # ★ 主轨：情节单元 arc
│   ├── arc_<NNN>.json × ⌈总章数/10⌉                # 副轨：每 10 章一段（兼容）
│   └── arc_summary.json                            # 全书 arc 形状统计
├── character_arcs/
│   └── <角色名>_emotion_arc.json × 主要角色数      # MARCUS 范式 actor/experiencer 双视角
├── 作者风格.json                                    # 35+ 维度蒸馏风格库
├── skill.md                                        # 写作 agent 注入用
└── ...
```

---

## 五、Step 1 必读检查清单（主代理用）

启动 `/distill-style` 时主代理必须确认以下都为 ✅，否则**禁止**进入 Step 2：

- [ ] 已 Read 本文件（`v22-cluster-migration.md`）
- [ ] 已 Read `workspace/styles/<书名>/cluster_index.json`（或确认其不存在 → 已跑 cluster_segmenter）
- [ ] 已确认 cluster_index.json 的 `schema_version` 在 `v22.cluster.*` 系列
- [ ] 已规划 agent 调度循环为 `FOR cluster in cluster_index.clusters`
- [ ] **未**写 `FOR batch_start = 1 to N step 3` 之类 v17 老调度模板
- [ ] Agent prompt 模板含 `PLAN_ID` + `STEP` + `CLUSTER_ID` + `CHAPTER_RANGE` 四字段
- [ ] 已规划 arc_aggregator 增量聚合时机（每 ~10 cluster）

---

## 六、翻车实例 + 经验沉淀

### 6.1 2026-05-24 · BookA重蒸 Step 1 盲套 v4.3 三章窗口

**现象**：
- 用户说"重新蒸馏BookA Ch1-200"
- 主代理 Step 1 没读 cluster_index.json，按 v4.3 历史经验设计了 66 个三章窗口
- 用户指出"系统已改 cluster"才矫正

**根因**：
1. lessons 元数据起始版本声明仍写 v17 → 主代理"按 lessons 老规则做事"
2. distill-style-lessons.md 完全没提 cluster
3. L2.6/L4.5/L4.6 全是 3 章窗口语境
4. commands/distill-style.md v17 章节标题在前，v22.cluster 阶段 1.5 在后 → 主代理顺序读时被前面误导

**修复（2026-05-24）**：
1. lessons 元数据加「当前主轨：v22.cluster」标注
2. L2.6/L4.5/L4.6 标 `[DEPRECATED v22.cluster]`
3. 新增 L2.8 cluster 故事块自适应切分（红线）
4. commands/distill-style.md 顶部加红框警示 + Step 1 必做项
5. commands v17 段落全部标 DEPRECATED + 新增 v22.cluster 主轨流程
6. 新建本文件（`v22-cluster-migration.md`）作为强制迁移指南
7. plan.json `description` 字段更新

**预防（系统级）**：
- 任何"方向性决策" Step 1 第一动作 = Grep `cluster_index|v22\.cluster` 验证当前规则版本
- 不能仅凭"以前蒸馏过这本书的经验" 跳过规则版本验证
- 老规则 `[DEPRECATED]` 标记是必备防御——不只是注释，是 hook / lessons-extractor 可以扫描的标记

---

## 七、与其他 lessons 的关系

| Lesson | 关系 |
|---|---|
| L2.6 末组合并规则 | DEPRECATED · segmenter 自动处理 |
| L2.7 单批 ≤300 章 | **仍有效** · cluster 数也受 ≤300 章总量约束 |
| L2.8 cluster 故事块自适应（新增） | **本指南的核心规则** |
| L4.5 「1 章/agent」颗粒度问题 | SUPERSEDED · cluster 颗粒度 ≥3 章沿用 |
| L4.6 蒸馏轮次预算 | SUPERSEDED · 调用数 = `len(cluster_index.clusters)` |
| L8.2 Agent prompt 必须含 PLAN_ID/STEP | 扩展 · 新增 `CLUSTER_ID` + `CHAPTER_RANGE` 字段 |

---

## 八、已蒸馏 5 本书的 cluster 兼容状态

| 书名 | cluster_index.json | schema | 切分时间 |
|---|---|---|---|
| BookC | ✅ 存在 | v22.cluster.x | retroactive |
| BookB | ✅ 存在 | v22.cluster.x | retroactive |
| 饲养全人类 | ✅ 存在 | v22.cluster.x | retroactive |
| 没钱修什么仙 | ✅ 存在 | v22.cluster.x | retroactive |
| BookA | ✅ 存在 | v22.cluster.2 | retroactive |

**意义**：5 本已蒸馏书都已补跑 cluster_segmenter；后续做"重蒸"或"增量蒸馏"都应**直接复用现有 cluster_index.json**，不要重切（除非用户明确要求）。
