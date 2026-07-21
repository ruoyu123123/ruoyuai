#!/usr/bin/env python3
"""enum_consistency_gate.py — G3-ENUMKAPPA 标注一致性前置闸（蒸馏画骨 D2/D3 切 active 前必过）。

背景：D2（张力三向度 tension_type）/ D3（知识差三态 gap_code）是 novel-distill-analyzer 的
枚举型主观判断维度，不像句长/段长有确定性真理源——同一 cluster 换一次独立分析，判断可能漂移。
在把这类维度下发给 writer（active 注入·会真实改变生成内容）之前，先测「标注本身稳不稳」：
对同一 cluster 跑 N≥3 次独立 novel-distill-analyzer 分析（外部 spawn agent 产出·本脚本零 LLM
依赖），把每次的枚举点位按位置对齐后算众数占比 + Fleiss kappa，够稳才允许 active。

判据：众数占比 ≥ 0.6 或 Fleiss kappa ≥ 0.67 → PASS（允许 active）；否则 FAIL（保持 shadow）。
单 cluster 有效点（非 none/未分类）< 3 → low_confidence，不参与判定（保守不误判）。

北极星⑤：本闸只判「标注一致不一致」，不判断内容对错——绝不进 HARD_GATE_CODES，纯 advisory。

用法：
  python enum_consistency_gate.py <project_root> --cluster <cluster_id> --dim tension_type \
      --surface <path1> <path2> <path3> ...
  python enum_consistency_gate.py <project_root> --cluster <cluster_id> --dim gap_code \
      --surface <path1> <path2> <path3> ...

退出码：0 恒成功（advisory·结果写进报告的 should_inject 字段，不抛错·报告落
  _数据库/.enum_consistency/<cluster>_g3_<dim>.json）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# suspense/curiosity/surprise 三桶归一（D2·与 consolidate_author_profile._tension_type_bucket 同源）。
_TENSION_KEYWORDS = {
    "suspense": ("已知", "危险", "炸弹", "等爆", "倒计时", "高压开头", "悬"),
    "curiosity": ("想知道", "谜", "疑问", "为什么", "到底", "新设定"),
    "surprise": ("反转", "打脸", "没想到", "留白反转", "意外", "逆转"),
}
_GAP_CODES = ("reader_adv", "reader_disadv", "double_blind")


def _tension_type_bucket(s: str) -> str:
    s = s or ""
    t = s.strip().lower()
    if t in ("suspense", "curiosity", "surprise"):
        return t
    for bucket, kws in _TENSION_KEYWORDS.items():
        if any(kw in s for kw in kws):
            return bucket
    return "未分类"


def _gap_code_bucket(s: str) -> str:
    t = (s or "").strip().lower()
    return t if t in _GAP_CODES else "未分类"


def fleiss_kappa(rows: list[dict]) -> float:
    """Fleiss' kappa 纯 Python 实现（N 固定 rater、M item、K category）。

    rows: 每个 item 一条 {category: count}，同一 item 各 category 计数之和须相等（= rater 数）。
    全一致输入 → 1.0；类别分布均匀的完全随机标注 → 趋近 0。少于 2 个 item 或 rater 数 <2 → 0.0。
    """
    if len(rows) < 2:
        return 0.0
    n = sum(rows[0].values())
    if n < 2:
        return 0.0
    categories = sorted({k for row in rows for k in row})
    if len(categories) < 2:
        return 1.0  # 全部 item 都只标了同一类 → 完全一致
    N = len(rows)
    p_j = {c: 0 for c in categories}
    P_i_list = []
    for row in rows:
        counts = [row.get(c, 0) for c in categories]
        if sum(counts) != n:
            continue  # 该 item rater 数不齐 → 跳过（对齐阶段应已保证，双重防御）
        for c, cnt in zip(categories, counts):
            p_j[c] += cnt
        P_i = (sum(cnt * cnt for cnt in counts) - n) / (n * (n - 1))
        P_i_list.append(P_i)
    if not P_i_list:
        return 0.0
    total = N * n
    p_j = {c: v / total for c, v in p_j.items()}
    P_bar = sum(P_i_list) / len(P_i_list)
    P_bar_e = sum(v * v for v in p_j.values())
    if P_bar_e >= 1.0:
        return 1.0
    return (P_bar - P_bar_e) / (1 - P_bar_e)


def _extract_points(surface: dict, dim: str) -> list[tuple[float, str]]:
    """从一份 surface JSON 抽 (position_pct, bucketed_category) 点位列表。

    dim=tension_type：读 dim51_张力曲线（自带 pct 字段，0-100）。
    dim=gap_code：读 dim32_信息差管理.per_point（无 pct，按出现顺序等距映射到 0-100）。
    """
    qd = surface.get("qualitative_dims") or {}
    if dim == "tension_type":
        pts = qd.get("dim51_张力曲线") or []
        out = []
        for p in pts:
            if not isinstance(p, dict):
                continue
            pct = p.get("pct")
            tt = _tension_type_bucket(str(p.get("tension_type", "")))
            if isinstance(pct, (int, float)) and tt != "未分类":
                out.append((float(pct), tt))
        return out
    if dim == "gap_code":
        d32 = qd.get("dim32_信息差管理") or {}
        pts = d32.get("per_point") or []
        n = len(pts)
        out = []
        for i, p in enumerate(pts):
            if not isinstance(p, dict):
                continue
            if str(p.get("confidence", "")).strip().lower() == "low":
                continue
            gc = _gap_code_bucket(str(p.get("gap_code", "")))
            if gc == "未分类":
                continue
            pct = (i / n * 100) if n > 1 else 50.0
            out.append((pct, gc))
        return out
    raise ValueError(f"未知 dim: {dim}（只支持 tension_type / gap_code）")


def align_by_pct_bucket(samples: list[list[tuple[float, str]]], bucket_size: int = 10) -> list[dict]:
    """N 份独立采样的点位列表 → 按 pct 分桶(默认每 10% 一格)对齐 → 每桶一条 {category: count}。

    只保留「至少 2 份采样在该桶都有有效点」的桶（保守：单份采样的桶无法判一致性）。
    """
    n_buckets = 100 // bucket_size + 1
    per_bucket: list[Counter] = [Counter() for _ in range(n_buckets)]
    covered: list[set] = [set() for _ in range(n_buckets)]
    for sample_idx, pts in enumerate(samples):
        for pct, cat in pts:
            b = min(int(pct // bucket_size), n_buckets - 1)
            per_bucket[b][cat] += 1
            covered[b].add(sample_idx)
    rows = []
    for b, counter in enumerate(per_bucket):
        if len(covered[b]) >= 2 and counter:
            rows.append(dict(counter))
    return rows


def _mode_ratio(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    ratios = []
    for row in rows:
        total = sum(row.values())
        if total <= 0:
            continue
        ratios.append(max(row.values()) / total)
    return sum(ratios) / len(ratios) if ratios else 0.0


def run_enum_kappa_gate(surface_paths: list[Path], *, dim: str,
                         mode_threshold: float = 0.6, kappa_threshold: float = 0.67,
                         bucket_size: int = 10) -> dict:
    """核心判定：读 N 份 surface JSON → 抽点位 → 对齐 → 算一致性 → PASS/FAIL。"""
    samples = []
    for p in surface_paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[enum_consistency_gate] 跳过读取失败的 surface: {p} ({e})", file=sys.stderr)
            continue
        samples.append(_extract_points(data, dim))
    n_samples = len(samples)
    if n_samples < 3:
        return {"should_inject": False, "low_confidence": True, "n_samples": n_samples,
                "reason": f"有效 surface 采样数 {n_samples} < 3，无法判定一致性"}
    rows = align_by_pct_bucket(samples, bucket_size=bucket_size)
    total_valid_points = sum(sum(r.values()) for r in rows)
    if len(rows) < 3 or total_valid_points < 3:
        return {"should_inject": False, "low_confidence": True, "n_samples": n_samples,
                "n_aligned_buckets": len(rows), "total_valid_points": total_valid_points,
                "reason": "有效对齐点位不足 3 个（该维度在此 cluster 上信号太稀疏，无法判定）"}
    mode_ratio = round(_mode_ratio(rows), 4)
    kappa = round(fleiss_kappa(rows), 4)
    passed = mode_ratio >= mode_threshold or kappa >= kappa_threshold
    return {
        "should_inject": passed, "low_confidence": False, "n_samples": n_samples,
        "n_aligned_buckets": len(rows), "total_valid_points": total_valid_points,
        "mode_ratio": mode_ratio, "fleiss_kappa": kappa,
        "mode_threshold": mode_threshold, "kappa_threshold": kappa_threshold,
        "reason": ("PASS：众数占比或 kappa 达标，标注足够稳定" if passed
                   else "FAIL：众数占比与 kappa 均未达标，标注漂移过大，保持 shadow"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G3-ENUMKAPPA 标注一致性前置闸")
    ap.add_argument("project_root", type=Path)
    ap.add_argument("--cluster", required=True, help="cluster_id（仅用于报告落盘命名）")
    ap.add_argument("--dim", required=True, choices=("tension_type", "gap_code"))
    ap.add_argument("--surface", required=True, nargs="+", type=Path,
                     help="N 份独立 novel-distill-analyzer 产出的 surface JSON 路径（N>=3）")
    ap.add_argument("--mode-threshold", type=float, default=0.6)
    ap.add_argument("--kappa-threshold", type=float, default=0.67)
    ap.add_argument("--bucket-size", type=int, default=10)
    args = ap.parse_args(argv)

    result = run_enum_kappa_gate(
        args.surface, dim=args.dim,
        mode_threshold=args.mode_threshold, kappa_threshold=args.kappa_threshold,
        bucket_size=args.bucket_size,
    )
    result["dim"] = args.dim
    result["cluster"] = args.cluster

    out_dir = args.project_root / "_数据库" / ".enum_consistency"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.cluster}_g3_{args.dim}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = "PASS" if result.get("should_inject") else "FAIL"
    print(f"[enum_consistency_gate] {args.cluster}·{args.dim} → {verdict} "
          f"(mode_ratio={result.get('mode_ratio')}, kappa={result.get('fleiss_kappa')}) "
          f"→ {out_path}")
    return 0  # advisory：恒 exit 0，判定结果只影响是否切 active，不影响流水线


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
