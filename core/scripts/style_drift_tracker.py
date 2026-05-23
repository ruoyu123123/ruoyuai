#!/usr/bin/env python3
"""
style_drift_tracker.py — 跨章节风格漂移追踪器

用法：
  python style_drift_tracker.py <chapters目录> [--baseline <风格JSON>] [--output report.json]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))
from style_analyzer import analyze_text  # noqa: E402

# 阈值
DRIFT_THR = 0.20        # >20% → DRIFT
SEVERE_THR = 0.50       # >50% → SEVERE_DRIFT
JUMP_THR = 0.30         # 相邻两章变化 >30% → JUMP
TREND_WIN = 3           # 连续 N 章同维度 DRIFT → TREND

# analyze_text 输出 → 追踪维度 的取值路径
DIM_EXTRACTORS: dict[str, tuple] = {
    "dialogue_ratio":     ("dialogue_ratio",),
    "sentence_mean":      ("sentence_stats", "mean"),
    "sentence_std":       ("sentence_stats", "std"),
    "paragraph_mean":     ("para_sentence_stats", "mean"),
    "comma_period_ratio": ("punctuation_density_per_1000", "comma_period_ratio"),
    "ultra_short_para":   ("ultra_short_para_ratio",),
    "banned_word_count":  ("banned_word_hits",),
    "quota_word_count":   ("quota_word_hits",),
}

# baseline quantitative → 追踪维度 的映射（None = 无对应或特殊处理）
BASELINE_MAP: dict[str, tuple | None] = {
    "dialogue_ratio":     ("dialogue_ratio", "overall"),
    "sentence_mean":      ("sentence_length", "mean"),
    "sentence_std":       ("sentence_length", "std"),
    "paragraph_mean":     ("paragraph_length", "mean_sentences"),
    "comma_period_ratio": ("punctuation_density_per_1000", "comma_period_ratio"),
    "ultra_short_para":   None,
    "banned_word_count":  None,   # 目标固定为 0
    "quota_word_count":   None,
}

DIM_LABELS: dict[str, str] = {
    "dialogue_ratio": "对话占比", "sentence_mean": "句长均值",
    "sentence_std": "句长标准差", "paragraph_mean": "段落均长",
    "comma_period_ratio": "逗句比", "ultra_short_para": "极短段占比",
    "banned_word_count": "禁用词", "quota_word_count": "配额词",
}


def _deep_get(d: dict, keys: tuple):
    """沿键路径取值；末端若为 dict 则返回其 values 之和。"""
    val = d
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
    return sum(val.values()) if isinstance(val, dict) else val


def extract_dims(profile: dict) -> dict[str, float]:
    out = {}
    for dim, path in DIM_EXTRACTORS.items():
        v = _deep_get(profile, path)
        out[dim] = float(v) if v is not None else 0.0
    return out


def load_baseline_targets(path: Path) -> dict[str, float]:
    quant = json.loads(path.read_text(encoding="utf-8")).get("quantitative", {})
    targets: dict[str, float] = {}
    for dim, mapping in BASELINE_MAP.items():
        if mapping is None:
            if dim == "banned_word_count":
                targets[dim] = 0.0
            continue
        val = quant
        for k in mapping:
            val = val.get(k) if isinstance(val, dict) else None
            if val is None:
                break
        if val is not None:
            targets[dim] = float(val)
    return targets


def pct_dev(actual: float, target: float) -> float:
    """百分比偏差。target=0 时: 每增 1.0 视为 50% 偏差。"""
    if target == 0.0:
        return actual * 0.5 if actual >= 0 else 0.0
    return abs(actual - target) / max(abs(target), 0.001)


def _mean(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def _std(v: list[float]) -> float:
    if len(v) < 2:
        return 0.0
    m = _mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / len(v))


def _fmt(dim: str, val: float) -> str:
    if dim in ("dialogue_ratio", "ultra_short_para"):
        return f"{val * 100:.0f}%"
    return f"{val:.1f}"


def _bar(ratio: float, w: int = 10) -> str:
    f = max(0, min(w, round(ratio * w)))
    return "█" * f + "░" * (w - f)


# ── 核心分析 ──────────────────────────────────────────────

def collect_chapters(d: Path) -> list[tuple[str, Path]]:
    return [(f.stem, f) for f in sorted(d.glob("*.txt"))]


def analyze_all(chapters: list[tuple[str, Path]]) -> list[tuple[str, dict]]:
    results = []
    for label, path in chapters:
        text = path.read_text(encoding="utf-8")
        profile = analyze_text(text)
        results.append((label, extract_dims(profile)))
        print(f"  已分析: {label} ({profile['total_chinese_chars']} 字)", file=sys.stderr)
    return results


def build_timeline(data: list[tuple[str, dict]]) -> dict[str, list[float]]:
    tl: dict[str, list[float]] = {d: [] for d in DIM_EXTRACTORS}
    for _, dims in data:
        for d in DIM_EXTRACTORS:
            tl[d].append(round(dims.get(d, 0.0), 4))
    return tl


def detect_baseline_drift(
    data: list[tuple[str, dict]], targets: dict[str, float],
) -> tuple[dict, list[dict]]:
    comp: dict[str, dict] = {}
    alerts: list[dict] = []
    labels = [lb for lb, _ in data]

    for dim, target in targets.items():
        vals = [dims.get(dim, 0.0) for _, dims in data]
        ma = _mean(vals)
        dev = pct_dev(ma, target)
        level = ("SEVERE_DRIFT" if dev > SEVERE_THR
                 else "DRIFT" if dev > DRIFT_THR else "OK")

        comp[dim] = {"target": target, "mean_actual": round(ma, 4),
                     "deviation_pct": round(dev * 100, 1), "drift_level": level}

        dl = DIM_LABELS.get(dim, dim)
        if level != "OK":
            prefix = "严重偏离" if level == "SEVERE_DRIFT" else "偏离"
            alerts.append({"type": level, "dim": dim, "chapters": "all",
                           "msg": f"{dl}{prefix}: {_fmt(dim, ma)} vs 目标 {_fmt(dim, target)}"})

        # TREND 检测
        per_ch = [pct_dev(v, target) > DRIFT_THR for v in vals]
        streak = streak_start = 0
        for i, drifted in enumerate(per_ch):
            if drifted:
                if streak == 0:
                    streak_start = i
                streak += 1
                if streak >= TREND_WIN:
                    alerts.append({
                        "type": "TREND", "dim": dim,
                        "chapters": f"{labels[streak_start]}-{labels[i]}",
                        "msg": f"{dl}持续漂移: {_fmt(dim, vals[streak_start])}→{_fmt(dim, vals[i])}",
                    })
                    streak = 0
            else:
                streak = 0
    return comp, alerts


def detect_jumps(data: list[tuple[str, dict]]) -> list[dict]:
    alerts: list[dict] = []
    for i in range(1, len(data)):
        pl, pd = data[i - 1]
        cl, cd = data[i]
        for dim in DIM_EXTRACTORS:
            pv, cv = pd.get(dim, 0.0), cd.get(dim, 0.0)
            base = max(abs(pv), abs(cv), 0.001)
            if abs(cv - pv) / base > JUMP_THR:
                dl = DIM_LABELS.get(dim, dim)
                alerts.append({"type": "JUMP", "dim": dim,
                               "from": pl, "to": cl,
                               "msg": f"{dl}骤变: {_fmt(dim, pv)}→{_fmt(dim, cv)}"})
    return alerts


def compute_summary(
    timeline: dict[str, list[float]], targets: dict[str, float] | None,
) -> dict:
    dim_cv = {}
    for dim, vals in timeline.items():
        m = _mean(vals)
        dim_cv[dim] = _std(vals) / m if m != 0 else 0.0

    ranked = sorted(dim_cv.items(), key=lambda x: x[1])
    most_stable = [d for d, _ in ranked[:3]]
    most_drifted = [d for d, _ in ranked[-3:]][::-1]

    # 章间一致性分 (CV→score)
    scores = [max(0.0, min(100.0, (0.5 - cv) / 0.4 * 100)) for cv in dim_cv.values()]
    cons = _mean(scores)

    # baseline 符合度
    if targets:
        bs_scores = []
        for dim, tgt in targets.items():
            vals = timeline.get(dim, [])
            if not vals:
                continue
            dev = pct_dev(_mean(vals), tgt)
            bs_scores.append(max(0.0, min(100.0, (1.0 - dev) * 100)))
        if bs_scores:
            cons = cons * 0.4 + _mean(bs_scores) * 0.6

    return {"most_stable_dims": most_stable,
            "most_drifted_dims": most_drifted,
            "overall_consistency_score": round(cons, 1)}


# ── 终端可视化 ────────────────────────────────────────────

def print_report(n: int, tl: dict, comp: dict | None,
                 alerts: list[dict], summary: dict) -> None:
    print(f"\n风格漂移追踪 · {n} 章", file=sys.stderr)
    print("═" * 36, file=sys.stderr)

    for dim in DIM_EXTRACTORS:
        lb = DIM_LABELS.get(dim, dim)
        vals = tl.get(dim, [])
        mv = _mean(vals)
        if comp and dim in comp:
            info = comp[dim]
            tgt = info["target"]
            ratio = min(mv / tgt, 1.0) if tgt > 0 else (1.0 if mv == 0 else 0.0)
            tag = f"[{info['drift_level']}]" if info["drift_level"] != "OK" else "[OK]"
            print(f"  {lb:<8} {_bar(ratio)} {_fmt(dim, mv):>6} {tag:<16} "
                  f"目标 {_fmt(dim, tgt)}", file=sys.stderr)
        else:
            cv = _std(vals) / mv if mv != 0 else 0
            print(f"  {lb:<8} {_bar(max(0, min(1, 1 - cv)))} {_fmt(dim, mv):>6} "
                  f"CV={cv:.2f}", file=sys.stderr)

    print(f"\n  一致性总分: {summary['overall_consistency_score']}/100", file=sys.stderr)
    counts = {}
    for a in alerts:
        counts[a["type"]] = counts.get(a["type"], 0) + 1
    parts = [f"{v} {k}" for k, v in counts.items() if v]
    print(f"  告警: {', '.join(parts) if parts else '无'}\n", file=sys.stderr)


# ── 主入口 ────────────────────────────────────────────────

def run(chapters_dir: Path, baseline_path: Path | None = None,
        output_path: Path | None = None) -> dict:
    chapters = collect_chapters(chapters_dir)
    if not chapters:
        print(f"错误: {chapters_dir} 下未找到 .txt 文件", file=sys.stderr)
        sys.exit(1)

    print(f"找到 {len(chapters)} 个章节文件，开始分析...", file=sys.stderr)
    ch_data = analyze_all(chapters)
    timeline = build_timeline(ch_data)
    alerts: list[dict] = []
    comp = None
    targets = None

    if baseline_path:
        targets = load_baseline_targets(baseline_path)
        comp, ba = detect_baseline_drift(ch_data, targets)
        alerts.extend(ba)

    alerts.extend(detect_jumps(ch_data))
    summary = compute_summary(timeline, targets)

    report: dict = {
        "chapter_count": len(chapters),
        "chapter_labels": [lb for lb, _ in ch_data],
        "timeline": timeline,
    }
    if comp is not None:
        report["baseline_comparison"] = comp
    report["alerts"] = alerts
    report["summary"] = summary

    print_report(len(chapters), timeline, comp, alerts, summary)

    js = json.dumps(report, ensure_ascii=False, indent=2)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(js, encoding="utf-8")
        print(f"报告已保存: {output_path}", file=sys.stderr)
    else:
        print(js)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="跨章节风格漂移追踪器")
    ap.add_argument("chapters_dir", type=Path, help="章节 .txt 文件目录")
    ap.add_argument("--baseline", type=Path, default=None, help="风格基线 JSON")
    ap.add_argument("--output", type=Path, default=None, help="输出报告 JSON 路径")
    args = ap.parse_args()

    if not args.chapters_dir.is_dir():
        print(f"错误: {args.chapters_dir} 不是有效目录", file=sys.stderr)
        sys.exit(1)
    if args.baseline and not args.baseline.is_file():
        print(f"错误: baseline 不存在: {args.baseline}", file=sys.stderr)
        sys.exit(1)

    run(args.chapters_dir, args.baseline, args.output)


if __name__ == "__main__":
    main()
