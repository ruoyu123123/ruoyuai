"""cross_cluster_narrative_debt_ledger_aggregate.py — 叙事债务账本（R7 Batch-D · 2026-06-20）

【缺口】R7 W2 联网调研：长篇叙事「债务」是结构骨——伏笔/秘密/承诺/悬念都是
作者向读者借的债，必须在某处偿还。AI 写作的系统性短板：
  · 借得快还得快（同 cluster 内开闭合 → 没债务感）
  · 借完忘还（后段累计未偿超载 → 烂尾感）
  · 前 30% 不借（开篇 hook 弱 → Book Mortgage 缺位）

本 aggregator 三档：
  ① BOOK 桶（全书 stock+flow）：累计 planted/paid · 健康债比 = open_debt / total_planted
  ② VOLUME 桶（每卷 stock+flow）：60% 后段 open_ratio 连续上升 → 烂尾风险 advisory
  ③ SCENE/CLUSTER 桶（每 cluster flow）：当 cluster 借出/偿还的债务流水

读账本 cluster_summary_reader（foreshadow_planted/paid · secrets_revealed） · 零 LLM · 确定性。
env NARRATIVE_DEBT_MODE：off / shadow（默认·只记不判·零回归）/ active。

输出 advisory：
  · DEBT_BOOK_MORTGAGE_ABSENT：前 30% cluster 累计 planted=0（开篇没借债）
  · DEBT_VOLUME_TAIL_RUNAWAY：当前卷 60%+ 区段 open_ratio 连续 2+ cluster 上升
  · DEBT_VOLUME_OVERSHOOT：当前卷末 open_debt 占该卷 planted > 60%（卷末烂尾）

snapshot 写 _数据库/.cross_chapter_scan/narrative_debt_ledger_*.json
供 build_manifest 注入 debt_ledger_snapshot（writer D7 用）。

退出码: 0 健康 / 1 advisory（默认 shadow 不上报） / 2 warning
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
# 🔴 2026-06-27 SYS-5 ②：cluster 摘要无 foreshadow 流水时回退伏笔表.json，需 normalize_cluster_id
# 把 setup_cluster 归一到与摘要 cluster_id 同形（防 "6"/"cluster_006" 比对漏匹配）。
try:
    import cluster_lookup  # noqa: E402
except Exception:  # pragma: no cover
    cluster_lookup = None

IS_CLUSTER_MODE = os.environ.get("CLUSTER_MODE") == "1"

# 三档 advisory code（绝不进 audit_hub.HARD_GATE_CODES）
CODE_BOOK_MORTGAGE = "DEBT_BOOK_MORTGAGE_ABSENT"
CODE_VOL_RUNAWAY = "DEBT_VOLUME_TAIL_RUNAWAY"
CODE_VOL_OVERSHOOT = "DEBT_VOLUME_OVERSHOOT"

# 触发阈值（保守 · 待金标准校准）
BOOK_MORTGAGE_HEAD_PCT = 0.30          # 前 30% cluster
VOL_TAIL_THRESHOLD_PCT = 0.60          # 卷 60% 后段
VOL_RUNAWAY_RISE_STREAK = 2            # 连续 N cluster open_ratio 上升
VOL_OVERSHOOT_OPEN_RATIO = 0.60        # 卷末 open / planted > 60%


def _mode() -> str:
    m = (os.environ.get("NARRATIVE_DEBT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _cluster_volume(c: dict) -> int:
    """从 cluster 字段抽 volume 号。优先 _me_volume / volume / metadata.volume，缺则归 0。"""
    for k in ("_me_volume", "volume", "vol"):
        v = c.get(k)
        if isinstance(v, int) and v > 0:
            return v
    meta = c.get("metadata") or {}
    if isinstance(meta, dict):
        v = meta.get("volume")
        if isinstance(v, int) and v > 0:
            return v
    return 0


def _norm_cid(cid) -> str:
    """归一 cluster_id（"6"/"cluster_006" → "cluster_006"）。cluster_lookup 不可用时原样返回。"""
    if cluster_lookup is not None:
        try:
            n = cluster_lookup.normalize_cluster_id(cid)
            if n:
                return n
        except Exception:
            pass
    return str(cid or "")


def _load_foreshadow_table_fallback(project_root: Path) -> dict:
    """🔴 2026-06-27 SYS-5 ②：读 伏笔表.json，按 setup_cluster 归集 planted/paid。

    返回 {normalized_cluster_id: {"planted": set[fid], "paid": set[fid]}}。
    · planted = 该 cluster 埋下的所有 promises/deadlines/pledges/secrets 的 id（authoring 即确定·可靠）。
    · paid = 其中 resolved/revealed 为真者（伏笔表是权威结算账·消除 cluster 摘要 foreshadow 流水缺失
      导致的恒 0 → 误报 DEBT_BOOK_MORTGAGE_ABSENT）。
    无表/损坏/空 → {}（调用方据此判定是否回退·零回归）。
    """
    db = project_root if project_root.name == "_数据库" else project_root / "_数据库"
    p = db / "伏笔表.json"
    try:
        if not p.is_file():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for bucket in ("promises", "deadlines", "pledges", "secrets"):
        for item in data.get(bucket, []) or []:
            if not isinstance(item, dict):
                continue
            fid = item.get("id") or item.get("fid")
            if not fid:
                continue
            cid = _norm_cid(item.get("setup_cluster") or item.get("cluster_id"))
            if not cid:
                continue
            entry = out.setdefault(cid, {"planted": set(), "paid": set()})
            entry["planted"].add(str(fid))
            if item.get("resolved") or item.get("revealed") or item.get("paid"):
                entry["paid"].add(str(fid))
    return out


def _cluster_planted_paid(c: dict, fb_index: dict | None = None) -> tuple[set, set]:
    """从 cluster 摘要取本块 planted/paid 集合（伏笔 + secrets 联合·secrets 视作 paid 的揭示）。

    🔴 SYS-5 ②：cluster 摘要既无 foreshadow_planted 也无 foreshadow_paid（流水未持久化）时，
    回退伏笔表.json 按 setup_cluster 归集的 planted/paid（fb_index）→ 消除 total_planted 恒 0。
    """
    planted = set()
    for fid in c.get("foreshadow_planted", []) or []:
        if isinstance(fid, str) and fid.strip():
            planted.add(fid.strip())
    paid = set()
    for fid in c.get("foreshadow_paid", []) or []:
        if isinstance(fid, str) and fid.strip():
            paid.add(fid.strip())
    # secrets_revealed = 一种 paid 形态（揭示秘密 = 还债）
    for sid in c.get("secrets_revealed", []) or []:
        if isinstance(sid, str) and sid.strip():
            paid.add(f"_secret:{sid.strip()}")
    # 🔴 SYS-5 ②：本 cluster 摘要零 foreshadow 流水 → 回退伏笔表（按 setup_cluster 归集）。
    if not planted and not paid and fb_index:
        fb = fb_index.get(_norm_cid(c.get("cluster_id")))
        if fb:
            planted |= set(fb.get("planted") or set())
            paid |= set(fb.get("paid") or set())
    return planted, paid


def compute_book_ledger(clusters: list[dict], fb_index: dict | None = None) -> dict:
    """全书 stock+flow 账本。fb_index = 伏笔表回退索引（SYS-5 ②·摘要缺流水时启用）。"""
    total_planted: set = set()
    total_paid: set = set()
    timeline = []  # 每 cluster 的累计 stock 序列
    for c in clusters:
        planted, paid = _cluster_planted_paid(c, fb_index)
        total_planted |= planted
        total_paid |= paid
        open_set = total_planted - total_paid
        timeline.append({
            "cluster_id": c.get("cluster_id"),
            "planted_count": len(planted),
            "paid_count": len(paid),
            "cum_planted": len(total_planted),
            "cum_paid": len(total_paid),
            "open_debt": len(open_set),
            "open_ratio": (len(open_set) / len(total_planted)) if total_planted else 0.0,
        })
    return {
        "total_planted": len(total_planted),
        "total_paid": len(total_paid),
        "open_debt": len(total_planted - total_paid),
        "open_ratio": (len(total_planted - total_paid) / len(total_planted))
                       if total_planted else 0.0,
        "timeline": timeline,
    }


def compute_volume_ledger(clusters: list[dict], fb_index: dict | None = None) -> dict:
    """分卷 stock+flow 账本（每卷独立 set·跨卷不串）。fb_index = 伏笔表回退索引（SYS-5 ②）。"""
    by_vol: dict = {}
    for c in clusters:
        vol = _cluster_volume(c)
        planted, paid = _cluster_planted_paid(c, fb_index)
        entry = by_vol.setdefault(vol, {
            "volume": vol,
            "planted_set": set(),
            "paid_set": set(),
            "timeline": [],
        })
        entry["planted_set"] |= planted
        entry["paid_set"] |= paid
        open_set = entry["planted_set"] - entry["paid_set"]
        entry["timeline"].append({
            "cluster_id": c.get("cluster_id"),
            "planted_count": len(planted),
            "paid_count": len(paid),
            "cum_planted": len(entry["planted_set"]),
            "cum_paid": len(entry["paid_set"]),
            "open_debt": len(open_set),
            "open_ratio": (len(open_set) / len(entry["planted_set"]))
                           if entry["planted_set"] else 0.0,
        })
    # 序列化：去掉 set 字段 + 加 summary
    out = {}
    for vol, entry in by_vol.items():
        planted = entry["planted_set"]
        paid = entry["paid_set"]
        out[str(vol)] = {
            "volume": vol,
            "total_planted": len(planted),
            "total_paid": len(paid),
            "open_debt": len(planted - paid),
            "open_ratio": (len(planted - paid) / len(planted)) if planted else 0.0,
            "timeline": entry["timeline"],
        }
    return out


def detect_findings(book: dict, by_vol: dict, total_clusters: int) -> list[dict]:
    """三条规则。"""
    findings = []

    # ① BOOK_MORTGAGE_ABSENT：前 30% cluster 累计 planted=0
    head_cut = max(1, int(total_clusters * BOOK_MORTGAGE_HEAD_PCT))
    head_timeline = book["timeline"][:head_cut]
    if head_timeline and head_timeline[-1]["cum_planted"] == 0 and total_clusters >= 3:
        findings.append({
            "severity": "advisory",
            "code": CODE_BOOK_MORTGAGE,
            "head_clusters": head_cut,
            "suggestion": (f"前 {head_cut} 个 cluster 零伏笔/零秘密埋设·开篇 Book Mortgage 缺位"
                           f"·读者没『欠债感』 = 没翻页动力"),
        })

    # ② VOLUME_TAIL_RUNAWAY：卷 60%+ 区段 open_ratio 连续 N cluster 上升
    for vid, vdata in by_vol.items():
        timeline = vdata["timeline"]
        if len(timeline) < 3:
            continue
        cut = max(1, int(len(timeline) * VOL_TAIL_THRESHOLD_PCT))
        tail = timeline[cut:]
        if len(tail) < VOL_RUNAWAY_RISE_STREAK + 1:
            continue
        rising = 0
        for i in range(1, len(tail)):
            if tail[i]["open_ratio"] > tail[i - 1]["open_ratio"]:
                rising += 1
            else:
                rising = 0
            if rising >= VOL_RUNAWAY_RISE_STREAK:
                findings.append({
                    "severity": "advisory",
                    "code": CODE_VOL_RUNAWAY,
                    "volume": vdata["volume"],
                    "rising_streak": rising + 1,
                    "tail_ratios": [round(t["open_ratio"], 3) for t in tail],
                    "suggestion": (f"卷 {vdata['volume']} 后段 open_ratio 连续 {rising + 1} cluster 上升"
                                   f"·烂尾债务累积 → 安排集中偿还场景"),
                })
                break

    # ③ VOLUME_OVERSHOOT：卷末 open_ratio > 60% 且 planted >= 3
    for vid, vdata in by_vol.items():
        if vdata["total_planted"] >= 3 and vdata["open_ratio"] > VOL_OVERSHOOT_OPEN_RATIO:
            findings.append({
                "severity": "advisory",
                "code": CODE_VOL_OVERSHOOT,
                "volume": vdata["volume"],
                "open_ratio": round(vdata["open_ratio"], 3),
                "open_debt": vdata["open_debt"],
                "total_planted": vdata["total_planted"],
                "suggestion": (f"卷 {vdata['volume']} 累计借债 {vdata['total_planted']} 还 "
                               f"{vdata['total_planted'] - vdata['open_debt']}·剩余 "
                               f"{vdata['open_debt']}（{vdata['open_ratio']:.0%}）超 60% 阈值"
                               f" = 跨卷悬空风险"),
            })
    return findings


def build_snapshot(book: dict, by_vol: dict, findings: list[dict]) -> dict:
    """供 writer D7 注入的简化债务状态卡。"""
    return {
        "schema_version": "1.0",
        "book": {
            "total_planted": book["total_planted"],
            "total_paid": book["total_paid"],
            "open_debt": book["open_debt"],
            "open_ratio": round(book["open_ratio"], 3),
        },
        "volumes": [
            {
                "volume": v["volume"],
                "total_planted": v["total_planted"],
                "total_paid": v["total_paid"],
                "open_debt": v["open_debt"],
                "open_ratio": round(v["open_ratio"], 3),
            }
            for v in by_vol.values()
        ],
        "advisory_codes": [f["code"] for f in findings],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    mode = _mode()
    project_root = Path(args.project)
    if mode == "off":
        print("[OFF] NARRATIVE_DEBT_MODE=off")
        sys.exit(0)

    clusters = csr.get_clusters(project_root)
    if not clusters:
        print("[SKIP] 账本无 cluster 记录")
        sys.exit(0)
    # 🔴 2026-06-27 SYS-5 ②：cluster 摘要 foreshadow 流水未持久化时回退伏笔表.json（消除 total_planted 恒 0）。
    fb_index = _load_foreshadow_table_fallback(project_root)
    book = compute_book_ledger(clusters, fb_index)
    by_vol = compute_volume_ledger(clusters, fb_index)
    findings = detect_findings(book, by_vol, total_clusters=len(clusters))
    fb_used = bool(fb_index) and book["total_planted"] > 0 and not any(
        (c.get("foreshadow_planted") or c.get("foreshadow_paid")) for c in clusters)

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = build_snapshot(book, by_vol, findings)
    report = {
        "scan_type": "narrative_debt_ledger",
        "scan_ts": ts,
        "mode": mode,
        "foreshadow_table_fallback": fb_used,  # SYS-5 ②：摘要缺流水→读伏笔表
        "clusters_total": len(clusters),
        "book": book,
        "volumes": by_vol,
        "snapshot": snapshot,
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
        },
    }
    out_path = out_dir / f"narrative_debt_ledger_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 单独写一份给 build_manifest 注入用的稳定路径
    snap_path = out_dir / "narrative_debt_snapshot.json"
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[narrative_debt_ledger] book open_debt={book['open_debt']}/{book['total_planted']} · "
          f"volumes={len(by_vol)} · findings={len(findings)}"
          + ("  [fallback=伏笔表.json]" if fb_used else ""))
    for f in findings[:4]:
        print(f"  [{f['severity'].upper()}] {f['code']}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")

    if mode == "shadow":
        # shadow 只记不判（零回归）
        for f in findings:
            print(f"[SHADOW] narrative_debt: {f['code']} — 不上报", file=sys.stderr)
        sys.exit(0)
    if report["summary"]["warning"] > 0:
        sys.exit(2)
    if report["summary"]["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
