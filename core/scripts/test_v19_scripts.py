"""test_v19_scripts.py — v19 新增 9 个脚本的最小可行测试（v19.4 新增）"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent
import os as _os
TEST_PROJECT_NAME = _os.environ.get("V19_TEST_PROJECT", "_example_test_book")
TEST_PROJECT = PROJECT_ROOT / "workspace" / "novels" / TEST_PROJECT_NAME
SCRIPTS = PROJECT_ROOT / "core" / "scripts"


def run(script: str, args: list[str]) -> tuple[int, str, str]:
    cmd = ["python", str(SCRIPTS / script)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def main():
    if not TEST_PROJECT.is_dir():
        print(f"[SKIP] 测试项目不存在: {TEST_PROJECT}", file=sys.stderr)
        sys.exit(0)

    tests = [
        ("cross_chapter_pattern_scan.py", [str(TEST_PROJECT)], (0, 1)),
        ("cross_chapter_continuity_scan.py", [str(TEST_PROJECT)], (0, 1)),
        ("cross_chapter_offscreen_scan.py", [str(TEST_PROJECT)], (0, 1)),
        ("cross_chapter_declarative_data_scan.py", [str(TEST_PROJECT)], (0, 1)),
        ("judge_reports_archive.py", [str(TEST_PROJECT), "4"], (0,)),
        ("offscreen_update.py", [str(TEST_PROJECT), "4", "--dry-run"], (0, 1)),
        ("declarative_data_update.py", [str(TEST_PROJECT), "4", "--dry-run"], (0, 1)),
        ("wal_recovery.py", [TEST_PROJECT_NAME], (0, 1)),
        ("maybe_judge_consensus.py", [str(TEST_PROJECT), "4"], (0,)),
    ]

    passed = 0
    failed = 0
    for script, args, allowed in tests:
        code, out, err = run(script, args)
        ok = code in allowed
        flag = "✓" if ok else "✗"
        print(f"  {flag} {script}: exit={code} (expected ∈ {allowed})")
        if not ok and err:
            print(f"      stderr: {err[:150]}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n=== 测试总结：{passed}/{len(tests)} 通过 ===")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
