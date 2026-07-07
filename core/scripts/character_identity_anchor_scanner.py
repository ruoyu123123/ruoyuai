#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_identity_anchor_scanner.py - stable identity anchor drift scanner.

Borrowed concept: moyin-creator keeps character identity anchors for visual
continuity. In ruoyuai this scanner turns that idea into prose continuity:
stable hair/eye/mark anchors must not drift in nearby narration.

2026-07-07 A7 (round2 porting, moyin 6-layer identity anchors as text):
  - characters[].recognition_anchors ([{anchor, position_or_scene}]) feed the
    same drift detection (anchor phrase -> hair/eye/mark anchor when parsable).
  - characters[].negative_facts (["不会武功", "不识字", ...]) get a reverse
    check: positive-capability terms near the character name are advisory
    violations (kind="negative_fact_violation", same top-level issue code -
    no new code, no registry chain). Quoted dialogue mentions are exempt
    (「他要是会武功就好了」 is not a violation).

Input:
  - draft text
  - _数据库/人物卡.json characters[].identity_anchors
      + characters[].recognition_anchors / characters[].negative_facts (A7)
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

# ── A7 negative_facts (2026-07-07) ──────────────────────────────────────────
# A negative fact is a negated capability statement ("不会武功"). We derive the
# positive-assertion terms whose appearance near the character name flags an
# advisory violation ("会武功" / "精通武功" ...). Marker order matters: longer
# markers first so "不能" is not shadowed by a shorter prefix.
_NEGATIVE_FACT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("没学过", ("学过{a}", "会{a}")),
    ("不擅长", ("擅长{a}", "精通{a}")),
    ("不会", ("会{a}", "精通{a}", "擅长{a}")),
    ("不能", ("能{a}", "可以{a}")),
    ("无法", ("能{a}", "可以{a}")),
    ("不懂", ("懂{a}", "精通{a}")),
    ("不识", ("识{a}", "认得{a}")),
)
_ABILITY_STOP_CHARS = "，。；、！？：…—,.;!? \n\t“”「」『』"
_MAX_ABILITY_LEN = 8
# Prefix tokens that make a positive-term hit non-violating (negation or
# hypothetical framing right before the match).
_NEG_PREFIX_TOKENS = ("不", "没", "未", "别", "并非", "无法", "难以",
                      "要是", "如果", "若是", "假如", "除非")
_QUOTE_PAIRS = (("“", "”"), ("「", "」"), ("『", "』"))


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


def _recognition_anchor_phrases(raw) -> list[str]:
    """A7: characters[].recognition_anchors -> anchor phrase strings."""
    phrases: list[str] = []
    if not isinstance(raw, list):
        return phrases
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("anchor") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            phrases.append(text)
    return phrases


def _parse_negative_facts(raw) -> list[dict]:
    """A7: negated-capability strings -> {fact, positive_terms[]} entries.

    "不会武功" -> positive terms 会武功/精通武功/擅长武功; facts whose ability
    part cannot be derived produce no entry (conservative: no false alarms).
    """
    entries: list[dict] = []
    for fact in _as_text_list(raw):
        terms: list[str] = []
        for marker, templates in _NEGATIVE_FACT_MARKERS:
            start = 0
            while True:
                pos = fact.find(marker, start)
                if pos < 0:
                    break
                tail = fact[pos + len(marker):]
                ability = ""
                for chch in tail:
                    if chch in _ABILITY_STOP_CHARS:
                        break
                    ability += chch
                ability = ability.strip()
                if ability and len(ability) <= _MAX_ABILITY_LEN:
                    terms.extend(t.format(a=ability) for t in templates)
                start = pos + len(marker)
        terms = list(dict.fromkeys(t for t in terms if t))
        if terms:
            entries.append({"fact": fact, "positive_terms": terms})
    return entries


def _quote_spans(text: str) -> list[tuple[int, int]]:
    """Absolute [start, end) spans of quoted dialogue (A7 quote exemption)."""
    spans: list[tuple[int, int]] = []
    for open_q, close_q in _QUOTE_PAIRS:
        open_at = None
        for idx, chch in enumerate(text):
            if chch == open_q and open_at is None:
                open_at = idx
            elif chch == close_q and open_at is not None:
                spans.append((open_at, idx + 1))
                open_at = None
    return spans


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _character_anchors(card: dict) -> list[dict]:
    anchors = _parse_identity_anchors(card.get("identity_anchors"))
    # A7: recognition_anchors feed the same drift detection when the anchor
    # phrase is parsable into a hair/eye/mark anchor (habit/prop anchors are
    # descriptive-only and yield no drift anchor - conservative).
    for phrase in _recognition_anchor_phrases(card.get("recognition_anchors")):
        anchors.extend(_anchors_from_phrase(phrase, "recognition_anchors"))
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
            "negative_facts": _parse_negative_facts(card.get("negative_facts")),
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


def _is_prefix_exempt(window: str, pos: int) -> bool:
    """Negation/hypothetical framing right before a positive-term hit."""
    prefix = window[max(0, pos - 6):pos]
    return any(token in prefix for token in _NEG_PREFIX_TOKENS)


def _detect_negative_fact_violations(text: str, characters: list[dict]) -> list[dict]:
    """A7: positive-capability term near a character whose card negates it.

    Quoted dialogue mentions are exempt (a character talking about the
    ability is not the character demonstrating it - judged by voice-checker
    with narrative context, not by this mechanical scanner).
    """
    spans = _quote_spans(text)
    violations: list[dict] = []
    for character in characters:
        facts = character.get("negative_facts") or []
        if not facts:
            continue
        windows = _windows_for_character(text, character)
        if not windows:
            continue
        for entry in facts:
            for window in windows:
                for term in entry["positive_terms"]:
                    pos = window["text"].find(term)
                    if pos < 0:
                        continue
                    if _in_spans(window["start"] + pos, spans):
                        continue
                    if _is_prefix_exempt(window["text"], pos):
                        continue
                    violations.append({
                        "code": ISSUE_CODE,
                        "kind": "negative_fact_violation",
                        "severity": "minor",
                        "gate_level": "advisory",
                        "character": character["name"],
                        "matched_alias": window["alias"],
                        "negative_fact": entry["fact"],
                        "observed": term,
                        "source": "negative_facts",
                        "evidence": window["text"].strip()[:160],
                    })
                    break
    return violations


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
    out["negative_fact_count"] = sum(len(c.get("negative_facts", [])) for c in characters)
    if not characters:
        out["note"] = "no character cards; skipped"
        return out
    if out["anchor_count"] == 0 and out["negative_fact_count"] == 0:
        out["note"] = "no identity anchors / negative facts; skipped"
        return out

    drifts = _detect_drifts(text, characters)
    negative_hits = _detect_negative_fact_violations(text, characters)
    out["drift_count"] = len(drifts)
    out["drift_samples"] = drifts[:5]
    out["negative_fact_violation_count"] = len(negative_hits)
    out["negative_fact_samples"] = negative_hits[:5]
    found = drifts + negative_hits
    if found:
        parts = []
        if drifts:
            parts.append(f"stable identity anchor drift detected: {len(drifts)}")
        if negative_hits:
            parts.append(f"negative fact violation detected: {len(negative_hits)}")
        msg = "; ".join(parts)
        if mode == "active":
            out["violations"] = found
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
