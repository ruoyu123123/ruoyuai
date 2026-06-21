#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intent_ledger.py — per-cluster monitoring 账本 · R24 W12 Batch-LL · P2

【缺口 · AGI 协作 monitoring ledger（reflexivity 反算法议程）】
弱模型/长 cluster 链路里：你 want 的事/实际发生的事/下一 cluster framing 三者
被时间稀释 → 漂移看不见。本账本每 cluster 三行卡片：
  · 你想干什么（writer_intent + scope_summary 提取）
  · 实际发生（cluster 写完后 changes.json + cluster_brief 实状态）
  · 与下一 cluster framing 漂移（emergence brief vs intent 关键词 jaccard）
append-only jsonl · 只读不修改 cluster_brief / 世界状态 / 大势卡。

【做法 · 确定性 · 零 LLM/零联网】
  · CLI `append <project> <cluster_key>` 串三源:
       writer_intent_anchor.load_anchor (4 字段·若存在)
       cluster_brief.scope_summary
       cluster_brief.emergence brief（下一 cluster 候选 · 若存在）
  · 三行卡片格式化 + 漂移占位指标（char-Jaccard {want_keys, next_framing_keys}）
  · 写 _数据库/.intent_ledger.jsonl append-only
  · CLI `view <project> [--last N=5]` 滚动回看 N 行
  · CLI `summary <project>` 全本累积统计

【写入路径】<project>/_数据库/.intent_ledger.jsonl

【北极星】② cluster 单位 · ⑤ 只读不干涉模型判断 · ⑥ 清旧码
  本账本只追加观测·绝不修改 cluster_brief / 世界状态 / 大势卡 / 用户偏好。

用法:
  python intent_ledger.py append <project> <cluster_key> [--actual <jsonpath>]
  python intent_ledger.py view <project> [--last N]
  python intent_ledger.py summary <project>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_LEDGER_FILENAME = ".intent_ledger.jsonl"
_SCHEMA_VERSION = "1.0"


def _ledger_path(project_root) -> Path:
    return Path(project_root) / "_数据库" / _LEDGER_FILENAME


def _safe_read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_writer_intent(project_root, cluster_key) -> dict:
    """SHA-256 校验过的盲意图卡（fail = 缺/篡改 → 返回 {}）"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import writer_intent_anchor as wia
        a = wia.load_anchor(project_root, cluster_key)
        if a is None:
            return {}
        return {k: a.get(k, "") for k in ("want", "antagonist", "stake", "tone_word")}
    except (ImportError, OSError, ValueError):
        return {}


def _load_cluster_brief(project_root, cluster_key) -> dict:
    """从 _数据库/事件簇.json 取目标 cluster·scope_summary + emergence brief。"""
    p = Path(project_root) / "_数据库" / "事件簇.json"
    data = _safe_read_json(p) or {}
    clusters = data.get("clusters") or {}
    if cluster_key in clusters:
        c = clusters[cluster_key]
    else:
        # 容错：cluster_001 / 001 / cluster_X 等
        c = clusters.get(f"cluster_{cluster_key}", {})
    if not isinstance(c, dict):
        return {}
    return {
        "scope_summary": c.get("scope_summary", "") or "",
        "scene_storyboard": c.get("scene_storyboard", []) or [],
        "emergence_brief": c.get("emergence_brief", "") or c.get("brief", "") or "",
    }


def _load_actual_state(project_root, cluster_key, override_path=None) -> dict:
    """从 cluster_<key>_changes.json / draft 摘要拿实际发生。

    优先 override_path（CLI 显式指）·否则约定 章节/cluster_<key>_draft/cluster_<key>_changes.json。
    缺失返回 {}（不崩）。
    """
    if override_path:
        d = _safe_read_json(Path(override_path)) or {}
        return d
    # 默认查约定路径
    cand = (Path(project_root) / "章节"
            / f"cluster_{cluster_key}_draft" / f"cluster_{cluster_key}_changes.json")
    return _safe_read_json(cand) or {}


def _next_cluster_emergence(project_root, cluster_key) -> str:
    """从 事件簇.json 取下一 cluster brief（emergence 涌现的 framing）。"""
    p = Path(project_root) / "_数据库" / "事件簇.json"
    data = _safe_read_json(p) or {}
    clusters = data.get("clusters") or {}
    # 简单求下一 key（数字递增）
    try:
        n = int(cluster_key.lstrip("0") or "0")
    except ValueError:
        return ""
    nxt = f"{n + 1:03d}"
    c = clusters.get(nxt) or clusters.get(f"cluster_{nxt}", {})
    if not isinstance(c, dict):
        return ""
    return c.get("emergence_brief", "") or c.get("brief", "") or ""


def _extract_keys(text: str, top_k=8) -> set:
    """从文本里抽 top-K 高频 CJK 2-gram 当 framing 关键词（占位·零依赖）。"""
    if not isinstance(text, str) or not text:
        return set()
    cjk = "".join(ch for ch in text if "一" <= ch <= "鿿")
    if len(cjk) < 4:
        return set()
    counts = {}
    for i in range(len(cjk) - 1):
        bg = cjk[i:i + 2]
        counts[bg] = counts.get(bg, 0) + 1
    top = sorted(counts.items(), key=lambda x: -x[1])[:top_k]
    return {bg for bg, _ in top}


def _drift_score(want_text: str, next_framing: str) -> float:
    """want_text vs next_cluster framing 的 2-gram Jaccard 反相似度（漂移度）。

    返回 0-1·0=完全对齐·1=完全偏离。"""
    a = _extract_keys(want_text)
    b = _extract_keys(next_framing)
    if not a or not b:
        return 0.0  # 缺侧 → 无法评估 → 0
    inter = a & b
    union = a | b
    if not union:
        return 0.0
    return round(1.0 - len(inter) / len(union), 4)


def _build_card(project_root, cluster_key, actual_override=None) -> dict:
    intent = _load_writer_intent(project_root, cluster_key)
    brief = _load_cluster_brief(project_root, cluster_key)
    actual = _load_actual_state(project_root, cluster_key, actual_override)
    next_brief = _next_cluster_emergence(project_root, cluster_key)

    want_text = (intent.get("want", "") + " "
                 + brief.get("scope_summary", "")).strip()
    actual_text = ""
    if isinstance(actual, dict):
        # 兼容 changes.json 常见字段
        parts = []
        for k in ("summary", "facts_added", "key_event", "scope_summary"):
            v = actual.get(k)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(x for x in v if isinstance(x, str))
        actual_text = " ".join(parts).strip()

    drift = _drift_score(want_text, next_brief)
    return {
        "_v": _SCHEMA_VERSION,
        "_ts": int(time.time()),
        "cluster_key": cluster_key,
        "intent": intent,
        "scope_summary": brief.get("scope_summary", ""),
        "actual_summary": actual_text,
        "next_cluster_framing": next_brief,
        "drift_score": drift,
    }


def _format_card_lines(card: dict) -> list:
    intent = card.get("intent", {}) or {}
    want = intent.get("want", "") or card.get("scope_summary", "")
    actual = card.get("actual_summary", "")
    nxt = card.get("next_cluster_framing", "")
    drift = card.get("drift_score", 0.0)
    return [
        f"[cluster_{card['cluster_key']}] 你想干什么: {want[:120]}",
        f"[cluster_{card['cluster_key']}] 实际发生: {actual[:120] or '(缺)'}",
        f"[cluster_{card['cluster_key']}] 下一 framing 漂移: {drift:.2f} | {nxt[:80] or '(无)'}",
    ]


def _append_jsonl(path: Path, payload: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def cmd_append(args) -> int:
    project = Path(args.project)
    cluster_key = args.cluster_key
    card = _build_card(project, cluster_key, args.actual)
    p = _ledger_path(project)
    ok = _append_jsonl(p, card)
    if not ok:
        print(f"[intent_ledger] append 失败: {p}", file=sys.stderr)
        return 2
    lines = _format_card_lines(card)
    for line in lines:
        print(line)
    print(json.dumps({"ok": True, "path": str(p),
                      "drift_score": card["drift_score"]},
                     ensure_ascii=False))
    return 0


def cmd_view(args) -> int:
    p = _ledger_path(Path(args.project))
    if not p.exists():
        print(json.dumps({"ok": False, "exists": False}, ensure_ascii=False))
        return 1
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        print(f"[intent_ledger] 读账本失败: {e}", file=sys.stderr)
        return 2
    last = args.last
    rows = []
    for line in lines[-last:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            rows.append(rec)
        except json.JSONDecodeError:
            continue
    for rec in rows:
        for ln in _format_card_lines(rec):
            print(ln)
    print(json.dumps({"ok": True, "rows": len(rows)}, ensure_ascii=False))
    return 0


def cmd_summary(args) -> int:
    p = _ledger_path(Path(args.project))
    summary = {"ok": True, "exists": p.exists(),
               "rows": 0, "mean_drift": 0.0,
               "high_drift_count": 0, "_threshold": 0.5}
    if not p.exists():
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    drifts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            d = rec.get("drift_score")
            if isinstance(d, (int, float)):
                drifts.append(float(d))
        except json.JSONDecodeError:
            continue
    if drifts:
        summary["rows"] = len(drifts)
        summary["mean_drift"] = round(sum(drifts) / len(drifts), 4)
        summary["high_drift_count"] = sum(1 for d in drifts if d > 0.5)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="per-cluster monitoring 账本")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("append", help="append per-cluster 卡片")
    pa.add_argument("project")
    pa.add_argument("cluster_key")
    pa.add_argument("--actual", default=None,
                    help="changes.json 路径覆盖（默认 章节/cluster_<key>_draft/cluster_<key>_changes.json）")
    pa.set_defaults(func=cmd_append)

    pv = sub.add_parser("view", help="view last N 行")
    pv.add_argument("project")
    pv.add_argument("--last", type=int, default=5)
    pv.set_defaults(func=cmd_view)

    ps = sub.add_parser("summary", help="全本累积漂移统计")
    ps.add_argument("project")
    ps.set_defaults(func=cmd_summary)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
