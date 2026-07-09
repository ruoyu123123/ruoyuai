"""maybe_judge_consensus.py — cluster 触发章判定 + judge_consensus 条件触发"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from frozen_util import child_python, scripts_dir  # frozen-aware（M4·dev=no-op）
except Exception:  # pragma: no cover
    def child_python():
        return sys.executable

try:
    import cluster_lookup  # noqa: E402  2026-05-29 复审修复：SC-1 blueprint list 归一守卫
except Exception:  # pragma: no cover - 防御性
    cluster_lookup = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


KEY_TURNING_POINT_KW = ["高潮", "反转", "触发", "觉醒", "崩溃", "牺牲", "宣战", "复仇", "终局"]


def _emotion_value(scene: dict) -> int:
    emo = scene.get("emotion", {})
    if isinstance(emo, dict):
        v = emo.get("value", 0)
        return v if isinstance(v, (int, float)) else 0
    return 0


def _cluster_trigger_chapters(project_root: Path) -> dict[int, list[str]]:
    """2026-05-29 cluster 化：返回 {章号: [触发原因]}，取代硬编码 STC_KEY_CHAPTERS。

    每个 cluster 取两类触发点：
      - 末章（cluster 收尾，judge consensus 最该跑的边界）
      - climax 章（scene_storyboard 里 turning_point 含关键词 / |emotion|≥7 的最强一章）
    数据来源：进度.cluster_blueprint（chapter_range + scene_storyboard）+ 事件簇.json.chapter_range。
    任一数据缺失只是少几个触发点，绝不崩。
    """
    triggers: dict[int, list[str]] = {}
    progress = load_json(project_root / "_数据库" / "进度.json", {}) or {}
    # 2026-05-29 复审修复：SC-1 — cluster_blueprint 可能是 list（城南实测 25 条逐章
    # scene 记录），裸 .items() 会 AttributeError 崩。normalize_blueprint 归一成 dict。
    if cluster_lookup is not None:
        blueprint = cluster_lookup.normalize_blueprint(progress)
    else:
        blueprint = progress.get("cluster_blueprint", {}) or {}
        if not isinstance(blueprint, dict):
            blueprint = {}

    # 1) cluster_blueprint：末章 + climax 章
    for cid, cdata in blueprint.items():
        if not isinstance(cdata, dict):
            continue
        cr = cdata.get("chapter_range") or []
        if isinstance(cr, list) and len(cr) == 2 and isinstance(cr[1], int):
            triggers.setdefault(cr[1], []).append(f"{cid} 末章")

        # climax：扫 scene_storyboard 找最强冲突章
        best_ch, best_strength, best_reason = None, 0, ""
        for sb in cdata.get("scene_storyboard", []) or []:
            if not isinstance(sb, dict):
                continue
            sch = sb.get("ch")
            if not isinstance(sch, int):
                continue
            tp = sb.get("turning_point", "") or ""
            hit_kw = next((kw for kw in KEY_TURNING_POINT_KW if kw in tp), None)
            emo = abs(_emotion_value(sb))
            strength = (100 if hit_kw else 0) + emo
            if strength > best_strength and (hit_kw or emo >= 7):
                best_ch = sch
                best_strength = strength
                best_reason = f"climax(turning_point含'{hit_kw}')" if hit_kw else f"climax(emotion={emo})"
        if best_ch is not None:
            triggers.setdefault(best_ch, []).append(f"{cid} {best_reason}")

    # 2) 事件簇.json.chapter_range（blueprint 没切到时的兜底末章）
    ec = load_json(project_root / "_数据库" / "事件簇.json", {}) or {}
    for c in ec.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("cluster_id", "cluster_?")
        cr = c.get("chapter_range") or []
        if isinstance(cr, list) and len(cr) == 2 and isinstance(cr[1], int):
            end = cr[1]
            if not any("末章" in r for r in triggers.get(end, [])):
                triggers.setdefault(end, []).append(f"{cid} 末章")

    return triggers


def trigger_consensus(project_root: Path, ch: int) -> int:
    judge_dir = project_root / "_数据库" / ".judge_reports"
    if not judge_dir.is_dir():
        print(f"[SKIP] .judge_reports/ 不存在")
        return 0
    # 2026-05-29 修：原 glob 会匹配到自己上轮写的 ch_NNN_consensus.json，
    # 重跑时把 consensus 当成普通 judge report 再纳入 merge（自污染）。过滤掉。
    reports = [
        p for p in judge_dir.glob(f"ch_{ch:03d}_*.json")
        if not p.name.endswith("_consensus.json")
    ]
    if len(reports) < 2:
        print(f"[SKIP] ch{ch} 仅 {len(reports)} 份 report，<2 不需 consensus")
        return 0
    consensus_script = scripts_dir() / "judge_consensus.py"  # frozen-aware（狩猎修）
    if not consensus_script.exists():
        print(f"[SKIP] judge_consensus.py 不存在")
        return 0
    cmd = [child_python(), str(consensus_script), "merge"] + [str(p) for p in reports]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=30)
        out_path = judge_dir / f"ch_{ch:03d}_consensus.json"
        if result.returncode == 0 and result.stdout.strip().startswith("{"):
            out_path.write_text(result.stdout, encoding="utf-8")
            print(f"[OK] consensus 合并 {len(reports)} 份 → {out_path.name}")
        else:
            print(f"[WARN] consensus 输出异常 (exit={result.returncode})")
        return 0  # 不阻塞主流水线
    except subprocess.TimeoutExpired:
        print(f"[WARN] consensus 超时")
        return 0


def _resolve_cluster_chapters(project_root: Path, cluster_key: str) -> list[int]:
    """2026-05-29 cluster 化：把 cluster key（'001' / 'cluster_001'）展开成章号列表。

    数据来源：事件簇.json.clusters[].chapter_range（与 judge_reports_archive
    的 _resolve_chapter_range 同源）。range 缺失（fluid 未切）→ 返回 []。
    """
    target = cluster_key.replace("cluster_", "")
    ec = load_json(project_root / "_数据库" / "事件簇.json", {}) or {}
    for c in ec.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("cluster_id", "")).replace("cluster_", "")
        if cid == target:
            cr = c.get("chapter_range") or []
            if isinstance(cr, list) and len(cr) == 2 and isinstance(cr[0], int) and isinstance(cr[1], int):
                return list(range(cr[0], cr[1] + 1))
    return []


def run_cluster(project_root: Path, cluster_key: str) -> int:
    """2026-05-29 cluster 化（主路径）：展开本 cluster 章节，对每个 cluster 触发章
    （cluster 末章 + climax 章，复用已有 _cluster_trigger_chapters）跑 consensus。

    plan cluster-save-state.plan.json:127 以 `--cluster {key}` 调用（无 || true），
    故此入口绝不能崩：range 缺失 / 无触发章 → 优雅 [SKIP] + exit 0。
    """
    chapters = _resolve_cluster_chapters(project_root, cluster_key)
    cluster_id = "cluster_" + cluster_key.replace("cluster_", "")
    if not chapters:
        print(f"[SKIP] {cluster_id} 在 事件簇.json 无 chapter_range（fluid 未切定），跳过 consensus")
        return 0

    # cluster 触发章 = 本 cluster 范围内的「末章 + climax 章」
    trigger_map = _cluster_trigger_chapters(project_root)
    triggered = [ch for ch in chapters if ch in trigger_map]
    if not triggered:
        # blueprint/storyboard 缺失时兜底：至少把本 cluster 末章当触发章
        triggered = [chapters[-1]]
        print(f"[INFO] {cluster_id} 无 blueprint 触发章，兜底取末章 ch{triggered[0]}")

    print(f"[judge_consensus · cluster mode] {cluster_id} 章 {chapters} → 触发章 {triggered}")
    for ch in triggered:
        reasons = trigger_map.get(ch, [f"{cluster_id} 末章(兜底)"])
        print(f"[KEY] ch{ch} 触发: {reasons}")
        trigger_consensus(project_root, ch)
    return 0


def main():
    ap = argparse.ArgumentParser(description="maybe_judge_consensus · cluster 触发章 judge_consensus 派发")
    ap.add_argument("project")
    ap.add_argument("--cluster", type=str, required=True,
                    help="cluster key（'001' 或 'cluster_001'）· 展开本 cluster 触发章跑 consensus")
    args = ap.parse_args()
    project_root = Path(args.project)
    sys.exit(run_cluster(project_root, args.cluster))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
