"""apply_archive.py — 把 novel-archivist 梳理产物 archive.json 确定性回库（无模型）。

🔴 2026-06-28 架构纠正：配置的写作模型只产正文、不自报"改了什么"；Claude(archivist agent)
读正文分辨出新增/变更的角色/道具/关系/硬事实/throughline，产 cluster_<key>_archive.json；本
脚本把它确定性写进 人物卡 / 角色池 / 道具 / 关系 / 事件簇.clusters[].locked_facts +
事件簇.clusters[].throughline_progress。

权威源 = archive（Claude 读正文）·不再读 writer 的 changes.factual 自报。

幂等：全部按 id 去重（角色 C_*、道具 I_*、关系 REL_*）·re-apply / 多次跑不重复建、不后移。

🔴 2026-06-28 不降级收尾：archive 是 factual 回库的**唯一权威路径**。每个写完的 cluster 必有
出场角色——archive 缺 characters = archivist 失败 = 错误，**exit 非0 让 plan 硬停**（不再
"空 archive → exit 1 当 no-op"静默降级）。幂等去重保留（re-apply 全已存在→exit 0 成功·非降级）。

用法:
    python apply_archive.py <项目路径> --cluster <key>
        [--archive <archive.json 路径，默认 _数据库/.wal/cluster_<key>_archive.json>]
        [--dry-run]

退出码: 0 成功（含幂等无新增）/ 2 archive 缺失/缺出场角色（archivist 失败·硬停）或回库异常
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atomic_json import atomic_write_text  # noqa: E402

try:
    import cluster_lookup
except Exception:  # noqa: BLE001
    cluster_lookup = None


def _norm_cid(key):
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


def save_json(p: Path, obj):
    atomic_write_text(p, json.dumps(obj, ensure_ascii=False, indent=2))


# ───────────────────────── 角色 → 人物卡 + 角色池 ─────────────────────────
def apply_characters(db: Path, chars: list, summary: dict, dry: bool):
    """新角色建卡 + 老角色 state_changes 追加；按 tier 分类进角色池。id 幂等。"""
    pc_path = db / "人物卡.json"
    pool_path = db / "角色池.json"
    pc = load_json(pc_path, {"schema_version": 1, "characters": []})
    pool = load_json(pool_path, {"schema_version": 1, "core": [], "emerged": [], "extras": []})
    pc.setdefault("characters", [])
    for grp in ("core", "emerged", "extras"):
        pool.setdefault(grp, [])

    by_id = {c.get("id"): c for c in pc["characters"] if isinstance(c, dict) and c.get("id")}
    # 名字 → id 反查（防 archivist 偶尔漏复用 id 时按名兜底）
    by_name = {c.get("name"): c.get("id") for c in pc["characters"] if isinstance(c, dict) and c.get("name")}
    pool_ids = {c.get("id") for grp in ("core", "emerged", "extras")
                for c in pool[grp] if isinstance(c, dict)}

    added_cards, updated_cards, pooled = 0, 0, 0
    for ch in chars or []:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id") or by_name.get(ch.get("name"))
        name = ch.get("name")
        if not cid or not name:
            continue
        card = by_id.get(cid)
        if card is None:
            # 新角色建卡（人物卡 schema: {id,name,role}）。🔴 2026-06-28：新角色也要立即填 state_changes
            # （真实管线每 cluster 只 apply 一次·不能等 re-apply 才补·否则首次出场的状态变化丢失）。
            _sl = [{"ch": s.get("ch"), "change": s.get("change")}
                   for s in (ch.get("state_changes") or []) if isinstance(s, dict)]
            card = {"id": cid, "name": name, "role": ch.get("role", ""),
                    "status": ch.get("status", "alive"),
                    "first_appearance_ch": ch.get("first_ch"), "state_log": _sl}
            pc["characters"].append(card)
            by_id[cid] = card
            by_name[name] = cid
            added_cards += 1
        else:
            # 老角色：更新 status/role（若给）+ 追加 state_changes（按 (ch,change) 去重）
            if ch.get("status"):
                card["status"] = ch["status"]
            if ch.get("role") and not card.get("role"):
                card["role"] = ch["role"]
            card.setdefault("state_log", [])
            seen = {(s.get("ch"), s.get("change")) for s in card["state_log"] if isinstance(s, dict)}
            had = len(card["state_log"])
            for sc in ch.get("state_changes", []) or []:
                if isinstance(sc, dict) and (sc.get("ch"), sc.get("change")) not in seen:
                    card["state_log"].append({"ch": sc.get("ch"), "change": sc.get("change")})
                    seen.add((sc.get("ch"), sc.get("change")))
            if len(card["state_log"]) > had:
                updated_cards += 1
        # 角色池分类（id 幂等）
        if cid not in pool_ids:
            tier = ch.get("tier", "extra")
            grp = tier if tier in ("core", "emerged", "extras") else (
                "extras" if tier == "extra" else "emerged")
            pool[grp].append({"id": cid, "name": name, "ch": ch.get("first_ch"),
                              "role": ch.get("role", "")})
            pool_ids.add(cid)
            pooled += 1

    summary["characters"] = {"cards_added": added_cards, "cards_updated": updated_cards,
                             "pooled": pooled}
    if not dry and (added_cards or updated_cards or pooled):
        save_json(pc_path, pc)
        save_json(pool_path, pool)


# ───────────────────────── 道具 → 道具.json ─────────────────────────
def apply_items(db: Path, items: list, summary: dict, dry: bool):
    p = db / "道具.json"
    d = load_json(p, {"schema_version": 1, "items": [], "item_locations": [], "crafting_recipes": []})
    d.setdefault("items", [])
    by_id = {it.get("id") for it in d["items"] if isinstance(it, dict)}
    by_name = {it.get("name") for it in d["items"] if isinstance(it, dict)}
    added = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        iid, name = it.get("id"), it.get("name")
        if not name or iid in by_id or name in by_name:
            continue
        d["items"].append({"id": iid or f"I_{len(d['items'])+1}", "name": name,
                           "desc": it.get("desc", ""), "holder": it.get("holder", ""),
                           "first_appearance_ch": it.get("first_ch")})
        if iid:
            by_id.add(iid)
        by_name.add(name)
        added += 1
    summary["items"] = {"added": added}
    if not dry and added:
        save_json(p, d)


# ───────────────────────── 关系 → 关系.json ─────────────────────────
def apply_relationships(db: Path, rels: list, summary: dict, dry: bool):
    p = db / "关系.json"
    d = load_json(p, {"schema_version": 1, "relationships": [], "faction_standings": []})
    d.setdefault("relationships", [])
    by_id = {r.get("id") for r in d["relationships"] if isinstance(r, dict)}
    by_pair = {(r.get("from"), r.get("to")) for r in d["relationships"] if isinstance(r, dict)}
    added = 0
    for r in rels or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        pair = (r.get("from"), r.get("to"))
        if not r.get("from") or not r.get("to"):
            continue
        if rid in by_id or pair in by_pair:
            continue
        d["relationships"].append({"id": rid or f"REL_{len(d['relationships'])+1}",
                                   "from": r.get("from"), "to": r.get("to"),
                                   "type": r.get("type", ""), "note": r.get("note", "")})
        if rid:
            by_id.add(rid)
        by_pair.add(pair)
        added += 1
    summary["relationships"] = {"added": added}
    if not dry and added:
        save_json(p, d)


# ───────────────────── 硬事实 → 事件簇.clusters[].locked_facts ─────────────────────
def apply_locked_facts(db: Path, cid: str, facts: list, summary: dict, dry: bool):
    p = db / "事件簇.json"
    ec = load_json(p, {})
    cluster = None
    for c in ec.get("clusters", []):
        c_id = c.get("cluster_id")
        if _norm_cid(c_id) == cid or str(c_id) == cid:
            cluster = c
            break
    added = 0
    if cluster is not None:
        existing = cluster.setdefault("locked_facts", [])
        seen = {(e.get("fact") if isinstance(e, dict) else e) for e in existing}
        for lf in facts or []:
            fact = lf.get("fact") if isinstance(lf, dict) else (lf if isinstance(lf, str) else None)
            if not fact or fact in seen:
                continue
            existing.append({"fact": fact, "subject": lf.get("subject", "") if isinstance(lf, dict) else "",
                             "_cluster": cid})
            seen.add(fact)
            added += 1
    summary["locked_facts"] = {"added": added}
    if not dry and added and cluster is not None:
        save_json(p, ec)


# ─────────────── throughline → 事件簇.clusters[].throughline_progress ───────────────
def apply_throughline(db: Path, cid: str, tp: dict, summary: dict, dry: bool):
    """🔴 2026-06-28 不降级收尾：本块推进的叙事线（Dramatica 4 线 OS/MC/IC/RS 的 bool）→
    事件簇.clusters[].throughline_progress（cluster 级·持久）。

    架构纠正：throughline（本块推进了哪几条叙事线）是叙事分析=梳理，由 archivist 读正文判定，
    **不再由 writer 自报 changes.factual.throughline_progress**。cluster_summary_builder 从此处
    读 cluster 级 throughline 注入每章账本记录 → cross_cluster_throughline_balance_aggregate 消费。
    advisory 遥测：缺失 = no-signal（线 DORMANT），不是 hard_gate（不强制 archivist 必产）。"""
    if not isinstance(tp, dict) or not tp:
        summary["throughline"] = {"written": False}
        return
    norm = {k: bool(v) for k, v in tp.items() if k in ("OS", "MC", "IC", "RS")}
    if not norm:
        summary["throughline"] = {"written": False}
        return
    p = db / "事件簇.json"
    ec = load_json(p, {})
    cluster = None
    for c in ec.get("clusters", []):
        c_id = c.get("cluster_id")
        if _norm_cid(c_id) == cid or str(c_id) == cid:
            cluster = c
            break
    written = False
    if cluster is not None:
        cluster["throughline_progress"] = norm
        written = True
    summary["throughline"] = {"written": written}
    if not dry and written:
        save_json(p, ec)


# ──── 🔴 2026-06-29 角色信息差(per-character belief) → character_belief_ledger.json ────
def apply_belief_updates(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """🔴 2026-06-29 角色信息差(per-character belief)·witness 检测确定性回库（零模型·幂等）。

    archivist 读整 cluster 正文 + scene_storyboard.participants 判定『本块每个 reveal 的 fact
    被哪些在场角色 witness 到』，产 archive.belief_updates=[{char_id, fact_id, content,
    learned_at_scene, source, can_speak, reader_knows, is_red_herring?, subject?}]。本步把它确定性
    append 进 character_belief_ledger.json（SymbolicToM arXiv:2306.00924：信念只沿在场传播）：

      · facts{} 登记 fact 元信息（content / first_revealed_cluster / subject）。
      · characters[char_id].known_facts 按 fact_id 去重 append（已有则更新 can_speak / source /
        reader_knows 等可变字段，绝不重复 append）。
      · unaware_of 维护（保守）：① 学到 fact → 从该 char.unaware_of 移除（已知不再 unaware·确定性）；
        ② archivist 显式标的 unaware（archive.belief_unaware·在场集外且 subject 相关的核心角色）→
        加进 unaware_of（去重）。缺席角色不 learn = 自动 false belief（根本不写进其 known_facts），
        不确定就不标 unaware。

    默认安全·向后兼容：archive 无 belief_updates（旧数据 / scene 无 participants 退化全员或跳过）
    → no-op 不报错。确定性·幂等（同 fact_id 不重复 append·re-apply 不变·全已存在时不写盘）。"""
    updates = archive.get("belief_updates") if isinstance(archive, dict) else None
    unaware_marks = archive.get("belief_unaware") if isinstance(archive, dict) else None
    if not isinstance(updates, list):
        updates = []
    if not isinstance(unaware_marks, list):
        unaware_marks = []
    if not updates and not unaware_marks:
        summary["belief"] = {"facts_registered": 0, "known_facts_added": 0,
                             "known_facts_updated": 0, "unaware_marked": 0}
        return

    p = db / "character_belief_ledger.json"
    ledger = load_json(p, {"schema_version": 1, "characters": {}, "facts": {}})
    if not isinstance(ledger, dict):
        ledger = {"schema_version": 1, "characters": {}, "facts": {}}
    ledger.setdefault("schema_version", 1)
    chars = ledger.setdefault("characters", {})
    facts = ledger.setdefault("facts", {})
    if not isinstance(chars, dict):
        chars = ledger["characters"] = {}
    if not isinstance(facts, dict):
        facts = ledger["facts"] = {}

    facts_registered = known_added = known_updated = unaware_marked = 0

    def _entry(char_id):
        e = chars.setdefault(char_id, {"known_facts": [], "unaware_of": []})
        if not isinstance(e, dict):
            e = chars[char_id] = {"known_facts": [], "unaware_of": []}
        if not isinstance(e.get("known_facts"), list):
            e["known_facts"] = []
        if not isinstance(e.get("unaware_of"), list):
            e["unaware_of"] = []
        return e

    for u in updates:
        if not isinstance(u, dict):
            continue
        char_id = u.get("char_id")
        fact_id = u.get("fact_id")
        if not char_id or not fact_id:
            continue
        content = u.get("content", "")
        # ── facts{} 元信息登记（first_revealed_cluster 只在首次登记时写·幂等不覆盖）──
        fmeta = facts.get(fact_id)
        if not isinstance(fmeta, dict):
            facts[fact_id] = {"content": content, "first_revealed_cluster": cid,
                              "subject": u.get("subject", "")}
            facts_registered += 1
        else:
            if not fmeta.get("content") and content:
                fmeta["content"] = content
            if not fmeta.get("subject") and u.get("subject"):
                fmeta["subject"] = u.get("subject")
            fmeta.setdefault("first_revealed_cluster", cid)
        # ── characters[char_id].known_facts append（按 fact_id 去重）──
        entry = _entry(char_id)
        existing = next((kf for kf in entry["known_facts"]
                         if isinstance(kf, dict) and kf.get("fact_id") == fact_id), None)
        record = {
            "fact_id": fact_id,
            "content": content,
            "learned_at_cluster": u.get("learned_at_cluster", cid),
            "learned_at_scene": u.get("learned_at_scene"),
            "source": u.get("source", "witnessed"),
            "can_speak": bool(u.get("can_speak", True)),
            "reader_knows": bool(u.get("reader_knows", False)),
            "is_red_herring": bool(u.get("is_red_herring", False)),
        }
        if existing is None:
            entry["known_facts"].append(record)
            known_added += 1
        else:
            changed = False
            for k in ("content", "learned_at_cluster", "learned_at_scene",
                      "source", "can_speak", "reader_knows", "is_red_herring"):
                if existing.get(k) != record[k]:
                    existing[k] = record[k]
                    changed = True
            if changed:
                known_updated += 1
        # 学到 fact → 从 unaware_of 移除（已知不再 unaware·确定性）
        if fact_id in entry["unaware_of"]:
            entry["unaware_of"].remove(fact_id)

    # ── 显式 unaware 标记（保守·archivist 确信在场集外且 subject 相关的核心角色才给）──
    for m in unaware_marks:
        if not isinstance(m, dict):
            continue
        char_id = m.get("char_id")
        fact_id = m.get("fact_id")
        if not char_id or not fact_id:
            continue
        entry = _entry(char_id)
        # 已 witness 到该 fact 的角色绝不标 unaware（learn 优先·矛盾保护）
        if any(isinstance(kf, dict) and kf.get("fact_id") == fact_id
               for kf in entry["known_facts"]):
            continue
        if fact_id not in entry["unaware_of"]:
            entry["unaware_of"].append(fact_id)
            unaware_marked += 1

    summary["belief"] = {"facts_registered": facts_registered,
                         "known_facts_added": known_added,
                         "known_facts_updated": known_updated,
                         "unaware_marked": unaware_marked}
    if not dry and (facts_registered or known_added or known_updated or unaware_marked):
        save_json(p, ledger)


# ──── 🔴 2026-06-29 反派轮替ledger接通producer → 反派轮替.json ────
def apply_antagonist_rotation(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """🔴 2026-06-29 反派轮替ledger接通producer（确定性·幂等·零模型）。

    archivist 读整 cluster 正文 + 人物卡，判定本块**实际出场的反派**的轮替条目，产
    archive.antagonist_rotation=[{cluster_id, antagonist_id, tier, faction, motive_type,
    power_system_tag, defeat_cluster}]。本步把它确定性写进 反派轮替.json append-only ledger
    （schema/consumer/scanner 全就绪·此前**零 producer**→scanner 永远 `note:无...跳过` 死码）。

    antagonist_rotation_scanner.py L52 读 `_数据库/反派轮替.json` 的 entries，四 advisory
    检测(defeated 后>3 cluster 空窗 / 新反派 tier 不升 / motive 同类 / power 同类)。本 producer
    落地后 scanner 才第一次有真数据可跑（shadow 观察·绝不 hard_gate·守 19 码三方一致）。

    幂等·去重：按 (cluster_id, antagonist_id) 去重 —— 同键已存在则就地更新可变字段
    (tier/faction/motive_type/power_system_tag/defeat_cluster)·不重复 append；新键 append。
    defeat 处理：archivist 击败既有反派时复用其引入 cluster_id（对齐既有引入条目键）→ 此处
    就地补 defeat_cluster·不另起重复条目。

    C03 fluid：反派是涌现产物·**非每 cluster 必有反派** —— archive 无 antagonist_rotation
    (多数 cluster 无反派轮替) → no-op 不报错、不建 ledger 文件（同 apply_belief_updates 向后兼容）。"""
    rotations = archive.get("antagonist_rotation") if isinstance(archive, dict) else None
    if not isinstance(rotations, list) or not rotations:
        summary["antagonist_rotation"] = {"appended": 0, "updated": 0}
        return

    p = db / "反派轮替.json"
    ledger = load_json(p, {"schema_version": 1, "entries": []})
    if not isinstance(ledger, dict):
        ledger = {"schema_version": 1, "entries": []}
    ledger.setdefault("schema_version", 1)
    entries = ledger.setdefault("entries", [])
    if not isinstance(entries, list):
        entries = ledger["entries"] = []

    # (cluster_id, antagonist_id) → entry（幂等去重键）
    index = {}
    for e in entries:
        if isinstance(e, dict) and e.get("antagonist_id"):
            index[(e.get("cluster_id"), e.get("antagonist_id"))] = e

    _FIELDS = ("tier", "faction", "motive_type", "power_system_tag", "defeat_cluster")
    appended = updated = 0
    for r in rotations:
        if not isinstance(r, dict):
            continue
        aid = r.get("antagonist_id")
        if not aid:
            continue
        ecid = r.get("cluster_id") or cid  # archivist 缺 cluster_id 时默认当前块
        key = (ecid, aid)
        existing = index.get(key)
        if existing is None:
            entry = {"cluster_id": ecid, "antagonist_id": aid}
            for f in _FIELDS:
                if r.get(f) is not None:
                    entry[f] = r.get(f)
            entries.append(entry)
            index[key] = entry
            appended += 1
        else:
            changed = False
            for f in _FIELDS:
                v = r.get(f)
                if v is not None and existing.get(f) != v:
                    existing[f] = v
                    changed = True
            if changed:
                updated += 1

    summary["antagonist_rotation"] = {"appended": appended, "updated": updated}
    if not dry and (appended or updated):
        save_json(p, ledger)


# ──── 🔴 2026-06-29 power_progression接通producer → 角色弧线.json ────
def apply_protagonist_power_tier(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """🔴 2026-06-29 power_progression接通producer（确定性·幂等·零模型）。

    archivist 读整 cluster 正文客观抽取**本块主角力量 tier 变化**，产 archive
    .protagonist_power_tier_update={char_id, cluster_id, tier:int, notes}（单条 dict 或 list）。
    本步把它确定性 append 进 `角色弧线.json` 的 characters[pid].protagonist_power_tier
    序列（schema/consumer/scanner 全就绪·此前 **零 producer** → power_progression_scanner
    永远命中 `note:tier 序列过短/无 角色弧线.json·跳过` 死码·同 反派轮替.json 款契约债）。

    power_progression_scanner.py L88/L108 读 `_数据库/角色弧线.json`：
      `characters`(**dict** keyed by pid) → `_protagonist_id` 取 role∈{protagonist,主角,主}
      或第一个 → `protagonist_power_tier`(list of {cluster_id, tier, notes}) 序列化判
      单调性(POWER_TIER_REGRESSION)/加速峰(ESCALATION_ACCELERATION_SPIKE)/停滞(PROGRESSION_STALL)。
    本 producer 落地后 scanner 才第一次有真 tier 序列可跑（shadow 观察·全 advisory·绝不
    hard_gate·守 19 码三方一致）。tier 是 archivist 内部叙事梯度（炼气1→筑基2…按本书梯度·
    非绝对战力·**绝不暴露给 writer**·同 v27 不暴露目标章数），仅 scanner 内部排序用。

    pid 解析：优先 update.char_id（archivist 给的主角人物卡 id）；缺失则复用 角色弧线.json
    既有 protagonist 条目 pid；再缺 → 跳过该条（不脑补·北极星②宁缺毋滥）。首次写入某 pid
    时标 role="protagonist" 让 scanner._protagonist_id 能识别。

    幂等·去重：同 pid 的 series 按 cluster_id 去重 —— 同 cluster 已存在则就地更新
    tier/notes·不重复 append；新 cluster append（保持写入即时间序·scanner 不排序）。

    C03 fluid / 默认安全：非升级流题材(scanner 自有 _GENRE_SKIP romance/mystery…)/本块主角
    力量无变化 → archivist 不产 protagonist_power_tier_update → no-op 不报错、不建 角色弧线.json
    文件（同 apply_antagonist_rotation 向后兼容）。"""
    updates = archive.get("protagonist_power_tier_update") if isinstance(archive, dict) else None
    if isinstance(updates, dict):  # 单条 dict 归一成 list
        updates = [updates]
    if not isinstance(updates, list) or not updates:
        summary["protagonist_power_tier"] = {"appended": 0, "updated": 0}
        return

    p = db / "角色弧线.json"
    arc = load_json(p, {"schema_version": 1, "characters": {}})
    if not isinstance(arc, dict):
        arc = {"schema_version": 1, "characters": {}}
    arc.setdefault("schema_version", 1)
    chars = arc.setdefault("characters", {})
    if not isinstance(chars, dict):
        chars = arc["characters"] = {}

    # 缺 char_id 时复用既有 protagonist pid（role∈markers·与 scanner._protagonist_id 对齐）
    _ROLE_MARKERS = {"protagonist", "主角", "主"}

    def _existing_pid():
        for pid, info in chars.items():
            if isinstance(info, dict) and info.get("role") in _ROLE_MARKERS:
                return pid
        return None

    def _norm_tier(t):
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            return None
        return int(t) if float(t).is_integer() else t

    appended = updated = 0
    for u in updates:
        if not isinstance(u, dict):
            continue
        tier = _norm_tier(u.get("tier"))
        if tier is None:
            continue
        pid = u.get("char_id") or u.get("protagonist_id") or _existing_pid()
        if not pid:
            continue
        ucid = u.get("cluster_id") or u.get("cluster") or cid
        notes = u.get("notes") or u.get("note") or ""
        entry = chars.setdefault(pid, {})
        if not isinstance(entry, dict):
            entry = chars[pid] = {}
        entry.setdefault("role", "protagonist")  # 让 scanner._protagonist_id 能识别
        series = entry.setdefault("protagonist_power_tier", [])
        if not isinstance(series, list):
            series = entry["protagonist_power_tier"] = []
        existing = next((s for s in series if isinstance(s, dict)
                         and (s.get("cluster_id") or s.get("cluster")) == ucid), None)
        if existing is None:
            series.append({"cluster_id": ucid, "tier": tier, "notes": notes})
            appended += 1
        else:
            changed = False
            if existing.get("tier") != tier:
                existing["tier"] = tier
                changed = True
            if notes and existing.get("notes") != notes:
                existing["notes"] = notes
                changed = True
            if changed:
                updated += 1

    summary["protagonist_power_tier"] = {"appended": appended, "updated": updated}
    if not dry and (appended or updated):
        save_json(p, arc)


# ──── 🔴 2026-06-29 actant链接通producer → cluster_actant_ledger.json ────
def apply_actant_state(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """🔴 2026-06-29 actant链接通producer（Greimas 六 actant·确定性·幂等·零模型）。

    archivist 读整 cluster 正文 + 人物卡，按 Greimas actantial model 客观判定本块六位 actant 派分
    （subject/object/sender/receiver/helper/opponent → 角色），产 archive.cluster_actant_state=
    {subject, object, sender, receiver, helper:[ids], opponent:[ids]}。本步把它确定性写进
    `cluster_actant_ledger.json`（{clusters:[{cluster_id, assignments:{pos:name}}]}）当**历史台账**——
    actant_drift_scanner（同角色 helper↔opponent 无 pivot 漂移 / 关键位空缺 / 过载）+ cast_economy_scanner
    （role_split 隐式拆分）读它做跨 cluster 比对（此前**零 producer** → 两 scanner 永远
    `note:无...跳过` 死码·像 反派轮替.json 款契约债）。

    🔴 命名空间纪律：scanner 把 ledger assignments 值当**可哈希键**用（`out[name]=pos` /
    `setdefault(name,[])`）→ 值必须是**字符串**（list 值会 TypeError 崩 scanner·北极星只读不改逻辑
    →不可触）；且 cast_economy 的 known 集 = 人物卡 name、active_cast/manifest 也走 name → ledger
    统一存**角色 display name**（archivist 给的 char_id 在此经 人物卡 id→name 解析·与 manifest 注入同源）。
    helper/opponent 多角色取**首位代表**（scanner ledger schema 为 {pos:单值}·一位一名；多 helper/
    opponent 的并存由 manifest.cluster_actant_state 的 list 表达 composite·不进 ledger——这是 scanner
    既有 schema 约束·非本 producer 引入）。

    幂等：按 cluster_id 去重——同 cluster 已存在则就地替换 assignments·不重复 append。
    C03 fluid / 默认安全·向后兼容：archive 无 cluster_actant_state（旧数据 / archivist 没标 actant）→
    no-op 不报错、不建 ledger 文件（同 apply_antagonist_rotation·无 actant 旧书零行为变化）。"""
    state = archive.get("cluster_actant_state") if isinstance(archive, dict) else None
    if not isinstance(state, dict) or not state:
        summary["actant_state"] = {"written": False}
        return

    # 人物卡 id→name 解析（apply_characters 已先跑·新角色已落 人物卡；非 id 的值原样保留）
    id2name = {}
    pc = load_json(db / "人物卡.json", {})
    for c in (pc.get("characters") or []) if isinstance(pc, dict) else []:
        if isinstance(c, dict) and c.get("id") and c.get("name"):
            id2name[c["id"]] = c["name"]

    def _name(tok):
        if not isinstance(tok, str) or not tok.strip():
            return None
        t = tok.strip()
        return id2name.get(t, t)  # id→name·非 id 则原样（已是 name 或尚未建卡）

    assignments = {}
    for pos in ("subject", "object", "sender", "receiver"):
        nm = _name(state.get(pos))
        if nm:
            assignments[pos] = nm
    for pos in ("helper", "opponent"):
        v = state.get(pos)
        if isinstance(v, list):
            names = [n for n in (_name(x) for x in v) if n]
        else:
            n = _name(v)
            names = [n] if n else []
        if names:
            # 首位代表（scanner ledger 单值·多位由 manifest list 表 composite·见 docstring 命名空间纪律）
            assignments[pos] = names[0]

    if not assignments:
        summary["actant_state"] = {"written": False}
        return

    p = db / "cluster_actant_ledger.json"
    ledger = load_json(p, {"clusters": []})
    if not isinstance(ledger, dict):
        ledger = {"clusters": []}
    clusters = ledger.setdefault("clusters", [])
    if not isinstance(clusters, list):
        clusters = ledger["clusters"] = []
    # 幂等：按 cluster_id 去重替换（同 actant_drift active 写回款·不重复 append）
    clusters = [r for r in clusters
                if not (isinstance(r, dict) and str(r.get("cluster_id") or "") == str(cid))]
    clusters.append({"cluster_id": cid, "assignments": assignments})
    ledger["clusters"] = clusters

    summary["actant_state"] = {"written": True, "positions": len(assignments)}
    if not dry:
        save_json(p, ledger)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--cluster", required=True)
    ap.add_argument("--archive", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    project = Path(args.project)
    db = project / "_数据库"
    cid = _norm_cid(args.cluster)
    key3 = str(args.cluster).replace("cluster_", "")
    archive_path = Path(args.archive) if args.archive else (db / ".wal" / f"cluster_{key3}_archive.json")

    # 🔴 2026-06-28 不降级收尾：archive 缺失 = archivist 没产出 = 硬错误（exit 2 让 plan 硬停）。
    if not archive_path.exists():
        sys.stderr.write(f"[apply_archive] FATAL: archive 不存在: {archive_path}"
                         f"（archivist 未产 archive·状态回库链断）\n")
        sys.stderr.flush()
        return 2
    archive = load_json(archive_path, {})
    # 🔴 2026-06-28 不降级收尾：每个写完的 cluster 必有出场角色——archive 缺 characters =
    # archivist 失败 = 错误（非「空 = 静默 no-op」）。exit 2 让 plan（step6 已去 advisory 前缀）硬停。
    # 道具/关系/locked_facts/throughline 可空（非每块都有新物件/关系/硬事实）·只刚性要求 characters。
    chars = archive.get("characters") if isinstance(archive, dict) else None
    if not isinstance(chars, list) or not chars:
        sys.stderr.write(
            f"[apply_archive] FATAL: {cid} archive 缺出场角色（characters 空/缺）——"
            f"archivist 失败或正文未梳理出角色，状态回库链断（每个写完的 cluster 必有角色）。"
            f"archive={archive_path}\n")
        sys.stderr.flush()
        return 2

    summary = {"cluster_id": cid}
    try:
        apply_characters(db, archive.get("characters", []), summary, args.dry_run)
        apply_items(db, archive.get("items", []), summary, args.dry_run)
        apply_relationships(db, archive.get("relationships", []), summary, args.dry_run)
        apply_locked_facts(db, cid, archive.get("locked_facts", []), summary, args.dry_run)
        apply_throughline(db, cid, archive.get("throughline_progress", {}), summary, args.dry_run)
        # 🔴 2026-06-29 角色信息差(per-character belief)·witness 回库（确定性·幂等·向后兼容）
        apply_belief_updates(db, cid, archive, summary, args.dry_run)
        # 🔴 2026-06-29 反派轮替ledger接通producer（确定性·幂等·C03 fluid 无反派合法跳过）
        apply_antagonist_rotation(db, cid, archive, summary, args.dry_run)
        # 🔴 2026-06-29 power_progression接通producer（确定性·幂等·C03 fluid 无力量变化合法跳过）
        apply_protagonist_power_tier(db, cid, archive, summary, args.dry_run)
        # 🔴 2026-06-29 actant链接通producer（确定性·幂等·C03 fluid 无 actant 合法跳过·向后兼容）
        apply_actant_state(db, cid, archive, summary, args.dry_run)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[apply_archive] FATAL: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        return 2

    c = summary.get("characters", {})
    print(f"[apply_archive] {cid} {'(dry-run) ' if args.dry_run else ''}回库: "
          f"角色卡 +{c.get('cards_added',0)}建/{c.get('cards_updated',0)}更 · 池+{c.get('pooled',0)} · "
          f"道具+{summary.get('items',{}).get('added',0)} · "
          f"关系+{summary.get('relationships',{}).get('added',0)} · "
          f"硬事实+{summary.get('locked_facts',{}).get('added',0)} · "
          f"叙事线{'已记' if summary.get('throughline',{}).get('written') else '无'} · "
          f"信念+{summary.get('belief',{}).get('known_facts_added',0)}知"
          f"/{summary.get('belief',{}).get('unaware_marked',0)}不知"
          f"/fact{summary.get('belief',{}).get('facts_registered',0)} · "
          f"反派轮替+{summary.get('antagonist_rotation',{}).get('appended',0)}"
          f"/{summary.get('antagonist_rotation',{}).get('updated',0)}更 · "
          f"主角力量tier+{summary.get('protagonist_power_tier',{}).get('appended',0)}"
          f"/{summary.get('protagonist_power_tier',{}).get('updated',0)}更 · "
          f"actant{'已记' if summary.get('actant_state',{}).get('written') else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
