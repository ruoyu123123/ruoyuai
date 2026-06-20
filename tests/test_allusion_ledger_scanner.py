# -*- coding: utf-8 -*-
"""allusion_ledger_scanner R11 W6 MODEST 回归(确定性·零依赖)"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import allusion_ledger_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("ALLUSION_LEDGER_MODE", None)
    else:
        os.environ["ALLUSION_LEDGER_MODE"] = m


def _write(text):
    p = Path(tempfile.mkdtemp()) / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 含承重典故无 gloss(决策动词跟随)
_LOAD_BEARING = ("他想起守株待兔的故事，便决定就此等下去不再寻觅。" * 8 +
                 "\n" + "他思来想去，于是塞翁失马，便选择放手不管。" * 8 +
                 "\n" + "若是邯郸学步，反失其本，于是即刻收手。" * 8 +
                 "\n" + "想起精卫填海的故事，便不再多言遂走。" * 8)

# 典故有 gloss(意思是/即/便是)
_WITH_GLOSS = ("守株待兔的典故，意思是固守不变。他望着远方。" * 24 +
               "\n" + "庄周梦蝶，即物我两忘。他笑了。" * 24)


def test_off():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_LOAD_BEARING))
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_skip():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短。" * 3))
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_read_fail():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "no.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_active_load_bearing_flag():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_LOAD_BEARING))
        assert out["bucket_counts"]["chengyu"] > 0
        assert out["load_bearing_no_gloss"]
        assert out["violations"]
        assert out["violations"][0]["code"] == "LOAD_BEARING_ALLUSION_NO_GLOSS"
        assert out["gate_level"] == "advisory"
    finally:
        _set_mode(bak)


def test_with_gloss_no_load_bearing():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_WITH_GLOSS))
        # gloss 跟随就不算 load-bearing(去重首现)
        assert out["bucket_counts"]["chengyu"] > 0
        # 没有承重 advisory
        codes = [v.get("code") for v in out["violations"]]
        assert "LOAD_BEARING_ALLUSION_NO_GLOSS" not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_LOAD_BEARING))
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_detect_buckets():
    res = mod.detect_allusions("守株待兔与桃花源相映。",
                                {"chengyu": ["守株待兔"],
                                 "classical_locus": ["桃花源"],
                                 "pop_modern_ref": []})
    assert len(res["buckets"]["chengyu"]) == 1
    assert len(res["buckets"]["classical_locus"]) == 1


def test_seed_loads():
    seed = mod._load_seed()
    assert "chengyu" in seed
    assert isinstance(seed["chengyu"], list)
    assert len(seed["chengyu"]) > 0


def test_author_signature_attached():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        (proj / "_数据库").mkdir(parents=True)
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"allusion_signature": {"per_1k": 2.0}},
                       ensure_ascii=False), encoding="utf-8")
        out = mod.scan(_write(_LOAD_BEARING), proj)
        assert out["author_allusion_signature"]["per_1k"] == 2.0
    finally:
        _set_mode(bak)


def test_mode_invalid():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_concreteness_no_decision_no_flag():
    bak = os.environ.get("ALLUSION_LEDGER_MODE")
    try:
        _set_mode("active")
        # 典故出现但无决策动词跟随
        d = "守株待兔是个故事啊。" * 120
        out = mod.scan(_write(d))
        assert out["load_bearing_no_gloss"] == []
    finally:
        _set_mode(bak)
