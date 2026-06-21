# -*- coding: utf-8 -*-
"""author_signature_preservation R24 W12 Batch-KK · P1"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import author_signature_preservation as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("AUTHOR_SIGNATURE_MODE", None)
    else:
        os.environ["AUTHOR_SIGNATURE_MODE"] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project_with_slots(slots, cluster_key="001"):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    ec = {
        "clusters": [{
            "cluster_id": f"cluster_{cluster_key}",
            "scene_storyboard": [{
                "ch": 1,
                "author_signature_slots": slots,
            }],
        }]
    }
    (db / "事件簇.json").write_text(json.dumps(ec, ensure_ascii=False),
                                    encoding="utf-8")
    return proj


_SIG_VERBATIM = "他说：「我命由我不由天。」"
_SIG_NEAR = "她笑了笑，没说什么。"


def test_off_returns_skeleton():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write_draft("xxx"), None, "001")
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_no_slots_in_project_skips():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("active")
        proj = Path(tempfile.mkdtemp())
        out = mod.scan(_write_draft("正文" * 200), proj, "001")
        assert out["slot_count"] == 0
        assert "跳过" in (out.get("note") or "")
    finally:
        _set_mode(bak)


def test_verbatim_slot_present_passes():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_slots([{
            "slot_id": "credo",
            "text": _SIG_VERBATIM,
            "preserve_policy": "verbatim",
        }])
        draft = f"上文铺垫……{_SIG_VERBATIM}下文继续。" * 5
        out = mod.scan(_write_draft(draft), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        # 只剩 OK info
        assert "AUTHOR_SIGNATURE_MISMATCH" not in codes
        assert "AUTHOR_SIGNATURE_NOT_PLACED" not in codes
    finally:
        _set_mode(bak)


def test_verbatim_slot_absent_emits_not_placed():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_slots([{
            "slot_id": "credo",
            "text": "完全不存在的句子绝对不会出现",
            "preserve_policy": "verbatim",
        }])
        draft = "随便写点东西。" * 200
        out = mod.scan(_write_draft(draft), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "AUTHOR_SIGNATURE_NOT_PLACED" in codes
    finally:
        _set_mode(bak)


def test_near_verbatim_punct_only_passes():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("active")
        proj = _mk_project_with_slots([{
            "slot_id": "tag",
            "text": "她笑了笑，没说什么。",
            "preserve_policy": "near_verbatim_punct_only",
        }])
        # 改了标点
        draft = "上文。她笑了笑;没说什么!下文。" * 5
        out = mod.scan(_write_draft(draft), proj, "001")
        codes = {v["code"] for v in out.get("violations", [])}
        assert "AUTHOR_SIGNATURE_MISMATCH" not in codes
    finally:
        _set_mode(bak)


def test_codes_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("AUTHOR_SIGNATURE_MISMATCH", "AUTHOR_SIGNATURE_NOT_PLACED",
              "AUTHOR_SIGNATURE_OK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag_and_supports_anchor():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("author_signature_preservation")
    assert s is not None
    assert s.get("_new") is True
    assert s.get("_supports_anchor") is True


def test_lev_distance_basic():
    assert mod._lev_distance("abc", "abc") == 0
    assert mod._lev_distance("abc", "abd") == 1
    assert mod._lev_distance("", "abc") == 3
    assert mod._lev_distance("abc", "") == 3


def test_strip_punct_removes_cjk_punct():
    s = mod._strip_punct("她说：「天哪！」")
    assert s == "她说天哪"


def test_find_best_window_finds_exact():
    draft = "前面的内容然后他说：「我命由我」结束"
    s, e, win, ratio = mod._find_best_window(draft, "他说：「我命由我」")
    assert ratio == 0.0
    assert "他说" in win


def test_check_slot_verbatim_exact_match():
    rec = mod._check_slot("xxx他说：「我命由我」yyy", {
        "slot_id": "x", "text": "他说：「我命由我」",
        "preserve_policy": "verbatim",
    })
    assert rec["verdict"] == "ok_verbatim"


def test_check_slot_mismatch():
    rec = mod._check_slot("他说：「我命由地不由我」太长了", {
        "slot_id": "x", "text": "他说：「我命由我不由天」",
        "preserve_policy": "verbatim",
    })
    assert rec["verdict"] in ("mismatch", "miss")


def test_shadow_no_violation():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project_with_slots([{
            "slot_id": "credo", "text": "缺失的话",
            "preserve_policy": "verbatim",
        }])
        out = mod.scan(_write_draft("xxx" * 200), proj, "001")
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_build_manifest_injects_author_signature():
    """build_manifest 在 AUTHOR_SIGNATURE_MODE=active 时注入 directive"""
    src = (_SCRIPTS / "build_manifest.py").read_text(encoding="utf-8")
    assert "author_signature_slots" in src
    assert "AUTHOR_SIGNATURE_MODE" in src
    assert "MUST PRESERVE EXACTLY" in src or "preserve_policy" in src


def test_collect_author_signature_slots_helper():
    """helper 从 cluster 取 slots"""
    sys.path.insert(0, str(_SCRIPTS))
    import build_manifest as bm
    c = {
        "scene_storyboard": [
            {"ch": 1, "author_signature_slots": [
                {"slot_id": "a", "text": "X", "preserve_policy": "verbatim"},
            ]}
        ],
        "author_signature_slots": [
            {"slot_id": "b", "text": "Y", "preserve_policy": "verbatim"},
        ],
    }
    slots = bm._collect_author_signature_slots(c)
    assert len(slots) == 2
    assert slots[0]["slot_id"] == "a"
    assert slots[1]["slot_id"] == "b"


def test_collect_author_signature_slots_empty():
    sys.path.insert(0, str(_SCRIPTS))
    import build_manifest as bm
    assert bm._collect_author_signature_slots({}) == []
    assert bm._collect_author_signature_slots({"scene_storyboard": []}) == []


def test_strip_changes_marker():
    assert "yada" not in mod._strip_changes("正文\n---CHANGES---\nyada")


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("AUTHOR_SIGNATURE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
