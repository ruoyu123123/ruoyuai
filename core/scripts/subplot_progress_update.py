"""subplot_progress_update.py — 从 cluster 摘要推进 subplot_threads + 四线脉络

G3 调研发现: subplot_threads.json + 四线脉络.json 只在 outline 写一次后零写回。

机制(零 LLM · 确定性):
- 读 故事块摘要.json 的 cluster 摘要
- 如果摘要提及 subplot thread → 标 thread.last_cluster / thread.status
- 如果超过 5 cluster 没提及某 thread → 标 thread.status = "dormant"

接入点: save-state step9
exit 0: advisory · 不阻断

用法:
  python core/scripts/subplot_progress_update.py <project_root> --cluster <cluster_id>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def update(project_root: Path, cluster_id: str) -> dict:
    """从故事块摘要推进 subplot_threads 状态。"""
    db = project_root / "_数据库"
    sub_path = db / "subplot_threads.json"
    summary_path = db / "故事块摘要.json"
    throughline_path = db / "四线脉络.json"

    sub = _load(sub_path)
    summary = _load(summary_path)

    if not sub.get("threads"):
        sub.setdefault("threads", [])

    # 拿当前 cluster 摘要文本
    cluster_summary_text = ""
    for c in summary.get("clusters", []):
        if c.get("cluster_id") == cluster_id:
            cluster_summary_text = json.dumps(c, ensure_ascii=False)
            break

    updated = 0
    ts = datetime.now().isoformat(timespec="seconds")

    for thread in sub.get("threads", []):
        if isinstance(thread, str):
            # 兼容字符串列表 schema：str 元素只能读名匹配计数，不回写状态
            if thread and thread in cluster_summary_text:
                updated += 1
            continue
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("id", "")
        thread_name = thread.get("name", thread_id)
        # 简单关键词匹配: thread 名/id 出现在摘要中
        if thread_name and thread_name in cluster_summary_text:
            thread["last_cluster"] = cluster_id
            thread["last_updated"] = ts
            if thread.get("status") == "dormant":
                thread["status"] = "active"
            updated += 1

    # dormant 检测: 超过 5 cluster 没出现
    all_clusters = [c.get("cluster_id", "") for c in summary.get("clusters", [])]
    if len(all_clusters) >= 5:
        for thread in sub.get("threads", []):
            if not isinstance(thread, dict):
                continue
            last = thread.get("last_cluster", "")
            if last and last in all_clusters:
                idx = all_clusters.index(last)
                gap = len(all_clusters) - 1 - idx
                if gap >= 5 and thread.get("status") != "dormant":
                    thread["status"] = "dormant"
                    thread["dormant_since"] = ts
                    updated += 1

    if updated > 0:
        _save(sub_path, sub)

    # 四线脉络同理
    tl = _load(throughline_path)
    tl_updated = 0
    for line in tl.get("throughlines", []):
        # 🔴 G3 e2e 修：四线脉络 schema 可能存成字符串列表（走向线 = str），
        # 也可能是 dict 列表。line 是 str 时直接当线名；是 dict 时取 name。
        # 原 line.get(...) 对 str 抛 'str' object has no attribute 'get'。
        if isinstance(line, str):
            line_name = line
            if line_name and line_name in cluster_summary_text:
                tl_updated += 1          # str 元素只读不可回写状态（保持 schema 不变）
            continue
        if not isinstance(line, dict):
            continue
        line_name = line.get("name", "")
        if line_name and line_name in cluster_summary_text:
            line["last_cluster"] = cluster_id
            line["last_updated"] = ts
            tl_updated += 1
    if tl_updated > 0:
        _save(throughline_path, tl)

    return {"subplot_updated": updated, "throughline_updated": tl_updated}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="从 cluster 摘要推进 subplot + 四线脉络")
    ap.add_argument("project_root")
    ap.add_argument("--cluster", required=True)
    args = ap.parse_args()

    r = update(Path(args.project_root), args.cluster)
    total = r["subplot_updated"] + r["throughline_updated"]
    if total:
        print(f"[subplot_progress] 更新 {r['subplot_updated']} threads + {r['throughline_updated']} throughlines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
