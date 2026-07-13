#!/usr/bin/env python3
"""gen_creative_volume_arc.py — volume_arc 卷级大纲单元验收 + 确定性合并落库（零 LLM）

创作由 novel-outline-planner MODE=volume_arc_unit 亲笔逐单元完成（scene_jobs 范式·
对齐 skill_opt/scene_jobs.py），本模块负责确定性调度与验收：
  阶段A 骨架单元（story_destiny/volumes/cluster_001/world_seed）→ .wal/volume_arc_skeleton.json
  阶段B 逐卷 ME 池单元（schema 合法的部分产物）→ .wal/volume_arc_v<N>.json
  阶段C 全部单元合法后确定性合并（ME id 跨卷重复=硬报错不静默覆盖）→ 一把梭等价结构 → emit

单元 WAL 缺失或校验不合法 → 写任务清单 .wal/volume_arc_jobs.json（unit / 输入材料路径 /
期望产物路径 / 输入 digest / 破损诊断）并 exit 2=pending；主代理按清单逐单元 spawn agent
亲笔写单元 JSON 后重跑本模块续跑验收。断点续跑：已存在且校验合法的单元 WAL 直接复用
（幂等）；破损单元退回 pending 重写。

公开 CLI 入口：`python core/scripts/gen_creative.py --mode volume_arc ...`
（gen_creative.main 的 volume_arc 分支调本模块 _run_volume_arc）。
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import atomic_json  # noqa: E402
from reference_pattern_extract import build_reference_patterns_block  # noqa: E402  参考语料结构基线（advisory·写入 jobs 清单）


# ═══════ MODE: volume_arc（卷级大纲·agent 亲笔单元 + WAL 断点续跑验收）═══════
VOLUME_ARC_SKELETON_WAL = "volume_arc_skeleton.json"
VOLUME_ARC_JOBS_WAL = "volume_arc_jobs.json"
VOLUME_ARC_AGENT = "novel-outline-planner"
VOLUME_ARC_AGENT_MODE = "volume_arc_unit"


def volume_arc_wal_name(vol_no: int) -> str:
    """单卷 ME 池 chunk 的 WAL 文件名（_数据库/.wal/ 下）。"""
    return f"volume_arc_v{vol_no}.json"


# ═══════ 任务清单层（scene_jobs 范式·主代理按清单 spawn agent 补件）═══════
def _input_digest(paths: list[Path | None]) -> str:
    """输入材料 digest（sha256·跳过缺失文件·供任务清单溯源，不做验收门——单元验收
    以 _normalize_skeleton / _normalize_volume_chunk 为唯一裁决）。"""
    h = hashlib.sha256()
    for p in paths:
        if p is not None and p.is_file():
            h.update(p.name.encode("utf-8"))
            try:
                h.update(p.read_bytes())
            except OSError:
                continue
    return h.hexdigest()


def _resolve_author_inputs(project_root: Path, style_ref: str | None) -> tuple[str | None, str | None]:
    """作者档输入路径解析（agent 读它只学笔法·两布局：novels 项目档在 _数据库/·
    styles 风格库档在项目根）。返回 (作者风格.json 路径|None, 风格 skill 路径|None)。"""
    profile = None
    for cand in (project_root / "_数据库" / "作者风格.json", project_root / "作者风格.json"):
        if cand.exists():
            profile = str(cand.resolve())
            break
    skill = None
    if style_ref and Path(style_ref).exists():
        skill = str(Path(style_ref).resolve())
    return profile, skill


def _write_jobs_manifest(path: Path, *, project_root: Path, params: dict,
                         inputs: dict, jobs: list[dict],
                         reference_patterns_block: str) -> None:
    """原子写任务清单（按 unit 保留 created_at·状态 ready/pending 每跑刷新）。"""
    prev: dict[str, dict] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                prev = {j.get("unit"): j for j in old.get("jobs", []) if isinstance(j, dict)}
        except (OSError, json.JSONDecodeError):
            prev = {}
    now = datetime.now().isoformat(timespec="seconds")
    for job in jobs:
        job["created_at"] = (prev.get(job["unit"]) or {}).get("created_at", now)
        job["updated_at"] = now
    doc = {
        "version": 1,
        "contract": "volume_arc_jobs.v1",
        "agent": VOLUME_ARC_AGENT,
        "agent_mode": VOLUME_ARC_AGENT_MODE,
        "project": str(project_root.resolve()),
        "params": params,
        "inputs": inputs,
        "reference_patterns_block": reference_patterns_block,
        "jobs": jobs,
        "updated_at": now,
    }
    atomic_json.atomic_write_json(path, doc)


# ============ ME 池结构与引用验证 ============
def _normalize_me_pool(mes: list, project_root: Path | None = None) -> dict:
    """验证每个 ME 的稳定 id、卷号、prerequisite 和唯一卷末标记。"""
    if not isinstance(mes, list):
        raise ValueError("major_events 必须是 array")
    if not mes:
        raise ValueError("major_events 不能为空")
    ids = []
    by_volume: dict[int, list[dict]] = {}
    for index, event in enumerate(mes):
        if not isinstance(event, dict):
            raise ValueError(f"major_events[{index}] 必须是 object")
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError(f"major_events[{index}].id 不能为空")
        if event_id in ids:
            raise ValueError(f"major_events id 重复: {event_id}")
        ids.append(event_id)
        volume = event.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 1:
            raise ValueError(f"ME {event_id}.volume 必须是正整数")
        if not isinstance(event.get("is_volume_finale"), bool):
            raise ValueError(f"ME {event_id}.is_volume_finale 必须是 bool")
        prerequisites = event.get("prerequisites", [])
        if not isinstance(prerequisites, list) or any(not isinstance(p, str) for p in prerequisites):
            raise ValueError(f"ME {event_id}.prerequisites 必须是 string array")
        by_volume.setdefault(volume, []).append(event)
    id_set = set(ids)
    for event in mes:
        dangling = [p for p in event.get("prerequisites", []) if p not in id_set]
        if dangling:
            raise ValueError(f"ME {event['id']} 含悬空 prerequisites: {dangling}")
    for volume, events in sorted(by_volume.items()):
        finales = [event["id"] for event in events if event["is_volume_finale"]]
        if len(finales) != 1:
            raise ValueError(f"卷{volume} 必须恰有一个 is_volume_finale=true，实际 {finales}")
    return {"validated_events": len(mes), "volumes": sorted(by_volume)}


# ============ volume_arc emit（阶段2 创建书籍·确定性平铺落库）============
def _emit_world_seed_projection(db: Path, world_seed: dict) -> list[str]:
    """🔴 把 agent volume_arc 单元产出的 world_seed 创意投影确定性 reshape 落盘。

    模型自由产内容（北极星⑤·脚本只 reshape 不规训 schema 枚举），_emit 把它平铺进：
      · 世界状态.json   → protagonist_state(主角 arc 基线) + factions_state(1-3 核心阵营 power/stability/wealth)
      · 涟漪规则.json   → ripple_rules(2-5 条 seed·模型产的因果)
      · 人物卡.json     → characters(主角 1 张)
      · 关系.json       → relationships(cluster_001 可见)

    幂等：只在目标当前为空/骨架时写（不覆盖已有内容）。world_seed_init(step5.5) 补本投影没覆盖的。
    返回写入的文件名列表（供日志）。
    """
    written: list[str] = []
    if not isinstance(world_seed, dict) or not world_seed:
        return written

    def _load(name: str) -> dict:
        p = db / name
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save(name: str, doc: dict):
        (db / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        if name not in written:
            written.append(name)

    # 1) 世界状态：protagonist_state + factions_state（仅当当前为空）
    prot = world_seed.get("protagonist_state") or world_seed.get("protagonist")
    factions = world_seed.get("factions_state") or world_seed.get("factions")
    if prot or factions:
        ws = _load("世界状态.json")
        ws.setdefault("current_world_time", {"ch": 0, "cluster": "cluster_001", "day": 1})
        if isinstance(prot, dict) and not ws.get("protagonist_state"):
            ws["protagonist_state"] = {**prot, "_seeded_by": "volume_arc_emit"}
        if isinstance(factions, dict) and not ws.get("factions_state"):
            fs = {}
            for k, v in factions.items():
                if isinstance(v, dict):
                    fs[k] = {"power": 50, "stability": 50, "wealth": 50, **v,
                             "_seeded_by": "volume_arc_emit"}
            if fs:
                ws["factions_state"] = fs
        # consequence_tracker 必须是 dict（engine setdefault+字符串键·list 会 TypeError）
        if not isinstance(ws.get("consequence_tracker"), dict):
            ws["consequence_tracker"] = {}
        ws.setdefault("active_npc_threads", [])
        ws.setdefault("emergent_opportunities", [])
        _save("世界状态.json", ws)

    # 2) 涟漪规则：seed ripple_rules（仅当当前为空）
    seed_rules = world_seed.get("ripple_rules") or world_seed.get("seed_rules")
    if isinstance(seed_rules, list) and seed_rules:
        rr = _load("涟漪规则.json")
        if not rr.get("ripple_rules"):
            rr["ripple_rules"] = [r for r in seed_rules if isinstance(r, dict)]
            _save("涟漪规则.json", rr)

    # 3) 人物卡：主角 1 张（仅当当前为空）
    chars = world_seed.get("characters")
    if isinstance(chars, list) and chars:
        cc = _load("人物卡.json")
        if not cc.get("characters"):
            cc["characters"] = [c for c in chars if isinstance(c, dict)]
            _save("人物卡.json", cc)

    # 4) 关系：cluster_001 可见（仅当当前为空）
    rels = world_seed.get("relationships")
    if isinstance(rels, list) and rels:
        rj = _load("关系.json")
        if not rj.get("relationships"):
            rj["relationships"] = [r for r in rels if isinstance(r, dict)]
            _save("关系.json", rj)

    return written


def _emit_volume_arc_to_db(project_root: Path, data: dict, *,
                           rhythm: str = "", framework: str = "") -> tuple[Path, Path]:
    """把模型产出拆成 大势卡.json + 事件簇.json 原子落盘（确定性平铺·不靠模型写 schema 形状）。

    rhythm/framework：用户 pause 答案（CLI 透传）——确定性写 _metadata + 用户偏好.json；
    cluster-write step6 data_flow 的 <rhythm> 读 用户偏好.json.rhythm_profile，缺 producer
    写入会导致用户选择被静默丢弃、splitter 收不到值退回默认「标准」。"""
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    # producer 补齐（同名字段有多个 consumer 源，须**全**覆盖）：
    #   用户偏好.json    → splitter（cluster-write step6 data_flow）
    #   叙事节拍器.json  → narrator_calibrate → writer manifest storyteller_directive
    #   进度.json        → finalize_book._read_rhythm_profile
    if rhythm:
        for fname, extra in (("用户偏好.json", {"narrative_framework": framework}),
                             ("叙事节拍器.json", {}),
                             ("进度.json", {})):
            fpath = db / fname
            doc = {}
            if fpath.exists():
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    doc = {}
            if not isinstance(doc, dict):
                continue
            doc["rhythm_profile"] = rhythm
            for k, v in extra.items():
                if v:
                    doc[k] = v
            fpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        # _metadata 确定性覆盖（不靠 LLM 自觉回写）
        md = data.setdefault("_metadata", {})
        if isinstance(md, dict):
            md["rhythm_profile"] = rhythm
            if framework:
                md["narrative_framework"] = framework
    # 大势卡.json：story_destiny + _metadata + volumes + major_events（+ 静态 schema 锚点）
    major = {
        "_schema": "major_events_v21_phase", "schema_version": "v27",
        "_doc": "卷=阶段触发点·ME池=本卷小走向候选(每ME=1cluster)·每ME标volume:N·卷末ME标"
                "is_volume_finale·卷长fluid不锁章。",
        "_metadata": data.get("_metadata", {}),
        "story_destiny": data.get("story_destiny", {}),
        "volumes": data.get("volumes", []),
        "major_events": [{**me, "status": me.get("status", "pending")}
                         for me in data.get("major_events", []) if isinstance(me, dict)],
    }
    major["_validation"] = _normalize_me_pool(major["major_events"], project_root)
    # 事件簇.json：只详化 clusters[0]=cluster_001（其余留涌现）
    c1 = data.get("cluster_001") or {}
    research_ref = c1.get("research_ref") if isinstance(c1, dict) else None
    if not isinstance(research_ref, dict):
        research_ref = {
            "cache_path": "_数据库/.research_cache",
            "anchors_used": [],
            "research_topics": [],
            "researcher_confidence": 0,
            "_note": "cluster_001 未返回具体调研 cache；保留调研引用骨架，后续 outline/novel-researcher 可覆盖。",
        }
    cluster = {
        "_schema": "event_clusters", "schema_version": "v2.cluster",
        "_doc": "只详化 cluster_001(黄金三章倒叙)·后续 cluster 留 cluster_emergence_engine 涌现。",
        "clusters": [{
            "cluster_id": "cluster_001",
            "status": "pending",   # step6 cluster_choice_apply 改 in_progress（注入-gate 白名单）
            "narrative_mode": c1.get("narrative_mode", "in_medias_res"),
            "scope_summary": c1.get("scope_summary", ""),
            "scene_storyboard": c1.get("scene_storyboard", []),
            "foreshadowing_to_plant": c1.get("foreshadowing_to_plant", []),
            "parent_me": c1.get("parent_me", "ME-V1-01"),
            "research_ref": research_ref,
        }],
    }
    p_major = db / "大势卡.json"
    p_cluster = db / "事件簇.json"
    p_major.write_text(json.dumps(major, ensure_ascii=False, indent=2), encoding="utf-8")
    p_cluster.write_text(json.dumps(cluster, ensure_ascii=False, indent=2), encoding="utf-8")
    # 🔴 模型 world_seed 创意投影 → 世界状态/涟漪规则/人物卡/关系（仅当为空·幂等）
    seeded = _emit_world_seed_projection(db, data.get("world_seed") or {})
    if seeded:
        print(f"[_emit_volume_arc][world_seed] 投影落盘: {', '.join(seeded)}", file=sys.stderr)
    return p_major, p_cluster


# ── volume_arc 单元 WAL 断点续跑：确定性结构层（normalize / 合并·单元验收唯一裁决）──
def _read_json_or_none(p: Path):
    """读 JSON 文件；缺/坏（含编码破损）→ None（损坏 WAL 判定入口）。"""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _normalize_skeleton(cand) -> tuple:
    """骨架单元 WAL → 校验归一（返回 (dict|None, diag)·None=结构破损·job 退回 pending 重写）。

    确定性结构修补（非创作干涉）：卷缺 vol 号 → 按位置回填；骨架里混产的
    major_events 一律丢弃（ME 池单一来源 = 卷 chunk WAL）。破损判定：缺顶层键 /
    volumes 空 / 卷号无法归一为唯一正整数。
    """
    if not isinstance(cand, dict) or cand.get("_parse_failed"):
        return None, "parse_failed/非dict"
    missing = [k for k in ("story_destiny", "volumes", "cluster_001") if k not in cand]
    if missing:
        return None, f"missing={missing}"
    if not isinstance(cand.get("story_destiny"), dict) or not isinstance(cand.get("cluster_001"), dict):
        return None, "story_destiny/cluster_001 非对象"
    raw_vols = cand.get("volumes")
    if not isinstance(raw_vols, list):
        return None, "volumes 非列表"
    vols = [v for v in raw_vols if isinstance(v, dict)]
    if not vols:
        return None, "volumes 空/无合法卷对象"
    for i, v in enumerate(vols):
        try:
            n = int(v.get("vol"))
            if n < 1:
                raise ValueError
        except (TypeError, ValueError):
            v["vol"] = i + 1     # 结构回填（按位置·非创作干涉）
        else:
            v["vol"] = n
    nums = [v["vol"] for v in vols]
    if len(set(nums)) != len(nums):
        return None, f"卷号重复: {nums}"
    cand["volumes"] = vols
    cand.pop("major_events", None)   # ME 池分卷生成·骨架混产的丢弃（单一来源=chunk WAL）
    return cand, ""


def _normalize_volume_chunk(cand, vol_no: int, known_ids: set | None = None) -> tuple:
    """单卷 chunk 输出/WAL → {"volume": N, "major_events": [...]}（(dict|None, diag)）。

    确定性结构修补：ME 缺 volume → 回填本卷号；裸串/非 dict 元素过滤（与 emit 同款守卫）。
    破损判定（→ job 退回 pending 重写，规则与 _normalize_me_pool 一致·同一套口径）：无合法 ME / ME
    缺 id（id 是合并去重锚）/ 同 chunk 内 id 重复 / ME 显式 volume ≠ 本卷（卷号错位）/
    is_volume_finale 非 bool / 本卷不是恰一个 is_volume_finale=true / prerequisites 非
    string array / prerequisites 引用本卷 + known_ids 之外的未知 id（悬空引用）。
    known_ids：已完成前卷（chunks[1..vol_no-1]）的 ME id 全集——prerequisites 允许回溯引用
    已生成的前卷 ME（跨卷衔接是合法用法），但不能引用任何尚不存在的 id。
    """
    if isinstance(cand, list):
        mes = cand
    elif isinstance(cand, dict):
        if cand.get("_parse_failed"):
            return None, "parse_failed"
        mes = cand.get("major_events")
    else:
        return None, "非 dict/list"
    if not isinstance(mes, list):
        return None, "缺 major_events 列表"
    out = [m for m in mes if isinstance(m, dict)]
    if not out:
        return None, "major_events 无合法 ME"
    seen: set[str] = set()
    for m in out:
        mid = str(m.get("id") or "").strip()
        if not mid:
            return None, "ME 缺 id（合并去重锚·必须有）"
        if mid in seen:
            return None, f"chunk 内 ME id 重复: {mid}"
        seen.add(mid)
        mv = m.get("volume")
        if mv in (None, "", 0):
            m["volume"] = vol_no
        else:
            try:
                mv_i = int(mv)
            except (TypeError, ValueError):
                return None, f"ME {mid} volume 非整数: {mv!r}"
            if mv_i != vol_no:
                return None, f"ME {mid} 卷号错位: volume={mv_i} ≠ 本卷 {vol_no}"
            m["volume"] = mv_i
        if not isinstance(m.get("is_volume_finale"), bool):
            return None, f"ME {mid}.is_volume_finale 必须是 bool"
        prerequisites = m.get("prerequisites", [])
        if not isinstance(prerequisites, list) or any(not isinstance(p, str) for p in prerequisites):
            return None, f"ME {mid}.prerequisites 必须是 string array"
    finales = [str(m.get("id") or "").strip() for m in out if m["is_volume_finale"]]
    if len(finales) != 1:
        return None, f"第 {vol_no} 卷必须恰有一个 is_volume_finale=true，实际 {finales}"
    all_known = seen | set(known_ids or ())
    for m in out:
        dangling = [p for p in m.get("prerequisites", []) if p not in all_known]
        if dangling:
            mid = str(m.get("id") or "").strip()
            return None, f"ME {mid} 含悬空 prerequisites: {dangling}"
    return {"volume": vol_no, "major_events": out}, ""


def _merge_volume_arc_chunks(skeleton: dict, chunks: dict) -> tuple:
    """骨架 + 全部卷 chunk → 一把梭等价结构（确定性合并·卷号升序）。

    返回 (data, dup_report)。dup_report 非空 = ME id 跨卷重复——**硬报错**（调用方 block
    非零退出·绝不静默覆盖）。
    """
    data = {k: v for k, v in skeleton.items() if k not in ("major_events", "_wal_meta")}
    merged, first_vol, dups = [], {}, []
    for n in sorted(chunks):
        for me in chunks[n]["major_events"]:
            mid = str(me.get("id"))
            if mid in first_vol:
                dups.append({"id": mid, "first_vol": first_vol[mid], "dup_vol": n})
                continue
            first_vol[mid] = n
            merged.append(me)
    data["major_events"] = merged
    return data, dups


def _known_me_ids(chunks: dict) -> set[str]:
    """已完成前卷（chunks 累积）的全部 ME id 集合（跨卷 prerequisites 回溯引用合法性判据）。"""
    return {str(me.get("id")) for c in chunks.values() for me in c["major_events"]}


def _run_volume_arc(args) -> int:
    """卷级大纲单元验收 + 确定性合并落库（agent 亲笔单元·scene_jobs 范式）。

    阶段A 骨架单元 → .wal/volume_arc_skeleton.json；阶段B 逐卷 ME 池单元 →
    .wal/volume_arc_v<N>.json（合法 WAL 直接复用·破损退回 pending）；单元缺失/破损 →
    写 .wal/volume_arc_jobs.json 并 exit 2=pending（主代理逐单元 spawn
    novel-outline-planner MODE=volume_arc_unit 补件后重跑续跑）；阶段C 全部单元合法后
    确定性合并（ME id 跨卷重复=硬报错）→ emit 大势卡.json + 事件簇.json
    （最终产物与一把梭结构等价）。exit：0=完成 / 1=硬错误 / 2=pending。
    """
    project_root = Path(args.project) if args.project else None
    if not project_root:
        print("[FATAL] --mode volume_arc 需要 --project", file=sys.stderr)
        return 1

    cluster_count = args.cluster_count or 10
    framework = args.framework or "自定义"
    rhythm = args.rhythm or "标准"
    author_profile, style_skill = _resolve_author_inputs(project_root, args.style_ref)
    research = (str(Path(args.research).resolve())
                if args.research and Path(args.research).exists() else None)
    card_path = (Path(args.selected_card)
                 if args.selected_card and Path(args.selected_card).exists() else None)
    # 参考语料结构基线（artifact 存在才产文本·advisory 可偏离·纯数字无原文·写入任务清单）
    reference_patterns_block = build_reference_patterns_block(project_root)

    wal_dir = project_root / "_数据库" / ".wal"
    wal_dir.mkdir(parents=True, exist_ok=True)
    sk_path = wal_dir / VOLUME_ARC_SKELETON_WAL

    shared_inputs = {
        "selected_card": str(card_path.resolve()) if card_path else None,
        "author_profile": author_profile,
        "style_skill": style_skill,
        "research": research,
    }
    jobs: list[dict] = []

    # ── 阶段A：骨架单元验收（合法 WAL 直接复用·缺失/破损 → pending）──
    skeleton = None
    sk_diag = "期望产物未落盘"
    if sk_path.exists():
        norm, diag = _normalize_skeleton(_read_json_or_none(sk_path))
        if norm is not None:
            skeleton = norm
        else:
            sk_diag = f"单元破损（{diag}）·退回 pending 重写"
    jobs.append({
        "unit": "skeleton",
        "status": "ready" if skeleton is not None else "pending",
        "expected_output": str(sk_path.resolve()),
        "inputs": dict(shared_inputs),
        "input_digest": _input_digest([card_path]),
        "diag": "" if skeleton is not None else sk_diag,
    })
    if skeleton is not None:
        print(f"[gen_creative][volume_arc] 骨架单元验收通过（{len(skeleton['volumes'])} 卷）")

    # ── 阶段B：逐卷 ME 池单元验收（骨架就绪后才能列出卷单元）──
    chunks: dict[int, dict] = {}
    if skeleton is not None:
        vol_nos = [v["vol"] for v in skeleton["volumes"]]
        for n in sorted(vol_nos):
            known_ids = _known_me_ids(chunks)   # 已完成前卷 id 全集·prerequisites 回溯合法性判据
            p = wal_dir / volume_arc_wal_name(n)
            ready = False
            diag_n = "期望产物未落盘"
            if p.exists():
                norm, diag = _normalize_volume_chunk(_read_json_or_none(p), n, known_ids)
                if norm is not None:
                    chunks[n] = norm
                    ready = True
                else:
                    diag_n = f"单元破损（{diag}）·退回 pending 重写"
                    print(f"[WARN] {p.name} {diag_n}", file=sys.stderr)
            jobs.append({
                "unit": f"v{n}",
                "vol": n,
                "status": "ready" if ready else "pending",
                "expected_output": str(p.resolve()),
                "inputs": {
                    **shared_inputs,
                    "skeleton": str(sk_path.resolve()),
                    "prev_chunk_wals": [str((wal_dir / volume_arc_wal_name(m)).resolve())
                                        for m in sorted(chunks) if m < n],
                },
                "input_digest": _input_digest([card_path, sk_path]),
                "diag": "" if ready else diag_n,
            })

    manifest_path = wal_dir / VOLUME_ARC_JOBS_WAL
    _write_jobs_manifest(
        manifest_path, project_root=project_root,
        params={"cluster_count": cluster_count, "framework": framework, "rhythm": rhythm},
        inputs=shared_inputs, jobs=jobs,
        reference_patterns_block=reference_patterns_block)

    pending = [j for j in jobs if j["status"] == "pending"]
    if pending:
        if card_path is None:
            print("[FATAL] volume_arc 有待补单元但选中灵感卡不可读（--selected-card）——"
                  "agent 无故事内容来源；先修输入再重跑", file=sys.stderr)
            return 1
        units = ", ".join(j["unit"] for j in pending)
        print(f"[PENDING] volume_arc {len(pending)} 个单元待 agent 亲笔补件: {units}\n"
              f"任务清单: {manifest_path}\n"
              f"主代理按清单逐单元 spawn {VOLUME_ARC_AGENT} MODE={VOLUME_ARC_AGENT_MODE} "
              f"写 expected_output 后重跑本命令（同参续跑验收）", file=sys.stderr)
        return 2

    # ── 阶段C：确定性合并（ME id 跨卷重复=硬报错·绝不静默覆盖）──
    data, dups = _merge_volume_arc_chunks(skeleton, chunks)
    if dups:
        for d in dups:
            print(f"[ERROR] volume_arc ME id 跨卷重复: {d['id']}"
                  f"（首现 v{d['first_vol']}·重复 v{d['dup_vol']}）", file=sys.stderr)
        for n in sorted({d["dup_vol"] for d in dups}):
            bad = wal_dir / volume_arc_wal_name(n)
            broken = wal_dir / (volume_arc_wal_name(n) + ".dup_broken")
            try:
                bad.replace(broken)
                print(f"[ERROR] 已隔离 {bad.name} → {broken.name}（重跑该卷退回 pending 重写）",
                      file=sys.stderr)
            except OSError:
                pass
        return 1

    # _metadata 确定性覆盖（不靠 agent 自觉回填·cluster_emergence_engine 消费此键判卷末阈值）
    md = data.setdefault("_metadata", {})
    if isinstance(md, dict):
        md["cluster_count_per_volume"] = cluster_count
    if author_profile is None:
        data["_author_profile_missing"] = True

    if args.emit_to_db:
        pm, pc = _emit_volume_arc_to_db(project_root, data,
                                        rhythm=args.rhythm or "",
                                        framework=args.framework or "")
        print(f"[gen_creative][volume_arc] 大势卡 {len(data.get('volumes', []))} 卷 / "
              f"{len(data.get('major_events', []))} ME → {pm.name} + {pc.name}"
              f"（单元 WAL {sorted(chunks)}·agent 亲笔 + 确定性验收合并）")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
