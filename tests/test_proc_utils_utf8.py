#!/usr/bin/env python3
"""proc_utils 子进程 UTF-8 单一真理源回归锁（G6 · 2026-07-14）。

背景：全仓 30+ 处 subprocess 起子进程时不强制 PYTHONIOENCODING/PYTHONUTF8。
在 GBK 控制台或缺这两个 env 的父环境（agent 上下文）下，子进程 CJK stdout 按本地编码
落字节，被父进程按 utf-8 捕获成 mojibake（`锟斤拷`）→ 解析静默拿空结果、错误指纹被
污染进 incidents.jsonl，流水线却照报「成功」。实证：self_heal_engine dashboard 里
躺着 `::FATAL::锟斤拷...` 乱码指纹。

根治：抽 `core/scripts/proc_utils.py` 单一真理源（run_utf8 / popen_utf8 / utf8_env），
强制 UTF-8 env + utf-8 解码，所有起子进程的调用点收敛过去。

本锁定：
  1. utf8_env 构造的 env 恒含 PYTHONIOENCODING=utf-8 + PYTHONUTF8=1，且强制项不可被覆盖。
  2. run_utf8 默认 encoding=utf-8/errors=replace（text=True），text=False 时不注入。
  3. 端到端：父环境剥掉 UTF-8 env 时，run_utf8 仍无损回传子进程的 CJK stdout（防 mojibake）。
  4. 关键调用点 adaptive_runner.run_with_resilience 真的经 run_utf8 起子进程。
"""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import proc_utils  # noqa: E402


# ── 1. utf8_env 强制项 ──────────────────────────────────────────────
def test_utf8_env_has_forced_keys():
    env = proc_utils.utf8_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


def test_utf8_env_forced_keys_are_the_module_constant():
    # 强制项来自单一真理源常量，不许某处各写各的
    assert proc_utils.FORCED_UTF8_ENV == {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def test_utf8_env_env_extra_cannot_override_forced():
    # 调用方即使传 gbk / 空值也覆盖不掉强制 UTF-8
    env = proc_utils.utf8_env(env_extra={"PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"})
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


def test_utf8_env_base_cannot_override_forced():
    # 传入的 base（如剥了 UTF-8 又设成 gbk 的父环境）也覆盖不掉强制项
    hostile_base = {"PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0", "FOO": "bar"}
    env = proc_utils.utf8_env(base=hostile_base)
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["FOO"] == "bar"  # 业务变量仍透传


def test_utf8_env_passes_through_business_vars():
    env = proc_utils.utf8_env(env_extra={"CLUSTER_MODE": "1", "CLUSTER_ID": "cluster_001"})
    assert env["CLUSTER_MODE"] == "1"
    assert env["CLUSTER_ID"] == "cluster_001"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_utf8_env_drops_none_values():
    env = proc_utils.utf8_env(env_extra={"MAYBE": None, "KEEP": "x"})
    assert "MAYBE" not in env
    assert env["KEEP"] == "x"


# ── 2. run_utf8 encoding 语义 ───────────────────────────────────────
def test_run_utf8_defaults_encoding_when_text(monkeypatch):
    captured = {}

    def _fake_run(cmd, **kw):
        captured.update(kw)

        class _P:  # noqa: D401
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()

    monkeypatch.setattr(proc_utils.subprocess, "run", _fake_run)
    proc_utils.run_utf8(["x"])
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["text"] is True
    assert captured["capture_output"] is True
    # env 走强制 UTF-8
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"


def test_run_utf8_no_encoding_when_text_false(monkeypatch):
    captured = {}

    def _fake_run(cmd, **kw):
        captured.update(kw)

        class _P:
            returncode = 0
            stdout = b""
            stderr = b""
        return _P()

    monkeypatch.setattr(proc_utils.subprocess, "run", _fake_run)
    proc_utils.run_utf8(["x"], text=False)
    # 字节模式：不注入 encoding/errors（调用方自行 decode），也不显式传 text
    # （subprocess 默认即字节·等价历史裸调用形态·避免破坏按老签名写的测试 fake）
    assert "encoding" not in captured
    assert "errors" not in captured
    assert "text" not in captured
    # env 仍强制 UTF-8
    assert captured["env"]["PYTHONUTF8"] == "1"


# ── 3. 端到端防 mojibake（真子进程）───────────────────────────────────
def test_run_utf8_cjk_roundtrip_under_stripped_parent_env():
    """父环境剥掉 UTF-8 env（模拟 GBK 控制台 / agent 上下文）时，run_utf8 仍无损回传 CJK。

    这是 G6 bug 的核心复现：裸 subprocess.run(text=True) 在这种父环境下会把子进程
    CJK stdout 解成 mojibake；run_utf8 强制子进程 UTF-8 输出后回传无损。
    """
    stripped = dict(os.environ)
    stripped.pop("PYTHONIOENCODING", None)
    stripped.pop("PYTHONUTF8", None)
    marker = "涟漪规则大势已定锟斤拷区分"
    r = proc_utils.run_utf8(
        [sys.executable, "-c", f"print({marker!r})"],
        env=stripped, timeout=30,
    )
    assert r.returncode == 0
    assert marker in r.stdout  # 无损回传·无 mojibake


# ── 4. adaptive_runner 关键调用点真的走 helper ───────────────────────
def test_adaptive_runner_routes_through_run_utf8(tmp_path, monkeypatch):
    """adaptive_runner 包裹所有 required step 脚本——必须经 run_utf8 起子进程，
    否则子进程 CJK 输出 mojibake 会把乱码写进 incidents.jsonl 错误指纹。"""
    import adaptive_runner as ar

    calls = {"n": 0}
    real = ar.run_utf8

    def _spy(cmd, **kw):
        calls["n"] += 1
        return real(cmd, **kw)

    monkeypatch.setattr(ar, "run_utf8", _spy)
    r = ar.run_with_resilience(
        [sys.executable, "-c", "print('ok')"],
        label="g6_route_probe",
        project_root=str(tmp_path),
    )
    assert calls["n"] >= 1, "run_with_resilience 必须经 run_utf8 起子进程"
    assert r["ok"] is True


def test_adaptive_runner_imports_run_utf8_symbol():
    import adaptive_runner as ar
    assert hasattr(ar, "run_utf8")
    assert ar.run_utf8 is proc_utils.run_utf8


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
