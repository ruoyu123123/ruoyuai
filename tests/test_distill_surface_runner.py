#!/usr/bin/env python3
"""distill_surface_runner 测试（phase-1 串联·mock judge·不调 gen-model）。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import distill_surface_runner as sr  # noqa: E402


class _O:
    def __init__(self, d):
        self.data = d
        self.ok = True


def _proj():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "原文").mkdir(parents=True)
    (tmp / "蒸馏进度").mkdir(parents=True)
    for ch in (1, 2, 3):
        (tmp / "原文" / f"第{ch}章.txt").write_text(f"第{ch}章 T\n正文{ch}", encoding="utf-8")
        (tmp / "蒸馏进度" / f"ch{ch}_metrics.json").write_text(
            json.dumps({"profile": {"cjk_chars": 1000 + ch}}), encoding="utf-8")
    (tmp / "cluster_index.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 2]},
        {"cluster_id": "cluster_002", "chapter_range": [3, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    return tmp


def _fake_judge(agent, pr, *, params, context_files, output_path, **kw):
    assert agent == "novel-distill-analyzer"
    # 验全章全文 context 注入（must_fix·不截断）
    assert context_files and context_files[0][1].exists()
    d = {"quantitative": {}, "qualitative_dims": {"dim1": "短句连发"},
         "golden_paragraphs": {"golden_opening": "X"},
         "continuity": {"pacing_curve": "慢-中-快", "emotional_arc": "升"}}
    Path(output_path).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return _O(d)


def test_surface_runner_full_loop():
    tmp = _proj()
    try:
        assert sr.run(tmp, judge_fn=_fake_judge) == 0
        # surface JSON per cluster
        surf = sorted(p.name for p in (tmp / "蒸馏进度").glob("cluster_*_surface.json"))
        assert surf == ["cluster_001_surface.json", "cluster_002_surface.json"]
        # continuity per cluster（must_fix#2·arc_aggregator 消费）
        cont = sorted(p.name for p in (tmp / "衔接分析").glob("*.json"))
        assert cont == ["cluster_001_continuity.json", "cluster_002_continuity.json"]
        # 逐章投影 ch{N}.json（arc_aggregator 读 word_count）
        ch1 = json.loads((tmp / "蒸馏进度" / "ch1.json").read_text(encoding="utf-8"))
        assert ch1["word_count"] == 1001    # 从 chN_metrics 取
        assert ch1["qualitative_dims"]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_surface_runner_idempotent():
    """已有 surface JSON 不重跑（除非 --overwrite）。"""
    tmp = _proj()
    try:
        sr.run(tmp, judge_fn=_fake_judge)
        calls = {"n": 0}

        def counting(agent, pr, **kw):
            calls["n"] += 1
            return _fake_judge(agent, pr, **kw)
        sr.run(tmp, judge_fn=counting)   # 已有 → 跳过
        assert calls["n"] == 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_surface_runner_block_failure_returns_3():
    tmp = _proj()
    try:
        def blocking(agent, pr, **kw):
            raise RuntimeError("judge block fail")
        assert sr.run(tmp, judge_fn=blocking) == 3
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_surface_runner_no_index_returns_1():
    tmp = Path(tempfile.mkdtemp())
    try:
        assert sr.run(tmp, judge_fn=_fake_judge) == 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
