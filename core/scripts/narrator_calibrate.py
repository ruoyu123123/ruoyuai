"""narrator_calibrate.py — Storyteller 风格 + Adaptation Factor 调压器（v21 R1.2 新增）

借鉴 RimWorld 的三种 Storyteller（Cassandra 升压 / Phoebe 长间歇 / Randy 随机）+ Adaptation Factor。

每章 save-state 末尾跑：
1. 取本章 outcome（win/setback/neutral）：优先消费 writer 申报的
   _changes.json.self_eval.storyteller_alignment.actual_outcome（#7 孤儿契约修复 · 北极星⑤
   作者申报第一权威），未申报才回退 heuristic 推断；intensity 仍 heuristic 估算
2. 写入 叙事节拍器.json.chapter_outcome_log（带 outcome_source 标 writer_declared / inferred）
3. 滑窗 N 章计算 setback_count / win_streak / loss_streak
4. 对照 storyteller_profile 的 expected_setback_per_n_ch，决定下章 target_outcome:
   - 实际 setback < 预期 → 下章 target=setback（"该让主角吃亏了"）
   - 实际 setback > 预期 → 下章 target=win（"读者要喘息"）
   - 区间内 → target=auto
5. 输出更新后的 narrator_recommendation 字段

四种 phase 自动切换：
- 连续 3 章 win + intensity 累计 ≥ 12 → climax 触发后 cooldown
- cooldown 持续 ≥ 3 章 → steady
- steady 期累计 setback ≥ 2 → rising
- rising → 自动按 storyteller 节奏推进

用法：python narrator_calibrate.py <project> [--ch N] [--auto]
退出码: 0 健康 / 1 narrator 强烈建议下章修正 / 2 致命
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# 2026-05-29 修：注入 scripts 目录以 import atomic_json（原子写）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data: dict):
    # 2026-05-29 修：裸写 → 原子写（atomic_write_json 内部已 mkdir + fsync）
    atomic_json.atomic_write_json(p, data)


# ───────────────────── 真实项目 schema 兼容层（2026-05-30 北极星③契约修复） ─────────────────────
# 背景（3 真实项目实测）：calibrate 读 storyteller_profile/current_pressure_phase/adaptation_factor
# （靠 setdefault 部分自愈），但真实项目顶层用别的 schema，导致 outline 产的字段成孤儿：
#   · 纵尸司：framework:"Save_the_Cat" + beats[]（id/desc/at_cluster/done）—— Save_the_Cat 节拍位点
#   · 诡异：  rhythm_profile:"混合" + beat_density_by_cluster{cluster_001:"中"...} —— cluster 节奏密度
#   · 城南：  v21 form（storyteller_profile/adaptation_factor/...）
# 旧版 calibrate 跑这俩项目时只是 setdefault 出一堆空 v21 字段，但 framework/beats/rhythm_profile
# 永远不被读、不进 manifest → writer 看不到「本 cluster 该到哪个 Save_the_Cat 节拍 / 节奏密度多高」。
# 修复：抽 narrator_view() 把 3 套形态归一暴露（参照 fate_engine accessor），calibrate + manifest 共用。


def narrator_view(pacer: dict, cluster_id: str = None) -> dict:
    """归一叙事节拍器视图（calibrate + build_manifest 共用）。
    保留 v21 调压字段，同时把 framework/beats(Save_the_Cat) 与 rhythm_profile/beat_density 暴露给 writer。
    """
    framework = pacer.get("framework")
    beats = pacer.get("beats") if isinstance(pacer.get("beats"), list) else None
    # 当前 cluster 该命中的 Save_the_Cat 节拍（at_cluster 命中本 cluster 的）
    current_beats = None
    if beats and cluster_id:
        current_beats = [
            {"id": b.get("id"), "desc": b.get("desc"), "done": b.get("done", False)}
            for b in beats
            if isinstance(b, dict) and str(cluster_id) in str(b.get("at_cluster", ""))
        ] or None
    # cluster 节奏密度（诡异 schema）
    rhythm_profile = pacer.get("rhythm_profile")
    density_map = pacer.get("beat_density_by_cluster") if isinstance(pacer.get("beat_density_by_cluster"), dict) else None
    current_density = density_map.get(cluster_id) if (density_map and cluster_id) else None
    return {
        "profile": pacer.get("storyteller_profile", "cassandra"),
        "current_phase": pacer.get("current_pressure_phase", "rising"),
        "since_phase_change_ch": pacer.get("since_phase_change_ch"),
        "framework": framework,
        "current_cluster_beats": current_beats,
        "rhythm_profile": rhythm_profile,
        "current_cluster_density": current_density,
        "adaptation_factor": pacer.get("adaptation_factor", {}) or {},
        "narrator_recommendation": pacer.get("narrator_recommendation", {}) or {},
    }


def infer_outcome_from_changes(changes: dict) -> tuple[str, int]:
    """从 _changes.json 取/推断 outcome + intensity。

    2026-05-30 #7 孤儿契约修复：writer 在 self_eval.storyteller_alignment.actual_outcome
    （changes_schema.json:555）已**主动申报**本章实际结局（setback/win/neutral）。此前 narrator
    每次都靠下方 heuristic **重推断**，把 writer 的第一手申报丢弃 → storyteller_alignment 成孤儿
    字段（writer 报了但无消费方）。北极星⑤：作者/写作端的申报是第一权威，系统不该用启发式覆盖
    模型的判断。故现在**优先消费** writer 申报的 actual_outcome；intensity 仍由 heuristic 估算
    （schema 未让 writer 申报 intensity）。仅当 writer 未申报 actual_outcome 时回退 heuristic
    （向后兼容老 changes / 申报缺失）。

    回退 heuristic 准则（writer 未申报时）：
    - factual.fate_events_triggered 含 status=completed → win 偏向（除非是悲剧大事件）
    - factual.foreshadowing_paid 数量 ≥ 2 → win 偏向
    - factual.locked_facts 含负面关键词（死/失/被/暴露/受伤）→ setback
    - self_eval.judge_health_warnings 严重 → setback
    - 都没有 → neutral
    """
    factual = changes.get("factual", {}) or {}
    self_eval = changes.get("self_eval", {}) or {}

    # writer 申报优先（#7 孤儿契约修复）：消费 self_eval.storyteller_alignment.actual_outcome
    sa = self_eval.get("storyteller_alignment") or {}
    declared = sa.get("actual_outcome")
    if declared in ("setback", "win", "neutral"):
        _, heuristic_intensity = _infer_outcome_heuristic(factual)
        return (declared, heuristic_intensity)

    return _infer_outcome_heuristic(factual)


def _model_full_text_valence(full_text: str) -> "float | None":
    """VAD 模型对 locked_facts/relationships 拼接文本判 valence；env 未开/模型不可用/未命中
    → None（调用方回退关键词计数）。"""
    if not full_text.strip() or os.environ.get("RUOYU_NN_VAD") != "1":
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
            from feature_cache import FeatureStore, enabled as feature_store_enabled
            preds = FeatureStore.get().compute_vad_batch([full_text]) if feature_store_enabled() else None
        except Exception:
            preds = None
        if preds is None:
            import nn_vad_bridge
            preds = nn_vad_bridge.predict_batch([full_text])
    except Exception:
        return None
    if not preds or not preds[0] or preds[0].get("valence") is None:
        return None
    try:
        return float(preds[0]["valence"])
    except (TypeError, ValueError):
        return None


def _infer_outcome_heuristic(factual: dict) -> tuple[str, int]:
    """启发式推断 outcome + intensity（writer 未申报 actual_outcome 时的回退）。

    负面判定优先走 VAD 模型 valence（locked_facts/relationships 拼接文本·<0.5 视为负面主导）；
    模型未启用/不可用/未命中 → 回退关键词计数（原逻辑不变）。intensity 仍按关键词命中数计
    （模型只接管"是否负面"的判断，强度量级维持关键词口径，下游 min(8, 3+hits) 契约不变）。
    """
    fate_count = len(factual.get("fate_events_triggered", []) or [])
    foreshadow_paid = len(factual.get("foreshadowing_paid", []) or [])

    # 负面关键词扫
    negative_kw = ["失败", "受伤", "死亡", "暴露", "被发现", "被打", "败退", "崩溃", "失控", "受重创"]
    locked_text = json.dumps(factual.get("locked_facts") or factual.get("facts_locked") or [], ensure_ascii=False)  # 2026-05-30：writer 实产 facts_locked，双读兜底（否则 setback 信号恒空）
    relations_text = json.dumps(factual.get("relationships", []) or [], ensure_ascii=False)
    full_text = locked_text + " " + relations_text
    negative_hits = sum(1 for kw in negative_kw if kw in full_text)

    model_valence = _model_full_text_valence(full_text)
    is_negative = (model_valence < 0.5) if model_valence is not None else (negative_hits >= 2)

    if is_negative:
        return ("setback", min(8, 3 + negative_hits))
    if fate_count >= 1 or foreshadow_paid >= 2:
        return ("win", min(7, 3 + fate_count + foreshadow_paid))
    if foreshadow_paid >= 1 or fate_count >= 0:
        return ("neutral", 3)
    return ("neutral", 2)


def evaluate_phase(profile: str, log: list[dict], current_phase: str, since_change_ch: int, ch: int) -> tuple[str, int]:
    """评估 phase 切换。返回 (new_phase, since_change_ch)。"""
    recent = log[-5:]
    win_run = 0
    win_intensity_sum = 0
    setback_run = 0
    for entry in reversed(recent):
        if entry["outcome"] == "win":
            win_run += 1
            win_intensity_sum += entry.get("intensity", 0)
        else:
            break
    for entry in reversed(recent):
        if entry["outcome"] == "setback":
            setback_run += 1
        else:
            break

    chs_since = ch - since_change_ch + 1

    # rising → climax → cooldown → steady → rising
    if current_phase == "rising":
        if win_run >= 3 and win_intensity_sum >= 12:
            return ("climax", ch)
        return (current_phase, since_change_ch)
    if current_phase == "climax":
        # climax 持续 1-2 章后转 cooldown
        if chs_since >= 1:
            return ("cooldown", ch)
        return (current_phase, since_change_ch)
    if current_phase == "cooldown":
        if chs_since >= 3:
            return ("steady", ch)
        return (current_phase, since_change_ch)
    if current_phase == "steady":
        if setback_run >= 2 or chs_since >= 5:
            return ("rising", ch)
        return (current_phase, since_change_ch)
    return (current_phase, since_change_ch)


def calibrate(project_root: Path, ch: int) -> dict:
    pacer_path = project_root / "_数据库" / "叙事节拍器.json"
    pacer = load_json(pacer_path, None)
    if pacer is None:
        return {"error": "叙事节拍器.json 不存在"}

    # 读 _changes
    changes_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"
    changes = load_json(changes_path, {})
    outcome, intensity = infer_outcome_from_changes(changes)

    # #7 孤儿契约修复：标注 outcome 来源（writer 申报 / heuristic 回退），供审计与下游分布判断
    declared = ((changes.get("self_eval") or {}).get("storyteller_alignment") or {}).get("actual_outcome")
    outcome_source = "writer_declared" if declared in ("setback", "win", "neutral") else "inferred"
    note = ("from _changes.self_eval.storyteller_alignment"
            if outcome_source == "writer_declared" else "auto-inferred from _changes")

    # 写入 log（去重）
    log = pacer.setdefault("chapter_outcome_log", [])
    if any(e.get("ch") == ch for e in log):
        # 替换
        log = [e for e in log if e.get("ch") != ch]
    log.append({"ch": ch, "outcome": outcome, "intensity": intensity,
                "outcome_source": outcome_source, "_note": note})
    log.sort(key=lambda e: e.get("ch", 0))
    pacer["chapter_outcome_log"] = log

    # 更新 phase
    profile = pacer.get("storyteller_profile", "cassandra")
    cur_phase = pacer.get("current_pressure_phase", "rising")
    since_ch = pacer.get("since_phase_change_ch", 1)
    new_phase, new_since = evaluate_phase(profile, log, cur_phase, since_ch, ch)
    pacer["current_pressure_phase"] = new_phase
    pacer["since_phase_change_ch"] = new_since

    # 滑窗 adaptation_factor
    af = pacer.setdefault("adaptation_factor", {})
    n = af.get("recent_n_chapters", 10)
    expected = af.get("expected_setback_per_n_ch", 4)
    tol = af.get("tolerance_window", 2)
    window = log[-n:]
    setback_count = sum(1 for e in window if e["outcome"] == "setback")
    win_streak = 0
    loss_streak = 0
    for e in reversed(window):
        if e["outcome"] == "win":
            win_streak += 1
        else:
            break
    for e in reversed(window):
        if e["outcome"] == "setback":
            loss_streak += 1
        else:
            break
    af["current_setback_count_in_window"] = setback_count
    af["current_win_streak"] = win_streak
    af["current_loss_streak"] = loss_streak

    # 推荐下章 outcome
    rec = pacer.setdefault("narrator_recommendation", {})
    if setback_count + tol < expected:
        rec["next_chapter_target_outcome"] = "setback"
        rec["next_chapter_intensity_target"] = "high"
        rec["_reason"] = f"近 {n} 章 setback={setback_count} < 期望{expected}-{tol}={expected-tol} → 该让主角吃亏"
        urgent = True
    elif setback_count - tol > expected:
        rec["next_chapter_target_outcome"] = "win"
        rec["next_chapter_intensity_target"] = "high"
        rec["_reason"] = f"近 {n} 章 setback={setback_count} > 期望{expected}+{tol}={expected+tol} → 该给主角喘息"
        urgent = True
    else:
        rec["next_chapter_target_outcome"] = "auto"
        rec["next_chapter_intensity_target"] = "auto"
        rec["_reason"] = f"近 {n} 章 setback={setback_count} 在期望区间 [{expected-tol}, {expected+tol}] 内 → 自由发挥"
        urgent = False

    # 2026-05-30 孤儿契约修复：Save_the_Cat beats（纵尸司 schema）此前无任何消费方。
    # 反查本 ch 所属 cluster_id，把该 cluster 的 beats 标 done=true（节拍达成）——让 framework/beats
    # 真正进入「写→标记达成」闭环，而非 outline 写完就烂在文件里。北极星①：用 cluster_lookup 反查，
    # 禁 f"cluster_{ch:03d}" 拼接；北极星⑤：只标 done（事实记录），不据此硬约束 writer。
    cluster_id = None
    try:
        import cluster_lookup
        cluster_id = cluster_lookup.ch_to_cluster_id(project_root, ch)
    except Exception:
        cluster_id = None
    beats_marked = []
    beats = pacer.get("beats")
    if isinstance(beats, list) and cluster_id:
        for b in beats:
            if isinstance(b, dict) and str(cluster_id) in str(b.get("at_cluster", "")) and not b.get("done"):
                b["done"] = True
                b["done_at_ch"] = ch
                beats_marked.append(b.get("id"))

    save_json(pacer_path, pacer)

    view = narrator_view(pacer, cluster_id)
    return {
        "ch": ch,
        "cluster_id": cluster_id,
        "outcome_inferred": outcome,
        "outcome_source": outcome_source,
        "intensity": intensity,
        "phase_change": cur_phase != new_phase,
        "phase": new_phase,
        "framework": view["framework"],
        "current_cluster_beats": view["current_cluster_beats"],
        "beats_marked_done": beats_marked,
        "rhythm_profile": view["rhythm_profile"],
        "current_cluster_density": view["current_cluster_density"],
        "adaptation_factor": af,
        "next_recommendation": rec,
        "_urgent": urgent,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ch", type=int, default=None)
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project)
    if not (project_root / "_数据库" / "叙事节拍器.json").exists():
        print("[SKIP] 叙事节拍器.json 不存在 — 项目未启用 storyteller 系统")
        sys.exit(0)

    ch = args.ch
    if ch is None or args.auto:
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        if not chapters:
            print("[SKIP] 无已写章节")
            sys.exit(0)
        ch = chapters[-1]

    r = calibrate(project_root, ch)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(1 if r.get("_urgent") else 0)


if __name__ == "__main__":
    main()
