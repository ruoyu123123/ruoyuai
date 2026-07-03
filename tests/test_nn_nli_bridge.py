# -*- coding: utf-8 -*-
# 🔴 2026-07-03 中文NLI蕴含桥
"""nn_nli_bridge 回归：默认安全（env off / venv 缺 / ckpt 缺 / subprocess 失败 / 超时 / 条数失配 /
unavailable·error source 过滤）一律 → None（调用方回退字面判断·不崩）；mock 模型输出验解析 + 归一化；
可选真 venv 冒烟（门控·用真下载的 IDEA-CCNL/Erlangshen-Roberta-110M-NLI checkpoint）。

确定性·零网络（mock subprocess）。真模型冒烟需 RUOYU_RUN_REAL_NN=1（慢·本地无 API 花费）。"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import nn_nli_bridge as mod  # noqa: E402


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


def _pair(premise="张三把钥匙给了李四", hypothesis="李四拿到了钥匙"):
    return {"premise": premise, "hypothesis": hypothesis}


# ---------------- 默认安全：env off ----------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RUOYU_NN_NLI", raising=False)
    assert mod.enabled() is False
    assert mod.predict_batch([_pair(), _pair()]) == [None, None]
    assert mod.predict_one("甲", "乙") is None
    assert mod.entails("甲", "乙") is None


def test_empty_input():
    assert mod.predict_batch([]) == []


# ---------------- 默认安全：先决条件缺 ----------------

def test_missing_venv_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch([_pair(), _pair()]) == [None, None]


def test_missing_ckpt_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(mod, "_resolve_ckpt", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch([_pair()]) == [None]


# ---------------- 默认安全：subprocess 失败 / 超时 ----------------

def _force_enabled(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: Path("py.exe"))
    monkeypatch.setattr(mod, "_resolve_ckpt", lambda: "ckpt")


def test_subprocess_timeout_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def boom(*a, **k):
        raise mod.subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.predict_batch([_pair(), _pair()]) == [None, None]


def test_subprocess_nonzero_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def fail(cmd, capture_output=True, timeout=None, env=None):
        class _R:
            returncode = 1
            stderr = b"boom"
            stdout = b""
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", fail)
    assert mod.predict_batch([_pair()]) == [None]


# ---------------- 解析：模型 source 命中 + 归一化 ----------------

def test_model_results_parsed_and_normalized(monkeypatch):
    """nli_infer 输出大写 id2label(CONTRADICTION/NEUTRAL/ENTAILMENT) → 桥归一化小写 key。"""
    _force_enabled(monkeypatch)
    out_lines = [
        json.dumps({"label": "ENTAILMENT",
                    "probs": {"CONTRADICTION": 0.05, "NEUTRAL": 0.1, "ENTAILMENT": 0.85},
                    "source": "nli"}),
        json.dumps({"label": "CONTRADICTION",
                    "probs": {"CONTRADICTION": 0.9, "NEUTRAL": 0.05, "ENTAILMENT": 0.05},
                    "source": "nli"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch([_pair(), _pair("张三把钥匙给了李四", "李四没有钥匙")])
    assert res[0]["label"] == "entailment"
    assert abs(res[0]["probs"]["entailment"] - 0.85) < 1e-9
    assert set(res[0]["probs"].keys()) == {"contradiction", "neutral", "entailment"}
    assert res[1]["label"] == "contradiction"
    assert res[0]["source"] == "nli"


def test_unavailable_source_filtered_to_none(monkeypatch):
    """nli_infer 整体不可用(source=unavailable) → 桥返回 None（调用方回退字面判断）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": None, "probs": {}, "source": "unavailable"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch([_pair()]) == [None]


def test_error_source_filtered_to_none(monkeypatch):
    """单条推理异常(source=error) → 该条 None（不影响契约·仍是「让调用方用自己的判断」）。"""
    _force_enabled(monkeypatch)
    out_lines = [
        json.dumps({"label": "entailment", "probs": {"ENTAILMENT": 0.9, "NEUTRAL": 0.05, "CONTRADICTION": 0.05},
                    "source": "nli"}),
        json.dumps({"label": None, "probs": {}, "source": "error"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch([_pair(), _pair()])
    assert res[0] is not None
    assert res[1] is None


def test_count_mismatch_graceful(monkeypatch):
    """输出条数 != 输入条数 → 全 None（保序契约破损即回退）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": "entailment",
                             "probs": {"ENTAILMENT": 0.9, "NEUTRAL": 0.05, "CONTRADICTION": 0.05},
                             "source": "nli"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch([_pair(), _pair()]) == [None, None]


def test_missing_label_filtered_to_none(monkeypatch):
    """source=nli 但 label 缺失/空 → 视为不可信解析·None。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": None, "probs": {"ENTAILMENT": 0.5}, "source": "nli"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch([_pair()]) == [None]


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
    assert mod.predict_batch([_pair()]) == [None]


# ---------------- predict_one / entails 便捷函数 ----------------

def test_predict_one_delegates_to_batch(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": "ENTAILMENT",
                             "probs": {"ENTAILMENT": 0.7, "NEUTRAL": 0.2, "CONTRADICTION": 0.1},
                             "source": "nli"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_one("甲", "乙")
    assert res["label"] == "entailment"


def test_entails_true_above_threshold(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": "ENTAILMENT",
                             "probs": {"ENTAILMENT": 0.85, "NEUTRAL": 0.1, "CONTRADICTION": 0.05},
                             "source": "nli"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.entails("张三把钥匙给了李四", "李四拿到了钥匙") is True


def test_entails_false_when_label_not_entailment(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": "CONTRADICTION",
                             "probs": {"ENTAILMENT": 0.05, "NEUTRAL": 0.05, "CONTRADICTION": 0.9},
                             "source": "nli"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.entails("张三把钥匙给了李四", "李四没有钥匙") is False


def test_entails_false_when_below_threshold(monkeypatch):
    """label=entailment 但置信度低于阈值 → False（不是 None——模型确有判断只是不够自信）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"label": "ENTAILMENT",
                             "probs": {"ENTAILMENT": 0.4, "NEUTRAL": 0.35, "CONTRADICTION": 0.25},
                             "source": "nli"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.entails("甲", "乙", threshold=0.6) is False


def test_entails_none_when_bridge_unavailable(monkeypatch):
    """门控关闭 → None（调用方必须区分 None≠False，绝不能当「不蕴含」处理）。"""
    monkeypatch.delenv("RUOYU_NN_NLI", raising=False)
    assert mod.entails("甲", "乙") is None


# ---------------- 异常安全 ----------------

def test_unexpected_exception_graceful(monkeypatch):
    _force_enabled(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.predict_batch([_pair()]) == [None]


# ---------------- 真 venv 冒烟（门控·慢·用真下载的 checkpoint） ----------------

@pytest.mark.skipif(os.environ.get("RUOYU_RUN_REAL_NN") != "1",
                    reason="需 RUOYU_RUN_REAL_NN=1 跑真模型(慢·本地无 API 花费)")
def test_real_model_smoke():
    os.environ["RUOYU_NN_NLI"] = "1"
    if not mod.enabled():
        pytest.skip("venv / checkpoint 不在·跳过真模型冒烟")
    res = mod.predict_batch([
        {"premise": "张三把钥匙给了李四", "hypothesis": "李四拿到了钥匙"},   # entailment
        {"premise": "张三把钥匙给了李四", "hypothesis": "李四没有钥匙"},     # contradiction
        {"premise": "张三把钥匙给了李四", "hypothesis": "今天天气很好"},     # neutral
    ])
    assert res[0] is not None and res[0]["label"] == "entailment"
    assert res[1] is not None and res[1]["label"] == "contradiction"
    assert res[2] is not None and res[2]["label"] == "neutral"
