# plan 模板字段说明（主代理 checklist / 契约）

> `core/claude-home/plans/*.plan.json` 是 6 个多步命令的**强制规划层**——主代理
> **Claude Code** 把每个 plan 当作可审计的 checklist + 契约：按 `steps[]` 顺序
> spawn 对应 agent、跑确定性脚本、在停顿点弹卡给用户，并经 `plan_tracker.py`
> 盖 attestation 章逐步推进。
>
> 创意/梳理/判断由 **Claude 主代理 spawn Agent** 完成（v29：正文由 novel-writer agent
> Claude 亲笔逐场景创作，gen-model/gemini 只做分段等体量润色）；确定性活由 `scripts[]`
> 列出的 `python core/scripts/*.py` 完成。本文档是 plan JSON 里各字段的语义参考——
> **改字段语义须同步本表**。

## 字段词表

| 字段 | 含义 | 示例 |
|---|---|---|
| `steps[].scripts[]` 行首 `"? "` | 仅用于“项目未启用该条件能力”时可合法不产物的脚本（如 `style_injector` 无风格档）。禁止用于主链状态更新、走向卡选择、账本写入、状态推进或离线观察 sidecar。 | `"? python core/scripts/style_injector.py {project_root} <cluster_start_ch>"` |
| `steps[].must_spawn_agent` | 本步必须 spawn 的 judge/创意 agent 名（str 或 list）；`end_plan` 校验对应 JudgeReport 真存在 | `"novel-summarizer"` |
| `steps[].agent_input` | spawn judge agent 时的入参（从 agent `.md` 散文上移到 plan）；`*_PATH` 键由主代理代读为 agent 的 context_files（judge 无 Read 工具时） | `{"MODE": "cluster", "CLUSTER_DRAFT_PATH": "章节/..."}` |
| `steps[].judge_report_path` | Agent 完成回执路径（str 或 `{agent: path}` dict）；`end_plan` 优先验声明路径（`<round>` → 通配）。有专用回执校验器的 Agent 会同时核对 schema、plan、step 与源产物绑定，普通业务 artifact 不能冒充回执。 | |
| `steps[].judge_report_path_secondary` | 双载体落盘（如 voice-checker 的 brief 内嵌一份 judge_report 平铺） | |
| `steps[].agent_executor: "script"` | 创意 wrapper（novel-writer / splitter）非 judge——工作由 `scripts[]` 完成，主代理不派 judge（北极星④章节仅格式） | |
| `steps[].data_flow` | `<angle>` 占位符回填声明：主代理跑脚本前按行从 source_json 取值填占位符（lazy 按行解析，同 step 内前一行脚本产物喂后一行）；`join_range` 把 `[lo,hi]` 拼 `"lo-hi"`；只回填路径/数值不固化创作内容（北极星②③） | `{"<chapter_range_dash>": {"source_json": "_数据库/.wal/splitter_...json", "field": "chapter_range", "join_range": true}}` |
| `steps[].control_flow.exit_codes` | 退出码→动作（`ok` / `fail` / `dispatch:<agent>` / `pending_*`=主代理按 manifest/brief 补 agent 产物后重跑同 step 脚本）；如 audit_hub：0=pass / 1=auto_fixed / 2=needs_agent→主代理派单 / 3=fatal；如 gen_chapter_titles --apply：2=pending_titles→spawn novel-titler 补件/重命名后重跑 --apply | |
| `steps[].control_flow.round_loop` | ROUND 循环（连续 N 轮 clean 才通过；超 max_rounds 仍未 clean 即 hard_stop）——只表达控制流不编码创作决策 | |
| `steps[].pause_for_user` | 停顿点（choice/integer + source/options_field/answer_artifact）；主代理弹卡给用户选，**默认必弹**（北极星③）；用户「全自动」时才取第一候选 | |
| `steps[].after_pause_scripts` | 用户选择落定后的确定性后续（如 `cluster_choice_apply` 把选中 brief 机械写回 事件簇.json） | |
| `steps[].touch_outputs` | 标记文件（多轮产物文件名可变时的存在性代理，主代理落 `.placeholder`） | |
| `steps[].expected_outputs` | 本步必须落盘的产物（`plan_tracker step` 时校验存在）；主链 required step 必须配置非空 expected_outputs 或 touch_outputs 代理产物。`*_writer-truth-check.json`、`*_apply_cluster.json`、`*_foreshadow_state_receipt.json` 等确定性回执还会按当前 cluster 校验内容与新鲜度，失败 verdict、错 cluster 或早于当前 plan 的文件不能过步 | |

## `<angle>` 占位符（主代理跑脚本前自行解析）

plan 脚本行里的 `<...>` 占位符由**主代理**在执行前解析填实，绝不带占位符跑命令：

- `<cluster_start_ch>` / `<prev_key>` / `<prev_pending_tail>` / `<cluster_num>`：用
  `cluster_lookup.py` 权威算出（上一 cluster 范围 hi+1 等）+ pending_tail 物理探测
  （空值时把 `--previous-pending-tail <prev_pending_tail>` 整对丢弃）。
- `<chapter_range_dash>` / `<rhythm>` / `<style_name>` 等：经 `data_flow` 声明从对应
  source_json 字段取值回填。
- `{project_root}` / `{project}` / `{key}` / `{cluster_id}` / `{next_key}` / `{plan_id}`：由 `plan_tracker` 在 create 时替换。`{ch}` 系列旧章号占位符会被硬拒。

## 与 `.wal/` / attestation 的关系

- **恢复点的唯一真相源 = plan JSON 本身**（`_数据库/.plans/<plan_id>.json` 的 `steps[].status`）：`wal_recovery.py` 读它算 first incomplete step。
- **`.wal/`**：各 step 的产物与回执存放区（summary / state_delta / archive / reflection / titles / receipt 等），供下游 step 消费和 `expected_outputs` 验收——**不承载 step 进度，不是断点日志**。
- cluster-save-state step 1 的产物是 `cluster_<key>_schema_validate.json` 确定性校验报告（`db_schema_validate.py --report-out` 落盘）；模板不用 `touch_outputs` 造 0 字节占位当 step 产物，禁止据任何 `.wal/` 文件判断中断/完成。
- **plan**（本目录）：跨命令、粗颗粒、可审计的强制规划层。
- 合法手动改 plan 后用 `plan_tracker.py reattest <plan_id>` 重新盖章。

详见项目根 `CLAUDE.md`「🛡️ Plan 强制规划」+「🔴 禁止跳步」段，以及
`core/claude-home/STRUCTURE.md`。
