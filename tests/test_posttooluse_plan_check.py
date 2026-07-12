# -*- coding: utf-8 -*-
"""posttooluse_plan_check exit-0 兜底回归。

CLAUDE.md 明文：PostToolUse hook 严禁 exit 非 0（打断主流水线）。posttooluse_plan_check.py
的入口有外层 try/except 兜底：未捕异常（畸形 stdin / 非 dict payload）也 exit 0，
与 runtime_monitor/step_reflection 同口径。

subprocess 喂各类 payload 验 returncode==0（真实 hook 行为·不 mock）。
"""
import subprocess
import sys
from pathlib import Path

_HOOK = (Path(__file__).resolve().parents[1] / "core" / "claude-home"
         / "hooks" / "posttooluse_plan_check.py")


def _run(stdin_text):
    r = subprocess.run([sys.executable, str(_HOOK)], input=stdin_text,
                       capture_output=True, text=True, timeout=20)
    return r.returncode


def test_malformed_json_exit0():
    """🔴 畸形 JSON stdin → exit 0（不打断流水线）。"""
    assert _run("not json{{{") == 0


def test_empty_stdin_exit0():
    assert _run("") == 0


def test_non_dict_json_exit0():
    """非 dict JSON（list）→ exit 0。"""
    assert _run("[1, 2, 3]") == 0


def test_valid_empty_payload_exit0():
    """合法但无 plan 痕迹的 payload → exit 0。"""
    assert _run('{"tool_name": "Bash", "tool_input": {"command": "ls"}}') == 0


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
