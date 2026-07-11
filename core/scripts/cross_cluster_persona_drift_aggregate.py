"""cross_cluster_persona_drift_aggregate.py — Persona Drift 跨章扫描（v19.6 G8 新增）

复用 embedding_store 的 character baseline + per-chapter cosine 距离，识别角色 voice 漂移。

业界对照：
- SyncScore 用 latent-style embedding 量化角色行为偏离 baseline
- 业界 LLM 长故事中频繁出现 persona drift（angry 角色突然温柔说话）

实现：
- 对每章最近 N 章扫所有有 baseline 的角色
- drift > 0.3 = 显著漂移告警

═══════════════════════════════════════════════════════════════════════
D5 人设漂移曲线（2026-05-31 新增 · 跨 cluster 连续偏离度 · 北极星①②⑤⑥）
═══════════════════════════════════════════════════════════════════════
原 PERSONA_DRIFT_DETECTED 只对单章逐点判 drift>0.5（接近 0/1 阈值告警），
看不到「角色 voice 在多个 cluster 上是否系统性、单调地越走越偏」。D5 在**同一份
逐章 drift 数据**上叠一层「连续偏离度曲线」（非 0/1）：
  · 把逐章 drift 序列拍平成每角色的曲线点 [{ch, drift}, ...]
  · 算连续指标：mean / 末点 / 线性回归斜率（趋势）/ 波动（相邻差绝对值均值）
  · 对斜率（持续走偏）+ 高位均值产出 advisory 提示，severity 永远 advisory

北极星纪律：
  · ⑥ 只加字段不新起系统：复用现有 aggregate 框架 + embedding 逐章 drift，**不新建 scanner**。
  · ② cluster 为单位：曲线点附 cluster_id（cluster_lookup 反查），趋势按 cluster 视野解读。
  · ⑤ 顾问非法官：全 advisory，**绝不进 audit_hub.HARD_GATE_CODES**。
  · 作者档第一权威：作者档 narrative_fingerprint.character_behavior_loops /
    character_depth_grade_distribution 标定「该作者人设有多稳」→ 标定容忍带（band）；
    作者档缺 → 通用兜底带（不臆造）。
  · 影子并行（同 L3a/style_evaluator 范式）env PERSONA_D5_MODE：
      - shadow（默认）：算 D5 曲线挂 report.d5_persona_drift_curve + d5_shadow_findings，
        **不进顶层 findings、不改 exit code**（默认行为零回归）。
      - active：同样计算附加 + 把 D5 advisory 升顶层 findings（仍 advisory，绝不 hard_gate）。
      - off：完全不算 D5（纯旧 persona_drift 行为）。

用法：python cross_cluster_persona_drift_aggregate.py <project> [--last-n 5]
退出码: 0 健康 / 1 advisory / 2 warning（D5 shadow 默认不影响退出码）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

# D5 影子并行开关（同 style_evaluator L3A_BURSTINESS_MODE 范式）：
#   shadow（默认）= 算曲线只记不判 · active = 升顶层 advisory · off = 不算 D5。
PERSONA_D5_MODE = (_os.environ.get("PERSONA_D5_MODE", "shadow") or "shadow").strip().lower()
if PERSONA_D5_MODE not in ("shadow", "active", "off"):
    PERSONA_D5_MODE = "shadow"

sys.path.insert(0, str(Path(__file__).parent))
import embedding_store  # type: ignore
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

try:
    import cluster_lookup  # type: ignore  # 章号⇄cluster_id 唯一权威反查（北极星②）
except Exception:  # pragma: no cover - 兜底：反查不可用时曲线点 cluster_id 留 None，不崩
    cluster_lookup = None


def _build_findings(per_chapter: dict) -> list[dict]:
    """对 per_chapter[ch] = [{character, drift}, ...] 跑统一的 drift 阈值判定。

    2026-05-29 cluster 化：抽出原内联阈值逻辑，让磁盘分支 & 账本分支共用同一判定，
    确保两条路径 finding 输出格式/severity/code 完全一致。
    """
    findings = []
    for ch in sorted(per_chapter.keys()):
        for entry in per_chapter[ch]:
            char = entry.get("character")
            drift = entry.get("drift")
            metric = entry.get("metric", {"drift": drift, "character": char})
            if drift is None:
                continue
            if drift > 0.5:
                findings.append({
                    "severity": "warning" if drift > 0.75 else "advisory",
                    "code": "PERSONA_DRIFT_DETECTED",
                    "chapter": ch,
                    "metric": metric,
                    "message": f"ch{ch} 角色「{char}」voice drift {drift}（与 baseline 余弦距离）",
                    "suggestion": f"writer 可能让 {char} 行为偏离 baseline persona，validator-repair 审查是否合理",
                })
    return findings


# ============================================================
# D5 人设漂移曲线（连续偏离度 · 非 0/1）—— 纯函数，可单测，无 IO
# ============================================================

# 通用容忍带（作者档缺 character_behavior_loops 时兜底；不臆造作者）。
# mean 高于 warn_mean 视作整体偏离偏高；slope 高于 warn_slope 视作持续走偏。
# 数值保守宽松（顾问非法官 · 北极星⑤）：宁可漏报不误伤作者高级手法。
_D5_GENERIC_BAND = {"warn_mean": 0.5, "warn_slope": 0.04, "_source": "通用"}


def _linreg_slope(xs: list, ys: list) -> float:
    """最小二乘斜率（每章 drift 变化率）。零依赖、点 < 2 返回 0.0。"""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return num / denom


def compute_d5_author_band(project_root) -> dict:
    """作者档第一权威：从 narrative_fingerprint 标定该作者「人设有多稳」→ 容忍带。

    依据（作者档现有字段，不新起字段）：
      · character_behavior_loops —— 作者越依赖固定行为环（loop 多/计数高）= 人设越稳定
        → 收紧带（更早提示偏离）。
      · character_depth_grade_distribution —— 高深度角色占比高 = 作者刻意做复杂多面人设
        → 放宽带（容忍更大的合理 voice 起伏，避免误伤作者高级手法）。
    作者档缺 narrative_fingerprint / 两字段都缺 → 返回通用兜底带（_source=通用）。
    """
    root = Path(project_root)
    band = dict(_D5_GENERIC_BAND)
    style = None
    for cand in (root / "_数据库" / "作者风格.json", root / "作者风格.json"):
        if cand.exists():
            try:
                style = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                style = None
            break
    if not isinstance(style, dict):
        return band
    nf = style.get("narrative_fingerprint")
    if not isinstance(nf, dict) or not nf:
        return band  # 蛊真人式空 narrative_fingerprint → 通用兜底

    loops = nf.get("character_behavior_loops")
    depth = nf.get("character_depth_grade_distribution")
    used = False
    warn_mean = _D5_GENERIC_BAND["warn_mean"]
    warn_slope = _D5_GENERIC_BAND["warn_slope"]

    # 行为环：loop 总命中越多 → 人设越固定 → 带略收紧（提早提示）。
    if isinstance(loops, dict) and loops:
        loop_total = sum(v for v in loops.values() if isinstance(v, (int, float)))
        if loop_total >= 8:
            warn_mean -= 0.05
            warn_slope -= 0.005
            used = True
        elif loop_total >= 1:
            used = True

    # 角色深度：高/极高深度占比高 → 作者刻意多面人设 → 放宽带（容忍合理起伏）。
    if isinstance(depth, dict) and depth:
        total = sum(v for v in depth.values() if isinstance(v, (int, float)))
        high = sum(v for k, v in depth.items()
                   if isinstance(v, (int, float)) and any(t in str(k) for t in ("极高", "高")))
        if total > 0:
            high_ratio = high / total
            if high_ratio >= 0.15:
                warn_mean += 0.08
                warn_slope += 0.01
            used = True

    if used:
        # 带钳到安全区间，避免基线极端值算出反直觉带
        band["warn_mean"] = round(min(0.75, max(0.35, warn_mean)), 3)
        band["warn_slope"] = round(min(0.10, max(0.02, warn_slope)), 3)
        band["_source"] = "作者档"
    return band


def build_d5_curves(per_chapter: dict, project_root=None) -> dict:
    """把逐章 drift 拍平成每角色「连续偏离度曲线」+ 连续指标（非 0/1）。

    入参 per_chapter[ch] = [{"character","drift",...}, ...]（与 _build_findings 同源）。
    返回 {char: {"points":[{ch,cluster_id,drift}], "mean","last","slope","volatility","n"}}。
    """
    by_char: dict[str, list] = {}
    for ch in sorted(per_chapter.keys()):
        for entry in per_chapter[ch]:
            char = entry.get("character")
            drift = entry.get("drift")
            if char is None or not isinstance(drift, (int, float)):
                continue
            cid = None
            if project_root is not None and cluster_lookup is not None:
                try:
                    cid = cluster_lookup.ch_to_cluster_id(Path(project_root), int(ch))
                except Exception:
                    cid = None
            by_char.setdefault(char, []).append({
                "ch": int(ch), "cluster_id": cid, "drift": round(float(drift), 3),
            })

    curves: dict[str, dict] = {}
    for char, pts in by_char.items():
        pts.sort(key=lambda p: p["ch"])
        ys = [p["drift"] for p in pts]
        xs = [p["ch"] for p in pts]
        n = len(ys)
        mean = sum(ys) / n if n else 0.0
        slope = _linreg_slope(xs, ys)
        # 波动 = 相邻点 drift 差绝对值均值（曲线起伏剧烈程度）
        volatility = (sum(abs(ys[i] - ys[i - 1]) for i in range(1, n)) / (n - 1)) if n >= 2 else 0.0
        curves[char] = {
            "points": pts,
            "n": n,
            "mean": round(mean, 3),
            "last": round(ys[-1], 3) if ys else 0.0,
            "slope": round(slope, 4),
            "volatility": round(volatility, 3),
        }
    return curves


def build_d5_findings(curves: dict, band: dict) -> list:
    """对每角色曲线产 advisory 提示（连续偏离度 · 全 advisory · 非 hard_gate）。

    只在「曲线有 ≥3 点」时判趋势（避免 2 点的噪声趋势）；触发条件二选一：
      · 持续走偏：slope ≥ band.warn_slope 且 mean 已偏高（≥ 0.5 * warn_mean）
      · 整体高位：mean ≥ band.warn_mean
    severity 恒 advisory。
    """
    findings = []
    warn_mean = band.get("warn_mean", _D5_GENERIC_BAND["warn_mean"])
    warn_slope = band.get("warn_slope", _D5_GENERIC_BAND["warn_slope"])
    for char, cv in curves.items():
        n = cv.get("n", 0)
        if n < 3:
            continue
        mean = cv.get("mean", 0.0)
        slope = cv.get("slope", 0.0)
        last = cv.get("last", 0.0)
        rising = slope >= warn_slope and mean >= 0.5 * warn_mean
        elevated = mean >= warn_mean
        if not (rising or elevated):
            continue
        reason = []
        if rising:
            reason.append(f"曲线持续上行 slope={slope}（≥带 {warn_slope}）")
        if elevated:
            reason.append(f"整体偏离均值 {mean}（≥带 {warn_mean}）")
        findings.append({
            "severity": "advisory",   # D5 恒 advisory（北极星⑤ · 绝不 hard_gate）
            "code": "PERSONA_DRIFT_CURVE_TREND",
            "character": char,
            "metric": {
                "n_points": n, "mean": mean, "last": last,
                "slope": slope, "volatility": cv.get("volatility", 0.0),
                "band": {"warn_mean": warn_mean, "warn_slope": warn_slope,
                         "source": band.get("_source", "通用")},
            },
            "curve": cv.get("points", []),
            "message": f"角色「{char}」persona 偏离度曲线{'、'.join(reason)}",
            "suggestion": (
                f"{char} 跨 {n} 个章点的 voice 偏离呈{'上行趋势' if rising else '高位'}"
                f"（基线源={band.get('_source','通用')}）→ 可裁量是否人设系统性走偏；"
                f"若为作者档允许的复杂多面演变则豁免（advisory 仅供参考，不硬锁）"
            ),
        })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=5)
    args = ap.parse_args()

    project_root = Path(args.project)

    per_chapter = {}
    chapters: list[int] = []

    # ===== 2026-05-29 cluster 化分支：账本有 persona_drift → 取预算 drift，跳过 embedding 重算 =====
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster 的章」
    if csr.is_cluster_mode() and csr.ledger_has_field(project_root, "persona_drift"):
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        for ch, rec in recs:
            drift_map = rec.get("persona_drift") or {}
            if not isinstance(drift_map, dict):
                continue
            per_chapter.setdefault(ch, [])
            for char, drift in drift_map.items():
                if not isinstance(drift, (int, float)):
                    continue
                per_chapter[ch].append({
                    "character": char,
                    "drift": drift,
                    "metric": {"character": char, "drift": drift, "source": "ledger"},
                })
            chapters.append(ch)
        chapters = sorted(set(chapters))
        if not chapters:
            print("[SKIP] cluster 账本无 persona_drift 记录")
            sys.exit(0)
        findings = _build_findings(per_chapter)
    else:
        # ===== 原逐章磁盘逻辑（非 cluster 模式 / 账本缺字段 → 零回归）=====
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

        for ch in chapters:
            per_chapter[ch] = []
            for char in characters:
                result = embedding_store.compute_character_drift(project_root, char, ch)
                if "error" in result:
                    continue
                drift = result.get("drift")
                if drift is None:
                    continue
                per_chapter[ch].append({"character": char, "drift": drift, "metric": result})
        findings = _build_findings(per_chapter)

    # 输出 per_chapter 时剥离内部辅助字段 metric（保持原磁盘分支结构 [{character, drift}]）
    per_chapter_out = {
        ch: [{"character": e["character"], "drift": e["drift"]} for e in entries]
        for ch, entries in per_chapter.items()
    }

    # ===== D5 人设漂移曲线（连续偏离度 · 非 0/1 · 影子并行）=====
    # off → 完全不算（纯旧行为）。shadow/active → 在同一份 per_chapter 上叠曲线层。
    d5_curves: dict = {}
    d5_band: dict = {}
    d5_findings: list = []
    if PERSONA_D5_MODE != "off":
        d5_band = compute_d5_author_band(project_root)             # 作者档第一权威标定容忍带
        d5_curves = build_d5_curves(per_chapter, project_root)     # 复用现有逐章 drift
        d5_findings = build_d5_findings(d5_curves, d5_band)         # 全 advisory
        # active 模式：D5 advisory 升顶层 findings（仍 advisory · 绝不进 hard_gate）。
        # shadow 默认：只挂 report，不进顶层、不改 exit code（零回归）。
        if PERSONA_D5_MODE == "active":
            findings = list(findings) + list(d5_findings)

    # 输出
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "persona_drift",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "per_chapter": per_chapter_out,
        "findings": findings,
        "summary": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "total": len(findings),
        },
        # D5 曲线层（始终挂报告便于复盘；active 时其 finding 已同步进顶层 findings）
        "d5_persona_drift_curve": {
            "mode": PERSONA_D5_MODE,
            "gate_level": "advisory",   # D5 恒 advisory（北极星⑤）
            "band": d5_band,
            "curves": d5_curves,
        },
        "d5_shadow_findings": d5_findings,
    }
    out_path = out_dir / f"persona_drift_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[persona_drift_scan] 扫描 ch{chapters}: {len(findings)} 项")
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f['message']}")
    if PERSONA_D5_MODE != "off":
        print(f"[D5 曲线] mode={PERSONA_D5_MODE} 角色 {len(d5_curves)} 条曲线 · "
              f"{len(d5_findings)} 项 advisory（基线源={d5_band.get('_source','通用')}）")
    print(f"报告: {out_path}")

    # 2026-05-29 复审修复 [L6/SC-2]：warning 级发现统一 exit 2、advisory 统一 exit 1（旧版 warning 误用 exit 1）。
    # D5 shadow 默认不进 findings → 不影响退出码（默认行为零回归）；active 时其 advisory 走下面 exit 1。
    if any(f["severity"] == "warning" for f in findings):
        sys.exit(2)
    if any(f["severity"] == "advisory" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
