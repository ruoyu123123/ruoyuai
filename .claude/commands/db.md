---
description: 查看和管理小说项目数据库
---

你是一位数据库管理专家。请管理以下小说项目的数据库：

$ARGUMENTS

---

# 数据库管理

## 可用操作

### 查看状态
- `/db 状态` — 显示所有数据库文件的概览（文件大小、条目数、最后更新时间）
- `/db 角色` — 列出所有角色及其当前状态
- `/db 伏笔` — 列出所有伏笔及其回收状态
- `/db 进度` — 显示写作进度和 cluster_blueprint
- `/db 经验` — 显示写作经验库内容
- `/db 风格` — 显示当前使用的作者风格摘要
- `/db 地图` — 显示世界地图概览
- `/db 偏好` — 显示用户偏好

### 搜索
- `/db 搜索 [关键词]` — 在所有数据库文件中搜索关键词
- `/db 角色 [名字]` — 查看指定角色的完整档案
- `/db 伏笔 [状态]` — 筛选伏笔（未回收/已回收/到期）

### 修复
- `/db 修复` — 检查所有 JSON 文件的完整性，修复格式错误
- `/db 重建 [文件名]` — 从已写章节中重建指定数据库文件
- `/db 清理` — 清理过期数据（低置信度经验、已完结的幕后行动等）

> 🔴 **2026-06-27 W5 · 手改后必须重校验**：你（或用户）**手动改了任何一个 `_数据库/*.json` 子系统文件**后，
> 在保存收尾前**必须**对被改文件跑一次契约重校验——否则破坏契约的手改会直接被 `build_manifest` 消费导致写作穿帮
> （C03/C17 门只在 `/outline`、`/cluster-save-state` 触发，**不覆盖 `/db` 之后的手改**）：
>
> ```bash
> python core/scripts/db_schema_validate.py "<项目路径>" --post-edit "<项目路径>/_数据库/<被改文件>.json"
> ```
>
> - exit 0 → 手改未破坏 schema 契约，可安全继续。
> - exit 2 → 手改破坏了契约（结构 TYPE_MISMATCH / 大势卡 ME 池引用断裂 / 涟漪规则·事件簇 载荷被清空），
>   终端会打印**修复 hint**；按 hint 修正或从 `_数据库/_backup/db_schema/` 取最近备份还原后再保存。
> - 只查 **schema 契约**（结构 / 类型 / 载荷非空），**绝不**评判内容质量；合法的稀疏（fluid 起步、空角色列表）不会误拦。

### 导出
- `/db 导出` — 将所有数据库文件打包为一个可读的 Markdown 报告
- `/db 导出 角色` — 导出所有角色的完整档案（含 Voice DNA）

---

## 数据库文件清单

| 文件 | 用途 | 关键字段 |
|------|------|----------|
| 人物卡.json | 角色档案 | voice_pack, locked_facts, knowledge, growth_arc |
| 世界观.json | 设定+关键词触发 | entries, keywords, priority |
| 伏笔表.json | 伏笔追踪 | planted_ch, due_by, resolved |
| 故事块摘要.json | 章节摘要 | summary, emotion_value, anchors |
| 进度.json | 写作进度 | cluster_blueprint, completed, current |
| 场景规则.json | 场景写作规则 | scene_types |
| 写作经验.json | Learning Loop | success_patterns, failure_patterns |
| 用户偏好.json | 用户口味 | style/content/workflow preferences |
| 作者风格.json | 蒸馏的风格 | style_profile, writing_rules |
| 地图.json | 世界地图 | locations, character_positions |

---

**目标：让用户随时了解项目状态，快速定位问题**
