#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对白即行动 Dialogue-as-Action（对话表达层 P1·McKee verbal action）注入/消费回归锁。🔴 2026-06-29

覆盖：
  ① _collect_dialogue_objectives：标注 storyboard.dialogue_objectives → 注入 directive + 透传 wants/tactic；
     无字段 → None（默认安全·向后兼容旧 storyboard / 旧书·零行为变化）。
  ② what_unsaid 隔离门控（_sanitize_dialogue_objectives）：objective 标 reveal_cluster 且未到期 → 剥 what_unsaid；
     到/越过揭晓点 → 保留；无 reveal_cluster 标记 → 原样透传（默认安全）。
  ③ build_manifest event_cluster_context 端到端透传 dialogue_objectives。
  ④ gen_writer._sanitize_cluster_brief_foreshadowing：闭合直读 scene_storyboard 的 what_unsaid 剧透口
     （未到期 reveal_cluster → 剥 what_unsaid；无标记 → 原样透传）。
  ⑤ 北极星⑤：对白即行动全 advisory·绝不进 audit_hub.HARD_GATE_CODES。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import gen_writer as gw  # noqa: E402
import audit_hub  # noqa: E402


def _write(db: Path, name: str, obj):
    db.mkdir(parents=True, exist_ok=True)
    (db / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ════════════════════ ① _collect_dialogue_objectives ════════════════════

def test_dialogue_objectives_injects_directive_and_passes_wants_tactic():
    """标注 dialogue_objectives → 注入对白即行动指令 + 透传 character/wants/tactic/obstacle。"""
    cluster = {
        "cluster_id": "cluster_001",
        "scene_storyboard": [
            {"title": "对峙", "dialogue_objectives": [
                {"character": "池迟", "wants": "套出老钟知不知道钥匙下落",
                 "tactic": "试探", "obstacle": "老钟戒心重", "dialogue_act": "试探",
                 "what_unsaid": "他怀疑老钟在撒谎"},
            ]},
            {"title": "纯叙述场", "characters": ["池迟"]},  # 无 dialogue_objectives
        ],
    }
    out = bm._collect_dialogue_objectives(cluster, "cluster_001")
    assert out is not None, "标注 dialogue_objectives 应注入"
    assert "scenes" in out and len(out["scenes"]) == 1  # 只收有 objectives 的场
    sc = out["scenes"][0]
    assert sc["scene_index"] == 0
    obj = sc["objectives"][0]
    assert obj["character"] == "池迟"
    assert obj["wants"].startswith("套出")
    assert obj["tactic"] == "试探"
    assert obj["obstacle"] == "老钟戒心重"
    # 无 reveal_cluster → what_unsaid 默认安全透传
    assert obj.get("what_unsaid") == "他怀疑老钟在撒谎"
    d = out["directive"]
    assert "对白即行动" in d and "what_unsaid" in d and "on-the-nose" in d
    assert "透明原则" in d
    assert out["_doc"].startswith("🔴 2026-06-29")


def test_dialogue_objectives_default_safe_no_field():
    """旧 storyboard（无 dialogue_objectives）→ None（默认安全·零行为变化）。"""
    cluster = {"cluster_id": "cluster_001", "scene_storyboard": [
        {"title": "场景A", "characters": ["主角"]},
        {"title": "场景B", "scene_type": "proactive_scene", "goal": "活下去"},  # 有 Swain 无对白 objective
    ]}
    assert bm._collect_dialogue_objectives(cluster, "cluster_001") is None
    # 空 list / 空 storyboard / 缺 storyboard / None 全安全
    assert bm._collect_dialogue_objectives({"scene_storyboard": [{"dialogue_objectives": []}]}, "c") is None
    assert bm._collect_dialogue_objectives({"cluster_id": "c"}, "c") is None
    assert bm._collect_dialogue_objectives({}, "c") is None
    assert bm._collect_dialogue_objectives(None, "c") is None


# ════════════════════ ② what_unsaid 隔离门控 ════════════════════

def test_what_unsaid_stripped_when_reveal_cluster_not_due():
    """objective 标 reveal_cluster 且未到期（当前 cluster_001 < reveal cluster_005）→ 剥 what_unsaid 防剧透。"""
    objs = [{"character": "池迟", "wants": "拖住对方", "tactic": "回避",
             "what_unsaid": "他其实早知道凶手是父亲（到 cluster_005 才揭晓）",
             "reveal_cluster": "cluster_005"}]
    out = bm._sanitize_dialogue_objectives(objs, "cluster_001")
    assert "what_unsaid" not in out[0], "未到期暗线 what_unsaid 必须剥离"
    # 表达层非密字段保留
    assert out[0]["character"] == "池迟" and out[0]["tactic"] == "回避"
    assert out[0]["wants"] == "拖住对方"


def test_what_unsaid_kept_when_reveal_cluster_due():
    """到/越过揭晓点（当前 cluster_005 >= reveal cluster_003）→ 保留 what_unsaid。"""
    objs = [{"character": "池迟", "wants": "摊牌", "what_unsaid": "他要揭穿父亲",
             "reveal_cluster": "cluster_003"}]
    out = bm._sanitize_dialogue_objectives(objs, "cluster_005")
    assert out[0].get("what_unsaid") == "他要揭穿父亲"


def test_what_unsaid_default_safe_no_reveal_marker():
    """无 reveal_cluster 标记（今天几乎全部）→ 原样透传 what_unsaid（默认安全·零行为变化）。"""
    objs = [{"character": "A", "wants": "试探", "what_unsaid": "普通潜台词"}]
    out = bm._sanitize_dialogue_objectives(objs, "cluster_001")
    assert out[0].get("what_unsaid") == "普通潜台词"
    # 畸形 reveal_cluster（无法解析）→ 保守剥离
    objs2 = [{"character": "A", "what_unsaid": "x", "reveal_cluster": "乱七八糟"}]
    out2 = bm._sanitize_dialogue_objectives(objs2, "cluster_001")
    assert "what_unsaid" not in out2[0]
    # 非 list 入参 → 原样返回（默认安全）
    assert bm._sanitize_dialogue_objectives(None, "c") is None
    assert bm._sanitize_dialogue_objectives("x", "c") == "x"


def test_collect_dialogue_objectives_applies_isolation():
    """_collect_dialogue_objectives 内部经隔离门控：未到期 reveal_cluster 的 what_unsaid 被剥。"""
    cluster = {"scene_storyboard": [
        {"dialogue_objectives": [
            {"character": "A", "wants": "拖延", "what_unsaid": "暗线秘密",
             "reveal_cluster": "cluster_009"}]},
    ]}
    out = bm._collect_dialogue_objectives(cluster, "cluster_002")
    obj = out["scenes"][0]["objectives"][0]
    assert "what_unsaid" not in obj
    assert obj["character"] == "A"  # 非密字段留


# ════════════════════ ③ 端到端 event_cluster_context 透传 ════════════════════

def test_event_cluster_context_carries_dialogue_objectives():
    """build_manifest._collect_event_cluster_context 把 dialogue_objectives 透传进 manifest。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "book"
        db = proj / "_数据库"
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001", "status": "in_progress", "chapter_range": [1, 4],
            "scope_summary": "开局",
            "scene_storyboard": [
                {"title": "对峙", "dialogue_objectives": [
                    {"character": "池迟", "wants": "套话", "tactic": "试探",
                     "what_unsaid": "未到期暗线", "reveal_cluster": "cluster_007"}]},
            ],
        }]})
        s = bm.DatabaseScanner(proj, 1)
        ctx = bm._collect_event_cluster_context(s, 1)
        assert ctx.get("mode") == "on"
        dobj = ctx.get("dialogue_objectives")
        assert dobj is not None and "directive" in dobj
        # 端到端隔离：未到期 reveal_cluster 的 what_unsaid 被剥
        inj = dobj["scenes"][0]["objectives"][0]
        assert "what_unsaid" not in inj
        assert inj["tactic"] == "试探"


def test_event_cluster_context_default_safe_no_dialogue_objectives():
    """旧书 storyboard 无 dialogue_objectives → manifest 该字段为 None（不注入·零行为变化）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "book"
        db = proj / "_数据库"
        _write(db, "事件簇", {"clusters": [{
            "cluster_id": "cluster_001", "status": "in_progress", "chapter_range": [1, 4],
            "scope_summary": "开局",
            "scene_storyboard": [{"title": "开场", "characters": ["主角"]}],
        }]})
        s = bm.DatabaseScanner(proj, 1)
        ctx = bm._collect_event_cluster_context(s, 1)
        assert ctx.get("mode") == "on"
        assert ctx.get("dialogue_objectives") is None


# ════════════════════ ④ gen_writer 直读路径闭合剧透口 ════════════════════

def test_gen_writer_brief_sanitize_strips_what_unsaid_not_due():
    """gen_writer._sanitize_cluster_brief_foreshadowing 闭合 scene_storyboard 直读剧透口：
    未到期 reveal_cluster 的 what_unsaid 被剥（与 build_manifest 同口径）。"""
    brief = {
        "cluster_id": "cluster_001",
        "scope_summary": "开局",
        "scene_storyboard": [
            {"title": "对峙", "goal": "活下去", "dialogue_objectives": [
                {"character": "A", "wants": "拖延", "tactic": "回避",
                 "what_unsaid": "未到期暗线秘密", "reveal_cluster": "cluster_006"}]},
        ],
    }
    safe = gw._sanitize_cluster_brief_foreshadowing(brief, "cluster_001")
    inj = safe["scene_storyboard"][0]["dialogue_objectives"][0]
    assert "what_unsaid" not in inj, "直读路径必须剥未到期 what_unsaid"
    assert inj["tactic"] == "回避"  # 表达层留
    assert safe["scene_storyboard"][0]["goal"] == "活下去"  # 其余 beat 不动
    # 原 brief 不被 mutate（浅拷贝隔离）
    assert "what_unsaid" in brief["scene_storyboard"][0]["dialogue_objectives"][0]


def test_gen_writer_brief_sanitize_default_safe_passthrough():
    """无 reveal_cluster 标记 / 无 dialogue_objectives → 原样透传（默认安全·零行为变化）。"""
    brief = {
        "scope_summary": "x",
        "scene_storyboard": [
            {"title": "s0", "dialogue_objectives": [
                {"character": "A", "wants": "试探", "what_unsaid": "普通潜台词"}]},
            {"title": "s1", "goal": "g"},  # 无 dialogue_objectives
        ],
    }
    safe = gw._sanitize_cluster_brief_foreshadowing(brief, "cluster_001")
    assert safe["scene_storyboard"][0]["dialogue_objectives"][0].get("what_unsaid") == "普通潜台词"
    assert safe["scene_storyboard"][1]["goal"] == "g"


# ════════════════════ ⑤ 北极星⑤ advisory 边界 ════════════════════

def test_dialogue_objectives_never_hard_gate():
    """对白即行动全 advisory：无任何 dialogue/objective/what_unsaid 相关码进 HARD_GATE_CODES。"""
    codes = set(audit_hub.HARD_GATE_CODES)
    for c in codes:
        cu = c.upper()
        assert "DIALOGUE_OBJECTIVE" not in cu
        assert "WHAT_UNSAID" not in cu
        assert "ON_THE_NOSE" not in cu
    # 北极星不变量：HARD_GATE_CODES 数量恒 18（本特性不新增 hard_gate）
    assert len(codes) == 18
