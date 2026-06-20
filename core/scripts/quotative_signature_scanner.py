#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quotative_signature_scanner.py — Quotative/reporting-verb per-character 签名
(advisory · cluster · 2026-06-20 R9 W5 Batch-N P1)

【缺口】R9 W5 联网调研：人物对话的『说/道』动词选择（quotative · reporting-verb）是
characterization 强信号。Lin & Sims 2018《Detecting Speech Acts in Narrative Texts》+ 王雪辉
《现代汉语引述动词研究》等指出：每个角色的 quotative palette（冷笑/沉吟/低声/呢喃 8 桶
比例）是 idiolect 隐性指纹。LLM 默认全部角色用『说 / 道』两桶（neutral collapse）=对话失
人物感·全书所有角色像一人开口。此前 R3/R4 cross_scene_voice_drift 查同角色跨场景漂移
·R9 W5 character_distinctiveness 查 idiolect Gini ·**全系统无 scanner 查 quotative-verb
桶级签名**。

【做法 · 确定性零依赖 lexicons/quotative_verbs.json 60 词 8 桶】：
  1. 加载 lexicons/quotative_verbs.json（60 词·8 桶: neutral_say / neutral_dao /
     shouting / whispering / sneering / pondering / laughing / murmuring）。
  2. 扫 cluster 草稿对话行：识别『专名+quotative_verb+"内容"』或 quotative_verb 在
     ±20 字内紧邻 “XX” 引号段。归 speaker。
  3. 全 cluster 聚合：
     · author_palette = 全 cluster 8 桶分布（占比）·只用了 ≤2 桶 → AUTHOR_QUOTATIVE_PALETTE_COLLAPSE
     · per_speaker_distribution：每说话人 ≥3 个 quotative 才入统计。
       所有 pairwise cosine > 0.9 → CHARACTER_QUOTATIVE_HOMOGENIZED（角色间桶级签名雷同）。
  4. 输出 quotative_bias 字段（per-speaker top-3 桶）供 voice_pack 更新软提示。

【北极星② / ⑤ 顾问非法官】quotative palette 是工艺信号·爽文/快节奏题材天然偏 neutral·
  全 advisory，code AUTHOR_QUOTATIVE_PALETTE_COLLAPSE / CHARACTER_QUOTATIVE_HOMOGENIZED
  **绝不进 audit_hub.HARD_GATE_CODES**。env QUOTATIVE_SIGNATURE_MODE: off/shadow(默认)/active。

用法：python quotative_signature_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

ISSUE_AUTHOR = "AUTHOR_QUOTATIVE_PALETTE_COLLAPSE"
ISSUE_CHARACTER = "CHARACTER_QUOTATIVE_HOMOGENIZED"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_DEFAULT_LEXICON = Path(__file__).resolve().parent / "lexicons" / "quotative_verbs.json"

# 中文引号对（U+201C/U+201D 双引号 + 单引号 + 全角直角引号）
_QUOTE_LEFT = "“‘「『"
_QUOTE_RIGHT = "”’」』"

# 检测对话引用 + 周边 ±20 字回扫 quotative_verb
DIALOG_QUOTE_RE = re.compile(
    f"([{_QUOTE_LEFT}])([^{_QUOTE_LEFT}{_QUOTE_RIGHT}]{{1,300}}?)([{_QUOTE_RIGHT}])")
ATTRIBUTION_WINDOW = 20  # 引号外 ±N 字
PROPER_NAME_RE = re.compile(r"([一-龥]{2,4})")  # 简化的中文人名占位


def _mode() -> str:
    m = (os.environ.get("QUOTATIVE_SIGNATURE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_lexicon(path=None) -> dict:
    """返回 {bucket_name: set(verbs)}。失败 → 空。"""
    p = Path(path) if path else _DEFAULT_LEXICON
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    buckets = obj.get("buckets") if isinstance(obj, dict) else None
    if not isinstance(buckets, dict):
        return {}
    out = {}
    for name, payload in buckets.items():
        verbs = payload.get("verbs") if isinstance(payload, dict) else None
        if isinstance(verbs, list) and verbs:
            out[name] = set(v for v in verbs if isinstance(v, str) and v.strip())
    return out


def _build_verb_regex(lexicon: dict):
    """构建一次性 verb→bucket 映射 + alternation regex。长 verb 优先。"""
    verb_to_bucket = {}
    for bucket, verbs in lexicon.items():
        for v in verbs:
            verb_to_bucket[v] = bucket
    # 长 verb 在前避免被短 verb 截断匹配
    verbs_sorted = sorted(verb_to_bucket.keys(), key=lambda x: -len(x))
    if not verbs_sorted:
        return verb_to_bucket, None
    pattern = "(" + "|".join(re.escape(v) for v in verbs_sorted) + ")"
    return verb_to_bucket, re.compile(pattern)


def _known_names(project_root) -> set:
    """从 _数据库/人物卡.json 读已知角色名（作 attribution 锚点）。"""
    if not project_root:
        return set()
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return set()
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names = set()
    if isinstance(obj, dict):
        chars = obj.get("characters") or obj.get("人物") or obj.get("list") or []
        if isinstance(chars, dict):
            chars = list(chars.values())
        if isinstance(chars, list):
            for c in chars:
                if isinstance(c, dict):
                    n = c.get("name") or c.get("姓名") or c.get("id")
                    if isinstance(n, str) and n.strip():
                        names.add(n.strip())
                elif isinstance(c, str):
                    names.add(c.strip())
    return names


def _extract_speaker(text: str, quote_start: int, known_names: set) -> str:
    """从引号外 ±ATTRIBUTION_WINDOW 字窗口里抓 speaker。优先 known_names·退已知词典外
       的 2-4 字 CJK token。无 → 'UNKNOWN'。"""
    lo = max(0, quote_start - ATTRIBUTION_WINDOW)
    window = text[lo: quote_start]
    # 优先匹配已知名（最后出现的）
    if known_names:
        last_hit = None
        for name in known_names:
            for m in re.finditer(re.escape(name), window):
                if last_hit is None or m.end() > last_hit[1]:
                    last_hit = (name, m.end())
        if last_hit:
            return last_hit[0]
    # 退化：抓最后一个 2-4 字 CJK token
    matches = list(PROPER_NAME_RE.finditer(window))
    if matches:
        return matches[-1].group(0)
    return "UNKNOWN"


def _bucket_attribution(text: str, quote_start: int, quote_end: int,
                        verb_to_bucket: dict, verb_re) -> str:
    """在引号外 ±ATTRIBUTION_WINDOW 找 quotative verb（前后窗）→ bucket。"""
    if not verb_re:
        return ""
    lo = max(0, quote_start - ATTRIBUTION_WINDOW)
    hi = min(len(text), quote_end + ATTRIBUTION_WINDOW)
    window_before = text[lo: quote_start]
    window_after = text[quote_end: hi]
    # 优先 attribution 后置（中文典型『说道』在引号后）·前置兜底
    for window in (window_after, window_before):
        for m in verb_re.finditer(window):
            return verb_to_bucket.get(m.group(0), "")
    return ""


def _cosine(a: dict, b: dict) -> float:
    """两个 bucket→count 分布的 cosine 相似度（>=0）。"""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return round(dot / (na * nb), 4)


def analyze(text: str, lexicon: dict, known_names: set) -> dict:
    """扫文本·返回 (author_palette_counts, per_speaker_counts, dialog_total)。"""
    text = _strip_changes(text)
    verb_to_bucket, verb_re = _build_verb_regex(lexicon)
    author_counts = {b: 0 for b in lexicon}
    per_speaker = {}  # speaker -> {bucket: count}
    dialog_total = 0
    for m in DIALOG_QUOTE_RE.finditer(text):
        dialog_total += 1
        q_start, q_end = m.start(), m.end()
        bucket = _bucket_attribution(text, q_start, q_end,
                                     verb_to_bucket, verb_re)
        if not bucket:
            continue
        author_counts[bucket] = author_counts.get(bucket, 0) + 1
        speaker = _extract_speaker(text, q_start, known_names)
        if speaker not in per_speaker:
            per_speaker[speaker] = {}
        per_speaker[speaker][bucket] = per_speaker[speaker].get(bucket, 0) + 1
    return {"author_counts": author_counts, "per_speaker": per_speaker,
            "dialog_total": dialog_total}


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "quotative_signature", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out

    lexicon = _load_lexicon()
    if not lexicon:
        out["note"] = "无 lexicons/quotative_verbs.json·跳过"
        return out

    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out

    body = _strip_changes(draft)
    cjk = _cjk_count(body)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    known_names = _known_names(project_root)
    analysis = analyze(body, lexicon, known_names)
    author_counts = analysis["author_counts"]
    per_speaker = analysis["per_speaker"]
    total_attributed = sum(author_counts.values())

    out["dialog_total"] = analysis["dialog_total"]
    out["attributed_total"] = total_attributed
    out["author_palette"] = author_counts
    # voice_pack quotative_bias 输出（每说话人 top-3 桶）
    quotative_bias = {}
    for sp, dist in per_speaker.items():
        if sum(dist.values()) >= 3:
            top3 = sorted(dist.items(), key=lambda kv: -kv[1])[:3]
            quotative_bias[sp] = [{"bucket": b, "count": c} for b, c in top3]
    out["quotative_bias"] = quotative_bias

    violations = []

    # ① 作者 palette collapse（仅用了 ≤2 桶 + 总 attributed ≥ 8）
    used_buckets = [b for b, c in author_counts.items() if c > 0]
    if len(used_buckets) <= 2 and total_attributed >= 8:
        msg = (f"作者 quotative palette 坍缩: 全 cluster 仅用 {len(used_buckets)} 桶"
               f"({'/'.join(used_buckets)})·建议加入冷笑/沉吟/低声/笑道/呢喃等多桶")
        violations.append({
            "code": ISSUE_AUTHOR, "kind": "quotative_collapse",
            "severity": "minor", "message": msg,
            "used_buckets": used_buckets, "author_palette": author_counts,
            "_doc": "advisory·爽文/快节奏天然偏 neutral·绝不 hard_gate",
        })

    # ② 角色间 quotative cosine > 0.9 同质化（每角色 ≥3 quotative 才入对比）
    eligible = {sp: dist for sp, dist in per_speaker.items()
                if sum(dist.values()) >= 3 and sp != "UNKNOWN"}
    homogenized_pairs = []
    speakers = list(eligible.keys())
    for i in range(len(speakers)):
        for j in range(i + 1, len(speakers)):
            sim = _cosine(eligible[speakers[i]], eligible[speakers[j]])
            if sim > 0.9:
                homogenized_pairs.append({"a": speakers[i], "b": speakers[j],
                                          "cosine": sim})
    out["homogenized_pairs"] = homogenized_pairs

    if homogenized_pairs:
        pair_descs = [f"{p['a']}↔{p['b']}={p['cosine']}"
                      for p in homogenized_pairs[:3]]
        msg = (f"角色 quotative 签名同质化: {len(homogenized_pairs)} 对"
               f"({'·'.join(pair_descs)})·建议给不同人物分配独立 quotative 桶"
               f"(冷酷派→冷笑/沉默派→低声等)")
        violations.append({
            "code": ISSUE_CHARACTER, "kind": "quotative_homogenized",
            "severity": "minor", "message": msg,
            "homogenized_pairs": homogenized_pairs[:10],
            "_doc": "advisory·角色 voice 自由是创作选择·绝不 hard_gate",
        })

    if violations:
        if mode == "active":
            out["violations"] = violations
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = violations[0]["message"]
        else:
            for v in violations:
                print(f"[SHADOW] quotative_signature: {v['message']} — 不上报",
                      file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Quotative/reporting-verb per-character 签名(advisory)")
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
