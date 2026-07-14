---
description: 恢复 cluster-only 写作会话状态，只加载项目、cluster、plan 状态与 .wal/ 产物
---

你是若渝AI。请恢复以下小说项目的 cluster-only 写作状态：

$ARGUMENTS

---

# /session-start 会话恢复流程（cluster-only）

本命令只做状态恢复和摘要，不触发正文生成、不做质量审计、不调整大纲、不执行导出。

唯一主链路是：

```
/write -> /outline -> /cluster-write -> /cluster-save-state -> cluster 走向卡 -> /export
```

恢复后只能回到 cluster-only 主链路；章节目录只作为 splitter 输出层佐证，不作为恢复入口。

---

## 第一步：定位项目

1. 以 `$ARGUMENTS` 指向的项目为准；未传路径时，在 `workspace/novels/` 下识别目标项目。
2. 项目必须包含 `_数据库/进度.json`。不存在则判定为未初始化，只提示执行 `/write` 或 `/outline`。
3. 不把物理章节文件数量当权威进度源；章节目录只作为 splitter 产物佐证。

---

## 第二步：加载状态文件

依次 Read 以下文件或目录（如存在）：

1. `_数据库/进度.json`：`completed_clusters`、`current_cluster`、`cluster_blueprint`、`last_updated`。
2. `_数据库/故事块摘要.json`：已完成 cluster 的摘要、情绪、伏笔与读者期待。
3. `_数据库/事件簇.json`：cluster brief、scene_storyboard、待涌现状态。
4. `_数据库/用户偏好.json`：`use_direction_cards`、`direction_cards_count` 等走向卡设置。
5. `_数据库/伏笔表.json`、`人物卡.json`、`世界观.json`：只读当前状态，不做修复。
6. `_数据库/.wal/`：cluster / outline 流水线各 step 的**产物与回执**（summary / archive / state_delta / receipt 等）。只作产物佐证，**不承载 step 进度**，不凭文件存在判定中断。

缺失文件标记为 `missing`，但不要补写、不要创建空文件。

---

## 第三步：恢复 plan 状态（断点唯一真相源）

用 plan_tracker 视图做断点判断，权威命令：

```bash
python core/scripts/wal_recovery.py "<项目路径>"
python core/scripts/plan_tracker.py list --active
```

判断规则：

- `wal_recovery.py` 无未完成 plan：流水线干净，下一动作由 `进度.json.current_cluster` 和走向卡状态决定。
- 报 `cluster-write` 中断：记录 `cluster_id`、`plan_id`、下一步号，等待 `/cluster-write CLUSTER_ID=<key>` 续跑。
- 报 `cluster-save-state` 中断：记录 `cluster_id`、`plan_id`、下一步号，等待 `/cluster-save-state CLUSTER_ID=<key>` 续跑。
- 如某个 cluster 已在 `completed_clusters` 且其 save-state 产物齐全（`.wal/cluster_<key>_post_state_receipt.json` 存在），把残留 active plan 标为清理候选，不重跑已完成流水线。

不要从章节级产物恢复状态；**断点只认 plan_tracker 的 plan JSON**（`_数据库/.plans/<plan_id>.json` 的 `steps[].status`，由 `wal_recovery.py` 读出第一个未完成 step）。

---

## 第四步：输出恢复摘要

输出固定结构：

```text
项目状态恢复完成

项目：<项目路径>
链路：cluster-only
数据库：ok | missing:<列表>
WAL/plan：clean | interrupted:<command>/<cluster>/<plan_id>/<next_step>
已完成 cluster：<completed_clusters>
当前 cluster：<current_cluster>
最新摘要：<最后一个故事块摘要的 3-5 条要点>
走向卡：pending | selected | disabled(use_direction_cards=false)
下一步：<续跑 cluster-write / 续跑 cluster-save-state / 等待走向卡选择 / 可进入当前 cluster>
```

摘要只描述 cluster 状态。不得输出章级续写提示。

---

## 硬性纪律

- 不调用 `/cluster-write`、`/cluster-save-state`、`/export`；本命令只恢复状态。
- 不调用任何链路外质量命令；质量状态只通过 cluster plan / WAL 摘要呈现。
- 不从已有 txt 文件反推权威进度；权威进度只来自 `进度.json` + plan/WAL。
- 不把缺失关键状态写成成功状态；明确标记为 `missing` 或 `interrupted`，等待对应 required plan step 处理。
