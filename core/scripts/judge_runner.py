#!/usr/bin/env python3
"""judge_runner.py — 判断层统一入口（程序驱动 M2 · 2026-06-10）

8 个判断 agent（summarizer/foreshadower/reflector/reading-reflector/voice-checker/
validator-checker/outline-planner/researcher）从「Claude spawn .claude/agents/*.md」
迁移为「driver 调 gen-model + 结构化输出」。本模块是唯一入口。

四条硬契约（对抗审查定调 · 北极星⑤）：
1. 【作者档第一权威存续】needs_author_profile 的 judge，system prompt 必须注入
   作者风格档全文；读不到 → 报告写 _author_profile_missing=True + system 注入
   「不得输出任何风格类 finding」——绝不退回通用规则审稿（惊悚乐园流水账翻车实证）。
2. 【重试边界】重试只针对 JSON 语法/结构破损（parse 失败 / required_keys 缺失）；
   绝不针对「判断内容/枚举值不符预期」——枚举形状不得反向规训模型判断。
   截断（finish=length）走 transport 续写补全，不整发重试（同位置再截不收敛）。
3. 【failure_policy 分级】block = 输出喂确定性状态机（summarizer/foreshadower/
   outline-planner——locked_facts/伏笔/下一 cluster brief 断了状态链 → 整本书后半崩），
   失败必须抛 JudgeBlockedError 停流水线；soft = 纯 advisory（reflector/voice-checker/
   validator-checker/reading-reflector），失败降级落 _parse_failed 报告不阻断。
4. 【自由文本兜底】所有 judge 输出顶层允许 free_notes 字段，表达 schema 预定义
   维度之外、作者档独有的判断——schema 是格式闸不是裁决闸。

prompt 单一真理源：运行时读 .claude/agents/<name>.md（剥 frontmatter + 加适配头），
不在本模块复制 prompt 文本——.md 改了 judge 自动跟进。

gate 裁决不在这里：judge 只产 findings（顾问），gate_level 标注/waiver 收集/hard_gate
强制忽略豁免由 audit_hub._apply_waivers 做（确定性·法官）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import llm_transport as lt  # noqa: E402
from gen_model_loader import GenModelLoader  # noqa: E402
from log_util import get_logger  # noqa: E402

logger = get_logger(__name__)

# 🔴 frozen-aware（对抗审查同款 FATAL）：PyInstaller 把 core/scripts 模块扁平收成顶层名，
# _SCRIPTS=Path(__file__).parent 在 frozen 下指 _internal/（扁平）→ .parent.parent 跑出
# bundle 外，.claude/agents/*.md 读不到 → 所有 needs_author_profile 的 judge 拿不到 prompt。
# 用 frozen_util.bundle_root()（frozen=_MEIPASS·dev=仓库根·与 _SCRIPTS.parent.parent 逐字节
# 一致）。datas 把 .claude/agents/*.md 落到 bundle_root()/.claude/agents → AGENTS_DIR 命中。
try:
    from frozen_util import bundle_root as _bundle_root
    REPO_ROOT = _bundle_root()
except Exception:
    REPO_ROOT = _SCRIPTS.parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

JUDGE_TEMPERATURE = 0.3       # 复核档（与 ai_wrapper 对齐·低于创作温度）
JUDGE_MAX_TOKENS = 16000      # 深 schema + reasoning thinking 段都要预算


class JudgeBlockedError(Exception):
    """block 级 judge 失败（喂状态机的输出拿不到 → 必须停，静默透传=状态丢失）。"""


@dataclass
class JudgeSpec:
    name: str                          # agent 名（= .claude/agents/<name>.md）
    failure_policy: str                # 'block' | 'soft'
    needs_author_profile: bool = False # 风格判断类必须注入作者档
    required_keys: tuple = ()          # 输出 JSON 顶层必须存在的键（结构校验·非内容）
    max_tokens: int = JUDGE_MAX_TOKENS
    # 输出落地路径模板（相对 project_root·{key}/{next_key}/{round} 由调用方填）
    output_template: str = ""
    secondary_output_template: str = ""  # voice-checker 双载体（brief + JudgeReport）


# 8 个判断 agent 注册表。
# failure_policy 依据（对抗审查）：summarizer/foreshadower/outline-planner 的输出被
# build_manifest / foreshadowing_handoff / cluster_emergence / 事件簇.json 当输入消费
# （确定性状态机）→ block；其余纯 advisory → soft。
# required_keys 只校验「结构存在」，绝不校验内容/枚举（北极星⑤重试边界）。
AGENT_SPECS: dict[str, JudgeSpec] = {
    "novel-summarizer": JudgeSpec(
        name="novel-summarizer", failure_policy="block",
        required_keys=("cluster_id", "summary", "emotion"),
        output_template="_数据库/.wal/cluster_{key}_summary.json"),
    "novel-foreshadower": JudgeSpec(
        name="novel-foreshadower", failure_policy="block",
        required_keys=("judge_id", "specific_findings"),
        output_template="_数据库/.judge_reports/cluster_{key}_foreshadower.json"),
    "novel-reflector": JudgeSpec(
        name="novel-reflector", failure_policy="soft",
        required_keys=("entries",),
        output_template="_数据库/.wal/cluster_{key}_reflection.json"),
    "novel-reading-reflector": JudgeSpec(
        name="novel-reading-reflector", failure_policy="soft",
        needs_author_profile=True,
        required_keys=("verdict", "new_issues_this_round"),
        output_template="_数据库/.reading_reflection/cluster_{key}_round_{round}.json"),
    "novel-voice-checker": JudgeSpec(
        name="novel-voice-checker", failure_policy="soft",
        needs_author_profile=True,
        required_keys=("violations",),
        output_template="_数据库/.checker_briefs/cluster_{key}_voice.json",
        secondary_output_template="_数据库/.judge_reports/cluster_{key}_voice-checker.json"),
    "novel-validator-checker": JudgeSpec(
        name="novel-validator-checker", failure_policy="soft",
        needs_author_profile=True,
        required_keys=("violations",),
        output_template="_数据库/.checker_briefs/cluster_{key}_validator.json"),
    "novel-outline-planner": JudgeSpec(
        name="novel-outline-planner", failure_policy="block",
        needs_author_profile=True,
        required_keys=(),  # 三模式输出形态不同·结构校验交模式各自的调用点传
        output_template="_数据库/.wal/cluster_{next_key}_brief_candidates.json"),
    "novel-researcher": JudgeSpec(
        name="novel-researcher", failure_policy="soft",
        required_keys=("synthesis_summary",),
        output_template="_数据库/.research_cache/{task_type}_{slug}_{ts}.json"),
    # 🔴 蒸馏 phase-1 表层蒸馏（阶段3）：唯一 needs_author_profile=False 的 judge——它在「产」
    # 作者档不是消费（driver 代读本 cluster 全章原文经 context 注入）。required_keys 只 3 个顶层
    # 结构键·绝不逐项列 48 dim（弱模型为凑键产空壳 = 惊悚乐园流水账覆辙·北极星⑤）。block：
    # surface JSON 是整条蒸馏链源头·喂 consolidate/arc_aggregator 确定性状态机。
    "novel-distill-analyzer": JudgeSpec(
        name="novel-distill-analyzer", failure_policy="block",
        needs_author_profile=False,
        required_keys=("quantitative", "qualitative_dims", "golden_paragraphs"),
        output_template="蒸馏进度/cluster_{key}_surface.json"),
}


@dataclass
class JudgeOutcome:
    agent: str
    ok: bool                      # parse + 结构校验通过
    data: dict                    # 解析出的 JSON（失败时含 _parse_failed/_degraded）
    output_path: Path | None      # 已落盘路径（未落盘 None）
    degraded: bool = False        # soft 失败降级
    author_profile_missing: bool = False
    profile_name: str = ""
    retries: int = 0


# ============ prompt 装配 ============
def load_agent_system_prompt(agent_name: str, agents_dir: Path | None = None) -> str:
    """读 .claude/agents/<name>.md 当 system prompt（单一真理源）· 剥 YAML frontmatter。"""
    d = agents_dir or AGENTS_DIR
    p = d / f"{agent_name}.md"
    if not p.exists():
        raise FileNotFoundError(f"agent 定义不存在: {p}")
    text = p.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    return text.strip()


# gen-model 没有 Read/Write 工具——driver 代读文件、judge 只输出 JSON、driver 落盘。
ADAPTER_HEADER = """\
# 运行环境适配（程序驱动模式 · 必读）
你运行在无文件系统工具的环境：
1. 你**无法 Read/Write 文件**。下方原始职责说明里所有「Read xxx」步骤对应的文件内容，
   已经由调度器代读并附在 user 消息中（按【文件: 路径】分块）。直接基于这些内容工作。
2. **最终只输出一个 JSON 对象**（用 ```json 围栏包裹），不要写任何文件——调度器负责落盘。
3. 输出 JSON 顶层**允许且鼓励**加 `free_notes` 字段（字符串），表达职责 schema 预定义
   维度之外的发现（尤其作者风格档独有的维度）——schema 不是你判断的天花板。
"""

AUTHOR_PROFILE_MISSING_GUARD = """\
# ⚠️ 作者风格档缺失（硬约束）
本项目的作者风格档（作者风格.json / skill）未能读取。按「作者档第一权威·通用规则仅兜底
且不得僭越」纪律：本次审查**不得输出任何风格/文笔/节奏类 finding**（句长/段长/腔调/
对话风格等一律不评）。只允许输出一致性/事实/格式契约类发现。
输出 JSON 须含 "_author_profile_missing": true。
"""


ANCHORED_ADVISORY_CONTRACT = """\
# 🔗 Anchored Advisory 引用契约（R24 W12 Batch-KK · 2026-06-22）
你输出的每条 advisory 类 finding 必须携带 anchor 字段（hard_gate 类选填）：
{
  "anchor": {
    "start_char": <int>,
    "end_char":   <int>,
    "anchor_window": <≤40 CJK 引用片段>,   # 必填·让人立刻能在草稿里找到位置
    "recursive_widen_level": 0   # 0=原 span，1-3=外扩重试层
  }
}
约束：
  · anchor_window 长度上限 40 CJK 汉字（截断不报错）。
  · 不写 anchor 的 advisory 会被下游打 `_anchor_missing=true` 标记，但不阻断。
  · hard_gate 类 finding（一致性/穿帮/契约破损）若无明确 char 位置可省 anchor。
"""


def build_author_profile_block(project_root: Path, max_chars: int = 30000) -> str | None:
    """读项目作者风格档（作者风格.json + 可选 skill 文件）拼注入块。读不到 → None。

    全量注入不截断（feedback_no_token_saving）——max_chars 仅作极端兜底（30k 字符
    ≈ 正常作者档 2-3 倍体量，正常档案永远不会触发）。
    """
    # 两布局（真 distill e2e 抓出）：novels 项目档在 _数据库/·styles 风格库档在项目根
    db = project_root / "_数据库"
    profile_path = db / "作者风格.json"
    if not profile_path.exists():
        profile_path = project_root / "作者风格.json"
        db = project_root
    if not profile_path.exists():
        return None
    try:
        profile_text = profile_path.read_text(encoding="utf-8").strip()
        json.loads(profile_text)  # 校验是合法 JSON（防半截文件混进 prompt）
    except (OSError, json.JSONDecodeError):
        return None

    parts = [f"# 作者风格档（第一权威 · 凡此档规定的维度以此为准，通用规则让位）\n"
             f"```json\n{profile_text}\n```"]
    # 可选 skill（蒸馏写作指导）——项目常拷为 skill_FINAL.md / skill_vN.md
    for cand in sorted(db.glob("skill*.md")) + sorted(db.glob("*skill*.md")):
        try:
            skill_text = cand.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        parts.append(f"# 作者写作 skill（{cand.name} · 与风格档同级权威）\n{skill_text}")
        break
    block = "\n\n".join(parts)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n…[极端兜底截断·正常作者档不应触发]"
    return block


def assemble_user_prompt(params: dict, context_files: list[tuple[str, Path]],
                         extra_blocks: list[str] | None = None) -> str:
    """driver 代读：params 渲染成输入契约行 + 逐文件全文附上（不截断·feedback_no_token_saving）。"""
    lines = ["# 输入契约"]
    for k, v in params.items():
        lines.append(f"{k}: {v}")
    chunks = ["\n".join(lines)]
    for label, path in context_files:
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            content = f"<文件读取失败: {e}>"
        chunks.append(f"【文件: {label}】\n{content}")
    for b in (extra_blocks or []):
        chunks.append(b)
    chunks.append("现在执行你的职责，输出最终 JSON（```json 围栏包裹）。")
    return "\n\n".join(chunks)


# ============ 核心调用 ============
def _json_cont_msg(reason: str, rnd: int) -> str:
    return ("上一条输出因长度上限被截断。请从断点处**继续输出剩余的 JSON**，"
            "不要重复已输出的部分、不要重新开始——保证最终拼起来是一个完整、"
            "嵌套闭合的合法 JSON。")


def _missing_keys(data: dict, required: tuple) -> list[str]:
    return [k for k in required if k not in data]


def judge_call(loader_or_profiles, system: str, user: str, *,
               required_keys: tuple = (),
               max_tokens: int = JUDGE_MAX_TOKENS,
               max_format_retries: int = 2,
               label: str = "judge",
               retry: lt.RetryPolicy | None = None,
               _generate_fn=None) -> tuple[dict, int, str]:
    """单个 judge 的「调用 + 抽取 + 结构校验 + 格式重试」→ (data, retries_used, profile_name)。

    重试边界（硬契约 2）：只在 ① parse 失败（拿到完整文本但非合法 JSON）② required_keys
    顶层缺失（结构破损）时整发重试 ≤ max_format_retries；截断由 transport 续写处理；
    判断内容/枚举值不符**永不**触发重试。
    """
    gen = _generate_fn or lt.generate
    attempt = 0
    last_data: dict = {"_parse_failed": True, "raw_text": ""}
    profile_name = ""
    while attempt <= max_format_retries:
        u = user
        if attempt > 0:
            u += ("\n\n# ⚠️ 格式重试提示（第 {} 次）\n上次输出不是合法/完整的 JSON"
                  "{}。请重新输出**一个完整的 JSON 对象**：第一个非空白字符必须是 {{，"
                  "所有嵌套必须闭合，外面用 ```json 围栏包裹。"
                  .format(attempt,
                          f"（顶层缺键: {_missing_keys(last_data, required_keys)}）"
                          if not last_data.get("_parse_failed") else ""))
        result = gen(loader_or_profiles, system, u,
                     max_tokens=max_tokens,
                     temperature=JUDGE_TEMPERATURE,
                     response_format_json=True,
                     cont_msg_builder=_json_cont_msg,
                     retry=retry,
                     label=label)
        profile_name = result.profile.name
        data = lt.parse_json_loose(result.text)
        last_data = data
        if not data.get("_parse_failed") and not _missing_keys(data, required_keys):
            return data, attempt, profile_name
        attempt += 1
    return last_data, attempt - 1, profile_name


def run_judge(agent_name: str, project_root: str | Path, *,
              params: dict,
              context_files: list[tuple[str, Path]] | None = None,
              extra_blocks: list[str] | None = None,
              output_path: str | Path | None = None,
              secondary_output_path: str | Path | None = None,
              loader: GenModelLoader | None = None,
              agents_dir: Path | None = None,
              retry: lt.RetryPolicy | None = None,
              required_keys: tuple | None = None,
              _generate_fn=None) -> JudgeOutcome:
    """跑一个判断 agent 端到端：装配 prompt → judge_call → 按 failure_policy 落盘/抛错。

    output_path：显式传则覆盖 spec.output_template（orchestrator 从 plan 模板
    judge_report_path 字段传入——命名学外移，不在本模块拼路径变体）。
    """
    spec = AGENT_SPECS.get(agent_name)
    if spec is None:
        raise KeyError(f"未注册的判断 agent: {agent_name}（注册表: {list(AGENT_SPECS)}）")
    # per-call 结构键覆盖（轮次10：outline-planner 多模式输出形态不同·spec 注释承诺的
    # 「结构校验交模式各自的调用点传」在此兑现——plan step 可声明 judge_required_keys）。
    req_keys = tuple(required_keys) if required_keys is not None else spec.required_keys
    project_root = Path(project_root)
    # token ledger（一人公司·BYOK 看烧多少钱）：judge 也走 gen-model·设账本路径让其 token 计入（已设则尊重）
    import os as _os
    _os.environ.setdefault("RUOYU_TOKEN_LEDGER",
                           str(project_root / "_数据库" / ".token_ledger.jsonl"))

    # —— system 装配：适配头 + .md 原文 + 作者档（硬契约 1） + anchored advisory 契约 ——
    system_parts = [ADAPTER_HEADER, load_agent_system_prompt(agent_name, agents_dir)]
    author_missing = False
    if spec.needs_author_profile:
        block = build_author_profile_block(project_root)
        if block:
            system_parts.append(block)
        else:
            author_missing = True
            system_parts.append(AUTHOR_PROFILE_MISSING_GUARD)
    # R24 W12 Batch-KK: advisory 必填 anchor·hard_gate 选填·不阻断
    system_parts.append(ANCHORED_ADVISORY_CONTRACT)
    system = "\n\n".join(system_parts)

    user = assemble_user_prompt(params, context_files or [], extra_blocks)

    profiles = loader or GenModelLoader()
    try:
        data, retries, profile_name = judge_call(
            profiles, system, user,
            required_keys=req_keys,
            max_tokens=spec.max_tokens,
            label=f"judge:{agent_name}",
            retry=retry,
            _generate_fn=_generate_fn)
    except lt.TransportExhausted as e:
        if spec.failure_policy == "block":
            raise JudgeBlockedError(
                f"{agent_name} 全链调用失败且为 block 级（输出喂状态机·不可静默透传）："
                f"{e}") from e
        data = {"_parse_failed": True, "_degraded": True,
                "_transport_exhausted": [f"{n}: {r}" for n, r in e.failures]}
        retries, profile_name = 0, ""

    ok = not data.get("_parse_failed") and not _missing_keys(data, req_keys)
    degraded = False
    if not ok:
        if spec.failure_policy == "block":
            raise JudgeBlockedError(
                f"{agent_name} 输出结构破损（重试 {retries} 次后仍 "
                f"parse_failed={data.get('_parse_failed', False)} / 缺键="
                f"{_missing_keys(data, req_keys)}）——block 级不可静默透传，"
                f"修复后重跑本步。")
        degraded = True
        data.setdefault("_degraded", True)
        logger.warning(f"{agent_name} 降级（soft·该 cluster 少一条顾问意见·不阻断）")

    if author_missing:
        data["_author_profile_missing"] = True

    # R7 W2 Batch-E：style-bias swap-check（advisory · 默认 off · 不改主判决·只挂元数据）
    try:
        bias_meta = style_bias_swap_check(
            agent_name, system, user, data, profiles,
            required_keys=req_keys, max_tokens=spec.max_tokens,
            _generate_fn=_generate_fn)
        if bias_meta.get("mode") != "off" or bias_meta.get("agent_eligible"):
            data["_style_bias_check"] = bias_meta
    except Exception as _e:    # noqa: BLE001
        logger.debug(f"style_bias_swap_check 异常·忽略: {_e}")

    # —— 落盘（driver 代写·judge 无 Write） ——
    written: Path | None = None
    if output_path:
        written = Path(output_path)
        # 🔴 防御（真 end-to-end 暴露）：未解析的 <placeholder>（如 <round>）含 Windows 非法
        # 文件名字符 < >（Errno22）。绝不带它落盘——清晰报错而非 _atomic_write 里晦涩 OSError。
        if "<" in str(written) or ">" in str(written):
            raise ValueError(
                f"judge 输出路径含未解析占位符（< >·Windows 非法文件名）：{written}"
                f"——orchestrator default_judge_dispatch 应已解析 {agent_name} 的 output_template")
        written.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(written, data)
        # voice-checker 双载体：brief 内嵌 judge_report 时平铺一份 JudgeReport
        if secondary_output_path and isinstance(data.get("judge_report"), dict):
            sp = Path(secondary_output_path)
            sp.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(sp, data["judge_report"])

    return JudgeOutcome(agent=agent_name, ok=ok, data=data, output_path=written,
                        degraded=degraded, author_profile_missing=author_missing,
                        profile_name=profile_name, retries=retries)


# ════════════════════════════════════════════════════════════════
# R7 W2 Batch-E：LLM-judge style bias mitigation（去位置/呈现序偏 · advisory · 不阻断）
# ════════════════════════════════════════════════════════════════
# 调研锚（R7-W1·LitBench/HelpSteer）：LLM-judge 输出受 prompt 中候选呈现顺序影响（position bias）
# 与作者风格偏好（style bias）。缓解：把 user prompt 里的文件 chunk 顺序反转后再判一次，
# 对比两次输出关键字段的差异 → 显著不对称 = style_bias_suspected advisory（**不阻断**·让 audit_hub
# 后续按 advisory 处理）。**默认 off**（额外 1 次 API 调用·按需开）；shadow=只标 _swap_check_skipped。
# 北极星⑤：不引入新 hard_gate · 只在原报告挂 _style_bias_check 元数据段。

_BIAS_MODE_ENV = "JUDGE_STYLE_BIAS_SWAP_MODE"
_BIAS_DELTA_THRESHOLD = 0.30   # verdict 等核心字段差异占比 > 30% 即标 suspected
_BIAS_ELIGIBLE_AGENTS = (
    # 风格类裁决 · 最易受 style bias 影响 · 数值/枚举 judge 不在内（重判预算贵）
    "novel-voice-checker",
    "novel-reading-reflector",
    "novel-validator-checker",
)


def _bias_mode() -> str:
    """env JUDGE_STYLE_BIAS_SWAP_MODE: off(默认·零额外调用) / shadow(只记不重判) / active(真重判)。"""
    import os as _os
    m = (_os.environ.get(_BIAS_MODE_ENV) or "off").strip().lower()
    return m if m in ("off", "shadow", "active") else "off"


def make_swapped_user(user: str) -> str:
    """把 user prompt 里的【文件: ...】chunk 顺序反转（保留输入契约头+尾的「输出 JSON」提示）。

    用于 swap-check：同样的内容、不同的呈现顺序 → 真鲁棒的裁决两次结果应一致。
    """
    if "【文件:" not in user:
        return user + "\n\n# 位置偏校验提示\n（已切换素材呈现顺序·结论应与正向一致）"
    # 分块：第一段=输入契约（无【文件:】头）；末段=「现在执行你的职责...」尾巴。
    parts = user.split("\n\n")
    head: list[str] = []
    file_chunks: list[str] = []
    tail: list[str] = []
    # 头：连续若干非文件块
    i = 0
    while i < len(parts) and not parts[i].startswith("【文件:"):
        head.append(parts[i]); i += 1
    # 文件块连续段
    while i < len(parts) and parts[i].startswith("【文件:"):
        file_chunks.append(parts[i]); i += 1
    # 尾：剩余
    while i < len(parts):
        tail.append(parts[i]); i += 1
    if len(file_chunks) < 2:
        return user + "\n\n# 位置偏校验提示\n（单文件块无可反转·结论应稳定）"
    swapped = "\n\n".join(head + list(reversed(file_chunks)) + tail)
    return swapped + "\n\n# 位置偏校验提示\n（已切换素材呈现顺序·结论应与正向一致）"


def _bias_signature(data: dict) -> dict:
    """从 judge 输出抽稳健签名（verdict + violations 计数 + grade）·NaN/缺字段安全。"""
    if not isinstance(data, dict):
        return {}
    sig = {}
    for k in ("verdict", "overall_grade", "grade", "decision"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            sig[k] = v.strip().lower()
            break
    vs = data.get("violations")
    if isinstance(vs, list):
        sig["violation_count"] = len(vs)
    nis = data.get("new_issues_this_round")
    if isinstance(nis, list):
        sig["new_issue_count"] = len(nis)
    return sig


def _bias_delta(sig_a: dict, sig_b: dict) -> dict:
    """两签名差异：verdict 不同记 1.0；计数差 / max(1, mean) 归一化。"""
    if not sig_a or not sig_b:
        return {"asymmetry": 0.0, "reason": "empty_signature"}
    diffs: list[float] = []
    detail: dict = {}
    for k in ("verdict", "overall_grade", "grade", "decision"):
        if k in sig_a and k in sig_b:
            d = 0.0 if sig_a[k] == sig_b[k] else 1.0
            diffs.append(d)
            detail[k] = {"a": sig_a[k], "b": sig_b[k], "diff": d}
            break
    for k in ("violation_count", "new_issue_count"):
        if k in sig_a and k in sig_b:
            a, b = sig_a[k], sig_b[k]
            denom = max(1.0, (a + b) / 2.0)
            d = abs(a - b) / denom
            diffs.append(min(1.0, d))
            detail[k] = {"a": a, "b": b, "norm_diff": round(min(1.0, d), 3)}
    asym = round(sum(diffs) / len(diffs), 3) if diffs else 0.0
    return {"asymmetry": asym, "detail": detail,
            "style_bias_suspected": asym > _BIAS_DELTA_THRESHOLD}


def style_bias_swap_check(agent_name: str, system: str, user: str,
                          baseline_data: dict, profiles_or_loader, *,
                          required_keys: tuple = (),
                          max_tokens: int = JUDGE_MAX_TOKENS,
                          _generate_fn=None) -> dict:
    """对已完成 judge 跑一次「素材顺序反转」对照·返回 _style_bias_check 元数据段。

    返回值始终是 advisory 字典（绝不抛错·绝不阻断流水线）：
      · mode: off / shadow / active
      · agent_eligible: bool
      · style_bias_suspected: bool
      · asymmetry: 0-1
      · baseline_signature / swapped_signature
    """
    mode = _bias_mode()
    out = {"mode": mode, "agent_eligible": agent_name in _BIAS_ELIGIBLE_AGENTS,
           "style_bias_suspected": False, "asymmetry": 0.0}
    if mode == "off" or agent_name not in _BIAS_ELIGIBLE_AGENTS:
        return out
    if mode == "shadow":
        out["_note"] = "shadow·只标 eligible 不重判（零额外 API 成本）"
        return out
    # active：真重判一次
    try:
        swapped_user = make_swapped_user(user)
        swapped_data, _, _ = judge_call(
            profiles_or_loader, system, swapped_user,
            required_keys=required_keys, max_tokens=max_tokens,
            max_format_retries=1, label=f"judge:{agent_name}:bias_swap",
            _generate_fn=_generate_fn)
    except Exception as e:    # noqa: BLE001 — 永不让 swap-check 拖崩主链
        out["_note"] = f"swap-check 调用失败·降级（{type(e).__name__}）"
        return out
    sig_a = _bias_signature(baseline_data)
    sig_b = _bias_signature(swapped_data)
    delta = _bias_delta(sig_a, sig_b)
    out.update({
        "baseline_signature": sig_a,
        "swapped_signature": sig_b,
        "asymmetry": delta["asymmetry"],
        "style_bias_suspected": delta.get("style_bias_suspected", False),
        "detail": delta.get("detail", {}),
    })
    if out["style_bias_suspected"]:
        out["_advisory"] = ("style_bias_suspected：正反呈现序裁决差异显著 > "
                            f"{_BIAS_DELTA_THRESHOLD}·建议人工抽核·绝不阻断")
    return out


def _atomic_write_json(path: Path, data: dict):
    try:
        from atomic_json import atomic_write_json as _aw
        _aw(path, data)
    except ImportError:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


# ============ CLI（单 judge 手动跑/调试） ============
def main():
    import argparse
    ap = argparse.ArgumentParser(description="判断层统一入口（程序驱动 M2）")
    ap.add_argument("agent", choices=sorted(AGENT_SPECS))
    ap.add_argument("project", help="项目根（workspace/novels/<书名>）")
    ap.add_argument("--param", action="append", default=[],
                    help="输入契约 KEY=VALUE（可多次）")
    ap.add_argument("--file", action="append", default=[],
                    help="代读文件 LABEL=PATH（可多次）")
    ap.add_argument("--output", help="输出 JSON 落盘路径")
    args = ap.parse_args()

    params = dict(kv.split("=", 1) for kv in args.param)
    files = [tuple(kv.split("=", 1)) for kv in args.file]
    outcome = run_judge(args.agent, args.project, params=params,
                        context_files=[(l, Path(p)) for l, p in files],
                        output_path=args.output)
    from log_util import print_result_json
    print_result_json({"agent": outcome.agent, "ok": outcome.ok,
                       "degraded": outcome.degraded,
                       "output": str(outcome.output_path),
                       "profile": outcome.profile_name})
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
