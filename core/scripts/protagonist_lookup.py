#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""protagonist_lookup.py — 主角反查唯一真理源。

全仓任何需要「谁是主角」的脚本都从这里取，禁止再各自写 `role == "主角"` 精确匹配
（人物卡 role 是自由文本，实测写成「主角（视角人物）」「炎帝幼女·…执念主角之一」
都合法 → 精确匹配解析不出主角 → scanner 空跑）。

【producer 契约（唯一 canonical 形态）】
`_数据库/人物卡.json` 的主角卡 `role` **必须以「主角」开头**，补充描述跟在后面：
    "role": "主角·炎帝幼女·不甘认命的刚烈幼妹"
由 `db_schema_validate.py` 的 `check_protagonist_contract` 硬校验（characters 非空却
无 canonical 主角位 = 契约错误），novel-outline-planner / novel-archivist 合约同口径。

【consumer 反查（多源兜底 · 按优先级）】
1. 调用方 override
2. `人物卡.json`：role 以「主角」开头（canonical）> `is_protagonist: true` > role 含「主角」/英文别名
3. `角色弧线.json`.characters{id: {role}}（id 经人物卡 id→name 归一）
4. `character_arc_state.json`.characters{name: {role}}
5. `事件簇.json`：顶层 `protagonist` / clusters[].protagonist
6. `角色池.json`：core → emerged → extras 里的主角位

解析不出 → 返回 None（调用方自行决定 fatal 还是降级），绝不猜。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atomic_json import load_json  # noqa: E402

DB_DIRNAME = "_数据库"
CANONICAL_PREFIX = "主角"
CANONICAL_ROLE_HINT = (
    "人物卡主角位 role 必须以「主角」开头（如 \"主角\" 或 \"主角·炎帝幼女·不甘认命的刚烈幼妹\"）"
)

# 英文/单字别名（整体等值匹配，不做子串）
_ROLE_ALIASES = {"protagonist", "主", "lead", "hero", "mc", "main character", "main"}
# 含「主角」但实为关系描述/否定位的 role（如「主角的师父」「非主角」）
_ROLE_NEGATIVE = re.compile(r"非主角|反主角|伪主角|主角的")


def _db(project_root) -> Path:
    return Path(project_root) / DB_DIRNAME


def _load(path: Path, default):
    return load_json(path, default=default)


def is_canonical_protagonist_role(role) -> bool:
    """producer 契约形态：role 以「主角」开头（且不是「主角的…」这类关系描述）。"""
    if not isinstance(role, str):
        return False
    text = role.strip()
    return text.startswith(CANONICAL_PREFIX) and not _ROLE_NEGATIVE.search(text)


def is_protagonist_role(role) -> bool:
    """consumer 兜底判定：canonical / 含「主角」/ 英文别名 都算主角位。

    否定位（「非主角」「主角的师父」等关系描述）先排除，再判正例，避免
    「主角的师父」因以「主角」开头被误判为主角。
    """
    if not isinstance(role, str):
        return False
    text = role.strip()
    if not text:
        return False
    if _ROLE_NEGATIVE.search(text):
        return False
    if text.lower() in _ROLE_ALIASES:
        return True
    return CANONICAL_PREFIX in text


def _card_rank(card: dict) -> int:
    """主角信号强度：3=canonical role · 2=is_protagonist 标记 · 1=宽松 role · 0=不是主角。"""
    if not isinstance(card, dict):
        return 0
    role = card.get("role")
    if is_canonical_protagonist_role(role):
        return 3
    if card.get("is_protagonist") is True:
        return 2
    if is_protagonist_role(role):
        return 1
    return 0


def protagonist_cards(cards) -> list[dict]:
    """人物卡 characters 列表里的全部主角位卡（信号强度降序，同强度保留原序）。"""
    if not isinstance(cards, list):
        return []
    ranked = [(_card_rank(c), i, c) for i, c in enumerate(cards)]
    hits = [(rank, i, c) for rank, i, c in ranked if rank > 0]
    hits.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in hits]


def has_canonical_protagonist(cards) -> bool:
    """producer 契约体检：至少一条卡 role 以「主角」开头。"""
    return isinstance(cards, list) and any(
        isinstance(c, dict) and is_canonical_protagonist_role(c.get("role")) for c in cards
    )


def _card_name(card: dict) -> str | None:
    for key in ("name", "id"):
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _cards(project_root) -> list[dict]:
    document = _load(_db(project_root) / "人物卡.json", {})
    characters = document.get("characters") if isinstance(document, dict) else None
    return [c for c in characters if isinstance(c, dict)] if isinstance(characters, list) else []


def _id_to_name(cards: list[dict]) -> dict[str, str]:
    mapping = {}
    for card in cards:
        cid, name = card.get("id"), card.get("name")
        if isinstance(cid, str) and cid and isinstance(name, str) and name:
            mapping[cid] = name
    return mapping


def _from_cards(cards: list[dict]) -> tuple[str | None, dict | None]:
    for card in protagonist_cards(cards):
        name = _card_name(card)
        if name:
            return name, card
    return None, None


def _from_arc_mapping(document, id2name: dict[str, str]) -> str | None:
    characters = document.get("characters") if isinstance(document, dict) else None
    if not isinstance(characters, dict):
        return None
    for key, info in characters.items():
        if not isinstance(info, dict):
            continue
        if is_protagonist_role(info.get("role")) or info.get("is_protagonist") is True:
            name = id2name.get(key, key)
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _from_event_clusters(document) -> str | None:
    if not isinstance(document, dict):
        return None
    top = document.get("protagonist")
    if isinstance(top, str) and top.strip():
        return top.strip()
    clusters = document.get("clusters")
    for record in clusters if isinstance(clusters, list) else []:
        if not isinstance(record, dict):
            continue
        value = record.get("protagonist")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _from_pool(document) -> str | None:
    if not isinstance(document, dict):
        return None
    for group in ("core", "emerged", "extras"):
        for entry in document.get(group) or []:
            if not isinstance(entry, dict):
                continue
            if is_protagonist_role(entry.get("role")) or entry.get("is_protagonist") is True:
                name = _card_name(entry)
                if name:
                    return name
    return None


def resolve_protagonist_detail(project_root, override: str | None = None) -> dict:
    """返回 {"name": str|None, "source": str, "role": str|None, "card": dict|None}。"""
    if isinstance(override, str) and override.strip():
        return {"name": override.strip(), "source": "override", "role": None, "card": None}

    cards = _cards(project_root)
    name, card = _from_cards(cards)
    if name:
        return {"name": name, "source": "人物卡.json", "role": card.get("role"), "card": card}

    database = _db(project_root)
    id2name = _id_to_name(cards)
    name = _from_arc_mapping(_load(database / "角色弧线.json", {}), id2name)
    if name:
        return {"name": name, "source": "角色弧线.json", "role": None, "card": None}
    name = _from_arc_mapping(_load(database / "character_arc_state.json", {}), id2name)
    if name:
        return {"name": name, "source": "character_arc_state.json", "role": None, "card": None}
    name = _from_event_clusters(_load(database / "事件簇.json", {}))
    if name:
        return {"name": name, "source": "事件簇.json", "role": None, "card": None}
    name = _from_pool(_load(database / "角色池.json", {}))
    if name:
        return {"name": name, "source": "角色池.json", "role": None, "card": None}
    return {"name": None, "source": "unresolved", "role": None, "card": None}


def resolve_protagonist(project_root, override: str | None = None,
                        default: str | None = None) -> str | None:
    """主角名（多源兜底）。全解析不出时返回 default（默认 None）。"""
    return resolve_protagonist_detail(project_root, override=override)["name"] or default


def protagonist_names(project_root) -> list[str]:
    """人物卡里全部主角位名字（双主角/群像用；按主角信号强度降序）。"""
    names = [_card_name(card) for card in protagonist_cards(_cards(project_root))]
    return list(dict.fromkeys([n for n in names if n]))
