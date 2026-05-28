"""semantic_slop_scanner.py — 语义层 AI 腔扫描器（v19 · B+ 文笔语义层）

【定位】
anti-slop.md 是【机械层】：正则抓【词】（禁用词 / 情绪套话 / 动作套话）。
本扫描器是【语义层】：抓正则漏掉的【句级 / 段级 AI 腔】——要理解结构才能判的。
规则取自 Humanizer-zh（op7418，源自 Wikipedia「Signs of AI writing」），
经【小说化裁剪】：剔除非虚构专属项（标题大小写 / 表情符号 / 协作痕迹 / 弯引号 /
知识截止免责），保留 8 条适用小说正文的语义模式。

【8 个检测器】
  B+1 metaphor_explain   — 隐喻后立即解释（"像困兽，这意味着…" → 不信任读者）
  B+2 aphorism           — 金句体（短句 + 抽象名词 + 断言词，AI 爱写格言）
  B+3 neg_parallel       — 否定式排比（"不是…而是…" / "不仅…而且…"）
  B+4 copula_avoid       — 系动词回避（"作为…的存在" 替代 "是"）
  B+5 fake_range         — 虚假范围（一句里 ≥2 个 "从…到…" 强行铺跨度）
  B+6 over_hedge         — 过度限定（"可能也许大概" 一句里叠 ≥2 个）
  B+7 forced_triple      — 强行三段列举（顿号三连，AI 凑"全面感"）
  B+8 tag_synonym_cycle  — 对话标签同义词循环（说道/答道/回道/沉声道… 轮换避重复）

【v19 顾问制】
本扫描器 8 检测器输出的全是「文笔建议」非「客观错误」——
每个有 warning 的 check block 统一带 gate_level="advisory"。
advisory 即「写作 agent 有充分理由可豁免」（豁免带理由 < 100 字）。
codes（SEMANTIC_*）不在 audit_hub 的 HARD_GATE_CODES 内，天然 advisory。
与 anti-slop.md（机械层）分工互补，不重叠。

【用法】
    python semantic_slop_scanner.py <项目路径> <章节号> [--all]
    python semantic_slop_scanner.py <项目路径> <章节号> --checks metaphor_explain,aphorism
输出: JSON 报告。退出 0 = 通过；1 = 有 advisory 警告；2 = 致命（章节不存在）

【接入 audit_hub】
audit_hub.py 走 _parse_scanner_json(out, "semantic", SEMANTIC_DIM) 解析——
报告顶层 key 必须 == 检测器名；每个 check block 须有 warning(str|None) /
severity / gate_level / fix_hint 字段。
"""

import sys
import re
import json
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写


# ============ 辅助 ============

def load_chapter_body(project_root: Path, ch: int) -> str | None:
    """加载章节正文（v18 已分离直接读 txt）。去掉章节标题行。"""
    try:
        body = cio.read_body(project_root, ch)
    except FileNotFoundError:
        return None
    lines = body.split("\n")
    cleaned = [l for l in lines if not re.match(r"^第\d+章", l.strip())]
    return "\n".join(cleaned).lstrip()


def split_paragraphs(body: str) -> list[str]:
    """按空行切段。"""
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def split_sentences(body: str) -> list[str]:
    """按句末标点切句，保留可读的句子单元（含对话）。"""
    raw = re.split(r"(?<=[。！？\n])", body)
    return [s.strip() for s in raw if s.strip()]


def _preview(s: str, n: int = 50) -> str:
    return s[:n].replace("\n", " ")


# ============ B+1 隐喻后立即解释 ============

# 比喻标记：句中出现「像/如同/仿佛…」
METAPHOR_MARK = re.compile(r"(像|如同|仿佛|宛如|犹如|好似|好比|恰似|似一|像一|如一)")
# 解释标记：紧跟其后的「抽象总结」连接词 —— AI 不信任读者、把比喻嚼碎喂
EXPLAIN_MARK = re.compile(
    r"(意味着|象征着|代表着|也就是说|换句话说|这说明|这表明|这意味|"
    r"暗示着|预示着|象征了|代表了|寓意着|折射出|映照出)"
)


def scan_metaphor_explain(sentences: list[str]) -> dict:
    """句内：先有比喻标记，紧接着又有「解释/抽象总结」标记 → 隐喻后立即解释。
    豁免：比喻与解释之间隔着对话引号边界（「」""）→ 多半是叙述比喻 + 另一句
    对话内容，并非「叙述者解释自己的比喻」，跳过（杜绝跨对话边界误报）。"""
    hits = []
    for i, s in enumerate(sentences):
        m = METAPHOR_MARK.search(s)
        if not m:
            continue
        e = EXPLAIN_MARK.search(s, m.end())
        if e and e.start() - m.end() <= 40:
            span = s[m.end():e.start()]
            if any(q in span for q in ("「", "」", "“", "”", "\"")):
                continue  # 跨对话引号边界 → 不是同一叙述者在解释比喻
            hits.append({"sentence_idx": i, "preview": _preview(s, 60),
                         "metaphor": m.group(0), "explain": e.group(0)})
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "比喻写完就停，别紧跟「这意味着/象征着」把它嚼碎——信任读者能接住。",
        "warning": (f"⚠️ {len(hits)} 处「隐喻后立即解释」（不信任读者）"
                    if hits else None),
    }


# ============ B+2 金句体 ============

# 抽象大词：AI 格言句的常客
ABSTRACT_NOUN = re.compile(
    r"(命运|人生|世界|真相|选择|时间|孤独|恐惧|希望|答案|生命|死亡|"
    r"自由|未来|过去|人性|欲望|信仰|宿命|代价|意义|本质|灵魂|存在|"
    r"光|黑暗|terror|救赎|远方|青春|成长)"
)
# 断言词：把抽象大词钉成"永恒真理"的口吻
ASSERTION = re.compile(
    r"(从来|永远|终究|不过是|本就|向来|从不|注定|总是|往往|无非|"
    r"终归|本质上|说到底|归根结底|不外乎|始终|无外乎|本质就是)"
)


def scan_aphorism(paragraphs: list[str], sentences: list[str]) -> dict:
    """短句 + 抽象名词 + 断言词 = 金句体嫌疑。独立成段的短金句加权。"""
    solo_short = {p.strip() for p in paragraphs if len(p.strip()) <= 30}
    hits = []
    for i, s in enumerate(sentences):
        core = s.rstrip("。！？\n ")
        if len(core) > 28:
            continue
        if ABSTRACT_NOUN.search(core) and ASSERTION.search(core):
            hits.append({
                "sentence_idx": i,
                "preview": _preview(s, 50),
                "standalone": s.strip() in solo_short,
            })
    standalone_hits = [h for h in hits if h["standalone"]]
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "standalone_count": len(standalone_hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "「人生从来都是…」式金句删掉或落地成具体动作/细节——"
                    "可引用 ≠ 好，往往是 AI 在装深刻。",
        "warning": (f"⚠️ {len(hits)} 处金句体（短句+抽象大词+断言口吻），"
                    f"其中 {len(standalone_hits)} 处独立成段"
                    if len(hits) >= 2 else None),
    }


# ============ B+3 否定式排比 ============

NEG_PARALLEL = re.compile(
    r"((?:不仅仅?|不只|不光|不单)[^。！？\n]{1,30}(?:而且|并且|还|更|也|甚至))"
    r"|((?:不是|并非|不再是|不只是|不仅仅是)[^。！？\n]{1,30}(?:而是|更是|却是|乃是))"
)


def scan_neg_parallel(sentences: list[str]) -> dict:
    """「不是…而是…」「不仅…而且…」结构计数，≥2 次 → AI 排比腔。"""
    hits = []
    for i, s in enumerate(sentences):
        for m in NEG_PARALLEL.finditer(s):
            hits.append({"sentence_idx": i, "preview": _preview(s, 55),
                         "match": m.group(0)[:30]})
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "「这不是X，而是Y」用一两次是力量，三次以上是套路——"
                    "直接说 Y 就行。",
        "warning": (f"⚠️ {len(hits)} 处否定式排比（不是…而是 / 不仅…而且）"
                    if len(hits) >= 2 else None),
    }


# ============ B+4 系动词回避 ============

COPULA_AVOID = re.compile(
    r"(作为[^，。！？\n]{1,15}(?:的存在|般的存在|而存在))"
    r"|(充当着?[^，。！？\n]{0,12}(?:的角色|的存在))"
    r"|(扮演着?[^，。！？\n]{0,12}的角色)"
    r"|(堪称[^，。！？\n]{1,12})"
    r"|(称得上是?[^，。！？\n]{0,12})"
    r"|(不啻[于为])"
)


def scan_copula_avoid(sentences: list[str]) -> dict:
    """用「作为…的存在 / 充当着…的角色」绕开一个简单的「是」。"""
    hits = []
    for i, s in enumerate(sentences):
        for m in COPULA_AVOID.finditer(s):
            hits.append({"sentence_idx": i, "preview": _preview(s, 55),
                         "match": m.group(0)[:24]})
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "「他作为团队核心的存在」→「他是团队的核心」。"
                    "能用「是」就别绕。",
        "warning": (f"⚠️ {len(hits)} 处系动词回避（作为…的存在 / 充当…的角色）"
                    if len(hits) >= 2 else None),
    }


# ============ B+5 虚假范围 ============

RANGE_PAIR = re.compile(r"从[^，。！？、\n]{1,14}到[^，。！？、\n]{1,14}")


def scan_fake_range(sentences: list[str]) -> dict:
    """一句话里塞 ≥2 个「从X到Y」强行铺跨度 = AI 的虚假范围腔。
    单个「从…到…」是正常表达，不报。

    v2 cluster 化（2026-05-28）：cluster 视野下 voice 归因，
    阈值从 ≥1 命中即报 → ≥3 命中才报（容忍 voice 必要使用）。
    """
    import os as _os
    _cluster_mode = _os.environ.get("CLUSTER_MODE") == "1"
    hits = []
    for i, s in enumerate(sentences):
        ranges = RANGE_PAIR.findall(s)
        if len(ranges) >= 2:
            hits.append({"sentence_idx": i, "preview": _preview(s, 60),
                         "range_count": len(ranges)})
    _report_threshold = 3 if _cluster_mode else 1
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "「从晨光到暮色，从山巅到海底」式连环跨度——"
                    "挑一个真正相关的，删掉其余。",
        "warning": (f"⚠️ {len(hits)} 句出现连环「从…到…」（虚假范围）"
                    if len(hits) >= _report_threshold else None),
    }


# ============ B+6 过度限定 ============

HEDGE = re.compile(
    r"(可能|也许|大概|或许|似乎|应该|差不多|几乎|多半|约莫|想必|"
    r"怕是|说不定|兴许|大抵|多少有些|有点儿?|有些)"
)


def scan_over_hedge(sentences: list[str]) -> dict:
    """一句话里叠 ≥2 个限定词 → 过度限定（把话说得软塌塌没立场）。
    注：「似乎」单用已被 anti-slop 机械层管，这里管的是【叠用】这个不同信号。"""
    hits = []
    for i, s in enumerate(sentences):
        ms = HEDGE.findall(s)
        if len(ms) >= 2:
            hits.append({"sentence_idx": i, "preview": _preview(s, 55),
                         "hedge_count": len(ms)})
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "「他可能大概也许是想……」→ 留一个限定词或干脆删光。"
                    "叙述要有立场，不是免责声明。",
        "warning": (f"⚠️ {len(hits)} 句限定词叠用 ≥2（过度限定）"
                    if len(hits) >= 2 else None),
    }


# ============ B+7 强行三段列举 ============

TRIPLE_DUNHAO = re.compile(r"[一-鿿]{2,7}、[一-鿿]{2,7}、[一-鿿]{2,7}")


def scan_forced_triple(sentences: list[str]) -> dict:
    """顿号三连计数。三项并列本身不一定坏，但高频出现 = AI 凑「全面感」的套路。
    频次驱动报警（≥3 次），由写作 agent 凭场景判断是否豁免。"""
    hits = []
    for i, s in enumerate(sentences):
        for m in TRIPLE_DUNHAO.finditer(s):
            hits.append({"sentence_idx": i, "preview": _preview(s, 55),
                         "triple": m.group(0)})
    return {
        "sentences_scanned": len(sentences),
        "hits_count": len(hits),
        "hits": hits[:8],
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "「创新、灵感和洞察」式三连——两项更利落，四项更真实，"
                    "强行凑三项是 AI 的「完整感」执念。",
        "warning": (f"⚠️ {len(hits)} 处顿号三连列举（强行三段式，AI 套路）"
                    if len(hits) >= 3 else None),
    }


# ============ B+8 对话标签同义词循环 ============

SPEECH_TAG = re.compile(
    r"(说道|问道|答道|回道|应道|喊道|叫道|笑道|怒道|沉声道|低声道|"
    r"轻声道|冷声道|开口道|出声道|嘟囔道|嘀咕道|反问道|追问道|解释道|"
    r"补充道|叹道|哼道|嗤道|喃喃道|呢喃道|附和道|插话道|打断道|"
    r"沉吟道|苦笑道|冷笑道|轻笑道|低喝道|厉声道)"
)


def scan_tag_synonym_cycle(body: str) -> dict:
    """收集对话标签动词，统计【不同变体数】。
    变体太多 = AI 在用同义词循环硬避「说道」重复——这本身就是 AI 腔。
    （与 anti-slop 机械层互补：那边管「说道」单一词重复，这边管「换着花样说」。）"""
    tags = SPEECH_TAG.findall(body)
    variety = Counter(tags)
    distinct = len(variety)
    return {
        "speech_tags_total": len(tags),
        "distinct_variants": distinct,
        "distribution": dict(variety.most_common(12)),
        "severity": "warning",
        "gate_level": "advisory",
        "fix_hint": "对话标签变体 ≥6 种 = 在「换着花样说」硬避重复。"
                    "多数对话靠动作/上下文带出说话人，标签只留「说」就够。",
        "warning": (f"⚠️ 对话标签用了 {distinct} 种变体（同义词循环嫌疑，"
                    f"共 {len(tags)} 处）" if distinct >= 6 else None),
    }


# ============ 主入口 ============

ALL_CHECKS = {
    "metaphor_explain": "B+1 隐喻后立即解释",
    "aphorism": "B+2 金句体",
    "neg_parallel": "B+3 否定式排比",
    "copula_avoid": "B+4 系动词回避",
    "fake_range": "B+5 虚假范围",
    "over_hedge": "B+6 过度限定",
    "forced_triple": "B+7 强行三段列举",
    "tag_synonym_cycle": "B+8 对话标签同义词循环",
}


def scan_chapter(project_root: Path, ch: int, checks: list[str]) -> dict:
    """扫一章，返回报告 dict。顶层 key == 检测器名（audit_hub 据此解析）。"""
    body = load_chapter_body(project_root, ch)
    if body is None:
        return {"_fatal": f"找不到第{ch}章正文"}
    paragraphs = split_paragraphs(body)
    sentences = split_sentences(body)

    report = {
        "schema_version": "1.0",
        "scanner": "semantic_slop_scanner",
        "chapter": ch,
        "paragraphs_count": len(paragraphs),
        "sentences_count": len(sentences),
        "checks_run": checks,
    }
    if "metaphor_explain" in checks:
        report["metaphor_explain"] = scan_metaphor_explain(sentences)
    if "aphorism" in checks:
        report["aphorism"] = scan_aphorism(paragraphs, sentences)
    if "neg_parallel" in checks:
        report["neg_parallel"] = scan_neg_parallel(sentences)
    if "copula_avoid" in checks:
        report["copula_avoid"] = scan_copula_avoid(sentences)
    if "fake_range" in checks:
        report["fake_range"] = scan_fake_range(sentences)
    if "over_hedge" in checks:
        report["over_hedge"] = scan_over_hedge(sentences)
    if "forced_triple" in checks:
        report["forced_triple"] = scan_forced_triple(sentences)
    if "tag_synonym_cycle" in checks:
        report["tag_synonym_cycle"] = scan_tag_synonym_cycle(body)
    return report


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    if len(args) < 2:
        print("[FATAL] 需要章节号", file=sys.stderr)
        sys.exit(2)
    try:
        ch = int(args[1])
    except ValueError:
        print(f"[FATAL] 章节号必须是整数: {args[1]}", file=sys.stderr)
        sys.exit(2)

    if "--all" in args:
        checks = list(ALL_CHECKS.keys())
    elif "--checks" in args:
        idx = args.index("--checks")
        checks = [c.strip() for c in args[idx + 1].split(",") if c.strip()]
    else:
        checks = list(ALL_CHECKS.keys())  # 默认全开

    report = scan_chapter(project_root, ch, checks)
    if "_fatal" in report:
        print(f"[FATAL] {report['_fatal']}", file=sys.stderr)
        sys.exit(2)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    warnings = [v["warning"] for k, v in report.items()
                if isinstance(v, dict) and v.get("warning")]
    if warnings:
        print(f"\n=== 语义层 AI 腔 {len(warnings)} 项（advisory，写作 agent 可凭理由豁免）===",
              file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
