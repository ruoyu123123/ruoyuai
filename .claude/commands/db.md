---
description: 只读查看和定位小说项目数据库状态
---

你是一位数据库状态审计员。请只读查看以下小说项目的数据库：

$ARGUMENTS

---

# 数据库只读诊断

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

### 定位
- `/db 定位 [关键词]` — 定位相关 JSON 文件、字段路径和引用位置
- `/db 差异` — 只读汇总最近一次 `/cluster-save-state` 写入后的数据库变化

### 导出
- `/db 导出` — 将所有数据库文件打包为一个可读的 Markdown 报告
- `/db 导出 角色` — 导出所有角色的完整档案（含 Voice DNA）

## 写入边界

`/db` 不写 `_数据库/`。它只负责观察、搜索、定位和导出。

设定或事实需要变化时：

1. 当前 cluster 尚未保存：回到 `章节/cluster_<key>_draft/cluster_<key>_draft.txt` 修正草稿，再重跑 `/cluster-write`。
2. cluster 已保存：从后续 cluster 承接变化，由 `/cluster-save-state` 的 archivist / foreshadower / apply 脚本统一回库。
3. JSON 结构异常：停止写作链路，回到产生该结构的 required plan step 重新产出并校验；`/db` 只定位异常文件和字段路径。

---

## 数据库文件清单

| 文件 | 用途 | 关键字段 |
|------|------|----------|
| 人物卡.json | 角色档案 | voice_pack, locked_facts, knowledge, growth_arc |
| 世界观.json | 设定+关键词触发 | entries, keywords, priority |
| 伏笔表.json | 伏笔追踪 | setup_cluster, due_by, status(open/suspended/consumed), owner, payoff_scope |
| 故事块摘要.json | 故事块摘要 | summary, emotion_value, anchors |
| 进度.json | 写作进度 | cluster_blueprint, completed, current |
| 场景规则.json | 场景写作规则 | scene_types |
| 写作经验.json | Learning Loop | success_patterns, failure_patterns |
| 用户偏好.json | 用户口味 | style/content/workflow preferences |
| 作者风格.json | 蒸馏的风格 | style_profile, writing_rules |
| 地图.json | 世界地图 | locations, character_positions |

---

**目标：让用户随时了解项目状态，快速定位问题；不形成第二条写库链路。**
