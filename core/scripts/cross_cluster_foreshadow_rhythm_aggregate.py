"""按故事块检查伏笔的设置、推进与回收节奏。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr


_CLUSTER_RE = re.compile(r"^cluster_(\d{3,})$")
_ACTIVE_STATUSES = frozenset({"open", "suspended", "consumed"})
_PROGRESS_THRESHOLD = 5
_OVER_REINFORCEMENT_THRESHOLD = 8


class ForeshadowContractError(ValueError):
    """伏笔表不符合当前故事块合同。"""


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ForeshadowContractError(f"无法读取伏笔表: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ForeshadowContractError(f"伏笔表必须是 UTF-8 无 BOM: {path}")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ForeshadowContractError(f"伏笔表不是合法 UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ForeshadowContractError("伏笔表顶层必须是 object")
    return value


def _cluster_number(value: object, field: str) -> int:
    if not isinstance(value, str):
        raise ForeshadowContractError(f"{field} 必须是 cluster_id")
    match = _CLUSTER_RE.fullmatch(value)
    if not match:
        raise ForeshadowContractError(f"{field} 不是规范 cluster_id: {value!r}")
    return int(match.group(1))


def _validate_promise(promise: object, index: int) -> dict:
    if not isinstance(promise, dict):
        raise ForeshadowContractError(f"promises[{index}] 必须是 object")
    promise_id = promise.get("id")
    if not isinstance(promise_id, str) or not promise_id.strip():
        raise ForeshadowContractError(f"promises[{index}].id 必须是非空字符串")
    status = promise.get("status")
    if status not in _ACTIVE_STATUSES:
        raise ForeshadowContractError(
            f"promises[{index}].status 必须是 open/suspended/consumed"
        )
    setup = _cluster_number(promise.get("setup_cluster"),
                            f"promises[{index}].setup_cluster")
    due = promise.get("due_by_cluster")
    if due is not None:
        due = _cluster_number(due, f"promises[{index}].due_by_cluster")
        if due < setup:
            raise ForeshadowContractError(
                f"promises[{index}].due_by_cluster 不得早于 setup_cluster"
            )
    consumed = promise.get("consumed_at_cluster")
    if status == "consumed":
        if consumed is None:
            raise ForeshadowContractError(
                f"promises[{index}] status=consumed 必须带 consumed_at_cluster"
            )
        consumed = _cluster_number(
            consumed, f"promises[{index}].consumed_at_cluster"
        )
    elif consumed is not None:
        raise ForeshadowContractError(
            f"promises[{index}] 非 consumed 不得带 consumed_at_cluster"
        )
    progress = promise.get("payoff_progress", [])
    if not isinstance(progress, list):
        raise ForeshadowContractError(
            f"promises[{index}].payoff_progress 必须是 list"
        )
    progress_numbers = []
    for progress_index, cluster_id in enumerate(progress):
        progress_numbers.append(_cluster_number(
            cluster_id,
            f"promises[{index}].payoff_progress[{progress_index}]",
        ))
    return {
        "id": promise_id,
        "status": status,
        "setup": setup,
        "due": due,
        "consumed": consumed,
        "progress": progress_numbers,
    }


def _finding(
    severity: str,
    code: str,
    promise: dict,
    current_cluster_id: str,
    **extra: object,
) -> dict:
    result = {
        "severity": severity,
        "gate_level": "advisory",
        "code": code,
        "id": promise["id"],
        "setup_cluster": f"cluster_{promise['setup']:03d}",
        "current_cluster_id": current_cluster_id,
    }
    result.update(extra)
    return result


def build_report(project_root: Path) -> dict:
    clusters = csr.get_clusters(project_root)
    if not clusters:
        raise ForeshadowContractError("故事块摘要没有已完成 cluster")
    current_cluster_id = str(clusters[-1]["cluster_id"])
    current_number = _cluster_number(current_cluster_id, "故事块摘要.clusters[-1].cluster_id")

    fs_path = project_root / "_数据库" / "伏笔表.json"
    data = _read_json(fs_path)
    promises = data.get("promises")
    if not isinstance(promises, list):
        raise ForeshadowContractError("伏笔表.promises 必须是 list")

    findings: list[dict] = []
    for index, raw_promise in enumerate(promises):
        promise = _validate_promise(raw_promise, index)
        setup = promise["setup"]
        if promise["status"] == "consumed":
            continue
        if setup > current_number:
            continue
        if promise["status"] == "suspended":
            continue

        elapsed = current_number - setup
        due = promise["due"]
        if due is not None and current_number > due:
            overdue = current_number - due
            findings.append(_finding(
                "warning" if overdue >= 5 else "advisory",
                "FORESHADOW_OVERDUE",
                promise,
                current_cluster_id,
                due_by_cluster=f"cluster_{due:03d}",
                overdue_by_clusters=overdue,
            ))

        progress = promise["progress"]
        if (
            elapsed >= _PROGRESS_THRESHOLD
            and not progress
            and (due is None or current_number < due - 2)
        ):
            findings.append(_finding(
                "advisory",
                "FORESHADOW_NO_REINFORCEMENT",
                promise,
                current_cluster_id,
                elapsed_clusters=elapsed,
            ))
        if len(progress) >= _OVER_REINFORCEMENT_THRESHOLD:
            findings.append(_finding(
                "advisory",
                "FORESHADOW_OVER_REINFORCEMENT",
                promise,
                current_cluster_id,
                payoff_progress_count=len(progress),
                payoff_progress_clusters=[f"cluster_{n:03d}" for n in progress],
            ))

    summary = {
        "warning": sum(item["severity"] == "warning" for item in findings),
        "advisory": sum(item["severity"] == "advisory" for item in findings),
        "total": len(findings),
    }
    return {
        "scan_type": "foreshadow_rhythm",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "current_cluster_id": current_cluster_id,
        "clusters_scanned": [str(cluster["cluster_id"]) for cluster in clusters],
        "total_promises": len(promises),
        "findings": findings,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    args = parser.parse_args()
    try:
        project_root = Path(args.project)
        report = build_report(project_root)
        out_dir = project_root / "_数据库" / ".cross_cluster_scan"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"foreshadow_rhythm_{stamp}.json"
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = report["summary"]
        print(
            f"[foreshadow_rhythm] {report['total_promises']} 条伏笔: "
            f"{summary['warning']} warning / {summary['advisory']} advisory"
        )
        print(f"报告: {out_path}")
        return 2 if summary["warning"] else 1 if summary["advisory"] else 0
    except (OSError, ForeshadowContractError, csr.ClusterSummaryError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
