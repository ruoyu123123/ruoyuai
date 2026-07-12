"""Deterministic book export for the single writing chain.

The exporter is a pure delivery step. It reads completed standard chapter
files, verifies the book-level contract, then writes the final artifact. It does
not repair, clean, skip, or reinterpret upstream chapter output.
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
import finalize_book as fb  # noqa: E402

CHAPTER_DIR = "\u7ae0\u8282"
DB_DIR = "_\u6570\u636e\u5e93"
EXPORTS_DIR = "exports"
COMPLIANCE_FILENAME = "\u53d1\u5e03\u524d\u5408\u89c4\u81ea\u67e5.md"

CHAPTER_DIR_RE = re.compile(r"^\u7b2c(\d{3})\u7ae0$")
CHAPTER_FILE_RE = re.compile(r"^\u7b2c(\d{3})\u7ae0\.txt$")
TITLE_LINE_RE = re.compile(r"^\u7b2c0*\d+\u7ae0(?:\s+.+)?$")

# Machine-metadata markers that must never leak into an exported chapter body.
# chapter_io no longer owns any body/CHANGES marker syntax (bodies are always
# plain prose; CHANGES lives only in the sibling _changes.json), so this
# export-time guard keeps its own literal list rather than importing symbols
# chapter_io does not expose.
FORBIDDEN_BODY_MARKERS = ("---CHANGES_FACTUAL---", "---CHANGES---", "---CHANGES_SELF_EVAL---")


class ExportIntegrityError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("book export integrity failed")
        self.report = report


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return data


def _is_excluded(path: Path, project_root: Path) -> bool:
    try:
        parts = path.relative_to(project_root).parts
    except ValueError:
        parts = path.parts
    parent_parts = parts[:-1]
    return any(part.startswith("_") for part in parent_parts) or any(part.endswith("_draft") for part in parent_parts)


def scan_chapter_sources(project_root: Path) -> dict[int, list[Path]]:
    """Return standard completed chapter files grouped by chapter number."""
    project_root = Path(project_root)
    chapters_root = project_root / CHAPTER_DIR
    if not chapters_root.is_dir():
        return {}

    result: dict[int, list[Path]] = {}
    for path in sorted(chapters_root.rglob("*.txt")):
        if _is_excluded(path, project_root):
            continue
        file_match = CHAPTER_FILE_RE.fullmatch(path.name)
        dir_match = CHAPTER_DIR_RE.fullmatch(path.parent.name)
        if not file_match or not dir_match:
            continue
        file_no = int(file_match.group(1))
        dir_no = int(dir_match.group(1))
        if file_no != dir_no:
            continue
        result.setdefault(file_no, []).append(path)
    return result


def discover_chapters(project_root: Path) -> dict[int, Path]:
    return {chapter_no: paths[0] for chapter_no, paths in scan_chapter_sources(project_root).items()}


def find_missing_chapter_dirs(project_root: Path, found: dict[int, Path]) -> list[int]:
    chapters_root = Path(project_root) / CHAPTER_DIR
    missing: list[int] = []
    if not chapters_root.is_dir():
        return missing
    for child in chapters_root.iterdir():
        match = CHAPTER_DIR_RE.fullmatch(child.name)
        if not child.is_dir() or not match:
            continue
        chapter_no = int(match.group(1))
        expected = child / f"\u7b2c{chapter_no:03d}\u7ae0.txt"
        if chapter_no not in found or not expected.is_file():
            missing.append(chapter_no)
    return sorted(set(missing))


def read_title(project_root: Path, chapter_no: int) -> str:
    changes = cio.changes_path(project_root, chapter_no)
    if not changes.is_file():
        raise FileNotFoundError(f"missing chapter title metadata: {changes}")
    data = _read_json(changes)
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"missing non-empty title in {changes}")
    return title.strip()


def validate_chapter_body(path: Path, text: str) -> None:
    for marker in FORBIDDEN_BODY_MARKERS:
        if marker in text:
            raise ValueError(f"machine metadata marker found in chapter body: {path}")

    lines = text.splitlines()
    first_nonempty = next((line.strip() for line in lines if line.strip()), "")
    if first_nonempty and TITLE_LINE_RE.fullmatch(first_nonempty):
        raise ValueError(f"chapter body includes a title line; splitter must remove it before export: {path}")


def build_chapter_parts(chapter_no: int, raw_text: str, title: str, source: Path | None = None) -> tuple[str, str]:
    if source is not None:
        validate_chapter_body(source, raw_text)
    header = f"\u7b2c{chapter_no}\u7ae0 {title}"
    body = raw_text.strip()
    if not body:
        raise ValueError(f"empty chapter body: chapter {chapter_no}")
    return header, body


def build_chapter_block(chapter_no: int, raw_text: str, title: str) -> str:
    header, body = build_chapter_parts(chapter_no, raw_text, title)
    return header + "\n\n" + body


def _load_cluster_coverage(project_root: Path) -> set[int] | None:
    try:
        import cluster_lookup as cl  # noqa: WPS433

        covered: set[int] = set()
        for _cluster_id, chapter_range in cl._iter_event_cluster_ranges(project_root):
            if chapter_range:
                covered.update(range(chapter_range[0], chapter_range[1] + 1))
        if not covered:
            for _cluster_id, chapter_range, _storyboard in cl._iter_blueprint_ranges(project_root):
                if chapter_range:
                    covered.update(range(chapter_range[0], chapter_range[1] + 1))
        return covered or None
    except Exception:
        return None


def check_book_integrity(
    project_root: Path,
    parts_by_chapter: dict[int, tuple[str, str, str]],
    full_text: str,
    *,
    read_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    exported = sorted(parts_by_chapter)
    issues: list[dict[str, Any]] = []

    missing_in_sequence: list[int] = []
    if exported:
        present = set(exported)
        missing_in_sequence = [ch for ch in range(min(exported), max(exported) + 1) if ch not in present]

    missing_dirs = find_missing_chapter_dirs(project_root, discover_chapters(project_root))
    duplicate_sources = {
        chapter_no: [str(path) for path in paths]
        for chapter_no, paths in scan_chapter_sources(project_root).items()
        if len(paths) > 1
    }

    body_cjk = sum(cio.count_cjk(body) for _header, body, _block in parts_by_chapter.values())
    header_cjk = sum(cio.count_cjk(header) for header, _body, _block in parts_by_chapter.values())
    full_cjk = cio.count_cjk(full_text)
    conservation_diff = full_cjk - (body_cjk + header_cjk)

    coverage = {"available": False, "uncovered": [], "extra": []}
    covered = _load_cluster_coverage(project_root)
    if covered is not None:
        present = set(exported)
        coverage = {
            "available": True,
            "uncovered": sorted(covered - present),
            "extra": sorted(present - covered),
        }

    if missing_dirs:
        issues.append({"code": "CHAPTER_BODY_FILE_MISSING", "level": "hard", "detail": missing_dirs})
    if missing_in_sequence:
        issues.append({"code": "CHAPTER_MISSING", "level": "hard", "detail": missing_in_sequence})
    if duplicate_sources:
        issues.append({"code": "CHAPTER_DUPLICATE", "level": "hard", "detail": duplicate_sources})
    if read_errors:
        issues.append({"code": "CHAPTER_READ_OR_CONTRACT_ERROR", "level": "hard", "detail": read_errors})
    if conservation_diff != 0:
        issues.append({"code": "WORD_CONSERVATION_DRIFT", "level": "hard", "detail": conservation_diff})
    if coverage["uncovered"]:
        issues.append({"code": "CLUSTER_RANGE_UNCOVERED", "level": "hard", "detail": coverage["uncovered"]})

    hard = any(issue["level"] == "hard" for issue in issues)
    return {
        "verdict": "hard" if hard else "ok",
        "ok": not hard,
        "exported_chapters": exported,
        "continuity": {
            "expected_range": [min(exported), max(exported)] if exported else [],
            "missing": missing_in_sequence,
            "missing_body_files": missing_dirs,
            "duplicates": duplicate_sources,
            "ascending": exported == sorted(exported),
        },
        "coverage": coverage,
        "word_conservation": {
            "body_content_cjk": body_cjk,
            "title_overhead_cjk": header_cjk,
            "full_cjk": full_cjk,
            "diff": conservation_diff,
            "ok": conservation_diff == 0,
        },
        "issues": issues,
    }


def _attach_finalize_issues(integrity: dict[str, Any], finalize_report: dict[str, Any]) -> None:
    for issue in finalize_report.get("issues", []):
        integrity["issues"].append(issue)
    if finalize_report.get("issues"):
        integrity["verdict"] = "hard"
        integrity["ok"] = False


def _print_integrity_report(integrity: dict[str, Any]) -> None:
    if integrity["verdict"] == "ok":
        return
    print(f"[FATAL] export integrity verdict={integrity['verdict']}", file=sys.stderr)
    for issue in integrity.get("issues", []):
        print(f"  - {issue['level']} {issue['code']}: {issue['detail']}", file=sys.stderr)


def build_compliance_checklist() -> str:
    return """# 发布前合规自查

本文件由导出步骤生成，只作为发布前人工检查清单，不替代平台规则、合同或法律意见。

- 已核对目标平台关于 AI 辅助创作披露、字数、章节、标签和敏感内容的最新规则。
- 已确认本书不是批量铺量、搬运、规避检测或模拟登录自动发布产物。
- 已确认简介、标题、标签和正文不包含平台禁止内容。
- 若进行有声、漫画、影视或游戏改编，另行确认授权范围与署名要求。
"""


def _prepare_optional_side_outputs(
    project_root: Path,
    out_path: Path,
    *,
    with_adaptation_kit: bool,
    with_compliance_checklist: bool,
) -> tuple[dict[str, str], dict[str, Any] | None, str | None]:
    files: dict[str, str] = {}
    adaptation = None
    if with_adaptation_kit:
        import adaptation_kit  # noqa: WPS433

        parts = {
            "人物小传.md": adaptation_kit.build_character_bios(project_root),
            "世界设定集.md": adaptation_kit.build_worldbuilding(project_root),
            "故事梗概.md": adaptation_kit.build_synopsis(project_root),
            "高潮伏笔清单.md": adaptation_kit.build_climax_hooks(project_root),
            "有声化适配提示.md": adaptation_kit.build_audio_adaptation_hint(project_root),
        }
        out_dir = project_root / "改编资料包"
        written: dict[str, int] = {}
        for fname, content in parts.items():
            if content and content.strip():
                target = out_dir / fname
                files[str(target)] = content
                written[fname] = len(content)
        adaptation = {"out_dir": str(out_dir), "written": written}

    compliance_path = None
    if with_compliance_checklist:
        compliance = out_path.parent / COMPLIANCE_FILENAME
        files[str(compliance)] = build_compliance_checklist()
        compliance_path = str(compliance)
    return files, adaptation, compliance_path


def _write_success_outputs(final_out: Path, full_text: str, side_files: dict[str, str]) -> None:
    final_out.parent.mkdir(parents=True, exist_ok=True)
    final_out.write_text(full_text, encoding="utf-8")
    for target, content in side_files.items():
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def export_book(
    project_root: Path,
    out_path: str | Path | None = None,
    *,
    with_adaptation_kit: bool = False,
    with_compliance_checklist: bool = True,
) -> dict[str, Any] | None:
    project_root = Path(project_root).resolve()
    found = discover_chapters(project_root)
    if not found:
        integrity = {
            "verdict": "hard",
            "ok": False,
            "exported_chapters": [],
            "continuity": {
                "expected_range": [],
                "missing": [],
                "missing_body_files": [],
                "duplicates": {},
                "ascending": True,
            },
            "coverage": {"available": False, "uncovered": [], "extra": []},
            "word_conservation": {
                "body_content_cjk": 0,
                "title_overhead_cjk": 0,
                "full_cjk": 0,
                "diff": 0,
                "ok": True,
            },
            "issues": [
                {
                    "code": "CHAPTER_SOURCE_MISSING",
                    "level": "hard",
                    "detail": f"no standard chapter body files found under {project_root / CHAPTER_DIR}",
                }
            ],
        }
        _print_integrity_report(integrity)
        raise ExportIntegrityError(
            {
                "out_path": None,
                "chapters": 0,
                "chapter_list": [],
                "total_words": 0,
                "total_cjk": 0,
                "pending_tails": 0,
                "integrity": integrity,
                "adaptation_kit": None,
                "compliance_checklist": None,
            }
        )

    finalize_report = fb.build_report(project_root)

    blocks: list[str] = []
    exported: list[int] = []
    parts_by_chapter: dict[int, tuple[str, str, str]] = {}
    read_errors: list[dict[str, Any]] = []
    for chapter_no in sorted(found):
        source = found[chapter_no]
        try:
            raw_text = _read_text(source)
            title = read_title(project_root, chapter_no)
            header, body = build_chapter_parts(chapter_no, raw_text, title, source)
            block = header + "\n\n" + body
        except Exception as exc:  # noqa: BLE001 - converted into hard integrity detail
            read_errors.append({"chapter": chapter_no, "path": str(source), "error": str(exc)})
            continue
        parts_by_chapter[chapter_no] = (header, body, block)
        blocks.append(block)
        exported.append(chapter_no)

    full_text = "\n\n".join(blocks) + ("\n" if blocks else "")
    integrity = check_book_integrity(project_root, parts_by_chapter, full_text, read_errors=read_errors)
    _attach_finalize_issues(integrity, finalize_report)
    _print_integrity_report(integrity)
    if not integrity["ok"]:
        report = {
            "out_path": None,
            "chapters": len(blocks),
            "chapter_list": exported,
            "total_words": cio.count_words(full_text),
            "total_cjk": cio.count_cjk(full_text),
            "pending_tails": finalize_report.get("pending_tail_count", 0),
            "integrity": integrity,
            "adaptation_kit": None,
            "compliance_checklist": None,
        }
        raise ExportIntegrityError(report)

    if not blocks:
        raise ExportIntegrityError(
            {
                "out_path": None,
                "chapters": 0,
                "chapter_list": [],
                "total_words": 0,
                "total_cjk": 0,
                "pending_tails": finalize_report.get("pending_tail_count", 0),
                "integrity": integrity,
                "adaptation_kit": None,
                "compliance_checklist": None,
            }
        )

    if out_path is None:
        out_dir = project_root / EXPORTS_DIR
        final_out = out_dir / f"{project_root.name}_\u5168\u6587_{len(blocks)}\u7ae0.txt"
    else:
        final_out = Path(out_path)
    side_files, adaptation, compliance_path = _prepare_optional_side_outputs(
        project_root,
        final_out,
        with_adaptation_kit=with_adaptation_kit,
        with_compliance_checklist=with_compliance_checklist,
    )
    _write_success_outputs(final_out, full_text, side_files)

    report = {
        "out_path": str(final_out),
        "chapters": len(blocks),
        "chapter_list": exported,
        "total_words": cio.count_words(full_text),
        "total_cjk": cio.count_cjk(full_text),
        "pending_tails": finalize_report.get("pending_tail_count", 0),
        "integrity": integrity,
        "adaptation_kit": adaptation,
        "compliance_checklist": compliance_path,
    }
    print(f"[OK] exported {len(blocks)} chapters / {report['total_cjk']} CJK -> {final_out}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export a verified completed book to TXT")
    parser.add_argument("project", help="Novel project path")
    parser.add_argument("--out", default=None, help="Output file path")
    parser.add_argument(
        "--adaptation-kit",
        action="store_true",
        help="Also generate deterministic adaptation reference materials after the export passes integrity",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project path does not exist: {args.project}", file=sys.stderr)
        sys.exit(2)

    try:
        export_book(project_root, args.out, with_adaptation_kit=args.adaptation_kit)
    except ExportIntegrityError:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
