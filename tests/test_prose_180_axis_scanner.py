# -*- coding: utf-8 -*-
"""prose_180_axis_scanner · R24 W12 Batch-LL · P2
确定性·零依赖·零 LLM/零联网。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import prose_180_axis_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "prose_180_axis_scanner.py"
_ENV = "PROSE_180_AXIS_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


# 同一 actor 「张三」相邻 scene 由「面朝东」→「面朝西」无 transition_cue → flip
_FLIP_DRAFT = (
    "张三面朝东，仔细打量院子。" + "正" * 400
    + "\n\n*\n\n"
    + "张三面朝西，看着河岸。" + "对" * 400
)

# 同一 actor 相邻 scene 反向但带 transition_cue → 不报
_NO_FLIP_WITH_CUE_DRAFT = (
    "张三面朝东，仔细打量院子。" + "正" * 400
    + "\n\n*\n\n"
    + "他转过身，张三面朝西，看着河岸。" + "对" * 400
)

# 无空间三元组（中性叙述）
_NO_TRIPLE_DRAFT = "走过去就是了。" * 100


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_FLIP_DRAFT), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "scene_count" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("短。"), _mk_project())
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_flip_detected_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_FLIP_DRAFT), _mk_project(), "cluster_001")
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_FLIP in codes
        assert out["flips_count"] >= 1
    finally:
        _set_mode(bak)


def test_transition_cue_suppresses_flip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_FLIP_WITH_CUE_DRAFT), _mk_project(), "cluster_001")
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_FLIP not in codes
    finally:
        _set_mode(bak)


def test_no_triples_emits_info():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_NO_TRIPLE_DRAFT), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        # active 模式·info 也记录
        assert mod.ISSUE_CODE_TRIPLE_EMPTY in codes
        assert out["triples_total"] == 0
    finally:
        _set_mode(bak)


def test_shadow_does_not_record_violations():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_FLIP_DRAFT), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_axis_ledger_written_in_active():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        proj = _mk_project()
        out = mod.scan(_write(_FLIP_DRAFT), proj, "cluster_001")
        ledger = proj / "_数据库" / ".cross_chapter_scan" / "spatial_axis.json"
        assert ledger.exists()
        data = json.loads(ledger.read_text(encoding="utf-8"))
        assert "cluster_001" in data["clusters"]
        assert data["clusters"]["cluster_001"]["flips"]
    finally:
        _set_mode(bak)


def test_extract_triples_pattern1():
    triples = mod._extract_triples("张三面朝东。")
    assert any(t["actor"] == "张三" and t["direction"] == "东" for t in triples)


def test_extract_triples_pattern2():
    triples = mod._extract_triples("张三在房屋的南边。")
    assert any(t.get("landmark") == "房屋" and t["direction"] == "南" for t in triples)


def test_split_scenes_marker():
    text = "段一。\n\n*\n\n段二。"
    out = mod._split_scenes(text)
    assert len(out) == 2


def test_has_transition_cue_window():
    assert mod._has_transition_cue("前文走着走着 转过身", "下文。")
    assert not mod._has_transition_cue("前文。", "下文。")


def test_placeholder_flag_set():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_FLIP_DRAFT), _mk_project())
        assert out.get("_placeholder") is True
    finally:
        _set_mode(bak)


def test_directions_opposites_complete():
    op = mod._DIRECTIONS["_opposites"]
    for k, v in (("东", "西"), ("南", "北"), ("上", "下"), ("左", "右"), ("前", "后")):
        assert op[k] == v
        assert op[v] == k


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    assert mod.ISSUE_CODE_FLIP not in audit_hub.HARD_GATE_CODES
    assert mod.ISSUE_CODE_TRIPLE_EMPTY not in audit_hub.HARD_GATE_CODES


def test_cli_returns_json():
    p = _write(_FLIP_DRAFT)
    proj = _mk_project()
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p),
         "--project", str(proj), "--cluster", "cluster_001"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "prose_180_axis_scanner"


def test_read_failure_returns_note():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)
