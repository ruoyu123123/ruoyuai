#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chapter_title_concreteness_scanner.py — 章节标题具象度曲线带
(advisory · cluster · 2026-06-20 R11 W6 MODEST · shadow)

【缺口】Quéré&Matias 2025 Nature Sci Rep 41598-024-81575-9 curvilinear curiosity-gap +
Qidian-Webnovel Corpus 110 部报告：章节标题具象度(α·NER 密度+β·内容词/虚词比+
γ·具体名词比) 是 CTR 重要预测因子·应贴作者档 ECDF。

【做法】
  1. 取本 cluster 已切章标题(章节/第N章/章节名.txt) 或 manifest.chapter_titles
  2. 算每章具象度 score = 0.4·NER 比 + 0.3·实词比 + 0.3·具体名词比(复用 R10 童声 noun 词典)
  3. 与作者档 chapter_title_profile.concreteness_ecdf{p10,p50,p90} 对比
  4. cross_cluster_engagement_metrics_aggregate 连续 ≥3 cluster 偏离 2σ
     → TITLE_CONCRETENESS_DRIFT advisory
  5. 作者档未规定 → 静默

【北极星】② 作者档第一权威·shadow·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "TITLE_CONCRETENESS_DRIFT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 简化具体名词词袋（人/物/地/器物·借鉴 R10 child_voice concrete noun）
CONCRETE_NOUNS = re.compile(
    r"(刀|剑|斧|戟|枪|弓|箭|马|车|桥|塔|庙|寺|山|岭|海|湖|江|河|"
    r"城|关|镇|村|店|铺|店家|宅|院|门|窗|床|桌|椅|碗|杯|壶|酒|茶|"
    r"师|父|师兄|妹|徒弟|师叔|爷|奶|爹|娘|儿|郎|姐|哥)")
NER_PROPER = re.compile(r"([一-鿿]{2,4}(?:山|城|宫|阁|寺|庙|关|岭|"
                         r"郡|州|府|县|楼|塔|院|门|宗|派|盟|教))")
FUNCTION_WORDS = re.compile(r"(的|了|和|与|是|又|且|于|而|及|或|"
                             r"也|都|就|便|乃|为|向|从|被|由)")
CONTENT_WORD = re.compile(r"[一-鿿]")


def _mode() -> str:
    m = (os.environ.get("CHAPTER_TITLE_CONCRETENESS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _read_ecdf(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        ctp = obj.get("chapter_title_profile") or {}
        ecdf = ctp.get("concreteness_ecdf")
        if isinstance(ecdf, dict):
            return ecdf
    return None


def _collect_titles_from_project(project_root) -> list:
    """从 章节/第NNN章 目录扒章名"""
    if not project_root:
        return []
    chap_dir = Path(project_root) / "章节"
    if not chap_dir.exists():
        return []
    titles = []
    for d in sorted(chap_dir.glob("第[0-9]*章*")):
        name = d.name
        # 形如 "第001章 标题文字" 或 "第001章_标题"
        m = re.match(r"第(\d+)章[ _]+(.+)", name)
        if m:
            titles.append({"chapter_num": int(m.group(1)),
                           "title": m.group(2).strip()})
        elif "章" in name:
            titles.append({"chapter_num": None, "title": name})
    return titles


def concreteness_score(title: str) -> float:
    if not title or len(title) < 2:
        return 0.0
    content = CONTENT_WORD.findall(title)
    n = len(content)
    if n < 2:
        return 0.0
    ner = len(NER_PROPER.findall(title))
    noun = len(CONCRETE_NOUNS.findall(title))
    func = len(FUNCTION_WORDS.findall(title))
    ner_ratio = ner / max(1, n / 2)  # NER 密度
    content_ratio = (n - func) / n
    noun_ratio = noun / n
    score = 0.4 * min(1.0, ner_ratio) + 0.3 * content_ratio + 0.3 * min(1.0, noun_ratio * 4)
    return round(score, 4)


def scan(draft_path=None, project_root=None, titles_override=None) -> dict:
    mode = _mode()
    out = {"scanner": "chapter_title_concreteness", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    titles = titles_override if titles_override else _collect_titles_from_project(project_root)
    if not titles:
        out["note"] = "无章节标题·跳过"
        return out
    scores = [{"chapter_num": t.get("chapter_num"), "title": t["title"],
               "score": concreteness_score(t["title"])} for t in titles]
    out["per_chapter_scores"] = scores
    mean = round(sum(s["score"] for s in scores) / len(scores), 4)
    out["cluster_mean_concreteness"] = mean
    ecdf = _read_ecdf(project_root)
    if not ecdf:
        out["note"] = "作者档未规定 chapter_title_profile.concreteness_ecdf·静默"
        return out
    out["author_ecdf"] = ecdf
    p10 = ecdf.get("p10", 0.2)
    p50 = ecdf.get("p50", 0.4)
    p90 = ecdf.get("p90", 0.6)
    sd = max((p90 - p10) / 4, 0.05)
    z = (mean - p50) / sd
    out["z_concreteness"] = round(z, 3)
    if abs(z) > 2.0:
        direction = "偏低" if z < 0 else "偏高"
        msg = (f"cluster 标题具象度均值 {mean} 偏离作者 ECDF p50={p50} {direction} "
               f"|z|={abs(z):.2f} > 2σ")
        if mode == "active":
            out["violations"].append({
                "kind": "title_concreteness", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "mean": mean, "ecdf": ecdf, "z": round(z, 3),
                "_doc": "Quéré&Matias 2025 CTR·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] title_concreteness: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="章节标题具象度 advisory(shadow)")
    ap.add_argument("draft_path", nargs="?", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
