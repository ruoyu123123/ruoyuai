# -*- coding: utf-8 -*-
"""indirect_characterization_ratio_scanner R18 W7 Batch-T·P1 烘云托月+横云断山回归。

确定性·零依赖。覆盖 off/短稿/无主角/全直写/侧写均衡/作者档 z-band/兜底地板/
tone score/cross-cluster pendulum/作者档 allow_monotone 旁路/active/shadow/
读取失败/_mode/CLI/registry hard_gate codes 不污染。
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
import indirect_characterization_ratio_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "indirect_characterization_ratio_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("INDIRECT_CHARACTERIZATION_MODE", None)
    else:
        os.environ["INDIRECT_CHARACTERIZATION_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(characters=None, baseline=None, prior_clusters=None):
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    if baseline is not None:
        (db / "作者风格.json").write_text(
            json.dumps({"quantitative": {
                "indirect_characterization_baseline": baseline}},
                ensure_ascii=False), encoding="utf-8")
    if prior_clusters is not None:
        (db / ".cross_chapter_scan").mkdir(parents=True, exist_ok=True)
        (db / ".cross_chapter_scan" / "cluster_tonal_registry.json").write_text(
            json.dumps({"entries": prior_clusters}, ensure_ascii=False),
            encoding="utf-8")
    return proj


_PROTAG = "张三"
_SIDE_A = "李四"
_SIDE_B = "王五"

# 全直写（direct 主导）：主角名 + 动作动词反复
_DIRECT_HEAVY = (
    "张三说。张三做。张三走。张三看。张三想。张三笑。张三答。张三问。"
) * 80

# 侧写丰富（indirect 主导）：配角观察 + 配角议论主角
_INDIRECT_RICH = (
    "李四凝视张三的背影。王五瞥张三。\"张三真是个怪人。\""
    "李四望张三的手。\"张三这一刀很重。\"王五瞧张三远去。"
) * 30 + "张三说。张三走。" * 5

# tone 测试：纯 hot
_HOT_TEXT = "怒吼。怒吼。怒吼。杀血震裂。怒吼。怒吼。" * 50

# tone 测试：纯 cold
_COLD_TEXT = "静凉默沉淡。冷雪寂幽。霜冰薄。静凉默沉淡。冷雪寂幽。霜冰薄。" * 50


def test_off_returns_skeleton():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_DIRECT_HEAVY))
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("短稿。"))
        assert out["note"] == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_no_protagonist_skip():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        # 无主角声明
        out = mod.scan(_write(_DIRECT_HEAVY), _mk_project())
        assert "无主角声明" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_direct_heavy_triggers_floor():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[
            {"name": _PROTAG, "role": "主角"},
            {"name": _SIDE_A, "role": "配角"},
        ])
        out = mod.scan(_write(_DIRECT_HEAVY), proj)
        m = out["metrics"]
        # 全直写 → indirect_ratio 极低 < 0.10
        assert m["indirect_ratio"] is not None
        assert m["indirect_ratio"] < mod.FLOOR_INDIRECT_RATIO
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_indirect_rich_passes():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(characters=[
            {"name": _PROTAG, "role": "主角"},
            {"name": _SIDE_A, "role": "配角"},
            {"name": _SIDE_B, "role": "配角"},
        ])
        out = mod.scan(_write(_INDIRECT_RICH), proj)
        m = out["metrics"]
        assert m["indirect_signals"] > 0
        # ratio 应该高于地板
        assert m["indirect_ratio"] > mod.FLOOR_INDIRECT_RATIO
    finally:
        _set_mode(bak)


def test_author_baseline_z_band_thin():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        # 作者基线极高 0.8±0.02 → 任何低 ratio 都 z<<-2
        proj = _mk_project(
            characters=[
                {"name": _PROTAG, "role": "主角"},
                {"name": _SIDE_A, "role": "配角"},
            ],
            baseline={"mean": 0.8, "std": 0.02, "allow_monotone": False})
        out = mod.scan(_write(_DIRECT_HEAVY), proj)
        assert out["author_baseline"]["from_author_profile"] is True
        assert out["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)


def test_tone_hot_detected():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": _PROTAG, "role": "主角"}])
        out = mod.scan(_write(_HOT_TEXT), proj)
        assert out["metrics"]["tone_tag"] == "hot"
        assert out["metrics"]["hot_hits"] > out["metrics"]["cold_hits"]
    finally:
        _set_mode(bak)


def test_tone_cold_detected():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": _PROTAG, "role": "主角"}])
        out = mod.scan(_write(_COLD_TEXT), proj)
        assert out["metrics"]["tone_tag"] == "cold"
    finally:
        _set_mode(bak)


def test_write_registry_appends():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[{"name": _PROTAG, "role": "主角"}])
        mod.scan(_write(_HOT_TEXT), proj, cluster_id="cluster_001",
                 write_registry=True)
        reg = json.loads(
            (proj / "_数据库" / ".cross_chapter_scan"
             / "cluster_tonal_registry.json").read_text(encoding="utf-8"))
        ids = [e["cluster_id"] for e in reg["entries"]]
        assert "cluster_001" in ids
    finally:
        _set_mode(bak)


def test_pendulum_flat_detected():
    """末 5 cluster 同 tone streak ≥ 4 → flat advisory"""
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        prior = [
            {"cluster_id": "c1", "tone_tag": "hot", "tone_diff": 5.0},
            {"cluster_id": "c2", "tone_tag": "hot", "tone_diff": 5.0},
            {"cluster_id": "c3", "tone_tag": "hot", "tone_diff": 5.0},
            {"cluster_id": "c4", "tone_tag": "hot", "tone_diff": 5.0},
        ]
        proj = _mk_project(
            characters=[{"name": _PROTAG, "role": "主角"}],
            prior_clusters=prior)
        # 当前 cluster 也 hot → 末 5 全 hot → streak=5
        out = mod.scan(_write(_HOT_TEXT), proj, cluster_id="c5")
        codes = [v["code"] for v in out["violations"]]
        assert mod.ISSUE_PENDULUM in codes
    finally:
        _set_mode(bak)


def test_allow_monotone_bypass_pendulum():
    """作者档 allow_monotone=true → pendulum 静默"""
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        prior = [
            {"cluster_id": f"c{i}", "tone_tag": "hot", "tone_diff": 5.0}
            for i in range(1, 6)
        ]
        proj = _mk_project(
            characters=[{"name": _PROTAG, "role": "主角"}],
            baseline={"mean": 0.05, "std": 1.0, "allow_monotone": True},
            prior_clusters=prior)
        out = mod.scan(_write(_HOT_TEXT), proj, cluster_id="c6")
        codes = [v.get("code") for v in out["violations"]]
        assert mod.ISSUE_PENDULUM not in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(characters=[
            {"name": _PROTAG, "role": "主角"},
            {"name": _SIDE_A, "role": "配角"},
        ])
        out = mod.scan(_write(_DIRECT_HEAVY), proj)
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back():
    bak = os.environ.get("INDIRECT_CHARACTERIZATION_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_codes_not_in_hard_gate():
    rg = _SCRIPTS / "scanner_registry.json"
    reg = json.loads(rg.read_text(encoding="utf-8"))
    hgs = set(reg.get("hard_gate_codes", []))
    assert "INDIRECT_CHARACTERIZATION_THIN" not in hgs
    assert "TONAL_PENDULUM_FLAT" not in hgs


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "INDIRECT_CHARACTERIZATION_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(characters=[
        {"name": _PROTAG, "role": "主角"},
        {"name": _SIDE_A, "role": "配角"},
    ])
    r = _run_cli(_write(_DIRECT_HEAVY), project=proj)
    assert r.returncode == 1, r.stderr


def test_main_exit_0_on_clean():
    proj = _mk_project(characters=[
        {"name": _PROTAG, "role": "主角"},
        {"name": _SIDE_A, "role": "配角"},
        {"name": _SIDE_B, "role": "配角"},
    ])
    r = _run_cli(_write(_INDIRECT_RICH), project=proj)
    assert r.returncode == 0, r.stderr
