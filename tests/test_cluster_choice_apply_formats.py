#!/usr/bin/env python3
"""cluster_choice_apply 三种 brief 来源格式（真 outline e2e 抓出·judge cluster_brief 嵌套）。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import cluster_choice_apply as cca  # noqa: E402

_BRIEF = {"cluster_id": "cluster_001", "scope_summary": "首块",
          "scene_storyboard": [{"scene": 0, "summary": "灾难开场"}],
          "research_ref": {
              "cache_path": "_数据库/.research_cache/inspiration_cluster_001_test.md",
              "anchors_used": ["anchor_A"],
          }}


def _proj():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True)
    (tmp / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": []}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _run(payload):
    tmp = _proj()
    ch = tmp / "choice.json"
    ch.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = cca.apply_choice(tmp, "001", ch)
    ev = json.loads((tmp / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return r, ev


def test_pause_answer_format():
    """① pause answer_artifact：{"step":n, "answer": brief}。"""
    r, ev = _run({"step": 6, "answer": _BRIEF})
    assert ev["clusters"][0]["status"] == "in_progress"


def test_judge_cluster_brief_format():
    """② outline-planner judge：{cluster_id, ..., cluster_brief: brief, free_notes}。"""
    r, ev = _run({"mode": "ecas_cluster_brief", "cluster_id": "cluster_001",
                  "free_notes": "x", "cluster_brief": _BRIEF})
    c0 = ev["clusters"][0]
    assert c0["status"] == "in_progress"
    assert c0.get("scene_storyboard"), "judge brief 的 storyboard 丢了"


def test_direct_brief_format():
    """③ brief dict 本身（有 scope_summary/scene_storyboard）。"""
    r, ev = _run(_BRIEF)
    assert ev["clusters"][0]["status"] == "in_progress"
    assert ev["clusters"][0]["research_ref"]["cache_path"].endswith("inspiration_cluster_001_test.md")
    assert ev["clusters"][0]["research_ref"]["anchors_used"] == ["anchor_A"]


def test_blueprint_written_for_build_manifest_preflight():
    """🔴 轮次1 blocker 回归：apply_choice 必须同步写 进度.json.cluster_blueprint
    （scene ch 占位 start+i）·否则 GUI 建的书 cluster-write step1 preflight 必 fatal
    （v27 不预填 chapter_range → build_manifest fluid fallback 永不可达）。"""
    tmp = _proj()
    ch = tmp / "choice.json"
    ch.write_text(json.dumps({"answer": {
        "cluster_id": "cluster_001", "scope_summary": "首块",
        "scene_storyboard": [{"scene": 0, "summary": "灾难开场"},
                             {"scene": 1, "summary": "反转"}]}},
        ensure_ascii=False), encoding="utf-8")
    try:
        cca.apply_choice(tmp, "001", ch)
        prog = json.loads((tmp / "_数据库" / "进度.json").read_text(encoding="utf-8"))
        bp = prog["cluster_blueprint"]["cluster_001"]
        chs = [s["ch"] for s in bp["scene_storyboard"]]
        assert chs == [1, 2], f"ch 占位错: {chs}"
        assert all(s.get("title") for s in bp["scene_storyboard"])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_blueprint_start_ch_after_prev_range():
    """后续 cluster：start = 前一 cluster 已回填 chapter_range 末 + 1。"""
    tmp = _proj()
    sj = tmp / "_数据库" / "事件簇.json"
    sj.write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 5], "status": "done"}]},
        ensure_ascii=False), encoding="utf-8")
    ch = tmp / "choice.json"
    ch.write_text(json.dumps({"answer": {
        "cluster_id": "cluster_002",
        "scene_storyboard": [{"scene": 0, "summary": "新块开场"}]}},
        ensure_ascii=False), encoding="utf-8")
    try:
        cca.apply_choice(tmp, "002", ch)
        prog = json.loads((tmp / "_数据库" / "进度.json").read_text(encoding="utf-8"))
        assert prog["cluster_blueprint"]["cluster_002"]["scene_storyboard"][0]["ch"] == 6
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_brief_raises():
    tmp = _proj()
    ch = tmp / "x.json"
    ch.write_text(json.dumps({"mode": "x", "no_brief": True}), encoding="utf-8")
    try:
        cca.apply_choice(tmp, "001", ch)
        assert False, "应抛 ValueError"
    except ValueError:
        pass
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
