# ECAS v23 迁移指南

> v22 DCAS → v23 ECAS 升级 / 回滚 / 兼容指南
> 与 [ECAS_ARCHITECTURE.md](./ECAS_ARCHITECTURE.md) 配合使用

## 1. 兼容性保证

**100% 向后兼容**：v22 项目无需改任何已写章节。新章节从启用 ECAS 开始用新流程。

| 已写章节 | ECAS 启用后 | 兼容方式 |
|---|---|---|
| ch1 (DCAS, cluster_id=null) | 不动 | 旧 changes.json 仍 valid |
| ch2 (DCAS, cluster_id=null) | 不动 | 旧 changes.json 仍 valid |
| ch3 起新章节 | ECAS cluster | 新 changes.json 带 ecas_metadata |
| 大势卡 18 ME | 复用 | ECAS cluster.parent_me 直接引用 |
| 事件池 抽签事件 | 复用 | outline-planner 仍读 |
| 章纲摘要 | 加 cluster_id 字段 | 旧条目 cluster_id=null |
| 用户偏好 | 加 ecas_config 段 | 旧字段不动 |

## 2. 升级步骤（v22 项目接入 ECAS）

### 步骤 1：项目级偏好配置
编辑 `_数据库/用户偏好.json` 加：
```json
"ecas_config": {
  "ecas_enabled": true,
  "cluster_word_range": {"default_min": 8000, "default_max": 12000, "critical_event_min": 13000, "critical_event_max": 16000},
  "critical_events_use_opus": ["ME_010", "ME_015", "ME_017"],
  "extended_thinking_critical": true,
  "mid_checkpoint_interval": 3000,
  "max_checkpoint_failures": 3,
  "cluster_stop_frequency": "per_cluster",
  "sub_summary_enabled": true,
  "auto_cluster_size_estimation": true
}
```

### 步骤 2：初始化事件簇文件
新建 `_数据库/事件簇.json`：
```json
{
  "schema_version": "v23.0",
  "_doc": "事件簇池 (clusters) - 与大势卡.json 配套",
  "clusters": []
}
```

### 步骤 3：跑 outline-planner 生成首个 cluster brief
spawn novel-outline-planner MODE=ecas_cluster_brief 自动写入 事件簇.json。

### 步骤 4：用 /write-event-cluster 命令而非 /write-chapter
新流程：用户偏好 ecas_enabled=true 时主代理优先用 write-event-cluster.plan.json (15 步)。

## 3. 回滚步骤（ECAS 失败回到 DCAS）

如 ECAS 在某 cluster 失败连续 3 次：

### Soft 回滚（项目级）
```json
"ecas_config": {
  "ecas_enabled": false,
  "_disabled_reason": "writer mid-checkpoint 连续 3 簇失败，回到 DCAS",
  "_disabled_at": "2026-MM-DD"
}
```

### Hard 回滚（特定 cluster）
该 cluster 标记 status=`failed`，主代理 fallback 流程：
1. 把 cluster 拆成 2-3 个 sub-cluster
2. 每 sub-cluster 走 DCAS 双章模式
3. 标记 ecas_metadata.cluster_position=`solo`（兼容方式）
4. 不影响其他 cluster 继续 ECAS

### 完全卸载 ECAS
```bash
# 把 ecas_enabled=false
# 不删除事件簇.json (保留供后续重启)
# write-event-cluster.plan.json 不删 (供后续重启)
```

## 4. 字段映射表（详细）

### changes.json 兼容
```json
// v22 章节 (ch1/ch2) - 100% valid
{
  "factual": {...},
  "self_eval": {...}
  // ecas_metadata 字段不存在 = cluster_id null 隐含
}

// v23 章节 (ch3+) - 加 ecas_metadata
{
  "factual": {...},
  "self_eval": {...},
  "ecas_metadata": {
    "cluster_id": "cluster_002",
    "cluster_position": "head",  // head/mid/tail/solo
    "cluster_total_chapters": 4,
    "cluster_word_total": 10500,
    "writer_model_used": "claude-sonnet-4-6",
    "checkpoint_data": {
      "mid_checkpoint_results": [...]
    }
  }
}
```

### manifest 兼容
```json
// v22 manifest (ch1/ch2)
{
  "chapter": 1,
  "active_characters": [...],
  // event_cluster_context 字段不存在 = MODE=dcas/single 隐含
}

// v23 manifest (ch3+, ECAS cluster head 章)
{
  "chapter": 3,
  "active_characters": [...],
  "event_cluster_context": {
    "cluster_id": "cluster_002",
    "parent_me": "ME_002",
    "scope_summary": "...",
    "expected_word_range": {"min": 9000, "max": 11000},
    "mid_checkpoints": [3000, 6000, 9000],
    "foreshadowing_to_plant": [...],
    "anchor_props": [...]
  }
}
```

### 章纲摘要兼容
```json
// v22
{"ch": 1, "title": "...", "scenes": [...]}

// v23 (加字段)
{"ch": 3, "title": "...", "scenes": [...], "cluster_id": "cluster_002", "cluster_position": "head"}
```

## 5. ECAS vs DCAS 决策矩阵

| 场景 | 推荐模式 |
|---|---|
| 关键 ME (Midpoint/Break Into Three/Finale) | **ECAS + Opus 4.7 + Extended Thinking** |
| 标准 ME 大势事件 | **ECAS Sonnet 4.6** |
| 单挑事件 (E_001 类) | **ECAS solo cluster** 或 DCAS 双章 |
| 过场章节 (角色互动 / 日常) | **DCAS 双章** 或单章 |
| 用户偏好 ecas_enabled=false | **DCAS 强制** |
| 短篇 (< 10 章) | **DCAS** (ECAS 优势在长篇) |
| 超长 (> 200 章) | **ECAS 必用** (省 spawn 次数) |

## 6. FAQ

**Q: 我的 ch1/ch2 已经用 DCAS 写完，要重写吗？**
A: 不需要。changes.json 加 ecas_metadata 字段后旧章节 cluster_id=null 仍合法。

**Q: ECAS 完整 cluster 失败一半怎么办？**
A: writer 累计 3 次 checkpoint 失败 → 主代理 fallback Opus + Extended Thinking 重写整簇。如再失败 → soft 回滚到 DCAS 写该事件。

**Q: 短事件 (5000 字) 用 ECAS 划算吗？**
A: 用 ECAS solo cluster（estimated_chapters=2）。比 DCAS 多一个 cluster_brief 设计成本，但 audit/voice 流程一致。

**Q: 用户偏好 cluster_stop_frequency 怎么选？**
A: 
- 长期写作 + 自动化 → `per_cluster`（默认）
- 兼容旧习惯 + 每章决策 → `per_chapter`
- 仅关键节点停 → `critical_only`

**Q: cluster 内某章 audit 失败影响其他章吗？**
A: 不影响。每章独立 audit + 独立 validator-repair（≤3 轮）。一章修不好 → 整簇标 `partial_failed` 但其他章继续。

**Q: ECAS 启用后还能用 write-chapter 命令吗？**
A: 能。但主代理会优先用 write-event-cluster.plan.json。除非用户明确说 "用单章模式写 chN"。

**Q: meta-judge 触发条件变了吗？**
A: v22 每 10 章 → v23 改成每 3 个 cluster（约 9-15 章）。cluster 级 meta-judge 更聚焦。

## 7. 测试 / 验证清单

升级后必验证：
- [ ] `python core/scripts/db_schema_validate.py <project>` 0 error 0 warning
- [ ] `python core/scripts/build_manifest.py <project> <first_new_ch>` 含 event_cluster_context 字段
- [ ] spawn novel-outline-planner MODE=ecas_cluster_brief 成功写 事件簇.json
- [ ] spawn novel-writer MODE=ecas 草稿字数 in expected_word_range
- [ ] spawn novel-chapter-splitter MODE=ecas_multi_chapter 切 N 章全 ≤ ceiling
- [ ] 每章独立 audit verdict in (pass/waived/auto_fixed)
- [ ] git commit cluster 单 commit 含所有 N 章
- [ ] dashboard 显示 cluster 维度进度

## 8. 已知限制

- ECAS 首个 cluster 必须等 step 5 + 6 实现完才能跑（build_manifest event_cluster_context 注入 + save_state --ecas-checkpoint 子命令）
- 现有 cross_chapter scanner 22 个需要按 cluster 维度做小改造（scope=cluster vs scope=pre_chapter）
- meta-judge 触发条件改造需要在 v23 第一个 cluster 完成后验证

## 9. 路线图（v24 候选）

- embedding 检索的 sub-summary（精准锚定相关历史段落）
- cluster reward modeling（用户选择数据反哺自动加权）
- dynamic cluster fusion（小事件自动合并大事件减少 splitter 失败）
- multi-writer 并行（多 cluster 并行生成 + cross-cluster 调和）
