#!/usr/bin/env python3
"""cross_cluster_meta_quality_aggregate 账本版 SUMMARY_KEYWORD_MISMATCH 量纲对齐测试。

2026-06-15 Workflow 审计 high confirmed：scan_summary_consistency_from_ledger 原抽摘要
3-4 字片段(re.findall r"[一-鿿]{3,4}")与 text_keyword_set(builder _extract_text_keywords
抽的高频 2 字 2gram)量纲永不匹配 → 结构性必然误报 SUMMARY_KEYWORD_MISMATCH。
修：摘要也抽高频 2gram top-8 与正文 2gram 指纹同量纲比。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import cross_cluster_meta_quality_aggregate as mq  # noqa: E402


def _mismatch(findings):
    return [x for x in findings if x.get("code") == "SUMMARY_KEYWORD_MISMATCH"]


def test_faithful_summary_no_false_mismatch():
    """🔴 忠实摘要(高频 2gram 含正文指纹 top 词)不该误报——量纲对齐后 miss 低。
    原 bug：3-4 字片段 vs 2gram 指纹零匹配·忠实摘要也被误报编故事。"""
    faithful = "重黎挥剑斩断天柱，重黎挥剑再起，斩断天柱崩塌，重黎挥剑斩断天柱不止" * 2
    recs = [(5, {"summary": faithful, "text_keyword_set": ["重黎", "挥剑", "斩断", "天柱"]})]
    assert not _mismatch(mq.scan_summary_consistency_from_ledger(recs)), \
        "忠实摘要(高频词重合正文指纹)不该误报 SUMMARY_KEYWORD_MISMATCH"


def test_fabricated_summary_detected():
    """编故事摘要(高频 2gram 与正文指纹零重合)应检出——检测力保留(非一刀切跳过)。"""
    fabricate = "飞船宇宙星际穿越，飞船引擎过载，宇宙辐射星际航行，飞船坠毁宇宙深处遨游" * 2
    recs = [(5, {"summary": fabricate, "text_keyword_set": ["重黎", "挥剑", "斩断", "天柱"]})]
    assert _mismatch(mq.scan_summary_consistency_from_ledger(recs)), \
        "编故事摘要(主题完全偏离正文指纹)应检出 mismatch"


def test_no_fingerprint_skips_check():
    """text_keyword_set 缺失时跳过 keyword 检查(不崩·不误报)。"""
    recs = [(5, {"summary": "重黎挥剑斩断天柱崩塌大地震动天地变色风云突变" * 2, "text_keyword_set": []})]
    assert not _mismatch(mq.scan_summary_consistency_from_ledger(recs)), \
        "无正文指纹应跳过 mismatch 检查"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
