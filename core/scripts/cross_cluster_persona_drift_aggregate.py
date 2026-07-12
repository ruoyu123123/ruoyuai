"""汇总各故事块的角色 voice 偏离度并生成连续趋势建议。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # noqa: E402


_D5_GENERIC_BAND = {
    "warn_mean": 0.5,
    "warn_slope": 0.04,
    "_source": "通用",
}


def _build_findings(per_cluster: dict[str, list[dict]]) -> list[dict]:
    """对每个故事块的角色偏离点应用宽松的顾问阈值。"""
    findings = []
    for cluster_id, entries in per_cluster.items():
        for entry in entries:
            character = entry.get("character")
            drift = entry.get("drift")
            if not isinstance(drift, (int, float)) or isinstance(drift, bool):
                continue
            if drift <= 0.5:
                continue
            findings.append({
                "severity": "warning" if drift > 0.75 else "advisory",
                "gate_level": "advisory",
                "code": "PERSONA_DRIFT_DETECTED",
                "cluster_id": cluster_id,
                "character": character,
                "metric": entry.get("metric") or {
                    "character": character,
                    "drift": drift,
                },
                "message": (
                    f"{cluster_id} 角色「{character}」voice drift={drift:.3f}"
                ),
                "suggestion": (
                    "检查偏离是否由角色弧线和当前压力自然造成；有明确因果可豁免"
                ),
            })
    return findings


def _linreg_slope(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator == 0:
        return 0.0
    numerator = sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)
    )
    return numerator / denominator


def compute_d5_author_band(project_root) -> dict:
    """用作者档的人物稳定性与复杂度标定趋势容忍带。"""
    band = dict(_D5_GENERIC_BAND)
    style = None
    root = Path(project_root)
    for path in (root / "_数据库" / "作者风格.json", root / "作者风格.json"):
        try:
            style = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            continue
    fingerprint = (
        style.get("narrative_fingerprint") if isinstance(style, dict) else None
    )
    if not isinstance(fingerprint, dict) or not fingerprint:
        return band

    mean_limit = band["warn_mean"]
    slope_limit = band["warn_slope"]
    used = False
    loops = fingerprint.get("character_behavior_loops")
    if isinstance(loops, dict) and loops:
        loop_total = sum(
            value for value in loops.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        if loop_total >= 8:
            mean_limit -= 0.05
            slope_limit -= 0.005
        used = True
    depth = fingerprint.get("character_depth_grade_distribution")
    if isinstance(depth, dict) and depth:
        total = sum(
            value for value in depth.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        high = sum(
            value for key, value in depth.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and any(label in str(key) for label in ("极高", "高"))
        )
        if total and high / total >= 0.15:
            mean_limit += 0.08
            slope_limit += 0.01
        used = True
    if used:
        band.update({
            "warn_mean": round(min(0.75, max(0.35, mean_limit)), 3),
            "warn_slope": round(min(0.10, max(0.02, slope_limit)), 3),
            "_source": "作者档",
        })
    return band


def build_d5_curves(per_cluster: dict[str, list[dict]]) -> dict:
    """把每个角色在不同故事块的偏离度组成连续曲线。"""
    by_character: dict[str, list[dict]] = {}
    for index, (cluster_id, entries) in enumerate(per_cluster.items(), start=1):
        for entry in entries:
            character = entry.get("character")
            drift = entry.get("drift")
            if not isinstance(character, str) or not isinstance(drift, (int, float)):
                continue
            by_character.setdefault(character, []).append({
                "cluster_id": cluster_id,
                "cluster_index": index,
                "drift": round(float(drift), 3),
            })

    curves = {}
    for character, points in by_character.items():
        values = [point["drift"] for point in points]
        indexes = [point["cluster_index"] for point in points]
        count = len(values)
        volatility = (
            sum(abs(values[i] - values[i - 1]) for i in range(1, count))
            / (count - 1)
            if count >= 2 else 0.0
        )
        curves[character] = {
            "points": points,
            "n": count,
            "mean": round(sum(values) / count, 3),
            "last": round(values[-1], 3),
            "slope": round(_linreg_slope(indexes, values), 4),
            "volatility": round(volatility, 3),
        }
    return curves


def build_d5_findings(curves: dict, band: dict) -> list[dict]:
    """对至少三个故事块的高位或持续上行曲线给出 advisory。"""
    findings = []
    mean_limit = band.get("warn_mean", _D5_GENERIC_BAND["warn_mean"])
    slope_limit = band.get("warn_slope", _D5_GENERIC_BAND["warn_slope"])
    for character, curve in curves.items():
        if curve.get("n", 0) < 3:
            continue
        mean_value = curve.get("mean", 0.0)
        slope = curve.get("slope", 0.0)
        rising = slope >= slope_limit and mean_value >= 0.5 * mean_limit
        elevated = mean_value >= mean_limit
        if not (rising or elevated):
            continue
        findings.append({
            "severity": "advisory",
            "gate_level": "advisory",
            "code": "PERSONA_DRIFT_CURVE_TREND",
            "character": character,
            "metric": {
                "n_points": curve["n"],
                "mean": mean_value,
                "last": curve["last"],
                "slope": slope,
                "volatility": curve["volatility"],
                "band": {
                    "warn_mean": mean_limit,
                    "warn_slope": slope_limit,
                    "source": band.get("_source", "通用"),
                },
            },
            "curve": curve["points"],
            "message": f"角色「{character}」的 voice 偏离在多个故事块持续偏高",
            "suggestion": "结合角色弧线判断是合理演变还是无因果的人设走偏",
        })
    return findings


def extract_persona_drift(clusters: list[dict]) -> dict[str, list[dict]]:
    """从 canonical ``audit.persona_drift`` 读取每个故事块的角色偏离点。"""
    result = {}
    for cluster in clusters:
        drift_map = (cluster.get("audit") or {}).get("persona_drift") or {}
        if not isinstance(drift_map, dict):
            continue
        entries = [
            {
                "character": character,
                "drift": float(drift),
                "metric": {"character": character, "drift": float(drift)},
            }
            for character, drift in drift_map.items()
            if isinstance(character, str)
            and isinstance(drift, (int, float))
            and not isinstance(drift, bool)
        ]
        if entries:
            result[cluster["cluster_id"]] = entries
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=5)
    args = parser.parse_args()
    project_root = Path(args.project)
    clusters = csr.get_clusters(project_root, last_n=args.last_n)
    per_cluster = extract_persona_drift(clusters)
    if not per_cluster:
        print("[SKIP] 故事块摘要没有 persona drift telemetry")
        raise SystemExit(0)

    point_findings = _build_findings(per_cluster)
    band = compute_d5_author_band(project_root)
    curves = build_d5_curves(per_cluster)
    trend_findings = build_d5_findings(curves, band)
    findings = point_findings + trend_findings
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "persona_drift",
        "scan_ts": timestamp,
        "clusters_scanned": list(per_cluster),
        "per_cluster": {
            cluster_id: [
                {"character": item["character"], "drift": item["drift"]}
                for item in entries
            ]
            for cluster_id, entries in per_cluster.items()
        },
        "findings": findings,
        "d5_persona_drift_curve": {
            "gate_level": "advisory",
            "band": band,
            "curves": curves,
        },
        "summary": {
            "warning": sum(item["severity"] == "warning" for item in findings),
            "advisory": sum(item["severity"] == "advisory" for item in findings),
            "total": len(findings),
        },
    }
    output_dir = project_root / "_数据库" / ".cross_cluster_scan"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"persona_drift_{timestamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[persona_drift] 扫描 {len(per_cluster)} 个故事块，发现 {len(findings)} 项")
    print(f"报告: {output_path}")
    if report["summary"]["warning"]:
        raise SystemExit(2)
    raise SystemExit(1 if findings else 0)


if __name__ == "__main__":
    main()
