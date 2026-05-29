---
description: 若渝AI 命令系统总览（文档非命令）
---

# 若渝AI 命令系统

## 系统概览

若渝AI 是一个以**故事块（cluster）为单位**的 AI 小说写作系统，包含 13 个命令（+2 索引文档）、34 个核心子系统 JSON。
v26 起写作单位从「章节」升级为「故事块（cluster）」，v27 起 writer 自由发挥 + splitter 按字数切。

> 🔴 **2026-05-29 精简（只保证故事块流程）**：命令文件 44→15（13 命令 + 2 索引文档）。短剧 /script、孤儿命令、自进化层全删；
> world/叙事/深度独立命令（map/events/timeline/relationships/power-system/narrator/fate-system/
> ensemble/legacy/persona-depth/reaction-engine/foreshadowing/anti-slop/brainstorm/template/
> review-book）**折叠进 cluster-save-state（自动维护对应子系统 JSON）+ outline（初始化）**——
> 子系统 JSON 全保留、由 writer 经 build_manifest 消费、流水线自动维护，手动微调走 `/db`。

## 快速开始

1. 启动后选择菜单项，或直接说你想做什么
2. 选择/蒸馏作者风格
3. AI 生成灵感 → 你选择
4. 自动写作（每个故事块后展示走向卡片）
5. 写完后 `/export` 导出全文

## 命令分类

### 核心流程
| 命令 | 功能 |
|------|------|
| `/write` | 写小说完整流程（端到端引导） |
| `/cluster-write` | 写一个故事块（7 步 · v27 freestyle 默认） |
| `/cluster-save-state` | 故事块状态保存（12 步 · 自动维护所有子系统 JSON + 涌现下一 cluster） |
| `/outline` | 生成大纲+初始化 34 子系统数据库（含 AskUser 每卷 cluster 数） |
| `/continue` | 续写/断点恢复 |
| `/export` | 导出全文 |

> 🔴 **v26 起 `/write-chapter` / `/save-state` 单章命令彻底废弃**——cluster mode 是唯一形态。

### 蒸馏系统
| 命令 | 功能 |
|------|------|
| `/distill-style` | 蒸馏作者风格（writer 第一权威 · 生成 Skill 文件） |
| `/distill-character` | 深度角色蒸馏（Voice DNA · 产 voice_pack） |

### 质量保障
| 命令 | 功能 |
|------|------|
| `/check-quality` | 质量+正典+风格三重校验 |
| `/reconcile` | 一致性调和 |

### 工具
| 命令 | 功能 |
|------|------|
| `/db` | 数据库管理（查看/搜索/修复 · 世界/叙事/深度子系统手动微调入口） |
| `/session-start` | 恢复写作会话 |
| `/plan-status` | 查看 plan 强制规划状态 |

> **世界/叙事/深度子系统去哪了？** map/relationships/events/timeline/power-system/narrator/
> fate-system/ensemble/legacy/persona-depth/reaction-engine 等独立命令已删——它们维护的 JSON
> （地图/关系/事件表/时间线/大势卡/角色弧线…）由 `/outline` 初始化、`/cluster-save-state`
> 在每个故事块自动更新、writer 经 build_manifest 自动消费。需手动微调时走 `/db`。

## 核心子系统 JSON（34 个 · 分 9 大类）

### 基础（18 个）

| 类别 | 文件 |
|------|------|
| 人物世界（5） | 人物卡.json · 世界观.json · 世界状态.json · 涟漪规则.json · 角色池.json |
| 叙事（7） | 进度.json · 事件簇.json · 大势卡.json · 故事块摘要.json · 伏笔表.json · 事件表.json · 时间线.json |
| 风格质控（4） | 作者风格.json · 场景规则.json · 写作经验.json · 用户偏好.json |
| 世界演化（2） | 地图.json · 关系.json · 道具.json |

### 高级（16 个 · 允许最小骨架占位但文件必须存在）

Hub/Clock/Storyteller/Stress（4）+ 角色弧线/NPC（3）+ 事件池（2）+ 蒸馏（2）+ 长篇工具（5）

完整字段定义见 `core/claude-home/STRUCTURE.md` · hook `pretooluse_subsystems_gate.py` 强制门禁拦截缺失。

## 项目模板

| 模板 | 适合 | 备注 |
|------|------|------|
| 🗡️ 玄幻修仙 | 修仙/玄幻/仙侠 | 全 34 子系统启用 |
| 💕 都市言情 | 言情/甜宠/虐恋 | 全 34 子系统启用 |
| 🔍 悬疑推理 | 推理/悬疑/惊悚 | 全 34 子系统启用 |
| ⚔️ 战争史诗 | 军事/历史/权谋 | 全 34 子系统启用 |
| 🌟 轻量模式 | 短篇/快速出稿 | `_数据库/.subsystems_bypass.json` 旁路高级 16 |
| 🎭 自定义 | 手动选择 | 自由组合 |

## 写作流程（v26 cluster mode · v27 freestyle 升级）

```
选择风格 → AI生成灵感 → 用户选择
       ↓
/outline (4 步 · 含 AskUser 每卷 cluster 数 · 仅详化 cluster_001)
       ↓
逐故事块循环：
  /cluster-write (7 步 · writer 自由发挥整块 → 双轨质检 → ★最后才 splitter 按字数切)
       ↓
  /cluster-save-state (12 步 · 状态保存 + 涌现下个 cluster brief)
       ↓
  走向卡 (用户选择) → 下一个故事块
       ↓
全书完成 → /export 导出全文
```
