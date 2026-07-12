#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 novel-archivist 生成确定性的 cluster 实体证据基线。

报告包含已登记实体的出场次数、对白条数估计、首现位置以及高频新专名候选。
统计只提供证据，实体归类与状态变化仍由 archivist 根据正文判断。

用法（cluster-save-state step 5 spawn archivist 之前跑）：
  python cluster_entity_stats.py <project_root> --cluster <key> \
      [--draft <path>] [--min-new-name-freq 3] [--out <path>]

输出：_数据库/.wal/cluster_<key>_entity_stats.json（schema 见 _build_report）

同一输入产生同一报告；名称变体按长度降序遮罩，避免长名被短名重复计数。
新专名候选采用说话人模式和项目 false-positive 表过滤，默认至少出现三次。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_json import atomic_write_text  # noqa: E402

try:
    import cluster_lookup
except Exception:  # noqa: BLE001
    cluster_lookup = None

_CJK_RE = re.compile(r"[一-鿿]")

# 引号左右配对（与 character_index / style_analyzer 一致：U+201C≠U+201D）
_QUOTE_RE = re.compile(r'"([^"\n]{1,120})"|“([^”\n]{1,120})”|「([^」\n]{1,120})」')

# 说话动词后只认开引号，避免把引文内以“道”结尾的词误当说话人。
_SPEAKER_RE = re.compile(
    r'([一-鿿]{2,4})(?:说道?|道|问道?|答道?|笑道?|骂道?|喊道?|嘀咕|开口)'
    r'(?=[：:，,。．！？!?、“「『"])')

# 引号邻域后缀归属用的说话动词（「……」伊莱说。/ “……”她朝伊莱喊）
_SUFFIX_VERB_RE_TPL = r'{name}.{{0,4}}?(?:说|道|问|答|喊|叹|骂|开口|嘀咕|回)'

# ── 新专名候选过滤集（与 validate_chapter 同源·保持口径一致） ──
_BAD_FIRST_CHARS = set(
    "的了在是这那和与你我他她它们之就只也都还又再已便"
    "不没否非无别莫勿"
    "上下里外前后旁中"
    "才刚很太极颇较挺真"
    "有要会能可应该需想"
    "但及并而却则即既故若虽因由从向往朝己新各另随当")
_VERB_PHRASE_BLACKLIST = {
    "知道", "不知", "我知", "你知", "他知", "她知", "都知", "也知", "已知", "得知",
    "看见", "看到", "听见", "听到", "想到", "见到", "感到", "察觉",
    "没说", "再说", "又说", "也说", "却说", "竟说",
    "没问", "再问", "又问", "也问", "追问",
    "没答", "没看", "没听", "没想", "没动",
    "为什", "什么", "怎么", "为何", "如何", "那么", "这么",
    "并没", "也没", "还没", "都没", "却没",
    "已经", "曾经", "正在", "刚才", "刚刚", "马上", "立刻",
}
_FUNCTION_SUBSTRINGS = ("似乎", "仿佛", "好像", "已经", "正在", "忽然",
                        "突然", "这时", "此时", "竟然", "居然", "渐渐")
_COMMON_WORDS = {"这时", "此时", "那人", "众人", "有人", "旁边", "对面", "身后", "其中",
                 "忽然", "突然", "随后", "终于", "这里", "一个", "几个", "三人",
                 "对方", "众生", "众僧", "众弟子"}


def _norm_cid(key) -> str:
    if cluster_lookup:
        try:
            n = cluster_lookup.normalize_cluster_id(key)
            if n:
                return n
        except Exception:  # noqa: BLE001
            pass
    k = str(key).replace("cluster_", "")
    return f"cluster_{k}"


def load_json(p: Path, default):
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        pass
    return default


def _cjk_len(text: str) -> int:
    return len(_CJK_RE.findall(text))


# ═══════════════════ 已知实体收集（人物卡 + 角色池） ═══════════════════

def collect_known_entities(db: Path) -> list[dict]:
    """[{id, name, aliases[], source}]·按 name 去重（人物卡优先于角色池）。"""
    out, seen = [], set()
    cards = load_json(db / "人物卡.json", {})
    for c in cards.get("characters", []) if isinstance(cards, dict) else []:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        aliases = [a for a in (c.get("aliases") or []) if isinstance(a, str) and a]
        out.append({"id": c.get("id"), "name": name, "aliases": aliases, "source": "人物卡"})
    pool = load_json(db / "角色池.json", {})
    for grp in ("core", "emerged", "extras"):
        for c in pool.get(grp, []) if isinstance(pool, dict) else []:
            if not isinstance(c, dict):
                continue
            name = c.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"id": c.get("id"), "name": name,
                        "aliases": [a for a in (c.get("aliases") or []) if isinstance(a, str) and a],
                        "source": "角色池"})
    return out


# ═══════════════════ 出场计数（变体长度降序遮罩·子串防重） ═══════════════════

def count_mentions(body: str, entities: list[dict]) -> dict:
    """返回 {name: {"mention_count", "first_offset"}}。
    全部变体（name+aliases）按长度降序在共享遮罩副本上计数：长变体先计先遮，
    短变体（含别名是全名子串、或某实体名是另一实体名子串）不重复计。"""
    variants = []  # (variant, canonical_name)
    for e in entities:
        variants.append((e["name"], e["name"]))
        for a in e["aliases"]:
            variants.append((a, e["name"]))
    # 长度降序 + 字典序（确定性 tie-break）
    variants.sort(key=lambda t: (-len(t[0]), t[0]))
    masked = body
    stats = {e["name"]: {"mention_count": 0, "first_offset": None} for e in entities}
    for var, canon in variants:
        hits = [m.start() for m in re.finditer(re.escape(var), masked)]
        if not hits:
            continue
        st = stats[canon]
        st["mention_count"] += len(hits)
        first = hits[0]
        if st["first_offset"] is None or first < st["first_offset"]:
            st["first_offset"] = first
        masked = masked.replace(var, "\x00" * len(var))  # 等长遮罩·保位置
    return stats


def _first_context(body: str, offset: int, width: int = 80) -> str:
    start = max(0, body.rfind("\n", 0, offset) + 1,
                body.rfind("。", 0, offset) + 1)
    end = body.find("。", offset)
    if end < 0:
        end = min(len(body), offset + width)
    return body[start:end + 1].strip()[:width]


# ═══════════════════ 对白归属估计（引号邻域） ═══════════════════

def estimate_dialogue(body: str, entities: list[dict]) -> tuple[dict, int, int]:
    """引号邻域归属：每条引文先在**同段前缀**（引号前 ≤30 字）找最近的实体变体
    （取结束位置最靠近引号者·同位取更长变体）；前缀无命中再看**同段后缀**
    （引号后 ≤30 字）里「实体名 + ≤4 字内说话动词」。都无 → 未归属。
    返回 ({name: count}, quote_count, unattributed)。"""
    variant_of = []  # (variant, canonical) 长度降序
    for e in entities:
        variant_of.append((e["name"], e["name"]))
        for a in e["aliases"]:
            variant_of.append((a, e["name"]))
    variant_of.sort(key=lambda t: (-len(t[0]), t[0]))

    counts = {e["name"]: 0 for e in entities}
    quote_count = 0
    unattributed = 0
    for m in _QUOTE_RE.finditer(body):
        quote_count += 1
        qstart, qend = m.start(), m.end()
        para_start = body.rfind("\n", 0, qstart) + 1
        prefix = body[max(para_start, qstart - 30):qstart]
        owner = None
        # 前缀：最近（结束位置最大）的变体
        best_end = -1
        for var, canon in variant_of:
            idx = prefix.rfind(var)
            if idx >= 0 and idx + len(var) > best_end:
                best_end = idx + len(var)
                owner = canon
        if owner is None:
            para_end = body.find("\n", qend)
            if para_end < 0:
                para_end = len(body)
            suffix = body[qend:min(para_end, qend + 30)]
            best_pos = None
            for var, canon in variant_of:
                sm = re.search(_SUFFIX_VERB_RE_TPL.format(name=re.escape(var)), suffix)
                if sm and (best_pos is None or sm.start() < best_pos):
                    best_pos = sm.start()
                    owner = canon
        if owner is None:
            unattributed += 1
        else:
            counts[owner] += 1
    return counts, quote_count, unattributed


# ═══════════════════ 新专名候选（启发式·validate_chapter 同源） ═══════════════════

def find_new_name_candidates(body: str, known_names: set, false_positives: set,
                             min_freq: int) -> list[dict]:
    cands = set()
    for m in _SPEAKER_RE.finditer(body):
        name = m.group(1)
        if len(name) < 2 or name[0] in _BAD_FIRST_CHARS:
            continue
        if name in _VERB_PHRASE_BLACKLIST or name in _COMMON_WORDS:
            continue
        if len(name) >= 2 and name[1] in "不没又也已再还才刚都":
            continue
        if any(fw in name for fw in _FUNCTION_SUBSTRINGS):
            continue
        cands.add(name)
    cands -= known_names
    cands -= false_positives
    # 已知名的子串/超串不算新专名（「玛莎」已知 → 「玛莎修女」按同人处理·不另列）
    cands = {c for c in cands
             if not any((c in k or k in c) for k in known_names)}
    out = []
    for name in sorted(cands):
        freq = body.count(name)
        if freq < min_freq:
            continue
        first = body.find(name)
        out.append({"name": name, "mention_count": freq,
                    "first_offset": first,
                    "first_ratio": round(first / max(1, len(body)), 4),
                    "first_context": _first_context(body, first),
                    "evidence": "speaker_pattern"})
    out.sort(key=lambda d: (-d["mention_count"], d["name"]))
    return out


# ═══════════════════ 汇总 ═══════════════════

def build_stats(project: Path, cluster_id: str, draft_path: Path,
                min_new_name_freq: int = 3) -> dict:
    db = project / "_数据库"
    body = draft_path.read_text(encoding="utf-8-sig")
    entities = collect_known_entities(db)
    mention = count_mentions(body, entities)
    dialogue, quote_count, unattributed = estimate_dialogue(body, entities)

    known_names = set()
    for e in entities:
        known_names.add(e["name"])
        known_names.update(e["aliases"])
        if e.get("id"):
            known_names.add(e["id"])
    ci = load_json(db / "character_index.json", {})
    fp = set(ci.get("false_positives", []) if isinstance(ci, dict) else [])
    candidates = find_new_name_candidates(body, known_names, fp, min_new_name_freq)

    known_out = []
    for e in entities:
        st = mention[e["name"]]
        row = {"id": e.get("id"), "name": e["name"], "source": e["source"],
               "aliases": e["aliases"],
               "mention_count": st["mention_count"],
               "dialogue_count_estimate": dialogue[e["name"]],
               "appears": st["mention_count"] > 0,
               "first_offset": st["first_offset"],
               "first_ratio": (round(st["first_offset"] / max(1, len(body)), 4)
                               if st["first_offset"] is not None else None),
               "first_context": (_first_context(body, st["first_offset"])
                                 if st["first_offset"] is not None else None)}
        known_out.append(row)
    known_out.sort(key=lambda d: (-d["mention_count"], d["name"]))

    return {
        "schema_version": 1,
        "generator": "cluster_entity_stats",
        "cluster_id": cluster_id,
        "draft_path": str(draft_path),
        "draft_chars": len(body),
        "draft_cjk": _cjk_len(body),
        "params": {"min_new_name_freq": min_new_name_freq},
        "known_entities": known_out,
        "new_name_candidates": candidates,
        "totals": {
            "known_registered": len(known_out),
            "known_appearing": sum(1 for r in known_out if r["appears"]),
            "new_candidates": len(candidates),
            "quote_count": quote_count,
            "unattributed_quotes": unattributed,
        },
        "_doc": ("archivist 的确定性实体证据基线；高频实体缺失时应回正文核对，"
                 "new_name_candidates 是允许误报的启发式候选。"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 cluster 实体确定性证据基线")
    ap.add_argument("project")
    ap.add_argument("--cluster", required=True, help="cluster key（001 或 cluster_001）")
    ap.add_argument("--draft", default=None, help="草稿路径（默认 章节/cluster_<key>_draft/cluster_<key>_draft.txt）")
    ap.add_argument("--min-new-name-freq", type=int, default=3)
    ap.add_argument("--out", default=None, help="输出路径（默认 _数据库/.wal/cluster_<key>_entity_stats.json）")
    args = ap.parse_args(argv)

    project = Path(args.project)
    cid = _norm_cid(args.cluster)
    key = cid.replace("cluster_", "")
    draft = Path(args.draft) if args.draft else (
        project / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt")
    if not draft.exists():
        print(f"[FATAL] cluster 草稿不存在: {draft}", file=sys.stderr)
        return 2

    report = build_stats(project, cid, draft, args.min_new_name_freq)
    out = Path(args.out) if args.out else (
        project / "_数据库" / ".wal" / f"cluster_{key}_entity_stats.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(report, ensure_ascii=False, indent=2))
    t = report["totals"]
    print(f"[OK] entity_stats 已写入: {out}"
          f"（已知 {t['known_registered']} / 出场 {t['known_appearing']}"
          f" / 新专名候选 {t['new_candidates']} / 引文 {t['quote_count']}）")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
