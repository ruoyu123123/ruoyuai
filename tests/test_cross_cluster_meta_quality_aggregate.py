#!/usr/bin/env python3
"""cross_cluster_meta_quality_aggregate.py 确定性回归测试（零 LLM / 零联网）。

专属回归网，聚焦尚未被既有间接覆盖（tests/test_meta_quality_keyword_dim.py 只测
scan_summary_consistency_from_ledger 的 SUMMARY_KEYWORD_MISMATCH 量纲对齐）锁住的核心逻辑：

- A. scan_length_distribution：LENGTH_OUTLIER（±50% 中位数 + median>1000 闸）/
     LENGTH_TREND_DROP（连续 4 章单调降）/ LENGTH_VARIANCE_HIGH（cv>0.4 且 ≥5 章）/
     <3 章短路返回空。
- B. _flatten_v2_chapter_summary：v2 clusters[].chapters[].summary 摊平成 {ch: summary}，
     非 dict / 空 summary / clusters 非 list 防御。
- B. scan_summary_consistency（磁盘版）：SUMMARY_TOO_SHORT（<50 CJK）/ 缺摘要文件返回空。
- B. scan_summary_consistency_from_ledger：SUMMARY_TOO_SHORT 分支（与 keyword 维度正交）。
- C. scan_lessons_feedback：FAILURE_RECURRING（≥2 后续章复发）/ SUCCESS_NEVER_REUSED
     （后续 ≥5 章零复用）/ 缺经验文件返回空。
- 工具：load_json（缺失/坏 JSON 回退 default）/ get_chapters（章号排序+取末 N）。
- main CLI（含 sys.exit）：走 subprocess 跑真 CLI，断言退出码（warning→2 / 健康→0 /
     无章节 SKIP→0 且不落报告目录）+ 报告 JSON summary。全程不 mock 被测逻辑。

所有 test_* 无参数，断言失败 raise AssertionError。零第三方依赖（仅标准库）。
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
import cross_cluster_meta_quality_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_meta_quality_aggregate.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目 / 章目录 / 经验 / 摘要 / 跑真 CLI
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_chapter(proj: Path, ch: int, text: str) -> None:
    cd = proj / "章节" / f"第{ch:03d}章"
    cd.mkdir(parents=True, exist_ok=True)
    (cd / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")


def _write_lessons(proj: Path, failure=None, success=None) -> None:
    (proj / "_数据库" / "写作经验.json").write_text(
        json.dumps({"failure_patterns": failure or [],
                    "success_patterns": success or []}, ensure_ascii=False),
        encoding="utf-8")


def _write_summary(proj: Path, data: dict) -> None:
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _codes(findings):
    return [f["code"] for f in findings]


def _run_cli(proj: Path, *args):
    """跑真 CLI（显式剔除 CLUSTER_MODE → 走逐章磁盘路径，确定性）。

    stdout 走 Windows 控制台编码，用 errors='replace' 容错；断言只锚 returncode +
    报告文件（显式 utf-8 落盘的权威确定性输出）。
    """
    env = dict(os.environ)
    env.pop("CLUSTER_MODE", None)
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), *args],
        capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def _latest_report(proj: Path) -> dict:
    out_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(out_dir.glob("meta_quality_*.json"))
    assert reports, "未生成 meta_quality 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# A. scan_length_distribution —— 纯函数（lengths 直接喂，绕开磁盘读正文）
# ══════════════════════════════════════════════════════════════════════════
def test_length_outlier_detects_offending_chapter():
    """中位数 2100，ch4=500 偏离 >50% 且 median>1000 → 仅 ch4 命中 LENGTH_OUTLIER。"""
    lengths = [(1, 2000), (2, 2100), (3, 2200), (4, 500)]
    findings = mod.scan_length_distribution(None, [1, 2, 3, 4], lengths=lengths)
    outliers = [f for f in findings if f["code"] == "LENGTH_OUTLIER"]
    assert len(outliers) == 1, f"应只 1 个 outlier，实得 {_codes(findings)}"
    assert outliers[0]["ch"] == 4
    assert outliers[0]["median"] == 2100
    # diff_pct = (500-2100)/2100 ≈ -0.76
    assert outliers[0]["diff_pct"] < -0.5


def test_length_outlier_suppressed_when_median_small():
    """median ≤ 1000 时 outlier 闸关闭（短文不判字数控制不稳）→ 即便偏离也不报。"""
    # 中位数 = sorted=[100,500,900,1000][idx2]=900；900 不 >1000 → 全部不触发
    lengths = [(1, 100), (2, 500), (3, 900), (4, 1000)]
    findings = mod.scan_length_distribution(None, [1, 2, 3, 4], lengths=lengths)
    assert not [f for f in findings if f["code"] == "LENGTH_OUTLIER"], \
        f"median≤1000 不该触发 LENGTH_OUTLIER，实得 {_codes(findings)}"


def test_length_trend_drop_on_four_monotonic_decline():
    """连续 4 章字数单调下降 → LENGTH_TREND_DROP，consecutive_chs 为这 4 章。"""
    lengths = [(1, 3000), (2, 2500), (3, 2000), (4, 1500)]
    findings = mod.scan_length_distribution(None, [1, 2, 3, 4], lengths=lengths)
    drops = [f for f in findings if f["code"] == "LENGTH_TREND_DROP"]
    assert len(drops) == 1, f"应触发 1 次 TREND_DROP，实得 {_codes(findings)}"
    assert drops[0]["consecutive_chs"] == [1, 2, 3, 4]
    assert drops[0]["trail"] == [3000, 2500, 2000, 1500]


def test_length_variance_high_on_alternating_lengths():
    """cv>0.4 且 ≥5 章（交替 1000/4000）→ LENGTH_VARIANCE_HIGH。"""
    lengths = [(1, 1000), (2, 4000), (3, 1000), (4, 4000), (5, 1000)]
    findings = mod.scan_length_distribution(None, [1, 2, 3, 4, 5], lengths=lengths)
    var = [f for f in findings if f["code"] == "LENGTH_VARIANCE_HIGH"]
    assert len(var) == 1, f"应触发 VARIANCE_HIGH，实得 {_codes(findings)}"
    assert var[0]["cv"] > 0.4


def test_length_distribution_short_input_returns_empty():
    """< 3 个数据点 → 直接返回空（样本不足不下判断）。"""
    assert mod.scan_length_distribution(None, [1, 2], lengths=[(1, 100), (2, 200)]) == []


# ══════════════════════════════════════════════════════════════════════════
# B. _flatten_v2_chapter_summary —— v2 schema 摊平
# ══════════════════════════════════════════════════════════════════════════
def test_flatten_v2_summary_collects_nonempty_string_summaries():
    """clusters[].chapters[].summary 摊平：只收非空 str summary，跳过空串/非 dict 记录。"""
    data = {"clusters": [
        {"chapters": {"1": {"summary": "第一章梗概"}, "2": {"summary": ""},
                      "3": {"nope": 1}}},
        {"chapters": {"4": {"summary": "第四章梗概"}}},
        "脏数据",                # 非 dict cluster → 跳过
        {"no_chapters": True},   # 无 chapters → 跳过
    ]}
    flat = mod._flatten_v2_chapter_summary(data)
    assert flat == {"1": "第一章梗概", "4": "第四章梗概"}, f"实得 {flat}"


def test_flatten_v2_summary_non_list_clusters_returns_empty():
    """clusters 非 list（旧 v1 文件无 clusters）→ 返回空 dict，不崩。"""
    assert mod._flatten_v2_chapter_summary({"clusters": "notlist"}) == {}
    assert mod._flatten_v2_chapter_summary({}) == {}


# ══════════════════════════════════════════════════════════════════════════
# B. scan_summary_consistency（磁盘版） / from_ledger 的 TOO_SHORT 分支
# ══════════════════════════════════════════════════════════════════════════
def test_summary_too_short_disk_v1_topmost():
    """v1 顶层 chapter_summary 为短 str（<50 CJK）→ SUMMARY_TOO_SHORT。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_summary(proj, {"chapter_summary": {"3": "太短了"}})
        findings = mod.scan_summary_consistency(proj, [3])
        short = [f for f in findings if f["code"] == "SUMMARY_TOO_SHORT"]
        assert len(short) == 1, f"应检出 SUMMARY_TOO_SHORT，实得 {_codes(findings)}"
        assert short[0]["ch"] == 3
        assert short[0]["summary_len"] < 50


def test_summary_consistency_missing_file_returns_empty():
    """无 故事块摘要.json → 返回空（不崩，无误报）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        assert mod.scan_summary_consistency(proj, [1, 2, 3]) == []


def test_summary_too_short_from_ledger():
    """账本版短摘要（<50 CJK）→ SUMMARY_TOO_SHORT（与 keyword 维度正交，既有测试未覆盖）。"""
    recs = [(7, {"summary": "短摘要", "text_keyword_set": ["甲", "乙"]})]
    findings = mod.scan_summary_consistency_from_ledger(recs)
    short = [f for f in findings if f["code"] == "SUMMARY_TOO_SHORT"]
    assert len(short) == 1, f"应检出 SUMMARY_TOO_SHORT，实得 {_codes(findings)}"
    assert short[0]["ch"] == 7


# ══════════════════════════════════════════════════════════════════════════
# C. scan_lessons_feedback —— FAILURE_RECURRING / SUCCESS_NEVER_REUSED
# ══════════════════════════════════════════════════════════════════════════
def test_failure_recurring_detected():
    """failure 关键词在记录章之后 ≥2 章复发 → FAILURE_RECURRING（writer 没消费 lessons）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_lessons(proj, failure=[
            {"id": "F1", "recorded_at_ch": 1, "keywords": ["顿时"]}])
        for ch, txt in [(2, "无关"), (3, "顿时有"), (4, "顿时再"), (5, "无关")]:
            _write_chapter(proj, ch, txt)
        findings = mod.scan_lessons_feedback(proj, [2, 3, 4, 5])
        rec = [f for f in findings if f["code"] == "FAILURE_RECURRING"]
        assert len(rec) == 1, f"应检出 FAILURE_RECURRING，实得 {_codes(findings)}"
        assert rec[0]["recurring_chs"] == [3, 4]
        assert rec[0]["failure_id"] == "F1"


def test_success_never_reused_detected():
    """success 关键词在记录章后 ≥5 章中零复用 → SUCCESS_NEVER_REUSED（经验沉淀失败）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_lessons(proj, success=[
            {"id": "S1", "recorded_at_ch": 1, "keywords": ["独门神功"]}])
        chs = [2, 3, 4, 5, 6, 7]
        for ch in chs:
            _write_chapter(proj, ch, "完全无关的内容")
        findings = mod.scan_lessons_feedback(proj, chs)
        sk = [f for f in findings if f["code"] == "SUCCESS_NEVER_REUSED"]
        assert len(sk) == 1, f"应检出 SUCCESS_NEVER_REUSED，实得 {_codes(findings)}"
        assert sk[0]["success_id"] == "S1"
        assert sk[0]["post_chs_checked"] == chs


def test_lessons_feedback_missing_file_returns_empty():
    """无 写作经验.json → 返回空（向后兼容，不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        assert mod.scan_lessons_feedback(proj, [1, 2, 3]) == []


# ══════════════════════════════════════════════════════════════════════════
# 工具函数：load_json / get_chapters
# ══════════════════════════════════════════════════════════════════════════
def test_load_json_fallbacks():
    """load_json：文件缺失 / 坏 JSON 都回退 default；合法 JSON 正常解析。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.json"
        assert mod.load_json(p, {"k": 1}) == {"k": 1}          # 缺失 → default
        p.write_text("{bad json", encoding="utf-8")
        assert mod.load_json(p, "DEF") == "DEF"                # 坏 JSON → default
        p.write_text('{"a": 1}', encoding="utf-8")
        assert mod.load_json(p) == {"a": 1}                    # 合法 → 解析


def test_get_chapters_sorts_and_tails():
    """get_chapters：按章号数值排序、取末 N，忽略非『第N章』目录。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        for nm in ("第001章", "第012章", "第007章", "乱七八糟"):
            (proj / "章节" / nm).mkdir(parents=True)
        assert mod.get_chapters(proj, 99) == [1, 7, 12]   # 全量按数值升序
        assert mod.get_chapters(proj, 2) == [7, 12]       # 取末 2 个


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— 真 argparse + 真扫描 + 真报告落盘 + 退出码
# ══════════════════════════════════════════════════════════════════════════
def test_cli_warning_exits_2():
    """FAILURE_RECURRING（warning）→ exit 2，报告 summary.warning==1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_lessons(proj, failure=[
            {"id": "F1", "recorded_at_ch": 1, "keywords": ["顿时"]}])
        for ch, txt in [(2, "无"), (3, "顿时有"), (4, "顿时再"), (5, "无")]:
            _write_chapter(proj, ch, txt)
        p = _run_cli(proj, "--last-n", "10")
        assert p.returncode == 2, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["scan_type"] == "meta_quality"
        assert report["summary"]["warning"] == 1
        assert any(f["code"] == "FAILURE_RECURRING" for f in report["findings"])


def test_cli_healthy_exits_0():
    """正常章（无任何 finding）→ exit 0，报告 summary 全 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # 5 章字数平稳（避免 outlier/trend/variance）+ 无经验文件 + 无摘要文件
        for i, ch in enumerate([1, 2, 3, 4, 5]):
            _write_chapter(proj, ch, "正" * (2000 + i * 10))
        p = _run_cli(proj, "--last-n", "10")
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["summary"]["warning"] == 0
        assert report["summary"]["advisory"] == 0
        assert report["findings"] == []


def test_cli_no_chapters_skips_without_report():
    """无 cluster 账本 + 无章目录 → [SKIP] 无已写章节 → exit 0 且不建报告目录。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        p = _run_cli(proj)
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"
        assert not (proj / "_数据库" / ".cross_chapter_scan").exists(), \
            "SKIP 分支提前 exit，不该落报告目录"


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  [OK] {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  [FAIL] {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"[cross_cluster_meta_quality_aggregate] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
