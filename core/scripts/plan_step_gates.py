#!/usr/bin/env python3
"""plan_step_gates.py — 5 门「纯判定逻辑」共享库（C16 · 2026-06-27）

# 🔴 2026-06-28 移除exe/gen-model梳理方向
原 C16 设计有「两条消费路径」（PreToolUse hooks + 程序驱动 orchestrator._run_step_gates）。
orchestrator 随 exe/程序驱动方向整体删除，本模块回到**单一消费路径 = PreToolUse hooks**
（拦的是 Claude 主代理工具调用）；本体纯判定函数不变，audit_hub / scaffold_subsystems 仍复用。

本模块把 5 门的**纯判定逻辑**抽成可被 hook 共享的函数：
  · check_subsystems()      — 34 子系统 JSON 存在性（hard_gate）·🔴 C03 content_check=True
                              追加载荷非空验收（inert→hard·裸骨架→advisory）
  · check_agent_injection() — Agent prompt 契约/PLAN_ID 绑定/注入/篡改（hard_gate）
  · check_anti_skip()       — 输出完整性 vs expected_outputs（hard_gate）
  · check_chapter_edit()    — 章节正文剧本体/章末过渡（hard_gate）
  · check_research_ref()    — 决策前置 step 的调研缓存（hard_gate）

消费路径：5 个 pretooluse_*.py 薄 wrapper 读 stdin → 抽参 → 调对应 check →
  ok ? exit 0 : exit 2（hook 一律 fail→exit2；gate_level 字段标注硬/软语义供消费方区分）。

每个 check 返回统一形状（北极星⑥消重复·单一真相源）：
    {"ok": bool, "gate_level": "hard_gate"|"advisory", "msg": str,
     "waivable": bool, ...extra}

北极星护栏：hard_gate-class 只认 STRUCTURE§11 / audit_hub.HARD_GATE_CODES 既有
语义（子系统缺失 / anti-skip / agent 注入 / 决策前置调研缺失）；research 门
不再允许 auto_pilot/无网/跳过调研自动豁免。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GATE_HARD = "hard_gate"
GATE_ADVISORY = "advisory"


def _verdict(ok: bool, *, gate_level: str, msg: str = "",
             waivable: bool = False, **extra) -> dict:
    d = {"ok": bool(ok), "gate_level": gate_level, "msg": msg,
         "waivable": bool(waivable)}
    d.update(extra)
    return d


# ════════════════════════════════════════════════════════════════════
# 门 1：子系统门（hard_gate）——来源 pretooluse_subsystems_gate.py
# 34 个必建 JSON 单一真相源（基础 18 + 高级 16）
# ════════════════════════════════════════════════════════════════════
REQUIRED_DB_FILES = {
    "基础-人物世界": [
        "人物卡.json", "世界观.json", "关系.json", "地图.json", "道具.json",
    ],
    "基础-叙事": [
        "进度.json", "故事块摘要.json", "大势卡.json", "事件簇.json",
        "事件表.json", "时间线.json", "伏笔表.json",
    ],
    "基础-风格质控": [
        "作者风格.json", "场景规则.json", "写作经验.json", "用户偏好.json",
    ],
    "基础-世界演化（v20.1 R 系列）": [
        "世界状态.json", "涟漪规则.json",
    ],
    "高级-Hub/Clock/Storyteller/Stress（v21 R 系列）": [
        "枢纽场景.json", "时钟表.json", "叙事节拍器.json", "主角压力档.json",
    ],
    "高级-角色弧线 + NPC 动态（v21+）": [
        "character_arc_state.json", "角色行动表.json", "群像档.json",
    ],
    "高级-fluid 事件池 + 行动判定": [
        "事件池.json", "行动判定模板.json",
    ],
    "高级-v22 SE3 蒸馏专用（角色蒸馏 + 烙印）": [
        "角色池.json", "角色烙印.json",
    ],
    "高级-v23 长篇叙事工具": [
        "knowledge_graph.json", "subplot_threads.json", "beat_map.json",
        "四线脉络.json", "webnovel_bench_mapping.json",
    ],
}
ALL_REQUIRED: list[str] = []
for _cat, _files in REQUIRED_DB_FILES.items():
    ALL_REQUIRED.extend(_files)

def check_subsystems(db_dir, *, required=None, content_check=False) -> dict:
    """34 子系统 JSON 存在性（hard_gate · 不可豁免）。

    db_dir 不存在 / None → hard_gate。子系统目录是 outline 后续点火前置，不存在就不能
    让主链继续。

    🔴 2026-06-27 C03：content_check=True 时，**全齐后追加载荷白名单非空检查**——
    3 个「机器永不点火」载荷文件(涟漪规则/大势卡当前卷 ME 池/cluster_001 storyboard)空
    → inert → ok=False · hard_gate(不可豁免)；非载荷裸骨架 → advisory(不阻断·fluid 合法)。
    默认 False 保持纯存在性语义（既有调用零回归）；wiring 点见 _check_load_bearing
    docstring（outline plan-end / cluster-save-state validate 步可显式传 content_check=True）。
    """
    required = required or ALL_REQUIRED
    if db_dir is None:
        return _verdict(False, gate_level=GATE_HARD, waivable=False,
                        msg="db_dir 未知：无法验证 34 子系统")
    db_dir = Path(db_dir)
    if not db_dir.is_dir():
        return _verdict(False, gate_level=GATE_HARD, waivable=False,
                        msg=f"db_dir 不存在：{db_dir}")
    missing = [f for f in required if not (db_dir / f).exists()]
    if not missing:
        if content_check:
            cr = _check_load_bearing(db_dir)
            if cr is not None and not cr["ok"]:
                return cr  # 载荷空货架 inert → hard_gate
        return _verdict(True, gate_level=GATE_HARD)
    head = ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
    return _verdict(
        False, gate_level=GATE_HARD, waivable=False, missing=missing,
        msg=(f"缺 {len(missing)}/{len(required)} 个子系统 JSON: {head}\n"
             f"   修复: python core/scripts/scaffold_subsystems.py emit <项目>"))


def _check_load_bearing(db_dir: Path):
    """🔴 2026-06-27 C03：全齐后载荷白名单非空检查（单一真理源复用 scaffold_subsystems）。

    返回：
      · verdict(ok=False·hard_gate) —— 有 inert（载荷文件载荷路径空·机器永不点火·不可豁免）。
      · verdict(ok=True·gate_level=advisory) —— 无 inert（含仅非载荷裸骨架 bare·advisory·不阻断）。

    inert hard 子集恒 ≤3：涟漪规则空 / 当前卷 ME 池空 / cluster_001 storyboard 空——性质同
    MANIFEST_MISSING（文件契约/机器点火）。cluster_002+ ME/storyboard 空属 fluid·标记只查
    clusters[0] + 池非空·永不命中（回归锁）。其余 31 子系统裸骨架永远 advisory（fluid-allowed）。
    """
    try:
        import sys as _sys
        sd = str(Path(__file__).resolve().parent)
        if sd not in _sys.path:
            _sys.path.insert(0, sd)
        import scaffold_subsystems as _scaf
        canonical, skeletons = _scaf._load_skeletons()
    except Exception as exc:
        return _verdict(
            False, gate_level=GATE_HARD, waivable=False,
            msg=f"无法加载子系统骨架，不能执行 content_check: {type(exc).__name__}: {exc}")
    inert, bare = [], []
    db_dir = Path(db_dir)
    for name in canonical:
        path = db_dir / f"{name}.json"
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        skel = skeletons.get(name) or {}
        marker = skel.get(_scaf.LOAD_BEARING_KEY) if isinstance(skel, dict) else None
        if isinstance(marker, dict):
            code, ok = _scaf.eval_load_bearing(marker, obj)
            if not ok:
                inert.append((name, code))
        elif obj == skel:
            bare.append(name)
    if inert:
        head = "; ".join(f"{n}({c})" for n, c in inert)
        return _verdict(
            False, gate_level=GATE_HARD, waivable=False, inert=inert, bare=bare,
            msg=(f"载荷子系统空货架（inert·hard·机器永不点火·不可豁免）: {head}\n"
                 f"   涟漪规则空=引擎零触发 / 当前卷 ME 池空=大势无方向 / "
                 f"cluster_001 storyboard 空=首块未详化\n"
                 f"   修复: 让 outline 真正填充载荷内容（非仅建空骨架）"))
    return _verdict(True, gate_level=GATE_ADVISORY, bare=bare,
                    msg=(f"{len(bare)} 个非载荷裸骨架（advisory·fluid 合法）"
                         if bare else "载荷全非空·无裸骨架"))


# ════════════════════════════════════════════════════════════════════
# 门 2：禁跳步门（hard_gate）——来源 pretooluse_plan_step_anti_skip.py
# ════════════════════════════════════════════════════════════════════
def check_anti_skip(step_def: dict, *, skip_output_requested: bool = True) -> dict:
    """输出跳过逃避防御（hard_gate）。

    仅在「请求 --skip-output」时判定（hook 只钩同时含 step + --skip-output 的命令）。
    expected_outputs 非空 且 step.skip_output_allowed != true → block。
    """
    if not skip_output_requested:
        return _verdict(True, gate_level=GATE_HARD)
    expected = step_def.get("expected_outputs") or []
    skip_allowed = bool(step_def.get("skip_output_allowed", False))
    if expected and not skip_allowed:
        return _verdict(
            False, gate_level=GATE_HARD, waivable=False, expected=list(expected),
            msg=(f"step {step_def.get('n')} ({step_def.get('name', '?')}) 模板要求 "
                 f"expected_outputs（{len(expected)} 个）·--skip-output 被拒\n"
                 f"   A. 真跑 step 产出后用正常 step（不带 --skip-output）\n"
                 f"   B. 模板该 step 改 skip_output_allowed: true（仅确无输出时合法）"))
    return _verdict(True, gate_level=GATE_HARD)


# ════════════════════════════════════════════════════════════════════
# 门 3：章节正文门（hard_gate）——来源 pretooluse_chapter_edit_gate.py
# ════════════════════════════════════════════════════════════════════
SCREENPLAY_PATTERNS = [
    (r"（镜头[^）]{0,15}）", "剧本体镜头指令"),
    (r"（切镜[^）]{0,10}）", "剧本体切镜"),
    (r"（旁白[^）]{0,15}）", "剧本体旁白"),
    (r"（画外音[^）]{0,15}）", "剧本体画外音"),
    (r"（音效[^)]{0,15}）", "剧本体音效"),
    (r"（背景音[^）]{0,15}）", "剧本体背景音"),
    (r"（[^）]{0,15}的视角[^）]{0,5}）", "剧本体 POV 指令"),
    (r"（[^）]{0,10}离开[^）]{0,10}视角[^）]{0,5}）", "剧本体 POV 切换"),
    (r"\bCUT TO\b", "英文剧本切镜"),
    (r"\bFADE\s+(IN|OUT)\b", "英文剧本淡入淡出"),
    (r"\(V\.O\.\)", "英文画外音标记"),
    (r"\(O\.S\.\)", "英文 off-screen 标记"),
]
CHAPTER_END_PATTERNS = [
    (r"^\s*\*{1,3}\s*$", "章末单独 * 分隔符 · 大结局感"),
    (r"^\s*[·]{3,}\s*$", "章末单独 ··· 分隔符"),
    (r"^\s*—{3,}\s*$", "章末单独 ——— 分隔符"),
]


def scan_screenplay(content: str) -> list[tuple[str, str]]:
    hits = []
    for pattern, reason in SCREENPLAY_PATTERNS:
        for m in re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE):
            hits.append((m.group(0), reason))
    return hits


def scan_chapter_end(content: str) -> list[tuple[str, str]]:
    hits = []
    tail = "\n".join(content.split("\n")[-30:])
    for pattern, reason in CHAPTER_END_PATTERNS:
        for m in re.finditer(pattern, tail, re.MULTILINE):
            hits.append((m.group(0), reason))
    return hits


def check_chapter_edit(content: str) -> dict:
    """章节正文剧本体 + 章末物理分隔符（hard_gate）。
    """
    if not content:
        return _verdict(True, gate_level=GATE_HARD)
    hits = scan_screenplay(content) + scan_chapter_end(content)
    if not hits:
        return _verdict(True, gate_level=GATE_HARD)
    lines = [f"     - [{reason}] {matched.strip()[:60]!r}"
             for matched, reason in hits[:8]]
    if len(hits) > 8:
        lines.append(f"     ... 还有 {len(hits) - 8} 处")
    return _verdict(
        False, gate_level=GATE_HARD, waivable=False, hits=hits,
        msg=("章节正文检测到禁用 pattern（剧本体 / 章末物理分隔符）:\n"
             + "\n".join(lines)
             + "\n   剧本体 → 删，POV 不切；章末过渡 → 删，末句=心理悬念峰值"))


# ════════════════════════════════════════════════════════════════════
# 门 5：调研先行门（hard_gate）——来源 pretooluse_step_research.py
# 决策前置 step 必须有可核验 research artifact，禁止纯模型记忆降级。
# ════════════════════════════════════════════════════════════════════
def check_research_ref(step_def: dict, *, project_dir, auto_pilot: bool = False,
                       research_skipped: bool = False) -> dict:
    """决策前置 step 的 research_ref（.research_cache/*）存在性（hard_gate）。

    无 research_ref 字段（旧 plan / 非决策步）→ ok=True。
    auto_pilot / research_skipped 参数保留旧调用签名，但不再豁免。
    文件缺失 → ok=False · gate_level=hard_gate · waivable=False。
    """
    research_ref = step_def.get("research_ref")
    if not research_ref:
        return _verdict(True, gate_level=GATE_HARD)
    refs = [research_ref] if isinstance(research_ref, str) else research_ref
    if not isinstance(refs, list):
        return _verdict(True, gate_level=GATE_ADVISORY)
    base = Path(project_dir) if project_dir is not None else Path(".")
    missing = []
    for ref in refs:
        if not isinstance(ref, str):
            continue
        p = Path(ref)
        if not p.is_absolute():
            p = base / ref
        if not p.exists():
            missing.append(ref)
    if missing:
        return _verdict(
            False, gate_level=GATE_HARD, waivable=False, missing=missing,
            msg=("调研先行：research_ref 文件缺失（hard_gate）:\n"
                 + "\n".join(f"     - {m}" for m in missing)
                 + "\n   必须先 spawn novel-researcher 写 .research_cache/*，不得用纯模型记忆继续"))
    return _verdict(True, gate_level=GATE_HARD)


# ════════════════════════════════════════════════════════════════════
# 门 4：Agent 注入门（hard_gate）——来源 pretooluse_agent_gate.py
# 用于 Claude 主代理 spawn Agent 工具路径（拦 prompt 契约/注入/篡改）。
# ════════════════════════════════════════════════════════════════════
NOVEL_NAME_KEYWORDS = [
    "Writer", "writer", "Validator", "validator",
    "Voice", "voice", "novel-writer", "novel-validator", "novel-voice",
    "Archivist", "archivist", "novel-archivist", "归档", "档案",
    "写第", "审第", "修第", "写作", "正文", "写章节", "蒸馏",
]
MULTISTEP_KEYWORDS_DESC = [
    "cluster-save-state", "cluster save state", "cluster-write", "cluster write",
    "distill-style", "distill style", "outline",
    # 🔴 2026-06-27 C13：distill-character 纳入 plan 强制规划层 → 多步流水线 Agent 须含 PLAN_ID/STEP（L3 hook 规则 5）
    "distill-character", "distill character",
]
MULTISTEP_KEYWORDS_PROMPT = [
    "cluster_save_state", "cluster_write", "distill style",
    "全 7 阶段", "三章窗口", "12 步流水线", "7 步流水线",
]
NOVEL_SUBAGENT_TYPES = {
    "novel-writer", "novel-validator-checker", "novel-voice-checker",
    "novel-foreshadower", "novel-reflector", "novel-summarizer",
    "novel-outline-planner", "novel-chapter-splitter",
    "novel-reading-reflector", "novel-researcher", "novel-archivist",
}
_AUX_TYPES = {
    "novel-validator-checker", "novel-voice-checker", "novel-foreshadower",
    "novel-summarizer", "novel-reflector", "novel-outline-planner",
    "novel-reading-reflector", "novel-researcher", "novel-archivist",
}
INJECTION_PATTERNS = [
    (r"ignore\s+(?:the\s+)?(?:previous|all|above|prior)\s+instruction",
     "英文 ignore previous instructions"),
    (r"disregard\s+(?:the\s+)?(?:above|prior|earlier)", "英文 disregard above"),
    (r"forget\s+(?:everything|all|all\s+previous)", "英文 forget everything"),
    (r"new\s+system\s+(?:prompt|message)[:：]", "英文 new system prompt"),
    (r"override\s+(?:system|previous|all)\s+(?:prompt|instruction)",
     "英文 override system"),
    (r"忽略\s*(?:之前|上述|所有|以上|前面|前文)\s*(?:的)?\s*(?:指令|要求|内容|系统|提示)",
     "中文 忽略之前指令"),
    (r"无视\s*(?:前面|上文|之前|系统)", "中文 无视前文"),
    (r"忘记\s*(?:之前|所有|前面|系统).{0,12}(?:指令|提示|要求)", "中文 忘记之前指令"),
    (r"重新\s*定义\s*你\s*(?:是|为)", "中文 重新定义你是"),
    (r"新\s*(?:的)?\s*(?:系统|身份|指令)\s*[:：]", "中文 新系统/身份/指令"),
]


def is_multistep_agent(desc: str, prompt: str) -> bool:
    if any(kw in desc.lower() for kw in MULTISTEP_KEYWORDS_DESC):
        return True
    return any(kw in prompt for kw in MULTISTEP_KEYWORDS_PROMPT)


def _block(msg: str, warnings: list) -> dict:
    return _verdict(False, gate_level=GATE_HARD, waivable=False, msg=msg,
                    warnings=list(warnings))


def check_agent_injection(prompt: str, desc: str, subagent_type: str, *,
                          plan_state=None) -> dict:
    """Agent prompt 契约 / 长度 / frontmatter / 多步 PLAN_ID / 篡改 / ECAS
    RESEARCH_REF / 蒸馏复刻 / 注入模板（hard_gate）。

    与原 hook 同序判定（rule1→2→3→4warn→5→8→10→11→9warn），首个硬命中即 block；
    warn-only（rule4/9）收进 warnings（不影响 ok·wrapper 打印不退出）。

    plan_state：调用方（hook）传 plan_tracker.verify_plan(PLAN_ID) 结果。novel 主链
    必须为 "ok"；PLAN_ID 缺失 / not_found / tampered / unattested / error 均 fail closed。
    """
    prompt = prompt or ""
    desc = desc or ""
    subagent_type = subagent_type or ""
    warnings: list[str] = []

    if not prompt:
        return _verdict(True, gate_level=GATE_HARD, warnings=warnings)

    if subagent_type:
        is_novel_agent = subagent_type in NOVEL_SUBAGENT_TYPES
    else:
        is_novel_agent = any(kw in desc for kw in NOVEL_NAME_KEYWORDS)
    plan_id_match = re.search(r"PLAN_ID:\s*(\S+)", prompt)
    has_plan_id = bool(plan_id_match)

    # ---- 规则 1：novel Agent 契约字段（PLAN_ID 只做绑定，不豁免业务字段）----
    if is_novel_agent:
        if not has_plan_id:
            return _block("novel Agent 缺少 PLAN_ID（plan 绑定必需）", warnings)
        if plan_state != "ok":
            state = plan_state or "missing"
            return _block(f"novel Agent PLAN_ID 校验失败: {state}", warnings)
        has_project = "PROJECT:" in prompt
        has_cluster = "CLUSTER_ID:" in prompt
        has_mode = "MODE:" in prompt
        if subagent_type:
            is_writer = (subagent_type == "novel-writer")
            is_aux = (subagent_type in _AUX_TYPES)
        else:
            is_writer = ("writer" in desc.lower() or "写第" in desc)
            is_aux = any(kw in desc.lower() for kw in
                         ["validator", "voice", "修第", "审第", "摘要", "伏笔", "经验", "规划"])
        if is_writer:
            if not (has_project and has_cluster and has_mode):
                miss = [m for m, ok in (("PROJECT", has_project),
                                         ("CLUSTER_ID", has_cluster),
                                         ("MODE", has_mode)) if not ok]
                return _block(f"Writer Agent 缺少必填字段: {', '.join(miss)}", warnings)
        elif is_aux:
            if not (has_project and has_cluster and has_mode):
                miss = [m for m, ok in (("PROJECT", has_project),
                                        ("CLUSTER_ID", has_cluster),
                                         ("MODE", has_mode)) if not ok]
                return _block(f"Agent 缺少必填字段: {', '.join(miss)}", warnings)
        else:
            if not (has_project and has_cluster and has_mode):
                miss = [m for m, ok in (("PROJECT", has_project),
                                        ("CLUSTER_ID", has_cluster),
                                        ("MODE", has_mode)) if not ok]
                return _block(f"novel Agent 缺少必填字段: {', '.join(miss)}", warnings)

    # ---- 规则 2：prompt 长度门禁 ----
    prompt_len = len(prompt)
    if is_novel_agent and prompt_len < 50:
        return _block(f"写作 Agent prompt 过短（{prompt_len} < 50），疑似漏传", warnings)
    if prompt_len > 15000:
        return _block(f"Agent prompt 过长（{prompt_len} > 15000），疑似塞满旧模式", warnings)

    # ---- 规则 3：禁塞整个 skill frontmatter ----
    if re.search(r"^---\s*$", prompt, re.MULTILINE) and "description:" in prompt:
        return _block("Agent prompt 含 frontmatter，疑似塞入整个 skill 文件", warnings)

    # ---- 规则 4（warn-only）：大段规则文本 ----
    if is_novel_agent:
        rule_words = re.findall(r"(必须|禁止|严禁|不得|不允许)", prompt)
        if len(rule_words) > 8:
            warnings.append(f"写作 Agent prompt 含 {len(rule_words)} 条规则性指令（>8）·建议精简")

    # ---- 规则 5：多步流水线必须含 PLAN_ID/STEP ----
    if is_multistep_agent(desc, prompt):
        has_step = "STEP:" in prompt or "STEPS:" in prompt
        if not (has_plan_id or has_step):
            return _block("多步流水线 Agent 缺 PLAN_ID/STEP 字段", warnings)

    # ---- 规则 8：PLAN_ID 引用的 plan 防篡改（plan_state 由 caller 传）----
    if has_plan_id and plan_state == "tampered":
        return _block("PLAN_ID 指向的 plan 防篡改校验失败（tampered）·疑似伪造 step 状态", warnings)

    # ---- 规则 10：ECAS agent 必须含 RESEARCH_REF ----
    is_ecas_mode = (
        ("MODE: ecas" in prompt) or ("MODE: ecas_cluster_brief" in prompt)
        or ("CLUSTER_ID:" in prompt and is_novel_agent))
    if is_ecas_mode:
        has_research_ref = ("RESEARCH_REF:" in prompt
                            or "research_cache" in prompt.lower()
                            or ".research_cache/" in prompt)
        is_splitter = ("novel-chapter-splitter" in desc
                       or "splitter" in subagent_type.lower())
        is_checkpoint = ("ecas-checkpoint" in desc.lower()
                         or "checkpoint" in desc.lower())
        is_distill = ("蒸馏" in desc or "distill-style" in desc.lower()
                      or "distill style" in desc.lower()
                      or ("PLAN_ID:" in prompt and "distill-style" in prompt))
        if not (has_research_ref or is_splitter or is_checkpoint or is_distill):
            return _block("ECAS agent spawn 缺 RESEARCH_REF 字段", warnings)

    # ---- 规则 11：蒸馏复刻禁用 Agent（须走 gen-model）----
    desc_l = desc.lower()
    is_replicate = False
    if (("复刻测试" in desc) or ("v0 复刻" in desc) or ("v1 复刻" in desc)
            or ("v2 复刻" in desc) or ("phase-2 复刻" in desc)
            or ("phase-5 复刻" in desc)
            or ("replica" in desc_l and "test" in desc_l)):
        is_replicate = True
    elif "复刻" in desc and ("skill_v" in prompt or "复刻测试/v" in prompt
                            or "test_opening_replica" in prompt
                            or "test_battle_replica" in prompt
                            or "test_psychology_replica" in prompt):
        is_replicate = True
    if is_replicate:
        return _block("蒸馏复刻测试禁用 Agent 工具（须走 gen-model · distill_replicate.py）",
                      warnings)

    # ---- 规则 9（warn-only）：内容级注入模板检测 ----
    prompt_lower = prompt.lower()
    hits = []
    for pat, label in INJECTION_PATTERNS:
        try:
            if re.search(pat, prompt_lower, re.IGNORECASE):
                hits.append(label)
        except re.error:
            continue
    if len(hits) >= 2:
        warnings.append("prompt 含 %d 个 injection 模板特征（仅警告）: %s"
                        % (len(hits), "; ".join(hits[:5])))

    return _verdict(True, gate_level=GATE_HARD, warnings=warnings)
