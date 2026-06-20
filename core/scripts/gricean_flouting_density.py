#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gricean_flouting_density.py — Gricean 四准则 flouting 潜台词密度 (advisory · cluster · 2026-06-20 R8 W4 L21)

【缺口】R8 联网调研 (Media Horizons 2025 戏剧 flouting · JELTAL Cooperative Principle &
maxims · Lakin《高手对话在没说什么》)：好对话的潜台词来自 flouting Grice 四准则 (Quality/
Quantity/Relation/Manner)·LLM 默认产「准则全合作的中性对话」=工艺扁平·零潜台词。

【做法 · 确定性纯规则正则 (不依赖 LLM)】：

  四子检测器 (每个对话回合 turn 独立判·任一 flout 则计数)：

    Quality (说违心话)
      - 强联动作者档/资料库：character_secrets.json 列出每角色「不可说真相」·若资料缺则
        降级 skip 子项 (零侵入)·此处仅做轻量代理信号「明显反讽词」(才怪/真是的/呵呵/省得我)。

    Quantity (说太少/太多)
      - 简单字数比：turn 字数 < 4 (字短·言不尽意) 或 > 80 (过度铺陈言外有意)·
        计入 quantity flouts。

    Relation (跳话题)
      - 词表：『说回正事』『扯远了』『不说这个』『不提了』『换个话题』『说点别的』
      - 反向 (主动跳走)：『反正』『话说回来』『对了』(开 turn) ·命中即标 flouting。

    Manner (含混)
      - 含混词表：也许 / 或许 / 再说吧 / 到时候看 / 看情况 / 难说 / 谁知道 / 你猜 /
        反正 / 一言难尽。
      - 反讽标记：才怪 / 我看未必 / 呵呵 / 啧 (口语反讽)。

  聚合 flouting_per_dialogue_turn = 4 子项联合 (任一即计一次) / turn 总数。

【作者档基线】：
  unreliable_narrator_profile / dialogue_flouting_profile 提供 expected_flouting_min。
  无作者档：通用阈值 0.15 (即 15% turn 至少一次 flouting·官场/心理可拉高)。
  manifest dialogue.expected_flouting_min 可覆盖 (writer 注入冲突 cluster 时调高)。

【北极星② / ⑤ 顾问非法官】对话工艺由作者档决定 · flouting 是工艺 advisory ·
  code GRICEAN_FLOUTING_THIN 绝不进 audit_hub.HARD_GATE_CODES。
  env GRICEAN_FLOUTING_MODE: off / shadow (默认·只记不判) / active。

【与 R6 OIR / 延迟解码 D2 正交】：
  - OIR (other-initiated repair) 查「澄清-修复」次邻对结构
  - D2 延迟解码查叙述者向读者的延迟信息
  - 本 scanner 查对话内单 turn 的 Grice 准则违反 (合作原则下生潜台词)
  - 三者并存不冲突

用法：python gricean_flouting_density.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "GRICEAN_FLOUTING_THIN"

# Quality 子检测器 (反讽 / 违心话代理词·宁可漏报)
QUALITY_IRONIC_MARKERS = re.compile(
    r"(才怪|真是的|呵呵|哼|啧|我看未必|是吗[?？]|得了吧|我看你是|说得倒[轻好]|"
    r"你倒挺会说|你倒[是好]意思)"
)

# Relation 子检测器
RELATION_JUMP_MARKERS = re.compile(
    r"(说回正事|扯远了|不说这个|不提了|换个话题|说点别的|跑题了|"
    r"先按下不提|这事不提|不提这茬)"
)
# 开口转移标记 (turn 开头出现 → 主动跳走)
RELATION_OPEN_DEFLECT = re.compile(
    r"^[,\s,。.！!？?]*(反正|话说回来|对了|嗯[，,]?|那[，,]?|话又说回来|算了)"
)

# Manner 子检测器
MANNER_HEDGE_MARKERS = re.compile(
    r"(也许|或许|再说吧|到时候看|看情况|难说|谁知道|你猜|"
    r"一言难尽|不太好说|说不准|看着办)"
)

# Quantity 阈值 (turn 字数)
QUANTITY_TOO_SHORT = 4
QUANTITY_TOO_LONG = 80

# 对话提取（中文左右双引号 + 直引号兜底）
DIALOGUE_PATTERN = re.compile(r"[“”‘’\"][^“”‘’\"\n]+?[“”‘’\"]")
# 更鲁棒：成对 “…”
PAIRED_QUOTES = re.compile(r"“([^“”\n]+?)”")
SINGLE_QUOTES = re.compile(r"‘([^‘’\n]+?)’")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("GRICEAN_FLOUTING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_expected_flouting_min(project_root) -> float | None:
    """读作者档 dialogue_flouting_profile.expected_flouting_min。无 → None (用通用阈值)。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    prof = obj.get("dialogue_flouting_profile") or {}
    if isinstance(prof, dict):
        v = prof.get("expected_flouting_min")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return None


def extract_dialogue_turns(text: str) -> list:
    """提取对话 turn (paired “…” 优先·single ‘…’ 兜底)。返回 [turn_text, ...]。"""
    turns = []
    for m in PAIRED_QUOTES.finditer(text):
        t = m.group(1).strip()
        if t:
            turns.append(t)
    for m in SINGLE_QUOTES.finditer(text):
        t = m.group(1).strip()
        if t:
            turns.append(t)
    return turns


def classify_turn(turn: str) -> dict:
    """单 turn 四子检测器分类。返回 4 个布尔位。"""
    q_hit = bool(QUALITY_IRONIC_MARKERS.search(turn))
    cjk_len = _cjk_count(turn)
    qty_hit = (cjk_len > 0) and (cjk_len < QUANTITY_TOO_SHORT or cjk_len > QUANTITY_TOO_LONG)
    rel_hit = bool(RELATION_JUMP_MARKERS.search(turn)) or bool(RELATION_OPEN_DEFLECT.search(turn))
    man_hit = bool(MANNER_HEDGE_MARKERS.search(turn))
    any_flout = q_hit or qty_hit or rel_hit or man_hit
    return {
        "quality": q_hit,
        "quantity": qty_hit,
        "relation": rel_hit,
        "manner": man_hit,
        "any_flout": any_flout,
        "cjk_len": cjk_len,
    }


def scan(draft_path, project_root=None) -> dict:
    """Gricean flouting 潜台词密度检测。永远 advisory。"""
    mode = _mode()
    out = {
        "scanner": "gricean_flouting_density",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "warning": None,
        "violations": [],
        "verdict": "PASS",
    }
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

    turns = extract_dialogue_turns(draft)
    out["dialogue_turn_total"] = len(turns)
    if len(turns) < 4:
        out["note"] = f"对话 turn 数太少 ({len(turns)} < 4)·跳过"
        return out

    classified = [classify_turn(t) for t in turns]
    quality_n = sum(1 for c in classified if c["quality"])
    quantity_n = sum(1 for c in classified if c["quantity"])
    relation_n = sum(1 for c in classified if c["relation"])
    manner_n = sum(1 for c in classified if c["manner"])
    any_flout_n = sum(1 for c in classified if c["any_flout"])
    ratio = round(any_flout_n / len(turns), 3) if turns else 0.0

    out["flouting_per_dialogue_turn"] = ratio
    out["quality_flouts"] = quality_n
    out["quantity_flouts"] = quantity_n
    out["relation_flouts"] = relation_n
    out["manner_flouts"] = manner_n
    out["any_flout_count"] = any_flout_n

    floor = _read_expected_flouting_min(project_root)
    floor_source = "author_profile"
    if floor is None:
        floor = 0.15  # 通用兜底（15% turn 至少一次 flouting）
        floor_source = "default_fallback"
    out["expected_flouting_min"] = floor
    out["expected_floor_source"] = floor_source

    msg = None
    if ratio < floor:
        msg = (
            f"对话 flouting 密度偏低: {any_flout_n}/{len(turns)} turn 含 Grice 准则违反 "
            f"({ratio:.2f} < 阈值 {floor:.2f}·source={floor_source})·"
            f"准则全合作 → 对话扁平/零潜台词·"
            f"分项 quality={quality_n} quantity={quantity_n} relation={relation_n} manner={manner_n}·"
            f"建议加反讽/含混/跳题/答非所问"
        )
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "gricean_flouting_density",
                "severity": "minor",
                "message": msg,
                "flouting_per_dialogue_turn": ratio,
                "floor": floor,
                "floor_source": floor_source,
                "dialogue_turn_total": len(turns),
                "category_counts": {
                    "quality": quality_n, "quantity": quantity_n,
                    "relation": relation_n, "manner": manner_n,
                },
                "_doc": (
                    "Gricean flouting 是对话工艺 advisory·官场/心理类基线高·爽文低·"
                    "作者偏好直陈风格可豁免·绝不 hard_gate·与 R6 OIR + D2 延迟解码正交"
                ),
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow
            print(f"[SHADOW] gricean_flouting_density: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Gricean 四准则 flouting 潜台词密度 (advisory · cluster)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 dialogue_flouting_profile 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
