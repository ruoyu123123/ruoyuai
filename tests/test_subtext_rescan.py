"""W1 潜台词/on-the-nose 情绪直陈回查 scanner 测试（2026-06-15·确定性·零依赖）。

守护（北极星⑤⑥）：
  1. detect_on_the_nose 抓「引导词+情绪名词紧邻」·去重(同情绪名词不被多引导词重复计)·不抓动作侧写；
  2. scan 三态（off / shadow 只记不判 / active 超阈值 warning）；
  3. 阈值金标准校准（真作者最大密度 1.14 < 阈值 3.0·不误伤）；
  4. gate_level 恒 advisory·code 绝不进 audit_hub.HARD_GATE_CODES。
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import subtext_rescan_scanner as srs  # noqa: E402


def _write(p, text):
    Path(p).write_text(text, encoding="utf-8")


def _set_mode(m):
    if m is None:
        os.environ.pop("SUBTEXT_RESCAN_MODE", None)
    else:
        os.environ["SUBTEXT_RESCAN_MODE"] = m


def test_detect_catches_lead_plus_emotion_not_action():
    """抓「引导词+情绪名词紧邻」·不抓动作侧写（摔杯子）。"""
    hits = srs.detect_on_the_nose("他感到愤怒，心中充满了绝望。她把杯子摔在地上。")
    assert {h["emotion"] for h in hits} == {"愤怒", "绝望"}


def test_detect_dedup_same_emotion():
    """去重：同一情绪名词被多引导词紧邻只算 1 次（感到+一阵→同一愤怒）。"""
    hits = srs.detect_on_the_nose("他感到一阵愤怒。")
    assert len([h for h in hits if h["emotion"] == "愤怒"]) == 1


def test_detect_no_emotion_no_hit():
    """纯动作/无情绪名词 → 0 hit。"""
    assert srs.detect_on_the_nose("他把门推开，走进房间，倒了杯水。") == []


def test_scan_off_skips():
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("off")
        out = srs.scan("/nonexistent.txt")
        assert out["mode"] == "off" and out["violations"] == [] and out["warning"] is None
    finally:
        _set_mode(bak)


def test_scan_high_density_shadow_silent_active_reports():
    """高 on-the-nose 密度 → shadow 只记不判·active 上报 violation。"""
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        sent = "他感到愤怒，心中充满了绝望，内心涌起痛苦，觉得无比失望。"
        draft = sent * 30   # 高密度 + 足够长
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, draft)
            _set_mode("shadow")
            out_s = srs.scan(dp)
            assert out_s["warning"] is None
            assert out_s["on_the_nose_per_1k"] > srs.ON_THE_NOSE_PER_1K_FLOOR
            _set_mode("active")
            out_a = srs.scan(dp)
            assert out_a["gate_level"] == "advisory"
            assert out_a["violations"] and out_a["verdict"] == "FAIL_MINOR"
            assert out_a["warning"] is not None
    finally:
        _set_mode(bak)


def test_scan_low_density_pass():
    """低 on-the-nose 密度（动作侧写为主）→ PASS·无 violation。"""
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("active")
        draft = ("他把碗洗了三遍，水声盖过了窗外的雨。她合上书，"
                 "指尖在桌面敲了两下，起身走向门口。") * 20
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, draft)
            out = srs.scan(dp)
            assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


def test_threshold_not_hurt_real_author():
    """金标准：阈值 > 真作者最大密度 1.14（不误伤·北极星⑥防矫枉过正）。"""
    assert srs.ON_THE_NOSE_PER_1K_FLOOR > 1.14


def test_gate_level_never_hard_gate():
    """北极星⑤护栏：ISSUE_CODE 绝不在 audit_hub.HARD_GATE_CODES·scan gate_level=advisory。"""
    import audit_hub
    assert srs.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
    bak = os.environ.get("SUBTEXT_RESCAN_MODE")
    try:
        _set_mode("shadow")
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, "他走进房间。" * 100)
            assert srs.scan(dp)["gate_level"] == "advisory"
    finally:
        _set_mode(bak)
