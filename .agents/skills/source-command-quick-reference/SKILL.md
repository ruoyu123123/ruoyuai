---
name: "source-command-quick-reference"
description: "slash command 快速参考（文档非命令）"
---

# source-command-quick-reference

Use this skill when the user asks to run the migrated source command `QUICK_REFERENCE`.

## Command Template

# 快速参考

## 从零写书

```text
/write <题材或想法>
```

`/write` 会完成风格选择或蒸馏、灵感卡、`/outline`、第一个 `/cluster-write` 和 `/cluster-save-state`。之后每个故事块结束只停在走向卡选择。

新增成熟功能或模型时，只能落成这条链路里的 required plan step 或 required 子步骤。

## 单独执行链路步骤

| 场景 | 命令 |
|---|---|
| 生成大纲和数据库 | `/outline <故事描述>` |
| 写当前故事块 | `/cluster-write CLUSTER_ID=<cluster_key>` |
| 保存当前故事块状态 | `/cluster-save-state CLUSTER_ID=<cluster_key>` |
| 恢复中断 | `/continue` |
| 导出全文 | `/export` |

## 风格与角色

| 场景 | 命令 |
|---|---|
| 蒸馏作者风格 | `/distill-style <参考文本或路径>` |
| 蒸馏角色声音和行为 | `/distill-character <角色名>` |

## 维护

| 场景 | 命令 |
|---|---|
| 查数据库、搜索字段或导出状态报告 | `/db <操作>` |

质量审计在 `/cluster-write` 内完成，状态一致性在 `/cluster-save-state` 内完成，成品完整性在 `/export` 前硬校验。

## 验证

```bash
py -m pytest
```

验证入口只使用仓库根 `pytest.ini`。
