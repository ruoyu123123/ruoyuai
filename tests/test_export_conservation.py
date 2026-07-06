"""Book-level export conservation and integrity tests."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_io as cio  # noqa: E402
import export_book as eb  # noqa: E402

CHAPTER_DIR = "\u7ae0\u8282"
DB_DIR = "_\u6570\u636e\u5e93"


def _mk_chapter(root: Path, ch: int, body: str, title: str | None = None) -> None:
    cio.write_body(root, ch, body)
    if title is not None:
        changes = cio.changes_path(root, ch)
        changes.parent.mkdir(parents=True, exist_ok=True)
        changes.write_text(json.dumps({"chapter": ch, "title": title}, ensure_ascii=False), encoding="utf-8")


def _mk_event_clusters(root: Path, ranges: list[tuple[int, int]]) -> None:
    db_dir = root / DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    clusters = [
        {"cluster_id": f"cluster_{idx + 1:03d}", "chapter_range": [start, end]}
        for idx, (start, end) in enumerate(ranges)
    ]
    (db_dir / "\u4e8b\u4ef6\u7c07.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_contiguous_book_verdict_ok_and_conservation_exact() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u5b88\u6052\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u7b2c\u4e00\u7ae0\u6b63\u6587\u5185\u5bb9\u3002", title="\u5f00\u5c40")
        _mk_chapter(root, 2, "\u7b2c\u4e8c\u7ae0\u6b63\u6587\u5185\u5bb9\u3002", title="\u8f6c\u6298")
        _mk_chapter(root, 3, "\u7b2c\u4e09\u7ae0\u6b63\u6587\u5185\u5bb9\u3002", title="\u6536\u675f")

        report = eb.export_book(root, with_compliance_checklist=False)
        integrity = report["integrity"]
        assert integrity["verdict"] == "ok", integrity
        assert integrity["continuity"]["missing"] == []
        assert integrity["continuity"]["duplicates"] == {}
        assert integrity["continuity"]["ascending"] is True

        wc = integrity["word_conservation"]
        assert wc["ok"] is True
        assert wc["diff"] == 0
        assert wc["full_cjk"] == wc["body_content_cjk"] + wc["title_overhead_cjk"]
        assert wc["full_cjk"] == report["total_cjk"]


def test_missing_chapter_is_hard_and_export_is_not_written() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u6b63\u6587\u3002", title="\u4e00")
        _mk_chapter(root, 2, "\u4e8c\u7ae0\u6b63\u6587\u3002", title="\u4e8c")
        _mk_chapter(root, 4, "\u56db\u7ae0\u6b63\u6587\u3002", title="\u56db")

        try:
            eb.export_book(root)
            raise AssertionError("missing chapter should block export")
        except eb.ExportIntegrityError as exc:
            integrity = exc.report["integrity"]
        assert integrity["verdict"] == "hard"
        assert integrity["continuity"]["missing"] == [3]
        assert any(issue["code"] == "CHAPTER_MISSING" for issue in integrity["issues"])
        assert not (root / "exports").exists()


def test_missing_chapter_main_exit2_by_default() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u6b63\u6587\u3002", title="\u4e00")
        _mk_chapter(root, 3, "\u4e09\u7ae0\u6b63\u6587\u3002", title="\u4e09")

        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(root)])
            raise AssertionError("main should exit")
        except SystemExit as exc:
            assert exc.code == 2
        assert not (root / "exports").exists()


def test_duplicate_chapter_source_is_hard() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u6b63\u6587\u3002", title="\u4e00")
        _mk_chapter(root, 2, "\u4e8c\u7ae0\u6b63\u6587\u6807\u51c6\u4f4d\u3002", title="\u4e8c")
        duplicate_dir = root / CHAPTER_DIR / "\u5377\u4e00" / "\u7b2c002\u7ae0"
        duplicate_dir.mkdir(parents=True, exist_ok=True)
        (duplicate_dir / "\u7b2c002\u7ae0.txt").write_text("\u4e8c\u7ae0\u91cd\u590d\u6e90\u3002", encoding="utf-8")

        try:
            eb.export_book(root)
            raise AssertionError("duplicate chapter source should block export")
        except eb.ExportIntegrityError as exc:
            integrity = exc.report["integrity"]
        assert integrity["verdict"] == "hard"
        assert 2 in integrity["continuity"]["duplicates"]
        assert any(issue["code"] == "CHAPTER_DUPLICATE" for issue in integrity["issues"])


def test_archived_and_draft_not_counted_as_duplicate() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u5178\u4e00\u7ae0\u3002", title="\u4e00")
        for subdir in ("_archived_v1/\u7b2c001\u7ae0", "cluster_001_draft"):
            target = root / CHAPTER_DIR / subdir
            target.mkdir(parents=True, exist_ok=True)
            (target / "\u7b2c001\u7ae0.txt").write_text("\u975e\u6b63\u5178\u526f\u672c\u3002", encoding="utf-8")

        report = eb.export_book(root, with_compliance_checklist=False)
        assert report["integrity"]["verdict"] == "ok"
        assert report["integrity"]["continuity"]["duplicates"] == {}


def test_cluster_range_uncovered_is_hard_and_export_is_not_written() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u3002", title="\u4e00")
        _mk_chapter(root, 2, "\u4e8c\u7ae0\u3002", title="\u4e8c")
        _mk_event_clusters(root, [(1, 3)])

        try:
            eb.export_book(root, with_compliance_checklist=False)
            raise AssertionError("uncovered cluster range should block export")
        except eb.ExportIntegrityError as exc:
            integrity = exc.report["integrity"]
        assert integrity["verdict"] == "hard"
        assert integrity["ok"] is False
        assert integrity["coverage"]["available"] is True
        assert integrity["coverage"]["uncovered"] == [3]
        assert any(issue["code"] == "CLUSTER_RANGE_UNCOVERED" for issue in integrity["issues"])
        assert not (root / "exports").exists()


def test_coverage_unavailable_when_no_event_clusters() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u3002", title="\u4e00")
        _mk_chapter(root, 2, "\u4e8c\u7ae0\u3002", title="\u4e8c")

        report = eb.export_book(root, with_compliance_checklist=False)
        assert report["integrity"]["coverage"]["available"] is False
        assert report["integrity"]["verdict"] == "ok"


def test_integrity_section_always_present_in_success_report() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u552f\u4e00\u4e00\u7ae0\u3002", title="\u4e00")

        report = eb.export_book(root, with_compliance_checklist=False)
        assert "integrity" in report
        for key in ("verdict", "ok", "continuity", "coverage", "word_conservation", "issues"):
            assert key in report["integrity"]


def test_scan_chapter_sources_returns_standard_sources() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u3002", title="\u4e00")
        _mk_chapter(root, 2, "\u4e8c\u7ae0\u3002", title="\u4e8c")

        sources = eb.scan_chapter_sources(root)
        assert set(sources.keys()) == {1, 2}
        assert all(len(paths) == 1 for paths in sources.values())
