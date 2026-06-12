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
│   ├── scripts/                                # Python 工具脚本
│   ├── config/                                 # 内置非密 config（gen_profiles.default.env）
│   └── gui/                                    # NiceGUI 图形界面层（见第四-bis节）
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
├── 全文.txt                                   # 拼接所有章节
│
├── _数据库/                                   # 13 个核心 JSON
│   ├── 人物卡.json                            # 角色信息 + 声纹包
│   ├── 世界观.json                            # 设定关键词
│   ├── 伏笔表.json                            # 契诃夫之枪引擎
│   ├── 故事块摘要.json                          # 各章摘要 + 情绪值
│   ├── 进度.json                              # 卷/章规划 + 当前进度
│   ├── 场景规则.json                          # 场景类型写作规则
│   ├── 写作经验.json                          # Learning Loop
│   ├── 用户偏好.json                          # 用户偏好档案
│   ├── 地图.json                              # 世界地图
│   ├── 关系.json                              # 角色关系（4 维数值）
│   ├── 事件表.json                            # 事件触发器
│   ├── 时间线.json                            # 时间系统
│   ├── 道具.json                              # 道具/物品
│   ├── 作者风格.json                          # 【从全局风格库复制】
│   ├── .wal/                                  # WAL 写前日志（save-state）
│   └── .lock                                  # 项目锁
│
├── 章节/                                       # 每章产物
│   └── 第{N}章/
│       ├── 第{N}章.txt                        # 【v18】纯正文（用户可读，不含任何 CHANGES 段/分隔符）
│       ├── 第{N}章_changes.json               # 【v18】CHANGES 数据：{"factual":{9类变更}, "self_eval":{applied_style 等}}
│       ├── 第{N}章_摘要.md                    # 200 字摘要
│       ├── 第{N}章_反思.md                    # 写作反思
│       ├── 第{N}章_走向卡.md                  # 下一章走向卡
│       └── .pre_opening.txt                   # 【v17.8 DCAS】下章 pre_opening（隐藏文件，不入 git）
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
- `第{N}章_changes.json` 顶层两键：`factual`（9 类客观变更，judge 可读）+ `self_eval`（writer 自评 applied_style 等，judge 默认不读，v17.4 分权纪律）。

---

## 四、核心系统目录（`core/claude-home/`）

```
core/claude-home/
├── plans/                                      # plan_tracker 强制规划模板（<command>.plan.json）
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
│   └── ...（未来：write-lessons.md / outline-lessons.md）
└── STRUCTURE.md                                # 本文档
```

> **✅ `core/claude-home/commands/` 历史副本已于 2026-05-15 删除**（曾为 v18 前命令文档副本，长期未与 `.claude/commands/` 同步）。命令文档的**唯一权威**位置是 `.claude/commands/`——改命令文档只改这里，grep 验收也只验这里，系统运行时也只加载这里。

---

## 四-bis、GUI 图形界面层（`core/gui/`）

NiceGUI 图形界面层（v28 · 2026-06-10 引入），让非技术用户脱离 Claude CLI 直接驱动 orchestrator：

```
core/gui/
├── app.py                                      # 页面（唯一 import nicegui：写作台 / Plan 续跑页 / 设置页）
├── runner.py                                   # 流水线驱动 + PauseBridge 接线（工作线程驱动 orchestrator.run_command）
├── state.py                                    # 纯逻辑状态层（AppState / PauseBridge / LogBuffer · 零 nicegui）
├── widgets.py                                  # 展示组件
└── theme.py                                    # 主题
```

- **入口**：仓库根 `ruoyu_gui.py`（`python ruoyu_gui.py` 浏览器 / `--native` 桌面窗口）。
- **frozen exe**：由 `packaging/ruoyu_gui.spec` 打包（onedir · 一键构建走 `packaging/build_all.py`）。
- 详细设计见 `core/claude-home/PROGRAM_DRIVEN.md` 〔图形界面〕节。

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
| `workspace/novels/{书名}/` | **独立 Git**（`workspace/novels/{书名}/.git`） | 每章 save-state 时提交 |
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
    ├─→ core/claude-home/lessons/write-lessons.md（写作教训 · 未来）
    └─→ core/claude-home/lessons/{command}-lessons.md（其他命令教训 · 未来）
```

---

## 八、迁移检查清单（从旧规范迁移）

如果项目存在旧规范文件，按下表迁移：

| 旧路径 | 新路径 | 命令影响 |
|---|---|---|
| `风格库/{书名}.json` | `workspace/styles/{书名}/作者风格_FINAL.json` | distill-style |
| `风格库/{书名}_skill.md` | `workspace/styles/{书名}/skill_FINAL.md` | distill-style |
| `风格库/复刻测试/v0/` | `workspace/styles/{书名}/复刻测试/v0/` | distill-style |
| `风格库/对比报告/` | `workspace/styles/{书名}/对比报告/` | distill-style |
| 小说_书名/（项目根同级） | `workspace/novels/{书名}/` | outline / write / save-state |
| `_数据库/蒸馏进度/`（小说项目内） | `workspace/styles/{书名}/蒸馏进度/`（独立风格库） | distill-style |

**迁移命令示例**（不要直接跑，确认后执行）：
```bash
mkdir -p "workspace/styles/{书名}"
mv "风格库/{书名}.json" "workspace/styles/{书名}/作者风格_FINAL.json"
mv "风格库/{书名}_skill.md" "workspace/styles/{书名}/skill_FINAL.md"
```

---

## 九、命令产出对照表

每个命令的产出位置（权威表）：

| 命令 | 主要产出 | 路径 |
|---|---|---|
| `/distill-style` | 风格基线 / skill / 衔接分析 / 复刻测试 / 对比报告 / distillation_log | `workspace/styles/{书名}/` |
| `/distill-character` | 角色 voice DNA | `workspace/styles/{书名}/角色档案/{角色名}.json` |
| `/outline` | 大纲 / 34 个数据库 JSON | `workspace/novels/{书名}/_数据库/` |
| `/cluster-write` | cluster 整块草稿 + 切章物理文件 + 平铺 CHANGES | `workspace/novels/{书名}/章节/cluster_<key>_draft/cluster_<key>_draft.txt` → splitter 切出 `第{N}章/第{N}章.txt` + `第{N}章_changes.json` |
| `/cluster-save-state` | cluster 摘要 / 反思 / 走向卡 + 涌现下个 cluster brief（一次性 apply cluster_changes 到数据库） | `workspace/novels/{书名}/_数据库/故事块摘要.json` + `章节/cluster_<key>_draft/` 伴生文件 |
| `/check-quality` | 校验报告 | `workspace/novels/{书名}/_tmp/quality_ch{N}.md` |
| `/scan` | 市场分析 | `workspace/novels/{书名}/_数据库/扫榜.json` |
| `/export` | 拼接全文 | `workspace/novels/{书名}/全文.txt` |
| `/reconcile` | 一致性调和 | `workspace/novels/{书名}/_数据库/调和日志_{date}.md` |

---

## 十、给开发者的强约束

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

## 十、附录 · 工具兼容的章节路径布局（v17.5 新增 · v18 扩充正文/数据分离）

### 10.A 正文 txt 路径布局（4 种兼容布局）

为了在旧项目迁移期保持兼容，所有读取章节正文的脚本支持以下 4 种布局，按优先级查找：

| 优先级 | 布局 | 路径示例 | 备注 |
|---|---|---|---|
| **1（推荐）** | 嵌套-zero-pad | `章节/第004章/第004章.txt` | v17.5+ 标准 |
| 2 | 嵌套-原长 | `章节/第4章/第4章.txt` | 兼容 |
| 3 | 平铺-zero-pad | `第004章.txt` | 旧布局（已废弃，gitignore 阻止再生） |
| 4 | rglob 兜底 | 任何 `**/第N章*.txt` | 极端兜底 |

**强约束**：新项目必须用布局 1。布局 3 已在 v17.5 P0.3 标记为 `.gitignore` 忽略——禁止再创建。

### 10.B 【v18】正文/数据分离 — 章节文件 = 两个物理文件

v17 及之前，章节 txt = 正文 + `---CHANGES_FACTUAL---` JSON + `---CHANGES_SELF_EVAL---` JSON 混在一个文件，靠分隔符切。十几个脚本各自 split，口径不一，导致 `validate_style` 把 JSON 当正文算字数虚高、`save_state` 分隔符不一致解析失败、git commit 字数算错。

**v18 起，章节落地为两个物理文件**：

| 文件 | 标准路径 | 内容 | 由谁产出 |
|---|---|---|---|
| 正文 | `章节/第NNN章/第NNN章.txt` | **纯正文**（无 CHANGES、无分隔符） | `novel-chapter-splitter`（cluster mode · 从 cluster 草稿切章） |
| 数据 | `章节/第NNN章/第NNN章_changes.json` | `{"factual": {9类变更}, "self_eval": {applied_style 等}}` | `novel-chapter-splitter`（splitter 从 `cluster_<key>_changes.json` 按切点平铺到各 ch） |

**强约束（开发者必读）**：
- 所有读写章节正文 / CHANGES 的脚本和 agent，**必须**走 `core/scripts/chapter_io.py` 统一模块——禁止各自写 `split("---CHANGES")`。
  - 读正文：`read_body(project_root, ch)` → 纯正文 str（遇旧混合 txt 自动剥离）
  - 读数据：`read_changes(project_root, ch)` → `{"factual": {...}, "self_eval": {...}}`（只读 `_changes.json`，v2 后旧混合稿解析已删）
  - 写正文：`write_body(project_root, ch, text)` / 写数据：`write_changes(project_root, ch, changes)`
  - 路径：`body_path()` / `changes_path()`；字数：`count_words()`（全系统统一口径）
- judge agent（validator-repair / voice-keeper / foreshadower 等）只拿正文 txt；看 9 类变更读 `_changes.json` 的 `factual` 段；`self_eval` 段按 v17.4 分权纪律默认不读。

---

## 十一、v19 检测体系顾问制 + hard_gate 不可豁免清单

> v19 把检测体系从「门禁/法官」改成「顾问」。本节是 hard_gate 清单的**权威定义**，所有工具/agent/命令引用此清单，不得另立。

### 11.1 顾问制理念

v19 起，检测工具（`validate_style` / `narrative_scanner` / `plot_structure_scanner` / `semantic_slop_scanner` / `hook_strength_scanner` / `golden_three_scanner` / `validate_chapter` 等 cluster 视野 scanner）输出的是**「待裁决项」，不是判决**：

- 每条 issue 带 `gate_level` 字段，取值 `"hard_gate"` 或 `"advisory"`。
- **advisory 项**：风格 / 文笔 / 叙事工艺 / 情节结构 / 读者体验层的检测项。AI（writer / validator / voice-keeper / foreshadower）**有充分理由可以豁免**——豁免必带具体理由（< 100 字、具体到本章场景），理由不充分 = 豁免无效。
- **hard_gate 项**：E 层一致性 + 文件契约破损。这是**客观错误**，不是风格选择，**AI 不可豁免**——即便在 `--waivers` 里传了豁免理由，audit_hub 也强制忽略豁免，仍按问题处理。
- 豁免理由的载体：writer 写在 `第N章_changes.json` 的 `self_eval.waivers: [{code, reason}]`；judge agent 写在 JudgeReport 的 `waivers` 段。
- `audit_hub.py` 通过 `--waivers <json路径>` 入参收集豁免；对 advisory 项命中豁免 → 转 `waived`（记 `waive_reason`），不计入 `needs_agent`；剩余 issue 全是被合理豁免的 advisory 且无 hard_gate 残留 → verdict = `waived`（等同放行）。
- `learning_loop.py` 统计 `waivers`——同一 advisory code 在同类章节被反复合理豁免 → 产出「工具校准建议」写入 `写作经验.json` 的 `tool_calibration_suggestions` 段，反向校准工具阈值，而不是反复骚扰 AI。

### 11.2 hard_gate 不可豁免清单（权威）

以下 15 个 code 是 hard_gate，**AI 不可豁免**。与 `core/scripts/audit_hub.py` 的 `HARD_GATE_CODES` 常量一一对应（改清单必须两边同步）：

> **🔴 severity 条件降级（与 `audit_hub._gate_level_for()` 对齐 · 2026-06-12 文档补正）**：清单内 code 不是无条件 hard_gate——gate_level 判定前有两条 severity 前置规则：
> 1. **info severity 永不 hard_gate**（2026-06-02 修：info = 自动生成的低置信旁注，下游可忽略；北极星⑤顾问非法官）。实践影响：`UNKNOWN_CHARACTER_DETECTED` 由 validate_chapter **恒以 info 发**（低置信 NER · 历史 250+ 误报），故**实践中恒为 advisory**；真要 block 的项应以 error/fatal 发。
> 2. **`STYLE_单段超长` 仅 severity ∈ {fatal, error} 时 hard_gate**；WARN 状态（80-120 警告区或例外内）降 advisory 可豁免（v23.12）。

| 维度 | code 数量 |
|---|---|
| E 层一致性 | 5（LOCKED_FACT_CONFLICT / FUTURE_KNOWLEDGE_LEAK / FORESHADOWING_NOT_PAID / SECRET_NOT_REVEALED / UNKNOWN_CHARACTER_DETECTED） |
| 文件契约 | 3（CHANGES_MISSING / MANIFEST_MISSING / FILE_NOT_FOUND） |
| 道具状态 | 2（ITEM_HOLDER_ABSENT / ITEM_NOT_YET_INTRODUCED） |
| 传播债 | 1（PROPAGATION_DEBT_CREATED） |
| 移动阅读体验 | 1（STYLE_单段超长 · v23.12 新增） |
| 章末工艺（v2 cluster 新增） | 2（CHAPTER_END_FORBIDDEN_SCREENPLAY / CHAPTER_END_FORBIDDEN_TRANSITION） |
| cluster 跨场景一致性（v2 cluster 新增） | 1（LOCKED_FACT_CROSS_SCENE_CONFLICT） |


| code | 来源 | 类别 | 为什么不可豁免 |
|---|---|---|---|
| `LOCKED_FACT_CONFLICT` | validate_chapter | E 层一致性 | 正文与已锁定事实冲突 = 设定矛盾 |
| `FUTURE_KNOWLEDGE_LEAK` | validate_chapter | E 层一致性 | 角色知道不该知道的 = 逻辑错误 |
| `FORESHADOWING_NOT_PAID` | validate_chapter | E 层一致性 | Tier-1 到期伏笔未回收 = 对读者的承诺违约 |
| `SECRET_NOT_REVEALED` | validate_chapter | E 层一致性 | 秘密该揭未揭 = 剧情债 |
| `UNKNOWN_CHARACTER_DETECTED` | validate_chapter | E 层一致性 | 引用未声明实体 = 引用错误（注：分词误检的另算，需先修检测器，不当普通 advisory 豁免）。**⚠ severity 条件**：validate_chapter 恒以 info 发该 code，而 info 永不 hard_gate（2026-06-02 修）→ 实践中恒为 advisory |
| `CHANGES_MISSING` | validate_chapter | 文件契约 | `_changes.json` 缺失或 `factual` 为空 = 文件契约破损 |
| `MANIFEST_MISSING` | validate_chapter | 文件契约 | manifest 缺失 = 文件契约破损 |
| `FILE_NOT_FOUND` | validate_chapter | 文件契约 | 正文文件缺失 = 文件契约破损 |
| `ITEM_HOLDER_ABSENT` | validate_chapter | 道具状态 | 道具持有者不在场 = 道具状态矛盾 |
| `ITEM_NOT_YET_INTRODUCED` | validate_chapter | 道具状态 | 道具尚未引入就被用 = 道具状态矛盾 |
| `PROPAGATION_DEBT_CREATED` | validate_chapter | 传播债 | 跨集合数据未同步 = 传播债 |
| `STYLE_单段超长` | validate_style | 移动阅读 | 单段 > 120 CJK 字（每章 ≤1 例外）= 移动阅读硬上限。**为什么不可豁免**：v23.12 写入，基于 2026-05-21 调研（起点官方+中国作家网+12 来源互证），移动端 > 120 字单段严重不适，是读者体验客观下限。**⚠ severity 条件**：仅 fatal/error 时 hard_gate；WARN（80-120 警告区或例外内）降 advisory（`_gate_level_for` 实现）。**项目级覆盖**：蒸馏文学向项目可写 `_数据库/style_scanner_overrides.json` 调高阈值，按 [[feedback_distill_scanner_threshold_link_missing]] 流程；不允许 AI 豁免单条。详见 memory `feedback_paragraph_length_hard_constraint` |
| `CHAPTER_END_FORBIDDEN_SCREENPLAY` | chapter_end_anchor_scan | 章末工艺（v2 cluster 新增） | 章末出现剧本体过渡（「（镜头XX）」等舞台指示）= 连续小说工艺破坏。**为什么不可豁免**：2026-05-28 cluster_001 ch4 三次翻车 sediment，章末是钩子不是收束。详见 memory `feedback_no_screenplay_stage_directions_in_novels` |
| `CHAPTER_END_FORBIDDEN_TRANSITION` | chapter_end_anchor_scan | 章末工艺（v2 cluster 新增） | 章末出现文学过渡分隔符 / 听觉视觉淡出 / 收束句 = 移动阅读 cliffhanger 工艺破坏。**为什么不可豁免**：同上，章末不允许任何场景过渡收束 |
| `LOCKED_FACT_CROSS_SCENE_CONFLICT` | locked_fact_cross_scene_scanner | cluster 跨场景一致性（v2 cluster 新增） | 人物卡 `locked_facts` 中的数值/描述类事实在 cluster 不同场景引用矛盾（如同角色年龄两处不符）= cluster 内设定矛盾。**为什么不可豁免**：与 `LOCKED_FACT_CONFLICT` 同级，是客观设定冲突非风格选择。2026-05-28 v2 cluster 化新增 |

**其余全部 advisory，AI 可凭充分理由豁免**：A 机械（`BANNED_WORD` / `WC_TOO_SHORT` / `WC_TOO_LONG` 等）/ B 文笔（`validate_style` 12 项：对话占比、逗句比、段落均长、极短段、拟声格式 `PSEUDO_SOUND_MISSING` 等）/ B+ 文笔语义层（`semantic_slop_scanner` 8 检测器：`SEMANTIC_metaphor_explain` 隐喻后立即解释 / `SEMANTIC_aphorism` 金句体 / `SEMANTIC_neg_parallel` 否定式排比 / `SEMANTIC_copula_avoid` 系动词回避 / `SEMANTIC_fake_range` 虚假范围 / `SEMANTIC_over_hedge` 过度限定 / `SEMANTIC_forced_triple` 强行三段列举 / `SEMANTIC_tag_synonym_cycle` 对话标签同义词循环——抓 anti-slop 机械层正则漏掉的句级 AI 腔）/ C 叙事工艺（`narrative_scanner` 8 检测器：GMC / MRU / Orphan / Micro-tension / Repetition / POV / info_dump / **perspective_shift**——后者 P2-16 新增的人称切换检测，章内 first ↔ third 跳转报警）/ D 情节结构（`plot_structure_scanner` 7 检测器：beat / tryfail / midpoint / knowledge / arc / subplot / **kishotenketsu**——后者为 P1-4 新增的起承转结四段结构，仅对 `chapter_mode=solo_atmospheric` 激活）/ F 读者体验（`hook_strength_scanner` / `golden_three_scanner` / `HOOK_SUMMARY_ENDING` 等）的所有检测项。

### 11.3 强约束

- **hard_gate 清单是权威单一来源**——`audit_hub.py` 的 `HARD_GATE_CODES`、各 agent 定义里的 hard_gate 清单、各命令文档，全部引用本节，不得各自另立或增减。
- **改 hard_gate 清单 = 改本节 + 改 `audit_hub.py` 的 `HARD_GATE_CODES`**，两边必须同步，否则文档与实现脱节。
- 新增检测器默认 `advisory`——除非它检测的是「客观错误而非风格选择」，且经评审纳入本节清单。

---

## 十二、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-05-13 | 首次规范化，整合 distill-style v17 后的实际路径 + outline 的项目结构 |
| v1.1 | 2026-05-14 | v17.5：新增第十节"工具兼容章节路径布局"，规范化 4 种布局优先级 + 强制布局 1 |
| v1.2 | 2026-05-14 | v18：章节正文/数据分离——`第N章.txt`（纯正文）+ `第N章_changes.json`（`{factual, self_eval}`）两个物理文件；第三节章节结构、第十节布局说明、第九节产出表同步；强制所有脚本走 `chapter_io.py` 统一读写 |
| v1.3 | 2026-05-14 | v19：检测体系顾问制——新增第十一节，固化 hard_gate 不可豁免 11 code 清单（与 `audit_hub.py` 的 `HARD_GATE_CODES` 一一对应）+ 顾问制理念（advisory 可豁免、豁免带理由、learning_loop 反向校准） |
| v1.4 | 2026-05-15 | v19 P0-2：第十一节新增 `semantic_slop_scanner`（B+ 文笔语义层 8 检测器，全 advisory，audit_hub 第 7 个校验器）；删除已废弃的 `core/claude-home/commands/` 历史副本（37 文件）+ 清理过期文件 |
| v1.5 | 2026-05-15 | P1-1：`plan_tracker.py` 加防篡改 attestation——每次写盘盖 `_attestation` SHA-256 章，`step`/`end`/`abort` 写前读校验（tampered → 阻断），新增 `verify`/`reattest` 子命令；`pretooluse_agent_gate.py` 加规则 8（PLAN_ID 引用的 plan 篡改 → Agent spawn 前拦截） |
| v1.6 | 2026-05-15 | P1-4：`plot_structure_scanner.py` 加第 7 个检测器 **H10 Kishōtenketsu**（起承转结四段结构，对应单人氛围章 / 东方叙事，仅 `chapter_mode=solo_atmospheric` 激活）；`narrative_scanner._detect_chapter_mode` 改公开 `detect_chapter_mode`（单一来源，plot_structure_scanner 导入复用避免参差）；`audit_hub.PLOT_DIM` 同步 |
| v1.7 | 2026-05-15 | P1-5：`style_repair_engine.py` 加 **三遍迭代法**（`apply_fixes_iterative`）—— 默认最多跑 3 遍 `apply_fixes`，每遍后比对文本若无变化（收敛）则提前停止，捕获级联型修复机会（如 merge 后产生新 banned word 命中）；CLI 加 `--max-passes N` / `--no-iterative`；report JSON 加 `per_pass` / `converged_at_pass` 审计字段。**audit_hub 零改动**——子进程内部迭代收敛后才返回，复核逻辑保持。 |
| v1.8 | 2026-05-15 | P1-6：`posttooluse_plan_check.py` 加 **self-correct injection** —— (1) auto-step 成功后追加「下一步提示」(`↪ 下一步: step N (name), 期望输出: ...`) 反馈给 agent context；(2) 停滞检测：plan 已 in_progress 但 >10 分钟无新 step 完成 → 输出 `📋 [Plan-drift]` 提示。链式引导 agent self-correct，不拦截不阻断。 |
| v1.9 | 2026-05-15 | P2-5：`learning_loop.py` 加 **时间维度衰减/清理** —— `success_patterns` / `failure_patterns` 加 `updated_at` 字段（创建/刷新时自动盖戳，`_route_entry` + `_escalate_recurring` 两处植入）；`--scan-recurring` 自动跑 `_prune_and_decay`：超 `DECAY_DAYS=14` 天未强化 → confidence `*=0.8`，超 `EXPIRY_DAYS=30` 天未强化 → 自动清理。旧条目（无 `updated_at`）首次扫描时迁移打戳，**非破坏式**。 |
| v1.10 | 2026-05-15 | P2-6：`judge_consensus.py` 加 **persona 维度分析** —— `JudgeReport` 可选 `persona` 字段（如 `common_reader` / `developmental_editor` / `line_editor` / `harsh_critic`），merge 时输出 `persona_breakdown`（按 persona 分组的平均评分）+ `persona_dissent_severity`（跨 persona 的 grade level 极差）。Persona 间分歧 ≥ 2 grade levels 触发 escalate。**向前兼容**：所有 report 都无 persona 字段时退回原行为（合并到 default 桶）。schema_version 1.0→1.1。 |
| v1.11 | 2026-05-15 | P2-7：voice 链路加 **反 over-generalize 守则** —— `novel-voice-keeper.md` 加守则段（不把单段特色横移、catchphrase 是允许非必须、单次样本不构成硬约束）；`distill-character.md` voice_pack 蒸馏要求 `style_samples`/`anti_samples` 频次 ≥2 章节、`catchphrases` 频次 ≥3 次才升级；`banned_phrases` 不受门槛（底线，首次即列）。判断口诀：「频次 ≥3 才算 pattern」「未见 ≠ 违规」「特色 ≠ 必用」。 |
| v1.12 | 2026-05-15 | P2-8：`plan_tracker.py` 加 **subagent cost 追踪** —— `step` 子命令加 `--tokens N` + `--duration-ms N` 可选参数，持久化到 step 字段 `tokens_used`/`duration_ms`；`end_plan` 返回值加 `cost_summary` 段（total_tokens / total_duration_ms / steps_with_cost）；`status` CLI 行末追加 `[X.XK tok, X.Xs]` 显示。**幂等扩展**：已 completed 的 step 可补录 cost（不重写其他字段）。**向后兼容**：不传 cost 参数完全不写字段，所有旧调用零冲击。test 套件加 `TestCostTracking` 4 用例（30→34 全过）。 |
| v1.13 | 2026-05-15 | P2-9：`novel-researcher.md` 加 **iterative-retrieval 模式（Step 2.5）** —— 首轮广查询完成后基于结果识别 2-3 个跟进问题（空白补全 / 细节深挖 / 矛盾消解），最多 1 轮跟进。触发条件：SCOPE 结果 <2 OR 出现不确定标记 OR 多源矛盾。报告 Synthesis 段末标注跟进状态。**向前兼容**：现有 Step 1/2/3 不重编号，跳过条件明确。 |
| v1.14 | 2026-05-15 | P2-10：`pretooluse_agent_gate.py` 加 **规则 9 内容级注入模式检测**（warn-only）—— 10 条英中双语 injection 模板（"ignore previous instructions" / "disregard above" / "忽略之前指令" / "重新定义你是" 等）。命中 ≥2 个不同 pattern 时 stderr 警告但 exit 0 不拦截，避免误伤 NPC 对话/研究内容中的合法字符串。补 anti-slop + plan attestation 之外的内容级威胁视角。 |
| v1.15 | 2026-05-15 | P2-4：新增 **session 级跨会话记忆**（`core/scripts/session_memory.py`，借鉴 claude-mem MVP 聚合版）—— SessionStart hook 自动注入最近 3 个会话摘要（commits + active plans + 时长），Stop hook 自动收尾持久化 summary。存储 `core/claude-home/.sessions/<sid>.json`（gitignore），30 天自动清理。**一体化纪律**：不读写 WAL 字段（L8.4 共存）、只读 `plan_tracker.list_plans` 不修改状态、不进 learning_loop 经验库。Hook 永不拦截 / 永不抛异常（永远 exit 0），与已有 `~/.claude/hooks/session_start.sh` + `send_notify.sh` 在 settings.json 同事件数组里并存。 |
| v1.16 | 2026-05-15 | P1-2：新增 **plan 命令 evals 行为契约**（`core/claude-home/evals/<command>.evals.json`，借鉴 skill-creator 2.0）—— 6 个 plan 命令（save-state / distill-style / check-quality / write-chapter / outline / reconcile）各 2 个代表性场景（共 12 entries），含 `user_prompt` / `fixture` / `expected_trace` / `expected_artifacts` / `tolerance` 5 字段。与 plan_tracker 模板互补：模板定义「执行期硬约束」，evals 定义「场景期 spec」。当前为纸面契约，后续可加 `eval_runner.py` 自动跑。README.md 说明 schema + 维护纪律（模板改 → evals 同步审一遍）。 |
| v1.17 | 2026-05-15 | Round 10-14 学习落地三连：**P2-11** `judge_consensus.py` 加 evidence_quality 完备度统计（业界 grounding 实践——judge 评分必须附 quote），voice-keeper JudgeReport schema 1.0→1.1 加 `evidence_quotes` 字段强制要求 ≥2 条原文 quote；evidence_quality < 0.5 触发 escalate；schema 1.1→1.2。**P2-13** novel-writer.md `self_eval` 加 `uncertainty_flags` 字段：writer 主动标自评不确定项（aspect + detail + suggest_judge），与 waivers 区分（waivers=主动豁免，uncertainty=不确定建议复审）。**P2-14** `narrative_scanner.py` 加第 7 检测器 **G7 info_dump**：长叙述（≥80字）+ 设定堆砌关键词 + 对话占比<10% 三连命中 → advisory warning，对应 oh-story 三遍法 Pass1 去泛化。NARRATIVE_DIM 同步加 info_dump → 节奏 维度。STRUCTURE.md §11.2 narrative 6→7 检测器对齐。 |
| v1.18 | 2026-05-15 | P2-15：`audit_hub.py` 7 scanner **并行执行**（借鉴 Programmatic Tool Calling）—— 用 `ThreadPoolExecutor` 同时跑 7 个 subprocess（原串行约 21s → 并行约 3-5s），结果按 submit 顺序读取保持 `scanner_status` 一致性。子进程隔离不变（任一挂了不连累其他），timeout/parse-error 各自独立。`_exec_one` 内 try/except parse_fn 避免单 scanner 解析异常拖垮整次 audit。验证：verdict + scanner_status 顺序 + issues 数与串行版完全一致。 |
| v1.19 | 2026-05-15 | P2-16：`narrative_scanner.py` 加第 8 检测器 **G8 perspective_shift**（人称切换检测，来自 Round 20 学习）—— 检测章内段间 first ↔ third 人称切换（业界共识「third limited should never head-hop within scenes」）。`_classify_paragraph_person()` 用「我/我的/咱/俺」vs「他/她/它/他的」频次判定段主导人称（≥0.7 占比为主导），neutral 段（指代<2）不算切换点。NARRATIVE_DIM 加 perspective_shift→结构 维度。区别于 G6 pov（叙述距离）：G8 是叙述人称，正交维度。 |
| v1.20 | 2026-05-21 | v23.12：**段长系统级硬约束**——基于 2026-05-21 网文段长调研（起点官方 + 中国作家网 + 12 来源互证）。5 处同步：(1) hard_gate 清单 11→12 加 `PARAGRAPH_TOO_LONG`（>120 CJK 字单段；每章 ≤1 例外）；(2) `validate_style.py` 加 3 项检查 `_chk_para_max` / `_chk_single_line_ratio` / `_chk_long_para_count`；(3) `audit_hub.HARD_GATE_CODES` 同步 + `STYLE_DIM` 加新 code；(4) `CLAUDE.md` + `core/claude-home/CLAUDE.md` 反 AI 腔调守卫第 7 条段长规则；(5) memory `feedback_paragraph_length_hard_constraint` + MEMORY.md 红索引。**流派档**：吐槽爽文档（默认）平均 15-30 字 / 修仙玄幻 25-50 / 文学向 50-100。**项目级覆盖**：蒸馏文学向项目走 `_数据库/style_scanner_overrides.json`。触发实例：某项目 cluster_001 v2 平均段长 37.5 / 最长 >200，用户反馈调研后定档。 |

---

**维护责任**：本文档由 `/distill-style`、`/outline`、`/write` 等命令共同遵守。如有路径冲突，**以本文档为准**。
