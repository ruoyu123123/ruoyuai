#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""load_clash_registry.py — 题材包冲突解析库（被 genre_pack_clash_scanner.py 消费·resolve_clashes() 是核心接口）
(R11 W6 MODEST · advisory · 2026-06-20)

【用法】
  from load_clash_registry import resolve_clashes
  hints = resolve_clashes(author_genre_packs=["apocalypse_survival", "romance"])

【北极星】② 作者档显式 fusion_resolution > registry seed·shadow scanner 占位
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

_REGISTRY_PATH = Path(__file__).resolve().parent / "trope_clash_registry.json"
_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _CACHE = {"clashes": []}
    return _CACHE


def list_clashes() -> list:
    return list(_load().get("clashes", []))


def resolve_clashes(author_genre_packs: list, author_fusion_override: dict | None = None) -> list:
    """取 author_genre_packs 的 2-combination，命中 registry 返回 fusion_resolution_hints

    author_fusion_override: {"<pack_a>+<pack_b>": "...override hint..."} 作者档显式覆盖
    """
    if not author_genre_packs or len(author_genre_packs) < 2:
        return []
    packs = [str(p).strip().lower() for p in author_genre_packs if isinstance(p, str)]
    seen_pairs = set()
    hints = []
    registry = list_clashes()
    for a, b in itertools.combinations(packs, 2):
        key = tuple(sorted([a, b]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        # 作者档覆盖
        ov_key = "+".join(key)
        if author_fusion_override and ov_key in author_fusion_override:
            hints.append({
                "pair": list(key),
                "source": "author_override",
                "fusion_hint": author_fusion_override[ov_key],
            })
            continue
        for entry in registry:
            if tuple(sorted(entry.get("pair", []))) == key:
                hints.append({
                    "pair": list(key),
                    "source": "registry_seed",
                    "axis": entry.get("axis"),
                    "tension": entry.get("tension"),
                    "fusion_hint": entry.get("fusion_hint"),
                })
                break
    return hints


if __name__ == "__main__":
    import sys
    packs = sys.argv[1:] or ["apocalypse_survival", "romance"]
    print(json.dumps(resolve_clashes(packs), ensure_ascii=False, indent=2))
