#!/usr/bin/env python3
"""frozen_util.child_python() 解析逻辑测试（对抗审查 finding #1·M4 frozen fan-out）。

dev=no-op(sys.executable) / frozen+RUOYU_PYTHON=bundled / frozen 缺 env=回退+告警。
真 onedir 端到端需建 exe 验，本测只锁解析逻辑（monkeypatch frozen + env）。
"""
import os
import re
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


def test_no_bare_child_interpreter_anywhere_in_scripts():
    """全 core/scripts 扫描：subprocess 启动子脚本绝不裸用 sys.executable 或 "python"
    字面量（必走 child_python·防 frozen 重启 GUI / PATH 无 python 静默失败）。

    这是 finding #1「comprehensive 根治」的守卫——未来任何新加的 fan-out 漏改都会被
    本测试当场抓住（reviewer 建议：扫全目录而非写死文件名）。
    白名单：frozen_util.py（定义处）、orchestrator.py（docstring 注释 + in-process 分支
    保留 sys.executable 引用做对照说明）。
    """
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    bare_exec = re.compile(r"\[\s*sys\.executable\b")
    bare_python = re.compile(r"\[\s*[\"']python[3]?[\"']\s*,")
    offenders = []
    for p in sorted(scripts.glob("*.py")):
        if p.name == "frozen_util.py":
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = ln.split("#", 1)[0]  # 去行尾注释
            if p.name == "orchestrator.py" and "child_python()" in ln:
                continue  # orchestrator 的 full=[child_python()]+tokens 已正确
            if bare_exec.search(code) or bare_python.search(code):
                offenders.append(f"{p.name}:{i}: {ln.strip()[:70]}")
    assert not offenders, "fan-out 子进程仍裸用解释器（应走 child_python）:\n" + \
        "\n".join(offenders)


def test_wal_recovery_and_maybe_judge_use_child_python():
    """finding #1 第二轮补漏：wal_recovery + maybe_judge_consensus 两处 "python" 字面量
    已改 child_python（这俩 pre-existing fan-out 站点在首轮被漏）。"""
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    for fname in ["wal_recovery.py", "maybe_judge_consensus.py"]:
        text = (scripts / fname).read_text(encoding="utf-8")
        assert "child_python" in text, f"{fname} 未用 child_python"


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
