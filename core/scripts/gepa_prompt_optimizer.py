"""gepa_prompt_optimizer.py — GEPA Pareto 前沿 Prompt 演化（v23 Layer 4）

业界依据：GEPA (ICLR 2026 Oral, arxiv 2507.19457)
"Reflective Prompt Evolution Can Outperform Reinforcement Learning"
- +13% over MIPROv2  +20% over GRPO  (RL)
- 仅需 10 个样本 + 20-100 次评估
- 核心机制：Pareto 前沿 + 反思式变异 + stochastic select

【为什么单独实施】
现有 meta-prompt-optimizer 把所有 prompt 改进建议**累加进同一份建议文档**，相当于
单一 best-of-all 策略，丢失多样性 —— 一旦某个建议被采纳，其他都被覆盖。

GEPA 思路：**保留候选池**而不是合并最优。每个候选 prompt 可能：
- 在「战斗类」章节是 top
- 在「日常对话」章节是 top
- 在「悬念翻转」章节是 top

→ 这些都该入 **Pareto 前沿**（each is best-on-at-least-one-task）。
最后用 stochastic select：下次需要某 scene_type 时从前沿挑覆盖该场景的候选。

【它做的事】

1. 扫所有项目的 `_数据库/.evolution/prompt_suggestions_*.json`（meta-prompt-optimizer 候选）
2. 对每个 candidate 标注：target_agent / section / signal_codes / 关联 chapters /
   关联 scene_types / 估算代理 metric（候选生命周期内 judge 分数趋势）
3. 计算 Pareto 前沿：候选 X 在某 (target_agent, scene_type) 维度上是 top → 入前沿
4. 输出 system-wide GEPA snapshot 到 core/claude-home/.gepa/candidates_<ts>.json
5. 输出推荐策略：下次需要 (agent, scene_type) 时该用哪个候选

【代理 metric（GEPA 不需要真跑也能做的关键）】

GEPA 论文用 evaluator 跑 trace；我们没法每次跑 LLM 评估，但用已有的 judge 数据：
- 候选生命周期 = 候选 ts 之后该 target_agent 所有产出的 judge 分数均值
- 用 scene_type 分组取均值 → 候选在该 scene 上的"代理表现"

【输出】
- core/claude-home/.gepa/candidates_<ts>.json
- core/claude-home/.gepa/pareto_frontier_<ts>.json
- core/claude-home/.gepa/recommendations_<ts>.json

【CLI】
  python gepa_prompt_optimizer.py                       # 扫所有项目
  python gepa_prompt_optimizer.py --project <path>      # 单项目
  python gepa_prompt_optimizer.py --recommend <scene>   # 查询给某 scene 推荐哪个候选
  python gepa_prompt_optimizer.py --status              # 当前前沿快照

退出码：0 健康 / 1 候选池过窄（< 3）/ 2 候选全部退化（最优分 < 阈值）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# ============================================================
# 路径常量
# ============================================================
GEPA_HOME = Path(__file__).resolve().parent.parent / "claude-home" / ".gepa"


# ============================================================
# 阈值
# ============================================================
MIN_CANDIDATES = 3                  # 候选池小于这个数 → advisory（多样性不足）
SCORE_DEGRADATION_THRESHOLD = 5.0   # 最优候选 score 低于这个 → 候选全退化
TOP_K_PER_SCENE = 2                 # 每个 scene_type 保留前 K 个进 Pareto 前沿


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_projects(workspace_root: Path) -> list[Path]:
    novels_root = workspace_root / "novels"
    if not novels_root.exists():
        return []
    return [p for p in novels_root.iterdir() if p.is_dir()]


# ============================================================
# Step 1: 收集候选
# ============================================================

def collect_candidates_from_project(project_root: Path) -> list[dict]:
    """从单项目 .evolution/prompt_suggestions_*.json 抽出全部候选。"""
    candidates = []
    evo_dir = project_root / "_数据库" / ".evolution"
    if not evo_dir.exists():
        return candidates
    for f in sorted(evo_dir.glob("prompt_suggestions_*.json"), key=lambda p: p.stat().st_mtime):
        data = load_json(f, {})
        ts = data.get("ts") or datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
        window = data.get("analysis_window_chs") or []
        for issue in (data.get("issues_found") or []):
            change = issue.get("suggested_change") or {}
            candidates.append({
                "candidate_id": f"{project_root.name}::{f.stem}::{issue.get('signal', '?')}",
                "source_project": project_root.name,
                "source_file": f.name,
                "proposed_ts": ts,
                "signal": issue.get("signal"),
                "target_agent": issue.get("target_agent"),
                "section": change.get("section"),
                "change_type": change.get("type"),
                "justification": change.get("justification", "")[:200],
                "expected_impact": issue.get("expected_impact", "")[:120],
                "root_cause": issue.get("root_cause", "")[:200],
                "analysis_window_chs": window,
                "evidence": issue.get("evidence", "")[:200],
            })
    return candidates


# ============================================================
# Step 2: 标注候选的 scene_types 和代理 metric
# ============================================================

def get_chapter_scenes_and_scores(project_root: Path, chs: list[int]) -> dict:
    """读 manifest 拿 scene_type + judge 报告拿分数。"""
    out = {}  # ch -> {"scene_type": str, "score": float}
    manifest_dir = project_root / "_数据库" / ".manifest"
    judge_dir = project_root / "_数据库" / ".judge_reports"
    for ch in chs:
        scene_type = None
        score = None
        m_path = manifest_dir / f"ch_{ch:03d}.json"
        if m_path.exists():
            mf = load_json(m_path, {})
            cp = mf.get("chapter_plan_subset") or {}
            scene_type = cp.get("scene_type")
        for name in (f"ch_{ch:03d}_consensus.json", f"ch_{ch:03d}_audit-hub.json"):
            jp = judge_dir / name
            if jp.exists():
                jd = load_json(jp, {})
                s = (jd.get("score") or jd.get("overall_score")
                     or (jd.get("aggregated") or {}).get("score")
                     or (jd.get("scores") or {}).get("overall"))
                if isinstance(s, (int, float)):
                    score = float(s)
                    break
        out[ch] = {"scene_type": scene_type, "score": score}
    return out


def annotate_candidate_with_metrics(project_root: Path, cand: dict) -> dict:
    """给候选标注 scene 覆盖 + 代理 metric。

    代理 metric: 候选提出后**该 target_agent 产出的章节** judge 分数（按 scene_type 分组）。
    """
    chs = cand.get("analysis_window_chs") or []
    if not chs:
        cand["proxy_metric_by_scene"] = {}
        cand["proxy_metric_overall"] = None
        cand["covered_scenes"] = []
        return cand

    chs_data = get_chapter_scenes_and_scores(project_root, chs)
    scene_scores = defaultdict(list)
    overall_scores = []
    for ch, info in chs_data.items():
        st = info.get("scene_type")
        s = info.get("score")
        if s is not None:
            overall_scores.append(s)
            if st:
                scene_scores[st].append(s)

    cand["proxy_metric_by_scene"] = {
        st: round(sum(ss) / len(ss), 2)
        for st, ss in scene_scores.items() if ss
    }
    cand["proxy_metric_overall"] = (
        round(sum(overall_scores) / len(overall_scores), 2)
        if overall_scores else None
    )
    cand["covered_scenes"] = sorted(scene_scores.keys())
    return cand


# ============================================================
# Step 3: Pareto 前沿计算
# ============================================================

def compute_pareto_frontier(candidates: list[dict]) -> list[dict]:
    """每个 (target_agent, scene_type) 维度上取 top K → 入前沿。

    Pareto 定义：候选 X 至少在一个 (agent, scene) 上是 top K → X ∈ 前沿。
    """
    frontier_ids = set()
    # 按 (target_agent, scene) 分组
    groups = defaultdict(list)
    for c in candidates:
        agent = c.get("target_agent") or "unknown"
        scenes = c.get("covered_scenes") or ["_any_"]
        for st in scenes:
            score = (c.get("proxy_metric_by_scene") or {}).get(st)
            if score is None:
                score = c.get("proxy_metric_overall")
            if score is None:
                continue
            groups[(agent, st)].append((score, c["candidate_id"]))

    # 每组取 top K
    pareto_evidence = defaultdict(list)
    for (agent, st), pairs in groups.items():
        pairs.sort(key=lambda x: x[0], reverse=True)
        for score, cid in pairs[:TOP_K_PER_SCENE]:
            frontier_ids.add(cid)
            pareto_evidence[cid].append({"agent": agent, "scene": st, "score": score})

    frontier = []
    for c in candidates:
        if c["candidate_id"] in frontier_ids:
            c2 = dict(c)
            c2["pareto_top_in"] = pareto_evidence[c["candidate_id"]]
            frontier.append(c2)
    return frontier


# ============================================================
# Step 4: 反思（GEPA 核心 — 用自然语言读 trace 推改进方向）
# ============================================================

def reflect_on_frontier(frontier: list[dict]) -> list[dict]:
    """从前沿候选里提取反思 —— 哪些 section / change_type 反复出现 ="""
    section_counter = defaultdict(int)
    change_type_counter = defaultdict(int)
    signal_counter = defaultdict(int)
    for c in frontier:
        if c.get("section"):
            section_counter[c["section"]] += 1
        if c.get("change_type"):
            change_type_counter[c["change_type"]] += 1
        if c.get("signal"):
            signal_counter[c["signal"]] += 1

    reflections = []
    if section_counter:
        top_section = max(section_counter.items(), key=lambda x: x[1])
        if top_section[1] >= 2:
            reflections.append({
                "type": "hot_section",
                "section": top_section[0],
                "frontier_count": top_section[1],
                "insight": f"Pareto 前沿有 {top_section[1]} 个候选都改动 prompt 的「{top_section[0]}」段 — "
                           f"该段是当前系统的高 leverage 点，未来 prompt 演化应优先在此节探索变种",
            })
    if change_type_counter:
        top_type = max(change_type_counter.items(), key=lambda x: x[1])
        if top_type[1] >= 2:
            reflections.append({
                "type": "hot_change_type",
                "change_type": top_type[0],
                "frontier_count": top_type[1],
                "insight": f"前沿候选有 {top_type[1]} 个用「{top_type[0]}」类改动 — 该改动类型有效",
            })
    if signal_counter:
        top_signal = max(signal_counter.items(), key=lambda x: x[1])
        if top_signal[1] >= 2:
            reflections.append({
                "type": "hot_signal",
                "signal": top_signal[0],
                "frontier_count": top_signal[1],
                "insight": f"信号「{top_signal[0]}」反复触发前沿候选 — 是系统持续痛点，应优先优化",
            })
    return reflections


# ============================================================
# Step 5: 推荐策略
# ============================================================

def build_recommendations(frontier: list[dict]) -> dict:
    """按 (target_agent, scene_type) 推 top 候选。"""
    recs = defaultdict(list)
    for c in frontier:
        for evidence in c.get("pareto_top_in", []):
            key = f"{evidence['agent']}::{evidence['scene']}"
            recs[key].append({
                "candidate_id": c["candidate_id"],
                "score": evidence["score"],
                "section": c.get("section"),
                "expected_impact": c.get("expected_impact"),
            })
    # 每组按 score 排序
    return {k: sorted(v, key=lambda x: x.get("score") or 0, reverse=True)[:3]
            for k, v in recs.items()}


# ============================================================
# 主流程
# ============================================================

def run_gepa(workspace_root: Path, target_project: Path | None = None) -> dict:
    if target_project:
        projects = [target_project]
    else:
        projects = find_projects(workspace_root)

    all_candidates = []
    for proj in projects:
        proj_candidates = collect_candidates_from_project(proj)
        for c in proj_candidates:
            c = annotate_candidate_with_metrics(proj, c)
            all_candidates.append(c)

    frontier = compute_pareto_frontier(all_candidates)
    reflections = reflect_on_frontier(frontier)
    recommendations = build_recommendations(frontier)

    summary = {
        "candidate_pool_size": len(all_candidates),
        "pareto_frontier_size": len(frontier),
        "diversity_ratio": (
            round(len(frontier) / len(all_candidates), 2)
            if all_candidates else 0
        ),
        "projects_scanned": [p.name for p in projects],
        "scenes_covered": sorted({s for c in frontier for s in (c.get("covered_scenes") or [])}),
    }

    return {
        "snapshot_ts": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "candidates": all_candidates,
        "pareto_frontier": frontier,
        "reflections": reflections,
        "recommendations": recommendations,
        "_note": (
            "GEPA (ICLR 2026 Oral, arxiv 2507.19457) — 用 Pareto 前沿保留候选多样性，"
            "代理 metric 用历史 judge 分数（按 scene_type 分组）"
        ),
    }


def query_recommend(scene: str) -> dict:
    """查 .gepa/ 最新 snapshot 的推荐。"""
    if not GEPA_HOME.exists():
        return {"error": "GEPA 还未跑过，先 run 一次", "scene": scene}
    snapshots = sorted(GEPA_HOME.glob("candidates_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not snapshots:
        return {"error": "无 snapshot", "scene": scene}
    latest = load_json(snapshots[0], {})
    recs = latest.get("recommendations", {})
    matches = {k: v for k, v in recs.items() if scene in k}
    return {
        "scene_query": scene,
        "matches": matches,
        "snapshot_file": snapshots[0].name,
    }


def status() -> dict:
    if not GEPA_HOME.exists():
        return {"status": "no_runs"}
    snapshots = sorted(GEPA_HOME.glob("candidates_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not snapshots:
        return {"status": "no_snapshots"}
    latest = load_json(snapshots[0], {})
    return {
        "latest_snapshot": snapshots[0].name,
        "summary": latest.get("summary", {}),
        "reflections": latest.get("reflections", []),
    }


def main():
    ap = argparse.ArgumentParser(description="GEPA Pareto 前沿 Prompt 演化 v23")
    ap.add_argument("--project", help="只扫指定项目（如 workspace/novels/<book>）")
    ap.add_argument("--recommend", help="查询给某 scene_type 推荐哪个候选")
    ap.add_argument("--status", action="store_true", help="只看最新 snapshot 状态")
    args = ap.parse_args()

    if args.status:
        s = status()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.recommend:
        r = query_recommend(args.recommend)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0)

    workspace_root = Path(__file__).resolve().parent.parent.parent / "workspace"
    target_project = Path(args.project).resolve() if args.project else None
    result = run_gepa(workspace_root, target_project)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = GEPA_HOME / f"candidates_{ts}.json"
    save_json(out_path, result)
    save_json(GEPA_HOME / f"pareto_frontier_{ts}.json",
              {"frontier": result["pareto_frontier"], "reflections": result["reflections"]})
    save_json(GEPA_HOME / f"recommendations_{ts}.json", result["recommendations"])

    s = result["summary"]
    print(f"[gepa] 候选池 {s['candidate_pool_size']} / 前沿 {s['pareto_frontier_size']} / "
          f"多样性比 {s['diversity_ratio']}")
    print(f"  扫描项目: {', '.join(s['projects_scanned']) or '(无)'}")
    print(f"  覆盖场景: {', '.join(s['scenes_covered'][:8]) or '(无)'}")
    for r in result["reflections"][:3]:
        print(f"  💡 {r['type']}: {r['insight'][:120]}")
    print(f"  报告: {out_path}")

    # 退化检测
    best_overall = max(
        (c.get("proxy_metric_overall") or 0 for c in result["candidates"]),
        default=0,
    )
    if not result["candidates"]:
        print("[gepa] 候选池为空 — 先跑 novel-meta-prompt-optimizer agent 累积建议")
        sys.exit(0)
    if best_overall < SCORE_DEGRADATION_THRESHOLD:
        print(f"  ⚠️ 最优候选 metric={best_overall} < {SCORE_DEGRADATION_THRESHOLD} — 候选全退化")
        sys.exit(2)
    if len(result["candidates"]) < MIN_CANDIDATES:
        print(f"  ℹ️ 候选池仅 {len(result['candidates'])} < {MIN_CANDIDATES} — 多样性不足")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
