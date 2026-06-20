# -*- coding: utf-8 -*-
"""genre_dominance_scanner R11 W6 MODEST 回归(确定性·零依赖)"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import genre_dominance_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("GENRE_DOMINANCE_MODE", None)
    else:
        os.environ["GENRE_DOMINANCE_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(packs, primary=None, dominance=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"author_genre_packs": packs}, ensure_ascii=False),
        encoding="utf-8")
    if primary:
        (proj / "_数据库" / "fusion_declaration.json").write_text(
            json.dumps({"primary": primary, "dominance_ratio": dominance or {}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


# 多场景草稿(段落间空行切场景) — 重 romance 标记 + 弱 xianxia 标记
_INVERSION_DRAFT = ("\n\n".join([
    "她心跳加速，脸红耳根，怀里温柔暖意环绕，气息相贴。" * 16,
    "他低语告白，誓言深深守护，唇角微扬眼神湿润纠缠。" * 16,
    "拥抱在怀，胸膛贴近，亲吻暧昧承诺再次重逢。" * 16,
    "灵气一闪，飞剑掠空。仙气漫漫。" * 4,
]) + "\n")

_STARVED_DRAFT = ("\n\n".join([
    "她心跳加速，脸红耳根，怀里温柔暖意环绕。" * 16,
    "他低语告白，誓言深深守护，唇角微扬。" * 16,
    "拥抱在怀，胸膛贴近，亲吻暧昧承诺。" * 16,
    "灵气一闪。日落山头。" * 2,
    "他笑着望她，心上人，承诺，定情信物。" * 16,
]) + "\n")

# 用于无 packs 场景的"长草稿"
_LONG_PLAIN = "她心跳脸红怀里温柔暖意环绕气息相贴。\n\n他低语告白誓言深深守护。\n\n" * 30


def test_off():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_INVERSION_DRAFT))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_packs_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LONG_PLAIN))
        assert "无多 pack" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_single_pack_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["romance"])
        out = mod.scan(_write(_LONG_PLAIN), proj)
        assert "无多 pack" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_inversion_flag():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
        assert out["per_pack_total"]["romance"] > out["per_pack_total"]["xianxia"]
        codes = [f["code"] for f in out["flags"]]
        assert "GENRE_DOMINANCE_INVERSION" in codes or "GENRE_PRIMARY_STARVED" in codes
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_active_primary_starved():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_STARVED_DRAFT), proj)
        codes = [f["code"] for f in out["flags"]]
        assert "GENRE_PRIMARY_STARVED" in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        out = mod.scan(_write(_INVERSION_DRAFT), proj)
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_packs_no_file():
    proj = Path(tempfile.mkdtemp())
    assert mod._read_genre_packs(proj) == []
    assert mod._read_genre_packs(None) == []


def test_load_lexicon_missing_pack():
    assert mod._load_marker_lexicon("bogus_pack") == []


def test_gini_zero_all_zero():
    assert mod._gini([0, 0, 0]) == 0.0


def test_gini_uniform():
    g = mod._gini([1, 1, 1, 1])
    assert g < 0.05


def test_few_scenes_skip():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "romance"], primary="xianxia")
        # 单段·无空行
        out = mod.scan(_write("她心跳加速脸红耳根怀里温柔。" * 60), proj)
        assert "场景数 <2" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get("GENRE_DOMINANCE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
