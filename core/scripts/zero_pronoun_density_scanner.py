#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zero_pronoun_density_scanner.py — 中文零代词残留(R18 W7 Batch-U·P2)

【缺口·2026-06-21·arxiv 2510.09116 DITING 2025-10 + PMC8581763 Frontiers 2021
实证 91.3% same-sentence zero pronoun + ACL 2022 GuoFeng 阅文 + arxiv 2412.11732
WMT 2024】

中文是 pro-drop 语言·谓语前无主语 NP(零代词 zero pronoun, ZP)极为常见·
真中文作者基线 same-sentence ZP rate ≈ 91.3%(Frontiers 2021 实证)。
LLM/翻译 → 中文时倾向译出英文显式主语 → ZP rate 偏低 → 翻译腔。

三指标(jieba 切句·谓语前无主语 NP 检测)：
  ① zero_subject_ratio_per_clause  全篇 ZP / 总 clause
  ② same_sentence_zp_ratio         同句内 ZP / 同句 clause(实证 91.3%)
  ③ dialogue_vs_narrator_zp_gap    对话段 ZP 率 vs 叙述段 ZP 率(差值)

【与既有 scanner 显式去重】
  - translationese_residual_scanner 4 桶(de_stack/bei_passive/pre_modifier_long/
    name_overrepetition)·译入残留物质层
    本 scanner = pro-drop 反向(译出残留)·维度相反·正交
  - aspect_grounding(le/zhe/zai 体貌)·正交

【简化检测启发式·零依赖】
  - 句末符号切句·句内逗号切 clause
  - clause 起头模式：(？:[他她它我你][^他她它我你])|[^子句开头检测]
    若 clause 头 ∈ {他/她/它/我/你/咱/俺/这|那|此} + (不/也/将/已经/正/才|动词) → 显式主语
    否则视作 ZP(谓语开头 / 无主语 NP)
  - 简化版不依赖 jieba(占位待 jieba 接入)

【北极星⑤】顾问非法官·全 advisory·env ZERO_PRONOUN_DENSITY_MODE
  ZP_DENSITY_OFF_AUTHOR_BAND 绝不 hard_gate。
  作者档 quantitative.zp_baseline{same_sentence_zp_rate_mean/std} z-band 第一权威。
  无作者档 → 通用兜底地板 same_sentence_zp_rate ≥ 0.60(LLM 翻译腔常 < 0.60)。

用法: python zero_pronoun_density_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ZP_DENSITY_OFF_AUTHOR_BAND"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 第三/第一/第二人称显式主语词头
EXPLICIT_SUBJECT_HEAD = re.compile(
    r"^[\s]*(他|她|它|他们|她们|它们|"
    r"我|我们|咱|咱们|俺|俺们|"
    r"你|你们|您|"
    r"这|那|此|彼)(?![的得地么里])")

# 命名实体粗略：连续 2-3 中文字 + 后接动词(简化版只用人名常见姓 + 头尾)
NAME_LIKE_HEAD = re.compile(
    r"^[\s]*[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    r"戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    r"费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹"
    r"姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强"
    r"贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪]"
    r"[一-鿿]{1,2}(?=[，。！？\s])")

SENTENCE_SPLIT = re.compile(r"(?<=[。！？……])")
COMMA_SPLIT = re.compile(r"[，、；]")
DIALOGUE_QUOTE = re.compile(r"[“”]")

MIN_CJK = 500
FLOOR_SAME_SENT_ZP = 0.60   # 通用兜底地板


def _mode() -> str:
    m = (os.environ.get("ZERO_PRONOUN_DENSITY_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_baseline(project_root, style_path):
    out = {"same_sentence_zp_rate_mean": None,
           "same_sentence_zp_rate_std": None,
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
        zpb = q.get("zp_baseline") or {}
        m = zpb.get("same_sentence_zp_rate_mean")
        s = zpb.get("same_sentence_zp_rate_std")
        if isinstance(m, (int, float)):
            out["same_sentence_zp_rate_mean"] = float(m)
            out["same_sentence_zp_rate_std"] = float(s) if isinstance(s, (int, float)) else None
            out["from_author_profile"] = True
    return out


def _is_zero_pronoun_clause(clause: str) -> bool:
    """clause 是否零代词(谓语前无主语 NP)。
    简化启发式：
      - clause 头如果是显式人称代词/指示词 → 有主语
      - clause 头如果是命名实体(姓氏字+2-3 中文)+动词 → 有主语
      - 否则视作 ZP
    """
    s = clause.lstrip(" 　\t")
    if not s:
        return False
    if EXPLICIT_SUBJECT_HEAD.match(s):
        return False
    if NAME_LIKE_HEAD.match(s):
        return False
    return True


def _split_paragraphs(text: str):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _is_dialogue_para(p: str) -> bool:
    return bool(DIALOGUE_QUOTE.search(p))


def _scan_clauses(text: str):
    """返回 (total_clauses, zp_count, same_sent_total, same_sent_zp)。
    same_sent: 句内 ≥2 clause 中第 2+clause 的 ZP 率(实证 91.3%)。"""
    total = 0
    zp = 0
    same_total = 0
    same_zp = 0
    sents = SENTENCE_SPLIT.split(text)
    for sent in sents:
        sent = sent.strip()
        if len(sent) < 4:
            continue
        clauses = [c.strip() for c in COMMA_SPLIT.split(sent) if c.strip()]
        for i, c in enumerate(clauses):
            if len(c) < 2:
                continue
            total += 1
            is_zp = _is_zero_pronoun_clause(c)
            if is_zp:
                zp += 1
            if i >= 1:
                same_total += 1
                if is_zp:
                    same_zp += 1
    return total, zp, same_total, same_zp


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "zero_pronoun_density", "schema_version": "1.0",
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

    paragraphs = _split_paragraphs(text)
    narr_text = "\n".join(p for p in paragraphs if not _is_dialogue_para(p))
    dial_text = "\n".join(p for p in paragraphs if _is_dialogue_para(p))

    total, zp, st, sz = _scan_clauses(text)
    zr = (zp / total) if total else 0.0
    ssr = (sz / st) if st else 0.0

    nt, nz, _, _ = _scan_clauses(narr_text)
    dt, dz, _, _ = _scan_clauses(dial_text)
    narr_zp = (nz / nt) if nt else 0.0
    dial_zp = (dz / dt) if dt else 0.0
    gap = abs(narr_zp - dial_zp)

    out["author_baseline"] = {
        "from_author_profile": baseline["from_author_profile"],
        "same_sentence_zp_rate_mean": baseline["same_sentence_zp_rate_mean"],
    }
    out["metrics"] = {
        "zero_subject_ratio_per_clause": round(zr, 3),
        "same_sentence_zp_ratio": round(ssr, 3),
        "narrator_zp_rate": round(narr_zp, 3),
        "dialogue_zp_rate": round(dial_zp, 3),
        "dialogue_vs_narrator_zp_gap": round(gap, 3),
        "total_clauses": total,
        "same_sentence_clauses": st,
    }

    messages = []
    m, s = baseline["same_sentence_zp_rate_mean"], baseline["same_sentence_zp_rate_std"]
    if m is not None and s and s > 1e-6 and st > 0:
        z = (ssr - m) / s
        out["metrics"]["same_sentence_zp_z"] = round(z, 2)
        if z <= -2.0:
            messages.append(
                f"同句 ZP 率 {round(ssr,3)} 偏离作者基线 "
                f"{round(m,3)}±{round(s,3)} {round(z,1)}σ·译出残留过密")
    elif st > 0 and ssr < FLOOR_SAME_SENT_ZP:
        messages.append(
            f"同句 ZP 率 {round(ssr,3)} < 通用地板 {FLOOR_SAME_SENT_ZP}·"
            f"翻译腔(英文 → 中文译出主语)")

    if messages:
        msg = " · ".join(messages)
        if mode == "active":
            out["violations"].append({
                "kind": "zero_pronoun_density_off_band",
                "severity": "minor", "code": ISSUE_CODE,
                "message": msg, "metrics": out["metrics"],
                "_doc": "中文 pro-drop 同句 ZP·91.3% Frontiers 实证·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] zero_pronoun_density: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="中文零代词密度·91.3% Frontiers·advisory·shadow")
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
