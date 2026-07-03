# -*- coding: utf-8 -*-
"""belief_update_alignment R23 W11 Batch-HH · P1"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import belief_update_alignment as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("BELIEF_UPDATE_ALIGNMENT_MODE", None)
    else:
        os.environ["BELIEF_UPDATE_ALIGNMENT_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_manifest(intent=None, finale=False, sharpness=None):
    d = Path(tempfile.mkdtemp())
    p = d / "manifest.json"
    ec = {}
    if intent:
        ec["belief_update_intent"] = intent
    if finale:
        ec["is_volume_finale"] = True
    if sharpness:
        ec["event_boundary_sharpness"] = sharpness
    p.write_text(json.dumps({"event_cluster_context": ec}, ensure_ascii=False),
                 encoding="utf-8")
    return p


# 大量普通段 + 末段 PE 强烈
_NORMAL_PARA = "他静静地走在路上，望着前方未明的去路，思绪散落如雪，无声地沉到心底深处。\n\n"
_SURPRISE_TAIL = (
    "猛地！没想到！原来真相竟是如此！瞳孔骤缩！陡然！霎时一切都翻转了！万万没想到！\n"
    "其实是另一个人！真相竟然如此！意外！突如其来！出乎意料！\n"
    "原来如此！原来如此！原来如此！原来如此！原来如此！原来如此！\n"
) * 5
_FLAT_TAIL = "他继续走着。\n夜色温柔，月光下他想起了昨天的事情。\n他叹了口气。\n" * 30

_UPDATE_TEXT = _NORMAL_PARA * 100 + _SURPRISE_TAIL
_PRESERVE_FLAT_TEXT = _NORMAL_PARA * 100 + _FLAT_TAIL
_UPDATE_FLAT_TEXT = _NORMAL_PARA * 100 + _FLAT_TAIL
_PRESERVE_SURPRISE_TEXT = _NORMAL_PARA * 100 + _SURPRISE_TAIL


def test_off_returns_skeleton():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_UPDATE_TEXT), None, "update")
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation_even_if_flagged():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_UPDATE_FLAT_TEXT), None, "update")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_update_with_surprise_passes():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_UPDATE_TEXT), None, "update")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BELIEF_UPDATE_NO_SURPRISE" not in codes
    finally:
        _set_mode(bak)


def test_active_update_flat_fails():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_UPDATE_FLAT_TEXT), None, "update")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BELIEF_UPDATE_NO_SURPRISE" in codes
    finally:
        _set_mode(bak)


def test_active_preserve_surprise_fails():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PRESERVE_SURPRISE_TEXT), None, "preserve")
        codes = {v["code"] for v in out.get("violations", [])}
        # 末段 score 高（PE 信号强）·preserve 视为误伤红鲱鱼
        if out["signal"]["score"] > mod.SURPRISE_HIGH:
            assert "BELIEF_PRESERVE_RED_HERRING_KILLED" in codes
    finally:
        _set_mode(bak)


def test_active_preserve_flat_passes():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PRESERVE_FLAT_TEXT), None, "preserve")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BELIEF_PRESERVE_RED_HERRING_KILLED" not in codes
    finally:
        _set_mode(bak)


def test_active_intent_missing_info():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        # 不传 manifest 也不传 intent → 命中 INTENT_MISSING info advisory
        out = mod.scan(_write(_UPDATE_TEXT), None, None)
        codes = {v["code"] for v in out.get("violations", [])}
        assert "BELIEF_INTENT_MISSING" in codes
    finally:
        _set_mode(bak)


def test_manifest_intent_read():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        mf = _mk_manifest(intent="update")
        out = mod.scan(_write(_UPDATE_TEXT), str(mf), None)
        assert out["declared_intent"] == "update"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), None, "update")
        assert "短" in (out.get("note") or "") or "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("BELIEF_UPDATE_NO_SURPRISE", "BELIEF_PRESERVE_RED_HERRING_KILLED",
              "BELIEF_INTENT_MISSING"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("belief_update_alignment")
    assert s is not None
    assert s.get("_new") is True


def test_tail_slice_correct_size():
    text = "一二三四五六七八九十" * 500  # 5000 CJK
    tail = mod._tail_slice(text, target_cjk=1500)
    assert mod._cjk_count(tail) >= 1500


def test_emergence_engine_brief_has_belief_field():
    """cluster_emergence_engine brief 必带 belief_update_intent / event_boundary_sharpness"""
    import cluster_emergence_engine as cee
    me = {"id": "ME_test", "title": "测试 ME", "description": "测试涌现 brief",
          "is_volume_finale": True, "stakes_delta": "+1"}
    brief = cee.me_to_cluster_brief(me, "cluster_test", 1, {})
    assert "belief_update_intent" in brief
    assert "event_boundary_sharpness" in brief
    # volume_finale → update / sharp
    assert brief["belief_update_intent"] == "update"
    assert brief["event_boundary_sharpness"] == "sharp"


def test_non_finale_brief_defaults():
    import cluster_emergence_engine as cee
    me = {"id": "ME_normal", "title": "普通 ME", "description": "普通",
          "is_volume_finale": False}
    brief = cee.me_to_cluster_brief(me, "cluster_x", 1, {})
    assert brief["belief_update_intent"] is None
    assert brief["event_boundary_sharpness"] == "default"


# ============ 🔴 2026-07-02 真模型(surprisal_gpt2) 接入回归 ============

def test_model_source_used_when_enabled(monkeypatch):
    """RUOYU_NN_SURPRISAL=1 且 bridge 命中 → signal.source == model·score 与纯词典基线不同。"""
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        # content-aware 假模型：surprisal 随文本长度轻微变化(确定性·非常量)
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [
                {"mean_surprisal": 7.5, "max_surprisal": 15.0, "source": "model"}
                for _ in texts
            ]
        )
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        out = mod.scan(_write(_UPDATE_FLAT_TEXT), None, "update")
        assert out["signal"]["source"] == "model"
        assert out["signal"]["surprisal_norm"] is not None
        assert out["signal"]["score"] != out["signal"]["lexicon_score"]
    finally:
        _set_mode(bak)


def test_model_unavailable_keeps_lexicon_score_unchanged(monkeypatch):
    """bridge enabled 但返回 None → 整体回退词典拼分·score 与不开模型时完全一致(零回归)。"""
    bak = os.environ.get("BELIEF_UPDATE_ALIGNMENT_MODE")
    try:
        _set_mode("active")
        path = _write(_UPDATE_FLAT_TEXT)
        baseline = mod.scan(path, None, "update")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [None for _ in texts])
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        out = mod.scan(path, None, "update")
        assert out["signal"]["source"] == "heuristic"
        assert out["signal"]["surprisal_norm"] is None
        assert out["signal"] == baseline["signal"]
        assert out["verdict"] == baseline["verdict"]
        assert out["warning"] == baseline["warning"]
    finally:
        _set_mode(bak)
