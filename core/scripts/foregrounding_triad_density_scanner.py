#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""foregrounding_triad_density_scanner.py — Miall-Kuiken 三维 foregrounding 复合
(R19 W8 Batch-W·P1)

【缺口·2026-06-21·Miall-Kuiken 30 年实证】文体 foregrounding(前景化)三大维度：
  ① phonetic  : 语音/音律 (押韵/谐音双关/拟声/对仗音律)
  ② grammatical : 语法/句式 (倒装/排比/重复结构/省略)
  ③ semantic  : 语义 (隐喻/通感/反讽/奇喻)

【输入】cluster 草稿(CLUSTER_MODE=1 env)·【输出】FTDI 复合指数：
  FTDI = z(phonetic_per_1k) + z(grammatical_per_1k) + z(semantic_per_1k)
  作者档 ECDF p20/p80 → z 化
  缺基线 → 走兜底地板

【三桶规则探针(零依赖 regex/词典)】
  - phonetic   : 拟声(\\u00b7?咣当/砰砰/咯咯/呼啦/沙沙类) + 双引号尾「啊/呀/呢」语气词
                 + 同句末同韵母字(粗 codepoint 末位区段)
  - grammatical: 「不/没」前置否定 + 「是/也/又」并列重复 + 「的+主语后置」倒装(复用 inverted_modifier 简化版)
                 + 顿号长串(≥3 顿号同句)
  - semantic   : 隐喻关联词 (「如/像/似/仿佛/犹如/宛如」) + 通感跨感官(声 vs 色)
                 + 矛盾修辞(「沉默的呐喊」式)

【北极星⑤】顾问非法官·全 advisory·env FOREGROUNDING_TRIAD_DENSITY_MODE 默认 shadow·
  FOREGROUNDING_TRIAD_DENSITY_OFF_BAND 绝不 hard_gate。
  作者档 quantitative.foregrounding_triad.{phonetic_per_1k_ecdf,grammatical_per_1k_ecdf,
  semantic_per_1k_ecdf} 第一权威。

【与既有 scanner 严格正交】
  - rhetoric_parallel        : 排比单轴·正交(本=三维复合 FTDI)
  - inverted_modifier        : 倒装单轴·正交
  - synesthesia_density      : 通感单轴·正交
  - syntactic_diversity      : POS n-gram 多样性·正交
  - prose_homophonic_pun     : 谐音单轴·正交

用法: python foregrounding_triad_density_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "FOREGROUNDING_TRIAD_DENSITY_OFF_BAND"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 600

# 兜底地板·z-band 缺失时
FLOOR = {
    "phonetic_per_1k_min": 0.5,    "phonetic_per_1k_max": 20.0,
    "grammatical_per_1k_min": 1.0, "grammatical_per_1k_max": 25.0,
    "semantic_per_1k_min": 0.3,    "semantic_per_1k_max": 15.0,
    "ftdi_min": -4.0,              "ftdi_max": 4.0,
}

PHONETIC_ONOMATOPOEIA = (
    "咣当", "砰砰", "咯咯", "呼啦", "沙沙", "啪啪", "哗啦", "嘎吱",
    "扑通", "咔嚓", "嗖嗖", "嗡嗡", "叮咚", "丁零", "呼噜",
)
PHONETIC_MOOD_PARTICLES = ("啊", "呀", "呢", "哎", "嘛", "哦")

GRAMMATICAL_NEG_PREFIXES = ("不", "没", "勿", "莫", "甭")
GRAMMATICAL_REPEAT_MARKERS = ("是", "也", "又", "都")

SEMANTIC_METAPHOR_MARKERS = ("如", "像", "似", "仿佛", "犹如", "宛如", "好比", "恰似")
SEMANTIC_OXYMORON_CLUES = (
    ("沉默", "呐喊"), ("寒冷", "炽热"), ("明亮", "黑暗"), ("温柔", "残酷"),
    ("柔软", "坚硬"), ("急速", "凝固"),
)
# 通感跨感官词集
SENSORY_SOUND = ("声", "响", "鸣", "笑", "唱", "嘶")
SENSORY_COLOR = ("红", "蓝", "绿", "白", "黑", "金", "暗")
SENSORY_TASTE = ("甜", "苦", "辣", "酸", "咸")


def _mode() -> str:
    m = (os.environ.get("FOREGROUNDING_TRIAD_DENSITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_sentences(text):
    parts = re.split(r"[。！？!?…]+", text)
    return [s.strip() for s in parts if s.strip()]


def _load_baseline(project_root, style_path):
    out = {
        "phonetic_per_1k_ecdf_p20": None, "phonetic_per_1k_ecdf_p80": None,
        "grammatical_per_1k_ecdf_p20": None, "grammatical_per_1k_ecdf_p80": None,
        "semantic_per_1k_ecdf_p20": None, "semantic_per_1k_ecdf_p80": None,
        "from_author_profile": False,
    }
    data = None
    if style_path and Path(style_path).exists():
        try:
            data = json.loads(Path(style_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and project_root:
        for fname in ("作者风格_FINAL.json", "作者风格.json"):
            p = Path(project_root) / "_数据库" / fname
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    break
                except (OSError, json.JSONDecodeError):
                    continue
    if isinstance(data, dict):
        q = data.get("quantitative") or {}
        b = q.get("foregrounding_triad") or {}
        for k in list(out.keys()):
            if k == "from_author_profile":
                continue
            v = b.get(k)
            if isinstance(v, (int, float)):
                out[k] = float(v)
                out["from_author_profile"] = True
    return out


# ============ 三桶探针 ============

def probe_phonetic(text):
    """语音/音律层 hits 计数 per 1k CJK。"""
    cjk = max(1, _cjk_count(text))
    hits = 0
    for w in PHONETIC_ONOMATOPOEIA:
        hits += text.count(w)
    # 引号尾语气词("啊"/"呀"等 + 引号闭合)
    for m in re.finditer(r'["」』』』』』』』』』』』』』』』""」]', text):
        # 检查前 1 字符
        if m.start() >= 1 and text[m.start() - 1] in PHONETIC_MOOD_PARTICLES:
            hits += 1
    # 对仗叠字(连续两个相同字)
    for i in range(len(text) - 1):
        if text[i] == text[i + 1] and "一" <= text[i] <= "鿿":
            hits += 1
            # 不双计
    return {"per_1k": hits * 1000.0 / cjk, "hits": hits, "cjk": cjk}


def probe_grammatical(text):
    """语法/句式层 hits per 1k CJK。"""
    sentences = _split_sentences(text)
    cjk = max(1, _cjk_count(text))
    hits = 0
    for s in sentences:
        # 否定前置(句首否定词)
        if s[:1] in GRAMMATICAL_NEG_PREFIXES:
            hits += 1
        # 重复句式 markers ≥3 出现
        rep_count = sum(s.count(m) for m in GRAMMATICAL_REPEAT_MARKERS)
        if rep_count >= 3:
            hits += 1
        # 倒装提示：句首长定语 + 「的」(前 8 字内出现 ≥2 个「的」)
        if s[:8].count("的") >= 2:
            hits += 1
        # 顿号长串 ≥3
        if s.count("、") >= 3:
            hits += 1
    return {"per_1k": hits * 1000.0 / cjk, "hits": hits, "cjk": cjk,
            "total_sentences": len(sentences)}


def _has_cross_sensory(s):
    """同句出现 ≥2 个不同感官的代表字 → 通感。"""
    pools = (SENSORY_SOUND, SENSORY_COLOR, SENSORY_TASTE)
    seen = 0
    for pool in pools:
        if any(ch in s for ch in pool):
            seen += 1
    return seen >= 2


def probe_semantic(text):
    """语义/隐喻 / 通感 / 矛盾修辞 hits per 1k CJK。"""
    sentences = _split_sentences(text)
    cjk = max(1, _cjk_count(text))
    hits = 0
    # 隐喻关联词
    for w in SEMANTIC_METAPHOR_MARKERS:
        hits += text.count(w)
    # 通感
    for s in sentences:
        if _has_cross_sensory(s):
            hits += 1
    # 矛盾修辞
    for a, b in SEMANTIC_OXYMORON_CLUES:
        if a in text and b in text:
            # 同句出现一次算一对
            for s in sentences:
                if a in s and b in s:
                    hits += 1
    return {"per_1k": hits * 1000.0 / cjk, "hits": hits, "cjk": cjk}


def _ecdf_z(value, p20, p80):
    """ECDF z 化·p20/p80 → 输入分位区间，输出标准 z。"""
    if p20 is None or p80 is None or p80 <= p20:
        return None
    median = (p20 + p80) / 2.0
    spread = (p80 - p20) / 2.0
    if spread <= 1e-9:
        return None
    return (value - median) / spread


def _judge_ftdi(ftdi_value, floor_min, floor_max):
    if floor_min is None or floor_max is None:
        return False
    return ftdi_value < floor_min or ftdi_value > floor_max


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "foregrounding_triad_density", "schema_version": "1.0",
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

    baseline = _load_baseline(project_root, style_path)
    out["author_baseline"] = {"from_author_profile": baseline["from_author_profile"]}

    p1 = probe_phonetic(text)
    p2 = probe_grammatical(text)
    p3 = probe_semantic(text)

    z_p = _ecdf_z(p1["per_1k"],
                  baseline["phonetic_per_1k_ecdf_p20"],
                  baseline["phonetic_per_1k_ecdf_p80"])
    z_g = _ecdf_z(p2["per_1k"],
                  baseline["grammatical_per_1k_ecdf_p20"],
                  baseline["grammatical_per_1k_ecdf_p80"])
    z_s = _ecdf_z(p3["per_1k"],
                  baseline["semantic_per_1k_ecdf_p20"],
                  baseline["semantic_per_1k_ecdf_p80"])

    have_z = all(z is not None for z in (z_p, z_g, z_s))
    ftdi = round((z_p or 0) + (z_g or 0) + (z_s or 0), 3) if have_z else None

    metrics = {
        "phonetic_per_1k": round(p1["per_1k"], 3),
        "grammatical_per_1k": round(p2["per_1k"], 3),
        "semantic_per_1k": round(p3["per_1k"], 3),
        "z_phonetic": round(z_p, 3) if z_p is not None else None,
        "z_grammatical": round(z_g, 3) if z_g is not None else None,
        "z_semantic": round(z_s, 3) if z_s is not None else None,
        "ftdi": ftdi,
        "cjk": p1["cjk"],
    }
    out["metrics"] = metrics

    findings = []
    if have_z:
        if _judge_ftdi(ftdi, FLOOR["ftdi_min"], FLOOR["ftdi_max"]):
            findings.append(f"FTDI 复合 {ftdi} 超 ECDF z-band [{FLOOR['ftdi_min']}, {FLOOR['ftdi_max']}]")
    else:
        # 无基线·走单轴兜底地板
        if p1["per_1k"] < FLOOR["phonetic_per_1k_min"] or p1["per_1k"] > FLOOR["phonetic_per_1k_max"]:
            findings.append(f"phonetic per-1k {round(p1['per_1k'],2)} 超兜底带"
                            f"[{FLOOR['phonetic_per_1k_min']},{FLOOR['phonetic_per_1k_max']}]")
        if p2["per_1k"] < FLOOR["grammatical_per_1k_min"] or p2["per_1k"] > FLOOR["grammatical_per_1k_max"]:
            findings.append(f"grammatical per-1k {round(p2['per_1k'],2)} 超兜底带"
                            f"[{FLOOR['grammatical_per_1k_min']},{FLOOR['grammatical_per_1k_max']}]")
        if p3["per_1k"] < FLOOR["semantic_per_1k_min"] or p3["per_1k"] > FLOOR["semantic_per_1k_max"]:
            findings.append(f"semantic per-1k {round(p3['per_1k'],2)} 超兜底带"
                            f"[{FLOOR['semantic_per_1k_min']},{FLOOR['semantic_per_1k_max']}]")

    if findings:
        msg = " · ".join(findings)
        if mode == "active":
            out["violations"].append({
                "kind": "foregrounding_triad_density", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": metrics,
                "_doc": "R19 W8 Batch-W·Miall-Kuiken 三维 foregrounding·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] foregrounding_triad_density[{ISSUE_CODE}]: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-W·Miall-Kuiken 三维 foregrounding 复合·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.style)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
