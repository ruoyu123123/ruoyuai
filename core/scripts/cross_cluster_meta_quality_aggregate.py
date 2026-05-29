"""cross_cluster_meta_quality_aggregate.py — 摘要/字数/经验 跨章扫（CCR15）

3 类元质量跨章检测：

A. CHAPTER_LENGTH_DISTRIBUTION
   - LENGTH_OUTLIER：章节字数偏离中位数 ±50%
   - LENGTH_TREND_DROP：连续 ≥ 3 章字数单调下降
   - LENGTH_VARIANCE_HIGH：近 N 章 std/mean > 0.4 = 字数控制差

B. SUMMARY_CONSISTENCY
   读 _数据库/故事块摘要.json 的 chapter_summary[ch] vs 正文实际内容
   - SUMMARY_TOO_SHORT：摘要 < 50 字（无效摘要）
   - SUMMARY_KEYWORD_MISSING：摘要中提到的关键名词在正文中未出现 → 摘要在编故事

C. LESSONS_FEEDBACK_LOOP
   读 _数据库/写作经验.json 的 success_patterns / failure_patterns
   - FAILURE_RECURRING：failure_pattern 中的关键词在新章中再次出现 → 重复犯错
   - SUCCESS_NEVER_REUSED：success_pattern 中的关键词在 ≥ 5 章后再无出现 → 经验未沉淀

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



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def get_chapters(project_root: Path, last_n: int) -> list[int]:
    chs = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                 for d in (project_root / "章节").glob("第*章")
                 if re.match(r"第(\d+)章", d.name))
    return chs[-last_n:] if chs else []


def read_text(project_root: Path, ch: int) -> str:
    p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- A. CHAPTER_LENGTH_DISTRIBUTION ----------

def scan_length_distribution(project_root: Path, chapters: list[int],
                             lengths: list[tuple[int, int]] | None = None) -> list[dict]:
    """lengths 不传 → 逐章读正文算 CJK（原磁盘逻辑）；传入 → 直接用（cluster 账本 cjk_count）。

    2026-05-29 cluster 化：仅前置数据来源可替换，下游中位数/趋势/方差判定逻辑原样保留。
    """
    findings = []
    if lengths is None:
        lengths = []  # [(ch, char_count)]
        for ch in chapters:
            text = read_text(project_root, ch)
            # 中文字数（剔除空白和半角符号）
            cn_chars = len(re.findall(r"[一-鿿]", text))
            lengths.append((ch, cn_chars))
    if len(lengths) < 3:
        return []

    counts = [c for _, c in lengths]
    sorted_counts = sorted(counts)
    median = sorted_counts[len(sorted_counts) // 2]
    mean = sum(counts) / len(counts)
    var = sum((c - mean) ** 2 for c in counts) / len(counts)
    std = var ** 0.5
    cv = std / mean if mean > 0 else 0

    # LENGTH_OUTLIER
    for ch, c in lengths:
        if abs(c - median) > median * 0.5 and median > 1000:
            findings.append({
                "severity": "advisory",
                "code": "LENGTH_OUTLIER",
                "ch": ch,
                "length": c,
                "median": median,
                "diff_pct": round((c - median) / median, 2),
                "suggestion": f"ch{ch} 字数 {c} 偏离中位数 {median} 超 50% → 章节字数控制不稳",
            })

    # LENGTH_TREND_DROP
    drop_streak = 0
    for i in range(1, len(lengths)):
        if lengths[i][1] < lengths[i - 1][1]:
            drop_streak += 1
            if drop_streak >= 3:
                findings.append({
                    "severity": "advisory",
                    "code": "LENGTH_TREND_DROP",
                    "consecutive_chs": [lengths[j][0] for j in range(i - 3, i + 1)],
                    "trail": [lengths[j][1] for j in range(i - 3, i + 1)],
                    "suggestion": f"近 4 章字数单调下降（{lengths[i-3][1]} → {lengths[i][1]}）→ 写作疲劳",
                })
                drop_streak = 0
        else:
            drop_streak = 0

    # LENGTH_VARIANCE_HIGH
    if cv > 0.4 and len(lengths) >= 5:
        findings.append({
            "severity": "advisory",
            "code": "LENGTH_VARIANCE_HIGH",
            "cv": round(cv, 2),
            "mean": round(mean),
            "std": round(std),
            "suggestion": f"近 {len(lengths)} 章字数 std/mean = {cv:.2f}（>0.4）→ 字数控制差",
        })
    return findings


# ---------- B. SUMMARY_CONSISTENCY ----------

def scan_summary_consistency(project_root: Path, chapters: list[int]) -> list[dict]:
    findings = []
    summary_path = project_root / "_数据库" / "故事块摘要.json"
    summary_data = load_json(summary_path, {})
    chapter_summary = summary_data.get("chapter_summary", {}) or {}
    if not chapter_summary:
        return []

    for ch in chapters:
        s = chapter_summary.get(str(ch)) or chapter_summary.get(ch) or {}
        summary_text = s.get("summary") if isinstance(s, dict) else (s if isinstance(s, str) else "")
        if not summary_text:
            continue
        text_len = len(re.findall(r"[一-鿿]", summary_text))
        if text_len < 50:
            findings.append({
                "severity": "advisory",
                "code": "SUMMARY_TOO_SHORT",
                "ch": ch,
                "summary_len": text_len,
                "suggestion": f"ch{ch} 摘要仅 {text_len} 字（应 ≥ 50）→ 无效摘要",
            })
            continue
        # SUMMARY_KEYWORD_MISSING：提取摘要中的中文 2-3 字词，看是否在正文出现
        text = read_text(project_root, ch)
        if not text:
            continue
        kws = re.findall(r"[一-鿿]{3,4}", summary_text)[:10]
        miss_kws = [kw for kw in kws if kw not in text]
        if len(miss_kws) >= len(kws) * 0.6 and len(kws) >= 5:
            findings.append({
                "severity": "warning",
                "code": "SUMMARY_KEYWORD_MISMATCH",
                "ch": ch,
                "missing_kws": miss_kws[:6],
                "miss_ratio": round(len(miss_kws) / len(kws), 2),
                "suggestion": f"ch{ch} 摘要中 {round(len(miss_kws)/len(kws)*100)}% 关键词在正文未出现 → 摘要可能在编故事",
            })
    return findings


def scan_summary_consistency_from_ledger(chapter_records: list[tuple[int, dict]]) -> list[dict]:
    """2026-05-29 cluster 化：账本驱动版 SUMMARY_CONSISTENCY。

    用 ChapterRecord.summary（富摘要）+ text_keyword_set（正文关键词指纹）替代逐章读正文：
    - SUMMARY_TOO_SHORT：summary < 50 CJK
    - SUMMARY_KEYWORD_MISMATCH：摘要抽词与正文关键词指纹比对（指纹缺则跳过 keyword 检查）
    severity/code/输出字段与磁盘版完全一致。
    """
    findings = []
    for ch, rec in chapter_records:
        summary_text = rec.get("summary") or ""
        if not isinstance(summary_text, str) or not summary_text:
            continue
        text_len = len(re.findall(r"[一-鿿]", summary_text))
        if text_len < 50:
            findings.append({
                "severity": "advisory",
                "code": "SUMMARY_TOO_SHORT",
                "ch": ch,
                "summary_len": text_len,
                "suggestion": f"ch{ch} 摘要仅 {text_len} 字（应 ≥ 50）→ 无效摘要",
            })
            continue
        # 正文关键词指纹（builder 预抽）作为「正文」的代理；缺则不做 mismatch 检查
        text_kw_set = set(rec.get("text_keyword_set") or [])
        if not text_kw_set:
            continue
        kws = re.findall(r"[一-鿿]{3,4}", summary_text)[:10]
        miss_kws = [kw for kw in kws if kw not in text_kw_set]
        if len(miss_kws) >= len(kws) * 0.6 and len(kws) >= 5:
            findings.append({
                "severity": "warning",
                "code": "SUMMARY_KEYWORD_MISMATCH",
                "ch": ch,
                "missing_kws": miss_kws[:6],
                "miss_ratio": round(len(miss_kws) / len(kws), 2),
                "suggestion": f"ch{ch} 摘要中 {round(len(miss_kws)/len(kws)*100)}% 关键词在正文指纹未出现 → 摘要可能在编故事",
            })
    return findings


# ---------- C. LESSONS_FEEDBACK_LOOP ----------

def scan_lessons_feedback(project_root: Path, chapters: list[int]) -> list[dict]:
    lessons_path = project_root / "_数据库" / "写作经验.json"
    if not lessons_path.exists():
        return []
    lessons = load_json(lessons_path, {})
    failure_patterns = lessons.get("failure_patterns", []) or []
    success_patterns = lessons.get("success_patterns", []) or []
    findings = []

    # FAILURE_RECURRING
    for fp in failure_patterns[-10:]:  # 仅看最近 10 条 failure
        if not isinstance(fp, dict):
            continue
        recorded_at_ch = fp.get("recorded_at_ch") or fp.get("ch") or 0
        keywords = fp.get("keywords") or fp.get("trigger_keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        if not keywords or recorded_at_ch == 0:
            continue
        # 看后续 ≥ 3 章是否再出现
        post_chs = [c for c in chapters if c > recorded_at_ch][:5]
        if len(post_chs) < 3:
            continue
        recurring_chs = []
        for ch in post_chs:
            text = read_text(project_root, ch)
            if any(kw in text for kw in keywords):
                recurring_chs.append(ch)
        if len(recurring_chs) >= 2:
            findings.append({
                "severity": "warning",
                "code": "FAILURE_RECURRING",
                "failure_id": fp.get("id") or fp.get("name", "?"),
                "recorded_at_ch": recorded_at_ch,
                "recurring_chs": recurring_chs,
                "keywords": keywords[:3],
                "suggestion": f"失败模式「{fp.get('id', fp.get('name'))}」在 ch{recorded_at_ch} 被记录后，又在 ch{recurring_chs[0]}+ 重现 → writer 没消费 lessons",
            })

    # SUCCESS_NEVER_REUSED
    for sp in success_patterns[-10:]:
        if not isinstance(sp, dict):
            continue
        recorded_at_ch = sp.get("recorded_at_ch") or sp.get("ch") or 0
        keywords = sp.get("keywords") or sp.get("trigger_keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        if not keywords or recorded_at_ch == 0:
            continue
        post_chs = [c for c in chapters if c > recorded_at_ch][:8]
        if len(post_chs) < 5:
            continue
        reuse_chs = []
        for ch in post_chs:
            text = read_text(project_root, ch)
            if any(kw in text for kw in keywords):
                reuse_chs.append(ch)
        if not reuse_chs:
            findings.append({
                "severity": "advisory",
                "code": "SUCCESS_NEVER_REUSED",
                "success_id": sp.get("id") or sp.get("name", "?"),
                "recorded_at_ch": recorded_at_ch,
                "post_chs_checked": post_chs,
                "suggestion": f"成功模式「{sp.get('id', sp.get('name'))}」(ch{recorded_at_ch}) 后续 {len(post_chs)} 章无任何复用 → 经验沉淀失败",
            })
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)

    findings = []

    # ===== 2026-05-29 cluster 化分支：账本有 cjk_count → A/B 走账本（cjk_count + summary）=====
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster 的章」
    use_ledger = (
        csr.is_cluster_mode()
        and csr.ledger_has_field(project_root, "cjk_count")
    )
    if use_ledger:
        recs = csr.get_chapter_records(project_root, last_n_clusters=args.last_n)
        chapters = [ch for ch, _ in recs]
        if not chapters:
            print("[SKIP] cluster 账本无章记录")
            sys.exit(0)
        # A：用账本 cjk_count 构造 lengths（缺 cjk_count 的章跳过该数据点）
        lengths = [(ch, int(rec["cjk_count"])) for ch, rec in recs
                   if isinstance(rec.get("cjk_count"), (int, float))]
        findings.extend(scan_length_distribution(project_root, chapters, lengths=lengths))
        # B：用账本 summary + text_keyword_set
        findings.extend(scan_summary_consistency_from_ledger(recs))
        # C：lessons 反馈环仍基于磁盘正文（账本无对应字段，原样保留向后兼容）
        findings.extend(scan_lessons_feedback(project_root, chapters))
    else:
        # ===== 原逐章磁盘逻辑（非 cluster 模式 / 账本缺字段 → 零回归）=====
        chapters = get_chapters(project_root, args.last_n)
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        findings.extend(scan_length_distribution(project_root, chapters))
        findings.extend(scan_summary_consistency(project_root, chapters))
        findings.extend(scan_lessons_feedback(project_root, chapters))

    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = {
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
    }
    report = {
        "scan_type": "meta_quality",
        "scan_ts": ts,
        "chapters_scanned": chapters,
        "findings": findings,
        "summary": summary,
    }
    out_path = out_dir / f"meta_quality_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[meta_quality] {summary['warning']} warning / {summary['advisory']} advisory")
    for f in findings[:6]:
        print(f"  [{f['severity'].upper()}] {f.get('code')}: {f.get('suggestion', '')[:80]}")
    print(f"报告: {out_path}")
    if summary["warning"] > 0:
        sys.exit(2)
    if summary["advisory"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
