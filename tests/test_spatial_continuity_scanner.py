# -*- coding: utf-8 -*-
"""spatial_continuity_scanner 测试 · ConStory location 盲区补口（2026-07-07）

对应盲区：tests/test_constory_consistency_gold.py::test_location_teleport_blindspot
——角色在地窖深处，下一段无移动动词/场景切换标志出现在北境城墙（无标志词位置瞬移）。
focalizer/locked_fact/pov 三族均 0 检出，本 scanner 用角色↔地点绑定追踪补上。

北极星⑤纪律：SPATIAL_CONTINUITY_TELEPORT 永远 advisory（空间跳切可以是叙事省略），
本文件含制度锁断言该 code 不在 HARD_GATE_CODES。
注意：audit_hub 接线/registry/flywheel 集成由主代理统一做——本文件**不**断言已接线。

跑：py -m pytest tests/test_spatial_continuity_scanner.py -q
"""
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import audit_hub  # noqa: E402
import spatial_continuity_scanner as sc  # noqa: E402


# ───────────────────────── 脚手架 ─────────────────────────

@contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mk_project(characters=None, world=None) -> Path:
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters or []}, ensure_ascii=False),
        encoding="utf-8")
    if world is not None:
        (db / "世界观.json").write_text(
            json.dumps(world, ensure_ascii=False), encoding="utf-8")
    return tmp


def _write_draft(project: Path, text: str) -> Path:
    p = project / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


_CHARS = [{"name": "顾长风", "role": "主角"},
          {"name": "沈青梧", "role": "配角"}]

# ConStory 盲区同款第一对（地窖 → 北境城墙 · 零过渡）+ 第二对（天牢 → 皇城）
# —— 噪声地板要求 ≥2 对才报，正例刻意铺两对。
_TELEPORT_2PAIRS = (
    "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石，头顶只有一线天光。\n\n"
    "下一句话还没说完，顾长风已经站在北境城墙的垛口上，朔风灌满衣领。\n\n"
    "沈青梧披着单衣守在天牢最里侧的栅栏前，指尖掐着一枚冷透的铜钱。\n\n"
    "沈青梧的下一口气已经呼在皇城白玉阶的寒气里，宫灯次第亮着。\n"
)


# ═══════════ 0. 制度锁：advisory 永不 hard_gate（北极星⑤）═══════════

def test_code_is_advisory_never_hard_gate():
    """SPATIAL_CONTINUITY_TELEPORT 绝不得进 HARD_GATE_CODES（空间跳切可以是叙事省略）。"""
    assert sc.ISSUE_CODE == "SPATIAL_CONTINUITY_TELEPORT"
    assert sc.ISSUE_CODE not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for(sc.ISSUE_CODE, "error") == "advisory"


# ═══════════ 1. 正例：ConStory 盲区同款瞬移（2 对）→ active 检出 ═══════════

def test_teleport_two_pairs_detected_active():
    """地窖→北境城墙 + 天牢→皇城，同场景零过渡零移动动词 → 2 条 violations。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, _TELEPORT_2PAIRS)
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["mode"] == "active", r
    assert r["location_lexicon_source"] == "heuristic", r
    assert r["teleport_candidate_count"] == 2, r
    assert r["verdict"] == "FAIL_MINOR", r
    assert r["warning"], r
    assert len(r["violations"]) == 2, r
    v0, v1 = r["violations"]
    assert v0["code"] == "SPATIAL_CONTINUITY_TELEPORT", v0
    assert v0["gate_level"] == "advisory", v0  # 北极星⑤：顾问非法官
    assert v0["character"] == "顾长风", v0
    assert v0["from_location"] == "地窖", v0
    assert v0["to_location"] == "北境城墙", v0
    assert v1["character"] == "沈青梧", v1
    assert v1["from_location"] == "天牢", v1
    assert v1["to_location"] == "皇城", v1


# ═══════════ 2. 负例：带移动动词 → 合法位移 0 检出 ═══════════

def test_movement_verb_between_no_report():
    """两地之间有移动动词（赶到）→ 合法位移，候选归零。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石。\n\n"
        "顾长风提着灯一路小跑，赶到北境城墙的垛口下。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["teleport_candidate_count"] == 0, r
    assert r["verdict"] == "PASS", r
    assert r["violations"] == [], r


# ═══════════ 3. 豁免①：子空间/同词根（字符串包含）═══════════

def test_subspace_containment_waived():
    """北境城墙 → 城墙（同词根·B⊂A）→ 豁免不算瞬移。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风按刀立在北境城墙的垛口后，雪一寸寸埋着他的靴尖。\n\n"
        "顾长风伏低身子，城墙的阴影吞了他半边肩甲。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["teleport_candidate_count"] == 0, r
    assert r["waived_count"] >= 1, r  # 证明确实走到了豁免通路，不是没绑定
    assert r["violations"] == [], r


# ═══════════ 4. 豁免②：对话/回忆中提及地点 ≠ 身处 ═══════════

def test_dialogue_and_recall_mention_waived():
    """引号内提到北境城墙 + 回忆段提到皇城 → 均不参与绑定，0 候选。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石。\n\n"
        "顾长风压低嗓音：“北境城墙的守军怕是撑不住了。”\n\n"
        "顾长风想起皇城那年的灯会，指节一紧。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["teleport_candidate_count"] == 0, r
    assert r["binding_count"] == 1, r  # 只有地窖真绑定；对话/回忆提及全被剥掉
    assert r["violations"] == [], r


# ═══════════ 5. 豁免③：传送/闪现类超能力词命中（玄幻常态）═══════════

def test_teleport_ability_word_waived():
    """段内出现「传送阵」→ 位移是超能力常态，豁免。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石。\n\n"
        "白光一闪，顾长风的身影已在北境城墙的垛口，脚边传送阵的纹路尚未黯淡。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["teleport_candidate_count"] == 0, r
    assert r["waived_count"] >= 1, r
    assert r["violations"] == [], r


# ═══════════ 6. 噪声地板：候选 <2 对不报（单例噪声）═══════════

def test_single_candidate_noise_floor_suppressed():
    """ConStory 同款单对瞬移 → 候选=1 < 2，active 也不产 violations。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石，头顶只有一线天光。\n\n"
        "下一句话还没说完，顾长风已经站在北境城墙的垛口上，朔风灌满衣领。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["teleport_candidate_count"] == 1, r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r
    assert "suppressed" in (r.get("note") or ""), r


# ═══════════ 7. 三态开关：shadow 不产 violations / off 直接返回 ═══════════

def test_shadow_mode_no_violations(capsys):
    """shadow（默认档）：候选照算，但 violations 不产、verdict PASS、走 stderr。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, _TELEPORT_2PAIRS)
    with _env(SPATIAL_CONTINUITY_MODE="shadow"):
        r = sc.scan(draft, proj)
    assert r["mode"] == "shadow", r
    assert r["teleport_candidate_count"] == 2, r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r
    assert r["warning"] is None, r
    assert "[SHADOW] spatial_continuity" in capsys.readouterr().err


def test_off_mode_returns_immediately():
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, _TELEPORT_2PAIRS)
    with _env(SPATIAL_CONTINUITY_MODE="off"):
        r = sc.scan(draft, proj)
    assert r["mode"] == "off", r
    assert r["violations"] == [], r
    assert "teleport_candidate_count" not in r, r


# ═══════════ 8. 地点词典：项目 世界观.json locations 优先于启发式 ═══════════

def test_project_lexicon_preferred():
    """项目地点（落霞坪/问剑坪·后缀启发式认不出）也能追踪瞬移。"""
    proj = _mk_project(characters=_CHARS,
                       world={"locations": ["落霞坪", "问剑坪"]})
    draft = _write_draft(proj, (
        "顾长风按剑坐于落霞坪的青石上，剑穗垂着不动。\n\n"
        "顾长风的衣角沾着问剑坪的霜，指腹摩挲着剑柄。\n\n"
        "顾长风俯身拾着落霞坪畔的断剑，眉心拧成一团。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["location_lexicon_source"] == "project", r
    assert r["location_term_count"] == 2, r
    assert r["teleport_candidate_count"] == 2, r
    assert r["verdict"] == "FAIL_MINOR", r
    assert {v["to_location"] for v in r["violations"]} == {"问剑坪", "落霞坪"}, r


# ═══════════ 9. 场景边界：跨场景块跳切 = 合法叙事省略，绝不报 ═══════════

def test_scene_break_resets_no_report():
    """三个场景块（\\n\\n\\n 分隔）各在一地 → 跨场景跳切是合法蒙太奇，0 候选。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石。\n\n\n"
        "顾长风按刀立在北境城墙的垛口后，雪埋着靴尖。\n\n\n"
        "顾长风负手立于皇城的白玉阶前，宫灯次第亮着。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["scene_count"] == 3, r
    assert r["teleport_candidate_count"] == 0, r
    assert r["violations"] == [], r


def test_transition_marker_resets_no_report():
    """同场景内时间跳跃标志（半个时辰后）→ 绑定重置，不算瞬移。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石。\n\n"
        "半个时辰后，顾长风已经立在北境城墙的垛口边。\n"
    ))
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["teleport_candidate_count"] == 0, r
    assert r["violations"] == [], r


# ═══════════ 10. 无数据安全闸 ═══════════

def test_no_character_cards_skipped():
    proj = _mk_project(characters=[])
    draft = _write_draft(proj, _TELEPORT_2PAIRS)
    with _env(SPATIAL_CONTINUITY_MODE="active"):
        r = sc.scan(draft, proj)
    assert r["violations"] == [], r
    assert "no character cards" in (r.get("note") or ""), r


# ═══════════ 11. CLI 契约：draft_path --project <root> [--cluster] ═══════════

def test_cli_contract_active_exit1():
    """CLI 全链：active 下 2 违规 → JSON 形态完整 + exit 1（照抄 cia scanner 约定）。"""
    proj = _mk_project(characters=_CHARS)
    draft = _write_draft(proj, _TELEPORT_2PAIRS)
    env = dict(os.environ,
               SPATIAL_CONTINUITY_MODE="active", PYTHONIOENCODING="utf-8")
    cp = subprocess.run(
        [sys.executable, str(_ROOT / "core" / "scripts" / "spatial_continuity_scanner.py"),
         str(draft), "--project", str(proj), "--cluster"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert cp.returncode == 1, (cp.returncode, cp.stdout, cp.stderr)
    report = json.loads(cp.stdout)
    assert report["code"] == "SPATIAL_CONTINUITY_TELEPORT", report
    assert report["cluster_mode"] is True, report
    assert report["gate_level"] == "advisory", report
    assert len(report["violations"]) == 2, report
