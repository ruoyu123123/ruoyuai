#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""biber_mda_scanner.py — Biber Multi-Dimensional Analysis 4 维中文映射 advisory · cluster · 2026-06-21 R20 W9 Batch-AA · P1

【缺口 · R20 fanfic_voice id 4】Douglas Biber 1988《Variation Across Speech and
Writing》经典 MDA 框架——任何文本可投射到「涉入度/叙事关切/语境指称/说服度」
等多维风格指纹。中文映射(Xiao 2009 等)给出可计数的标记词集。LLM 默认在
某几维上向语料中位回归(说服度漂高、涉入度漂低)，本 scanner 把作者档基线
当第一权威，每 cluster 算 4 维 z-score 报漂移。

【四维 · 确定性中文映射】
  D1 涉入度(involvement)：
      1/2 人称代词(我/我们/你/你们/咱) + 私人动词(觉得/想/认为/记得/明白)
      + 情态副词(也许/大概/可能/恐怕/估计)
  D2 叙事关切(narrative concern)：
      过去时标记(了/过) + 3 人称代词(他/她/它/他们) + 行动动词
      (走/跑/抓/喊/打/推/拉)
  D3 语境指称(situated reference)：
      时间副词(此刻/那时/此前/此后/方才/旋即) + 地点副词(此处/那里/远方
      /近旁) + 指代(这/那)
  D4 说服度(persuasion / overt expression of argumentation)：
      情态动词(应当/必须/应该/可能/会) + 强调词(确实/的确/分明/绝对/必然)
      + 推论词(因此/所以/由此/故/则)

【做法】
  · 每维 = 该维所有 marker hits / kCJK · 与作者档 biber_mda_baseline.{D1..D4}
    {mean, std} 算 z-score。
  · |z| > 1.0 → BIBER_MDA_DRIFT_Dn 报维度漂移(advisory · shadow)。
  · 无作者档 → 兜底中性 band(各维度 mean=fallback 估计)·只在极端偏离时报。

【与既有 scanner 严格正交】
  · function_word_fingerprint 查虚词总指纹 · 不分 4 维
  · syntactic_diversity 查 POS n-gram · 不查标记词分布
  · indirect_characterization 查侧写 ratio · 不查涉入度
  本 scanner = MDA 4 维 唯一覆盖。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  BIBER_MDA_DRIFT_Dn 绝不进 audit_hub.HARD_GATE_CODES。

env BIBER_MDA_MODE: off / shadow(默认) / active
用法: python biber_mda_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "BIBER_MDA_DRIFT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 4 维标记词(占位中文映射 · 真 1500 词需 Xiao 2009 词表授权 defer · _placeholder=true)
D1_MARKERS = {
    "pronouns_1_2": ["我", "我们", "你", "你们", "咱", "咱们"],
    "private_verbs": ["觉得", "想", "认为", "记得", "明白", "知道", "感到", "以为", "怀疑"],
    "modal_adverbs": ["也许", "大概", "可能", "恐怕", "估计", "或许", "兴许"],
}
D2_MARKERS = {
    "past_aspect": ["了", "过"],
    "pronouns_3": ["他", "她", "它", "他们", "她们", "它们"],
    "action_verbs": ["走", "跑", "抓", "喊", "打", "推", "拉", "撞", "扔", "踢"],
}
D3_MARKERS = {
    "time_adverbs": ["此刻", "那时", "此前", "此后", "方才", "旋即", "随后", "随即", "片刻"],
    "place_adverbs": ["此处", "那里", "远方", "近旁", "门外", "屋内", "脚下", "前方"],
    "demonstratives": ["这", "那"],
}
D4_MARKERS = {
    "modals": ["应当", "必须", "应该", "可能", "会", "能", "得"],
    "emphatics": ["确实", "的确", "分明", "绝对", "必然", "实在"],
    "inferences": ["因此", "所以", "由此", "故", "则", "于是"],
}

# 兜底基线(无作者档 · 中性估计 · per kcjk)
DEFAULT_BASELINE = {
    "D1": {"mean": 35.0, "std": 12.0},
    "D2": {"mean": 60.0, "std": 18.0},
    "D3": {"mean": 18.0, "std": 8.0},
    "D4": {"mean": 12.0, "std": 6.0},
}

DRIFT_Z_THRESHOLD = 1.0


def _mode() -> str:
    m = (os.environ.get("BIBER_MDA_MODE") or "shadow").strip().lower()
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
            mb = obj.get("biber_mda_baseline")
            if isinstance(mb, dict):
                return mb
    return None


def _count_terms(text: str, terms: list[str]) -> int:
    n = 0
    for t in terms:
        if not t:
            continue
        n += text.count(t)
    return n


def _dim_density(text: str, markers: dict, cjk: int) -> tuple[float, dict]:
    """返回 (per_kcjk_total, breakdown)。"""
    breakdown = {}
    total = 0
    for k, terms in markers.items():
        c = _count_terms(text, terms)
        breakdown[k] = c
        total += c
    per_kcjk = round(total / (cjk / 1000.0), 3) if cjk else 0.0
    return per_kcjk, breakdown


def _z(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return round((value - mean) / std, 3)


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "biber_mda", "schema_version": "1.0",
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

    d1, b1 = _dim_density(text, D1_MARKERS, cjk)
    d2, b2 = _dim_density(text, D2_MARKERS, cjk)
    d3, b3 = _dim_density(text, D3_MARKERS, cjk)
    d4, b4 = _dim_density(text, D4_MARKERS, cjk)
    dims_per_kcjk = {"D1": d1, "D2": d2, "D3": d3, "D4": d4}
    breakdown = {"D1": b1, "D2": b2, "D3": b3, "D4": b4}

    baseline = _read_author_baseline(project_root)
    baseline_source = "fallback"
    use_baseline = dict(DEFAULT_BASELINE)
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        for dim in ("D1", "D2", "D3", "D4"):
            entry = baseline.get(dim)
            if isinstance(entry, dict) and isinstance(entry.get("mean"), (int, float)):
                use_baseline[dim] = {
                    "mean": float(entry["mean"]),
                    "std": float(entry.get("std") or DEFAULT_BASELINE[dim]["std"]),
                }

    z_scores = {dim: _z(dims_per_kcjk[dim], use_baseline[dim]["mean"], use_baseline[dim]["std"])
                for dim in ("D1", "D2", "D3", "D4")}

    out.update({
        "cjk": cjk,
        "dims_per_kcjk": dims_per_kcjk,
        "z_scores": z_scores,
        "breakdown": breakdown,
        "baseline_source": baseline_source,
        "baseline": use_baseline,
    })

    flags = []
    for dim, z in z_scores.items():
        if abs(z) > DRIFT_Z_THRESHOLD:
            direction = "偏高" if z > 0 else "偏低"
            flags.append({
                "code": f"{ISSUE_CODE}_{dim}",
                "msg": (f"{dim} 维 z={z}{direction}(per_kcjk={dims_per_kcjk[dim]} "
                        f"vs author mean={use_baseline[dim]['mean']}±{use_baseline[dim]['std']})")
            })

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "biber_mda", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Biber 1988 MDA + Xiao 2009 中文映射 · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] biber_mda: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Biber MDA 4-dim 中文映射 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
