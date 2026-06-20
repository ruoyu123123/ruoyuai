#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""genre_dominance_scanner.py — 题材主副 pack 标记密度比漂移
(advisory · cluster · 2026-06-20 R11 W6 MODEST)

【缺口】题材融合(多 pack)时 LLM 经常把副 pack 标记密度顶过主 pack，或主 pack 标记
"挨饿"(starved)·或两 pack 标记分布过平(Gini<0.15)看不出主副。

【做法 · 词袋密度·零 LLM】
  1. 读 author_genre_packs(作者档 author_profile.author_genre_packs · 或 manifest.genre)
  2. 多 pack 时读 fusion_declaration(_数据库/fusion_declaration.json) 主副比 dominance_ratio
     默认 0.6/0.4
  3. 加载各 pack 的 marker_lexicon.json(30-80 token 判别词袋)
  4. 按场景(段落空行切)计算每 pack token-density
  5. 三 advisory:
     GENRE_DOMINANCE_INVERSION（副 pack 在 ≥2 scene 反超主 pack）
     GENRE_PRIMARY_STARVED（主 pack 总占比 < 0.15）
     GENRE_BLEND_FLAT（Gini 系数 < 0.15·分布过平）

【北极星】② 作者档第一权威·shadow 默认·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("GENRE_DOMINANCE_INVERSION", "GENRE_PRIMARY_STARVED", "GENRE_BLEND_FLAT")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent / "lexicons" / "genre_markers"


def _mode() -> str:
    m = (os.environ.get("GENRE_DOMINANCE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_genre_packs(project_root) -> list:
    if not project_root:
        return []
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(obj, dict):
        return []
    packs = obj.get("author_genre_packs", [])
    if isinstance(packs, list):
        return [str(p_).strip() for p_ in packs if isinstance(p_, str) and p_.strip()]
    return []


def _read_fusion_declaration(project_root) -> dict | None:
    if not project_root:
        return None
    p = Path(project_root) / "_数据库" / "fusion_declaration.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return obj if isinstance(obj, dict) else None


def _load_marker_lexicon(pack: str) -> list:
    f = _LEXICON_PATH / f"{pack}.json"
    if not f.exists():
        return []
    try:
        obj = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(obj, dict):
        return [str(t) for t in obj.get("markers", []) if isinstance(t, str)]
    if isinstance(obj, list):
        return [str(t) for t in obj if isinstance(t, str)]
    return []


def _scene_density(scene: str, markers: list) -> float:
    if not scene.strip() or not markers:
        return 0.0
    k = max(1, _cjk_count(scene) / 1000.0)
    hits = sum(scene.count(t) for t in markers)
    return round(hits / k, 4)


def _gini(values: list) -> float:
    """简化 Gini 系数（0=完全均匀，1=完全不均）"""
    if not values:
        return 0.0
    vals = sorted(v for v in values if v >= 0)
    n = len(vals)
    cum = sum(vals)
    if cum == 0:
        return 0.0
    idx = sum((i + 1) * v for i, v in enumerate(vals))
    return round((2 * idx) / (n * cum) - (n + 1) / n, 4)


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "genre_dominance", "schema_version": "1.0", "mode": mode,
           "gate_level": "advisory", "violations": [], "verdict": "PASS",
           "warning": None}
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
    packs = _read_genre_packs(project_root)
    if len(packs) < 2:
        out["note"] = "无多 pack 声明·跳过(融合题材专用)"
        return out
    out["author_genre_packs"] = packs
    fusion = _read_fusion_declaration(project_root) or {}
    dominance = fusion.get("dominance_ratio") or {}
    primary = fusion.get("primary") or packs[0]
    out["primary_pack"] = primary
    out["dominance_ratio"] = dominance

    # 切场景
    scenes = [s for s in re.split(r"\n\s*\n+", text) if _cjk_count(s) >= 80]
    if len(scenes) < 2:
        out["note"] = "场景数 <2·跳过"
        return out

    # 每 scene 算每 pack token-density
    per_pack_total = {p: 0.0 for p in packs}
    per_scene = []
    inversion_count = 0
    for sc in scenes:
        ds = {}
        for p in packs:
            markers = _load_marker_lexicon(p)
            ds[p] = _scene_density(sc, markers)
            per_pack_total[p] += ds[p]
        if primary in ds:
            primary_d = ds[primary]
            top_other = max((v for k, v in ds.items() if k != primary), default=0)
            if top_other > primary_d and top_other > 0:
                inversion_count += 1
        per_scene.append(ds)
    out["per_scene_density"] = per_scene
    out["per_pack_total"] = {k: round(v, 4) for k, v in per_pack_total.items()}

    total = sum(per_pack_total.values()) or 1.0
    primary_share = per_pack_total.get(primary, 0) / total
    out["primary_share"] = round(primary_share, 4)
    gini = _gini(list(per_pack_total.values()))
    out["gini"] = gini

    flags = []
    if inversion_count >= 2:
        flags.append({"code": "GENRE_DOMINANCE_INVERSION",
                      "msg": f"副 pack 在 {inversion_count} 个 scene 反超主 pack {primary}"})
    if primary_share < 0.15:
        flags.append({"code": "GENRE_PRIMARY_STARVED",
                      "msg": f"主 pack {primary} 总占比 {primary_share:.3f} < 0.15"})
    if gini < 0.15:
        flags.append({"code": "GENRE_BLEND_FLAT",
                      "msg": f"题材融合 Gini={gini} < 0.15·分布过平看不出主副"})
    out["flags"] = flags
    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "genre_dominance", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "_doc": "题材主副比·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] genre_dominance: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="题材主副 pack 漂移(advisory)")
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
