"""cluster_lookup.py — 章号 ⇄ cluster_id 反查共享工具（v2 cluster 修复）

背景（2026-05-29 系统审计发现的头号系统性 bug）：
v2 cluster 迁移时，多处代码把「章号」直接当成「cluster 号」机械拼成
`f"cluster_{ch:03d}"`。但一个 cluster 通常含 2-6 章，章号 ≠ cluster 号
（第 7 章极可能属于 cluster_002 而非 cluster_007）。这个语义错误散布在
save_state / migrate_data_model_v2 / world_evolution_engine /
character_lazy_spawn / state_tracker / build_manifest 等处，导致迁移后
伏笔 setup_cluster / 角色 first_appear_cluster / 道具 obtained_cluster
等正典字段普遍指向错误 cluster。

本模块提供单一权威的 章号→cluster_id 反查，所有需要由章号推 cluster 归属
的地方都应改用 `ch_to_cluster_id`，不得再用 `f"cluster_{ch:03d}"`。

反查链（与 build_manifest._current_cluster_id 一致）：
  1. 进度.json → cluster_blueprint[cid].chapter_range = [lo, hi]
  2. 进度.json → cluster_blueprint[cid].scene_storyboard[].ch
  3. 事件簇.json → clusters[].chapter_range + cluster_id

fluid v27 注意：章数由 splitter step 6 决定，chapter_range 可能尚未回填。
反查不到时返回 None（调用方需自行决定 fallback，禁止静默回退到
`cluster_{ch}` 这种错误推断 —— 宁可标 unknown 也不要写错 cluster）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

__all__ = [
    "normalize_cluster_id",
    "cluster_num",
    "ch_to_cluster_id",
    "cluster_id_to_range",
]


def _load_json(p: Path, default=None):
    try:
        if not Path(p).exists():
            return default
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _db_dir(project_root) -> Path:
    """定位 _数据库 目录。接受 项目根 或 直接传 _数据库 路径。"""
    root = Path(project_root)
    if root.name == "_数据库":
        return root
    cand = root / "_数据库"
    return cand if cand.exists() else root


def normalize_cluster_id(value) -> str | None:
    """把各种形态的 cluster 标识归一化为 'cluster_NNN'（零填充 3 位）。

    支持：6 / "6" / "cluster_6" / "cluster_006" / "cluster_002" → "cluster_006"/...
    无法解析数字时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # 防 True/False 被当 int
        return None
    if isinstance(value, int):
        return f"cluster_{value:03d}"
    if isinstance(value, str):
        m = re.search(r"(\d+)", value)
        if m:
            return f"cluster_{int(m.group(1)):03d}"
    return None


def cluster_num(cluster_id) -> int | None:
    """从 cluster_id 提取数字序号。无法解析返回 None。"""
    if cluster_id is None:
        return None
    if isinstance(cluster_id, bool):
        return None
    if isinstance(cluster_id, int):
        return cluster_id
    if isinstance(cluster_id, str):
        m = re.search(r"(\d+)", cluster_id)
        if m:
            return int(m.group(1))
    return None


def _iter_blueprint_ranges(project_root):
    """yield (cluster_id, [lo, hi] | None, scene_chs:set) from 进度.cluster_blueprint."""
    db = _db_dir(project_root)
    prog = _load_json(db / "进度.json", {}) or {}
    for cid, cdata in (prog.get("cluster_blueprint", {}) or {}).items():
        if not isinstance(cdata, dict):
            continue
        cr = cdata.get("chapter_range") or []
        rng = cr if (isinstance(cr, list) and len(cr) == 2) else None
        scene_chs = set()
        for sb in cdata.get("scene_storyboard", []) or []:
            ch = sb.get("ch") if isinstance(sb, dict) else None
            if isinstance(ch, int):
                scene_chs.add(ch)
        yield cid, rng, scene_chs


def _iter_event_cluster_ranges(project_root):
    """yield (cluster_id, [lo, hi] | None) from 事件簇.clusters."""
    db = _db_dir(project_root)
    ec = _load_json(db / "事件簇.json", {}) or {}
    for c in ec.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("cluster_id")
        cr = c.get("chapter_range") or []
        rng = cr if (isinstance(cr, list) and len(cr) == 2) else None
        yield cid, rng


def ch_to_cluster_id(project_root, ch: int) -> str | None:
    """反查第 ch 章所属的 cluster_id。查不到返回 None（禁止伪造 cluster_{ch}）。

    project_root: 项目根目录 或 _数据库 目录均可。
    """
    if not isinstance(ch, int):
        n = cluster_num(ch)
        if n is None:
            return None
        ch = n

    # 1) cluster_blueprint.chapter_range
    sb_fallback = None
    for cid, rng, scene_chs in _iter_blueprint_ranges(project_root):
        if rng and rng[0] <= ch <= rng[1]:
            return cid
        if ch in scene_chs:
            sb_fallback = sb_fallback or cid
    if sb_fallback:
        return sb_fallback

    # 2) 事件簇.json.chapter_range
    for cid, rng in _iter_event_cluster_ranges(project_root):
        if rng and rng[0] <= ch <= rng[1]:
            return cid

    return None


def cluster_id_to_range(project_root, cluster_id) -> list | None:
    """取 cluster_id 的 [lo, hi] 章范围。查不到返回 None。"""
    target = normalize_cluster_id(cluster_id)
    if target is None:
        return None
    for cid, rng, _ in _iter_blueprint_ranges(project_root):
        if normalize_cluster_id(cid) == target and rng:
            return rng
    for cid, rng in _iter_event_cluster_ranges(project_root):
        if normalize_cluster_id(cid) == target and rng:
            return rng
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        root, ch = sys.argv[1], int(sys.argv[2])
        print(f"ch {ch} -> {ch_to_cluster_id(root, ch)}")
    else:
        # 自测
        assert normalize_cluster_id(6) == "cluster_006"
        assert normalize_cluster_id("cluster_2") == "cluster_002"
        assert normalize_cluster_id("cluster_006") == "cluster_006"
        assert normalize_cluster_id(None) is None
        assert cluster_num("cluster_012") == 12
        assert cluster_num(6) == 6
        print("[OK] cluster_lookup self-test passed")
