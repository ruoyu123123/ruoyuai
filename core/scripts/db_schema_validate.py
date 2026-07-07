"""db_schema_validate.py — 13 个核心 JSON schema 校验 + 自动 migrate（v17.5 / P1.1）

修复"数据库 schema 漂移"问题——常见 migrate：
- 人物卡 dict→list
- 故事块摘要 dict→list
- 进度.cluster_blueprint 字段补全

每次 /outline 初始化和 /cluster-save-state 前跑一次。schema 不符合 → 自动 migrate（备份后改）或报错。

用法：
    python db_schema_validate.py <项目路径> [--auto-migrate] [--strict]
    python db_schema_validate.py <项目路径> --require-quantitative-keys [作者档路径]
    # 🔴 W5：/db 手改后单文件契约重校验（手改破契约防护）
    python db_schema_validate.py <项目路径> --post-edit <被手改的子系统JSON路径>
    python db_schema_validate.py <项目路径> --revalidate-after-manual <路径>   # 同义

退出码：
    0  全部通过 / 已自动 migrate / 手改未破坏契约
    1  发现 schema 错误（--strict 模式下）
    2  致命错误（如 JSON 损坏）/ --post-edit 检出手改破契约
"""

import sys
import json
import shutil
from pathlib import Path
from datetime import datetime


# 13 个核心 JSON 的【深 schema 规则】（collection 类型 + 必需字段）。其余 21 个高级子系统
# （见 subsystem_skeletons.json._canonical_34）刻意不在此做深校验——它们 schema 灵活、由
# build_manifest 容差读取多种真实项目变体；存在性 + 合法 JSON + schema_version 由
# scaffold_subsystems.py verify 兜底（二者分工不重复·见 SUBSYSTEM_FRAMEWORK.md）。给灵活件
# 加严 schema 会误报（本验证器自身曾因过严规则反向误导·见下方契约债根治记录）。
# 字段格式：(json_name, top_keys_required, item_collection_key, expected_item_type, expected_item_required_fields)
SCHEMA_RULES = {
    "人物卡": {
        "required_top_keys": ["schema_version", "characters"],
        "collection_key": "characters",
        "collection_type": list,
        "item_required_fields": ["id", "name", "role"],
    },
    "世界观": {
        "required_top_keys": ["schema_version"],
        "collection_key": "entries",
        "collection_type": list,
        "item_required_fields": ["id", "title", "keywords"],
        "optional": True,  # entries 字段可能不存在（早期项目）
    },
    "伏笔表": {
        "required_top_keys": ["schema_version", "promises"],
        "collection_key": "promises",
        "collection_type": list,
        # v2 cluster 化（2026-05-28）：纯 cluster 模式
        "item_required_fields": ["id", "description", "setup_cluster", "tier"],
    },
    "故事块摘要": {
        "required_top_keys": ["schema_version", "clusters"],
        "collection_key": "clusters",
        "collection_type": list,
        "item_required_fields": ["cluster_id", "title"],
    },
    "进度": {
        "required_top_keys": ["schema_version", "book_title", "current_cluster"],
        "collection_key": "cluster_blueprint",
        "collection_type": dict,
        "item_required_fields": [],  # cluster_blueprint 是 dict（cluster_id → cluster_data）
    },
    "场景规则": {
        "required_top_keys": ["schema_version"],
        "collection_key": "scene_types",
        "collection_type": dict,  # 这个本身是 dict 而非 list
        "item_required_fields": [],
    },
    # 2026-06-01 根治[契约债]：权威结构 = success_patterns/failure_patterns/preferences
    # （见 learning_loop.py 文档：entries 是 novel-reflector 的"输入"格式，非本文件结构；
    # build_manifest.experience_entries() 兼容读两种）。旧规则要 entries 产生持续误报，
    # 反过来会误导弱模型把对的文件改错。改对齐权威结构，不强约束 item 字段。
    "写作经验": {
        "required_top_keys": ["schema_version", "success_patterns"],
        "collection_key": "success_patterns",
        "collection_type": list,
        "item_required_fields": [],
    },
    # 2026-05-29 复审修复（L15）：实际 用户偏好.json 顶层是分组键
    # （workflow_preferences / style_preferences / content_preferences / ecas_config），
    # 多数项目无 preferences[] 数组，旧规则要求 preferences[] + item {id,key,value} 产生
    # 大量误报 advisory。改为：顶层不强制 preferences；preferences[] 存在时才校验为 list，
    # 不再要求 id/key/value（不同项目 schema 形态不一）。
    "用户偏好": {
        "required_top_keys": ["schema_version"],
        "collection_key": "preferences",
        "collection_type": list,
        "item_required_fields": [],
        "optional": True,  # 文件可缺；preferences[] 也可缺（顶层分组键形态）
    },
    # 2026-06-01 根治[契约债]：locations 是 list（命令文档 / scaffold 骨架 / 本模块自身的
    # migrate_dict_to_list 都按 list；消费方 build_manifest 只读 character_positions(dict)/
    # travel_log，不按类型读 locations）。旧规则写成 dict 与自身 migrate 方向矛盾 → 误报 TYPE_MISMATCH。
    "地图": {
        "required_top_keys": ["schema_version", "locations"],
        "collection_key": "locations",
        "collection_type": list,
        "item_required_fields": [],
    },
    "关系": {
        "required_top_keys": ["schema_version", "relationships"],
        "collection_key": "relationships",
        "collection_type": list,
        "item_required_fields": ["id", "from", "to", "type"],
    },
    "事件表": {
        "required_top_keys": ["schema_version"],
        "collection_key": "pending_events",
        "collection_type": list,
        "item_required_fields": ["id", "name"],
    },
    "时间线": {
        "required_top_keys": ["schema_version"],
        "collection_key": "world_clock_events",
        "collection_type": list,
        # v2 cluster 化（2026-05-28）：world clock event 用 day/cluster 颗粒
        "item_required_fields": ["event"],
    },
    "道具": {
        "required_top_keys": ["schema_version", "items"],
        "collection_key": "items",
        "collection_type": list,
        "item_required_fields": ["id", "name"],
    },
}


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON 损坏：{p} — {e}")


def save_json(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def backup_then_save(p: Path, data):
    """改写前备份原文件到 _backup/db_schema/<时间戳>/。"""
    backup_root = p.parent / "_backup" / "db_schema" / datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, backup_root / p.name)
    save_json(p, data)


def migrate_dict_to_list(data: dict, collection_key: str) -> tuple[dict, bool]:
    """把 dict 形式的 collection 转 list。返回 (new_data, migrated)。"""
    coll = data.get(collection_key)
    if not isinstance(coll, dict):
        return data, False
    new_list = []
    for k, v in coll.items():
        if isinstance(v, dict):
            v = dict(v)  # 拷贝
            # 如果原 key 是数字（章号），追加 'ch' 字段
            try:
                v["ch"] = int(k)
            except (ValueError, TypeError):
                v["_orig_key"] = k
            new_list.append(v)
    # 按 ch 排序（如有）
    try:
        new_list.sort(key=lambda x: x.get("ch", 0))
    except TypeError:
        pass
    data[collection_key] = new_list
    return data, True


# 🔴 2026-07-06 P1 伏笔生命周期三态枚举（借鉴 PlotPilot foreshadow registry ·
# research/open_source_writing_systems.md）。伏笔表.promises 从「resolved: bool」升级为：
#   status ∈ {open, suspended, consumed}（open=已埋未收 / suspended=显式挂起延后 / consumed=已回收）
#   owner（负责回收的角色/线索归属·缺省 "writer"）
#   payoff_scope（预期回收范围描述·从 due_by/due_by_cluster 派生·允许空串）
# 不兼容不降级：迁移后消费方只读 status，resolved 字段删除（true→consumed / false→open）；
# resolved_at_ch/_resolved_by 同步更名 consumed_at_ch/_consumed_by（单口径·零 resolved 残留）。
# secrets[] 的 status 语义（hidden/revealed·明暗线隔离机制）不在本枚举内，绝不混改。
FORESHADOW_STATUS_ENUM = ("open", "suspended", "consumed")

# 历史 status 字符串形态归一（旧 example/项目实测存在 "planted" 等）——一次性迁移进三态。
_FORESHADOW_LEGACY_STATUS_MAP = {
    "planted": "open", "active": "open", "pending": "open",
    "declared": "open", "initiated": "open",
    "paid": "consumed", "resolved": "consumed", "done": "consumed", "回收": "consumed",
    "挂起": "suspended",
}


def derive_payoff_scope(p: dict) -> str:
    """从 promise 现有到期字段派生 payoff_scope 文本（无到期信息 → 空串·允许）。"""
    dbc = p.get("due_by_cluster")
    if isinstance(dbc, str) and dbc.strip():
        return f"预期在 {dbc.strip()} 内回收"
    dby = p.get("due_by")
    if isinstance(dby, int) and not isinstance(dby, bool):
        return f"预期在第 {dby} 章前回收"
    if p.get("due_by_pending_resolution"):
        off = p.get("due_by_ch_offset", 20)
        setc = p.get("setup_cluster")
        anchor = setc if setc else "setup"
        return f"预期自 {anchor} 起始章 {off} 章内回收"
    return ""


def migrate_promise_lifecycle(p: dict) -> bool:
    """单条 promise 迁移到三态生命周期。返回是否有改动（幂等：已迁移条目再跑返回 False）。

    规则：
      1. status 缺失/空 → 按 resolved 派生（truthy→consumed / 否则→open）；
         历史 status 字符串（planted/paid/...）→ 归一进三态枚举。
      2. resolved 字段删除（不留兼容读）；resolved_at_ch→consumed_at_ch、_resolved_by→_consumed_by。
      3. owner 缺失/空 → "writer"；payoff_scope 缺失 → 从 due_by 族派生（可为空串）。
    """
    changed = False
    status = p.get("status")
    if isinstance(status, str) and status.strip():
        raw = status.strip()
        norm = _FORESHADOW_LEGACY_STATUS_MAP.get(raw.lower(), _FORESHADOW_LEGACY_STATUS_MAP.get(raw, raw))
        if norm != status:
            p["status"] = norm
            changed = True
    else:
        p["status"] = "consumed" if p.get("resolved") else "open"
        changed = True
    if "resolved" in p:
        del p["resolved"]
        changed = True
    if "resolved_at_ch" in p:
        p["consumed_at_ch"] = p.pop("resolved_at_ch")
        changed = True
    if "_resolved_by" in p:
        p["_consumed_by"] = p.pop("_resolved_by")
        changed = True
    owner = p.get("owner")
    if not (isinstance(owner, str) and owner.strip()):
        p["owner"] = "writer"
        changed = True
    if "payoff_scope" not in p:
        p["payoff_scope"] = derive_payoff_scope(p)
        changed = True
    return changed


def check_foreshadow_lifecycle(db_root: Path, auto_migrate: bool) -> tuple[list[str], list[str], bool]:
    """伏笔表.promises 三态生命周期迁移 + status 枚举白名单校验。

    返回 (errors, warnings, migrated)。auto_migrate=True 时先迁移（备份后落盘·幂等），
    再校验；非法/缺失 status（不在 FORESHADOW_STATUS_ENUM）→ error（枚举硬白名单）。
    只处理 promises 族——secrets/deadlines/pledges 有各自 status 语义，不碰。
    """
    errors: list[str] = []
    warnings: list[str] = []
    path = db_root / "伏笔表.json"
    if not path.exists():
        return errors, warnings, False
    data = _safe_load(path)
    if not isinstance(data, dict):
        return errors, warnings, False  # JSON_BROKEN 由 validate_file 报，不重复
    promises = data.get("promises")
    if not isinstance(promises, list):
        return errors, warnings, False  # TYPE_MISMATCH 由 SCHEMA_RULES 报，不重复
    changed_count = 0
    for i, item in enumerate(promises):
        if not isinstance(item, dict):
            continue
        if auto_migrate and migrate_promise_lifecycle(item):
            changed_count += 1
        st = item.get("status")
        if st not in FORESHADOW_STATUS_ENUM:
            errors.append(
                f"[FORESHADOW_STATUS_INVALID] 伏笔表.promises[{i}]({item.get('id')}) "
                f"status={st!r} 非法（合法枚举 {list(FORESHADOW_STATUS_ENUM)}·"
                f"旧 resolved/planted 形态跑 --auto-migrate 一次性迁移）")
    migrated = False
    if changed_count:
        backup_then_save(path, data)
        migrated = True
        warnings.append(
            f"[MIGRATED] 伏笔表.json.promises: {changed_count} 条迁移到三态生命周期 "
            f"status/owner/payoff_scope（resolved 已删除·已备份）")
    return errors, warnings, migrated


def validate_file(path: Path, rules: dict, auto_migrate: bool) -> tuple[list[str], list[str], bool]:
    """校验单个 JSON。返回 (errors, warnings, migrated)。"""
    errors = []
    warnings = []
    migrated = False

    if not path.exists():
        if rules.get("optional"):
            warnings.append(f"[OPTIONAL_MISSING] {path.name} 不存在（可选文件）")
            return errors, warnings, False
        errors.append(f"[MISSING] {path.name} 不存在")
        return errors, warnings, False

    try:
        data = load_json(path)
    except RuntimeError as e:
        errors.append(f"[JSON_BROKEN] {path.name}: {e}")
        return errors, warnings, False

    # 顶层 keys 检查
    for k in rules.get("required_top_keys", []):
        if k not in data:
            warnings.append(f"[MISSING_TOP_KEY] {path.name} 缺 '{k}'")

    # collection 类型检查
    coll_key = rules.get("collection_key")
    coll_type = rules.get("collection_type")
    if coll_key and coll_key in data:
        coll = data[coll_key]
        if not isinstance(coll, coll_type):
            current_type = type(coll).__name__
            expected = coll_type.__name__
            if coll_type is list and isinstance(coll, dict) and auto_migrate:
                data, did = migrate_dict_to_list(data, coll_key)
                if did:
                    backup_then_save(path, data)
                    migrated = True
                    warnings.append(f"[MIGRATED] {path.name}.{coll_key}: dict→list（已备份）")
            else:
                errors.append(
                    f"[TYPE_MISMATCH] {path.name}.{coll_key}: 实际 {current_type}，期望 {expected}"
                )

    # item 必需字段检查（仅警告，不致命）
    req_fields = rules.get("item_required_fields", [])
    if req_fields and coll_key in data and isinstance(data[coll_key], list):
        for i, item in enumerate(data[coll_key]):
            if not isinstance(item, dict):
                continue
            missing = [f for f in req_fields if f not in item]
            if missing:
                warnings.append(
                    f"[ITEM_MISSING_FIELD] {path.name}.{coll_key}[{i}] 缺字段：{missing}"
                )

    return errors, warnings, migrated


def check_idempotency_invariants(db_root: Path) -> list[str]:
    """🔴 2026-06-27 C11：cluster 级幂等不变量（advisory · 纯确定性 · 不改 exit 语义）。

    检 split_cluster_changes 平铺 + 逐章重放残留的 N 倍污染——只读现有库数据即可判定，
    不引入新 hard_gate（北极星）。三条不变量：
      1. 人物卡 growth_arc：同一角色不应有两条 (key_change,trigger,_source_cluster) 全同的成长记录
         （同 cluster 同一逻辑成长事件被 append 多次 = 幂等失守）。
      2. 时间线 time_log：不应有两条 (elapsed,key_events,_source_cluster) 全同的推进记录。
      3. 世界状态 active_npc_threads：responded_count 不应 > 去重 responded_by_cluster 数
         （同 cluster 一次呼应被多次计数 = 幂等失守）。
    返回 advisory warning 列表（调用方并入 total_warnings · 不计入 errors · 不影响退出码）。
    """
    warnings: list[str] = []

    # 1. 人物卡 growth_arc per-cluster 重复
    cards = _safe_load(db_root / "人物卡.json")
    if isinstance(cards, dict):
        for c in cards.get("characters", []):
            if not isinstance(c, dict):
                continue
            seen: dict = {}
            for e in c.get("growth_arc", []) or []:
                if not isinstance(e, dict) or "_source_cluster" not in e:
                    continue  # 只对带幂等键的新条目判定（旧条目无键 → 跳过不误报）
                key = (e.get("key_change"), e.get("trigger", ""), e.get("_source_cluster"))
                seen[key] = seen.get(key, 0) + 1
            dups = {k: n for k, n in seen.items() if n > 1}
            if dups:
                warnings.append(
                    f"[IDEMPOTENCY_GROWTH_ARC] 人物卡 角色 {c.get('name', '?')!r} growth_arc "
                    f"有 {len(dups)} 组 cluster 级重复成长记录（同 _source_cluster 同事件 append 多次）")

    # 2. 时间线 time_log per-cluster 重复
    timeline = _safe_load(db_root / "时间线.json")
    if isinstance(timeline, dict):
        seen_t: dict = {}
        for e in timeline.get("time_log", []) or []:
            if not isinstance(e, dict) or "_source_cluster" not in e:
                continue
            ke = e.get("key_events", [])
            ke_key = tuple(ke) if isinstance(ke, list) else ke
            key = (e.get("elapsed", ""), ke_key, e.get("_source_cluster"))
            seen_t[key] = seen_t.get(key, 0) + 1
        dups_t = {k: n for k, n in seen_t.items() if n > 1}
        if dups_t:
            warnings.append(
                f"[IDEMPOTENCY_TIME_LOG] 时间线 time_log 有 {len(dups_t)} 组 cluster 级重复推进记录")

    # 3. 世界状态 active_npc_threads responded_count vs responded_by_cluster
    world = _safe_load(db_root / "世界状态.json")
    if isinstance(world, dict):
        for t in world.get("active_npc_threads", []) or []:
            if not isinstance(t, dict):
                continue
            rc = t.get("responded_count")
            rbc = t.get("responded_by_cluster")
            if isinstance(rc, int) and isinstance(rbc, list):
                distinct = len(set(rbc))
                if rc > distinct:
                    warnings.append(
                        f"[IDEMPOTENCY_RESPONDED_COUNT] 世界状态 thread {t.get('thread_id', '?')!r} "
                        f"responded_count={rc} > 去重 cluster 数={distinct}（同 cluster 一次呼应被多计）")
    return warnings


def _safe_load(p: Path):
    """读 JSON · 缺失/损坏返回 None（不变量检查不阻断 · advisory）。"""
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# 🔴 2026-06-27 C19：大势卡（major_events ME 池）结构契约（确定性·引用完整性·hard）。
# 【接线】outline.plan.json step7 跑 `db_schema_validate.py {project_root}` 消费本校验（C19 接线点·
#   该步在 step5 volume_arc 填完大势卡之后跑）；cluster-save-state step1 的 --auto-migrate 也会带跑。
# 【协调】W2-A 在 gen_creative_volume_arc._normalize_me_pool 做**生产端归一**（产出标准 ME 池·别的 agent·别的文件）·
#   本函数在**消费端**校验结构破损 + 引用完整性·两者互补不冲突。
# 【北极星②③④】只校验确定性结构/引用完整性——绝不校验 ME 内容质量/数量/叙事顺序·绝不增删/重排 ME·
#   绝不预设 cluster_002+（fluid 涌现归 emergence）。bare scaffold 空骨架按 advisory 处理（不当破损）。
def check_grand_trend_structure(db_root: Path) -> tuple[list[str], list[str]]:
    """大势卡结构契约。返回 (errors, warnings)。

    规则（仅当 authoring 已开始 = volumes 或 major_events 非空才施加 hard 约束）：
      1. major_events 非空且为 list（卷已定义但 ME 池空 → emergence 大势无方向）。
      2. 每个 ME 有 id + volume。
      3. 每个出现在 ME 池里的 volume ≥1 is_volume_finale=true（卷边界标记·emergence 换卷靠它）。
      4. 所有 prerequisites 引用可解析（指向存在的 ME id）。
    bare scaffold 空骨架（volumes + major_events 皆空）→ advisory（outline 未填·fluid 起步合法）。
    """
    errors: list[str] = []
    warnings: list[str] = []
    p = db_root / "大势卡.json"
    if not p.exists():
        warnings.append("[OPTIONAL_MISSING] 大势卡.json 不存在（可选·IP 线性改编/未到 outline 阶段）")
        return errors, warnings
    data = _safe_load(p)
    if not isinstance(data, dict):
        errors.append("[GRAND_TREND_BROKEN] 大势卡.json 不是合法 JSON 对象")
        return errors, warnings

    mes = data.get("major_events")
    volumes = data.get("volumes")
    authored = bool(volumes) or bool(mes)   # 已开始 authoring（非 bare scaffold 空骨架）
    if not authored:
        warnings.append(
            "[GRAND_TREND_NOT_AUTHORED] 大势卡.json 仍是空骨架（volumes/major_events 皆空·outline 未填 ME 池·fluid 起步）")
        return errors, warnings

    if not isinstance(mes, list) or not mes:
        errors.append(
            "[GRAND_TREND_ME_POOL_EMPTY] 大势卡.major_events 空/非 list（卷已定义但 ME 池未填 → emergence 大势无方向·北极星③）")
        return errors, warnings

    me_ids: set = set()
    vol_has_finale: dict = {}
    vol_seen: set = set()
    for i, me in enumerate(mes):
        if not isinstance(me, dict):
            errors.append(f"[GRAND_TREND_ME_NOT_OBJECT] major_events[{i}] 非对象")
            continue
        mid = me.get("id")
        vol = me.get("volume")
        if isinstance(mid, str) and mid.strip():
            me_ids.add(mid.strip())
        else:
            errors.append(f"[GRAND_TREND_ME_MISSING_FIELD] major_events[{i}] 缺 id")
        if vol is None or (isinstance(vol, str) and not vol.strip()):
            errors.append(f"[GRAND_TREND_ME_MISSING_FIELD] major_events[{i}]({mid}) 缺 volume")
        else:
            vol_seen.add(vol)
            is_finale = me.get("is_volume_finale") in (True, "true", "True", "是")
            vol_has_finale[vol] = vol_has_finale.get(vol, False) or is_finale

    for vol in sorted(vol_seen, key=lambda x: str(x)):
        if not vol_has_finale.get(vol):
            errors.append(
                f"[GRAND_TREND_VOLUME_NO_FINALE] volume {vol} 的 ME 池无 is_volume_finale=true"
                f"（卷末转折标记缺失 → emergence 无法识别卷边界·北极星 v28 卷=阶段触发点）")

    for i, me in enumerate(mes):
        if not isinstance(me, dict):
            continue
        for pr in me.get("prerequisites", []) or []:
            if isinstance(pr, str) and pr.strip() and pr.strip() not in me_ids:
                errors.append(
                    f"[GRAND_TREND_PREREQ_UNRESOLVED] major_events[{i}]({me.get('id')}) "
                    f"prerequisite {pr!r} 指向不存在的 ME id")
    return errors, warnings


# 🔴 2026-06-27 C02：live-consumed 子系统空内容 advisory 背板（非 hard·只产 warning·不改 exit）。
# outline 完成后（大势卡 major_events 非空 = outline 已填 ME 池）·扫 _数据库 子系统 JSON：
#   consumption.status == live 且 consumption.by 含 world_evolution_engine/build_manifest，
#   但内容全空 → advisory 提示（被引擎 live 消费却没料）。
# 北极星：advisory 非 hard_gate（IP 线性改编/严肃文学可稀疏起步·涟漪/emergence 后续填料）。
_C02_TARGET_CONSUMERS = ("world_evolution_engine", "build_manifest")


def _has_content(v) -> bool:
    """递归判定值是否含『真内容』（空串/空表/空字典/0/bool flag 都算空·跳过 _ 前缀 meta 键）。"""
    if isinstance(v, bool):
        return False  # 布尔 flag 不算内容
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, list):
        return any(_has_content(x) for x in v)
    if isinstance(v, dict):
        return any(_has_content(x) for k, x in v.items() if not str(k).startswith("_"))
    return False


def check_consumed_but_empty(db_root: Path) -> list[str]:
    """C02 advisory 背板：live-consumed 却空内容的子系统。返回 advisory warnings（不计 errors）。"""
    warnings: list[str] = []
    # gate：仅 outline 完成后跑（大势卡 ME 池非空）→ 否则 bare scaffold 普遍空属正常·避免噪声
    gt = _safe_load(db_root / "大势卡.json")
    if not (isinstance(gt, dict) and isinstance(gt.get("major_events"), list) and gt["major_events"]):
        return warnings
    for p in sorted(db_root.glob("*.json")):
        data = _safe_load(p)
        if not isinstance(data, dict):
            continue
        cons = data.get("consumption")
        if not isinstance(cons, dict) or cons.get("status") != "live":
            continue
        by = cons.get("by")
        by_str = " ".join(str(x) for x in by) if isinstance(by, list) else str(by or "")
        if not any(t in by_str for t in _C02_TARGET_CONSUMERS):
            continue
        content_vals = [v for k, v in data.items()
                        if not str(k).startswith("_") and k not in ("schema_version", "consumption")]
        if not any(_has_content(v) for v in content_vals):
            warnings.append(
                f"[CONSUMED_BUT_EMPTY] {p.name} 被 {by_str} live 消费却内容全空"
                f"（advisory·IP 线性改编/严肃文学可稀疏起步·涟漪/emergence 后续填·非 hard）")
    return warnings


# 🔴 2026-06-27 C14②：作者档数值契约键存在性闸（distill-style.plan.json step2 消费·消费端二道闸）。
# 与 consolidate_author_profile.py 末尾的生产端自断言（C14①）互补：consolidate 在产出点
# 缺核心 consumer 键即 [FATAL] exit2；本闸在 plan step 层再确认产出的 作者风格.json 契约键齐全。
# 只断言键 PRESENCE·绝不断言数值落区间（高方差作者句长 31 也合法·防题材先验误杀·北极星④⑤）。
def _missing_quantitative_keys(q: dict) -> list[str]:
    """检 quantitative 块缺哪些核心 consumer 契约键（build_manifest/validate_style band 门控硬依赖）。"""
    missing: list[str] = []
    if not (isinstance(q.get("sentence_length"), dict) and q["sentence_length"]):
        missing.append("sentence_length")
    if not ("paragraph_length_chars" in q
            or (isinstance(q.get("paragraph_length"), dict) and "mean_chars" in q["paragraph_length"])):
        missing.append("paragraph_length_chars|paragraph_length.mean_chars")
    if not ("dialogue_ratio_pct" in q or "dialogue_ratio" in q):
        missing.append("dialogue_ratio_pct|dialogue_ratio")
    if "single_sentence_para_ratio" not in q:
        missing.append("single_sentence_para_ratio")
    return missing


def _require_quantitative_keys(profile_path: Path) -> int:
    """作者档 quantitative 含全核心 consumer 契约键 → 0；缺则 [FATAL] stderr → 2。"""
    if not profile_path.exists():
        print(f"[FATAL] --require-quantitative-keys：作者档不存在 {profile_path}", file=sys.stderr)
        return 2
    try:
        prof = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[FATAL] --require-quantitative-keys：作者档 JSON 损坏 {profile_path} — {e}", file=sys.stderr)
        return 2
    q = prof.get("quantitative") or {}
    missing = _missing_quantitative_keys(q if isinstance(q, dict) else {})
    if missing:
        print(f"[FATAL] 作者档 {profile_path.name} quantitative 缺核心 consumer 契约键 {missing} "
              f"— consolidate_author_profile 未产出/源 metrics schema 漂移（作者档=第一权威·缺契约键即响亮失败·北极星④⑤）。",
              file=sys.stderr)
        return 2
    print(f"[OK] 作者档数值契约键齐全：{profile_path.name}")
    return 0


# 🔴 2026-06-27 W5：/db 手改单文件后重校验（手改破契约防护·schema 契约层·非内容质量）。
# 根因（completeness critic 揪出的零约束裸奔环节）：用户经 /db 手改子系统 JSON 后无强制重新
# schema 校验——手改可破坏契约后直接被 build_manifest 消费（C03/C17 只在 outline/save-state
# 触发·不覆盖 /db 之后的手改）。本模式补这条裸奔链路：手改后跑该单文件的契约重校验。
# 北极星④⑤：只查 schema 契约（结构/类型/载荷非空）·绝不查内容质量·手改合法稀疏(fluid)不误拦
# ——只拦真契约破损（SCHEMA_RULES 结构 error + C03 载荷 hard 子集 ≤3 码 + 大势卡引用完整性）。
_LOAD_BEARING_DEDICATED = {"大势卡"}  # 大势卡由 check_grand_trend_structure 覆盖 ME 池·不重复跑 eval_load_bearing


def _eval_single_load_bearing(stem: str, obj) -> tuple[str, bool] | None:
    """对单个子系统跑 C03 载荷非空判定（复用 scaffold_subsystems 单一真理源）。

    返回 (code, ok)：仅当该 stem 在 skeleton 里带 _load_bearing_nonempty 标记才非 None。
    bare scaffold（文件 == 骨架·outline 未填）→ 返回 None（advisory·fluid 起步合法·不误拦）。
    """
    try:
        import sys as _sys
        sd = str(Path(__file__).resolve().parent)
        if sd not in _sys.path:
            _sys.path.insert(0, sd)
        import scaffold_subsystems as _scaf
        _canonical, skeletons = _scaf._load_skeletons()
    except Exception:
        return None  # 骨架不可达 → 防御性放行（不因辅助检查阻断手改重校验）
    skel = skeletons.get(stem)
    if not isinstance(skel, dict):
        return None
    marker = skel.get(_scaf.LOAD_BEARING_KEY)
    if not isinstance(marker, dict):
        return None
    # bare scaffold 逃逸：文件与骨架完全相同 = outline 未真正填·属合法稀疏起步（advisory·不拦）
    if obj == skel:
        return None
    return _scaf.eval_load_bearing(marker, obj)


def revalidate_after_manual(file_path: Path) -> int:
    """手改单个子系统 JSON 后跑该文件契约重校验。返回 exit code（0 过 / 2 破契约）。

    复用：SCHEMA_RULES（结构/类型）+ check_grand_trend_structure（大势卡 ME 池引用完整性）
         + scaffold_subsystems.eval_load_bearing（C03 载荷非空 hard 子集 ≤3·涟漪规则/事件簇）。
    破契约（结构 error / 载荷 inert）→ 打印 + 修复 hint → exit 2；warning → advisory 打印·exit 0。
    """
    if not file_path.exists():
        print(f"[FATAL] --post-edit：文件不存在 {file_path}", file=sys.stderr)
        return 2
    try:
        obj = load_json(file_path)
    except RuntimeError as e:
        print(f"[FATAL] --post-edit：JSON 损坏 {file_path.name} — {e}\n"
              f"   修复 hint：手改引入了非法 JSON（逗号/引号/括号）·用编辑器格式化校验后重存。",
              file=sys.stderr)
        return 2

    stem = file_path.stem  # 如 "人物卡" / "大势卡" / "涟漪规则"
    errors: list[str] = []
    warnings: list[str] = []

    # 1) SCHEMA_RULES 结构/类型校验（auto_migrate=False·手改不静默迁移·只如实报告）
    rules = SCHEMA_RULES.get(stem)
    if rules:
        errs, warns, _mig = validate_file(file_path, rules, auto_migrate=False)
        errors.extend(errs)
        warnings.extend(warns)

    # 2) 大势卡：ME 池引用完整性（W2-C check_grand_trend_structure·含 bare scaffold→advisory 纳入）
    if stem == "大势卡":
        gt_errs, gt_warns = check_grand_trend_structure(file_path.parent)
        errors.extend(gt_errs)
        warnings.extend(gt_warns)

    # 2.5) 伏笔表：promises 三态 status 枚举白名单（2026-07-06 P1·手改不静默迁移·非法即报）
    if stem == "伏笔表":
        fs_errs, fs_warns, _ = check_foreshadow_lifecycle(file_path.parent, auto_migrate=False)
        errors.extend(fs_errs)
        warnings.extend(fs_warns)

    # 3) C03 载荷非空（涟漪规则/事件簇·大势卡已由 step2 覆盖不重复）
    if stem not in _LOAD_BEARING_DEDICATED:
        lb = _eval_single_load_bearing(stem, obj)
        if lb is not None:
            code, ok = lb
            if not ok:
                errors.append(
                    f"[LOAD_BEARING_EMPTY] {file_path.name} 载荷点火路径空（{code}·机器永不点火·"
                    f"hard·涟漪规则空=引擎零触发 / cluster_001 storyboard 空=首块未详化）")

    rel_name = file_path.name
    print(f"[Schema 重校验·手改] {rel_name}")
    print(f"  Errors: {len(errors)}, Warnings: {len(warnings)}")
    if warnings:
        print("\n[警告·advisory（合法稀疏/fluid·不阻断）]")
        for w in warnings:
            print(f"  {w}")
    if errors:
        print("\n[错误·契约破损（手改破坏 schema 契约·须修复后再被 build_manifest 消费）]")
        for e in errors:
            print(f"  {e}")
        print("\n[修复 hint]")
        print("  · TYPE_MISMATCH → collection 字段（characters/locations/...）须是数组而非对象")
        print("  · GRAND_TREND_* → ME 池每条带 id+volume·每卷 ≥1 is_volume_finale·prereq 指向存在 ME")
        print("  · FORESHADOW_STATUS_INVALID → promises[].status 只能是 open/suspended/consumed")
        print("  · LOAD_BEARING_EMPTY → 涟漪规则 rules / 事件簇 clusters[0].scene_storyboard 不可清空")
        print("  · 还原：从 _backup/db_schema/ 取最近备份·或撤销本次手改")
        return 2
    print("\n[OK] 手改未破坏 schema 契约·可安全被 build_manifest 消费。")
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # 🔴 2026-06-27 W5：/db 手改后单文件重校验（--post-edit <file> / --revalidate-after-manual <file>）。
    # 须先于 db_root 检查处理（接收的是被手改文件的直接路径·不依赖 _数据库 目录推断）。
    for _flag in ("--post-edit", "--revalidate-after-manual"):
        if _flag in args:
            idx = args.index(_flag)
            if idx + 1 < len(args) and not args[idx + 1].startswith("-"):
                sys.exit(revalidate_after_manual(Path(args[idx + 1])))
            print(f"[FATAL] {_flag} 需跟被手改的子系统 JSON 路径", file=sys.stderr)
            sys.exit(2)
    project_root = Path(args[0])
    auto_migrate = "--auto-migrate" in args
    strict = "--strict" in args

    # 🔴 2026-06-27 C14②：作者档数值契约键存在性闸（distill-style step2 消费·distill 项目根直挂 作者风格.json·
    # 与 _数据库 无关·须先于 db_root 检查处理）。用法：db_schema_validate.py <project> --require-quantitative-keys [profile]
    if "--require-quantitative-keys" in args:
        idx = args.index("--require-quantitative-keys")
        if idx + 1 < len(args) and not args[idx + 1].startswith("-"):
            prof_path = Path(args[idx + 1])
        else:
            prof_path = project_root / "作者风格.json"
        sys.exit(_require_quantitative_keys(prof_path))

    db_root = project_root / "_数据库"
    if not db_root.exists():
        print(f"[FATAL] 找不到 _数据库 目录：{db_root}", file=sys.stderr)
        sys.exit(2)

    total_errors = []
    total_warnings = []
    migrated_files = []
    for name, rules in SCHEMA_RULES.items():
        path = db_root / f"{name}.json"
        errs, warns, mig = validate_file(path, rules, auto_migrate)
        total_errors.extend(errs)
        total_warnings.extend(warns)
        if mig:
            migrated_files.append(name)

    # 🔴 2026-07-06 P1 伏笔生命周期：promises 三态迁移（--auto-migrate 时）+ status 枚举白名单
    fs_errs, fs_warns, fs_mig = check_foreshadow_lifecycle(db_root, auto_migrate)
    total_errors.extend(fs_errs)
    total_warnings.extend(fs_warns)
    if fs_mig:
        migrated_files.append("伏笔表")

    # 🔴 2026-06-27 C11：cluster 级幂等不变量（advisory · 并入 warnings · 不计 errors）
    total_warnings.extend(check_idempotency_invariants(db_root))

    # 🔴 2026-06-27 C19：大势卡 ME 池结构契约（结构破损/引用不完整 → errors·hard）
    gt_errs, gt_warns = check_grand_trend_structure(db_root)
    total_errors.extend(gt_errs)
    total_warnings.extend(gt_warns)

    # 🔴 2026-06-27 C02：live-consumed 子系统空内容 advisory 背板（仅 warnings·不改 exit）
    total_warnings.extend(check_consumed_but_empty(db_root))

    print(f"[Schema 校验] 项目：{project_root.name}")
    print(f"  共校验 {len(SCHEMA_RULES)} 个 JSON")
    print(f"  Errors: {len(total_errors)}, Warnings: {len(total_warnings)}, Migrated: {len(migrated_files)}")
    if migrated_files:
        print(f"\n[已自动迁移]")
        for n in migrated_files:
            print(f"  - {n}.json（已备份至 _backup/db_schema/）")
    if total_warnings:
        print(f"\n[警告]")
        for w in total_warnings:
            print(f"  {w}")
    if total_errors:
        print(f"\n[错误]")
        for e in total_errors:
            print(f"  {e}")

    if total_errors and strict:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
