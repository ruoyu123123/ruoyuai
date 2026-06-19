# -*- coding: utf-8 -*-
"""synesthesia_density_scanner.py 专属回归测试（2026-06-20·确定性·零依赖·零 LLM/零联网）。

覆盖契约五分支 + 合成草稿【真命中】（非空壳）：
  ① active 通感密度过高 → FAIL_MINOR + warning + violations 携 per_1k/syn_count；
  ② 干净草稿（0 通感）→ PASS；
  ③ shadow 模式：高密度也只记不判（violations 空·verdict PASS·零回归）；
  ④ cjk<500 短稿跳过（note·不算 per_1k）；
  ⑤ off 模式只返骨架；
  + 辅助：_strip_changes / _cjk_count / _mode 回落 + detect 真抓词 + gate_level 恒 advisory
    + 不进 HARD_GATE + main() CLI 退出码（subprocess 真跑 argparse）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import synesthesia_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "synesthesia_density_scanner.py"

# 单段含 6 处明显通感（覆盖 6 条 regex 分支各一）
_SYN_UNIT = "刺耳的光照进来，冰冷的声音传来，明亮的气味弥漫四周，甜的风拂过脸庞，声音很湿，气味很刺眼。"
# 干净展示式段落（0 通感·合法搭配）
_CLEAN_UNIT = "他推开门，桌上的信封压着一枚铜钥匙。她笑着接过茶，指尖在杯沿停了一瞬。窗外的雨落在青石板上。"


def _set_mode(m):
    if m is None:
        os.environ.pop("SYNESTHESIA_MODE", None)
    else:
        os.environ["SYNESTHESIA_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ── ① active 通感密度过高 → FAIL_MINOR ───────────────────────────────────────
def test_active_overuse_flags_fail_minor():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("active")
        p = _write(_SYN_UNIT * 20)  # >500 CJK·120 处通感·per_1k 远超 1.5
        out = mod.scan(p)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["warning"] is not None
        assert out["per_1k"] > mod.SYN_PER_1K_FLOOR
        assert out["syn_count"] >= 100
        v = out["violations"][0]
        assert v["kind"] == "synesthesia_overuse" and v["severity"] == "minor"
        assert v["per_1k"] == out["per_1k"] and v["syn_count"] == out["syn_count"]
        assert len(out["sample_phrases"]) == 8  # 截断守护
    finally:
        _set_mode(bak)


# ── ② 干净草稿 → PASS ────────────────────────────────────────────────────────
def test_active_clean_draft_passes():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("active")
        p = _write(_CLEAN_UNIT * 25)  # >500 CJK·0 通感
        out = mod.scan(p)
        assert out["verdict"] == "PASS"
        assert out["warning"] is None
        assert out["syn_count"] == 0 and out["per_1k"] == 0.0
        assert out["violations"] == []
    finally:
        _set_mode(bak)


# ── ③ shadow：只记不判（零回归） ─────────────────────────────────────────────
def test_shadow_records_but_no_violation():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("shadow")
        p = _write(_SYN_UNIT * 20)
        out = mod.scan(p)
        assert out["mode"] == "shadow"
        assert out["per_1k"] > mod.SYN_PER_1K_FLOOR  # 确实超标
        assert out["violations"] == [] and out["verdict"] == "PASS"
        assert out["warning"] is None  # shadow 不上报
    finally:
        _set_mode(bak)


# ── ④ 短稿 cjk<500 跳过 ──────────────────────────────────────────────────────
def test_short_draft_skipped():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("active")
        p = _write(_SYN_UNIT)  # 单段·远不足 500 CJK
        out = mod.scan(p)
        assert out["note"] == "草稿太短·跳过"
        assert "per_1k" not in out
        assert out["verdict"] == "PASS" and out["violations"] == []
    finally:
        _set_mode(bak)


# ── ⑤ off 模式只返骨架 ───────────────────────────────────────────────────────
def test_off_mode_skeleton():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("off")
        p = _write(_SYN_UNIT * 20)
        out = mod.scan(p)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "per_1k" not in out and "note" not in out  # 直返骨架·没跑检测
    finally:
        _set_mode(bak)


# ── 默认 shadow（契约：env 缺省 = shadow·零回归） ────────────────────────────
def test_default_mode_is_shadow():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "active"  # 2026-06-20 金标准校准放量(5真作者per_1k全0零误报)
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "active"  # 放量后非法值回退 active
        _set_mode("ACTIVE")  # 大小写不敏感
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


# ── 辅助函数 + 契约不变量 ────────────────────────────────────────────────────
def test_strip_changes_cuts_separators():
    assert mod._strip_changes("正文。\n\n---CHANGES_FACTUAL---\n{}") == "正文。"
    assert mod._strip_changes("正文。  \n---CHANGES---\nx") == "正文。"
    assert mod._strip_changes("无分隔符原样") == "无分隔符原样"


def test_cjk_count_only_counts_cjk():
    assert mod._cjk_count("你好世界中abc123，。") == 5
    assert mod._cjk_count("") == 0


def test_detect_catches_each_branch():
    """6 条 regex 分支各命中一次（明显跨感官搭配真抓词·非空壳）。"""
    hits = mod.detect_synesthesia(_SYN_UNIT)
    phrases = {h["phrase"] for h in hits}
    assert "刺耳的光" in phrases
    assert "冰冷的声音" in phrases
    assert "明亮的气味" in phrases
    assert "甜的风" in phrases
    assert "声音很湿" in phrases
    assert "气味很刺眼" in phrases
    assert len(hits) == 6


def test_gate_level_always_advisory_and_code():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_SYN_UNIT * 20))
        assert out["gate_level"] == "advisory"
        assert out["code"] == "SYNESTHESIA_OVERUSE" == mod.ISSUE_CODE
        assert out["scanner"] == "synesthesia_density"
    finally:
        _set_mode(bak)


def test_code_never_in_hard_gate():
    """ISSUE_CODE 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤）。"""
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_CODE not in audit_hub.HARD_GATE_CODES


def test_scan_read_failure_returns_note():
    bak = os.environ.get("SYNESTHESIA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


# ── main() CLI 退出码（subprocess 真跑 argparse） ────────────────────────────
def _run_cli(*args, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "SYNESTHESIA_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    p = _write(_SYN_UNIT * 20)
    r = _run_cli(str(p))
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is not None and rep["verdict"] == "FAIL_MINOR"


def test_main_exit_0_clean_draft():
    p = _write(_CLEAN_UNIT * 25)
    r = _run_cli(str(p))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["warning"] is None and rep["verdict"] == "PASS"
