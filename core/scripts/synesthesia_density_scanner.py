#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""synesthesia_density_scanner.py — 通感/移觉密度检测（advisory · cluster · 2026-06-20）

【缺口】全系统 scanner 无一查「通感/移觉（synesthesia）过用」。来源 arXiv:2110.09710
（Inter-Sense·跨感官语义迁移）：通感是修辞【高光】非常态——偶现一笔（"响亮的红"）惊艳，
密集堆砌则【钝化效果】比不用更糟（每句都跨感官借用 = 辞藻油腻·失去高光的稀缺性）。

【做法 · 确定性可算半边】（北极星守卫：通感质量是语义判断·只做可算的「明显跨感官搭配」密度）：
  用【保守的高确定性通感模板词典】只匹配【明显】通感短语——"视觉/听觉形容词 + 异感官名词"或
  反之的典型模式（响亮的光 / 冰冷的声音 / 甜的风 / 声音很湿…）。宁可漏报（隐性通感/合法搭配
  不入典）·裁决留作者。单向只报【过用】（密度过高）·不报「不用」（不用通感是正常的）。
  syn_count=命中数·per_1k=syn_count/(CJK/1000)·per_1k>阈值 → advisory「通感密度过高」。

【北极星⑤ 顾问非法官】通感密度是创作选择·writer 有理由偏离 → 永远 advisory，code
  SYNESTHESIA_OVERUSE **绝不进 audit_hub.HARD_GATE_CODES**。
  env SYNESTHESIA_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值 SYN_PER_1K_FLOOR 保守占位·待金标准校准（真作者原文喂自身 PASS·真作者通感密度极低·留大余量）。

用法：python synesthesia_density_scanner.py <draft_path> [--project <root>] [--manifest m.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "SYNESTHESIA_OVERUSE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 明显跨感官借用模板（保守·高确定性·只匹配典型通感短语·宁可漏报不误报·北极星⑤）
SYNESTHESIA_PAT = re.compile(
    r"(响亮|刺耳|尖锐|嘈杂|安静)的(光|颜色|红|白|气味|味道)"
    r"|(冰冷|温热|滚烫|柔软|粗糙|尖锐)的(声音|光线|气味|味道|嗓音)"
    r"|(明亮|暗淡|鲜艳|苍白)的(声音|气味|味道)"
    r"|(甜|苦|酸|咸)的(声音|气味|空气|风)"
    r"|声音(很|是)(湿|冷|甜|软|硬|亮)"
    r"|气味(很)?(吵|响|刺眼|明亮)"
)
# 🔬 金标准校准占位（真作者通感密度极低·通感是修辞高光非常态）：密度超此/千字 = 过用·留大余量
SYN_PER_1K_FLOOR = 1.5   # 通感密度过高门槛（建议每 cluster 约 1-3 处·待真作者原文喂自身校准）

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 2026-06-20 金标准校准放量 active：5 真作者 synesthesia per_1k 全 0.0(保守正则只抓明显通感)
    # —— 真作者不滥用通感·零误报·只在草稿过用(>1.5/千)时报·安全放量。
    m = (os.environ.get("SYNESTHESIA_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_synesthesia(text: str) -> list:
    """检测明显跨感官通感短语 hits。返回 [{"phrase","pos"}]。"""
    text = _strip_changes(text)
    return [{"phrase": m.group(0), "pos": m.start()} for m in SYNESTHESIA_PAT.finditer(text)]


def scan(draft_path, project_root=None) -> dict:
    """通感/移觉密度检测·单向只报过用。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "synesthesia_density", "schema_version": "1.0", "mode": mode,
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    hits = detect_synesthesia(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["syn_count"] = len(hits)
    out["per_1k"] = per_1k
    out["sample_phrases"] = [h["phrase"] for h in hits[:8]]

    msg = None
    if per_1k > SYN_PER_1K_FLOOR:
        msg = (f"通感密度过高（{per_1k}/千字 > {SYN_PER_1K_FLOOR}·响亮的光/冰冷的声音等）·"
               f"通感是修辞高光非常态·过用钝化效果·建议每 cluster 约 1-3 处")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "synesthesia_overuse", "severity": "minor",
                "message": msg, "per_1k": per_1k, "syn_count": len(hits),
                "_doc": "通感密度是创作选择·advisory 待裁决·跨感官搭配密度是可算半边·"
                        "高光稀缺性语义留 judge/作者（arXiv:2110.09710 Inter-Sense）"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] synesthesia_density: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="通感/移觉密度检测(advisory·单向只报过用)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
