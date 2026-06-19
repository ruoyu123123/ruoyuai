#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""physio_cue_diversity_scanner.py — 生理情绪线索面部偏置回查（advisory · cluster · 2026-06-19）

【缺口】LLM 写情绪生理线索时存在系统性「面部偏置」（facial bias）：愤怒=皱眉、紧张=眼神
闪躲、震惊=瞪大眼睛……反复堆在眉/眼/嘴角/脸色等面部区域，冷落了手/呼吸/肠胃/姿态/后背等
非面部躯体信号。来源 arXiv:2509.19595（ELENA）实证 LLM 情绪表达的 facial-region 集中度远高于
人类作者。全库无任何 scanner 回查这个躯体表达多样性维度 → 本 scanner 补检测端的【可算半边】。

【做法 · 确定性可算半边】（北极星守卫：情绪躯体化的质量是语义判断·只做可算的区域占比，裁决留
judge/作者）：分别正则匹配【面部生理线索】与【非面部生理线索】两类词命中数，度量
  facial_ratio = facial命中 / (facial命中 + nonfacial命中)。
样本太少不可靠 → 需 (facial+nonfacial) >= 8 才判（样本足）。单向检测【面部占比过高】：
facial_ratio > 阈值 → advisory「建议多用手/呼吸/肠胃/姿态等非面部信号」。低占比不报（多样化是好事）。

【北极星⑤ 顾问非法官】情绪躯体化是创作选择·writer 有理由偏面部（特写镜头/POV 贴脸） → 永远
  advisory，code PHYSIO_CUE_FACIAL_BIAS **绝不进 audit_hub.HARD_GATE_CODES**。
  env PHYSIO_CUE_DIVERSITY_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值 FACIAL_RATIO_FLOOR 为保守占位·待金标准校准（真作者原文喂自身 PASS·防矫枉过正）。

用法：python physio_cue_diversity_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PHYSIO_CUE_FACIAL_BIAS"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 🔬 待金标准校准（真作者原文喂自身）：占位阈值保守（宁可漏报不误报）。
# 面部生理线索占比超此 = facial bias（面部区域堆砌·缺非面部躯体信号）。
FACIAL_RATIO_FLOOR = 0.55
MIN_CUE_SAMPLES = 8   # facial+nonfacial 命中总数低于此 = 样本不足·不判（防小样本噪声）

# 面部生理线索（眉/眼/嘴/脸/额头等面部区域）
FACIAL_CUE = re.compile(
    r"(眉头|眉梢|眉|眼神|眼眶|眼|瞳|嘴角|脸色|面色|脸颊|唇|额头)"
)
# 非面部生理线索（手/呼吸/喉/胸口/肠胃/后背/肩/姿态/脚等躯体信号）
NONFACIAL_CUE = re.compile(
    r"(指节|指|拳|手心|手|呼吸|喉咙|喉|嗓子|胸口|心跳|肠胃|胃|后背|脊背|肩|站姿|姿态|体温|膝|脚)"
)
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("PHYSIO_CUE_DIVERSITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_physio_cues(text: str) -> dict:
    """统计面部 vs 非面部生理线索命中数。返回 {facial, nonfacial, facial_samples, nonfacial_samples}。"""
    text = _strip_changes(text)
    facial = [m.group(0) for m in FACIAL_CUE.finditer(text)]
    nonfacial = [m.group(0) for m in NONFACIAL_CUE.finditer(text)]
    return {
        "facial": len(facial),
        "nonfacial": len(nonfacial),
        "facial_samples": facial[:8],
        "nonfacial_samples": nonfacial[:8],
    }


def _author_facial_ratio(project_root):
    """读作者档 physio_cue_profile.facial_ratio（若蒸馏端日后产此基线·当前无→None）。无档→None。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
        pc = prof.get("physio_cue_profile") if isinstance(prof, dict) else None
        return (pc or {}).get("facial_ratio") if isinstance(pc, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def scan(draft_path, project_root=None) -> dict:
    """生理情绪线索面部偏置回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "physio_cue_diversity",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 北极星⑤ · 绝不 hard_gate
        "warning": None,
        "violations": [],           # 对齐 audit_hub._parse_violations_scanner（drift→1条·shadow 空）
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
        out["note"] = "草稿太短·跳过"
        return out

    cues = detect_physio_cues(draft)
    facial = cues["facial"]
    nonfacial = cues["nonfacial"]
    total = facial + nonfacial
    out["facial_count"] = facial
    out["nonfacial_count"] = nonfacial
    out["cue_total"] = total
    out["facial_samples"] = cues["facial_samples"]
    out["nonfacial_samples"] = cues["nonfacial_samples"]

    # 🔬 作者基线（physio_cue_profile.facial_ratio·若日后蒸馏端产）·当前仅 report 参考·不作判据
    author_ratio = _author_facial_ratio(project_root)
    out["author_facial_ratio"] = author_ratio

    if total < MIN_CUE_SAMPLES:
        out["facial_ratio"] = None
        out["note"] = f"生理线索样本不足（{total} < {MIN_CUE_SAMPLES}）·不判"
        out["violations_count"] = len(out["violations"])
        return out

    facial_ratio = round(facial / total, 3)
    out["facial_ratio"] = facial_ratio

    msg = None
    if facial_ratio > FACIAL_RATIO_FLOOR:
        msg = (f"生理情绪线索面部偏置（facial_ratio {facial_ratio} > {FACIAL_RATIO_FLOOR}·"
               f"面部 {facial}/非面部 {nonfacial}）·建议多用手/呼吸/肠胃/姿态等非面部信号")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "physio_cue_facial_bias", "severity": "minor",
                "message": msg, "facial_ratio": facial_ratio,
                "facial_count": facial, "nonfacial_count": nonfacial,
                "_doc": "情绪躯体化是创作选择·面部特写有时合理(贴脸 POV/特写镜头)→advisory 待裁决·"
                        "区域占比是可算半边粗糙哨兵·真躯体化质量留 judge/作者",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] physio_cue_diversity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="生理情绪线索面部偏置回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者 physio_cue 基线对账(若日后蒸馏端产)")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·active 有 warning 才 exit 1·否则 exit 0(不阻断·北极星⑤)
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
