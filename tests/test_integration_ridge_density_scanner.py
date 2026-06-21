# -*- coding: utf-8 -*-
"""integration_ridge_density_scanner R21 W10 Batch-DD · R21-NB-03 · DMN integration ridge"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import integration_ridge_density_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "integration_ridge_density_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("INTEGRATION_RIDGE_MODE", None)
    else:
        os.environ["INTEGRATION_RIDGE_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(names=None, locked_facts=None, foreshadow_payoffs=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if names is not None:
        (proj / "_数据库" / "角色池.json").write_text(
            json.dumps({"emerged": names}, ensure_ascii=False), encoding="utf-8")
    if locked_facts is not None:
        # 兼容 schema: {key: {key_noun: "..."}}
        obj = {f"fact_{i}": {"key_noun": k} for i, k in enumerate(locked_facts)}
        (proj / "_数据库" / "锁定事实账本.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    if foreshadow_payoffs is not None:
        obj = {"items": [{"key_noun": k, "status": "paid"}
                         for k in foreshadow_payoffs]}
        (proj / "_数据库" / "伏笔账本.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"integration_ridge_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def test_off_returns_skeleton():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write("xxx" * 200), _mk_project())
        assert out["mode"] == "off"
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("shadow")
        # 长 CJK 平淡文本·shadow 不报
        text = "他在喝水的早晨慢慢展开。" * 200
        out = mod.scan(_write(text), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_collect_established_names():
    proj = _mk_project(names=["李四", "张三"])
    names = mod._collect_established_names(proj)
    assert "李四" in names and "张三" in names


def test_collect_locked_facts():
    proj = _mk_project(locked_facts=["天命剑", "古经"])
    facts = mod._collect_locked_facts(proj)
    assert "天命剑" in facts and "古经" in facts


def test_collect_foreshadow_payoffs():
    proj = _mk_project(foreshadow_payoffs=["遗书", "暗号"])
    payoffs = mod._collect_foreshadow_payoffs(proj)
    assert "遗书" in payoffs and "暗号" in payoffs


def test_ridge_detected_with_signals():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("active")
        # 制造 ridge：在末段集中 names + 合流标志 + 伏笔 payoffs（4 信号）
        proj = _mk_project(names=["李四", "张三"],
                           locked_facts=["天命剑"],
                           foreshadow_payoffs=["遗书", "暗号"])
        prologue = "平静而漫长的早晨。" * 150
        ridge_window = (
            "李四走来。张三随后。终于明白这一切。原来如此，串到一起。"
            "天命剑回到眼前。遗书的内容浮现。暗号被破解。"
        ) * 3
        tail = "结尾。" * 50
        text = prologue + ridge_window + tail
        out = mod.scan(_write(text), proj)
        # 应有至少 1 ridge
        assert out["ridge_count"] >= 1
    finally:
        _set_mode(bak)


def test_ridge_absent_flagged_active():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("active")
        text = "他在喝水的早晨慢慢展开漫长无变化。" * 200
        out = mod.scan(_write(text), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "INTEGRATION_RIDGE_ABSENT" in codes
    finally:
        _set_mode(bak)


def test_ridge_too_early_flagged():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("active")
        # 唯一 ridge 出现在 < 60% 位置
        proj = _mk_project(names=["李四", "张三"],
                           locked_facts=["天命剑"],
                           foreshadow_payoffs=["遗书", "暗号"])
        early_ridge = (
            "李四走来。张三随后。终于明白这一切。原来如此，串到一起。"
            "天命剑回到眼前。遗书的内容浮现。暗号被破解。"
        ) * 3
        rest = "之后平静许多年的日常。" * 800  # 拉长尾部
        text = early_ridge + rest
        out = mod.scan(_write(text), proj)
        # 当 ridge 数量 == 1 且 position < 60% → TOO_EARLY
        if out["ridge_count"] == 1:
            codes = {v["code"] for v in out.get("violations", [])}
            assert "INTEGRATION_RIDGE_TOO_EARLY" in codes
    finally:
        _set_mode(bak)


def test_count_window_signals():
    names = ["李四", "张三"]
    facts = ["天命剑"]
    payoffs = ["遗书", "暗号"]
    window = "李四和张三都看见了天命剑。遗书与暗号也都出现。终于明白了。"
    sig = mod._count_window_signals(window, names, facts, payoffs)
    assert sig["sig1_names"] and sig["sig2_facts"]
    assert sig["sig3_payoffs"] and sig["sig4_confluence"]
    assert sig["signals_triggered"] == 4


def test_count_window_signals_partial():
    names = ["李四"]
    window = "李四走来。"
    sig = mod._count_window_signals(window, names, [], [])
    # 单名字只 1 个 → sig1 不触发
    assert not sig["sig1_names"]
    assert sig["signals_triggered"] == 0


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("active")
        baseline = {"window_cjk": 100, "signal_threshold": 1,
                    "culmination_ratio": 0.1}
        # 充分长度 > 800 CJK
        out = mod.scan(_write("他在喝水的早晨慢慢展开漫长无变化。" * 100),
                       _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他喝水。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("INTEGRATION_RIDGE_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "INTEGRATION_RIDGE_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    text = "他喝水的早晨。" * 200
    p = _write(text)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "integration_ridge_density"
    assert rep["layer"] == "cross-cluster"
