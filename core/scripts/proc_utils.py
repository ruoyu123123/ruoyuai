#!/usr/bin/env python3
"""proc_utils.py — 子进程 UTF-8 单一真理源。

全仓所有起子进程的调用点（scanner fan-out / NN 推理桥 / git / 学习链）统一走
`run_utf8()` / `popen_utf8()` / `utf8_env()`：

* **强制子进程 UTF-8 输出**：`PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`。
  否则在 GBK 控制台、或父进程没有这两个 env 的环境（agent 上下文、Windows 服务）下，
  子进程的 CJK stdout/stderr 按本地编码落字节，被父进程按 utf-8 解码成 mojibake
  （`锟斤拷`）→ 解析函数静默拿到空结果、错误指纹被污染进知识库，流水线却照报「成功」。
* **强制 utf-8 解码**：捕获输出时固定 `encoding="utf-8", errors="replace"`
  （不靠 `text=True` 的 locale 默认编码）。

强制项在 `utf8_env()` 里**最后叠加**，调用方的 env_extra/env 覆盖不掉。
"""
from __future__ import annotations

import os
import subprocess
from typing import Mapping, Optional

# 子进程强制 env —— 任何调用方都不得覆盖。
FORCED_UTF8_ENV: dict[str, str] = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def utf8_env(env_extra: Optional[Mapping[str, str]] = None,
             base: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """构造子进程 env：base（默认继承 os.environ）+ env_extra + 强制 UTF-8 项。

    env_extra 只叠加调用方的业务变量（如 CLUSTER_MODE=1）；FORCED_UTF8_ENV 最后写入，
    保证强制项永远生效。
    """
    env: dict[str, str] = dict(os.environ if base is None else base)
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items() if v is not None})
    env.update(FORCED_UTF8_ENV)
    return env


def run_utf8(cmd, *, env: Optional[Mapping[str, str]] = None,
             env_extra: Optional[Mapping[str, str]] = None,
             capture_output: bool = True, text: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` 的 UTF-8 强制版。

    - env/env_extra → `utf8_env()`（强制项不可覆盖）
    - 捕获输出默认 `capture_output=True, text=True, encoding="utf-8", errors="replace"`
    - `text=False` 时按字节返回（调用方自行 decode）：不注入 encoding/errors，且不显式传
      text（subprocess 默认即字节，等价于历史裸调用形态）
    - 其余 kwargs（timeout / cwd / shell / creationflags / stdout ...）原样透传
    """
    if text:
        kwargs["text"] = True
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, env=utf8_env(env_extra, base=env),
                          capture_output=capture_output, **kwargs)


def popen_utf8(cmd, *, env: Optional[Mapping[str, str]] = None,
               env_extra: Optional[Mapping[str, str]] = None,
               **kwargs) -> subprocess.Popen:
    """`subprocess.Popen` 的 UTF-8 强制版（长驻子进程：daemon 等）。"""
    return subprocess.Popen(cmd, env=utf8_env(env_extra, base=env), **kwargs)
