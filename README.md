# 若渝AI · 智能小说写作助手

> 一个跑在 Claude Code 上的中文小说创作系统。你想故事，剩下的它来。

[English](#english) · [中文](#中文)

---

## 中文

### 这是什么

**若渝AI** 是基于 Claude Code 的中文小说写作助手。它能：

- 🎨 **蒸馏作者风格** — 把你喜欢的作者的写作 DNA 提取成 35 维度档案，照着那种感觉写
- 📐 **规划大纲** — 卷级大势 + 事件池 + 涟漪传播，剧情自然涌现而非僵化
- ✍️ **逐章写作** — 多 agent 流水线：planner → writer → splitter → fixer → validator → reflector
- 🛡️ **质量保障** — 30+ 种跨章 scanner（AI 腔调、人设漂移、伏笔回收、字数、节奏…）
- 💾 **断点续写** — WAL 日志 + Git 自动快照，崩溃可恢复，写错可回滚
- 🎯 **天道大势** — 大势已定 / 小势可改，用户每章只在 2-3 张走向卡里选

### 特性亮点

- **44 个原创 slash command**（`/write`、`/distill-style`、`/save-state`、`/reconcile` …）
- **13 个原创 agent**（writer、splitter、reflector、voice-checker、validator-checker …）
- **122 个 Python 系统脚本**（audit_hub、cross_chapter_*_scan、gen_writer 等）
- **Plan 强制规划层**：6 个多步命令必走 plan_tracker，杜绝跳步
- **检测体系顾问制**：advisory 可豁免 / hard_gate 不可豁免，含 11 类客观错误
- **没调查没发言权** 元规则：所有决策前必先调研（联网/实地/问用户三选一）

### 系统要求

- **Node.js** ≥ 18
- **[Claude Code](https://docs.claude.com/en/docs/claude-code)** 官方 CLI（自行安装）
- **Python** ≥ 3.10（系统脚本运行时）
- **Git**（自动版本快照）
- **OS**：Windows / macOS / Linux 均可（脚本兼容）

### 安装

```bash
# 1. 装 Claude Code 官方 CLI
npm install -g @anthropic-ai/claude-code

# 2. 克隆本仓库
git clone https://github.com/<your-username>/ruoyuai.git
cd ruoyuai

# 3. 配置 gen-model（用于生成内容的二级模型）
cp .env.example .env
# 编辑 .env 填入你的 API key（支持 DeepSeek/Kimi/GLM/Qwen 等 OpenAI 兼容 API）

# 4. 启动
claude --dangerously-skip-permissions
```

### 3 步上手

#### Step 1 · 打招呼

```
你好
```

会看到主菜单（10 个选项 + 自然语言路由）。

#### Step 2 · 选风格或直接写

```
我想模仿《BookC》的风格
```

或

```
/distill-style D:\小说\参考小说.txt
```

或直接

```
帮我写个修仙小说，男主是外卖员
```

#### Step 3 · 确认后开写

系统会：

1. 调研先行（联网搜热点 + 同题材爆款元素）
2. 生成 3 个灵感供你挑
3. 生成卷级大纲 + 初始化 13 个数据库 + Git init
4. 写第一章（多 agent 流水线 + 自动 save-state）
5. 展示 2-3 张「剧情走向卡片」 → 你选哪张就往哪走
6. 循环 4-5 直到完书 → 自动拼接全文.txt

**全程只有一个停顿点**：每章末尾的走向卡。说「全自动」连这个也跳过。

### 命令速查表

| 命令 | 用途 |
|------|------|
| `/write` | 完整写小说流程 |
| `/continue` | 断点续写 |
| `/outline` | 生成大纲 + 建库 |
| `/distill-style` | 蒸馏作者风格 |
| `/distill-character` | 深度蒸馏角色 |
| `/check-quality` | 质量 + 正典 + 风格三维校验 |
| `/anti-slop` | 机械扫描 AI 腔调 |
| `/reconcile` | 设定修改后一致性调和 |
| `/save-state` | 章节状态保存（11 步流水线） |
| `/db` | 数据库管理 |

完整 44 个命令见 `.claude/commands/` 或 `使用说明.md`。

### 目录结构

```
ruoyuai/
├── CLAUDE.md                  # 系统主指令（Claude Code 自动加载）
├── 使用说明.md                 # 用户文档
├── .env.example               # gen-model 配置示例
├── .claude/
│   ├── agents/                # 13 个 novel-* sub-agent
│   ├── commands/              # 44 个 slash command
│   ├── templates/             # agent 调用模板
│   ├── styles/                # 风格库（用户产出，本仓库不追踪）
│   └── projects/              # 小说项目（用户产出，本仓库不追踪）
└── core/
    ├── claude-home/
    │   ├── agents/            # agent prompt 全集
    │   ├── commands/          # command 全集（start.cmd 同步源）
    │   ├── schemas/           # JSON schema 定义
    │   ├── lessons/           # 跨项目教训
    │   ├── plans/             # plan_tracker 模板（6 个命令）
    │   ├── templates/
    │   │   └── examples/      # 2 个完整项目 schema 示例
    │   ├── regression_gold_suite/  # 回归测试样本
    │   ├── STRUCTURE.md       # 目录权威规范
    │   └── CLAUDE.md          # 系统配置主文档
    └── scripts/               # 122 个 Python 系统脚本
        ├── audit_hub.py
        ├── validate_style.py
        ├── cross_chapter_*_scan.py    # 30+ 跨章 scanner
        ├── gen_writer.py / gen_fixer.py / gen_creative.py
        ├── plan_tracker.py
        └── ...
```

### 设计理念

- **Gen-Model + Claude 角色分工**：创意笔触走 gen-model（DeepSeek/Kimi 等可一行切换），收集/整理/判断/裁决走 Claude
- **DCAS 双章自然截断**：writer 出整块 6500 字叙事，splitter 选自然截断点切章，章节边界 = 页面物理限制而非刻意设计
- **三层防御 plan**：契约层（文档）+ 追踪层（plan_tracker + SHA-256 attestation）+ 校验层（PreToolUse hook）
- **检测顾问制**：scanner 不当法官只当顾问，writer 有充分理由可豁免 advisory，hard_gate 客观错误不可豁免
- **天道大势**：大势已定（卷级 event）+ 小势可改（章级走向卡 2-3 选 1）+ 涟漪传播（用户选择→世界先动一格→writer 感知）
- **没调查没发言权**：所有方向性决策前必先调研（联网/实地/问用户三选一），高于所有其他规则

### 不要做的事

- ❌ 别把第三方版权小说的蒸馏产物上传到公开仓库（含原文摘录）
- ❌ 别手动编辑 `_数据库/` 里的 JSON
- ❌ 别 `git reset --hard` 到很早的章节（数据库会和章节对不上）

### 许可证

MIT License — 见 [LICENSE](LICENSE)

本项目运行依赖 [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code)，Claude Code 本身是 Anthropic PBC 的专有软件，请遵守其许可条款。

---

## English

### What is this

**Ruoyu AI** is a Chinese novel writing assistant built on top of Claude Code. It can:

- 🎨 **Distill author style** — extract a 35-dimension writing DNA from reference novels
- 📐 **Plan outlines** — volume-level momentum + event pool + ripple propagation
- ✍️ **Write chapter-by-chapter** — multi-agent pipeline: planner → writer → splitter → fixer → validator → reflector
- 🛡️ **Quality guardrails** — 30+ cross-chapter scanners (AI-slop, persona drift, foreshadowing payoff, word count, pacing…)
- 💾 **Crash recovery** — WAL log + auto Git snapshots
- 🎯 **Destiny system** — fixed grand momentum + flexible local momentum, user picks 1 of 2-3 direction cards per chapter

### Highlights

- **44 original slash commands**
- **13 original sub-agents**
- **122 Python system scripts**
- **Mandatory planning layer** with SHA-256 anti-tampering attestation
- **Advisory/Hard-Gate detection** — advisory waivable with reason, hard-gate enforced
- **"No investigation, no voice"** meta-rule — all decisions require evidence

### Requirements

- Node.js ≥ 18
- [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI
- Python ≥ 3.10
- Git

### Install

```bash
npm install -g @anthropic-ai/claude-code
git clone https://github.com/<your-username>/ruoyuai.git
cd ruoyuai
cp .env.example .env  # fill in your gen-model API key
claude --dangerously-skip-permissions
```

Type `你好` (Hello) to see the main menu, or jump straight in:

```
/write          # full novel pipeline
/distill-style  # distill an author's style
/continue       # resume from last breakpoint
```

### License

MIT License — see [LICENSE](LICENSE).

This project runs on top of [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code), which is proprietary software by Anthropic PBC. Please comply with its license terms.

---

## 致谢 · Credits

- [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code) — 底层 AI agent runtime
- 灵感来源：网文写作社区、arxiv 多 agent 论文（SCORE / Multi-Agent Evolve / AutoSkill / ERL 等）
