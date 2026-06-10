# 程序驱动层（v28 · 2026-06-10）

> 用确定性 Python driver 替换「Claude 主循环人肉跟 plan 走步」的编排层。
> 写作主轨（cluster-write → cluster-save-state 循环）已全量程序驱动；
> outline / distill-style / check-quality / reconcile 仍 Claude 编排（见〔迁移状态〕）。

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

## BYOK 密钥管理（非技术用户自带 key · 2026-06-10）

分发版绝不带开发者 `.env` 私钥——非技术用户在 GUI 设置页录入**自己的** API key，经
`keyring`（Windows 凭据管理器·DPAPI 用户级加密）存储。

| 层 | 实现 |
|---|---|
| `core/scripts/secrets_store.py` | keyring 薄抽象（唯一 import keyring 的非 GUI 模块）：`get/set/delete/has_api_key`、`is_available()`（isinstance fail/null 判定）、`redact()`（gemini key-in-URL 脱敏）。service=`ruoyuai-gen-model`，username=`profile.name`。软退化：keyring 缺→静默 None |
| `gen_model_loader._resolve_api_key` | `Profile.api_key` 三级优先级 **keyring > os.environ > .env 文本**（唯一注入点·下游 13 脚本零改）。`load_dotenv(override=True)` 使 environ 层仅在 .env **未定义**该 key 时独立生效 |
| GUI 设置页 | per-profile 录入卡片（密码框 + 保存到 keyring + 已配置/未配置徽章 + 测试连接）；保存后清空输入、绝不展示明文、绝不写回 .env。`runner.save_api_key` 后 `reset_default_loader()` 让录入即生效 |

**安全红线**：绝不 log/写回 .env key；gemini key 在 URL（gen_writer urllib / llm_transport
httpx）的异常 str() 必经 `redact()` 再抛（否则经 stderr→GUI LogBuffer→界面泄漏）。

测试：`test_secrets_store(11)`/`test_loader_precedence(7·含 dev 零回归基线)`/
`tests/gui/test_gui_settings(8·整链端到端+redact 守卫)`。内存 keyring 后端不碰真
Credential Manager。

### 非密 config 分离（step5 · 已做 · 2026-06-10）

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

### PyInstaller 打包 step6 阶段A（frozen 架构 de-risk · 已验 · 2026-06-10）

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

**残留 step6 阶段B（全 GUI onedir · 下轮）**：① ruoyu_gui.py 完整 spec（NiceGUI 静态资源
datas + 全 127 脚本 + .claude/agents/*.md + plans/*.json + jiter/tiktoken hidden-import）
② **真 bundle 精简 python + set RUOYU_PYTHON**（fan-out 子进程 audit_hub 21 scanner 需真解释器
import numpy/scipy·embeddable python 能否 import C 扩展是最大未知·须单独 spike）③ 干净 Win11
端到端 cluster-write + GUI 录 key 真机冒烟（PowerShell 复验 stderr·`feedback_verify_stderr_not_exitcode`）。

## 图形界面（脱离 Claude CLI · 2026-06-10）

NiceGUI 桌面/浏览器界面，直接驱动 orchestrator——非技术用户无需 Claude CLI。

| 文件 | 职责 |
|---|---|
| `ruoyu_gui.py` | 启动器（`multiprocessing.freeze_support()` 首句 · UTF-8 reconfigure） |
| `core/gui/state.py` | 纯逻辑（零 nicegui）：`AppState` / `PauseBridge`（req_id 代际令牌防多 tab 抢答）/ `StderrTee`+`LogBuffer`（日志捕获）/ `scan_project`（项目进度 + 下一步推断） |
| `core/gui/runner.py` | 纯逻辑：`PipelineRunner` 工作线程驱动 `orchestrator.run_command`，与 UI 走 `AppState`+`PauseBridge` 解耦 |
| `core/gui/app.py` | 唯一 import nicegui：写作台（项目/一键写故事块·保存·连跑/实时日志/走向卡 awaitable dialog）+ Plan 续跑页 + 设置页 |

启动：`python ruoyu_gui.py`（浏览器）/ `--native`（桌面窗口·需 pywebview）。

**官方成熟模式（复用减少排错）**：走向卡 = awaitable `ui.dialog().submit()` + 后台任务解耦；
日志 = `ui.log` + `LogBuffer` 单调游标（**per-client 闭包游标**，非共享）；长任务 = 工作线程 +
`ui.timer(0.5)` 轮询；走向卡停顿 = `PauseBridge`（threading.Event 桥 + req_id 防陈旧/串台应答）。

**北极星③**：走向卡默认必弹卡等用户，`auto_pilot` 是显式开关（默认 False）；超时/被抢答的
陈旧卡由 `_tick` 主动回收，迟到点击被桥 req_id 校验拒绝——绝不静默替用户选剧情走向。

测试：`tests/test_gui_state.py`（28·逻辑）/ `tests/gui/test_gui_user.py`（10·NiceGUI 官方 User
模拟 UI）。boot smoke 实测服务器起得来且对外服务。

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
| `/outline` `/distill-style` `/check-quality` `/reconcile` | ⏳ Claude 编排（模板零 scripts——创作步骤需先落为 gen-model 脚本才可机械执行，见各模板 `_program_driven_status`） |
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
