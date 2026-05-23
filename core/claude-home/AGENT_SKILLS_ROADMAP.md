# Agent Skills 共享路线图（v21 P8.2）

## 背景

Anthropic 2026 推出 **Agent Skills** 开放标准：
- SKILL.md 元数据 + 脚本/工作流自包含
- skills.sh 注册中心（Vercel + Anthropic 联建，2026 年初上线）
- 标准被 Cursor / Copilot / Claude Code 同步采纳——形成 npm/pip-like 生态

## 我们可共享的 18 个 SKILL 候选

### 文本质量类（10 个）
1. **chinese-anti-slop**：中文 AI 写作禁词 + 句法 cliche 检测（cross_chapter_pattern_scan 20+ 维度）
2. **chinese-paragraph-rhythm**：段落主语去重 + dialogue tag 多样性 + body reactions 控制
3. **dcas-dual-chapter-split**：双章合一 + 自然截断（DCAS pipeline）
4. **idiom-cooldown**：成语/口头禅冷却期机制
5. **chapter-continuity-check**：cliffhanger 衔接 + 时间跳跃 + 物件持续 + 情绪桥接
6. **persona-drift-embedding**：基于 embedding 的角色发声漂移检测
7. **over-confidence-detector**：过度自信词（hallucination 红旗）
8. **manifest-compress**：LLMLingua 风格 JSON 压缩
9. **manifest-context-rot-check**：Context Rot 防御
10. **input-sanitizer**：prompt injection 防御

### 叙事系统类（5 个）
11. **clock-engine**：Citizen Sleeper 式显式 Clock 进度
12. **fate-engine**：鬼谷八荒式大势池 + lazy spawn
13. **world-evolution-engine**：factions ripple + NPC threads + emergent_opps
14. **storyteller-adaptation**：RimWorld 式 storyteller 风格 + adaptation factor
15. **save-the-cat-beat-map**：15 节拍跨章映射 + compliance scan

### 评估类（3 个）
16. **judge-consensus**：多 judge 异质 persona 投票 + meta-judge 校准
17. **audit-hub-waiver**：advisory/hard_gate 双轨 + 自动豁免
18. **cross-chapter-scan-suite**：18 维度跨章扫描器矩阵

## SKILL.md 标准格式

```markdown
---
name: chinese-anti-slop
version: 1.0.0
description: 中文 AI 写作反套话扫描器（禁词 + cliche + 句法）
keywords: [chinese, anti-slop, writing-quality, ai-writing]
author: 若渝AI
license: MIT
---

# 中文 Anti-Slop Scanner

## 何时使用
你正在写或审查中文 AI 生成的文章/小说。

## 如何调用
[包含的脚本/工作流说明]

## 输出格式
[预期产出]

## 调优建议
[参数推荐]
```

## 实施分阶段

### Phase 1（quick win）—— 1 SKILL 试水
选择 **chinese-anti-slop**（最有差异化价值）：
- 拷贝 `cross_chapter_pattern_scan.py` 到 `skills/chinese-anti-slop/`
- 编写 SKILL.md 元数据
- 测试 Cursor / Claude Code 中加载效果
- 发布到 skills.sh

### Phase 2（中期）—— 5 个核心 SKILL
- chinese-anti-slop / dcas-dual-chapter-split / chapter-continuity-check
- judge-consensus / cross-chapter-scan-suite
- 形成 "Chinese fiction writing skill bundle"

### Phase 3（长期）—— 完整 18 SKILL 库
- 发布 npm-like 安装方式：`claude-skill install ruoyu-fiction-suite`
- 接收社区 PR 改进
- 与其他作家系统互操作（Sudowrite/NovelCrafter）

## 价值

| 角度 | 收益 |
|---|---|
| **生态影响力** | 把"内部工具"变"行业标准" |
| **质量反馈** | 社区贡献会发现我们没看到的盲区 |
| **市场定位** | 从"AI 写作系统"升级为"AI 写作工具集" |
| **可持续性** | 单维度 SKILL 比"完整系统"更易维护 |

## 实施 checklist

- [ ] Phase 1: chinese-anti-slop SKILL 试水
- [ ] 注册 skills.sh 账号 + 上传第一个 SKILL
- [ ] Phase 2: 5 个核心 SKILL bundle
- [ ] Phase 3: 完整 18 SKILL 库 + 社区维护

## 参考

- [Anthropic Skills GitHub](https://github.com/anthropics/skills)
- [Agent Skills Overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [skills.sh 注册中心](https://agentskills.io/home)
- [SKILL.md 开放标准 (npm-like for AI)](https://dev.to/dmgjdagooc/the-rise-of-reusable-ai-agent-skills-how-skillssh-and-anthropic-are-changing-the-way-we-build-242d)
