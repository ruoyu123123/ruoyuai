#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-03 Wave-5 常驻推理 daemon
"""model_daemon.py — venv 侧常驻推理 daemon（localhost HTTP·业界标准"模型驻内存"形态）。

【为什么】5 类模型桥（style_embed/vad/surprisal/coherence/nli）此前每次调用都新起 venv
子进程重新加载模型（~23-30s/次）。本 daemon 常驻 venv 进程，模型只加载一次，所有调用经
localhost HTTP 复用（对标 llama.cpp server / MLflow local inference server；idle 自退对标
vLLM sleep mode）。系统侧经 `core/scripts/nn_daemon_client.py` 调用本 daemon。

【懒加载纪律（北极星⑤·确定性 + 可测试性）】
  · 模块顶层零 torch import——本文件顶层只用 stdlib，系统 py（无 torch）也能安全 import
    （被 tests/test_nn_daemon.py 当懒加载设计的回归锁）。
  · torch/模型 import 全部推迟到各 task 首次 /infer 请求命中 handler 内部才发生。
  · 某 task 加载失败 → 该 task 标记不可用（不缓存·下次请求重试）·返回 {"ok":false,"error":...}·
    不影响其它已加载/待加载的 task。

【已知陷阱：vad_infer.py / coherence_infer.py 同名 model.py】
  两者各自目录下都有一个 `model.py`，各自 `_ensure()` 内部 `sys.path.insert(0, 自己目录)` 后
  `from model import XXX`。原设计是「一 subprocess 一 task」，`sys.modules['model']` 缓存
  不会跨 task 撞车；daemon 常驻同进程内先后加载两个 task 就会撞（Python `from X import Y`
  优先命中 sys.modules 缓存，不会因为 sys.path 顺序变了就重新执行文件）。本 daemon 在每个
  task 首次加载完后主动 `sys.modules.pop("model", None)`，逼下一个不同 task 按当时 sys.path
  顺序重新 import 自己的 model.py。

用法：
  python model_daemon.py            # 前台常驻（一般由 nn_daemon_client.ensure_daemon 后台拉起）
  python model_daemon.py --stop     # 读发现文件调 /shutdown 后退出
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_ML_DIR = Path(__file__).resolve().parent.parent          # core/ml
_DEFAULT_IDLE_SEC = 1800.0
_MAX_LOG_BYTES = 2 * 1024 * 1024                           # 2MB 封顶·超过截断重开（简单粗暴·非无限增长）
_TOKEN_HEADER = "X-Ruoyu-Token"


# ════════════════════════════ 发现文件 / 日志（纯 stdlib·可被系统 py 安全调用） ════════════════════════════

def _discovery_dir() -> Path:
    """发现文件所在目录。env RUOYU_NN_DAEMON_DIR 可覆盖（测试用·重定向到 tmp）。"""
    override = os.environ.get("RUOYU_NN_DAEMON_DIR")
    return Path(override) if override else (_ML_DIR / ".cache" / "daemon")


def _discovery_path() -> Path:
    return _discovery_dir() / "daemon.json"


def _log_path() -> Path:
    return _discovery_dir() / "daemon.log"


def _log(msg: str) -> None:
    """写日志（截断式简单写·封顶 2MB·绝不因日志失败影响主流程）。"""
    try:
        d = _discovery_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = _log_path()
        if p.exists() and p.stat().st_size > _MAX_LOG_BYTES:
            p.write_text("", encoding="utf-8")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _write_discovery(port: int, token: str, pid: int) -> None:
    """原子写发现文件（tmp + os.replace）。"""
    d = _discovery_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _discovery_path()
    tmp = d / f".daemon.json.tmp-{pid}-{secrets.token_hex(4)}"
    payload = {"port": port, "token": token, "pid": pid, "started_at": time.time()}
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _clear_discovery_file() -> None:
    try:
        _discovery_path().unlink(missing_ok=True)
    except OSError:
        pass


# ════════════════════════════ 懒加载 task handler（各自首次调用才 import/加载模型） ════════════════════════════

_TASK_CACHE: dict = {}      # 懒加载缓存：task 或 "task:variant" → 已加载的 predictor/model 实例
_MODULE_CACHE: dict = {}    # 子模块 import 缓存：alias → 已 import 的 module 对象


def _import_sibling(subdir: str, modname: str, alias: str):
    """把 core/ml/<subdir> 插到 sys.path[0] 并 import modname，按 alias 缓存。仅 handler 内调用。"""
    if alias in _MODULE_CACHE:
        return _MODULE_CACHE[alias]
    d = str(_ML_DIR / subdir)
    if d not in sys.path:
        sys.path.insert(0, d)
    mod = importlib.import_module(modname)
    _MODULE_CACHE[alias] = mod
    return mod


def _pop_generic_model_module() -> None:
    """清掉 sys.modules['model'] 缓存（见模块 docstring「已知陷阱」段）。"""
    sys.modules.pop("model", None)


def _resolve_ckpt(env_name: str, default_path: Path) -> "str | None":
    """通用 ckpt 路径解析：env 覆盖优先·否则默认路径（存在才返回）。与各 nn_*_bridge.py 的
    _resolve_ckpt() 同一约定（env 名 + 默认路径逐一对齐·不共享代码只对齐行为，daemon 独立于
    bridge 实现，互不耦合）。"""
    env_val = os.environ.get(env_name)
    if env_val and Path(env_val).exists():
        return env_val
    if default_path.exists():
        return str(default_path)
    return None


# ---------- style_embed（author/character 两个 SentenceTransformer 模型） ----------

_STYLE_MODEL_DIRS = {
    "author": _ML_DIR / "style_embed" / "runs" / "style_embed_v1" / "final",
    "character": _ML_DIR / "style_embed" / "runs" / "style_embed_char_v1" / "final",
}
_STYLE_MODEL_ENV = {"author": "RUOYU_STYLE_MODEL", "character": "RUOYU_CHAR_STYLE_MODEL"}


def _resolve_style_model_dir(variant: str) -> Path:
    env_key = _STYLE_MODEL_ENV.get(variant)
    if env_key and os.environ.get(env_key):
        p = Path(os.environ[env_key])
    else:
        p = _STYLE_MODEL_DIRS.get(variant, _STYLE_MODEL_DIRS["author"])
    if not p.exists():
        raise FileNotFoundError(f"style_embed[{variant}] 模型目录不存在: {p}")
    return p


def _get_style_model(variant: str):
    key = f"style_embed:{variant}"
    if key in _TASK_CACHE:
        return _TASK_CACHE[key]
    style_infer = _import_sibling("style_embed", "style_infer", "style_infer")
    mp = _resolve_style_model_dir(variant)
    m = style_infer.load_model(mp)
    _TASK_CACHE[key] = m
    return m


def _infer_style_embed(items: list, model: "str | None" = None) -> list:
    variant = model or "author"
    style_infer = _import_sibling("style_embed", "style_infer", "style_infer")
    m = _get_style_model(variant)
    embs = style_infer.encode_with_model(m, [str(t) for t in items])
    return [{"embedding": e} for e in embs]


# ---------- vad ----------

def _resolve_vad_ckpt() -> "str | None":
    return _resolve_ckpt("RUOYU_VAD_CKPT", _ML_DIR / "emotion_vad" / "checkpoints" / "va_base")


def _get_vad_predictor():
    key = "vad"
    if key in _TASK_CACHE:
        return _TASK_CACHE[key]
    vad_infer = _import_sibling("emotion_vad", "vad_infer", "vad_infer")
    predictor = vad_infer.get_predictor(_resolve_vad_ckpt())
    try:
        _ = predictor.mode  # 触发 _ensure()：懒加载 torch + checkpoint（失败退占位词典·不抛）
    finally:
        _pop_generic_model_module()
    _TASK_CACHE[key] = predictor
    return predictor


def _infer_vad(items: list, model: "str | None" = None) -> list:
    predictor = _get_vad_predictor()
    return predictor.predict_batch([str(t) for t in items])


# ---------- coherence（单文本窗口 + 文本对衔接·混合批次） ----------

def _resolve_coherence_ckpt() -> "str | None":
    return _resolve_ckpt("RUOYU_COHERENCE_CKPT", _ML_DIR / "coherence" / "runs" / "coherence_v1")


def _get_coherence_predictor():
    key = "coherence"
    if key in _TASK_CACHE:
        return _TASK_CACHE[key]
    coherence_infer = _import_sibling("coherence", "coherence_infer", "coherence_infer")
    predictor = coherence_infer.get_predictor(_resolve_coherence_ckpt())
    try:
        _ = predictor.mode
    finally:
        _pop_generic_model_module()
    _TASK_CACHE[key] = predictor
    return predictor


def _is_pair_item(it) -> bool:
    return isinstance(it, dict) and ("text_a" in it or "text_b" in it)


def _infer_coherence(items: list, model: "str | None" = None) -> list:
    predictor = _get_coherence_predictor()
    results: list = [None] * len(items)
    single_idx = [i for i, it in enumerate(items) if not _is_pair_item(it)]
    pair_idx = [i for i, it in enumerate(items) if _is_pair_item(it)]
    if single_idx:
        texts = [str(items[i]) for i in single_idx]
        for i, r in zip(single_idx, predictor.predict_batch(texts)):
            results[i] = r
    if pair_idx:
        pairs = [(str(items[i].get("text_a", "")), str(items[i].get("text_b", ""))) for i in pair_idx]
        for i, r in zip(pair_idx, predictor.predict_pairs(pairs)):
            results[i] = r
    return results


# ---------- surprisal（GPT-2·模块自带 _SCORER 单例·daemon 只需缓存模块引用避免重复 import） ----------

def _get_surprisal_module():
    key = "surprisal"
    if key in _TASK_CACHE:
        return _TASK_CACHE[key]
    surprisal_infer = _import_sibling("surprisal", "surprisal_infer", "surprisal_infer")
    _TASK_CACHE[key] = surprisal_infer
    return surprisal_infer


def _infer_surprisal(items: list, model: "str | None" = None) -> list:
    surprisal_infer = _get_surprisal_module()
    model_name = os.environ.get("RUOYU_SURPRISAL_MODEL", surprisal_infer.DEFAULT_MODEL)
    return surprisal_infer.predict_batch([str(t) for t in items], model_name=model_name)


# ---------- nli ----------

def _resolve_nli_ckpt() -> "str | None":
    return _resolve_ckpt("RUOYU_NLI_CKPT", _ML_DIR / "models" / "nli" / "erlangshen-roberta-110m-nli")


def _get_nli_predictor():
    key = "nli"
    if key in _TASK_CACHE:
        return _TASK_CACHE[key]
    nli_infer = _import_sibling("nli", "nli_infer", "nli_infer")
    predictor = nli_infer.get_predictor(_resolve_nli_ckpt())
    _ = predictor.mode  # nli_infer 无同名 model.py 陷阱·无需 pop
    _TASK_CACHE[key] = predictor
    return predictor


def _infer_nli(items: list, model: "str | None" = None) -> list:
    predictor = _get_nli_predictor()
    pairs = [{"premise": str((it or {}).get("premise", "")),
              "hypothesis": str((it or {}).get("hypothesis", ""))} for it in items]
    return predictor.predict_batch(pairs)


_TASK_HANDLERS = {
    "style_embed": _infer_style_embed,
    "vad": _infer_vad,
    "coherence": _infer_coherence,
    "surprisal": _infer_surprisal,
    "nli": _infer_nli,
}


# ════════════════════════════ daemon 状态 + HTTP handler ════════════════════════════

class _DaemonState:
    def __init__(self, token: str):
        self.token = token
        self.start_time = time.time()
        self.last_request_ts = time.time()
        self.lock = threading.Lock()   # 全局推理锁：GPU 推理串行化·避免并发显存竞争


_STATE: "_DaemonState | None" = None


def _health_payload() -> dict:
    return {
        "ok": True,
        "models_loaded": sorted(_TASK_CACHE.keys()),
        "uptime_sec": round(time.time() - _STATE.start_time, 1) if _STATE else 0.0,
    }


def _delayed_exit(reason: str) -> None:
    """留 0.2s 给 HTTP 响应真正发出去，再清理发现文件 + 退出进程。"""
    time.sleep(0.2)
    _log(f"[model_daemon] {reason}·退出")
    _clear_discovery_file()
    os._exit(0)


def _dispatch_infer(body: dict) -> "tuple[int, dict]":
    """`/infer` 的纯协议逻辑：body dict → (status, payload)。不碰任何 socket/IO。"""
    task = body.get("task")
    items = body.get("items")
    model = body.get("model")
    if task not in _TASK_HANDLERS:
        return 400, {"ok": False, "error": f"unknown_task:{task}"}
    if not isinstance(items, list):
        return 400, {"ok": False, "error": "items_must_be_list"}
    if not items:
        return 200, {"ok": True, "results": []}
    handler = _TASK_HANDLERS[task]
    try:
        with _STATE.lock:  # 全局推理锁：整个 task 调用（含首次懒加载）都串行化
            results = handler(items, model=model)
    except Exception as e:  # noqa: BLE001 该 task 加载/推理异常 → 标记不可用·不影响其它 task
        err = f"{type(e).__name__}: {str(e)[:200]}"
        _log(f"[model_daemon] task={task} 推理异常：{err}\n{traceback.format_exc()}")
        return 200, {"ok": False, "error": err}
    return 200, {"ok": True, "results": results}


def _get_header_ci(headers: dict, name: str) -> "str | None":
    """大小写不敏感的 header 查找。HTTP header 名规范上大小写不敏感——真实请求经
    `email.message.Message`（`self.headers`）读取时原生大小写不敏感，但 `_Handler` 为了让
    协议逻辑能脱离 socket 单测而把它转成普通 dict 时会丢失这个语义（且 `urllib.request.Request`
    客户端侧还会把 header 名 `.capitalize()` 成 `X-ruoyu-token` 这种大小写）——本函数补回来。"""
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return None


def dispatch_request(method: str, path: str, headers: dict, raw_body: bytes) -> "tuple[int, dict]":
    """全部 3 个端点的纯协议分发：(method, path, headers, raw_body) → (status, payload)。

    刻意与 `_Handler` 的 socket I/O 解耦——`_Handler.do_GET`/`do_POST` 只是把
    `BaseHTTPRequestHandler` 的属性适配成这几个参数后调用本函数。这样协议层（鉴权/路由/
    task 分发）可以在测试里直接函数调用验证，不需要真实起 HTTP server/socket
    （tests/test_nn_daemon.py 用此函数绕开测试全局的网络安全闸——见该文件顶部说明）。
    """
    if not (_STATE is not None and _get_header_ci(headers, _TOKEN_HEADER) == _STATE.token):
        return 403, {"ok": False, "error": "unauthorized"}
    if path == "/health":
        return 200, _health_payload()
    if method == "POST" and path == "/shutdown":
        return 200, {"ok": True}   # 真正退出由 _Handler.do_POST 在响应发出后另起线程触发
    if method == "POST" and path == "/infer":
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 400, {"ok": False, "error": "invalid_json"}
        if not isinstance(body, dict):
            return 400, {"ok": False, "error": "body_must_be_object"}
        return _dispatch_infer(body)
    return 404, {"ok": False, "error": "not_found"}


class _Handler(BaseHTTPRequestHandler):
    """真实 socket I/O 适配层：把请求属性喂给 `dispatch_request()`，把结果写回连接。
    自身不含任何协议判断逻辑（鉴权/路由/task 分发全在 dispatch_request 里，可脱离 socket 单测）。"""

    protocol_version = "HTTP/1.1"
    server_version = "RuoyuModelDaemon/1.0"

    def log_message(self, fmt, *args):  # 覆盖默认 stderr 日志·统一走文件
        _log("[http] " + (fmt % args))

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def do_GET(self):  # noqa: N802
        if _STATE is not None:
            _STATE.last_request_ts = time.time()
        status, payload = dispatch_request("GET", self.path, dict(self.headers), b"")
        self._send_json(status, payload)

    def do_POST(self):  # noqa: N802
        if _STATE is not None:
            _STATE.last_request_ts = time.time()
        raw = self._read_body()   # 无论鉴权是否通过都先把 body 读完·避免 keep-alive 连接错位
        status, payload = dispatch_request("POST", self.path, dict(self.headers), raw)
        self._send_json(status, payload)
        if status == 200 and self.path == "/shutdown" and payload.get("ok"):
            threading.Thread(target=_delayed_exit, args=("收到 /shutdown 请求",), daemon=True).start()


# ════════════════════════════ idle 自退（可注入退出回调·便于测试） ════════════════════════════

class IdleWatchdog(threading.Thread):
    """空闲监控线程：每 check_interval 秒检查一次 `state.last_request_ts`；
    连续空闲超过 idle_sec → 调用 on_idle()（生产环境退出进程；测试可注入假回调）。"""

    def __init__(self, state: _DaemonState, idle_sec: float, on_idle, check_interval: float = 1.0):
        super().__init__(daemon=True)
        self.state = state
        self.idle_sec = idle_sec
        self.on_idle = on_idle
        self.check_interval = check_interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.wait(self.check_interval):
            if time.time() - self.state.last_request_ts >= self.idle_sec:
                self.on_idle()
                return


# ════════════════════════════ 启动 / CLI ════════════════════════════

def serve_forever(idle_sec: "float | None" = None) -> int:
    """前台常驻主循环：起 HTTP server + 写发现文件 + 起 idle watchdog。"""
    global _STATE
    token = secrets.token_hex(16)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    pid = os.getpid()
    _STATE = _DaemonState(token)
    _write_discovery(port, token, pid)
    _log(f"[model_daemon] 启动 port={port} pid={pid}")

    resolved_idle = idle_sec if idle_sec is not None else float(
        os.environ.get("RUOYU_NN_DAEMON_IDLE_SEC", _DEFAULT_IDLE_SEC))

    def _on_idle():
        _log(f"[model_daemon] 空闲超过 {resolved_idle}s")
        try:
            httpd.shutdown()
        finally:
            _clear_discovery_file()
            os._exit(0)

    watchdog = IdleWatchdog(_STATE, resolved_idle, _on_idle)
    watchdog.start()
    try:
        httpd.serve_forever()
    finally:
        watchdog.stop()
        _clear_discovery_file()
    return 0


def _cli_stop() -> int:
    try:
        info = json.loads(_discovery_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("[model_daemon] 未找到发现文件·daemon 可能未运行", file=sys.stderr)
        return 1
    port, token = info.get("port"), info.get("token")
    if not port or not token:
        print("[model_daemon] 发现文件损坏（缺 port/token）", file=sys.stderr)
        return 1
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/shutdown", data=b"{}", method="POST",
            headers={_TOKEN_HEADER: token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[model_daemon] 已发送 shutdown（status={resp.status}）")
            return 0
    except (urllib.error.URLError, OSError) as e:
        print(f"[model_daemon] shutdown 请求失败: {e}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="若渝 NN 常驻推理 daemon（venv 侧·localhost HTTP）")
    ap.add_argument("--stop", action="store_true", help="向已运行的 daemon 发送 /shutdown 并退出")
    args = ap.parse_args()
    if args.stop:
        return _cli_stop()
    return serve_forever()


if __name__ == "__main__":
    sys.exit(main())
