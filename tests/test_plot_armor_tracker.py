# -*- coding: utf-8 -*-
"""plot_armor_tracker.py 专属回归测试（2026-06-20 · 确定性 · 零依赖 · 零 LLM/零联网）。

覆盖：
  · off / shadow / active 三种 mode
  · 题材门控（轻喜剧 skip）
  · 作者档 allow_high_armor 豁免
  · 草稿太短 skip / 读取失败 note
  · 威胁三档命中（轻伤/重伤/濒死）detect_threats
  · cost_events 提取（factual / facts_locked / cost_events）
  · 滚动窗口 N=3 cluster 历史聚合
  · stakes_credibility 计算 + advisory 触发 / 不触发
  · snapshot 写盘
  · CLI subprocess 退出码
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import plot_armor_tracker as mod  # noqa: E402

_TARGET = _SCRIPTS / "plot_armor_tracker.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("PLOT_ARMOR_MODE", None)
    else:
        os.environ["PLOT_ARMOR_MODE"] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(*, genre=None, allow_high_armor=False,
                clusters=None, changes_by_cluster=None):
    """造项目目录 + _数据库 + 故事块摘要.json + 章节/<cid>_draft/<cid>_changes.json。"""
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if genre:
        (db / "用户偏好.json").write_text(
            json.dumps({"genre": genre}, ensure_ascii=False), encoding="utf-8")
    if allow_high_armor:
        (db / "作者风格.json").write_text(json.dumps(
            {"plot_armor_profile": {"allow_high_armor": True}},
            ensure_ascii=False), encoding="utf-8")
    if clusters:
        (db / "故事块摘要.json").write_text(json.dumps(
            {"clusters": [{"cluster_id": c} for c in clusters]},
            ensure_ascii=False), encoding="utf-8")
    if changes_by_cluster:
        for cid, ch in changes_by_cluster.items():
            cd = proj / "章节" / f"{cid}_draft"
            cd.mkdir(parents=True, exist_ok=True)
            (cd / f"{cid}_changes.json").write_text(
                json.dumps(ch, ensure_ascii=False), encoding="utf-8")
    return proj


_THREAT_DRAFT = (
    "他濒死，剑光闪过，腹部被刺穿，又是骨折。" * 60 +
    "对手厉声咆哮，他身亡了！" * 20)
_CLEAN_DRAFT = "他提剑走进山门，青石阶上落满松针。师兄煮茶。" * 40


def test_off_returns_skeleton():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=None)
        assert out["mode"] == "off"
        assert out["verdict"] == "PASS" and out["violations"] == []
        assert "current_threats" not in out
    finally:
        _set_mode(bak)


def test_comedy_genre_skips():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="comedy_light")
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj)
        assert "轻喜剧" in out["note"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_slice_of_life_skips():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(genre="slice_of_life")
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj)
        assert "轻喜剧" in out["note"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_author_allow_high_armor_skips():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(allow_high_armor=True)
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj)
        assert "allow_high_armor" in out["note"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write_draft("他濒死。" * 5), project_root=None)
        assert out["note"] == "草稿太短·跳过"
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_read_failure_note():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out["note"]
        assert out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_detect_threats_three_tiers():
    r = mod.detect_threats("擦伤了，骨折了，濒死。")
    assert len(r["light"]) >= 1
    assert len(r["heavy"]) >= 1
    assert len(r["lethal"]) >= 1
    assert r["total"] == len(r["light"]) + len(r["heavy"]) + len(r["lethal"])


def test_detect_threats_clean():
    r = mod.detect_threats("他喝茶，看月亮。")
    assert r["total"] == 0


def test_cost_events_from_factual():
    ch = {"factual": {"character_state_changes": [{"a": 1}, {"b": 2}],
                       "deaths": [{"who": "甲"}]}}
    assert mod._cost_events_from_changes(ch) == 3


def test_cost_events_explicit_field():
    ch = {"cost_events": ["x", "y", "z"]}
    assert mod._cost_events_from_changes(ch) == 3


def test_cost_events_bad_input():
    assert mod._cost_events_from_changes({}) == 0
    assert mod._cost_events_from_changes(None) == 0


def test_active_inflation_triggers():
    """威胁 ≥3 但 cost=0 → credibility=0 < 0.2 → FAIL_MINOR。"""
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        proj = _mk_project(
            clusters=["cluster_001"],
            changes_by_cluster={"cluster_001": {"factual": {}}})
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj)
        assert out["window_threat_total"] >= 3
        assert out["window_cost_events"] == 0
        assert out["stakes_credibility"] == 0
        assert out["verdict"] == "FAIL_MINOR"
        assert out["violations"][0]["code"] == "PLOT_ARMOR_INFLATION"
        # snapshot 写盘
        snap = (Path(proj) / "_数据库" / ".cross_chapter_scan"
                / "plot_armor_snapshot.json")
        assert snap.exists()
    finally:
        _set_mode(bak)


def test_active_high_credibility_pass():
    """威胁 + 高 cost → credibility 高 → PASS。"""
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        # cost = 100 件 character_state_changes
        cost_ch = {"factual": {"character_state_changes":
                                [{"i": i} for i in range(100)]}}
        proj = _mk_project(clusters=["cluster_001"],
                           changes_by_cluster={"cluster_001": cost_ch})
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj)
        assert out["stakes_credibility"] >= 0.2
        assert out["verdict"] == "PASS"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_shadow_records_but_no_violation():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("shadow")
        proj = _mk_project(clusters=["cluster_001"],
                           changes_by_cluster={"cluster_001": {}})
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj)
        assert out["mode"] == "shadow"
        assert out["stakes_credibility"] is not None
        assert out["violations"] == [] and out["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_insufficient_threats_skips_judgment():
    """威胁 < 3 → 不判定但写 snapshot。"""
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        # 仅 1 个威胁
        draft = ("他擦伤了。" + "他喝茶。" * 200)
        proj = _mk_project(clusters=["cluster_001"],
                           changes_by_cluster={"cluster_001": {}})
        out = mod.scan(_write_draft(draft), project_root=proj)
        assert "样本不足" in out["note"]
        assert out["verdict"] == "PASS"
        snap = (Path(proj) / "_数据库" / ".cross_chapter_scan"
                / "plot_armor_snapshot.json")
        assert snap.exists()
        snap_obj = json.loads(snap.read_text(encoding="utf-8"))
        assert snap_obj["verdict"] == "skip_insufficient_sample"
    finally:
        _set_mode(bak)


def test_rolling_window_picks_recent_three():
    """窗口 N=3·5 个 cluster 历史只取最近 3 个。"""
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("active")
        cids = [f"cluster_00{i}" for i in range(1, 6)]
        manifest_path = Path(tempfile.mkdtemp()) / "m.json"
        manifest_path.write_text(
            json.dumps({"cluster_id": "cluster_005"}, ensure_ascii=False),
            encoding="utf-8")
        proj = _mk_project(
            clusters=cids,
            changes_by_cluster={cid: {"cost_events": ["x"]}
                                for cid in cids})
        out = mod.scan(_write_draft(_THREAT_DRAFT), project_root=proj,
                       manifest_path=manifest_path)
        # 只看末 3 个 cluster → cost_total=3
        assert out["window_clusters"] == cids[-3:]
        assert out["window_cost_events"] == 3
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
        _set_mode("ACTIVE")
        assert mod._mode() == "active"
    finally:
        _set_mode(bak)


def test_mode_default_when_unset():
    bak = os.environ.get("PLOT_ARMOR_MODE")
    try:
        _set_mode(None)
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_strip_changes_separators():
    assert mod._strip_changes("正文\n---CHANGES---\nlog") == "正文"
    assert mod._strip_changes("一段。\n---CHANGES_FACTUAL---\n{}") == "一段。"
    assert mod._strip_changes("无分隔") == "无分隔"


def test_cjk_count():
    assert mod._cjk_count("你好abc") == 2
    assert mod._cjk_count("") == 0


def test_read_genre_from_user_pref():
    proj = _mk_project(genre="xianxia")
    assert mod._read_genre(proj) == "xianxia"


def test_read_genre_none_project():
    assert mod._read_genre(None) == ""


def test_recent_clusters_fallback_to_chapter_dirs():
    """无 故事块摘要.json 时回退扫 章节/cluster_*_draft。"""
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    for i in (1, 2, 3):
        (proj / "章节" / f"cluster_00{i}_draft").mkdir(parents=True, exist_ok=True)
    window = mod._recent_clusters(proj, "cluster_003", n=2)
    assert window == ["cluster_002", "cluster_003"]


def test_changes_for_cluster_flash_fallback():
    proj = Path(tempfile.mkdtemp())
    cd = proj / "章节" / "cluster_001_draft"
    cd.mkdir(parents=True, exist_ok=True)
    (cd / "cluster_001_changes.flash.json").write_text(
        json.dumps({"cost_events": ["x"]}, ensure_ascii=False), encoding="utf-8")
    obj = mod._changes_for_cluster(proj, "cluster_001")
    assert obj.get("cost_events") == ["x"]


def _run_cli(draft, project, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(draft), "--project", str(project)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PLOT_ARMOR_MODE": mode,
             "PYTHONIOENCODING": "utf-8"})


def test_main_exit_1_on_warning():
    proj = _mk_project(clusters=["cluster_001"],
                       changes_by_cluster={"cluster_001": {}})
    p = _write_draft(_THREAT_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "FAIL_MINOR"
    assert rep["violations"][0]["code"] == "PLOT_ARMOR_INFLATION"


def test_main_exit_0_clean():
    proj = _mk_project(genre="comedy_light")
    p = _write_draft(_THREAT_DRAFT)
    r = _run_cli(p, proj)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["verdict"] == "PASS"
