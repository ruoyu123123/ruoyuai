#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""antagonist_fidelity_scanner.py — 反派 safety-alignment 替代扁平化检测（advisory · cluster · 2026-06-20）

【缺口】R7 W2·安全对齐(safety-alignment)研究：RLHF 训练让 LLM 默认不擅长写【操纵性、有信念、
共情危险】的反派——倾向把反派降格为【直白嘲讽 / 冷哼 / 咆哮 / 狂笑】等扁平刻板印象（stereotype
substitution）。结果：反派全员『冷哼一声』『阴森一笑』『狂笑道』『不屑地嗤笑』，失去【操纵
signature / 局部正确性 / 共情点】。CLAUDE.md D1「反派必须有信念驱动」+ D6「角色不当旁白机器」
都没有客观检测端·本 scanner 补检测端。

【做法 · 反派 anti-pattern 词典（高确定性·零 LLM）】：
  ① 直白嘲讽 anti-pattern（直陈情绪 + 单维刻板）：嘲讽道 / 冷哼一声 / 嗤笑 / 不屑 / 阴森一笑 /
     狰狞 / 狂笑 / 咆哮 / 怒吼 / 恶狠狠 / 凶狠地 / 龇牙咧嘴 / 杀气腾腾
  ② 单维情绪标签裸贴：怒不可遏 / 嫉妒得发狂 / 仇恨满满 / 杀意凛然 / 邪恶地
  3 统计 per_1k 这类词命中数·超 floor → advisory「反派可能被 safety-alignment 替代扁平化·
  建议加入 voice_pack.moral_level (L1-L4) + manipulation_signature（操纵手法签名）」

【北极星⑤ 顾问非法官】反派写法是创作选择（爽文反派就该夸张/喜剧反派就该浮夸）·writer 有理由
  可豁免 → 永远 advisory，code ANTAGONIST_FIDELITY_FLAT **绝不进 audit_hub.HARD_GATE_CODES**。
  env ANTAGONIST_FIDELITY_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值占位保守（宁可漏报）·待金标准校准（爽文反派密度真实分布）。

用法：python antagonist_fidelity_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ANTAGONIST_FIDELITY_FLAT"   # ⚠️ advisory 专用

# 反派扁平化 anti-pattern 词典（直陈情绪 + 单维刻板）
# 高确定性反派 substitution 关键词·真操纵性反派会用【话术/局部正确/共情陷阱】而非这些刻板词
FLAT_ANTAGONIST_PATTERNS = re.compile(
    r"(冷哼一声|冷哼|嘲讽道|嘲讽地|嗤笑|不屑地|不屑一顾|阴森一笑|阴森地|"
    r"狰狞地|狰狞一笑|狂笑道|狂笑一声|狂笑|咆哮道|咆哮起来|怒吼道|怒吼一声|"
    r"恶狠狠地|恶狠狠|凶狠地|龇牙咧嘴|杀气腾腾|怒不可遏|嫉妒得发狂|"
    r"仇恨满满|杀意凛然|邪恶地|邪魅一笑|阴险地|阴狠地|歹毒地)"
)

# 单向阈值（保守占位·待金标准校准）
# per_1k 超此 = 反派密集时 substitution 替代扁平化嫌疑
FLAT_FLOOR_PER_1K = 1.0
MIN_FLAT_HITS = 4    # 总命中数低于此 = 不判（cluster 内仅零星出现非系统性问题）

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("ANTAGONIST_FIDELITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_antagonist_signal(project_root):
    """读人物卡看是否有 role=反派 / antagonist 角色。无 → None。仅用于报告增强不影响门控。"""
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    chars = obj.get("characters", []) if isinstance(obj, dict) else []
    antagonists = []
    for c in chars:
        if isinstance(c, dict) and c.get("role") in ("反派", "antagonist", "BBEG", "主反"):
            antagonists.append(c.get("name", ""))
    return antagonists or None


def detect_flat_antagonist(text: str) -> list:
    """反派扁平化 anti-pattern 命中。返回 [{term, pos}]。"""
    text = _strip_changes(text)
    return [{"term": m.group(0), "pos": m.start()} for m in FLAT_ANTAGONIST_PATTERNS.finditer(text)]


def scan(draft_path, project_root=None) -> dict:
    """反派 safety-alignment 替代扁平化检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "antagonist_fidelity", "schema_version": "1.0", "mode": mode,
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

    antagonists = _load_antagonist_signal(project_root)
    out["registered_antagonists"] = antagonists

    hits = detect_flat_antagonist(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["flat_antagonist_count"] = len(hits)
    out["per_1k"] = per_1k
    out["sample_terms"] = sorted({h["term"] for h in hits})[:8]

    if len(hits) < MIN_FLAT_HITS:
        out["note"] = f"反派 anti-pattern 命中样本不足（{len(hits)} < {MIN_FLAT_HITS}）·不判"
        out["violations_count"] = len(out["violations"])
        return out

    msg = None
    if per_1k > FLAT_FLOOR_PER_1K:
        msg = (f"反派扁平化嫌疑（{per_1k}/千字 > {FLAT_FLOOR_PER_1K}·{len(hits)} 处『冷哼/狂笑/嗤笑』式 anti-pattern）·"
               f"可能被 safety-alignment 替代成单维刻板印象·"
               f"建议加 voice_pack.moral_level(L1-L4) + manipulation_signature 改写操纵性反派")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "antagonist_fidelity_flat", "severity": "minor",
                "message": msg, "per_1k": per_1k, "count": len(hits),
                "sample_terms": out["sample_terms"],
                "_doc": "反派写法是创作选择·爽文/喜剧反派夸张可豁免·voice_pack.moral_level/manipulation_signature 工艺指南·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] antagonist_fidelity: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="反派 safety-alignment 替代扁平化检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="读人物卡识别反派(报告增强·不门控)")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
