#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""premise_blend_card_scanner.py — outline premise_blend_card 落地度 + vital_relations · R22 W10 Batch-FF · P1+P2

【缺口】outline 阶段登记的 conceit(两个 input space frame) 是否真在 cluster 草稿落地·
全系统零检测·CBT(Fauconnier&Turner) 整合 emergent meaning 是否退化 (堆词 vs 真 blend)。

【输入】
  · cluster 草稿(必)
  · 项目根(可选)·读 _数据库/事件簇.json.clusters[i].premise_blend_card
    schema:
      premise_blend_card = {
        "blend_type": "single_scope|double_scope|mirror|simplex",
        "input_space_A": {"frame": "<frame 名>", "signature_lexemes": [...]},
        "input_space_B": {"frame": "<frame 名>", "signature_lexemes": [...]},
        "generic_space": "<两 frame 共有抽象层>",
        "emergent_structure": ["≥3 条 emergent_meaning"],
        "vital_relations_compressed": ["identity", "causation", ...]
      }
  · 内嵌 vital_relations_probe (零 LLM 7 类标记)

【四 advisory】
  · PREMISE_BLEND_CARD_MISSING       — outline 未填卡(无 premise_blend_card 节)
  · PREMISE_BLEND_EMERGENCE_MISSING  — 已填卡 + emergent_structure≥3·但草稿 vital_relations_density 跌出作者档 ±2σ band
  · PREMISE_BLEND_INPUT_DROP         — signature_lexemes A 或 B hit==0(单边塌缩=Blend 退化为单 frame)
  · PREMISE_BLEND_LEXICAL_OVER       — A+B signature lex_hits/kCJK > 阈值 + relation_density 低 → 堆词非真 blend

【与既有 scanner 严格正交】
  · rhetorical_balance / inventory  → 38 格分布层
  · semantic_slop                   → 句级 stale 词
  · 本者                            → outline 概念整合卡落地度

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  PREMISE_BLEND_* 绝不进 audit_hub.HARD_GATE_CODES。

env PREMISE_BLEND_MODE: off / shadow(默认) / active
用法: python premise_blend_card_scanner.py <draft> [--project <root>] [--cluster <cluster_id>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vital_relations_probe as vrp  # noqa: E402

ISSUE_CODE_MISSING = "PREMISE_BLEND_CARD_MISSING"
ISSUE_CODE_EMERGENCE = "PREMISE_BLEND_EMERGENCE_MISSING"
ISSUE_CODE_INPUT_DROP = "PREMISE_BLEND_INPUT_DROP"
ISSUE_CODE_LEXICAL_OVER = "PREMISE_BLEND_LEXICAL_OVER"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 兜底阈值 (作者档第一权威覆盖)
DEFAULT_DENSITY_FLOOR = 4.0   # vital_relations_density < 4.0/kCJK = emergent meaning 退化
DEFAULT_DENSITY_CEIL = 30.0   # vital_relations_density > 30/kCJK = 关系词堆砌
DEFAULT_LEX_OVER_PKCJK = 6.0  # A+B signature_lexemes hit/kCJK 上限 (堆词阈值)


def _mode() -> str:
    m = (os.environ.get("PREMISE_BLEND_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_card(project_root: str | None, cluster_id: str | None) -> dict | None:
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "事件簇.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    clusters = obj.get("clusters") if isinstance(obj, dict) else None
    if not isinstance(clusters, list):
        return None
    if cluster_id:
        for c in clusters:
            if isinstance(c, dict) and c.get("cluster_id") == cluster_id:
                card = c.get("premise_blend_card")
                if isinstance(card, dict):
                    return card
                return None
    # cluster_id 未指定 → 取首个有 card 的
    for c in clusters:
        if isinstance(c, dict):
            card = c.get("premise_blend_card")
            if isinstance(card, dict):
                return card
    return None


def _read_author_band(project_root: str | None) -> dict | None:
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
        if not isinstance(obj, dict):
            continue
        sig = obj.get("vital_relations_compression")
        if not isinstance(sig, dict):
            continue
        mean = sig.get("mean")
        std = sig.get("std")
        if isinstance(mean, (int, float)):
            return {"mean": float(mean),
                    "std": float(std) if isinstance(std, (int, float)) else 0.0,
                    "source": "author_profile"}
    return None


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {"scanner": "premise_blend_card", "schema_version": "1.0",
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

    card = _load_card(project_root, cluster_id)
    band = _read_author_band(project_root)

    flags: list[dict] = []
    out["cjk"] = cjk

    if not card:
        # 卡未填 → 仅一条 advisory·不跑后续 (避免误报)
        flags.append({"code": ISSUE_CODE_MISSING,
                      "msg": "未填 premise_blend_card·outline conceit 登记缺失"})
        out["card_present"] = False
    else:
        out["card_present"] = True
        out["card_summary"] = {
            "blend_type": card.get("blend_type"),
            "input_A_frame": (card.get("input_space_A") or {}).get("frame"),
            "input_B_frame": (card.get("input_space_B") or {}).get("frame"),
            "emergent_count": len(card.get("emergent_structure") or []),
            "vital_relations_compressed": card.get("vital_relations_compressed") or [],
        }
        a_lex = ((card.get("input_space_A") or {}).get("signature_lexemes")) or []
        b_lex = ((card.get("input_space_B") or {}).get("signature_lexemes")) or []
        emergent_struct = card.get("emergent_structure") or []

        # vital_relations 子探针
        probe = vrp.probe(text, a_lex if isinstance(a_lex, list) else None,
                          b_lex if isinstance(b_lex, list) else None)
        out["vital_relations"] = probe
        density = float(probe.get("vital_relations_density") or 0.0)

        # band 计算
        floor = DEFAULT_DENSITY_FLOOR
        ceil = DEFAULT_DENSITY_CEIL
        band_source = "fallback"
        if band:
            mean = band["mean"]
            sd = band.get("std", 0.0)
            floor = max(0.0, mean - 2 * sd)
            ceil = mean + 2 * sd if sd > 0 else mean + DEFAULT_DENSITY_CEIL
            band_source = band["source"]
        out["density_band"] = {"floor": round(floor, 3), "ceil": round(ceil, 3),
                                "source": band_source}

        # emergent_structure≥3 但 density 跌出 floor → emergent meaning 退化
        if len(emergent_struct) >= 3 and density < floor:
            flags.append({"code": ISSUE_CODE_EMERGENCE,
                          "msg": f"emergent_structure {len(emergent_struct)} 条·vital_relations_density={density}<floor={round(floor,3)}·emergent meaning 退化"})

        # input space 单边塌缩
        a_hits = probe.get("signature_lexemes_hits", {}).get("a_total", 0)
        b_hits = probe.get("signature_lexemes_hits", {}).get("b_total", 0)
        if (a_lex and a_hits == 0) or (b_lex and b_hits == 0):
            side = "A" if (a_lex and a_hits == 0) else "B"
            flags.append({"code": ISSUE_CODE_INPUT_DROP,
                          "msg": f"input_space_{side} signature_lexemes 草稿命中=0·blend 单边塌缩"})

        # lex_hits 高 + density 低 → 堆词非真 blend
        ab_hits = a_hits + b_hits
        lex_pkcjk = round(ab_hits / (cjk / 1000.0), 3) if cjk else 0.0
        out["lex_per_kcjk"] = lex_pkcjk
        if lex_pkcjk > DEFAULT_LEX_OVER_PKCJK and density < floor:
            flags.append({"code": ISSUE_CODE_LEXICAL_OVER,
                          "msg": f"signature_lex={lex_pkcjk}/kCJK 高+vital_relations={density}/kCJK 低·堆词非真 blend"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "premise_blend_card", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "CBT Fauconnier&Turner 概念整合·R22 W10 Batch-FF·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] premise_blend_card: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="premise_blend_card 落地度 + vital_relations 探针")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
