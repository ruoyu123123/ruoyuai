#!/usr/bin/env python3
"""frozen_util.py — frozen exe（PyInstaller onedir）下的子解释器解析（程序驱动 M4）

对抗审查 finding #1（2026-06-10）：orchestrator 顶层脚本调用已 frozen-aware（进程内
importlib），但脚本**内部** fan-out（audit_hub 并行 21 个 scanner / save_state /
gen_writer / gen_fixer / run_cross_cluster_aggregates / evolution_orchestrator …）仍用
`subprocess.run([sys.executable, ...])`。onedir 里 sys.executable = GUI 本体 → 这些内部
调用会重启 GUI 而非跑目标脚本（fix F 同一 bug 深一层）。

统一解决：所有 fan-out 子进程的解释器走 `child_python()`——
- **dev / source 模式**（绝大多数当前用法）：返回 `sys.executable`，行为零变化。
- **frozen onedir**：返回环境变量 `RUOYU_PYTHON` 指向的随包 python 启动器（打包脚本
  bundle 一份精简 python 并 set RUOYU_PYTHON）；未设置则回退 sys.executable 并告警
  （让问题**暴露**而非静默重启 GUI）。

为什么不全改进程内 importlib：audit_hub 的 21 个 scanner 靠进程隔离（任一崩不连累其他
+ ThreadPoolExecutor 并行），改进程内会丢隔离 + 全局状态污染风险。bundle python +
统一解释器解析保留现有 subprocess 架构，是最小风险的根治。

🔴 真 onedir 端到端验证仍需建 exe（dev 无法验 frozen）——本模块在 dev 是 no-op，
frozen 路径靠 RUOYU_PYTHON 注入，二者都可在 dev 用 monkeypatch 测解析逻辑。
"""
from __future__ import annotations

import os
import sys

_warned = False


def is_frozen() -> bool:
    """是否运行在 PyInstaller/cx_Freeze frozen 二进制里。"""
    return bool(getattr(sys, "frozen", False))


def child_python() -> str:
    """返回用于 fan-out 子进程的 python 解释器路径。

    dev → sys.executable（真 python.exe）。
    frozen → RUOYU_PYTHON（随包 python）；缺失则回退 sys.executable + 一次性告警
    （onedir 下 sys.executable 是 GUI 本体，子进程会重启 GUI——暴露不静默）。
    """
    global _warned
    if not is_frozen():
        return sys.executable
    bundled = os.environ.get("RUOYU_PYTHON", "").strip()
    if bundled:
        return bundled
    if not _warned:
        _warned = True
        print("[frozen_util] ⚠️ frozen 模式但未设 RUOYU_PYTHON——fan-out 子进程会重启 "
              "GUI 本体而非跑脚本。打包时须 bundle python 并 set RUOYU_PYTHON。"
              "（详见 PROGRAM_DRIVEN.md M4）", file=sys.stderr)
    return sys.executable
