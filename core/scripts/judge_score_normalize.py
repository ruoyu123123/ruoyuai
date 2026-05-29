"""judge_score_normalize.py — JudgeReport 评分归一化 + 多 judge 投票（v19.6 G2 新增）

业界 NovelCritique 论文证明：GPT-4o 即使 temperature=0 评分也"显著抖动"
解决：
- 评分归一化（z-score）：每个 judge 在历史评分分布上做 normalize
- 多 judge 投票：多数原则替代单一 judge

不训练小模型（长期规划），只做工程降噪。

用法：
    python judge_score_normalize.py <project> --ch <N>          # 单章 normalized + 投票
    python judge_score_normalize.py <project> --cluster <key>   # cluster 综合归一化（2026-05-29）
    python judge_score_normalize.py <project> --stats           # 各 judge 历史评分分布
退出码: 0 成功

2026-05-29 cluster 化：
  v27 freestyle 浮动章数下「单章投票」不再是稳定的检测/归一化单位 —— cluster 才是
  v2 检测层。新增 `--cluster <key>`：对 cluster 的关键章（账本 chapter_range 全部章）
  各自做单章投票，再综合（per-ch normalized final_score 均值/中位）+ 引入 cluster 级
  judge_grade 作为锚点对照。`--ch` 完整保留兼容。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_summary_reader as csr  # 2026-05-29 cluster 化：cluster 账本
    import cluster_lookup  # 2026-05-29 cluster 化：cluster_id ⇄ range
except Exception:  # 防御：缺模块时 --cluster 不可用，--ch 仍工作
    csr = None
    cluster_lookup = None


GRADE_TO_NUM = {"A": 5, "A-": 4.5, "B+": 4, "B": 3.5, "B-": 3, "C+": 2.5, "C": 2, "C-": 1.5, "D": 1, "F": 0}
NUM_TO_GRADE = sorted(GRADE_TO_NUM.items(), key=lambda kv: -kv[1])


def grade_to_num(g: str) -> float | None:
    if not g or g == "N/A":
        return None
    return GRADE_TO_NUM.get(g.strip())


def num_to_grade(n: float) -> str:
    for grade, val in NUM_TO_GRADE:
        if n >= val - 0.25:
            return grade
    return "F"


def collect_judge_history(project_root: Path) -> dict[str, list[float]]:
    """收集每个 judge_id 的历史 grade 数值列表。"""
    jr_dir = project_root / "_数据库" / ".judge_reports"
    if not jr_dir.is_dir():
        return {}
    history = {}
    for f in jr_dir.glob("ch_*_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        jid = d.get("judge_id", "?")
        g = grade_to_num(d.get("overall_grade", ""))
        if g is not None:
            history.setdefault(jid, []).append(g)
    return history


def normalize_score(score: float, history: list[float]) -> float:
    """z-score normalize 到 [0, 5]。"""
    if len(history) < 3:
        return score
    mean = statistics.mean(history)
    stdev = statistics.pstdev(history) or 1.0
    z = (score - mean) / stdev
    # z-score → [0, 5]：z=0 → 3.5（中位 B+），z=2 → 5（A），z=-2 → 1.5（C-）
    normalized = 3.5 + z * 0.75
    return max(0.0, min(5.0, normalized))


def chapter_vote(project_root: Path, ch: int) -> dict:
    """对本章所有 judge_reports 做投票 + 归一化。"""
    jr_dir = project_root / "_数据库" / ".judge_reports"
    if not jr_dir.is_dir():
        return {"error": "无 .judge_reports/"}
    chapter_reports = []
    for f in jr_dir.glob(f"ch_{ch:03d}_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        chapter_reports.append(d)
    if not chapter_reports:
        return {"error": f"ch{ch} 无 judge_reports"}

    history = collect_judge_history(project_root)
    raw_scores = []
    normalized_scores = []
    judge_outputs = []
    for r in chapter_reports:
        jid = r.get("judge_id", "?")
        g = r.get("overall_grade", "N/A")
        gn = grade_to_num(g)
        if gn is None:
            continue
        norm = normalize_score(gn, history.get(jid, []))
        raw_scores.append(gn)
        normalized_scores.append(norm)
        judge_outputs.append({
            "judge_id": jid,
            "raw_grade": g,
            "raw_score": gn,
            "normalized_score": round(norm, 2),
            "normalized_grade": num_to_grade(norm),
        })

    if not normalized_scores:
        return {"error": "无可投票评分"}

    # 投票：平均 + 中位（多数原则）
    avg = statistics.mean(normalized_scores)
    median = statistics.median(normalized_scores)
    final_score = (avg + median) / 2
    return {
        "ch": ch,
        "judges_voted": len(judge_outputs),
        "raw_avg": round(statistics.mean(raw_scores), 2),
        "normalized_avg": round(avg, 2),
        "normalized_median": round(median, 2),
        "final_score": round(final_score, 2),
        "final_grade": num_to_grade(final_score),
        "judge_outputs": judge_outputs,
    }


def _cluster_chapter_list(project_root: Path, cluster_key) -> tuple[str | None, list[int]]:
    """解出 cluster_id + 该 cluster 的物理章号列表（chapter_range 优先，回退账本 chapters）。"""
    if cluster_lookup is None or csr is None:
        return None, []
    cid = cluster_lookup.normalize_cluster_id(cluster_key)
    rng = cluster_lookup.cluster_id_to_range(project_root, cluster_key)
    chs: list[int] = []
    if rng and len(rng) == 2:
        chs = list(range(rng[0], rng[1] + 1))
    if not chs:
        # 回退账本 chapters keys
        for c in csr.get_clusters(project_root):
            if cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == cid:
                chs = sorted(int(k) for k in (c.get("chapters") or {}) if str(k).isdigit())
                break
    return cid, chs


def cluster_vote(project_root: Path, cluster_key) -> dict:
    """对一个 cluster 的关键章逐章投票后做综合归一化（2026-05-29 cluster 化）。"""
    if csr is None or cluster_lookup is None:
        return {"error": "cluster 模块不可用（cluster_summary_reader/cluster_lookup 缺失）"}
    cid, chs = _cluster_chapter_list(project_root, cluster_key)
    if not cid:
        return {"error": f"无法解析 cluster 标识: {cluster_key}"}
    # cluster 级 judge_grade 锚点
    cluster_grade = None
    for c in csr.get_clusters(project_root):
        if cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == cid:
            cluster_grade = c.get("judge_grade")
            break
    if not chs:
        return {"cluster_id": cid, "error": "cluster 章范围未知（chapter_range/账本均缺）",
                "cluster_judge_grade": cluster_grade}

    per_ch = []
    finals = []
    for ch in chs:
        r = chapter_vote(project_root, ch)
        if "final_score" in r:
            per_ch.append(r)
            finals.append(r["final_score"])
    if not finals:
        return {"cluster_id": cid, "chapters": chs, "cluster_judge_grade": cluster_grade,
                "error": "cluster 内无任何章有 judge_reports"}

    cluster_final = (statistics.mean(finals) + statistics.median(finals)) / 2
    return {
        "cluster_id": cid,
        "chapters_voted": [r["ch"] for r in per_ch],
        "cluster_judge_grade": cluster_grade,   # cluster 级综合评级（账本锚点对照）
        "per_chapter_final_scores": {r["ch"]: r["final_score"] for r in per_ch},
        "cluster_normalized_avg": round(statistics.mean(finals), 2),
        "cluster_normalized_median": round(statistics.median(finals), 2),
        "cluster_final_score": round(cluster_final, 2),
        "cluster_final_grade": num_to_grade(cluster_final),
        "per_chapter": per_ch,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--cluster", default=None,
                    help="cluster 标识（如 cluster_002 / 2）：对该 cluster 综合归一化（2026-05-29）")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.stats:
        history = collect_judge_history(project_root)
        print(f"=== judge 历史评分分布 ===")
        for jid, scores in history.items():
            if len(scores) < 2:
                print(f"  {jid}: 仅 {len(scores)} 条")
                continue
            mean = statistics.mean(scores)
            stdev = statistics.pstdev(scores)
            print(f"  {jid}: n={len(scores)} mean={mean:.2f} stdev={stdev:.2f}")
        sys.exit(0)

    if args.cluster is not None:
        r = cluster_vote(project_root, args.cluster)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.ch:
        r = chapter_vote(project_root, args.ch)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0)

    print("用法: judge_score_normalize.py <project> --cluster <key> | --ch <N> | --stats",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
