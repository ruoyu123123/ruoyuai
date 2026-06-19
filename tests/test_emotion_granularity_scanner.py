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
