#!/usr/bin/env python3
"""split_cluster_changes.py — v24 cluster_changes.json 拆分到 N 个 per-chapter _changes.json

输入：
- 章节/cluster_{key}_draft/cluster_{key}_changes.json
- _数据库/.wal/splitter_cluster_{key}_decisions.json（splitter 切点 + ch_range）

输出：
- 章节/第NNN章/第NNN章_changes.json × N

策略：
- 整 cluster 的 factual / self_eval 段平铺到每章（最简实现 v1）
- v2 可基于切点把 factual.key_events 按章节范围分配（需要 events 标注 ch_anchor）

2026-05-29 复审修复（批次 B5-split-changes · SC-5 owner）：
- [H1] 统一识别两套 splitter WAL schema：chapters_split=int + cluster_start_ch（freestyle
       真实产出）/ chapters_split=[列表]（历史）；fresh fluid cluster 无 range 时用
       chapters_split(int)+cluster_start_ch 重建，避免误判 ok:False；0 切（pending_tail）正常返回。
- [H2] 重叠检测前移到写盘前：与其它已落章 cluster 重叠的章跳过不覆盖；全重叠则中止。
- [H11] 回填强制相邻不相交：本 lo <= 前 hi 时按 前 hi+1 修正，非法区间拒写。
- [L1] atomic_json import 失败 fail-fast（不降级裸 write 破坏一致性）。
- SC-2 exit：advisory=1 / 严重=2 / 纯成功=0。

CLI:
    python split_cluster_changes.py <project> --cluster <key>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402 · normalize_changes 恢复 HYBRID factual

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import atomic_json  # noqa: E402  原子写 事件簇.json 回填
except Exception:  # pragma: no cover
    atomic_json = None
try:
    import cluster_lookup as _cl  # noqa: E402  cluster_id 归一化
except Exception:  # pragma: no cover
    _cl = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _norm_cid(x):
    if _cl is not None:
        n = _cl.normalize_cluster_id(x)
        if n:
            return n
    s = str(x)
    digits = "".join(ch for ch in s if ch.isdigit())
    return f"cluster_{int(digits):03d}" if digits else s


def _is_landed_range(cr) -> bool:
    """SC-6：判断某 cluster 的 chapter_range 是否「已落章」（有效 [int,int]）。

    2026-05-29 复审修复：pending/未涌现 cluster 不会有有效 range；有效 [int,int]
    本身即「已落章」的操作性定义，与中英文 status 词表正交（已完成|done|进行中
    且有 range 都满足此条件）。
    """
    return (
        isinstance(cr, list)
        and len(cr) == 2
        and isinstance(cr[0], int)
        and isinstance(cr[1], int)
        and not isinstance(cr[0], bool)
        and not isinstance(cr[1], bool)
    )


def detect_other_cluster_overlap(project_root: Path, cluster_key: str, chapters: list) -> dict:
    """[H2] 写盘前重叠检测（2026-05-29 复审修复）：本 cluster 拟切章号是否与
    其它已落章 cluster（事件簇.json 中有有效 chapter_range 的）重叠。

    返回 {hard_overlap: bool, owned_by_other: set[int], detail: [...]}
    - owned_by_other：被其它 cluster 占用的章号集合（写盘时应跳过，不覆盖别 cluster 的章）
    - hard_overlap：本 cluster 全部拟切章都被其它 cluster 占用（无章可写 → 调用方应中止）
    """
    out = {"hard_overlap": False, "owned_by_other": set(), "detail": []}
    if not chapters:
        return out
    target = _norm_cid(cluster_key)
    shi = load_json(project_root / "_数据库" / "事件簇.json")
    if not isinstance(shi, dict):
        return out
    chset = set(int(x) for x in chapters)
    for c in shi.get("clusters", []):
        if not isinstance(c, dict):
            continue
        if _norm_cid(c.get("cluster_id")) == target:
            continue  # 自己不算重叠
        cr = c.get("chapter_range")
        if not _is_landed_range(cr):  # SC-6：仅与「已落章」cluster 比对
            continue
        other_chs = set(range(cr[0], cr[1] + 1))
        inter = chset & other_chs
        if inter:
            out["owned_by_other"].update(inter)
            out["detail"].append(
                f"{c.get('cluster_id')} range {cr} 占用本 cluster 拟切章 {sorted(inter)}"
            )
    if out["owned_by_other"] and out["owned_by_other"] >= chset:
        out["hard_overlap"] = True
    return out


def writeback_event_cluster_range(project_root: Path, cluster_key: str, chapters: list) -> dict:
    """v2 权威源回填（2026-05-29）：切章后把 splitter 的真实 [min,max] 写回
    事件簇.json.clusters[N].chapter_range，使 事件簇 成为切章后 chapter_range 的唯一权威。

    2026-05-29 复审修复 [H11]：回填强制相邻不相交。若本 cluster.lo <= 任一前序
    已落章 cluster.hi，按 前.hi + 1 修正本 cluster 的 lo（不写入造成重叠的值）。
    若修正后区间非法（lo > hi）则拒写回（ok:False），不污染权威源。

    2026-05-29 复审修复 [L1]：atomic_json import 失败时不再降级裸 write_text
    （破坏写盘一致性的死分支）。改 fail-fast：拒绝回填并返回 ok:False，由调用方
    上报（split 本体已成功，回填是 consistency-critical，宁可不写也不裸写）。
    """
    if not chapters:
        return {"ok": False, "error": "chapters 为空，跳过回填"}
    lo, hi = int(min(chapters)), int(max(chapters))
    shi_path = project_root / "_数据库" / "事件簇.json"
    shi = load_json(shi_path)
    if not isinstance(shi, dict) or "clusters" not in shi:
        return {"ok": False, "error": "事件簇.json 不存在或无 clusters"}

    target = _norm_cid(cluster_key)
    hit = None
    warnings = []
    # [H11] 防御第二层（非死代码说明 · 2026-05-29 复审清理标注）：
    # 生产路径下 split_changes 传入的是 own_chapters（已被 H2 detect_other_cluster_overlap
    # 剔除他 cluster 占用章），故此处 prev_his 通常为空、相邻修正不触发。保留它是因为
    # writeback_event_cluster_range 是写「权威源 事件簇.json」的 public 函数，可能被独立/未来
    # 调用方传入未经 H2 过滤的 chapters——这是对权威源的廉价兜底防护，刻意保留。
    prev_his = []  # 与本 cluster 有重叠/相邻关系、且 lo <= 其 hi 的前序 cluster hi
    for c in shi.get("clusters", []):
        if not isinstance(c, dict):
            continue
        if _norm_cid(c.get("cluster_id")) == target:
            hit = c
            continue
        # 重叠检测（与其它已落 range 的 cluster）
        cr = c.get("chapter_range")
        if _is_landed_range(cr):
            if not (hi < cr[0] or lo > cr[1]):
                warnings.append(f"{c.get('cluster_id')} range {cr} 与本 cluster [{lo},{hi}] 重叠")
            # [H11] 前序 cluster（其 lo 不大于本 cluster lo）若 hi >= 本 lo → 需相邻修正
            if cr[0] <= lo and cr[1] >= lo:
                prev_his.append(cr[1])
    if hit is None:
        return {"ok": False, "error": f"事件簇.json 未找到 {target}"}

    # [H11] 相邻不相交修正：本 lo 必须 > 所有前序 hi
    if prev_his:
        adjusted_lo = max(prev_his) + 1
        if adjusted_lo != lo:
            warnings.append(
                f"相邻不相交修正：本 cluster lo {lo} → {adjusted_lo}（前序末章 {max(prev_his)} + 1）"
            )
            lo = adjusted_lo
    if lo > hi:
        # 修正后区间非法（本 cluster 全部章号都被前序占用）→ 拒写，避免污染权威源
        return {
            "ok": False,
            "error": f"相邻不相交修正后区间非法 [{lo},{hi}]（本 cluster 章号疑被前序 cluster 占用），拒绝回填",
            "warnings": warnings,
        }

    # [L1] atomic_json 不可用时 fail-fast（不降级裸 write，避免破坏一致性）
    if atomic_json is None:
        return {
            "ok": False,
            "error": "atomic_json 模块不可用，拒绝非原子回填 事件簇.json（fail-fast 避免破坏一致性）",
            "warnings": warnings,
        }

    old = hit.get("chapter_range")
    hit["chapter_range"] = [lo, hi]
    atomic_json.atomic_write_json(shi_path, shi)
    return {"ok": True, "cluster": target, "old": old, "new": [lo, hi], "warnings": warnings}


def split_changes(project_root: Path, cluster_key: str) -> dict:
    cluster_draft_dir = project_root / "章节" / f"cluster_{cluster_key}_draft"
    cluster_changes_path = cluster_draft_dir / f"cluster_{cluster_key}_changes.json"
    splitter_decisions_path = project_root / "_数据库" / ".wal" / f"splitter_cluster_{cluster_key}_decisions.json"

    if not cluster_changes_path.exists():
        return {"ok": False, "error": f"cluster_changes 不存在: {cluster_changes_path}"}
    if not splitter_decisions_path.exists():
        return {"ok": False, "error": f"splitter_decisions 不存在: {splitter_decisions_path}"}

    # 2026-06-02 修：走 normalize_changes 恢复 HYBRID 布局（gen-model 常产 空factual={}+事实散顶层
    # facts_locked/foreshadowing_planted）→ 否则平铺出 per-chapter factual 也空 → 下游 validate 逐章
    # 误报 CHANGES_MISSING（cluster→split→per-chapter 三级级联）。
    cluster_changes = cio.normalize_changes(load_json(cluster_changes_path))
    splitter_decisions = load_json(splitter_decisions_path)

    # 取 chapter_range —— 统一识别两套 WAL schema（2026-05-29 复审修复 [H1]）
    #  schema A (chapter_splitter.run_freestyle 真实产出 / LLM splitter agent)：
    #    chapters_split=int（章数）+ cluster_start_ch=int（起始章）+ chapter_range=[lo,hi]
    #  schema B (历史/列表形态)：chapters_split=[章号列表] 或 chapter_range="lo-hi"
    # v26 修复: splitter 实际输出字段名兼容（ch_range / chapter_range / cluster_range）
    chapter_range = (
        splitter_decisions.get("chapter_range")
        or splitter_decisions.get("cluster_range")
        or splitter_decisions.get("ch_range")
    )
    chapters_split = splitter_decisions.get("chapters_split")
    # 起始章兼容两种字段名：消费侧历史只认 ch_start，producer 实际写 cluster_start_ch
    start_ch_raw = splitter_decisions.get("cluster_start_ch")
    if start_ch_raw is None:
        start_ch_raw = splitter_decisions.get("ch_start")

    chapters = []
    if isinstance(chapters_split, bool):
        # 防 True/False 被当 int（isinstance(True, int) 为真）
        chapters_split = None

    if isinstance(chapters_split, list) and chapters_split:
        # schema B：splitter 直接给的章节号列表（最可靠）
        chapters = [int(x) for x in chapters_split]
    elif isinstance(chapter_range, list) and len(chapter_range) == 2 \
            and isinstance(chapter_range[0], int) and isinstance(chapter_range[1], int) \
            and not isinstance(chapter_range[0], bool) \
            and chapter_range[0] <= chapter_range[1]:
        # schema A：chapter_range=[lo,hi]（freestyle 真实产出）·倒序=损坏→跳过让 fallback 接管
        chapters = list(range(int(chapter_range[0]), int(chapter_range[1]) + 1))
    elif isinstance(chapter_range, str) and "-" in chapter_range:
        start, end = chapter_range.split("-")
        # 🔴 倒序区间=损坏数据·跳过让 fallback 接管（不静默 range(hi,lo+1) 产空列表）
        if int(start) <= int(end):
            chapters = list(range(int(start), int(end) + 1))
    elif isinstance(chapters_split, int) and start_ch_raw is not None:
        # [H1] schema A 但 chapter_range 缺失：用 chapters_split(int) + cluster_start_ch 重建
        # chapters = range(start, start + n)。chapters_split==0 → 整段退 pending_tail，0 切
        n = int(chapters_split)
        start = int(start_ch_raw)
        chapters = list(range(start, start + n)) if n > 0 else []

    # [H1 复修] 显式 0 切（chapters_split==0，整段退 pending_tail / 本轮未切任何章）：
    # 立即返回 ok:True 空，**前置**到 事件簇 fallback + phantom range(start,start+4) 之前——
    # 否则 producer 总写 cluster_start_ch 使后面的 start_ch_raw 守卫失效，凭空造 4 章幽灵并污染权威源。
    _explicit_zero_cut = (
        isinstance(chapters_split, int) and not isinstance(chapters_split, bool)
        and int(chapters_split) == 0
    )
    if _explicit_zero_cut and not chapters:
        return {
            "ok": True,
            "cluster_key": cluster_key,
            "chapter_range": [],
            "written_count": 0,
            "written": [],
            "event_cluster_writeback": {"ok": False, "error": "本 cluster 0 切（pending_tail），无 range 可回填"},
            "_note": "splitter 本轮 0 切（pending_tail 等下 cluster 拼接），跳过拆分（不造幽灵章）",
        }

    if not chapters:
        # fallback: 从 事件簇.json.clusters[N].chapter_range 取（v2 权威源）
        shi_path = project_root / "_数据库" / "事件簇.json"
        shi = load_json(shi_path)
        if isinstance(shi, dict):
            target = _norm_cid(cluster_key)
            for c in shi.get("clusters", []):
                if not isinstance(c, dict):
                    continue
                if _norm_cid(c.get("cluster_id")) != target:  # SC-5：归一比对，不机械拼 f"cluster_{ch}"
                    continue
                cr = c.get("chapter_range") or []
                if isinstance(cr, list) and len(cr) == 2 \
                        and isinstance(cr[0], int) and isinstance(cr[1], int) \
                        and not isinstance(cr[0], bool) \
                        and cr[0] <= cr[1]:
                    chapters = list(range(cr[0], cr[1] + 1))
                break
        if not chapters:
            # 最后兜底（cluster_001 特殊 · 但拒绝盲写 ch1-4 给非 cluster_001）
            # 注：start_ch_raw 已兼容 cluster_start_ch / ch_start 两种字段名
            target_chapters = splitter_decisions.get("target_chapters")
            if start_ch_raw is None:
                # [H1] fresh fluid cluster：chapters_split==0（全 pending_tail）或纯缺字段
                #  → 本 cluster 本轮未切出任何章，正常返回 ok:True 空 written（不视为失败）
                if chapters_split == 0 or splitter_decisions.get("pending_tail", {}).get("exists"):
                    return {
                        "ok": True,
                        "cluster_key": cluster_key,
                        "chapter_range": [],
                        "written_count": 0,
                        "written": [],
                        "event_cluster_writeback": {"ok": False, "error": "本 cluster 0 切（pending_tail），无 range 可回填"},
                        "_note": "splitter 本轮 0 切（pending_tail 等下 cluster 拼接），跳过拆分",
                    }
                return {"ok": False, "error": f"无法确定 cluster_{cluster_key} chapter_range · splitter_decisions 缺 chapter_range/chapters_split/cluster_start_ch · 事件簇.json fallback 失败"}
            _tc = int(target_chapters or 4)
            if not target_chapters:
                print(f"[split_changes] WARN cluster_{cluster_key} 末级兜底：splitter_decisions 缺 "
                      f"target_chapters/chapter_range/chapters_split·按 cluster_start_ch={start_ch_raw} "
                      f"猜测 {_tc} 章（无章数上限源·可能越界·建议核 splitter 产出）", file=sys.stderr)
            chapters = list(range(int(start_ch_raw), int(start_ch_raw) + _tc))

    # [H2] 写盘前重叠检测（前移 · 不再先覆盖后 warn）
    overlap = detect_other_cluster_overlap(project_root, cluster_key, chapters)
    if overlap["hard_overlap"]:
        # 本 cluster 全部拟切章都属于其它 cluster → 中止，避免覆盖别 cluster 的 _changes
        return {
            "ok": False,
            "error": f"cluster_{cluster_key} 拟切章 {chapters} 全部被其它 cluster 占用，中止以防覆盖",
            "overlap_detail": overlap["detail"],
        }
    owned_by_other = overlap["owned_by_other"]
    overlap_skipped = []

    written = []
    for n in chapters:
        if n in owned_by_other:
            # 该章已属其它 cluster → 跳过，不覆盖（H2）
            overlap_skipped.append(n)
            continue
        ch_dir = project_root / "章节" / f"第{n:03d}章"
        ch_changes_path = ch_dir / f"第{n:03d}章_changes.json"
        if not ch_dir.exists():
            continue  # splitter 没切出来这章，跳过

        # v1 策略：平铺整 cluster_changes 到每章
        ch_data = {
            "schema_version": "v18",
            "chapter": n,
            "title": "",  # gen_chapter_titles 写
            "factual": cluster_changes.get("factual", {}),
            "self_eval": cluster_changes.get("self_eval", {}),
            "_doc": f"v24 cluster_{cluster_key} ch{n} _changes (从 cluster_changes.json 平铺 · ECAS 模式 cluster 级单源)",
            "_source_cluster_changes": str(cluster_changes_path)
        }
        ch_changes_path.write_text(json.dumps(ch_data, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(ch_changes_path))

    # v2 权威源回填（2026-05-29）：把切章真实 [min,max] 写回 事件簇.json（唯一权威）
    # [H2] 只用本 cluster 真正拥有的章（剔除被其它 cluster 占用的），避免回填重叠值
    own_chapters = [n for n in chapters if n not in owned_by_other]
    writeback = writeback_event_cluster_range(project_root, cluster_key, own_chapters)

    return {
        "ok": True,
        "cluster_key": cluster_key,
        "chapter_range": own_chapters,
        "written_count": len(written),
        "written": written,
        "overlap_skipped": sorted(overlap_skipped),
        "overlap_detail": overlap["detail"],
        "writeback_ok": (writeback.get("ok") if isinstance(writeback, dict) else None),
        "event_cluster_writeback": writeback,
    }


def main():
    parser = argparse.ArgumentParser(description="v24 cluster_changes.json 拆分到 per-chapter _changes")
    parser.add_argument("project", help="项目路径")
    parser.add_argument("--cluster", required=True, help="cluster key (e.g. 001 or cluster_001)")
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    cluster_key = args.cluster.replace("cluster_", "")

    result = split_changes(project_root, cluster_key)
    if result.get("ok"):
        print(f"[OK] cluster_{cluster_key} _changes 拆分到 {result['written_count']} 章")
        for p in result["written"]:
            print(f"     + {p}")
        # [H2] 重叠跳过的章（advisory）
        advisory = False
        for w in result.get("overlap_detail", []) or []:
            print(f"     ⚠ 重叠跳过: {w}")
            advisory = True
        if result.get("overlap_skipped"):
            print(f"     ⚠ 已跳过被其它 cluster 占用的章: {result['overlap_skipped']}")
            advisory = True
        wb = result.get("event_cluster_writeback") or {}
        if wb.get("ok"):
            print(f"     ↩ 事件簇.json 回填 chapter_range: {wb.get('old')} → {wb.get('new')}（权威源）")
            for w in wb.get("warnings", []):
                print(f"     ⚠ 边界重叠: {w}")
                advisory = True
        elif wb.get("error"):
            print(f"     ⚠ 事件簇 回填跳过: {wb.get('error')}")
            # 🔴 2026-06-17 bug-hunt 修：进到这里说明切章本体已成功（在 result.ok 分支内·
            # written_count 章 _changes.json 已落盘）。事件簇.json 回填是 **consistency-only
            # 可选步骤**，其失败（0切/pending_tail/缺该cluster/事件簇不存在/相邻区间非法/
            # atomic_json不可用）**绝不应让切章成功的 step 返回 exit1 卡死 orchestrator 管线**。
            # 仅警告不升 advisory。（原 _zero_cut 守卫只豁免 0 切·漏了「切章成功+回填失败」
            # 兄弟分支 → 与已修的 0切 exit1 同族 bug。）
            pass
        # SC-2 exit 语义：advisory 级发现统一 exit 1；纯成功 exit 0
        return 1 if advisory else 0
    else:
        # SC-2 exit 语义：严重发现（无法切分 / 硬重叠中止）统一 exit 2
        print(f"[FAIL] {result.get('error')}", file=sys.stderr)
        for w in result.get("overlap_detail", []) or []:
            print(f"     ⚠ {w}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # 🔴 2026-06-17 bug-hunt 修：直接 CLI 调用时 Windows GBK 控制台编码不了 print 里的
    # ⚠(U+26A0)/↩(U+21A9) → UnicodeEncodeError 崩（orchestrator 路径有 PYTHONIOENCODING=utf-8
    # 或 frozen reconfigure 掩盖·直接调用裸崩）。入口强制 UTF-8 输出（对齐 judge_runner __main__）。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
