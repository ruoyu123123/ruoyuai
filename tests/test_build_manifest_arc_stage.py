"""build_manifest._collect_main_character_arc_stage 回归测试 — [#7] 北极星①。

钉死：writer manifest 必须内联注入「当前主角的弧线 current_stage + 简短上下文」。
背景：character_arc_state.json 由 character_arc_update 滚动写、cluster_emergence 消费，
但 writer 此前看不到 —— 本字段补上这个缺口。

守护点：
  · v2 schema（arcs + active_cluster）→ 用本 cluster 命中 active_cluster 定位阶段
  · 旧 schema（characters + current_stage_at_ch "ch:stage"）→ 解析阶段
  · 文件缺失 → mode off（不崩、不伪造）
  · advisory（顾问非法官·北极星⑤）—— gate_level 必须 advisory，绝不 hard_gate
  · 只注入当前主角当前阶段，不全量塞 stages（context 预算）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


def _mk_project(tmp: Path, *, clusters=None, characters=None, arc=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    if arc is not None:
        (db / "character_arc_state.json").write_text(
            json.dumps(arc, ensure_ascii=False), encoding="utf-8")
    return tmp


# ---------- v2 schema（诡异接待处真实结构） ----------

_V2_ARC = {
    "schema_version": "v2.cluster",
    "arcs": {
        "陆建国": {
            "framework": "mckee_arc",
            "current_stage": "stage_1_protect",
            "stages": [
                {"id": "stage_1_protect", "name": "防御阶段",
                 "description": "把机构 SOP 当生存协议（不知为何）",
                 "active_cluster": ["cluster_001", "cluster_002", "cluster_003"]},
                {"id": "stage_2_reveal", "name": "揭示阶段",
                 "description": "知道自己就是档案",
                 "active_cluster": ["cluster_004", "cluster_005"]},
                {"id": "stage_3_collapse", "name": "崩塌阶段",
                 "description": "All Is Lost", "active_cluster": ["cluster_006"]},
            ],
        },
        "林若昭": {
            "framework": "truby_arc",
            "current_stage": "stage_1_supervisor",
            "stages": [
                {"id": "stage_1_supervisor", "name": "监督者",
                 "active_cluster": ["cluster_001", "cluster_002"]},
                {"id": "stage_2_doubt", "name": "动摇",
                 "active_cluster": ["cluster_003", "cluster_004"]},
            ],
        },
    },
}

_CARDS = [
    {"id": "lu", "name": "陆建国", "role": "主角"},
    {"id": "lin", "name": "林若昭", "role": "配角"},
]

_CLUSTERS = [
    {"cluster_id": "cluster_001", "chapter_range": [1, 4]},
    {"cluster_id": "cluster_002", "chapter_range": [5, 8]},
    {"cluster_id": "cluster_004", "chapter_range": [13, 16]},
]


def test_v2_active_cluster_resolves_stage_for_cluster_002():
    """ch6 ∈ cluster_002 ∈ 陆建国 stage_1_protect.active_cluster → 注入 stage_1_protect。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS, arc=_V2_ARC)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        assert r["mode"] == "on"
        assert r["current_cluster"] == "cluster_002"
        mc = r["main_characters"]
        # 只注入主角（陆建国 role==主角），不注入林若昭（配角）
        assert [c["character"] for c in mc] == ["陆建国"]
        st = mc[0]
        assert st["current_stage_id"] == "stage_1_protect"
        assert st["current_stage_name"] == "防御阶段"
        assert st["_resolved_by"] == "active_cluster"
        assert "SOP" in st["stage_description"]


def test_v2_active_cluster_advances_in_later_cluster():
    """ch14 ∈ cluster_004 ∈ stage_2_reveal.active_cluster → 阶段推进到 stage_2_reveal。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS, arc=_V2_ARC)
        s = bm.DatabaseScanner(tmp, 14)
        r = bm._collect_main_character_arc_stage(s, 14)
        assert r["main_characters"][0]["current_stage_id"] == "stage_2_reveal"
        assert r["main_characters"][0]["_resolved_by"] == "active_cluster"


def test_advisory_gate_never_hard():
    """北极星⑤顾问非法官：gate_level 必须 advisory，绝不 hard_gate。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS, arc=_V2_ARC)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)


def test_lightweight_no_full_stages_dump():
    """context 预算：只注入当前阶段，不全量塞 stages 列表。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS, arc=_V2_ARC)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        # 不应泄完整 stages 数组（context 预算）：每个主角条目不带 stages / active_cluster 键
        for c in r["main_characters"]:
            assert "stages" not in c
            assert "active_cluster" not in c
            # 描述被截断到 ≤160 字
            if c["stage_description"]:
                assert len(c["stage_description"]) <= 160


def test_v2_fallback_current_stage_field_when_no_cluster_match():
    """cluster 命中不到任何 active_cluster → 回退 current_stage 字段。"""
    arc = {
        "arcs": {
            "陆建国": {
                "framework": "mckee_arc",
                "current_stage": "stage_2_reveal",
                "stages": [
                    {"id": "stage_2_reveal", "name": "揭示阶段", "description": "知道真相",
                     "active_cluster": ["cluster_009"]},  # 不含当前 cluster
                ],
            }
        }
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS, arc=arc)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        st = r["main_characters"][0]
        assert st["current_stage_id"] == "stage_2_reveal"
        assert st["_resolved_by"] == "current_stage_field"
        assert st["current_stage_name"] == "揭示阶段"


def test_v2_fallback_first_arc_when_protagonist_not_in_arc():
    """主角不在 arc 表内 → fallback 注入弧线表第一个角色（不丢信号）。"""
    cards = [{"id": "x", "name": "无弧线主角", "role": "主角"}]
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=cards, arc=_V2_ARC)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        assert r["mode"] == "on"
        assert r["main_characters"][0]["character"] == "陆建国"  # arcs 第一个


# ---------- 旧 schema（characters + current_stage_at_ch） ----------

def test_legacy_schema_current_stage_at_ch():
    """旧形态：current_stage_at_ch='10:lie_cracking' → 解析出 lie_cracking。"""
    arc = {
        "characters": [
            {"id": "luyan", "name": "陆衍", "framework": "lie_arc",
             "stages_by_chapter": {"1": "lie", "8": "lie_cracking", "14": "want_threatened"},
             "current_stage_at_ch": "10:lie_cracking",
             "stage_description": "谎言开始裂开"},
        ]
    }
    cards = [{"id": "luyan", "name": "陆衍", "role": "主角"}]
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=cards, arc=arc)
        s = bm.DatabaseScanner(tmp, 10)
        r = bm._collect_main_character_arc_stage(s, 10)
        st = r["main_characters"][0]
        assert st["character"] == "陆衍"
        assert st["current_stage_id"] == "lie_cracking"
        assert st["_resolved_by"] == "current_stage_at_ch"


def test_legacy_schema_computed_from_stages_by_chapter():
    """旧形态无 current_stage_at_ch → 从 stages_by_chapter 现算（≤ch 的最近一个）。"""
    arc = {
        "characters": [
            {"id": "luyan", "name": "陆衍",
             "stages_by_chapter": {"1": "lie", "8": "lie_cracking", "14": "want_threatened"}},
        ]
    }
    cards = [{"id": "luyan", "name": "陆衍", "role": "主角"}]
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=cards, arc=arc)
        s = bm.DatabaseScanner(tmp, 9)  # 最近 ≤9 的 key 是 8 → lie_cracking
        r = bm._collect_main_character_arc_stage(s, 9)
        st = r["main_characters"][0]
        assert st["current_stage_id"] == "lie_cracking"
        assert st["_resolved_by"] == "stages_by_chapter_computed"


# ---------- 边界 ----------

def test_missing_file_mode_off():
    """无 character_arc_state.json → mode off（不崩不伪造）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS)  # 不写 arc 文件
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        assert r["mode"] == "off"


def test_empty_arcs_mode_off():
    """character_arc_state.json 存在但无可解析阶段 → mode off。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS,
                          arc={"schema_version": "v2.cluster", "arcs": {}})
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_main_character_arc_stage(s, 6)
        assert r["mode"] == "off"


def test_wired_into_final_manifest():
    """字段必须真正出现在 full build_manifest() dict 里（防 collector 写了却没接线）。
    用 ch1（preflight 不要求上一章 txt）+ 进度.json cluster_blueprint scene_storyboard。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, characters=_CARDS, arc=_V2_ARC)
        # build_manifest() preflight 需要 进度.json + ch1 的 cluster_blueprint scene
        prog = {
            "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 8]}],
            "cluster_blueprint": {
                "cluster_001": {
                    "chapter_range": [1, 4],
                    "scene_storyboard": [
                        {"ch": 1, "characters": ["陆建国"], "key_events": ["开局"],
                         "scene_type": ["悬疑"], "summary": "陆建国值夜班接待第一位访客"}
                    ],
                }
            },
        }
        (tmp / "_数据库" / "进度.json").write_text(
            json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        m = bm.build_manifest(tmp, 1)
        assert m["preflight"]["passed"], m["preflight"]
        assert "main_character_arc_stage" in m
        assert m["main_character_arc_stage"]["mode"] == "on"
        assert m["main_character_arc_stage"]["main_characters"][0]["current_stage_id"] == "stage_1_protect"
        # cache_layout 也应登记
        semi = m["_cache_layout"].get("SEMI_STATIC_90_cacheable_v22", [])
        assert "main_character_arc_stage" in semi
