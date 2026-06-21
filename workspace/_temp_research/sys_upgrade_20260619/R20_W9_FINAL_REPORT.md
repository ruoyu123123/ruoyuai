# R20 W9 Batch-CC P2 收尾·最终报告

**日期**: 2026-06-21
**Batch**: R20 W9 Batch-CC (P2 收尾)
**前序基线**: 6125 tests (Batch-BB 73bd3ae)
**落地后**: 6206 tests (+81 · 0 回归)

## 落地 6 件 P2

| # | scanner / 扩展 | 文件 | issue code | env | 测试数 |
|---|----------------|------|------------|-----|-------|
| 1 | mid_chapter_micro_cliffhanger_cadence_scanner | new | MID_CHAPTER_CLIFF_CADENCE_OFF_BAND | MID_CHAPTER_CLIFF_CADENCE_MODE | 14 |
| 2 | prose_rhythm_scanner 探针9: 300字 payload | extended | PARAGRAPH_300CJK_PAYLOAD_OFF_BAND | PROSE_300CJK_PAYLOAD_MODE | 11 |
| 3 | paragraph_engagement_heat_predictor: comment-triggered density | extended | (复用 PARAGRAPH_ENGAGEMENT_FLATLINE) | PARAGRAPH_ENGAGEMENT_HEAT_MODE | 5 |
| 4 | forecasting_tension_scanner | new | FORECASTING_TENSION_FLAT | FORECASTING_TENSION_MODE | 14 |
| 5 | imageability_scanner + imageability_zh.json | new + new placeholder dict | IMAGEABILITY_OFF_BAND | IMAGEABILITY_MODE | 16 |
| 6 | ACW directive (build_manifest) + acw_drift_scanner | new + extension | ACW_DRIFT_FROM_CENTER | ACW_MODE | 16 + 5 |

测试合计新增: 14+11+5+14+16+16+5 = **81 新 case** · 0 回归。

## 关键设计点

### 1. mid_chapter_micro_cliffhanger_cadence
- **缺口**: hook_strength_scanner 只看章末 + 拟切点强度均值·缺『章内多 hook 间距分布』
- **做法**: 复用 hook_strength 11 型 regex 子集(避免循环依赖)·收集全 cluster hook 位置序列·相邻间距 mean/median/pstdev·间距 <500 过密 / >3000 过疏
- **z-band**: 作者档 micro_cliffhanger_cadence_baseline.{mean_distance, std_distance} (z>1.0 报) · 无作者档兜底 μ=1500 σ=600 (z>1.5 报)
- **正交守卫**: hook_strength(章末/拟切点) / cliffhanger_quota(跨章配比)

### 2. prose_rhythm 探针 9 - 300字 payload
- **场景**: 移动端竖屏阅读甜区·段长 300CJK±50 (250-350) 占比·番茄/起点/七猫 2024-2025 拆书经验
- **作者档**: paragraph_300cjk_share_baseline.{target, std} (z>1.5 报偏离) · 兜底 target=0.20 std=0.10
- **集成**: 直接扩 prose_rhythm metrics 字段 + author_baseline 字段·无新文件

### 3. paragraph_engagement comment-triggered
- **新字段**: comment_triggered_density (热度 ≥ 0.30 段占比) + position_distribution (head/mid/tail)
- **新 advisory**:
  - 密度 <5% 且至少 1 段触发 → 评论甜点稀缺
  - 任一段位 >60% 且 ≥5 段触发 → 分布失衡

### 4. forecasting_tension (占位)
- **真版**: 句级 SBERT 自相关 → 张力 forecast horizon·依赖 sentence-transformers·defer
- **占位**: char Shannon entropy 相邻 200 CJK 块差分 (textual surprise proxy)
  - gradient_pstdev (相邻块 entropy 差的 pstdev) → 张力梯度变异
  - flatline_ratio (|diff|<0.05 占比) → 平稳张力
- **触发条件**: gradient_pstdev < 0.3 且 flatline_ratio > 0.55
- **正交**: narrative_rhythm(序列级 macro) / premature_resolution(冲突→消解距离 macro)

### 5. imageability (占位)
- **真版**: Paivio 1968 dual-coding theory + Coltheart MRC 1500+ 中文 norms·defer 授权
- **占位**: core/data/imageability_zh.json 60 高具象(桌椅刀杯/血汗泪骨/雨雪风云) + 60 低具象(意义本质/概念理念/可能必然)
- **指标**: imageability_index = (high-low)/(high+low) ∈ [-1, 1]
- **作者档**: imageability_baseline.{mean, std} z-band (|z|>1.0 报偏离) · 兜底 μ=0 σ=0.3
- **正交**: repeat_noun_density / semantic_slop / scene_grounding

### 6. ACW Activity-Centric Writing
**双面落地**:
- **directive 侧** (build_manifest): env ACW_MODE=active 时注入 ACW_DIRECTIVE 字段 (off/shadow=null)·告知 writer『每段聚焦一个中心活动·禁止子动作堆叠·主语切换=换段』
- **scanner 侧** (acw_drift_scanner): HEAD_LEN=1·句首 1 CJK 主语 proxy·段内 distinct_head_ratio > 0.75 = 中心漂移·全 cluster drift_paragraph_ratio > 30% 触发 ACW_DRIFT_FROM_CENTER

## 工程纪律

- ✅ 全 advisory · shadow 默认 · hard_gate 12 码不变
- ✅ 每 scanner 含 `test_code_not_in_hard_gate` 守卫
- ✅ 作者档第一权威·无作者档退兜底常量
- ✅ 占位词典/占位实现标 `_placeholder=true`
- ✅ audit_hub 注册 4 新 scanner (mid_chapter_cliff_cadence + forecasting_tension + imageability + acw_drift) · 2 扩展(prose_rhythm 探针9 + paragraph_engagement 字段) 复用既有调度
- ✅ scanner_registry 注册 7 entries (含 6 新 + extending 文档化)
- ✅ pytest 6206/6206 PASS · 无 Traceback · 0 回归 from 6125

## 后续 defer

- forecasting_tension: 真版 SBERT 句级嵌入·依赖 sentence-transformers·授权 + GPU
- imageability: 真版 Paivio MRC 1500+ 中文 norms·授权或 distill 抽取
- acw_drift: 真版 SBERT 段中心活动 cluster (而非简单首字主语 proxy)
- 真校准: 6 件全 shadow 默认·真实金标准抽样 (惊悚乐园/诡秘/主神等 真作者 cluster) 校准阈值后才能升 active
- micro_cliffhanger_cadence_baseline / paragraph_300cjk_share_baseline / forecasting_tension_baseline / imageability_baseline / acw_baseline 字段需经 consolidate_author_profile.py 集成抽取入作者档

## 命中文件清单

新建 (6 + 1 数据 + 7 测试 = 14):
- `core/scripts/mid_chapter_micro_cliffhanger_cadence_scanner.py`
- `core/scripts/forecasting_tension_scanner.py`
- `core/scripts/imageability_scanner.py`
- `core/scripts/acw_drift_scanner.py`
- `core/data/imageability_zh.json`
- `tests/test_mid_chapter_micro_cliffhanger_cadence_scanner.py`
- `tests/test_prose_rhythm_300cjk_payload.py`
- `tests/test_forecasting_tension_scanner.py`
- `tests/test_imageability_scanner.py`
- `tests/test_acw_drift_scanner.py`
- `tests/test_acw_directive_build_manifest.py`

修改 (6):
- `core/scripts/audit_hub.py` (注册 4 新 scanner task entries)
- `core/scripts/build_manifest.py` (import os + ACW directive 字段注入 + ACW_MODE env 检测)
- `core/scripts/prose_rhythm_scanner.py` (探针 9 paragraph_300cjk_share · baseline 字段扩展 · metrics 字段扩展)
- `core/scripts/paragraph_engagement_heat_predictor.py` (comment_triggered_density + position_distribution 字段 + 2 新 advisory 触发)
- `core/scripts/scanner_registry.json` (注册 6 新 entries + 1 kishotenketsu 边界保留)
- `tests/test_paragraph_engagement_heat_predictor.py` (扩 5 新 test case 覆盖新字段 + 位置偏置检测)
