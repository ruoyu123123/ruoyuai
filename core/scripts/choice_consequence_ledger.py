#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""choice_consequence_ledger.py — 用户走向选择延迟可见性账本 · R25 W13 Batch-MM · P1

【缺口 · 互动小说 BG3 reactivity + Emily Short delayed-consequence + WHAT-IF
arxiv 2412.10582】
用户在 cluster-save-state 选某条走向后系统记账：选择内容、利害层级、预期
可见 cluster 窗口、预期 resonance keywords。每 cluster 完成时跑
choice_consequence_visibility_scanner：
  · 窗口内文本/storyboard/锁定事实是否含 resonance_keywords
  · 命中 → 已显化（visible）
  · 未命中且窗口未到 → 等待（pending）
  · 窗口到期仍未命中 → starving_choices advisory · 给 outline-planner 加
    callback 钩子

【命名空间】与 foreshadowing_handoff(author_planted) 分立：
  · author_planted = 作者埋伏笔 → 主动回收
  · choice_consequence = 用户选择后果 → 反应式回响

【schema · _数据库/选择账本.json】
  {
    "_schema_version": "1.0",
    "_placeholder": true,
    "_namespace": "choice_consequence",
    "entries": [
      {"cluster_id": "cluster_005",
       "choice_key": "card_b",
       "choice_summary": "主角决定揭露 X",
       "stakes_tier": "faction",   # life / faction / moral / preference
       "expected_visibility_window": 3,   # 1-5 cluster
       "expected_resonance_keywords": ["仇视", "X 派", "排挤"],
       "created_at_cluster": "cluster_005",
       "expires_at_cluster_idx": 8,
       "status": "pending"   # pending/visible/expired
      }
    ]
  }

【两 advisory · scanner】
  · CHOICE_CONSEQUENCE_STARVING       — 过期未呼应
  · CHOICE_CONSEQUENCE_VISIBLE        — 已显化 · info
  · CHOICE_CONSEQUENCE_PENDING        — 等待中 · info

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  CHOICE_CONSEQUENCE_* 绝不进 audit_hub.HARD_GATE_CODES。

env CHOICE_CONSEQUENCE_MODE: off / shadow（默认） / active

用法（ledger CLI）:
  python choice_consequence_ledger.py append <project> <cluster_id> --choice-key <k>
         --summary <s> --tier <t> --window <n> --keywords <a,b,c>
  python choice_consequence_ledger.py view <project> [--cluster <id>]
  python choice_consequence_ledger.py scan <project> <cluster_id> <draft_path>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

ISSUE_CODE_STARVING = "CHOICE_CONSEQUENCE_STARVING"
ISSUE_CODE_VISIBLE = "CHOICE_CONSEQUENCE_VISIBLE"
ISSUE_CODE_PENDING = "CHOICE_CONSEQUENCE_PENDING"

_LEDGER_FILE = "选择账本.json"
_NAMESPACE = "choice_consequence"
_STAKES_TIERS = ("life", "faction", "moral", "preference")

SEMANTIC_RESONANCE_SIM_THRESHOLD = 0.52   # choice 摘要/关键词 vs 正文段落余弦阈值
# 金标准校准 2026-07-04：content_embed_separability_20260704 报告 neg_p95=0.5165/Youden=0.4904


def _mode() -> str:
    m = (os.environ.get("CHOICE_CONSEQUENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


# ── 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）───────
def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available·
    替代旧的按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测的 _has_real_embedding_backend）。

    import 失败 → False（调用方回退字面 keyword-in-text）。
    """
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


def _build_semantic_context(text: str) -> "dict | None":
    """内容后端就绪时把正文预切段 + 批量编码一次，供本次 scan() 内所有 ledger entry
    复用（避免每条 entry 都重复编码同一正文·2026-07-02）。

    内容后端不可用 / 无有效段落 / 编码异常 → None（调用方逐条回退字面 keyword-in-text）。
    """
    if not _content_backend_ready():
        return None
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) >= 10]
    if not paras:
        return None
    try:
        from embedding_store import compute_content_embedding
        embs = [compute_content_embedding(p) for p in paras]
        return {"paragraphs": paras, "embeddings": embs}
    except Exception:
        return None


def _semantic_resonance(choice_summary: str, keywords: list, ctx) -> "dict | None":
    """内容后端下：choice 摘要/关键词 vs 正文段落 embedding 余弦补充判定（意译呼应漏检）。

    命中最相似段落 → {"paragraph_index", "similarity"}；ctx 为 None（无内容后端/无段落）
    / 查询文本为空 / 计算异常 → None（调用方回退字面 keyword-in-text · _check_resonance
    仍是兜底 · 字面命中永远优先）。
    """
    if not ctx:
        return None
    query = " ".join([choice_summary or ""] + list(keywords or [])).strip()
    if not query:
        return None
    try:
        from embedding_store import compute_content_embedding, cosine_similarity
        qe = compute_content_embedding(query)
        if not qe:
            return None
        best_idx, best_sim = -1, -1.0
        for i, pe in enumerate(ctx["embeddings"]):
            if pe and len(pe) == len(qe):
                sim = cosine_similarity(qe, pe)
                if sim > best_sim:
                    best_idx, best_sim = i, sim
        if best_idx >= 0 and best_sim >= SEMANTIC_RESONANCE_SIM_THRESHOLD:
            return {"paragraph_index": best_idx, "similarity": round(best_sim, 4)}
    except Exception:
        return None
    return None


def _ledger_path(project_root) -> Path:
    return Path(project_root) / "_数据库" / _LEDGER_FILE


def _load_ledger(project_root) -> dict:
    p = _ledger_path(project_root)
    if not p.exists():
        return {"_schema_version": "1.0", "_placeholder": True,
                "_namespace": _NAMESPACE, "entries": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"_schema_version": "1.0", "_placeholder": True,
                "_namespace": _NAMESPACE, "entries": []}


def _save_ledger(project_root, data) -> None:
    p = _ledger_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _cluster_idx(cluster_id: str) -> int:
    """cluster_005 → 5。"""
    m = re.search(r"(\d+)", cluster_id or "")
    return int(m.group(1)) if m else 0


def append_entry(project_root, cluster_id, choice_key, choice_summary,
                 stakes_tier, window, resonance_keywords) -> dict:
    if stakes_tier not in _STAKES_TIERS:
        raise ValueError(f"stakes_tier 必须为 {_STAKES_TIERS}")
    window = max(1, min(5, int(window)))
    cur_idx = _cluster_idx(cluster_id)
    entry = {
        "cluster_id": cluster_id,
        "choice_key": choice_key,
        "choice_summary": choice_summary,
        "stakes_tier": stakes_tier,
        "expected_visibility_window": window,
        "expected_resonance_keywords": list(resonance_keywords),
        "created_at_cluster": cluster_id,
        "expires_at_cluster_idx": cur_idx + window,
        "status": "pending",
    }
    data = _load_ledger(project_root)
    data["entries"].append(entry)
    _save_ledger(project_root, data)
    return entry


def view_entries(project_root, cluster_id=None) -> list:
    data = _load_ledger(project_root)
    entries = data.get("entries", [])
    if cluster_id:
        entries = [e for e in entries if e.get("cluster_id") == cluster_id]
    return entries


def _check_resonance(text: str, keywords: list) -> list:
    return [w for w in keywords if w and w in text]


def scan(project_root, cluster_id, draft_path) -> dict:
    mode = _mode()
    out = {
        "scanner": "choice_consequence_visibility_scanner", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": True, "_namespace": _NAMESPACE,
    }
    if mode == "off":
        return out

    cur_idx = _cluster_idx(cluster_id)
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out

    data = _load_ledger(project_root)
    entries = data.get("entries", [])
    starving, visible, pending = [], [], []
    updated = False

    # 🔴 2026-07-03 Wave-4：正文段落 + 全部待判定 entry 的 choice 摘要/关键词查询
    # 两侧文本一次性 prefetch（真后端子进程按条调用极贵·合并成一次批调用）——
    # 下面 _build_semantic_context / _semantic_resonance 内逐条 compute_content_embedding 命中缓存。
    if _content_backend_ready():
        try:
            from embedding_store import prefetch_content_embeddings
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) >= 10]
            queries = [
                " ".join([e.get("choice_summary", "")] +
                        list(e.get("expected_resonance_keywords") or [])).strip()
                for e in entries if e.get("status") not in ("visible", "expired")
            ]
            prefetch_content_embeddings(paras + [q for q in queries if q])
        except Exception:
            pass

    # 真后端就绪时正文只切段编码一次（本次 scan() 内所有 entry 复用·2026-07-02）
    semantic_ctx = _build_semantic_context(text)

    for e in entries:
        if e.get("status") in ("visible", "expired"):
            continue
        kw = e.get("expected_resonance_keywords") or []
        # 窗口未到：只检查命中（不报缺）
        # 窗口到期：未命中 → starving
        hits = _check_resonance(text, kw)
        if hits:
            e["status"] = "visible"
            e["visible_at_cluster"] = cluster_id
            e["matched_keywords"] = hits
            e["match_method"] = "literal_substring"
            visible.append(e)
            updated = True
        else:
            # 字面命中优先·未中时真后端补语义（意译呼应漏检）
            semantic = _semantic_resonance(e.get("choice_summary", ""), kw, semantic_ctx)
            if semantic is not None:
                e["status"] = "visible"
                e["visible_at_cluster"] = cluster_id
                e["matched_keywords"] = []
                e["match_method"] = "embedding_cosine"
                e["semantic_similarity"] = semantic["similarity"]
                visible.append(e)
                updated = True
            else:
                expires = e.get("expires_at_cluster_idx", cur_idx + 1)
                if cur_idx >= expires:
                    e["status"] = "starving"
                    starving.append(e)
                    updated = True
                else:
                    pending.append(e)

    if updated:
        _save_ledger(project_root, data)

    out.update({
        "cluster_id": cluster_id,
        "cluster_idx": cur_idx,
        "starving_entries": starving,
        "visible_entries": visible,
        "pending_entries": pending,
        "entry_count": len(entries),
    })

    flags = []
    for e in starving:
        flags.append({"code": ISSUE_CODE_STARVING, "severity": "minor",
                      "msg": (f"choice={e.get('choice_key')} stakes={e.get('stakes_tier')}"
                              f"·窗口到期 cluster_idx={e.get('expires_at_cluster_idx')}"
                              f"·未呼应"),
                      "entry_choice": e.get("choice_key"),
                      "entry_cluster": e.get("cluster_id")})
    for e in visible:
        flags.append({"code": ISSUE_CODE_VISIBLE, "severity": "info",
                      "msg": (f"choice={e.get('choice_key')} 已显化"
                              f"·命中 {len(e.get('matched_keywords', []))} kw")})
    if pending:
        flags.append({"code": ISSUE_CODE_PENDING, "severity": "info",
                      "msg": f"{len(pending)} 条 pending · 窗口未到"})

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "choice_consequence",
                "severity": f["severity"],
                "code": f["code"], "message": f["msg"],
                "_doc": "R25 W13 Batch-MM·BG3 reactivity·advisory·绝不 hard_gate",
            })
        minor = [f for f in flags if f["severity"] == "minor"]
        out["verdict"] = "FAIL_MINOR" if minor else "PASS"
        out["warning"] = "·".join(f["msg"] for f in minor[:3]) or None
    elif mode == "shadow":
        minor = [f for f in flags if f["severity"] == "minor"]
        if minor:
            print("[SHADOW] choice_consequence: "
                  + "·".join(f["msg"] for f in minor[:3]) + " — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="choice_consequence_ledger CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append")
    a.add_argument("project")
    a.add_argument("cluster_id")
    a.add_argument("--choice-key", required=True)
    a.add_argument("--summary", required=True)
    a.add_argument("--tier", required=True, choices=_STAKES_TIERS)
    a.add_argument("--window", type=int, required=True)
    a.add_argument("--keywords", required=True,
                   help="逗号分隔 resonance keywords")

    v = sub.add_parser("view")
    v.add_argument("project")
    v.add_argument("--cluster", default=None)

    s = sub.add_parser("scan")
    s.add_argument("project")
    s.add_argument("cluster_id")
    s.add_argument("draft_path")

    args = ap.parse_args()
    if args.cmd == "append":
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        entry = append_entry(args.project, args.cluster_id, args.choice_key,
                             args.summary, args.tier, args.window, kws)
        print(json.dumps(entry, ensure_ascii=False, indent=2))
    elif args.cmd == "view":
        entries = view_entries(args.project, args.cluster)
        print(json.dumps(entries, ensure_ascii=False, indent=2))
    elif args.cmd == "scan":
        rep = scan(args.project, args.cluster_id, args.draft_path)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
