# -*- coding: utf-8 -*-
"""cross_cluster_ousiometric_emd_scanner 专属测试(advisory · 2026-06-20 R10)。"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_ousiometric_emd_scanner as eemd  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("OUSIOMETRIC_EMD_MODE", None)
    else:
        os.environ["OUSIOMETRIC_EMD_MODE"] = m


def _mk_project(num_clusters=0, with_drafts=False, post_amp_low=False):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    drafts_dir = proj / "章节"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    clusters = []
    for i in range(num_clusters):
        cid = f"cluster_{i+1:03d}"
        clusters.append({"cluster_id": cid})
        if with_drafts:
            # 前段振幅 high(各种 power+danger 交替) · 后段 amp 低(单调)
            if post_amp_low and i >= num_clusters // 2:
                text = "平静走过田野。" * 800  # 低振幅 几乎无 power/danger
            else:
                # 高振幅: 力量/危险交替
                text = (("强威霸猛雷怒战斗破碎" * 50)
                        + "\n"
                        + ("危险惧怕惊恐怖凶狰狞" * 50))
            (drafts_dir / f"{cid}_draft.txt").write_text(text, encoding="utf-8")
    (db / "cluster_index.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8")
    return proj


def test_off_skeleton():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("off")
        proj = _mk_project()
        rep = eemd.scan_project(proj)
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_cluster_count_skips():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=5)
        rep = eemd.scan_project(proj)
        assert "长篇门槛" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_no_drafts_skips():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=35, with_drafts=False)
        rep = eemd.scan_project(proj)
        assert "无可读" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_oscillation_basic_pass_with_balanced_drafts():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=30, with_drafts=True,
                          post_amp_low=False)
        rep = eemd.scan_project(proj)
        # 全程高振幅 → 不应报塌缩
        codes = [v.get("code") for v in rep.get("violations", [])]
        assert "OUSIOMETRIC_OSCILLATION_DEGRADED" not in codes
    finally:
        _set_mode(bak)


def test_shadow_mode():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(num_clusters=35, with_drafts=True,
                          post_amp_low=True)
        rep = eemd.scan_project(proj)
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "OUSIOMETRIC_OSCILLATION_DEGRADED" not in hgs


def test_scan_signature_compat():
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("off")
        proj = _mk_project()
        rep = eemd.scan(draft_path=None, project_root=proj)
        assert rep["mode"] == "off"
    finally:
        _set_mode(bak)


def test_local_extrema_helper():
    ext = eemd.local_extrema([1, 3, 2, 5, 4, 6, 1])
    assert isinstance(ext, list)


def test_amplitude_envelope_empty():
    assert eemd.amplitude_envelope([1, 2, 3], []) == 0.0


# ============================================================
# 🔴 2026-07-01 emotion_vad 模型集成测试(source=model_vad|lexicon_fallback|mixed·零回归)
# ============================================================

def test_model_scores_replace_lexicon_when_dominance_and_arousal_available(monkeypatch):
    """RUOYU_NN_VAD=1 + 桥命中 dominance/arousal → 用 Dominance-Arousal 连续值·source=model_vad。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    text = "强大的力量" * 20 + "危险的气息" * 20  # 短文本 < WINDOW_CJK → 整段一窗
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts: [
            {"valence": 0.5, "arousal": 0.2, "dominance": 0.9, "source": "model"}
            for _ in texts
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    detail = eemd.compute_valence_series_detail(text)
    assert detail["source"] == "model_vad"
    assert detail["model_window_count"] == 1
    assert detail["lexicon_window_count"] == 0
    assert detail["series"] == [round(0.9 - 0.2, 4)]


def test_model_unavailable_matches_default_lexicon_series_exactly(monkeypatch):
    """零回归契约：RUOYU_NN_VAD=1 但桥返回全 None(模型不可用) → 序列必须与默认(env 不开)逐字节一致。"""
    text = "强威霸猛雷怒战斗破碎" * 30 + "危险惧怕惊恐怖凶狰狞" * 30
    baseline = eemd.compute_valence_series(text)  # 默认(env 未设)行为

    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    detail = eemd.compute_valence_series_detail(text)
    assert detail["series"] == baseline
    assert detail["source"] == "lexicon_fallback"


def test_model_dominance_none_falls_back_to_lexicon(monkeypatch):
    """现网 va_base 检查点现实：模型只出 valence/arousal·dominance 恒 None → 该窗回退关键词计数
    (与默认行为逐字节一致·验证「有模型但缺 dominance 字段」不会产出半吊子混合分数)。"""
    text = "强威霸猛雷怒战斗破碎" * 30
    baseline = eemd.compute_valence_series(text)

    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts: [
            {"valence": 0.4, "arousal": 0.6, "dominance": None, "source": "model"}
            for _ in texts
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    detail = eemd.compute_valence_series_detail(text)
    assert detail["series"] == baseline
    assert detail["source"] == "lexicon_fallback"
    assert detail["model_window_count"] == 0


def test_scan_project_default_source_is_lexicon_fallback():
    """默认(env 不开)：scan_project 输出 valence_source=lexicon_fallback·不影响既有振幅/衰减比字段。"""
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=30, with_drafts=True, post_amp_low=False)
        rep = eemd.scan_project(proj)
        assert rep["valence_source"] == "lexicon_fallback"
        assert rep["model_window_count"] == 0
        assert rep["lexicon_window_count"] > 0
        assert rep["valence_series_len"] == rep["lexicon_window_count"]
    finally:
        _set_mode(bak)


def test_violation_carries_source_field_when_active_and_degraded():
    """active 模式真报出振荡塌缩时(默认 lexicon 路径)·violation 必须带 source 字段(供训练数据归因)。"""
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=35, with_drafts=True, post_amp_low=True)
        rep = eemd.scan_project(proj)
        assert rep["violations"], "既有 fixture(前段高振幅/后段平段)应能触发振荡塌缩 violation"
        for v in rep["violations"]:
            assert v["source"] == "lexicon_fallback"
    finally:
        _set_mode(bak)


def test_violation_carries_source_field_when_model_path_degraded(monkeypatch):
    """RUOYU_NN_VAD=1 + 内容感知 fake 桥(dominance/arousal 按窗口真实 power/danger 密度连续化，
    模拟一个"读得懂内容"的模型) → 复现与 lexicon 路径相同的前高后低振幅塌缩模式·
    violation.source == model_vad(验证模型路径也会正确打标·而非常数分导致零振幅误判)。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")

    def _content_aware_predict(texts):
        out = []
        for t in texts:
            p = len(eemd.POWER_LEX.findall(t))
            d = len(eemd.DANGER_LEX.findall(t))
            diff = (p - d) / max(len(t), 1)
            dominance = min(1.0, max(0.0, 0.5 + diff * 5))
            arousal = min(1.0, max(0.0, 0.5 - diff * 5))
            out.append({"valence": 0.5, "arousal": arousal, "dominance": dominance, "source": "model"})
        return out

    fake_bridge = types.SimpleNamespace(predict_batch=_content_aware_predict)
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    bak = os.environ.get("OUSIOMETRIC_EMD_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(num_clusters=35, with_drafts=True, post_amp_low=True)
        rep = eemd.scan_project(proj)
        assert rep["valence_source"] == "model_vad"
        assert rep["violations"], "内容感知模型分应复现前高后低振幅塌缩"
        for v in rep["violations"]:
            assert v["source"] == "model_vad"
    finally:
        _set_mode(bak)


def test_model_window_scores_helper_empty_input():
    assert eemd._model_window_scores([]) == []


def test_model_window_scores_disabled_by_default():
    assert eemd._model_window_scores(["随便一段文本"]) == [None]
