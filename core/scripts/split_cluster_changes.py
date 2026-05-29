#!/usr/bin/env python3
"""split_cluster_changes.py — v24 cluster_changes.json 拆分到 N 个 per-chapter _changes.json

输入：
- 章节/cluster_{key}_draft/cluster_{key}_changes.json
- _数据库/.wal/splitter_cluster_{key}_decisions.json（splitter 切点 + ch_range）

输出：
- 章节/第NNN章/第NNN章_changes.json × N

策略：
- 整 cluster 的 factual / self_eval 段平铺到每章（最简实现 v1）
- v2 可基于切点把 factual.key_events 按章节范围分配（需要 events 标注 ch_anchor）

CLI:
    python split_cluster_changes.py <project> --cluster <key>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import atomic_json  # noqa: E402  原子写 事件簇.json 回填
except Exception:  # pragma: no cover
    atomic_json = None
try:
    import cluster_lookup as _cl  # noqa: E402  cluster_id 归一化
except Exception:  # pragma: no cover
    _cl = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _norm_cid(x):
    if _cl is not None:
        n = _cl.normalize_cluster_id(x)
        if n:
            return n
    s = str(x)
    digits = "".join(ch for ch in s if ch.isdigit())
    return f"cluster_{int(digits):03d}" if digits else s


def writeback_event_cluster_range(project_root: Path, cluster_key: str, chapters: list) -> dict:
    """v2 权威源回填（2026-05-29）：切章后把 splitter 的真实 [min,max] 写回
    事件簇.json.clusters[N].chapter_range，使 事件簇 成为切章后 chapter_range 的唯一权威。

    同时检测与其它 cluster 的边界重叠（相邻 cluster 不应共享章号），warn 但不强改其它 cluster。
    """
    if not chapters:
        return {"ok": False, "error": "chapters 为空，跳过回填"}
    lo, hi = int(min(chapters)), int(max(chapters))
    shi_path = project_root / "_数据库" / "事件簇.json"
    shi = load_json(shi_path)
    if not isinstance(shi, dict) or "clusters" not in shi:
        return {"ok": False, "error": "事件簇.json 不存在或无 clusters"}

    target = _norm_cid(cluster_key)
    hit = None
    warnings = []
    for c in shi.get("clusters", []):
        if not isinstance(c, dict):
            continue
        if _norm_cid(c.get("cluster_id")) == target:
            hit = c
            continue
        # 重叠检测（与其它已落 range 的 cluster）
        cr = c.get("chapter_range")
        if isinstance(cr, list) and len(cr) == 2 and isinstance(cr[0], int) and isinstance(cr[1], int):
            if not (hi < cr[0] or lo > cr[1]):
                warnings.append(f"{c.get('cluster_id')} range {cr} 与本 cluster [{lo},{hi}] 重叠")
    if hit is None:
        return {"ok": False, "error": f"事件簇.json 未找到 {target}"}

    old = hit.get("chapter_range")
    hit["chapter_range"] = [lo, hi]
    if atomic_json is not None:
        atomic_json.atomic_write_json(shi_path, shi)
    else:
        shi_path.write_text(json.dumps(shi, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "cluster": target, "old": old, "new": [lo, hi], "warnings": warnings}


def split_changes(project_root: Path, cluster_key: str) -> dict:
    cluster_draft_dir = project_root / "章节" / f"cluster_{cluster_key}_draft"
    cluster_changes_path = cluster_draft_dir / f"cluster_{cluster_key}_changes.json"
    splitter_decisions_path = project_root / "_数据库" / ".wal" / f"splitter_cluster_{cluster_key}_decisions.json"

    if not cluster_changes_path.exists():
        return {"ok": False, "error": f"cluster_changes 不存在: {cluster_changes_path}"}
    if not splitter_decisions_path.exists():
        return {"ok": False, "error": f"splitter_decisions 不存在: {splitter_decisions_path}"}

    cluster_changes = load_json(cluster_changes_path)
    splitter_decisions = load_json(splitter_decisions_path)

    # 取 chapter_range
    # v26 修复: splitter 实际输出字段名兼容（ch_range / chapter_range / cluster_range / chapters_split）
    chapter_range = (
        splitter_decisions.get("chapter_range")
        or splitter_decisions.get("cluster_range")
        or splitter_decisions.get("ch_range")
    )
    chapters_split = splitter_decisions.get("chapters_split")

    if isinstance(chapters_split, list) and chapters_split:
        # splitter 直接给的章节号列表（最可靠）
        chapters = [int(x) for x in chapters_split]
    elif isinstance(chapter_range, str) and "-" in chapter_range:
        start, end = chapter_range.split("-")
        chapters = list(range(int(start), int(end) + 1))
    elif isinstance(chapter_range, list) and len(chapter_range) == 2:
        chapters = list(range(int(chapter_range[0]), int(chapter_range[1]) + 1))
    else:
        # v26 fallback: 从 事件簇.json.clusters[N].chapter_range 取（最权威源）
        shi_path = project_root / "_数据库" / "事件簇.json"
        if shi_path.exists():
            try:
                import json as _json
                shi = _json.loads(shi_path.read_text(encoding="utf-8"))
                for c in shi.get("clusters", []):
                    cid = c.get("cluster_id", "").replace("cluster_", "")
                    if cid == cluster_key.replace("cluster_", ""):
                        cr = c.get("chapter_range") or []
                        if isinstance(cr, list) and len(cr) == 2:
                            chapters = list(range(cr[0], cr[1] + 1))
                            break
                else:
                    chapters = []
            except Exception:
                chapters = []
        else:
            chapters = []
        if not chapters:
            # 最后兜底（cluster_001 特殊 · 但拒绝盲写 ch1-4 给非 cluster_001）
            target_chapters = splitter_decisions.get("target_chapters", 4)
            ch_start = splitter_decisions.get("ch_start")
            if ch_start is None:
                return {"ok": False, "error": f"无法确定 cluster_{cluster_key} chapter_range · splitter_decisions 缺 ch_range/chapters_split · 事件簇.json fallback 失败"}
            chapters = list(range(int(ch_start), int(ch_start) + int(target_chapters)))

    written = []
    for n in chapters:
        ch_dir = project_root / "章节" / f"第{n:03d}章"
        ch_changes_path = ch_dir / f"第{n:03d}章_changes.json"
        if not ch_dir.exists():
            continue  # splitter 没切出来这章，跳过

        # v1 策略：平铺整 cluster_changes 到每章
        ch_data = {
            "schema_version": "v18",
            "chapter": n,
            "title": "",  # gen_chapter_titles 写
            "factual": cluster_changes.get("factual", {}),
            "self_eval": cluster_changes.get("self_eval", {}),
            "_doc": f"v24 cluster_{cluster_key} ch{n} _changes (从 cluster_changes.json 平铺 · ECAS 模式 cluster 级单源)",
            "_source_cluster_changes": str(cluster_changes_path)
        }
        ch_changes_path.write_text(json.dumps(ch_data, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(ch_changes_path))

    # v2 权威源回填（2026-05-29）：把切章真实 [min,max] 写回 事件簇.json（唯一权威）
    writeback = writeback_event_cluster_range(project_root, cluster_key, chapters)

    return {
        "ok": True,
        "cluster_key": cluster_key,
        "chapter_range": chapters,
        "written_count": len(written),
        "written": written,
        "event_cluster_writeback": writeback,
    }


def main():
    parser = argparse.ArgumentParser(description="v24 cluster_changes.json 拆分到 per-chapter _changes")
    parser.add_argument("project", help="项目路径")
    parser.add_argument("--cluster", required=True, help="cluster key (e.g. 001 or cluster_001)")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    cluster_key = args.cluster.replace("cluster_", "")

    result = split_changes(project_root, cluster_key)
    if result.get("ok"):
        print(f"[OK] cluster_{cluster_key} _changes 拆分到 {result['written_count']} 章")
        for p in result["written"]:
            print(f"     + {p}")
        wb = result.get("event_cluster_writeback") or {}
        if wb.get("ok"):
            print(f"     ↩ 事件簇.json 回填 chapter_range: {wb.get('old')} → {wb.get('new')}（权威源）")
            for w in wb.get("warnings", []):
                print(f"     ⚠ 边界重叠: {w}")
        elif wb.get("error"):
            print(f"     ⚠ 事件簇 回填跳过: {wb.get('error')}")
        return 0
    else:
        print(f"[FAIL] {result.get('error')}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
