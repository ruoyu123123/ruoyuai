#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motif_recurrence_ledger.py — 跨 cluster 母题循环账本 (advisory · cross-cluster · 2026-06-20 R8 W4 L20)

【缺口】R8 联网调研合并 (阮芳水浒哨棒 18 次 leitmotif + arXiv:2503.07977 leitmotif +
arXiv:2510.18561 TUNa folktale motif + Tim Weed Image Systems)：经典中文文学/英美爆款
共性=母题反复出现 4-20 次铺线 (草蛇灰线)·LLM 默认产「一次性意象/物件/口头禅」=
零循环·读者无可贯连节奏。本 aggregator 补：

  五类 motif (确定性词典 + 作者档可扩)：
    ① 物件   props          (兵器/信物/书/灯/茶具/钥匙…)
    ② 意象   imagery        (月/风/雪/血/镜/影/灯火…)
    ③ 口头禅 catchphrase    (作者档 character_voice.catchphrases / NPC voice_pack)
    ④ 感官印记 sensory_mark (嗅:腥/檀/酒;听:钟声/雨声;触:凉/湿)
    ⑤ 地名   places         (具体地名·从世界观/章节抽)

  五态 status：
    new_seed       首次出现 (N=1·首块或最新 cluster 出现)
    recurring      已重复 ≥3 次·均匀分布
    dormant        N≥2 但末 ≥3 cluster 全缺席 → 草蛇灰线断流
    over_saturated N>7 且 cluster 覆盖率>80% → 用得过密
    payoff_due     dormant 且 cluster_summary 里有 promise_payoff 标记 → 该回收

  指标：
    Gini 系数 over 首现/末现 cluster 间距 (集中/分散度)
    N (累计出现次数) 直方图
    R (recurrence span = last - first cluster index) 直方图

  输出：
    _数据库/motif_ledger.json (五类 motif 状态账本·持久化)
    _数据库/.cross_chapter_scan/motif_advisory_snapshot.json (供 build_manifest 注入下个 cluster)

【北极星 ⑤ 顾问非法官】母题反复是工艺 advisory · code MOTIF_DORMANT / MOTIF_OVER_SATURATED
/ MOTIF_PAYOFF_DUE 绝不进 audit_hub.HARD_GATE_CODES · env MOTIF_RECURRENCE_MODE:
off / shadow (默认·只记不判) / active。

用法：python motif_recurrence_ledger.py <project> [--last-n N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402

CODE_DORMANT = "MOTIF_DORMANT"
CODE_OVER_SATURATED = "MOTIF_OVER_SATURATED"
CODE_PAYOFF_DUE = "MOTIF_PAYOFF_DUE"
CODE_SEED_TOO_MANY = "MOTIF_SEED_PROLIFERATION"

# 五类 motif 词典 (高确定性·宁可漏报)
MOTIF_DICTIONARIES: dict = {
    "props": re.compile(
        r"(玉佩|玉珮|长剑|短刀|银钗|发簪|铜镜|油灯|香炉|茶盏|"
        r"信物|信件|遗物|铁匣|木匣|令牌|腰牌|铜钥|残卷|手稿)"
    ),
    "imagery": re.compile(
        r"(月光|月色|寒风|北风|雪花|风雪|血迹|血色|镜影|倒影|"
        r"灯火|烛火|薄雾|浓雾|乌云|残月|斜阳|残阳|落日|余晖)"
    ),
    "sensory_mark": re.compile(
        r"(檀香|沉香|血腥|铁锈味|霉味|酒香|雨腥|海腥|青草味|"
        r"钟声|风铃|涛声|呜咽声|低吟|冷汗|颤栗|刺骨|湿冷)"
    ),
    # places 与 catchphrases 由作者档动态扩 (此处保留入口)
    "places": re.compile(r""),       # 占位·由 _build_places_pattern 动态构建
    "catchphrase": re.compile(r""),  # 占位·由 _build_catchphrase_pattern 动态构建
}

# 阈值
RECURRING_MIN_COUNT = 3
DORMANT_ABSENCE_CLUSTERS = 3
OVER_SATURATED_COUNT = 7
OVER_SATURATED_COVERAGE = 0.80
SEED_PROLIFERATION_FLOOR = 6   # 同 cluster 内 N=1 motif 超过 N 个 → 种子过多


def _mode() -> str:
    m = (os.environ.get("MOTIF_RECURRENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_author_extra_motifs(project_root: Path) -> dict:
    """读作者档 author_motif_signature (扩 places / catchphrase 词典)。无 → {}。"""
    p = project_root / "_数据库" / "作者风格.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    sig = obj.get("author_motif_signature") or {}
    if not isinstance(sig, dict):
        return {}
    out = {}
    for cat in ("places", "catchphrase", "props", "imagery", "sensory_mark"):
        v = sig.get(cat) or []
        if isinstance(v, list):
            out[cat] = [str(x) for x in v if isinstance(x, str) and x.strip()]
    return out


def _build_extra_pattern(words: list) -> re.Pattern | None:
    """从词列表构 OR 正则·escape 元字符。"""
    words = [w.strip() for w in words if isinstance(w, str) and w.strip()]
    if not words:
        return None
    parts = [re.escape(w) for w in words]
    return re.compile("(" + "|".join(parts) + ")")


def _read_cluster_text(project_root: Path, cluster: dict) -> str:
    """读 cluster 的草稿/章节文本拼接。

    优先级：cluster.scope_summary + scenes_brief 描述 (账本里有)·退章节文件。
    """
    parts = []
    # 1) 账本里的 scope_summary / 章节摘要
    scope = cluster.get("scope_summary") or ""
    if isinstance(scope, str) and scope:
        parts.append(scope)
    chapters = cluster.get("chapters") or {}
    if isinstance(chapters, dict):
        for ch_key, rec in chapters.items():
            if not isinstance(rec, dict):
                continue
            for fld in ("summary", "scene_summary", "title"):
                v = rec.get(fld)
                if isinstance(v, str) and v:
                    parts.append(v)
    # 2) 退章节文件 (若账本字段空)
    if not parts:
        cluster_id = cluster.get("cluster_id") or ""
        draft_dir = project_root / "章节" / f"{cluster_id}_draft"
        if draft_dir.exists():
            for f in sorted(draft_dir.glob("*.txt")):
                try:
                    parts.append(f.read_text(encoding="utf-8"))
                except OSError:
                    pass
    return "\n".join(parts)


def scan_motifs_in_text(text: str, dictionaries: dict) -> dict:
    """扫文本中每类 motif 命中。返回 {category: {term: count}}。"""
    out: dict = {}
    for cat, rx in dictionaries.items():
        if rx is None:
            continue
        hits: dict = {}
        for m in rx.finditer(text):
            term = m.group(0)
            hits[term] = hits.get(term, 0) + 1
        out[cat] = hits
    return out


def gini_coefficient(values: list) -> float:
    """Gini 系数 (0=平等·1=集中)·numpy-free。"""
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


def build_ledger(project_root: Path, clusters: list) -> dict:
    """从 clusters 序列构建母题账本。

    schema:
      motifs: { motif_term: {
        category, total_count, cluster_appearances:[cluster_id...],
        first_cluster, last_cluster, status,
        cluster_idx_first, cluster_idx_last
      } }
      gini_by_category: { category: gini }
      histograms: { N: {...}, R: {...} }
    """
    # 动态扩词典
    extra = _read_author_extra_motifs(project_root)
    dictionaries = dict(MOTIF_DICTIONARIES)
    for cat in ("places", "catchphrase"):
        pat = _build_extra_pattern(extra.get(cat, []))
        if pat is not None:
            dictionaries[cat] = pat
        else:
            dictionaries[cat] = None  # 没扩 → 跳过该类
    # 扩展通用类 (作者额外加)
    for cat in ("props", "imagery", "sensory_mark"):
        extra_words = extra.get(cat, [])
        if extra_words:
            base_pat = MOTIF_DICTIONARIES[cat].pattern
            extra_pat = "|".join(re.escape(w) for w in extra_words)
            dictionaries[cat] = re.compile(f"({base_pat[1:-1]}|{extra_pat})")

    motifs: dict = {}
    for idx, c in enumerate(clusters):
        cid = c.get("cluster_id") or f"_idx_{idx}"
        text = _read_cluster_text(project_root, c)
        if not text:
            continue
        per_cat = scan_motifs_in_text(text, dictionaries)
        for cat, hits in per_cat.items():
            for term, cnt in hits.items():
                key = f"{cat}::{term}"
                if key not in motifs:
                    motifs[key] = {
                        "category": cat,
                        "term": term,
                        "total_count": 0,
                        "cluster_appearances": [],
                        "cluster_idx_first": idx,
                        "cluster_idx_last": idx,
                        "first_cluster": cid,
                        "last_cluster": cid,
                    }
                motifs[key]["total_count"] += cnt
                if cid not in motifs[key]["cluster_appearances"]:
                    motifs[key]["cluster_appearances"].append(cid)
                motifs[key]["cluster_idx_last"] = idx
                motifs[key]["last_cluster"] = cid

    n_clusters = len(clusters)
    # 状态分类
    for m in motifs.values():
        appearances = m["cluster_appearances"]
        n_count = m["total_count"]
        coverage = len(appearances) / max(1, n_clusters)
        gap_from_last = (n_clusters - 1) - m["cluster_idx_last"]

        if n_count >= OVER_SATURATED_COUNT and coverage > OVER_SATURATED_COVERAGE:
            status = "over_saturated"
        elif gap_from_last >= DORMANT_ABSENCE_CLUSTERS and n_count >= 2:
            status = "dormant"
        elif n_count >= RECURRING_MIN_COUNT:
            status = "recurring"
        elif n_count == 1:
            status = "new_seed"
        else:
            status = "recurring"  # 2-(N-1) → 进展中
        m["status"] = status

    # Gini by category (基于各 motif 的 total_count 分布)
    gini_by_cat: dict = {}
    for cat in {m["category"] for m in motifs.values()}:
        counts = [m["total_count"] for m in motifs.values() if m["category"] == cat]
        gini_by_cat[cat] = round(gini_coefficient(counts), 3)

    # 直方图
    n_hist: dict = {}
    r_hist: dict = {}
    for m in motifs.values():
        n_bin = f"N={m['total_count']}" if m['total_count'] < 5 else "N>=5"
        n_hist[n_bin] = n_hist.get(n_bin, 0) + 1
        r = m["cluster_idx_last"] - m["cluster_idx_first"]
        r_bin = f"R={r}" if r < 4 else "R>=4"
        r_hist[r_bin] = r_hist.get(r_bin, 0) + 1

    return {
        "motifs": motifs,
        "gini_by_category": gini_by_cat,
        "histograms": {"N": n_hist, "R": r_hist},
        "n_clusters": n_clusters,
    }


def emit_findings(ledger: dict, project_root: Path) -> list:
    """根据账本五态 → advisory finding 列表。"""
    findings = []
    motifs = ledger["motifs"]
    n_clusters = ledger["n_clusters"]

    # 检查 promise_payoff (从 cluster_summary 读 foreshadowing/secret)
    payoff_motifs = set()
    p = project_root / "_数据库" / "故事块摘要.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for c in data.get("clusters", []):
                if not isinstance(c, dict):
                    continue
                for fld in ("foreshadowing_planted", "promises", "secrets"):
                    items = c.get(fld) or []
                    if isinstance(items, list):
                        for it in items:
                            if isinstance(it, dict):
                                tag = it.get("target") or it.get("name") or it.get("symbol")
                                if isinstance(tag, str):
                                    payoff_motifs.add(tag)
                            elif isinstance(it, str):
                                payoff_motifs.add(it)
        except (OSError, json.JSONDecodeError):
            pass

    dormant_list = []
    over_sat_list = []
    payoff_due_list = []
    new_seed_count = 0
    for key, m in motifs.items():
        if m["status"] == "dormant":
            dormant_list.append(m)
            # 若 dormant 且 motif term 在 payoff 标记里 → payoff_due
            if m["term"] in payoff_motifs:
                payoff_due_list.append(m)
        elif m["status"] == "over_saturated":
            over_sat_list.append(m)
        elif m["status"] == "new_seed":
            new_seed_count += 1

    if dormant_list and n_clusters >= 3:
        findings.append({
            "severity": "advisory",
            "code": CODE_DORMANT,
            "count": len(dormant_list),
            "samples": [{"term": m["term"], "category": m["category"],
                         "last_cluster": m["last_cluster"], "total": m["total_count"]}
                        for m in dormant_list[:6]],
            "suggestion": (
                f"{len(dormant_list)} 个母题进入 dormant 状态 (末 ≥{DORMANT_ABSENCE_CLUSTERS} cluster"
                f"未出现)·草蛇灰线断流·建议下个 cluster 复现或显式放弃"),
        })

    if over_sat_list:
        findings.append({
            "severity": "advisory",
            "code": CODE_OVER_SATURATED,
            "count": len(over_sat_list),
            "samples": [{"term": m["term"], "category": m["category"],
                         "total": m["total_count"], "coverage": round(
                             len(m["cluster_appearances"]) / max(1, n_clusters), 2)}
                        for m in over_sat_list[:6]],
            "suggestion": (
                f"{len(over_sat_list)} 个母题 over_saturated (N>{OVER_SATURATED_COUNT} 且覆盖率>"
                f"{OVER_SATURATED_COVERAGE:.0%})·用得过密失新鲜·建议变形 (角度/感官切换)"),
        })

    if payoff_due_list:
        findings.append({
            "severity": "advisory",
            "code": CODE_PAYOFF_DUE,
            "count": len(payoff_due_list),
            "samples": [{"term": m["term"], "category": m["category"],
                         "last_cluster": m["last_cluster"]}
                        for m in payoff_due_list[:6]],
            "suggestion": (
                f"{len(payoff_due_list)} 个母题已被 promise/foreshadowing 标记且 dormant"
                f"·建议下个 cluster 回收"),
        })

    if new_seed_count >= SEED_PROLIFERATION_FLOOR and n_clusters >= 3:
        findings.append({
            "severity": "advisory",
            "code": CODE_SEED_TOO_MANY,
            "count": new_seed_count,
            "floor": SEED_PROLIFERATION_FLOOR,
            "suggestion": (
                f"{new_seed_count} 个 motif 仍 new_seed (N=1)·种子过多无回响·"
                f"建议精选 4-20 次循环铺线 (草蛇灰线)"),
        })

    return findings


def main():
    ap = argparse.ArgumentParser(description="跨 cluster 母题循环账本 (advisory · cross-cluster)")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=None, help="只取末 N cluster")
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project).resolve()
    if mode == "off":
        print("[OFF] MOTIF_RECURRENCE_MODE=off")
        sys.exit(0)

    clusters = csr.get_clusters(project_root, last_n=args.last_n)
    if not clusters:
        print("[SKIP] 账本无 cluster 记录")
        sys.exit(0)
    if len(clusters) < 2:
        print(f"[SKIP] cluster 数太少 ({len(clusters)} < 2)")
        sys.exit(0)

    ledger = build_ledger(project_root, clusters)
    findings = emit_findings(ledger, project_root)

    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    # 🔴 2026-06-27 P2-12: motif_ledger.json 迁 .cross_chapter_scan/（与 advisory snapshot 同目录·根目录干净·scaffold KNOWN_EXTRAS 不必再网开一面）。
    snap_dir = db / ".cross_chapter_scan"
    snap_dir.mkdir(parents=True, exist_ok=True)
    # 持久化 motif_ledger.json (完整账本)
    ledger_path = snap_dir / "motif_ledger.json"
    try:
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[WARN] motif_ledger.json 写失败: {e}", file=sys.stderr)
    # 兼容旧位置：若 _数据库/motif_ledger.json 残留则尝试静默清理（不阻塞）
    _legacy = db / "motif_ledger.json"
    try:
        if _legacy.exists():
            _legacy.unlink()
    except OSError:
        pass
    snapshot = {
        "scan_type": "motif_recurrence_ledger",
        "scan_ts": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "mode": mode,
        "n_clusters": ledger["n_clusters"],
        "gini_by_category": ledger["gini_by_category"],
        "histograms": ledger["histograms"],
        "findings": findings,
        "advisory_codes": sorted({f["code"] for f in findings}),
        "dormant_motifs": [
            {"term": m["term"], "category": m["category"], "last_cluster": m["last_cluster"]}
            for m in ledger["motifs"].values() if m["status"] == "dormant"
        ][:10],
        "recurring_unchanged_motifs": [
            {"term": m["term"], "category": m["category"], "total": m["total_count"]}
            for m in ledger["motifs"].values() if m["status"] == "recurring"
        ][:10],
        "over_saturated_motifs": [
            {"term": m["term"], "category": m["category"], "total": m["total_count"]}
            for m in ledger["motifs"].values() if m["status"] == "over_saturated"
        ][:10],
    }
    snap_path = snap_dir / "motif_advisory_snapshot.json"
    try:
        snap_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[WARN] motif_advisory_snapshot.json 写失败: {e}", file=sys.stderr)

    print(f"[motif_recurrence_ledger] n_clusters={ledger['n_clusters']} "
          f"motifs={len(ledger['motifs'])} findings={len(findings)}")
    for f in findings[:4]:
        print(f"  [{f['severity'].upper()}] {f['code']}: {f.get('suggestion', '')[:80]}")
    print(f"账本: {ledger_path}")
    print(f"snapshot: {snap_path}")

    if mode == "shadow":
        for f in findings:
            print(f"[SHADOW] motif_recurrence_ledger: {f['code']} — 不上报", file=sys.stderr)
        sys.exit(0)
    if any(f["severity"] == "warning" for f in findings):
        sys.exit(2)
    if findings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
