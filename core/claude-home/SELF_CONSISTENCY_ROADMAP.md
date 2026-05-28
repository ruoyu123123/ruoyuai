# Self-Consistency 高争议段重采样路线图（v21 P3.2 文档）

## 背景

业界研究（2025-2026）：
- **CISC / RASC / ReASC** 等 self-consistency sampling 方法 reduce 70% 推理成本 + 提升 accuracy
- 关键发现：高争议/高 stakes 段落用 multiple sampling + confidence-weighted voting 可显著降低 hallucination

## 我们现状

- ✅ `judge_consensus` 已有多 judge 投票
- ✅ `meta-judge` 已校准 judge 漂泊
- ❌ **writer 本身无 self-consistency** — 每章只写 1 次，关键章节抖动风险高

## 推荐分阶段实施

### Phase 1（轻量 · 立即可做）
对**关键章节**（满足以下任一条件）启动双 writer 并行：
- `cluster_blueprint[ch].beat` 含 `Catalyst / Midpoint / All Is Lost / Break Into Three / Finale`
- `mental_break_triggered == true`
- `pending_heart_event_reveals` 含 reveal
- `urgent_clocks` 触发
- 用户明示「精雕模式」

实现：
```
主代理 step "spawn_writer":
  if is_critical_chapter:
    parallel spawn novel-writer-A + novel-writer-B (不同 temperature)
    spawn novel-meta-judge → 投票挑选
  else:
    spawn novel-writer (单实例，省成本)
```

成本：关键章节 2x 写作 + 1x judge = 3x。但关键章节占比 < 15%，整体成本 +30%。

### Phase 2（业界 SOTA · 中期）
ReASC reliability-aware adaptive sampling：
- writer 写第一遍 → 算 confidence（基于 self_eval + waivers 数量）
- 高 confidence → 一次过；低 confidence → 重采样
- 平均 +30-50% 成本但 accuracy 显著提升

### Phase 3（fine-tune · 长期）
- 收集自己的 RLHF 数据（用户对走向卡的选择 = preference signal）
- 用 DPO 训本地小模型（Llama 3 / Qwen 2.5）做 voice keeper
- 长期可降低对 Claude API 依赖

## 实施 checklist

- [ ] Phase 1 主代理逻辑（关键章节判定 + 双 spawn）
- [ ] writer prompt 加 confidence 自评字段
- [ ] meta-judge 加 best-of-N 投票模式
- [ ] Phase 2 ReASC 算法
- [ ] Phase 3 DPO 数据采集 pipeline

## 参考

- [ReASC 2026 arxiv](https://arxiv.org/html/2601.02970v1)
- [Confidence-Improved Self-Consistency](https://aclanthology.org/2025.findings-acl.1030.pdf)
- [Best-of-N Self-Certainty](https://arxiv.org/pdf/2502.18581)
