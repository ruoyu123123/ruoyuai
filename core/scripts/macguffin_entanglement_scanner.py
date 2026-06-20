#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macguffin_entanglement_scanner.py — MacGuffin Entanglement Index
(advisory · cross-cluster · 2026-06-20 R9 W5 Batch-M · plot_devices_taxonomy)

【缺口】R9 联网调研 (Hitchcock MacGuffin definition + plot_devices_taxonomy):
MacGuffin = 推动情节的「核心驱动物」(可以是真实物件/抽象目标)·真正好的 MacGuffin
应该与主角 goal 紧绷·LLM 默认产「装饰性 MacGuffin」(出现但与 goal 脱钩)。此前
全系统:
  · 道具.json     声明物件
  · timeline_item_location 跟持有者
  · narrative_debt_ledger 跟伏笔
  · 【MacGuffin entanglement-with-goal 零检测】

本 scanner 补：跨 cluster MacGuffin Entanglement Index =
  entanglement_ratio = |S_m^goal| / |S_m|
其中:
  S_m       = 出现 MacGuffin 的 cluster 集合
  S_m^goal  = 既出现 MacGuffin 又出现主角 goal pursuit 信号的 cluster 集合
ratio < 0.4 → advisory MACGUFFIN_ORNAMENTAL (装饰性·与 goal 脱钩)

【做法 · 确定性纯 JSON 抽取·零 LLM·零依赖】
  1. 读 _数据库/道具.json (扩 is_macguffin 声明位)
     items_list = [{"name", "is_macguffin": true/false, ...}]
     若所有 item.is_macguffin 都 False → skip (北极星②·作者未声明)
  2. 读 cluster_summary_reader 的 clusters 序列
  3. goal_pursuit 信号词典 (高确定性):
     追查/夺回/护送/守护/为了/目的/任务/接近/锁定/找到/找回/查清
  4. 每 cluster 扫:
     - 道具命中: name 在 cluster 全文/摘要任意出现
     - goal 命中: goal_pursuit 信号 ≥1 命中
     - S_m += cluster (含道具)
     - S_m^goal += cluster (含道具 且 含 goal 信号)
  5. ratio = |S_m^goal| / max(1, |S_m|)
  6. ratio < 0.4 且 |S_m| >= 2 → advisory MACGUFFIN_ORNAMENTAL

【输出】
  _数据库/.cross_chapter_scan/macguffin_advisory_snapshot.json (供 build_manifest 注入)

【北极星② / ⑤ 顾问非法官】作者档第一权威 (is_macguffin 必须显式声明)·全 advisory·
code MACGUFFIN_ORNAMENTAL 绝不进 audit_hub.HARD_GATE_CODES。
env MACGUFFIN_ENTANGLEMENT_MODE: off / shadow(默认) / active。

用法：python macguffin_entanglement_scanner.py <project> [--last-n N]
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
try:
    import cluster_summary_reader as csr   # noqa: E402
except Exception:
    csr = None

ISSUE_CODE = "MACGUFFIN_ORNAMENTAL"
ENTANGLEMENT_FLOOR = 0.4

GOAL_PURSUIT_PATTERN = re.compile(
    r"追查|夺回|护送|守护|为了|目的|任务|接近|锁定|找到|找回|查清|"
    r"寻回|奔向|抢回|追踪|追寻|追击|阻止|抵达|获取|取得"
)


def _mode() -> str:
    m = (os.environ.get("MACGUFFIN_ENTANGLEMENT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_macguffins(project_root: Path) -> list:
    """读 道具.json items_list·过滤 is_macguffin=true·返回 [{name, ...}]"""
    p = project_root / "_数据库" / "道具.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    items = obj.get("items") or []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("title") or it.get("id")
        if not isinstance(name, str) or not name.strip():
            continue
        if it.get("is_macguffin") is True:
            out.append({"name": name.strip(),
                        "id": it.get("id") or name.strip(),
                        "category": it.get("category")})
    return out


def _read_cluster_text(project_root: Path, cluster: dict) -> str:
    parts = []
    scope = cluster.get("scope_summary") or ""
    if isinstance(scope, str) and scope:
        parts.append(scope)
    chapters = cluster.get("chapters") or {}
    if isinstance(chapters, dict):
        for rec in chapters.values():
            if not isinstance(rec, dict):
                continue
            for fld in ("summary", "scene_summary", "title"):
                v = rec.get(fld)
                if isinstance(v, str) and v:
                    parts.append(v)
    if not parts:
        cluster_id = cluster.get("cluster_id") or ""
        if cluster_id:
            draft_dir = project_root / "章节" / f"{cluster_id}_draft"
            if draft_dir.exists():
                for f in sorted(draft_dir.glob("*.txt")):
                    try:
                        parts.append(f.read_text(encoding="utf-8"))
                    except OSError:
                        pass
    return "\n".join(parts)


def compute_entanglement(macguffins: list, clusters: list,
                         project_root: Path) -> dict:
    """计算每个 macguffin 的 entanglement_ratio"""
    per_mac = {}
    cluster_texts = []
    for c in clusters:
        cid = c.get("cluster_id") or "?"
        text = _read_cluster_text(project_root, c)
        cluster_texts.append((cid, text))

    for mac in macguffins:
        name = mac["name"]
        name_pat = re.escape(name)
        S_m = []
        S_m_goal = []
        for cid, text in cluster_texts:
            if not text:
                continue
            if re.search(name_pat, text):
                S_m.append(cid)
                if GOAL_PURSUIT_PATTERN.search(text):
                    S_m_goal.append(cid)
        if not S_m:
            ratio = None
        else:
            ratio = round(len(S_m_goal) / len(S_m), 3)
        per_mac[name] = {
            "name": name,
            "id": mac.get("id"),
            "S_m": S_m,
            "S_m_goal": S_m_goal,
            "entanglement_ratio": ratio,
            "appearance_clusters": len(S_m),
        }
    return per_mac


def emit_findings(per_mac: dict) -> list:
    findings = []
    ornamental = []
    for name, info in per_mac.items():
        ratio = info["entanglement_ratio"]
        if ratio is None:
            continue
        if info["appearance_clusters"] < 2:
            continue  # 单 cluster 出现不判 (尚未循环铺线)
        if ratio < ENTANGLEMENT_FLOOR:
            ornamental.append(info)
    if ornamental:
        findings.append({
            "severity": "advisory",
            "code": ISSUE_CODE,
            "count": len(ornamental),
            "samples": [{"name": o["name"], "ratio": o["entanglement_ratio"],
                         "S_m": o["S_m"], "S_m_goal": o["S_m_goal"]}
                        for o in ornamental[:6]],
            "suggestion": (
                f"{len(ornamental)} 个 MacGuffin entanglement_ratio < {ENTANGLEMENT_FLOOR}"
                f"·与主角 goal 脱钩=装饰性·建议下个 cluster 把 MacGuffin 写入 goal pursuit "
                f"动作 (追查/夺回/守护)"),
        })
    return findings


def main():
    ap = argparse.ArgumentParser(
        description="MacGuffin Entanglement Index 跨 cluster (advisory · cross-cluster)")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=None)
    args = ap.parse_args()

    mode = _mode()
    out = {
        "scanner": "macguffin_entanglement",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "verdict": "PASS",
        "findings": [],
    }
    project_root = Path(args.project).resolve()
    if mode == "off":
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    macguffins = _read_macguffins(project_root)
    out["macguffin_count"] = len(macguffins)
    if not macguffins:
        out["note"] = "无 is_macguffin=true 声明·跳过 (北极星②·作者未声明)"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    if csr is None:
        out["note"] = "cluster_summary_reader 不可用·跳过"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    try:
        clusters = csr.get_clusters(project_root, last_n=args.last_n)
    except Exception as e:
        out["note"] = f"读取 clusters 失败:{str(e)[:120]}"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    if not clusters or len(clusters) < 2:
        out["note"] = f"cluster 数太少 ({len(clusters) if clusters else 0} < 2)"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    per_mac = compute_entanglement(macguffins, clusters, project_root)
    out["per_macguffin"] = per_mac

    findings = emit_findings(per_mac) if mode == "active" else []
    out["findings"] = findings
    if findings:
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = findings[0]["suggestion"]

    # 写 cross_chapter_scan snapshot
    snap_dir = project_root / "_数据库" / ".cross_chapter_scan"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "scan_type": "macguffin_entanglement",
        "scan_ts": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "mode": mode,
        "n_clusters": len(clusters),
        "per_macguffin": per_mac,
        "findings": findings,
        "advisory_codes": sorted({f["code"] for f in findings}),
    }
    try:
        (snap_dir / "macguffin_advisory_snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[WARN] macguffin snapshot 写失败:{e}", file=sys.stderr)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1 if out.get("warning") else 0)


if __name__ == "__main__":
    main()
