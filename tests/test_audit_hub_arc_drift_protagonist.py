# -*- coding: utf-8 -*-
"""🔴 _check_character_arc_drift 主角反查收敛 protagonist_lookup 回归锁（H3·2026-07-15）。

canonical 人物卡是 {"characters": [ {name, role, ...} ]} list；主角识别必须走
protagonist_lookup 唯一真理源（role 以「主角」开头 canonical · 多源兜底 · 解析不出不猜），
禁止 `role == "protagonist"` 精确匹配或把人物卡当 {name: info} dict 读。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import audit_hub as ah  # noqa: E402

_WORK = "测试书"


def _mk_project(tmp: Path, characters, intensity=0.2, arc_name="沈瑶"):
    """建最小沙盒：作者风格.json(meta.work) + 人物卡 + 风格库 emotion_arc + 章 changes。"""
    proj = tmp / "novels" / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    (db / "作者风格.json").write_text(
        json.dumps({"meta": {"work": _WORK}}, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    arc_dir = tmp / "novels" / "workspace" / "styles" / _WORK / "character_arcs"
    arc_dir.mkdir(parents=True)
    (arc_dir / f"{arc_name}_emotion_arc.json").write_text(json.dumps({
        "chapters_with_data": [1],
        "emotion_actor_curve_smoothed": [0.9],
        "emotion_experiencer_curve_smoothed": [0.9],
    }, ensure_ascii=False), encoding="utf-8")
    ch_dir = proj / "章节" / "第001章"
    ch_dir.mkdir(parents=True)
    (ch_dir / "第001章_changes.json").write_text(json.dumps({
        "self_eval": {"emotion_intensity": intensity}
    }, ensure_ascii=False), encoding="utf-8")
    return proj


def test_arc_drift_fires_with_canonical_card_list():
    """canonical list 人物卡 + role「主角·…」→ 正确解析主角并 emit advisory issue。
    「主角的师父」这类关系描述不得被误判为主角。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [
            {"name": "配角甲", "role": "主角的师父"},
            {"name": "沈瑶", "role": "主角·炎帝幼女·不甘认命"},
        ])
        issues = ah._check_character_arc_drift(proj, 1)
        assert len(issues) == 1, "canonical 人物卡下 arc drift 必须点火"
        issue = issues[0]
        assert issue["gate_level"] == "advisory"
        assert "沈瑶" in issue["desc"]
        assert issue["code"] == "CHARACTER_ARC_DRIFT_沈瑶"


def test_arc_drift_english_alias_role_still_resolves():
    """role 写英文别名 protagonist（整体等值）→ protagonist_lookup 兜底仍能解析。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"name": "沈瑶", "role": "protagonist"}])
        issues = ah._check_character_arc_drift(proj, 1)
        assert len(issues) == 1
        assert "沈瑶" in issues[0]["desc"]


def test_arc_drift_no_protagonist_returns_empty_no_guess():
    """人物卡无主角位且无其他兜底源 → 解析 None → 不 emit（绝不拿第一张卡瞎猜）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [
            {"name": "配角甲", "role": "反派"},
            {"name": "配角乙", "role": "路人"},
        ])
        assert ah._check_character_arc_drift(proj, 1) == []


def test_arc_drift_within_band_no_issue():
    """情感强度贴合 arc 期望（drift <= 0.3）→ 不 emit。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"name": "沈瑶", "role": "主角"}], intensity=0.85)
        assert ah._check_character_arc_drift(proj, 1) == []


def test_source_converged_to_protagonist_lookup():
    """🔴 源码锁：audit_hub 主角识别只走 protagonist_lookup，精确匹配旧形态不得回潮。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "protagonist_lookup.resolve_protagonist" in src
    assert '.get("role") == "protagonist"' not in src, \
        "禁止 role 精确匹配（人物卡 role 是自由文本，走 protagonist_lookup）"
    assert "next(iter(cd.keys())" not in src, \
        "禁止把人物卡当 {name:info} dict 读 + 拿第一个 key 瞎猜主角"
