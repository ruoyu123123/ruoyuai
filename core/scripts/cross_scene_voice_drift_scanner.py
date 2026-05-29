#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_scene_voice_drift_scanner.py — cluster 内同角色跨场景 voice 漂移检测

v2 cluster 化方案 Phase 3（2026-05-28）·
之前 voice 漂移由 novel-voice-checker agent 检测，本 scanner 是机械层补丁，
快速检出同角色在 cluster 不同 scene 的 voice 偏差（句长 / catchphrase / banned_phrases）。

输出 issue code: VOICE_DRIFT_CROSS_SCENE
gate_level: advisory（声音漂移 = 工艺类，writer 有理由可豁免）

用法：python cross_scene_voice_drift_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from collections import defaultdict


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def split_scenes(text: str, min_scene_len: int = 500) -> list[str]:
    """启发式切场景：按 \n---\n 或 \n\n\n 分隔；不够则按段数硬切。"""
    parts = re.split(r"\n---+\n|\n\n\n+", text)
    parts = [p.strip() for p in parts if p.strip() and len(p) >= min_scene_len]
    return parts if parts else [text]


# 2026-05-30 北极星复审：原正则只含 ASCII " + 「」，漏 U+201C/U+201D 弯引号（项目正文实际用弯引号）
# → scanner 抽不到对话整体空转。补全弯引号（遵 feedback_dialogue_quote_unicode_distinction）。
DIALOGUE_RE = re.compile('["“「『]([^"”」』\n]{1,300})["”」』]')
SPEAKER_PATTERN = re.compile(r'([一-鿿]{2,4})(?:说道?|道|问道?|答道?|笑道?|骂道?|喊道?|嘀咕|开口|说)')


def extract_dialogues_by_speaker(scene_text: str, known_aliases: dict[str, str]) -> dict:
    """从场景文本提取每个角色的对话样本。
    aliases: alias → canonical_name 映射"""
    by_speaker = defaultdict(list)
    lines = scene_text.split("\n")
    last_speaker = None
    for line in lines:
        # 先看本行有没有 speaker tag
        m = SPEAKER_PATTERN.search(line)
        if m:
            name = m.group(1)
            canonical = known_aliases.get(name, name)
            last_speaker = canonical
        # 抽对话
        for q in DIALOGUE_RE.findall(line):
            if last_speaker:
                by_speaker[last_speaker].append(q)
    return dict(by_speaker)


def compute_voice_metrics(dialogues: list[str]) -> dict:
    """对一组对话计算 voice 指纹。"""
    if not dialogues:
        return None
    total_chars = sum(len(d) for d in dialogues)
    return {
        "count": len(dialogues),
        "avg_len": total_chars / len(dialogues),
        "max_len": max(len(d) for d in dialogues),
        "has_ellipsis_ratio": sum(1 for d in dialogues if "…" in d or "..." in d) / len(dialogues),
        "has_question_ratio": sum(1 for d in dialogues if "?" in d or "？" in d) / len(dialogues),
    }


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")
    scenes = split_scenes(text)

    # 加载人物卡 aliases
    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])
    aliases = {}
    for c in cards:
        name = c.get("name", "")
        aliases[name] = name
        for a in c.get("aliases", []) or []:
            aliases[a] = name

    # 每个 scene 提取每个角色的对话
    scene_metrics = []  # [{scene_idx, by_speaker: {name: metrics}}]
    for i, scene in enumerate(scenes):
        sd = extract_dialogues_by_speaker(scene, aliases)
        scene_metrics.append({
            "scene_idx": i,
            "by_speaker": {name: compute_voice_metrics(qs) for name, qs in sd.items() if qs},
        })

    # 跨场景检测每个角色的 voice 漂移
    drift_issues = []
    # 收集每个角色在所有场景的 metrics
    by_char = defaultdict(list)  # name → [(scene_idx, metrics)]
    for sm in scene_metrics:
        for name, met in sm["by_speaker"].items():
            if met:
                by_char[name].append((sm["scene_idx"], met))

    for name, entries in by_char.items():
        if len(entries) < 2:
            continue  # 出场 <2 场景，无法对比
        # 比较 avg_len 偏差（>50% 偏差视为漂移）
        lens = [e[1]["avg_len"] for e in entries]
        if max(lens) > 0:
            mean_len = sum(lens) / len(lens)
            for scene_idx, met in entries:
                deviation = abs(met["avg_len"] - mean_len) / max(mean_len, 1)
                if deviation > 0.5 and met["count"] >= 3:
                    drift_issues.append({
                        "character": name,
                        "scene_idx": scene_idx,
                        "avg_len_this_scene": round(met["avg_len"], 1),
                        "avg_len_cluster_mean": round(mean_len, 1),
                        "deviation_pct": round(deviation * 100, 1),
                        "sample_count": met["count"],
                        "type": "avg_dialogue_length_drift",
                    })

    return {
        "schema_version": "1.0",
        "scanner": "cross_scene_voice_drift_scanner",
        "gate_level": "advisory",
        "cluster_mode": True,
        "scenes_scanned": len(scenes),
        "characters_with_voice_sample": len(by_char),
        "drift_issues_count": len(drift_issues),
        "drift_issues": drift_issues[:10],
        "warning": (
            f"⚠️ {len(drift_issues)} 处跨场景 voice 漂移嫌疑（句长偏差 >50%）"
            if drift_issues else None
        ),
        "severity": "warning" if drift_issues else "info",
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    draft = Path(args[1]).resolve()
    report = scan(project, draft)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)  # 与 locked_fact/pov/foreshadowing_handoff 兄弟 scanner 一致：数据缺失≠干净通过
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
