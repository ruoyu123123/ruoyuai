# 若渝AI · 智能小说写作助手

> 一个跑在 Claude Code 上的中文小说创作系统。你想故事，剩下的它来。

[English](#english) · [中文](#中文)

---

## 中文

### 这是什么

**若渝AI** 是基于 Claude Code 的中文小说写作助手。它能：

- 🎨 **蒸馏作者风格** — 把你喜欢的作者的写作 DNA 提取成 35 维度档案，照着那种感觉写
- 📐 **规划大纲** — 卷级大势 + 事件池 + 涟漪传播，剧情自然涌现而非僵化
- ✍️ **故事块写作** — v26 cluster mode 流水线：planner → writer (整块) → 质检 (cluster 级) → splitter (按字数切)
- 🛡️ **质量保障** — 30+ 种跨章 scanner（AI 腔调、人设漂移、伏笔回收、字数、节奏…）
- 💾 **断点续写** — WAL 日志 + Git 自动快照，崩溃可恢复，写错可回滚
- 🎯 **天道大势** — 大势已定 / 小势可改，用户每故事块只在 2-3 张走向卡里选

### 特性亮点

- **34 个原创 slash command**（`/write`、`/distill-style`、`/cluster-write`、`/cluster-save-state`、`/reconcile` …）
- **15 个原创 agent**（writer、chapter-splitter、reading-reflector、voice-checker、validator-checker、adversarial-reader、counterfactual-judge …）
- **120+ 个 Python 系统脚本**（audit_hub、cross_chapter_*_scan、gen_writer、stuck_loop_guard 等）
- **v23 异质监督 5 层架构**：破 LLM Self-Correction Blind Spot（业界数据 64.5%），见下文
- **Plan 强制规划层**：6 个多步命令必走 plan_tracker，杜绝跳步
- **检测体系顾问制**：advisory 可豁免 / hard_gate 不可豁免，含 12 类客观错误
- **没调查没发言权** 元规则：所有决策前必先调研（联网/实地/问用户三选一）
- **🆕 v27 writer freestyle**：writer 不知章数 + 字数自由发挥 / splitter 按 3000-4500/章字数硬范围切 / 末章不够字数从下个故事块补料

### v23 异质监督 5 层架构

业界数据：LLM 平均 **64.5% 盲点率**（Self-Correction Bench, arxiv 2507.02778）—— 能改别人的错改不了自己的错。所有「writer / judge / reflector 共享同一套 manifest 视角」的系统都会**集体盲**。

v23 用 4 个独立攻击角度补盲：

| 层 | 文件 | 攻击角度 | 业界依据 |
|---|---|---|---|
| **L0 卡死守卫** | `core/scripts/stuck_loop_guard.py` | **纯规则**不调 LLM，不会被同源 prompt 污染 | Antigravity loop break / Wink (arxiv 2602.17037) |
| **L1 敌对读者** | `.claude/agents/novel-adversarial-reader.md` + `core/scripts/adversarial_blindspot_scan.py` | **屏蔽**所有内部 context，纯网文老读者视角挑刺 | VIGIL (arxiv 2512.07094) sibling supervisor |
| **L2+3 反事实盲审** | `.claude/agents/novel-counterfactual-judge.md` + `core/scripts/counterfactual_judge_diff.py` | **伪装匿名稿**，破 self-protection 13-22% 偏见 | Counterfactual Debating (arxiv 2406.11514) · SPC (arxiv 2504.19162) |
| **L4 Pareto 演化** | `core/scripts/gepa_prompt_optimizer.py` | **保留候选多样性**不让单一最优覆盖 | GEPA (ICLR 2026 Oral, arxiv 2507.19457) |

详见 `core/claude-home/lessons/v23-blindspot-layer0-1.md`。

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
3. 生成卷级大纲 + 初始化 34 个核心子系统 JSON + Git init
4. 写第一个故事块（cluster-write 7 步流水线 + 自动 cluster-save-state 12 步）
5. 展示 2-3 张「剧情走向卡片」 → 你选哪张就往哪走
6. 循环 4-5 直到完书 → 自动拼接全文.txt

**全程只有一个停顿点**：每个故事块结束的走向卡。说「全自动」连这个也跳过。

### 命令速查表

| 命令 | 用途 |
|------|------|
| `/write` | 完整写小说流程 |
| `/continue` | 断点续写 |
| `/outline` | 生成大纲 + 建库（含 step 1.7 AskUser 每卷 cluster 数） |
| `/distill-style` | 蒸馏作者风格 |
| `/distill-character` | 深度蒸馏角色 |
| `/check-quality` | 质量 + 正典 + 风格三维校验 |
| `/anti-slop` | 机械扫描 AI 腔调 |
| `/reconcile` | 设定修改后一致性调和 |
| `/cluster-write` | 写一个故事块（7 步流水线 · v27 freestyle 默认） |
| `/cluster-save-state` | 故事块状态保存（12 步流水线 + 涌现下个 cluster brief） |
| `/db` | 数据库管理 |

完整 34 个命令见 `.claude/commands/` 或 `使用说明.md`。

> 🔴 **v26 起 chapter mode（`/write-chapter` / `/save-state` 单章命令）已彻底废弃**，统一走 cluster mode（故事块整体迭代→最后才切章）。

### 目录结构

```
ruoyuai/
├── CLAUDE.md                  # 系统主指令（项目根 · Claude Code 自动加载 · 唯一权威源）
├── 使用说明.md                 # 用户文档
├── README.md                   # 仓库入口
├── .env.example               # gen-model 配置示例
├── workspace/                  # 用户产出（仓库不追踪）
│   ├── novels/                # 小说项目（每本独立 Git 仓库）
│   └── styles/                # 风格库（跨项目共享）
├── .claude/
│   ├── agents/                # 15 个 novel-* sub-agent
│   ├── commands/              # 34 个 slash command（v26 chapter mode 已删）
│   └── templates/             # agent 调用模板
└── core/
    ├── claude-home/
    │   ├── schemas/           # JSON schema 定义（含 event_cluster v23 · v27 字段降级标记）
    │   ├── lessons/           # 跨项目教训（v23-v27 增量）
    │   ├── plans/             # plan_tracker 模板（6 个命令）
    │   ├── hooks/             # PreToolUse 拦截层（plan / subsystems / step-skip 防御）
    │   ├── templates/
    │   │   └── examples/      # 2 个完整项目 schema 示例
    │   ├── regression_gold_suite/  # 回归测试样本
    │   ├── STRUCTURE.md       # 目录权威规范（hard_gate 单一来源）
    │   └── CLAUDE.md          # ⚠️ 仅作为 core 子目录的 README · 项目根 CLAUDE.md 才是 Claude Code 加载的主指令
    └── scripts/               # 120+ 个 Python 系统脚本
        ├── audit_hub.py
        ├── validate_style.py
        ├── cross_chapter_*_scan.py    # 30+ 跨章 scanner
        ├── gen_writer.py / gen_fixer.py / gen_creative.py
        ├── plan_tracker.py
        └── ...
```

### 设计理念

- **Gen-Model + Claude 角色分工**：创意笔触走 gen-model（DeepSeek/Kimi 等可一行切换），收集/整理/判断/裁决走 Claude
- **v26 cluster mode 倒置流水线**：writer 出整块 12K-25K 字叙事，cluster 级双轨质检（机械+阅读），**最后才** splitter 按字数切章。章节边界 = 页面物理限制而非刻意设计。chapter mode 已彻底废弃
- **v27 writer 自由 + 字数切**：writer 不知道目标章数/字数 · splitter 按 3000-4500/章硬范围切 · 末章不足从下个 cluster 草稿头部补料（pending_tail 机制）
- **三层防御 plan**：契约层（文档）+ 追踪层（plan_tracker + SHA-256 attestation）+ 校验层（PreToolUse hook）
- **检测顾问制**：scanner 不当法官只当顾问，writer 有充分理由可豁免 advisory，hard_gate 客观错误不可豁免
- **天道大势 + fluid 涌现**：大势已定（卷级 ME）+ 小势可改（cluster 级走向卡 2-3 选 1）+ 涟漪传播（用户选择→世界先动一格→writer 感知）+ cluster brief 涌现（cluster_002+ 在每个 cluster 完成时动态生成）
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
- ✍️ **Write cluster-by-cluster** — v26 cluster-mode pipeline: planner → writer (full block) → cluster-level dual-track audit → splitter (by word count, after audit passes)
- 🛡️ **Quality guardrails** — 30+ cross-chapter scanners (AI-slop, persona drift, foreshadowing payoff, word count, pacing…)
- 💾 **Crash recovery** — WAL log + auto Git snapshots
- 🎯 **Destiny system** — fixed grand momentum + flexible local momentum, user picks 1 of 2-3 direction cards per cluster

### Highlights

- **34 original slash commands** (chapter-mode `/write-chapter` / `/save-state` removed in v26)
- **15 original sub-agents**
- **120+ Python system scripts**
- **🆕 v27 writer freestyle**: writer doesn't know chapter count / word target; splitter cuts at 3000-4500 CJK/chapter hard range; tail-end backfill from next cluster (pending_tail mechanism)
- **Mandatory planning layer** with SHA-256 anti-tampering attestation
- **Advisory/Hard-Gate detection** — advisory waivable with reason, hard-gate enforced
- **"No investigation, no voice"** meta-rule — all decisions require evidence
- **v23 Heterogeneous Oversight (4-layer)** — counters the 64.5% LLM Self-Correction Blind Spot (arxiv 2507.02778) via independent siblings that don't share the writer's context: rule-based loop guard (L0), context-blind adversarial reader (L1), blind-review counterfactual judge (L2+3), GEPA Pareto candidate pool (L4, ICLR 2026 Oral)

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
/write              # full novel pipeline (v26 cluster-mode)
/distill-style      # distill an author's style
/cluster-write      # write one story-cluster (v27 freestyle default)
/cluster-save-state # persist cluster state + emerge next cluster brief
/continue           # resume from last breakpoint
```

> Note: chapter-mode (`/write-chapter` / `/save-state`) was removed in v26. All writing flows through cluster-mode (cluster-level iteration, then split by word count).

### License

MIT License — see [LICENSE](LICENSE).

This project runs on top of [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code), which is proprietary software by Anthropic PBC. Please comply with its license terms.

---

## 致谢 · Credits

- [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code) — 底层 AI agent runtime
- 灵感来源：网文写作社区、arxiv 多 agent 论文（SCORE / Multi-Agent Evolve / AutoSkill / ERL 等）
