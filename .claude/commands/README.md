---
description: 若渝AI 命令索引（文档非命令）
---

# 若渝AI 命令索引

本目录只定义 slash command 的职责边界。**唯一创作链路以仓库根 README 为准**：`/write -> /outline -> /cluster-write -> /cluster-save-state -> 走向卡 -> /export`。

成熟功能、模型或论文/开源机制可以进入创作系统，但必须落成这条链路里的 required plan step 或 required 子步骤，并同步命令文档、agent 合约和验收口径。

## 核心链路

| 命令 | 职责 |
|---|---|
| `/write` | 端到端创作入口：风格、灵感、大纲、故事块循环 |
| `/outline` | 初始化项目和 34 个核心子系统，生成卷级大势，只详化首个 cluster |
| `/cluster-write` | 写一个故事块：manifest、整块草稿、cluster 级审核、切章、标题 |
| `/cluster-save-state` | 保存故事块状态，回写账本，涌现下一 cluster 候选 |
| `/continue` | 从 plan_tracker 的 plan JSON（唯一断点真相源）恢复中断步骤 |
| `/export` | 拼接并导出全文 |

## 蒸馏与校准

| 命令 | 职责 |
|---|---|
| `/distill-style` | 蒸馏作者风格，生成作者风格档和 skill |
| `/distill-character` | 蒸馏角色 voice、行为模式和反应倾向 |

## 维护

| 命令 | 职责 |
|---|---|
| `/db` | 只读查看、搜索、定位或导出数据库状态 |
| `/session-start` | 恢复写作会话上下文 |
| `/plan-status` | 查看 plan_tracker 状态 |

质量审计和状态一致性职责属于主链路 required step：写作中由 `/cluster-write` 的 cluster 审计和 `/cluster-save-state` 的状态回库承担，成品前由 `/export` 硬校验承担。

## 子系统归属

世界、叙事、深度角色、伏笔、时间线、关系、地图、命运、反应引擎等子系统没有独立创作命令。它们由：

- `/outline` 初始化骨架；
- `/cluster-write` 通过 manifest 注入给 writer 和 scanner；
- `/cluster-save-state` 在每个故事块后统一回写；
- `/db` 提供只读观察、搜索、定位和导出入口。

## 验证入口

在仓库根目录执行：

```bash
py -m pytest
```

不要把 `tests/` 子目录配置或旧 runner 当成官方验证入口。
