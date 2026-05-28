"""webnovel_bench_alignment.py — WebNovelBench 8 维评分映射（v19.6 G13 轻量版）

WebNovelBench arxiv 2505.14818 - 4000 部中文网文 + 8 维 LLM-as-judge：
1. Pacing 节奏 - 我们 pacing_analyzer / NARRATIVE_microten 部分覆盖
2. Plot Structure 情节结构 - 我们 plot_structure_scanner 覆盖
3. Character Depth 角色深度 - 我们 validator-repair 16 维含 nuanced_characters
4. Dialogue Quality 对话质量 - 我们 voice-keeper + STYLE_对话占比
5. World Building 世界构建 - 我们 world_keyword_hits + 世界观.json
6. Prose Quality 文笔 - 我们 validate_style + semantic_slop_scanner
7. Emotional Resonance 情感共鸣 - 我们 emotion_arc_analyzer
8. Originality 原创性 - 我们 anti-slop + cliche cooldown 部分覆盖

本脚本：把我们现有 audit/judge_reports 映射到 8 维，输出对照表。
不做 PCA+ECDF 分布拟合（成本高 ROI 低）。

用法：python webnovel_bench_alignment.py <project> --ch <N>
退出码: 0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


WEBNOVEL_8DIM = {
    "Pacing": {
        "source_scanners": ["pacing_analyzer", "NARRATIVE_microten", "PARAGRAPH_LENGTH_DEGRADATION"],
        "validator_16_dim": ["pacing"],
        "_doc": "节奏 - 微张力 / 段落退化 / 章节内部速度变化",
    },
    "Plot Structure": {
        "source_scanners": ["plot_structure_scanner", "cluster_blueprint_compliance"],
        "validator_16_dim": ["compelling_plot", "coherent", "tension_buildup"],
        "_doc": "情节结构 - STC 节拍 / try-fail / midpoint / 角色弧光",
    },
    "Character Depth": {
        "source_scanners": ["character_arc_state", "persona_drift"],
        "validator_16_dim": ["nuanced_characters", "voice_distinctiveness"],
        "_doc": "角色深度 - 弧光 / persona 一致性 / voice 差异化",
    },
    "Dialogue Quality": {
        "source_scanners": ["voice-keeper", "STYLE_对话占比", "DIALOGUE_TAG_MECHANIZATION", "DIALOGUE_STREAM_FLAT"],
        "validator_16_dim": ["dialogue_naturalness"],
        "_doc": "对话质量 - voice_pack 命中 / 标签机械化 / 流水",
    },
    "World Building": {
        "source_scanners": ["world_keyword_hits", "state_tracker"],
        "validator_16_dim": ["world_consistency"],
        "_doc": "世界构建 - 设定一致性 / 关键词触发注入命中",
    },
    "Prose Quality": {
        "source_scanners": ["validate_style", "semantic_slop_scanner", "MODIFIER_STACK", "CLICHE_AI_WORDS_CN"],
        "validator_16_dim": ["show_dont_tell", "sensory_immersion"],
        "_doc": "文笔 - 句法 / 语义 slop / 修饰堆叠 / 套话",
    },
    "Emotional Resonance": {
        "source_scanners": ["emotion_arc_analyzer", "EMOTION_DISCONTINUITY", "hook_strength_scanner"],
        "validator_16_dim": ["emotionally_engaging", "emotional_arc"],
        "_doc": "情感共鸣 - 弧线 / 断层 / 章末钩子",
    },
    "Originality": {
        "source_scanners": ["anti-slop", "IDIOM_COOLDOWN_VIOLATION", "OPENING_WARMUP", "METAPHOR_OVERUSE"],
        "validator_16_dim": ["originality"],
        "_doc": "原创性 - 词汇/惯用语冷却 / 模板暖场",
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    args = ap.parse_args()

    project_root = Path(args.project)

    print("=" * 70)
    print(f"📊 WebNovelBench 8 维 vs 我们检测体系映射")
    print("=" * 70)
    print()

    # 如果给了 --ch，尝试从 audit + judge_reports 抽对应分数
    audit = None
    judges = []
    if args.ch:
        ap_path = project_root / "_数据库" / ".audit" / f"ch_{args.ch:03d}_audit.json"
        if ap_path.exists():
            audit = json.loads(ap_path.read_text(encoding="utf-8"))
        jr_dir = project_root / "_数据库" / ".judge_reports"
        if jr_dir.is_dir():
            for f in jr_dir.glob(f"ch_{args.ch:03d}_*.json"):
                try:
                    judges.append(json.loads(f.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, ValueError):
                    pass

    for dim, info in WEBNOVEL_8DIM.items():
        print(f"### {dim}")
        print(f"  {info['_doc']}")
        print(f"  ➤ 映射 scanners: {info['source_scanners']}")
        print(f"  ➤ validator 16 维: {info['validator_16_dim']}")
        if args.ch and judges:
            # 从 validator-repair judge 抽 16 维评分
            for j in judges:
                if j.get("judge_id") == "validator-repair":
                    s16 = j.get("score_16dim", {})
                    for dim_key in info["validator_16_dim"]:
                        val = s16.get(dim_key)
                        if val is not None:
                            print(f"    📈 ch{args.ch}.{dim_key}: {val}/10")
        print()

    # 输出映射文件供用户参考
    out_path = project_root / "_数据库" / "webnovel_bench_mapping.json"
    out_path.write_text(json.dumps(WEBNOVEL_8DIM, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完整映射表已存: {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
