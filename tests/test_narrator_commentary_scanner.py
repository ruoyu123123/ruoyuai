# -*- coding: utf-8 -*-
"""narrator_commentary_scanner 专属测试 (R9 W5 Batch-M·L41·2026-06-20)

钉死：
  · evaluative summary 句式正则命中
  · 三指标 (density / chapter-end share / intra-action) 计算
  · 作者档 narrator_voice_signature.commentary_target_per_1k 第一权威
  · F3 AnchoredAI · violations 必含 anchor_span(char_start/char_end/surface_text)
  · shadow / off / active 三态
  · 永远 advisory · 不在 HARD_GATE_CODES
  · 与 R8 L25 metalepsis 无词典重叠 (frame-breaking marker vs telling-weight evaluative)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import narrator_commentary_scanner as nc  # noqa: E402


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(*, narrator_voice_signature=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if narrator_voice_signature is not None:
        obj["narrator_voice_signature"] = narrator_voice_signature
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


def _set_mode(m):
    if m is None:
        os.environ.pop("NARRATOR_COMMENTARY_MODE", None)
    else:
        os.environ["NARRATOR_COMMENTARY_MODE"] = m


_CLEAN_FILLER = "夜风扫过山脊，他独自向上踏步，影子被拉得很长。" * 25
_EVAL_HEAVY = (
    "显然，他错了。" * 8 + "不得不说，这便是命运。" * 6 + "毋庸置疑，他无法回头。" * 6
    + "众所周知，山下风大。" * 4 + _CLEAN_FILLER)


def test_off_returns_skeleton():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("off")
        rep = nc.scan(str(_write(_EVAL_HEAVY)))
        assert rep["mode"] == "off"
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
    finally:
        _set_mode(bak)


def test_short_draft_skips():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(_write("显然。")))
        assert "太短" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_clean_draft_passes():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(_write(_CLEAN_FILLER)))
        assert rep["verdict"] == "PASS"
        assert rep["violations"] == []
        # 至少计算了密度字段
        assert "commentary_density_per_1k" in rep
    finally:
        _set_mode(bak)


def test_evaluative_overuse_active_fail():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(_write(_EVAL_HEAVY)))
        codes = [v["code"] for v in rep["violations"]]
        assert "NARRATOR_COMMENTARY_OVERUSE" in codes
        assert rep["verdict"] == "FAIL_MINOR"
        assert rep["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_shadow_mode_no_report():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("shadow")
        rep = nc.scan(str(_write(_EVAL_HEAVY)))
        assert rep["mode"] == "shadow"
        assert rep["violations"] == []
        assert rep["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_anchor_span_present_in_violations():
    """F3 AnchoredAI · 每条 violation 必须带 anchor_span"""
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(_write(_EVAL_HEAVY)))
        assert len(rep["violations"]) >= 1
        for v in rep["violations"]:
            assert "anchor_span" in v
            anchor = v["anchor_span"]
            assert "char_start" in anchor
            assert "char_end" in anchor
            assert "surface_text" in anchor
            assert anchor["char_end"] > anchor["char_start"]
            assert isinstance(anchor["surface_text"], str)
            assert len(anchor["surface_text"]) > 0
    finally:
        _set_mode(bak)


def test_author_profile_target_first_authority():
    """作者档 commentary_target_per_1k = 第一权威"""
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        # 作者档允许高密度 evaluative
        proj = _mk_project(narrator_voice_signature={"commentary_target_per_1k": 5.0})
        rep = nc.scan(str(_write(_EVAL_HEAVY)), project_root=proj)
        assert rep["target_source"] == "author_profile"
        assert rep["commentary_target_per_1k"] == 5.0
    finally:
        _set_mode(bak)


def test_uniform_fallback_when_no_profile():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(_write(_EVAL_HEAVY)))
        assert rep["target_source"] == "uniform_fallback"
        assert rep["commentary_target_per_1k"] == 0.8
    finally:
        _set_mode(bak)


def test_chapter_end_share_measured():
    """末 20% evaluative 集中 → chapter_end share 偏高"""
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        text = _CLEAN_FILLER + "显然，他错了。" * 10 + "毋庸置疑，命运至此。" * 8
        rep = nc.scan(str(_write(text)))
        # 末段 evaluative 集中
        assert rep["chapter_end_commentary_share"] > 0
    finally:
        _set_mode(bak)


def test_intra_action_density_measured():
    """动作段内插评价 → intra_action_density > 0"""
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        # 段内含动作动词 + evaluative
        action_paras = ("他掏出长剑挥手向前。显然，对手太弱。\n"
                        "他转身扑上去。毋庸置疑，胜负已定。\n") * 6
        text = action_paras + _CLEAN_FILLER
        rep = nc.scan(str(_write(text)))
        assert rep["intra_action_density"] > 0
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    rg = Path(__file__).resolve().parents[1] / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "NARRATOR_COMMENTARY_OVERUSE" not in hgs


def test_audit_hub_hard_gate_codes_no_intersect():
    """与 audit_hub HARD_GATE_CODES 也无交集 (15 码不变)"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
    import audit_hub
    assert "NARRATOR_COMMENTARY_OVERUSE" not in audit_hub.HARD_GATE_CODES


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        os.environ["NARRATOR_COMMENTARY_MODE"] = "garbage"
        assert nc._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(Path(tempfile.mkdtemp()) / "missing.txt"))
        assert "读取失败" in rep.get("note", "")
    finally:
        _set_mode(bak)


def test_changes_section_stripped():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        body = _CLEAN_FILLER
        # CHANGES 段后大量 evaluative 不应进入正文统计
        post = "---CHANGES---\n" + "显然，他错了。毋庸置疑。" * 30
        rep = nc.scan(str(_write(body + "\n" + post)))
        # commentary_count 应非常低
        assert rep["commentary_count"] < 5
    finally:
        _set_mode(bak)


def test_no_dict_overlap_with_metalepsis():
    """L41 vs R8 L25 metalepsis · evaluative 词典不包含 metalepsis marker"""
    import narrator_commentary_scanner as ncs
    pattern_str = ncs.EVALUATIVE_PREDICATES.pattern
    forbidden = ["【系统提示】", "【作者注】", "亲爱的读者", "诸位看官", "▶"]
    for term in forbidden:
        assert term not in pattern_str


def test_invalid_target_falls_back():
    """作者档 commentary_target_per_1k 非数值 → 兜底"""
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(narrator_voice_signature={"commentary_target_per_1k": "bad"})
        rep = nc.scan(str(_write(_EVAL_HEAVY)), project_root=proj)
        assert rep["target_source"] == "uniform_fallback"
    finally:
        _set_mode(bak)


def test_negative_target_falls_back():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(narrator_voice_signature={"commentary_target_per_1k": -1})
        rep = nc.scan(str(_write(_EVAL_HEAVY)), project_root=proj)
        assert rep["target_source"] == "uniform_fallback"
    finally:
        _set_mode(bak)


def test_no_action_paras_intra_density_zero():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        # 无动作动词只评价
        text = ("显然如此。" * 5 + "毋庸置疑。" * 5) * 5 + _CLEAN_FILLER
        rep = nc.scan(str(_write(text)))
        # intra_action 没动作段 → 0
        assert rep["intra_action_density"] == 0.0
    finally:
        _set_mode(bak)


def test_split_sentences_basic():
    out = nc._split_sentences_with_pos("一句。两句！三句？尾不带符")
    assert len(out) == 4
    assert out[0][0].startswith("一")
    # 顺序 char_start 单调
    starts = [s[1] for s in out]
    assert starts == sorted(starts)


def test_anchor_span_within_bounds():
    bak = os.environ.get("NARRATOR_COMMENTARY_MODE")
    try:
        _set_mode("active")
        rep = nc.scan(str(_write(_EVAL_HEAVY)))
        for v in rep["violations"]:
            anchor = v["anchor_span"]
            # surface_text 长度合理
            assert 0 < len(anchor["surface_text"]) <= 200
    finally:
        _set_mode(bak)
