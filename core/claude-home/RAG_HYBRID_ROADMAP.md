# RAG Hybrid 升级路线图（v21 P5.2）

## 背景

业界 2025-2026 共识：「naive RAG 在 production 是 liability」

- Vector embedding 单独使用语义召回率高但精度低
- 关键词匹配（BM25）精度高但召回低
- **生产级方案 = Hybrid（embedding + keyword + rerank）**

## 我们现状

- ✅ `core/scripts/embedding_store.py` — hash-based 384-dim embedding（无外部依赖）
- ✅ `selective_history_retrieval` — top-K 语义检索注入 manifest
- ❌ **纯 embedding，无 keyword fallback / rerank**
- ❌ embedding 是 hash 模拟，**与真实语义距离仍有 gap**

## 升级路线

### Phase 1（中期·内置）

加 hybrid retriever：
```python
# core/scripts/hybrid_retrieval.py
def retrieve(query, top_k=5):
    # 1. embedding 召回 top-20
    emb_candidates = embedding_store.search(query, k=20)
    # 2. BM25 关键词召回 top-20（基于章纲摘要）
    bm25_candidates = bm25_search(query, k=20)
    # 3. 合并去重 → top-30
    candidates = dedupe(emb_candidates + bm25_candidates)
    # 4. rerank（用 cross-encoder 或 LLM scoring）
    reranked = rerank(query, candidates, top_k=top_k)
    return reranked
```

依赖：
- `rank-bm25` Python 包（pure Python，无大依赖）
- rerank：第一阶段可用 hash 相似度，第二阶段调小 LLM（Haiku）

### Phase 2（长期·外接）

- 接入 `bge-large-zh-v1.5`（智源中文 embedding）—— 真实语义
- 替换 hash 384-dim 为 1024-dim
- 加 fine-tune：用本项目 章纲摘要 自训 query-passage pair
- 投资约 1-2 天，retrieval 精度跳升

### Phase 3（终极·domain-specific）

- 收集多本小说的 章纲摘要 + 读者评论 → 训自己的 retrieval 模型
- 中文叙事专用 embedding

## 实施 checklist

- [ ] Phase 1: hybrid_retrieval.py（embedding + BM25 + 简单 rerank）
- [ ] selective_history_retrieval 切换到 hybrid
- [ ] manifest 注入字段加 retrieval_method 标记
- [ ] Phase 2: 引入 bge-large-zh-v1.5
- [ ] Phase 3: domain fine-tune

## 紧迫度评估

- **当前**：embedding hash + selective_retrieval 已能用，章数 < 100 时影响小
- **当章数 > 200**：retrieval 精度下降明显，必须 Phase 1
- **当章数 > 500**：必须 Phase 2

## 参考

- [What Is RAG 2026 Production Guide](https://www.marsdevs.com/blog/what-is-rag-in-ai-the-2026-production-guide)
- [BGE-Large Chinese](https://huggingface.co/BAAI/bge-large-zh-v1.5)
- [Hybrid Search Pattern (Pinecone/Weaviate docs)](https://www.pinecone.io/learn/hybrid-search-intro/)
