#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dramatic_question_lifecycle_scanner.py — 戏剧问题（PITQ/MDQ）生命周期检测
(advisory · cluster · 2026-06-29)

🔴 2026-06-29 戏剧问题账本(PITQ/MDQ)

【缺口】读者粘性宏观结构缺口（读者粘性_提案.json designs[0] · P0）：全系统此前无任何
dramatic_question / open_question 结构化 tracker。读者追读是因为「想知道某个二元核心问题
的答案」(Cambridge 2026 PITQ「Potentially Interrogative Terminable Question」、McKee Major
Dramatic Question、Loewenstein 1994 信息缺口)。若渝只有段级 info gap，没有跨 cluster 追踪
「open question 何时提出→何时回答」的生命周期。本 scanner 补这一闭环。

【数据源】_数据库/戏剧问题账本.json（A agent 由 cluster-save-state 读正文后登记 · schema）：
  {clusters: {<cluster_id>: {
     raised:   [{qid, question, scope(cluster|volume|series), raised_at_scene, expected_payoff_window}],
     answered: [{qid, answered_at_scene}]}}}
  · clusters 是 dict（按 cluster_id 键），不是 list。
  · qid 全局唯一。open_question = raised 过但尚未 answered 的 qid。

【做法 · 确定性零 LLM · cluster_lookup 反查（禁 cluster_{ch:03d}）】
  累计到目标 cluster（num <= T）算 open_questions = raised(qid) − answered(qid)，查三类 → advisory：
   ① NO_OPEN_DRAMATIC_QUESTION —— 目标 cluster 结束时 open 集合为空（纯过场·无追读拉力）。
      （含可选「章末无 open question」语义·章是格式层不单独判·折叠进 cluster 视野·北极星④）
   ② DRAMATIC_QUESTION_STALE —— 某 open question 悬挂超 N cluster 未碰（烂尾感·N 默认 8·可配）。
   ③ OPEN_CLOSE_IMBALANCE —— 只开坑不闭合（闭合率过低 + open 积压过多 = Zeigarnik 反面毒点·
      读者 frustration 弃读）。Zeigarnik 张力须给足闭合·绝非越多 open 越好。

【北极星② / ④ / ⑤ 顾问非法官】open question 数量是创作工艺（慢热文学/严肃文学可少钩）·
  作者档第一权威·占位 regex/登记易误报反问修辞问 → 全 advisory，三个 code
  **绝不进 audit_hub.HARD_GATE_CODES**。env DRAMATIC_QUESTION_LIFECYCLE_MODE:
  off / shadow(默认·只记不判) / active。默认安全：无账本/旧书 → 零检测零行为变化。

用法：
  python dramatic_question_lifecycle_scanner.py [draft_path] --project <root> [--cluster <id>] [--stale-n N]
  --cluster 省略 → 从 draft_path 文件名解析 cluster_(\\d+)；都无 → 取账本里最大 cluster num。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# cluster_lookup = 章号⇄cluster_id 唯一权威反查（北极星①·禁 cluster_{ch:03d} 机械拼接）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup  # noqa: E402

ISSUE_CODE_NO_OPEN = "NO_OPEN_DRAMATIC_QUESTION"
ISSUE_CODE_STALE = "DRAMATIC_QUESTION_STALE"
ISSUE_CODE_IMBALANCE = "OPEN_CLOSE_IMBALANCE"

LEDGER_NAME = "戏剧问题账本.json"

DEFAULT_STALE_N = 8            # 某 open question 悬挂超 N cluster 未碰 → 烂尾感（宽阈·北极星⑤）
IMBALANCE_MIN_RAISED = 5       # 闭合率判定的最小已开坑数（样本太少不判）
IMBALANCE_OPEN_BACKLOG = 6     # open 积压数阈值（积压且闭合率低才报）
IMBALANCE_CLOSE_RATIO = 0.20   # 闭合率 floor（answered/raised < 此值 = 只开坑不闭合）


def _mode() -> str:
    m = (os.environ.get("DRAMATIC_QUESTION_LIFECYCLE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_ledger(project_root) -> dict | None:
    if not project_root:
        return None
    root = Path(project_root)
    p = root / "_数据库" / LEDGER_NAME if root.name != "_数据库" else root / LEDGER_NAME
    if not p.exists():
        return None
    obj = _read_json(p)
    return obj if isinstance(obj, dict) else None


def _cluster_num(cid) -> int | None:
    """cluster_id → 数字序号（唯一权威反查·禁机械拼接）。"""
    return cluster_lookup.cluster_num(cid)


def _derive_target_num(draft_path, cluster_arg, ledger: dict) -> int | None:
    """确定目标 cluster 序号：--cluster > draft 文件名 cluster_(\\d+) > 账本最大 num。"""
    if cluster_arg:
        n = _cluster_num(cluster_arg)
        if n is not None:
            return n
    if draft_path:
        m = re.search(r"cluster[_\-]?0*(\d+)", str(draft_path))
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    # 账本里最大 cluster num（最近完成的 cluster）
    nums = [n for n in (_cluster_num(cid) for cid in (ledger.get("clusters") or {}))
            if n is not None]
    return max(nums) if nums else None


def compute_open_questions(ledger: dict, target_num: int) -> dict:
    """累计到 target_num（含）的 open_questions（raised − answered，按 qid 去重·取最早 raised）。

    返回 {open: [{qid, question, raised_at_num, scope, expected_payoff_window, staleness}],
          raised_unique, answered_unique, close_ratio}。
    """
    clusters = ledger.get("clusters")
    if not isinstance(clusters, dict):
        return {"open": [], "raised_unique": 0, "answered_unique": 0, "close_ratio": 0.0}

    raised_first: dict[str, dict] = {}   # qid -> {raised_at_num, question, scope, expected_payoff_window}
    answered_qids: set[str] = set()

    for cid, payload in clusters.items():
        cnum = _cluster_num(cid)
        if cnum is None or cnum > target_num:
            continue
        if not isinstance(payload, dict):
            continue
        for r in payload.get("raised") or []:
            if not isinstance(r, dict):
                continue
            qid = r.get("qid")
            if not qid:
                continue
            qid = str(qid)
            # 同 qid 多次 raised → 保留最早出现的 cluster
            if qid not in raised_first or cnum < raised_first[qid]["raised_at_num"]:
                raised_first[qid] = {
                    "raised_at_num": cnum,
                    "question": str(r.get("question") or ""),
                    "scope": str(r.get("scope") or "cluster"),
                    "expected_payoff_window": str(r.get("expected_payoff_window") or ""),
                }
        for a in payload.get("answered") or []:
            if not isinstance(a, dict):
                continue
            qid = a.get("qid")
            if qid:
                answered_qids.add(str(qid))

    open_qs = []
    for qid, info in raised_first.items():
        if qid in answered_qids:
            continue
        open_qs.append({
            "qid": qid,
            "question": info["question"],
            "raised_at_num": info["raised_at_num"],
            "scope": info["scope"],
            "expected_payoff_window": info["expected_payoff_window"],
            "staleness": target_num - info["raised_at_num"],
        })
    # 紧迫度：staleness 大者优先（更久未碰）
    open_qs.sort(key=lambda q: (-q["staleness"], q["raised_at_num"]))

    raised_unique = len(raised_first)
    answered_unique = len(answered_qids & set(raised_first.keys()))
    close_ratio = (answered_unique / raised_unique) if raised_unique else 0.0
    return {
        "open": open_qs,
        "raised_unique": raised_unique,
        "answered_unique": answered_unique,
        "close_ratio": round(close_ratio, 3),
    }


def scan(project_root, cluster_id=None, draft_path=None, stale_n=DEFAULT_STALE_N) -> dict:
    """戏剧问题生命周期检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "dramatic_question_lifecycle", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "verdict": "PASS", "violations": [], "warning": None}
    if mode == "off":
        return out

    ledger = _load_ledger(project_root)
    if ledger is None:
        out["note"] = f"无 {LEDGER_NAME}（旧书/未登记）·默认安全跳过"
        return out

    target_num = _derive_target_num(draft_path, cluster_id, ledger)
    if target_num is None:
        out["note"] = "账本无可解析 cluster·跳过"
        return out
    out["target_cluster_num"] = target_num

    metrics = compute_open_questions(ledger, target_num)
    out["metrics"] = {
        "open_count": len(metrics["open"]),
        "raised_unique": metrics["raised_unique"],
        "answered_unique": metrics["answered_unique"],
        "close_ratio": metrics["close_ratio"],
        "open_sample": [
            {"qid": q["qid"], "question": q["question"][:40], "staleness": q["staleness"]}
            for q in metrics["open"][:5]
        ],
    }

    # 账本对该 cluster 完全没登记任何 raised/answered（且无累计 open）→ 多半未登记·不误报
    if metrics["raised_unique"] == 0:
        out["note"] = "账本累计无任何 raised question（未登记/慢热开篇）·跳过"
        return out

    violations = []

    # ① 整 cluster 无任何 open PITQ（纯过场·无追读拉力）·折叠章末无 open question 语义（北极星④章是格式）
    if len(metrics["open"]) == 0:
        violations.append({
            "code": ISSUE_CODE_NO_OPEN, "kind": "dramatic_question_lifecycle",
            "severity": "minor",
            "message": (f"cluster_{target_num:03d} 结束时无任何悬而未决的核心问题（open PITQ=0）·"
                        f"纯过场缺追读拉力。建议本块抛出 1 个具体二元问题（读者想知道答案）牵引下文"),
            "_doc": "Cambridge 2026 PITQ / McKee MDQ·慢热文学可少钩·advisory·绝不 hard_gate"})

    # ② 某 open question 悬挂超 N cluster 未碰（烂尾感）
    stale = [q for q in metrics["open"] if q["staleness"] >= stale_n]
    if stale:
        samples = [{"qid": q["qid"], "question": q["question"][:40],
                    "staleness": q["staleness"], "raised_at": f"cluster_{q['raised_at_num']:03d}"}
                   for q in stale[:4]]
        violations.append({
            "code": ISSUE_CODE_STALE, "kind": "dramatic_question_lifecycle",
            "severity": "minor",
            "message": (f"{len(stale)} 个核心问题悬挂 ≥{stale_n} cluster 未推进（最久 "
                        f"{stale[0]['staleness']} cluster·『{stale[0]['question'][:30]}』）·"
                        f"烂尾感风险。建议推进或部分揭示（哪怕给一点新线索）"),
            "stale_count": len(stale), "stale_n": stale_n, "samples": samples,
            "_doc": "悬挂过久 = 读者忘记/失去耐心·advisory·作者档可豁免长线伏笔"})

    # ③ 闭合率：只开坑不闭合（Zeigarnik 反面毒点）——积压多 + 闭合率低才报
    if (metrics["raised_unique"] >= IMBALANCE_MIN_RAISED
            and len(metrics["open"]) >= IMBALANCE_OPEN_BACKLOG
            and metrics["close_ratio"] < IMBALANCE_CLOSE_RATIO):
        violations.append({
            "code": ISSUE_CODE_IMBALANCE, "kind": "dramatic_question_lifecycle",
            "severity": "minor",
            "message": (f"只开坑不闭合（已开 {metrics['raised_unique']} 个核心问题·闭合 "
                        f"{metrics['answered_unique']} 个·闭合率 {metrics['close_ratio']:.0%}"
                        f"·open 积压 {len(metrics['open'])} 个）。Zeigarnik 张力须给足闭合·"
                        f"积压过多 = 读者 frustration 弃读。建议先回答/收束部分旧问题再开新坑"),
            "close_ratio": metrics["close_ratio"],
            "open_backlog": len(metrics["open"]),
            "_doc": "Zeigarnik 反面·闭合率过低 = 虚假悬念毒点·advisory·绝不 hard_gate"})

    out["violations_count"] = len(violations)
    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = " · ".join(v["message"] for v in violations)
        else:
            for v in violations:
                print(f"[SHADOW] dramatic_question_lifecycle: {v['message']} — 不上报",
                      file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(
        description="戏剧问题(PITQ/MDQ)生命周期检测（advisory · cluster）")
    ap.add_argument("draft_path", nargs="?", default=None,
                    help="占位兼容 audit_hub 风格(本 scanner 实际读 戏剧问题账本.json·"
                         "draft 文件名仅用于解析当前 cluster num)")
    ap.add_argument("--project", default=None, required=False)
    ap.add_argument("--cluster", default=None, help="cluster_id（省略=从 draft 名解析或取账本最大 num）")
    ap.add_argument("--stale-n", type=int, default=DEFAULT_STALE_N,
                    help=f"悬挂超 N cluster 判 stale（默认 {DEFAULT_STALE_N}）")
    ap.add_argument("--manifest", default=None)  # audit_hub 兼容占位
    ap.add_argument("--style", default=None)      # audit_hub 兼容占位
    args, _ = ap.parse_known_args()
    rep = scan(args.project, args.cluster, args.draft_path, args.stale_n)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
