"""cluster_lookup.py — 章号 ⇄ cluster_id 反查共享工具

一个 cluster 通常含 2-6 章，章号 ≠ cluster 号（第 7 章可能属于 cluster_002
而非 cluster_007），因此禁止用 `f"cluster_{ch:03d}"` 把章号机械拼成 cluster_id。

本模块提供单一权威的 章号→cluster_id 反查，所有需要由章号推 cluster 归属
的地方都应改用 `ch_to_cluster_id`，不得再用 `f"cluster_{ch:03d}"`。

反查链（事件簇.json 为权威源）：
  1. 事件簇.json → clusters[].chapter_range + cluster_id（权威 · 切章后由
     split_cluster_changes.writeback_event_cluster_range 回填真实范围）
  2. 进度.json → cluster_blueprint[cid].chapter_range（派生/缓存 · 兜底）
  3. 进度.json → cluster_blueprint[cid].scene_storyboard[].ch（兜底）

  注：cluster_blueprint 是 writer 执行蓝图/缓存，不是 chapter_range 权威源；
  故事块本体 = 事件簇.json（cluster_id / status / scope / ME / 切章后真实 range）。

注：章数由 splitter step 6 决定，chapter_range 可能尚未回填。
反查不到时返回 None（调用方需自行决定 fallback，禁止静默回退到
`cluster_{ch}` 这种错误推断 —— 宁可标 unknown 也不要写错 cluster）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

__all__ = [
    "normalize_cluster_id",
    "cluster_num",
    "normalize_blueprint",
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


def infer_cluster_id_by_chapter(ch) -> str:
    """⚠️ 不可信兜底（北极星①·禁用字面 f"cluster_{ch:03d}" 的唯一合法出处）。

    authoritative 反查 `ch_to_cluster_id` 失败时，按章号机械推断 cluster_id。机械拼接被
    北极星①禁止散落在各 consumer——集中到本权威模块为唯一出处。**调用方必须标 inferred**
    （按章号推断 ≠ 真实涌现归属；正常路径一律先走 `ch_to_cluster_id`，本函数仅 dormant/最后兜底）。
    """
    return normalize_cluster_id(ch) or f"cluster_{int(ch):03d}"


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


def normalize_blueprint(prog) -> dict:
    """把 进度.json 的 cluster_blueprint 归一成规范 dict 形态。

    规范形态 = dict（cluster_id -> {"scene_storyboard": [...], "chapter_range": [lo,hi]|缺省}）。

    cluster_blueprint 可能是 list 形态（每项是一条 scene/章计划，带 `cluster`
    字段标 cluster 归属，无 `cluster_id`/`chapter_range`），裸 .items() 会 AttributeError 崩。
    本 helper 把 list 按各项 cluster 标识（优先 cluster_id，其次 cluster）归并成 dict：
      · 同一 cluster 的项归入该 cluster 的 scene_storyboard
      · 由各项 ch 推出该 cluster 的 chapter_range = [min_ch, max_ch]（无 ch 则不写）

    入参既接受完整 进度.json dict，也接受直接的 blueprint 值（dict/list/其它）。
    非 dict/非 list/缺失 一律返回 {}（调用方据空 dict 走 fallback，不崩）。
    """
    if prog is None:
        return {}
    # 既可传整个 进度.json，也可直接传 blueprint 值
    if isinstance(prog, dict) and "cluster_blueprint" in prog:
        bp = prog.get("cluster_blueprint")
    else:
        bp = prog

    if isinstance(bp, dict):
        # 已是规范 dict 形态：原样返回（只保留 dict-value 项，防脏数据）
        return {k: v for k, v in bp.items() if isinstance(v, dict)}

    if isinstance(bp, list):
        out: dict = {}
        for item in bp:
            if not isinstance(item, dict):
                continue
            # 优先 cluster_id，其次 cluster；都缺时归 cluster_001 兜底
            raw_cid = item.get("cluster_id") or item.get("cluster")
            cid = normalize_cluster_id(raw_cid) or "cluster_001"
            slot = out.setdefault(cid, {"scene_storyboard": []})
            slot.setdefault("scene_storyboard", []).append(item)
            ch = item.get("ch")
            if isinstance(ch, int):
                cr = slot.get("chapter_range")
                if isinstance(cr, list) and len(cr) == 2:
                    cr[0] = min(cr[0], ch)
                    cr[1] = max(cr[1], ch)
                else:
                    slot["chapter_range"] = [ch, ch]
        return out

    # 非 dict/非 list（None 已在上面挡掉，这里是 str/int 等脏数据）
    return {}


def _coerce_range(cr):
    """归一 chapter_range 到 [lo, hi]·否则 None。

    权威反查兼容两种形态：list[lo,hi]，或 str "lo-hi"（部分 schema 会写字符串区间）。
    """
    lo_hi = None
    if isinstance(cr, list) and len(cr) == 2:
        lo_hi = cr
    elif isinstance(cr, str) and "-" in cr:
        a, b = cr.split("-", 1)
        lo_hi = [a.strip(), b.strip()]
    if lo_hi is None:
        return None
    # 归一为 [int, int] 并校验 lo <= hi；倒序或非数值区间视为损坏数据返回 None，
    # 避免下游 range(lo, hi+1) 产生空区间导致章数误判为 0。
    try:
        lo, hi = int(lo_hi[0]), int(lo_hi[1])
    except (ValueError, TypeError):
        return None
    return [lo, hi] if lo <= hi else None


def _iter_blueprint_ranges(project_root):
    """yield (cluster_id, [lo, hi] | None, scene_chs:set) from 进度.cluster_blueprint.

    cluster_blueprint 可能是 list 形态，裸 .items() 会 AttributeError 崩，
    因此先经 normalize_blueprint 归一成 dict 再迭代。
    """
    db = _db_dir(project_root)
    prog = _load_json(db / "进度.json", {}) or {}
    bp = normalize_blueprint(prog)
    for cid, cdata in bp.items():
        if not isinstance(cdata, dict):
            continue
        rng = _coerce_range(cdata.get("chapter_range") or [])
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
        rng = _coerce_range(c.get("chapter_range") or [])
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

    # 1) 事件簇.json.chapter_range（权威源 · 切章后由
    #    split_cluster_changes.writeback_event_cluster_range 回填真实范围）
    # 相邻 cluster range 重叠时不静默取首匹配——收集所有命中 range，
    # >=2 个则 stderr warn 并返回 start 较小者（标 ambiguous）。
    ec_matches = [
        (cid, rng)
        for cid, rng in _iter_event_cluster_ranges(project_root)
        if rng and rng[0] <= ch <= rng[1]
    ]
    picked = _pick_unambiguous(ch, ec_matches, "事件簇.json")
    if picked is not None:
        return picked

    # 2) cluster_blueprint.chapter_range（派生/缓存 · 事件簇未回填时兜底）
    bp_matches = []
    sb_fallback = None
    for cid, rng, scene_chs in _iter_blueprint_ranges(project_root):
        if rng and rng[0] <= ch <= rng[1]:
            bp_matches.append((cid, rng))
        if ch in scene_chs and sb_fallback is None:
            sb_fallback = cid
    picked = _pick_unambiguous(ch, bp_matches, "进度.json.cluster_blueprint")
    if picked is not None:
        return picked
    if sb_fallback:
        return sb_fallback

    return None


def _pick_unambiguous(ch, matches, source: str):
    """从命中同一 ch 的 (cluster_id, range) 列表中挑选归属。

      · 0 命中 → None（调用方走下一兜底层）
      · 1 命中 → 该 cluster_id
      · >=2 命中（相邻 cluster range 重叠）→ stderr warn，返回 range start 最小者
    """
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0][0]
    # 多命中：按 range start 升序，取最小者；start 相同再按 cluster_num
    ordered = sorted(
        matches,
        key=lambda m: (m[1][0], cluster_num(m[0]) if cluster_num(m[0]) is not None else 10**9),
    )
    chosen = ordered[0][0]
    ids = ", ".join(f"{cid}{rng}" for cid, rng in ordered)
    print(
        f"[cluster_lookup WARN] ch={ch} 命中 {len(matches)} 个重叠 cluster range（{source}）: "
        f"{ids} → ambiguous，取 start 最小者 {chosen}",
        file=sys.stderr,
    )
    return chosen


def cluster_id_to_range(project_root, cluster_id) -> list | None:
    """取 cluster_id 的 [lo, hi] 章范围。查不到返回 None。"""
    target = normalize_cluster_id(cluster_id)
    if target is None:
        return None
    # 事件簇.json 权威优先，blueprint 兜底
    for cid, rng in _iter_event_cluster_ranges(project_root):
        if normalize_cluster_id(cid) == target and rng:
            return rng
    for cid, rng, _ in _iter_blueprint_ranges(project_root):
        if normalize_cluster_id(cid) == target and rng:
            return rng
    return None


if __name__ == "__main__":
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
        # normalize_blueprint 对非 dict/list 输入的防御性处理
        # 非 dict/None → {}
        assert normalize_blueprint(None) == {}
        assert normalize_blueprint("garbage") == {}
        assert normalize_blueprint(123) == {}
        # 已是规范 dict → 原样（剔除非 dict value）
        d_form = {"cluster_blueprint": {"cluster_001": {"scene_storyboard": []}, "bad": 5}}
        nb = normalize_blueprint(d_form)
        assert set(nb.keys()) == {"cluster_001"}, nb
        # list 形态（每项带 cluster 字段 + ch，无 cluster_id/chapter_range）
        list_form = {"cluster_blueprint": [
            {"ch": 1, "cluster": "cluster_001"},
            {"ch": 2, "cluster": "cluster_001"},
            {"ch": 6, "cluster": "cluster_002"},
            {"ch": 8, "cluster": "cluster_002"},
            "junk_str",  # 脏数据应被跳过
        ]}
        nb2 = normalize_blueprint(list_form)
        assert set(nb2.keys()) == {"cluster_001", "cluster_002"}, nb2
        assert nb2["cluster_001"]["chapter_range"] == [1, 2], nb2["cluster_001"]
        assert nb2["cluster_002"]["chapter_range"] == [6, 8], nb2["cluster_002"]
        assert len(nb2["cluster_001"]["scene_storyboard"]) == 2
        # 直接传 blueprint 值（非完整 进度.json）也要支持
        assert normalize_blueprint([{"ch": 3, "cluster_id": "cluster_005"}])["cluster_005"]["chapter_range"] == [3, 3]
        # _pick_unambiguous: 多重叠命中 → 取 start 最小者
        assert _pick_unambiguous(7, [("cluster_002", [6, 9]), ("cluster_003", [7, 12])], "test") == "cluster_002"
        assert _pick_unambiguous(7, [("cluster_003", [7, 12])], "test") == "cluster_003"
        assert _pick_unambiguous(7, [], "test") is None
        print("[OK] cluster_lookup self-test passed")
