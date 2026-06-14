#!/usr/bin/env python3
"""judge_consistency_probe.py — B-3 多 judge 一致性离线筛子（GATEKEEPER 域·experiment）。

[语义收窄·必读·防 C2 复述陷阱]
本脚本是「**稳定性下限筛子**」**不是**「可靠性/准确性度量」。多 judge 对同一稿同一维
评分高度一致，**只证『同源稳定』不证『准确』**——judge 若都学会复述注入的 rationale
也会高一致（C2 复述陷阱）。故：
  · rho<RHO_FLOOR 或 alpha<ALPHA_FLOOR 的维 → 标 `reference_only=True`（该维**不稳**·
    不能用于 active 升档·只作 reference 参考）。这是「不稳→踢出 active」的**单向降权**。
  · 但 rho/alpha 高（reference_only=False）**只是必要非充分条件**：该维要真正升 active，
    仍需 `replication_fidelity_check` 跨栈 + 金标准抽样验证。高一致 ≠ 可升 active。

北极星⑤⑥：本脚本是 advisory/experiment 层——
  · 默认 shadow·**绝不进 audit_hub.HARD_GATE_CODES**·**绝不阻断 distill / 写作主流程**·
    **永不抛异常**（全 advisory·exit0）。
  · `reference_only` 标记是**降权**不是 hard_gate：下游升档决策脚本读它决定该维能否进
    active，本脚本自己不裁决任何 hard 结果。

为什么独立离线脚本（不塞 judge_runner 热路径·核验依据）：
  · `judge_runner.run_judge`（AGENT_SPECS L79·8 judge）是 per-cluster **实时**调 gen-model 的
    热路径（中转站 520 限速·~13rpm），对同稿跑 ≥2 judge 算 rho/alpha 会显著加预算+延迟。
  · 故本脚本**离线读已落盘的 judge report / ledger** 的离散/序数评分复算，judge_runner 零改动。

中转站约束（已知教训）：
  · gen-model 弱·中转站**无稳定 logprobs** → 只用 judge **离散/序数评分**算 rho/alpha，
    **不依赖 logprob**（手写 rank 相关·零 scipy 依赖·与测试 1551 zero-dep 基线对齐）。

挂载（独立·CLI）：
  python judge_consistency_probe.py <project_root> --cluster cluster_001 \
      --judge-reports <dir>            # 离线读 dir 下各 judge 的评分（含 dim 向量）
  退出码恒 0（advisory·shadow）。报告落 `_数据库/.judge_consistency/cluster_<key>.json`。
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# ───────────────────────── 模块常量（R2 给定·写成常量便于校准·单测 import 校验） ─────────
# rho<RHO_FLOOR → 该维 judge 间排序不稳，不能升 active（只作 reference）。
RHO_FLOOR = 0.75
# 序数 alpha<ALPHA_FLOOR → 该维 N raters 序数一致性不足（Krippendorff substantial 业界线）。
ALPHA_FLOOR = 0.67
# ≥MIN_JUDGES_FOR_ALPHA 个 judge 才能算序数 alpha（<3 时 alpha=None·只看 rho）。
MIN_JUDGES_FOR_ALPHA = 3
# 算 rho/alpha 每维至少需要的有效评分点数（<2 无法谈相关/一致）。
MIN_POINTS = 2


# ═════════════════════════ 纯算法层：手写 Spearman（零 scipy 依赖） ═════════════════════════
def _average_ranks(vals: list) -> list:
    """对 vals 求**平均秩**（1-based·平票取均秩 → 正确处理 ties）。

    用平均秩而非 `1-6Σd²/n(n²-1)` 速算公式：后者在有 ties 时不成立，平均秩 + Pearson
    是 Spearman 在有结时的标准定义（与 scipy.stats.spearmanr 一致）。
    """
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        # 找出与 order[i] 等值的一段连续区间 [i, j]
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0          # 1-based 平均秩
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: list, y: list):
    """Pearson 相关系数（x/y 等长）。任一向量零方差（全等值）→ 返 None（无法定义相关）。"""
    n = len(x)
    if n == 0:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    if dx <= 0.0 or dy <= 0.0:                   # 至少一方常数序列 → 相关无定义
        return None
    return num / ((dx * dy) ** 0.5)


def spearman_rho(a: list, b: list):
    """两序列 Spearman 秩相关 = 各自平均秩的 Pearson 相关。

    完全同序 → 1.0；完全反序 → -1.0；任一向量常数 → None（不可定义·上游剔除该对）。
    长度不齐时由调用方先截到公共长度（这里要求等长）。
    """
    a = list(a)
    b = list(b)
    if len(a) != len(b) or len(a) < MIN_POINTS:
        return None
    return _pearson(_average_ranks(a), _average_ranks(b))


def pairwise_spearman_mean(scores_by_judge: dict):
    """judge 两两 Spearman rho 的均值（跨 judge·非同 judge 自一致）。

    scores_by_judge: {judge_name: [该维各点评分]}。长度不齐的两 judge 截到公共最短长度。
    返回所有合法 pair 的 rho 均值；无任何合法 pair（<2 judge / 全常数 / 点数不足）→ None。

    语义：完全一致 → 1.0；完全反序 → 负值；判据见 classify_dim。
    """
    if not isinstance(scores_by_judge, dict):
        return None
    # 只取有 ≥MIN_POINTS 个数值评分的 judge
    usable = {
        j: [float(v) for v in vec]
        for j, vec in scores_by_judge.items()
        if isinstance(vec, (list, tuple)) and len(vec) >= MIN_POINTS
    }
    names = list(usable.keys())
    rhos: list[float] = []
    for ja, jb in combinations(names, 2):
        n = min(len(usable[ja]), len(usable[jb]))
        if n < MIN_POINTS:
            continue
        r = spearman_rho(usable[ja][:n], usable[jb][:n])
        if r is not None:
            rhos.append(r)
    if not rhos:
        return None
    return sum(rhos) / len(rhos)


# ═════════════════════ 纯算法层：序数 Krippendorff alpha（零依赖·≥3 judge） ═════════════════
def ordinal_krippendorff_alpha(scores_by_judge: dict):
    """序数 Krippendorff alpha（N raters·序数度量·零依赖手写）。

    输入同 pairwise_spearman_mean：{judge: [各点评分]}。把**每个评分点位置当一个 unit**
    （列），judge 当 rater（行）；按公共最短长度对齐。
    序数距离度量 δ_ordinal(c,k)²（Krippendorff 标准）：
        δ² = ( Σ_{g=c..k} n_g − (n_c + n_k)/2 )²
    其中 n_g 是值 g 在全部可配对值中的频次。
    α = 1 − Do/De：Do=观测不一致(unit 内成对距离·权重 1/(m−1))，De=期望不一致(全局成对距离)。

    返回：
      · <MIN_JUDGES_FOR_ALPHA 个 judge / 点数 <2 → None（不足以算序数 alpha·上游只看 rho）。
      · De==0（全数据同一值·无变异）→ 1.0（完全一致）。
      · 否则标准 α（完全一致 1.0；随机 ≈0；系统反向可负）。
    """
    if not isinstance(scores_by_judge, dict):
        return None
    judges = [j for j, vec in scores_by_judge.items()
              if isinstance(vec, (list, tuple)) and vec]
    if len(judges) < MIN_JUDGES_FOR_ALPHA:
        return None
    n_units = min(len(scores_by_judge[j]) for j in judges)
    if n_units < MIN_POINTS:
        return None

    # 每 unit（点位置）一列：来自各 judge 的评分值
    units: list[list[float]] = []
    for u in range(n_units):
        col = [float(scores_by_judge[j][u]) for j in judges]
        units.append(col)

    all_vals = [v for col in units for v in col]
    from collections import Counter
    freq = Counter(all_vals)
    distinct = sorted(freq)
    idx = {v: i for i, v in enumerate(distinct)}
    # 累计频次 cum[v] = Σ_{g<=v} n_g（用于序数距离的区间和）
    cum: dict = {}
    running = 0
    for v in distinct:
        running += freq[v]
        cum[v] = running

    def _ord_dist2(c, k) -> float:
        if c == k:
            return 0.0
        lo, hi = (c, k) if idx[c] < idx[k] else (k, c)
        # Σ_{g=lo..hi} n_g  =  cum[hi] − cum[lo] + freq[lo]
        seg = cum[hi] - cum[lo] + freq[lo]
        return (seg - (freq[lo] + freq[hi]) / 2.0) ** 2

    # 观测不一致 Do
    do_num = 0.0
    n_total = 0
    for col in units:
        m = len(col)
        if m < 2:
            continue
        n_total += m
        s = 0.0
        for a in range(m):
            for b in range(m):
                if a != b:
                    s += _ord_dist2(col[a], col[b])
        do_num += s / (m - 1)
    if n_total == 0:
        return None
    do = do_num / n_total

    # 期望不一致 De（全局成对·N(N-1) 归一）
    big_n = len(all_vals)
    if big_n < 2:
        return None
    de_num = 0.0
    for c in distinct:
        for k in distinct:
            if c != k:
                de_num += freq[c] * freq[k] * _ord_dist2(c, k)
    de = de_num / (big_n * (big_n - 1))
    if de <= 0.0:                                # 全数据同一值 → 无变异 → 完全一致
        return 1.0
    return 1.0 - do / de


# ═════════════════════════ 判定层 ═════════════════════════
def classify_dim(dim_name: str, scores_by_judge: dict, *,
                 rho_floor: float = RHO_FLOOR,
                 alpha_floor: float = ALPHA_FLOOR) -> dict:
    """对单维做稳定性筛子判定 → 是否 reference_only。

    [语义·收窄·防 C2 复述陷阱] 本判定是「**稳定性下限筛子**」。
      · rho<rho_floor **或** alpha<alpha_floor → `reference_only=True`（该维 judge 间不稳·
        **不能升 active**·只作 reference）。
      · 高一致（reference_only=False）**只证同源稳定不证准确**——该维要真升 active 仍需
        replication_fidelity_check 跨栈 + 金标准抽样（高一致是必要非充分条件）。

    n_judges<3 时无 alpha，仅以 rho 判（alpha=None 不触发 alpha 那一支）。
    rho 也无法算（<2 judge / 全常数）→ 数据不足·保守标 reference_only=True。

    返回 {dim, rho, alpha, n_judges, reference_only, verdict}。
    """
    n_judges = sum(
        1 for vec in (scores_by_judge or {}).values()
        if isinstance(vec, (list, tuple)) and len(vec) >= MIN_POINTS
    )
    rho = pairwise_spearman_mean(scores_by_judge)
    alpha = ordinal_krippendorff_alpha(scores_by_judge)

    # 触发 reference_only 的任一条件
    rho_unstable = (rho is not None) and (rho < rho_floor)
    alpha_unstable = (alpha is not None) and (alpha < alpha_floor)
    insufficient = rho is None                   # 连 rho 都算不出 = 数据不足

    reference_only = bool(rho_unstable or alpha_unstable or insufficient)

    # verdict 文本：稳/不稳 + 永远带「稳定≠准确·仍需金标准升 active」收窄语义
    rho_s = "n/a" if rho is None else f"{rho:.4f}"
    alpha_s = "n/a" if alpha is None else f"{alpha:.4f}"
    if insufficient:
        head = (f"数据不足（合法 judge<2 或评分全常数）→ reference_only=True·"
                f"该维本批不可升 active")
    elif reference_only:
        bits = []
        if rho_unstable:
            bits.append(f"rho {rho_s} < {rho_floor}")
        if alpha_unstable:
            bits.append(f"alpha {alpha_s} < {alpha_floor}")
        head = (f"judge 间不稳（{' 且 '.join(bits)}）→ reference_only=True·"
                f"该维不能升 active·只作 reference")
    else:
        head = (f"judge 间稳定（rho {rho_s} ≥ {rho_floor}"
                + (f"·alpha {alpha_s} ≥ {alpha_floor}" if alpha is not None else "·alpha 不适用<3judge")
                + ")·非 reference_only")
    verdict = (head
               + "。[收窄] 稳定≠准确——同源高一致只证稳定不证准（C2 复述陷阱），"
                 "仍需 replication_fidelity_check 跨栈 + 金标准抽样才升 active。")

    return {
        "dim": dim_name,
        "rho": (None if rho is None else round(rho, 4)),
        "alpha": (None if alpha is None else round(alpha, 4)),
        "n_judges": n_judges,
        "reference_only": reference_only,
        "verdict": verdict,
    }


# ═════════════════════════ 离线数据装载（读已落盘 judge report·零 gen-model 调用） ═════════════
def _coerce_score_vector(val):
    """把单 judge 单维的评分挤成 float 向量（容错·非法元素剔除·非 list 返 None）。"""
    if not isinstance(val, (list, tuple)):
        return None
    out: list[float] = []
    for x in val:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            continue
    return out if len(out) >= MIN_POINTS else None


def load_scores_from_reports(reports_dir, dims=None) -> dict:
    """离线扫 reports_dir 下各 judge JSON → {dim: {judge_name: [scores]}}。

    约定（宽松容错·适配多种已落盘形态·任何异常吞成空·北极星⑤）：
      · 每个 *.json 文件 = 一个 judge 的 report；judge_name 取文件内 `judge`/`agent` 字段，
        缺则用文件名 stem。
      · report 内评分向量挂在 `dim_scores`（{dim: [..]}）或 `dimensions`（{dim: {scores:[..]}}
        或 {dim: [..]}）。
      · dims 不为空 → 只收这些维；为空 → 收全部出现过的维。
    返回可直接喂 classify_dim 的嵌套 dict。
    """
    by_dim: dict[str, dict[str, list]] = {}
    rd = Path(reports_dir)
    if not rd.is_dir():
        return by_dim
    want = set(dims) if dims else None
    for fp in sorted(rd.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:                        # noqa: BLE001 — 坏文件跳过·不抛
            continue
        if not isinstance(data, dict):
            continue
        judge = str(data.get("judge") or data.get("agent") or fp.stem)
        # 形态1：dim_scores: {dim: [..]}
        dim_scores = data.get("dim_scores")
        # 形态2：dimensions: {dim: [..] 或 {scores:[..]}}
        dimensions = data.get("dimensions")
        merged: dict[str, list] = {}
        if isinstance(dim_scores, dict):
            for d, v in dim_scores.items():
                vec = _coerce_score_vector(v)
                if vec:
                    merged[str(d)] = vec
        if isinstance(dimensions, dict):
            for d, v in dimensions.items():
                vec = _coerce_score_vector(
                    v.get("scores") if isinstance(v, dict) else v)
                if vec:
                    merged.setdefault(str(d), vec)
        for d, vec in merged.items():
            if want is not None and d not in want:
                continue
            by_dim.setdefault(d, {})[judge] = vec
    return by_dim


# ═════════════════════════ 主入口 ═════════════════════════
def run_consistency_probe(project_root, cluster_id, *,
                          reports_dir=None,
                          scores_by_dim=None,
                          dims=None,
                          rho_floor: float = RHO_FLOOR,
                          alpha_floor: float = ALPHA_FLOOR) -> dict:
    """多 judge 一致性离线筛子主入口（advisory·exit0·永不抛）。

    数据来源二选一：
      · scores_by_dim: {dim: {judge: [scores]}}（测试/上游直传·优先）。
      · reports_dir: 离线读该目录下各 judge report（零 gen-model 调用）。
    对每维跑 classify_dim → 汇总 reference_only 维列表 → 落 advisory 报告。

    返回 {cluster_id, dims: [classify_dim 结果...], reference_only_dims, stable_dims,
          rho_floor, alpha_floor}。报告落 `_数据库/.judge_consistency/cluster_<key>.json`。
    """
    result = {
        "cluster_id": cluster_id,
        "rho_floor": rho_floor,
        "alpha_floor": alpha_floor,
        "dims": [],
        "reference_only_dims": [],
        "stable_dims": [],
        "semantics": ("稳定性下限筛子·非可靠性度量：reference_only=True 仅证『不稳→不可升 active』；"
                      "reference_only=False 仅证『同源稳定』不证准，升 active 仍需金标准（防 C2 复述陷阱）。"),
    }
    try:
        if scores_by_dim is None:
            scores_by_dim = load_scores_from_reports(reports_dir, dims=dims) if reports_dir else {}
        if not isinstance(scores_by_dim, dict):
            scores_by_dim = {}
        target_dims = list(dims) if dims else list(scores_by_dim.keys())
        for d in target_dims:
            res = classify_dim(d, scores_by_dim.get(d, {}),
                               rho_floor=rho_floor, alpha_floor=alpha_floor)
            result["dims"].append(res)
            if res["reference_only"]:
                result["reference_only_dims"].append(d)
            else:
                result["stable_dims"].append(d)
    except Exception as e:                        # noqa: BLE001 — 筛子永不抛（advisory·exit0）
        result["error"] = f"{type(e).__name__}: {e}"

    _write_report(project_root, cluster_id, result)
    return result


def _write_report(project_root, cluster_id, result: dict):
    """落 advisory 报告 _数据库/.judge_consistency/cluster_<key>.json（落盘失败也不抛）。"""
    try:
        out_dir = Path(project_root) / "_数据库" / ".judge_consistency"
        out_dir.mkdir(parents=True, exist_ok=True)
        key = "".join(ch for ch in str(cluster_id) if ch.isalnum()) or "cluster"
        (out_dir / f"cluster_{key}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:                        # noqa: BLE001
        print(f"[jcp] 报告落盘失败（吞·advisory）: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="B-3 多 judge 一致性离线筛子（advisory·shadow·exit0·永不阻断）")
    ap.add_argument("project_root")
    ap.add_argument("--cluster", required=True, help="cluster_id（如 cluster_001）")
    ap.add_argument("--judge-reports", default=None,
                    help="离线 judge report 目录（读已落盘评分·零 gen-model 调用）")
    ap.add_argument("--dim", action="append", default=None,
                    help="只评指定维（可重复）；缺省评 report 内全部维")
    ap.add_argument("--rho-floor", type=float, default=RHO_FLOOR)
    ap.add_argument("--alpha-floor", type=float, default=ALPHA_FLOOR)
    args = ap.parse_args()
    res = run_consistency_probe(
        Path(args.project_root), args.cluster,
        reports_dir=args.judge_reports, dims=args.dim,
        rho_floor=args.rho_floor, alpha_floor=args.alpha_floor)
    # stdout 打印必须**永不翻转 exit 码**：Windows GBK 控制台遇非 GBK 字符（如 CJK 标点）
    # 直接 print 会抛 UnicodeEncodeError。报告已 UTF-8 落盘，stdout 仅供人看 → 容错降级。
    payload = json.dumps(res, ensure_ascii=False, indent=2)
    try:
        sys.stdout.write(payload + "\n")
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        sys.stdout.buffer.write((payload + "\n").encode(enc, errors="replace"))
    sys.exit(0)                                   # 恒 0·advisory·shadow·绝不阻断


if __name__ == "__main__":
    main()
