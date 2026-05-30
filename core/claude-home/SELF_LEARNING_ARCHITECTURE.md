# 若渝AI 运行时自学习 / 自适应 / 自监控架构（2026-05-30）

> 目标（用户原话）：「让自学习系统能实现自动学习运行过程中的报错，能让脚本自适应运行过程中的问题，
> 并且能随时监控运行过程中缺失的步骤并且补充上去，同时要联网对标其他自学习自适应系统」。
>
> 本文档是**联网对标 + 落地设计**的单一权威。设计前已联网调研 5 个方向（来源见末尾），
> 落地复用现有底座（learning_loop / plan_tracker / WAL / hook），**不另起一套被删过的「自进化层」**——
> 聚焦**运行时健壮性**（报错学习 / 脚本自适应 / 缺步补全），而非创作自进化。

---

## 一、业界对标（联网调研结论）

业界共识的自学习自适应骨架 = **MAPE-K 控制环 + Reflexion 记忆层 + Saga 编排 + 韧性模式（熔断/容错）**。

| 方向 | 核心模式 | 来源 | 若渝AI 落地 |
|------|---------|------|------------|
| 自愈系统 | **MAPE-K**（Monitor→Analyze→Plan→Execute over Knowledge，IBM 自治计算） | ScienceDirect 自治计算综述 / SEAMS 2015 | 整体骨架（见下图） |
| 错误驱动学习 | **Reflexion**（失败→自然语言反思入记忆）/ **ExpeL**（成败对比提炼 insight）| arXiv 2303.11366 / 2308.10144 | self_heal_engine 的 lesson 生成 |
| 运行时自适应 | **Circuit Breaker** 三态机 / **Tolerant Reader**（`.get(k,default)` 容错读） | Azure 架构中心 / Martin Fowler | adaptive_runner 熔断 + 各脚本容错读 |
| 缺步监控 | **Saga 编排** + 补偿幂等 + 超时定位未完成步 | AWS / Google Cloud | step_completion_monitor + plan_tracker |
| LLM 自进化 | 记忆 ADD/MERGE/DELETE 演化 · prompt 反射进化（GEPA）· 自改代码（Gödel Agent，**缺 rollback 是其已知短板**）| arXiv 2507.21046 / 2507.19457 / 2410.04444 | 仅 advisory 参数 · Git 快照当 rollback 锚点 |

---

## 二、若渝AI 落地架构（MAPE-K 闭环 · 6 组件）

```
            ┌─────────── Knowledge 知识库（系统级 · core/claude-home/runtime/）──────────┐
            │  incidents.jsonl（原始报错流） · self_heal_kb.json（指纹→根因→动作→lesson）  │
            │  circuit_state.json（熔断器态） · lessons/runtime_lessons.md（Reflexion 沉淀） │
            └────────────────────────────────────────────────────────────────────────────┘
                  ▲ 读                                                    ▲ 写
  ① Monitor ───→ ② Analyze ───→ ③ Plan ───→ ④ Execute ───→ ⑤ Reflect/Learn ──┐
  hook 扫 stderr  错误指纹复发    按 severity   重试/降级/    复发≥3 recurring     │
  Traceback +     计数 + 分类     选策略        熔断/补缺步   ≥5 known→lesson     │
  adaptive 捕获   (ACTION_MAP)                  (幂等)        regression 检测     │
  内部 subprocess                                                               │
        ▲                      ⑥ Orchestrator（plan_tracker + step_completion_monitor）│
        └──────── 缺步监控（假完成/失败/未跑）+ 幂等补产出 + WAL 断点 ←─────────────────┘
```

**数据流**：脚本崩溃 → Monitor 捕获指纹写 incidents.jsonl → self_heal_engine 复发计数+分类入 kb →
adaptive_runner 查 kb 选策略（重试/降级/熔断）→ 失败提炼成 lesson 回写 → 下次召回，越跑越稳。
Orchestrator 横贯保证不漏步、可补、可断点续跑。

---

## 三、组件详解

### ① Monitor — `core/claude-home/hooks/posttooluse_runtime_monitor.py`（PostToolUse:Bash hook）
- 扫 Bash 子进程输出的**真实 Python Traceback / [FATAL] / [CRASH]**，提取错误指纹
  `signature = script::error_type::location` append `runtime/incidents.jsonl`。
- 纪律：查 **stderr 的 Traceback**（不信 exit code，呼应 memory `feedback_verify_stderr_not_exitcode`）；
  排除 grep/echo/cat/git 等搜索类避免字面误报；**观察层永不 exit 非 0**（不打断主流水线）。
- 局限：只能看主代理**直接** Bash 调用的脚本 → 流水线**内部** subprocess 报错由 ④ adaptive_runner 补捕获。

### ② + ⑤ Analyze + Knowledge + Reflect — `core/scripts/self_heal_engine.py`
- `--ingest`：增量读 incidents.jsonl，按 signature 复发计数（**复用 learning_loop ≥3 recurring / ≥5 known 阈值范式**），
  按 `error_type` 查 `ACTION_MAP` 填根因猜测 + 推荐动作 + severity，写 `self_heal_kb.json`。
- `--suggest <sig|type>`：给某错误的推荐动作（adaptive_runner / 主代理查询，输出 JSON）。
- `--emit-lessons`：known/regression 级 → `lessons/runtime_lessons.md`（Reflexion 自然语言「现象/为什么/怎么用」）。
- `--resolve <sig>` / `--dashboard`：标记已修（再现→**regression 重新激活**，警示「修复未生效」）/ 运行时健康全景。
- severity 五类：`adapt`（脚本自适应可解）/ `retry`（transient 重试）/ `degrade`（降级）/ `missing_step`（缺步）/ `escalate`（代码 bug 升人）。

### ③ + ④ Plan + Execute — `core/scripts/adaptive_runner.py`
- 跑流水线**内部** subprocess：捕获 stderr Traceback/exit → 提取指纹 append incidents → 查 kb 的 severity → 按策略：
  `retry`（指数退避，仅 transient）/ `degrade`（降级放行，已记录）/ `escalate`（🔴 升人警告）。
- **熔断器三态**（Closed→Open→Half-Open）：同一 label 累计失败 ≥5 → Open（快速失败+降级，防连续崩盲跑），冷却 300s → Half-Open 试探。
- **核心价值**：取代流水线 `|| true` 的静默吞错——失败不再消失，而是**记录 + 学习 + 熔断**
  （呼应 memory `feedback_no_micro_task_workaround`「故障显式降级记录，不偷偷绕过」）。
- 用法：`python adaptive_runner.py --label X -- python core/scripts/Y.py ...`（失败默认 degrade 放行 exit 0；`--strict` 则 exit 1）。

### ⑥ Orchestrator — `core/scripts/step_completion_monitor.py` + `plan_tracker.py` + `wal_recovery.py`
- step_completion_monitor 扫 plan 实例，检测三类缺步：**output_missing**（completed 但 expected_outputs 缺=假完成）/ **failed** / **not_run**（中断未跑）。
- `--auto-heal`：对有 scripts 的假完成/失败 step → 经 adaptive_runner **幂等重跑**补产出（去 `|| true`、跳过 `#` 行、替换 `{project_root}`）；
  agent 类 / not_run → 输出 brief 给主代理（脚本不 spawn agent、不替主代理跑流程）。
- 把 plan_tracker 从「缺步则拦」升级为「缺步可补」；WAL（`completed_steps`）保留断点续跑。

---

## 四、集成点（嵌入 cluster 主流程，非独立层）

`cluster-save-state.plan.json`（每 cluster 跑一次）：
- **step 8 / 9 的 4 个 `|| true`** → `adaptive_runner` 包裹（judge_reports_archive / skill_evolver evolve+promote / evolution_orchestrator / maybe_judge_consensus）。
- **step 9 末尾追加**：`self_heal_engine --ingest` + `--emit-lessons` + `step_completion_monitor --scan-latest`。
- Monitor hook 在 `.claude/settings.json` 的 `PostToolUse:Bash` 常驻（全程捕获）。

---

## 五、北极星边界（不可逾越）

1. 自学习只学**运行时报错**（脚本崩溃 / 缺步），**绝不碰创作判断**——推荐动作是 advisory，不改 hard_gate 一致性/契约/穿帮逻辑（北极星原则 5「不干涉模型判断」）。
2. **绝不自改脚本逻辑**（Gödel Agent 自改代码缺 rollback 是已知短板）——只做重试/降级/记录/升人/补产出/调 advisory 阈值。
3. 补跑/补偿必须**幂等**（Saga 纪律），且系统已有 Git 快照当 rollback 锚点。
4. runtime 数据是**系统级**（跨小说项目锚 `core/claude-home/runtime/`），脚本报错与具体项目无关。
5. 以 **cluster** 为学习/监控单位（嵌在 cluster-save-state，非 chapter）。

---

## 六、来源清单（联网调研）

- MAPE-K / 自愈：ScienceDirect 自治计算综述；SEAMS 2015《Modeling MAPE-K Feedback Loops》
- 错误学习：arXiv 2303.11366（Reflexion）；arXiv 2308.10144（ExpeL）；Zalando AI Postmortem
- 运行时自适应：Azure Architecture Center（Circuit Breaker）；Martin Fowler（Tolerant Reader）
- 缺步监控：AWS Prescriptive Guidance（Saga Orchestration）；Google Cloud（Saga in Workflows）
- LLM 自进化：arXiv 2507.21046（Self-Evolving Agents Survey）；2507.19457（GEPA）；2410.04444（Gödel Agent）
