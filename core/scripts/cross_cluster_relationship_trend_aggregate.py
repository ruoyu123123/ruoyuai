"""cross_cluster_relationship_trend_aggregate.py — 关系 4 维数值跨章变化趋势（CCR21）

收集每章 _changes.factual.relationships[] 中 from→to 的 4 维变化（affinity/trust/fear/respect），
重建跨章变化时序，检测：

- RELATIONSHIP_LEAP：单章某维度变化 ≥ 5（如 trust +6）= 不合理急变
- RELATIONSHIP_FROZEN：≥ 8 章某关系无任何数值变更
- RELATIONSHIP_MONOTONIC_DROP：某关系某维度连续 ≥ 4 章单调下降无回弹
- RELATIONSHIP_OUT_OF_BOUND：当前数值 > 10 或 < -10（关系数值规范化外）

退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=15)
    args = ap.parse_args()

    project_root = Path(args.project)

    # 收集每章 relationships 变化
    # 结构：(from, to) -> [{ch, affinity, trust, fear, respect}]
    history = defaultdict(list)
    recent = []

    def _ingest_rels(ch, rels):
        for r in rels or []:
            if not isinstance(r, dict):
                continue
            f = r.get("from")
            t = r.get("to")
            if not f or not t:
                continue
            entry = {"ch": ch}
            for dim in ["affinity", "trust", "fear", "respect"]:
                if dim in r and isinstance(r[dim], (int, float)) and not isinstance(r[dim], bool):
                    entry[dim] = r[dim]
            if len(entry) > 1:
                history[(f, t)].append(entry)

    # ===== 2026-05-29 cluster 化分支：账本有 relationships → 摘要驱动 =====
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster」
    if csr.is_cluster_mode() and csr.ledger_has_field(project_root, "relationships"):
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        recent = sorted({ch for ch, _ in recs})
        if not recent:
            print("[SKIP] cluster 账本无 relationships 记录")
            sys.exit(0)
        for ch, rec in recs:
            _ingest_rels(ch, rec.get("relationships", []))
    else:
        # ===== 原逐章磁盘逻辑（非 cluster 模式 / 账本缺字段 → 零回归）=====
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        recent = chapters[-args.last_n:] if chapters else []
        if not recent:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        for ch in recent:
            p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
            changes = load_json(p, {})
            rels = (changes.get("factual", {}) or {}).get("relationships", []) or []
            _ingest_rels(ch, rels)

    findings = []

    # 当前关系状态（直接读关系.json）
    current_rels = (load_json(project_root / "_数据库" / "关系.json", {}) or {}).get("relationships", []) or []
    for r in current_rels:
        f = r.get("from")
        t = r.get("to")
        if not f or not t:
            continue
        for dim in ["affinity", "trust", "fear", "respect"]:
            v = r.get(dim)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if v > 10 or v < -10:
                findings.append({
                    "severity": "warning",
                    "code": "RELATIONSHIP_OUT_OF_BOUND",
                    "from": f,
                    "to": t,
                    "dimension": dim,
                    "value": v,
                    "suggestion": f"{f}→{t}.{dim}={v} 超出 [-10, 10] 规范化范围",
                })

    # 时序检测
    for (f, t), entries in history.items():
        if len(entries) < 2:
            continue
        # 排序
        entries_sorted = sorted(entries, key=lambda e: e["ch"])

        # LEAP & MONOTONIC_DROP（按 dimension 维度独立）
        for dim in ["affinity", "trust", "fear", "respect"]:
            dim_series = [(e["ch"], e[dim]) for e in entries_sorted if dim in e]
            if len(dim_series) < 2:
                continue
            # LEAP
            for i in range(1, len(dim_series)):
                ch1, v1 = dim_series[i - 1]
                ch2, v2 = dim_series[i]
                delta = abs(v2 - v1)
                if delta >= 5:
                    findings.append({
                        "severity": "warning",
                        "code": "RELATIONSHIP_LEAP",
                        "from": f,
                        "to": t,
                        "dimension": dim,
                        "from_ch": ch1,
                        "to_ch": ch2,
                        "delta": v2 - v1,
                        "suggestion": f"{f}→{t}.{dim} 在 ch{ch1}→{ch2} 变化 {v2-v1} (|Δ|≥5) → 不合理急变",
                    })
            # MONOTONIC_DROP
            if len(dim_series) >= 4:
                drop_streak = 0
                for i in range(1, len(dim_series)):
                    if dim_series[i][1] < dim_series[i - 1][1]:
                        drop_streak += 1
                        if drop_streak >= 3:
                            findings.append({
                                "severity": "advisory",
                                "code": "RELATIONSHIP_MONOTONIC_DROP",
                                "from": f,
                                "to": t,
                                "dimension": dim,
                                "trail": [(c, v) for c, v in dim_series[i - 3:i + 1]],
                                "suggestion": f"{f}→{t}.{dim} 连续 {drop_streak + 1} 次单调下降无回弹 → 关系恶化太单调",
                            })
                            drop_streak = 0
                    else:
                        drop_streak = 0

    # FROZEN：关系.json 里存在但近 last_n 章窗口内无任何数值变更（不在 history 中）
    # （history 是 defaultdict，键随首次 append 诞生 → 真正零变更的关系结构性地从不进 history，
    #  故必须独立遍历 current_rels 找『不在 history』者，原内联 len(entries)==0 分支恒为死代码）
    if len(recent) >= 8:
        for r in current_rels:
            f = r.get("from")
            t = r.get("to")
            if not f or not t:
                continue
            if (f, t) not in history:
                findings.append({
                    "severity": "advisory",
                    "code": "RELATIONSHIP_FROZEN",
                    "from": f,
                    "to": t,
                    "no_change_chs": len(recent),
                    "suggestion": f"关系 {f}→{t} 近 {len(recent)} 章无任何数值变更 → 关系停滞",
                })

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "relationship_trend",
        "scan_ts": ts,
        "chapters_scanned": recent,
        "relationships_with_history": len(history),
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"relationship_trend_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[relationship_trend] {len(history)} 关系: {summary['warning']} warning / {summary['advisory']} advisory")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
