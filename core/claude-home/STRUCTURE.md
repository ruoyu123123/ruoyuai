# 若渝AI · 文件目录与命名规范（v1.0）

> 整个小说系统的目录框架和命名规范。所有命令产出文件必须遵守本规范。
> 本文档优先级：**高于各命令文档**。命令文档应反向引用本文档。

---

## 一、顶层目录全景

```
<REPO_ROOT>/                          # 项目根（Claude Code working directory）
├── .claude/                                   # Claude Code 配置层
│   ├── commands/                              # 用户级命令定义
│   ├── agents/                                # Subagent 定义（judge prompt 单一真理源）
│   ├── templates/                             # 命令调用模板
│   └── settings.json                          # 项目设置
├── core/                                       # 系统核心代码
│   ├── claude-home/
│   │   ├── plans/                              # plan_tracker 强制规划模板
│   │   ├── hooks/                              # PreToolUse hooks
│   │   ├── agents/                             # Subagent 定义
│   │   ├── templates/                          # 通用模板
│   │   ├── lessons/                            # 蒸馏经验库
│   │   └── STRUCTURE.md                        # 【本文档】
│   └── scripts/                                # Python 工具脚本
├── workspace/                                  # 【用户产出区】
│   ├── styles/{书名}/                          # 全局风格库（见第二节）
│   └── novels/{书名}/                          # 小说项目（见第三节）
└── CLAUDE.md                                   # 项目级 AI 指令
```

**铁律**：
- ⛔ 用户产出文件**永远不要**写到 `core/` 下（core 是系统代码，不是数据）
- ⛔ 临时文件**永远不要**写到 `.claude/` 根目录（污染配置层）
- ✅ 风格库 = `workspace/styles/{书名}/`
- ✅ 小说项目 = `workspace/novels/{书名}/`
- ✅ 系统经验 = `core/claude-home/lessons/`

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
├── agent_brief_3ch.md                         # 本项目的 3 章 agent 模板
├── lessons_learned_v{N}.md                    # 各版本反思日志
│
├── 蒸馏进度/                                   # 单章蒸馏 JSON
│   ├── ch{N}.json                             # 单章完整蒸馏
│   ├── ch{N}_metrics.json                     # style_analyzer 量化输出
│   └── ...
│
├── 衔接分析/                                   # 章际衔接 JSON（v17 新增）
│   ├── ch{N}_{N+2}_continuity.json            # 3 章窗口衔接分析
│   └── ...
│
├── 复刻测试/                                   # 闭环阶段 2 产物
│   ├── v{X}/                                  # 第 X 版 round 1
│   │   ├── {type}_replica.txt                 # type ∈ {opening, battle, psychology}
│   │   ├── {type}_replica_meta.json
│   │   ├── {type}_replica_metrics.json
│   │   └── {type}_replica_eval.json
│   └── v{X}_round{Y}/                         # 第 X 版第 Y 轮
│
├── 对比报告/                                   # 闭环阶段 3 产物
│   ├── eval_v{X}_{type}.json                  # SFS 量化报告
│   ├── diff_v{X}_{type}.md                    # 多维度差距报告
│   ├── ref_ch{N}_{type}.txt                   # 对照原文片段
│   └── ...
│
├── 原文/                                       # 【可选】原文备份
│   └── 第{N}章.txt                            # 用户可读的中文命名
│
├── _tmp/                                       # 临时文件（agent 中间产物）
│   └── ch{N}.txt                              # 章节正文临时副本
│
└── _archive_v{X}/                              # 旧版本归档（升级时移入）
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
│   ├── .wal/                                  # WAL 写前日志（cluster 流水线）
│   └── .lock                                  # 项目锁
│
├── 章节/                                       # cluster 草稿 + splitter 后章节产物
│   ├── cluster_{key}_draft/
│   │   ├── cluster_{key}_draft.txt             # 整块正文草稿（writer 产物）
│   │   ├── cluster_{key}_changes.json          # writer 创作期自评 + waivers + 确定性遥测
│   │   └── cluster_{key}_pending_tail.txt      # 【v27】末章不足 3000 CJK 退回的尾段（等下 cluster 拼）
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

**【v18 正文/数据分离】**：
- `第{N}章.txt` = **纯正文**，`第{N}章_changes.json` = **结构化数据**，两者是两个物理文件，不再混在一个 txt 里靠 `---CHANGES---` 分隔符切。
- `第{N}章_changes.json` 由 `novel-chapter-splitter`（cluster mode · splitter 在 step 6 从 `cluster_<key>_changes.json` 按切点平铺成 per-chapter）产出，不再由 save-state 产出。
- 所有读写章节正文 / CHANGES 的脚本必须走 `core/scripts/chapter_io.py` 统一模块（`read_body` / `read_changes` / `write_body` / `write_changes`），禁止各自 split。
- `第{N}章_changes.json` 顶层两键：`self_eval`（writer 创作自评 applied_style / `waivers` 等，judge 默认不读，v17.4 分权纪律）+ `factual`（确定性遥测如字数 + archivist 回库后的客观状态派生镜像，**非 writer 自报权威源**）。
- **🔴 2026-06-28 架构纠正（北极星⑥不留双口径）**：配置的写作模型（gen-model writer）**只产正文**、**不自报"改了什么"**。cluster 级客观状态（角色 / 道具 / 关系 / `locked_facts` / 伏笔）的**权威源**由 Claude 梳理、确定性脚本回库——**不再以 writer 的 `changes.factual` 自报为准**：
  - **角色 / 道具 / 关系 / `locked_facts`**：`novel-archivist` 读整 cluster 正文客观抽取 → `_数据库/.wal/cluster_<key>_archive.json` → `apply_archive.py <项目> --cluster <key>` 确定性回库（幂等·按 id 去重）。
  - **伏笔**：`novel-foreshadower` 的 JudgeReport + `outline` brief（plant / payoff 由 Claude 梳理，非 writer 自报）。
  - `save_state` 已停读 writer factual；`time_advance` / `location` 等**非 archive 域**仍由 `save_state --apply-cluster-changes` 落地。

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
├── agents/                                     # Subagent 定义
│   ├── novel-writer.md
│   ├── novel-summarizer.md
│   └── ...
├── templates/                                  # 通用模板（跨项目共享）
│   ├── distill_3ch_agent_brief.md             # 3 章蒸馏 brief
│   └── ...
├── lessons/                                    # 经验库（自学习）
│   ├── distill-style-lessons.md               # 蒸馏教训
│   ├── EXTRACTOR_PROMPT.md                    # 自动提取 prompt
│   └── ...
└── STRUCTURE.md                                # 本文档
```

> 命令文档的**唯一权威**位置是 `.claude/commands/`。`core/claude-home/` 不设 commands 副本；改命令文档只改 `.claude/commands/`，系统运行时也只加载这里。

---

## 四-bis、运行入口

用户入口是 **Claude Code CLI**。主代理通过 `.claude/commands/` 命令文档、`core/claude-home/plans/` plan 模板、subagent prompt 与 `core/scripts/` 确定性脚本驱动写作管线。

创作主链路只允许：

```
/write -> /outline -> /cluster-write -> /cluster-save-state -> cluster 走向卡 -> /export
```

任何新增能力必须落入这条链路的正式步骤或子步骤，不设旁路入口。

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

**分布速览**：Semantic ~12、Procedural ~16、Working ~6、Episodic ~3（部分跨类）。**Episodic（经历事件流）偏薄**——故事块摘要 / 时间线 / 角色行动表勉强算，但缺「按时序连续可检索的事件日志」标准 Episodic 形态（`memory_layer.py` 的 chapter/summary/archive 三层是另一套独立检索记忆，未进 34 子系统）。**加新子系统时优先补 Episodic 缺口、勿无脑堆 Procedural。**

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
| `/outline` | 大纲 / 34 个数据库 JSON | `workspace/novels/{书名}/_数据库/` |
| `/cluster-write` | cluster 整块草稿 + 切章物理文件 + 平铺 CHANGES | `workspace/novels/{书名}/章节/cluster_<key>_draft/cluster_<key>_draft.txt` → splitter 切出 `第{N}章/第{N}章.txt` + `第{N}章_changes.json` |
| `/cluster-save-state` | cluster 摘要 / 反思 / 走向卡 + 涌现下个 cluster brief（factual 客观状态由 `novel-archivist`→`archive.json`→`apply_archive` 确定性回库·`time_advance`/`location` 等非 archive 域由 `save_state --apply-cluster-changes` 落地·均**非 writer 自报**） | `workspace/novels/{书名}/_数据库/故事块摘要.json` + `章节/cluster_<key>_draft/` 伴生文件 |
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

## 九-bis、调研缓存目录（v17.7 引入 · novel-researcher agent 产出）

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
| cluster 草稿 | `章节/cluster_<key>_draft/cluster_<key>_draft.txt` | 整块正文草稿 | `novel-writer` / `gen_writer.py` |
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
- 伏笔：`novel-foreshadower` 的 JudgeReport + outline brief 梳理，非 writer 自报。
- `time_advance` / `location` 等非 archive 域由 `save_state --apply-cluster-changes` 落地。

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
- `learning_loop.py` 统计 `waivers`；同一 advisory code 在同类 cluster 被反复合理豁免时，产出工具校准建议写入 `写作经验.json` 的 `tool_calibration_suggestions` 段。

禁止把章级验证脚本作为创作链路入口；章节物理文件只由 splitter 产出，不能成为修复或回库旁路。

### 12.2 hard_gate 不可豁免清单（权威）

以下 19 个 code 是 hard_gate，**AI 不可豁免**。与 `core/scripts/audit_hub.py` 的 `HARD_GATE_CODES` 常量一一对应（改清单必须两边同步）。这些 code 一旦在 cluster 审计中以 hard_gate 进入结果，必须修复，不做 no-op、fallback 或 advisory 降级。

| 维度 | code 数量 |
|---|---|
| E 层一致性 | 5（LOCKED_FACT_CONFLICT / FUTURE_KNOWLEDGE_LEAK / FORESHADOWING_NOT_PAID / SECRET_NOT_REVEALED / UNKNOWN_CHARACTER_DETECTED） |
| 文件契约 | 3（CHANGES_MISSING / MANIFEST_MISSING / FILE_NOT_FOUND） |
| 道具状态 | 2（ITEM_HOLDER_ABSENT / ITEM_NOT_YET_INTRODUCED） |
| 传播债 | 1（PROPAGATION_DEBT_CREATED） |
| 移动阅读体验 | 1（STYLE_单段超长 · v23.12 新增） |
| 章末工艺（v2 cluster 新增） | 2（CHAPTER_END_FORBIDDEN_SCREENPLAY / CHAPTER_END_FORBIDDEN_TRANSITION） |
| cluster 跨场景一致性（v2 cluster 新增） | 1（LOCKED_FACT_CROSS_SCENE_CONFLICT） |
| 子系统载荷点火（C03 · 2026-06-27 新增） | 3（RIPPLE_RULES_EMPTY / GRAND_TREND_ME_POOL_EMPTY / CLUSTER001_STORYBOARD_EMPTY） |
| splitter 字数守恒（C18 · 2026-06-27 新增） | 1（SPLIT_WORD_NOT_CONSERVED） |


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
| `PROPAGATION_DEBT_CREATED` | audit_hub cluster | 传播债 | 跨集合数据未同步 = 传播债 |
| `STYLE_单段超长` | validate_style in audit_hub | 移动阅读 | 单段 > 120 CJK 字（物理章节 ≤1 例外）= 移动阅读硬上限；不允许 AI 豁免单条 |
| `CHAPTER_END_FORBIDDEN_SCREENPLAY` | chapter_end_anchor_scan | 章末工艺（v2 cluster 新增） | 章末出现剧本体过渡（「（镜头XX）」等舞台指示）= 连续小说工艺破坏。**为什么不可豁免**：2026-05-28 cluster_001 ch4 三次翻车 sediment，章末是钩子不是收束。详见 memory `feedback_no_screenplay_stage_directions_in_novels` |
| `CHAPTER_END_FORBIDDEN_TRANSITION` | chapter_end_anchor_scan | 章末工艺（v2 cluster 新增） | 章末出现文学过渡分隔符 / 听觉视觉淡出 / 收束句 = 移动阅读 cliffhanger 工艺破坏。**为什么不可豁免**：同上，章末不允许任何场景过渡收束 |
| `LOCKED_FACT_CROSS_SCENE_CONFLICT` | locked_fact_cross_scene_scanner | cluster 跨场景一致性（v2 cluster 新增） | 人物卡 `locked_facts` 中的数值/描述类事实在 cluster 不同场景引用矛盾（如同角色年龄两处不符）= cluster 内设定矛盾。**为什么不可豁免**：与 `LOCKED_FACT_CONFLICT` 同级，是客观设定冲突非风格选择。2026-05-28 v2 cluster 化新增 |
| `RIPPLE_RULES_EMPTY` | scaffold_subsystems verify --content / plan_step_gates.check_subsystems | 子系统载荷点火（C03 新增） | `涟漪规则.json` 的 `ripple_rules` 为空 = `world_evolution_engine` 零触发，涟漪核心机器永不点火（北极星②）。**为什么不可豁免**：性质同 `MANIFEST_MISSING`——不是风格选择而是机器无法运转的客观断点。其余子系统必须按字段契约显式定级；轻量模式也必须保留核心载荷文件与可验证结构。2026-06-27 C03 新增 |
| `GRAND_TREND_ME_POOL_EMPTY` | scaffold_subsystems verify --content / plan_step_gates.check_subsystems | 子系统载荷点火（C03 新增） | `大势卡.json` 的 ME 池 `major_events` 为空 = `cluster_emergence_engine` 大势无方向，无法涌现下一 cluster（北极星③大势已定）。**为什么不可豁免**：性质同 `MANIFEST_MISSING`。**回归锁**：只查池非空，不逐 cluster 校验，cluster_002+ 未涌现 ME 属 fluid 合法显式豁免。2026-06-27 C03 新增 |
| `CLUSTER001_STORYBOARD_EMPTY` | scaffold_subsystems verify --content / plan_step_gates.check_subsystems | 子系统载荷点火（C03 新增） | `事件簇.json` 的 `clusters[0].scene_storyboard` 为空 = 首块未详化（黄金三章必详化）。**为什么不可豁免**：cluster_001 是唯一必须 outline 阶段详化的块。**回归锁**：标记只查 `clusters[0]`，cluster_002+ 空 storyboard 属 fluid 涌现合法显式豁免（北极星·事件簇 fluid 涌现）。2026-06-27 C03 新增 |
| `SPLIT_WORD_NOT_CONSERVED` | chapter_splitter.run_freestyle | splitter 字数守恒（C18 新增） | splitter 切章后 `sum(per_chapter_cjk) + pending_tail_cjk != draft_cjk`（丢字/重复）或落盘空 chunk 或切片计数失配 = 北极星④纯格式层契约破损。**为什么不可豁免**：splitter 是纯格式层（切章后 0 audit），却必须 round-trip 完整——丢字/重复无人守。落盘前确定性自检 raise `SplitterIntegrityError` → main `[FATAL]` stderr → exit 2（坏章节零落盘·cluster-write step6 fail-fast）。**北极星⑤边界**：只查 CJK 守恒 + 无空块 + 计数同步，**绝不断言章数 N（v27/v28 fluid 由字数涌现禁锁）/ 切点质量 / 叙事顺序 / 任何内容判断**。2026-06-27 C18 新增 |

**非 hard_gate 检测项必须显式定级**：A 机械（`BANNED_WORD` / `WC_TOO_SHORT` / `WC_TOO_LONG` 等）/ B 文笔（`validate_style` 检测项）/ B+ 文笔语义层（`semantic_slop_scanner` 检测项）/ C 叙事工艺（`narrative_scanner` 检测项）/ D 情节结构（`plot_structure_scanner` 检测项）/ F 读者体验（`hook_strength_scanner` / `golden_three_scanner` / `HOOK_SUMMARY_ENDING` 等）如果属于风格/工艺/体验建议，可定为 advisory 并要求具体豁免理由；如果属于契约、状态、完整性或客观一致性，必须进入 hard_gate 清单或在对应工具内硬失败。

### 12.3 强约束

- **hard_gate 清单是权威单一来源**——`audit_hub.py` 的 `HARD_GATE_CODES`、各 agent 定义里的 hard_gate 清单、各命令文档，全部引用本节，不得各自另立或增减。
- **改 hard_gate 清单 = 改本节 + 改 `audit_hub.py` 的 `HARD_GATE_CODES`**，两边必须同步，否则文档与实现脱节。
- 新增检测器接入时必须显式定级；契约、状态、完整性、客观一致性默认 hard 或纳入本节 hard_gate 清单，只有风格/工艺/体验建议可以定为 advisory。

**维护责任**：本文档由 `/distill-style`、`/outline`、`/write`、`/cluster-write`、`/cluster-save-state`、`/export` 等命令共同遵守。如有路径冲突，**以本文档 active contract 为准**。
