# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN情绪VAD集成
"""nn_vad_bridge 回归：默认安全（env off / venv 缺 / ckpt 缺 / subprocess 失败 / 超时 / 条数失配 /
词典 source 过滤）一律 → None（调用方回退启发式·不崩）；mock 模型输出验解析；可选真 venv 冒烟（门控）。

确定性·零网络（mock subprocess）。真模型冒烟需 RUOYU_RUN_REAL_NN=1（慢·本地无 API 花费）。"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import nn_vad_bridge as mod  # noqa: E402


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
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲", "乙"]) == [None, None]
    assert mod.predict_one("丙") is None


def test_empty_input():
    assert mod.predict_batch([]) == []


# ---------------- 默认安全：先决条件缺 ----------------

def test_missing_venv_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲", "乙"]) == [None, None]


def test_missing_ckpt_graceful(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(mod, "_resolve_ckpt", lambda: None)
    assert mod.enabled() is False
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- 默认安全：subprocess 失败 / 超时 ----------------

def _force_enabled(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(mod, "_venv_python", lambda: Path("py.exe"))
    monkeypatch.setattr(mod, "_resolve_ckpt", lambda: "ckpt")


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
        json.dumps({"valence": 0.2, "arousal": 0.8, "dominance": None, "source": "model"}),
        json.dumps({"valence": 0.9, "arousal": 0.3, "dominance": None, "source": "model"}),
    ]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    res = mod.predict_batch(["怒", "喜"])
    assert res[0]["source"] == "model" and abs(res[0]["valence"] - 0.2) < 1e-9
    assert abs(res[1]["arousal"] - 0.3) < 1e-9


def test_lexicon_source_filtered_to_none(monkeypatch):
    """vad_infer 退词典模式(source!=model) → 桥返回 None（让调用方用自己的启发式）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"valence": 0.5, "arousal": 0.5, "dominance": None, "source": "lexicon"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch(["甲"]) == [None]


def test_count_mismatch_graceful(monkeypatch):
    """输出条数 != 输入条数 → 全 None（保序契约破损即回退）。"""
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"valence": 0.2, "arousal": 0.8, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch(["甲", "乙"]) == [None, None]


def test_null_valence_filtered(monkeypatch):
    _force_enabled(monkeypatch)
    out_lines = [json.dumps({"valence": None, "arousal": None, "source": "model"})]
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(out_lines))
    assert mod.predict_batch(["甲"]) == [None]


# ---------------- binning 工具 ----------------

def test_to_vad_bin():
    b = mod.to_vad_bin(0.1, 0.85, None)
    assert b["valence"] == "VL"
    assert b["arousal"] == "VH"
    assert b["dominance"] is None
    b2 = mod.to_vad_bin(0.5, 0.5, 0.5)
    assert b2["valence"] == "M" and b2["dominance"] == "M"


# ---------------- 真 venv 冒烟（门控·慢） ----------------

@pytest.mark.skipif(os.environ.get("RUOYU_RUN_REAL_NN") != "1",
                    reason="需 RUOYU_RUN_REAL_NN=1 跑真模型(慢·本地无 API 花费)")
def test_real_model_smoke():
    os.environ["RUOYU_NN_VAD"] = "1"
    if not mod.enabled():
        pytest.skip("venv / checkpoint 不在·跳过真模型冒烟")
    res = mod.predict_batch(["他怒火中烧，攥紧了拳头，指节发白", "她笑了笑，心里满是幸福和喜悦"])
    assert res[0] is not None and res[0]["source"] == "model"
    assert res[1] is not None and res[1]["source"] == "model"
    # 愤怒 valence 应显著低于幸福 valence（模型语义正确性）
    assert res[0]["valence"] < res[1]["valence"]
    # 愤怒 arousal 应高
    assert res[0]["arousal"] > 0.5
