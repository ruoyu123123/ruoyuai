# 程序驱动层（v28 · 2026-06-10 · 2026-06-21 GUI/BYOK 回滚收口）

> 用确定性 Python driver 替换「Claude 主循环人肉跟 plan 走步」的编排层。
> 写作主轨（cluster-write → cluster-save-state 循环）已全量程序驱动；
> outline / distill-style 已程序驱动化（2026-06-11 阶段2/3 落地）；
> 仅 check-quality / reconcile 仍 Claude 编排（见〔迁移状态〕）。
>
> **🔴 2026-06-21 commit 2a4d7ce 决策 A**：删 GUI 整层 + 回滚 BYOK·主代理
> **Claude Code 唯一入口**·吃 Claude Code 订阅。本文档 GUI / BYOK / PyInstaller 打包
> 相关段落（标 `[DEPRECATED commit 2a4d7ce]`）仅作历史回溯；当前生效路径 =
> 主代理 Claude Code spawn 调度 → `python core/scripts/orchestrator.py <cmd>` →
> 程序驱动管线。.env 为唯一 key 来源（仓库根·dev 默认路径）。详见 commit 2a4d7ce
> + memory `project_gui_layer_nicegui` / `project_byok_keyring` /
> `project_gui_full_coverage` / `project_packaging_ruoyuai_standalone_exe`。

## 三个新模块（core/scripts/）

| 模块 | 职责 | 关键设计 |
|---|---|---|
| `llm_transport.py` | 统一 LLM transport（M1） | 双协议分发（OpenAI 兼容 + gemini 原生 SSE）· 异常归一 `TransportRateLimit/Timeout/Empty/Exhausted`（gemini path 429 此前不进重试 → 修复）· finish_reason 归一（MAX_TOKENS→length）· 截断**续写**循环（绝不整发重试——同位置再截不收敛）· gemini 原生补 `generationConfig.thinkingConfig.thinkingLevel`（此前缺失 = reasoning thinking 吃光预算的头号失败模式）· httpx 替 urllib（read-timeout 治 43min 僵死）· Retry-After 优先 · 空响应守卫 · `parse_json_loose` 三级抽取 |
| `judge_runner.py` | 判断层统一入口（M2）：8 个判断 agent 直连 gen-model | prompt 单一真理源 = 运行时读 `.claude/agents/<name>.md`（剥 frontmatter + 适配头「文件已代读/只输出 JSON/允许 free_notes」）· **四硬契约**见下 · `AGENT_SPECS` 注册表（failure_policy / needs_author_profile / required_keys / 输出路径模板） |
| `orchestrator.py` | plan-DAG 驱动器（M3）：消费 `plans/*.json` 当声明式 DAG | `plan_tracker` 当库 in-process 调（create/step_complete/end_plan/断点续跑）· `{project_root}` + `<angle>` 占位符解析（plan_tracker 只管 `{key}/{ch}/{next_key}`）· data_flow **lazy 按行解析**（同 step 内脚本产物互喂：splitter→titles）· `prime_cluster_context`（cluster_lookup 权威算 `<cluster_start_ch>/<prev_key>/<prev_pending_tail>/<cluster_num>`）· 占位符解析不掉 = 报错停（绝不带占位符跑命令）· script_runner 可注入（frozen exe 换进程内 import 的唯一改造点） |

辅助：`cluster_choice_apply.py` — 走向卡用户选择 → 机械写回 事件簇.json（status=`in_progress`，
白名单成员——根治 memory 记录的「active 不在白名单 → brief 注入静默 off」坑）。

## judge_runner 四硬契约（北极星⑤ · 对抗审查定调）

1. **作者档第一权威存续**：`needs_author_profile` 的 judge（voice/validator/outline-planner/
   reading-reflector）system 必注作者风格档全文；读不到 → 注入「不得输出风格类 finding」守卫 +
   报告标 `_author_profile_missing`——**绝不退回通用规则审稿**（惊悚乐园流水账实证）。
2. **重试边界**：只对 JSON 语法破损 / required_keys 顶层缺失整发重试 ≤2；
   **判断内容/枚举值不符永不重试**（枚举形状不得规训模型判断）；截断走 transport 续写。
3. **failure_policy 分级**：`block`（summarizer/foreshadower/outline-planner——输出喂状态机，
   失败抛 `JudgeBlockedError` 停流水线，静默透传 = 状态丢失 = 整本书后半崩）；
   `soft`（reflector/voice/validator/reading-reflector——降级落 `_degraded` 报告不阻断）。
4. **自由文本兜底**：所有 judge 输出顶层允许 `free_notes`——schema 是格式闸不是裁决闸。

## plan 模板新增字段（v28 · 增量 · Claude 路径兼容）

| 字段 | 含义 | 示例 |
|---|---|---|
| `steps[].scripts[]` 行首 `"? "` | 条件脚本：非零退出仅警告（style_injector 无风格档 exit 1 = 条件不适用） | `"? python core/scripts/style_injector.py {project_root} <cluster_start_ch>"` |
| `steps[].agent_input` | 判断 agent 的 spawn 参数（从 .md 散文上移）；`*_PATH` 键由 driver 代读为 context_files | `{"MODE": "cluster", "CLUSTER_DRAFT_PATH": "章节/..."}` |
| `steps[].judge_report_path` | JudgeReport 产物路径（str 或 `{agent: path}` dict）——命名学从 `plan_tracker._verify_agent_report` 的 if/elif 外移；end_plan 优先验声明路径（`<round>` → 通配） | |
| `steps[].judge_report_path_secondary` | voice-checker 双载体（brief 内嵌 judge_report 平铺一份） | |
| `steps[].agent_executor: "script"` | 创意 wrapper（novel-writer/splitter）非 judge——工作由 scripts[] 完成，不派 judge_runner（北极星④） | |
| `steps[].data_flow` | `<angle>` 占位符回填声明（lazy 按行解析）；`join_range` 把 `[lo,hi]` 拼 `"lo-hi"`；只回填路径/数值不固化创作内容（北极星②③） | `{"<chapter_range_dash>": {"source_json": "_数据库/.wal/splitter_...json", "field": "chapter_range", "join_range": true}}` |
| `steps[].control_flow.exit_codes` | 退出码→动作（`ok` / `fail` / `dispatch:<agent>`）；audit_hub：0=pass/1=auto_fixed/2=needs_agent→派单/3=fatal | |
| `steps[].control_flow.round_loop` | ROUND 循环（连续 N 轮 clean 放行 / 超 max_rounds 软预算放行）——只表达控制流不编码创作决策 | |
| `steps[].pause_for_user` | 停顿点（choice/integer + source/options_field/answer_artifact）；auto_pilot 显式开关才取第一候选，**默认必弹卡**（北极星③） | |
| `steps[].after_pause_scripts` | 选择落定后的确定性后续（cluster_choice_apply 写回） | |
| `steps[].touch_outputs` | 标记文件（多轮产物文件名可变时的存在性代理） | |

## BYOK 密钥管理 — 🔴 [DEPRECATED commit 2a4d7ce · 2026-06-21 回滚]

**2026-06-21 用户决策 A 回滚**：删 GUI → BYOK keyring 路径不再是用户入口，回到仓库根
`.env` 单一密钥来源（dev 默认路径·开发者自管）。`secrets_store.py` 保留作 keyring 薄抽象
+ redact 工具（gemini key URL 脱敏仍在用），但 GUI 录入卡片 / `set_api_key` 用户入口已删。
`gen_model_loader._resolve_api_key` 三级优先级 **keyring > os.environ > .env 文本**链路代码
保留（向下兼容），实际只走 environ/.env 路径。`tests/gui/` 整目录连同 GUI 一并删除。

历史背景（仅供回溯）：v28 2026-06-10 曾设计 BYOK——分发版 + 非技术用户 GUI 录入 keyring。

### 非密 config 分离 — 🔴 [DEPRECATED commit 2a4d7ce]

分发版不带 `.env`（含开发者私钥）。新增内置非密 config `core/config/gen_profiles.default.env`
（沿用 .env 文本格式·**无任何 `GEN__*__API_KEY` 行**——留空行会被 `load_dotenv(override=True)`
刷 `''` 打断 environ 注入）。

| 机制 | 实现 |
|---|---|
| loader 三级回落 | `gen_model_loader.__init__`：cwd/.env → 仓库根 .env → **内置 config（分发模式）**。`_dist_mode` 按「解析出的 env_path == builtin_cfg」判定（显式传也进 dist·测试生产口径统一） |
| 模式语义 | 「.env 存在 = dev 单一来源」「.env 不存在 = 分发模式 = 内置 config + keyring」。dev 零回归（有 .env 逐字节不变） |
| 用户切 active | `gen_model.set_active`/`runner.switch_active`：dev → 原子改写 .env；dist → 写 `%APPDATA%/ruoyuai/user_overrides.env`（`_user_override_path()`·仅 ACTIVE/FALLBACK 两键·绝不碰只读内置 config）。`__init__` dist 模式叠加 user_override（`load_dotenv override=True`·用户选的 active 赢） |
| frozen 定位 | `Path(__file__).resolve().parent.parent/"config"`·dev 与 onedir 同式命中（**禁 onefile**·否则需读 `sys._MEIPASS`） |

测试：`test_config_split(7·无密钥硬闸/dist回落/keyring供key/user_override切active/set_active dev-dist/dev零回归)`。

### PyInstaller 打包 step6 阶段A — 🔴 [DEPRECATED commit 2a4d7ce]
（`packaging/` 整目录已删·下面是历史记录·frozen 路径推算代码 `frozen_util.py`/
`bundle_root()` 等仍在源码——保留作 dev-no-op·未来重新打包可复用·测试 `test_frozen_util`
按 dev 守卫继续跑。）



**真 PyInstaller onedir exe 实测验证 6 条 dev 无法验的 frozen 路径全过**（`packaging/frozen_smoke.exe` EXIT=0·6/6 PASS·无 Traceback·`_internal` 33MB·torch 排净·dist 无 .env）。

🔴 **修了一个 FATAL 生产 bug**（dev-no-op 测不出·真 GUI exe 必撞）：PyInstaller 扁平收模块使
`gen_model_loader`/`orchestrator` 的 `Path(__file__).parent.parent` 推算在 frozen 下指错 →
config 第三级回落 + REPO_ROOT 脚本解析全失败。修复 = 派生项目路径的模块改用
`frozen_util.bundle_root()`（frozen=`sys._MEIPASS`·dev=仓库根·`resource_path()` 派生）；
`gen_model_loader` builtin_cfg 改 bundle_root 定位 + frozen 跳过 dev repo_env；
`orchestrator.REPO_ROOT` 改 bundle_root。

已验：① `run_script_in_process` 进程内 importlib 在 frozen 工作（正例+无main负例+import-fail负例）
② `child_python`+RUOYU_PYTHON 解析 ③ `secrets_store` WinVault set→get→delete 真回环
④ config 第三级回落经 `_MEIPASS` 命中 `_internal/core/config/` ⑤ REPO_ROOT==`_MEIPASS`。
敌对验证：移走 config → path4 正确 FAIL（harness 非橡皮图章）。
spec 关键：`hiddenimports` 列项目模块（FrozenImporter 只认 PYZ）+ keyring.backends.Windows/fail/null
+ win32ctypes + dotenv；`excludes` torch/sentence_transformers/transformers/scipy/numpy/nicegui；
`datas` config + 脚本 .py（dest `core/config`/`core/scripts`·逐字对齐 bundle_root 推算）·**绝不列 .env**。
测试 `test_packaging_frozen_smoke(6·dev 守卫)` + `test_frozen_util` bundle_root frozen-aware。

### PyInstaller step6 frozen fan-out — 🔴 [DEPRECATED commit 2a4d7ce]
（`ruoyu_gui.py` 入口 + `packaging/` 已删·`frozen_util.is_script_dispatch`/`dispatch_or_none`
代码保留作未来重新打包基础设施·dev no-op。）



🔴 **「最大未知量」消除**：勘察证 cluster-write 质检 17 个 scanner **全是纯 Python**（仅 advisory 的
`style_evaluator` 用 numpy/scipy）→ 采 **multi-call 二进制**方案——GUI exe 兼当 fan-out 解释器，
**完全不需 bundle 独立 python**（绕开 embeddable-python-C-扩展兔子洞）。

- `frozen_util.is_script_dispatch(argv)` / `dispatch_or_none(argv)`：白名单 dispatcher（argv[1] 在
  `bundle_root()/core/scripts` 或 `/packaging` 下且 `.exists()` 才派发·绝不误伤 GUI 启动/spawn）。
- `ruoyu_gui.py` + `frozen_smoke.py` 入口共享：exe 收 `[exe, core/scripts/X.py, args]`（audit_hub
  fan-out 形态）→ `run_script_in_process` 进程内跑 → 带退出码退出·不启 GUI。
- `child_python()` frozen 无 RUOYU_PYTHON → 返 exe 本体（dispatcher 接住·设计正道·去掉旧告警）。
- 🔴 **FATAL 第三处修复**：`audit_hub` 等 6 个 fan-out 脚本 `_SCRIPT_DIR = Path(__file__).parent`
  在 frozen 扁平后传出 `_internal/X.py`（磁盘不存在）→ 统一改 `frozen_util.scripts_dir()`
  （frozen=`bundle_root()/core/scripts`·dev 同值）。

**真 onedir exe 实测 7/7 PASS**（path6 = `[exe, prose_rhythm_scanner.py, draft]` → exe 自我再分派 →
scanner JSON + 退出码透传·与 audit_hub fan-out 同款路径构造）。exe 级敌对：`exe cluster_lookup.py`
→ exit 3（无 main 契约）。`_internal` 仍 33MB（无第二 python）。

### 全 GUI onedir exe — 🔴 [DEPRECATED commit 2a4d7ce]
（`dist/ruoyu_gui`/`packaging/ruoyu_gui.spec`/`validate_gui_exe.py` 全删·下面是历史记录·
当前用户入口 = Claude Code CLI。）



**可分发的全 GUI onedir exe 诞生并验证**（`dist/ruoyu_gui`·`_internal` 274MB·torch 排净）。
`packaging/validate_gui_exe.py` 实测 **PASS 4/4**：① 安全 dist 无 .env / 无 sk- 明文 ② fan-out
dispatch（`ruoyu_gui.exe core/scripts/prose_rhythm_scanner.py draft` → scanner JSON·exe 兼当解释器
在真 GUI exe 工作）③ **可写数据**（`ruoyu_gui.exe core/scripts/plan_tracker.py create ...` 隔离
APPDATA → attest HMAC 密钥 + GLOBAL plan 真写 `%APPDATA%/ruoyuai` 而非只读 bundle）④ GUI serve
（`ruoyu_gui.exe --port N` → HTTP 200 + 含「若渝AI」）。**frozen 路径双面在真二进制全闭合**。

`packaging/ruoyu_gui.spec`（权威·根目录不留第二份）：`collect_all("nicegui")`（无 hook→抓 static/
elements/templates+子模块+metadata）+ 133 脚本 datas+hiddenimports 双登记 + `.claude/agents`/
`plans`/`lessons`/`config`/`subsystem_skeletons` datas + `collect_data_files numpy/scipy`（收进 exe·
style_evaluator multi-call 进程内 import）+ `copy_metadata`（import 期 metadata.version）+ excludes
torch · **绝不 .env**。

🔴 **FATAL 第 4-6 处修复**（「没调查没发言权」彻底网罗 `__file__` 资源路径 bug）：`build_manifest`
lessons（cluster-write 路径·`project_root.parent×3`→`bundle_root()`·frozen 下项目在用户工作区不在
bundle·旧推算 lessons 静默丢失削弱风格一致）/ `scaffold_subsystems` SKELETON / `skill_evolver`
pool → 全改 `bundle_root()`（dev 逐字节一致）。守卫 `test_packaging_frozen_smoke`（13·含 5 GUI spec）。

### frozen 可写系统数据迁 user_data_dir — 🟡 [部分 DEPRECATED commit 2a4d7ce]
（`frozen_util.user_data_dir()` 代码保留——dev 仍逐字节零回归走仓库根·frozen 分支无入口
触发但留作未来基础设施。下面记录原 4 类迁移路径。）



frozen 的 `_internal` 只读 → 跨项目**可写**系统数据写 bundle 必崩。`frozen_util.user_data_dir()`
（dev=仓库根逐字节零回归·frozen=`%APPDATA%/ruoyuai` 可写·同 BYOK user_overrides 范式）迁 4 类：
MAPE-K runtime（self_heal/adaptive·incidents/kb/circuit/runtime_lessons·cluster-save-state step8/9）/
`plan_tracker` GLOBAL_PLANS+ATTEST_KEY（每命令盖章写）/ model_capabilities 缓存（读写同根）/
`skill_evolver` pool。单 agent 对抗验证 sound + 收口 2 项（skill pool 误用 bundle_root 纠 user_data_dir
/ wal_recovery 引 plan_tracker.GLOBAL_PLANS_DIR 归一）。**frozen 路径双面闭合**：只读 `bundle_root()`
+ 可写 `user_data_dir()`。守卫 `test_frozen_util`（18·含 user_data_dir/writable 锚点）。

### 🎉🎉 完整 7 步 cluster-write 真 exe 闭环 — 🔴 [HISTORICAL · GUI 已 DEPRECATED commit 2a4d7ce]
（程序驱动写作主轨**当前生效**——只是不再走 GUI exe·改走 Claude Code CLI 主代理调度
`python core/scripts/orchestrator.py cluster-write`·下面记录原 GUI exe 闭环验证·留作
管线完备性证据：`gen_throttle` / llm_transport 429 退避 / 5 个 e2e bug 修复全保留。）



**整条程序驱动写作主轨在真 frozen GUI exe 完整跑通**（`ruoyu_gui.exe core/scripts/orchestrator.py
cluster-write --project <copy> --key 001 --auto-pilot`·`GEN_MIN_INTERVAL_S=4.5 BEST_OF_N=1`）：
step1 build-manifest → step2 gen_writer **13664 CJK 真实正文**（expand 2 轮·18103 chars）→
step3 质检 scanner + reading-reflector 5 轮循环 + validator-checker 派单 → step4 voice-checker
（瞬态 404 软降级·不阻断·北极星⑤）→ step5 foreshadower+reflector+summarizer（reflector 撞 429
自动指数退避重试成功）→ step6 splitter **切 3 章 + gen_chapter_titles**（`第001章 断梯` 等）→
step7 plan-end·**EXIT=0 无 Traceback 无 520**。产出 3 章真实连贯辰东风格《凿窍纪》正文（断天者
重黎/建木绝顶/绝天之刀）。**「程序驱动 exe 写小说」终极目标完整达成**。

🔴 **第 5 个 e2e bug·中转站限速**：完整管线快速连发 writer best-of-N/expand+多 judge → pie-xian
中转站 <15rpm 限速返 Cloudflare 520（用户告知特性·非端点挂）。`gen_throttle.py` 模块级全局
min-interval 闸（frozen 单进程内 writer+judge 共享·4 处请求点·env `GEN_MIN_INTERVAL_S` 默认 0
关零回归·15rpm 端点设 4.5）+ llm_transport 429 指数退避 → 瞬态限流优雅处理。

### 🎉 真 GUI exe 端到端写一章 — 🔴 [DEPRECATED commit 2a4d7ce]

**真 GUI onedir exe + BYOK keyring 密钥 → 写出真实连贯一章**（用户选「端到端真写一章」授权 gen-model
API）。链路全程真二进制：`ruoyu_gui.exe core/scripts/gen_writer.py --project <copy> --cluster 1`
→ dispatch 进程内跑 → build_manifest 读全子系统+lessons → 组装 60k 字 prompt（system 39628 +
user 20301）→ 调 gemini_pro_preview → 收 5641 chars → 存 `章节/cluster_001_draft.txt`（3619 CJK·
辰东风格《凿窍纪》真实正文：重黎/断天者/天梯绝顶/法则纹理）+ changes.json·EXIT=0 无 Traceback。

**真二进制 end-to-end 抓出 2 个单测/模拟测不出的真实情况**：
1. 🔴 **UTF-8 dispatch bug**（已修）：frozen Windows exe stdout 默认 GBK → dispatch 脚本 print
   emoji（prompt 里的 🔴）UnicodeEncodeError 崩。三道纵深 reconfigure UTF-8（ruoyu_gui 最早 +
   dispatch_or_none + run_script_in_process·frozen-gated）。prose_rhythm dispatch 没暴露因中文 GBK
   可编码·emoji 不行。
2. ✅ **BYOK 强制正确**（设计意图非 bug）：frozen 下 run_script_in_process chdir 到 `_MEIPASS`，
   gen_model_loader `cwd/.env` 查到 bundle 内（无 .env·安全）→ 内置 config（无密钥）→ keyring。
   用户须经 GUI 录 key 进 keyring（DPAPI）→ gen_writer 读到。**不泄漏开发者 .env 私钥**。验证时模拟
   GUI 录入（key→keyring·`secrets_store.set_api_key`）后写成功·测毕删除恢复机器状态。

`packaging/validate_gui_exe.py` + 本次 end-to-end = **整条程序驱动写作主轨在真分发 exe 验证可用**。

### 一键构建验证 runbook — 🔴 [DEPRECATED commit 2a4d7ce]
（`packaging/build_all.py` 已删·当前无分发版构建路径——主代理 Claude Code 直跑 python
脚本即可。）

## 用户入口（2026-06-21 commit 2a4d7ce 后唯一形态）

主代理 **Claude Code CLI** 是用户与若渝AI 的唯一入口。流程：

1. 用户在 Claude Code 会话中输入命令（`/cluster-write` / `/outline` / `/distill-style` …）。
2. 主代理读 plan 模板（`core/claude-home/plans/*.json`），按强制规划层 spawn 出 `python
   core/scripts/orchestrator.py <cmd>` 子进程。
3. orchestrator 按 plan-DAG 跑 scripts / judge_runner / pause_for_user（终端 input() 渲染
   走向卡候选）。
4. 走向卡停顿 = 终端候选列表（`orchestrator._cli_pause_handler`）；用户回车选号 →
   `cluster_choice_apply.py` 机械写回事件簇.json。
5. key 来源 = 仓库根 `.env`（dev 默认路径·开发者自管·gen-model API key）。

历史背景：v28 2026-06-10 曾经引入 NiceGUI 图形界面（`core/gui/` + `ruoyu_gui.py` +
`packaging/`），让非技术用户脱离 CLI。2026-06-21 commit 2a4d7ce 整层物理删除——决策见
memory `project_gui_layer_nicegui` / `project_gui_full_coverage`（均已加 DEPRECATED 标）。

## 用法

```bash
# 写一个 cluster（程序驱动 · 不经 Claude）
python core/scripts/orchestrator.py cluster-write --project 书名 --key 001
# 状态保存 + 涌现下一 cluster（走向卡在终端弹选）
python core/scripts/orchestrator.py cluster-save-state --project 书名 --key 001
# 断点续跑（plan JSON steps[].status 是唯一断点真相源）
python core/scripts/orchestrator.py cluster-write --project 书名 --key 001 --resume <plan_id>
# 显式全自动（走向卡取引擎第一候选——绝非隐式默认）
python core/scripts/orchestrator.py cluster-save-state --project 书名 --key 001 --auto-pilot
# 单 judge 调试
python core/scripts/judge_runner.py novel-summarizer workspace/novels/书名 \
  --param CLUSTER_ID=cluster_001 --file 草稿=章节/cluster_001_draft/cluster_001_draft.txt \
  --output _数据库/.wal/cluster_001_summary.json
```

## 与 Claude 编排共存

- driver 经 plan_tracker 合法 API 写 plan（每次写盘重盖 attestation 章）→ 不触发防篡改。
- hooks 拦的是 Claude 工具调用，对独立 python 进程无感。
- 模板新字段是增量——Claude 路径忽略不认识的字段；`end_plan` 无 `judge_report_path`
  声明时回落旧命名推算（全向后兼容）。
- **共存期不删 attestation / hooks**（driver 全量验证后按北极星⑥清旧码）。

## 迁移状态

| 命令 | 状态 |
|---|---|
| `/cluster-write` | ✅ 全量程序驱动（7 步：manifest → gen_writer → audit+reading-reflector 循环 → voice → 三 judge → splitter+titles+changes → end） |
| `/cluster-save-state` | ✅ 全量程序驱动（12 步含 emergence + 走向卡停顿点 + 选择写回） |
| `/outline` | ✅ 全量程序驱动（12 步 · 2026-06-11 阶段2 落地；GUI 前置 = RUNNER.start 前 mkdir 项目目录 + 写 `_数据库/.wal/book_meta.json`） |
| `/distill-style` | ✅ 全量程序驱动（8 步 · 2026-06-11 阶段3 落地；GUI 前置 = mkdir `STYLES_DIR/<书名>/蒸馏进度/.wal` + 落原文 `raw_author_text.txt`） |
| `/check-quality` `/reconcile` | ⏳ Claude 编排（模板零 scripts——创作步骤需先落为 gen-model 脚本才可机械执行，见各模板 `_program_driven_status`） |
| novel-researcher | ⏳ soft 降级运行（gen-model 无 web 工具；联网检索待接外部 API 或确认代理透传 grounding） |

## 已知边界（上线前必验）

1. **judge「Claude 过 ≠ gen-model 过」**：8 个 judge 的金标准对比重测（真作者原文当输入，
   对比 JudgeReport 字段完整性 + 作者档维度引用）尚未跑——M2 验证里程碑。
2. **frozen exe（解释器解析已全面就绪 · 打包 recipe 待补）**：
   - 顶层脚本调度：`orchestrator.default_script_runner` frozen 时走 `run_script_in_process`
     （进程内 importlib 调脚本 main()·已测）；dev 子进程路径强制
     `PYTHONIOENCODING=utf-8`+`PYTHONUTF8=1` 治 GBK 日志乱码。
   - **所有 fan-out 子进程解释器统一走 `frozen_util.child_python()`**（dev=`sys.executable`
     no-op·frozen=`RUOYU_PYTHON` 随包 python·缺则回退+告警暴露）：直接 fan-out
     `[child_python(), script, …]`（audit_hub 21 scanner / save_state / gen_writer /
     gen_fixer / run_cross_cluster_aggregates / evolution_orchestrator / maybe_judge_consensus /
     wal_recovery / distill_finalize_verify / system_health_audit）+ **间接 fan-out**
     `adaptive_runner.run_with_resilience` 入口 `_normalize_interpreter` 归一内层 `python`
     字面量（plan REMAINDER `-- python core/scripts/Y.py` + auto_heal str cmd）。
     守卫测试 `test_no_bare_child_interpreter_anywhere_in_scripts` 扫全 `core/scripts`
     杜绝未来漏网。
   - **残留 = 打包 recipe（非代码）**：onedir 打包时须 bundle 一份 python 运行时并
     `set RUOYU_PYTHON` 指向它；且必须用**真 onedir 产物**在干净 Win11（无 Python）
     端到端跑一遍 cluster-write/save-state 验证（dev 无法验 frozen·见对抗审查纪律）。
3. **深 schema judge**（validator-checker 16 维 / outline-planner 3 模式）在 reasoning
   模型上的截断率需实测；必要时拆分多次调用。

测试：`tests/test_llm_transport.py`（25）/ `test_judge_runner.py`（22）/
`test_orchestrator.py`（26）/ `test_plan_templates_program_driven.py`（9）。
