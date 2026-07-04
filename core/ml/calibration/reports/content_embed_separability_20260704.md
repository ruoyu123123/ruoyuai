# ruoyu_style 语义可分性校准报告

- 生成时间：2026-07-04T05:12:47.538048+00:00
- 书目：主神大道, 诡秘之主, 轮回乐园
- seed=20260704 · 每类目标对数=150
- embedding 后端：content:bge-small-zh-v1.5（dim=512）

## 五类样本分布

| 类别 | n | p5 | p25 | p50 | p75 | p95 | mean |
|---|---|---|---|---|---|---|---|
| pos_adjacent | 150 | 0.3948 | 0.4636 | 0.5201 | 0.5974 | 0.6836 | 0.5282 |
| pos_same_chapter_far | 150 | 0.3491 | 0.3994 | 0.4593 | 0.5296 | 0.634 | 0.4711 |
| probe_same_book_diff_chapter | 150 | 0.3303 | 0.4028 | 0.4422 | 0.4883 | 0.5495 | 0.4445 |
| neg_cross_book | 150 | 0.324 | 0.3689 | 0.4136 | 0.4553 | 0.4983 | 0.4117 |
| summary_vs_body_pos | 150 | 0.3596 | 0.4437 | 0.5193 | 0.5751 | 0.6665 | 0.5115 |
| summary_vs_body_neg | 150 | 0.3001 | 0.3754 | 0.4124 | 0.4544 | 0.5165 | 0.4105 |

## 关键 AUC

- pos_adjacent_vs_neg_cross_book = **0.8592**
- pos_adjacent_vs_probe_same_book_diff_chapter = **0.7629**
- probe_vs_neg_cross_book = **0.6377**
- summary_vs_body_pos_vs_neg = **0.8079**

probe 与 neg 均值差 = 0.0328（风格敏感度：越大说明模型对「同作者」信号越敏感）

## 裁决

**A** —— 内容可分：AUC(adjacent vs neg_cross_book)=0.859≥0.7 且 AUC(adjacent vs probe)=0.763≥0.65——模型把「内容相关」和「同作者但内容无关」分得开，非单纯风格混淆。

## 各 relation_family 建议 operating point

### content_relatedness（AUC=0.8592 · 可用）
相邻强相关内容 vs 完全无关跨书内容——基础可分性上限
- neg p95（低误报候选）= 0.4983
- Youden 最优点 = 0.4708（TPR=0.7267 FPR=0.1467 J=0.58）

### content_vs_style_confound（AUC=0.7629 · 可用）
相邻强相关内容 vs 同书同风格但内容无关的远章节——内容敏感度（核心疑问）
- neg p95（低误报候选）= 0.5495
- Youden 最优点 = 0.4955（TPR=0.6333 FPR=0.22 J=0.4133）

### style_signal_strength（AUC=0.6377 · 不建议用）
同书跨章（同风格·内容不同）vs 跨书（异风格）——风格信号是否存在
- neg p95（低误报候选）= 0.4983
- Youden 最优点 = 0.4023（TPR=0.7533 FPR=0.54 J=0.2133）

### content_echo（AUC=0.8079 · 可用）
章首摘要代理 vs 正文——对应 SEMANTIC_RESONANCE_SIM_THRESHOLD(choice_consequence_ledger.py) / SEMANTIC_ANCHOR_SIM_THRESHOLD(deus_ex_solution_audit.py) / SEMANTIC_THREAD_MATCH_THRESHOLD(subplot_progress_update.py) / MILESTONE_SEMANTIC_TOUCH_FLOOR(volume_arc_drift_scanner.py) / topic_drift_scanner.py 的 scope_summary 校验
- neg p95（低误报候选）= 0.5165
- Youden 最优点 = 0.4904（TPR=0.6133 FPR=0.0867 J=0.5267）
