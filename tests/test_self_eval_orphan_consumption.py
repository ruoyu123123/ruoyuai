"""writer 自评孤儿字段消费回归测试 — [#4 stress_evaluation_self · #5 mckee_truby_alignment]。

两个孤儿字段同型（对齐已修的 #7 storyteller_alignment 范式）：writer 在 _changes.json 的
self_eval 里**主动申报**了，但全仓零脚本消费 → 系统改用启发式（关键词/预设排期）重推，丢弃 writer
第一手判断（违反北极星⑤：作者/写作端申报是第一权威，系统是顾问非法官）。

[#4] stress_evaluator 优先消费 self_eval.stress_evaluation_self.{violations_made/alignments_made/
     estimated_stress_change} 算 stress delta，未申报才回退正文 violation/align 关键词扫描。
     · 下游链路重（delta→满阈值→抽 mental_break→permanent persona→locked_facts），关键词可靠性
       远低于 writer 申报。
     · writer 显式 estimated_stress_change 时以其为准。

[#5] cross_cluster_arc_progression_aggregate 旁路核对 writer 申报的 mckee_truby 节拍 vs 预设
     Save-the-Cat stage 排期是否背离，产 BEAT_STAGE_DIVERGENCE **advisory**（不硬锁/不倒退 stage ·
     北极星③软牵引）。预设缺失 / writer 未申报 → 自然不产 finding。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import stress_evaluator as se  # noqa: E402
import cross_cluster_arc_progression_aggregate as arc  # noqa: E402


# ═══════════════════════ 公共脚手架 ═══════════════════════

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_chapter(project: Path, ch: int, body: str = "", changes: dict | None = None) -> None:
    ch_dir = project / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    if body:
        (ch_dir / f"第{ch:03d}章.txt").write_text(body, encoding="utf-8")
    if changes is not None:
        (ch_dir / f"第{ch:03d}章_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")


_STRESS_v21 = {  # 标量 schema（mental_break 链路所在）
    "protagonist": "林七",
    "stress_level": 0,
    "stress_max": 10,
    "stress_threshold_break": 8,
    "persona_violations_tracked": {"core_traits": [
        {"trait": "护短", "violation_keywords": ["背叛"], "align_keywords": ["保护"],
         "stress_per_violation": 2},
    ]},
    "stress_log": [],
}


# ═══════════════════════ #4 stress_evaluation_self 纯函数层 ═══════════════════════

def test_self_eval_delta_violations_only():
    """只申报 violations_made → +per×min(条数,3)（per=trait stress_per_violation=2）。"""
    traits = _STRESS_v21["persona_violations_tracked"]["core_traits"]
    r = se.evaluate_stress_delta_from_self_eval(
        {"violations_made": ["背叛了搭档", "对师父撒谎"]}, traits)
    assert r is not None
    assert r["source"] == "writer_declared"
    assert r["delta"] == 4  # 2 条 × per 2
    assert r["violations"][0]["count"] == 2


def test_self_eval_delta_violations_capped_at_3():
    """violations_made > 3 条 → 单章 cap 3（与关键词模式 min(hits,3) 对齐）。"""
    traits = _STRESS_v21["persona_violations_tracked"]["core_traits"]
    r = se.evaluate_stress_delta_from_self_eval(
        {"violations_made": ["a", "b", "c", "d", "e"]}, traits)
    assert r["delta"] == 6  # min(5,3)=3 × per 2


def test_self_eval_delta_alignments_relief():
    """≥2 条 alignments_made → -1 relief（与关键词模式同语义）。"""
    traits = _STRESS_v21["persona_violations_tracked"]["core_traits"]
    r = se.evaluate_stress_delta_from_self_eval(
        {"alignments_made": ["保护了弱者", "守住了承诺"]}, traits)
    assert r["delta"] == -1
    assert r["alignments"][0]["count"] == 2


def test_self_eval_explicit_estimate_overrides_count():
    """writer 显式 estimated_stress_change → 以 writer 自估为准（覆盖计数 · 北极星⑤）。"""
    traits = _STRESS_v21["persona_violations_tracked"]["core_traits"]
    r = se.evaluate_stress_delta_from_self_eval(
        {"violations_made": ["背叛"], "estimated_stress_change": "+7"}, traits)
    assert r["delta"] == 7  # 不是 2（per×1），以 writer 自估 +7 为准


def test_self_eval_negative_estimate_parsed():
    """estimated_stress_change 负数也能解析。"""
    r = se.evaluate_stress_delta_from_self_eval({"estimated_stress_change": "-3"}, [])
    assert r["delta"] == -3


def test_self_eval_no_traits_default_per_2():
    """无 traits（维度 schema）时 per 默认 2。"""
    r = se.evaluate_stress_delta_from_self_eval({"violations_made": ["x"]}, [])
    assert r["delta"] == 2


def test_self_eval_empty_returns_none():
    """writer 未申报任何信号 → None（调用方回退关键词扫描）。"""
    assert se.evaluate_stress_delta_from_self_eval({}, []) is None
    assert se.evaluate_stress_delta_from_self_eval(
        {"violations_made": [], "alignments_made": [], "coping_behaviors_used": ["深呼吸"]}, []) is None
    assert se.evaluate_stress_delta_from_self_eval(None, []) is None


# ═══════════════════════ #4 evaluate() 集成 ═══════════════════════

def test_evaluate_prefers_writer_declared_over_keywords():
    """writer 申报存在 → evaluate 用申报 delta（不被正文关键词覆盖），标 delta_source=writer_declared。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(
            json.dumps(json.loads(json.dumps(_STRESS_v21)), ensure_ascii=False), encoding="utf-8")
        # 正文里「背叛」关键词命中 2 次（关键词模式会算 +4）；但 writer 申报只 1 条 violation（+2）
        _write_chapter(root, 2,
                       body="他背叛了，又一次背叛。",
                       changes={"factual": {}, "self_eval": {"stress_evaluation_self": {
                           "violations_made": ["对兄弟见死不救"]}}})
        r = se.evaluate(root, 2)
        assert r["delta_source"] == "writer_declared"
        assert r["stress_delta"] == 2  # writer 申报 1 条 × per 2 —— 不是关键词的 +4
        assert r["stress_new"] == 2


def test_evaluate_falls_back_to_keywords_when_no_declaration():
    """writer 未申报 stress_evaluation_self → 回退正文关键词扫描（向后兼容不破坏）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(
            json.dumps(json.loads(json.dumps(_STRESS_v21)), ensure_ascii=False), encoding="utf-8")
        # 无 _changes.json（或无 self_eval）→ 回退关键词：「背叛」命中 2 → +4
        _write_chapter(root, 3, body="他背叛了，再次背叛。")
        r = se.evaluate(root, 3)
        assert r["delta_source"] == "keyword_scan"
        assert r["stress_delta"] == 4


def test_evaluate_writer_estimate_triggers_mental_break():
    """writer 自估高 stress 推到阈值 → 抽 mental_break（下游链路用 writer 申报驱动 · 全程闭环）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        st = json.loads(json.dumps(_STRESS_v21))
        st["mental_break_pool"] = [
            {"card_id": "MB_breakdown", "label": "崩溃", "weight": 1, "trigger_min_stress": 8,
             "permanent_persona_changes": ["从此多疑"], "narrative_effect": "..."},
        ]
        (db / "主角压力档.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        _write_chapter(root, 5, body="正文。",
                       changes={"factual": {}, "self_eval": {"stress_evaluation_self": {
                           "estimated_stress_change": "+9"}}})
        r = se.evaluate(root, 5)
        assert r["delta_source"] == "writer_declared"
        assert r["mental_break_triggered"] is True
        # mental_break 把卡写入事件表.json（locked_facts 链路）
        events = json.loads((db / "事件表.json").read_text(encoding="utf-8"))
        assert any(e.get("type") == "mental_break_triggered" for e in events["events"])


# ═══════════════════════ #5 mckee_truby_alignment 纯函数层 ═══════════════════════

def test_stage_at_ch_step_function():
    """_stage_at_ch 返回某章生效中的预设 stage（step 函数 · 取最后一个 sch<=ch）。"""
    stages = [(1, "lie"), (8, "lie_cracking"), (14, "want_threatened")]
    assert arc._stage_at_ch(stages, 5) == "lie"
    assert arc._stage_at_ch(stages, 8) == "lie_cracking"
    assert arc._stage_at_ch(stages, 100) == "want_threatened"
    assert arc._stage_at_ch(stages, 0) is None  # ch 早于首个 stage


def test_read_declared_mckee_beats():
    """_read_declared_mckee_beats 从各章 _changes.json 取 self_eval.mckee_truby_alignment。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, changes={"factual": {}, "self_eval": {}})  # 无 mckee → 不收
        _write_chapter(root, 3, changes={"factual": {}, "self_eval": {"mckee_truby_alignment": {
            "need_glimpsed_this_ch": "窥见自己其实想被记住", "moral_argument_advanced": True}}})
        out = arc._read_declared_mckee_beats(root, 3)
        assert 1 not in out
        assert 3 in out
        assert out[3]["moral_argument_advanced"] is True


# ═══════════════════════ #5 main() 集成（advisory · 不硬锁） ═══════════════════════

def _mk_arc_project(tmp: Path, stages_by_chapter: dict) -> Path:
    db = _mk_db(tmp)
    (db / "character_arc_state.json").write_text(json.dumps({
        "characters": {"主角": {
            "stages_by_chapter": stages_by_chapter,
            "current_stage_at_ch": "5:lie", "_last_updated_at_ch": 5,
        }},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp


def _run_arc_main(root: Path) -> dict:
    """跑 arc aggregate main()，捕 SystemExit，返回最新报告 JSON。"""
    argv_bak, env_bak = sys.argv[:], os.environ.get("CLUSTER_MODE")
    os.environ.pop("CLUSTER_MODE", None)  # 走磁盘逐章分支（非 cluster 模式）
    sys.argv = ["arc", str(root)]
    try:
        try:
            arc.main()
        except SystemExit:
            pass
    finally:
        sys.argv = argv_bak
        if env_bak is not None:
            os.environ["CLUSTER_MODE"] = env_bak
    reports = sorted((root / "_数据库" / ".cross_chapter_scan").glob("arc_progression_*.json"))
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def test_beat_stage_divergence_emitted_advisory():
    """writer ch3 申报 need_glimpsed + moral_advanced，但预设此章仍 lie → BEAT_STAGE_DIVERGENCE advisory。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 预设排期：ch1-50 都还在 lie/lie_cracking（早期），主角弧
        _mk_arc_project(root, {"1": "lie", "5": "lie", "50": "lie_cracking"})
        _write_chapter(root, 3, body="x", changes={"factual": {}, "self_eval": {
            "mckee_truby_alignment": {
                "need_glimpsed_this_ch": "他第一次意识到自己要的不是钱",
                "moral_argument_advanced": True,
                "desire_pursued_this_ch": "保住工作"}}})
        report = _run_arc_main(root)
        divs = [f for f in report["findings"] if f["code"] == "BEAT_STAGE_DIVERGENCE"]
        assert len(divs) == 1
        assert divs[0]["severity"] == "advisory"  # 绝不 warning/hard_gate
        assert divs[0]["ch"] == 3
        assert divs[0]["preset_stage"] == "lie"


def test_beat_stage_no_divergence_when_aligned():
    """writer 申报 need_glimpsed 时预设已到 midpoint_revelation（深层节拍区）→ 不背离，无 finding。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_arc_project(root, {"1": "lie", "50": "midpoint_revelation", "60": "all_is_lost"})
        _write_chapter(root, 55, body="x", changes={"factual": {}, "self_eval": {
            "mckee_truby_alignment": {
                "need_glimpsed_this_ch": "彻底看清", "moral_argument_advanced": True}}})
        report = _run_arc_main(root)
        divs = [f for f in report["findings"] if f["code"] == "BEAT_STAGE_DIVERGENCE"]
        assert len(divs) == 0  # 预设 midpoint_revelation 已是深层区 → 不背离


def test_beat_stage_no_divergence_when_only_desire():
    """writer 只申报 desire_pursued（表层 want，早期正常）→ 不产背离 finding。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_arc_project(root, {"1": "lie", "5": "lie", "50": "lie_cracking"})
        _write_chapter(root, 3, body="x", changes={"factual": {}, "self_eval": {
            "mckee_truby_alignment": {"desire_pursued_this_ch": "保住工作",
                                      "need_glimpsed_this_ch": "", "moral_argument_advanced": False}}})
        report = _run_arc_main(root)
        divs = [f for f in report["findings"] if f["code"] == "BEAT_STAGE_DIVERGENCE"]
        assert len(divs) == 0


def test_beat_stage_no_finding_when_no_declaration():
    """writer 未申报 mckee → 自然不产背离 finding（不破坏既有 STAGE_* 检测）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_arc_project(root, {"1": "lie", "5": "lie", "50": "lie_cracking"})
        _write_chapter(root, 3, body="x", changes={"factual": {}, "self_eval": {}})
        report = _run_arc_main(root)
        divs = [f for f in report["findings"] if f["code"] == "BEAT_STAGE_DIVERGENCE"]
        assert len(divs) == 0
