#!/usr/bin/env python3
"""step_completion_monitor.py — Saga 缺步监控 + 幂等补全（2026-05-30 自学习能力）

MAPE-K **Orchestrator** 增强：把 plan 流水线从「缺步则拦」升级为「缺步可补」。
扫描 plan 实例，检测三类缺步：
  · output_missing（假完成）：step status=completed 但 expected_outputs 文件不存在（声称做了没产出）
  · failed：step status=failed
  · not_run（中断）：plan 未 end + required step 仍 pending
对「有 scripts 可幂等重跑」的 output_missing/failed step → 经 adaptive_runner 重跑补产出
（去 `|| true`、跳过 `#` 注释行、替换 `{project_root}`，失败记录学习）；
对「agent 类 / 纯标记类 / not_run」缺步 → 输出 brief 给主代理（脚本不 spawn agent / 不替主代理跑流程）。

命令：
  --scan <plan_id>                                     输出缺步诊断（JSON + 摘要）
  --scan-latest --command <cmd> --project <name>       自动找项目最近一个该命令 plan 再 scan
  --auto-heal <plan_id> [--include-not-run]            幂等重跑补产出（默认只补 output_missing/failed）
  --list --project <name>                              列项目所有 plan 缺步概览

北极星边界：monitor 只补**产出**，**不改 plan status**（status 权威仍是 plan_tracker，避免触碰
HMAC attestation）；重跑前提是 scripts 幂等（Saga 补偿幂等纪律）；agent / hard_gate step 绝不自动执行。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_tracker as pt          # 复用 get_plan / _verify_outputs / resolve_project_root
import adaptive_runner as ar       # 复用 run_with_resilience（韧性重跑 + 记录学习）


def _clean_scripts(step: dict):
    """取可重跑的脚本行：去 `#` 注释行 / 空行。

    狩猎修：剥 `? ` advisory 前缀（对齐 orchestrator 语义）——否则 auto_heal 把
    `? python ...` 原样交 cmd.exe 报「'?' 不是内部或外部命令」。返回 (line, advisory)。"""
    out = []
    for s in (step.get("scripts") or []):
        s = (s or "").strip()
        if not s or s.startswith("#"):
            continue
        advisory = s.startswith("? ")
        if advisory:
            s = s[2:].strip()
        out.append((s, advisory))
    return out


def scan(plan_id: str) -> dict:
    try:
        plan = pt.get_plan(plan_id)
    except Exception as e:
        return {"error": f"读取 plan 失败: {e}", "plan_id": plan_id, "findings": []}
    if not plan:
        return {"error": "plan 不存在", "plan_id": plan_id, "findings": []}

    project = plan.get("project")
    required = set(plan.get("required_steps", []) or [])
    plan_ended = bool(plan.get("completed_at") or plan.get("aborted_at"))

    findings = []
    for step in plan.get("steps", []):
        n = step.get("n")
        status = step.get("status")
        try:
            _verified, missing = pt._verify_outputs(plan, step, project)
        except Exception as e:
            print(f"⚠️ [step-monitor] step{n} 产出校验异常（按无缺处理，请核查）: {e}", file=sys.stderr)
            missing = []
        scripts = _clean_scripts(step)
        agent = step.get("must_spawn_agent")

        reason = None
        if status == pt.STATUS_COMPLETED and missing:
            reason = "output_missing"          # 假完成：声称完成但产出缺
        elif status == pt.STATUS_FAILED:
            reason = "failed"
        elif status in (pt.STATUS_PENDING, None, pt.STATUS_IN_PROGRESS) and n in required and not plan_ended:
            reason = "not_run"                 # 中断：required step 还没跑完
        if not reason:
            continue

        heal_kind = "scripts" if scripts else ("agent" if agent else "marker")
        findings.append({"n": n, "name": step.get("name"), "status": status, "reason": reason,
                         "missing": missing, "heal_kind": heal_kind,
                         "scripts": scripts, "spawn_agent": agent})

    return {"plan_id": plan_id, "command": plan.get("command"), "project": project,
            "plan_ended": plan_ended, "findings": findings,
            "healable": [f for f in findings if f["heal_kind"] == "scripts"
                         and f["reason"] in ("output_missing", "failed")],
            "agent_needed": [f for f in findings if f["heal_kind"] == "agent"]}


def _project_root_of(project) -> Path:
    try:
        if project:
            r = pt.resolve_project_root(project)
            if r:
                return Path(r)
    except Exception:
        pass
    return Path(".").resolve()


def auto_heal(plan_id: str, include_not_run: bool = False) -> dict:
    rep = scan(plan_id)
    if rep.get("error"):
        return rep
    project_root = _project_root_of(rep.get("project"))

    reasons = {"output_missing", "failed"}
    if include_not_run:
        reasons.add("not_run")
    targets = [f for f in rep["findings"] if f["heal_kind"] == "scripts" and f["reason"] in reasons]

    healed, still_missing, ran = [], [], []
    import shlex
    for f in targets:
        for item in f["scripts"]:
            script, advisory = item if isinstance(item, tuple) else (item, False)
            # 狩猎修：弃 shell 字符串（cmd.exe 不解 bash 单引号·中文路径双引号嵌单引号
            # 双重破损）→ shlex list 化·占位符替换后天然单 token·绕 cmd.exe。
            toks = shlex.split(script.replace(" || true", ""), posix=True)
            toks = [t.replace("{project_root}", str(project_root)) for t in toks]
            label = f"heal_s{f['n']}_{rep.get('command', 'plan')}"
            r = ar.run_with_resilience(toks, label=label)
            if advisory and not r.get("ok"):
                r = {**r, "ok": True, "action": "advisory_nonzero_ignored"}
            ran.append({"n": f["n"], "cmd": " ".join(toks)[:120], "ok": r.get("ok"),
                        "degraded": r.get("degraded"), "action": r.get("action")})
        # 重跑后复查产出（仅对 output_missing 有 expected_outputs 可判）
        try:
            plan2 = pt.get_plan(plan_id)
            step2 = next((s for s in plan2.get("steps", []) if s.get("n") == f["n"]), None)
            _, missing2 = pt._verify_outputs(plan2, step2, rep.get("project")) if step2 else ([], [])
        except Exception:
            missing2 = []
        (healed if not missing2 else still_missing).append(f["n"])

    # 需主代理介入的缺步（agent 类 / not_run 未补 / marker）
    needs_agent = [{"n": f["n"], "name": f["name"], "reason": f["reason"],
                    "spawn_agent": f["spawn_agent"], "heal_kind": f["heal_kind"]}
                   for f in rep["findings"]
                   if f["heal_kind"] in ("agent", "marker") or (f["reason"] == "not_run" and not include_not_run)]

    return {"plan_id": plan_id, "command": rep.get("command"), "project": rep.get("project"),
            "ran": ran, "healed_steps": healed, "still_missing_steps": still_missing,
            "needs_main_agent": needs_agent}


def _print_report(rep: dict, title: str):
    print("=" * 64)
    print(f"{title} · plan={rep.get('plan_id')} · {rep.get('command')} · project={rep.get('project')}")
    if rep.get("error"):
        print(f"  ❌ {rep['error']}")
        print("=" * 64)
        return
    findings = rep.get("findings", [])
    if not findings:
        print("  ✅ 无缺步（所有 required step 已完成且产出齐全）")
    for f in findings:
        mark = {"output_missing": "🟠假完成", "failed": "🔴失败", "not_run": "🟡未跑"}.get(f["reason"], f["reason"])
        heal = {"scripts": "可幂等重跑补", "agent": f"需 spawn {f['spawn_agent']}", "marker": "纯标记/需人工"}[f["heal_kind"]]
        print(f"  step {f['n']} [{f['name']}] {mark} → {heal}")
        if f["missing"]:
            print(f"      缺产出: {f['missing']}")
    print("=" * 64)


def list_project_plans(project: str):
    root = _project_root_of(project)
    plans_dir = root / "_数据库" / ".plans"
    if not plans_dir.exists():
        print(f"无 plan 目录: {plans_dir}")
        return 0
    files = sorted(plans_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"项目 {project} 共 {len(files)} 个 plan：")
    for fp in files[:20]:
        rep = scan(fp.stem)
        n_find = len(rep.get("findings", []))
        flag = "✅" if n_find == 0 else f"⚠️{n_find}缺步"
        print(f"  {flag}  {fp.stem}")
    return 0


def _plan_id_of(fp: Path) -> str:
    """读 plan 文件里真正的 id 字段（权威）；读不到则退回文件名 stem。

    🔴 G3 e2e 修：当 plan 用路径型 project 创建时 make_plan_id 把斜杠塞进 id，
    plan 文件落到 .plans/<嵌套子目录>/<id 尾段>.json。此时 fp.stem 只是 id 的尾段，
    拿去 get_plan 会因 _find_plan_path(project=split前段) 错配而 FileNotFoundError。
    读文件内的 "id" 字段才能拿到含完整路径的真 plan_id，回喂 get_plan 正好命中。"""
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        pid = data.get("id")
        if pid:
            return pid
    except Exception:
        pass
    return fp.stem


def _find_latest(command: str, project: str):
    root = _project_root_of(project)
    plans_dir = root / "_数据库" / ".plans"
    if not plans_dir.exists():
        return None
    # 🔴 G3 e2e 修：rglob 递归扫（含路径型 project 造成的嵌套子目录），否则
    # 顶层非递归 glob 永远找不到落在 .plans/workspace/novels/... 的 plan。
    cands = [p for p in plans_dir.rglob(f"*_{command}_*.json")]
    if not cands:
        cands = [p for p in plans_dir.rglob("*.json") if command in p.stem]
    if not cands:
        return None
    latest = max(cands, key=lambda p: p.stat().st_mtime)
    return _plan_id_of(latest)


def _harden_stdio():
    """🔴 G3 e2e 修：Windows GBK 控制台打 emoji（🟡🔴✅）会 UnicodeEncodeError 崩溃，
    缺步监控是 advisory 观察层，不该因打印编码崩掉阻断主流水 → stdout/stderr 转 utf-8
    + errors=replace（无法编码字符降级为占位符不抛异常）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _harden_stdio()
    ap = argparse.ArgumentParser(description="Saga 缺步监控 + 幂等补全")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", metavar="PLAN_ID")
    g.add_argument("--auto-heal", metavar="PLAN_ID")
    g.add_argument("--scan-latest", action="store_true")
    g.add_argument("--list", action="store_true")
    ap.add_argument("--command")
    ap.add_argument("--project")
    ap.add_argument("--include-not-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    if args.list:
        if not args.project:
            print("--list 需 --project", file=sys.stderr)
            return 2
        return list_project_plans(args.project)

    plan_id = args.scan or args.auto_heal
    if args.scan_latest:
        if not (args.command and args.project):
            print("--scan-latest 需 --command 和 --project", file=sys.stderr)
            return 2
        plan_id = _find_latest(args.command, args.project)
        if not plan_id:
            # 🔴 G3 e2e 修：缺步监控是观察层（advisory），找不到 plan 不该 exit 1
            # 阻断主流水线（对齐 posttooluse_runtime_monitor「严禁 exit 非 0」纪律）。
            print(f"⚠️ [step-monitor] 未找到项目 {args.project} 的 {args.command} plan"
                  f"（advisory · 跳过缺步监控不阻断主流水）", file=sys.stderr)
            return 0

    if args.auto_heal:
        rep = auto_heal(plan_id, include_not_run=args.include_not_run)
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print("=" * 64)
            print(f"auto-heal · plan={plan_id}")
            if rep.get("error"):
                print(f"  ❌ {rep['error']}")
            else:
                print(f"  重跑脚本 {len(rep['ran'])} 条 · 补全 step {rep['healed_steps']} · 仍缺 {rep['still_missing_steps']}")
                for r in rep["ran"]:
                    print(f"    step{r['n']} {'✅' if r['ok'] else ('⚠️降级' if r['degraded'] else '🔴')} {r['cmd']}")
                if rep["needs_main_agent"]:
                    print("  ⚠️ 需主代理介入（脚本不能替跑）：")
                    for na in rep["needs_main_agent"]:
                        print(f"    step{na['n']} [{na['name']}] {na['reason']} → {na.get('spawn_agent') or na['heal_kind']}")
            print("=" * 64)
        return 0

    # scan / scan-latest
    rep = scan(plan_id)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _print_report(rep, "缺步监控 scan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
