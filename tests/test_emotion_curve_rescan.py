"""W3 情绪曲线 live 回查 scanner 测试（2026-06-15·确定性·零依赖）。

守护（北极星⑤）：
  1. segment_valence_curve 情绪 valence 提取（正面高/负面低/中性 0.5）；
  2. _find_target_from_manifest 递归找 emotion_curve_full + matched_reagan_shape；
  3. scan 三态（off 跳过 / shadow 只记不判 / active 超阈值 warning）；
  4. gate_level 恒 advisory·code 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤护栏）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import emotion_curve_rescan_scanner as ecr  # noqa: E402


def _write(p, text):
    Path(p).write_text(text, encoding="utf-8")


def _set_mode(m):
    if m is None:
        os.environ.pop("EMOTION_RESCAN_MODE", None)
    else:
        os.environ["EMOTION_RESCAN_MODE"] = m


def test_segment_valence_positive_high_negative_low():
    """正面情绪段 valence 高·负面段低·无情绪段 0.5 中性。"""
    curve = ecr.segment_valence_curve(
        "他高兴地笑了，兴奋畅快欣喜。\n\n泪水滑落，悲伤心痛哽咽。\n\n青砖墙面爬满藤蔓。", 3)
    assert len(curve) == 3
    assert curve[0] > 0.8          # 高兴段
    assert curve[1] < 0.3          # 悲痛段
    assert curve[2] == 0.5         # 无情绪词 → 中性


def test_segment_empty_text():
    """空草稿 → 空曲线（不崩）。"""
    assert ecr.segment_valence_curve("", 10) == []


def test_find_target_from_manifest():
    """从 manifest 递归找 emotion_curve_full + matched_reagan_shape。"""
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "m.json"
        _write(mp, json.dumps({"arc_tpl": {
            "emotion_curve_full": [0.1, 0.3, 0.9],
            "matched_reagan_shape": "Rags-to-Riches"}}, ensure_ascii=False))
        curve, shape = ecr._find_target_from_manifest(mp)
        assert curve == [0.1, 0.3, 0.9]
        assert shape == "Rags-to-Riches"


def test_find_target_missing_file():
    """manifest 不存在 → ([], None)（不崩）。"""
    assert ecr._find_target_from_manifest("/nonexistent/m.json") == ([], None)


def test_scan_off_mode_skips():
    """off 模式 → 直接跳过（不读草稿·零行为）。"""
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        _set_mode("off")
        out = ecr.scan("/nonexistent.txt")
        assert out["mode"] == "off"
        assert out["warning"] is None and out["violations"] == []
    finally:
        _set_mode(bak)


def test_scan_drift_shadow_silent_active_reports():
    """actual 上升 vs target 下降 → 余弦低+形状不匹配 → drift。shadow 只记不判·active 上报。"""
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        with tempfile.TemporaryDirectory() as td:
            # actual：全程上升（Rags-to-Riches 走向）
            draft = "\n\n".join([
                "恐惧战栗颤抖害怕。", "紧张担心忐忑。", "平静镇定。",
                "好奇疑惑。", "高兴笑了。", "兴奋欣喜畅快高兴大笑欢呼。",
            ])
            dp = Path(td) / "draft.txt"; _write(dp, draft)
            # target：全程下降（Tragedy）·与 actual 相反 → drift
            mp = Path(td) / "m.json"
            _write(mp, json.dumps({"t": {
                "emotion_curve_full": [1.0, 0.9, 0.7, 0.5, 0.3, 0.1],
                "matched_reagan_shape": "Tragedy"}}, ensure_ascii=False))
            _set_mode("shadow")
            out_s = ecr.scan(dp, mp)
            assert out_s["warning"] is None          # shadow 永不上报顶层 warning（零回归）
            _set_mode("active")
            out_a = ecr.scan(dp, mp)
            assert out_a["gate_level"] == "advisory"  # 恒 advisory
            # 这组数据应触发 drift（趋势相反 Pearson 负）→ active 上报 violation
            assert out_a["violations"], "上升 vs 下降应判 drift"
            assert out_a["verdict"] == "FAIL_MINOR"
            assert out_a["warning"] is not None
    finally:
        _set_mode(bak)


def test_scan_no_target_records_actual_only():
    """manifest 无 target → 只记 actual 不对账（note 说明·不误判 drift）。"""
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "draft.txt"
            _write(dp, "\n\n".join(["高兴笑了。"] * 5 + ["悲伤哭了。"] * 5))
            out = ecr.scan(dp, None)              # 无 manifest
            assert "actual_reagan_shape" in out
            assert out["warning"] is None         # 无 target 不判 drift
            assert "note" in out
    finally:
        _set_mode(bak)


def test_gate_level_never_hard_gate():
    """北极星⑤护栏：ISSUE_CODE 绝不在 audit_hub.HARD_GATE_CODES·scan 输出 gate_level=advisory。"""
    import audit_hub
    assert ecr.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        _set_mode("shadow")
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "d.txt"; _write(dp, "\n\n".join(["高兴。"] * 5))
            assert ecr.scan(dp)["gate_level"] == "advisory"
    finally:
        _set_mode(bak)
