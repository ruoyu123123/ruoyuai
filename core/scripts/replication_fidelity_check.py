#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作者金标准对比闸（量化层）

把生成正文的可量化风格指纹（句长/段长/单句独行/标点密度）跟本项目作者风格档基线对比，
标出偏离大的维度。默认作为离线分析输出 report；主链路通过 --strict 启用硬闸。

动机：机械维度检测(audit/reflector/voice)不比对真实作者基线，抓不出「调性跑偏」类问题。
情绪标点(感叹/问号/省略)缺口是「喜剧引擎没落地」的可量化代理信号；质性调性(市井喜剧vs惊悚)
判断需配 gemini/agent 读 golden_passages。

用法：
  python core/scripts/replication_fidelity_check.py --project <项目> --cluster 1
  python core/scripts/replication_fidelity_check.py --project <项目> --chapters 1-4
  python core/scripts/replication_fidelity_check.py --project <项目> --cluster 1 --strict
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


def _mstyle_cosine_subscore(root, gen_text):
    """mstyle 余弦风格子分（advisory·单一接入点·topic-confound 用相对作者自相似 z）。

    返回 dict 含 status: ok/invalid/skip。🔴 绝不静默 hash 冒充风格余弦·绝不抛异常
    （main 安全·永不影响 verdict/exit）。复用 style_similarity_scanner 已建的作者 centroid+自相似 σ。
    """
    sp = str(Path(__file__).resolve().parent)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    # ① 硬断言后端·失败→标 invalid 不参与判定（护栏·绝不 hash 冒充）
    try:
        import embedding_store as es
        es.assert_mstyle_backend()
    except Exception as e:
        return {"status": "invalid", "reason": f"后端非 mstyle/未装包：{e}"[:200],
                "_doc": "hash 后端→余弦子分自动标 invalid 不参与判定（绝不假风格信号）"}
    # ③ 复用 style_similarity_scanner 的作者 centroid+自相似分布（topic-confound：相对 z 非绝对值）
    try:
        import style_similarity_scanner as ss
        bl = ss._load_baseline(root) or ss.build_baseline(root)
        if not bl or "_skip" in bl:
            return {"status": "skip", "reason": (bl or {}).get("_skip", "无作者基线")}
        center = bl.get("center_embedding") or []
        gen_c = ss._text_centroid(gen_text)
        if not center or not gen_c:
            return {"status": "skip", "reason": "centroid 计算失败"}
        sim = es.cosine_similarity(center, gen_c)
        self_mean = (bl.get("self_similarity") or {}).get("mean")
        self_std = (bl.get("self_similarity") or {}).get("std")
        if self_mean is None or self_std is None:
            return {"status": "skip", "reason": "作者自相似分布缺失"}
        z = (sim - self_mean) / self_std if self_std > 1e-6 else 0.0
        return {"status": "ok", "cosine_to_author_center": round(sim, 4),
                "author_self_sim_mean": round(self_mean, 4), "author_self_sim_std": round(self_std, 4),
                "z_vs_author_self": round(z, 2), "backend": es.embedding_method(),
                "note": "相对作者自相似分布的 z（topic-confound 控制·绝对余弦受同题材抬高不直接用）"}
    except Exception as e:
        return {"status": "skip", "reason": f"余弦计算异常：{e}"[:200]}


# ════════════════════════════════════════════════════════════════
# intent_recovery（experiment · advisory）— 作者思维（B1-B3）确定性余弦判决
# ════════════════════════════════════════════════════════════════
# 设计：av_judge 的「作者思维」第 5 维（include_intent_dim）让 LLM-judge 反推一段
#   仿写在『价值取舍/情绪处理/信息释放』上的决策走向，但**判决权不交弱模型 verdict**——交确定性
#   mstyle 余弦：把 judge 反推文本 与 consolidate 聚合的 author_decision_principles
#   （B1-B3 去重观察）算 StyleDistance 余弦。
# 🔴 两条护栏（绝不 hash 冒充语义 · 北极星⑤⑥）：
#   ① 硬断言 embedding_store.assert_mstyle_backend()——hash 后端标 invalid 不出余弦（绝不假语义信号）。
#   ② author_decision_principles 序列化用 sort_keys 固定键序（同输入同输出 · 余弦可复现 · 复用
#      consolidate._merge_observations._key 的 json.dumps(sort_keys=True) 范式）。
# 全 advisory/experiment · 永不进 audit_hub.HARD_GATE_CODES · 永不阻断（exit 0）。


def _flatten_principles(principles) -> str:
    """把 author_decision_principles（嵌套 dict / list / str 混合）确定性序列化成稳定文本。

    用于喂 mstyle embed——必须**同输入同输出**（键序固定 · 余弦可复现）。规则（贴合 consolidate
    的 _merge_observations 产物形状：值或为 {subkey: [strings]} 嵌套、或为 list、或为 list[str]）：
      · dict：按 sorted(key) 递归展开 `key：<flatten(value)>`，行间换行。
      · list：逐项 flatten，'；' 连接（dict 项走 json.dumps(sort_keys=True) 稳定化）。
      · 标量：str()。
    顶层非 dict（防御）→ 直接 flatten 返回。
    """
    def _flat(v) -> str:
        if isinstance(v, dict):
            parts = []
            for k in sorted(v.keys(), key=lambda x: str(x)):
                if str(k).startswith("_"):   # 跳过 _raw_* / 内部字段（与 consolidate 一致）
                    continue
                parts.append(f"{k}：{_flat(v[k])}")
            return "\n".join(parts)
        if isinstance(v, list):
            items = []
            for x in v:
                if isinstance(x, (dict, list)):
                    items.append(json.dumps(x, ensure_ascii=False, sort_keys=True))
                else:
                    items.append(str(x))
            return "；".join(items)
        return str(v)

    return _flat(principles if principles is not None else {})


def intent_recovery_cosine(judge_reconstructed_text: str, author_b_principles) -> dict:
    """确定性 mstyle 余弦判决（零 LLM）：judge 反推文本 vs 作者 B1-B3 决策原则（author_decision_principles）。

    返回 {cosine, valid, method?, reason?}。判决落带由元验证（_intent_recovery_band · multi-ref 变异带）
    决定·此函数只产余弦不下判决（advisory）。

    🔴 护栏：
      · 后端非 mstyle（含默认 hash）→ valid=False + reason 含「≠mstyle」（绝不返回假余弦 · 防 hash 冒充）。
      · 空文本 / 空 principles → valid=False（无可比内容）。
    """
    sp = str(Path(__file__).resolve().parent)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    # ① 硬断言后端=mstyle（绝不 hash 冒充）
    try:
        import embedding_store as es
        es.assert_mstyle_backend()
    except Exception as e:
        method = ""
        try:
            import embedding_store as es  # noqa: F811
            method = es.embedding_method()
        except Exception:
            method = "(探测失败)"
        return {"cosine": None, "valid": False, "status": "invalid",
                "reason": f"后端={method}≠mstyle·intent_recovery 余弦不可信（hash 假语义）·跳过：{str(e)[:120]}"}
    # ③ 序列化作者原则 + 算余弦
    author_text = _flatten_principles(author_b_principles)
    jt = (judge_reconstructed_text or "").strip()
    if not jt or not author_text.strip():
        return {"cosine": None, "valid": False, "status": "skip",
                "reason": "judge 反推文本 或 author_decision_principles 为空·无可比内容"}
    try:
        v1 = es._mstyle_embed(jt)
        v2 = es._mstyle_embed(author_text)
        sim = es.cosine_similarity(v1, v2)
        return {"cosine": round(float(sim), 4), "valid": True, "status": "ok",
                "method": es.embedding_method()}
    except Exception as e:
        return {"cosine": None, "valid": False, "status": "skip",
                "reason": f"mstyle 余弦计算异常：{str(e)[:160]}"}


def _intent_recovery_band(author_texts: list, k: float = 2.0) -> dict:
    """用作者多章原文两两 mstyle 余弦的分布定义 multi-ref 变异带（mu±kσ）。

    复用「单 ref 失真→强制 multi-ref」纪律：≥2 段原文，两两 _mstyle_embed 余弦 → {mu, sigma, lo, hi, n_pairs}。
    band 随章数增加收窄（更多 pair → sigma 更稳）。后端非 mstyle / 段数 <2 → {valid: False, reason}。
    """
    sp = str(Path(__file__).resolve().parent)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    texts = [t for t in (author_texts or []) if isinstance(t, str) and t.strip()]
    if len(texts) < 2:
        return {"valid": False, "reason": f"multi-ref 变异带需 ≥2 段作者原文，当前 {len(texts)} 段"}
    try:
        import embedding_store as es
        es.assert_mstyle_backend()
    except Exception as e:
        return {"valid": False, "reason": f"后端非 mstyle：{str(e)[:120]}"}
    try:
        vecs = [es._mstyle_embed(t) for t in texts]
    except Exception as e:
        return {"valid": False, "reason": f"mstyle embed 异常：{str(e)[:120]}"}
    sims = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            sims.append(es.cosine_similarity(vecs[i], vecs[j]))
    if not sims:
        return {"valid": False, "reason": "无有效配对"}
    mu = st.mean(sims)
    sigma = st.pstdev(sims) if len(sims) >= 2 else 0.0
    return {"valid": True, "mu": round(mu, 4), "sigma": round(sigma, 4),
            "lo": round(mu - k * sigma, 4), "hi": round(min(1.0, mu + k * sigma), 4),
            "k": k, "n_pairs": len(sims), "n_refs": len(texts)}


def intent_recovery_probe(judge_text: str, author_principles, author_ref_texts: list,
                          k: float = 2.0, cross_stack: "dict | None" = None) -> dict:
    """intent_recovery 旁挂探针（advisory · 永不阻断）：余弦 + multi-ref 变异带 + 跨栈一致性。

    · cosine = intent_recovery_cosine(judge_text, author_principles)（确定性 mstyle）。
    · band = _intent_recovery_band(author_ref_texts, k)（作者多章原文两两余弦定义 mu±kσ）。
    · in_band = band.lo ≤ cosine ≤ band.hi（真作者落带内不误报 / 偏离稿落带外可分辨）。
    · cross_stack（可选 {'claude_cosine': float}）：gemini 反推余弦 + claude 反推余弦双双落带内 →
      localization_confidence=high；跨栈不一致 → low（不参与 active/shadow 决策 · 防单模型自偏循环）。
    恒 gate_level=advisory（顾问制 · 调用方 main 恒 exit 0）。
    """
    cos = intent_recovery_cosine(judge_text, author_principles)
    band = _intent_recovery_band(author_ref_texts, k)
    sim = cos.get("cosine")
    in_band = None
    if cos.get("valid") and band.get("valid") and sim is not None:
        in_band = bool(band["lo"] <= sim <= band["hi"])

    # 跨栈一致性：gemini 与 claude 反推余弦双双落带内才高置信
    localization_confidence = "low"
    cross = None
    if cross_stack and isinstance(cross_stack, dict):
        claude_cos = cross_stack.get("claude_cosine")
        claude_in = (band.get("valid") and claude_cos is not None
                     and band["lo"] <= claude_cos <= band["hi"])
        cross = {"claude_cosine": claude_cos, "claude_in_band": bool(claude_in)}
        if in_band and claude_in:
            localization_confidence = "high"
    elif in_band:
        # 无跨栈对照 → 单栈落带内只给 medium（单模型自偏未排除）
        localization_confidence = "medium"

    return {
        "tag": "intent_recovery",
        "gate_level": "advisory",   # ⚠️ 永远 advisory · 永不阻断 · 永不进 HARD_GATE_CODES
        "cosine": sim,
        "cosine_status": cos.get("status"),
        "cosine_valid": cos.get("valid", False),
        "cosine_reason": cos.get("reason"),
        "band": band,
        "in_band": in_band,
        "cross_stack": cross,
        "localization_confidence": localization_confidence,
        "method": cos.get("method"),
        "_doc": ("intent_recovery=av_judge 第5维(作者思维)反推 + 确定性 mstyle 余弦判决 · "
                 "真作者落带内防误报/偏离稿落带外证可分辨/跨栈双落带内防单模型自偏 · "
                 "全 advisory experiment · hash 后端自动 skip 不假语义"),
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
# 近零基线绝对阈值（每千字）：基线低于此值的 _k 密度维走绝对口径而非 ratio 带
#（ratio 对近零基线数学失效 · 见 main 内注释）
_NEARZERO_K = 0.5


def main():
    ap = argparse.ArgumentParser(description="作者金标准对比闸(量化层)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--cluster", type=int, default=None)
    ap.add_argument("--chapters", default=None, help="如 1-4")
    ap.add_argument("--strict", action="store_true",
                    help="主链路硬闸：无正文/无作者基线 exit2；存在偏离 exit1；无偏离 exit0")
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
        sys.exit(2 if args.strict else 0)

    base = _author_baseline(root)
    if not base:
        print("[replication_fidelity] 无作者风格档基线", file=sys.stderr)
        sys.exit(2 if args.strict else 0)

    gen = _metrics(text)
    issues = []
    for k, (lo, hi) in _BANDS.items():
        a = base.get(k)
        g = gen.get(k)
        if a is None or g is None or a == 0:
            continue
        # 🔴 近零基线护栏：per-1000 密度维基线 < 0.5/千 时 ratio 口径失效——生成 0 次
        # → 0.0x 假偏离；短稿哪怕 1 次命中密度也 >2.2x 假偏离，数学上无法通过。
        # 改绝对口径：两边都「几乎不用」（生成 ≤ 0.5/千）= 贴合跳过；
        # 生成 > 0.5/千 才按偏离报（作者不用而生成在用，仍是真信号）。
        if k.endswith("_k") and a < _NEARZERO_K:
            if g > _NEARZERO_K:
                issues.append({"dim": _LABEL[k], "key": k, "gen": g, "author": round(a, 2),
                               "ratio": round(g / a, 2), "band": [lo, hi], "tag": "advisory",
                               "note": f"近零基线绝对口径：作者 {a}/千 < {_NEARZERO_K} 而生成 {g}/千 超阈"})
            continue
        ratio = g / a
        if not (lo <= ratio <= hi):
            sev = "comedy_engine" if (k in _COMEDY_PUNCT and ratio < lo) else "advisory"
            issues.append({"dim": _LABEL[k], "key": k, "gen": g, "author": round(a, 1),
                           "ratio": round(ratio, 2), "band": [lo, hi], "tag": sev})

    report = {"verdict": "fail" if (args.strict and issues) else ("advisory" if issues else "pass"),
              "strict": bool(args.strict),
              "gen": gen, "author": base, "issues": issues,
              "_doc": "作者金标准量化对比·情绪标点(感叹/问号/省略)偏低=喜剧引擎未落地代理信号；--strict 下偏离阻断主链路"}
    report["mstyle_cosine"] = _mstyle_cosine_subscore(root, text)   # advisory 附加·永不影响 verdict/exit
    if report["mstyle_cosine"].get("status") == "invalid":
        print(f"  [mstyle余弦] invalid（{report['mstyle_cosine'].get('reason', '')[:60]}）· 不参与判定", file=sys.stderr)
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
    sys.exit(1 if (args.strict and issues) else 0)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
