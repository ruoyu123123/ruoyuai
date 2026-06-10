#!/usr/bin/env python3
"""frozen_util.child_python() 解析逻辑测试（对抗审查 finding #1·M4 frozen fan-out）。

dev=no-op(sys.executable) / frozen+RUOYU_PYTHON=bundled / frozen 缺 env=回退+告警。
真 onedir 端到端需建 exe 验，本测只锁解析逻辑（monkeypatch frozen + env）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import frozen_util as fu  # noqa: E402


def test_dev_returns_sys_executable():
    saved = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        assert fu.child_python() == sys.executable
        assert fu.is_frozen() is False
    finally:
        sys.frozen = saved


def test_frozen_with_ruoyu_python_uses_bundled():
    saved = getattr(sys, "frozen", False)
    saved_env = os.environ.get("RUOYU_PYTHON")
    sys.frozen = True
    os.environ["RUOYU_PYTHON"] = r"C:\app\_internal\python.exe"
    try:
        assert fu.child_python() == r"C:\app\_internal\python.exe"
        assert fu.is_frozen() is True
    finally:
        sys.frozen = saved
        if saved_env is None:
            os.environ.pop("RUOYU_PYTHON", None)
        else:
            os.environ["RUOYU_PYTHON"] = saved_env


def test_frozen_without_env_falls_back_and_warns():
    saved = getattr(sys, "frozen", False)
    saved_env = os.environ.get("RUOYU_PYTHON")
    saved_warned = fu._warned
    sys.frozen = True
    os.environ.pop("RUOYU_PYTHON", None)
    fu._warned = False
    try:
        # 缺 env → 回退 sys.executable（暴露不静默·下一次调用不重复告警）
        assert fu.child_python() == sys.executable
        assert fu._warned is True
    finally:
        sys.frozen = saved
        fu._warned = saved_warned
        if saved_env is not None:
            os.environ["RUOYU_PYTHON"] = saved_env


def test_fanout_files_use_child_python_not_raw_executable():
    """fan-out 文件已不再裸用 sys.executable（全走 child_python·防 frozen 重启 GUI）。"""
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    for fname in ["audit_hub.py", "save_state.py", "gen_writer.py", "gen_fixer.py",
                  "run_cross_cluster_aggregates.py", "save_state_updates.py",
                  "save_state_evaluators.py", "evolution_orchestrator.py"]:
        text = (scripts / fname).read_text(encoding="utf-8")
        assert "from frozen_util import child_python" in text, f"{fname} 缺 import"
        # subprocess argv 里不应再有裸 sys.executable（注释/docstring 除外的代码行）
        for ln in text.splitlines():
            stripped = ln.strip()
            if stripped.startswith("#") or "sys.executable" not in ln:
                continue
            assert False, f"{fname} 仍有裸 sys.executable: {ln.strip()[:80]}"


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
