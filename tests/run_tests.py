#!/usr/bin/env python3
"""零依赖测试 runner（2026-05-30 · 第二轮审查发现「110 脚本零测试」元风险后补）。

用法：
  python tests/run_tests.py          # 零依赖（stdlib），CI/本地都能跑
  pytest tests/                       # 装了 pytest 也能直接发现 test_ 函数

只测**确定性纯函数层**（cluster_lookup / splitter 钳位 / gen_writer 解析等），
不碰 LLM、不碰 agent —— 对标业界「把确定性逻辑与 LLM 输出测试分离」共识。
"""
import importlib.util
import inspect
import os
import sys
import traceback
from pathlib import Path

_NN_GATES = (
    "RUOYU_NN_SURPRISAL", "RUOYU_NN_COHERENCE", "RUOYU_NN_VAD",
    "RUOYU_NN_COREF", "RUOYU_CHARACTER_NETWORK",
    "RUOYU_FEATURE_STORE", "RUOYU_DATA_FLYWHEEL", "RUOYU_MODEL_REGISTRY",
)


def _run_with_gate_isolation(fn):
    """对齐 pytest conftest autouse：每个无参测试前清空 NN/可成长门控，结束后恢复。"""
    saved = {g: os.environ.get(g) for g in _NN_GATES}
    for g in _NN_GATES:
        os.environ.pop(g, None)
    try:
        return fn()
    finally:
        for g, v in saved.items():
            if v is None:
                os.environ.pop(g, None)
            else:
                os.environ[g] = v


def _needs_pytest_fixture(fn) -> bool:
    """零依赖 runner 只会无参调用；必需参数视为 pytest fixture 用例并跳过。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def main():
    # Windows GBK 控制台打印 Unicode 符号会 UnicodeEncodeError 崩 runner（吞失败清单）
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    tests_dir = Path(__file__).resolve().parent
    files = sorted(tests_dir.glob("test_*.py"))
    total = passed = failed = skipped = 0
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
                fn = getattr(mod, name)
                if _needs_pytest_fixture(fn):
                    skipped += 1
                    print(f"  [SKIP] {f.name}::{name}（需 pytest fixture）")
                    continue
                total += 1
                try:
                    _run_with_gate_isolation(fn)
                    passed += 1
                    print(f"  [OK] {f.name}::{name}")
                except Exception as e:
                    failed += 1
                    failures.append(f"{f.name}::{name}")
                    print(f"  [FAIL] {f.name}::{name}: {e}")
                    traceback.print_exc()
    # 🔴 2026-06-20 GUI 删档：原 tests/gui pytest 委托段一并删除。
    print("=" * 54)
    print(f"测试 {total} · 通过 {passed} · 失败 {failed} · 跳过 {skipped}")
    for fl in failures:
        print(f"  [X] {fl}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
