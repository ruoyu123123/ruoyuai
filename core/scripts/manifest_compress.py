"""manifest_compress.py — LLMLingua 风格 manifest 压缩（v21 P2.1 新增）

业界研究：LLMLingua 自动删冗余 token → 20x 压缩 + 1.5% 损失。我们 manifest 当前 56KB，
其中大量 _doc / _note / _reason / _writer_hint / _consumption_chain 等说明字段是「写给开发者看的」，
注入到 LLM agent prompt 中纯属浪费 token。

压缩策略（保守版，无损语义）：
1. 删 _doc / _note / _reason / _writer_hint / _consumption_chain / _field_doc / _example
   等以 _ 开头的"开发者注释"字段（**但保留 _critical_summary / _cache_layout / _id**——这些是 LLM 要用的）
2. 删 reason / suggestion 等冗余说明（在 hard_constraints 中保留 message 即可）
3. 删值为 null / [] / {} 的空字段
4. 数组截断：列表 > 10 时截到 10，加 "_truncated_at": original_count
5. 字符串截断：默认 > 200 字符盲切前缀 200 + "...(N truncated)"（LLMLingua 风格却零模型调用的占位版）

【2026-07-02 接入真模型·信息量优选截断】RUOYU_NN_SURPRISAL=1 时超限字符串改走真 LLMLingua 路数：
按句切分 → 经 nn_surprisal_bridge/feature_cache 批量算句级 GPT-2 surprisal → 保留高信息量句子
填满预算（而非盲切前缀，可能丢开头恰是关键信息的情况）。**全 manifest 一次性批量调用**（先遍历收集
所有超限字符串的候选句子，合并成一次 subprocess，避免逐句/逐字段调用——本模块是 build_manifest
热路径消费点）。门控关闭 / 桥不可用 / 该字符串任一句未命中 → 该字符串回退盲切前缀（逐字节不变）。

输出：_数据库/.manifest/ch_NNN_compressed.json + 体积对比报告

用法：python manifest_compress.py <project> <ch>
退出码：0 成功
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# 必须保留的特殊 _ 字段（LLM 要用）
KEEP_UNDERSCORE_FIELDS = {
    "_id",
    "_schema",
    "_priority",
    "_role",
}

# 冗余说明字段（删）
DROP_FIELDS_DEEP = {
    "_doc", "_note", "_reason", "_writer_hint", "_consumption_chain",
    "_field_doc", "_example", "_writer_examples", "_log_doc",
    "_aspect_pool_doc", "_status", "_propp_doc", "_outcome_categories",
    "_phase_doc", "_profile_doc", "_filter_doc",
}

# 顶层仅 Claude agent 用的元数据字段（gen-model 无法执行 Read/Bash）
# budget_report：S1 分层 token 预算记账/压缩日志（manifest_budget.py 产·人看的元数据非创作载荷）
DROP_TOP_LEVEL_KEYS = {
    "must_read", "_cache_layout", "_critical_summary",
    "instructions_for_subagent", "post_write_checks", "database_coverage",
    "budget_report",
}

MAX_LIST_LEN = 10
MAX_STR_LEN = 200

_SENT_SPLIT_RE = re.compile(r"[^。！？!?;；\n]+[。！？!?;；\n]*")


def _surprisal_gate_on() -> bool:
    return os.environ.get("RUOYU_NN_SURPRISAL") == "1"


def _split_sentences(s: str) -> list[str]:
    """按句末标点切句(保留标点)·供高 surprisal 句优选截断用。"""
    parts = [m.group() for m in _SENT_SPLIT_RE.finditer(s)]
    return [p for p in parts if p.strip()]


def _predict_surprisal_batch(texts: list[str]) -> list[float | None]:
    """批量取 GPT-2 mean_surprisal(经 FeatureStore 缓存优先→退 nn_surprisal_bridge 直连)。
    全不可用 → 全 None(调用方对涉及字符串整体回退盲切前缀·逐字节不变)。"""
    if not texts:
        return []
    preds = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_surprisal_batch(texts)
    except Exception:  # noqa: BLE001 FeatureStore 故障 → 退 bridge，绝不影响压缩
        preds = None
    if preds is None:
        try:
            import nn_surprisal_bridge as bridge
        except ImportError:
            return [None] * len(texts)
        preds = bridge.predict_batch(texts)
    if len(preds) != len(texts):
        return [None] * len(texts)
    return [(p.get("mean_surprisal") if p else None) for p in preds]


def _collect_long_strings(obj, depth: int = 0, out: list[str] | None = None) -> list[str]:
    """与 compress() 完全同构的过滤遍历·只收集『确实会走到盲切分支』的超限字符串
    (避免为会被丢弃的字段(_doc/_note 等)浪费模型调用)。"""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if depth == 0 and k in DROP_TOP_LEVEL_KEYS:
                continue
            if k in DROP_FIELDS_DEEP and k not in KEEP_UNDERSCORE_FIELDS:
                continue
            if v is None or v == [] or v == {} or v == "":
                continue
            _collect_long_strings(v, depth + 1, out)
    elif isinstance(obj, list):
        items = obj[:MAX_LIST_LEN] if len(obj) > MAX_LIST_LEN else obj
        for item in items:
            _collect_long_strings(item, depth + 1, out)
    elif isinstance(obj, str) and len(obj) > MAX_STR_LEN:
        out.append(obj)
    return out


def _select_high_surprisal(original: str, sentences: list[str], scores: list[float]) -> str:
    """按句 surprisal 降序挑句填满 MAX_STR_LEN 预算·再按原文相对顺序拼回(保留可读性)。"""
    budget = MAX_STR_LEN
    order = sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)
    picked = []
    used = 0
    for i in order:
        length = len(sentences[i])
        if picked and used + length > budget:
            continue
        picked.append(i)
        used += length
        if used >= budget:
            break
    picked.sort()
    text = "".join(sentences[i] for i in picked)
    if len(text) > budget:
        text = text[:budget]
    return text + f"...(surprisal精选{len(text)}/{len(original)}字·{len(picked)}/{len(sentences)}句)"


def _build_surprisal_cache(obj) -> dict:
    """门控开时一次性收集全 manifest 超限字符串·合并成一次批量调用算句级 surprisal·
    选高信息量句替代盲切前缀。任一环节失败/桥不可用 → 空 cache(调用方回退盲切前缀·逐字节不变)。"""
    long_strings = _collect_long_strings(obj)
    if not long_strings:
        return {}
    unique_strings = list(dict.fromkeys(long_strings))  # 去重·同内容只算一次
    per_string_sentences = [_split_sentences(s) for s in unique_strings]
    all_sentences = [sent for sents in per_string_sentences for sent in sents]
    if not all_sentences:
        return {}
    scores = _predict_surprisal_batch(all_sentences)  # 唯一一次批量调用(避免逐句 subprocess)
    if len(scores) != len(all_sentences):
        return {}
    cache = {}
    cursor = 0
    for s, sents in zip(unique_strings, per_string_sentences):
        n = len(sents)
        sent_scores = scores[cursor:cursor + n]
        cursor += n
        if not sents or any(sc is None for sc in sent_scores):
            continue  # 该字符串任一句未命中 → 不入 cache·该字符串回退盲切前缀(逐字节不变)
        cache[s] = _select_high_surprisal(s, sents, sent_scores)
    return cache


def compress(obj, depth: int = 0, _surprisal_cache: "dict | None" = None):
    """递归压缩 JSON 对象。字符串超限截断：门控 RUOYU_NN_SURPRISAL=1 时优先按句 surprisal 精选
    高信息量句子(_build_surprisal_cache 一次性批量算好)；门控关/桥不可用/该字符串未完整命中
    → 盲切前缀(逐字节不变·默认行为)。"""
    if depth == 0 and _surprisal_cache is None:
        _surprisal_cache = _build_surprisal_cache(obj) if _surprisal_gate_on() else {}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # 顶层剥除 Claude agent 专用元数据（gen-model 不可操作）
            if depth == 0 and k in DROP_TOP_LEVEL_KEYS:
                continue
            # 删冗余字段（但保留必须的）
            if k in DROP_FIELDS_DEEP and k not in KEEP_UNDERSCORE_FIELDS:
                continue
            # 删空值
            if v is None or v == [] or v == {} or v == "":
                continue
            # 递归压缩
            compressed_v = compress(v, depth + 1, _surprisal_cache)
            # 二次过滤：压缩后变空也删
            if compressed_v is None or compressed_v == {} or compressed_v == []:
                continue
            out[k] = compressed_v
        return out
    if isinstance(obj, list):
        if len(obj) > MAX_LIST_LEN:
            out = [compress(item, depth + 1, _surprisal_cache) for item in obj[:MAX_LIST_LEN]]
            out.append({"_truncated_at": len(obj), "_kept": MAX_LIST_LEN})
            return out
        return [compress(item, depth + 1, _surprisal_cache) for item in obj]
    if isinstance(obj, str) and len(obj) > MAX_STR_LEN:
        cached = _surprisal_cache.get(obj)
        if cached is not None:
            return cached
        return obj[:MAX_STR_LEN] + f"...(+{len(obj)-MAX_STR_LEN} chars)"
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int)
    args = ap.parse_args()

    project_root = Path(args.project)
    src = project_root / "_数据库" / ".manifest" / f"ch_{args.ch:03d}.json"
    if not src.exists():
        print(f"[ERROR] manifest 不存在: {src}", file=sys.stderr)
        sys.exit(2)

    original = json.loads(src.read_text(encoding="utf-8"))
    compressed = compress(original)

    out = project_root / "_数据库" / ".manifest" / f"ch_{args.ch:03d}_compressed.json"
    out.write_text(json.dumps(compressed, ensure_ascii=False, indent=2), encoding="utf-8")

    orig_size = src.stat().st_size
    new_size = out.stat().st_size
    ratio = new_size / orig_size if orig_size else 0
    print(f"[manifest_compress] ch{args.ch}")
    print(f"  原始: {orig_size:,} bytes")
    print(f"  压缩: {new_size:,} bytes ({ratio:.0%})")
    print(f"  节省: {orig_size - new_size:,} bytes ({(1-ratio)*100:.0f}%)")
    print(f"  输出: {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
