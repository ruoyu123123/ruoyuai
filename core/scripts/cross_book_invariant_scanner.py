#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_book_invariant_scanner.py — Sanderson Laws 跨书硬规则不变量(R19 W8 Batch-X·P1)

【缺口·2026-06-21·Sanderson Laws of Magic + Will Wight Cradle 系列文】跨书系列文
中早期建立的「magic_system_invariant」(力量规则/世界硬法/词条/形而上)在续作里被
LLM 静默违反 → 系统失自治。需要 cross-book invariant ledger + NLI 兜底比对。

【数据契约】
  workspace/styles/<series>/magic_invariants.json
    {
      "schema_version": 1,
      "_placeholder": false,
      "invariants": [
        {"invariant_id": "I001", "rule_text": "金丹境不能跨界传送",
         "scope": "main_world", "coverage_books": ["凡人修仙传"],
         "source_quotes": ["…"], "severity": "advisory"},
        ...
      ]
    }

【探针·字面启发式 + NLI 补充证据（2026-07-03）】
  主判定仍是「字面规则关键词命中 + 否定/反义词触发」启发式（breach 判定逻辑不变）：
  - 抽 rule_text 关键词 set·扫草稿正文找 contradicts_kw（否定/反义/越界标志）
  - 命中 +20 字窗口 → CROSS_BOOK_INVARIANT_BREACH advisory（仅 advisory·北极星⑤）
  桥（`nn_nli_bridge.py`·IDEA-CCNL/Erlangshen-Roberta-110M-NLI）可用时，对已命中的 hit
  额外批量跑一次 NLI(window, rule_text) 蕴含推理，contradiction 高置信 → 附加 `nli_evidence`
  字段佐证（单批 subprocess 调用·不逐 hit spawn）。桥不可用/关闭/异常 → breaches 100% 原字面
  逻辑不变——NLI 只加字段不改判定，零回归。

【与既有 scanner 严格正交】
  - locked_fact_cross_scene  : 单本场景一致性·正交(本=跨书宇宙)
  - future_knowledge_leak    : 信息时序·正交
  - motif_recurrence         : 母题召回·正交
  - cross_book_rank_scarcity : 顶阶密度·正交(本=硬规则·彼=数值阶梯)

【北极星⑤】顾问非法官·全 advisory·env CROSS_BOOK_INVARIANT_MODE 默认 shadow·
  CROSS_BOOK_INVARIANT_BREACH 绝不 hard_gate·无 ledger → skip(非系列文)·
  build_manifest 注入 top-K(默认 5) 相关 invariant hint。

【distill 抽 hard_law】distill-style step 7.5 + author profile 可调用
  extract_hard_law_from_profile() 把作者档 magic_system_invariants 段拷贝进 ledger。

用法: python cross_book_invariant_scanner.py <draft> [--series <styles_path>] [--top-k 5]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CROSS_BOOK_INVARIANT_BREACH"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 600
DEFAULT_TOP_K = 5
WINDOW_RADIUS = 40  # 命中规则关键词后扫描 ±40 字窗口找否定/反义
NLI_CONTRADICTION_THRESHOLD = 0.6  # 🔴 2026-07-03 NLI 补充证据高置信阈值（3 分类·非校准值·经验保守取值）

# 否定/越界/反义标志词（简化占位 · 真 NLI defer）
_NEGATION_TOKENS = (
    "不能", "无法", "未能", "不会", "不可能",
    "禁止", "违反", "越界", "跨过", "破解",
    "打破", "颠覆", "推翻", "无视", "绕过",
    "可以", "竟然", "居然", "突破", "破例",
)


def _mode() -> str:
    m = (os.environ.get("CROSS_BOOK_INVARIANT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_ledger(series_path):
    """读 workspace/styles/<series>/magic_invariants.json·失败/不存在返回 None。"""
    if not series_path:
        return None
    p = Path(series_path)
    if p.is_dir():
        p = p / "magic_invariants.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _extract_keywords(rule_text: str):
    """从 rule_text 抽 2-字以上的 CJK 关键词·过滤虚词 stoplist。"""
    stop = {"不能", "可以", "需要", "可能", "无法", "必须", "因为",
            "所以", "如果", "那么", "就是", "或者", "但是", "然而"}
    # 简化：连续 CJK 切 2-4 字 ngram
    chunks = re.findall(r"[一-鿿]{2,}", rule_text)
    out = []
    for c in chunks:
        if c in stop:
            continue
        if len(c) <= 4:
            out.append(c)
        else:
            # 长串切 2 字滑窗
            for i in range(len(c) - 1):
                w = c[i:i + 2]
                if w not in stop:
                    out.append(w)
    return list(dict.fromkeys(out))[:8]


def _find_breach(text: str, rule: dict):
    """对一条 invariant 找 breach 命中证据。

    启发式：rule 关键词命中 + ±窗口内出现 negation/越界 token → breach。
    """
    rule_text = rule.get("rule_text") or ""
    kws = _extract_keywords(rule_text)
    if not kws:
        return None
    hits = []
    for kw in kws:
        idx = 0
        while True:
            j = text.find(kw, idx)
            if j < 0:
                break
            window = text[max(0, j - WINDOW_RADIUS): j + WINDOW_RADIUS]
            for neg in _NEGATION_TOKENS:
                if neg in window:
                    hits.append({"keyword": kw, "neg": neg,
                                 "snippet": window.strip()[:120]})
                    break
            idx = j + len(kw)
    if not hits:
        return None
    return {
        "invariant_id": rule.get("invariant_id"),
        "rule_text": rule_text[:200],
        "hits": hits[:3],
    }


def _nli_augment_breaches(breaches: "list[dict]") -> None:
    """🔴 2026-07-03 NLI 补充证据（advisory·原地追加字段·绝不改变已判定的 breach 集合）。

    对已由字面否定词启发式确认的每条 hit，单批跑一次 NLI(window, rule_text) 蕴含推理
    （不逐 hit spawn 子进程·摊薄模型加载开销），contradiction 高置信时附加 `nli_evidence`
    字段佐证。桥不可用/关闭/异常/条数不符 → breaches 原样不变（100% 原字面逻辑·零回归）。
    """
    try:
        import nn_nli_bridge
    except ImportError:
        return
    if not nn_nli_bridge.enabled():
        return
    hits_flat: "list[dict]" = []
    candidates: "list[dict]" = []
    for b in breaches:
        rule_text = b.get("rule_text", "")
        for h in (b.get("hits") or []):
            hits_flat.append(h)
            candidates.append({"premise": h.get("snippet", ""), "hypothesis": rule_text})
    if not candidates:
        return
    try:
        results = nn_nli_bridge.predict_batch(candidates)
    except Exception:  # noqa: BLE001 — advisory 佐证，任何异常都不影响已判定的 breach
        return
    if not results or len(results) != len(hits_flat):
        return
    for hit, res in zip(hits_flat, results):
        if not res:
            continue
        contradiction_p = (res.get("probs") or {}).get("contradiction", 0.0)
        if res.get("label") == "contradiction" and contradiction_p >= NLI_CONTRADICTION_THRESHOLD:
            hit["nli_evidence"] = {"label": res["label"], "contradiction_prob": contradiction_p}


def collect_invariant_hint(series_path, top_k: int = DEFAULT_TOP_K):
    """build_manifest 注入入口·返回 top-K 相关 invariant hint。

    占位排序：按 ledger 顺序前 K 条（真 embedding 相关性 defer）。
    """
    ledger = _load_ledger(series_path)
    if not isinstance(ledger, dict):
        return None
    invs = ledger.get("invariants") or []
    if not isinstance(invs, list) or not invs:
        return None
    picked = []
    for r in invs[:top_k]:
        if not isinstance(r, dict):
            continue
        picked.append({
            "invariant_id": r.get("invariant_id"),
            "rule_text": (r.get("rule_text") or "")[:200],
            "scope": r.get("scope"),
            "coverage_books": r.get("coverage_books") or [],
        })
    return {
        "_doc": "R19 W8 Batch-X·magic_system_invariant hint·writer 写跨书续作需保持硬规则",
        "invariants": picked,
        "_placeholder_nli": True,
    }


def extract_hard_law_from_profile(profile_path):
    """distill-style step 7.5·从 author profile 抽 magic_system_invariants 段。

    返回 list[invariant dict]·空则空列表。
    """
    p = Path(profile_path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    laws = []
    raw = (data.get("worldview") or {}).get("magic_system_invariants") or []
    if isinstance(raw, list):
        for i, x in enumerate(raw, 1):
            if isinstance(x, dict) and x.get("rule_text"):
                laws.append({
                    "invariant_id": x.get("invariant_id") or f"I{i:03d}",
                    "rule_text": x.get("rule_text"),
                    "scope": x.get("scope") or "main_world",
                    "coverage_books": x.get("coverage_books") or [],
                    "source_quotes": x.get("source_quotes") or [],
                    "severity": "advisory",
                })
            elif isinstance(x, str):
                laws.append({
                    "invariant_id": f"I{i:03d}",
                    "rule_text": x,
                    "scope": "main_world",
                    "coverage_books": [],
                    "source_quotes": [],
                    "severity": "advisory",
                })
    return laws


def scan(draft_path, series_path=None, top_k: int = DEFAULT_TOP_K) -> dict:
    mode = _mode()
    out = {"scanner": "cross_book_invariant", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
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

    ledger = _load_ledger(series_path)
    if not isinstance(ledger, dict):
        out["note"] = "无 magic_invariants.json·skip(非系列文/未配置)"
        return out
    invs = ledger.get("invariants") or []
    if not isinstance(invs, list) or not invs:
        out["note"] = "ledger 为空·skip"
        return out

    breaches = []
    for r in invs:
        if not isinstance(r, dict):
            continue
        b = _find_breach(text, r)
        if b:
            breaches.append(b)

    out["metrics"] = {
        "invariants_count": len(invs),
        "breaches_count": len(breaches),
        "top_k_hinted": min(top_k, len(invs)),
    }

    if breaches:
        msg = (f"{len(breaches)} 条 cross-book invariant 疑似违反 · "
               f"top: {breaches[0]['invariant_id']}·{breaches[0]['rule_text'][:60]}")
        if mode == "active":
            _nli_augment_breaches(breaches)  # advisory 补充证据·不影响本轮 breaches 集合/verdict
            out["violations"].append({
                "kind": "cross_book_invariant_breach",
                "severity": "minor",
                "code": ISSUE_CODE,
                "message": msg,
                "metrics": out["metrics"],
                "breaches": breaches[:5],
                "_doc": "R19 W8 Batch-X·跨书硬规则违反 advisory·绝不 hard_gate·占位 NLI 启发式",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] cross_book_invariant: {msg} — 不上报", file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-X 跨书硬规则不变量·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--series", default=None,
                    help="workspace/styles/<series>/ 路径或直接到 magic_invariants.json")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.series, args.top_k)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
