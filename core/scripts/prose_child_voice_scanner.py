#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_child_voice_scanner.py — 童声 concrete_noun_ratio + 词性指纹
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L58 P1 · 校园低龄/儿童视角)

【缺口】R10 联网调研(Oxford ORA lexical richness children's books + arXiv
2001.01863 Text Complexity concrete vs abstract + Dale-Chall + Tandfonline 2025
5-7 岁儿童叙事)：低龄/校园/童年视角【具象词高占比、低难度词】是核心质感。
LLM 默认偏抽象大词。此前【0 童声指纹检测】。

【做法 · 确定性词频近似】：
  1. 门控：cluster.pov_age < 18(优先 manifest) 或 genre ∈ {校园, 童年,
     coming_of_age, slice_of_life} 或作者档 child_voice_baseline 启用。
  2. 三指标：
     · concrete_abstract_noun_ratio：具象名词占比(身体部位/食物/动物/玩具/
       学校器物等高确定性词典)。
     · hard_word_rate：HSK 4 级以上代理 — 罕用抽象词词典命中率。
     · abstract_lead_sentence_ratio：句首为抽象词或介词长结构的句子占比。
  3. 作者档 child_voice_baseline ECDF z-band > 1.0σ → CHILD_VOICE_REGISTER_DRIFT
     advisory(z-band 简化为 ratio ≤ baseline*0.7)。

【北极星② / ⑤】纯 advisory · genre 不匹配 skip · 绝不 hard_gate。
  env CHILD_VOICE_MODE: off / shadow(默认) / active。

用法：python prose_child_voice_scanner.py <draft_path> [--project <root>]
       [--manifest <ch_manifest.json>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CHILD_VOICE_REGISTER_DRIFT"
MIN_CJK = 500

# 具象名词高确定性词典(身体/食物/动物/玩具/校园)
CONCRETE_NOUNS = re.compile(
    r"手|脚|眼|嘴|鼻|耳|头|脸|肚子|腿|"
    r"米饭|面条|苹果|香蕉|糖|蛋糕|面包|牛奶|水|饭|"
    r"狗|猫|鸟|鱼|兔子|蝴蝶|蚂蚁|"
    r"玩具|球|积木|笔|本子|铅笔|橡皮|书|课本|"
    r"桌子|椅子|黑板|教室|操场|书包|铅笔盒")

# 抽象/难词代理(HSK 4 以上 / 文学性词)
HARD_ABSTRACT_WORDS = re.compile(
    r"抽象|象征|隐喻|本质|存在|意识|超越|宿命|哲思|玄想|"
    r"辨证|逻辑|范畴|意识形态|建构|解构|形而上|主体性|客体性|"
    r"叙事|话语|权力|身份|认同|焦虑|疏离|彻悟")

# 抽象 lead words(句首抽象/介词长结构标志)
ABSTRACT_LEAD = re.compile(
    r"^(关于|对于|至于|然而|因此|无论|尽管|纵使|纵然|然则|"
    r"由此可见|换言之|或许|也许|事实上)")

CHILD_GENRES = {"school", "campus", "childhood", "coming_of_age",
                "校园", "童年", "slice_of_life_child"}


def _mode() -> str:
    m = (os.environ.get("CHILD_VOICE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _genre(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if isinstance(obj, dict):
        g = obj.get("genre") or obj.get("genre_pack")
        if isinstance(g, str):
            return g.strip().lower()
    return None


def _load_manifest(manifest_path):
    if not manifest_path:
        return None
    p = Path(manifest_path)
    return _read_json(p) if p.exists() else None


def _baseline(project_root):
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(p) if p.exists() else None
    if not isinstance(obj, dict):
        return None
    return obj.get("child_voice_baseline")


def compute_indices(text):
    sents = [s for s in re.split(r"[。！？……\n]+", text) if s.strip()]
    concrete = len(CONCRETE_NOUNS.findall(text))
    hard = len(HARD_ABSTRACT_WORDS.findall(text))
    cjk = _cjk_count(text)
    abstract_lead = sum(1 for s in sents if ABSTRACT_LEAD.match(s))
    sent_total = len(sents) or 1
    indices = {
        "concrete_per_kCJK": round(concrete / (cjk / 1000.0), 3)
        if cjk else 0,
        "hard_word_per_kCJK": round(hard / (cjk / 1000.0), 3)
        if cjk else 0,
        "abstract_lead_sentence_ratio": round(abstract_lead / sent_total, 4),
        "concrete_count": concrete,
        "hard_count": hard,
    }
    return indices


def _is_child_voice(manifest, genre, baseline):
    if isinstance(manifest, dict):
        pov_age = manifest.get("pov_age")
        if isinstance(pov_age, (int, float)) and pov_age < 18:
            return True
    if genre and any(c in genre for c in CHILD_GENRES):
        return True
    if isinstance(baseline, dict) and baseline.get("enabled") is True:
        return True
    return False


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    mode_env = _mode()
    out = {"scanner": "prose_child_voice", "schema_version": "1.0",
           "mode": mode_env, "code": ISSUE_CODE, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode_env == "off":
        return out
    manifest = _load_manifest(manifest_path)
    g = _genre(project_root)
    baseline = _baseline(project_root)
    if not _is_child_voice(manifest, g, baseline):
        out["note"] = "非童声 pov / 非低龄题材 · 跳过(北极星②)"
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out
    indices = compute_indices(text)
    out["indices"] = indices

    msg = None
    if isinstance(baseline, dict):
        # baseline 字段：concrete_per_kCJK / hard_word_per_kCJK_max /
        # abstract_lead_max
        cp = baseline.get("concrete_per_kCJK")
        hw_max = baseline.get("hard_word_per_kCJK_max")
        al_max = baseline.get("abstract_lead_max")
        drift_flags = []
        if isinstance(cp, (int, float)) and indices["concrete_per_kCJK"] < 0.7 * cp:
            drift_flags.append(
                f"concrete {indices['concrete_per_kCJK']} < 0.7x baseline {cp}")
        if isinstance(hw_max, (int, float)) and indices["hard_word_per_kCJK"] > hw_max:
            drift_flags.append(
                f"hard_word {indices['hard_word_per_kCJK']} > max {hw_max}")
        if (isinstance(al_max, (int, float))
                and indices["abstract_lead_sentence_ratio"] > al_max):
            drift_flags.append(
                f"abstract_lead {indices['abstract_lead_sentence_ratio']} > max {al_max}")
        out["drift_flags"] = drift_flags
        if drift_flags:
            msg = (f"童声漂移 {len(drift_flags)} 维: "
                   + "; ".join(drift_flags[:3]))
    else:
        # 兜底：hard 词命中 > concrete 命中
        if indices["hard_count"] > indices["concrete_count"] and indices["hard_count"] >= 3:
            msg = (f"童声漂移(兜底): hard 词 {indices['hard_count']} > "
                   f"concrete {indices['concrete_count']}")
    if msg:
        if mode_env == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "child_voice",
                "severity": "minor", "message": msg,
                "_doc": "advisory · 作者档 child_voice_baseline 第一权威 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] prose_child_voice: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="童声 concrete/hard/lead 三指标 (advisory · shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
