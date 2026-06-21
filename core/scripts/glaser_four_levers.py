#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""glaser_four_levers.py — Glaser 四杠杆救援诊断 · R24 W12 Batch-JJ · P1

【缺口 · Glaser 教育叙事 four levers for info-dump rescue】
Glaser 教学设计：info-dump 段救援 4 杠杆 → 任一 ≥1 即可激活：
  · dramatize     — 动作动词具体化（看/走/伸手/...）
  · emotionalize  — 情绪词具体化（怒/笑/心头一震/...）
  · personalize   — 具名角色介入（角色名 + 动作/对话）
  · fictionalize  — 具体物件 / 五感（桌/灯/光/气味/...）

【做法 · 确定性 · 零 LLM/零联网】
  · 识别 info-dump 段：长段（CJK ≥ 120）+ 对话密度低（无引号）
    + 抽象名词密度高（规则/制度/原理/系统/...）
  · 对每个 dump 段算 4 杠杆 0/1
  · 0/4 dump 段 → GLASER_LEVER_MISSING advisory + 建议补哪个最便宜
  · 1-2/4 dump 段 → GLASER_LEVER_THIN（info）
  · cluster 汇总 lever_coverage = 命中 ≥1 杠杆的 dump 段 / 总 dump 段
  · writer prompt D9.3 advisory（build_manifest 注入）

【三 advisory】
  · GLASER_LEVER_MISSING — 0/4 dump 段（救援失败）
  · GLASER_LEVER_THIN    — 1-2/4 dump 段（弱救援）
  · GLASER_LEVER_OK      — 全 dump 段 ≥3 杠杆（info）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  GLASER_LEVER_* 绝不进 audit_hub.HARD_GATE_CODES。

env GLASER_LEVERS_MODE: off / shadow（默认） / active
用法: python glaser_four_levers.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_MISSING = "GLASER_LEVER_MISSING"
ISSUE_CODE_THIN = "GLASER_LEVER_THIN"
ISSUE_CODE_OK = "GLASER_LEVER_OK"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

DUMP_CJK_MIN = 120
ABSTRACT_DENSITY_THRESHOLD = 0.04  # 抽象名词 / CJK 字符 阈值

_LEXICONS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-JJ·Glaser 四杠杆·占位词典",
    "dramatize": [  # 动作动词
        "走", "跑", "推", "拉", "踢", "拍", "握", "甩", "蹲", "起身",
        "翻", "跳", "踢", "敲", "扯", "踩", "倾身",
    ],
    "emotionalize": [  # 情绪词
        "怒", "笑", "哭", "悲", "喜", "惧", "焦灼", "颤抖", "心头一震",
        "猛地", "怔住", "失神",
    ],
    "personalize": [],  # 角色名 placeholder（动态从 character_index 拿）+ 占位代词锚
    "fictionalize": [  # 具体物件 + 五感
        "桌", "椅", "门", "窗", "杯", "灯", "石", "木", "铁", "光", "影",
        "气味", "风", "声", "刺", "凉", "热", "冷",
    ],
    "abstract": [  # 抽象名词高密度 = 疑 info-dump
        "规则", "制度", "原理", "系统", "结构", "理论", "概念", "层级",
        "机制", "条款", "公约", "定律", "范式", "范畴", "体系",
    ],
    # personalize 兜底代词触发
    "_personal_fallback": ["他", "她", "我"],
}

# 对话符号
_DIALOGUE_RE = re.compile(r"[「『“][^「」『』“”]+[」』”]")


def _mode() -> str:
    m = (os.environ.get("GLASER_LEVERS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p and p.strip()]


def _load_character_names(project_root) -> list[str]:
    if not project_root:
        return []
    cp = Path(project_root) / "_数据库" / "人物档案.json"
    if not cp.exists():
        return []
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            chars = data.get("characters", []) or list(data.values())
        else:
            chars = data
        names = []
        for c in chars:
            if isinstance(c, dict):
                n = c.get("name") or c.get("姓名")
                if n and isinstance(n, str):
                    names.append(n)
        return names
    except (OSError, json.JSONDecodeError):
        return []


def _hit_lever(paragraph: str, lever_words: list[str]) -> int:
    for w in lever_words:
        if w and w in paragraph:
            return 1
    return 0


def _is_info_dump(paragraph: str) -> bool:
    cjk = _cjk_count(paragraph)
    if cjk < DUMP_CJK_MIN:
        return False
    if _DIALOGUE_RE.search(paragraph):
        return False
    # 抽象名词密度
    abstract_hits = sum(paragraph.count(w) for w in _LEXICONS["abstract"])
    density = abstract_hits / max(cjk, 1)
    return density >= ABSTRACT_DENSITY_THRESHOLD


def _cheapest_missing_lever(scored: dict) -> str | None:
    """0/4 段建议补哪个最便宜：fictionalize > dramatize > emotionalize > personalize"""
    priority = ("fictionalize", "dramatize", "emotionalize", "personalize")
    for lev in priority:
        if scored.get(lev, 0) == 0:
            return lev
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "glaser_four_levers", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": _LEXICONS.get("_placeholder", True),
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 1000:
        out["note"] = "草稿太短·跳过"
        return out

    char_names = _load_character_names(project_root)
    personalize_words = list(char_names) + _LEXICONS["_personal_fallback"]

    paragraphs = _split_paragraphs(text)
    dump_segments = []
    for idx, p in enumerate(paragraphs):
        if not _is_info_dump(p):
            continue
        scored = {
            "dramatize": _hit_lever(p, _LEXICONS["dramatize"]),
            "emotionalize": _hit_lever(p, _LEXICONS["emotionalize"]),
            "personalize": _hit_lever(p, personalize_words),
            "fictionalize": _hit_lever(p, _LEXICONS["fictionalize"]),
        }
        total = sum(scored.values())
        dump_segments.append({
            "paragraph_idx": idx,
            "cjk": _cjk_count(p),
            "scored": scored,
            "total_levers": total,
            "cheapest_missing": _cheapest_missing_lever(scored) if total < 4 else None,
            "preview": p[:60],
        })

    total_dumps = len(dump_segments)
    if total_dumps == 0:
        out["note"] = "无 info-dump 段"
        out["dump_count"] = 0
        return out

    missing_segs = [d for d in dump_segments if d["total_levers"] == 0]
    thin_segs = [d for d in dump_segments if 1 <= d["total_levers"] <= 2]
    full_segs = [d for d in dump_segments if d["total_levers"] >= 3]
    coverage = (total_dumps - len(missing_segs)) / total_dumps

    out.update({
        "cjk": cjk,
        "dump_count": total_dumps,
        "missing_count": len(missing_segs),
        "thin_count": len(thin_segs),
        "ok_count": len(full_segs),
        "lever_coverage": round(coverage, 3),
        "dump_segments": dump_segments[:10],  # 限 10 条
    })

    flags = []
    if missing_segs:
        flags.append({
            "code": ISSUE_CODE_MISSING,
            "msg": (f"{len(missing_segs)}/{total_dumps} info-dump 段 0/4 杠杆"
                    f"·建议补 fictionalize/dramatize"),
            "severity": "minor",
        })
    if thin_segs:
        flags.append({
            "code": ISSUE_CODE_THIN,
            "msg": f"{len(thin_segs)}/{total_dumps} info-dump 段 1-2/4 杠杆·弱救援",
            "severity": "info",
        })
    if not missing_segs and not thin_segs:
        flags.append({
            "code": ISSUE_CODE_OK,
            "msg": f"全 {total_dumps} info-dump 段 ≥3 杠杆·救援充分",
            "severity": "info",
        })

    msg = "·".join(f["msg"] for f in flags)
    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "glaser_four_levers",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": "R24 W12 Batch-JJ·Glaser·advisory·绝不 hard_gate"})
        any_minor = any(v["severity"] == "minor" for v in out["violations"])
        out["verdict"] = "FAIL_MINOR" if any_minor else "PASS"
        out["warning"] = msg if any_minor else None
    elif mode == "shadow" and missing_segs:
        print(f"[SHADOW] glaser_four_levers: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Glaser 四杠杆 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
