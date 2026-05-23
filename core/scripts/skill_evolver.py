"""skill_evolver.py — AutoSkill 风格写作经验自动演化（v22 SE1）

业界 arxiv 2603.01145 AutoSkill：crystallize 经验为 versioned skill artifacts。
我们的 写作经验.json 当前是 flat list，升级为有生命周期的 skill：
- 每条 pattern 加 version / evolution_history / usage_count / last_validated_at / confidence
- 自动 evolve：合并相似 patterns / 提炼通用规则 / 淘汰长期未用

4 个能力：
1. evolve: 合并相似 (Jaccard > 0.6) + 提炼共性 + version+1
2. promote: usage_count >= 5 + confidence >= 0.8 → 升 universal_skill_pool
3. retire: last_validated_at > N 章未触发 → 标 retired
4. dashboard: skill 演化全景

用法：python skill_evolver.py <project> <action> [args]
  action: evolve / promote / retire / dashboard
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
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


def jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def upgrade_to_versioned(pattern: dict, current_ch: int) -> dict:
    """把旧 flat pattern 升级为 versioned skill。"""
    if "version" in pattern:
        return pattern  # 已升级
    upgraded = dict(pattern)
    upgraded["version"] = 1
    upgraded["evolution_history"] = [{
        "version": 1,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": "initial_record",
        "summary": "原始记录（升级到 versioned 时刻）",
    }]
    upgraded["usage_count"] = pattern.get("usage_count", 0)
    upgraded["last_validated_at_ch"] = pattern.get("recorded_at_ch", current_ch)
    upgraded["confidence"] = pattern.get("confidence", 0.5)
    upgraded["status"] = "active"
    return upgraded


def evolve(project_root: Path, current_ch: int) -> dict:
    """合并相似 patterns + 提炼共性 + version+1"""
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})

    results = {"merged": [], "upgraded_count": 0, "version_bumped": []}

    for category in ["success_patterns", "failure_patterns"]:
        patterns = exp.get(category, []) or []
        if not patterns:
            continue

        # Step 1: 全部升级到 versioned
        upgraded = [upgrade_to_versioned(p, current_ch) if isinstance(p, dict) else p for p in patterns]
        results["upgraded_count"] += sum(1 for p in upgraded if p.get("version") == 1 and len(p.get("evolution_history", [])) == 1)

        # Step 2: 找相似 pattern pair（描述 Jaccard > 0.6）
        merged_indices = set()
        new_patterns = []
        for i, p in enumerate(upgraded):
            if i in merged_indices:
                continue
            p_desc = p.get("description", "") + " " + p.get("name", "")
            similar = []
            for j in range(i + 1, len(upgraded)):
                if j in merged_indices:
                    continue
                q = upgraded[j]
                q_desc = q.get("description", "") + " " + q.get("name", "")
                if jaccard(p_desc, q_desc) > 0.6:
                    similar.append((j, q))
            if similar:
                # 合并：取最高 confidence + 累积 usage + 升 version
                all_p = [p] + [q for _, q in similar]
                max_conf = max(x.get("confidence", 0.5) for x in all_p)
                total_use = sum(x.get("usage_count", 0) for x in all_p)
                max_version = max(x.get("version", 1) for x in all_p)
                merged = dict(p)
                merged["version"] = max_version + 1
                merged["confidence"] = min(1.0, max_conf + 0.1)
                merged["usage_count"] = total_use
                merged["last_validated_at_ch"] = current_ch
                merged["evolution_history"] = merged.get("evolution_history", []) + [{
                    "version": merged["version"],
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "action": "merged",
                    "merged_count": len(all_p),
                    "summary": f"合并 {len(all_p)} 条相似 pattern",
                }]
                results["merged"].append({"category": category, "count": len(all_p), "new_version": merged["version"]})
                results["version_bumped"].append(merged.get("id") or merged.get("name", "?"))
                new_patterns.append(merged)
                for j, _ in similar:
                    merged_indices.add(j)
            else:
                new_patterns.append(p)

        exp[category] = new_patterns

    # 记录 evolution_log
    exp.setdefault("_evolution_log", []).append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": "evolve",
        "current_ch": current_ch,
        "results": results,
    })
    # 保留 log 最近 50 条
    exp["_evolution_log"] = exp["_evolution_log"][-50:]

    save_json(exp_path, exp)
    return results


def retire(project_root: Path, current_ch: int, threshold_ch: int = 30) -> dict:
    """long-unused patterns 标 retired"""
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    retired_ids = []
    for category in ["success_patterns", "failure_patterns"]:
        for p in exp.get(category, []) or []:
            if not isinstance(p, dict):
                continue
            last = p.get("last_validated_at_ch", 0)
            if current_ch - last > threshold_ch and p.get("status") != "retired":
                p["status"] = "retired"
                p["retired_at_ch"] = current_ch
                retired_ids.append(p.get("id") or p.get("name", "?"))
    if retired_ids:
        save_json(exp_path, exp)
    return {"retired_count": len(retired_ids), "retired_ids": retired_ids}


def promote(project_root: Path) -> dict:
    """高 usage + 高 confidence pattern 升 universal_skill_pool（跨项目）"""
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    pool_path = Path(__file__).parent.parent / "claude-home" / "universal_skill_pool.json"
    pool = load_json(pool_path, {"universal_patterns": [], "_meta": {"created_at": datetime.now().isoformat(timespec="seconds")}})

    promoted_ids = []
    for category in ["success_patterns", "failure_patterns"]:
        for p in exp.get(category, []) or []:
            if not isinstance(p, dict):
                continue
            usage = p.get("usage_count", 0)
            conf = p.get("confidence", 0)
            if usage >= 5 and conf >= 0.8 and not p.get("promoted_to_universal"):
                # 复制到 pool
                universal_entry = dict(p)
                universal_entry["category"] = category
                universal_entry["source_project"] = exp_path.parent.parent.name
                universal_entry["promoted_at"] = datetime.now().isoformat(timespec="seconds")
                pool["universal_patterns"].append(universal_entry)
                p["promoted_to_universal"] = True
                promoted_ids.append(p.get("id") or p.get("name", "?"))

    if promoted_ids:
        save_json(pool_path, pool)
        save_json(exp_path, exp)
    return {"promoted_count": len(promoted_ids), "promoted_ids": promoted_ids, "pool_total": len(pool["universal_patterns"])}


def dashboard(project_root: Path) -> dict:
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    stats = {"by_category": {}, "by_version": defaultdict(int), "active": 0, "retired": 0, "promoted": 0}
    for category in ["success_patterns", "failure_patterns"]:
        patterns = exp.get(category, []) or []
        cat_stats = {"total": len(patterns), "versioned": 0, "active": 0, "retired": 0}
        for p in patterns:
            if not isinstance(p, dict):
                continue
            if p.get("version"):
                cat_stats["versioned"] += 1
                stats["by_version"][p.get("version", 1)] += 1
            if p.get("status") == "retired":
                cat_stats["retired"] += 1
                stats["retired"] += 1
            else:
                cat_stats["active"] += 1
                stats["active"] += 1
            if p.get("promoted_to_universal"):
                stats["promoted"] += 1
        stats["by_category"][category] = cat_stats
    stats["evolution_log_count"] = len(exp.get("_evolution_log", []))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["evolve", "promote", "retire", "dashboard"])
    ap.add_argument("--ch", type=int, default=0)
    ap.add_argument("--retire-threshold", type=int, default=30)
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if args.action == "evolve":
        r = evolve(project_root, args.ch)
    elif args.action == "promote":
        r = promote(project_root)
    elif args.action == "retire":
        r = retire(project_root, args.ch, args.retire_threshold)
    else:
        r = dashboard(project_root)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
