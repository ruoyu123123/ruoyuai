---
description: 项目进度仪表盘
---

# 📊 项目进度仪表盘

$ARGUMENTS

## 何时调用

- 用户问「我写到哪了」「下一个 beat 是什么」「主角 stress 怎样」
- 用户输入「/dashboard」「进度」「状态」「面板」
- 多日没写后回来想看全景
- 决定是否启动「精雕模式」前看看当前压力

## 执行

```bash
python core/scripts/project_dashboard.py <项目路径>
```

输出包含：
- **总进度**：已写 N 章 / 总目标 + 进度条
- **当前卷 / 当前 beat**：基于 beat_map.json
- **距离下个关键 beat**：Midpoint / All Is Lost / Finale 等
- **写作统计**：近 5 章字数 + 平均 + 与目标对比
- **角色弧光**：每核心角色当前 stage
- **主角 Stress**：当前值 + 距离 mental_break 阈值
- **Active Clocks**：urgent / approaching / normal 分级
- **Fate Events**：已完成 / 待激活 + top 5
- **Factions 数值**：5 大势力 P/S/W
- **用户偏好状态**：是否跑过 /wizard

## 输出后

如发现：
- ⚠️ 多个 urgent clocks → 提示「下章必埋暗示」
- ⚠️ stress 接近 break → 提示「下章可能触发 mental_break」
- ⚠️ 未跑 /wizard → 建议「跑 /wizard 配置偏好」
- ⚠️ 字数远偏目标 → 建议调整
