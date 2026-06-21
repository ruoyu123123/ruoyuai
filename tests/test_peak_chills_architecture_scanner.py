# -*- coding: utf-8 -*-
"""peak_chills_architecture_scanner R21 W10 Batch-DD · R21-NB-02 · Aesthetic chills 双相"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import peak_chills_architecture_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "peak_chills_architecture_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("PEAK_CHILLS_ARCH_MODE", None)
    else:
        os.environ["PEAK_CHILLS_ARCH_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"chills_arch_baseline": baseline}, ensure_ascii=False),
            encoding="utf-8")
    return proj


# 完整双相：前向 anticipation + peak + 后向 release
_COMPLETE_CHILLS = (
    "他屏息听着。心跳越发剧烈。倒数着最后一刻。眼看就要到了。下一秒。" * 5
    + "她崩溃地哭。"
    + "她瘫倒在地。颤抖着泪如雨下。化作一滩泥。" * 3
    + "一切归于平静。" * 80
)

# 无前向（突现 peak）
_NO_ANTICIPATION = (
    "他在喝水。" * 100
    + "她崩溃地哭。"
    + "她瘫倒。颤抖着。化作一滩泥。" * 3
    + "归于平静。" * 80
)

# 无后向（突消 peak）
_NO_RELEASE = (
    "他屏息。心跳。倒数。眼看就要。下一秒。" * 8
    + "她崩溃地哭。"
    + "又过了好久好久好久。" * 80
)

# 无 peak
_NO_PEAK = "他在喝水。" * 200


def test_off_returns_skeleton():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_COMPLETE_CHILLS), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_NO_ANTICIPATION), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_complete_chills_passes():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_COMPLETE_CHILLS), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert out["peak_count"] >= 1
        # 完整双相 → 不应有 CHILLS_ARCH_INCOMPLETE
        # （考虑到前向窗口的 200-500 CJK 范围）
        # 验证至少存在 complete 的 peak
        assert any(p["complete"] for p in out["peaks"])
    finally:
        _set_mode(bak)


def test_no_anticipation_flagged():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_ANTICIPATION), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "CHILLS_ARCH_INCOMPLETE" in codes
        # peak 前向 anticipation 缺
        assert any(not p["anticipation_ok"] for p in out["peaks"])
    finally:
        _set_mode(bak)


def test_no_release_flagged():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_RELEASE), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "CHILLS_ARCH_INCOMPLETE" in codes
        # peak 后向 release 缺
        assert any(not p["release_ok"] for p in out["peaks"])
    finally:
        _set_mode(bak)


def test_no_peak_no_violation():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_PEAK), _mk_project())
        assert out["peak_count"] == 0
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_author_baseline_overrides_fallback():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        # baseline 设极宽 → 即使 0 hits 也通过
        baseline = {"anticipation_min_hits": 0, "release_min_hits": 0, "top_k": 3}
        out = mod.scan(_write(_NO_ANTICIPATION), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        # min_hits=0 → 任何 peak 都完整
        for p in out["peaks"]:
            assert p["complete"]
    finally:
        _set_mode(bak)


def test_find_peaks_dedup_close():
    # 同 100 CJK 内的连续 peak 词只保留首个
    text = "她崩溃地哭。她崩溃地哭。" + "x" * 200
    peaks = mod._find_peaks(text)
    # 至多 1 个（同位置聚簇过滤）
    assert len(peaks) <= 1


def test_find_peaks_top_k():
    # 三个 peak 在不同位置（间隔 > 100 CJK）
    text = ("她崩溃地哭。" + "正常文字。" * 30
            + "他狂喜地笑。" + "其他内容。" * 30
            + "她哀嚎着。")
    peaks = mod._find_peaks(text, top_k=2)
    assert len(peaks) <= 2


def test_count_in_window_basic():
    text = "abc屏息def心跳ghi"
    total, matched = mod._count_in_window(text, ["屏息", "心跳", "倒数"], 0, len(text))
    assert total == 2
    assert "屏息" in matched and "心跳" in matched


def test_short_draft_skipped():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("她崩溃地哭。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("PEAK_CHILLS_ARCH_MODE")
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
        env={**os.environ, "PEAK_CHILLS_ARCH_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_NO_ANTICIPATION)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "peak_chills_architecture"
