#!/usr/bin/env python3
"""零依赖测试 runner（2026-05-30 · 第二轮审查发现「110 脚本零测试」元风险后补）。

用法：
  python tests/run_tests.py          # 零依赖（stdlib），CI/本地都能跑
  pytest tests/                       # 装了 pytest 也能直接发现 test_ 函数

只测**确定性纯函数层**（cluster_lookup / splitter 钳位 / gen_writer 解析等），
不碰 LLM、不碰 agent —— 对标业界「把确定性逻辑与 LLM 输出测试分离」共识。
"""
import importlib.util
import os
import sys
import traceback
from pathlib import Path

# 🔴 测试态禁 GUI 文件日志（import core.gui.app 触发模块级 STATE=AppState()→_make_log_buffer·
# 本 runner 不在 sys.modules 留 pytest → 否则往 repo/logs 撒文件污染 git·与 pytest bypass 同闸）。
os.environ.setdefault("RUOYUAI_GUI_LOG_DISABLE", "1")


def main():
    # Windows GBK 控制台打印 Unicode 符号会 UnicodeEncodeError 崩 runner（吞失败清单）
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    tests_dir = Path(__file__).resolve().parent
    files = sorted(tests_dir.glob("test_*.py"))
    total = passed = failed = 0
    failures = []
    for f in files:
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"  [IMPORT FAIL] {f.name}: {e}")
            traceback.print_exc()
            failed += 1
            failures.append(f"{f.name}::<import>")
            continue
        for name in sorted(dir(mod)):
            if name.startswith("test_") and callable(getattr(mod, name)):
                total += 1
                try:
                    getattr(mod, name)()
                    passed += 1
                    print(f"  [OK] {f.name}::{name}")
                except Exception as e:
                    failed += 1
                    failures.append(f"{f.name}::{name}")
                    print(f"  [FAIL] {f.name}::{name}: {e}")
                    traceback.print_exc()
    print("=" * 54)
    print(f"测试 {total} · 通过 {passed} · 失败 {failed}")
    for fl in failures:
        print(f"  [X] {fl}")
    # tests/gui 是 pytest 风格（依赖 nicegui fixture）·零依赖循环发现不了——
    # 环境有 pytest+nicegui 就委托跑·没有则跳过（保持本 runner 零依赖承诺）
    gui_dir = tests_dir / "gui"
    if gui_dir.is_dir():
        try:
            import nicegui  # noqa: F401
            import pytest   # noqa: F401
            import subprocess
            print(f"-- 委托 pytest 跑 {gui_dir.name}/ --")
            r = subprocess.run(
                [sys.executable, "-m", "pytest", str(gui_dir), "-q",
                 "--no-header"],
                cwd=str(tests_dir.parent), timeout=600)
            if r.returncode != 0:
                failed += 1
                print("  [X] tests/gui (pytest)")
        except ImportError:
            print("-- 跳过 tests/gui（缺 pytest 或 nicegui）--")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
