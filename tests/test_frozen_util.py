#!/usr/bin/env python3
"""frozen_util 的开发环境路径与子解释器一致性测试。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import frozen_util as fu  # noqa: E402


def test_dev_returns_sys_executable():
    assert fu.child_python() == sys.executable
    assert fu.is_frozen() is False


def test_bundle_root_dev_is_repo_root():
    repo = Path(__file__).resolve().parent.parent
    assert fu.bundle_root() == repo
    assert fu.resource_path("core", "config") == repo / "core" / "config"


def test_scripts_dir_dev_is_core_scripts():
    assert fu.scripts_dir() == Path(__file__).resolve().parent.parent / "core" / "scripts"


def test_user_data_dir_dev_is_repo_root():
    """user_data_dir() dev=仓库根（writable 锚点逐字节零回归）。"""
    assert fu.user_data_dir() == Path(__file__).resolve().parent.parent


def test_user_workspace_dir_dev():
    """user_workspace_dir() dev=仓库根/workspace。"""
    assert fu.user_workspace_dir() == \
        Path(__file__).resolve().parent.parent / "workspace"


# ============ fan-out 子进程解释器一致性 guard ============
def test_no_bare_child_interpreter_anywhere_in_scripts():
    """全 core/scripts 扫描：subprocess 启动子脚本绝不裸用 sys.executable 或 "python"
    字面量（必走 child_python·防 PATH 无 python 静默失败）。

    未来任何新加的 fan-out 漏改都会被本测试当场抓住（扫全目录而非写死文件名）。
    白名单：frozen_util.py（child_python 定义处）。
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
            if bare_exec.search(code) or bare_python.search(code):
                offenders.append(f"{p.name}:{i}: {ln.strip()[:70]}")
    assert not offenders, "fan-out 子进程仍裸用解释器（应走 child_python）:\n" + \
        "\n".join(offenders)


def test_wal_recovery_and_maybe_judge_use_child_python():
    """wal_recovery + maybe_judge_consensus 两处 fan-out 站点用 child_python。"""
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    for fname in ["wal_recovery.py", "maybe_judge_consensus.py"]:
        text = (scripts / fname).read_text(encoding="utf-8")
        assert "child_python" in text, f"{fname} 未用 child_python"


def test_adaptive_runner_normalizes_python_interpreter():
    """adaptive_runner 内层 'python' 字面量（来自 plan REMAINDER / auto_heal str cmd）须在
    run_with_resilience 入口归一 child_python——否则整条自学习/演化链静默失效。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
    import adaptive_runner as ar

    # list 形态：内层 python 换 child_python
    out = ar._normalize_interpreter(["python", "core/scripts/Y.py", "--flag"])
    assert out[0] == fu.child_python() and out[1:] == ["core/scripts/Y.py", "--flag"]
    out3 = ar._normalize_interpreter(["python3", "x.py"])
    assert out3[0] == fu.child_python()
    # str 形态（auto_heal）：首 token 换（带引号保护空格路径）
    s = ar._normalize_interpreter("python core/scripts/X.py --auto-migrate")
    assert s.startswith(f'"{fu.child_python()}" ') and "core/scripts/X.py" in s
    # 非 python 首位（如直接脚本路径）不动
    assert ar._normalize_interpreter(["core/scripts/Z.py"]) == ["core/scripts/Z.py"]
    assert ar._normalize_interpreter("echo hi") == "echo hi"


def test_no_bare_python_literal_in_any_run_with_resilience_caller():
    """守卫收口：adaptive_runner 已 import child_python + 入口确实调归一。"""
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    ar_text = (scripts / "adaptive_runner.py").read_text(encoding="utf-8")
    assert "child_python" in ar_text
    assert "_normalize_interpreter(cmd)" in ar_text  # 入口确实调归一


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
