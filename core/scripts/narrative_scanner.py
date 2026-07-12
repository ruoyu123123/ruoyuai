"""段落与场景级叙事质感扫描器。

集成 7 个检测器：

  G1 gmc                — 场景 Goal-Motivation-Conflict-Disaster 完整性
  G2 mru                — 段落动机-反应顺序（Swain MRU）
  G3 microten           — 段末微张力（每段尾应留小钩子）
  G4 repetition         — 同段重复具体名词
  G5 pov                — POV 距离梯度（近/中/远切换）
  G6 info_dump          — 信息堆砌段检测（长叙述+设定词+低对话）
  G7 perspective_shift  — 人称切换检测

用法:
    python narrative_scanner.py <项目路径> <cluster_id> --draft <cluster草稿> --all
    python narrative_scanner.py <项目路径> <cluster_id> --draft <cluster草稿> --checks gmc,mru,microten

输出: JSON 报告。退出 0 = 通过；1 = 警告；2 = 严重问题

所有 warning 都是 `advisory`。工具可按文本特征写入 `suppressed_reason`，写作 agent
也可按豁免协议提供理由；本扫描器不产生 hard_gate。
"""

import argparse
import sys
import re
import json
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup  # noqa: E402

# 输入始终是显式传入的完整 cluster 草稿。info_dump 按段数归一，其余检查按段或场景计算。
# ============ 辅助 ============


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

DIALOGUE_PATTERN = re.compile(r'["“「]([^"”」\n]+)["”」]')  # 含弯引号 U+201C/U+201D
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


# ============ 具体名词识别 ============

CONCRETE_NOUN = re.compile(r"[一-鿿]{2,5}(?:杯|簿|钥匙|火柴|烟斗|灯|斗|钟|表|刀|剑|戒指|笔|纸|信|匣|盒|铃|镜|链|绳|纸|带|刻痕|痕迹|印|疤)")


# ============ G3 Micro-tension 段末小钩子 ============

TENSION_END_KW = re.compile(r"(\?|？|……|—|不知|或许|也许|不确定|未答|未解|似乎|仿佛)")
ACTION_INCOMPLETE = re.compile(r"(伸手|抬头|睁开|站起|准备|刚要|正要|还没)")


def check_paragraph_tension(p: str) -> int:
    """0-5 分。段末张力。"""
    # 取段末「真正最后一句」：保留所有句末终结符再取最后一个非空句
    # （与姊妹 hook_strength_scanner.py 的「找末句」写法一致；直接 p.split("。")[-2] 会漏掉
    #  以钩子符 ？/……/」 收尾的网文刻意钩子形态，把有问句/省略号钩子的段误判成段末张力 0）。
    # 切分点：句末终结符 / 右引号 之后，且其后不再紧跟同类符——这样 ……（省略号串）与
    # ？」（问号+右引号）等终结符串整段视为一个收尾，不被切碎（否则末句只剩单 … 或单 」 仍漏）。
    _sents = re.split(r"(?<=[。！？!?…」”])(?![。！？!?…」”])", p.strip())
    last_sentence = next((s for s in reversed(_sents) if s and s.strip()), p)
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


# ============ G4 重复词扫描 ============

# scan_repetition 局部归一：把 CONCRETE_NOUN 命中片段（带前置修饰字，如「茶杯/门钥匙」）
# 归一到核心名词（杯/钥匙）再计数，否则「茶杯…又…茶杯…再…茶杯」会被切成多个不同 key，
# max 计数恒为 1、永不触发 > 3 阈值（漏报）。多字后缀须排在单字前以匹配最长后缀。
# 后缀集与 CONCRETE_NOUN 保持同步；这里只合并同一显式名词，不做语义近似推断。
_NOUN_SUFFIX = re.compile(r"(钥匙|火柴|烟斗|戒指|刻痕|痕迹|杯|簿|灯|斗|钟|表|刀|剑|笔|纸|信|匣|盒|铃|镜|链|绳|带|印|疤)$")


def _repetition_core_key(s: str) -> str:
    m = _NOUN_SUFFIX.search(s)
    if not m:
        return s
    return s[max(0, m.start(1) - 1):]  # 后缀 + 其前 1 字 = 核心名词（茶杯/门钥匙→杯钥匙核）


def scan_repetition(paragraphs: list[str]) -> dict:
    """同段内同一具体名词 > 3 次 → 报警。"""
    violations = []
    for i, p in enumerate(paragraphs):
        counts = Counter()
        for m in CONCRETE_NOUN.finditer(p):
            counts[_repetition_core_key(m.group(0))] += 1
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


# ============ G5 POV 距离梯度 ============

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


# ============ G6 Info-dump 检测 ============
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
        dialogue_chars = sum(len(m) for m in re.findall(r'["“「][^"”」\n]{1,200}["”」]', p))  # 含弯引号
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
    # · cluster 视野：cluster 草稿段数是 chapter 数倍，若用绝对命中 ≥1 的扁平阈值会过报，
    #   故按段数归一的密度门槛：命中数 < max(2, 总段数的 3%) 视为 cluster 体量下的
    #   正常本底，不报 warning。
    n_scanned = len(paragraphs)
    warn_floor = max(2, round(n_scanned * 0.03))
    emit = len(hits) >= warn_floor
    return {
        "paragraphs_scanned": n_scanned,
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "warn_floor": warn_floor,
        "fix_hint": "把『设定堆砌段』拆成「对话/动作/感官+设定碎片」的混合段，"
                    "或挪到「需要这条设定」的剧情时机才放出。",
        "warning": (
            f"⚠️ {len(hits)} 段 info-dump 嫌疑（长叙述+设定词+低对话），"
            f"建议拆段或后置至需要时刻"
            + f"（cluster 体量门槛 ≥{warn_floor} 段）"
            if emit else None
        ),
    }


# ============ G7 人称切换检测 ============
#
# 业界共识："third limited should never head-hop within scenes"——章内人称切换
# 是写作 bug（除非在 scene/chapter break 处显式切）。
# 与 G5 pov 距离梯度（close/mid/far）的区别：G5 是「叙述距离」，G7 是「叙述
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
    "microten": "G3 段末微张力",
    "repetition": "G4 段内重复词",
    "pov": "G5 POV 距离梯度",
    "info_dump": "G6 信息堆砌段",
    "perspective_shift": "G7 人称切换检测",
}


def main():
    parser = argparse.ArgumentParser(description="扫描完整 cluster 草稿的叙事质感")
    parser.add_argument("project", type=Path)
    parser.add_argument("cluster_id")
    parser.add_argument("--draft", required=True, type=Path)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--all", action="store_true")
    choice.add_argument("--checks")
    args = parser.parse_args()

    project_root = args.project.resolve()
    cluster_id = cluster_lookup.normalize_cluster_id(args.cluster_id)
    if not cluster_id:
        parser.error(f"非法 cluster_id: {args.cluster_id}")
    draft_path = args.draft
    if not draft_path.is_absolute():
        draft_path = project_root / draft_path
    try:
        body = draft_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        parser.error(f"cluster 草稿读取失败: {exc}")
    if not body.strip():
        parser.error("cluster 草稿为空")

    if args.all:
        checks = list(ALL_CHECKS)
    elif args.checks:
        checks = [value.strip() for value in args.checks.split(",") if value.strip()]
    else:
        checks = ["gmc", "mru", "microten", "repetition", "pov"]
    unknown = sorted(set(checks) - set(ALL_CHECKS))
    if unknown:
        parser.error(f"未知 checks: {', '.join(unknown)}")

    paragraphs = split_paragraphs(body)
    scenes = split_scenes(body)

    narrative_mode = detect_narrative_mode(project_root, cluster_id, body, paragraphs)

    report = {
        "schema_version": "1.1",
        "scanner": "narrative_scanner",
        "cluster_id": cluster_id,
        "narrative_mode": narrative_mode,
        "paragraphs_count": len(paragraphs),
        "scenes_count": len(scenes),
        "checks_run": checks,
    }
    if "gmc" in checks:
        report["gmc"] = scan_gmc(scenes)
    if "mru" in checks:
        report["mru"] = scan_mru(paragraphs)
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

    suppressed_in_solo = {"gmc", "mru", "microten"}
    warnings = []
    suppressed = []
    for k, v in report.items():
        if isinstance(v, dict) and v.get("warning"):
            # 质感建议统一标 advisory；suppressed_reason 记录工具自适配豁免。
            v["gate_level"] = "advisory"
            if narrative_mode == "solo_atmospheric" and k in suppressed_in_solo:
                suppressed.append(f"  [{k}] {v['warning']}  (单人氛围 cluster 豁免 → info)")
                v["suppressed_reason"] = "solo_atmospheric cluster 不适用该通用结构建议"
            else:
                warnings.append(f"  [{k}] {v['warning']}")
    report["suppressed_warnings"] = suppressed
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if suppressed:
        print(f"\n=== 已豁免 {len(suppressed)} 条（{narrative_mode}）===", file=sys.stderr)
        for w in suppressed:
            print(w, file=sys.stderr)
    if warnings:
        print("\n=== 汇总警告（真问题）===", file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def detect_narrative_mode(project_root, cluster_id, body, paragraphs) -> str:
    """判断文本特征模式。solo_atmospheric = 单人/低对话氛围章。

    判定：cluster_blueprint.characters ≤ 1 人 OR 对话占比 < 5%

    其他 scanner 复用本函数，避免重复实现。"""
    # 1) cluster_blueprint 角色数
    char_count = None
    try:
        import json as _json
        prog = _json.loads((project_root / "_数据库" / "进度.json").read_text(encoding="utf-8"))
        _all_scenes = []
        # blueprint 可能是 list，先归一成 dict 再迭代。
        _bp = cluster_lookup.normalize_blueprint(prog)
        cluster = _bp.get(cluster_id) if isinstance(_bp, dict) else None
        if isinstance(cluster, dict):
            characters = {
                character
                for scene in cluster.get("scene_storyboard", []) or []
                if isinstance(scene, dict)
                for character in scene.get("characters", []) or []
                if character
            }
            char_count = len(characters)
    except Exception:
        pass
    # 2) 对话占比（引号内字数 / 总字数）
    dialogue_chars = sum(len(m) for m in re.findall(r'["“「][^"”」\n]{1,200}["”」]', body))  # 含弯引号
    total_chars = len(body.replace(" ", "").replace("\n", ""))
    dialogue_ratio = dialogue_chars / max(1, total_chars)

    if (char_count is not None and char_count <= 1) or dialogue_ratio < 0.05:
        return "solo_atmospheric"
    return "normal"


if __name__ == "__main__":
    main()
