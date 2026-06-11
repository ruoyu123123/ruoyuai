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
          "scene_storyboard": [{"scene": 0, "summary": "灾难开场"}]}


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
