#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chapter_title_couplet_scanner.py — 回目 huimu 对仗扫描
(advisory · cluster · 2026-06-20 R10 W6 Batch-O · L60 P1)

【缺口】R10 联网调研(百度百科章回体 + ACL 2024 NLP4DH 对偶 F1=0.43 + Literariness)
：传统章回体小说回目 huimu 对仗为核心工艺(诸如『林黛玉抛父进京都｜贾雨村夤缘复
旧职』)。古典 pastiche / huaben_zhanghui 题材若 LLM 默认产【单句标题】或【上下
不对仗】丢失体裁质感。此前【0 scanner 检测】。

【做法 · 确定性纯规则】：
  1. 门控：作者档 huimu_couplet=true / huaben_zhanghui_pastiche=true 才启用。
     或题材 ∈ {xianxia, xuanhuan, wuxia, historical} + classical_pastiche=true。
  2. 读章节标题(章节/第NNN章_*.txt 文件名 或 manifest titles)。
  3. 按 ｜ / / / 切两半。
  4. 三维对仗：① 字数对齐(±1) ② 句末标点对 ③ 简化 POS 序列相似度
     (jieba 不可用时退用字符长度比)。
  5. 三维任一脱钩 + 整体 broken_ratio > 0.4 → ZHANGHUI_HUIMU_PARALLELISM_BROKEN。

【北极星② / ⑤】纯 advisory · 作者档 huimu_couplet=false 默认 skip · 绝不 hard_gate。
  env CHAPTER_TITLE_COUPLET_MODE: off / shadow(默认) / active。

用法：python chapter_title_couplet_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ZHANGHUI_HUIMU_PARALLELISM_BROKEN"

SPLIT_PAT = re.compile(r"[｜｜\|\/　—]+")
CJK_PAT = re.compile(r"[一-鿿]")
PUNCT_END = re.compile(r"[？！。…，；]$")

CLASSICAL_GENRES = {"xianxia", "xuanhuan", "wuxia", "historical",
                    "classical", "huaben", "zhanghui"}


def _mode() -> str:
    m = (os.environ.get("CHAPTER_TITLE_COUPLET_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _enabled_by_profile(project_root) -> tuple:
    """(enabled, reason)。"""
    if not project_root:
        return False, "无 project_root"
    style_p = Path(project_root) / "_数据库" / "作者风格.json"
    obj = _read_json(style_p) if style_p.exists() else None
    if isinstance(obj, dict):
        if obj.get("huimu_couplet") is True:
            return True, "作者档 huimu_couplet=true"
        if obj.get("title_form") == "huimu_couplet":
            return True, "作者档 title_form=huimu_couplet"
        if obj.get("huaben_zhanghui_pastiche") is True:
            return True, "作者档 huaben_zhanghui_pastiche=true"
        g = (obj.get("genre") or obj.get("genre_pack") or "").lower()
        if g in CLASSICAL_GENRES and obj.get("classical_pastiche") is True:
            return True, f"题材 {g} + classical_pastiche"
    return False, "未启用"


def _collect_titles(project_root, draft_path=None):
    """从章节目录或 manifest 收集本 cluster 章节标题。fallback: draft 首行。"""
    titles = []
    if project_root:
        ch_dir = Path(project_root) / "章节"
        if ch_dir.exists():
            for p in sorted(ch_dir.glob("第*章*.txt")):
                # 文件名格式 "第001章_标题.txt"
                m = re.match(r"第(\d+)章[_\s\-：:]+(.+?)\.txt$", p.name)
                if m:
                    titles.append(m.group(2))
                else:
                    titles.append(p.stem)
    if not titles and draft_path:
        try:
            first_line = Path(draft_path).read_text(
                encoding="utf-8").splitlines()[:1]
            if first_line and first_line[0].strip():
                titles.append(first_line[0].strip()[:60])
        except OSError:
            pass
    return titles


def split_couplet(title):
    parts = [p.strip() for p in SPLIT_PAT.split(title) if p.strip()]
    if len(parts) == 2:
        return parts
    return None


def cjk_len(s):
    return len(CJK_PAT.findall(s))


def punct_match(a, b):
    ea = PUNCT_END.search(a)
    eb = PUNCT_END.search(b)
    if ea is None and eb is None:
        return True
    if ea and eb and ea.group(0) == eb.group(0):
        return True
    return False


def parallelism_score(a, b):
    """三维对仗评分 → bool 整体过关。"""
    la, lb = cjk_len(a), cjk_len(b)
    length_ok = abs(la - lb) <= 1 and la >= 2
    punct_ok = punct_match(a, b)
    # 字符 trigram overlap 作为 POS 替代(jieba 可选)
    sims = []
    try:
        import jieba.posseg as pseg  # type: ignore
        pa = [tag[:1] for _, tag in pseg.cut(a)]
        pb = [tag[:1] for _, tag in pseg.cut(b)]
        match = sum(1 for x, y in zip(pa, pb) if x == y)
        denom = max(len(pa), len(pb))
        pos_sim = match / denom if denom else 0
        sims.append(pos_sim)
    except Exception:
        # 字符长度比代替
        sims.append(min(la, lb) / max(la, lb) if max(la, lb) else 0)
    pos_ok = (sims[0] if sims else 0) >= 0.4
    return {"length_ok": length_ok, "punct_ok": punct_ok, "pos_ok": pos_ok,
            "pos_sim": round(sims[0], 3) if sims else None,
            "length_a": la, "length_b": lb}


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "chapter_title_couplet", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "verdict": "PASS", "violations": [], "warning": None}
    if mode == "off":
        return out
    enabled, reason = _enabled_by_profile(project_root)
    if not enabled:
        out["note"] = f"{reason} · 跳过(北极星②)"
        return out
    out["gate_reason"] = reason
    titles = _collect_titles(project_root, draft_path)
    out["title_count"] = len(titles)
    if not titles:
        out["note"] = "无章节标题可读 · 跳过"
        return out
    results = []
    broken = []
    no_split = []
    for t in titles:
        parts = split_couplet(t)
        if parts is None:
            no_split.append(t)
            continue
        score = parallelism_score(*parts)
        results.append({"title": t, **score})
        if not (score["length_ok"] and score["punct_ok"] and score["pos_ok"]):
            broken.append(t)
    out["evaluated_count"] = len(results)
    out["no_split_count"] = len(no_split)
    out["broken_count"] = len(broken)
    if results:
        broken_ratio = len(broken) / len(results)
        out["broken_ratio"] = round(broken_ratio, 3)
    else:
        broken_ratio = 0
    out["sample_broken"] = broken[:5]
    out["sample_no_split"] = no_split[:5]

    msg = None
    # 双触发：无切分占比过高 或 切分后 broken 过多
    if titles:
        no_split_ratio = len(no_split) / len(titles)
        out["no_split_ratio"] = round(no_split_ratio, 3)
        if no_split_ratio >= 0.5:
            msg = (f"回目体启用但 {no_split_ratio:.0%} 章节标题非对联结构"
                   f"({len(no_split)}/{len(titles)})·建议补 ｜ 分隔")
    if results and broken_ratio >= 0.4 and msg is None:
        msg = (f"回目对仗破损率 {broken_ratio:.0%} "
               f"({len(broken)}/{len(results)}) · 字数/标点/POS 任一脱钩")
    if msg:
        if mode == "active":
            out["violations"].append({
                "code": ISSUE_CODE, "kind": "couplet",
                "severity": "minor", "message": msg,
                "_doc": "advisory · 作者档可豁免 · 绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] chapter_title_couplet: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="回目对仗扫描 (advisory · shadow)")
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
