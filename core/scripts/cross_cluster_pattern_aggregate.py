"""汇总故事块文本模式，并输出可豁免的分布风险建议。

故事块摘要生产者调用本模块的纯文本度量函数；CLI 只消费
``故事块摘要.json.clusters[]`` 的预计算字段，不扫描物理章节。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cluster_summary_reader as csr  # noqa: E402


IDIOM_COOLDOWN_DICT = [
    "心如刀绞", "心如死灰", "魂飞魄散", "不寒而栗", "万箭穿心",
    "肝肠寸断", "撕心裂肺", "痛不欲生", "万念俱灰", "形同陌路",
    "兵败如山倒", "怒发冲冠", "黯然神伤", "悲痛欲绝", "肝胆俱裂",
]
CLICHE_AI_CN_FALLBACK = [
    "空气凝固", "空气仿佛凝固", "时间静止", "时间仿佛静止", "心头一震",
    "心头一紧", "心头一颤", "血液冻结", "血液凝固", "空气中弥漫",
    "心如刀绞", "魂飞魄散", "不寒而栗", "寒意袭来", "面色凝重",
    "脸色一变", "脸色铁青", "眼中闪过一丝", "嘴角勾起一抹",
]
WARMUP_WORDS = [
    "众所周知", "不得不说", "值得一提", "在这个", "不知不觉",
    "由此可见", "说起来", "其实", "顺便一提",
]
TIME_ANCHOR_WORDS = [
    "早上", "中午", "下午", "傍晚", "晚上", "凌晨", "夜里", "清晨",
    "半夜", "上午", "今天", "昨天", "明天", "周一", "周二", "周三",
    "周四", "周五", "周六", "周日", "点", "小时", "分钟", "天后",
]
SENSORY_TERMS = {
    "视觉": ["看", "见", "望", "瞄", "盯", "瞥", "色", "光", "亮", "影", "目", "瞳"],
    "听觉": ["听", "响", "叫", "声音", "吵", "静", "嗡", "咔", "啪", "叮"],
    "嗅觉": ["闻", "气味", "味道", "香", "臭", "腥", "味儿", "香水"],
    "味觉": ["尝", "咸", "甜", "苦", "辣", "酸", "甘"],
    "触觉": ["摸", "握", "抓", "按", "碰", "凉", "热", "烫", "冷", "滑", "硬", "软"],
}

_PER_CLUSTER_DEFAULTS = {
    "wc": 0, "catchphrase": {}, "para_protagonist_start": 0,
    "dialogue_tag": 0, "body_reaction": 0, "negation_desc": 0,
    "para_first_word_top1_pct": 0.0, "tell_count": 0, "tell_per_1k": 0.0,
    "particle_dist": {"le": 0, "guo": 0, "zhe": 0, "de": 0, "total": 0},
    "pronoun_action": 0,
    "punctuation": {"halfwidth_comma": 0, "three_dot": 0, "straight_quote": 0},
    "metaphor_count": 0, "metaphor_per_1k": 0.0, "cliche_hits": {},
    "name_density_per_100": 0.0, "warmup_hits": [], "causal_count": 0,
    "causal_per_1k": 0.0, "time_anchor_drops": 0,
    "modifier_stack_sentences": 0, "sensory_dist": {}, "dialogue_stream_max": 0,
}


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def get_protagonist(project_root: Path, override: str | None) -> str:
    if override:
        return override
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}) or {}
    for character in cards.get("characters") or []:
        if isinstance(character, dict) and character.get("role") == "主角":
            return character.get("name") or character.get("id") or "主角"
    return "主角"


def get_catchphrases(project_root: Path, protagonist: str) -> list[dict]:
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}) or {}
    for character in cards.get("characters") or []:
        if not isinstance(character, dict):
            continue
        if protagonist not in {character.get("name"), character.get("id")}:
            continue
        result = []
        for item in (character.get("voice_pack") or {}).get("catchphrase") or []:
            if isinstance(item, str):
                result.append({"phrase": item})
            elif isinstance(item, dict) and item.get("phrase"):
                result.append(item)
        return result
    return []


def scan_catchphrase(text: str, catchphrases: list[dict]) -> dict[str, int]:
    return {
        item["phrase"]: text.count(item["phrase"])
        for item in catchphrases
        if isinstance(item, dict) and item.get("phrase")
    }


def scan_paragraph_starts_with_protagonist(text: str, protagonist: str) -> int:
    return sum(
        paragraph.strip().startswith(protagonist)
        for paragraph in text.splitlines()
        if paragraph.strip()
    )


def scan_dialogue_tags(text: str) -> int:
    pronouns = re.findall(r"[他她][说想道]", text)
    named = re.findall(
        r"(?<![他她])[一-龥]{1,3}(?:说道|笑道|问道|答道|喝道|低声道|开口道)[：，]?"
        r"|(?<![他她])[一-龥]{1,3}[说问喊吼][：，]",
        text,
    )
    return len(pronouns) + len(named)


def scan_body_reactions(text: str) -> int:
    terms = ["瞳孔", "半秒", "按住", "后颈", "鸡皮疙瘩", "愣了", "怔了", "胃部一凉", "脚步一顿"]
    return sum(text.count(term) for term in terms)


def scan_negation_descriptions(text: str) -> int:
    return len(re.findall(r"不[大小远近早晚多少长短快慢高低深浅重轻]", text))


def scan_punctuation_halfwidth(text: str) -> dict[str, int]:
    return {
        "halfwidth_comma": len(re.findall(r",(?=[^\x00-\x7f])", text)),
        "three_dot": len(re.findall(r"(?<!\.)\.{3}(?!\.)", text)),
        "straight_quote": len(re.findall(r'"[^"]*"', text)),
    }


def scan_metaphor_overuse(text: str) -> tuple[int, float]:
    count = len(re.findall(r"宛如|犹如|仿佛|好似|恰似|如同", text))
    return count, count / max(len(text) / 1000, 1)


def _load_cliche_dict(project_root: Path) -> list[str]:
    words = set(CLICHE_AI_CN_FALLBACK)
    style = load_json(project_root / "_数据库" / "作者风格.json", {}) or {}
    for item in (style.get("anti_patterns") or {}).get("never_words") or []:
        word = item if isinstance(item, str) else (item.get("word") or item.get("text"))
        if word:
            words.add(re.split(r"[（(]", word)[0].strip())
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}) or {}
    for character in cards.get("characters") or []:
        if isinstance(character, dict):
            words.update((character.get("voice_pack") or {}).get("banned_phrases") or [])
    return sorted(word for word in words if isinstance(word, str) and len(word) >= 2)


def scan_cliche_ai_cn(text: str, dictionary: list[str] | None = None) -> dict[str, int]:
    return {
        word: text.count(word)
        for word in (dictionary or CLICHE_AI_CN_FALLBACK)
        if text.count(word)
    }


def scan_protagonist_name_density(text: str, protagonist: str) -> float:
    return text.count(protagonist) / max(len(text) / 100, 1)


def scan_opening_warmup(text: str, head_chars: int = 200) -> list[str]:
    return [word for word in WARMUP_WORDS if word in text[:head_chars]]


def scan_causal_heuristic(text: str) -> tuple[int, float]:
    count = sum(text.count(word) for word in ["于是", "因此", "所以", "因而"])
    return count, count / max(len(text) / 1000, 1)


def scan_time_anchor_drops(text: str, win_size: int = 1500, step: int = 500) -> int:
    return sum(
        not any(word in text[index:index + win_size] for word in TIME_ANCHOR_WORDS)
        for index in range(0, max(len(text) - win_size, 1), step)
    )


def scan_modifier_stack(text: str) -> int:
    return sum(
        len(sentence) > 10 and sentence.count("地") + sentence.count("的") >= 4
        for sentence in re.split(r"[。！？]", text)
    )


def scan_sensory_distribution(text: str) -> dict[str, float]:
    counts = {
        sense: sum(text.count(word) for word in words)
        for sense, words in SENSORY_TERMS.items()
    }
    total = sum(counts.values())
    return {sense: (count / total if total else 0.0) for sense, count in counts.items()}


def scan_dialogue_stream_flat(text: str) -> int:
    longest = current = 0
    for paragraph in [item.strip() for item in text.splitlines() if item.strip()]:
        quoted = any(mark in paragraph for mark in ['"', "“", "”", "「"])
        speaking = bool(re.search(
            r"[他她][说想道]|(?<![他她])[一-龥]{1,3}"
            r"(?:说道|笑道|问道|答道|喝道|低声道|开口道|[说问喊吼][：，])",
            paragraph,
        ))
        if quoted and (speaking or len(paragraph) < 80):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def scan_tell_overuse(text: str) -> tuple[int, float]:
    count = sum(text.count(word) for word in ["觉得", "感觉到", "意识到", "认为", "明白", "知道", "想到", "想着"])
    return count, count / max(len(text) / 1000, 1)


def scan_sentence_particle_distribution(text: str) -> dict[str, float]:
    counts = {
        "le": sum(text.count(mark) for mark in ["了。", "了，", "了？"]),
        "guo": sum(text.count(mark) for mark in ["过。", "过，"]),
        "zhe": sum(text.count(mark) for mark in ["着。", "着，"]),
        "de": sum(text.count(mark) for mark in ["的。", "的，"]),
    }
    total = sum(counts.values())
    return {**{key: (value / total if total else 0) for key, value in counts.items()}, "total": total}


def scan_pronoun_action(text: str) -> int:
    return len(re.findall(r"他[把在想说看走坐站抬伸点抓拈翻打按掏摸合扣笑听等顿]", text))


def scan_para_first_word_concentration(text: str, top_n: int = 50) -> float:
    paragraphs = [item.strip() for item in text.splitlines() if item.strip()][:top_n]
    if not paragraphs:
        return 0.0
    starts = Counter(item[:2] for item in paragraphs)
    return starts.most_common(1)[0][1] / len(paragraphs)


def scan_paragraph_length_trend(text: str) -> dict:
    paragraphs = [item.strip() for item in text.splitlines() if len(item.strip()) >= 5]
    if len(paragraphs) < 30:
        return {"degradation": False, "reason": "段落太少不评"}
    third = len(paragraphs) // 3
    first = sum(map(len, paragraphs[:third])) / third
    last = sum(map(len, paragraphs[-third:])) / third
    ratio = last / first if first else 1.0
    return {
        "degradation": ratio < 0.6,
        "avg_first_third": round(first, 1),
        "avg_last_third": round(last, 1),
        "ratio_last_over_first": round(ratio, 2),
    }


def scan_idiom_cooldown_violations(
    cluster_hits: dict[str, dict[str, int]], cooldown: int = 5
) -> list[dict]:
    ordered = list(cluster_hits)
    violations = []
    for idiom in IDIOM_COOLDOWN_DICT:
        used = [cluster_id for cluster_id in ordered if (cluster_hits[cluster_id] or {}).get(idiom)]
        pairs = []
        for previous, current in zip(used, used[1:]):
            gap = ordered.index(current) - ordered.index(previous)
            if gap < cooldown:
                pairs.append((previous, current, gap))
        if pairs:
            violations.append({
                "idiom": idiom,
                "all_clusters_used": used,
                "violation_pairs": pairs,
                "cooldown_required": cooldown,
            })
    return violations


def _merge_metrics(metrics: dict) -> dict:
    merged = dict(_PER_CLUSTER_DEFAULTS)
    for key, value in metrics.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _finding(cluster_id, code, severity, metric, message, suggestion, dimension="style"):
    return {
        "cluster_id": cluster_id, "dimension": dimension, "severity": severity,
        "gate_level": "advisory", "code": code, "metric": metric,
        "message": message, "suggestion": suggestion,
    }


def _scan_metrics(cluster_id: str, metrics: dict, protagonist: str) -> list[dict]:
    paragraph_start = metrics["para_protagonist_start"]
    dialogue = metrics["dialogue_tag"]
    subject_actions = metrics["pronoun_action"] + paragraph_start
    punctuation = metrics["punctuation"]
    sensory = metrics["sensory_dist"]
    particle = metrics["particle_dist"]
    rules = [
        (paragraph_start >= 25, "PARAGRAPH_SUBJECT_HIGH_ABS", "advisory", {"count": paragraph_start}, f"{cluster_id} 有 {paragraph_start} 段以「{protagonist}」开头", "轮换代词、动作、对话与场景起句"),
        (dialogue >= 18, "DIALOGUE_TAG_MECHANIZATION" if dialogue >= 25 else "DIALOGUE_TAG_HIGH", "warning" if dialogue >= 25 else "advisory", {"count": dialogue}, f"{cluster_id} 的说话标签共 {dialogue} 处", "用动作或环境反应替代部分机械说话标签"),
        (metrics["body_reaction"] >= 10, "BODY_REACTION_OVERUSE", "advisory", {"count": metrics["body_reaction"]}, f"{cluster_id} 的身体反应套话偏多", "减少同类身体反应模板重复"),
        (metrics["negation_desc"] >= 8, "NEGATION_DESC_OVERUSE", "advisory", {"count": metrics["negation_desc"]}, f"{cluster_id} 的否定式描写偏多", "用具体尺度、动作或名词替代泛化否定"),
        (metrics["para_first_word_top1_pct"] >= 0.35, "PARA_FIRST_WORD_CONCENTRATION", "advisory", {"top1_pct": round(metrics["para_first_word_top1_pct"], 2)}, f"{cluster_id} 的段首词集中度偏高", f"让段首在{protagonist}、代词、动作、对话与场景间轮换"),
        (metrics["tell_per_1k"] >= 1.0, "TELL_OVERUSE" if metrics["tell_per_1k"] >= 1.5 else "TELL_RATE_HIGH", "warning" if metrics["tell_per_1k"] >= 1.5 else "advisory", {"count": metrics["tell_count"], "per_1k": metrics["tell_per_1k"]}, f"{cluster_id} 的内心判断动词密度偏高", "用动作、感官和选择呈现判断过程"),
        (particle.get("total", 0) >= 10 and particle.get("le", 0) > 0.55, "SENTENCE_PARTICLE_MONO", "advisory", particle, f"{cluster_id} 的句末助词「了」占比偏高", "按语义轮换完成、持续和描述结构"),
        (subject_actions >= 35, "SUBJECT_ACTION_MECHANICAL" if subject_actions >= 45 else "SUBJECT_ACTION_HIGH", "warning" if subject_actions >= 45 else "advisory", {"combined": subject_actions}, f"{cluster_id} 的代词动作与主角段首合计偏高", "使用隐藏主语、动作前置和复合句"),
        (punctuation.get("halfwidth_comma", 0) >= 3 or punctuation.get("three_dot", 0) >= 1, "PUNCTUATION_HALFWIDTH_LEAK", "warning", punctuation, f"{cluster_id} 存在中文标点格式泄漏", "统一使用全角中文标点与六点省略号"),
        (metrics["metaphor_per_1k"] >= 3, "METAPHOR_OVERUSE", "warning", {"count": metrics["metaphor_count"], "per_1k": metrics["metaphor_per_1k"]}, f"{cluster_id} 的类比比喻密度偏高", "优先用具体动作和感官事实承载形象"),
        (bool(metrics["cliche_hits"]), "CLICHE_AI_WORDS_CN", "warning", metrics["cliche_hits"], f"{cluster_id} 命中套话词组", "用具体动作、物件和环境变化替代抽象套话"),
        (metrics["name_density_per_100"] >= 3, "PROTAGONIST_NAME_OVERFLOW", "advisory", {"per_100": metrics["name_density_per_100"]}, f"{cluster_id} 的主角名密度偏高", "指代清楚时使用代词或省略主语"),
        (bool(metrics["warmup_hits"]), "OPENING_WARMUP", "warning", {"hits": metrics["warmup_hits"]}, f"{cluster_id} 开头命中暖场套话", "直接从事件、动作或具体感官切入"),
        (metrics["causal_per_1k"] >= 4, "CAUSAL_HEURISTIC_LEAK", "advisory", {"count": metrics["causal_count"], "per_1k": metrics["causal_per_1k"]}, f"{cluster_id} 的显式因果连接词密度偏高", "让动作结果和信息反应自然承接因果"),
        (metrics["time_anchor_drops"] >= 1, "TIME_ANCHOR_DROP", "advisory", {"drops": metrics["time_anchor_drops"]}, f"{cluster_id} 存在长区间缺少时间锚点", "在场景转换或推进处补具体时间感"),
        (metrics["modifier_stack_sentences"] >= 3, "MODIFIER_STACK", "advisory", {"sentences": metrics["modifier_stack_sentences"]}, f"{cluster_id} 有多句修饰语堆叠", "把修饰语拆成可观察动作或环境事实"),
        (sensory.get("视觉", 0) >= 0.75, "SENSORY_IMBALANCE_VISUAL", "advisory", sensory, f"{cluster_id} 的视觉感官占比过高", "按场景需要补听觉、嗅觉或触觉信息"),
        (metrics["dialogue_stream_max"] >= 6, "DIALOGUE_STREAM_FLAT", "advisory", {"max_streak": metrics["dialogue_stream_max"]}, f"{cluster_id} 的连续纯对话过长", "插入有因果作用的动作或环境反馈"),
    ]
    return [
        _finding(cluster_id, code, severity, metric, message, suggestion)
        for active, code, severity, metric, message, suggestion in rules if active
    ]


def aggregate_patterns(project_root: Path, clusters: list[dict], protagonist: str, catchphrases: list[dict]):
    per_cluster = {
        cluster["cluster_id"]: _merge_metrics(cluster.get("pattern_metrics") or {})
        for cluster in clusters if isinstance(cluster.get("pattern_metrics"), dict)
    }
    findings = [item for cid, metrics in per_cluster.items() for item in _scan_metrics(cid, metrics, protagonist)]
    totals = Counter()
    for metrics in per_cluster.values():
        for phrase, count in (metrics.get("catchphrase") or {}).items():
            if isinstance(count, (int, float)):
                totals[phrase] += count
    total = sum(totals.values())
    if total and catchphrases:
        phrase, count = totals.most_common(1)[0]
        ratio = count / total
        if total >= 5 and ratio > 0.5:
            findings.append(_finding(clusters[-1]["cluster_id"], "CATCHPHRASE_UNIFICATION", "warning" if ratio > 0.7 else "advisory", {"top_phrase": phrase, "top_ratio": round(ratio, 2)}, f"「{phrase}」占口头禅总量 {ratio:.0%}", "按人物档语境轮换口头禅"))
    for violation in scan_idiom_cooldown_violations({c["cluster_id"]: c.get("idiom_hits") or {} for c in clusters}):
        findings.append(_finding(violation["all_clusters_used"][-1], "IDIOM_COOLDOWN_VIOLATION", "advisory", violation, f"惯用语「{violation['idiom']}」在冷却窗口内重复", "拉开高强度惯用语的故事块间隔"))
    return per_cluster, totals, findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--protagonist")
    parser.add_argument("--last-n", type=int, default=10)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    clusters = csr.get_clusters(project_root, last_n=args.last_n)
    if not clusters:
        print("[SKIP] 无已落账故事块")
        raise SystemExit(0)
    protagonist = get_protagonist(project_root, args.protagonist)
    per_cluster, totals, findings = aggregate_patterns(project_root, clusters, protagonist, get_catchphrases(project_root, protagonist))
    if not per_cluster:
        print("[SKIP] 故事块摘要缺少 pattern_metrics")
        raise SystemExit(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "scan_type": "pattern", "scan_ts": timestamp, "protagonist": protagonist,
        "clusters_scanned": list(per_cluster), "per_cluster": per_cluster,
        "catchphrase_totals": dict(totals), "findings": findings,
        "summary": {
            "advisory": sum(item["severity"] == "advisory" for item in findings),
            "warning": sum(item["severity"] == "warning" for item in findings),
            "total": len(findings),
        },
    }
    output_dir = project_root / "_数据库" / ".cross_cluster_scan"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"pattern_{timestamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cross_cluster_pattern_aggregate] 扫描 {len(per_cluster)} 个故事块，发现 {len(findings)} 项建议")
    print(f"报告: {output_path}")
    raise SystemExit(1 if findings else 0)


if __name__ == "__main__":
    main()
