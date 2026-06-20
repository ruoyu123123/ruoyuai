#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""firstperson_retro_self_gap_scanner.py — 第一人称回溯叙述 hindsight 签到检测
(advisory · cluster · 2026-06-20)

【缺口】R7 联网调研(Stanzel/Cohn consonant-dissonant 1964/1978 · Cohn《Transparent Minds》·
Taylor&Francis 2020 Narrating-vs-Experiencing Self): 第一人称回溯叙述需要 narrating-self
(回顾视角)与 experiencing-self(当下视角)的距离签到。LLM 默认全程贴 experiencing-self 写
→ 回溯感缺失 = 退化成第一人称当下时。本 scanner 补 hindsight 词典命中度。

【与 R6 future_knowledge_leak 去重】:
  - future_knowledge_leak 检测『主角当下不该知道却用了未来知识』(信息泄露 hard_gate 方向)
  - 本 scanner 检测『narrating-self 缺位』(工艺密度 advisory 方向)
  - 两者完全正交:前者管不应有, 本者管应有不足

【做法 · 确定性纯规则正则(不依赖 LLM)】:
  1. narrative_pov_mode 门控:仅在 first_retro_consonant / first_retro_dissonant 激活;
     其他模式(first_present/third_limited/third_omniscient)skip。
  2. hindsight 词典:回想起来 / 现在想想 / 后来才明白 / 那时候我并不知道 / 多年后 / 如今想来 /
     回头看 / 事后回想 / 那时我不知道 / 后来才发现 / 现在回头看 / 直到很久以后 / 直到后来 /
     我当时哪里知道 / 后来才意识到 / 时至今日 / 多年之后 / 数年后回望。
  3. 计算 hindsight_density_per_1k。
  4. 阈值:回溯模式 hindsight_density < 0.4/千字 → advisory(narrating-self 缺位·
     退化成 experiencing-self 当下时叙述)。

【北极星② / ⑤ 顾问非法官】POV 模式由作者档/cluster brief 声明 · hindsight 是工艺 advisory ·
  code FIRSTPERSON_RETRO_HINDSIGHT_THIN 绝不进 audit_hub.HARD_GATE_CODES 。
  env FIRSTPERSON_RETRO_MODE: off / shadow(默认·只记不判) / active 。

用法:python firstperson_retro_self_gap_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "FIRSTPERSON_RETRO_HINDSIGHT_THIN"

# hindsight 词典(narrating-self 签到标志·回顾视角自报)
HINDSIGHT_MARKERS = re.compile(
    r"(回想起来|现在想想|现在回想|后来才明白|后来才发现|后来才意识到|后来才知道|"
    r"那时候我[并都没]?不知道|那时我[并都没]?不知道|"
    r"多年[之以]?后|多年后回[望首想]|数年[之以]?后|时至今日|如今想来|如今回[望首想]|"
    r"回头看|事后回想|事后想来|直到[很多]?[年久]?[之以]?后|直到后来|"
    r"我当时哪里知道|我那时哪里晓得|那时的我[并不]?知道|如今再想|"
    r"日后[我]?才明白|日后[我]?才知道|往后的日子)"
)

# 回溯模式集合
RETRO_POV_MODES = {"first_retro_consonant", "first_retro_dissonant"}
VALID_POV_MODES = {"first_present", "first_retro_consonant", "first_retro_dissonant",
                   "third_limited", "third_omniscient"}

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("FIRSTPERSON_RETRO_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _resolve_narrative_pov_mode(project_root, cluster_index: int | None = None) -> str | None:
    """读当前 cluster 的 narrative_pov_mode(事件簇.json active cluster · 退作者风格.json)。

    优先级: active cluster (status active) > 作者风格.json.narrative_pov_mode 兜底。
    """
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    # 事件簇.json
    ec = db / "事件簇.json"
    if ec.exists():
        try:
            data = json.loads(ec.read_text(encoding="utf-8"))
            clusters = data.get("clusters") or []
            active_words = {"in_progress", "active", "进行中", "活跃", "current"}
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                status = (c.get("status") or "").strip().lower()
                if status in active_words:
                    npm = c.get("narrative_pov_mode")
                    if isinstance(npm, str) and npm.strip().lower() in VALID_POV_MODES:
                        return npm.strip().lower()
        except (json.JSONDecodeError, OSError):
            pass
    # 作者风格.json 兜底
    ap = db / "作者风格.json"
    if ap.exists():
        try:
            obj = json.loads(ap.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                npm = obj.get("narrative_pov_mode")
                if isinstance(npm, str) and npm.strip().lower() in VALID_POV_MODES:
                    return npm.strip().lower()
        except (json.JSONDecodeError, OSError):
            pass
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "firstperson_retro_self_gap", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    npm = _resolve_narrative_pov_mode(project_root)
    out["narrative_pov_mode"] = npm
    if not npm:
        out["note"] = "无 narrative_pov_mode 声明·跳过(北极星②:作者未标 POV 模式不擅判)"
        return out
    if npm not in RETRO_POV_MODES:
        out["note"] = f"非第一人称回溯(narrative_pov_mode={npm})·跳过"
        return out

    hits = HINDSIGHT_MARKERS.findall(draft)
    per1k = cjk / 1000.0
    density = round(len(hits) / per1k, 3) if per1k > 0 else 0.0
    out["hindsight_marker_count"] = len(hits)
    out["hindsight_density_per_1k"] = density
    out["sample_markers"] = list({h if isinstance(h, str) else h[0] for h in hits})[:8]

    FLOOR = 0.4  # /千字
    msg = None
    if density < FLOOR:
        msg = (f"第一人称回溯 hindsight 签到偏稀:命中 {len(hits)} 处({density}/千字 < {FLOOR})·"
               f"narrative_pov_mode={npm} 需 narrating-self 定期签到(『回想起来』『后来才明白』式)·"
               f"否则退化成 experiencing-self 当下时叙述·失去回溯感")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "firstperson_retro_self_gap", "severity": "minor",
                "message": msg, "density_per_1k": density, "floor": FLOOR,
                "hindsight_count": len(hits), "narrative_pov_mode": npm,
                "_doc": ("回溯感是 first_retro_* 工艺·advisory 可豁免(consonant 模式作者刻意贴近"
                         "experiencing-self·只在关键节点签到)·绝不 hard_gate·"
                         "与 FUTURE_KNOWLEDGE_LEAK 正交(前者查不应有 · 本者查应有不足)")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] firstperson_retro_self_gap: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="第一人称回溯 hindsight 签到检测(advisory · first_retro_* POV 模式)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 narrative_pov_mode 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
