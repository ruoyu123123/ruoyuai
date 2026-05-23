---
name: novel-validator-repair
description: "[DEPRECATED 2026-05-19] 已拆分为 novel-validator-checker (检查) + gen_fixer.py --mode validator-repair (生成修复)。本 stub 仅作 spawn 兼容期保留，主代理应改 spawn novel-validator-checker 后调 gen_fixer。计划移除：2026-07-19"
tools: Read
---

# [DEPRECATED] novel-validator-repair

本 agent 已于 **2026-05-19** 拆分为：

| 新组件 | 路径 | 职责 |
|---|---|---|
| `novel-validator-checker` | `.claude/agents/novel-validator-checker.md` | 跑 validate 工具 + 独立 style_directive 复核 + 输出 repair brief JSON（**只检查，不改文**） |
| `gen_fixer.py --mode validator-repair` | `core/scripts/gen_fixer.py` | 读 brief JSON → 调当前 active gen-model profile → 按 line_start/line_end 精确替换章节文件（**只生成，不判断**） |

## 为什么拆分

用户系统级偏好（2026-05-19 拍板）：
- **内容生成**（含修违规段落）→ gen-model（当前选择的生成模型，可一行切换）
- **检查 / 判断 / 裁决** → Claude（主代理 + sub-agent）

原 validator-repair 同时做「检查 + 评分 + Edit 修复」，违反了上述分工。拆分后：
- Claude 做擅长的判断（哪些是违规、违规类型、修复方向 fix_hint）
- gen-model 做擅长的生成（按 fix_hint 写出修复后的段落正文）

## 主代理新调用流程

替代原本 `spawn novel-validator-repair`：

1. `spawn novel-validator-checker`（同样的 prompt 契约：PROJECT/CHAPTER/MODE）
2. checker 返回 brief_path
3. 主代理跑：
   ```bash
   python core/scripts/gen_fixer.py \\
     --project <PROJECT> \\
     --mode validator-repair \\
     --brief <brief_path>
   ```
4. gen_fixer 输出 fixer_report，主代理读取后续接 audit_hub / save-state

## 移除计划

- 计划移除日期：**2026-07-19**（约 2 月兼容期）
- 移除后所有 `spawn novel-validator-repair` 调用必须先迁移到上述新流程
- 移除前本文件仅作占位防止 spawn 失败

## 历史文档

完整的 v19 顾问制规则 / 16 维评分纲领 / hard_gate 清单等内容已迁到 `novel-validator-checker.md`。需要查阅请去新 agent 文档。
