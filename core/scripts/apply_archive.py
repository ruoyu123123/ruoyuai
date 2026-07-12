"""把 novel-archivist 的 cluster 归档确定性写入状态库。

归档按稳定 id 幂等写入角色、道具、关系、硬事实、叙事线、角色信念、
反派轮替、力量梯度和 actant 台账。输入合同损坏时返回 2；可选状态域为空时
记录零变更。抽取结果中的伏笔或戏剧问题终态会被剥离并写入审计记录。
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atomic_json import atomic_write_text  # noqa: E402
# 终态剥离与审计由 save_state 提供单一实现。
from save_state_common import record_terminal_contract, strip_terminal_state_payload  # noqa: E402
import cluster_lookup  # noqa: E402


def _norm_cid(key):
    cid = cluster_lookup.normalize_cluster_id(key)
    if cid is None:
        raise ValueError(f"无效 cluster_id: {key!r}")
    return cid


def load_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON 无法读取: {p}: {exc}") from exc


def save_json(p: Path, obj):
    atomic_write_text(p, json.dumps(obj, ensure_ascii=False, indent=2))


def _require_cluster(value, label: str) -> str:
    cid = _norm_cid(value)
    if value != cid:
        raise ValueError(f"{label} 必须是规范 cluster_id，收到 {value!r}")
    return cid


def _validate_archive(archive: object, cid: str) -> None:
    """在任何写盘前验证归档的 cluster-native 合同。"""
    if not isinstance(archive, dict):
        raise ValueError("archive 顶层必须是 object")
    if archive.get("cluster_id") != cid:
        raise ValueError(f"archive.cluster_id 必须等于 {cid}")
    if "chapter_range" in archive:
        raise ValueError("archive 禁止 chapter_range；章节只属于 splitter 输出")

    chars = archive.get("characters")
    if not isinstance(chars, list) or not chars:
        raise ValueError("archive.characters 必须是非空数组")
    for index, char in enumerate(chars):
        label = f"characters[{index}]"
        if not isinstance(char, dict):
            raise ValueError(f"{label} 必须是 object")
        if "first_ch" in char:
            raise ValueError(f"{label}.first_ch 已禁用；使用 first_cluster")
        if not isinstance(char.get("id"), str) or not char["id"].strip():
            raise ValueError(f"{label}.id 必须是稳定 id")
        if not isinstance(char.get("name"), str) or not char["name"].strip():
            raise ValueError(f"{label}.name 不能为空")
        if "first_cluster" in char:
            first_cluster = _require_cluster(char["first_cluster"], f"{label}.first_cluster")
            if char.get("new") is True and first_cluster != cid:
                raise ValueError(f"{label}.first_cluster 必须等于当前 {cid}")
        elif char.get("new") is True:
            raise ValueError(f"{label} 是新角色，必须提供 first_cluster")
        if "tier" in char and char["tier"] not in ("core", "emerged", "extra"):
            raise ValueError(f"{label}.tier 无效: {char['tier']!r}")
        changes = char.get("state_changes", [])
        if not isinstance(changes, list):
            raise ValueError(f"{label}.state_changes 必须是数组")
        for change_index, change in enumerate(changes):
            change_label = f"{label}.state_changes[{change_index}]"
            if not isinstance(change, dict):
                raise ValueError(f"{change_label} 必须是 object")
            if "ch" in change:
                raise ValueError(f"{change_label}.ch 已禁用；使用 changed_at_cluster")
            if not isinstance(change.get("change"), str) or not change["change"].strip():
                raise ValueError(f"{change_label}.change 不能为空")
            if _require_cluster(change.get("changed_at_cluster"),
                                f"{change_label}.changed_at_cluster") != cid:
                raise ValueError(f"{change_label} 必须属于当前 {cid}")

    for field in ("items", "relationships", "locked_facts", "belief_updates",
                  "belief_unaware", "antagonist_rotation", "warnings"):
        if field in archive and not isinstance(archive[field], list):
            raise ValueError(f"archive.{field} 必须是数组")
    for index, item in enumerate(archive.get("items", [])):
        if not isinstance(item, dict):
            raise ValueError(f"items[{index}] 必须是 object")
        if "first_ch" in item:
            raise ValueError(f"items[{index}].first_ch 已禁用；使用 first_cluster")
        if not item.get("id") or not item.get("name"):
            raise ValueError(f"items[{index}] 必须包含 id 和 name")
        if "first_cluster" not in item:
            raise ValueError(f"items[{index}] 必须包含 first_cluster")
        if _require_cluster(item["first_cluster"],
                            f"items[{index}].first_cluster") != cid:
            raise ValueError(f"items[{index}].first_cluster 必须等于当前 {cid}")
    for index, relationship in enumerate(archive.get("relationships", [])):
        if not isinstance(relationship, dict):
            raise ValueError(f"relationships[{index}] 必须是 object")
        if not all(isinstance(relationship.get(k), str) and relationship[k].strip()
                   for k in ("id", "from", "to")):
            raise ValueError(f"relationships[{index}] 必须包含 id/from/to")
    for index, fact in enumerate(archive.get("locked_facts", [])):
        if not isinstance(fact, dict) or not isinstance(fact.get("fact"), str) \
                or not fact["fact"].strip():
            raise ValueError(f"locked_facts[{index}] 必须包含非空 fact")
    if "throughline_progress" in archive:
        throughline = archive["throughline_progress"]
        if not isinstance(throughline, dict) or set(throughline) != {"OS", "MC", "IC", "RS"}:
            raise ValueError("throughline_progress 必须恰含 OS/MC/IC/RS")
        if any(not isinstance(value, bool) for value in throughline.values()):
            raise ValueError("throughline_progress 的值必须是 bool")
    for field in ("belief_updates", "belief_unaware"):
        required = ("char_id", "fact_id")
        for index, record in enumerate(archive.get(field, [])):
            if not isinstance(record, dict) or not all(record.get(k) for k in required):
                raise ValueError(f"{field}[{index}] 必须包含 char_id/fact_id")
    for index, rotation in enumerate(archive.get("antagonist_rotation", [])):
        if not isinstance(rotation, dict) or not rotation.get("antagonist_id"):
            raise ValueError(f"antagonist_rotation[{index}] 缺 antagonist_id")
        _require_cluster(rotation.get("cluster_id"),
                         f"antagonist_rotation[{index}].cluster_id")
        if "defeat_cluster" in rotation:
            _require_cluster(rotation["defeat_cluster"],
                             f"antagonist_rotation[{index}].defeat_cluster")
    power_updates = archive.get("protagonist_power_tier_update")
    if power_updates is not None:
        power_updates = [power_updates] if isinstance(power_updates, dict) else power_updates
        if not isinstance(power_updates, list):
            raise ValueError("protagonist_power_tier_update 必须是 object 或数组")
        for index, update in enumerate(power_updates):
            if not isinstance(update, dict) or not update.get("char_id"):
                raise ValueError(f"protagonist_power_tier_update[{index}] 缺 char_id")
            if _require_cluster(update.get("cluster_id"),
                                f"protagonist_power_tier_update[{index}].cluster_id") != cid:
                raise ValueError(f"protagonist_power_tier_update[{index}] 必须属于当前 {cid}")
            tier = update.get("tier")
            if isinstance(tier, bool) or not isinstance(tier, (int, float)):
                raise ValueError(f"protagonist_power_tier_update[{index}].tier 必须是数值")
    if "cluster_actant_state" in archive and not isinstance(archive["cluster_actant_state"], dict):
        raise ValueError("cluster_actant_state 必须是 object")
    for index, warning in enumerate(archive.get("warnings", [])):
        if not isinstance(warning, dict):
            raise ValueError(f"warnings[{index}] 必须是 object")
        if "ch" in warning:
            raise ValueError(f"warnings[{index}].ch 已禁用；使用 evidence_cluster")
        if "evidence_cluster" in warning:
            _require_cluster(warning["evidence_cluster"],
                             f"warnings[{index}].evidence_cluster")


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
    pool_ids = {c.get("id") for grp in ("core", "emerged", "extras")
                for c in pool[grp] if isinstance(c, dict)}

    added_cards, updated_cards, pooled = 0, 0, 0
    for ch in chars:
        cid = ch["id"]
        name = ch.get("name")
        card = by_id.get(cid)
        if card is None:
            first_cluster = ch.get("first_cluster")
            if first_cluster is None:
                raise ValueError(f"新角色 {cid} 缺 first_cluster")
            if ch.get("new") is False:
                raise ValueError(f"角色 {cid} 标记 new=false 但人物卡不存在")
            state_log = [{"changed_at_cluster": s["changed_at_cluster"],
                          "change": s["change"]}
                         for s in ch.get("state_changes", [])]
            card = {"id": cid, "name": name, "role": ch.get("role", ""),
                    "status": ch.get("status", "alive"),
                    "first_appearance_cluster": first_cluster, "state_log": state_log}
            _ra = [a for a in (ch.get("recognition_anchors") or [])
                   if isinstance(a, dict) and str(a.get("anchor") or "").strip()]
            if _ra:
                card["recognition_anchors"] = _ra
            _nf = [str(f).strip() for f in (ch.get("negative_facts") or [])
                   if isinstance(f, str) and str(f).strip()]
            if _nf:
                card["negative_facts"] = _nf
            pc["characters"].append(card)
            by_id[cid] = card
            added_cards += 1
        else:
            if card.get("name") != name:
                raise ValueError(
                    f"角色 id/name 不一致: {cid} 已登记 {card.get('name')!r}，归档给出 {name!r}")
            if ch.get("status"):
                card["status"] = ch["status"]
            if ch.get("role") and not card.get("role"):
                card["role"] = ch["role"]
            card.setdefault("state_log", [])
            if not isinstance(card["state_log"], list):
                raise ValueError(f"人物卡 {cid}.state_log 必须是数组")
            if any(isinstance(s, dict) and "ch" in s for s in card["state_log"]):
                raise ValueError(f"人物卡 {cid}.state_log 禁止字段 ch")
            seen = {(s.get("changed_at_cluster"), s.get("change"))
                    for s in card["state_log"] if isinstance(s, dict)}
            had = len(card["state_log"])
            for sc in ch.get("state_changes", []):
                key = (sc["changed_at_cluster"], sc["change"])
                if key not in seen:
                    card["state_log"].append({"changed_at_cluster": key[0], "change": key[1]})
                    seen.add(key)
            if len(card["state_log"]) > had:
                updated_cards += 1
        # 角色池分类（id 幂等）
        if cid not in pool_ids:
            tier = ch.get("tier", "extra")
            if tier not in ("core", "emerged", "extra"):
                raise ValueError(f"角色 {cid}.tier 无效: {tier!r}")
            grp = "extras" if tier == "extra" else tier
            pool[grp].append({"id": cid, "name": name,
                              "first_cluster": ch.get("first_cluster"),
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
    by_name = {it.get("name"): it.get("id") for it in d["items"] if isinstance(it, dict)}
    added = 0
    for it in items:
        iid, name = it["id"], it["name"]
        if iid in by_id:
            continue
        if name in by_name:
            raise ValueError(f"道具名 {name!r} 已绑定其他 id: {by_name[name]}")
        first_cluster = it.get("first_cluster")
        if first_cluster is None:
            raise ValueError(f"新道具 {iid} 缺 first_cluster")
        d["items"].append({"id": iid, "name": name,
                           "desc": it.get("desc", ""), "holder": it.get("holder", ""),
                           "first_appearance_cluster": first_cluster})
        by_id.add(iid)
        by_name[name] = iid
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
    for r in rels:
        if not isinstance(r, dict):
            raise ValueError("relationships 条目必须是 object")
        rid = r.get("id")
        pair = (r.get("from"), r.get("to"))
        if not rid or not r.get("from") or not r.get("to"):
            raise ValueError("relationship 必须包含 id/from/to")
        if rid in by_id or pair in by_pair:
            continue
        d["relationships"].append({"id": rid,
                                   "from": r.get("from"), "to": r.get("to"),
                                   "type": r.get("type", ""), "note": r.get("note", "")})
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
    if cluster is None:
        raise ValueError(f"事件簇.json 不含 {cid}")
    added = 0
    existing = cluster.setdefault("locked_facts", [])
    if not isinstance(existing, list):
        raise ValueError(f"{cid}.locked_facts 必须是数组")
    seen = {e.get("fact") for e in existing if isinstance(e, dict)}
    for lf in facts:
        if not isinstance(lf, dict) or not isinstance(lf.get("fact"), str) or not lf["fact"].strip():
            raise ValueError("locked_facts 条目必须包含非空 fact")
        fact = lf["fact"]
        if fact in seen:
            continue
        existing.append({"fact": fact, "subject": lf.get("subject", ""),
                         "source_cluster": cid})
        seen.add(fact)
        added += 1
    summary["locked_facts"] = {"added": added}
    if not dry and added:
        save_json(p, ec)


# ─────────────── throughline → 事件簇.clusters[].throughline_progress ───────────────
def apply_throughline(db: Path, cid: str, tp: dict, summary: dict, dry: bool):
    """把本 cluster 的 OS/MC/IC/RS 推进结果写入事件簇。"""
    if tp is None:
        summary["throughline"] = {"written": False}
        return
    if not isinstance(tp, dict) or set(tp) != {"OS", "MC", "IC", "RS"}:
        raise ValueError("throughline_progress 必须恰含 OS/MC/IC/RS")
    if any(not isinstance(value, bool) for value in tp.values()):
        raise ValueError("throughline_progress 的值必须是 bool")
    norm = dict(tp)
    p = db / "事件簇.json"
    ec = load_json(p, {})
    cluster = None
    for c in ec.get("clusters", []):
        c_id = c.get("cluster_id")
        if _norm_cid(c_id) == cid or str(c_id) == cid:
            cluster = c
            break
    if cluster is None:
        raise ValueError(f"事件簇.json 不含 {cid}")
    cluster["throughline_progress"] = norm
    summary["throughline"] = {"written": True}
    if not dry:
        save_json(p, ec)


# ─────────────────────── 角色信念 ───────────────────────
def apply_belief_updates(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """按 fact_id 幂等维护角色已知事实和显式未知事实。"""
    updates = archive.get("belief_updates") if isinstance(archive, dict) else None
    unaware_marks = archive.get("belief_unaware") if isinstance(archive, dict) else None
    updates = [] if updates is None else updates
    unaware_marks = [] if unaware_marks is None else unaware_marks
    if not updates and not unaware_marks:
        summary["belief"] = {"facts_registered": 0, "known_facts_added": 0,
                             "known_facts_updated": 0, "unaware_marked": 0}
        return

    p = db / "character_belief_ledger.json"
    ledger = load_json(p, {"schema_version": 1, "characters": {}, "facts": {}})
    if not isinstance(ledger, dict):
        raise ValueError("character_belief_ledger.json 顶层必须是 object")
    ledger.setdefault("schema_version", 1)
    chars = ledger.setdefault("characters", {})
    facts = ledger.setdefault("facts", {})
    if not isinstance(chars, dict) or not isinstance(facts, dict):
        raise ValueError("character_belief_ledger.json characters/facts 必须是 object")

    facts_registered = known_added = known_updated = unaware_marked = 0

    def _entry(char_id):
        e = chars.setdefault(char_id, {"known_facts": [], "unaware_of": []})
        if not isinstance(e, dict):
            raise ValueError(f"belief 角色 {char_id} 条目必须是 object")
        if not isinstance(e.get("known_facts"), list):
            raise ValueError(f"belief 角色 {char_id}.known_facts 必须是数组")
        if not isinstance(e.get("unaware_of"), list):
            raise ValueError(f"belief 角色 {char_id}.unaware_of 必须是数组")
        return e

    for u in updates:
        if not isinstance(u, dict):
            raise ValueError("belief_updates 条目必须是 object")
        char_id = u.get("char_id")
        fact_id = u.get("fact_id")
        if not char_id or not fact_id:
            raise ValueError("belief_updates 条目必须包含 char_id/fact_id")
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
            raise ValueError("belief_unaware 条目必须是 object")
        char_id = m.get("char_id")
        fact_id = m.get("fact_id")
        if not char_id or not fact_id:
            raise ValueError("belief_unaware 条目必须包含 char_id/fact_id")
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


# ─────────────────────── 反派轮替 ───────────────────────
def apply_antagonist_rotation(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """按引入 cluster 和角色 id 幂等维护反派轮替台账。"""
    rotations = archive.get("antagonist_rotation") if isinstance(archive, dict) else None
    if not isinstance(rotations, list) or not rotations:
        summary["antagonist_rotation"] = {"appended": 0, "updated": 0}
        return

    p = db / "反派轮替.json"
    ledger = load_json(p, {"schema_version": 1, "entries": []})
    if not isinstance(ledger, dict):
        raise ValueError("反派轮替.json 顶层必须是 object")
    ledger.setdefault("schema_version", 1)
    entries = ledger.setdefault("entries", [])
    if not isinstance(entries, list):
        raise ValueError("反派轮替.json.entries 必须是数组")

    # (cluster_id, antagonist_id) → entry（幂等去重键）
    index = {}
    for e in entries:
        if isinstance(e, dict) and e.get("antagonist_id"):
            index[(e.get("cluster_id"), e.get("antagonist_id"))] = e

    _FIELDS = ("tier", "faction", "motive_type", "power_system_tag", "defeat_cluster")
    appended = updated = 0
    for r in rotations:
        if not isinstance(r, dict):
            raise ValueError("antagonist_rotation 条目必须是 object")
        aid = r.get("antagonist_id")
        if not aid:
            raise ValueError("antagonist_rotation 缺 antagonist_id")
        ecid = _require_cluster(r.get("cluster_id"), "antagonist_rotation.cluster_id")
        if "defeat_cluster" in r:
            _require_cluster(r["defeat_cluster"], "antagonist_rotation.defeat_cluster")
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


# ─────────────────────── 主角力量梯度 ───────────────────────
def apply_protagonist_power_tier(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """按主角 id 和 cluster_id 幂等维护力量梯度。"""
    updates = archive.get("protagonist_power_tier_update") if isinstance(archive, dict) else None
    if isinstance(updates, dict):  # 单条 dict 归一成 list
        updates = [updates]
    if not isinstance(updates, list) or not updates:
        summary["protagonist_power_tier"] = {"appended": 0, "updated": 0}
        return

    p = db / "角色弧线.json"
    arc = load_json(p, {"schema_version": 1, "characters": {}})
    if not isinstance(arc, dict):
        raise ValueError("角色弧线.json 顶层必须是 object")
    arc.setdefault("schema_version", 1)
    chars = arc.setdefault("characters", {})
    if not isinstance(chars, dict):
        raise ValueError("角色弧线.json.characters 必须是 object")

    def _norm_tier(t):
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            return None
        return int(t) if float(t).is_integer() else t

    appended = updated = 0
    for u in updates:
        if not isinstance(u, dict):
            raise ValueError("protagonist_power_tier_update 条目必须是 object")
        tier = _norm_tier(u.get("tier"))
        if tier is None:
            raise ValueError("protagonist_power_tier_update.tier 必须是数值")
        pid = u.get("char_id")
        if not pid:
            raise ValueError("protagonist_power_tier_update 缺 char_id")
        ucid = _require_cluster(u.get("cluster_id"),
                                "protagonist_power_tier_update.cluster_id")
        if ucid != cid:
            raise ValueError("力量变化必须属于当前 cluster")
        notes = u.get("notes", "")
        entry = chars.setdefault(pid, {})
        if not isinstance(entry, dict):
            entry = chars[pid] = {}
        entry.setdefault("role", "protagonist")  # 让 scanner._protagonist_id 能识别
        series = entry.setdefault("protagonist_power_tier", [])
        if not isinstance(series, list):
            series = entry["protagonist_power_tier"] = []
        existing = next((s for s in series if isinstance(s, dict)
                         and s.get("cluster_id") == ucid), None)
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


# ─────────────────────── Actant 台账 ───────────────────────
def apply_actant_state(db: Path, cid: str, archive: dict, summary: dict, dry: bool):
    """把本 cluster 的 actant 角色 id 解析为显示名并幂等落账。"""
    state = archive.get("cluster_actant_state") if isinstance(archive, dict) else None
    if not isinstance(state, dict) or not state:
        summary["actant_state"] = {"written": False}
        return

    id2name = {}
    pc = load_json(db / "人物卡.json", {})
    for c in (pc.get("characters") or []) if isinstance(pc, dict) else []:
        if isinstance(c, dict) and c.get("id") and c.get("name"):
            id2name[c["id"]] = c["name"]

    def _name(tok):
        if not isinstance(tok, str) or not tok.strip():
            return None
        t = tok.strip()
        return id2name.get(t, t)

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
            assignments[pos] = names[0]

    if not assignments:
        summary["actant_state"] = {"written": False}
        return

    p = db / "cluster_actant_ledger.json"
    ledger = load_json(p, {"clusters": []})
    if not isinstance(ledger, dict):
        raise ValueError("cluster_actant_ledger.json 顶层必须是 object")
    clusters = ledger.setdefault("clusters", [])
    if not isinstance(clusters, list):
        raise ValueError("cluster_actant_ledger.json.clusters 必须是数组")
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
    try:
        cid = _norm_cid(args.cluster)
    except ValueError as exc:
        print(f"[apply_archive] FATAL: {exc}", file=sys.stderr)
        return 2
    key = cid.removeprefix("cluster_")
    archive_path = Path(args.archive) if args.archive else (
        db / ".wal" / f"cluster_{key}_archive.json")

    if not archive_path.exists():
        sys.stderr.write(f"[apply_archive] FATAL: archive 不存在: {archive_path}"
                         f"（archivist 未产 archive·状态回库链断）\n")
        sys.stderr.flush()
        return 2
    try:
        archive = load_json(archive_path, None)
        _validate_archive(archive, cid)
        ts_stripped, ts_details = strip_terminal_state_payload(archive)
        if ts_stripped:
            print(
                f"[apply_archive] WARNING: {cid} 剥离未经验证的终态声明 "
                f"terminal_state_stripped={ts_stripped}",
                file=sys.stderr,
            )
            if not args.dry_run:
                record_terminal_contract(
                    db, cid, "archive_strip",
                    {"terminal_state_stripped": ts_stripped, "details": ts_details},
                )

        summary = {"cluster_id": cid, "terminal_state_stripped": ts_stripped}
        apply_characters(db, archive.get("characters", []), summary, args.dry_run)
        apply_items(db, archive.get("items", []), summary, args.dry_run)
        apply_relationships(db, archive.get("relationships", []), summary, args.dry_run)
        apply_locked_facts(db, cid, archive.get("locked_facts", []), summary, args.dry_run)
        apply_throughline(db, cid, archive.get("throughline_progress"), summary, args.dry_run)
        apply_belief_updates(db, cid, archive, summary, args.dry_run)
        apply_antagonist_rotation(db, cid, archive, summary, args.dry_run)
        apply_protagonist_power_tier(db, cid, archive, summary, args.dry_run)
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
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
