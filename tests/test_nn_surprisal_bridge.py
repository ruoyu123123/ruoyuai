# -*- coding: utf-8 -*-
# 🔴 2026-07-04 Wave-5 daemon-first 接线（补 nn_surprisal_bridge 桥级回归·此前只有 scanner 间接覆盖）
"""nn_surprisal_bridge 回归：默认安全（env off / venv 缺 / ids 长度不齐 / subprocess 失败 / 超时 /
条数失配 / error source 过滤）一律 → None（调用方静默降级·不崩）；mock 模型输出验解析；
daemon-first（Wave-5 常驻推理 daemon）命中/未命中/关闭三态；可选真 venv 冒烟（门控）。

确定性·零网络（mock subprocess）。真模型冒烟需 RUOYU_RUN_REAL_NN=1（慢·本地无 API 花费）。"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import nn_surprisal_bridge as mod  # noqa: E402


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


# ---------------- 默认安全：env off ----------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_SURPRISAL", raising=False)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲", "乙"]) == [None, None]
    assert mod.predict_one("丙") is None


def test_empty_input():
    assert mod.predict_batch([]) == []


# ---------------- 默认安全：先决条件缺 ----------------

def test_missing_venv_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲", "乙"]) == [None, None]


def _force_enabled(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: Path("py.exe"))


def test_ids_length_mismatch_graceful(monkeypatch):
    _force_enabled(monkeypatch)
    assert mod.predict_batch(["甲", "乙"], ids=["only_one"]) == [None, None]


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


# ---------------- 解析：模型 source 命中 ----------------

def test_model_results_parsed(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [
        json.dumps({"id": "para_0000", "mean_surprisal": 5.2, "std_surprisal": 1.1,
                    "max_surprisal": 8.0, "min_surprisal": 2.0, "skewness": 0.3,
                    "kurtosis": -0.1, "token_count": 12, "source": "model"}),
        json.dumps({"id": "para_0001", "mean_surprisal": 3.4, "std_surprisal": 0.9,
                    "max_surprisal": 6.0, "min_surprisal": 1.0, "skewness": 0.1,
                    "kurtosis": 0.0, "token_count": 8, "source": "model"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["长句子甲", "短句乙"])
    assert res[0]["source"] == "model" and abs(res[0]["mean_surprisal"] - 5.2) < 1e-9
    assert res[0]["id"] == "para_0000"
    assert res[1]["token_count"] == 8


def test_custom_ids_roundtrip(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"id": "my_custom_id", "mean_surprisal": 4.0, "std_surprisal": 1.0,
                             "max_surprisal": 5.0, "min_surprisal": 3.0, "skewness": 0.0,
                             "kurtosis": 0.0, "token_count": 5, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["文本"], ids=["my_custom_id"])
    assert res[0]["id"] == "my_custom_id"


def test_error_source_filtered_to_none(monkeypatch):
    """推理条目 source=="error"（该条异常）→ 该条 None（不影响契约·仍是「让调用方静默降级」）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"id": "para_0000", "mean_surprisal": None, "source": "error",
                             "error": "boom"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch(["甲"]) == [None]


def test_empty_source_filtered_to_none(monkeypatch):
    """空文本 → surprisal_infer 标 source=="empty" → None。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"id": "para_0000", "mean_surprisal": None, "source": "empty"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch([""]) == [None]


def test_count_mismatch_graceful(monkeypatch):
    """输出条数 != 输入条数 → 全 None（保序契约破损即回退）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"id": "para_0000", "mean_surprisal": 5.0, "source": "model"})]
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


def test_unexpected_exception_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- predict_one 便捷函数 ----------------

def test_predict_one_delegates_to_batch(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"id": "para_0000", "mean_surprisal": 4.4, "std_surprisal": 1.0,
                             "max_surprisal": 6.0, "min_surprisal": 2.0, "skewness": 0.0,
                             "kurtosis": 0.0, "token_count": 10, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_one("甲")
    assert abs(res["mean_surprisal"] - 4.4) < 1e-9


# ---------------- daemon-first（Wave-5 常驻推理 daemon·~0.1s） ----------------

def test_daemon_hit_bypasses_subprocess(monkeypatch):
    """daemon 命中 → 直接用其结果（本地 ids 补 id 字段）·绝不碰 subprocess（mock 为炸弹断言零调用）。"""
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
            assert task == "surprisal"
            assert items == ["长句子甲", "短句乙"]
            # daemon 侧裸 predict_batch 结果本就不带 id（见 model_daemon._infer_surprisal）
            return [
                {"mean_surprisal": 5.2, "std_surprisal": 1.1, "max_surprisal": 8.0,
                 "min_surprisal": 2.0, "skewness": 0.3, "kurtosis": -0.1,
                 "token_count": 12, "source": "model"},
                {"mean_surprisal": 3.4, "std_surprisal": 0.9, "max_surprisal": 6.0,
                 "min_surprisal": 1.0, "skewness": 0.1, "kurtosis": 0.0,
                 "token_count": 8, "source": "model"},
            ]
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientHit())

    def _boom(*a, **k):
        raise AssertionError("daemon 命中时不应调用 subprocess")
    monkeypatch.setattr(mod.subprocess, "run", _boom)

    res = mod.predict_batch(["长句子甲", "短句乙"])
    assert abs(res[0]["mean_surprisal"] - 5.2) < 1e-9
    assert res[0]["id"] == "para_0000"   # daemon 结果本身无 id·桥按位置补本地 ids
    assert res[1]["id"] == "para_0001"


def test_daemon_hit_with_custom_ids(monkeypatch):
    """daemon 命中 + 调用方自带 ids → id 字段用调用方传入的值（而非默认 para_XXXX）。"""
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
            return [{"mean_surprisal": 4.0, "std_surprisal": 1.0, "max_surprisal": 5.0,
                     "min_surprisal": 3.0, "skewness": 0.0, "kurtosis": 0.0,
                     "token_count": 5, "source": "model"}]
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientHit())
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用 subprocess")))

    res = mod.predict_batch(["文本"], ids=["my_custom_id"])
    assert res[0]["id"] == "my_custom_id"


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

    out_lines = [json.dumps({"id": "para_0000", "mean_surprisal": 7.0, "std_surprisal": 1.0,
                             "max_surprisal": 9.0, "min_surprisal": 5.0, "skewness": 0.0,
                             "kurtosis": 0.0, "token_count": 20, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["甲"])
    assert abs(res[0]["mean_surprisal"] - 7.0) < 1e-9


def test_daemon_flag_off_never_triggers_daemon_client(monkeypatch):
    """RUOYU_NN_DAEMON 关（默认）→ 即便塞一个 enabled() 即炸的假模块，_daemon_infer 的
    try/except 也吞掉异常·predict_batch 无缝落回既有 subprocess 路径（不崩）。"""
    _force_enabled(monkeypatch)

    class _BoomDaemonClient:
        @staticmethod
        def enabled():
            raise AssertionError("不该被有效触发导致崩溃")

    monkeypatch.setitem(sys.modules, "nn_daemon_client", _BoomDaemonClient())

    out_lines = [json.dumps({"id": "para_0000", "mean_surprisal": 3.0, "std_surprisal": 1.0,
                             "max_surprisal": 4.0, "min_surprisal": 2.0, "skewness": 0.0,
                             "kurtosis": 0.0, "token_count": 6, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["甲"])
    assert abs(res[0]["mean_surprisal"] - 3.0) < 1e-9


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
            return [{"mean_surprisal": 1.0, "source": "model"}]  # 故意少一条
    monkeypatch.setitem(sys.modules, "nn_daemon_client", _FakeDaemonClientShort())

    out_lines = [
        json.dumps({"id": "para_0000", "mean_surprisal": 5.0, "std_surprisal": 1.0,
                    "max_surprisal": 6.0, "min_surprisal": 4.0, "skewness": 0.0,
                    "kurtosis": 0.0, "token_count": 10, "source": "model"}),
        json.dumps({"id": "para_0001", "mean_surprisal": 6.0, "std_surprisal": 1.0,
                    "max_surprisal": 7.0, "min_surprisal": 5.0, "skewness": 0.0,
                    "kurtosis": 0.0, "token_count": 11, "source": "model"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["甲", "乙"])
    assert abs(res[0]["mean_surprisal"] - 5.0) < 1e-9
    assert abs(res[1]["mean_surprisal"] - 6.0) < 1e-9


# ---------------- 真 venv 冒烟（门控·慢） ----------------

@pytest.mark.skipif(os.environ.get("RUOYU_RUN_REAL_NN") != "1",
                    reason="需 RUOYU_RUN_REAL_NN=1 跑真模型(慢·本地无 API 花费)")
def test_real_model_smoke():
    os.environ["RUOYU_NN_SURPRISAL"] = "1"
    if not mod.enabled():
        pytest.skip("venv 不在·跳过真模型冒烟")
    res = mod.predict_batch(["今天天气不错，我们去公园散步吧。", "研究表明神经网络推理存在固有的不确定性边界。"])
    assert res[0] is not None and res[0]["source"] == "model"
    assert res[1] is not None and res[1]["source"] == "model"
