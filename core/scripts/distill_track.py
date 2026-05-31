#!/usr/bin/env python3
"""distill_track.py — 蒸馏流程追踪 · 元层防反复打转（2026-05-31 · 北极星①⑤⑥）

治什么病
--------
cluster 级蒸馏反复迭代时（v0→v8…），单趟 SFS 分受 LLM 非确定性噪声污染，
分不清「skill 真涨」还是「只是这趟运气好」。叠加 arXiv 2601.22025 实证教训
（"更好的 prompt 实测反而掉分"）+ 本项目 DCAS 方向来回反复 3 次翻车模式，
迭代很容易在噪声里空转打转。

本脚本是**元层**（meta-layer）追踪器，不参与生成 / 不参与评分 / 不做 judge：
  · 把每次复刻测试的 {skill_version, cluster_ref, sfs 多趟均值/方差, model,
    timestamp, git_sha, runs} append 进 `复刻测试/_baseline_ledger.json`；
  · 与「同一 cluster_ref 的上一条基线」比对，掉幅超 tolerance band → 打印 advisory；
  · dashboard 读 ledger 打文本表（版本 × 趋势 × 回归标记）。

关键纪律（与任务书一致）
----------------------
  1. **零模型推理 · 零网络 · 零新依赖**：纯 stdlib + JSON 文件读写（atomic_json 复用现有）。
  2. **advisory only**：掉分只 print 警示，**绝不 hard_gate / 绝不 exit 非 0 阻断**（北极星⑤·不干涉模型）。
  3. **多趟均值/方差**：论文强调非确定性必须多趟——单趟 SFS 当基线 = 噪声误报源。
     record 接受 `--sfs A --sfs B ...` 多趟分，存均值 + 样本方差 + runs 计数。
  4. **tolerance band**：只有「掉幅 > band」才报回归。band 默认取
     max(abs_floor, k×pooled_std) —— 噪声越大容忍越宽（避免高方差作者单趟误报）。
  5. **独立**：不挂 distill_replicate（避与其他在改的件冲突）· 借力已有 复刻测试/ 目录 + 独立 git。

用法
----
  # 记录一次复刻测试（3 趟分 · 自动取 git_sha · 自动比上一基线）
  python core/scripts/distill_track.py record \\
    --project workspace/styles/蛊真人 \\
    --skill-version v8 --cluster-ref cluster_001 --model deepseek-v4-pro \\
    --sfs 71.2 --sfs 68.9 --sfs 73.5

  # 也可从 style_evaluator 报告 JSON 直接读 sfs_quick（可多个 → 当多趟）
  python core/scripts/distill_track.py record --project ... --skill-version v8 \\
    --cluster-ref cluster_001 --from-report r1.json --from-report r2.json

  # 看趋势表
  python core/scripts/distill_track.py dashboard --project workspace/styles/蛊真人
  python core/scripts/distill_track.py dashboard --project ... --cluster-ref cluster_001
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# 复用现有原子写（同目录 import · 与 distill_replicate 一致的注入手法）
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import atomic_json  # noqa: E402
    _HAVE_ATOMIC = True
except Exception:  # pragma: no cover - 退化路径
    _HAVE_ATOMIC = False

# ---- 容差带常量（保守默认 · 高方差作者自动放宽，靠 pooled_std）----
DEFAULT_ABS_FLOOR = 2.0    # SFS 百分制下 < 2 分的掉幅视作噪声，绝不报
DEFAULT_STD_K = 1.5        # band = max(abs_floor, K × pooled_std)
LEDGER_NAME = "_baseline_ledger.json"
LEDGER_SUBDIR = "复刻测试"
SCHEMA_VERSION = 1


# ============================================================
# 纯函数层（全部可单测 · 零 IO / 零模型 / 零网络）
# ============================================================

def mean(xs):
    """算数均值。空列表返回 0.0（调用方负责非空校验）。"""
    xs = [float(x) for x in xs]
    return sum(xs) / len(xs) if xs else 0.0


def sample_variance(xs):
    """样本方差（无偏 · n-1）。少于 2 趟无法估方差 → 返回 0.0。

    论文强调非确定性必须多趟：runs<2 时方差未知，下游 tolerance band
    会退化到 abs_floor（保守不误报）。
    """
    xs = [float(x) for x in xs]
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def sample_std(xs):
    """样本标准差。"""
    return sample_variance(xs) ** 0.5


def pooled_std(var_a, n_a, var_b, n_b):
    """两组样本的合并标准差（pooled）。

    用「上一基线」与「本次」两组的方差合并估噪声尺度——任一组样本不足时
    退化为另一组，两组都不足 → 0.0（band 退到 abs_floor）。
    """
    n_a = int(n_a or 0)
    n_b = int(n_b or 0)
    var_a = float(var_a or 0.0)
    var_b = float(var_b or 0.0)
    df = (n_a - 1 if n_a > 1 else 0) + (n_b - 1 if n_b > 1 else 0)
    if df <= 0:
        return 0.0
    num = (max(n_a - 1, 0)) * var_a + (max(n_b - 1, 0)) * var_b
    return (num / df) ** 0.5


def tolerance_band(prev_entry, cur_entry, abs_floor=DEFAULT_ABS_FLOOR, std_k=DEFAULT_STD_K):
    """计算回归判定容差带（分数单位 · 越噪越宽）。

    band = max(abs_floor, std_k × pooled_std(prev, cur))
    噪声越大（方差越大）→ band 越宽 → 越不容易误报回归。这是论文「非确定性
    必须多趟、否则单趟噪声误报」的直接落地。
    """
    if not prev_entry or not cur_entry:
        return float(abs_floor)
    ps = pooled_std(
        prev_entry.get("variance", 0.0), prev_entry.get("runs", 0),
        cur_entry.get("variance", 0.0), cur_entry.get("runs", 0),
    )
    return max(float(abs_floor), float(std_k) * ps)


def detect_regression(prev_entry, cur_entry, abs_floor=DEFAULT_ABS_FLOOR, std_k=DEFAULT_STD_K):
    """纯函数：与上一基线比对，判定是否回归（掉分超容差带）。

    返回 dict：
      {
        "regressed": bool,        # 掉幅 > band 才 True
        "delta": float,           # cur_mean - prev_mean（负=掉分）
        "band": float,            # 本次容差带
        "prev_mean": float, "cur_mean": float,
        "trend": "up"|"down"|"flat"|"baseline",  # 趋势标签（band 内算 flat）
        "note": str,
      }

    无 prev（首条基线）→ regressed=False, trend="baseline"（无可比对象，绝不误报）。
    """
    cur_mean = float((cur_entry or {}).get("sfs_mean", 0.0))
    if not prev_entry:
        return {
            "regressed": False, "delta": 0.0, "band": float(abs_floor),
            "prev_mean": None, "cur_mean": cur_mean,
            "trend": "baseline", "note": "首条基线·无上一条可比对象",
        }
    prev_mean = float(prev_entry.get("sfs_mean", 0.0))
    delta = cur_mean - prev_mean
    band = tolerance_band(prev_entry, cur_entry, abs_floor=abs_floor, std_k=std_k)
    regressed = delta < -band
    if delta > band:
        trend = "up"
    elif delta < -band:
        trend = "down"
    else:
        trend = "flat"  # 落在容差带内：当作噪声·不算真涨真跌
    if trend == "flat":
        note = f"Δ{delta:+.2f} 落在容差带 ±{band:.2f} 内·视作噪声非真变化（多趟方差兜底）"
    elif regressed:
        note = f"掉分 {delta:+.2f} 超容差带 -{band:.2f}·疑似回归（advisory·不阻断）"
    else:
        note = f"提升 {delta:+.2f} 超容差带 +{band:.2f}·真涨"
    return {
        "regressed": regressed, "delta": round(delta, 3), "band": round(band, 3),
        "prev_mean": round(prev_mean, 3), "cur_mean": round(cur_mean, 3),
        "trend": trend, "note": note,
    }


def build_entry(skill_version, cluster_ref, sfs_scores, model=None,
                git_sha=None, timestamp=None, holdout=None):
    """把多趟 SFS 分聚成一条 ledger 记录（纯函数）。

    sfs_scores: 多趟分 list（论文强调多趟）·单趟也允许但 variance=0、下游 band 退 abs_floor。
    holdout（可选 · 2026-05-31）：distill_holdout 挂进来的留出落差信息
      {tuning_mean, holdout_mean, gap, overfit, verdict}——基线 ledger 多一个 holdout 列。
      None 时不写该字段（不污染纯 tuning 记录的 schema）。
    """
    scores = [float(s) for s in sfs_scores]
    if not scores:
        raise ValueError("sfs_scores 不能为空：至少要 1 趟分")
    entry = {
        "skill_version": str(skill_version),
        "cluster_ref": str(cluster_ref),
        "sfs_scores": [round(s, 3) for s in scores],
        "sfs_mean": round(mean(scores), 3),
        "variance": round(sample_variance(scores), 4),
        "std": round(sample_std(scores), 4),
        "runs": len(scores),
        "model": model or "unknown",
        "git_sha": git_sha or "unknown",
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    if holdout is not None:
        entry["holdout"] = dict(holdout)
    return entry


def last_entry_for_ref(ledger, cluster_ref):
    """从 ledger 取「同一 cluster_ref」最近一条记录（纯函数）。

    不同 cluster_ref 不可比（不同场景/题材），所以基线比对严格按 cluster_ref 分组。
    """
    entries = (ledger or {}).get("entries", [])
    for e in reversed(entries):
        if e.get("cluster_ref") == cluster_ref:
            return e
    return None


def append_entry(ledger, entry):
    """把 entry append 进 ledger（纯函数 · 返回新 ledger · 不原地改输入）。"""
    new = {
        "schema_version": (ledger or {}).get("schema_version", SCHEMA_VERSION),
        "entries": list((ledger or {}).get("entries", [])),
    }
    new["entries"].append(entry)
    return new


def ledger_rows_for_dashboard(ledger, cluster_ref=None,
                              abs_floor=DEFAULT_ABS_FLOOR, std_k=DEFAULT_STD_K):
    """把 ledger 渲染成 dashboard 行（纯函数 · 每条带与「同 ref 上一条」的趋势/回归标记）。

    返回 list[dict]，按 ledger 顺序，每条含 detect_regression 结果。便于 dashboard
    与测试共用同一逻辑（不黑箱：表里的 trend/regress 由可单测的纯函数算）。
    """
    entries = (ledger or {}).get("entries", [])
    rows = []
    seen_prev = {}  # cluster_ref -> 上一条 entry
    for e in entries:
        ref = e.get("cluster_ref")
        if cluster_ref is not None and ref != cluster_ref:
            continue
        prev = seen_prev.get(ref)
        reg = detect_regression(prev, e, abs_floor=abs_floor, std_k=std_k)
        rows.append({"entry": e, "regression": reg})
        seen_prev[ref] = e
    return rows


# ============================================================
# IO 层（读写 ledger · 取 git_sha · 渲染表 · 全 advisory 永不阻断）
# ============================================================

def ledger_path(project) -> Path:
    return Path(project) / LEDGER_SUBDIR / LEDGER_NAME


def load_ledger(project) -> dict:
    p = ledger_path(project)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "entries" not in data:
            return {"schema_version": SCHEMA_VERSION, "entries": []}
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] ledger 解析失败({e})·按空账本处理（不覆盖原文件）", file=sys.stderr)
        return {"schema_version": SCHEMA_VERSION, "entries": []}


def save_ledger(project, ledger):
    p = ledger_path(project)
    if _HAVE_ATOMIC:
        atomic_json.atomic_write_json(p, ledger)
    else:  # 退化：直写（仍 mkdir）
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


def current_git_sha(cwd=None) -> str:
    """取当前短 sha·失败返回 'unknown'（绝不抛·绝不阻断·北极星纪律）。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _scores_from_report(path) -> float | None:
    """从 style_evaluator 报告 JSON 抽 sfs_quick（兜底 programmatic_score.total）。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] 报告读不出 sfs({path}): {e}", file=sys.stderr)
        return None
    if isinstance(d.get("sfs_quick"), (int, float)):
        return float(d["sfs_quick"])
    ps = d.get("programmatic_score") or {}
    if isinstance(ps.get("total"), (int, float)):
        return float(ps["total"])
    print(f"[WARN] 报告无 sfs_quick/programmatic_score.total: {path}", file=sys.stderr)
    return None


def cmd_record(args) -> int:
    project = Path(args.project)
    scores = list(args.sfs or [])
    for rp in (args.from_report or []):
        v = _scores_from_report(Path(rp))
        if v is not None:
            scores.append(v)
    if not scores:
        print("[ERROR] 没有 SFS 分：用 --sfs X [--sfs Y ...] 或 --from-report r.json", file=sys.stderr)
        return 1  # 用法错（非门禁阻断·是参数缺失）
    if len(scores) < 2:
        print("[NOTE] 仅 1 趟分·方差=0·容差带退化到 abs_floor。论文建议 ≥3 趟以可靠估噪声。",
              file=sys.stderr)

    git_sha = args.git_sha or current_git_sha(project)
    entry = build_entry(
        skill_version=args.skill_version, cluster_ref=args.cluster_ref,
        sfs_scores=scores, model=args.model, git_sha=git_sha,
    )

    ledger = load_ledger(project)
    prev = last_entry_for_ref(ledger, args.cluster_ref)
    reg = detect_regression(prev, entry, abs_floor=args.abs_floor, std_k=args.std_k)

    ledger = append_entry(ledger, entry)
    save_ledger(project, ledger)

    print(f"[OK] 记录 {entry['skill_version']} @ {entry['cluster_ref']}: "
          f"SFS 均值 {entry['sfs_mean']} (±{entry['std']}, {entry['runs']} 趟) "
          f"git={entry['git_sha']}")
    print(f"     写入 {ledger_path(project)}")
    # advisory：掉分超带 → 醒目警示·但**不 exit 非 0**（不阻断迭代·不干涉模型创作判断）
    if reg["trend"] == "baseline":
        print(f"     [BASELINE] {reg['note']}")
    elif reg["regressed"]:
        print(f"     [ADVISORY · 疑似回归] 上版 {reg['prev_mean']} → 本版 {reg['cur_mean']} "
              f"({reg['delta']:+.2f}, 容差带 {reg['band']:.2f})")
        print(f"     [ADVISORY] {reg['note']}")
        print("     [ADVISORY] 别急着定方向：先确认是真掉分还是单趟噪声（多跑几趟/换 ref 复核）。"
              "防 DCAS 式来回反复打转。")
    elif reg["trend"] == "up":
        print(f"     [↑ 真涨] 上版 {reg['prev_mean']} → 本版 {reg['cur_mean']} ({reg['delta']:+.2f})")
    else:
        print(f"     [≈ 持平] {reg['note']}")
    return 0  # 永远 0：advisory 层绝不阻断流水线


def _trend_glyph(reg) -> str:
    t = reg.get("trend")
    if t == "baseline":
        return "·基线"
    if t == "up":
        return "↑涨"
    if reg.get("regressed"):
        return "↓回归!"
    if t == "down":
        return "↓跌"
    return "≈持平"


def cmd_dashboard(args) -> int:
    project = Path(args.project)
    ledger = load_ledger(project)
    rows = ledger_rows_for_dashboard(
        ledger, cluster_ref=args.cluster_ref,
        abs_floor=args.abs_floor, std_k=args.std_k,
    )
    if not rows:
        scope = f"（cluster_ref={args.cluster_ref}）" if args.cluster_ref else ""
        print(f"[INFO] ledger 无记录{scope}: {ledger_path(project)}")
        return 0
    title = f"蒸馏基线趋势 · {project.name}"
    if args.cluster_ref:
        title += f" · {args.cluster_ref}"
    print("=" * 78)
    print(title)
    print("=" * 78)
    hdr = (f"{'版本':<8}{'cluster_ref':<16}{'SFS均值':>9}{'±std':>8}{'趟':>4}"
           f"{'Δ前版':>9}{'趋势':>10}  {'git':<10}")
    print(hdr)
    print("-" * 78)
    regress_count = 0
    overfit_count = 0
    for r in rows:
        e, reg = r["entry"], r["regression"]
        delta = "" if reg["prev_mean"] is None else f"{reg['delta']:+.2f}"
        glyph = _trend_glyph(reg)
        if reg.get("regressed"):
            regress_count += 1
        # holdout 列：distill_holdout --track 挂进来的留出落差（过拟合一眼可见）。
        ho = e.get("holdout") or {}
        if ho:
            gap = ho.get("gap")
            ho_col = (f"Δho{gap:+.1f}" if isinstance(gap, (int, float)) else "ho")
            if ho.get("overfit"):
                ho_col += "!过拟合"
                overfit_count += 1
        else:
            ho_col = ""
        print(f"{e.get('skill_version',''):<8}{e.get('cluster_ref',''):<16}"
              f"{e.get('sfs_mean',0):>9.2f}{e.get('std',0):>8.2f}{e.get('runs',0):>4}"
              f"{delta:>9}{glyph:>10}  {e.get('git_sha','')[:10]:<10}{ho_col}")
    print("-" * 78)
    print(f"共 {len(rows)} 条记录·疑似回归 {regress_count} 条·疑似过拟合 {overfit_count} 条"
          f"（容差带 abs_floor={args.abs_floor} K={args.std_k}）")
    if regress_count:
        print("[ADVISORY] 有疑似回归版本·均为 advisory 提示·不阻断（核实是否单趟噪声再定方向）")
    if overfit_count:
        print("[ADVISORY] 有疑似过拟合版本（holdout 掉分多）·均为 advisory·"
              "提醒优化别只盯 tuning SFS（防 metric overfit）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="蒸馏流程追踪·元层防反复打转（advisory only·零模型零依赖）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record", help="记录一次复刻测试到 ledger 并比对上一基线")
    pr.add_argument("--project", required=True, help="风格项目目录 workspace/styles/<书>")
    pr.add_argument("--skill-version", required=True, help="skill 版本号 如 v8")
    pr.add_argument("--cluster-ref", required=True, help="复刻对象 如 cluster_001")
    pr.add_argument("--model", default=None, help="gen-model 名（meta·便于追因）")
    pr.add_argument("--sfs", type=float, action="append", default=[],
                    help="单趟 SFS 分（可多次 → 多趟·论文强调）")
    pr.add_argument("--from-report", action="append", default=[],
                    help="从 style_evaluator 报告 JSON 读 sfs_quick（可多次）")
    pr.add_argument("--git-sha", default=None, help="覆盖 git sha（默认自动取）")
    pr.add_argument("--abs-floor", type=float, default=DEFAULT_ABS_FLOOR)
    pr.add_argument("--std-k", type=float, default=DEFAULT_STD_K)
    pr.set_defaults(func=cmd_record)

    pd = sub.add_parser("dashboard", help="读 ledger 打趋势表（版本×趋势×回归标记）")
    pd.add_argument("--project", required=True)
    pd.add_argument("--cluster-ref", default=None, help="只看某个 cluster_ref")
    pd.add_argument("--abs-floor", type=float, default=DEFAULT_ABS_FLOOR)
    pd.add_argument("--std-k", type=float, default=DEFAULT_STD_K)
    pd.set_defaults(func=cmd_dashboard)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
