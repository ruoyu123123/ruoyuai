# -*- coding: utf-8 -*-
"""dwell_progression_scanner R20 W9 Batch-BB · P2 · iyashikei dwell/progression
确定性·零依赖。"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import dwell_progression_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("DWELL_PROGRESSION_MODE", None)
    else:
        os.environ["DWELL_PROGRESSION_MODE"] = m


def _mk_project(genre_tags=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    payload = {}
    if genre_tags:
        payload["genre_tags"] = genre_tags
    if baseline:
        payload["dwell_progression_baseline"] = baseline
    if payload:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return proj


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 治愈风：大量 ambient / dwell 段
_HEAL_TEXT = "\n\n".join([
    "春雨落在屋檐，风声轻轻。",
    "我看着窗外，感觉一切都慢下来。",
    "院子里落叶飘飞，秋风带着凉意，月光照在地上。",
    "她听着雨声，闻到木头的气息，自己也安心。",
    "夏夜的虫鸣阵阵，风从纱窗吹进来，云层很薄。",
    "我倾听屋外的脚步，感觉到温度的变化。",
] * 6)

# 推进密集：全对话 + 感叹号
_PLOT_DENSE = "\n\n".join([
    "「你来了我必须告诉你这个秘密！」他大声说。",
    "「我必须见你不然永远不会知道！」她回喊。",
    "「告诉我真相否则就走人！」他逼问到底。",
    "「不行你永远不能知道这个秘密！」她坚持。",
    "「你必须告诉我现在就告诉我！」他急了。",
    "「现在就走再不走就来不及了！」她推开他。",
] * 20)


def test_off():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_HEAL_TEXT), _mk_project(genre_tags=["iyashikei_healing"]))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_non_iyashikei_silent():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PLOT_DENSE), _mk_project(genre_tags=["爽文"]))
        assert out.get("genre_silent") is True
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_iyashikei_dwell_high_pass():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_HEAL_TEXT), _mk_project(genre_tags=["iyashikei_healing"]))
        # 治愈文本应该 dwell+ambient 都不少
        assert out["is_iyashikei_genre"] is True
        assert out["type_counts"]["plot_beat"] < out["paragraphs_total"]
    finally:
        _set_mode(bak)


def test_iyashikei_plot_dense_flagged():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PLOT_DENSE), _mk_project(genre_tags=["iyashikei_healing"]))
        codes = {f["code"] for f in out.get("flags", [])}
        # 全对话+感叹 → progression_density ≈ 1.0 → flag
        assert "PROGRESSION_TOO_DENSE" in codes or "DWELL_RATIO_LOW" in codes
    finally:
        _set_mode(bak)


def test_classify_paragraph_plot_beat_on_quote():
    p = "「来吧」他说。"
    assert mod._classify_paragraph(p) == "plot_beat"


def test_classify_paragraph_ambient_on_weather():
    p = "春雨落下，秋风轻拂，月光满地。"
    assert mod._classify_paragraph(p) == "ambient_setup"


def test_classify_paragraph_dwell_on_sense():
    p = "我看着远方，感觉一切都凝固。"
    assert mod._classify_paragraph(p) == "dwell"


def test_baseline_override():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PLOT_DENSE),
                       _mk_project(genre_tags=["iyashikei_healing"],
                                   baseline={"dwell_ratio_min": 0.0,
                                             "progression_density_max": 1.0}))
        assert out["baseline_source"] == "author_profile"
        codes = {f["code"] for f in out.get("flags", [])}
        assert "DWELL_RATIO_LOW" not in codes
        assert "PROGRESSION_TOO_DENSE" not in codes
    finally:
        _set_mode(bak)


def test_short_skipped():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("春雨。"),
                       _mk_project(genre_tags=["iyashikei_healing"]))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("DWELL_PROGRESSION_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_PLOT_DENSE),
                       _mk_project(genre_tags=["iyashikei_healing"]))
        assert out["violations"] == []
    finally:
        _set_mode(bak)
