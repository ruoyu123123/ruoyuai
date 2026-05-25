# 蒸馏系统经验库（自学习追加式）

> 跨项目通用教训沉淀。每次 `/distill-style` 完成后由 lessons-extractor agent 自动提取并追加。手动条目用 `[MANUAL]` 标记。

## 元数据

- **起始版本**：v17（三章窗口 + 衔接分析 + 闭环复刻）
- **当前主轨**：**v22.cluster · 故事块自适应蒸馏**（2026-05-19 重构 · LumberChunker EMNLP 2024 + MARCUS 2025 + Multi-Agent TV Arcs 2025）
- **首次写入**：2026-05-13
- **追加规则**：每条教训必须有「现象 → 影响 → 修复 → 预防」四要素
- **去重规则**：lessons-extractor 必须先读现有库，相同根因不重复追加（可补充细节）
- **优先级**：⛔ 红线 / ⚠️ 重要 / 💡 提示

---

## 🚨 v22.cluster 迁移警示（2026-05-24 翻车后强制加入 · 主代理 Step 1 必读）

**老规则（v17 三章固定窗口）已弃用**——所有标 `[DEPRECATED v22.cluster]` 的 lesson 仅作历史保留，**新蒸馏一律按 cluster 故事块颗粒度**。

| 项 | v17 老规则（弃） | v22.cluster 新规则 |
|---|---|---|
| 颗粒度 | 固定 3 章/窗口 | **cluster 自适应 3-6 章 / 4000-20000 字** |
| 切分器 | 章号取模 | **`cluster_segmenter.py`**（语义边界 + max_chapters 硬上限） |
| Agent 数（200 章估）| 67 | **~37**（节省 ~45% 调用） |
| 衔接 JSON 路径 | `衔接分析/ch{N}_{N+2}_continuity.json` | `衔接分析/cluster_<id>_continuity.json` |
| arc 聚合 | ❌（v17 无）| `arc_aggregator.py` 主轨 cluster + 副轨 fixed10 |
| 业界依据 | 经验法则 | LumberChunker (EMNLP 2024) +7.37% DCG@20 / MARCUS 事件中心 / Multi-Agent TV Arcs 自然终结 |

**主代理 Step 1 必做项**：
1. 读 `workspace/styles/<书名>/cluster_index.json`——
   - **场景 A 复用**：存在 → 直接复用（含 strong 边界，v22.cluster.2 等级）
   - **场景 B 裸切**：不存在 → 跑 `cluster_segmenter.py`（首轮 strong=0 是设计，不是 bug）
2. Agent 调度循环改为 `FOR cluster in cluster_index.clusters`，**不要**再写 `FOR batch_start = 1 to N step 3`
3. **场景 B 额外**：表层蒸馏完成后必须重跑 segmenter 做 retro-refine 才能得到含 strong 边界的最终 cluster_index
4. 详细迁移指南见 [v22-cluster-migration.md](v22-cluster-migration.md)（v1.1 含两种场景 caveat）

**翻车实例 1（2026-05-24 · BookA v5 重蒸）**：主代理 Step 1 没读 cluster_index.json，盲套 v4.3 历史的 66 三章窗口规则；用户指出"系统已改 cluster"才矫正。根因 = lessons 元数据起始版本声明仍写 v17 + L2.6/L4.5/L4.6 全是 3 章窗口语境 + 零 cluster 提及 → 主代理"按 lessons 老规则做事"。

**翻车实例 2（2026-05-24 · BookA v6 全清重做）**：用户全清后主代理重跑 segmenter，得到 34 cluster · strong=0，误判为"降级 cluster_index 质量缺陷"停下来报警。实际上是场景 B 自然首轮态——v22-cluster-migration.md v1.0 没区分"新蒸馏裸切 vs 重蒸复用"两种场景，导致主代理把首轮裸切当作降级。修复：v1.1 加 §0 关键 caveat + §3.4 retro-refine SOP。

---

## 1. 调度层教训

### L1.1 ⛔ Agent gate hook 必须传契约字段
- **现象**：写作类 Agent（description 含「章」「写作」「正文」等关键词）prompt 缺 PROJECT/CHAPTER/MODE/MANIFEST 任一字段时被 hook exit 2 拦截
- **影响**：5 个并行 agent 全部 PreToolUse 失败，整批返工
- **修复**：每个 Agent prompt 头部必须有以下任一字段（兜底规则）：
  ```
  PROJECT: <项目名>
  CHAPTER: <章节号或范围>
  MODE: <distill-style-3ch / aggregate-skill / 等>
  MANIFEST: <共享 brief 文件路径>
  ```
- **预防**：写主代理调度模板时把契约字段做成 prompt 必填项，不靠记忆

### L1.2 ⛔ 不要传 team_name 除非先 TeamCreate
- **现象**：直接传 `team_name="xxx"` 报 "Team does not exist. Call spawnTeam first"
- **影响**：5 个并行 agent 启动失败
- **修复**：Agent 工具的 `team_name` 是可选参数。除非明确要 spawn teammate 群，否则**省略**该参数直接 spawn 独立 agent 即可
- **预防**：spawn Agent 前先确认是否需要 team。team task list 会与主对话 task list 切换上下文，谨慎使用

### L1.3 ⚠️ 工作目录永远用绝对路径
- **现象**：`cd "中文路径" && ...` 改变了主代理工作目录，后续 spawn Agent 时 hook 用相对路径 `core/claude-home/...` 失败
- **影响**：再次 spawn 5 个 agent 全部失败
- **修复**：Bash 命令禁止用 `cd` 切换主代理目录。一切路径用绝对路径（如 `<REPO_ROOT>/...`）
- **预防**：遵守 CLAUDE.md "Try to maintain your current working directory" 原则。需要切换路径时用 `(cd path && cmd)` 子 shell 形式

### L1.4 💡 多 agent 并行的账户配额规划
- **现象**：5 个 agent 并行跑大批量任务，每个 ~30k tokens，1 小时内触发 "You've hit your limit · resets 6am"
- **影响**：15 章产出半成品（metrics + 部分 JSON）
- **修复**：
  - 估算单 agent token 消耗 × 并行数 × 批次数 ≤ 账户配额
  - 长时间任务分多个会话/分批跑
  - 等限额重置后用救援 agent 补完，不是从头跑
- **预防**：长任务前先看账户配额阈值。每 30 分钟做一次 ls 验证产出，避免出问题才发现
- **补充细节（2026-05-13 晚, 大批量增量蒸馏）**：**多窗口配额规划**——单次大批量蒸馏任务（跨多阶段、覆盖 200+ 章）可能触发**多次**配额耗尽事件，且不同重置时间窗口（如 5h reset 短周期 + 24h reset 长周期）可能交替触发。**策略升级**：
  - **配额事件分类**：短周期重置（usage limit · resets in 5h）vs 长周期重置（weekly limit · resets next Monday），两类响应策略不同
  - **短周期触发**：暂停 spawn 新批次，等 reset 后继续，已有产出不动
  - **长周期触发**：必须切换会话或分多日完成；任务调度器应优先把"易失败/易救援"章段排到周期开始
  - **预判配额耗尽**：每完成一个阶段后估算剩余 token 配额，配额 < 单阶段成本 × 1.5 时**主动**暂停而非等触发
  - **救援链路抗配额**：救援 agent 设计为"低 token 消耗"模式（单章 ≤5k tokens），即使配额紧张也能完成救援

---

## 2. Agent 执行层教训

### L2.1 ⛔ Turn 末尾不要跳过 continuity
- **现象**：Agent 写完 3 个单章 JSON 后 turn 接近结束，跳过 continuity JSON（"最后写衔接分析 JSON" 但实际没写）
- **影响**：缺 1 个 continuity JSON，需要救援 agent
- **修复**：prompt 加明确指令「3 单章 JSON 写完后**立即**写 continuity JSON，不要拖到 turn 末尾。如果时间紧，先写 continuity 框架再回填」
- **预防**：把 continuity 当成 4 文件的第一优先级，单章 JSON 颗粒度可以略减，continuity 必须完整
- **补充细节（2026-05-13 晚, 大批量增量蒸馏）**：**高频根因分析**——同一批增量段（覆盖跨阶段 4 个增量版本的大批量章节）累计触发 7 次 L2.1，平均每阶段约 2 次。说明仅在 prompt 里写「不要拖到 turn 末尾」是不够强的——Agent 在写完单章 JSON 后 token 预算已偏紧，subjectively"先写完最后一个单章"的思维惯性压过 prompt 警告。**根因升级**：prompt 警告是被动约束，agent 自然语言路径优化会跳过被动约束。**强约束设计**：
  - **顺序硬翻转**：要求 agent 在 spawn 后**先写 continuity 空架子**（{"window": "ch-X-Y", "transitions": "TODO"}），再依次回填三个单章，最后回头补 continuity 正文。这样即使 turn 截断，continuity 框架已存在，救援 agent 只需"填充"而非"创建"
  - **强制 checkpoint**：agent prompt 要求每写完一个 JSON 立即输出 `[CHECKPOINT N/4 done]` 标记，主代理可监控进度

### L2.1.1 ⚠️ 空架子先占位（Continuity 抗截断最佳实践）
- **现象**：在 L2.1 高频触发的背景下引入"空架子 continuity 先占位"策略（v17.1）——agent 接到 N 章窗口任务时，第一个动作是 Write 一个最小骨架 continuity JSON（仅含 window 标识 + TODO 占位字段），再依次写单章 JSON，最后回填 continuity 正文
- **影响**：实战收益——后续批次 L2.1 触发率从「每阶段约 2 次」降到「最末阶段 1 次」，下降约 50%；救援 agent 不再需要"凭印象重建衔接结构"，只需要"基于已写完的单章数据填充骨架字段"，救援 token 成本降低约 60%
- **修复**：在调度模板和 agent prompt 模板中默认嵌入「空架子优先」指令：
  ```
  执行顺序硬约束：
  1. FIRST: Write 一个 continuity_chN-M.json 骨架（含 window/anchor_chapters/transitions: "TODO"/handoff_notes: "TODO"）
  2. 然后依次 Write 单章 JSON 1 → 2 → 3（每个写完后输出 [CHECKPOINT k/4 done]）
  3. LAST: Edit 回填 continuity 的 transitions 和 handoff_notes 字段
  ```
- **预防**：所有跨章节分析类 agent（衔接/反应链/伏笔追踪）默认采用「空架子先占位」模式，把"跨章节产物"做成"先存在再完善"而非"最后一次性创建"
- **关联**：L2.1（同根因不同修法）/ L2.4（Write 一次性写入与空架子的协调——空架子用 Write，正文回填可用 Edit）

### L2.2 ⛔ Agent 过度优化反模式
- **现象**：Agent 一直 +1 分迭代，从 v7 SFS 87.40 跑到 v8 跌到 84.83 想回退，被 watchdog 杀
- **影响**：失去优化中间产物，需重跑或手动接管
- **修复**：prompt 加明确"3 轮内收手"纪律（v1 出稿 → v2 修关键问题 → v3 微调）
- **预防**：复刻类 Agent 在 prompt 里写：
  ```
  ⚠️ 3 轮内收手。避免过度优化迭代。
  - v1: 出第一版
  - v2: 修关键问题
  - v3: 微调
  不要为了+1分跌入 v8/v9 回退陷阱。
  ```

### L2.3 ⚠️ 救援 agent 必须明确"跳过重跑"
- **现象**：救援场景下，agent 看到 _tmp.txt 又重新跑了一遍 style_analyzer 浪费 tool calls
- **影响**：救援耗时多一倍，token 浪费
- **修复**：救援 prompt 头部必须写：
  ```
  已有（无需重跑）：
  - metrics.json: <路径>
  - _tmp.txt: <路径>
  - 已完成 JSON: <文件列表>

  缺失（需补）：<文件列表>

  任务：跳过 style_analyzer 重跑，直接基于已有数据补写缺失文件
  ```
- **预防**：救援场景写清楚"哪些已有/哪些缺失"，agent 才能跳过冗余步骤

### L2.4 ⚠️ Write 一次性写入 > Edit 增量构建
- **现象**：Agent 用 Edit 一次次追加构建大 JSON，tool calls 翻倍
- **影响**：23-33 tool calls 才完成一个 3 章任务，临近 turn limit
- **修复**：prompt 加效率铁律：「**每个 JSON 用 Write 工具一次性写完，不要 Edit 增量构建**」
- **预防**：复杂 JSON 输出场景默认指定 Write 一次性写入


### L2.7 ⛔ 单次蒸馏批次硬上限 300 章 [MANUAL · 2026-05-13]
- **现象**：单次会话尝试跑全书 2000+ 章，闭环验证发现核心问题（如 v17 之前的 1 章/agent 颗粒度问题）时，已经投入大量 token 和时间。回退/重做代价巨大。本次蒸馏前 200 章已耗时数小时 + 多次救援 + API 限额触发
- **影响**：
  - 失败成本随章数线性上升（500 章发现问题 vs 50 章发现问题，代价差 10 倍）
  - 容易触发账户配额（200 章约触发一次 API 限额）
  - 主代理上下文积累过快，task notification 占比过高
  - 出现严重 skill 设计问题时，已蒸馏数据可能要全部重跑
- **修复**：单次 `/distill-style` 命令**硬上限 300 章**
  - 单批 ≤ 300 章（推荐 200-250 章）
  - 大书分多个会话跑：第 1 次 Ch1-250 → 验证收敛 → 第 2 次 Ch251-500（基于上次 skill 增量）→ 以此类推
  - 阶段 0 检测到用户提供章数 >300 时，**主动询问分批策略**而非默认全跑
- **预防**：
  - distill-style.md 命令文档加硬性规则（违反时拒绝执行）
  - 主代理预处理时打印「本次蒸馏 N 章，建议分 ⌈N/250⌉ 批」
  - 每完成一批先跑阶段 2-6 闭环验证再启动下一批，避免"蒸馏半天验证发现不符合要求"
- **关联**：L2.2 过度优化反模式 / L4.6 蒸馏轮次预算 / L1.4 配额规划

### L2.6 ⚠️ 末组合并规则（窗口尾部不平衡时）`[DEPRECATED v22.cluster]`
- **状态**：v22.cluster 改 cluster_segmenter 切分后，末窗口由 segmenter 启发式自动处理（max_chapters/end_of_book 边界），不再需要"末 1-2 章手动并入前窗口"。本条保留作 v17 历史记录。
- **现象（v17 时代）**：N 章蒸馏按 3 章/窗口切分，遇 N % 3 ≠ 0 时末窗口剩 1-2 章无法独立成窗
- **影响**：末窗口章际衔接分析样本不足；强行单章窗口违反"≥3 章窗口"原则
- **修复**：末组允许合并为 4-5 章窗口（如 ch196-200 五章合并），单次 agent 处理。prompt 显式标注「末组合并规则」并允许 ≤5 章窗口
- **预防**：调度模板内置末窗口检测：`if remainder in [1,2]: merge into previous window`。意外的好处——五章合并往往产出章首/章末多样性教科书样本
- **v22.cluster 替代**：见下方 L2.8 「cluster 故事块自适应切分」

### L2.8 ⛔ Cluster 故事块自适应切分（v22.cluster 主轨规则 · [MANUAL · 2026-05-24]）
- **现象**：v17 的 "3 章固定窗口" 与 写作端 ECAS cluster 模式（gen_writer.py --cluster N，长度 2-6 章 / 4000-20000 字）颗粒度错位 → 蒸馏出的"3 章衔接模板"对 cluster 写作没用。2026-05-24 BookA重蒸 Step 1 主代理盲套 v4.3 历史的 66 三章窗口，被用户指出系统已改 cluster
- **影响**：
  - 蒸馏 / 写作两端颗粒度错位 → 蒸馏指纹 30%+ 不被写作端使用
  - 主代理读 lessons 时被 v17 老规则误导，重复翻车
- **修复（强制）**：
  1. 主代理 Step 1 第一动作 = Read `workspace/styles/<书名>/cluster_index.json`
  2. **场景 A**（已蒸馏书重蒸）：cluster_index 存在 → 直接复用（含 strong 边界）
  3. **场景 B**（新蒸馏 / 全清后重做）：cluster_index 不存在 → 跑 `cluster_segmenter.py` 产首轮裸切索引（strong=0 是设计，必经阶段）
  4. Agent 调度循环 = `FOR cluster in cluster_index.clusters`，**不**用章数取模
  5. 单 cluster agent 任务：读 N 章原文 → 产单章 JSON × N + cluster 衔接 JSON × 1
  6. **场景 B 必做的 retro-refine**：表层蒸馏完成后再跑一次 `cluster_segmenter.py`，此时 segmenter 能读 continuity 数据自动识别 strong 边界，输出 cluster_index 升级到 v22.cluster.2 等级
  7. 后置聚合：`python core/scripts/arc_aggregator.py --project ... --all-clusters` 自动产 cluster_arc + 副轨 fixed10
- **预防**：
  - lessons 元数据明确标注「当前主轨：v22.cluster」（已修，2026-05-24）
  - 任何蒸馏方向决策前**必须** Grep `cluster_index|v22\.cluster` 验证当前规则版本
  - 不要把"首轮裸切 strong=0"误判为"降级"或"质量缺陷"——这是场景 B 自然态
  - L8.2 plan_tracker contract 字段新增 `CLUSTER_ID` + `CHAPTER_RANGE`
- **业界依据**：LumberChunker (arXiv 2406.17526, EMNLP 2024) variable-length 比 fixed-N +7.37% DCG@20；MARCUS (arXiv 2510.18201, 2025) event-centric；Multi-Agent TV Arcs (arXiv 2503.04817, 2025) 自然终结
- **翻车实例 v1.1（2026-05-24 v6 全清重做）**：主代理裸切出 34 cluster · strong=0 后误判为"降级"停下来报警；实际是场景 B 必经阶段。修复后写入 v22-cluster-migration.md v1.1 §0 + §3.4
- **关联**：L4.5（颗粒度选择历史）/ L4.6（轮次预算）/ L9.4（蒸馏维度审计）

---

## 3. 数据质量教训

### L3.0 ⛔ 文件路径必须遵守 STRUCTURE.md [MANUAL · 2026-05-13]
- **现象**：早期蒸馏命令文档写 `_数据库/蒸馏进度/...` 路径，但实际本次蒸馏用了 `workspace/styles/{书名}/蒸馏进度/...`。命令文档与实际产出路径不一致
- **影响**：未来调用命令时 agent 可能写到错误位置；用户难以定位产出文件
- **修复**：建立 `core/claude-home/STRUCTURE.md` 作为权威路径规范，所有命令文档反向引用，CLAUDE.md 显式声明优先级
- **预防**：
  - 任何新命令开发前先读 STRUCTURE.md
  - Agent prompt 中**永远用绝对路径**（如 `<REPO_ROOT>/workspace/styles/{书名}/...`），禁止相对路径
  - 命令文档「输出路径」部分必须有链接指向 STRUCTURE.md
- **关联**：CLAUDE.md / STRUCTURE.md 第一/二/九节

### L3.1 ⛔ JSON 转义错误前置检测
- **现象**：6+ 个章节 JSON 含未转义双引号/单引号（如 `"无"字`、列表元素裸双引号）
- **影响**：聚合 agent 跑 json.load 报错，需要先修复才能继续
- **修复**：聚合 agent 应该有 fallback：尝试 parse → 失败时用正则定位损坏位置 → 就地修复
- **预防**：
  - Agent 写 JSON 后立即 `python -c "import json; json.load(open('...'))"` 自验
  - 在 brief 模板里加「JSON 自检步骤」
  - 给 Write 工具的 content 中含中文引号时优先用 `\"` 或全角引号
- **补充细节（2026-05-13, 大批量章节蒸馏）**：JSON 损坏分级处置——主 JSON（含整段叙述文本）转义失败概率高但**不致命**（聚合阶段用 metrics JSON 兜底）；metrics JSON 是数值字段为主**必须 100% 可读**。救援策略分级：
  - 损坏的主 JSON：记录章节号入"unique_signature 缺失清单"，不阻塞聚合（仅影响该章独有指纹文本统计）
  - 损坏的 metrics JSON：立即触发救援 agent 单章重跑，量化基线绝不能缺
  - 两份都损坏：整章重跑
- **补充细节（2026-05-13 晚, 大批量增量蒸馏）**：**新增高频损坏模式清单**（除原有未转义中英文双引号外）：
  - **正号嵌入数值**：写 `"漂移": "+8.27%"` 时，`+` 是合法 JSON 字符串内字符，但 agent 偶尔会写成 `"漂移": +8.27%`（去掉引号），导致 JSON parser 报"unexpected token +"
  - **双引号嵌套场景**：黄金段落字段含原文对白（如`"对白": "他说\"你来了\"。"`），agent 若用单层转义或漏转，立即损坏
  - **方括号术语作为 key**：作者风格含【XX】方括号术语，agent 试图把它当 JSON key（如 `"【瑶光】斩纸人": 1`）通常 OK，但出现在数组里时偶尔出现裸 `【】` 不闭合
  - **救援回归**：随着批次推进，**JSON 损坏率不一定下降**——后期增量段虽然 agent 经验积累更多，但章节复杂度提升（多重转折/多角色对白）也提升损坏概率。结论：JSON 自校验步骤必须**每批必跑**，不能因前期通过就放弃
  - **自校验铁律**：agent 写完每个 JSON 后 prompt 强制要求执行 `python -c "import json; json.load(open('文件名'))"`，**失败立即就地修复**而非交给聚合阶段

### L3.2 ⚠️ 文件命名严格遵守路径规范
- **现象**：sub-agent 把救援文件写到 styles 目录而非项目子目录
- **修复**：prompt 全部用绝对路径，禁止相对路径

### L3.3 💡 大文件用 cat heredoc，禁用 Write
- **来源**：用户全局规则 CLAUDE.md
- **触发条件**：>500 行的文件
- **修复**：用 Bash `cat > file << 'EOF'`，超长截断用分段 Edit

---

## 4. 方法论教训（蒸馏算法本身）

### L4.1 ⛔ 「基线区间不是必填堆砌」
- **现象**：v0.5 给的句长 std ≥15、单句段 ≥70%、拟声段 0-4 等是"作者 200 章分布的下限/区间"，AI 误读为"这一段必须填到的值"
- **影响**：v0.5 round1 SFS 均值仅 74.36（C+），过度堆砌 3 个方括号术语、4 段拟声等
- **修复**：v0.5.1 加：
  - CC-39: 基线区间是参考，不是任务
  - RL-14: 上限非目标值
  - 「ref 实际值优先」原则
- **预防**：skill 文档头部必须有「step 0 必读：基线 vs 任务」章节，明确写「区间下限是参考，不是必填」
- **补充细节（2026-05-13, 大批量章节蒸馏）**：演进路径分三层——(1) v0.5.1 修「区间不是填空题」；(2) v0.5.2 进一步修「上限不是安全目标值」——AI 读单边上限"≤15%"会启动"低一点更安全"策略，遇到 ref 实际贴近上限时反向偏离扣分；(3) 终极原则：**single-ref 模式下 ref 实际值才是目标，区间是防偏离红线**。每条 CC 约束必须标注 `semantic: "防偏离红线" | "目标值" | "下限保护"`，并强制双侧区间

### L4.2 ⛔ 章型亚型必须显式拆分
- **现象**：v0.5.1 战斗章只有一个章型，但作者实际有 2 种亚型（拟声密集型 / 诗化排比型）。复刻"拟声密集型"对照"诗化排比型"原文，硬冲突
- **影响**：Battle round2 SFS 75.04 回退
- **修复**：v0.5.2 加战斗章亚型字段 + 决策树（先判 ref 是哪一型，再复刻）
- **预防**：聚合阶段如果章型内方差 >30%，自动拆亚型
- **补充细节（2026-05-13, 大批量章节蒸馏）**：亚型识别决策树必须可执行（不能模糊语义）。范例——战斗章决策树：
  ```
  if 整章拟声段 >= 3 → 拟声密集型（1v1 + 玩笑收束 + 短句节拍器）
  elif 整章拟声段 <= 1 and 对话占比 >= 0.35 → 诗化排比型（群戏觉醒 + 单句段诗化 + 0 拟声）
  else → 默认拟声密集型（兜底）
  ```
  evaluator 也必须先跑决策树识别亚型再加载对应 baseline，否则 skill 和工具会硬冲突

### L4.3 ⛔ 章型功能词区间必须分化
- **现象**：「的」「却」等功能词在不同章型差异巨大（觉醒型「的」≥60/k，压抑型「的」≈40/k）
- **影响**：psychology round2 「的」偏低 -32.63 / 「却」反向偏离 +5.44
- **修复**：v0.5.2 按章型分化功能词区间
- **预防**：聚合时按章型分别统计功能词，不只看全书均值
- **补充细节（2026-05-13, 大批量章节蒸馏）**：功能词分化需要**双向约束**：
  - 部分章型需"下限保护"（如觉醒型『的』≥60/k，AI 默认偏好精简定语会失真）
  - 部分章型需"上限保护"（如觉醒型『却』≤2/k，AI 训练偏好用『却』做转折会反向偏离）
  - 章型参数表新增 `function_word_floor_per_1k` 和 `function_word_ceiling_per_1k` 两个字段
  - 提供 4+ 种替代手法（如『却』的替代：动词对仗 / 物象反差 / 破折号 / 句号切分）

### L4.4 ⛔ CC 约束必须双侧区间
- **现象**：CC-37 极短段 ≤15% 单边上限，AI 把上限当"安全目标"控到 5%，遇到 ref 实际值 11% 时反向偏离扣分
- **影响**：opening round2 极短段维度从 94 跌到 44
- **修复**：v0.5.2 改 CC-37 为章型相关双侧区间（如喜剧章 [5%, 13%]）
- **预防**：所有 CC 约束默认双侧区间，单边约束需明确标注「下限不限」或「上限不限」

### L4.5 ⛔ 「1 章/agent」颗粒度问题 `[SUPERSEDED v22.cluster]`
- **状态**：v17 升级（3 章/agent）解决了 1 章颗粒度盲填问题，但本身已被 v22.cluster 故事块自适应颗粒度（3-6 章）取代。本条保留作历史
- **现象**：单 agent 只看 1 章，dim26-27 章际衔接只能凭印象盲填
- **影响**：v17 升级前 Ch1-25 衔接分析质量不足
- **修复**：v17 改为 3 章/agent + 独立 continuity JSON，agent 真读上下文
- **预防**：任何"需要上下文"的分析维度，必须设计 ≥3 章窗口的 agent
- **v22.cluster 升级**：颗粒度由 cluster_segmenter 按情节单元自适应给出（3-6 章）；最小 cluster ≥ 3 章 ≥ 4000 字，沿用"≥3 章窗口"原则。详见 L2.8

### L4.6 ⚠️ 蒸馏轮次预算 `[SUPERSEDED v22.cluster]`
- **状态**：v17 的"3 章/agent = 67 调用"已被 cluster 颗粒度取代（37 cluster ≈ 37 调用，约 -45%）。本条保留作历史
- **现象**：200 章按 1 章/agent = 200 调用，3 章/agent = 67 调用
- **修复**：v17 选 3 章/agent 是颗粒度 / 章际衔接 / 调用次数 / 失败重做代价的甜点
- **预防**：未来章节数 N 时，每 agent 处理章数 ≈ √(N/100) × 3 作为起点
- **v22.cluster 升级**：调用次数 ≈ cluster_index.clusters 总数（不再按章数估算）；典型 200 章 → 30-40 cluster；500 章 → 80-100 cluster。预防字段改为：「Agent 数 = len(cluster_index.clusters)，由 cluster_segmenter 启发式产出」

### L4.7 💡 闭环测试 vs 实质收敛
- **现象**：v17 严格收敛标准「连续 2 轮 ≤2 维度变化」，但某些维度变化是工具盲点不是 skill 缺陷
- **修复**：阶段 5 做 root cause 分析。如果差距维度归因为"工具盲点 / 章型亚型"，记入 future work 而非阻塞收敛
- **预防**：收敛判定要区分「skill 问题 / 工具问题 / 边界问题」三类
- **补充细节（2026-05-13, 大批量章节蒸馏）**：**RCA 分离协议**（每轮闭环复刻必跑）：
  1. 列出所有差距维度（dimension-level diff）
  2. 对每个维度问三问：
     - (a) 是否 skill 文档/约束/参数表缺失或表述不清？→ A 类（进 skill 修补）
     - (b) 是否 evaluator 公式不准 / 算法盲点 / ref 选择问题？→ C 类（进 future work）
     - (c) 是否单样本噪声 / 章型亚型边界？→ B 类（进 multi-ref 路线）
  3. **决策铁律**：C 类差距不阻塞 skill 收敛，只进 future work 清单
  4. **反例**：v0.5.2 round2 battle 4 个差距都被错误归为 skill 问题，浪费一轮迭代——分离后才发现 4 个全是 evaluator 工具盲点

### L4.8 ⚠️ 闭环复刻三轮迭代法
- **现象**：单轮复刻 SFS 提升有限，但盲目多轮迭代易陷入过度优化或回退陷阱
- **影响**：v0.5 → v0.5.2 三轮迭代实现 SFS 74.36 → 85.72 → 93.64（+29% 跃升），但若延伸到第 4 轮收益递减
- **修复**：三轮固定节奏——
  - **Round 1**：用初版 skill 复刻 N 个章型样本，识别共性问题矩阵（≥2 个样本同问题 = 共性，单样本问题挂 future work）
  - **Round 2**：修订核心共性问题后再跑，目标 SFS ≥80（B 级）；暴露的新差距做 A/C 分离
  - **Round 3**：仅修 A 类剩余差距 + 短样本/亚型精细化，目标 SFS ≥90（A 级）
- **预防**：
  - 三轮内未达标 → 触发 RCA 分离，剩余差距入 future work，不强行 round4
  - 共性度阈值：3 个样本中 ≥2 个命中 = 共性问题（必修）；1/3 = 单点（挂记录不阻塞）
  - 每轮固化版本号（v0.5 / v0.5.1 / v0.5.2），版本快照可回滚

### L4.9 ⚠️ 闪光点保留规则（修订不能动已成功项）
- **现象**：修订过程中容易"为修而修"——动了上一轮验证过的成功约束，导致回退
- **影响**：round2 修战斗章亚型时差点动了 round1 修对的 CC-37/38/39/40/41（已守护成功的极短段/中长句/拟声段/链式动作/感叹号约束）
- **修复**：每轮 lessons_learned 文档必须有"闪光点清单"章节，列出本轮所有 PASS 的维度。下轮修订前先 grep 闪光点清单，**禁止动这些约束**
- **预防**：
  - 修订模板加 checklist：「本次修订是否动了上一轮的闪光点？如是 → 必须有数据证据证明该闪光点反向失效」
  - 闪光点回归测试：每轮完成后跑一遍闪光点验证脚本，确保没有意外破坏

### L4.10 ⛔ Rollback 纪律（无数据支撑别 commit）
- **现象**：复刻过程激进改动策略 → SFS 跳水后回退原版反弹 +15 分（如 psychology v6 拆短段策略 → SFS -4.63 → rollback 后 +15.79）
- **影响**：浪费一轮迭代 + 中间产物混乱 + 收敛轨迹被噪声污染
- **修复**：段位移动前必须先做 dry-run 模拟——预估该改动对 le5/极短段/句长 std 等核心维度的触发概率，**模拟通过才 commit**
- **预防**：
  - 每个候选修订打上"风险等级"（低/中/高）。高风险（如颠覆核心策略）必须 dry-run
  - 建立 rollback 触发条件：单维度跌 ≥4 分 或 SFS 总分跌 ≥3 分 → 自动 rollback 上一版
  - 中间产物按版本快照保留 ≥3 版（vN-2 / vN-1 / vN），rollback 才有目标

### L4.11 ⛔ 短样本句长两极分化保护
- **现象**：短样本（500-800 字）字数紧 → AI 用 1-2 个超长句压 std → 16-30 字中段被挤压 → 节奏断裂
- **影响**：psychology round2 句长分布 gt50 23.53% vs ref 10.71%（+12.82pp）/ 16-30 17.65% vs ref 32.14%（-14.49pp）
- **修复**：短样本句长保护必须三段同时管控：
  - 短段下限：极短段（≤5字）≤15%
  - **中段下限**：16-30 字段 ≥25%（新增，关键）
  - 长段上限：gt50 字段 ≤20%
- **预防**：分布类指标必须配对「均值 + 极端值上限 + 中段下限」三个约束，不能只看两端

### L4.12 ⚠️ 链式动作动词重复检测
- **现象**：AI 复刻倾向"看了一眼…又看了一眼…再看了一眼"等链式完成态动作，造成视觉节拍器化
- **影响**：『又』字偏高 +526% / 『了』字偏高 +42%；但简单"次数限制"会误伤作者本人的高频腔
- **修复**：检测**模式多样性**而非次数：
  - 「看了一眼」连续 ≤2 次，第三次起强制换用「瞥/扫/瞄/定睛/望」
  - 「又 + 动词」单段 ≤1 次，跨段连续 ≤2 次
  - 不收紧『又』字总配额（保留作者腔）
- **预防**：anti-slop 规则库加入"链式动词检测正则"——如 `(看了一眼|按了下|醒了|笑了)\s*[。，,.]\s*\1{2,}`

### L4.13 ⚠️ 标点密度按章型 per 类全覆盖
- **现象**：v0.5 章型参数表只有破折号/省略号/逗号的章型分化，缺感叹号字段 → AI 沿用"克制"美学全用句号 → 喜剧/排比章感叹号清零
- **影响**：opening 喜剧 0 处 vs ref 3.98/k；psychology 排比 0 处 vs ref 17.13/k
- **修复**：章型参数表必须按 6 类标点 per 章型全覆盖：句号 / 逗号 / 省略号 / 破折号 / 感叹号 / 问号——每类都给 `[下限, 上限]` 双侧区间
- **预防**：聚合阶段输出"标点密度章型矩阵"（行=章型，列=6类标点），缺一类即报警

### L4.14 ⚠️ 指纹「淡化」误判 = 卷型差异化
- **现象**：单卷统计某个指纹密度下降 → 标记为"淡化项" → 后续卷型转换时该指纹回归 → 淡化判断被推翻
- **影响**：某卷训练章主导导致主线反派腔暂时退潮，被误判为"反英雄黑色幽默淡化"；后续战斗卷该指纹爆发回归
- **修复**：禁用「淡化」表述，改为「卷型差异化」描述
- **预防**：
  - 蒸馏文档模板里删除「淡化」字段，强制按"卷型差异化"维度展示
  - 多卷蒸馏必须按"卷型加权 vs 全书平均"两套视角看指纹，单卷不下"淡化"结论

### L4.15 ⚠️ 章首/章末连续相似度警告
- **现象**：某卷卷型集中（如卷尾突围+支线密集）导致章首类型聚集（连续 6 处地点短句冷开）
- **影响**：复刻易过载——AI 看到连续相似章首会进一步同质化生成
- **修复**：聚合阶段输出"章首/章末连续相似度报告"，阈值 ≥3 处连续即报警；CC 约束加"章首/章末连续重复硬约束"
- **预防**：
  - 章首/章末家族分类（如"地点短句冷开 / 空间锚定 / 对白起 / 心理切入"等），相邻章必须切换家族
  - AI 复刻指南节加"章型选择决策树"打断同根类型连续

### L4.17 ⚠️ 骨架稳定性验证（增量蒸馏的关键收敛指标）
- **现象**：单次蒸馏的核心指纹"延续率"是判断"作者风格是否被正确捕获"的强信号。如果连续多个增量版本（每版相距数十章新材料）核心指纹延续率持续 100%，证明蒸馏算法捕获的是**作者本人的稳定特征**而非样本噪声
- **影响**：缺乏"骨架稳定性"指标时，蒸馏者无法区分"已收敛 / 继续蒸馏会有新发现"两种状态，导致：(a) 过早停手错失关键章型；(b) 过度蒸馏浪费算力。**实战观测**：跨连续 4 个增量版本骨架延续率 100%——这是"该停止主线蒸馏，转向卷型差异化分析"的强信号
- **修复**：每个增量版本聚合阶段强制输出「骨架延续率报告」：
  - **核心指纹延续率**：上一版的核心指纹（作者本人稳定层，如 TTR/Hapax/单句段/功能词阈值）在本版章节中**100% 复现**（即新增章节的统计值仍落在原区间内）= 延续
  - **摆动指纹差异化率**：摆动指纹（拟声段/对话占比/方括号密度等）按卷型变化是预期行为，单独统计
  - **收敛判定**：连续 3+ 增量版本核心指纹延续率 100% + 基线漂移 |Δ%| < 10% → 主线蒸馏已收敛，进入"卷型皮肤"细化阶段而非继续扩大基线样本
- **预防**：
  - 增量蒸馏调度模板内置"骨架延续率"计算节点，每版自动产出报告
  - 报告中区分「核心指纹层（应延续）/ 摆动指纹层（应差异化）/ 新增指纹层（卷型独有）」三类，避免"全员追求延续"或"全员追求新发现"两种误区
  - 骨架稳定性证据用于支持「免重测决策」（见 L4.18）
- **关联**：L4.14（卷型差异化原则）/ L5.5（单 ref 噪声 vs 多版本稳定性）

### L4.18 ⚠️ 增量版本免重测准则（闭环验证的边际收益判断）
- **现象**：第一次闭环复刻达 A 级（SFS ≥90）后，后续增量版本（仅新增章节、骨架不变）是否需要每次都跑闭环复刻三轮迭代？实战观测——某基线版本 Round3 SFS 93.64 A 级后，连续多个增量版本未再 round-test，最终终版直接出货并被评定为生产就绪
- **影响**：每次闭环复刻三轮 = 至少 9 个样本 × 1-2k tokens × 多轮迭代，是高成本动作。盲目重测浪费算力；完全不测则承担"骨架未变但卷型新增导致 SFS 退化"的风险
- **修复**：建立**免重测的三条件准则**（同时满足才可免）：
  1. **骨架延续率 100%**（L4.17）——上一版的核心指纹在本版完全延续
  2. **基线漂移 |Δ%| < 10%**——全维度统计漂移在容差内
  3. **新增章节无颠覆性新章型**——本版新章型/亚型是已有大类的细分（如战斗章新增亚型），不是全新大类
- **预防**：
  - 不满足任一条件 → 强制重跑至少 1 轮闭环复刻（不需要三轮，但至少要验证 SFS ≥ 上版 -3 分）
  - 满足全部三条件 → 免重测，但增量版本仍需在 distillation_log 显式标注「未重测原因 + 三条件证据」，留下可追溯证据
  - **保险策略**：终版出货前最少跑 1 轮抽样验证（每章型 1 个新样本），分数不要求 A 级，只要不退化即可
- **关联**：L4.8（三轮迭代法）/ L4.17（骨架稳定性）

### L4.16 💡 复刻可反向超越早期原文
- **现象**：成熟版本 skill 复刻的样本，在某些反 AI 维度上**反向碾压**作者早期章节的原文（如早期章节作者还在用 AI 句式 "似乎/仿佛"，但 skill 蒸馏的是完整轨迹的成熟形态）
- **影响**：单 ref 评测时复刻样本对早期章节"扣分"反而是好事，不应强行回退迁就早期原文
- **修复**：
  - 闭环测试 ref 选择优先用"作者成熟期章节"（如总章节数后 2/3 区段）
  - 如必须用早期章节做 ref，需在差距报告中标注"反 AI 反向超越项"为正向加成而非偏离
- **预防**：ref 选择算法加权——`weight = 0.3 * 章型代表性 + 0.7 * 章节位置 / 总章数`，向后期倾斜

## 5. 评估工具盲点（Future work）

### L5.1 💡 style_evaluator 短样本对话下限
- **现象**：500-800 字短样本对话占比天然低于全章基线
- **影响**：被工具误判扣分
- **路线图 FW-1**：evaluator 集成「gen ≤1000 字时切换短样本基准表」

### L5.2 💡 speaker_count 阈值评分
- **现象**：作者"去标签化"群戏被识别为 0 speaker
- **路线图 FW-2**：evaluator 用去标签识别算法 + 阈值评分

### L5.3 💡 战斗章亚型自动识别
- **现象**：评估时要手动选 ref，没法自动判
- **路线图 FW-3**：evaluator 加亚型自动识别（基于拟声密度 / 长句比例）

### L5.4 💡 字数合规度未计入 SFS
- **现象**：严守字数反而扣分（与超字数版本对比）
- **路线图 FW-4**：SFS 加字数合规度维度（权重 0.03）

### L5.5 💡 单 ref 评测噪声
- **现象**：单样本特征拖低评分洼地 ~10 分
- **路线图**：multi-ref 评测框架（多个 ref 取均值 / 最优值）

---

## 6. 通用红线

### RL-G1: 不要因为限额/失败放弃全量
- 全量任务遇到限额 → 等重置 + 救援，不是改采样
- 用户明确全量就是全量

### RL-G2: 不要伪造数据
- 量化数据必须真跑 style_analyzer，不能 LLM 估算
- 黄金段落必须原文逐字摘录

### RL-G3: 不要平均化颗粒度
- 3 章/agent 必须输出 3 个独立 JSON，不能合并

### RL-G4: 不要把"完成"等同于"自嗨"
- 完成必须有数据证据（json.load 验证 / 文件大小 / 字段完整性）

---

## 7. 元教训：写经验库的经验

### L7.1 经验条目格式
每条必须有：现象 → 影响 → 修复 → 预防

### L7.2 去重规则
新经验追加前先 grep 现有库：
- 相同根因 → 在原条目下补「补充细节」字段
- 不同根因相同现象 → 新条目
- 完全相同 → 不追加

### L7.3 自动提取的范围
仅提取"跨项目通用"教训。**不要**提取：
- 项目特定的角色名 / 章节号
- 项目特定的术语
- 已经在 distill-style.md 命令文档里有的规则

### 自动提取记录 2026-05-13 / BookB（v17 首次蒸馏） / 追加 10 条 / 补充 5 条
- **新增条目（10）**：L2.6（末组合并规则）/ L4.8（闭环复刻三轮迭代法）/ L4.9（闪光点保留规则）/ L4.10（Rollback 纪律）/ L4.11（短样本句长两极分化）/ L4.12（链式动作动词检测）/ L4.13（标点密度章型全覆盖）/ L4.14（淡化误判=卷型差异）/ L4.15（章首章末连续相似度警告）/ L4.16（复刻反向超越早期原文）
- **补充细节（5）**：L3.1（JSON 损坏分级处置）/ L4.1（演进三层 + ref 实际值优先）/ L4.2（亚型识别决策树）/ L4.3（功能词双向约束）/ L4.7（RCA 分离协议）
- **跳过条目（4）**：L4.4（双侧区间，完全相同）/ L4.5（颗粒度，完全相同）/ L4.6（轮次预算，完全相同）/ L5.5（单样本噪声，完全相同）

### 自动提取记录 2026-05-13（晚） / BookB v1.0（第二批增量蒸馏） / 追加 3 条 / 补充 3 条
- **新增条目（3）**：
  - **L2.1.1**（空架子 continuity 先占位）——v17.1 实战验证有效的抗 turn 截断最佳实践，与 L2.1 同根因不同修法
  - **L4.17**（骨架稳定性验证）——增量蒸馏跨多版本 100% 指纹延续率的方法论沉淀，区分核心/摆动/新增三类指纹
  - **L4.18**（增量版本免重测准则）——A 级基线后续增量版本免闭环复刻的三条件准则（骨架延续率 + 基线漂移 + 无颠覆章型）
- **补充细节（3）**：
  - **L1.4**（多窗口配额规划）——大批量任务可能触发多次配额耗尽，且短周期/长周期重置策略不同 + 预判暂停 + 救援链路抗配额
  - **L2.1**（高频根因分析）——大批量增量段累计触发约每阶段 2 次，prompt 警告被动约束不够强 → 顺序硬翻转 + 强制 checkpoint
  - **L3.1**（新增 JSON 损坏模式清单）——正号嵌入数值 / 双引号嵌套 / 方括号术语作 key / 损坏率不随批次推进下降的反直觉发现
- **跳过条目（2）**：
  - "100% 指纹延续率连续 4 版" 的项目特定数据（已在 L4.17 模糊化为"连续多个增量版本"通用规则）
  - "闭环 SFS 93.64 后免重测" 的项目特定数值（已在 L4.18 模糊化为"A 级 SFS ≥90 后"通用准则）

---

**最后更新**：2026-05-13（晚 · 第二批增量蒸馏 v1.0 410 章正式终版 / 自动提取 +3 条 / 补充 3 条 / 跳过 2 条 · 累计经验库 L1-L7 共 35+ 条目）

---

## 8. Plan 强制规划层教训（v17.2 引入）

> 本章沉淀 plan_tracker 体系（PreToolUse hook + 6 命令模板 + plan_id/step/end 三段 CLI）从设计到上线过程中暴露的根因级问题。**用户称作「§六」，编号紧接 §7 元教训为 §8。**

### L8.1 ⛔ 跳阶段是默认行为，必须 plan 强制
- **现象**：多步命令（save-state 11 步 / distill-style 7 阶段 / check-quality 3 步）在 Agent 执行时**默认会跳步**——尤其当 turn 接近 token 上限或 prompt 描述"最后一步"在文档末尾时，Agent 会"识相地"跳过"看起来不重要"的步骤直接收尾
- **影响**：
  - save-state 跳掉 WAL finalize / git snapshot / 卡片渲染 → 进度黑洞，下次断点恢复失败
  - distill-style 跳掉闭环复刻 / write_skill 阶段 → 蒸馏产物缺失 skill 文件，看似完成实际未交付
  - 多次返工 + token 浪费 + 主代理误以为"已完成"释放上下文
- **修复**：v17.2 引入 plan_tracker 强制规划层——
  - 6 命令统一模板存 `core/claude-home/plans/<command>.plan.json`
  - 命令开头必须 `python plan_tracker.py create --command X --project Y` 拿到 plan_id
  - 每步完成必须 `step <id> --n N`（脚本校验 expected_outputs 存在）
  - 命令结尾必须 `end <id>`（检查 required_steps 全部 completed）
  - 中途中止必须 `abort <id> --reason "..."` 留审计痕迹
- **预防**：
  - 命令文档头部硬性写明「无 plan_id 不开工，无 end 不完成」
  - 主代理对 plan_id 缺失立即拒绝执行，不"灵活适应"
  - 任何宣称"已完成"的输出，先 `plan_tracker status <id>` 验证 progress = 总步数
- **关联**：L1.1（Agent gate hook 契约字段）/ L8.2（PLAN_ID/STEP 字段）/ L8.4（与 WAL 共存）

### L8.2 ⚠️ Agent prompt 必须含 PLAN_ID 和 STEP 字段
- **现象**：主代理 spawn Agent 时只传 PROJECT/CHAPTER/MODE 三个 L1.1 既定字段，Agent 上下文里**完全不知道**自己处在哪个 plan 的第几步。Agent 干完活，主代理换会话/换 turn 后想 `plan_tracker step` 时，已经无法精确对应步骤号
- **影响**：
  - 跨 Agent 协作上下文断裂——主代理传给 Agent 的"任务"和 plan 模板里的"步骤"对不上
  - Agent 自己看不到 plan，无法做"我做的是第 N 步"的自我定位
  - PostToolUse hook 即使想反查也无凭无据
- **修复**：v17.2 在 L1.1 契约字段集合中追加两项硬性字段：
  ```
  PLAN_ID: <plan_tracker create 返回的 id>
  STEP: <当前步骤号，与模板 steps[].n 对齐>
  ```
  - PreToolUse hook 检测写作/蒸馏类 Agent description，若 prompt 缺 PLAN_ID 或 STEP → exit 2 拦截
  - Agent 执行完毕后由**主代理**调 `plan_tracker step --n STEP` 回写状态（Agent 自己不调，因为 Agent 上下文释放即销毁）
- **预防**：
  - 调度模板把 PLAN_ID/STEP 做成强制 placeholder，不靠主代理记忆
  - 命令文档「Agent 调用」章节先写 "spawn 前的 PLAN_ID/STEP 计算"，再写 prompt 模板
  - lessons §1.1 同步更新契约字段清单（PROJECT/CHAPTER/MODE/MANIFEST + **PLAN_ID/STEP**）
- **关联**：L1.1（基础契约字段）/ L8.1（plan 强制根因）

### L8.3 💡 「章」字误判修复 — 单字关键词改词组
- **现象**：PreToolUse hook 早期版本用单字关键词 `["章", "写作", "正文"]` 匹配 Agent description 来判定是否需要校验契约字段。但「章」字命中范围太广——例如「检查所有规章制度」「印章鉴定」等 description 也会被误判为"写作类"，导致 hook 在非写作场景错误拦截
- **影响**：
  - 非写作类 Agent（如 lessons-extractor、status-scanner）被误拦截
  - 出现 PreToolUse exit 2 但用户和主代理都困惑"明明不是写章节为什么被拦"
  - 误判一次 = 主代理花 1-2 turn 排查 hook 日志
- **修复**：把单字关键词替换为**词组**：
  - ❌ 旧：`["章", "写作", "正文"]`
  - ✅ 新：`["章节写作", "写章", "保存章节", "正文生成", "蒸馏", "闭环复刻"]`
  - 词组长度 ≥2 个字，且语义上明确指向"多步命令场景"
- **预防**：
  - hook 关键词新增/修改必须先跑误判测试集（取 10 个无关 Agent description 做反向验证）
  - 关键词清单做成可配置（`core/claude-home/hooks/gate_keywords.json`）而非硬编码
  - 任何「单字关键词」入库前必须三思——中文单字几乎都是高频字
- **关联**：L1.1（hook 契约校验根源）

### L8.4 ⚠️ Plan 与 WAL 共存策略
- **现象**：save-state 命令内部已有 WAL（`completed_steps` 字段）做断点恢复——按理说 plan_tracker 重复造轮子。但 WAL 是「单命令内的细粒度断点续跑」，plan_tracker 是「所有命令统一的强制规划层」，二者**定位不同不能合并**
- **影响**：
  - 若强行合并：WAL 字段绑死 save-state，distill-style 等其他命令无法复用 → 设计退化
  - 若两层都改 WAL：跨命令字段语义混乱
  - 若两层都改 plan：WAL 的细颗粒断点续跑能力被牺牲
- **修复**：**职责分离设计**——
  - **WAL** 负责：save-state **单命令内** 11 步的细粒度 completed_steps 跟踪 + 断点恢复
  - **Plan** 负责：6 命令统一的**粗颗粒**强制规划 + 跨命令审计 + plan_id 全局唯一性
  - 二者字段不互通——plan_tracker 不读写 WAL，WAL 不读写 plan_tracker 状态
  - 在 save-state 内部：WAL 决定"从第几步续跑"，plan 决定"整个 save-state 是否已 end"
- **预防**：
  - 任何新命令引入"内部断点"系统时，先回答「这是 plan 粒度还是 WAL 粒度」
  - 文档显式声明 plan 和 WAL 的关系（plan_tracker.py 头注释 + CLAUDE.md「Plan 强制规划」章节）
  - 测试用例覆盖「plan_tracker step 中途断电 → WAL 续跑 → plan 状态自洽」场景
- **关联**：L8.1（plan 引入背景）/ save_state.py WAL 实现

### L8.5 ⛔ posttooluse hook 严禁拦截
- **现象**：早期 PostToolUse hook 设计成"扫描 Bash 输出发现 plan 异常 → exit 2 拦截后续工具调用"。结果：
  - Bash 输出里有 stderr 噪音 → hook 误判为异常 → 主代理被打断
  - Plan 校验失败但用户仍想继续看下一条命令的结果 → hook 强行拦截
  - 主代理一旦被 PostToolUse 阻断，无法优雅恢复（PostToolUse 阶段工具调用已经发生，拦截只能"事后惩罚"）
- **影响**：
  - 主流水线被无脑打断，用户体验崩坏
  - 主代理陷入"PostToolUse 报错 → 重试 → 再被拦"的死循环
  - 信任崩塌：hook 本来是辅助工具，反而成了阻塞器
- **修复**：**posttooluse hook 严禁拦截，永远 exit 0** ——
  - PostToolUse 仅做：日志上报 / 状态扫描 / `[INFO]` 提示
  - 任何"异常"都用 stdout 打印 `⚠️ [INFO]` 让主代理在下一 turn 自然感知，不靠 exit 码强制
  - 拦截能力**只交给 PreToolUse**（在工具调用前判定，可干净拒绝）
- **预防**：
  - hook 文件头注释明确写「PostToolUse: exit always 0」铁律
  - hook 测试集必须有「故意制造异常 → 验证 hook 仍 exit 0」用例
  - 评审 hook PR 时把 `sys.exit(非 0)` 出现在 PostToolUse 文件中视为 P0 阻塞
- **关联**：L8.1（plan 体系的边界控制）/ L8.3（误判修复）

---

## 9. 蒸馏-写作落地层教训（v17.3 引入）

> 用户 2026-05-14 反馈："蒸馏系统中会注意开头结尾以及各种描写，但是放到写作上却没有在意，两本书都存在这个问题"。
> 这一节专门记录蒸馏库（`作者风格_FINAL.json`）的精细数据如何被强制下沉到写作的经验。

### L9.1 ⛔ 蒸馏库写完不等于会被用——必须有强制落地脚本
- **现象**：v17 蒸馏出的 `cross_chapter_diversity`（11 种 opening_type 分布 + opening_rule "连续3章禁止重复"）、`env_anchor_high_risk_elements`（带"每3章不超过2次"等量化策略）、`golden_passages`（真实样本），novel-writer agent 完全没读过——它只读 `skill_FINAL.md` 散文规则
- **影响**：
  - 灯塔守人前 3 章 opening_type 全是"拟声定格"，直接违反BookC蒸馏出的 opening_rule
  - 蒸馏端 35 维度精细分析的价值 ≈ 写作时被忽略 80%
  - 两本书（BookC蒸馏的写作 + BookB蒸馏的写作）都出现同症状——证明是系统级 bug 而非单章失误
- **修复**：v17.3 引入 `core/scripts/style_injector.py` 作为蒸馏↔写作的中间层 ——
  - 输入：作者风格_FINAL.json + 章纲摘要（历史 applied_style）+ 章节号
  - 输出：`_数据库/.style_directive/ch_NNN.json` 含 opening_type/ending_type/transitions/anchor_strategy/narrative_targets 等强制指令
  - 轮拿算法：基于 `opening_type_distribution_300ch` 权重 + 前 N-1 章 avoid 列表，确保不违反 `opening_rule`
  - 注入路径：build_manifest 调用 style_injector，把 directive 嵌入 manifest 同时落地到 `.style_directive/ch_N.json`
  - novel-writer 强制：写前 Read directive，CHANGES 必含 `applied_style` 字段（opening_type/opening_line/anchors_hit/...）
- **预防**：
  - 任何"蒸馏端有但写作端可能漏读"的字段都要被 style_injector 显式转译成可执行指令
  - 评审 writer prompt PR 时：每一条蒸馏维度（35+）都要追问"writer 怎么用上它"，没答案的字段视为浪费
- **关联**：L9.2（validator 复核）/ L9.3（applied_style 撒谎防御）

### L9.2 ⚠️ writer 申报必须可被 validator 验真——防"假应用"
- **现象**：如果只让 writer 在 CHANGES 中报告 "opening_type=X"，writer 可能撒谎（实际开头是 Y 但报告 X，因为读者看不出）
- **影响**：
  - 蒸馏库的轮拿规则形同虚设——writer 永远报告"对的 type"但实际全是同一种
  - 数据库里的 applied_style 历史变成假数据，下次 style_injector 拿来轮拿 → 算法基于假数据决策 → 进一步漂移
- **修复**：novel-validator-repair 加入 **style_directive 复核**——
  - 比对 `applied_style.opening_line` 是否 == 正文真实首句（substring 匹配）
  - 比对 `applied_style.anchors_hit` 中每个 anchor 是否真的出现在正文（substring）
  - 比对 `applied_style.opening_type/ending_type` 是否落入 `opening_avoid/ending_avoid`（落入 = 致命违规要回炉）
  - 撒谎处理：致命违规 → 回炉 writer；轻度撒谎 → Edit 改 applied_style 让数据真实
- **预防**：
  - 任何"writer 自报申报"字段都必须有"可机器验真"的字段配套（如 opening_type 配 opening_line）
  - 不可让 writer 申报无法被 grep / substring 验证的内容（如"本章节奏紧张"这种主观语）
- **关联**：L9.1（style_injector 注入）

### L9.3 💡 style_directive 的轮拿不能是"绝对禁止"，要"权重避免"
- **现象**：初版 style_injector 实现了 "avoid 列表里出现过的 type 一律不选"。结果：当前章 chapter_plan.scene_type 强烈倾向某 type，但该 type 恰好在 avoid 里 → 算法被迫选低权重 type（如 1.8% 的"人物内心吐槽"），可能与本章实际剧情冲突
- **影响**：
  - 风格反重复 vs 剧情类型匹配 之间张力管理失衡
  - 强制选超低频 type 反而显得突兀
- **修复**：（当前为权重抽样 + avoid hard exclude，未来可改为）——
  - avoid 内 type 权重 ×0.1 而非完全排除，让算法在多样性和场景适配间软平衡
  - 加入 chapter_plan.scene_type 倾向加权（如战斗章 +"动作承接"权重，内省章 +"心理铺陈"权重）
- **预防**：style_injector 输出 directive 时必须含 `opening_pick_reason` 字段（如 `weighted_pick(0.018/0.640)`），便于后期审计是否过度偏离高频 type

### L9.4 ⚠️ 蒸馏 35 维度审计——写作时未使用 = 浪费
- **现象**：作者风格_FINAL.json 共 9 个顶层 key，35+ 细维度。novel-writer 实际仅明显使用 ≤6 个（writing_rules、anti_patterns、character_voice_pack、style_profile.narrative、style_profile.dialogue、golden_passages.opening_passages 偶尔）。其余 29 维度无强制落地
- **影响**：蒸馏成本 vs 实际收益 比值严重失衡
- **修复**：v17.3 通过 style_injector 把 cross_chapter_diversity（含 4 个分布 + 2 条 rule + anchor strategy + narrative_craft）落地。后续应继续审计：
  - `narrative_continuity_template`（卷间过渡模板）：当前未注入，可在跨卷章节激活
  - `subtext_types`（潜台词类型清单）：当前仅作为 narrative_targets 信息，可强制每章 ≥ 2 个 subtext 来源
  - `humor_types`（幽默类型）：当前无注入，可作为可选风格调味
- **预防**：建立"蒸馏-写作维度覆盖表"——每个蒸馏字段必须明确"被哪个脚本/agent 在何时使用"，未使用字段定期清理或注入

---

**最后更新**：2026-05-14（v17.3 蒸馏-写作落地层修复 / 新增 §9 4 条 L9.x 教训 + style_injector.py + writer/validator 契约升级 · 累计经验库 L1-L9 共 44+ 条目）

---

## 10. AI 监督 AI 分权层教训（v17.4 引入 · OpenClaw + Agent-as-Judge 启发）

> 用户 2026-05-14 提出："参考 OpenClaw 分权监督，让 AI 监督 AI"。
> 调研：OpenClaw governance（工具权限矩阵 + 浓缩通信 + OTEL audit）+
>      Agent-as-Judge 论文（judge 独立性 + 不确定性量化 + meta-judge 校准）。
> 本节记录 5 条 actor↔judge 分权设计原则。

### L10.1 ⛔ Writer 自评不可喂给 Judge（最严重断层）
- **现象**：v17.3 之前，writer 在 CHANGES JSON 末尾写 `applied_style`（自评开头类型/锚点命中/技法应用）。validator-repair 读章节文件时同时读到 CHANGES——被 writer "我用了 X" 引导。结果 ch1-3 三个 judge agent 全打 A 级，可能不是真 A，是 writer 自评导出的 A。
- **影响**：
  - 违反 Agent-as-Judge 论文核心铁律「judge 应独立观察，不接触 actor 自陈」
  - judge 失去独立性 → 监督形同虚设 → writer 长期撒谎不被发现 → 历史 applied_style 数据全假
  - 下次 style_injector 拿假数据做轮拿决策 → 系统级漂移
- **修复**：v17.4 拆 CHANGES 为双 JSON ——
  - `---CHANGES_FACTUAL---` 段：9 类客观事实变更（character_changes / location_changes / item_transfers / ...）——judge 可读，apply_changes 据此更新数据库
  - `---CHANGES_SELF_EVAL---` 段：writer 自评（applied_style / writer_notes）——**judge 不可见**，仅主代理保留供事后验真
  - 3 个 judge agent 明确要求"读章节 txt 时只读到 `---END_CHANGES_FACTUAL---` 为止"
- **预防**：
  - 任何 actor→judge 流水线设计时先问：「judge 是否会接触到 actor 的自陈？」如果是 → 拆开
  - judge 必须独立从原始产出（正文）重新提取关键指标，再与 actor 自评比对（验真链）
- **关联**：L9.1 / L9.2（style_directive 验真闭环）

### L10.2 ⚠️ Judge 报告必须含 confidence + reasoning trace
- **现象**：原 judge 只输出 A/B/C/D 级。结果：
  - 主代理无法判断 judge 自己有多确定（A 是"百分百"还是"勉强 A"？）
  - reasoning 缺失 → meta-judge 无法审计 judge 推理质量
  - confidence < 0.7 应该触发再审，但没字段就没触发条件
- **影响**：监督链路缺少信号，无法做"低信心自动再审"
- **修复**：v17.4 统一 JudgeReport schema ——
  ```json
  {"judge_id":"...","chapter":N,"overall_grade":"A|B|C|D",
   "confidence":0.85,
   "reasoning_trace":["step1: ...","step2: ...","step3: ..."],
   "specific_findings":{...},
   "uncertainty_flags":[]}
  ```
  - confidence 取值规则在 agent prompt 中明文写死（1.0=零边界 case；<0.7=触发再审）
  - reasoning_trace ≥3 步，每步 ≤50 字，按"读→比对→判定→改写→总评"链路
  - uncertainty_flags 字段强制存在（即使空 `[]`）
- **预防**：任何 LLM-judge 系统都不应只输出 grade —— 至少加 confidence；最好加 reasoning trace 供 meta-judge 审计

### L10.3 ⚠️ 关键决策必须 ≥2 judge 共识（防单点偏差）
- **现象**：单 validator 可能偏松/偏紧/被风格适配。一票判决等于赌运气
- **影响**：单 judge 看错一次 = writer 一章漂移 = 后续 N 章基于漂移基线 → 漂移指数级放大
- **修复**：v17.4 引入 `core/scripts/judge_consensus.py` ——
  - 关键维度（opening_type 匹配 / ending_type 匹配 / anchor 真实性 / voice 匹配）由 2-3 个独立 validator 并行评
  - 主代理用 majority vote + median 仲裁
  - agreement_score < 0.5 或 avg_confidence < 0.65 或任一 D 级 → 升级用户
  - dissent 列表保留供 meta-judge 长期追踪
- **预防**：
  - 不是所有 judge 调用都要 ≥2 路（成本 ×2-3）—— 只有"关键二元判定"需要（match/not-match 类）
  - 主观维度（如"voice 风格匹配度"）一定要多 judge；客观维度（如禁用词扫描）单 judge 即可
- **关联**：L10.5（meta-judge 校准）

### L10.4 ⛔ Judge 之间也要分权——不互相读对方报告
- **现象**：如果 voice-keeper 能读 validator-repair 的结论，它会被影响（"validator 给 A 了，我也给 A 吧"——羊群效应）
- **影响**：N 个 judge 退化为 1 个判决 + N-1 个 echo
- **修复**：v17.4 各 judge agent prompt 明确「不读章纲摘要中其他 judge 的评分」「同章节多 judge 并行启动而非串行」
- **预防**：
  - judge 之间通信由主代理仲裁，judge 直接交流被禁止
  - judge 启动顺序随机化（不让 actor 通过"先调用 validator-A 还是 -B"暗号引导后续 judge）

### L10.5 💡 Meta-Judge：定期审计 judge 自身
- **现象**：长期跑下来，单 judge 可能：偏松（连续 10 章全 A）/ 偏紧（动辄 D）/ 漂移（前严后松）/ 被 writer 风格驯化
- **影响**：judge 失效但用户察觉不到 → 监督名存实亡
- **修复**：v17.4 新增 `.claude/agents/novel-meta-judge.md` ——
  - 每 10 章 save-state 流程 step 9 自动触发
  - 计算 per-judge a_rate / mean_confidence / confidence_variance / drift_signal
  - 计算 inter-judge agreement
  - 输出 verdict（healthy / drifting / imbalanced）+ recommendations 给用户
  - 异常 spike（连续 5 章全 A 或全 D）立即触发，不等 10 章
- **预防**：
  - meta-judge **不可递归**（meta-judge 不评 meta-judge 自己）—— prompt 中明文写"绝对不评 meta-judge 自己"
  - meta-judge 样本量 <5 章时拒绝出结论，防小样本误判

---

## 设计哲学（v17.4 总结）

| OpenClaw 概念 | 我们的实现 |
|---|---|
| 工具权限矩阵 | 每 agent 在 frontmatter 声明 `tools: Read, Edit, Bash` 等 |
| Coordinator 不递归 | 主代理仲裁，judge 不能 spawn judge |
| Specialist 浓缩输出 | judge JudgeReport schema 限制了输出长度和结构 |
| OTEL 审计 | meta-judge + plan_tracker 提供 per-agent 历史追踪 |
| 共享文件状态 | `_数据库/章纲摘要.json` + judge_reports[] 追加式存储 |
| 紧急停止 | plan_tracker abort + user `/goal stop` |

| Agent-as-Judge 概念 | 我们的实现 |
|---|---|
| Judge 与 Actor 分离 | L10.1 拆 CHANGES 双 JSON |
| 过程级别监督 | reasoning_trace ≥3 步链路 |
| 不确定性量化 | confidence 0-1 + uncertainty_flags[] |
| 多 Judge 共识 | judge_consensus.py |
| Judge 自身可信度 | meta-judge 周期审计 |

**最后更新**：2026-05-14（v17.4 AI 监督 AI 分权层 / 新增 §10 5 条 L10.x 教训 + judge_consensus.py + meta-judge agent + 3 judge 独立性升级 · 累计经验库 L1-L10 共 49+ 条目）

---

## 11. 业界对照录（v17.5 引入 · 联网调研沉淀）

> 用户 2026-05-14 第三轮反馈："联网搜索分析下目前还有什么问题"。
> 调研 4 篇业界文章：Memory drift（kore.ai）/ Agent Memory Systems（Steve Kinney）/ Multi-Agent
> Reliability Failure Patterns（Maxim）/ Context Compaction（Microsoft Learn）+
> arxiv "Stop Wasting Your Tokens"（10.26585v2）。
> 本节记录"我们之前没做、业界已成熟"的 5 类设计。

### L11.1 ⛔ Memory Drift = 业界 #1 杀手（65% 失败归因）
- **业界数据**：kore.ai 2025 统计 65% 企业 AI 失败 = context drift / memory loss during multi-step reasoning，**不是** raw context exhaustion。
- **现象**：ch1 details 到 ch8 被稀释（详情不被关注），角色描写漂移、伏笔被弃、tone shift。
- **我们之前的应对**（v17.5 P1.4）：章纲摘要分级注入（最近 5 章 + 卷首 + 关键事件章）—— 仅缓解，未根治。
- **v17.5 C1 升级**：build_manifest 真正调用 `rag_retriever.py`（已存在但未用）—— 把基于 chapter_plan 的 TF-IDF 检索结果作为 **P0 must_read** 注入。例：ch100 时自动找到 ch37 埋的"伊森笔迹"伏笔，无须 writer 自己回忆。
- **预防**：所有长篇项目（>20 章）的 manifest 必须含 RAG hits，且优先级 ≥ P0。

### L11.2 ⛔ 角色 facts 必须显式锁定（locked_facts 字段）
- **业界推荐**：Steve Kinney「token-level memory storage」—— facts 提取成扁平、可检索、可审计的形式存储。
- **我们之前**：人物卡有 voice_pack / appearance / background 等散文字段，但没有可机器校验的 facts dict。
- **v17.5 C2 升级**：
  - 人物卡 schema 添加 `locked_facts: dict`（age / physical / family / sequence / ...）
  - `validate_chapter.check_locked_facts()` 升级为支持 dict + list 双格式
  - 新增年龄数字冲突检测（locked_fact age=21 但正文写 19 岁 → 报 LOCKED_FACT_CONFLICT）
- **预防**：任何"读者必须记住的角色属性"必须进 locked_facts，不能只放在 appearance 散文里。

### L11.3 ⚠️ 状态同步必须用 version 字段
- **业界数据**：Maxim Failure Pattern #1 — State Synchronization Failures（多 agent 并发改共享状态 → 竞态 / 重复工作）。
- **我们之前**：4 个 optional agents（summarizer/foreshadower/reflector/outline-planner）并行 spawn，可能同时改 章纲摘要[ch]。
- **v17.5 C4 升级**：每次写 章纲摘要 时打上 `_version` (单调递增) + `_last_modified_by` + `_last_modified_at`。
- **未来扩展**：写入前比对 _version，不一致 → 重读+合并 + 重写（乐观并发控制 / OCC）。
- **预防**：任何会被 2+ agent 同时改写的 JSON 节点都要有 version 字段。

### L11.4 ⚠️ JudgeReport 必须有 schema_version 字段
- **业界数据**：Maxim Failure Pattern #2 — Communication Protocol Breakdowns（schema 升级 → 旧报告无法解析）。
- **我们之前**：JudgeReport schema 在 prompt 里写死，没版本号 → 未来扩字段（如加 `cost_estimation`）会让 meta-judge 在旧报告上崩溃。
- **v17.5 C5 升级**：所有 4 个 judge agent prompt 输出格式加 `"schema_version": "1.0"` 字段；judge_consensus.py 检测多份报告版本不一致时告警。
- **预防**：任何 LLM 输出的"结构化报告"都必须有 schema_version；schema 升级时 bumb 版本号 + meta-judge 加兼容矩阵。

### L11.5 💡 章节级回滚是必备应急
- **业界推荐**：LangGraph time-travel debugging / checkpoint at every state transition。
- **我们之前**：plan_tracker abort 只在 plan 层级回滚，章节级回滚靠用户手动 git reset。
- **v17.5 C6 升级**：新增 `core/scripts/rollback_to_chapter.py`：
  - 自动查找 `feat(ch-N):` commit
  - hard reset + 清理 ch>N 的 txt / .manifest / .style_directive / .wal
  - abort 活跃 plan
  - 备份当前 HEAD 到 `backup-rollback-N-{sha}` ref 防误操作
- **预防**：任何长流水线系统都应有 N-1 级回滚机制；不依赖用户手动 git。

---

## 设计哲学（v17.5 总结）

| 业界来源 | 我们的实现 |
|---|---|
| kore.ai memory drift 报告 | RAG 集成（C1） |
| Steve Kinney token-level memory | locked_facts dict（C2） |
| Maxim State Sync failure pattern | _version 字段（C4） |
| Maxim Comm Protocol failure pattern | schema_version 字段（C5） |
| LangGraph checkpointing | rollback_to_chapter.py（C6） |
| Microsoft context compaction | 待做（C3 未实施） |

**剩余坑位**：
- C3 spawn agent 前 token budget 控制（用户暂未要求）
- 角色一致性 LLM spot check（每 10 章主动验角色行为是否漂移）
- 完整的 OCC（乐观并发控制）写入前 version 检查（当前仅写时打标）

**最后更新**：2026-05-14（v17.5 业界对照录 / 新增 §11 5 条 L11.x 教训 + rag_retriever 集成 + locked_facts + _version + schema_version + rollback_to_chapter.py · 累计经验库 L1-L11 共 54+ 条目）

---

## 12. SCORE 框架对照录（v17.6 引入 · 第四轮联网调研沉淀）

> 用户 2026-05-14 第四轮"联网搜索分析"。
> 调研：SCORE（arxiv 2503.23512）/ Story Bible 架构（Sudowrite/NovelCrafter）/
> EQ-Bench 14 维评分 / Prompt Injection OWASP 2026 / Cost Control gateway。
> 本节记录"业界已成熟但我们漏掉"的设计，6 个 D 系列升级。

### L12.1 ⚠️ SCORE 框架三剑客：State Tracking + Context-Aware Summary + Hybrid Retrieval
- **业界证据**：arxiv 2503.23512 SCORE 实测三组件融合，**NCI-2.0 提升 23.6% coherence + 89.7% emotional consistency + 41.8% fewer hallucinations**
- **我们之前**：
  - 仅 TF-IDF（无语义检索）
  - 仅章节摘要分级（无符号事实图）
  - 角色/物件持有状态分散在 13 JSON，无统一图
- **v17.6 升级**：
  - D1 `state_tracker.py`：扫描 ch1..N 的 CHANGES_FACTUAL，构建符号事实图（character 持有 items / current_location / last_appearance_ch）
  - 加 `--validate ch` 模式：检测 ch 内容与事实图冲突（如"克莱找钥匙"但克莱已持有）
  - D2 `rag_retriever.py` 加 `--mode hybrid` 双路融合（TF-IDF + embedding，无 API 时优雅降级）
- **预防**：长篇项目（>50 章）必须开 hybrid retrieval；超过 20 章必须开 state_tracker

### L12.2 ⚠️ Character Index 是 Story Bible 标配
- **业界证据**：Sudowrite/NovelCrafter 都内置"搜某角色上次出现在哪一章说了什么"功能
- **我们之前**：靠 RAG 模糊匹配，对"具体某角色"查询不准
- **v17.6 D5 升级**：`character_index.py` 维护 character_index.json：
  - 每个角色：first/last_appearance_ch / total_chapters / dialogue_samples / first_line_context
  - 实战发现：本会话 ch1-4 扫描立即抓出 2 个"申报 vs 实际"偏差（莫顿/伊森）
- **预防**：长篇项目每 5 章必须重建 character_index

### L12.3 ⚠️ Story Bible 自动提取（NER + 申报对比）
- **业界**：每章后扫描正文识别角色/地点/物件，与 CHANGES 申报对比，找漏报/误报
- **我们之前**：完全靠 writer 在 CHANGES 里手工报告
- **v17.6 D6 升级**：`story_bible_extractor.py`：
  - 扫描人物卡/地图/道具的已登记实体，统计正文提及频次
  - 与 CHANGES.active_characters / character_movements / location_changes 比对
  - 启发式发现潜在新地点（regex 匹配 X+塔/教堂/港/河 等）
  - 仅警告型，不自动修改
- **预防**：把 story_bible_extractor 集成进 save-state step 9，每章自动跑

### L12.4 ⚠️ 14 维评分（EQ-Bench Longform Creative Writing 标准）
- **业界**：Nuanced Characters / Emotionally Engaging / Compelling Plot / Coherent / Show-don't-tell / Voice / Pacing / World Consistency / Subtext / Dialogue / Sensory / Emotional Arc / Tension / Originality
- **我们之前**：仅 A/B/C/D 总评 → meta-judge 无法做细粒度漂移检测
- **v17.6 D7 升级**：novel-validator-repair JudgeReport schema bump 到 1.1，加 `score_14dim` 字段（每项 1-10）
- **预防**：高质量小说项目必须开 14 维评分 + 每 5 章看维度趋势（如"compelling_plot 后期持续下降 = 故事失血"）

### L12.5 💡 我们暂未实施的 D3/D4 业界关键防御
- **D3 Prompt Injection 防御（OWASP #1 2026）**：73% 生产 AI 出现注入。本项目单用户本地风险有限，但蒸馏库的网络小说原文可能含间接注入（base64 / zero-width unicode）。未来需要 `input_sanitizer.py`。
- **D4 Cost 监控 + 熔断**：本会话真出过 API 配额耗尽（5 agent 同时报 hit limit）。需要 gateway 3 层（token bucket + breaker + fallback）。
- **决策**：用户 2026-05-14 选择跳过 D3/D4，先做内容质量类（D1+D2+D5+D6+D7）。Cost 风险靠用户人工监控+断点恢复（rollback_to_chapter.py）。

---

## 设计哲学（v17.6 总结）

| SCORE 组件 / 业界标准 | 我们的实现 | 状态 |
|---|---|---|
| Dynamic State Tracking | state_tracker.py 符号事实图 | ✓ D1 |
| Context-Aware Summarization | 章纲摘要分级（最近+卷首+关键） | ✓ P1.4 |
| Hybrid Retrieval | rag_retriever --mode hybrid | ✓ D2（embedding 待 API） |
| Character Last-Appeared | character_index.json | ✓ D5 |
| Auto NER + Story Bible | story_bible_extractor.py | ✓ D6 |
| 14-dim Quality Score | JudgeReport.score_14dim | ✓ D7 |
| Prompt Injection Defense | — | ❌ 待 D3 |
| Cost / Budget Control | — | ❌ 待 D4 |

**最后更新**：2026-05-14（v17.6 SCORE 框架对照录 / 新增 §12 5 条 L12.x 教训 + state_tracker + character_index + story_bible_extractor + 14 维评分 + rag embedding 骨架 · 累计经验库 L1-L12 共 59+ 条目）

---

## 13. 调研先行原则（v17.7 引入 · 用户主动需求）

> 用户 2026-05-14 反馈："当前的灵感以及剧情都是模型生成，我希望在生成前强制加入联网搜索功能。一来可以结合当前实时热点或者爆火内容，二来可以把互联网当成知识库储备"。
> 本节记录"模型记忆 vs 实时联网"决策原则 + 强制调研集成模式。

### L13.1 ⛔ 模型记忆 vs 实时数据的边界
- **现象**：之前 /write 第 2 步（生成 3 灵感）和 outline-planner（生成走向卡）都是纯模型推理，没用联网
- **风险**：
  - 模型 cutoff（2026 年初）之后的真实热点 / 爆款元素 / 同题材成功案例完全错过
  - 小说世界观涉及的真实事物（1880 年代灯塔、维多利亚商会）—— 模型记忆稀疏易出错
  - 灵感卡千篇一律——靠的都是同样的训练分布
- **修复**：v17.7 新增 `novel-researcher` agent，强制在灵感/走向卡/角色蒸馏前联网调研：
  - tools: WebSearch + WebFetch + Read + Write
  - 4 种 TASK_TYPE（inspiration / outline / character / fact_check）
  - 4 类 SCOPE（hot_topic / knowledge / competition / setting_reference）
  - 输出 `.research_cache/<task_type>_<topic>_<时间>.md` 含 Source URL + Synthesis 段
- **预防**：所有生成型 agent（writer/outline-planner/...）启动前先问"这章/这卡需要的真实知识/热点信息从哪来"——能联网就先联网，不要凭模型记忆瞎编

### L13.2 ⚠️ "强制调研" 集成的两条路径
- **路径 A 主代理负责**：/write 流程文档明确"第 2 步前 spawn novel-researcher"。优势：简单。代价：靠主代理记得调用
- **路径 B agent prompt 检测**：novel-outline-planner 检测 prompt 里没 RESEARCH_REF 字段时降级为纯模型 + 输出 `research_ref_missing: true`。优势：可观察、可审计
- **v17.7 实施**：A+B 双管齐下
  - CLAUDE.md /write 流程加"调研先行"硬性规则
  - novel-outline-planner.md 加 RESEARCH_REF 检测 + 缺失时降级 + 报告
- **预防**：未来如果加新生成型 agent（如 /distill-character），同样需要"调研先行 + RESEARCH_REF 检测"双重保护

### L13.3 💡 .research_cache 不入 git 是有意设计
- **为什么不入 git**：
  - 调研结果是临时知识源 + URL 引用，本身不是项目内容
  - URL 可能失效（网络小说被删 / 网站下线），git 历史保留无意义
  - 真正有价值的"调研结论"已经被 writer / outline-planner 融合进章节正文 + chapter_plan，可追溯
- **预防**：所有"外部数据缓存"目录（.research_cache / .embedding_cache / ...）都应在 .gitignore，不污染 git 历史

### L13.4 💡 缓存复用：24 小时内同主题不重复搜
- **场景**：用户在同一天调研"克苏鲁灯塔题材热点"，1 小时后又要调研同主题
- **业界做法**：researcher agent 内部判定—检查 `.research_cache` 是否有 24h 内 same task_type + same topic_slug 的 cache → Read 复用，不重新跑 WebSearch
- **预防**：cache filename 应含 `task_type` 和 `topic_slug` 两个判定字段（不仅时间戳）。同时配置 `/research-cleanup` 命令定期清 30 天 +

### L13.5 💡 调研内容不上传敏感
- **风险**：novel-researcher 可能误把项目 locked_facts / 章节正文当查询内容发到外部搜索引擎
- **防御**：
  - prompt 中明确 "只搜公开关键词，不把项目内的 locked_facts / 章节正文外发"
  - 未来可加输入审查：QUERIES 字段中如果含人物卡 locked_facts 字段值 → 拒绝
- **预防**：所有"外发数据" agent（researcher / fact_check / ...）必须在 prompt 中显式声明"不外发的数据列表"

---

**最后更新**：2026-05-14（v17.7 调研先行 / 新增 §13 5 条 L13.x 教训 + novel-researcher agent + /write 流程强制 + outline-planner RESEARCH_REF 接入 + .research_cache 规范 · 累计经验库 L1-L13 共 64+ 条目）

---

## 14. DCAS 双章自然截断（v17.8 引入 · 用户主动需求）

> 用户 2026-05-14 反馈："章节衔接没有那么紧密或者自然，能否就是一次性生成两章的内容，然后截断成一章内容，这样章末尾的截断就很正常，然后把上一章的结尾用于下一章的开头，这样第二章的开头也很自然"
> 本节记录"双章生成 + 自动截断"的设计原理 + 与现有系统的协同。

### L14.1 ⛔ 单章独立生成 = 章节边界刻意化
- **现象**：v17.7 之前每章独立生成 3000 字。writer 为了"章末有钩子"会强行设计信息炸弹；下章 writer 为了"开头有冲击"会重启叙事
- **后果**：章节边界显得刻意，读者能感觉到"AI 在交接班"
- **根因**：单章模式让 writer 同时承担"叙事中段+章末设计"+"下章开头设计"，三件事在 3000 字内拼凑必然生硬
- **修复**：v17.8 DCAS 模式 —— writer 一气呵成生成 6000-7000 字连续叙事（不知章节边界），chapter-splitter agent 自动选最佳截断点切割
- **预防**：长篇小说（>20 章）从 ch4 起强制 DCAS；ch1-3 保持单章（因为还在风格摸索期 + 测试方便）

### L14.2 ⚠️ 截断点 7 维度评分算法
- **业界标准**：严肃文学的章节边界 = 页面物理限制（白鲸记/战争与和平），非叙事完整性
- **v17.8 chapter-splitter 评分**：
  - **字数接近度**（±300/±500）+1~+5
  - **钩子张力**（末句拟声/破折号/独立短句）+2~+3
  - **场景边界**（后段分隔符/换章符）+5
  - **接续动作**（后段以"回头/抬头/睁开"开头）+3
  - **角色接续**（后段以角色名开头）+2
  - **avoid 项**（对话引号未闭 -50 / 破折号中间 -30 / 心理戏中间 -10）
  - **avoid 项**（后第一句直接揭谜底 -5，破坏悬念）
- **预防**：splitter 是"保守的剪刀手"——top-1 与 top-2 差 <2 时报 uncertainty_flag；所有候选都有 avoid 时报错让主代理决策

### L14.3 ⚠️ DCAS 与 style_directive 的协同
- **冲突**：style_directive 强制 opening_type，但 DCAS 切出的 ch+1 开头是上章自然延续 → 不能再"独立选 opening_type"
- **v17.8 解决**：
  - style_injector 检测 `章节/第{ch+1}章/.pre_opening.txt` 存在 → 设置 `opening_type: "inherit_from_dcas"` + `opening_type_enforcement: skipped_due_to_dcas_inheritance`
  - validator 跳过 ch+1 的 opening_type 独立提取
  - style_drift_scan 跳过 ch+1 的 opening_rule 违规检测
  - applied_style.opening_line = pre_opening 第一句（事实记录，不评判）
- **预防**：未来任何"上下游产物互锁"机制都要检查上游 schema 字段是否需要降级

### L14.4 ⚠️ DCAS 触发条件 + 章节状态机
- **触发条件**：
  - chapter ≥ dcas_threshold（默认 4，可在用户偏好配 dcas_threshold）
  - 本章没有 pre_opening 继承（即本章是"原章"，不是被前章切出来的）
- **章节生命周期**（v17.8）：
  ```
  原章状态（dcas_enabled=true）：
    writer 生成 6500 字 → splitter 切 ~3000 字 → 留 ~3000 字给下章
  
  继承章状态（has_pre_opening=true）：
    writer 读 pre_opening (~3000 字) + 续写 ~2000-3000 字 → 不切（普通收尾）
  
  下个原章再启 DCAS 循环
  ```
- **预防**：状态机的两端必须互斥——"被切的章"和"切别人的章"不能是同一章

### L14.5 💡 .pre_opening.txt 命名 + 不入 git
- **命名规则**：`章节/第{ch:03d}章/.pre_opening.txt`（点开头隐藏）
- **不入 git 原因**：
  - pre_opening 是"过渡产物"——下章 writer 启动后会合并进正式 ch.txt，pre_opening 文件本身使命结束
  - 入 git 会让 git 历史里同一段文字出现两次（一次 .pre_opening / 一次正式 ch）
  - 类似 .wal/ .research_cache/ 是"工作文件不入版本"
- **生命周期**：写 ch+1 时主代理可手动删除 pre_opening；或保留供 rollback 用
- **预防**：所有"上下游过渡产物"都应在 .gitignore，不污染历史

---

**最后更新**：2026-05-14（v17.8 DCAS 双章自然截断 / 新增 §14 5 条 L14.x 教训 + novel-chapter-splitter agent + writer 双模式 + style_injector pre_opening 适配 · 累计经验库 L1-L14 共 69+ 条目）

---

## 15. 段/场景级别叙事质感扫描（v17.9 引入 · 用户主动需求）

> 用户 2026-05-14 询问"还有没有类似 DCAS 这种能改进剧情效果的技巧"。
> 调研业界 2026：Swain MRU / GMC / Chekhov / Micro-tension / POV distance / Repetition。
> 现有系统主要管"章级别"，本节填补"段/场景级别"质量管控空白。

### L15.1 ⚠️ GMC 是业界 #1 共识：每场景必须含冲突
- **业界引言**：*"Conflict is not optional in any scene. Every scene needs someone who wants something and something standing in their way—without which you have a summary, not a story."*
- **GMC 四要素**：Goal（角色想要什么）+ Motivation（为什么）+ Conflict（什么阻拦）+ Disaster（失败或部分成功）
- **现象**：AI 容易写"克莱在塔顶值班"这种纯氛围场景——读者觉得"啥也没发生"
- **修复**：v17.9 `narrative_scanner.py --check gmc` 按场景扫描 4 要素，缺 ≥2 个标记为 "summary 而非 story"
- **预防**：氛围章/内省章可允许某些场景 GMC 弱化，但应在 chapter_plan.scene_type 显式标记 "internal" / "atmospheric"，否则 scanner 默认按 GMC 检查

### L15.2 ⚠️ MRU 段落动机-反应顺序是 AI 最常见破绽
- **业界标准**：Swain Motivation-Reaction Unit。先写**动机**（外部刺激）→ 再写**反应**（顺序：感觉→本能动作→理性→对话）
- **AI 错误典型**：
  - ❌ "克莱说：'天哪。' 然后他的心跳加速，手开始抖。"（对话先于身体反应）
  - ✅ "心跳骤然加速。他的手开始抖。然后他听见自己说：'天哪。'"
- **检测**：narrative_scanner --check mru —— 同段中对话出现位置 < 身体反应位置 30 字内 → 报警
- **预防**：writer.md 已写明此原则，但 writer 仍可能犯。scanner 在 validator step 后扫，写后修正

### L15.3 ⚠️ Orphan Objects = Chekhov 风险信号
- **业界**：Sudowrite/AutoCrit 标配——追踪每个具体名词的出现次数
- **判定**：
  - 出现 ≥2 次 = chekhov 候选（已伏笔化）
  - 出现 1 次 = orphan，要么深化为伏笔要么砍掉
- **实测**：ch3 扫出 **92 个 orphan**（regex 较宽）——大多是一次性环境道具，可砍可深化
- **预防**：每 5 章跑一次 --history --check orphan 模式，看 orphan/章 趋势。趋势上升 = writer 在堆砌冗余细节

### L15.4 ⚠️ Micro-tension：每段末尾留小钩子
- **业界**：每段最后一句应留**未答的小问题** / **未完成动作** / **悬念词**。读者不停翻页的微观机制
- **检测**：段末包含 ?/……/—/不知/或许/也许/未答/似乎/仿佛 = +3；含未完成动作（伸手/抬头/睁开/刚要）= +2；独立短句（< 10 字）= +1
- **报警阈值**：低张力段（0 分） > 1/3 总段数
- **实测**：ch3 内省章 66/181 段（37%）张力 0 —— 接近阈值，内省章可豁免
- **预防**：场景规则.json 加 scene_type 的"允许低张力"标记

### L15.5 💡 POV 距离梯度避单调
- **业界**：第三人称有"近/中/远"。连续 3 段同距离 → 单调
- **检测**：
  - 近：含"他想/她记得/心跳/喉咙"等心理 + 身体内感
  - 中：含动作动词（走/拿/握/看/说）
  - 远：含环境词（雾/雨/海/塔/墙）
- **修复**：narrative_scanner --check pov 报"连续 ≥3 段同距离"runs

### L15.6 💡 Repetition：同段重复名词 ≤3 次
- **业界**：AutoCrit/ProWritingAid 标配
- **检测**：narrative_scanner --check repetition 扫每段，同一具体名词 > 3 次报警

---

## 设计哲学（v17.9 总结）

| 章级别管控（v17.6 之前） | 段/场景级别管控（v17.9 新增） |
|---|---|
| 14 维评分（章总评） | GMC 场景级（G1） |
| 章末钩子检测 | 段末微张力（G4） |
| Chekhov 章级 plant/payoff | Orphan 单次物件（G3） |
| 禁用词扫描 | 同段重复词（G5） |
| 风格漂移扫描 | POV 距离梯度（G6） |
| validator 总评 | MRU 段落顺序（G2） |

**v17.9 集成位置**：
- write-chapter plan step 3：narrative_scanner --all 在 validator 前后调用
- validator agent 必读 scanner 报告，把警告纳入 reasoning_trace

**未来扩展（待用户决策）**：
- AI agent 模式：让 scanner 把警告交给一个 micro-editor agent 自动 fix
- 实时增量扫描：writer 写每段时立即扫，而非写完一章统一扫
- 学习用户偏好：连续 3 章用户接受了"低 GMC 内省章"，scanner 调整阈值

---

**最后更新**：2026-05-14（v17.9 段/场景级质感扫描 / 新增 §15 6 条 L15.x 教训 + narrative_scanner.py 6 检测器 + plan step 3 集成 · 累计经验库 L1-L15 共 75+ 条目）

---

## 16. 情节结构层（v17.10 引入 · 用户继续问"还有什么改进剧情技巧"）

> 用户 2026-05-14 第二次问"还有没有改进剧情效果的技巧"。
> 业界 2026 调研：Save the Cat 15-beat / Try-Fail Cycle / Midpoint Reversal / Information Asymmetry / K.M.Weiland 角色弧光 / 多线沉睡子情节。
> v17.9 已做"段/场景级"，本节填补"情节结构层"（章 ↔ 全书）。

### L16.1 ⛔ Save the Cat 15-beat 章级映射是长篇必备
- **业界数据**：200 章长篇映射到 15 个标准 beat。Penwise.ai 2026 章节模板把 First Act 14 scenes / Second Act 28 scenes / Third Act 14 scenes 精确分配
- **关键 beat**：Opening Image / Theme Stated / Setup / Catalyst / Debate / Break into Two / B Story / Fun and Games / **Midpoint** / Bad Guys Close In / **All Is Lost** / Dark Night / Break into Three / Finale / Final Image
- **我们漏掉**：仅卷级大势，章级 beat 无。writer 不知道本章的"结构功能"（如"我现在是 Midpoint 应该有伪胜利"）
- **修复**：v17.10 `plot_structure_scanner.py --check beat` + `beat_map.json` 章 ↔ beat 映射
- **预防**：任何 ≥20 章项目必须建 beat_map.json，writer 启动时知道本章 beat 期望

### L16.2 ⛔ Try-Fail Cycle：主角不能"想做就做成"
- **业界共识**：*"Partial wins create bigger problems that the old skills cannot solve"*
- **AI 通病**：让主角一次就成功 = 读者无成长感 → "开挂"印象
- **检测**：scanner 扫历史 6 章，连续 3 章无 fail 关键词 → 报警
- **修复**：v17.10 `--check tryfail`
- **预防**：每 5 章用户应自检"主角最近遇到失败了吗"。即使爽文也要 *partial win* 节拍

### L16.3 ⛔ Midpoint Reversal 是 50% 位置的硬要求
- **业界标准**：故事中点必须有重大反转，**前后两半的"问题性质"必须翻转**。
- **例**：灯塔守人 ch100 应该有什么"颠覆前 100 章理解"的揭示？大纲卷三末"听潮者反水"对应这个
- **修复**：v17.10 `--check midpoint` — 中点 ±5 章 zone 内必须有 reversal 关键词 ≥2 次
- **预防**：大纲规划时必须显式标注 midpoint chapter，scanner 在 50% ±5 章区间监控

### L16.4 ⛔ Information Asymmetry 是戏剧张力的根
- **业界**：*"谁知道什么 + 谁不知道什么 = 戏剧张力的根"*
- **两种张力**：
  - 读者知道 + 角色不知道 → **dramatic irony**（紧张感）
  - 角色知道 + 读者不知道 → **悬念**（神秘感）
- **我们之前**：locked_facts 是**静态属性**，缺**动态 who knows what when** 图
- **修复**：v17.10 `knowledge_graph.json` + scanner `--check knowledge`
  - 每个 fact：content / known_by [{role, ch}] / hidden_from / reveal_to_protagonist_at_ch / reveal_to_reader_at_ch
  - asymmetry_index = 当前隐藏的 fact 数 → 应 ≥1 维持张力
- **预防**：所有 secret 上线时同时建 knowledge_graph 条目，不仅放伏笔表

### L16.5 ⚠️ 角色弧光四段（Lie/Want/Need/Truth）
- **业界 K.M.Weiland 经典**：每主角四段弧
  - **Lie**：开篇坚信的错误观念（如克莱"我必须完成契约"）
  - **Want**：外在目标（活到第 7000 夜）
  - **Need**：内在需要（理解父亲为何 + 接受被牺牲）
  - **Truth**：终章学到的真理（『被选中』 ≠ 『被诅咒』）
- **检测**：scanner 看主角当前章 stage_by_chapter 是否声明
- **修复**：v17.10 `character_arc_state.json` 每章标注主角弧段位置

### L16.6 ⚠️ 多线沉睡子情节检测
- **业界**：长篇必须 ≥2 条线并行。某条线"沉睡 > 5 章"应触发激活提醒
- **修复**：v17.10 `subplot_threads.json` + scanner `--check subplot`
  - 每个 thread: id / name / last_advanced_ch / target_resolve_ch / trigger_chapters
  - last_advanced_ch + 5 < current_ch → warning
- **预防**：大纲阶段就建 subplot_threads.json，写每章后由 save_state 自动更新 last_advanced_ch

---

## 设计哲学：v17.9 + v17.10 形成"三尺度质量管控"

| 尺度 | 工具 | 检测器 |
|---|---|---|
| **段/场景级**（v17.9） | narrative_scanner.py | GMC / MRU / Orphan / Micro-tension / Repetition / POV 距离 |
| **情节结构层**（v17.10） | plot_structure_scanner.py | Beat / Try-Fail / Midpoint / Knowledge graph / Arc / Subplot |
| **风格指纹层**（v17.3） | style_drift_scan.py | Opening rule / Anchor frequency / Ending rule |

writer 写完一章后，**三个 scanner 串行扫描** → judge agent 读三份报告 → 综合 14 维评分。

**最后更新**：2026-05-14（v17.10 情节结构层 / 新增 §16 6 条 L16.x 教训 + plot_structure_scanner.py 6 检测器 + 4 个数据 JSON + plan step 3 集成 · 累计经验库 L1-L16 共 81+ 条目）

---

## 17. 端到端验证产出（v17.11 · ch4 首次真实 DCAS 流程跑通）

> 用户 2026-05-14 选择"先验证再说"。ch4 走完完整流程：build_manifest → writer(DCAS 8399 字) → chapter_splitter(切 3178+5221) → narrative_scanner + plot_structure_scanner → validator(A- 级)。
> 本节记录验证暴露的真问题 + 修复。**验证比继续加功能更重要**。

### L17.1 ⛔ 纯算法逻辑不应做成 LLM agent（splitter 教训）
- **现象**：v17.8 把 chapter-splitter 设计成 LLM agent。ch4 验证时发现 agent 未被 session 加载（新建 agent 定义不自动生效）
- **更深的问题**：splitter 的 7 维度评分**全是确定性规则**——字数/拟声/破折号/场景分隔符——根本不需要 LLM 语言理解
- **修复**：v17.11 把 chapter-splitter 从 agent 改为脚本 `core/scripts/chapter_splitter.py`。不依赖加载、确定性、可单元测试、毫秒级
- **预防**：判断"该用 agent 还是脚本"——**如果逻辑能写成 if/正则/打分，就用脚本**。agent 只用于需要语言理解/创作判断的环节（writer/validator/voice-keeper）。novel-researcher / novel-meta-judge 同样建议复审是否该降级为脚本

### L17.2 ⛔ 检测器必须按 scene_type 豁免分流（最大验证产出）
- **现象**：ch4 是"单人独处氛围章"，validator 实测 12 检测器——
  - **4 个有效**：beat / arc / knowledge / tryfail（准确暴露 beat_map 未同步等真问题）
  - **2 个安静正确**：repetition / subplot（无误报无漏报）
  - **6 个刷噪音**：gmc（14/16 误报，把"——"分隔小节当独立 scene）/ mru（把恐怖文体"先反应后解释"倒装当错误）/ orphan（把中文分词碎片当物件）/ microten（把慢推恐怖节奏停顿当低张力）/ validate_chapter.UNKNOWN_CHARACTER（分词切出假人名）/ validate_chapter.LONG_MONOLOGUE（把内心独白当台词）
- **根因**：检测器规则库按"常规多人剧情章"设计，对"单人/低对话氛围章"全面失灵
- **修复**：v17.11 narrative_scanner 加 `_detect_chapter_mode()` —— chapter_plan.characters ≤1 人 OR 对话占比 <5% → `solo_atmospheric`，对 gmc/mru/microten/orphan 降级为 info（不触发 exit 1）
- **待办（v17.12）**：validate_chapter 的 UNKNOWN_CHARACTER / LONG_MONOLOGUE 同样需要 scene_type 豁免（涉及分词逻辑，改动较大，标记 known issue）
- **预防**：任何"规则型检测器"上线前必须问"它在 X 章型下会不会失灵"。检测器不是越多越好——**带噪音的检测器会稀释真信号**

### L17.3 ⚠️ DCAS 首次实战：writer 易写超 target
- **现象**：DCAS target 6500 字，writer 实际写 8399 字（1.3×）。splitter 切出 ch4 3178 字（达标）+ pre_opening 5221 字（超长）
- **修复**：v17.11 chapter_splitter 加 `pre_opening_oversized` 标记 + note。ch5 启动时可直接用 pre_opening 作为完整章节，或再次 DCAS 切割
- **预防**：writer DCAS 模式 prompt 应强调"6500 字是上限不是目标"。或 splitter 支持"超长草稿切 3 段"（ch + ch+1 + ch+2 pre_opening）

### L17.4 ⚠️ plan step 编号必须整数（2.5 教训）
- **现象**：write-chapter.plan v4 用 step "2.5" 插入 splitter。plan_tracker `--n` 是 int 类型，传 "2.5" 解析失败
- **修复**：v17.11 双修——plan_tracker `--n` 改 str 容错 + `_find_step` 字符串化比较；write-chapter.plan v5 重编号为整数 1-6（splitter 并入 step 2 的 scripts）
- **预防**：plan step 永远用整数序列。需要"插入"时整体重编号，不用小数

### L17.5 💡 验证的元价值：证明"该停止加功能了"
- **数据**：8 轮升级累计 39+ 项、12 检测器、10 agent、24 脚本。ch4 验证显示——6/12 检测器在常见章型下是噪音
- **教训**：连续加功能时，每加 3-5 个就该跑一次真实验证。否则会在"看起来有用但实际制造噪音"的方向上越走越远
- **预防**：把"端到端验证"作为固定节拍——每个大版本（v17.x）至少跑一次真实章节，用 validator 的元评价校准方向

---

**最后更新**：2026-05-14（v17.11 端到端验证产出 / 新增 §17 5 条 L17.x 教训 + chapter_splitter 改脚本 + narrative_scanner 单人章豁免 + plan_tracker --n 容错 + write-chapter plan v5 整数重编号 · 累计经验库 L1-L17 共 86+ 条目）

---

## §18 v22.cluster.3 完整闭环实证（2026-05-25 · 惊悚乐园 250 章蒸馏）

### L18.1 ⛔ 复刻必须走 gen-model（已写入 hooks 规则 11）

- **现象**：phase-2 第一轮用 Claude sub-agent (Opus 4.7) 复刻 opening/battle/psychology，agent 自评全部 PASS（字数 / 禁词 / 段长达标）
- **真相**：切回 gen-model（deepseek_v4_pro，正式写作要用的栈）后，特征显著漂移（叙述者跳出频率 / 系统【】嵌入密度 / ACG 引用率 都低 30-50%）
- **根因**：Claude 对自然语言风格规则的理解 ≠ deepseek/qwen/gemini。用 Claude 测的 v0→v1 升级 = 针对错的模型迭代 = 无效迭代
- **修复**：
  - 写 `core/scripts/distill_replicate.py`（gen-model 唯一合法入口）
  - hooks 加规则 11（描述含「复刻测试 / v{N} 复刻」→ exit 2 拦截 Agent spawn）
  - CLAUDE.md 加「蒸馏复刻强制 gen-model」全局规则
- **预防**：蒸馏闭环必须用最终写作要用的同一个 LLM 栈

### L18.2 ⛔ SFS 迭代非单调收敛 = 选峰值版本，不要硬推

- **数据**：v0 (65.72) → v1 (68.38, +2.66) → v2 (64.68, -3.70)
- **现象**：v2 的修正过激，触发"按下葫芦浮起瓢"
  - ✅ 改善：句长 std（psychology 0→21.4）、引号独白（46.7→94.8）
  - ❌ 退步：单句成段率（opening 85.9→59.6）、对话占比、拟声段（100→0）
- **决策规则**：连续 2 轮 SFS 整体下降 → 停止迭代，选峰值版本为 FINAL（不是最新版）
- **预防**：phase-5 每轮都跑 SFS 对比上一轮，整体退步立即触发收敛检测，不要为"再试一次"赌博
- **预防**：v1→v2 修正方向是"加新约束 + negative example"——容易引发回归。改进建议是「保留所有 v1 约束，只**新增**针对低分维度的硬指令，不动其他维度的现有约束」

### L18.3 💡 agent 必须查实际数据，不能盲信用户简报

- **现象**：phase-4 agent 收到指令「极短段占比为零，要加下限」。agent 实际 Read eval JSON 发现：v0 极短段占比 13-22%，**远超原文 ref 的 5%**，score=0 是因为「过多」而非「过少」
- **agent 决策**：按实测数据反向修正约束（封顶 5% 而非下限）
- **教训**：用户口述的"低分维度"和 score=0 的实际原因可能完全相反。LLM agent 必须 Read 原始数据自己判断，不能盲信主代理的简报
- **预防**：phase-4/phase-5 修正反思的 agent prompt 必须强调「先 Read 原始 eval JSON 确认数据方向，再做修正」

### L18.4 ⛔ deepseek_v4_pro 在 distill 闭环的天花板 ≈ SFS 70 分

- **数据**：3 轮迭代 + 6 个低分维度针对性优化，psychology 峰值 71.10 / opening 峰值 67.14 / battle 峰值 66.90
- **原因**：deepseek 对细颗粒规则指令（"段内长短对比 3 处以上" / "至少 3 个拟声段"）的遵循度低
- **预防**：
  - 蒸馏闭环 round 数硬上限 = 3 轮，不要追求 SFS > 80
  - SFS 70 = 可用基线，注入 gen_writer 后由 writer agent 自检循环补偏差
  - 切其他 gen-model（qwen / claude-via-gen / gpt-via-gen）可能解锁更高分（待验证）

### L18.5 💡 蒸馏 phase-6 出货决策：v1 = FINAL 不是 v2

- **决策依据**：v1 SFS 68.38 > v2 SFS 64.68（明显退步）
- **FINAL 文件**：skill_FINAL.md = skill_v1.md + frontmatter（不重写）
- **distillation_log**：完整保留 v0→v1→v2 三轮历史 + 最终选 v1 的决策说明
- **预防**：FINAL 永远是「迭代过程中的峰值版本」，不是「最后一版」。让用户和 reader 能从 log 看清楚为什么不是最新版
