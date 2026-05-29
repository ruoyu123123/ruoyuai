"""cross_cluster_pattern_aggregate.py — 跨章模式扫描（v19 新增）

补 style_drift_scan 的盲区：分布均衡类问题。

扫描 6 个维度（单章静态检测器看不出来的）：
1. catchphrase 单一化      — 某 catchphrase 占主角对话总配额 > 60%
2. 段首主语重复            — "<主角名>XX" 开头段落数过密
3. dialogue tag 机械化     — "[他她]说道|[姓名]说道" 频率过高
4. 身体反应套话集中        — 瞳孔/半秒/按住/后颈/鸡皮疙瘩 等
5. 否定式描写过密          — "不+形容词" 模式
6. 段首词云重复            — 前 50 段段首 2 字的多样性

输出：
- 报告 JSON 写到 _数据库/.cross_chapter_scan/scan_<timestamp>.json
- 终端打印每维度均值/方差/超阈值章节
- 触发警告时给具体建议（不是简单 fail）

阈值策略：
- z-score: 单章 > 跨章均值 + 2σ 视为 outlier
- catchphrase: 用 frequency_per_chapter 上限（人物卡指定）或默认 50%
- 集中度: top1 段首词 > 35% 即告警

用法：
    python cross_cluster_pattern_aggregate.py <项目路径> [--protagonist 陆衍] [--last-n 10]

退出码:
    0 = 健康
    1 = 有 advisory (跨章不均衡，但单章 OK)
    2 = 严重违规 (跨章持续不均衡)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev



# ============================================================
# v2 cluster 化方案 Phase 3 PX（2026-05-28）：
# 本 scanner 标记为「待升维 cross_cluster_aggregate」
# CLUSTER_MODE env=1 时已感知 cluster 视野（具体阈值逐步迁移）
# 计划：下个版本（v4）正式 git mv → cross_cluster_<X>_aggregate.py
# ============================================================
import os as _os
IS_CLUSTER_MODE = _os.environ.get("CLUSTER_MODE") == "1"

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # 2026-05-29 cluster 化：摘要驱动

# 2026-05-29 复审修复 [C3]：cluster 模式 per_chapter[ch] = builder 的 pattern_metrics，
# 但 builder 逐维 try/except 写入，任一维度失败即缺键。下方 20 个 Finding 用裸下标读，
# 缺键 → KeyError → Traceback（SC-2 真崩溃）。这里固化磁盘版同构 schema 的缺省，
# 让 ledger 记录补齐后再消费，与磁盘模式（20 键恒全）行为一致。
_PER_CHAPTER_DEFAULTS = {
    "wc": 0,
    "catchphrase": {},
    "para_protagonist_start": 0,
    "dialogue_tag": 0,
    "body_reaction": 0,
    "negation_desc": 0,
    "para_first_word_top1_pct": 0.0,
    "tell_count": 0,
    "tell_per_1k": 0.0,
    "particle_dist": {"le": 0, "guo": 0, "zhe": 0, "de": 0, "total": 0},
    "pronoun_action": 0,
    "punctuation": {"halfwidth_comma": 0, "three_dot": 0, "straight_quote": 0},
    "metaphor_count": 0,
    "metaphor_per_1k": 0.0,
    "cliche_hits": {},
    "name_density_per_100": 0.0,
    "warmup_hits": [],
    "causal_count": 0,
    "causal_per_1k": 0.0,
    "time_anchor_drops": 0,
    "modifier_stack_sentences": 0,
    "sensory_dist": {},
    "dialogue_stream_max": 0,
}

# ===== 通用工具 =====

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_files(project_root: Path) -> list[tuple[int, Path]]:
    """返回 [(ch_num, path), ...] 排序后的章节文件列表（含 .pre_opening.txt 也算入对应章）。"""
    out: list[tuple[int, Path]] = []
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if not m:
            continue
        ch = int(m.group(1))
        # 主正文优先
        for f in d.glob(f"第{ch:03d}章.txt"):
            out.append((ch, f))
            break
        else:
            for f in d.glob(f"第{ch}章*.txt"):
                out.append((ch, f))
                break
    out.sort(key=lambda x: x[0])
    return out


def get_protagonist(project_root: Path, override: str | None) -> str:
    """从人物卡读主角名（role=主角），允许命令行 override。"""
    if override:
        return override
    chars = load_json(project_root / "_数据库" / "人物卡.json", {"characters": []})
    for c in chars.get("characters", []):
        if c.get("role") == "主角":
            return c.get("name", c.get("id", "主角"))
    return "主角"


def get_catchphrases(project_root: Path, protagonist: str) -> list[dict]:
    """读主角的 catchphrase，兼容两种 schema：
       - 旧：['行吧', '算了']
       - 新：[{phrase: '行吧', scene: '...', frequency_per_chapter: '≤1'}, ...]
    """
    chars = load_json(project_root / "_数据库" / "人物卡.json", {"characters": []})
    for c in chars.get("characters", []):
        if c.get("name") == protagonist or c.get("id") == protagonist:
            vp = c.get("voice_pack", {})
            raw = vp.get("catchphrase", [])
            out = []
            for item in raw:
                if isinstance(item, str):
                    out.append({"phrase": item, "frequency_per_chapter": "0-2"})
                elif isinstance(item, dict):
                    out.append(item)
            return out
    return []


# ===== v19.6 G11 惯用语冷却期 =====

IDIOM_COOLDOWN_DICT = [
    # 高强度成语/词组：每 N 章只能用 1 次
    "心如刀绞", "心如死灰", "魂飞魄散", "不寒而栗",
    "万箭穿心", "肝肠寸断", "撕心裂肺", "痛不欲生",
    "万念俱灰", "形同陌路", "兵败如山倒", "怒发冲冠",
    "黯然神伤", "悲痛欲绝", "肝胆俱裂",
]


def scan_paragraph_length_trend(text: str) -> dict:
    """G12: 检测章内段落长度退化（虎头蛇尾）。
    把段落按位置切前/中/后三段，计算每段均长，若末段 < 首段 60% → 退化告警。
    """
    paras = [p.strip() for p in text.split("\n") if p.strip() and len(p.strip()) >= 5]
    if len(paras) < 30:
        return {"degradation": False, "reason": "段落太少不评"}
    n = len(paras)
    first_third = paras[:n//3]
    last_third = paras[2*n//3:]
    avg_first = sum(len(p) for p in first_third) / len(first_third)
    avg_last = sum(len(p) for p in last_third) / len(last_third)
    ratio = avg_last / avg_first if avg_first > 0 else 1.0
    return {
        "degradation": ratio < 0.6,
        "avg_first_third": round(avg_first, 1),
        "avg_last_third": round(avg_last, 1),
        "ratio_last_over_first": round(ratio, 2),
    }


def scan_idiom_cooldown_violations(chapter_texts: dict[int, str], cooldown: int = 5) -> list[dict]:
    """G11: 检查惯用语冷却期违规。

    规则：同一惯用语在 cooldown 章内最多用 1 次。
    返回 [{idiom, chapters_used, violation_pairs}, ...]
    """
    violations = []
    for idiom in IDIOM_COOLDOWN_DICT:
        used_in = sorted([ch for ch, text in chapter_texts.items() if idiom in text])
        if len(used_in) < 2:
            continue
        # 找间隔 < cooldown 的相邻对
        pairs = []
        for i in range(1, len(used_in)):
            gap = used_in[i] - used_in[i-1]
            if gap < cooldown:
                pairs.append((used_in[i-1], used_in[i], gap))
        if pairs:
            violations.append({
                "idiom": idiom,
                "all_chapters_used": used_in,
                "violation_pairs": pairs,
                "cooldown_required": cooldown,
            })
    return violations


# ===== 6 个维度的扫描器 =====

def scan_catchphrase(text: str, catchphrases: list[dict]) -> dict[str, int]:
    """返回 {phrase: count}"""
    return {cp["phrase"]: text.count(cp["phrase"]) for cp in catchphrases}


def scan_paragraph_starts_with_protagonist(text: str, protagonist: str) -> int:
    """段落以 <主角名> 开头的数量。"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    return sum(1 for p in paras if p.startswith(protagonist))


def scan_dialogue_tags(text: str) -> int:
    """X 说道/X 想 模式（不严格区分）。"""
    # 1) 「他/她+说想道」+ 2) 「[姓]+1-2字+说道」
    patt1 = re.findall(r"[他她][说想道]", text)
    patt2 = re.findall(r"[陆顾林吴老程韩邵宋钟谢沈周][一-鿿]?[说道]", text)
    return len(patt1) + len(patt2)


def scan_body_reactions(text: str) -> int:
    """身体反应套话集中度。"""
    terms = ["瞳孔", "半秒", "按住", "后颈", "鸡皮疙瘩", "愣了", "怔了", "胃部一凉", "脚步一顿"]
    return sum(text.count(t) for t in terms)


def scan_negation_descriptions(text: str) -> int:
    """不+形容词 否定式描写（不大/不小/不远/不近）。"""
    return len(re.findall(r"不(大|小|远|近|早|晚|多|少|长|短|快|慢|高|低|深|浅|重|轻)", text))


# ===== v19.2 后备防御 10 项（业界 AI 写作通用盲区） =====

def scan_punctuation_halfwidth(text: str) -> dict[str, int]:
    """半角逗号紧贴中文 / 三点省略号 / 英文引号混用。"""
    halfwidth_comma = len(re.findall(r",(?=[^\x00-\x7f])", text))
    three_dot = len(re.findall(r"(?<!\.)\.{3}(?!\.)", text))
    straight_quote = len(re.findall(r'"[^"]*"', text))  # 直引号
    return {"halfwidth_comma": halfwidth_comma, "three_dot": three_dot, "straight_quote": straight_quote}


def scan_metaphor_overuse(text: str) -> tuple[int, float]:
    """类比比喻密度（宛如/犹如/仿佛/好似/恰似/如同），返回 (count, per_1k)。"""
    c = len(re.findall(r"宛如|犹如|仿佛|好似|恰似|如同", text))
    return c, c / max(len(text) / 1000, 1)


CLICHE_AI_CN_FALLBACK = [
    "空气凝固", "空气仿佛凝固", "时间静止", "时间仿佛静止",
    "心头一震", "心头一紧", "心头一颤", "血液冻结", "血液凝固",
    "空气中弥漫", "心如刀绞", "魂飞魄散", "不寒而栗",
    "寒意袭来", "面色凝重", "脸色一变", "脸色铁青",
    "眼中闪过一丝", "嘴角勾起一抹",
]


def _load_cliche_dict(project_root: Path) -> list[str]:
    """v19.5 对齐: 动态从蒸馏 anti_patterns.never_words + 人物卡 banned_phrases 拼接 cliche 词典。
    Fallback 到硬编码列表。"""
    out = set(CLICHE_AI_CN_FALLBACK)
    # 1) 蒸馏 anti_patterns.never_words
    style_path = project_root / "_数据库" / "作者风格.json"
    if style_path.exists():
        try:
            sd = json.loads(style_path.read_text(encoding="utf-8"))
            for w in sd.get("anti_patterns", {}).get("never_words", []):
                if isinstance(w, str):
                    # 去除"（解释）"
                    core = re.split(r"[（(]", w)[0].strip()
                    if core and len(core) >= 2:
                        out.add(core)
                elif isinstance(w, dict):
                    word = w.get("word") or w.get("text")
                    if word and len(word) >= 2:
                        out.add(word)
        except (json.JSONDecodeError, ValueError):
            pass
    # 2) 人物卡 voice_pack.banned_phrases (所有角色合并)
    cards_path = project_root / "_数据库" / "人物卡.json"
    if cards_path.exists():
        try:
            cd = json.loads(cards_path.read_text(encoding="utf-8"))
            for c in cd.get("characters", []):
                for w in c.get("voice_pack", {}).get("banned_phrases", []):
                    if isinstance(w, str) and len(w) >= 2:
                        out.add(w)
        except (json.JSONDecodeError, ValueError):
            pass
    return sorted(out)


def scan_cliche_ai_cn(text: str, dictionary: list[str] | None = None) -> dict[str, int]:
    """dictionary: 可选自定义词典；不传走 fallback。"""
    if dictionary is None:
        dictionary = CLICHE_AI_CN_FALLBACK
    return {c: text.count(c) for c in dictionary if text.count(c) > 0}


def scan_protagonist_name_density(text: str, protagonist: str) -> float:
    """主角名每百字密度。"""
    if len(text) == 0:
        return 0.0
    return text.count(protagonist) / (len(text) / 100)


WARMUP_WORDS = ["众所周知", "不得不说", "值得一提", "在这个", "不知不觉", "由此可见", "说起来", "其实", "顺便一提"]


def scan_opening_warmup(text: str, head_chars: int = 200) -> list[str]:
    head = text[:head_chars]
    return [w for w in WARMUP_WORDS if w in head]


def scan_causal_heuristic(text: str) -> tuple[int, float]:
    causal_terms = ["于是", "因此", "所以", "因而"]
    c = sum(text.count(t) for t in causal_terms)
    return c, c / max(len(text) / 1000, 1)


TIME_ANCHOR_WORDS = ["早上", "中午", "下午", "傍晚", "晚上", "凌晨", "夜里", "清晨", "半夜", "上午",
                     "今天", "昨天", "明天", "周一", "周二", "周三", "周四", "周五", "周六", "周日",
                     "点", "小时", "分钟", "天后"]


def scan_time_anchor_drops(text: str, win_size: int = 1500, step: int = 500) -> int:
    """连续 1500 字窗口无时间词的次数。"""
    drops = 0
    for i in range(0, max(len(text) - win_size, 1), step):
        window = text[i:i+win_size]
        if not any(w in window for w in TIME_ANCHOR_WORDS):
            drops += 1
    return drops


def scan_modifier_stack(text: str) -> int:
    """单句副词形容词叠加（「地/的」≥4 次的句子）。"""
    sentences = re.split(r"[。！？]", text)
    overstack = 0
    for s in sentences:
        if len(s) > 10 and (s.count("地") + s.count("的")) >= 4:
            overstack += 1
    return overstack


SENSORY_TERMS = {
    "视觉": ["看", "见", "望", "瞄", "盯", "瞥", "色", "光", "亮", "影", "目", "瞳"],
    "听觉": ["听", "响", "叫", "声音", "吵", "静", "嗡", "咔", "啪", "叮"],
    "嗅觉": ["闻", "气味", "味道", "香", "臭", "腥", "味儿", "香水"],
    "味觉": ["尝", "咸", "甜", "苦", "辣", "酸", "甘"],
    "触觉": ["摸", "握", "抓", "按", "碰", "凉", "热", "烫", "冷", "滑", "硬", "软"],
}


def scan_sensory_distribution(text: str) -> dict[str, float]:
    counts = {k: sum(text.count(w) for w in v) for k, v in SENSORY_TERMS.items()}
    total = sum(counts.values())
    if total == 0:
        return {k: 0.0 for k in SENSORY_TERMS}
    return {k: c / total for k, c in counts.items()}


def scan_dialogue_stream_flat(text: str) -> int:
    """连续 ≥6 轮纯「X说/想」无动作 beat。返回最长连续段数。"""
    # 分段，找连续多段只含说话 tag 的最大长度
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    max_streak = 0
    cur = 0
    for p in paras:
        # 判定该段是否「纯对话/说话标签」：含引号 + 含「X说/X想/X道」+ 长度 < 80
        has_quote = '"' in p or '"' in p or '"' in p
        has_say = bool(re.search(r"[他她][说想道]|[陆顾林吴老程沈周][一-鿿]?[说道]", p))
        short = len(p) < 80
        if has_quote and (has_say or short):
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0
    return max_streak


def scan_tell_overuse(text: str) -> tuple[int, float]:
    """内心动词密度（show vs tell 信号）。返回 (count, per_1k_chars)。"""
    terms = ["觉得", "感觉到", "意识到", "认为", "明白", "知道", "想到", "想着"]
    c = sum(text.count(t) for t in terms)
    return c, c / max(len(text) / 1000, 1)


def scan_sentence_particle_distribution(text: str) -> dict[str, float]:
    """句末助词分布（了/过/着/的）的比例。"""
    le = text.count("了。") + text.count("了，") + text.count("了？")
    guo = text.count("过。") + text.count("过，")
    zhe = text.count("着。") + text.count("着，")
    de = text.count("的。") + text.count("的，")
    total = le + guo + zhe + de
    if total == 0:
        return {"le": 0, "guo": 0, "zhe": 0, "de": 0, "total": 0}
    return {"le": le / total, "guo": guo / total, "zhe": zhe / total, "de": de / total, "total": total}


def scan_pronoun_action(text: str) -> int:
    """「他+1字动作」模式（独立于段首主语）。"""
    matches = re.findall(r"他[把在想说看走坐站抬伸点抓拈翻打按掏摸合扣笑听等顿]", text)
    return len(matches)


def scan_para_first_word_concentration(text: str, top_n: int = 50) -> float:
    """前 top_n 段段首 2 字的最大占比（top1 频率/总段数）。"""
    paras = [p.strip() for p in text.split("\n") if p.strip()][:top_n]
    if not paras:
        return 0.0
    starts = Counter(p[:2] if len(p) >= 2 else p for p in paras)
    top1_count = starts.most_common(1)[0][1]
    return top1_count / len(paras)


# ===== 统计 + 阈值 =====

def compute_zscore(values: list[int | float], v: int | float) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    sd = pstdev(values)
    if sd == 0:
        return 0.0
    return (v - m) / sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--protagonist", default=None)
    ap.add_argument("--last-n", type=int, default=10)
    args = ap.parse_args()

    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    protagonist = get_protagonist(project_root, args.protagonist)
    catchphrases = get_catchphrases(project_root, protagonist)
    cliche_dict = _load_cliche_dict(project_root)

    # ===== 2026-05-29 cluster 化分支：账本有 pattern_metrics → 取 builder 预算的 20 维 =====
    # --last-n 在 cluster 模式语义为「最后 N 个 cluster 的章」；不再逐章 glob 重扫正文。
    # 趋势/分布检测的数据点来自账本而非磁盘。
    use_ledger = (
        csr.is_cluster_mode()
        and csr.ledger_has_field(project_root, "pattern_metrics")
    )
    per_chapter: dict[int, dict] = {}
    # ledger 模式辅助数据（替代逐章 text 重扫的 Finding 10 / G11）
    ledger_char_mentions: dict[int, dict] = {}   # ch -> {char: count}
    ledger_idiom_hits: dict[int, dict] = {}      # ch -> {idiom: count}
    chapter_records: list = []                   # [(ch, rec)]，cluster 模式才填

    if use_ledger:
        # 2026-05-30 北极星复审：args.last_n 是【章数】窗口（编排器已把 --last-n-clusters 换算成章数）。
        # 原把它当 last_n_clusters（cluster 个数）传 reader → 单位错配、cluster 窗口失效扫全量。
        # 改为全取后按章数截最后 N 章，与磁盘分支 chapters[-last_n:] 语义一致。
        chapter_records = csr.get_chapter_records(project_root)
        if args.last_n and args.last_n > 0:
            chapter_records = chapter_records[-args.last_n:]
        if not chapter_records:
            print("[OK] cluster 账本无章记录，跳过跨章扫描")
            sys.exit(0)
        for ch, rec in chapter_records:
            pm = rec.get("pattern_metrics")
            if not isinstance(pm, dict):
                continue
            per_chapter[ch] = pm
            ledger_char_mentions[ch] = rec.get("char_mention_counts") or {}
            ledger_idiom_hits[ch] = rec.get("idiom_hits") or {}
        if not per_chapter:
            print("[OK] cluster 账本无 pattern_metrics，跳过跨章扫描")
            sys.exit(0)
        # 2026-05-29 复审修复 [C3]：builder 的 pattern_metrics 逐维 try/except 写入，
        # 任一维度缺失就会让下方 Finding 的裸下标 d["xxx"] 抛 KeyError → Traceback
        # （SC-2 真崩溃，编排器误判脚本挂掉）。这里按磁盘版同构 schema 补齐缺省，
        # 与磁盘模式行为一致（磁盘版 20 键恒全），零回归。
        for _ch in per_chapter:
            base = dict(_PER_CHAPTER_DEFAULTS)
            # 2026-05-30 北极星复审：嵌套 dict（punctuation/particle_dist/sensory_dist）须深合并——
            # 原 .update 对嵌套 dict 是整体替换，builder 写部分 dict（缺 three_dot 等内部键）会覆盖掉
            # defaults 的完整 dict → 下游裸下标 p["three_dot"] KeyError（C3 只补了顶层键，漏嵌套键）。
            for _k, _v in per_chapter[_ch].items():
                if _v is None:
                    continue
                if isinstance(_v, dict) and isinstance(base.get(_k), dict):
                    _merged = dict(base[_k])
                    _merged.update(_v)
                    base[_k] = _merged
                else:
                    base[_k] = _v
            per_chapter[_ch] = base
        # 与磁盘分支 chapters 同构：[(ch, None)]，path 在 ledger 模式不可用
        chapters = [(ch, None) for ch in sorted(per_chapter.keys())]
        # 跳过逐章 text 统计循环（per_chapter 已由账本填好）
        do_text_scan = False
    else:
        chapters = find_chapter_files(project_root)
        if not chapters:
            print("[OK] 无已写章节，跳过跨章扫描")
            sys.exit(0)
        chapters = chapters[-args.last_n:]
        do_text_scan = True

    # 逐章统计 20 维度（10 主防御 + 10 后备防御）—— 仅磁盘模式跑（cluster 模式 per_chapter 来自账本）
    for ch, path in (chapters if do_text_scan else []):
        text = path.read_text(encoding="utf-8")
        tell_count, tell_rate = scan_tell_overuse(text)
        metaphor_count, metaphor_rate = scan_metaphor_overuse(text)
        causal_count, causal_rate = scan_causal_heuristic(text)
        per_chapter[ch] = {
            "wc": len(text),
            "catchphrase": scan_catchphrase(text, catchphrases),
            "para_protagonist_start": scan_paragraph_starts_with_protagonist(text, protagonist),
            "dialogue_tag": scan_dialogue_tags(text),
            "body_reaction": scan_body_reactions(text),
            "negation_desc": scan_negation_descriptions(text),
            "para_first_word_top1_pct": scan_para_first_word_concentration(text),
            "tell_count": tell_count,
            "tell_per_1k": round(tell_rate, 2),
            "particle_dist": scan_sentence_particle_distribution(text),
            "pronoun_action": scan_pronoun_action(text),
            # v19.2 后备防御
            "punctuation": scan_punctuation_halfwidth(text),
            "metaphor_count": metaphor_count,
            "metaphor_per_1k": round(metaphor_rate, 2),
            "cliche_hits": scan_cliche_ai_cn(text, cliche_dict),
            "name_density_per_100": round(scan_protagonist_name_density(text, protagonist), 2),
            "warmup_hits": scan_opening_warmup(text),
            "causal_count": causal_count,
            "causal_per_1k": round(causal_rate, 2),
            "time_anchor_drops": scan_time_anchor_drops(text),
            "modifier_stack_sentences": scan_modifier_stack(text),
            "sensory_dist": scan_sensory_distribution(text),
            "dialogue_stream_max": scan_dialogue_stream_flat(text),
        }

    # 跨章聚合
    # 2026-05-29 复审修复 [C3]：cluster 模式 per_chapter[ch] = builder 的 pattern_metrics，
    # 其逐维 try/except 写入，任一维度失败即缺键。原裸下标 d["xxx"] → KeyError → Traceback
    # （SC-2 视为真崩溃，编排器误判脚本挂掉）。全程改 d.get(默认) 优雅跳过。
    para_start_vals = [d.get("para_protagonist_start", 0) for d in per_chapter.values()]
    dialogue_vals = [d.get("dialogue_tag", 0) for d in per_chapter.values()]
    body_vals = [d.get("body_reaction", 0) for d in per_chapter.values()]
    neg_vals = [d.get("negation_desc", 0) for d in per_chapter.values()]

    findings = []

    # ===== Finding 1: catchphrase 单一化 =====
    catchphrase_totals = Counter()
    for d in per_chapter.values():
        cp = d.get("catchphrase") or {}
        if not isinstance(cp, dict):
            continue
        for k, v in cp.items():
            catchphrase_totals[k] += v
    total_cp = sum(catchphrase_totals.values())
    if total_cp > 0 and catchphrases:
        top_phrase, top_count = catchphrase_totals.most_common(1)[0]
        top_ratio = top_count / total_cp
        # 检测 1: top1 占比过高
        if top_ratio > 0.5 and total_cp >= 5:
            severity = "warning" if top_ratio > 0.7 else "advisory"
            findings.append({
                "dimension": "catchphrase",
                "severity": severity,
                "gate_level": "advisory",
                "code": "CATCHPHRASE_UNIFICATION",
                "metric": {"top_phrase": top_phrase, "top_count": top_count, "total": total_cp, "top_ratio": round(top_ratio, 2)},
                "message": f"'{top_phrase}' 占 catchphrase 总用量 {top_ratio:.0%} (共 {total_cp})；分布不均衡",
                "suggestion": f"后续章节 writer 主动用其他 catchphrase，'{top_phrase}' 每章 ≤1 次",
            })
        # 检测 2: 是否有 catchphrase 0 使用（说明 writer 锁第一个）
        unused = [cp["phrase"] for cp in catchphrases if catchphrase_totals[cp["phrase"]] == 0]
        if len(unused) >= len(catchphrases) // 2 + 1 and len(chapters) >= 3:
            findings.append({
                "dimension": "catchphrase",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "CATCHPHRASE_UNUSED",
                "metric": {"unused": unused, "total_options": len(catchphrases), "chapters_scanned": len(chapters)},
                "message": f"过半数 catchphrase 跨 {len(chapters)} 章 0 次使用: {unused}",
                "suggestion": "writer 锁定数组前几个，建议人物卡 catchphrase 加 scene 标注让分配场景化",
            })

    # ===== Finding 2: 段首主语重复 =====
    if para_start_vals:
        avg_start = mean(para_start_vals)
        for ch, d in per_chapter.items():
            z = compute_zscore(para_start_vals, d["para_protagonist_start"])
            if d["para_protagonist_start"] >= 20 and z >= 2.0:
                findings.append({
                    "dimension": "paragraph_subject",
                    "severity": "warning",
                    "gate_level": "advisory",
                    "code": "PARAGRAPH_SUBJECT_REPETITION",
                    "chapter": ch,
                    "metric": {"count": d["para_protagonist_start"], "z_score": round(z, 2), "avg": round(avg_start, 1)},
                    "message": f"ch{ch} 有 {d['para_protagonist_start']} 段以「{protagonist}」开头，z={z:.2f}",
                    "suggestion": f"交替用「他」、隐藏主语、动作前置（'把鼠标晃醒' 而非 '{protagonist}把鼠标晃醒'）",
                })
        # 跨章绝对值告警（即使 z 不超阈值，但单章过多也警告）
        for ch, d in per_chapter.items():
            if d["para_protagonist_start"] >= 25 and not any(f.get("code") == "PARAGRAPH_SUBJECT_REPETITION" and f.get("chapter") == ch for f in findings):
                findings.append({
                    "dimension": "paragraph_subject",
                    "severity": "advisory",
                    "gate_level": "advisory",
                    "code": "PARAGRAPH_SUBJECT_HIGH_ABS",
                    "chapter": ch,
                    "metric": {"count": d["para_protagonist_start"]},
                    "message": f"ch{ch} 段首「{protagonist}」开头 {d['para_protagonist_start']} 次（绝对值偏高）",
                    "suggestion": "建议 ≤15 次/章",
                })

    # ===== Finding 3: dialogue tag 机械化（绝对阈值，不依赖 z-score） =====
    if dialogue_vals:
        for ch, d in per_chapter.items():
            count = d["dialogue_tag"]
            if count >= 25:
                findings.append({
                    "dimension": "dialogue_tag",
                    "severity": "warning",
                    "gate_level": "advisory",
                    "code": "DIALOGUE_TAG_MECHANIZATION",
                    "chapter": ch,
                    "metric": {"count": count},
                    "message": f"ch{ch} 「X说/想」标签 {count} 处（≥25 警戒线）",
                    "suggestion": "用动作 dialogue tag 替代（'把咖啡杯放下：xxx' 而非 'X说：xxx'），换 1/3 即可",
                })
            elif count >= 18:
                findings.append({
                    "dimension": "dialogue_tag",
                    "severity": "advisory",
                    "gate_level": "advisory",
                    "code": "DIALOGUE_TAG_HIGH",
                    "chapter": ch,
                    "metric": {"count": count},
                    "message": f"ch{ch} 「X说/想」标签 {count} 处（≥18 偏多）",
                    "suggestion": "建议 ≤15 处/章",
                })

    # ===== Finding 4: 身体反应套话集中 =====
    if body_vals:
        for ch, d in per_chapter.items():
            if d["body_reaction"] >= 6:
                findings.append({
                    "dimension": "body_reaction",
                    "severity": "advisory",
                    "gate_level": "advisory",
                    "code": "BODY_REACTION_OVERUSE",
                    "chapter": ch,
                    "metric": {"count": d["body_reaction"]},
                    "message": f"ch{ch} 身体反应套话 {d['body_reaction']} 处（瞳孔/半秒/按住/鸡皮疙瘩等）",
                    "suggestion": "建议 ≤6 处/章；同一章不重复使用同一组身体反应模板",
                })

    # ===== Finding 5: 否定式描写过密 =====
    if neg_vals:
        for ch, d in per_chapter.items():
            if d["negation_desc"] >= 6:
                findings.append({
                    "dimension": "negation_desc",
                    "severity": "advisory",
                    "gate_level": "advisory",
                    "code": "NEGATION_DESC_OVERUSE",
                    "chapter": ch,
                    "metric": {"count": d["negation_desc"]},
                    "message": f"ch{ch} 「不+形容词」否定式描写 {d['negation_desc']} 处",
                    "suggestion": "用具体名词/形容词替代否定式（'巴掌大' 而非 '不大'）",
                })

    # ===== Finding 6: 段首词云重复 =====
    for ch, d in per_chapter.items():
        if d["para_first_word_top1_pct"] >= 0.35:
            findings.append({
                "dimension": "para_first_word",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "PARA_FIRST_WORD_CONCENTRATION",
                "chapter": ch,
                "metric": {"top1_pct": round(d["para_first_word_top1_pct"], 2)},
                "message": f"ch{ch} 前 50 段段首词集中度 {d['para_first_word_top1_pct']:.0%}",
                "suggestion": "段首词应多样（陆衍/他/动作/对话/场景轮换）",
            })

    # ===== Finding 7: TELL_OVERUSE 内心动词过密 =====
    for ch, d in per_chapter.items():
        if d["tell_per_1k"] >= 1.5:
            findings.append({
                "dimension": "tell_overuse",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "TELL_OVERUSE",
                "chapter": ch,
                "metric": {"count": d["tell_count"], "per_1k_chars": d["tell_per_1k"]},
                "message": f"ch{ch} 内心动词（觉得/感觉到/意识到/想到/知道）密度 {d['tell_per_1k']}/千字（≥1.5 警戒）",
                "suggestion": "用动作/感官描写替代内心动词（'他停下来' 而非 '他觉得不对'）",
            })
        elif d["tell_per_1k"] >= 1.0:
            findings.append({
                "dimension": "tell_overuse",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "TELL_RATE_HIGH",
                "chapter": ch,
                "metric": {"count": d["tell_count"], "per_1k_chars": d["tell_per_1k"]},
                "message": f"ch{ch} 内心动词密度 {d['tell_per_1k']}/千字（建议 <1.0）",
                "suggestion": "诡秘风格 show 应远胜 tell，每千字内心动词建议 ≤1.0",
            })

    # ===== Finding 8: SENTENCE_PARTICLE_MONO 句末助词单一 =====
    for ch, d in per_chapter.items():
        dist = d.get("particle_dist") or {}  # 2026-05-29 复审修复 [C3]
        if dist.get("total", 0) >= 30 and dist.get("le", 0) > 0.55:
            findings.append({
                "dimension": "sentence_particle",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "SENTENCE_PARTICLE_MONO",
                "chapter": ch,
                "metric": {"le_ratio": round(dist["le"], 2), "total": dist["total"]},
                "message": f"ch{ch} 句末助词「了」占 {dist['le']:.0%}（>55%），「过/着/的」用得少",
                "suggestion": "末助词多样化：完成态用「过/已经」，进行态用「着/正在」，描述用「的」",
            })

    # ===== Finding 9: PRONOUN_ACTION_HIGH 「他+动作」+段首主语合计 =====
    for ch, d in per_chapter.items():
        combined = d["pronoun_action"] + d["para_protagonist_start"]
        if combined >= 45:
            findings.append({
                "dimension": "subject_action_mechanical",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "SUBJECT_ACTION_MECHANICAL",
                "chapter": ch,
                "metric": {"pronoun_action": d["pronoun_action"], "para_start": d["para_protagonist_start"], "combined": combined},
                "message": f"ch{ch} 「他+1字动作」({d['pronoun_action']}) + 段首主语({d['para_protagonist_start']}) = {combined}（≥45 警戒）",
                "suggestion": "句首主语机械化总和过高，需交替用 隐藏主语/动作前置/复合句 打破节奏",
            })
        elif combined >= 35:
            findings.append({
                "dimension": "subject_action_mechanical",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "SUBJECT_ACTION_HIGH",
                "chapter": ch,
                "metric": {"pronoun_action": d["pronoun_action"], "para_start": d["para_protagonist_start"], "combined": combined},
                "message": f"ch{ch} 「他+1字动作」+段首主语合计 {combined}（≥35 偏多）",
                "suggestion": "建议 ≤30",
            })

    # ===== Finding 10: CHARACTER_ROTATION_BREAK 角色出场频率断层（用 name_aliases） =====
    if len(per_chapter) >= 2:
        # 从人物卡读取所有非主角角色 + 其 aliases
        char_aliases_map: dict[str, list[str]] = {}
        chars_json = load_json(project_root / "_数据库" / "人物卡.json", {"characters": []})
        for c in chars_json.get("characters", []):
            if c.get("role") == "主角":
                continue
            primary = c.get("name") or c.get("id")
            if not primary:
                continue
            aliases = c.get("name_aliases", []) + [primary]
            if c.get("id") and c["id"] not in aliases:
                aliases.append(c["id"])
            char_aliases_map[primary] = aliases

        for primary, aliases in char_aliases_map.items():
            counts = []
            for ch, path in chapters:
                if use_ledger:
                    # 2026-05-29 cluster 化：用账本 char_mention_counts 替代逐章 text.count
                    mentions = ledger_char_mentions.get(ch, {})
                    total = max((mentions.get(a, 0) for a in aliases), default=0)
                else:
                    text = path.read_text(encoding="utf-8")
                    # 任一 alias 命中即计入，总数 = 主名出现次数（保守计）
                    total = max(text.count(a) for a in aliases)
                counts.append(total)
            # 检查相邻章是否有跳跃（前章 ≥5 次 → 后章 ≤ 0.2 倍）
            for i in range(1, len(counts)):
                prev, curr = counts[i-1], counts[i]
                if prev >= 5 and (curr == 0 or curr / prev <= 0.2):
                    ch_prev, _ = chapters[i-1]
                    ch_curr, _ = chapters[i]
                    findings.append({
                        "dimension": "character_rotation",
                        "severity": "advisory",
                        "gate_level": "advisory",
                        "code": "CHARACTER_ROTATION_BREAK",
                        "metric": {"character": primary, "aliases": aliases, "ch_prev": ch_prev, "count_prev": prev, "ch_curr": ch_curr, "count_curr": curr},
                        "message": f"角色「{primary}」ch{ch_prev}:{prev}次 → ch{ch_curr}:{curr}次（出场频率断崖式下降）",
                        "suggestion": "若是策略性退场，在 _changes.json 注明 offscreen 状态；否则后续章节至少提及一次维持存在感",
                    })

    # ===== v19.6 G12 段落退化检测（章内）=====
    # 2026-05-29 cluster 化：需整章正文，账本无对应字段 → cluster 模式跳过本检测
    for ch, path in ([] if use_ledger else chapters):
        text = path.read_text(encoding="utf-8")
        trend = scan_paragraph_length_trend(text)
        if trend.get("degradation"):
            findings.append({
                "dimension": "paragraph_degradation",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "PARAGRAPH_LENGTH_DEGRADATION",
                "chapter": ch,
                "metric": trend,
                "message": f"ch{ch} 末段平均长度 {trend['avg_last_third']} 字仅为首段 {trend['avg_first_third']} 字的 {trend['ratio_last_over_first']:.0%}",
                "suggestion": "章节后半段落骤短 = 虎头蛇尾；末段补充感官描写/动作 beat/环境锚点",
            })

    # ===== v19.6 G11 惯用语冷却期 =====
    if use_ledger:
        # 2026-05-29 cluster 化：用账本 idiom_hits 合成「命中过的惯用语」文本，复用同一冷却期算法
        chapter_texts = {
            ch: "".join(idm for idm, n in (ledger_idiom_hits.get(ch, {}) or {}).items() if n)
            for ch, _ in chapters
        }
    else:
        chapter_texts = {ch: path.read_text(encoding="utf-8") for ch, path in chapters}
    cooldown_violations = scan_idiom_cooldown_violations(chapter_texts, cooldown=5)
    for v in cooldown_violations:
        findings.append({
            "dimension": "idiom_cooldown",
            "severity": "advisory",
            "gate_level": "advisory",
            "code": "IDIOM_COOLDOWN_VIOLATION",
            "metric": {
                "idiom": v["idiom"],
                "chapters_used": v["all_chapters_used"],
                "violation_pairs": v["violation_pairs"],
                "cooldown_required": v["cooldown_required"],
            },
            "message": f"惯用语「{v['idiom']}」违反冷却期（{v['cooldown_required']} 章内不可重复），章节 {v['all_chapters_used']}",
            "suggestion": f"高强度惯用语应间隔 ≥{v['cooldown_required']} 章使用；当前重复对 {v['violation_pairs']}",
        })

    # ===== v19.2 后备防御 Finding 11-20 =====

    # Finding 11: PUNCTUATION_HALFWIDTH_LEAK
    for ch, d in per_chapter.items():
        p = d["punctuation"]
        if p["halfwidth_comma"] >= 3 or p["three_dot"] >= 1:
            findings.append({
                "dimension": "punctuation",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "PUNCTUATION_HALFWIDTH_LEAK",
                "chapter": ch,
                "metric": p,
                "message": f"ch{ch} 半角逗号紧贴中文 {p['halfwidth_comma']} 处 / 三点省略号 {p['three_dot']} 处",
                "suggestion": "中文文本应用全角「，」+「……」（不是 ,,, 或 ...）",
            })

    # Finding 12: METAPHOR_OVERUSE
    for ch, d in per_chapter.items():
        if d["metaphor_per_1k"] >= 3:
            findings.append({
                "dimension": "metaphor",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "METAPHOR_OVERUSE",
                "chapter": ch,
                "metric": {"count": d["metaphor_count"], "per_1k": d["metaphor_per_1k"]},
                "message": f"ch{ch} 类比比喻（宛如/犹如/仿佛/好似）{d['metaphor_per_1k']}/千字（≥3 警戒）",
                "suggestion": "翻译腔嫌疑，建议用具体动作/感官替代类比",
            })

    # Finding 13: CLICHE_AI_WORDS_CN
    for ch, d in per_chapter.items():
        if d["cliche_hits"]:
            findings.append({
                "dimension": "cliche_ai_cn",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "CLICHE_AI_WORDS_CN",
                "chapter": ch,
                "metric": d["cliche_hits"],
                "message": f"ch{ch} AI 套话命中: {d['cliche_hits']}",
                "suggestion": "用具体动作/物件替代抽象套话（'手指攥紧扶手' 而非 '心头一震'）",
            })

    # Finding 14: PROTAGONIST_NAME_OVERFLOW
    for ch, d in per_chapter.items():
        if d["name_density_per_100"] >= 3:
            findings.append({
                "dimension": "name_density",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "PROTAGONIST_NAME_OVERFLOW",
                "chapter": ch,
                "metric": {"per_100": d["name_density_per_100"]},
                "message": f"ch{ch} 主角名密度 {d['name_density_per_100']}/百字（≥3 偏高）",
                "suggestion": "用代词「他」替代部分主角名（中文 AI 写作典型坑）",
            })

    # Finding 15: OPENING_WARMUP
    for ch, d in per_chapter.items():
        if d["warmup_hits"]:
            findings.append({
                "dimension": "opening_warmup",
                "severity": "warning",
                "gate_level": "advisory",
                "code": "OPENING_WARMUP",
                "chapter": ch,
                "metric": {"hits": d["warmup_hits"]},
                "message": f"ch{ch} 章首 200 字命中 AI 暖场词: {d['warmup_hits']}",
                "suggestion": "禁用「众所周知/不得不说/在这个 X 的世界里」等 AI 文章开头套路",
            })

    # Finding 16: CAUSAL_HEURISTIC_LEAK
    for ch, d in per_chapter.items():
        if d["causal_per_1k"] >= 4:
            findings.append({
                "dimension": "causal",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "CAUSAL_HEURISTIC_LEAK",
                "chapter": ch,
                "metric": {"count": d["causal_count"], "per_1k": d["causal_per_1k"]},
                "message": f"ch{ch} 因果连接词「于是/因此/所以」{d['causal_per_1k']}/千字（≥4 警戒）",
                "suggestion": "AI 浅层因果推理标志，用动作过渡替代连接词",
            })

    # Finding 17: TIME_ANCHOR_DROP
    for ch, d in per_chapter.items():
        if d["time_anchor_drops"] >= 1:
            findings.append({
                "dimension": "time_anchor",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "TIME_ANCHOR_DROP",
                "chapter": ch,
                "metric": {"drops": d["time_anchor_drops"]},
                "message": f"ch{ch} 连续 1500 字无时间词的窗口 {d['time_anchor_drops']} 处",
                "suggestion": "至少每 1500 字一个时间锚点（早上/三点/三天后/翌日等）",
            })

    # Finding 18: MODIFIER_STACK
    for ch, d in per_chapter.items():
        if d["modifier_stack_sentences"] >= 3:
            findings.append({
                "dimension": "modifier_stack",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "MODIFIER_STACK",
                "chapter": ch,
                "metric": {"sentences": d["modifier_stack_sentences"]},
                "message": f"ch{ch} 单句副词/形容词叠加 {d['modifier_stack_sentences']} 句（≥3 偏多）",
                "suggestion": "避免「小心翼翼地、安静地走进昏暗的、压抑的房间」式堆叠",
            })

    # Finding 19: SENSORY_IMBALANCE_VISUAL
    for ch, d in per_chapter.items():
        vis = d["sensory_dist"].get("视觉", 0)
        if vis >= 0.75:
            findings.append({
                "dimension": "sensory",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "SENSORY_IMBALANCE_VISUAL",
                "chapter": ch,
                "metric": {k: round(v, 2) for k, v in d["sensory_dist"].items()},
                "message": f"ch{ch} 视觉感官占比 {vis:.0%}（≥75% 警戒）",
                "suggestion": "增加听觉/嗅觉/触觉描写比例（Sudowrite Show-not-Tell 原理）",
            })

    # Finding 20: DIALOGUE_STREAM_FLAT
    for ch, d in per_chapter.items():
        if d["dialogue_stream_max"] >= 6:
            findings.append({
                "dimension": "dialogue_stream",
                "severity": "advisory",
                "gate_level": "advisory",
                "code": "DIALOGUE_STREAM_FLAT",
                "chapter": ch,
                "metric": {"max_streak": d["dialogue_stream_max"]},
                "message": f"ch{ch} 连续纯对话段最长 {d['dialogue_stream_max']} 段无动作 beat 插入",
                "suggestion": "对话流水 ≥6 段时插入动作 beat（'他放下杯子' / '雨打在窗上'）",
            })

    # ===== 输出报告 =====
    out_dir = project_root / "_数据库" / ".cross_chapter_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_ts": ts,
        "protagonist": protagonist,
        "chapters_scanned": [ch for ch, _ in chapters],
        "per_chapter": {str(k): v for k, v in per_chapter.items()},
        "catchphrase_totals": dict(catchphrase_totals),
        "findings": findings,
        "summary": {
            "advisory": sum(1 for f in findings if f["severity"] == "advisory"),
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "total": len(findings),
        },
    }
    out_path = out_dir / f"scan_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 终端打印
    print(f"[cross_cluster_pattern_aggregate] 主角={protagonist} 扫描章节={[ch for ch, _ in chapters]}")
    print(f"  catchphrase 跨章合计: {dict(catchphrase_totals)}")
    print(f"  段首{protagonist} 各章: {[d['para_protagonist_start'] for d in per_chapter.values()]}")
    print(f"  dialogue tag 各章: {[d['dialogue_tag'] for d in per_chapter.values()]}")
    print(f"  body_reaction 各章: {[d['body_reaction'] for d in per_chapter.values()]}")
    print()
    print(f"=== 发现 {len(findings)} 项 (warning={report['summary']['warning']} / advisory={report['summary']['advisory']}) ===")
    for f in findings:
        ch_str = f"ch{f.get('chapter', '*')}"
        print(f"  [{f['severity'].upper()}] [{f['code']}] {ch_str} :: {f['message']}")
        print(f"     建议: {f['suggestion']}")
    print()
    print(f"报告: {out_path}")

    # 退出码
    if any(f["severity"] == "warning" for f in findings):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
