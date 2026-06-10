"""skill_evolver.py — AutoSkill 风格写作经验自动演化（v22 SE1）

业界 arxiv 2603.01145 AutoSkill：crystallize 经验为 versioned skill artifacts。
我们的 写作经验.json 当前是 flat list，升级为有生命周期的 skill：
- 每条 pattern 加 version / evolution_history / usage_count / last_validated_at / confidence
- 自动 evolve：合并相似 patterns / 提炼通用规则 / 淘汰长期未用

4 个能力：
1. evolve: 合并相似 (Jaccard > 0.6) + 提炼共性 + version+1
2. promote: usage_count >= 5 + confidence >= 0.8 → 升 universal_skill_pool
   · transfer_scope filter（2026-05-31 · 防跨项目负迁移）：升级前按 scope 过滤——
     通用工艺（节奏/结构/钩子）才跨项目，作者 idiolect 特异（口癖/签名词/特有遣词/角色名）
     锁本地。advisory（pattern 自带 transfer_scope 字段以其为准）· env 默认 active。
3. retire: last_validated_at > N 章未触发 → 标 retired
4. dashboard: skill 演化全景

用法：python skill_evolver.py <project> <action> [args]
  action: evolve / promote / retire / dashboard
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 2026-05-30 修[#6]：注入 scripts 目录以 import atomic_json（写作经验.json 原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json

# 2026-05-29 cluster 化：cluster 模式下「章阈值」语义改为「cluster 序号阈值」。
# cluster_lookup 把 cluster key → 末章号，从而沿用按章计的 last_validated_at_ch 比较
# （retire 的「N 章未验证」改成「N 个 cluster 未验证」时也复用 cluster→末章映射）。
try:
    import cluster_lookup as _cl  # noqa: E402
except Exception:  # pragma: no cover - 防御性
    _cl = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-30 修[#6]：裸 write_text → 原子写。evolve/promote 都经此写 写作经验.json，
    # 与 learning_loop.save_experience 同库 RMW；非原子写在 subprocess 被 kill 时留半截 JSON。
    # atomic_write_json 内部已 mkdir + tmp 唯一名 + fsync + os.replace 原子落盘。
    atomic_json.atomic_write_json(p, data)


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


def _cluster_to_end_ch(project_root: Path, cluster_key: str) -> int:
    """2026-05-29 cluster 化：把 cluster key 解析成其末章号，作为按章阈值的等价锚点。

    优先用 cluster_lookup.cluster_id_to_range（进度.cluster_blueprint + 事件簇.json）。
    解析不到（fluid 未切定）→ 回退用 cluster 序号 × 一个粗略系数，至少保证单调递增、
    不崩。返回值仅用于和 last_validated_at_ch 比较，不要求精确。
    """
    if _cl is not None:
        try:
            rng = _cl.cluster_id_to_range(project_root, cluster_key)
            if isinstance(rng, list) and len(rng) == 2 and isinstance(rng[1], int):
                return rng[1]
            num = _cl.cluster_num(cluster_key)
            if num is not None:
                return num  # 退化：按 cluster 序号当锚点（用于 cluster 计数语义）
        except Exception:  # pragma: no cover - 防御性
            pass
    m = re.search(r"(\d+)", str(cluster_key))
    return int(m.group(1)) if m else 0


def retire(project_root: Path, current_ch: int, threshold_ch: int = 30) -> dict:
    """long-unused patterns 标 retired（chapter 语义：current_ch - last > threshold_ch 章）"""
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


def retire_by_cluster(project_root: Path, cluster_key: str, threshold_clusters: int = 3) -> dict:
    """2026-05-29 cluster 化：按「N 个 cluster 未验证」淘汰，取代「30 章未验证」。

    把 last_validated_at_ch 反查回所属 cluster 序号，与当前 cluster 序号比较；
    相差 > threshold_clusters 个 cluster 则 retire。pattern 未记 cluster 来源时
    用末章号反查（cluster_lookup.ch_to_cluster_id）；反查不到则保守不淘汰（不误杀）。
    """
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    cur_num = (_cl.cluster_num(cluster_key) if _cl is not None else None)
    if cur_num is None:
        m = re.search(r"(\d+)", str(cluster_key))
        cur_num = int(m.group(1)) if m else 0
    cur_end_ch = _cluster_to_end_ch(project_root, cluster_key)

    retired_ids = []
    for category in ["success_patterns", "failure_patterns"]:
        for p in exp.get(category, []) or []:
            if not isinstance(p, dict) or p.get("status") == "retired":
                continue
            last_num = None
            # 优先 pattern 自带的 cluster 来源字段
            for key in ("last_validated_at_cluster", "recorded_at_cluster", "cluster_id"):
                if p.get(key) and _cl is not None:
                    last_num = _cl.cluster_num(p.get(key))
                    if last_num is not None:
                        break
            # 回退：用 last_validated_at_ch 反查 cluster
            if last_num is None:
                last_ch = p.get("last_validated_at_ch", 0)
                if _cl is not None:
                    cid = _cl.ch_to_cluster_id(project_root, last_ch)
                    last_num = _cl.cluster_num(cid) if cid else None
            if last_num is None:
                continue  # 反查不到，保守不淘汰
            if cur_num - last_num > threshold_clusters:
                p["status"] = "retired"
                p["retired_at_ch"] = cur_end_ch
                p["retired_at_cluster"] = "cluster_%03d" % cur_num
                retired_ids.append(p.get("id") or p.get("name", "?"))
    if retired_ids:
        save_json(exp_path, exp)
    return {"retired_count": len(retired_ids), "retired_ids": retired_ids,
            "mode": "cluster", "current_cluster": cur_num, "threshold_clusters": threshold_clusters}


# ── transfer_scope filter（防跨项目负迁移 · advisory · env 默认 active）──────────
# 背景：promote() 旧实现「无差别升级」—— 任何 usage≥5 & confidence≥0.8 的 pattern 都升进
# universal_skill_pool 跨项目复用。但「作者 A 的 idiolect 特异约束」（口癖/签名词/特有遣词/
# 角色名硬编码）升上去会污染作者 B（负迁移 negative transfer）。北极星⑤不干涉模型判断：
# 分类是 advisory 建议，pattern 自带显式 transfer_scope 字段时**以其为准**（作者档/模型权威优先）。
#
# 两类：
#   · "universal" 通用工艺 —— 可跨项目升级（节奏/结构/钩子/场景衔接/冲突编排等与作者无关的写作技法）
#   · "local"     作者 idiolect 特异 —— 锁本地不跨（口癖/签名词/特有用词/具体角色名）

# idiolect 特异信号：category 命中（语义子类）/ 文本命中（关键词）→ 锁本地
_IDIOLECT_CATEGORIES = {
    "idiolect", "voice", "voice_dna", "catchphrase", "diction", "lexical",
    "用词", "遣词", "口癖", "签名词", "声口", "腔调",
}
# 文本里出现这些词 → 作者特异（具体口头禅 / 个人化遣词 / 命名硬编码）
_IDIOLECT_TEXT_MARKERS = (
    "口癖", "签名词", "口头禅", "招牌词", "特有用词", "惯用词", "遣词",
    "作者特有", "作者标志", "本作专属", "专有名词", "角色名", "人名",
    "catchphrase", "idiolect", "signature word", "verbal tic",
)
# 通用工艺信号：category 命中 → 倾向 universal（用于显式正向判定，仅辅助）
_UNIVERSAL_CATEGORIES = {
    "rhythm", "pacing", "structure", "hook", "plot", "conflict", "scene",
    "transition", "tension", "节奏", "结构", "钩子", "情节", "冲突", "场景", "悬念",
}


def classify_transfer_scope(pattern: dict) -> str:
    """把一条 pattern 分类为 'universal'（可跨项目升级）或 'local'（作者 idiolect 锁本地）。

    优先级（advisory · 不干涉模型判断）：
      1. pattern 自带显式 transfer_scope ∈ {universal, local} → 直接采纳（作者档/模型权威）。
      2. idiolect 信号命中（category 或文本关键词）→ 'local'（保守锁本地，防负迁移）。
      3. 通用工艺 category 命中 → 'universal'。
      4. 都不命中 → 默认 'universal'（保持向后兼容：旧 promote 全升，新增过滤只拦明确特异项）。
    """
    if not isinstance(pattern, dict):
        return "universal"
    explicit = pattern.get("transfer_scope")
    if isinstance(explicit, str) and explicit.strip().lower() in ("universal", "local"):
        return explicit.strip().lower()

    cat = (pattern.get("category") or "").strip().lower()
    if cat in _IDIOLECT_CATEGORIES:
        return "local"

    # 文本信号：trigger / technique / description / name / why_works 拼起来扫 idiolect marker
    blob = " ".join(str(pattern.get(k, "")) for k in
                     ("trigger", "technique", "description", "name", "why_works")).lower()
    if any(m in blob for m in _IDIOLECT_TEXT_MARKERS):
        return "local"

    if cat in _UNIVERSAL_CATEGORIES:
        return "universal"
    return "universal"


def _transfer_scope_filter_active() -> bool:
    """env 默认 active。SKILL_TRANSFER_SCOPE_FILTER=0/false/off/no → 关（回退旧无差别升级）。"""
    v = os.environ.get("SKILL_TRANSFER_SCOPE_FILTER", "1").strip().lower()
    return v not in ("0", "false", "off", "no", "")


def promote(project_root: Path) -> dict:
    """高 usage + 高 confidence pattern 升 universal_skill_pool（跨项目）。

    2026-05-31 transfer_scope filter：升级前按 scope 过滤 —— 通用工艺才跨项目，作者
    idiolect 特异锁本地（防负迁移）。filter env 默认 active；关掉则回退旧无差别升级。
    分类是 advisory，pattern 自带 transfer_scope 字段时以其为准（不干涉模型/作者档判断）。
    """
    exp_path = project_root / "_数据库" / "写作经验.json"
    exp = load_json(exp_path, {})
    # frozen-aware 读（__file__ 扁平化同款）：bundle_root()/core/claude-home（dev 同值）。
    # 注：frozen 下 bundle 只读，pool 的 promote 写入会失败降级——distill/learning 路径非
    # GUI cluster-write MVP·留作阶段B残留（writable 学习产物迁用户态）。
    try:
        from frozen_util import bundle_root as _bundle_root
        pool_path = _bundle_root() / "core" / "claude-home" / "universal_skill_pool.json"
    except Exception:
        pool_path = Path(__file__).parent.parent / "claude-home" / "universal_skill_pool.json"
    pool = load_json(pool_path, {"universal_patterns": [], "_meta": {"created_at": datetime.now().isoformat(timespec="seconds")}})

    filter_active = _transfer_scope_filter_active()
    promoted_ids = []
    locked_local = []  # 达标但 scope=local 被锁本地的（advisory 上报，不重复评估）
    for category in ["success_patterns", "failure_patterns"]:
        for p in exp.get(category, []) or []:
            if not isinstance(p, dict):
                continue
            usage = p.get("usage_count", 0)
            conf = p.get("confidence", 0)
            if not (usage >= 5 and conf >= 0.8 and not p.get("promoted_to_universal")):
                continue
            scope = classify_transfer_scope(p)
            if filter_active and scope == "local":
                # 锁本地：标记一次，避免每轮重复评估；不升进 pool（防负迁移）
                if not p.get("transfer_scope_locked"):
                    p["transfer_scope"] = "local"
                    p["transfer_scope_locked"] = True
                    p["transfer_scope_locked_at"] = datetime.now().isoformat(timespec="seconds")
                    locked_local.append(p.get("id") or p.get("name", "?"))
                continue
            # 复制到 pool（universal · 或 filter 关闭时无差别升）
            universal_entry = dict(p)
            universal_entry["category"] = category
            universal_entry["source_project"] = exp_path.parent.parent.name
            universal_entry["promoted_at"] = datetime.now().isoformat(timespec="seconds")
            universal_entry["transfer_scope"] = "universal"
            pool["universal_patterns"].append(universal_entry)
            p["promoted_to_universal"] = True
            p.setdefault("transfer_scope", "universal")
            promoted_ids.append(p.get("id") or p.get("name", "?"))

    if promoted_ids or locked_local:
        if promoted_ids:
            save_json(pool_path, pool)
        save_json(exp_path, exp)
    return {"promoted_count": len(promoted_ids), "promoted_ids": promoted_ids,
            "pool_total": len(pool["universal_patterns"]),
            "transfer_scope_filter": "active" if filter_active else "off",
            "locked_local_count": len(locked_local), "locked_local_ids": locked_local}


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
    ap = argparse.ArgumentParser(
        description="skill_evolver · cluster 模式（--cluster）为 v26 主路径 / --ch 向后兼容"
    )
    ap.add_argument("project")
    ap.add_argument("action", choices=["evolve", "promote", "retire", "dashboard"])
    ap.add_argument("--ch", type=int, default=0, help="当前章号（chapter 兼容模式）")
    # 2026-05-29 cluster 化：plan cluster-save-state.plan.json:124 以 `evolve --cluster {key}`
    # 调用。argparse 不认 --cluster 会非 0 退出被 `|| true` 吞掉 → skill 演化静默不跑。
    ap.add_argument("--cluster", type=str, default=None,
                    help="cluster key（'001' / 'cluster_001'）· 主路径：章阈值换成 cluster 序号阈值")
    ap.add_argument("--retire-threshold", type=int, default=30,
                    help="chapter 模式：N 章未验证 retire（默认 30）")
    ap.add_argument("--retire-cluster-threshold", type=int, default=3,
                    help="cluster 模式：N 个 cluster 未验证 retire（默认 3）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()

    # cluster 模式：把 cluster key 解析成等价末章号，evolve 沿用按章逻辑（last_validated_at_ch=末章）；
    # retire 切到 cluster 序号阈值。
    if args.cluster:
        end_ch = _cluster_to_end_ch(project_root, args.cluster)
        if args.action == "evolve":
            r = evolve(project_root, end_ch)
        elif args.action == "promote":
            r = promote(project_root)
        elif args.action == "retire":
            r = retire_by_cluster(project_root, args.cluster, args.retire_cluster_threshold)
        else:
            r = dashboard(project_root)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0)

    # chapter 兼容模式
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
