"""golden_passages_audit.py — 蒸馏 golden_passages 质量审计（v21 P10.1 新增）

业界研究（arxiv 2509.14543）：LLM 风格模仿  - **demos 越多收益递减**
- 仅 4-5 demos 时已回归均值
- 关键不是数量，是**多样性 + 独特性**

3 类质量检测：
A. DIVERSITY_LOW：同 scene_type 内 N 段过于相似（cosine 相似度高 / 字数雷同 / 句首词雷同）
B. UNIQUENESS_LOW：与"平均风格"距离近（句长/标点/虚词使用与中文小说平均一致 = 无特色）
C. LENGTH_IMBALANCE：单段过长 (>1500 字)或过短 (<100 字)

输出：_数据库/.audit/golden_passages_quality.json
退出码：0 健康 / 1 advisory / 2 warning
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


def get_text(passage) -> str:
    if isinstance(passage, dict):
        return passage.get("text") or passage.get("passage") or passage.get("content") or ""
    if isinstance(passage, str):
        return passage
    return ""


def char_overlap_ratio(a: str, b: str) -> float:
    """两段文本字符集合的 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    sa = set(a)
    sb = set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union > 0 else 0.0


def first_n_chars(text: str, n: int = 8) -> str:
    return text.strip()[:n] if text else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project)
    style_path = project_root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        print("[SKIP] 作者风格.json 不存在")
        sys.exit(0)
    data = load_json(style_path, {})
    gp = data.get("golden_passages", {}) or {}
    if not gp:
        print("[SKIP] golden_passages 为空")
        sys.exit(0)

    findings = []
    stats = {}

    for ptype, passages in gp.items():
        if not isinstance(passages, list) or len(passages) == 0:
            continue
        texts = [get_text(p) for p in passages if get_text(p)]
        if not texts:
            continue
        stats[ptype] = {"count": len(texts), "avg_len": int(sum(len(t) for t in texts) / len(texts))}

        # A. DIVERSITY_LOW：两两 cosine（用 jaccard 简化）
        if len(texts) >= 2:
            sims = []
            for i in range(len(texts)):
                for j in range(i + 1, len(texts)):
                    sims.append(char_overlap_ratio(texts[i], texts[j]))
            if sims:
                avg_sim = sum(sims) / len(sims)
                max_sim = max(sims)
                stats[ptype]["avg_jaccard"] = round(avg_sim, 2)
                stats[ptype]["max_jaccard"] = round(max_sim, 2)
                if avg_sim > 0.6:
                    findings.append({
                        "severity": "warning",
                        "code": "DIVERSITY_LOW",
                        "scene_type": ptype,
                        "avg_jaccard": round(avg_sim, 2),
                        "suggestion": f"golden_passages.{ptype} 内 {len(texts)} 段平均字符 Jaccard 相似 {round(avg_sim,2)} > 0.6 → 多样性低，重抽蒸馏样本",
                    })
                elif avg_sim > 0.45:
                    findings.append({
                        "severity": "advisory",
                        "code": "DIVERSITY_MODERATE",
                        "scene_type": ptype,
                        "avg_jaccard": round(avg_sim, 2),
                        "suggestion": f"{ptype} 平均 Jaccard {round(avg_sim,2)}（>0.45）→ 中等同质，建议扩样多样化",
                    })

        # C. LENGTH_IMBALANCE
        long_segs = [t for t in texts if len(t) > 1500]
        short_segs = [t for t in texts if len(t) < 100]
        if long_segs:
            findings.append({
                "severity": "advisory",
                "code": "LENGTH_TOO_LONG",
                "scene_type": ptype,
                "count": len(long_segs),
                "suggestion": f"{ptype} 含 {len(long_segs)} 段 >1500 字 → 注入 manifest 占预算，考虑截断或拆段",
            })
        if short_segs:
            findings.append({
                "severity": "advisory",
                "code": "LENGTH_TOO_SHORT",
                "scene_type": ptype,
                "count": len(short_segs),
                "suggestion": f"{ptype} 含 {len(short_segs)} 段 <100 字 → 风格 demo 太短，缺少完整句法节奏",
            })

        # 句首词重复（如 5 段都是"他..."开头）
        first_words = [first_n_chars(t, 2) for t in texts if t]
        if first_words:
            counter = Counter(first_words)
            most_first, most_count = counter.most_common(1)[0]
            if len(texts) >= 4 and most_count / len(texts) > 0.5:
                findings.append({
                    "severity": "advisory",
                    "code": "FIRST_WORD_REPETITION",
                    "scene_type": ptype,
                    "most_common": most_first,
                    "pct": round(most_count / len(texts), 2),
                    "suggestion": f"{ptype} 中 {round(most_count/len(texts)*100)}% 段以「{most_first}」开头 → 句首单调",
                })

    # B. UNIQUENESS_LOW（粗估）：所有 passages 平均句长 vs 业界中文小说均值 ~25 字
    all_texts = []
    for ps in gp.values():
        if isinstance(ps, list):
            all_texts.extend([get_text(p) for p in ps if get_text(p)])
    if all_texts:
        all_sentences = re.split(r"[。！？]", "\n".join(all_texts))
        all_sentences = [s.strip() for s in all_sentences if s.strip()]
        if all_sentences:
            avg_sentence_len = sum(len(s) for s in all_sentences) / len(all_sentences)
            stats["_global_avg_sentence_len"] = round(avg_sentence_len, 1)
            # 业界中文长篇小说平均句长 22-28 字
            if 22 <= avg_sentence_len <= 28:
                findings.append({
                    "severity": "advisory",
                    "code": "UNIQUENESS_LOW",
                    "avg_sentence_len": round(avg_sentence_len, 1),
                    "suggestion": f"全部 golden_passages 平均句长 {avg_sentence_len:.1f} 字 ≈ 中文小说均值 22-28 → 风格无特色（独特作者通常偏长/偏短）",
                })

    out_dir = project_root / "_数据库" / ".audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "golden_passages_quality",
        "scan_ts": ts,
        "stats_by_type": stats,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"golden_passages_quality_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[golden_passages_quality] {len(stats)} 类型, {summary['warning']}W / {summary['advisory']}A")
    for f in findings[:5]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
