# -*- coding: utf-8 -*-
"""cluster_rasa_layer_consistency_scanner · R25 W13 Batch-MM · P1"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_rasa_layer_consistency_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "cluster_rasa_layer_consistency_scanner.py"
_ENV = "CLUSTER_RASA_LAYER_MODE"


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


def _mk_project(profile=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return proj


# 多场景 · 主导 raudra · 12 类过场齐备
_GOOD_DRAFT = (
    "怒喝声震耳，怒火冲天，雷霆暴怒。" * 20
    + "他喜悦地想起回忆，怀疑着脸红的人。" * 10
    + "\n\n***\n\n"
    + "怒喝再起，暴怒翻涌，怒火不息。" * 20
    + "忙不迭赶来，急望对方，疲惫不堪。" * 10
    + "\n\n***\n\n"
    + "怒喝至顶，盛怒难抑，戾气滔天。" * 20
    + "颓丧低头，醺然倒下，心灰万念。" * 10
    + "\n\n***\n\n"
    + "怒喝平息，怒火仍在，余怒未消。" * 20
    + "镇定下来，从容收剑。" * 10
)

# 底色摇摆 · 每场景不同 rasa
_DRIFT_DRAFT = (
    "怒喝怒火暴怒震怒。" * 30
    + "\n\n***\n\n"
    + "悲痛哀伤悲泣凄然。" * 30
    + "\n\n***\n\n"
    + "笑声哈哈调笑捧腹。" * 30
    + "\n\n***\n\n"
    + "凛然豪气凌云无畏。" * 30
)

# 过场塌缩 · 只 1-2 类
_FLAT_DRAFT = (
    "怒喝怒火暴怒。" * 80
    + "喜悦欢喜雀跃。" * 40
    + "\n\n***\n\n"
    + "怒喝怒火暴怒。" * 80
    + "喜悦欢喜雀跃。" * 40
)


def test_off_returns_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        out = mod.scan(_write(_GOOD_DRAFT), _mk_project())
        assert out["mode"] == "off"
        assert "scene_count" not in out
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write("短"), _mk_project())
        assert "草稿太短" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_good_draft_passes():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_GOOD_DRAFT), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_DOMINANT_DRIFT not in codes
        assert mod.ISSUE_CODE_TRANSIENT_FLAT not in codes
    finally:
        _set_mode(bak)


def test_dominant_drift_flagged():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_DRIFT_DRAFT), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_DOMINANT_DRIFT in codes
    finally:
        _set_mode(bak)


def test_transient_flat_flagged():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        out = mod.scan(_write(_FLAT_DRAFT), _mk_project())
        codes = {v["code"] for v in out["violations"]}
        assert mod.ISSUE_CODE_TRANSIENT_FLAT in codes
    finally:
        _set_mode(bak)


def test_author_floor_override():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 作者档要求 floor=10 类·一定不达
        proj = _mk_project({"rasa_layer_preferences": {
            "transient_diversity_floor": 12}})
        out = mod.scan(_write(_GOOD_DRAFT), proj)
        assert out["thresholds"]["author_owned"] is True
        codes = {v["code"] for v in out["violations"]}
        # transient 必报
        assert mod.ISSUE_CODE_TRANSIENT_FLAT in codes
    finally:
        _set_mode(bak)


def test_shadow_no_violation_emitted():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_DRIFT_DRAFT), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_classify_scene_sthayi():
    label, counts = mod._classify_scene_sthayi("怒喝怒火暴怒")
    assert label == "raudra"
    assert counts.get("raudra", 0) > 0


def test_detect_vyabhicari():
    hits = mod._detect_vyabhicari("喜悦回忆怀疑")
    assert "harsa" in hits
    assert "smrti" in hits


def test_split_scenes_marker():
    text = "段一\n\n***\n\n段二\n\n***\n\n段三"
    scenes = mod._split_scenes(text)
    assert len(scenes) == 3


def test_load_thresholds_default():
    dom, trans, owned = mod._load_thresholds(None)
    assert dom == mod.DOMINANT_FLOOR_DEFAULT
    assert trans == mod.TRANSIENT_FLOOR_DEFAULT
    assert owned is False


def test_lexicons_placeholder():
    assert mod._STHAYI_LEX["_placeholder"] is True
    assert mod._VYABHICARI_LEX["_placeholder"] is True


def test_codes_not_in_hard_gate():
    sys.path.insert(0, str(_SCRIPTS))
    import audit_hub  # noqa: E402
    for c in (mod.ISSUE_CODE_DOMINANT_DRIFT,
              mod.ISSUE_CODE_TRANSIENT_FLAT,
              mod.ISSUE_CODE_OK):
        assert c not in audit_hub.HARD_GATE_CODES


def test_cli_returns_json():
    p = _write(_GOOD_DRAFT)
    r = subprocess.run(
        [sys.executable, str(_TARGET), str(p)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, _ENV: "active", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "cluster_rasa_layer_consistency_scanner"


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)
