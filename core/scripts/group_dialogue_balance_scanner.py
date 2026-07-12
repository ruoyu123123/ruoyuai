#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""group_dialogue_balance_scanner.py — 群戏对话失衡/显式点名过密回查（advisory · cluster）

【为什么】联网调研（arXiv:2603.04969 MPCEval 多方对话评测）：群戏（≥3 人同场）里弱模型常靠
「张三说/李四道/老钟问」**显式专名归属**逐句点名来维持「谁在说话」，而成熟作者用**语境隐式指称**
（动作/称谓/上下文）+「主导者发声·其他人反应」的不对称结构。本 scanner 检测显式点名是否过密
（= 点名拐杖 = 群戏调度生硬），补检测端的可算半边。

【做法 · 确定性可算半边】（北极星守卫：群戏调度质量是语义判断·只做可算的密度·裁决留 judge/作者）：
  对话行 = 含中文引号（U+201C/U+201D 或 「」）的段。
  显式点名归属 = 行内出现 [2-4 字中文专名/称谓] 紧跟（说道|说|道|问道|问|喊道|喊|叫道|笑道|
    冷笑道|开口）—— 用 [一-龥]{2,4}(...) 近似（会把「他知道/我觉得」类误纳·故阈值保守留余量）。
  仅当对话行数 ≥ MIN_DIALOGUE_LINES（群戏规模门槛）才判（避免双人小对话误伤）。
  explicit_name_attrib_ratio = 带显式点名的对话行数 / 对话行总数。超 RATIO_FLOOR → 单向报「偏高」。

【北极星⑤ 顾问非法官】群戏点名 vs 隐式指称是创作选择·writer 有理由可偏离（人物多/初登场需点名）→
  永远 advisory，code GROUP_DIALOGUE_IMBALANCE **绝不进 audit_hub.HARD_GATE_CODES**。
  env GROUP_DIALOGUE_BALANCE_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值 RATIO_FLOOR / MIN_DIALOGUE_LINES 挂「待金标准校准」注册表追踪（threshold_registry·RATIO_FLOOR 实测依据见常量注释）。

用法：python group_dialogue_balance_scanner.py <draft_path> [--manifest m.json] [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "GROUP_DIALOGUE_IMBALANCE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 群戏规模门槛 + 显式点名占比地板（保守取值·宁可漏报不误报）
MIN_DIALOGUE_LINES = 8     # 对话行 < 此 = 非群戏规模·不判（避免双人小对话误伤）
RATIO_FLOOR = 0.85          # 金标准校准:5真作者实测0.165-0.765(剑来0.765/将夜0.72·中文网文点名本就高频常态)·
                            # 阈值0.85只catch极端机械点名·保持shadow(信号弱·点名非缺陷)

# 含中文引号 = 对话行（U+201C/U+201D 弯引号 或 「」直角引号）
DIALOGUE_QUOTE = re.compile(r"[“”「」]")
# 显式点名归属：[2-4 字中文专名/称谓] 紧跟 说类动词（近似·会有 false positive·靠阈值余量兜底）
ATTRIB_WITH_NAME = re.compile(
    r"[一-龥]{2,4}(说道|说|道|问道|问|喊道|喊|叫道|笑道|冷笑道|开口)"
)
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("GROUP_DIALOGUE_BALANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _author_ratio_floor(project_root):
    """读作者档 group_dialogue_profile.explicit_name_attrib_ratio（作者群戏点名基线）。无→None（用通用 floor）。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
        gp = prof.get("group_dialogue_profile") if isinstance(prof, dict) else None
        return (gp or {}).get("explicit_name_attrib_ratio") if isinstance(gp, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def detect_group_dialogue_balance(text: str) -> dict:
    """统计对话行 + 显式点名归属行。返回 {dialogue_lines, name_attrib_lines, ratio, sample}。

    带名归属按【行】计（一行算 1 次显式点名·防同行多匹配虚高），ratio ∈ [0,1]。
    """
    text = _strip_changes(text)
    dialogue_lines = 0
    name_attrib_lines = 0
    sample = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or not DIALOGUE_QUOTE.search(line):
            continue
        dialogue_lines += 1
        if ATTRIB_WITH_NAME.search(line):
            name_attrib_lines += 1
            if len(sample) < 6:
                sample.append(line[:40])
    ratio = round(name_attrib_lines / dialogue_lines, 3) if dialogue_lines else 0.0
    return {
        "dialogue_lines": dialogue_lines,
        "name_attrib_lines": name_attrib_lines,
        "explicit_name_attrib_ratio": ratio,
        "sample": sample,
    }


def scan(draft_path, project_root=None) -> dict:
    """群戏显式点名密度回查。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {
        "scanner": "group_dialogue_balance",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 北极星⑤ · 绝不 hard_gate
        "warning": None,
        "violations": [],           # 对齐 audit_hub._parse_violations_scanner（命中→1条·shadow 空）
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
        out["note"] = "草稿太短（<500 CJK）·群戏点名密度不可估·跳过"
        return out
    metrics = detect_group_dialogue_balance(draft)
    out["dialogue_lines"] = metrics["dialogue_lines"]
    out["name_attrib_lines"] = metrics["name_attrib_lines"]
    out["explicit_name_attrib_ratio"] = metrics["explicit_name_attrib_ratio"]
    out["sample_attrib_lines"] = metrics["sample"]

    # 非群戏规模（对话行不足）→ 不判
    if metrics["dialogue_lines"] < MIN_DIALOGUE_LINES:
        out["note"] = f"对话行 {metrics['dialogue_lines']} < {MIN_DIALOGUE_LINES}（非群戏规模）·跳过群戏判定"
        out["violations_count"] = 0
        return out

    # 作者档基线第一权威·无档则通用 floor
    author_floor = _author_ratio_floor(project_root)
    floor = author_floor if isinstance(author_floor, (int, float)) else RATIO_FLOOR
    out["ratio_floor_used"] = floor
    out["author_ratio_floor"] = author_floor

    ratio = metrics["explicit_name_attrib_ratio"]
    msg = None
    if ratio > floor:
        msg = (f"群戏显式点名过密（带名归属占比 {ratio} > {floor}·"
               f"{metrics['name_attrib_lines']}/{metrics['dialogue_lines']} 行靠『专名+说/道』点名）·"
               f"建议靠语境隐式指称 + 主导者/反应者结构降低点名拐杖")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "group_dialogue_imbalance", "severity": "minor",
                "message": msg, "ratio": ratio,
                "name_attrib_lines": metrics["name_attrib_lines"],
                "dialogue_lines": metrics["dialogue_lines"],
                "_doc": "群戏调度是创作判断·人物多/初登场需点名→advisory 待裁决·点名密度是可算半边粗糙哨兵·"
                        "真群戏调度质量留 judge/作者",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] group_dialogue_balance: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="群戏对话失衡/显式点名过密回查(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者群戏点名基线对账")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # advisory scanner·active 有 warning 才 exit 1（不阻断·北极星⑤）
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
