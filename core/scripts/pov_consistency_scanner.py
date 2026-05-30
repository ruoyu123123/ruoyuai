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

# 凝视/观察类动词（其后紧跟的角色名是「宾语/被观察者」，不是 POV 持有者）。
# 中文限知叙事核心陷阱：「林尘盯着王虎」里王虎是宾语，POV 仍属林尘。
# 旧版仅取「POV 动词前 30 字内任意角色名」→ 配角名常因离 POV 动词更近被误判为 dominant_pov。
GAZE_VERBS = ["盯着", "盯住", "看着", "望着", "瞪着", "瞅着", "打量着", "打量", "注视着", "注视",
              "凝视着", "凝视", "瞧着", "扫了", "扫向", "看向", "望向", "瞥了", "瞥向", "瞥见",
              "看了看", "看了一眼", "上下打量", "环顾", "审视着", "审视", "盯", "看着的"]

# 第三人称代词（承前指代 / 零指代信号）
PRONOUNS = ["他", "她", "它", "他们", "她们"]


def _find_subject_name(ctx: str, character_names: list[str]) -> tuple[str | None, bool]:
    """在 POV 动词左侧上下文 ctx 中定位「主语角色名」。

    返回 (name, is_explicit)：
      · name=显式主语角色名，is_explicit=True —— 找到可信主语
      · name=None, is_explicit=False —— 无显式主语（零指代 / 代词 / 仅有宾语位角色名）

    归因启发式（保守，宁可返回 None 让上层归主导视角，也不错报给宾语）：
      1. 收集 ctx 内所有角色名出现位置；
      2. 标记「宾语位」名字：紧跟在凝视/观察类动词后的名字（如「盯着王虎」中的王虎）——排除；
      3. 在剩余「非宾语位」名字里取**最靠近 POV 动词（即位置最靠右）**的那个作主语；
      4. 若 POV 动词左侧紧邻处先出现的是代词「他/她」（在任何角色名之后）→ 视为代词指代，返回
         (None, False) 让上层承前归主导视角；
      5. 全是宾语位名字 / 无名字 → (None, False)。
    """
    # 1. 找出所有角色名出现位置（同名取所有 occurrence）
    occurrences = []  # [(pos, end, name)]
    for name in character_names:
        if not name:
            continue
        idx = ctx.find(name)
        while idx != -1:
            occurrences.append((idx, idx + len(name), name))
            idx = ctx.find(name, idx + 1)

    # 2. 标记宾语位（紧跟凝视动词后）
    def _is_object_position(start: int) -> bool:
        # 名字前若紧贴一个凝视动词 → 宾语位
        prefix = ctx[max(0, start - 5):start]
        return any(prefix.endswith(g) for g in GAZE_VERBS)

    subject_candidates = [(s, e, n) for (s, e, n) in occurrences if not _is_object_position(s)]

    # 3. 代词承前判断：POV 动词最近的「他/她」是否比最近的主语候选名更靠右
    last_pron_pos = -1
    for pron in PRONOUNS:
        idx = ctx.rfind(pron)
        if idx > last_pron_pos:
            last_pron_pos = idx
    last_subject = max(subject_candidates, key=lambda t: t[0]) if subject_candidates else None
    last_subject_pos = last_subject[0] if last_subject else -1

    # 代词比最近主语名更靠近 POV 动词 → 承前指代，交上层归主导视角
    if last_pron_pos > last_subject_pos:
        return None, False

    if last_subject:
        return last_subject[2], True
    return None, False


def _last_explicit_subject_before(text: str, pos: int, names_sorted: list[str]) -> str | None:
    """返回 text[:pos] 中最靠后的「非宾语位」角色名（段落已确立主语，供代词承前指代）。

    扫全文非仅 POV 动词前窗——「王虎走上前。他觉得…」里王虎是前句主语，
    「他」应承前指代王虎，即便王虎所在句没有 POV 动词。
    """
    best_pos, best_name = -1, None
    head = text[:pos]
    for name in names_sorted:
        idx = head.find(name)
        while idx != -1:
            prefix = head[max(0, idx - 5):idx]
            is_object = any(prefix.endswith(g) for g in GAZE_VERBS)
            if not is_object and idx > best_pos:
                best_pos, best_name = idx, name
            idx = head.find(name, idx + 1)
    return best_name


def detect_pov_signal_holders(text: str, character_names: set[str],
                              protagonist: str | None = None) -> dict[str, int]:
    """统计每个角色 POV 信号词出现次数。

    v2 归因改进（2026-05-30 修 #7 POV 归因缺陷）：
      · 区分主语位 vs 宾语位——「林尘盯着王虎…觉得」归林尘不归王虎；
      · 零指代 / 代词承前 → 归段落已确立的主导视角（最近显式主语，默认主角）；
      · 主角在场且 POV 动词无显式他人主语 → 倾向归主角。
    保守原则：宁可少报（归主导/主角）也不错报方向（错归宾语配角）。
    """
    counts = {n: 0 for n in character_names}
    # 角色名按长度降序匹配（先匹配长别名，避免「林」抢「林尘」），并定序保证确定性
    names_sorted = sorted([n for n in character_names if n], key=lambda n: (-len(n), n))
    protag_in_text = bool(protagonist) and protagonist in text

    for m in POV_VERBS.finditer(text):
        start = max(0, m.start() - 30)
        ctx = text[start:m.start()]
        name, is_explicit = _find_subject_name(ctx, names_sorted)

        if is_explicit and name:
            counts[name] = counts.get(name, 0) + 1
        else:
            # 零指代 / 代词承前 / 仅宾语位名：
            #   先按代词承前找全文最近显式主语；找不到再退主角（主角在场时）。
            target = _last_explicit_subject_before(text, m.start(), names_sorted)
            if not target and protag_in_text:
                target = protagonist
            if target:
                counts[target] = counts.get(target, 0) + 1
            # 无任何线索（无主角无显式主语）→ 不计入任何人（保守不错报）
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
        counts = detect_pov_signal_holders(scene, character_names, protagonist=protag)
        # 确定性排序：信号数降序，平局按名字升序（character_names 是 set，必须显式定序）
        sorted_pov = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
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
    non_protag_scenes = [sp for sp in scene_povs if sp["dominant_pov"] != protag]
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
