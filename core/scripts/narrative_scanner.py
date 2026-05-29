"""narrative_scanner.py — 段/场景级别叙事质感扫描器（v17.9 G1-G6 + P2-14 G7 + P2-16 G8）

业界 2026 共识：scene/段级别的"微观结构"决定剧情质感。
本脚本集成 8 个检测器：

  G1 gmc                — 场景 Goal-Motivation-Conflict-Disaster 完整性
  G2 mru                — 段落动机-反应顺序（Swain MRU）
  G3 orphan             — 单次出现具体名词（Chekhov 风险）
  G4 microten           — 段末微张力（每段尾应留小钩子）
  G5 repetition         — 同段重复具体名词
  G6 pov                — POV 距离梯度（近/中/远切换）
  G7 info_dump          — 信息堆砌段检测（P2-14，长叙述+设定词+低对话）
  G8 perspective_shift  — 人称切换检测（P2-16，章内第一/第三人称跳转）

用法:
    python narrative_scanner.py <项目路径> <章节号> [--checks gmc,mru,microten,orphan,repetition,pov,info_dump,perspective_shift]
    python narrative_scanner.py <项目路径> <章节号> --all
    python narrative_scanner.py <项目路径> --history --check orphan  # 跨章扫单次物件

输出: JSON 报告。退出 0 = 通过；1 = 警告；2 = 严重问题

【v19 顾问制】本扫描器 6 检测器输出的均为「质感建议」，非「客观错误」。
每个有 warning 的 check block 统一带 gate_level="advisory"
—— advisory 即「AI 有充分理由可豁免」（hard_gate 才不可豁免），一个字段说清。
原 suppressed_warnings（工具按章节模式自适配豁免）保留，与 AI 主动豁免并存
（工具自适配 + AI 主动豁免双层）。
"""

import sys
import re
import json
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写

# v2 cluster 化（2026-05-29 接入）：CLUSTER_MODE env 感知。
# 本 scanner 的多数 check（gmc/mru/microten/repetition/pov/perspective_shift）判定单元
# 是单段/单场景，与整篇体量无关。唯一对文本体量敏感的是 G7 info_dump：
# 它用「整篇命中 ≥1 段 → 报 advisory」的扁平阈值，cluster 草稿（12-25k CJK，段数 = chapter 数倍）
# 下绝对命中数天然偏高，扁平阈值会过报。故 cluster 模式给 info_dump 加按段数归一的密度门槛
# （见 scan_info_dump）。其余 check 不随 mode 浮动。
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"



# ============ 辅助 ============

def load_chapter_body(project_root: Path, ch: int) -> str | None:
    """加载章节正文（v18 已分离直接读 txt；旧混合 txt 自动剥离 CHANGES）。"""
    try:
        body = cio.read_body(project_root, ch)
    except FileNotFoundError:
        return None
    # 去掉章节标题行
    lines = body.split("\n")
    cleaned = [l for l in lines if not re.match(r"^第\d+章", l.strip())]
    return "\n".join(cleaned).lstrip()


def split_paragraphs(body: str) -> list[str]:
    """按空行切段。"""
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def split_scenes(body: str) -> list[list[str]]:
    """按空行 + 时空转换标记切场景。
    简单启发：遇到 '——' / '***' / 长空行 / 含'之后/几天后/翌日' 的句子 → 切场景。
    """
    paras = split_paragraphs(body)
    scenes = []
    current = []
    for p in paras:
        is_break = (
            p.strip() in ("——", "***", "* * *") or
            re.search(r"(?:几天|几小时|几分钟|第二天|翌日|次日|之后|片刻后|约|后来)", p[:30])
        )
        if is_break and current:
            scenes.append(current)
            current = []
        current.append(p)
    if current:
        scenes.append(current)
    return scenes


# ============ G1 GMC 场景结构 ============

GMC_GOAL_KEYWORDS = re.compile(r"(想要|要找|必须|得|想|需要|打算|要去|准备|计划|希望|盘算)")
GMC_MOTIVATION_KEYWORDS = re.compile(r"(因为|为了|为|由于|since|because)")
GMC_CONFLICT_KEYWORDS = re.compile(r"(但|可|然而|不过|挡住|阻拦|拒绝|阻止|无法|不能|没法|做不到|失败)")
GMC_DISASTER_KEYWORDS = re.compile(r"(失败|没有|没能|无果|未果|失去|失手|落空|没找到|没成功)")


def check_gmc(scene: list[str]) -> dict:
    """检查单场景的 GMC 四要素。"""
    text = "\n".join(scene)
    has_goal = bool(GMC_GOAL_KEYWORDS.search(text))
    has_motivation = bool(GMC_MOTIVATION_KEYWORDS.search(text))
    has_conflict = bool(GMC_CONFLICT_KEYWORDS.search(text))
    has_disaster = bool(GMC_DISASTER_KEYWORDS.search(text))
    score = sum([has_goal, has_motivation, has_conflict, has_disaster])
    return {
        "has_goal": has_goal,
        "has_motivation": has_motivation,
        "has_conflict": has_conflict,
        "has_disaster": has_disaster,
        "score": score,
        "verdict": "✅ 完整 GMC-D" if score == 4 else
                  "🟡 缺 1 要素" if score == 3 else
                  "🔴 缺多个要素（疑似 summary 而非 story）",
    }


def scan_gmc(scenes: list[list[str]]) -> dict:
    results = []
    for i, scene in enumerate(scenes):
        r = check_gmc(scene)
        r["scene_idx"] = i
        r["word_count"] = sum(len(p) for p in scene)
        r["first_line_preview"] = scene[0][:30] if scene else ""
        results.append(r)
    weak = [r for r in results if r["score"] < 3]
    return {
        "scenes_total": len(scenes),
        "scenes_complete": len([r for r in results if r["score"] == 4]),
        "scenes_weak": len(weak),
        "details": results,
        "warning": (
            f"⚠️ {len(weak)} 个场景 GMC 不足（业界："
            f"无 conflict = summary 而非 story）" if weak else None
        ),
    }


# ============ G2 MRU 段落动机-反应顺序 ============

DIALOGUE_PATTERN = re.compile(r'["「]([^"」\n]+)["」]')
BODY_REACTION = re.compile(r"(心跳|心一沉|手抖|眼睛|呼吸|颈侧|后背|手心|喉咙|肩|腰|脚|腿)")
THINKING_KW = re.compile(r"(他想|她想|他在想|她在想|他记得|她记得|他觉得|她觉得|他不知|她不知)")


def check_mru_paragraph(p: str) -> dict:
    """检测段内顺序错乱。MRU 正确顺序：动机/刺激 → 感觉 → 本能 → 理性 → 对话"""
    issues = []
    # 找对话与身体反应位置
    dia_match = DIALOGUE_PATTERN.search(p)
    body_match = BODY_REACTION.search(p)
    if dia_match and body_match:
        dia_pos = dia_match.start()
        body_pos = body_match.start()
        # 如果对话在身体反应之前 → 顺序错（应是先身体反应再说话）
        if dia_pos < body_pos:
            # 但豁免：对话之后伴随身体反应是合理的（"她说。她的心跳加速"）
            # 仅当 body_pos - dia_pos < 30 字 → 紧贴的反应错位
            if body_pos - dia_pos < 30:
                issues.append({
                    "type": "DIALOGUE_BEFORE_BODY",
                    "msg": "对话出现在身体反应之前（顺序疑似错）",
                    "preview": p[max(0, dia_pos-10):body_pos+10][:60],
                })
    # 段开头直接心理戏（缺动机）
    if THINKING_KW.search(p[:20]):
        issues.append({
            "type": "THINKING_START",
            "msg": "段以心理戏开始（应先有外部动机/刺激）",
            "preview": p[:40],
        })
    return {"issues": issues}


def scan_mru(paragraphs: list[str]) -> dict:
    all_issues = []
    for i, p in enumerate(paragraphs):
        r = check_mru_paragraph(p)
        for iss in r["issues"]:
            iss["paragraph_idx"] = i
            all_issues.append(iss)
    return {
        "paragraphs_scanned": len(paragraphs),
        "issues_count": len(all_issues),
        "issues": all_issues[:10],  # 前 10 条
        "warning": f"⚠️ MRU 顺序疑似错位 {len(all_issues)} 处" if all_issues else None,
    }


# ============ G3 Orphan Objects 单次物件检测 ============

CONCRETE_NOUN = re.compile(r"[一-鿿]{2,5}(?:杯|簿|钥匙|火柴|烟斗|灯|斗|钟|表|刀|剑|戒指|笔|纸|信|匣|盒|铃|镜|链|绳|纸|带|刻痕|痕迹|印|疤)")


def scan_orphan_objects(project_root: Path, current_ch: int) -> dict:
    """扫所有 ≤ current_ch 章节的具体名词频次，找出现 1 次的。"""
    counts = Counter()
    occurrences = {}  # noun → [(ch, snippet)]
    for ch in range(1, current_ch + 1):
        body = load_chapter_body(project_root, ch)
        if not body:
            continue
        for m in CONCRETE_NOUN.finditer(body):
            n = m.group(0)
            counts[n] += 1
            occurrences.setdefault(n, []).append((ch, body[max(0, m.start()-15):m.end()+15][:40]))
    orphans = []
    chekhov_candidates = []
    for n, c in counts.items():
        if c == 1:
            orphans.append({"noun": n, "ch": occurrences[n][0][0], "context": occurrences[n][0][1]})
        elif c >= 3:
            # 多次出现可能是 chekhov
            chekhov_candidates.append({"noun": n, "count": c, "chs": list(set(o[0] for o in occurrences[n]))})
    return {
        "scanned_chapters": f"1-{current_ch}",
        "orphans_total": len(orphans),
        "orphans_top10": orphans[:10],
        "chekhov_candidates_top5": chekhov_candidates[:5],
        "warning": (
            f"⚠️ {len(orphans)} 个具体物件仅出现 1 次（建议要么深化为伏笔，要么砍掉）"
            if orphans else None
        ),
    }


# ============ G4 Micro-tension 段末小钩子 ============

TENSION_END_KW = re.compile(r"(\?|？|……|—|不知|或许|也许|不确定|未答|未解|似乎|仿佛)")
ACTION_INCOMPLETE = re.compile(r"(伸手|抬头|睁开|站起|准备|刚要|正要|还没)")


def check_paragraph_tension(p: str) -> int:
    """0-5 分。段末张力。"""
    last_sentence = p.split("。")[-2] if "。" in p else p
    last_sentence = last_sentence.strip() or p[-30:]
    score = 0
    if TENSION_END_KW.search(last_sentence):
        score += 3
    if ACTION_INCOMPLETE.search(last_sentence):
        score += 2
    # 段末是独立短句（< 10 字）
    if len(p.split("\n")[-1]) <= 10:
        score += 1
    return min(5, score)


def scan_micro_tension(paragraphs: list[str]) -> dict:
    scores = [check_paragraph_tension(p) for p in paragraphs]
    low_tension = [i for i, s in enumerate(scores) if s == 0]
    avg = sum(scores) / max(1, len(scores))
    return {
        "paragraphs_scanned": len(paragraphs),
        "average_tension_score": round(avg, 2),
        "low_tension_paragraphs_count": len(low_tension),
        "low_tension_paragraphs": low_tension[:5],
        "warning": (
            f"⚠️ {len(low_tension)}/{len(paragraphs)} 段段末张力 0"
            if len(low_tension) > len(paragraphs) // 3 else None
        ),
    }


# ============ G5 重复词扫描 ============

def scan_repetition(paragraphs: list[str]) -> dict:
    """同段内同一具体名词 > 3 次 → 报警。"""
    violations = []
    for i, p in enumerate(paragraphs):
        counts = Counter()
        for m in CONCRETE_NOUN.finditer(p):
            counts[m.group(0)] += 1
        for noun, c in counts.items():
            if c > 3:
                violations.append({
                    "paragraph_idx": i,
                    "noun": noun,
                    "count": c,
                    "preview": p[:50],
                })
    return {
        "paragraphs_scanned": len(paragraphs),
        "violations_count": len(violations),
        "violations_top5": violations[:5],
        "warning": f"⚠️ {len(violations)} 处段内重复名词 > 3 次" if violations else None,
    }


# ============ G6 POV 距离梯度 ============

CLOSE_DIST = re.compile(r"(他想|她想|他记得|她记得|他觉得|她觉得|他不知|她不知|他笑|她笑|他骂|她骂|心一沉|心跳)")
MID_DIST = re.compile(r"(走|跑|站|坐|蹲|抬|放|拿|握|抓|举|挥|甩|推|拉|按|看|听|说)")
FAR_DIST = re.compile(r"(雾|雨|风|海|天|云|月光|阳光|街|路|房|楼|塔|墙)")


def classify_paragraph_pov_distance(p: str) -> str:
    """近/中/远。简单启发：含心理动词=近；含动作=中；含环境=远。"""
    close = len(CLOSE_DIST.findall(p))
    mid = len(MID_DIST.findall(p))
    far = len(FAR_DIST.findall(p))
    if close >= mid and close >= far and close > 0:
        return "close"
    if mid >= far and mid > 0:
        return "mid"
    if far > 0:
        return "far"
    return "neutral"


def scan_pov_distance(paragraphs: list[str]) -> dict:
    classes = [classify_paragraph_pov_distance(p) for p in paragraphs]
    # 检测连续 ≥3 段同距离
    runs = []
    if classes:
        cur = classes[0]
        run_start = 0
        run_count = 1
        for i in range(1, len(classes)):
            if classes[i] == cur:
                run_count += 1
            else:
                if run_count >= 3 and cur != "neutral":
                    runs.append({"start_idx": run_start, "length": run_count, "distance": cur})
                cur = classes[i]
                run_start = i
                run_count = 1
        if run_count >= 3 and cur != "neutral":
            runs.append({"start_idx": run_start, "length": run_count, "distance": cur})
    return {
        "paragraphs_scanned": len(paragraphs),
        "distribution": dict(Counter(classes)),
        "monotone_runs_count": len(runs),
        "monotone_runs": runs[:5],
        "warning": (
            f"⚠️ {len(runs)} 段连续 ≥3 段同距离（POV 单调）"
            if runs else None
        ),
    }


# ============ G7 Info-dump 检测（P2-14）============
#
# 信息堆砌段：长叙述（≥80字）+ 含设定关键词 + 几乎无对话/动作。AI 倾向于
# 在章节中段「停下来讲设定」造成 pacing 卡顿，oh-story 三遍法的 Pass1 也
# 把这类「空洞泛化」列为去 AI 味的优先项。
#
# 检测启发：单段命中所有 3 条 → info_dump 嫌疑
#   1. 段长 ≥ 80 字（短段不可能信息堆砌）
#   2. 含 ≥1 个设定堆砌关键词
#   3. 对话占比 < 10%（引号内字 / 段总字 < 0.1）

INFO_DUMP_KW = re.compile(
    r"(设定|规则|体系|历史|传说|资料|介绍|解释|起源|由来|"
    r"据说|相传|相传是|众所周知|众人皆知|世人皆知|"
    r"所谓的?|这就是|此乃|号称|号曰)"
)


def scan_info_dump(paragraphs: list[str]) -> dict:
    """检测段级 info-dump（信息堆砌段）。"""
    hits = []
    for i, p in enumerate(paragraphs):
        plen = len(p)
        if plen < 80:
            continue
        kw_hits = INFO_DUMP_KW.findall(p)
        if not kw_hits:
            continue
        # 对话占比（引号内字 / 段总字）
        dialogue_chars = sum(len(m) for m in re.findall(r'["「][^"」\n]{1,200}["」]', p))
        dialogue_ratio = dialogue_chars / max(1, plen)
        if dialogue_ratio >= 0.10:
            continue
        hits.append({
            "paragraph_idx": i,
            "paragraph_len": plen,
            "dialogue_ratio": round(dialogue_ratio, 3),
            "kw_hits": kw_hits[:5],
            "preview": p[:60].replace("\n", " "),
        })
    # 段数判定阈值：
    # · chapter 视野（默认）：单章 ≥1 处 即报 advisory（避免单段疏漏）
    # · cluster 视野（v2 2026-05-29 接入）：cluster 草稿段数是 chapter 数倍，
    #   绝对命中 ≥1 的扁平阈值会过报 → 改按段数归一的密度门槛：
    #   命中数 < max(2, 总段数的 3%) 视为 cluster 体量下的正常本底，不报 warning。
    n_scanned = len(paragraphs)
    if IS_CLUSTER_MODE:
        warn_floor = max(2, round(n_scanned * 0.03))
        emit = len(hits) >= warn_floor
    else:
        warn_floor = 1
        emit = bool(hits)
    return {
        "paragraphs_scanned": n_scanned,
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "cluster_mode": IS_CLUSTER_MODE,
        "warn_floor": warn_floor,
        "fix_hint": "把『设定堆砌段』拆成「对话/动作/感官+设定碎片」的混合段，"
                    "或挪到「需要这条设定」的剧情时机才放出。",
        "warning": (
            f"⚠️ {len(hits)} 段 info-dump 嫌疑（长叙述+设定词+低对话），"
            f"建议拆段或后置至需要时刻"
            + (f"（cluster 体量门槛 ≥{warn_floor} 段）" if IS_CLUSTER_MODE else "")
            if emit else None
        ),
    }


# ============ G8 人称切换检测（P2-16）============
#
# 业界共识："third limited should never head-hop within scenes"——章内人称切换
# 是写作 bug（除非在 scene/chapter break 处显式切）。
# 与 G6 pov 距离梯度（close/mid/far）的区别：G6 是「叙述距离」，G8 是「叙述
# 人称」。两者独立维度：第一人称可近可远，第三人称同样可近可远。
#
# 检测启发：分段统计第一/第三人称代词，判定每段主导人称；段间切换 = 警告。
#   first 信号: 我 / 我的 / 咱 / 俺 (≥2 个唯一出现 → 主导第一人称)
#   third 信号: 他 / 她 / 它 / 他的 / 她的 / 它的 (≥3 个唯一出现 → 主导第三人称)
# 两者并存且 first 占比 > 30%、third 占比 > 30% → 混用段
# 段间切换 ≥1 次（不在空行 / 场景标记后）→ warning

FIRST_PERSON_KW = re.compile(r"(我们|我的|我[^字者]|咱们|咱|俺)")
THIRD_PERSON_KW = re.compile(r"(他们|她们|它们|他的|她的|它的|他[^人字]|她[^人字]|它[^人字])")


def _classify_paragraph_person(p: str) -> str:
    """单段人称判定：first / third / mixed / neutral。"""
    first_hits = len(FIRST_PERSON_KW.findall(p))
    third_hits = len(THIRD_PERSON_KW.findall(p))
    total = first_hits + third_hits
    if total < 2:
        return "neutral"
    first_ratio = first_hits / max(1, total)
    if first_ratio > 0.7:
        return "first"
    if first_ratio < 0.3:
        return "third"
    return "mixed"


def scan_perspective_shift(paragraphs: list[str]) -> dict:
    """检测段间人称切换。仅在 first ↔ third 切换时报，neutral 段不算切换点。"""
    classes = [_classify_paragraph_person(p) for p in paragraphs]
    shifts = []
    last_solid = None  # 最近一个 first/third 段索引
    for i, c in enumerate(classes):
        if c in ("first", "third"):
            if last_solid is not None and classes[last_solid] != c:
                shifts.append({
                    "from_paragraph": last_solid,
                    "to_paragraph": i,
                    "from_person": classes[last_solid],
                    "to_person": c,
                    "preview": _preview_short(paragraphs[i]),
                })
            last_solid = i
    distribution = {
        "first": classes.count("first"),
        "third": classes.count("third"),
        "mixed": classes.count("mixed"),
        "neutral": classes.count("neutral"),
    }
    return {
        "paragraphs_scanned": len(paragraphs),
        "distribution": distribution,
        "shifts_count": len(shifts),
        "shifts": shifts[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "若有意切换（如倒叙/多视角），用空行 + 章节分隔标记（——/***）显式分场景；"
                    "段内 first ↔ third 跳转通常是 bug。",
        "warning": (
            f"⚠️ {len(shifts)} 处段间人称切换（first ↔ third）"
            if shifts else None
        ),
    }


def _preview_short(p: str, n: int = 40) -> str:
    return p[:n].replace("\n", " ")


# ============ 主入口 ============

ALL_CHECKS = {
    "gmc": "G1 GMC 场景结构",
    "mru": "G2 MRU 段落顺序",
    "orphan": "G3 单次物件 Chekhov 风险",
    "microten": "G4 段末微张力",
    "repetition": "G5 段内重复词",
    "pov": "G6 POV 距离梯度",
    "info_dump": "G7 信息堆砌段（P2-14）",
    "perspective_shift": "G8 人称切换检测（P2-16）",
}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])

    # 解析 checks
    if "--all" in args:
        checks = list(ALL_CHECKS.keys())
    elif "--checks" in args:
        idx = args.index("--checks")
        checks = args[idx + 1].split(",")
    elif "--check" in args:
        idx = args.index("--check")
        checks = [args[idx + 1]]
    else:
        checks = ["gmc", "mru", "microten", "repetition", "pov"]  # 默认章内

    # history 模式（仅 orphan 跨章）
    if "--history" in args:
        # 找最新章号
        import re as _re
        max_ch = 0
        for d in project_root.glob("章节/第*章"):
            m = _re.match(r"第(\d+)章", d.name)
            if m:
                max_ch = max(max_ch, int(m.group(1)))
        report = {"history_mode": True, "max_chapter": max_ch}
        if "orphan" in checks:
            report["orphan"] = scan_orphan_objects(project_root, max_ch)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0)

    # 单章模式
    if len(args) < 2:
        print("[FATAL] 单章模式需要章节号", file=sys.stderr)
        sys.exit(2)
    ch = int(args[1])
    body = load_chapter_body(project_root, ch)
    if not body:
        print(f"[FATAL] 找不到 ch{ch}", file=sys.stderr)
        sys.exit(2)
    paragraphs = split_paragraphs(body)
    scenes = split_scenes(body)

    # v17.11 ch4 验证产出：单人氛围章豁免分流
    # validator 实测：gmc/mru/microten 对"单人独处氛围章"产生大量噪音
    chapter_mode = _detect_chapter_mode(project_root, ch, body, paragraphs)

    report = {
        "schema_version": "1.1",
        "scanner": "narrative_scanner",
        "chapter": ch,
        "chapter_mode": chapter_mode,
        "paragraphs_count": len(paragraphs),
        "scenes_count": len(scenes),
        "checks_run": checks,
    }
    if "gmc" in checks:
        report["gmc"] = scan_gmc(scenes)
    if "mru" in checks:
        report["mru"] = scan_mru(paragraphs)
    if "orphan" in checks:
        report["orphan"] = scan_orphan_objects(project_root, ch)
    if "microten" in checks:
        report["microten"] = scan_micro_tension(paragraphs)
    if "repetition" in checks:
        report["repetition"] = scan_repetition(paragraphs)
    if "pov" in checks:
        report["pov"] = scan_pov_distance(paragraphs)
    if "info_dump" in checks:
        report["info_dump"] = scan_info_dump(paragraphs)
    if "perspective_shift" in checks:
        report["perspective_shift"] = scan_perspective_shift(paragraphs)

    # v17.11 豁免分流：单人氛围章对 gmc/mru/microten/orphan 降级为 info（不触发 exit 1）
    # ch4 validator 实测：这 4 个检测器对单人独处章规则不适配（orphan 含中文分词碎片）
    SUPPRESSED_IN_SOLO = {"gmc", "mru", "microten", "orphan"}
    warnings = []
    suppressed = []
    for k, v in report.items():
        if isinstance(v, dict) and v.get("warning"):
            # v19 顾问制：narrative_scanner 6 检测器全是「质感建议」非「客观错误」，
            # 每条 warning 统一标 gate_level="advisory" —— advisory 即「AI 有充分理由可豁免」。
            # 与下方 suppressed_warnings（工具按章节模式自适配豁免）并存形成双层：
            #   gate_level=advisory = AI 主动豁免的「权限标签」（顾问制）
            #   suppressed_reason   = 工具自适配的「已豁免标记」（工具自设限）
            v["gate_level"] = "advisory"
            if chapter_mode == "solo_atmospheric" and k in SUPPRESSED_IN_SOLO:
                suppressed.append(f"  [{k}] {v['warning']}  (单人氛围章豁免 → info)")
                v["suppressed_reason"] = "solo_atmospheric chapter — gmc/mru/microten 对单人独处章规则不适配"
            else:
                warnings.append(f"  [{k}] {v['warning']}")
    report["suppressed_warnings"] = suppressed
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if suppressed:
        print(f"\n=== 已豁免 {len(suppressed)} 条（{chapter_mode}）===", file=sys.stderr)
        for w in suppressed:
            print(w, file=sys.stderr)
    if warnings:
        print("\n=== 汇总警告（真问题）===", file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def detect_chapter_mode(project_root, ch, body, paragraphs) -> str:
    """v17.11：判断章节模式。solo_atmospheric = 单人/低对话氛围章。

    判定：cluster_blueprint.characters ≤ 1 人 OR 对话占比 < 5%

    【单一来源约定】（P1-4 起）：本函数是「章节模式」判定的**唯一权威**。
    其他 scanner（如 plot_structure_scanner 的 Kishōtenketsu）必须从此处导入，
    严禁各自复制实现 —— 避免参差。"""
    # 1) cluster_blueprint 角色数
    prog = load_json(project_root / "_数据库" / "进度.json", {}) if 'load_json' in dir() else None
    char_count = None
    try:
        import json as _json
        prog = _json.loads((project_root / "_数据库" / "进度.json").read_text(encoding="utf-8"))
        _all_scenes = []
        for cid, cdata in (prog.get("cluster_blueprint", {}) or {}).items():
            _all_scenes.extend(cdata.get("scene_storyboard", []))
        for p in _all_scenes:
            if p.get("ch") == ch:
                char_count = len(p.get("characters", []))
                break
    except Exception:
        pass
    # 2) 对话占比（引号内字数 / 总字数）
    dialogue_chars = sum(len(m) for m in re.findall(r'["「][^"」\n]{1,200}["」]', body))
    total_chars = len(body.replace(" ", "").replace("\n", ""))
    dialogue_ratio = dialogue_chars / max(1, total_chars)

    if (char_count is not None and char_count <= 1) or dialogue_ratio < 0.05:
        return "solo_atmospheric"
    return "normal"


# 兼容别名 —— 保持 v17.11 起 _detect_chapter_mode 调用者可继续工作
_detect_chapter_mode = detect_chapter_mode


if __name__ == "__main__":
    main()
