#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""foreshadowing_handoff_scanner.py — cluster 内伏笔埋设+回收配对检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测 cluster_brief.foreshadowing_to_plant 列出的伏笔是否在正文中真实埋设；
+ 检测 伏笔表.promises 中标记 setup_cluster = 本 cluster 的伏笔是否有物理证据。

输出 issue code:
  · FORESHADOWING_NOT_PLANTED (hard_gate? 看 brief 要求)
  · FORESHADOWING_PHYSICAL_EVIDENCE_MISSING (advisory)

用法：python foreshadowing_handoff_scanner.py <project> <cluster_id>
     例：python foreshadowing_handoff_scanner.py 项目 cluster_001
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan(project_root: Path, cluster_id: str) -> dict:
    cluster_id = cluster_id if cluster_id.startswith("cluster_") else f"cluster_{cluster_id}"

    # 找 cluster 草稿
    cluster_key = cluster_id.replace("cluster_", "")
    draft_path = project_root / "章节" / f"cluster_{cluster_key}_draft" / f"cluster_{cluster_key}_draft.txt"
    if not draft_path.exists():
        return {"_fatal": f"cluster_draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")

    # 加载事件簇 + 伏笔表
    ec = load(project_root / "_数据库" / "事件簇.json")
    foreshadow = load(project_root / "_数据库" / "伏笔表.json")

    # 找 cluster brief（兼容 cluster_id / key 两种 schema）
    cluster_brief = None
    for c in ec.get("clusters", []):
        c_id = c.get("cluster_id") or c.get("key")  # 兼容两种
        if c_id == cluster_id or c_id == cluster_key:
            cluster_brief = c
            break

    if not cluster_brief:
        return {
            "_fatal": f"cluster {cluster_id} 未在事件簇.json 中找到",
        }

    # 检测 1: foreshadowing_to_plant 兑现度
    planted_check = []
    not_planted = []
    for fs in cluster_brief.get("foreshadowing_to_plant", []):
        fs_id = fs.get("id", "")
        fs_desc = fs.get("desc", "") or fs.get("description", "")
        # 启发式找 desc 关键词在正文中
        # 取 desc 前 8 个 CJK 作为搜索词
        cjk_chars = re.findall(r"[一-鿿]", fs_desc)
        keyword = "".join(cjk_chars[:6])
        if keyword and keyword in text:
            planted_check.append({"id": fs_id, "found": True, "keyword": keyword})
        elif fs_desc:
            # 2026-05-29 复审修复 [M13]：构造 not_planted 时拷入 tier 键。
            # 否则下方 gate_level 判定 `f.get("tier", 99) == 1` 永远拿不到 tier，
            # tier-1（必埋）漏埋被误降为 advisory（应为 hard_gate）。
            not_planted.append({
                "id": fs_id,
                "desc": fs_desc[:80],
                "keyword_searched": keyword,
                "tier": fs.get("tier", 99),
            })

    # 检测 2: 伏笔表 promises setup_cluster = 本 cluster 的伏笔
    promises = foreshadow.get("promises", [])
    cluster_promises = [p for p in promises if p.get("setup_cluster") == cluster_id]
    promises_with_evidence = []
    promises_no_evidence = []
    for p in cluster_promises:
        ev = p.get("trigger_condition", {}).get("physical_evidence", "")
        if not ev:
            continue
        cjk_ev = re.findall(r"[一-鿿]", ev)
        ev_keyword = "".join(cjk_ev[:5])
        if ev_keyword and ev_keyword in text:
            promises_with_evidence.append(p.get("id"))
        else:
            promises_no_evidence.append({"id": p.get("id"), "physical_evidence_expected": ev[:60]})

    # 汇总
    issues = []
    if not_planted:
        issues.append({
            "code": "FORESHADOWING_NOT_PLANTED",
            # 2026-05-29 复审复修 [M13]：tier 存在 int(1) 与 string("A") 双约定（event_cluster_schema
            # 定义为 "A"/"B"/"C"，运行时部分项目写 1/2/3）。两种都认 tier-1/A 为必埋 → hard_gate。
            # 2026-05-30 北极星复审：伏笔【埋设】漏（计划埋但没埋）≠ 穿帮（读者看不出该埋未埋），属
            # advisory 提醒；只有伏笔【回收】漏（FORESHADOWING_NOT_PAID，已在 HARD_GATE_CODES）才是穿帮。
            # 且本检测用 6 连字精确匹配极易误报（实测全 not_planted）→ 不得自立 hard_gate 误卡写作。
            "gate_level": "advisory",
            "severity": "error" if len(not_planted) >= 2 else "warning",
            "count": len(not_planted),
            "items": not_planted[:5],
            "msg": f"⚠️ {len(not_planted)} 个 cluster 计划伏笔未在正文中检出关键词",
        })
    if promises_no_evidence:
        issues.append({
            "code": "FORESHADOWING_PHYSICAL_EVIDENCE_MISSING",
            "gate_level": "advisory",
            "severity": "warning",
            "count": len(promises_no_evidence),
            "items": promises_no_evidence[:5],
            "msg": f"⚠️ {len(promises_no_evidence)} 个伏笔的 physical_evidence 未在正文中检出",
        })

    return {
        "schema_version": "1.0",
        "scanner": "foreshadowing_handoff_scanner",
        "cluster_id": cluster_id,
        "cluster_mode": True,
        "gate_level": "advisory",  # 整体 advisory，单 issue 可能 hard_gate
        "planned_count": len(cluster_brief.get("foreshadowing_to_plant", [])),
        "planted_check": len(planted_check),
        "not_planted_count": len(not_planted),
        "promises_in_cluster": len(cluster_promises),
        "promises_with_evidence": len(promises_with_evidence),
        "issues": issues,
        "warning": (
            f"⚠️ 伏笔接力: {len(not_planted)} 未埋设 + {len(promises_no_evidence)} 缺物理证据"
            if (not_planted or promises_no_evidence) else None
        ),
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    cluster_id = args[1]
    report = scan(project, cluster_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
