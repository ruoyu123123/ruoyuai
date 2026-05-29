"""learning_hub.py — 统一学习调度中枢（v22.5 L9）

统一调度全部 8 学习器（涵盖叙事/UX/错误/性能/工具/安全/协作 全维度）：

L1+L6: user_experience_learner   - 用户行为 + 痛点
L2:    error_pattern_analyzer    - 错误聚类
L3+L4: dead_feature_detector     - 死功能 + manifest 字段消费率
L8:    high_score_pattern_extractor - 高分章节共性学习

+ 既有 v22 SE:
SE1:   skill_evolver evolve/promote/retire
SE4:   evolution_orchestrator 三角共演化

聚合输出：_数据库/.learning/hub_summary_<ts>.json（统一进度报告）

用法：
  python learning_hub.py <project>              # 全跑
  python learning_hub.py <project> --quick      # 仅快学习器（user_experience + error + dead）
  python learning_hub.py <project> --status     # 只显示当前 learning 状态不重跑
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 2026-05-29 cluster 化：skill_evolver / evolution_orchestrator 已切到 cluster 主路径，
# 注入参数从 `--ch {cur_ch}` 改为 `--cluster {cur_cluster}`（取最新落账 cluster_id）。
# cur_ch 仍保留给可能需要章号的旧学习器；cluster 解析失败时优雅回退 --ch。
try:
    import cluster_summary_reader as _csr  # noqa: E402
except Exception:  # pragma: no cover - 防御性
    _csr = None
try:
    import cluster_lookup as _cl  # noqa: E402
except Exception:  # pragma: no cover - 防御性
    _cl = None


LEARNERS_FULL = [
    ("user_experience_learner.py", ["{project}"], "L1+L6 用户行为+痛点"),
    ("error_pattern_analyzer.py", ["{project}"], "L2 错误聚类"),
    ("dead_feature_detector.py", ["{project}"], "L3+L4 死功能 + manifest 消费率"),
    ("high_score_pattern_extractor.py", ["{project}", "--update-experience"], "L8 高分章节共性"),
    # 2026-05-29 cluster 化：注入 --cluster {cur_cluster}（cluster 解析失败时 run_learners 回退 --ch）
    ("skill_evolver.py", ["{project}", "evolve", "--cluster", "{cur_cluster}"], "SE1 evolve"),
    ("skill_evolver.py", ["{project}", "retire", "--cluster", "{cur_cluster}"], "SE1 retire"),
    ("skill_evolver.py", ["{project}", "promote"], "SE1 promote → universal_skill_pool"),
    ("evolution_orchestrator.py", ["{project}", "--cluster", "{cur_cluster}"], "SE4 三角共演化"),
    ("stuck_loop_guard.py", ["{project}"], "v23 Layer 0 卡死信号扫描"),
    ("adversarial_blindspot_scan.py", ["{project}"], "v23 Layer 1 集体盲点曝光"),
    ("counterfactual_judge_diff.py", ["{project}"], "v23 Layer 2+3 self-protection 曝光"),
    ("gepa_prompt_optimizer.py", ["--project", "{project}"], "v23 Layer 4 GEPA Pareto 前沿"),
]

LEARNERS_QUICK = [
    ("user_experience_learner.py", ["{project}"], "L1+L6 用户行为+痛点"),
    ("error_pattern_analyzer.py", ["{project}"], "L2 错误聚类"),
    ("dead_feature_detector.py", ["{project}"], "L3+L4 死功能"),
    ("stuck_loop_guard.py", ["{project}"], "v23 Layer 0 卡死信号扫描"),
]


def get_current_ch(project_root: Path) -> int:
    import re
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-1] if chs else 0


def get_current_cluster(project_root: Path, cur_ch: int) -> str | None:
    """2026-05-29 cluster 化：取「最新 cluster」的 cluster key（不含 'cluster_' 前缀）。

    链：1) cluster 账本最后一个落账 cluster；2) 回退 cluster_lookup 用最新章号反查；
    都失败返回 None（run_learners 据此把 cluster 学习器回退到 --ch）。
    """
    # 1) 账本最后一个落账 cluster
    if _csr is not None:
        try:
            clusters = _csr.get_clusters(project_root)
            if clusters:
                cid = clusters[-1].get("cluster_id")
                if cid:
                    return str(cid).replace("cluster_", "")
        except Exception:  # pragma: no cover - 防御性
            pass
    # 2) 用最新章号反查 cluster
    if _cl is not None and cur_ch:
        try:
            cid = _cl.ch_to_cluster_id(project_root, cur_ch)
            if cid:
                return str(cid).replace("cluster_", "")
        except Exception:  # pragma: no cover - 防御性
            pass
    return None


def status(project_root: Path) -> dict:
    """读最近一批 learning 报告，输出 status"""
    learning_dir = project_root / "_数据库" / ".learning"
    if not learning_dir.exists():
        return {"status": "no_data", "msg": "学习中枢未跑过，无数据"}

    # 各类型最新报告
    types = ["user_experience", "error_patterns", "dead_features", "high_score_patterns"]
    latest = {}
    for t in types:
        files = sorted(learning_dir.glob(f"{t}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            latest[t] = {
                "ts": data.get("scan_ts", ""),
                "summary": _summarize(t, data),
                "file": files[0].name,
            }
    return latest


def _summarize(t: str, data: dict) -> dict:
    if t == "user_experience":
        return {
            "card_choices": data.get("user_behavior", {}).get("card_choice_distribution"),
            "writing_intervals_h": data.get("user_behavior", {}).get("writing_intervals", {}).get("avg_interval_hours"),
            "pain_points": data.get("pain_points_count", 0),
        }
    if t == "error_patterns":
        return {
            "total_errors": data.get("total_errors", 0),
            "unique_codes": data.get("unique_codes", 0),
            "top_3_clusters": [c.get("code") for c in data.get("top_10_clusters", [])[:3]],
        }
    if t == "dead_features":
        return {
            "total_dead": data.get("total", 0),
            "by_category": data.get("by_category", {}),
        }
    if t == "high_score_patterns":
        return {
            "high_score_count": len(data.get("high_score_chapters", [])),
            "patterns_extracted": len(data.get("extracted_patterns", [])),
        }
    return {}


def run_learners(project_root: Path, learners: list) -> dict:
    script_dir = Path(__file__).parent
    cur_ch_int = get_current_ch(project_root)
    cur_ch = str(cur_ch_int)
    cur_cluster = get_current_cluster(project_root, cur_ch_int)
    results = {"ran": 0, "errors": [], "details": []}
    for script, raw_args, label in learners:
        sp = script_dir / script
        if not sp.exists():
            continue
        # 2026-05-29 cluster 化：raw_args 含 {cur_cluster} 但 cluster 解析失败时，
        # 回退到 chapter 模式（--cluster <key> → --ch <ch>），保证学习器不会因
        # cluster 缺失而拿到空串崩。
        if cur_cluster is None and any("{cur_cluster}" in a for a in raw_args):
            raw_args = ["--ch" if a == "--cluster" else a for a in raw_args]
            raw_args = [a.replace("{cur_cluster}", "{cur_ch}") for a in raw_args]
        args = [a.format(project=str(project_root), cur_ch=cur_ch,
                         cur_cluster=(cur_cluster or "")) for a in raw_args]
        cmd = [sys.executable, str(sp)] + args
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
            results["ran"] += 1
            results["details"].append({"label": label, "exit": r.returncode,
                                       "stdout_tail": (r.stdout or "")[-200:]})
            if r.returncode >= 2:
                results["errors"].append({"label": label, "exit": r.returncode})
        except Exception as e:
            results["errors"].append({"label": label, "exception": str(e)[:80]})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--quick", action="store_true", help="仅快学习器")
    ap.add_argument("--status", action="store_true", help="仅显示当前状态")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()

    if args.status:
        s = status(project_root)
        print(json.dumps(s, ensure_ascii=False, indent=2))
        sys.exit(0)

    learners = LEARNERS_QUICK if args.quick else LEARNERS_FULL
    print(f"[learning_hub] 跑 {len(learners)} 个学习器（mode={'quick' if args.quick else 'full'}）...")
    results = run_learners(project_root, learners)

    print(f"\n[learning_hub] 完成 {results['ran']}/{len(learners)} 个学习器")
    if results["errors"]:
        print(f"  错误: {len(results['errors'])} 个")
        for e in results["errors"]:
            print(f"    - {e}")

    # 汇总最新 status
    s = status(project_root)
    summary = {
        "scan_type": "learning_hub_summary",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "learners_ran": results["ran"],
        "errors_count": len(results["errors"]),
        "latest_status": s,
    }
    summary_path = project_root / "_数据库" / ".learning" / f"hub_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  汇总报告: {summary_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
