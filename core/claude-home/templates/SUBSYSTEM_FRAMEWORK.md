# 34 子系统 · 框架 / 脚手架 / 示例（单一真理源防契约债）

> 34 个子系统 JSON 若靠主代理手搓或 agent 自由生成 schema，会与 consumer（build_manifest）/
> validator（db_schema_validate）期望漂移，产生**契约债**（缺 schema_version、进度缺 book_title、
> 写作经验 entries vs success_patterns、地图 list vs dict…）；弱模型尤其扛不住「自由生成 schema」。
> 因此用**单一真理源 + 确定性脚手架**根治：骨架统一定义 schema，agent 只填内容不碰结构。

## 三件套（框架 / 脚手架 / 示例）

| 件 | 路径 | 作用 |
|---|---|---|
| **框架（单一真理源）** | `core/claude-home/templates/subsystem_skeletons.json` | 34 个 **schema 正确的空骨架**，全部满足 db_schema_validate（schema_version + 必需键 + 正确 collection 类型）+ 保留通用默认（场景规则 5 类 / ecas 标志 / Save the Cat 框架名）|
| **脚手架（脚本）** | `core/scripts/scaffold_subsystems.py` | `emit`（生成缺失骨架·不覆盖已填）/ `verify`（34 件存在+合法校验）/ `list` |
| **示例（填充参考）** | `core/claude-home/templates/examples/` | 已填的真实项目片段。`scp_anomaly_bureau/` + `urban_supernatural_business/` + `_subsystem_examples/`（9 个高级件示例，源自《无脸者守则》） |

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
| **plan 内（/outline 主路径）** | `outline.plan.json` step 1 `init_project.py … --emit-style-options` → after-pause `--style <名>` → step 6 `scaffold_subsystems.py emit` → step 7 `verify` | 主代理 Claude Code 按 plan steps 分步建目录+风格+34 子系统 |
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

## 验证器契约

`db_schema_validate.py` 只读校验当前结构：`地图.locations` 是 list，`写作经验.success_patterns`
是 list，伏笔 promises 使用三态字段。单一真理源与契约耦合测试
`tests/test_scaffold_subsystems.py::test_emit_skeletons_pass_db_schema_validate` 保证骨架与验证器一致。

## 测试

`tests/test_scaffold_subsystems.py`（5 例）：骨架完整性 / emit 生成 34 合法 / **scaffold 输出必过 validator（契约耦合）** / verify 检出缺失损坏 / emit 幂等不覆盖。
