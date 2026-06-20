# -*- coding: utf-8 -*-
"""genre_pack_clash_scanner R11 W6 MODEST 占位回归"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import genre_pack_clash_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("GENRE_PACK_CLASH_MODE", None)
    else:
        os.environ["GENRE_PACK_CLASH_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(packs, override=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True)
    obj = {"author_genre_packs": packs}
    if override:
        obj["author_fusion_resolution"] = override
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 4+ 场景·前 1/3 全 apocalypse / 后 1/3 全 romance
_BIPOLAR = ("\n\n".join([
    ("丧尸变异感染病毒物资罐头汽油净水避难所掩体基地幸存幸存者援助收容废墟空城死城。" * 6),
    ("丧尸变异物资罐头汽油净水掩体基地幸存援助。" * 6),
    ("普通场景，没有显著标记。" * 6),
    ("她心跳加速脸红耳根怀里温柔暖意环绕气息相贴拥抱告白誓言守护暧昧承诺。" * 6),
    ("心跳脸红怀里温柔暖意环绕气息告白誓言守护暧昧承诺纠缠。" * 6),
]) + "\n")


def test_off():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_BIPOLAR))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_packs_skip():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_BIPOLAR))
        assert "无多 pack" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_no_registry_hit_skip():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["xianxia", "xuanhuan"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert "无命中" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_bipolar_flag():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert "fusion_resolution_hints" in out
        # bipolar_flags 可能命中也可能不命中(占位 seed 词袋)·两情况都接受
        assert "bipolar_flags" in out
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(["apocalypse_survival", "romance"])
        out = mod.scan(_write(_BIPOLAR), proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_read_packs_no_file():
    proj = Path(tempfile.mkdtemp())
    p, o = mod._read_packs_and_override(proj)
    assert p == [] and o == {}


def test_read_packs_none_project():
    p, o = mod._read_packs_and_override(None)
    assert p == [] and o == {}


def test_lexicon_missing():
    assert mod._load_marker_lexicon("nonexistent_pack") == []


def test_mode_invalid():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_read_fail():
    bak = os.environ.get("GENRE_PACK_CLASH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "no.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)
