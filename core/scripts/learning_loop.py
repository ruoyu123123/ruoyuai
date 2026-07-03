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
（按 cluster_blueprint.scene_type 归类）给出豁免，说明问题不在「正文」而在「工具阈值
/场景适配不到位」—— 此时该校准工具，而不是反复骚扰 AI 豁免。
  - --ingest 时读 audit 报告的 waived_issues（gate-core 2.2 产出），按 code 累计；
  - 同一 code 被豁免 >= WAIVER_CALIBRATION_THRESHOLD 次 -> 产出 tool_calibration_suggestion；
  - 若这些豁免集中在某一 scene_type -> 建议「加场景适配」；否则 -> 建议「调阈值」；
  - 结果写入 写作经验.json 的 tool_calibration_suggestions 段，供后续人工/工具迭代参考。
hard_gate 项不会出现在 waived_issues（audit_hub 强制忽略其豁免），故天然不进统计。

【efficacy 闭环 — 约束注入有效性追踪 + 无效自动停注（2026-05-31 · 对照 self_heal regression 范式）】
原 learning_loop 是**开环**：把复发问题升级成 recur_* failure_pattern 注入下章 writer
（confidence>=0.5 → build_manifest 无条件注入），但从不验证「注入这条约束后该问题的后续误报是否真降」。
无效约束（注入后误报没降/反升）会被 confidence=0.95 持续注入，污染 writer prompt。本层闭合
复盘→改进环（与第 6 轮 holdout 正交：holdout 测蒸馏复刻泛化，这里测约束注入有效性）：
  1. 约束首次升级（_escalate_recurring）→ _record_efficacy_baseline 记基线（注入前复发率 +
     注入时已见章集合 baseline_chapters）；基线锚最早注入点·重升级不重置。
  2. 后续每次 ingest/scan → evaluate_efficacy 算「注入后窗口」（章号 > 基线最大章）的复发率：
     注入后真正跑过的章数（observed·分母）攒够 EFFICACY_MIN_POST_CHAPTERS 才判（不冤判）。
  3. 注入后复发率没降（>= 基线 · 含反升）→ 标 ineffective + 该 failure_pattern active=False
     → build_manifest 不再注入下章 writer（停注）。advisory 软停（不硬删 pattern·保留供人工
     复核 / 手动 active=true 复活·北极星⑤）。effective 不抖动回退·ineffective 维持停注。

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

【reflect 归因闭环 — skill 段落级反射 credit-assignment（2026-05-31 · 唯一没闭的环）】
writing-side 风格失败（scanner/judge 检出的 STYLE_* 复发 + 跨 cluster 长程漂移）原本只
**advisory 报出**，从不归因到 skill_vN.md 的**具体段落**（开环·不知哪条 skill 没生效）。
本层借 GEPA 的 reflective credit-assignment 一招（不做 GEPA Pareto 多版本 / merge 交叉·
论文自承不稳·违北极星⑥）：持续风格偏离 → 反射归因到 skill 最相关标题段落 → 产 advisory
「该改哪条 skill」改写建议（写 写作经验.json.skill_rewrite_suggestions·给人/复盘消费）。
  · 永远 advisory —— 绝不自动改 skill_vN.md（skill = 作者风格第一权威·人定·北极星⑤）；
  · 复用 _recurrence_tracker（已按 dimension::code 攒复发章）+ STYLE_DIM 概念·不新立系统；
  · env LL_REFLECT_ATTRIB 默认 active（off/0/false 关）；--scan-recurring 末尾自动带跑。

【命令行接口】
  python learning_loop.py <项目路径> --merge-reflection <reflection.json路径>
  python learning_loop.py <项目路径> --ingest <audit报告路径>
  python learning_loop.py <项目路径> --scan-recurring
  python learning_loop.py <项目路径> --reflect-attribution   # 单跑 reflect 归因（默认随 scan 带跑）

退出码：0=正常 / 1=检测到复发问题已升级约束（或产出校准建议 / skill 改写建议）/ 2=致命错误（路径/JSON）
"""
from __future__ import annotations

import json
import re  # 🔴 2026-06-27 C07：_bridge_pid_state(L226 re.search) 缺此 import·NameError 被调用方 try/except 静默吞·P0-04 PID 桥从未生效
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import atomic_json  # 2026-05-30 修[#6]：写作经验.json 原子写，防多写者半截损坏

try:
    import chapter_io as cio  # v18：统一正文/数据分离读写；v19 用于读 _changes.json 的 waivers
except ImportError:
    cio = None  # 缺失则 _changes.json 豁免源降级为不可用，audit 报告源仍工作

try:
    import cluster_lookup  # 2026-05-29 复审复修 SC-1：blueprint list 归一守卫
except Exception:
    cluster_lookup = None  # 缺模块退回原 dict 守卫

# 复发阈值：同一 dimension::code 累计出现 N 次 -> 升级为高 confidence failure_pattern
RECUR_THRESHOLD = 3
# P2-5：时间维度的 confidence 衰减 + 过期清理
EXPIRY_DAYS = 30      # pattern 超过 N 天未强化 → 自动清理
DECAY_DAYS = 14       # pattern 超过 N 天未强化 → confidence *= 0.8
# 连续 N 章命中 -> 视为"约束升级"级别（更高 confidence）
CONSECUTIVE_ESCALATE = 2
# v19 豁免阈值：同一 advisory code 被豁免 N 次 -> 产出工具校准建议（V19_PLAN 2.5 建议 N=3）
WAIVER_CALIBRATION_THRESHOLD = 3

# ── efficacy 闭环（2026-05-31 · 对照 self_heal_engine regression 范式）──
# 开环问题：learning_loop 把复发问题升级成 recur_* failure_pattern 注入下章 writer，
# 但从不验证「注入这条约束后，该问题的后续误报是否真降」。无效约束（注入后误报没降/反升）
# 会被 confidence=0.95 持续注入，污染 writer prompt。此层闭合复盘→改进环：
#   1. 约束升级时记基线（escalate 当时的复发率 = count / 已见章数）；
#   2. 后续每次 ingest/scan 评估「升级后新增章节」里该约束的复发率；
#   3. 注入后复发率没降（>= 基线 · 且攒够最小评估证据）→ 标 ineffective + active=False（停注）。
# advisory：active=False 是「建议停注」软停（不硬删 pattern·北极星⑤），保留供人工复核 / 反转。
# EFFICACY_MIN_POST_CHAPTERS：升级后至少新见 N 章（够独立验证）才下「无效」判定（够样本·不冤判）。
EFFICACY_MIN_POST_CHAPTERS = 2
# 允许的相对改善容差：post_rate <= pre_rate*(1-TOL) 才算「真降」；否则视为没降（保守判无效）。
EFFICACY_IMPROVE_TOLERANCE = 0.0   # 0 = 只要没升就不算无效（严格要求「真降」可调 >0）

# ── L2-1（2026-05-30）：把 adjust_threshold 文本建议升级成【量化幅度】供 PID 反馈消费 ──
# 反复豁免的 advisory code → 映射到被控的 4 个连续阈值键（与 pid_threshold_tuner._CONTROLLED_KEYS
# 严格一致·绝不含任何 HARD_GATE_CODE）。其余 code 不产量化幅度（text 建议照旧）。
# 量化幅度 = 保守相对放松量（随豁免次数温和增长·钳上限），作为 PID 期望误差方向的【前置提示】，
# 不直接改阈值（真正改阈值仍由 PID 控制器闭环·此处只给「该往放松方向走多少」的量化锚）。
_CODE_TO_CONTROLLED_KEY = {
    "STYLE_段落均长":   "para_mean_len",
    "STYLE_对话占比":   "dialogue_ratio",
    "STYLE_长段计数":   "long_para_per_chapter",
    "STYLE_配额词":     "quota_per_word",
    "STYLE_QUOTA_WORD": "quota_per_word",
    "STYLE_LONG_PARA":  "long_para_per_chapter",
    "STYLE_PARA_MEAN":  "para_mean_len",
    "STYLE_DIALOGUE":   "dialogue_ratio",
    # 🔴 2026-06-27 P1-07: 章末弱锚 advisory 3 码 → chapter_end_weak_anchor_ratio
    # （CHAPTER_END_FORBIDDEN_* 仍是 hard_gate·不映射·北极星⑤物理隔离）
    "CHAPTER_END_NO_ANCHOR":          "chapter_end_weak_anchor_ratio",
    "CHAPTER_END_WEAK_ANCHOR":        "chapter_end_weak_anchor_ratio",
    "CHAPTER_END_CLOSURE_ADVISORY":   "chapter_end_weak_anchor_ratio",
}
_QUANT_RELAX_PER_WAIVER = 0.02   # 每次豁免对应 2% 相对放松提示
_QUANT_RELAX_CAP = 0.10          # 量化幅度上限 10%（保守·防 windup）


def _quantized_delta_hint(code: str, waived_count: int) -> dict | None:
    """把反复豁免次数转成给 PID 的【量化放松幅度提示】（仅 4 被控键·硬拒其余）。
    返回 {controlled_key, relax_frac, basis} 或 None（非被控键 → 不量化）。"""
    key = _CODE_TO_CONTROLLED_KEY.get(code)
    if key is None:
        return None
    relax = min(_QUANT_RELAX_CAP, max(0, waived_count) * _QUANT_RELAX_PER_WAIVER)
    return {"controlled_key": key, "relax_frac": round(relax, 4),
            "basis": f"waived×{waived_count}"}


def accumulate_pid_state_from_calibration(author_dir, suggestions, cluster_id=None) -> dict | None:
    """cluster-save-state 运行积累路径（北极星⑤）：把本 cluster 产出的 quantized_delta
    校准建议喂进 per-作者 PID 控制器（update_controller·保守增益+抗 windup+死区），
    在【回测初始化的 theta_delta】基础上低频累积微调。

    · 只读写 4 被控键（pid_threshold_tuner 硬拒其余·物理隔离 HARD_GATE 回路外）。
    · cluster_id 给低频守卫（同 cluster 不重复迭代）。
    · author_dir 缺失 / 无 quantized_delta → 返回 None（无害空转）。
    · 误差 e 用 relax_frac 当「期望放松量」正向驱动（FPR 代理·放松方向 e>0）。
    返回更新后 state（已落盘）或 None。"""
    if author_dir is None:
        return None
    try:
        import pid_threshold_tuner as _pid  # noqa: E402
    except Exception:
        return None
    errors = {}
    evidence = 0  # 校准证据样本量（豁免次数累加·驱动死区随证据收窄）
    for s in (suggestions or []):
        qd = s.get("quantized_delta") if isinstance(s, dict) else None
        if not isinstance(qd, dict):
            continue
        key = qd.get("controlled_key")
        if key not in _pid._CONTROLLED_KEYS:
            continue  # 物理隔离：非白名单一律忽略（不 raise·积累路径要稳）
        # 同键多 code 取最大放松量（保守上界·不叠加放大）
        errors[key] = max(errors.get(key, 0.0), float(qd.get("relax_frac", 0.0)))
        evidence = max(evidence, int(s.get("waived_count", 0)))
    if not errors:
        return None
    state = _pid.load_state(Path(author_dir))
    # n_samples 取「已积累样本」与「本批校准证据」的较大者：豁免次数即证据样本，
    # 否则 relax_frac(≤0.10) 恒落在 n=0 的宽死区(~0.14)内 → 永不积累(死锁)。
    # 死区仍随证据缩放（少证据不轻易动阈值·北极星⑤保守）。
    n = max(int(state.get("n_samples", 0)), evidence, _pid._MIN_SAMPLES)
    _pid.update_controller(state, errors, cluster_id=cluster_id, n_samples=n)
    _pid.save_state(Path(author_dir), state)
    return state

# 🔴 2026-06-27 P0-04 PID 桥 helper：从 audit/experience 派生 author_dir + cluster_id·调 accumulate_pid_state
def _bridge_pid_state(project_root: Path, audit_path) -> None:
    """ingest/scan-recurring 末尾接通 PID 桥·让 quantized_delta 真落 pid_threshold_state.json。"""
    # 1. 找 author_dir（作者风格档目录）
    author_dir = None
    try:
        sp = Path(project_root) / "_数据库" / "作者风格.json"
        if sp.exists():
            sj = json.loads(sp.read_text(encoding="utf-8"))
            src = sj.get("style_source") or sj.get("_source_path")
            if src:
                author_dir = Path(src).parent if Path(src).is_file() else Path(src)
    except Exception:
        pass
    if author_dir is None or not Path(author_dir).exists():
        # 兜底：workspace/styles/<项目名>
        guess = Path(project_root).parent.parent / "styles" / Path(project_root).name
        if guess.exists():
            author_dir = guess
    if author_dir is None:
        return  # 找不到 author_dir → 静默退出
    # 2. 读 experience tool_calibration_suggestions
    exp_path = _experience_path(Path(project_root))
    if not exp_path.exists():
        return
    try:
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
    except Exception:
        return
    suggestions = exp.get("tool_calibration_suggestions") or []
    if not suggestions:
        return
    # 3. cluster_id 从 audit_path 派生（兜底 None）
    cluster_id = None
    if audit_path is not None:
        m = re.search(r"cluster_(\d+)", str(audit_path))
        if m:
            cluster_id = f"cluster_{m.group(1)}"
    # 4. 调用 PID 积累
    state = accumulate_pid_state_from_calibration(author_dir, suggestions, cluster_id)
    if state is not None:
        print(f"[learning_loop] [pid-bridge] author_dir={author_dir.name} updated state n_samples={state.get('n_samples', 0)}")


EXPERIENCE_FILE = "写作经验.json"
AUDIT_DIR = ".audit"


# ============ 通用 IO ============

def _db_dir(project_root: Path) -> Path:
    return Path(project_root) / "_数据库"


def _experience_path(project_root: Path) -> Path:
    return _db_dir(project_root) / EXPERIENCE_FILE


def _empty_experience() -> dict:
    """权威结构的空骨架（含 v19 豁免统计段 + efficacy 闭环段 + reflect 归因段）。"""
    return {"success_patterns": [], "failure_patterns": [], "preferences": [],
            "tool_calibration_suggestions": [],
            "skill_rewrite_suggestions": [],
            "_recurrence_tracker": {}, "_waiver_tracker": {},
            # 🔴 2026-06-27 C09：apply-moment 豁免诚实审计 per-cluster ledger（喂自学习信号）
            "_waiver_audit_ledger": {},
            "_efficacy_tracker": {}}


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
    data.setdefault("skill_rewrite_suggestions", [])     # reflect 归因产出（2026-05-31）
    data.setdefault("_recurrence_tracker", {})
    data.setdefault("_waiver_tracker", {})               # v19 豁免计数内部状态
    data.setdefault("_waiver_audit_ledger", {})          # 🔴 2026-06-27 C09 apply-moment 豁免审计 ledger
    data.setdefault("_efficacy_tracker", {})             # efficacy 闭环内部状态（2026-05-31）
    if legacy_entries:
        for e in legacy_entries:
            _route_entry(data, e)
    return data


def save_experience(project_root: Path, data: dict) -> Path:
    # 2026-05-30 修[#6]：裸 write_text → 原子写。cluster-save-state step7-9 有 ≥5 个写者
    # 集中 RMW 同一 写作经验.json，旧实现非原子写——任一进程 subprocess timeout 被 kill
    # 会留半截 JSON → 下个读者 json.JSONDecodeError 兜底成空 {} → 整库 success/failure_patterns
    # 静默清空。atomic_write_json 内部已 mkdir + tmp 唯一名 + fsync + os.replace 原子落盘。
    p = _experience_path(project_root)
    atomic_json.atomic_write_json(p, data)
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
    """读 进度.json 的 cluster_blueprint，取本章 scene_type 列表（章节类型信号）。
    找不到返回 []。scene_type 形如 ["日常", "心理外化", "悬疑"]。

    🔴 2026-06-27 P1-07 cluster 模式分支：ch >= 9000 = 虚拟 cluster 章（audit_hub 给的虚拟章号）·
    反查 cluster_key = ch - 9000 + 1·聚合该 cluster 所有 scene_storyboard scene_type（去重）。
    cluster 视野下章号不可信·走 cluster 聚合更稳。
    """
    prog_path = _db_dir(project_root) / "进度.json"
    if not prog_path.is_file():
        return []
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    # 2026-05-29 复审复修 SC-1：blueprint 可能是 list（城南实测），先归一成 dict 再迭代。
    if cluster_lookup is not None:
        _bp = cluster_lookup.normalize_blueprint(prog)
    else:
        _bp = prog.get("cluster_blueprint") or {}
        if not isinstance(_bp, dict):
            _bp = {}
    # 🔴 P1-07 cluster 模式：ch >= 9000 → 反查 cluster_key 聚合 scene_storyboard scene_type
    if ch >= 9000:
        cluster_idx = ch - 9000  # 9001=cluster_001, 9002=cluster_002...
        cluster_id = f"cluster_{cluster_idx:03d}"
        cdata = _bp.get(cluster_id) if isinstance(_bp, dict) else None
        if isinstance(cdata, dict):
            aggregated = []
            seen = set()
            for p in cdata.get("scene_storyboard", []) or []:
                if not isinstance(p, dict):
                    continue
                st = p.get("scene_type") or []
                if isinstance(st, str):
                    st = [st]
                for t in st:
                    if t and t not in seen:
                        seen.add(t)
                        aggregated.append(t)
            return aggregated
    for cid, cdata in _bp.items():
        if not isinstance(cdata, dict):
            continue
        for p in cdata.get("scene_storyboard", []):
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

    # 🔴 2026-06-27 C09：把 audit_hub apply-moment 算的 waiver_audit 信号落 per-cluster ledger。
    # _track_waivers 上面已 ingest 本章/本 cluster 豁免「计数」，这里补「apply-moment 比率/blanket/orphan」
    # 维度——纯观察信号喂自学习，绝不翻 verdict、绝不剥合法 waiver（北极星护栏 · META-only）。
    wa = audit.get("waiver_audit")
    if isinstance(wa, dict):
        ledger = exp.setdefault("_waiver_audit_ledger", {})
        is_cluster = audit.get("_cluster_mode") is True
        ck = audit.get("_cluster_key", "")
        key = f"cluster::{ck}" if (is_cluster and ck) else f"ch::{ch}"
        ledger[key] = {
            "waive_rate": wa.get("waive_rate", 0.0),
            "advisory_total": wa.get("advisory_total", 0),
            "advisory_waived": wa.get("advisory_waived", 0),
            "blanket_suspected": bool(wa.get("blanket_suspected", False)),
            "orphan_codes": list(wa.get("orphan_codes", []) or []),
            "repeated_reason_codes": dict(wa.get("repeated_reason_codes", {}) or {}),
            "ts": _now(),
        }
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
        # L2-1：adjust_threshold 类且命中被控 4 键 → 附【量化幅度】供 PID 反馈消费
        # （text 建议照旧·此为新增字段·零回归）。add_scene_adaptation 类不量化（属场景适配范畴）。
        if stype == "adjust_threshold":
            qd = _quantized_delta_hint(code, rec["count"])
            if qd is not None:
                entry["quantized_delta"] = qd
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

    # v2 cluster 化（2026-05-28）：识别 audit 是否来自 cluster 视野扫描
    _is_cluster_mode = audit.get("_cluster_mode") is True
    _cluster_key = audit.get("_cluster_key", "")

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
        # v2 cluster 化：cluster 视野 issue 用独立 key 前缀，避免 chapter 视野跟 cluster 视野混算复发
        if _is_cluster_mode:
            key = f"cluster::{key}"
        if key in seen_keys:
            continue  # 同章同类只计一次
        seen_keys.add(key)
        rec = tracker.setdefault(key, {"count": 0, "chapters": [],
                                       "first_seen": ch, "last_seen": ch,
                                       "dimension": issue.get("dimension", "unknown"),
                                       "sample_desc": issue.get("desc", ""),
                                       "_view_mode": "cluster" if _is_cluster_mode else "chapter",
                                       "_cluster_keys": [_cluster_key] if _is_cluster_mode else []})
        if ch not in rec["chapters"]:
            rec["count"] += 1
            rec["chapters"].append(ch)
            rec["chapters"].sort()
        rec["last_seen"] = ch
        # v2 cluster 视野：累计 cluster_key 列表（便于追溯）
        if _is_cluster_mode and _cluster_key and _cluster_key not in rec.get("_cluster_keys", []):
            rec.setdefault("_cluster_keys", []).append(_cluster_key)

    escalated = _escalate_recurring(exp, only_keys=seen_keys)

    # v19 块 2.5：统计豁免 -> 反复豁免同一 code 命中阈值 -> 产出工具校准建议
    waived_codes = _track_waivers(exp, project_root, audit, ch)
    calib = _build_calibration_suggestions(exp, only_codes=waived_codes)

    # efficacy 闭环：评估已升级约束「注入后复发率是否真降」→ 无效约束自动停注（advisory）。
    # --ingest 拿不到全章集 → observed_chapters=None 退化为「只看复发章」（保守·偏向继续注入）。
    ineffective = evaluate_efficacy(exp, observed_chapters=None)

    save_experience(project_root, exp)

    print(f"[ingest] 第{ch}章 audit 报告已吸收，{len(seen_keys)} 类问题入复发追踪"
          f"，{len(waived_codes)} 类豁免入豁免追踪")
    if ineffective:
        print(f"[ingest] EFFICACY {len(ineffective)} 条约束注入后误报未降 -> 自动停注（advisory）：")
        for x in ineffective:
            print(f"  - {x['pattern_id']}（基线 {x['baseline_rate']} -> 注入后 {x['post_rate']}）")
    if calib:
        print(f"[ingest] CALIB {len(calib)} 类检测项反复被豁免，已产出工具校准建议：")
        for c in calib:
            print(f"  - [{c['code']}] 豁免 {c['waived_count']} 次 -> "
                  f"{c['suggestion_type']}（{c['scene_type_hint'] or '调阈值'}）")
    if escalated:
        print(f"[ingest] WARN {len(escalated)} 类问题命中复发阈值，已升级为 failure_pattern：")
        for e in escalated:
            print(f"  - {e['trigger']}（已连续/累计 {e['_recurrence']} 章）")
    return {"escalated": escalated, "calibration": calib,
            "ineffective": ineffective, "ch": ch}


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
        # 🔴 2026-06-27 P2-11：cluster 视野阈值 3→2 解锁 efficacy 闭环·chapter 视野保持 3
        # （rec._view_mode = 'cluster'/'chapter'·line 651 已写入）
        _threshold = 2 if rec.get("_view_mode") == "cluster" else RECUR_THRESHOLD
        if rec["count"] < _threshold:
            continue
        consecutive = _is_consecutive(rec["chapters"], CONSECUTIVE_ESCALATE)
        # 连续命中 -> 0.95（约束升级级别）；累计命中 -> 0.8
        confidence = 0.95 if consecutive else 0.8
        pattern_id = f"recur_{key.replace('::', '_').replace(' ', '')}"
        # key = "dimension::code"，trigger 要带上 code 才能区分同维度下的不同问题
        # （如 结构::NARRATIVE_pov 与 结构::PLOT_beat 都属"结构"维度，不能混为一谈）
        dim = rec.get("dimension", "?")
        code = key.split("::", 1)[1] if "::" in key else key
        # efficacy 闭环：保留已有 pattern 的 active 标记。若之前已被判 ineffective→停注
        # （active=False），覆盖刷新时**不**复活它——否则停注的无效约束又被注入（前功尽弃）。
        prev = next((x for x in fp if x.get("id") == pattern_id), None)
        prev_active = prev.get("active") if isinstance(prev, dict) else None
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
        if prev_active is False:
            entry["active"] = False  # 已判无效·维持停注（advisory·人工复核可手动改回 true）
            if isinstance(prev, dict) and prev.get("efficacy"):
                entry["efficacy"] = prev["efficacy"]
        # efficacy 闭环：首次升级该 key → 记基线（注入前的复发率 + 当前已见章），
        # 供后续 evaluate_efficacy 对照「注入后的复发率是否真降」。重升级不重置基线
        # （基线必须锚在最早的注入点，否则后续误报被算进基线就永远「没复发」假象）。
        _record_efficacy_baseline(exp, key, pattern_id, rec)
        # 同 id 覆盖刷新
        fp[:] = [x for x in fp if x.get("id") != pattern_id]
        _stamp_updated(entry)  # P2-5：盖时间戳
        fp.append(entry)
        escalated.append(entry)
    return escalated


# ============ efficacy 闭环：约束注入有效性追踪 + 无效自动停注 ============

def _recur_rate(count: int, chapters) -> float:
    """复发率 = 复发次数 / 涉及的不同章节数（每章每类只计一次·见 ingest 去重）。
    章数为 0 时返回 0.0（无样本）。"""
    n = len(set(chapters)) if chapters else 0
    return (count / n) if n else 0.0


def _record_efficacy_baseline(exp: dict, key: str, pattern_id: str, rec: dict) -> None:
    """约束首次升级时记基线：注入前复发率 + 注入「点」之前的复发章集合（baseline_chapters）。
    幂等：已有基线则不覆盖（基线锚最早注入点·北极星⑥不复杂化）。

    基线锚「阈值刚被跨过」的那一刻——而非「当前已见的所有章」。否则 --scan-recurring 全量重建
    时 rec.chapters 一次性灌入全部历史章，base_max 会等于最后一章 → 注入后窗口恒为空（永不评估）。
    阈值跨越点 = 排序后第 RECUR_THRESHOLD 章（chapters[RECUR_THRESHOLD-1]）；该点及之前算「注入前」，
    之后的章才算「注入后」（约束真正开始注入下章 writer 的窗口）。"""
    et = exp.setdefault("_efficacy_tracker", {})
    if pattern_id in et:
        return  # 已有基线·重升级不重置
    all_chs = sorted(set(rec.get("chapters", [])))
    # 注入前 = 阈值跨越点及之前的章（约束尚未生效·这些复发不该算进有效性评估）
    cross_idx = min(RECUR_THRESHOLD, len(all_chs)) - 1
    baseline_chapters = all_chs[:cross_idx + 1] if cross_idx >= 0 else all_chs
    baseline_count = len(baseline_chapters)
    et[pattern_id] = {
        "key": key,
        "baseline_count": baseline_count,
        "baseline_chapters": baseline_chapters,
        "baseline_rate": round(_recur_rate(baseline_count, baseline_chapters), 4),
        "escalated_at": _now(),
        "status": "monitoring",                # monitoring → effective / ineffective
    }


def evaluate_efficacy(exp: dict, observed_chapters=None) -> list:
    """对照 self_heal regression 范式：逐条已升级约束评估「注入后复发率是否真降」。

    observed_chapters：本闭环已观察到的全部章号集合（注入后到底跑了多少章·分母）。
      · --scan-recurring 传所有扫到的章；--ingest 不易知全集 → 传 None 退化为「只看复发章」。
    注入后窗口（章号 > 基线最大章 的部分）：
      · post_observed = 注入后真正跑过的章数（分母·有 observed_chapters 时用它，更准）；
      · post_recur    = 注入后该约束仍复发的章数（分子）。
    判定（仅当 post_observed >= EFFICACY_MIN_POST_CHAPTERS·够样本才判·否则维持 monitoring）：
      · post_rate(=post_recur/post_observed) < baseline_rate*(1-TOL) → effective（误报真降·继续注入）；
      · 否则（没降 / 反升）→ ineffective + 该 failure_pattern active=False（停注·advisory 软停）。
    终态不抖动：effective 不回退·ineffective 维持停注（人工复核可手动 active=true 复活）。
    返回本轮新判 ineffective 的约束列表（供报告 + exit code）。"""
    et = exp.get("_efficacy_tracker", {})
    tracker = exp.get("_recurrence_tracker", {})
    fp = exp.get("failure_patterns", [])
    fp_by_id = {x.get("id"): x for x in fp if isinstance(x, dict)}
    obs = set(observed_chapters) if observed_chapters else None
    newly_ineffective = []
    for pattern_id, eff in et.items():
        if eff.get("status") in ("effective", "ineffective"):
            continue  # 终态不重判（ineffective 维持停注·effective 不抖动回退）
        rec = tracker.get(eff.get("key"))
        if not rec:
            continue  # 该 key 已不在追踪（被全量重建剔除）→ 留 monitoring·下轮再说
        baseline_chs = set(eff.get("baseline_chapters", []))
        base_max = max(baseline_chs) if baseline_chs else -1
        # 注入后复发章：升级后才出现的复发（章号 > 基线最大章）
        post_recur_chs = sorted(c for c in set(rec.get("chapters", [])) if c > base_max)
        post_recur = len(post_recur_chs)
        # 注入后真正跑过的章数（分母）：有全集就用全集里 > base_max 的；否则退化用复发章数
        if obs is not None:
            post_observed = len([c for c in obs if c > base_max])
        else:
            post_observed = post_recur
        eff["post_recur_chapters"] = post_recur_chs
        eff["post_observed"] = post_observed
        if post_observed < EFFICACY_MIN_POST_CHAPTERS:
            continue  # 证据不足·维持 monitoring（不冤判）
        post_rate = round(post_recur / post_observed, 4) if post_observed else 0.0
        eff["post_rate"] = post_rate
        eff["evaluated_at"] = _now()
        baseline_rate = float(eff.get("baseline_rate", 0.0))
        threshold = baseline_rate * (1.0 - EFFICACY_IMPROVE_TOLERANCE)
        if post_rate < threshold or (post_rate == 0.0 and baseline_rate > 0):
            eff["status"] = "effective"  # 误报真降 / 消失 → 约束有效·继续注入
        else:
            # 没降 / 反升 → 无效约束·停注（advisory 软停·不硬删）
            eff["status"] = "ineffective"
            eff["stopped_at"] = _now()
            pat = fp_by_id.get(pattern_id)
            if isinstance(pat, dict):
                pat["active"] = False
                pat["efficacy"] = {
                    "verdict": "ineffective",
                    "baseline_rate": baseline_rate,
                    "post_rate": post_rate,
                    "post_recur_chapters": post_recur_chs,
                    "note": (f"注入后该问题复发率未下降（基线 {baseline_rate} → 注入后 "
                             f"{post_rate}）→ 约束无效·已自动停注（advisory·人工复核后可手动 "
                             "active=true 复活）"),
                    "stopped_at": eff["stopped_at"],
                }
            newly_ineffective.append({
                "pattern_id": pattern_id, "key": eff.get("key"),
                "baseline_rate": baseline_rate, "post_rate": post_rate,
                "post_recur_chapters": post_recur_chs,
            })
    return newly_ineffective


# ============ reflect 归因闭环：风格失败 → skill 段落归因 → advisory 改写建议 ============
# 【为什么有这一层 · 唯一没闭的环】
# writing-side 的风格失败（scanner / judge 检出 STYLE_* 复发 + 跨 cluster 长程漂移）原本只
# **advisory 报出**，从不归因到 skill_vN.md 的**具体段落** —— 开环：知道「风格没复刻好」，
# 但不知道「是 skill 的哪条没生效 / 哪条该改」。本层借 GEPA 的 reflective credit-assignment
# 思路（只取「反射归因」这一招·不做 GEPA Pareto 多版本 / merge 交叉——论文自承不稳·违北极星⑥）：
#   持续风格偏离 → 把失败的 STYLE_ code/维度反射归因到 skill 里**最相关的标题段落** →
#   产出 advisory 「该改哪条 skill」改写建议（写 skill_rewrite_suggestions 段·给人/复盘）。
#
# 【北极星边界】⑤不干涉模型判断：
#   · 永远 advisory —— 只产**建议**，绝不自动改写 skill_vN.md（skill = 作者风格第一权威·人定）；
#   · 复用 learning_loop 既有框架（load/save_experience + _recurrence_tracker + STYLE_DIM 概念），
#     不新立系统；
#   · cluster 为单位的复发证据驱动（_recurrence_tracker 已按 dimension::code 攒复发章）。
#
# env：LL_REFLECT_ATTRIB（默认 active；设 off/0/false/no → 关）。

# 风格失败 code → (skill 段落定位关键词, 该风格维度的人话名)。
# 关键词用于在 skill markdown 的标题/正文里**反射定位**最相关段落（credit-assignment）。
# 只覆盖 STYLE_* 风格类 code（一致性 / 文件契约类 hard_gate 与 skill 无关·不归因）。
_STYLE_CODE_ATTRIB = {
    "STYLE_对话占比":     (("对话占比", "对话量", "对话风格", "对话"), "对话占比"),
    "STYLE_段落均长":     (("段长", "段落均长", "段落结构", "平均段长", "段落"), "平均段长"),
    "STYLE_单段超长":     (("段长", "单段", "段落硬约束", "字数硬上限", "段落"), "单段长度上限"),
    "STYLE_长段计数":     (("段长", "长段", "段落硬约束", "段落"), "长段计数"),
    "STYLE_极短段占比":   (("极短段", "段长策略", "单句独行", "段落"), "极短段占比"),
    "STYLE_单句成段率":   (("单句独行", "单句段", "单句成段", "段落"), "单句成段率"),
    "STYLE_单句独行占比": (("单句独行", "单句段", "段落"), "单句独行占比"),
    "STYLE_拟声格式":     (("拟声", "拟声词独段", "拟声段", "战斗描写"), "拟声词独段"),
    "STYLE_禁用词":       (("禁用词", "禁用", "AI 套话", "AI套话", "反 AI"), "禁用词"),
    "STYLE_配额词":       (("限频", "配额词", "白名单", "禁用词"), "配额词限频"),
    "STYLE_AI对话标签":   (("对话标签", "对话", "AI 套话", "AI腔"), "AI 对话标签"),
    "STYLE_逗句比":       (("逗号", "逗句比", "长句", "句长", "句式节奏"), "逗号/句号比"),
    "STYLE_极长句":       (("句长", "极长句", "长句", "句式节奏"), "极长句"),
    "STYLE_章节字数":     (("章字数", "字数", "字数硬下限", "量化"), "章节字数"),
    "STYLE_章节末":       (("章末", "衔接", "收尾", "钩子"), "章末/衔接"),
    "STYLE_DRIFT":        (("量化", "签名", "风格指纹", "节奏"), "整体作者文风（长程漂移）"),
    "LONGRANGE_STYLE_DRIFT": (("量化", "签名", "风格指纹", "节奏"), "整体作者文风（长程漂移）"),
}

# 反射归因触发线：同一风格 code 复发 >= N 章 → 视为「持续风格偏离」（值得归因到 skill 段落）。
REFLECT_RECUR_THRESHOLD = 2


def _reflect_enabled() -> bool:
    """env LL_REFLECT_ATTRIB：默认 active（off/0/false/no 关·北极星②默认全开）。"""
    import os
    v = (os.environ.get("LL_REFLECT_ATTRIB") or "").strip().lower()
    return v not in ("off", "0", "false", "no", "disable", "disabled")


def _resolve_skill_path(project_root: Path) -> "Path | None":
    """从 _数据库/作者风格.json 的 style_source 解析出本项目使用的 skill_vN.md 绝对路径。
    style_source 是相对仓库根的路径（如 workspace/styles/惊悚乐园/skill_FINAL.md）。
    缺文件 / 缺字段 / 路径不存在 → None（归因降级为「无 skill 可定位」·不报错·北极星⑥不复杂化）。"""
    style_json = _db_dir(project_root) / "作者风格.json"
    if not style_json.is_file():
        return None
    try:
        data = json.loads(style_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    src = data.get("style_source")
    if not src or not isinstance(src, str):
        return None
    cand = Path(src)
    if cand.is_absolute() and cand.is_file():
        return cand
    # 相对路径：先按仓库根（learning_loop 在 core/scripts/ → 上溯 2 层）解析，再按项目根兜底。
    repo_root = _SCRIPT_DIR.parent.parent
    for base in (repo_root, Path(project_root)):
        p = (base / src)
        if p.is_file():
            return p
    return None


def _parse_skill_sections(skill_text: str) -> list:
    """把 skill markdown 切成「标题段」列表：[{heading, level, line, body}]。
    heading = markdown 标题行文本（去 # 前缀）；body = 该标题到下一个同级/更高级标题之间的正文。
    用于反射归因时按关键词命中标题/正文定位**具体段落**（credit-assignment 的「段落」单位）。"""
    lines = skill_text.splitlines()
    sections = []
    cur = None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            heading = s.lstrip("#").strip()
            if cur is not None:
                sections.append(cur)
            cur = {"heading": heading, "level": level, "line": i + 1, "body_lines": []}
        elif cur is not None:
            cur["body_lines"].append(raw)
    if cur is not None:
        sections.append(cur)
    for sec in sections:
        sec["body"] = "\n".join(sec.pop("body_lines"))[:600]  # 截 600 字防 advisory 过长
    return sections


def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。
    原样复制自 topic_drift_scanner.py（本仓约定：每个消费 embedding 的脚本自带一份·
    不 import 跨脚本依赖）。也检查 .env 的 GEN_EMBED__* API 配置。"""
    import os
    v = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if v and v != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


# 🔬 待金标准校准：语义归因最小相似度下限（低于此值视为「没有真正相关的段落」，回退关键词
# 计数法而非强行归因到一个弱相关段落）。
SEMANTIC_ATTRIB_FLOOR = 0.30


def _semantic_attribute_to_skill_section(sections: list, keywords) -> "dict | None":
    """真后端时的语义归因：keywords 拼成查询文本，对每段 heading+body 做 embedding 余弦
    排序，取最相关段（能抓「对话要简短」vs「台词不宜过长」这类零字面重叠的同义表述）。
    embedding_store 不可用 / 查询为空 / 无段落越过 floor → None（调用方回退关键词计数法）。"""
    query = " ".join(str(k) for k in keywords if k).strip()
    if not query:
        return None
    try:
        from embedding_store import compute_embedding, cosine_similarity
        q_emb = compute_embedding(query)
    except Exception:
        return None
    if not q_emb:
        return None
    best, best_sim = None, 0.0
    for sec in sections:
        blob = (sec.get("heading", "") + " " + sec.get("body", "")).strip()
        if not blob:
            continue
        try:
            s_emb = compute_embedding(blob)
        except Exception:
            continue
        if not s_emb or len(s_emb) != len(q_emb):
            continue
        sim = cosine_similarity(q_emb, s_emb)
        if sim > best_sim:
            best, best_sim = sec, sim
    if best is None or best_sim < SEMANTIC_ATTRIB_FLOOR:
        return None
    return {"heading": best["heading"], "level": best["level"],
            "line": best["line"], "score": round(best_sim, 4),
            "excerpt": best["body"].strip()[:200], "method": "semantic"}


def _attribute_to_skill_section(sections: list, keywords) -> "dict | None":
    """反射归因核心：在 skill 段落里找与失败维度关键词**最相关**的标题段。

    真后端（embedding_store 配置）时：优先走语义相似度排序（见
    _semantic_attribute_to_skill_section）；embedding 不可用 / 无段落越过相似度下限 →
    回退关键词计数：标题命中关键词 ×3（标题最能代表段落主旨）+ 正文命中 ×1。
    无任何命中 → None（不强行归因·宁可标「无定位」也不乱指·北极星⑤不干涉）。"""
    if not sections:
        return None
    if _has_real_embedding_backend():
        sem = _semantic_attribute_to_skill_section(sections, keywords)
        if sem is not None:
            return sem
    best, best_score = None, 0
    for sec in sections:
        heading = sec.get("heading", "")
        body = sec.get("body", "")
        score = 0
        for kw in keywords:
            if kw in heading:
                score += 3
            if kw in body:
                score += 1
        if score > best_score:
            best, best_score = sec, score
    if best is None or best_score == 0:
        return None
    return {"heading": best["heading"], "level": best["level"],
            "line": best["line"], "score": best_score,
            "excerpt": best["body"].strip()[:200]}


def _collect_live_drift_findings(project_root: Path) -> list:
    """次通道 source-2 接线：in-process 复跑 cross_cluster_style_drift_scanner.scan()
    取本项目当前的 LONGRANGE_STYLE_DRIFT advisory findings（复用 build_drift_curve /
    build_drift_findings·不起 subprocess、不解析 stdout——scanner main() 只把摘要打 stdout
    不落 JSON，捕 stdout 脆且会漏巨大正文）。

    findings 同时取 active 模式的 report['issues'] 与 shadow 模式的 report['shadow_findings']：
    scanner 的 shadow/active 闸只控「是否进 audit_hub exit code」，**不该屏蔽 credit-assignment
    的归因信号**——长程漂移对 reflect 归因永远是有效证据（北极星⑤ advisory 不黑箱）。

    全防御：scanner 不可导入 / 样本不足 / scan 抛错 → 返回 []（降级·绝不打断 learning 流水线·
    北极星⑥不复杂化）。code 恒 advisory（scanner 已双保险强制 · 这里不再改 gate）。"""
    try:
        import cross_cluster_style_drift_scanner as ccsd  # type: ignore
    except Exception:
        return []
    try:
        report = ccsd.scan(Path(project_root))
    except Exception:
        # scanner 内部任何异常（缺依赖 / 文本编码 / 相似度模型 offline）都不该让 reflect 崩
        return []
    if not isinstance(report, dict):
        return []
    findings = []
    for key in ("issues", "shadow_findings"):
        v = report.get(key)
        if isinstance(v, list):
            findings.extend(f for f in v if isinstance(f, dict)
                            and f.get("code") in _STYLE_CODE_ATTRIB)
    return findings


def _collect_persistent_style_failures(exp: dict, drift_findings=None) -> dict:
    """汇总「持续风格偏离」证据：
      · 来源 1：_recurrence_tracker 里 STYLE_* code 复发 >= REFLECT_RECUR_THRESHOLD 章；
      · 来源 2（可选）：cross_cluster_style_drift_scanner 的 LONGRANGE_STYLE_DRIFT advisory findings。
    返回 {style_code: {chapters[], count, dimension, sample_desc, source}}。
    只看 STYLE_* / 漂移 code —— 一致性 / 文件契约类与 skill 段落无关·不归因（北极星⑤）。"""
    out = {}
    tracker = exp.get("_recurrence_tracker", {})
    for key, rec in tracker.items():
        # key 形如 "维度::CODE" 或 "cluster::维度::CODE"
        code = key.split("::")[-1]
        if not (code.startswith("STYLE_") or code in _STYLE_CODE_ATTRIB):
            continue
        if rec.get("count", 0) < REFLECT_RECUR_THRESHOLD:
            continue
        out[code] = {
            "chapters": sorted(set(rec.get("chapters", []))),
            "count": rec.get("count", 0),
            "dimension": rec.get("dimension", "风格"),
            "sample_desc": rec.get("sample_desc", ""),
            "source": "recurrence",
        }
    # 跨 cluster 长程漂移（advisory · 单独证据流·总是值得归因「整体文风」段落）
    for f in (drift_findings or []):
        if not isinstance(f, dict):
            continue
        code = f.get("code")
        if code not in _STYLE_CODE_ATTRIB:
            continue
        metric = f.get("metric", {}) if isinstance(f.get("metric"), dict) else {}
        out.setdefault(code, {
            "chapters": [],
            "count": metric.get("n_points", REFLECT_RECUR_THRESHOLD),
            "dimension": "风格",
            "sample_desc": (f.get("message", "") or "")[:120],
            "source": "longrange_drift",
        })
    return out


def reflect_attribution(project_root: Path, drift_findings=None) -> list:
    """GEPA 式 reflective credit-assignment（advisory · 默认 active · 北极星⑤不干涉）：
    持续风格失败 → 反射归因到 skill_vN.md 的**具体标题段落** → 产 advisory 改写建议
    （写 写作经验.json.skill_rewrite_suggestions·非自动改 skill·给人/复盘消费）。

    drift_findings 语义（次通道 source-2 接线·2026-05-31）：
      · None（默认·两个真实调用点 --scan-recurring / --reflect-attribution 都这么传）
        → in-process 复跑 cross_cluster scanner 自动收集 LONGRANGE_STYLE_DRIFT findings；
      · 显式 list（含 []）→ 直接当证据用（[] = 明示「无长程漂移」·测试/外部编排可覆盖·不再 live 扫）。

    幂等：同 code 覆盖刷新（重跑同证据不产重复）。返回本轮产出的建议列表。
    env LL_REFLECT_ATTRIB=off → 跳过（返回 []·不动文件）。"""
    if not _reflect_enabled():
        return []
    # source-2 接线：未显式传 drift_findings 时自动 live 收集（让两个真实调用点拿到长程漂移证据）。
    if drift_findings is None:
        drift_findings = _collect_live_drift_findings(project_root)
        if drift_findings:
            print(f"[reflect-attrib] 自动收集到 {len(drift_findings)} 条跨 cluster 长程漂移 advisory "
                  f"findings（source-2·in-process scan）")
    exp = load_experience(project_root)
    failures = _collect_persistent_style_failures(exp, drift_findings)
    if not failures:
        # 无持续风格失败 → 不动既有建议（增量·别误删人工尚未处理的旧建议）
        return []
    skill_path = _resolve_skill_path(project_root)
    sections = []
    skill_rel = None
    if skill_path is not None:
        try:
            sections = _parse_skill_sections(skill_path.read_text(encoding="utf-8"))
            skill_rel = str(skill_path)
        except OSError:
            sections = []

    suggestions = exp["skill_rewrite_suggestions"]
    produced = []
    for code, ev in failures.items():
        kw, human = _STYLE_CODE_ATTRIB.get(code, ((), code))
        attrib = _attribute_to_skill_section(sections, kw) if sections else None
        if attrib is not None:
            loc = (f"skill 段落「{attrib['heading']}」（第 {attrib['line']} 行）"
                   if skill_rel else f"skill 段落「{attrib['heading']}」")
            rewrite = (f"风格维度【{human}】在第 {ev['chapters'] or '多个'} cluster/章持续偏离作者参考"
                       f"（复发 {ev['count']} 次）—— 反射归因：{loc} 这条 skill 约束**未能让生成贴合作者**，"
                       f"建议复盘时收紧 / 具体化该段（如补量化下限 + 反例 + 章型适配），而非反复让 writer 豁免。")
        else:
            rewrite = (f"风格维度【{human}】在第 {ev['chapters'] or '多个'} cluster/章持续偏离作者参考"
                       f"（复发 {ev['count']} 次）—— 在 skill 里未定位到对应约束段落，"
                       f"建议复盘时**新增**一条针对【{human}】的 skill 约束（带量化基线 + 章型适配）。")
        entry = {
            "style_code": code,
            "style_dimension": human,
            "evidence_source": ev["source"],
            "chapters": list(ev["chapters"]),
            "recurrence": ev["count"],
            "sample_desc": ev.get("sample_desc", "")[:120],
            "skill_path": skill_rel,
            "attributed_section": attrib,          # None = 未在 skill 定位到（→ 建议新增）
            "suggestion_type": ("tighten_clause" if attrib is not None else "add_clause"),
            "suggestion": rewrite,
            "gate_level": "advisory",               # 永远 advisory · 不自动改 skill（北极星⑤）
            "confidence": 0.8 if attrib is not None else 0.6,
            "updated_at": _now(),
        }
        suggestions[:] = [s for s in suggestions if s.get("style_code") != code]
        suggestions.append(entry)
        produced.append(entry)

    save_experience(project_root, exp)
    if produced:
        print(f"[reflect-attrib] {len(produced)} 类持续风格失败已反射归因到 skill 段落 → advisory 改写建议：")
        for p in produced:
            sec = p["attributed_section"]
            loc = (f"段落「{sec['heading']}」" if sec else "未定位（建议新增 skill 约束）")
            print(f"  - [{p['style_code']}] {p['style_dimension']} 复发 {p['recurrence']} 次 -> "
                  f"{p['suggestion_type']}（{loc}）")
    return produced


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
    exp["_waiver_audit_ledger"] = {}      # 🔴 2026-06-27 C09：apply-moment 审计 ledger 同样全量重建
    tracker = exp["_recurrence_tracker"]

    # 2026-05-30 北极星复审：cluster 模式 audit 写 cluster_*_audit.json（ch_* 仅 chapter 模式）。原只
    # glob ch_* → cluster 流程（cluster-save-state step8 调本函数）扫不到任何报告，而上方已无条件清空
    # tracker → 每次 scan 抹平 --ingest 累积的复发/豁免计数（自学习闭环死 + 数据损坏）。两种都收。
    report_files = (sorted(audit_dir.glob("ch_*_audit.json")) +
                    sorted(audit_dir.glob("cluster_*_audit.json"))) if audit_dir.is_dir() else []
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
        # 2026-05-30 北极星复审：cluster 报告 chapter=9000 虚拟章 → 用 9000+cluster序号 区分多 cluster
        # （保持 int 防 _track_waivers 的 chapters.sort() 混 int/str 崩）；与 ingest_audit 同口径加 cluster:: key 前缀。
        _is_cluster = audit.get("_cluster_mode") is True
        _ckey = audit.get("_cluster_key", "")
        ch = audit.get("chapter", 0)
        if _is_cluster and _ckey:
            _d = "".join(c for c in str(_ckey) if c.isdigit())
            if _d:
                ch = 9000 + int(_d)
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
            if _is_cluster:
                key = f"cluster::{key}"
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
    # efficacy 闭环：全量重建后评估约束注入有效性（scanned_chapters = 注入后真正跑过的全章集·
    # 最准的分母）→ 无效约束自动停注（advisory）。_efficacy_tracker 基线**不随全量重建清空**
    # （基线锚最早注入点·重建只重算 recurrence_tracker）。
    ineffective = evaluate_efficacy(exp, observed_chapters=scanned_chapters)
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
    if ineffective:
        print(f"EFFICACY {len(ineffective)} 条约束注入后误报未降 -> 自动停注（advisory · 人工可复活）：")
        for x in ineffective:
            print(f"  - {x['pattern_id']}（基线复发率 {x['baseline_rate']} -> 注入后 {x['post_rate']}）")
    if (not escalated and not meta_problems and not calib and not ineffective
            and not time_prune["pruned"] and not time_prune["decayed"]):
        print("  暂无复发问题 / 反复豁免 / 无效约束 / 过期 pattern，写作经验库健康。")
    # reflect 归因闭环（默认 active · advisory）：持续风格失败 → 反射归因到 skill 段落 →
    # advisory 改写建议（非自动改 skill）。在 save_experience 之后跑（内部自带 load/save·
    # 读到刚重建的 _recurrence_tracker·北极星⑤不干涉模型）。
    skill_suggestions = reflect_attribution(project_root)
    return {"escalated": escalated, "meta_problems": meta_problems,
            "calibration": calib, "ineffective": ineffective, "time_prune": time_prune,
            "skill_rewrite": skill_suggestions}


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
        # 🔴 2026-06-27 P0-04 PID 桥：把 quantized_delta 校准建议喂 PID 控制器·MAPE-K Plan->Execute 闭环
        try:
            _bridge_pid_state(project_root, ap)
        except Exception as _e:
            print(f"[WARN] PID 桥失败(不阻断 learning): {_e}", file=sys.stderr)
        # exit 1 = 检测到复发问题已升级约束 / 反复豁免已产出校准建议 / 无效约束自动停注（都值得关注）
        sys.exit(1 if (result.get("escalated") or result.get("calibration")
                       or result.get("ineffective")) else 0)
    elif "--scan-recurring" in args:
        result = scan_recurring(project_root)
        # 🔴 2026-06-27 P0-04 PID 桥
        try:
            _bridge_pid_state(project_root, None)
        except Exception as _e:
            print(f"[WARN] PID 桥失败(不阻断 learning): {_e}", file=sys.stderr)
        sys.exit(1 if (result.get("escalated") or result.get("meta_problems")
                       or result.get("calibration") or result.get("ineffective")) else 0)
    elif "--reflect-attribution" in args:
        # 独立入口：单跑 reflect 归因（不重扫 audit·只读已攒的 _recurrence_tracker）。
        # 通常由 --scan-recurring 自动带跑·此入口供 cluster-save-state / 复盘单独触发。
        produced = reflect_attribution(project_root)
        # exit 1 = 产出了 skill 改写建议（值得复盘关注·与其他模式一致语义）
        sys.exit(1 if produced else 0)
    else:
        print("[FATAL] 需指定 --merge-reflection / --ingest / --scan-recurring / --reflect-attribution")
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
