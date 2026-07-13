#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""narrating_distance_scanner.py — time-of-telling vs time-told 时距五分级
(advisory · cluster · 2026-06-20 R9 W5 Batch-L)

【缺口】R9 联网调研 (Genette narrating distance《Narrative Discourse》): 叙述时(time of
telling) 与故事时(time told)的距离五分级:
  · concurrent  : 同时(直播感·现在进行时锚词主导)
  · recent      : 近回顾(『刚刚/方才/不久前』+ hindsight 低密度)
  · distant     : 远回顾(『多年前/那一年/如今想来』+ hindsight 高密度)
  · posthumous  : 死后/后世(『他死后』『传到后世』『多年以后人们传说』标记)
  · atemporal   : 抽象/史诗(『古时』『传说中』『远古时代』完全脱时间锚)
此前全系统：
  · R7 firstperson_retro_self_gap 只查 first_retro_* hindsight 密度
  · 【其他四级零检测】
本 scanner 是 R7 的超集：抓 tense 锚词 + hindsight 密度 → 推断 distance 等级 → 比 manifest
声明的 distance 偏差 → 两种 flag:
  · DISTANCE_TENSE_DRIFT: manifest 标 distant 实际锚词全 concurrent(无锚切换)。
  · DISTANCE_FLATTENED: 声称远距(distant/posthumous/atemporal)实际流水账无 hindsight。

【做法 · 确定性纯规则正则】:
  1. tense 锚词桶: concurrent_now / recent_past / distant_past / posthumous / atemporal。
  2. 计每桶 per_1k → 最大桶为 detected_distance。
  3. manifest.declared_narrating_distance(作者档 narrating_distance 兜底)优先。
  4. detected != declared 且 declared ∈ {distant, posthumous, atemporal} → FLATTENED。
  5. 无 declared 但桶混杂(top1/top2 比 <1.5 且 hindsight=0) → TENSE_DRIFT。

【与 R7 firstperson_retro_self_gap 去重】R7 是本 scanner 的子集(只覆盖
distant + 第一人称 retro);本 scanner 覆盖全 5 级 + 任意 POV。运行时:R7 active 时本
scanner 默认 shadow 等同空跑(共存零冲突)。

【北极星② / ⑤ 顾问非法官】distance 是作者选择·全 advisory，
  code DISTANCE_* **绝不进 audit_hub.HARD_GATE_CODES**。
  env NARRATING_DISTANCE_MODE: off / shadow(默认) / active。

用法：python narrating_distance_scanner.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE_DRIFT = "DISTANCE_TENSE_DRIFT"
ISSUE_CODE_FLATTENED = "DISTANCE_FLATTENED"

DISTANCE_LEVELS = ("concurrent", "recent", "distant", "posthumous", "atemporal")
REMOTE_LEVELS = {"distant", "posthumous", "atemporal"}

# 五桶 tense 锚词
TENSE_BUCKETS = {
    "concurrent": re.compile(r"此刻|此时|现在|正在|眼下|今天|今夜|刚刚要|马上|立刻"),
    "recent": re.compile(r"刚才|方才|刚刚|不久前|片刻前|适才|先前|前一刻"),
    "distant": re.compile(r"多年前|当年|那一年|早年|十年前|从前|往日|昔日|那时候"),
    "posthumous": re.compile(r"他死后|她死后|后世|多年以后人们|百年之后|传到后人|身后留下"),
    "atemporal": re.compile(r"古时|上古|远古时代|传说中|相传|神话时代|不知何时|亘古"),
}
# hindsight 标记（直接复用 R7 词典精简集）
HINDSIGHT = re.compile(r"回想起来|后来才明白|多年[之以]?后|如今想来|时至今日|事后回想")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 500


def _mode() -> str:
    m = (os.environ.get("NARRATING_DISTANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    return load_json(p)


def _read_manifest(manifest_path):
    if not manifest_path:
        return {}
    obj = _read_json(Path(manifest_path))
    return obj if isinstance(obj, dict) else {}


def _resolve_declared(project_root, manifest):
    v = manifest.get("declared_narrating_distance") or manifest.get("narrating_distance")
    if isinstance(v, str) and v.strip().lower() in DISTANCE_LEVELS:
        return v.strip().lower()
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    obj = _read_json(p)
    if isinstance(obj, dict):
        nd = obj.get("narrating_distance")
        if isinstance(nd, str) and nd.strip().lower() in DISTANCE_LEVELS:
            return nd.strip().lower()
    return None


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "narrating_distance", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "verdict": "PASS",
           "violations": [], "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    per_1k_factor = cjk / 1000.0
    buckets = {}
    for k, rx in TENSE_BUCKETS.items():
        cnt = len(rx.findall(text))
        buckets[k] = {"count": cnt,
                      "per_1k": round(cnt / per_1k_factor, 3) if per_1k_factor else 0.0}
    out["tense_buckets"] = buckets
    hindsight_count = len(HINDSIGHT.findall(text))
    hindsight_density = round(hindsight_count / per_1k_factor, 3) if per_1k_factor else 0.0
    out["hindsight_density_per_1k"] = hindsight_density

    # detected = 最大桶（命中数 > 0）·全空→None
    nonzero = [(k, v["count"]) for k, v in buckets.items() if v["count"] > 0]
    detected = None
    if nonzero:
        nonzero.sort(key=lambda kv: -kv[1])
        detected = nonzero[0][0]
    out["detected_distance"] = detected

    manifest = _read_manifest(manifest_path)
    declared = _resolve_declared(project_root, manifest)
    out["declared_narrating_distance"] = declared

    violations = []
    if declared in REMOTE_LEVELS:
        # FLATTENED: 声称远距实际锚词全 concurrent / 总命中很少 + hindsight=0
        if (detected == "concurrent"
                or (hindsight_density == 0
                    and sum(v["count"] for v in buckets.values()) < 2)):
            violations.append({"code": ISSUE_CODE_FLATTENED, "kind": "narrating_distance",
                               "severity": "minor",
                               "message": (f"声称 {declared} 远距实际无远距锚词("
                                           f"detected={detected}·hindsight={hindsight_density}/千字)"
                                           f"·流水账感"),
                               "declared": declared, "detected": detected,
                               "hindsight_density": hindsight_density,
                               "_doc": "advisory·R7 firstperson_retro 是 distant 子集"})
    elif declared is None:
        # TENSE_DRIFT: top1/top2 比 <1.5 (锚词混杂) 且 hindsight=0
        if len(nonzero) >= 2 and hindsight_density == 0:
            top1 = nonzero[0][1]
            top2 = nonzero[1][1]
            if top1 < 1.5 * top2:
                violations.append({"code": ISSUE_CODE_DRIFT,
                                   "kind": "narrating_distance",
                                   "severity": "minor",
                                   "message": (f"tense 锚词混杂无主导(top1 {nonzero[0][0]}={top1}"
                                               f"·top2 {nonzero[1][0]}={top2})·无 hindsight 切换"),
                                   "top_buckets": nonzero[:3],
                                   "_doc": "advisory·建议作者档声明 narrating_distance"})

    out["violations_count"] = len(violations)
    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = violations[0]["message"]
        else:
            for v in violations:
                print(f"[SHADOW] narrating_distance: {v['message']} — 不上报",
                      file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="time-of-telling 五分级(advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
