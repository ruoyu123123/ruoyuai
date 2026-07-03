# -*- coding: utf-8 -*-
# 🔴 2026-07-03 Wave-5 常驻推理 daemon + 客户端回归
"""test_nn_daemon.py — model_daemon.py（venv 侧·懒加载设计）+ nn_daemon_client.py（系统 py 侧）回归。

覆盖：
  · 懒加载回归锁：daemon 模块在系统 py（无 torch）也能安全 import。
  · 协议层（`model_daemon.dispatch_request`，纯函数无 socket）：鉴权 / 未知 task / items 非
    list / task 内部异常隔离（不拖垮其它 task）/ health payload。
  · 客户端 ↔ daemon 全链路 roundtrip（`urllib.request.urlopen` 换成路由到 dispatch_request 的假
    实现·非 2xx 按真实 urllib 语义抛 HTTPError）：正确 token 通过 / 错 token 403 / 条数不齐 /
    daemon down / stale pid 清理。
  · 客户端零网络：RUOYU_NN_DAEMON 默认 off 时 infer()/ensure_daemon() 不发起任何请求。
  · 发现文件：写入/读取/陈旧 pid 清理。
  · idle 自退：可注入回调·阈值到点触发·活跃时不触发。

🔴 为什么不起真实 socket：tests/test_gen_model.py 在模块导入时把 `urllib.request.urlopen` 和
`socket.socket.connect` 永久 monkeypatch 成"调用即 raise"（session 全程生效的网络兜底安全闸·
防任何测试意外出网真花钱），这是本仓既有的、刻意不可撤销的安全设计。真实 ThreadingHTTPServer
方案在单文件跑时不受影响，但在 `pytest tests/`（收集到 test_gen_model.py）时会撞上这道闸——
client 侧的 urlopen 调用会被拦成 AssertionError。修法是彻底不依赖真实 socket：daemon 协议
逻辑本身是纯函数（`dispatch_request`），client 侧测试改用假 urlopen 直接路由给这个纯函数，
两者都不经过真实 OS socket，天然对这道全局闸免疫，同时仍然端到端验证真实的协议/契约逻辑
（鉴权/路由/task 分发/请求构造/响应解析全部真跑，只是传输层不落地到 socket）。
真机字面意义上的 HTTP + 子进程验证已单独人工跑通（daemon 真实 spawn + vad→coherence→vad→
style_embed→nli→health→shutdown 全链路 + model.py 撞名陷阱验证，见交付报告）。

确定性·零 torch·零真实模型加载（task handler 全部用假函数替换）。
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "core" / "scripts"), str(_ROOT / "core" / "ml" / "daemon")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import model_daemon as daemon_mod   # noqa: E402  懒加载设计回归锁：系统 py（无 torch）也能 import
import nn_daemon_client as client   # noqa: E402


# ════════════════════════════ 懒加载回归锁 ════════════════════════════

def test_daemon_module_importable_without_torch():
    """import 本身成功（本测试进程即系统 py·无 torch）即证明模块顶层零 torch import。"""
    assert hasattr(daemon_mod, "serve_forever")
    assert hasattr(daemon_mod, "IdleWatchdog")
    assert hasattr(daemon_mod, "dispatch_request")
    assert set(daemon_mod._TASK_HANDLERS) == {"style_embed", "vad", "coherence", "surprisal", "nli"}


def test_client_module_importable_and_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_DAEMON", raising=False)
    assert client.enabled() is False


# ════════════════════════════ 小工具函数单测 ════════════════════════════

def test_pop_generic_model_module_removes_cache_key():
    sys.modules["model"] = object()  # 模拟某 task 加载后留下的同名缓存（vad_infer/coherence_infer 陷阱）
    daemon_mod._pop_generic_model_module()
    assert "model" not in sys.modules


def test_resolve_ckpt_env_override_and_default(tmp_path, monkeypatch):
    env_name = "RUOYU_TEST_CKPT_XYZ"
    monkeypatch.delenv(env_name, raising=False)
    default_dir = tmp_path / "default_ckpt"
    default_dir.mkdir()
    assert daemon_mod._resolve_ckpt(env_name, default_dir) == str(default_dir)

    env_dir = tmp_path / "env_ckpt"
    env_dir.mkdir()
    monkeypatch.setenv(env_name, str(env_dir))
    assert daemon_mod._resolve_ckpt(env_name, default_dir) == str(env_dir)

    monkeypatch.setenv(env_name, str(tmp_path / "nonexistent"))
    assert daemon_mod._resolve_ckpt(env_name, default_dir) == str(default_dir)  # env 指向不存在路径 → 退默认

    monkeypatch.delenv(env_name, raising=False)
    assert daemon_mod._resolve_ckpt(env_name, tmp_path / "also_missing") is None


def test_resolve_style_model_dir_raises_when_missing(monkeypatch):
    monkeypatch.delenv("RUOYU_STYLE_MODEL", raising=False)
    monkeypatch.setattr(daemon_mod, "_STYLE_MODEL_DIRS",
                        {**daemon_mod._STYLE_MODEL_DIRS, "author": Path("Z:/does_not_exist_xyz")})
    with pytest.raises(FileNotFoundError):
        daemon_mod._resolve_style_model_dir("author")


@pytest.mark.parametrize("item,expected", [
    ({"text_a": "a", "text_b": "b"}, True),
    ({"text_a": "a"}, True),
    ({"text_b": "b"}, True),
    ("plain string", False),
    ({"other": "x"}, False),
])
def test_is_pair_item_detection(item, expected):
    assert daemon_mod._is_pair_item(item) is expected


@pytest.mark.parametrize("pid,expected", [(0, False), (-1, False), (999_999, False)])
def test_pid_alive_bogus_false(pid, expected):
    assert client._pid_alive(pid) is expected


def test_pid_alive_self_true():
    assert client._pid_alive(os.getpid()) is True


# ════════════════════════════ 发现文件：写入/读取/陈旧清理 ════════════════════════════

def test_write_and_read_discovery_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(tmp_path / "wr"))
    daemon_mod._write_discovery(12345, "abc", 999)
    info = client._read_discovery()
    assert info["port"] == 12345
    assert info["token"] == "abc"
    assert info["pid"] == 999
    assert isinstance(info["started_at"], float)


def test_daemon_available_false_when_no_discovery_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(tmp_path / "empty"))
    assert client.daemon_available() is False


def test_daemon_available_clears_stale_discovery_when_pid_dead(tmp_path, monkeypatch):
    d = tmp_path / "stale"
    d.mkdir()
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(d))
    disc = d / "daemon.json"
    disc.write_text(json.dumps({"port": 59999, "token": "x", "pid": 999_999}), encoding="utf-8")

    def _refused(*_a, **_k):
        raise urllib.error.URLError(ConnectionRefusedError("nothing listening"))
    monkeypatch.setattr(urllib.request, "urlopen", _refused)

    assert client.daemon_available() is False
    assert not disc.exists()  # 死 pid → 清理陈旧发现文件


# ════════════════════════════ 门控 off：零网络动作 ════════════════════════════

def test_disabled_by_default_zero_network(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_DAEMON", raising=False)

    def _boom(*a, **k):
        raise AssertionError("RUOYU_NN_DAEMON 未开启时不应发起任何网络请求")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    assert client.ensure_daemon() is False
    assert client.infer("vad", ["文本"]) is None


def test_infer_empty_items_short_circuits(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")

    def _boom(*a, **k):
        raise AssertionError("空 items 不该发网络请求")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert client.infer("vad", []) == []


def test_infer_daemon_down_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(tmp_path / "no_daemon_here"))
    monkeypatch.setattr(client, "_venv_python", lambda: None)  # 无 venv → 不尝试拉起
    assert client.infer("vad", ["文本"]) is None
    assert client.ensure_daemon() is False


# ════════════════════════════ 协议层：直接函数调用（dispatch_request 无 socket） ════════════════════════════

def _fake_vad_handler(items, model=None):
    return [{"valence": 0.5, "arousal": 0.5, "dominance": None, "source": "model", "echo": t}
            for t in items]


@pytest.fixture
def daemon_state(monkeypatch):
    """只建 `daemon_mod._STATE`（鉴权 token + 推理锁），不碰任何 socket/HTTP。"""
    token = "tok-" + secrets.token_hex(8)
    monkeypatch.setattr(daemon_mod, "_STATE", daemon_mod._DaemonState(token))
    return token


def test_dispatch_health_requires_token(daemon_state):
    status, payload = daemon_mod.dispatch_request("GET", "/health", {}, b"")
    assert status == 403
    assert payload["ok"] is False


def test_dispatch_health_ok_with_token(daemon_state):
    status, payload = daemon_mod.dispatch_request(
        "GET", "/health", {"X-Ruoyu-Token": daemon_state}, b"")
    assert status == 200
    assert payload["ok"] is True
    assert isinstance(payload["models_loaded"], list)
    assert payload["uptime_sec"] >= 0


def test_dispatch_infer_wrong_token_403(daemon_state, monkeypatch):
    monkeypatch.setitem(daemon_mod._TASK_HANDLERS, "vad", _fake_vad_handler)
    body = json.dumps({"task": "vad", "items": ["x"]}).encode("utf-8")
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": "definitely-wrong"}, body)
    assert status == 403
    assert payload["ok"] is False


def test_dispatch_rejects_unknown_task(daemon_state):
    body = json.dumps({"task": "not_a_real_task", "items": ["x"]}).encode("utf-8")
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, body)
    assert status == 400
    assert payload["ok"] is False
    assert "unknown_task" in payload["error"]


def test_dispatch_rejects_non_list_items(daemon_state):
    body = json.dumps({"task": "vad", "items": "not-a-list"}).encode("utf-8")
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, body)
    assert status == 400
    assert "items_must_be_list" in payload["error"]


def test_dispatch_infer_empty_items_returns_empty_results(daemon_state):
    body = json.dumps({"task": "vad", "items": []}).encode("utf-8")
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, body)
    assert status == 200
    assert payload == {"ok": True, "results": []}


def test_dispatch_invalid_json_body(daemon_state):
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, b"{not valid json")
    assert status == 400
    assert "invalid_json" in payload["error"]


def test_dispatch_task_exception_isolated(daemon_state, monkeypatch):
    """一个 task 加载/推理异常 → ok:false + error·daemon 本身仍存活·换个正常 task 照常响应。"""
    def _boom(items, model=None):
        raise RuntimeError("simulated model load failure")
    monkeypatch.setitem(daemon_mod._TASK_HANDLERS, "vad", _boom)
    body = json.dumps({"task": "vad", "items": ["x"]}).encode("utf-8")
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, body)
    assert status == 200
    assert payload["ok"] is False
    assert "RuntimeError" in payload["error"]

    monkeypatch.setitem(
        daemon_mod._TASK_HANDLERS, "coherence",
        lambda items, model=None: [{"coherence_score": 0.9, "is_coherent": True, "source": "model"}])
    body2 = json.dumps({"task": "coherence", "items": ["y"]}).encode("utf-8")
    status2, payload2 = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, body2)
    assert status2 == 200
    assert payload2["ok"] is True
    assert payload2["results"][0]["is_coherent"] is True


def test_dispatch_infer_roundtrip_with_model_param(daemon_state, monkeypatch):
    captured = {}

    def _fake_style(items, model=None):
        captured["model"] = model
        return [{"embedding": [0.1, 0.2, 0.3]} for _ in items]
    monkeypatch.setitem(daemon_mod._TASK_HANDLERS, "style_embed", _fake_style)
    body = json.dumps({"task": "style_embed", "items": ["文本"], "model": "character"}).encode("utf-8")
    status, payload = daemon_mod.dispatch_request(
        "POST", "/infer", {"X-Ruoyu-Token": daemon_state}, body)
    assert status == 200
    assert payload["results"] == [{"embedding": [0.1, 0.2, 0.3]}]
    assert captured["model"] == "character"


# ════════════════════════════ 客户端 ↔ daemon 全链路（假 urlopen 路由到 dispatch_request） ════════════════════════════

class _FakeHTTPResponse:
    """伪造 urllib 响应对象：.status / .read() / 上下文管理器协议，对齐真 http.client.HTTPResponse 用法。"""

    def __init__(self, status: int, payload: dict):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _dispatch_urlopen(req, timeout=None):
    """假 urlopen：解析 Request → 交给 daemon_mod.dispatch_request() 处理 → 按真实 urllib
    语义把非 2xx 状态转成 HTTPError（client._post 靠捕获 HTTPError 识别失败）。不碰真实 socket。"""
    parsed = urllib.parse.urlsplit(req.full_url)
    headers = dict(req.header_items())
    status, payload = daemon_mod.dispatch_request(req.get_method(), parsed.path, headers, req.data or b"")
    if 200 <= status < 300:
        return _FakeHTTPResponse(status, payload)
    raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(json.dumps(payload).encode()))


def _ok_urlopen(req, timeout=None):
    """假 urlopen：任何请求都回 200 {"ok": true}（供 shutdown/cli_stop 客户端逻辑单测）。"""
    return _FakeHTTPResponse(200, {"ok": True})


@pytest.fixture
def fake_daemon(tmp_path, monkeypatch):
    """搭一个「假 daemon」：_STATE 就绪 + 发现文件指向任意端口（反正不会真连接）+ client 的
    urlopen 路由到 dispatch_request()。全链路验证 client ↔ daemon 协议（鉴权/路由/task 分发/
    请求构造/响应解析全部真跑），但不落地到真实 OS socket——天然对 test_gen_model.py 的全局
    urlopen/socket.connect 安全闸免疫。
    """
    d = tmp_path / "daemon_dir"
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(d))
    token = "tok-" + secrets.token_hex(8)
    monkeypatch.setattr(daemon_mod, "_STATE", daemon_mod._DaemonState(token))
    daemon_mod._write_discovery(12345, token, os.getpid())
    monkeypatch.setattr(urllib.request, "urlopen", _dispatch_urlopen)
    return Path(str(d))


def test_infer_roundtrip_correct_token(fake_daemon, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    monkeypatch.setitem(daemon_mod._TASK_HANDLERS, "vad", _fake_vad_handler)
    res = client.infer("vad", ["文本一", "文本二"])
    assert res is not None
    assert len(res) == 2
    assert res[0]["echo"] == "文本一"
    assert res[0]["source"] == "model"


def test_infer_style_embed_passes_model_param(fake_daemon, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    captured = {}

    def _fake_style(items, model=None):
        captured["model"] = model
        return [{"embedding": [0.1, 0.2, 0.3]} for _ in items]
    monkeypatch.setitem(daemon_mod._TASK_HANDLERS, "style_embed", _fake_style)
    res = client.infer("style_embed", ["文本"], model="character")
    assert res == [{"embedding": [0.1, 0.2, 0.3]}]
    assert captured["model"] == "character"


def test_infer_count_mismatch_returns_none(fake_daemon, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    monkeypatch.setitem(daemon_mod._TASK_HANDLERS, "vad",
                        lambda items, model=None: [{"source": "model"}])  # 故意少返回一条
    assert client.infer("vad", ["文本一", "文本二"]) is None


def test_infer_wrong_token_via_ensure_daemon_short_circuit(fake_daemon, monkeypatch):
    """发现文件的 token 与「daemon」持有的 token 不一致 → health 403 → ensure_daemon 判不可用
    且无 venv 可拉起 → infer() 快速返回 None（不阻塞 15s）。"""
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    monkeypatch.setattr(client, "_venv_python", lambda: None)
    disc_path = fake_daemon / "daemon.json"
    info = json.loads(disc_path.read_text(encoding="utf-8"))
    info["token"] = "wrong-token"
    disc_path.write_text(json.dumps(info), encoding="utf-8")
    started = time.monotonic()
    assert client.infer("vad", ["文本"]) is None
    assert time.monotonic() - started < 5.0  # 无 venv 时应立即放弃·不做 15s 轮询


def test_daemon_available_true_with_running_fake_daemon(fake_daemon, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    assert client.daemon_available() is True
    assert client.ensure_daemon() is True  # 已在跑 → 无需 spawn 直接 True


# ════════════════════════════ shutdown_daemon 客户端逻辑（假 urlopen·不碰真 os._exit 路径） ════════════════════════════

def test_shutdown_daemon_client_side(tmp_path, monkeypatch):
    d = tmp_path / "shutdown_test"
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(d))
    d.mkdir(parents=True, exist_ok=True)
    (d / "daemon.json").write_text(
        json.dumps({"port": 12345, "token": "tok", "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", _ok_urlopen)
    assert client.shutdown_daemon() is True
    assert not (d / "daemon.json").exists()  # 无论成败都清理发现文件


def test_daemon_cli_stop_against_fake_daemon(tmp_path, monkeypatch):
    d = tmp_path / "cli_stop_test"
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(d))
    d.mkdir(parents=True, exist_ok=True)
    (d / "daemon.json").write_text(json.dumps({"port": 12345, "token": "tok"}), encoding="utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", _ok_urlopen)
    assert daemon_mod._cli_stop() == 0


def test_daemon_cli_stop_no_discovery_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(tmp_path / "nope"))
    assert daemon_mod._cli_stop() == 1


# ════════════════════════════ idle 自退：可注入回调（纯内存状态·无 socket） ════════════════════════════

def test_idle_watchdog_triggers_on_idle_callback():
    state = daemon_mod._DaemonState("tok")
    state.last_request_ts = time.time() - 10  # 已经空闲了 10s
    triggered = threading.Event()
    wd = daemon_mod.IdleWatchdog(state, idle_sec=0.3, on_idle=triggered.set, check_interval=0.05)
    wd.start()
    try:
        assert triggered.wait(timeout=2.0), "idle watchdog 应在阈值后触发 on_idle 回调"
    finally:
        wd.stop()
        wd.join(timeout=1.0)


def test_idle_watchdog_does_not_trigger_while_active():
    state = daemon_mod._DaemonState("tok")
    triggered = threading.Event()
    wd = daemon_mod.IdleWatchdog(state, idle_sec=0.3, on_idle=triggered.set, check_interval=0.05)
    wd.start()
    try:
        end = time.time() + 0.6
        while time.time() < end:
            state.last_request_ts = time.time()  # 模拟持续活跃请求
            time.sleep(0.05)
        assert not triggered.is_set()
    finally:
        wd.stop()
        wd.join(timeout=1.0)


# ════════════════════════════ 2026-07-04 进程风暴根治回归锁 ════════════════════════════

def test_spawn_refused_under_pytest(monkeypatch, tmp_path):
    """pytest 环境（PYTEST_CURRENT_TEST 恒存在）→ _spawn_daemon 拒绝真拉起。
    实证教训：测试反复真拉起曾积出 24 个僵尸 python + 控制台黑框风暴。"""
    import subprocess as _sp
    monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("pytest 下不得真拉起 daemon")))
    assert "PYTEST_CURRENT_TEST" in os.environ  # pytest 自动设·前提成立
    client._spawn_daemon(Path("C:/definitely/venv/python.exe"))  # 不炸 = 拒绝生效


def test_spawn_allowed_with_explicit_override(monkeypatch, tmp_path):
    """显式 RUOYU_NN_DAEMON_ALLOW_SPAWN_IN_TESTS=1 才放行（真机冒烟测试用）。"""
    import subprocess as _sp
    calls = []
    monkeypatch.setenv("RUOYU_NN_DAEMON_ALLOW_SPAWN_IN_TESTS", "1")
    monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: calls.append(a) or None)
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("")
    client._spawn_daemon(fake_py)
    assert len(calls) == 1


def test_ensure_daemon_spawn_failure_sets_cooldown(monkeypatch, tmp_path):
    """拉起后轮询超时不可用 → 进入冷却期，冷却期内 ensure_daemon 不再尝试 spawn（防风暴）。"""
    monkeypatch.setenv("RUOYU_NN_DAEMON", "1")
    monkeypatch.setenv("RUOYU_NN_DAEMON_DIR", str(tmp_path / "no_daemon"))
    monkeypatch.setattr(client, "_venv_python", lambda: tmp_path / "python.exe")
    (tmp_path / "python.exe").write_text("")
    spawn_calls = []
    monkeypatch.setattr(client, "_spawn_daemon", lambda p: spawn_calls.append(p))
    monkeypatch.setattr(client, "daemon_available", lambda timeout=None: False)
    monkeypatch.setattr(client, "_spawn_cooldown_until", 0.0)
    assert client.ensure_daemon(timeout=0.05) is False
    assert len(spawn_calls) == 1
    assert client._spawn_cooldown_until > 0  # 冷却已设
    assert client.ensure_daemon(timeout=0.05) is False
    assert len(spawn_calls) == 1  # 冷却期内零新 spawn
