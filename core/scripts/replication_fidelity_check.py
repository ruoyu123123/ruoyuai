#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作者金标准对比闸（量化层）· 2026-06-04

把生成正文的可量化风格指纹（句长/段长/单句独行/标点密度）跟本项目作者风格档基线对比，
标出偏离大的维度。北极星：顾问制 advisory · 永不阻断（exit 0）· 真作者档=第一权威。

动机（cluster_001 翻车）：原有质检全过(audit/reflector/voice)却没抓到「调性跑偏成惊悚」，
因为它们查机械维不拿真作者基线比。情绪标点(感叹/问号/省略)缺口最离谱(实测 24x/3.7x/5x↓)
是「喜剧引擎没落地」的可量化代理信号。质性调性(市井喜剧vs惊悚)需配 gemini/agent 读 golden_passages。

用法：
  python core/scripts/replication_fidelity_check.py --project <项目> --cluster 1
  python core/scripts/replication_fidelity_check.py --project <项目> --chapters 1-4
"""
import argparse
import glob
import io
import json
import re
import statistics as st
import sys
from pathlib import Path


def _cjk(s):
    return sum(1 for c in s if "一" <= c <= "鿿")


def _metrics(text):
    paras = [p for p in re.split(r"\n\n+", text) if p.strip()]
    sents = []
    for p in paras:
        for s in re.split(r"[。！？…]+", p):
            c = _cjk(s)
            if c >= 2:
                sents.append(c)
    plens = [_cjk(p) for p in paras]
    single = sum(1 for p in paras if len(re.findall(r"[。！？…]", p)) <= 1)
    total = _cjk(text)
    k = total / 1000 if total else 1
    return {
        "sentence_mean": round(st.mean(sents), 1) if sents else 0,
        "para_mean": round(st.mean(plens), 1) if plens else 0,
        "single_para_ratio": round(single / len(paras), 3) if paras else 0,
        "comma_k": round(text.count("，") / k, 1),
        "period_k": round(text.count("。") / k, 1),
        "dash_k": round(text.count("——") / k, 1),
        "ellipsis_k": round((text.count("……") + text.count("…")) / k, 1),
        "excl_k": round(text.count("！") / k, 1),
        "ques_k": round(text.count("？") / k, 1),
        "cjk": total,
    }


def _author_baseline(project_root):
    p = project_root / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    q = d.get("quantitative", {})
    if not isinstance(q, dict):
        return None

    def g(*path):
        cur = q
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return None
        return cur if isinstance(cur, (int, float)) else None

    pd = q.get("punctuation_density_per_1000", {}) or {}

    def pg(k):
        v = pd.get(k, {})
        return v.get("mean") if isinstance(v, dict) else (v if isinstance(v, (int, float)) else None)

    return {
        "sentence_mean": g("sentence_length", "mean"),
        "para_mean": g("paragraph_length_chars", "mean") or g("paragraph_length", "mean_chars"),
        "single_para_ratio": g("single_sentence_para_ratio") or g("paragraph_length", "single_sentence_para_ratio_mean"),
        "comma_k": pg("comma"), "period_k": pg("period"), "dash_k": pg("dash"),
        "ellipsis_k": pg("ellipsis"), "excl_k": pg("exclamation"), "ques_k": pg("question"),
    }


_BANDS = {
    "sentence_mean": (0.80, 1.25), "para_mean": (0.80, 1.30), "single_para_ratio": (0.80, 1.20),
    "comma_k": (0.75, 1.30), "period_k": (0.70, 1.40),
    "dash_k": (0.45, 2.2), "ellipsis_k": (0.45, 2.2), "excl_k": (0.40, 2.5), "ques_k": (0.45, 2.2),
}
_LABEL = {
    "sentence_mean": "句长", "para_mean": "段长", "single_para_ratio": "单句独行率",
    "comma_k": "逗号/千", "period_k": "句号/千", "dash_k": "破折号/千",
    "ellipsis_k": "省略号/千", "excl_k": "感叹号/千", "ques_k": "问号/千",
}
_COMEDY_PUNCT = {"excl_k", "ellipsis_k", "ques_k"}


def main():
    ap = argparse.ArgumentParser(description="作者金标准对比闸(量化层·advisory)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", type=int, default=None)
    ap.add_argument("--chapters", default=None, help="如 1-4")
    args = ap.parse_args()
    root = Path(args.project).resolve()

    text = ""
    if args.chapters:
        a, b = map(int, args.chapters.split("-"))
        for n in range(a, b + 1):
            f = root / "章节" / f"第{n:03d}章" / f"第{n:03d}章.txt"
            if f.exists():
                text += f.read_text(encoding="utf-8") + "\n\n"
    elif args.cluster is not None:
        dpath = root / "章节" / f"cluster_{args.cluster:03d}_draft" / f"cluster_{args.cluster:03d}_draft.txt"
        if dpath.exists():
            text = dpath.read_text(encoding="utf-8")
        else:
            for f in sorted(glob.glob(str(root / "章节" / "第*章" / "第*章.txt"))):
                text += io.open(f, encoding="utf-8").read() + "\n\n"
    if not text.strip():
        print("[replication_fidelity] 无生成正文可比", file=sys.stderr)
        sys.exit(0)

    base = _author_baseline(root)
    if not base:
        print("[replication_fidelity] 无作者风格档基线 → 跳过(放行)", file=sys.stderr)
        sys.exit(0)

    gen = _metrics(text)
    issues = []
    for k, (lo, hi) in _BANDS.items():
        a = base.get(k)
        g = gen.get(k)
        if a is None or g is None or a == 0:
            continue
        ratio = g / a
        if not (lo <= ratio <= hi):
            sev = "comedy_engine" if (k in _COMEDY_PUNCT and ratio < lo) else "advisory"
            issues.append({"dim": _LABEL[k], "key": k, "gen": g, "author": round(a, 1),
                           "ratio": round(ratio, 2), "band": [lo, hi], "tag": sev})

    report = {"verdict": "advisory" if issues else "pass", "gen": gen, "author": base, "issues": issues,
              "_doc": "作者金标准量化对比·顾问制·情绪标点(感叹/问号/省略)偏低=喜剧引擎未落地代理信号"}
    out_dir = root / "_数据库" / ".audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"cluster_{args.cluster:03d}" if args.cluster is not None else (args.chapters or "all")
    (out_dir / f"replication_fidelity_{tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"== 作者金标准对比闸（{tag}）==")
    print(f"  verdict: {report['verdict']} · 偏离维度 {len(issues)}")
    comedy = [i for i in issues if i["tag"] == "comedy_engine"]
    if comedy:
        print("  [喜剧引擎代理信号]情绪标点偏低：")
        for i in comedy:
            print(f"     {i['dim']}: 生成 {i['gen']} vs 作者 {i['author']}（{i['ratio']}x·带{i['band']}）")
    for i in issues:
        if i["tag"] != "comedy_engine":
            print(f"  · {i['dim']}: 生成 {i['gen']} vs 作者 {i['author']}（{i['ratio']}x）")
    sys.exit(0)


if __name__ == "__main__":
    main()
