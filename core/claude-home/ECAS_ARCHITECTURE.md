# ECAS v23 架构设计

> Event-Cluster Auto Split — v22 DCAS 全面升级版
> 借鉴 [StoryWriter (arxiv 2506.16445)](https://arxiv.org/abs/2506.16445) + [StoryBox (arxiv 2510.11618)](https://arxiv.org/html/2510.11618v3)
> 设计日期：2026-05-17

## 1. 核心理念

**v22 DCAS 痛点**：以「双章 5600 字」为单位 = 人为切碎事件，cliffhanger 强造，伏笔/因果链常被章节边界打断。

**v23 ECAS 思路**：以「**一个 ME 大势事件**」为生成单位 = 8000-16000 字一气呵成 → splitter 切成 N 章 → 章节边界全在事件内部自然过渡。

```
旧 v22 DCAS:    writer 5600 字 → splitter 切 2 章
新 v23 ECAS:    writer 8K-16K (1 个 ME 事件)
                  ↓ writer 内 mid-checkpoint × 3 + sub-summary 锚定
                  ↓ Critical events 用 Opus 4.7 + Extended Thinking
                splitter multi-chapter (N 章自适应)
                  ↓ N 个 ch.txt + N-1 个 pre_opening.txt
                每章独立 audit + voice-keeper（沿用 v22 章节级 judge）
                  ↓
                cluster 级 cross-chapter scan（新增）
                  ↓
                save-state + git commit cluster
```

## 2. 数据流图

```
┌─────────────────────────────────────────────────────────────────┐
│                    ECAS Pipeline (v23)                          │
└─────────────────────────────────────────────────────────────────┘

[大势卡 18 ME] ──┐
                  ├──> outline-planner ──> [event_cluster_brief]
[事件池 抽签]  ──┤                            │
                  │                            ▼
[cluster_blueprint]  ──┘                       brief 含:
                                          - cluster_id (ME_002)
                                          - parent_me
                                          - scope_summary
                                          - expected_word_range [8000, 12000]
                                          - scenes_estimated 4
                                          - anchor_props
                                          - foreshadowing_to_plant
                                          - mid_checkpoints (3K/6K/9K)
                                          - opus_recommended (true/false)
                                                │
                                                ▼
[manifest with event_cluster_context] ──> novel-writer (ECAS mode)
                                                │
                                          writer 内部循环:
                                          ┌───────────────────────┐
                                          │ 写 ~3000 字 →         │
                                          │ mid-checkpoint:       │
                                          │   - voice spot-check  │
                                          │   - 字数 vs 预算       │
                                          │   - 伏笔进度          │
                                          │ → sub-summary 100字   │
                                          │ → 注入下段头部 (LiM) │
                                          └─────────┬─────────────┘
                                                    │ × N 次
                                                    ▼
[第N事件簇_草稿.txt 8K-16K]                  Write 完整 cluster 草稿
                                                    │
                                                    ▼
                                  chapter-splitter (multi-chapter mode)
                                                    │
                                          算法升级:
                                          - 估算合理章数 N = ceil(words/target)
                                          - 找 N-1 个等距锚点
                                          - 每个锚点按 v22.6 三机制 score
                                          - 输出 N 个 ch.txt + N-1 个 pre.txt
                                                    │
                                                    ▼
[ch_N.txt × N] + [ch_N+1/.pre_opening.txt × N-1]
                                                    │
                            ┌──────────────────┬────┴────┬──────────────────┐
                            ▼                  ▼         ▼                  ▼
                      audit_hub(ch1)    audit_hub(ch2) ...           audit_hub(chN)
                      voice-keeper(ch1) voice-keeper(2)...           voice-keeper(N)
                                                    │
                                                    ▼
                                       cluster-level cross-chapter scan
                                       (新增: 簇内一致性 / 节奏 / 伏笔覆盖率)
                                                    │
                                                    ▼
                                              save-state (per chapter)
                                                    │
                                                    ▼
                                              git commit cluster
```

## 3. Agent 拓扑（与 v22 对照）

| Agent | v22 角色 | v23 ECAS 角色 | 改造点 |
|---|---|---|---|
| **novel-outline-planner** | 生成下章走向卡 | **生成下个事件簇 brief** | 输入大势卡 + 抽签事件，输出 cluster_brief |
| **novel-writer** | 单章/DCAS 双章 | **ECAS 簇写 8K-16K + 内部 mid-checkpoint + sub-summary** | 加 MODE=ecas + 4 防御 |
| **novel-chapter-splitter** | DCAS 切 2 章 | **multi-chapter 切 N 章** | score 算法升级支持 N 章 |
| **novel-validator-repair** | 章级修复 | 章级修复（不变） | 沿用 |
| **novel-voice-keeper** | 章级 voice 审 | 章级 voice 审（不变） | 沿用 + 新增 cluster 内一致性 |
| **novel-foreshadower** | 章级伏笔评估 | **cluster 级伏笔覆盖率** | 评估整簇是否兑现 brief.foreshadowing_to_plant |
| **novel-reflector** | 章级经验沉淀 | **cluster 级 + 章级**（双层） | 簇内技巧 + 簇间模式 |
| **novel-summarizer** | 章级 200 字摘要 | 章级摘要（不变） + cluster_summary 500 字 | 加 cluster 总摘要 |
| **novel-researcher** | 调研 | 调研（不变） | 沿用 |
| **novel-meta-judge** | 10 章周期审 | **cluster 周期审**（每 2-3 簇） | 触发条件改 |
| **novel-meta-prompt-optimizer** | meta-prompt 优化 | 不变 | 沿用 |

## 4. v22 → v23 字段映射表

| v22 字段 | v23 等价字段 | 兼容策略 |
|---|---|---|
| `manifest.dcas_word_target` | `manifest.ecas_word_range` (min/max) | v23 同时输出 dcas_word_target=range.min 向后兼容 |
| `章节/第N章/第N章.txt` | 保持不变（每章仍独立 txt） | 100% 兼容 |
| `章节/第N章/第N章_changes.json` | 加 `cluster_id` + `cluster_position` 字段 | v22 章 cluster_id=null（合法） |
| `故事块摘要.chapters[ch]` | 同结构 + 加 `cluster_id` 字段 | 向后兼容 |
| `进度.cluster_blueprint[ch]` | 同 + 加 `cluster_id` + `cluster_position` | 向后兼容 |
| `_数据库/大势卡.json` (18 ME) | **不变** — 仍是事件 spawn 源 | 100% 复用 |
| `_数据库/事件池.json` | **不变** | 100% 复用 |
| 新增 `_数据库/事件簇.json` | cluster 池 + 元数据 | 由 outline-planner 维护 |
| `_数据库/.manifest/ch_NNN.json` | 同 + 加 `event_cluster_context` 字段 | 向后兼容 |
| 新增 `_数据库/.ecas_checkpoints/cluster_NNN_chunk_M.json` | writer mid-checkpoint 落盘 | 新目录 |
| 新增 `_数据库/章节/第N簇_草稿.txt` | writer 完整 cluster 草稿（splitter 切前） | 新文件类型 |
| 用户偏好 `dcas_dual_chapter_mode` | `ecas_enabled` + `cluster_word_range` | v23 默认 enabled，老项目可关 |

## 5. 失败回滚策略

**writer 内 mid-checkpoint 失败（每 3000 字一次）**：
1. checkpoint 检测 voice/字数/伏笔不符 → 回滚最近 1500 字
2. writer 重写该段（保留前 1500 字 + 重生成 1500 字）
3. 累计 3 次 checkpoint 失败 → spawn fallback writer（用 Opus 4.7 + Extended Thinking 重写整簇）

**完整 cluster 草稿失败（splitter 阶段）**：
1. splitter 找不到合理 N-1 锚点 → 报警上报主代理
2. 主代理决定：spawn writer 重写 / 降级到 DCAS 双章模式 / 人工 review
3. 降级 DCAS：把 cluster 拆成 2 个 sub-cluster，每 sub-cluster 走 DCAS

**章级 audit 失败（per-chapter）**：
1. 沿用 v22 流程：validator-repair ≤3 轮
2. 不影响其他章节（章级独立）

**cluster 级 cross-chapter scan 失败**：
1. 簇内一致性 < 阈值 → spawn cluster-validator-repair
2. 修不好 → 整簇标记 `_failed_cluster_<id>` 待人工 review
3. 不阻塞下个 cluster 启动（缓解传播）

**ECAS 完全失败回滚到 DCAS**：
1. 用户偏好 `ecas_enabled` 改 false
2. 系统自动 fallback 到 v22 DCAS 流程（writer 5600 字 + 2 章 splitter）
3. ECAS 资产保留（事件簇.json 不删，便于未来回切）

## 6. Use Cases

### Use Case 1: ME 大势事件（典型场景）
```
ME_002: 老周递红色档案盒首次提先生
预算: 10000 字 / 4 章
clusters[ME_002] = {
  cluster_id: "cluster_002",
  parent_me: "ME_002",
  scope_summary: "老周观察陈默后递档案 → 陈默看完独自调查 → 决定继续",
  expected_word_range: [9000, 11000],
  scenes_estimated: 4,
  anchor_props: ["红色档案盒 ITM_003", "老周左手缺指", "南郊小李子墓"],
  foreshadowing_to_plant: ["FS_003 (planted ch3)", "FS_004 (planted ch3)", "FS_008 (subtle ch4)"],
  foreshadowing_to_callback: ["FS_011 ch2 → ch3 强化"],
  mid_checkpoints: [3000, 6000, 9000],
  opus_recommended: false,  // 中点重要但非 Catalyst/Midpoint/Finale
  extended_thinking: false
}
splitter 切: ch3 (2800) + ch4 (2500) + ch5 (2400) + ch6 (2300) [4 章]
```

### Use Case 2: 关键事件 ME_010「先生」首次现身
```
ME_010: 先生现身三选一招揽 (Midpoint)
预算: 14000 字 / 5 章
opus_recommended: TRUE  ← 关键章用 Opus 4.7
extended_thinking: TRUE  ← Midpoint 必启 thinking
mid_checkpoints 加密: [3000, 5000, 7000, 9000, 11000, 13000]
splitter 切: ch55-ch59 共 5 章
```

### Use Case 3: 短事件（单挑事件）
```
E_009: 出租屋邻居异常事件
预算: 5000 字 / 2 章
expected_word_range: [4500, 5500]
opus_recommended: false
- 自适应模式让 model 自决 = 写到 5000 字自然收
- splitter 切 2 章（退化为 DCAS-like 模式）
```

### Use Case 4: 跨 ME 复合事件（多线并行）
```
parent_me: ["ME_004", "ME_005"]  (王局询问 + 序列觉醒)
- 一个 cluster 覆盖 2 个 ME（罕见但可能）
- 预算 16000 字 / 6 章
- 双 POV 切换（陈默 / 林秋）
- splitter 切时优先按 POV 边界
```

## 7. 与现有 v22 系统协同

| v22 系统 | ECAS 适配 |
|---|---|
| **大势卡 18 ME** | 直接作为 cluster 抽签库 |
| **18 cross_chapter scanner** | 沿用 + 加 cluster 维度（pre_chapter 改为 pre_cluster） |
| **22 v22.5 学习器** | 沿用 + 加 cluster_pattern_extractor（簇级共性） |
| **plan_tracker** | 加 write-event-cluster command + 16 步 plan template |
| **wal_recovery** | cluster 级 WAL（writer 写一半崩可断点恢复） |
| **prompt cache** | cluster_brief 作为新 STATIC 段（首次写入后整簇 cache 复用） |
| **fate_engine** | 不变（事件抽签层独立） |
| **world_evolution_engine** | apply_cluster 替代 apply_chapter（簇结束时统一 tick） |
| **character_arc_state** | 章级 + 簇级双层更新（簇结束触发大 stage 推进） |

## 8. 关键创新点（vs DCAS / 业界）

| 创新 | 描述 | 业界对照 |
|---|---|---|
| **事件级生成单位** | 1 簇 = 1 ME 大势事件 | StoryWriter 事件级 outline ✓ |
| **自适应字数** | brief 给 range，writer 自决具体字数 | StoryBox 动态窗口 ✓ |
| **mid-checkpoint 防退化** | 每 3000 字 self-audit + 回滚机制 | 业界少见，受 CoT chunked verify 启发 |
| **sub-summary LiM 缓解** | 每场景 100 字摘要注入下段头部 | LiM 论文 + StoryWriter 动态压缩 ✓ |
| **multi-chapter splitter** | 按事件复杂度切 2-5 章自适应 | NovelCrafter 单章 → ECAS 多章 |
| **cluster 级 audit** | 簇级一致性 + 章级 quality gate 双层 | 业界单层 |
| **fallback 到 DCAS** | ECAS 失败可优雅降级 | 业界一般不留退路 |

## 9. 实施步骤总览（与 ecas-v23-transition plan 16 步对应）

| step | 名称 | 关键产出 |
|---|---|---|
| 1 | 锁定决策 + 架构文档 | 本文档 ✅ |
| 2 | event_cluster_brief schema | schemas/event_cluster_schema.json |
| 3 | writer ECAS 模式 | novel-writer.md 升级 |
| 4 | splitter multi-chapter | novel-chapter-splitter.md 升级 |
| 5 | build_manifest event_cluster | build_manifest.py 加字段 |
| 6 | save_state mid-checkpoint | save_state.py 加 --ecas-checkpoint |
| 7 | narrator cluster stop | outline-planner.md 升级 |
| 8 | outline-planner cluster brief | outline-planner.md 升级 |
| 9 | changes_schema cluster_id | schemas/changes_schema.json |
| 10 | write-event-cluster.plan | plans/write-event-cluster.plan.json |
| 11 | user_preferences ECAS 字段 | schemas/user_preferences_schema.json |
| 12 | KNOWN_COMMANDS + hooks | plan_tracker.py + hooks ✅ |
| 13 | dashboard cluster view | project_dashboard.py |
| 14 | migration guide | ECAS_MIGRATION.md |
| 15 | ch3 ECAS pilot | 事件簇.json + ch3.txt |
| 16 | system audit + memory + commit | system_health_audit + MEMORY |

## 10. FAQ

**Q1: ECAS 会让单次 LLM 调用成本暴涨吗？**
A: 是（10K 字 ≈ 3 倍单章成本），但「不考虑成本」决策已锁定。收益是 50%+ 章节衔接质量提升 + 伏笔/因果链不被打断。

**Q2: ch1/ch2 已用 DCAS 写完，会被 v23 重写吗？**
A: 不会。changes_schema 加 cluster_id=null 兼容旧章节。新章节从 ch3 起用 ECAS。

**Q3: 用户停顿点变长（每 3-5 章一次）会失控吗？**
A: 提供 fallback 模式：用户偏好 `cluster_stop_frequency=per_chapter` 可保留每章停顿（writer 仍按簇生成，但走向卡按章给）。

**Q4: mid-checkpoint 失败重写浪费 token 怎么办？**
A: 这是 SOTA tradeoff。「不考虑成本」决策已接受。学习闭环会逐渐减少失败率。

**Q5: ECAS 和 v22 DCAS 三机制（字数硬约束）冲突吗？**
A: 不冲突。multi-chapter splitter 沿用 v22.6 三机制（字数硬约束 + 动态总字数 + 后置兜底），只是从「切 2 章」扩展到「切 N 章」。

## 11. 后续路线图（v24 候选）

- **embedding 检索的 sub-summary**（精准锚定相关历史）
- **cluster reward modeling**（用户喜欢的 cluster 模式自动加权）
- **dynamic cluster fusion**（小事件自动合并大事件减少 splitter 失败）
- **multi-writer 并行**（多 cluster 并行生成 + cross-cluster 调和）
