#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_cluster_style_drift_scanner.py — 跨 cluster 长程作者文风漂移哨兵（cross-cluster · advisory · 2026-05-31）

【治什么】第 2 轮·D 级长程退化根因。现有 cross_scene_voice_drift（cluster 内同角色声音）/
cross_cluster_persona_drift（角色人设余弦）都不盯一个独立机制——**作者文风随篇幅增长逐步退化
成「通用 LLM 腔」**：句长节奏被摊平、虚词指纹/标点节奏/字组笔迹离作者越来越远。
ConStory-Bench 实证：风格漂移与事实/时序一致性近零相关（独立信号），且 40-60% 中段最易聚集。

【怎么测】纯 Python · 零 GPU · 复用现有 SFS（style_evaluator）：
  · 对每个已写 cluster 草稿，算两类**去题材风格相似度**（0-100，越高越贴）：
      ① vs 作者原文参考池（resolve_author_pool · style_similarity_scanner 同路定位）；
      ② vs 已写的前 K 个 cluster（自漂移：本书内部文风是否在跑偏）。
  · 把 ①「vs 作者参考」沿 cluster 序号画成曲线 → 线性回归斜率（每 cluster 相似度变化率）。
    斜率显著为负（单调远离作者参考）= 全局文风退化 → advisory。
  · **位置加权**：cluster 处于全书 40-60% 中段时收紧阈值（ConStory-Bench 中段聚集 → 早提示）。
  · 熵早警（可选）：gen-model 返回 logprobs 时可叠加熵升趋势；拿不到则纯 SFS 距离（不强依赖）。

【边界 · 北极星纪律】
  · ⑤ 顾问非法官：code LONGRANGE_STYLE_DRIFT **永远 advisory · 绝不进 audit_hub.HARD_GATE_CODES**·
    绝不强锁风格（只提示「可能在退化」，不替模型决定怎么写）。
  · ② cluster 为单位：曲线点 = cluster（非章），距离/斜率按 cluster 视野解读。
  · ⑥ 不臃肿：复用 style_evaluator 的 SFS + style_similarity_scanner 的作者池定位 +
    volume_arc_drift_scanner 的 cross-cluster aggregator 同位框架（挂 run_cross_cluster_aggregates）·
    不新起 embedding/系统。
  · 作者档第一权威：作者原文池是「贴不贴作者」的唯一参照系；缺池 → 退化为「自漂移」单轨（不臆造）。
  · 影子并行（共同纪律 2）env LONGRANGE_DRIFT_MODE：
      active（默认，2026-05-31 放量）：把漂移升 advisory issue 进顶层（仍 advisory · 绝不 hard_gate）。
      shadow：算全量曲线挂 report · 顶层 issues=[] · 不改 exit code（零回归）。
      off：完全跳过（连 SFS 都不算）。

用法：python cross_cluster_style_drift_scanner.py <project> [--last-n N] [--author-pool <dir>]
退出码：0=干净/数据不足/shadow · 1=active 命中 advisory 漂移 · 2=fatal（仅 CLI 参数错）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 复用现有框架（北极星⑥ 不新起并行系统）——只读调用，不改它们任何一行。
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import style_evaluator as se          # noqa: E402  SFS 距离（compute_charngram_sfs / compute_style_only_sfs）
import style_similarity_scanner as sss  # noqa: E402  作者原文池定位（resolve_author_pool / _list_author_chapters）

ISSUE_CODE = "LONGRANGE_STYLE_DRIFT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 漂移触发线（advisory 提示线 · 非判决线 · 北极星⑤）。保守宽松：宁漏报不误伤作者长程演变。
_DRIFT_SLOPE_WARN = -2.0     # vs作者参考相似度的回归斜率 ≤ 此（每 cluster 掉 ≥2 分）= 单调退化
_MID_SLOPE_WARN = -1.2       # 40-60% 中段收紧（ConStory-Bench 中段聚集 → 更早提示）
_LOW_SIM_FLOOR = 55.0        # 末点 vs 作者相似度低于此 = 已退化到低位（绝对低位补充判据）
_MID_POS_LO, _MID_POS_HI = 0.40, 0.60   # 中段窗（位置加权区间）
_MIN_CURVE_POINTS = 3        # 曲线 < 3 点不判趋势（避免 2 点噪声趋势）
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


# ════════════════════════════════════════════════════════════════
# 影子并行模式
# ════════════════════════════════════════════════════════════════

def _mode() -> str:
    """LONGRANGE_DRIFT_MODE：默认 active（2026-05-31 放量 · 跨 cluster 长程漂移升 advisory issue ·
    实证真作者跨章距离曲线斜率平缓不误报退化 · code LONGRANGE_STYLE_DRIFT 永不进 HARD_GATE_CODES）/
    shadow / off。非法值回退 active。"""
    m = (os.environ.get("LONGRANGE_DRIFT_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


# ════════════════════════════════════════════════════════════════
# 纯函数：回归斜率 / SFS 风格距离（可单测 · 无 IO）
# ════════════════════════════════════════════════════════════════

def _linreg_slope(xs: list, ys: list) -> float:
    """最小二乘斜率（每 cluster 相似度变化率 · 纯 stdlib）。点 < 2 → 0.0。"""
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


def style_similarity(ref_text: str, gen_text: str) -> float:
    """两段文本的**去题材**作者文风相似度（0-100 · 越高越贴）。

    复用 style_evaluator 的两个 SFS：
      · compute_style_only_sfs —— 虚词指纹/标点节奏/句长节奏/去题材 POS（题材无关风格核心）；
      · compute_charngram_sfs —— 字符 3-gram + 词 unigram 字组笔迹（最难复刻的作者特征）。
    两者等权平均 → 单一可比相似度。任一不可用（环境/异常）则取可用者；都不可用 → 0.0。
    纯 stdlib 返回 float（不漏 numpy 类型 · JSON/测试可读）。"""
    vals: list[float] = []
    try:
        s1 = se.compute_style_only_sfs(ref_text, gen_text)
        vals.append(float(s1["style_only_sfs"]))
    except Exception:
        pass
    try:
        s2 = se.compute_charngram_sfs(ref_text, gen_text)
        vals.append(float(s2["charngram_sfs"]))
    except Exception:
        pass
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 2)


def position_weight_band(position_ratio: float) -> dict:
    """位置加权：cluster 处于全书 40-60% 中段时收紧斜率阈值（ConStory-Bench 中段聚集）。

    position_ratio = 该 cluster 序号 / 总 cluster 数（0~1）。
    返回 {"slope_warn": float, "mid": bool}——中段用更宽容（更接近 0）的负斜率线 = 更早提示。"""
    mid = _MID_POS_LO <= position_ratio <= _MID_POS_HI
    return {"slope_warn": _MID_SLOPE_WARN if mid else _DRIFT_SLOPE_WARN, "mid": mid}


def build_drift_curve(cluster_sims: list[dict]) -> dict:
    """把每 cluster「vs 作者参考相似度」拍平成曲线 + 连续指标（非 0/1）。

    入参 cluster_sims = [{"cluster_id","idx","sim_vs_author","sim_vs_prior"(可None)}, ...]（按 idx 升序）。
    返回 {"points":[...], "n", "mean", "last", "slope", "min_point"}。
    只在含 sim_vs_author 的点上算曲线（缺作者池的点不进趋势）。"""
    pts = [p for p in cluster_sims if isinstance(p.get("sim_vs_author"), (int, float))]
    pts = sorted(pts, key=lambda p: p["idx"])
    ys = [float(p["sim_vs_author"]) for p in pts]
    xs = [int(p["idx"]) for p in pts]
    n = len(ys)
    if n == 0:
        return {"points": [], "n": 0, "mean": 0.0, "last": 0.0, "slope": 0.0, "min_point": None}
    slope = _linreg_slope(xs, ys)
    min_i = min(range(n), key=lambda i: ys[i])
    return {
        "points": pts,
        "n": n,
        "mean": round(sum(ys) / n, 2),
        "last": round(ys[-1], 2),
        "slope": round(slope, 3),
        "min_point": {"cluster_id": pts[min_i].get("cluster_id"), "sim": round(ys[min_i], 2)},
    }


def build_drift_findings(curve: dict, total_clusters: int) -> list[dict]:
    """对漂移曲线产 advisory 提示（连续退化趋势 + 中段加权 + 绝对低位 · 全 advisory）。

    触发（任一）：
      · 单调退化：回归斜率 ≤ 位置加权斜率线（末点所在位置决定收紧与否）；
      · 绝对低位：末点相似度 < _LOW_SIM_FLOOR（已退化到低位 · 不论趋势）。
    severity 恒 advisory（北极星⑤）。曲线 < _MIN_CURVE_POINTS 点 → 不判（噪声）。"""
    findings: list[dict] = []
    n = curve.get("n", 0)
    if n < _MIN_CURVE_POINTS:
        return findings
    pts = curve["points"]
    last_idx = int(pts[-1]["idx"])
    pos_ratio = (last_idx / total_clusters) if total_clusters else 1.0
    band = position_weight_band(pos_ratio)
    slope = curve["slope"]
    last = curve["last"]

    declining = slope <= band["slope_warn"]
    low = last < _LOW_SIM_FLOOR
    if not (declining or low):
        return findings

    reasons = []
    if declining:
        reasons.append(
            f"作者文风相似度曲线单调下行 slope={slope}（≤{'中段收紧线' if band['mid'] else '常规线'} "
            f"{band['slope_warn']}）"
        )
    if low:
        reasons.append(f"末 cluster 相似度 {last} 已低于低位线 {_LOW_SIM_FLOOR}")
    findings.append({
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 恒 advisory · 双保险（北极星⑤）
        "severity": "advisory",
        "metric": {
            "n_points": n,
            "mean": curve["mean"],
            "last": last,
            "slope": slope,
            "position_ratio": round(pos_ratio, 3),
            "mid_section": band["mid"],
            "slope_warn_applied": band["slope_warn"],
            "min_point": curve.get("min_point"),
        },
        "curve": [{"cluster_id": p.get("cluster_id"), "idx": p["idx"],
                   "sim_vs_author": p["sim_vs_author"],
                   "sim_vs_prior": p.get("sim_vs_prior")} for p in pts],
        "message": ("⚠️ 跨 cluster 长程作者文风漂移：" + "；".join(reasons)
                    + "（疑似随篇幅退化成通用腔 · 顾问提示不限定写法）"),
        "suggestion": ("后续 cluster 写作时把「离作者参考最近的本书已写片段」当 rolling anchor 重新锚定"
                       "（build_manifest 已注入 rolling_style_anchor）；"
                       "若为作者档允许的长程文风演变可豁免（advisory 仅供参考 · 不硬锁）"),
    })
    return findings


# ════════════════════════════════════════════════════════════════
# IO：定位作者参考片段 / 已写 cluster 草稿
# ════════════════════════════════════════════════════════════════

def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def author_reference_text(project_root: Path, author_pool: "Path | None" = None,
                          max_chapters: int = 6) -> "str | None":
    """作者原文参考文本（多章拼接 · 复用 style_similarity_scanner 的作者池定位/列章）。

    缺池 → None（顾问制：不报错 · 调用方退化为自漂移单轨）。"""
    pool = author_pool or sss.resolve_author_pool(project_root)
    if pool is None:
        return None
    chapters = sss._list_author_chapters(pool, limit=max_chapters)
    parts = []
    for f in chapters:
        try:
            t = _strip_changes(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if sss._cjk_count(t) >= 200:
            parts.append(t)
    return "\n\n".join(parts) if parts else None


def _cluster_draft_path(project_root: Path, cluster_id: str) -> "Path | None":
    """已写 cluster 草稿磁盘路径：章节/cluster_<key>_draft/cluster_<key>_draft.txt。"""
    key = str(cluster_id)
    cand = project_root / "章节" / f"{key}_draft" / f"{key}_draft.txt"
    if cand.exists():
        return cand
    # 兜底：扁平放置
    flat = project_root / "章节" / f"{key}_draft.txt"
    return flat if flat.exists() else None


def collect_written_cluster_texts(project_root: Path, last_n: "int | None" = None) -> list[dict]:
    """收集已写 cluster 的草稿正文（按 cluster 序号升序）。

    优先用 cluster_summary_reader（账本权威顺序）；账本不可用则磁盘 glob 兜底。
    返回 [{"cluster_id","idx","text"}, ...]（仅含正文 CJK ≥200 的 cluster）。"""
    out: list[dict] = []
    clusters: list[dict] = []
    try:
        import cluster_summary_reader as csr  # type: ignore
        clusters = csr.get_clusters(project_root, last_n=last_n)
    except Exception:
        clusters = []

    seen = set()
    idx = 0
    if clusters:
        for c in clusters:
            cid = str(c.get("cluster_id") or "")
            if not cid or cid in seen:
                continue
            p = _cluster_draft_path(project_root, cid)
            if not p:
                continue
            try:
                text = _strip_changes(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if sss._cjk_count(text) < 200:
                continue
            seen.add(cid)
            out.append({"cluster_id": cid, "idx": idx, "text": text})
            idx += 1
    else:
        # 磁盘兜底：扫 章节/cluster_*_draft 目录，按 cluster 号排序
        chap_dir = project_root / "章节"
        if chap_dir.is_dir():
            import re
            drafts = []
            for d in chap_dir.glob("cluster_*_draft"):
                m = re.search(r"cluster_(\d+)", d.name)
                if m:
                    drafts.append((int(m.group(1)), d.name.replace("_draft", "")))
            drafts.sort(key=lambda x: x[0])
            if last_n:
                drafts = drafts[-last_n:]
            for _num, cid in drafts:
                if cid in seen:
                    continue
                p = _cluster_draft_path(project_root, cid)
                if not p:
                    continue
                try:
                    text = _strip_changes(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if sss._cjk_count(text) < 200:
                    continue
                seen.add(cid)
                out.append({"cluster_id": cid, "idx": idx, "text": text})
                idx += 1
    return out


# ════════════════════════════════════════════════════════════════
# 核心 scan
# ════════════════════════════════════════════════════════════════

def scan(project_root: Path, last_n: "int | None" = None,
         author_pool: "Path | None" = None, prior_k: int = 2) -> dict:
    mode = _mode()
    report = {
        "schema_version": "1.0",
        "scanner": "cross_cluster_style_drift",
        "layer": "cross-cluster",
        "cluster_mode": True,
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 永久 advisory · 绝不进 HARD_GATE_CODES
        "issues": [],
        "drift_curve": None,
        "shadow_findings": [],
    }
    if mode == "off":
        report["_skip"] = "mode=off"
        return report

    written = collect_written_cluster_texts(project_root, last_n=last_n)
    if len(written) < _MIN_CURVE_POINTS:
        report["_skip"] = f"已写 cluster 数 {len(written)} < {_MIN_CURVE_POINTS}，长程趋势不适用（样本不足）"
        report["n_clusters"] = len(written)
        return report

    ref_text = author_reference_text(project_root, author_pool)
    report["author_pool_resolved"] = ref_text is not None
    total_clusters = len(written)

    cluster_sims: list[dict] = []
    for i, c in enumerate(written):
        entry: dict = {"cluster_id": c["cluster_id"], "idx": c["idx"]}
        # ① vs 作者参考池（核心：贴不贴作者）
        if ref_text is not None:
            entry["sim_vs_author"] = style_similarity(ref_text, c["text"])
        # ② vs 已写前 K 个 cluster（自漂移：本书内部文风是否在跑偏）
        priors = written[max(0, i - prior_k):i]
        if priors:
            prior_text = "\n\n".join(p["text"] for p in priors)
            entry["sim_vs_prior"] = style_similarity(prior_text, c["text"])
        cluster_sims.append(entry)

    report["cluster_similarities"] = cluster_sims

    # 缺作者池 → 退化为「自漂移」单轨（用 sim_vs_prior 顶替 sim_vs_author 画曲线 · 不臆造作者参照）
    if ref_text is None:
        for e in cluster_sims:
            if isinstance(e.get("sim_vs_prior"), (int, float)):
                e["sim_vs_author"] = e["sim_vs_prior"]   # 单轨：本书内部自相似当趋势锚
        report["_note"] = "未定位作者原文池 → 退化为本书内部自漂移单轨（sim_vs_prior 当趋势锚）"

    curve = build_drift_curve(cluster_sims)
    report["drift_curve"] = curve
    findings = build_drift_findings(curve, total_clusters)
    # 双保险：强制 advisory（北极星⑤）
    for f in findings:
        f["gate_level"] = "advisory"

    if mode == "active":
        report["issues"] = findings          # active：升顶层 issues（仍 advisory · 绝不 hard_gate）
    else:
        report["shadow_findings"] = findings  # shadow：只记不进顶层、不改 exit code（零回归）
        for f in findings:
            print(f"[longrange_drift:shadow] {f['message']}", file=sys.stderr)
    return report


def main():
    ap = argparse.ArgumentParser(description="跨 cluster 长程作者文风漂移哨兵（cross-cluster · advisory）")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=None,
                    help="只看最近 N 个 cluster（默认全看 · 长程趋势宜全看）")
    ap.add_argument("--author-pool", help="显式作者原文池目录（默认自动定位）")
    ap.add_argument("--prior-k", type=int, default=2, help="自漂移参照的前 K 个 cluster（默认 2）")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[SKIP] 无 _数据库: {project_root}", file=sys.stderr)
        sys.exit(0)
    pool = Path(args.author_pool).resolve() if args.author_pool else None
    report = scan(project_root, last_n=args.last_n, author_pool=pool, prior_k=args.prior_k)
    # 打印不含巨大正文的摘要
    summary = {k: v for k, v in report.items() if k != "cluster_similarities"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # advisory scanner：active 命中漂移才 exit 1（待裁决项）；shadow/off/数据不足 exit 0
    sys.exit(1 if report.get("issues") else 0)


if __name__ == "__main__":
    main()
