#!/usr/bin/env python3
"""manifest_budget.py — S1 manifest 分层 token 预算（2026-07-07 二轮移植）。

业界源：PlotPilot context_budget_allocator/policy（「约束是药不是饭」V9 减法改革 +
Phase 2 T0 动态阈值 40% / T3 最低保障 5% 实证参数）。出处：
research/open_source_writing_systems_round2.md S1。

两层职责（严格分离·零回归优先）：

1. **分层记账（始终开·纯元数据）**：给 manifest 每个顶层注入段归 tier——
   - T0 = 硬约束/契约类（hard_constraints / must_read / 预检 / 揭秘契约 / 信息隔离 masking）
   - T1 = 当前 cluster 创作载荷（brief / storyboard / 场景卡 / 作者风格骨 / 蒸馏参考）
   - T2 = 状态库（人物 / 世界 / 关系 / 道具 / 阵营 / 时间线快照）
   - T3 = 长程记忆（RAG / memory 检索 / 历史选择性召回）
   - META = 身份与机器元数据（不参与预算与裁剪）
   产出 manifest["budget_report"]：各段 (tier, est_bytes, est_cjk, share) + tier 汇总 +
   T0 占比。T0 占比 > 20%（env 可调）记 constraint_share_warning——只观测不裁
   （「过多强制内容 → 注意力坍塌」的观测起点，等真机数据再决定是否收紧）。

2. **分层裁剪（只在触发既有 size 守卫时生效）**：manifest 序列化体积 >= 既有硬上限
   （BUDGET_HARD_KB=100·与 build_manifest.main 的 v19.4 守卫同阈值同口径）才启动，
   把旧「打印分级裁剪建议给人看」升级为可执行的按 tier/priority 智能裁剪：
   - 裁剪顺序：T3（牺牲位·留 5% 最低保障地板）→ T2 → T1；T0/META 不进低层裁剪队列
   - T3 触底 → 剩余超额转 T2 回收（对应 PlotPilot「T3 最低保障：从 T2 回收配额」）
   - T0 自身超硬上限 40%（防约束无限膨胀挤占叙事载荷）→ 裁 T0 自身到上限内
   - 每段先走 manifest_compress 无损压缩（删开发者注释/截长列表长串），仍不够才整段
     置 stub；每次裁剪记 budget_report.compression_log（段名/tier/裁前后大小/理由）
   - **不触发守卫 = 所有注入段逐字节不变**（只新增 budget_report 元数据一键）

纪律：本模块不新增任何触发条件（不主动截断 context）；预算参数 env 可覆盖：
  MANIFEST_BUDGET_HARD_KB      裁剪触发阈值 KB（默认 100·= 既有硬守卫）
  MANIFEST_BUDGET_T0_MAX_RATIO T0 硬上限占比（默认 0.40）
  MANIFEST_BUDGET_T3_MIN_RATIO T3 最低保障占比（默认 0.05）
  MANIFEST_BUDGET_T0_WARN_RATIO T0 观测告警占比（默认 0.20·advisory 只记录）
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TIER_T0 = "T0"
TIER_T1 = "T1"
TIER_T2 = "T2"
TIER_T3 = "T3"
TIER_META = "META"

# 低层裁剪顺序（牺牲位在前）；T0 只受自身 40% 硬上限约束，META 永不裁。
TRIM_TIER_ORDER = (TIER_T3, TIER_T2, TIER_T1)

# 预算元数据自身的键（不参与记账/裁剪）
BUDGET_META_KEYS = {"budget_report"}

# 小于该字节数的段不值得裁（stub 本身也有体积）
MIN_TRIMMABLE_BYTES = 64

DEFAULT_HARD_KB = 100.0          # = build_manifest.main v19.4 BUDGET_HARD_KB（同口径）
DEFAULT_T0_MAX_RATIO = 0.40      # PlotPilot Phase 2 T0 动态阈值
DEFAULT_T3_MIN_RATIO = 0.05      # PlotPilot Phase 2 T3 最低保障
DEFAULT_T0_WARN_RATIO = 0.20     # PlotPilot V9「约束是药不是饭」目标占比（advisory 观测）

# ============ 注入段 → tier 归类（单一真理源·新增顶层段必须在此归类，
# tests/test_manifest_budget.py::test_tier_classification_full_coverage 会测红） ============
SECTION_TIERS: dict[str, str] = {
    # --- META：身份/机器元数据（不参与预算与裁剪） ---
    "chapter": TIER_META,
    "project": TIER_META,
    "generated_at": TIER_META,
    "writer_mode": TIER_META,
    "database_coverage": TIER_META,
    "_cache_layout": TIER_META,
    "_subsystem_consumption_audit": TIER_META,
    # --- T0：硬约束/契约类 ---
    "preflight": TIER_T0,
    "must_read": TIER_T0,
    "hard_constraints": TIER_T0,
    "post_write_checks": TIER_T0,
    "instructions_for_subagent": TIER_T0,
    "instructions": TIER_T0,               # preflight-fail 早退形态
    "_critical_summary": TIER_T0,
    "style_directive": TIER_T0,            # 「本章必须显式应用」的硬性风格指令
    "foreshadowing_summary": TIER_T0,      # 到期伏笔契约计数
    "will_learn_due_this_ch": TIER_T0,
    "pending_secrets_to_reveal": TIER_T0,
    "scene_character_knowledge": TIER_T0,  # per-character 负向 masking（防穿帮契约）
    # --- T1：当前 cluster 创作载荷（brief/storyboard/场景卡/作者风格骨） ---
    "volume": TIER_T1,
    "active_characters": TIER_T1,
    "active_character_cards": TIER_T1,
    "event_cluster_context": TIER_T1,
    "storyteller_directive": TIER_T1,
    "appraisal_directive": TIER_T1,
    "fate_dice_hint": TIER_T1,
    "active_offscreen_actions": TIER_T1,
    "active_clocks": TIER_T1,
    "scene_rule_matched": TIER_T1,
    "arc_template": TIER_T1,
    "main_character_arc_stage": TIER_T1,
    "open_dramatic_questions": TIER_T1,
    "hub_directive": TIER_T1,
    "research_cache_ref": TIER_T1,
    "rolling_style_anchor": TIER_T1,
    "recent_openings": TIER_T1,
    "prev_judge_findings": TIER_T1,
    "knowledge_gap_signature": TIER_T1,
    "author_style_fingerprint": TIER_T1,
    "author_rhythm_signature": TIER_T1,
    "narrative_function_sequence": TIER_T1,
    "author_decision_principles": TIER_T1,
    "genre_pack_directives": TIER_T1,
    "deep_writing_dims": TIER_T1,
    "emotion_body_topography_hint": TIER_T1,
    "focalization_matrix": TIER_T1,
    "distill_continuity_template": TIER_T1,
    "distill_voice_packs_reference": TIER_T1,
    "distill_golden_few_shot": TIER_T1,
    "title_style": TIER_T1,
    "naming_convention": TIER_T1,
    "main_character_arcs": TIER_T1,
    "position_effect_template": TIER_T1,
    "genre_baseline_diff": TIER_T1,
    "motif_recurrence_directive": TIER_T1,
    # --- T2：状态库（人物/世界/关系/道具/阵营/时间线快照） ---
    "active_cast": TIER_T2,
    "cluster_actant_state": TIER_T2,
    "active_fate_events": TIER_T2,
    "world_state_snapshot": TIER_T2,
    "location_atmosphere": TIER_T2,
    "protagonist_stress": TIER_T2,
    "active_aspects": TIER_T2,
    "ensemble_layer": TIER_T2,
    "user_preferences_v21": TIER_T2,
    "relevant_heuristics": TIER_T2,
    "character_moves": TIER_T2,
    "throughlines": TIER_T2,
    "reader_preferences": TIER_T2,
    "active_relationships": TIER_T2,
    "faction_standings_snapshot": TIER_T2,
    "world_keyword_hits": TIER_T2,
    "triggerable_events": TIER_T2,
    "relationships_loaded": TIER_T2,
    "items_loaded": TIER_T2,
    "time_snapshot": TIER_T2,
    "character_positions": TIER_T2,
    "debt_ledger_snapshot": TIER_T2,
    "sagging_middle_snapshot": TIER_T2,
    # --- T3：长程记忆（RAG/memory/历史选择性召回·牺牲位但有 5% 地板） ---
    "rag_relevant_chapters": TIER_T3,
    "memory_search_results": TIER_T3,
    "selective_history_retrieval": TIER_T3,
    # S10 消费端（2026-07-07）：已闭合卷卷级摘要（Ex3 金字塔·历史卷换粒度替代截断）
    "volume_summaries_digest": TIER_T3,
}

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


# ============ 基础度量 ============

def _dumps(obj) -> str:
    """与 build_manifest.save_json 完全同口径的序列化（体积口径必须一致）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _size_bytes(obj) -> int:
    return len(_dumps(obj).encode("utf-8"))


def _cjk_count(obj) -> int:
    return len(_CJK_RE.findall(_dumps(obj)))


def section_tier(key: str) -> str | None:
    """段名 → tier；未归类返回 None（记账时按 T2 兜底 + 进 unclassified 名单）。"""
    return SECTION_TIERS.get(key)


# ============ 配置（调用时读 env·测试可覆盖） ============

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_config() -> dict:
    hard_kb = _env_float("MANIFEST_BUDGET_HARD_KB", DEFAULT_HARD_KB)
    return {
        "hard_kb": hard_kb,
        "hard_bytes": int(hard_kb * 1024),
        "t0_max_ratio": _env_float("MANIFEST_BUDGET_T0_MAX_RATIO", DEFAULT_T0_MAX_RATIO),
        "t3_min_ratio": _env_float("MANIFEST_BUDGET_T3_MIN_RATIO", DEFAULT_T3_MIN_RATIO),
        "t0_warn_ratio": _env_float("MANIFEST_BUDGET_T0_WARN_RATIO", DEFAULT_T0_WARN_RATIO),
    }


# ============ 记账层（始终开·纯元数据） ============

def build_budget_report(manifest: dict, cfg: dict, *, triggered: bool,
                        compression_log: list) -> dict:
    sections: dict[str, dict] = {}
    tier_bytes: dict[str, int] = {t: 0 for t in (TIER_T0, TIER_T1, TIER_T2, TIER_T3, TIER_META)}
    unclassified: list[str] = []
    total_bytes = 0
    total_cjk = 0
    for key, value in manifest.items():
        if key in BUDGET_META_KEYS:
            continue
        tier = section_tier(key)
        if tier is None:
            unclassified.append(key)
            tier = TIER_T2  # 兜底记账；覆盖测试会把漏归类测红
        est_bytes = _size_bytes(value) + len(key.encode("utf-8")) + 6  # 键名+引号冒号逗号开销
        est_cjk = _cjk_count(value)
        sections[key] = {"tier": tier, "est_bytes": est_bytes, "est_cjk": est_cjk}
        tier_bytes[tier] += est_bytes
        total_bytes += est_bytes
        total_cjk += est_cjk
    for key, rec in sections.items():
        rec["share"] = round(rec["est_bytes"] / total_bytes, 4) if total_bytes else 0.0
    tier_totals = {
        t: {"bytes": b, "share": round(b / total_bytes, 4) if total_bytes else 0.0}
        for t, b in tier_bytes.items()
    }
    t0_share = tier_totals[TIER_T0]["share"]
    warning = None
    if t0_share > cfg["t0_warn_ratio"]:
        warning = (
            f"T0 硬约束/契约段占比 {t0_share:.0%} > {cfg['t0_warn_ratio']:.0%}"
            "（PlotPilot V9「约束是药不是饭」：过多强制内容 → 注意力坍塌，"
            "AI 从写故事变成满足约束）。advisory 只观测不裁·真机数据积累后再决定是否收紧"
        )
    return {
        "_doc": (
            "S1 manifest 分层 token 预算（PlotPilot context_budget_allocator 移植）。"
            "记账始终开；分层裁剪只在体积 >= 既有硬守卫(BUDGET_HARD_KB)时生效，"
            "裁序 T3(留5%地板)→T2→T1，T0 只受自身 40% 硬上限约束，META 永不裁。"
        ),
        "total_bytes": total_bytes,
        "total_cjk": total_cjk,
        "sections": sections,
        "tier_totals": tier_totals,
        "t0_share": t0_share,
        "constraint_share_warning": warning,
        "unclassified_sections": sorted(unclassified),
        "budget": {
            "hard_kb": cfg["hard_kb"],
            "t0_max_ratio": cfg["t0_max_ratio"],
            "t3_min_ratio": cfg["t3_min_ratio"],
            "t0_warn_ratio": cfg["t0_warn_ratio"],
            "triggered": triggered,
        },
        "compression_log": compression_log,
    }


# ============ 裁剪层（只在触发既有 size 守卫时生效） ============

def _stub_for(value, tier: str, original_bytes: int):
    if isinstance(value, str):
        return f"[budget_trimmed:{original_bytes}B]"
    return {
        "_budget_trimmed": True,
        "_tier": tier,
        "_original_bytes": original_bytes,
        "_reason": "manifest 超既有硬守卫·低优先级段整段让位（内容以数据库原文件为准）",
    }


def _compress_section(value):
    """无损压缩单段（复用 manifest_compress：删开发者注释/空值/截长列表长串）。
    depth=1：段值不是顶层 manifest，避开 DROP_TOP_LEVEL_KEYS 顶层剥除逻辑。"""
    import manifest_compress
    return manifest_compress.compress(value, 1, {})


def _tier_sections(manifest: dict, tier: str) -> list[tuple[str, int]]:
    """该 tier 下 (段名, est_bytes) 列表·按体积降序（大段先裁）·同体积按名序（确定性）。"""
    out = []
    for key, value in manifest.items():
        if key in BUDGET_META_KEYS:
            continue
        if (section_tier(key) or TIER_T2) != tier:
            continue
        out.append((key, _size_bytes(value)))
    out.sort(key=lambda kv: (-kv[1], kv[0]))
    return out


def _tier_total_bytes(manifest: dict, tier: str) -> int:
    return sum(b for _, b in _tier_sections(manifest, tier))


def _trim_section(manifest: dict, key: str, tier: str, action: str,
                  reason: str, log: list, *, stub: bool) -> int:
    """裁一段（compress 或 stub）·记 compression_log·返回节省字节数。"""
    value = manifest[key]
    if isinstance(value, dict) and value.get("_budget_trimmed"):
        return 0  # 已 stub 的段不再动（防重复裁剪/破坏 stub 结构）
    if isinstance(value, str) and value.startswith("[budget_trimmed:"):
        return 0
    before = _size_bytes(value)
    new_value = _stub_for(value, tier, before) if stub else _compress_section(value)
    after = _size_bytes(new_value)
    if after >= before:
        return 0  # 无收益不动（保持逐字节不变）
    manifest[key] = new_value
    log.append({
        "section": key, "tier": tier, "action": action,
        "before_bytes": before, "after_bytes": after, "reason": reason,
    })
    return before - after


def _trim_t0_to_cap(manifest: dict, cfg: dict, log: list) -> None:
    """T0 硬上限保护：T0 总量 > hard_bytes*40% → 裁 T0 自身（先 compress 后 stub·大段先）。"""
    cap = int(cfg["hard_bytes"] * cfg["t0_max_ratio"])
    if _tier_total_bytes(manifest, TIER_T0) <= cap:
        return
    reason = (f"T0 硬上限保护：T0 超总预算 {cfg['t0_max_ratio']:.0%}"
              "（防约束无限膨胀挤占叙事载荷·PlotPilot Phase 2）")
    for stub in (False, True):
        for key, size in _tier_sections(manifest, TIER_T0):
            if _tier_total_bytes(manifest, TIER_T0) <= cap:
                return
            if size < MIN_TRIMMABLE_BYTES:
                continue
            _trim_section(manifest, key, TIER_T0,
                          "t0_cap_stub" if stub else "t0_cap_compress",
                          reason, log, stub=stub)


def _trim_lower_tiers(manifest: dict, target_bytes: int, cfg: dict, log: list) -> None:
    """低优先级分层裁剪：T3（留 5% 地板）→ T2 → T1，先 compress 后 stub，大段先裁。"""
    t3_floor = int(cfg["hard_bytes"] * cfg["t3_min_ratio"])
    floor_logged = False
    for tier in TRIM_TIER_ORDER:
        for stub in (False, True):
            for key, size in _tier_sections(manifest, tier):
                if _size_bytes(manifest) <= target_bytes:
                    return
                if size < MIN_TRIMMABLE_BYTES:
                    continue
                if tier == TIER_T3 and _tier_total_bytes(manifest, TIER_T3) <= t3_floor:
                    if not floor_logged:
                        log.append({
                            "section": "(tier T3)", "tier": TIER_T3, "action": "t3_floor_hold",
                            "before_bytes": _tier_total_bytes(manifest, TIER_T3),
                            "after_bytes": _tier_total_bytes(manifest, TIER_T3),
                            "reason": (f"T3 最低保障 {cfg['t3_min_ratio']:.0%} 触底：长程记忆不再裁，"
                                       "剩余超额转 T2 回收（PlotPilot Phase 2）"),
                        })
                        floor_logged = True
                    break  # T3 触底 → 剩余超额转下一 tier（T2）回收
                _trim_section(manifest, key, tier,
                              "stub" if stub else "compress",
                              f"超预算·{tier} 低优先级让位（目标 {target_bytes}B）",
                              log, stub=stub)


def apply_budget(manifest: dict) -> dict:
    """分层记账（始终）+ 分层裁剪（仅体积 >= 既有硬守卫时）。原地修改并返回 manifest。

    不触发守卫时：除新增 budget_report 元数据键外，所有注入段逐字节不变。
    """
    cfg = load_config()
    body_bytes = _size_bytes(manifest)
    triggered = body_bytes / 1024 >= cfg["hard_kb"]  # 与 main v19.4 守卫同判式（KB >= 上限）
    log: list = []
    if triggered:
        _trim_t0_to_cap(manifest, cfg, log)
        # 预留 budget_report 自身开销（log 会随裁剪增长 → 迭代收敛），
        # 保证含元数据的最终落盘体积也压回既有硬上限内。
        for _ in range(4):
            overhead = _size_bytes(build_budget_report(manifest, cfg, triggered=True,
                                                       compression_log=log))
            target = max(cfg["hard_bytes"] - overhead, 1024)
            if _size_bytes(manifest) <= target:
                break
            before_pass = _size_bytes(manifest)
            _trim_lower_tiers(manifest, target, cfg, log)
            if _size_bytes(manifest) >= before_pass:
                break  # 无可裁进展（避免死循环）
    manifest["budget_report"] = build_budget_report(manifest, cfg, triggered=triggered,
                                                    compression_log=log)
    return manifest
