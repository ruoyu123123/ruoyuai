# -*- coding: utf-8 -*-
# 🔴 2026-07-04 Wave-5 daemon-first 接线（补 nn_coherence_bridge 桥级回归·此前只有 scanner 间接覆盖）
"""nn_coherence_bridge 回归：默认安全（env off / venv 缺 / ckpt 缺 / subprocess 失败 / 超时 /
条数失配 / 非 model source 过滤）一律 → None（调用方回退启发式·不崩）；mock 模型输出验解析
（单文本窗口 + 文本对衔接两种形态）；daemon-first（Wave-5 常驻推理 daemon）命中/未命中/关闭三态；
可选真 venv 冒烟（门控）。

确定性·零网络（mock subprocess）。真模型冒烟需 RUOYU_RUN_REAL_NN=1（慢·本地无 API 花费）。"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import nn_coherence_bridge as mod  # noqa: E402


def _fake_run(out_lines):
    """伪 subprocess.run：解析 argv 的 --out，把 canned jsonl 写进去，返回 returncode=0。"""
    def runner(cmd, capture_output=True, timeout=None, env=None):
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.write_text(("\n".join(out_lines) + "\n") if out_lines else "", encoding="utf-8")

        class _R:
            returncode = 0
            stderr = b""
            stdout = b""
        return _R()
    return runner


def _force_enabled(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: Path("py.exe"))
    monkeypatch.setattr(mod, "_resolve_ckpt", lambda: "ckpt")


# ---------------- 默认安全：env off ----------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲", "乙"]) == [None, None]
    assert mod.predict_one("丙") is None
    assert mod.predict_pair("甲", "乙") is None


def test_empty_input():
    assert mod.predict_batch([]) == []
    assert mod.predict_pairs([]) == []


# ---------------- 默认安全：先决条件缺 ----------------

def test_missing_venv_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲", "乙"]) == [None, None]


def test_missing_ckpt_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    monkeypatch.setattr(mod, "_resolve_ckpt", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- 默认安全：subprocess 失败 / 超时 ----------------

def test_subprocess_timeout_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def boom(*a, **k):
        raise mod.subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.predict_batch(["甲", "乙"]) == [None, None]


def test_subprocess_nonzero_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def fail(cmd, capture_output=True, timeout=None, env=None):
        class _R:
            returncode = 1
            stderr = b"boom"
            stdout = b""
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", fail)
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- 解析：单文本窗口 · 模型 source 命中 ----------------

def test_model_results_parsed_single(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [
        json.dumps({"coherence_score": 0.9, "is_coherent": True, "source": "model"}),
        json.dumps({"coherence_score": 0.2, "is_coherent": False, "source": "model"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["连贯的文本", "拼接混乱的文本"])
    assert res[0]["source"] == "model" and abs(res[0]["coherence_score"] - 0.9) < 1e-9
    assert res[0]["is_coherent"] is True
    assert res[1]["is_coherent"] is False


def test_unavailable_source_filtered_to_none(monkeypatch):
    """coherence_infer 无 checkpoint(mode=unavailable) → 桥返回 None（调用方回退启发式）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"coherence_score": None, "is_coherent": None, "source": "unavailable"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch(["甲"]) == [None]


def test_count_mismatch_graceful(monkeypatch):
    """输出条数 != 输入条数 → 全 None（保序契约破损即回退）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"coherence_score": 0.5, "is_coherent": True, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch(["甲", "乙"]) == [None, None]


def test_malformed_json_line_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def runner(cmd, capture_output=True, timeout=None, env=None):
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.write_text("{not valid json\n", encoding="utf-8")

        class _R:
            returncode = 0
            stderr = b""
            stdout = b""
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", runner)
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- 解析：文本对衔接 · 模型 source 命中 ----------------

def test_model_results_parsed_pairs(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"coherence_score": 0.8, "is_coherent": True, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_pairs([("场景 A 的结尾", "场景 B 的开头")])
    assert res[0]["is_coherent"] is True
    assert abs(res[0]["coherence_score"] - 0.8) < 1e-9


def test_predict_pair_convenience(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"coherence_score": 0.3, "is_coherent": False, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_pair("甲", "乙")
    assert res["is_coherent"] is False


# ---------------- 异常安全 ----------------

def test_unexpected_exception_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- daemon-first（Wave-5 常驻推理 daemon·~0.1s） ----------------

def test_daemon_hit_bypasses_subprocess_single(monkeypatch):
    """单文本窗口：daemon 命中 → 直接用其结果·绝不碰 subprocess（mock 为炸弹断言零调用）。
    验证 daemon items 传的是裸字符串（不是 {"text":...} dict），对齐 model_daemon._is_pair_item 契约。"""
    _force_enabled(monkeypatch)

    class _FakeDaemonClientHit:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        def ensure_daemon():
            return True

        @staticmethod
        def infer(task, items, model=None, timeout=None):
            assert task == "coherence"
            assert items == ["甲文本", "乙文本"]   # 裸字符串·非 {"text":...} dict
            return [{"coherence_score": 0.95, "is_coherent": True, "source": "model"},
                    {"coherence_score": 0.1, "is_coherent": False, "source": "model"}]
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientHit())

    def _boom(*a, **k):
        raise AssertionError("daemon 命中时不应调用 subprocess")
    monkeypatch.setattr(mod.subprocess, "run", _boom)

    res = mod.predict_batch(["甲文本", "乙文本"])
    assert res[0]["is_coherent"] is True and abs(res[0]["coherence_score"] - 0.95) < 1e-9
    assert res[1]["is_coherent"] is False


def test_daemon_hit_bypasses_subprocess_pairs(monkeypatch):
    """文本对衔接：daemon 命中 → items 传 {"text_a","text_b"} dict（对齐 model_daemon._is_pair_item）。"""
    _force_enabled(monkeypatch)

    class _FakeDaemonClientHit:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        def ensure_daemon():
            return True

        @staticmethod
        def infer(task, items, model=None, timeout=None):
            assert task == "coherence"
            assert items == [{"text_a": "场景 A", "text_b": "场景 B"}]
            return [{"coherence_score": 0.77, "is_coherent": True, "source": "model"}]
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientHit())

    def _boom(*a, **k):
        raise AssertionError("daemon 命中时不应调用 subprocess")
    monkeypatch.setattr(mod.subprocess, "run", _boom)

    res = mod.predict_pairs([("场景 A", "场景 B")])
    assert res[0]["is_coherent"] is True and abs(res[0]["coherence_score"] - 0.77) < 1e-9


def test_daemon_miss_falls_back_to_subprocess(monkeypatch):
    """daemon 返回 None（未命中/不可用）→ 无缝落 subprocess 路径（mock subprocess 验证照常执行）。"""
    _force_enabled(monkeypatch)

    class _FakeDaemonClientMiss:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        def ensure_daemon():
            return True

        @staticmethod
        def infer(task, items, model=None, timeout=None):
            return None
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientMiss())

    out_lines = [json.dumps({"coherence_score": 0.6, "is_coherent": True, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["甲"])
    assert abs(res[0]["coherence_score"] - 0.6) < 1e-9


def test_daemon_flag_off_never_triggers_daemon_client(monkeypatch):
    """RUOYU_NN_DAEMON 关（默认）→ 即便塞一个 enabled() 即炸的假模块，_daemon_infer 的
    try/except 也吞掉异常·predict_batch 无缝落回既有 subprocess 路径（不崩）。"""
    _force_enabled(monkeypatch)

    class _BoomDaemonClient:
        @staticmethod
        def enabled():
            raise AssertionError("不该被有效触发导致崩溃")

    monkeypatch.setitem(sys.modules, "nn_daemon_client", _BoomDaemonClient())

    out_lines = [json.dumps({"coherence_score": 0.4, "is_coherent": False, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["甲"])
    assert abs(res[0]["coherence_score"] - 0.4) < 1e-9


def test_daemon_count_mismatch_falls_back_to_subprocess(monkeypatch):
    """daemon 结果条数与输入不齐 → 视为不可信·回退 subprocess。"""
    _force_enabled(monkeypatch)

    class _FakeDaemonClientShort:
        @staticmethod
        def enabled():
            return True

        @staticmethod
        def ensure_daemon():
            return True

        @staticmethod
        def infer(task, items, model=None, timeout=None):
            return [{"coherence_score": 0.5, "source": "model"}]  # 故意少一条
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientShort())

    out_lines = [
        json.dumps({"coherence_score": 0.2, "is_coherent": False, "source": "model"}),
        json.dumps({"coherence_score": 0.9, "is_coherent": True, "source": "model"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["甲", "乙"])
    assert abs(res[0]["coherence_score"] - 0.2) < 1e-9
    assert abs(res[1]["coherence_score"] - 0.9) < 1e-9


# ---------------- 真 venv 冒烟（门控·慢） ----------------

@pytest.mark.skipif(os.environ.get("RUOYU_RUN_REAL_NN") != "1",
                    reason="需 RUOYU_RUN_REAL_NN=1 跑真模型(慢·本地无 API 花费)")
def test_real_model_smoke():
    os.environ["RUOYU_NN_COHERENCE"] = "1"
    if not mod.enabled():
        pytest.skip("venv / checkpoint 不在·跳过真模型冒烟")
    res = mod.predict_batch([
        "他攥紧了拳头，指节发白，转身朝门口走去。门外的雨还在下。",
        "苹果是一种水果。光速约为每秒三十万公里。他昨天买了一双鞋。",
    ])
    assert res[0] is not None and res[0]["source"] == "model"
    assert res[1] is not None and res[1]["source"] == "model"
