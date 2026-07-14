# 若渝AI · 中文小说创作系统

若渝AI 是运行在 Claude Code 上的中文长篇小说创作系统。它的核心设计是：**创作、质检、状态保存、涌现下一步都以故事块（cluster）为唯一工作单位**，章节只是在故事块通过审核后的输出格式。

## 唯一创作链路

```
/write
  选择或蒸馏作者风格
  novel-outline-planner 亲笔写灵感卡并让用户选定方向
  调用 /outline
    初始化项目与 34 个核心子系统 JSON
    novel-outline-planner 亲笔写卷级大势与事件池（gen_creative.py 确定性验收合并）
    只详化 cluster_001
  循环：
    /cluster-write
      build_manifest 生成注入清单
      novel-writer（Claude）亲笔逐场景写草稿 → gen_writer.py 调 gemini 分段润色出终稿
      audit_hub + reading/voice/foreshadow/reflect/summarize 做 cluster 级检查
      通过后 splitter 按字数切章，novel-titler 亲笔命名章标题
    /cluster-save-state
      回写事实、摘要、伏笔、角色、关系、评价与聚合账本
      涌现下一 cluster 的候选走向卡
    用户选择走向卡
  /export
```

这条链路是唯一入口。正文生成必须由 `/cluster-write` 的 required plan step 产出，状态变化必须由 `/cluster-save-state` 的 required plan step 回写。

可以把已经验证过、非常适合的功能、模型或论文/开源机制加入创作系统，但必须作为 `/outline`、`/cluster-write`、`/cluster-save-state`、走向卡或 `/export` 的 required plan step / required 子步骤落地；同步更新命令文档、plan 模板、agent 合约、STRUCTURE/CLAUDE 描述和测试。

## 核心原则

| 原则 | 落地方式 |
|---|---|
| 故事块唯一 | cluster 是写作、质检、状态保存和学习的核心单位；`cluster_lookup` 只负责章号到 cluster 的反查 |
| 章节是输出格式 | splitter 只在整块草稿审核完成后按字数切章；章节不参与主质检和状态学习 |
| 作者风格优先 | 作者风格档是第一权威，通用规则只在作者档未规定时补位 |
| 顾问制质检 | 风格和工艺问题走 advisory，可说明豁免；一致性、格式、穿帮等客观错误走 hard_gate |
| 大势收敛，小势涌现 | 卷级终点固定，cluster 走向由用户选择、世界状态和剩余事件池共同涌现 |
| 调研先行 | 方向性决策前必须有调研、用户输入或已有数据库依据 |
| 创作亲笔 | 灵感卡、大纲/ME 池、走向卡、正文场景稿、章节标题、风格 skill 与 AV 评审全部由 CLI Claude 亲笔；外部 gen-model 只做对已有文本的等体量润色与按 brief 修复 |

## 主要能力

- 作者风格蒸馏：从参考文本提取风格档案，`novel-skill-author` 亲笔撰写可注入 skill，同栈复刻 + `novel-av-judge` AV 配对评审验证。
- 卷级大纲：`novel-outline-planner` 亲笔生成阶段、事件池、cluster 规划和首块 scene storyboard，确定性脚本验收合并。
- 故事块写作：Claude 亲笔逐场景写草稿（novel-writer agent · 含创作自评）→ `gen_writer.py` 调 gemini 分段等体量润色出终稿。
- Cluster 级审核：机械 scanner、阅读反思、声纹检查、伏笔评估和经验沉淀都看整块文本。
- 状态保存：`/cluster-save-state` 统一回写人物、世界、关系、伏笔、摘要、评价和下块候选。
- 断点恢复：plan_tracker 的 plan JSON 记录 step 状态（续跑点唯一真相源），配合 `.wal/` 产物与 Git 快照保证可续跑。

## 命令边界

| 命令 | 职责 |
|---|---|
| `/write` | 端到端创作编排入口 |
| `/outline` | 生成卷级大纲，初始化数据库，只详化首个 cluster |
| `/cluster-write` | 写一个故事块并完成切章前审核、切章和标题 |
| `/cluster-save-state` | 保存故事块状态，涌现下一块走向卡 |
| `/continue` | 从中断点恢复到正确步骤 |
| `/export` | 导出全文 |
| `/distill-style` | 蒸馏作者风格 |
| `/distill-character` | 蒸馏角色 voice / 行为模式 |
| `/db` | 数据库只读查看、搜索、导出和定位 |

质量审计、正典一致性和历史状态传播不再有独立 slash command。写作中由 `/cluster-write` 和 `/cluster-save-state` 串在主链里处理，成品导出前由 `/export` 做硬校验。

设定变更规则：当前未保存的 cluster 回到草稿层修正并重跑 `/cluster-write`；已经保存的状态通过后续 cluster 承接；事实回库只走 `/cluster-save-state`。

命令索引见 `.claude/commands/README.md`，用户向导见 `使用说明.md`。

## 目录结构

```
ruoyuai/
├── CLAUDE.md                  # 项目级主指令
├── README.md                  # 仓库入口与唯一创作链路
├── 使用说明.md                 # 用户使用手册
├── pytest.ini                 # 唯一测试入口配置
├── .claude/
│   ├── agents/                # novel-* agent 合约
│   └── commands/              # slash command 合约与索引
├── core/
│   ├── claude-home/
│   │   ├── schemas/           # JSON schema
│   │   ├── templates/         # 子系统骨架
│   │   ├── hooks/             # Claude Code hook
│   │   └── plans/             # plan_tracker 模板
│   └── scripts/               # 确定性脚本与 gen-model 润色/修复 wrapper
├── tests/                     # pytest 测试
└── workspace/                 # 用户小说、风格库、临时研究，默认不入库
```

## 安装与运行

```bash
npm install -g @anthropic-ai/claude-code
git clone https://github.com/<your-username>/ruoyuai.git
cd ruoyuai
cp .env.example .env
claude --dangerously-skip-permissions
```

配置 gen-model（外部润色/修复模型）后可查看当前 profile：

```bash
python core/scripts/gen_model.py show
```

## 验证入口

唯一测试入口是在仓库根目录执行：

```bash
py -m pytest
```

根 `pytest.ini` 负责限定 `tests/`、排除 `workspace/`、`moyin-creator/`、临时目录和构建产物。不要使用子目录 pytest 配置或旧的自定义 runner 作为官方入口。

## 不要做的事

- 正文生成、状态保存、质量审计和导出必须落在唯一创作链路的 required plan step 上。
- 新能力接入时必须进入 `/outline`、`/cluster-write`、`/cluster-save-state`、走向卡或 `/export`，并同步文档和测试。
- 不要把用户小说、参考原文、蒸馏中间产物、真实 API 日志提交到系统仓库。
- 不要手动编辑 `_数据库/` JSON；`/db` 只用于查看、搜索、导出和定位，事实回库只走 `/cluster-save-state`。
- 不要用破坏性 Git 操作回滚用户小说项目，除非用户明确要求。

## 许可证

MIT License，见 [LICENSE](LICENSE)。
