#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adversarial_judge_pair.py — attacker-defender-arbiter 三角 scaffolding(R19 W8 Batch-Y·P2)

【缺口·2026-06-21·cluster finale 单 judge 易漏 corner】
cluster finale(is_volume_finale)是卷转折点·strong-state shift / 高烈度反派对决·单 judge 易
PASS 漏掉 subtle 弱攻击面. 借鉴 Constitutional AI / debate (Irving et al. 2018) 三角:
  ① attacker  : 对草稿产 K 个 attack point (确定性启发式·针对结构/情感/钩子)
  ② defender  : 对每 attack point 给 defense (草稿原文中找证据反驳)
  ③ arbiter   : tally attack-vs-defense·若 attacker_unanswered >= floor → degraded

【输入】
  - draft_path: cluster finale 草稿
  - judge_report_path: 主 judge 报告
  - cluster_brief: 含 is_volume_finale 标记

【触发条件】cluster_brief.is_volume_finale == True (其他 cluster 直接 skip).

【attacker 模板(确定性·无 LLM·占位)】
  - A1 "钩子结尾"      : 末段 CJK<60 但既有钩子词 / 末段无悬念词 → 弱钩子
  - A2 "情感对位"      : 末段情感 marker 与 brief.expected_emotion 不匹配
  - A3 "副线收束缺失"  : brief.foreshadowing 在草稿末段未提及
  - A4 "权力转移失败"  : brief.power_shift 标的角色在末段无主语动作
  - A5 "代价签收缺失"  : brief.stakes 中关键代价词在草稿全文未现

【北极星⑤】顾问非法官·占位 scaffolding(真 LLM debate defer)·env ADVERSARIAL_JUDGE_PAIR_MODE
  默认 off·全 advisory·ADVERSARIAL_JUDGE_DEGRADED 绝不 hard_gate.

【与既有 scanner 严格正交】
  - meta_critic_audit        : 元批评单视角·正交
  - cluster_evaluator        : 单稿评估·正交
  - parallel_rollout_arbiter : K 稿排序·正交
  - chapter_end_anchor_scan  : 章末钩子·正交(本=cluster finale 三角)

用法: python adversarial_judge_pair.py <draft> --judge-report <path> [--cluster-brief <p>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ADVERSARIAL_JUDGE_DEGRADED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 800
UNANSWERED_FLOOR = 3   # ≥3 个 attack 未被反驳 → degraded

# 占位 attacker 词典(真正攻击面用 LLM debate, 这里启发式)
HOOK_WORDS = ("可", "却", "竟", "突", "原来", "再", "又", "然而", "悬念",
              "等等", "下一", "未完", "戛然")
SUSPENSE_WORDS = ("?", "？", "...", "……", "！", "!", "莫非", "难道")
STAKE_WORDS = ("代价", "牺牲", "失去", "断", "死", "毁", "崩")


def _mode() -> str:
    m = (os.environ.get("ADVERSARIAL_JUDGE_PAIR_MODE") or "off").strip().lower()
    return m if m in ("off", "shadow", "active") else "off"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_brief(project_root, cluster_brief_path):
    data = None
    if cluster_brief_path and Path(cluster_brief_path).exists():
        try:
            data = json.loads(Path(cluster_brief_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        p = Path(project_root) / "_数据库" / "事件簇.json"
        if p.exists():
            try:
                ec = json.loads(p.read_text(encoding="utf-8"))
                clusters = (ec or {}).get("clusters") or []
                if clusters:
                    data = clusters[0]
            except (OSError, json.JSONDecodeError):
                data = None
    return data if isinstance(data, dict) else None


def _last_paragraph(text):
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras[-1] if paras else ""


def attack(text: str, brief: dict) -> list:
    """5 个启发式 attack point. brief 缺字段 → 该 attack 跳过."""
    last_para = _last_paragraph(text)
    attacks = []

    # A1 弱钩子
    if last_para:
        has_hook = any(w in last_para for w in HOOK_WORDS)
        has_susp = any(w in last_para for w in SUSPENSE_WORDS)
        if _cjk_count(last_para) < 60 and not (has_hook and has_susp):
            attacks.append({"id": "A1", "name": "weak_hook",
                            "claim": f"末段 CJK<{_cjk_count(last_para)}<60 且钩子词/悬念词不全",
                            "target": last_para[:200]})

    # A2 情感对位
    exp_emo = brief.get("expected_emotion") or brief.get("emotion_target")
    if isinstance(exp_emo, str) and last_para and exp_emo not in last_para:
        attacks.append({"id": "A2", "name": "emotion_mismatch",
                        "claim": f"brief.expected_emotion={exp_emo!r} 未在末段出现",
                        "target": last_para[:200]})

    # A3 副线收束
    fores = brief.get("foreshadowing") or brief.get("foreshadowing_to_plant") or []
    if isinstance(fores, list):
        for f in fores[:3]:
            tag = f.get("tag") if isinstance(f, dict) else (f if isinstance(f, str) else None)
            if tag and last_para and tag not in last_para:
                attacks.append({"id": "A3", "name": "foreshadow_not_paid_in_finale",
                                "claim": f"foreshadow tag={tag!r} 末段未现",
                                "target": tag})

    # A4 权力转移
    ps = brief.get("power_shift") or {}
    if isinstance(ps, dict):
        actor = ps.get("actor") or ps.get("from")
        if actor and last_para and actor not in last_para:
            attacks.append({"id": "A4", "name": "power_shift_actor_absent",
                            "claim": f"power_shift.actor={actor!r} 末段无主语动作",
                            "target": actor})

    # A5 代价签收
    stakes = brief.get("stakes") or []
    if isinstance(stakes, list):
        for st in stakes[:2]:
            keyword = st.get("keyword") if isinstance(st, dict) else (
                st if isinstance(st, str) else None)
            if keyword and keyword not in text:
                attacks.append({"id": "A5", "name": "stake_keyword_missing",
                                "claim": f"stake keyword={keyword!r} 全稿未现",
                                "target": keyword})
    return attacks


def defend(attacks: list, text: str) -> list:
    """对每 attack 查证据反驳. 启发式: 在草稿中能找到 attack.target 周边 ±200 字 → 反驳."""
    defenses = []
    for a in attacks:
        target = a.get("target") or ""
        answered = False
        evidence = ""
        if target:
            idx = text.find(target[:20]) if len(target) >= 5 else -1
            if idx >= 0:
                # 找到了 target 字串证据
                start = max(0, idx - 100)
                end = min(len(text), idx + len(target) + 100)
                evidence = text[start:end][:200]
                answered = True
        defenses.append({"attack_id": a.get("id"), "answered": answered,
                         "evidence": evidence})
    return defenses


def arbitrate(attacks: list, defenses: list) -> dict:
    by_aid = {d["attack_id"]: d for d in defenses if isinstance(d, dict)}
    unanswered = []
    for a in attacks:
        aid = a.get("id")
        d = by_aid.get(aid) or {}
        if not d.get("answered"):
            unanswered.append({"id": aid, "name": a.get("name"),
                               "claim": a.get("claim")})
    return {"unanswered_count": len(unanswered), "unanswered": unanswered,
            "total_attacks": len(attacks)}


def scan(draft_path, judge_report_path=None, project_root=None,
         cluster_brief_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "adversarial_judge_pair", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "attacks": [], "defenses": [], "verdict_pair": None,
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        out["note"] = "off·skip"
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    brief = _load_brief(project_root, cluster_brief_path)
    if brief is None:
        out["note"] = "无 cluster brief·skip"
        return out
    if not brief.get("is_volume_finale"):
        out["note"] = "非 volume_finale·skip"
        return out

    atks = attack(text, brief)
    defs = defend(atks, text)
    res = arbitrate(atks, defs)
    out["attacks"] = atks
    out["defenses"] = defs
    out["verdict_pair"] = res

    if res["unanswered_count"] >= UNANSWERED_FLOOR:
        msg = (f"adversarial debate degraded·{res['unanswered_count']}/{res['total_attacks']} "
               f"attack 未被反驳(≥{UNANSWERED_FLOOR})")
        if mode == "active":
            out["violations"].append({
                "kind": "adversarial_judge_pair", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "verdict_pair": res,
                "_doc": "R19 W8 Batch-Y·P2·三角 scaffolding·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] adversarial_judge_pair[{ISSUE_CODE}]: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="R19 W8 Batch-Y·P2·attacker-defender-arbiter 三角·cluster finale·advisory·off")
    ap.add_argument("draft_path")
    ap.add_argument("--judge-report", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-brief", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.judge_report, args.project, args.cluster_brief)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
