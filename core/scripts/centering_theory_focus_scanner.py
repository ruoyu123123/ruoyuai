#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""centering_theory_focus_scanner.py — Grosz Centering Theory 4 转移类型 · 读者注意焦点

【缺口 · R18 W7 Batch-T·P1 · 2026-06-21】Grosz/Joshi/Weinstein 1995
《Computational Linguistics》Centering Theory + arxiv 2604.* Centering Theory 2026 应用
+ Walker 1998 CT 综述：
  读者阅读时维护一个注意焦点（attentional focus）。CT 把每句的实体表示拆成 ——
    Cf（forward-looking centers）：本句提到的全部实体（按显著度排序，首位 = Cp，
      preferred center）
    Cb（backward-looking center）：本句继承前句 Cf 中最高显著实体
  句间转移分 4 型（Walker 1998 重排）：
    continue：Cb(n) == Cb(n-1) 且 Cb(n) == Cp(n)
    retain：Cb(n) == Cb(n-1) 且 Cb(n) != Cp(n)
    smooth-shift：Cb(n) != Cb(n-1) 且 Cb(n) == Cp(n)
    rough-shift：Cb(n) != Cb(n-1) 且 Cb(n) != Cp(n)

  rough-shift 是读者认知负载最高的转移（焦点切+无新焦点延续）。LLM 默认产连续 Cf
  跳变 = rough-shift 密度过高 → 读者每句都要重建心智模型 = 阅读流断。

【与既有 scanner 显式去重】
  - R7 focalizer_perception_bounds：narrator 知觉边界（自体不可见/他人内心）
    本 scanner = 句间 Cf/Cb 实体焦点转移（不依赖 narrator 知觉）·正交
  - R6/R7 pov_consistency：scene 主导 POV 一致性
    本 scanner = 句级注意焦点漂移（同 POV 内也会 rough-shift）·正交
  - R18 character_belief_ledger：跨场景信念
    本 scanner = 句级 Cf/Cb（不依赖 belief）·正交

【两探针 · 确定性纯规则】
  ① rough_shift_density（per 1k CJK）
     - 每句抽 Cf 候选 = 主语前置名词 + 配角名命中（人物卡 + 占位代词他/她/它）
     - Cp = 句首位置 NP（启发：本句开头第一个出现的 Cf 候选）
     - Cb = 上一句 Cf ∩ 本句 Cf 中显著度最高者
     - 4 转移分类 + rough_shift_density
  ② transitions_distribution
     continue/retain/smooth/rough 占比 vs 作者基线

【作者档第一权威】
  quantitative.centering_baseline = {
    rough_shift_density_mean, rough_shift_density_std,
    rough_shift_density_max  # advisory 触发上限
  }

  无作者档兜底：rough_shift_density > 8.0 / kCJK → CENTERING_ROUGH_SHIFT_OVERLOAD

【北极星⑤】顾问非法官·全 advisory·env CENTERING_THEORY_FOCUS_MODE
  CENTERING_ROUGH_SHIFT_OVERLOAD 绝不进 HARD_GATE_CODES。

用法: python centering_theory_focus_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CENTERING_ROUGH_SHIFT_OVERLOAD"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 句切分（句末符号）
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？……])")

# 代词 Cf 候选（中文常见三人称 / 第一人称）
PRONOUNS = ("他", "她", "它", "我", "你", "他们", "她们", "它们", "我们", "你们")

# 对话引号剥离（CT 只在叙述层算）
DIALOGUE_SPAN = re.compile(r'["“「『][^"”」』\n]{0,300}["”」』]')

MIN_CJK = 500
FLOOR_ROUGH_SHIFT_DENSITY = 8.0     # /kCJK 兜底


def _mode() -> str:
    m = (os.environ.get("CENTERING_THEORY_FOCUS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_cast(project_root):
    """读人物卡 → 全部命名实体集合（主角+配角+alias）"""
    names = set()
    if not project_root:
        return names
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return names
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return names
    for c in (obj.get("characters", []) if isinstance(obj, dict) else []):
        if not isinstance(c, dict):
            continue
        nm = c.get("name", "")
        if nm:
            names.add(nm)
        for a in (c.get("aliases") or []):
            if a:
                names.add(a)
    return names


def _load_baseline(project_root):
    out = {"mean": None, "std": None, "max": None, "from_author_profile": False}
    if not project_root:
        return out
    for fname in ("作者风格_FINAL.json", "作者风格.json"):
        p = Path(project_root) / "_数据库" / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        q = data.get("quantitative") or {}
        cb = q.get("centering_baseline") or {}
        if isinstance(cb, dict):
            mn = cb.get("rough_shift_density_mean")
            sd = cb.get("rough_shift_density_std")
            mx = cb.get("rough_shift_density_max")
            if isinstance(mn, (int, float)):
                out["mean"] = float(mn)
                out["from_author_profile"] = True
            if isinstance(sd, (int, float)):
                out["std"] = float(sd)
            if isinstance(mx, (int, float)):
                out["max"] = float(mx)
        break
    return out


def _extract_cf(sentence: str, cast_names):
    """Cf = 本句实体列表（按出现顺序·首位是 Cp）。
    实体 = cast_names ∪ PRONOUNS · 不去对话内容（CT 含 turn 主语）。
    返回 [entity_str, ...] 按首次出现位置升序。"""
    hits = []  # [(pos, entity)]
    for nm in cast_names:
        idx = sentence.find(nm)
        if idx >= 0:
            hits.append((idx, nm))
    for pn in PRONOUNS:
        # 防止『他的』『她的』算两次：第一次出现位置即可
        idx = sentence.find(pn)
        if idx >= 0:
            # 防嵌入式『其他』『她家』等多义 → 简化保留高频代词独立出现
            # 但 PRONOUNS 已是常见单字代词·这里宽松保留
            hits.append((idx, pn))
    # 同 entity 出现多次只算第一次
    seen = set()
    cf = []
    for pos, ent in sorted(hits, key=lambda x: x[0]):
        if ent in seen:
            continue
        seen.add(ent)
        cf.append(ent)
    return cf


def _classify_transition(prev_cf, cur_cf):
    """计算本句相对前句的 Cb + transition 类型。
    返回 (cb, transition) · transition ∈ {continue, retain, smooth, rough, none}
    none：本句无 Cf 候选 / 前句无 Cf → 跳过。
    """
    if not prev_cf or not cur_cf:
        return None, "none"
    # Cb(n) = prev_cf 中显著度最高且 ∈ 本句 Cf（CT 经典定义）
    cur_set = set(cur_cf)
    cb = None
    for ent in prev_cf:           # 已按位置排序 = 显著度顺序
        if ent in cur_set:
            cb = ent
            break
    if cb is None:
        # 前句 Cf 完全无延续 → 强 shift
        return None, "rough"
    # 前句 Cb（递归近似：前句 Cf 第一位 = 前句 Cp ≈ Cb_pref）
    prev_cb = prev_cf[0]
    cp = cur_cf[0]
    if cb == prev_cb and cb == cp:
        return cb, "continue"
    if cb == prev_cb and cb != cp:
        return cb, "retain"
    if cb != prev_cb and cb == cp:
        return cb, "smooth"
    return cb, "rough"


def _scan_centering(text: str, cast_names):
    """切句 + 4 转移类型计数。返回 (transitions_dict, sentences_scanned)"""
    # 去对话内容降噪
    body = DIALOGUE_SPAN.sub("", text)
    sents = [s.strip() for s in SENTENCE_SPLIT_RE.split(body) if s.strip()]
    transitions = {"continue": 0, "retain": 0, "smooth": 0, "rough": 0, "none": 0}
    prev_cf = None
    scanned = 0
    for s in sents:
        cur_cf = _extract_cf(s, cast_names)
        if prev_cf is None:
            prev_cf = cur_cf if cur_cf else None
            continue
        _, kind = _classify_transition(prev_cf, cur_cf)
        transitions[kind] += 1
        scanned += 1
        if cur_cf:
            prev_cf = cur_cf
        # cur_cf 空时 prev_cf 不变（保留焦点）
    return transitions, scanned


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "centering_theory_focus", "schema_version": "1.0",
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

    cast = _load_cast(project_root)
    baseline = _load_baseline(project_root)
    transitions, scanned = _scan_centering(text, cast)
    rough = transitions["rough"]
    rough_density = round(rough / cjk * 1000.0, 3)
    out["metrics"] = {
        "rough_shift_density_per_kcjk": rough_density,
        "transitions": transitions,
        "sentences_scanned": scanned,
        "cast_size": len(cast),
        "total_cjk": cjk,
    }
    out["author_baseline"] = {
        "from_author_profile": baseline["from_author_profile"],
        "rough_shift_density_mean": baseline["mean"],
        "rough_shift_density_max": baseline["max"],
    }

    # 单句叙述无 Cf → none 占比过高时不判（场景全无人物·哑场）
    if scanned < 5:
        out["note"] = "可分析句数 <5·跳过"
        return out

    msg = None
    m, s, mx = baseline["mean"], baseline["std"], baseline["max"]
    if m is not None and s and s > 1e-6:
        z = (rough_density - m) / s
        out["metrics"]["rough_shift_z"] = round(z, 2)
        if z >= 2.0:
            msg = (f"rough_shift 密度 {rough_density}/kCJK 偏离作者基线 "
                   f"{round(m,3)}±{round(s,3)} {round(z,1)}σ·焦点漂移频繁·"
                   f"读者每句重建心智模型·阅读流断")
    elif mx is not None:
        if rough_density >= mx:
            msg = (f"rough_shift 密度 {rough_density}/kCJK ≥ 作者档上限 {mx}"
                   f"·焦点漂移频繁")
    elif rough_density >= FLOOR_ROUGH_SHIFT_DENSITY:
        msg = (f"rough_shift 密度 {rough_density}/kCJK ≥ 通用地板 "
               f"{FLOOR_ROUGH_SHIFT_DENSITY}·焦点漂移频繁·"
               f"建议补承接代词/同名实体延续焦点")

    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "centering_rough_shift_overload", "severity": "minor",
                "code": ISSUE_CODE, "message": msg,
                "metrics": out["metrics"],
                "_doc": "Grosz CT 4 转移·rough-shift 读者认知负载·"
                        "advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] centering_theory_focus: {msg} — 不上报",
                  file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Grosz CT 4 转移焦点·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
