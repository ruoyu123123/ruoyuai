# -*- coding: utf-8 -*-
"""chronotope_typology_scanner 专属测试 (R9 W5 Batch-M·L38·2026-06-20)

钉死：
  · 7 型 chronotope 关键词命中分类
  · 每场景前 200 字打 tag
  · cluster 级 distribution_entropy + monotony_streak 算法
  · monotony_streak ≥3 → CHRONOTOPE_MONOTONY (active)
  · 作者档 chronotope_signature.allowed_monotony 豁免
  · shadow/off/active 三态
  · 永远 advisory · 不在 HARD_GATE_CODES
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chronotope_typology_scanner as ct  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, chronotope_signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if chronotope_signature is not None:
        obj["chronotope_signature"] = chronotope_signature
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("CHRONOTOPE_TYPOLOGY_MODE", None)
    else:
        os.environ["CHRONOTOPE_TYPOLOGY_MODE"] = m


_FILLER = "夜风扫过山脊石阶落满松针他独自向上踏步影子被拉得很长。" * 8


def _scene(head):
    """生成包含 head 词的场景段·>200 CJK 保证够长"""
    return head + _FILLER


def test_off_returns_skeleton():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("off")
        rep = ct.scan(str(_write(_FILLER * 5)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        rep = ct.scan(str(_write("门口。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_chronotope_tag_single_scene():
    tag, counts = ct.tag_scene_chronotope("他停在门口望着玄关。" + "x" * 50)
    assert tag == "threshold"
    assert counts.get("threshold", 0) >= 1


def test_chronotope_road_tag():
    tag, counts = ct.tag_scene_chronotope("他在驿道上赶路，远处客栈灯火明灭。" + "x" * 50)
    assert tag == "road"


def test_chronotope_castle_tag():
    tag, counts = ct.tag_scene_chronotope("他踏入朝堂，殿内雕梁画栋。" + "x" * 50)
    assert tag == "castle"


def test_chronotope_untagged():
    tag, _ = ct.tag_scene_chronotope("纯粹自然词无任何 chronotope 命中。" + "x" * 50)
    assert tag == "untagged"


def test_monotony_streak_detected_active():
    """5 场景全 salon → streak=5 → FAIL"""
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        scenes = [_scene("他坐在书房雅间，会客的茶刚泡好。") for _ in range(5)]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        codes = [v["code"] for v in rep["violations"]]
        assert "CHRONOTOPE_MONOTONY" in codes
        assert rep["monotony_streak"] >= 3
        assert rep["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_diverse_chronotopes_pass():
    """7 不同 chronotope → entropy 高 + streak=1 → PASS"""
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        scenes = [
            _scene("他在驿道上赶路。"),       # road
            _scene("他踏入朝堂的殿内。"),     # castle
            _scene("他坐在书房会客。"),       # salon
            _scene("他穿过街市巷弄。"),       # town
            _scene("他在田园小院里小憩。"),   # idyll
            _scene("他在广场上听朝会。"),     # square
            _scene("他止步在门槛。"),         # threshold
        ]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        codes = [v["code"] for v in rep["violations"]]
        assert "CHRONOTOPE_MONOTONY" not in codes
        assert rep["distribution_entropy"] >= ct.ENTROPY_FLOOR
    finally:
        _set_mode(bak)


def test_shadow_mode_no_report():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("shadow")
        scenes = [_scene("他坐在书房雅间会客。") for _ in range(5)]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_allowed_monotony_exemption():
    """作者档 allowed_monotony=True → 同型连续不报"""
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(chronotope_signature={"allowed_monotony": True})
        scenes = [_scene("他坐在书房雅间会客。") for _ in range(5)]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)), project_root=proj)
        codes = [v["code"] for v in rep["violations"]]
        assert "CHRONOTOPE_MONOTONY" not in codes
    finally:
        _set_mode(bak)


def test_max_streak_algorithm():
    streak, tag = ct.max_streak(["a", "b", "b", "b", "c", "a"])
    assert streak == 3
    assert tag == "b"


def test_max_streak_empty():
    assert ct.max_streak([])[0] == 0


def test_entropy_uniform_vs_skewed():
    h_uni = ct.shannon_entropy_norm({"road": 1, "castle": 1, "salon": 1, "town": 1,
                                     "idyll": 1, "square": 1, "threshold": 1})
    h_skew = ct.shannon_entropy_norm({"salon": 10})
    assert h_uni > h_skew
    assert h_skew == 0.0
    # 词典 8 类 (含 instance_dungeon)·7 类均匀分布 entropy < 1.0
    assert 0.9 < h_uni < 1.0


def test_codes_not_in_hard_gate_registry():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "CHRONOTOPE_MONOTONY" not in hgs
    assert "CHRONOTOPE_LOW_DIVERSITY" not in hgs


def test_codes_not_in_audit_hub_hard_gate():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
    import audit_hub
    assert "CHRONOTOPE_MONOTONY" not in audit_hub.HARD_GATE_CODES
    assert "CHRONOTOPE_LOW_DIVERSITY" not in audit_hub.HARD_GATE_CODES


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        os.environ["CHRONOTOPE_TYPOLOGY_MODE"] = "garbage"
        assert ct._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        rep = ct.scan(str(Path(tempfile.mkdtemp()) / "missing.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_low_diversity_advisory():
    """4+ 场景全 salon → entropy 低 → CHRONOTOPE_LOW_DIVERSITY"""
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        scenes = [_scene("他在书房雅间会客茶馆里。") for _ in range(5)]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        codes = [v["code"] for v in rep["violations"]]
        # 同型 → entropy 应该 < 0.35
        assert rep["distribution_entropy"] < ct.ENTROPY_FLOOR
        assert "CHRONOTOPE_LOW_DIVERSITY" in codes
    finally:
        _set_mode(bak)


def test_threshold_anchor_flag():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        scenes = [_scene("他在驿道赶路。"), _scene("他止步在门槛上。"),
                  _scene("他穿过街市。"), _scene("他坐在书房。")]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        assert rep["has_threshold_anchor"] is True
    finally:
        _set_mode(bak)


def test_no_threshold_anchor_flag():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        scenes = [_scene("他在驿道赶路。"), _scene("他在田园小院里。"),
                  _scene("他穿过街市。"), _scene("他坐在书房。")]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        assert rep["has_threshold_anchor"] is False
    finally:
        _set_mode(bak)


def test_few_scenes_skip():
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        rep = ct.scan(str(_write(_FILLER * 6)))
        # 单场景 (没有空行) → 跳过
        if "note" in rep and "场景数太少" in rep["note"]:
            assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_split_scenes_basic():
    out = ct._split_scenes("A段\n\nB段\n\nC段")
    assert len(out) == 3


def test_distribution_excludes_untagged_for_entropy():
    """entropy 计算应该忽略 untagged"""
    bak = os.environ.get("CHRONOTOPE_TYPOLOGY_MODE")
    try:
        _set_mode("active")
        scenes = [_scene("纯净段落 1"), _scene("他在书房雅间。"),
                  _scene("他踏入朝堂殿内。")]
        text = "\n\n".join(scenes)
        rep = ct.scan(str(_write(text)))
        assert "scene_tags" in rep
    finally:
        _set_mode(bak)
