# -*- coding: utf-8 -*-
"""A10 目标停滞检测 回归测试 — 🔴 2026-07-07（Magnet arXiv:2607.00918 若渝形态）。

钉死 emergence_transparency.goal_stagnation + cluster_emergence_engine 接线：
  · 停滞正例：卷核心任务相关 ME 连续 window 个 cluster 零推进 + 候选也无推进项
  · 负例三路：窗口内有核心推进 / 候选卡含推进项 / 已落章不足 window（证据不足不妄断）
  · env RUOYU_GOAL_STAGNATION_WINDOW 可调（非法值回默认 3）
  · 数据缺失诚实 skip（无卷标记 / 缺 volume_core_conflict）
  · emergence 输出零破坏：emerge_next_cluster 新增 goal_stagnation 段·候选数量/
    排序/既有字段不变（北极星③软牵引 ⑤不裁决——只提示人绝不换目标）
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_emergence_engine as cee  # noqa: E402
import emergence_transparency as et  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

_VOLS = [{"vol": 1, "title": "立序殿风波",
          "volume_core_conflict": "夺回玄铁令镇压立序殿叛乱",
          "volume_thread": "玄铁令的下落贯穿本卷"}]

# 核心相关 ME（与 core_conflict 2-gram 重叠 ≥2：玄铁/铁令/夺回…）与完全无关 ME
_CORE_ME = {"id": "ME-V1-09", "volume": 1, "title": "玄铁令决战",
            "description": "夺回玄铁令与立序殿正面对决", "status": "pending"}
_SIDE_MES = [
    {"id": f"ME-V1-0{i}", "volume": 1, "title": f"支线{i}",
     "description": desc, "status": "pending"}
    for i, desc in ((1, "主角在市集买菜遇旧识闲聊家常"),
                    (2, "帮邻居修补屋顶换一顿晚饭"),
                    (3, "陪小妹去河边放灯许愿"),
                    (4, "碾米坊帮工赚几个铜板"))
]


def _landed(num: int, me_id: str) -> dict:
    return {"cluster_id": f"cluster_{num:03d}", "status": "done", "vol": 1,
            "parent_me": me_id, "ME_to_advance": [me_id]}


def _call(clusters, candidates, *, volumes=_VOLS, pool=None, vol=1, window=None):
    dashishi = {"major_events": pool if pool is not None else [_CORE_ME] + _SIDE_MES,
                "volumes": volumes}
    return et.goal_stagnation(
        {"clusters": clusters}, dashishi, vol, candidates,
        get_me_id=cee._get_me_id, me_text=cee._me_text,
        keyword_set=cee._keyword_set, me_volume=cee._me_volume, window=window)


# ═══════════════════ 正/负例 ═══════════════════

def test_stagnation_detected(tmp_path):
    """连续 3 个已落章 cluster 全推进支线 + 候选也全支线 → detected。"""
    clusters = [_landed(i, f"ME-V1-0{i}") for i in (1, 2, 3)]
    out = _call(clusters, [_SIDE_MES[3]])
    assert out["detected"] is True
    assert out["window"] == 3
    assert out["checked_clusters"] == ["cluster_001", "cluster_002", "cluster_003"]
    assert "停滞" in out["advisory"] and "绝不改打分" in out["advisory"]


def test_core_progress_in_window_not_stagnant():
    """窗口内有一块推进了核心相关 ME → 不算滞。"""
    clusters = [_landed(1, "ME-V1-01"), _landed(2, "ME-V1-09"), _landed(3, "ME-V1-02")]
    out = _call(clusters, [_SIDE_MES[3]])
    assert out["detected"] is False
    assert out["core_progress_in_window"] == {"cluster_002": ["ME-V1-09"]}


def test_candidate_core_related_not_stagnant():
    """候选卡里有核心推进项（用户下一步就能选）→ 不算滞。"""
    clusters = [_landed(i, f"ME-V1-0{i}") for i in (1, 2, 3)]
    out = _call(clusters, [_CORE_ME])
    assert out["detected"] is False
    assert out["candidates_core_related"] == ["ME-V1-09"]


def test_insufficient_clusters_no_verdict():
    """已落章不足 window → 证据不足不妄断（skipped 留痕）。"""
    clusters = [_landed(1, "ME-V1-01"), _landed(2, "ME-V1-02")]
    out = _call(clusters, [_SIDE_MES[3]])
    assert out["detected"] is False
    assert "证据不足" in out["skipped"]


def test_candidate_status_clusters_never_counted():
    """status=candidate 的涌现候选块绝不算写作进度。"""
    clusters = [_landed(i, f"ME-V1-0{i}") for i in (1, 2, 3)]
    clusters.append({"cluster_id": "cluster_004", "status": "candidate", "vol": 1,
                     "parent_me": "ME-V1-09", "ME_to_advance": ["ME-V1-09"]})
    out = _call(clusters, [_SIDE_MES[3]])
    assert out["detected"] is True  # candidate 块的核心推进不解除停滞


# ═══════════════════ env 窗口 / skip ═══════════════════

def test_env_window_adjustable(monkeypatch):
    monkeypatch.setenv("RUOYU_GOAL_STAGNATION_WINDOW", "2")
    clusters = [_landed(1, "ME-V1-01"), _landed(2, "ME-V1-02")]
    out = _call(clusters, [_SIDE_MES[3]])
    assert out["window"] == 2 and out["detected"] is True


def test_env_window_invalid_falls_back_default(monkeypatch):
    monkeypatch.setenv("RUOYU_GOAL_STAGNATION_WINDOW", "abc")
    assert et.stagnation_window() == 3
    monkeypatch.setenv("RUOYU_GOAL_STAGNATION_WINDOW", "0")
    assert et.stagnation_window() == 3
    monkeypatch.delenv("RUOYU_GOAL_STAGNATION_WINDOW", raising=False)
    assert et.stagnation_window() == 3


def test_skip_no_volume_tag():
    out = _call([_landed(1, "ME-V1-01")], [], vol=None)
    assert out["detected"] is False and "无卷标记" in out["skipped"]


def test_skip_missing_core_conflict_field():
    """本卷缺 volume_core_conflict/volume_thread → 诚实 skip（不猜核心任务）。"""
    vols = [{"vol": 1, "title": "无核心字段卷"}]
    clusters = [_landed(i, f"ME-V1-0{i}") for i in (1, 2, 3)]
    out = _call(clusters, [], volumes=vols)
    assert out["detected"] is False
    assert "volume_core_conflict" in out["skipped"]


# ═══════════════════ emergence 接线零破坏 ═══════════════════

def _mk_project(tmp: Path, clusters: list) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(json.dumps(
        {"major_events": [_CORE_ME] + _SIDE_MES, "volumes": _VOLS},
        ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    (db / "世界状态.json").write_text("{}", encoding="utf-8")
    (db / "character_arc_state.json").write_text("{}", encoding="utf-8")
    return tmp


def test_emerge_output_has_goal_stagnation_zero_breakage(tmp_path):
    """emerge_next_cluster 输出新增 goal_stagnation 段·既有字段/候选生成不受影响。"""
    clusters = [_landed(i, f"ME-V1-0{i}") for i in (1, 2, 3)]
    p = _mk_project(tmp_path, clusters)
    result = cee.emerge_next_cluster(p, "cluster_003")
    assert result["ok"] is True
    assert isinstance(result.get("goal_stagnation"), dict)
    em = json.loads(Path(result["emergence_path"]).read_text(encoding="utf-8"))
    # 既有 schema 零破坏
    for key in ("_schema", "candidates", "dag_health", "current_volume", "next_cluster_id"):
        assert key in em
    assert em["_schema"] == "cluster_emergence_v24"
    assert 1 <= len(em["candidates"]) <= 3
    # 新段存在且结构完整
    gs = em["goal_stagnation"]
    assert set(gs) >= {"detected", "window", "volume"}
    assert gs["volume"] == 1


def test_emerge_stagnation_never_alters_candidates(tmp_path, monkeypatch):
    """北极星③⑤：同一项目，停滞判定「跑出结论」vs「窗口不足 skip」两种状态下
    候选 ME 集合与排序完全一致——goal_stagnation 只提示不干涉。"""
    stag_clusters = [_landed(i, f"ME-V1-0{i}") for i in (1, 2, 3)]
    p = _mk_project(tmp_path, stag_clusters)

    monkeypatch.setenv("RUOYU_GOAL_STAGNATION_WINDOW", "3")
    r1 = cee.emerge_next_cluster(p, "cluster_003")
    em1 = json.loads(Path(r1["emergence_path"]).read_text(encoding="utf-8"))
    assert "skipped" not in em1["goal_stagnation"]  # 窗口够·真跑了判定

    monkeypatch.setenv("RUOYU_GOAL_STAGNATION_WINDOW", "99")
    r2 = cee.emerge_next_cluster(p, "cluster_003")
    em2 = json.loads(Path(r2["emergence_path"]).read_text(encoding="utf-8"))
    assert "证据不足" in em2["goal_stagnation"]["skipped"]  # 窗口不足 → skip

    def _me_ids(em):
        return [c["parent_me"] for c in em["candidates"]]

    assert _me_ids(em1) == _me_ids(em2)
