# [DEPRECATED] deepseek_writer.README.md

本文档已废弃，迁移到：

- **新脚本**：`core/scripts/gen_writer.py`（OpenAI 兼容协议 + 多 profile + fallback 链）
- **新文档**：`core/scripts/gen_writer.README.md`
- **配置**：`.env` 用 `GEN_MODEL_ACTIVE` + `GEN__<name>__*` 多 profile（不再用 `DEEPSEEK_*`）
- **管理 CLI**：`python core/scripts/gen_model.py list / switch / show / add`

## 兼容性

- 旧路径 `python core/scripts/deepseek_writer.py ...` 仍可用（Shim 转发到 gen_writer.py + deprecation 警告）
- 计划移除：**2026-07-19**

## 改造背景

参见 lesson `feedback_genmodel_claude_role_split.md`（gen-model 与 Claude 角色分工设计）。

核心：从绑死 DeepSeek 改为「**当前选择的生成模型**（gen-model）」抽象层，profile 完全通用模板化，可一行命令切换 DeepSeek/Kimi/GLM/Qwen/...
