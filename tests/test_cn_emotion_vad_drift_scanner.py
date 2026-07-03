# -*- coding: utf-8 -*-
"""cn_emotion_vad_drift_scanner R24 W12 Batch-KK · P1

🔴 2026-07-01 NN情绪VAD集成回归：signal A(_signal_a_drift/_model_va_for_words) 的 source
归因·核心断言=NN 开启但桥未命中时输出必须与 NN 完全关闭时逐字节一致(零回归)。
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cn_emotion_vad_drift_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CN_EMOTION_VAD_MODE", None)
    else:
        os.environ["CN_EMOTION_VAD_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_baseline(cultural_density=2.0, clear_amb_ratio=2.0):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    style = {
        "cn_emotion_baseline": {
            "cultural_density": cultural_density,
            "clear_ambivalent_ratio": clear_amb_ratio,
        }
    }
    (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False),
                                       encoding="utf-8")
    return proj


_FILLER = "他静静地走在路上，望着前方未明的去路，思绪散落如雪。\n\n" * 25


def test_off_returns_skeleton():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_FILLER))
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"))
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_no_emotion_words_emits_info():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 全是中性叙述 · 无情感词
        text = "他走在路上，路是直的，天是蓝的，云是白的。" * 100
        out = mod.scan(_write(text))
        codes = {v["code"] for v in out.get("violations", [])}
        assert "CN_EMOTION_NO_EMOTION_WORDS" in codes
    finally:
        _set_mode(bak)


def test_anglo_drift_signal():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 仅用 dv 同向词（悲愤+窘迫+惆怅 在 CVAW vs NRC 都是 +0.20）平均突破阈值
        # 避免不同词差正负相消（占位字典本身 design 选了多方向词以求接近真实）
        text = ("他悲愤地看着窘迫的对方，只剩惆怅。\n\n") * 80
        out = mod.scan(_write(text))
        codes = {v["code"] for v in out.get("violations", [])}
        # 至少有命中 drift_words
        assert out["signal_a_anglo_drift"]["drift_words"]
        # 占位词典设的差是显著的 → ANGLO_DRIFT 应触发
        assert "CN_EMOTION_ANGLO_DRIFT" in codes
    finally:
        _set_mode(bak)


def test_cultural_underuse_signal():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 拉高基线到 200 → 草稿密度需<120 / 千 才触发 UNDERUSE
        proj = _mk_project_with_baseline(cultural_density=200.0)
        # 极少量文化特有词（仅 1 处 含蓄）+ 大量非情感铺垫文
        text = ("天空很蓝，云朵很白，他往前走着，景色不错，一切都很普通。\n\n"
                "他抬头看了一眼，又低下头，继续往前走。\n\n") * 80
        text += "他含蓄地点头。\n\n"  # 仅 1 处
        out = mod.scan(_write(text), project_root=proj)
        codes = {v["code"] for v in out.get("violations", [])}
        # 触发 UNDERUSE（密度<60% 基线）
        assert "CN_EMOTION_CULTURAL_UNDERUSE" in codes
    finally:
        _set_mode(bak)


def test_binary_polarization_signal():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        # 拉低基线 → 草稿 clear/ambivalent 比例显著
        proj = _mk_project_with_baseline(clear_amb_ratio=0.5)
        text = ("他大笑起来，又狂怒地暴怒起来，最后嚎啕痛哭。\n\n"
                "她狂喜，他狂怒。\n\n") * 60
        out = mod.scan(_write(text), project_root=proj)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "CN_EMOTION_BINARY_POLARIZATION" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violations():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("shadow")
        text = ("他悲愤地看着窘迫的对方，雀跃的心情消失了，只剩惆怅。\n\n") * 30
        out = mod.scan(_write(text))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_placeholder_lexicons():
    assert mod._CVAW_V2["_placeholder"] is True
    assert mod._NRC_VAD_CN["_placeholder"] is True
    assert mod._CULTURAL_SPECIFIC["_placeholder"] is True


def test_load_baseline_default():
    base = mod._load_baseline(None)
    assert base["_source"] == "default"
    assert base["cultural_density"] == mod.DEFAULT_BASELINE_CULTURAL_DENSITY


def test_load_baseline_from_project():
    proj = _mk_project_with_baseline(cultural_density=5.0, clear_amb_ratio=3.0)
    base = mod._load_baseline(proj)
    assert base["cultural_density"] == 5.0
    assert base["clear_ambivalent_ratio"] == 3.0


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_codes_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("CN_EMOTION_ANGLO_DRIFT", "CN_EMOTION_CULTURAL_UNDERUSE",
              "CN_EMOTION_BINARY_POLARIZATION",
              "CN_EMOTION_NO_EMOTION_WORDS", "CN_EMOTION_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("cn_emotion_vad_drift_scanner")
    assert s is not None
    assert s.get("_new") is True


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_signal_a_no_emotion_returns_zero():
    """无 CVAW 命中 → avg_dv/avg_da=0"""
    out = mod._signal_a_drift("天空很蓝，云朵飘飘。")
    assert out["avg_dv"] == 0.0
    assert out["avg_da"] == 0.0
    assert out["drift_words"] == []


def test_audit_hub_integrates_scanner():
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "cn_emotion_vad_drift_scanner" in src
    assert "CN_EMOTION_ANGLO_DRIFT" in src


# ═══════════════ 🔴 2026-07-01 NN情绪VAD集成回归(signal A) ═══════════════

_ANGLO_DRIFT_TEXT = ("他悲愤地看着窘迫的对方，只剩惆怅。\n\n") * 80


def test_model_va_for_words_off_by_default(monkeypatch):
    """env 未开(默认) → _model_va_for_words 空 dict·不碰 FeatureStore/nn_vad_bridge。"""
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    assert mod._model_va_for_words("他悲愤地看着窘迫的对方。", ["悲愤", "窘迫"]) == {}


def test_model_va_for_words_empty_words_list():
    assert mod._model_va_for_words("随便文本", []) == {}


def test_model_va_for_words_model_source_when_enabled(monkeypatch):
    """env 开 + 桥命中模型 → 每个命中词返回窗口平均 (v, a)。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [
        {"valence": 0.42, "arousal": 0.24, "dominance": None, "source": "model"} for _ in texts
    ])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    out = mod._model_va_for_words("他悲愤地看着窘迫的对方，只剩惆怅。", ["悲愤", "窘迫", "惆怅"])
    assert set(out.keys()) == {"悲愤", "窘迫", "惆怅"}
    for w in out:
        assert abs(out[w][0] - 0.42) < 1e-9
        assert abs(out[w][1] - 0.24) < 1e-9


def test_model_va_for_words_bridge_miss_returns_empty(monkeypatch):
    """env 开但桥未命中(全 None) → 空 dict(调用方回退占位坐标)。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    assert mod._model_va_for_words("他悲愤地看着窘迫的对方。", ["悲愤", "窘迫"]) == {}


def test_model_va_for_words_no_hit_skips_model_call(monkeypatch):
    """词典词未在文中出现 → 窗口为空 → 根本不调模型(省一次 subprocess)。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    calls = []

    def fake_predict(texts, timeout=None):
        calls.append(texts)
        return [{"valence": 0.5, "arousal": 0.5, "dominance": None, "source": "model"} for _ in texts]

    monkeypatch.setitem(sys.modules, "nn_vad_bridge", types.SimpleNamespace(predict_batch=fake_predict))
    out = mod._model_va_for_words("天气真好，风和日丽。", ["悲愤"])
    assert out == {}
    assert calls == []


def test_signal_a_drift_model_unavailable_matches_lexicon_baseline(monkeypatch):
    """🔴 零回归核心断言：NN 开启但桥返回 None → _signal_a_drift 须与 NN 完全关闭时逐字节一致。"""
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    baseline = mod._signal_a_drift(_ANGLO_DRIFT_TEXT)
    assert baseline["drift_words"], "fixture 应至少命中词典(否则本测试无意义)"
    assert baseline["source"] == "lexicon_fallback"

    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    r_none = mod._signal_a_drift(_ANGLO_DRIFT_TEXT)
    assert r_none == baseline


def test_signal_a_drift_model_source_when_enabled(monkeypatch):
    """env 开 + 桥命中模型 → CVAW 占位坐标一侧被模型读数取代·NRC 一侧不变·source 标 model_vad。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [
        {"valence": 0.9, "arousal": 0.9, "dominance": None, "source": "model"} for _ in texts
    ])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    r = mod._signal_a_drift(_ANGLO_DRIFT_TEXT)
    assert r["source"] == "model_vad"
    assert r["drift_words"]
    assert all(h["source"] == "model_vad" for h in r["drift_words"])
    # 按「模型读数替换 CVAW 一侧·NRC 一侧不变」口径重算期望的加权 avg_dv/avg_da 自洽校验
    nrc = mod._NRC_VAD_CN["_words"]
    counts = [h["count"] for h in r["drift_words"]]
    total = sum(counts)
    expect_dv = round(sum((0.9 - nrc[h["word"]][0]) * h["count"] for h in r["drift_words"]) / total, 4)
    expect_da = round(sum((0.9 - nrc[h["word"]][1]) * h["count"] for h in r["drift_words"]) / total, 4)
    assert r["avg_dv"] == expect_dv
    assert r["avg_da"] == expect_da


def test_signal_a_drift_no_match_returns_none_source():
    """无命中词 → source='none'(与原 avg_dv/avg_da=0.0 行为一致，新增字段不影响该分支)。"""
    r = mod._signal_a_drift("天气真好，风和日丽。")
    assert r == {"avg_dv": 0.0, "avg_da": 0.0, "drift_words": [], "source": "none"}


def test_scan_signal_a_source_surfaces_model_vad(monkeypatch):
    """scan() 顶层 signal_a_anglo_drift 透传 source 字段(供训练数据归因)。"""
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        monkeypatch.setenv("RUOYU_NN_VAD", "1")
        fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [
            {"valence": 0.9, "arousal": 0.9, "dominance": None, "source": "model"} for _ in texts
        ])
        monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
        out = mod.scan(_write(_ANGLO_DRIFT_TEXT))
        assert out["signal_a_anglo_drift"]["source"] == "model_vad"
    finally:
        _set_mode(bak)


def test_scan_model_unavailable_matches_lexicon_baseline(monkeypatch):
    """🔴 零回归核心断言：NN 开启但桥返回 None → scan() 完整输出须与 NN 完全关闭时逐字节一致。"""
    bak = os.environ.get("CN_EMOTION_VAD_MODE")
    try:
        _set_mode("active")
        draft = _write(_ANGLO_DRIFT_TEXT)
        monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
        baseline = mod.scan(draft)
        assert baseline["signal_a_anglo_drift"]["drift_words"]

        monkeypatch.setenv("RUOYU_NN_VAD", "1")
        fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
        monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
        with_model_unavailable = mod.scan(draft)
        assert with_model_unavailable == baseline
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate_after_nn_integration():
    """确认 NN 集成后 5 个 issue code 仍不在 hard_gate 清单(北极星⑤守卫·与已有守卫测试重复保险)。"""
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE_ANGLO_DRIFT not in hgs
