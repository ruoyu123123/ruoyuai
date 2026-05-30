"""finalize_book 回归测试 — 守护 pending_tail 孤儿检测 + flush + 字数对账（2026-05-30）。

核心断言：末 cluster 有 pending_tail 时**检测得到** + **不被静默丢弃**（导出对账暴露）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import finalize_book as fb
import chapter_io as cio


# ============ 测试夹具：造一本带 pending_tail 的小书 ============

def _mk_chapter(root: Path, ch: int, body: str):
    """写 章节/第NNN章/第NNN章.txt 纯正文。"""
    cio.write_body(root, ch, body)


def _mk_cluster_draft(root: Path, key: str, draft_body: str):
    d = root / "章节" / f"cluster_{key}_draft"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"cluster_{key}_draft.txt").write_text(draft_body, encoding="utf-8")


def _mk_pending_tail(root: Path, key: str, body: str):
    d = root / "章节" / f"cluster_{key}_draft"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"cluster_{key}_pending_tail.txt").write_text(body, encoding="utf-8")


def _mk_splitter_wal(root: Path, key: str, payload: dict):
    d = root / "_数据库" / ".wal"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"splitter_cluster_{key}_decisions.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# 一段足够长的中文正文（> 单章下限，便于对账可见）
_TAIL_TEXT = "她拿起那个牛皮纸信封，把封口朝下倒了倒。\n\n" + ("照片的边缘已经发黄了，正面印着一间办公室。" * 60)


def test_last_cluster_pending_tail_detected_not_silently_lost():
    """[#3/#4 核心] 全书最后一个 cluster 的 pending_tail 必须被检出为孤儿，且对账暴露其字数。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 1, "第001章 开场\n\n" + ("正文一段。" * 100))
        _mk_chapter(root, 2, "第002章 推进\n\n" + ("正文二段。" * 100))
        _mk_cluster_draft(root, "001", "cluster001 草稿全文。" * 200)
        # cluster_001 是唯一 cluster（最后） · 末段退回 pending_tail · 无后继可拼 → 孤儿
        _mk_pending_tail(root, "001", _TAIL_TEXT)

        orphans = fb.scan_pending_tails(root)
        assert len(orphans) == 1, f"应检出 1 个孤儿，实际 {len(orphans)}"
        o = orphans[0]
        assert o["cluster_key"] == "001"
        assert o["is_last_cluster"] is True
        assert o["cjk"] > 0

        rec = fb.reconcile(root, threshold=fb.DEFAULT_RECONCILE_THRESHOLD)
        # 孤儿字数 = 导出会静默丢失的下界，必须 > 0 且被对账暴露
        assert rec["orphan_pending_tail_cjk"] == o["cjk"]
        assert rec["silent_loss_lower_bound_cjk"] > 0
        assert rec["over_threshold"] is True


def test_successor_cluster_consumed_via_wal_not_flagged():
    """后继 cluster 的 WAL 记录 previous_pending_tail_consumed_cjk > 0 → 已拼接 · 不算孤儿。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_cluster_draft(root, "001", "cluster001 草稿。" * 100)
        _mk_pending_tail(root, "001", _TAIL_TEXT)
        _mk_cluster_draft(root, "002", "cluster002 草稿。" * 100)
        # cluster_002 splitter WAL 记录已消费 cluster_001 的 pending_tail
        _mk_splitter_wal(root, "002", {"previous_pending_tail_consumed_cjk": 1200,
                                       "chapter_range": [3, 5], "chapters_split": 3})
        orphans = fb.scan_pending_tails(root)
        assert orphans == [], "WAL 已记录消费的 pending_tail 不应被判孤儿"


def test_successor_cluster_consumed_via_prepend_head_match():
    """后继 cluster 草稿头部 == 本 pending_tail 头部（被 prepend 进草稿）→ 已拼接 · 不算孤儿。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_pending_tail(root, "001", _TAIL_TEXT)
        # cluster_002 草稿是 pending_tail prepend 在前 + 新内容
        _mk_cluster_draft(root, "002", _TAIL_TEXT + "\n\n" + ("新内容继续写。" * 50))
        orphans = fb.scan_pending_tails(root)
        assert orphans == [], "草稿头部匹配 pending_tail（已 prepend）不应被判孤儿"


def test_successor_written_but_orphan_when_not_consumed():
    """真实漏拼场景（诡异接待处 cluster_004）：后继 cluster 已写但没拼上 pending_tail → 孤儿。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_pending_tail(root, "004", _TAIL_TEXT)
        # cluster_005 草稿独立内容（没 prepend cluster_004 pending_tail）+ WAL 无 consumed 记录
        _mk_cluster_draft(root, "005", "cluster005 全新内容。" * 80)
        _mk_splitter_wal(root, "005", {"chapter_range": [18, 20], "chapters_split": 3})
        orphans = fb.scan_pending_tails(root)
        assert len(orphans) == 1 and orphans[0]["cluster_key"] == "004"
        assert orphans[0]["is_last_cluster"] is False  # 005 在后，004 不是最后


def test_empty_pending_tail_not_flagged():
    """空 / 无正文 pending_tail 不算孤儿。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_pending_tail(root, "001", "\n  \n")
        assert fb.scan_pending_tails(root) == []


def test_flush_append_merges_into_last_chapter_and_removes_orphan():
    """--flush append：孤儿正文 prepend 到本 cluster 末章尾部 · pending_tail 文件删除 · 不再是孤儿。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 5, "第005章 末章\n\n" + ("原末章正文。" * 80))
        _mk_cluster_draft(root, "001", "cluster001 草稿。" * 100)
        _mk_pending_tail(root, "001", _TAIL_TEXT)
        _mk_splitter_wal(root, "001", {"chapter_range": [1, 5], "chapters_split": 5,
                                       "cluster_start_ch": 1})
        before = fb.scan_pending_tails(root)
        assert len(before) == 1
        res = fb.flush_orphan(root, before[0], "append")
        assert res["applied"] is True and res["appended_to_chapter"] == 5
        # 孤儿正文已并入末章
        merged = cio.read_body(root, 5)
        assert "牛皮纸信封" in merged
        # pending_tail 文件已删 · 不再是孤儿
        assert not (root / "章节" / "cluster_001_draft" / "cluster_001_pending_tail.txt").exists()
        assert fb.scan_pending_tails(root) == []


def test_flush_split_creates_new_chapter_and_removes_orphan():
    """--flush split：孤儿强切成新末章 · pending_tail 删除 · 不再是孤儿。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 5, "第005章 末章\n\n" + ("原末章正文。" * 80))
        _mk_pending_tail(root, "001", _TAIL_TEXT)
        _mk_splitter_wal(root, "001", {"chapter_range": [1, 5], "chapters_split": 5,
                                       "cluster_start_ch": 1})
        before = fb.scan_pending_tails(root)
        res = fb.flush_orphan(root, before[0], "split")
        assert res["applied"] is True and res["new_chapter"] == 6
        new_body = cio.read_body(root, 6)
        assert "牛皮纸信封" in new_body
        assert fb.scan_pending_tails(root) == []


def test_flush_warn_does_not_mutate():
    """--flush warn（默认）：只告警 · 不动用户内容（北极星⑤顾问非法官）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_pending_tail(root, "001", _TAIL_TEXT)
        before = fb.scan_pending_tails(root)
        res = fb.flush_orphan(root, before[0], "warn")
        assert res["applied"] is False
        # 文件仍在 · 仍是孤儿（warn 不处理）
        assert (root / "章节" / "cluster_001_draft" / "cluster_001_pending_tail.txt").exists()
        assert len(fb.scan_pending_tails(root)) == 1


def test_flush_append_degrades_to_warn_when_owner_unknown():
    """末章归属不可知（无 WAL chapter_range + 空 chapter_plan）→ append 降级 warn · 绝不乱改章。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 1, "第001章\n\n" + ("正文。" * 80))
        _mk_pending_tail(root, "004", _TAIL_TEXT)  # 无 cluster_004 的 WAL / plan 映射
        before = fb.scan_pending_tails(root)
        res = fb.flush_orphan(root, before[0], "append")
        assert res["applied"] is False  # 不乱猜末章
        # 用户内容未被改动 · 孤儿仍在（留给用户决策）
        assert (root / "章节" / "cluster_004_draft" / "cluster_004_pending_tail.txt").exists()
        assert len(fb.scan_pending_tails(root)) == 1


def test_flush_split_global_last_fallback_when_owner_unknown():
    """末章归属不可知时 split 退到全书最后一章 +1 新增章（只增不覆盖 · 安全）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 1, "第001章\n\n" + ("正文。" * 80))
        _mk_chapter(root, 17, "第017章\n\n" + ("正文。" * 80))  # 全书最后一章 = 17
        _mk_pending_tail(root, "004", _TAIL_TEXT)
        before = fb.scan_pending_tails(root)
        res = fb.flush_orphan(root, before[0], "split")
        assert res["applied"] is True and res["new_chapter"] == 18
        assert "牛皮纸信封" in cio.read_body(root, 18)
        assert fb.scan_pending_tails(root) == []


def test_last_chapter_from_progress_chapter_plan():
    """有 chapter_plan 时按 cluster 反查末章（且需正文物理存在）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 4, "第004章\n\n" + ("正文。" * 80))
        _mk_chapter(root, 5, "第005章\n\n" + ("正文。" * 80))
        (root / "_数据库").mkdir(parents=True, exist_ok=True)
        (root / "_数据库" / "进度.json").write_text(json.dumps({
            "chapter_plan": [
                {"ch": 4, "cluster": "cluster_002"},
                {"ch": 5, "cluster": "cluster_002"},
                {"ch": 6, "cluster": "cluster_003"},  # 第006章 物理不存在 → 不应被选
            ]
        }, ensure_ascii=False), encoding="utf-8")
        assert fb._last_chapter_from_progress(root, "002") == 5
        assert fb._last_chapter_from_progress(root, "003") is None  # 第006章 未切出


def test_build_report_advisory_gate_level():
    """报告必须标 advisory（不阻断导出）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_pending_tail(root, "001", _TAIL_TEXT)
        report = fb.build_report(root, "warn", fb.DEFAULT_RECONCILE_THRESHOLD)
        assert report["gate_level"] == "advisory"
        assert report["orphan_count"] == 1
        assert report["has_findings"] is True
