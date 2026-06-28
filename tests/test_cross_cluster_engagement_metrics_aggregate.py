"""CCR17：跨章 engagement metrics 聚合测试（cross_cluster_engagement_metrics_aggregate·纯函数·零 LLM）。

钉死章末钩子/Golden Three/lazy_spawn 跨章趋势检测的确定性逻辑：
- scan_hook_trend：HOOK_STRENGTH_DECLINE（连降 streak 状态机）/ HOOK_PERSISTENT_LOW（<0.4 计数）
- scan_golden_trend：GOLDEN_DEGRADATION（per-metric 连降）/ GOLDEN_FLAT（近 5 章波动 <0.1）
- scan_lazy_spawn：LAZY_SPAWN_NEVER_PROMOTED（gap>=10 且 promoted=None）+ 非 dict 项守卫
- collect_audit_scores / collect_ledger_scores：多命名 fallback + dict 解包
- load_json / get_chapters：IO + 章号解析
"""
import json
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "scripts"))
import cross_cluster_engagement_metrics_aggregate as mod  # noqa: E402


# ---------- scan_hook_trend ----------

def test_hook_trend_too_few_scores_no_findings():
    """少于 3 个分数 → 直接空（边界：len(scores) < 3）。"""
    assert mod.scan_hook_trend([]) == []
    assert mod.scan_hook_trend([(1, 0.9), (2, 0.8)]) == []


def test_hook_decline_fires_on_four_consecutive_drops():
    """4 章连降（streak 达 3）→ HOOK_STRENGTH_DECLINE warning，trail 含 4 章。"""
    scores = [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)]
    findings = mod.scan_hook_trend(scores)
    codes = [f["code"] for f in findings]
    assert "HOOK_STRENGTH_DECLINE" in codes
    decline = next(f for f in findings if f["code"] == "HOOK_STRENGTH_DECLINE")
    assert decline["severity"] == "warning"
    assert decline["trail"] == [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)]
    # 仅连降 3 步（4 章）不应误触双发
    assert codes.count("HOOK_STRENGTH_DECLINE") == 1


def test_hook_three_drops_only_no_decline():
    """只连降 2 步（3 章，streak=2 < 3）→ 不触发 DECLINE。"""
    scores = [(1, 0.9), (2, 0.8), (3, 0.7)]
    codes = [f["code"] for f in mod.scan_hook_trend(scores)]
    assert "HOOK_STRENGTH_DECLINE" not in codes


def test_hook_persistent_low_counts_below_threshold():
    """>=3 章 < 0.4 → HOOK_PERSISTENT_LOW advisory，low_chs 精确收集。"""
    scores = [(1, 0.3), (2, 0.5), (3, 0.35), (4, 0.2)]
    findings = mod.scan_hook_trend(scores)
    low = next(f for f in findings if f["code"] == "HOOK_PERSISTENT_LOW")
    assert low["severity"] == "advisory"
    assert low["low_chs"] == [1, 3, 4]  # 0.5 不计


def test_hook_persistent_low_below_count_threshold():
    """只 2 章 < 0.4（< 3）→ 不报 PERSISTENT_LOW（边界：恰好 0.4 不算 < 0.4）。"""
    scores = [(1, 0.3), (2, 0.39), (3, 0.4), (4, 0.9)]
    codes = [f["code"] for f in mod.scan_hook_trend(scores)]
    assert "HOOK_PERSISTENT_LOW" not in codes


# ---------- scan_golden_trend ----------

def test_golden_degradation_and_flat():
    """kindling 连降触 DEGRADATION；hook 近 5 章波动 <0.1 触 FLAT。"""
    scores_dict = {
        "golden_kindling": [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)],
        "golden_hook": [(1, 0.5), (2, 0.52), (3, 0.51), (4, 0.5), (5, 0.55)],
        "golden_turn": [(1, 0.5), (2, 0.6)],  # < 3 → 跳过
    }
    findings = mod.scan_golden_trend(scores_dict)
    deg = [f for f in findings if f["code"] == "GOLDEN_DEGRADATION"]
    flat = [f for f in findings if f["code"] == "GOLDEN_FLAT"]
    assert any(f["metric"] == "golden_kindling" for f in deg)
    assert any(f["metric"] == "golden_hook" for f in flat)
    # turn 太短不产任何 finding
    assert not any(f.get("metric") == "golden_turn" for f in findings)


def test_golden_flat_not_fired_when_wide_swing():
    """近 5 章波动 >=0.1 → 不报 FLAT。"""
    scores_dict = {
        "golden_kindling": [(1, 0.2), (2, 0.5), (3, 0.4), (4, 0.6), (5, 0.45)],
    }
    codes = [f["code"] for f in mod.scan_golden_trend(scores_dict)]
    assert "GOLDEN_FLAT" not in codes


def test_golden_empty_dict_safe():
    """空 dict → 无 finding 不崩。"""
    assert mod.scan_golden_trend({}) == []


# ---------- scan_lazy_spawn ----------

def _mk_pool(tmp: pathlib.Path, emerged):
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "角色池.json").write_text(
        json.dumps({"emerged": emerged}, ensure_ascii=False),
        encoding="utf-8")
    return tmp


def test_lazy_spawn_never_promoted_flagged():
    """spawn 后 >=10 章未 promoted → LAZY_SPAWN_NEVER_PROMOTED，gap 精确。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_pool(pathlib.Path(d), [
            {"id": "陈五", "spawned_at_ch": 3, "promoted_to_emerged_at_ch": None},
        ])
        findings = mod.scan_lazy_spawn(proj, chapters=[3, 10, 15])
        f = next(x for x in findings if x["code"] == "LAZY_SPAWN_NEVER_PROMOTED")
        assert f["character"] == "陈五"
        assert f["spawned_at_ch"] == 3
        assert f["current_max_ch"] == 15
        assert f["gap"] == 12
        assert f["severity"] == "advisory"


def test_lazy_spawn_gap_below_threshold_not_flagged():
    """gap < 10 → 不报（边界：恰好 9 不触发，10 触发）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_pool(pathlib.Path(d), [
            {"id": "未到期", "spawned_at_ch": 5, "promoted_to_emerged_at_ch": None},
        ])
        # max_ch=14 → gap=9 < 10
        assert mod.scan_lazy_spawn(proj, chapters=[5, 14]) == []
        # max_ch=15 → gap=10 → 触发
        assert any(x["code"] == "LAZY_SPAWN_NEVER_PROMOTED"
                   for x in mod.scan_lazy_spawn(proj, chapters=[5, 15]))


def test_lazy_spawn_promoted_not_flagged():
    """已 promoted → 不报。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_pool(pathlib.Path(d), [
            {"id": "已转正", "spawned_at_ch": 1, "promoted_to_emerged_at_ch": 4},
        ])
        assert mod.scan_lazy_spawn(proj, chapters=[1, 20]) == []


def test_lazy_spawn_string_item_guard():
    """emerged 混入字符串项（L17 修复）→ isinstance 守卫跳过不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_pool(pathlib.Path(d), [
            "仅角色名字符串",  # 非 dict，必须被跳过
            {"id": "正常", "spawned_at_ch": 2, "promoted_to_emerged_at_ch": None},
        ])
        findings = mod.scan_lazy_spawn(proj, chapters=[2, 20])
        # 不抛 AttributeError，且仍能命中 dict 项
        assert [f["character"] for f in findings] == ["正常"]


def test_lazy_spawn_missing_pool_returns_empty():
    """无 角色池.json → 返回空（早退分支）。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.scan_lazy_spawn(pathlib.Path(d), chapters=[1, 99]) == []


# ---------- collect_audit_scores ----------

def _write_audit(proj: pathlib.Path, ch: int, payload: dict):
    audit = proj / "_数据库" / ".audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / f"ch_{ch:03d}_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_collect_audit_scores_nested_and_flat():
    """hook_strength 可为标量/嵌套 dict(score)；golden_three 三项解包。"""
    with tempfile.TemporaryDirectory() as d:
        proj = pathlib.Path(d)
        _write_audit(proj, 1, {"hook_strength": 0.7,
                               "golden_three": {"kindling": 0.5,
                                                "hook": {"score": 0.6},
                                                "turn": 0.8}})
        _write_audit(proj, 2, {"scanners": {"hook_strength": {"overall": 0.4}}})
        out = mod.collect_audit_scores(proj, [1, 2])
        assert out["hook"] == [(1, 0.7), (2, 0.4)]
        assert out["golden_kindling"] == [(1, 0.5)]
        assert out["golden_hook"] == [(1, 0.6)]   # 嵌套 score 解包
        assert out["golden_turn"] == [(1, 0.8)]


def test_collect_audit_scores_missing_dir():
    """无 .audit 目录 → 全空结构不崩。"""
    with tempfile.TemporaryDirectory() as d:
        out = mod.collect_audit_scores(pathlib.Path(d), [1, 2, 3])
        assert out == {"hook": [], "golden_kindling": [],
                       "golden_hook": [], "golden_turn": []}


# ---------- collect_ledger_scores ----------

def test_collect_ledger_scores_from_records():
    """账本 ChapterRecord 取 hook_score / golden_scores → 与 audit 同结构。"""
    recs = [
        (1, {"hook_score": 0.9, "golden_scores": {"kindling": 0.5, "hook": 0.6, "turn": 0.7}}),
        (2, {"hook_score": "not_a_number", "golden_scores": {"kindling": 0.4}}),  # 字符串 hook 被忽略
        (3, {}),  # 缺字段 → 跳过
    ]
    out = mod.collect_ledger_scores(recs)
    assert out["hook"] == [(1, 0.9)]  # ch2 字符串 hook_score 被忽略
    assert out["golden_kindling"] == [(1, 0.5), (2, 0.4)]
    assert out["golden_hook"] == [(1, 0.6)]
    assert out["golden_turn"] == [(1, 0.7)]


# ---------- load_json / get_chapters ----------

def test_load_json_roundtrip_and_fallbacks():
    """存在→解析；缺失→default；坏 JSON→default。"""
    with tempfile.TemporaryDirectory() as d:
        good = pathlib.Path(d) / "g.json"
        good.write_text(json.dumps({"k": 1}), encoding="utf-8")
        assert mod.load_json(good) == {"k": 1}
        assert mod.load_json(pathlib.Path(d) / "missing.json", default="X") == "X"
        bad = pathlib.Path(d) / "b.json"
        bad.write_text("{not json", encoding="utf-8")
        assert mod.load_json(bad, default=[]) == []


def test_get_chapters_parses_and_tails():
    """章目录（glob 第*章，名以「章」结尾）→ 章号排序 + 取末 N；非章目录被滤掉。"""
    with tempfile.TemporaryDirectory() as d:
        proj = pathlib.Path(d)
        chdir = proj / "章节"
        chdir.mkdir(parents=True)
        for name in ["第1章", "第10章", "第2章", "杂项目录"]:
            (chdir / name).mkdir()
        assert mod.get_chapters(proj, last_n=10) == [1, 2, 10]
        assert mod.get_chapters(proj, last_n=2) == [2, 10]


def test_get_chapters_empty_when_no_dir():
    """无 章节 目录 → []。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.get_chapters(pathlib.Path(d), last_n=5) == []


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[cross_cluster_engagement_metrics_aggregate] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
