#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""baxter_staging_density_scanner.py — Baxter 身体/空间/道具微调度密度 advisory · cluster · 2026-06-21 R20 W9 Batch-AA · P1

【缺口 · R20 MFA id 1】Stephen Baxter《The Art of Subtext》/ MFA workshop sheets：
对话场景里「talking heads(只对白没动作)」是 LLM 通病——读者不知道说话者
在做什么、在哪里、握着什么。Baxter staging = 在每段对白前后插入身体动作 +
空间调度 + 道具操作，让角色从『嘴在动』变成『人在场』。

【做法 · 确定性 · 零 LLM/零联网】
  · 词典 core/data/staging_lexicon_zh.json 四桶 staging cue
    (body_cue / space_cue / prop_cue / posture_shift · 各 ~20 条 placeholder)
  · 总 staging_per_kcjk = 四桶 hits 之和 / kCJK
  · 各桶 per_kcjk + 占比
  · per_character_staging_share = 读 _数据库/角色池.json 已知角色名·
    扫窗口 (角色名 ± 20 CJK 内有任何 staging cue 命中即计 1)
  · dialogue_tag_to_staging_ratio = （"<角色>X说" 类 tag 数）/(staging cue 总 hits)
    · >2.0 = talking heads ratio 失衡 · <0.3 = 戏台化(全动作零对话标签)

【三 advisory】
  · STAGING_THIN — 总 staging_per_kcjk < 兜底 4.0(默认) 或作者档 band 下限
  · STAGING_BUCKET_UNBALANCED — 任一桶占比 >65% 或 <5%(占比畸形)
  · STAGING_TALKING_HEADS — dialogue_tag_to_staging_ratio > 2.0

【gating · 不干涉模型判断】
  · dialogue_density > p50(占位：对话引号字符 / cjk > 0.10) + active_chars ≥ 2 时 active 报；
    其他场景仍跑指标但 shadow 默认不报 issue(由 _mode 控)。

【与既有 scanner 严格正交】
  · group_dialogue_balance 查群口戏轮次失衡 · 不查身体调度
  · physio_cue_diversity 查生理 cue 多样性(心跳/呼吸/脸红) · 不查身体动作/道具
  · indirect_characterization_ratio 查配角侧写主角 · 不查 staging
  本 scanner = Baxter staging 唯一覆盖。

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  STAGING_THIN / STAGING_BUCKET_UNBALANCED / STAGING_TALKING_HEADS 绝不进 audit_hub.HARD_GATE_CODES。

env BAXTER_STAGING_MODE: off / shadow(默认) / active
用法: python baxter_staging_density_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_THIN = "STAGING_THIN"
ISSUE_CODE_BUCKET = "STAGING_BUCKET_UNBALANCED"
ISSUE_CODE_HEADS = "STAGING_TALKING_HEADS"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

DEFAULT_STAGING_PER_KCJK_LOW = 4.0
DEFAULT_BUCKET_HIGH_SHARE = 0.65
DEFAULT_BUCKET_LOW_SHARE = 0.05
DEFAULT_TALKING_HEADS_RATIO = 2.0
DEFAULT_DIALOGUE_DENSITY_P50 = 0.10
DEFAULT_ACTIVE_CHARS_MIN = 2

# 词典路径
_LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "staging_lexicon_zh.json"
# 对话 tag 模式：「X 说/道/笑」或 X 字+ 说/道 + ：/，
_DIALOGUE_TAG_PAT = re.compile(r"[一-鿿]{1,4}(?:说道|说|笑道|道|呢喃|喃喃|低声|喝道|怒道|问道)[：，:,“\"”]")
_QUOTE_CHARS = "“”\"「」"


def _mode() -> str:
    m = (os.environ.get("BAXTER_STAGING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _load_lexicon() -> dict:
    try:
        return json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 极简兜底
        return {
            "_placeholder": True,
            "buckets": {
                "body_cue": ["抬头", "低头", "皱眉"],
                "space_cue": ["走到", "退后", "转身"],
                "prop_cue": ["拿起", "放下", "递给"],
                "posture_shift": ["坐下", "站起", "俯身"],
            }
        }


def _read_author_baseline(project_root) -> dict | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "作者风格_FINAL.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            sb = obj.get("staging_baseline")
            if isinstance(sb, dict):
                return sb
    return None


def _read_character_names(project_root) -> list[str]:
    if not project_root:
        return []
    db = Path(project_root) / "_数据库"
    pool = db / "角色池.json"
    names: list[str] = []
    if not pool.exists():
        return names
    try:
        obj = json.loads(pool.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return names
    # 兼容多种 schema
    candidates = []
    if isinstance(obj, dict):
        if isinstance(obj.get("emerged"), list):
            candidates.extend(obj["emerged"])
        if isinstance(obj.get("main"), list):
            candidates.extend(obj["main"])
        if isinstance(obj.get("characters"), list):
            candidates.extend(obj["characters"])
    for c in candidates:
        if isinstance(c, str):
            names.append(c)
        elif isinstance(c, dict):
            n = c.get("name") or c.get("id")
            if isinstance(n, str):
                names.append(n)
    return [n for n in names if n and len(n) <= 8]


def _count_bucket_hits(text: str, terms: list[str]) -> int:
    total = 0
    for term in terms:
        if not term:
            continue
        total += text.count(term)
    return total


def _dialogue_density(text: str) -> float:
    if not text:
        return 0.0
    qc = sum(1 for ch in text if ch in _QUOTE_CHARS)
    cjk = _cjk_count(text)
    return (qc / cjk) if cjk else 0.0


def _per_character_staging_share(text: str, names: list[str], all_cues: list[str]) -> dict:
    if not names:
        return {"chars_total": 0, "with_staging": 0, "share": 0.0, "active_chars": 0}
    active_chars = 0
    with_staging = 0
    char_hits: dict[str, int] = {}
    for n in names:
        idx = 0
        char_pos = []
        while True:
            i = text.find(n, idx)
            if i < 0:
                break
            char_pos.append(i)
            idx = i + len(n)
        if not char_pos:
            continue
        active_chars += 1
        # 任一出现 + ±20 char 内有 cue → 算 staging
        hit_for_char = 0
        for p in char_pos:
            window = text[max(0, p - 20): p + len(n) + 20]
            if any(c and c in window for c in all_cues):
                hit_for_char += 1
        char_hits[n] = hit_for_char
        if hit_for_char > 0:
            with_staging += 1
    total_appearances = sum(1 for _ in names) if names else 1
    return {
        "chars_total": len(names),
        "with_staging": with_staging,
        "share": round(with_staging / max(1, active_chars), 3) if active_chars else 0.0,
        "active_chars": active_chars,
        "per_char_hits": char_hits,
    }


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "baxter_staging", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory",
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    lex = _load_lexicon()
    buckets = lex.get("buckets") or {}
    bucket_hits = {bn: _count_bucket_hits(text, terms) for bn, terms in buckets.items()}
    total_hits = sum(bucket_hits.values())
    per_kcjk = round(total_hits / (cjk / 1000.0), 3) if cjk else 0.0
    bucket_share = {bn: (h / total_hits if total_hits else 0.0) for bn, h in bucket_hits.items()}

    dial_density = _dialogue_density(text)
    dialogue_tag_count = len(_DIALOGUE_TAG_PAT.findall(text))
    tag_to_staging_ratio = (
        round(dialogue_tag_count / total_hits, 3) if total_hits else (float("inf") if dialogue_tag_count else 0.0)
    )

    all_cues = [t for bn, terms in buckets.items() for t in (terms or [])]
    char_names = _read_character_names(project_root)
    per_char = _per_character_staging_share(text, char_names, all_cues)

    baseline = _read_author_baseline(project_root)
    staging_low = DEFAULT_STAGING_PER_KCJK_LOW
    bucket_high = DEFAULT_BUCKET_HIGH_SHARE
    bucket_low = DEFAULT_BUCKET_LOW_SHARE
    heads_max = DEFAULT_TALKING_HEADS_RATIO
    dial_p50 = DEFAULT_DIALOGUE_DENSITY_P50
    active_min = DEFAULT_ACTIVE_CHARS_MIN
    baseline_source = "fallback"
    if isinstance(baseline, dict):
        baseline_source = "author_profile"
        if isinstance(baseline.get("staging_per_kcjk_low"), (int, float)):
            staging_low = float(baseline["staging_per_kcjk_low"])
        if isinstance(baseline.get("bucket_share_high"), (int, float)):
            bucket_high = float(baseline["bucket_share_high"])
        if isinstance(baseline.get("bucket_share_low"), (int, float)):
            bucket_low = float(baseline["bucket_share_low"])
        if isinstance(baseline.get("talking_heads_ratio_max"), (int, float)):
            heads_max = float(baseline["talking_heads_ratio_max"])
        if isinstance(baseline.get("dialogue_density_p50"), (int, float)):
            dial_p50 = float(baseline["dialogue_density_p50"])

    # gating: dialogue_density > p50 + active_chars ≥ active_min
    dialogue_gated = (dial_density > dial_p50) and (per_char.get("active_chars", 0) >= active_min)
    out.update({
        "cjk": cjk,
        "bucket_hits": bucket_hits,
        "bucket_share": {bn: round(v, 3) for bn, v in bucket_share.items()},
        "staging_per_kcjk": per_kcjk,
        "total_staging_hits": total_hits,
        "dialogue_density": round(dial_density, 4),
        "dialogue_tag_count": dialogue_tag_count,
        "tag_to_staging_ratio": tag_to_staging_ratio,
        "per_character": per_char,
        "dialogue_gated": dialogue_gated,
        "baseline_source": baseline_source,
        "baseline": {
            "staging_per_kcjk_low": staging_low,
            "bucket_share_high": bucket_high,
            "bucket_share_low": bucket_low,
            "talking_heads_ratio_max": heads_max,
            "dialogue_density_p50": dial_p50,
        }
    })

    flags = []
    if per_kcjk < staging_low:
        flags.append({"code": ISSUE_CODE_THIN,
                      "msg": f"staging_per_kcjk={per_kcjk} < {staging_low}·身体/空间/道具调度密度过低·talking heads 风险"})
    for bn, share in bucket_share.items():
        if total_hits >= 8 and share > bucket_high:
            flags.append({"code": ISSUE_CODE_BUCKET,
                          "msg": f"{bn} 桶占比={share:.2f} > {bucket_high}·单桶垄断·四桶失衡"})
            break
    if dialogue_gated and tag_to_staging_ratio != float("inf") and tag_to_staging_ratio > heads_max:
        flags.append({"code": ISSUE_CODE_HEADS,
                      "msg": (f"tag_to_staging_ratio={tag_to_staging_ratio} > {heads_max}·对话场景调度匮乏"
                              f"·active_chars={per_char.get('active_chars')}")})

    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "baxter_staging", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "Baxter《Art of Subtext》MFA staging · advisory · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] baxter_staging: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Baxter staging density advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
