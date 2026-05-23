---
description: 传承与世界演化系统——角色传承、能力协同、世界自转
---

你是一位世界演化系统专家。请管理以下小说项目的传承与演化系统：

$ARGUMENTS

---

# 传承与世界演化系统

## 一、角色传承系统（借鉴 Wildermyth Legacy）

### 设计哲学

> 角色会老去、退休、死亡——但他们的故事不会消失。
> 下一代继承前人的遗产，站在巨人的肩膀上。

### 适用场景

- 修仙小说：主角闭关百年后出关，世界已变
- 玄幻小说：师父陨落，弟子继承衣钵
- 都市小说：时间跳跃（5年后...）
- 家族小说：几代人的传奇

### 传承数据结构

```json
{
  "legacy_system": {
    "time_skips": [
      {
        "from_ch": 10,
        "to_ch": 11,
        "duration": "百年",
        "world_changes": ["青云宗衰落", "万妖山崛起", "新势力出现"],
        "character_changes": {
          "主角": {"age_change": "+100年（修仙者寿命长）", "power_change": "筑基→金丹"},
          "王经理": {"status": "已故（寿终正寝）", "legacy": "其子继承位置"},
          "小张": {"status": "已成长为中层管理", "personality_shift": "从天真变为老练"}
        }
      }
    ],
    "character_legacies": [
      {
        "original": "师父·玄清真人",
        "status": "陨落于第8章",
        "inheritor": "主角",
        "inherited": {
          "abilities": ["青莲剑法（残缺）"],
          "items": ["青莲剑"],
          "knowledge": ["宗门秘辛"],
          "personality_influence": "主角变得更沉稳",
          "unfinished_business": ["师父未完成的使命"]
        },
        "narrative_weight": "师父的遗志成为主角的核心驱动力"
      }
    ],
    "generational_themes": {
      "第一代": "开拓/生存",
      "第二代": "守成/发展",
      "第三代": "变革/超越"
    }
  }
}
```

### 时间跳跃处理

当故事需要时间跳跃时：
1. 记录跳跃前的世界状态快照
2. 根据各角色的性格/目标/势力，推演跳跃期间的变化
3. 更新所有数据库（人物卡/地图/势力/关系/时间线）
4. 生成"时间跳跃摘要"供下一章使用

---

## 二、能力协同系统（借鉴 Slay the Spire）

### 设计哲学

> 1+1>2。单个能力平平无奇，组合使用时产生远超预期的效果。
> 这让战斗/冲突场景充满创意和惊喜。

### 协同数据结构

存储在 `_数据库/人物卡.json` 的 abilities 扩展中：

```json
{
  "abilities": [
    {"name": "焚天诀", "element": "火", "type": "攻击", "level": 3},
    {"name": "寒冰掌", "element": "冰", "type": "攻击", "level": 2}
  ],
  "synergies": [
    {
      "combo": ["焚天诀", "寒冰掌"],
      "effect": "冰火两重天——极热极寒交替，对手防御崩溃",
      "power_multiplier": 2.5,
      "discovery_ch": null,
      "status": "未发现"
    },
    {
      "combo": ["青莲剑法", "御剑术"],
      "effect": "万剑归宗——剑阵覆盖范围扩大十倍",
      "power_multiplier": 3.0,
      "discovery_ch": null,
      "status": "未发现"
    }
  ],
  "item_synergies": [
    {
      "items": ["赤焰剑", "焚天诀"],
      "effect": "剑体共鸣——火属性伤害翻倍",
      "discovery_condition": "在极端愤怒时使用"
    }
  ]
}
```

### 协同发现机制

- 协同不是一开始就知道的——需要在特定条件下"发现"
- 发现协同是重要的爽点（"原来这两个能力可以这样用！"）
- 叙事导演控制发现时机（在最需要的时候发现，最有戏剧性）

### 写作集成

- 战斗场景写前：注入角色已知的协同组合
- 关键战斗：叙事导演可以安排"发现新协同"作为转折点
- save-state：如本章发现了新协同，更新 synergies 状态

---

## 三、世界自转系统（借鉴 Kenshi）

### 设计哲学

> 世界不围着主角转。主角不在的时候，世界也在运转。
> NPC 有自己的生活，势力有自己的兴衰，事件有自己的发展。

### 世界自转规则

每章 save-state 时，除了更新主角相关的状态，还要推演"主角不在场时世界发生了什么"：

```json
{
  "world_autonomy": {
    "background_events": [
      {
        "ch": 5,
        "event": "万妖山偷袭了天机阁的商队",
        "protagonist_aware": false,
        "consequences": ["天机阁物资短缺", "两派关系恶化"],
        "will_affect_protagonist_at": 7
      }
    ],
    "npc_life_events": [
      {
        "character": "前台小张",
        "ch": 5,
        "event": "私下拜师学艺",
        "protagonist_aware": false,
        "visible_change": "下次见面时气质有变化"
      }
    ],
    "faction_dynamics": [
      {
        "ch_range": [3, 8],
        "dynamic": "青云宗内部权力斗争加剧",
        "protagonist_involvement": "低（主角在闭关）",
        "outcome_if_no_intervention": "保守派获胜，改革派被边缘化"
      }
    ]
  }
}
```

### 世界自转的写作效果

1. **主角回归时的"陌生感"**：闭关/离开一段时间后回来，发现世界变了
2. **错过的机会**：因为主角不在场，某些事件的结果不是最优的
3. **NPC 的独立成长**：配角不是"等着主角来推动"，他们有自己的故事
4. **势力消长**：即使主角不参与，势力之间的博弈也在继续

### 写作集成

- **outline 初始化**：为每个"主角不在场"的时间段预设世界自转事件
- **save-state**：每章推演背景事件的进展
- **write-chapter 写前**：如有背景事件的后果在本章显现，注入相关信息
- **叙事导演**：控制"主角何时发现世界变化"的时机

---

## 操作

- `/legacy 时间跳跃 [时长]` — 执行时间跳跃，推演世界变化
- `/legacy 传承 [角色A] [角色B]` — 记录角色A向角色B的传承
- `/legacy 协同 查看` — 显示所有已知/未知的能力协同
- `/legacy 世界自转 [章节范围]` — 推演指定章节范围内的世界背景变化

---

**目标：让故事有"时间的重量"——角色会老去，能力会组合，世界会自转。不是所有事都等着主角去做。**
