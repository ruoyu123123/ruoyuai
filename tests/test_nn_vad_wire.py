# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN情绪VAD集成
"""3 处 wire 落点回归（mock 桥·零网络·零 torch）：
  A. save_state.cmd_apply_appraisal_beats — vad_bin 的 V/A 由模型覆盖·D 保留 summarizer
  B. character_vad_ued_scanner._score_vad / _nn_vad_prime — 占位词典→模型(失败兜底词典)
  C. emotion_curve_rescan.segment_valence_curve — 关键词 valence→模型真 valence

每处都验：① NN off（默认）→ 行为完全不变（零回归）；② NN on + mock 桥命中模型 → 用模型读数。
桥用 monkeypatch 替身（不 spawn subprocess·不依赖 venv）。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import nn_vad_bridge  # noqa: E402
import save_state as ss  # noqa: E402
import character_vad_ued_scanner as cv  # noqa: E402
import emotion_curve_rescan_scanner as ec  # noqa: E402


def _fake_model(valence, arousal, dominance=None):
    def f(texts, timeout=None):
        return [{"valence": valence, "arousal": arousal, "dominance": dominance, "source": "model"}
                for _ in texts]
    return f


# ═══════════════ A. save_state appraisal vad_bin 重算 ═══════════════

def _mk_project(tmp: Path, summary, pacer):
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    (db / "叙事节拍器.json").write_text(json.dumps(pacer, ensure_ascii=False), encoding="utf-8")
    (db / ".wal" / "cluster_001_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return tmp


def _beat():
    return {"cluster_id": "cluster_001", "scene_idx": 0, "focal_character": "C_PROT",
            "trigger_event": "他发现遗嘱是未来的自己写的",
            "derived_emotion": "地面塌陷的眩晕", "behavior_externalization": "抠信纸边角",
            "vad_bin": {"valence": "H", "arousal": "VL", "dominance": "M"}}


_PACER = {"schema_version": "v27", "rhythm_profile": "混合", "beat_targets": [], "appraisal_beats": []}


def _read_pacer(root):
    return json.loads((Path(root) / "_数据库" / "叙事节拍器.json").read_text(encoding="utf-8"))


def test_save_state_nn_off_vad_bin_unchanged(monkeypatch):
    """NN off（默认）→ vad_bin 完全保留 summarizer 手判·无 _source（零回归）。"""
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), {"appraisal_beats": [_beat()]}, dict(_PACER))
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _read_pacer(root)["appraisal_beats"]
        assert ab[0]["vad_bin"] == {"valence": "H", "arousal": "VL", "dominance": "M"}
        assert "_source" not in ab[0]["vad_bin"]


def test_save_state_nn_on_recomputes_va_keeps_d(monkeypatch):
    """NN on + mock 模型(V=0.1→VL·A=0.85→VH) → vad_bin.V/A 用模型·D('M')保留·标 _source。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", _fake_model(0.1, 0.85, None))
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), {"appraisal_beats": [_beat()]}, dict(_PACER))
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        vb = _read_pacer(root)["appraisal_beats"][0]["vad_bin"]
        assert vb["valence"] == "VL"          # 模型 0.1
        assert vb["arousal"] == "VH"          # 模型 0.85
        assert vb["dominance"] == "M"         # summarizer D 保留
        assert vb["_source"] == "model_va+summarizer_d"


def test_save_state_nn_on_bridge_miss_falls_back(monkeypatch):
    """NN on 但桥返回 None（模型不可用）→ 保留 summarizer vad_bin（兜底·不崩）。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", lambda texts, timeout=None: [None for _ in texts])
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), {"appraisal_beats": [_beat()]}, dict(_PACER))
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        vb = _read_pacer(root)["appraisal_beats"][0]["vad_bin"]
        assert vb == {"valence": "H", "arousal": "VL", "dominance": "M"}


# ═══════════════ B. character_vad_ued_scanner._score_vad ═══════════════

def test_character_vad_nn_off_lexicon(monkeypatch):
    """NN off → _score_vad 走占位/真词典（零回归·返回 3 元组）。"""
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    cv._NN_VAD_CACHE.clear()
    r = cv._score_vad("幸福快乐")
    assert r is not None and len(r) == 3


def test_character_vad_nn_on_uses_model(monkeypatch):
    """NN on + mock 模型(V=0.8,A=0.6,D=None) → _nn_vad_prime 缓存·_score_vad 返模型 V/A + 词典 D。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    cv._NN_VAD_CACHE.clear()
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", _fake_model(0.8, 0.6, None))
    cv._nn_vad_prime(["开心快乐"])
    r = cv._score_vad("开心快乐")
    assert r is not None
    assert abs(r[0] - 0.8) < 1e-9      # 模型 valence
    assert abs(r[1] - 0.6) < 1e-9      # 模型 arousal
    assert 0.0 <= r[2] <= 1.0          # 词典 D 兜底（模型为 VA）
    cv._NN_VAD_CACHE.clear()


def test_character_vad_nn_on_miss_falls_back(monkeypatch):
    """NN on 但桥未命中（None）→ 不缓存·_score_vad 退词典（不崩）。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    cv._NN_VAD_CACHE.clear()
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", lambda texts, timeout=None: [None for _ in texts])
    cv._nn_vad_prime(["幸福快乐"])
    assert "幸福快乐" not in cv._NN_VAD_CACHE
    r = cv._score_vad("幸福快乐")
    assert r is not None and len(r) == 3   # 词典兜底
    cv._NN_VAD_CACHE.clear()


def test_compute_signature_primes_and_uses_model(monkeypatch):
    """compute_vad_ued_signature 入口批量预热 → 全角色 utterance 用模型读数。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    cv._NN_VAD_CACHE.clear()
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", _fake_model(0.3, 0.7, None))
    utts = [{"speaker": "小王", "text": f"句子{i}哈哈"} for i in range(8)]
    out = cv.compute_vad_ued_signature(utts)
    assert "小王" in out                  # UED 算出
    # 每条 utterance 都进了模型缓存
    assert all(f"句子{i}哈哈" in cv._NN_VAD_CACHE for i in range(8))
    cv._NN_VAD_CACHE.clear()


# ═══════════════ C. emotion_curve_rescan.segment_valence_curve ═══════════════

_TEXT_10 = "\n\n".join(f"这是第{i}段平凡的叙述。" for i in range(10))


def test_emotion_curve_nn_off_keyword(monkeypatch):
    """NN off → segment_valence_curve 走关键词（无情绪词→0.5·零回归）。"""
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    curve = ec.segment_valence_curve(_TEXT_10, 10)
    assert len(curve) == 10
    assert all(v == 0.5 for v in curve)   # 无情绪关键词 → 中性


def test_emotion_curve_nn_on_uses_model(monkeypatch):
    """NN on + mock 模型(valence=0.123) → 每段取模型真 valence。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", _fake_model(0.123, 0.5, None))
    curve = ec.segment_valence_curve(_TEXT_10, 10)
    assert len(curve) == 10
    assert all(abs(v - 0.123) < 1e-9 for v in curve)


def test_emotion_curve_nn_on_miss_falls_back(monkeypatch):
    """NN on 但桥全 None → 退关键词（不崩·零回归）。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setattr(nn_vad_bridge, "predict_batch", lambda texts, timeout=None: [None for _ in texts])
    curve = ec.segment_valence_curve(_TEXT_10, 10)
    assert len(curve) == 10
    assert all(v == 0.5 for v in curve)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
