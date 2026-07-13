#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mid_chapter_micro_cliffhanger_cadence_scanner.py — 章内 micro-cliffhanger 节奏 advisory · cluster · 2026-06-21 R20 W9 Batch-CC · P2

【缺口 · R20 SEO id 12】此前 hook_strength_scanner 只看章末钩 + 拟切点
强度均值——缺『章内多 hook 之间间距分布』的检测闭环。爆款移动阅读经验
(番茄/起点/七猫拆书 + RiverEditor pacing notes 2025): 章内 micro-cliffhanger
间距均匀(每 1k-2k CJK 一个 hook 钩)= 读者翻页粘性最大化；间距过疏(单
长场景独享一段)→ 读者中段流失，间距过密(连珠炮抛钩)→ 透支感官疲劳。

【做法 · 确定性 · 零 LLM/零联网】
  · 复用 hook_strength_scanner 的 HOOK_TYPES 11 型 regex (suspense/conflict/
    contrast/infogap/eerie + R7 W2 升级 6 型 reversal_setup/unfinished_action/
    new_setting/decision_pending/promise/threat)
  · 全 cluster 草稿扫每个 hook 命中 → char_pos 序列
  · 相邻 hook 距离序列 distances[] (单位 CJK)
  · 关键指标:
    - hook_count 总 hook 数
    - mean_distance / median_distance / pstdev_distance
    - intervals_below_min: 间距 < HIGH_DENSITY_MIN 个数 (过密)
    - intervals_above_max: 间距 > LOW_DENSITY_MAX 个数 (过疏)

【作者档第一权威 · z-band】
  · 读 作者风格.json.micro_cliffhanger_cadence_baseline
    {"mean_distance": μ, "std_distance": σ}
  · cluster mean_distance 算 z = (mean_dist - μ) / σ
  · |z| > 1.0 → MID_CHAPTER_CLIFF_CADENCE_OFF_BAND advisory
  · 无作者档 → 兜底 (μ=1500, σ=600)·只在极端偏离(|z| > 1.5)报

【与既有 scanner 严格正交】
  · hook_strength_scanner 看章末强度 + 拟切点钩子均值·**不算间距分布**
  · cliffhanger_quota (cross_cluster_engagement_metrics) 看章末钩配比·跨章·**不算章内间距**
  · 本 scanner = 章内 hook 间距分布(cadence)唯一覆盖

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  MID_CHAPTER_CLIFF_CADENCE_OFF_BAND 绝不进 audit_hub.HARD_GATE_CODES。

env MID_CHAPTER_CLIFF_CADENCE_MODE: off / shadow(默认) / active
用法: python mid_chapter_micro_cliffhanger_cadence_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

ISSUE_CODE = "MID_CHAPTER_CLIFF_CADENCE_OFF_BAND"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 兜底基线(无作者档 · 中性估计 · 单位 CJK)
DEFAULT_MEAN_DISTANCE = 1500.0
DEFAULT_STD_DISTANCE = 600.0

# 钩子密度门槛(确定性 · 占位 · 真校准 defer 真作者 hook ledger)
HIGH_DENSITY_MIN_DISTANCE = 500    # 间距 < 500 CJK = 过密(连珠炮)
LOW_DENSITY_MAX_DISTANCE = 3000    # 间距 > 3000 CJK = 过疏(中段冷场)

# z-score 阈值
DRIFT_Z_THRESHOLD_AUTHOR = 1.0      # 有作者档 z>1.0 报
DRIFT_Z_THRESHOLD_FALLBACK = 1.5    # 无作者档 z>1.5 报

# 最少 hook 数才出统计 (低于跳过)
MIN_HOOKS = 3

# 占位 hook regex (复用 hook_strength_scanner 11 型核心词袋的子集 · 避免循环依赖)
# 真生产从 hook_strength_scanner import HOOK_TYPES 同步·此处镜像一份保持独立
_SUSPENSE = re.compile(
    r"(为什么|怎么会|是谁|什么东西|不知道|不明白|想不通|说不清|"
    r"倒计时|还剩|来不及|赶不上|等着他|等了很久|一直在等|"
    r"不记得|没印象|想不起|从没见过|第一次见)")
_CONFLICT = re.compile(
    r"(动手|出手|拔|举枪|举起|逼近|围住|挡住|拦住|对峙|"
    r"威胁|警告|命令|不许|必须|否则|要么|杀|打|砸|撞|"
    r"翻脸|撕破|摊牌|宣战|找上门|堵在|逼到)")
_CONTRAST = re.compile(
    r"(竟然|居然|没想到|出乎意料|意外|反而|不是.{0,8}而是|"
    r"原来|真相|其实|根本|并不是|早就|一直都是|"
    r"不对劲|不太对|哪里不对|事情不简单)")
_INFOGAP = re.compile(
    r"(没说完|话没说完|欲言又止|顿住|停住|没再|不肯说|"
    r"空着|空白|没有(?:编号|签名|名字|落款)|"
    r"只有.{0,10}没有|留下.{0,10}就走|消失|不见了|不翼而飞)")
_EERIE = re.compile(
    r"(凉了|冷了下来|背后(?:一凉|发凉)|寒意|"
    r"认得他|等他.{0,6}很久|看着他|没人在|自己(?:都没|没有|没下|动|浮|爬))")
_UNFINISHED = re.compile(
    r"(刚要|正要|还没来得及|话音未落|手刚伸出|脚还没落地|"
    r"刚走到一半|刀刚出鞘|门刚推开|字才写到一半|"
    r"还没说完|没等.{0,4}就|一脚踏出|举到一半)")

_HOOK_PATTERNS = [
    ("suspense", _SUSPENSE),
    ("conflict", _CONFLICT),
    ("contrast", _CONTRAST),
    ("infogap", _INFOGAP),
    ("eerie", _EERIE),
    ("unfinished_action", _UNFINISHED),
]


def _mode() -> str:
    m = (os.environ.get("MID_CHAPTER_CLIFF_CADENCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


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
            mb = obj.get("micro_cliffhanger_cadence_baseline")
            if isinstance(mb, dict):
                return mb
    return None


def _collect_hook_positions(text: str) -> list[tuple[int, str]]:
    """收集所有 hook 命中的字符位置 + 类型。按位置升序排序。"""
    positions: list[tuple[int, str]] = []
    for name, pat in _HOOK_PATTERNS:
        for m in pat.finditer(text):
            positions.append((m.start(), name))
    positions.sort(key=lambda x: x[0])
    return positions


def _adjacent_distances(positions: list[tuple[int, str]]) -> list[int]:
    if len(positions) < 2:
        return []
    return [positions[i + 1][0] - positions[i][0] for i in range(len(positions) - 1)]


def _z(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return round((value - mean) / std, 3)


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "mid_chapter_micro_cliffhanger_cadence",
           "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory",
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
    if cjk < 1500:
        out["note"] = "草稿太短·跳过"
        return out

    positions = _collect_hook_positions(text)
    distances = _adjacent_distances(positions)

    out.update({
        "cjk": cjk,
        "hook_count": len(positions),
        "hook_types": [t for _, t in positions[:20]],
    })

    if len(positions) < MIN_HOOKS:
        out["note"] = f"hook 命中 {len(positions)} < {MIN_HOOKS}·样本不足跳过"
        out["violations_count"] = 0
        return out

    mean_d = round(statistics.mean(distances), 1)
    median_d = round(statistics.median(distances), 1)
    std_d = round(statistics.pstdev(distances), 1) if len(distances) >= 2 else 0.0

    below_min = sum(1 for d in distances if d < HIGH_DENSITY_MIN_DISTANCE)
    above_max = sum(1 for d in distances if d > LOW_DENSITY_MAX_DISTANCE)

    baseline = _read_author_baseline(project_root)
    baseline_source = "fallback"
    use_mean = DEFAULT_MEAN_DISTANCE
    use_std = DEFAULT_STD_DISTANCE
    threshold = DRIFT_Z_THRESHOLD_FALLBACK
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        m = baseline.get("mean_distance")
        s = baseline.get("std_distance")
        if isinstance(m, (int, float)) and m > 0:
            use_mean = float(m)
        if isinstance(s, (int, float)) and s > 0:
            use_std = float(s)
        threshold = DRIFT_Z_THRESHOLD_AUTHOR

    z_score = _z(mean_d, use_mean, use_std)

    out.update({
        "distances": distances,
        "mean_distance": mean_d,
        "median_distance": median_d,
        "pstdev_distance": std_d,
        "intervals_below_min": below_min,
        "intervals_above_max": above_max,
        "z_score": z_score,
        "baseline_source": baseline_source,
        "baseline": {"mean_distance": use_mean, "std_distance": use_std},
        "threshold_z": threshold,
    })

    flags = []
    if abs(z_score) > threshold:
        direction = "过疏" if z_score > 0 else "过密"
        flags.append({
            "code": ISSUE_CODE,
            "msg": (f"章内 hook 间距 z={z_score}{direction}"
                    f"(mean={mean_d} vs author μ={round(use_mean,1)}±σ={round(use_std,1)})"
                    f"·章内 {len(positions)} hook·{below_min} 间距过密 / {above_max} 间距过疏")
        })

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "mid_chapter_cliff_cadence", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "章内 micro-cliffhanger 间距分布 · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] mid_chapter_cliff_cadence: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="章内 micro-cliffhanger 节奏 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
