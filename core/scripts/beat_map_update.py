"""beat_map_update.py — cluster_beats 确定性 producer（接通 beat_map 孤儿契约债）

# 🔴 2026-06-29 beat_map接通producer

背景（孤儿契约债）：`plot_structure_scanner.py`（CLUSTER_MODE 分支）读
`beat_map.json` 的 `cluster_beats[cluster_id]` 判定 cluster 内多 beat 兑现，
`cross_cluster_structure_compliance_aggregate.py` 读 `chapters_beat`；但全仓
beat_map 写入此前**仅 scaffold 空骨架 + migrate（ch→scene_index 重命名）·零内容
producer** → cluster_beats 永远空 → beat 合规检测拿空列表 = 死码。

本脚本据 cluster brief 的 `scene_storyboard` **确定性派生** 起承转合
（Kishōtenketsu）四段结构功能 beat 序列，回写 `beat_map.cluster_beats[cluster_id]`，
让 `plot_structure_scanner`（cluster 视野）端到端读到真数据（不再 fallback / 空）。

北极星纪律：
- **零创作判断**：只读 storyboard 既有场景 + `climax_marker` 派生结构功能标签
  （确定性·像其他 `*_update.py`），不发明剧情、不写 prose、不锁章数字数。
- **全 advisory**：plot_structure issue 全是「情节结构建议」非客观错误，BEAT_* 码
  **绝不进 `audit_hub.HARD_GATE_CODES`**（已核对）。
- **C03 fluid**：只为**当前 active cluster** 派生 beats（cluster_choice_apply 每次只
  应用一个 cluster），**绝不预设 cluster_002+**（守事件簇 fluid 铁律·同 scene_storyboard）。
- **默认安全·向后兼容**：无 storyboard / 旧书 → 不写空键（scanner 优雅降级·不崩）。

落点：`cluster_choice_apply.apply_choice()` 内（/outline step 6.5 cluster_001 +
/cluster-save-state emergence cluster_002+ 唯一确定性数据管道），亦可 CLI 独立重跑。

用法:
  python core/scripts/beat_map_update.py <project_root> --cluster cluster_001
退出码: 0 成功/优雅跳过 · 2 致命（项目不存在）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# 起承转合（Kishōtenketsu）四段结构功能 → 中文标签（advisory 软标·cluster 级结构功能）
_FUNC_CN = {"ki": "起", "sho": "承", "ten": "转", "ketsu": "结"}


def _load(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        from atomic_json import atomic_write_json
        atomic_write_json(p, d)
    except ImportError:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def _segment_function(i: int, n: int, climax_idx: int | None) -> str:
    """起承转合四段定位（确定性·按场景位置 + climax 标记）：
    首场=起(ki)·尾场=结(ketsu)·climax 及其后=转(ten)·其余前半=承(sho)/后半=转(ten)。
    """
    if n <= 1:
        return "ki"
    if i == 0:
        return "ki"
    if i == n - 1:
        return "ketsu"
    if climax_idx is not None and i >= climax_idx:
        return "ten"
    return "sho" if (i / (n - 1)) < 0.5 else "ten"


def derive_cluster_beats(storyboard, narrative_mode: str = "linear",
                         climax_hint: int | None = None) -> list[dict]:
    """据 scene_storyboard 确定性派生 起承转合 结构功能 beat 序列（每 scene 一条 beat）。

    返回 [{beat, scene_index, function, is_climax, turn}, ...]。
    - `beat`：人类可读结构功能名（plot_structure_scanner 读 `b.get("beat")`）。
    - climax 定位：优先 scene 自带 `climax_marker:true`，再退 `climax_hint`（climax_hint_scene_index）。
    - 空/非法 storyboard → []（向后兼容·调用方据此优雅跳过不写）。
    """
    if not isinstance(storyboard, list) or not storyboard:
        return []
    n = len(storyboard)

    climax_idx: int | None = None
    for i, s in enumerate(storyboard):
        if isinstance(s, dict) and s.get("climax_marker"):
            climax_idx = i
            break
    if climax_idx is None and isinstance(climax_hint, int) and 0 <= climax_hint < n:
        climax_idx = climax_hint

    beats: list[dict] = []
    for i, s in enumerate(storyboard):
        sc = s if isinstance(s, dict) else {}
        func = _segment_function(i, n, climax_idx)
        scene_name = str(sc.get("scene") or sc.get("summary") or f"场景{i + 1}").strip()
        beat_name = f"{_FUNC_CN[func]}·{scene_name}"
        beats.append({
            "beat": beat_name[:60],
            "scene_index": i,
            "function": func,
            "is_climax": (i == climax_idx),
            "turn": str(sc.get("turn") or "")[:120],
        })
    return beats


def write_cluster_beats(project_root: Path, cluster_id: str,
                        beats: list[dict]) -> dict:
    """回写 beat_map.cluster_beats[cluster_id]（保留既有 beat_map 顶层结构）。

    无 beats（空 storyboard / 旧书）→ 不写空键（守向后兼容·scanner 优雅降级）。
    """
    bm_path = project_root / "_数据库" / "beat_map.json"
    bm = _load(bm_path, {})
    if not isinstance(bm, dict):
        bm = {}
    bm.setdefault("schema_version", "v27")
    cb = bm.get("cluster_beats")
    if not isinstance(cb, dict):
        cb = {}
    if not beats:
        return {"cluster_id": cluster_id, "beats": 0, "skipped": "empty_storyboard"}
    cb[cluster_id] = beats
    bm["cluster_beats"] = cb
    # 防再孤儿·可审计：标记 producer 已接通（grep 可证 cluster_beats 有内容 producer）。
    bm["_cluster_beats_producer"] = (
        "beat_map_update.py（确定性·据 scene_storyboard 派生 起承转合 beat·advisory）"
    )
    _save(bm_path, bm)
    return {"cluster_id": cluster_id, "beats": len(beats)}


def update(project_root: Path, cluster_id: str) -> dict:
    """读 事件簇.json 该 cluster 的 scene_storyboard → 派生 beats → 回写 beat_map。

    只处理传入的单个 active cluster（守 C03 fluid·不遍历未来 cluster）。
    """
    db = project_root / "_数据库"
    sj = _load(db / "事件簇.json", {})
    if not isinstance(sj, dict):
        return {"cluster_id": cluster_id, "beats": 0, "skipped": "event_cluster_unreadable"}

    cluster = None
    for c in sj.get("clusters", []):
        if isinstance(c, dict) and c.get("cluster_id") == cluster_id:
            cluster = c
            break
    if not cluster:
        return {"cluster_id": cluster_id, "beats": 0, "skipped": "cluster_not_found"}

    storyboard = cluster.get("scene_storyboard") or []
    climax_hint = cluster.get("climax_hint_scene_index")
    narrative_mode = cluster.get("narrative_mode", "linear")
    beats = derive_cluster_beats(storyboard, narrative_mode, climax_hint)
    return write_cluster_beats(project_root, cluster_id, beats)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="cluster_beats 确定性 producer（据 scene_storyboard 派生）")
    ap.add_argument("project_root")
    ap.add_argument("--cluster", required=True, help="cluster_id（如 cluster_001）")
    args = ap.parse_args()

    root = Path(args.project_root)
    if not (root / "_数据库").exists():
        print(f"[beat_map_update] 项目 _数据库 不存在: {root}", file=sys.stderr)
        return 2
    r = update(root, args.cluster)
    if r.get("beats"):
        print(f"[beat_map_update] beat_map.cluster_beats[{r['cluster_id']}] 写入 {r['beats']} beat")
    else:
        print(f"[beat_map_update] 跳过 {r['cluster_id']}（{r.get('skipped', 'no_beats')}）",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
