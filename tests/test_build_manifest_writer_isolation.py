#!/usr/bin/env python3
"""写手信息隔离回归锁（2026-06-28）。

镜像 test_build_manifest_foreshadow_isolation：防 gemini 在 build_manifest 注入层提前泄露
未来/暗线秘密（隐藏身份 true_role / 关系暗议程 / 世界真相 / 时钟满格结果 / fate 前向链 /
未来知识 will_learn / 幕后未来结果 / 秘密护栏 anti_patterns / 收敛锚精确终局）。

钉死（9 项 build_manifest 侧门控）：
  1. 角色隐藏身份：concealed_until 未到 → 剥 true_role·role 用 surface_role 替换；到 → 解锁 + reveal。
  2. 关系隐藏议程：hidden_intent/hidden_note 未到 reveal_cluster → 剥；note/notes 字段名 bug 修复。
  3. 世界真相：hidden faction 剥真 current_focus 留 surface；world entry hidden_truth 未到 reveal 剥。
  4. 时钟 trigger_on_max：visible_to_writer=False 且未满格 → 剥 trigger_on_max（恒真 bug + 读错字段已修）。
  5. fate downstream_unlocks：纯删。
  6. knowledge.will_learn：未来隔离·doesnt_know_yet 本体保留。
  7. 幕后 offscreen result_expected/current_plan/goals 剥·visible action 留。
  8. voice_pack.anti_patterns 秘密措辞剥·纯工艺 anti_pattern 留。
  9. volume_convergence_anchor 早期 cluster 降精（final_image→软方向）·volume_arc 始终给。

默认安全闸：无 hidden_*/reveal_cluster/true_role 等显式标记的旧条目一律原样透传零行为变化。

契约更新（2026-07 · auto_fate_draw 收编为 required step1）：事件池.json 存在时
build_manifest 硬性要求 `.manifest/ch_<NNN>_fate_draw_decision.json`
（_schema=fate_draw_decision_v1 · status ∈ {not_required, drawn, no_candidate} · 非空 reason），
缺失即 RuntimeError——抽签不再是主代理的 advisory 手动动作（旧 draw_command 提示已删）。
本文件的 _make_project 据此写入 not_required decision 模拟正式链路 step1 已执行；
硬失败路径本身由 test_build_manifest_fate_draw_overlay.py 回归锁覆盖。

纯确定性（0 gen-model 调用）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import scaffold_subsystems as scaf  # noqa: E402


# ---- 秘密内容标记串（只在 producer 数据里，绝不该泄漏进写手 manifest）----
TRUE_ROLE_SECRET = "TRUEROLE_周校长其实是吞噬学生的怪谈本体"
GHOST_REVEAL_SECRET = "GHOSTREVEAL_他亲手害死了自己的双胞胎弟弟"
WRITER_HINT_SECRET = "WRITERHINTSECRET 此角色是false_hero·终卷揭密·不可早暴露其真身"
WILL_LEARN_FUTURE = "WILLLEARN_主角第九块才会知道自己是培养皿实验体"
ANTIPATTERN_SECRET = "ANTISECRET_别让他说漏自己其实是卧底"
REL_HIDDEN_INTENT = "RELINTENT_导师暗中要在终局夺取主角的钥匙"
REL_HIDDEN_NOTE = "RELNOTE_两人其实是失散多年的亲兄妹"
FACTION_HIDDEN_FOCUS = "FACTIONHIDDEN_暗中策划在月圆夜屠尽全校"
WORLD_HIDDEN_TRUTH = "WORLDTRUTH_这座学校是一具吞噬学生的活体建筑"
CLOCK_TRIGGER = "CLOCKTRIGGER_满格时校长现身当众吞人"
OFFSCREEN_RESULT = "OFFSCREENRESULT_三块之后他会带兵反叛主角"
THREAD_OUTCOME = "THREADOUTCOME_线程完成后整个年级会消失"
FATE_DOWNSTREAM = "FATEDOWN_解锁第七块的弑神事件"
ANCHOR_FINAL_IMAGE = "FINALIMAGE_主角站在校长尸体上点燃整座学校"

# ---- 明面 surface（必须保留）----
SURFACE_ROLE = "敬业的老校长"
SURFACE_DRIVER = "表面动机是想保护学生"
DOESNT_KNOW = "DOESNTKNOW_主角此刻还不知道自己的身世"
CRAFT_ANTIPATTERN = "口头禅不要每段都用（纯工艺禁忌）"
SURFACE_NOTE = "表面亦师亦友很亲近"
FACTION_SURFACE_FOCUS = "维持校园秩序"
WORLD_SURFACE_DESC = "市郊一所封闭式寄宿中学"
OFFSCREEN_ACTION = "在办公室批阅文件"
VOLUME_ARC = "揭开育新中学背后的真相"


def _make_project(tmp: Path) -> Path:
    proj = tmp / "writer_isolation_book"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    rc = scaf.cmd_emit([str(proj)])
    assert rc == 0, "scaffold emit 应成功"
    db = proj / "_数据库"

    # auto_fate_draw required step1 contract（2026-07）：事件池.json 存在（scaffold 建骨架）
    # → build_manifest 硬要求 decision artifact，缺失即 RuntimeError。
    # 这里写 not_required 模拟 step1 已按正式链路执行（本文件只测写手隔离，不测抽签本身）。
    manifest_dir = db / ".manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "ch_001_fate_draw_decision.json").write_text(json.dumps({
        "_schema": "fate_draw_decision_v1",
        "producer": "auto_fate_draw.py",
        "chapter": 1,
        "status": "not_required",
        "reason": "测试夹具：事件池为骨架空池，无可抽事件",
    }, ensure_ascii=False), encoding="utf-8")

    # 事件簇：cluster_001 active 非空 brief（scene chars 含隐藏角色 周校长）+ 2 个 candidate（total=3 触发降精）
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [
            {"cluster_id": "cluster_001", "status": "in_progress", "vol": 1,
             "chapter_range": [1, 5],
             "scope_summary": "主角林越进入育新中学，初遇规则怪谈的诡异。",
             "scene_storyboard": [
                 {"ch": 1, "title": "灾难开场", "type": "悬疑",
                  "characters": ["林越", "周校长"]},
             ]},
            {"cluster_id": "cluster_002", "status": "candidate", "vol": 1},
            {"cluster_id": "cluster_003", "status": "candidate", "vol": 1},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    # 人物卡：林越（主角·携 will_learn 未来 + doesnt_know_yet + voice_pack anti_patterns）；
    #         周校长（非主角·隐藏身份 true_role/concealed_until=cluster_009 + ghost + _writer_hint + offscreen）
    (db / "人物卡.json").write_text(json.dumps({
        "characters": [
            {"name": "林越", "role": "主角",
             "knowledge": {
                 "will_learn": [{"fact": WILL_LEARN_FUTURE, "how": "终局揭晓",
                                 "learn_at_cluster": "cluster_009"}],
                 "doesnt_know_yet": [DOESNT_KNOW],
             },
             "voice_pack": {"anti_patterns": [
                 CRAFT_ANTIPATTERN,
                 {"text": ANTIPATTERN_SECRET, "reveals_secret": True},
             ]}},
            {"name": "周校长", "role": "校长",
             "surface_role": SURFACE_ROLE,
             "true_role": TRUE_ROLE_SECRET,
             "concealed_until_cluster": "cluster_009",
             "propp_function": "false_hero",
             "ghost": {"surface_driver": SURFACE_DRIVER, "wound": GHOST_REVEAL_SECRET},
             "_writer_hint": WRITER_HINT_SECRET,
             "offscreen": {
                 "actions": [{"action": OFFSCREEN_ACTION, "result": OFFSCREEN_RESULT,
                              "ch_range": [1, 5], "visible_to_protagonist": False}],
                 "current_plan": "暗中布局吞噬全校",
                 "goals": ["在终局现出原形"],
             }},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    # 关系：林越→周校长含尚未揭示的暗议程与明面备注。
    (db / "关系.json").write_text(json.dumps({
        "relationships": [
            {"from": "林越", "to": "周校长", "type": "师徒",
             "affinity": 8, "trust": 5, "fear": 0, "respect": 6,
             "surface_note": SURFACE_NOTE,
             "hidden_intent": REL_HIDDEN_INTENT, "reveal_cluster": "cluster_009",
             "hidden_note": REL_HIDDEN_NOTE, "hidden_note_reveal_cluster": "cluster_009"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    # 世界状态：hidden faction（剥真 current_focus 留 surface）+ public faction + 含 outcome 的 thread
    (db / "世界状态.json").write_text(json.dumps({
        "current_world_time": {"date": "第一日"},
        "factions_state": {
            "校方": {"hidden": True, "power": 50, "stability": 60, "wealth": 30,
                     "current_focus": FACTION_HIDDEN_FOCUS, "surface_focus": FACTION_SURFACE_FOCUS},
            "学生会": {"power": 20, "stability": 50, "wealth": 10,
                       "current_focus": "组织迎新社团活动"},
        },
        "active_npc_threads": [
            {"thread_id": "T1", "npc_id": "周校长", "current_action": "巡视走廊",
             "outcome_if_complete": THREAD_OUTCOME, "_priority": 5},
        ],
        "emergent_opportunities": [], "world_ticks_log": [],
        "consequence_tracker": {}, "narrative_consequences": [],
    }, ensure_ascii=False), encoding="utf-8")

    # 世界观：含 hidden_truth 的条目（keyword 命中本章 goal『育新中学』）
    (db / "世界观.json").write_text(json.dumps({
        "entries": [
            {"id": "W_school", "keywords": ["育新中学"], "priority": 5,
             "description": WORLD_SURFACE_DESC,
             "hidden_truth": WORLD_HIDDEN_TRUTH, "reveal_cluster": "cluster_009"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    # 大势卡：vol 1 终局画面（早期 cluster 应降精）+ volume_arc（始终给）
    (db / "大势卡.json").write_text(json.dumps({
        "major_events": [
            {
                "id": "ME-V1-01",
                "title": "发现地下室",
                "status": "pending",
                "volume": 1,
                "prerequisites": [],
                "expected_window_after": None,
                "is_volume_finale": True,
            }
        ],
        "volumes": [
            {"vol": 1, "final_image": ANCHOR_FINAL_IMAGE, "volume_arc": VOLUME_ARC,
             "core_conflict": "对抗校长揭开真相", "key_milestones": ["发现地下室"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    return proj


# ============================================================================
# 单元测试：每个 _sanitize/_resolve/_soften 单一真理源（确定性·不依赖引擎）
# ============================================================================

def test_unit_character_card_hides_then_reveals():
    card = {
        "name": "周校长", "role": "校长", "surface_role": SURFACE_ROLE,
        "true_role": TRUE_ROLE_SECRET, "concealed_until_cluster": "cluster_009",
        "propp_function": "false_hero",
        "ghost": {"surface_driver": SURFACE_DRIVER, "wound": GHOST_REVEAL_SECRET},
        "_writer_hint": WRITER_HINT_SECRET,
        "knowledge": {"will_learn": [{"fact": WILL_LEARN_FUTURE, "learn_at_cluster": "cluster_009"}],
                      "doesnt_know_yet": [DOESNT_KNOW]},
        "voice_pack": {"anti_patterns": [CRAFT_ANTIPATTERN,
                                         {"text": ANTIPATTERN_SECRET, "reveals_secret": True}]},
    }
    # 未到 concealed cluster（cluster_001）→ 全剥
    out = bm._sanitize_character_card(card, "cluster_001")
    blob = json.dumps(out, ensure_ascii=False)
    assert "true_role" not in out
    assert out["role"] == SURFACE_ROLE, "role 应被 surface_role 等价替换"
    assert out["propp_function"] == "ally", "false_hero 应表面化为 ally"
    assert "surface_subtext" in out
    assert TRUE_ROLE_SECRET not in blob
    assert GHOST_REVEAL_SECRET not in blob and out["ghost"]["surface_driver"] == SURFACE_DRIVER
    assert WRITER_HINT_SECRET not in blob and "_writer_hint" not in out
    assert WILL_LEARN_FUTURE not in blob, "未来 will_learn 应剥"
    assert DOESNT_KNOW in blob, "doesnt_know_yet 本体必须保留（防 FUTURE_KNOWLEDGE_LEAK）"
    assert ANTIPATTERN_SECRET not in blob, "秘密 anti_pattern 应剥"
    assert CRAFT_ANTIPATTERN in blob, "纯工艺 anti_pattern 必须保留"

    # 到 concealed cluster（cluster_009）→ 解锁 true_role + reveal_directive
    out2 = bm._sanitize_character_card(card, "cluster_009")
    assert out2["role"] == TRUE_ROLE_SECRET
    assert "reveal_directive" in out2 and "周校长" in out2["reveal_directive"]


def test_unit_character_card_default_safe_passthrough():
    """无 true_role/concealed/hidden 标记的普通卡 → 原样透传零行为变化。"""
    card = {"name": "路人甲", "role": "配角", "propp_function": "helper",
            "ghost": {"wound": "童年丧父（普通背景·无 surface_driver 拆分）"},
            "voice_pack": {"anti_patterns": ["别太啰嗦"]},
            "knowledge": {"doesnt_know_yet": ["不知道结局"]}}
    out = bm._sanitize_character_card(card, "cluster_001")
    assert out == card, "无隐藏标记的卡应完全原样（含 ghost.wound 当普通背景）"


def test_unit_relationship_hidden_and_default_safe():
    rel = {"from": "A", "to": "B", "type": "师徒",
           "hidden_intent": REL_HIDDEN_INTENT, "reveal_cluster": "cluster_009",
           "hidden_note": REL_HIDDEN_NOTE, "hidden_note_reveal_cluster": "cluster_009"}
    out = bm._sanitize_relationship(rel, "cluster_001")
    assert out["type"] == "师徒", "明面 type 不动"
    assert "hidden_intent" not in out and "hidden_note" not in out
    # 到 reveal → 暴露 + reveal_directive
    out2 = bm._sanitize_relationship(rel, "cluster_009")
    assert out2.get("hidden_intent") == REL_HIDDEN_INTENT and "reveal_directive" in out2
    # 默认安全闸：无 hidden_* → 原样
    plain = {"from": "A", "to": "B", "type": "朋友", "surface_note": "普通", "affinity": 3}
    assert bm._sanitize_relationship(plain, "cluster_001") == plain


def test_unit_faction_and_world_entry():
    hidden_f = {"hidden": True, "power": 50, "current_focus": FACTION_HIDDEN_FOCUS,
                "surface_focus": FACTION_SURFACE_FOCUS}
    fs = bm._sanitize_faction_focus(hidden_f)
    assert fs["current_focus"] == FACTION_SURFACE_FOCUS and fs["power"] == 50
    public_f = {"power": 20, "current_focus": "公开动向"}
    assert bm._sanitize_faction_focus(public_f) == public_f, "公开 faction 原样（涟漪 surface 留）"

    entry = {"id": "W", "description": WORLD_SURFACE_DESC,
             "hidden_truth": WORLD_HIDDEN_TRUTH, "reveal_cluster": "cluster_009"}
    we = bm._resolve_world_entry(entry, "cluster_001")
    assert "hidden_truth" not in we and we["description"] == WORLD_SURFACE_DESC
    we2 = bm._resolve_world_entry(entry, "cluster_009")
    assert we2.get("hidden_truth") == WORLD_HIDDEN_TRUTH and "reveal_directive" in we2
    plain_e = {"id": "P", "description": "公开设定"}
    assert bm._resolve_world_entry(plain_e, "cluster_001") == plain_e


def test_unit_clock_offscreen_fate():
    # clock：visible_to_writer=False 且未满格 → 剥 trigger_on_max
    ck = {"label": "倒计时", "ticks": 1, "max": 4, "urgency": "approaching",
          "visible_to_writer": False, "trigger_on_max": CLOCK_TRIGGER}
    out = bm._sanitize_clock_to_writer(ck)
    assert "trigger_on_max" not in out and out["label"] == "倒计时"
    # 满格 → 含 trigger_on_max
    ck_full = dict(ck, ticks=4)
    assert bm._sanitize_clock_to_writer(ck_full)["trigger_on_max"] == CLOCK_TRIGGER
    # visible_to_writer=True → 含
    assert bm._sanitize_clock_to_writer(dict(ck, visible_to_writer=True))["trigger_on_max"] == CLOCK_TRIGGER

    # offscreen：剥未来结果/幕后意图·留 visible action
    osd = {"character": "周校长", "action": OFFSCREEN_ACTION, "result_expected": OFFSCREEN_RESULT,
           "current_plan": "暗中布局", "goals_short": "吞噬", "outcome_if_complete": THREAD_OUTCOME}
    so = bm._sanitize_offscreen(osd)
    assert so["action"] == OFFSCREEN_ACTION
    for k in ("result_expected", "current_plan", "goals_short", "outcome_if_complete"):
        assert k not in so

    # fate：纯删 downstream_unlocks
    ev = {"id": "ME1", "title": "事件", "downstream_unlocks": [FATE_DOWNSTREAM]}
    sf = bm._strip_fate_downstream(ev)
    assert "downstream_unlocks" not in sf and sf["id"] == "ME1"
    assert bm._strip_fate_downstream({"id": "x"}) == {"id": "x"}, "无该字段原样"


def test_unit_soften_convergence_anchor():
    anchor = {"final_image": ANCHOR_FINAL_IMAGE, "volume_arc": VOLUME_ARC,
              "core_conflict": "对抗", "key_milestones": ["m1"]}
    # 早期（idx 1 / total 3 = 0.33 < 0.5）→ 降精
    out = bm._soften_convergence_anchor(anchor, "cluster_001", 3)
    assert "final_image" not in out and "final_image_softened" in out
    assert out["volume_arc"] == VOLUME_ARC and out["core_conflict"] == "对抗"
    assert out["_softened_for_early_cluster"] is True
    # 后半程（idx 3 / total 3 = 1.0）→ 精确终局保留
    out2 = bm._soften_convergence_anchor(anchor, "cluster_003", 3)
    assert out2["final_image"] == ANCHOR_FINAL_IMAGE
    # 无法判定位置（total<=1）→ 原样
    assert bm._soften_convergence_anchor(anchor, "cluster_001", 1) == anchor


# ============================================================================
# 集成测试：整份 manifest 序列化后不含任何未来秘密（cluster_001）
# ============================================================================

def test_manifest_excludes_all_future_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        assert m.get("preflight", {}).get("passed", True), f"preflight 不应失败: {m.get('preflight')}"
        blob = json.dumps(m, ensure_ascii=False)

        for secret in (TRUE_ROLE_SECRET, GHOST_REVEAL_SECRET, WRITER_HINT_SECRET,
                       WILL_LEARN_FUTURE, ANTIPATTERN_SECRET, REL_HIDDEN_INTENT,
                       REL_HIDDEN_NOTE, FACTION_HIDDEN_FOCUS, WORLD_HIDDEN_TRUTH,
                       OFFSCREEN_RESULT, THREAD_OUTCOME, ANCHOR_FINAL_IMAGE):
            assert secret not in blob, f"未来秘密泄漏进 manifest: {secret}"

        # 明面 surface 必须保留
        for surface in (SURFACE_ROLE, SURFACE_DRIVER, DOESNT_KNOW, CRAFT_ANTIPATTERN,
                        SURFACE_NOTE, FACTION_SURFACE_FOCUS, WORLD_SURFACE_DESC,
                        OFFSCREEN_ACTION, VOLUME_ARC):
            assert surface in blob, f"明面 surface 被误剥: {surface}"


def test_manifest_active_character_cards_isolated():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        cards = {c.get("name"): c for c in m["active_character_cards"]}
        assert "周校长" in cards and "林越" in cards, "出场角色（含主角）卡都应注入"
        zx = cards["周校长"]
        assert "true_role" not in zx and zx["role"] == SURFACE_ROLE
        assert "_writer_hint" not in zx
        ly = cards["林越"]
        assert WILL_LEARN_FUTURE not in json.dumps(ly, ensure_ascii=False)
        assert DOESNT_KNOW in json.dumps(ly, ensure_ascii=False)


def test_manifest_relationships_note_bug_fixed_and_isolated():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        rels = m["active_relationships"]
        assert rels, "出场角色关系应注入"
        r = rels[0]
        assert r["surface_note"] == SURFACE_NOTE
        assert "hidden_intent" not in r and "hidden_note" not in r
        assert r["type"] == "师徒"


def test_manifest_offscreen_and_factions_isolated():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        offs = m["active_offscreen_actions"]
        assert offs and offs[0]["action"] == OFFSCREEN_ACTION
        assert "result_expected" not in offs[0] and "current_plan" not in offs[0]
        snap = m["world_state_snapshot"]
        if snap.get("mode") == "fluid":
            assert snap["factions_state"]["校方"]["current_focus"] == FACTION_SURFACE_FOCUS
            assert snap["factions_state"]["校方"]["power"] == 50  # 数值（涟漪 surface）留


def test_manifest_convergence_anchor_softened():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp))
        m = bm.build_manifest(proj, 1)
        anchor = m["event_cluster_context"]["volume_convergence_anchor"]
        assert anchor is not None
        assert "final_image" not in anchor, "早期 cluster 精确终局应降精"
        assert "final_image_softened" in anchor
        assert anchor.get("volume_arc") == VOLUME_ARC, "volume_arc 始终给"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
