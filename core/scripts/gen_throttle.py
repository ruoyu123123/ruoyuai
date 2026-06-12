#!/usr/bin/env python3
"""gen_throttle.py — 全局 gen-model 调用限速（限速端点/中转站支持·真 e2e 暴露）。

某些中转站对 gen-model 请求限速（如 < 15 rpm），超速返 Cloudflare 520（Web server returning
unknown error）。完整 cluster-write 快速连发 writer best-of-N/expand 续写 + 多 judge → 超速 520。

frozen exe 单进程 in-process 跑 orchestrator+writer+judge → 都 import 本模块 → 共享同一
module 级 min-interval 闸 → **全局**限速。dev 子进程模式各脚本独立进程（限速器各自计时·限速
没那么必要因调用本就分散）。

env `GEN_MIN_INTERVAL_S`：两次 gen-model 请求最小间隔秒数。默认 0=不限速（零回归）。
  15 rpm 端点设 4.5（→ ~13 rpm 安全余量）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

_lock = threading.Lock()
_last_call = [0.0]


def _min_interval() -> float:
    """env 最优先；env 未设且 frozen（分发 exe）→ 4.5s 兜底（中转站 <15rpm 限速保护）。

    🔴 同类bug狩猎 critical（2026-06-12）：此前只读 env 默认 0——非技术 exe 用户无法设
    环境变量 → 真 e2e 抓出的 520 限速保护在正式分发形态下完全失效。dev/tests 非 frozen
    零回归（默认仍 0）。"""
    raw = os.environ.get("GEN_MIN_INTERVAL_S")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0.0, float(raw))
        except (ValueError, TypeError):
            return 0.0
    try:
        from frozen_util import is_frozen
        if is_frozen():
            return 4.5
    except Exception:
        pass
    return 0.0


def wait() -> None:
    """阻塞到距上次 gen-model 调用 ≥ GEN_MIN_INTERVAL_S（默认 0=不限速·零回归）。

    每个 gen-model HTTP 请求执行前调一次（gen_writer 的 OpenAI/gemini path + llm_transport
    的 OpenAI/gemini path 共 4 处）。线程安全（audit fan-out 虽走线程池但不调 gen-model·锁仅防御）。
    """
    iv = _min_interval()
    if iv <= 0:
        return
    with _lock:
        now = time.monotonic()
        delta = now - _last_call[0]
        if delta < iv:
            sleep_s = iv - delta
            print(f"[gen_throttle] 限速等待 {sleep_s:.1f}s（GEN_MIN_INTERVAL_S={iv}·"
                  f"防中转站 15rpm 超速 520）", file=sys.stderr)
            time.sleep(sleep_s)
        _last_call[0] = time.monotonic()
