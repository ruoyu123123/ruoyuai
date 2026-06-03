#!/usr/bin/env python3
"""audit_hub.py — 质检管家子系统（v18）

【定位】统一审核中枢。不重写校验器，是调度它们的"管家"。
用户要求："给小说系统增加审核子系统，能针对各种写作问题进行修复和记录学习"
        "全自动：发现就修，修完报告"

【它做什么】
  1. 统一调度 7 个校验器跑一章（各自命令行接口/退出码不一，本模块抹平）：
       validate_chapter.py        — 硬约束（字数/禁用词/伏笔/锁定事实/POV/对话工艺...）
       validate_style.py --strict — 风格合规 12 项
       narrative_scanner.py --all — 段/场景级叙事质感 8 检测器（含 P2-14 info_dump + P2-16 perspective_shift）
       plot_structure_scanner.py --all — 情节结构层 7 检测器（含 P1-4 kishotenketsu）
       hook_strength_scanner.py   — F 层 章末钩子强度正向评分（v19，归「读者体验」维度）
       golden_three_scanner.py    — F 层 黄金三章专项检测（v19，仅 ch1-3 激活）
       semantic_slop_scanner.py --all — B+ 文笔语义层 8 检测器（v19，正则漏掉的句级 AI 腔）
  2. 汇总所有问题，按 致命/错误/警告 x 维度（剧情/风格/结构/伏笔/对话/节奏）分类
  3. 全自动修复决策：
       - 确定性可修（标点/段落/拟声格式/禁用词）-> 直接调 style_repair_engine.py 原地修
       - 需 agent 判断（致命冲突/大段重写/对话密度严重不足）-> 进「待派 agent 清单」交主调度器
  4. 产出统一 JSON 报告 -> _数据库/.audit/ch_NNN_audit.json
  5. 调 learning_loop.py --ingest 把问题报告交给自学习闭环

【命令行接口】
  python audit_hub.py <项目路径> <章节号> [--auto-fix] [--json]
    无 --auto-fix：只审核出报告，不改任何文件
    有 --auto-fix：确定性问题原地修；需 agent 的进 pending_agent 清单
    --json：把报告 JSON 打到 stdout（默认只打人类可读摘要）

退出码：0=全通过 / 1=有问题且已自动修完（无 pending_agent）/ 2=有问题需派 agent
       3=致命错误（章节不存在/校验器全挂）

【v19 顾问制改造】
  检测工具从「门禁/法官」改为「顾问」。每条 issue 标 gate_level：
    hard_gate —— E 层一致性 + 文件契约类客观错误，AI 不可豁免（清单见 HARD_GATE_CODES）
    advisory  —— A 机械 / B 文笔 / C 叙事工艺 / D 情节结构 / F 读者体验，AI 有充分理由可豁免
  豁免协议：audit_hub 接收 --waivers <json>（_changes.json 或独立 waivers.json），
    读 self_eval.waivers: [{code, reason}]；对 advisory 项 code 匹配 → 转 waived（waived=True
    + waive_reason 记录理由）；hard_gate 项即便传了豁免也强制忽略豁免。
  全部 advisory 项都被合理豁免、无 hard_gate 残留 → verdict = "waived"。

【报告 JSON 结构】_数据库/.audit/ch_NNN_audit.json
  {
    schema_version, chapter, ts, verdict(pass/auto_fixed/needs_agent/fixable_pending/waived),
    summary: {fatal, error, warning, info, waived, total},
    issues:        [ {dimension, severity, gate_level, code, desc, source, fix_hint,
                      waived, waive_reason, meta_suspect?} ],  # 全部原始问题
    auto_fixed:    [ {dimension, code, desc, action} ],   # --auto-fix 已确定性修掉的
    pending_agent: [ {dimension, severity, gate_level, code, desc, suggested_agent, fix_brief} ],
    waived_issues: [ {dimension, gate_level, code, desc, waive_reason} ],  # 被 AI 合理豁免的
    scanner_status:[ {scanner, exit_code, ok} ]
  }
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import chapter_io as cio  # noqa: E402
# 2026-05-29 复审修复 [M12]：cluster 视野下 chapter_end_anchor 限本 cluster 章范围
import cluster_lookup  # noqa: E402

# ---- 维度归类：把各校验器的 code 映射到 6 大维度 ----
# 剧情 / 风格 / 结构 / 伏笔 / 对话 / 节奏
DIMENSION_BY_PREFIX = {
    # validate_chapter 的 code 前缀（前缀须能匹配 validate_chapter 真实 emit 的 code）
    "WC_": "节奏", "BANNED_": "风格", "CHANGES_": "剧情",
    "FORESHADOWING_": "伏笔", "SECRET_": "剧情", "CHARACTER_": "剧情",
    "UNKNOWN_CHARACTER": "剧情", "PROTAGONIST_": "剧情",
    "ITEM_": "剧情", "TIME_": "结构", "LOCKED_": "剧情",
    "FUTURE_KNOWLEDGE_": "剧情", "POV_": "结构", "HOOK_": "节奏",
    "DIALOGUE_": "对话", "LONG_MONOLOGUE": "对话",
    "TRY_FAIL": "结构", "PROPAGATION_": "剧情",
    "READER_EXP_": "读者体验",  # v19 F 层：hook_strength / golden_three
}
# narrative_scanner 检测器 -> 维度（P2-14：info_dump；P2-16：perspective_shift）
NARRATIVE_DIM = {
    "gmc": "结构", "mru": "结构", "orphan": "伏笔",
    "microten": "节奏", "repetition": "风格", "pov": "结构",
    "info_dump": "节奏",          # 信息堆砌段是 pacing 卡顿问题
    "perspective_shift": "结构",  # 人称切换是叙事结构问题
}
# plot_structure_scanner 检测器 -> 维度（P1-4：加 kishotenketsu 第 7 检测器）
PLOT_DIM = {
    "beat": "结构", "tryfail": "结构", "midpoint": "结构",
    "knowledge": "剧情", "arc": "剧情", "subplot": "剧情",
    "kishotenketsu": "结构",  # 起承转结（单人氛围章 / 东方叙事）
}
# semantic_slop_scanner 检测器 -> 维度（v19 B+ 文笔语义层 8 检测器）
# 全部归「风格」，tag_synonym_cycle 归「对话」。codes 形如 SEMANTIC_metaphor_explain，
# 不在 HARD_GATE_CODES 内 -> _gate_level_for() 自动判 advisory（与 STRUCTURE.md §11.3
# 「新增检测器默认 advisory」一致）。
SEMANTIC_DIM = {
    "metaphor_explain": "风格", "aphorism": "风格", "neg_parallel": "风格",
    "copula_avoid": "风格", "fake_range": "风格", "over_hedge": "风格",
    "forced_triple": "风格", "tag_synonym_cycle": "对话",
}
# v19 F 层「读者体验」检测器 -> code（这两个 scanner 输出扁平结构：单 top-level warning，
# 不像 narrative/plot 那样有 per-check 子块，故单独一类 code）
FLAT_FSCANNER_CODE = {
    "hook_strength": "READER_EXP_HOOK_STRENGTH",
    "golden_three": "READER_EXP_GOLDEN_THREE",
}
# validate_style 的 15 项 -> 维度（v23.12 新增 3 项段长）
STYLE_DIM = {
    "对话占比": "对话", "段落均长": "风格", "极短段占比": "风格",
    "单句成段率": "风格", "拟声格式": "风格", "极长句": "风格",
    "禁用词": "风格", "AI对话标签": "风格", "配额词": "风格",
    "方括号设定": "风格", "逗句比": "风格", "章节字数": "节奏",
    # v23.12（2026-05-21）网文段长硬约束 —— 来自 12 来源调研互证
    "单段超长": "风格", "单句独行占比": "风格", "长段计数": "风格",
}

# ---- 确定性可修的 code（style_repair_engine fix 模式覆盖范围）----
# style_repair_engine 能确定性修的：标点（逗句比）/ 禁用词替换 / 段落合并 / AI对话标签替换。
# ⚠ 拟声格式不在此列 —— style_repair 只能 normalize 已有拟声词、不能凭空生成；
#   「0 处拟声词需 ≥2 处」是生成型问题，必须派 novel-writer 补写（见 AGENT_ROUTING）。
# 注：即使 code 在本集合内，修完也必须重跑校验器复核（_apply_deterministic_fix），
#    没真正修成的不进 auto_fixed —— 杜绝「标 OK 实际没修」。
DETERMINISTIC_FIX_CODES = {
    "BANNED_WORD",          # 禁用词替换
    "STYLE_禁用词", "STYLE_逗句比",
    "STYLE_AI对话标签", "STYLE_极短段占比", "STYLE_单句成段率",
}
# ---- 需 agent 判断的：致命冲突 / 大段重写 / 生成型问题 ----
# key 必须是 validate_chapter / validate_style 真实 emit 的 code
# （已与 validate_chapter v18 源码逐个核对，杜绝幽灵 code）
AGENT_ROUTING = {
    "CHANGES_MISSING":          ("novel-writer", "正文缺 CHANGES，补 _changes.json"),
    # 2026-05-29 北极星 P3 [F3]：路由从 DEPRECATED stub(validator-repair/voice-keeper)
    # 改为 v2 拆分后的 checker（检查=Claude agent，主代理据 brief 调 gen_fixer 修复）。
    # 死线 2026-07-19 删 stub 后此处不再断 hard_gate 修复链。
    "LOCKED_FACT_CONFLICT":     ("novel-validator-checker", "正文与锁定事实冲突，重写冲突段"),
    "FORESHADOWING_NOT_PAID":   ("novel-foreshadower", "Tier1 伏笔未回收，补 payoff"),
    "FUTURE_KNOWLEDGE_LEAK":    ("novel-validator-checker", "角色知道了不该知道的信息，重写"),
    "POV_HEAD_HOPPING":         ("novel-voice-checker", "POV 越界，改回限定视角"),
    "SECRET_NOT_REVEALED":      ("novel-writer", "本章该揭的 secret 没揭，补揭示情节"),
    "CHARACTER_MISSING":        ("novel-writer", "大纲出场角色正文没出现，补戏份"),
    "UNKNOWN_CHARACTER_DETECTED": ("novel-writer", "正文出现未声明的角色，补声明或删除"),
    "STYLE_对话占比":           ("novel-writer", "对话密度严重不足，叙述改写为对话"),
    "STYLE_拟声格式":           ("novel-writer", "拟声词不足（生成型问题），在动作节点补写拟声词"),
    # v23.12 段长 hard_gate（不可豁免）+ advisory 路由
    "STYLE_单段超长":           ("novel-writer", "单段 > 120 CJK 字超移动阅读硬上限，找停顿切段（每章 ≤1 例外）"),
    "STYLE_长段计数":           ("novel-writer", "80-120 字长段过多，多段切碎或转对话独行"),
    "STYLE_单句独行占比":       ("novel-writer", "单句独行占比不足（吐槽爽文档目标 ≥40%），增加对话独行/单句脉冲"),
}

# ---- v19 顾问制：hard_gate 不可豁免清单（来自 V19_PLAN 第五节）----
# 这些是【客观错误】不是风格选择 —— E 层一致性 + 文件契约类。
# AI 即便在 --waivers 里传了豁免理由，audit_hub 也强制忽略豁免，仍按问题处理。
# 其余所有 code（validate_style 12 项 / narrative / plot scanner / WC_ / HOOK_ ...）
# 一律 advisory，AI 有充分理由可豁免。
HARD_GATE_CODES = {
    "LOCKED_FACT_CONFLICT",        # 正文与已锁定事实冲突 = 设定矛盾
    "FUTURE_KNOWLEDGE_LEAK",       # 角色知道不该知道的 = 逻辑错误
    "FORESHADOWING_NOT_PAID",      # Tier-1 到期伏笔未回收 = 承诺违约
    "SECRET_NOT_REVEALED",         # 秘密该揭未揭 = 剧情债
    "UNKNOWN_CHARACTER_DETECTED",  # 引用未声明实体 = 引用错误
    "CHANGES_MISSING",             # _changes.json 缺失 = 文件契约破损
    "MANIFEST_MISSING",            # manifest 缺失 = 文件契约破损
    "FILE_NOT_FOUND",              # 正文文件缺失 = 文件契约破损
    "ITEM_HOLDER_ABSENT",          # 道具持有者不在场 = 道具状态矛盾
    "ITEM_NOT_YET_INTRODUCED",     # 道具尚未引入就被用 = 道具状态矛盾
    "PROPAGATION_DEBT_CREATED",    # 跨集合数据未同步 = 传播债
    # v23.12（2026-05-21）：单段 > 120 CJK 字（超例外 1 段）= 移动阅读硬上限
    # AI 不可豁免；项目级可在 _数据库/style_scanner_overrides.json 调高阈值
    "STYLE_单段超长",
    # L2 防御（2026-05-28 · cluster_001 ch4 三次翻车 sediment）：
    # 章末出现剧本体过渡 / 文学过渡分隔符 / 听觉视觉淡出 / 收束句 = 移动阅读 cliffhanger 工艺破坏
    "CHAPTER_END_FORBIDDEN_SCREENPLAY",
    "CHAPTER_END_FORBIDDEN_TRANSITION",
    # v2 cluster 化（2026-05-28）：锁定事实跨场景引用冲突 = cluster 内设定矛盾
    # locked_fact_cross_scene_scanner emit；不可豁免（与 LOCKED_FACT_CONFLICT 同级）
    "LOCKED_FACT_CROSS_SCENE_CONFLICT",
}


def _gate_level_for(code: str, severity: str = "error") -> str:
    """v19：判定一条 issue 的权力等级。hard_gate 不可豁免，其余 advisory。
    v23.12：STYLE_单段超长 只在 fatal/error 时是 hard_gate（超例外 ≤1 才 FAIL）；
    WARN 状态（80-120 警告区或例外内）降 advisory 可豁免。"""
    # 2026-06-02 修：info severity = 自动生成的旁注（本模块 docstring 定义「下游可忽略」），永不 hard_gate。
    # 否则像 UNKNOWN_CHARACTER_DETECTED（validate_chapter 恒以 info 发的低置信 NER·历史 250+ 误报）
    # 会用 NER 垃圾碎片（「一起的味」「人的耳朵」）硬毙整 cluster。真要 block 的项应以 error/fatal 发。
    # 北极星⑤：检测是顾问非法官·低置信信号 advisory 可豁免。
    if severity == "info":
        return "advisory"
    if code == "STYLE_单段超长":
        return "hard_gate" if severity in ("fatal", "error") else "advisory"
    return "hard_gate" if code in HARD_GATE_CODES else "advisory"


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dimension_for_code(code: str) -> str:
    for prefix, dim in DIMENSION_BY_PREFIX.items():
        if code.startswith(prefix):
            return dim
    return "剧情"


def _run(cmd: list, env_extra: dict = None) -> tuple:
    """跑子进程，返回 (exit_code, stdout, stderr)。子进程隔离 —— 任一校验器挂了不连累其他。

    env_extra: v2 cluster 化支持。传 {"CLUSTER_MODE": "1"} 让子进程 scanner 感知 cluster 视野。
    """
    try:
        env = None
        if env_extra:
            import os as _os
            env = {**_os.environ, **env_extra}
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180, env=env)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 99, "", "[TIMEOUT] 校验器超时 180s"
    except Exception as e:
        return 98, "", f"[EXEC-ERROR] {e}"


def load_scanner_registry() -> dict:
    """v2 cluster 化：读 scanner_registry.json 决定跑哪些 scanner。
    缺失则 fallback 到硬编码 7 scanner（向后兼容）。"""
    reg_path = _SCRIPT_DIR / "scanner_registry.json"
    if not reg_path.exists():
        return {}
    try:
        return json.loads(reg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ============ 各校验器结果归一化 ============

def _parse_validate_chapter(stdout: str, exit_code: int) -> list:
    """validate_chapter --json 输出结构化 JSON，直接解析（v18 #12 加固：不再正则抽文本）。
    code 直接来自 validate_chapter 源头 emit —— 杜绝幽灵 code，validate_chapter 即便
    改报告格式也不会让 audit_hub 静默失效。
    schema: {summary{fatal,error,warning,info,total}, errors[{code,severity,msg,fix_hint}]}"""
    issues = []
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return issues  # --json 解析失败 → 返回空，scanner_status 的 ok 判定会暴露异常
    for e in data.get("errors", []):
        code = e.get("code", "")
        severity = e.get("severity", "warning")
        issues.append({
            "dimension": _dimension_for_code(code),
            "severity": severity,
            "gate_level": _gate_level_for(code, severity),
            "code": code,
            "desc": e.get("msg", ""),
            "source": "validate_chapter",
            "fix_hint": e.get("fix_hint", ""),
            "waived": False,
            "waive_reason": "",
        })
    return issues


def _parse_validate_style(stdout: str) -> list:
    """validate_style 输出 '[FAIL] 项目名: detail (target)'。只收 FAIL/WARN。"""
    issues = []
    for ln in stdout.splitlines():
        s = ln.strip()
        if s.startswith("[FAIL]") or s.startswith("[WARN]"):
            status = "FAIL" if s.startswith("[FAIL]") else "WARN"
            body = s[s.find("]") + 1:].strip()
            name = body.split(":", 1)[0].strip()
            detail = body.split(":", 1)[1].strip() if ":" in body else body
            dim = STYLE_DIM.get(name, "风格")
            severity = "error" if status == "FAIL" else "warning"
            code = f"STYLE_{name}"
            issues.append({
                "dimension": dim, "severity": severity,
                "gate_level": _gate_level_for(code, severity),
                "code": code, "desc": detail,
                "source": "validate_style", "fix_hint": "",
                "waived": False, "waive_reason": "",
            })
    return issues


def _parse_chapter_end_anchor(stdout: str, exit_code: int) -> list:
    """chapter_end_anchor_scan.py --json 输出解析。"""
    issues = []
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start < 0 or end < 0:
            return issues
        report = json.loads(stdout[start:end + 1])
    except json.JSONDecodeError:
        return issues
    for r in report.get("results", []):
        ch_path = r.get("chapter_path", "")
        m = re.search(r"第(\d+)章", ch_path)
        ch_label = f"ch{m.group(1)}" if m else "?"
        for iss in r.get("issues", []):
            issues.append({
                "dimension": "章末工艺",
                "severity": iss.get("severity", "warning"),
                "gate_level": iss.get("gate_level", "advisory"),
                "code": iss.get("code", "CHAPTER_END_UNKNOWN"),
                "desc": f"[{ch_label}] {iss.get('reason', '')}"
                        + (f" · matched={iss.get('matched','')[:50]}" if iss.get('matched') else ""),
                "source": "chapter_end_anchor_scan",
                "fix_hint": iss.get("fix_hint", ""),
                "waived": False,
                "waive_reason": "",
            })
    return issues


def _parse_scanner_json(stdout: str, scanner: str, dim_map: dict) -> list:
    """narrative/plot scanner 输出 JSON 报告，每个检测器有 warning 字段。"""
    issues = []
    try:
        # 报告 JSON 是 stdout 第一个 { 到最后一个 } —— scanner 可能在 JSON 后还打了别的
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start < 0 or end < 0:
            return issues
        report = json.loads(stdout[start:end + 1])
    except json.JSONDecodeError:
        return issues
    for check, dim in dim_map.items():
        block = report.get(check)
        if not isinstance(block, dict):
            continue
        warn = block.get("warning")
        if not warn:
            continue
        # 被 scanner 自己豁免的（suppressed_reason）-> 降级 info，不进 issues 主流
        suppressed = bool(block.get("suppressed_reason"))
        sev = block.get("severity")
        severity = sev if sev in ("fatal", "error", "warning") else "warning"
        if suppressed:
            severity = "info"
        code = f"{scanner.upper()}_{check}"
        # narrative/plot scanner 的检测项全是 advisory（C 叙事工艺 / D 情节结构 / F 读者体验）
        issues.append({
            "dimension": dim, "severity": severity,
            "gate_level": _gate_level_for(code, severity),
            "code": code, "desc": str(warn),
            "source": scanner, "fix_hint": block.get("fix_hint", ""),
            "waived": False, "waive_reason": "",
        })
    return issues


def _parse_flat_fscanner(stdout: str, check_key: str) -> list:
    """v19 F 层检测器（hook_strength / golden_three）输出归一化。

    与 narrative/plot 不同：这两个 scanner 输出【扁平结构】——
    单个 top-level `warning`（可能为 null）、`gate_level`、`severity`、
    可选 `suppressed_reason`、golden_three 还有 `status`（n/a / active）。
    golden_three 对 ch≥4 返回 status=n/a + warning=null —— 不产生 issue。
    """
    issues = []
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start < 0 or end < 0:
            return issues
        report = json.loads(stdout[start:end + 1])
    except json.JSONDecodeError:
        return issues
    # n/a（golden_three 非 ch1-3）或无 warning → 无 issue
    if report.get("status") == "n/a":
        return issues
    warn = report.get("warning")
    if not warn:
        return issues
    code = FLAT_FSCANNER_CODE.get(check_key, f"READER_EXP_{check_key.upper()}")
    # F 层检测器自带 suppressed_reason（过渡章豁免）→ 降级 info
    suppressed = bool(report.get("suppressed_reason"))
    sev = report.get("severity")
    severity = sev if sev in ("fatal", "error", "warning") else "warning"
    if suppressed:
        severity = "info"
    issues.append({
        "dimension": "读者体验", "severity": severity,
        # F 层全部 advisory，可豁免（与 scanner 自报 gate_level 一致；以 HARD_GATE_CODES 为准）
        "gate_level": _gate_level_for(code, severity),
        "code": code, "desc": str(warn),
        "source": check_key, "fix_hint": "",
        "waived": False, "waive_reason": "",
    })
    return issues


# ============ v2 cluster-only scanner 结果归一化 ============
# 这 4 个 scanner 输出结构各异：
#   · foreshadowing_handoff / pov_consistency → 顶层 issues[]，每条带 code/gate_level/severity/msg
#   · locked_fact_cross_scene → 扁平顶层 code/gate_level/severity/warning（单 issue）
#   · cross_scene_voice_drift → drift_issues[]（无 per-item code），顶层 warning/severity
#   · narrative_short_sentence / repeat_noun_density → violations[]（无 per-item code），
#     顶层 gate_level=advisory、verdict、severity 为 major/minor（映射 error/warning）
# 之前这 6 个 parse_fn 全是 `lambda out,code: []`，scanner 结果（含 hard_gate）被静默丢弃。

def _load_scanner_json(stdout: str) -> dict | None:
    """从 scanner stdout 抽第一个 { 到最后一个 } 解析 JSON。失败返回 None。"""
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start < 0 or end < 0:
            return None
        return json.loads(stdout[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _norm_severity(sev: str) -> str:
    """scanner 的 major/minor 等非标准 severity 归一化到 audit 标准（fatal/error/warning/info）。"""
    m = {"major": "error", "minor": "warning"}
    if sev in ("fatal", "error", "warning", "info"):
        return sev
    return m.get(sev, "warning")


def _parse_issues_list_scanner(stdout: str, source: str, dimension: str,
                               default_severity: str = "warning") -> list:
    """通用解析：scanner 顶层 issues[]，每条带 code/gate_level/severity/msg/count/items。
    用于 foreshadowing_handoff / pov_consistency。
    gate_level 以 HARD_GATE_CODES 为权威（scanner 自报仅参考），但 scanner 显式标 hard_gate
    的也尊重（如 foreshadowing tier-1 → hard_gate）。"""
    issues = []
    report = _load_scanner_json(stdout)
    if not report:
        return issues
    for it in report.get("issues", []) or []:
        if not isinstance(it, dict):
            continue
        code = it.get("code", "")
        if not code:
            continue
        severity = _norm_severity(it.get("severity", default_severity))
        # gate_level：HARD_GATE_CODES 命中 → hard_gate。2026-05-30 北极星复审：scanner 自报 hard_gate
        # 仅当其 code 在 HARD_GATE_CODES（权威单一来源）时才尊重——否则任意 scanner 可在清单外自立
        # hard_gate（foreshadowing_handoff 曾用 FORESHADOWING_NOT_PLANTED 越权卡死写作），违反北极星
        # 「hard_gate 清单单一来源，不得各自另立」。
        gl = _gate_level_for(code, severity)
        if gl != "hard_gate" and it.get("gate_level") == "hard_gate" and code in HARD_GATE_CODES:
            gl = "hard_gate"
        desc = it.get("msg", "") or it.get("desc", "")
        if it.get("count") is not None:
            desc = f"{desc}（命中 {it.get('count')} 处）"
        issues.append({
            "dimension": dimension, "severity": severity,
            "gate_level": gl, "code": code, "desc": str(desc),
            "source": source, "fix_hint": "",
            "waived": False, "waive_reason": "",
        })
    return issues


def _parse_locked_fact_cross_scene(stdout: str) -> list:
    """locked_fact_cross_scene_scanner：扁平顶层结构 —— 有冲突时 code/gate_level/severity/warning
    全在顶层，conflicts[] 给细节。无冲突 code=null → 不产 issue。
    LOCKED_FACT_CROSS_SCENE_CONFLICT 是 hard_gate（不可豁免）。"""
    issues = []
    report = _load_scanner_json(stdout)
    if not report:
        return issues
    code = report.get("code")
    if not code:
        return issues  # 无冲突
    severity = _norm_severity(report.get("severity", "error"))
    desc = report.get("warning") or ""
    conflicts = report.get("conflicts", []) or []
    if conflicts:
        first = conflicts[0]
        desc = f"{desc} · {first.get('character','?')}: {first.get('fact','')[:40]} ↔ {first.get('conflict_value','')}"
    issues.append({
        "dimension": "剧情", "severity": severity,
        "gate_level": _gate_level_for(code, severity),
        "code": code, "desc": str(desc),
        "source": "locked_fact_cross_scene_scanner", "fix_hint": "",
        "waived": False, "waive_reason": "",
    })
    return issues


def _parse_cross_scene_voice_drift(stdout: str) -> list:
    """cross_scene_voice_drift_scanner：无 per-item code，drift_issues[] + 顶层 warning/severity。
    有 warning 时合成单条 VOICE_DRIFT_CROSS_SCENE（advisory）。"""
    issues = []
    report = _load_scanner_json(stdout)
    if not report:
        return issues
    warn = report.get("warning")
    if not warn:
        return issues
    severity = _norm_severity(report.get("severity", "warning"))
    code = "VOICE_DRIFT_CROSS_SCENE"
    drift = report.get("drift_issues", []) or []
    desc = str(warn)
    if drift:
        d0 = drift[0]
        desc += f" · 例：{d0.get('character','?')} scene{d0.get('scene_idx','?')} 偏差 {d0.get('deviation_pct','?')}%"
    issues.append({
        "dimension": "风格", "severity": severity,
        "gate_level": _gate_level_for(code, severity),
        "code": code, "desc": desc,
        "source": "cross_scene_voice_drift_scanner", "fix_hint": "",
        "waived": False, "waive_reason": "",
    })
    return issues


def _parse_scene_seam(stdout: str) -> list:
    """scene_seam_scanner：顶层 issues[]，每条 code=SEAM_DISTRIBUTION_DRIFT + gate_level=advisory
    + detail（非 msg/desc）。shadow/off 模式 issues 永远空 → 不产 issue（零回归）。
    SEAM_DISTRIBUTION_DRIFT **不在 HARD_GATE_CODES** → _gate_level_for 必判 advisory（北极星 5：
    顾问非法官 · 衔接手法偏好走 advisory 可豁免 · 绝不进 hard_gate）。"""
    issues = []
    report = _load_scanner_json(stdout)
    if not report:
        return issues
    for it in report.get("issues", []) or []:
        if not isinstance(it, dict):
            continue
        code = it.get("code", "")
        if not code:
            continue
        severity = _norm_severity(it.get("severity", report.get("severity", "warning")))
        desc = it.get("detail", "") or it.get("msg", "") or it.get("desc", "")
        issues.append({
            "dimension": "风格", "severity": severity,
            "gate_level": _gate_level_for(code, severity),  # 永远 advisory（不在 HARD_GATE_CODES）
            "code": code, "desc": str(desc),
            "source": "scene_seam_scanner", "fix_hint": "",
            "waived": False, "waive_reason": "",
        })
    return issues


def _parse_violations_scanner(stdout: str, source: str, code: str, dimension: str) -> list:
    """narrative_short_sentence / repeat_noun_density：violations[]（无 per-item code），
    顶层 gate_level=advisory。每条 violation 的 severity 为 major/minor（映射 error/warning）。
    PASS（无 violation）→ 不产 issue。合成 1 条聚合 issue（severity 取最高）。"""
    issues = []
    report = _load_scanner_json(stdout)
    if not report:
        return issues
    violations = report.get("violations", []) or []
    if not violations:
        return issues
    # severity 取最高：任一 major → error，否则 warning
    has_major = any(v.get("severity") == "major" for v in violations)
    severity = "error" if has_major else "warning"
    top_gl = report.get("gate_level", "advisory")
    gl = _gate_level_for(code, severity)
    if gl != "hard_gate" and top_gl == "hard_gate":
        gl = "hard_gate"
    desc = f"{report.get('scanner', source)}: {len(violations)} 处违规（verdict={report.get('verdict','?')}）"
    issues.append({
        "dimension": dimension, "severity": severity,
        "gate_level": gl, "code": code, "desc": desc,
        "source": source, "fix_hint": "",
        "waived": False, "waive_reason": "",
    })
    return issues


# ============ v19.2 工具校准建议：自动豁免 ============

def _load_calibration_suggestions(project_root: Path) -> list[dict]:
    """读 写作经验.json 的 tool_calibration_suggestions，让 audit_hub 自动豁免反复出现的 code。

    返回 [{code, scene_type_hint, suggestion_type}, ...]
    learning_loop 产出该字段——同一 code 被豁免 ≥4 次后升级为 calibration suggestion。
    audit_hub 启动时读它，对命中 code + scene_type 的 issue 自动 waived（无需 writer 再次手动豁免）。
    """
    exp_path = project_root / "_数据库" / "写作经验.json"
    if not exp_path.exists():
        return []
    try:
        data = json.loads(exp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return []
    return data.get("tool_calibration_suggestions", []) or []


def _check_auto_waiver(issue_code: str, scene_types_set: set[str], suggestions: list[dict]) -> dict | None:
    """检查 issue_code + 当前 scene_type 是否命中 calibration suggestion。
    命中 → 返回该 suggestion（标记为自动豁免）；未命中返回 None。
    """
    for s in suggestions:
        if s.get("code") != issue_code:
            continue
        stype = s.get("suggestion_type", "")
        hint = s.get("scene_type_hint", "")
        if stype == "add_scene_adaptation" and hint and hint in scene_types_set:
            return s
        elif stype == "adjust_threshold":
            return s  # 阈值调整类不依赖 scene_type，直接命中
    return None


# severity 阶梯（严 → 宽）：fatal > error > warning > info。
# L2-0 soft-cap 只下降一档，绝不把 info 再往下消失。
_SEVERITY_DOWNGRADE = {"fatal": "error", "error": "warning", "warning": "info", "info": "info"}


def _apply_auto_calibration_softcap(issue: dict, match: dict) -> bool:
    """L2-0 止血（2026-05-30）：calibration suggestion 命中后【降一档严格度】而非整条豁免。

    根因复盘：旧逻辑命中 ≥3 次后把整条 issue waived=True —— 等于「完全关掉该检测」，
    无穷增益二元跳变、关了回不来、无衰减，是矫枉过正反向震荡源。

    新逻辑（呼应 learning_loop 建议原文「降级默认 severity」）：
      - 保留检测【存在性】：issue 仍留在 all_issues / 报告里（不 waived、不删除），
        只是 severity 沿阶梯下降一档（fatal→error→warning→info），不再反复刷屏为高优阻断项。
      - info 已是最低档：到 info 后只打标记、不再下降（永不彻底消失）。
      - 北极星⑤顾问制边界：本函数永不碰 hard_gate（调用方已过滤），永不新增/改判 code、
        永不写 HARD_GATE_CODES，纯 advisory 内部降档。

    原地修改 issue。返回 True 表示真发生了降档（用于计数 / 日志）。
    """
    old_sev = issue.get("severity", "info")
    new_sev = _SEVERITY_DOWNGRADE.get(old_sev, "info")
    # 已经在最低档 info：仅盖标记保留可审计性，不重复降、不静默
    issue["severity"] = new_sev
    issue["_auto_calibration_softcap"] = {
        "from_severity": old_sev,
        "to_severity": new_sev,
        "suggestion": str(match.get("suggestion", ""))[:120],
        "waived_count": match.get("waived_count"),
    }
    return new_sev != old_sev


# ============ v19 豁免协议：读取 + 应用 ============

def _load_waivers(waivers_path: str) -> list:
    """从 --waivers 指向的 json 读豁免清单，返回 [{code, reason}, ...]。
    兼容两种文件：
      1) _changes.json —— 读 self_eval.waivers
      2) 独立 waivers.json —— 顶层就是 {"waivers": [...]} 或裸 list
    理由 < 100 字才算有效豁免（防「不想改」式空理由）；code 缺失的条目丢弃。
    文件不存在 / 解析失败 → 返回空 list（不中断，等同没传豁免）。"""
    if not waivers_path:
        return []
    p = Path(waivers_path)
    if not p.is_file():
        print(f"  [waivers] 文件不存在，忽略: {waivers_path}", file=sys.stderr)
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  [waivers] 解析失败，忽略: {e}", file=sys.stderr)
        return []
    raw = []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        # _changes.json 布局：self_eval.waivers
        se = data.get("self_eval")
        if isinstance(se, dict) and isinstance(se.get("waivers"), list):
            raw = se["waivers"]
        # 独立 waivers.json 布局：顶层 waivers
        elif isinstance(data.get("waivers"), list):
            raw = data["waivers"]
    out = []
    for w in raw:
        if not isinstance(w, dict):
            continue
        code = str(w.get("code", "")).strip()
        reason = str(w.get("reason", "")).strip()
        if not code:
            continue
        if not reason:
            print(f"  [waivers] {code} 无理由，豁免无效（豁免必带具体理由）",
                  file=sys.stderr)
            continue
        # v2 cluster 化方案 Phase A hot-fix（2026-05-28）：
        # 阈值 100→300。cluster mode 涉及多场景多角色多伏笔，理由 150-280 字常见。
        # stderr 缩短打印（前 80 字+省略），完整理由仍写入 audit 报告 JSON。
        if len(reason) >= 300:
            print(f"  [waivers] {code} 理由超 300 字，已截断: {reason[:80]}…", file=sys.stderr)
            reason = reason[:300]
        out.append({"code": code, "reason": reason})
    return out


def _apply_waivers(all_issues: list, waivers: list) -> list:
    """v19 顾问制核心：对 issue 应用 AI 豁免。
      - advisory 项 code 命中豁免清单 → waived=True + waive_reason 记录理由
      - hard_gate 项即便命中豁免清单也【强制忽略豁免】（不可豁免，仍按问题处理）
    原地修改 all_issues，返回被成功豁免的 issue 引用列表（供报告 waived_issues 段）。"""
    if not waivers:
        return []
    by_code = {w["code"]: w["reason"] for w in waivers}
    waived = []
    for issue in all_issues:
        code = issue.get("code", "")
        if code not in by_code:
            continue
        if issue.get("gate_level") == "hard_gate":
            # 不可豁免：即便传了理由也强制忽略
            print(f"  [waivers] {code} 是 hard_gate，不可豁免，豁免理由已忽略",
                  file=sys.stderr)
            continue
        issue["waived"] = True
        issue["waive_reason"] = by_code[code]
        waived.append(issue)
    return waived


# ============ 修复决策 ============

def _check_character_arc_drift(project_root: Path, ch: int) -> list:
    """v22 方案 3 · MARCUS 范式角色情感弧偏差检测（advisory）。

    读项目对应风格库的 character_arcs/<主角>_emotion_arc.json，对照本章 changes.json
    的 self_eval 情感强度，偏差 > 0.3 → advisory issue。
    本检测器不阻塞，所有问题都是 advisory（可豁免）。
    """
    issues = []
    # 1. 找项目风格库的 work 名
    style_path = project_root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return issues
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return issues
    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return issues

    # 2. 推算 character_arcs 路径
    arc_dir = None
    for parent in [project_root, *project_root.parents]:
        candidate = parent / "workspace" / "styles" / work / "character_arcs"
        if candidate.exists():
            arc_dir = candidate
            break
    if arc_dir is None:
        candidate = Path.cwd() / "workspace" / "styles" / work / "character_arcs"
        if candidate.exists():
            arc_dir = candidate
    if arc_dir is None:
        return issues

    # 3. 找主角名（从人物卡.json）
    protagonist = None
    chars_path = project_root / "_数据库" / "人物卡.json"
    if chars_path.exists():
        try:
            cd = json.loads(chars_path.read_text(encoding="utf-8"))
            for name, info in cd.items() if isinstance(cd, dict) else []:
                if isinstance(info, dict) and (info.get("role") == "protagonist" or info.get("is_protagonist")):
                    protagonist = name
                    break
            if not protagonist and isinstance(cd, dict):
                protagonist = next(iter(cd.keys()), None)
        except (json.JSONDecodeError, ValueError):
            pass
    if not protagonist:
        return issues

    # 4. 读主角 arc
    import re as _re
    safe_name = _re.sub(r"[\\/:*?\"<>|]", "_", protagonist)
    arc_file = arc_dir / f"{safe_name}_emotion_arc.json"
    if not arc_file.exists():
        return issues
    try:
        arc_data = json.loads(arc_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return issues

    chapters_with_data = arc_data.get("chapters_with_data") or []
    actor_curve = arc_data.get("emotion_actor_curve_smoothed") or []
    experiencer_curve = arc_data.get("emotion_experiencer_curve_smoothed") or []
    if ch not in chapters_with_data:
        return issues
    idx = chapters_with_data.index(ch)
    if idx >= len(actor_curve) or idx >= len(experiencer_curve):
        return issues
    expected_actor = actor_curve[idx]
    expected_experiencer = experiencer_curve[idx]

    # 5. 读本章 changes.json self_eval 估算 actual
    ch_dir = project_root / "章节" / f"第{ch:03d}章"
    changes_file = ch_dir / f"第{ch:03d}章_changes.json"
    if not changes_file.exists():
        return issues
    try:
        changes = json.loads(changes_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return issues
    se = changes.get("self_eval") or {}
    actual_intensity = se.get("emotion_intensity") or se.get("情感强度") or se.get("intensity") or 0.5
    if not isinstance(actual_intensity, (int, float)):
        actual_intensity = 0.5

    # 6. 比对（advisory）
    drift_actor = abs(actual_intensity - expected_actor)
    drift_experiencer = abs(actual_intensity - expected_experiencer)
    if drift_actor > 0.3 or drift_experiencer > 0.3:
        issues.append({
            "code": f"CHARACTER_ARC_DRIFT_{safe_name.upper()}",
            "severity": "warning",
            "gate_level": "advisory",
            "desc": (f"主角 {protagonist} 本章情感强度 {actual_intensity:.2f} 偏离原作 arc 期望 "
                     f"(actor={expected_actor:.2f}, experiencer={expected_experiencer:.2f}, "
                     f"drift_actor={drift_actor:.2f})"),
            "source": "character_arc_drift",
            "fix_hint": (f"调整本章 {protagonist} 的情感曲线，让强度接近 "
                         f"actor={expected_actor:.2f} / experiencer={expected_experiencer:.2f}。"
                         f"参考 character_arcs/{safe_name}_emotion_arc.json 的 stage_transitions 和高频情绪。"),
            "_arc_ref": str(arc_file),
            "_chapter_index_in_arc": idx,
        })
    return issues


def _is_deterministic(issue: dict) -> bool:
    """该问题是否可由 style_repair_engine 确定性修复。"""
    return issue["code"] in DETERMINISTIC_FIX_CODES


def _agent_for(issue: dict) -> tuple:
    """该问题需派哪个 agent + fix_brief。返回 (agent, brief) 或 (None, None)。"""
    if issue["code"] in AGENT_ROUTING:
        return AGENT_ROUTING[issue["code"]]
    # 致命/错误级但没显式路由 -> 兜底派 validator-checker（v2 拆分 · 2026-05-29 P3 F3 改名）
    if issue["severity"] in ("fatal", "error"):
        return ("novel-validator-checker", issue.get("fix_hint") or issue["desc"])
    return (None, None)


def _rescan_codes(project_root: Path, ch: int, body_file: Path) -> set:
    """修复后重跑校验器，返回当前仍存在的 code 集合（用于复核「真的修成了没」）。
    确定性问题来自 validate_style（STYLE_*）和 validate_chapter（BANNED_WORD），都重跑。"""
    present = set()
    # validate_style --strict
    vs = _SCRIPT_DIR / "validate_style.py"
    _, out, _ = _run([sys.executable, str(vs), str(body_file), "--strict"])
    for issue in _parse_validate_style(out):
        present.add(issue["code"])
    # validate_chapter（BANNED_WORD 来自这里）—— v18 #12：--json 结构化输出
    vc = _SCRIPT_DIR / "validate_chapter.py"
    _, out, _ = _run([sys.executable, str(vc), str(project_root), str(ch), "--json"])
    for issue in _parse_validate_chapter(out, 0):
        present.add(issue["code"])
    return present


def _apply_deterministic_fix(project_root: Path, ch: int, det_issues: list) -> tuple:
    """调 style_repair_engine fix 原地修复，修完【重跑校验器复核】。
    返回 (auto_fixed, still_unfixed)：
      auto_fixed    —— 复核确认真的修掉了的 issue
      still_unfixed —— 跑了修复但 issue 仍在的（转待派 agent，杜绝「标 OK 实际没修」）
    style_repair_engine fix 默认写 _fixed.txt，用 --output 指回原 body_path 实现原地修。"""
    if not det_issues:
        return [], []
    body_file = cio.find_body_file(project_root, ch)
    if not body_file:
        return [], list(det_issues)
    repair = _SCRIPT_DIR / "style_repair_engine.py"
    code, out, err = _run([sys.executable, str(repair), str(body_file),
                           "--mode", "fix", "--output", str(body_file)])
    if code not in (0, 1):
        # 修复脚本自身挂了 —— 一个都没修成，全转待处理
        return [], list(det_issues)

    # ★关键：重跑校验器复核 —— issue 的 code 还在 = 没修成，不准进 auto_fixed
    still_present = _rescan_codes(project_root, ch, body_file)
    auto_fixed, still_unfixed = [], []
    for issue in det_issues:
        if issue["code"] in still_present:
            still_unfixed.append(issue)  # 复核未通过：跑了修复但问题仍在
        else:
            auto_fixed.append({
                "dimension": issue["dimension"], "code": issue["code"],
                "desc": issue["desc"],
                "action": f"style_repair_engine fix 原地修复（{body_file.name}），已重跑校验器复核通过",
            })
    return auto_fixed, still_unfixed


# ============ 主流程 ============

def _get_user_audit_mode(project_root: Path) -> str:
    """v21 UX5: 读用户偏好.json 取 audit_mode。default=advisory。"""
    prefs_path = project_root / "_数据库" / "用户偏好.json"
    if not prefs_path.exists():
        return "advisory"
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        return (prefs.get("quality_control") or {}).get("audit_mode", "advisory")
    except Exception:
        return "advisory"


def _apply_audit_mode_filter(issues: list, mode: str) -> list:
    """v21 UX5: 按 audit_mode 调整 issue severity。
    - strict: 所有 advisory 升 warning，禁止豁免
    - advisory（默认）: 不变
    - permissive: warning 降 info，advisory 降 info（仅 hard_gate 保留）
    """
    if mode == "advisory":
        return issues
    out = []
    for i in issues:
        copy = dict(i)
        gate = i.get("gate_level", "advisory")
        sev = i.get("severity", "info")
        if mode == "strict":
            # advisory 升 warning（hard_gate 保持）
            if gate == "advisory" and sev in ("info", "advisory"):
                copy["severity"] = "warning"
                copy["_audit_mode_applied"] = "strict_upgrade"
        elif mode == "permissive":
            # 非 hard_gate 全降 info（更宽容）
            if gate != "hard_gate" and sev in ("warning", "error"):
                copy["severity"] = "info"
                copy["_audit_mode_applied"] = "permissive_downgrade"
        out.append(copy)
    return out


def audit_chapter(project_root: Path, ch: int, auto_fix: bool,
                  waivers: list = None, cluster_mode: bool = False,
                  cluster_key: str = None) -> dict:
    """审一章。waivers: [{code, reason}] —— v19 AI 豁免清单，对 advisory 项生效。

    cluster_mode: v2 cluster 化支持。True 时给 scanner 子进程传 CLUSTER_MODE=1 env。
    cluster_key: v2 cluster 化（如 "001"）· 用于激活 4 个 cluster-only scanner。
    """
    waivers = waivers or []
    body_file = cio.find_body_file(project_root, ch)
    if not body_file:
        return {"_fatal": f"第{ch}章正文未找到: {project_root}"}
    # v21 UX5: 读用户 audit_mode
    audit_mode = _get_user_audit_mode(project_root)

    # v2 cluster 化：scanner 子进程 env 透传
    # 2026-05-29：cluster mode 把真实 cluster_key 经 CLUSTER_ID env 透传给子进程
    # （沿用 run_cross_cluster_aggregates 的 CLUSTER_ID 先例），让 golden_three 能区分
    # cluster_001 ↔ cluster_002+ —— 否则任何 cluster 都借虚拟 ch=9000 恒激活黄金三章开场检测。
    _env_extra = None
    if cluster_mode:
        _env_extra = {"CLUSTER_MODE": "1"}
        if cluster_key:
            _env_extra["CLUSTER_ID"] = cluster_key

    # P2-15：13 个 scanner 并行执行（v2 cluster 化方案 · 2026-05-28）
    # 9 升维 + 4 新 cluster-only 全部集成进 audit_hub
    scanner_status = []
    all_issues = []

    vc = _SCRIPT_DIR / "validate_chapter.py"
    vs = _SCRIPT_DIR / "validate_style.py"
    ns = _SCRIPT_DIR / "narrative_scanner.py"
    ps = _SCRIPT_DIR / "plot_structure_scanner.py"
    hs = _SCRIPT_DIR / "hook_strength_scanner.py"
    gt = _SCRIPT_DIR / "golden_three_scanner.py"
    ss = _SCRIPT_DIR / "semantic_slop_scanner.py"
    nsss = _SCRIPT_DIR / "narrative_short_sentence_scanner.py"
    rnds = _SCRIPT_DIR / "repeat_noun_density_scanner.py"
    # 4 新 cluster-only scanner（v2 cluster 化方案）
    csvd = _SCRIPT_DIR / "cross_scene_voice_drift_scanner.py"
    fhs = _SCRIPT_DIR / "foreshadowing_handoff_scanner.py"
    lfcs = _SCRIPT_DIR / "locked_fact_cross_scene_scanner.py"
    povs = _SCRIPT_DIR / "pov_consistency_scanner.py"
    # 「输入教了输出要查」闭环：作者衔接手法分布对账（cluster · advisory · 永不 hard_gate）
    sseam = _SCRIPT_DIR / "scene_seam_scanner.py"
    # L2 防御：章末 cliffhanger 锚定扫描（cluster_001 ch4 三次翻车 sediment）
    ceas = _SCRIPT_DIR / "chapter_end_anchor_scan.py"
    # [2026-06-03] AI 长文退化三连指纹（否定对照/破折号密度+比喻复读+整段近重复）· advisory
    rrs = _SCRIPT_DIR / "rhetoric_repetition_scanner.py"

    # 2026-05-29 北极星修复 [H3-style]：审核必须以【作者风格档】为基线，而非写死通用爽文阈值。
    # 作者风格.json 存在即给 validate_style 传 --style，激活已有但从未触发的 _apply_style_overrides
    # （dialogue/para_mean/chapter_words/onomatopoeia 等 advisory 数值阈按作者基线放宽，不碰 hard_gate）。
    _style_json = project_root / "_数据库" / "作者风格.json"
    _style_args = ["--style", str(_style_json)] if _style_json.exists() else []

    # 每个任务：(name, cmd, ok_set, parse_fn)
    tasks = [
        ("validate_chapter",
         [sys.executable, str(vc), str(project_root), str(ch), "--json"],
         {0, 1, 2},
         lambda out, code: _parse_validate_chapter(out, code)),
        ("validate_style",
         [sys.executable, str(vs), str(body_file), "--strict"] + _style_args,
         {0, 1},
         lambda out, code: _parse_validate_style(out)),
        ("narrative_scanner",
         [sys.executable, str(ns), str(project_root), str(ch), "--all"],
         {0, 1, 2},
         lambda out, code: _parse_scanner_json(out, "narrative", NARRATIVE_DIM)),
        ("plot_structure_scanner",
         [sys.executable, str(ps), str(project_root), str(ch), "--all"],
         {0, 1, 2},
         lambda out, code: _parse_scanner_json(out, "plot", PLOT_DIM)),
        ("hook_strength_scanner",
         [sys.executable, str(hs), str(project_root), str(ch)],
         {0, 1},
         lambda out, code: _parse_flat_fscanner(out, "hook_strength")),
        ("golden_three_scanner",
         [sys.executable, str(gt), str(project_root), str(ch)],
         {0, 1},
         lambda out, code: _parse_flat_fscanner(out, "golden_three")),
        ("semantic_slop_scanner",
         [sys.executable, str(ss), str(project_root), str(ch), "--all"],
         {0, 1, 2},
         lambda out, code: _parse_scanner_json(out, "semantic", SEMANTIC_DIM)),
        ("narrative_short_sentence_scanner",
         [sys.executable, str(nsss), str(body_file)],
         {0, 1},
         lambda out, code: _parse_violations_scanner(
             out, "narrative_short_sentence_scanner",
             "NARRATIVE_SHORT_SENTENCE_OVERUSE", "风格")),
        ("repeat_noun_density_scanner",
         [sys.executable, str(rnds), str(body_file)],
         {0, 1},
         lambda out, code: _parse_violations_scanner(
             out, "repeat_noun_density_scanner",
             "REPEAT_NOUN_DENSITY", "风格")),
    ]

    # v2 cluster 化：cluster mode 下加 4 个 cluster-only scanner（需要 cluster_draft 路径）
    if cluster_mode and cluster_key:
        cluster_draft = project_root / "章节" / f"cluster_{cluster_key}_draft" / f"cluster_{cluster_key}_draft.txt"
        cluster_id_full = f"cluster_{cluster_key}"
        if cluster_draft.exists():
            tasks.extend([
                ("cross_scene_voice_drift",
                 [sys.executable, str(csvd), str(project_root), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_cross_scene_voice_drift(out)),
                ("foreshadowing_handoff",
                 [sys.executable, str(fhs), str(project_root), cluster_id_full],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "foreshadowing_handoff_scanner", "剧情")),
                ("locked_fact_cross_scene",
                 [sys.executable, str(lfcs), str(project_root), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_locked_fact_cross_scene(out)),
                ("pov_consistency",
                 [sys.executable, str(povs), str(project_root), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "pov_consistency_scanner", "视角")),
                # 「输入教了输出要查」闭环：衔接手法分布 vs 作者蒸馏档对账（advisory · 永不 hard_gate）。
                # 传 --style 走作者基线 + --project 兜底定位 作者风格_FINAL.json；SEAM_SCANNER_MODE 默认 active。
                ("scene_seam",
                 [sys.executable, str(sseam), str(cluster_draft),
                  "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_scene_seam(out)),
                # [2026-06-03] 修辞复读三连指纹 · cluster 视野 · advisory（兜底 gen_writer 元anti-slop·弱模型守不住的客观检测闭环）
                ("rhetoric_repetition",
                 [sys.executable, str(rrs), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "rhetoric_repetition_scanner", "RHETORIC_REPETITION", "风格")),
            ])
        # L2 防御：章末锚定扫描 · 仅在切章后 (有 第NNN章 文件) 才跑
        # 检测是否已切章
        chapter_dirs = sorted((project_root / "章节").glob("第[0-9]*章"))
        if chapter_dirs:
            ch_nums = []
            for d in chapter_dirs:
                m = re.search(r"第(\d+)章", d.name)
                if m and int(m.group(1)) < 9000:  # 排除虚拟 ch_9000
                    ch_nums.append(int(m.group(1)))
            # 2026-05-29 复审修复 [M12]：cluster 模式下 chapter_end_anchor 只扫本 cluster
            # 的 chapter_range，不再用 min~max 全工程章号（之前把别的 cluster 的章也扫进来 →
            # 报告污染：把历史 cluster 的章末问题算到当前 cluster 头上）。
            # 取不到本 cluster range（fluid v27 未回填）时回退全工程区间（保持原行为，零回归）。
            cl_rng = cluster_lookup.cluster_id_to_range(project_root, cluster_key)
            if cl_rng and len(cl_rng) == 2:
                lo = max(cl_rng[0], 1)
                hi = cl_rng[1]
                ch_nums = [c for c in ch_nums if lo <= c <= hi]
                if not ch_nums:
                    ch_nums = list(range(lo, hi + 1))
            if ch_nums:
                ch_range = f"{min(ch_nums)}-{max(ch_nums)}"
                tasks.append((
                    "chapter_end_anchor_scan",
                    [sys.executable, str(ceas), str(project_root), "--chapters", ch_range, "--json"],
                    {0, 1, 2},
                    lambda out, code: _parse_chapter_end_anchor(out, code),
                ))

    def _exec_one(task):
        name, cmd, ok_set, parse_fn = task
        # v2 cluster 化：cluster 调用上下文给 scanner 传 CLUSTER_MODE=1 env
        code, out, err = _run(cmd, env_extra=_env_extra)
        try:
            issues = parse_fn(out, code)
        except Exception as e:
            print(f"  [parse-error] {name}: {e}", file=sys.stderr)
            issues = []
        return name, code, code in ok_set, issues

    # 并行执行，结果按 submit 顺序读取（保证 scanner_status 顺序）
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(_exec_one, t) for t in tasks]
        for fut in futures:
            name, code, ok, issues = fut.result()
            scanner_status.append({"scanner": name, "exit_code": code, "ok": ok})
            all_issues += issues

    # 全部校验器都挂了 —— 致命，无法出审核结论
    if not any(s["ok"] for s in scanner_status):
        return {"_fatal": f"全部 {len(tasks)} 个校验器执行失败", "scanner_status": scanner_status}

    # v22 方案 3：角色情感弧偏差检测（advisory · 不阻塞）
    arc_drift_issues = _check_character_arc_drift(project_root, ch)
    if arc_drift_issues:
        all_issues += arc_drift_issues
        scanner_status.append({"scanner": "character_arc_drift", "exit_code": 0, "ok": True,
                               "issues_count": len(arc_drift_issues)})

    # 元问题嗅探：某校验器 100% 章节都 FAIL 同一项 -> 由 learning_loop --scan-recurring 跨章判定，
    # 这里只对单章内"明显误判"打 meta_suspect 标（如 validate_style 在已分离 v18 仍报字数虚高）
    # 单章无法判定 100% 命中，留空 —— meta 判定交给 learning_loop 跨章扫描

    # v19.2 工具校准自动降档（L2-0 止血 2026-05-30 · 原「自动豁免」改为「降一档严格度」）：
    # 基于 learning_loop 累积的 tool_calibration_suggestions。同一 code 在同场景被反复豁免
    # ≥3 次后，audit_hub 启动时对命中的 advisory issue【降一档 severity】（保留检测存在性，
    # 不再整条 waived / 完全关闭该检测）—— 修旧逻辑「无穷增益二元跳变、关了回不来」矫枉过正。
    calibration_suggestions = _load_calibration_suggestions(project_root)
    if calibration_suggestions:
        # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
        scene_types_set = set()
        progress_path = project_root / "_数据库" / "进度.json"
        if progress_path.exists():
            try:
                prog_data = json.loads(progress_path.read_text(encoding="utf-8"))
                # 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测 list(25)），
                # 裸 .items() 会 AttributeError 崩。先 normalize_blueprint 归一成 dict 再迭代。
                for cid, cdata in cluster_lookup.normalize_blueprint(prog_data).items():
                    for c in cdata.get("scene_storyboard", []):
                        if c.get("ch") == ch:
                            st = c.get("scene_type", [])
                            if isinstance(st, list):
                                scene_types_set.update(st)
                            elif st:
                                scene_types_set.add(st)
                            break
                    if scene_types_set:
                        break
            except (json.JSONDecodeError, ValueError):
                pass
        auto_softcap_count = 0
        for issue in all_issues:
            if issue.get("gate_level") == "hard_gate":
                continue  # hard_gate 不可自动降档/豁免（北极星⑤顾问制边界）
            match = _check_auto_waiver(issue.get("code", ""), scene_types_set, calibration_suggestions)
            if match:
                # L2-0 止血：命中 → 降一档严格度，【保留检测存在性】（不再 waived 整条关闭）
                if _apply_auto_calibration_softcap(issue, match):
                    auto_softcap_count += 1
        if auto_softcap_count:
            print(f"  [auto-calibration] 降档 {auto_softcap_count} 项严格度（保留检测，未关闭；基于 {len(calibration_suggestions)} 条 tool_calibration_suggestion）", file=sys.stderr)

    # v19 顾问制：应用 AI 豁免 —— advisory 项命中豁免 → waived=True；hard_gate 强制忽略豁免。
    # 在分类之前应用：被豁免的 issue 不进 det_issues/agent_issues，不计入 needs_agent。
    waived_issues = _apply_waivers(all_issues, waivers)

    # 分类：确定性可修 vs 需 agent。已豁免的 issue（waived=True）不参与修复决策。
    det_issues, agent_issues, info_issues = [], [], []
    for issue in all_issues:
        if issue.get("waived"):
            continue  # v19：被合理豁免 —— 不修、不派 agent
        if issue["severity"] == "info":
            info_issues.append(issue)
        elif _is_deterministic(issue):
            det_issues.append(issue)
        else:
            agent_issues.append(issue)

    auto_fixed = []
    pending_agent = []
    # 没开 --auto-fix 时、确定性问题没修 -> 进报告标注「可 --auto-fix」（不静默吞）
    fixable_not_attempted = []
    # 开了 --auto-fix 但跑完复核仍未修成的确定性问题 -> 这些要派 agent 兜底
    tried_but_unfixed = []

    if auto_fix and det_issues:
        # _apply_deterministic_fix 修完会重跑校验器复核，没真修成的不进 auto_fixed
        auto_fixed, tried_but_unfixed = _apply_deterministic_fix(
            project_root, ch, det_issues)
    elif det_issues:
        # 未开 --auto-fix：确定性问题没修 -> 进待处理（标注可 --auto-fix）
        fixable_not_attempted = det_issues

    # 需 agent 的问题（含「跑了修复但复核没过」的确定性问题）-> 配 suggested_agent
    # tried_but_unfixed 也走 _agent_for —— 机械修复都修不掉，说明是生成型/复杂问题，得派 agent
    for issue in agent_issues + tried_but_unfixed:
        agent, brief = _agent_for(issue)
        if agent is None:
            # 纯 warning 且无路由 -> 不强制派 agent，留在 issues 里
            # 但「跑了修复仍没修成」的即便是 warning 也要让调用方知道
            if issue in tried_but_unfixed:
                pending_agent.append({
                    "dimension": issue["dimension"], "severity": issue["severity"],
                    "gate_level": issue.get("gate_level", "advisory"),
                    "code": issue["code"], "desc": issue["desc"],
                    "suggested_agent": "novel-writer",
                    "fix_brief": "自动修复已尝试但复核未通过，需人工/agent 介入",
                })
            continue
        brief_final = brief
        if issue in tried_but_unfixed:
            brief_final = f"{brief}（注：audit_hub 已尝试自动修复但复核未通过）"
        pending_agent.append({
            "dimension": issue["dimension"], "severity": issue["severity"],
            "gate_level": issue.get("gate_level", "advisory"),
            "code": issue["code"], "desc": issue["desc"],
            "suggested_agent": agent, "fix_brief": brief_final,
        })

    # 没尝试修的确定性问题：不派 agent（agent 修不了机械格式问题），
    # 标注 suggested_agent=None + fix_brief 提示用 --auto-fix。
    # 只有 error/fatal 级才进 pending（warning 级确定性问题留在 issues 即可）
    for issue in fixable_not_attempted:
        if issue["severity"] in ("fatal", "error"):
            pending_agent.append({
                "dimension": issue["dimension"], "severity": issue["severity"],
                "gate_level": issue.get("gate_level", "advisory"),
                "code": issue["code"], "desc": issue["desc"],
                "suggested_agent": None,
                "fix_brief": "确定性可修问题 —— 用 audit_hub --auto-fix 自动解决，无需派 agent",
            })

    # v21 UX5: 按 audit_mode 调整 severity（strict 升 / permissive 降）
    all_issues = _apply_audit_mode_filter(all_issues, audit_mode)
    # v19：计数区分「被豁免」与「未豁免」。被豁免的 issue 不计入 fatal/error/warning，
    # 单独计 waived —— verdict 判定只看未豁免的残留问题。
    # v21 UX5 strict 模式下豁免被禁用——advisory 强升 warning 不能被豁免
    if audit_mode == "strict":
        active = [i for i in all_issues if not (i.get("waived") and i.get("gate_level") == "hard_gate")]
    else:
        active = [i for i in all_issues if not i.get("waived")]
    fatal = sum(1 for i in active if i["severity"] == "fatal")
    error = sum(1 for i in active if i["severity"] == "error")
    warning = sum(1 for i in active if i["severity"] == "warning")
    info = sum(1 for i in active if i["severity"] == "info")
    waived_count = len(waived_issues)

    # 2026-05-30 北极星复审：hard_gate（一致性/格式/穿帮）契约上不可豁免、必须阻断。但若某 scanner
    # 把 hard_gate code emit 成 info/warning severity（如 UNKNOWN_CHARACTER_DETECTED=info /
    # ITEM_HOLDER_ABSENT=warning），原 verdict 只看 fatal/error severity → 这些 hard_gate 进
    # info_issues 或无路由 agent_issues 死胡同被静默判 pass。兜底：凡未进 pending_agent 的未豁免
    # hard_gate 残留，强制补进 pending_agent（带 suggested_agent → real_pending → verdict=needs_agent）。
    _pending_codes = {p.get("code") for p in pending_agent}
    for issue in active:
        if issue.get("gate_level") == "hard_gate" and issue.get("code") not in _pending_codes:
            _hg_agent, _hg_brief = _agent_for(issue)
            pending_agent.append({
                "dimension": issue.get("dimension"), "severity": issue.get("severity"),
                "gate_level": "hard_gate", "code": issue.get("code"), "desc": issue.get("desc"),
                "suggested_agent": _hg_agent or "novel-validator-checker",
                "fix_brief": _hg_brief or "hard_gate 一致性/格式/穿帮问题，必须修复（不可豁免）",
            })
            _pending_codes.add(issue.get("code"))

    # pending_agent 区分：真需派 agent / 仅"可 --auto-fix"提示
    real_pending = [p for p in pending_agent if p.get("suggested_agent")]

    # v19 verdict：waived 出口 —— 没有真需派 agent、没有待修、无 fatal/error 残留，
    # 且确实发生过豁免 → verdict=waived（区别于 pass：pass 是本来就干净，
    # waived 是「有 advisory 问题但被 AI 合理豁免了」）。hard_gate 项不会被豁免，
    # 仍会进 real_pending/fatal/error，所以 hard_gate 残留时绝不会判 waived。
    if real_pending:
        verdict = "needs_agent"
    elif auto_fixed:
        verdict = "auto_fixed"
    elif pending_agent:
        # 只剩"可 --auto-fix"提示项 -> 没开 auto-fix 的确定性问题
        verdict = "fixable_pending"
    elif fatal == 0 and error == 0:
        verdict = "waived" if waived_count > 0 else "pass"
    else:
        verdict = "needs_agent" if (fatal or error) else "pass"

    return {
        "schema_version": "1.1",
        "chapter": ch,
        "ts": _ts(),
        "verdict": verdict,
        "_audit_mode": audit_mode,
        "summary": {"fatal": fatal, "error": error, "warning": warning,
                    "info": info, "waived": waived_count, "total": len(all_issues)},
        "issues": all_issues,
        "auto_fixed": auto_fixed,
        "pending_agent": pending_agent,
        "waived_issues": [
            {"dimension": i["dimension"], "gate_level": i.get("gate_level", "advisory"),
             "code": i["code"], "desc": i["desc"],
             "waive_reason": i.get("waive_reason", "")}
            for i in waived_issues
        ],
        "scanner_status": scanner_status,
    }


def _write_report(project_root: Path, ch: int, report: dict) -> Path:
    audit_dir = Path(project_root) / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    p = audit_dir / f"ch_{ch:03d}_audit.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _feed_learning_loop(project_root: Path, report_path: Path,
                        quiet: bool = False) -> None:
    """把审核报告交给自学习闭环。失败不中断主流程（learning 是增值，不是阻塞项）。
    quiet=True（--json 模式）时日志打到 stderr —— 否则会污染 stdout 的 JSON，
    导致调用方 json.loads(stdout) 失败。"""
    ll = _SCRIPT_DIR / "learning_loop.py"
    if not ll.is_file():
        return
    code, out, err = _run([sys.executable, str(ll), str(project_root),
                           "--ingest", str(report_path)])
    if out.strip():
        stream = sys.stderr if quiet else sys.stdout
        for ln in out.strip().splitlines():
            print(f"  [learning_loop] {ln}", file=stream)


def _print_summary(report: dict, report_path: Path) -> None:
    s = report["summary"]
    verdict_label = {"pass": "全通过", "auto_fixed": "已自动修复",
                     "needs_agent": "需派 agent",
                     "fixable_pending": "确定性问题待修（重跑 --auto-fix）",
                     "waived": "advisory 问题已被 AI 合理豁免"}.get(
                         report["verdict"], report["verdict"])
    print(f"== 第{report['chapter']}章 质检报告 ==")
    print(f"结论: {verdict_label}  |  致命 {s['fatal']} 错误 {s['error']} "
          f"警告 {s['warning']} 豁免 {s.get('waived', 0)}")
    if report.get("waived_issues"):
        print(f"AI 豁免 {len(report['waived_issues'])} 项（顾问制：advisory 项有理由可驳回）：")
        for w in report["waived_issues"]:
            print(f"  [豁免] [{w['dimension']}] {w['code']}: {w['waive_reason'][:50]}")
    if report["auto_fixed"]:
        print(f"自动修复 {len(report['auto_fixed'])} 项：")
        for f in report["auto_fixed"]:
            print(f"  [OK] [{f['dimension']}] {f['code']}: {f['desc'][:50]}")
    # pending 分两类：真需派 agent 的 / 确定性可 --auto-fix 的
    real_pending = [p for p in report["pending_agent"] if p.get("suggested_agent")]
    fixable = [p for p in report["pending_agent"] if not p.get("suggested_agent")]
    if real_pending:
        print(f"待派 agent {len(real_pending)} 项：")
        for p in real_pending:
            print(f"  [>>] [{p['dimension']}/{p['severity']}] {p['code']} "
                  f"-> {p['suggested_agent']}: {p['fix_brief'][:50]}")
    if fixable:
        print(f"可 --auto-fix 自动解决 {len(fixable)} 项（本次未修）：")
        for p in fixable:
            print(f"  [~~] [{p['dimension']}/{p['severity']}] {p['code']}: {p['desc'][:45]}")
    bad_scanners = [s for s in report["scanner_status"] if not s["ok"]]
    if bad_scanners:
        print(f"注意: {len(bad_scanners)} 个校验器执行异常 "
              f"({', '.join(s['scanner'] for s in bad_scanners)})")
    print(f"报告: {report_path}")


def _parse_waivers_arg(args: list) -> str:
    """从 argv 取 --waivers <path>。支持 --waivers=path 和 --waivers path 两种写法。"""
    for i, a in enumerate(args):
        if a == "--waivers" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--waivers="):
            return a.split("=", 1)[1]
    return ""


def _parse_cluster_arg(args):
    """v24: 解析 --mode cluster --cluster-id <key> 参数。返回 cluster_key 或 None"""
    if "--mode" not in args:
        return None
    try:
        mode_idx = args.index("--mode")
        if args[mode_idx + 1] != "cluster":
            return None
        cid_idx = args.index("--cluster-id")
        return args[cid_idx + 1].replace("cluster_", "")
    except (ValueError, IndexError):
        return None


def _cleanup_virtual_chapters(project_root: Path) -> None:
    """2026-05-29 复审修复 [M14]：清理虚拟章号 (>=9000) 残留。

    cluster 级 audit 用虚拟 ch=9000 占位复制 cluster_draft 成临时章目录 + 临时 manifest，
    跑完在 finally rmtree。但进程被 kill / 异常退出会留残留，之后被各处 glob(第*章)
    误捡（phantom 章）。本函数统一清「章节/第>=9000章」目录 + 「.manifest/ch_>=9000*.json」，
    供 audit_cluster 入口（建前先清）与 finally（兜底再清）复用。失败不抛（best-effort）。
    """
    import shutil as _shutil
    chap_dir = project_root / "章节"
    if chap_dir.exists():
        for d in chap_dir.glob("第[0-9]*章"):
            m = re.search(r"第(\d+)章", d.name)
            if m and int(m.group(1)) >= 9000:
                try:
                    if d.is_dir():
                        _shutil.rmtree(d)
                    else:
                        d.unlink(missing_ok=True)
                except Exception:
                    pass
    manifest_dir = project_root / "_数据库" / ".manifest"
    if manifest_dir.exists():
        for p in manifest_dir.glob("ch_[0-9]*.json"):
            m = re.search(r"ch_(\d+)", p.name)
            if m and int(m.group(1)) >= 9000:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass


def audit_cluster(project_root: Path, cluster_key: str, auto_fix: bool, waivers: list) -> dict:
    """v24: cluster 级 audit — 把 cluster_draft.txt 当一个超长章跑现有 scanner 集合，
    报告聚合所有 scene 的 issue。

    v26 修复（feedback_audit_hub_cluster_mode_virtual_manifest_auto_prepare）：
    旧版主代理需要手动 cp ch_<起首章>.json → ch_9000.json 让 validate_chapter
    找到 manifest。现自动复制 cluster 起首章 manifest 给虚拟 ch_9000。
    """
    cluster_draft_path = project_root / "章节" / f"cluster_{cluster_key}_draft" / f"cluster_{cluster_key}_draft.txt"
    cluster_changes_path = project_root / "章节" / f"cluster_{cluster_key}_draft" / f"cluster_{cluster_key}_changes.json"
    if not cluster_draft_path.exists():
        return {"_fatal": f"cluster_draft 不存在: {cluster_draft_path}"}
    # 复用现有 audit_chapter 逻辑，但传 cluster_key 当 chapter 编号占位（虚 ch=9999 + cluster_key）
    # 简化策略 v1：跑现有 chapter 级 audit 但 body_file 指向 cluster_draft
    # 这里需要 chapter_io.find_body_file 能找到 cluster_draft —— v1 用复制章节方式兜底
    import shutil
    fake_ch = 9000  # cluster 虚拟章号
    fake_ch_dir = project_root / "章节" / f"第{fake_ch:04d}章"
    # 2026-05-29 复审修复 [M14]：入口先清上次崩溃残留的虚拟章 + 虚拟 manifest。
    # 旧版只靠 finally rmtree 清理，进程被 kill / 异常退出时虚拟 ch>=9000 会残留磁盘，
    # 之后被各处 glob(第*章) 误捡（phantom 章污染 splitter / scanner / 拼接全文）。
    # 入口统一清「章节/第>=9000章」目录 + 「.manifest/ch_>=9000*.json」，确保干净起点。
    _cleanup_virtual_chapters(project_root)
    fake_ch_dir.mkdir(parents=True, exist_ok=True)
    fake_body = fake_ch_dir / f"第{fake_ch:04d}章.txt"
    fake_changes = fake_ch_dir / f"第{fake_ch:04d}章_changes.json"
    shutil.copy(cluster_draft_path, fake_body)
    if cluster_changes_path.exists():
        shutil.copy(cluster_changes_path, fake_changes)

    # v26: 自动准备虚拟 manifest (validate_chapter 校验需要 ch_9000.json 存在)
    manifest_dir = project_root / "_数据库" / ".manifest"
    fake_manifest = manifest_dir / f"ch_{fake_ch}.json"
    fake_manifest_compressed = manifest_dir / f"ch_{fake_ch}_compressed.json"
    created_fake_manifest = False
    try:
        if manifest_dir.exists() and not fake_manifest.exists():
            # 找 cluster 起首章 manifest 作为模板
            import json as _json
            start_ch = None
            shi_path = project_root / "_数据库" / "事件簇.json"
            if shi_path.exists():
                try:
                    shi = _json.loads(shi_path.read_text(encoding="utf-8"))
                    for c in shi.get("clusters", []):
                        cid = c.get("cluster_id", "").replace("cluster_", "")
                        if cid == cluster_key or c.get("cluster_id") == f"cluster_{cluster_key}":
                            cr = c.get("chapter_range") or []
                            if isinstance(cr, list) and len(cr) >= 1:
                                start_ch = cr[0]
                                break
                except Exception:
                    pass
            # 找候选 manifest 复制
            source_manifest = None
            if start_ch is not None:
                source_manifest = manifest_dir / f"ch_{start_ch:03d}.json"
            if not source_manifest or not source_manifest.exists():
                # fallback: 用任意已存在的 manifest
                existing = sorted(manifest_dir.glob("ch_[0-9]*.json"))
                existing = [p for p in existing if "_compressed" not in p.name and "_9000" not in p.name]
                if existing:
                    source_manifest = existing[0]
            if source_manifest and source_manifest.exists():
                shutil.copy(source_manifest, fake_manifest)
                src_compressed = source_manifest.parent / source_manifest.name.replace(".json", "_compressed.json")
                if src_compressed.exists():
                    shutil.copy(src_compressed, fake_manifest_compressed)
                created_fake_manifest = True
    except Exception as _e:
        # manifest 准备失败不阻断 · validate_chapter 会自己报 MANIFEST_MISSING
        pass

    try:
        # v2 cluster 化：cluster_mode=True · 传 cluster_key 激活 4 个 cluster-only scanner
        report = audit_chapter(project_root, fake_ch, auto_fix, waivers, cluster_mode=True, cluster_key=cluster_key)
        report["_cluster_mode"] = True
        report["_cluster_key"] = cluster_key
        report["_cluster_draft_path"] = str(cluster_draft_path)

        # v2 cluster 化方案（2026-05-28）：Phase A hot-fix 黑名单已删除·
        # scanner 已全员升维到 cluster 视野，无须黑名单兜底。
        return report
    finally:
        # 清理虚拟章节 + 虚拟 manifest
        try:
            shutil.rmtree(fake_ch_dir)
        except Exception:
            pass
        if created_fake_manifest:
            try:
                fake_manifest.unlink(missing_ok=True)
                fake_manifest_compressed.unlink(missing_ok=True)
            except Exception:
                pass
        # 2026-05-29 复审修复 [M14]：兜底统一清虚拟章号 >=9000 残留（含本次 + 历史残留），
        # 与入口的建前清理对称，双保险防 phantom 章。
        _cleanup_virtual_chapters(project_root)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python audit_hub.py <项目路径> <章节号> [--auto-fix] [--json] [--waivers <json路径>]"
              " | python audit_hub.py <项目路径> --mode cluster --cluster-id <key> [--auto-fix] [--waivers ...]",
              file=sys.stderr)
        sys.exit(3)
    project_root = Path(args[0]).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(3)
    auto_fix = "--auto-fix" in args
    want_json = "--json" in args
    waivers = _load_waivers(_parse_waivers_arg(args))

    # v24 cluster mode 入口
    cluster_key = _parse_cluster_arg(args)
    if cluster_key:
        report = audit_cluster(project_root, cluster_key, auto_fix, waivers)
        if "_fatal" in report:
            print(f"[FATAL] {report['_fatal']}", file=sys.stderr)
            sys.exit(3)
        # 写到 cluster 级报告路径
        report_dir = project_root / "_数据库" / ".audit"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"cluster_{cluster_key}_audit.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _feed_learning_loop(project_root, report_path, quiet=want_json)
        if want_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"== cluster_{cluster_key} 质检报告 ==")
            print(f"  verdict: {report.get('verdict', '?')}")
            print(f"  issues:  {len(report.get('issues', []))}")
            print(f"  报告: {report_path}")
        if report.get("verdict") == "needs_agent":
            sys.exit(2)
        if report.get("verdict") in ("auto_fixed", "fixable_pending"):
            sys.exit(1)
        sys.exit(0)

    # chapter mode (v23 兼容)
    try:
        ch = int(args[1])
    except ValueError:
        print(f"[FATAL] 章节号必须是整数: {args[1]}", file=sys.stderr)
        sys.exit(3)

    report = audit_chapter(project_root, ch, auto_fix, waivers)
    if "_fatal" in report:
        print(f"[FATAL] {report['_fatal']}", file=sys.stderr)
        sys.exit(3)

    report_path = _write_report(project_root, ch, report)
    # BUG1 修复：--json 模式必须传 quiet=True，否则 learning_loop 日志污染 stdout 的 JSON
    _feed_learning_loop(project_root, report_path, quiet=want_json)

    if want_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report, report_path)

    # 退出码：0=全通过 / 1=有问题已自动修完 / 2=需派 agent
    # 注：verdict=fixable_pending（确定性问题没开 --auto-fix）也归 1 ——
    #     调用方按 1 处理时重跑 --auto-fix 即可，不必派 agent
    # v19：verdict=waived（advisory 问题被 AI 合理豁免）归 0 —— 顾问制下豁免=放行，
    #     等同 pass；hard_gate 残留时不会判 waived，所以 0 退出码不会漏掉客观错误。
    if report["verdict"] == "needs_agent":
        sys.exit(2)
    if report["verdict"] in ("auto_fixed", "fixable_pending"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
