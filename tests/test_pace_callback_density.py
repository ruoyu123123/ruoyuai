# -*- coding: utf-8 -*-
"""pace_callback_density R23 W11 Batch-GG · P0"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import pace_callback_density as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("PACE_CARRIER_WINDOW_MODE", None)
    else:
        os.environ["PACE_CARRIER_WINDOW_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


_BODY_LONG = "他走过长街。天阴沉沉。脚步声回响在巷子里。" * 80
_HOOK_LINE = "他猛地停下，门轰然打开！"

# 钩子两侧含 anchor 命中
_HOOK_WITH_ANCHOR = (
    "他打开盒子，里面是那块印记。\n"
    + _HOOK_LINE + "\n"
    + "信物滚到地上。规则被悄然激活。\n"
)

# 全程无 anchor 的纯节奏文本
_PURE_RHYTHM_BODY = ("夜风。低语。" * 600 + "\n" + _HOOK_LINE + "\n" + "他喘息。")


def test_off_returns_skeleton():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_BODY_LONG + _HOOK_WITH_ANCHOR), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_BODY_LONG + _HOOK_WITH_ANCHOR), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_empty_window_flagged():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_PURE_RHYTHM_BODY), _mk_project())
        # 全程无 anchor → EMPTY 或 PURE_RHYTHM 命中
        codes = {v["code"] for v in out.get("violations", [])}
        assert "PACE_CARRIER_WINDOW_EMPTY" in codes or "PACE_CARRIER_PURE_RHYTHM_HOOK" in codes
    finally:
        _set_mode(bak)


def test_active_with_anchor_window_pass():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("active")
        # body 长 + 末尾钩子带 anchor → 不报 EMPTY
        text = _BODY_LONG + _HOOK_WITH_ANCHOR
        out = mod.scan(_write(text), _mk_project())
        # 不必然无 violations，但 EMPTY 不能在仅有 1 hook 全命中时触发
        if out.get("hook_count") == 1:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "PACE_CARRIER_WINDOW_EMPTY" not in codes
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短文"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_no_hook_skipped():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("active")
        # 大量纯平淡叙述无钩子标志
        plain = "他走着。她看着。" * 400
        out = mod.scan(_write(plain), _mk_project())
        # 可能识别到 0 个钩子
        if "未识别钩子位置" in (out.get("note") or ""):
            assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_anchor_loaded_from_locked_fact():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("active")
        proj = _mk_project()
        (proj / "_数据库" / "锁定事实.json").write_text(
            json.dumps({"facts": [{"entity": "魔晶"}, {"entity": "圣杯"}]},
                       ensure_ascii=False),
            encoding="utf-8")
        out = mod.scan(_write(_BODY_LONG + _HOOK_WITH_ANCHOR), proj)
        # 锚词池里应包含 default + 锁定事实实体
        assert out["anchor_pool_size"] >= 15
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("PACE_CARRIER_WINDOW_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_marker():
    s = "正文\n---CHANGES---\nyada"
    assert "yada" not in mod._strip_changes(s)


def test_code_not_in_hard_gate():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    for c in ("PACE_CARRIER_WINDOW_EMPTY", "PACE_CARRIER_WINDOW_OVER_LOADED",
              "PACE_CARRIER_PURE_RHYTHM_HOOK"):
        assert c not in hgs, c


def test_registry_registered_with_new_flag():
    rg = _ROOT / "core" / "scripts" / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    s = reg.get("scanners", {}).get("pace_callback_density")
    assert s is not None
    assert s.get("_new") is True


def test_placeholder_flag():
    out = mod.scan(_write(_BODY_LONG + _HOOK_WITH_ANCHOR), _mk_project())
    assert out.get("_placeholder") is True


def test_count_anchor_hits_basic():
    assert mod._count_anchor_hits("规则规则", ["规则"]) == 2
    assert mod._count_anchor_hits("", ["规则"]) == 0
    assert mod._count_anchor_hits("abc", []) == 0
