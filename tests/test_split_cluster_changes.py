# -*- coding: utf-8 -*-
"""split_cluster_changes 核心拆分逻辑回归网（第四轮 Workflow 挖的零测试盲区·2026-06-17）。

cluster-write step6 唯一拆分器·H1/H2/H11/L1 一堆文档化边界修复全无回归网（grep 实证 git 史从未测过）。
覆盖 3 个确定性核心函数（split_changes 双 schema/pending_tail 待后续补）：
  · _is_landed_range —— SC-6 bool 守卫（Python isinstance(True,int)=True 核心陷阱）
  · detect_other_cluster_overlap —— H2 写盘前重叠检测
  · writeback_event_cluster_range —— H11 相邻不相交修正 + L1 atomic fail-fast

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import split_cluster_changes as scc  # noqa: E402


def _mk_shi(td, clusters):
    """造 _数据库/事件簇.json fixture，返回 project_root。"""
    db = Path(td) / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return Path(td)


# ============ _is_landed_range（SC-6 · bool 守卫核心回归陷阱）============
def test_is_landed_range_valid():
    assert scc._is_landed_range([1, 3]) is True


def test_is_landed_range_bool_guard():
    """🔴 核心陷阱：Python isinstance(True,int)=True·无 bool 守卫则 [True,3] 被误判合法 range 污染权威源。"""
    assert scc._is_landed_range([True, 3]) is False
    assert scc._is_landed_range([1, False]) is False


def test_is_landed_range_rejects_non_range():
    assert scc._is_landed_range("1-3") is False
    assert scc._is_landed_range([1]) is False
    assert scc._is_landed_range([1, 2, 3]) is False
    assert scc._is_landed_range(None) is False


# ============ detect_other_cluster_overlap（H2 重叠检测）============
def test_overlap_none():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                            {"cluster_id": "cluster_002", "chapter_range": [5, 8]}])
        out = scc.detect_other_cluster_overlap(root, "cluster_003", [9, 10])
        assert out["owned_by_other"] == set()
        assert out["hard_overlap"] is False


def test_overlap_partial():
    """部分重叠：[3,4,9] 中 3/4 被 cluster_001 占·9 自由 → owned{3,4}·非全占 hard False。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                            {"cluster_id": "cluster_002", "chapter_range": [5, 8]}])
        out = scc.detect_other_cluster_overlap(root, "cluster_003", [3, 4, 9])
        assert out["owned_by_other"] == {3, 4}
        assert out["hard_overlap"] is False


def test_overlap_hard():
    """全部拟切章被他 cluster 占 → hard_overlap True（无章可写·调用方应中止）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]}])
        out = scc.detect_other_cluster_overlap(root, "cluster_003", [1, 2, 3, 4])
        assert out["hard_overlap"] is True


def test_overlap_self_excluded():
    """自己不算重叠（_norm_cid 归一比对）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]}])
        out = scc.detect_other_cluster_overlap(root, "cluster_001", [1, 2])
        assert out["owned_by_other"] == set()


def test_overlap_ignores_unlanded():
    """未落章 cluster（range=None）不参与重叠比对（SC-6）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": None}])
        out = scc.detect_other_cluster_overlap(root, "cluster_003", [1, 2])
        assert out["owned_by_other"] == set()


# ============ writeback_event_cluster_range（H11 相邻修正 + L1）============
def test_writeback_clean():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                            {"cluster_id": "cluster_002", "chapter_range": None}])
        out = scc.writeback_event_cluster_range(root, "cluster_002", [5, 6])
        assert out["ok"] is True
        assert out["new"] == [5, 6]


def test_writeback_adjacent_fix():
    """H11：本 cluster lo<=前序 hi → lo 修正为 前.hi+1（[3,4,5,6] vs prev[1,4] → new[5,6]）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                            {"cluster_id": "cluster_002", "chapter_range": None}])
        out = scc.writeback_event_cluster_range(root, "cluster_002", [3, 4, 5, 6])
        assert out["ok"] is True
        assert out["new"] == [5, 6]
        assert out["warnings"]


def test_writeback_illegal_rejected():
    """H11：修正后 lo>hi（全被前序占）→ 拒写 ok False（不污染权威源·[2,3] vs prev[1,4] → lo5>hi3）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                            {"cluster_id": "cluster_002", "chapter_range": None}])
        out = scc.writeback_event_cluster_range(root, "cluster_002", [2, 3])
        assert out["ok"] is False


def test_writeback_empty_chapters():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": None}])
        out = scc.writeback_event_cluster_range(root, "cluster_001", [])
        assert out["ok"] is False


def test_writeback_cluster_not_found():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": [1, 4]}])
        out = scc.writeback_event_cluster_range(root, "cluster_999", [5, 6])
        assert out["ok"] is False


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
