# 若渝AI · 文件目录与命名规范

> 整个小说系统的目录框架和命名规范。所有命令产出文件必须遵守本规范。
> 本文档优先级：**高于各命令文档**。命令文档应反向引用本文档。

---

## 一、顶层目录全景

```
<REPO_ROOT>/                          # 项目根（Claude Code working directory）
├── .claude/                                   # Claude Code 配置层
│   ├── commands/                              # 用户级命令定义
│   ├── agents/                                # Subagent 定义与 judge prompt 单一真理源
│   └── settings.json                          # 项目设置
├── .codex/                                    # Codex 跑道配置层（agents/*.toml 为 .claude/agents 镜像 + config.toml/hooks.json）
├── core/                                       # 系统核心代码
│   ├── claude-home/                            # 系统主目录（见第四节）
│   ├── scripts/                                # Python 确定性工具、scanner 与运行编排（见第四-ter 节）
│   ├── ml/                                     # 神经网络模型训练/推理（见第四-ter 节）
│   └── data/                                   # scanner/模型消费的词典数据
├── tests/                                       # pytest 回归测试
├── research/                                    # 【本地调研报告区 · 不入 git】物理保留原位
├── workspace/                                  # 【用户产出区】
│   ├── styles/{书名}/                          # 全局风格库（见第二节）
│   ├── novels/{书名}/                          # 小说项目（见第三节）
│   └── _temp_research/                         # 实验证据链暂存区，默认不入 git
├── start.sh / start.cmd                         # 跨平台启动入口（仓库根外启动时同步 core/claude-home/CLAUDE.md 入口桩 + .claude/commands 到当前目录）
├── .env.example / .gitattributes / .gitignore   # 环境示例、文本属性与追踪边界
├── pytest.ini / requirements.txt                # 测试与依赖配置
├── LICENSE                                      # 许可证
├── README.md / INSTALL.md / 使用说明.md         # 用户文档
└── CLAUDE.md                                   # 项目级 AI 指令
```

**铁律**：
- ⛔ 用户产出文件**永远不要**写到 `core/` 下（core 是系统代码，不是数据）
- ⛔ 临时文件**永远不要**写到 `.claude/` 根目录（污染配置层）
- ✅ 风格库 = `workspace/styles/{书名}/`
- ✅ 小说项目 = `workspace/novels/{书名}/`
- ✅ 系统经验 = `core/claude-home/lessons/`
- ✅ Agent 定义唯一权威位置 = `.claude/agents/`（`.codex/agents/` 是 Codex 跑道 TOML 镜像，随权威同步；`core/claude-home/` 不设 agents 副本）

---

## 二、风格库结构（`workspace/styles/{书名}/`）

蒸馏产物的标准目录。每个被蒸馏过的小说一个子目录。

```
workspace/styles/{书名}/                         # 风格项目根
├── README.md                                  # 风格简介 + 快速调用
├── 作者风格_FINAL.json                        # 终版数据基线（机读）
├── skill_FINAL.md                             # 终版写作指导（人读）
├── distillation_log.md                        # 蒸馏迭代日志
├── chapter_ranges.json                        # 章节索引（行号映射表）
├── lessons_learned_v{N}.md                    # 各版本反思日志
│
├── 蒸馏进度/                                   # 单章蒸馏 JSON
│   ├── ch{N}.json                             # 单章完整蒸馏
│   ├── ch{N}_metrics.json                     # style_analyzer 量化输出
│   └── ...
│
├── 衔接分析/                                   # 章际衔接 JSON
│   ├── ch{N}_{N+2}_continuity.json            # 3 章窗口衔接分析
│   └── ...
│
├── 复刻测试/                                   # 同栈复刻产物
│   └── {验证轮次}/
│       ├── claude_scenes/
│       │   ├── scene_*.txt                    # novel-replica-writer 亲笔场景稿
│       │   └── agent_report.json              # 场景文件、skill 与 cluster 绑定回执
│       ├── cluster_{key}_replica.txt          # gemini 分段润色后的复刻终稿
│       └── cluster_{key}_replica_meta.json    # 润色遥测与评分元数据
│
├── _skillopt/train/{run_id}/                  # SkillOpt 可恢复训练状态
│   ├── claude_scene_jobs.json                 # 候选 digest × run × cluster required 任务
│   ├── claude_scene_jobs/{job_run}/{digest}/{cluster_id}/claude_scenes/
│   │   ├── scene_*.txt                        # 每个候选独占的 Claude 场景稿
│   │   └── agent_report.json                  # novel-replica-writer 回执
│   ├── optimizer_patch_jobs.json              # skill digest × ep/step patch 提案 required 任务
│   ├── optimizer_patch_jobs/{ep_step}/{digest}/
│   │   ├── trajectory_batch.json              # 轨迹素材（含 PROTECTED/REJECT_BUFFER 上下文）
│   │   ├── skill_snapshot.md                  # 提案对象 skill 快照
│   │   └── patches.json                       # novel-skill-author (MODE=patch) 亲笔提案
│   └── replicas/{rollout_run}/                # rollout 复刻评分产物（{cluster}_replica.txt / _eval.json / _av.json + {cluster}_av_jobs/ AV 投票任务·novel-av-judge 亲笔判别）
│
├── 对比报告/                                   # 闭环阶段 3 产物
│   ├── eval_v{X}_cluster_{key}.json           # SFS 量化报告
│   ├── diff_v{X}_cluster_{key}.md             # 多维度差距报告
│   ├── ref_cluster_{key}.txt                  # 对照原文片段
│   ├── av_v{N}.json + av_v{N}_jobs/           # AV 配对判别 advisory 报告 + 投票任务（av_judge_jobs.json / vote_* prompt·verdict / agent_receipt.json · novel-av-judge 亲笔判别 · distill_av_verify.py 渲染+聚合 · skillopt 终验收同构 skillopt_av_verify*_jobs/）
│   └── ...
│
├── 原文/                                       # 【可选】原文备份
│   └── 第{N}章.txt                            # 用户可读的中文命名
│
├── _tmp/                                       # 临时文件（agent 中间产物）
│   └── ch{N}.txt                              # 章节正文临时副本
│
└── _archive_v{X}/                              # 版本归档
    └── ...
```

**版本号规则**：
- `v0.1`、`v0.2`、`v0.3`、`v0.4`、`v0.5`（每 30 章一次聚合）
- `v0.5.1`、`v0.5.2`（闭环阶段 4-5 修正版）
- `FINAL`（出货终版，阶段 6 标记）

---

## 三、小说项目结构（`workspace/novels/{书名}/`）

写作中的小说。每本小说独立 Git 仓库。

```
workspace/novels/{书名}/                       # 项目根（独立 Git 仓库）
├── .git/                                      # Git 仓库（独立）
├── .gitignore                                 # 排除临时文件
├── README.md                                  # 项目简介 + 进度
├── 大纲.md                                    # 卷级大纲
├── exports/                                   # /export 产出（<书名>_全文_<章数>章.txt）
│
├── _数据库/                                   # 34 个核心子系统 JSON
│   ├── 人物卡.json                            # 角色信息 + 声纹包
│   ├── 世界观.json                            # 设定关键词
│   ├── 伏笔表.json                            # 契诃夫之枪引擎（promises 三态 status∈open/suspended/consumed + owner + payoff_scope）
│   ├── 故事块摘要.json                          # cluster 摘要 + 情绪值
│   ├── 进度.json                              # 卷级大纲 + cluster 规划 + 当前进度
│   ├── 场景规则.json                          # 场景类型写作规则
│   ├── 写作经验.json                          # Learning Loop
│   ├── 用户偏好.json                          # 用户偏好档案
│   ├── 地图.json                              # 世界地图
│   ├── 关系.json                              # 角色关系（4 维数值）
│   ├── 事件表.json                            # 事件触发器
│   ├── 时间线.json                            # 时间系统
│   ├── 道具.json                              # 道具/物品
│   ├── 作者风格.json                          # 【从全局风格库复制】
│   ├── .distill_character/{角色}_claude_drafts/ # novel-replica-writer 草稿
│   │   ├── sample_*.txt                       # style/anti JSON 样本草稿
│   │   └── agent_report.json                  # 角色、素材与样本绑定回执
│   ├── .wal/                                  # WAL 写前日志（cluster 流水线）
│   └── .lock                                  # 项目锁
│
├── 章节/                                       # cluster 草稿 + splitter 后章节产物
│   ├── cluster_{key}_draft/
│   │   ├── claude_scenes/scene_*.txt           # Claude 逐场景亲笔稿
│   │   ├── cluster_{key}_draft_claude.txt      # 场景稿拼接审计基线
│   │   ├── changes_claude.json                 # Claude self_eval/waivers 草稿
│   │   ├── cluster_{key}_draft.txt             # gemini 分段润色后的整块终稿
│   │   ├── cluster_{key}_changes.json          # writer 创作期自评 + waivers + 确定性遥测
│   │   └── cluster_{key}_pending_tail.txt      # 末章不足下限时留给下个 cluster 拼接的尾段
│   └── 第{N}章/
│       ├── 第{N}章.txt                        # splitter 切出的纯正文（用户可读）
│       └── 第{N}章_changes.json               # 从 cluster changes 平铺的章节级数据
│
├── _tmp/                                       # 临时文件
└── _archive/                                   # 归档（重写章节时）
```

**命名规则**：
- 章节文件夹：`第{N}章/`（用户可读，中文）
- 章节文件：`第{N}章{后缀}` 用户可见，中文化
- 数据库 JSON：中文命名（人物卡 / 世界观 / 伏笔表...）

**正文/数据分离**：
- `第{N}章.txt` = **纯正文**，`第{N}章_changes.json` = **结构化数据**，两者是两个物理文件，不再混在一个 txt 里靠 `---CHANGES---` 分隔符切。
- `第{N}章_changes.json` 由 `novel-chapter-splitter`（cluster mode · splitter 在 step 6 从 `cluster_<key>_changes.json` 按切点平铺成 per-chapter）产出，不再由 save-state 产出。
- 所有读写章节正文 / CHANGES 的脚本必须走 `core/scripts/chapter_io.py` 统一模块（`read_body` / `read_changes` / `write_body` / `write_changes`），禁止各自 split。
- `第{N}章_changes.json` 保存 `self_eval`、`waivers` 和确定性遥测；客观状态不由 writer 自报。
- **正文生成两阶段**：step 2a `novel-writer` 逐场景写作；step 2b `gen_writer.py` 调 gemini 分段等体量润色并拼接终稿。缺 `claude_scenes` 时立即失败。
- writer 链只产正文与创作自评。cluster 级客观状态由 Claude 梳理、确定性脚本回库：
  - **角色 / 道具 / 关系 / `locked_facts`**：`novel-archivist` 读整 cluster 正文客观抽取 → `_数据库/.wal/cluster_<key>_archive.json` → `apply_archive.py <项目> --cluster <key>` 确定性回库（幂等·按 id 去重）。
  - **时间 / 地点 / Hub / 世界事件与消费 / heart events**：`novel-state-tracker` 读整 cluster 正文和当前运行态 → `_数据库/.wal/cluster_<key>_state_delta.json`，再由 `state_tracker_receipt.py` 写 `_数据库/.wal/cluster_<key>_state_tracker_receipt.json`，以 `PLAN_ID`、`STEP` 和 SHA-256 绑定 delta；确定性消费者按 cluster 回库。普通 state delta 不是 Agent 完成回执。
  - **伏笔**：`novel-foreshadower` 的 JudgeReport + `outline` brief（plant / payoff 由 Claude 梳理，非 writer 自报）。
  - `save_state --apply-cluster-changes` 只解析创作自评、豁免和确定性遥测，不落任何客观状态；step 3 随后由 `writer_truth_check.py --cluster` 产通过报告 `_数据库/.judge_reports/cluster_<key>_writer-truth-check.json`。
  - step 10 的 `save_state --apply-foreshadow-state` 注册 brief 伏笔并应用本块 payoff，产 `_数据库/.wal/cluster_<key>_foreshadow_state_receipt.json`；plan_tracker 按 cluster 内容验收。

---

## 四、核心系统目录（`core/claude-home/`）

```
core/claude-home/
├── plans/                                      # plan_tracker 强制规划模板（<command>.plan.json）
│   ├── README.md                               # plan 字段词表（主代理 checklist 字段说明）
│   ├── cluster-write.plan.json
│   ├── cluster-save-state.plan.json
│   ├── distill-style.plan.json
│   └── ...
├── hooks/                                      # Pre/Post tool hooks
│   ├── pretooluse_agent_gate.py
│   └── ...
├── templates/                                  # 通用模板（跨项目共享）
│   ├── subsystem_skeletons.json                # 34 核心子系统骨架单一真理源
│   ├── genre_baseline.json / genre_dimension_packs.json  # 题材分层基线
│   ├── SUBSYSTEM_FRAMEWORK.md                  # 子系统设计框架说明
│   ├── 平台冷启动SOP.md                        # 一人公司发布节奏 SOP
│   └── examples/                               # 完整题材 schema 范例包（见下）
│       ├── scp_anomaly_bureau/                 # SCP/异常局/多身体同步题材（8 个 example JSON + README）
│       ├── urban_supernatural_business/        # 都市超自然商战题材（9 个 example JSON + README）
│       └── _subsystem_examples/                # 前两包未覆盖的其余高级子系统单文件范例（9 个）
├── schemas/                                    # JSON Schema 契约定义
│   ├── changes_schema.json
│   ├── cluster_state_delta_schema.json
│   ├── event_cluster_schema.json
│   └── user_preferences_schema.json
├── knowledge/                                  # 题材/技法/世界观知识库（jsonl，scanner 消费）
│   ├── genre/ · technique/ · worldbuilding/ · research/
├── skills/                                     # 【预留】当前仅占位，无实质内容
├── lessons/                                    # 经验库（自学习）
│   ├── distill-style-lessons.md               # 蒸馏教训
│   └── ...
├── CLAUDE.md                                   # Agent 入口补充约束（sub-agent 读取，不重复根 CLAUDE.md）
├── SELF_LEARNING_ARCHITECTURE.md               # MAPE-K 运行时自学习层权威设计文档
├── universal_skill_pool.json                   # 跨题材通用写作技法池
└── STRUCTURE.md                                # 本文档
```

> 命令文档的**唯一权威**位置是 `.claude/commands/`。`core/claude-home/` 不设 commands 副本；改命令文档只改 `.claude/commands/`，系统运行时也只加载这里。
>
> **Agent 定义的唯一权威**位置是 `.claude/agents/`。`core/claude-home/CLAUDE.md` 只是 sub-agent 读取的补充约束文件，不是 agent 定义本体。

---

## 四-bis、运行入口

用户入口是 **Claude Code CLI**。主代理通过 `.claude/commands/` 命令文档、`core/claude-home/plans/` plan 模板、subagent prompt 与 `core/scripts/` 确定性脚本驱动写作管线。

创作主链路只允许：

```
/write -> /outline -> /cluster-write -> /cluster-save-state -> cluster 走向卡 -> /export
```

任何新增能力必须落入这条链路的正式步骤或子步骤，不设旁路入口。

---

## 四-ter、脚本、模型、数据与测试层

```
core/scripts/                                   # 运行时确定性代码
├── gen_*.py / distill_*.py                     # 润色/蒸馏与 agent 亲笔产物确定性验收（gen_creative 零 LLM）
├── cluster_*.py / save_state*.py               # cluster 编排、涌现与状态回库
├── *_scanner.py / cross_cluster_*.py            # cluster 与跨 cluster 顾问扫描
├── plan_*.py / adaptive_runner.py               # plan、门禁、恢复与自学习运行时
├── skill_opt/                                   # SkillOpt 训练循环
└── lexicons/                                    # 脚本消费的文本词表

core/ml/                                        # 神经网络模型训练/推理
├── emotion_vad/ · style_embed/ · quality_clf/   # 训练 pipeline 三件套（各含 data_prep/train/eval/infer + README/INTEGRATION.md）
├── coherence/ · content_embed/ · nli/ · surprisal/  # 推理侧（*_infer.py，daemon 懒加载调用）
├── daemon/model_daemon.py                       # 常驻推理服务（5 类模型驻内存·热路径 0.04-0.13s）
├── registry/                                    # ModelRegistry（active/shadow 版本、路径、指标）
├── feature_store/                               # 统一特征缓存（embedding/surprisal/VAD/coherence）
├── flywheel/                                    # DataFlywheel 数据飞轮（cluster-save-state 后台采集训练样本）
├── calibration/                                 # 语义阈值/统计阈值校准 harness + 历史校准报告
├── LEARNABLE_BACKLOG.md                         # 可成长模型化 backlog（滚动更新的权威进度记录）
└── TRAINING_RESULTS.md                          # 各模型训练结果记录

core/data/                                       # scanner/core/ml 消费的系统词典 JSON

tests/                                           # pytest 回归测试，按 test_<module>.py 对应系统模块
```

`core/ml/` 消费方式：`core/scripts/nn_*_bridge.py`（vad/coherence/nli/surprisal）+ `embedding_store.py`（style_embed/content_embed）经 subprocess 或 daemon HTTP 调用 `core/ml/` 侧推理脚本，系统主 Python（无 torch）与 `core/ml/.venv`（torch CUDA）进程隔离。

---

## 五、文件命名规范

### 5.1 通用规则
- **目录名**：中文为主（用户可读），如 `蒸馏进度/`、`衔接分析/`
- **数据 JSON**：英文 + 章节号，如 `ch5.json`、`eval_v0.5_battle.json`
- **用户可读文件**：中文 + 章节号，如 `第5章.txt`、`第5章_摘要.md`
- **临时文件**：`_tmp/` 前缀
- **归档文件**：`_archive_v{X}/` 前缀

### 5.2 章节号格式
- **章节 JSON**：`ch{N}.json`（如 `ch1.json`、`ch200.json`，**不补零**）
- **章节文件**：`第{N}章.txt`（中文化）
- **章节范围**：`ch{N}_{N+2}` 连字符表示连续，如 `ch1_3_continuity.json`

### 5.3 版本号格式
- 蒸馏 skill：`v{major}.{minor}[.{patch}]`，最多三段
  - 例：`v0.1` / `v0.5` / `v0.5.2`
- 复刻测试：`v{X}_round{Y}` 表示某版本的第 Y 轮
  - 例：`v0.5.1_round2/`
- 终版：`_FINAL` 后缀（如 `作者风格_FINAL.json`）

### 5.4 类型标识符
复刻测试 / 对比报告中的 type：
- `opening` — 开场段
- `battle` — 战斗段
- `psychology` — 内心独白段
- 未来扩展：`dialogue` / `description` / `transition`

### 5.5 评估文件
- SFS 量化报告：`eval_v{X}_{type}.json`
- 多维度差距：`diff_v{X}_{type}.md`
- 对照原文：`ref_ch{N}_{type}.txt`

---

## 六、Git 管理边界

| 路径 | Git 仓库归属 | 提交策略 |
|---|---|---|
| `workspace/styles/{书名}/` | **项目根 Git**（`<REPO_ROOT>/.git`） | 蒸馏完成时由 `/distill-style` 提交 |
| `workspace/novels/{书名}/` | **独立 Git**（`workspace/novels/{书名}/.git`） | 每个 cluster 完成 `/cluster-save-state` 时提交 |
| `core/` | **项目根 Git** | 系统改动时手动 commit |
| `_reference/` | 默认 ignore | 不入 Git |
| `_tmp/` | 默认 ignore | 不入 Git |
| `_archive_v{X}/` | 入 Git（保留历史） | 升级时提交 |

**Git 安全规则**（来自 CLAUDE.md）：
- 所有 git 调用必须用 `command -v git >/dev/null 2>&1 && [ -d ".git" ]` 预检
- 路径含中文，`cd` 或 `-C` 时必须加双引号
- ❌ 不做 push/pull/force/reset --hard
- ❌ 不修改全局 git config，只设本地 user.name/email

---

## 七、跨项目数据流

```
全局风格库（workspace/styles/{书名}/）
    │
    │ /write 启动新项目时复制：
    │ cp 作者风格_FINAL.json → 小说项目/_数据库/作者风格.json
    ↓
小说项目（workspace/novels/{书名}/）
    │
    │ 章节产出 + 反思 + 经验
    ↓
跨项目经验沉淀
    ├─→ core/claude-home/lessons/distill-style-lessons.md（蒸馏教训）
    └─→ core/claude-home/lessons/{command}-lessons.md（其他命令教训 · 未来）
```

---

## 七-bis、CoALA 记忆四分类（34 子系统记忆角色）

> **来源**：记忆与小说数据存储调研 R3 蓝图 A1（`workspace/_temp_research/记忆与小说数据存储调研/`）。CoALA = Cognitive Architectures for Language Agents 记忆四分类，标注每个子系统的记忆角色，供未来改记忆层 / 加子系统时参照（避免乱加破架构）。**纯文档地基 · 不改运行时行为**。
>
> 四类定义：**Episodic** 经历事件流（按时序的事件日志）/ **Semantic** 世界事实知识（设定 / 角色 / 关系）/ **Procedural** 程序规则模板（剧情骨架 / 触发器 / 经验规则）/ **Working** 当前工作上下文（每 cluster 变的状态快照）。

| # | 子系统 JSON | CoALA 记忆类型 |
|---|---|---|
| 1 | 人物卡 | **Semantic** |
| 2 | 世界观 | **Semantic** |
| 3 | 关系 | **Semantic** |
| 4 | 地图 | **Semantic**（+Working: positions） |
| 5 | 道具 | **Semantic** |
| 6 | 进度 | **Working** |
| 7 | 故事块摘要 | **Episodic**（cluster 账本主存储） |
| 8 | 大势卡 | **Procedural**（剧情骨架 / 牵引规则） |
| 9 | 事件簇 | **Procedural**（writer 工单） |
| 10 | 事件表 | **Procedural**（触发器） |
| 11 | 时间线 | **Episodic**（+Working: current_time） |
| 12 | 伏笔表 | **Procedural**（待兑现契约） |
| 13 | 作者风格 | **Semantic**（风格知识 · 第一权威） |
| 14 | 场景规则 | **Procedural** |
| 15 | 写作经验 | **Procedural**（Learning Loop 经验规则） |
| 16 | 用户偏好 | **Procedural** |
| 17 | 世界状态 | **Working**（当前世界快照 · 涟漪 delta） |
| 18 | 涟漪规则 | **Procedural**（因果规则 · 北极星②核心） |
| 19 | 枢纽场景 | **Semantic**（+Procedural: rhythm） |
| 20 | 时钟表 | **Working**（剧情时钟倒计时） |
| 21 | 叙事节拍器 | **Procedural** |
| 22 | 主角压力档 | **Working**（stress 累计） |
| 23 | character_arc_state | **Procedural**（弧线规则）+Working（stage） |
| 24 | 角色行动表 | **Episodic**（NPC 幕后事件流） |
| 25 | 群像档 | **Semantic** |
| 26 | 事件池 | **Procedural**（候选池）+Episodic（drawn_log） |
| 27 | 行动判定模板 | **Procedural** |
| 28 | 角色池 | **Semantic** |
| 29 | 角色烙印 | **Semantic**（voice DNA 5 层） |
| 30 | knowledge_graph | **Semantic**（图结构 · 空骨架待填） |
| 31 | subplot_threads | **Procedural**（线索追踪） |
| 32 | beat_map | **Procedural**（scanner 专用不注入 writer · `cluster_beats[cluster_id]` 由 `beat_map_update.py` 据 scene_storyboard 确定性派生·`cluster_choice_apply` 落库时接通·plot_structure_scanner CLUSTER_MODE 消费·advisory） |
| 33 | 四线脉络 | **Procedural**（Dramatica 四贯穿线） |
| 34 | webnovel_bench_mapping | **Procedural**（评估维度） |

**分布速览**：Semantic ~12、Procedural ~16、Working ~6、Episodic ~3（部分跨类）。**Episodic（经历事件流）偏薄**——故事块摘要 / 时间线 / 角色行动表勉强算，但缺「按时序连续可检索的事件日志」标准 Episodic 形态（`memory_layer.py` 的 cluster/summary/archive 三层是另一套独立检索记忆，未进 34 子系统）。**加新子系统时优先补 Episodic 缺口、勿无脑堆 Procedural。**

---

## 八、Active Contract

当前系统只维护以下 active 路径：

| 类型 | 唯一路径 |
|---|---|
| 风格库 | `workspace/styles/{书名}/` |
| 小说项目 | `workspace/novels/{书名}/` |
| 命令文档 | `.claude/commands/` |
| Agent 定义 | `.claude/agents/` |
| Plan 模板 | `core/claude-home/plans/` |
| 确定性脚本 | `core/scripts/` |

创作链路 active contract：

```
/write -> /outline -> /cluster-write -> /cluster-save-state -> cluster 走向卡 -> /export
```

禁止在文档、脚本或 agent 合约中新增章级写作入口、章级状态回库、章级走向卡、fallback、no-op、双形兼容或链路外孤立脚本。

---

## 九、命令产出对照表

每个命令的产出位置（权威表）：

| 命令 | 主要产出 | 路径 |
|---|---|---|
| `/distill-style` | 风格基线 / skill / 衔接分析 / 复刻测试 / 对比报告 / distillation_log | `workspace/styles/{书名}/` |
| `/distill-character` | 角色 voice DNA | `workspace/styles/{书名}/角色档案/{角色名}.json` |
| `/outline` | 大纲 / 34 个数据库 JSON；灵感卡与卷级大纲单元由 `novel-outline-planner` 亲笔（MODE=brainstorm → `.wal/inspiration_cards.json`；MODE=volume_arc_unit → `.wal/volume_arc_skeleton.json` + `.wal/volume_arc_v<N>.json`），`gen_creative.py` 确定性验收（缺件/破损 → `.wal/volume_arc_jobs.json` + exit 2=pending）并合并 emit `大势卡.json` + `事件簇.json` | `workspace/novels/{书名}/_数据库/` |
| `/cluster-write` | cluster 整块草稿 + 切章物理文件 + 章标题 + 平铺 CHANGES；章标题由 `gen_chapter_titles.py --emit-brief`（确定性产 `.wal/cluster_<key>_title_brief.json`）→ `novel-titler` 亲笔（→ `.wal/cluster_<key>_titles.json`）→ `--apply` 确定性验收（干净度/≤14 字/历史查重·退回章 exit 2=pending_titles·全过落 `.wal/cluster_<key>_title_apply_receipt.json` 并回填 blueprint） | `workspace/novels/{书名}/章节/cluster_<key>_draft/cluster_<key>_draft.txt` → splitter 切出 `第{N}章/第{N}章.txt` + `第{N}章_changes.json` |
| `/cluster-save-state` | cluster 摘要 / 反思 / 走向卡 + 下块 brief；实体归档由 `novel-archivist`→`archive.json`→`apply_archive` 回库，运行态由 `novel-state-tracker`→`cluster_state_delta.json` + 独立回执→`cluster_state_delta.py` 回库 | `workspace/novels/{书名}/_数据库/故事块摘要.json` + `_数据库/.wal/cluster_<key>_*` |
| `/export` | 拼接全文（`export_book.py` 硬校验通过后写出） | `workspace/novels/{书名}/exports/<书名>_全文_<章数>章.txt` |

---

## 十、给开发者的强约束

### 10.0 唯一链路可扩展
可以把验证过的成熟功能、模型或论文/开源机制加入创作系统，但必须落在 `/write -> /outline -> /cluster-write -> /cluster-save-state -> 走向卡 -> /export` 的正式步骤或子步骤中，并同步命令文档、plan 模板、agent 合约、STRUCTURE/CLAUDE 描述和测试。不得新增链路外脚本、旁路命令、双形兼容、章级写作入口或绕过 `/cluster-save-state` 的直接写库 CLI。

### 10.1 命令文档必须引用本文档
所有命令文档（`*.md`）的"输出路径"部分必须写：
```markdown
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
```

### 10.2 Agent prompt 必须用绝对路径
不要在 prompt 里写相对路径如 `蒸馏进度/ch{N}.json`，必须写完整绝对路径：
```
<REPO_ROOT>/workspace/styles/{书名}/蒸馏进度/ch{N}.json
```

### 10.3 路径变量化（未来）
未来引入 `$STYLE_DIR`、`$PROJECT_DIR` 环境变量统一替代硬编码绝对路径。

---

## 九-bis、调研缓存目录（novel-researcher agent 产出）

`workspace/novels/{书名}/_数据库/.research_cache/`

每次 spawn `novel-researcher` 时强制写入此目录，文件命名规范：

```
.research_cache/
├── inspiration_{topic_slug}_{YYYYMMDDHHMM}.md   # /write 第 2 步前调研
├── outline_{topic_slug}_{YYYYMMDDHHMM}.md       # outline-planner 前调研
├── character_{role_name}_{YYYYMMDDHHMM}.md      # /distill-character 前调研
└── fact_check_{topic}_{YYYYMMDDHHMM}.md         # 关键事实校验
```

**生命周期**：
- 24 小时内同 task_type + topic_slug 可复用（researcher agent 内部判定）
- 超过 30 天的 cache 由用户手动清理（或加 `/research-cleanup` 命令）
- 不入 git（已在项目 .gitignore 中加 `_数据库/.research_cache/`）

**为什么不入 git**：
- 调研结果是临时知识源 + URL 引用，本身不是项目内容
- URL 可能含失效链接 / 网络小说被删，git 历史保留无意义
- 真正有价值的"调研结论"已经被 writer / outline-planner 融合进章节正文 + cluster_blueprint，可追溯

---

## 十一、Cluster 产物契约与审计边界

### 11.1 Cluster 草稿与 splitter 产物

`/cluster-write` 的权威正文入口是 cluster 草稿：

| 文件 | 标准路径 | 内容 | 由谁产出 |
|---|---|---|---|
| cluster 草稿 | `章节/cluster_<key>_draft/cluster_<key>_draft.txt` | 整块正文终稿（v29=Claude 亲笔+gemini 润色） | `novel-writer`(2a 亲笔) + `gen_writer.py`(2b 润色) |
| Claude 亲笔场景稿 | `章节/cluster_<key>_draft/claude_scenes/scene_*.txt` + `cluster_<key>_draft_claude.txt` | v29 step 2a 产物（润色输入+审计基线） | `novel-writer` agent |
| cluster changes | `章节/cluster_<key>_draft/cluster_<key>_changes.json` | 创作期自评、waivers、确定性遥测 | `novel-writer` / cluster-write 调度器 |
| pending tail | `章节/cluster_<key>_draft/cluster_<key>_pending_tail.txt` | splitter 退回的不足字数尾段 | splitter |

splitter 在 `/cluster-write` step 6 才把草稿切为用户可读章节：

| 文件 | 标准路径 | 内容 | 由谁产出 |
|---|---|---|---|
| 章节正文 | `章节/第NNN章/第NNN章.txt` | 纯正文，无 CHANGES、无分隔符 | splitter |
| 章节数据 | `章节/第NNN章/第NNN章_changes.json` | 从 cluster changes 平铺的章节级镜像 | splitter |

章节文件是导出和阅读产物，不是创作入口。验证、修复、状态保存都以 cluster 为单位回到草稿层或数据库层处理。

### 11.2 状态回库

cluster 级客观状态变更的权威源是 Claude 梳理 + 确定性回库：

- 角色 / 道具 / 关系 / `locked_facts`：`novel-archivist` 读整 cluster 正文产 `_数据库/.wal/cluster_<key>_archive.json`，再由 `apply_archive.py <项目> --cluster <key>` 幂等回库。
- 时间 / 地点 / Hub / 世界事件与消费：`novel-state-tracker` 产 `_数据库/.wal/cluster_<key>_state_delta.json` 与独立 `_数据库/.wal/cluster_<key>_state_tracker_receipt.json`；回执绑定 plan/step/delta SHA-256，`cluster_state_delta.py <项目> --cluster <key>` 严格校验并幂等回库。
- 伏笔：`novel-foreshadower` 的 JudgeReport + outline brief 梳理，非 writer 自报。

`cluster-save-state` step 11 的完成证明是 `_数据库/.wal/cluster_<key>_post_state_receipt.json`。`cluster_post_state_receipt.py` 只在本 cluster 的状态更新/评估回执、全量跨块 wrapper、世界演化回执和 Judge consensus 决策均通过内容校验后生成，并按字节 SHA-256 绑定这些动态产物；常驻数据库文件不能替代 required 执行证明。

禁止绕过 `/cluster-save-state` 做章级状态回库。

---

## 十二、Cluster 审计 + hard_gate 不可豁免清单

本节是 hard_gate 清单的**权威定义**，所有工具、agent、命令引用此清单，不得另立。

### 12.1 审计边界

cluster 草稿阶段统一以 `audit_hub.py --mode cluster --cluster-id <key>` 为审计入口；`validate_style`、`narrative_scanner`、`plot_structure_scanner`、`semantic_slop_scanner`、`hook_strength_scanner`、`golden_three_scanner` 等 scanner 作为 cluster 审计的组成部分。

- 每条 issue 带 `gate_level` 字段，取值 `"hard_gate"` 或 `"advisory"`。
- **advisory 项**：风格 / 文笔 / 叙事工艺 / 情节结构 / 读者体验层的检测项。AI（writer / validator / novel-voice-checker / foreshadower）有充分理由可以豁免；豁免必带具体理由（< 100 字、具体到 cluster 场景），理由不充分则豁免无效。
- **hard_gate 项**：E 层一致性 + 文件契约破损。这是**客观错误**，不是风格选择，**AI 不可豁免**——即便在 `--waivers` 里传了豁免理由，audit_hub 也强制忽略豁免，仍按问题处理。
- 豁免理由的载体：writer 写在 `cluster_<key>_changes.json` 的 `self_eval.waivers: [{code, reason}]`；judge agent 写在 JudgeReport 的 `waivers` 段。
- `audit_hub.py` 通过 `--waivers <json路径>` 入参收集豁免；对 advisory 项命中豁免 → 转 `waived`（记 `waive_reason`），不计入 `needs_agent`；剩余 issue 全是被合理豁免的 advisory 且无 hard_gate 残留 → verdict = `waived`，并把理由写入审计报告供后续校准。
- `learning_loop.py` 只消费 `cluster_<key>_audit.json` 与 `cluster_<key>_reflection.json`。`写作经验.json` 的来源、复发、豁免和 efficacy 字段统一使用 `source_clusters` / `clusters` / `baseline_clusters` / `post_recur_clusters`；同一 advisory code 在同类 cluster 被反复合理豁免时，产出 `tool_calibration_suggestions`，无效约束停止注入下一 cluster。

禁止把章级验证脚本作为创作链路入口；章节物理文件只由 splitter 产出，不能成为修复或回库旁路。

### 12.2 hard_gate 不可豁免清单（权威）

以下 18 个 code 是 hard_gate，**AI 不可豁免**。与 `core/scripts/audit_hub.py` 的 `HARD_GATE_CODES` 常量一一对应（改清单必须两边同步）。这些 code 一旦在 cluster 审计中以 hard_gate 进入结果，必须修复，不做 no-op、fallback 或 advisory 降级。

| 维度 | code 数量 |
|---|---|
| E 层一致性 | 5（LOCKED_FACT_CONFLICT / FUTURE_KNOWLEDGE_LEAK / FORESHADOWING_NOT_PAID / SECRET_NOT_REVEALED / UNKNOWN_CHARACTER_DETECTED） |
| 文件契约 | 3（CHANGES_MISSING / MANIFEST_MISSING / FILE_NOT_FOUND） |
| 道具状态 | 2（ITEM_HOLDER_ABSENT / ITEM_NOT_YET_INTRODUCED） |
| 移动阅读体验 | 1（STYLE_单段超长） |
| 章末工艺 | 2（CHAPTER_END_FORBIDDEN_SCREENPLAY / CHAPTER_END_FORBIDDEN_TRANSITION） |
| cluster 跨场景一致性 | 1（LOCKED_FACT_CROSS_SCENE_CONFLICT） |
| 子系统载荷点火 | 3（RIPPLE_RULES_EMPTY / GRAND_TREND_ME_POOL_EMPTY / CLUSTER001_STORYBOARD_EMPTY） |
| splitter 字数守恒 | 1（SPLIT_WORD_NOT_CONSERVED） |


| code | 来源 | 类别 | 为什么不可豁免 |
|---|---|---|---|
| `LOCKED_FACT_CONFLICT` | audit_hub cluster | E 层一致性 | cluster 草稿与已锁定事实冲突 = 设定矛盾 |
| `FUTURE_KNOWLEDGE_LEAK` | audit_hub cluster | E 层一致性 | 角色知道不该知道的 = 逻辑错误 |
| `FORESHADOWING_NOT_PAID` | audit_hub cluster | E 层一致性 | Tier-1 到期伏笔未回收 = 对读者的承诺违约 |
| `SECRET_NOT_REVEALED` | audit_hub cluster | E 层一致性 | 秘密该揭未揭 = 剧情债 |
| `UNKNOWN_CHARACTER_DETECTED` | audit_hub cluster | E 层一致性 | 引用未声明实体 = 引用错误；分词误检应修检测器或证据，不当普通 advisory 豁免 |
| `CHANGES_MISSING` | audit_hub cluster | 文件契约 | cluster changes 或平铺 changes 缺失 = 文件契约破损 |
| `MANIFEST_MISSING` | audit_hub cluster | 文件契约 | manifest 缺失 = 文件契约破损 |
| `FILE_NOT_FOUND` | audit_hub cluster | 文件契约 | cluster 草稿或必要产物缺失 = 文件契约破损 |
| `ITEM_HOLDER_ABSENT` | audit_hub cluster | 道具状态 | 道具持有者不在场 = 道具状态矛盾 |
| `ITEM_NOT_YET_INTRODUCED` | audit_hub cluster | 道具状态 | 道具尚未引入就被用 = 道具状态矛盾 |
| `STYLE_单段超长` | validate_style in audit_hub | 移动阅读 | 单段 > 120 CJK 字（物理章节 ≤1 例外）= 移动阅读硬上限；不允许 AI 豁免单条 |
| `CHAPTER_END_FORBIDDEN_SCREENPLAY` | chapter_end_anchor_scan | 章末工艺 | 章末出现剧本体过渡（「（镜头XX）」等舞台指示）= 连续小说工艺破坏。**为什么不可豁免**：章末是钩子不是收束，不允许场景过渡打断。详见 memory `feedback_no_screenplay_stage_directions_in_novels` |
| `CHAPTER_END_FORBIDDEN_TRANSITION` | chapter_end_anchor_scan | 章末工艺 | 章末出现文学过渡分隔符 / 听觉视觉淡出 / 收束句 = 移动阅读 cliffhanger 工艺破坏。**为什么不可豁免**：同上，章末不允许任何场景过渡收束 |
| `LOCKED_FACT_CROSS_SCENE_CONFLICT` | locked_fact_cross_scene_scanner | cluster 跨场景一致性 | 人物卡 `locked_facts` 中的数值/描述类事实在 cluster 不同场景引用矛盾（如同角色年龄两处不符）= cluster 内设定矛盾。**为什么不可豁免**：与 `LOCKED_FACT_CONFLICT` 同级，是客观设定冲突非风格选择 |
| `RIPPLE_RULES_EMPTY` | scaffold_subsystems verify --content / plan_step_gates.check_subsystems | 子系统载荷点火 | `涟漪规则.json` 的 `ripple_rules` 为空 = `world_evolution_engine` 零触发，涟漪核心机器永不点火（北极星②）。**为什么不可豁免**：性质同 `MANIFEST_MISSING`——不是风格选择而是机器无法运转的客观断点。其余子系统必须按字段契约显式定级；轻量模式也必须保留核心载荷文件与可验证结构 |
| `GRAND_TREND_ME_POOL_EMPTY` | scaffold_subsystems verify --content / plan_step_gates.check_subsystems | 子系统载荷点火 | `大势卡.json` 的 ME 池 `major_events` 为空 = `cluster_emergence_engine` 大势无方向，无法涌现下一 cluster（北极星③大势已定）。**为什么不可豁免**：性质同 `MANIFEST_MISSING`。**回归锁**：只查池非空，不逐 cluster 校验，cluster_002+ 未涌现 ME 属 fluid 合法显式豁免 |
| `CLUSTER001_STORYBOARD_EMPTY` | scaffold_subsystems verify --content / plan_step_gates.check_subsystems | 子系统载荷点火 | `事件簇.json` 的 `clusters[0].scene_storyboard` 为空 = 首块未详化（黄金三章必详化）。**为什么不可豁免**：cluster_001 是唯一必须 outline 阶段详化的块。**回归锁**：标记只查 `clusters[0]`，cluster_002+ 空 storyboard 属 fluid 涌现合法显式豁免（北极星·事件簇 fluid 涌现） |
| `SPLIT_WORD_NOT_CONSERVED` | chapter_splitter.run_freestyle | splitter 字数守恒 | splitter 切章后 `sum(per_chapter_cjk) + pending_tail_cjk != draft_cjk`（丢字/重复）或落盘空 chunk 或切片计数失配 = 北极星④纯格式层契约破损。**为什么不可豁免**：splitter 是纯格式层（切章后 0 audit），却必须 round-trip 完整——丢字/重复无人守。落盘前确定性自检 raise `SplitterIntegrityError` → main `[FATAL]` stderr → exit 2（坏章节零落盘·cluster-write step6 fail-fast）。**北极星⑤边界**：只查 CJK 守恒 + 无空块 + 计数同步，**绝不断言章数 N（fluid 章数由字数涌现决定，禁止锁定）/ 切点质量 / 叙事顺序 / 任何内容判断** |

**非 hard_gate 检测项必须显式定级**：A 机械（`BANNED_WORD` / `WC_TOO_SHORT` / `WC_TOO_LONG` 等）/ B 文笔（`validate_style` 检测项）/ B+ 文笔语义层（`semantic_slop_scanner` 检测项）/ C 叙事工艺（`narrative_scanner` 检测项）/ D 情节结构（`plot_structure_scanner` 检测项）/ F 读者体验（`hook_strength_scanner` / `golden_three_scanner` / `HOOK_SUMMARY_ENDING` 等）如果属于风格/工艺/体验建议，可定为 advisory 并要求具体豁免理由；如果属于契约、状态、完整性或客观一致性，必须进入 hard_gate 清单或在对应工具内硬失败。

### 12.3 强约束

- **hard_gate 清单是权威单一来源**——`audit_hub.py` 的 `HARD_GATE_CODES`、各 agent 定义里的 hard_gate 清单、各命令文档，全部引用本节，不得各自另立或增减。
- **改 hard_gate 清单 = 改本节 + 改 `audit_hub.py` 的 `HARD_GATE_CODES`**，两边必须同步，否则文档与实现脱节。
- 新增检测器接入时必须显式定级；契约、状态、完整性、客观一致性默认 hard 或纳入本节 hard_gate 清单，只有风格/工艺/体验建议可以定为 advisory。

**维护责任**：本文档由 `/distill-style`、`/outline`、`/write`、`/cluster-write`、`/cluster-save-state`、`/export` 等命令共同遵守。如有路径冲突，**以本文档 active contract 为准**。
