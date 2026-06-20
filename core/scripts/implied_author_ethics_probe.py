#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""implied_author_ethics_probe.py — Phelan 三轴叙事伦理探针
(advisory · cluster · 2026-06-20 R9 W5 Batch-L)

【缺口】R9 联网调研 (Phelan《Living to Tell about It》): 隐含作者(implied author) 三轴伦理:
  · Ethics of the Told    : 被叙事的伦理(谁的痛被给屏占·反派 vs 受害方比例)。
  · Ethics of the Telling : 叙述行为的伦理(作者闯入/全知点评频率)。
  · Ethics of the Reading : 阅读契约的伦理(narratee 呼语密度)。
此前全系统【三轴零探针】。LLM 默认产中性叙述=反派单方屏占/无作者闯入/无 narratee 招呼。

【做法 · 确定性零依赖】:
  1. asymmetric_screen_ratio = 反派出场段字数 / (反派 + 受害方段字数)。
     反派/受害方名册由 manifest.antagonist_cast + victim_cast 显式声明。
     ≥0.75 → 反派单方屏占（受害方失语）。
  2. telling_intrusion_rate = 闯入标记(评判式短语「不得不说/读者可能不知」)per_1k。
     <作者档 ethics_signature.intrusion_min → SCREEN_ASYMMETRY_HIGH/TELLING_THIN。
  3. narratee_address_density = 二人称呼语(『各位读者/诸位看官/你们』)per_1k。
     与作者档 ethics_signature.narratee_min 比 → NARRATEE_ADDRESS_THIN。

【与 R8 L25 metalepsis_budget + R8 L28 narratee_address_scanner 去重】:
  · L25 查 metalepsis 越界次数 vs budget
  · L28 查 narratee 称谓一致性(混用)
  · 本探针查三轴整体伦理 signature 偏差(三指标聚合视角)·正交不双计。

【北极星② / ⑤ 顾问非法官】伦理签名是作者第一权威·全 advisory，
  code IMPLIED_AUTHOR_ETHICS_* **绝不进 audit_hub.HARD_GATE_CODES**。
  env IMPLIED_AUTHOR_ETHICS_MODE: off / shadow(默认) / active。

用法：python implied_author_ethics_probe.py <draft_path> [--project <root>] [--manifest <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_SCREEN = "IMPLIED_AUTHOR_SCREEN_ASYMMETRY"
ISSUE_CODE_TELLING = "IMPLIED_AUTHOR_TELLING_THIN"
ISSUE_CODE_NARRATEE = "IMPLIED_AUTHOR_NARRATEE_THIN"

# 作者闯入标记词
TELLING_INTRUSION = re.compile(
    r"不得不说|读者可能不知|且看[他她]|话说回来|须知|且不论|姑且不论|"
    r"在这里要说|笔者|作者要说|此处需说明|说来也奇|说来也怪")
# narratee 呼语
NARRATEE_ADDRESS = re.compile(
    r"各位读者|诸位看官|亲爱的读者|你们或许|你们可能|你以为|"
    r"诸君|看官|读者朋友|阅文人")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 500


def _mode() -> str:
    m = (os.environ.get("IMPLIED_AUTHOR_ETHICS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_manifest(path):
    if not path:
        return {}
    obj = _read_json(Path(path))
    return obj if isinstance(obj, dict) else {}


def _ethics_signature(project_root):
    if not project_root:
        return {}
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return {}
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return {}
    sig = obj.get("ethics_signature")
    return sig if isinstance(sig, dict) else {}


def _segments_by_cast(text, names):
    """按角色名出现近邻把文本分段（启发式：以角色名命中 ±200 字归属其段）。
    返回该 cast 总字数计数。"""
    if not names:
        return 0
    total = 0
    for n in names:
        if not n:
            continue
        # 每次命中算 ±100 字窗口
        for m in re.finditer(re.escape(n), text):
            start = max(0, m.start() - 100)
            end = min(len(text), m.end() + 100)
            total += end - start
    return total


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "implied_author_ethics", "schema_version": "1.0", "mode": mode,
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
    per_1k = cjk / 1000.0
    manifest = _read_manifest(manifest_path)
    sig = _ethics_signature(project_root)

    # ① asymmetric_screen_ratio
    antagonists = manifest.get("antagonist_cast") or []
    victims = manifest.get("victim_cast") or []
    if not isinstance(antagonists, list):
        antagonists = []
    if not isinstance(victims, list):
        victims = []
    ant_chars = _segments_by_cast(text, antagonists)
    vic_chars = _segments_by_cast(text, victims)
    ratio = None
    if (ant_chars + vic_chars) >= 200:  # 足够样本
        ratio = round(ant_chars / (ant_chars + vic_chars), 3)
    out["asymmetric_screen_ratio"] = ratio
    out["antagonist_chars"] = ant_chars
    out["victim_chars"] = vic_chars

    # ② telling_intrusion_rate
    intrusion_count = len(TELLING_INTRUSION.findall(text))
    intrusion_rate = round(intrusion_count / per_1k, 3) if per_1k else 0.0
    out["telling_intrusion_per_1k"] = intrusion_rate

    # ③ narratee_address_density
    narratee_count = len(NARRATEE_ADDRESS.findall(text))
    narratee_density = round(narratee_count / per_1k, 3) if per_1k else 0.0
    out["narratee_address_per_1k"] = narratee_density

    # 阈值
    screen_max = float(sig.get("asymmetric_screen_max", 0.75))
    intrusion_min = sig.get("intrusion_min")
    narratee_min = sig.get("narratee_min")

    violations = []
    if ratio is not None and ratio >= screen_max and victims:
        violations.append({"code": ISSUE_CODE_SCREEN,
                           "kind": "implied_author_ethics_told",
                           "severity": "minor",
                           "message": (f"反派单方屏占 ratio={ratio} ≥{screen_max}"
                                       f"·受害方失语·建议给被害方 ≥1 perceptual sentence"),
                           "ratio": ratio,
                           "antagonist_chars": ant_chars,
                           "victim_chars": vic_chars,
                           "_doc": "advisory·Ethics of the Told 轴"})

    if intrusion_min is not None and intrusion_rate < float(intrusion_min):
        violations.append({"code": ISSUE_CODE_TELLING,
                           "kind": "implied_author_ethics_telling",
                           "severity": "minor",
                           "message": (f"作者闯入 {intrusion_rate}/千字 < 作者档 floor"
                                       f" {intrusion_min}·全知点评偏少"),
                           "rate": intrusion_rate, "floor": float(intrusion_min),
                           "_doc": "advisory·Ethics of the Telling 轴"})

    if narratee_min is not None and narratee_density < float(narratee_min):
        violations.append({"code": ISSUE_CODE_NARRATEE,
                           "kind": "implied_author_ethics_reading",
                           "severity": "minor",
                           "message": (f"narratee 呼语 {narratee_density}/千字 < 作者档 floor"
                                       f" {narratee_min}·阅读契约缺位"),
                           "density": narratee_density, "floor": float(narratee_min),
                           "_doc": "advisory·Ethics of the Reading 轴"})

    out["violations_count"] = len(violations)
    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = violations[0]["message"]
        else:
            for v in violations:
                print(f"[SHADOW] implied_author_ethics: {v['message']} — 不上报",
                      file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Phelan 三轴伦理探针(advisory · shadow)")
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
