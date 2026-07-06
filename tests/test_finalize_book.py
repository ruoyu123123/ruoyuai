"""Regression tests for the hard finalize_book export preflight gate."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_io as cio  # noqa: E402
import finalize_book as fb  # noqa: E402

CHAPTER_DIR = "\u7ae0\u8282"


def _mk_chapter(root: Path, ch: int, body: str) -> None:
    cio.write_body(root, ch, body)


def _mk_cluster_draft(root: Path, key: str, draft_body: str) -> None:
    draft_dir = root / CHAPTER_DIR / f"cluster_{key}_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / f"cluster_{key}_draft.txt").write_text(draft_body, encoding="utf-8")


def _mk_pending_tail(root: Path, key: str, body: str) -> Path:
    draft_dir = root / CHAPTER_DIR / f"cluster_{key}_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    path = draft_dir / f"cluster_{key}_pending_tail.txt"
    path.write_text(body, encoding="utf-8")
    return path


TAIL_TEXT = "\u5c3e\u6bb5\u8fd8\u6ca1\u6709\u5165\u7ae0\u3002" * 120


def test_last_cluster_pending_tail_is_hard_finding() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_chapter(root, 1, "\u6b63\u6587\u4e00\u3002" * 100)
        _mk_cluster_draft(root, "001", "\u8349\u7a3f\u6b63\u6587\u3002" * 100)
        _mk_pending_tail(root, "001", TAIL_TEXT)

        pending_tails = fb.scan_pending_tails(root)
        assert len(pending_tails) == 1
        assert pending_tails[0]["cluster_key"] == "001"
        assert pending_tails[0]["is_last_cluster"] is True

        report = fb.build_report(root)
        assert report["ok"] is False
        assert report["gate_level"] == "hard"
        assert report["pending_tail_count"] == 1
        assert any(issue["code"] == "PENDING_TAIL_PRESENT" for issue in report["issues"])
        assert report["reconcile"]["silent_loss_lower_bound_cjk"] == pending_tails[0]["cjk"]


def test_successor_wal_does_not_make_pending_tail_exportable() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_cluster_draft(root, "001", "\u4e0a\u4e00\u5757\u8349\u7a3f\u3002" * 50)
        _mk_pending_tail(root, "001", TAIL_TEXT)
        _mk_cluster_draft(root, "002", "\u4e0b\u4e00\u5757\u8349\u7a3f\u3002" * 50)

        pending_tails = fb.scan_pending_tails(root)
        assert len(pending_tails) == 1
        assert pending_tails[0]["cluster_key"] == "001"
        assert fb.build_report(root)["ok"] is False


def test_successor_prepend_match_does_not_make_pending_tail_exportable() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_pending_tail(root, "001", TAIL_TEXT)
        _mk_cluster_draft(root, "002", TAIL_TEXT + "\n\n" + "\u65b0\u5185\u5bb9\u3002" * 50)

        pending_tails = fb.scan_pending_tails(root)
        assert len(pending_tails) == 1
        assert pending_tails[0]["cluster_key"] == "001"
        assert fb.build_report(root)["ok"] is False


def test_successor_exists_but_tail_not_resolved_is_blocking() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_pending_tail(root, "004", TAIL_TEXT)
        _mk_cluster_draft(root, "005", "\u72ec\u7acb\u65b0\u8349\u7a3f\u3002" * 80)

        pending_tails = fb.scan_pending_tails(root)
        assert len(pending_tails) == 1
        assert pending_tails[0]["cluster_key"] == "004"
        assert pending_tails[0]["is_last_cluster"] is False


def test_empty_pending_tail_is_not_flagged() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_pending_tail(root, "001", "\n  \n")
        assert fb.scan_pending_tails(root) == []
        assert fb.build_report(root)["ok"] is True


def test_finalize_book_has_no_flush_api() -> None:
    assert not hasattr(fb, "flush_orphan")


def test_cli_blocks_by_default_when_pending_tail_exists() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_pending_tail(root, "001", TAIL_TEXT)

        stderr = io.StringIO()
        try:
            with redirect_stderr(stderr):
                fb.main([str(root)])
            raise AssertionError("finalize_book should exit")
        except SystemExit as exc:
            assert exc.code == 2
        assert "[FATAL]" in stderr.getvalue()


def test_cli_json_ok_exit0_when_clean() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _mk_cluster_draft(root, "001", "\u5e72\u51c0\u8349\u7a3f\u3002" * 20)

        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                fb.main([str(root), "--json"])
            raise AssertionError("finalize_book should exit")
        except SystemExit as exc:
            assert exc.code == 0
        report = json.loads(stdout.getvalue())
        assert report["ok"] is True
        assert report["gate_level"] == "hard"
