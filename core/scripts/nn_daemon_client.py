#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-03 Wave-5 常驻推理 daemon 客户端
"""nn_daemon_client.py — 系统 py(3.14·无 torch) 侧调用常驻推理 daemon 的客户端（纯 stdlib）。

【为什么】`core/ml/daemon/model_daemon.py` 常驻 venv 进程加载模型一次；本模块是系统侧薄客户端，
经 localhost HTTP 复用该常驻模型，取代每次调用都新起 subprocess 重新加载模型（~23-30s/次）。

【默认安全铁律（北极星⑤·零回归·与 4 个 nn_*_bridge.py 同款纪律）】
  · RUOYU_NN_DAEMON != "1"（默认 off）→ enabled()=False，本模块零网络动作。
  · daemon 不可达 / 拉起失败 / 超时 / 响应非 ok / 结果条数与 items 不齐 → infer() 返回 None，
    **绝不抛异常**——调用方（下一步的桥）必须回退既有 subprocess 路径。
  · 门控开但 venv/daemon 脚本缺 → ensure_daemon() 返回 False，同样 None 回退。

用法（供桥/scanner 调用）：
    import nn_daemon_client as ndc
    if ndc.enabled():
        results = ndc.infer("vad", ["他攥紧了拳头", "她笑了笑"])
        if results is not None:
            ...  # 用常驻 daemon 结果
        # results is None → 回退既有 subprocess 桥路径
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent   # core/scripts → core
_VENV_PY = _CORE / "ml" / ".venv" / "Scripts" / "python.exe"   # Windows venv
_VENV_PY_POSIX = _CORE / "ml" / ".venv" / "bin" / "python"     # POSIX venv（容错）
_DAEMON_SCRIPT = _CORE / "ml" / "daemon" / "model_daemon.py"

_TOKEN_HEADER = "X-Ruoyu-Token"
_DEFAULT_SPAWN_TIMEOUT = 15.0     # 秒·HTTP 壳启动是秒级（模型懒加载不阻塞启动）
_HEALTH_TIMEOUT = 1.5
_LOCK_STALE_SEC = 30.0            # 拉起锁文件陈旧判定（防持锁进程崩溃后永久卡死其它进程）
_SPAWN_POLL_INTERVAL = 0.3
# 🔴 2026-07-04 拉起失败冷却（根治进程风暴）：daemon 拉起后轮询超时仍不可用 → 本进程内
# 冷却期不再重复 spawn（只轮询探测）。实证教训：无冷却时每次 ensure_daemon 都再拉一个，
# 曾在测试反复运行下积出 24 个僵尸 python 进程。
_SPAWN_COOLDOWN_SEC = float(os.environ.get("RUOYU_NN_DAEMON_SPAWN_COOLDOWN_SEC", "300"))
_spawn_cooldown_until = 0.0


def _venv_python() -> "Path | None":
    if _VENV_PY.exists():
        return _VENV_PY
    if _VENV_PY_POSIX.exists():
        return _VENV_PY_POSIX
    return None


def _discovery_dir() -> Path:
    """与 model_daemon.py 同一约定：env RUOYU_NN_DAEMON_DIR 可覆盖（测试用），默认 core/ml/.cache/daemon。"""
    override = os.environ.get("RUOYU_NN_DAEMON_DIR")
    return Path(override) if override else (_CORE / "ml" / ".cache" / "daemon")


def _discovery_path() -> Path:
    return _discovery_dir() / "daemon.json"


def _read_discovery() -> "dict | None":
    try:
        obj = json.loads(_discovery_path().read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _clear_discovery() -> None:
    try:
        _discovery_path().unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid) -> bool:
    """跨平台 PID 存活检测（零额外依赖）。pid 非法 → False。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 进程存在但无权限发信号·仍判活
    except OSError:
        return False


def _post(port, token: str, path: str, body: "dict | None", timeout: float) -> "dict | None":
    """POST JSON 到 daemon（/health 也接受 POST），返回解析后的 dict；
    任何失败（网络/超时/非 2xx/JSON 解析）→ None。"""
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/{path.lstrip('/')}", data=data, method="POST",
        headers={_TOKEN_HEADER: str(token), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def enabled() -> bool:
    """门控总开关：RUOYU_NN_DAEMON=1（默认 off·测试确定性）。"""
    return os.environ.get("RUOYU_NN_DAEMON") == "1"


def daemon_available(timeout: float = _HEALTH_TIMEOUT) -> bool:
    """发现文件存在 + /health 200 + ok=true → True。

    网络失败时只有确认进程已死（pid 存活检测）才清理发现文件——避免把「daemon 只是一时繁忙/
    响应慢」误判成「daemon 已死」从而清掉一个仍在正常工作的发现文件。
    """
    info = _read_discovery()
    if info is None:
        return False
    port, token, pid = info.get("port"), info.get("token"), info.get("pid")
    if not port or not token:
        _clear_discovery()
        return False
    payload = _post(port, token, "/health", {}, timeout=timeout)
    if payload is not None and payload.get("ok"):
        return True
    if pid is not None and not _pid_alive(pid):
        _clear_discovery()
    return False


# ---------------- 并发拉起互斥锁（防多进程同时 spawn daemon） ----------------

def _lock_path() -> Path:
    return _discovery_dir() / "daemon.spawn.lock"


def _acquire_spawn_lock() -> bool:
    lp = _lock_path()
    try:
        lp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - lp.stat().st_mtime > _LOCK_STALE_SEC:
                lp.unlink(missing_ok=True)
                fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
        except OSError:
            pass
        return False
    except OSError:
        return False


def _release_spawn_lock() -> None:
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def _spawn_daemon(venv_py: Path) -> None:
    """Windows：隐藏窗口 + 新进程组，脱离本进程生命周期，stdio 全部丢弃（daemon 自己写 daemon.log）。

    🔴 2026-07-04 双防线：①CREATE_NO_WINDOW（替换 DETACHED_PROCESS——实证有拉起路径弹出
    控制台黑框，CREATE_NO_WINDOW 保证任何情况下不显示窗口）；②pytest 环境拒绝真拉起
    （PYTEST_CURRENT_TEST 存在即拒绝，除非显式 RUOYU_NN_DAEMON_ALLOW_SPAWN_IN_TESTS=1）——
    测试残留真 daemon 进程是整类事故（僵尸占显存+黑框风暴），在唯一拉起点堵死。"""
    if not _DAEMON_SCRIPT.exists():
        return
    if (os.environ.get("PYTEST_CURRENT_TEST")
            and os.environ.get("RUOYU_NN_DAEMON_ALLOW_SPAWN_IN_TESTS") != "1"):
        return
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            [str(venv_py), str(_DAEMON_SCRIPT)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(_DAEMON_SCRIPT.parent),
            **kwargs,
        )
    except OSError:
        pass


def ensure_daemon(timeout: float = _DEFAULT_SPAWN_TIMEOUT) -> bool:
    """确保 daemon 可用：已在跑 → True；否则门控开+venv 在 → 拉起并轮询健康（≤timeout 秒）。

    并发拉起用锁文件防多进程同时 spawn——拿不到锁的进程只轮询等待，不重复拉起。
    """
    global _spawn_cooldown_until
    if not enabled():
        return False
    if daemon_available():
        return True
    venv_py = _venv_python()
    if venv_py is None:
        return False
    if time.monotonic() < _spawn_cooldown_until:
        return False   # 冷却期内不再重复拉起（防进程风暴）·下次自然重试

    got_lock = _acquire_spawn_lock()
    try:
        if got_lock:
            _spawn_daemon(venv_py)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if daemon_available():
                return True
            time.sleep(_SPAWN_POLL_INTERVAL)
        if daemon_available():
            return True
        _spawn_cooldown_until = time.monotonic() + _SPAWN_COOLDOWN_SEC
        return False
    finally:
        if got_lock:
            _release_spawn_lock()


def infer(task: str, items: list, model: "str | None" = None,
          timeout: float = 120.0) -> "list | None":
    """批量推理。items 与 task 的形状约定见 model_daemon.py `/infer` 契约。

    任何失败（未启用/daemon 不可达/超时/响应非 ok/结果条数与 items 不齐）→ None，
    调用方回退既有 subprocess 路径——绝不抛异常。
    """
    if not isinstance(items, list):
        return None
    if not items:
        return []
    if not ensure_daemon():
        return None
    info = _read_discovery()
    if info is None:
        return None
    port, token = info.get("port"), info.get("token")
    if not port or not token:
        return None

    body: dict = {"task": task, "items": items}
    if model is not None:
        body["model"] = model
    payload = _post(port, token, "/infer", body, timeout=timeout)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(items):
        return None
    return results


def shutdown_daemon(timeout: float = 5.0) -> bool:
    """向当前发现文件指向的 daemon 发 /shutdown（供 --stop / 测试清理用）。总是清理发现文件。"""
    info = _read_discovery()
    if info is None:
        return False
    port, token = info.get("port"), info.get("token")
    if not port or not token:
        _clear_discovery()
        return False
    payload = _post(port, token, "/shutdown", {}, timeout=timeout)
    _clear_discovery()
    return bool(payload is not None and payload.get("ok"))


def main() -> int:
    """CLI 自测：python nn_daemon_client.py [--stop]"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--stop" in sys.argv[1:]:
        ok = shutdown_daemon()
        print(json.dumps({"shutdown_ok": ok}, ensure_ascii=False))
        return 0
    print(f"enabled={enabled()}  venv={_venv_python()}  discovery={_discovery_path()}")
    if not enabled():
        print("RUOYU_NN_DAEMON 未开启（设为 1 才会尝试拉起/连接 daemon）")
        return 0
    ok = ensure_daemon()
    print(f"ensure_daemon={ok}")
    if ok:
        res = infer("vad", ["他攥紧了拳头，指节发白", "她笑了笑，心里很幸福"])
        print(json.dumps({"result": res}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
