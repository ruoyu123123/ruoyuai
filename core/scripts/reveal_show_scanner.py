#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reveal_show_scanner.py — 反转揭底 tell 回查（advisory · cluster）

【缺口】反转「公平性」（揭底前铺垫够不够）核心是语义·纯确定性测不了。但金标准实测
发现可测代理：揭底显式标志词（真相大白/恍然大悟/原来如此）密度·好作者≈0（用情节展示真相·惊悚
乐园最大 0.4 遮天/诡秘全 0）·『揭底靠 tell 说破而非 show 展示』可确定性检测（与 on-the-nose /
dramatic irony tell 同族）。promise_payoff 查承诺-兑现缺口·但揭底 tell 堆砌无检测·本 scanner 补。

【做法 · 确定性可算半边】检测揭底显式标志词密度·高 = 靠 tell 说破真相（初级·读者被告知而非自己
发现）→ advisory「建议改 show」。⚠️ 这是「揭底 tell 密度」代理·**非「公平性」本身**（铺垫够不够
是语义·留 judge/作者）。

【北极星⑤ 顾问非法官】揭底手法是创作选择·writer 有理由偏离 → 永远 advisory，code
  REVEAL_TELL_OVERUSE **绝不进 audit_hub.HARD_GATE_CODES**。env REVEAL_SHOW_MODE active 默认。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "REVEAL_TELL_OVERUSE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 揭底/反转显式标志词（说破真相·好作者用情节展示避免）
REVEAL_TELL = re.compile(
    r"(真相大白|谜底揭晓|恍然大悟|原来如此|这才明白|这才发现|这才知道|"
    r"这才意识到|水落石出|一切都明白了|终于明白|揭开了.{0,6}真相)")
# 🔬 金标准校准（真作者实测：揭底好作者用展示·密度≈0·惊悚乐园最大 0.4 遮天/诡秘全 0）
REVEAL_TELL_FLOOR = 0.8   # 揭底标志词密度超此/千字 = 靠 tell 说破（真作者最大 0.4 留 2x 余量）
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 默认 active（金标准 6 作者原文零误报实证·reveal tell 真作者最大 0.07/千 vs floor 0.8·11x 余量）。
    m = (os.environ.get("REVEAL_SHOW_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_reveal_tell(text: str) -> list:
    """检测揭底显式标志词 hits（说破真相·好作者用展示避免）。"""
    text = _strip_changes(text)
    return [{"marker": m.group(0), "pos": m.start()} for m in REVEAL_TELL.finditer(text)]


def scan(draft_path, project_root=None) -> dict:
    """揭底 tell 密度检测（好作者用 show·密度≈0）。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "reveal_show", "schema_version": "1.0", "mode": mode,
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
    hits = detect_reveal_tell(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["reveal_tell_count"] = len(hits)
    out["per_1k"] = per_1k
    out["sample_markers"] = [h["marker"] for h in hits[:8]]
    if per_1k > REVEAL_TELL_FLOOR:
        msg = (f"揭底用显式标志词 tell 过多（{per_1k}/千字 > {REVEAL_TELL_FLOOR}·真相大白/恍然大悟等）·"
               f"好作者用情节展示真相(真作者≈0)·建议改 show 让读者自己发现")
        if mode == "active":
            out["violations"].append({
                "kind": "reveal_tell_overuse", "severity": "minor",
                "message": msg, "per_1k": per_1k,
                "_doc": "揭底手法是创作选择·advisory·标志词密度是可算半边·铺垫公平性留 judge/作者"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（零回归）
            print(f"[SHADOW] reveal_show: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="W5 反转揭底 tell 回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
