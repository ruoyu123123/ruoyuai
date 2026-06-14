#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D5 角色动作动机锚点 scanner（cluster 级·advisory·默认 shadow·2026-06-14）。

检测 cluster draft 角色动作有无动机锚点：角色动作密集的段落，本段或邻近上文有无动机
交代（因为/为了/想/打算/决定/不得不…）。动作密集但全程无动机交代 → 疑似配角降智
（无动机当傻子衬主角）→ emit INTENT_ANCHOR_MISSING（advisory）。有动机交代 → 不 emit。

北极星⑤边界：INTENT_ANCHOR_MISSING 是【防降智·读者体验】advisory——配角应有算计有城府
（memory feedback-smart-side-characters），但作者可能有意（信息差喜剧/留白）→ advisory +
默认 shadow·FP 高永久 shadow 不挂 scanner 集合·绝不进 audit_hub.HARD_GATE_CODES。
env INTENT_SCAN_MODE 默认 shadow（过误报闸校准后才挂集合·active 才进 issues）。
确定性关键词/GMC 信号匹配·无 LLM·exit0 不抛错。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

INTENT_ANCHOR_MISSING = "INTENT_ANCHOR_MISSING"

# 动机锚点信号（复用 narrative_scanner.GMC_MOTIVATION_KEYWORDS 的『因为/为了/为/由于』+
# GMC_GOAL_KEYWORDS 的意图/打算类信号·扩展中文动机长尾·确定性·非 LLM）。
# R3 已警：GMC『因为/为了』太窄(FP/FN 都高)·故扩信号面 + 强制 shadow·过误报闸才上。
_MOTIVATION_CUES = re.compile(
    r"(因为|为了|为的是|由于|既然|想要|想着|打算|决定|不得不|只好|只能|为此|"
    r"是想|是为|目的|意图|动机|盘算|图的|惦记|生怕|担心|害怕|急于|想着要|"
    r"得先|必须|总算想|这才|于是|以便|好让|免得|省得)"
)

# 角色动作信号（具体外显行为动词·疑似『动作流水账』的载体）。
_ACTION_CUES = re.compile(
    r"(转身|抬手|抬头|低头|伸手|握住|攥|抓|拽|推开|拉开|甩|挥|抡|踢|踹|跨|"
    r"扑|冲|奔|窜|跳|蹲|跪|趴|爬|站起|坐下|靠近|逼近|后退|退开|绕|闪身|"
    r"举起|扔|掷|砸|劈|斩|刺|捅|拔|掏|摸出|抽出|拎|提起|放下|扣|掀|撕|"
    r"夺|按住|压住|拦住|挡|捂|捏|掐|拍|敲|踩|蹬|纵身|翻身|侧身|俯身)"
)

# 对话信号（对话密集段不是动作流水账·避免误伤台词戏）。
_DIALOGUE = re.compile(r'["“「]([^"”」\n]+)["”」]')


def _cjk(s: str) -> int:
    return sum(1 for c in s if "一" <= c <= "鿿")


def _paras(text: str) -> list[str]:
    return [p for p in re.split(r"\n\n+", text) if p.strip() and _cjk(p) >= 2]


def _dialogue_ratio(p: str) -> float:
    total = _cjk(p)
    if total == 0:
        return 0.0
    dia = sum(_cjk(m.group(1)) for m in _DIALOGUE.finditer(p))
    return dia / total


def scan_intent_consistency(text: str, action_floor: int = 3,
                            window: int = 1) -> dict:
    """检测角色动作动机锚点。确定性·无 LLM。

    启发：滑窗（当前段 + 前 `window` 段）做动机锚点上下文。某段动作信号 ≥ action_floor
    且窗内无任何动机锚点 + 非对话主导段 → INTENT_ANCHOR_MISSING（疑似降智·动作流水账）。

    返回 {issues, paras_scanned, action_dense_paras, anchored, missing}。
    """
    paras = _paras(text)
    if len(paras) < 2:
        return {"issues": [], "paras_scanned": len(paras),
                "action_dense_paras": 0, "anchored": 0, "missing": 0,
                "note": "段落不足"}
    # 预算每段动作信号计数 + 动机锚点命中。
    action_counts = [len(_ACTION_CUES.findall(p)) for p in paras]
    has_motiv = [bool(_MOTIVATION_CUES.search(p)) for p in paras]
    dia_ratio = [_dialogue_ratio(p) for p in paras]

    issues = []
    action_dense = 0
    anchored = 0
    missing = 0
    for i, p in enumerate(paras):
        if action_counts[i] < action_floor:
            continue
        # 对话主导段（台词戏·动机藏在对白）跳过·防误伤。
        if dia_ratio[i] >= 0.4:
            continue
        action_dense += 1
        # 滑窗动机锚点：本段或前 window 段任一有动机交代 → 已锚定。
        lo = max(0, i - window)
        windowed_anchor = any(has_motiv[j] for j in range(lo, i + 1))
        if windowed_anchor:
            anchored += 1
            continue
        missing += 1
        issues.append({
            "code": INTENT_ANCHOR_MISSING,
            "severity": "warning",
            "gate_level": "advisory",
            "paragraph_idx": i,
            "action_signals": action_counts[i],
            "preview": p[:40],
            "msg": f"第 {i} 段动作密集（{action_counts[i]} 处动作信号）但本段及前 "
                   f"{window} 段无动机交代（疑似配角降智/动作流水账·"
                   "配角应有算计·读者体验 advisory·作者有意留白/信息差喜剧可豁免）",
        })
    return {
        "issues": issues,
        "paras_scanned": len(paras),
        "action_dense_paras": action_dense,
        "anchored": anchored,
        "missing": missing,
    }


def main():
    ap = argparse.ArgumentParser(
        description="D5 角色动作动机锚点 scanner（advisory·默认 shadow）")
    ap.add_argument("draft", help="cluster draft 文本文件路径")
    ap.add_argument("--action-floor", type=int, default=3,
                    help="单段动作信号数达此值才视为动作密集（默认 3）")
    ap.add_argument("--window", type=int, default=1,
                    help="动机锚点滑窗回看段数（默认 1）")
    args = ap.parse_args()
    mode = (os.environ.get("INTENT_SCAN_MODE") or "shadow").strip().lower()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 防 Windows GBK 崩 exit
    except Exception:
        pass
    p = Path(args.draft)
    if not p.exists():
        print(json.dumps({"issues": [], "_skip": "draft 不存在"}, ensure_ascii=False))
        sys.exit(0)
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        print(json.dumps({"issues": [], "_skip": f"读取失败:{e}"}, ensure_ascii=False))
        sys.exit(0)
    result = scan_intent_consistency(text, action_floor=args.action_floor,
                                     window=args.window)
    if mode != "active":   # shadow（默认）：算但不上报（过误报闸校准后才 active）
        result["_shadow"] = True
        result["issues"] = []
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
