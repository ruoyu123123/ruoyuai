#!/usr/bin/env python3
"""cluster_evaluator.py — 故事块蒸馏 v2 章程 6 维评分器

对比「原 cluster_arc / continuity / character_arc」与「复刻 cluster_arc / continuity / character_arc」，
计算 6 维评分（SFS 之外的 cluster 级评分），输出 PASS/WARN/FAIL。

v2 章程 Article 3 + 5：cluster 复刻评分维度（保留 SFS · 新增 6 维）：
  1. arc 形状拟合度（matched_reagan_shape 一致性）
  2. emotion_curve_normalized 偏离度（cosine ≥ 0.7）
  3. 衔接模板覆盖度（continuity 6 种 connection_type Jaccard ≥ 0.5）
  4. 钩子分布（kicker_count_per_chapter cosine ≥ 0.7）
  5. 场景概述比（scene_summary_ratio MAE ≤ 0.15）
  6. cluster voice_pack 合规（character voice avg_len ±20%）

输入：
  --ref-cluster-arc <path>    原 cluster_arc JSON
  --gen-cluster-arc <path>    复刻 cluster_arc JSON
  --ref-continuity <path>     原 cluster continuity JSON（可选 · 影响维 3）
  --gen-continuity <path>     复刻 cluster continuity JSON（可选）
  --ref-character-arc-dir <dir>  原 character_arcs/ 目录（可选 · 影响维 6）
  --gen-character-arc-dir <dir>  复刻 character_arcs/ 目录（可选）
  --output <path>             输出报告 JSON 路径

verdict 阈值：
  - 6 维全 PASS（每维 ≥ 0.7）→ PASS
  - 4-5 维 PASS → WARN
  - ≤ 3 维 PASS → FAIL

依据：memory feedback_cluster_distill_v2_charter
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path


REAGAN_EQUIVALENCE = {
    # 等价类：形状不同但情绪走向接近视作 0.7（不完全 0）
    "Rags-to-Riches": {"Rags-to-Riches", "Cinderella"},
    "Riches-to-Rags": {"Riches-to-Rags", "Tragedy"},
    "Man-in-a-Hole": {"Man-in-a-Hole"},
    "Icarus": {"Icarus"},
    "Cinderella": {"Cinderella", "Rags-to-Riches"},
    "Oedipus": {"Oedipus"},
}


def load_json(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[WARN] {path} JSON 解析失败: {e}", file=sys.stderr)
        return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def mae(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 1.0
    n = min(len(a), len(b))
    return sum(abs(x - y) for x, y in zip(a[:n], b[:n])) / n


# ============ 维度 1: arc 形状拟合度 ============

def score_arc_shape(ref_arc: dict, gen_arc: dict) -> tuple[float, dict]:
    ref_shape = (ref_arc or {}).get("matched_reagan_shape", "Unknown")
    gen_shape = (gen_arc or {}).get("matched_reagan_shape", "Unknown")
    if ref_shape == "Unknown" or gen_shape == "Unknown":
        return 0.0, {"ref": ref_shape, "gen": gen_shape, "verdict": "missing"}
    if ref_shape == gen_shape:
        return 1.0, {"ref": ref_shape, "gen": gen_shape, "verdict": "exact"}
    if gen_shape in REAGAN_EQUIVALENCE.get(ref_shape, set()):
        return 0.7, {"ref": ref_shape, "gen": gen_shape, "verdict": "equivalent"}
    return 0.0, {"ref": ref_shape, "gen": gen_shape, "verdict": "different"}


# ============ 维度 2: emotion_curve 偏离 ============

def score_emotion_curve(ref_arc: dict, gen_arc: dict) -> tuple[float, dict]:
    ref_c = (ref_arc or {}).get("emotion_curve_normalized") or []
    gen_c = (gen_arc or {}).get("emotion_curve_normalized") or []
    if not ref_c or not gen_c:
        return 0.0, {"verdict": "missing_curve"}
    cos = cosine_sim(ref_c, gen_c)
    return cos, {"cosine": round(cos, 3), "ref_len": len(ref_c), "gen_len": len(gen_c)}


# ============ 维度 3: 衔接模板覆盖度 ============

CONNECTION_TYPES = {
    "直接承接", "信息炸弹→静默回响", "时间跳跃", "空间跳转",
    "情绪落差", "悬念承接新视角", "其他"
}


def _extract_connection_types(continuity: dict | None) -> Counter:
    if not continuity:
        return Counter()
    types = []
    for t in continuity.get("transitions", []) or []:
        ct = t.get("connection_type")
        if ct:
            types.append(ct)
    return Counter(types)


def score_connection_coverage(ref_cont: dict, gen_cont: dict) -> tuple[float, dict]:
    ref_set = set(_extract_connection_types(ref_cont).keys())
    gen_set = set(_extract_connection_types(gen_cont).keys())
    if not ref_set:
        return 0.5, {"verdict": "no_ref_connections"}  # 中性分（数据缺失非复刻问题）
    if not gen_set:
        return 0.0, {"verdict": "no_gen_connections"}
    inter = ref_set & gen_set
    union = ref_set | gen_set
    jaccard = len(inter) / len(union) if union else 0.0
    return jaccard, {
        "ref_types": sorted(ref_set), "gen_types": sorted(gen_set),
        "jaccard": round(jaccard, 3),
        "missing_in_gen": sorted(ref_set - gen_set),
        "extra_in_gen": sorted(gen_set - ref_set),
    }


# ============ 维度 4: 钩子分布 ============

def score_kicker_distribution(ref_arc: dict, gen_arc: dict) -> tuple[float, dict]:
    ref_k = (ref_arc or {}).get("kicker_count_per_chapter") or []
    gen_k = (gen_arc or {}).get("kicker_count_per_chapter") or []
    if not ref_k or not gen_k:
        return 0.0, {"verdict": "missing_kickers"}
    ref_f = [float(x) for x in ref_k]
    gen_f = [float(x) for x in gen_k]
    cos = cosine_sim(ref_f, gen_f)
    return cos, {"cosine": round(cos, 3), "ref_total": sum(ref_f), "gen_total": sum(gen_f)}


# ============ 维度 5: 场景概述比 ============

def score_scene_summary_ratio(ref_arc: dict, gen_arc: dict) -> tuple[float, dict]:
    ref_r = (ref_arc or {}).get("scene_summary_ratio_per_chapter") or []
    gen_r = (gen_arc or {}).get("scene_summary_ratio_per_chapter") or []
    if not ref_r or not gen_r:
        return 0.0, {"verdict": "missing_ratios"}
    ref_f = [float(x) for x in ref_r]
    gen_f = [float(x) for x in gen_r]
    e = mae(ref_f, gen_f)
    # MAE ≤ 0.15 → 1.0；MAE ≥ 0.5 → 0.0；线性插值
    if e <= 0.15:
        s = 1.0
    elif e >= 0.5:
        s = 0.0
    else:
        s = 1.0 - (e - 0.15) / (0.5 - 0.15)
    return s, {"mae": round(e, 3), "ref_mean": round(sum(ref_f)/len(ref_f), 3),
               "gen_mean": round(sum(gen_f)/len(gen_f), 3)}


# ============ 维度 6: cluster voice_pack 合规 ============

def _load_character_arcs(dir_path: Path | None) -> dict[str, dict]:
    """读 character_arcs/*.json → {character_name: arc_dict}"""
    if dir_path is None or not dir_path.exists():
        return {}
    out = {}
    for f in dir_path.glob("*_emotion_arc.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            name = d.get("character") or f.stem.replace("_emotion_arc", "")
            out[name] = d
        except json.JSONDecodeError:
            continue
    return out


def score_voice_pack(ref_dir: Path | None, gen_dir: Path | None) -> tuple[float, dict]:
    ref_chars = _load_character_arcs(ref_dir)
    gen_chars = _load_character_arcs(gen_dir)
    if not ref_chars:
        return 0.5, {"verdict": "no_ref_character_arcs"}  # 中性
    if not gen_chars:
        return 0.3, {"verdict": "no_gen_character_arcs"}  # 复刻没角色 arc 算偏弱

    # 主要角色（前 3 个）对比 voice 平均长度
    ref_main = list(ref_chars.keys())[:3]
    matches = 0
    total = 0
    details = []
    for name in ref_main:
        ref = ref_chars[name]
        gen = gen_chars.get(name)
        # 复刻不复制角色名（v2 章程），voice 是按角色顺位对齐
        if not gen:
            # 用 gen 的第 N 个角色按顺位匹配
            gen_list = list(gen_chars.values())
            idx = ref_main.index(name)
            if idx < len(gen_list):
                gen = gen_list[idx]
        if not gen:
            continue
        ref_len = (ref.get("voice_signature") or {}).get("dialogue_avg_len", 0)
        gen_len = (gen.get("voice_signature") or {}).get("dialogue_avg_len", 0)
        if ref_len > 0 and gen_len > 0:
            ratio = gen_len / ref_len
            ok = 0.8 <= ratio <= 1.2  # ±20%
            matches += 1 if ok else 0
            total += 1
            details.append({"ref_char": name, "ref_len": ref_len, "gen_len": gen_len,
                            "ratio": round(ratio, 2), "ok": ok})
    if total == 0:
        return 0.3, {"verdict": "no_voice_signatures_comparable", "details": details}
    score = matches / total
    return score, {"matches": matches, "total": total, "details": details}


# ============ 主评分 ============

DIM_LABELS = [
    "arc 形状",
    "emotion_curve 偏离",
    "衔接模板覆盖",
    "钩子分布",
    "场景概述比",
    "voice_pack 合规",
]


def verdict_from_scores(scores: list[float], threshold: float = 0.7) -> str:
    passes = sum(1 for s in scores if s >= threshold)
    if passes == 6:
        return "PASS"
    if passes >= 4:
        return "WARN"
    return "FAIL"


def evaluate(ref_arc: dict | None, gen_arc: dict | None,
             ref_cont: dict | None, gen_cont: dict | None,
             ref_char_dir: Path | None, gen_char_dir: Path | None) -> dict:
    scores: list[float] = []
    details: list[dict] = []

    for fn, label in [
        (lambda: score_arc_shape(ref_arc, gen_arc), DIM_LABELS[0]),
        (lambda: score_emotion_curve(ref_arc, gen_arc), DIM_LABELS[1]),
        (lambda: score_connection_coverage(ref_cont, gen_cont), DIM_LABELS[2]),
        (lambda: score_kicker_distribution(ref_arc, gen_arc), DIM_LABELS[3]),
        (lambda: score_scene_summary_ratio(ref_arc, gen_arc), DIM_LABELS[4]),
        (lambda: score_voice_pack(ref_char_dir, gen_char_dir), DIM_LABELS[5]),
    ]:
        s, d = fn()
        scores.append(s)
        details.append({"dim": label, "score": round(s, 3), "passes": s >= 0.7, "detail": d})

    weighted_total = sum(scores) / len(scores) * 100  # 0-100 分
    verdict = verdict_from_scores(scores)

    return {
        "verdict": verdict,
        "weighted_total": round(weighted_total, 1),
        "threshold_per_dim": 0.7,
        "dims_passed": sum(1 for s in scores if s >= 0.7),
        "dims_total": 6,
        "scores_by_dim": details,
        "schema_version": "1.0",
        "produced_by": "cluster_evaluator.py v1 · v2 章程 6 维",
    }


def main():
    parser = argparse.ArgumentParser(
        description="cluster 复刻 6 维评分（v2 章程 Article 5）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ref-cluster-arc", required=True, type=Path,
                        help="原 cluster_arc_<id>.json 路径")
    parser.add_argument("--gen-cluster-arc", required=True, type=Path,
                        help="复刻 cluster_arc_<id>.json 路径（由 P1b 回灌脚本产）")
    parser.add_argument("--ref-continuity", type=Path,
                        help="原 cluster 衔接分析 JSON（可选 · 影响维 3）")
    parser.add_argument("--gen-continuity", type=Path,
                        help="复刻 cluster 衔接分析 JSON（可选）")
    parser.add_argument("--ref-character-arc-dir", type=Path,
                        help="原 character_arcs/ 目录（可选 · 影响维 6）")
    parser.add_argument("--gen-character-arc-dir", type=Path,
                        help="复刻 character_arcs/ 目录（可选）")
    parser.add_argument("--output", required=True, type=Path,
                        help="输出报告 JSON 路径")
    parser.add_argument("--strict", action="store_true",
                        help="verdict != PASS 时 exit 2（用于 plan_tracker 闸门）")
    args = parser.parse_args()

    ref_arc = load_json(args.ref_cluster_arc)
    gen_arc = load_json(args.gen_cluster_arc)
    if ref_arc is None:
        print(f"[ERROR] 原 cluster_arc 不存在或解析失败: {args.ref_cluster_arc}", file=sys.stderr)
        sys.exit(2)
    if gen_arc is None:
        print(f"[ERROR] 复刻 cluster_arc 不存在或解析失败: {args.gen_cluster_arc}", file=sys.stderr)
        sys.exit(2)

    ref_cont = load_json(args.ref_continuity)
    gen_cont = load_json(args.gen_continuity)

    report = evaluate(
        ref_arc, gen_arc,
        ref_cont, gen_cont,
        args.ref_character_arc_dir, args.gen_character_arc_dir,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[cluster_evaluator] verdict = {report['verdict']}", file=sys.stderr)
    print(f"     weighted_total = {report['weighted_total']} / 100", file=sys.stderr)
    print(f"     dims_passed = {report['dims_passed']} / 6", file=sys.stderr)
    for d in report["scores_by_dim"]:
        mark = "✅" if d["passes"] else "❌"
        print(f"     {mark} {d['dim']}: {d['score']}", file=sys.stderr)
    print(f"     报告: {args.output}", file=sys.stderr)

    if args.strict and report["verdict"] != "PASS":
        print(f"[ERROR · strict] verdict={report['verdict']} ≠ PASS → exit 2", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
