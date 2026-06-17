#!/usr/bin/env python3
"""cross_cluster_ending_diversity_aggregate 确定性单测（2026-06-17 · /loop 自主硬化补漏）。

workflow 自动补测时此脚本的 author agent 撞瞬时 API 断连未落盘，手工补齐。
覆盖：
- _rec_ending_type 的 3 级取值优先级（M9 bug 修复点：扁平 → self_eval.applied_style → applied_style）。
- main() 磁盘分支的四类 finding（MONOTONE>50% / LOW_DIVERSITY≤2种 / MISSING≥3 / RUN≥4连续）
  + 退出码（0健康 / 1advisory / 2warning）。
强制非 cluster 模式（monkeypatch csr.is_cluster_mode→False）走逐章磁盘逻辑，确定可控。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))

import cross_cluster_ending_diversity_aggregate as mod  # noqa: E402


# ---------- 纯函数 _rec_ending_type ----------

def test_rec_ending_type_flat_wins():
    assert mod._rec_ending_type({"ending_type": "cliffhanger"}) == "cliffhanger"


def test_rec_ending_type_flat_over_nested():
    # 扁平命中即返回（零回归优先级），不被嵌套覆盖
    rec = {"ending_type": "revelation",
           "self_eval": {"applied_style": {"ending_type": "resolution"}}}
    assert mod._rec_ending_type(rec) == "revelation"


def test_rec_ending_type_self_eval_fallback():
    rec = {"self_eval": {"applied_style": {"ending_type": "emotional_pivot"}}}
    assert mod._rec_ending_type(rec) == "emotional_pivot"


def test_rec_ending_type_applied_style_fallback():
    rec = {"applied_style": {"ending_type": "question"}}
    assert mod._rec_ending_type(rec) == "question"


def test_rec_ending_type_missing_returns_empty():
    assert mod._rec_ending_type({}) == ""
    assert mod._rec_ending_type({"self_eval": {}}) == ""
    assert mod._rec_ending_type({"ending_type": ""}) == ""  # 空串不算命中


def test_rec_ending_type_non_dict_safe():
    assert mod._rec_ending_type(None) == ""
    assert mod._rec_ending_type("x") == ""
    assert mod._rec_ending_type(123) == ""


# ---------- main() 磁盘分支集成 ----------

def _seed_chapter(project_root: Path, ch: int, ending_type):
    d = project_root / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    if ending_type is None:
        body = {}  # 无 ending_type 标记
    else:
        body = {"self_eval": {"applied_style": {"ending_type": ending_type}}}
    (d / f"第{ch:03d}章_changes.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _run_main(project_root: Path, last_n=10) -> int:
    """跑 main()，强制磁盘分支，返回退出码。"""
    mod.csr.is_cluster_mode = lambda: False  # 强制非 cluster 分支
    saved = sys.argv
    sys.argv = ["prog", str(project_root), "--last-n", str(last_n)]
    try:
        mod.main()
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    finally:
        sys.argv = saved
    return 0


def _read_report(project_root: Path) -> dict:
    scan_dir = project_root / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("ending_diversity_*.json"))
    assert reports, "应写出 ending_diversity 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _codes(report: dict):
    return {f["code"] for f in report.get("findings", [])}


def test_main_monotone_warning_exit_2():
    with tempfile.TemporaryDirectory() as tmp:
        pr = Path(tmp)
        # 5 章，4 章同 ending_type（>50%）→ MONOTONE warning
        for ch, et in enumerate(["cliffhanger", "cliffhanger", "cliffhanger",
                                  "cliffhanger", "resolution"], start=1):
            _seed_chapter(pr, ch, et)
        rc = _run_main(pr)
        report = _read_report(pr)
        assert "ENDING_TYPE_MONOTONE" in _codes(report), report
        assert rc == 2, f"warning 应 exit 2，实际 {rc}"


def test_main_missing_warning_when_three_unlabeled():
    with tempfile.TemporaryDirectory() as tmp:
        pr = Path(tmp)
        # 3 章无标记 → MISSING；valid<3 → 早退 exit 0（报告仍含 MISSING）
        for ch in (1, 2, 3):
            _seed_chapter(pr, ch, None)
        rc = _run_main(pr)
        report = _read_report(pr)
        assert "ENDING_TYPE_MISSING" in _codes(report), report
        assert rc == 0, f"valid<3 早退应 exit 0，实际 {rc}"


def test_main_low_diversity_advisory_exit_1():
    with tempfile.TemporaryDirectory() as tmp:
        pr = Path(tmp)
        # 6 章 2 种交替 [A,A,B,B,A,B]：most=3/6=0.5 不>0.5（无 MONOTONE），
        # unique=2 且 total>=5 → LOW_DIVERSITY advisory；最长连续=2（无 RUN）
        seq = ["revelation", "revelation", "resolution",
               "resolution", "revelation", "resolution"]
        for ch, et in enumerate(seq, start=1):
            _seed_chapter(pr, ch, et)
        rc = _run_main(pr)
        report = _read_report(pr)
        codes = _codes(report)
        assert "ENDING_TYPE_LOW_DIVERSITY" in codes, report
        assert "ENDING_TYPE_MONOTONE" not in codes, "0.5 不该触发 MONOTONE(>0.5)"
        assert rc == 1, f"仅 advisory 应 exit 1，实际 {rc}"


def test_main_run_advisory_four_consecutive():
    with tempfile.TemporaryDirectory() as tmp:
        pr = Path(tmp)
        # 让某 ending_type 连续 >=4 且不占多数(避免被 MONOTONE 抢)：
        # 8 章 [A,A,A,A,B,C,D,B] → A 连续4(RUN)，A=4/8=0.5 不>0.5 → 无 MONOTONE
        seq = ["cliffhanger", "cliffhanger", "cliffhanger", "cliffhanger",
               "resolution", "revelation", "question", "resolution"]
        for ch, et in enumerate(seq, start=1):
            _seed_chapter(pr, ch, et)
        rc = _run_main(pr)
        report = _read_report(pr)
        codes = _codes(report)
        assert "ENDING_TYPE_RUN" in codes, report
        assert rc in (1, 2), f"有 finding 应非 0，实际 {rc}"


def test_main_healthy_diverse_exit_0():
    with tempfile.TemporaryDirectory() as tmp:
        pr = Path(tmp)
        # 6 章 4 种均衡分布、无连续>=4、无单一>50% → 健康 exit 0
        seq = ["cliffhanger", "resolution", "revelation",
               "question", "cliffhanger", "resolution"]
        for ch, et in enumerate(seq, start=1):
            _seed_chapter(pr, ch, et)
        rc = _run_main(pr)
        report = _read_report(pr)
        codes = _codes(report)
        assert "ENDING_TYPE_MONOTONE" not in codes
        assert "ENDING_TYPE_LOW_DIVERSITY" not in codes
        assert rc == 0, f"健康分布应 exit 0，实际 {rc}"


if __name__ == "__main__":
    for _n in sorted(k for k in dict(globals()) if k.startswith("test_")):
        globals()[_n]()
        print("OK", _n)
