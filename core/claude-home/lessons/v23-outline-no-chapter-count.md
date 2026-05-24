# v23.12 大纲章数解锁（废除 v23.11 硬公式）

> **状态**：MANDATORY · 适用所有 `/outline` 新流程
> **创建**：2026-05-24
> **触发**：用户反馈"故事块 + 涟漪效应让单卷章数无法预先确定，前期不应该规划每卷章数"
> **决策者**：用户（AskUserQuestion 选项 A 完全去除章数预计）

---

## 0 · 背景：v23.11 当初为什么强制

v23.11（2026-05 引入）通过强制公式 `T × (1-F) / (V × E)` 反推每卷 event 密度，目的防「ch80 翻车」：

> **历史翻车**：用户想写 500 章，但大纲只 30 event × 4 章 = 实际 120 章封顶，写到 ch80 才发现大纲已用完。

v23.11 三层防御：
- **L1** wizard 组 1 必问 `target_chapter_count` / `volume_count` / `events_per_volume` / `filler_ratio`
- **L2** outline Step 1.5 必跑公式 + 校验 A ∈ [4,12]
- **L3** outline-planner agent 缺 `_metadata` → 拒绝生成 cluster_brief

## 1 · 为什么 v23.12 废除

### 1.1 设计冲突

v23.11 跟系统其他设计本质冲突：

| 系统设计 | v23.11 假设 | 实际冲突 |
|---|---|---|
| **故事块（cluster）模式** | 每 cluster 章数可估算 | cluster 长度由 ME 重要度 + writer 涌现决定，**单卷 cluster 数会膨胀** |
| **涟漪效应（fate_engine）** | event 触发节奏可预测 | 用户每章涟漪选择会让下游 ME 推迟/提前/分裂，**章数无法静态推算** |
| **fluid 模式（v20）** | 章号不锁死 | v23.11 又把章数算回来，**两层逻辑打架** |
| **大势卡 example** | `expected_window_after: {max_chapters: 15-100}` 宽窗 | v23.11 公式锁死 events_per_volume = 8-10 / 卷，**宽窗失效** |

### 1.2 用户原话（2026-05-24）

> 「我小说系统里，我发现前期规划总是把每卷多少章节都规划出来了，但是我走的是故事块模式，并且会触发涟漪效应，可能单卷故事块会导致章节很多很多，所以前期不应该给每卷的章节数目规划出来，只需要描述大势是怎么样的就行」

### 1.3 决策矩阵（AskUserQuestion）

提供 4 选项：
- A. 完全去除章数预计 ← **用户选**
- B. 保留全局软目标，每卷不锁
- C. fluid 模式跳查、strict 保留 v23.11
- D. 暂不动

用户选 A：**完全去除**。

## 2 · v23.12 现行规则

### 2.1 保留 / 删除矩阵

| 状态 | 项 | 备注 |
|---|---|---|
| ✅ 保留 | `rhythm_profile`（紧凑/标准/厚重/混合）| 仅作软提示，影响 outline-planner cluster 章数弹性估算 |
| ✅ 保留 | `volumes[]` 的 `core_conflict` / `volume_arc` / `key_milestones` / `ending_state` | 描述**大势**核心 |
| ✅ 保留 | 大势卡 `major_events[]` 的 `expected_window_after` 宽窗 | 涌现触发（如 `max_chapters: 15-100`） |
| ❌ 删除 | `target_chapter_count` | wizard 不再问 |
| ❌ 删除 | `volume_count` | wizard 不再问 |
| ❌ 删除 | `events_per_volume` | 大势卡 _metadata 不再写 |
| ❌ 删除 | `avg_chapters_per_event` | 大势卡 _metadata 不再写 |
| ❌ 删除 | `filler_ratio` | wizard 不再问 |
| ❌ 删除 | `volumes[].chapter_range`（[1, 10] 死锁区间）| 进度.json schema 删字段 |
| ❌ 删除 | v23.11 公式 `T × (1-F) / (V × E)` | outline Step 1.5 整段重写 |

### 2.2 大势卡 `_metadata` 仅保留 1 个字段

```json
{
  "_metadata": {
    "rhythm_profile": "混合",
    "designed_at": "ISO 日期",
    "version": "v23.12"
  }
}
```

### 2.3 outline-planner cluster `estimated_chapters` 弹性估算

```python
BASE_BY_RHYTHM = {"紧凑": 4, "标准": 7, "厚重": 12, "混合": 7}
base = BASE_BY_RHYTHM.get(metadata.rhythm_profile, 7)

if ME.priority == 5: estimated_chapters = round(base × 1.6)
elif ME.priority == 4: estimated_chapters = round(base × 1.2)
elif ME.priority <= 2: estimated_chapters = round(base × 0.5)
else: estimated_chapters = base

estimated_chapters = clamp(estimated_chapters, 2, 20)  # 软钳制
```

**estimated_chapters 是 hint 不是 hard cap** —— writer 实际超出不报错，涟漪可膨胀。

## 3 · 风险接受声明

v23.11 当初为了防 ch80 翻车。**v23.12 接受这个 risk**。

### 3.1 ch80 翻车风险复发可能

用户写 ~~500 章~~（v23.12 不再设此目标），但大势卡 ME 池只 30 个 ME × 6 章/event = ~180 章 → 写到 ch180 大势用完。

### 3.2 应对方式（替代 v23.11 的事前防御）

| 场景 | v23.11 做法 | v23.12 做法 |
|---|---|---|
| 大势池设计 | 事前公式反推 ME 数量 | **不预设**，写到 ME 用尽时**动态加新 ME** |
| 卷分界 | 事前定 `chapter_range: [1,10]` | 写到 ME 池过半 + ending_state 达成时**自然切卷** |
| 章数预期 | 锁死 500 章 | **不锁**，用户随时可在 save-state 阶段决定继续 / 收尾 |

### 3.3 用户可选的"硬底线"（非强制）

用户偏好里可选填 `_soft_intent.target_chapter_count_aspiration`（**仅作心理预期**，不参与公式 / 不在 plan_status 显示 / 不报警）。

## 4 · 修改的文件清单

| 文件 | 改动 |
|---|---|
| `CLAUDE.md`（项目根） | 删 v23.11 段，替换为 v23.12 段 |
| `core/claude-home/CLAUDE.md` | 同上 |
| `.claude/commands/outline.md` | 删 Step 1.5 整段重写为节奏档软提示版 + Step 2 加 v23.12 规则 + Step 3 进度.json schema 删 chapter_range |
| `.claude/commands/wizard.md` | 组 1 删 target_chapter_count / volume_count / events_per_volume / filler_ratio 必问 |
| `.claude/agents/novel-outline-planner.md` | ECAS estimated_chapters 决策树改为按 rhythm_profile 弹性估算 |
| `core/claude-home/templates/examples/*/大势卡.example.json` × 2 | 验证：两个 example 本来就没 v23.11 字段，无需改 |

## 5 · 业界依据（v23.12 决策的支撑）

- **fluid 模式涌现叙事**（v20 引入，参考 SidekickWriter / 好莱坞方法论）—— 已证明章号不锁死可行
- **scp_anomaly_bureau 大势卡 example**—— 18 ME × 12 卷涌现叙事示范，`expected_window_after: {max_chapters: 15-100}` 宽窗
- **用户实际工作流**—— 故事块（cluster_segmenter.py）+ 涟漪（fate_engine.py）已稳定运行多本书

## 6 · 后续 monitor

| 监控项 | 触发条件 | 应对 |
|---|---|---|
| **ch80 翻车复发** | 写到大势池过半但用户还想继续 | save-state 弹"想继续吗？是 → 加新 ME，否 → 进入收尾" |
| **rhythm_profile 不准** | 用户实际 cluster 章数持续偏离 estimated_chapters ±50% | learning_loop 累积统计，给 calibration 建议 |
| **章数估算被滥用** | 主代理把 estimated_chapters 当 hard cap 卡 writer | reading-reflector 检测 → 报盲点 |

---

**v23.12 本质上是把"章数"从『系统强制约束』降级到『用户心理预期』。系统不再代用户操心总章数，由用户自己在写作过程中决定何时收尾。**
