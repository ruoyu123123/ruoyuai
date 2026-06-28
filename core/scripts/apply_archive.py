"""apply_archive.py — 把 novel-archivist 梳理产物 archive.json 确定性回库（无模型）。

🔴 2026-06-28 架构纠正：配置的写作模型只产正文、不自报"改了什么"；Claude(archivist agent)
读正文分辨出新增/变更的角色/道具/关系/硬事实，产 cluster_<key>_archive.json；本脚本把它
确定性写进 人物卡 / 角色池 / 道具 / 关系 / 事件簇.clusters[].locked_facts。

权威源 = archive（Claude 读正文）·不再读 writer 的 changes.factual 自报。

幂等：全部按 id 去重（角色 C_*、道具 I_*、关系 REL_*）·re-apply / 多次跑不重复建、不后移。

用法:
    python apply_archive.py <项目路径> --cluster <key>
        [--archive <archive.json 路径，默认 _数据库/.wal/cluster_<key>_archive.json>]
        [--dry-run]

退出码: 0 成功 / 1 archive 缺失或空 / 2 致命错误
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

    if not archive_path.exists():
        sys.stderr.write(f"[apply_archive] archive 不存在: {archive_path}\n")
        sys.stderr.flush()
        return 1
    archive = load_json(archive_path, {})
    if not isinstance(archive, dict) or not any(
            archive.get(k) for k in ("characters", "items", "relationships", "locked_facts")):
        print(f"[apply_archive] {cid} archive 为空 → 无回库")
        return 1

    summary = {"cluster_id": cid}
    try:
        apply_characters(db, archive.get("characters", []), summary, args.dry_run)
        apply_items(db, archive.get("items", []), summary, args.dry_run)
        apply_relationships(db, archive.get("relationships", []), summary, args.dry_run)
        apply_locked_facts(db, cid, archive.get("locked_facts", []), summary, args.dry_run)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[apply_archive] FATAL: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        return 2

    c = summary.get("characters", {})
    print(f"[apply_archive] {cid} {'(dry-run) ' if args.dry_run else ''}回库: "
          f"角色卡 +{c.get('cards_added',0)}建/{c.get('cards_updated',0)}更 · 池+{c.get('pooled',0)} · "
          f"道具+{summary.get('items',{}).get('added',0)} · "
          f"关系+{summary.get('relationships',{}).get('added',0)} · "
          f"硬事实+{summary.get('locked_facts',{}).get('added',0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
