"""B1 伏笔 tier→importance 贯通检索测试（2026-06-15·确定性·零依赖）。

守护（北极星·分桶→排序）：
  1. tier_to_importance 映射（tier1=1.0/tier2=0.5/tier3=0.2/非法=0.2）；
  2. mmr_rerank importance_weight=0 → 与原行为逐字节一致（零回归）；
  3. importance_weight>0 → 高 importance 文档在检索中优先（排序变了·分桶变排序）；
  4. 确定性（并列下标小优先·去随机）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import rag_retriever as rr  # noqa: E402


def test_tier_to_importance():
    """tier→importance 映射（tier1 核心 > tier2 支线 > tier3 细节）。"""
    assert rr.tier_to_importance(1) == 1.0
    assert rr.tier_to_importance(2) == 0.5
    assert rr.tier_to_importance(3) == 0.2
    assert rr.tier_to_importance("1") == 1.0   # str 兼容
    assert rr.tier_to_importance(None) == 0.2  # 非法 → 低保守
    assert rr.tier_to_importance("xx") == 0.2


def _no_sim(i, j):
    return 0.0   # 无两两相似度 · 纯 relevance(+importance) 排序


def test_mmr_importance_weight_zero_no_regression():
    """importance_weight=0 → 与不传 importance 逐字节一致（零回归铁律）。"""
    cand = [0, 1, 2, 3]
    rel = {0: 0.5, 1: 0.8, 2: 0.3, 3: 0.6}
    base = rr.mmr_rerank(cand, rel, _no_sim, 3, alpha=0.7)
    with_imp_w0 = rr.mmr_rerank(cand, rel, _no_sim, 3, alpha=0.7,
                                importance={0: 1.0, 2: 1.0}, importance_weight=0.0)
    assert base == with_imp_w0


def test_mmr_importance_boosts_high_tier():
    """importance_weight>0 → 高 importance 文档优先（分桶变排序）。"""
    cand = [0, 1, 2]
    rel = {0: 0.5, 1: 0.5, 2: 0.5}              # relevance 全相同
    base = rr.mmr_rerank(cand, rel, _no_sim, 3, alpha=0.7)
    assert base == [0, 1, 2]                    # 无 importance → 按下标
    imp = rr.mmr_rerank(cand, rel, _no_sim, 3, alpha=0.7,
                        importance={2: 1.0}, importance_weight=0.5)
    assert imp[0] == 2                          # doc2 高 importance → 排第一


def test_mmr_importance_deterministic_tie():
    """importance 并列时下标小优先（确定性·去随机）。"""
    cand = [0, 1, 2]
    rel = {0: 0.5, 1: 0.5, 2: 0.5}
    imp = rr.mmr_rerank(cand, rel, _no_sim, 3, alpha=0.7,
                        importance={0: 1.0, 1: 1.0}, importance_weight=0.5)
    assert imp[:2] == [0, 1]                    # doc0/1 同 importance → 下标小优先


def test_mmr_tier_end_to_end():
    """端到端：伏笔 tier→importance→检索排序。tier1 伏笔章在检索中优先于 tier3。"""
    cand = [0, 1, 2]
    rel = {0: 0.5, 1: 0.5, 2: 0.5}
    # doc0=tier3伏笔章 doc1=tier3 doc2=tier1伏笔章
    importance = {0: rr.tier_to_importance(3), 1: rr.tier_to_importance(3),
                  2: rr.tier_to_importance(1)}
    order = rr.mmr_rerank(cand, rel, _no_sim, 3, alpha=0.7,
                          importance=importance, importance_weight=0.5)
    assert order[0] == 2                        # tier1 伏笔章优先检索
