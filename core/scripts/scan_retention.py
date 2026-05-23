"""scan_retention.py — 跨章扫描报告老化清理（v19.4 新增）"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--keep", type=int, default=5)
    args = ap.parse_args()

    project_root = Path(args.project)
    scan_dir = project_root / "_数据库" / ".cross_chapter_scan"
    if not scan_dir.is_dir():
        print(f"[SKIP] {scan_dir} 不存在")
        sys.exit(0)

    groups: dict[str, list[Path]] = {}
    for f in scan_dir.glob("*.json"):
        m = re.match(r"^([a-z_]+?)_\d{8}_\d{6}\.json$", f.name)
        if m:
            prefix = m.group(1)
            groups.setdefault(prefix, []).append(f)

    deleted_count = 0
    kept_count = 0
    for prefix, files in groups.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        to_keep = files[:args.keep]
        to_delete = files[args.keep:]
        for f in to_delete:
            try:
                f.unlink()
                deleted_count += 1
            except OSError as e:
                print(f"  [WARN] 删除失败 {f.name}: {e}", file=sys.stderr)
        kept_count += len(to_keep)
        print(f"  [{prefix:<20}] 保留 {len(to_keep)} / 删除 {len(to_delete)}")

    print(f"\n[scan_retention] 共保留 {kept_count} 份，删除 {deleted_count} 份")
    sys.exit(0)


if __name__ == "__main__":
    main()
