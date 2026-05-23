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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import chapter_io as cio  # noqa: E402

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
    "LOCKED_FACT_CONFLICT":     ("novel-validator-repair", "正文与锁定事实冲突，重写冲突段"),
    "FORESHADOWING_NOT_PAID":   ("novel-foreshadower", "Tier1 伏笔未回收，补 payoff"),
    "FUTURE_KNOWLEDGE_LEAK":    ("novel-validator-repair", "角色知道了不该知道的信息，重写"),
    "POV_HEAD_HOPPING":         ("novel-voice-keeper", "POV 越界，改回限定视角"),
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
}


def _gate_level_for(code: str, severity: str = "error") -> str:
    """v19：判定一条 issue 的权力等级。hard_gate 不可豁免，其余 advisory。
    v23.12：STYLE_单段超长 只在 fatal/error 时是 hard_gate（超例外 ≤1 才 FAIL）；
    WARN 状态（80-120 警告区或例外内）降 advisory 可豁免。"""
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


def _run(cmd: list) -> tuple:
    """跑子进程，返回 (exit_code, stdout, stderr)。子进程隔离 —— 任一校验器挂了不连累其他。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 99, "", "[TIMEOUT] 校验器超时 180s"
    except Exception as e:
        return 98, "", f"[EXEC-ERROR] {e}"


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
        if len(reason) >= 100:
            print(f"  [waivers] {code} 理由超 100 字，已截断", file=sys.stderr)
            reason = reason[:100]
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

def _is_deterministic(issue: dict) -> bool:
    """该问题是否可由 style_repair_engine 确定性修复。"""
    return issue["code"] in DETERMINISTIC_FIX_CODES


def _agent_for(issue: dict) -> tuple:
    """该问题需派哪个 agent + fix_brief。返回 (agent, brief) 或 (None, None)。"""
    if issue["code"] in AGENT_ROUTING:
        return AGENT_ROUTING[issue["code"]]
    # 致命/错误级但没显式路由 -> 兜底派 validator-repair
    if issue["severity"] in ("fatal", "error"):
        return ("novel-validator-repair", issue.get("fix_hint") or issue["desc"])
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
                  waivers: list = None) -> dict:
    """审一章。waivers: [{code, reason}] —— v19 AI 豁免清单，对 advisory 项生效。"""
    waivers = waivers or []
    body_file = cio.find_body_file(project_root, ch)
    if not body_file:
        return {"_fatal": f"第{ch}章正文未找到: {project_root}"}
    # v21 UX5: 读用户 audit_mode
    audit_mode = _get_user_audit_mode(project_root)

    # P2-15：7 个 scanner 并行执行（借鉴 Programmatic Tool Calling 思路）。
    # 原串行 ≈ 7×3s=21s；并行 ≈ 3-5s。子进程隔离已保证「任一挂了不连累其他」，
    # 并行也保持这个属性。任务列表顺序 = scanner_status 落库顺序，保证审计可读性。
    scanner_status = []
    all_issues = []

    vc = _SCRIPT_DIR / "validate_chapter.py"
    vs = _SCRIPT_DIR / "validate_style.py"
    ns = _SCRIPT_DIR / "narrative_scanner.py"
    ps = _SCRIPT_DIR / "plot_structure_scanner.py"
    hs = _SCRIPT_DIR / "hook_strength_scanner.py"
    gt = _SCRIPT_DIR / "golden_three_scanner.py"
    ss = _SCRIPT_DIR / "semantic_slop_scanner.py"

    # 每个任务：(name, cmd, ok_set, parse_fn)
    # parse_fn 统一接收 (stdout, exit_code) 返回 issues list
    tasks = [
        ("validate_chapter",
         [sys.executable, str(vc), str(project_root), str(ch), "--json"],
         {0, 1, 2},
         lambda out, code: _parse_validate_chapter(out, code)),
        ("validate_style",
         [sys.executable, str(vs), str(body_file), "--strict"],
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
    ]

    def _exec_one(task):
        name, cmd, ok_set, parse_fn = task
        code, out, err = _run(cmd)
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
        return {"_fatal": "全部 7 个校验器执行失败", "scanner_status": scanner_status}

    # 元问题嗅探：某校验器 100% 章节都 FAIL 同一项 -> 由 learning_loop --scan-recurring 跨章判定，
    # 这里只对单章内"明显误判"打 meta_suspect 标（如 validate_style 在已分离 v18 仍报字数虚高）
    # 单章无法判定 100% 命中，留空 —— meta 判定交给 learning_loop 跨章扫描

    # v19.2 工具校准自动豁免：基于 learning_loop 累积的 tool_calibration_suggestions
    # 同一 code 在同场景被反复豁免 ≥4 次后，audit_hub 启动时自动加豁免（无需 writer 重新写理由）。
    calibration_suggestions = _load_calibration_suggestions(project_root)
    if calibration_suggestions:
        # 从 进度.json 的 chapter_plan 读本章 scene_types
        scene_types_set = set()
        progress_path = project_root / "_数据库" / "进度.json"
        if progress_path.exists():
            try:
                prog_data = json.loads(progress_path.read_text(encoding="utf-8"))
                for c in prog_data.get("chapter_plan", []):
                    if c.get("ch") == ch:
                        st = c.get("scene_type", [])
                        if isinstance(st, list):
                            scene_types_set.update(st)
                        elif st:
                            scene_types_set.add(st)
                        break
            except (json.JSONDecodeError, ValueError):
                pass
        auto_waived_count = 0
        for issue in all_issues:
            if issue.get("gate_level") == "hard_gate":
                continue  # hard_gate 不可自动豁免
            match = _check_auto_waiver(issue.get("code", ""), scene_types_set, calibration_suggestions)
            if match:
                # 把自动豁免追加到 waivers 列表（让 _apply_waivers 走原有逻辑）
                waivers.append({
                    "code": issue["code"],
                    "reason": f"[auto-calibration] learning_loop 累积建议: {match.get('suggestion', '')[:60]}",
                })
                auto_waived_count += 1
        if auto_waived_count:
            print(f"  [auto-calibration] 自动豁免 {auto_waived_count} 项（基于 {len(calibration_suggestions)} 条 tool_calibration_suggestion）", file=sys.stderr)

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


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("用法: python audit_hub.py <项目路径> <章节号> "
              "[--auto-fix] [--json] [--waivers <json路径>]",
              file=sys.stderr)
        sys.exit(3)
    project_root = Path(args[0]).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(3)
    try:
        ch = int(args[1])
    except ValueError:
        print(f"[FATAL] 章节号必须是整数: {args[1]}", file=sys.stderr)
        sys.exit(3)
    auto_fix = "--auto-fix" in args
    want_json = "--json" in args
    # v19：--waivers 指向 _changes.json 或独立 waivers.json，读 self_eval.waivers
    waivers = _load_waivers(_parse_waivers_arg(args))

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
