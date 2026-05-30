"""clock / stress / narrator 三引擎 + manifest 消费端真实维度 schema 兼容回归测试
   — [#3 北极星③契约修复 · 整子系统级]。

背景（3 真实项目实测）：三引擎硬编码 v21 扁平 schema，但 AI 在 outline 阶段自由生成维度/
段位 schema，导致引擎静默 no-op + 字段孤儿 + manifest 注入默认空值：
  · clock_engine 只处理 status==active + ticks/max/tick_on；真实(城南)是 current_segments/
    max_segments/linked_me（无 status/ticks）→ _do_tick 全跳过 / list_active 空 / writer 永拿不到 clock。
  · stress_evaluator 读 persona_violations_tracked.core_traits / stress_level；真实(纵尸司/诡异)是
    stress_dimensions/breakdown_threshold（无 stress_level 标量）→ traits=[] / delta 恒 0 / 永不抽
    mental_break / manifest stress_level 缺省 0 / is_high_stress 恒 false。
  · narrator_calibrate 读 storyteller_profile/current_pressure_phase；真实是 framework/beats /
    rhythm_profile —— outline 产的 framework/beats/rhythm_profile 成孤儿（无消费方进 manifest）。

修复纪律（参照 fate_engine._events/._event_id accessor 范式）：让三引擎 + manifest **兼容读取**真实
维度 schema（最小兼容读取·非重写引擎），不强制改 outline schema（不破坏已有项目）。
北极星③软牵引：引擎读到真实状态注入 writer，但不把子系统状态变成硬约束。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import clock_engine  # noqa: E402
import stress_evaluator  # noqa: E402
import narrator_calibrate  # noqa: E402
import build_manifest as bm  # noqa: E402


def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


# ═══════════════════════════════ CLOCK 引擎 ═══════════════════════════════

# ---- 真实 schema 形态 ----
_CLOCK_城南 = {  # current_segments/max_segments/linked_me · 无 status/ticks/tick_on
    "_meta": {"version": "v1"},
    "clocks": [
        {"clock_id": "clk_18th_face", "description": "第 18 张脸已在路上",
         "current_segments": 0, "max_segments": 18, "linked_me": ["ME-V1-01"]},
        {"clock_id": "clk_lao_zheng_curse", "description": "老郑在场必死累计",
         "current_segments": 12, "max_segments": 13, "linked_me": ["ME-V2-01"]},
    ],
}
_CLOCK_纵尸司 = {  # id/initial/current/trigger_at_zero · 倒计时（current 递减到 0 触发）
    "clocks": [
        {"id": "clock_松绑残卷收集", "name": "残卷七分之七", "initial": 7, "current": 1,
         "unit": "残卷段", "trigger_at_zero": "主角能彻底松绑所有尸吏", "tier": 1},
        {"id": "clock_陆母病情", "name": "陆母病情倒计时", "initial": 12, "current": 12,
         "unit": "cluster", "trigger_at_zero": "陆母病故", "tier": 1},
    ],
}
_CLOCK_诡异 = {  # story_clocks[] · id/current/target/trigger_at_target · 正计时（升到 target）
    "schema_version": "v2.cluster",
    "story_clocks": [
        {"id": "clock_001", "name": "审查组介入倒计时", "current": 0, "target": 10,
         "trigger_at_target": "cluster_005 审查组下令"},
        {"id": "clock_002", "name": "陆建国身份暴露倒计时", "current": 0, "target": 8,
         "trigger_at_target": "cluster_004 身份揭示"},
    ],
}
_CLOCK_v21 = {  # 旧形态（向后兼容必须不破坏）
    "_schema": "clocks_v21_explicit_progression",
    "clocks": [
        {"clock_id": "CK_001", "label": "顾沉招安耐心", "ticks": 1, "max": 10,
         "tick_on": ["chapter_end"], "tick_per_event": 1, "status": "active",
         "trigger_on_max": "ME_004"},
    ],
}


def _mk_clock_project(tmp: Path, data: dict) -> Path:
    (_mk_db(tmp) / "时钟表.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp


def test_clock_list_active_reads_segments_schema_城南():
    """城南 current_segments/max_segments 无 status → list_active 必须读到（旧版恒空）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), _CLOCK_城南)
        r = clock_engine.list_active(root, 1)
        assert r["total_active"] == 2
        by_id = {c["clock_id"]: c for c in r["active_clocks"]}
        assert by_id["clk_lao_zheng_curse"]["ticks"] == 12
        assert by_id["clk_lao_zheng_curse"]["max"] == 13
        assert by_id["clk_lao_zheng_curse"]["remaining"] == 1
        assert by_id["clk_lao_zheng_curse"]["urgency"] == "urgent"


def test_clock_list_active_reads_countdown_schema_纵尸司():
    """纵尸司倒计时 current 递减到 0 触发 → 归一成 ticks=initial-current 升到 max。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), _CLOCK_纵尸司)
        r = clock_engine.list_active(root, 1)
        by_id = {c["clock_id"]: c for c in r["active_clocks"]}
        # 残卷：initial=7 current=1 → elapsed=6 / max=7 / remaining=1（逼近触发）
        rc = by_id["clock_松绑残卷收集"]
        assert rc["ticks"] == 6 and rc["max"] == 7 and rc["remaining"] == 1
        assert rc["urgency"] == "urgent"
        # 陆母：initial=12 current=12 → elapsed=0 远未触发
        assert by_id["clock_陆母病情"]["remaining"] == 12


def test_clock_list_active_reads_story_clocks_schema_诡异():
    """诡异 story_clocks[] 键名 + current/target → list_active 必须读到。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), _CLOCK_诡异)
        r = clock_engine.list_active(root, 1)
        assert r["total_active"] == 2
        by_id = {c["clock_id"]: c for c in r["active_clocks"]}
        assert by_id["clock_001"]["max"] == 10
        assert by_id["clock_001"]["trigger_on_max"] == "cluster_005 审查组下令"


def test_clock_v21_still_works():
    """v21 ticks/max/tick_on/status 向后兼容不破坏。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), _CLOCK_v21)
        r = clock_engine.list_active(root, 1)
        assert r["total_active"] == 1
        assert r["active_clocks"][0]["ticks"] == 1
        assert r["active_clocks"][0]["max"] == 10


def test_clock_chapter_tick_does_not_mechanically_advance_no_tick_on():
    """北极星③⑤回归：无 tick_on 的维度 clock（城南 linked_me）chapter_end **不机械推进**。
    batch5 audit-r4 让无 tick_on 的 clock 每章无条件 advance，把 Clock 从 advisory soft-pull
    变成机械剧情驱动器（3 真实项目全 tick_on=None → 全被 force-tick）。修正后：surface 但 status 不变。
    """
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), json.loads(json.dumps(_CLOCK_城南)))
        # 即便 clk_lao_zheng_curse 已 12/13（差 1 满格），按章 tick 也不得机械触发
        r = clock_engine.tick_chapter(root, 5)
        assert r["ticked"] == []  # 无 tick_on → 不推进
        assert r["triggered"] == []  # → 不机械触发 ME
        after = json.loads((root / "_数据库" / "时钟表.json").read_text(encoding="utf-8"))
        by_id = {c["clock_id"]: c for c in after["clocks"]}
        # 进度保持不变（read-only · 不按章节计数推进）
        assert by_id["clk_18th_face"]["current_segments"] == 0
        assert by_id["clk_lao_zheng_curse"]["current_segments"] == 12
        # 也不写 status=triggered（差 1 满格仍不靠章节计数触发）
        assert by_id["clk_lao_zheng_curse"].get("status") != "triggered"
        # 但仍被 surface 给 writer（保留 batch5 修 no-op 的正确部分）
        la = clock_engine.list_active(root, 5)
        assert la["total_active"] == 2


def test_clock_chapter_tick_no_advance_countdown_no_tick_on():
    """纵尸司倒计时（无 tick_on · trigger_at_zero 由叙事驱动）chapter_end 不机械递减。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), json.loads(json.dumps(_CLOCK_纵尸司)))
        r = clock_engine.tick_chapter(root, 3)
        assert r["ticked"] == [] and r["triggered"] == []
        after = json.loads((root / "_数据库" / "时钟表.json").read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in after["clocks"]}
        # 残卷 current 仍 1（不机械递减到 0 触发）· 陆母仍 12
        assert by_id["clock_松绑残卷收集"]["current"] == 1
        assert by_id["clock_松绑残卷收集"].get("status") != "triggered"
        assert by_id["clock_陆母病情"]["current"] == 12


def test_clock_chapter_tick_no_advance_story_clocks_no_tick_on():
    """诡异 story_clocks（无 tick_on · incremented_by 叙事事件驱动）chapter_end 不机械推进。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), json.loads(json.dumps(_CLOCK_诡异)))
        r = clock_engine.tick_chapter(root, 2)
        assert r["ticked"] == []
        after = json.loads((root / "_数据库" / "时钟表.json").read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in after["story_clocks"]}
        assert by_id["clock_001"]["current"] == 0  # 不机械 +1


def test_clock_v21_tick_on_still_advances():
    """v21 显式 tick_on=[chapter_end] 仍按章推进（向后兼容·显式声明的才推进）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), json.loads(json.dumps(_CLOCK_v21)))
        r = clock_engine.tick_chapter(root, 2)
        # CK_001 ticks 1→2（显式 tick_on 匹配 chapter_end）
        assert len(r["ticked"]) == 1
        after = json.loads((root / "_数据库" / "时钟表.json").read_text(encoding="utf-8"))
        assert after["clocks"][0]["ticks"] == 2


def test_clock_dashboard_counts_all_schemas():
    """dashboard 必须统计到维度 schema 的 clock（旧版 total_clocks=0）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_clock_project(Path(d), _CLOCK_诡异)
        db = clock_engine.dashboard(root)
        assert db["total_clocks"] == 2


# ═══════════════════════════════ STRESS 引擎 ═══════════════════════════════

_STRESS_纵尸司 = {  # stress_dimensions 扁平 int + breakdown_threshold + stress_history
    "protagonist": "陆寒山",
    "stress_dimensions": {"guilt": 0, "fear": 30, "self_deception": 0, "isolation": 0, "duty": 0},
    "stress_log": [],
    "breakdown_threshold": 80,
}
_STRESS_诡异 = {  # stress_dimensions 嵌套 dict {current,max,trigger_threshold} + stress_history
    "schema_version": "v2.cluster",
    "protagonist_id": "陆建国",
    "stress_dimensions": {
        "身份暴露": {"current": 0, "max": 10, "trigger_threshold": 8},
        "机构存续": {"current": 0, "max": 10, "trigger_threshold": 9},
        "同事信任": {"current": 5, "max": 10},
    },
    "stress_history": [],
}
_STRESS_v21 = {  # 标量 schema（向后兼容）
    "protagonist": "林七",
    "stress_level": 4,
    "stress_max": 10,
    "stress_threshold_break": 8,
    "persona_violations_tracked": {"core_traits": [
        {"trait": "护短", "violation_keywords": ["背叛"], "align_keywords": ["保护"],
         "stress_per_violation": 2},
    ]},
    "stress_log": [],
}


def test_stress_view_flat_dimensions_纵尸司():
    """纵尸司扁平 int stress_dimensions → 归一出非 0 标量（peak=fear 30/? → 归一）。"""
    v = stress_evaluator.stress_view(_STRESS_纵尸司)
    assert v["mode"] == "dimensions"
    # fear=30，默认维度 max 取 stress_max 缺省 10 → norm = round(30/10*10)=30（夸张但非 0）
    # 重点：不再恒 0（旧版 manifest stress_level 缺省 0）
    assert v["stress_level"] > 0
    assert set(v["_dim_keys"]) == {"guilt", "fear", "self_deception", "isolation", "duty"}


def test_stress_view_nested_dimensions_诡异():
    """诡异嵌套 dict stress_dimensions → 用各维度 max 归一聚合（同事信任 5/10 → 5）。"""
    v = stress_evaluator.stress_view(_STRESS_诡异)
    assert v["mode"] == "dimensions"
    assert v["stress_level"] == 5  # 同事信任 current=5 max=10 → norm 5（峰值维度）
    assert v["stress_max"] == 10


def test_stress_view_nondefault_dim_max_threshold_normalized():
    """🔴 batch5 归一 bug 回归：峰值维度 max != 10 且 trigger_threshold > 10 时，
    threshold 必须用**该维度自己的 dmax** 归一（旧码硬编码 round(threshold/100*10) → 阈值塌成假高）。
    构造 max=20 / trigger_threshold=16 的维度（真实量纲非默认 10）：threshold = 16/20*10 = 8
    （旧 bug：16>10 → round(16/100*10)=2 → 阈值塌成 2 → 任意低 stress 都假高）。
    """
    high = {
        "protagonist_id": "高压",
        "stress_dimensions": {
            "罪疚": {"current": 18, "max": 20, "trigger_threshold": 16},
            "孤立": {"current": 2, "max": 20, "trigger_threshold": 16},
        },
        "stress_history": [],
    }
    v = stress_evaluator.stress_view(high)
    assert v["mode"] == "dimensions"
    assert v["stress_level"] == 9  # 罪疚 18/20 → 归一 9（峰值维度）
    assert v["stress_threshold_break"] == 8  # 16/20*10 = 8（旧 bug 会塌成 2）
    # 低 stress 档校验阈值不塌：4/20→level 2，threshold 仍 8 → 非高压（旧 bug threshold=2 会判假高）
    low = {
        "protagonist_id": "低压",
        "stress_dimensions": {"罪疚": {"current": 4, "max": 20, "trigger_threshold": 16}},
        "stress_history": [],
    }
    lv = stress_evaluator.stress_view(low)
    assert lv["stress_level"] == 2  # 4/20 → 2
    assert lv["stress_threshold_break"] == 8  # 仍 8（旧 bug 会是 2）
    assert lv["stress_level"] < lv["stress_threshold_break"] * 0.75  # 真实判据：非高压


def test_stress_view_scalar_v21():
    """v21 标量 schema 向后兼容：直接读 stress_level/threshold/traits。"""
    v = stress_evaluator.stress_view(_STRESS_v21)
    assert v["mode"] == "scalar"
    assert v["stress_level"] == 4
    assert v["stress_threshold_break"] == 8
    assert len(v["traits"]) == 1


def test_stress_evaluate_dimension_schema_not_noop():
    """维度 schema evaluate 跑完不再 no-op：写 stress_history、返回 schema_mode=dimensions、不崩。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps(json.loads(json.dumps(_STRESS_诡异)),
                                                  ensure_ascii=False), encoding="utf-8")
        ch = 3
        ch_dir = root / "章节" / f"第{ch:03d}章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / f"第{ch:03d}章.txt").write_text("正文内容，主角在窗口办公。", encoding="utf-8")
        r = stress_evaluator.evaluate(root, ch)
        assert r["schema_mode"] == "dimensions"
        assert "error" not in r
        after = json.loads((db / "主角压力档.json").read_text(encoding="utf-8"))
        # 维度 schema 用 stress_history 记日志（不是 stress_log）
        assert len(after["stress_history"]) == 1
        # 不擅自往维度写 delta（引擎只读维度 · 北极星⑤）
        assert after["stress_dimensions"]["同事信任"]["current"] == 5
        # 也不伪造 stress_level 标量键（避免污染维度 schema 文件）
        assert "stress_level" not in after


def test_stress_evaluate_scalar_still_accumulates():
    """v21 标量 schema evaluate 仍按 trait 关键词累加 delta（向后兼容不破坏）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps(json.loads(json.dumps(_STRESS_v21)),
                                                  ensure_ascii=False), encoding="utf-8")
        ch = 2
        ch_dir = root / "章节" / f"第{ch:03d}章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        # 命中 violation_keywords「背叛」2 次 → delta = 2*2 = 4
        (ch_dir / f"第{ch:03d}章.txt").write_text("他背叛了，再次背叛。", encoding="utf-8")
        r = stress_evaluator.evaluate(root, ch)
        assert r["schema_mode"] == "scalar"
        assert r["stress_delta"] == 4
        assert r["stress_new"] == 8  # 4 + 4


# ═══════════════════════════════ NARRATOR 引擎 ═══════════════════════════════

_NARR_纵尸司 = {  # framework + beats (Save_the_Cat)
    "framework": "Save_the_Cat",
    "beats": [
        {"id": "opening_image", "desc": "in_medias_res 松绑现场", "at_cluster": "cluster_001", "done": False},
        {"id": "catalyst", "desc": "第一次松绑完成", "at_cluster": "cluster_001", "done": False},
        {"id": "debate", "desc": "第二次松绑前犹豫", "at_cluster": "cluster_002", "done": False},
        {"id": "midpoint", "desc": "第一具失控尸吏", "at_cluster": "cluster_021", "done": False},
    ],
}
_NARR_诡异 = {  # rhythm_profile + beat_density_by_cluster
    "schema_version": "v2.cluster",
    "rhythm_profile": "混合",
    "beat_density_by_cluster": {"cluster_001": "中（首 cluster 倒叙）", "cluster_004": "高（Midpoint）"},
}
_NARR_v21 = {  # 旧形态
    "storyteller_profile": "cassandra",
    "current_pressure_phase": "cooldown",
    "adaptation_factor": {"current_setback_count_in_window": 0, "current_win_streak": 10},
    "narrator_recommendation": {"next_chapter_target_outcome": "setback"},
}


def test_narrator_view_savethecat_beats_纵尸司():
    """纵尸司 framework/beats → narrator_view 暴露本 cluster 该命中的节拍（旧版孤儿）。"""
    v = narrator_calibrate.narrator_view(_NARR_纵尸司, "cluster_001")
    assert v["framework"] == "Save_the_Cat"
    ids = [b["id"] for b in v["current_cluster_beats"]]
    assert ids == ["opening_image", "catalyst"]  # 只 cluster_001 命中的两个


def test_narrator_view_rhythm_density_诡异():
    """诡异 rhythm_profile/beat_density → narrator_view 暴露 cluster 节奏密度（旧版孤儿）。"""
    v = narrator_calibrate.narrator_view(_NARR_诡异, "cluster_001")
    assert v["rhythm_profile"] == "混合"
    assert v["current_cluster_density"] == "中（首 cluster 倒叙）"


def test_narrator_view_v21_still_works():
    """v21 storyteller_profile/adaptation_factor 向后兼容。"""
    v = narrator_calibrate.narrator_view(_NARR_v21, "cluster_001")
    assert v["profile"] == "cassandra"
    assert v["current_phase"] == "cooldown"
    assert v["adaptation_factor"]["current_win_streak"] == 10


def test_narrator_calibrate_marks_beats_done():
    """calibrate 跑完 → 本 cluster 的 Save_the_Cat beats 标 done=true（孤儿契约修复：beats 有消费方）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "叙事节拍器.json").write_text(json.dumps(json.loads(json.dumps(_NARR_纵尸司)),
                                                  ensure_ascii=False), encoding="utf-8")
        # 事件簇.json 让 cluster_lookup 能把 ch 反查到 cluster_001
        (db / "事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 5]},
        ]}, ensure_ascii=False), encoding="utf-8")
        ch = 2
        ch_dir = root / "章节" / f"第{ch:03d}章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / f"第{ch:03d}章_changes.json").write_text(json.dumps({
            "factual": {}, "self_eval": {},
        }, ensure_ascii=False), encoding="utf-8")
        r = narrator_calibrate.calibrate(root, ch)
        # cluster_001 命中的 opening_image + catalyst 应被标 done
        assert set(r["beats_marked_done"]) == {"opening_image", "catalyst"}
        after = json.loads((db / "叙事节拍器.json").read_text(encoding="utf-8"))
        by_id = {b["id"]: b for b in after["beats"]}
        assert by_id["opening_image"]["done"] is True
        assert by_id["debate"]["done"] is False  # cluster_002 未命中本 ch → 不动


# ═══════════════════════════════ MANIFEST 消费端 ═══════════════════════════════

def test_manifest_stress_dimensions_not_high_stress_false_when_high():
    """build_manifest._collect_protagonist_stress 维度 schema：is_high_stress 不再恒 false。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        high = json.loads(json.dumps(_STRESS_诡异))
        high["stress_dimensions"]["身份暴露"]["current"] = 9  # 9/10 → norm 9 ≥ threshold*0.75
        (db / "主角压力档.json").write_text(json.dumps(high, ensure_ascii=False), encoding="utf-8")
        s = bm.DatabaseScanner(root, 3)
        r = bm._collect_protagonist_stress(s, 3)
        assert r["mode"] == "on"
        assert r["schema_mode"] == "dimensions"
        assert r["stress_level"] == 9
        assert r["is_high_stress"] is True  # 旧版恒 false（level 缺省 0）
        assert r["protagonist"] == "陆建国"  # 读 protagonist_id
        assert r["stress_dimensions"]["身份暴露"] == 9  # 维度快照透传 writer


def test_manifest_stress_scalar_still_works():
    """manifest 标量 schema 向后兼容。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps(_STRESS_v21, ensure_ascii=False), encoding="utf-8")
        s = bm.DatabaseScanner(root, 3)
        r = bm._collect_protagonist_stress(s, 3)
        assert r["schema_mode"] == "scalar"
        assert r["stress_level"] == 4
        assert len(r["persona_violations_to_avoid"]) == 1


def test_manifest_storyteller_surfaces_beats_纵尸司():
    """manifest._collect_storyteller_directive 注入 framework/beats（旧版孤儿不进 manifest）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "叙事节拍器.json").write_text(json.dumps(_NARR_纵尸司, ensure_ascii=False), encoding="utf-8")
        (db / "事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 5]},
        ]}, ensure_ascii=False), encoding="utf-8")
        s = bm.DatabaseScanner(root, 2)
        r = bm._collect_storyteller_directive(s, 2)
        assert r["mode"] == "on"
        assert r["framework"] == "Save_the_Cat"
        ids = [b["id"] for b in (r["current_cluster_beats"] or [])]
        assert "opening_image" in ids


def test_manifest_storyteller_surfaces_rhythm_诡异():
    """manifest 注入 rhythm_profile/beat_density（旧版孤儿）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "叙事节拍器.json").write_text(json.dumps(_NARR_诡异, ensure_ascii=False), encoding="utf-8")
        (db / "事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 5]},
        ]}, ensure_ascii=False), encoding="utf-8")
        s = bm.DatabaseScanner(root, 2)
        r = bm._collect_storyteller_directive(s, 2)
        assert r["rhythm_profile"] == "混合"
        assert r["current_cluster_density"] == "中（首 cluster 倒叙）"


def test_manifest_active_clocks_surfaces_segments_城南():
    """manifest._collect_active_clocks 经 clock_engine 注入维度 schema clock（旧版恒空）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "时钟表.json").write_text(json.dumps(_CLOCK_城南, ensure_ascii=False), encoding="utf-8")
        s = bm.DatabaseScanner(root, 1)
        r = bm._collect_active_clocks(s, 1)
        assert r["mode"] == "on"
        assert r["total_active"] == 2
        assert r["urgent_count"] >= 1  # clk_lao_zheng_curse remaining=1
