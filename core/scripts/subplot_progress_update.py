"""subplot_progress_update.py — 从 cluster 摘要推进 subplot_threads + 四线脉络

G3 调研发现: subplot_threads.json + 四线脉络.json 只在 outline 写一次后零写回。

机制(零 LLM · 确定性):
- 读 故事块摘要.json 的 cluster 摘要
- 如果摘要提及 subplot thread → 标 thread.last_cluster / thread.status
- 如果超过 5 cluster 没提及某 thread → 标 thread.status = "dormant"

接入点: cluster-save-state step11
退出码:
- 0: 状态推进完成，或项目尚未启用 subplot/四线账本的确定性 no-op
- 2: 输入 JSON 损坏、schema 错误或写回失败

用法:
  python core/scripts/subplot_progress_update.py <project_root> --cluster <cluster_id>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型）───────
def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available·
    替代旧的按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测的 _has_real_embedding_backend）。

    import 失败 → False（调用方使用字面 substring）。
    """
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


SEMANTIC_THREAD_MATCH_THRESHOLD = 0.52   # thread 名+描述 vs cluster 摘要余弦阈值
# 金标准校准 2026-07-04：content_embed_separability_20260704 报告 neg_p95=0.5165/Youden=0.4904


def _embed_corpus_once(corpus_text: str) -> "list | None":
    """内容后端就绪时把 cluster_summary_text 编码一次，供本次 update() 内所有
    thread/throughline 复用（避免每条都重复编码同一段落·2026-07-02）。

    内容后端不可用 / 空文本 → None（调用方逐条使用字面 substring）。
    已声明可用的内容后端编码失败 → 抛错，避免状态推进链路静默缺信号。
    """
    backend_ready = _content_backend_ready()
    if not backend_ready or not corpus_text.strip():
        return None
    try:
        from embedding_store import compute_content_embedding
        emb = compute_content_embedding(corpus_text)
        return emb if emb else None
    except Exception as exc:
        raise RuntimeError(f"cluster 摘要内容 embedding 失败: {exc}") from exc


def _thread_appears(name: str, desc: str, corpus_text: str, corpus_emb) -> "tuple[bool, str]":
    """判定 thread/throughline 是否在本 cluster 摘要中出现。

    字面 substring 命中优先；内容后端下字面未中时补语义余弦（摘要换说法不误标 dormant）。
    返回 (是否命中, match_method)。match_method ∈ {"literal_substring", "embedding_cosine"}。
    """
    if name and name in corpus_text:
        return True, "literal_substring"
    if corpus_emb is not None and name:
        query = f"{name} {desc}".strip()
        from embedding_store import compute_content_embedding, cosine_similarity
        qe = compute_content_embedding(query)
        if qe and len(qe) == len(corpus_emb) and cosine_similarity(qe, corpus_emb) >= SEMANTIC_THREAD_MATCH_THRESHOLD:
            return True, "embedding_cosine"
    return False, "literal_substring"


def _load_required(p: Path) -> dict:
    if not p.exists():
        raise FileNotFoundError(str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{p} JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{p} 顶层必须是对象")
    return data


def _load_optional(p: Path) -> dict:
    if not p.exists():
        return {}
    return _load_required(p)


def _save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def update(project_root: Path, cluster_id: str) -> dict:
    """从故事块摘要推进 subplot_threads 状态。"""
    db = project_root / "_数据库"
    sub_path = db / "subplot_threads.json"
    summary_path = db / "故事块摘要.json"
    throughline_path = db / "四线脉络.json"

    if not summary_path.exists():
        return {
            "subplot_updated": 0,
            "throughline_updated": 0,
            "status": "no_action",
            "reason": "故事块摘要.json 不存在",
        }

    summary = _load_required(summary_path)
    sub = _load_optional(sub_path)

    if not sub.get("threads"):
        sub.setdefault("threads", [])
    if not isinstance(sub.get("threads"), list):
        raise RuntimeError("subplot_threads.json 的 threads 必须是列表")

    # 拿当前 cluster 摘要文本
    cluster_summary_text = ""
    for c in summary.get("clusters", []):
        if c.get("cluster_id") == cluster_id:
            cluster_summary_text = json.dumps(c, ensure_ascii=False)
            break

    updated = 0
    ts = datetime.now().isoformat(timespec="seconds")

    # 四线脉络提前读取（供下面 prefetch 收集 query 文本 + 后面判定循环复用·不重复 _load）
    tl = _load_optional(throughline_path)
    if tl and not isinstance(tl.get("throughlines", []), list):
        raise RuntimeError("四线脉络.json 的 throughlines 必须是列表")

    # 🔴 2026-07-03 Wave-4：本次 update() 会用到的全部待编码文本（corpus 摘要 +
    # 所有 thread/throughline query）一次性 prefetch（内容后端子进程按条调用极贵·
    # 合并成一次批调用），后续 _embed_corpus_once / _thread_appears 内的逐条
    # compute_content_embedding 全部命中缓存。
    if _content_backend_ready():
        from embedding_store import prefetch_content_embeddings
        queries = [cluster_summary_text] if cluster_summary_text else []
        for thread in sub.get("threads", []):
            if isinstance(thread, str):
                if thread:
                    queries.append(thread)
            elif isinstance(thread, dict):
                name = thread.get("name", thread.get("id", ""))
                if name:
                    desc = thread.get("description") or thread.get("desc") or ""
                    queries.append(f"{name} {desc}".strip())
            else:
                raise RuntimeError("subplot_threads.json 的 threads 只能包含字符串或对象")
        for line in tl.get("throughlines", []):
            if isinstance(line, str):
                if line:
                    queries.append(line)
            elif isinstance(line, dict):
                name = line.get("name", "")
                if name:
                    desc = line.get("description") or line.get("desc") or ""
                    queries.append(f"{name} {desc}".strip())
            else:
                raise RuntimeError("四线脉络.json 的 throughlines 只能包含字符串或对象")
        if queries:
            prefetch_content_embeddings(queries)

    # 内容后端就绪时 cluster 摘要只编码一次（本函数下面两个循环复用·2026-07-02）
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
        # 字面 substring 命中优先；内容后端下字面未中再补语义（摘要换说法不误标 dormant）
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

    if updated > 0 or not sub_path.exists():
        _save(sub_path, sub)

    # 四线脉络同理（同一 cluster_summary_text·同一 corpus_emb·同一 _thread_appears 判定·
    # 2026-07-02 举一反三：与上面 subplot_threads 同函数同 bug 模式一起升级语义补漏）
    # tl 已在函数开头为 prefetch 收集提前读取（见上），此处复用不重复 _load
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
    elif r.get("status") == "no_action":
        print(f"[subplot_progress] no-op: {r.get('reason', '')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[subplot_progress] FATAL: {exc}", file=sys.stderr)
        raise SystemExit(2)
