#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""duration_mix_scanner.py — Genette 五种 duration 比例检测 (advisory · cluster · 2026-06-20)

【缺口】R8 W4 Batch-H · L22 联网调研：Genette《叙事话语》(Narrative Discourse 1972) 的
五种叙述持续模式 (scene/summary/ellipsis/pause/stretch) 是叙事时长 vs 故事时长比的根盘工艺指纹，
此前全系统【零检测】。LLM 默认产 scene+summary 高占比、ellipsis/stretch/pause 几乎为 0 =
节奏单调，与作者档 (作者偏好 summary or stretch) 错配亦无检测闭环。

【五型定义 (Genette·Numberanalytics 2024·Sciencedirect 2024)】
  · scene   场景：叙述时长 ≈ 故事时长 (对话/即时动作)·LLM 默认偏好
  · summary 概述：多事件压缩 (整整三天/在接下来的几个月/这段时间/连日)
  · ellipsis 省略：时间跳过 (一晃数日/转眼/翌日/数月后)
  · pause   停顿：叙述继续但故事时间冻结 (景物描写/作者议论/心理停留)
  · stretch 拉伸/慢镜：叙述时长 > 故事时长 (一瞬间她想起/慢动作/时间凝固/那一刻)

【做法 · 确定性纯规则正则 (不依赖 LLM)】
  1. 按句末符号 (。！？……) 切句。
  2. 每句按优先级匹配标志词：ellipsis > summary > stretch > scene (含对话/动作) > pause (默认)。
  3. 输出五型 pct (scene_pct / summary_pct / ellipsis_pct / pause_pct / stretch_pct)。
  4. 对比作者档 duration_mix_baseline (若有) → z-band > 1.5σ 标 advisory；
     无作者档 → 通用兜底：ellipsis < 1% 或 stretch < 0.5% 且 cluster > 8000 CJK 视为
     LLM 默认节奏单调 (advisory hint)。

【北极星 ②/⑤ 顾问非法官】节奏分布是创作工艺·作者档第一权威·code DURATION_MIX_DRIFT
  绝不进 audit_hub.HARD_GATE_CODES。env DURATION_MIX_MODE: off / shadow (默认) / active。

用法: python duration_mix_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "DURATION_MIX_DRIFT"   # advisory · 绝不进 HARD_GATE_CODES

# 五型分类标志词 (高确定性·宁可漏报不误报)
ELLIPSIS_MARKERS = re.compile(
    r"(一晃|转眼|不知不觉|不觉|光阴荏苒|斗转星移|岁月如梭|时光飞逝|"
    r"翌日|次日|隔日|几日后|数日后|几天后|数天后|几周后|数周后|"
    r"几月后|数月后|几年后|数年后|多年后|几年间|多年来|"
    r"良久之后|许久之后|再后来|多年以后)"
)
SUMMARY_MARKERS = re.compile(
    r"(整整[一二三四五六七八九十百千万0-9]+[天日月年个]|"
    r"连续[一二三四五六七八九十百千万0-9]+[天日月年个]|"
    r"在接下来的|这段(?:日子|时间|期间)|那段(?:日子|时间|期间)|"
    r"连日来|连日不断|多日以来|数月以来|数年以来|"
    r"每天[都]?|每日[都]?|每个月[都]?|每周[都]?|"
    r"从此以后|自此以后|此后[的之]?|后来的[一二三四五六七八九十百千万0-9日月年])"
)
STRETCH_MARKERS = re.compile(
    r"(一瞬间|那一瞬|这一瞬|一刹那|刹那间|一霎那|霎时间|霎那间|"
    r"时间(?:凝固|静止|停滞|仿佛停)|时间像被|慢镜头|慢动作|"
    r"仿佛过了很久|仿佛过了一(?:个)?世纪|这一刻仿佛|"
    r"一帧帧|一寸寸|一点一点地|脑海中(?:闪过|浮现|涌现)|"
    r"恍惚间|恍惚之间|脑海里(?:闪过|浮现)|往事(?:涌上|涌入))"
)
# 对话/即时动作 (scene 主要识别)
SCENE_DIALOGUE = re.compile(r"[“”「」『』“”「」『』]")  # 中文引号
SCENE_ACTION_VERB = re.compile(
    r"^(?:他|她|它|我|你|你们|他们|她们)?\s*"
    r"(走|跑|跳|扑|抓|拍|推|拉|握|抬|挥|抽|抢|砸|踢|踹|劈|刺|斩|"
    r"看|望|瞧|瞪|盯|瞥|笑|哭|喊|叫|吼|喝|说|道|问|答|应|回|"
    r"坐|站|蹲|跪|躺|靠|侧|转|低|抬头|侧头|歪头|皱|皱眉)"
)
# 纯描写/议论标志 (pause)
PAUSE_DESC_HINT = re.compile(
    r"(月色|阳光|天空|云朵|雪花|夜色|晨曦|晚霞|暮色|霞光|"
    r"屋(?:子|内|外)|院(?:子|内|外)|墙(?:面|壁|角)|"
    r"远处|四周|四面|周围|前方|身后|两旁|"
    r"这是一(?:座|间|栋|片|个)|这里[是有]|外面[是有]|远方[是有])"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_SENT_SPLIT = re.compile(r"(?<=[。！？\?！…])(?!”|」|』|\.{2,3})")


def _mode() -> str:
    m = (os.environ.get("DURATION_MIX_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_sentences(text: str) -> list[str]:
    """按句末符号切句·过滤极短残片 (< 4 CJK)。"""
    out = []
    for s in _SENT_SPLIT.split(text):
        s = s.strip()
        if _cjk_count(s) >= 4:
            out.append(s)
    return out


def classify_sentence(sent: str) -> str:
    """按优先级分类单句 → ellipsis > summary > stretch > scene > pause。"""
    if ELLIPSIS_MARKERS.search(sent):
        return "ellipsis"
    if SUMMARY_MARKERS.search(sent):
        return "summary"
    if STRETCH_MARKERS.search(sent):
        return "stretch"
    # scene：有对话引号 / 句首是即时动作动词
    if SCENE_DIALOGUE.search(sent):
        return "scene"
    if SCENE_ACTION_VERB.search(sent):
        return "scene"
    # 描写痕迹明显 + 无动作 → pause
    if PAUSE_DESC_HINT.search(sent):
        return "pause"
    # 默认 scene (中性叙事·避免 pause 过分类)
    return "scene"


def compute_distribution(sentences: list[str]) -> dict:
    """统计五型 pct·总数。"""
    counts = {"scene": 0, "summary": 0, "ellipsis": 0, "pause": 0, "stretch": 0}
    for s in sentences:
        counts[classify_sentence(s)] += 1
    n = max(sum(counts.values()), 1)
    pct = {f"{k}_pct": round(v / n, 4) for k, v in counts.items()}
    return {"counts": counts, "total_sentences": n, **pct}


def _read_author_baseline(project_root) -> dict | None:
    """读 作者风格.json 的 duration_mix_baseline·无 → None (走通用兜底)。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path in (db / "作者风格.json", db / "作者风格_FINAL.json"):
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        b = obj.get("duration_mix_baseline")
        if isinstance(b, dict) and b:
            return b
    return None


def _z_drift(dist: dict, baseline: dict) -> list[dict]:
    """与作者档基线对比·z>1.5σ 标 drift item·每型独立。"""
    drift = []
    for k in ("scene_pct", "summary_pct", "ellipsis_pct", "pause_pct", "stretch_pct"):
        actual = dist.get(k)
        b = baseline.get(k)
        if not isinstance(b, dict) or actual is None:
            continue
        mean = b.get("mean")
        std = b.get("std")
        if not isinstance(mean, (int, float)) or not isinstance(std, (int, float)):
            continue
        if std <= 0:
            continue
        z = (actual - mean) / std
        if abs(z) > 1.5:
            drift.append({
                "dimension": k, "actual": round(actual, 4),
                "author_mean": round(mean, 4), "author_std": round(std, 4),
                "z_score": round(z, 2),
                "direction": "high" if z > 0 else "low",
            })
    return drift


def _generic_floor_check(dist: dict, cjk: int) -> str | None:
    """通用兜底：LLM 默认 ellipsis < 1%、stretch < 0.5% 且 cluster > 8000 CJK 视为节奏单调。"""
    if cjk < 8000:
        return None
    ellipsis_low = dist["ellipsis_pct"] < 0.01
    stretch_low = dist["stretch_pct"] < 0.005
    if ellipsis_low and stretch_low:
        return ("时长节奏单调：ellipsis 占比 {ep:.1%}、stretch 占比 {sp:.1%}·全是 scene+summary "
                "(LLM 默认偏好)·建议适度引入时间跳跃 (一晃数日) 或慢镜定格 (一瞬间脑海中闪过) "
                "调节叙事节奏").format(ep=dist["ellipsis_pct"], sp=dist["stretch_pct"])
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "duration_mix", "schema_version": "1.0", "mode": mode,
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

    sentences = _split_sentences(draft)
    if len(sentences) < 10:
        out["note"] = f"句数不足 (n={len(sentences)})·跳过"
        return out

    dist = compute_distribution(sentences)
    out["distribution"] = dist
    out["total_cjk"] = cjk

    baseline = _read_author_baseline(project_root)
    msg = None
    drift_items: list[dict] = []
    if baseline:
        drift_items = _z_drift(dist, baseline)
        out["author_baseline"] = True
        if drift_items:
            samples = "·".join(
                f"{d['dimension']}={d['actual']:.1%} vs 作者基线 {d['author_mean']:.1%} "
                f"(z={d['z_score']})"
                for d in drift_items[:3])
            msg = (f"时长分布偏离作者基线：{len(drift_items)} 项 z>1.5σ ({samples})·"
                   f"建议向作者档 duration_mix 风格靠拢")
    else:
        out["author_baseline"] = False
        msg = _generic_floor_check(dist, cjk)

    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "duration_mix", "severity": "minor",
                "message": msg, "distribution": dist,
                "drift_items": drift_items,
                "_doc": "Genette 五型时长比·作者档第一权威·advisory 可豁免·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] duration_mix: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Genette 五型时长比检测 (advisory · cluster)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读作者档基线")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
