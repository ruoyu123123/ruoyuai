#!/usr/bin/env python3
"""orchestrator.py — plan-DAG 程序驱动器（程序驱动 M3 · 2026-06-10）

确定性 Python driver，把 core/claude-home/plans/*.json 当声明式 DAG 解释执行，
替换「Claude 主循环人肉跟 plan 走步」。plan_tracker 当库 in-process 调（非 CLI），
复用其状态机内核（create/step_complete/end_plan/占位符替换/{next_key} 递增/断点续跑）。

核心循环：
    create_plan → for step (按 n 升序):
        status==completed → 跳过（断点续跑·plan JSON steps[].status 是唯一断点真相源）
        跑 scripts[]（跳 # 注释行 · {project_root}/<dataflow> 占位解析 · python 前缀
                     → 可注入 script_runner——为 frozen exe 改进程内 import 留接口）
        must_spawn_agent → judge_runner.run_judge（gen-model 判断层 · 非 Claude spawn）
        pause_for_user → 停顿点（auto_pilot 显式开关取默认 · 绝非隐式·北极星③软牵引）
        control_flow → 退出码分支 / ROUND 循环（非线性控制流从 .md 散文上移为模板字段）
        step_complete（expected_outputs 存在性 assert）
    end_plan（required_steps 全 completed + JudgeReport 存在 assert）

与现有 Claude 编排共存：本 driver 经 plan_tracker 合法 API 写 plan（每次 _save_plan
重新盖 attestation 章），不触发防篡改；hooks 拦的是 Claude 工具调用，对独立进程无感。
共存期不删 attestation/hooks（等 driver 全量验证后按北极星⑥清旧码）。

step 间数据流（对抗审查识别的最大隐性工作量）：
plan 模板 scripts[] 里的 <range_from_splitter_wal> / <start> 等角括号占位符是
「上一步产物回填」，此前靠 Claude 读 WAL 解析。本 driver 用 steps[].data_flow 声明：
    "data_flow": {"<range_from_splitter_wal>": {
        "source_json": "_数据库/.wal/splitter_cluster_{key}_decisions.json",
        "field": "chapter_range"}}
角括号占位符解析不掉 → 报错停（绝不带着占位符跑命令）。
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_tracker as pt  # noqa: E402

REPO_ROOT = _SCRIPTS.parent.parent
SCRIPT_TIMEOUT = 1800  # 单脚本 30min 上限（splitter/audit 大 cluster 也够）


class OrchestratorError(Exception):
    """driver 级失败（脚本非零退出 / 占位符解析不掉 / 停顿点无 handler）。"""


@dataclass
class StepOutcome:
    n: object
    name: str
    status: str                  # completed | skipped(已完成) | paused
    detail: str = ""


@dataclass
class RunSummary:
    plan_id: str
    command: str
    completed: list = field(default_factory=list)   # list[StepOutcome]
    paused_at: object = None                        # 停顿点 step n（None=跑完）
    end_report: dict | None = None                  # end_plan 返回


# ============ 占位符解析 ============
def resolve_placeholders(text: str, ctx: dict) -> str:
    """解析 {project_root} + ctx 里登记的 <angle> 占位符（plan_tracker 已在 create
    时替换掉 {project}/{key}/{ch}/{next_key}——这里只处理它不管的两类）。"""
    out = text.replace("{project_root}", str(ctx.get("project_root", "")))
    for k, v in ctx.items():
        if k.startswith("<") and k.endswith(">"):
            out = out.replace(k, str(v))
    return out


def unresolved_angle_placeholders(text: str) -> list[str]:
    import re
    # 排除比较运算符等误报：占位符约定为 <小写字母/数字/_> 形态
    return re.findall(r"<[a-z][a-z0-9_]*>", text)


def load_dataflow(step: dict, ctx: dict) -> dict:
    """按 steps[].data_flow 声明读上一步产物，填进 ctx（<placeholder> → 值）。

    data_flow 只回填路径/数值，不固化创作内容（北极星②③：候选/走向由引擎运行时算，
    模板只声明「从哪个文件哪个字段取」）。
    """
    flows = step.get("data_flow") or {}
    project_root = Path(ctx["project_root"])
    for placeholder, spec in flows.items():
        if not (placeholder.startswith("<") and placeholder.endswith(">")):
            continue
        if placeholder in ctx:
            continue  # 已解析（prime/round/前序 lazy 解析）不重复读
        if "literal" in spec:
            ctx[placeholder] = spec["literal"]
            continue
        src = resolve_placeholders(spec.get("source_json", ""), ctx)
        p = Path(src)
        if not p.is_absolute():
            p = project_root / src
        if not p.exists():
            if spec.get("optional"):
                ctx[placeholder] = spec.get("default", "")
                continue
            raise OrchestratorError(
                f"data_flow 源文件不存在: {p}（占位符 {placeholder}）")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise OrchestratorError(f"data_flow 源解析失败 {p}: {e}") from e
        cur = data
        for seg in (spec.get("field") or "").split("."):
            if not seg:
                continue
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                if spec.get("optional"):
                    cur = spec.get("default", "")
                    break
                raise OrchestratorError(
                    f"data_flow 字段缺失: {spec.get('field')} in {p}")
        # join_range：[lo, hi] 列表 → "lo-hi"（gen_chapter_titles --chapters 口径）
        if spec.get("join_range") and isinstance(cur, list) and len(cur) == 2:
            cur = f"{cur[0]}-{cur[1]}"
        ctx[placeholder] = cur
    return ctx


def ensure_dataflow(step: dict, ctx: dict, text: str):
    """lazy 数据流：只解析 text 里实际出现且 ctx 还没有的占位符。

    与 step 开头一次性 eager 解析的关键差异：同一 step 内「脚本 1 产 WAL → 脚本 2
    消费」（splitter → gen_chapter_titles 读 chapter_range）时，脚本 2 的占位符
    必须等脚本 1 跑完才解析得到——按行 just-in-time 解析。
    """
    needed = [ph for ph in unresolved_angle_placeholders(text) if ph not in ctx]
    if not needed:
        return ctx
    flows = step.get("data_flow") or {}
    subset = {k: v for k, v in flows.items() if k in needed}
    if subset:
        load_dataflow({"data_flow": subset}, ctx)
    return ctx


def prime_cluster_context(ctx: dict, project_root: Path, key: str | None):
    """cluster 命令的领域派生值预计算（driver 启动时一次性 · 用 cluster_lookup 权威
    反查——北极星①：禁 f"cluster_{ch:03d}" 机械拼接）。

    填入 ctx：<cluster_id> / <cluster_start_ch> / <prev_key> / <prev_pending_tail>。
    <cluster_start_ch> = 上一 cluster 范围 hi+1（事件簇.json 权威）→ 退而扫
    章节/第*章 物理目录 max+1 → 再退 1（新书首 cluster）。
    """
    if not key:
        return ctx
    try:
        import cluster_lookup as cl
    except ImportError:
        return ctx
    cid = cl.normalize_cluster_id(key) or f"cluster_{key}"
    ctx["<cluster_id>"] = cid
    num = cl.cluster_num(cid)
    ctx["<cluster_num>"] = num if num is not None else key  # gen_writer --cluster 要 int
    prev_key = f"{num - 1:0{max(3, len(str(num)))}d}" if num and num > 1 else ""
    ctx["<prev_key>"] = prev_key

    start_ch = None
    if prev_key:
        prev_range = cl.cluster_id_to_range(project_root, f"cluster_{prev_key}")
        if prev_range and len(prev_range) == 2:
            start_ch = int(prev_range[1]) + 1
    if start_ch is None:
        own = cl.cluster_id_to_range(project_root, cid)
        if own and len(own) == 2:
            start_ch = int(own[0])
    if start_ch is None:
        import re as _re
        max_ch = 0
        ch_dir = project_root / "章节"
        if ch_dir.exists():
            for d in ch_dir.iterdir():
                m = _re.match(r"第(\d+)章", d.name)
                if m:
                    max_ch = max(max_ch, int(m.group(1)))
        start_ch = max_ch + 1
    ctx["<cluster_start_ch>"] = start_ch

    tail = ""
    if prev_key:
        cand = (project_root / "章节" / f"cluster_{prev_key}_draft"
                / f"cluster_{prev_key}_pending_tail.txt")
        if cand.exists():
            tail = str(cand)
    ctx["<prev_pending_tail>"] = tail
    return ctx


# ============ 脚本执行 ============
def _strip_python_prefix(cmd_line: str) -> list[str]:
    tokens = shlex.split(cmd_line, posix=True)
    if tokens and tokens[0] in ("python", "python3", "py"):
        tokens = tokens[1:]
    return tokens


def run_script_in_process(tokens: list[str], *, repo_root: Path = REPO_ROOT,
                          label: str = "step") -> int:
    """frozen-safe：进程内 importlib 跑脚本（不起子进程）。

    onedir exe 里没有 python.exe，sys.executable 是 GUI 本体——subprocess 会重启 GUI
    而非跑目标脚本（对抗审查头号致命项 F）。frozen 下走本函数：import 脚本模块、
    临时改 sys.argv/cwd 调 module.main()、捕 SystemExit 取退出码。

    脚本 print 走 sys.stderr（已被 GUI StderrTee 接管）→ 日志照常流入 UI。
    脚本须有 main()（本库脚本一致满足）。共享解释器状态（sys.argv/cwd/单例）用
    try/finally 还原，避免步间污染。
    """
    import importlib
    import os as _os

    if not tokens:
        return 0
    script_path = Path(tokens[0])
    if not script_path.is_absolute():
        script_path = repo_root / tokens[0]
    mod_name = script_path.stem
    argv = [str(script_path)] + tokens[1:]
    print(f"[orchestrator][{label}] (in-process) {mod_name} {' '.join(tokens[1:])}",
          file=sys.stderr)

    saved_argv, saved_cwd = sys.argv, _os.getcwd()
    sys.argv = argv
    try:
        try:
            _os.chdir(str(repo_root))
        except OSError:
            pass
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        mod = importlib.import_module(mod_name)
        main_fn = getattr(mod, "main", None)
        if main_fn is None:
            print(f"[orchestrator][{label}] 脚本 {mod_name} 无 main()，frozen 下无法进程内调用",
                  file=sys.stderr)
            return 3
        try:
            ret = main_fn()
        except SystemExit as e:           # 脚本用 sys.exit() 退出
            ret = e.code
        return int(ret) if isinstance(ret, int) else (0 if ret is None else 3)
    except Exception as e:                # 脚本内部异常 → 当失败退出码（不崩 GUI）
        import traceback
        print(f"[orchestrator][{label}] in-process 脚本异常: {type(e).__name__}: {e}",
              file=sys.stderr)
        for line in traceback.format_exc().splitlines()[-6:]:
            print(f"[orchestrator][{label}]   {line}", file=sys.stderr)
        return 3
    finally:
        sys.argv = saved_argv
        try:
            _os.chdir(saved_cwd)
        except OSError:
            pass


def default_script_runner(cmd_line: str, *, repo_root: Path = REPO_ROOT,
                          label: str = "step") -> int:
    """script runner：dev 走子进程；frozen exe 走进程内 importlib（无 python.exe）。

    本函数是唯一注入点，orchestrator 主体不感知执行方式（GUI/CLI 都用它）。
    """
    tokens = _strip_python_prefix(cmd_line)
    if not tokens:
        return 0

    # frozen onedir：没有 python.exe，subprocess 会重启 GUI 本体 → 进程内跑（finding F）
    if getattr(sys, "frozen", False):
        return run_script_in_process(tokens, repo_root=repo_root, label=label)

    # dev：子进程。强制子进程 UTF-8 输出（GBK Windows 上 pipe 默认 locale 编码=gbk，
    # 父进程按 utf-8 解码 → 中文/emoji 全乱码进 GUI 日志·finding H）。
    import os as _os
    from frozen_util import child_python
    env = dict(_os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    full = [child_python()] + tokens
    print(f"[orchestrator][{label}] $ {' '.join(tokens)}", file=sys.stderr)
    proc = subprocess.run(full, cwd=str(repo_root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=SCRIPT_TIMEOUT, env=env)
    if proc.stdout:
        sys.stderr.write(proc.stdout[-4000:])
    if proc.stderr:
        sys.stderr.write(proc.stderr[-4000:])
    return proc.returncode


def _tokenize_then_resolve(line: str, ctx: dict) -> str:
    """先按模板 token 化再逐 token 替换占位符——替换值（中文/空格路径）保持单 token。"""
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        tokens = line.split()
    resolved = [resolve_placeholders(t, ctx) for t in tokens]
    # 可选参数清理：占位符解析为空串的 token 丢弃，且其前面的 --flag 一并丢
    # （如 --previous-pending-tail <prev_pending_tail> 在无 pending_tail 时整对消失）
    cleaned: list[str] = []
    for t in resolved:
        if t == "":
            if cleaned and cleaned[-1].startswith("--"):
                cleaned.pop()
            continue
        cleaned.append(t)
    # 含空格的 token 重新加引号（subprocess 走 list 不走 shell，这里只为日志可读 +
    # default_script_runner 的二次 shlex.split 不破坏）
    out = []
    for t in cleaned:
        out.append(f'"{t}"' if (" " in t and not t.startswith('"')) else t)
    return " ".join(out)


# ============ 判断 agent 派发 ============
def default_judge_dispatch(agent_name: str, step: dict, ctx: dict):
    """按 steps[].agent_input / judge_report_path 派发到 judge_runner（gen-model）。

    agent_input 的 *_PATH 键 → driver 代读为 context_files（judge 无 Read 工具）。
    judge_report_path 从模板字段直读——_verify_agent_report 那套 if/elif 命名学外移。
    """
    import judge_runner as jr

    project_root = Path(ctx["project_root"])
    raw_input = step.get("agent_input") or {}
    params: dict = {}
    context_files: list[tuple[str, Path]] = []
    for k, v in raw_input.items():
        ensure_dataflow(step, ctx, str(v))
        rv = resolve_placeholders(str(v), ctx)
        params[k] = rv
        if k.endswith("_PATH"):
            p = Path(rv)
            if not p.is_absolute():
                p = project_root / rv
            if p.exists():
                context_files.append((k, p))

    # judge_report_path：str（单 agent）或 {agent_name: path} dict（一步多 agent）
    jrp = step.get("judge_report_path")
    out_raw = jrp.get(agent_name, "") if isinstance(jrp, dict) else (jrp or "")
    output_path = None
    if out_raw:
        op = Path(resolve_placeholders(out_raw, ctx))
        output_path = op if op.is_absolute() else project_root / op
    sec_raw = step.get("judge_report_path_secondary") or ""
    secondary = None
    if sec_raw:
        sp = Path(resolve_placeholders(sec_raw, ctx))
        secondary = sp if sp.is_absolute() else project_root / sp

    return jr.run_judge(agent_name, project_root, params=params,
                        context_files=context_files, output_path=output_path,
                        secondary_output_path=secondary)


# ============ 停顿点 ============
def cli_pause_handler(step: dict, spec: dict, options: list) -> object:
    """CLI 缺省停顿点：终端渲染候选/提问 → input()。NiceGUI 模式换 async handler。"""
    prompt = spec.get("prompt") or f"step {step.get('n')} 需要你的选择"
    if spec.get("type") == "choice" and options:
        print(f"\n=== {prompt} ===")
        for i, opt in enumerate(options, 1):
            label = opt.get("label") or opt.get("title") or str(opt)[:80] \
                if isinstance(opt, dict) else str(opt)[:80]
            print(f"  [{i}] {label}")
        while True:
            raw = input(f"选择 1-{len(options)}: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
    raw = input(f"{prompt}: ").strip()
    return int(raw) if spec.get("type") == "integer" and raw.isdigit() else raw


def _resolve_pause(step: dict, ctx: dict, *, auto_pilot: bool,
                   pause_handler) -> object:
    spec = step.get("pause_for_user") or {}
    options: list = []
    src = spec.get("source")
    if src:
        p = Path(resolve_placeholders(src, ctx))
        if not p.is_absolute():
            p = Path(ctx["project_root"]) / p
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cur = data
                for seg in (spec.get("options_field") or "").split("."):
                    if seg and isinstance(cur, dict):
                        cur = cur.get(seg, [])
                options = cur if isinstance(cur, list) else []
            except (OSError, json.JSONDecodeError):
                options = []
    if auto_pilot:
        # 显式全自动：choice 取引擎排序第一候选（_emergence_score 序）；integer 取 default。
        # 北极星③：这是显式开关非隐式默认——默认必弹卡等用户。
        if spec.get("type") == "choice" and options:
            return options[0]
        return spec.get("default")
    if pause_handler is None:
        raise OrchestratorError(
            f"step {step.get('n')} 有 pause_for_user 但未提供 pause_handler "
            f"且未开 auto_pilot——停顿点不允许静默跳过")
    return pause_handler(step, spec, options)


# ============ 主驱动 ============
def run_command(command: str, project: str, *, key: str | None = None,
                chapter: int | None = None,
                resume_plan_id: str | None = None,
                auto_pilot: bool = False,
                script_runner=None,
                judge_dispatch=None,
                pause_handler=None,
                pause_stops_run: bool = True,
                step_callback=None,
                repo_root: Path = REPO_ROOT) -> RunSummary:
    """跑一个命令的完整 plan 流水线（或从 resume_plan_id 断点续跑）。

    pause_stops_run=True：遇停顿点取到用户输入后写回 ctx 并继续；handler 返回
    None → 暂停退出（GUI 异步模式下由前端拿到 plan_id 续跑）。
    """
    runner = script_runner or default_script_runner
    dispatch = judge_dispatch or default_judge_dispatch

    if resume_plan_id:
        plan_id = resume_plan_id
    else:
        plan_id = pt.create_plan(command, project, chapter=chapter, key=key)
    plan = pt.get_plan(plan_id)

    project_root = pt.resolve_project_root(plan.get("project"))
    if project_root is None:
        project_root = Path(plan.get("project") or ".")
    ctx: dict = {"project_root": str(project_root), "plan_id": plan_id,
                 "key": plan.get("key"), "chapter": plan.get("chapter")}
    prime_cluster_context(ctx, Path(project_root), plan.get("key"))

    summary = RunSummary(plan_id=plan_id, command=command)
    steps = sorted(plan.get("steps", []), key=lambda s: float(s.get("n", 0)))

    total_steps = len(steps)
    for step in steps:
        n, name = step.get("n"), step.get("name", "")
        if step.get("status") == pt.STATUS_COMPLETED:
            summary.completed.append(StepOutcome(n, name, "skipped", "断点续跑跳过"))
            continue
        print(f"\n[orchestrator] ▶ step {n}: {name}", file=sys.stderr)
        if step_callback:
            try:
                step_callback(n, name, total_steps)
            except Exception:
                pass  # 观察者回调绝不打断流水线

        # 1)+2) scripts[]（data_flow 按行 lazy 解析——同 step 内脚本产物互喂）
        for raw_line in (step.get("scripts") or []):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue  # 注释/伪命令行（spawn 描述走 must_spawn_agent）
            advisory_line = line.startswith("? ")  # 行首 "? " = 条件脚本·非零仅警告
            if advisory_line:
                line = line[2:].strip()
            ensure_dataflow(step, ctx, line)
            cmd = _tokenize_then_resolve(line, ctx)
            leftover = unresolved_angle_placeholders(cmd)
            if leftover:
                raise OrchestratorError(
                    f"step {n} 脚本占位符未解析: {leftover}（缺 data_flow 声明）"
                    f"\n  行: {cmd}")
            rc = runner(cmd, repo_root=repo_root, label=f"step{n}")
            if advisory_line:
                if rc != 0:
                    print(f"[orchestrator] WARN 条件脚本退出码 {rc}（advisory·继续）: "
                          f"{cmd}", file=sys.stderr)
                continue
            action = _exit_code_action(step, rc)
            if action == "fail":
                raise OrchestratorError(
                    f"step {n} 脚本退出码 {rc}: {cmd}\n（plan {plan_id} 停在本步·"
                    f"修复后 --resume 续跑）")
            if action.startswith("dispatch:"):
                agent = action.split(":", 1)[1]
                print(f"[orchestrator] 退出码 {rc} → 派单 {agent}", file=sys.stderr)
                dispatch(agent, step, ctx)
            # 'ok' → 继续

        # 3) must_spawn_agent（含 ROUND 循环）
        # agent_executor=="script"：创意 wrapper（novel-writer/novel-chapter-splitter）
        # 的真实工作已由本步 scripts[]（gen_writer.py/chapter_splitter.py）完成——
        # must_spawn_agent 仅为 Claude 编排路径兼容 + end_plan 校验保留，不派 judge。
        agents = step.get("must_spawn_agent")
        if agents and step.get("agent_executor") != "script":
            if isinstance(agents, str):
                agents = [agents]
            loop_cfg = step.get("control_flow", {}).get("round_loop") \
                if isinstance(step.get("control_flow"), dict) else None
            for agent in agents:
                if loop_cfg and loop_cfg.get("agent") == agent:
                    _run_round_loop(agent, step, ctx, loop_cfg, dispatch, runner,
                                    repo_root)
                else:
                    dispatch(agent, step, ctx)

        # 4) pause_for_user
        if step.get("pause_for_user"):
            answer = _resolve_pause(step, ctx, auto_pilot=auto_pilot,
                                    pause_handler=pause_handler)
            if answer is None and not auto_pilot:
                summary.paused_at = n
                print(f"[orchestrator] ⏸ step {n} 等待用户输入（plan {plan_id}）",
                      file=sys.stderr)
                return summary
            ctx[f"<user_answer_step_{n}>"] = answer
            ans_path = step["pause_for_user"].get("answer_artifact")
            if ans_path:
                ap = Path(resolve_placeholders(ans_path, ctx))
                if not ap.is_absolute():
                    ap = Path(ctx["project_root"]) / ap
                ap.parent.mkdir(parents=True, exist_ok=True)
                ap.write_text(json.dumps({"step": n, "answer": answer},
                                         ensure_ascii=False, indent=2),
                              encoding="utf-8")
            # 选择落定后的确定性后续（如 cluster_choice_apply 写回 事件簇.json）
            for raw_line in (step.get("after_pause_scripts") or []):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                ensure_dataflow(step, ctx, line)
                cmd = _tokenize_then_resolve(line, ctx)
                rc = runner(cmd, repo_root=repo_root, label=f"step{n}-post-pause")
                if rc != 0:
                    raise OrchestratorError(
                        f"step {n} after_pause 脚本退出码 {rc}: {cmd}")

        # 5) touch_outputs：模板声明的标记/骨架文件（.placeholder 存在性代理；
        #    .json 后缀写 "{}" 合法骨架——如 save-state step1 的 cluster WAL，
        #    Claude 流程里由 Claude 亲手建，程序驱动下由 driver 建）
        for t_raw in (step.get("touch_outputs") or []):
            tp = Path(resolve_placeholders(t_raw, ctx))
            if not tp.is_absolute():
                tp = Path(ctx["project_root"]) / tp
            tp.parent.mkdir(parents=True, exist_ok=True)
            if not tp.exists():
                tp.write_text("{}" if tp.suffix == ".json" else "", encoding="utf-8")

        # 6) step_complete（expected_outputs assert·plan_tracker 内置）
        pt.step_complete(plan_id, n, skip_output=bool(step.get("skip_output_allowed")))
        summary.completed.append(StepOutcome(n, name, "completed"))

    summary.end_report = pt.end_plan(plan_id)
    if not summary.end_report.get("ok"):
        raise OrchestratorError(
            f"end_plan 校验未过: {json.dumps(summary.end_report, ensure_ascii=False)}")
    return summary


def _exit_code_action(step: dict, rc: int) -> str:
    """退出码 → 动作。缺省 {0: ok, 其余 fail}；模板可声明
    control_flow.exit_codes: {"0":"ok","1":"ok","2":"dispatch:<agent>","3":"fail"}。"""
    cf = step.get("control_flow") or {}
    mapping = cf.get("exit_codes") or {}
    action = mapping.get(str(rc))
    if action:
        return action
    return "ok" if rc == 0 else "fail"


def _run_round_loop(agent: str, step: dict, ctx: dict, cfg: dict, dispatch,
                    runner, repo_root: Path):
    """ROUND 循环（reading-reflector：连续 N 轮 clean 才放行·超 max_rounds 软放行）。

    control_flow.round_loop 只表达循环控制（pass 条件/轮数上限/轮间修复脚本），
    判定内容来自 judge 输出——不编码创作决策（北极星⑤）。
    """
    max_rounds = int(cfg.get("max_rounds", 5))
    need_clean = int(cfg.get("consecutive_clean", 1))
    pass_field = cfg.get("pass_field", "verdict")
    pass_value = cfg.get("pass_value", "pass")
    between = cfg.get("between_rounds_scripts") or []

    clean = 0
    for rnd in range(1, max_rounds + 1):
        ctx["<round>"] = rnd
        outcome = dispatch(agent, step, ctx)
        data = getattr(outcome, "data", None) or {}
        verdict = data.get(pass_field)
        issues = data.get("new_issues_this_round") or data.get("issues") or []
        if verdict == pass_value and not issues:
            clean += 1
            if clean >= need_clean:
                print(f"[orchestrator] {agent} 连续 {clean} 轮 clean → 放行",
                      file=sys.stderr)
                return
        else:
            clean = 0
            for raw_line in between:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                cmd = _tokenize_then_resolve(line, ctx)
                rc = runner(cmd, repo_root=repo_root,
                            label=f"step{step.get('n')}-r{rnd}-fix")
                if rc != 0:
                    print(f"[orchestrator] WARN 轮间修复脚本退出码 {rc}（advisory·"
                          f"继续下一轮）", file=sys.stderr)
    print(f"[orchestrator] WARN {agent} {max_rounds} 轮后未达连续 {need_clean} 轮 "
          f"clean → 软预算放行（advisory 非门禁·北极星⑤）", file=sys.stderr)


# ============ CLI ============
def main():
    import argparse
    ap = argparse.ArgumentParser(description="plan-DAG 程序驱动器（程序驱动 M3）")
    ap.add_argument("command", help="cluster-write / cluster-save-state / outline / ...")
    ap.add_argument("--project", required=True)
    ap.add_argument("--key", help="cluster key（如 001）")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--resume", help="断点续跑的 plan_id")
    ap.add_argument("--auto-pilot", action="store_true",
                    help="显式全自动：停顿点取引擎第一候选（默认必弹卡等用户）")
    args = ap.parse_args()

    summary = run_command(args.command, args.project, key=args.key,
                          chapter=args.chapter, resume_plan_id=args.resume,
                          auto_pilot=args.auto_pilot,
                          pause_handler=cli_pause_handler)
    done = [s for s in summary.completed if s.status == "completed"]
    skipped = [s for s in summary.completed if s.status == "skipped"]
    print(f"\n[orchestrator] ✅ {summary.command} 完成 plan={summary.plan_id} "
          f"(完成 {len(done)} 步 · 续跑跳过 {len(skipped)} 步)")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
