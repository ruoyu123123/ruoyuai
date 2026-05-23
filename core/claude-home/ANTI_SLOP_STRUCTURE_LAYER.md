# 结构层 anti-slop N 维清单（v23.5）

> v23.5 前瞻性预防机制 — 防 [[feedback_meta_anti_slop_structure_blindspot_recurrence]] 第 4 次重犯
> 修订日期：2026-05-17 / 已记录 3 次重犯（字数 v23.2 / 破折号 v23.3 / 代词 v23.4）

## 核心论断

> **AI 写作 anti-slop 分两层**：词汇层好做（词典 + prompt 禁用）；**结构层难做**（跨章扫描）。每次 writer 学一个 voice 工艺，**如果不给频率/比例上限**，就会变成下一次 AI 套路过用。

## 已知 12 维结构层套路（持续扩展）

| # | 维度 | 健康指标 | 检测器 | 案例 |
|---|---|---|---|---|
| 1 | **字数单位** | CJK 严格量纲，brief 含 `unit: "CJK_chars"` required | `ecas-checkpoint` fail-loud | [[feedback_ecas_word_count_5_layer_break]] v23.2 |
| 2 | **「——」破折号密度** | ≤ 12/千字 / 单段 ≤ 1 / 极端段（≥3）= 0 | `dash_density_scan.py` | [[feedback_dash_overuse_structure_layer]] v23.3 |
| 3 | **代词「他/她」节奏** | 他 ≤ 角色名 × 1.5x / 单段连续「他」开头 ≤ 2 句 | `pronoun_density_scan.py`（待） | [[feedback_pronoun_overuse_structure_layer]] v23.4 |
| 4 | **段落结构** | 段均长 15-25 字 / 单句成段率 ≤ 60% / 极短段 ≤ 30% | `cross_chapter_pattern_scan.py` 已有 | writer 经验记录 |
| 5 | **句首单调性** | 段首主语相同 ≤ 3 段连续 | 待加 | 隐性 |
| 6 | **句式重复** | 「他没动」/「他想」/「然后」开头 ≤ N% | 待加 | 隐性 |
| 7 | **POV 一致性** | 全章零叙述层「我」（third_person_limited） | hook 规则 + voice-keeper | [[feedback_pov_must_ask]] v23.2 |
| 8 | **时态一致性** | 全章一致过去时 / 现在时不混 | 待加 | 隐性 |
| 9 | **对话标签密度** | 「他说」/「他道」/「他说道」 ≤ N% | 待加 | 隐性 |
| 10 | **拟声词密度** | 「砰」/「咚」/「嗖」 ≤ 5/千字 | 待加 | writer 经验有 |
| 11 | **形容词堆叠** | 单名词前形容词 ≤ 2 | 待加 | 隐性 |
| 12 | **设定堆叠** | 一章 > 3 个新设定 = 红牌 | 已 writer 教育 | LE_006 |
| 13 | **物件 orphan** | 单次出现物件 ≤ N | `cross_chapter_pattern_scan.py` | NARRATIVE_orphan |
| 14 | **场景边界感** | 时间跳跃 / 地点切换的过渡密度 | 待加 | 隐性 |
| 15 | **顿号「、」三段式** | 连续 ≥3 个顿号「X、Y、Z、W」≤ N/章 | 待加 | SEMANTIC_forced_triple |
| 16 | **真实世界时间禁令** (v23.6) | 正文 / 元数据 0 个「20XX 年」/「20XX-MM-DD」/「20XX/MM/DD」 | `D9_real_world_time` (structure_layer_anti_slop_scan) | [[feedback_real_world_time_forbidden]] - 用户禁令 |
| 17 | **具名对话标签冗余** (v23.7) | 「X说，/X道，」≤ 8/千字 / 连续「具名说」对话 ≤ 2 段（双人对话省标签 + 动作节拍代替） | `D10_named_dialogue_tag` (structure_layer_anti_slop_scan) | [[feedback_named_dialogue_tag_overuse]] - 用户反馈 |

## 已实施 / 待实施 status

- ✅ 已实施检测：1 (字数) / 2 (破折号) / 4 (段落) / 7 (POV) / 12 (设定堆叠 - writer 教育) / 13 (orphan) / 15 (顿号)
- ⚠️ 待实施：3 (代词 - 待 v23.4 加) / 5 (句首单调) / 6 (句式重复) / 8 (时态) / 9 (对话标签) / 10 (拟声) / 11 (形容词) / 14 (场景边界)

## v23.5 「每加 voice 工艺必问 4 问」纪律

每次给 `novel-writer.md` / `novel-outline-planner.md` 加新 voice 工艺时（如「破折号 = 0.3 秒延迟标记」），**必须问 4 问**：

1. **这工艺如果被 LLM 无限放大，会变成什么 AI 套路？**
   - 例：破折号 → 单段 5+ 个 / 代词「他」→ 段首单调 / 拟声词 → 每段都加
2. **频率/比例上限是多少？**
   - 例：破折号 ≤ 12/千字 / 「他」 ≤ 角色名 × 1.5x
3. **检测器在哪？**
   - 例：dash_density_scan.py / pronoun_density_scan.py
4. **voice-keeper 怎么评估？**
   - 例：voice-keeper 跑该 scanner 报告作为 voice 一致性的子维度

**违背 4 问的 voice 工艺禁止入 writer.md**（PR-style review）。

## v23.5 统一扫描器

`core/scripts/structure_layer_anti_slop_scan.py` 汇总所有 N 维到一个报告：

```bash
python core/scripts/structure_layer_anti_slop_scan.py <project_root> [--ch N | --all]
# 输出: _数据库/.cross_chapter_scan/structure_layer_<ts>.json
#   summary: { healthy_dimensions: 7, warning: 3, error: 2 }
#   per_dimension: { dash: {...}, pronoun: {...}, ... }
```

## save-state 集成

`save-state.plan.json` step 9（cross_chapter_scan）已包含 `run_cross_chapter_scans.py`。
v23.5 加：每 5 章自动跑 `structure_layer_anti_slop_scan.py --all` 做趋势分析（防 ch1→ch14 122x 连续退化被隐藏）。

## 迭代节奏

- **每发现新套路 → 必加入本清单**（即便只 1 次案例）
- **每加 voice 工艺到 writer.md → 必跑 4 问 + 加检测器**
- **每 1 cluster 写完 → 看 structure_layer scan 报告**（不只是 audit_hub）

## 失败模式 - 不要重蹈

1. **case-by-case 修复不够**（v23.2/3/4 重犯 3 次）— 必须 taxonomy 级覆盖
2. **commit log 写了 ≠ memory 写了**（v23.3 漏记）— 必须独立 memory 文件
3. **检测工具 fail-quiet → 静默通过**（v23.2 checkpoint case）— 必须 fail-loud
4. **趋势退化被几个 cluster 隐藏**（ch1→ch14 退化没人发现）— 必须趋势分析 scanner
5. **用户读到具体段落才发现**（已 3/3 次）— 表明检测器盲区严重

## 当前已知 anti-slop 套路完整清单（grep -l 词典层）

参考：`core/scripts/anti_slop.py`（词典层）/ 本文档（结构层）/ `cross_chapter_pattern_scan.py`（跨章趋势层）

三层覆盖：
- **词汇层** = 禁用词 + 用词偏好（anti_slop.py / catchphrase）
- **结构层** = 本文档 N 维（本 v23.5 新建）
- **趋势层** = cluster N+1 vs N 风格漂移（待 v24 加）
