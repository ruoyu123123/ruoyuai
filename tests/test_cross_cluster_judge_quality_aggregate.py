"""cross_cluster_judge_quality_aggregate.py 确定性单元测试（纯逻辑·零 LLM·零网络）。

钉死 judge 评分趋势 + waiver 累计 + prev_findings 消费率三类跨章健康检测：
- 磁盘版 scan_*（tempfile 造 章节/_数据库/.judge_reports/.manifest）
- 账本版 scan_*_ledger（直接喂 (ch, ChapterRecord) 序列 · 最纯）
覆盖 happy path + 边界（空/缺字段/不足 3 章）+ 趋势/阈值/连续段分支。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import cross_cluster_judge_quality_aggregate as mod  # noqa: E402


# ---------- 小工具 ----------

def _codes(findings):
    return [f["code"] for f in findings]


def _recs(scores=None, waivers_per_ch=None, consumed_per_ch=None, chs=None):
    """造账本版 recs = [(ch, ChapterRecord), ...]。"""
    chs = chs or list(range(1, (len(scores or waivers_per_ch or consumed_per_ch or [None]) + 1)))
    out = []
    for i, ch in enumerate(chs):
        rec = {}
        if scores is not None:
            rec["judge_score"] = scores[i]
        if waivers_per_ch is not None:
            rec["waivers"] = waivers_per_ch[i]
        if consumed_per_ch is not None:
            rec["prev_findings_consumed"] = consumed_per_ch[i]
        out.append((ch, rec))
    return out


# ============================================================
# A. JUDGE_SCORE_TREND（账本版·纯函数）
# ============================================================

def test_ledger_score_decline_detected():
    """连续 ≥3 步下降 → JUDGE_SCORE_DECLINE warning。"""
    recs = _recs(scores=[9.0, 8.0, 7.0, 6.0])  # 3 次下降
    f = mod.scan_judge_scores_ledger(recs)
    assert "JUDGE_SCORE_DECLINE" in _codes(f)
    decl = next(x for x in f if x["code"] == "JUDGE_SCORE_DECLINE")
    assert decl["severity"] == "warning"
    # trail 锁住起止评分（9.0 → 6.0）
    assert decl["trail"][0][1] == 9.0 and decl["trail"][-1][1] == 6.0


def test_ledger_score_decline_needs_three_drops():
    """只跌 2 次（不连续到 3）→ 不报 DECLINE。"""
    recs = _recs(scores=[9.0, 8.0, 7.0, 8.0])  # 跌跌涨：streak 断在 2
    f = mod.scan_judge_scores_ledger(recs)
    assert "JUDGE_SCORE_DECLINE" not in _codes(f)


def test_ledger_score_plateau_grade_scale():
    """近 5 章评分波动 < _GRADE_PLATEAU_DELTA（grade 标度 0.1）→ PLATEAU advisory。"""
    # 全部 3.0±0.05，波动 0.1 内 → 触发；同时要避免 VOLATILITY（std 远 < 0.45）
    recs = _recs(scores=[3.0, 3.05, 3.0, 2.98, 3.02])
    f = mod.scan_judge_scores_ledger(recs)
    codes = _codes(f)
    assert "JUDGE_SCORE_PLATEAU" in codes
    plat = next(x for x in f if x["code"] == "JUDGE_SCORE_PLATEAU")
    assert plat["severity"] == "advisory"
    assert "JUDGE_SCORE_VOLATILITY" not in codes  # 低波动不该同时报不稳定


def test_ledger_score_volatility_grade_scale():
    """近 5 章 std > _GRADE_VOLATILITY_STD（0.45）→ VOLATILITY advisory。"""
    # 1,4,1,4,1 在 1-4 grade 标度上是高波动（std≈1.47 > 0.45）
    recs = _recs(scores=[1.0, 4.0, 1.0, 4.0, 1.0])
    f = mod.scan_judge_scores_ledger(recs)
    assert "JUDGE_SCORE_VOLATILITY" in _codes(f)
    vol = next(x for x in f if x["code"] == "JUDGE_SCORE_VOLATILITY")
    assert vol["std"] > mod._GRADE_VOLATILITY_STD


def test_ledger_score_below_three_returns_empty():
    """< 3 个有效评分 → 直接返回空（不足以判趋势）。"""
    assert mod.scan_judge_scores_ledger(_recs(scores=[5.0, 6.0])) == []
    # 评分非数字（None / 字符串）被过滤掉 → 等效不足 3 点
    recs = [(1, {"judge_score": None}), (2, {"judge_score": "x"}), (3, {})]
    assert mod.scan_judge_scores_ledger(recs) == []


# ============================================================
# B. WAIVER_ACCUMULATION（账本版·纯函数）
# ============================================================

def test_ledger_waiver_runaway():
    """单章 waiver ≥ 5 → WAIVER_RUNAWAY warning，且记录正确章号/计数。"""
    five = [{"code": f"C{i}"} for i in range(5)]
    recs = _recs(waivers_per_ch=[[], five, []], chs=[10, 11, 12])
    f = mod.scan_waiver_accumulation_ledger(recs)
    runaway = [x for x in f if x["code"] == "WAIVER_RUNAWAY"]
    assert len(runaway) == 1
    assert runaway[0]["ch"] == 11 and runaway[0]["waiver_count"] == 5
    assert runaway[0]["severity"] == "warning"


def test_ledger_waiver_persistent_code_consecutive():
    """同 code 连续 3 章被豁免 → WAIVER_PERSISTENT_CODE advisory；非连续不报。"""
    same = [{"code": "STYLE_单段超长"}]
    # ch 1,2,3 连续命中 → 触发
    recs = _recs(waivers_per_ch=[same, same, same], chs=[1, 2, 3])
    f = mod.scan_waiver_accumulation_ledger(recs)
    persist = [x for x in f if x["code"] == "WAIVER_PERSISTENT_CODE"]
    assert len(persist) == 1
    assert persist[0]["waived_code"] == "STYLE_单段超长"
    assert persist[0]["consecutive_chs"] == 3

    # 同 code 出现 3 次但跨章不连续（1,3,5）→ max_streak=1 → 不报
    recs2 = _recs(waivers_per_ch=[same, [], same, [], same], chs=[1, 2, 3, 4, 5])
    f2 = mod.scan_waiver_accumulation_ledger(recs2)
    assert "WAIVER_PERSISTENT_CODE" not in _codes(f2)


def test_ledger_waiver_empty_clean():
    """全无 waiver → 零 finding。"""
    recs = _recs(waivers_per_ch=[[], [], []], chs=[1, 2, 3])
    assert mod.scan_waiver_accumulation_ledger(recs) == []


# ============================================================
# C. PREV_FINDINGS_CONSUMPTION（账本版·纯函数）
# ============================================================

def test_ledger_prev_findings_ignored_streak():
    """连续 3 章 consumed=False → PREV_FINDINGS_IGNORED advisory。"""
    recs = _recs(consumed_per_ch=[False, False, False], chs=[1, 2, 3])
    f = mod.scan_prev_findings_consumption_ledger(recs)
    assert "PREV_FINDINGS_IGNORED" in _codes(f)
    ign = next(x for x in f if x["code"] == "PREV_FINDINGS_IGNORED")
    assert ign["consecutive_chs"] == [1, 2, 3]


def test_ledger_prev_findings_none_breaks_streak():
    """中间 None（无注入·中性）断开连续段 → 不满 3 连续 → 不报。"""
    # False, None, False, False —— None 重置后只剩 2 连续
    recs = _recs(consumed_per_ch=[False, None, False, False], chs=[1, 2, 3, 4])
    f = mod.scan_prev_findings_consumption_ledger(recs)
    assert "PREV_FINDINGS_IGNORED" not in _codes(f)


def test_ledger_prev_findings_true_resets():
    """consumed=True（消费了）重置连续段。"""
    recs = _recs(consumed_per_ch=[False, False, True, False, False], chs=[1, 2, 3, 4, 5])
    f = mod.scan_prev_findings_consumption_ledger(recs)
    assert "PREV_FINDINGS_IGNORED" not in _codes(f)


# ============================================================
# 磁盘版 + helpers（tempfile 造目录树）
# ============================================================

def _mk_project(root: Path):
    proj = root / "测试书"
    (proj / "章节").mkdir(parents=True)
    (proj / "_数据库" / ".judge_reports").mkdir(parents=True)
    return proj


def _write_chapter(proj: Path, ch: int, changes: dict):
    d = proj / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{ch:03d}章_changes.json").write_text(
        json.dumps(changes, ensure_ascii=False), encoding="utf-8")


def _write_audit_hub(proj: Path, ch: int, score):
    f = proj / "_数据库" / ".judge_reports" / f"ch_{ch:03d}_audit-hub.json"
    f.write_text(json.dumps({"score": score}, ensure_ascii=False), encoding="utf-8")


def test_get_chapters_and_load_json():
    """get_chapters 解析 第NNN章 目录名取末 N · load_json 缺文件回退 default。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        for ch in (1, 2, 3, 10):
            (proj / "章节" / f"第{ch:03d}章").mkdir(parents=True, exist_ok=True)
        assert mod.get_chapters(proj, 10) == [1, 2, 3, 10]
        assert mod.get_chapters(proj, 2) == [3, 10]  # 末 2 章
        # load_json 缺文件
        assert mod.load_json(proj / "不存在.json", default="DFLT") == "DFLT"
        # load_json 坏 JSON 回退 default
        bad = proj / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert mod.load_json(bad, default=[]) == []


def test_disk_score_decline_via_audit_hub():
    """磁盘版：连续下降的 audit-hub score → JUDGE_SCORE_DECLINE。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        for ch, sc in zip(range(1, 5), [9.0, 8.0, 7.0, 6.0]):
            (proj / "章节" / f"第{ch:03d}章").mkdir(parents=True, exist_ok=True)
            _write_audit_hub(proj, ch, sc)
        chapters = mod.get_chapters(proj, 10)
        f = mod.scan_judge_scores(proj, chapters)
        assert "JUDGE_SCORE_DECLINE" in _codes(f)


def test_disk_no_judge_dir_returns_empty():
    """无 .judge_reports 目录 → scan_judge_scores 返回空·不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "空书"
        (proj / "章节").mkdir(parents=True)
        assert mod.scan_judge_scores(proj, [1, 2, 3]) == []


def test_disk_waiver_runaway_and_persistent():
    """磁盘版：单章 5 waiver → RUNAWAY；同 code 连续 3 章 → PERSISTENT_CODE。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # ch1-3 各 1 个相同 code waiver（连续 3 章）
        for ch in (1, 2, 3):
            _write_chapter(proj, ch, {"self_eval": {"waivers": [{"code": "STYLE_单段超长"}]}})
        # ch4 单章 5 个 waiver → runaway
        _write_chapter(proj, 4, {"self_eval": {"waivers": [{"code": f"X{i}"} for i in range(5)]}})
        chapters = mod.get_chapters(proj, 10)
        f = mod.scan_waiver_accumulation(proj, chapters)
        codes = _codes(f)
        assert "WAIVER_RUNAWAY" in codes
        assert "WAIVER_PERSISTENT_CODE" in codes
        runaway = next(x for x in f if x["code"] == "WAIVER_RUNAWAY")
        assert runaway["ch"] == 4 and runaway["waiver_count"] == 5


def test_disk_read_changes_missing_returns_empty_dict():
    """read_changes 缺文件 → 返回 {}（default），不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        assert mod.read_changes(proj, 999) == {}


# ---------- 独立 runner（与既有范例一致） ----------

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
    print(f"[cross_cluster_judge_quality_aggregate] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
