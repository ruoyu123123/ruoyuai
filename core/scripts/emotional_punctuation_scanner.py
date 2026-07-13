#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotional_punctuation_scanner.py — 情绪标点综合密度 vs 作者基线（advisory · cluster · 2026-06-16）

【缺口】穷尽核查 wyo52es0z #2 confirmed·feedback_author_goldstandard 实证「情绪标点（感叹/问号/
省略号）偏低 = 喜剧/情绪引擎没落地的代理信号」（弱模型复刻惊悚乐园情绪标点偏低）。全库无情绪标点回查。

【做法 · 确定性可算半边 + 🔴金标准防矫枉过正】：
  cluster 综合情绪标点密度（！+？+… per 1k CJK）vs 作者档三类 mean 之和（comb_mean）。
  🔴 金标准校准（2026-06-16·6 作者各 10 cluster 实测）：
     · 单类方差极大（真作者 cluster/mean 低到 excl 0.15 / ques 0.23）→ 单类单边下尾【必误报】
       真作者冷静叙述段（北极星⑤红线）→ 弃单类。
     · 综合（三类合计）方差小（全作者最小 0.44）→ 用综合 + FLOOR_RATIO 0.3（< 0.44 留余量·
       真作者绝不误报）。只报「综合情绪标点 < 作者 mean × 0.3」（严重偏低 = 情绪扁平）。
  偏高永不报（北极星③·作者高情绪标点是风格）。无作者档 → skip（不臆造基线·北极星②第一权威）。

【北极星⑤ 顾问非法官】情绪浓度是创作选择（冷静叙述合理）·永远 advisory·
  code EMOTIONAL_PUNCT_SPARSE **绝不进 audit_hub.HARD_GATE_CODES**。
  env EMOTIONAL_PUNCT_MODE: off / shadow（默认·只记不判·检测力待 gen-model 草稿验证再 active）/ active。

用法：python emotional_punctuation_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "EMOTIONAL_PUNCT_SPARSE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES
# 金标准校准：综合情绪标点 cluster/mean 全作者最小 0.44（惊悚乐园高方差离群）→ 0.3 留 0.14 余量不误报
EMOTIONAL_PUNCT_FLOOR_RATIO = 0.3
_EXCL, _QUES, _ELL = "！", "？", "…"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("EMOTIONAL_PUNCT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _author_comb_mean(project_root):
    """读作者档三类情绪标点 mean 之和（北极星②第一权威）。缺/非数 → None（scanner skip）。

    复用 replication_fidelity_check._author_baseline 同口径（quantitative
    .punctuation_density_per_1000.{exclamation,question,ellipsis}·{mean:..} 或纯数字均容错）。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    pd = (d.get("quantitative", {}) or {}).get("punctuation_density_per_1000", {}) or {}
    if not isinstance(pd, dict):
        return None

    def pg(k):
        v = pd.get(k, {})
        if isinstance(v, dict):
            return v.get("mean")
        return v if isinstance(v, (int, float)) else None

    vals = [pg("exclamation"), pg("question"), pg("ellipsis")]
    if not all(isinstance(x, (int, float)) for x in vals):
        return None
    s = sum(vals)
    return s if s > 0 else None


def scan(draft_path, manifest_path=None, project_root=None) -> dict:
    """情绪标点综合密度回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "emotional_punctuation",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 北极星⑤ · 绝不 hard_gate
        "warning": None,
        "violations": [],           # 对齐 audit_hub._parse_violations_scanner（shadow 空）
        "verdict": "PASS",
    }
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短（<500 CJK）·情绪标点密度不可估·跳过"
        return out

    comb_mean = _author_comb_mean(project_root)
    if comb_mean is None:
        out["note"] = "无作者档情绪标点基线（北极星②第一权威）·skip 不臆造"
        return out

    k = cjk / 1000.0
    comb_density = round((draft.count(_EXCL) + draft.count(_QUES) + draft.count(_ELL)) / k, 2)
    floor = round(comb_mean * EMOTIONAL_PUNCT_FLOOR_RATIO, 2)
    out["comb_density_per_1k"] = comb_density
    out["author_comb_mean"] = round(comb_mean, 2)
    out["floor"] = floor
    out["cjk_count"] = cjk

    # 单边下尾：只报「严重偏低」（< mean×0.3）·偏高永不报（北极星③·高情绪标点是作者风格）
    over = comb_density < floor
    if over:
        msg = (f"情绪标点综合密度 {comb_density}/千字 < {floor}（作者基线 {round(comb_mean, 2)}×"
               f"{EMOTIONAL_PUNCT_FLOOR_RATIO}）·情绪扁平（感叹/问号/省略号严重偏少·"
               f"建议查喜剧/情绪引擎是否落地）")
        if mode == "active":
            out["violations"].append({
                "kind": "emotional_punct_sparse", "severity": "minor",
                "message": msg, "comb_density": comb_density, "floor": floor,
                "author_mean": round(comb_mean, 2),
                "_doc": "情绪浓度是创作判断·冷静叙述有时合理→advisory 待裁决·密度是可算半边粗糙哨兵·"
                        "真情绪表达质量留 judge/作者（金标准：综合避单类误报·0.3<真作者最小0.44）",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] emotional_punctuation: {msg} — 不上报判决", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="情绪标点综合密度 vs 作者基线回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参（保留）")
    ap.add_argument("--project", default=None, help="读作者情绪标点基线（北极星②第一权威）")
    args = ap.parse_args()
    report = scan(args.draft_path, args.manifest, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·恒 exit 0（不阻断·北极星⑤）·active 有 warning 才 exit 1
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
