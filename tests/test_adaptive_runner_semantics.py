#!/usr/bin/env python3
"""adaptive_runner MAPE-K 语义锁测试（P2 工程债批次1 · 2026-06-12）

缺漏报告结论：adaptive_runner 是 save-state 主链取代 `|| true` 的韧性执行器，
但 run_with_resilience 的核心语义（ok 路径 / transient retry / 失败默认 fail-fast /
stderr Traceback 不信 exit code / 熔断三态 / list+str 双形态命令）此前零测试锁定
——任何回归都会让「失败被记录学习且阻断」退化回静默吞错。

全部用假命令（sys.executable -c ...）+ tmp project_root 隔离 runtime/
（incidents.jsonl / circuit_state.json 落 tmp，不污染系统 core/claude-home/runtime/）。
retry 测试 monkeypatch time.sleep 免真等指数退避。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import adaptive_runner as ar  # noqa: E402

PY = sys.executable  # 真解释器·不依赖 PATH 上的 python 字面量


def _runtime(tmp) -> Path:
    return Path(tmp) / "core" / "claude-home" / "runtime"


class _NoSleep:
    """monkeypatch ar.time.sleep：记录退避秒数·不真等。"""

    def __init__(self):
        self.calls = []
        self._saved = None

    def __enter__(self):
        self._saved = ar.time.sleep
        ar.time.sleep = lambda s: self.calls.append(s)
        return self

    def __exit__(self, *a):
        ar.time.sleep = self._saved


# ============ 1) ok 路径 ============
def test_ok_path_no_incident_no_degrade():
    """成功命令：ok=True / degraded=False / attempt=1 / 不写 incident。"""
    with tempfile.TemporaryDirectory() as tmp:
        r = ar.run_with_resilience([PY, "-c", "print('ok')"],
                                   label="ok_case", project_root=tmp)
        assert r["ok"] is True and r["degraded"] is False
        assert r["exit_code"] == 0 and r["attempt"] == 1
        assert not (_runtime(tmp) / "incidents.jsonl").exists()  # 成功零记录


# ============ 2) 失败默认 fail-fast（取代 || true 且必须记录学习） ============
def test_fail_default_blocks_records_incident_and_circuit():
    """崩溃命令：ok=False 且默认 degraded=False 阻断（北极星：失败必记录不静默）。
    incidents.jsonl 落 tmp（指纹含 error_type）+ circuit fail_count 累计。"""
    with tempfile.TemporaryDirectory() as tmp:
        r = ar.run_with_resilience(
            [PY, "-c", "raise ValueError('boom')"],
            label="fail_case", project_root=tmp, max_retries=0)
        assert r["ok"] is False and r["degraded"] is False
        assert r["exit_code"] == 1 and r["signature"]
        # incident 已落盘（Analyze 层喂 self_heal_engine 的学习原料）
        lines = (_runtime(tmp) / "incidents.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        inc = json.loads(lines[-1])
        assert inc["error_type"] == "ValueError"
        assert inc["via"] == "adaptive_runner"
        # 熔断计数 +1（未达阈值仍 closed）
        cs = json.loads((_runtime(tmp) / "circuit_state.json")
                        .read_text(encoding="utf-8"))
        assert cs["fail_case"]["fail_count"] == 1
        assert cs["fail_case"]["state"] == "closed"
        # allow_degrade 是内部遗留参数；主链 CLI 不使用它。
        r2 = ar.run_with_resilience(
            [PY, "-c", "raise ValueError('boom')"],
            label="fail_case_strict", project_root=tmp,
            max_retries=0, allow_degrade=False)
        assert r2["ok"] is False and r2["degraded"] is False


# ============ 3) transient → 指数退避重试 ============
def test_transient_connection_error_retries_with_backoff():
    """ConnectionError 强制 severity=retry：max_retries=2 → 真跑 3 次·退避 1s/2s。"""
    with tempfile.TemporaryDirectory() as tmp:
        cnt = Path(tmp) / "attempts.txt"
        code = f"open(r'{cnt}','a').write('x'); raise ConnectionError('flaky')"
        with _NoSleep() as ns:
            r = ar.run_with_resilience([PY, "-c", code], label="retry_case",
                                       project_root=tmp, max_retries=2)
        assert r["ok"] is False
        assert len(cnt.read_text()) == 3, "1 次原跑 + 2 次重试 = 3 次执行"
        assert ns.calls == [1, 2], f"指数退避应为 [1, 2]·实际 {ns.calls}"


def test_transient_retry_then_success():
    """第 1 次 ConnectionError·第 2 次成功 → ok=True / attempt=2（重试真能救活）。"""
    with tempfile.TemporaryDirectory() as tmp:
        cnt = Path(tmp) / "n.txt"
        code = ("import pathlib\n"
                f"p = pathlib.Path(r'{cnt}')\n"
                "n = int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n + 1))\n"
                "if n == 0:\n"
                "    raise ConnectionError('flaky once')\n")
        with _NoSleep():
            r = ar.run_with_resilience([PY, "-c", code], label="retry_ok",
                                       project_root=tmp, max_retries=2)
        assert r["ok"] is True and r["attempt"] == 2


# ============ 4) Monitor 纪律：查 stderr 不信 exit code ============
def test_exit0_with_stderr_traceback_counts_as_crash():
    """exit 0 但 stderr 有真 Traceback → 仍判 crashed（memory
    feedback_verify_stderr_not_exitcode：别信 exit code）。"""
    with tempfile.TemporaryDirectory() as tmp:
        code = ("import sys\n"
                "sys.stderr.write('Traceback (most recent call last):\\n"
                "  File \"x.py\", line 9, in <module>\\nValueError: fake\\n')\n")
        with _NoSleep():
            r = ar.run_with_resilience([PY, "-c", code], label="stderr_crash",
                                       project_root=tmp, max_retries=0)
        assert r["exit_code"] == 0
        assert r["ok"] is False, "exit 0 + stderr Traceback 必须判失败"
        assert (_runtime(tmp) / "incidents.jsonl").exists()


# ============ 5) 熔断三态：阈值 Open → 跳过执行 → reset 恢复 ============
def test_circuit_opens_at_threshold_blocks_then_reset():
    """同 label 连续失败 CIRCUIT_THRESHOLD 次 → Open；下一次调用不执行子进程直接
    失败返回 circuit=open / exit_code=None；cmd_reset 后恢复可跑。"""
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "ran.txt"
        fail_code = f"open(r'{marker}','a').write('x'); raise RuntimeError('x')"
        with _NoSleep():
            for _ in range(ar.CIRCUIT_THRESHOLD):
                ar.run_with_resilience([PY, "-c", fail_code], label="cb_case",
                                       project_root=tmp, max_retries=0)
        cs = json.loads((_runtime(tmp) / "circuit_state.json")
                        .read_text(encoding="utf-8"))
        assert cs["cb_case"]["state"] == "open"
        ran_before = len(marker.read_text())
        # Open 态：直接拒执行（盲跑防护）
        r = ar.run_with_resilience([PY, "-c", fail_code], label="cb_case",
                                   project_root=tmp, max_retries=0)
        assert r.get("circuit") == "open" and r["exit_code"] is None
        assert r["ok"] is False and r["degraded"] is False
        assert len(marker.read_text()) == ran_before, "Open 态不得真跑子进程"
        # 人工 reset → closed → 成功跑通并保持 closed
        ar.cmd_reset(Path(tmp), "cb_case")
        r2 = ar.run_with_resilience([PY, "-c", "print('back')"], label="cb_case",
                                    project_root=tmp)
        assert r2["ok"] is True
        cs2 = json.loads((_runtime(tmp) / "circuit_state.json")
                         .read_text(encoding="utf-8"))
        assert cs2["cb_case"]["state"] == "closed"


# ============ 6) list / str 双形态命令 + 解释器归一 ============
def test_normalize_interpreter_list_and_str_forms():
    """plan 行 `adaptive_runner -- python core/scripts/X.py` 的内层 'python' 字面量
    必须归一成 child_python()（frozen onedir 无 PATH python·M4 finding）。"""
    cp = ar.child_python()
    # list 形态：首位 python → child_python()
    out = ar._normalize_interpreter(["python", "-c", "pass"])
    assert out == [cp, "-c", "pass"]
    # str 形态：前缀替换（带引号包裹·非简单 prepend）
    s = ar._normalize_interpreter("python core/scripts/x.py --a 1")
    assert s == f'"{cp}" core/scripts/x.py --a 1'
    # 非 python 开头不动
    assert ar._normalize_interpreter("echo hi") == "echo hi"
    assert ar._normalize_interpreter(["git", "status"]) == ["git", "status"]


def test_str_shell_command_form_runs_ok():
    """str 形态（shell=True 路径·plan scripts 行原样喂入）真跑通 ok 路径。"""
    with tempfile.TemporaryDirectory() as tmp:
        r = ar.run_with_resilience('python -c "import sys; sys.exit(0)"',
                                   label="str_form", project_root=tmp)
        assert r["ok"] is True and r["exit_code"] == 0


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
