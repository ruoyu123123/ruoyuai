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
import os
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── 🔴 2026-07-02 真语义 embedding 可选路径（照抄 topic_drift_scanner 已验证的模式）───────
def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。

    与 topic_drift_scanner._has_real_embedding_backend 同口径（本仓约定：每个消费
    embedding 的文件自带一份，不互相 import）。也检查 .env 的 GEN_EMBED__* API 配置。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


SEMANTIC_THREAD_MATCH_THRESHOLD = 0.72   # thread 名+描述 vs cluster 摘要余弦阈值 · 待金标准校准


def _embed_corpus_once(corpus_text: str) -> "list | None":
    """真后端就绪时把 cluster_summary_text 编码一次，供本次 update() 内所有
    thread/throughline 复用（避免每条都重复编码同一段落·2026-07-02）。

    未配真后端 / 空文本 / 编码异常 → None（调用方逐条回退字面 substring）。
    """
    if not _has_real_embedding_backend() or not corpus_text.strip():
        return None
    try:
        from embedding_store import compute_embedding
        emb = compute_embedding(corpus_text)
        return emb if emb else None
    except Exception:
        return None


def _thread_appears(name: str, desc: str, corpus_text: str, corpus_emb) -> "tuple[bool, str]":
    """判定 thread/throughline 是否在本 cluster 摘要中出现。

    字面 substring 命中优先；真后端下字面未中时补语义余弦（摘要换说法不误标 dormant）。
    返回 (是否命中, match_method)。match_method ∈ {"literal_substring", "embedding_cosine"}。
    """
    if name and name in corpus_text:
        return True, "literal_substring"
    if corpus_emb is not None and name:
        query = f"{name} {desc}".strip()
        try:
            from embedding_store import compute_embedding, cosine_similarity
            qe = compute_embedding(query)
            if qe and len(qe) == len(corpus_emb) and cosine_similarity(qe, corpus_emb) >= SEMANTIC_THREAD_MATCH_THRESHOLD:
                return True, "embedding_cosine"
        except Exception:
            pass
    return False, "literal_substring"


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

    # 真后端就绪时 cluster 摘要只编码一次（本函数下面两个循环复用·2026-07-02）
    corpus_emb = _embed_corpus_once(cluster_summary_text)

    for thread in sub.get("threads", []):
        if isinstance(thread, str):
            # 兼容字符串列表 schema：str 元素只能读名匹配计数，不回写状态
            if thread:
                hit, _ = _thread_appears(thread, "", cluster_summary_text, corpus_emb)
                if hit:
                    updated += 1
            continue
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("id", "")
        thread_name = thread.get("name", thread_id)
        thread_desc = thread.get("description") or thread.get("desc") or ""
        # 字面 substring 命中优先；真后端下字面未中再补语义（摘要换说法不误标 dormant）
        if thread_name:
            hit, method = _thread_appears(thread_name, thread_desc, cluster_summary_text, corpus_emb)
            if hit:
                thread["last_cluster"] = cluster_id
                thread["last_updated"] = ts
                thread["match_method"] = method
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

    # 四线脉络同理（同一 cluster_summary_text·同一 corpus_emb·同一 _thread_appears 判定·
    # 2026-07-02 举一反三：与上面 subplot_threads 同函数同 bug 模式一起升级语义补漏）
    tl = _load(throughline_path)
    tl_updated = 0
    for line in tl.get("throughlines", []):
        # 🔴 G3 e2e 修：四线脉络 schema 可能存成字符串列表（走向线 = str），
        # 也可能是 dict 列表。line 是 str 时直接当线名；是 dict 时取 name。
        # 原 line.get(...) 对 str 抛 'str' object has no attribute 'get'。
        if isinstance(line, str):
            line_name = line
            if line_name:
                hit, _ = _thread_appears(line_name, "", cluster_summary_text, corpus_emb)
                if hit:
                    tl_updated += 1       # str 元素只读不可回写状态（保持 schema 不变）
            continue
        if not isinstance(line, dict):
            continue
        line_name = line.get("name", "")
        line_desc = line.get("description") or line.get("desc") or ""
        if line_name:
            hit, method = _thread_appears(line_name, line_desc, cluster_summary_text, corpus_emb)
            if hit:
                line["last_cluster"] = cluster_id
                line["last_updated"] = ts
                line["match_method"] = method
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
