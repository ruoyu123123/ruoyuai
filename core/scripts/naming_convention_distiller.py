"""naming_convention_distiller.py — v22 角色命名规范蒸馏器

从已蒸馏数据收集角色名（character_arcs / 衔接分析.character_continuity）
→ 去重 → 分析命名指纹：文化体系 / 姓氏 / 长度 / 音节 / 称谓后缀。

业界依据：
- 角色命名是作者风格指纹的最显性维度之一（玄幻 vs 都市 vs 西式异世界 命名差异极大）
- 仿写时如果角色名不像，第一眼就让读者察觉"这不是该作者写的"

输入：
    python naming_convention_distiller.py --project workspace/styles/<书名>

输出：
    workspace/styles/<书名>/naming_convention.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter


# 常见中文姓氏（约 100 个高频）
COMMON_CHINESE_SURNAMES = set("""
赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜
戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐
费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄
和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁
杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁锺徐丘骆高夏蔡田樊胡凌霍
万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程
嵇邢滑裴陆荣翁荀羊於惠甄麴家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧
""".strip())

# 网文大姓（Round 1 D 调研来源：知乎/CSDN 网文起名教程 - 网文 TOP 姓氏共识）
WEB_NOVEL_SURNAMES = set("萧叶方陈楚沈苏李秦林陆唐许周韩")

# 网文常用单字字库（Round 1 D）
WEB_NOVEL_NAME_CHARS = set("东南北易逸毅宣轩风枫渊运云尘辰阳寒墨玄凡天霄宇飞鸿羽华")

# 道家/玄幻常用字
DAOIST_FANTASY_CHARS = set("长生纯阳青云凌霄定虚守一道君神圣魔仙佛")

# 玄幻复姓（Round 1 D 调研：「慕容多如狗，东方遍地走」）
WEB_NOVEL_COMPOUND_SURNAMES = {"慕容", "上官", "欧阳", "司马", "诸葛", "夏侯", "独孤",
                                "东方", "西门", "皇甫", "尉迟", "公孙", "南宫", "宇文",
                                "轩辕", "百里", "令狐", "纳兰"}

# 西式异世界常见命名特征（音节）
WESTERN_SYLLABLE_HINTS = [
    "·", ".", "·",
    "斯", "尔", "克", "特", "兰", "莱", "雷", "森", "顿", "曼", "瑟", "维", "德",
    "Mr", "Miss", "Lord", "Lady", "Sir", "von",
]

# 称谓后缀
TITLE_SUFFIXES = [
    "先生", "小姐", "夫人", "大人", "陛下", "殿下", "阁下", "队长", "队员",
    "师傅", "师父", "师", "者", "侠", "仙", "尊", "王", "帝", "君", "公子",
]

# 缩略 / 复合姓名常见分隔符
NAME_SEPARATORS = re.compile(r"[\s\.·_\-—]+")

# 噪声标签前缀（剔除）
NOISE_PREFIXES = [
    "【", "[", "(", "（", "「", "『",
]


def normalize_name(raw: str) -> str:
    """归一化角色名：剥离括号备注、问号标签、求票后缀等噪声。"""
    if not raw:
        return ""
    # 剥括号和括号内内容
    s = re.sub(r"[（(\[【「『][^）)\]】」』]*[）)\]】」』]", "", raw)
    # 剥 / 之后部分（一名多写）
    s = re.split(r"[/／,，;；]", s)[0]
    # 剥首尾空白和标点
    s = s.strip(" \t-—·.·•'\"")
    return s


def detect_culture(name: str) -> str:
    """文化倾向：chinese / western_translit / fantasy / mixed / scifi / unknown。

    v22.naming.2 修正：
    - 不在常见姓氏表的纯中文短名 → western_translit（如「HeroC」「CharC1」是西式音译）
    - 首字常见中文姓氏 → chinese
    """
    if not name:
        return "unknown"
    # 含分隔符（HeroC_Full / CharC2_Full） → 西式音译
    if re.search(r"[·.•]", name):
        return "western_translit"
    # 全英文
    if re.match(r"^[A-Za-z][\w\-]*$", name):
        return "western"
    # 中英混合（SCP-173 / K3-赤铁）
    if re.search(r"[A-Z][a-z]?\d|^\d", name) or re.search(r"\d+", name):
        return "scifi"
    # 全中文短名
    if re.match(r"^[一-鿿]{1,5}$", name):
        first = name[0]
        # 首字是常见汉姓 → chinese 风格
        if first in COMMON_CHINESE_SURNAMES:
            return "chinese"
        # 称号词缀 → fantasy
        if any(s in name for s in ("帝", "君", "王", "尊", "祖", "圣", "神", "魔", "仙")):
            return "fantasy"
        # 含西式音节字（如「HeroC」含「克/莱」音译惯用字）→ western_translit
        translit_syllables = set("HeroC斯尔特德罗洛雷塞瑟伯赛兰玛丽娅琳奥维加贝兰蒂阿曼妮娜")
        if any(c in translit_syllables for c in name):
            return "western_translit"
        # 其他短名 → 默认 chinese（如「子衿」「青鸾」古风名）
        return "chinese"
    # 长中文（6+ 字）— 多为仿西/奇幻拼音
    if re.match(r"^[一-鿿]+$", name):
        return "fantasy"
    return "mixed"


def detect_title_suffix(name: str) -> str | None:
    """检测称谓后缀。"""
    for s in TITLE_SUFFIXES:
        if name.endswith(s):
            return s
    return None


def detect_surname(name: str) -> str | None:
    """检测中文姓氏（首字 / 首两字）。"""
    if not name or not re.match(r"[一-鿿]", name[0]):
        return None
    if name[0] in COMMON_CHINESE_SURNAMES:
        if len(name) >= 2 and name[:2] in {"欧阳", "司马", "上官", "诸葛", "皇甫", "尉迟", "公孙", "慕容"}:
            return name[:2]
        return name[0]
    return None


def collect_character_names(project: Path) -> list[str]:
    """从 character_arcs/ 优先；fallback 到 衔接分析/."""
    names = set()

    # ① character_arcs（去重已部分完成）
    ca_dir = project / "character_arcs"
    if ca_dir.exists():
        for f in ca_dir.glob("*_emotion_arc.json"):
            raw = f.stem.replace("_emotion_arc", "")
            n = normalize_name(raw)
            if n:
                names.add(n)

    # ② 衔接分析 fallback
    if not names:
        c_dir = project / "衔接分析"
        if c_dir.exists():
            for f in c_dir.glob("*continuity.json"):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                for cc in d.get("character_continuity", []) or []:
                    # 兼容 cc 可能是 dict 也可能是 str（某些蒸馏 schema 简化）
                    if isinstance(cc, dict):
                        n = normalize_name(cc.get("character", ""))
                    elif isinstance(cc, str):
                        n = normalize_name(cc)
                    else:
                        continue
                    if n and not _is_label_name(n):
                        names.add(n)

    # ③ 最后过滤标签型 / 噪声型
    return sorted(name for name in names if not _is_label_name(name))


# 排除标签型角色名（不是真实人名）
LABEL_NAME_PATTERNS = [
    r"小队$|群像|队员们|【|『|」",
    r"声部$|玩家.*$|NPC|嘉宾|来宾",
    r"^(萌妹|车速|策划书|学白学)$",   # 黑名单（饲养全人类风格的标签）
    r"等\d+人$|^.{0,3}诸",
    r"[（(].{4,}[)）]",  # 备注过长
]


def _is_label_name(name: str) -> bool:
    """启发式：标签型角色名（不是真实人名）。"""
    if not name or len(name) > 15:
        return True
    if name.startswith(("【", "[", "{", "'", "\"")):
        return True
    for p in LABEL_NAME_PATTERNS:
        if re.search(p, name):
            return True
    return False


def dedupe_aliases(names: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """把「HeroC」/「HeroC.CharC5」/「HeroC_Full」识别为同一人。"""
    canonical: list[str] = []
    alias_map: dict[str, list[str]] = {}
    seen_short: dict[str, str] = {}

    # 先按长度降序（长的优先作 canonical，短的作 alias）
    sorted_names = sorted(names, key=lambda n: (-len(n), n))
    for name in sorted_names:
        short = re.split(r"[·.•_\-—\s]", name)[0]
        if short and short in seen_short and short != name:
            alias_map.setdefault(seen_short[short], []).append(name)
        else:
            canonical.append(name)
            seen_short[short or name] = name

    return canonical, alias_map


def aggregate_naming_convention(names: list[str]) -> dict:
    """聚合命名指纹。"""
    if not names:
        return {"error": "no character names found"}

    canonical, alias_map = dedupe_aliases(names)

    n = len(canonical)
    culture_dist = Counter(detect_culture(name) for name in canonical)
    length_dist = Counter(len(name) for name in canonical)
    surnames = Counter(s for name in canonical if (s := detect_surname(name)))
    suffixes = Counter(s for name in canonical if (s := detect_title_suffix(name)))

    # 西式名分隔符使用率
    with_separator = sum(1 for name in canonical if re.search(r"[·.•]", name))
    # 复姓使用率（中文）
    chinese_names = [name for name in canonical if detect_culture(name) == "chinese"]
    compound_surnames = sum(1 for name in chinese_names
                            if len(name) >= 2 and name[:2] in {"欧阳", "司马", "上官", "诸葛", "皇甫", "尉迟", "公孙", "慕容"})

    # 平均长度（按文化分）
    by_culture_lens: dict[str, list[int]] = {}
    for name in canonical:
        c = detect_culture(name)
        by_culture_lens.setdefault(c, []).append(len(name))
    by_culture_avg_len = {c: round(sum(ls) / len(ls), 2) for c, ls in by_culture_lens.items() if ls}

    # 音节高频字（用于仿写时同风格起名）
    syllable_freq = Counter()
    for name in canonical:
        clean = re.sub(r"[·.•_\-—\s]", "", name)
        for c in clean:
            if "一" <= c <= "鿿":
                syllable_freq[c] += 1
    high_freq_syllables = [(c, n) for c, n in syllable_freq.most_common(30) if n >= 2]

    # 黄金示例（每种文化取 5 个）
    golden_samples_by_culture: dict[str, list[str]] = {}
    for c in ("chinese", "western", "fantasy", "scifi", "mixed"):
        examples = [name for name in canonical if detect_culture(name) == c][:5]
        if examples:
            golden_samples_by_culture[c] = examples

    # v22.4dim Round 1 D 应用：网文化指数
    web_novel_surname_hits = sum(1 for name in canonical
                                  if (s := detect_surname(name)) and s in WEB_NOVEL_SURNAMES)
    web_novel_compound_hits = sum(1 for name in canonical
                                   if len(name) >= 2 and name[:2] in WEB_NOVEL_COMPOUND_SURNAMES)
    web_novel_char_hits = sum(1 for name in canonical
                               for c in name if c in WEB_NOVEL_NAME_CHARS)
    daoist_char_hits = sum(1 for name in canonical
                            for c in name if c in DAOIST_FANTASY_CHARS)
    web_novel_index = round(
        (web_novel_surname_hits + web_novel_compound_hits * 2 + web_novel_char_hits * 0.5 + daoist_char_hits * 0.7)
        / max(n, 1), 3
    )

    return {
        "schema_version": "v22.naming.2",
        "total_unique_characters": n,
        "alias_groups": len(alias_map),
        "culture_distribution_pct": {k: round(v / n, 3) for k, v in culture_dist.items()},
        "primary_culture": max(culture_dist, key=culture_dist.get),
        "length_distribution": dict(sorted(length_dist.items())),
        "average_length_by_culture": by_culture_avg_len,
        "chinese_surname_top10": surnames.most_common(10),
        "chinese_compound_surname_count": compound_surnames,
        "western_separator_usage_pct": round(with_separator / n, 3),
        "title_suffix_distribution": dict(suffixes.most_common(10)),
        "high_freq_syllables_top20": high_freq_syllables[:20],
        "golden_samples_by_culture": golden_samples_by_culture,
        "web_novel_indicators_round1d_applied": {
            "_doc": "Round 1 D 调研依据：「网文大姓 + 字库 + 复姓滥用」三件套指标",
            "web_novel_index": web_novel_index,
            "web_novel_index_tier": "高度网文化" if web_novel_index > 0.5 else ("中等网文化" if web_novel_index > 0.2 else "低/无网文化"),
            "web_novel_surname_hits": web_novel_surname_hits,
            "web_novel_compound_surname_hits": web_novel_compound_hits,
            "web_novel_char_hits": web_novel_char_hits,
            "daoist_char_hits": daoist_char_hits,
        },
        "_doc": (
            f"作者命名指纹：主文化 {max(culture_dist, key=culture_dist.get)} "
            f"({culture_dist[max(culture_dist, key=culture_dist.get)]/n:.0%})；"
            f"平均长度 {round(sum(len(n) for n in canonical)/n, 2)} 字；"
            f"西式分隔符使用率 {round(with_separator/n, 2):.0%}"
        ),
        "_alias_map_preview": dict(list(alias_map.items())[:5]),
        "_metadata": {
            "distill_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "distiller_version": "v22.naming.1",
            "raw_name_count_before_dedup": len(names),
            "canonical_count_after_dedup": n,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="naming_convention_distiller v22 · 角色命名规范蒸馏")
    parser.add_argument("--project", required=True, help="风格库项目路径")
    args = parser.parse_args()

    project = Path(args.project)
    if not project.exists():
        print(f"[error] project not found: {project}", file=sys.stderr)
        sys.exit(2)

    names = collect_character_names(project)
    print(f"[info] {project.name}: 收集到 {len(names)} 个角色名（去重前）")

    result = aggregate_naming_convention(names)
    out_path = project / "naming_convention.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {out_path}")
    if "error" in result:
        print(f"     [warn] {result['error']} · 请先跑 character_arc_aggregator 或检查 衔接分析/")
        return
    print(f"     主文化: {result['primary_culture']} | 文化分布: {result['culture_distribution_pct']}")
    print(f"     高频音节 TOP5: {result['high_freq_syllables_top20'][:5]}")
    print(f"     去重: {result['_metadata']['raw_name_count_before_dedup']} → {result['_metadata']['canonical_count_after_dedup']}")


if __name__ == "__main__":
    main()
