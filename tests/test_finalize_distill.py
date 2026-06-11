#!/usr/bin/env python3
"""finalize_distill 测试（phase-5 定稿·确定性·最新 skill→FINAL + log）。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import finalize_distill as fd  # noqa: E402


def _proj():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "原文").mkdir(parents=True)
    for ch in range(1, 6):
        (tmp / "原文" / f"第{ch}章.txt").write_text("x", encoding="utf-8")
    return tmp


def test_finalize_picks_latest_skill():
    tmp = _proj()
    try:
        (tmp / "skill_v0.md").write_text("# v0", encoding="utf-8")
        (tmp / "skill_v1.md").write_text("# v1 最新", encoding="utf-8")
        (tmp / "作者风格.json").write_text("{}", encoding="utf-8")
        assert fd.finalize(tmp) == 0
        assert (tmp / "skill_FINAL.md").read_text(encoding="utf-8") == "# v1 最新"
        assert (tmp / "作者风格_FINAL.json").exists()
        assert (tmp / "distillation_log.md").exists()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_finalize_log_includes_sfs():
    tmp = _proj()
    try:
        (tmp / "skill_v1.md").write_text("# v1", encoding="utf-8")
        (tmp / "cluster_index.json").write_text(
            json.dumps({"clusters": [{}, {}]}), encoding="utf-8")
        ev = tmp / "eval.json"
        ev.write_text(json.dumps({"sfs_quick": 88.5, "grade": "B"}), encoding="utf-8")
        fd.finalize(tmp, ev)
        log = (tmp / "distillation_log.md").read_text(encoding="utf-8")
        assert "88.5" in log
        assert "5" in log    # 原文 5 章
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_finalize_no_skill_returns_1():
    tmp = _proj()
    try:
        assert fd.finalize(tmp) == 1   # 无 skill_v*.md
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
