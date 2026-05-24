---
description: 项目偏好向导 — 把"系统擅自决定"全部交给用户决定（v21 UX1 新增）
---

# 📋 项目偏好向导

$ARGUMENTS

你是若渝AI的**偏好引导者**。本命令的唯一目标：把全流程中**之前系统擅自决定的 20+ 决策点**交给用户决定，输出 `_数据库/用户偏好.json`。

## 何时调用

- 开新书时（推荐 /outline 之前先跑）
- 用户说「我想调下设置」「重新配置」
- 用户问「能不能改 X」时，看 X 是否在本向导覆盖

## 设计原则

- **分组询问**：不要一次问 20 个问题（用户会崩溃）
- **5 组问题，每组 3-5 个问题**
- **默认值要解释**（"默认 cassandra 因为传统长篇用 80%"）
- **每组完成后可暂停**（用户可中断后续配置）
- **进阶用户可 /wizard --quick 用所有默认**

## 执行流程

### 准备阶段
1. Read `core/claude-home/schemas/user_preferences_schema.json` 了解全字段
2. Read 现有 `_数据库/用户偏好.json`（如有）作为基线
3. 询问用户的总体定位（用 AskUserQuestion）

### 组 1：项目基础（必问 · v23.12 简化版）

> 📝 **v23.12 已废除 v23.11 章数密度公式**。本组只问节奏档作软提示，**不再问目标章数 / 卷数 / event 数 / filler ratio**。
> 理由：故事块（cluster）+ 涟漪效应让单卷章数无法预先锁定；章数由 ME 触发自然涌现。

- **`rhythm_profile` 节奏档位**（默认「混合」 · 仅作软提示）：
  - **紧凑**：高密度 event、转折频繁（短中篇）
  - **标准**：主流商业网文节奏（中长篇）
  - **厚重**：慢热铺垫、event 间章数多（史诗/严肃文学）
  - **混合（推荐）**：重 event 拉长 + 轻 event 紧凑（大部分长篇）
- **每章字数目标**：
  - 网文：1800-3000 / 短篇正文：2500-4000 / 长篇：3000-5000
- **DCAS 双章模式**：默认 true（推荐）
- **写作模式**：完整/轻量

**禁问**：~~`target_chapter_count`~~ / ~~`volume_count`~~ / ~~`events_per_volume`~~ / ~~`filler_ratio`~~ —— v23.12 删除。**禁跑**：~~`T × (1-F) / (V × E)` 公式~~。

### 组 2：叙事节拍（关键个人偏好）
- **storyteller_profile**：
  - cassandra = 标准升压（适合 长篇/严肃文学）
  - phoebe = 长间歇爽文（适合 网文/治愈系）
  - randy = 随机展开（适合 脑洞向/沙盒）
- **expected_setback_per_n_ch**：
  - 爽文：8-12（主角少吃亏）
  - 标准：4-6
  - 黑深残：2-3
- **happy_vs_dark_ratio**：0.0-1.0
  - 0.8 = 爽文
  - 0.5 = 平衡
  - 0.3 = 悲剧向

### 组 3：叙事结构（v20+v21）
- **outline_mode**：
  - strict = 大纲死定每章（短篇适合）
  - fluid = 大势池涌现（长篇适合，**默认**）
  - hybrid = 卷一 strict + 后续 fluid
- **major_events_count**：13（500 章建议 13-20）
- **use_position_effect_judgment / use_throughlines / use_mckee_truby_arc**：高级用户选关 / 默认全开

### 组 4：心理/情感系统（个性化重）
- **stress_threshold_break**：4-15
- **allowed_mental_break_cards**：5 张可勾选
  - 治愈系建议去 005_嗜杀
  - 黑深残建议全保留
- **use_active_aspects / use_fate_dice**：默认全开

### 组 5：交互 / 校验严格度
- **use_fate_cards**：是否要每章走向卡（默认 true）
- **fully_auto**：全自动模式
- **audit_mode**：
  - strict = 全严卡（出稿慢/质量高）
  - advisory = 顾问制（**默认**）
  - permissive = 宽容（快速出稿）
- **cross_chapter_scan_intensity**：full_18 / core_10 / minimal_5

### 组 6（可选）：高级 + model routing
- 一般跳过
- 高级用户可改 writer_model / 关 manifest_compression 等

## 输出

Write 到 `_数据库/用户偏好.json`，按 schema 结构：

```json
{
  "_meta": {
    "wizard_completed_at": "<ISO>",
    "schema_version": "v21"
  },
  "project_basics": {...},
  "narrative_pacing": {...},
  "narrative_structure": {...},
  "character_psychology": {...},
  "interactive_mode": {...},
  "quality_control": {...},
  "anti_slop_personal": {...},
  "agent_model_routing": {...},
  "advanced": {...}
}
```

## 完成后返回

```
✅ 偏好向导完成

总览：
- 项目定位：[<总章数> 章 / 平均 <字数>/章 / DCAS=<bool> / <写作模式>]
- 叙事节拍：[storyteller=<profile> / 爽虐比 <ratio> / setback 每 <N> 章]
- 叙事结构：[outline=<mode> / 大势 <N> 个]
- 心理系统：[stress=<阈值> / 允许 break 卡 <N>/5 / aspects=<bool>]
- 交互：[走向卡=<bool> / 全自动=<bool> / 校验=<mode>]
- 反套话：[禁词 <N> 个 / 偏好句式 <N> 个]

下一步：执行 /outline 生成大纲（系统会用本偏好）
```

## 硬性纪律

- **必须用 AskUserQuestion 工具问每个组**
- **每组提供合理默认值 + 一句解释**
- 用户不答某问 → 用 default
- 不要假装"我帮你选了 X"——只问用户回答
- 完成后必 Write 到 `_数据库/用户偏好.json`

## 与现有系统集成

向导写完的 用户偏好.json 会被：
- `build_manifest` 注入 manifest（agent 据此调整行为）
- `outline` 命令读 `project_basics` + `narrative_structure`
- 各 agent prompt 读 `narrative_pacing` / `character_psychology`
- audit_hub 读 `quality_control.audit_mode`
- save-state 读 `quality_control.cross_chapter_scan_intensity`
- writer 读 `anti_slop_personal.user_banned_words`

## 升级/重置

- `/wizard --quick`：跳过所有问，用全 default（适合熟练用户）
- `/wizard --update <组名>`：只更新某组（如 `--update narrative_pacing`）
- `/wizard --reset`：清空重来
