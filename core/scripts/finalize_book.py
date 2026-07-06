"""Final book integrity gate.

This module is the export preflight gate for the single writing chain. It only
scans and reports. It never edits chapters, never deletes pending tails, and
never offers an export-stage repair path.

Exit codes:
  0: project is export-ready
  2: project path is invalid or export-blocking integrity debt exists
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio  # noqa: E402

CHAPTER_DIR = "\u7ae0\u8282"
DEFAULT_RECONCILE_THRESHOLD = 500


def _cluster_key_int(key: str) -> int | None:
    match = re.search(r"(\d+)", str(key))
    return int(match.group(1)) if match else None


def _draft_dir(project_root: Path, key: str) -> Path:
    return project_root / CHAPTER_DIR / f"cluster_{key}_draft"


def _read_text_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _list_cluster_keys(project_root: Path) -> list[str]:
    base = project_root / CHAPTER_DIR
    if not base.is_dir():
        return []
    keys: list[str] = []
    for child in base.iterdir():
        match = re.match(r"^cluster_(.+)_draft$", child.name)
        if child.is_dir() and match:
            keys.append(match.group(1))
    keys.sort(key=lambda k: (_cluster_key_int(k) if _cluster_key_int(k) is not None else 1 << 30, k))
    return keys


def scan_pending_tails(project_root: Path) -> list[dict[str, Any]]:
    """Return every non-empty pending_tail file as export-blocking debt."""
    project_root = Path(project_root)
    all_keys = _list_cluster_keys(project_root)
    numeric_keys = [_cluster_key_int(k) for k in all_keys if _cluster_key_int(k) is not None]
    last_numeric_key = max(numeric_keys, default=None)

    pending_tails: list[dict[str, Any]] = []
    for key in all_keys:
        pending_tail = _draft_dir(project_root, key) / f"cluster_{key}_pending_tail.txt"
        if not pending_tail.is_file():
            continue
        text = _read_text_or_empty(pending_tail)
        cjk = cio.count_cjk(text)
        if cjk == 0:
            continue
        key_int = _cluster_key_int(key)
        pending_tails.append(
            {
                "cluster_key": key,
                "path": str(pending_tail.relative_to(project_root)),
                "abs_path": str(pending_tail),
                "cjk": cjk,
                "chars": cio.count_words(text),
                "is_last_cluster": key_int is not None and key_int == last_numeric_key,
            }
        )
    return pending_tails


def _exported_chapters_cjk(project_root: Path) -> tuple[int, int]:
    total = 0
    count = 0
    seen: set[int] = set()
    chapters_root = project_root / CHAPTER_DIR
    if not chapters_root.is_dir():
        return 0, 0

    for chapter_dir in sorted(chapters_root.iterdir()):
        match = re.fullmatch(r"\u7b2c(\d{3})\u7ae0", chapter_dir.name)
        if not chapter_dir.is_dir() or not match:
            continue
        chapter_no = int(match.group(1))
        body_file = chapter_dir / f"\u7b2c{chapter_no:03d}\u7ae0.txt"
        if chapter_no in seen or not body_file.is_file():
            continue
        seen.add(chapter_no)
        total += cio.count_cjk(cio._strip_changes(_read_text_or_empty(body_file)))
        count += 1
    return total, count


def _draft_total_cjk(project_root: Path) -> tuple[int, int]:
    draft_total = 0
    pending_tail_total = 0
    for key in _list_cluster_keys(project_root):
        draft_path = _draft_dir(project_root, key) / f"cluster_{key}_draft.txt"
        if draft_path.is_file():
            draft_total += cio.count_cjk(cio._strip_changes(_read_text_or_empty(draft_path)))
    for pending_tail in scan_pending_tails(project_root):
        pending_tail_total += int(pending_tail.get("cjk") or 0)
    return draft_total, pending_tail_total


def reconcile(project_root: Path, threshold: int = DEFAULT_RECONCILE_THRESHOLD) -> dict[str, Any]:
    """Compare exported body volume with unresolved pending_tail debt."""
    project_root = Path(project_root)
    exported_cjk, exported_chapters = _exported_chapters_cjk(project_root)
    draft_total, pending_tail_total = _draft_total_cjk(project_root)
    return {
        "exported_chapters": exported_chapters,
        "exported_cjk": exported_cjk,
        "draft_total_cjk": draft_total,
        "pending_tail_cjk": pending_tail_total,
        "threshold": threshold,
        "silent_loss_lower_bound_cjk": pending_tail_total,
        "over_threshold": pending_tail_total > threshold,
        "blocking": pending_tail_total > 0,
    }


def build_report(project_root: Path, threshold: int = DEFAULT_RECONCILE_THRESHOLD) -> dict[str, Any]:
    project_root = Path(project_root)
    pending_tails = scan_pending_tails(project_root)
    rec = reconcile(project_root, threshold)
    issues: list[dict[str, Any]] = []
    if pending_tails:
        issues.append(
            {
                "code": "PENDING_TAIL_PRESENT",
                "level": "hard",
                "detail": [
                    {"cluster_key": item["cluster_key"], "path": item["path"], "cjk": item["cjk"]}
                    for item in pending_tails
                ],
            }
        )
    if rec["over_threshold"]:
        issues.append(
            {
                "code": "PENDING_TAIL_SILENT_LOSS_OVER_THRESHOLD",
                "level": "hard",
                "detail": {
                    "silent_loss_lower_bound_cjk": rec["silent_loss_lower_bound_cjk"],
                    "threshold": threshold,
                },
            }
        )
    return {
        "scanner": "finalize_book",
        "schema_version": "2.0",
        "gate_level": "hard",
        "project": str(project_root),
        "pending_tails": pending_tails,
        "pending_tail_count": len(pending_tails),
        "reconcile": rec,
        "issues": issues,
        "ok": not issues,
        "has_findings": bool(issues),
    }


def _print_human(report: dict[str, Any]) -> None:
    if report["ok"]:
        print("[OK] finalize_book: no pending_tail debt")
        return

    print("[FATAL] finalize_book: export-blocking integrity debt", file=sys.stderr)
    for issue in report["issues"]:
        print(f"  - {issue['code']}: {issue['detail']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export preflight integrity gate")
    parser.add_argument("project", help="Novel project path")
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_RECONCILE_THRESHOLD,
        help=f"CJK threshold for pending-tail loss reporting, default {DEFAULT_RECONCILE_THRESHOLD}",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project path does not exist: {args.project}", file=sys.stderr)
        sys.exit(2)

    report = build_report(project_root, args.threshold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    sys.exit(0 if report["ok"] else 2)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
