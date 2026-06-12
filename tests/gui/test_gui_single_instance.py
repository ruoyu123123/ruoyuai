#!/usr/bin/env python3
"""单实例端口探测 + 关窗保护测试（占用 socket fixture·不开真 nicegui 服务）。

覆盖：free / healthy（既有实例 200+若渝指纹）/ unhealthy（陌生 HTTP / 假死不答）
两态判定 + _graceful_shutdown 运行中调 RUNNER.stop·空闲时不调。

运行：python -m pytest tests/gui/test_gui_single_instance.py -q
"""
import http.server
import socket
import threading

import pytest

from core.gui.single_instance import probe_existing_instance


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_occupied():
    """占用 socket fixture：起可控 body 的 HTTP server（线程·测毕关闭）。"""
    servers = []

    def _start(body: bytes, status: int = 200) -> int:
        class _H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):       # 静音·不污染测试输出
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_free_port_returns_free():
    assert probe_existing_instance(_free_port(), timeout=0.5) == "free"


def test_healthy_existing_instance(http_occupied):
    """既有实例健康态：200 + body 含「若渝」（ui.run title 渲染进 <title>）。"""
    port = http_occupied("<title>若渝AI · 智能写作助手</title>".encode("utf-8"))
    assert probe_existing_instance(port, timeout=1.0) == "healthy"


def test_unhealthy_alien_http(http_occupied):
    """端口被别的程序占用（200 但无指纹）→ unhealthy。"""
    port = http_occupied(b"<title>other app</title>")
    assert probe_existing_instance(port, timeout=1.0) == "unhealthy"


def test_unhealthy_http_error_status(http_occupied):
    """旧实例半死（HTTP 500）→ unhealthy（urlopen HTTPError 归并）。"""
    port = http_occupied("若渝".encode("utf-8"), status=500)
    assert probe_existing_instance(port, timeout=1.0) == "unhealthy"


def test_unhealthy_non_http_listener():
    """占了端口但不答 HTTP（假死/陌生协议）→ timeout 内判 unhealthy 不挂死。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert probe_existing_instance(port, timeout=0.5) == "unhealthy"
    finally:
        srv.close()


# ============ 关窗保护（_graceful_shutdown） ============
def test_graceful_shutdown_requests_stop(monkeypatch):
    """STATE.running=True → 调 RUNNER.stop + 日志含可续跑提示。"""
    import core.gui.app as app_module
    calls = []
    monkeypatch.setattr(app_module.RUNNER, "stop",
                        lambda: calls.append(1) or True)
    app_module.STATE.running = True
    try:
        app_module._graceful_shutdown()
    finally:
        app_module.STATE.running = False
    assert calls == [1]
    lines, _ = app_module.STATE.log_buffer.since(0)
    assert any("体面停止" in ln and "Plan 续跑" in ln for ln in lines)


def test_graceful_shutdown_noop_when_idle(monkeypatch):
    """空闲时不调 stop（不往 plan 状态里写无意义的取消请求）。"""
    import core.gui.app as app_module

    def _boom():
        raise AssertionError("空闲时不应调 RUNNER.stop")
    monkeypatch.setattr(app_module.RUNNER, "stop", _boom)
    app_module.STATE.running = False
    app_module._graceful_shutdown()
