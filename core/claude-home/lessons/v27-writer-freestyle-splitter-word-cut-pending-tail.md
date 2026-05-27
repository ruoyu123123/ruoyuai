---
name: v27-writer-freestyle-splitter-word-cut-pending-tail
date: 2026-05-27
version: v27.0
type: architecture-major
related: v26-build-manifest-fluid-cluster-mode, v23-outline-no-chapter-count
---

# v27 三件套：writer 自由 + splitter 字数切 + 跨 cluster 补料

## 背景

cluster_001-005 端到端走完后用户发现：

> 「故事块能切多少章我发现你一开始已经间接限制死了，这是不对的，应该让ai自由发挥，只要不脱离既有事实和大势，然后根据生成内容的字数，按照固定范围字数进行切割（一定程度上要参考最佳切割点），最后一章切出来字数不够就拿下一个故事块生成后的内容来补一些，这个补也是要放在切割的过程中」

锁死点（v26 残余）：
1. **gen_writer.py 默认 `--target-cjk 13000-22000`** → writer prompt 注入字数硬约束 → AI 自由度受限
2. **cluster brief.expected_word_range required** → outline-planner 必须算字数预算 → 反推章数死锁
3. **splitter MODE=ecas_multi_chapter 接 `TARGET_CHAPTERS`** → 主代理传 N 章 → splitter 按 N 平均切（writer 写多了硬塞 / 写少了硬撑）

## v27 改造方案

### 1. writer freestyle（gen_writer.py）

- `--chapter-end` 改 optional（默认 None）· 缺省 = freestyle 模式
- `--target-cjk` 改 optional（默认 None）· 缺省 = 不注入字数硬约束
- `build_prompt(freestyle=True)`:
  - user prompt 「目标字数」段不输出
  - 「splitter 切成 ch{X}-ch{Y} 共 N 章」改成「splitter 按字数 3000-4500/章 自然切 · 章数由你写的内容决定」
  - 「按 7 项硬铁律 + 元 anti-slop 写 N 章」改成「完整覆盖 cluster_brief 的所有 scene_storyboard 自由发挥（章数由 splitter 后期切，你不必管）」
- `save_output` 加元数据：`writer_mode: "freestyle_v27"` / `chapter_count_decided_by_splitter: true` / `ch_range: "X-TBD_by_splitter"`

### 2. event_cluster_schema.json 字段降级

- `required` 移除 `expected_word_range`（仍可写 · 但仅 advisory）
- 加 `_writer_mode: "freestyle" | "locked"`（default: "freestyle"）
- `estimated_chapters` / `chapter_range` 标 `_v27_status: "deprecated · outline 阶段禁止写 · splitter 切完自动填"`

### 3. outline.md 加 step 1.7

- AskUserQuestion 1：「《<书名>》第 1 卷你想要几个故事块（cluster）？」
  - 4-5（紧凑短篇向） / 6-8（标准，推荐） / 9-12（厚重长篇向） / Other
  - 答案写入 `_数据库/用户偏好.json.workflow_preferences[cluster_count_per_volume]`
- AskUserQuestion 2：「writer 写作模式 · 推荐 freestyle」（首次新书）
  - freestyle（v27 默认） / locked（v26 兼容）
- 反推 ME 数：用户答 N → 大势卡 V1 必须含 N+1 个 ME（弹性）

### 4. novel-chapter-splitter 加 MODE: ecas_freestyle

字数硬范围 3000-4500/章自动算 N：
```python
N_min = math.ceil(draft_cjk / 4500)
N_max = math.floor(draft_cjk / 3000)
N_recommend = round(draft_cjk / 3500)
N = max(N_min, min(N_max, N_recommend))
```

`rhythm_profile` 微调区间：紧凑 3000-4000 / 厚重 3500-5000。

末章 < 3000 → 退回 pending_tail.txt（不切 · 等下 cluster 拼）。

### 5. 跨 cluster 字数补料（pending_tail 机制）

```
cluster N writer 写完 → splitter 切完 N-1 章 + 末段 < 3000 写 pending_tail.txt
                                                ↓
cluster N+1 writer 写完 → cluster-write step 6 调度器检测 pending_tail
                                                ↓
                  传 PREVIOUS_PENDING_TAIL_PATH 给 splitter
                                                ↓
        splitter 把 pending_tail prepend 到 cluster N+1 草稿头部 + 联合切
                                                ↓
                切出的 ch_K 章号回填到 cluster N 的 chapter_range
```

最简实施：split_cluster_changes 按 splitter_wal.chapter_range 切 · pending_tail 段的 factual 自然留在 cluster_changes.json 不分配 · 等 cluster N+1 拼接后归并到下个 cluster 的 ch_changes（v27.1 优化）。

## 失败避免

- ❌ 不要在 cluster_001-005 已存的 brief 上回填 `_writer_mode: "freestyle"`（向后兼容）
- ❌ writer prompt 顶部「7 项硬铁律 #4 单章字数 2500-5000」**保留**（splitter 切完每章的硬约束 · 不是 writer 写时的）
- ❌ pending_tail.txt **不算独立章节**（不写 章节/第NNN章/ 目录）

## 调研支撑

- 用户原话锁定 4 个改动点（writer/outline/splitter/补料）
- cluster_001-005 实测：v26 锁死字数导致 cluster_005 round 1 reflector 拒绝 final_pass（cascade 排比末段强行凑字）

## 验证

cluster_006 走 v27 全流程（writer freestyle / outline AskUser / splitter ecas_freestyle / 末章 pending_tail）。
