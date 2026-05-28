---
description: 若渝AI 命令系统总览（文档非命令）
---

# 若渝AI 命令系统

## 系统概览

若渝AI 是一个模块化的 AI 小说写作系统，包含 34 个 Skill 命令、34 个核心子系统 JSON。
v26 起写作单位从「章节」升级为「故事块（cluster）」，v27 起 writer 自由发挥 + splitter 按字数切。

## 快速开始

1. 启动后选择菜单项，或直接说你想做什么
2. 选择/蒸馏作者风格
3. AI 生成灵感 → 你选择
4. 自动写作（每个故事块后展示走向卡片）
5. 写完后全书复盘

## 命令分类

### 核心流程
| 命令 | 功能 |
|------|------|
| `/write` | 写小说完整流程 |
| `/script` | 写短剧剧本 |
| `/cluster-write` | 写一个故事块（7 步 · v27 freestyle 默认） |
| `/cluster-save-state` | 故事块状态保存（12 步流水线） |
| `/outline` | 生成大纲+初始化数据库（含 AskUser 每卷 cluster 数） |
| `/continue` | 续写/断点恢复 |
| `/template` | 选择项目模板 |

> 🔴 **v26 起 `/write-chapter` / `/save-state` 单章命令彻底废弃**——cluster mode 是唯一形态。

### 蒸馏系统
| 命令 | 功能 |
|------|------|
| `/distill-style` | 蒸馏作者风格（生成Skill文件） |
| `/distill-character` | 深度角色蒸馏（Voice DNA） |

### 质量保障
| 命令 | 功能 |
|------|------|
| `/check-quality` | 质量+正典+风格三重校验 |
| `/foreshadowing` | 契诃夫之枪引擎 |
| `/review-book` | 全书复盘 |

### 世界系统
| 命令 | 功能 |
|------|------|
| `/map` | 地图/空间管理 |
| `/relationships` | 角色关系（4维数值） |
| `/events` | 事件触发器（5种类型） |
| `/timeline` | 时间系统（世界时钟+NPC日程） |
| `/power-system` | 势力/成长系统 |

### 叙事引擎
| 命令 | 功能 |
|------|------|
| `/narrator` | 叙事导演（节奏调控+NPC计划+涌现规则） |
| `/fate-system` | 天道大势（大势/小势/因果/气运） |
| `/ensemble` | 群戏引擎（多角色自主对话） |
| `/legacy` | 传承/能力协同/世界自转 |

### 角色深度
| 命令 | 功能 |
|------|------|
| `/persona-depth` | 人格双层+认知框架+态度着色 |
| `/reaction-engine` | 角色反应引擎（BG3+Hades+太吾） |

### 工具
| 命令 | 功能 |
|------|------|
| `/db` | 数据库管理（查看/搜索/修复） |
| `/session-start` | 恢复写作会话 |
| `/brainstorm` | 生成故事灵感 |

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
全书完成 → /review-book 复盘
```
