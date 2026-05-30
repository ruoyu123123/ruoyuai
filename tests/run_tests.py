#!/usr/bin/env python3
"""零依赖测试 runner（2026-05-30 · 第二轮审查发现「110 脚本零测试」元风险后补）。

用法：
  python tests/run_tests.py          # 零依赖（stdlib），CI/本地都能跑
  pytest tests/                       # 装了 pytest 也能直接发现 test_ 函数

只测**确定性纯函数层**（cluster_lookup / splitter 钳位 / gen_writer 解析等），
不碰 LLM、不碰 agent —— 对标业界「把确定性逻辑与 LLM 输出测试分离」共识。
"""
import importlib.util
import sys
import traceback
from pathlib import Path


def main():
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
        print(f"  ✗ {fl}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
