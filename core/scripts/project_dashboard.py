"""project_dashboard.py — 项目全景仪表盘（v21 UX7）

用户随时跑：`python core/scripts/project_dashboard.py <project>`

显示：
- 当前进度（vol / ch / beat / 距离 Midpoint/All Is Lost 还多远）
- 写作总览（已写 N 章 / 总目标 / 平均字数 / 字数趋势）
- 角色弧光（每核心角色当前 stage / 距离 truth_realized 多远）
- Stress 状态（主角当前 stress / 距离 mental_break 多远）
- Active Clocks（urgent / approaching / normal）
- Active Fate Events（priority 排序）
- World Factions 数值
- Throughlines 4 线最近 N 章覆盖率
- 用户偏好（如已跑 /wizard）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测），裸 .items() 会崩。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_lookup  # blueprint list 归一守卫
except Exception:
    cluster_lookup = None


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def format_progress_bar(current: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    pct = min(1.0, current / total)
    fill = int(width * pct)
    return f"[{'█' * fill}{'░' * (width - fill)}] {current}/{total} ({pct*100:.1f}%)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    args = ap.parse_args()

    project_root = Path(args.project).resolve()
    db = project_root / "_数据库"
    if not db.exists():
        print(f"[ERROR] 项目不存在或无 _数据库: {project_root}", file=sys.stderr)
        sys.exit(2)

    # 章节状态
    chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                      for d in (project_root / "章节").glob("第*章")
                      if re.match(r"第(\d+)章", d.name))
    current_ch = chapters[-1] if chapters else 0

    # 用户偏好
    prefs = load_json(db / "用户偏好.json", {})
    pb = prefs.get("project_basics", {}) or {}
    total_chapters = pb.get("total_chapters", 0)

    # 进度
    progress = load_json(db / "进度.json", {})
    volumes = progress.get("volumes", []) or []
    current_vol = None
    for v in volumes:
        crange = v.get("chapter_range", [0, 0])
        if crange[0] <= current_ch <= crange[1]:
            current_vol = v
            break

    # beat_map
    bm = load_json(db / "beat_map.json", {})
    bm_chs = bm.get("chapters_beat", {}) or {}
    current_beat = bm_chs.get(str(current_ch)) or bm_chs.get(current_ch) or "未标"
    # 找下个关键 beat
    next_key_beat_ch = None
    next_key_beat_name = None
    key_beats = ["Midpoint", "All Is Lost", "Break into Three", "Finale", "Final Image"]
    for c_str, beat in sorted(bm_chs.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
        c = int(c_str) if str(c_str).isdigit() else 0
        if c <= current_ch:
            continue
        for kb in key_beats:
            if kb in str(beat):
                next_key_beat_ch = c
                next_key_beat_name = beat
                break
        if next_key_beat_ch:
            break

    # ============ 输出 ============
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║                  若渝AI · 项目进度仪表盘                              ║")
    print("╠══════════════════════════════════════════════════════════════════════╣")
    print(f"║ 项目: {project_root.name:<60s} ║")
    print("╠══════════════════════════════════════════════════════════════════════╣")

    # 总进度
    print(f"║ 📖 总进度: {format_progress_bar(current_ch, total_chapters, 30):<58s}║" if total_chapters else f"║ 📖 已写 {current_ch} 章（未配置总章数）{'':<35s}║")
    if current_vol:
        vol_title = current_vol.get('title', '?')
        vol_arc = current_vol.get('volume_arc', '')[:50]
        print(f"║ 📑 当前卷: 卷{current_vol.get('vol')} 《{vol_title}》".ljust(72) + "║")
        if vol_arc:
            print(f"║    弧线: {vol_arc[:60]:<60s}║")
    print(f"║ 🎬 当前 beat: {current_beat:<55s}║")
    if next_key_beat_name:
        gap = next_key_beat_ch - current_ch
        print(f"║ ⏭️  下个关键 beat: ch{next_key_beat_ch} 「{next_key_beat_name}」 （还有 {gap} 章）".ljust(72) + "║")

    # 字数
    print("╠══════════════════════════════════════════════════════════════════════╣")
    print(f"║ ✍️  写作统计                                                          ║")
    if chapters:
        recent = chapters[-5:]
        word_counts = []
        for ch in recent:
            text_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
            if text_path.exists():
                text = text_path.read_text(encoding="utf-8")
                cn = len(re.findall(r"[一-鿿]", text))
                word_counts.append((ch, cn))
        if word_counts:
            avg = sum(c for _, c in word_counts) / len(word_counts)
            target = pb.get("target_words_per_chapter", 3000)
            print(f"║   近 {len(word_counts)} 章平均字数: {int(avg)} (目标 {target})".ljust(72) + "║")
            for ch, cn in word_counts[-3:]:
                bar_fill = int(min(1.0, cn / target) * 20)
                print(f"║   ch{ch:03d}: [{'█'*bar_fill}{'░'*(20-bar_fill)}] {cn} 字".ljust(72) + "║")

    # 角色弧光
    arc = load_json(db / "character_arc_state.json", {})
    if arc.get("characters"):
        print("╠══════════════════════════════════════════════════════════════════════╣")
        print(f"║ 🎭 角色弧光                                                           ║")
        for cname, cdata in arc.get("characters", {}).items():
            stage = cdata.get("current_stage_at_ch", "?")
            print(f"║   {cname}: stage={stage}".ljust(72) + "║")

    # Stress
    stress = load_json(db / "主角压力档.json", {})
    if stress:
        level = stress.get("stress_level", 0)
        threshold = stress.get("stress_threshold_break", 8)
        max_v = stress.get("stress_max", 10)
        bar = int(min(1.0, level / max_v) * 20)
        status = "⚠️ HIGH" if level >= threshold * 0.75 else "OK"
        print("╠══════════════════════════════════════════════════════════════════════╣")
        print(f"║ 💢 主角 Stress: [{'█'*bar}{'░'*(20-bar)}] {level}/{max_v} (break阈={threshold}, {status})".ljust(72) + "║")

    # Clocks
    clocks = load_json(db / "时钟表.json", {})
    if clocks:
        active = [c for c in clocks.get("clocks", []) if c.get("status") == "active"]
        urgent = [c for c in active if c.get("max", 99) - c.get("ticks", 0) <= 2]
        print("╠══════════════════════════════════════════════════════════════════════╣")
        print(f"║ ⏱  Active Clocks: {len(active)} 总 / {len(urgent)} urgent".ljust(72) + "║")
        for c in active[:5]:
            ticks = c.get("ticks", 0)
            mx = c.get("max", 99)
            rem = mx - ticks
            label = c.get("label", "?")
            mark = "🔥" if rem <= 2 else ""
            print(f"║   {c.get('clock_id')} {label}: {ticks}/{mx} {mark}".ljust(72) + "║")

    # Fate Events
    fate = load_json(db / "大势卡.json", {})
    if fate.get("major_events"):
        scheduled = [e for e in fate["major_events"] if e.get("status") == "scheduled"][:5]
        completed = sum(1 for e in fate["major_events"] if e.get("status") == "completed")
        total = len(fate["major_events"])
        print("╠══════════════════════════════════════════════════════════════════════╣")
        print(f"║ 🎯 Fate Events: {completed}/{total} 完成 (剩余 {len(scheduled)} active)".ljust(72) + "║")
        for e in scheduled[:5]:
            print(f"║   {e.get('id')} {e.get('title', '')[:40]}".ljust(72) + "║")

    # World factions
    world = load_json(db / "世界状态.json", {})
    if world.get("factions_state"):
        print("╠══════════════════════════════════════════════════════════════════════╣")
        print(f"║ 🏛  Factions 数值                                                     ║")
        for fname, fdata in list(world.get("factions_state", {}).items())[:5]:
            p = fdata.get("power", 0)
            s = fdata.get("stability", 0)
            w = fdata.get("wealth", 0)
            print(f"║   {fname[:14]:<14s} P:{p:3d} S:{s:3d} W:{w:3d}".ljust(72) + "║")

    # v23 ECAS Event Clusters
    clusters_data = load_json(db / "事件簇.json", {})
    if clusters_data.get("clusters"):
        clusters = clusters_data["clusters"]
        ns = prefs.get("narrative_style", {}) or {}
        ec = prefs.get("ecas_config", {}) or {}
        print("╠══════════════════════════════════════════════════════════════════════╣")
        ecas_status = "启用" if ec.get("ecas_enabled") else "关闭"
        pov_str = ns.get("pov", "未配置")
        print(f"║ 🎬 ECAS v23 事件簇 ({ecas_status} / POV: {pov_str})".ljust(72) + "║")
        # 统计 cluster 状态
        completed_clusters = [c for c in clusters if c.get("status") == "completed"]
        in_progress = [c for c in clusters if c.get("status") == "in_progress"]
        pending = [c for c in clusters if c.get("status") == "pending"]
        print(f"║   总: {len(clusters)} / 完成 {len(completed_clusters)} / 进行中 {len(in_progress)} / 待启 {len(pending)}".ljust(72) + "║")
        # 显示活跃 + 最近 cluster (≤3)
        active_recent = (in_progress + completed_clusters[-2:])[:3]
        for c in active_recent:
            cid = c.get("cluster_id", "?")
            pm = c.get("parent_me", "?")
            cr = c.get("chapter_range") or []
            cr_str = f"ch{cr[0]}-{cr[1]}" if len(cr) == 2 else "未切分"
            status = c.get("status", "?")
            wr = c.get("expected_word_range") or {}
            opus = "Opus" if c.get("opus_recommended") else "Sonnet"
            print(f"║   [{status:11s}] {cid} ({pm}) {cr_str} {opus} {wr.get('min','?')}-{wr.get('max','?')}字".ljust(72) + "║")
        # cluster ↔ ch 映射 (从 进度.cluster_blueprint)
        
        # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 派生 plan list
        # 2026-05-29 复审复修 SC-1：blueprint 可能是 list（城南实测），先归一成 dict 再迭代。
        _plan_list = []
        if cluster_lookup is not None:
            _bp = cluster_lookup.normalize_blueprint(progress)
        else:
            _bp = progress.get("cluster_blueprint") or {}
            if not isinstance(_bp, dict):
                _bp = {}
        for cid, cdata in _bp.items():
            if not isinstance(cdata, dict):
                continue
            _plan_list.extend(cdata.get("scene_storyboard", []))
        if _plan_list:
            print(f"║   cluster ↔ ch 映射:".ljust(72) + "║")
            cluster_chs = {}
            for cp in _plan_list:
                cid = cp.get("cluster_id") or "(DCAS/旧)"
                cluster_chs.setdefault(cid, []).append((cp.get("ch"), cp.get("cluster_position", "?")))
            for cid, chs in list(cluster_chs.items())[:4]:
                pos_summary = " ".join(f"ch{ch}[{pos[0]}]" for ch, pos in sorted(chs))
                print(f"║     {cid}: {pos_summary}".ljust(72) + "║")
        # ECAS checkpoint 状态
        ckp_dir = db / ".ecas_checkpoints"
        if ckp_dir.exists():
            ckps = list(ckp_dir.glob("*_final.json"))
            passed = sum(1 for f in ckps if (load_json(f, {}).get("passed") is True))
            print(f"║   checkpoint 状态: {passed}/{len(ckps)} passed".ljust(72) + "║")

    # 用户偏好状态
    print("╠══════════════════════════════════════════════════════════════════════╣")
    if (prefs.get("_meta") or {}).get("wizard_completed_at"):
        wt = prefs["_meta"]["wizard_completed_at"][:10]
        np_ = prefs.get("narrative_pacing", {}) or {}
        ic = prefs.get("interactive_mode", {}) or {}
        qc = prefs.get("quality_control", {}) or {}
        print(f"║ ⚙️  用户偏好已配置 ({wt})".ljust(72) + "║")
        print(f"║   storyteller={np_.get('storyteller_profile')} setback每{np_.get('expected_setback_per_n_ch')}章 happy={np_.get('happy_vs_dark_ratio')}".ljust(72) + "║")
        print(f"║   走向卡={'开' if ic.get('use_fate_cards') else '关'} audit={qc.get('audit_mode')} scan={qc.get('cross_chapter_scan_intensity')}".ljust(72) + "║")
    else:
        print(f"║ ⚠️  未跑 /wizard - 全用系统默认（建议跑 /wizard 定制）".ljust(72) + "║")

    print("╚══════════════════════════════════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
