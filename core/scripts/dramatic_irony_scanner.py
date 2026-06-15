#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dramatic_irony_scanner.py — W4 叙事诡计/dramatic irony 信号回查（advisory · cluster · 2026-06-15）

【缺口】记忆调研 W4：narrator_calibrate 只校准叙事距离·不管「诡计型信息控制」·全库 0 个
dramatic irony 检测（Grep 殊不知/浑然不知全空）。D3 蒸馏端产作者 reader_advantage_pct（上帝视角
信息差占比基线），但 writer 写完【零回查】草稿是否体现作者的「信息差风格」（信息差流上帝视角 vs
悬疑流双盲）。本 scanner 补检测端。

【做法 · 确定性可算半边】dramatic irony 有明确中文语言标志（殊不知/浑然不知/却不知道/蒙在鼓里/
并不知道/没意识到）。**🔬金标准实测发现**：这些是【初级 tell】——好作者用情节【展示】信息差，
不用显式标志词说破（惊悚乐园/遮天密度全 0·诡秘最大 0.37）。故单向检测【tell 过多】（与 W1
on-the-nose 同理：标志词高密度 = 靠说破而非展示）→ advisory「建议改 show」。
（原双向对账 reader_adv 逻辑已废——会误判好作者:reader_adv 高但标志词本就 0=正常。）

【北极星⑤ 顾问非法官】信息差风格是创作选择·writer 有理由偏离 → 永远 advisory，code
  DRAMATIC_IRONY_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**。env DRAMATIC_IRONY_MODE shadow 默认。
  🔬 阈值 LOW/HIGH_FLOOR 待金标准校准（真作者原文喂自身）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "DRAMATIC_IRONY_DRIFT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# dramatic irony 标志词（叙述者明示读者·角色不知 = reader_adv 上帝视角信息差）
DRAMATIC_IRONY = re.compile(
    r"(殊不知|浑然不知|浑然不觉|却不知道|并不知道|不知道的是|没意识到|"
    r"未曾察觉|毫无察觉|全然不觉|全然不知|蒙在鼓里|做梦也没想到|"
    r"怎么也想不到|完全没料到|岂知|哪里知道|焉知|不曾想)"
)
# 🔬 金标准校准（2026-06-15·真作者实测）：dramatic irony 好作者用「情节展示」不用「显式标志词 tell」
# —— 惊悚乐园/遮天前 15 章密度全 0·诡秘最大 0.37。「殊不知/浑然不知」是【初级 tell】(说破信息差)，
# 好作者避免。所以本 scanner 单向检测【tell 过多】(像 W1 on-the-nose)·而非原设计的「信号不足」双向
# 对账(会误判好作者:reader_adv 高但标志词本就 0)。
IRONY_TELL_FLOOR = 0.8   # 显式标志词密度超此/千字 = dramatic irony 靠 tell 而非 show（真作者最大0.37留2x余量）
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 2026-06-16 切 active 放量（金标准 6 作者原文零误报实证·irony tell 真作者最大 0.11/千 vs floor 0.8·7x 余量）。
    m = (os.environ.get("DRAMATIC_IRONY_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def detect_dramatic_irony(text: str) -> list:
    """检测 dramatic irony 标志词 hits（叙述者明示读者·角色不知）。"""
    text = _strip_changes(text)
    return [{"marker": m.group(0), "pos": m.start()} for m in DRAMATIC_IRONY.finditer(text)]


def _author_reader_adv(project_root):
    """读作者档 knowledge_gap_profile.reader_advantage_pct（D3·作者上帝视角占比·0-1）。无→None。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
        kg = prof.get("knowledge_gap_profile") if isinstance(prof, dict) else None
        return (kg or {}).get("reader_advantage_pct") if isinstance(kg, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def scan(draft_path, project_root=None) -> dict:
    """dramatic irony 信号 vs 作者 reader_adv 双向对账。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "dramatic_irony", "schema_version": "1.0", "mode": mode,
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
    hits = detect_dramatic_irony(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["dramatic_irony_count"] = len(hits)
    out["per_1k"] = per_1k
    out["sample_markers"] = [h["marker"] for h in hits[:8]]

    # reader_adv 仅作 report 信息参考·不作判据（金标准证好作者标志词密度≈0·与 reader_adv 无关）
    reader_adv = _author_reader_adv(project_root)
    out["author_reader_advantage_pct"] = reader_adv
    # 单向检测：dramatic irony 显式标志词 tell 过多（好作者用 show 不用 tell·像 W1 on-the-nose）
    msg = None
    if per_1k > IRONY_TELL_FLOOR:
        msg = (f"dramatic irony 用显式标志词 tell 过多（{per_1k}/千字 > {IRONY_TELL_FLOOR}·"
               f"殊不知/浑然不知等）·好作者用情节展示信息差(真作者密度≈0)·建议改 show")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "dramatic_irony_drift", "severity": "minor",
                "message": msg, "per_1k": per_1k, "author_reader_adv": reader_adv,
                "_doc": "信息差风格是创作选择·advisory 待裁决·标志词密度是可算半边·语义留 judge/作者"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（零回归）
            print(f"[SHADOW] dramatic_irony: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="W4 dramatic irony 信号回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者 reader_adv 基线对账")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
