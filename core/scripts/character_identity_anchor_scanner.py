#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_identity_anchor_scanner.py - stable identity anchor drift scanner.

Borrowed concept: moyin-creator keeps character identity anchors for visual
continuity. In ruoyuai this scanner turns that idea into prose continuity:
stable hair/eye/mark anchors must not drift in nearby narration.

Input:
  - draft text
  - _数据库/人物卡.json characters[].identity_anchors
  - optional fallback from appearance / locked_facts

Output is advisory only. The code must never be added to HARD_GATE_CODES.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CHARACTER_IDENTITY_ANCHOR_DRIFT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_WINDOW_RADIUS = 60

_HAIR_COLOR_GROUPS = {
    "black": ("黑发", "乌发", "墨发", "黑色头发", "乌黑头发"),
    "white": ("白发", "银发", "雪发", "白色头发", "银色头发"),
    "gold": ("金发", "金色头发", "金黄头发"),
    "red": ("红发", "赤发", "红色头发"),
    "brown": ("棕发", "褐发", "栗色头发", "棕色头发"),
}

_EYE_COLOR_GROUPS = {
    "black": ("黑眼", "黑眸", "黑瞳", "黑色眼睛", "黑色瞳孔"),
    "blue": ("蓝眼", "蓝眸", "蓝瞳", "蓝色眼睛", "蓝色瞳孔"),
    "green": ("绿眼", "绿眸", "绿瞳", "绿色眼睛", "绿色瞳孔"),
    "gold": ("金眼", "金眸", "金瞳", "金色眼睛", "金色瞳孔"),
    "gray": ("灰眼", "灰眸", "灰瞳", "灰色眼睛", "灰色瞳孔"),
    "red": ("红眼", "红眸", "赤瞳", "红色眼睛", "红色瞳孔"),
}

_MARK_GROUPS = {
    "scar": ("疤", "伤疤", "刀疤", "疤痕"),
    "mole": ("痣", "黑痣", "朱砂痣"),
    "birthmark": ("胎记", "印记"),
}

_TYPE_ALIASES = {
    "hair": "hair_color",
    "hair_color": "hair_color",
    "发色": "hair_color",
    "头发": "hair_color",
    "eye": "eye_color",
    "eyes": "eye_color",
    "eye_color": "eye_color",
    "瞳色": "eye_color",
    "眼睛": "eye_color",
    "mark": "mark",
    "scar": "mark",
    "birthmark": "mark",
    "疤痕": "mark",
    "胎记": "mark",
    "标记": "mark",
}


def _mode() -> str:
    mode = (os.environ.get("CHARACTER_IDENTITY_ANCHOR_MODE") or "shadow").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _as_text_list(value) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _norm_type(raw) -> str:
    key = str(raw or "").strip().lower()
    return _TYPE_ALIASES.get(key, key or "identity_phrase")


def _find_group(text: str, groups: dict[str, tuple[str, ...]]) -> str | None:
    for group, terms in groups.items():
        if any(term in text for term in terms):
            return group
    return None


def _terms_for_group(groups: dict[str, tuple[str, ...]], group: str | None) -> list[str]:
    if not group:
        return []
    return list(groups.get(group, ()))


def _conflict_terms(anchor_type: str, expected: str) -> list[str]:
    if anchor_type == "hair_color":
        groups = _HAIR_COLOR_GROUPS
    elif anchor_type == "eye_color":
        groups = _EYE_COLOR_GROUPS
    elif anchor_type == "mark":
        groups = _MARK_GROUPS
    else:
        return []

    expected_group = _find_group(expected, groups)
    conflicts: list[str] = []
    for group, terms in groups.items():
        if group != expected_group:
            conflicts.extend(terms)
    return conflicts


def _make_anchor(anchor_type: str, expected: str, *, aliases=None, forbidden=None, source="identity_anchors") -> dict | None:
    anchor_type = _norm_type(anchor_type)
    expected = str(expected or "").strip()
    if not expected:
        return None
    alias_terms = _as_text_list(aliases)
    forbidden_terms = _as_text_list(forbidden)
    forbidden_terms.extend(_conflict_terms(anchor_type, expected))
    # Preserve order while deduplicating.
    forbidden_terms = list(dict.fromkeys(t for t in forbidden_terms if t and t != expected and t not in alias_terms))
    return {
        "anchor_type": anchor_type,
        "expected": expected,
        "aliases": alias_terms,
        "forbidden": forbidden_terms,
        "source": source,
    }


def _anchors_from_phrase(phrase: str, source: str) -> list[dict]:
    anchors: list[dict] = []
    if not isinstance(phrase, str) or not phrase.strip():
        return anchors
    phrase = phrase.strip()
    if any(token in phrase for token in ("发", "头发")):
        group = _find_group(phrase, _HAIR_COLOR_GROUPS)
        if group:
            term = _terms_for_group(_HAIR_COLOR_GROUPS, group)[0]
            anchor = _make_anchor("hair_color", term, source=source)
            if anchor:
                anchors.append(anchor)
    if any(token in phrase for token in ("眼", "眸", "瞳")):
        group = _find_group(phrase, _EYE_COLOR_GROUPS)
        if group:
            term = _terms_for_group(_EYE_COLOR_GROUPS, group)[0]
            anchor = _make_anchor("eye_color", term, source=source)
            if anchor:
                anchors.append(anchor)
    group = _find_group(phrase, _MARK_GROUPS)
    if group:
        term = _terms_for_group(_MARK_GROUPS, group)[0]
        anchor = _make_anchor("mark", term, aliases=[phrase], source=source)
        if anchor:
            anchors.append(anchor)
    return anchors


def _parse_identity_anchors(raw) -> list[dict]:
    anchors: list[dict] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                anchor = _make_anchor(
                    key,
                    value.get("expected") or value.get("value") or value.get("text"),
                    aliases=value.get("aliases"),
                    forbidden=value.get("forbidden") or value.get("negative") or value.get("conflicts"),
                )
                if anchor:
                    anchors.append(anchor)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        anchor = _make_anchor(
                            item.get("type") or item.get("anchor_type") or key,
                            item.get("expected") or item.get("value") or item.get("text"),
                            aliases=item.get("aliases"),
                            forbidden=item.get("forbidden") or item.get("negative") or item.get("conflicts"),
                        )
                    else:
                        anchor = _make_anchor(key, item)
                    if anchor:
                        anchors.append(anchor)
            else:
                anchor = _make_anchor(key, value)
                if anchor:
                    anchors.append(anchor)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                anchor = _make_anchor(
                    item.get("type") or item.get("anchor_type"),
                    item.get("expected") or item.get("value") or item.get("text"),
                    aliases=item.get("aliases"),
                    forbidden=item.get("forbidden") or item.get("negative") or item.get("conflicts"),
                )
                if anchor:
                    anchors.append(anchor)
            elif isinstance(item, str):
                anchors.extend(_anchors_from_phrase(item, "identity_anchors"))
    return anchors


def _character_anchors(card: dict) -> list[dict]:
    anchors = _parse_identity_anchors(card.get("identity_anchors"))
    appearance = card.get("appearance")
    if isinstance(appearance, dict):
        for key in ("hair_color", "eye_color", "scar", "mark", "birthmark"):
            if key in appearance:
                anchor = _make_anchor(key, appearance.get(key), source="appearance")
                if anchor:
                    anchors.append(anchor)
    elif isinstance(appearance, str):
        anchors.extend(_anchors_from_phrase(appearance, "appearance"))

    for fact in _as_text_list(card.get("locked_facts")):
        anchors.extend(_anchors_from_phrase(fact, "locked_facts"))

    dedup: dict[tuple[str, str], dict] = {}
    for anchor in anchors:
        key = (anchor["anchor_type"], anchor["expected"])
        if key not in dedup:
            dedup[key] = anchor
        else:
            old = dedup[key]
            old["aliases"] = list(dict.fromkeys(old.get("aliases", []) + anchor.get("aliases", [])))
            old["forbidden"] = list(dict.fromkeys(old.get("forbidden", []) + anchor.get("forbidden", [])))
    return list(dedup.values())


def _load_characters(project_root) -> list[dict]:
    if not project_root:
        return []
    path = Path(project_root) / "_数据库" / "人物卡.json"
    obj = _read_json(path, {})
    raw = obj.get("characters") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return []

    characters: list[dict] = []
    for card in raw:
        if not isinstance(card, dict):
            continue
        name = str(card.get("name") or card.get("id") or "").strip()
        if not name:
            continue
        aliases = _as_text_list(card.get("aliases"))
        anchors = _character_anchors(card)
        characters.append({
            "name": name,
            "aliases": aliases,
            "anchors": anchors,
        })
    return characters


def _windows_for_character(text: str, character: dict) -> list[dict]:
    names = [character["name"]] + character.get("aliases", [])
    windows: list[dict] = []
    for alias in names:
        if not alias:
            continue
        for match in re.finditer(re.escape(alias), text):
            start = max(0, match.start() - _WINDOW_RADIUS)
            end = min(len(text), match.end() + _WINDOW_RADIUS)
            windows.append({"alias": alias, "start": start, "end": end, "text": text[start:end]})
    return windows


def _is_negated(window: str, pos: int) -> bool:
    prefix = window[max(0, pos - 6):pos]
    return any(token in prefix for token in ("不是", "并非", "没有", "不再是", "非"))


def _detect_drifts(text: str, characters: list[dict]) -> list[dict]:
    drifts: list[dict] = []
    for character in characters:
        windows = _windows_for_character(text, character)
        if not windows:
            continue
        for anchor in character.get("anchors", []):
            expected_terms = [anchor["expected"]] + anchor.get("aliases", [])
            for window in windows:
                for term in anchor.get("forbidden", []):
                    pos = window["text"].find(term)
                    if pos < 0 or _is_negated(window["text"], pos):
                        continue
                    if term in expected_terms:
                        continue
                    drifts.append({
                        "code": ISSUE_CODE,
                        "kind": "stable_identity_anchor_drift",
                        "severity": "minor",
                        "gate_level": "advisory",
                        "character": character["name"],
                        "matched_alias": window["alias"],
                        "anchor_type": anchor["anchor_type"],
                        "expected": anchor["expected"],
                        "observed": term,
                        "source": anchor["source"],
                        "evidence": window["text"].strip()[:160],
                    })
                    break
    return drifts


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "character_identity_anchor",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "violations": [],
        "verdict": "PASS",
        "warning": None,
    }
    if mode == "off":
        return out

    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as exc:
        out["note"] = f"draft read failed: {str(exc)[:120]}"
        return out
    text = _strip_changes(text)

    characters = _load_characters(project_root)
    out["character_count"] = len(characters)
    out["anchor_count"] = sum(len(c.get("anchors", [])) for c in characters)
    if not characters:
        out["note"] = "no character cards; skipped"
        return out
    if out["anchor_count"] == 0:
        out["note"] = "no identity anchors; skipped"
        return out

    drifts = _detect_drifts(text, characters)
    out["drift_count"] = len(drifts)
    out["drift_samples"] = drifts[:5]
    if drifts:
        msg = f"stable identity anchor drift detected: {len(drifts)}"
        if mode == "active":
            out["violations"] = drifts
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] character_identity_anchor: {msg}", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Stable character identity anchor drift scanner (advisory).")
    parser.add_argument("draft_path")
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
