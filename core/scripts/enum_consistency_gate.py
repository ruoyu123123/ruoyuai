#!/usr/bin/env python3
"""enum_consistency_gate.py — G3-ENUMKAPPA 枚举维标注一致性前置闸（D2-5·STERNBERG 域）。

北极星⑤⑥：本闸是**顾问/实验**层——只决定枚举维（dim51 tension_type 三向度）
是否**稳定到可注入 active**（should_inject 开关），**绝不阻断 distill 主流程**、
**绝不进 audit_hub.HARD_GATE_CODES**、**永不抛异常**（全 advisory·exit0）。

R2 第5条核心：enum 维注入 active 前必须先证「同一作者同一 cluster 多次标注稳定」，
否则注入的是噪声（惊悚乐园几分之差 rollback 教训：分不清噪声 vs 改进·这里前置堵）。
一致性判据用业界 N-raters 标准：Fleiss kappa（≥0.67 substantial）+ 众数占比（≥0.6）。

弱模型适配（gemini·thinking LOW）：
  · llm_transport.generate 无 seed 参数（已核实）→ 多 seed 靠 N 次独立调用 + 采样抖动
  · dim51 各点 pct 位置 N 次不齐 → align_by_pct_bucket 按 10% 粗格对齐
  · 单 cluster 有效 type 点（非 未分类/none）<3 → low_confidence·该维不注入

挂载（独立可被外部调用·本批不改 distill_surface_runner）：
  python enum_consistency_gate.py <project_root> --cluster cluster_001 --n 3
退出码恒 0（advisory）。报告落 `_数据库/.enum_consistency/<cluster>_g3.json`。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# 默认审计的枚举维：dim51_张力曲线 列表下每点的 tension_type 字段（Sternberg 三向度）。
DEFAULT_DIMS = ("dim51_张力曲线.tension_type",)
# Sternberg 三向度业界阈值（R2 给数·首版可配·实跑后校准记 distillation_log）。
DEFAULT_MODE_THRESHOLD = 0.6      # 众数占比 substantial 经验线
DEFAULT_KAPPA_THRESHOLD = 0.67    # Fleiss kappa substantial 业界线
DEFAULT_N_SAMPLES = 3
# 单 cluster 有效 type 点 <3 → low_confidence（R2 第5条：占比需跨 cluster 累积）。
MIN_VALID_POINTS = 3
PCT_BUCKET_SIZE = 10              # 按 10% 粗格对齐 N 次不齐的点


# ───────────────────────── Fleiss kappa（纯 Python·零依赖） ─────────────────────────
def fleiss_kappa(rows: list[dict]) -> float:
    """Fleiss kappa（N raters 固定）。

    rows: 每个 item 一个 {category: count} 字典；同一 item 的 count 之和 = rater 数 n，
    **要求所有 item 的 n 相同**（Fleiss 前提）。不满足或 item 不足 → 退化返回（见下）。

    返回值约定（便于上游判据 + 单测）：
      · 全部 rater 把所有 item 都标同一类（完全一致·分母 1-Pe == 0）→ 返回 1.0
      · 无有效 item / 单 item 不足以计算 → 返回 0.0（保守·当作不一致·不放行注入）
      · 否则标准 Fleiss 公式：kappa = (Pbar - Pe) / (1 - Pe)
    全一致 → 1.0；类别均匀随机 → ≈0（可能轻微负，是 Fleiss 正常行为，不裁剪）。
    """
    items = [r for r in rows if isinstance(r, dict) and sum(r.values()) > 0]
    if not items:
        return 0.0
    # rater 数 n：取众数（容忍个别 item 缺 rater）；只保留 n_i == n 的 item 参与计算。
    counts_per_item = [int(sum(r.values())) for r in items]
    n = Counter(counts_per_item).most_common(1)[0][0]
    if n < 2:                                  # 单 rater 无法谈一致性
        return 0.0
    items = [r for r in items if int(sum(r.values())) == n]
    big_n = len(items)
    if big_n == 0:
        return 0.0
    # 全类别集合
    cats = sorted({c for r in items for c in r})
    # 每类总分配数 → p_j
    total_assignments = big_n * n
    p_j = {c: sum(int(r.get(c, 0)) for r in items) / total_assignments for c in cats}
    p_e = sum(v * v for v in p_j.values())
    # 每 item 的一致度 P_i = (Σ n_ij^2 - n) / (n(n-1))
    denom_i = n * (n - 1)
    p_i_sum = 0.0
    for r in items:
        sq = sum(int(r.get(c, 0)) ** 2 for c in cats)
        p_i_sum += (sq - n) / denom_i
    p_bar = p_i_sum / big_n
    if abs(1.0 - p_e) < 1e-12:                 # 所有分配落同一类 → 完全一致
        return 1.0
    return round((p_bar - p_e) / (1.0 - p_e), 4)


# ───────────────────────── 按 pct 10% 粗格对齐 N 次标注 ─────────────────────────
def _resolve_dim_field(dim_spec: str) -> tuple[str, str | None]:
    """'dim51_张力曲线.tension_type' → ('dim51_张力曲线', 'tension_type')。无 '.' → field None。"""
    if "." in dim_spec:
        dim, field = dim_spec.split(".", 1)
        return dim, field
    return dim_spec, None


def _extract_points(sample: dict, dim: str) -> list[dict]:
    """从单次标注（judge .data）抽 qualitative_dims[dim] 的点列表（容错·非 list 返 []）。"""
    if not isinstance(sample, dict):
        return []
    qd = sample.get("qualitative_dims")
    container = qd if isinstance(qd, dict) else sample   # 容忍直接挂 dim 的扁平形态
    pts = container.get(dim) if isinstance(container, dict) else None
    return [p for p in pts if isinstance(p, dict)] if isinstance(pts, list) else []


def align_by_pct_bucket(samples: list, dim: str, field: str,
                        bucket_fn=None) -> list[dict]:
    """N 次标注按 pct 10% 粗格对齐 → 每格一个 {category: count}（item·喂 fleiss_kappa）。

    samples: N 份 judge .data（点数/位置可不齐）。
    dim: 'dim51_张力曲线'；field: 'tension_type'（每点取此字段值）。
    bucket_fn: 把字段原始值归一成类别（如 consolidate._tension_type_bucket）；None 则原值。

    解决「一次 4 点一次 3 点」：每点按 floor(pct/10) 落进同一 10% 格，
    跨 N 次同格的字段值累计成该格的 {category: count}。归一后为 未分类/none 的值**剔除**
    （不计入分母·保守不误分类）。只保留至少有 1 个有效类别的格。
    """
    bf = bucket_fn or (lambda x: x)
    grid: dict[int, Counter] = {}
    for sample in samples:
        for p in _extract_points(sample, dim):
            pct = p.get("pct")
            try:
                pv = float(pct)
            except (TypeError, ValueError):
                continue
            g = int(pv // PCT_BUCKET_SIZE)
            raw = p.get(field, "") if field else p
            cat = bf(str(raw))
            if cat in (None, "", "未分类", "none", "其他"):
                continue                       # 无效类别不入分母（保守）
            grid.setdefault(g, Counter())[cat] += 1
    # 按格序输出（pct 升序）·每格转成普通 dict{category:count}
    return [dict(grid[g]) for g in sorted(grid) if sum(grid[g].values()) > 0]


def _mode_share(item: dict) -> float:
    """单 item 众数占比 = 最高类别 count / 总 count。"""
    total = sum(item.values())
    if total <= 0:
        return 0.0
    return max(item.values()) / total


# ───────────────────────── 主闸 ─────────────────────────
def _default_judge_fn():
    """默认走真 run_judge（延迟 import·避免顶层依赖热路径）。"""
    import judge_runner as jr
    return jr.run_judge


def _judge_once(judge_fn, project_root: Path, cluster_id: str):
    """调一次 judge 产 surface·返回 .data dict（任何异常吞成空 dict·北极星⑤）。"""
    try:
        outcome = judge_fn(
            "novel-distill-analyzer", project_root,
            params={"CLUSTER_ID": cluster_id},
        )
    except Exception as e:                      # noqa: BLE001 — 闸永不抛
        print(f"[g3] judge 调用异常（吞·advisory）: {e}", file=sys.stderr)
        return {}
    data = getattr(outcome, "data", None)
    if data is None and isinstance(outcome, dict):
        data = outcome
    return data if isinstance(data, dict) else {}


def run_enum_kappa_gate(project_root, cluster_id, *,
                        dims: tuple = DEFAULT_DIMS,
                        n_samples: int = DEFAULT_N_SAMPLES,
                        mode_threshold: float = DEFAULT_MODE_THRESHOLD,
                        kappa_threshold: float = DEFAULT_KAPPA_THRESHOLD,
                        judge_fn=None) -> dict:
    """枚举维标注一致性前置闸（N 次独立标注 → Fleiss kappa + 众数占比）。

    judge_fn(agent, project_root, *, params, ...)：默认真 run_judge；测试传 mock。
    流程：N 次调 judge → 抽 dims 各点字段 → 延迟 import consolidate._tension_type_bucket
    归一 → 按 10% 格对齐 → 有效点 <3 标 low_confidence·should_inject=False；
    否则 众数占比≥mode_threshold 或 kappa≥kappa_threshold → should_inject=True。

    返回 {should_inject, mode_ratio, fleiss_kappa, low_confidence}（+落 advisory 报告）。
    永不抛异常·exit0。
    """
    project_root = Path(project_root)
    if judge_fn is None:
        judge_fn = _default_judge_fn()
    # 延迟 import 归一函数（避免顶层 import consolidate·只读复用·绝不改 consolidate）。
    try:
        from consolidate_author_profile import _tension_type_bucket as _bucket
    except Exception:                           # noqa: BLE001 — 兜底身份映射
        _bucket = lambda s: (s or "").strip().lower() or "未分类"

    result = {
        "cluster_id": cluster_id,
        "should_inject": False,
        "mode_ratio": 0.0,
        "fleiss_kappa": 0.0,
        "low_confidence": True,
        "n_samples": int(n_samples),
        "dims": list(dims),
    }
    try:
        # ① N 次独立标注
        samples = [_judge_once(judge_fn, project_root, cluster_id)
                   for _ in range(max(1, int(n_samples)))]
        # ② 对每个声明的枚举维对齐（当前只 dim51.tension_type·多维则取首个有效维主判）
        per_dim_items: dict[str, list[dict]] = {}
        for dim_spec in dims:
            dim, field = _resolve_dim_field(dim_spec)
            items = align_by_pct_bucket(samples, dim, field, bucket_fn=_bucket)
            per_dim_items[dim_spec] = items
        # 主判维 = 第一个声明维（dim51.tension_type）
        primary = dims[0] if dims else ""
        items = per_dim_items.get(primary, [])
        total_valid_points = sum(sum(it.values()) for it in items)
        result["total_valid_points"] = total_valid_points
        result["aligned_buckets"] = len(items)
        # ③ 有效点 <3 → low_confidence（R2 第5条·占比需跨 cluster 累积）
        if total_valid_points < MIN_VALID_POINTS:
            result["low_confidence"] = True
            result["should_inject"] = False
            result["reason"] = (f"有效 type 点 {total_valid_points} < {MIN_VALID_POINTS}·"
                                f"low_confidence·该维本 cluster 不注入（占比需跨 cluster 累积）")
        else:
            result["low_confidence"] = False
            # ④ 众数占比（各对齐格众数占比均值）+ Fleiss kappa
            mode_ratio = round(sum(_mode_share(it) for it in items) / len(items), 4)
            kf = fleiss_kappa(items)
            passed = (mode_ratio >= mode_threshold) or (kf >= kappa_threshold)
            result["mode_ratio"] = mode_ratio
            result["fleiss_kappa"] = kf
            result["should_inject"] = bool(passed)
            result["reason"] = (
                f"众数占比 {mode_ratio} (阈 {mode_threshold}) / Fleiss kappa {kf} "
                f"(阈 {kappa_threshold}) → {'PASS·允许 active 注入' if passed else 'FAIL·保持 shadow·建议补 few-shot/兜底映射'}")
    except Exception as e:                      # noqa: BLE001 — 闸永不抛（advisory·exit0）
        result["error"] = f"{type(e).__name__}: {e}"
        result["should_inject"] = False
        result["low_confidence"] = True
        result["reason"] = "闸内部异常（吞·advisory·保守不注入）"

    _write_report(project_root, cluster_id, result)
    return result


def _write_report(project_root: Path, cluster_id: str, result: dict):
    """落 advisory 报告 _数据库/.enum_consistency/<cluster>_g3.json（落盘失败也不抛）。"""
    try:
        out_dir = Path(project_root) / "_数据库" / ".enum_consistency"
        out_dir.mkdir(parents=True, exist_ok=True)
        key = "".join(ch for ch in str(cluster_id) if ch.isalnum()) or "cluster"
        (out_dir / f"{key}_g3.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:                      # noqa: BLE001
        print(f"[g3] 报告落盘失败（吞·advisory）: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="G3-ENUMKAPPA 枚举维标注一致性前置闸（advisory·exit0）")
    ap.add_argument("project_root")
    ap.add_argument("--cluster", required=True, help="cluster_id（如 cluster_001）")
    ap.add_argument("--n", type=int, default=DEFAULT_N_SAMPLES, help="独立标注次数（默认 3）")
    ap.add_argument("--mode-threshold", type=float, default=DEFAULT_MODE_THRESHOLD)
    ap.add_argument("--kappa-threshold", type=float, default=DEFAULT_KAPPA_THRESHOLD)
    args = ap.parse_args()
    res = run_enum_kappa_gate(
        Path(args.project_root), args.cluster,
        n_samples=args.n, mode_threshold=args.mode_threshold,
        kappa_threshold=args.kappa_threshold)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0)                                  # 恒 0·advisory·绝不阻断


if __name__ == "__main__":
    main()
