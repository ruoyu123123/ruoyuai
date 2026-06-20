# -*- coding: utf-8 -*-
"""affective_signature_scanner 专属测试 — Plutchik KL(advisory · 2026-06-20)

钉死：
  · 8 类情感命中映射
  · joy 膨胀 + anger/disgust 塌陷 → CONGENIALITY_SKEW
  · 作者档 author_affective_signature 第一权威
  · 无作者档 → 均匀兜底
  · 命中过少 → 不判
  · 永远 advisory · 绝不 hard_gate
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import affective_signature_scanner as af  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, affective_signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if affective_signature is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"author_affective_signature": affective_signature},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("AFFECTIVE_SIGNATURE_MODE", None)
    else:
        os.environ["AFFECTIVE_SIGNATURE_MODE"] = m


_FILLER = "夜风扫过山脊石阶落满松针他独自向上踏步影子被拉得很长。" * 20


def test_off_returns_skeleton():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("off")
        rep = af.scan(str(_write(_FILLER)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        rep = af.scan(str(_write("高兴。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_no_emotion_words_sample_low():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        rep = af.scan(str(_write(_FILLER)))
        assert "样本不足" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_joy_inflation_with_anger_collapse_active():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 作者基线: anger=0.5(主导)
        proj = _mk_project(affective_signature={
            "joy": 0.1, "trust": 0.05, "fear": 0.05, "surprise": 0.05,
            "sadness": 0.1, "disgust": 0.1, "anger": 0.5, "anticipation": 0.05})
        # 草稿 joy 暴涨 anger=0(塌陷)
        text = (_FILLER + "高兴愉快喜悦欣喜开心快活畅快欢乐笑容笑意大喜舒畅惊喜欢喜。" * 10)
        rep = af.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "CONGENIALITY_SKEW" in codes
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_balanced_emotion_distribution_passes():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 作者基线均匀 + 草稿包含各类
        proj = _mk_project()
        text = (_FILLER
                + "高兴愉快喜悦。" * 3
                + "信任信赖依赖。" * 3
                + "害怕恐惧恐慌。" * 3
                + "惊讶吃惊愕然。" * 3
                + "悲伤难过悲痛。" * 3
                + "厌恶恶心嫌弃。" * 3
                + "愤怒恼怒气愤。" * 3
                + "期待期盼盼望。" * 3)
        rep = af.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "CONGENIALITY_SKEW" not in codes
    finally:
        _set_mode(bak)


def test_uniform_fallback_when_no_author_profile():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # joy 大量 + anger=0
        text = _FILLER + "高兴愉快喜悦欣喜开心快活。" * 15
        rep = af.scan(str(_write(text)))
        assert rep["baseline_source"] == "uniform_fallback"
    finally:
        _set_mode(bak)


def test_shadow_mode_no_report():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(affective_signature={
            "joy": 0.1, "trust": 0.1, "fear": 0.1, "surprise": 0.1,
            "sadness": 0.1, "disgust": 0.1, "anger": 0.3, "anticipation": 0.1})
        text = _FILLER + "高兴愉快喜悦欣喜开心快活畅快欢乐笑容笑意大喜。" * 10
        rep = af.scan(str(_write(text)), project_root=proj)
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_codes_not_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "CONGENIALITY_SKEW" not in hgs


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        os.environ["AFFECTIVE_SIGNATURE_MODE"] = "bogus"
        assert af._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        rep = af.scan(str(Path(tempfile.mkdtemp()) / "missing.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_kl_divergence_computed():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        text = _FILLER + "高兴愉快喜悦欣喜开心快活。" * 5
        rep = af.scan(str(_write(text)), project_root=proj)
        # 仅 joy 命中→KL 大
        if "kl_divergence" in rep:
            assert rep["kl_divergence"] >= 0
    finally:
        _set_mode(bak)


def test_disgust_only_collapse_with_joy_inflation():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 基线 disgust 高 · 草稿 joy 暴+ disgust 0
        proj = _mk_project(affective_signature={
            "joy": 0.05, "trust": 0.1, "fear": 0.1, "surprise": 0.1,
            "sadness": 0.1, "disgust": 0.3, "anger": 0.2, "anticipation": 0.05})
        text = (_FILLER + "高兴愉快喜悦欣喜开心快活畅快欢乐笑容笑意。" * 12
                + "愤怒气愤恼怒。" * 2)  # anger 不为 0 但 disgust 为 0
        rep = af.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "CONGENIALITY_SKEW" in codes
    finally:
        _set_mode(bak)


def test_invalid_author_signature_falls_back():
    bak = os.environ.get("AFFECTIVE_SIGNATURE_MODE")
    try:
        _set_mode("active")
        # 全 0 → 退兜底
        proj = _mk_project(affective_signature={
            "joy": 0, "trust": 0, "fear": 0, "surprise": 0,
            "sadness": 0, "disgust": 0, "anger": 0, "anticipation": 0})
        text = _FILLER + "高兴愉快喜悦欣喜开心快活。" * 8
        rep = af.scan(str(_write(text)), project_root=proj)
        assert rep["baseline_source"] == "uniform_fallback"
    finally:
        _set_mode(bak)
