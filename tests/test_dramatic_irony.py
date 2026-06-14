"""W4 dramatic irony 信号回查 scanner 测试（2026-06-15·确定性·零依赖）。

守护（北极星⑤⑥）：
  1. detect_dramatic_irony 抓标志词（殊不知/浑然不知）·不抓普通句/展示式信息差；
  2. scan 单向 tell 检测（高密度→advisory·低密度好作者→PASS）；
  3. 金标准:真作者密度≈0 不误伤（IRONY_TELL_FLOOR 0.8 > 真作者最大 0.37）；
  4. 三态（off/shadow/active）·gate_level 恒 advisory·绝不 hard_gate。
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import dramatic_irony_scanner as dis  # noqa: E402


def _write(p, text):
    Path(p).write_text(text, encoding="utf-8")


def _set_mode(m):
    if m is None:
        os.environ.pop("DRAMATIC_IRONY_MODE", None)
    else:
        os.environ["DRAMATIC_IRONY_MODE"] = m


def test_detect_markers_not_plain():
    """抓 dramatic irony 标志词·不抓普通句。"""
    hits = dis.detect_dramatic_irony("他殊不知敌人在身后。她浑然不知。今天天气好。")
    markers = {h["marker"] for h in hits}
    assert "殊不知" in markers and "浑然不知" in markers
    assert len(hits) == 2


def test_detect_show_style_no_hit():
    """展示式信息差（无显式标志词·好作者写法）→ 0 hit。"""
    assert dis.detect_dramatic_irony("他推开门，看见桌上的信，脸色变了。门后的影子动了一下。") == []


def test_scan_high_tell_density_active_reports():
    """高 tell 密度（堆殊不知）→ active 上报 advisory。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        draft = "他殊不知危险。她浑然不知。他做梦也没想到。众人并不知道真相。" * 20
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, draft)
            _set_mode("active")
            out = dis.scan(dp)
            assert out["per_1k"] > dis.IRONY_TELL_FLOOR
            assert out["gate_level"] == "advisory"
            assert out["violations"] and out["verdict"] == "FAIL_MINOR"
            assert out["warning"] is not None
    finally:
        _set_mode(bak)


def test_scan_low_density_good_author_pass():
    """低密度（好作者用展示·无显式标志词）→ PASS（金标准:不误伤真作者）。"""
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("active")
        draft = ("他推开门，桌上的信封压着一枚铜钥匙。他没回头，影子在门后停住。"
                 "她笑着接过茶，指尖在杯沿停了一瞬。") * 20
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, draft)
            out = dis.scan(dp)
            assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_threshold_not_hurt_real_author():
    """金标准：阈值 > 真作者最大密度 0.37（不误伤·北极星⑥防矫枉过正）。"""
    assert dis.IRONY_TELL_FLOOR > 0.37


def test_scan_off_skips():
    bak = os.environ.get("DRAMATIC_IRONY_MODE")
    try:
        _set_mode("off")
        out = dis.scan("/nonexistent.txt")
        assert out["mode"] == "off" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_gate_level_never_hard_gate():
    """北极星⑤护栏：ISSUE_CODE 绝不在 audit_hub.HARD_GATE_CODES。"""
    import audit_hub
    assert dis.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
