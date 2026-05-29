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
- 🛡️ **质量保障** — 30+ 个 cluster-level scanner（AI 腔调、人设漂移、伏笔回收、字数、节奏、章末锚定…）
- 💾 **断点续写** — WAL 日志 + Git 自动快照，崩溃可恢复，写错可回滚
- 🎯 **天道大势** — 大势已定 / 小势可改，用户每故事块只在 2-3 张走向卡里选

### 🌟 北极星架构

**终极目标：写出和你选定那位网文作者风格一致的文章。** 整个系统从 **蒸馏 → 写作 → 审核 → 复盘** 每一环都服务于这一目标。六条核心原则：

| 原则 | 系统怎么落地 |
|---|---|
| **① 以故事块（cluster）为单位** | cluster 是写作/质检/状态/学习的核心单位；`cluster_lookup` 是章号⇄cluster 唯一权威反查 |
| **② 涟漪规则为核心** | 因果+信息触发事件、事件涌现非预设。混合式引擎：**客观世界数值**→引擎确定性算 delta，**叙事因果**→收集成 context 交模型解读，注入 writer + 下一 cluster 涌现 |
| **③ 大势已定** | 每卷无论小势怎么折腾，方向收敛到固定终点。writer 始终看到本卷收敛锚（ending_state/key_milestones）、涌现朝未达成里程碑倾斜、漂移哨兵 advisory 告警——全是**软牵引非硬锁** |
| **④ 章节切割只是格式输出** | splitter 按字数切 + 章节命名是格式层，**不参与核心质检/状态/学习**；除它俩外全系统以 cluster 为单位 |
| **⑤ 不干涉模型判断** | 系统是**顾问非法官**：作者风格档=第一权威，通用规则仅在作者档未规定时兜底；风格/工艺偏好走 advisory 可豁免，只有一致性/格式/穿帮是 hard_gate |
| **⑥ 及时清理旧版本旧代码** | chapter mode / DCAS 等旧形态持续清除 |

**作者风格如何贯穿全链**：蒸馏提取 35 维风格 DNA → 写作 prompt 以「复刻该作者」为最高准则（通用爽文铁律让位）→ 审核按**作者风格档基线**判（而非写死通用阈值，作者签名词如「似乎/仿佛」不被硬毙）→ 修复只补穿帮/质量、绝不把作者笔法改成通用腔。

### 特性亮点

- **13 个原创 slash command**（`/write`、`/outline`、`/cluster-write`、`/cluster-save-state`、`/distill-style`、`/reconcile` …）+ 2 索引文档
- **10 个原创 agent**（writer、chapter-splitter、reading-reflector、voice-checker、validator-checker、outline-planner、foreshadower、summarizer、reflector、researcher）
- **100+ 个 Python 系统脚本**（audit_hub、cross_cluster_*_aggregate、gen_writer、cluster_lookup、build_manifest 等）
- **Plan 强制规划层**：6 个多步命令必走 plan_tracker，杜绝跳步
- **检测体系顾问制**：advisory 可豁免 / hard_gate 不可豁免，含 15 类客观错误（一致性/格式/穿帮）；风格工艺类按作者风格档松绑
- **没调查没发言权** 元规则：所有决策前必先调研（联网/实地/问用户三选一）
- **🆕 v27 writer freestyle**：writer 不知章数 + 字数自由发挥 / splitter 按 3000-4500/章字数硬范围切 / 末章不够字数从下个故事块补料
- **🆕 章末工艺 4 层防御**（L1-L4 · cluster_001 ch4 三次翻车 sediment）：剧本体「（镜头XX）」+ 文学过渡「* 分隔/听觉淡出/收束句」+ 装神弄鬼无锚 cliffhanger 全拦截。详见下文

### 章末工艺 4 层防御（L1-L4）

连续小说的章末是**钩子**不是**收束**。剧本体「（镜头拉远）」+ 文学过渡「* 分隔/听觉淡出/一切安静下来」+ 装神弄鬼无锚 cliffhanger 都会破坏读者的「下一章渴望」。这条规则用户三次反馈才沉淀清楚（剧本体→文学过渡→装神弄鬼），任何「lesson 文档」都不如系统主动拦截。4 层防御：

| 层 | 实现 | 防御点 |
|---|---|---|
| **L1 PreToolUse Hook** | `core/claude-home/hooks/pretooluse_chapter_edit_gate.py` | 拦主代理 / sub-agent Write/Edit 章节正文含剧本体或章末过渡 → exit 2 |
| **L2 audit_hub scanner** | `core/scripts/chapter_end_anchor_scan.py` | 章末 5 段实词关键词 grep 事件簇/伏笔表/进度/人物卡/道具 · 0 命中 advisory · 命中 banned_patterns hard_gate |
| **L3 writer prompt 自动注入** | `core/scripts/gen_writer.py::_collect_feedback_rules()` | 启动扫 memory/feedback_*.md 抽规则段拼到 writer system prompt 头 · 实测注入 26K chars |
| **L4 cluster-write step 6.4** | `.claude/commands/cluster-write.md` | splitter+titles+changes 后强制跑 anchor scan · hard_gate 触发 → validator-checker → gen_fixer 重写 |

权威 lesson：`core/claude-home/lessons/feedback_no_screenplay_stage_directions_in_novels.md`

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
| `/reconcile` | 设定修改后一致性调和 |
| `/cluster-write` | 写一个故事块（7 步流水线 · v27 freestyle 默认） |
| `/cluster-save-state` | 故事块状态保存（12 步流水线 + 涌现下个 cluster brief） |
| `/db` | 数据库管理（世界/叙事/深度子系统手动微调入口） |

完整 13 个命令见 `.claude/commands/` 或 `使用说明.md`。机械扫描 AI 腔调已并入 `/check-quality`（audit_hub 顾问制）。

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
│   ├── agents/                # 10 个 novel-* sub-agent
│   ├── commands/              # 13 个 slash command + 2 索引文档（v26 chapter mode + 2026-05 精简）
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
    └── scripts/               # 100+ 个 Python 系统脚本
        ├── audit_hub.py
        ├── validate_style.py
        ├── cross_cluster_*_aggregate.py  # 22 跨故事块聚合器（v2 cluster 化前为 cross_chapter_*_scan）
        ├── cross_scene_voice_drift / foreshadowing_handoff / locked_fact_cross_scene / pov_consistency  # 4 新 cluster-only scanner
        ├── chapter_end_anchor_scan.py     # L2 章末锚定扫描
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
- **连续小说章末工艺**：章末是钩子不是收束 · POV 不切换是默认 · cliffhanger 由角色感知传达（看见/听见/意识到+不反应）· 禁止任何「镜头/分隔符/淡出/收束句」破坏跨章渴望

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

- **13 original slash commands** (+2 index docs; chapter-mode `/write-chapter` / `/save-state` removed in v26; pruned to the cluster pipeline in 2026-05)
- **10 original sub-agents**
- **100+ Python system scripts**
- **🆕 v27 writer freestyle**: writer doesn't know chapter count / word target; splitter cuts at 3000-4500 CJK/chapter hard range; tail-end backfill from next cluster (pending_tail mechanism)
- **🆕 4-layer chapter-end guard** (L1-L4 · sediment from cluster_001 ch4 triple regression): blocks screenplay-style stage directions「(camera pulls back)」, literary closure markers「* divider / sound fade / 'everything fell silent'」, and unanchored mystical cliffhangers. L1 hook + L2 anchor scanner + L3 writer prompt auto-inject + L4 mandatory pipeline step
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
