"""golden_three_scanner.py — F 层「黄金三章」检测器（v19 F2）

【为什么有这个】
网文行业铁律：前 3 章决定读者留存（黄金三章）。本脚本把「第一句够不够冲」「主角
500 字内有没有行动出场」等黄金三章要素程序化，量化检查。

本脚本对 ch1-3 做黄金三章专项检测（5 项）：
  G1 first_sentence_conflict  — 第一句是否有冲突性 / 钩力（非纯写景写天气）
  G2 protagonist_onstage      — 主角是否在 500 字内行动出场（出现 + 有动作动词）
  G3 strong_ending_hook       — 章末是否有强钩子（复用 hook_strength 评分逻辑）
  G4 info_gap_present         — 是否有明确信息差（读者/角色未知项）
  G5 no_mundane_opening       — 开篇是否为禁止的日常流水账（起床/吃饭/天气/赶路）

仅对 ch1-3 激活；ch ≥ 4 返回 status="n/a"（黄金三章规则不适用）。

【v19 顾问制】本检测器输出 advisory（gate_level=advisory）——
工具只提醒，writer/validator 有充分理由可豁免（如 ch3 是刻意的慢热铺垫章）。
gate_level=advisory 单字段即表达「可豁免」，无需额外字段。

用法:
    python golden_three_scanner.py <项目路径> <章节号>

输出: JSON 报告到 stdout。退出 0 = 达标 / n/a；1 = 黄金三章项偏弱（advisory 警告）。
"""

import sys
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402
import protagonist_lookup  # noqa: E402

# 复用 hook_strength 的章末钩子评分逻辑（DRY，避免两套打分口径）
import hook_strength_scanner as hss  # noqa: E402


# ============ 辅助 ============

def load_chapter_body(project_root: Path, ch: int):
    """加载章节正文，去掉章节标题行。找不到返回 None。"""
    try:
        body = cio.read_body(project_root, ch)
    except FileNotFoundError:
        return None
    lines = body.split("\n")
    cleaned = [l for l in lines if not re.match(r"^第\d+章", l.strip())]
    return "\n".join(cleaned).lstrip()


def first_sentence(body: str) -> str:
    """取正文第一个句子（跳过纯标记行如【...】，但保留其后的实义句）。"""
    # 先按段切，找第一段里第一个句子
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        sents = re.split(r"(?<=[。！？!?…])", para)
        for s in sents:
            s = s.strip()
            if s:
                return s
    return body[:60]


def find_protagonist_name(project_root: Path, ch: int, body: str):
    """定位主角名。优先级：
      1) protagonist_lookup 多源反查（人物卡主角位 → 角色弧线 → 事件簇 → 角色池）
      2) _changes.json 的 character_changes 第一个出现的人名
      3) 启发式：正文里出现频次最高的 2-3 字中文人名候选
    返回 (name, source)；都失败返回 (None, "unknown")。
    """
    # 1) 全仓唯一主角反查
    detail = protagonist_lookup.resolve_protagonist_detail(project_root)
    if detail["name"]:
        return detail["name"], detail["source"]
    # 2) _changes.json
    try:
        changes = cio.read_changes(project_root, ch)
        cc = changes.get("factual", {}).get("character_changes", [])
        if cc and cc[0].get("name"):
            return cc[0]["name"], "_changes.json"
    except Exception:
        pass
    # 3) 启发式：高频人名候选（2-3 字，含常见姓氏 或 反复带动作动词）
    name_counter = {}
    for m in re.finditer(r"([一-鿿]{2,3})(?:[说想看走站坐笑骂喊问答])", body):
        name_counter[m.group(1)] = name_counter.get(m.group(1), 0) + 1
    if name_counter:
        best = max(name_counter, key=name_counter.get)
        if name_counter[best] >= 3:
            return best, "heuristic_frequency"
    return None, "unknown"


# ============ G1 第一句冲突性 ============

# 冲突/钩力信号：动作冲突 / 异常 / 对峙 / 强动词 / 设问 / 反常陈述
CONFLICT_FIRST = re.compile(
    r"(死|杀|血|逃|追|抓|打|砸|撞|拔|举枪|喊|叫|尖叫|"
    r"不许|必须|否则|来不及|完了|出事|爆|炸|崩|塌|"
    r"为什么|怎么会|是谁|什么东西|竟然|居然|没想到|"
    r"评估|收容|异常|审判|宣判|复核|倒计时|处决|危险等级)")
# 纯写景/天气/平铺时间 → 弱开场
WEAK_FIRST = re.compile(
    r"^(?:天|阳光|月光|风|雨|雪|云|早晨|清晨|夜|傍晚|空气|"
    r"这(?:一)?天|那(?:一)?天|又是|和往常一样)")


def check_first_sentence(body: str) -> dict:
    fs = first_sentence(body)
    has_conflict = bool(CONFLICT_FIRST.search(fs))
    is_weak = bool(WEAK_FIRST.match(fs))
    # 短促有力的开场句（≤ 12 字且非弱开场）也算有钩力
    is_punchy = len(fs) <= 12 and not is_weak
    ok = has_conflict or (is_punchy and not is_weak)
    return {
        "pass": ok,
        "first_sentence": fs[:60],
        "has_conflict_signal": has_conflict,
        "is_punchy_short": is_punchy,
        "is_weak_scenery": is_weak,
        "note": ("第一句有冲突/钩力" if ok else
                 "第一句偏弱——纯写景/平铺，建议改为冲突或悬念切入"),
    }


# ============ G2 主角 500 字内行动出场 ============

ACTION_VERB = re.compile(
    r"(走|跑|站|坐|蹲|抬|放|拿|握|抓|举|挥|甩|推|拉|按|"
    r"翻|拍|敲|签|写|看|盯|瞥|转身|低头|抬头|开口|说|问|"
    r"停|顿|皱|捏|攥|塞|扔|摔|踢|喊|笑|骂|伸手|起身)")


def check_protagonist_onstage(body: str, protag_name) -> dict:
    """主角是否在 500 字（count_words 口径）内出现且有动作。"""
    # 取前 500 字窗口（按非空白字符计）
    window = ""
    cnt = 0
    for ch_c in body:
        window += ch_c
        if not ch_c.isspace():
            cnt += 1
        if cnt >= 500:
            break

    if not protag_name:
        # 无法定位主角名 → 退化为「前 500 字是否有人物动作」
        has_action = bool(ACTION_VERB.search(window))
        return {
            "pass": has_action,
            "protagonist": None,
            "detection": "degraded_no_name",
            "appears_in_500w": None,
            "has_action_in_500w": has_action,
            "note": ("前 500 字有人物动作（主角名未知，降级判定）" if has_action else
                     "前 500 字无明显人物动作——主角疑似未行动出场"),
        }

    appears = protag_name in window
    # 主角名附近 ±20 字内有动作动词
    has_action = False
    if appears:
        for m in re.finditer(re.escape(protag_name), window):
            ctx = window[max(0, m.start() - 20):m.end() + 20]
            if ACTION_VERB.search(ctx):
                has_action = True
                break
    ok = appears and has_action
    return {
        "pass": ok,
        "protagonist": protag_name,
        "detection": "by_name",
        "appears_in_500w": appears,
        "has_action_in_500w": has_action,
        "note": ("主角 500 字内行动出场" if ok else
                 f"主角「{protag_name}」未在 500 字内行动出场"
                 f"（出现={appears} 有动作={has_action}）"),
    }


# ============ G3 章末强钩子（复用 hook_strength）============

def check_ending_hook(paragraphs) -> dict:
    eh = hss.score_ending_hook(paragraphs)
    # 黄金三章对章末钩子要求更高：≥ 5（普通章达标线 4）
    GOLDEN_HOOK_THRESHOLD = 5
    ok = eh["score"] >= GOLDEN_HOOK_THRESHOLD
    return {
        "pass": ok,
        "ending_hook_score": eh["score"],
        "hook_types": eh["hook_types"],
        "threshold": GOLDEN_HOOK_THRESHOLD,
        "last_sentence_preview": eh.get("last_sentence_preview", ""),
        "note": ("章末强钩子达标" if ok else
                 f"章末钩子 {eh['score']}/10 未达黄金三章线 {GOLDEN_HOOK_THRESHOLD}"),
    }


# ============ G4 信息差 ============

INFOGAP_SIGNAL = re.compile(
    r"(不知道|不明白|想不通|不记得|没印象|想不起|没见过|第一次见|"
    r"为什么|怎么会|是谁|什么东西|是什么|什么时候|什么人|"
    r"秘密|隐瞒|瞒着|没告诉|没说|不肯说|没说完|欲言又止|"
    r"真相|其实|原来|背后|另有|不简单|不对劲|"
    r"有人.{0,8}知道|只有.{0,6}知道|没有人知道|不该知道|"
    # 含蓄信息差：来历追问 / 刻意不深想 / 记忆空洞 / 错位归属
    r"打哪(?:来|儿来)|哪(?:儿|里)?来的|从不(?:细想|去想|多想)|"
    r"不(?:愿|肯|敢)(?:想|提|问)|回避|追问|"
    r"不是(?:他|她|自己|本人)的|不是他写的|不记得.{0,8}写过|"
    r"没(?:有)?(?:编号|签名|落款|名字)|空着|空白)")


def check_info_gap(body: str) -> dict:
    hits = INFOGAP_SIGNAL.findall(body)
    # 黄金三章需明确信息差：≥ 3 处信号
    ok = len(hits) >= 3
    return {
        "pass": ok,
        "info_gap_signal_count": len(hits),
        "threshold": 3,
        "note": ("信息差明确" if ok else
                 f"信息差信号仅 {len(hits)} 处——读者/角色未知项不够明确，"
                 f"黄金三章应制造『想知道』的拉力"),
    }


# ============ G5 禁止日常开场 ============

MUNDANE_OPENING = re.compile(
    r"(起床|睁开眼|闹钟|赖床|洗漱|刷牙|洗脸|早饭|早餐|吃饭|"
    r"上班路上|挤地铁|挤公交|赶路|又是(?:平凡|普通|新)的一天|"
    r"和往常一样|像往常一样|跟平时一样|例行|平平无奇|平淡无奇)")


def check_no_mundane_opening(body: str) -> dict:
    """检查开篇前 ~300 字是否是禁止的日常流水账。"""
    # 取前 300 字窗口
    window = ""
    cnt = 0
    for ch_c in body:
        window += ch_c
        if not ch_c.isspace():
            cnt += 1
        if cnt >= 300:
            break
    hits = MUNDANE_OPENING.findall(window)
    ok = len(hits) == 0
    return {
        "pass": ok,
        "mundane_signal_count": len(hits),
        "mundane_signals": list(set(hits))[:5],
        "note": ("开篇非日常流水账" if ok else
                 f"开篇命中日常流水账信号 {hits[:3]}——黄金三章禁止起床/吃饭/赶路式开场"),
    }


# ============ 主入口 ============

GOLDEN_CHECKS = ["first_sentence_conflict", "protagonist_onstage",
                 "strong_ending_hook", "info_gap_present", "no_mundane_opening"]


def scan(project_root: Path, ch: int):
    # · cluster 视野：仅 cluster_001 草稿激活（虚拟 ch=9000 + cluster_id=001 触发）
    # · chapter 视野（CLUSTER_MODE 未设置时）：仅 ch1-3 激活
    import os as _os
    _cluster_mode = _os.environ.get("CLUSTER_MODE") == "1"

    if _cluster_mode:
        # cluster 视野：检测 cluster 草稿前 1500 CJK 强冲突开场
        # 仅 cluster_001 激活（其他 cluster 走 linear narrative_mode 不评开场）
        # audit_hub 对「任何」cluster 都借虚拟 ch=9000 跑检测，仅判 ch==9000 会让黄金三章
        # 开场检测被恒激活到每个 cluster；这里额外用 audit_hub 透传的 CLUSTER_ID env
        # 归一化判定，仅 cluster_001（含 "001"/"cluster_001"/"1"）才真正激活。
        _cluster_id = _os.environ.get("CLUSTER_ID", "")
        _norm = _cluster_id.replace("cluster_", "").lstrip("0") or "0"
        _is_first_cluster = _norm == "1"
        if ch != 9000 or not _is_first_cluster:
            # 非虚拟 cluster 章号，或非首个 cluster → 不评开场
            return {
                "schema_version": "1.0",
                "scanner": "golden_three_scanner",
                "chapter": ch,
                "cluster_mode": True,
                "cluster_id": _cluster_id or None,
                "status": "n/a",
                "gate_level": "advisory",
                "note": ("cluster 模式仅 cluster_001 草稿评黄金三章开场"
                         f"（当前 CLUSTER_ID={_cluster_id or '未传'} → 跳过）"),
                "warning": None,
            }
        # cluster_001 草稿激活：检测前 1500 CJK 区段
    elif ch > 3 or ch < 1:
        return {
            "schema_version": "1.0",
            "scanner": "golden_three_scanner",
            "chapter": ch,
            "status": "n/a",
            "gate_level": "advisory",
            "note": f"第{ch}章不在黄金三章范围（仅 ch1-3 激活）",
            "warning": None,
        }

    body = load_chapter_body(project_root, ch)
    if body is None:
        return {"_fatal": f"第{ch}章正文未找到: {project_root}"}
    paragraphs = hss.split_paragraphs(body)
    wc = cio.count_words(body)
    protag_name, protag_source = find_protagonist_name(project_root, ch, body)

    checks = {
        "first_sentence_conflict": check_first_sentence(body),
        "protagonist_onstage": check_protagonist_onstage(body, protag_name),
        "strong_ending_hook": check_ending_hook(paragraphs),
        "info_gap_present": check_info_gap(body),
        "no_mundane_opening": check_no_mundane_opening(body),
    }
    passed = [k for k, v in checks.items() if v["pass"]]
    failed = [k for k, v in checks.items() if not v["pass"]]

    # 5 项里 < 4 项达标 → advisory 警告
    PASS_LINE = 4
    weak = len(passed) < PASS_LINE
    warning = None
    if weak:
        fail_notes = "；".join(checks[k]["note"] for k in failed)
        warning = (f"⚠️ 黄金第{ch}章 {len(passed)}/5 项达标"
                   f"（达标线 {PASS_LINE}）——待改进：{fail_notes}")

    return {
        "schema_version": "1.0",
        "scanner": "golden_three_scanner",
        "chapter": ch,
        "status": "active",
        "word_count": wc,
        "protagonist": protag_name,
        "protagonist_source": protag_source,
        # v19 顾问制
        "gate_level": "advisory",
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "score": f"{len(passed)}/5",
        "pass_line": PASS_LINE,
        "severity": "warning",
        "warning": warning,
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        print("[FATAL] 用法: golden_three_scanner.py <项目路径> <章节号>", file=sys.stderr)
        sys.exit(2)
    project_root = Path(args[0])
    try:
        ch = int(args[1])
    except ValueError:
        print(f"[FATAL] 章节号必须是整数: {args[1]}", file=sys.stderr)
        sys.exit(2)

    report = scan(project_root, ch)
    if "_fatal" in report:
        print(f"[FATAL] {report['_fatal']}", file=sys.stderr)
        sys.exit(2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("warning"):
        print(f"\n=== 黄金三章项偏弱（advisory，可豁免）===\n  {report['warning']}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
