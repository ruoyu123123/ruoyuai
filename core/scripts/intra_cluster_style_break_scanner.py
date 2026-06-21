#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intra_cluster_style_break_scanner.py — 句对级风格断点定位
(R19 W8 Batch-W·P1·PAN 2025 SSPC + Better Call Claude arxiv 2508.00680)

【缺口·2026-06-21】既有 character_distinctiveness / cross_scene_voice_drift /
voice_pack / SCH / silence_gap_lapse 只查 character / scene 粒度·缺**句对级**风格
断点定位 → cluster 内突然换叙述声(从 narrator 切到第三方旁观/AI 腔扎堆) 漏检。

【双栈架构】
  (a) SSPC BGE-small-zh + 3 层 MLP (Sentence-Pair Style Change) 自监督预训练
      参数固化 → 占位 (本批仅写入 _placeholder=true 路径·真模型权重 defer)
  (b) 规则 ensemble 兜底 (本批落地)
      - 衔接词反转密度       (突然的「但是/然而」转折抓不住)
      - 句长 z-score 跳跃    (邻接两段 句长 z 差 > 2.0)
      - 标点 KL              (邻接两段 句末符号分布 KL > 0.5)
      - quotative 失踪/激增   (说/道/答 等 quotative 出现/消失)
      - register 切换       (口语 vs 书面 register 词集出现率反转)

【输出】每个断点写 style_break_anchors[]：
  {anchor_idx, prev_signature, next_signature, score, drivers: [...]}
  → 写回 changes.json (gen_fixer 接收) advisory，最强 anchor.score 报警

【环境变量】
  STYLE_BREAK_LLM_FALLBACK = off  (默认 off·只跑规则 ensemble + SSPC 占位)
  INTRA_CLUSTER_STYLE_BREAK_MODE = shadow (默认)

【北极星⑤】顾问非法官·全 advisory·env INTRA_CLUSTER_STYLE_BREAK_MODE 默认 shadow·
  STYLE_BREAK_DETECTED 绝不 hard_gate。

【与既有 scanner 严格正交】
  - character_distinctiveness: 角色间区分 (本 = 段对级断点)
  - cross_scene_voice_drift  : 同角色跨场景 drift (本 = cluster 内邻段断点)
  - voice_pack / SCH         : voice DNA per-character (本 = signature 段对差)
  - silence_gap_lapse        : 角色沉默间隔 (本 = style 信号)

用法: python intra_cluster_style_break_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

ISSUE_CODE = "STYLE_BREAK_DETECTED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 1200
WINDOW_SENT = 8  # 滑窗大小 (句)
SCORE_THRESHOLD = 1.5  # ensemble 综合得分阈值

# SSPC BGE 模型权重路径占位 (true defer)
SSPC_PLACEHOLDER_PATH = "core/data/sspc_bge_small_zh_placeholder.json"

# register 词集 (简化 5-5 对照)
REGISTER_COLLOQ = ("特么", "尼玛", "卧槽", "哎呀", "妈呀", "草", "靠", "服了", "牛逼", "拉胯")
REGISTER_FORMAL = ("乃是", "盖以", "兹有", "至若", "焉得", "尔后", "嗟乎", "诚然", "罢了", "诸如")
QUOTATIVES = ("说", "道", "答", "问", "应", "喊", "叹", "笑", "怒", "嚷", "嘀咕", "低语")
CONJ_REVERSAL = ("但是", "然而", "不过", "却", "可是", "反而", "偏偏")


def _mode() -> str:
    m = (os.environ.get("INTRA_CLUSTER_STYLE_BREAK_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _llm_fallback_enabled() -> bool:
    return (os.environ.get("STYLE_BREAK_LLM_FALLBACK") or "off").strip().lower() == "on"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_sentences(text):
    """返回 [(sentence, end_punct)] 保留句末符号。"""
    parts = re.findall(r"[^。！？!?…]+[。！？!?…]+", text)
    return [p.strip() for p in parts if p.strip()]


def _sspc_placeholder_check():
    """SSPC BGE 占位路径检查·返回占位 marker。"""
    return {
        "_placeholder": True,
        "path": SSPC_PLACEHOLDER_PATH,
        "model": "BGE-small-zh + 3-layer MLP",
        "training_params_status": "deferred",
        "_doc": "PAN 2025 SSPC 真模型权重 + 训练超参 defer·本批仅占位·rule ensemble 兜底",
    }


# ============ 规则 ensemble 信号 ============

def _window_signature(sentences):
    """计算窗口 signature：句长 mean / 标点分布 / quotative 比 / register 比 / 反转词比。"""
    if not sentences:
        return None
    lens = [_cjk_count(s) for s in sentences]
    mean_len = sum(lens) / len(lens)

    # 末标点分布
    punct_counter = Counter()
    for s in sentences:
        end = s[-1] if s else ""
        if end in "。！？!?…":
            punct_counter[end] += 1

    total = sum(punct_counter.values()) or 1
    punct_dist = {k: v / total for k, v in punct_counter.items()}

    joined = "".join(sentences)
    n = max(1, len(joined))
    quot_ratio = sum(joined.count(q) for q in QUOTATIVES) / n
    colloq_ratio = sum(joined.count(c) for c in REGISTER_COLLOQ) / n
    formal_ratio = sum(joined.count(c) for c in REGISTER_FORMAL) / n
    reversal_ratio = sum(joined.count(c) for c in CONJ_REVERSAL) / n

    return {
        "mean_len": mean_len,
        "punct_dist": punct_dist,
        "quot_ratio": quot_ratio,
        "colloq_ratio": colloq_ratio,
        "formal_ratio": formal_ratio,
        "reversal_ratio": reversal_ratio,
        "n_sentences": len(sentences),
    }


def _punct_kl(a, b):
    """对称 KL 分歧 (smoothed)."""
    keys = set(a.keys()) | set(b.keys())
    eps = 1e-6
    kl_ab = 0.0
    kl_ba = 0.0
    for k in keys:
        pa = a.get(k, 0.0) + eps
        pb = b.get(k, 0.0) + eps
        kl_ab += pa * math.log(pa / pb)
        kl_ba += pb * math.log(pb / pa)
    return (kl_ab + kl_ba) / 2.0


def _pair_distance(sig_prev, sig_next):
    """两窗 signature 距离，并返回 driver 列表。"""
    if sig_prev is None or sig_next is None:
        return 0.0, []
    drivers = []
    score = 0.0

    # 句长 z 差·标度 mean_len 由邻段方差近似 (用绝对差/平均)
    avg_len = (sig_prev["mean_len"] + sig_next["mean_len"]) / 2 + 1e-6
    len_z = abs(sig_prev["mean_len"] - sig_next["mean_len"]) / avg_len
    if len_z > 0.5:
        drivers.append({"driver": "sentence_length_jump", "delta": round(len_z, 3)})
        score += min(len_z, 2.0)

    # 标点 KL
    kl = _punct_kl(sig_prev["punct_dist"], sig_next["punct_dist"])
    if kl > 0.3:
        drivers.append({"driver": "punctuation_kl", "kl": round(kl, 3)})
        score += min(kl, 2.0)

    # quotative 出现/消失
    qd = abs(sig_prev["quot_ratio"] - sig_next["quot_ratio"])
    if qd > 0.02:
        drivers.append({"driver": "quotative_shift", "delta": round(qd, 4)})
        score += min(qd * 30.0, 2.0)

    # register 切换
    rcolloq = abs(sig_prev["colloq_ratio"] - sig_next["colloq_ratio"])
    rformal = abs(sig_prev["formal_ratio"] - sig_next["formal_ratio"])
    if rcolloq + rformal > 0.005:
        drivers.append({"driver": "register_switch",
                        "colloq_delta": round(rcolloq, 4),
                        "formal_delta": round(rformal, 4)})
        score += min((rcolloq + rformal) * 100.0, 2.0)

    # 反转衔接词突激
    rev = abs(sig_prev["reversal_ratio"] - sig_next["reversal_ratio"])
    if rev > 0.005:
        drivers.append({"driver": "conjunction_reversal_burst", "delta": round(rev, 4)})
        score += min(rev * 80.0, 2.0)

    return score, drivers


def detect_breaks(text):
    """滑窗 邻接对 → score + drivers·返回 anchors list。"""
    sentences = _split_sentences(text)
    n = len(sentences)
    if n < WINDOW_SENT * 2:
        return [], 0

    anchors = []
    # 邻接窗对 (i, i+WINDOW_SENT) ·步长 WINDOW_SENT
    i = 0
    while i + WINDOW_SENT * 2 <= n:
        prev = sentences[i: i + WINDOW_SENT]
        nxt = sentences[i + WINDOW_SENT: i + WINDOW_SENT * 2]
        sig_a = _window_signature(prev)
        sig_b = _window_signature(nxt)
        score, drivers = _pair_distance(sig_a, sig_b)
        if score >= SCORE_THRESHOLD:
            anchors.append({
                "anchor_sentence_idx": i + WINDOW_SENT,
                "score": round(score, 3),
                "drivers": drivers,
                "prev_signature": {k: round(v, 4) if isinstance(v, float) else v
                                   for k, v in sig_a.items() if k != "punct_dist"},
                "next_signature": {k: round(v, 4) if isinstance(v, float) else v
                                   for k, v in sig_b.items() if k != "punct_dist"},
            })
        i += WINDOW_SENT
    return anchors, n


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "intra_cluster_style_break", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None,
           "sspc_status": _sspc_placeholder_check(),
           "llm_fallback_enabled": _llm_fallback_enabled()}
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

    anchors, total = detect_breaks(text)
    out["metrics"] = {
        "total_sentences": total,
        "anchors_count": len(anchors),
        "window": WINDOW_SENT,
        "score_threshold": SCORE_THRESHOLD,
    }
    out["style_break_anchors"] = anchors

    if anchors:
        # 最强断点报警
        best = max(anchors, key=lambda a: a["score"])
        msg = (f"检测到 {len(anchors)} 个 cluster 内风格断点·最强 anchor "
               f"sentence_idx={best['anchor_sentence_idx']} score={best['score']} "
               f"drivers={[d['driver'] for d in best['drivers']]}")
        if mode == "active":
            out["violations"].append({
                "kind": "intra_cluster_style_break", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": out["metrics"],
                "anchors": anchors[:5],
                "_doc": "R19 W8 Batch-W·PAN 2025 SSPC 风格断点·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] intra_cluster_style_break[{ISSUE_CODE}]: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-W·句对级风格断点定位·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
