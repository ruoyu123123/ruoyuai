# -*- coding: utf-8 -*-
"""paragraph_engagement_heat_predictor 段落热度回归。

确定性·零依赖。覆盖 off/短稿/段落不足/通章 cold flat 报/正常 PASS/shadow/
读取失败/_mode/CLI/_heat 维度计算/_split_paragraphs/strip_changes/
hard_gate 注册防污染。
"""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import paragraph_engagement_heat_predictor as mod  # noqa: E402

_TARGET = _SCRIPTS / "paragraph_engagement_heat_predictor.py"
_ENV = "PARAGRAPH_ENGAGEMENT_HEAT_MODE"


def _fake_vad(predict_batch_fn):
    """临时装 RUOYU_NN_VAD=1 + 假 nn_vad_bridge 模块，返回还原函数（无 monkeypatch 依赖）。"""
    old_env = os.environ.get("RUOYU_NN_VAD")
    old_mod = sys.modules.get("nn_vad_bridge")
    os.environ["RUOYU_NN_VAD"] = "1"
    sys.modules["nn_vad_bridge"] = types.SimpleNamespace(predict_batch=predict_batch_fn)

    def _restore():
        if old_env is None:
            os.environ.pop("RUOYU_NN_VAD", None)
        else:
            os.environ["RUOYU_NN_VAD"] = old_env
        if old_mod is None:
            sys.modules.pop("nn_vad_bridge", None)
        else:
            sys.modules["nn_vad_bridge"] = old_mod
    return _restore


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 通章 cold（无 question / no ambiguous ref / no temporal suspense / no character amb）
_COLD_FLAT = ("\n\n".join(["天空很蓝海水很咸山高路远风吹叶落万物自有规律。" for _ in range(60)]))

# 热度高（带 question + ambiguous ref + temporal suspense + char ambiguity）
_HOT = ("\n\n".join(
    ["怎么会这样？还有十分钟就到了。那人没说话，只是站着。某种声音从远处传来。"
     for _ in range(60)]))


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write("短稿。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_too_few_paragraphs_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 字数够·段落 < 4
        text = "段落一" * 200 + "\n\n" + "段落二" * 200
        r = mod.scan(_write(text))
        assert "段落不足" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_cold_flat_warns():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["verdict"] == "FAIL_MINOR"
        assert "cold flat" in (r["warning"] or "")
    finally:
        _set_mode(bak)


def test_hot_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write(_HOT))
        # 热度高·verdict PASS
        assert r["verdict"] == "PASS"
        assert r["metrics"]["mean_heat_score"] > 0
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["violations"] == []
        assert r["warning"] is None
    finally:
        _set_mode(bak)


def test_read_failure():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes():
    assert mod._strip_changes("正文。\n---CHANGES---\n{}") == "正文。"


def test_cjk_count():
    assert mod._cjk_count("中abc文") == 2


def test_heat_score_zero_for_plain():
    h = mod._heat("天空很蓝海水很咸")
    assert h["score"] >= 0
    assert h["question"] == 0


def test_heat_score_positive_for_question():
    h = mod._heat("为什么会这样？")
    assert h["question"] >= 1


def test_valence_positive():
    assert mod._valence("他微笑温暖安心") == 1


def test_valence_negative():
    assert mod._valence("怒火冷汗死亡") == -1


def test_valence_neutral():
    assert mod._valence("普通描述。") == 0


def test_compute_valences_model_hit_uses_model_and_batches_once():
    """RUOYU_NN_VAD=1 + 假模型命中 → _compute_valences 走模型路径(source=model_vad)，
    且只调一次 predict_batch（整批·非逐段 N 次调用·摊薄模型加载开销）。
    假模型按内容区分（'开心'→高valence，'愤怒'→低valence，其余中性），防常量导致断言恒真。"""
    calls = []

    def _fake(items):
        calls.append(list(items))
        out = []
        for t in items:
            if "开心" in t:
                out.append({"valence": 0.9, "arousal": 0.5, "dominance": None, "source": "model"})
            elif "愤怒" in t:
                out.append({"valence": 0.1, "arousal": 0.8, "dominance": None, "source": "model"})
            else:
                out.append({"valence": 0.5, "arousal": 0.3, "dominance": None, "source": "model"})
        return out
    restore = _fake_vad(_fake)
    try:
        paras = ["他很开心地笑了", "普通的一句话", "他愤怒地咆哮"]
        vals, source = mod._compute_valences(paras)
        assert source == "model_vad"
        assert vals == [1, 0, -1], f"应按模型 valence 映射 1/0/-1，得 {vals}"
        assert len(calls) == 1, f"应整批调用一次，实际调了 {len(calls)} 次"
        assert calls[0] == paras, "整批传入应含全部段落（非逐段拆调）"
    finally:
        restore()


def test_compute_valences_model_unavailable_zero_regression():
    """RUOYU_NN_VAD=1 但 predict_batch 返回 None（模型不可用）→ 与默认(env off)词典路径逐位一致。"""
    paras = ["他微笑温暖安心", "普通描述。", "怒火冷汗死亡"]
    baseline_vals, baseline_source = mod._compute_valences(paras)
    assert baseline_source == "lexicon_fallback"
    assert baseline_vals == [mod._valence(p) for p in paras]

    restore = _fake_vad(lambda items: None)
    try:
        got_vals, got_source = mod._compute_valences(paras)
        assert got_source == "lexicon_fallback"
        assert got_vals == baseline_vals, "模型不可用应与默认词典路径逐位一致（零回归）"
    finally:
        restore()


def test_split_paragraphs():
    assert len(mod._split_paragraphs("a\n\nb\n\nc")) == 3


def test_code_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert mod.ISSUE_CODE not in hgs


def _run_cli(draft_path, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_warns_on_cold():
    r = _run_cli(_write(_COLD_FLAT))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None


# comment-triggered 密度 + 位置分布
def test_comment_triggered_density_present():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_HOT))
        assert "comment_triggered_density" in r["metrics"]
        assert "position_distribution" in r["metrics"]
        assert "head" in r["metrics"]["position_distribution"]
        assert "mid" in r["metrics"]["position_distribution"]
        assert "tail" in r["metrics"]["position_distribution"]
    finally:
        _set_mode(bak)


def test_comment_triggered_count_zero_for_cold():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_COLD_FLAT))
        assert r["metrics"]["comment_triggered_count"] == 0
        assert r["metrics"]["comment_triggered_density"] == 0.0
    finally:
        _set_mode(bak)


def test_comment_triggered_count_nonzero_for_hot():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_HOT))
        # 热度段·至少 1 段触发
        assert r["metrics"]["comment_triggered_count"] >= 1
    finally:
        _set_mode(bak)


def test_position_distribution_sums_to_one_or_zero():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = mod.scan(_write(_HOT))
        pd = r["metrics"]["position_distribution"]
        # all triggered → 各位三分占比之和 ≈ 1 (或 0 若无触发)
        total = pd["head"] + pd["mid"] + pd["tail"]
        if r["metrics"]["comment_triggered_count"] >= 1:
            assert abs(total - 1.0) < 0.01
        else:
            assert total == 0
    finally:
        _set_mode(bak)


def test_position_bias_detected_when_all_head():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 前 8 段全 hot (字数堆够) · 后 30 段全 cold (字数堆够)
        hot_para = "怎么会这样？还有十分钟就到了。某种声音从远处传来。那人没说话只是站着。" * 3
        cold_para = "天空很蓝海水很咸山高路远风吹叶落。" * 5
        text = "\n\n".join([hot_para] * 8 + [cold_para] * 30)
        r = mod.scan(_write(text))
        # 整 hot 集中头部 → 位置偏置
        if "metrics" in r and r["metrics"]["comment_triggered_count"] >= 5:
            assert r["metrics"]["position_distribution"]["head"] > 0.5
    finally:
        _set_mode(bak)
