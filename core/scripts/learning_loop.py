#!/usr/bin/env python3
"""learning_loop.py — 自学习闭环子系统（v18）

【为什么有这个模块】
v17 的"经验沉淀"链路三方字段名不一致、且 save-state 第 8 步合并只是注释占位：
  - novel-reflector 产出  {ch, entries:[{category,trigger,technique,...}], note}
  - 写作经验.json 权威结构 {success_patterns, failure_patterns, preferences}
  - build_manifest.experience_entries() 兼容读两种 —— 但没人真正写
本模块把三方收口到一个脚本：
  1. --merge-reflection  把 reflector 的 entries 按 category 分流进权威结构（字段统一的"根"）
  2. --ingest            吃 audit_hub 的审核报告 -> 复发问题计数 -> 命中阈值升级为高 confidence failure_pattern
                         + v19：统计 waived_issues -> 同 code 在同类章节反复豁免 -> 产出工具校准建议
  3. --scan-recurring    跨章扫描历史 audit 报告 -> 找"同类问题连续 N 章不达标" -> 升级约束 + 标元问题
                         + P2-5：时间维度衰减/清理 —— pattern 超 DECAY_DAYS=14 天未强化 → confidence *=0.8；
                         超 EXPIRY_DAYS=30 天未强化 → 自动清理（迁移：旧条目本轮标 now，下轮再评估）

【v19 顾问制 — 豁免统计（块 2.5）】
检测工具是「顾问」不是「法官」。当 AI 反复对同一 advisory code 在同类章节
（按 chapter_plan.scene_type 归类）给出豁免，说明问题不在「正文」而在「工具阈值
/场景适配不到位」—— 此时该校准工具，而不是反复骚扰 AI 豁免。
  - --ingest 时读 audit 报告的 waived_issues（gate-core 2.2 产出），按 code 累计；
  - 同一 code 被豁免 >= WAIVER_CALIBRATION_THRESHOLD 次 -> 产出 tool_calibration_suggestion；
  - 若这些豁免集中在某一 scene_type -> 建议「加场景适配」；否则 -> 建议「调阈值」；
  - 结果写入 写作经验.json 的 tool_calibration_suggestions 段，供后续人工/工具迭代参考。
hard_gate 项不会出现在 waived_issues（audit_hub 强制忽略其豁免），故天然不进统计。

【权威数据结构】_数据库/写作经验.json
  {
    "success_patterns": [ {id,category,trigger,technique,why_works,confidence,source_chapters,scene_types,example_quote} ],
    "failure_patterns":  [ {id,category,trigger,technique,why_works,confidence,source_chapters,scene_types,
                            recurrence?, severity?, meta_problem?} ],
    "preferences":       [ {preference,confidence} ],
    "tool_calibration_suggestions": [ {code,dimension,waived_count,chapters[],scene_type_hint,    # v19
                                       suggestion_type,suggestion,sample_reasons[],confidence,updated_at} ],
    "_recurrence_tracker": { "<dimension>::<code>": {count,chapters[],first_seen,last_seen} },  # 内部状态
    "_waiver_tracker":     { "<code>": {count,chapters[],dimension,scene_types{},reasons[]} }   # 内部状态 v19
  }
failure_pattern 里 confidence>=0.5 会被 build_manifest 无条件注入下一章 writer。

【命令行接口】
  python learning_loop.py <项目路径> --merge-reflection <reflection.json路径>
  python learning_loop.py <项目路径> --ingest <audit报告路径>
  python learning_loop.py <项目路径> --scan-recurring

退出码：0=正常 / 1=检测到复发问题已升级约束（或产出校准建议）/ 2=致命错误（路径/JSON）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    import chapter_io as cio  # v18：统一正文/数据分离读写；v19 用于读 _changes.json 的 waivers
except ImportError:
    cio = None  # 缺失则 _changes.json 豁免源降级为不可用，audit 报告源仍工作

# 复发阈值：同一 dimension::code 累计出现 N 次 -> 升级为高 confidence failure_pattern
RECUR_THRESHOLD = 3
# P2-5：时间维度的 confidence 衰减 + 过期清理
EXPIRY_DAYS = 30      # pattern 超过 N 天未强化 → 自动清理
DECAY_DAYS = 14       # pattern 超过 N 天未强化 → confidence *= 0.8
# 连续 N 章命中 -> 视为"约束升级"级别（更高 confidence）
CONSECUTIVE_ESCALATE = 2
# v19 豁免阈值：同一 advisory code 被豁免 N 次 -> 产出工具校准建议（V19_PLAN 2.5 建议 N=3）
WAIVER_CALIBRATION_THRESHOLD = 3

EXPERIENCE_FILE = "写作经验.json"
AUDIT_DIR = ".audit"


# ============ 通用 IO ============

def _db_dir(project_root: Path) -> Path:
    return Path(project_root) / "_数据库"


def _experience_path(project_root: Path) -> Path:
    return _db_dir(project_root) / EXPERIENCE_FILE


def _empty_experience() -> dict:
    """权威结构的空骨架（含 v19 豁免统计段）。"""
    return {"success_patterns": [], "failure_patterns": [], "preferences": [],
            "tool_calibration_suggestions": [],
            "_recurrence_tracker": {}, "_waiver_tracker": {}}


def load_experience(project_root: Path) -> dict:
    """读 写作经验.json，归一化到权威结构。兼容历史上的裸 entries 结构。"""
    p = _experience_path(project_root)
    if not p.is_file():
        return _empty_experience()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_experience()
    # 兼容：历史上可能有裸 entries（旧 reflector 直接覆盖写的）-> 分流进权威结构
    legacy_entries = data.pop("entries", None)
    data.setdefault("success_patterns", [])
    data.setdefault("failure_patterns", [])
    data.setdefault("preferences", [])
    data.setdefault("tool_calibration_suggestions", [])  # v19 豁免统计产出
    data.setdefault("_recurrence_tracker", {})
    data.setdefault("_waiver_tracker", {})               # v19 豁免计数内部状态
    if legacy_entries:
        for e in legacy_entries:
            _route_entry(data, e)
    return data


def save_experience(project_root: Path, data: dict) -> Path:
    p = _experience_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============ entry 分流（字段统一的"根"）============

def _stamp_updated(entry: dict) -> dict:
    """P2-5：盖 updated_at 时间戳。每次 pattern 被创建/刷新都调，
    供 _prune_and_decay 计算时间维度的衰减/清理。"""
    entry["updated_at"] = _now()
    return entry


def _route_entry(exp: dict, entry: dict) -> str:
    """把单条 reflector entry 按 category 分流进权威结构。
    返回去向 'success' / 'failure' / 'skip'。同 id 去重（后写覆盖）。"""
    if not isinstance(entry, dict):
        return "skip"
    cat = (entry.get("category") or "").strip().lower()
    eid = entry.get("id")
    if cat == "success":
        bucket = exp["success_patterns"]
    elif cat == "failure":
        bucket = exp["failure_patterns"]
    else:
        return "skip"
    # 同 id 去重：先删旧的再追加（reflector 重跑同章不产生重复）
    if eid:
        bucket[:] = [x for x in bucket if x.get("id") != eid]
    _stamp_updated(entry)  # P2-5：盖时间戳
    bucket.append(entry)
    return cat


def _prune_and_decay(exp: dict, expiry_days: int = EXPIRY_DAYS,
                     decay_days: int = DECAY_DAYS) -> dict:
    """P2-5：时间维度清理 + 衰减。
      - updated_at > expiry_days 天 → 删除（pattern 视为已过期）
      - updated_at > decay_days 天 → confidence *= 0.8（pattern 重要性降低）
      - 无 updated_at 字段的旧条目（迁移前）→ 本轮标 now，下轮再评估
        （非破坏式迁移）

    返回 {pruned, decayed} 用于报告。"""
    pruned, decayed = [], []
    now_dt = datetime.now()
    for cat in ("success_patterns", "failure_patterns"):
        new_list = []
        for p in exp.get(cat, []):
            updated = p.get("updated_at")
            if not updated:
                # 迁移：旧条目无 updated_at → 标当前时间，本轮放行
                p["updated_at"] = _now()
                new_list.append(p)
                continue
            try:
                u_dt = datetime.fromisoformat(updated.split("+")[0].split("Z")[0])
            except (ValueError, AttributeError):
                p["updated_at"] = _now()  # 损坏的时间戳 → 重置
                new_list.append(p)
                continue
            age_days = (now_dt - u_dt).total_seconds() / 86400
            if age_days > expiry_days:
                pruned.append({
                    "category": cat,
                    "id": p.get("id"),
                    "trigger": (p.get("trigger") or "")[:50],
                    "age_days": int(age_days),
                })
                continue  # 不加入 new_list = 删除
            if age_days > decay_days:
                old_conf = p.get("confidence")
                if isinstance(old_conf, (int, float)) and old_conf > 0.1:
                    new_conf = round(old_conf * 0.8, 2)
                    p["confidence"] = new_conf
                    decayed.append({
                        "category": cat,
                        "id": p.get("id"),
                        "age_days": int(age_days),
                        "from": old_conf,
                        "to": new_conf,
                    })
            new_list.append(p)
        exp[cat] = new_list
    return {"pruned": pruned, "decayed": decayed}


# ============ 模式 1：--merge-reflection ============

def merge_reflection(project_root: Path, reflection_path: Path) -> dict:
    """把 reflector 产出的 reflection.json 合并进 写作经验.json。
    reflection 结构：{ch, entries:[...], note}（兼容 reflector 直出权威结构的情况）。"""
    if not reflection_path.is_file():
        print(f"[FATAL] reflection 文件不存在: {reflection_path}", file=sys.stderr)
        sys.exit(2)
    try:
        refl = json.loads(reflection_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[FATAL] reflection JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(2)

    exp = load_experience(project_root)
    routed = {"success": 0, "failure": 0, "skip": 0}

    # 情况 A：reflector 已直出权威结构（success_patterns/failure_patterns）
    if "success_patterns" in refl or "failure_patterns" in refl:
        for e in refl.get("success_patterns", []):
            e.setdefault("category", "success")
            routed[_route_entry(exp, e)] += 1
        for e in refl.get("failure_patterns", []):
            e.setdefault("category", "failure")
            routed[_route_entry(exp, e)] += 1
    # 情况 B：reflector 产出 {ch, entries:[...]}（当前 reflector.md 契约）
    else:
        for e in refl.get("entries", []):
            routed[_route_entry(exp, e)] += 1

    save_experience(project_root, exp)
    ch = refl.get("ch", "?")
    note = refl.get("note", "")
    print(f"[merge-reflection] 第{ch}章 reflection 已合并 -> 写作经验.json")
    print(f"  success_patterns +{routed['success']}  failure_patterns +{routed['failure']}"
          f"  跳过 {routed['skip']}")
    if note:
        print(f"  reflector note: {note}")
    print(f"  当前库存: success={len(exp['success_patterns'])} "
          f"failure={len(exp['failure_patterns'])}")
    return {"routed": routed, "ch": ch}


# ============ 模式 2：--ingest（吃 audit 报告）============

def _issue_key(issue: dict) -> str:
    """复发追踪键：dimension::code。同一类问题归一到同一个 key。"""
    dim = issue.get("dimension", "unknown")
    code = issue.get("code") or issue.get("check") or issue.get("desc", "")[:20]
    return f"{dim}::{code}"


# ---- v19 豁免统计（块 2.5）----

def _chapter_scene_types(project_root: Path, ch: int) -> list:
    """读 进度.json 的 chapter_plan，取本章 scene_type 列表（章节类型信号）。
    找不到返回 []。scene_type 形如 ["日常", "心理外化", "悬疑"]。"""
    prog_path = _db_dir(project_root) / "进度.json"
    if not prog_path.is_file():
        return []
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    for p in prog.get("chapter_plan", []):
        if p.get("ch") == ch:
            st = p.get("scene_type", [])
            return st if isinstance(st, list) else [st] if st else []
    return []


def _waivers_from_changes(project_root: Path, ch: int) -> list:
    """读 _changes.json 的 self_eval.waivers（writer 主动声明的豁免清单）。
    结构 [{code, reason}]（gate-core 2.3 定义）。归一化成 [{code, dimension, waive_reason}]。
    注意：这是 writer 「声明」的豁免，未经 audit_hub 裁决——故 dimension 标 'unknown'，
    且不区分 hard_gate（audit 报告源已剔除 hard_gate 豁免，此源作补充兜底）。

    与 audit_hub._load_waivers 同口径清洗（gate-core 确认的规则）：
      - reason 为空 -> 丢弃（V19：空理由 = 无效豁免，不算豁免，否则可空理由刷掉工具校准）
      - reason >= 100 字 -> 截断到 100
    走兜底路（路 2 audit 报告无豁免痕迹）时才用，主路从 audit 报告读到的 reason 已是干净的。"""
    if cio is None:
        return []
    try:
        changes = cio.read_changes(project_root, ch)
    except Exception:
        return []
    waivers = (changes.get("self_eval") or {}).get("waivers") or []
    out = []
    for w in waivers:
        if not isinstance(w, dict):
            continue
        code = w.get("code")
        reason = (w.get("reason") or w.get("waive_reason") or "").strip()
        if not code or not reason:
            continue  # 空 code 或空理由 = 无效豁免，丢弃
        out.append({"code": code, "dimension": "unknown",
                    "waive_reason": reason[:100]})
    return out


def _collect_waived_issues(audit: dict, project_root: Path = None, ch: int = None) -> list:
    """从 audit 报告抽出豁免项。优先用 gate-core 2.2 产出的 waived_issues 段；
    退回扫 issues 里 waived==True 的（兼容只回填 issue 字段、没单列 waived_issues 的报告）。
    若 audit 报告里没有任何豁免痕迹，再退回读 _changes.json 的 self_eval.waivers
    （writer 主动声明、audit_hub 尚未裁决的场景）。返回 [{code, dimension, waive_reason}]。"""
    out = []
    waived_block = audit.get("waived_issues")
    if isinstance(waived_block, list) and waived_block:
        for w in waived_block:
            code = w.get("code")
            if code:
                out.append({"code": code,
                            "dimension": w.get("dimension", "unknown"),
                            "waive_reason": w.get("waive_reason", "")})
        return out
    # 退回 1：扫 issues 里 waived==True
    for issue in audit.get("issues", []):
        if issue.get("waived") is True:
            code = issue.get("code")
            if code:
                out.append({"code": code,
                            "dimension": issue.get("dimension", "unknown"),
                            "waive_reason": issue.get("waive_reason", "")})
    if out:
        return out
    # 退回 2：audit 报告无豁免痕迹 -> 读 _changes.json 的 writer 声明豁免
    if project_root is not None and ch is not None:
        return _waivers_from_changes(project_root, ch)
    return out


def _track_waivers(exp: dict, project_root: Path, audit: dict, ch: int) -> set:
    """把本章豁免项计入 _waiver_tracker，返回本次触及的 code 集合。
    每个 code 记：豁免次数 / 章节列表 / 维度 / 各 scene_type 命中次数 / 理由样本。"""
    wt = exp["_waiver_tracker"]
    scene_types = _chapter_scene_types(project_root, ch)
    waived = _collect_waived_issues(audit, project_root, ch)
    touched = set()
    seen_codes = set()
    for w in waived:
        code = w["code"]
        if code in seen_codes:
            continue  # 同章同 code 只计一次
        seen_codes.add(code)
        rec = wt.setdefault(code, {"count": 0, "chapters": [],
                                   "dimension": w["dimension"],
                                   "scene_types": {}, "reasons": []})
        if ch not in rec["chapters"]:
            rec["count"] += 1
            rec["chapters"].append(ch)
            rec["chapters"].sort()
        # scene_type 命中累计（用于判定豁免是否集中在某类章节）
        for st in scene_types:
            rec["scene_types"][st] = rec["scene_types"].get(st, 0) + 1
        reason = (w.get("waive_reason") or "").strip()
        if reason and reason not in rec["reasons"]:
            rec["reasons"].append(reason)
            rec["reasons"] = rec["reasons"][-5:]  # 留最近 5 条样本
        touched.add(code)
    return touched


def _build_calibration_suggestions(exp: dict, only_codes=None) -> list:
    """扫 _waiver_tracker，对豁免次数 >= 阈值的 code 产出/刷新工具校准建议。
    only_codes 限定本次只看这些 code（--ingest 增量）；None = 全扫。
    判定逻辑：
      - 某 scene_type 命中次数 >= 阈值 -> suggestion_type='add_scene_adaptation'（加场景适配）
      - 否则（豁免分散在不同章节类型）-> suggestion_type='adjust_threshold'（调阈值）
    """
    wt = exp["_waiver_tracker"]
    suggestions = exp["tool_calibration_suggestions"]
    produced = []
    for code, rec in wt.items():
        if only_codes is not None and code not in only_codes:
            continue
        if rec["count"] < WAIVER_CALIBRATION_THRESHOLD:
            continue
        # 找豁免最集中的 scene_type
        scene_hits = rec.get("scene_types", {})
        dominant_st, dominant_n = None, 0
        for st, n in scene_hits.items():
            if n > dominant_n:
                dominant_st, dominant_n = st, n
        if dominant_st and dominant_n >= WAIVER_CALIBRATION_THRESHOLD:
            stype = "add_scene_adaptation"
            suggestion = (f"检测项 [{code}] 在「{dominant_st}」类章节被反复豁免 "
                          f"{dominant_n} 次 —— 建议给该检测器加「{dominant_st}」场景适配"
                          f"（类似 narrative_scanner 的 solo_atmospheric 豁免分流），"
                          f"而非反复让 AI 手动豁免。")
            confidence = 0.9
        else:
            stype = "adjust_threshold"
            suggestion = (f"检测项 [{code}] 在第 {rec['chapters']} 章被累计豁免 "
                          f"{rec['count']} 次，且不集中于单一章节类型 —— 建议复核该检测器"
                          f"阈值是否过严，考虑放宽阈值或降级默认 severity。")
            confidence = 0.75
        entry = {
            "code": code,
            "dimension": rec.get("dimension", "unknown"),
            "waived_count": rec["count"],
            "chapters": list(rec["chapters"]),
            "scene_type_hint": dominant_st if stype == "add_scene_adaptation" else None,
            "suggestion_type": stype,
            "suggestion": suggestion,
            "sample_reasons": list(rec.get("reasons", [])),
            "confidence": confidence,
            "updated_at": _now(),
        }
        # 同 code 覆盖刷新（重跑同章不产生重复）
        suggestions[:] = [s for s in suggestions if s.get("code") != code]
        suggestions.append(entry)
        produced.append(entry)
    return produced


def ingest_audit(project_root: Path, audit_path: Path) -> dict:
    """吃单份 audit 报告 -> 更新复发计数器 -> 命中阈值则升级 failure_pattern。
    v19：同时统计 waived_issues -> 反复豁免命中阈值则产出工具校准建议。"""
    if not audit_path.is_file():
        print(f"[FATAL] audit 报告不存在: {audit_path}", file=sys.stderr)
        sys.exit(2)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[FATAL] audit JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(2)

    exp = load_experience(project_root)
    tracker = exp["_recurrence_tracker"]
    ch = audit.get("chapter", 0)

    # audit 报告里只有"未被自动修掉的真问题"才该进入学习（auto_fixed 的不算复发负债）
    issues = audit.get("issues", []) + audit.get("pending_agent", [])
    seen_keys = set()
    for issue in issues:
        # 只学习 error/fatal 级别 + warning 里反复出现的；info 不学
        sev = (issue.get("severity") or "warning").lower()
        if sev not in ("fatal", "error", "warning"):
            continue
        # v19：被 AI 豁免的 issue 不是「复发负债」而是「已裁决的合理例外」——
        # 不进 failure_pattern 升级链，否则 AI 的正当豁免会被反向升级成硬约束（违背顾问制本意）。
        # 豁免项另走 _track_waivers -> 反复豁免则校准工具，不骚扰 AI。
        if issue.get("waived") is True:
            continue
        key = _issue_key(issue)
        if key in seen_keys:
            continue  # 同章同类只计一次
        seen_keys.add(key)
        rec = tracker.setdefault(key, {"count": 0, "chapters": [],
                                       "first_seen": ch, "last_seen": ch,
                                       "dimension": issue.get("dimension", "unknown"),
                                       "sample_desc": issue.get("desc", "")})
        if ch not in rec["chapters"]:
            rec["count"] += 1
            rec["chapters"].append(ch)
            rec["chapters"].sort()
        rec["last_seen"] = ch

    escalated = _escalate_recurring(exp, only_keys=seen_keys)

    # v19 块 2.5：统计豁免 -> 反复豁免同一 code 命中阈值 -> 产出工具校准建议
    waived_codes = _track_waivers(exp, project_root, audit, ch)
    calib = _build_calibration_suggestions(exp, only_codes=waived_codes)

    save_experience(project_root, exp)

    print(f"[ingest] 第{ch}章 audit 报告已吸收，{len(seen_keys)} 类问题入复发追踪"
          f"，{len(waived_codes)} 类豁免入豁免追踪")
    if calib:
        print(f"[ingest] CALIB {len(calib)} 类检测项反复被豁免，已产出工具校准建议：")
        for c in calib:
            print(f"  - [{c['code']}] 豁免 {c['waived_count']} 次 -> "
                  f"{c['suggestion_type']}（{c['scene_type_hint'] or '调阈值'}）")
    if escalated:
        print(f"[ingest] WARN {len(escalated)} 类问题命中复发阈值，已升级为 failure_pattern：")
        for e in escalated:
            print(f"  - {e['trigger']}（已连续/累计 {e['_recurrence']} 章）")
    return {"escalated": escalated, "calibration": calib, "ch": ch}


def _is_consecutive(chapters: list, n: int) -> bool:
    """chapters 里是否存在长度 >= n 的连续段。"""
    if len(chapters) < n:
        return False
    s = sorted(set(chapters))
    run = 1
    for i in range(1, len(s)):
        run = run + 1 if s[i] == s[i - 1] + 1 else 1
        if run >= n:
            return True
    return False


def _escalate_recurring(exp: dict, only_keys=None) -> list:
    """扫复发计数器，命中阈值的升级/刷新为高 confidence failure_pattern。
    only_keys 限定本次只看这些 key（--ingest 用）；None = 全扫（--scan-recurring 用）。"""
    tracker = exp["_recurrence_tracker"]
    fp = exp["failure_patterns"]
    escalated = []
    for key, rec in tracker.items():
        if only_keys is not None and key not in only_keys:
            continue
        if rec["count"] < RECUR_THRESHOLD:
            continue
        consecutive = _is_consecutive(rec["chapters"], CONSECUTIVE_ESCALATE)
        # 连续命中 -> 0.95（约束升级级别）；累计命中 -> 0.8
        confidence = 0.95 if consecutive else 0.8
        pattern_id = f"recur_{key.replace('::', '_').replace(' ', '')}"
        # key = "dimension::code"，trigger 要带上 code 才能区分同维度下的不同问题
        # （如 结构::NARRATIVE_pov 与 结构::PLOT_beat 都属"结构"维度，不能混为一谈）
        dim = rec.get("dimension", "?")
        code = key.split("::", 1)[1] if "::" in key else key
        entry = {
            "id": pattern_id,
            "category": "failure",
            "trigger": f"{dim}维度 [{code}] 反复出现",
            "technique": f"问题样本：{rec.get('sample_desc', '')[:60]}",
            "why_works": (f"该问题在第 {rec['chapters']} 章{'连续' if consecutive else '累计'}"
                          f"出现 {rec['count']} 次，已成模式，下一章 writer 必须主动规避"),
            "confidence": confidence,
            "source_chapters": list(rec["chapters"]),
            "scene_types": [],
            "recurrence": rec["count"],
            "severity": "约束升级" if consecutive else "高频警示",
            "_recurrence": rec["count"],
        }
        # 同 id 覆盖刷新
        fp[:] = [x for x in fp if x.get("id") != pattern_id]
        _stamp_updated(entry)  # P2-5：盖时间戳
        fp.append(entry)
        escalated.append(entry)
    return escalated


# ============ 模式 3：--scan-recurring（跨章扫描）============

def _safe_load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def scan_recurring(project_root: Path) -> dict:
    """跨章扫描所有历史 audit 报告 -> 重建复发计数器 -> 升级约束 + 标元问题。
    元问题：疑似审核工具自身有 bug（如 validate_style 把 CHANGES 当正文）。
    v19：同时重建豁免计数器 -> 跨章反复豁免 -> 刷新工具校准建议。"""
    audit_dir = _db_dir(project_root) / AUDIT_DIR
    exp = load_experience(project_root)
    # 重建 tracker（全量扫描时以历史报告为准，避免计数器漂移）
    exp["_recurrence_tracker"] = {}
    exp["_waiver_tracker"] = {}            # v19：豁免计数器同样全量重建
    tracker = exp["_recurrence_tracker"]

    report_files = sorted(audit_dir.glob("ch_*_audit.json")) if audit_dir.is_dir() else []
    if not report_files:
        print(f"[scan-recurring] 未找到历史 audit 报告（{audit_dir}），无可扫描")
        save_experience(project_root, exp)
        return {"escalated": [], "meta_problems": [], "calibration": []}

    meta_signals = {}
    scanned_chapters = set()
    for rf in report_files:
        audit = _safe_load(rf)
        if not audit:
            continue
        ch = audit.get("chapter", 0)
        scanned_chapters.add(ch)
        # v19：每份报告的豁免项计入豁免计数器（issues 里 waived==True 的不进复发链）
        _track_waivers(exp, project_root, audit, ch)
        issues = audit.get("issues", []) + audit.get("pending_agent", [])
        seen = set()
        for issue in issues:
            sev = (issue.get("severity") or "warning").lower()
            if sev not in ("fatal", "error", "warning"):
                continue
            # v19：被 AI 豁免的 issue 不进复发链（与 ingest_audit 同口径）——
            # 否则正当豁免会被反向升级成 failure_pattern 硬约束，违背顾问制本意。
            if issue.get("waived") is True:
                continue
            key = _issue_key(issue)
            if key not in seen:
                seen.add(key)
                rec = tracker.setdefault(key, {"count": 0, "chapters": [],
                                               "first_seen": ch, "last_seen": ch,
                                               "dimension": issue.get("dimension", "unknown"),
                                               "sample_desc": issue.get("desc", "")})
                if ch not in rec["chapters"]:
                    rec["count"] += 1
                    rec["chapters"].append(ch)
                    rec["chapters"].sort()
                rec["last_seen"] = max(rec["last_seen"], ch)
            # 元问题信号：issue 自带 meta_suspect 标记
            if issue.get("meta_suspect"):
                ms = meta_signals.setdefault(key, {"chapters": [], "desc": issue.get("desc", "")})
                if ch not in ms["chapters"]:
                    ms["chapters"].append(ch)

    # 元问题判定：分两档置信度，避免误伤"数据没填"当成"工具 bug"
    #  - high   ：audit_hub 显式打了 meta_suspect 标 -> 基本确定是审核器误判
    #  - candidate：某类问题 >=3 章且 100% 命中，且不是"数据缺失类" code
    #             -> 仅作候选提示，需人工确认（旧稿没填数据库也会 100% 命中，不能直接判工具 bug）
    # 数据缺失类 code：问题根因是作者/数据没填，不是工具 bug，排除出元问题候选
    DATA_GAP_HINTS = ("未声明", "未在", "NOT_PAID", "MISSING", "UNDECLARED", "_arc")
    scanned_chapters.discard(None)
    meta_problems = []
    for key, rec in tracker.items():
        if not (rec["count"] >= 3 and scanned_chapters
                and len(set(rec["chapters"])) == len(scanned_chapters)):
            continue
        sample = rec.get("sample_desc", "")
        # 数据缺失类不算工具 bug 候选
        if any(h in key or h in sample for h in DATA_GAP_HINTS):
            continue
        meta_problems.append({
            "key": key, "dimension": rec.get("dimension"),
            "chapters": rec["chapters"], "count": rec["count"],
            "confidence": "candidate",
            "reason": "100% 章节命中同类问题 — 候选元问题，需人工确认是否审核规则过严",
            "sample": sample,
        })
    for key, ms in meta_signals.items():
        meta_problems.append({
            "key": key, "chapters": ms["chapters"],
            "confidence": "high",
            "reason": "audit_hub 标记 meta_suspect — 疑似审核器误判",
            "sample": ms["desc"],
        })

    # v19：全量重建时，剪掉「已不在重建后 tracker 里」的 recur_* failure_pattern。
    # recur_* 是 _escalate_recurring 从 tracker 派生的，归本轮重建管辖；
    # tracker 里没有的 key（如某 code 现已被全部豁免、不再进复发链）-> 对应 recur_* 是
    # 残留陈旧约束，必须剪掉，否则 confidence=0.95 的陈旧约束会被持续注入下一章 writer。
    # 非 recur_*（reflector 产出的）一律不动。
    live_recur_ids = {f"recur_{k.replace('::', '_').replace(' ', '')}" for k in tracker}
    fp = exp["failure_patterns"]
    pruned = [x for x in fp
              if str(x.get("id", "")).startswith("recur_") and x.get("id") not in live_recur_ids]
    if pruned:
        fp[:] = [x for x in fp if x not in pruned]

    escalated = _escalate_recurring(exp, only_keys=None)
    # v19：全量重建后刷新工具校准建议（全扫，only_codes=None）。
    #      先清空旧建议——以历史报告为准，避免已不复现的建议残留。
    exp["tool_calibration_suggestions"] = []
    calib = _build_calibration_suggestions(exp, only_codes=None)
    exp["_meta_problems"] = meta_problems
    # P2-5：时间维度衰减 + 过期清理 —— 在 escalate 之后跑，让本轮新刷的 pattern
    # 拿到新鲜的 updated_at；旧 pattern 若长期未被强化则衰减/清理。
    time_prune = _prune_and_decay(exp)
    save_experience(project_root, exp)

    print(f"[scan-recurring] 扫描 {len(report_files)} 份 audit 报告，"
          f"{len(tracker)} 类问题进入追踪，{len(exp['_waiver_tracker'])} 类豁免进入追踪")
    if pruned:
        print(f"  剪掉 {len(pruned)} 条陈旧 recur_* 约束（已不复现 / 现已被全部豁免）：")
        for x in pruned:
            print(f"    - {x.get('id')}: {x.get('trigger', '')}")
    if escalated:
        print(f"WARN {len(escalated)} 类问题命中复发阈值，已升级 failure_pattern：")
        for e in escalated:
            print(f"  - {e['trigger']} -> confidence={e['confidence']}（第 {e['source_chapters']} 章）")
    if calib:
        print(f"CALIB {len(calib)} 类检测项跨章反复被豁免，已产出工具校准建议：")
        for c in calib:
            print(f"  - [{c['code']}] 豁免 {c['waived_count']} 次（第 {c['chapters']} 章）"
                  f" -> {c['suggestion_type']}")
    if meta_problems:
        high = [m for m in meta_problems if m.get("confidence") == "high"]
        cand = [m for m in meta_problems if m.get("confidence") != "high"]
        print(f"META 元问题：{len(high)} 个高置信（审核器误判）+ {len(cand)} 个候选（需人工确认）")
        for m in high + cand:
            tag = "高置信" if m.get("confidence") == "high" else "候选"
            print(f"  - [{tag}|{m.get('dimension', '?')}] {m['reason']}")
            print(f"    样本：{m['sample'][:80]}")
    # P2-5：时间维度变化报告
    if time_prune["pruned"]:
        print(f"PRUNE {len(time_prune['pruned'])} 条 pattern 超 {EXPIRY_DAYS} 天未强化已清理：")
        for p in time_prune["pruned"]:
            print(f"  - [{p['category']}] {p['id']} (age={p['age_days']}d): "
                  f"{p['trigger']}")
    if time_prune["decayed"]:
        print(f"DECAY {len(time_prune['decayed'])} 条 pattern 超 {DECAY_DAYS} 天未强化 confidence 衰减：")
        for d in time_prune["decayed"]:
            print(f"  - [{d['category']}] {d['id']} (age={d['age_days']}d): "
                  f"{d['from']} → {d['to']}")
    if not escalated and not meta_problems and not calib and not time_prune["pruned"] and not time_prune["decayed"]:
        print("  暂无复发问题 / 反复豁免 / 过期 pattern，写作经验库健康。")
    return {"escalated": escalated, "meta_problems": meta_problems,
            "calibration": calib, "time_prune": time_prune}


# ============ CLI ============

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0]).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    if "--merge-reflection" in args:
        idx = args.index("--merge-reflection")
        if idx + 1 >= len(args):
            print("[FATAL] --merge-reflection 需要 reflection.json 路径", file=sys.stderr)
            sys.exit(2)
        rp = Path(args[idx + 1])
        if not rp.is_absolute():
            rp = project_root / rp
        merge_reflection(project_root, rp)
        sys.exit(0)
    elif "--ingest" in args:
        idx = args.index("--ingest")
        if idx + 1 >= len(args):
            print("[FATAL] --ingest 需要 audit 报告路径", file=sys.stderr)
            sys.exit(2)
        ap = Path(args[idx + 1])
        if not ap.is_absolute():
            ap = project_root / ap
        result = ingest_audit(project_root, ap)
        # exit 1 = 检测到复发问题已升级约束，或反复豁免已产出工具校准建议（都值得调用方关注）
        sys.exit(1 if (result.get("escalated") or result.get("calibration")) else 0)
    elif "--scan-recurring" in args:
        result = scan_recurring(project_root)
        sys.exit(1 if (result.get("escalated") or result.get("meta_problems")
                       or result.get("calibration")) else 0)
    else:
        print("[FATAL] 需指定 --merge-reflection / --ingest / --scan-recurring", file=sys.stderr)
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
