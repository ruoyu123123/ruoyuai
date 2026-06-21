# -*- coding: utf-8 -*-
"""cotton_needle_subtext_advisor R23 W11 Batch-HH · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cotton_needle_subtext_advisor as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("COTTON_NEEDLE_MODE", None)
    else:
        os.environ["COTTON_NEEDLE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(antagonist_style: str | None = None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if antagonist_style is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"antagonist_style": antagonist_style}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 绵针泥刺典型对话：表温情/恭顺 + 隐藏威胁/讽刺
_COTTON_TEXT = (
    "他笑道：“亲爱的好兄弟，注意身体，保重啊。听说外面都在传你最近不太顺，"
    "万一有个三长两短，难道也不想想咱们这些老兄弟？呵呵，真是辛苦你了。”\n"
    "“别忘了上次那回的事情，听您的，我也不愿意提。”\n"
) * 40

_PLAIN_TEXT = "他走在路上，看着远方的山。山很高，路很长。" * 200


def test_off_returns_skeleton():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_COTTON_TEXT), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_COTTON_TEXT), _mk_project("阴狠"))
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_detects_cotton_needle():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_COTTON_TEXT), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "COTTON_NEEDLE_DETECTED" in codes
        assert out["cotton_hits_count"] > 0
    finally:
        _set_mode(bak)


def test_active_plain_no_detect():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PLAIN_TEXT), _mk_project())
        # 无对话段 → 跳过或 hits=0
        if "cotton_hits_count" in out:
            assert out["cotton_hits_count"] == 0
        else:
            assert "跳过" in (out.get("note") or "") or "不足" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_below_target_for_recommended_style():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("active")
        # 仅 1 段绵针对话 + 大量普通段（密度极低 vs 阴狠 1.5/千字）
        text = ("他笑道：“亲爱的老哥，保重身体，万一有个三长两短就麻烦了。”\n"
                + "他走在路上，看着远方的山。\n" * 400)
        out = mod.scan(_write(text), _mk_project("阴狠"))
        codes = {v["code"] for v in out.get("violations", [])}
        # 可能命中 BELOW_TARGET（实际密度 << 1.5）
        if out.get("target_density_per_1k_dialogue") is not None:
            if (out["density_per_1k_dialogue"] <
                    out["target_density_per_1k_dialogue"] * 0.5):
                assert "COTTON_NEEDLE_BELOW_TARGET" in codes
    finally:
        _set_mode(bak)


def test_active_writes_relations_back():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write(_COTTON_TEXT), proj)
        if out["cotton_hits_count"] > 0:
            rel = proj / "_数据库" / "角色关系.json"
            assert rel.exists()
            obj = json.loads(rel.read_text(encoding="utf-8"))
            assert "subtext_aggression_edges" in obj
            assert "author_intent_card" in obj
            assert obj["author_intent_card"]["cotton_needle_observation_count"] > 0
    finally:
        _set_mode(bak)


def test_active_relations_idempotent():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        # 跑两次·边数应不翻倍（snippet_hash 去重）
        path = _write(_COTTON_TEXT)
        mod.scan(path, proj)
        first = json.loads((proj / "_数据库" / "角色关系.json").read_text(encoding="utf-8"))
        first_n = len(first.get("subtext_aggression_edges", []))
        mod.scan(path, proj)
        second = json.loads((proj / "_数据库" / "角色关系.json").read_text(encoding="utf-8"))
        second_n = len(second.get("subtext_aggression_edges", []))
        assert first_n == second_n, "snippet_hash 去重失效"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), _mk_project())
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("COTTON_NEEDLE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_lexicon_loadable():
    lex = mod._load_lexicon()
    assert "surface_warmth_lexicon" in lex
    assert "subtext_aggression_lexicon" in lex
    assert "antagonist_styles" in lex


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("COTTON_NEEDLE_DETECTED", "COTTON_NEEDLE_BELOW_TARGET",
              "COTTON_NEEDLE_OVER"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("cotton_needle_subtext_advisor")
    assert s is not None
    assert s.get("_new") is True


def test_dialogue_extraction_works():
    text = '他笑道：“亲爱的好兄弟”然后又「不会吧」'
    out = mod._extract_dialogues(text)
    assert any("亲爱" in d for d in out)
