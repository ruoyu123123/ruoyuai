#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_grounding_scanner.py — 白房间综合症/欠写检测（advisory · cluster · 2026-06-19）

【缺口】全系统 ~30 个 scanner 全在查「过写」（AI 腔/句长超标/情绪直陈/标志词 tell 过多/重复名词…），
**无一查「欠写」** —— 这是一整个缺失的检测类别。来源 Turkey City Lexicon 的 White Room Syndrome
（白房间综合症）：场景在「白房间」里发生——人物悬浮在抽象对话+抽象动作里，读者不知道他们站在
哪、周围有什么、是什么时辰天气、有没有声音气味。弱模型尤其爱开「纯对话白场景」（省力·不落地）。

【做法 · 确定性可算半边】（北极星守卫：欠写质量是语义判断·只做可算的「接地锚点缺失」密度）：
  1. 把 cluster 草稿按空行分段·识别「场景开头」= 第 1 段 + 每个场景断点（空行间隔 >= 2 / 出现
     时空转换标志词 翌日/次日/三天后/与此同时/回到… / 分隔符 *** 之后）的首段。
  2. 取每个场景开头 ~150 CJK·检测是否含任一【接地锚点 GROUNDING】：
       地点专名/室内外（屋房室门窗院街路楼厅店桥山河林城村）·方位（上下左右前后旁里外/中央/角落）·
       可触物件+天气时段（雨雪风雾灯烛夜晨/阳光/月光/黄昏）·非视觉感官（声响味冷热潮/气味/温度）。
  3. 某场景开头纯对话+抽象动作且 0 锚点 → 该场景「白房间」。
     ungrounded_scene_ratio = 白房间场景数 / 总场景数。ratio > 阈值且 总场景 >= 2 → advisory。
  ⚠️ 只能测「显式空间/感官词的缺失」·靠氛围隐性落地的留白笔法测不到 → 故意宽锚点（宁可漏报）·裁决留作者。

【北极星⑤ 顾问非法官】接地密度是创作选择（极简留白/快节奏纯对话场可能故意不落地）·writer 有理由偏离
  → 永远 advisory，code SCENE_GROUNDING_THIN **绝不进 audit_hub.HARD_GATE_CODES**。
  env SCENE_GROUNDING_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值 UNGROUNDED_RATIO_FLOOR/MIN_SCENES 保守占位（宁可漏报不误报）·待金标准校准（真作者原文喂自身 PASS）。
  豁免：作者档极简留白风（_数据库/作者风格.json）· 承接同一地点（开头含 仍/还在/这里/依旧）。

用法：python scene_grounding_scanner.py <draft_path> [--project <root>] [--manifest m.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "SCENE_GROUNDING_THIN"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 接地锚点（空间/感官落地词）—— 故意宽（宁可漏报不误报·北极星⑤）
GROUNDING = re.compile(
    r"[屋房室门窗院街路楼厅店桥山河林城村]"     # 地点专名/室内外
    r"|[上下左右前后旁里外]|中央|角落"          # 方位
    r"|[雨雪风雾灯烛夜晨]|阳光|月光|黄昏"        # 天气/时段/光
    r"|[声响味冷热潮]|气味|温度"               # 非视觉感官
)
# 承接同一地点的延续词（开头含此 = 接上一场景空间·非白房间·豁免）
CONTINUATION = re.compile(r"仍在|仍旧|仍然|还在|这里|原地|依旧|依然")
# 时空转换标志词（在段首 = 新场景开头）
TRANSITION = re.compile(
    r"^[\s　]*(翌日|翌晨|次日|第二天|第三天|三天后|两天后|几天后|半个月后|"
    r"一个月后|多年后|多日后|与此同时|同一时刻|同一时间|另一边|另一头|"
    r"回到|此时|当晚|当夜|入夜|傍晚|清晨|许久之后|片刻之后)")
# 分隔符行（纯分隔·其后首段是新场景·分隔符自身不算场景）
SEPARATOR = re.compile(r"^[\s　\*＊·.。…\-—－◇◆○●※☆★]{1,20}$")

OPENING_CJK = 150           # 取场景开头 ~150 CJK 判接地
SCENE_BREAK_BLANKS = 2      # 空行间隔 >= 2 视为场景断点（单空行=普通分段·非换场）
UNGROUNDED_RATIO_FLOOR = 0.5   # 🔬 待金标准校准（真作者原文喂自身）：白房间占比 > 此 = 欠写偏多
MIN_SCENES = 2              # 总场景 < 此 不报（样本太少·conservative）
MIN_CJK = 500              # 草稿 < 此 跳过

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("SCENE_GROUNDING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _first_cjk(text: str, n: int) -> str:
    """取 text 前 n 个 CJK 为止的切片（含中间非 CJK 字符）。"""
    out = []
    count = 0
    for ch in text:
        out.append(ch)
        if "一" <= ch <= "鿿":
            count += 1
            if count >= n:
                break
    return "".join(out)


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
            or p["blank_before"] >= SCENE_BREAK_BLANKS
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


def detect_white_rooms(text: str) -> dict:
    """检测各场景开头是否接地。返回 {total_scenes, ungrounded_count, ratio, white_rooms[]}。"""
    scenes = split_scenes(text)
    white_rooms = []
    for idx, sc in enumerate(scenes):
        opening = _first_cjk(sc["text"], OPENING_CJK)
        if CONTINUATION.search(opening):
            continue   # 承接同一地点·豁免（非白房间）
        if GROUNDING.search(opening):
            continue   # 有接地锚点
        white_rooms.append({"scene_index": idx, "opening_preview": opening[:60]})
    total = len(scenes)
    ratio = (len(white_rooms) / total) if total else 0.0
    return {
        "total_scenes": total,
        "ungrounded_count": len(white_rooms),
        "ungrounded_scene_ratio": round(ratio, 3),
        "white_rooms": white_rooms[:6],
    }


def _author_minimalist(project_root) -> bool:
    """作者档是否极简留白风（→ 豁免白房间检测）。无档/无关键词→False。"""
    if not project_root:
        return False
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return False
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(prof, dict):
        return False
    if prof.get("minimalist") is True or prof.get("白描风") is True:
        return True
    blob = []
    for k in ("style_tags", "tags", "prose_style", "narrative_style",
              "style_summary", "description", "风格标签", "白描", "留白"):
        v = prof.get(k)
        if isinstance(v, str):
            blob.append(v)
        elif isinstance(v, (list, tuple)):
            blob.append(" ".join(str(x) for x in v))
    text = " ".join(blob)
    return any(kw in text for kw in ("白描", "极简留白", "留白风", "极简主义", "极简风"))


def scan(draft_path, project_root=None) -> dict:
    """白房间/欠写检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "scene_grounding", "schema_version": "1.0", "mode": mode,
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

    result = detect_white_rooms(draft)
    out["metrics"] = result
    out["total_scenes"] = result["total_scenes"]
    out["ungrounded_scene_ratio"] = result["ungrounded_scene_ratio"]

    ratio = result["ungrounded_scene_ratio"]
    over = result["total_scenes"] >= MIN_SCENES and ratio > UNGROUNDED_RATIO_FLOOR
    if over:
        if _author_minimalist(project_root):
            out["note"] = "作者档极简留白风·豁免白房间检测"
            out["violations_count"] = 0
            return out
        msg = (f"白房间/欠写偏多（{ratio:.0%} 的场景开头 0 接地锚点 > "
               f"{UNGROUNDED_RATIO_FLOOR:.0%}·{result['ungrounded_count']}/{result['total_scenes']} 场景）。"
               f"建议给场景开头加空间/感官接地（人物站在哪·周围有什么·时辰天气·声音气味）")
        if mode == "active":
            out["violations"].append({
                "kind": "scene_grounding_thin", "severity": "minor",
                "message": msg, "ungrounded_scene_ratio": ratio,
                "sample_white_rooms": result["white_rooms"][:4],
                "_doc": "White Room Syndrome(Turkey City)·欠写是创作选择(极简留白/快节奏纯对话)→"
                        "advisory 待裁决·接地锚点缺失是可算半边粗糙哨兵·真留白质量留 judge/作者"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] scene_grounding: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="白房间综合症/欠写检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读作者档极简留白豁免基线")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
