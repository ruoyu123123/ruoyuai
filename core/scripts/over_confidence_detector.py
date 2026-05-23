"""over_confidence_detector.py — 过度自信词检测（v21 P2.3 / hallucination 红旗）

业界研究（2026 multiple papers）：AI hallucination 时 **34% 更易使用过度自信词**
（"definitely / certainly / without doubt / 必然 / 无疑 / 绝对 / 一定"）。

本扫描器检测正文中过度自信词频次，作为 hallucination 风险红旗。

特别针对：
- 主角心理活动中的"绝对断言"（如"他知道这绝对是阴谋"）
- 设定描述中的"必然"（如"神性股权制必然崩溃"）
- 叙述者的过度自信（"这无疑是个陷阱"）

输出：_数据库/.audit/ch_NNN_overconfidence.json
退出码: 0 健康 / 1 advisory / 2 warning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 过度自信词典（中文 + 英文翻译）
OVER_CONFIDENCE_WORDS = [
    "必然", "无疑", "绝对", "必定", "肯定", "一定", "毫无疑问",
    "毋庸置疑", "definitely", "certainly", "without doubt",
    "毫无悬念", "毫无疑义", "百分百", "100%", "百分之百",
    "毋庸置喙", "板上钉钉",
]

# 警示阈值
WARN_PER_1000 = 2.0  # 每千字 ≥ 2 次 = warning
ADVISORY_PER_1000 = 1.0  # 每千字 ≥ 1 次 = advisory


def detect(text: str) -> dict:
    if not text:
        return {"density_per_1000": 0, "hits": {}, "total": 0, "char_count": 0}
    cn_chars = len(re.findall(r"[一-鿿]", text))
    if cn_chars < 100:
        return {"density_per_1000": 0, "hits": {}, "total": 0, "char_count": cn_chars}
    hits = Counter()
    for w in OVER_CONFIDENCE_WORDS:
        c = text.count(w)
        if c > 0:
            hits[w] = c
    total = sum(hits.values())
    density = total / cn_chars * 1000
    return {
        "density_per_1000": round(density, 2),
        "hits": dict(hits),
        "total": total,
        "char_count": cn_chars,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("ch", type=int)
    args = ap.parse_args()

    project_root = Path(args.project)
    text_path = project_root / "章节" / f"第{args.ch:03d}章" / f"第{args.ch:03d}章.txt"
    if not text_path.exists():
        print(f"[SKIP] 正文不存在: {text_path}")
        sys.exit(0)

    text = text_path.read_text(encoding="utf-8")
    result = detect(text)

    findings = []
    density = result["density_per_1000"]
    severity = None
    if density >= WARN_PER_1000:
        severity = "warning"
        findings.append({
            "severity": "warning",
            "code": "OVER_CONFIDENCE_HIGH",
            "density_per_1000": density,
            "total_hits": result["total"],
            "top_words": list(result["hits"].keys())[:5],
            "suggestion": (
                f"过度自信词密度 {density}/千字（阈值 {WARN_PER_1000}）→ "
                f"hallucination 风险红旗。AI 在不确定时倾向用「必然/无疑/绝对」掩盖。"
                f"改为「他猜」「他以为」「似乎」类不确定表达"
            ),
        })
    elif density >= ADVISORY_PER_1000:
        severity = "advisory"
        findings.append({
            "severity": "advisory",
            "code": "OVER_CONFIDENCE_ADVISORY",
            "density_per_1000": density,
            "total_hits": result["total"],
            "top_words": list(result["hits"].keys())[:5],
            "suggestion": (
                f"过度自信词密度 {density}/千字（阈值 {ADVISORY_PER_1000}）→ "
                f"建议用不确定表达替代部分"
            ),
        })

    out_dir = project_root / "_数据库" / ".audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ch_{args.ch:03d}_overconfidence.json"
    report = {
        "scan_type": "over_confidence",
        "ch": args.ch,
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "detection": result,
        "findings": findings,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[over_confidence] ch{args.ch}: density={density}/千字 hits={result['total']}")
    for f in findings:
        print(f"  [{f['severity'].upper()}] {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if severity == "warning":
        sys.exit(2)
    if severity == "advisory":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
