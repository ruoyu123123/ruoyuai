---
description: 管理条件触发的剧情事件
---

你是一位事件系统管理专家。请管理以下小说项目的事件触发器：

$ARGUMENTS

---

# 事件触发器系统

## 设计哲学

借鉴 AnyZork 的确定性事件系统：
- **AI 负责叙事**，事件系统负责"什么时候发生什么"
- 事件有明确的触发条件，满足条件时必须触发
- 避免 AI 随意编造事件，保证剧情逻辑性

## 数据结构

`_数据库/事件表.json` 格式：

```json
{
  "pending_events": [
    {
      "id": "发现密室",
      "trigger": {
        "type": "condition",
        "conditions": [
          {"field": "location", "operator": "==", "value": "藏经阁"},
          {"field": "time", "operator": ">=", "value": "第3章"},
          {"field": "item", "operator": "has", "value": "古钥匙"}
        ],
        "logic": "AND"
      },
      "description": "主角在藏经阁发现隐藏的密室入口",
      "consequences": ["获得功法残页", "触发新伏笔：密室主人是谁"],
      "priority": 8,
      "created_at_ch": 1,
      "status": "pending"
    },
    {
      "id": "王经理摊牌",
      "trigger": {
        "type": "relationship",
        "conditions": [
          {"from": "王经理", "to": "主角", "dimension": "trust", "operator": "<", "value": -30}
        ]
      },
      "description": "王经理对主角的不信任达到临界点，当面质问",
      "consequences": ["关系破裂或修复（取决于主角回应）"],
      "priority": 9,
      "created_at_ch": 2,
      "status": "pending"
    }
  ],
  "triggered_events": [],
  "recurring_events": [
    {
      "id": "月考",
      "trigger": {"type": "interval", "every_n_chapters": 3},
      "description": "宗门月考，排名变化影响资源分配",
      "status": "active"
    }
  ]
}
```

## 触发类型

| 类型 | 说明 | 示例 |
|------|------|------|
| condition | 条件满足时触发 | 到达某地+拥有某物 |
| relationship | 关系值达到阈值 | trust < -30 |
| time | 到达指定章节 | 第5章开始时 |
| interval | 每N章循环触发 | 每3章一次月考 |
| chain | 前置事件完成后触发 | 发现密室→解读残页 |
| clock | 威胁时钟满格时触发 | 魔族入侵时钟6/6→大军突破 |

## 威胁时钟系统（v16 新增 · 移植自外部工艺库 TRPG 设计）

在 `_数据库/事件表.json` 中新增 `threat_clocks` 字段：

```json
{
  "threat_clocks": [
    {
      "id": "clock_001",
      "name": "势力入侵",
      "segments": 6,
      "filled": 2,
      "advance_conditions": ["每当主角远离时+1", "每当线人传递情报时+1"],
      "trigger_event": "大军突破防线",
      "visible_to_reader": true,
      "visible_to_protagonist": false,
      "created_ch": 5,
      "last_advanced_ch": 12
    }
  ]
}
```

**时钟规则：**
- 每章 save-state 检查活跃时钟，本章事件匹配 advance_conditions → filled += 1
- filled >= segments → 触发 trigger_event
- visible_to_reader 但 !visible_to_protagonist → 产生信息差（读者紧张角色不知）
- 时钟进度传给叙事导演影响事件强度

## 写作集成

- **write-chapter 写前**：检查所有 pending_events，如有满足触发条件的事件，注入到本章必须发生的事件列表
- **save-state 写后**：检查本章是否触发了事件，更新状态为 triggered
- **outline 初始化**：从大纲中提取关键事件，创建初始事件表

## 操作

- `/events 查看` — 显示所有待触发事件
- `/events 添加` — 手动添加新事件
- `/events 检查` — 检查当前哪些事件满足触发条件

---

**目标：让剧情有"因果链"——埋下的种子在条件成熟时必然开花**
