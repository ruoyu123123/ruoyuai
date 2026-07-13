#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imageability_round_trip_probe.py — 视觉具象往返 · R24 W12 Batch-LL · P2

【缺口 · 多模态视觉具象往返 retention probe】
作者级具象写作=读者闭眼能视见画面。LLM 抽象写作=画面残留<35%。
理想链路 prose→T2I prompt（强提取空间+实物 token）→反向 caption→
对原段算 anchor_noun/spatial/sensory token jaccard·retention 越高画面越保。
真版 T2I+VLM 接力调 gen-model·占位用 char-Jaccard 替身（_placeholder=true）
保证零依赖落地 + 真版接口预留。

【做法 · 确定性 · 零 LLM/零联网（占位 Jaccard 替身）】
  · 取段：草稿按段拆分·≥120 CJK 段进入候选池·top-K 段（K=3 默认）抽样
  · prose→T2I prompt 占位提取：保留 anchor_noun（具象名词）/spatial（方位词）/
    sensory（五感动词）三类 token 子集
  · 反向 caption 占位：等价 prose 自身（替身）·真版需走 T2I+VLM 两段调用
  · retention = token_jaccard(prose_tokens, caption_tokens)
    - retention < 0.35 LOW（占位代理：候选段三类 token 命中过少→画面感弱）
    - retention > 0.75 HIGH（候选段三类 token 充裕）
  · N≥10 cluster 后 ECDF 标定（defer 到 cross-cluster aggregator）

【三 advisory · 全 advisory shadow】
  · IMAGEABILITY_ROUND_TRIP_LOW    — retention < 0.35 段过多·画面残留差
  · IMAGEABILITY_ROUND_TRIP_HIGH   — retention > 0.75 段优秀（info）
  · IMAGEABILITY_ROUND_TRIP_NA     — 候选段不足（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  IMAGEABILITY_ROUND_TRIP_* 绝不进 audit_hub.HARD_GATE_CODES。

env IMAGEABILITY_ROUND_TRIP_MODE: off / shadow（默认） / active
用法: python imageability_round_trip_probe.py <draft> [--project <root>] [--cluster <key>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_LOW = "IMAGEABILITY_ROUND_TRIP_LOW"
ISSUE_CODE_HIGH = "IMAGEABILITY_ROUND_TRIP_HIGH"
ISSUE_CODE_NA = "IMAGEABILITY_ROUND_TRIP_NA"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 占位 lexicon · _placeholder=true · 真版用 Paivio MRC + sensorimotor lexicons
_ANCHOR_NOUNS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·anchor_noun 具象名词·占位",
    "_words": [
        "桌子", "椅子", "茶杯", "门", "窗", "墙", "灯", "刀", "剑", "镜子",
        "床", "钥匙", "锁", "屋", "院", "树", "石", "草", "水", "火",
        "酒", "茶", "饭", "鞋", "衣", "袍", "帽", "纸", "笔", "书",
    ],
}
_SPATIAL_WORDS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·spatial 方位词·占位",
    "_words": [
        "东", "南", "西", "北", "上", "下", "左", "右", "前", "后",
        "里", "外", "中", "旁", "侧", "边", "顶", "底", "周围", "之间",
    ],
}
_SENSORY_VERBS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·sensory 五感动词·占位",
    "_words": [
        "看", "听", "闻", "尝", "摸", "握", "推", "拉", "拍", "捏",
        "瞧", "瞥", "嗅", "舔", "嚼", "撞", "碰", "踩", "蹭", "抱",
    ],
}

MIN_PARA_CJK = 120
TOP_K = 3
RETENTION_LOW = 0.35
RETENTION_HIGH = 0.75


def _mode() -> str:
    m = (os.environ.get("IMAGEABILITY_ROUND_TRIP_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _split_paragraphs(text: str) -> list:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_tokens(text: str, lex_words: list) -> set:
    """提取段内命中的占位 lexicon token 集合（去重）。"""
    return {w for w in lex_words if w in text}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 0.0
    return round(len(a & b) / len(u), 4)


def _build_prompt_tokens(para: str) -> set:
    """prose→T2I prompt 占位：anchor_noun + spatial + sensory 三类 token 子集。"""
    out = set()
    out |= _extract_tokens(para, _ANCHOR_NOUNS["_words"])
    out |= _extract_tokens(para, _SPATIAL_WORDS["_words"])
    out |= _extract_tokens(para, _SENSORY_VERBS["_words"])
    return out


def _round_trip(para: str) -> dict:
    """单段 round-trip retention·返回 {prompt_tokens, caption_tokens, retention}。

    真版：prose → T2I prompt (gen-model 抽取) → image → VLM caption。
    占位：caption_tokens 等价 prompt_tokens 在原段内的命中（替身）。
    retention 用 prompt_tokens vs anchor_noun 总数比例校准（防全空段虚高）。
    """
    prompt_tokens = _build_prompt_tokens(para)
    # 占位 caption = 同段命中（替身）
    caption_tokens = _build_prompt_tokens(para)
    # 校准：占位场景下 prompt==caption 总=1.0·改用 token 密度替代
    # retention 占位 = (prompt_tokens 命中数) / (段长 / 30 CJK 标杆)
    para_cjk = _cjk_count(para)
    standard = max(1, para_cjk // 30)
    density = min(1.0, len(prompt_tokens) / standard)
    return {
        "prompt_tokens": sorted(prompt_tokens),
        "caption_tokens": sorted(caption_tokens),
        "retention": round(density, 4),
        "para_cjk": para_cjk,
    }


def scan(draft_path, project_root=None, cluster_id=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "imageability_round_trip_probe", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": True,
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    paragraphs = _split_paragraphs(text)
    candidates = [p for p in paragraphs if _cjk_count(p) >= MIN_PARA_CJK]
    if len(candidates) < TOP_K:
        out["note"] = f"候选段 {len(candidates)} < {TOP_K}·样本不足跳过"
        out["candidate_count"] = len(candidates)
        if mode == "active":
            out["violations"].append({
                "kind": "imageability_round_trip_probe",
                "severity": "info",
                "code": ISSUE_CODE_NA,
                "message": f"候选段 {len(candidates)} < {TOP_K}·N/A",
                "_doc": "R24 W12 Batch-LL·候选不足·info advisory",
            })
        out["violations_count"] = len(out["violations"])
        return out

    # 取前 K 段（占位策略：按 CJK 长度 desc 排序）
    candidates_sorted = sorted(candidates, key=lambda p: -_cjk_count(p))[:TOP_K]
    rt_reports = [_round_trip(p) for p in candidates_sorted]
    retentions = [r["retention"] for r in rt_reports]
    mean_ret = round(sum(retentions) / len(retentions), 4) if retentions else 0.0
    low_count = sum(1 for r in retentions if r < RETENTION_LOW)
    high_count = sum(1 for r in retentions if r > RETENTION_HIGH)

    out.update({
        "cjk": cjk,
        "candidate_count": len(candidates),
        "sampled_k": len(candidates_sorted),
        "round_trip": rt_reports,
        "retentions": retentions,
        "mean_retention": mean_ret,
        "low_count": low_count,
        "high_count": high_count,
        "_thresholds": {"low": RETENTION_LOW, "high": RETENTION_HIGH},
    })

    flags = []
    if low_count >= max(1, TOP_K // 2):
        flags.append({
            "code": ISSUE_CODE_LOW,
            "msg": (f"round-trip retention 低段 {low_count}/{len(candidates_sorted)}"
                    f"·mean={mean_ret}·画面残留<{RETENTION_LOW}"),
            "severity": "minor",
        })
    elif high_count >= max(1, TOP_K // 2):
        flags.append({
            "code": ISSUE_CODE_HIGH,
            "msg": (f"round-trip retention 高段 {high_count}/{len(candidates_sorted)}"
                    f"·mean={mean_ret}·画面残留>{RETENTION_HIGH}"),
            "severity": "info",
        })

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "imageability_round_trip_probe",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": "R24 W12 Batch-LL·视觉具象往返·advisory·绝不 hard_gate"})
        out["verdict"] = ("FAIL_MINOR"
                          if any(v["severity"] == "minor" for v in out["violations"])
                          else "PASS")
        out["warning"] = "·".join(f["msg"] for f in flags if f["severity"] == "minor") or None
    elif mode == "shadow":
        minor = [f for f in flags if f["severity"] == "minor"]
        if minor:
            print("[SHADOW] imageability_round_trip_probe: "
                  + "·".join(f["msg"] for f in minor)
                  + " — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="视觉具象往返 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
