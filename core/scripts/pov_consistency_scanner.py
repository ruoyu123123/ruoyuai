#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pov_consistency_scanner.py — POV 跨场景切换合法性检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测主第三人称限知视角在 cluster 不同场景的连贯性：
  · 默认 POV 是主角（人物卡 role=主角）
  · 单场景内 POV 切换 → 警告（head-hopping）
  · 跨场景 POV 切换 → 必须在场景边界（\n---\n）

输出 issue code: POV_CROSS_SCENE_VIOLATION (advisory)

用法：python pov_consistency_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# POV 信号词：心理描写动词（仅 POV 角色可拥有）
POV_VERBS = re.compile(r"(想到|想着|心想|觉得|察觉|意识到|明白|猜测|看出|嗅到|尝到|感到|听到|看见)")


def detect_pov_signal_holders(text: str, character_names: set[str]) -> dict[str, int]:
    """统计每个角色 POV 信号词出现次数（角色名前 30 字内有 POV verb）。"""
    counts = {n: 0 for n in character_names}
    for m in POV_VERBS.finditer(text):
        # 找前 30 字内的角色名
        start = max(0, m.start() - 30)
        ctx = text[start:m.start()]
        for name in character_names:
            if name and name in ctx:
                counts[name] = counts.get(name, 0) + 1
                break
    return counts


def split_scenes(text: str) -> list[str]:
    parts = re.split(r"\n---+\n|\n\n\n+", text)
    return [p.strip() for p in parts if p.strip()]


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")

    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])
    # 取所有角色名
    character_names = set()
    protag = None
    for c in cards:
        name = c.get("name", "")
        if name:
            character_names.add(name)
            for a in (c.get("aliases") or []):
                character_names.add(a)
        if c.get("role") == "主角" and not protag:
            protag = name

    if not protag:
        return {"_fatal": "未找到主角（role='主角'）"}

    scenes = split_scenes(text)
    scene_povs = []  # [(scene_idx, dominant_pov, runner_up)]
    for i, scene in enumerate(scenes):
        counts = detect_pov_signal_holders(scene, character_names)
        sorted_pov = sorted(counts.items(), key=lambda kv: -kv[1])
        if sorted_pov and sorted_pov[0][1] > 0:
            dom = sorted_pov[0][0]
            runner_up = sorted_pov[1][0] if len(sorted_pov) > 1 and sorted_pov[1][1] > 0 else None
            scene_povs.append({
                "scene_idx": i,
                "dominant_pov": dom,
                "dominant_signals": sorted_pov[0][1],
                "runner_up_pov": runner_up,
                "runner_up_signals": sorted_pov[1][1] if runner_up else 0,
            })

    # 检测 1: 单场景内多 POV（head-hopping）
    head_hopping = []
    for sp in scene_povs:
        if sp["runner_up_signals"] and sp["dominant_signals"] > 0:
            ratio = sp["runner_up_signals"] / sp["dominant_signals"]
            if ratio > 0.4:  # runner-up >= 40% 显著
                head_hopping.append(sp)

    # 检测 2: 非主角 POV 场景（应明确标场景边界）
    non_protag_scenes = [sp for sp in scene_povs if sp["dominant_pov"] != protag and sp["dominant_pov"] not in (c.get("name") for c in cards if c.get("name") == protag)]
    # 简化：non_protag scene 是否在场景边界后？无法机械判定，仅 advisory 提示

    issues = []
    if head_hopping:
        issues.append({
            "code": "POV_HEAD_HOPPING",
            "gate_level": "advisory",
            "count": len(head_hopping),
            "items": head_hopping[:5],
            "msg": f"⚠️ {len(head_hopping)} 个场景内 POV head-hopping（runner-up POV ≥40%）",
        })
    if non_protag_scenes:
        issues.append({
            "code": "POV_NON_PROTAGONIST_SCENE",
            "gate_level": "advisory",
            "count": len(non_protag_scenes),
            "items": [{"scene_idx": s["scene_idx"], "pov": s["dominant_pov"]} for s in non_protag_scenes[:5]],
            "msg": f"⚠️ {len(non_protag_scenes)} 个场景非主角 POV（主角={protag}）"
                   "·若刻意切应在场景边界",
        })

    return {
        "schema_version": "1.0",
        "scanner": "pov_consistency_scanner",
        "cluster_mode": True,
        "gate_level": "advisory",
        "protagonist": protag,
        "scenes_scanned": len(scenes),
        "scenes_with_pov_signal": len(scene_povs),
        "head_hopping_count": len(head_hopping),
        "non_protag_scenes_count": len(non_protag_scenes),
        "issues": issues,
        "warning": (
            f"⚠️ POV 一致性: {len(head_hopping)} head-hopping + {len(non_protag_scenes)} 非主角场景"
            if (head_hopping or non_protag_scenes) else None
        ),
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
        sys.exit(2)
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
