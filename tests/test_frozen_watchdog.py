#!/usr/bin/env python3
"""test_frozen_watchdog.py — run_script_in_process 步级 watchdog 测试（2026-06-13）

覆盖：① 正常脚本 rc 透传不受子线程化影响 ② sleep 超阈值（env monkeypatch 到 1s）
→ rc=124 + [watchdog] 日志 + argv 还原 ③ SystemExit(2) 透传 rc=2
④ main 内异常仍走外层 except → rc=3（语义与原 inline 调用一致）。

纯 mock：临时脚本落 tempfile 沙箱 + sys.path 注入，不打真 API、不碰真 plans 目录。
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402


def _make_script(tmpdir: Path, name: str, body: str) -> Path:
    p = tmpdir / f"{name}.py"
    p.write_text(body, encoding="utf-8")
    return p


def _run_temp(tmpdir: str, script: Path, mod_name: str) -> int:
    """临时目录入 sys.path → 进程内跑 → 清 path/模块缓存（防测试间污染）。"""
    sys.path.insert(0, tmpdir)
    try:
        return orc.run_script_in_process([str(script)], repo_root=_ROOT, label="t")
    finally:
        sys.path.remove(tmpdir)
        sys.modules.pop(mod_name, None)


def test_normal_rc_passthrough():
    """① 正常脚本 rc 透传：main 返回 None→0 / 返回 7→7（子线程化不改返回语义）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s0 = _make_script(tmp, "wd_ok_none", "def main():\n    return None\n")
        s7 = _make_script(tmp, "wd_ok_seven", "def main():\n    return 7\n")
        assert _run_temp(td, s0, "wd_ok_none") == 0
        assert _run_temp(td, s7, "wd_ok_seven") == 7


def test_timeout_returns_124_and_logs():
    """② sleep 超阈值（RUOYUAI_SCRIPT_TIMEOUT_S=1）→ rc=124 + [watchdog] 日志
    （含「产物可能不完整」续跑提示）+ argv 还原。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s = _make_script(tmp, "wd_sleeper",
                         "import time\ndef main():\n    time.sleep(8)\n    return 0\n")
        saved_env = os.environ.get("RUOYUAI_SCRIPT_TIMEOUT_S")
        saved_argv = list(sys.argv)
        saved_err = sys.stderr
        os.environ["RUOYUAI_SCRIPT_TIMEOUT_S"] = "1"
        buf = io.StringIO()
        sys.stderr = buf
        t0 = time.monotonic()
        try:
            rc = _run_temp(td, s, "wd_sleeper")
        finally:
            sys.stderr = saved_err
            if saved_env is None:
                os.environ.pop("RUOYUAI_SCRIPT_TIMEOUT_S", None)
            else:
                os.environ["RUOYUAI_SCRIPT_TIMEOUT_S"] = saved_env
        elapsed = time.monotonic() - t0
        assert rc == 124, f"超时应返回 124，实际 {rc}"
        assert elapsed < 6, f"watchdog 未生效（耗时 {elapsed:.1f}s·应 ~1s 返回）"
        out = buf.getvalue()
        assert "[watchdog]" in out and "wd_sleeper" in out, f"超时日志缺失: {out}"
        assert "可能不完整" in out and "续跑" in out, f"超时日志缺续跑/产物提示: {out}"
        assert sys.argv == saved_argv, "超时返回后 argv 必还原（不污染后续步）"
        # 弃置的 sleeper 线程仍存活（daemon 泄漏是已知代价·docstring 已注明）——
        # 等它睡完再退 with 块，防 Windows 上 TemporaryDirectory 清理与其竞态。
        import threading as _t
        for th in _t.enumerate():
            if th.name == "inproc-wd_sleeper":
                th.join(12)


def test_systemexit_passthrough():
    """③ SystemExit(2) 透传 rc=2（子线程内接住 e.code·与原 inline 语义一致）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s = _make_script(tmp, "wd_exit2", "import sys\ndef main():\n    sys.exit(2)\n")
        assert _run_temp(td, s, "wd_exit2") == 2
        # sys.exit() 无参 → code None → 0（原语义边界）
        s0 = _make_script(tmp, "wd_exit_none",
                          "import sys\ndef main():\n    sys.exit()\n")
        assert _run_temp(td, s0, "wd_exit_none") == 0


def test_exception_in_main_returns_3():
    """④ main 内异常带回调用线程重抛 → 外层 except Exception → rc=3（原语义）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s = _make_script(tmp, "wd_boom",
                         "def main():\n    raise ValueError('boom')\n")
        saved_err = sys.stderr
        buf = io.StringIO()
        sys.stderr = buf
        try:
            rc = _run_temp(td, s, "wd_boom")
        finally:
            sys.stderr = saved_err
        assert rc == 3, f"main 内异常应返回 3，实际 {rc}"
        assert "ValueError" in buf.getvalue(), "异常 traceback 应打到 stderr 日志"


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
