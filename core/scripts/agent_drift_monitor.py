"""agent_drift_monitor.py — Agent Stability Index（v22.5 L19）

业界 arxiv 2601.04170 Agent Drift + MI9 framework：
监测 agent 行为随时间漂移（不是角色漂移，是 agent 自己的行为）。

6 维度 ASI（简化版）：
1. OUTPUT_LENGTH_STABILITY: agent 输出长度趋势是否稳
2. STRUCTURED_FIELD_COMPLETENESS: _changes.json schema 字段完整率
3. ERROR_RATE_TREND: agent 失败/超时率
4. WAIVER_RATE_TREND: writer 用 waiver 频次趋势
5. RETRY_RATE_TREND: 同章节 audit 次数趋势
6. RESPONSE_TIME_TREND（路线图，需 God Log）

输出：_数据库/.learning/agent_drift_<ts>.json
退出码: 0 健康 / 1 advisory drift / 2 warning 显著漂移

2026-05-29 cluster 化：
  v27 splitter 按字数硬切 3000-4500/章已**人为均一化**章字数 —— 逐章 CJK 的 CV
  漂移失真（splitter 把波动磨平了）。writer 的真实输出长度漂移只在 **cluster draft
  层**可见（writer freestyle 自由发挥，cluster draft 字数才反映 agent 行为）。
  故分析单位「章」→「cluster」：窗口 `--last-n` 默认从 20 章改 6 cluster；字数 CV 测
  cluster draft（章节/cluster_<key>_draft/cluster_<key>_draft.txt → 回退 word_count
  账本字段）；waiver/schema/retry 在 cluster 内聚合后按 cluster 序列做漂移。
  账本/草稿缺失优雅退回原逐章逻辑（cluster 化只换单位，不删能力）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_summary_reader as csr  # 2026-05-29 cluster 化：cluster 账本/窗口
    import cluster_lookup  # 2026-05-29 cluster 化：章号→cluster_id 反查
except Exception:  # 防御：共享模块缺失也不崩，退回纯逐章模式
    csr = None
    cluster_lookup = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_output_length_stability(project_root: Path, chs: list[int]) -> dict:
    """维度 1: writer 输出长度稳定性"""
    lengths = []
    for ch in chs:
        text_p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
        if text_p.exists():
            cn = len(re.findall(r"[一-鿿]", text_p.read_text(encoding="utf-8")))
            lengths.append((ch, cn))
    if len(lengths) < 5:
        return {"status": "insufficient_data"}
    counts = [c for _, c in lengths]
    mean = sum(counts) / len(counts)
    std = (sum((c - mean) ** 2 for c in counts) / len(counts)) ** 0.5
    cv = std / mean if mean > 0 else 0
    finding = None
    if cv > 0.35:
        finding = {
            "code": "OUTPUT_LENGTH_DRIFT",
            "cv": round(cv, 2),
            "msg": f"writer 字数 CV {cv:.2f} > 0.35 → 输出长度不稳",
        }
    return {"cv": round(cv, 2), "mean": round(mean), "finding": finding}


def check_schema_completeness(project_root: Path, chs: list[int]) -> dict:
    """维度 2: _changes.json 关键字段完整率"""
    required_fields_factual = ["locked_facts", "relationships"]
    required_fields_self_eval = ["applied_style"]
    total = 0
    missing = Counter()
    for ch in chs:
        cp = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        if cp.exists():
            total += 1
            changes = load_json(cp, {})
            fact = changes.get("factual", {}) or {}
            seval = changes.get("self_eval", {}) or {}
            for f in required_fields_factual:
                if f not in fact:
                    missing[f"factual.{f}"] += 1
            for f in required_fields_self_eval:
                if f not in seval:
                    missing[f"self_eval.{f}"] += 1
    if total < 3:
        return {"status": "insufficient_data"}
    completeness = 1.0 - sum(missing.values()) / (total * (len(required_fields_factual) + len(required_fields_self_eval)))
    finding = None
    if completeness < 0.8:
        finding = {
            "code": "SCHEMA_COMPLETENESS_DRIFT",
            "completeness": round(completeness, 2),
            "missing_fields": dict(missing),
            "msg": f"changes schema 完整率 {completeness:.0%} < 80% → writer 越来越偷懒",
        }
    return {"completeness": round(completeness, 2), "missing": dict(missing), "finding": finding}


def check_waiver_trend(project_root: Path, chs: list[int]) -> dict:
    """维度 4: waiver 用量趋势"""
    if len(chs) < 6:
        return {"status": "insufficient_data"}
    waivers_per_ch = []
    for ch in chs:
        cp = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
        if cp.exists():
            d = load_json(cp, {})
            w = (d.get("self_eval", {}) or {}).get("waivers", []) or []
            waivers_per_ch.append((ch, len(w)))
    if len(waivers_per_ch) < 6:
        return {"status": "insufficient_data"}
    half = len(waivers_per_ch) // 2
    first = sum(w for _, w in waivers_per_ch[:half]) / half
    second = sum(w for _, w in waivers_per_ch[half:]) / (len(waivers_per_ch) - half)
    finding = None
    if second > first + 2:
        finding = {
            "code": "WAIVER_RATE_INFLATION",
            "first_half_avg": round(first, 1),
            "second_half_avg": round(second, 1),
            "msg": f"waiver 使用从 {first:.1f}/章 升到 {second:.1f}/章 → writer 越来越用 waiver 逃避",
        }
    return {"first_half_avg": round(first, 1), "second_half_avg": round(second, 1), "finding": finding}


def check_retry_trend(project_root: Path, chs: list[int]) -> dict:
    """维度 5: audit 重试率趋势"""
    audit_dir = project_root / "_数据库" / ".audit"
    if not audit_dir.exists():
        return {"status": "no_audit_data"}
    per_ch_audit = Counter()
    for f in audit_dir.glob("ch_*_audit*.json"):
        m = re.match(r"ch_(\d+)", f.name)
        if m:
            ch = int(m.group(1))
            if ch in chs:
                per_ch_audit[ch] += 1
    if not per_ch_audit or len(per_ch_audit) < 4:
        return {"status": "insufficient_data"}
    sorted_chs = sorted(per_ch_audit.items())
    half = len(sorted_chs) // 2
    first = sum(c for _, c in sorted_chs[:half]) / half
    second = sum(c for _, c in sorted_chs[half:]) / (len(sorted_chs) - half)
    finding = None
    if second > first * 1.5 and second >= 2.5:
        finding = {
            "code": "RETRY_RATE_INFLATION",
            "first_half_avg": round(first, 1),
            "second_half_avg": round(second, 1),
            "msg": f"audit 次数从 {first:.1f}/章 升 {second:.1f}/章 → writer 不稳定",
        }
    return {"first_half_avg": round(first, 1), "second_half_avg": round(second, 1), "finding": finding}


# ============================================================
# 2026-05-29 cluster 化：cluster 窗口维度（取代逐章 CV）
# ============================================================

def _cluster_draft_cjk(project_root: Path, cluster_id: str) -> int | None:
    """取 cluster draft 的 CJK 字数：先读草稿 .txt，回退账本 word_count。"""
    key = None
    if cluster_lookup is not None:
        n = cluster_lookup.cluster_num(cluster_id)
        if n is not None:
            key = f"{n:03d}"
    if key:
        for cand in (
            project_root / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt",
            project_root / "章节" / f"cluster_{key}_draft.txt",
        ):
            if cand.exists():
                try:
                    return len(re.findall(r"[一-鿿]", cand.read_text(encoding="utf-8")))
                except OSError:
                    pass
    return None


def check_cluster_length_stability(project_root: Path, clusters: list[dict]) -> dict:
    """维度 1（cluster）: cluster draft 字数 CV —— writer freestyle 的真实输出漂移。"""
    counts = []
    for c in clusters:
        cjk = _cluster_draft_cjk(project_root, c.get("cluster_id"))
        if cjk is None:
            wc = c.get("word_count")
            cjk = wc if isinstance(wc, (int, float)) and wc > 0 else None
        if cjk:
            counts.append(cjk)
    if len(counts) < 3:
        return {"status": "insufficient_data", "n": len(counts)}
    mean = sum(counts) / len(counts)
    std = (sum((c - mean) ** 2 for c in counts) / len(counts)) ** 0.5
    cv = std / mean if mean > 0 else 0
    finding = None
    if cv > 0.35:
        finding = {
            "code": "CLUSTER_OUTPUT_LENGTH_DRIFT",
            "cv": round(cv, 2),
            "msg": f"cluster draft 字数 CV {cv:.2f} > 0.35 → writer 块级输出长度不稳",
        }
    return {"cv": round(cv, 2), "mean": round(mean), "n": len(counts), "finding": finding}


def _cluster_chapter_records(c: dict) -> list[dict]:
    chapters = c.get("chapters") or {}
    if not isinstance(chapters, dict):
        return []
    return [r for r in chapters.values() if isinstance(r, dict)]


def check_cluster_waiver_trend(project_root: Path, clusters: list[dict]) -> dict:
    """维度 4（cluster）: 每 cluster 聚合 waiver 数的趋势（前半 vs 后半）。"""
    waivers_per_cluster = []
    for c in clusters:
        total_w = 0
        for rec in _cluster_chapter_records(c):
            w = rec.get("waivers") or []
            if isinstance(w, list):
                total_w += len(w)
        waivers_per_cluster.append(total_w)
    if len(waivers_per_cluster) < 4:
        return {"status": "insufficient_data", "n": len(waivers_per_cluster)}
    half = len(waivers_per_cluster) // 2
    first = sum(waivers_per_cluster[:half]) / half
    second = sum(waivers_per_cluster[half:]) / (len(waivers_per_cluster) - half)
    finding = None
    if second > first + 2:
        finding = {
            "code": "WAIVER_RATE_INFLATION",
            "first_half_avg": round(first, 1),
            "second_half_avg": round(second, 1),
            "msg": f"waiver 从 {first:.1f}/块 升到 {second:.1f}/块 → writer 越来越用 waiver 逃避",
        }
    return {"first_half_avg": round(first, 1), "second_half_avg": round(second, 1), "finding": finding}


def check_cluster_schema_completeness(project_root: Path, clusters: list[dict]) -> dict:
    """维度 2（cluster）: 账本里 per-ch 关键字段（judge_score/waivers）填充率。"""
    required = ["judge_score", "hook_score"]
    total = 0
    missing = Counter()
    for c in clusters:
        for rec in _cluster_chapter_records(c):
            total += 1
            for f in required:
                if rec.get(f) is None:
                    missing[f] += 1
    if total < 3:
        return {"status": "insufficient_data", "n": total}
    completeness = 1.0 - sum(missing.values()) / (total * len(required))
    finding = None
    if completeness < 0.8:
        finding = {
            "code": "SCHEMA_COMPLETENESS_DRIFT",
            "completeness": round(completeness, 2),
            "missing_fields": dict(missing),
            "msg": f"账本 per-ch 字段完整率 {completeness:.0%} < 80% → save-state 越来越偷懒",
        }
    return {"completeness": round(completeness, 2), "missing": dict(missing), "finding": finding}


def check_cluster_retry_trend(project_root: Path, clusters: list[dict]) -> dict:
    """维度 5（cluster）: 每 cluster 内 audit 重跑次数聚合的趋势。"""
    audit_dir = project_root / "_数据库" / ".audit"
    if not audit_dir.exists():
        return {"status": "no_audit_data"}
    per_ch_audit = Counter()
    for f in audit_dir.glob("ch_*_audit*.json"):
        m = re.match(r"ch_(\d+)", f.name)
        if m:
            per_ch_audit[int(m.group(1))] += 1
    if not per_ch_audit:
        return {"status": "insufficient_data"}
    per_cluster = []
    for c in clusters:
        cr = c.get("chapter_range")
        chs = set()
        if isinstance(cr, list) and len(cr) == 2:
            chs = set(range(cr[0], cr[1] + 1))
        else:
            chs = {int(k) for k in (c.get("chapters") or {}) if str(k).isdigit()}
        per_cluster.append(sum(per_ch_audit.get(ch, 0) for ch in chs))
    nz = [x for x in per_cluster if x]
    if len(nz) < 3:
        return {"status": "insufficient_data", "n": len(nz)}
    half = len(per_cluster) // 2
    if half == 0:
        return {"status": "insufficient_data"}
    first = sum(per_cluster[:half]) / half
    second = sum(per_cluster[half:]) / (len(per_cluster) - half)
    finding = None
    if second > first * 1.5 and second >= 2.5:
        finding = {
            "code": "RETRY_RATE_INFLATION",
            "first_half_avg": round(first, 1),
            "second_half_avg": round(second, 1),
            "msg": f"块级 audit 次数从 {first:.1f} 升 {second:.1f} → writer 不稳定",
        }
    return {"first_half_avg": round(first, 1), "second_half_avg": round(second, 1), "finding": finding}


def run_cluster_mode(project_root: Path, last_n: int) -> dict | None:
    """cluster 窗口漂移分析。账本无 cluster → 返回 None（调用方回退逐章）。"""
    if csr is None:
        return None
    clusters = csr.get_clusters(project_root, last_n=last_n)
    if len(clusters) < 3:
        return None
    dimensions = {
        "cluster_output_length": check_cluster_length_stability(project_root, clusters),
        "schema_completeness": check_cluster_schema_completeness(project_root, clusters),
        "waiver_trend": check_cluster_waiver_trend(project_root, clusters),
        "retry_trend": check_cluster_retry_trend(project_root, clusters),
    }
    findings = [d["finding"] for d in dimensions.values() if d.get("finding")]
    return {
        "scan_type": "agent_drift_monitor",
        "analysis_unit": "cluster",  # 2026-05-29 cluster 化
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "clusters_window": [c.get("cluster_id") for c in clusters],
        "dimensions": dimensions,
        "findings": findings,
        "asi_score": max(0, 1.0 - len(findings) * 0.2),
    }


def run_chapter_mode(project_root: Path, last_n: int) -> dict | None:
    """逐章漂移分析（cluster 账本缺失时的向后兼容回退）。"""
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    recent = chapters[-last_n:] if chapters else []
    if not recent:
        return None
    dimensions = {
        "output_length": check_output_length_stability(project_root, recent),
        "schema_completeness": check_schema_completeness(project_root, recent),
        "waiver_trend": check_waiver_trend(project_root, recent),
        "retry_trend": check_retry_trend(project_root, recent),
    }
    findings = [d["finding"] for d in dimensions.values() if d.get("finding")]
    return {
        "scan_type": "agent_drift_monitor",
        "analysis_unit": "chapter",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "chs_window": recent,
        "dimensions": dimensions,
        "findings": findings,
        "asi_score": max(0, 1.0 - len(findings) * 0.2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    # 2026-05-29 cluster 化：默认窗口改 cluster 数（旧默认 20 章 ≈ 6 cluster）
    ap.add_argument("--last-n", type=int, default=6,
                    help="cluster 窗口大小（账本缺失回退逐章时按 章×3.3 ≈ 此值扩展）")
    ap.add_argument("--mode", choices=["auto", "cluster", "chapter"], default="auto",
                    help="auto: 优先 cluster，账本缺失回退逐章")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()

    out = None
    if args.mode in ("auto", "cluster"):
        out = run_cluster_mode(project_root, args.last_n)
    if out is None and args.mode in ("auto", "chapter"):
        # cluster 账本不足 → 回退逐章（窗口换算回章数）
        out = run_chapter_mode(project_root, max(args.last_n * 4, 20))
    if out is None:
        print("[SKIP] 无 cluster 账本也无章节")
        sys.exit(0)

    findings = out["findings"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"agent_drift_{ts}.json"
    save_json(out_path, out)
    print(f"[agent_drift_monitor] unit={out.get('analysis_unit')} "
          f"ASI={out['asi_score']:.2f} findings={len(findings)}")
    for f in findings:
        print(f"  [{f['code']}] {f.get('msg', '')[:80]}")
    print(f"  报告: {out_path}")
    sys.exit(2 if len(findings) >= 3 else (1 if findings else 0))


if __name__ == "__main__":
    main()
