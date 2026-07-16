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
def test_split_changes_preserves_chapter_title():
    """🔴 回归：split_changes 整体覆盖 per-chapter _changes.json（只写 self_eval）时，必须保留
    gen_chapter_titles --apply 已回写的顶层 title——否则 6.2c(写title)→6.3(本脚本覆盖) 顺序下
    title 被吞，export_book.read_title FATAL missing title（本会话真机 export 撞的坑）。
    优先读既有 _changes.json.title，兜底从章头「第NNN章 标题」解析。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_shi(td, [{"cluster_id": "cluster_001", "chapter_range": None}])
        # cluster-level changes + splitter WAL(chapter_range=[1,2])
        draft = root / "章节" / "cluster_001_draft"
        draft.mkdir(parents=True)
        (draft / "cluster_001_changes.json").write_text(
            json.dumps({"self_eval": {"writer_mode": "x"}}, ensure_ascii=False), encoding="utf-8")
        (root / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)
        (root / "_数据库" / ".wal" / "splitter_cluster_001_decisions.json").write_text(
            json.dumps({"chapter_range": [1, 2], "cluster_start_ch": 1, "chapters_split": 2},
                       ensure_ascii=False), encoding="utf-8")
        # ch1: _changes.json 已有 title（apply 写入）；ch2: 只有章头（兜底解析路径）
        c1 = root / "章节" / "第001章"; c1.mkdir(parents=True)
        (c1 / "第001章.txt").write_text("第001章 含毒\n\n正文一。", encoding="utf-8")
        (c1 / "第001章_changes.json").write_text(
            json.dumps({"self_eval": {}, "title": "含毒"}, ensure_ascii=False), encoding="utf-8")
        c2 = root / "章节" / "第002章"; c2.mkdir(parents=True)
        (c2 / "第002章.txt").write_text("第002章 东望\n\n正文二。", encoding="utf-8")

        r = scc.split_changes(root, "001")
        assert r.get("ok") is not False, r
        t1 = json.loads((c1 / "第001章_changes.json").read_text(encoding="utf-8"))
        t2 = json.loads((c2 / "第002章_changes.json").read_text(encoding="utf-8"))
        assert t1.get("title") == "含毒", "既有 _changes.json.title 须被保留"
        assert t2.get("title") == "东望", "无既有 title 时须从章头兜底解析"


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


# ============ split_changes 早返回防御分支（完整写路径需 per-chapter fixture·独立会话补）============
def _mk_split_fixture(td, key, decisions, changes=None, clusters=None):
    """造 split_changes fixture：cluster_<key>_draft/changes.json + .wal/decisions.json + 可选 事件簇.json。"""
    root = Path(td)
    draft = root / "章节" / f"cluster_{key}_draft"
    draft.mkdir(parents=True, exist_ok=True)
    (draft / f"cluster_{key}_changes.json").write_text(
        json.dumps(changes if changes is not None else {}, ensure_ascii=False), encoding="utf-8")
    wal = root / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / f"splitter_cluster_{key}_decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
    if clusters is not None:
        (root / "_数据库" / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return root


def test_split_missing_changes():
    """cluster_changes.json 不存在 → ok False。"""
    with tempfile.TemporaryDirectory() as td:
        out = scc.split_changes(Path(td), "002")
        assert out["ok"] is False
        assert "cluster_changes 不存在" in out["error"]


def test_split_missing_decisions():
    """changes 在但 splitter_decisions 不存在 → ok False。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        draft = root / "章节" / "cluster_002_draft"
        draft.mkdir(parents=True)
        (draft / "cluster_002_changes.json").write_text("{}", encoding="utf-8")
        out = scc.split_changes(root, "002")
        assert out["ok"] is False
        assert "splitter_decisions 不存在" in out["error"]


def test_split_explicit_zero_cut():
    """🔴 H1 复修：chapters_split=0 → 早返回 ok True 空·不造 phantom 4 章（pending_tail）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_split_fixture(td, "002", {"chapters_split": 0, "cluster_start_ch": 5})
        out = scc.split_changes(root, "002")
        assert out["ok"] is True
        assert out["written_count"] == 0
        assert "pending_tail" in out["_note"]


def test_split_cannot_determine_range():
    """缺 chapter_range/chapters_split/cluster_start_ch + 事件簇 fallback 失败 → ok False（不盲写）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_split_fixture(td, "002", {})
        out = scc.split_changes(root, "002")
        assert out["ok"] is False


def test_split_rejects_target_chapters_fallback():
    """只有 cluster_start_ch + target_chapters 时必须失败，禁止恢复目标章数兜底。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_split_fixture(td, "002", {"cluster_start_ch": 5, "target_chapters": 4})
        out = scc.split_changes(root, "002")
        assert out["ok"] is False
        assert "禁止使用 target_chapters" in out["error"]


def test_split_uses_chapters_split_with_start_ch():
    """合法 fallback 只能来自 splitter 明确产出的 chapters_split。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_split_fixture(td, "002", {"cluster_start_ch": 5, "chapters_split": 2},
                                 clusters=[{"cluster_id": "cluster_002", "chapter_range": None}])
        for n in (5, 6):
            ch_dir = root / "章节" / f"第{n:03d}章"
            ch_dir.mkdir(parents=True, exist_ok=True)
        out = scc.split_changes(root, "002")
        assert out["ok"] is True
        assert out["chapter_range"] == [5, 6]
        assert out["written_count"] == 2


def test_split_hard_overlap_abort():
    """拟切章全被他 cluster 占 → hard_overlap 中止 ok False（防覆盖别 cluster 的 _changes）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _mk_split_fixture(td, "002", {"chapter_range": [1, 4]},
                                 clusters=[{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                                           {"cluster_id": "cluster_002", "chapter_range": None}])
        out = scc.split_changes(root, "002")
        assert out["ok"] is False
        assert "占用" in out["error"]


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
