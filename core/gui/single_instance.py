#!/usr/bin/env python3
"""single_instance.py — 单实例端口探测（纯 stdlib · 零 nicegui 依赖 · 2026-06-13）

exe 用户双击两次 / 端口被别的程序占用时的数据安全闸：app.main() 在 ui.run 前调
probe_existing_instance() 三态判定——healthy 复用旧窗口·unhealthy 提示先关旧窗口·
free 正常启动。抽成纯函数供 tests/gui 直测（占用 socket fixture·不开真 nicegui 服务）。
"""
from __future__ import annotations

import socket
import urllib.request

#: GET / 响应 body 里认「自己人」的指纹（ui.run(title="若渝AI") → HTML <title>若渝AI</title>）
_FINGERPRINT = "若渝"


def probe_existing_instance(port: int, host: str = "127.0.0.1",
                            timeout: float = 1.0) -> str:
    """三态判定（不抛异常·所有失败路径都归并到三态之一）：

    - "free"      端口空闲（connect 被拒）→ 正常启动
    - "healthy"   已有健康若渝AI 实例（GET / timeout 内 200 且 body 含「若渝」）
                  → 调用方 webbrowser.open 复用旧窗口 + exit(0)
    - "unhealthy" 端口被其他程序占用 / 旧实例假死无响应
                  → 调用方提示「端口被占用·请先关闭旧窗口」+ exit(1)
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return "free"
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/",
                                    timeout=timeout) as r:
            body = r.read(64 * 1024).decode("utf-8", errors="replace")
            if getattr(r, "status", None) == 200 and _FINGERPRINT in body:
                return "healthy"
    except Exception:
        pass  # 超时 / 非 HTTP / 非 200 / 连接重置——都按不健康处理
    return "unhealthy"
