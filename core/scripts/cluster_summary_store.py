"""cluster_summary_store.py — cluster 账本（故事块摘要.json）唯一原子写入器

v2 cluster 化「摘要驱动」架构（2026-05-29）：
账本有多个写入方（cluster_summary_builder 产 chapters[ch] 富摘要 / judge 化写 judge_grade
等），为避免读-改-写竞态互相覆盖（系统审计 P0：非原子写丢库），所有写入统一走本模块的
`upsert_cluster`，内部用 atomic_json 原子写 + 深合并。

字段契约见 cluster_summary_reader.py 顶部 docstring（单一来源）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json  # noqa: E402  原子写
import cluster_lookup  # noqa: E402  cluster_id 归一化
from cluster_summary_reader import SUMMARY_FILENAME, load_summary  # noqa: E402

__all__ = ["upsert_cluster", "patch_chapter"]


def _db_dir(project_root) -> Path:
    root = Path(project_root)
    return root if root.name == "_数据库" else root / "_数据库"


def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并 patch 进 base：dict 递归合，其它（含 list）直接覆盖。"""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _apply_upsert(summary: dict, target_norm: str, patch: dict) -> dict:
    """纯内存合并：把 patch 深合并进 summary.clusters 里 target_norm 对应记录（不存在则建）。

    2026-05-29 复审修复（M1）：抽成纯函数，由 safe_update_json 在 with_file_lock 内调用，
    保证「读-改-写」整体在锁内完成，跨进程不丢更新。
    """
    if not isinstance(summary, dict):
        summary = {"schema_version": "v2.cluster", "clusters": []}
    clusters = summary.setdefault("clusters", [])
    if not isinstance(clusters, list):
        clusters = []
        summary["clusters"] = clusters

    rec = None
    for c in clusters:
        if not isinstance(c, dict):
            continue
        if cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == target_norm:
            rec = c
            break
    if rec is None:
        rec = {"cluster_id": target_norm, "title": ""}
        clusters.append(rec)

    _deep_merge(rec, patch)
    # cluster_id 始终保持归一化形态
    rec["cluster_id"] = target_norm
    summary.setdefault("schema_version", "v2.cluster")
    return summary


def upsert_cluster(project_root, cluster_id: str, patch: dict) -> dict:
    """把 patch 深合并进账本里 cluster_id 对应的记录（不存在则新建），原子落盘。

    cluster_id 归一化比对（int 6 / "6" / "cluster_006" 视为同一 cluster）。
    返回合并后的整份账本 dict。

    2026-05-29 复审修复（M1）：旧实现 load_summary → 内存合并 → atomic_write_json 三步无锁，
    两个写入方（builder / judge 化）并发时各自读到旧账本、各自写回 → 后写覆盖先写，丢更新。
    新实现用 atomic_json.safe_update_json 把整个「读-改-写」包进 with_file_lock：
    锁内 load 当前最新账本，合并本次 patch，原子写回。第二个写者必然读到第一个写者的结果，零丢失。
    """
    target_norm = cluster_lookup.normalize_cluster_id(cluster_id) or str(cluster_id)
    target_path = _db_dir(project_root) / SUMMARY_FILENAME

    # safe_update_json 在锁内把 target 反序列化成 current 传入 update_fn；账本损坏/缺失时给空骨架默认。
    # 我们仍要复用 load_summary 的「dict 校验」语义，故 update_fn 内对 current 做一次 dict 兜底。
    return atomic_json.safe_update_json(
        target_path,
        lambda current: _apply_upsert(
            current if isinstance(current, dict) else {"schema_version": "v2.cluster", "clusters": []},
            target_norm,
            patch,
        ),
        default={"schema_version": "v2.cluster", "clusters": []},
    )


def patch_chapter(project_root, cluster_id: str, ch: int, chapter_patch: dict) -> dict:
    """便捷：把 chapter_patch 合并进 cluster 的 chapters[str(ch)]。"""
    return upsert_cluster(project_root, cluster_id, {"chapters": {str(ch): chapter_patch}})


if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    upsert_cluster(d, "cluster_002", {"title": "测试块", "chapter_range": [5, 8], "word_count": 12000})
    patch_chapter(d, 2, 5, {"cjk_count": 3200, "scene_type": "悬疑"})
    patch_chapter(d, "cluster_002", 6, {"cjk_count": 3400})
    # 二次 upsert 合并不丢字段
    upsert_cluster(d, "cluster_002", {"judge_grade": "B"})
    s = load_summary(d)
    rec = s["clusters"][0]
    assert rec["cluster_id"] == "cluster_002"
    assert rec["word_count"] == 12000 and rec["judge_grade"] == "B"
    assert rec["chapters"]["5"]["cjk_count"] == 3200 and rec["chapters"]["5"]["scene_type"] == "悬疑"
    assert rec["chapters"]["6"]["cjk_count"] == 3400
    assert len(s["clusters"]) == 1  # 同 cluster 不重复建
    print("[OK] cluster_summary_store self-test passed")
