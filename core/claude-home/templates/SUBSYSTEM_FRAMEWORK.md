# 34 子系统 · 框架 / 脚手架 / 示例（2026-06-01 根治契约债）

> 背景：新建项目时，34 个子系统 JSON 此前**无确定性框架**——每本书靠主代理手搓 + agent 自由生成 schema，
> 与 consumer（build_manifest）/ validator（db_schema_validate）期望漂移 → **契约债成簇**（缺 schema_version、
> 进度缺 book_title、写作经验 entries vs success_patterns、地图 list vs dict…）。弱模型尤其扛不住「自由生成 schema」。
> 本批次确立**单一真理源 + 确定性脚手架**根治。

## 三件套（框架 / 脚手架 / 示例）

| 件 | 路径 | 作用 |
|---|---|---|
| **框架（单一真理源）** | `core/claude-home/templates/subsystem_skeletons.json` | 34 个 **schema 正确的空骨架**，全部满足 db_schema_validate（schema_version + 必需键 + 正确 collection 类型）+ 保留通用默认（场景规则 5 类 / ecas 标志 / Save the Cat 框架名）|
| **脚手架（脚本）** | `core/scripts/scaffold_subsystems.py` | `emit`（生成缺失骨架·不覆盖已填）/ `verify`（34 件存在+合法校验）/ `list` |
| **示例（填充参考）** | `core/claude-home/templates/examples/` | 已填的真实项目片段。`scp_anomaly_bureau/` + `urban_supernatural_business/` + **新增 `_subsystem_examples/`（9 个此前无示例的高级件，源自《无脸者守则》）** |

## 设计哲学（照顾弱模型 · 北极星⑤不干涉创作判断）

- **骨架给 schema，agent 给内容**：弱模型只往骨架里填创意，不碰结构 → schema 永远对，杜绝契约债。
- **emit 不覆盖已存在文件**：保护已填内容，可反复跑（幂等）；`--force` 才覆盖。
- **verify 是流程闸**：防「Workflow 名义返回 done 却静默漏文件」——缺/坏 → exit 2 阻断，不许带病进 plan-step 3。
- **作者风格.json 是占位**：scaffold 只保证文件存在过门禁，正式写作前必须由 `/distill-style` 或风格库复制替换。

## 🔴 新书 / 新文件夹创建唯一 sanctioned 入口

**禁止在 plan 之外手搓 `mkdir` 建新书项目目录。** 一律走系统脚本——散落的 ad-hoc mkdir 会漏建
`.wal/` 与 34 子系统 → 后续 plan（resolve_project_root / scaffold / data_flow）错位。两条合法路径：

| 场景 | 入口 | 说明 |
|---|---|---|
| **plan 内（/outline 主路径）** | `outline.plan.json` step 1 `init_project.py … --emit-style-options` → after-pause `--style <名>` → step 6 `scaffold_subsystems.py emit` → step 7 `verify` | orchestrator/GUI 机械驱动·分步建目录+风格+34 子系统 |
| **plan 外（CLI 测试 / 手动 / 一键）** | `python core/scripts/init_project.py "<项目路径>" --scaffold [--style <名>]` | 🆕 一条命令建完整骨架（目录+git+.wal+34 子系统[+风格档]）·幂等不覆盖已填 |

> `--scaffold` 复用 `scaffold_subsystems.emit`（同一真理源 `subsystem_skeletons.json`），所以
> plan 内分步建 与 plan 外一键建 **产出逐字节一致**，不会分叉。回归锁：
> `tests/test_init_project.py::test_scaffold_builds_full_skeleton` / `…_idempotent_keeps_filled_content`。

## 新书 /outline step 3 标准流程

```bash
python core/scripts/scaffold_subsystems.py emit  "<书名>"          # ① 34 骨架
# ② 按 outline.md 1-16 填创意内容 + 复制/蒸馏 作者风格.json
python core/scripts/scaffold_subsystems.py verify "<书名>"          # ③ 34 件存在+合法
python core/scripts/db_schema_validate.py "workspace/novels/<书名>" # ④ schema 0 error
```

## 契约债根治记录（验证器自身的 2 个 bug · 2026-06-01）

`db_schema_validate.py` 作为契约执行者，自身有 2 条过时/错误规则（会反过来误导弱模型把对的文件改错）：

1. **地图 collection_type dict → list**：命令文档 / scaffold / 本模块自己的 migrate_dict_to_list 都按 list；消费方 build_manifest 只读 character_positions(dict)/travel_log，不按类型读 locations。旧规则与自身 migrate 方向矛盾 → 误报 TYPE_MISMATCH。
2. **写作经验 entries → success_patterns/failure_patterns/preferences**：权威结构见 learning_loop.py 文档（entries 是 novel-reflector 的输入格式，非本文件结构；build_manifest.experience_entries 兼容读两种）。旧规则要 entries 产生持续误报。

> 教训：**契约执行者（validator）也会漂移**。单一真理源（skeletons.json）+ 契约耦合测试
> （`tests/test_scaffold_subsystems.py::test_emit_skeletons_pass_db_schema_validate`）锁死「scaffold 输出必过 validator」，防再次分叉。

## 测试

`tests/test_scaffold_subsystems.py`（5 例）：骨架完整性 / emit 生成 34 合法 / **scaffold 输出必过 validator（契约耦合）** / verify 检出缺失损坏 / emit 幂等不覆盖。
