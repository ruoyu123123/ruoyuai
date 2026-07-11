"""cross_cluster_declarative_data_aggregate.py — 声明式数据消费扫描（v19.2 新增）

检测 6 类声明式字段是否被消费 / 是否数值停滞 / 是否到期未触发：
1. RELATIONSHIPS_STAGNANT     - 关系数值连续 >5 章无变化
2. FACTION_STANDINGS_FROZEN    - 势力数值卷级里程碑章未变
3. TRIGGERED_EVENTS_EMPTY      - pending_events 有但 triggered_events 全空
4. TRAVEL_LOG_BARREN           - 角色 character_movements 多次但 travel_log 没记录
5. SECRET_OVERDUE              - secret.reveal_at_cluster <= 当章但 status 还是 hidden
6. WILL_LEARN_NOT_TRIGGERED    - will_learn.learn_at_cluster <= 当章但 knows 没新增

用法：python cross_cluster_declarative_data_aggregate.py <项目路径> [--last-n 10]
退出码：0 健康 / 1 advisory / 2 warning
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_dirs(project_root: Path) -> list[tuple[int, Path]]:
    out = []
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            out.append((int(m.group(1)), d))
    out.sort(key=lambda x: x[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    db = project_root / "_数据库"
    if not db.is_dir():
        print(f"[FATAL] _数据库 不存在", file=sys.stderr)
        sys.exit(2)

    chapter_dirs = find_chapter_dirs(project_root)

    # ===== 2026-05-29 cluster 化分支：last_ch + faction/relationship 信号摘要驱动 =====
    # secret/will_learn 维度此前已读 reveal_at_cluster/learn_at_cluster（保留）。
    # 本分支补 last_ch 锚点 + FACTION/RELATIONSHIPS 维度的账本来源。
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster」（仅影响账本派生的 movement 信号）。
    use_ledger = csr.is_cluster_mode() and csr.ledger_has_field(project_root, "relationship_changes")
    ledger_recs = []
    if csr.is_cluster_mode():
        ledger_recs = csr.get_chapter_records(project_root)
        if ledger_recs and not chapter_dirs:
            # 账本有章但物理目录尚未落盘 —— 仍可跑 last_ch 驱动的 overdue 检测
            pass

    if chapter_dirs:
        last_ch = chapter_dirs[-1][0]
    elif ledger_recs:
        last_ch = ledger_recs[-1][0]
    else:
        print("[OK] 无已写章节")
        sys.exit(0)
    findings = []

    # ===== 1. RELATIONSHIPS_STAGNANT =====
    rel_data = load_json(db / "关系.json", {})
    rels = rel_data.get("relationships", [])
    stagnant_rels = []
    for r in rels:
        last_mod = r.get("_last_modified_at_ch", 1)
        if last_ch - last_mod >= 5 and last_ch >= 6:
            stagnant_rels.append({"from": r.get("from"), "to": r.get("to"), "last_mod_ch": last_mod})
    if stagnant_rels and last_ch >= 6:
        findings.append({
            "severity": "advisory",
            "code": "RELATIONSHIPS_STAGNANT",
            "metric": {"count": len(stagnant_rels), "current_ch": last_ch, "samples": stagnant_rels[:3]},
            "message": f"{len(stagnant_rels)} 条关系数值连续 ≥5 章无变化（截至 ch{last_ch}）",
            "suggestion": "writer 应在 _changes.json 写 relationship_changes 让关系网动起来",
        })

    # ===== 2. FACTION_STANDINGS_FROZEN =====
    standings = rel_data.get("faction_standings", {})
    if standings and last_ch >= 10:
        # 看是否所有 standing 都是 init 值（手工填的整数）
        # 通过检查 _changes 历史是否触发过 faction_standing_changes
        any_changed = False
        # 2026-05-29 cluster 化：账本有 relationship_changes/faction_snapshot 时
        # 用账本判定 faction 是否动过；否则回退逐章 glob faction_standing_changes。
        if use_ledger:
            any_changed = any(
                rec.get("relationship_changes") or rec.get("faction_snapshot")
                or rec.get("faction_standing_changes")
                for _ch, rec in ledger_recs
            )
        if not any_changed:
            for ch, d in chapter_dirs:
                changes = load_json(d / f"第{ch:03d}章_changes.json", {})
                if changes.get("factual", {}).get("faction_standing_changes"):
                    any_changed = True
                    break
        if not any_changed:
            findings.append({
                "severity": "advisory",
                "code": "FACTION_STANDINGS_FROZEN",
                "metric": {"standings": standings, "scanned_chapters": last_ch},
                "message": f"faction_standings 数值已 {last_ch} 章无任何更新（卷级里程碑应触发）",
                "suggestion": "卷级关键事件后（财团崩塌/联姻/敌对升级）writer 应写 faction_standing_changes",
            })

    # ===== 3. TRIGGERED_EVENTS_EMPTY =====
    events = load_json(db / "事件表.json", {})
    pending = events.get("pending_events", [])
    triggered = events.get("triggered_events", [])
    # 检查 pending_events 是否有 scheduled_ch <= last_ch 的还在 pending
    overdue_pending = [p for p in pending if p.get("scheduled_ch") and p["scheduled_ch"] <= last_ch]
    if overdue_pending and not triggered:
        findings.append({
            "severity": "warning",
            "code": "TRIGGERED_EVENTS_EMPTY",
            "metric": {"overdue_pending": len(overdue_pending), "triggered_total": len(triggered), "samples": [p.get("id") for p in overdue_pending[:5]]},
            "message": f"{len(overdue_pending)} 条 pending_events 已到期但 triggered_events 全空",
            "suggestion": "writer 应在 _changes.json 写 event_triggers，cluster-save-state 自动转移",
        })
    elif overdue_pending and triggered:
        # 部分情况
        findings.append({
            "severity": "advisory",
            "code": "EVENTS_PARTIAL_TRIGGERED",
            "metric": {"overdue_pending": len(overdue_pending), "triggered_total": len(triggered)},
            "message": f"{len(overdue_pending)} 条 pending 已到期未触发（已有 {len(triggered)} 条触发记录）",
            "suggestion": "排查未触发的 pending 是否被遗忘",
        })

    # ===== 4. TRAVEL_LOG_BARREN =====
    map_data = load_json(db / "地图.json", {})
    travel_log = map_data.get("travel_log", [])
    if last_ch >= 3 and len(travel_log) < last_ch:
        # 检查 _changes 是否有 character_movements 但 travel_log 没对应
        total_movements = 0
        for ch, d in chapter_dirs:
            changes = load_json(d / f"第{ch:03d}章_changes.json", {})
            total_movements += len(changes.get("factual", {}).get("character_movements", []))
        if total_movements > len(travel_log) * 2:
            findings.append({
                "severity": "advisory",
                "code": "TRAVEL_LOG_BARREN",
                "metric": {"travel_log_size": len(travel_log), "total_movements_in_changes": total_movements, "current_ch": last_ch},
                "message": f"travel_log 仅 {len(travel_log)} 条，但 _changes character_movements 累计 {total_movements} 次（断层）",
                "suggestion": "writer 写 travel_log_added 字段或 cluster-save-state 自动从 character_movements 派生",
            })

    # ===== 5. SECRET_OVERDUE =====
    fs_data = load_json(db / "伏笔表.json", {})
    secrets = fs_data.get("secrets", [])
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 读 reveal_at_cluster
    import re as _re
    try:
        import cluster_lookup as _cl
    except Exception:
        _cl = None
    overdue_secrets = []
    for s in secrets:
        rc = s.get("reveal_at_cluster")
        # 2026-05-30 北极星复审：reveal_at_cluster 是 cluster ID 不是章号——用 cluster_id_to_range 取末章，
        # cluster 整块写完(hi<=last_ch)才算到期（禁抽数字当章号比）。
        _rng = _cl.cluster_id_to_range(project_root, rc) if (_cl and isinstance(rc, str)) else None
        reveal_hi = int(_rng[1]) if _rng and len(_rng) == 2 else None
        if reveal_hi is not None and reveal_hi <= last_ch and s.get("status") == "hidden":
            overdue_secrets.append({"id": s.get("id"), "reveal_at_cluster": rc, "secret": s.get("secret", "")[:40]})
    if overdue_secrets:
        findings.append({
            "severity": "warning",
            "code": "SECRET_OVERDUE",
            "metric": {"count": len(overdue_secrets), "samples": overdue_secrets[:3]},
            "message": f"{len(overdue_secrets)} 条 secret 已到期 reveal_at_cluster 但 status 仍 hidden",
            "suggestion": "writer 在到期 cluster 写 secret_status_changes，将 status 改为 leaked/revealed",
        })

    # ===== 6. WILL_LEARN_NOT_TRIGGERED =====
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 读 learn_at_cluster
    cards = load_json(db / "人物卡.json", {})
    overdue_wl = []
    for c in cards.get("characters", []):
        if not isinstance(c, dict):
            continue
        for wl in (c.get("knowledge") or {}).get("will_learn", []):
            lac = wl.get("learn_at_cluster")
            # 2026-05-30 北极星复审：learn_at_cluster 是 cluster ID 不是章号——用 cluster_id_to_range 取末章。
            _lrng = _cl.cluster_id_to_range(project_root, lac) if (_cl and isinstance(lac, str)) else None
            learn_hi = int(_lrng[1]) if _lrng and len(_lrng) == 2 else None
            if learn_hi is not None and learn_hi <= last_ch:
                overdue_wl.append({"character": c.get("name") or c.get("id"), "fact": wl.get("fact", "")[:40], "learn_at_cluster": lac})
    if overdue_wl:
        findings.append({
            "severity": "advisory",
            "code": "WILL_LEARN_NOT_TRIGGERED",
            "metric": {"count": len(overdue_wl), "samples": overdue_wl[:3]},
            "message": f"{len(overdue_wl)} 条 will_learn 已过 learn_at_cluster 但未移入 knows",
            "suggestion": "writer 在对应章节写 knowledge_gained，cluster-save-state 自动从 will_learn 移到 knows",
        })

    # ===== 输出 =====
    out_dir = db / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "declarative_data",
        "scan_ts": ts,
        "last_chapter": last_ch,
        "findings": findings,
        "summary": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"declarative_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[cross_chapter_declarative_scan] 截至 ch{last_ch}")
    print(f"  关系数值条目: {len(rels)}")
    print(f"  faction_standings: {len(standings)} 项")
    print(f"  pending/triggered events: {len(pending)}/{len(triggered)}")
    print(f"  travel_log: {len(travel_log)} 条")
    print(f"  secrets 状态: {len([s for s in secrets if s.get('status') == 'hidden'])} hidden / {len([s for s in secrets if s.get('status') != 'hidden'])} 已流转")
    print(f"  will_learn 未触发: {len(overdue_wl)}")
    print()
    print(f"=== 发现 {len(findings)} 项 (warning={report['summary']['warning']} / advisory={report['summary']['advisory']}) ===")
    for f in findings:
        print(f"  [{f['severity'].upper()}] [{f['code']}] :: {f['message']}")
        print(f"     建议: {f['suggestion']}")
    print()
    print(f"报告: {out_path}")

    # 2026-06 退出码契约对齐 [L6/SC-2]：warning 级发现统一 exit 2、advisory 统一 exit 1（旧版 warning 误用 exit 1 → 被调度器当 advisory 丢弃）。
    if any(f["severity"] == "warning" for f in findings):
        sys.exit(2)
    if any(f["severity"] == "advisory" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
