#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""otter_tail_scanner.py — 獭尾法残波收尾 advisory shadow · R23 W11 Batch-GG · P1

【缺口 · 古典评点（脂砚斋《红楼梦评》）】獭尾法：高潮过后留一条"残波"长尾·
情绪退潮+主角目标偏移+未来锚点呼应·古典/悠长余韵作者签名。当前 kicker / 章末强钩
（hook_strength）路径默认强势收尾·与「悠长余韵 / 古典留白」型作者档冲突。

【做法 · 确定性 · 零 LLM/零联网】
  · 末段 8% （cluster 草稿末段 ~ 800-2000 CJK）
  · 三特征联检：
    (a) 张力衰减 ≤ 60% — 末段相邻段「情绪标点（！？…）密度 / 短句独行率」相对
        cluster 全文 mean 衰减比例 ≤ 0.6 → 满足
    (b) 主角目标偏移 — 末段未出现 cluster.scope_summary / 主角 want/need 关键词
        或出现「转身 / 离开 / 不再 / 放下」 类 distancing 动词 ≥1 → 满足
    (c) 未来锚点 — 末段含「将来 / 日后 / 此后 / 多年后 / 春去秋来 / 时光 / 岁月 /
        从此」 等未来时序词 ≥1 → 满足
  · 三特征 ≥2 命中 = 獭尾形成 · 0 命中且作者档为悠长余韵 → 缺失 advisory

【三 advisory】
  · OTTER_TAIL_DETECTED       — 末段形成獭尾（≥2 特征命中）· 命中作者档推荐
  · OTTER_TAIL_MISSING        — 作者档 author_pacing_style ∈ {悠长余韵/古典留白}
                                但末段 0 特征命中 · 风格签名丢失
  · OTTER_TAIL_INCOMPATIBLE   — 作者档 author_pacing_style ∈ {强爽点} 同时命中 ≥2
                                特征 · 与 kicker 路由互斥 · info

【北极星】②④⑤ 作者档第一权威 · cluster · advisory shadow · 绝不 hard_gate
  OTTER_TAIL_* 绝不进 audit_hub.HARD_GATE_CODES。

env OTTER_TAIL_MODE: off / shadow（默认） / active
用法: python otter_tail_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import style_analyzer as sa  # noqa: E402
    _HAS_SA = True
except ImportError:
    _HAS_SA = False
    sa = None  # type: ignore

ISSUE_CODE_DETECTED = "OTTER_TAIL_DETECTED"
ISSUE_CODE_MISSING = "OTTER_TAIL_MISSING"
ISSUE_CODE_INCOMPATIBLE = "OTTER_TAIL_INCOMPATIBLE"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

TAIL_RATIO = 0.08          # 末段 8%
TAIL_MIN_CJK = 400
TAIL_MAX_CJK = 2400
TENSION_DECAY_TRIGGER = 0.60

_DISTANCING_LEX = ["转身", "离开", "不再", "放下", "释怀", "远去", "背影", "回头", "告别", "走出"]
_FUTURE_LEX = ["将来", "日后", "此后", "多年后", "春去秋来", "时光", "岁月",
               "从此", "未来", "若干年后", "数年后", "他日", "此去"]
_EMOTION_PUNCT_RE = re.compile(r"[！？…]+")

_AUTHOR_PACING_OTTER = {"悠长余韵", "古典留白", "悠长", "古典"}
_AUTHOR_PACING_KICKER = {"强爽点", "强冲突", "爽点", "高密度爽"}


def _mode() -> str:
    m = (os.environ.get("OTTER_TAIL_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_author_pacing_style(project_root) -> str | None:
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
            v = obj.get("author_pacing_style")
            if isinstance(v, str) and v.strip():
                return v.strip()
            v = obj.get("pacing_style")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _split_sentences(text: str) -> list[str]:
    if _HAS_SA:
        return sa.split_sentences(text)
    parts = re.split(r"(?<=[。！？…\n])", text)
    return [p.strip() for p in parts if p.strip()]


def _short_solo_ratio(sentences: list[str]) -> float:
    if not sentences:
        return 0.0
    short = sum(1 for s in sentences if 0 < _cjk_count(s) <= 12)
    return short / len(sentences)


def _emotion_punct_density(text: str) -> float:
    cjk = _cjk_count(text)
    if cjk == 0:
        return 0.0
    hits = len(_EMOTION_PUNCT_RE.findall(text))
    return hits / (cjk / 1000.0)


def _tail_slice(text: str) -> str:
    n = len(text)
    if n == 0:
        return ""
    tail_len = max(int(n * TAIL_RATIO), 0)
    # CJK guarded
    tail_cjk_target_min = TAIL_MIN_CJK
    tail_cjk_target_max = TAIL_MAX_CJK
    tail = text[-tail_len:] if tail_len else ""
    # 钳到 [400, 2400] CJK
    while _cjk_count(tail) < tail_cjk_target_min and tail_len < n:
        tail_len = min(n, tail_len + 200)
        tail = text[-tail_len:]
    while _cjk_count(tail) > tail_cjk_target_max and tail_len > tail_cjk_target_min:
        tail_len -= 200
        if tail_len < 0:
            tail_len = 0
        tail = text[-tail_len:] if tail_len else ""
    return tail


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "otter_tail", "schema_version": "1.0",
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
    if cjk < 2000:
        out["note"] = "草稿太短·跳过"
        return out

    tail = _tail_slice(text)
    tail_cjk = _cjk_count(tail)
    body = text[: -len(tail)] if tail and len(tail) < len(text) else text
    body_sents = _split_sentences(body)
    tail_sents = _split_sentences(tail)

    body_emotion_density = _emotion_punct_density(body)
    tail_emotion_density = _emotion_punct_density(tail)
    body_short_solo = _short_solo_ratio(body_sents)
    tail_short_solo = _short_solo_ratio(tail_sents)

    # (a) 张力衰减 ≤ 60%（即末段相对全文降到 60% 以下）
    tension_ratio_emo = (tail_emotion_density / body_emotion_density) if body_emotion_density > 0 else 1.0
    tension_ratio_solo = (tail_short_solo / body_short_solo) if body_short_solo > 0 else 1.0
    tension_decay_ratio = min(tension_ratio_emo, tension_ratio_solo)
    feat_a = tension_decay_ratio <= TENSION_DECAY_TRIGGER

    # (b) 主角目标偏移：distancing 动词命中
    distancing_hits = sum(tail.count(w) for w in _DISTANCING_LEX)
    feat_b = distancing_hits >= 1

    # (c) 未来锚点
    future_hits = sum(tail.count(w) for w in _FUTURE_LEX)
    feat_c = future_hits >= 1

    feature_count = sum([feat_a, feat_b, feat_c])
    pacing_style = (_read_author_pacing_style(project_root) or "").strip()
    is_otter_author = pacing_style in _AUTHOR_PACING_OTTER
    is_kicker_author = pacing_style in _AUTHOR_PACING_KICKER

    out.update({
        "cjk": cjk,
        "tail_cjk": tail_cjk,
        "tail_ratio": TAIL_RATIO,
        "features": {
            "tension_decay": {"hit": feat_a,
                              "ratio": round(tension_decay_ratio, 3),
                              "threshold": TENSION_DECAY_TRIGGER},
            "distancing_drift": {"hit": feat_b, "hits": distancing_hits},
            "future_anchor": {"hit": feat_c, "hits": future_hits},
        },
        "feature_count": feature_count,
        "author_pacing_style": pacing_style or None,
        "is_otter_author": is_otter_author,
        "is_kicker_author": is_kicker_author,
    })

    flags = []
    if feature_count >= 2:
        flags.append({"code": ISSUE_CODE_DETECTED,
                      "msg": f"獭尾形成（{feature_count}/3 特征命中）",
                      "severity": "info" if is_otter_author else "minor"})
        if is_kicker_author:
            flags.append({"code": ISSUE_CODE_INCOMPATIBLE,
                          "msg": f"作者档强爽点 vs 末段獭尾 · 路由互斥",
                          "severity": "minor"})
    elif is_otter_author and feature_count == 0:
        flags.append({"code": ISSUE_CODE_MISSING,
                      "msg": f"作者档「{pacing_style}」推荐獭尾 · 末段 0 特征命中 · 签名丢失",
                      "severity": "minor"})

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "otter_tail", "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "脂砚斋獭尾法·R23 W11 Batch-GG·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] otter_tail: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="獭尾法 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
