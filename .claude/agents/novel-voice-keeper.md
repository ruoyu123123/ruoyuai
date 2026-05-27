---
name: novel-voice-keeper
description: "[DEPRECATED 2026-05-19] 已拆分为 novel-voice-checker (检查) + gen_fixer.py --mode voice-fix (生成修复)。本 stub 仅作 spawn 兼容期保留，主代理应改 spawn novel-voice-checker 后调 gen_fixer。计划移除：2026-07-19"
tools: Read
---

# [DEPRECATED] novel-voice-keeper

本 agent 已于 **2026-05-19** 拆分为：

| 新组件 | 路径 | 职责 |
|---|---|---|
| `novel-voice-checker` | `.claude/agents/novel-voice-checker.md` | 扫描章节对话 + 比对人物卡 voice_pack + 输出 voice fix brief JSON（**只检查，不改对话**） |
| `gen_fixer.py --mode voice-fix` | `core/scripts/gen_fixer.py` | 读 brief JSON → 调当前 active gen-model profile → 按 fix_hint 精确改对话（**只生成，不判断**） |

## 为什么拆分

用户系统级偏好（2026-05-19 拍板）：
- **内容生成**（含修对话）→ gen-model
- **检查 / 判断 / 裁决** → Claude

原 voice-keeper 同时做「检查 + Edit 改对话」，混淆了角色。拆分后：
- Claude 做擅长的判断（哪些对话偏离 voice_pack、违规类型 voice_drift/tone_inconsistency/pov_violation、修复方向）
- gen-model 做擅长的生成（按 fix_hint 写出符合 voice_pack 的对话替换）

## 主代理新调用流程

替代原本 `spawn novel-voice-keeper`：

1. `spawn novel-voice-checker`（同样的 prompt 契约：PROJECT/CHAPTER/MODE/FIX_BRIEF）
2. checker 返回 brief_path
3. 主代理跑：
   ```bash
   python core/scripts/gen_fixer.py \\
     --project <PROJECT> \\
     --mode voice-fix \\
     --brief <brief_path>
   ```
4. gen_fixer 输出 fixer_report，主代理接 audit_hub / cluster-save-state

## 移除计划

- 计划移除日期：**2026-07-19**（约 2 月兼容期）
- 移除前本文件仅作占位防止 spawn 失败

## 历史文档

完整的 voice_pack 比对规则 / 跨章一致性检查 / catchphrase_overuse 阈值等已迁到 `novel-voice-checker.md`。
