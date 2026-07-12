#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_identity_anchor_scanner tests."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import character_identity_anchor_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "character_identity_anchor_scanner.py"


def _set_mode(mode):
    if mode is None:
        os.environ.pop("CHARACTER_IDENTITY_ANCHOR_MODE", None)
    else:
        os.environ["CHARACTER_IDENTITY_ANCHOR_MODE"] = mode


def _write_draft(text: str) -> Path:
    root = Path(tempfile.mkdtemp())
    path = root / "draft.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _mk_project(characters) -> Path:
    root = Path(tempfile.mkdtemp())
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def test_active_detects_hair_color_drift_from_identity_anchors():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟推开门，雨水顺着金发往下淌。他没有回头。")

        out = mod.scan(draft, project)

        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "CHARACTER_IDENTITY_ANCHOR_DRIFT"
        assert out["violations"][0]["character"] == "池迟"
        assert out["violations"][0]["anchor_type"] == "hair_color"
        assert out["violations"][0]["expected"] == "黑发"
        assert out["violations"][0]["observed"] == "金发"
    finally:
        _set_mode(bak)


def test_shadow_records_samples_but_does_not_fail():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("shadow")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"eye_color": "黑眼"}},
        ])
        draft = _write_draft("池迟抬起蓝眼，看向被雨冲开的巷口。")

        out = mod.scan(draft, project)

        assert out["verdict"] == "PASS"
        assert out["violations"] == []
        assert out["drift_count"] == 1
        assert out["drift_samples"][0]["observed"] == "蓝眼"
    finally:
        _set_mode(bak)


def test_explicit_forbidden_terms_detect_mark_drift():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {
                "name": "老钟",
                "identity_anchors": [
                    {"type": "mark", "expected": "左脸刀疤", "forbidden": ["左脸黑痣"]},
                ],
            }
        ])
        draft = _write_draft("老钟摸了摸左脸黑痣，像是在确认旧伤还在。")

        out = mod.scan(draft, project)

        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["anchor_type"] == "mark"
        assert out["violations"][0]["observed"] == "左脸黑痣"
    finally:
        _set_mode(bak)


def test_appearance_and_locked_facts_can_seed_anchors():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {
                "name": "林砚",
                "appearance": "黑发，灰眼，左眉有疤",
                "locked_facts": ["林砚一直是黑发"],
            }
        ])
        draft = _write_draft("林砚把斗篷拉低，那双蓝眼在阴影里一闪。")

        out = mod.scan(draft, project)

        assert out["anchor_count"] >= 2
        assert out["verdict"] == "FAIL_MINOR"
        assert any(v["anchor_type"] == "eye_color" and v["observed"] == "蓝眼"
                   for v in out["violations"])
    finally:
        _set_mode(bak)


def test_no_anchors_default_safe_skip():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([{"name": "池迟"}])
        out = mod.scan(_write_draft("池迟走进雨里。"), project)
        assert out["verdict"] == "PASS"
        assert out["anchor_count"] == 0
        assert "no identity anchors" in out["note"]
    finally:
        _set_mode(bak)


def test_negated_conflict_does_not_report():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟不是金发，雨水贴着黑发落进衣领。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "PASS"
        assert out["drift_count"] == 0
    finally:
        _set_mode(bak)


def test_audit_hub_integrates_scanner():
    """接线回归锁：audit_hub cluster-mode tasks 真调本 scanner
    （参考 test_agenda_drift_scanner 同款源码断言模式）。"""
    src = (_SCRIPTS / "audit_hub.py").read_text(encoding="utf-8")
    assert "character_identity_anchor_scanner" in src
    assert "CHARACTER_IDENTITY_ANCHOR_DRIFT" in src


def test_code_never_in_hard_gate_codes():
    """北极星⑤：身份锚点漂移是 advisory·绝不进 HARD_GATE_CODES。"""
    import audit_hub
    assert "CHARACTER_IDENTITY_ANCHOR_DRIFT" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("CHARACTER_IDENTITY_ANCHOR_DRIFT", "error") == "advisory"


# ═══════════════ A7 辨识锚点分层 + 角色负面事实清单 ═══════════════


def test_skeleton_defines_recognition_anchors_and_negative_facts():
    """人物卡 skeleton 的 _recognition_schema 是 A7 两字段的 schema 单一真理源。"""
    skel = json.loads(
        (_ROOT / "core" / "claude-home" / "templates" / "subsystem_skeletons.json")
        .read_text(encoding="utf-8"))
    card = skel["skeletons"]["人物卡"]
    rec = card["_recognition_schema"]
    assert "recognition_anchors" in rec
    assert "negative_facts" in rec
    assert isinstance(rec["recognition_anchors"], list)
    assert "anchor" in rec["recognition_anchors"][0]
    assert "position_or_scene" in rec["recognition_anchors"][0]
    # advisory 软建议·fluid 不硬锁（北极星⑤）
    assert "advisory" in rec["_doc"]
    assert "不硬锁" in rec["_doc"] or "绝不硬锁" in rec["_doc"]
    assert "_recognition_schema" in card["_doc"]


def test_negative_fact_violation_detected_active():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "negative_facts": ["不会武功"]},
        ])
        draft = _write_draft("众人惊呼，池迟竟会武功，一脚踹翻了三个壮汉。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["negative_fact_violation_count"] == 1
        v = out["violations"][0]
        assert v["code"] == "CHARACTER_IDENTITY_ANCHOR_DRIFT"
        assert v["kind"] == "negative_fact_violation"
        assert v["gate_level"] == "advisory"
        assert v["negative_fact"] == "不会武功"
        assert v["observed"] == "会武功"
    finally:
        _set_mode(bak)


def test_negative_fact_negated_mention_not_reported():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "negative_facts": ["不会武功"]},
        ])
        draft = _write_draft("池迟不会武功，只能抱着头往桌子底下钻。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "PASS"
        assert out["negative_fact_violation_count"] == 0
    finally:
        _set_mode(bak)


def test_negative_fact_quoted_dialogue_exempt():
    """引号内对话提及豁免：说起这能力 ≠ 角色展现这能力（交给 voice-checker 语境判）。"""
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "negative_facts": ["不会武功"]},
        ])
        draft = _write_draft("「我会武功。」小乞丐冲池迟喊，比划了两下。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "PASS"
        assert out["negative_fact_violation_count"] == 0
    finally:
        _set_mode(bak)


def test_negative_fact_hypothetical_prefix_exempt():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "negative_facts": ["不会武功"]},
        ])
        draft = _write_draft("池迟要是会武功就好了，可惜他连鸡都追不上。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "PASS"
        assert out["negative_fact_violation_count"] == 0
    finally:
        _set_mode(bak)


def test_negative_fact_disability_run_detected():
    """「左腿旧伤不能跑」类反向锚：正文出现「能跑」→ 违背。"""
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "negative_facts": ["左腿旧伤不能跑"]},
        ])
        draft = _write_draft("池迟撒腿狂奔，巷子里数他最能跑。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["kind"] == "negative_fact_violation"
        assert out["violations"][0]["observed"] == "能跑"
    finally:
        _set_mode(bak)


def test_old_project_without_new_fields_zero_behavior_change():
    """老项目人物卡无 recognition_anchors/negative_facts → 行为与从前完全一致。"""
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟竟会武功，黑发被汗水浸透。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "PASS"
        assert out["negative_fact_count"] == 0
        assert out["negative_fact_violation_count"] == 0
        assert not any(v.get("kind") == "negative_fact_violation"
                       for v in out["violations"])
    finally:
        _set_mode(bak)


def test_recognition_anchor_seeds_drift_detection():
    """recognition_anchors 的 anchor 短语可解析为 hair/eye/mark 时进漂移检测。"""
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟",
             "recognition_anchors": [
                 {"anchor": "白发", "position_or_scene": "开场·满头"},
                 {"anchor": "摩挲扳指", "position_or_scene": "紧张时"},
             ]},
        ])
        draft = _write_draft("池迟拨了拨额前的黑发，指节没有停。")
        out = mod.scan(draft, project)
        assert out["verdict"] == "FAIL_MINOR"
        v = out["violations"][0]
        assert v["kind"] == "stable_identity_anchor_drift"
        assert v["source"] == "recognition_anchors"
        assert v["expected"] == "白发"
        assert v["observed"] == "黑发"
    finally:
        _set_mode(bak)


def test_apply_archive_passes_new_fields_only_on_new_card():
    """apply_archive 新建卡透传两字段；老卡绝不改（终态契约·冲突只走 warnings）。"""
    import apply_archive
    db = Path(tempfile.mkdtemp()) / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    new_char = {
        "id": "C_MAN", "name": "小蛮", "tier": "core",
        "first_cluster": "cluster_001", "new": True,
        "recognition_anchors": [{"anchor": "左脸刀疤", "position_or_scene": "左脸颧骨"}],
        "negative_facts": ["不会武功"],
        "state_changes": [],
    }
    apply_archive.apply_characters(db, [new_char], {}, dry=False)
    pc = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
    card = pc["characters"][0]
    assert card["recognition_anchors"] == [
        {"anchor": "左脸刀疤", "position_or_scene": "左脸颧骨"}]
    assert card["negative_facts"] == ["不会武功"]
    # 老卡：同 id 再来一份不同的 negative_facts → 不覆盖不追加
    again = dict(new_char)
    again["negative_facts"] = ["其实会武功"]
    again["recognition_anchors"] = [{"anchor": "右脸黑痣", "position_or_scene": "右脸"}]
    apply_archive.apply_characters(db, [again], {}, dry=False)
    pc2 = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
    assert len(pc2["characters"]) == 1
    assert pc2["characters"][0]["negative_facts"] == ["不会武功"]
    assert pc2["characters"][0]["recognition_anchors"] == [
        {"anchor": "左脸刀疤", "position_or_scene": "左脸颧骨"}]


def test_agent_contracts_mention_a7_fields():
    """合约 grep 锁：archivist/voice-checker/distill-character 文档与代码现实一致。"""
    archivist = (_ROOT / ".claude" / "agents" / "novel-archivist.md").read_text(encoding="utf-8")
    assert "recognition_anchors" in archivist
    assert "negative_facts" in archivist
    assert "negative_fact_conflict" in archivist
    assert "warnings" in archivist
    assert "只报告" in archivist  # 终态契约：不改卡
    voice = (_ROOT / ".claude" / "agents" / "novel-voice-checker.md").read_text(encoding="utf-8")
    assert "negative_facts" in voice
    assert "pov_violation" in voice


def test_cli_exits_one_when_active_warning():
    bak = os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE")
    try:
        _set_mode("active")
        project = _mk_project([
            {"name": "池迟", "identity_anchors": {"hair_color": "黑发"}},
        ])
        draft = _write_draft("池迟甩开金发上的雨。")
        proc = subprocess.run(
            [sys.executable, str(_TARGET), str(draft), "--project", str(project)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 1
        data = json.loads(proc.stdout)
        assert data["verdict"] == "FAIL_MINOR"
    finally:
        _set_mode(bak)
