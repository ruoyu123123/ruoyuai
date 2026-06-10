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
from pathlib import Path

_warned = False

# dev 仓库根：frozen_util 在 core/scripts/ → parents[2] = 仓库根
_DEV_REPO_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """是否运行在 PyInstaller/cx_Freeze frozen 二进制里。"""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """资源/代码树的根目录——派生 core/config、core/scripts、core/claude-home 等相对路径的基准。

    🔴 对抗审查 FATAL 修复（2026-06-10）：PyInstaller 把 core/scripts 下的模块收成**扁平
    顶层名**（gen_model_loader.__file__ = _internal/gen_model_loader.py），于是源码里
    `Path(__file__).resolve().parent.parent / "config"` 之类的相对推算在 frozen 下全错
    （core/scripts 两级目录消失）。派生项目路径的模块必须改用本函数当基准，而非 __file__。

    - frozen（PyInstaller onedir）：sys._MEIPASS 指向 _internal/，资源经 datas 落在
      _internal/core/config、_internal/core/scripts 等 → bundle_root()/core/config 命中。
    - dev：返回仓库根（core/scripts 的 parents[2]）→ 与现状逐字节一致。
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # frozen 但无 _MEIPASS（极少·非 PyInstaller 冻结器）→ exe 同级兜底
        return Path(sys.executable).resolve().parent
    return _DEV_REPO_ROOT


def resource_path(*parts: str) -> Path:
    """bundle_root() 下的资源绝对路径（如 resource_path('core','config','x.env')）。"""
    return bundle_root().joinpath(*parts)


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
