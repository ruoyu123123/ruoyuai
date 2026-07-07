#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""draft_temporal_order_scanner.py — 草稿内部时序倒错扫描（确定性·零 LLM·advisory）

【盲区出处】tests/test_constory_consistency_gold.py::test_temporal_order_reversal_blindspot
诚实记录：草稿**内部**两处时间锚互相打架（scene1=第九天、scene2=第五天）且无锁定事实
数值锚时，确定性层 0 检出——locked_fact_cross_scene 只做「锁定事实 ↔ 正文」对账，
anachrony_order / temporal_grounding 只测锚词密度非矛盾。本 scanner 补该缺口。

【做法 · 确定性纯规则】
  1. 按空行段落块切场景（与 scene_receipts_scanner 同口径）。
  2. 每场景抽可排序时间锚：
     - 绝对序数：第N天/第N日/第N夜（N=阿拉伯或中文数字）→ 天数值；
     - 相对推进：N天后/N日后/翌日/次日/隔天/隔日/第二天/第二日（只向前推进时钟，
       「第二天/第二日」按网文惯用语义视为相对 +1，不当绝对第 2 天，防误报）；
     - 时段序：清晨→上午→正午→午后→黄昏→夜里→深夜（含常见同义词），
       仅在明确同日（两锚之间无任何跨日锚介入）时参与排序。
  3. 构建场景序列时间戳偏序 → 后场景时间早于前场景 = 倒错候选。

【防误报六重豁免（全做）】
  ① narrative_mode 门控：从 事件簇.json 反查该 cluster 的 narrative_mode，
     "in_medias_res"（黄金三章倒叙·cluster_001 未声明时默认）整体 skip；
  ② 闪回豁免：场景内命中回忆标志（回忆/想起/那时/当年/N年前/记忆里）→
     该场景整体不参与排序；
  ③ 锚点稀疏不判：可排序锚点场景数 < 3 不判；时段序只在无跨日锚介入的
     同日链内比较，任何天数锚出现即重置时段链。
  ④ 引语掩蔽：引号内（“” 「」 『』 ‘’·未闭合引语掩到行尾）的时间词不参与
     时钟——话语谈论的时间（问候「晚上好」/计划「等傍晚再返回」/医嘱
     「连服三日之后」）不是叙事时钟（金标准：诡秘/人生长恨误报主根因）；
  ⑤ 时段复合词豁免：时段词前邻「一半整两几/数字」（时长量词「一上午」）或
     后邻非「时分的里」的汉字（专名「黄昏隐士会」/名词「下午茶」/问候
     「晚上好」）→ 不算时点锚；
  ⑥ 时段链场景跨度上限：两时段锚相隔场景数 > 3 → 链过远重置不判
     （真作者密集分段下相隔数十段的两个时段词几乎必然隐式跨日）。

【北极星⑤ · 绝不 hard_gate】时序自由（倒叙/插叙/多线）是叙事手法，本 scanner
只捞**无标记的意外倒错**（无闪回标志、非倒叙模式下时间线倒退）。
DRAFT_TEMPORAL_ORDER_REVERSED 永远 advisory，绝不得加入 HARD_GATE_CODES。
env DRAFT_TEMPORAL_ORDER_MODE 三态（off/shadow/active·默认 active·
shadow 只 stderr 不产 violations）。

【金标准校准放量】金标准10作者100chunk零误报放量·2026-07-07：
主神大道/惊悚乐园/诡秘之主/遮天/剑来/将夜/佛本是道/轮回乐园/小世界其乐无穷/
人生长恨水长东 各 10 个连续 4 章 chunk（全书均匀取样·linear 显式声明·active）。
首轮 6/100 chunk 误报（8 violations·全为 scanner 误判非作者笔误），根因四类：
引语内时间词当叙事时钟（问候「晚上好」/对话计划「等傍晚再返回」「连服三日
之后」）、时段复合词（「黄昏隐士会」「下午茶」）、时长量词（「一上午」）、
时段链跨数十场景强行同日比较。据此加豁免④⑤⑥后复跑 0/100 零误报 →
默认 shadow→active。校准脚本/报告见 scratchpad temporal_calibration。

用法: python draft_temporal_order_scanner.py <draft> --project <root> [--cluster cluster_<key>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import cluster_lookup

ISSUE_CODE = "DRAFT_TEMPORAL_ORDER_REVERSED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 豁免③：可排序锚点场景数地板（低于此不判——两个孤锚可能是合法省叙/换线）
MIN_ANCHORED_SCENES = 3

_KEY_FROM_PATH = re.compile(r"cluster_([0-9]{3}[a-z]?)_draft")

# ── 时间锚词典 ──────────────────────────────────────────────
_NUM = r"[0-9０-９〇零一二三四五六七八九十百千两]+"
# 相对推进先扫（并掩蔽），防「第二天」被绝对序数正则抢走
_REL_FIXED_RE = re.compile(r"翌日|次日|隔天|隔日|第二天|第二日")
_REL_N_RE = re.compile(rf"({_NUM})[天日]之?后")
_ABS_RE = re.compile(rf"第({_NUM})[天日夜]")

# 闪回标志（豁免②·按盲区规格列表）：命中即整场景退出排序
_FLASHBACK_RE = re.compile(rf"回忆|想起|那时|当年|记忆里|{_NUM}年前")

# 时段序（豁免③：仅明确同日链内可比）
_TOD_RANKS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0, ("清晨", "黎明", "拂晓", "早晨", "一大早", "大清早")),
    (1, ("上午",)),
    (2, ("正午", "中午", "晌午")),
    (3, ("午后", "下午")),
    (4, ("黄昏", "傍晚", "日暮", "薄暮")),
    (5, ("夜里", "夜晚", "晚上", "入夜", "当晚")),
    (6, ("深夜", "半夜", "午夜", "子夜")),
)
# 「凌晨」刻意不收：跨零点归属次日，语义天然歧义（防误报）。
# 深夜(6)→清晨/上午/正午(≤2) 视为隐式跨日翻页（网文常漏写「翌日」），重置链不判。
_ROLLOVER_FROM_RANK = 6
_ROLLOVER_TO_RANK_MAX = 2

# 豁免⑥：时段同日链场景跨度上限（超过即视为隐式跨日·重置链不判）
MAX_TOD_SCENE_GAP = 3

# 豁免④：引语掩蔽（成对引号 + 网文多段引语「开引号无闭引号」掩到行尾）
_QUOTE_RES = (
    re.compile(r"“[^”\n]*”"), re.compile(r"“[^”\n]*$", re.MULTILINE),
    re.compile(r"「[^」\n]*」"), re.compile(r"「[^」\n]*$", re.MULTILINE),
    re.compile(r"『[^』\n]*』"), re.compile(r"『[^』\n]*$", re.MULTILINE),
    re.compile(r"‘[^’\n]*’"),
)

# 豁免⑤：时段词前邻时长量词字 / 后邻允许尾字白名单
_TOD_PREFIX_BLOCK = set("一半整两几0123456789０１２３４５６７８９")
_TOD_SUFFIX_ALLOW = set("时分的里")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _mode() -> str:
    """默认 active（金标准10作者100chunk零误报放量·2026-07-07）。"""
    m = (os.environ.get("DRAFT_TEMPORAL_ORDER_MODE") or "active").strip().lower()
    return m if m in ("off", "shadow", "active") else "active"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cn_to_int(raw: str) -> int | None:
    """阿拉伯/全角/中文数字 → int（支持 十/百/千 组合·解析失败返回 None）。"""
    s = str(raw or "").strip()
    if not s:
        return None
    normalized = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if normalized.isdigit():
        return int(normalized)
    total = 0
    rest = s
    for unit_char, unit_val in (("千", 1000), ("百", 100), ("十", 10)):
        if unit_char in rest:
            left, _, rest = rest.partition(unit_char)
            left = left.strip("零〇")
            if left and (len(left) != 1 or left not in _CN_DIGITS):
                return None
            total += (_CN_DIGITS[left] if left else 1) * unit_val
            rest = rest.lstrip("零〇")
    if rest:
        if len(rest) != 1 or rest not in _CN_DIGITS:
            return None
        total += _CN_DIGITS[rest]
    return total if total > 0 else None


def _split_scenes(text: str) -> list[str]:
    """空行段落块切场景（与 scene_receipts_scanner 同口径）。"""
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]


def _mask_quoted(text: str) -> str:
    """豁免④：掩蔽引号内话语（等长替换保 pos 稳定）。话语谈论的时间不是叙事时钟。"""
    for rx in _QUOTE_RES:
        text = rx.sub(lambda m: "＊" * len(m.group(0)), text)
    return text


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "一" <= ch <= "鿿"


def _extract_day_events(scene_text: str) -> list[dict]:
    """按出现顺序抽取天数锚：先掩蔽相对推进词，再扫绝对序数（防「第二天」误判绝对）。"""
    events: list[dict] = []
    for m in _REL_FIXED_RE.finditer(scene_text):
        events.append({"kind": "rel", "delta": 1, "text": m.group(0), "pos": m.start()})
    for m in _REL_N_RE.finditer(scene_text):
        delta = _cn_to_int(m.group(1))
        if delta:
            events.append({"kind": "rel", "delta": delta, "text": m.group(0), "pos": m.start()})
    masked = _REL_FIXED_RE.sub(lambda m: "＊" * len(m.group(0)), scene_text)
    masked = _REL_N_RE.sub(lambda m: "＊" * len(m.group(0)), masked)
    for m in _ABS_RE.finditer(masked):
        day = _cn_to_int(m.group(1))
        if day:
            events.append({"kind": "abs", "day": day, "text": m.group(0), "pos": m.start()})
    events.sort(key=lambda e: e["pos"])
    return events


def _extract_tod(scene_text: str) -> dict | None:
    """场景内最先出现的**合法**时段词（场景开场时段）。

    豁免⑤：前邻时长量词字（「一上午」「半晌午」是时长非时点）或后邻不在
    白名单的汉字（「黄昏隐士会」「下午茶」「晚上好」是复合词/专名/问候）→ 跳过。"""
    best: dict | None = None
    for rank, terms in _TOD_RANKS:
        for term in terms:
            start = 0
            while True:
                pos = scene_text.find(term, start)
                if pos < 0:
                    break
                start = pos + 1
                prev_ch = scene_text[pos - 1] if pos > 0 else ""
                next_ch = scene_text[pos + len(term)] if pos + len(term) < len(scene_text) else ""
                if prev_ch in _TOD_PREFIX_BLOCK:
                    continue
                if _is_cjk(next_ch) and next_ch not in _TOD_SUFFIX_ALLOW:
                    continue
                if best is None or pos < best["pos"]:
                    best = {"rank": rank, "text": term, "pos": pos}
                break
    return best


def _resolve_cluster_id(cluster_arg, draft_path) -> str | None:
    """--cluster 优先（cluster_lookup 权威归一·禁机械拼接）；缺省时从 draft 路径推 key。"""
    cid = cluster_lookup.normalize_cluster_id(cluster_arg) if cluster_arg else None
    if cid:
        return cid
    m = _KEY_FROM_PATH.search(str(draft_path))
    if m:
        return cluster_lookup.normalize_cluster_id(f"cluster_{m.group(1)}")
    return None


def _narrative_mode_for(project_root, cluster_id) -> str | None:
    """从 事件簇.json 反查 narrative_mode。未声明时按系统默认：
    clusters[0]（黄金三章）= in_medias_res，其余 = linear（对齐 build_manifest 口径）。
    反查不到返回 None（不门控·继续扫描）。"""
    if not project_root or not cluster_id:
        return None
    path = Path(project_root) / "_数据库" / "事件簇.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    clusters = obj.get("clusters") if isinstance(obj, dict) else None
    if not isinstance(clusters, list):
        return None
    for idx, cl in enumerate(clusters):
        if not isinstance(cl, dict):
            continue
        if cluster_lookup.normalize_cluster_id(cl.get("cluster_id")) == cluster_id:
            declared = str(cl.get("narrative_mode") or "").strip()
            if declared:
                return declared
            return "in_medias_res" if idx == 0 else "linear"
    return None


def _detect_reversals(scenes: list[str]) -> tuple[list[dict], int, int]:
    """核心偏序检测。返回 (reversals, anchored_scene_count, flashback_scene_count)。

    时间线模型：
      - timeline_day：绝对天数时钟（绝对锚设值，相对锚只向前推进）；
      - 后场景绝对天数 < 当前时钟 = 天数倒错；
      - 时段链：同日内 rank 递减 = 时段倒错；任何天数锚（绝对/相对）出现即视为
        跨日介入，重置时段链（豁免③：时段序仅明确同日可比）。
    """
    reversals: list[dict] = []
    timeline_day: int | None = None
    last_day_ref: dict | None = None   # {"scene_index", "text", "day"}
    last_tod: dict | None = None       # {"scene_index", "rank", "text"}
    anchored = 0
    flashbacks = 0

    for idx, scene_text in enumerate(scenes):
        if _FLASHBACK_RE.search(scene_text):
            flashbacks += 1
            continue  # 豁免②：闪回场景整体退出排序（不重置时间线，链对闪回透明）

        masked_scene = _mask_quoted(scene_text)  # 豁免④：引语内时间词不进时钟
        day_events = _extract_day_events(masked_scene)
        tod = _extract_tod(masked_scene)
        if day_events or tod:
            anchored += 1

        for ev in day_events:
            if ev["kind"] == "abs":
                if timeline_day is not None and ev["day"] < timeline_day and last_day_ref:
                    reversals.append({
                        "code": ISSUE_CODE,
                        "kind": "absolute_day_reversal",
                        "severity": "minor",
                        "gate_level": "advisory",
                        "scene_index_pair": [last_day_ref["scene_index"], idx],
                        "anchor_pair": [last_day_ref["text"], ev["text"]],
                        "reason": (f"场景{last_day_ref['scene_index']}时间线已到"
                                   f"「{last_day_ref['text']}」(第{timeline_day}天)，"
                                   f"场景{idx}倒退到「{ev['text']}」(第{ev['day']}天)"
                                   f"且无闪回标志"),
                    })
                timeline_day = ev["day"]
                last_day_ref = {"scene_index": idx, "text": ev["text"], "day": ev["day"]}
            else:  # rel：只向前推进（永不产倒错）
                if timeline_day is not None:
                    timeline_day += ev["delta"]
                last_day_ref = {"scene_index": idx, "text": ev["text"], "day": timeline_day}

        if day_events:
            # 跨日锚介入 → 时段链重置（本场景时段作为新日链起点）
            last_tod = ({"scene_index": idx, "rank": tod["rank"], "text": tod["text"]}
                        if tod else None)
        elif tod is not None:
            if (last_tod is not None
                    and idx - last_tod["scene_index"] > MAX_TOD_SCENE_GAP):
                pass  # 豁免⑥：时段链跨度过大 → 几乎必然隐式跨日，重置链不判
            elif (last_tod is not None
                    and last_tod["rank"] == _ROLLOVER_FROM_RANK
                    and tod["rank"] <= _ROLLOVER_TO_RANK_MAX):
                pass  # 深夜→清晨类隐式跨日翻页：重置链不判（防「漏写翌日」误报）
            elif last_tod is not None and tod["rank"] < last_tod["rank"]:
                reversals.append({
                    "code": ISSUE_CODE,
                    "kind": "time_of_day_reversal",
                    "severity": "minor",
                    "gate_level": "advisory",
                    "scene_index_pair": [last_tod["scene_index"], idx],
                    "anchor_pair": [last_tod["text"], tod["text"]],
                    "reason": (f"同日链内场景{last_tod['scene_index']}已到时段"
                               f"「{last_tod['text']}」，场景{idx}回到更早时段"
                               f"「{tod['text']}」且两锚之间无任何跨日锚介入、无闪回标志"),
                })
            last_tod = {"scene_index": idx, "rank": tod["rank"], "text": tod["text"]}

    return reversals, anchored, flashbacks


def scan(draft_path, project_root=None, cluster_arg=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "draft_temporal_order",
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

    # 豁免①：narrative_mode 门控（倒叙 cluster 整体 skip）
    cluster_id = _resolve_cluster_id(cluster_arg, draft_path)
    out["cluster_id"] = cluster_id
    narrative_mode = _narrative_mode_for(project_root, cluster_id)
    if narrative_mode:
        out["narrative_mode"] = narrative_mode
    if narrative_mode == "in_medias_res":
        out["note"] = "narrative_mode=in_medias_res（黄金三章倒叙·时序重排是设计）; skipped"
        return out

    scenes = _split_scenes(text)
    out["scene_count"] = len(scenes)
    reversals, anchored, flashbacks = _detect_reversals(scenes)
    out["anchored_scene_count"] = anchored
    out["flashback_scene_count"] = flashbacks

    # 豁免③：锚点稀疏不判
    if anchored < MIN_ANCHORED_SCENES:
        out["reversal_count"] = 0
        out["note"] = (f"anchored scenes {anchored} < {MIN_ANCHORED_SCENES}"
                       f"（锚点稀疏·孤锚可能是合法省叙/换线）; not judged")
        return out

    out["reversal_count"] = len(reversals)
    out["reversal_samples"] = reversals[:5]
    if reversals:
        msg = (f"draft internal temporal order reversed: {len(reversals)}"
               f"（无标记时序倒退·倒叙/插叙请标闪回或设 narrative_mode）")
        if mode == "active":
            out["violations"] = reversals
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] draft_temporal_order: {msg}", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="草稿内部时序倒错扫描（确定性·advisory·active 默认·金标准零误报放量）")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project, args.cluster)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
