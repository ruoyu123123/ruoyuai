"""high_score_pattern_extractor.py — 高 judge 评分章节共性学习（v22.5 L8）

业界 Multi-Agent Evolve 思路：让 Judge 反馈不仅审单章，还自动总结「高分章节有何共性」
喂回 writer 的 skill artifacts，让后续章节学习这些隐性 pattern。

3 类共性提取：
A. 句法层：平均句长 / 标点比例 / 段落结构
B. 节奏层：对话比例 / 动作描写比例 / 心理活动比例
C. 情感层：情绪曲线（按 summarizer emotion_value）

输出：_数据库/.learning/high_score_patterns_<ts>.json
+ 自动追加 writer skill artifacts 作为 success_patterns
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_chapter_score(project_root: Path, ch: int) -> float:
    """从 judge_reports 取章节综合评分"""
    judge_dir = project_root / "_数据库" / ".judge_reports"
    for name in [f"ch_{ch:03d}_audit-hub.json", f"ch_{ch:03d}_consensus.json"]:
        p = judge_dir / name
        if p.exists():
            data = load_json(p, {})
            score = (data.get("score") or data.get("overall_score") or
                     (data.get("aggregated") or {}).get("score") or
                     (data.get("scores") or {}).get("overall"))
            if isinstance(score, (int, float)):
                return float(score)
    return None


def analyze_chapter_text(text: str) -> dict:
    """提取章节文本特征"""
    if not text:
        return {}
    cn_chars = len(re.findall(r"[一-鿿]", text))
    if cn_chars < 200:
        return {}
    sentences = re.split(r"[。！？]", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    avg_sentence_len = sum(len(s) for s in sentences) / len(sentences) if sentences else 0

    # 对话比例（"" 内）
    dialogue_chars = sum(len(m) for m in re.findall(r'["「『][^"」』]+["」』]', text))
    dialogue_ratio = dialogue_chars / cn_chars if cn_chars else 0

    # 段落数
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    avg_para_len = sum(len(p) for p in paragraphs) / len(paragraphs) if paragraphs else 0

    # 标点密度
    punct_count = sum(text.count(p) for p in "，。！？；：")
    punct_density = punct_count / cn_chars if cn_chars else 0

    return {
        "cn_chars": cn_chars,
        "avg_sentence_len": round(avg_sentence_len, 1),
        "dialogue_ratio": round(dialogue_ratio, 2),
        "paragraph_count": len(paragraphs),
        "avg_para_len": round(avg_para_len, 1),
        "punct_density": round(punct_density, 3),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--threshold", type=float, default=7.5, help="判定高分阈值")
    ap.add_argument("--update-experience", action="store_true",
                    help="把提取的 pattern 追加到 写作经验.json success_patterns")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    if len(chapters) < 5:
        print("[SKIP] 章节 < 5，数据不足")
        sys.exit(0)

    high_score_features = []
    low_score_features = []
    for ch in chapters:
        score = get_chapter_score(project_root, ch)
        if score is None:
            continue
        text_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        features = analyze_chapter_text(text)
        if not features:
            continue
        if score >= args.threshold:
            high_score_features.append({"ch": ch, "score": score, **features})
        elif score <= args.threshold - 2.0:
            low_score_features.append({"ch": ch, "score": score, **features})

    if not high_score_features:
        print(f"[SKIP] 无评分 ≥ {args.threshold} 的章节")
        sys.exit(0)

    # 提取共性
    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0

    high_avg = {
        "avg_sentence_len": avg([h["avg_sentence_len"] for h in high_score_features]),
        "dialogue_ratio": avg([h["dialogue_ratio"] for h in high_score_features]),
        "avg_para_len": avg([h["avg_para_len"] for h in high_score_features]),
        "punct_density": avg([h["punct_density"] for h in high_score_features]),
    }
    low_avg = {
        "avg_sentence_len": avg([h["avg_sentence_len"] for h in low_score_features]),
        "dialogue_ratio": avg([h["dialogue_ratio"] for h in low_score_features]),
        "avg_para_len": avg([h["avg_para_len"] for h in low_score_features]),
        "punct_density": avg([h["punct_density"] for h in low_score_features]),
    } if low_score_features else {}

    # 共性提取（高分 vs 低分对比）
    patterns = []
    if low_avg:
        for k in ["avg_sentence_len", "dialogue_ratio", "avg_para_len"]:
            diff = high_avg.get(k, 0) - low_avg.get(k, 0)
            if abs(diff) > 0.1 * (high_avg.get(k, 1) or 1):  # 差异 > 10%
                patterns.append({
                    "feature": k,
                    "high_score_avg": high_avg[k],
                    "low_score_avg": low_avg[k],
                    "diff": round(diff, 2),
                    "implication": f"高分章节{k}平均 {high_avg[k]}（低分 {low_avg[k]}）→ writer 应朝 {high_avg[k]} 靠拢",
                })

    out = {
        "scan_type": "high_score_pattern_extractor",
        "scan_ts": datetime.now().isoformat(timespec="seconds"),
        "threshold": args.threshold,
        "high_score_chapters": [h["ch"] for h in high_score_features],
        "low_score_chapters": [h["ch"] for h in low_score_features],
        "high_score_avg": high_avg,
        "low_score_avg": low_avg,
        "extracted_patterns": patterns,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = project_root / "_数据库" / ".learning" / f"high_score_patterns_{ts}.json"
    save_json(out_path, out)

    # 更新写作经验
    if args.update_experience and patterns:
        exp_path = project_root / "_数据库" / "写作经验.json"
        exp = load_json(exp_path, {"success_patterns": [], "failure_patterns": []})
        chapters_max = max(chapters) if chapters else 0
        for p in patterns:
            exp.setdefault("success_patterns", []).append({
                "id": f"HSP_{p['feature']}_{ts}",
                "name": f"高分章节 {p['feature']} 模式",
                "description": p["implication"],
                "keywords": [p["feature"]],
                "recorded_at_ch": chapters_max,
                "source": "high_score_pattern_extractor",
                "confidence": 0.75,
                "usage_count": 0,
                "version": 1,
                "status": "active",
            })
        save_json(exp_path, exp)
        print(f"[update_experience] 追加 {len(patterns)} 条 success_patterns 到 写作经验.json")

    print(f"[high_score_pattern_extractor] 高分 {len(high_score_features)} / 低分 {len(low_score_features)} / 提取 {len(patterns)} 模式")
    for p in patterns:
        print(f"  {p['feature']}: high={p['high_score_avg']} low={p['low_score_avg']} (Δ={p['diff']})")
    print(f"  报告: {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
