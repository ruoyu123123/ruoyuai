"""title_style_distiller.py — v22 章节标题命名风格蒸馏器

从单章 JSON 的 title 字段聚合作者标题命名指纹（长度/词性/句式/标点/主题词 + 网文专项）。
对齐 gen_chapter_titles.py 的三档策略（normal 80% / mid 15% / high 5%），但用作者实际数据驱动而非硬编码。

业界依据：
- gen_chapter_titles.py 头部「2026-05 调研，《BookC》《饲养全人类》《十日终焉》《斩神》70 章样本」
  → 已确立"网文标题三档策略"，本脚本提供 per-book 数据校准
- LumberChunker (EMNLP 2024) — 同一作者风格指纹要 per-work 提取

输入：
    python title_style_distiller.py --project workspace/styles/<书名>

输出：
    workspace/styles/<书名>/title_style.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter


# 网文求票/求月票传统的副标题（剔除统计噪声）
NOISE_PATTERNS = [
    r"[（(]\s*[第二三四五]+更\s*[)）]",
    r"[（(]\s*求推荐票\s*[)）]",
    r"[（(]\s*求月票\s*[)）]",
    r"[（(]\s*求订阅\s*[)）]",
    r"[（(]\s*上架感言\s*[)）]",
    r"[（(]\s*感谢\s*[^)）]*[)）]",
    r"[（(]\s*为\s*[^)）]*[）)]",
]


# 标题"档位"分类（对齐 gen_chapter_titles.py 三档）
def classify_tier(length: int) -> str:
    if length <= 4:
        return "normal"
    if length <= 8:
        return "mid"
    return "high"


# 标题"结构类型"分类（启发式）
def classify_structure(title: str) -> str:
    """名词型 / 动作型 / 短语型 / 人物型 / 数字型 / 引用型"""
    if re.search(r"[一二三四五六七八九十百千\d]+(?:号|位|个|名|岁)?", title):
        if "—" in title or "_" in title or re.search(r"\d+[—-]\d+", title):
            return "编号型"   # 如 「研究"3—0782"」
        return "数字型"
    if "\"" in title or "「" in title or "『" in title:
        return "引用型"
    # 动作型（含动词字尾）
    action_chars = ("现", "看", "杀", "战", "归", "出", "入", "破", "醒", "降", "去", "来",
                    "斗", "上门", "见", "决战", "接触", "追", "逃", "藏", "改", "撞")
    for ac in action_chars:
        if ac in title:
            return "动作型"
    # 人物型（有"先生/小姐/师"等称谓 或 首字大写英文）
    if re.search(r"[A-Z][a-z]*先生|[A-Z][a-z]*小姐|大人$|师$|者$|王$|帝$", title):
        return "人物型"
    if re.search(r"^[A-Z][\w]+$", title):
        return "人物型"
    # 短语型（含动词中间）
    if re.search(r"[的之]", title):
        return "短语型"
    # 默认名词型
    return "名词型"


def clean_title(title: str) -> tuple[str, list[str]]:
    """剥离网文求票噪声副标题，返回 (clean_title, noise_tags)。"""
    noise_tags = []
    out = title
    for p in NOISE_PATTERNS:
        m = re.search(p, out)
        if m:
            noise_tags.append(m.group(0))
            out = re.sub(p, "", out).strip()
    return out, noise_tags


def has_punctuation(title: str) -> bool:
    """检测标题含特殊标点（"" / 「」 / —— / ？ / ！ / · 等）。"""
    return bool(re.search(r"[\"「」『』——\?\!？！·,，:：]", title))


def collect_titles(project: Path) -> list[dict]:
    """扫所有单章 JSON 的 title 字段（兼容 第N章.json / chN.json 两种命名）。"""
    out = []
    metrics_dir = project / "蒸馏进度"
    if not metrics_dir.exists():
        return out
    seen_chapters: set[int] = set()
    # 双 glob：第*.json + ch*.json（排除 _metrics）
    candidates = list(metrics_dir.glob("第*.json")) + [
        f for f in metrics_dir.glob("ch*.json")
        if not f.stem.endswith("_metrics") and not f.stem.endswith("_continuity")
    ]
    for f in sorted(candidates):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ch = d.get("chapter")
        title = (d.get("title") or "").strip()
        if not ch or not title:
            continue
        if ch in seen_chapters:
            continue  # 避免 第1章.json + ch1.json 双重计数
        seen_chapters.add(ch)
        clean, noise = clean_title(title)
        out.append({
            "chapter": ch,
            "title_raw": title,
            "title_clean": clean,
            "length": len(clean),
            "tier": classify_tier(len(clean)),
            "structure": classify_structure(clean),
            "has_punctuation": has_punctuation(clean),
            "noise_tags": noise,
        })
    return out


def aggregate_title_style(titles: list[dict]) -> dict:
    """聚合标题命名风格指纹。"""
    if not titles:
        return {"error": "no titles found"}

    n = len(titles)
    length_dist = Counter(t["length"] for t in titles)
    tier_dist = Counter(t["tier"] for t in titles)
    structure_dist = Counter(t["structure"] for t in titles)
    punct_count = sum(1 for t in titles if t["has_punctuation"])

    # 字频统计（剥噪声后）→ 出现 ≥ 3 次的高频字
    char_freq = Counter()
    for t in titles:
        for c in t["title_clean"]:
            if "一" <= c <= "鿿":  # CJK 字符
                char_freq[c] += 1
    high_freq_chars = [(c, n) for c, n in char_freq.most_common(30) if n >= 3]

    # 标题首字 / 末字模式
    first_chars = Counter(t["title_clean"][0] for t in titles if t["title_clean"])
    last_chars = Counter(t["title_clean"][-1] for t in titles if t["title_clean"])

    # tier 比例（与 gen_chapter_titles.py 假设的 80/15/5 对比）
    tier_pct = {k: round(v / n, 3) for k, v in tier_dist.items()}

    # 黄金示例（每个 tier 取 3 个最有代表性的）
    golden_samples = {}
    for tier in ("normal", "mid", "high"):
        examples = [t["title_clean"] for t in titles if t["tier"] == tier][:5]
        golden_samples[tier] = examples

    # 网文求票噪声占比
    noisy = sum(1 for t in titles if t["noise_tags"])
    noisy_pct = round(noisy / n, 3) if n else 0

    return {
        "schema_version": "v22.title.1",
        "total_titles": n,
        "length_stats": {
            "mean": round(sum(t["length"] for t in titles) / n, 2),
            "min": min(t["length"] for t in titles),
            "max": max(t["length"] for t in titles),
            "distribution": dict(sorted(length_dist.items())),
        },
        "tier_distribution_pct": tier_pct,
        "tier_recommended_compared_to_default_80_15_5": {
            "normal_deviation": round(tier_pct.get("normal", 0) - 0.80, 3),
            "mid_deviation": round(tier_pct.get("mid", 0) - 0.15, 3),
            "high_deviation": round(tier_pct.get("high", 0) - 0.05, 3),
            "_doc": "正值 = 本书超出默认占比；负值 = 不足。±0.05 内视为对齐",
        },
        "structure_distribution_pct": {k: round(v / n, 3) for k, v in structure_dist.items()},
        "punctuation_usage_pct": round(punct_count / n, 3),
        "high_freq_chars": high_freq_chars[:20],
        "first_char_top5": first_chars.most_common(5),
        "last_char_top5": last_chars.most_common(5),
        "golden_samples_per_tier": golden_samples,
        "web_novel_noise_pct": noisy_pct,
        "_doc": (
            f"作者标题指纹：平均 {round(sum(t['length'] for t in titles) / n, 1)} 字；"
            f"tier 分布 normal={tier_pct.get('normal', 0):.0%}/mid={tier_pct.get('mid', 0):.0%}/high={tier_pct.get('high', 0):.0%}；"
            f"主结构 {max(structure_dist, key=structure_dist.get)}；"
            f"标点率 {round(punct_count / n, 1):.0%}"
        ),
        "_metadata": {
            "distill_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "distiller_version": "v22.title.1",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="title_style_distiller v22 · 章节标题命名风格蒸馏")
    parser.add_argument("--project", required=True, help="风格库项目路径")
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"[error] project not found: {project}", file=sys.stderr)
        sys.exit(2)

    titles = collect_titles(project)
    print(f"[info] {project.name}: 收集到 {len(titles)} 个章节标题")

    result = aggregate_title_style(titles)
    out_path = project / "title_style.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {out_path}")
    if "error" in result:
        print(f"     [warn] {result['error']} · 单章 JSON 缺 title 字段，请检查蒸馏 schema")
        return
    print(f"     平均字数: {result['length_stats']['mean']} | tier: {result['tier_distribution_pct']}")
    print(f"     主结构: {result['structure_distribution_pct']}")
    print(f"     高频字 TOP5: {result['high_freq_chars'][:5]}")


if __name__ == "__main__":
    main()
