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


def upsert_cluster(project_root, cluster_id: str, patch: dict) -> dict:
    """把 patch 深合并进账本里 cluster_id 对应的记录（不存在则新建），原子落盘。

    cluster_id 归一化比对（int 6 / "6" / "cluster_006" 视为同一 cluster）。
    返回合并后的整份账本 dict。
    """
    summary = load_summary(project_root)
    clusters = summary.setdefault("clusters", [])
    target_norm = cluster_lookup.normalize_cluster_id(cluster_id) or str(cluster_id)

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
    atomic_json.atomic_write_json(_db_dir(project_root) / SUMMARY_FILENAME, summary)
    return summary


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
