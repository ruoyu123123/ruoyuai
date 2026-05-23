"""plot_structure_scanner.py — 情节结构层扫描器（v17.10 H1-H8 + P1-4 H10）

业界 2026：情节结构层（章 ↔ 全书）的 7 个关键技巧。

  H1 beat          — Save the Cat 15-beat 章级映射进度
  H2 tryfail       — Try-Fail Cycle 检测（主角不能开挂）
  H5 midpoint      — Midpoint Reversal 中点反转验证
  H9 knowledge     — Information Asymmetry 信息差追踪
  H3 arc           — 角色弧光四段（Lie/Want/Need/Truth）状态
  H8 subplot       — 多线沉睡子情节检测（thread > 5 章未推进 → 报警）
  H10 kishotenketsu — 起承转结四段结构（P1-4，对应单人氛围章 / 东方叙事）

用法:
    python plot_structure_scanner.py <项目路径> <章节号> [--checks beat,tryfail,midpoint,knowledge,arc,subplot,kishotenketsu]
    python plot_structure_scanner.py <项目路径> <章节号> --all

【v19 顾问制】本扫描器 7 检测器输出的均为「情节结构建议」，非「客观错误」。
每个有 warning 的 check block 统一带 gate_level="advisory"
—— advisory 即「AI 有充分理由可豁免」（如卷尾故意总结式收束、过渡章节奏）。
"""

import sys
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写
# P1-4：从 narrative_scanner 导入【单一来源】的 chapter_mode 判定，避免参差
from narrative_scanner import detect_chapter_mode  # noqa: E402


# ============ 辅助 ============

def load_json(p, default=None):
    if not Path(p).exists():
        return default
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_chapter_body(project_root: Path, ch: int) -> str | None:
    """加载章节正文（v18 已分离直接读 txt；旧混合 txt 自动剥离 CHANGES）。"""
    try:
        return cio.read_body(project_root, ch)
    except FileNotFoundError:
        return None


# ============ H1 Save the Cat 15-beat 章级映射 ============

# Save the Cat 标准 15 beat（百分比位置 + 名称 + 含义）
STC_15_BEATS = [
    {"n": 1, "pct": 0.01, "name": "Opening Image", "desc": "开篇画面（呈现主角现状）"},
    {"n": 2, "pct": 0.05, "name": "Theme Stated", "desc": "主题陈述（往往他人之口）"},
    {"n": 3, "pct": 0.10, "name": "Setup", "desc": "搭建（主角的日常+欲望+缺陷）"},
    {"n": 4, "pct": 0.10, "name": "Catalyst", "desc": "催化剂（打破日常的事件）"},
    {"n": 5, "pct": 0.20, "name": "Debate", "desc": "辩论（是否接受冒险）"},
    {"n": 6, "pct": 0.25, "name": "Break into Two", "desc": "进入第二幕（接受冒险）"},
    {"n": 7, "pct": 0.30, "name": "B Story", "desc": "B 故事（次要主题/关系线启动）"},
    {"n": 8, "pct": 0.30, "name": "Fun and Games", "desc": "玩与乐（承诺前提的爽点）"},
    {"n": 9, "pct": 0.50, "name": "Midpoint", "desc": "中点（伪胜/伪败 + 利害提升）"},
    {"n": 10, "pct": 0.60, "name": "Bad Guys Close In", "desc": "反派逼近（外部+内部压力）"},
    {"n": 11, "pct": 0.75, "name": "All Is Lost", "desc": "万事皆失（最低点）"},
    {"n": 12, "pct": 0.80, "name": "Dark Night of the Soul", "desc": "黑暗灵魂之夜（绝望与顿悟）"},
    {"n": 13, "pct": 0.85, "name": "Break into Three", "desc": "进入第三幕（找到方案）"},
    {"n": 14, "pct": 0.95, "name": "Finale", "desc": "终局（执行方案）"},
    {"n": 15, "pct": 1.00, "name": "Final Image", "desc": "结束画面（与 Opening Image 对照）"},
]


def map_beat_for_chapter(ch: int, total_chapters: int) -> dict:
    """根据章号占比，返回应处于的 beat。"""
    pct = ch / max(1, total_chapters)
    # 找最近的 beat（向上吻合）
    chosen = STC_15_BEATS[0]
    for beat in STC_15_BEATS:
        if pct <= beat["pct"]:
            chosen = beat
            break
    else:
        chosen = STC_15_BEATS[-1]
    return {"chapter": ch, "pct": round(pct, 3), "expected_beat": chosen}


def scan_beat(project_root: Path, ch: int) -> dict:
    prog = load_json(project_root / "_数据库" / "进度.json", {})
    total = prog.get("total_chapters_planned", 200)
    mapping = map_beat_for_chapter(ch, total)
    # 检查 beat_map.json 是否已记录本章 beat
    beat_map_path = project_root / "_数据库" / "beat_map.json"
    beat_map = load_json(beat_map_path, {"schema_version": "1.0", "total_chapters": total, "chapters_beat": {}})
    actual_beat = beat_map.get("chapters_beat", {}).get(str(ch))
    return {
        "chapter": ch,
        "total_chapters": total,
        "percent_position": mapping["pct"],
        "expected_beat": mapping["expected_beat"],
        "actual_beat_declared": actual_beat,
        "match": actual_beat == mapping["expected_beat"]["name"] if actual_beat else None,
        "warning": (
            f"⚠️ ch{ch} 实际 beat 未声明（应是 '{mapping['expected_beat']['name']}'）"
            if not actual_beat else None
        ),
    }


# ============ H2 Try-Fail Cycle 检测 ============

TRY_VERBS = re.compile(r"(尝试|试着|想要|打算|准备|要|去|找|开|推|拉|抓|握)")
FAIL_KW = re.compile(r"(失败|没能|未能|没找到|无果|落空|挡住|拒绝|不行|失手|没成|未成|阻拦)")
SUCCESS_KW = re.compile(r"(成功|做到|拿到|找到|打开|解开|解决|完成|搞定)")


def scan_try_fail(project_root: Path, ch: int) -> dict:
    """扫历史 6 章，看主角是否有 try-fail 节拍。"""
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    protag = next((c for c in cards if c.get("role") == "主角"), None)
    if not protag:
        return {"error": "no protagonist found"}
    name = protag.get("name", "")
    history = []
    for c in range(max(1, ch - 5), ch + 1):
        body = load_chapter_body(project_root, c)
        if not body:
            continue
        # 段落内含主角 + try 动词
        tries_count = len(re.findall(rf"{re.escape(name)}[^\n。]{{0,15}}?(?:尝试|试|想|打算|要|去|找)", body))
        fails_count = len(FAIL_KW.findall(body))
        succ_count = len(SUCCESS_KW.findall(body))
        history.append({
            "ch": c,
            "tries_estimate": tries_count,
            "fails": fails_count,
            "successes": succ_count,
        })
    # 连续 3 章无失败 → 报警
    recent3 = history[-3:]
    no_fail_run = sum(1 for h in recent3 if h["fails"] == 0)
    return {
        "protagonist": name,
        "history_recent_6ch": history,
        "no_fail_in_recent_3ch": no_fail_run == len(recent3) and len(recent3) >= 3,
        "warning": (
            f"⚠️ 主角「{name}」最近 3 章无失败节拍——疑似开挂（Try-Fail Cycle 缺）"
            if no_fail_run == len(recent3) and len(recent3) >= 3 else None
        ),
    }


# ============ H5 Midpoint Reversal 验证 ============

REVERSAL_KW = re.compile(r"(反转|颠覆|没想到|出乎意料|竟然|原来|真相|背叛|揭开|发现.*?(?:其实|根本|真正))")


def scan_midpoint(project_root: Path, ch: int) -> dict:
    """检查中点章节是否有反转。"""
    prog = load_json(project_root / "_数据库" / "进度.json", {})
    total = prog.get("total_chapters_planned", 200)
    midpoint_ch = total // 2
    # 中点附近 ±5 章是关键
    in_midpoint_zone = abs(ch - midpoint_ch) <= 5
    if not in_midpoint_zone:
        # 检查中点章节是否已写 + 是否含 reversal
        if ch > midpoint_ch:
            body = load_chapter_body(project_root, midpoint_ch)
            if body:
                rev_count = len(REVERSAL_KW.findall(body))
                return {
                    "midpoint_ch": midpoint_ch,
                    "current_ch": ch,
                    "in_zone": False,
                    "midpoint_already_passed": True,
                    "midpoint_reversal_keyword_count": rev_count,
                    "warning": (
                        f"⚠️ 中点 ch{midpoint_ch} 反转关键词仅 {rev_count} 次（建议 ≥3 次）"
                        if rev_count < 3 else None
                    ),
                }
        return {"midpoint_ch": midpoint_ch, "current_ch": ch, "in_zone": False,
                "message": "中点尚未到达"}
    # 在中点 zone 内
    body = load_chapter_body(project_root, ch)
    rev_count = len(REVERSAL_KW.findall(body or ""))
    return {
        "midpoint_ch": midpoint_ch,
        "current_ch": ch,
        "in_zone": True,
        "reversal_keyword_count": rev_count,
        "warning": (
            f"⚠️ 处于中点 zone（ch{ch}）但反转关键词仅 {rev_count} 次"
            if rev_count < 2 else None
        ),
    }


# ============ H9 Information Asymmetry 信息差追踪 ============

def scan_knowledge_graph(project_root: Path, ch: int) -> dict:
    """读 knowledge_graph.json + 伏笔表，列出 ch 时刻的 who_knows_what。"""
    kg_path = project_root / "_数据库" / "knowledge_graph.json"
    kg = load_json(kg_path, {
        "schema_version": "1.0",
        "facts": [],
        "_doc": "每个 fact: {id, content, known_by: [{role, ch}], hidden_from: [role]}"
    })
    facts = kg.get("facts", [])
    # 从伏笔表 secrets 自动派生
    foreshadow = load_json(project_root / "_数据库" / "伏笔表.json", {})
    secrets = foreshadow.get("secrets", [])
    derived_facts = []
    for s in secrets:
        derived_facts.append({
            "id": s.get("id"),
            "content": s.get("title"),
            "established_ch": s.get("established_ch"),
            "reveal_at_ch": s.get("reveal_at_ch"),
            "status_now": "hidden" if (s.get("reveal_at_ch", 999) > ch) else "revealed",
        })
    asymmetry_index = sum(1 for d in derived_facts if d["status_now"] == "hidden")
    return {
        "chapter": ch,
        "registered_facts": len(facts),
        "derived_from_secrets": len(derived_facts),
        "asymmetry_index": asymmetry_index,  # 当前还隐藏的 fact 数
        "info_diff_summary": (
            f"ch{ch} 时刻，读者-角色信息差：{asymmetry_index} 个 fact 仍隐藏。"
            f"建议读者已知 / 角色未知 ≥1 → 制造 dramatic irony"
        ),
        "secrets_status": derived_facts[:5],
        "warning": (
            f"⚠️ asymmetry_index = 0（所有秘密已揭示），戏剧张力降低"
            if asymmetry_index == 0 else None
        ),
    }


# ============ H3 角色弧光四段 ============

def scan_character_arc(project_root: Path, ch: int) -> dict:
    """读 character_arc_state.json 看主角当前弧段。"""
    arc_path = project_root / "_数据库" / "character_arc_state.json"
    arc = load_json(arc_path, {
        "schema_version": "1.0",
        "characters": {},
        "_doc": "每个 character: {lie, want, need, truth, current_stage_at_ch}"
    })
    characters = arc.get("characters", {})
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    protag = next((c for c in cards if c.get("role") == "主角"), None)
    if not protag:
        return {"error": "no protagonist"}
    name = protag.get("name", "")
    protag_arc = characters.get(name, {})
    if not protag_arc:
        return {
            "protagonist": name,
            "arc_declared": False,
            "warning": f"⚠️ 主角「{name}」未在 character_arc_state.json 声明 Lie/Want/Need/Truth 四段",
        }
    stages = protag_arc.get("stages_by_chapter", {})
    current_stage = stages.get(str(ch), "unknown")
    return {
        "protagonist": name,
        "lie": protag_arc.get("lie", ""),
        "want": protag_arc.get("want", ""),
        "need": protag_arc.get("need", ""),
        "truth": protag_arc.get("truth", ""),
        "current_stage": current_stage,
        "warning": (
            f"⚠️ ch{ch} 主角弧段未标注（stages_by_chapter 缺）" if current_stage == "unknown" else None
        ),
    }


# ============ H8 多线沉睡子情节 ============

def scan_subplot_threads(project_root: Path, ch: int) -> dict:
    """读 subplot_threads.json 看各线 last_advanced_ch。"""
    sp_path = project_root / "_数据库" / "subplot_threads.json"
    sp = load_json(sp_path, {
        "schema_version": "1.0",
        "threads": [],
        "_doc": "每个 thread: {id, name, current_status, last_advanced_ch, target_resolve_ch}"
    })
    threads = sp.get("threads", [])
    sleeping = []
    for t in threads:
        last = t.get("last_advanced_ch", 0)
        if ch - last > 5:
            sleeping.append({
                "thread": t.get("name"),
                "last_advanced_ch": last,
                "chapters_silent": ch - last,
                "current_status": t.get("current_status"),
            })
    return {
        "chapter": ch,
        "total_threads": len(threads),
        "sleeping_threads_count": len(sleeping),
        "sleeping_threads": sleeping,
        "warning": (
            f"⚠️ {len(sleeping)} 条副线沉睡 > 5 章，建议本章激活其中至少 1 条"
            if sleeping else None
        ),
    }


# ============ H10 Kishōtenketsu 起承转结四段结构（P1-4，东方叙事补充）============
#
# 业界共识：东方叙事（中/日/韩）的四段结构 起(Ki)/承(Sho)/转(Ten)/结(Ketsu)，
# 区别于西方三幕（冲突驱动）—— 不强求冲突，靠 Q3 的「转」（视角/时间/认知的
# pivot）撑起整章张力。补西方 Save-the-Cat 15-beat 在「单人氛围章 / slice-of-
# life / 心理戏」上的盲点。
#
# 适用范围：仅在 chapter_mode == solo_atmospheric 时激活（与 narrative_scanner
# 共享判定，单一来源）。非 solo_atmospheric → status="n/a"，不产生 issue ——
# 避免对冲突驱动章节误报。
#
# 检测核心：Q3（章节第 50%-75% 段落区间）必须出现至少一个「转」标记
#   认知反转类: 才(意识到/明白/想起) / 原来 / 竟 / 没想到 / 这才 / 忽地 / 猛地
#   时间转折类: 突然 / 忽然 / 这时 / 正当 / 转眼 / 蓦地
# Q3 完全无 pivot → warning「单人氛围章缺『转』，全章四段结构未撑起」。
# 注：未把「然而 / 可是」纳入 pivot 标记 —— 它们与 anti-slop 机械层禁用词重叠，
#     强 pivot 应是上面的 realization/time 类，避免与机械层互相冲突。

TEN_REALIZATION_KW = re.compile(
    r"才(?:意识到|想起来|明白|知道|发现|察觉)|原来|竟然?|没想到|这才|"
    r"直到这时|直到[^，。\n]{1,8}才|忽地|猛地"
)
TEN_TIME_PIVOT_KW = re.compile(
    r"突然|忽然|这时|正当|转眼|一刹那|刹那间|蓦地|蓦然|霎时"
)


def scan_kishotenketsu(project_root: Path, ch: int) -> dict:
    """检测单人氛围章的「起承转结」四段结构。
    非 solo_atmospheric 章节 → status="n/a"，不产生 issue（不参与 audit 计数）。"""
    body = load_chapter_body(project_root, ch)
    if not body:
        return {"chapter": ch, "status": "n/a", "reason": "正文不存在",
                "warning": None}
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paragraphs) < 4:
        return {"chapter": ch, "status": "n/a",
                "reason": f"段落数 {len(paragraphs)} < 4，无法分四段",
                "warning": None}

    # 复用 narrative_scanner 的 chapter_mode 判定 —— 单一来源约定（不参差）
    mode = detect_chapter_mode(project_root, ch, body, paragraphs)
    if mode != "solo_atmospheric":
        return {
            "chapter": ch,
            "status": "n/a",
            "chapter_mode": mode,
            "reason": "非单人氛围章；Kishōtenketsu 适用于无冲突驱动场景，"
                      "本章走西方冲突结构（Save-the-Cat / Try-Fail）即可",
            "warning": None,
        }

    # 起承转结四段定位：Q1/Q2 = 起承（前半），Q3/Q4 = 转结（后半）。
    # 实际叙事中「转」可在 Q3 也可跨到 Q4 起始，二者紧贴。短章节段落少，
    # 严格 Q3 切片（50%-75%）会丢漏 pivot —— 实际搜索区扩大到【后半部】，
    # 与「转结连续」的叙事直觉一致。
    n = len(paragraphs)
    q1_end = max(1, n // 4)
    q2_end = max(q1_end + 1, n // 2)
    q3_end = max(q2_end + 1, (3 * n) // 4)
    back_half_start = q2_end  # 后半部 = 转 + 结 段
    back_half_text = "\n".join(paragraphs[back_half_start:])

    # 后半部必须有 pivot 标记 —— 「转」是 Kishōtenketsu 的灵魂
    realiz = TEN_REALIZATION_KW.findall(back_half_text)
    time_pivot = TEN_TIME_PIVOT_KW.findall(back_half_text)
    pivots_found = len(realiz) + len(time_pivot)
    has_twist = pivots_found >= 1

    return {
        "chapter": ch,
        "status": "active",
        "chapter_mode": mode,
        "paragraphs_total": n,
        "quarters": {
            "ki": [0, q1_end],
            "sho": [q1_end, q2_end],
            "ten": [q2_end, q3_end],
            "ketsu": [q3_end, n],
        },
        "ten_search_region": [back_half_start, n],  # 实际 pivot 搜索区
        "ten_pivots": {
            "realization_hits": realiz[:5],
            "time_pivot_hits": time_pivot[:5],
            "total": pivots_found,
        },
        "verdict": (
            "✅ 起承转结完整：后半部有「转」段标记"
            if has_twist else
            "🟡 单人氛围章后半部缺『转』段标记"
        ),
        "warning": (
            None if has_twist else
            f"⚠️ 单人氛围章后半部（段落 {back_half_start}-{n}）缺『转』段标记，"
            "建议加入认知反转（才/原来/竟/没想到）或时间转折"
            "（突然/忽然/这时/蓦地）撑起 Kishōtenketsu 的「转」"
        ),
    }


# ============ 主入口 ============

ALL_CHECKS = {
    "beat": "H1 Save the Cat 15-beat 章级映射",
    "tryfail": "H2 Try-Fail Cycle 检测",
    "midpoint": "H5 Midpoint Reversal 验证",
    "knowledge": "H9 信息差追踪",
    "arc": "H3 角色弧光四段",
    "subplot": "H8 多线沉睡子情节",
    "kishotenketsu": "H10 起承转结（单人氛围章，P1-4）",
}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    if len(args) < 2:
        print("[FATAL] 需要章节号", file=sys.stderr)
        sys.exit(2)
    ch = int(args[1])

    if "--all" in args:
        checks = list(ALL_CHECKS.keys())
    elif "--checks" in args:
        idx = args.index("--checks")
        checks = args[idx + 1].split(",")
    else:
        checks = list(ALL_CHECKS.keys())

    report = {
        "schema_version": "1.0",
        "scanner": "plot_structure_scanner",
        "chapter": ch,
        "checks_run": checks,
    }
    if "beat" in checks:
        report["beat"] = scan_beat(project_root, ch)
    if "tryfail" in checks:
        report["tryfail"] = scan_try_fail(project_root, ch)
    if "midpoint" in checks:
        report["midpoint"] = scan_midpoint(project_root, ch)
    if "knowledge" in checks:
        report["knowledge"] = scan_knowledge_graph(project_root, ch)
    if "arc" in checks:
        report["arc"] = scan_character_arc(project_root, ch)
    if "subplot" in checks:
        report["subplot"] = scan_subplot_threads(project_root, ch)
    if "kishotenketsu" in checks:
        report["kishotenketsu"] = scan_kishotenketsu(project_root, ch)

    warnings = []
    for k, v in report.items():
        if isinstance(v, dict) and v.get("warning"):
            # v19 顾问制：plot_structure_scanner 7 检测器全是「情节结构建议」非「客观错误」，
            # 每条 warning 统一标 gate_level="advisory" —— advisory 即「AI 有充分理由可豁免」。
            v["gate_level"] = "advisory"
            warnings.append(f"  [{k}] {v['warning']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if warnings:
        print("\n=== 汇总警告 ===", file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
