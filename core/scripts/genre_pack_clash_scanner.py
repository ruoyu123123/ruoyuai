#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""genre_pack_clash_scanner.py — pack 间已知冲突两端峰值 advisory
(shadow · cluster · 2026-06-20 R11 W6 MODEST 占位)

【缺口】两 pack clash 在 cluster 内呈现"两端峰值"分布(scene A 全主 pack / scene B 全副 pack)
缺少融合·与 dominance_scanner 正交(那个查整体平均·此处查 clash 维度峰值二态分布)

【做法】
  1. 取 author_genre_packs 2-combination 命中 registry 的 axis(emotional_intensity_baseline /
     stakes_persistence / metaphysics_register 等)
  2. 每 scene 给 axis 打分(简化为 marker_density 差)
  3. 若分布双峰(top 1/3 全 pack A 主导 + bottom 1/3 全 pack B 主导 + 中段无过渡)
     → advisory CLASH_UNRESOLVED
  4. 作者档 author_fusion_resolution 覆盖 registry 时 skip

【北极星】② 作者档显式 fusion_resolution > registry seed·shadow 默认占位·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CLASH_UNRESOLVED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent / "lexicons" / "genre_markers"


def _mode() -> str:
    m = (os.environ.get("GENRE_PACK_CLASH_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_packs_and_override(project_root):
    if not project_root:
        return [], {}
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return [], {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], {}
    if not isinstance(obj, dict):
        return [], {}
    packs = obj.get("author_genre_packs", []) or []
    override = obj.get("author_fusion_resolution", {}) or {}
    return [str(p_).lower() for p_ in packs if isinstance(p_, str)], override


def _load_marker_lexicon(pack):
    f = _LEXICON_PATH / f"{pack}.json"
    if not f.exists():
        return []
    try:
        obj = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(obj, dict):
        return [str(t) for t in obj.get("markers", []) if isinstance(t, str)]
    return []


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "genre_pack_clash", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory",
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
    packs, override = _read_packs_and_override(project_root)
    if len(packs) < 2:
        out["note"] = "无多 pack 声明·跳过"
        return out
    try:
        import load_clash_registry as _lcr
    except ImportError:
        out["note"] = "registry loader 缺·跳过"
        return out
    hints = _lcr.resolve_clashes(packs, override)
    out["fusion_resolution_hints"] = hints
    if not hints:
        out["note"] = "无命中 registry clash·跳过"
        return out

    scenes = [s for s in re.split(r"\n\s*\n+", text) if _cjk_count(s) >= 80]
    if len(scenes) < 4:
        out["note"] = "场景 <4·二态分布判定不充分·跳过"
        return out
    flags = []
    for h in hints:
        pa, pb = h["pair"][0], h["pair"][1]
        ma, mb = _load_marker_lexicon(pa), _load_marker_lexicon(pb)
        if not ma or not mb:
            continue
        scores = []
        for sc in scenes:
            k = max(1, _cjk_count(sc) / 1000.0)
            da = sum(sc.count(t) for t in ma) / k
            db = sum(sc.count(t) for t in mb) / k
            scores.append({"scene_idx": len(scores), "da": round(da, 3),
                           "db": round(db, 3), "diff": round(da - db, 3)})
        if not scores:
            continue
        # 双峰检测：top 1/3 平均 diff > 1，bottom 1/3 平均 diff < -1
        n = len(scores)
        top = sorted(scores, key=lambda s: -s["diff"])[:max(1, n // 3)]
        bot = sorted(scores, key=lambda s: s["diff"])[:max(1, n // 3)]
        top_mu = sum(s["diff"] for s in top) / len(top)
        bot_mu = sum(s["diff"] for s in bot) / len(bot)
        if top_mu > 1 and bot_mu < -1:
            flags.append({
                "pair": h["pair"],
                "top_mu": round(top_mu, 3),
                "bot_mu": round(bot_mu, 3),
                "fusion_hint": h.get("fusion_hint"),
            })
    out["bipolar_flags"] = flags
    if flags:
        msg = f"题材融合两端峰值未化解：{[f['pair'] for f in flags]}"
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "genre_pack_clash", "severity": "minor",
                    "code": ISSUE_CODE,
                    "message": f"pair={f['pair']}·top diff={f['top_mu']}·bot diff={f['bot_mu']}",
                    "fusion_hint": f.get("fusion_hint"),
                    "_doc": "registry 冲突·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] genre_pack_clash: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="题材包 clash advisory(shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
