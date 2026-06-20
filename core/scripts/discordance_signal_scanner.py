#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discordance_signal_scanner.py — Booth Discordance 4-cue 反讽信号包(advisory · cluster · 2026-06-20 R8 W4 Batch-I)

【缺口】L28 联网调研(Booth Rhetoric of Irony stable irony 4 步 + Phelan Ideal Narratee
Poetics Today 2022 + Tang arXiv:2209.04712 verbal irony): 稳定反讽(stable irony)的 4 种
discordance(讽刺信号)·LLM 默认产直白叙述=反讽零信号。题材如黑色幽默/官场/讽刺小说
缺这一层质感。此前**全系统零检测**。

【做法 · 确定性纯规则正则】4 子检测器:
  1. saying_doing(说做不一·言行反差):同段内
     『嘴上说...心里却』『一边...一边』『口口声声...实际上』式语义反向
  2. style_fact(语体事实错配):宏大/夸张词配琐碎事实
     『英雄般地...买菜』『庄严宣告...泡面』
  3. world_clash(世界观冲撞):神圣词配世俗污渍 / 古风词配现代物
     已部分被 R8 L18 world_register_drift 覆盖·本 scanner 只查贴近词组
  4. value_clash(价值观冲撞):正面词反向使用
     『真是个好人』(语境明显在骂)·『天才操作』式反讽叹号

  聚合 discordance_per_1k = (saying_doing + style_fact + world_clash + value_clash) / 千字
  作者档 ironic_voice_profile.stable_irony=True + discordance_target 第一权威·
    通用兜底:无作者档则 skip(北极星②:作者没声明反讽风格别擅判)

【北极星② / ⑤ 顾问非法官】advisory · 与 L25 metalepsis 关联防双计(narratee 越界单独算) ·
  code DISCORDANCE_SIGNAL_THIN 绝不进 audit_hub.HARD_GATE_CODES。
  env DISCORDANCE_MODE: off / shadow(默认) / active。

用法: python discordance_signal_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "DISCORDANCE_SIGNAL_THIN"

# 1. saying_doing: 言行反差
SAYING_DOING = re.compile(
    r"(嘴(上|里)?说[^,，。]{1,20}心(里|中)?(却|偏偏|实则)|"
    r"一边[^,，。]{1,15}一边[^,，。]{1,15}|"
    r"口口声声[^,，。]{1,20}(实际|实则|背后|私下)|"
    r"表面上[^,，。]{1,15}(实际|背地|心里))"
)
# 2. style_fact: 语体错配(夸张词 + 琐碎事实)
STYLE_FACT = re.compile(
    r"(英雄(般|似)地[^,，。]{1,15}(买|吃|喝|睡|拉|蹲)|"
    r"庄严(地|的)?宣告[^,，。]{1,15}(泡面|外卖|早餐|午饭)|"
    r"神圣(地|的)[^,，。]{1,15}(刷|擦|扫|洗)|"
    r"史诗(级|般)的[^,，。]{1,15}(下班|加班|挤地铁|排队))"
)
# 3. world_clash 局部短语(与 L18 register_drift 互补·只查贴近反讽搭配)
WORLD_CLASH = re.compile(
    r"(神圣[^,，。]{1,8}(污渍|脏|垃圾)|"
    r"陛下[^,，。]{1,10}(手机|微信|外卖)|"
    r"贫道[^,，。]{1,10}(上班|加班|996))"
)
# 4. value_clash: 反向使用正面词 + 反讽语气标志
VALUE_CLASH = re.compile(
    r"(真是(个|位)?好(人|官|领导)[^!?！？]{0,15}[!?！？]|"
    r"(天才|高明|了不起)的(操作|主意|想法)[!?！？]|"
    r"多(亏|谢)了[^,，。]{1,15}(救命|帮忙|提醒)|"
    r"呵呵[!?！？]?|"
    r"我谢谢你|你可真行)"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("DISCORDANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_ironic_voice_profile(project_root):
    """读 ironic_voice_profile。无 → None(skip·北极星②)。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path in [db / "作者风格.json", db / "用户偏好.json"]:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        prof = obj.get("ironic_voice_profile")
        if isinstance(prof, dict):
            return prof
    return None


def count_cues(text: str) -> dict:
    return {
        "saying_doing": len(SAYING_DOING.findall(text)),
        "style_fact": len(STYLE_FACT.findall(text)),
        "world_clash": len(WORLD_CLASH.findall(text)),
        "value_clash": len(VALUE_CLASH.findall(text)),
    }


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "discordance_signal", "schema_version": "1.0", "mode": mode,
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

    profile = _read_ironic_voice_profile(project_root)
    out["ironic_voice_profile"] = profile
    if not profile or not profile.get("stable_irony"):
        out["note"] = ("无 ironic_voice_profile.stable_irony=true·skip"
                       "(北极星②:作者未声明稳定反讽风格不擅判)")
        return out

    cues = count_cues(draft)
    total = sum(cues.values())
    per1k = round(total / (cjk / 1000.0), 3)
    out["cue_counts"] = cues
    out["discordance_total"] = total
    out["discordance_per_1k"] = per1k

    target = profile.get("discordance_target")
    if not isinstance(target, (int, float)):
        target = 1.0  # 兜底:稳定反讽 ≥1.0/千字
    out["target_per_1k"] = target

    msg = None
    if per1k < target * 0.5:
        msg = (f"稳定反讽 discordance 信号稀薄: {per1k}/千字 < target {target} × 0.5 = {target*0.5}"
               f"·四 cue 分布 {cues}·建议加 saying_doing/style_fact/value_clash 反讽搭配")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "discordance_signal", "severity": "minor",
                "message": msg, "discordance_per_1k": per1k,
                "target_per_1k": target, "cue_counts": cues,
                "_doc": "Booth stable irony 4-cue·题材工艺 advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] discordance_signal: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Booth Discordance 4-cue 反讽信号(advisory)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 ironic_voice_profile")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
