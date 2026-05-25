# 若渝AI v19 改造计划 · 检测体系「顾问制」改造

> **理念转向**：检测工具从「门禁/法官」改为「顾问」——工具只提醒，AI 有充分理由可驳回。
> **触发**：v18 后的维度分析发现两个真缺口（F 层读者体验薄弱、检测工具大量是不可反驳的硬门禁）。
> **基线**：v18（commit d4c937f）之后。

---

## 一、背景与理念

v18 解决了「正文/数据分离」的架构病根。v19 解决**检测体系的定位问题**：

1. **维度覆盖**：A 机械 / B 文笔 / C 叙事工艺 / D 情节结构 / E 一致性 五层覆盖充分；**F 读者体验层薄弱**（章末钩子强度、爽点/反转、黄金三章无专门工具）；G 内容逻辑层靠 LLM judge 兜底（合理，但 14 维评分颗粒度粗）。
2. **权力定位问题**：大量工具是「硬门禁」（validate_chapter exit、validate_style FAIL→强制 repair、hook exit 2）——假设「工具说有问题=一定有问题」。但纯心理独白章被判「拟声 0 处 FAIL」、过渡章被判「WC_TOO_SHORT」、卷尾故意总结式收束被判 `HOOK_SUMMARY_ENDING`——这些都是**工具不适配场景、AI 应能有理由驳回**的情况。
3. 系统里唯一接近「驳回」的是 `narrative_scanner.suppressed_warnings`，但那是工具自我设限、非 AI 主动驳回，且只此一家。

**v19 核心原则**：
- 检测工具输出的是「待裁决项」，不是「判决」
- AI（writer / validator agent）可对任一 advisory 项「豁免 + 理由」；理由不充分则不算豁免，仍按问题处理
- 唯一例外：**E 层一致性**（锁定事实冲突、知识边界泄露、伏笔断裂、引用未声明实体）是**客观错误**不是风格选择，保持 `hard_gate` 不可豁免
- learning_loop 学习「反复被合理豁免的 issue」→ 反向校准工具阈值，而非反复骚扰 AI

---

## 二、三大改造块

### 块 1 — 补 F 层「读者体验」检测工具

| 任务 | 目标 | 改动文件 | 做法 | 验收 |
|---|---|---|---|---|
| **1.1 钩子强度检测器** | 章末钩子做**正向评分**（不只查反模式） | 新建 `core/scripts/hook_strength_scanner.py` | 评 4 类钩子（悬念/冲突/反差/信息缺口）+ 钩子位置（开头/中段/章末）+ 强度分。命令行 `python hook_strength_scanner.py <项目> <章节>`，输出 JSON | 旧稿 ch1-3 跑出合理钩子分；纯过渡章不误判 |
| **1.2 黄金三章检测器** | 前 3 章特殊规则程序化 | 新建 `core/scripts/golden_three_scanner.py` | 检测：第一句冲突性 / 500字内主角行动出场 / 章末强钩子 / 信息差明确 / 禁止日常开场。仅对 ch1-3 激活，其余章节返回 `n/a` | 旧稿 ch1-3 跑出可解释结果 |
| **1.3 validator 维度细化** | 14 维 → 16 维，爽点更具体 | `.claude/agents/novel-validator-repair.md` | 加 `hook_strength`（章末钩子）+ `payoff_design`（爽点节拍设计），比泛泛的 `emotionally_engaging` 具体 | score_16dim schema 自洽 |
| **1.4 audit_hub 接入** | 新检测器纳入统一调度 | `core/scripts/audit_hub.py` | hook_strength / golden_three 加入 `audit_chapter` 的 scanner 列表，归类到「读者体验」维度 | audit_hub --json 含新检测器结果 |

### 块 2 — 「工具建议 → AI 裁决 → 记录理由」机制（v19 核心）

| 任务 | 目标 | 改动文件 | 做法 | 验收 |
|---|---|---|---|---|
| **2.1 权力等级标签** | 每条 issue 标 `gate_level` | `core/scripts/audit_hub.py` + 各 scanner | issue 结构加 `gate_level: "hard_gate" \| "advisory"`。E 层一致性 code（LOCKED_FACT_CONFLICT / FUTURE_KNOWLEDGE_LEAK / FORESHADOWING_NOT_PAID / SECRET_NOT_REVEALED / UNKNOWN_CHARACTER_DETECTED / CHANGES_MISSING 等）= hard_gate；其余 = advisory | 每条 issue 都有 gate_level |
| **2.2 audit_hub waived verdict + 豁免协议** | verdict 增加豁免出口 | `core/scripts/audit_hub.py` | verdict 增加 `waived`；issue 结构加 `waived: bool` + `waive_reason: str`。新增 `--waivers <json>` 入参接收豁免清单。hard_gate 项即便传了豁免也强制忽略豁免（不可豁免） | 传入合理豁免 → 该 advisory issue 转 waived 不计入 needs_agent；hard_gate 项豁免无效 |
| **2.3 豁免理由载体** | 豁免理由有地方存 | `chapter_io.py` schema 说明 + `novel-writer.md` / `novel-validator-repair.md` | `_changes.json` 的 `self_eval` 增加 `waivers: [{code, reason}]` 段；validator agent 可在 JudgeReport 增加 `waivers` 段。理由 < 100 字、须具体（"本章是纯心理独白章，拟声不适配"合格；"不想改"不合格） | writer/validator 能写入豁免；audit_hub 能读取 |
| **2.4 scanner warning 可豁免化** | 推广 narrative_scanner 的 suppressed 思路 | `narrative_scanner.py` / `plot_structure_scanner.py` / `pacing_analyzer.py` / `emotion_arc_analyzer.py` | 所有 scanner 输出的 warning 默认带 `gate_level: advisory`（⚠️ **实现期裁决**：原计划的 `waivable: true` 字段已砍掉——`gate_level: advisory` 单字段即表达「可豁免」，加 waivable 属冗余。最终实现只用 `gate_level` 单字段）；narrative_scanner 的 `suppressed_warnings`（工具自适配）保留，与 AI 豁免并存（工具自适配 + AI 主动豁免双层） | 各 scanner 输出统一带 `gate_level: advisory` 标记 |
| **2.5 learning_loop 豁免统计** | 反复豁免 → 校准工具 | `core/scripts/learning_loop.py` | 新增 `--ingest` 时统计 `waivers`：同一 code 在同类章节被豁免 ≥N 次 → 产出「工具校准建议」（建议调阈值/加场景适配），写入 `写作经验.json` 的新段 `tool_calibration_suggestions`，而非反复让 AI 豁免 | 喂 3 章同类豁免 → 产出校准建议 |
| **2.6 judge agent + 流程接入** | agent 知道自己有豁免权 | 6 个 `novel-*.md` + `write-chapter.md` + `save-state.md` | 明确写入：「检测工具是顾问不是法官。对 advisory 项，如有充分理由可豁免——豁免必带具体理由，写入 self_eval.waivers / JudgeReport.waivers。hard_gate 项不可豁免。」write-chapter 第 3.8 步 audit_hub 调用传 `--waivers` | 文档明确顾问制；流程串通 |

### 块 3 — 硬门禁边界明确 + 文档

| 任务 | 目标 | 改动文件 | 做法 | 验收 |
|---|---|---|---|---|
| **3.1 不可豁免清单固化** | hard_gate 清单写死、有共识 | `core/claude-home/STRUCTURE.md` 新增章节 | 明列 hard_gate code 清单（E 层一致性 + 文件契约类）+ 理由（客观错误非风格选择） | 清单明确、可被工具引用 |
| **3.2 文档与 CLAUDE.md 同步** | 顾问制理念入文档 | `STRUCTURE.md` / `write.md` / `write-chapter.md` / `save-state.md` / 两份 `CLAUDE.md` | 写明 v19 顾问制：工具提醒、AI 可驳回、豁免带理由、learning_loop 反向校准 | grep 无残留「门禁/强制 repair」旧表述 |
| **3.3 回归测试** | 全链路验证 | （测试，不改产品代码） | 用 `_archive/回炉前_v1` 旧稿：① 新检测器跑出合理结果 ② 豁免协议端到端（写豁免→audit_hub 读→转 waived）③ hard_gate 豁免无效 ④ learning_loop 豁免统计 ⑤ 旧 v18 链路不回归 | 全链路通过，出验收报告 |

---

## 三、依赖关系与执行顺序

```
2.1 权力等级标签 ─┬─→ 2.2 waived verdict ─→ 2.3 豁免理由载体 ─┬─→ 2.6 流程接入
                  └─→ 2.4 scanner 可豁免化 ────────────────────┤
                                          2.5 learning_loop ───┤
1.1/1.2 新检测器 ─→ 1.3 validator维度 ─→ 1.4 audit_hub接入 ─────┤
3.1 hard_gate 清单（与 2.1 同期）─────────────────────────────┤
                                                              └─→ 3.2 文档 ─→ 3.3 回归测试
```

**关键路径**：2.1 → 2.2 → 2.3 → 2.6（豁免机制主链）
**可并行**：块 1（新检测器）整体独立，可与块 2 并行；3.1 与 2.1 同期

---

## 四、Agent Team 分工建议（吸取 v18 教训）

| Teammate | 负责 | 任务 |
|---|---|---|
| **gate-core** | 豁免机制核心（关键路径） | 2.1 权力等级标签 + 2.2 waived verdict + 2.3 豁免理由载体 |
| **scanner-new** | F 层新检测器 | 1.1 钩子强度 + 1.2 黄金三章 + 1.4 audit_hub 接入 |
| **learn-scanner** | 自学习 + scanner 升级 | 2.4 scanner 可豁免化 + 2.5 learning_loop 豁免统计 |
| **agent-doc** | agent 定义 + 文档 | 1.3 validator 16维 + 2.6 judge agent/流程接入 + 3.1 hard_gate 清单 + 3.2 文档 |
| **Leader** | 协调 + 3.3 回归测试 + 集成 | — |

**v18 教训强制项**（写入每个 teammate 的 spawn prompt）：
- 分步做、改一个报一个、绝不憋大招
- 长 turn 里也要分段处理 inbox + 每里程碑主动回报进度（参考 memory: long-turn-inbox-discipline）

---

## 五、不可豁免的 hard_gate 清单（v19 固化）

以下是**客观错误**，不是风格选择，AI 不可豁免：

| code | 来源 | 为什么不可豁免 |
|---|---|---|
| `LOCKED_FACT_CONFLICT` | validate_chapter | 正文与已锁定事实冲突 = 设定矛盾 |
| `FUTURE_KNOWLEDGE_LEAK` | validate_chapter | 角色知道不该知道的 = 逻辑错误 |
| `FORESHADOWING_NOT_PAID` | validate_chapter | Tier-1 到期伏笔未回收 = 承诺违约 |
| `SECRET_NOT_REVEALED` | validate_chapter | 秘密该揭未揭 = 剧情债 |
| `UNKNOWN_CHARACTER_DETECTED` | validate_chapter | 引用未声明实体 = 引用错误（注：分词误检的另算，需先修检测器） |
| `CHANGES_MISSING` / `MANIFEST_MISSING` / `FILE_NOT_FOUND` | validate_chapter | 文件契约破损 |
| `ITEM_HOLDER_ABSENT` / `ITEM_NOT_YET_INTRODUCED` | validate_chapter | 道具状态矛盾 |
| `PROPAGATION_DEBT_CREATED` | validate_chapter | 跨集合数据未同步 |

**其余全部** advisory，可凭充分理由豁免：A 机械 / B 文笔 / C 叙事工艺 / D 情节结构 / F 读者体验 的所有检测项（含 validate_style 12 项、narrative/plot scanner、pacing/emotion analyzer、hook_strength、golden_three）。

---

## 六、验收标准（v19 整体）

- [ ] 块 1：hook_strength + golden_three 检测器落地，旧稿跑出合理结果，audit_hub 接入
- [ ] 块 2：audit_hub 有 `waived` verdict；豁免协议端到端跑通（写理由→读→转 waived）；hard_gate 豁免无效；learning_loop 能统计反复豁免产出校准建议
- [ ] 块 3：hard_gate 清单固化进 STRUCTURE.md；顾问制理念入所有相关文档；全链路回归通过
- [ ] 不破坏 v18：正文/数据分离、chapter_io、原有 12 个任务的产出不回归

---

*v19 计划 · 2026-05-14 · 基线 v18(d4c937f)*
