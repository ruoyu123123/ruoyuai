"""hook_strength_scanner.py — F 层「钩子强度」检测器（v19 F1）

【为什么有这个】
v18 之前，章末钩子只被「反模式查杀」覆盖（validate_chapter 的 HOOK_SUMMARY_ENDING：
查总结式收尾这类“坏”）。但从没有人做**正向评分**——一个钩子到底强不强、
钩在哪类情绪上、位置对不对，没有工具量化。F 层（读者体验）就此薄弱。

本脚本对一章做正向钩子评分：
  - 识别 4 类钩子：悬念 / 冲突 / 反差 / 信息缺口
  - 定位钩子位置：开头钩（前 ~10%）/ 中段钩 / 章末钩（后 ~10%）
  - 章末钩子强度分（0-10），综合形式信号 + 钩子类型 + 反模式扣分
  - 纯过渡章/总结章不误判（识别 transition 模式 → 降级为 advisory-info）

【v19 顾问制】本检测器输出 advisory（gate_level=advisory）——
工具只提醒，writer/validator 有充分理由可豁免（如本章是卷尾故意收束式结尾）。
gate_level=advisory 单字段即表达「可豁免」，无需额外字段。

用法:
    python hook_strength_scanner.py <项目路径> <章节号>
    python hook_strength_scanner.py <项目路径> <章节号> --json   # 同义，默认就是 JSON

输出: JSON 报告到 stdout。退出 0 = 钩子达标；1 = 钩子偏弱（advisory 警告）。
"""

import sys
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写


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


def split_paragraphs(body: str):
    """按空行切段。"""
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


# ============ 钩子类型识别（4 类）============

# 悬念钩：未解的问题 / 未知的来历 / 倒计时 / 不确定
SUSPENSE_KW = re.compile(
    r"(为什么|怎么会|是谁|什么东西|不知道|不明白|想不通|说不清|"
    r"倒计时|还剩|来不及|赶不上|等着他|等了很久|一直在等|"
    r"不记得|没印象|想不起|从没见过|第一次见)")
# 冲突钩：对峙 / 威胁 / 即将爆发的矛盾 / 命令
CONFLICT_KW = re.compile(
    r"(动手|出手|拔|举枪|举起|逼近|围住|挡住|拦住|对峙|"
    r"威胁|警告|命令|不许|必须|否则|要么|杀|打|砸|撞|"
    r"翻脸|撕破|摊牌|宣战|找上门|堵在|逼到)")
# 反差钩：意料之外 / 身份反转 / 与预期相悖的事实
CONTRAST_KW = re.compile(
    r"(竟然|居然|没想到|出乎意料|意外|反而|不是.{0,8}而是|"
    r"原来|真相|其实|根本|并不是|早就|一直都是|"
    r"不对劲|不太对|哪里不对|事情不简单|"
    r"不是(?:他|她|自己|本人)的|不是他写的|笔迹不|不像是)")
# 信息缺口钩：刻意留白 / 部分揭示 / 半截信息 / 错位细节
INFOGAP_KW = re.compile(
    r"(没说完|话没说完|欲言又止|顿住|停住|没再|不肯说|"
    r"省略号|破折号|空着|空白|没有(?:编号|签名|名字|落款)|"
    r"只有.{0,10}没有|留下.{0,10}就走|消失|不见了|不翼而飞|"
    r"有字|有人写|是谁写|不知道是谁|不知道什么时候)")
# 诡异/失控钩：无主体的自动发生 / 反常理现象（多见于悬疑/异常题材章末定格）
EERIE_KW = re.compile(
    r"(自己(?:都没|没有|没下|动|浮|爬|填|写|响|亮)|"
    r"没(?:人|有人)(?:动|碰|喊|叫|说)|没下命令|"
    r"悄悄(?:浮|动|爬|出现)|自动(?:浮|填|写|亮)|"
    r"凉了|冷了下来|背后(?:一凉|发凉)|寒意|"
    r"认得他|等他.{0,6}很久|看着他)")

# R7 W2：章末钩子 11 型 taxonomy 升级（5→11）。来源 CSDN 网文 7 钩子 / RiverEditor 8 型 / SelfPublishingSchool 6 型。
# 新增 6 型：reversal_setup（反转铺垫）/ unfinished_action（未完成动作）/ new_setting（新场景登场）/
#          decision_pending（待决断）/ promise（承诺/誓言）/ threat（迫近威胁）
REVERSAL_SETUP_KW = re.compile(
    r"(伏笔|铺垫已久|该来的总会来|果然不出所料|早就料到|"
    r"暗中布置|早有准备|这一刻终于|等的就是这一刻|"
    r"埋下的种子|该收网了|时机已到|布局多年)")
UNFINISHED_ACTION_KW = re.compile(
    r"(刚要|正要|还没来得及|话音未落|手刚伸出|脚还没落地|"
    r"刚走到一半|刀刚出鞘|门刚推开|字才写到一半|"
    r"还没说完|没等.{0,4}就|一脚踏出|举到一半)")
NEW_SETTING_KW = re.compile(
    r"(推开门|跨过门槛|抵达|来到一处|站在.{0,6}前|"
    r"眼前出现|展现在眼前|穿过.{0,8}就是|一脚踏进|"
    r"陌生的地方|从未见过的|第一次踏入|进入这片)")
DECISION_PENDING_KW = re.compile(
    r"(必须做出选择|该如何抉择|两难|该不该|要不要|"
    r"该走哪条|留下还是离开|杀还是不杀|信还是不信|"
    r"答应还是拒绝|在心里反复|犹豫不决|何去何从)")
PROMISE_KW = re.compile(
    r"(我发誓|我保证|此生必|这辈子|总有一天|"
    r"我一定会|定要|誓不|不达目的不|此仇必报|"
    r"立下誓言|许下诺言|信守承诺|定不辜负)")
THREAT_KW = re.compile(
    r"(已经盯上|正在赶来|很快就会|不日将至|"
    r"风暴将至|山雨欲来|大祸临头|阴影笼罩|"
    r"步步紧逼|越来越近|逐渐逼近|迫在眉睫|"
    r"暗中窥伺|危机四伏|劫数难逃)")

HOOK_TYPES = {
    "suspense": ("悬念", SUSPENSE_KW),
    "conflict": ("冲突", CONFLICT_KW),
    "contrast": ("反差", CONTRAST_KW),
    "infogap": ("信息缺口", INFOGAP_KW),
    "eerie": ("诡异/失控", EERIE_KW),
    # R7 W2 新增 6 型
    "reversal_setup": ("反转铺垫", REVERSAL_SETUP_KW),
    "unfinished_action": ("未完成动作", UNFINISHED_ACTION_KW),
    "new_setting": ("新场景登场", NEW_SETTING_KW),
    "decision_pending": ("待决断", DECISION_PENDING_KW),
    "promise": ("承诺/誓言", PROMISE_KW),
    "threat": ("迫近威胁", THREAT_KW),
}

# 形式信号：独立短句 / 省略号 / 破折号收尾 / 问句收尾
ELLIPSIS_END = re.compile(r"(……|\.\.\.|—{1,2}|――)\s*$")
QUESTION_END = re.compile(r"[?？]\s*$")

# R18 W7 Batch-U·P2 · mid-sentence cliffhanger primitive (Loewenstein 信息缺口)
# 末位破折号/省略号 + 前句残缺 · 或末字以连接词（的/了/吗/呢）戛然而止
MID_SENTENCE_CUT_REGEX = re.compile(
    r"(?:[，,；;]\s*[^。！？!?…\n]{2,40}(?:——|――|—|……|\.\.\.))"
    r"|"
    r"(?:[^。！？!?…\n]{4,60}[的了吗呢着在](?:——|――|—|……|\.\.\.))"
)


def count_mid_sentence_cuts(text: str) -> int:
    """R18 W7 Batch-U·P2 · 句内悬挂（Loewenstein 信息缺口）命中数。
    末位破折号/省略号 + 前句以连接词或残缺动词收尾。
    consolidate 抓真作者 mid_sentence_cut_rate；作者档基线 0 则 skip。"""
    if not text:
        return 0
    return len(MID_SENTENCE_CUT_REGEX.findall(text))

# 反模式：总结式收尾 / 平铺直叙的句号收尾 / 大团圆松弛感
SUMMARY_ENDING_KW = re.compile(
    r"(就这样|从此|从那以后|总算|终于(?:结束|平静|安定|过去)|"
    r"一切都|风平浪静|尘埃落定|画上句号|告一段落|安稳|踏实地|放心地)")
# 过渡章信号：明确的时空转场陈述 / 全章低强度
TRANSITION_KW = re.compile(
    r"(几天后|几天过去|一晃|转眼|日子一天天|平静地过|"
    r"没什么特别|和往常一样|照例|例行|日常)")


def detect_hooks_in_text(text: str):
    """返回该段文本命中的钩子类型 dict：{type: 命中次数}。"""
    hits = {}
    for key, (_, pat) in HOOK_TYPES.items():
        c = len(pat.findall(text))
        if c:
            hits[key] = c
    return hits


# ============ 章末钩子强度评分 ============

def score_ending_hook(paragraphs):
    """对章末（最后 1-3 段）做钩子强度评分，0-10 分。

    评分构成：
      钩子类型命中  最多 6 分（每类 2 分，封顶 3 类）
      形式信号      最多 3 分（独立短句 / 省略号收尾 / 问句收尾）
      反模式扣分    -3 分（总结式收尾）
    """
    if not paragraphs:
        return {"score": 0, "detail": "无正文", "hook_types": [], "form_signals": []}

    tail = paragraphs[-3:] if len(paragraphs) >= 3 else paragraphs
    tail_text = "\n".join(tail)
    last_para = paragraphs[-1]
    # 章末「最后一句」：取最后一段的最后一个句子
    last_sentences = re.split(r"(?<=[。！？!?…])", last_para.strip())
    last_sentence = next((s for s in reversed(last_sentences) if s.strip()), last_para)

    hook_hits = detect_hooks_in_text(tail_text)
    # 每类 2 分，封顶 3 类 = 6 分（钩子类型贵精不贵多）
    type_score = min(6, len(hook_hits) * 2)

    form_signals = []
    form_score = 0
    # 独立短句收尾（最后一段 ≤ 15 字且自成一段）
    if len(last_para) <= 15 and "\n" not in last_para:
        form_signals.append("独立短句收尾")
        form_score += 1
    # 省略号/破折号收尾：章末窗口任一段以 …… / —— 结尾即算（悬念定格的常见手法）
    if any(ELLIPSIS_END.search(p) for p in tail) or ELLIPSIS_END.search(last_sentence):
        form_signals.append("省略号/破折号收尾")
        form_score += 1
    if QUESTION_END.search(last_para) or QUESTION_END.search(last_sentence):
        form_signals.append("问句收尾")
        form_score += 1
    form_score = min(3, form_score)

    penalty = 0
    penalty_reason = None
    if SUMMARY_ENDING_KW.search(tail_text):
        penalty = 3
        penalty_reason = "命中总结式收尾反模式"

    score = max(0, type_score + form_score - penalty)
    return {
        "score": score,
        "hook_types": [HOOK_TYPES[k][0] for k in hook_hits],
        "hook_type_hits": hook_hits,
        "type_score": type_score,
        "form_signals": form_signals,
        "form_score": form_score,
        "penalty": penalty,
        "penalty_reason": penalty_reason,
        "last_sentence_preview": last_sentence.strip()[:50],
    }


# ============ 钩子位置分布 ============

def scan_hook_positions(paragraphs):
    """统计开头钩 / 中段钩 / 章末钩的命中情况。"""
    n = len(paragraphs)
    if n == 0:
        return {"opening": {}, "middle": {}, "ending": {}}
    head_n = max(1, n // 10)
    tail_n = max(1, n // 10)
    head_text = "\n".join(paragraphs[:head_n])
    mid_text = "\n".join(paragraphs[head_n:n - tail_n]) if n > head_n + tail_n else ""
    tail_text = "\n".join(paragraphs[n - tail_n:])
    return {
        "opening": {"paragraphs": head_n, "hooks": detect_hooks_in_text(head_text)},
        "middle": {"paragraphs": max(0, n - head_n - tail_n),
                   "hooks": detect_hooks_in_text(mid_text)},
        "ending": {"paragraphs": tail_n, "hooks": detect_hooks_in_text(tail_text)},
    }


# ============ 过渡章识别 ============

def detect_transition_chapter(body: str, paragraphs):
    """判断是否为纯过渡章。过渡章对钩子强度规则不适配 → 降级 advisory-info。

    判定（任一满足）：
      - 全章命中过渡信号 ≥ 2 处 且 全章钩子总命中 < 3
      - 字数偏短（< 1500）且 章末无任何钩子类型命中
    """
    transition_hits = len(TRANSITION_KW.findall(body))
    total_hooks = sum(detect_hooks_in_text(body).values())
    wc = cio.count_words(body)
    if transition_hits >= 2 and total_hooks < 3:
        return True, f"过渡信号 {transition_hits} 处 + 全章钩子命中仅 {total_hooks}"
    if wc < 1500:
        tail_hooks = detect_hooks_in_text("\n".join(paragraphs[-3:]))
        if not tail_hooks:
            return True, f"短章（{wc}字）且章末无钩子命中"
    return False, None


# ============ 主入口 ============

def scan_cluster_hook_pacing(paragraphs, n_pseudo_cuts=4):
    """v2 cluster 视野（2026-05-28）：检测 cluster 内 N-1 个拟切点钩子节奏。
    splitter 按字数 3500/章硬切 cluster (11k-25k)，故 cluster 内有 N-1 个候选切点。
    每个切点前 3 段算钩子强度。返回均值 + 最弱点。"""
    if len(paragraphs) < 5:
        return {"pseudo_cuts": 0, "scores": [], "mean_score": 0, "min_score": 0}
    # 按段数等距取 N 个拟切点（避免依赖具体字数，因为段长不均）
    cuts = []
    for i in range(1, n_pseudo_cuts + 1):
        idx = int(len(paragraphs) * i / (n_pseudo_cuts + 1))
        if idx >= 2:
            cuts.append(idx)
    scores = []
    for cut_idx in cuts:
        # 切点前 3 段视作"章末"
        window = paragraphs[max(0, cut_idx-3):cut_idx]
        s = score_ending_hook(window)
        scores.append(s.get("score", 0))
    mean = sum(scores) / len(scores) if scores else 0
    return {
        "pseudo_cuts": len(cuts),
        "scores": scores,
        "mean_score": round(mean, 1),
        "min_score": min(scores) if scores else 0,
    }


def scan(project_root: Path, ch: int):
    body = load_chapter_body(project_root, ch)
    if body is None:
        return {"_fatal": f"第{ch}章正文未找到: {project_root}"}
    paragraphs = split_paragraphs(body)
    wc = cio.count_words(body)

    # v2 cluster 化：CLUSTER_MODE 下跑拟切点节奏（不跑单章末段）
    import os as _os
    _cluster_mode = _os.environ.get("CLUSTER_MODE") == "1"

    ending = score_ending_hook(paragraphs)
    positions = scan_hook_positions(paragraphs)
    is_transition, transition_reason = detect_transition_chapter(body, paragraphs)

    # 达标线
    PASS_THRESHOLD = 4
    weak = ending["score"] < PASS_THRESHOLD

    # v2 cluster 模式：用拟切点均值替换章末单点评估
    cluster_pacing = None
    if _cluster_mode:
        cluster_pacing = scan_cluster_hook_pacing(paragraphs, n_pseudo_cuts=4)
        # cluster 视野改判：均值 ≥ PASS_THRESHOLD 即放行（不看 cluster 末段）
        weak = cluster_pacing["mean_score"] < PASS_THRESHOLD

    warning = None
    severity = "warning"
    suppressed_reason = None
    if weak:
        if _cluster_mode and cluster_pacing:
            warning = (f"⚠️ cluster 拟切点钩子均值 {cluster_pacing['mean_score']}/10 偏弱"
                       f"（达标线 {PASS_THRESHOLD} · {cluster_pacing['pseudo_cuts']} 个候选切点）")
        else:
            warning = (f"⚠️ 章末钩子强度 {ending['score']}/10 偏弱"
                       f"（达标线 {PASS_THRESHOLD}）"
                       f"——命中钩子类型 {ending['hook_types'] or '无'}")
        if is_transition:
            severity = "info"
            suppressed_reason = f"过渡章豁免 → info：{transition_reason}"

    # R18 W7 Batch-U·P2 · mid-sentence cliffhanger primitive (Loewenstein)
    mid_cut_count = count_mid_sentence_cuts(body)
    mid_sentence_cut_rate = (
        round(mid_cut_count / (wc / 1000.0), 3) if wc > 0 else 0.0
    )

    report = {
        "schema_version": "1.0",
        "scanner": "hook_strength_scanner",
        "chapter": ch,
        "cluster_mode": _cluster_mode,
        "word_count": wc,
        "paragraphs_count": len(paragraphs),
        "is_transition_chapter": is_transition,
        "transition_reason": transition_reason,
        # v19 顾问制：本检测器全部 advisory，可凭充分理由豁免
        "gate_level": "advisory",
        "ending_hook": ending,
        "hook_positions": positions,
        "cluster_pacing": cluster_pacing,  # v2 cluster 视野拟切点节奏
        "pass_threshold": PASS_THRESHOLD,
        "severity": severity,
        "warning": warning,
        # R18 W7 Batch-U·P2 · 句内悬挂指纹（作者档基线 0 → consolidate 端 skip）
        "mid_sentence_cut_count": mid_cut_count,
        "mid_sentence_cut_rate_per_kcjk": mid_sentence_cut_rate,
    }
    if suppressed_reason:
        report["suppressed_reason"] = suppressed_reason
    return report


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        print("[FATAL] 用法: hook_strength_scanner.py <项目路径> <章节号>", file=sys.stderr)
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
        # info 级（过渡章豁免）不触发 exit 1
        if report["severity"] == "info":
            print(f"\n=== 已豁免（{report.get('suppressed_reason')}）===", file=sys.stderr)
            sys.exit(0)
        print(f"\n=== 钩子偏弱（advisory，可豁免）===\n  {report['warning']}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
