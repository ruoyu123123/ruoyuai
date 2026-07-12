#!/usr/bin/env python3
"""确定性合并角色 Voice DNA 与同栈 voice samples，并写合并 receipt。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from atomic_json import atomic_write_json


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"必须是 JSON object: {path}")
    return data


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_character(doc: dict, key: str) -> dict:
    characters = doc.get("characters")
    matches: list[dict] = []
    if isinstance(characters, list):
        matches = [item for item in characters if isinstance(item, dict)
                   and key in {str(item.get("id", "")), str(item.get("name", ""))}]
    elif isinstance(characters, dict):
        for item_key, item in characters.items():
            if isinstance(item, dict) and key in {
                str(item_key), str(item.get("id", "")), str(item.get("name", "")),
            }:
                matches.append(item)
    elif isinstance(doc.get(key), dict):
        matches = [doc[key]]
    if len(matches) != 1:
        raise ValueError(f"目标角色必须唯一存在: {key}，匹配数={len(matches)}")
    return matches[0]


def merge_voice_pack(*, cards_path: Path, character: str, voice_dna_path: Path,
                     samples_path: Path, receipt_path: Path) -> dict:
    cards = _load(cards_path)
    voice_dna_doc = _load(voice_dna_path)
    samples = _load(samples_path)
    style = samples.get("style_samples")
    anti = samples.get("anti_samples")
    meta = samples.get("_meta")
    if not isinstance(style, list) or not style or not isinstance(anti, list) or not anti:
        raise ValueError("voice_samples 必须同时含非空 style_samples 与 anti_samples")
    required_meta = {"writer_mode", "claude_drafts_dir", "generated_by_model", "generated_by_profile"}
    if not isinstance(meta, dict) or any(not meta.get(key) for key in required_meta):
        raise ValueError("voice_samples._meta 同栈 provenance 不完整")
    voice_dna = voice_dna_doc.get("voice_dna", voice_dna_doc)
    if not isinstance(voice_dna, dict) or not voice_dna:
        raise ValueError("voice_dna 为空")
    target = _find_character(cards, character)
    existing = target.get("voice_pack") if isinstance(target.get("voice_pack"), dict) else {}
    voice_pack = {
        **existing,
        "style_samples": style,
        "anti_samples": anti,
        "banned_phrases": list(dict.fromkeys([
            *(existing.get("banned_phrases", []) if isinstance(existing.get("banned_phrases"), list) else []),
            *(samples.get("banned_phrases_candidates", []) if isinstance(samples.get("banned_phrases_candidates"), list) else []),
        ])),
        "_gen_provenance": meta,
    }
    target["voice_dna"] = voice_dna
    target["voice_pack"] = voice_pack
    atomic_write_json(cards_path, cards)
    receipt = {
        "version": 1,
        "character": character,
        "cards_path": str(cards_path.resolve()),
        "style_sample_count": len(style),
        "anti_sample_count": len(anti),
        "writer_mode": meta["writer_mode"],
        "gen_provenance": meta,
        "inputs": {
            "voice_dna": {"path": str(voice_dna_path.resolve()), "sha256": _digest(voice_dna_path)},
            "voice_samples": {"path": str(samples_path.resolve()), "sha256": _digest(samples_path)},
        },
    }
    atomic_write_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="确定性合并角色 voice_pack")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--character", required=True)
    parser.add_argument("--voice-dna", required=True, type=Path)
    parser.add_argument("--voice-samples", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        merge_voice_pack(
            cards_path=args.project / "_数据库" / "人物卡.json",
            character=args.character,
            voice_dna_path=args.voice_dna,
            samples_path=args.voice_samples,
            receipt_path=args.receipt,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] voice_pack merged: {args.receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
