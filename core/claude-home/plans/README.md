# plan 模板字段说明（主代理 checklist / 契约）

> `core/claude-home/plans/*.plan.json` 是 6 个多步命令的**强制规划层**——主代理
> **Claude Code** 把每个 plan 当作可审计的 checklist + 契约：按 `steps[]` 顺序
> spawn 对应 agent、跑确定性脚本、在停顿点弹卡给用户，并经 `plan_tracker.py`
> 盖 attestation 章逐步推进。
>
> 创意/梳理/判断由 **Claude 主代理 spawn Agent** 完成（gen-model 只负责正文创作）；
> 确定性活由 `scripts[]` 列出的 `python core/scripts/*.py` 完成。本文档是 plan JSON
> 里各字段的语义参考——**改字段语义须同步本表**。

## 字段词表

| 字段 | 含义 | 示例 |
|---|---|---|
| `steps[].scripts[]` 行首 `"? "` | 条件脚本：非零退出仅警告不拦（如 `style_injector` 无风格档 exit 1 = 条件不适用非错误） | `"? python core/scripts/style_injector.py {project_root} <cluster_start_ch>"` |
| `steps[].must_spawn_agent` | 本步必须 spawn 的 judge/创意 agent 名（str 或 list）；`end_plan` 校验对应 JudgeReport 真存在 | `"novel-summarizer"` |
| `steps[].agent_input` | spawn judge agent 时的入参（从 agent `.md` 散文上移到 plan）；`*_PATH` 键由主代理代读为 agent 的 context_files（judge 无 Read 工具时） | `{"MODE": "cluster", "CLUSTER_DRAFT_PATH": "章节/..."}` |
| `steps[].judge_report_path` | JudgeReport 产物路径（str 或 `{agent: path}` dict）；`end_plan` 优先验声明路径（`<round>` → 通配） | |
| `steps[].judge_report_path_secondary` | 双载体落盘（如 voice-checker 的 brief 内嵌一份 judge_report 平铺） | |
| `steps[].agent_executor: "script"` | 创意 wrapper（novel-writer / splitter）非 judge——工作由 `scripts[]` 完成，主代理不派 judge（北极星④章节仅格式） | |
| `steps[].data_flow` | `<angle>` 占位符回填声明：主代理跑脚本前按行从 source_json 取值填占位符（lazy 按行解析，同 step 内前一行脚本产物喂后一行）；`join_range` 把 `[lo,hi]` 拼 `"lo-hi"`；只回填路径/数值不固化创作内容（北极星②③） | `{"<chapter_range_dash>": {"source_json": "_数据库/.wal/splitter_...json", "field": "chapter_range", "join_range": true}}` |
| `steps[].control_flow.exit_codes` | 退出码→动作（`ok` / `fail` / `dispatch:<agent>`）；如 audit_hub：0=pass / 1=auto_fixed / 2=needs_agent→主代理派单 / 3=fatal | |
| `steps[].control_flow.round_loop` | ROUND 循环（连续 N 轮 clean 放行 / 超 max_rounds 软预算放行）——只表达控制流不编码创作决策 | |
| `steps[].pause_for_user` | 停顿点（choice/integer + source/options_field/answer_artifact）；主代理弹卡给用户选，**默认必弹**（北极星③）；用户「全自动」时才取第一候选 | |
| `steps[].after_pause_scripts` | 用户选择落定后的确定性后续（如 `cluster_choice_apply` 把选中 brief 机械写回 事件簇.json） | |
| `steps[].touch_outputs` | 标记文件（多轮产物文件名可变时的存在性代理，主代理落 `.placeholder`） | |
| `steps[].expected_outputs` | 本步必须落盘的产物（`plan_tracker step` 时校验存在；空表示无固定单文件名约束） | |
| `steps[].skip_output_allowed` | 默认 false；显式 true 才允许 `plan_tracker step --skip-output`（禁止当万能逃避·见 CLAUDE.md「禁止跳步」） | |

## `<angle>` 占位符（主代理跑脚本前自行解析）

plan 脚本行里的 `<...>` 占位符由**主代理**在执行前解析填实，绝不带占位符跑命令：

- `<cluster_start_ch>` / `<prev_key>` / `<prev_pending_tail>` / `<cluster_num>`：用
  `cluster_lookup.py` 权威算出（上一 cluster 范围 hi+1 等）+ pending_tail 物理探测
  （空值时把 `--previous-pending-tail <prev_pending_tail>` 整对丢弃）。
- `<chapter_range_dash>` / `<rhythm>` / `<style_name>` 等：经 `data_flow` 声明从对应
  source_json 字段取值回填。
- `{project_root}` / `{key}` / `{ch}` / `{next_key}`：由 `plan_tracker` 在 create 时替换。

## 与 WAL / attestation 的关系

- **WAL**（`cluster-save-state` 单命令内的 `completed_steps`）：单命令、细颗粒、断点续跑。
- **plan**（本目录）：跨命令、粗颗粒、可审计的强制规划层。
- 二者共存不冲突——plan_tracker 不动 WAL 字段，WAL 不动 plan_tracker 状态。
- 合法手动改 plan 后用 `plan_tracker.py reattest <plan_id>` 重新盖章。

详见项目根 `CLAUDE.md`「🛡️ Plan 强制规划」+「🔴 禁止跳步」段，以及
`core/claude-home/STRUCTURE.md`。
