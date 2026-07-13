#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anachrony_order_scanner.py — Genette 时序 order 维度检测（advisory · cluster · 2026-06-20）

【缺口】R9 联网调研：Genette《Narrative Discourse》(1980) 的时序 order（叙事顺序 vs 故事顺序）
是叙事学三大维度（order/duration/frequency）之一。此前全系统：
  · R6 anachronism_scanner 查【时代错位/穿帮】（古代场景出现现代词·跨现实时间轴）
  · R8 duration_mix_scanner 查【时长比】（scene/summary/ellipsis/pause/stretch）
  · 【时序 order 维度零覆盖】——文本内回顾（analepsis 倒叙）/前瞻（prolepsis 闪前）锚词分布
  没有任何 scanner 测。LLM 默认按 linear 写=时序扁平·留白闪回/前瞻笔法（"多年以后"/"那时
  他不知道，将来…"）几乎不出现。本 scanner 补检测端。

【做法 · 确定性纯规则正则（不依赖 LLM）】：
  1. 时序锚词典：
     · analepsis（倒叙 / 回顾·指向过去）：
       - internal_homo（在叙事主线内回顾同一主角已发生事件）:"刚才/方才/此前/那时/早前/不久前/适才"
       - external_homo（叙事主线开始前·同一主角的过去）:"小时候/年少时/当年/儿时/幼时/十年前/多年前/早年/从前"
       - hetero（他人的过去/历史回顾）:"祖父曾说/古时/上古/历史上/史载/相传/传说中/民间相传/古书有云"
     · prolepsis（闪前/前瞻·指向未来）:"后来/日后/许多年后/多年后/未来/将来/此后/将会/再后来/再次回到"
  2. 草稿命中每个锚词记 hit·分类·算 per_1k。
  3. baseline 门控：作者档 anachrony_baseline 第一权威·无→通用兜底
     （analepsis_per_1k_min=0.3 + prolepsis_per_1k_min=0.05）
  4. anachrony 总密度 < baseline_min → advisory「时序扁平·缺回顾/前瞻锚」。

【与 R6 anachronism_scanner 严格去重】R6 是【时代错位】（古代场景出现现代词=穿帮 bug 方向）·
  本 scanner 是【时序 order 维度】（叙事顺序 vs 故事顺序·工艺方向）·两者完全正交·零词典重叠。

【北极星② / ⑤ 顾问非法官】作者档 anachrony_baseline 第一权威·线性叙事是合理风格 → 永远 advisory，
  code ANACHRONY_ORDER_THIN **绝不进 audit_hub.HARD_GATE_CODES**。
  env ANACHRONY_ORDER_MODE: off / shadow(默认·只记不判·零回归) / active。

用法：python anachrony_order_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ANACHRONY_ORDER_THIN"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# Analepsis（回顾·过去）三子类锚词典
ANALEPSIS_TERMS = {
    "internal_homo": re.compile(r"刚才|方才|此前|那时|早前|不久前|适才|片刻前|先前"),
    "external_homo": re.compile(r"小时候|年少时|当年|儿时|幼时|十年前|多年前|早年|从前|那一年|那时候|童年"),
    "hetero": re.compile(r"古时|上古|历史上|史载|相传|传说中|民间相传|古书有云|典籍记载|史书记载"),
}
# Prolepsis（闪前·未来）锚词
PROLEPSIS_TERMS = re.compile(
    r"后来|日后|许多年后|多年后|未来|将来|此后|将会|再后来|再次回到|后来才知道|往后|将要|多年以后")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 500
SAMPLE_LIMIT = 6
# 通用兜底（无作者档时·宽松阈值防误伤线性叙事）
DEFAULT_ANALEPSIS_FLOOR = 0.3   # per 1k CJK
DEFAULT_PROLEPSIS_FLOOR = 0.05


def _mode() -> str:
    m = (os.environ.get("ANACHRONY_ORDER_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_baseline(project_root):
    """读作者档 anachrony_baseline·无→None。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "narrative_signature.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get("anachrony_baseline")
        if isinstance(v, dict):
            return v
    return None


def detect_anachrony(text: str) -> dict:
    """检测 analepsis/prolepsis 五分类命中。

    返回 {analepsis: {internal_homo, external_homo, hetero, total},
          prolepsis_total, total_hits, samples: {category: [terms]}}。
    """
    text = _strip_changes(text)
    samples = {}
    ana = {"internal_homo": 0, "external_homo": 0, "hetero": 0, "total": 0}
    for cat, rx in ANALEPSIS_TERMS.items():
        hits = rx.findall(text)
        ana[cat] = len(hits)
        ana["total"] += len(hits)
        if hits:
            samples[cat] = list(dict.fromkeys(hits))[:4]
    prol_hits = PROLEPSIS_TERMS.findall(text)
    prol_total = len(prol_hits)
    if prol_hits:
        samples["prolepsis"] = list(dict.fromkeys(prol_hits))[:4]
    return {
        "analepsis": ana,
        "prolepsis_total": prol_total,
        "total_hits": ana["total"] + prol_total,
        "samples": samples,
    }


def scan(draft_path, project_root=None) -> dict:
    """Genette 时序 order 检测。永远 advisory（北极星②/⑤）。"""
    mode = _mode()
    out = {"scanner": "anachrony_order", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    result = detect_anachrony(draft)
    per_1k_base = cjk / 1000.0
    analepsis_per_1k = round(result["analepsis"]["total"] / per_1k_base, 3)
    prolepsis_per_1k = round(result["prolepsis_total"] / per_1k_base, 3)

    baseline = _read_baseline(project_root)
    if baseline:
        ana_floor = float(baseline.get("analepsis_per_1k_min", DEFAULT_ANALEPSIS_FLOOR))
        prol_floor = float(baseline.get("prolepsis_per_1k_min", DEFAULT_PROLEPSIS_FLOOR))
        baseline_source = "author_profile"
    else:
        ana_floor, prol_floor = DEFAULT_ANALEPSIS_FLOOR, DEFAULT_PROLEPSIS_FLOOR
        baseline_source = "default_fallback"

    out["analepsis_per_1k"] = analepsis_per_1k
    out["prolepsis_per_1k"] = prolepsis_per_1k
    out["analepsis_subtypes"] = result["analepsis"]
    out["samples"] = result["samples"]
    out["baseline_source"] = baseline_source
    out["thresholds"] = {"analepsis_floor": ana_floor, "prolepsis_floor": prol_floor}

    # 单向：仅报「过低」（线性叙事工艺扁平）；过高不报（密集回顾/前瞻是合理风格选择·北极星③）
    flat = analepsis_per_1k < ana_floor and prolepsis_per_1k < prol_floor
    if flat:
        msg = (f"时序扁平：回顾(analepsis) {analepsis_per_1k}/千字 < {ana_floor}·"
               f"前瞻(prolepsis) {prolepsis_per_1k}/千字 < {prol_floor}·叙事顺序贴近 linear。"
               f"建议引入回顾锚词(刚才/当年/史载…)或前瞻锚词(后来/多年后/将来…)"
               f"丰富 Genette 时序 order 维度·线性叙事可豁免")
        if mode == "active":
            out["violations"].append({
                "kind": "anachrony_order_thin", "severity": "minor",
                "message": msg,
                "analepsis_per_1k": analepsis_per_1k,
                "prolepsis_per_1k": prolepsis_per_1k,
                "samples": result["samples"],
                "_doc": "Genette Narrative Discourse 1980·order 维(叙事 vs 故事顺序)·"
                        "analepsis 五分类 homo/hetero/internal/external·prolepsis 五种·"
                        "线性叙事合理→advisory·与 R6 时代错位 + R8 时长比正交"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] anachrony_order: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Genette 时序 order 维度检测（advisory）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 anachrony_baseline 第一权威")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
