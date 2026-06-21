#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rhetorical_balance_scanner.py — 陈望道 38 格四类分布 advisory · R22 W10 Batch-DD · P0 STRONG

【缺口·R22 STRONG】《修辞学发凡》38 格四类(材料/意境/词语/章句)整张热图全空白。

【做法 · 确定性 · 零 LLM/零联网】
  · 复用 rhetorical_inventory 算 4 类分布·四类 share + Shannon 熵
  · 作者档 rhetorical_signature.distribution 第一权威·对称 KL 散度 > KL_THRESHOLD → 提示某类辞格塌缩
  · 无作者档兜底 KL_THRESHOLD = 0.5
  · share < 0.05 → 该类塌缩 advisory

【三 advisory】
  · RHETORICAL_BALANCE_DRIFT — KL 散度超阈值（整体分布偏离）
  · RHETORICAL_CATEGORY_COLLAPSE — 某类 share < 0.05（单类塌缩）
  · RHETORICAL_INVENTORY_THIN — 总 hits / kCJK < 兜底 4.0（修辞密度过低）

【与既有 scanner 严格正交】
  · zeugma / anadiplosis 单格深扫 → 本者四类分类层
  · semantic_slop / repeat_noun_density → 本者查 38 格分布
  · validate_style → 段长/标点·不查辞格分布

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  RHETORICAL_* 绝不进 audit_hub.HARD_GATE_CODES。

env RHETORICAL_BALANCE_MODE: off / shadow(默认) / active
用法: python rhetorical_balance_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rhetorical_inventory as ri  # noqa: E402

ISSUE_CODE_DRIFT = "RHETORICAL_BALANCE_DRIFT"
ISSUE_CODE_COLLAPSE = "RHETORICAL_CATEGORY_COLLAPSE"
ISSUE_CODE_THIN = "RHETORICAL_INVENTORY_THIN"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

DEFAULT_KL_THRESHOLD = 0.5
DEFAULT_COLLAPSE_SHARE = 0.05
DEFAULT_THIN_PER_KCJK = 4.0
DEFAULT_DISTRIBUTION = {"material": 0.25, "imagery": 0.25, "wording": 0.25, "syntax": 0.25}


def _mode() -> str:
    m = (os.environ.get("RHETORICAL_BALANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            sig = obj.get("rhetorical_signature")
            if isinstance(sig, dict):
                return sig
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "rhetorical_balance", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    inv = ri.load_inventory()
    per_figure = ri.count_figure_hits(text, inv)
    cat_tot = ri.category_totals(per_figure)
    total_hits = sum(cat_tot.values())
    distribution = ri.category_distribution(text, inv)
    entropy = ri.shannon_entropy(distribution)
    per_kcjk = round(total_hits / (cjk / 1000.0), 3) if cjk else 0.0

    baseline = _read_author_baseline(project_root)
    target_dist = dict(DEFAULT_DISTRIBUTION)
    kl_threshold = DEFAULT_KL_THRESHOLD
    collapse_share = DEFAULT_COLLAPSE_SHARE
    thin_pkc = DEFAULT_THIN_PER_KCJK
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        bd = baseline.get("distribution")
        if isinstance(bd, dict):
            s = sum(float(bd.get(c, 0)) for c in ri.CATEGORIES)
            if s > 0:
                target_dist = {c: float(bd.get(c, 0)) / s for c in ri.CATEGORIES}
        if isinstance(baseline.get("kl_threshold"), (int, float)):
            kl_threshold = float(baseline["kl_threshold"])
        if isinstance(baseline.get("collapse_share"), (int, float)):
            collapse_share = float(baseline["collapse_share"])
        if isinstance(baseline.get("thin_per_kcjk"), (int, float)):
            thin_pkc = float(baseline["thin_per_kcjk"])

    kl = ri.kl_divergence(distribution, target_dist)

    out.update({
        "cjk": cjk,
        "per_figure": per_figure,
        "category_totals": cat_tot,
        "category_distribution": {c: round(v, 4) for c, v in distribution.items()},
        "entropy_bits": entropy,
        "total_hits": total_hits,
        "per_kcjk": per_kcjk,
        "kl_vs_baseline": kl,
        "baseline_source": baseline_source,
        "baseline_distribution": {c: round(v, 4) for c, v in target_dist.items()},
        "thresholds": {
            "kl_threshold": kl_threshold,
            "collapse_share": collapse_share,
            "thin_per_kcjk": thin_pkc,
        },
    })

    flags = []
    if per_kcjk < thin_pkc:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"修辞密度={per_kcjk}/kCJK < {thin_pkc}·38 格热图整体稀薄"})
    for c in ri.CATEGORIES:
        share = distribution.get(c, 0.0)
        # only flag collapse when there's meaningful coverage elsewhere
        if total_hits >= 8 and share < collapse_share:
            flags.append({"code": ISSUE_CODE_COLLAPSE,
                          "msg": f"{c} 类 share={share:.3f} < {collapse_share}·该类辞格塌缩"})
    if total_hits >= 8 and kl > kl_threshold:
        flags.append({"code": ISSUE_CODE_DRIFT,
                      "msg": f"对称 KL={kl} > {kl_threshold}·四类分布偏离作者档"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "rhetorical_balance", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "陈望道《修辞学发凡》38 格四类·R22 STRONG·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] rhetorical_balance: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="陈望道 38 格四类修辞分布 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
