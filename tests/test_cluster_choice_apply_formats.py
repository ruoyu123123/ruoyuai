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


def test_answer_wrapped_judge_format():
    """🔴 2026-07-08 回归（验证书 e2e 抓出）：①×② 组合态——outline plan step6.5 把 judge
    整体包进 answer（{"answer": <emergence.json>}）。原逻辑取 answer 后不再解 cluster_brief
    → judge 元数据被当 brief 落库（真 brief 困在嵌套键·storyboard 丢失·blueprint 只写 1
    占位 scene）。修后 answer 内含 cluster_brief dict 时必须继续下钻取真 brief。"""
    r, ev = _run({"answer": {"mode": "ecas_cluster_brief", "cluster_id": "cluster_001",
                             "default_choice_label": "A", "free_notes": "x",
                             "cluster_brief": _BRIEF}})
    c0 = ev["clusters"][0]
    assert c0["status"] == "in_progress"
    assert c0.get("scene_storyboard"), "answer 包装 judge 的 storyboard 丢了"
    assert "cluster_brief" not in c0, "judge 元数据被当 brief 落库（嵌套 cluster_brief 泄漏进主表）"
    assert c0.get("scope_summary") == "首块"


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


def test_blueprint_vol_reads_volume_key():
    """🔴 回归：brief 的卷号 canonical 键是 "volume"（emergence/outline-planner 产），
    blueprint 必须读到它——旧代码读 brief.get("vol") 永远拿不到 → vol2+ cluster 全被
    误标 vol=1（进度.json 与 事件簇.json volume 双口径）。"""
    tmp = _proj()
    ch = tmp / "choice.json"
    ch.write_text(json.dumps({"answer": {
        "cluster_id": "cluster_004", "volume": 2, "scope_summary": "vol2 开卷",
        "scene_storyboard": [{"scene": 0, "summary": "起意东行"}]}},
        ensure_ascii=False), encoding="utf-8")
    try:
        cca.apply_choice(tmp, "004", ch)
        prog = json.loads((tmp / "_数据库" / "进度.json").read_text(encoding="utf-8"))
        assert prog["cluster_blueprint"]["cluster_004"]["vol"] == 2, "brief volume:2 未传到 blueprint vol"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_choice_advances_current_cluster():
    """🔴 A9 回归：schema-required 的 进度.json.current_cluster 必须随应用下一 cluster
    brief 前移——此前无人回写，全程卡 skeleton 初值 cluster_001（db_schema_validate 要求非空）。"""
    tmp = _proj()
    ch = tmp / "choice.json"
    ch.write_text(json.dumps({"answer": {
        "cluster_id": "cluster_004", "volume": 2, "scope_summary": "vol2 开卷",
        "scene_storyboard": [{"scene": 0, "summary": "起意东行"}]}},
        ensure_ascii=False), encoding="utf-8")
    try:
        cca.apply_choice(tmp, "004", ch)
        prog = json.loads((tmp / "_数据库" / "进度.json").read_text(encoding="utf-8"))
        assert prog["current_cluster"] == "cluster_004"
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
