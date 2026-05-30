"""finalize_book.py — 书末 / 导出前收尾检测（2026-05-30 · 修 pending_tail 数据孤儿 + 书末缺终态）

【为什么有这个脚本】（北极星⑥ 最小聚焦 · 顾问非法官）
v27 splitter 末章 < 3000 CJK 时把末段退回 `章节/cluster_<key>_draft/cluster_<key>_pending_tail.txt`，
设计假设「永远有下一个 cluster 来 prepend 拼接」（消费方仅 cluster-write step6 检测「上一个」cluster）。

两个静默丢失漏洞：
  [#3] 残留 pending_tail 数据孤儿 ——
       · 全书最后一个 cluster 无后继 → 它的 pending_tail 永远没人消费；
       · 或后继 cluster 写作时漏传 PREVIOUS_PENDING_TAIL_PATH（实测「诡异接待处」cluster_004
         有 2435 CJK pending_tail，cluster_005/006 已 done 却没拼）。
       export 只 Glob `第*章*.txt`（剥 CHANGES），扫不到 draft 目录里的 pending_tail → 正文被静默丢弃。
  [#4] 书末缺终态 —— cluster-save-state step11 永远涌现下个 cluster，全周期无「书完结」终态。

【本脚本做什么 · 全程 advisory（不阻断导出、不干涉创作）】
  1. scan-pending：扫所有 cluster_*_draft/*_pending_tail.txt，对**有内容**的判定是否被后继 cluster 消费；
     未消费 = 孤儿，给出文件路径 + CJK 字数。
  2. flush（默认 warn 只告警）：opt-in 把孤儿 pending_tail
       --flush append → prepend 到该 cluster 已切的末章尾部（最保守：内容归位、不新增章）
       --flush split  → 走 splitter run_freestyle 强制切成该 cluster 的新末章
       --flush warn   → 只报告路径 + 字数（默认 · 不动用户内容）
  3. reconcile：对账「各 cluster 草稿总 CJK（计入孤儿 pending_tail）vs 已导出章节总 CJK」，
     差额 > 阈值 → advisory 告警（暴露静默丢失）。

退出码：始终 0（顾问制 · 不强制阻断）。`--strict` 下检出孤儿/超阈差额返回 1（供 CI 选用，导出流程不传）。
"""

import sys
import json
import argparse
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio  # noqa: E402

# 字数对账默认阈值（CJK）：单个孤儿 pending_tail 下限 ~3000，留一点余量给标点/空白口径差异。
DEFAULT_RECONCILE_THRESHOLD = 500
# pending_tail 头部比对长度（判断后继 cluster 是否 prepend 消费了它）
_HEAD_MATCH_LEN = 80


def _cluster_key_int(key: str):
    """'004' / 'cluster_004' → 4；非数字返回 None（按字符串排序兜底）。"""
    m = re.search(r"(\d+)", str(key))
    return int(m.group(1)) if m else None


def _draft_dir(project_root: Path, key: str) -> Path:
    return project_root / "章节" / f"cluster_{key}_draft"


def _read_text_or_empty(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _list_cluster_keys(project_root: Path):
    """返回所有存在 draft 目录的 cluster key（如 '001'），按数字序升序。"""
    base = project_root / "章节"
    if not base.is_dir():
        return []
    keys = []
    for d in base.iterdir():
        m = re.match(r"^cluster_(.+)_draft$", d.name)
        if d.is_dir() and m:
            keys.append(m.group(1))
    keys.sort(key=lambda k: (_cluster_key_int(k) if _cluster_key_int(k) is not None else 1 << 30, k))
    return keys


def _splitter_wal(project_root: Path, key: str) -> dict:
    p = project_root / "_数据库" / ".wal" / f"splitter_cluster_{key}_decisions.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _norm_head(text: str) -> str:
    """取正文头部用于 prepend 消费比对：去空白后前 N 字。"""
    return re.sub(r"\s", "", text)[:_HEAD_MATCH_LEN]


def _next_cluster_consumed(project_root: Path, key: str, all_keys, tail_text: str) -> bool:
    """判断 cluster_<key> 的 pending_tail 是否已被某个后继 cluster prepend 消费。

    证据（任一成立即视为已消费）：
      A) 后继 cluster 的 splitter WAL previous_pending_tail_consumed_cjk > 0
      B) 后继 cluster 的草稿头部 == 本 pending_tail 头部（被 prepend 进草稿）
    无后继 cluster → 必然未消费（孤儿）。
    """
    ki = _cluster_key_int(key)
    later = [k for k in all_keys if _cluster_key_int(k) is not None and ki is not None
             and _cluster_key_int(k) > ki]
    if not later:
        return False  # 无后继 → 孤儿（#3/#4 核心场景）
    tail_head = _norm_head(tail_text)
    for nk in later:
        wal = _splitter_wal(project_root, nk)
        if isinstance(wal.get("previous_pending_tail_consumed_cjk"), (int, float)) \
                and wal["previous_pending_tail_consumed_cjk"] > 0:
            return True
        draft = _read_text_or_empty(_draft_dir(project_root, nk) / f"cluster_{nk}_draft.txt")
        if tail_head and _norm_head(cio._strip_changes(draft)).startswith(tail_head):
            return True
    return False


def scan_pending_tails(project_root: Path) -> list:
    """扫所有 cluster_*_draft/*_pending_tail.txt，返回孤儿列表（有内容且未被后继消费）。"""
    project_root = Path(project_root)
    all_keys = _list_cluster_keys(project_root)
    orphans = []
    for key in all_keys:
        pt = _draft_dir(project_root, key) / f"cluster_{key}_pending_tail.txt"
        if not pt.is_file():
            continue
        text = _read_text_or_empty(pt)
        cjk = cio.count_cjk(text)
        if cjk == 0:
            continue  # 空 / 无正文 pending_tail 不算孤儿
        if _next_cluster_consumed(project_root, key, all_keys, text):
            continue  # 已被后继 cluster 拼接
        orphans.append({
            "cluster_key": key,
            "path": str(pt.relative_to(project_root)),
            "abs_path": str(pt),
            "cjk": cjk,
            "chars": cio.count_words(text),
            "is_last_cluster": _cluster_key_int(key) == max(
                (_cluster_key_int(k) for k in all_keys if _cluster_key_int(k) is not None),
                default=_cluster_key_int(key)),
        })
    return orphans


# ============ flush 动作 ============

def _cluster_last_chapter(project_root: Path, key: str):
    """返回本 cluster 已切出的最后一章号。

    依次尝试（任一命中即返回）：
      1) splitter WAL chapter_range[1]
      2) splitter WAL cluster_start_ch + chapters_split - 1
      3) 进度.json.chapter_plan 中 cluster == cluster_<key> 的最大 ch（兼容旧/缺 WAL 真实项目）
    都失败返回 None。
    """
    wal = _splitter_wal(project_root, key)
    rng = wal.get("chapter_range")
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng):
        return rng[1]
    start = wal.get("cluster_start_ch")
    n = wal.get("chapters_split")
    if isinstance(start, int) and isinstance(n, int) and n > 0:
        return start + n - 1
    # 3) 进度.json.chapter_plan 反查（WAL 旧 schema / 缺失时兜底）
    return _last_chapter_from_progress(project_root, key)


def _last_chapter_from_progress(project_root: Path, key: str):
    """从 进度.json.chapter_plan 取 cluster_<key> 的最大章号（且该章正文文件真实存在）。"""
    p = Path(project_root) / "_数据库" / "进度.json"
    if not p.is_file():
        return None
    try:
        plan = json.loads(p.read_text(encoding="utf-8")).get("chapter_plan") or []
    except Exception:
        return None
    target = f"cluster_{key}"
    chs = [c.get("ch") for c in plan
           if isinstance(c, dict) and str(c.get("cluster", "")).strip() == target
           and isinstance(c.get("ch"), int)]
    if not chs:
        return None
    last = max(chs)
    # 必须确认该章正文物理存在（chapter_plan 可能含未切出的镜像占位）
    return last if cio.find_body_file(project_root, last) else None


def flush_orphan(project_root: Path, orphan: dict, mode: str) -> dict:
    """对单个孤儿执行 flush。mode ∈ {warn, append, split}。返回动作结果 dict。"""
    project_root = Path(project_root)
    key = orphan["cluster_key"]
    pt_path = Path(orphan["abs_path"])
    tail_text = _read_text_or_empty(pt_path).rstrip()

    if mode == "warn":
        return {"cluster_key": key, "action": "warn", "applied": False,
                "message": f"孤儿 pending_tail 未拼接：{orphan['path']}（{orphan['cjk']} CJK）— "
                           f"需人工决定拼接归属，或用 --flush append/split 处理"}

    if mode == "append":
        last_ch = _cluster_last_chapter(project_root, key)
        if last_ch is None:
            return {"cluster_key": key, "action": "append", "applied": False,
                    "message": f"无法定位 cluster_{key} 已切末章（splitter WAL 缺 chapter_range）· 降级 warn · "
                               f"{orphan['path']}（{orphan['cjk']} CJK）"}
        try:
            body = cio.read_body(project_root, last_ch)
        except FileNotFoundError:
            return {"cluster_key": key, "action": "append", "applied": False,
                    "message": f"末章 第{last_ch}章 正文文件不存在 · 降级 warn · {orphan['path']}"}
        merged = body.rstrip() + "\n\n" + tail_text
        cio.write_body(project_root, last_ch, merged)
        pt_path.unlink(missing_ok=True)
        return {"cluster_key": key, "action": "append", "applied": True, "appended_to_chapter": last_ch,
                "message": f"已把孤儿 pending_tail（{orphan['cjk']} CJK）prepend 到 cluster_{key} 末章 "
                           f"第{last_ch}章 尾部 · 已删除 pending_tail 文件"}

    if mode == "split":
        # 把 pending_tail 强切成一个新章。新章号优先接在本 cluster 末章之后；
        # 本 cluster 末章不可知时（旧/缺 WAL + 空 chapter_plan）退而接在**全书最后一章**之后——
        # split 只新增章不覆盖既有内容，故全局末章兜底是安全的（不会写错已有章）。
        last_ch = _cluster_last_chapter(project_root, key)
        if last_ch is None:
            last_ch = _global_last_chapter(project_root)
        if last_ch is None:
            return {"cluster_key": key, "action": "split", "applied": False,
                    "message": f"无法定位任何已切章号 · 降级 warn · {orphan['path']}"}
        new_start = last_ch + 1
        rhythm = _read_rhythm_profile(project_root)
        # 强切：哪怕 < 单章下限也要落地（用 cio.write_body 直写单章，run_freestyle 在 < lo 时会再退 pending_tail，
        # 故此处不复用 run_freestyle 的 N==0 退路，而是直接写一章——书末/孤儿没有下一 cluster 可拼）。
        cio.write_body(project_root, new_start, tail_text)
        pt_path.unlink(missing_ok=True)
        return {"cluster_key": key, "action": "split", "applied": True, "new_chapter": new_start,
                "rhythm": rhythm,
                "message": f"已把孤儿 pending_tail（{orphan['cjk']} CJK）强制切成 cluster_{key} 新末章 "
                           f"第{new_start}章 · 已删除 pending_tail 文件"}

    return {"cluster_key": key, "action": mode, "applied": False, "message": f"未知 flush 模式: {mode}"}


def _global_last_chapter(project_root: Path):
    """全书已切出的最大章号（扫物理 第NNN章.txt），无章返回 None。"""
    project_root = Path(project_root)
    base = project_root / "章节"
    roots = [r for r in (base, project_root) if r.is_dir()]
    last = None
    for root in roots:
        for f in root.rglob("第*章.txt"):
            if "_archive" in f.parts or "_tmp" in f.parts or "_draft" in f.parent.name:
                continue
            m = re.match(r"^第(\d+)章\.txt$", f.name)
            if m:
                ch = int(m.group(1))
                last = ch if last is None else max(last, ch)
    return last


def _read_rhythm_profile(project_root: Path) -> str:
    try:
        p = Path(project_root) / "_数据库" / "进度.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("rhythm_profile") or "标准"
    except Exception:
        pass
    return "标准"


# ============ 字数对账 ============

def _exported_chapters_cjk(project_root: Path):
    """遍历 章节/第NNN章/第NNN章.txt（兼容平铺），返回 (总 CJK, 章数)。"""
    project_root = Path(project_root)
    total = 0
    count = 0
    seen = set()
    base = project_root / "章节"
    roots = [base] if base.is_dir() else []
    roots.append(project_root)  # 平铺旧布局兜底
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("第*章.txt"):
            if "_archive" in f.parts or "_tmp" in f.parts:
                continue
            if f.name.endswith("_changes.json") or "_draft" in f.parent.name:
                continue
            m = re.match(r"^第(\d+)章\.txt$", f.name)
            if not m:
                continue
            ch = int(m.group(1))
            if ch in seen:
                continue
            seen.add(ch)
            total += cio.count_cjk(cio._strip_changes(_read_text_or_empty(f)))
            count += 1
    return total, count


def _draft_total_cjk(project_root: Path):
    """各 cluster 草稿 cluster_<key>_draft.txt 总 CJK + 孤儿 pending_tail 总 CJK。

    注意：splitter 跨 cluster 补料时上 cluster pending_tail 会被 prepend 进下 cluster 草稿，
    导致 draft 之间内容重叠（双算）。对账只能给「上界 vs 已导出」的粗对比 + 孤儿专项核对，
    故 reconcile 主信号是「孤儿 CJK 是否被吞」，draft_total 仅作辅助参考（标 advisory_only）。
    """
    project_root = Path(project_root)
    draft_total = 0
    orphan_total = 0
    for key in _list_cluster_keys(project_root):
        d = _draft_dir(project_root, key)
        draft = d / f"cluster_{key}_draft.txt"
        if draft.is_file():
            draft_total += cio.count_cjk(cio._strip_changes(_read_text_or_empty(draft)))
    for o in scan_pending_tails(project_root):
        orphan_total += o["cjk"]
    return draft_total, orphan_total


def reconcile(project_root: Path, threshold: int = DEFAULT_RECONCILE_THRESHOLD) -> dict:
    """字数对账。核心信号：孤儿 pending_tail CJK 是导出会静默丢失的下界。"""
    project_root = Path(project_root)
    exported_cjk, exported_chapters = _exported_chapters_cjk(project_root)
    draft_total, orphan_total = _draft_total_cjk(project_root)
    return {
        "exported_chapters": exported_chapters,
        "exported_cjk": exported_cjk,
        "draft_total_cjk": draft_total,
        "orphan_pending_tail_cjk": orphan_total,
        "threshold": threshold,
        # 孤儿字数 = 导出一定会丢的下界（最硬的静默丢失证据）
        "silent_loss_lower_bound_cjk": orphan_total,
        "over_threshold": orphan_total > threshold,
        "_note": ("draft_total 因跨 cluster prepend 补料存在内容重叠（双算），不可直接等式对账；"
                  "reconcile 主信号是 orphan_pending_tail_cjk —— 它就是导出会静默丢失的下界。"),
    }


# ============ 报告 ============

def build_report(project_root: Path, flush_mode: str, threshold: int) -> dict:
    project_root = Path(project_root)
    orphans = scan_pending_tails(project_root)
    flush_results = []
    if flush_mode != "warn":
        for o in orphans:
            flush_results.append(flush_orphan(project_root, o, flush_mode))
        # flush 后重扫，确认孤儿是否已处理（warn 模式仍留作待办）
        orphans_after = scan_pending_tails(project_root)
    else:
        for o in orphans:
            flush_results.append(flush_orphan(project_root, o, "warn"))
        orphans_after = orphans
    rec = reconcile(project_root, threshold)
    return {
        "scanner": "finalize_book",
        "schema_version": "1.0",
        "gate_level": "advisory",  # 北极星⑤：顾问非法官，不阻断导出
        "project": str(project_root),
        "flush_mode": flush_mode,
        "orphan_pending_tails": orphans,
        "orphan_count": len(orphans),
        "flush_results": flush_results,
        "orphans_remaining_after_flush": orphans_after if flush_mode != "warn" else orphans,
        "reconcile": rec,
        "has_findings": bool(orphans) or rec["over_threshold"],
    }


def _print_human(report: dict):
    out = sys.stdout
    print("══ 书末收尾检测（finalize_book · advisory）══", file=out)
    oc = report["orphan_count"]
    if oc == 0:
        print("  [OK] 无残留 pending_tail 孤儿", file=out)
    else:
        print(f"  [⚠ advisory] 检出 {oc} 个未拼接的 pending_tail 孤儿（导出会静默丢失正文）：", file=out)
        for o in report["orphan_pending_tails"]:
            last = "（全书最后 cluster · 无后继可拼）" if o["is_last_cluster"] else "（后继 cluster 漏拼）"
            print(f"    · cluster_{o['cluster_key']}  {o['cjk']} CJK  {o['path']}  {last}", file=out)
    for fr in report["flush_results"]:
        tag = "[已处理]" if fr.get("applied") else "[待办]"
        print(f"  {tag} {fr['message']}", file=out)
    rec = report["reconcile"]
    print(f"  对账：已导出 {rec['exported_chapters']} 章 / {rec['exported_cjk']} CJK · "
          f"孤儿待拼 {rec['orphan_pending_tail_cjk']} CJK", file=out)
    if rec["over_threshold"]:
        print(f"  [⚠ advisory] 静默丢失下界 {rec['silent_loss_lower_bound_cjk']} CJK > 阈值 {rec['threshold']} CJK "
              f"— 建议导出前用 --flush append/split 处理", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="书末/导出前 pending_tail 孤儿检测 + 字数对账（advisory）")
    ap.add_argument("project", help="项目路径（workspace/novels/<书名>）")
    ap.add_argument("--flush", choices=["warn", "append", "split"], default="warn",
                    help="孤儿处理：warn 只告警(默认) / append prepend 到本 cluster 末章尾 / split 强切新末章")
    ap.add_argument("--threshold", type=int, default=DEFAULT_RECONCILE_THRESHOLD,
                    help=f"对账告警阈值 CJK（默认 {DEFAULT_RECONCILE_THRESHOLD}）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器消费）")
    ap.add_argument("--strict", action="store_true", help="检出孤儿/超阈 → exit 1（CI 用 · 导出流程不传）")
    args = ap.parse_args(argv)

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {args.project}", file=sys.stderr)
        sys.exit(2)

    report = build_report(project_root, args.flush, args.threshold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if args.strict and report["has_findings"]:
        sys.exit(1)
    sys.exit(0)  # 顾问制：默认始终 0，不阻断导出


if __name__ == "__main__":
    main()
