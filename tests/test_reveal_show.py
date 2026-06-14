"""W5 反转揭底 tell 回查 scanner 测试（2026-06-15·确定性·零依赖）。

守护（北极星⑤⑥）：
  1. detect_reveal_tell 抓揭底标志词（真相大白/恍然大悟）·不抓展示式揭底；
  2. scan 单向 tell 检测（高密度→advisory·低密度好作者→PASS）；
  3. 金标准:真作者密度≈0 不误伤（REVEAL_TELL_FLOOR 0.8 > 真作者最大 0.4）；
  4. 三态·gate_level 恒 advisory·绝不 hard_gate。
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import reveal_show_scanner as rss  # noqa: E402


def _write(p, text):
    Path(p).write_text(text, encoding="utf-8")


def _set_mode(m):
    if m is None:
        os.environ.pop("REVEAL_SHOW_MODE", None)
    else:
        os.environ["REVEAL_SHOW_MODE"] = m


def test_detect_markers_not_plain():
    hits = rss.detect_reveal_tell("他恍然大悟，真相大白。这才明白。今天天气好。")
    markers = {h["marker"] for h in hits}
    assert "恍然大悟" in markers and "真相大白" in markers and "这才明白" in markers


def test_detect_show_style_no_hit():
    """展示式揭底（读者从情节看出·无显式标志）→ 0 hit。"""
    assert rss.detect_reveal_tell("他翻开账本，数字对不上。那笔钱去了城南那座宅子。") == []


def test_scan_high_tell_active_reports():
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        draft = "真相大白。他恍然大悟。这才明白。原来如此。谜底揭晓。" * 30
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, draft)
            _set_mode("active")
            out = rss.scan(dp)
            assert out["per_1k"] > rss.REVEAL_TELL_FLOOR
            assert out["gate_level"] == "advisory"
            assert out["violations"] and out["verdict"] == "FAIL_MINOR"
            assert out["warning"] is not None
    finally:
        _set_mode(bak)


def test_scan_low_density_show_pass():
    """低密度（展示式揭底）→ PASS（金标准:不误伤真作者）。"""
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("active")
        draft = ("他翻开账本，数字对不上。墙上的影子比人多了一道。"
                 "她端起茶，杯底压着一张纸条。") * 20
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, draft)
            out = rss.scan(dp)
            assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_threshold_not_hurt_real_author():
    """金标准：阈值 > 真作者最大密度 0.4（不误伤·北极星⑥）。"""
    assert rss.REVEAL_TELL_FLOOR > 0.4


def test_scan_off_skips():
    bak = os.environ.get("REVEAL_SHOW_MODE")
    try:
        _set_mode("off")
        out = rss.scan("/nonexistent.txt")
        assert out["mode"] == "off" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_gate_level_never_hard_gate():
    import audit_hub
    assert rss.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
