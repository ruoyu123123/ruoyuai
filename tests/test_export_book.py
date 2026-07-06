"""Regression tests for the hard book exporter."""

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
COMPLIANCE_FILENAME = "\u53d1\u5e03\u524d\u5408\u89c4\u81ea\u67e5.md"


def _mk_chapter(root: Path, ch: int, body: str, title: str | None = None) -> None:
    cio.write_body(root, ch, body)
    if title is not None:
        changes = cio.changes_path(root, ch)
        changes.parent.mkdir(parents=True, exist_ok=True)
        changes.write_text(
            json.dumps({"schema_version": "v2.cluster", "chapter": ch, "title": title}, ensure_ascii=False),
            encoding="utf-8",
        )


def _mk_pending_tail(root: Path, key: str, body: str) -> None:
    draft_dir = root / CHAPTER_DIR / f"cluster_{key}_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / f"cluster_{key}_pending_tail.txt").write_text(body, encoding="utf-8")


def _read_export(report: dict) -> str:
    return Path(report["out_path"]).read_text(encoding="utf-8")


def test_basic_concat_titles_and_order() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u6d4b\u8bd5\u4e66"
        root.mkdir()
        _mk_chapter(root, 3, "\u7b2c\u4e09\u7ae0\u6b63\u6587\u3002", title="\u4e09")
        _mk_chapter(root, 1, "\u7b2c\u4e00\u7ae0\u6b63\u6587\u3002", title="\u5f00\u5c40\u4e00\u5ea7\u5c71")
        _mk_chapter(root, 2, "\u7b2c\u4e8c\u7ae0\u6b63\u6587\u3002", title="\u4e8c")

        report = eb.export_book(root, with_compliance_checklist=False)
        assert report is not None
        assert report["chapters"] == 3
        assert report["chapter_list"] == [1, 2, 3]
        assert report["integrity"]["verdict"] == "ok"
        assert Path(report["out_path"]).name == "\u6d4b\u8bd5\u4e66_\u5168\u6587_3\u7ae0.txt"

        text = _read_export(report)
        assert "\u7b2c1\u7ae0 \u5f00\u5c40\u4e00\u5ea7\u5c71" in text
        assert text.index("\u7b2c1\u7ae0") < text.index("\u7b2c2\u7ae0") < text.index("\u7b2c3\u7ae0")


def test_missing_title_metadata_is_hard_and_no_output_written() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u3002")

        try:
            eb.export_book(root)
            raise AssertionError("missing title metadata should block export")
        except eb.ExportIntegrityError as exc:
            report = exc.report
        assert report["out_path"] is None
        assert report["integrity"]["verdict"] == "hard"
        assert not (root / "exports").exists()
        assert any(issue["code"] == "CHAPTER_READ_OR_CONTRACT_ERROR" for issue in report["integrity"]["issues"])


def test_body_title_line_is_hard_contract_error() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u7b2c001\u7ae0 \u96ea\u591c\u6765\u5ba2\n\n\u95e8\u88ab\u63a8\u5f00\u3002", title="\u96ea\u591c")

        try:
            eb.export_book(root)
            raise AssertionError("title line in chapter body should block export")
        except eb.ExportIntegrityError as exc:
            errors = [
                detail
                for issue in exc.report["integrity"]["issues"]
                if issue["code"] == "CHAPTER_READ_OR_CONTRACT_ERROR"
                for detail in issue["detail"]
            ]
        assert any("title line" in item["error"] for item in errors)
        assert not (root / "exports").exists()


def test_changes_markers_in_body_are_hard_contract_error() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u4e00\u3002\n\n---CHANGES---\n{}", title="\u4e00")

        try:
            eb.export_book(root)
            raise AssertionError("machine marker in chapter body should block export")
        except eb.ExportIntegrityError as exc:
            assert exc.report["out_path"] is None
            assert not (root / "exports").exists()
            assert any(issue["code"] == "CHAPTER_READ_OR_CONTRACT_ERROR" for issue in exc.report["integrity"]["issues"])


def test_missing_chapter_file_blocks_export() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u6b63\u6587\u3002", title="\u4e00")
        _mk_chapter(root, 3, "\u4e09\u7ae0\u6b63\u6587\u3002", title="\u4e09")
        (root / CHAPTER_DIR / "\u7b2c002\u7ae0").mkdir(parents=True, exist_ok=True)

        try:
            eb.export_book(root)
            raise AssertionError("missing chapter body should block export")
        except eb.ExportIntegrityError as exc:
            integrity = exc.report["integrity"]
        assert integrity["verdict"] == "hard"
        assert any(issue["code"] == "CHAPTER_BODY_FILE_MISSING" for issue in integrity["issues"])
        assert any(issue["code"] == "CHAPTER_MISSING" for issue in integrity["issues"])
        assert not (root / "exports").exists()


def test_no_chapters_exit_2_and_no_output_written() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u7a7a\u4e66"
        root.mkdir()
        try:
            with redirect_stderr(io.StringIO()):
                eb.export_book(root)
            raise AssertionError("no chapters should block export")
        except eb.ExportIntegrityError as exc:
            assert exc.report["integrity"]["verdict"] == "hard"
            assert any(issue["code"] == "CHAPTER_SOURCE_MISSING" for issue in exc.report["integrity"]["issues"])
        assert not (root / "exports").exists()

        out = Path(temp) / "custom" / "book.txt"
        with redirect_stderr(io.StringIO()):
            try:
                eb.main([str(root), "--out", str(out)])
                raise AssertionError("main should exit")
            except SystemExit as exc:
                assert exc.code == 2
        assert not out.exists()
        assert not out.parent.exists()

        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(Path(temp) / "missing")])
            raise AssertionError("main should exit")
        except SystemExit as exc:
            assert exc.code == 2


def test_missing_project_exit_2() -> None:
    with tempfile.TemporaryDirectory() as temp:
        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(Path(temp) / "missing")])
            raise AssertionError("main should exit")
        except SystemExit as exc:
            assert exc.code == 2


def test_main_success_exit_0_and_out_override() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u3002", title="\u4e00")
        out = Path(temp) / "custom" / "book.txt"

        try:
            eb.main([str(root), "--out", str(out)])
            raise AssertionError("main should exit")
        except SystemExit as exc:
            assert exc.code == 0
        assert out.is_file()
        assert "\u7b2c1\u7ae0 \u4e00" in out.read_text(encoding="utf-8")


def test_pending_tail_blocks_and_does_not_write_export() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u6b63\u6587\u3002", title="\u4e00")
        _mk_pending_tail(root, "001", "\u5c3e\u6bb5\u672a\u5165\u7ae0\u3002" * 80)

        try:
            eb.export_book(root)
            raise AssertionError("pending_tail should block export")
        except eb.ExportIntegrityError as exc:
            report = exc.report
        assert report["pending_tails"] == 1
        assert report["out_path"] is None
        assert any(issue["code"] == "PENDING_TAIL_PRESENT" for issue in report["integrity"]["issues"])
        assert not (root / "exports").exists()


def test_cli_pending_tail_exit2() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u4e00\u7ae0\u6b63\u6587\u3002", title="\u4e00")
        _mk_pending_tail(root, "001", "\u5c3e\u6bb5\u672a\u5165\u7ae0\u3002" * 40)

        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(root)])
            raise AssertionError("main should exit")
        except SystemExit as exc:
            assert exc.code == 2
        assert not (root / "exports").exists()


def test_failure_does_not_write_custom_out_or_side_outputs() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u3002", title="\u4e00")
        _mk_pending_tail(root, "001", "\u5c3e\u6bb5\u672a\u5165\u7ae0\u3002" * 40)
        db_dir = root / DB_DIR
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "\u4eba\u7269\u5361.json").write_text(
            json.dumps({"characters": [{"id": "A", "name": "\u7532", "role": "\u4e3b\u89d2"}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        out = Path(temp) / "custom" / "book.txt"

        try:
            eb.export_book(root, out, with_adaptation_kit=True, with_compliance_checklist=True)
            raise AssertionError("pending_tail should block export")
        except eb.ExportIntegrityError:
            pass

        assert not out.exists()
        assert not out.parent.exists()
        assert not (root / "\u6539\u7f16\u8d44\u6599\u5305").exists()


def test_archived_and_draft_dirs_excluded() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u5178\u6b63\u6587\u3002", title="\u4e00")

        archived = root / CHAPTER_DIR / "_archived_v1" / "\u7b2c002\u7ae0"
        archived.mkdir(parents=True, exist_ok=True)
        (archived / "\u7b2c002\u7ae0.txt").write_text("\u5f52\u6863\u65e7\u7a3f\u3002", encoding="utf-8")

        draft = root / CHAPTER_DIR / "cluster_001_draft"
        draft.mkdir(parents=True, exist_ok=True)
        (draft / "\u7b2c003\u7ae0.txt").write_text("\u8349\u7a3f\u4e2d\u95f4\u4ea7\u7269\u3002", encoding="utf-8")

        report = eb.export_book(root, with_compliance_checklist=False)
        assert report["chapters"] == 1
        text = _read_export(report)
        assert "\u6b63\u5178\u6b63\u6587" in text
        assert "\u5f52\u6863\u65e7\u7a3f" not in text
        assert "\u8349\u7a3f\u4e2d\u95f4\u4ea7\u7269" not in text


def test_compliance_checklist_default_produced_after_clean_export() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u3002", title="\u4e00")

        report = eb.export_book(root)
        compliance = Path(report["compliance_checklist"])
        assert compliance.name == COMPLIANCE_FILENAME
        assert compliance.exists()
        assert "\u53d1\u5e03\u524d\u5408\u89c4\u81ea\u67e5" in compliance.read_text(encoding="utf-8")


def test_compliance_checklist_can_disable() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u3002", title="\u4e00")

        report = eb.export_book(root, with_compliance_checklist=False)
        assert report["compliance_checklist"] is None
        assert not (root / "exports" / COMPLIANCE_FILENAME).exists()


def test_adaptation_kit_flag_produces_kit_after_clean_export() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "\u4e66"
        root.mkdir()
        _mk_chapter(root, 1, "\u6b63\u6587\u3002", title="\u4e00")
        db_dir = root / DB_DIR
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "\u4eba\u7269\u5361.json").write_text(
            json.dumps(
                {"characters": [{"id": "A", "name": "\u7532", "role": "\u4e3b\u89d2", "arc": "\u6210\u957f"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        report = eb.export_book(root, with_adaptation_kit=True, with_compliance_checklist=False)
        assert report["adaptation_kit"] is not None
