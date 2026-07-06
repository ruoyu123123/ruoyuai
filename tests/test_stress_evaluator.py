"""stress_evaluator.py 专属回归测试 — 锁尚未被间接覆盖的核心确定性逻辑。

已有间接覆盖（不重复）：
  · test_engine_schema_compat.py — stress_view（扁平/嵌套维度·非默认 dmax 归一·v21 标量）+
    evaluate（维度 no-op / 标量 keyword 累加）
  · test_self_eval_orphan_consumption.py — evaluate_stress_delta_from_self_eval 全分支 +
    evaluate（writer 申报优先 / keyword 回退 / writer 自估触发 mental_break）

本文件聚焦尚未覆盖的：
  · _dim_value / _dim_max / _dim_threshold 三个底层 accessor
  · draw_mental_break_card —— weight 加权抽 / trigger_min_stress 过滤 / 空池 None
  · evaluate_stress_delta（正文关键词扫描）—— 单 trait cap 3 / align relief / violation 优先 / 空
  · apply_card_to_locked_facts —— 事件表.json 写记录
  · read_chapter_text / read_changes —— 缺文件兜底
  · stress_view 的 breakdown_threshold 分支（80→8 百分量纲归一）+ 空 dims 落回 scalar
  · evaluate 的 error / skip 路径
  · main() 真 CLI 退出码（0 健康 / 1 高 stress / 2 mental_break / SKIP 缺档 exit 0）

零依赖约定：仅标准库 · test_* 无参 · tempfile.mkdtemp · encoding=utf-8 · Windows。
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
import stress_evaluator as se  # noqa: E402

_INTERNAL_ENV_NAME = "RUOYUAI_CLUSTER_STATE_INTERNAL"


def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_chapter_txt(project: Path, ch: int, body: str) -> None:
    ch_dir = project / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / f"第{ch:03d}章.txt").write_text(body, encoding="utf-8")


# ═══════════════════════ 底层维度 accessor（_dim_value/_dim_max/_dim_threshold） ═══════════════════════

def test_dim_value_handles_all_shapes():
    """_dim_value：嵌套 dict 取 current / 扁平 int / float 截断 / 其它落 0 / current 缺省 0。"""
    assert se._dim_value({"current": 7, "max": 10}) == 7
    assert se._dim_value(30) == 30
    assert se._dim_value(3.9) == 3        # int() 截断不四舍五入
    assert se._dim_value("abc") == 0      # 非数值非 dict → 0
    assert se._dim_value({"max": 10}) == 0  # dict 无 current → 0
    assert se._dim_value({"current": 0}) == 0


def test_dim_max_default_and_nested():
    """_dim_max：嵌套 dict 有 max 用 max，否则用传入 default；扁平值一律 default。"""
    assert se._dim_max({"current": 5, "max": 20}, 10) == 20
    assert se._dim_max({"current": 5}, 10) == 10   # dict 无 max → default
    assert se._dim_max(99, 10) == 10               # 扁平 int → default
    assert se._dim_max({"max": 0}, 10) == 10       # max=0 falsy → default 兜底


def test_dim_threshold_only_from_nested():
    """_dim_threshold：仅嵌套 dict 的 trigger_threshold，扁平/无键 → None。"""
    assert se._dim_threshold({"current": 1, "trigger_threshold": 8}) == 8
    assert se._dim_threshold({"current": 1}) is None
    assert se._dim_threshold(5) is None


# ═══════════════════════ stress_view 未覆盖分支 ═══════════════════════

def test_stress_view_breakdown_threshold_percent_normalized():
    """breakdown_threshold=80（百分量纲 >10）→ 归一成 8（round(80/100*10)）。
    无逐维度 trigger_threshold 时走 breakdown_threshold 分支。"""
    stress = {
        "stress_dimensions": {"guilt": 0, "fear": 0},
        "breakdown_threshold": 80,
    }
    v = se.stress_view(stress)
    assert v["mode"] == "dimensions"
    assert v["stress_threshold_break"] == 8  # 80 百分量纲 → 8


def test_stress_view_breakdown_threshold_small_value_kept():
    """breakdown_threshold<=10 视作已是 0..10 量纲，直接用（不再 /100）。"""
    stress = {
        "stress_dimensions": {"guilt": 0, "fear": 0},
        "breakdown_threshold": 7,
    }
    v = se.stress_view(stress)
    assert v["stress_threshold_break"] == 7


def test_stress_view_empty_dims_falls_back_to_scalar():
    """stress_dimensions 为空 dict（falsy）→ 不进维度分支 → 落回 scalar 默认值。"""
    v = se.stress_view({"stress_dimensions": {}})
    assert v["mode"] == "scalar"
    assert v["stress_level"] == 0
    assert v["stress_max"] == 10
    assert v["stress_threshold_break"] == 8
    assert v["traits"] == []


def test_stress_view_dims_ignore_underscore_keys():
    """stress_dimensions 里 _ 前缀键（如 _meta/_doc）不计入维度。"""
    stress = {
        "stress_dimensions": {"_doc": "说明", "fear": {"current": 9, "max": 10}},
    }
    v = se.stress_view(stress)
    assert v["_dim_keys"] == ["fear"]   # _doc 被跳过
    assert v["stress_level"] == 9


# ═══════════════════════ evaluate_stress_delta（正文关键词扫描） ═══════════════════════

_TRAITS = [
    {"trait": "护短", "violation_keywords": ["背叛", "见死不救"],
     "align_keywords": ["保护"], "stress_per_violation": 2},
]


def test_keyword_scan_violation_cap_3_per_trait():
    """单 trait 单章命中 cap 3：背叛×5 → delta = 2 × min(5,3) = 6。"""
    r = se.evaluate_stress_delta("背叛背叛背叛背叛背叛", _TRAITS)
    assert r["delta"] == 6
    assert r["violations"][0]["trait"] == "护短"
    assert r["violations"][0]["hits"] == 5
    assert r["violations"][0]["stress_added"] == 6


def test_keyword_scan_multi_keyword_summed_then_capped():
    """同 trait 多关键词 hits 求和：背叛×1 + 见死不救×1 = 2 → delta 2×min(2,3)=4。"""
    r = se.evaluate_stress_delta("他背叛了，又见死不救。", _TRAITS)
    assert r["violations"][0]["hits"] == 2
    assert r["delta"] == 4


def test_keyword_scan_align_relief_needs_two():
    """≥2 次 align（且无 violation）→ relief -1；单次 align → 不给 relief。"""
    r2 = se.evaluate_stress_delta("他保护了，再次保护。", _TRAITS)
    assert r2["delta"] == -1
    assert r2["alignments"][0]["hits"] == 2
    r1 = se.evaluate_stress_delta("他保护了一次。", _TRAITS)
    assert r1["delta"] == 0
    assert r1["alignments"] == []


def test_keyword_scan_violation_takes_priority_over_align():
    """同 trait 同时有 violation 与 align：violation 优先（elif → align 不参与）。"""
    r = se.evaluate_stress_delta("他背叛了，但也保护了，还保护了。", _TRAITS)
    assert r["delta"] == 2          # 仅 violation 计：2 × min(1,3)
    assert len(r["violations"]) == 1
    assert r["alignments"] == []   # elif 短路，align 不计


def test_keyword_scan_no_hits_zero_delta():
    """无任何命中 → delta 0 / 空 violations/alignments。"""
    r = se.evaluate_stress_delta("平静的一天，什么都没发生。", _TRAITS)
    assert r["delta"] == 0
    assert r["violations"] == []
    assert r["alignments"] == []


def test_keyword_scan_empty_traits():
    """无 traits（维度 schema）→ 文本扫描恒 0。"""
    r = se.evaluate_stress_delta("背叛背叛背叛", [])
    assert r["delta"] == 0


# ═══════════════════════ draw_mental_break_card（加权随机抽卡） ═══════════════════════

def test_draw_card_empty_pool_returns_none():
    """空池 → None。"""
    assert se.draw_mental_break_card([], 10) is None


def test_draw_card_filters_by_trigger_min_stress():
    """stress 未达 trigger_min_stress 的卡被过滤；都不达 → None。"""
    pool = [{"card_id": "A", "trigger_min_stress": 9, "weight": 1}]
    assert se.draw_mental_break_card(pool, 5) is None  # 5 < 9
    got = se.draw_mental_break_card(pool, 9)           # 9 >= 9 边界含等于
    assert got is not None and got["card_id"] == "A"


def test_draw_card_default_trigger_min_stress_8():
    """卡无 trigger_min_stress → 默认 8；stress=7 不够，8 够。"""
    pool = [{"card_id": "X", "weight": 1}]
    assert se.draw_mental_break_card(pool, 7) is None
    assert se.draw_mental_break_card(pool, 8)["card_id"] == "X"


def test_draw_card_zero_weight_card_never_drawn():
    """weight=0 的卡（即便 eligible）权重 0 → 与正权重卡共存时永不抽中。"""
    pool = [
        {"card_id": "never", "trigger_min_stress": 8, "weight": 0},
        {"card_id": "always", "trigger_min_stress": 8, "weight": 5},
    ]
    drawn = {se.draw_mental_break_card(pool, 10)["card_id"] for _ in range(60)}
    assert drawn == {"always"}  # weight 0 卡永不出现


# ═══════════════════════ apply_card_to_locked_facts（事件表写记录） ═══════════════════════

def test_apply_card_writes_event_record():
    """抽到的卡写入 事件表.json.events，含 type/protagonist/permanent_persona_changes。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root)
        card = {"card_id": "MB_paranoid", "label": "草木皆兵",
                "permanent_persona_changes": ["从此多疑"], "narrative_effect": "后续都不信人"}
        se.apply_card_to_locked_facts(root, 12, card, "林七")
        events = json.loads((root / "_数据库" / "事件表.json").read_text(encoding="utf-8"))
        ev = events["events"][-1]
        assert ev["type"] == "mental_break_triggered"
        assert ev["ch"] == 12
        assert ev["protagonist"] == "林七"
        assert ev["card_id"] == "MB_paranoid"
        assert ev["permanent_persona_changes"] == ["从此多疑"]
        assert ev["id"] == "MB_event_12_MB_paranoid"


def test_apply_card_appends_to_existing_events():
    """已有 events 时 append 不覆盖。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "事件表.json").write_text(
            json.dumps({"events": [{"id": "preexisting"}]}, ensure_ascii=False), encoding="utf-8")
        se.apply_card_to_locked_facts(root, 3, {"card_id": "C", "label": "L"}, "主角")
        events = json.loads((db / "事件表.json").read_text(encoding="utf-8"))
        assert len(events["events"]) == 2
        assert events["events"][0]["id"] == "preexisting"


# ═══════════════════════ read_chapter_text / read_changes 兜底 ═══════════════════════

def test_read_chapter_text_missing_returns_empty():
    """章节正文缺失 → 空串（不抛异常）。"""
    with tempfile.TemporaryDirectory() as d:
        assert se.read_chapter_text(Path(d), 9) == ""


def test_read_chapter_text_present():
    """正文存在 → 原样读出。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter_txt(root, 7, "第七章正文内容。")
        assert se.read_chapter_text(root, 7) == "第七章正文内容。"


def test_read_changes_missing_returns_skeleton():
    """_changes.json 缺失 → 空骨架 {factual, self_eval}（下游可安全 .get）。"""
    with tempfile.TemporaryDirectory() as d:
        r = se.read_changes(Path(d), 4)
        assert r == {"factual": {}, "self_eval": {}}


# ═══════════════════════ evaluate error / skip 路径 ═══════════════════════

def test_evaluate_missing_stress_file_returns_error():
    """主角压力档.json 不存在 → {error}。"""
    with tempfile.TemporaryDirectory() as d:
        r = se.evaluate(Path(d), 1)
        assert r == {"error": "主角压力档.json 不存在"}


def test_evaluate_no_chapter_text_skips():
    """压力档在但本章无正文 → skipped（不改档、不抽卡）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps({
            "stress_level": 0, "stress_max": 10, "stress_threshold_break": 8,
        }, ensure_ascii=False), encoding="utf-8")
        r = se.evaluate(root, 99)
        assert r.get("skipped") == "本章无正文"


def test_evaluate_scalar_clamps_at_max():
    """标量 delta 推过 stress_max 被钳到 max（max(0,min(max,old+delta)))。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps({
            "protagonist": "林七", "stress_level": 9, "stress_max": 10,
            "stress_threshold_break": 8, "stress_log": [],
            "persona_violations_tracked": {"core_traits": _TRAITS},
            # 无 mental_break_pool → 不抽卡，留 new 在高位看钳制
        }, ensure_ascii=False), encoding="utf-8")
        # 背叛×3 → delta 2×3=6 → 9+6=15 钳到 10
        _write_chapter_txt(root, 2, "背叛背叛背叛")
        r = se.evaluate(root, 2)
        assert r["schema_mode"] == "scalar"
        assert r["stress_delta"] == 6
        assert r["stress_new"] == 10  # 钳到 stress_max


# ═══════════════════════ main() 真 CLI 退出码 ═══════════════════════

def _run_cli(project: Path, ch: int):
    """跑真 CLI，返回 CompletedProcess（参照 test_cross_cluster_fate_drift_aggregate 子进程范式）。

    注：Windows 控制台默认 GBK，child print(json) 走控制台编码 → 父进程 utf-8 解码会撞非法字节。
    强制 PYTHONIOENCODING=utf-8 让 child stdout 以 utf-8 落字节，再容错解码确保 json.loads 干净。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **{_INTERNAL_ENV_NAME: "1"})
    p = subprocess.run(
        [sys.executable, str(_SCRIPTS / "stress_evaluator.py"), str(project), "--ch", str(ch)],
        capture_output=True, cwd=str(_ROOT), env=env,
    )
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def test_main_skip_when_no_stress_file_exit_0():
    """无压力档 → [SKIP] 且退出码 0（项目未启用 Stress 系统不算失败）。"""
    with tempfile.TemporaryDirectory() as d:
        cp = _run_cli(Path(d), 1)
        assert cp.returncode == 0
        assert "SKIP" in cp.stdout


def test_main_healthy_exit_0():
    """低 stress 无告警 → 退出码 0。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps({
            "protagonist": "林七", "stress_level": 0, "stress_max": 10,
            "stress_threshold_break": 8, "stress_log": [],
            "persona_violations_tracked": {"core_traits": _TRAITS},
        }, ensure_ascii=False), encoding="utf-8")
        _write_chapter_txt(root, 1, "平静的开篇，主角喝了杯茶。")
        cp = _run_cli(root, 1)
        assert cp.returncode == 0
        out = json.loads(cp.stdout)
        assert out["mental_break_triggered"] is False
        assert out["high_stress_warning"] is False


def test_main_high_stress_warning_exit_1():
    """stress 推到 ≥threshold*0.75 但未抽卡（无 mental_break_pool）→ high_stress_warning 退出码 1。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        # threshold 8 → 0.75*8=6；起 4，背叛×1=+2 → 6 命中告警线，无 pool 不抽卡
        (db / "主角压力档.json").write_text(json.dumps({
            "protagonist": "林七", "stress_level": 4, "stress_max": 10,
            "stress_threshold_break": 8, "stress_log": [],
            "persona_violations_tracked": {"core_traits": _TRAITS},
        }, ensure_ascii=False), encoding="utf-8")
        _write_chapter_txt(root, 1, "他背叛了搭档。")
        cp = _run_cli(root, 1)
        out = json.loads(cp.stdout)
        assert out["stress_new"] == 6
        assert out["high_stress_warning"] is True
        assert out["mental_break_triggered"] is False
        assert cp.returncode == 1


def test_main_mental_break_exit_2():
    """stress 越阈值且有 mental_break_pool → 抽卡触发 → 退出码 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _mk_db(root)
        (db / "主角压力档.json").write_text(json.dumps({
            "protagonist": "林七", "stress_level": 6, "stress_max": 10,
            "stress_threshold_break": 8, "stress_log": [],
            "persona_violations_tracked": {"core_traits": _TRAITS},
            "mental_break_pool": [
                {"card_id": "MB_collapse", "label": "崩溃", "weight": 1,
                 "trigger_min_stress": 8, "permanent_persona_changes": ["多疑"],
                 "narrative_effect": "..."},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        # 背叛×1 → +2 → 6+2=8 ≥ threshold 8 → 抽卡
        _write_chapter_txt(root, 1, "他背叛了。")
        cp = _run_cli(root, 1)
        out = json.loads(cp.stdout)
        assert out["mental_break_triggered"] is True
        assert out["card"]["id"] == "MB_collapse"
        assert cp.returncode == 2
        # 标量模式 reset → stress_level 归 0
        after = json.loads((db / "主角压力档.json").read_text(encoding="utf-8"))
        assert after["stress_level"] == 0
        # 卡写入事件表
        events = json.loads((db / "事件表.json").read_text(encoding="utf-8"))
        assert any(e.get("type") == "mental_break_triggered" for e in events["events"])
