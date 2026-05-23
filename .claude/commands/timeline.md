---
description: 管理故事世界的时间流逝和NPC日程
---

你是一位时间线管理专家。请管理以下小说项目的时间系统：

$ARGUMENTS

---

# 时间系统

## 设计哲学

借鉴 Chasm Engine 的世界时钟：
- 故事世界有自己的时间流逝
- NPC 有独立日程，不是只在主角面前才"存在"
- 时间推进会触发事件、改变世界状态

## 数据结构

`_数据库/时间线.json` 格式：

```json
{
  "current_time": {
    "day": 5,
    "period": "午后",
    "chapter": 5,
    "season": "秋"
  },
  "time_per_chapter": "约半天（可变）",
  "npc_schedules": {
    "王经理": {
      "morning": "办公室处理公务",
      "afternoon": "巡视各部门",
      "evening": "书房独处",
      "night": "休息"
    },
    "前台小张": {
      "morning": "前台值班",
      "afternoon": "前台值班",
      "evening": "宿舍",
      "night": "休息"
    }
  },
  "world_clock_events": [
    {"day": 7, "period": "morning", "event": "宗门大比开始", "affects": ["所有角色集中在比武场"]},
    {"day": 10, "period": "night", "event": "月食之夜", "affects": ["灵气紊乱，修炼效果翻倍但有风险"]}
  ],
  "time_log": [
    {"ch": 1, "time_start": {"day": 1, "period": "morning"}, "time_end": {"day": 1, "period": "evening"}, "duration": "一天"}
  ]
}
```

## 时间对写作的影响

1. **NPC 可用性**：根据日程，某些 NPC 在特定时间不在某地
   - 写前检查：如果主角要找王经理，但当前是晚上，王经理在书房不在办公室
2. **世界事件**：到达指定时间点时，世界事件自动触发
3. **氛围变化**：不同时间段有不同的环境氛围（清晨雾气/正午烈日/黄昏余晖/深夜寂静）
4. **时间压力**：某些任务有截止时间（"必须在月考前完成"）

## 写作集成

- **write-chapter 写前**：注入当前时间、NPC 当前位置（根据日程）、即将到来的世界事件
- **save-state 写后**：推进时间，更新 time_log
- **outline 初始化**：设定时间流速、NPC 日程、关键世界事件时间点

## 操作

- `/timeline 查看` — 显示当前时间和即将到来的事件
- `/timeline 推进 [时间量]` — 手动推进时间
- `/timeline [角色] 在哪` — 根据当前时间查询角色位置

---

**目标：让故事世界有"时间感"——不是所有事都等着主角去触发**
