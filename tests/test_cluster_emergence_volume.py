"""卷=阶段触发点 · cluster=小走向 涌现引擎回归测试（v28 · 2026-06-03）。

钉死「单 cluster 塌缩成整副本/整阶段」根治后的三条契约：
  1. _me_volume —— ME 卷号解析（显式 volume / 'ME-V2-03' id / None 兜底）。
  2. me_to_cluster_brief —— 卷末 finale ME → brief 标 is_volume_finale + scope_summary
     追加卷末转折指令 + 透传 volume/stakes_delta（build_manifest 据此给 writer 注入）。
  3. emerge_next_cluster 硬过滤 —— 核心任务(本卷)未解前只在【当前卷】内涌现小走向，
     绝不跳到下一卷/新副本；本卷只剩 volume_finale → 置 volume_transition_advisory（advisory·不硬切）。

北极星边界：仍是顾问——advisory 是建议非硬锁，无 volume 标记的旧项目 ME 不参与过滤（向后兼容）。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_emergence_engine as cee  # noqa: E402


# ───────────────────── 1. _me_volume 卷号解析 ─────────────────────

def test_me_volume_explicit_int():
    assert cee._me_volume({"id": "ME-V1-01", "volume": 2}) == 2  # 显式 volume 优先于 id


def test_me_volume_str_digit():
    assert cee._me_volume({"volume": "3"}) == 3


def test_me_volume_from_id_pattern():
    assert cee._me_volume({"id": "ME-V2-03"}) == 2      # 大写 V
    assert cee._me_volume({"me_id": "ME-v4-09"}) == 4   # 小写 v


def test_me_volume_none_when_no_marker():
    """无 volume 字段且 id 无 V 标记 → None（不参与卷过滤·向后兼容旧项目）。"""
    assert cee._me_volume({"id": "ME_001"}) is None
    assert cee._me_volume({}) is None


# ───────────────────── 2. me_to_cluster_brief 透传卷级语义 ─────────────────────

def test_brief_finale_marks_volume_finale():
    """卷末 finale ME → brief is_volume_finale=True + scope_summary 含卷末转折指令。"""
    me = {"id": "ME-V1-07", "volume": 1, "title": "进主大典反杀", "description": "收束本阶段",
          "is_volume_finale": True, "stakes_delta": "从个人逃生升到掀翻整座副本规则"}
    brief = cee.me_to_cluster_brief(me, "cluster_007_candidate_1", 1, {})
    assert brief["is_volume_finale"] is True
    assert brief["volume"] == 1
    assert brief["stakes_delta"] == "从个人逃生升到掀翻整座副本规则"
    assert "volume_finale" in brief["scope_summary"]
    assert "禁平稳收束" in brief["scope_summary"]


def test_brief_non_finale_no_finale_marker():
    """普通小走向 ME → is_volume_finale=False·scope_summary 不带卷末指令。"""
    me = {"id": "ME-V1-02", "volume": 1, "title": "摸规则", "description": "试错阶段",
          "stakes_delta": "比上块略升"}
    brief = cee.me_to_cluster_brief(me, "cluster_002_candidate_1", 1, {})
    assert brief["is_volume_finale"] is False
    assert brief["volume"] == 1
    assert "volume_finale" not in brief["scope_summary"]


# ───────────────────── 3. emerge_next_cluster 卷内硬过滤 ─────────────────────

def _mk_project(tmp: Path, major_events: list, clusters: list) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(
        json.dumps({"schema_version": "v27", "major_events": major_events},
                   ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False), encoding="utf-8")
    # 世界状态 / arc 给空骨架即可（load_json 有 default，emerge 不强依赖内容）
    (db / "世界状态.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    (db / "character_arc_state.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    return tmp


_ME_POOL = [
    {"id": "ME-V1-01", "volume": 1, "title": "育新入门", "description": "进第一个副本", "status": "pending"},
    {"id": "ME-V1-02", "volume": 1, "title": "摸规则", "description": "试错升级", "status": "pending",
     "is_volume_finale": True},
    {"id": "ME-V2-01", "volume": 2, "title": "明伦书院", "description": "换新副本/新阶段", "status": "pending"},
]


def _read_emergence(result):
    return json.loads(Path(result["emergence_path"]).read_text(encoding="utf-8"))


def test_emerge_filters_to_current_volume():
    """V1 仍有非 finale 小走向未完成 → candidate 只能来自卷 1，绝不冒出卷 2（防塌缩整副本）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ME_POOL, clusters=[])  # 无 cluster 完成
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        em = _read_emergence(r)
        assert em["current_volume"] == 1
        parent_mes = {c["parent_me"] for c in em["candidates"]}
        assert parent_mes  # 至少一个候选
        assert "ME-V2-01" not in parent_mes        # 🔴 卷 2 被硬过滤
        assert parent_mes <= {"ME-V1-01", "ME-V1-02"}
        # 本卷还有非 finale（ME-V1-01）→ 不该提前置换卷 advisory
        assert em["volume_transition_advisory"] is None


def test_emerge_sets_transition_advisory_when_only_finale_left():
    """V1 非 finale 全完成、只剩 volume_finale → 置换卷 advisory + 仍不跳卷 2。"""
    with tempfile.TemporaryDirectory() as d:
        # ME-V1-01 已被某 cluster 完成
        clusters = [{"cluster_id": "cluster_001", "status": "done", "ME_to_advance": ["ME-V1-01"]}]
        root = _mk_project(Path(d), _ME_POOL, clusters=clusters)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        em = _read_emergence(r)
        assert em["current_volume"] == 1
        assert em["volume_transition_advisory"] is not None
        assert "阶段触发点" in em["volume_transition_advisory"]
        parent_mes = {c["parent_me"] for c in em["candidates"]}
        assert parent_mes == {"ME-V1-02"}          # 只剩本卷 finale
        assert "ME-V2-01" not in parent_mes         # 仍不硬切到卷 2


def test_emerge_legacy_no_volume_marker_not_filtered():
    """向后兼容：ME 全无 volume 标记 → current_volume=None·不做卷过滤（旧项目不受影响）。"""
    legacy_pool = [
        {"id": "ME_001", "description": "事件甲", "status": "pending"},
        {"id": "ME_002", "description": "事件乙", "status": "pending"},
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), legacy_pool, clusters=[])
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        em = _read_emergence(r)
        assert em["current_volume"] is None
        assert em["volume_transition_advisory"] is None
        parent_mes = {c["parent_me"] for c in em["candidates"]}
        assert parent_mes <= {"ME_001", "ME_002"}   # 两个旧 ME 都可候选
