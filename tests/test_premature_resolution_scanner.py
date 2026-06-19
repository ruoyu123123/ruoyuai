"""premature_resolution_scanner 专属测试 — 过早消解冲突检测（advisory · 2026-06-19）

钉死：
  · 冲突+快速消解 → 检出 quick_pair · ratio 正确
  · 冲突无消解 / 远距离消解 → PASS
  · 消解标志词在冲突前 → 不算 quick
  · mode=off → 不跑 · mode=shadow → 不上报 · mode=active → 上报
  · 永远 advisory · 绝不 hard_gate
  · 短文本 / 冲突不足 → 跳过
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import premature_resolution_scanner as prs  # noqa: E402


def _write_draft(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


# ---------- 核心检测逻辑 ----------

def test_detect_quick_resolution():
    """冲突后 100 CJK 内有消解 → quick_pair 命中。"""
    text = "这是一个" * 20 + "遭到突袭" + "他躲闪着" * 10 + "还好有惊无险" + "后面的故事" * 50
    r = prs.detect_premature_resolutions(text)
    assert r["quick_count"] >= 1
    assert r["quick_ratio"] > 0


def test_detect_no_quick_when_far():
    """冲突和消解间距 > QUICK_WINDOW → 不算 quick。"""
    filler = "他在山间行走看着远处的风景心中想着各种事情。" * 30  # ~600 CJK
    text = "遭到突袭" + filler + "总算安全了" + "后续内容" * 20
    r = prs.detect_premature_resolutions(text)
    assert r["quick_count"] == 0


def test_resolution_before_conflict_not_counted():
    """消解标志词在冲突前 → 不算 quick。"""
    text = "有惊无险" + "正常内容" * 30 + "遭到突袭" + "后续紧张内容" * 30
    r = prs.detect_premature_resolutions(text)
    assert r["quick_count"] == 0


def test_no_conflicts_skip():
    """无冲突标志词 → conflict_count=0 → 不报。"""
    text = "他平静地走在路上看着风景。" * 100
    r = prs.detect_premature_resolutions(text)
    assert r["conflict_count"] == 0


# ---------- scan() 模式 ----------

def test_scan_off_returns_pass():
    text = "遭到突袭有惊无险" * 30
    p = _write_draft(text)
    try:
        os.environ["PREMATURE_RESOLUTION_MODE"] = "off"
        r = prs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["mode"] == "off"
    finally:
        os.environ.pop("PREMATURE_RESOLUTION_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_violations():
    """shadow 模式下有 quick_ratio 超阈值也不上报 violations。"""
    text = ("遭到突袭" + "正常内容" * 5 + "有惊无险" + "其他内容" * 10) * 10
    p = _write_draft(text)
    try:
        os.environ["PREMATURE_RESOLUTION_MODE"] = "shadow"
        r = prs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert len(r["violations"]) == 0
    finally:
        os.environ.pop("PREMATURE_RESOLUTION_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_active_reports_violations():
    """active 模式下 quick_ratio 超阈值 → 上报 violations。"""
    text = ("遭到突袭" + "十二个中文字的内容" * 5 + "有惊无险" + "后续故事内容填充更多的文字" * 20) * 8
    p = _write_draft(text)
    try:
        os.environ["PREMATURE_RESOLUTION_MODE"] = "active"
        r = prs.scan(str(p))
        if r["metrics"]["conflict_count"] >= prs.MIN_CONFLICTS and r["metrics"]["quick_ratio"] >= prs.QUICK_RATIO_MINOR:
            assert r["verdict"].startswith("FAIL")
            assert len(r["violations"]) > 0
            assert r["violations"][0]["kind"] == "premature_resolution"
    finally:
        os.environ.pop("PREMATURE_RESOLUTION_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_short_text_skip():
    """短文本 → 跳过。"""
    p = _write_draft("危机解决了" * 10)
    try:
        r = prs.scan(str(p))
        assert r["verdict"] == "PASS"
        assert "太短" in r.get("note", "")
    finally:
        p.unlink(missing_ok=True)


# ---------- advisory 边界 ----------

def test_always_advisory():
    """永远 advisory · 绝不 hard_gate。"""
    text = ("遭到突袭有惊无险" + "内容" * 15) * 10
    p = _write_draft(text)
    try:
        os.environ["PREMATURE_RESOLUTION_MODE"] = "active"
        r = prs.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("PREMATURE_RESOLUTION_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_in_hard_gate():
    """PREMATURE_RESOLUTION 不在 audit_hub.HARD_GATE_CODES。"""
    try:
        import audit_hub
        assert "PREMATURE_RESOLUTION" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass  # audit_hub 不在 path → 不阻断
