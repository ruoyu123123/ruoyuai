#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""temporal_grounding_scanner.py — 时间流逝感缺失检测（advisory · cluster · 2026-06-20）

【缺口】R1 真编辑实证 AI 破绽：文笔漂亮但「没时间概念」—— 长草稿跨多场景却缺时间推进锚点 =
时间扁平（temporal flatness）。全系统 scanner 查空间接地（scene_grounding 白房间）、查节奏、查
情绪，**无一查「时间流逝感」**。弱模型爱把多场景写成「悬浮在同一抽象时刻」，读者感觉不到时辰
推移 / 日子过去 / 等待煎熬。本 scanner 补这一维度的可算半边。

【做法 · 确定性可算半边】（北极星守卫：时间感质量是语义判断·只做可算的「时间锚点缺失」密度）：
  1. 把 cluster 草稿按空行分段聚成场景（参考 scene_grounding 的场景切分：空行间隔 >= 2 +
     时空转换词 + 分隔符之后首段）。
  2. 统计每个场景是否含任一【时间推进锚点 TIME_ANCHOR】：时段（清晨/傍晚/深夜…）/相对时间
     （翌日/三天后/片刻后/许久…）/钟点（时辰/更天/点钟）/时间流逝动词（等了/过了/挨到/捱过）。
  3. time_grounded_scenes = 含至少 1 个 TIME_ANCHOR 的场景。
     thin_ratio = (total - grounded) / total。total_scenes >= 3（够长才判时间感）且
     thin_ratio > 阈值 → advisory「多数场景缺时间推进锚点·时间流逝感弱」。
  ⚠️ 只能测「显式时间词的缺失」·靠情节隐性推移的时间感测不到 → 故意宽锚点（宁可漏报）·裁决留作者。

【北极星⑤ 顾问非法官】时间标记密度是创作选择（高速连续场/单一时刻定格可能故意不标）·writer 有
  理由偏离 → 永远 advisory，code TEMPORAL_GROUNDING_THIN **绝不进 audit_hub.HARD_GATE_CODES**。
  env TEMPORAL_GROUNDING_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值 THIN_RATIO_FLOOR/MIN_SCENES 保守占位（宁可漏报不误报）·待金标准校准（真作者原文喂自身 PASS）。

用法：python temporal_grounding_scanner.py <draft_path> [--project <root>] [--manifest m.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "TEMPORAL_GROUNDING_THIN"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 时间推进锚点（时段 / 相对时间 / 钟点 / 时间流逝动词）—— 故意宽（宁可漏报不误报·北极星⑤）
TIME_ANCHOR = re.compile(
    r"清晨|早晨|晌午|正午|午后|傍晚|黄昏|入夜|深夜|半夜|拂晓|破晓|天亮|天黑"   # 时段
    r"|翌日|次日|隔日|三天后|片刻后|半晌|须臾|不多时|过了一会|良久|许久"        # 相对时间
    r"|转眼|不知过了多久|入秋|开春"
    r"|时辰|更天|点钟"                                                       # 钟点
    r"|等了|过了|挨到|捱过"                                                  # 时间流逝动词
)
# 时空转换标志词（在段首 = 新场景开头）
TRANSITION = re.compile(
    r"^[\s　]*(翌日|翌晨|次日|第二天|第三天|三天后|两天后|几天后|半个月后|"
    r"一个月后|多年后|多日后|与此同时|同一时刻|同一时间|另一边|另一头|"
    r"回到|此时|当晚|当夜|入夜|傍晚|清晨|许久之后|片刻之后)")
# 分隔符行（纯分隔·其后首段是新场景·分隔符自身不算场景）
SEPARATOR = re.compile(r"^[\s　\*＊·.。…\-—－◇◆○●※☆★]{1,20}$")

THIN_RATIO_FLOOR = 0.7   # 🔬 待金标准校准（真作者原文喂自身 PASS）：缺时间锚点场景占比 > 此 = 时间流逝感弱
MIN_SCENES = 3           # 总场景 < 此 不报（够长才判时间感·conservative）
MIN_CJK = 500            # 草稿 < 此 跳过

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 2026-06-20 金标准校准放量 active：5 真作者(诡秘/主神/惊悚/剑来/将夜)thin_ratio 全 0.0
    # —— 真作者场景总带时间锚点·零误报·安全放量。
    m = (os.environ.get("TEMPORAL_GROUNDING_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _paragraphs_with_gaps(text: str) -> list:
    """按空行分段·记录每段前的空行数 blank_before。返回 [{"text","blank_before"}]。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = []
    cur = []
    blank_run = 0
    blank_before_cur = 0
    for ln in text.split("\n"):
        if ln.strip() == "":
            if cur:
                paras.append({"text": "\n".join(cur), "blank_before": blank_before_cur})
                cur = []
                blank_run = 1
            else:
                blank_run += 1
            continue
        if not cur:
            blank_before_cur = blank_run
        cur.append(ln.strip())
    if cur:
        paras.append({"text": "\n".join(cur), "blank_before": blank_before_cur})
    return paras


def split_scenes(text: str) -> list:
    """把草稿切成场景（[{"text"}]）。场景开头 = 第 1 段 / 空行间隔>=2 / 时空转换词 / 分隔符之后首段。"""
    text = _strip_changes(text)
    scenes = []
    cur = None
    for p in _paragraphs_with_gaps(text):
        ptext = p["text"].strip()
        if not ptext:
            continue
        if SEPARATOR.fullmatch(ptext):
            if cur is not None:
                scenes.append(cur)
                cur = None
            continue
        new_scene = (
            cur is None
            or p["blank_before"] >= 2
            or bool(TRANSITION.match(ptext))
        )
        if new_scene:
            if cur is not None:
                scenes.append(cur)
            cur = {"text": ptext}
        else:
            cur["text"] += "\n" + ptext
    if cur is not None:
        scenes.append(cur)
    return scenes


def detect_temporal_flatness(text: str) -> dict:
    """统计各场景是否含时间推进锚点。返回 {total_scenes, time_grounded_scenes, thin_ratio, thin_scenes[]}。"""
    scenes = split_scenes(text)
    thin_scenes = []
    grounded = 0
    for idx, sc in enumerate(scenes):
        if TIME_ANCHOR.search(sc["text"]):
            grounded += 1
        else:
            thin_scenes.append({"scene_index": idx, "preview": sc["text"][:60]})
    total = len(scenes)
    thin_ratio = ((total - grounded) / total) if total else 0.0
    return {
        "total_scenes": total,
        "time_grounded_scenes": grounded,
        "thin_ratio": round(thin_ratio, 3),
        "thin_scenes": thin_scenes[:6],
    }


def scan(draft_path, project_root=None) -> dict:
    """时间流逝感缺失检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "temporal_grounding", "schema_version": "1.0", "mode": mode,
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
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    result = detect_temporal_flatness(draft)
    out["metrics"] = result
    out["total_scenes"] = result["total_scenes"]
    out["thin_ratio"] = result["thin_ratio"]

    thin_ratio = result["thin_ratio"]
    over = result["total_scenes"] >= MIN_SCENES and thin_ratio > THIN_RATIO_FLOOR
    if over:
        msg = (f"多数场景缺时间推进锚点（{thin_ratio:.0%} 的场景 0 时间锚点 > "
               f"{THIN_RATIO_FLOOR:.0%}·{result['total_scenes'] - result['time_grounded_scenes']}/"
               f"{result['total_scenes']} 场景）·时间流逝感弱。"
               f"建议场景转换加时段/相对时间标记（清晨/傍晚/翌日/三天后/过了许久）")
        if mode == "active":
            out["violations"].append({
                "kind": "temporal_grounding_thin", "severity": "minor",
                "message": msg, "thin_ratio": thin_ratio,
                "sample_thin_scenes": result["thin_scenes"][:4],
                "_doc": "时间流逝感(temporal flatness)·时间标记密度是创作选择(高速连续场/单一时刻定格)→"
                        "advisory 待裁决·时间锚点缺失是可算半边粗糙哨兵·真时间感留 judge/作者"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] temporal_grounding: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="时间流逝感缺失检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="兼容传参(本 scanner 暂不读作者档)")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
