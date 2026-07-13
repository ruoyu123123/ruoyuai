#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spatial_continuity_scanner.py - 同场景无标志词位置瞬移（角色↔地点绑定）扫描器。

检测目标：角色上一段还在地窖深处，下一段无任何移动动词/场景切换标志就
出现在北境城墙。这类纯空间瞬移矛盾其他检测族均不覆盖——focalizer 规则③只认
「与此同时/同一时刻+远方」语言标志，locked_fact 只对恒定数值，POV 族只看
信号归属（盲区金标准见 tests/test_constory_consistency_gold.py
test_location_teleport_blindspot）。本 scanner 用确定性的
角色↔地点绑定追踪覆盖这条通路。

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
  ② 对话/回忆/作品名提及不算「身处」：引号（U+201C/U+201D、「」、『』）与
     书名号（《》〈〉）内的地点 + 含回忆/追述标志（想起/回忆/梦见/当年/当时/先前…）
     的段落整段不参与绑定；
  ③ 传送/闪现类超能力词（传送阵/瞬移/挪移/遁术…）在两绑定之间或所在段落命中即豁免
     （玄幻常态）；
  ④ 场景切换/时间跳跃标志（次日/半个时辰后/与此同时…）命中即重置绑定；
  ⑤ 噪声地板：全稿瞬移候选 < 2 对不报（单例噪声）；
  ⑥ 远观/传闻/意图/比喻标志（望向/对面/听说/想去/就像/仿佛…）段落整段不参与绑定
     （提及≠身处）；
  ⑦ 同段多地点共现只更新绑定不报（从属地点链「兜率宫→主殿→丹房」/路线地理
     「泥瓶巷在小镇西边」/群像归属列举是空间铺陈常态；真瞬移错误的形态是
     「上段在 A、下段在 B」——只报跨段绑定冲突）；
  ⑧ 绑定就近性：地点提及与角色名间隔 ≤50 字才算身处证据（远距同段共现多为
     环境铺陈/他人行程）；
  ⑨ 角色名重叠排除：地点匹配与人物卡角色名/别名区间重叠即弃
     （莫山山≠山山、周青+海外≠周青海）。

📊 金标准校准（10 作者 100 chunk·连续 4 章拼接·全书均匀取样·
   启发式词典路径·SPATIAL_CONTINUITY_MODE=active）：
  地点后缀/边界集/黑名单/豁免规则按该语料校准到 0/100 零误报（剔除 关/台/海 等抽象词撞
  单字后缀、词根左扩边界集覆盖动词/量词/代词、提及≠身处豁免⑥⑦⑧、人名撞地点后缀豁免⑨）。
  残留单例候选（人名残留/远观无标志/宗门迁移叙述）由噪声地板⑤压制。

🔴 北极星⑤纪律（advisory 永不 hard_gate）：
  空间跳切本身可以是合法的叙事省略/蒙太奇——跨场景块的位置跳跃一律不报，
  本 scanner 只捞【同一场景块内】的明显空间矛盾，且移动动词/传送词按
  「宁可漏报不可误毙」偏置（单字移动动词高频误命中只会造成漏报）。
  产出是待裁决项不是判决；SPATIAL_CONTINUITY_TELEPORT 必须永远 advisory，
  绝不得加入 audit_hub.HARD_GATE_CODES。

CLI:
  py spatial_continuity_scanner.py <draft_path> --project <root> [--cluster]

三态开关: env SPATIAL_CONTINUITY_MODE = off / shadow / active(默认·金标准
  10 作者 100 chunk 零误报实测)
输出形态: violations[] / warning（与 character_identity_anchor_scanner.py 一致）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from atomic_json import load_json

ISSUE_CODE = "SPATIAL_CONTINUITY_TELEPORT"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_MIN_CANDIDATE_PAIRS = 2   # 噪声地板：瞬移候选 < 2 对不报（单例噪声）
_MIN_TERM_LEN = 2          # 启发式地名至少 2 字（裸后缀单字太泛不算地名）
_BINDING_PROXIMITY = 50    # 绑定就近性：地点提及与角色名间隔 ≤50 字才算身处证据

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
    "当时", "先前", "此前", "早前",
)

# ── 远观/传闻/意图标志（段落命中 = 提及非身处·整段不参与绑定）──
# 金标准校准实证（10 位作者）：望向华山/听说骑龙巷/大夫府对面
# 这类「远眺·转述·方位参照·打算前往」是真作者原文最大宗误报源之一。
_NON_PRESENCE_MARKERS = (
    "望向", "望去", "眺望", "遥望", "远望", "远眺", "远处", "远方", "方向",
    "对面", "隔壁", "听说", "据说", "传闻", "传言", "提起", "说起", "谈起",
    "聊起", "打听", "所谓", "叫做", "名为", "想去", "要去", "打算", "准备去",
    "就像", "好像", "好似", "像是", "仿佛", "如同", "犹如", "宛如", "恍如",
    "恍若",
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
# 单字后缀不含 关/台/海：这三个字的抽象词性远压倒地名性（通关/闭关/无关/牙关 53 次 ·
# 平台/柜台/灵台 29 次 · 虚海/烟海/人名+海 20 次·10 作者 100 chunk 金标准实测），
# 真地名（陈塘关/东海）漏报可接受——本 scanner 偏置宁漏勿误。
_LOC_SUFFIX_SINGLE = "城镇村殿阁楼院窖牢山谷林宫府寺庙塔桥街巷湖河岛洞窟寨营堡坊斋堂庄园馆房厅"
# 向左扩词根时的边界字（虚词/方位/动词/量词/代词·撞到即停）——不把动词/量词/代词纳入
# 边界会把词根跨词误抽（如"开明斯克街"的离开·"达杠杆教堂"的抵达·"听说几条街"·"多年不种庄"）。
_EXTEND_STOPSET = set(
    "在的了着是于到往从与和把被向离过出进回去来又已就还再那这此其某"
    "一二两三处身入个座间栋幢所有朝沿经越穿遍满守望立坐站躺跪靠倚上下"
    "开听说看瞧见闻问聊谈讲登临近旁给扔丢拿抓搬翻种买卖购租付聚汇载"
    "点排套层条堆位片名令期待知想认以作干做能强如同比跟且得没无何么"
    "你我他她它们要才刚正终竟仍皆均亦眼心不但即而或若径途俯漫直"
)
# 移动动词字绝不进词根（金标准实证「直奔交易广场」把 奔 吞进地名，
# 导致两绑定 between 段丢失移动证据）——单字移动动词全量并入边界集。
_EXTEND_STOPSET |= set(_MOVEMENT_SINGLE)
# 启发式常见假地名（词根撞常用抽象词·用【子串】命中判断弃用，覆盖「江湖」的
# 扩展变体「位江湖/些江湖/聊江湖」等）
_TERM_BLACKLIST = {
    "脑海", "人海", "火海", "血海", "苦海", "云海", "武林", "绿林", "难关",
    "年关", "开关", "机关", "内阁", "阵营", "经营", "钻营", "名堂", "江山",
    "靠山", "漏洞", "江湖", "心湖", "山河", "河山", "乐园", "脑洞", "课堂",
    "庙堂",
}
# 单字后缀右邻复合词（后缀+右邻字构成常用非地点词 = 中词命中·弃）
# 金标准实证：强作镇定/庄严/大门洞开/颇有城府/山山水水。
_RIGHT_COMPOUND_BLACKLIST = {
    "镇定", "镇静", "庄严", "庄稼", "洞开", "洞察", "洞悉", "城府", "街坊",
    "山水", "山川", "山脉", "山摇", "山峰", "院袍", "营业",
}

# 书名号也入豁免区间：《搜山图》这类作品名里的地点词是提及非身处（金标准实证）
_QUOTE_PAIRS = (("“", "”"), ("「", "」"), ("『", "』"), ("《", "》"), ("〈", "〉"))
_CJK_RE = re.compile(r"[一-鿿]")


def _mode() -> str:
    # 默认 active：金标准 10 作者 100 chunk 零误报实测（校准明细见模块 docstring）
    mode = (os.environ.get("SPATIAL_CONTINUITY_MODE") or "active").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _read_json(path: Path, default):
    return load_json(path, default=default)


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
        ch = m.group(0)
        nxt = text[m.end():m.end() + 1]
        prv = text[m.start() - 1:m.start()]
        if nxt == ch or prv == ch:
            continue  # 叠词（山山水水/牢牢/楼楼）非地名
        if ch + nxt in _RIGHT_COMPOUND_BLACKLIST:
            continue  # 中词命中（镇定/庄严/洞开/城府）
        begin = _extend_left(text, m.start())
        term = text[begin:m.end()]
        if len(term) >= _MIN_TERM_LEN:
            terms.append(term)
    terms = [t for t in dict.fromkeys(terms)
             if not any(b in t for b in _TERM_BLACKLIST)]
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


def _location_mentions(para: str, terms: list[str],
                       exclude_spans: list[tuple[int, int]] | None = None,
                       ) -> list[tuple[int, str]]:
    """段内地点提及（长词优先·跳过引号内=对话提及非身处·跳过角色名重叠区间：
    金标准实证 莫山山/周青+海外 这类人名撞地点后缀）·按出现位置排序。"""
    qspans = _quote_spans(para)
    banned = list(qspans) + list(exclude_spans or [])
    taken: list[tuple[int, int]] = []
    mentions: list[tuple[int, str]] = []
    for term in sorted(terms, key=len, reverse=True):
        for m in re.finditer(re.escape(term), para):
            if any(not (m.end() <= s or m.start() >= e) for s, e in banned):
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


def _alias_spans(para: str, character: dict) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for alias in [character["name"], *character.get("aliases", [])]:
        if not alias:
            continue
        for m in re.finditer(re.escape(alias), para):
            spans.append((m.start(), m.end()))
    return spans


def _near(pos: int, length: int, spans: list[tuple[int, int]]) -> bool:
    """地点提及与角色名就近（间隔 ≤ _BINDING_PROXIMITY 字）才算「身处」证据。
    金标准实证：远距同段共现绝大多数是环境铺陈/他人行程（提及≠身处）。"""
    end = pos + length
    return any(max(0, pos - e, s - end) <= _BINDING_PROXIMITY for s, e in spans)


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
            if any(t in para for t in _NON_PRESENCE_MARKERS):
                continue  # 远观/传闻/意图段：提及≠身处
            spans_by_char = {c["name"]: (c, _alias_spans(para, c))
                             for c in characters}
            all_spans = [s for _, sp in spans_by_char.values() for s in sp]
            if not all_spans:
                continue
            mentions = _location_mentions(para, terms, exclude_spans=all_spans)
            if not mentions:
                continue
            present = [c for c, sp in spans_by_char.values() if sp]
            for character in present:
                name = character["name"]
                char_spans = spans_by_char[name][1]
                for pos, term in mentions:
                    if not _near(pos, len(term), char_spans):
                        continue  # 距角色名过远：环境铺陈非身处证据
                    abs_pos = para_off + pos
                    new_binding = {"loc": term, "end": abs_pos + len(term),
                                   "para": para, "para_off": para_off}
                    prev = bindings.get(name)
                    binding_count += 1
                    if prev is None or prev["loc"] == term:
                        bindings[name] = new_binding
                        continue
                    if term in prev["loc"] or prev["loc"] in term:
                        waived += 1  # ① 子空间/同词根豁免
                        bindings[name] = new_binding
                        continue
                    if prev.get("para_off") == para_off:
                        # ⑥ 同段多地点共现 = 空间铺陈常态（从属地点链/路线地理/
                        # 群像归属），金标准实证同段对几乎全是误报——真瞬移错误
                        # 的形态是「上段在 A、下段在 B」。同段只更新绑定不报。
                        waived += 1
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
