"""interiority_mode_balance_scanner 专属测试 — 内心戏三态失衡检测（advisory · 2026-06-19）

钉死：
  · 标记独白密度超 floor → active 模式上报 FAIL_MINOR + warning
  · 标记独白稀疏 → PASS
  · shadow 模式超阈值也不上报 violations（零回归）
  · mode=off → 不跑骨架返回
  · 短文本 < 500 CJK → 跳过
  · 永远 advisory · 绝不 hard_gate
  · detect 函数真实命中正则
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import interiority_mode_balance_scanner as imb  # noqa: E402


def _write_draft(text: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


# 标记独白单元：他想 / 她想 / 心中暗道 = 3 命中 · 25 CJK
_MONO_UNIT = "他想这次必须成功。她想那个人到底是谁。心中暗道果然如此。"
# 无标记 filler（避开 想/心/暗/念/默 等标记字）· 19 CJK · 0 命中
_FILLER = "外面下着大雨街道上空无一人路灯昏黄微弱。"


def _set_mode(v):
    os.environ["INTERIORITY_MODE_BALANCE_MODE"] = v


def _clear_mode():
    os.environ.pop("INTERIORITY_MODE_BALANCE_MODE", None)


# ---------- 核心检测逻辑 ----------

def test_detect_marked_monologue_hits():
    """detect 真实命中三种标记独白。"""
    hits = imb.detect_marked_monologue(_MONO_UNIT)
    markers = [h["marker"] for h in hits]
    assert "他想" in markers
    assert "她想" in markers
    assert "心中暗道" in markers
    assert len(hits) == 3


def test_detect_no_marker_clean():
    """纯 filler 无标记独白 → 0 命中。"""
    hits = imb.detect_marked_monologue(_FILLER * 30)
    assert len(hits) == 0


# ---------- scan() 模式 ----------

def test_scan_active_reports_violation():
    """active 模式 · 标记独白密度超 floor → FAIL_MINOR + warning。"""
    text = _MONO_UNIT * 25  # 625 CJK · 75 命中 · per_1k≈120 >> 2.5
    p = _write_draft(text)
    try:
        _set_mode("active")
        r = imb.scan(str(p))
        assert r["marked_monologue_per_1k"] > imb.MARKED_MONOLOGUE_FLOOR
        assert r["verdict"] == "FAIL_MINOR"
        assert r["warning"]
        assert len(r["violations"]) == 1
        assert r["violations"][0]["kind"] == "interiority_mode_imbalance"
        assert r["violations"][0]["severity"] == "minor"
        assert r["violations_count"] == 1
    finally:
        _clear_mode()
        p.unlink(missing_ok=True)


def test_scan_active_clean_passes():
    """active 模式 · 标记独白稀疏（远低于 floor）→ PASS。"""
    text = _FILLER * 40 + "他想了一下又继续走。"  # ~770 CJK · 仅 1 命中 · per_1k≈1.3 < 2.5
    p = _write_draft(text)
    try:
        _set_mode("active")
        r = imb.scan(str(p))
        assert r["marked_monologue_per_1k"] <= imb.MARKED_MONOLOGUE_FLOOR
        assert r["verdict"] == "PASS"
        assert r["warning"] is None
        assert r["violations"] == []
    finally:
        _clear_mode()
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    """shadow 模式 · 超阈值也不上报 violations（零回归）。"""
    text = _MONO_UNIT * 25
    p = _write_draft(text)
    try:
        _set_mode("shadow")
        r = imb.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert r["warning"] is None
        # 但度量字段仍计算（shadow 仍观测）
        assert r["marked_monologue_per_1k"] > imb.MARKED_MONOLOGUE_FLOOR
    finally:
        _clear_mode()
        p.unlink(missing_ok=True)


def test_scan_off_returns_skeleton():
    """mode=off → 骨架返回·不读草稿。"""
    text = _MONO_UNIT * 25
    p = _write_draft(text)
    try:
        _set_mode("off")
        r = imb.scan(str(p))
        assert r["mode"] == "off"
        assert r["verdict"] == "PASS"
        assert r["violations"] == []
        assert "marked_monologue_per_1k" not in r
    finally:
        _clear_mode()
        p.unlink(missing_ok=True)


def test_scan_short_text_skip():
    """短文本 < 500 CJK → 跳过。"""
    p = _write_draft(_MONO_UNIT * 5)  # 125 CJK
    try:
        _set_mode("active")
        r = imb.scan(str(p))
        assert r["verdict"] == "PASS"
        assert "太短" in r.get("note", "")
        assert r["violations"] == []
    finally:
        _clear_mode()
        p.unlink(missing_ok=True)


# ---------- advisory 边界 ----------

def test_always_advisory():
    """永远 advisory · 整个 report 不含 hard_gate。"""
    text = _MONO_UNIT * 25
    p = _write_draft(text)
    try:
        _set_mode("active")
        r = imb.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        _clear_mode()
        p.unlink(missing_ok=True)


def test_issue_code_constant():
    """ISSUE_CODE 常量正确。"""
    assert imb.ISSUE_CODE == "INTERIORITY_MODE_IMBALANCE"


def test_issue_code_not_in_hard_gate():
    """INTERIORITY_MODE_IMBALANCE 不在 audit_hub.HARD_GATE_CODES。"""
    try:
        import audit_hub
        assert "INTERIORITY_MODE_IMBALANCE" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass  # audit_hub 不在 path → 不阻断
