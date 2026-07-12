#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interiority_mode_balance_scanner.py — 内心戏三态失衡检测（advisory · cluster）

【为什么】arXiv:2605.07102 (SAGE) + Cohn 叙事学：人物内心戏有三态——
  ① 心理叙述（psycho-narration·叙述者转述人物心理）
  ② 直接独白（quoted/direct monologue·带「他想/心说」引导标记）
  ③ 自由间接引语（FID·free indirect discourse·叙述贴人物意识流·无引导标记）
弱模型写内心戏【过度依赖带标记的直接独白】（满篇「他想」「心中暗道」），三态坍缩成单态——
意识流的贴近感（FID）和叙述者的距离调度（psycho-narration）都丢了。本 scanner 检测这块。

【做法 · 确定性可算半边】带标记的直接独白有明确中文语言标志（他想/心想/暗道/默念/寻思道…）。
单向检测【标记独白过密】：marked_monologue_per_1k = 标记命中数/(CJK/1000)。超 floor =
「他想式」标记独白过度·三态坍缩成单态 → advisory「建议部分转 FID：叙述贴人物意识流·去引导标记」。
（FID/psycho-narration 无显式标记·机械测不到·只能反向测「标记独白过多」·语义留 judge/作者。）

【北极星⑤ 顾问非法官】内心戏模式是创作选择（某些作者/场景就爱用直接独白）·writer 有理由偏离
  → 永远 advisory，code INTERIORITY_MODE_IMBALANCE **绝不进 audit_hub.HARD_GATE_CODES**。
  env INTERIORITY_MODE_BALANCE_MODE: off / shadow(只记不判) / active(默认·金标准实测零误报后放量)。
  🔬 阈值 MARKED_MONOLOGUE_FLOOR 挂「待金标准校准」注册表追踪（threshold_registry·实测依据见常量注释）。

用法：python interiority_mode_balance_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "INTERIORITY_MODE_IMBALANCE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 带引导标记的「直接独白」标志词（quoted/direct monologue·三态之一·过密=坍缩成单态）
MARKED_MONOLOGUE = re.compile(
    r"(他想|她想|心想|心中想|心里想|心说|心中暗道|暗道|默念|寻思道|想道|暗想|心下)")

# FID 正向半算法（自由间接引语正向检测）：FID 无引导标记·机械难判·只能拣【高确定性
# 语言信号】做正向半算法（命中=可能是 FID 段，但漏报必然·只用作分子）。
# ① 中文语气助词（modal particle）：吗 / 呢 / 啊 / 罢了 / 算了 / 也好 —— 主语缺失裸句末出现常是
#    FID 贴意识（"她真的会来吗。"）
# ② 评判词（evaluative）：分明 / 当真 / 终究 / 偏偏 / 居然 / 竟然 / 怎能 / 何必 / 委实 / 简直 ——
#    叙述段直接出现这些主观评判 = 叙述者贴上人物意识
# ③ 反问 / 感叹叙述句：非对话段（不在引号内）以 ？ / ！ / …… 收尾 + 句首无引导标记 = 多半 FID
# 这三类信号在叙述段(非对话段)出现且无【他想/心说】等引导词紧前 → 计为 FID 候选 hit。
FID_MODAL_PARTICLE = re.compile(r"[一-鿿]{2,}(吗|呢|啊|罢了|算了|也好)[。！？…]")
FID_EVALUATIVE = re.compile(
    r"(分明|当真|终究|偏偏|居然|竟然|怎能|何必|委实|简直|何曾|莫非|或许是|想来是)")
FID_RHETORIC_END = re.compile(r"[一-鿿]{4,}[？！…]{1,3}")

# 🔬 floor=2.5/千字（超出=「他想式」标记独白过度·三态坍缩成单态）。金标准实测 5 真作者
# 标记独白密度 0-0.51/千字（真作者用 FID 少用标记），2.5 留 5x+ 余量·宁可漏报不误报。
MARKED_MONOLOGUE_FLOOR = 2.5   # 标记独白密度超此/千字 = 过度依赖直接独白·建议部分转 FID
# FID 占比下限：当 marked 已过密时·若 fid_share = fid / (fid + marked) < 此·提示"全单态"
FID_SHARE_FLOOR = 0.15

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 金标准校准：5 真作者标记独白密度实测 0-0.51/千字(floor 2.5·5x+ 余量)——真作者用 FID
    # 不用「他想」标记·零误报·故默认可放量 active。
    m = (os.environ.get("INTERIORITY_MODE_BALANCE_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _author_floor(project_root):
    """读作者档 interiority_profile.marked_monologue_per_1k（若有·作者标记独白基线）。无→None。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
        ip = prof.get("interiority_profile") if isinstance(prof, dict) else None
        return (ip or {}).get("marked_monologue_per_1k") if isinstance(ip, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def detect_marked_monologue(text: str) -> list:
    """检测带引导标记的直接独白 hits。返回 [{marker, pos}]。"""
    text = _strip_changes(text)
    return [{"marker": m.group(0), "pos": m.start()} for m in MARKED_MONOLOGUE.finditer(text)]


# FID 正向半算法
_DIALOGUE_PAIRS = (("“", "”"), ("「", "」"))   # U+201C/D 直引号 + 角引号


def _dialogue_spans(text: str) -> list:
    """返回 (start, end) 对话片段·outside 视为叙述段。"""
    spans = []
    for left, right in _DIALOGUE_PAIRS:
        i = 0
        while True:
            l = text.find(left, i)
            if l < 0:
                break
            r = text.find(right, l + 1)
            if r < 0:
                break
            spans.append((l, r + 1))
            i = r + 1
    spans.sort()
    return spans


def _in_dialogue(pos: int, spans: list) -> bool:
    for s, e in spans:
        if s <= pos < e:
            return True
        if s > pos:
            return False
    return False


def detect_fid_signals(text: str) -> dict:
    """FID 正向半算法：modal particle / evaluative / rhetoric_end 三类信号在叙述段计 hit。"""
    text = _strip_changes(text)
    spans = _dialogue_spans(text)

    modal_hits = []
    for m in FID_MODAL_PARTICLE.finditer(text):
        if not _in_dialogue(m.start(), spans):
            modal_hits.append({"term": m.group(0), "pos": m.start()})

    eval_hits = []
    for m in FID_EVALUATIVE.finditer(text):
        if not _in_dialogue(m.start(), spans):
            # 避免和 MARKED_MONOLOGUE 重叠：前 6 字若含引导词跳过
            head = text[max(0, m.start() - 6):m.start()]
            if MARKED_MONOLOGUE.search(head):
                continue
            eval_hits.append({"term": m.group(0), "pos": m.start()})

    rhet_hits = []
    for m in FID_RHETORIC_END.finditer(text):
        if not _in_dialogue(m.start(), spans):
            rhet_hits.append({"term": m.group(0)[-12:], "pos": m.start()})

    total = len(modal_hits) + len(eval_hits) + len(rhet_hits)
    return {
        "modal": len(modal_hits), "evaluative": len(eval_hits),
        "rhetoric": len(rhet_hits), "total": total,
        "modal_samples": [h["term"] for h in modal_hits[:5]],
        "eval_samples": [h["term"] for h in eval_hits[:5]],
        "rhetoric_samples": [h["term"] for h in rhet_hits[:5]],
    }


def scan(draft_path, project_root=None) -> dict:
    """内心戏三态失衡检测·单向报「标记独白过密」。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "interiority_mode_balance", "schema_version": "1.0", "mode": mode,
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

    hits = detect_marked_monologue(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["marked_monologue_count"] = len(hits)
    out["marked_monologue_per_1k"] = per_1k
    out["sample_markers"] = [h["marker"] for h in hits[:8]]

    # FID 正向半算法：补足三态另一半（FID 密度 / 占比）
    fid = detect_fid_signals(draft)
    fid_per_1k = round(fid["total"] / (cjk / 1000.0), 2)
    out["fid_signal_count"] = fid["total"]
    out["fid_per_1k"] = fid_per_1k
    out["fid_breakdown"] = {"modal": fid["modal"], "evaluative": fid["evaluative"], "rhetoric": fid["rhetoric"]}
    total_interiority = len(hits) + fid["total"]
    fid_share = (fid["total"] / total_interiority) if total_interiority > 0 else None
    out["fid_share"] = round(fid_share, 3) if fid_share is not None else None

    # 作者档基线优先（D·作者标记独白密度）·无档则用通用 floor
    author_floor = _author_floor(project_root)
    out["author_marked_monologue_per_1k"] = author_floor
    floor = author_floor if isinstance(author_floor, (int, float)) and author_floor > 0 else MARKED_MONOLOGUE_FLOOR
    out["effective_floor"] = floor

    msg = None
    if per_1k > floor:
        share_note = ""
        if fid_share is not None and fid_share < FID_SHARE_FLOOR:
            share_note = f"·FID 占比仅 {fid_share:.0%} < {FID_SHARE_FLOOR:.0%}(三态确实坍缩)"
        msg = (f"带标记的直接独白过密（{per_1k}/千字 > {floor}·他想/心中暗道等{share_note}）·"
               f"内心戏三态坍缩成单态(满篇「他想式」)·建议部分转 FID：叙述贴人物意识流·去引导标记")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "interiority_mode_imbalance", "severity": "minor",
                "message": msg, "marked_monologue_per_1k": per_1k,
                "effective_floor": floor, "author_floor": author_floor,
                "_doc": "内心戏模式是创作选择·advisory 待裁决·标记独白密度是可算半边·FID/psycho-narration 语义留 judge/作者"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（零回归）
            print(f"[SHADOW] interiority_mode_balance: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="内心戏三态失衡检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者标记独白基线对账")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
