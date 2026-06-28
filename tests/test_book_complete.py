#!/usr/bin/env python3
"""完本终态回归测试（P0-2 缺漏修复 · 2026-06-12 · 零依赖范式）。

缺漏报告结论：ME 池耗尽时旧版 cluster_emergence_engine 走 [FAIL] exit 1——
把「大势走完=完本」误当故障；GUI scan_project 也继续建议写下一块。钉死四条契约：
  1. ME 真耗尽 → 写 _数据库/.book_complete.json（completed_at/reason/last_cluster 三字段）
     + main() exit 0 + stderr 打「完本不是故障」。
  2. 其他错误路径（启发式无候选等）保持 exit 1 不变。
  3. 正常书（ME 池未耗尽 / 无标记）不误判：emerge 正常涌现不写标记。
（2026-06-20 GUI 删档后收窄：原第 3/4 条 GUI scan_project 断言已删 · 完本契约本身保留。）
"""
import io
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cluster_emergence_engine as cee  # noqa: E402


# ───────────────────── 测试脚手架 ─────────────────────

def _mk_project(tmp: Path, major_events: list, clusters: list) -> Path:
    """最小项目骨架（同 test_cluster_emergence_volume 范式）。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "大势卡.json").write_text(
        json.dumps({"schema_version": "v27", "major_events": major_events},
                   ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters},
                   ensure_ascii=False), encoding="utf-8")
    (db / "世界状态.json").write_text("{}", encoding="utf-8")
    (db / "character_arc_state.json").write_text("{}", encoding="utf-8")
    return tmp


# ME 池全部 completed → find_remaining_mes 返回空 = 真耗尽（含 finale 也耗尽）
_EXHAUSTED_POOL = [
    {"id": "ME-V1-01", "volume": 1, "title": "入门", "status": "completed"},
    {"id": "ME-V1-02", "volume": 1, "title": "收束", "status": "completed",
     "is_volume_finale": True},
]

_ALIVE_POOL = [
    {"id": "ME-V1-01", "volume": 1, "title": "入门", "description": "进副本", "status": "pending"},
    {"id": "ME-V1-02", "volume": 1, "title": "收束", "description": "卷末", "status": "pending",
     "is_volume_finale": True},
]


# ───────────────────── 1. 标记文件写入格式 ─────────────────────

def test_marker_written_with_required_fields():
    """ME 真耗尽 → book_complete=True + 标记文件三字段齐全（completed_at ISO 可解析）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _EXHAUSTED_POOL, clusters=[])
        r = cee.emerge_next_cluster(root, "cluster_002")
        assert r.get("book_complete") is True
        assert r.get("ok") is False          # 「没涌现出新 cluster」语义不变
        marker = root / "_数据库" / ".book_complete.json"
        assert marker.exists()
        data = json.loads(marker.read_text(encoding="utf-8"))
        datetime.fromisoformat(data["completed_at"])   # ISO 时间可解析（格式错会抛）
        assert data["reason"] == "ME 池耗尽·大势已走完"
        assert data["last_cluster"] == "cluster_002"


def test_marker_idempotent_keeps_first_completed_at():
    """幂等：重跑 emerge 不覆盖首次完本时间。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _EXHAUSTED_POOL, clusters=[])
        cee.emerge_next_cluster(root, "cluster_002")
        marker = root / "_数据库" / ".book_complete.json"
        first = json.loads(marker.read_text(encoding="utf-8"))
        # 人为改 completed_at 再重跑·若被覆盖则说明非幂等
        first["completed_at"] = "2000-01-01T00:00:00"
        marker.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
        cee.emerge_next_cluster(root, "cluster_003")
        again = json.loads(marker.read_text(encoding="utf-8"))
        assert again["completed_at"] == "2000-01-01T00:00:00"
        assert again["last_cluster"] == "cluster_002"  # 保留首次记录


# ───────────────────── 2. main() 退出码语义 ─────────────────────

def _run_main(argv: list):
    """跑 cee.main() 并捕获 stderr。返回 (exit_code, stderr_text)。"""
    saved_argv, saved_err = sys.argv, sys.stderr
    sys.argv = ["cluster_emergence_engine.py"] + argv
    sys.stderr = io.StringIO()
    try:
        code = cee.main()
        return code, sys.stderr.getvalue()
    finally:
        sys.argv, sys.stderr = saved_argv, saved_err


def test_main_exit_0_on_me_exhaustion():
    """完本不是失败：ME 耗尽 → exit 0 + stderr 打庆祝行（下游 step13 自然过）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _EXHAUSTED_POOL, clusters=[])
        code, err = _run_main([str(root), "emerge", "--after-cluster", "cluster_002"])
        assert code == 0
        assert "完本不是故障" in err
        assert (root / "_数据库" / ".book_complete.json").exists()


def test_main_other_error_paths_still_exit_1():
    """只动「ME 真耗尽」分支：其他 emerge 错误路径保持 exit 1。"""
    saved = cee.emerge_next_cluster
    cee.emerge_next_cluster = lambda *a, **k: {"ok": False, "error": "无符合启发式条件的 candidate ME"}
    try:
        with tempfile.TemporaryDirectory() as d:
            root = _mk_project(Path(d), _ALIVE_POOL, clusters=[])
            code, err = _run_main([str(root), "emerge", "--after-cluster", "cluster_001"])
            assert code == 1
            assert "[FAIL]" in err
    finally:
        cee.emerge_next_cluster = saved


# ───────────────────── 3. 完本标记 vs 正常涌现 ─────────────────────


def test_emerge_normal_pool_writes_no_marker():
    """正常书（ME 池未耗尽）emerge 正常涌现 · 绝不写完本标记。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ALIVE_POOL, clusters=[])
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r.get("ok") is True
        assert not r.get("book_complete")
        assert not (root / "_数据库" / ".book_complete.json").exists()


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
