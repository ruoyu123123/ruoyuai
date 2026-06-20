#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rule_text_ambiguity_scanner.py — 规则块字面歧义/陷阱条款比 (advisory · cluster · 2026-06-20)

【缺口】R7 联网调研: 番茄爆款『规则类怪谈无限流』(千万订阅·与用户《这规则它会还价》同构)。
此前规则块全直陈=工具书读感, 系统零检测。本 scanner 补:规则块条款是否带歧义/陷阱条款
信号（半真半假规则=题材核心工艺）。

【做法 · 确定性纯规则正则(不依赖 LLM)】:
  1. 规则块识别:连续 ≥2 条编号项(1./2./3./一、二、三、）或开头含「规则/守则/注意事项/条款」。
  2. 每条款检测两类信号:
     - 歧义标志:或许 / 也许 / 据说 / 不一定 / 可能 / 除非 / 但是 / 若...则 / 如果...则
     - 陷阱标志:不可 / 禁止 / 切勿 / 千万 / 否则 / 便会 / 立刻 / 必死 / 失踪
  3. 含任一信号的条款 = 半真半假条款; 全直陈 = 工具书条款。
  4. 计算 ambiguity_ratio = 半真半假条款数 / 总条款数。
  5. genre_gated: 仅 genre=rule_anomaly 激活; 其他题材 skip(规则块在其他题材常合法直陈)。

【北极星② / ⑤ 顾问非法官】题材声明由作者档/用户偏好决定 · 规则歧义是工艺 advisory ·
  code RULE_TEXT_AMBIGUITY_LOW 绝不进 audit_hub.HARD_GATE_CODES 。
  env RULE_TEXT_AMBIGUITY_MODE: off / shadow(默认·只记不判) / active 。

用法:python rule_text_ambiguity_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "RULE_TEXT_AMBIGUITY_LOW"   # ⚠️ advisory 专用·绝不进 HARD_GATE_CODES

# 歧义信号(留疑/留漏洞·让读者权衡条款真意)
AMBIGUITY_MARKERS = re.compile(
    r"(或许|也许|据说|不一定|可能|大概|似乎|大约|未必|除非|但是|然而|不过|"
    r"若.{0,8}则|如果.{0,8}则|要是.{0,8}则|一旦.{0,8}就)"
)
# 陷阱信号(死亡/惩罚式条款·提示后果)
TRAP_MARKERS = re.compile(
    r"(不可|不得|禁止|切勿|千万不要|千万别|否则|便会|立刻|必死|"
    r"将会消失|将会被|失踪|惨死|发疯|永远困|无法离开)"
)

# 规则块块头标志(任一命中即认为后续编号项构成规则块)
RULE_BLOCK_HEADER = re.compile(
    r"(规则|守则|注意事项|条款|须知|安全须知|须遵守|须知如下)[:：]"
)

# 编号条款行(中英文阿拉伯/汉数字编号 + 顿号/点/反括号)
NUMBERED_ITEM = re.compile(
    r"^\s*(?:(?:[0-9]{1,2})[\.\、\)\）]|(?:[一二三四五六七八九十]{1,3})[\、\.\)\）]|(?:[（(])[0-9]{1,2}[)\）])\s*(.+)$"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("RULE_TEXT_AMBIGUITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _resolve_genre(project_root) -> str | None:
    """读 genre(作者风格.json genre_tags[0] · 退用户偏好.json genre)。无 → None。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path, key in [(db / "作者风格.json", "genre_tags"),
                       (db / "用户偏好.json", "genre")]:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get(key)
        if isinstance(v, list) and v:
            return str(v[0]).strip().lower()
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _extract_rule_items(text: str) -> list:
    """从正文里抽『规则块条款』(连续编号项·块头加分)。返回 [item_text, ...]。

    策略:把所有编号项收集进 candidate · 若全文出现 RULE_BLOCK_HEADER 则放宽门槛(任意编号项)
    · 若无 header 则要求至少 2 条连续编号项(防误抓段落标号)。
    """
    text = _strip_changes(text)
    lines = text.split("\n")
    items = []
    candidates = []  # [(line_index, item_text), ...]
    for i, ln in enumerate(lines):
        m = NUMBERED_ITEM.match(ln)
        if m:
            candidates.append((i, m.group(1).strip()))

    has_header = bool(RULE_BLOCK_HEADER.search(text))
    if not candidates:
        return []

    if has_header:
        # 块头存在 → 收所有编号项
        return [t for _, t in candidates]

    # 无块头 → 仅收『连续 ≥2 条』的段
    # 连续 = 行号相邻(差距 ≤2 容空行/缩进续行)
    groups = []
    cur = [candidates[0]]
    for j in range(1, len(candidates)):
        if candidates[j][0] - candidates[j-1][0] <= 2:
            cur.append(candidates[j])
        else:
            if len(cur) >= 2:
                groups.append(cur)
            cur = [candidates[j]]
    if len(cur) >= 2:
        groups.append(cur)
    for g in groups:
        items.extend(t for _, t in g)
    return items


def _classify_item(item: str) -> dict:
    """单条款分类:命中歧义/陷阱标志即半真半假。"""
    has_amb = bool(AMBIGUITY_MARKERS.search(item))
    has_trap = bool(TRAP_MARKERS.search(item))
    return {
        "text": item[:80],
        "has_ambiguity": has_amb,
        "has_trap": has_trap,
        "is_half_true": has_amb or has_trap,
    }


def scan(draft_path, project_root=None) -> dict:
    """规则块字面歧义检测。永远 advisory。"""
    mode = _mode()
    out = {"scanner": "rule_text_ambiguity", "schema_version": "1.0", "mode": mode,
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

    genre = _resolve_genre(project_root)
    out["genre"] = genre
    if genre != "rule_anomaly":
        out["note"] = f"非 rule_anomaly 题材(genre={genre})·跳过"
        return out

    items = _extract_rule_items(draft)
    out["rule_items_total"] = len(items)
    if len(items) < 2:
        out["note"] = "未检测到规则块条款(<2 条)·跳过"
        return out

    classified = [_classify_item(it) for it in items]
    half_true = sum(1 for c in classified if c["is_half_true"])
    ratio = round(half_true / len(items), 3)
    out["half_true_count"] = half_true
    out["ambiguity_ratio"] = ratio
    out["sample_items"] = classified[:8]

    # 阈值:< 0.30 = 太直陈缺歧义(题材核心工艺缺失)
    THRESHOLD = 0.30
    msg = None
    if ratio < THRESHOLD:
        msg = (f"规则块字面歧义偏低:{half_true}/{len(items)} 条带歧义/陷阱信号 "
               f"({ratio:.2f} < {THRESHOLD})·rule_anomaly 题材规则忌全直陈(工具书读感)·"
               f"建议规则条款掺『或许/不一定/除非』式留疑 + 『不可X·否则Y』式陷阱搭配")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "rule_text_ambiguity", "severity": "minor",
                "message": msg, "ratio": ratio, "threshold": THRESHOLD,
                "half_true_count": half_true, "total_items": len(items),
                "_doc": "规则歧义是 rule_anomaly 工艺·advisory 可豁免(作者偏向工具书风格)·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] rule_text_ambiguity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="规则块字面歧义/陷阱条款比(advisory · rule_anomaly 题材)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 genre 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
