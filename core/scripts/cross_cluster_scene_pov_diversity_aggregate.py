"""Audit scene-type and POV variety across completed story clusters.

The scanner consumes ``故事块摘要.json`` only. Chapters nested inside a
cluster are precomputed evidence and never form the audit unit.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402


def _normalise(value):
    if isinstance(value, list):
        return value[0] if value else ""
    return value if isinstance(value, str) else ""


def _cluster_observations(project_root: Path, last_n: int) -> list[dict]:
    clusters = csr.get_clusters(project_root)
    if last_n > 0:
        clusters = clusters[-last_n:]
    observations: list[dict] = []
    for cluster in clusters:
        cid = str(cluster.get("cluster_id") or "")
        scene_type = _normalise(cluster.get("scene_type"))
        pov = _normalise(cluster.get("pov"))
        characters = cluster.get("characters") or []
        if not pov and characters:
            pov = _normalise(characters[0])
        nested = cluster.get("chapters") or {}
        if isinstance(nested, dict):
            scene_values = [_normalise(v.get("scene_type")) for v in nested.values()
                            if isinstance(v, dict) and _normalise(v.get("scene_type"))]
            pov_values = [_normalise(v.get("pov")) for v in nested.values()
                          if isinstance(v, dict) and _normalise(v.get("pov"))]
            if not scene_type and scene_values:
                scene_type = Counter(scene_values).most_common(1)[0][0]
            if not pov and pov_values:
                pov = Counter(pov_values).most_common(1)[0][0]
        if cid:
            observations.append({"cluster_id": cid, "scene_type": scene_type, "pov": pov})
    return observations


def _scan(observations: list[dict]) -> list[dict]:
    findings: list[dict] = []
    scenes = [(o["cluster_id"], o["scene_type"]) for o in observations if o["scene_type"]]
    povs = [(o["cluster_id"], o["pov"]) for o in observations if o["pov"]]

    if len(scenes) >= 3:
        current = scenes[0][1]
        run_ids = [scenes[0][0]]
        for cid, scene_type in scenes[1:]:
            if scene_type == current:
                run_ids.append(cid)
                if len(run_ids) >= 4:
                    findings.append({
                        "severity": "advisory",
                        "code": "SCENE_TYPE_RUN",
                        "scene_type": current,
                        "consecutive_clusters": run_ids[-4:],
                        "suggestion": f"连续 cluster 使用 scene_type={current}，应改变场景功能",
                    })
                    run_ids = [cid]
            else:
                current = scene_type
                run_ids = [cid]
        distribution = Counter(scene_type for _, scene_type in scenes)
        if len(scenes) >= 5 and len(distribution) <= 2:
            findings.append({
                "severity": "advisory",
                "code": "SCENE_TYPE_LOW_DIVERSITY",
                "distribution": dict(distribution),
                "suggestion": "当前 cluster 的场景类型过于集中",
            })

    if len(povs) >= 4:
        current = povs[0][1]
        run_ids = [povs[0][0]]
        for cid, pov in povs[1:]:
            if pov == current:
                run_ids.append(cid)
                if len(run_ids) >= 8:
                    findings.append({
                        "severity": "advisory",
                        "code": "POV_LOCKED",
                        "pov": current,
                        "consecutive_clusters": run_ids[-8:],
                        "suggestion": "同一 POV 连续覆盖过多 cluster，应引入有效视角变化",
                    })
                    run_ids = [cid]
            else:
                current = pov
                run_ids = [cid]
        distribution = Counter(pov for _, pov in povs)
        dominant, count = distribution.most_common(1)[0]
        if len(povs) >= 5 and count / len(povs) > 0.85:
            findings.append({
                "severity": "advisory",
                "code": "POV_OVERCONCENTRATED",
                "dominant_pov": dominant,
                "pct": round(count / len(povs), 2),
                "distribution": dict(distribution),
                "suggestion": "单一 POV 覆盖过高，应评估是否需要切换视角",
            })
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)

    observations = _cluster_observations(project_root, args.last_n)
    if not observations:
        print("[SKIP] no completed cluster records")
        raise SystemExit(0)
    findings = _scan(observations)
    summary = {
        "warning": sum(f["severity"] == "warning" for f in findings),
        "advisory": sum(f["severity"] == "advisory" for f in findings),
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "scene_pov_diversity",
        "scan_ts": ts,
        "clusters_scanned": [o["cluster_id"] for o in observations],
        "cluster_observations": observations,
        "scene_distribution": dict(Counter(o["scene_type"] for o in observations if o["scene_type"])),
        "pov_distribution": dict(Counter(o["pov"] for o in observations if o["pov"])),
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"scene_pov_diversity_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scene_pov_diversity] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
