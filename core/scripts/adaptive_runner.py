#!/usr/bin/env python3
"""adaptive_runner.py — MAPE-K **Plan + Execute** 脚本自适应执行器（韧性模式 · 2026-05-30 自学习）

统一跑流水线内部子进程：捕获 stderr Traceback / exit → 提取错误指纹 append incidents.jsonl
（喂 self_heal_engine 学习）→ 查 self_heal_kb 的 severity → 按策略 Plan：
  retry(指数退避·仅 transient) / adapt(记录续跑) / escalate(升人警告) / missing_step(标缺步)
+ 熔断器三态（Closed→Open→Half-Open）防同一脚本连续崩还盲跑。

**核心价值**：取代流水线里 `|| true` 的静默吞错——失败不再消失，而是**记录 + 学习 + 熔断**
（呼应 memory feedback_verify_stderr_not_exitcode「别信 exit code、别吞 crash」+
 feedback_no_micro_task_workaround「故障显式降级记录，不偷偷绕过」）。

用法：
  CLI:    python adaptive_runner.py --label build_manifest -- python core/scripts/build_manifest.py ...
          （失败默认 exit 1 中断；仍记录 incident 并更新熔断）
  Module: from adaptive_runner import run_with_resilience; r = run_with_resilience([...], label="x")

北极星边界：自适应只做「重试 / 记录 / 升人 / 标缺步」，**绝不自改脚本逻辑、绝不绕过 hard_gate**。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from frozen_util import child_python, user_data_dir as _udd  # noqa: E402

# 系统根：incidents/circuit 是跨项目**可写**系统数据。
REPO_ROOT = _udd()
CIRCUIT_THRESHOLD = 5      # 同一 label 累计失败 N 次 → Open（熔断）
CIRCUIT_COOLDOWN = 300     # Open 冷却秒数 → Half-Open 放一次试探
DEFAULT_MAX_RETRIES = 2    # retry severity（transient）的重试次数

TRACEBACK_MARK = "Traceback (most recent call last):"
EXC_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit)):?\s*(.*)$", re.M)
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')
SCRIPT_RE = re.compile(r"core[/\\]scripts[/\\](\w+\.py)")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ts_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _runtime_dir(root: Path) -> Path:
    d = root / "core" / "claude-home" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm_msg(msg: str) -> str:
    msg = re.sub(r"['\"][^'\"]*['\"]", "'X'", msg)
    msg = re.sub(r"[A-Za-z]:[\\/][^\s]+", "PATH", msg)
    msg = re.sub(r"\b\d+\b", "N", msg)
    return msg.strip()[:200]


def extract_incident(command: str, output: str):
    """从子进程输出提取错误指纹（与 Monitor hook 同源逻辑，自包含保鲁棒）。"""
    if TRACEBACK_MARK not in output:
        return None
    seg = output[output.rfind(TRACEBACK_MARK):]
    files = FILE_LINE_RE.findall(seg)
    excs = EXC_RE.findall(seg)
    et, em = (excs[-1] if excs else ("UnknownError", ""))
    loc = f"{files[-1][0].replace(chr(92), '/').split('/')[-1]}:{files[-1][1]}" if files else ""
    scripts = SCRIPT_RE.findall(seg) or SCRIPT_RE.findall(command)
    sc = scripts[-1] if scripts else ""
    return {"kind": "python_traceback", "script": sc, "error_type": et, "location": loc,
            "message": _norm_msg(em), "signature": f"{sc}::{et}::{loc}", "raw": seg[:600]}


def _append_incident(root: Path, inc: dict, command: str):
    try:
        inc = dict(inc)
        inc["ts"] = _now()
        inc["command_snippet"] = command[:160]
        inc["via"] = "adaptive_runner"
        with open(_runtime_dir(root) / "incidents.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(inc, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _query_severity(root: Path, inc: dict) -> str:
    """查 self_heal_kb 已知 severity；未见过则按 error_type 用 ACTION_MAP 分类。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import self_heal_engine as she
        kb = she.load_kb(root)
        pat = kb.get("patterns", {}).get(inc.get("signature"))
        if pat and pat.get("severity"):
            return pat["severity"]
        return she._classify(inc.get("error_type", ""))[2]
    except Exception:
        return "escalate"


def _load_circuit(root: Path) -> dict:
    p = _runtime_dir(root) / "circuit_state.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _save_circuit(root: Path, st: dict):
    p = _runtime_dir(root) / "circuit_state.json"
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def _eval_circuit(cs: dict) -> str:
    """返回当前有效态：closed / open / half_open（考虑冷却）。"""
    state = cs.get("state", "closed")
    if state == "open":
        opened = cs.get("opened_at_epoch", 0)
        if _ts_epoch() - opened >= CIRCUIT_COOLDOWN:
            return "half_open"   # 冷却到期 → 放一次试探
        return "open"
    return state


_PY_TOKENS = ("python", "python3", "py")


def _normalize_interpreter(cmd):
    """把命令首位的 'python'/'python3'/'py' 字面量换成 frozen-aware child_python()。

    list 形态：cmd[0] in _PY_TOKENS → 换 child_python()。
    str  形态：以 'python '/'python3 '/'py ' 开头 → 替换首 token（不简单 prepend·
              否则得到 'child_python() python ...'）。
    dev 下 child_python()==sys.executable，但字面量 'python' 在 dev 也可能因 PATH 无
    python 而失败——统一归一更稳（dev 行为：'python'→真解释器，等价或更好）。
    """
    if isinstance(cmd, str):
        for tok in _PY_TOKENS:
            prefix = tok + " "
            if cmd.startswith(prefix):
                return f'"{child_python()}" ' + cmd[len(prefix):]
        return cmd
    if isinstance(cmd, (list, tuple)) and cmd and str(cmd[0]) in _PY_TOKENS:
        return [child_python()] + [str(c) for c in cmd[1:]]
    return cmd


def run_with_resilience(cmd, label, project_root=None, max_retries=None,
                        allow_degrade=False, timeout=600):
    """跑子命令并自适应。返回 dict: {ok, degraded, exit_code, action, signature, label, ...}。
    project_root 仅决定 runtime/（incidents/circuit）落盘根，默认系统根；子命令自身的工作目录不受影响。"""
    root = Path(project_root).resolve() if project_root else REPO_ROOT
    circuit = _load_circuit(root)
    cs = circuit.setdefault(label, {"state": "closed", "fail_count": 0, "opened_at_epoch": 0})
    eff = _eval_circuit(cs)
    if eff == "open":
        print(f"⚡ [circuit OPEN] {label} 熔断中（连续失败 {cs.get('fail_count')} 次，冷却未到）"
              f"→ 跳过执行并失败返回（请先修复后 reset）", file=sys.stderr)
        return {"ok": False, "degraded": False, "circuit": "open", "action": "circuit_open",
                "label": label, "exit_code": None}

    # cmd 可为 list（subprocess 直跑）或 str（shell 命令串，用于跑 plan template 的 scripts 行）
    # 🔴 frozen 归一（对抗审查 finding·M4）：内层 'python' 字面量（来自 plan 的
    # `adaptive_runner --label X -- python core/scripts/Y.py` REMAINDER，或 auto_heal 的
    # str cmd）必须换 child_python()——onedir 里 PATH 上无 python，否则这些间接 fan-out
    # 间接 fan-out 失败必须显式失败；adaptive_runner 只负责记录和熔断，不负责放行。
    cmd = _normalize_interpreter(cmd)
    is_shell = isinstance(cmd, str)
    if is_shell:
        run_target, cmd_str = cmd, cmd
    else:
        cmd = [str(c) for c in cmd]
        run_target, cmd_str = cmd, " ".join(cmd)
    attempt = 0
    last = {}
    while True:
        attempt += 1
        try:
            proc = subprocess.run(run_target, shell=is_shell, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            stdout, stderr, rc = proc.stdout or "", proc.stderr or "", proc.returncode
        except subprocess.TimeoutExpired:
            stdout, stderr, rc = "", f"TimeoutError: 子进程超过 {timeout}s 未完成", 1
        except Exception as e:
            stdout, stderr, rc = "", f"RunnerError: 无法启动子进程: {e}", 1

        out = stdout + "\n" + stderr
        # Monitor 纪律：查 stderr Traceback，不只信 exit code（exit 0 也可能 stderr crash）
        crashed = (rc != 0) or (TRACEBACK_MARK in stderr)
        if not crashed:
            if cs.get("state") != "closed":
                cs.update({"state": "closed", "fail_count": 0, "opened_at_epoch": 0})
                _save_circuit(root, circuit)
            return {"ok": True, "degraded": False, "exit_code": rc, "label": label, "attempt": attempt}

        # 崩了 → 提取指纹 + 记录 + 查 severity
        inc = extract_incident(cmd_str, out)
        if inc is None:   # 无 Traceback 但 exit 非0 → 合成一条通用 incident
            inc = {"kind": "nonzero_exit", "script": (SCRIPT_RE.search(cmd_str).group(1) if SCRIPT_RE.search(cmd_str) else ""),
                   "error_type": "NonZeroExit", "location": "",
                   "message": _norm_msg(stderr[-200:]), "signature": f"{label}::NonZeroExit::rc{rc}",
                   "raw": stderr[-600:]}
        severity = _query_severity(root, inc)
        if inc.get("error_type") in ("TimeoutError", "ConnectionError"):
            severity = "retry"
        _append_incident(root, inc, cmd_str)

        # transient → 指数退避重试
        if severity == "retry" and attempt <= (max_retries if max_retries is not None else DEFAULT_MAX_RETRIES):
            backoff = 2 ** (attempt - 1)
            print(f"🔁 [retry {attempt}] {label} ({inc.get('error_type')}) {backoff}s 后重试", file=sys.stderr)
            time.sleep(backoff)
            continue

        # 失败终态 → 熔断计数
        cs["fail_count"] = cs.get("fail_count", 0) + 1
        if cs["fail_count"] >= CIRCUIT_THRESHOLD:
            cs["state"] = "open"
            cs["opened_at_epoch"] = _ts_epoch()
            cs["opened_at"] = _now()
        elif eff == "half_open":
            cs["state"] = "open"      # 试探又失败 → 立刻回 Open
            cs["opened_at_epoch"] = _ts_epoch()
        _save_circuit(root, circuit)

        mark = "🔴" if severity == "escalate" else "⚠️"
        print(f"{mark} [adaptive] {label} 失败 (rc={rc}, {inc.get('error_type')}, severity={severity})"
              f" · 指纹 {inc['signature']} 已记录学习 · stderr尾: {stderr.strip()[-200:]}", file=sys.stderr)
        degraded = allow_degrade
        last = {"ok": False, "degraded": degraded, "exit_code": rc, "action": severity,
                "signature": inc["signature"], "label": label, "stderr_tail": stderr[-500:]}
        return last


def cmd_reset(root: Path, label: str):
    circuit = _load_circuit(root)
    if label == "*":
        circuit = {}
    elif label in circuit:
        circuit[label] = {"state": "closed", "fail_count": 0, "opened_at_epoch": 0}
    _save_circuit(root, circuit)
    print(f"[reset] 熔断器已重置: {label}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="MAPE-K Plan+Execute 脚本自适应执行器")
    ap.add_argument("--label", required=True, help="脚本标识（熔断器按此累计）")
    ap.add_argument("--project-root", default=None, help="runtime 根（默认系统根 REPO_ROOT · 仅测试 override）")
    ap.add_argument("--max-retries", type=int, default=None)
    ap.add_argument("--strict", action="store_true", help="保留兼容参数；失败始终 exit 1")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--reset-circuit", action="store_true", help="重置 --label 的熔断器（* 全部）后退出")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 后接要跑的命令")
    args = ap.parse_args()
    root = Path(args.project_root).resolve() if args.project_root else REPO_ROOT

    if args.reset_circuit:
        return cmd_reset(root, args.label)

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("用法: adaptive_runner.py --label X -- <command...>", file=sys.stderr)
        return 2

    r = run_with_resilience(cmd, args.label, str(root), args.max_retries,
                            allow_degrade=False, timeout=args.timeout)
    print(json.dumps(r, ensure_ascii=False))
    if r["ok"]:
        return 0
    return 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
