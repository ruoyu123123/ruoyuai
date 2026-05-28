#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_data_model_v2.py — 数据模型 v1 → v2 cluster-centric 迁移

v2 cluster 化方案 Phase 2（2026-05-28）· 设计原则：
  · cluster_id = 数据模型主体单位
  · scene_index = cluster 内段位置（0-based）
  · chapter 单位仅保留在物理产物文件名（第NNN章.txt）

迁移范围（12 个 JSON 文件）：
  · 进度.json: chapter_plan → cluster_blueprint
  · 章纲摘要.json → 故事块摘要.json (git mv + schema 改)
  · 伏笔表.json: setup_ch → setup_cluster + setup_scene_index
  · 人物卡.json: 8 处 _ch 字段
  · 道具.json / 关系.json / 时间线.json / 事件簇.json / beat_map.json

用法：
  python migrate_data_model_v2.py <project_path>
  python migrate_data_model_v2.py <project_path> --dry-run  # 只看不改
  python migrate_data_model_v2.py <project_path> --rollback # 用 backup 还原

自动 backup 到 <project>/_数据库.bak.YYYYMMDD_HHMMSS/
迁移成功标记 <project>/_数据库/.migration_v2_done.flag
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 读取 {p.name} 失败: {e}", file=sys.stderr)
        return None


def save(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def backup_db(project: Path) -> Path:
    src = project / "_数据库"
    if not src.exists():
        raise FileNotFoundError(f"_数据库 不存在: {src}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = project / f"_数据库.bak.{ts}"
    shutil.copytree(src, dst, dirs_exist_ok=False)
    print(f"[backup] {dst.name}")
    return dst


def migrate_progress(project: Path, dry_run: bool):
    """进度.json: chapter_plan → cluster_blueprint, total_chapters → total_clusters"""
    p = project / "_数据库" / "进度.json"
    d = load(p)
    if not d:
        return None
    changes = []
    if "total_chapters" in d:
        d["total_chapters_estimate"] = d.pop("total_chapters")
        changes.append("total_chapters → total_chapters_estimate")
    if "chapter_plan" in d and isinstance(d["chapter_plan"], list):
        # 按 cluster 分组
        cluster_blueprint = {}
        for cp in d["chapter_plan"]:
            cluster_key = cp.get("cluster", "cluster_001")
            if cluster_key not in cluster_blueprint:
                cluster_blueprint[cluster_key] = {"scene_storyboard": []}
            # 把 ch 字段改为 scene_index（cluster 内 0-based）
            cp_new = dict(cp)
            ch_in_cluster = len(cluster_blueprint[cluster_key]["scene_storyboard"])
            cp_new["scene_index"] = ch_in_cluster
            cp_new["_legacy_ch"] = cp_new.pop("ch", None)
            cluster_blueprint[cluster_key]["scene_storyboard"].append(cp_new)
        d["cluster_blueprint"] = cluster_blueprint
        d["chapter_plan"] = d.pop("chapter_plan")  # 保留 legacy
        d["_chapter_plan_DEPRECATED_v27"] = "由 cluster_blueprint 取代 · 见 v2_cluster_centric.md"
        changes.append(f"chapter_plan → cluster_blueprint ({len(cluster_blueprint)} 组)")
    if "words_per_chapter" in d:
        d.pop("words_per_chapter")
        changes.append("words_per_chapter 删除（v27 freestyle 不锁）")
    if not dry_run and changes:
        save(p, d)
    return changes


def migrate_chapter_summary(project: Path, dry_run: bool):
    """章纲摘要.json → 故事块摘要.json"""
    old = project / "_数据库" / "章纲摘要.json"
    new = project / "_数据库" / "故事块摘要.json"
    if not old.exists() and not new.exists():
        return None
    if new.exists() and not old.exists():
        return ["already renamed"]
    d = load(old) or {}
    if "chapters" in d:
        d["clusters"] = d.pop("chapters")
    if not dry_run:
        save(new, d)
        old.unlink(missing_ok=True)
    return [f"章纲摘要.json → 故事块摘要.json (schema clusters[])"]


def migrate_foreshadowing(project: Path, dry_run: bool):
    """伏笔表.json: setup_ch → setup_cluster + setup_scene_index, established_ch → established_cluster"""
    p = project / "_数据库" / "伏笔表.json"
    d = load(p)
    if not d:
        return None
    changes = []
    for fs in d.get("promises", []):
        if "setup_ch" in fs and "setup_cluster" not in fs:
            ch = fs.pop("setup_ch")
            fs["setup_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
            fs["_legacy_setup_ch"] = ch
        if "due_by" in fs and isinstance(fs["due_by"], int):
            fs["due_by_cluster"] = f"cluster_{fs['due_by']:03d}" if fs["due_by"] < 100 else None
    for sec in d.get("secrets", []):
        if "established_ch" in sec and "established_cluster" not in sec:
            ch = sec.pop("established_ch")
            sec["established_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
            sec["_legacy_established_ch"] = ch
        if "reveal_at_ch" in sec and "reveal_at_cluster" not in sec:
            ch = sec.pop("reveal_at_ch")
            sec["reveal_at_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else None
            sec["_legacy_reveal_at_ch"] = ch
    changes.append(f"伏笔表 promises/secrets · _ch → _cluster")
    if not dry_run:
        save(p, d)
    return changes


def migrate_characters(project: Path, dry_run: bool):
    """人物卡.json: first_appear_ch / since_ch / learn_at_ch / actions.ch_range / growth_arc.ch"""
    p = project / "_数据库" / "人物卡.json"
    d = load(p)
    if not d:
        return None
    changes = []
    for c in d.get("characters", []):
        # first_appear
        if "first_appear_ch" in c and not isinstance(c.get("first_appear_ch"), str):
            ch = c.pop("first_appear_ch")
            c["first_appear_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
            c["_legacy_first_appear_ch"] = ch
        # locked_facts
        for lf in c.get("locked_facts", []):
            if isinstance(lf, dict) and "since_ch" in lf:
                ch = lf.pop("since_ch")
                lf["since_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
        # knowledge.will_learn
        kn = c.get("knowledge", {})
        for wl in kn.get("will_learn", []) if isinstance(kn, dict) else []:
            if isinstance(wl, dict) and "learn_at_ch" in wl:
                ch = wl.pop("learn_at_ch")
                wl["learn_at_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else None
        # knowledge_state
        for ks in kn.get("knowledge_state", []) if isinstance(kn, dict) else []:
            if isinstance(ks, dict) and "since_ch" in ks:
                ch = ks.pop("since_ch")
                ks["since_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else None
        # offscreen.actions
        off = c.get("offscreen", {})
        for act in off.get("actions", []) if isinstance(off, dict) else []:
            if isinstance(act, dict) and "ch_range" in act:
                rng = act.pop("ch_range")
                if isinstance(rng, list) and len(rng) == 2:
                    act["cluster_range"] = [f"cluster_{rng[0]:03d}", f"cluster_{rng[1]:03d}"]
        # growth_arc
        for ga in c.get("growth_arc", []):
            if isinstance(ga, dict) and "ch" in ga:
                ch = ga.pop("ch")
                ga["cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else None
    changes.append(f"人物卡 · 8 处 _ch 字段迁移到 _cluster")
    if not dry_run:
        save(p, d)
    return changes


def migrate_items(project: Path, dry_run: bool):
    """道具.json: items[].obtained_ch → obtained_cluster"""
    p = project / "_数据库" / "道具.json"
    d = load(p)
    if not d:
        return None
    for it in d.get("items", []):
        if "obtained_ch" in it:
            ch = it.pop("obtained_ch")
            it["obtained_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
    if not dry_run:
        save(p, d)
    return ["道具 · obtained_ch → obtained_cluster"]


def migrate_relations(project: Path, dry_run: bool):
    """关系.json: relationships[].since_ch → since_cluster"""
    p = project / "_数据库" / "关系.json"
    d = load(p)
    if not d:
        return None
    for r in d.get("relationships", []):
        if "since_ch" in r:
            ch = r.pop("since_ch")
            r["since_cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
    if not dry_run:
        save(p, d)
    return ["关系 · since_ch → since_cluster"]


def migrate_timeline(project: Path, dry_run: bool):
    """时间线.json: current_time.chapter → cluster, time_per_chapter → time_per_cluster"""
    p = project / "_数据库" / "时间线.json"
    d = load(p)
    if not d:
        return None
    changes = []
    ct = d.get("current_time", {})
    if "chapter" in ct:
        ch = ct.pop("chapter")
        ct["cluster"] = f"cluster_{ch:03d}" if isinstance(ch, int) else "cluster_001"
        ct["chapter_in_cluster"] = 0
        changes.append("current_time.chapter → cluster + chapter_in_cluster")
    if "time_per_chapter" in d:
        d["time_per_cluster"] = d.pop("time_per_chapter")
        changes.append("time_per_chapter → time_per_cluster")
    if not dry_run:
        save(p, d)
    return changes


def migrate_event_cluster(project: Path, dry_run: bool):
    """事件簇.json: scene_storyboard[].ch → scene_index"""
    p = project / "_数据库" / "事件簇.json"
    d = load(p)
    if not d:
        return None
    for cl in d.get("clusters", []):
        sb = cl.get("scene_storyboard", [])
        for i, scene in enumerate(sb):
            if isinstance(scene, dict) and "ch" in scene:
                scene["_legacy_ch"] = scene.pop("ch")
                scene["scene_index"] = i
    if not dry_run:
        save(p, d)
    return ["事件簇 · scene_storyboard[].ch → scene_index"]


def migrate_beat_map(project: Path, dry_run: bool):
    """beat_map.json: cluster_beats[cluster_001][].ch → scene_index"""
    p = project / "_数据库" / "beat_map.json"
    d = load(p)
    if not d:
        return None
    for cluster_key, beats in d.get("cluster_beats", {}).items():
        if isinstance(beats, list):
            for i, beat in enumerate(beats):
                if isinstance(beat, dict) and "ch" in beat:
                    beat["_legacy_ch"] = beat.pop("ch")
                    beat["scene_index"] = i
    if not dry_run:
        save(p, d)
    return ["beat_map · cluster_beats[].ch → scene_index"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project")
    parser.add_argument("--dry-run", action="store_true", help="只看不改 · 不动文件")
    parser.add_argument("--rollback", help="从指定 backup 目录还原（如 _数据库.bak.20260528_120000）")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"[FATAL] 项目不存在: {project}", file=sys.stderr)
        sys.exit(2)

    flag = project / "_数据库" / ".migration_v2_done.flag"

    if args.rollback:
        bak = project / args.rollback
        if not bak.is_dir():
            print(f"[FATAL] backup 不存在: {bak}", file=sys.stderr)
            sys.exit(2)
        src = project / "_数据库"
        if src.exists():
            shutil.rmtree(src)
        shutil.copytree(bak, src)
        flag.unlink(missing_ok=True)
        print(f"[ROLLBACK OK] 从 {bak.name} 还原")
        return

    if flag.exists():
        print(f"[SKIP] migration v2 已完成（{flag.read_text(encoding='utf-8').strip()}）")
        return

    print(f"[migrate_v2] 项目: {project.name}")
    print(f"[migrate_v2] dry_run = {args.dry_run}")
    print()

    if not args.dry_run:
        bak = backup_db(project)
        print()

    all_changes = []
    for fn, name in [
        (migrate_progress, "进度.json"),
        (migrate_chapter_summary, "章纲摘要.json → 故事块摘要.json"),
        (migrate_foreshadowing, "伏笔表.json"),
        (migrate_characters, "人物卡.json"),
        (migrate_items, "道具.json"),
        (migrate_relations, "关系.json"),
        (migrate_timeline, "时间线.json"),
        (migrate_event_cluster, "事件簇.json"),
        (migrate_beat_map, "beat_map.json"),
    ]:
        try:
            changes = fn(project, args.dry_run)
            if changes:
                print(f"[{name}]")
                for c in changes:
                    print(f"  · {c}")
                all_changes.extend([(name, c) for c in changes])
            else:
                print(f"[{name}] no changes")
        except Exception as e:
            print(f"[ERROR · {name}] {e}", file=sys.stderr)

    if not args.dry_run:
        flag.write_text(f"migrated at {datetime.now().isoformat()} · {len(all_changes)} changes\n", encoding="utf-8")
        print()
        print(f"[OK · v2 migration done] 共 {len(all_changes)} 处修改")
    else:
        print()
        print(f"[OK · dry-run] 共 {len(all_changes)} 处修改（未保存）")


if __name__ == "__main__":
    main()
