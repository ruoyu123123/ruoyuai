"""检查 cluster-native 数据库投影中的到期未完成项。

只读取已回库数据库，不从 writer 自评推导客观状态。
退出码：0 健康 / 1 advisory / 2 warning。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_state_sources as css


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _completed_ids(project_root: Path) -> list[str]:
    return [cluster_id for cluster_id, _record in css.iter_completed_clusters(project_root)]


def _is_due(cluster_id: str | None, completed: set[str]) -> bool:
    normalized = css.normalize_cluster_id(cluster_id)
    return bool(normalized and normalized in completed)


def scan(project_root: Path) -> dict:
    db = project_root / "_数据库"
    completed_ids = _completed_ids(project_root)
    completed = set(completed_ids)
    findings = []

    events = load_json(db / "事件表.json", {}) or {}
    overdue_events = [
        event for event in events.get("pending_events", []) or []
        if isinstance(event, dict)
        and _is_due(event.get("scheduled_cluster") or event.get("due_cluster"), completed)
    ]
    if overdue_events:
        findings.append({
            "severity": "warning",
            "code": "EVENT_OVERDUE",
            "metric": {"count": len(overdue_events), "ids": [event.get("id") for event in overdue_events[:5]]},
            "message": f"{len(overdue_events)} 条事件已到期但仍在 pending_events",
            "suggestion": "由状态保存链核对事件触发与事件表投影",
        })

    foreshadow = load_json(db / "伏笔表.json", {}) or {}
    overdue_secrets = [
        secret for secret in foreshadow.get("secrets", []) or []
        if isinstance(secret, dict) and secret.get("status") == "hidden"
        and _is_due(secret.get("reveal_at_cluster"), completed)
    ]
    if overdue_secrets:
        findings.append({
            "severity": "warning",
            "code": "SECRET_OVERDUE",
            "metric": {"count": len(overdue_secrets), "ids": [secret.get("id") for secret in overdue_secrets[:5]]},
            "message": f"{len(overdue_secrets)} 条 secret 到期后仍为 hidden",
            "suggestion": "由 foreshadower required 回库步骤核对秘密终态",
        })

    cards = load_json(db / "人物卡.json", {}) or {}
    overdue_knowledge = []
    for character in cards.get("characters", []) or []:
        if not isinstance(character, dict):
            continue
        knowledge = character.get("knowledge") or {}
        known = set(knowledge.get("knows") or [])
        for item in knowledge.get("will_learn", []) or []:
            if not isinstance(item, dict) or not _is_due(item.get("learn_at_cluster"), completed):
                continue
            fact = item.get("fact")
            if fact and fact not in known:
                overdue_knowledge.append({"character": character.get("id") or character.get("name"), "fact": fact})
    if overdue_knowledge:
        findings.append({
            "severity": "advisory",
            "code": "WILL_LEARN_NOT_TRIGGERED",
            "metric": {"count": len(overdue_knowledge), "samples": overdue_knowledge[:5]},
            "message": f"{len(overdue_knowledge)} 条 will_learn 到期后未进入 knows",
            "suggestion": "由 archivist belief_updates 与人物卡投影核对知识传播",
        })

    return {"clusters_scanned": completed_ids, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not (project_root / "_数据库").is_dir():
        print("[FATAL] _数据库 不存在", file=sys.stderr)
        return 2
    result = scan(project_root)
    findings = result["findings"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "declarative_data",
        "scan_ts": timestamp,
        **result,
        "summary": {
            "warning": sum(item["severity"] == "warning" for item in findings),
            "advisory": sum(item["severity"] == "advisory" for item in findings),
            "total": len(findings),
        },
    }
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"declarative_{timestamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"=== 发现 {len(findings)} 项 ===")
    for finding in findings:
        print(f"[{finding['severity'].upper()}] {finding['code']}: {finding['message']}")
    if report["summary"]["warning"]:
        return 2
    return 1 if report["summary"]["advisory"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
