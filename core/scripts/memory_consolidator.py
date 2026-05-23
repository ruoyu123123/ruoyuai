"""memory_consolidator.py — 双层记忆 + Forgetting Curve（v22.5 L13）

业界 2026（FadeMem / MaRS / AgeMem）共识：
- 双层 memory：LML（长期高重要性）/ SML（短期低重要性）
- Priority Decay：importance 随时间衰减
- LRU eviction：最久未访问的 evict
- 「遗忘是优化不是 bug」

升级 skill_evolver 的 retire 逻辑：
- 静态 last_validated_at 阈值 → 动态 importance score
- importance = usage_count × recency_decay × confidence × success_weight

3 个能力：
A. score_all: 给所有 pattern 算 importance + decay → 重排
B. promote_to_LML: importance 持续 ≥ 0.8 → 升 LML（永久保留）
C. evict_SML: SML 中 importance < 0.2 → 真正删除（不只 retired）

写入 写作经验.json:
- 每个 pattern 加 layer (LML/SML) + importance_score + last_accessed_at
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def calc_importance(pattern: dict, current_ch: int, success_weight: float = 1.0) -> float:
    """importance = usage_count × recency_decay × confidence × success_weight

    recency_decay：FadeMem 风格 = exp(-(current_ch - last_accessed) / 30)
    （30 章半衰期）
    """
    usage = pattern.get("usage_count", 0)
    last = pattern.get("last_validated_at_ch", pattern.get("recorded_at_ch", current_ch))
    confidence = pattern.get("confidence", 0.5)

    ch_gap = max(0, current_ch - last)
    recency = math.exp(-ch_gap / 30.0)  # 30 章衰减 1/e

    # log scale usage（避免 usage 主导）
    usage_score = math.log(usage + 1)

    importance = usage_score * recency * confidence * success_weight
    # 钳制 [0, 1]
    return min(1.0, max(0.0, importance / 5.0))  # / 5 normalize


def consolidate(project_root: Path, current_ch: int,
                lml_threshold: float = 0.7, evict_threshold: float = 0.1) -> dict:
    """全 pattern 算 importance + 分层 + evict 低分"""
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    results = {"scored": 0, "lml": 0, "sml": 0, "evicted": []}

    for category in ["success_patterns", "failure_patterns"]:
        patterns = exp.get(category, []) or []
        new_patterns = []
        # success_patterns 权重高（更应保留）
        success_weight = 1.2 if category == "success_patterns" else 1.0

        for p in patterns:
            if not isinstance(p, dict):
                new_patterns.append(p)
                continue
            imp = calc_importance(p, current_ch, success_weight)
            p["importance_score"] = round(imp, 3)
            p["importance_calculated_at"] = datetime.now().isoformat(timespec="seconds")
            results["scored"] += 1

            # 分层
            if imp >= lml_threshold:
                p["layer"] = "LML"
                results["lml"] += 1
                new_patterns.append(p)
            elif imp >= evict_threshold:
                p["layer"] = "SML"
                results["sml"] += 1
                new_patterns.append(p)
            else:
                # evict
                results["evicted"].append({
                    "id": p.get("id") or p.get("name", "?"),
                    "category": category,
                    "importance": imp,
                    "reason": f"importance {imp:.3f} < evict_threshold {evict_threshold}",
                })
                # 不放入 new_patterns（真删除）

        exp[category] = new_patterns

    # 记录 consolidation log
    exp.setdefault("_consolidation_log", []).append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "current_ch": current_ch,
        "lml_threshold": lml_threshold,
        "evict_threshold": evict_threshold,
        "results": {k: (len(v) if isinstance(v, list) else v) for k, v in results.items()},
    })
    exp["_consolidation_log"] = exp["_consolidation_log"][-30:]

    save_json(exp_path, exp)
    return results


def dashboard(project_root: Path) -> dict:
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    stats = {"by_layer": {"LML": 0, "SML": 0, "unclassified": 0},
             "importance_distribution": {">0.7": 0, "0.4-0.7": 0, "0.1-0.4": 0, "<0.1": 0},
             "by_category": {}}
    for category in ["success_patterns", "failure_patterns"]:
        cat_stats = {"total": 0, "LML": 0, "SML": 0, "avg_importance": 0}
        total_imp = 0
        patterns = exp.get(category, []) or []
        for p in patterns:
            if not isinstance(p, dict):
                continue
            cat_stats["total"] += 1
            layer = p.get("layer", "unclassified")
            stats["by_layer"][layer] = stats["by_layer"].get(layer, 0) + 1
            cat_stats[layer if layer in ("LML", "SML") else "total"] += 0  # 累计 LML/SML
            if layer == "LML":
                cat_stats["LML"] += 1
            elif layer == "SML":
                cat_stats["SML"] += 1
            imp = p.get("importance_score", 0)
            total_imp += imp
            if imp > 0.7:
                stats["importance_distribution"][">0.7"] += 1
            elif imp > 0.4:
                stats["importance_distribution"]["0.4-0.7"] += 1
            elif imp > 0.1:
                stats["importance_distribution"]["0.1-0.4"] += 1
            else:
                stats["importance_distribution"]["<0.1"] += 1
        cat_stats["avg_importance"] = round(total_imp / cat_stats["total"], 3) if cat_stats["total"] else 0
        stats["by_category"][category] = cat_stats
    stats["consolidation_log_count"] = len(exp.get("_consolidation_log", []))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["consolidate", "dashboard"])
    ap.add_argument("--ch", type=int, default=0)
    ap.add_argument("--lml-threshold", type=float, default=0.7)
    ap.add_argument("--evict-threshold", type=float, default=0.1)
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if args.action == "consolidate":
        r = consolidate(project_root, args.ch, args.lml_threshold, args.evict_threshold)
    else:
        r = dashboard(project_root)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
