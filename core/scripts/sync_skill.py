#!/usr/bin/env python3
"""
sync_skill.py — 从风格 JSON + lessons_learned 自动生成 skill.md

用法：
  python sync_skill.py <风格JSON路径> [--lessons <lessons_learned.json>] [--output <skill.md路径>]

设计原则：
  - 单一数据源：JSON 是权威，skill.md 是自动生成的视图
  - 生成的 skill.md 可以直接注入给 Writer agent
  - 按场景类型组织规范，方便按需加载
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from datetime import date


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_rules(rules: list) -> str:
    if not rules:
        return "- (无)\n"
    return "".join(f"- {r}\n" for r in rules)


def fmt_list(items: list, prefix: str = "- ") -> str:
    if not items:
        return f"{prefix}(无)\n"
    return "".join(f"{prefix}{item}\n" for item in items)


def extract_lessons_by_level(lessons: list[dict]) -> dict:
    result = {"critical": [], "high": [], "medium": []}
    for item in lessons:
        level = item.get("level", "medium")
        name = item.get("name", item.get("id", "unknown"))
        lid = item.get("id", "")
        desc = f"[{lid}] {name}" if lid else name
        if level in result:
            result[level].append(desc)
        else:
            result["medium"].append(desc)
    return result


def generate_skill(style: dict, lessons: list[dict] = None, source_path: str = "") -> str:
    source = style.get("source", source_path or "unknown")
    total_ch = style.get("total_chapters", "?")
    analyzed = style.get("analyzed_chapters", "?")
    version = style.get("version", {})
    ver_num = version.get("current", "?") if isinstance(version, dict) else "?"
    total_words = style.get("total_words_analyzed", "?")

    quant = style.get("quantitative", {})
    sp = style.get("style_profile", {})
    narrative = sp.get("narrative", {})
    dialogue = sp.get("dialogue", {})
    description = sp.get("description", {})
    pacing = sp.get("pacing", {})
    vocab = sp.get("vocabulary", {})

    anti = style.get("anti_patterns", {})
    rules = style.get("writing_rules", [])
    must_have = style.get("must_have_per_chapter", {})

    sl = quant.get("sentence_length", {})
    pl = quant.get("paragraph_length", {})
    dr = quant.get("dialogue_ratio", {})
    cw = quant.get("chapter_words", {})
    punc = quant.get("punctuation_density_per_1000", {})
    fw = quant.get("function_word_fingerprint_per_1000", {})

    lessons_by_level = extract_lessons_by_level(lessons or [])

    sections = []
    sections.append(f"""---
name: {source.replace(' ', '-')}-style
description: 基于《{source}》蒸馏的写作风格 Skill（v{ver_num} 自动生成）
source: {source}
analyzed: {analyzed}/{total_ch} 章 · {total_words} 字
version: v{ver_num}
generated: {date.today().isoformat()}
---

# 写作风格 Skill：{source}（v{ver_num}）

## 身份
你是一位模仿《{source}》风格的写作者。你的文字必须读起来像原作者写的。
""")

    if rules:
        sections.append("## 硬性规则（不可违反）\n")
        sections.append(fmt_rules(rules))

    if lessons_by_level["critical"]:
        sections.append("## Critical 级规范（违反即重写）\n")
        sections.append(fmt_list(lessons_by_level["critical"]))

    if lessons_by_level["high"]:
        sections.append("## High 级规范（尽量遵守）\n")
        sections.append(fmt_list(lessons_by_level["high"]))

    sections.append(f"""## 定量约束（精确数字 · 由 style_analyzer.py 验证）
- 句子平均长度：{sl.get('mean', '?')} 字（std {sl.get('std', '?')}）
- 段落平均长度：{pl.get('mean_sentences', '?')} 句
- 对话占比：{dr.get('mean', '?')}（允许 ±15%）
- 章节字数：{cw.get('mean', '?')} 字（允许 ±500）
- 逗句比：{punc.get('comma_period_ratio', '?')}:1
- 省略号/千字：{punc.get('ellipsis', '?')}
""")

    if fw:
        sections.append("## 功能词指纹（每1000字目标频率）\n")
        pairs = [f'"{k}": {v}' for k, v in fw.items()]
        for i in range(0, len(pairs), 4):
            sections.append("- " + " | ".join(pairs[i:i+4]) + "\n")
        sections.append("\n")

    sections.append(f"""## 叙事约束
- 视角：{narrative.get('pov', '?')}
- 章节开头分布：{json.dumps(narrative.get('chapter_opening_distribution', {}), ensure_ascii=False)}
- 章节结尾分布：{json.dumps(narrative.get('chapter_ending_distribution', {}), ensure_ascii=False)}
- 句式节奏：{narrative.get('sentence_rhythm', '?')}
""")

    sections.append(f"""## 对话约束
- 标签分布：{json.dumps(dialogue.get('tag_distribution', {}), ensure_ascii=False)}
- 角色区分度：{dialogue.get('differentiation', '?')}
- 动作插入频率：{dialogue.get('action_inserts', '?')}
""")

    sections.append(f"""## 描写约束
- 环境描写密度：{description.get('env_density', '?')}
- 感官分布：{json.dumps(description.get('sensory_distribution', {}), ensure_ascii=False)}
- 心理描写占比：{description.get('psychology_ratio', '?')}
""")

    sections.append(f"""## 节奏约束
- 情绪模式：{pacing.get('emotion_pattern', '?')}
- 高潮频率：{pacing.get('climax_frequency', '?')}
- 悬念方式：{pacing.get('suspense_method', '?')}
""")

    never_words = anti.get("never_words", [])
    if never_words:
        sections.append("## 禁用词（该作者从不使用）\n")
        sections.append(", ".join(never_words[:30]) + "\n\n")

    never_sp = anti.get("never_sentence_patterns", [])
    if never_sp:
        sections.append("## 禁用句式\n")
        sections.append(fmt_list(never_sp[:15]))

    never_dt = anti.get("never_dialogue_tags", [])
    if never_dt:
        sections.append("## 禁用对话标签\n")
        sections.append(fmt_list(never_dt[:15]))

    sig = vocab.get("signature_phrases", [])
    if sig:
        sections.append("## 标志性表达\n")
        sections.append(fmt_list(sig[:15]))

    golden = style.get("golden_passages", {})
    if golden:
        sections.append("## 黄金段落库（按场景分类）\n")
        for ptype, passages in golden.items():
            if passages and passages != ["null"]:
                sections.append(f"\n### {ptype}\n")
                for p in passages[:3]:
                    sections.append(f"> {p}\n\n")

    evo = style.get("style_evolution", {})
    if evo.get("summary"):
        sections.append(f"## 风格演变\n{evo['summary']}\n")

    return "\n".join(sections)


def main():
    if len(sys.argv) < 2:
        print("用法: python sync_skill.py <风格JSON> [--lessons lessons.json] [--output skill.md]")
        sys.exit(1)

    style_path = Path(sys.argv[1])
    lessons_path = None
    output_path = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--lessons" and i + 1 < len(sys.argv):
            lessons_path = Path(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_path = Path(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    style = load_json(style_path)
    lessons = []
    if lessons_path and lessons_path.exists():
        data = load_json(lessons_path)
        if isinstance(data, list):
            lessons = data
        elif isinstance(data, dict) and "lessons" in data:
            lessons = data["lessons"]

    md = generate_skill(style, lessons, source_path=str(style_path.stem))
    if output_path:
        output_path.write_text(md, encoding="utf-8")
        print(f"skill.md synced to {output_path}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
