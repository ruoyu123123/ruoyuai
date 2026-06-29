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
from frozen_util import child_python, scripts_dir  # frozen-aware 子解释器/脚本目录（dev=no-op）
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = scripts_dir()
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
    "dialogue_tag_density": "对话",
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
#
# ---- 🔴 思维层探针硬约束（2026-06-14 · 北极星⑤制度锁 · 设计约束·不可删）----
# 物理可靠性上限：作者「思维/意图/读者心理/因果链/弧线形状」类探针的 LLM/人评
# 一致性天花板 ≈ 0.71-0.74（心理深度类标注 alpha 上限；arc-shape 被金标准证伪实证）。
# 因此任何思维层探针 code（含 PROMISE_PAYOFF_GAP / ARC_SHAPE_* / INTENT_* /
# READER_* / CAUSALITY_* / PPP_* 等 D1-D8 派生 code）一律 advisory，
# **永不加入 HARD_GATE_CODES**——探针自身噪声可能 ≥ 真实效应，bootstrap 再窄
# 也是测探针噪声不是测改动。这类 code 即便其 scanner 自报 gate_level='hard_gate'，
# 也被 _gate_level_for()（见下）+ 两条 _parse_*_scanner 双闸拦回 advisory（不在白名单即降档）。
# 升 active 靠跨栈/金标准抽样（replication_fidelity_check / distill_holdout），不靠门禁；
# judge 同源高一致只证「稳定」不证「准确」。增改本清单 = 同步改 STRUCTURE.md §11.2
# （单一来源），且新 code 必须先过 tests/test_thinking_probe_advisory.py 的断言。
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
    # 🔴 2026-06-27 C03：子系统载荷空货架 = 机器永不点火（性质同 MANIFEST_MISSING·文件契约层）。
    # 由 scaffold_subsystems verify --content / plan_step_gates.check_subsystems(content_check=True)
    # emit。仅这 3 个「引擎零触发」码 hard；其余 31 子系统裸骨架 = fluid-allowed 永远 advisory。
    # 回归锁：cluster_002+ ME/storyboard 空必须显式豁免（标记只查 clusters[0] + 池非空·永不命中）。
    "RIPPLE_RULES_EMPTY",            # 涟漪规则空 = world_evolution_engine 零触发（北极星②）
    "GRAND_TREND_ME_POOL_EMPTY",    # 当前卷 ME 池空 = 大势无方向·cluster_emergence 不点火（北极星③）
    "CLUSTER001_STORYBOARD_EMPTY",  # cluster_001 scene_storyboard 空 = 首块未详化（黄金三章必详化）
    # 🔴 2026-06-27 C18：splitter 字数守恒被破坏（丢字/重复/空块/计数失配）= 北极星④纯格式层契约破损。
    # chapter_splitter.run_freestyle 落盘前确定性自检 raise SplitterIntegrityError → main exit2；
    # 性质同 MANIFEST_MISSING/FILE_NOT_FOUND（文件契约客观断点·非风格选择）。
    "SPLIT_WORD_NOT_CONSERVED",
}


def _resolve_audit_genre(project_root) -> str:
    """阶段3：审计期解析本书题材（作者档 genre_tags > 书名推断 > unknown）。"""
    from pathlib import Path as _P
    try:
        sp = _P(project_root) / "_数据库" / "作者风格.json"
        if sp.exists():
            prof = json.loads(sp.read_text(encoding="utf-8"))
            gt = prof.get("genre_tags")
            if isinstance(gt, list) and gt:
                return str(gt[0]).strip().lower()
    except Exception:
        pass
    try:
        import cluster_segmenter as _cs
        return _cs._infer_genre_from_naming(_P(project_root), _P(project_root).name)
    except Exception:
        return "unknown"


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


def _run(cmd: list, env_extra: dict = None, timeout: int = 180) -> tuple:
    """跑子进程，返回 (exit_code, stdout, stderr)。子进程隔离 —— 任一校验器挂了不连累其他。

    env_extra: v2 cluster 化支持。传 {"CLUSTER_MODE": "1"} 让子进程 scanner 感知 cluster 视野。
    timeout: 秒。NN scanner 批推理需要更长(300s)。
    """
    try:
        env = None
        if env_extra:
            import os as _os
            env = {**_os.environ, **env_extra}
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 99, "", f"[TIMEOUT] 校验器超时 {timeout}s"
    except Exception as e:
        return 98, "", f"[EXEC-ERROR] {e}"


# 🔴 2026-06-27 P1-07: 解析 PID per-作者 state 给 chapter_end_anchor_scan 算 --weak-anchor-ratio。
# 找 author_dir → 调 pid_threshold_tuner.apply_pid_delta（带物理 clamp）→ 取出新阈值。
# 失败返回 None（scanner 走默认 0.15 · 零回归）。
def _resolve_chapter_end_weak_anchor_ratio(project_root: Path):
    try:
        author_dir = None
        sp = project_root / "_数据库" / "作者风格.json"
        if sp.exists():
            try:
                sj = json.loads(sp.read_text(encoding="utf-8"))
                src = sj.get("style_source") or sj.get("_source_path")
                if src:
                    cand = Path(src)
                    author_dir = cand.parent if cand.is_file() else cand
            except (OSError, json.JSONDecodeError):
                author_dir = None
        if author_dir is None or not author_dir.exists():
            guess = project_root.parent.parent / "styles" / project_root.name
            if guess.exists():
                author_dir = guess
        if author_dir is None or not author_dir.exists():
            return None
        import importlib
        pid = importlib.import_module("pid_threshold_tuner")
        base = {"chapter_end_weak_anchor_ratio": {"_scalar": 0.15}}
        out = pid.apply_pid_delta({k: dict(v) for k, v in base.items()}, author_dir)
        val = out.get("chapter_end_weak_anchor_ratio", {}).get("_scalar")
        if isinstance(val, (int, float)) and val != 0.15:
            return float(val)
    except Exception:
        return None
    return None


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


# 🔴 2026-06-27 C06：章末物理污染 hard_gate 真阻断（治 v27 freestyle 无真阻断点）。
# 病灶：CHAPTER_END_FORBIDDEN_SCREENPLAY 在 STRUCTURE§11 钉死不可豁免，但 freestyle 链路无真阻断——
#   step3 audit 时草稿【尚未切章】（chapter_end_anchor_scan 依赖 第NNN章 文件，在 step6 切章后才跑且只
#   advisory 不 exit2）→ 剧本体污染溜过最该拦的点。修：step3 对【整段 cluster_draft】跑 SCREENPLAY_PATTERNS。
# 北极星⑤边界：只硬毙【位置无关】的 SCREENPLAY（剧本体镜头/旁白/音效指令 = 排版/格式契约破损·任何风格、
#   任何位置都非法 → 整段硬扫安全）。TRANSITION 物理分隔符须锚定章末（中段场景分隔某些作者合法）故不在
#   整段层硬扫；语义收束句（『灯熄了』等）保持 advisory（绝不在此升格）。
def _scan_cluster_draft_screenplay(cluster_draft_path: Path) -> list:
    """C06：整段 cluster 草稿扫剧本体 SCREENPLAY 标记（位置无关 hard_gate）。

    复用 chapter_end_anchor_scan.SCREENPLAY_PATTERNS（同源·防口径分歧），命中即 emit
    CHAPTER_END_FORBIDDEN_SCREENPLAY · severity=error → _gate_level_for 落 hard_gate →
    verdict=needs_agent → main() exit 2（当前真正能拦的点）。仅 SCREENPLAY 一族（排版污染·
    任何位置非法）；不碰 TRANSITION/CLOSURE（位置/语义敏感→保 advisory·不升格）。"""
    issues = []
    try:
        text = cluster_draft_path.read_text(encoding="utf-8")
    except Exception:
        return issues
    try:
        import chapter_end_anchor_scan as _ceas_mod
        patterns = _ceas_mod.SCREENPLAY_PATTERNS
    except Exception:
        # fallback：模块不可用时用内联同源 pattern（保持检测闭环·绝不静默放过）
        patterns = [
            (r"（镜头[^）]{0,15}）", "剧本体镜头指令"),
            (r"（切镜[^）]{0,10}）", "剧本体切镜"),
            (r"（旁白[^）]{0,15}）", "剧本体旁白"),
            (r"（画外音[^）]{0,15}）", "剧本体画外音"),
            (r"（音效[^）]{0,15}）", "剧本体音效"),
            (r"（[^）]{0,15}的视角[^）]{0,5}）", "剧本体 POV 指令"),
            (r"（[^）]{0,10}离开[^）]{0,10}视角[^）]{0,5}）", "剧本体 POV 切换"),
        ]
    seen = set()
    for pat, reason in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE | re.MULTILINE):
            frag = m.group(0)[:60]
            if frag in seen:
                continue
            seen.add(frag)
            issues.append({
                "dimension": "格式契约",
                "severity": "error",  # → _gate_level_for 落 hard_gate（info 会被降 advisory）
                "gate_level": "hard_gate",
                "code": "CHAPTER_END_FORBIDDEN_SCREENPLAY",
                "desc": f"整段草稿出现剧本体标记（{reason}）· matched={frag}"
                        f" · 小说正文禁止剧本体镜头/旁白/音效指令（位置无关 hard_gate）",
                "source": "audit_hub_cluster_screenplay_scan",
                "fix_hint": "删除剧本体过渡标记 · POV 不切换让角色全程在场",
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
        desc = it.get("msg", "") or it.get("message", "") or it.get("desc", "")
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


ANCHOR_WINDOW_MAX_CJK = 40  # R24 W12 Batch-KK·anchored_advisory_contract


def _collect_anchor_spans(violations: list) -> list:
    """[2026-06-20 R9 W5 Batch-M·F3 AnchoredAI] 收集 violation 里的 anchor_span 字段。

    Finding schema 扩展：每条 issue 可携带 anchor_spans[]（char_start/char_end/surface_text），
    供 gen_fixer prompt 用 surface_text markdown >quote 包裹 + 只锚定段改动闭环；
    `out_of_anchor_edit_ratio` 可观察 (gen_fixer 日志侧)。

    向后兼容：scanner 未填 anchor_span 时返回空列表，不影响现有判定路径。
    L41 narrator_commentary_scanner / 后续 prose_rhythm / repeat_noun_density 等可逐步填充。

    [2026-06-22 R24 W12 Batch-KK·anchored_advisory_contract] schema 升级：
      · anchor_window: 引用窗口（≤40 CJK 字符，超出截断）
      · recursive_widen_level: int(0..3) 递归加宽层（0=原 span，1-3=外扩重试）
      · 字段都 OPTIONAL·已有 scanner 不强制改·新 scanner 选填
    advisory 必填 anchor·hard_gate 选填兼容（北极星⑤不可豁免 hard_gate 一致性问题
    本就有 code/dim，安全裕度）。
    """
    spans = []
    for v in violations or []:
        if not isinstance(v, dict):
            continue
        a = v.get("anchor_span")
        if isinstance(a, dict) and "char_start" in a and "char_end" in a:
            try:
                entry = {
                    "char_start": int(a["char_start"]),
                    "char_end": int(a["char_end"]),
                    "surface_text": str(a.get("surface_text", ""))[:200],
                }
                # anchor_window：截到 40 CJK（多语言混合按 char 截）
                aw = a.get("anchor_window")
                if isinstance(aw, str) and aw:
                    cjk_buf, c = [], 0
                    for ch in aw:
                        cjk_buf.append(ch)
                        if "一" <= ch <= "鿿":
                            c += 1
                        if c >= ANCHOR_WINDOW_MAX_CJK:
                            break
                    entry["anchor_window"] = "".join(cjk_buf)
                rwl = a.get("recursive_widen_level")
                if isinstance(rwl, int) and 0 <= rwl <= 3:
                    entry["recursive_widen_level"] = rwl
                spans.append(entry)
            except (TypeError, ValueError):
                continue
    return spans


def _parse_violations_scanner(stdout: str, source: str, code: str, dimension: str) -> list:
    """narrative_short_sentence / repeat_noun_density：violations[]（无 per-item code），
    顶层 gate_level=advisory。每条 violation 的 severity 为 major/minor（映射 error/warning）。
    PASS（无 violation）→ 不产 issue。合成 1 条聚合 issue（severity 取最高）。

    [F3 AnchoredAI · 2026-06-20] 若 violations 含 anchor_span(char_start/char_end/surface_text)
    则透传到 issue.anchor_spans[]，供 gen_fixer 仅锚定段改动 + 可观察 out_of_anchor_edit_ratio。
    向后兼容：scanner 未填则字段缺省。"""
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
    # 2026-06-14 B-2：顶层 gate_level 升格同样以 HARD_GATE_CODES 为权威（与 _parse_issues_list_scanner
    # L476 的双闸对称）。防 violations 形态 scanner 顶层越权自立 hard_gate（北极星⑤「hard_gate 清单
    # 单一来源，不得各自另立」）。现有 narrative_short_sentence / repeat_noun_density 顶层皆 advisory，
    # 此守卫对现状 no-op；纯防御未来思维层探针（若实现成 violations 输出 + 顶层 hard_gate）绕过制度锁。
    if gl != "hard_gate" and top_gl == "hard_gate" and code in HARD_GATE_CODES:
        gl = "hard_gate"
    desc = f"{report.get('scanner', source)}: {len(violations)} 处违规（verdict={report.get('verdict','?')}）"
    issue = {
        "dimension": dimension, "severity": severity,
        "gate_level": gl, "code": code, "desc": desc,
        "source": source, "fix_hint": "",
        "waived": False, "waive_reason": "",
    }
    # F3 AnchoredAI · 透传锚点
    anchor_spans = _collect_anchor_spans(violations)
    if anchor_spans:
        issue["anchor_spans"] = anchor_spans
    issues.append(issue)
    return issues


def _parse_advisories_scanner(stdout: str, source: str, default_code: str, dimension: str) -> list:
    """[G2 P2 2026-06-22] 通用解析：scanner 顶层 `advisories[]` 形态(R23 W11 Batch-II
    writer_growth_dashboard / antagonist_valence_trajectory / 部分 cross-cluster 工具)。

    形态：{ "scanner": "...", "advisories": [{"code", "msg", ...}], "violations"?: [...] }
    若有 `violations` 优先按 violations 走（沿用 _parse_violations_scanner 合成 1 条聚合）；
    否则按 advisories 每条出一条 advisory issue。无 advisory / violations → 不产 issue。

    与 _parse_issues_list_scanner 区别：那个吃 `issues[]`（已带 severity + gate_level 元信息），
    这个吃 `advisories[]`（只带 code+msg，severity 兜底为 info 让顶层 audit_mode 决定）。

    全部以 HARD_GATE_CODES 为权威·北极星⑤：advisory 类 scanner 顶层永不自立 hard_gate。"""
    issues = []
    report = _load_scanner_json(stdout)
    if not report:
        return issues
    # 走 violations 优先（active 模式下 scanner 已自带 violations[]）
    if report.get("violations"):
        return _parse_violations_scanner(stdout, source, default_code, dimension)
    advisories = report.get("advisories", []) or []
    for it in advisories:
        if not isinstance(it, dict):
            continue
        code = it.get("code", "") or default_code
        if not code:
            continue
        severity = _norm_severity(it.get("severity", "info"))
        gl = _gate_level_for(code, severity)
        desc = it.get("msg", "") or it.get("message", "") or it.get("desc", "")
        issues.append({
            "dimension": dimension, "severity": severity,
            "gate_level": gl, "code": code, "desc": str(desc),
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

# 🔴 2026-06-27 C09 豁免诚实审计（WAIVER-HONESTY-AUDIT · META-only · 绝不翻 verdict）：
# 运动员当裁判防线——writer 自己在 self_eval.waivers 写豁免，旧逻辑唯一校验是「理由非空 + <300 字」。
# 下面两个阈值用于「疑似 blanket 豁免」检测，只 emit META 信号 + 喂 learning_loop，
# 绝不 block / cap-reject / downgrade-fail（风格与通用爽文基线合法冲突的 cluster 应能全豁免）。
_WAIVER_BLANKET_RATE = 0.7        # advisory 豁免率 > 此值 → 疑似 blanket
_WAIVER_BLANKET_REASON_K = 3      # 单一 reason 串映射 >= K 个 distinct code → 疑似 blanket


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
            print(f"  [waivers] {code} 无理由，豁免无效（豁免必带具体理由）")
            continue
        # v2 cluster 化方案 Phase A hot-fix（2026-05-28）：
        # 阈值 100→300。cluster mode 涉及多场景多角色多伏笔，理由 150-280 字常见。
        # stderr 缩短打印（前 80 字+省略），完整理由仍写入 audit 报告 JSON。
        if len(reason) >= 300:
            print(f"  [waivers] {code} 理由超 300 字，已截断: {reason[:80]}…", file=sys.stderr)
            reason = reason[:300]
        out.append({"code": code, "reason": reason})
    # 🔴 2026-06-27 C09 豁免诚实审计：按 code 去重（保最长 reason · 冲突打 warn）。
    # 运动员当裁判防线之一——writer 可能对同一 code 重复声明（复制粘贴/刷豁免）。去重纯卫生·
    # 行为中性（同 code 本就映射一条 reason），保最长是因更长更可能是具体到本 cluster 的真理由。
    deduped: dict = {}
    for w in out:
        code = w["code"]
        prev = deduped.get(code)
        if prev is None:
            deduped[code] = w
            continue
        if prev["reason"] != w["reason"]:
            print(f"  [waivers] {code} 多条豁免理由冲突，保留最长一条", file=sys.stderr)
        if len(w["reason"]) > len(prev["reason"]):
            deduped[code] = w
    return list(deduped.values())


def _apply_waivers(all_issues: list, waivers: list) -> list:
    """v19 顾问制核心：对 issue 应用 AI 豁免。
      - advisory 项 code 命中豁免清单 → waived=True + waive_reason 记录理由
      - hard_gate 项即便命中豁免清单也【强制忽略豁免】（不可豁免，仍按问题处理）
    原地修改 all_issues，返回被成功豁免的 issue 引用列表（供报告 waived_issues 段）。

    🔴 2026-06-27 C09 豁免诚实审计：orphan 豁免（code 不在本次任何 issue 里）从 by_code
    排除并 log——而非静默 no-op（旧逻辑里 orphan 只是「凑巧没命中」，不留痕迹）。排除是行为中性
    的（orphan 本就匹配不到 issue），只是把「凭空豁免不存在的 code」显性化。apply-moment 的
    blanket / orphan 量化信号在 _compute_waiver_audit 里统一算（META-only · 不在此翻 verdict）。"""
    if not waivers:
        return []
    issue_codes = {i.get("code", "") for i in all_issues}
    by_code = {w["code"]: w["reason"] for w in waivers}
    # orphan：豁免了一个本次根本不存在的 code → 从 by_code 排除（不再误匹配后续 issue）+ log
    for c in sorted(by_code):
        if c not in issue_codes:
            print(f"  [waivers] {c} 豁免的 code 在本次 issue 中不存在（orphan），已忽略该豁免",
                  file=sys.stderr)
    by_code = {c: r for c, r in by_code.items() if c in issue_codes}
    waived = []
    for issue in all_issues:
        code = issue.get("code", "")
        if code not in by_code:
            continue
        if issue.get("gate_level") == "hard_gate":
            # 不可豁免：即便传了理由也强制忽略
            print(f"  [waivers] {code} 是 hard_gate，不可豁免，豁免理由已忽略")
            continue
        issue["waived"] = True
        issue["waive_reason"] = by_code[code]
        waived.append(issue)
    return waived


def _compute_waiver_audit(all_issues: list, waivers: list, waived_issues: list) -> dict:
    """🔴 2026-06-27 C09 豁免诚实审计信号（META-only · 喂 learning_loop · 【绝不翻 verdict】）。

    纯函数：不改 all_issues / waived_issues，只产观察信号。北极星护栏——
    blanket / orphan 只是 advisory META flag + ledger 喂 learning_loop，
    绝不 block / cap-reject / downgrade-fail / 剥合法 waiver。

    blanket_suspected 触发条件（任一）：
      - advisory 豁免率 waive_rate > _WAIVER_BLANKET_RATE
      - 单一 reason 串映射 >= _WAIVER_BLANKET_REASON_K 个 distinct code（同理由刷多 code）
    orphan_codes：豁免了本次 issue 里不存在的 code（与 _apply_waivers 同口径重算）。
    """
    issue_codes = {i.get("code", "") for i in all_issues}
    # advisory 总数 = 非 hard_gate 的 issue（hard_gate 不可豁免，不进豁免分母）
    advisory_total = sum(1 for i in all_issues if i.get("gate_level") != "hard_gate")
    advisory_waived = len(waived_issues)
    waive_rate = round(advisory_waived / advisory_total, 4) if advisory_total else 0.0
    # 单一 reason → distinct code 集合（同 reason 刷多 code = 运动员当裁判典型特征）
    reason_to_codes: dict = {}
    for issue in waived_issues:
        reason = (issue.get("waive_reason") or "").strip()
        if not reason:
            continue
        reason_to_codes.setdefault(reason, set()).add(issue.get("code", ""))
    repeated_reason_codes = {
        # reason 串可能很长 → key 截断到 60 字便于人读 + ledger 落盘
        (r[:60] + ("…" if len(r) > 60 else "")): sorted(codes)
        for r, codes in reason_to_codes.items()
        if len(codes) >= _WAIVER_BLANKET_REASON_K
    }
    orphan_codes = sorted({w["code"] for w in waivers} - issue_codes)
    blanket_suspected = (waive_rate > _WAIVER_BLANKET_RATE) or bool(repeated_reason_codes)
    return {
        "advisory_total": advisory_total,
        "advisory_waived": advisory_waived,
        "waive_rate": waive_rate,
        "blanket_suspected": blanket_suspected,
        "repeated_reason_codes": repeated_reason_codes,
        "orphan_codes": orphan_codes,
    }


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
    _, out, _ = _run([child_python(), str(vs), str(body_file), "--strict"])
    for issue in _parse_validate_style(out):
        present.add(issue["code"])
    # validate_chapter（BANNED_WORD 来自这里）—— v18 #12：--json 结构化输出
    vc = _SCRIPT_DIR / "validate_chapter.py"
    _, out, _ = _run([child_python(), str(vc), str(project_root), str(ch), "--json"])
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
    code, out, err = _run([child_python(), str(repair), str(body_file),
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
    # [2026-06-03] 句法节奏/流水账作文感（句长 vs 作者基线 + 主语+动作 streak + 主语开头占比）· advisory
    prs = _SCRIPT_DIR / "prose_rhythm_scanner.py"
    # [2026-06-13 阶段1] 叙事节奏序列（张力轨迹后段保持/匀速平铺/节拍单调 vs 作者基线）· advisory
    nrs = _SCRIPT_DIR / "narrative_rhythm_scanner.py"
    # [2026-06-15 记忆调研W3] 情绪曲线 live 回查（actual valence 曲线 vs manifest 注入 target）· advisory
    ecrs = _SCRIPT_DIR / "emotion_curve_rescan_scanner.py"
    # [2026-06-15 记忆调研W1] 潜台词/on-the-nose 情绪直陈回查（说透情绪密度·金标准校准阈值）· advisory
    srss = _SCRIPT_DIR / "subtext_rescan_scanner.py"
    # [2026-06-15 记忆调研W4] dramatic irony 信号回查（显式标志词 tell 过多·金标准证好作者用 show）· advisory
    dis = _SCRIPT_DIR / "dramatic_irony_scanner.py"
    # [2026-06-15 记忆调研W5] 反转揭底 tell 回查（揭底显式标志词 tell 过多·金标准证好作者用 show）· advisory
    rvss = _SCRIPT_DIR / "reveal_show_scanner.py"
    # [2026-06-16 盲区落地] 句法多样性(CR-POS 句法骨架同形复用 + theme over-explanation·作者自适应 z-band) · advisory
    sds = _SCRIPT_DIR / "syntactic_diversity_scanner.py"
    # [2026-06-16 穷尽核查#2] 情绪标点综合密度 vs 作者基线(感叹/问号/省略号·金标准综合避单类误报·单边下尾) · advisory
    eps = _SCRIPT_DIR / "emotional_punctuation_scanner.py"
    # [2026-06-16 第二轮穷尽核查#2] 功能词指纹偏离作者基线(SFS 最高权重维却 0 写作时回查·金标准综合 min0.86→FLOOR0.6·单边下尾) · advisory
    fwfs = _SCRIPT_DIR / "function_word_fingerprint_scanner.py"
    # [2026-06-16 第二轮穷尽核查#3] 二阶句长节奏动力学(Δ²var/var·order-sensitive·探针7 permutation-invariant 看不见时序·DivEye·通用 floor3.0) · advisory
    sors = _SCRIPT_DIR / "second_order_rhythm_scanner.py"

    # 2026-05-29 北极星修复 [H3-style]：审核必须以【作者风格档】为基线，而非写死通用爽文阈值。
    # 作者风格.json 存在即给 validate_style 传 --style，激活已有但从未触发的 _apply_style_overrides
    # （dialogue/para_mean/chapter_words/onomatopoeia 等 advisory 数值阈按作者基线放宽，不碰 hard_gate）。
    _style_json = project_root / "_数据库" / "作者风格.json"
    _style_args = ["--style", str(_style_json)] if _style_json.exists() else []

    # 每个任务：(name, cmd, ok_set, parse_fn)
    tasks = [
        ("validate_chapter",
         [child_python(), str(vc), str(project_root), str(ch), "--json"],
         {0, 1, 2},
         lambda out, code: _parse_validate_chapter(out, code)),
        ("validate_style",
         [child_python(), str(vs), str(body_file), "--strict"] + _style_args,
         {0, 1},
         lambda out, code: _parse_validate_style(out)),
        ("narrative_scanner",
         [child_python(), str(ns), str(project_root), str(ch), "--all"],
         {0, 1, 2},
         lambda out, code: _parse_scanner_json(out, "narrative", NARRATIVE_DIM)),
        ("plot_structure_scanner",
         [child_python(), str(ps), str(project_root), str(ch), "--all"],
         {0, 1, 2},
         lambda out, code: _parse_scanner_json(out, "plot", PLOT_DIM)),
        ("hook_strength_scanner",
         [child_python(), str(hs), str(project_root), str(ch)],
         {0, 1},
         lambda out, code: _parse_flat_fscanner(out, "hook_strength")),
        ("golden_three_scanner",
         [child_python(), str(gt), str(project_root), str(ch)],
         {0, 1},
         lambda out, code: _parse_flat_fscanner(out, "golden_three")),
        ("semantic_slop_scanner",
         [child_python(), str(ss), str(project_root), str(ch), "--all"],
         {0, 1, 2},
         lambda out, code: _parse_scanner_json(out, "semantic", SEMANTIC_DIM)),
        ("narrative_short_sentence_scanner",
         [child_python(), str(nsss), str(body_file)],
         {0, 1},
         lambda out, code: _parse_violations_scanner(
             out, "narrative_short_sentence_scanner",
             "NARRATIVE_SHORT_SENTENCE_OVERUSE", "风格")),
        ("repeat_noun_density_scanner",
         [child_python(), str(rnds), str(body_file)],
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
                 [child_python(), str(csvd), str(project_root), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_cross_scene_voice_drift(out)),
                ("foreshadowing_handoff",
                 [child_python(), str(fhs), str(project_root), cluster_id_full],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "foreshadowing_handoff_scanner", "剧情")),
                ("locked_fact_cross_scene",
                 [child_python(), str(lfcs), str(project_root), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_locked_fact_cross_scene(out)),
                ("pov_consistency",
                 [child_python(), str(povs), str(project_root), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "pov_consistency_scanner", "视角")),
                # 「输入教了输出要查」闭环：衔接手法分布 vs 作者蒸馏档对账（advisory · 永不 hard_gate）。
                # 传 --style 走作者基线 + --project 兜底定位 作者风格_FINAL.json；SEAM_SCANNER_MODE 默认 active。
                ("scene_seam",
                 [child_python(), str(sseam), str(cluster_draft),
                  "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_scene_seam(out)),
                # [2026-06-03] 修辞复读三连指纹 · cluster 视野 · advisory（兜底 gen_writer 元anti-slop·弱模型守不住的客观检测闭环）
                ("rhetoric_repetition",
                 [child_python(), str(rrs), str(cluster_draft),
                  "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "rhetoric_repetition_scanner", "RHETORIC_REPETITION", "风格")),
                # [2026-06-03] 句法节奏/流水账作文感 · 作者基线第一权威(传 --project 读作者档+人物卡) · advisory
                ("prose_rhythm",
                 [child_python(), str(prs), str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "prose_rhythm_scanner", "PROSE_RHYTHM", "风格")),
                # [2026-06-16 盲区落地·perplexity_obsolete] 句法骨架同形复用 + 主题过度解释 · 作者自适应
                # per-scene z-band(无作者档退绝对地板·补 memory feedback_inverted_modifier 同语法骨架盲区) · advisory
                ("syntactic_diversity",
                 [child_python(), str(sds), str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "syntactic_diversity_scanner", "SYNTACTIC_DIVERSITY", "风格")),
                # [2026-06-13 阶段1] 叙事节奏序列 · 作者基线第一权威(传 --project 读作者档) · advisory
                ("narrative_rhythm",
                 [child_python(), str(nrs), str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "narrative_rhythm_scanner", "NARRATIVE_RHYTHM", "风格")),
                # [2026-06-15 记忆调研W3] 情绪曲线 live 回查 · actual valence 曲线 vs manifest 注入
                # target（emotion_curve_full）· Pearson 趋势相关 · advisory · EMOTION_RESCAN_MODE 默认 shadow
                ("emotion_curve_rescan",
                 [child_python(), str(ecrs), str(cluster_draft),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "emotion_curve_rescan_scanner", "EMOTION_CURVE_RESCAN_DRIFT", "风格")),
                # [2026-06-15 记忆调研W1] 潜台词/on-the-nose 情绪直陈回查 · 说透情绪密度（金标准阈值）
                # · advisory · SUBTEXT_RESCAN_MODE 默认 active（2026-06-16 金标准 6 作者零误报放量）· 与 semantic_slop 主题大词正交
                ("subtext_rescan",
                 [child_python(), str(srss), str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "subtext_rescan_scanner", "ON_THE_NOSE_EMOTION_DENSITY", "风格")),
                # [2026-06-15 记忆调研W4] dramatic irony 信号回查 · 显式标志词 tell 过多（好作者用 show）
                # · advisory · DRAMATIC_IRONY_MODE 默认 active（2026-06-16 金标准 6 作者零误报放量）· 金标准阈值防误伤
                ("dramatic_irony",
                 [child_python(), str(dis), str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "dramatic_irony_scanner", "DRAMATIC_IRONY_DRIFT", "风格")),
                # [2026-06-15 记忆调研W5] 反转揭底 tell 回查 · 揭底显式标志词 tell 过多（好作者用 show）
                # · advisory · REVEAL_SHOW_MODE 默认 active（2026-06-16 金标准 6 作者零误报放量）· 金标准阈值防误伤
                ("reveal_show",
                 [child_python(), str(rvss), str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "reveal_show_scanner", "REVEAL_TELL_OVERUSE", "风格")),
                # [2026-06-16 穷尽核查#2] 情绪标点综合密度 vs 作者基线 · advisory · EMOTIONAL_PUNCT_MODE 默认 shadow
                # （金标准证单类必误报真作者冷静段→用综合 + FLOOR_RATIO 0.3·检测力待 gen-model 草稿验证再 active）
                ("emotional_punctuation",
                 [child_python(), str(eps), str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "emotional_punctuation_scanner", "EMOTIONAL_PUNCT_SPARSE", "风格")),
                # [2026-06-16 第二轮穷尽核查#2] 功能词指纹偏离 · advisory · FUNCTION_WORD_FINGERPRINT_MODE 默认 shadow
                # （SFS 最高权重维写作时 0 回查·金标准综合 15 词 cluster/base min0.86→FLOOR0.6·检测力待 gen-model 验证再 active）
                ("function_word_fingerprint",
                 [child_python(), str(fwfs), str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "function_word_fingerprint_scanner", "FUNCTION_WORD_FINGERPRINT_DRIFT", "风格")),
                # [2026-06-16 第二轮穷尽核查#3] 二阶句长节奏 · advisory · SECOND_ORDER_RHYTHM_MODE 默认 shadow
                # （探针7 permutation-invariant 看不见时序·Δ²var/var order-sensitive 正交补盲·DivEye·var=0→None 解耦探针7·通用 floor 不传 --project）
                ("second_order_rhythm",
                 [child_python(), str(sors), str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "second_order_rhythm_scanner", "SECOND_ORDER_RHYTHM_FLAT", "风格")),
                # [2026-06-19 联网调研] 过早消解冲突(LLM第一弱点·arXiv:2604.09854)
                # · 冲突→消解距离<500CJK = 快速消解 · ratio>50%报 · advisory · 默认 shadow
                ("premature_resolution",
                 [child_python(), str(_SCRIPT_DIR / "premature_resolution_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "premature_resolution_scanner", "PREMATURE_RESOLUTION", "张力")),
                # [2026-06-19 R1 联网调研] 白房间综合症/欠写检测(全系统首个查『欠写』·Turkey City)
                # · 场景开头缺空间/感官接地锚点 · advisory · SCENE_GROUNDING_MODE 默认 shadow
                ("scene_grounding",
                 [child_python(), str(_SCRIPT_DIR / "scene_grounding_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "scene_grounding_scanner", "SCENE_GROUNDING_THIN", "风格")),
                # [2026-06-19 R1·arXiv:2605.07102 SAGE/Cohn] 内心戏三态失衡·带标记直接独白过密(建议转FID)
                # · advisory · INTERIORITY_MODE_BALANCE_MODE 默认 shadow
                ("interiority_mode_balance",
                 [child_python(), str(_SCRIPT_DIR / "interiority_mode_balance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "interiority_mode_balance_scanner", "INTERIORITY_MODE_IMBALANCE", "风格")),
                # [2026-06-19 R1·SAGE Emotional Granularity] 情绪颗粒度粗·四大类粗情绪大词裸词频(与subtext_rescan正交)
                # · advisory · EMOTION_GRANULARITY_MODE 默认 shadow
                ("emotion_granularity",
                 [child_python(), str(_SCRIPT_DIR / "emotion_granularity_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "emotion_granularity_scanner", "EMOTION_GRANULARITY_COARSE", "风格")),
                # [2026-06-19 R1·arXiv:2509.19595 ELENA] 生理情绪线索面部偏置·对抗LLM facial bias
                # · advisory · PHYSIO_CUE_DIVERSITY_MODE 默认 shadow
                ("physio_cue_diversity",
                 [child_python(), str(_SCRIPT_DIR / "physio_cue_diversity_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "physio_cue_diversity_scanner", "PHYSIO_CUE_FACIAL_BIAS", "风格")),
                # [2026-06-19 R1·arXiv:2603.04969 MPCEval] 群戏对话失衡·显式点名过密(建议隐式指称)
                # · advisory · GROUP_DIALOGUE_BALANCE_MODE 默认 shadow
                ("group_dialogue_balance",
                 [child_python(), str(_SCRIPT_DIR / "group_dialogue_balance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "group_dialogue_balance_scanner", "GROUP_DIALOGUE_IMBALANCE", "风格")),
                # [2026-06-20 R2·会话分析 PMC8504554] 非偏好回应裸拒绝 · advisory · 默认 shadow(真作者裸拒常态·潜在误报)
                ("dispreferred_turn_shape",
                 [child_python(), str(_SCRIPT_DIR / "dispreferred_turn_shape_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "dispreferred_turn_shape_scanner", "DISPREFERRED_TURN_BARE", "风格")),
                # [2026-06-20 R1·真编辑实证] 时间流逝感缺失 · advisory · 默认 active(5真作者thin_ratio全0.0)
                ("temporal_grounding",
                 [child_python(), str(_SCRIPT_DIR / "temporal_grounding_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "temporal_grounding_scanner", "TEMPORAL_GROUNDING_THIN", "风格")),
                # [2026-06-20 R2·arXiv:2110.09710 Inter-Sense] 通感过用 · advisory · 默认 active(5真作者per_1k全0)
                ("synesthesia_density",
                 [child_python(), str(_SCRIPT_DIR / "synesthesia_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "synesthesia_density_scanner", "SYNESTHESIA_OVERUSE", "风格")),
                # [2026-06-20 R6 联网调研] 时代错位/穿帮(古代/古言/仙侠/古风最大题材群·零覆盖)·era门控(世界观.json·无→skip)·advisory·默认shadow
                ("anachronism",
                 [child_python(), str(_SCRIPT_DIR / "anachronism_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "anachronism_scanner", "ANACHRONISM_DETECTED", "风格")),
                # [2026-06-20 R8 W4 Batch-F · L18 Le Guin Register Drift] 题材语域漂移
                # (当代俚语/工程黑话漂入高语域 + 反向高语域古风词漂入现代场景)·5 tier 词典
                # (epic_fantasy/xianxia/xuanhuan/historical/modern_urban)·tier 门控
                # (genre_packs.register_tier · 无→skip)·voice_pack.allow_register_drift 豁免
                # ·与 R6 anachronism 时代轴正交·advisory·默认 shadow
                ("world_register_drift",
                 [child_python(), str(_SCRIPT_DIR / "world_register_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "world_register_drift_scanner", "REGISTER_DRIFT", "风格")),
                # [2026-06-20 R7 联网调研·番茄爆款规则怪谈] 规则块字面歧义/陷阱条款比·genre 门控(rule_anomaly·非则 skip)·advisory·默认 shadow
                ("rule_text_ambiguity",
                 [child_python(), str(_SCRIPT_DIR / "rule_text_ambiguity_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "rule_text_ambiguity_scanner", "RULE_TEXT_AMBIGUITY_LOW", "风格")),
                # [2026-06-20 R7 联网调研·DiLouie/末世生存] 资源稀缺账本·genre 门控(apocalypse_survival·非则 skip)·advisory·默认 shadow
                ("resource_ledger",
                 [child_python(), str(_SCRIPT_DIR / "resource_ledger_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "resource_ledger_scanner", "RESOURCE_LEDGER_THIN", "风格")),
                # [2026-06-20 R7 联网调研·Stanzel/Cohn] 第一人称回溯 hindsight 签到·narrative_pov_mode 门控(first_retro_*·非则 skip)·与 future_knowledge_leak 显式去重·advisory·默认 shadow
                ("firstperson_retro_self_gap",
                 [child_python(), str(_SCRIPT_DIR / "firstperson_retro_self_gap_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "firstperson_retro_self_gap_scanner", "FIRSTPERSON_RETRO_HINDSIGHT_THIN", "风格")),
                # [2026-06-20 R7 W2·Bal/FocalLens] 聚焦人感知边界违例(自体不可见/他人内心/空间不在场)
                # · 与 R6 pov_consistency 正交去重 · advisory · 默认 shadow
                ("focalizer_perception_bounds",
                 [child_python(), str(_SCRIPT_DIR / "focalizer_perception_bounds_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "focalizer_perception_bounds_scanner", "FOCALIZER_PERCEPTION_OUT_OF_BOUNDS", "结构")),
                # [2026-06-20 R7 W2·safety-alignment] 反派 substitution 扁平化(冷哼/狂笑/嗤笑 anti-pattern)
                # · 建议 voice_pack.moral_level + manipulation_signature · advisory · 默认 shadow
                ("antagonist_fidelity",
                 [child_python(), str(_SCRIPT_DIR / "antagonist_fidelity_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "antagonist_fidelity_scanner", "ANTAGONIST_FIDELITY_FLAT", "剧情")),
                # [2026-06-20 R8 W4 Batch-G·L19 Phelan 6 轴 × TUNa 4 原型] 不可靠叙述 + 8 类 verbal_tic 密度
                # · unreliable_narrator_profile 门控(reliable=1.0 → skip)·与 firstperson_retro 正交
                # · advisory · 默认 shadow
                ("unreliable_narrator_typology",
                 [child_python(), str(_SCRIPT_DIR / "unreliable_narrator_typology_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "unreliable_narrator_typology_scanner",
                     "UNRELIABLE_NARRATOR_SIGNAL_THIN", "风格")),
                # [2026-06-20 R8 W4 Batch-G·L21 Gricean flouting] 对话四准则 flouting 潜台词密度
                # · 四子检测器 Quality/Quantity/Relation/Manner·作者档 dialogue_flouting_profile 优先
                # · 与 R6 OIR + D2 延迟解码正交·advisory·默认 shadow
                ("gricean_flouting_density",
                 [child_python(), str(_SCRIPT_DIR / "gricean_flouting_density.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "gricean_flouting_density",
                     "GRICEAN_FLOUTING_THIN", "风格")),
                # [2026-06-20 R8 W4 Batch-H·L22 Genette 五型时长比] scene/summary/ellipsis/pause/stretch 占比
                # · 作者档 duration_mix_baseline 第一权威·无作者档走通用兜底 (ellipsis<1%+stretch<0.5%)
                # · advisory · 默认 shadow
                ("duration_mix",
                 [child_python(), str(_SCRIPT_DIR / "duration_mix_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "duration_mix_scanner", "DURATION_MIX_DRIFT", "风格")),
                # [2026-06-20 R8 W4 Batch-H·L23 Shklovsky 先体感后命名] 新世界元素首现是否带感官锚
                # · manifest.first_encounter_targets 优先·世界观.json entries fallback
                # · LitRPG/horror_game/rule_anomaly 题材豁免·advisory·默认 shadow
                ("first_encounter_anchor",
                 [child_python(), str(_SCRIPT_DIR / "first_encounter_anchor_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "first_encounter_anchor_scanner",
                     "FIRST_ENCOUNTER_LABEL_FIRST", "风格")),
                # [2026-06-20 R8 W4 Batch-I·L25 Pier Metalepsis LHN 2014 + 马良系统流] 元叙事越界预算
                # · 作者档/genre pack metalepsis_budget 门控 (无 → skip)·type=none/rhetorical/
                # ontological/mixed·ontological 窗口闭合检测·与 L28 narratee 关联防双计
                # · advisory · 默认 shadow
                ("metalepsis_budget",
                 [child_python(), str(_SCRIPT_DIR / "metalepsis_budget_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "metalepsis_budget_scanner", "METALEPSIS_BUDGET_DRIFT", "风格")),
                # [2026-06-20 R8 W4 Batch-I·L26 Project MUSE Mimesis and 興·朱熹比兴·SCIRP 2017]
                # 起兴 scene-opener 检测·新场景前 60-150 字外部环境意象不点情绪
                # · 作者档 scene_opener_profile.xing_ratio 基线·现代都市/职场题材天然豁免
                # · advisory · 默认 shadow
                ("scene_opener_xing",
                 [child_python(), str(_SCRIPT_DIR / "scene_opener_xing_check.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "scene_opener_xing_check", "SCENE_OPENER_XING_THIN", "风格")),
                # [2026-06-20 R8 W4 Batch-I·L27 Nature Sci Rep 2025 EC/PD·Keen Theory of Narrative
                # Empathy] 苦难场景 Empathic Concern vs Personal Distress 二相平衡 (仅
                # suffering/grief/sacrifice/torment/desperation 触发)·ec_pd_ratio<0.4 advisory
                # · 与 R7 Nummenmaa body map 协同 (独立维度)·默认 shadow
                ("empathic_concern_distress",
                 [child_python(), str(_SCRIPT_DIR / "empathic_concern_distress_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "empathic_concern_distress_scanner",
                     "EMPATHIC_CONCERN_DISTRESS_IMBALANCE", "风格")),
                # [2026-06-20 R8 W4 Batch-I·L28 Booth Rhetoric of Irony stable irony 4 步
                # + Tang arXiv:2209.04712] Discordance 4-cue 反讽信号 (saying_doing/
                # style_fact/world_clash/value_clash)·作者档 ironic_voice_profile.stable_irony
                # 第一权威·advisory · 默认 shadow
                ("discordance_signal",
                 [child_python(), str(_SCRIPT_DIR / "discordance_signal_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "discordance_signal_scanner",
                     "DISCORDANCE_SIGNAL_THIN", "风格")),
                # [2026-06-20 R8 W4 Batch-I·L28 Phelan Ideal Narratee Poetics Today 2022]
                # narratee 称谓一致性 (元小说/破壁叙述)·作者档 narratee_registry.primary 门控
                # · 与 L25 metalepsis 关联防双计·advisory · 默认 shadow
                ("narratee_address",
                 [child_python(), str(_SCRIPT_DIR / "narratee_address_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "narratee_address_scanner", "NARRATEE_DRIFT", "风格")),
                # [2026-06-20 R8 W4 Batch-J·L29 LHN Genette Narrative Levels + BookishBay
                # Mise en Abyme + DMovies Rashomon] Frame-Tale 嵌套叙事一致性
                # · 作者档 nested_narrative_profile 门控 / scheming_politics/regression/
                # espionage 默认启用 · advisory · 默认 shadow
                ("frame_tale_consistency",
                 [child_python(), str(_SCRIPT_DIR / "frame_tale_consistency_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "frame_tale_consistency_scanner", "FRAME_TALE_DRIFT", "结构")),
                # [2026-06-20 R8 W4 Batch-J·L30 Schegloff Sequence Organization 2007 +
                # 起点男频试探/谈判三五步扩展] CA Adjacency Pair 扩展密度
                # · CN 触发词表 pre/insert/post · 与 R6 OIR 正交 · advisory · 默认 shadow
                ("dialogue_sequence_expansion",
                 [child_python(), str(_SCRIPT_DIR / "dialogue_sequence_expansion.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "dialogue_sequence_expansion",
                     "DIALOGUE_SEQUENCE_EXPANSION_THIN", "风格")),
                # [2026-06-20 R8 W4 Batch-J·L31 Heldner & Edlund pause/gap/lapse +
                # RB Kelly Power of Pauses] 沉默/停顿/失语三档密度
                # · 词表 within-turn/gap/lapse · 情绪上下文匹配 · advisory · 默认 shadow
                ("dialogue_silence_density",
                 [child_python(), str(_SCRIPT_DIR / "dialogue_silence_density.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "dialogue_silence_density",
                     "SILENCE_DENSITY_THIN", "风格")),
                # [2026-06-20 R8 W4 Batch-J·L32 Hanwen Shen arXiv:2505.12572 Optimal
                # Expansion + LongEval arXiv:2502.19103] Genette 扩写率守门
                # · 作者档 expansion_ratio_baseline z-band · 通用兜底 4-60 · 默认 shadow
                ("expansion_ratio_gate",
                 [child_python(), str(_SCRIPT_DIR / "expansion_ratio_gate.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "expansion_ratio_gate",
                     "EXPANSION_RATIO_DRIFT", "结构")),
                # [2026-06-20 R8 W4 Batch-J·L35 知乎拆 30+本爆款 2025 番茄 +
                # WebNovelBench arXiv:2505.14818] 开篇 3k/10k 里程碑 (仅 cluster_001 激活)
                # · M1 ambiguity_hook + M2 core_stake · 严肃文学/IP 改编 override · 默认 shadow
                ("opening_window_milestone",
                 [child_python(), str(_SCRIPT_DIR / "opening_window_milestone_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "opening_window_milestone_scanner",
                     "OPENING_WINDOW_MILESTONE_THIN", "结构")),
                # [2026-06-20 R8 W4 Batch-J·L36 Jo Walton Reactor SF Reading Protocols
                # incluing + AlphaLexChinese] 世界术语首现 Gini + lexical density 突变
                # · 世界观.json 术语词表 · 硬科幻/LitRPG override 0.65 · 默认 shadow
                ("world_term_seepage",
                 [child_python(), str(_SCRIPT_DIR / "world_term_seepage_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "world_term_seepage_scanner",
                     "WORLD_TERM_INFO_DUMP", "风格")),
                # [2026-06-20 R9 W5 Batch-K·L38 Genette 时序 order 维度] analepsis 五分类 +
                # prolepsis · 与 R6 anachronism(时代错位) + R8 duration_mix 正交 · 作者档
                # anachrony_baseline 第一权威·通用兜底·advisory · 默认 shadow
                ("anachrony_order",
                 [child_python(), str(_SCRIPT_DIR / "anachrony_order_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "anachrony_order_scanner",
                     "ANACHRONY_ORDER_THIN", "结构")),
                # [2026-06-20 R9 W5 Batch-K·L39 Genette frequency 三态] iterative/singulative/
                # repetitive · xianxia/cultivation/training_arc/slice_of_life 题材尤需 montage
                # · 作者档 frequency_baseline 第一权威 · advisory · 默认 shadow
                ("narrative_frequency",
                 [child_python(), str(_SCRIPT_DIR / "narrative_frequency_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "narrative_frequency_scanner",
                     "NARRATIVE_FREQUENCY_FLAT", "结构")),
                # [2026-06-20 R9 W5 Batch-K·Burrows-Δ/Craig-Zeta 字符 3-gram bootstrap]
                # 跨角色 idiolect Gini · 与 R3/R4 同角色跨场景 voice drift 正交 · 群像题材重要
                # · 作者档 character_voice_gini_baseline 第一权威 · advisory · 默认 shadow
                ("character_distinctiveness",
                 [child_python(), str(_SCRIPT_DIR / "character_distinctiveness_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "character_distinctiveness_scanner",
                     "INTER_CHARACTER_VOICE_COLLAPSE", "风格")),
                # [2026-06-20 R9 W5 Batch-K·Aristotle deus ex machina + Narrative Debt 对偶]
                # finale cluster 触发(manifest is_volume_finale)·present-payoff→past-anchors 方向
                # · 与 R7 Narrative Debt Ledger 完全正交 · advisory · 默认 shadow
                ("deus_ex_solution_audit",
                 [child_python(), str(_SCRIPT_DIR / "deus_ex_solution_audit.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "deus_ex_solution_audit",
                     "DEUS_EX_SOLUTION", "剧情")),
                # [2026-06-20 R9 W5 Batch-L·L38 Greimas 6 actant] 角色功能漂移
                # (helper↔opponent 无 pivot / 关键位空缺 / 单角色过载)·读 manifest
                # cluster_actant_state + 历史 ledger.json·advisory · 默认 shadow
                ("actant_drift",
                 [child_python(), str(_SCRIPT_DIR / "actant_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "actant_drift_scanner", "ACTANT_DRIFT_NO_PIVOT", "结构")),
                # [2026-06-20 R9 W5 Batch-L·L39 Bremond outcome 节奏] 三段式四态分布
                # · 同型 streak / over_success / over_failure · 跨 cluster aggregator
                # · 作者档 outcome_signature.allow_no_setback 豁免 · advisory · 默认 shadow
                ("bremond_cadence",
                 [child_python(), str(_SCRIPT_DIR / "bremond_cadence_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "bremond_cadence_scanner",
                     "BREMOND_CADENCE_MONOTONE", "结构")),
                # [2026-06-20 R9 W5 Batch-L·L40 Truby cast economy] 配角经济
                # (introduce_burst / composite_hint / role_split_implicit)·
                # 群像题材(scheming_politics/heist_caper/espionage) budget override
                # · advisory · 默认 shadow
                ("cast_economy",
                 [child_python(), str(_SCRIPT_DIR / "cast_economy_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cast_economy_scanner",
                     "CAST_INTRODUCE_BURST", "结构")),
                # [2026-06-20 R9 W5 Batch-L·L40 Genette narrating distance] time-of-telling
                # vs time-told 五分级(concurrent/recent/distant/posthumous/atemporal)
                # · R7 firstperson_retro 是其 distant 子集 · advisory · 默认 shadow
                ("narrating_distance",
                 [child_python(), str(_SCRIPT_DIR / "narrating_distance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "narrating_distance_scanner",
                     "DISTANCE_FLATTENED", "风格")),
                # [2026-06-20 R9 W5 Batch-L·L38 Phelan 三轴伦理] Told/Telling/Reading
                # 三轴 implied_author_ethics_probe(asymmetric_screen / telling_intrusion /
                # narratee_address) · 与 L25/L28 正交 · advisory · 默认 shadow
                ("implied_author_ethics",
                 [child_python(), str(_SCRIPT_DIR / "implied_author_ethics_probe.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "implied_author_ethics_probe",
                     "IMPLIED_AUTHOR_SCREEN_ASYMMETRY", "结构")),
                # [2026-06-20 R9 W5 Batch-L·L40 Plutchik+LLM congeniality bias] 8 类情感
                # KL vs 作者 author_affective_signature(无→均匀兜底)·flag
                # CONGENIALITY_SKEW(joy 膨胀 + anger/disgust 塌陷)·advisory · 默认 shadow
                ("affective_signature",
                 [child_python(), str(_SCRIPT_DIR / "affective_signature_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "affective_signature_scanner",
                     "CONGENIALITY_SKEW", "风格")),
                # [2026-06-20 R9 W5 Batch-M·L41 Booth narrator intrusion + Cohn psycho-narration]
                # 全知点评/evaluative_summary 句式密度 · 三指标 (density/chapter-end share/intra-action)
                # · 作者档 narrator_voice_signature.commentary_target_per_1k 第一权威·与 R8 L25
                # metalepsis_budget 严格正交(L25 查 frame-breaking marker·L41 查 telling-weight 句式)
                # · F3 AnchoredAI · violation 携 anchor_span 供 gen_fixer 锚定段改 · advisory · 默认 shadow
                ("narrator_commentary",
                 [child_python(), str(_SCRIPT_DIR / "narrator_commentary_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "narrator_commentary_scanner",
                     "NARRATOR_COMMENTARY_OVERUSE", "风格")),
                # [2026-06-20 R9 W5 Batch-M·L38 Bakhtin Dialogic Imagination 1981 chronotope]
                # 7 型时空体 (road/threshold/castle/salon/town/square/idyll + instance_dungeon)
                # 场景分布 + cluster 级 distribution_entropy + monotony_streak (同型≥3 advisory)
                # · 作者档 chronotope_signature.allowed_monotony 豁免独角戏室内剧·与 scene_seam /
                # scene_grounding / location_signature 正交 · advisory · 默认 shadow
                ("chronotope_typology",
                 [child_python(), str(_SCRIPT_DIR / "chronotope_typology_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "chronotope_typology_scanner",
                     "CHRONOTOPE_MONOTONY", "结构")),
                # [2026-06-20 R9 W5 Batch-N P1·plot armor stakes erosion] 3-cluster 滚动窗口
                # 威胁三档(轻伤/重伤/濒死)vs 持久化代价(state_delta/factual/facts_locked)·
                # stakes_credibility<0.2 且威胁≥3 触发·题材门控(轻喜剧/slice_of_life skip)
                # · 作者档 plot_armor_profile.allow_high_armor 豁免 · advisory · 默认 shadow
                ("plot_armor_tracker",
                 [child_python(), str(_SCRIPT_DIR / "plot_armor_tracker.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "plot_armor_tracker",
                     "PLOT_ARMOR_INFLATION", "剧情")),
                # [2026-06-20 R9 W5 Batch-N P1·red herring recall-at-reveal · R2 setup 对偶]
                # 仅在 reveal/twist/climax_reveal beat 触发·读 _数据库/伏笔表.json red_herrings
                # 在草稿正文检查是否被显式否决(±60 字内 NEGATION_MARKER)·dangling → advisory
                # · 北极星② 作者未声明则 skip · advisory · 默认 shadow
                ("red_herring_recall",
                 [child_python(), str(_SCRIPT_DIR / "red_herring_recall_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "red_herring_recall_scanner",
                     "RED_HERRING_DANGLING", "剧情")),
                # [2026-06-20 R9 W5 Batch-N P1·quotative/reporting-verb per-character 签名]
                # 8 桶 60 词词典(lexicons/quotative_verbs.json) · author palette collapse(≤2 桶)
                # + per-character cosine > 0.9 同质化 · 输出 quotative_bias top-3 供 voice_pack
                # · 与 R8 L31 silence_marker 正交(那个查停顿沉默·本者查言说动作)·advisory · 默认 shadow
                ("quotative_signature",
                 [child_python(), str(_SCRIPT_DIR / "quotative_signature_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "quotative_signature_scanner",
                     "AUTHOR_QUOTATIVE_PALETTE_COLLAPSE", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L60 P0 STRONG·Sanderson 2025 + 凡人修仙传 9 阶
                # + Andrew Rowe progression fantasy] 升级流 tier 单调性/突跳/停滞
                # · 读 _数据库/角色弧线.json characters[<pid>].protagonist_power_tier
                # · 用户偏好/genre(romance/mystery)skip·advisory · 默认 shadow
                ("power_progression",
                 [child_python(), str(_SCRIPT_DIR / "power_progression_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "power_progression_scanner",
                     "POWER_TIER_REGRESSION", "结构")),
                # [2026-06-20 R10 W6 Batch-O·L60 P1·百度百科章回体 + ACL 2024 NLP4DH 对偶]
                # 回目 huimu 对仗·门控作者档 huimu_couplet/title_form==huimu_couplet
                # · advisory · 默认 shadow
                ("chapter_title_couplet",
                 [child_python(), str(_SCRIPT_DIR / "chapter_title_couplet_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "chapter_title_couplet_scanner",
                     "ZHANGHUI_HUIMU_PARALLELISM_BROKEN", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L60 P1·Literariness pinghua 楔子]
                # 楔子 kernel symbol 末卷召回 · 门控 huaben_zhanghui_pastiche · advisory · 默认 shadow
                ("xiezi_kernel_recall",
                 [child_python(), str(_SCRIPT_DIR / "xiezi_kernel_recall_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "xiezi_kernel_recall_scanner",
                     "XIEZI_KERNEL_NOT_RECALLED", "结构")),
                # [2026-06-20 R10 W6 Batch-O·L60 P1·Cohn Transparent Minds] Cohn 意识表征四模式
                # · 作者档 cohn_mode_signature 第一权威 · 2σ 偏离 advisory · 默认 shadow
                ("cohn_consciousness_mode",
                 [child_python(), str(_SCRIPT_DIR / "cohn_consciousness_mode_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cohn_consciousness_mode_scanner",
                     "COHN_MODE_DRIFT", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L61 P1·arXiv 2312.00100 中文 parallelism]
                # 排比/反复密度 · 四子 metric(anaphora/epistrophe/parallel_clause/polysyndeton)
                # · 作者档 author_rhetoric_parallel_signature 基线 · advisory · 默认 shadow
                ("rhetoric_parallel",
                 [child_python(), str(_SCRIPT_DIR / "rhetoric_parallel_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "rhetoric_parallel_scanner",
                     "RHETORIC_PARALLEL_GAP", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L59 P1·TVTropes RotatingArcs + 吞噬星空 + Sanderson]
                # 反派轮替节奏(长篇 1000+)· 读 _数据库/反派轮替.json append-only ledger
                # · 四 advisory(空窗/tier 不升/motive 同类/power 同类)· 默认 shadow
                ("antagonist_rotation",
                 [child_python(), str(_SCRIPT_DIR / "antagonist_rotation_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "antagonist_rotation_scanner",
                     "ANTAGONIST_ROTATION_VOID", "剧情")),
                # [2026-06-20 R10 W6 Batch-O·L49 P1·Litreactor Chorus + 弹幕 + 朝臣议论]
                # 群口段/弹幕式集体反应块 · 复数集合名词说话人 + 匿名引号串 ≥3 句聚簇
                # · 作者档 author_mass_reactor_baseline.density_target 校准 · 默认 shadow
                ("mass_reactor",
                 [child_python(), str(_SCRIPT_DIR / "mass_reactor_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "mass_reactor_scanner",
                     "MASS_REACTOR_DENSITY_DRIFT", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L58 P1·Atlantis Press ICOLLITE + ANLP 2024]
                # 拟声/拟态/拟情 mimetic 三类密度+形态分布
                # · genre 门控{anime_isekai/xianxia_battle/fantasy_combat/litrpg/xianxia/xuanhuan}
                # · 默认 shadow
                ("onomatopoeia_density",
                 [child_python(), str(_SCRIPT_DIR / "onomatopoeia_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "onomatopoeia_density_scanner",
                     "MIMETIC_DENSITY_DRIFT", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L59 P1·World Anvil LitRPG Storyteller's Guide]
                # LitRPG 状态框/系统提示密度甜区 · genre 门控
                # {litrpg/system_isekai/game_anime/horror_game/rule_anomaly}
                # · 作者档 0 反向 advisory · 默认 shadow
                ("status_block_density",
                 [child_python(), str(_SCRIPT_DIR / "status_block_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "status_block_density_scanner",
                     "STATUS_BLOCK_DENSITY_DRIFT", "风格")),
                # [2026-06-20 R10 W6 Batch-O·L58 P1·Oxford ORA + arXiv 2001.01863 + Dale-Chall]
                # 童声 concrete_noun_ratio + 词性指纹 · 门控 pov_age<18 / genre∈{campus/childhood}
                # · 默认 shadow
                ("prose_child_voice",
                 [child_python(), str(_SCRIPT_DIR / "prose_child_voice_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "prose_child_voice_scanner",
                     "CHILD_VOICE_REGISTER_DRIFT", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P0 STRONG·NeurIPS 2025 LLM Lifecycle Workshop
                # arXiv 2510.18932 + EMNLP 2022 arXiv 2211.00676] 关系签名图 + LLM 抱团正向
                # bias 哨兵·作者档 signed_graph_baseline z-band·advisory·默认 shadow
                ("signed_relation_graph",
                 [child_python(), str(_SCRIPT_DIR / "signed_relation_graph_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "signed_relation_graph_scanner",
                     "SIGNED_GRAPH_OVERLY_COZY", "剧情")),
                # [2026-06-20 R11 W6 Batch-P·P0 STRONG·Microsoft Research arXiv 2603.05890
                # ConStory-Bench 2026-03] 一致性错误三联分诊带·熵代理+中段窗口+共现 hotspot
                # · 严禁升 hard_gate 或累加扣分(单条聚合)·advisory·默认 shadow
                ("consistency_error_triage_band",
                 [child_python(), str(_SCRIPT_DIR / "consistency_error_triage_band.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "consistency_error_triage_band",
                     "CONSISTENCY_HOTSPOT_COOCCURRENCE", "结构")),
                # [2026-06-20 R11 W6 Batch-P·P1 MODEST·EMNLP 2025 arXiv 2507.12260 T-index
                # + NAACL-W 2018 arXiv 1804.08756 + 余光中《论的的不休》] 译文味 4 桶 advisory
                # · de_stack_depth / bei_passive / pre_modifier_long / name_overrepetition
                # · 4 桶 z-score vs 作者档 translationese_baseline·shadow
                ("translationese_residual",
                 [child_python(), str(_SCRIPT_DIR / "translationese_residual_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "translationese_residual_scanner",
                     "TRANSLATIONESE_RESIDUAL", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P1 MODEST·题材主副 pack 标记密度比漂移]
                # marker_lexicon.json 17 pack seed·fusion_declaration 主副比·三 advisory
                # GENRE_DOMINANCE_INVERSION / GENRE_PRIMARY_STARVED / GENRE_BLEND_FLAT
                # · shadow
                ("genre_dominance",
                 [child_python(), str(_SCRIPT_DIR / "genre_dominance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "genre_dominance_scanner",
                     "GENRE_DOMINANCE_INVERSION", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P1 MODEST 占位·Scribble Hub primary/subordinate
                # + Countercraft Age of Genre Bending] pack 间 trope clash registry 两端峰值
                # · trope_clash_registry.json 10 seed pair·load_clash_registry 注入 manifest
                # · advisory CLASH_UNRESOLVED·shadow
                ("genre_pack_clash",
                 [child_python(), str(_SCRIPT_DIR / "genre_pack_clash_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "genre_pack_clash_scanner",
                     "CLASH_UNRESOLVED", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P1 MODEST·EMNLP 2025 Stanford/UCSD Chengyu-Bench
                # arXiv 2506.18105 + arXiv 2510.27045 + Thomas 1986 6 类] 三槽典故密度 +
                # 三读者承重测试·core/data/allusion_seed_zh.json seed·LOAD_BEARING_ALLUSION_NO_GLOSS
                # · shadow
                ("allusion_ledger",
                 [child_python(), str(_SCRIPT_DIR / "allusion_ledger_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "allusion_ledger_scanner",
                     "LOAD_BEARING_ALLUSION_NO_GLOSS", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P2 MODEST·Sebald + Kramer + 报告文学] 文档单元插入
                # · 题材门控(reportage/documentary/literary_journalism/historical_nonfiction_novel
                # /nonfiction_documentary_lit)·shadow
                ("paratext_interpolation",
                 [child_python(), str(_SCRIPT_DIR / "paratext_interpolation_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "paratext_interpolation_scanner",
                     "PARATEXT_INTERPOLATION_THIN", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P2 MODEST·Kramer/Wolfe] 信息源 5 桶出处分布
                # · direct/paraphrase/archived/reconstructed/inferred·题材门控同 paratext
                # · 与 R9 quotative 8 桶(词法)正交·shadow
                ("attribution_mode",
                 [child_python(), str(_SCRIPT_DIR / "attribution_mode_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "attribution_mode_scanner",
                     "ATTRIBUTION_MODE_MONOTONE", "风格")),
                # [2026-06-20 R11 W6 Batch-P·P2 MODEST·Quéré&Matias 2025 Nature Sci Rep] 章节
                # 标题具象度曲线带·作者档 chapter_title_profile.concreteness_ecdf 第一权威
                # · 作者档未规定该维则静默·shadow
                ("chapter_title_concreteness",
                 [child_python(), str(_SCRIPT_DIR / "chapter_title_concreteness_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "chapter_title_concreteness_scanner",
                     "TITLE_CONCRETENESS_DRIFT", "风格")),
                # [2026-06-20 R12 W6 Batch-Q·P2·CFPG arxiv 2601.07033 + Farland reread test]
                # 隐显伏笔 delivery_mode 占比 advisory·作者档 author_covert_ratio_baseline
                # 第一权威·无作者档兜底 [0.40, 0.70]·advisory·默认 shadow
                ("covert_foreshadowing",
                 [child_python(), str(_SCRIPT_DIR / "covert_foreshadowing_audit.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "covert_foreshadowing_audit",
                     "COVERT_FORESHADOWING_THIN", "伏笔")),
                # [2026-06-20 R12 W6 Batch-Q·P2·ConStory-Bench arxiv 2603.05890]
                # 能力/技艺首现无 acquisition 锚点·capability_ledger.json 驱动·
                # inherent=true 自动豁免·advisory·默认 shadow
                ("capability_emergence",
                 [child_python(), str(_SCRIPT_DIR / "capability_emergence_audit.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "capability_emergence_audit",
                     "CAPABILITY_EMERGENCE_UNGROUNDED", "剧情")),
                # [2026-06-20 R12 W6 Batch-Q·P2·Reeve WordNet + MWA chiaroscuro]
                # 光暗意象比·core/data/luminance_lexicon_cn.json (光/暗各 40+ 词)
                # 作者档 luminance_signature.ratio_p50 第一权威·advisory·默认 shadow
                ("chiaroscuro",
                 [child_python(), str(_SCRIPT_DIR / "chiaroscuro_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "chiaroscuro_scanner", "OVER_BRIGHT", "风格")),
                # [2026-06-20 R12 W6 Batch-Q·P2·Hsu 2022 salience-contrast +
                # Lost in Pronunciation arxiv 2507.07640] 谐音双关 salience-contrast
                # 占位·4 字滑窗 + ±150 字 context noun 支撑·genre-conditioned ECDF
                # (xianxia/comedy/urban_supernatural 兜底·硬科幻 skip)·advisory·默认 shadow
                ("homophonic_pun",
                 [child_python(), str(_SCRIPT_DIR / "prose_homophonic_pun_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "prose_homophonic_pun_scanner",
                     "HOMOPHONIC_PUN_THIN", "风格")),
                # [2026-06-20 R12 W6 Batch-Q·P2·Brill Chinese Character Manipulation +
                # kfcd/chaizi + Kelly 金瓶梅 chaizi] 拆字/字谜 glyphic-decomposition
                # 6 模板 + 占位字典 + 5 功能桶(prophecy/name_pun/secret_msg/divination/joke)
                # genre-gated(玄幻/仙侠/历史/古风/谍战)·advisory·默认 shadow
                ("chaizi_ledger",
                 [child_python(), str(_SCRIPT_DIR / "prose_chaizi_ledger.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "prose_chaizi_ledger",
                     "CHAIZI_DENSITY_THIN", "风格")),
                # [2026-06-20 R13 W6 Batch-R·P1 STRONG·Kopytoff 1986 物件文化传记 + Bill Brown 2003 +
                # Penn Museum object biography 指南 + Heritage Studies 2023] 物件生命传记相位账本·
                # 8 相位标签(acquired/in_use/transformed/damaged/lost/recovered/discarded/reentered)·
                # named_objects 门槛(物件登记表 plot_critical=true 或 mentions>=2)·缺登记表则草稿候选·
                # 4 信号(phase_skip_rate/phase_dwell_imbalance/phase_silence_gap/terminal_phase_consistency)·
                # snapshot 写盘供 cluster_emergence_engine 下卷读·与 R8 motif/R10 power_progression/R6
                # anachronism/R11 signed_relation 全部正交·advisory·默认 shadow·绝不 hard_gate
                ("object_biography",
                 [child_python(), str(_SCRIPT_DIR / "object_biography_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "object_biography_scanner",
                     "OBJECT_BIOGRAPHY_THIN", "剧情")),
                # [2026-06-20 R13 W6 Batch-R·P1 STRONG·首次落地·Li 2014 Studies in Language 38:1 +
                # Xiao&McEnery 2004 Benjamins corpus + perfective paradox-guo + Zai/Zhe 构式语法]
                # 中文体貌前景-背景密度·4 信号(bare_le_unbounded_streak/background_marker_ratio/
                # prospective_overuse/guo_experiential_misuse)·作者档 aspect_baseline 第一权威·
                # 无作者档兜底 band·与 R7 prose_rhythm/R8 duration_mix/R9 anachrony_order/R12
                # narrative_frequency 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("aspect_grounding",
                 [child_python(), str(_SCRIPT_DIR / "aspect_grounding_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "aspect_grounding_scanner",
                     "ASPECT_GROUNDING_THIN", "风格")),
                # [2026-06-21 R18 W7 Batch-S·P0·arxiv 2605.26322 OmniToM 2026-05-25
                # + arxiv 2506.13641 EvolvTrip + arxiv 2601.12410 LLM-vs-Chimps] 角色信念
                # 账本(OmniToM 7 维)·按 storyboard 维护 belief_state[character]·末轮回查
                # 越权知识(character + KNOWLEDGE_VERB + fact_ref 在该 char belief 之外)·
                # 与 focalizer_perception_bounds(narrator 层)+ dramatic_irony(TELL 词)+
                # locked_fact_cross_scene(恒定事实)显式去重·advisory·默认 shadow
                ("character_belief_ledger",
                 [child_python(), str(_SCRIPT_DIR / "character_belief_ledger_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "character_belief_ledger_scanner",
                     "CHARACTER_KNOWLEDGE_LEAK", "结构")),
                # [2026-06-21 R18 W7 Batch-S·P0·tomenovel cliffhanger-economy + 知乎 681376328 +
                # 橙瓜 + Qidian-Webnovel Corpus 2.79M] 入V过墙双峰钩(stake 递增 + mega-reveal 末段)·
                # 读 用户偏好.json workflow_preferences.paywall_transition_cluster_id 门控
                # (用户/编辑手填·绝不自动推断)·non-paywall 跳过·advisory·默认 shadow
                ("paywall_transition_gradient",
                 [child_python(), str(_SCRIPT_DIR / "paywall_transition_gradient_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster-id", f"cluster_{cluster_key}" if cluster_key else ""],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "paywall_transition_gradient_scanner",
                     "BRIDGE_PAYWALL_HOOK_GRADIENT_OFF", "剧情")),
                # [2026-06-21 R18 W7 Batch-S·P0·Liang 2024 Science Advances PubMed 5.2B token
                # + arxiv 2412.11400 ChineseLLM excess vocab + 番茄AI识别公开规范] LLM 训练偏置
                # 词 type 级 z-test·占位 llm_chinese_corpus_freq + human_webnovel_corpus_freq·
                # z(LLM)>+2 且 z'(text)>+1 且 z(author)<+1σ → hit·与 R12 metaphor anti-AI
                # (anti-pattern 句法层)正交·advisory·默认 shadow
                ("excess_vocab_corpus_zscan",
                 [child_python(), str(_SCRIPT_DIR / "excess_vocab_corpus_zscan.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "excess_vocab_corpus_zscan",
                     "EXCESS_VOCAB_SIGNATURE_HIT", "风格")),
                # [2026-06-21 R18 W7 Batch-S·P0·Zwaan 1998 event-indexing +
                # arxiv 2506.* situation model 2026 + Cognitive Load Sweller 2024]
                # 5 维 situation model 跟踪(time/space/causation/intentionality/protagonist)·
                # 场景间任一维突变无 marker → dropout·作者档 dim_dropout_tolerance 可旁路·
                # R7-R13 共 101 条全 craft-output 层·首次切到读者认知层·advisory·默认 shadow
                ("situation_model_5dim",
                 [child_python(), str(_SCRIPT_DIR / "situation_model_5dim_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "situation_model_5dim_scanner",
                     "SITUATION_MODEL_DIM_DROPOUT", "结构")),
                # [2026-06-21 R18 W7 Batch-U·P2·filmustage vertical-drama-script +
                # finaldraft verticals-micro-dramas + medium real-reel china-vertical-drama-2026]
                # 短剧竖屏相邻集双侧握手桥·激活门控 genre_tags=short_drama_vertical·
                # resolve_latency_ratio + new_hook_position_ratio·与 R7 hook_strength 11 型(单边)
                # + R8 frame_tale 正交·advisory·默认 shadow
                ("episode_bilateral_bridge",
                 [child_python(), str(_SCRIPT_DIR / "episode_bilateral_bridge_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "episode_bilateral_bridge_scanner",
                     "BRIDGE_RESOLVE_TOO_LATE", "结构")),
                # [2026-06-21 R18 W7 Batch-U·P2·Qidian-Webnovel Corpus 2.79M 评论 +
                # Loewenstein Information Gap 1994 + Groningen Qidian-110]
                # 段落热度·5 Loewenstein gap 特征·仅通章 cold flat 报·
                # 与 cross_cluster_engagement_metrics + hook_strength 正交·advisory·默认 shadow
                ("paragraph_engagement_heat_predictor",
                 [child_python(), str(_SCRIPT_DIR / "paragraph_engagement_heat_predictor.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "paragraph_engagement_heat_predictor",
                     "PARAGRAPH_ENGAGEMENT_FLATLINE", "结构")),
                # [2026-06-21 R18 W7 Batch-U·P2·Cowan 2001/2024 magical number 4±1
                # + Miller 7±2] 单场景活跃角色数·>5 报 COGNITIVE_OVERLOAD·从角色池.json
                # 读 emerged/main names·作者档 wm_load_tolerance 可旁路群像·与 R9 cast_economy
                # (introduce_burst) 正交·advisory·默认 shadow
                ("active_character_wm_load",
                 [child_python(), str(_SCRIPT_DIR / "active_character_wm_load_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "active_character_wm_load_scanner",
                     "COGNITIVE_OVERLOAD", "人物")),
                # [2026-06-21 R18 W7 Batch-U·P2·arxiv 2510.09116 DITING 2025-10
                # + PMC8581763 Frontiers 2021 实证 91.3% + ACL 2022 GuoFeng] 中文
                # pro-drop 零代词残留·三指标(zero_subject/same_sentence_zp/dialogue_gap)·
                # 作者档 zp_baseline z-band 第一权威·兜底地板 same_sentence_zp≥0.60·
                # 与 translationese_residual(译入残留 NP/被动)方向相反·advisory·默认 shadow
                ("zero_pronoun_density",
                 [child_python(), str(_SCRIPT_DIR / "zero_pronoun_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "zero_pronoun_density_scanner",
                     "ZP_DENSITY_OFF_AUTHOR_BAND", "风格")),
                # [2026-06-21 R18 W7 Batch-U·P2·Liang 2024 Science Advances Zipf
                # + Pangram 2025 detector + arxiv 2025-2026 Zipf LLM detection]
                # 词频 log-log α 斜率·人类 α≈1.0 重尾·LLM 偏高瘦尾·作者档 zipf_baseline z-band·
                # 兜底地板 α>1.4 报 ZIPF_ALPHA_DRIFT·与 R18 excess_vocab(type 级)正交(本=分布形状)·
                # advisory·默认 shadow
                ("zipf_alpha",
                 [child_python(), str(_SCRIPT_DIR / "zipf_alpha_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "zipf_alpha_scanner",
                     "ZIPF_ALPHA_DRIFT", "风格")),
                # [2026-06-21 R19 W8 Batch-V·P0·PNAS 2025 Reinhart LLM 4 语法过用]
                # 4 子探针 present participial / nominalization / 嵌套 X的Y / 串联并列堆栈·
                # 作者档 llm_grammar_overuse_baseline z-band 第一权威·与 anti_slop/semantic_slop
                # (词项) + syntactic_diversity (POS n-gram) 严格正交·advisory·默认 shadow
                ("llm_grammar_overuse",
                 [child_python(), str(_SCRIPT_DIR / "llm_grammar_overuse_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "llm_grammar_overuse_scanner",
                     "LLM_GRAMMAR_PARTICIPIAL_OVERUSE", "风格")),
                # [2026-06-21 R19 W8 Batch-V·P0·凡人修仙传仙界篇 / Cradle]
                # 跨书系列文顶阶角色稀缺性塌缩·读 series_rank_ledger.json·top-rank 密度比 +
                # leapfrog 战斗·与单本 capability_emergence 严格正交·advisory·默认 shadow·
                # 无 ledger skip
                ("cross_book_rank_scarcity",
                 [child_python(), str(_SCRIPT_DIR / "cross_book_rank_scarcity_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cross_book_rank_scarcity_scanner",
                     "CROSS_BOOK_RANK_INFLATION", "剧情")),
                # [2026-06-21 R19 W8 Batch-V·P0·affect dynamics + arxiv 2503.23547]
                # 3D VAD × 6 UED = 18 指标 per-character·占位词典 + 引语切片复用 角色池.json·
                # 作者档 vad_ued_signature.per_character 第一权威·与 affective/sentiment_arc/
                # ousiometric/emotion_curve 严格正交·advisory·默认 shadow
                ("character_vad_ued",
                 [child_python(), str(_SCRIPT_DIR / "character_vad_ued_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "character_vad_ued_scanner",
                     "VAD_UED_DRIFT", "人物")),
                # [2026-06-21 R19 W8 Batch-X·P1·Sanderson Laws + Cradle 跨书系列文不变量]
                # 读 workspace/styles/<series>/magic_invariants.json·占位 NLI 启发式·
                # CROSS_BOOK_INVARIANT_BREACH advisory·绝不 hard_gate·无 ledger skip·
                # 与 locked_fact_cross_scene/future_knowledge_leak/motif_recurrence 严格正交
                ("cross_book_invariant",
                 [child_python(), str(_SCRIPT_DIR / "cross_book_invariant_scanner.py"),
                  str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cross_book_invariant_scanner",
                     "CROSS_BOOK_INVARIANT_BREACH", "剧情")),
                # [2026-06-21 R19 W8 Batch-X·P1·Vishnubhotla 旁白对话 VAD 0.06-0.09 baseline]
                # 复用 _QUOTE_PAT 切两通道·Pearson per V/A/D·|r|>0.50 → NARR_DIAL_VAD_OVERCOUPLED
                # 作者档 dial_narr_vad_target_corr 第一权威·与 character_vad_ued/affective 正交
                ("narration_dialogue_vad",
                 [child_python(), str(_SCRIPT_DIR / "narration_dialogue_vad_coherence_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "narration_dialogue_vad_coherence_scanner",
                     "NARR_DIAL_VAD_OVERCOUPLED", "风格")),
                # [2026-06-21 R19 W8 Batch-X·P1·Hatfield emotional contagion + Gottman 4 阶段]
                # 双人主导对话 lagged cross-correlation 同步窗 + 冲突场景 Gottman 级联·
                # DIALOGUE_CONTAGION_ABNORMAL advisory·作者档 dialogue_contagion_signature 第一
                # 权威·与 character_vad_ued/narration_dialogue_vad 严格正交·默认 shadow
                ("dialogue_emotion_contagion",
                 [child_python(), str(_SCRIPT_DIR / "dialogue_emotion_contagion_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "dialogue_emotion_contagion_scanner",
                     "DIALOGUE_CONTAGION_ABNORMAL", "对话")),
                # [2026-06-21 R19 W8 Batch-X·P2·AdaMARP 多人对话编排]
                # 扫 _数据库/dialogue_turn_log.json 校验 turn 合规率·>30% 异常 →
                # DIALOGUE_ORCHESTRATOR_DEGRADED advisory·env DIALOGUE_ORCHESTRATOR_MODE
                # 默认 off(无 turn log 直接 skip)·与 hierarchical_planner/quotative 正交
                ("dialogue_scene_manager",
                 [child_python(), str(_SCRIPT_DIR / "dialogue_scene_manager.py"),
                  "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "dialogue_scene_manager",
                     "DIALOGUE_ORCHESTRATOR_DEGRADED", "对话")),
                # [2026-06-21 R20 W9 Batch-Z·P0·OSCToM K-order(K=2) belief nesting]
                # 扩 R18 1-order belief_state → belief_about[a][b][topic]·K-2 嵌套
                # 模式 <A>(以为|认为|觉得|猜) <B>(知道|不知道) <fact_ref> · 末轮
                # K-order vs K-1 矛盾·outline-planner manifest.dramatic_irony_anchor
                # 显式白名单合法不报·与 R18 character_belief_ledger(K=1) / dramatic_irony
                # (TELL 词) / focalizer_perception_bounds(narrator 层) / locked_fact_cross_scene
                # 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("cross_character_kth_order_belief",
                 [child_python(), str(_SCRIPT_DIR / "cross_character_kth_order_belief_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--manifest", str(project_root / "_数据库" / ".manifest" / f"ch_{ch:03d}.json")],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cross_character_kth_order_belief_scanner",
                     "CHARACTER_KTH_ORDER_BELIEF_DRIFT", "人物")),
                # [2026-06-21 R20 W9 Batch-Z·P0·NKW 时态可分 entity profile]
                # stable_identity SLOW_UPDATE 慢变身份维度 / dynamic_state FAST_UPDATE
                # 快变状态维度·扫稿抽 <char>(的)?<attr>(是|为)<value>
                # 断言·attr∈stable & 与 ledger value 不符 → CHARACTER_STATE_DRIFT_DETECTED·
                # attr∈dynamic_state 白名单 = 合法剧情进展不报·提供
                # filter_dynamic_state_changes() 给 R12 contradiction 二筛剔除·
                # 与 R12 contradiction / locked_fact / character_belief_ledger 严格正交·
                # advisory·默认 shadow·绝不 hard_gate·
                # 🔴 2026-06-29 清假producer口径:per-project character_state_ledger.json 当前
                # 无 producer(曾误称 distill-character/save-state step 12 填·实无此步)·恒走
                # DEFAULT 通用白名单兜底(合法基线非降级)·此兜底下 stable_drift 恒 0·
                # archivist character_state baseline 为 TODO
                ("character_state_drift",
                 [child_python(), str(_SCRIPT_DIR / "character_state_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "character_state_drift_scanner",
                     "CHARACTER_STATE_DRIFT_DETECTED", "人物")),
                # [2026-06-21 R20 W9 Batch-AA·P1·Gordon Lish MFA consecution doctrine]
                # 句间正向回扣链 3 探针(lexical_carryover + syntactic_template_repeat +
                # phonic_carryover · phonic=placeholder pypinyin defer)·题材 gating
                # 言情/严肃/古风 active · 爽文 silent·与 R7 prose_rhythm/R8 rhetoric_repetition/
                # R20 anti_slop 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("lish_consecution_chain",
                 [child_python(), str(_SCRIPT_DIR / "lish_consecution_chain_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "lish_consecution_chain_scanner",
                     "LISH_CONSECUTION_THIN", "风格")),
                # [2026-06-21 R20 W9 Batch-AA·P1·Stephen Baxter《Art of Subtext》MFA staging]
                # 身体/空间/道具微调度密度·4 桶 staging cue(body_cue/space_cue/prop_cue/posture_shift)·
                # per-character staging_share + dialogue_tag_to_staging_ratio·gating dialogue_density>p50
                # + active_chars≥2·与 group_dialogue_balance/physio_cue_diversity/
                # indirect_characterization 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("baxter_staging_density",
                 [child_python(), str(_SCRIPT_DIR / "baxter_staging_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "baxter_staging_density_scanner",
                     "STAGING_THIN", "人物")),
                # [2026-06-21 R20 W9 Batch-AA·P1·Biber 1988 MDA + Xiao 2009 中文映射]
                # 4 维(D1 涉入度/D2 叙事关切/D3 语境指称/D4 说服度)中文 marker 映射·
                # per-cluster 4 维 z-score vs 作者档·|z|>1 报 BIBER_MDA_DRIFT_Dn·与
                # function_word_fingerprint/syntactic_diversity/indirect_characterization
                # 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("biber_mda",
                 [child_python(), str(_SCRIPT_DIR / "biber_mda_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "biber_mda_scanner",
                     "BIBER_MDA_DRIFT_D1", "风格")),
                # [2026-06-21 R20 W9 Batch-CC·P2·SEO id 12·章内 micro-cliffhanger 节奏]
                # 复用 hook_strength 11 型 regex 子集·章内 hook 相邻间距 z-band·与 hook_strength
                # /cliffhanger_quota 严格正交(那俩看章末/拟切点/跨章配比·本者看章内间距分布)·
                # advisory·默认 shadow·绝不 hard_gate
                ("mid_chapter_micro_cliffhanger_cadence",
                 [child_python(), str(_SCRIPT_DIR / "mid_chapter_micro_cliffhanger_cadence_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "mid_chapter_micro_cliffhanger_cadence_scanner",
                     "MID_CHAPTER_CLIFF_CADENCE_OFF_BAND", "节奏")),
                # [2026-06-21 R20 W9 Batch-CC·P2·Q3-Q4 id 16·句级张力梯度 forecasting]
                # 占位 char Shannon entropy 相邻 200 CJK 块差分·真版 SBERT 自相关 defer·
                # 梯度 pstdev + flatline_ratio 双闸·与 narrative_rhythm(macro)/
                # premature_resolution(标志词距离) 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("forecasting_tension",
                 [child_python(), str(_SCRIPT_DIR / "forecasting_tension_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "forecasting_tension_scanner",
                     "FORECASTING_TENSION_FLAT", "节奏")),
                # [2026-06-21 R20 W9 Batch-CC·P2·Q3-Q4 id 20·Paivio 1968 dual-coding]
                # 句级具象度词典 z-band·core/data/imageability_zh.json 60 高 + 60 低·
                # imageability_index = (high-low)/(high+low) ∈ [-1,1]·与 repeat_noun_density/
                # semantic_slop/scene_grounding 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("imageability",
                 [child_python(), str(_SCRIPT_DIR / "imageability_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "imageability_scanner",
                     "IMAGEABILITY_OFF_BAND", "风格")),
                # [2026-06-21 R20 W9 Batch-CC·P2·Q3-Q4 id 21·ACW Activity-Centric Writing]
                # 段中心活动漂移·HEAD_LEN=1 句首 CJK 主语 proxy 去重比·writer 端 ACW_DIRECTIVE
                # 通过 build_manifest 在 ACW_MODE=active 时注入·与 narrative_short_sentence/
                # paragraph_engagement_heat/prose_rhythm 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("acw_drift",
                 [child_python(), str(_SCRIPT_DIR / "acw_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "acw_drift_scanner",
                     "ACW_DRIFT_FROM_CENTER", "风格")),
                # [2026-06-21 R22 W10 Batch-DD·P0 STRONG·陈望道《修辞学发凡》38 格四类]
                # 材料/意境/词语/章句四类分布 KL 散度 + 类塌缩 + 总密度·占位 8 词/格·_placeholder=true
                # 与 zeugma/anadiplosis 单格深扫严格正交·与 semantic_slop/repeat_noun_density
                # 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("rhetorical_balance",
                 [child_python(), str(_SCRIPT_DIR / "rhetorical_balance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "rhetorical_balance_scanner",
                     "RHETORICAL_BALANCE_DRIFT", "风格")),
                # [2026-06-21 R21 W10 Batch-DD·R21-NB-01·Nijhof&Willems 2015 motor vs mentalizing]
                # 动作词 vs 心智词 ratio·脑网络竞争代理 r=-0.48·场景级 ratio 偏离 ±0.20
                # 通用兜底 0.35-0.65·与 interiority_mode_balance/duration_mix/narrating_distance
                # 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("action_mentalizing_balance",
                 [child_python(), str(_SCRIPT_DIR / "action_mentalizing_balance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "action_mentalizing_balance_scanner",
                     "ACTION_MENTAL_RATIO_DRIFT", "节奏")),
                # [2026-06-21 R21 W10 Batch-DD·R21-NB-02·Schoeller 2024 CABN aesthetic chills]
                # peak 双相架构 anticipation(200-500CJK 前向) + release(50-150CJK 后向)
                # |valence|>0.7 top-3 peak·任缺一相 → CHILLS_ARCH_INCOMPLETE·与 hook_strength/
                # premature_resolution/emotion_curve_rescan 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("peak_chills_architecture",
                 [child_python(), str(_SCRIPT_DIR / "peak_chills_architecture_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "peak_chills_architecture_scanner",
                     "CHILLS_ARCH_INCOMPLETE", "节奏")),
                # [2026-06-21 R21 W10 Batch-DD·R21-NB-03·Kaneshiro 2024 EJN ISC r=0.65]
                # DMN integration ridge 密度·200CJK 滑窗·4 信号(回指 names/locked_fact/foreshadowing
                # 回收/合流标志)·任窗≥3 命中=ridge·无 ridge→ABSENT·单 ridge<60%→TOO_EARLY
                # cross-cluster 视野·与 cross_cluster_engagement_metrics/retention_proxy/
                # paragraph_engagement_heat 严格正交·advisory·默认 shadow·绝不 hard_gate
                ("integration_ridge_density",
                 [child_python(), str(_SCRIPT_DIR / "integration_ridge_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "integration_ridge_density_scanner",
                     "INTEGRATION_RIDGE_ABSENT", "剧情")),
                # [2026-06-22 R24 W12 Batch-JJ·P0 STRONG·直播弹幕预测 9-class burst typology]
                # 9 类爆点(laughter/shock/grief/anticipation/shipping/awe/critique/callback/meta)
                # 从 cluster brief.intended_burst_type 拿目标·扫尾部 80-200 CJK·缺面 advisory
                # env BURST_TYPE_MODE 默认 shadow·绝不 hard_gate·_placeholder=true
                ("cluster_burst_type_predictor",
                 [child_python(), str(_SCRIPT_DIR / "cluster_burst_type_predictor.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cluster_burst_type_predictor",
                     "BURST_TYPE_NOT_DELIVERED", "节奏")),
                # [2026-06-22 R24 W12 Batch-JJ·P1·Cialdini commitment+consistency K-12 教育叙事]
                # 首 200 CJK anomaly_seed+pledge·末 500 CJK reveal·三状态 advisory
                # env MYSTERY_PLEDGE_MODE 默认 shadow·绝不 hard_gate
                ("mystery_pledge_scanner",
                 [child_python(), str(_SCRIPT_DIR / "mystery_pledge_scanner.py"),
                  str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "mystery_pledge_scanner",
                     "MYSTERY_PLEDGE_DANGLING", "剧情")),
                # [2026-06-22 R24 W12 Batch-JJ·P1·Nathan-Koedinger expert blindspot 教育]
                # 术语首引登记·距上次具体锚定 gap·base 2000/复杂规则 800/POV 不计
                # top-5 drift_unanchored·env EXPERT_BLINDSPOT_MODE 默认 shadow·绝不 hard_gate
                ("expert_blindspot_scanner",
                 [child_python(), str(_SCRIPT_DIR / "expert_blindspot_scanner.py"),
                  str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "expert_blindspot_scanner",
                     "EXPERT_BLINDSPOT_DRIFT", "风格")),
                # [2026-06-22 R24 W12 Batch-JJ·P1·Glaser four levers 教育叙事 info-dump 救援]
                # 识别 info-dump 段·四杠杆 0/1·0/4 段 advisory 建议补最便宜
                # env GLASER_LEVERS_MODE 默认 shadow·绝不 hard_gate
                ("glaser_four_levers",
                 [child_python(), str(_SCRIPT_DIR / "glaser_four_levers.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "glaser_four_levers",
                     "GLASER_LEVER_MISSING", "风格")),
                # [2026-06-22 R24 W12 Batch-JJ·P1·anticipation signposting 直播弹幕预测]
                # top-K(3) 爆点段向前回溯 2-3 段窗口·5 类 signpost·z-band·<-1σ advisory
                # env SIGNPOST_MODE 默认 shadow·绝不 hard_gate
                ("gaoneng_anticipation_signposting_scanner",
                 [child_python(), str(_SCRIPT_DIR / "gaoneng_anticipation_signposting_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "gaoneng_anticipation_signposting_scanner",
                     "BURST_LEAD_SIGNPOST_LOW", "节奏")),
                # [2026-06-22 R24 W12 Batch-KK·P1·跨语言情感坐标漂移]
                # CVAW v2 vs NRC-VAD CN 双词典 Δv/Δa>0.15 → ANGLO_DRIFT；
                # 文化特有词覆盖率<0.6× 基线 → UNDERUSE；
                # clear/ambivalent 比例>1.8× 基线 → BINARY_POLARIZATION
                # env CN_EMOTION_VAD_MODE 默认 shadow·绝不 hard_gate·_placeholder=true
                ("cn_emotion_vad_drift_scanner",
                 [child_python(), str(_SCRIPT_DIR / "cn_emotion_vad_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cn_emotion_vad_drift_scanner",
                     "CN_EMOTION_ANGLO_DRIFT", "风格")),
                # [2026-06-22 R24 W12 Batch-KK·P1·writer intent agenda drift]
                # 4 维盲意图卡(want/antagonist/stake/tone-word) SHA-256 锁定 step 1
                # step 6 草稿 char-Jaccard 比对·任一<0.62 → WRITER_INTENT_AGENDA_DRIFT
                # env AGENDA_DRIFT_MODE 默认 shadow·绝不 hard_gate
                ("agenda_drift_scanner",
                 [child_python(), str(_SCRIPT_DIR / "agenda_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "agenda_drift_scanner",
                     "WRITER_INTENT_AGENDA_DRIFT", "剧情")),
                # [2026-06-22 R24 W12 Batch-KK·P1·author signature slot preservation]
                # scene_storyboard.author_signature_slots[{slot_id,text,preserve_policy,anchor_hint}]
                # verbatim(Lev≤5%) / near_verbatim_punct_only(剥标点等价)
                # 失配 → AUTHOR_SIGNATURE_MISMATCH·缺失(verbatim) → NOT_PLACED minor
                # env AUTHOR_SIGNATURE_MODE 默认 shadow·绝不 hard_gate
                ("author_signature_preservation",
                 [child_python(), str(_SCRIPT_DIR / "author_signature_preservation.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "author_signature_preservation",
                     "AUTHOR_SIGNATURE_MISMATCH", "风格")),
                # ============ [G2 P2 2026-06-22] 17 SHADOW_SCANNERS 接齐(R18 Batch-T 3 + R22-R25 14) ============
                # 解开「在线但 0 advisory」死代码·全 advisory shadow·hard_gate 12 码不变·绝不 hard_gate
                # 北极星⑤顾问制·作者档第一权威·shadow 默认不上报(仅 scanner_status 痕迹证明在线)
                # [R18 Batch-T·P0·Halliday parataxis 中文叙事铁律]
                ("paratactic_implicit_logic",
                 [child_python(), str(_SCRIPT_DIR / "paratactic_implicit_logic_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "paratactic_implicit_logic_scanner",
                     "PARATAXIS_OFF_AUTHOR_BAND", "风格")),
                # [R18 Batch-T·P0·间接刻画密度 + 情感钟摆]
                ("indirect_characterization_ratio",
                 [child_python(), str(_SCRIPT_DIR / "indirect_characterization_ratio_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "indirect_characterization_ratio_scanner",
                     "INDIRECT_CHARACTERIZATION_THIN", "人物")),
                # [R18 Batch-T·P0·Centering Theory 焦点持续性]
                ("centering_theory_focus",
                 [child_python(), str(_SCRIPT_DIR / "centering_theory_focus_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)] + _style_args,
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "centering_theory_focus_scanner",
                     "CENTERING_ROUGH_SHIFT_OVERLOAD", "节奏")),
                # [R22 Batch-FF·P1·陈望道拈连格 zeugma]
                ("zeugma",
                 [child_python(), str(_SCRIPT_DIR / "zeugma_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "zeugma_scanner",
                     "ZEUGMA_DETECTED", "风格")),
                # [R22 Batch-FF·P1·陈望道顶真格 anadiplosis]
                ("anadiplosis",
                 [child_python(), str(_SCRIPT_DIR / "anadiplosis_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "anadiplosis_scanner",
                     "ANADIPLOSIS_DETECTED", "风格")),
                # [R22 Batch-FF·P1·Fauconnier&Turner CBT premise_blend_card]
                ("premise_blend_card",
                 [child_python(), str(_SCRIPT_DIR / "premise_blend_card_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "premise_blend_card_scanner",
                     "PREMISE_BLEND_CARD_MISSING", "剧情")),
                # [R22 Batch-FF·P2·CBT 7 类 vital_relations 探针·占位词典 lexicon_placeholder=true]
                # vital_relations_probe 是 probe(无 violations/advisories)·_parse_advisories_scanner
                # 兼容空字段·只占在线 scanner_status 痕迹·不产 issue(零回归保障)
                ("vital_relations_probe",
                 [child_python(), str(_SCRIPT_DIR / "vital_relations_probe.py"),
                  str(cluster_draft)],
                 {0, 1},
                 lambda out, code: _parse_advisories_scanner(
                     out, "vital_relations_probe",
                     "VITAL_RELATIONS_DENSITY_TRACK", "风格")),
                # [R25 Batch-MM·P1·Foucault Fearless Speech parrhesia·G2 P2 词典外部化]
                ("parrhesia_density",
                 [child_python(), str(_SCRIPT_DIR / "parrhesia_density_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "parrhesia_density_scanner",
                     "PARRHESIA_DENSITY_THIN", "风格")),
                # [R25 Batch-MM·P1·梵剧 Rasa 双层一致(dominant/transient)]
                ("cluster_rasa_layer",
                 [child_python(), str(_SCRIPT_DIR / "cluster_rasa_layer_consistency_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "cluster_rasa_layer_consistency_scanner",
                     "RASA_LAYER_DOMINANT_DRIFT", "风格")),
                # [R25 Batch-MM·P1·梵剧 Rasa 因果链(vibhava→anubhava 完整性)]
                ("rasa_causal_chain",
                 [child_python(), str(_SCRIPT_DIR / "rasa_causal_chain_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "rasa_causal_chain_scanner",
                     "RASA_CAUSAL_BREAK_MISMATCH", "剧情")),
                # [R25 Batch-MM·P1·Mawhorter Choice Poetics 5 维(走向卡 advisory)]
                # 走向卡 JSON 路径 = _数据库/.direction_card_advisory/<cluster_key>.json
                # 文件不存在 → scanner 优雅"无候选卡·跳过"(无 violations 产)
                ("direction_card_poetics",
                 [child_python(), str(_SCRIPT_DIR / "direction_card_poetics_scanner.py"),
                  str(project_root / "_数据库" / ".direction_card_advisory" / f"{cluster_key}.json"),
                  "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "direction_card_poetics_scanner",
                     "DIRECTION_CARD_FRAMING_THIN", "剧情")),
                # [R23 Batch-II·P2·Echo Draft 4-Pass 朗读 performance]
                ("audio_performance",
                 [child_python(), str(_SCRIPT_DIR / "audio_performance_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "audio_performance_scanner",
                     "AUDIO_PERF_TRI_CLAUSE_HEAVY", "风格")),
                # [R23 Batch-II·P2·writer growth 词汇多样性 dashboard·cross-cluster·advisories[] 形态]
                ("writer_growth_dashboard",
                 [child_python(), str(_SCRIPT_DIR / "writer_growth_dashboard.py"),
                  str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_advisories_scanner(
                     out, "writer_growth_dashboard",
                     "WRITER_GROWTH_VOCAB_DROP", "风格")),
                # [R23 Batch-II·P2·反派情感重充电监控·cross-cluster·advisories[] 形态·G2 P2 词典外部化]
                ("antagonist_valence_trajectory",
                 [child_python(), str(_SCRIPT_DIR / "antagonist_valence_trajectory.py"),
                  str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_advisories_scanner(
                     out, "antagonist_valence_trajectory",
                     "ANTAGONIST_VALENCE_DRIFT_UNAUTHORIZED", "人物")),
                # [R24 Batch-LL·P2·微短剧节拍栅格(head/mid/tail 3-15-30s)]
                ("microdrama_intraep_beat_lattice",
                 [child_python(), str(_SCRIPT_DIR / "microdrama_intraep_beat_lattice.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "microdrama_intraep_beat_lattice",
                     "MICRODRAMA_HEAD_3S_NO_ACTION", "节奏")),
                # [R24 Batch-LL·P2·prose 180° 空间轴一致性]
                ("prose_180_axis",
                 [child_python(), str(_SCRIPT_DIR / "prose_180_axis_scanner.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "prose_180_axis_scanner",
                     "PROSE_AXIS_FLIP", "剧情")),
                # [R24 Batch-LL·P2·视觉具象往返(Paivio dual-coding round-trip)]
                ("imageability_round_trip_probe",
                 [child_python(), str(_SCRIPT_DIR / "imageability_round_trip_probe.py"),
                  str(cluster_draft), "--project", str(project_root),
                  "--cluster", cluster_key],
                 {0, 1},
                 lambda out, code: _parse_violations_scanner(
                     out, "imageability_round_trip_probe",
                     "IMAGEABILITY_ROUND_TRIP_LOW", "风格")),
                # ============ [G2 P2 2026-06-22] 17 SHADOW_SCANNERS 接齐 END ============
                # ============ [2026-06-29 NN 模型扩展] 5 个 NN-backed scanner ============
                # [2026-06-29 NN②] 信息密度 surprisal · GPT-2 token-level 段间方差/断崖/单调/高潮失衡
                # · RUOYU_NN_SURPRISAL 门控(默认 off·桥不可用返回空)·advisory·默认 shadow
                ("surprisal",
                 [child_python(), str(_SCRIPT_DIR / "surprisal_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "surprisal_scanner", "风格"),
                 300),
                # [2026-06-29 NN⑥] 主题漂移 · embedding cosine 距离 vs scope_summary
                # · EMBED_BACKEND 非 hash 才激活·advisory·默认 shadow
                ("topic_drift",
                 [child_python(), str(_SCRIPT_DIR / "topic_drift_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "topic_drift_scanner", "风格"),
                 300),
                # [2026-06-29 NN③A] 情感弧线分类 · Reagan 六弧型 · 复用 VAD 或词典兜底
                # · RUOYU_NN_VAD 门控(VAD 桥不可用退词典)·advisory·默认 shadow
                ("emotion_arc",
                 [child_python(), str(_SCRIPT_DIR / "emotion_arc_classifier.py"),
                  str(cluster_draft), "--project", str(project_root), "--scan"],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "emotion_arc_classifier", "风格"),
                 300),
                # [2026-06-29 NN①] 段落连贯性 · 相邻段对 BERT 二分类 + 滑窗
                # · RUOYU_NN_COHERENCE 门控(桥不可用返回空)·advisory·默认 shadow
                ("coherence",
                 [child_python(), str(_SCRIPT_DIR / "coherence_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "coherence_scanner", "风格"),
                 300),
                # [2026-06-29 NN⑤④] 角色一致性 · 角色网络+共指消解整合
                # · CHARACTER_CONSISTENCY_MODE 门控(默认 shadow)·advisory
                ("character_consistency",
                 [child_python(), str(_SCRIPT_DIR / "character_consistency_scanner.py"),
                  str(cluster_draft), "--project", str(project_root)],
                 {0, 1},
                 lambda out, code: _parse_issues_list_scanner(
                     out, "character_consistency_scanner", "角色"),
                 300),
            ])
            # [2026-06-13 阶段3] 题材专属 scanner 路由：按 genre 条件激活(romance/litrpg)·全 advisory·
            # 通用维度池 always-on(上面)·题材层按 genre·hard_gate 清单不随题材变。
            try:
                import scaffold_genre_packs as _gp
                _genre = _resolve_audit_genre(project_root)
                _gscanner = _gp.get_scanner(_genre)
                if _gscanner:
                    _gpath = _SCRIPT_DIR / _gscanner
                    if _gpath.exists():
                        _code = _gscanner.replace("_scanner.py", "").upper()
                        tasks.append((
                            _gscanner.replace("_scanner.py", ""),
                            [child_python(), str(_gpath), str(cluster_draft),
                             "--project", str(project_root)] + _style_args, {0, 1},
                            lambda out, code, _c=_code: _parse_violations_scanner(
                                out, _gscanner, _c, "风格")))
            except Exception as _e:
                print(f"[audit_hub] 题材 scanner 路由跳过: {_e}", file=sys.stderr)
            # 🔴 2026-06-27 C06：整段草稿扫剧本体 SCREENPLAY 标记（位置无关 hard_gate · step3 真阻断点）。
            # 此处草稿尚未切章 → chapter_end_anchor_scan（依赖 第NNN章 文件）跑不到，整段硬扫补上这个缺口。
            _screenplay_issues = _scan_cluster_draft_screenplay(cluster_draft)
            all_issues += _screenplay_issues
            scanner_status.append({
                "scanner": "cluster_draft_screenplay_scan",
                "exit_code": 2 if _screenplay_issues else 0,
                "ok": True,
                "issues_count": len(_screenplay_issues),
            })
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
                # 🔴 2026-06-27 P1-07: 按 PID state 注入 chapter_end_weak_anchor_ratio（advisory 阈值）。
                # 解析失败 / 无 state / off → 不传 --weak-anchor-ratio（scanner 走默认 0.15·零回归）。
                _ceas_cmd = [child_python(), str(ceas), str(project_root),
                             "--chapters", ch_range, "--json"]
                try:
                    _wa_ratio = _resolve_chapter_end_weak_anchor_ratio(project_root)
                    if _wa_ratio is not None:
                        _ceas_cmd += ["--weak-anchor-ratio", f"{_wa_ratio:.4f}"]
                except Exception:
                    pass
                tasks.append((
                    "chapter_end_anchor_scan",
                    _ceas_cmd,
                    {0, 1, 2},
                    lambda out, code: _parse_chapter_end_anchor(out, code),
                ))

    def _exec_one(task):
        name, cmd, ok_set, parse_fn = task[:4]
        # 默认 300s：NN 开启后 VAD/coherence/emotion 等 scanner 需 spawn venv + 加载模型(~90s+)·
        # 显式 task[4] 可覆盖（5 个 NN scanner 已显式 300）。非 NN scanner 秒退不受影响·
        # 只有真卡死才等满（罕见·兜底）。根治 emotion_granularity 等 VAD scanner 180s 超时(exit 99)。
        task_timeout = task[4] if len(task) > 4 else 300
        # v2 cluster 化：cluster 调用上下文给 scanner 传 CLUSTER_MODE=1 env
        code, out, err = _run(cmd, env_extra=_env_extra, timeout=task_timeout)
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
    # 🔴 2026-06-27 C09 豁免诚实审计：在 apply-moment 算 blanket/orphan/waive_rate 信号。
    # META-only —— 下面的 verdict 判定【完全不读】waiver_audit，blanket 也判不出 block。
    waiver_audit = _compute_waiver_audit(all_issues, waivers, waived_issues)

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
        # 🔴 2026-06-27 C09 豁免诚实审计 META 段（绝不参与 verdict 判定 · 喂 learning_loop）
        "waiver_audit": waiver_audit,
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
    code, out, err = _run([child_python(), str(ll), str(project_root),
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
    # 🔴 2026-06-30 创作流程 NN 默认接入（命令行入口·main only·测试 import 不触发·能力不足各桥自动回退）
    import nn_runtime_defaults
    _nn_on = nn_runtime_defaults.enable_creative_nn_defaults()
    if _nn_on:
        print(f"[nn] 创作 audit 默认开启 NN 门控: {', '.join(_nn_on)}", file=sys.stderr)
    if len(args) < 2:
        print("用法: python audit_hub.py <项目路径> <章节号> [--auto-fix] [--json] [--waivers <json路径>]"
              " | python audit_hub.py <项目路径> --mode cluster --cluster-id <key> [--auto-fix] [--waivers ...]")
        sys.exit(3)
    project_root = Path(args[0]).resolve()
    if not project_root.is_dir():
        print(f"[FATAL] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(3)
    auto_fix = "--auto-fix" in args
    want_json = "--json" in args
    # [2026-06-20 R8 W4 Batch-H · L24] DRAMATURGE 三阶段分层 audit 开关·shadow 默认·
    # 跑完 audit_hub 再调 audit_hub_hierarchical_planner.run_hierarchical 把 issues 统筹成
    # revision_plan.json·不破坏现有路径·全 advisory·default shadow (env HIERARCHICAL_AUDIT_MODE)
    hierarchical = "--hierarchical" in args
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
        # L24 hierarchical：跑完写 revision_plan.json (advisory · 不影响主报告)
        if hierarchical:
            try:
                import audit_hub_hierarchical_planner as _hp
                _hier = _hp.run_hierarchical(project_root, cluster_key, report)
                (report_dir / f"cluster_{cluster_key}_revision_plan.json").write_text(
                    json.dumps(_hier, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as _e:
                print(f"[hierarchical] 脚手架跳过: {_e}", file=sys.stderr)
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
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
