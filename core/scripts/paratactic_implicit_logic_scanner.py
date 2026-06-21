#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paratactic_implicit_logic_scanner.py — 中文意合 vs 英文形合 · 逻辑连词显隐度

【缺口 · R18 W7 Batch-T·P1 · 2026-06-21】HKU 学位论文 hub.hku.hk 127800 +
AJHSS 2024 francis-press 14715 + Academy Pub JLTR vol02/01/14：
  中文为意合（parataxis）语言，英文为形合（hypotaxis）语言。中文叙事偏依靠
  时序、语义、语境承接因果逻辑，而英文必须显式连词标记。LLM 在中文写作时
  常残留英文形合习惯——『因为...所以...』『虽然...但是...』成对堆砌、显式
  连词密度远超真中文作者基线 → 翻译腔/学生作文感。

【与既有 scanner 显式去重】
  - syntactic_diversity_scanner：POS n-gram 同骨架重复（句法模板复用）
    本 scanner = 单 token 级逻辑连词密度（不依赖 POS）·正交
  - translationese_residual_scanner：4 桶（de_stack/bei_passive/pre_modifier_long/
    name_overrepetition）NP 嵌套与被动·译文味物质层
    本 scanner = 逻辑连词显隐比（hypotactic vs paratactic）·语篇逻辑层·正交
  - R13 aspect_grounding_scanner：体貌 le/zhe/zai 前景背景密度
    本 scanner = 不同语法层（连词 vs 体标记）·正交

【两探针 · 确定性纯规则】
  ① logical_connective_density_per_kcjk
     30+ 显式逻辑连词词频汇总（因果/转折/选择/递进 四类）：
       因果：因为/所以/于是/因此/故/故而/故此/则/便/即/导致/以致/致使
       转折：虽然/但是/然而/不过/可是/却/反而/反倒
       选择：或/或者/要么/要不
       递进：而且/并且/此外/再者
     除以 cjk × 1000。
  ② causal_chain_implicit_ratio
     扫『XX，YY』结构（句号/逗号分隔短句对），其中两短句构成 implicit causal
     chain（动作连续 + 无连词 → implicit）vs explicit（含上述连词 → explicit）。
     ratio = implicit / (implicit + explicit)。
     中文真作者 ratio 通常 > 0.65；LLM 翻译腔 ratio 偏低（< 0.40）。

【作者档第一权威】
  quantitative.paratactic_baseline = {
    connective_density_mean, connective_density_std,
    implicit_ratio_mean, implicit_ratio_std
  }
  无作者档 → 通用兜底（implicit_ratio < 0.30 报偏低；connective_density > 25/kCJK 报偏高）。

【北极星⑤】顾问非法官·全 advisory·env PARATAXIS_IMPLICIT_LOGIC_MODE
  PARATAXIS_OFF_AUTHOR_BAND 绝不进 audit_hub.HARD_GATE_CODES。

用法: python paratactic_implicit_logic_scanner.py <draft> [--project <root>] [--style <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PARATAXIS_OFF_AUTHOR_BAND"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 4 类逻辑连词（高确定性 · 单字连词谨慎避免误报）
CAUSAL_CONJ = ("因为", "所以", "于是", "因此", "故而", "故此",
               "导致", "以致", "致使")
ADVERSATIVE_CONJ = ("虽然", "但是", "然而", "不过", "可是",
                    "反而", "反倒", "尽管")
ALTERNATIVE_CONJ = ("或者", "要么", "要不")
PROGRESSIVE_CONJ = ("而且", "并且", "此外", "再者")
# 单字连词（仅在句首/逗号后扫，防『便利店』『则名』等误报）
LEADING_SINGLE_CONJ = ("故", "则", "便", "即", "却", "或")

ALL_MULTI_CONJ = (CAUSAL_CONJ + ADVERSATIVE_CONJ
                  + ALTERNATIVE_CONJ + PROGRESSIVE_CONJ)

# implicit causal chain 锚词：连续动作（无连词承接）
SENTENCE_SPLIT = re.compile(r"(?<=[。！？……])")
COMMA_SPLIT = re.compile(r"[，、；]")

# 兜底阈值（无作者档时）
FLOOR_IMPLICIT_RATIO = 0.30      # 低于此 → 翻译腔（显式连词过密）
CEIL_CONNECTIVE_DENSITY = 25.0   # /kCJK 高于此 → 连词洪流

MIN_CJK = 500


def _mode() -> str:
    m = (os.environ.get("PARATAXIS_IMPLICIT_LOGIC_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_baseline(project_root, style_path):
    """读作者风格档 quantitative.paratactic_baseline。
    优先 --style，再项目 _数据库 双档。北极星⑤第一权威。"""
    out = {"connective_density_mean": None, "connective_density_std": None,
           "implicit_ratio_mean": None, "implicit_ratio_std": None,
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
        pb = q.get("paratactic_baseline") or {}
        if isinstance(pb, dict):
            cdm = pb.get("connective_density_mean")
            cds = pb.get("connective_density_std")
            irm = pb.get("implicit_ratio_mean")
            irs = pb.get("implicit_ratio_std")
            if isinstance(cdm, (int, float)):
                out["connective_density_mean"] = float(cdm)
                out["connective_density_std"] = (float(cds)
                                                 if isinstance(cds, (int, float))
                                                 else None)
                out["from_author_profile"] = True
            if isinstance(irm, (int, float)):
                out["implicit_ratio_mean"] = float(irm)
                out["implicit_ratio_std"] = (float(irs)
                                             if isinstance(irs, (int, float))
                                             else None)
                out["from_author_profile"] = True
    return out


def _count_connectives(text: str):
    """返回 (multi_hits, single_hits) · multi=多字连词总数·single=句首/逗号后单字连词"""
    multi = sum(text.count(c) for c in ALL_MULTI_CONJ)
    # 单字连词：仅在句首/逗号/句末符号后扫
    single = 0
    for m in re.finditer(r"(?:^|[，。！？；、\n])\s*([" + "".join(LEADING_SINGLE_CONJ) + r"])",
                         text):
        single += 1
    return multi, single


def _implicit_chain_ratio(text: str):
    """扫『短句a，短句b』结构·两短句构成因果/顺承 chain 时是否含连词。
    简化启发式：
      - 按句末符号切句·每句内按逗号切短句
      - 短句对 (a,b)：若 b 含 ALL_MULTI_CONJ 或 LEADING_SINGLE_CONJ → explicit
      - 否则若 b 含动词性短句（≥3 CJK）→ implicit chain
    返回 (implicit_count, explicit_count, ratio)。"""
    implicit = 0
    explicit = 0
    sentences = SENTENCE_SPLIT.split(text)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 6:
            continue
        clauses = [c.strip() for c in COMMA_SPLIT.split(sent) if c.strip()]
        if len(clauses) < 2:
            continue
        for prev_c, cur_c in zip(clauses, clauses[1:]):
            if len(cur_c) < 3 or len(prev_c) < 3:
                continue
            # explicit: 当前 clause 起头/嵌入连词
            explicit_hit = False
            for w in ALL_MULTI_CONJ:
                if cur_c.startswith(w) or cur_c[:6].find(w) != -1:
                    explicit_hit = True
                    break
            if not explicit_hit:
                for w in LEADING_SINGLE_CONJ:
                    if cur_c.startswith(w):
                        explicit_hit = True
                        break
            if explicit_hit:
                explicit += 1
            else:
                implicit += 1
    total = implicit + explicit
    ratio = (implicit / total) if total else None
    return implicit, explicit, ratio


def scan(draft_path, project_root=None, style_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "paratactic_implicit_logic", "schema_version": "1.0",
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
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    baseline = _load_baseline(project_root, style_path)
    out["author_baseline"] = {
        "from_author_profile": baseline["from_author_profile"],
        "connective_density_mean": baseline["connective_density_mean"],
        "implicit_ratio_mean": baseline["implicit_ratio_mean"],
    }

    multi, single = _count_connectives(text)
    connective_density = round((multi + single) / cjk * 1000.0, 3)
    implicit, explicit, ratio = _implicit_chain_ratio(text)
    out["metrics"] = {
        "connective_density_per_kcjk": connective_density,
        "multi_connective_hits": multi,
        "single_connective_hits": single,
        "implicit_chain_count": implicit,
        "explicit_chain_count": explicit,
        "implicit_ratio": round(ratio, 3) if ratio is not None else None,
        "total_cjk": cjk,
    }

    messages = []

    # 探针 ① · connective density vs author band（z-band 优先 · 兜底地板）
    cdm, cds = baseline["connective_density_mean"], baseline["connective_density_std"]
    if cdm is not None and cds and cds > 1e-6:
        z = (connective_density - cdm) / cds
        out["metrics"]["connective_density_z"] = round(z, 2)
        if abs(z) >= 2.0:
            messages.append(
                f"连词密度 {connective_density}/kCJK 偏离作者基线 "
                f"{round(cdm,3)}±{round(cds,3)} {round(z,1)}σ "
                f"({'连词洪流(形合化)' if z>0 else '连词稀薄(刻意去连词)'})")
    elif connective_density >= CEIL_CONNECTIVE_DENSITY:
        messages.append(
            f"连词密度 {connective_density}/kCJK ≥ 通用上限 {CEIL_CONNECTIVE_DENSITY}"
            f"·形合化（hypotaxis）洪流·中文意合应削减成对连词")

    # 探针 ② · implicit causal chain ratio vs author band
    irm, irs = baseline["implicit_ratio_mean"], baseline["implicit_ratio_std"]
    if ratio is not None:
        if irm is not None and irs and irs > 1e-6:
            z = (ratio - irm) / irs
            out["metrics"]["implicit_ratio_z"] = round(z, 2)
            if z <= -2.0:
                messages.append(
                    f"隐含因果链比 {round(ratio,3)} 偏离作者基线 "
                    f"{round(irm,3)}±{round(irs,3)} {round(z,1)}σ·显式连词过密")
        elif ratio < FLOOR_IMPLICIT_RATIO:
            messages.append(
                f"隐含因果链比 {round(ratio,3)} < 通用地板 {FLOOR_IMPLICIT_RATIO}"
                f"·显式连词过密·翻译腔/学生作文感")

    if messages:
        msg = "·".join(messages)
        if mode == "active":
            out["violations"].append({
                "kind": "paratactic_off_author_band", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "metrics": out["metrics"],
                "_doc": "中文意合 vs 英文形合 · advisory · 作者档第一权威 · 绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] paratactic_implicit_logic: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="中文意合/形合 paratactic implicit logic · advisory · shadow")
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
