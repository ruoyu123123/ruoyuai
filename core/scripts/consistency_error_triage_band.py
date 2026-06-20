#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consistency_error_triage_band.py — 一致性错误三联分诊带
(advisory · cluster · 2026-06-20 R11 W6 STRONG)

【缺口】Microsoft Research arXiv:2603.05890 ConStory-Bench 2026-03 跨 5 模型实证：
长文本一致性错误集中在三联元层：(A) 段落熵代理(TTR+低频词+标点突变)突峰 +12-19%、
(B) splitter act2 区间 mid-band 集中度高、(C) 500-token 滑窗 ≥2 异 code 共现。

【做法 · 三件套·零依赖·零 LLM】：
  1. (A) 段落 entropy_proxy = α·TTR(token) + β·rare_word_ratio + γ·punct_burst
  2. (B) act2 mid-band 标记：取草稿字数 25%-75% 区间，标 in_act2_band=True
  3. (C) 读 audit_hub 产 issues(--audit-issues JSON) 滑窗 500-token 内 ≥2 异 code
     → advisory CONSISTENCY_HOTSPOT_COOCCURRENCE
  4. 产 triage_band_report.json 供 lessons feed

【北极星硬约束】绝不因高熵/共现升 hard_gate 或累加扣分(advisory 单条聚合 issue)·
作者档第一权威·shadow 默认

用法：python consistency_error_triage_band.py <draft_path> [--project <root>]
            [--audit-issues <issues.json>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CONSISTENCY_HOTSPOT_COOCCURRENCE"
WINDOW_TOKEN = 500  # 滑窗 token 数（中文按 CJK 字符近似）

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_PUNCT = re.compile(r"[，。！？；：、""''…—]")
_CJK = re.compile(r"[一-鿿]")


def _mode() -> str:
    m = (os.environ.get("CONSISTENCY_ERROR_TRIAGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _entropy_proxy(para: str) -> float:
    """段落 entropy_proxy = TTR + 低频词 + 标点突变"""
    chars = [c for c in para if "一" <= c <= "鿿"]
    if len(chars) < 20:
        return 0.0
    ttr = len(set(chars)) / len(chars)
    # 低频字符占比(出现次数 1 的字)
    freq = {}
    for c in chars:
        freq[c] = freq.get(c, 0) + 1
    rare = sum(1 for c, n in freq.items() if n == 1) / len(chars)
    # 标点突变（标点密度）
    punct_density = len(_PUNCT.findall(para)) / max(1, len(para))
    # 加权
    return round(0.4 * ttr + 0.4 * rare + 0.2 * (1 - abs(punct_density - 0.15) * 4), 4)


def _act2_band(text: str) -> dict:
    """标 act2 mid-band(25%-75% 字数区间)"""
    n = len(text)
    return {"start": n // 4, "end": 3 * n // 4, "total": n}


def _load_audit_issues(path):
    if not path:
        return []
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    # 兼容 list of dict 或 {issues:[...]}
    if isinstance(obj, list):
        return [i for i in obj if isinstance(i, dict) and i.get("code")]
    if isinstance(obj, dict):
        for k in ("issues", "all_issues"):
            v = obj.get(k)
            if isinstance(v, list):
                return [i for i in v if isinstance(i, dict) and i.get("code")]
    return []


def _scan_cooccurrence(text: str, issues: list) -> list:
    """500-token 滑窗 ≥2 异 code 共现"""
    if not issues:
        return []
    # 每 issue 取 char_offset/anchor 字段，无则用 issue 顺序近似
    placed = []
    for i, it in enumerate(issues):
        code = it.get("code")
        offset = None
        for k in ("char_start", "offset", "pos"):
            if isinstance(it.get(k), int):
                offset = it[k]
                break
        spans = it.get("anchor_spans") or []
        if spans and isinstance(spans, list) and isinstance(spans[0], dict):
            sp = spans[0].get("char_start")
            if isinstance(sp, int):
                offset = sp
        if offset is None:
            # 用 issue 序号当代理(等分)
            offset = int(len(text) * (i + 1) / max(2, len(issues) + 1))
        placed.append({"code": code, "offset": offset})
    placed.sort(key=lambda x: x["offset"])
    hotspots = []
    for i in range(len(placed)):
        window_codes = []
        for j in range(i, len(placed)):
            if placed[j]["offset"] - placed[i]["offset"] > WINDOW_TOKEN:
                break
            window_codes.append(placed[j])
        if len({w["code"] for w in window_codes}) >= 2:
            hotspots.append({
                "window_start": placed[i]["offset"],
                "window_end": placed[i]["offset"] + WINDOW_TOKEN,
                "codes": sorted({w["code"] for w in window_codes}),
            })
    # 去重(相邻窗口合并)
    merged = []
    for h in hotspots:
        if merged and h["window_start"] <= merged[-1]["window_end"]:
            merged[-1]["window_end"] = max(merged[-1]["window_end"], h["window_end"])
            merged[-1]["codes"] = sorted(set(merged[-1]["codes"]) | set(h["codes"]))
        else:
            merged.append(dict(h))
    return merged


def scan(draft_path, project_root=None, audit_issues_path=None) -> dict:
    mode = _mode()
    out = {"scanner": "consistency_error_triage_band", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    # (A) 段落熵代理
    paragraphs = [p for p in text.split("\n") if p.strip()]
    para_entropies = []
    for idx, p in enumerate(paragraphs):
        e = _entropy_proxy(p)
        if e > 0:
            para_entropies.append({"idx": idx, "entropy_proxy": e, "length": len(p)})
    para_entropies.sort(key=lambda x: -x["entropy_proxy"])
    top_entropy = para_entropies[:5]
    # (B) act2 band
    band = _act2_band(text)
    # 标 entropy 段是否落在 mid-band
    char_cursor = 0
    para_offsets = []
    for p in paragraphs:
        para_offsets.append(char_cursor)
        char_cursor += len(p) + 1
    for h in top_entropy:
        off = para_offsets[h["idx"]] if h["idx"] < len(para_offsets) else 0
        h["in_act2_band"] = band["start"] <= off <= band["end"]
    # (C) 共现热点
    issues = _load_audit_issues(audit_issues_path)
    hotspots = _scan_cooccurrence(text, issues)
    out["entropy_top"] = top_entropy
    out["act2_band"] = band
    out["audit_issue_count"] = len(issues)
    out["cooccurrence_hotspots"] = hotspots

    # 产 triage_band_report.json
    if project_root:
        try:
            tgt = Path(project_root) / "_数据库" / "triage_band_report.json"
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_text(json.dumps({
                "entropy_top": top_entropy,
                "act2_band": band,
                "cooccurrence_hotspots": hotspots,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            out["triage_report_path"] = str(tgt)
        except OSError:
            pass

    if hotspots:
        msg = (f"一致性错误分诊带：{len(hotspots)} 个 500-token 共现热点 "
               f"(codes 示例: {hotspots[0]['codes'][:4]})")
        if mode == "active":
            out["violations"].append({
                "kind": "consistency_triage", "severity": "minor",
                "message": msg, "hotspots": hotspots[:8],
                "_doc": "三联元定位·advisory 待裁决·绝不升 hard_gate 或累加扣分(单条聚合)",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] consistency_triage: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="一致性错误三联分诊带(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--audit-issues", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project, args.audit_issues)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
