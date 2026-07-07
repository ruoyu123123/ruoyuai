#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spatial_continuity_scanner.py - 同场景无标志词位置瞬移（角色↔地点绑定）扫描器。

补 ConStory location 维度盲区（tests/test_constory_consistency_gold.py
test_location_teleport_blindspot 记录的缺口）：角色上一段还在地窖深处，
下一段无任何移动动词/场景切换标志就出现在北境城墙——focalizer 规则③只认
「与此同时/同一时刻+远方」语言标志，locked_fact 只对恒定数值，POV 族只看
信号归属，三族对纯空间瞬移矛盾均 0 检出。本 scanner 用确定性的
角色↔地点绑定追踪补上这条通路。

追踪逻辑（全确定性·无 LLM）：
  1. 场景块 = 以 2+ 空行（\\n\\n\\n 及以上）切开的文本段；段内再按单空行聚合成段落。
  2. 地点词典：优先项目数据（_数据库/世界观.json 的 locations/地点/location 词条 +
     _数据库/地图.json 的 locations list·与人物卡 identity 无关）；
     项目无地点数据时退通用地点后缀启发式（城墙/大厅/房间 等多字后缀 +
     城/镇/村/殿/阁/楼/院/窖/牢/山/谷/林 等单字后缀·向左扩词根·单字地名丢弃）。
  3. 同一场景块内：角色名与地点 A 同段共现 → 绑定；后文同场景与地点 B 共现，
     两处之间无移动动词且无场景/时间切换标志 → 瞬移候选。

防误报豁免（全部内建）：
  ① 地点从属/同词根：B 是 A 的子空间或同词根（北境城墙 vs 城墙·字符串包含即豁免）；
  ② 对话/回忆提及不算「身处」：引号（U+201C/U+201D、「」、『』）内的地点 +
     含回忆标志（想起/回忆/梦见/当年…）的段落整段不参与绑定；
  ③ 传送/闪现类超能力词（传送阵/瞬移/挪移/遁术…）在两绑定之间或所在段落命中即豁免
     （玄幻常态）；
  ④ 场景切换/时间跳跃标志（次日/半个时辰后/与此同时…）命中即重置绑定；
  ⑤ 噪声地板：全稿瞬移候选 < 2 对不报（单例噪声）。

🔴 北极星⑤纪律（advisory 永不 hard_gate）：
  空间跳切本身可以是合法的叙事省略/蒙太奇——跨场景块的位置跳跃一律不报，
  本 scanner 只捞【同一场景块内】的明显空间矛盾，且移动动词/传送词按
  「宁可漏报不可误毙」偏置（单字移动动词高频误命中只会造成漏报）。
  产出是待裁决项不是判决；SPATIAL_CONTINUITY_TELEPORT 必须永远 advisory，
  绝不得加入 audit_hub.HARD_GATE_CODES。

CLI:
  py spatial_continuity_scanner.py <draft_path> --project <root> [--cluster]

三态开关: env SPATIAL_CONTINUITY_MODE = off / shadow(默认) / active
输出形态: violations[] / warning（照抄 character_identity_anchor_scanner.py）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "SPATIAL_CONTINUITY_TELEPORT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_MIN_CANDIDATE_PAIRS = 2   # 噪声地板：瞬移候选 < 2 对不报（单例噪声）
_MIN_TERM_LEN = 2          # 启发式地名至少 2 字（裸后缀单字太泛不算地名）

# ── 移动动词（两绑定之间命中任一 = 合法位移·豁免）──
_MOVEMENT_MULTI = (
    "离开", "穿过", "穿越", "掠过", "疾驰", "疾行", "奔赴", "折返", "动身",
    "启程", "出发", "前往", "抵达", "赶到", "赶往", "回到", "走到", "来到",
    "瞬移", "传送", "闪现",
)
_MOVEMENT_SINGLE = "去来到走赶回进出飞掠奔跑踏跃闯逃追退爬渡潜迁返赴"

# ── 传送/闪现类超能力词（两绑定之间或任一所在段落命中 = 玄幻常态·豁免）──
_TELEPORT_ABILITY = (
    "瞬移", "传送", "传送阵", "闪现", "挪移", "遁术", "遁地", "遁走", "神行",
    "缩地成寸", "空间跳跃", "空间裂缝", "空间之门", "折跃", "穿梭", "御剑", "飞升",
)

# ── 回忆/梦境标志（段落命中 = 提及非身处·整段不参与绑定）──
_RECALL_MARKERS = (
    "想起", "忆起", "回忆", "记得", "回想", "梦见", "梦里", "梦中", "梦回",
    "当年", "那时", "那年", "曾经", "往昔", "犹记", "脑海里", "脑海中", "浮现",
)

# ── 场景切换/时间跳跃标志（段落命中 = 重置本场景绑定·不算瞬移）──
_TRANSITION_MARKERS = (
    "与此同时", "同一时刻", "另一边", "另一处", "镜头一转", "视角一转",
    "次日", "翌日", "第二天", "三日后", "数日后", "半月后", "一个月后",
    "时辰后", "片刻后", "片刻之后", "一炷香", "当晚", "入夜", "天亮",
    "黎明", "黄昏", "傍晚", "清晨",
)

# ── 启发式地点后缀（项目无地点数据时的退路）──
_LOC_SUFFIX_MULTI = (
    "城墙", "大厅", "房间", "广场", "客栈", "酒楼", "书房", "密室", "庭院",
    "长廊", "大殿", "宫殿", "祠堂", "地牢", "天牢", "水牢",
)
_LOC_SUFFIX_SINGLE = "城镇村殿阁楼院窖牢山谷林宫府寺庙塔桥街巷湖河海岛洞窟关寨营堡坊斋堂台庄园馆房厅"
# 向左扩词根时的边界字（虚词/方位/动词·撞到即停）
_EXTEND_STOPSET = set(
    "在的了着是于到往从与和把被向离过出进回去来又已就还再那这此其某"
    "一二两三处身入个座间栋幢所有朝沿经越穿遍满守望立坐站躺跪靠倚上下"
)
# 启发式常见假地名（词根撞常用抽象词）
_TERM_BLACKLIST = {
    "脑海", "人海", "火海", "血海", "苦海", "云海", "武林", "绿林", "难关",
    "年关", "开关", "机关", "内阁", "阵营", "经营", "钻营", "名堂", "江山",
    "靠山", "漏洞",
}

_QUOTE_PAIRS = (("“", "”"), ("「", "」"), ("『", "』"))
_CJK_RE = re.compile(r"[一-鿿]")


def _mode() -> str:
    mode = (os.environ.get("SPATIAL_CONTINUITY_MODE") or "shadow").strip().lower()
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


def _as_names(value) -> list[str]:
    """从 str / list[str|dict] 里抽地点名字符串。"""
    out: list[str] = []
    if isinstance(value, str) and value.strip():
        out.append(value.strip())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                for key in ("name", "名称", "title", "id"):
                    v = item.get(key)
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
                        break
    return out


def _load_project_locations(project_root) -> list[str]:
    """项目地点词典：世界观.json 的 locations/地点/location + 地图.json 的 locations。"""
    if not project_root:
        return []
    db = Path(project_root) / "_数据库"
    terms: list[str] = []
    world = _read_json(db / "世界观.json", {})
    if isinstance(world, dict):
        for key in ("locations", "地点", "location"):
            terms.extend(_as_names(world.get(key)))
    atlas = _read_json(db / "地图.json", {})
    if isinstance(atlas, dict):
        terms.extend(_as_names(atlas.get("locations")))
    terms = [t for t in terms if len(t) >= _MIN_TERM_LEN]
    return list(dict.fromkeys(terms))


def _extend_left(text: str, start: int, max_prefix: int = 4) -> int:
    """从后缀起点向左扩词根：遇到非 CJK / 边界字 / 超长即停，返回词根起点。"""
    i = start
    while start - i < max_prefix and i > 0:
        ch = text[i - 1]
        if not _CJK_RE.match(ch) or ch in _EXTEND_STOPSET:
            break
        i -= 1
    return i


def _heuristic_locations(text: str) -> list[str]:
    """通用地点后缀启发式（项目无地点数据时的退路）。"""
    terms: list[str] = []
    consumed: list[tuple[int, int]] = []
    multi_re = re.compile("|".join(re.escape(s) for s in _LOC_SUFFIX_MULTI))
    for m in multi_re.finditer(text):
        begin = _extend_left(text, m.start())
        terms.append(text[begin:m.end()])
        consumed.append((m.start(), m.end()))
    for m in re.finditer(f"[{_LOC_SUFFIX_SINGLE}]", text):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        begin = _extend_left(text, m.start())
        term = text[begin:m.end()]
        if len(term) >= _MIN_TERM_LEN:
            terms.append(term)
    terms = [t for t in dict.fromkeys(terms) if t not in _TERM_BLACKLIST]
    return terms


def _load_characters(project_root) -> list[dict]:
    if not project_root:
        return []
    obj = _read_json(Path(project_root) / "_数据库" / "人物卡.json", {})
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
        aliases = card.get("aliases")
        alias_list = [a.strip() for a in aliases if isinstance(a, str) and a.strip()] \
            if isinstance(aliases, list) else []
        characters.append({"name": name, "aliases": alias_list})
    return characters


def _split_with_offsets(text: str, sep_pattern: str) -> list[tuple[int, str]]:
    """按分隔符切块并保留每块在原文中的起始偏移。"""
    out: list[tuple[int, str]] = []
    offset = 0
    sep_re = re.compile(sep_pattern)
    for chunk in re.split(f"({sep_pattern})", text):
        if chunk and not sep_re.fullmatch(chunk) and chunk.strip():
            out.append((offset, chunk))
        offset += len(chunk)
    return out


def _quote_spans(para: str) -> list[tuple[int, int]]:
    """中文引号成对区间（未闭合的开引号延伸到段尾）。"""
    spans: list[tuple[int, int]] = []
    for opener, closer in _QUOTE_PAIRS:
        start = None
        for i, ch in enumerate(para):
            if ch == opener and start is None:
                start = i
            elif ch == closer and start is not None:
                spans.append((start, i + 1))
                start = None
        if start is not None:
            spans.append((start, len(para)))
    return spans


def _location_mentions(para: str, terms: list[str]) -> list[tuple[int, str]]:
    """段内地点提及（长词优先·跳过引号内=对话提及非身处）·按出现位置排序。"""
    qspans = _quote_spans(para)
    taken: list[tuple[int, int]] = []
    mentions: list[tuple[int, str]] = []
    for term in sorted(terms, key=len, reverse=True):
        for m in re.finditer(re.escape(term), para):
            if any(s <= m.start() < e for s, e in qspans):
                continue
            if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            mentions.append((m.start(), term))
    return sorted(mentions)


def _has_movement(segment: str) -> bool:
    return any(w in segment for w in _MOVEMENT_MULTI) or \
        any(c in segment for c in _MOVEMENT_SINGLE)


def _has_teleport_ability(segment: str) -> bool:
    return any(w in segment for w in _TELEPORT_ABILITY)


def _matched_alias(para: str, character: dict) -> str | None:
    for alias in [character["name"], *character.get("aliases", [])]:
        if alias and alias in para:
            return alias
    return None


def _detect(text: str, characters: list[dict], terms: list[str]) -> dict:
    """角色↔地点绑定追踪主循环。返回 candidates/waived/binding/scene 计数。"""
    candidates: list[dict] = []
    waived = 0
    binding_count = 0
    scenes = _split_with_offsets(text, r"(?:\r?\n[ \t]*){3,}")
    for scene_idx, (_, scene) in enumerate(scenes):
        bindings: dict[str, dict] = {}  # 角色名 -> {loc, end(场景内绝对位), para}
        for para_off, para in _split_with_offsets(scene, r"(?:\r?\n[ \t]*){2,}"):
            if any(t in para for t in _TRANSITION_MARKERS):
                bindings.clear()  # 场景/时间切换标志：绑定重置（跳切合法）
            if any(t in para for t in _RECALL_MARKERS):
                continue  # 回忆/梦境段：提及≠身处
            mentions = _location_mentions(para, terms)
            if not mentions:
                continue
            present = [c for c in characters if _matched_alias(para, c)]
            for character in present:
                name = character["name"]
                for pos, term in mentions:
                    abs_pos = para_off + pos
                    new_binding = {"loc": term, "end": abs_pos + len(term), "para": para}
                    prev = bindings.get(name)
                    binding_count += 1
                    if prev is None or prev["loc"] == term:
                        bindings[name] = new_binding
                        continue
                    if term in prev["loc"] or prev["loc"] in term:
                        waived += 1  # ① 子空间/同词根豁免
                        bindings[name] = new_binding
                        continue
                    between = scene[prev["end"]:abs_pos]
                    if _has_movement(between):
                        bindings[name] = new_binding  # 合法位移
                    elif _has_teleport_ability(between) or \
                            _has_teleport_ability(prev["para"]) or \
                            _has_teleport_ability(para):
                        waived += 1  # ③ 传送/闪现超能力豁免
                        bindings[name] = new_binding
                    else:
                        evidence = (prev["para"].strip()[-50:] + " ⇢ " + para.strip()[:80])[:160]
                        candidates.append({
                            "code": ISSUE_CODE,
                            "kind": "spatial_teleport_no_transition",
                            "severity": "minor",
                            "gate_level": "advisory",
                            "character": name,
                            "matched_alias": _matched_alias(para, character),
                            "from_location": prev["loc"],
                            "to_location": term,
                            "scene_index": scene_idx,
                            "evidence": evidence,
                        })
                        bindings[name] = new_binding
    return {
        "candidates": candidates,
        "waived_count": waived,
        "binding_count": binding_count,
        "scene_count": len(scenes),
    }


def scan(draft_path, project_root=None, cluster_mode: bool = False) -> dict:
    mode = _mode()
    out = {
        "scanner": "spatial_continuity",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "cluster_mode": bool(cluster_mode),
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
    if not characters:
        out["note"] = "no character cards; skipped"
        return out

    project_terms = _load_project_locations(project_root)
    if project_terms:
        terms = project_terms
        out["location_lexicon_source"] = "project"
    else:
        terms = _heuristic_locations(text)
        out["location_lexicon_source"] = "heuristic"
    out["location_term_count"] = len(terms)
    if not terms:
        out["note"] = "no location terms; skipped"
        return out

    result = _detect(text, characters, terms)
    candidates = result["candidates"]
    out["scene_count"] = result["scene_count"]
    out["binding_count"] = result["binding_count"]
    out["waived_count"] = result["waived_count"]
    out["teleport_candidate_count"] = len(candidates)
    out["teleport_samples"] = candidates[:5]

    if len(candidates) >= _MIN_CANDIDATE_PAIRS:
        msg = f"spatial teleport without transition: {len(candidates)}"
        if mode == "active":
            out["violations"] = candidates
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] spatial_continuity: {msg}", file=sys.stderr)
    elif candidates:
        out["note"] = "single teleport candidate suppressed (noise floor <2)"
    out["violations_count"] = len(out["violations"])
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Same-scene spatial continuity teleport scanner (advisory).")
    parser.add_argument("draft_path")
    parser.add_argument("--project", default=None)
    parser.add_argument("--cluster", action="store_true",
                        help="cluster 草稿模式标记（仅记录到输出，不改变检测逻辑）")
    args = parser.parse_args()
    cluster_mode = args.cluster or os.environ.get("CLUSTER_MODE") == "1"
    report = scan(args.draft_path, args.project, cluster_mode=cluster_mode)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
