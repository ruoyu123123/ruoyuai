#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07 指代消解（零指代/代词承前分支）接入 nn_coref_bridge
"""pov_consistency_scanner.py — POV 跨场景切换合法性检测

v2 cluster 化方案 Phase 3（2026-05-28）·
检测主第三人称限知视角在 cluster 不同场景的连贯性：
  · 默认 POV 是主角（`protagonist_lookup` 多源反查：人物卡主角位 → 角色弧线 → 事件簇 → 角色池）
  · 单场景内 POV 切换 → 警告（head-hopping）
  · 跨场景 POV 切换 → 必须在场景边界（\n---\n）

输出 issue code: POV_CROSS_SCENE_VIOLATION (advisory)

【NN 共指桥集成】detect_pov_signal_holders 在「无显式主语」分支（零指代/代词承前）
优先问 nn_coref_bridge.resolve_coreferences()：有解析结果 → 取 span 结束位置最靠近
POV 动词的一条 resolved_to 作为归因目标；桥无结果（RUOYU_NN_COREF 门控关闭默认状态）
→ 100% 回退本文件原有 _last_explicit_subject_before 正则 + 宾语位启发式逻辑。
显式主语（is_explicit=True，含宾语位排除）判定不受影响。

用法：python pov_consistency_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import protagonist_lookup  # noqa: E402


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


def _resolve_coref(text: str, known: list[str]) -> list[dict]:
    """调用 nn_coref_bridge 解析 text 中的代词/非命名指代 → 具体角色。

    RUOYU_NN_COREF 门控关闭（默认）/ 无结果 / 任何异常 → 返回 []，
    上层 detect_pov_signal_holders 100% 回退现有正则 + 宾语位启发式逻辑。
    """
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from nn_coref_bridge import resolve_coreferences
        return resolve_coreferences(text, known) or []
    except Exception as e:  # noqa: BLE001 — 桥失败绝不影响本 scanner 主流程
        print(f"[pov_consistency_scanner] 共指消解失败·回退正则："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return []


def _nearest_coref_target_before(coref_results: list[dict], pos: int) -> str | None:
    """coref_results 中 span 结束位置 <= pos 且最靠近 pos 的一条 resolved_to。

    用于零指代 / 代词承前场景：让 nn_coref_bridge 的消解结果优先于
    _last_explicit_subject_before 的正则回退（同类问题，桥通常更准）。
    """
    best_name, best_end = None, -1
    for r in coref_results:
        span = r.get("span")
        resolved = r.get("resolved_to")
        if not span or not resolved:
            continue
        end = span[1]
        if end <= pos and end > best_end:
            best_end, best_name = end, resolved
    return best_name


def detect_pov_signal_holders(text: str, character_names: set[str],
                              protagonist: str | None = None,
                              coref_stats: dict | None = None) -> dict[str, int]:
    """统计每个角色 POV 信号词出现次数。

    v2 归因改进（2026-05-30 修 #7 POV 归因缺陷）：
      · 区分主语位 vs 宾语位——「林尘盯着王虎…觉得」归林尘不归王虎；
      · 零指代 / 代词承前 → 归段落已确立的主导视角（最近显式主语，默认主角）；
      · 主角在场且 POV 动词无显式他人主语 → 倾向归主角。
    保守原则：宁可少报（归主导/主角）也不错报方向（错归宾语配角）。

    2026-07 NN 共指桥集成：零指代/代词承前分支优先问 nn_coref_bridge，
    有解析结果才用（门控关闭默认返回 [] → 逐字节回退原有正则逻辑）。
    coref_stats（可选，调用方传入 dict）：命中桥解析时 "resolved_count" 计数 +1，
    仅用于上层 scan() 汇总 source 诊断字段，不影响归因结果本身。
    """
    counts = {n: 0 for n in character_names}
    # 角色名按长度降序匹配（先匹配长别名，避免「林」抢「林尘」），并定序保证确定性
    names_sorted = sorted([n for n in character_names if n], key=lambda n: (-len(n), n))
    protag_in_text = bool(protagonist) and protagonist in text

    # 🔴 NN共指桥（优先）：门控关闭时返回 []·完全回退下面现有正则+宾语位启发式逻辑
    coref_results = _resolve_coref(text, names_sorted)

    for m in POV_VERBS.finditer(text):
        start = max(0, m.start() - 30)
        ctx = text[start:m.start()]
        name, is_explicit = _find_subject_name(ctx, names_sorted)

        if is_explicit and name:
            counts[name] = counts.get(name, 0) + 1
        else:
            # 零指代 / 代词承前 / 仅宾语位名：
            #   先问 nn_coref_bridge（有结果才用）；否则按代词承前找全文最近显式
            #   主语；再否则退主角（主角在场时）。
            target = _nearest_coref_target_before(coref_results, m.start()) \
                if coref_results else None
            if target and coref_stats is not None:
                coref_stats["resolved_count"] = coref_stats.get("resolved_count", 0) + 1
            if not target:
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
    for c in cards:
        name = c.get("name", "")
        if name:
            character_names.add(name)
            for a in (c.get("aliases") or []):
                character_names.add(a)

    # 主角走全仓唯一反查（人物卡主角位 → 角色弧线 → 事件簇 → 角色池），不做 role 精确匹配
    protag_detail = protagonist_lookup.resolve_protagonist_detail(project_root)
    protag = protag_detail["name"]
    if not protag:
        return {"_fatal": f"未找到主角（{protagonist_lookup.CANONICAL_ROLE_HINT}）"}
    character_names.add(protag)

    scenes = split_scenes(text)
    scene_povs = []  # [(scene_idx, dominant_pov, runner_up)]
    coref_stats = {"resolved_count": 0}
    for i, scene in enumerate(scenes):
        counts = detect_pov_signal_holders(scene, character_names, protagonist=protag,
                                           coref_stats=coref_stats)
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
        "protagonist_source": protag_detail["source"],
        "scenes_scanned": len(scenes),
        "scenes_with_pov_signal": len(scene_povs),
        "head_hopping_count": len(head_hopping),
        "non_protag_scenes_count": len(non_protag_scenes),
        "issues": issues,
        # 🔴 NN 共指桥集成（2026-07）：source 区分零指代/代词承前归因是否用到桥解析结果
        "coref_resolution_source": "nn_coref_bridge" if coref_stats["resolved_count"] > 0
        else "regex_fallback",
        "coref_resolved_mentions": coref_stats["resolved_count"],
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
