"""🔴 2026-06-29 场景级Appraisal Beat注入 + scene_goal动机 回归测试（心理 P0）。

钉死：
  · build_manifest._collect_appraisal_directive 注入「为何感受(appraisal)+如何外化(behavior)+
    derived_emotion 方向」+ 上块情绪余烬·**绝不由系统造『他很愤怒/心中一凛』式情绪词标签**。
  · 默认安全闸：无 叙事节拍器.appraisal_beats → None（不注入·向后兼容旧书·零行为变化）。
  · 全 advisory·gate_level=advisory·绝不出现 hard_gate 字样。
  · build_manifest._collect_scene_causal_skeleton 透传 scene_goal（Stanislavski scene-objective）。
  · 字段真正接线进 full build_manifest()（防写了 collector 却没接线）。
"""
import json
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import write_cluster_summary

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import build_manifest as bm  # noqa: E402

# 通用爽文情绪词标签黑名单（注入内容不得出现这些「贴标签」式直陈词，撞 CLAUDE.md 禁用词）
_EMOTION_LABELS = ["愤怒", "悲伤", "高兴", "害怕", "心中一凛", "他感到", "她感到"]

_CLUSTERS = [
    {"cluster_id": "cluster_001", "chapter_range": [1, 4]},
    {"cluster_id": "cluster_002", "chapter_range": [5, 8]},
]


def _mk_project(tmp: Path, *, clusters=None, pacer=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    write_cluster_summary(tmp, [])
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    if pacer is not None:
        (db / "叙事节拍器.json").write_text(
            json.dumps(pacer, ensure_ascii=False), encoding="utf-8")
    return tmp


# ---------- appraisal_directive ----------

_PACER = {
    "appraisal_beats": [
        {
            "cluster_id": "cluster_001", "scene_idx": 3, "focal_character": "陈默",
            "trigger_event": "老周递来红色档案盒",
            "appraisal": {"relevance": "高", "congruence": "负", "certainty": "低",
                          "coping_potential": "弱", "accountability": "他人", "norm_compat": "违背"},
            "prospect": {"type": "fear", "resolved_to": "fears_confirmed"},
            "derived_emotion": "这盒子把他多年回避的旧账重新摊开，让他既想推开又无法移开视线",
            "behavior_externalization": "指节抵着盒盖却没掀开，喉头动了一下，把烟摁灭在桌角",
            "vad_bin": "High",
        },
        {
            "cluster_id": "cluster_002", "scene_idx": 0, "focal_character": "陈默",
            "trigger_event": "墓碑上看到自己的名字",
            "appraisal": {"relevance": "极高", "congruence": "负", "certainty": "中",
                          "coping_potential": "无", "accountability": "未知"},
            "prospect": {"type": "fear"},
            "derived_emotion": "现实的地基被抽掉，他对『我是谁』这件确定的事第一次产生裂缝",
            "behavior_externalization": "伸手去摸刻字的凹槽，反复描那几笔，像要确认它不是真的",
            "vad_bin": "Mod",
        },
    ]
}


def test_appraisal_residue_and_planned_injected():
    """current=cluster_002：注入 cluster_001 情绪余烬 + cluster_002 本块方向。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, pacer=_PACER)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_appraisal_directive(s, "cluster_002")
        assert r is not None and r["mode"] == "on"
        assert r["residue_count"] == 1   # cluster_001 一条余烬
        assert r["planned_count"] == 1   # cluster_002 一条本块
        directive = r["directive"]
        # 注入「为何（评价）」+「如何外化」+ derived_emotion 方向
        assert "为何（评价" in directive
        assert "如何外化" in directive
        assert "情绪余烬" in directive          # 余烬标记
        assert "本拍情绪方向" in directive       # 本块标记
        assert "指节抵着盒盖" in directive       # behavior_externalization 透传
        assert "现实的地基被抽掉" in directive   # derived_emotion 透传


def test_appraisal_per_beat_lines_no_emotion_word_labels():
    """🔴 北极星⑤：系统逐条渲染 beat 时绝不造『愤怒/心中一凛』式情绪词标签（appraisal-as-prose）。"""
    beat = _PACER["appraisal_beats"][0]
    lines = "\n".join(bm._appraisal_beat_to_lines(beat, residue=True))
    for label in _EMOTION_LABELS:
        assert label not in lines, f"逐条注入不得含情绪词标签『{label}』（应写为何+如何外化）"
    # 但必须含「为何（appraisal 评价）」与「如何外化（动作）」实质内容
    assert "为何" in lines
    assert "如何外化" in lines
    assert "把烟摁灭在桌角" in lines


def test_appraisal_default_safe_no_pacer_file():
    """无 叙事节拍器.json → None（默认安全·向后兼容旧书）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS)  # 不写 pacer
        s = bm.DatabaseScanner(tmp, 6)
        assert bm._collect_appraisal_directive(s, "cluster_002") is None


def test_appraisal_default_safe_no_beats():
    """叙事节拍器.json 存在但无 appraisal_beats → None（零注入零行为变化）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS,
                          pacer={"framework": "save_the_cat", "beats": []})
        s = bm.DatabaseScanner(tmp, 6)
        assert bm._collect_appraisal_directive(s, "cluster_002") is None


def test_appraisal_advisory_never_hard():
    """北极星⑤顾问非法官：gate_level=advisory·结果无 hard_gate 字样。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, pacer=_PACER)
        s = bm.DatabaseScanner(tmp, 6)
        r = bm._collect_appraisal_directive(s, "cluster_002")
        assert r["gate_level"] == "advisory"
        # 绝不有任何字段取值 = hard_gate（_doc 里『绝不 hard_gate』是裸词·非取值·不算）
        assert '"hard_gate"' not in json.dumps(r, ensure_ascii=False)


def test_appraisal_first_cluster_only_planned_no_residue():
    """current=cluster_001（首块）：无历史余烬·只注入本块已规划 beat。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS, pacer=_PACER)
        s = bm.DatabaseScanner(tmp, 2)
        r = bm._collect_appraisal_directive(s, "cluster_001")
        assert r is not None
        assert r["residue_count"] == 0
        assert r["planned_count"] == 1


# ---------- scene_goal（_collect_scene_causal_skeleton 透传） ----------

def test_scene_goal_transmitted_in_skeleton():
    """scene_goal 被 _collect_scene_causal_skeleton 透传进 entry + directive 提及。"""
    cluster = {
        "scene_storyboard": [
            {"scene_index": 0, "scene_goal": "让老周松口交出名单",
             "scene_type": "proactive_scene", "goal": "拿到名单", "conflict": "老周戒备",
             "disaster": "老周反被盯上"},
        ]
    }
    r = bm._collect_scene_causal_skeleton(cluster)
    assert r is not None
    assert r["scenes"][0]["scene_goal"] == "让老周松口交出名单"
    assert "scene_goal" in r["directive"]


def test_scene_goal_only_triggers_injection():
    """只有 scene_goal（无 Swain 字段）也触发注入（has_any=True）。"""
    cluster = {"scene_storyboard": [{"scene_index": 0, "scene_goal": "不被发现地溜出去"}]}
    r = bm._collect_scene_causal_skeleton(cluster)
    assert r is not None
    assert r["scenes"][0]["scene_goal"] == "不被发现地溜出去"


def test_no_causal_fields_no_scene_goal_returns_none():
    """无 scene_goal 且无任何 Swain/But-Therefore 字段 → None（默认安全·零回归）。"""
    cluster = {"scene_storyboard": [{"scene_index": 0, "summary": "普通过场", "characters": ["甲"]}]}
    assert bm._collect_scene_causal_skeleton(cluster) is None


# ---------- 接线进 full build_manifest() ----------

def test_wired_into_final_manifest():
    """appraisal_directive + scene_goal 真正出现在 full build_manifest() dict（防没接线）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        clusters = [
            {"cluster_id": "cluster_001", "parent_me": "ME_001",
             "scope_summary": "陈默值夜班接待第一位访客并发现异常档案盒线索",
             "status": "in_progress", "chapter_range": [1, 4],
             "scene_storyboard": [
                 {"scene_index": 0, "scene_goal": "让老周松口交出名单",
                  "characters": ["陈默"], "summary": "陈默值夜班接待第一位访客"},
             ]},
        ]
        _mk_project(tmp, clusters=clusters, pacer=_PACER)
        (tmp / "_数据库" / "人物卡.json").write_text(
            json.dumps({"characters": [{"id": "cm", "name": "陈默", "role": "主角"}]},
                       ensure_ascii=False), encoding="utf-8")
        prog = {
            "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 4]}],
            "cluster_blueprint": {
                "cluster_001": {
                    "chapter_range": [1, 4],
                    "scene_storyboard": [
                        {"ch": 1, "scene_goal": "让老周松口交出名单",
                         "characters": ["陈默"], "summary": "陈默值夜班接待第一位访客"}
                    ],
                }
            },
        }
        (tmp / "_数据库" / "进度.json").write_text(
            json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        m = bm.build_manifest(tmp, 1)
        assert m["preflight"]["passed"], m["preflight"]
        # appraisal_directive 接线 + cluster_001 本块方向
        ad = m["appraisal_directive"]
        assert ad is not None and ad["mode"] == "on"
        assert ad["planned_count"] == 1
        # cache_layout 登记
        dyn = m["_cache_layout"].get("DYNAMIC_30_cacheable", [])
        assert "appraisal_directive" in dyn
        # scene_goal 透传进 event_cluster_context.scene_causal_skeleton
        ecc = m["event_cluster_context"]
        skel = ecc.get("scene_causal_skeleton")
        assert skel is not None, f"event_cluster_context.mode={ecc.get('mode')}"
        assert skel["scenes"][0]["scene_goal"] == "让老周松口交出名单"


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
