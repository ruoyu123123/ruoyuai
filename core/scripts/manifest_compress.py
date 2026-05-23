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
5. 字符串截断：> 200 字符截到 200，加 "...(N truncated)"

输出：_数据库/.manifest/ch_NNN_compressed.json + 体积对比报告

用法：python manifest_compress.py <project> <ch>
退出码：0 成功
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 必须保留的特殊 _ 字段（LLM 要用）
KEEP_UNDERSCORE_FIELDS = {
    "_cache_layout",
    "_critical_summary",
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

MAX_LIST_LEN = 10
MAX_STR_LEN = 200


def compress(obj, depth: int = 0):
    """递归压缩 JSON 对象。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # 删冗余字段（但保留必须的）
            if k in DROP_FIELDS_DEEP and k not in KEEP_UNDERSCORE_FIELDS:
                continue
            # 删空值
            if v is None or v == [] or v == {} or v == "":
                continue
            # 递归压缩
            compressed_v = compress(v, depth + 1)
            # 二次过滤：压缩后变空也删
            if compressed_v is None or compressed_v == {} or compressed_v == []:
                continue
            out[k] = compressed_v
        return out
    if isinstance(obj, list):
        if len(obj) > MAX_LIST_LEN:
            out = [compress(item, depth + 1) for item in obj[:MAX_LIST_LEN]]
            out.append({"_truncated_at": len(obj), "_kept": MAX_LIST_LEN})
            return out
        return [compress(item, depth + 1) for item in obj]
    if isinstance(obj, str) and len(obj) > MAX_STR_LEN:
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
