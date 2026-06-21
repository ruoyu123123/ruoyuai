# -*- coding: utf-8 -*-
"""premise_blend_card_scanner R22 W10 Batch-FF · P1+P2 · CBT 卡落地度 advisory"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import premise_blend_card_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "premise_blend_card_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("PREMISE_BLEND_MODE", None)
    else:
        os.environ["PREMISE_BLEND_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(card=None, baseline=None, cluster_id="cluster_001"):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if card is not None:
        shijianji = {"clusters": [{"cluster_id": cluster_id,
                                    "premise_blend_card": card}]}
        (proj / "_数据库" / "事件簇.json").write_text(
            json.dumps(shijianji, ensure_ascii=False), encoding="utf-8")
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    return proj


_DRAFT_RICH = ("战士就是诗人。诗人因为战场而流泪。" * 50)
_DRAFT_BARE = ("天气好。屋子大。桌上是茶。" * 80)


def test_off_returns_skeleton():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DRAFT_RICH), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_card_missing_advisory():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRAFT_RICH), _mk_project())  # 无卡
        codes = {v["code"] for v in out.get("violations", [])}
        assert "PREMISE_BLEND_CARD_MISSING" in codes
        assert out["card_present"] is False
    finally:
        _set_mode(bak)


def test_card_present_with_emergent_passes_when_density_ok():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        card = {
            "blend_type": "double_scope",
            "input_space_A": {"frame": "战场", "signature_lexemes": ["战士", "战场"]},
            "input_space_B": {"frame": "诗", "signature_lexemes": ["诗人", "流泪"]},
            "generic_space": "人物 + 处境",
            "emergent_structure": ["诗化战争", "战士的脆弱性", "暴力的美学化"],
            "vital_relations_compressed": ["identity", "causation"],
        }
        out = mod.scan(_write(_DRAFT_RICH), _mk_project(card=card))
        assert out["card_present"] is True
        # density 充足 + A/B 都命中 → 不报 INPUT_DROP / EMERGENCE / LEXICAL_OVER
        codes = {v["code"] for v in out.get("violations", [])}
        assert "PREMISE_BLEND_INPUT_DROP" not in codes
    finally:
        _set_mode(bak)


def test_input_drop_when_lex_missing_in_draft():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        card = {
            "blend_type": "single_scope",
            "input_space_A": {"frame": "战场", "signature_lexemes": ["战士"]},
            "input_space_B": {"frame": "诗", "signature_lexemes": ["俳句", "和歌"]},  # 草稿无
            "generic_space": "人物",
            "emergent_structure": ["a", "b", "c"],
            "vital_relations_compressed": ["identity"],
        }
        out = mod.scan(_write("战士冲过去。战士回来了。" * 100), _mk_project(card=card))
        codes = {v["code"] for v in out.get("violations", [])}
        assert "PREMISE_BLEND_INPUT_DROP" in codes
    finally:
        _set_mode(bak)


def test_emergence_missing_when_density_below_floor():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        # 极低 vital_relations 密度的草稿
        bare = ("远方山岗很高。天空蓝色。河水流淌。" * 100)
        card = {
            "blend_type": "double_scope",
            "input_space_A": {"frame": "山", "signature_lexemes": ["山岗", "远方"]},
            "input_space_B": {"frame": "水", "signature_lexemes": ["河水", "流淌"]},
            "generic_space": "景物",
            "emergent_structure": ["山水交融", "时空压缩", "动静对比"],
            "vital_relations_compressed": ["identity"],
        }
        out = mod.scan(_write(bare), _mk_project(card=card))
        codes = {v["code"] for v in out.get("violations", [])}
        # density 应该低（无 7 类标记词）
        assert "PREMISE_BLEND_EMERGENCE_MISSING" in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他就是英雄。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRAFT_RICH), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_author_band_applied():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("active")
        card = {
            "blend_type": "single_scope",
            "input_space_A": {"frame": "A", "signature_lexemes": ["战士"]},
            "input_space_B": {"frame": "B", "signature_lexemes": ["诗人"]},
            "emergent_structure": ["a", "b", "c"],
            "vital_relations_compressed": [],
        }
        baseline = {"vital_relations_compression": {"mean": 8.0, "std": 1.0}}
        out = mod.scan(_write(_DRAFT_RICH),
                       _mk_project(card=card, baseline=baseline))
        assert out.get("density_band", {}).get("source") == "author_profile"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    s = "正文战士诗人。\n---CHANGES---\nyada"
    out = mod._strip_changes(s)
    assert "yada" not in out


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("PREMISE_BLEND_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def _run_cli(draft_path, project=None, cluster=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    if cluster:
        cmd += ["--cluster", cluster]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PREMISE_BLEND_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_DRAFT_RICH)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "premise_blend_card"


def test_subsystem_skeletons_carries_premise_blend_card_hint():
    """落地保险：subsystem_skeletons.json 事件簇 _cluster_brief_schema_hint 含 premise_blend_card 节"""
    sk_path = _ROOT / "core" / "claude-home" / "templates" / "subsystem_skeletons.json"
    obj = json.loads(sk_path.read_text(encoding="utf-8"))
    shijianji = obj["skeletons"]["事件簇"]
    hint = shijianji.get("_cluster_brief_schema_hint", {})
    assert "premise_blend_card" in hint
    card_hint = hint["premise_blend_card"]
    for k in ("blend_type", "input_space_A", "input_space_B",
              "generic_space", "emergent_structure", "vital_relations_compressed"):
        assert k in card_hint


def test_cluster_emergence_engine_injects_empty_card():
    """落地保险：cluster_emergence_engine.me_to_cluster_brief 注入空 premise_blend_card"""
    sys.path.insert(0, str(_SCRIPTS))
    import cluster_emergence_engine as ce
    me = {"id": "ME-V1-1", "title": "test", "description": "x"}
    brief = ce.me_to_cluster_brief(me, "cluster_002", 1, {})
    assert "premise_blend_card" in brief
    assert brief["premise_blend_card"]["blend_type"] == ""
    assert brief["premise_blend_card"]["emergent_structure"] == []
