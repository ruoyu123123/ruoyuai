#!/usr/bin/env python3
"""gen_chapter_titles.py — cluster-write step 6.2 章标题三段式（确定性两端 + Claude 亲笔命名）

流程（emit-brief → novel-titler → apply）：
  (a) --emit-brief   读 splitter WAL 章范围 + 档位分级 + blueprint hint + per-book
      title_style 校准 + 历史标题全集，确定性产 novel-titler 任务合同
      `_数据库/.wal/cluster_<key>_title_brief.json`（逐章条目含正文路径）。
  (b) 主代理 spawn novel-titler（Claude 亲笔逐章命名·合约见 .claude/agents/novel-titler.md）
      读 brief 产 `_数据库/.wal/cluster_<key>_titles.json`。
  (c) --apply        确定性验收（干净度 `_is_clean_title` / ≤14 字 / 历史严禁重复——
      精确相等或母题包含都算重复）+「第NNN章 标题」头重写 + cluster_blueprint/进度.json
      回填 + receipt 落盘。验收不过的章退回 brief pending（带 rejected 原因与确定性
      fallback_title 兜底候选），主代理重 spawn novel-titler 重命名后重跑 --apply。

# 网文章标题三档策略（《BookC》《饲养全人类》《十日终焉》《斩神》70 章调研）

| 档 | 比例 | 字数 | 用途 | 例 |
|---|---|---|---|---|
| **normal** | 80% | 2-4 字 | 主体章节冷峻意象/名词 | 「葬」「断牙」「孤峰」「断梯」 |
| **mid** | 15% | 5-8 字 | cluster 收尾 / fate event 节点 / 重大转折 | 「凿齿夜袭十三死」 |
| **high** | 5% | 8-14 字 | 绝对高潮章（卷高潮 / 神战 / 史诗节点） | 「徇射穿了第二个太阳」 |

# 自动分级（emit-brief 确定性落进 brief）
- splitter WAL 末章（pending_tail 不存在时）→ mid（cluster 收尾章）
- per-chapter changes 带 ecas_metadata.cluster_position=tail → mid
- --high-chapters N,M 显式指定 → high
- 其余 → normal

# 退出码
- 0 = ok（brief 产出 / 全章验收通过 receipt 落盘 / 0 章 no-op）
- 2 = pending_titles（titles.json 缺失、契约不符或有章被验收退回——主代理按 brief
      spawn novel-titler 补件/重命名后重跑 --apply）
- 3 = fatal（splitter WAL / brief / 进度.json / 章正文缺失等流程契约破损）

用法:
  python gen_chapter_titles.py --project <path> --cluster <key> --emit-brief [--high-chapters 11,40]
  python gen_chapter_titles.py --project <path> --cluster <key> --apply
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atomic_json import atomic_write_json  # noqa: E402
import cluster_lookup  # noqa: E402

BRIEF_SCHEMA = "title_brief.v1"
TITLES_SCHEMA = "novel-titler.v1"
RECEIPT_SCHEMA = "gen_chapter_titles.receipt.v1"


def parse_chapters(s: str) -> list[int]:
    result = []
    for part in s.split(','):
        part = part.strip()
        if '-' in part:
            a, b = map(int, part.split('-'))
            result.extend(range(a, b + 1))
        else:
            result.append(int(part))
    return result


def parse_high_list(s: str | None) -> set[int]:
    if not s:
        return set()
    return set(parse_chapters(s))


def read_chapter(project: Path, ch: int) -> tuple[Path, str]:
    p = project / '章节' / f'第{ch:03d}章' / f'第{ch:03d}章.txt'
    if not p.exists():
        return p, ''
    return p, p.read_text(encoding='utf-8')


def read_changes(project: Path, ch: int) -> dict:
    p = project / '章节' / f'第{ch:03d}章' / f'第{ch:03d}章_changes.json'
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def strip_existing_title(text: str) -> str:
    """如果文件开头已有「第NNN章 X」格式标题，去掉"""
    pattern = re.compile(r'^第\s*[一二三四五六七八九十百千\d]+\s*章[ \t]+\S.*?\n\s*\n', re.MULTILINE)
    m = pattern.match(text)
    if m:
        return text[m.end():]
    return text


# 🔴 storyboard hint 是「大纲指令/场景描述文本」(如
# 「倒叙强冲突开场。铁十字街一间逼仄昏暗的出租屋里，主角顶着占卜…」)，**不是章标题**。
# 验收侧绝不能把它原样当标题写进正文。下面 helper 负责：① 判定字符串是否
# 「干净到能当标题」（--apply 硬验收判据）；② 给确定性 fallback 兜底候选。
_DIRECTIVE_MARKERS = (
    "主角", "男主", "女主", "某角色", "占位", "倒叙", "强冲突", "开场", "场景",
    "回溯", "反转", "揭底", "镜头", "POV", "pov", "storyboard", "scene",
    "高潮章", "本章", "本场", "钩子", "兑现", "伏笔", "节点",
)
_TITLE_PUNCT = "。！？，、；：…．,.!?;\n\r\t"


def _strip_title_wrappers(s: str) -> str:
    return (s or "").strip().strip("「」“”\"'《》【】（）() *—-").strip()


def _reject_reason(s: str) -> str | None:
    """标题验收判据单一真理源：干净返回 None，否则返回具体拒绝原因。

    干净标题：剥包裹符后非空、≤14 字、不含句末/分句标点、不含大纲指令/占位标记词。
    storyboard hint 多含句号/逗号或「主角」「倒叙强冲突开场」之类 → 判脏，拒用。
    """
    s = _strip_title_wrappers(s)
    if not s:
        return "标题为空"
    if len(s) > 14:
        return f"超长（{len(s)} 字 > 14）"
    for p in _TITLE_PUNCT:
        if p in s:
            return f"含标点 {p!r}"
    for m in _DIRECTIVE_MARKERS:
        if m in s:
            return f"含大纲指令/占位标记词「{m}」"
    return None


def _is_clean_title(s: str) -> bool:
    """判断一个字符串是否像「干净的章标题」(而非 storyboard 指令/描述句)。"""
    return _reject_reason(s) is None


def _clean_fallback_title(ch: int, hint: str) -> str:
    """确定性兜底候选（验收侧兜底命名冲突用）：

    优先级 = 干净 hint > 保守「第N章」无副标题。
    **绝不**把含句末标点/占位词「主角」的 storyboard 指令文本(如
    「倒叙强冲突开场。…主角顶着占卜」)当标题——那种 hint 判脏后退「第N章」。
    """
    if _is_clean_title(hint):
        return _strip_title_wrappers(hint)
    return f"第{ch}章"


def _title_conflict(title: str, taken: list[str]) -> str | None:
    """历史严禁重复：精确相等或互为包含（母题级重复，如「葬」↔「葬礼」）→ 返回冲突方。"""
    for t in taken:
        if not t:
            continue
        if title == t or title in t or t in title:
            return t
    return None


def classify_tier(ch: int, changes: dict, high_set: set[int]) -> str:
    """自动分级 normal/mid/high"""
    if ch in high_set:
        return 'high'
    pos = changes.get('ecas_metadata', {}).get('cluster_position', '')
    if pos == 'tail':
        return 'mid'
    return 'normal'


def _load_title_style(project: Path) -> dict | None:
    """从风格库读 title_style.json，做 per-book 校准。"""
    style_path = project / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return None
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return None
    for parent in [project, *project.parents]:
        f = parent / "workspace" / "styles" / work / "title_style.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _paths(project: Path, key: str) -> dict[str, Path]:
    wal_dir = project / "_数据库" / ".wal"
    return {
        "wal": wal_dir / f"splitter_cluster_{key}_decisions.json",
        "brief": wal_dir / f"cluster_{key}_title_brief.json",
        "titles": wal_dir / f"cluster_{key}_titles.json",
        "receipt": wal_dir / f"cluster_{key}_title_apply_receipt.json",
    }


def _load_json_or_none(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _build_hint_map(progress: dict) -> dict[int, str]:
    """cluster_blueprint（normalize_blueprint 归一成 dict）→ {ch: storyboard title hint}。"""
    hint_map: dict[int, str] = {}
    for _cid, cdata in cluster_lookup.normalize_blueprint(progress).items():
        if not isinstance(cdata, dict):
            continue
        for p in cdata.get('scene_storyboard', []) or []:
            if isinstance(p, dict) and 'ch' in p:
                hint_map[p['ch']] = p.get('title', '')
    return hint_map


_CH_DIR_RE = re.compile(r"^第(\d{1,4})章$")
_HEAD_TITLE_RE = re.compile(r"^第\s*(\d{1,4})\s*章[ \t]+(\S.*)$")


def _collect_history_titles(project: Path, exclude_chs: set[int]) -> list[str]:
    """收历史标题全集（权威源=物理章文件首行「第NNN章 标题」·排除本 cluster 章号）。

    排除本 cluster 章：重跑重命名时不把自己上次的标题当历史卡死自己。
    """
    out: list[str] = []
    seen: set[str] = set()
    root = project / "章节"
    if not root.is_dir():
        return out
    entries: list[tuple[int, Path]] = []
    for d in root.iterdir():
        m = _CH_DIR_RE.match(d.name)
        if not m or not d.is_dir():
            continue
        ch = int(m.group(1))
        if ch in exclude_chs:
            continue
        f = d / f"{d.name}.txt"
        if f.is_file():
            entries.append((ch, f))
    for _ch, f in sorted(entries):
        try:
            with f.open(encoding="utf-8") as fh:
                first = fh.readline().strip()
        except (OSError, UnicodeError):
            continue
        m = _HEAD_TITLE_RE.match(first)
        if not m:
            continue
        t = m.group(2).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _normalize(cluster: str) -> tuple[str, str] | None:
    """归一 cluster 标识 → (canonical_cluster_id, key)。cluster_lookup 是唯一权威。"""
    cid = cluster_lookup.normalize_cluster_id(cluster)
    if cid is None:
        return None
    return cid, cid.replace("cluster_", "", 1)


def emit_brief(project: Path, cluster: str, high_chapters: str = "") -> int:
    """确定性产 novel-titler 任务合同 title_brief.json（全量重建·幂等）。"""
    norm = _normalize(cluster)
    if norm is None:
        print(f"[FATAL] 无效 cluster: {cluster!r}", file=sys.stderr)
        return 3
    cid, key = norm
    paths = _paths(project, key)

    wal = _load_json_or_none(paths["wal"])
    if wal is None:
        print(f"[FATAL] splitter WAL 缺失或损坏: {paths['wal']}"
              "（step 6.1 chapter_splitter 必须先落盘）", file=sys.stderr)
        return 3
    chapter_range = wal.get("chapter_range") or []
    pending_tail_exists = bool((wal.get("pending_tail") or {}).get("exists"))

    brief: dict = {
        "schema_version": BRIEF_SCHEMA,
        "cluster_id": cid,
        "agent": "novel-titler",
        "project": str(project),
        "chapter_range": chapter_range,
        "pending_tail_exists": pending_tail_exists,
        "no_op": not chapter_range,
        "output_path": str(paths["titles"]),
        "accepted": {},
    }

    # 🔴 pending_tail 守卫：整稿 < 单章下限时 splitter 切 0 章·全退 pending_tail 等下
    # cluster 拼接（chapter_range=[]）→ 本 cluster 无章可命名 → no-op brief 放行
    # （--apply 消费 no_op 写 no-op receipt·不 spawn novel-titler）。
    if not chapter_range:
        brief.update({
            "chapters": [], "high_chapters": [], "history_titles": [], "title_style": None,
            "no_op_reason": "本 cluster 0 章（整稿 < 单章下限·退 pending_tail 等下 cluster 拼接）",
        })
        atomic_write_json(paths["brief"], brief)
        print("[gen_chapter_titles] 本 cluster 0 章（整稿退 pending_tail 等下 cluster 拼接）"
              "·无章可命名·no-op brief 已落盘", file=sys.stderr)
        return 0

    chapters = list(range(int(chapter_range[0]), int(chapter_range[1]) + 1))
    high_set = parse_high_list(high_chapters)

    progress_path = project / '_数据库' / '进度.json'
    progress = _load_json_or_none(progress_path)
    if progress is None:
        print(f"[FATAL] 进度.json 缺失或损坏: {progress_path}", file=sys.stderr)
        return 3
    hint_map = _build_hint_map(progress)

    title_style = _load_title_style(project)
    if title_style:
        td = title_style.get("tier_distribution_pct", {})
        print(f"[gen_chapter_titles] per-book title_style 已加载："
              f"normal={td.get('normal', 0):.0%} / mid={td.get('mid', 0):.0%} / high={td.get('high', 0):.0%}")
    else:
        print("[gen_chapter_titles] 无 per-book title_style，走默认 80/15/5", file=sys.stderr)

    history = _collect_history_titles(project, exclude_chs=set(chapters))

    entries = []
    for ch in chapters:
        p, body = read_chapter(project, ch)
        if not body:
            print(f"[FATAL] 章正文缺失: {p}（splitter WAL 声明 ch{ch} ∈ {chapter_range}）",
                  file=sys.stderr)
            return 3
        hint = hint_map.get(ch, '')
        tier = classify_tier(ch, read_changes(project, ch), high_set)
        if tier == 'normal' and ch == chapters[-1] and not pending_tail_exists:
            # splitter WAL 权威末章 = cluster 收尾章 → mid；
            # pending_tail 存在时末段属下 cluster，实切末章不是收尾，不升档。
            tier = 'mid'
        entries.append({
            "ch": ch,
            "tier": tier,
            "hint": hint,
            "body_path": p.relative_to(project).as_posix(),
            "fallback_title": _clean_fallback_title(ch, hint),
        })

    brief.update({
        "chapters": entries,
        "high_chapters": sorted(high_set),
        "history_titles": history,
        "title_style": title_style,
    })
    atomic_write_json(paths["brief"], brief)
    tiers = ', '.join(f"ch{e['ch']}={e['tier']}" for e in entries)
    print(f"[gen_chapter_titles] brief 就绪: {len(entries)} 章（{tiers}）· 历史标题 {len(history)} 个")
    print(f"  → 主代理 spawn novel-titler（TITLE_BRIEF_PATH={paths['brief']} · "
          f"OUTPUT_PATH={paths['titles']}）产 titles.json 后跑 --apply")
    return 0


def _apply_blueprint_titles(project: Path, new_titles: dict[int, str]) -> int:
    """已验收标题回填 cluster_blueprint/进度.json。

    写回必须 in-place 改持久化对象（normalize_blueprint 会新建 dict，改它不落盘）。
    故按 cluster_blueprint 真实形态原地改：
      dict → 遍历各 cluster 的 scene_storyboard
      list（逐章 scene 记录的旧项目遗留形态）→ 直接遍历列表项
    """
    progress_path = project / '_数据库' / '进度.json'
    progress = _load_json_or_none(progress_path)
    if progress is None:
        print(f"[FATAL] 进度.json 缺失或损坏: {progress_path}", file=sys.stderr)
        return 3
    _raw_bp = progress.get('cluster_blueprint')

    def _apply_title(cp):
        if isinstance(cp, dict) and cp.get('ch') in new_titles:
            cp['_old_title'] = cp.get('title', '')
            cp['title'] = new_titles[cp['ch']]

    if isinstance(_raw_bp, dict):
        for _cid, cdata in _raw_bp.items():
            if not isinstance(cdata, dict):
                continue
            for cp in cdata.get('scene_storyboard', []) or []:
                _apply_title(cp)
    elif isinstance(_raw_bp, list):
        for cp in _raw_bp:
            _apply_title(cp)
    atomic_write_json(progress_path, progress)
    return 0


def _write_receipt(path: Path, cid: str, *, accepted: dict, no_op: bool,
                   plan_id=None, reason: str | None = None) -> dict:
    """全部章验收通过（或 no-op）后写确定性回执（plan judge_report_path 锚定物）。"""
    tier_counts = {'normal': 0, 'mid': 0, 'high': 0}
    titles: dict[str, str] = {}
    for ch_str, rec in sorted(accepted.items(), key=lambda kv: int(kv[0])):
        titles[ch_str] = rec.get("title", "")
        tier = rec.get("tier", "normal")
        if tier in tier_counts:
            tier_counts[tier] += 1
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "agent": "novel-titler",
        "cluster_id": cid,
        "completed": True,
        "no_op": no_op,
        "titles": titles,
        "tier_counts": tier_counts,
        "plan_id": plan_id,
    }
    if reason:
        receipt["reason"] = reason
    atomic_write_json(path, receipt)
    return receipt


def _validate_titles_payload(payload: dict | None, cid: str,
                             pending: list[dict]) -> list[str]:
    """novel-titler 产物契约校验（schema/归属/键集合）。返回问题清单（空=合格）。"""
    if payload is None:
        return ["titles.json 缺失或不是合法 JSON object"]
    problems = []
    if payload.get("schema_version") != TITLES_SCHEMA:
        problems.append(f"schema_version 必须是 {TITLES_SCHEMA!r}，"
                        f"收到 {payload.get('schema_version')!r}")
    if payload.get("agent") != "novel-titler":
        problems.append(f"agent 必须是 'novel-titler'，收到 {payload.get('agent')!r}")
    if payload.get("cluster_id") != cid:
        problems.append(f"cluster_id 必须是 {cid!r}，收到 {payload.get('cluster_id')!r}")
    titles = payload.get("titles")
    if not isinstance(titles, dict):
        problems.append("titles 必须是 {章号字符串: 标题} object")
    else:
        want = {str(e["ch"]) for e in pending}
        got = set(titles)
        if want != got:
            extra = sorted(got - want)
            missing = sorted(want - got)
            problems.append(f"titles 键集合必须与 brief 待命名章完全一致（不多不少）："
                            f"多出 {extra} · 缺少 {missing}")
    return problems


def apply_titles(project: Path, cluster: str) -> int:
    """确定性验收 novel-titler 产物 + 章头重写 + blueprint 回填 + receipt。"""
    norm = _normalize(cluster)
    if norm is None:
        print(f"[FATAL] 无效 cluster: {cluster!r}", file=sys.stderr)
        return 3
    cid, key = norm
    paths = _paths(project, key)

    brief = _load_json_or_none(paths["brief"])
    if brief is None or brief.get("schema_version") != BRIEF_SCHEMA \
            or brief.get("cluster_id") != cid:
        print(f"[FATAL] title brief 缺失或契约破损: {paths['brief']}（先跑 --emit-brief）",
              file=sys.stderr)
        return 3

    if brief.get("no_op"):
        _write_receipt(paths["receipt"], cid, accepted={}, no_op=True,
                       reason=brief.get("no_op_reason") or "0 章 no-op")
        print("[gen_chapter_titles] no-op receipt 已落盘（本 cluster 0 章·无章可命名）")
        return 0

    pending = brief.get("chapters") or []
    accepted: dict = dict(brief.get("accepted") or {})

    if not pending:
        # 幂等重跑：全部章已验收完成。
        _write_receipt(paths["receipt"], cid, accepted=accepted, no_op=False,
                       plan_id=brief.get("_last_plan_id"))
        print(f"[gen_chapter_titles] 全部 {len(accepted)} 章此前已验收完成·receipt 已确保落盘")
        return 0

    payload = _load_json_or_none(paths["titles"])
    problems = _validate_titles_payload(payload, cid, pending)
    if problems:
        print(f"[PENDING] novel-titler 产物不可验收: {paths['titles']}", file=sys.stderr)
        for msg in problems:
            print(f"  - {msg}", file=sys.stderr)
        print(f"  → 主代理须 spawn novel-titler（TITLE_BRIEF_PATH={paths['brief']} · "
              f"OUTPUT_PATH={paths['titles']}）重产后重跑 --apply", file=sys.stderr)
        return 2

    titles: dict = payload["titles"]
    history = [str(t) for t in brief.get("history_titles") or []]
    taken = history + [str(rec.get("title", "")) for rec in accepted.values()]

    still_pending: list[dict] = []
    accepted_this_round: dict[int, dict] = {}
    for entry in sorted(pending, key=lambda e: int(e["ch"])):
        ch = int(entry["ch"])
        raw = str(titles[str(ch)])
        title = _strip_title_wrappers(raw)
        reason = _reject_reason(raw)
        if reason is None:
            conflict = _title_conflict(title, taken)
            if conflict is not None:
                reason = f"与已用标题「{conflict}」重复/母题包含（历史严禁重复）"
        if reason is not None:
            rejected_entry = dict(entry)
            rejected_entry["rejected"] = {"last_title": title or raw, "reason": reason}
            still_pending.append(rejected_entry)
            continue
        p, body = read_chapter(project, ch)
        if not body:
            print(f"[FATAL] 章正文缺失: {p}", file=sys.stderr)
            return 3
        body_stripped = strip_existing_title(body)
        p.write_text(f"第{ch:03d}章 {title}\n\n{body_stripped}", encoding='utf-8')
        taken.append(title)
        rec = {"title": title, "tier": entry.get("tier", "normal")}
        accepted_this_round[ch] = rec
        accepted[str(ch)] = rec
        marker = {'normal': '·', 'mid': '★', 'high': '★★★'}.get(rec["tier"], '·')
        print(f"  {marker} ch{ch} [{rec['tier']}]: 「{title}」")

    if accepted_this_round:
        rc = _apply_blueprint_titles(
            project, {ch: rec["title"] for ch, rec in accepted_this_round.items()})
        if rc != 0:
            return rc

    brief["chapters"] = still_pending
    brief["accepted"] = accepted
    brief["_last_plan_id"] = payload.get("plan_id")
    atomic_write_json(paths["brief"], brief)

    if still_pending:
        print(f"[PENDING] {len(still_pending)} 章验收退回（brief 已更新·主代理重 spawn "
              "novel-titler 重命名·必须给出与上次不同的新标题）:", file=sys.stderr)
        for e in still_pending:
            rej = e["rejected"]
            print(f"  ch{e['ch']}: 「{rej['last_title']}」 → {rej['reason']}"
                  f"（确定性兜底候选: 「{e.get('fallback_title', '')}」）", file=sys.stderr)
        return 2

    _write_receipt(paths["receipt"], cid, accepted=accepted, no_op=False,
                   plan_id=payload.get("plan_id"))
    total = len(accepted)
    tier_counts = {'normal': 0, 'mid': 0, 'high': 0}
    for rec in accepted.values():
        if rec.get("tier") in tier_counts:
            tier_counts[rec["tier"]] += 1
    print(f"\n[gen_chapter_titles] 完成 {total} 章验收 + 章头重写 + 同步进度.json + receipt",
          file=sys.stderr)
    if total:
        print(f"  分布: normal={tier_counts['normal']} ({100*tier_counts['normal']//total}%) / "
              f"mid={tier_counts['mid']} ({100*tier_counts['mid']//total}%) / "
              f"high={tier_counts['high']} ({100*tier_counts['high']//total}%)")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--cluster', required=True,
                        help='cluster 标识（6 / 006 / cluster_006 均可·cluster_lookup 归一）')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--emit-brief', action='store_true',
                      help='确定性产 novel-titler 任务合同 title_brief.json')
    mode.add_argument('--apply', action='store_true',
                      help='确定性验收 titles.json + 章头重写 + blueprint 回填 + receipt')
    parser.add_argument('--high-chapters', default='',
                        help='显式标 high 档位的章（5%% 高潮章·如 11,40）·仅 --emit-brief 消费')
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if args.emit_brief:
        sys.exit(emit_brief(project, args.cluster, args.high_chapters))
    sys.exit(apply_titles(project, args.cluster))


if __name__ == '__main__':
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
