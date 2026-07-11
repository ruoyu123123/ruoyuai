#!/usr/bin/env python3
"""开发环境的子解释器与资源路径解析工具。

`child_python()` 返回当前 Python；目录 helper 返回仓库内的脚本、资源、数据和工作区路径。
`is_frozen()` 在当前运行形态下恒为 False。
"""
from __future__ import annotations

import sys
from pathlib import Path

# dev 仓库根：frozen_util 在 core/scripts/ → parents[2] = 仓库根
_DEV_REPO_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """是否运行在 frozen 二进制里（exe 方向已下线·dev 恒 False·保留供外部模块判定）。"""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """资源/代码树的根目录——派生 core/config、core/scripts、core/claude-home 等相对路径的基准。

    dev-only：返回仓库根（core/scripts 的 parents[2]）。
    """
    return _DEV_REPO_ROOT


def resource_path(*parts: str) -> Path:
    """bundle_root() 下的资源绝对路径（如 resource_path('core','config','x.env')）。"""
    return bundle_root().joinpath(*parts)


def user_data_dir() -> Path:
    """**可写**系统数据的根目录（MAPE-K runtime / model 缓存 / plan attest 等）。

    dev-only：返回仓库根 → `user_data_dir()/core/claude-home/runtime` 等于历史路径。
    """
    return _DEV_REPO_ROOT


def user_workspace_dir() -> Path:
    """用户**创作产物**根目录（小说项目 novels/ + 风格库 styles/）。dev=仓库根/workspace。"""
    return user_data_dir() / "workspace"


def scripts_dir() -> Path:
    """core/scripts 目录（fan-out 脚本据此定位兄弟 scanner 子脚本）。"""
    return bundle_root() / "core" / "scripts"


def child_python() -> str:
    """fan-out 子进程的 python 解释器路径——当前真 python.exe（exe 方向下线后无 frozen 分支）。"""
    return sys.executable
