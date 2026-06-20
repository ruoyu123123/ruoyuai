"""run_cross_cluster_aggregates.py — 按 user_preferences.cross_chapter_scan_intensity 选择性跑 scanner（v21 UX6）

代替 save-state plan 中 18 行的 scanner 调用，统一通过本 wrapper：
- full_18：全部 18 个跨章 scanner（默认）
- core_10：仅核心 10 个（去多样性/趋势类 advisory）
- minimal_5：仅 5 个最关键（continuity/pattern/fate_drift/persona_drift/data_consumption）
- off：全不跑（紧急快速出稿）

用法：python run_cross_cluster_aggregates.py <project> --ch <ch>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from frozen_util import child_python, scripts_dir  # frozen-aware 子解释器/脚本目录（dev=no-op）
from pathlib import Path

# 18 个 scanner 分级（按用户感知重要度）
SCAN_TIERS = {
    "core_5": [
        "cross_cluster_continuity_aggregate",
        "cross_cluster_pattern_aggregate",
        "cross_cluster_fate_drift_aggregate",
        "cross_cluster_persona_drift_aggregate",
        "cross_cluster_data_consumption_aggregate",
    ],
    "core_10_extra": [
        "cross_cluster_offscreen_aggregate",
        "cross_cluster_declarative_data_aggregate",
        "cross_cluster_arc_progression_aggregate",
        "cross_cluster_throughline_balance_aggregate",
        "cross_cluster_foreshadow_rhythm_aggregate",
    ],
    "full_18_extra": [
        "cross_cluster_emotion_pattern_aggregate",
        "cross_cluster_character_dynamics_aggregate",
        "cross_cluster_world_dynamics_aggregate",
        "cross_cluster_judge_quality_aggregate",
        "cross_cluster_timeline_item_location_aggregate",
        "cross_cluster_meta_quality_aggregate",
        "cross_cluster_structure_compliance_aggregate",
        "cross_cluster_engagement_metrics_aggregate",
        "cross_cluster_ending_diversity_aggregate",
        "cross_cluster_scene_pov_diversity_aggregate",
        "cross_cluster_relationship_trend_aggregate",
        "cross_cluster_will_learn_aggregate",
        "volume_arc_drift_scanner",  # 2026-05-29 北极星 P2 [H3-trend]：卷级大势收敛漂移哨兵（advisory）
        "cross_cluster_style_drift_scanner",  # 2026-05-31 第2轮：跨 cluster 长程作者文风漂移哨兵（advisory · env LONGRANGE_DRIFT_MODE 默认 shadow）
        "volume_transition_scanner",  # 2026-06-20 R7 W2：卷过渡硬重置/钩零命中哨兵（advisory · env VOLUME_TRANSITION_MODE 默认 shadow）
        "cross_cluster_narrative_debt_ledger_aggregate",  # 2026-06-20 R7 Batch-D：叙事债务账本（book/volume/scene stock+flow·advisory·env NARRATIVE_DEBT_MODE 默认 shadow）
        "cross_cluster_sagging_middle_aggregate",  # 2026-06-20 R7 Batch-D：Sagging Middle 检测（40-60% 区段·advisory·env SAGGING_MIDDLE_MODE 默认 shadow）
        "cross_cluster_character_presence_balance_aggregate",  # 2026-06-20 R7 Batch-D：角色出场失衡+长尾遗忘+作者 ECDF z-band（advisory·env CHARACTER_PRESENCE_BALANCE_MODE 默认 shadow）
        "motif_recurrence_ledger",  # 2026-06-20 R8 W4 Batch-G L20：跨 cluster 母题循环账本(草蛇灰线·五类 props/imagery/sensory/places/catchphrase·五态 new_seed/recurring/dormant/over_saturated/payoff_due·Gini+N/R 直方图·advisory·env MOTIF_RECURRENCE_MODE 默认 shadow）
    ],
}

# 哪些 scanner 接 --ch 参数（其他用 --last-n 或全自动）
SCANNERS_WITH_CH = {
    "cross_cluster_fate_drift_aggregate",
}


def get_intensity(project_root: Path) -> str:
    prefs_path = project_root / "_数据库" / "用户偏好.json"
    if not prefs_path.exists():
        return "full_18"
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        return (prefs.get("quality_control") or {}).get("cross_chapter_scan_intensity", "full_18")
    except Exception:
        return "full_18"


def get_cluster_last_ch(project_root: Path, cluster_key: str) -> int | None:
    """从 事件簇.json 找 cluster 的最后一章号。"""
    shijianji_path = project_root / "_数据库" / "事件簇.json"
    if not shijianji_path.exists():
        return None
    try:
        data = json.loads(shijianji_path.read_text(encoding="utf-8"))
        for c in data.get("clusters", []):
            cid = c.get("cluster_id", "")
            if cid == cluster_key or cid.replace("cluster_", "") == cluster_key.replace("cluster_", ""):
                cr = c.get("chapter_range")
                if isinstance(cr, list) and len(cr) == 2:
                    return cr[1]
                elif isinstance(cr, str) and "-" in cr:
                    return int(cr.split("-")[1])
    except Exception:
        pass
    return None


# 2026-05-29 复审修复 [L7]：cluster 模式下把「最近 N 个 cluster」换算成「章数窗口」。
# 各 aggregator 的 --last-n 切的是 chapter_dirs[-N:]（章为单位），cluster 模式直接传
# --last-n 10 会被解释成 10 章≈3 个 cluster 之外的“覆盖到目标 cluster 头部”，
# 但 L7 担心的「10 cluster 爆量」根因是没把语义对齐——这里按目标 cluster 及其前 N-1 个
# 已落章 cluster 的累计章数算出真实窗口，下限 4（保证至少覆盖当前 cluster 上下文）。
def chapters_in_last_n_clusters(project_root: Path, cluster_key: str, n_clusters: int) -> int | None:
    """计算「含目标 cluster 在内的最近 n_clusters 个已落章 cluster」的累计章数。

    已落章判定（SC-6）：status ∈ {已完成, done, 进行中} 且 chapter_range 有有效 [lo,hi]。
    查不到 / 解析失败 → 返回 None（调用方回退到 --last-n 默认）。
    """
    shijianji_path = project_root / "_数据库" / "事件簇.json"
    if not shijianji_path.exists():
        return None
    try:
        data = json.loads(shijianji_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    landed_statuses = {"已完成", "done", "进行中", "in_progress"}
    landed = []  # [(hi_ch, span_chapters, cid)]
    target_norm = str(cluster_key).replace("cluster_", "")
    for c in data.get("clusters", []) or []:
        if not isinstance(c, dict):
            continue
        cr = c.get("chapter_range")
        rng = cr if (isinstance(cr, list) and len(cr) == 2) else None
        if not rng:
            continue
        status = str(c.get("status", "")).strip()
        # 目标 cluster 即便 status 不在表内也纳入（它正是当前在处理的 cluster）
        cid = str(c.get("cluster_id", ""))
        is_target = (cid == str(cluster_key) or cid.replace("cluster_", "") == target_norm)
        if status in landed_statuses or is_target:
            span = rng[1] - rng[0] + 1
            landed.append((rng[1], span, cid))
    if not landed:
        return None
    # 按末章排序取最近 n_clusters 个，累计 span
    landed.sort(key=lambda x: x[0])
    recent = landed[-max(1, n_clusters):]
    total = sum(span for _, span, _ in recent)
    return max(4, total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, help="单章模式（与 --cluster 二选一）")
    ap.add_argument("--cluster", help="cluster 模式：扫该 cluster 末章上下文")
    ap.add_argument("--last-n", type=int, default=10, help="aggregator 章数窗口（章为单位）")
    # 2026-05-29 复审修复 [L7]：cluster 模式专用——以「cluster 个数」表达窗口，
    # 内部换算成章数传给 aggregator 的 --last-n，避免「10 当 10 个 cluster 爆量」误解。
    ap.add_argument("--last-n-clusters", type=int, default=2,
                    help="cluster 模式窗口（含目标在内的最近 N 个已落章 cluster，默认 2；换算成章数）")
    ap.add_argument("--tier", choices=["minimal_5", "core_5", "core_10", "full_18", "off"], help="覆盖 user_preferences intensity（minimal_5 = core_5 别名）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()

    # cluster mode：用 cluster 末章作为 --ch
    if args.cluster:
        ch = get_cluster_last_ch(project_root, args.cluster)
        if ch is None:
            print(f"[FATAL] cluster {args.cluster} 未找到 chapter_range", file=sys.stderr)
            sys.exit(2)
        args.ch = ch
        print(f"[cluster {args.cluster}] 用末章 ch{ch} 作为扫描锚点")
        # 2026-05-29 复审修复 [L7]：cluster 模式把 --last-n-clusters 换算成章数窗口覆盖 --last-n。
        # 仅当用户未显式传 --last-n（仍是默认 10）时才覆盖，尊重显式覆盖。
        if "--last-n" not in sys.argv:
            eff = chapters_in_last_n_clusters(project_root, args.cluster, args.last_n_clusters)
            if eff is not None:
                print(f"[cluster {args.cluster}] --last-n-clusters={args.last_n_clusters} "
                      f"→ 换算章数窗口 --last-n={eff}（原默认 10）")
                args.last_n = eff
    elif args.ch is None:
        print(f"[FATAL] 必须指定 --ch 或 --cluster", file=sys.stderr)
        sys.exit(2)

    intensity = args.tier if args.tier else get_intensity(project_root)
    # 2026-05-30 北极星复审：minimal_5 是 core_5 的对外别名（docstring/CLAUDE.md 用 minimal_5，
    # SCAN_TIERS key 用 core_5）。归一——避免命令行 --tier minimal_5 被 argparse 拒、或配置写
    # minimal_5 时不匹配任何 if 分支静默走默认。
    if intensity == "minimal_5":
        intensity = "core_5"
    print(f"[run_cross_cluster_aggregates] ch{args.ch} intensity={intensity}")

    if intensity == "off":
        print("[SKIP] user_preferences.cross_chapter_scan_intensity=off")
        sys.exit(0)

    # 选 scanner 集合
    scanners = list(SCAN_TIERS["core_5"])
    if intensity in ("core_10", "full_18"):
        scanners.extend(SCAN_TIERS["core_10_extra"])
    if intensity == "full_18":
        scanners.extend(SCAN_TIERS["full_18_extra"])

    script_dir = scripts_dir()
    # 2026-05-29 复审修复 [C4]：新增 findings（exit 2 严重发现，区别于 errors 真崩溃）
    summary = {"ran": 0, "skipped": 0, "errors": [], "findings": []}
    for sc in scanners:
        sc_path = script_dir / f"{sc}.py"
        if not sc_path.exists():
            summary["skipped"] += 1
            continue
        # 构造 cmd
        if sc in SCANNERS_WITH_CH:
            cmd = [child_python(), str(sc_path), str(project_root), "--ch", str(args.ch)]
        elif sc in ("cross_cluster_arc_progression_aggregate", "cross_cluster_world_dynamics_aggregate",
                    "cross_cluster_foreshadow_rhythm_aggregate", "cross_cluster_will_learn_aggregate",
                    "cross_cluster_structure_compliance_aggregate",
                    "motif_recurrence_ledger"):  # 不需 --last-n / --ch · 自取末 N cluster
            cmd = [child_python(), str(sc_path), str(project_root)]
        else:
            cmd = [child_python(), str(sc_path), str(project_root), "--last-n", str(args.last_n)]
        try:
            # v2 cluster 化（2026-05-28）：cluster 模式给子进程透传 CLUSTER_MODE=1 env
            import os as _os
            _env = None
            if args.cluster:
                _env = {**_os.environ, "CLUSTER_MODE": "1", "CLUSTER_ID": args.cluster}
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", env=_env)
            summary["ran"] += 1
            # 2026-05-29 复审修复 [C4/SC-2]：原 `if r.returncode >= 2` 把 advisory(exit 1)
            # 当成功、又把真崩溃（exit 1 + Traceback）一并吞掉谎报成功。
            # 按 SC-2 退出码语义区分三档：
            #   - returncode >= 3  或  (returncode == 1 且 stderr 含 Traceback) = 真崩溃 → errors
            #   - returncode == 2  = 严重发现（warning 级）→ findings（非崩溃，记录但不算 error）
            #   - returncode == 1（无 Traceback）= advisory 发现 → 正常，不记
            stderr_txt = r.stderr or ""
            crashed = (r.returncode >= 3) or (r.returncode == 1 and "Traceback" in stderr_txt)
            if crashed:
                # 打印真实崩溃 scanner 名 + stderr 末尾便于定位
                tail = stderr_txt.strip().splitlines()[-3:] if stderr_txt.strip() else []
                print(f"[CRASH] scanner {sc} 崩溃 (exit={r.returncode}): " + " / ".join(tail), file=sys.stderr)
                summary["errors"].append({"scanner": sc, "exit": r.returncode,
                                          "crash": True, "stderr_tail": stderr_txt.strip()[-400:]})
            elif r.returncode == 2:
                summary["findings"].append({"scanner": sc, "exit": 2})
        except subprocess.TimeoutExpired:
            # 超时视为崩溃（scanner 卡死）
            print(f"[CRASH] scanner {sc} 超时 (>120s)", file=sys.stderr)
            summary["errors"].append({"scanner": sc, "timeout": True, "crash": True})
        except Exception as e:
            print(f"[CRASH] scanner {sc} 调用异常: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            summary["errors"].append({"scanner": sc, "exception": str(e)[:120], "crash": True})

    print(f"[OK] 跑了 {summary['ran']}/{len(scanners)} 个 scanner（intensity={intensity}）")
    if summary["findings"]:
        print(f"[FINDINGS] {len(summary['findings'])} 个 scanner 报严重发现（exit 2）: "
              + ", ".join(f["scanner"] for f in summary["findings"]))
    if summary["errors"]:
        crash_names = ", ".join(e["scanner"] for e in summary["errors"])
        print(f"[CRASH] {len(summary['errors'])} 个 scanner 真崩溃（不打断流水线，但已记录）: {crash_names}")
    # 2026-05-29 复审复修 [C4/SC-2]：本 wrapper 是「跑 + 报告」器，**恒 exit 0 不阻断流水线**
    # （「失败不中断流水线」铁律 + plan 调用处无 || true）。崩溃信号通过上面的 [CRASH] stderr
    # 大声上报（不再像旧版静默吞），但绝不因 scanner 崩溃让本脚本 exit 非 0 而中断 cluster-save-state。
    # 旧 C4 修复改成 exit 2 是过度——崩溃应「可观测」而非「阻断」。
    sys.exit(0)


if __name__ == "__main__":
    main()
