# ruoyu_style 语义可分性校准报告

- 生成时间：2026-07-03T16:57:25.471371+00:00
- 书目：主神大道, 诡秘之主, 轮回乐园
- seed=20260704 · 每类目标对数=150
- embedding 后端：ruoyu_style:final（dim=768）

## 五类样本分布

| 类别 | n | p5 | p25 | p50 | p75 | p95 | mean |
|---|---|---|---|---|---|---|---|
| pos_adjacent | 150 | 0.2217 | 0.6891 | 0.8455 | 0.9696 | 0.9957 | 0.76 |
| pos_same_chapter_far | 150 | 0.3331 | 0.724 | 0.8197 | 0.9641 | 0.9937 | 0.7867 |
| probe_same_book_diff_chapter | 150 | 0.2482 | 0.6902 | 0.8376 | 0.9586 | 0.9908 | 0.7753 |
| neg_cross_book | 150 | 0.0405 | 0.5962 | 0.8016 | 0.934 | 0.9919 | 0.7098 |
| summary_vs_body_pos | 150 | 0.4925 | 0.7657 | 0.8745 | 0.9721 | 0.9963 | 0.836 |
| summary_vs_body_neg | 150 | 0.2256 | 0.572 | 0.7321 | 0.8954 | 0.9893 | 0.6989 |

## 关键 AUC

- pos_adjacent_vs_neg_cross_book = **0.5632**
- pos_adjacent_vs_probe_same_book_diff_chapter = **0.5088**
- probe_vs_neg_cross_book = **0.5582**
- summary_vs_body_pos_vs_neg = **0.6887**

probe 与 neg 均值差 = 0.0655（风格敏感度：越大说明模型对「同作者」信号越敏感）

## 裁决

**B** —— 部分可分：AUC(adjacent vs neg)=0.563、AUC(adjacent vs probe)=0.509（内容敏感度不足）、AUC(content_echo)=0.689——部分族可用，部分族的高分可能是风格混淆而非真内容相关，需按族取舍。

## 各 relation_family 建议 operating point

### content_relatedness（AUC=0.5632 · 不建议用）
相邻强相关内容 vs 完全无关跨书内容——基础可分性上限
- neg p95（低误报候选）= 0.9919
- Youden 最优点 = 0.9407（TPR=0.3533 FPR=0.2267 J=0.1267）

### content_vs_style_confound（AUC=0.5088 · 不建议用）
相邻强相关内容 vs 同书同风格但内容无关的远章节——内容敏感度（核心疑问）
- neg p95（低误报候选）= 0.9908
- Youden 最优点 = 0.9894（TPR=0.1333 FPR=0.0533 J=0.08）

### style_signal_strength（AUC=0.5582 · 不建议用）
同书跨章（同风格·内容不同）vs 跨书（异风格）——风格信号是否存在
- neg p95（低误报候选）= 0.9919
- Youden 最优点 = 0.7639（TPR=0.7 FPR=0.5267 J=0.1733）

### content_echo（AUC=0.6887 · 可用）
章首摘要代理 vs 正文——对应 SEMANTIC_RESONANCE_SIM_THRESHOLD(choice_consequence_ledger.py) / SEMANTIC_ANCHOR_SIM_THRESHOLD(deus_ex_solution_audit.py) / SEMANTIC_THREAD_MATCH_THRESHOLD(subplot_progress_update.py) / MILESTONE_SEMANTIC_TOUCH_FLOOR(volume_arc_drift_scanner.py) / topic_drift_scanner.py 的 scope_summary 校验
- neg p95（低误报候选）= 0.9893
- Youden 最优点 = 0.7159（TPR=0.8333 FPR=0.5133 J=0.32）
