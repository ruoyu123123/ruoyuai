# [DEPRECATED] deepseek_workflow.README.md

本流程文档已废弃，迁移到：

- **新文档**：`core/scripts/gen_workflow.README.md`（含 gen-model 抽象层流程图 + 三件套对照）

## 改造背景

参见 memory `feedback_genmodel_claude_role_split.md`。
核心：所有「DeepSeek 写正文 / 修复」步骤已抽象为 gen-model 调用，可一行命令切换模型。

## 兼容性

- 旧 `deepseek_writer.py` / `deepseek_fixer.py` 保留为 Shim（转发到 gen_*.py + deprecation），计划 2026-07-19 移除
