"""Detect cross-cluster character-presence imbalance and forgotten long tails.

The scanner compares accumulated cluster presence with the active author's
``character_presence_distribution`` and emits advisory findings only. Solo
genres are excluded by explicit genre policy. ``CHARACTER_PRESENCE_BALANCE_MODE``
accepts ``off``, ``shadow``, or ``active``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402

CODE_GINI = "PRESENCE_GINI_DEVIATION"
CODE_LONG_TAIL = "PRESENCE_LONG_TAIL_FORGOTTEN"

# 阈值（保守 · 待金标准校准）
GINI_DEVIATION_K = 1.0           # 偏离 z-band > k·σ 才报
LONG_TAIL_MIN_APPEAR = 3         # 角色累计出现 ≥ N 次才纳入「曾出场」
LONG_TAIL_ABSENCE_CLUSTERS = 3   # 末 N cluster 全缺席 = 长尾遗忘
LONG_TAIL_COUNT_FLOOR = 3        # ≥ N 个长尾角色才报

# 独角戏豁免 genre 关键词
SOLO_GENRES = {"solo_cultivation", "first_person_retro", "diary", "monologue", "独角戏"}


def _mode() -> str:
    m = (os.environ.get("CHARACTER_PRESENCE_BALANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_genre(project_root: Path) -> str:
    """读用户偏好 / 作者风格的 genre 字段。"""
    for name in ("用户偏好.json", "作者风格.json"):
        p = project_root / "_数据库" / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        g = data.get("genre") or (data.get("metadata") or {}).get("genre")
        if isinstance(g, str) and g.strip():
            return g.strip().lower()
    return ""


def _read_author_presence_baseline(project_root: Path) -> dict | None:
    """Read the author cluster-presence distribution fingerprint."""
    p = project_root / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cpd = data.get("character_presence_distribution")
    if isinstance(cpd, dict) and "gini_mean" in cpd:
        sources = cpd.get("source_clusters")
        if not isinstance(sources, list) or not sources:
            return None
        if any(not isinstance(value, str) or not value.startswith("cluster_") for value in sources):
            return None
        return cpd
    return None


def gini_coefficient(values: list[float]) -> float:
    """计算 Gini 系数（0 = 完全平等·1 = 完全集中）。确定性 numpy-free 实现。"""
    vs = sorted(float(v) for v in values if v and v > 0)
    n = len(vs)
    if n < 2:
        return 0.0
    cum = 0.0
    total = sum(vs)
    if total == 0:
        return 0.0
    for i, v in enumerate(vs, start=1):
        cum += (2 * i - n - 1) * v
    return cum / (n * total)


def compute_presence_stats(clusters: list[dict]) -> dict:
    """累计每角色 mention_count + 末 N cluster 出场 flag。

    读账本 cluster 级顶层字段 char_mention_counts / characters（cluster_summary_reader
    CLUSTER_FIELDS 合同），不是按章嵌套结构——一个 cluster 摘要记录只有一份汇总。
    """
    char_total: dict = {}
    char_last_seen: dict = {}
    cluster_appearance: dict = {}  # cluster_id → set(chars)
    for c in clusters:
        cid = c.get("cluster_id")
        seen = set()
        cm = c.get("char_mention_counts") or {}
        if isinstance(cm, dict):
            for char, cnt in cm.items():
                if isinstance(cnt, (int, float)) and cnt > 0:
                    char_total[char] = char_total.get(char, 0) + int(cnt)
                    char_last_seen[char] = cid
                    seen.add(char)
        for char in (c.get("characters") or []):
            if isinstance(char, str) and char:
                seen.add(char)
                char_last_seen[char] = cid
                char_total.setdefault(char, 0)
        cluster_appearance[cid] = seen
    return {
        "char_total": char_total,
        "char_last_seen": char_last_seen,
        "cluster_appearance": cluster_appearance,
    }


def detect_long_tail_forgotten(stats: dict, clusters: list[dict]) -> list[str]:
    """末 N cluster 全缺席的曾出场角色。"""
    if len(clusters) < LONG_TAIL_ABSENCE_CLUSTERS + 1:
        return []
    recent_cids = [c.get("cluster_id") for c in clusters[-LONG_TAIL_ABSENCE_CLUSTERS:]]
    recent_chars: set = set()
    for cid in recent_cids:
        recent_chars |= (stats["cluster_appearance"].get(cid) or set())
    forgotten = []
    for char, total in stats["char_total"].items():
        if total >= LONG_TAIL_MIN_APPEAR and char not in recent_chars:
            forgotten.append(char)
    forgotten.sort(key=lambda x: -stats["char_total"][x])
    return forgotten


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project)
    if mode == "off":
        print("[OFF] CHARACTER_PRESENCE_BALANCE_MODE=off")
        sys.exit(0)

    clusters = csr.get_clusters(project_root)
    if not clusters:
        print("[SKIP] 账本无 cluster 记录")
        sys.exit(0)
    if len(clusters) < 3:
        print(f"[SKIP] cluster 数太少（{len(clusters)} < 3）")
        sys.exit(0)

    # genre 门控：独角戏题材直接跳过整个 scanner
    genre = _read_genre(project_root)
    if genre and any(s in genre for s in SOLO_GENRES):
        print(f"[SKIP] genre={genre} 独角戏豁免")
        sys.exit(0)

    stats = compute_presence_stats(clusters)
    char_total = stats["char_total"]
    if len(char_total) < 3:
        print("[SKIP] 出场角色 <3 · 样本不足")
        sys.exit(0)

    gini = gini_coefficient(list(char_total.values()))
    baseline = _read_author_presence_baseline(project_root)
    g_mean = float(baseline["gini_mean"]) if baseline else None
    g_std = max(float(baseline["gini_std"]), 0.05) if baseline else None
    z_band_lo = g_mean - GINI_DEVIATION_K * g_std if baseline else None
    z_band_hi = g_mean + GINI_DEVIATION_K * g_std if baseline else None

    findings = []
    if baseline and gini > z_band_hi:
        findings.append({
            "severity": "advisory", "code": CODE_GINI,
            "gini": round(gini, 3),
            "baseline": {"mean": g_mean, "std": g_std, "z_band": [round(z_band_lo, 3), round(z_band_hi, 3)]},
            "direction": "too_centralized",
            "suggestion": (f"角色出场失衡（Gini={gini:.2f} > 作者基线 {g_mean:.2f}+1σ）·"
                           f"主角戏过载 → 给配角分流戏份"),
        })
    elif baseline and gini < z_band_lo:
        findings.append({
            "severity": "advisory", "code": CODE_GINI,
            "gini": round(gini, 3),
            "baseline": {"mean": g_mean, "std": g_std, "z_band": [round(z_band_lo, 3), round(z_band_hi, 3)]},
            "direction": "too_dispersed",
            "suggestion": (f"角色戏份过分散（Gini={gini:.2f} < 作者基线 {g_mean:.2f}-1σ）·"
                           f"主角声量被淹 → 收拢主线 POV"),
        })

    forgotten = detect_long_tail_forgotten(stats, clusters)
    if len(forgotten) >= LONG_TAIL_COUNT_FLOOR:
        findings.append({
            "severity": "advisory", "code": CODE_LONG_TAIL,
            "forgotten_count": len(forgotten),
            "forgotten_samples": forgotten[:8],
            "absence_window_clusters": LONG_TAIL_ABSENCE_CLUSTERS,
            "suggestion": (f"{len(forgotten)} 个曾出场角色在末 {LONG_TAIL_ABSENCE_CLUSTERS} cluster 全缺席"
                           f"（如 {forgotten[:3]}）·要么 callback 要么显式退场避免读者疑惑"),
        })

    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "character_presence_balance",
        "scan_ts": ts,
        "mode": mode,
        "clusters_total": len(clusters),
        "gini": round(gini, 3),
        "baseline": baseline,
        "baseline_status": "available" if baseline else "not_available",
        "char_total_sample": dict(sorted(char_total.items(), key=lambda x: -x[1])[:10]),
        "forgotten_count": len(forgotten),
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
        },
    }
    out_path = out_dir / f"character_presence_balance_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    baseline_label = f"{g_mean:.3f}±{g_std:.3f}" if baseline else "not_available"
    print(f"[character_presence_balance] gini={gini:.3f} (baseline={baseline_label}) · "
          f"forgotten={len(forgotten)} · findings={len(findings)}")
    for f in findings[:4]:
        print(f"  [{f['severity'].upper()}] {f['code']}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")

    if mode == "shadow":
        for f in findings:
            print(f"[SHADOW] character_presence_balance: {f['code']} — 不上报", file=sys.stderr)
        sys.exit(0)
    if report["summary"]["warning"] > 0:
        sys.exit(2)
    if report["summary"]["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
