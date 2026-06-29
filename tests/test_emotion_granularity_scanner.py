"""emotion_granularity_scanner 专属测试 — 情绪颗粒度粗(粗类情绪大词裸用)检测（advisory · 2026-06-19）

钉死：
  · 粗情绪大词裸词频超 floor → active 上报 FAIL_MINOR + warning
  · 干净草稿(无粗大词) → PASS
  · shadow 模式即便超阈值也不上报 violations(零回归)
  · 草稿 <500 CJK → 跳过
  · 永远 advisory · 绝不 hard_gate
  · 与 subtext_rescan 正交：裸词频不依赖引导词
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import emotion_granularity_scanner as egs  # noqa: E402


def _write_draft(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


# 每段 20 CJK·含 5 个粗情绪大词(愤怒/悲伤/害怕/痛苦/绝望)·裸用无引导词
_COARSE_UNIT = "他十分愤怒非常悲伤又害怕极了内心痛苦绝望"
# 每段干净·无任何粗情绪大词·>20 CJK
_CLEAN_UNIT = "他安静地走在小路上看着远方的群山起伏连绵不绝景色宜人"


# ---------- 核心检测逻辑 ----------

def test_detect_bare_coarse_words():
    """裸用粗情绪大词(无引导词) → 命中(与 subtext_rescan 正交)。"""
    hits = egs.detect_coarse_emotions("他愤怒地砸了桌子又悲伤地哭了")
    words = {h["word"] for h in hits}
    assert "愤怒" in words
    assert "悲伤" in words


def test_detect_english_coarse():
    """英文 happy/sad 也命中。"""
    hits = egs.detect_coarse_emotions("she was happy then sad")
    words = {h["word"] for h in hits}
    assert "happy" in words and "sad" in words


def test_clean_text_no_hits():
    """无粗情绪大词 → 零命中。"""
    hits = egs.detect_coarse_emotions(_CLEAN_UNIT * 5)
    assert hits == []


# ---------- scan() 模式 ----------

def test_scan_active_reports_violation():
    """active 模式·密度远超 floor → FAIL_MINOR + warning + violations。"""
    text = _COARSE_UNIT * 30  # ~600 CJK · 150 粗大词 · 密度极高
    p = _write_draft(text)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        r = egs.scan(str(p))
        assert r["coarse_emotion_per_1k"] > egs.COARSE_EMOTION_PER_1K_FLOOR
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert len(r["violations"]) == 1
        assert r["violations"][0]["kind"] == "emotion_granularity_coarse"
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_clean_passes():
    """干净草稿(无粗大词·密度 0) → PASS·无 violations。"""
    text = _CLEAN_UNIT * 25  # ~600 CJK · 0 粗大词
    p = _write_draft(text)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        r = egs.scan(str(p))
        assert r["coarse_emotion_per_1k"] <= egs.COARSE_EMOTION_PER_1K_FLOOR
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    """shadow 模式·即便超阈值也不上报(零回归)。"""
    text = _COARSE_UNIT * 30
    p = _write_draft(text)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "shadow"
        r = egs.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["coarse_emotion_per_1k"] > egs.COARSE_EMOTION_PER_1K_FLOOR
        assert r["violations"] == []
        assert r["verdict"] == "PASS"
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_off_skips():
    """off 模式 → 直接返回骨架 PASS。"""
    text = _COARSE_UNIT * 30
    p = _write_draft(text)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "off"
        r = egs.scan(str(p))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert "coarse_emotion_per_1k" not in r
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_short_text_skip():
    """草稿 <500 CJK → 跳过。"""
    p = _write_draft(_COARSE_UNIT * 5)  # ~100 CJK
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        r = egs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert "太短" in r.get("note", "")
        assert r["violations"] == []
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


# ---------- advisory 边界 ----------

def test_always_advisory():
    """永远 advisory · report 中绝不出现 hard_gate。"""
    text = _COARSE_UNIT * 30
    p = _write_draft(text)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        r = egs.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_in_hard_gate():
    """EMOTION_GRANULARITY_COARSE 不在 audit_hub.HARD_GATE_CODES。"""
    assert egs.ISSUE_CODE == "EMOTION_GRANULARITY_COARSE"
    try:
        import audit_hub
        assert egs.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass  # audit_hub 不在 path → 不阻断


# ══════════════════════════════════════════════════════════════════════════
# 🔴 2026-06-29 NN增强路径 — 段级 VAD 方差量化情绪颗粒度（env RUOYU_NN_VAD=1 门控）
# 桥用 monkeypatch 替身（不 spawn subprocess·不依赖 venv·与 test_nn_vad_wire 同范式）。
# ══════════════════════════════════════════════════════════════════════════
import nn_vad_bridge  # noqa: E402  (egs 已把 core/scripts 插入 sys.path)

# 20 段·每段 ~27 CJK·总 ~540 CJK（过 <500 跳过 + ≥5 有效段门槛）
_VAD_PARA = "他站在窗前久久没有动只是望着楼下街道上来往的人群出神"


def _vad_draft(n: int = 20) -> str:
    return "\n\n".join(_VAD_PARA for _ in range(n))


def _fake_varying(texts, timeout=None):
    """段间 VAD 交替 0.1/0.9 → 高方差（情绪起伏大=颗粒度高=好）。"""
    out = []
    for i, _ in enumerate(texts):
        hi = i % 2 == 0
        out.append({"valence": 0.1 if hi else 0.9, "arousal": 0.9 if hi else 0.1,
                    "dominance": None, "source": "model"})
    return out


def _fake_constant(texts, timeout=None):
    """所有段 VAD 恒定 → 方差 0（情绪平铺=颗粒度粗）。"""
    return [{"valence": 0.5, "arousal": 0.5, "dominance": None, "source": "model"}
            for _ in texts]


def _fake_none(texts, timeout=None):
    """桥未命中模型（模型不可用）→ 全 None（调用方回退关键词）。"""
    return [None for _ in texts]


def _patch_predict(fn):
    orig = nn_vad_bridge.predict_batch
    nn_vad_bridge.predict_batch = fn
    return orig


def test_nn_off_uses_keyword_path():
    """NN off（默认）→ detection_method=keyword_density·无 vad_variance（零回归）。"""
    p = _write_draft(_vad_draft())
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        os.environ.pop("RUOYU_NN_VAD", None)
        r = egs.scan(str(p))
        assert r["detection_method"] == "keyword_density"
        assert "vad_variance" not in r
    finally:
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        p.unlink(missing_ok=True)


def test_nn_on_high_variance_passes():
    """NN on + 高方差段（起伏大）→ nn_vad_variance·方差 > floor → PASS·无 violation。"""
    p = _write_draft(_vad_draft())
    orig = _patch_predict(_fake_varying)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        os.environ["RUOYU_NN_VAD"] = "1"
        r = egs.scan(str(p))
        assert r["detection_method"] == "nn_vad_variance"
        assert r["vad_variance"] > egs.VAD_VARIANCE_FLOOR
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert r["vad_segments_scored"] >= egs.MIN_VAD_SEGMENTS
    finally:
        nn_vad_bridge.predict_batch = orig
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        os.environ.pop("RUOYU_NN_VAD", None)
        p.unlink(missing_ok=True)


def test_nn_on_flat_reports_with_vad_variance_in_details():
    """NN on + 平铺段（恒定 VAD）→ 方差 ≈0 < floor → FAIL_MINOR·violation details 带 vad_variance。"""
    p = _write_draft(_vad_draft())
    orig = _patch_predict(_fake_constant)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        os.environ["RUOYU_NN_VAD"] = "1"
        r = egs.scan(str(p))
        assert r["detection_method"] == "nn_vad_variance"
        assert r["vad_variance"] < egs.VAD_VARIANCE_FLOOR
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert len(r["violations"]) == 1
        v = r["violations"][0]
        assert v["kind"] == "emotion_granularity_coarse"   # code 不变（团队约定）
        assert "vad_variance" in v                          # details 多带 vad_variance
        assert "vad_coverage" in v
        assert v["detection_method"] == "nn_vad_variance"
    finally:
        nn_vad_bridge.predict_batch = orig
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        os.environ.pop("RUOYU_NN_VAD", None)
        p.unlink(missing_ok=True)


def test_nn_on_bridge_miss_falls_back_to_keyword():
    """NN on 但桥全 None（模型不可用）→ 回退关键词路径（不崩·零回归）。"""
    p = _write_draft(_vad_draft())
    orig = _patch_predict(_fake_none)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        os.environ["RUOYU_NN_VAD"] = "1"
        r = egs.scan(str(p))
        assert r["detection_method"] == "keyword_density"
        assert "vad_variance" not in r
    finally:
        nn_vad_bridge.predict_batch = orig
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        os.environ.pop("RUOYU_NN_VAD", None)
        p.unlink(missing_ok=True)


def test_nn_path_always_advisory():
    """NN 路径命中也永远 advisory·report 无 hard_gate。"""
    p = _write_draft(_vad_draft())
    orig = _patch_predict(_fake_constant)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "active"
        os.environ["RUOYU_NN_VAD"] = "1"
        r = egs.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        nn_vad_bridge.predict_batch = orig
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        os.environ.pop("RUOYU_NN_VAD", None)
        p.unlink(missing_ok=True)


def test_nn_shadow_no_report_even_flat():
    """shadow 模式 + 平铺 → 即便方差低也不上报 violations（零回归）。"""
    p = _write_draft(_vad_draft())
    orig = _patch_predict(_fake_constant)
    try:
        os.environ["EMOTION_GRANULARITY_MODE"] = "shadow"
        os.environ["RUOYU_NN_VAD"] = "1"
        r = egs.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["vad_variance"] < egs.VAD_VARIANCE_FLOOR
        assert r["violations"] == []
        assert r["verdict"] == "PASS"
    finally:
        nn_vad_bridge.predict_batch = orig
        os.environ.pop("EMOTION_GRANULARITY_MODE", None)
        os.environ.pop("RUOYU_NN_VAD", None)
        p.unlink(missing_ok=True)
