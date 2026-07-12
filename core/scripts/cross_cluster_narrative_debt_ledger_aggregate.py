"""cross_cluster_narrative_debt_ledger_aggregate.py — 叙事债务账本

【缺口】长篇叙事「债务」是结构骨——伏笔/秘密/承诺/悬念都是
作者向读者借的债，必须在某处偿还。AI 写作的系统性短板：
  · 借得快还得快（同 cluster 内开闭合 → 没债务感）
  · 借完忘还（后段累计未偿超载 → 烂尾感）
  · 前 30% 不借（开篇 hook 弱 → Book Mortgage 缺位）

本 aggregator 三档：
  ① BOOK 桶（全书 stock+flow）：累计 planted/paid · 健康债比 = open_debt / total_planted
  ② VOLUME 桶（每卷 stock+flow）：60% 后段 open_ratio 连续上升 → 烂尾风险 advisory
  ③ SCENE/CLUSTER 桶（每 cluster flow）：当 cluster 借出/偿还的债务流水

cluster 列表读 cluster_summary_reader（故事块摘要.json · CLUSTER_FIELDS 闭集不含
foreshadow 流水 / 卷号字段）。两项数据另走各自的唯一权威源：
  · planted/paid 债务 —— 伏笔表.json 按 setup_cluster 归集（_load_foreshadow_ledger）
  · cluster 卷号 —— 事件簇.json.clusters[].parent_me 联结 大势卡.json.major_events[].volume
    （_build_cluster_volume_index，ME id 由 gen_creative_volume_arc/cluster_emergence_engine
    写入时即取自 ME 自身 id，两表天然同形）
零 LLM · 确定性。env NARRATIVE_DEBT_MODE：off / shadow（默认·只记不判·零回归）/ active。

输出 advisory：
  · DEBT_BOOK_MORTGAGE_ABSENT：前 30% cluster 累计 planted=0（开篇没借债）
  · DEBT_VOLUME_TAIL_RUNAWAY：当前卷 60%+ 区段 open_ratio 连续 2+ cluster 上升
  · DEBT_VOLUME_OVERSHOOT：当前卷末 open_debt 占该卷 planted > 60%（卷末烂尾）

snapshot 写 _数据库/.cross_cluster_scan/narrative_debt_ledger_*.json
供 build_manifest 注入 debt_ledger_snapshot（writer D7 用）。

退出码: 0 健康 / 1 advisory（默认 shadow 不上报） / 2 warning
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
# cluster_id 归一化共享工具：伏笔表.json.setup_cluster / 事件簇.json.cluster_id 与
# 摘要账本的 cluster_id 形态不一定一致（"6" vs "cluster_006"），三表联结前统一走
# normalize_cluster_id 防漏匹配。
try:
    import cluster_lookup  # noqa: E402
except Exception:  # pragma: no cover
    cluster_lookup = None


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


def _norm_cid(cid) -> str:
    """归一 cluster 标识（"6"/"cluster_006" → "cluster_006"）。cluster_lookup 不可用时原样返回。"""
    if cluster_lookup is not None:
        try:
            n = cluster_lookup.normalize_cluster_id(cid)
            if n:
                return n
        except Exception:
            pass
    return str(cid or "")


def _load_json(p: Path) -> dict:
    """读 JSON 文件为 dict；不存在/损坏/非 dict → {}（调用方据空 dict 判无数据）。"""
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _db_dir(project_root: Path) -> Path:
    root = Path(project_root)
    return root if root.name == "_数据库" else root / "_数据库"


def _me_volume(me: dict) -> int | None:
    """ME（大势卡.json.major_events 条目）所属卷号：显式 volume 字段优先，否则从
    id（ME-V<N>-<序>）解析（gen_creative_volume_arc 生成时强制两者一致）。"""
    if not isinstance(me, dict):
        return None
    v = me.get("volume")
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    m = re.search(r"[Vv](\d+)", str(me.get("id") or ""))
    return int(m.group(1)) if m else None


def _build_cluster_volume_index(project_root: Path) -> dict[str, int]:
    """cluster_id → 卷号索引：事件簇.json.clusters[].parent_me 联结 大势卡.json.major_events[].volume。

    故事块摘要.json 的 cluster 记录不带卷号字段（CLUSTER_FIELDS 闭集无 volume/vol/metadata），
    卷号只能沿这条链路推：cluster_id → parent_me（事件簇.json，emergence/outline 写入时取自
    被选中 ME 自身 id，两表天然同形）→ ME.volume（大势卡.json，gen_creative_volume_arc 生成
    时强制校验非正整数即报错）。任一环节缺失/解析不出 → 该 cluster 不进索引，调用方按
    未知卷号 0 处理。
    """
    db = _db_dir(project_root)
    dashishi = _load_json(db / "大势卡.json")
    shijianji = _load_json(db / "事件簇.json")
    me_volume: dict[str, int] = {}
    for me in dashishi.get("major_events") or []:
        if isinstance(me, dict) and me.get("id"):
            v = _me_volume(me)
            if v is not None:
                me_volume[str(me["id"])] = v
    index: dict[str, int] = {}
    for c in shijianji.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        cid = _norm_cid(c.get("cluster_id"))
        if not cid:
            continue
        parents = c.get("parent_me")
        parents = parents if isinstance(parents, list) else [parents]
        for pid in parents:
            v = me_volume.get(str(pid))
            if v is not None:
                index[cid] = v
                break
    return index


def _cluster_volume(c: dict, vol_index: dict) -> int:
    """本 cluster 的卷号：查 vol_index（cluster_id → volume 联结索引），查不到归 0。"""
    return vol_index.get(_norm_cid(c.get("cluster_id")), 0)


def _load_foreshadow_ledger(project_root: Path) -> dict:
    """读 伏笔表.json，按 setup_cluster 归集 planted/paid——cluster 债务流水的唯一权威来源。

    故事块摘要.json 的 cluster 记录不带 foreshadow 流水字段（CLUSTER_FIELDS 闭集），每个
    cluster 借了多少债、还了多少债只能从 伏笔表.json 读出。

    返回 {normalized_cluster_id: {"planted": set[fid], "paid": set[fid]}}。
    · planted = 该 cluster 埋下的所有 promises/deadlines/pledges/secrets 的 id
      （authoring 即确定·可靠）。
    · paid = 其中已结清者——promises 按三态生命周期 status=="consumed"（open/suspended=
      未回收），secrets/deadlines/pledges 沿用各自 revealed/paid 语义。
    表不存在/损坏/空 → {}。
    """
    db = _db_dir(project_root)
    data = _load_json(db / "伏笔表.json")
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
            if item.get("status") == "consumed" or item.get("revealed") or item.get("paid"):
                entry["paid"].add(str(fid))
    return out


def _cluster_planted_paid(c: dict, fb_index: dict) -> tuple[set, set]:
    """本 cluster 的 planted/paid 债务集合（伏笔表.json 是唯一来源，按 cluster_id 查 fb_index）。"""
    fb = fb_index.get(_norm_cid(c.get("cluster_id"))) or {}
    return set(fb.get("planted") or set()), set(fb.get("paid") or set())


def compute_book_ledger(clusters: list[dict], fb_index: dict) -> dict:
    """全书 stock+flow 账本。fb_index = _load_foreshadow_ledger() 产出的 planted/paid 索引。"""
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


def compute_volume_ledger(clusters: list[dict], fb_index: dict, vol_index: dict) -> dict:
    """分卷 stock+flow 账本（每卷独立 set·跨卷不串）。

    fb_index = _load_foreshadow_ledger() 产出的 planted/paid 索引。
    vol_index = _build_cluster_volume_index() 产出的 cluster_id → 卷号索引。
    """
    by_vol: dict = {}
    for c in clusters:
        vol = _cluster_volume(c, vol_index)
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
    fb_index = _load_foreshadow_ledger(project_root)
    vol_index = _build_cluster_volume_index(project_root)
    book = compute_book_ledger(clusters, fb_index)
    by_vol = compute_volume_ledger(clusters, fb_index, vol_index)
    findings = detect_findings(book, by_vol, total_clusters=len(clusters))

    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = build_snapshot(book, by_vol, findings)
    report = {
        "scan_type": "narrative_debt_ledger",
        "scan_ts": ts,
        "mode": mode,
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
          f"volumes={len(by_vol)} · findings={len(findings)}")
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
