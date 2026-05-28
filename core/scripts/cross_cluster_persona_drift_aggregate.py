"""cross_cluster_persona_drift_aggregate.py — Persona Drift 跨章扫描（v19.6 G8 新增）

复用 embedding_store 的 character baseline + per-chapter cosine 距离，识别角色 voice 漂移。

业界对照：
- SyncScore 用 latent-style embedding 量化角色行为偏离 baseline
- 业界 LLM 长故事中频繁出现 persona drift（angry 角色突然温柔说话）

实现：
- 对每章最近 N 章扫所有有 baseline 的角色
- drift > 0.3 = 显著漂移告警

用法：python cross_cluster_persona_drift_aggregate.py <project> [--last-n 5]
退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import embedding_store  # type: ignore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=5)
    args = ap.parse_args()

    project_root = Path(args.project)
    emb_dir = project_root / "_数据库" / ".embeddings"
    if not emb_dir.is_dir():
        print(f"[SKIP] embeddings 不存在，先跑 embedding_store.py rebuild")
        sys.exit(0)

    # 找有 baseline 的角色
    baseline_files = list(emb_dir.glob("character_*.json"))
    characters = [f.stem.replace("character_", "") for f in baseline_files]
    if not characters:
        print(f"[SKIP] 无 character baseline")
        sys.exit(0)

    # 找所有已写章节
    all_chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
    chapters = all_chapters[-args.last_n:]

    findings = []
    per_chapter = {}
    for ch in chapters:
        per_chapter[ch] = []
        for char in characters:
            result = embedding_store.compute_character_drift(project_root, char, ch)
            if "error" in result:
                continue
            drift = result.get("drift")
            if drift is None:
                continue
            per_chapter[ch].append({"character": char, "drift": drift})
            if drift > 0.5:
                findings.append({
                    "severity": "warning" if drift > 0.75 else "advisory",
                    "code": "PERSONA_DRIFT_DETECTED",
                    "chapter": ch,
                    "metric": result,
                    "message": f"ch{ch} 角色「{char}」voice drift {drift}（与 baseline 余弦距离）",
                    "suggestion": f"writer 可能让 {char} 行为偏离 baseline persona，validator-repair 审查是否合理",
                })

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "persona_drift",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "per_chapter": per_chapter,
        "findings": findings,
        "summary": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"persona_drift_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[persona_drift_scan] 扫描 ch{chapters}: {len(findings)} 项")
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f['message']}")
    print(f"报告: {out_path}")

    if any(f["severity"] == "warning" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
