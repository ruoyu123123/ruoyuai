"""build_manifest.DatabaseScanner.time_state 回归测试 — 孤儿 #3（clock/timeline 分歧型）。

钉死：world_clock_events 的真实 schema 是 {date, event, vol[, cluster]}（见
城南火葬场夜班/时间线.json + db_schema_validate.py 只 require `event` · 注释明写
「world clock event 用 day/cluster 颗粒」）—— 全仓无 producer 写 `ch` 字段。

旧实现按废弃的 `e.get("ch") == self.ch` 过滤 → clock_events_this_ch 恒空 →
时钟事件浮现机制死掉、时间线 must_read 永停 P1。

守护点：
  · cluster 颗粒：event.cluster == 本章所属 cluster_id（经 cluster_lookup 归一）
  · date 颗粒：event.date == current_time.date
  · ch 颗粒：兼容仍带 ch 字段的旧数据
  · vol（卷）过粗 → 故意不命中（防整卷每章误标）
  · 真实城南 schema（无 ch、有 date/cluster）必须能浮现，不再恒空
  · build_manifest() 时间线 must_read 命中时升 P0（北极星⑤顾问层注入·非 hard_gate）
  · 不崩、不伪造（脏数据 / 文件缺失）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


def _mk_project(tmp: Path, *, timeline=None, clusters=None, prog=None, characters=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if timeline is not None:
        (db / "时间线.json").write_text(
            json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    if prog is not None:
        (db / "进度.json").write_text(
            json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    return tmp


_CARDS = [{"id": "lin_qi", "name": "林七", "role": "主角"}]


# 城南火葬场夜班真实结构（无 ch · 有 date/vol · event 文本提 cluster_001）
_CITYNAN_TIMELINE = {
    "current_time": {"day": 87, "period": "夜", "chapter": 1,
                     "season": "夏", "date": "2031-08-12"},
    "npc_schedules": {"xie_banjia": "...", "bai_sang": "..."},
    "world_clock_events": [
        {"date": "2024-05-17", "event": "成都零号事件", "vol": "背景"},
        {"date": "2031-08-12", "event": "cluster_001 ch1 开始 · 第 17 具同脸进馆", "vol": "1"},
    ],
}

_CLUSTERS = [
    {"cluster_id": "cluster_001", "chapter_range": [1, 4]},
    {"cluster_id": "cluster_002", "chapter_range": [5, 8]},
]


# ---------- date 颗粒（城南真实 schema：无 ch、有 date） ----------

def test_date_granularity_surfaces_real_citynan_event():
    """回归核心：旧实现按 ch 过滤 → 恒空；新实现按 date 命中当天世界时钟事件。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=_CITYNAN_TIMELINE, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 1)
        r = s.time_state()
        hits = r["clock_events_this_ch"]
        # current_time.date == 2031-08-12 → 命中那条，不命中 2024 背景事件
        assert len(hits) == 1, hits
        assert hits[0]["date"] == "2031-08-12"
        assert hits[0]["_matched_by"] == "date"
        assert "第 17 具同脸进馆" in hits[0]["event"]


def test_date_no_match_other_day_empty():
    """current_time.date 不等于任何事件 date → 不误命中（不伪造）。"""
    tl = dict(_CITYNAN_TIMELINE)
    tl["current_time"] = dict(tl["current_time"], date="2099-01-01")
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 1)
        r = s.time_state()
        assert r["clock_events_this_ch"] == []


# ---------- cluster 颗粒（event 带 cluster 字段） ----------

def test_cluster_granularity_matches_via_lookup():
    """event.cluster == 本章所属 cluster_id（经 cluster_lookup 反查 + 归一）→ 命中。

    ch6 ∈ cluster_002（[5,8]）；事件标 cluster='cluster_2'（非零填充也要归一命中）。
    """
    tl = {
        "current_time": {"date": "2031-09-01"},
        "world_clock_events": [
            {"date": "2031-08-12", "event": "cluster_001 旧事", "cluster": "cluster_001"},
            {"date": "2031-09-30", "event": "cluster_002 转折", "cluster": "cluster_2"},
        ],
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 6)
        r = s.time_state()
        assert r["current_cluster_id"] == "cluster_002"
        hits = r["clock_events_this_ch"]
        assert len(hits) == 1, hits
        assert hits[0]["_matched_by"] == "cluster"
        assert "cluster_002 转折" in hits[0]["event"]


def test_cluster_takes_priority_over_date():
    """同一事件既有匹配 cluster 又有匹配 date → 标 cluster（更精准颗粒优先）。"""
    tl = {
        "current_time": {"date": "2031-08-12"},
        "world_clock_events": [
            {"date": "2031-08-12", "event": "双匹配", "cluster": "cluster_001"},
        ],
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 1)  # ch1 ∈ cluster_001
        r = s.time_state()
        assert r["clock_events_this_ch"][0]["_matched_by"] == "cluster"


# ---------- ch 颗粒（旧数据兼容） ----------

def test_legacy_ch_field_still_matches():
    """仍带 `ch` 字段的旧项目数据 → 兼容命中（不破坏既有项目）。"""
    tl = {
        "current_time": {"date": "no-date"},
        "world_clock_events": [
            {"ch": 3, "event": "旧 ch 颗粒事件"},
            {"ch": 9, "event": "别的章"},
        ],
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 3)
        r = s.time_state()
        hits = r["clock_events_this_ch"]
        assert len(hits) == 1
        assert hits[0]["_matched_by"] == "ch"
        assert hits[0]["event"] == "旧 ch 颗粒事件"


# ---------- vol（卷）过粗：故意不命中 ----------

def test_vol_alone_never_matches():
    """只有 vol（无 date/cluster/ch 匹配）→ 不命中（防整卷每章误标）。"""
    tl = {
        "current_time": {"date": "2031-08-12"},
        "world_clock_events": [
            {"date": "1999-01-01", "event": "vol-only 背景", "vol": "1"},
        ],
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 1)
        r = s.time_state()
        assert r["clock_events_this_ch"] == []


# ---------- 边界：脏数据 / 文件缺失 ----------

def test_dirty_and_missing_no_crash():
    """脏数据（非 dict 项）跳过、文件缺失 → 不崩、不伪造。"""
    tl = {
        "current_time": {"date": "2031-08-12"},
        "world_clock_events": ["junk_str", 123, {"date": "2031-08-12", "event": "ok"}],
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 1)
        r = s.time_state()
        # 只命中那条合法 dict
        assert [h["event"] for h in r["clock_events_this_ch"]] == ["ok"]

    # 完全无时间线.json → 不崩、空命中
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), clusters=_CLUSTERS)
        s = bm.DatabaseScanner(tmp, 1)
        r = s.time_state()
        assert r["clock_events_this_ch"] == []
        assert r["current_time"] == {}


# ---------- 接线：build_manifest() 时间线 must_read 升 P0 ----------

def test_wired_into_manifest_timeline_promoted_to_p0():
    """命中时钟事件 → build_manifest() 时间线 must_read 升 P0 + focus 含事件文本。

    旧 bug 下 clock_events_this_ch 恒空 → 时间线永停 P1、focus 不带事件。
    北极星⑤：这是顾问层注入（升 priority/focus），不是 hard_gate。
    """
    prog = {
        "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 4]}],
        "cluster_blueprint": {
            "cluster_001": {
                "chapter_range": [1, 4],
                "scene_storyboard": [
                    {"ch": 1, "characters": ["林七"], "key_events": ["开局"],
                     "scene_type": ["悬疑"], "summary": "林七值夜班接待第 17 具同脸"}
                ],
            }
        },
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=_CITYNAN_TIMELINE,
                          clusters=_CLUSTERS, prog=prog, characters=_CARDS)
        m = bm.build_manifest(tmp, 1)
        assert m["preflight"]["passed"], m["preflight"]
        timeline_entry = next(
            (x for x in m["must_read"] if x["path"] == "_数据库/时间线.json"), None)
        assert timeline_entry is not None, m["must_read"]
        # 命中事件 → 升 P0
        assert timeline_entry["priority"] == "P0", timeline_entry
        assert "本章时钟事件" in timeline_entry["focus"], timeline_entry
        assert "第 17 具同脸进馆" in timeline_entry["focus"], timeline_entry
        # 不含 hard_gate 语义（顾问层 · 北极星⑤）
        assert "hard_gate" not in json.dumps(timeline_entry, ensure_ascii=False)


def test_wired_no_event_stays_p1():
    """无命中事件 → 时间线仍 P1（不误升）。"""
    tl = {
        "current_time": {"date": "2099-12-31", "period": "夜", "season": "夏"},
        "world_clock_events": [{"date": "2031-08-12", "event": "别的天", "vol": "1"}],
    }
    prog = {
        "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 4]}],
        "cluster_blueprint": {
            "cluster_001": {
                "chapter_range": [1, 4],
                "scene_storyboard": [
                    {"ch": 1, "characters": ["林七"], "key_events": ["开局"],
                     "scene_type": ["悬疑"], "summary": "值夜班"}
                ],
            }
        },
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), timeline=tl, clusters=_CLUSTERS,
                          prog=prog, characters=_CARDS)
        m = bm.build_manifest(tmp, 1)
        timeline_entry = next(
            (x for x in m["must_read"] if x["path"] == "_数据库/时间线.json"), None)
        assert timeline_entry is not None
        assert timeline_entry["priority"] == "P1", timeline_entry
