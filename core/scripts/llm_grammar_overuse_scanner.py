#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_grammar_overuse_scanner.py — LLM 4 语法过用中文化(R19 W8 Batch-V·P0)

【缺口·2026-06-21·PNAS 2025 Reinhart "LLMs overuse specific grammatical
patterns when writing"】实证 LLM 英文写作偏爱 4 类语法过用·中文化迁移：

  ① present_participial   → 中文「正在 / 着」前置状语丛 + 「-ing」式分词丛
                            (走着,看着,微笑着) 同句堆 3 个以上 / 句
  ② nominalization        → 动词名词化 (「实现」「进行」「展开」+ 抽象名词)
                            密度 per-1k-CJK 高于作者基线
  ③ nested_X_de_Y_subject  → 嵌套「X 的 Y 的 Z」做主语·3 层以上 / 句
  ④ parallel_and_stack    → 串联并列堆栈「A、B、C 和 D」≥4 项 / 句

【与既有 scanner 严格正交】
  - anti_slop / semantic_slop : AI 腔短语命中(词项)·正交(本=语法骨架计数)
  - syntactic_diversity      : POS n-gram 句法模板多样性·正交(本=四模具堆栈)
  - paratactic_density       : 并列连词「和/与/以及」密度·部分相邻但本 scanner
                                只算「≥4 项串联枚举」(枚举堆栈) 非全句并列连接

【输入】cluster 草稿(CLUSTER_MODE=1 env)·【输出】每子探针独立 advisory code：
  LLM_GRAMMAR_PARTICIPIAL_OVERUSE
  LLM_GRAMMAR_NOMINALIZATION_OVERUSE
  LLM_GRAMMAR_NESTED_X_DE_Y_OVERUSE
  LLM_GRAMMAR_PARALLEL_AND_STACK_OVERUSE

【北极星⑤】顾问非法官·全 advisory·env LLM_GRAMMAR_OVERUSE_MODE 默认 shadow·
  4 个 code 绝不 hard_gate。作者档 quantitative.llm_grammar_overuse_baseline
  z-band 第一权威·缺则用兜底地板。

用法: python llm_grammar_overuse_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = (
    "LLM_GRAMMAR_PARTICIPIAL_OVERUSE",
    "LLM_GRAMMAR_NOMINALIZATION_OVERUSE",
    "LLM_GRAMMAR_NESTED_X_DE_Y_OVERUSE",
    "LLM_GRAMMAR_PARALLEL_AND_STACK_OVERUSE",
)
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 600

# 子探针兜底地板(per-1k-CJK 或 per-sentence)
FLOOR = {
    "participial_per_sentence_avg": 0.35,   # 同句 着/正在 丛 句平均
    "nominalization_per_1k": 8.0,           # 动词名化短语密度
    "nested_per_sentence_avg": 0.08,        # 3 层 X的Y 主语 句平均
    "parallel_stack_per_1k": 1.2,           # ≥4 项串联枚举密度
}

# 名词化高频动词前缀(动词+抽象名词)·实证 PNAS Reinhart 翻译过来的中文偏好
NOMINALIZATION_VERBS = (
    "进行", "实现", "展开", "开展", "完成", "做出", "作出", "予以", "给予",
    "形成", "造成", "导致", "引发", "建立", "创建", "构建", "构成", "组成",
    "提出", "提供", "采取", "施加", "保持", "维持", "保留",
)

# 名词化常跟的抽象名词后缀触发词(降低误判)
ABSTRACT_NOUN_HINTS = (
    "改革", "调整", "推动", "建设", "工作", "计划", "分析", "判断", "选择",
    "尝试", "回应", "反应", "影响", "改变", "决定", "评估", "压力", "评价",
    "审视", "理解", "认识", "处理", "应对", "讨论", "对话", "互动", "运作",
    "操作", "调度", "管理", "协调",
)


def _mode() -> str:
    m = (os.environ.get("LLM_GRAMMAR_OVERUSE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_sentences(text: str):
    """按句末标点切·返回非空句。"""
    parts = re.split(r"[。！？!?…]+", text)
    return [s.strip() for s in parts if s.strip()]


def _load_baseline(project_root, style_path):
    out = {"present_participial_mean": None, "present_participial_std": None,
           "nominalization_per_1k_mean": None, "nominalization_per_1k_std": None,
           "nested_x_de_y_mean": None, "nested_x_de_y_std": None,
           "parallel_stack_per_1k_mean": None, "parallel_stack_per_1k_std": None,
           "from_author_profile": False}
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
        b = q.get("llm_grammar_overuse_baseline") or {}
        for k in list(out.keys()):
            if k == "from_author_profile":
                continue
            v = b.get(k)
            if isinstance(v, (int, float)):
                out[k] = float(v)
                out["from_author_profile"] = True
    return out


# ============ 4 子探针 ============

def probe_participial(sentences):
    """同句「正在/着」分词丛密度·句平均。中文「正在...」「V 着 N」是 -ing 投影。"""
    if not sentences:
        return {"per_sentence_avg": 0.0, "hits": 0, "total_sentences": 0}
    hits = 0
    for s in sentences:
        # 计数同句的 着 / 正在 / 正 V 出现
        c = 0
        c += s.count("着")
        c += s.count("正在")
        # 「V 着 N」V 后空格无 — 简化用 正在 + 着 总和
        # 同句出现 ≥3 → 算丛
        if c >= 3:
            hits += 1
    return {"per_sentence_avg": hits / max(1, len(sentences)),
            "hits": hits, "total_sentences": len(sentences)}


def probe_nominalization(text):
    """动词名化短语密度 per 1k CJK。"""
    cjk = max(1, _cjk_count(text))
    hits = 0
    for v in NOMINALIZATION_VERBS:
        idx = 0
        while True:
            j = text.find(v, idx)
            if j < 0:
                break
            # 后接最多 6 个 CJK 内是否包含抽象名词触发
            tail = text[j + len(v): j + len(v) + 6]
            if any(h in tail for h in ABSTRACT_NOUN_HINTS):
                hits += 1
            idx = j + len(v)
    per_1k = hits * 1000.0 / cjk
    return {"per_1k": per_1k, "hits": hits, "cjk": cjk}


def probe_nested_x_de_y(sentences):
    """嵌套「X 的 Y 的 Z (的 W)」做主语·按句首前 12 字内出现 ≥2 个「的」算 3 层主语。"""
    if not sentences:
        return {"per_sentence_avg": 0.0, "hits": 0, "total_sentences": 0}
    hits = 0
    for s in sentences:
        head = s[:14]
        # 主语侧前 14 字内至少 2 个「的」=≥3 层嵌套(X 的 Y 的 Z)
        de_count = head.count("的")
        if de_count >= 2:
            hits += 1
    return {"per_sentence_avg": hits / max(1, len(sentences)),
            "hits": hits, "total_sentences": len(sentences)}


def probe_parallel_and_stack(text):
    """串联并列堆栈「A、B、C 和 D」≥4 项·密度 per 1k CJK。"""
    cjk = max(1, _cjk_count(text))
    # 顿号连用 ≥3 个 + 后接「和/与/以及」=≥4 项串联枚举
    # 简化：在窗口 30 字内 出现 ≥3 个「、」紧接「和/与/以及/及」
    hits = 0
    for m in re.finditer(r"和|与|以及|及", text):
        start = max(0, m.start() - 30)
        window = text[start: m.start()]
        if window.count("、") >= 3:
            hits += 1
    per_1k = hits * 1000.0 / cjk
    return {"per_1k": per_1k, "hits": hits, "cjk": cjk}


# ============ judge utilities ============

def _z(value, mean, std):
    if mean is None or std is None or std <= 1e-6:
        return None
    return (value - mean) / std


def _judge(metric_value, mean, std, floor_value, prefer_high=True):
    """带 z-band 偏离判定·缺基线时走兜底地板。返回 (over: bool, reason)。"""
    z = _z(metric_value, mean, std)
    if z is not None:
        if abs(z) >= 2.0:
            sign = "偏高" if z > 0 else "偏低"
            return (True, f"偏离作者基线 {round(mean,3)}±{round(std,3)} {round(z,1)}σ ({sign})")
        return (False, None)
    if prefer_high:
        if metric_value > floor_value:
            return (True, f"超兜底地板 {floor_value}")
    return (False, None)


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "llm_grammar_overuse", "schema_version": "1.0",
           "mode": mode, "codes": list(ISSUE_CODES), "gate_level": "advisory",
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

    sentences = _split_sentences(text)
    baseline = _load_baseline(project_root, style_path)
    out["author_baseline"] = {"from_author_profile": baseline["from_author_profile"]}

    p1 = probe_participial(sentences)
    p2 = probe_nominalization(text)
    p3 = probe_nested_x_de_y(sentences)
    p4 = probe_parallel_and_stack(text)

    metrics = {
        "participial_per_sentence_avg": round(p1["per_sentence_avg"], 4),
        "nominalization_per_1k": round(p2["per_1k"], 3),
        "nested_per_sentence_avg": round(p3["per_sentence_avg"], 4),
        "parallel_stack_per_1k": round(p4["per_1k"], 3),
        "total_sentences": p1["total_sentences"],
        "cjk": p2["cjk"],
    }
    out["metrics"] = metrics

    findings = []
    over1, why1 = _judge(p1["per_sentence_avg"], baseline["present_participial_mean"],
                        baseline["present_participial_std"],
                        FLOOR["participial_per_sentence_avg"])
    if over1:
        findings.append((ISSUE_CODES[0],
                         f"同句『着/正在』分词丛过密(句平均 {metrics['participial_per_sentence_avg']})·{why1 or ''}"))
    over2, why2 = _judge(p2["per_1k"], baseline["nominalization_per_1k_mean"],
                        baseline["nominalization_per_1k_std"],
                        FLOOR["nominalization_per_1k"])
    if over2:
        findings.append((ISSUE_CODES[1],
                         f"动词名词化短语过密(per-1k {metrics['nominalization_per_1k']})·{why2 or ''}"))
    over3, why3 = _judge(p3["per_sentence_avg"], baseline["nested_x_de_y_mean"],
                        baseline["nested_x_de_y_std"],
                        FLOOR["nested_per_sentence_avg"])
    if over3:
        findings.append((ISSUE_CODES[2],
                         f"嵌套『X 的 Y 的 Z』主语过密(句平均 {metrics['nested_per_sentence_avg']})·{why3 or ''}"))
    over4, why4 = _judge(p4["per_1k"], baseline["parallel_stack_per_1k_mean"],
                        baseline["parallel_stack_per_1k_std"],
                        FLOOR["parallel_stack_per_1k"])
    if over4:
        findings.append((ISSUE_CODES[3],
                         f"串联并列堆栈『A、B、C 和 D』≥4 项过密(per-1k {metrics['parallel_stack_per_1k']})·{why4 or ''}"))

    if findings:
        if mode == "active":
            for code, msg in findings:
                out["violations"].append({
                    "kind": "llm_grammar_overuse", "severity": "minor",
                    "code": code, "message": msg, "metrics": metrics,
                    "_doc": "PNAS 2025 Reinhart LLM 4 语法过用·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = " · ".join(m for _, m in findings)
        else:
            for code, msg in findings:
                print(f"[SHADOW] llm_grammar_overuse[{code}]: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="PNAS 2025 LLM 4 语法过用中文化·advisory·shadow")
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
