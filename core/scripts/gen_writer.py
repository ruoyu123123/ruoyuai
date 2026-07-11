#!/usr/bin/env python3
"""
gen_writer.py — Gen-Model 分段润色引擎（v29 · OpenAI 兼容协议 /v1/chat/completions）

v29 架构（2026-07-11 用户定调「所有创作路线转向 Claude 自身创作内容 + gemini 润色」）：
  step 2a  novel-writer agent（Claude 亲笔）逐场景写作 →
           章节/cluster_<key>_draft/claude_scenes/scene_*.txt + changes_claude.json
  step 2b  本脚本：
             python gen_writer.py --project "workspace/novels/<book>" --cluster 6
           → 发现 claude_scenes/ → 逐场景段调 gemini 按风格档等体量重写润色
             （段级字数守恒带 [0.85, 1.30] · 超界带字数指令重试 1 次）
           → 拼接出终稿 cluster_<key>_draft.txt（文件名不变 · 下游 step3-7 零改动）
           → changes.json = Claude self_eval/waivers + 本脚本确定性遥测合并

实验依据（workspace/_temp_research/四组生成对比_20260711 · memory
project_4group_generation_comparison_2026_07_11）：cluster 级 Claude 草稿+gemini 分段润色
双通道最优；万字整体润色三连败、分段守恒一次成功；gen-model 从零生成+多轮扩写=套话
+设定漂移 → 从零生成路径已整体清除（不兼容不降级 · 缺 Claude 草稿即 [FATAL]）。

🔴 changes.json 仅承载创作期自评（self_eval / waivers · Claude step 2a 产）
+ 确定性遥测（word_count_cjk / length_telemetry / polish 守恒留痕）；cluster 级 factual 状态
（角色 / 道具 / 关系 / locked_facts / 伏笔）由 Claude（novel-archivist / foreshadower）
读正文梳理 → apply_archive.py 确定性回库，writer 链不自报任何 factual 状态。

配置：参见 .env 中 GEN__<name>__* 字段 + GEN_MODEL_ACTIVE。
管理：python core/scripts/gen_model.py list / switch / show / add
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from frozen_util import child_python  # frozen-aware 子解释器（M4·dev=no-op）
from datetime import datetime
from pathlib import Path

# 让本脚本可独立运行（同目录 import）
sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import (  # noqa: E402
    GenModelLoader,
    GenModelConfigError,
    GenModelExhaustedError,
    PromptTooLargeError,
    Profile,
    reasoning_extra_body,
)
import chapter_io as cio  # noqa: E402 · CJK 计数 + changes schema 规范化权威口径
import cluster_lookup  # noqa: E402 · cluster_id 归一化（int 6 ↔ "cluster_006" ↔ "6"）
from atomic_json import atomic_write_text  # noqa: E402 · 2026-06-13 草稿/CHANGES 产物原子落盘（崩溃不留半截）
import snippet_seed  # noqa: E402 · 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量）
from log_util import get_logger, info, debug, warning, error  # noqa: E402
# 🔴 2026-06-28 伏笔明暗线隔离：复用 build_manifest 的明暗线过滤为单一真理源——
#   埋设侧 _sanitize_foreshadowing_to_plant 剥 hidden_payoff（写手只见 surface_clue·当普通细节埋）；
#   揭晓侧 _resolve_foreshadowing_to_callback 仅 trigger_cluster==当前块才暴露 hidden_payoff + reveal_directive。
# 根治 gen_writer 直读 事件簇.json 把未到触发的暗线秘密 json.dumps 进 writer prompt（绕过 build_manifest 过滤）。
# 🔴 2026-06-28 写手信息隔离：复用 build_manifest 的 _sanitize_character_card 为单一真理源——
#   根治 gen_writer 直读 人物卡.json 原文 json.dump 进 writer prompt（角色未到 concealed_until_cluster
#   的 true_role / false_hero / 与反派灰色合作 / ghost.wound reveal / knowledge.will_learn 未来知识
#   全裸奔给 gemini）。注入前对每张卡跑该函数字段级脱敏，再 re-serialize。
from build_manifest import (  # noqa: E402
    _sanitize_foreshadowing_to_plant as _bm_sanitize_fs_plant,
    _resolve_foreshadowing_to_callback as _bm_resolve_fs_callback,
    _sanitize_character_card as _bm_sanitize_character_card,
    # 🔴 2026-06-29 对白即行动dialogue_objectives注入：复用 build_manifest 隔离门控为单一真理源——
    #   闭合 gen_writer 直读 事件簇.json 把 scene_storyboard[*].dialogue_objectives[*].what_unsaid
    #   未到期 hidden 伏笔(标 reveal_cluster) json.dumps 进 writer prompt 的剧透口。
    _sanitize_dialogue_objectives as _bm_sanitize_dialogue_objectives,
)

logger = get_logger(__name__)


# ============ 依赖检查 ============
def check_deps():
    missing = []
    try:
        import openai  # noqa
    except ImportError:
        missing.append('openai')
    try:
        from dotenv import load_dotenv  # noqa
    except ImportError:
        missing.append('python-dotenv')
    if missing:
        logger.error(f" 缺少依赖: {missing}")
        logger.error(f"  请运行: pip install {' '.join(missing)}")
        sys.exit(2)


# ============ Max tokens 解析 ============
def load_model_capabilities_cache() -> dict:
    """加载 model_probe.py 写出的能力缓存"""
    try:
        from frozen_util import user_data_dir as _udd
        cache_path = _udd() / '.claude' / '.model_capabilities.json'
    except Exception:
        cache_path = Path(__file__).parent.parent.parent / '.claude' / '.model_capabilities.json'
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def resolve_max_tokens(profile: Profile) -> tuple[int, str]:
    """决定 max_tokens 的优先级：
       1. profile.max_tokens 显式（最高）
       2. 缓存的 recommended_max_tokens_for_writing
       3. 保守默认值 16000
       返回 (max_tokens, source)
    """
    if profile.max_tokens is not None:
        return profile.max_tokens, 'profile_explicit'

    cache = load_model_capabilities_cache()
    caps = cache.get('model_capabilities', {}).get(profile.model)
    if caps:
        return caps.get('recommended_max_tokens_for_writing', 16000), f"cache:{caps.get('source','unknown')}"

    return 16000, 'default_fallback_16k_NO_PROBE_YET'


# ============ v29 helpers ============
def _infer_cluster_start_ch(project_root: Path, cluster_id: int) -> int:
    """v27 freestyle：从事件簇.json + 已写章节推导 cluster 起始章号

    优先级：
    1. 事件簇.json.clusters[N].ch_start（cluster-first schema）
    2. 上一 cluster 末章 + 1（从 章节/ 目录扫）
    3. cluster_001 = 1 兜底
    """
    db = project_root / '_数据库'
    ec_path = db / '事件簇.json'
    if ec_path.exists():
        try:
            ec = json.loads(ec_path.read_text(encoding='utf-8'))
            for c in ec.get('clusters', []):
                cid_raw = c.get('cluster_id', '')
                m = re.search(r'(\d+)', str(cid_raw))
                if m and int(m.group(1)) == cluster_id:
                    if c.get('ch_start'):
                        return int(c['ch_start'])
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    # 兜底：扫 章节/第NNN章/ 找最大章号 + 1
    chapters_dir = project_root / '章节'
    max_ch = 0
    if chapters_dir.exists():
        for p in chapters_dir.iterdir():
            m = re.match(r'第(\d+)章', p.name)
            if m:
                max_ch = max(max_ch, int(m.group(1)))
    if cluster_id == 1:
        return 1
    return max_ch + 1 if max_ch > 0 else 1


def _resolve_manifest_research_cache_path(db: Path, manifest_dict: dict) -> Path | None:
    """Resolve event_cluster_context.research_ref.cache_path inside the project DB.

    The path may be absolute, project-relative ("_数据库/.research_cache/x.md"),
    DB-relative (".research_cache/x.md"), or a legacy bare filename. Directories
    are advisory only and do not identify a single bound research cache.
    """
    if not isinstance(manifest_dict, dict):
        return None
    ctx = manifest_dict.get("event_cluster_context")
    if not isinstance(ctx, dict):
        return None
    ref = ctx.get("research_ref")
    if not isinstance(ref, dict):
        return None
    raw = ref.get("cache_path")
    if not isinstance(raw, str) or not raw.strip():
        return None

    raw_path = Path(raw.strip())
    project_root = db.parent.resolve()
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.extend([
            project_root / raw_path,
            db / raw_path,
            db / ".research_cache" / raw_path.name,
        ])

    for cand in candidates:
        try:
            resolved = cand.resolve()
        except (OSError, RuntimeError):
            continue
        try:
            resolved.relative_to(project_root)
        except ValueError:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _load_research_cache_for_cluster(db: Path, cluster_id: int, manifest_dict: dict) -> str:
    """Load cluster-bound research cache first, then legacy fallbacks.

    This keeps the creative chain single-source: novel-researcher writes a cache,
    the chosen cluster stores research_ref, build_manifest exposes it, and writer
    consumes that exact file. The old latest-file fallback remains only for
    historical projects without manifest-bound references.
    """
    bound = _resolve_manifest_research_cache_path(db, manifest_dict)
    if bound:
        return read_text(bound)

    cache_dir = db / ".research_cache"
    if not cache_dir.exists():
        return ""

    caches = sorted(cache_dir.glob(f"inspiration_cluster_{cluster_id:03d}_*.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    if not caches:
        caches = sorted(cache_dir.glob("inspiration_*.md"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return read_text(caches[0]) if caches else ""


# ============ Prompt 组装 ============
# 🔴 G4 writer prompt 瘦身（2026-06-23）：只有「写作工艺类」feedback 才注入 writer system prompt。
# 根因——_collect_feedback_rules() 此前把 memory 里**所有** type=feedback 文件（实测 29 个，每文件
# 截 2500 字 ≈ 45k chars）一股脑塞进 writer system，其中 ~22 个是流程/基建/蒸馏/测试/合规类 lesson
# （default_no_step_skipping / real_api_tests / verify_stderr / runtime_self_learning …），对「生成正文」
# 零价值，却随 211 轮 upgrade 不断沉淀、把 system prompt 从 ~20k 撑到 67k → 超 elysiver max_prompt_chars
# 直接跳过不调用（G3 真 API e2e 抓出的卡死根因）。
# 北极星⑤：砍的全是与创作无关的流程 lesson；作者档第一权威 + 写作工艺禁令完整保留。
# 扩展口径：白名单 ∪ 任何带 frontmatter `writer_relevant: true` 的 feedback（新增写作工艺 lesson
# 只要标这一行就会被注入，无需改本表）。
_WRITER_RELEVANT_FEEDBACK = frozenset({
    "feedback_no_screenplay_stage_directions_in_novels",
    "feedback_one_sentence_per_paragraph",
    "feedback_dialogue_quote_unicode_distinction",
    "feedback_dialogue_quote_distill_bug",
    "feedback_inverted_modifier_sentence_mold_overuse",
    "feedback_smart_side_characters_no_dumbing_down",
    "feedback_author_goldstandard_comparison_gate",
})


def _is_writer_relevant_feedback(stem_or_slug: str, text: str = "") -> bool:
    """该 feedback 文件是否「写作工艺类」（应注入 writer 生成 prompt）。

    判定：① 归一化后命中 _WRITER_RELEVANT_FEEDBACK 白名单；或
         ② 文件 frontmatter 显式 `writer_relevant: true`（可扩展 opt-in·新工艺 lesson 自助挂载）。
    其余（流程/基建/蒸馏/测试/合规 lesson）→ False·不进 writer prompt（北极星⑤·与生成无关）。
    """
    norm = (stem_or_slug or "").strip().replace("-", "_")
    if norm in _WRITER_RELEVANT_FEEDBACK:
        return True
    # 🔴 2026-06-23 fix：只在 frontmatter 块内判 `writer_relevant: true`，**不扫正文**。
    # 否则正文里只是「文字解释 opt-in 机制」(如 feedback_writer_prompt_bloat_feedback_whitelist
    # 的正文写了 `frontmatter `writer_relevant: true` opt-in`) 会被误判成 writer-relevant →
    # 该流程/基建 lesson 反被注入 writer prompt(正是 G4 要堵的膨胀)。
    if text:
        fm = re.match(r"\s*---\s*\n(.*?)\n---", text, re.DOTALL)
        scope = fm.group(1) if fm else ""
        if re.search(r"^\s*writer_relevant:\s*true\s*$", scope, re.MULTILINE):
            return True
    return False


def _collect_feedback_rules() -> str:
    """L3 防御：自动扫 ~/.claude/projects/.../memory/feedback_*.md，
    抽取 type=feedback **且写作工艺类**的全局规则段，注入 writer system prompt。

    设计目标：让 writer 在每段生成时都看到写作工艺禁令（不依赖主代理记得）。
    抽取策略：取 lesson 文件的「## 规则」段（如有），否则取文件头部 1500 字。

    🔴 G4 瘦身（2026-06-23）：只注入 _is_writer_relevant_feedback 通过的文件（写作工艺类），
    流程/基建/蒸馏/测试/合规 lesson 一律跳过——它们对生成正文零价值，却是 system prompt
    从 20k 膨到 67k 的主因。详见 _WRITER_RELEVANT_FEEDBACK。

    🔴 frozen fallback（2026-06-13）：开发机 memory 路径在 exe 用户机上不存在 →
    本层此前整层静默为空。home miss/为空时改读随 exe 出货的汇编
    lessons/global_feedback_rules.md（_collect_feedback_rules_bundle_fallback）。
    home 路径优先（开发机行为不变）。
    """
    try:
        memory_dir = Path.home() / ".claude" / "projects" / "D--Desktop-ruoyuai" / "memory"
        rules_chunks = []
        if memory_dir.exists():
            for f in sorted(memory_dir.glob("feedback_*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                # frontmatter 检查 type=feedback
                if "type: feedback" not in text:
                    continue
                # 🔴 G4 瘦身：只注入写作工艺类（白名单 ∪ writer_relevant:true）
                if not _is_writer_relevant_feedback(f.stem, text):
                    continue
                # 抽 "## 规则" 段或文件正文头部
                m = re.search(r"##\s*规则[\s\S]*?(?=\n##\s|\Z)", text)
                chunk = m.group(0) if m else text[text.find("---\n", 5) + 4:]
                chunk = chunk.strip()[:2500]
                if not chunk:
                    continue
                rules_chunks.append(f"### 来自 {f.stem}\n\n{chunk}")
        if not rules_chunks:
            return _collect_feedback_rules_bundle_fallback()
        header = "# 🔴 全局写作工艺 feedback 规则（自动注入 · 来自 memory/feedback_*.md）\n\n"
        header += "以下是历史用户反馈沉淀的写作工艺禁令，写作时**逐条遵守**。违反 = 出货后被打回 + lesson 复发。\n\n"
        return header + "\n\n---\n\n".join(rules_chunks)
    except Exception:
        return ""


def _collect_feedback_rules_bundle_fallback() -> str:
    """home memory miss/为空 → 读随 exe 出货的汇编 lessons/global_feedback_rules.md。

    汇编文件由 assemble_global_feedback_rules.py 机械产出（自带「逐条遵守」header 框架行，
    与 home 路径注入形态等价）；frozen_util.resource_path 定位（frozen=_MEIPASS·dev=仓库根）。

    🔴 G4 瘦身（2026-06-23）：与 home 路径同口径——只把汇编里**写作工艺类**的 `## feedback-xxx`
    节注入 writer prompt（流程/测试/蒸馏类节跳过）。汇编**文件本身不改**（build_manifest 的
    digest 仍消费全 13 节·只是 gen_writer 注入端过滤）。解析失败 → 兜底返回全文（不退化到 0 注入）。
    """
    try:
        from frozen_util import resource_path
        p = resource_path("core", "claude-home", "lessons", "global_feedback_rules.md")
        if not p.exists():
            return ""
        full = p.read_text(encoding="utf-8").strip()
        # 按 "## feedback-xxx" 节头切（保留捕获组）→ [preamble, h1, body1, h2, body2, ...]
        parts = re.split(r"(?m)^(##\s+feedback[-_][^\n]*)$", full)
        if len(parts) < 3:
            return full  # 无可识别节头 → 兜底全文
        kept = [parts[0].rstrip()]
        for i in range(1, len(parts), 2):
            heading = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            slug = heading.strip().lstrip("#").strip()  # feedback-xxx
            if _is_writer_relevant_feedback(slug):
                kept.append((heading + body).rstrip())
        if len(kept) <= 1:
            return full  # 解析后一条工艺节都没匹配（口径异常）→ 兜底全文
        return "\n\n".join(kept).strip()
    except Exception:
        return ""


def read_text(p: Path, limit_chars: int = None) -> str:
    if not p.exists():
        return f"[WARN] 文件不存在: {p}"
    t = p.read_text(encoding='utf-8')
    if limit_chars and len(t) > limit_chars:
        t = t[:limit_chars] + f"\n... [truncated at {limit_chars} chars]"
    return t


def _load_manifest_once(manifest_path: Path, _preloaded: dict | None = None) -> dict | None:
    if _preloaded is not None:
        return _preloaded
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None


def _build_style_fingerprint_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """从 manifest.author_style_fingerprint 抽显式量化指令拼成 writer prompt 段。

    L1a 升格消费（2026-05-31）：build_manifest 在 PROFILE_INJECT_MODE=active 下注入
    多维量化风格指纹（句长/段长/单句独行/标点/虚词/签名搭配 + 显式 directives 文案）。
    本函数把 directives 升到 prompt 前部醒目位置 —— 实证「显式数值目标」> 让模型看样本自己悟。

    返回值：
      · 指纹缺失 / 为 None（PROFILE_INJECT_MODE=off/shadow）/ 无 directives → ""（不注入·零回归）。
      · 有 directives → 拼成「作者量化风格指纹」段（advisory · 标注可校准偏离·北极星⑤不硬锁）。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    fp = m.get('author_style_fingerprint')
    if not isinstance(fp, dict):
        return ""
    directives = fp.get('directives') or []
    if not directives:
        return ""
    src = fp.get('source', 'author_profile')
    n_ch = fp.get('n_chapters')
    src_note = f"（数据源 {src}" + (f" · {n_ch} 章聚合" if n_ch else "") + "）"
    lines = [
        "## 🎯 作者量化风格指纹（写作时显式校准的多维目标硬数字 · advisory）",
        "",
        f"以下是从作者风格档蒸馏出的**量化风格目标**{src_note}。实证表明把这些数值**显式告知你**"
        "比让你看样本自己悟更有效。**逐条对照校准你的节奏**——它们是目标不是硬锁，",
        "遇到本场景必要的偏离（如高潮处句长骤变）可偏离，但默认贴合：",
        "",
    ]
    for d in directives:
        lines.append(f"- {d}")
    return "\n".join(lines)


def _rolling_anchor_inject_mode() -> str:
    """rolling style anchor 注入开关（env ROLLING_ANCHOR_INJECT_MODE · 默认 shadow）。

    动态文风锚（build_manifest._collect_rolling_style_anchor · 第2轮治 D 级长程文风退化：用本书已写得
    最像作者的 1-2 段对抗回归均值退化成通用 LLM 腔）此前只以 raw JSON 躺在 manifest dump 中段
    （lost-in-the-middle dead zone · writer 难识别为写作目标）。本开关把它升格到生成点近邻风格锚区
    （同族 style_fp/rhythm/seed）。默认 shadow（位置升格的文风改善效果需 gen-model A/B 定论 · 先影子）。
    """
    return (os.environ.get("ROLLING_ANCHOR_INJECT_MODE") or "shadow").strip().lower()


def _build_rolling_anchor_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """从 manifest.rolling_style_anchor 抽动态文风锚片段拼 writer prompt 段（dead-zone → 生成点近邻升格）。

    返回值：
      · ROLLING_ANCHOR_INJECT_MODE != active / 字段缺/None / 无 anchors / snippet 全空 → ""（零回归）。
      · 有 anchors → 拼「本书文风动态锚」段（advisory 软牵引 · 北极星⑤不硬锁 · 每锚 snippet）。
    """
    if _rolling_anchor_inject_mode() != "active":
        return ""
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    rsa = m.get('rolling_style_anchor')
    if not isinstance(rsa, dict):
        return ""
    anchors = rsa.get('anchors') or []
    if not anchors:
        return ""
    doc = (rsa.get('_doc') or
           "下面是本书已写片段中最贴作者文风的段落——写下一块时句长节奏/虚词标点/字组笔迹向它们看齐"
           "（顾问软牵引·不限定写什么内容·不硬锁写法）。")
    lines = ["## 🪢 本书文风动态锚（向已写得最像作者的片段看齐 · advisory）", "", doc, ""]
    n = 0
    for i, a in enumerate(anchors, 1):
        if not isinstance(a, dict):
            continue
        snip = (a.get('snippet') or "").strip()
        if not snip:
            continue
        cid = a.get('cluster_id', '?')
        lines.append(f"【锚 {i} · 本书 {cid} 最贴作者段】")
        lines.append(snip)
        lines.append("")
        n += 1
    if n == 0:
        return ""   # snippet 全空 → 不注入（零回归）
    return "\n".join(lines).rstrip()


def _build_rhythm_signature_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """阶段1：从 manifest.author_rhythm_signature 抽序列级节奏指令拼 writer prompt 段。

    与量化指纹（句长/段长·节点静态属性）互补——这是「写了这一拍接下一拍」的序列骨
    （节拍转移/翻转率/张力后段保持度/钩子兑现）。RHYTHM_INJECT_MODE=off/shadow 或无
    指纹 → ""（不注入·零回归）。advisory·作者档第一权威·北极星⑤不硬锁。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    rs = m.get('author_rhythm_signature')
    if not isinstance(rs, dict):
        return ""
    directives = rs.get('directives') or []
    if not directives:
        return ""
    lines = [
        "## 🎵 作者叙事节奏指纹（序列级·写了这拍接下拍的作者习惯 · advisory）",
        "",
        "段长/句长是「一句话长不长」的表层皮；下面是**序列骨**——拍接拍的转移、张力怎么"
        "起伏、钩子隔多久兑现。**别让张力中段就塌**（AI 通病：过早收束，一爽就泄）。"
        "逐条贴合作者基线，必要偏离可偏离：",
        "",
    ]
    for d in directives:
        lines.append(f"- {d}")
    return "\n".join(lines)


def _build_knowledge_gap_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """阶段·D3：从 manifest.knowledge_gap_signature 抽读者-角色信息差三态指令拼 writer prompt 段。

    序列骨之一（与 rhythm 并列）——「读者比角色多知道还是少知道」是悬念/虐心/打脸的底层
    引擎（Sternberg 三态：reader_adv 上帝视角虐心 / reader_disadv 角色优势卖关子 /
    double_blind 双盲悬疑）。KNOWLEDGE_GAP_INJECT_MODE=off/shadow 或无指令 → ""（不注入·
    零回归·切 active 前须过 G3 标注一致性闸 + 消融）。advisory·作者档第一权威·北极星⑤不硬锁。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    kg = m.get('knowledge_gap_signature')
    if not isinstance(kg, dict):
        return ""
    directives = kg.get('directives') or []
    if not directives:
        return ""
    lines = [
        "## 🕳️ 作者信息差主调（读者-角色知识差三态·序列骨 · advisory）",
        "",
        "悬念/虐心/打脸的底层引擎不是「写得多惊险」，是**读者和角色谁多知道一层**——读者"
        "比角色先知道危险（上帝视角虐心）、角色比读者先知道（卖关子）、还是双盲推进（纯悬疑）。"
        "逐条贴合作者基线的信息差分布：",
        "",
    ]
    for d in directives:
        lines.append(f"- {d}")
    return "\n".join(lines)


# 🔴 2026-06-29 角色信息差(per-character belief)
def _build_belief_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """从 manifest.scene_character_knowledge 抽各场景各角色认知边界拼 writer prompt 段（生成层物理 masking）。

    提案核心：重心在生成层注入非检测——让写手按各角色受限认知写，防角色用不该知道的知识穿帮
    （扮猪吃老虎/信息差/悬念底层引擎）。单一真理源 = build_manifest._sanitize_character_belief 投射出的
    scene_character_knowledge（knows[] = learned<=current 的 fact · must_not_reference[] = unaware/未到期负向）。

    默认安全闸（向后兼容·零回归）：无 scene_character_knowledge / 空 / 无 ledger → ""（不注入·今天所有旧书
    无 ledger → 零行为变化）。全 advisory·北极星⑤不硬锁。"""
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    scenes = m.get('scene_character_knowledge')
    if not isinstance(scenes, list) or not scenes:
        return ""
    lines = [
        "## 🧠 角色认知边界（per-character belief · 信息差物理 masking · advisory · 见 H7）",
        "",
        "下面按场景列出每个在场角色【当下知道什么】【绝不能用什么】。**严格按各角色的认知边界写**——",
        "每个角色只能基于自己 `knows[]` 行动/说话；`must_not_reference[]` 是该角色本场不知道的事实，"
        "其言行不得提及/不得基于其行动（角色 A 不知道的事即便 B 知道 ≠ A 知道）。",
        "",
    ]
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        chars = sc.get('characters')
        if not isinstance(chars, dict) or not chars:
            continue
        head = f"### 场景 {sc.get('scene_index')}"
        meta = []
        if sc.get('focal_character'):
            meta.append(f"聚焦视角={sc.get('focal_character')}")
        if sc.get('focalization_mode'):
            meta.append(f"聚焦模式={sc.get('focalization_mode')}")
        if sc.get('knowledge_gap_mode'):
            meta.append(f"信息差模式={sc.get('knowledge_gap_mode')}")
        if meta:
            head += "（" + " · ".join(meta) + "）"
        lines.append(head)
        for cid, cb in chars.items():
            if not isinstance(cb, dict):
                continue
            knows = cb.get('knows') or []
            must_not = cb.get('must_not_reference') or []
            lines.append(f"- **{cid}**")
            if knows:
                kparts = []
                for kf in knows:
                    if not isinstance(kf, dict):
                        continue
                    c = kf.get('content') or kf.get('fact_id') or ""
                    if kf.get('can_speak', True) is False:
                        kparts.append(f"{c}（知道但本场不能说出口·只内心/行动暗示）")
                    else:
                        kparts.append(str(c))
                if kparts:
                    lines.append(f"    - 知道（可基于其行动/言说）：{'；'.join(kparts)}")
            if must_not:
                mparts = []
                for mf in must_not:
                    if not isinstance(mf, dict):
                        continue
                    c = mf.get('content') or mf.get('fact_id') or ""
                    r = mf.get('reason') or ""
                    mparts.append(f"{c}（{r}）" if r else str(c))
                if mparts:
                    lines.append(f"    - 🚫 不知道（绝不提及/不得基于其行动）：{'；'.join(mparts)}")
    # 只有 head + 引言、没有任一角色条目 → 退回空（零回归）
    if len(lines) <= 5:
        return ""
    return "\n".join(lines)


def _build_appraisal_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """🔴 2026-06-29 场景级Appraisal Beat消费（心理 P0·消费 manifest.appraisal_directive 结构化情绪方向卡）。

    升级 EBS appraisal-prose（原 system prompt 一句提示·见下 system 段「情绪经角色个性化评估」）为消费
    build_manifest 注入的结构化情绪方向卡：『这一拍 focal_character 情绪往哪走(derived_emotion 方向) +
    为何(appraisal 评价) + 如何外化(behavior_externalization·动作非情绪词)』+ 情绪余烬(上块强情绪不归零延续)。

    🔴 北极星⑤纪律：情绪靠『事件→评价→动作/细节』落地(appraisal-as-prose)·**不写『他感到X』式情绪词标签**。
    默认安全闸（向后兼容·零回归）：无 appraisal_directive / mode!=on / 无 directive → ""（不注入·旧书零行为变化）。
    全 advisory·北极星⑤不硬锁。"""
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    ad = m.get('appraisal_directive')
    if not isinstance(ad, dict) or ad.get('mode') != 'on':
        return ""
    directive = (ad.get('directive') or "").strip()
    if not directive:
        return ""
    return (
        "## 🎭 场景级 Appraisal 情绪方向卡（心理 P0 · appraisal-as-prose · advisory）\n\n"
        + directive
    )


def _deep_dims_inject_mode() -> str:
    """deep_writing_dims 升格开关（env DEEP_DIMS_INJECT_MODE · 默认 shadow）。

    D1 心理距离/D2 visceral-first/D3 动机弧光创作提示（build_manifest._collect_deep_writing_dims）·
    此前只 raw JSON 躺 manifest dump dead-zone·升格生成点近邻醒目位。默认 shadow（tip 是大段创作提示·
    升格价值 + context 成本权衡需 gen-model A/B·先影子·该字段 producer 端无 env 闸·gen_writer 侧控）。
    """
    return (os.environ.get("DEEP_DIMS_INJECT_MODE") or "shadow").strip().lower()


def _build_deep_dims_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """#4（round2）：从 manifest.deep_writing_dims 抽 D1/D2/D3 创作提示拼 writer prompt 段（dead-zone→升格）。

    D1_psychic_distance（心理距离档位）/D2_visceral_first_emotion（先生理后命名情绪）/D3_motivation_arc
    （动机弧光）各 {label, tip}·升格生成点近邻。DEEP_DIMS_INJECT_MODE != active / 字段缺/空 → ""（零回归）。
    advisory·北极星⑤不硬锁（纯创作提示·无检测无门禁）。
    """
    if _deep_dims_inject_mode() != "active":
        return ""
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    dwd = m.get('deep_writing_dims')
    if not isinstance(dwd, dict):
        return ""
    lines = ["## 🎭 深层创作维度（心理距离 / 情绪顺序 / 动机弧光 · advisory）", ""]
    n = 0
    for key in ("D1_psychic_distance", "D2_visceral_first_emotion", "D3_motivation_arc"):
        dim = dwd.get(key)
        if not isinstance(dim, dict):
            continue
        tip = (dim.get("tip") or "").strip()
        if not tip:
            continue
        label = dim.get("label", key)
        lines.append(f"【{label}】")
        lines.append(tip)
        lines.append("")
        n += 1
    if n == 0:
        return ""
    return "\n".join(lines).rstrip()


def _golden_fewshot_inject_mode() -> str:
    """golden few-shot 注入开关（env GOLDEN_FEWSHOT_INJECT_MODE · 默认 shadow）。

    蒸馏 golden_passages 按 scene_type 选的原作金句段·此前只 raw JSON 躺 manifest dump 中段 dead-zone
    （writer 难识别为写作目标）。本开关升格到生成点近邻 few-shot 段（few-shot 比 zero-shot 提升 23.5x·
    arxiv 2509.14543）。默认 shadow（位置升格的文风改善效果需 gen-model A/B·先影子·该字段 producer 端
    无 env 闸·gen_writer 侧控）。
    """
    return (os.environ.get("GOLDEN_FEWSHOT_INJECT_MODE") or "shadow").strip().lower()


def _build_golden_fewshot_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """#7：从 manifest.distill_golden_few_shot.passages_by_type 抽原作金句段拼 writer few-shot 段（升格）。

    蒸馏库按 scene_type 选的原作金句（模仿句法/节奏·非抄内容）·此前只 raw JSON dead-zone·升格到生成点
    近邻风格锚区。每类取前 1-2 段（passages 已截 800 字）防生成点近邻二次塞爆。
    GOLDEN_FEWSHOT_INJECT_MODE != active / 字段缺/空 → ""（零回归）。advisory·北极星⑤不硬锁。
    """
    if _golden_fewshot_inject_mode() != "active":
        return ""
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    gfs = m.get('distill_golden_few_shot')
    if not isinstance(gfs, dict):
        return ""
    pbt = gfs.get('passages_by_type')
    if not isinstance(pbt, dict) or not pbt:
        return ""
    lines = [
        "## 📜 原作金句 few-shot（模仿句法/节奏·非抄内容 · advisory）",
        "",
        "下面是蒸馏库按本章场景类型选的**作者原作金句段**。模仿它们的句法骨架/节奏/用词色彩"
        "（不是抄内容）——few-shot 范例比抽象描述更能锚住作者笔法：",
        "",
    ]
    n = 0
    for ptype, passages in pbt.items():
        if not isinstance(passages, list) or not passages:
            continue
        picked = [p for p in passages[:2] if isinstance(p, str) and p.strip()]  # 每类前 1-2 段防爆量
        if not picked:
            continue
        label = str(ptype).replace("_passages", "")
        lines.append(f"【{label} 类】")
        for p in picked:
            lines.append(p.strip())
            lines.append("")
        n += 1
    if n == 0:
        return ""
    return "\n".join(lines).rstrip()


def _build_narrative_seq_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """#4：从 manifest.narrative_function_sequence 抽作者签名因果功能链拼 writer prompt 段（结构骨）。

    signature_bigrams（face_slap→gain_reward 等因果转移）比段长/句长表层指纹更深·让连续故事块功能
    转移贴作者签名节奏而非默认 LLM 高频模板（中文网文同质化结构层根因）。NARR_FUNC_SEQ_INJECT_MODE=
    off/shadow 或无指令（build_manifest 控字段 None）→ ""（不注入·零回归）。advisory·作者档第一权威·北极星⑤不硬锁。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    nfs = m.get('narrative_function_sequence')
    if not isinstance(nfs, dict):
        return ""
    directives = nfs.get('directives') or []
    if not directives:
        return ""
    lines = [
        "## 🧬 作者签名叙事功能链（结构骨·连续故事块功能转移看齐作者节奏 · advisory）",
        "",
        "段长/句长指纹是表层，**功能链**是深层——作者在「打脸」后习惯接「获益」还是「再起波澜」，"
        "这种因果功能转移是作者签名（比通用 Save-the-Cat 节拍更专属）。让本块功能节奏贴作者基线：",
        "",
    ]
    for d in directives:
        lines.append(f"- {d}")
    return "\n".join(lines)


def _build_genre_pack_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """阶段3：从 manifest.genre_pack_directives 拼题材专属工艺段（按 genre·advisory）。

    通用维度池(作者层)always-on；题材层(甜宠糖虐/游戏向面板)按 genre 激活。
    GENRE_INJECT_MODE=off/shadow 或 unknown genre → ""（退化纯通用·零回归）。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    gp = m.get('genre_pack_directives')
    if not isinstance(gp, dict):
        return ""
    directives = gp.get('directives') or []
    if not directives:
        return ""
    lines = [f"## 🎭 题材专属工艺（{gp.get('genre','')} · 内容工艺层 · advisory）", "",
             "下面是这个**题材**特有的工艺（爽文的爽点/甜宠的糖虐节拍/游戏向的面板副本）——"
             "通用作者风格之外的题材读者期待。贴合：", ""]
    for d in directives:
        lines.append(f"- {d}")
    return "\n".join(lines)


def _build_debt_ledger_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """R7 Batch-D（2026-06-20）：D7 叙事债务状态卡注入（advisory · 北极星⑤不硬锁）。

    数据源 manifest.debt_ledger_snapshot（cross_cluster_narrative_debt_ledger_aggregate 写）。
    把 book/volume open_debt + advisory_codes 折成一段给 writer 看的债务状态卡——
    让 writer 知道「现在有多少未偿还的叙事债务」+「卷末是否累积过 60%」+「BOOK_MORTGAGE 缺位」。

    NARRATIVE_DEBT_INJECT_MODE=off / 字段缺/空 → ""（不注入·零回归）。advisory。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    snap = m.get('debt_ledger_snapshot')
    if not isinstance(snap, dict):
        return ""
    book = snap.get("book") or {}
    if not book:
        return ""
    book_total = book.get("total_planted", 0)
    book_paid = book.get("total_paid", 0)
    book_open = book.get("open_debt", 0)
    book_ratio = book.get("open_ratio", 0.0)
    codes = snap.get("advisory_codes", []) or []
    vols = snap.get("volumes", []) or []
    lines = ["## 📒 叙事债务账本（D7 · 长篇 stock+flow · advisory）", ""]
    lines.append(
        f"- 全书：planted {book_total} / paid {book_paid} / **open_debt {book_open}** "
        f"（{book_ratio:.0%}）"
    )
    for v in vols:
        if not isinstance(v, dict):
            continue
        lines.append(
            f"- 卷 {v.get('volume')}：planted {v.get('total_planted')} / "
            f"paid {v.get('total_paid')} / open_debt {v.get('open_debt')} "
            f"（{v.get('open_ratio', 0.0):.0%}）"
        )
    if codes:
        lines.append("")
        lines.append("**触发 advisory**：" + "、".join(codes))
        lines.append("- 建议：在本 cluster 偿还 1-2 个最旧的伏笔/秘密（揭露 / 兑现 / 反转）·"
                     "或在卷末安排集中偿还场景·勿在卷末再大量埋新债")
    lines.append("")
    lines.append("> 这是 advisory：让你看见「读者欠的债」当前状态。writer 可自由判断何时偿还，"
                 "但**长期累积不偿 = 烂尾感**；开篇零借债 = 没翻页动力。")
    return "\n".join(lines).rstrip()


def _build_editor_note_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """A4 编辑手记消费指令（2026-07-08·PlotPilot「自然语言比结构分隔符更易融入创作」）。

    数据源 manifest.editor_note（build_manifest 把伏笔/悬置问题/主角状态/涟漪后果/卷方向
    等结构块确定性坍缩成的一段人话手记）。存在时注入手记正文 + 消费指令（软建议汇总·
    可自由取舍）；缺失（素材全空/旧 manifest）→ ""（不注入·零回归）。advisory（北极星⑤）。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    en = m.get("editor_note")
    if not isinstance(en, dict):
        return ""
    note = (en.get("note") or "").strip()
    if not note:
        return ""
    return "\n".join([
        "## 📝 编辑手记（advisory · 软建议汇总）",
        "",
        "manifest.editor_note 是编辑手记式的**软建议汇总**（把到期伏笔/悬置问题/主角状态/"
        "世界余波/本卷方向拢成一段人话）——**可自由取舍**：如果合适可以推进，不必强求，"
        "与你的创作判断或作者风格档冲突时以后者为准。",
        "",
        note,
    ])


def _build_prev_tail_echo_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """A3 前块结尾回响指令（2026-07-07·PlotPilot recent_chapter_context + 回响模板 advisory 化）。

    数据源 manifest.prev_cluster_tail（build_manifest cluster_002+ 注入·上一 cluster 草稿
    末尾原文）。存在时注入回响指令 + 结尾原文引文；缺失（cluster_001 / 前块草稿不存在）→ ""
    （不注入·零回归）。

    引文优先取全量：compressed manifest 会把 >200 字符字符串盲切（manifest_compress
    MAX_STR_LEN）——A3 核心主张=「章末完整保留提升连贯」，截断即失效，故检测到截断标记时
    回未压缩 manifest 取全量 tail_text。advisory：回响方式 writer 自由决定，不必逐句衔接
    （北极星⑤不硬锁）。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    pt = m.get('prev_cluster_tail')
    if not isinstance(pt, dict):
        return ""
    tail = (pt.get('tail_text') or '').strip()
    if not tail:
        return ""
    if '...(+' in tail or 'surprisal精选' in tail:
        try:
            full = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
            full_tail = ((full.get('prev_cluster_tail') or {}).get('tail_text') or '').strip()
            if full_tail:
                tail = full_tail
        except (OSError, json.JSONDecodeError):
            pass
    src = pt.get('source_cluster') or '上一故事块'
    return "\n".join([
        "## 🔗 上一故事块结尾回响（advisory · 开篇衔接）",
        "",
        f"上一故事块结尾原文见 manifest.prev_cluster_tail（{src}·引文如下）——"
        "**本块开篇应对其悬念钩子/情感余韵/场景状态有所回响**"
        "（自由决定回响方式：直接接续 / 时间跳跃后呼应 / 场景残留物 / 情绪延续……"
        "不必逐句衔接·不硬锁写法）。",
        "",
        f"【{src} 结尾原文】",
        tail,
    ])


def _build_decision_principles_section(manifest_path: Path, _preloaded: dict | None = None) -> str:
    """阶段2：从 manifest.author_decision_principles 拼作者思维/人物刻画骨段。

    骨③作者思维(道德滤镜/心理距离/留白)+骨②刻画手法——「作者在 X 情境倾向 Y」的决策
    原则·段长抓不到的骨。DECISION_INJECT_MODE=off/shadow 或无 → ""（零回归）。advisory。
    """
    m = _load_manifest_once(manifest_path, _preloaded)
    if m is None:
        return ""
    dp = m.get('author_decision_principles')
    if not isinstance(dp, dict):
        return ""
    dec = dp.get('author_decision_principles') or {}
    cha = dp.get('characterization_craft') or {}
    if not dec and not cha:
        return ""

    def _render(d: dict) -> list:
        out = []
        for k, v in d.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                sub = "；".join(f"{sk}：{('/'.join(sv) if isinstance(sv, list) else sv)}"
                               for sk, sv in v.items() if sv)
                if sub:
                    out.append(f"- **{k}**：{sub}")
            elif isinstance(v, list) and v:
                items = "；".join(json.dumps(x, ensure_ascii=False) if isinstance(x, dict)
                                 else str(x) for x in v[:5])
                out.append(f"- **{k}**：{items}")
            elif isinstance(v, str) and v.strip():
                out.append(f"- **{k}**：{'/'.join(v) if False else v}")
        return out

    lines = ["## 🧠 作者决策原则 + 人物刻画手法（作者在岔路口怎么选 · advisory）", "",
             "下面不是段长数字，是**作者的思维与刻画手法**——道德滤镜（胜利附带什么代价/对人物"
             "审判还是共情）、心理距离、留白冰山、人物声纹区分。这是这位作者之所以是这位作者的"
             "骨。贴合这些决策倾向去写，别塌回通用腔：", ""]
    lines += _render(dec)
    if cha:
        lines.append("")
        lines.append("**人物刻画手法：**")
        lines += _render(cha)
    # D7-4③：cheat-sheet 走专用纯文本分支（绝不 json.dumps·避免压缩后被吐成 blob 抵消压缩）
    sheet = dp.get('author_decision_cheat_sheet')
    if isinstance(sheet, list) and sheet:
        lines.append("")
        lines.append("**作者决策 cheat-sheet（情境→作者选｜vs 通用腔·advisory 写之前对齐·可偏离）：**")
        for c in sheet:
            if isinstance(c, dict):
                lines.append(f"- 情境「{c.get('situation', '')}」→ {c.get('author_choice', '')}"
                             f"｜vs 通用：{c.get('vs_generic', '')}")
    return "\n".join(lines)


def _ctx_reorder_mode() -> str:
    """写作上下文位置重排开关（env CTX_REORDER_MODE · 默认 active · P0）。

    lost-in-the-middle / RoPE recency 实证：long-context 中段注意力最弱（U 型），
    紧贴生成点（prompt 末尾）注意力最强。现状 build_prompt 把**第一权威风格 skill**
    落在 U 型最低的中段，而紧贴生成点「现在执行润色」之前的是 manifest 事实索引
    （非风格锚）→ 位置层北极星偏移。

    active（默认）：把风格 skill + 语感种子锚移到 prev_ch 之后、「现在请写正文」之前
                    （生成点近邻 RoPE 高位）；manifest 事实索引留中段。
    off / shadow：保持原版 join 顺序（零回归回退路径）。
    """
    return (os.environ.get("CTX_REORDER_MODE") or "active").strip().lower()


def _skill_primacy_mode() -> str:
    """skill 硬约束 primacy 重排开关（env SKILL_PRIMACY_MODE · 默认 active · 第8轮同族补完）。

    IFScale 实证：长指令文档里的硬约束维（段长契约 / 套话防线 / 对话格式）在 skill **中段衰减**
    （指令越多、文档越长，中段那几条越容易被模型"读过即忘"）。现状第8轮 ctx 重排把整个风格 skill
    下沉到生成点近邻，但 skill 内部仍是一大段连续文本——里面的硬约束维没有被 **primacy 强调**，
    长 skill 中段那几条照样衰减。

    active（默认）：在生成点近邻（风格锚区）补一段**精简硬约束 primacy 重述**——只点名 3 个最易
                    衰减的硬约束维（段长契约 / 套话防线 / 对话格式），轻量重述非整 prompt 复制
                    （黑箱零成本），把硬约束维 primacy 提到显著位置。
    off / shadow：不注入该段（零回归回退路径）。
    """
    return (os.environ.get("SKILL_PRIMACY_MODE") or "active").strip().lower()


def _author_emotive_punct(db: Path):
    """读作者风格档情绪标点密度（感叹/问号/省略 per 1000）·缺失返回 None。
    用于判断该作者要不要在 primacy 块强调情绪标点（搞笑流/口语向作者密·严肃作者疏）。"""
    p = db / "作者风格.json"
    if not p.exists():
        return None
    try:
        q = (json.loads(p.read_text(encoding="utf-8")).get("quantitative") or {})
        pd = q.get("punctuation_density_per_1000") or {}
    except (json.JSONDecodeError, OSError, AttributeError):
        return None

    def g(k):
        v = pd.get(k, {})
        return v.get("mean") if isinstance(v, dict) else (v if isinstance(v, (int, float)) else None)

    excl, ques, ell = g("exclamation"), g("question"), g("ellipsis")
    if excl is None and ques is None and ell is None:
        return None
    return {"excl": excl or 0, "ques": ques or 0, "ellipsis": ell or 0}


def _author_para_dialogue(db: Path):
    """读作者段落密度(单句独行率/段长) + 对话占比基线（瓶颈修复·让段长契约/对话占比贴作者
    非通用爽文一段一句）。缺失返回 None。"""
    p = db / "作者风格.json"
    if not p.exists():
        return None
    try:
        q = (json.loads(p.read_text(encoding="utf-8")).get("quantitative") or {})
    except (json.JSONDecodeError, OSError, AttributeError):
        return None

    def _m(v):
        return v.get("mean") if isinstance(v, dict) else (v if isinstance(v, (int, float)) else None)

    single = _m(q.get("single_sentence_para_ratio"))
    if single is None and isinstance(q.get("paragraph_length"), dict):
        single = q["paragraph_length"].get("single_sentence_para_ratio_mean")
    dia = _m(q.get("dialogue_ratio"))
    if dia is None:
        dp = _m(q.get("dialogue_ratio_pct"))
        dia = dp / 100 if isinstance(dp, (int, float)) else None
    para = _m(q.get("paragraph_length_chars"))
    if para is None and isinstance(q.get("paragraph_length"), dict):
        para = q["paragraph_length"].get("mean_chars")
    if single is None and dia is None:
        return None
    return {"single": single, "dialogue": dia, "para_mean": para}


def _para_contract_line(author_para: dict = None) -> str:
    """瓶颈1修复（北极星⑤·作者档段落基线让通用一段一句让位）：
    作者写密实多句长段(single<0.5)→明确要复合长段·不拆碎句；否则保通用一段一句默认。
    瓶颈2修复：作者对话占比可观(dialogue>0.25)→显著重申对话占比契约。
    实证锚点《人生长恨水长东》：single 0.1788·段长 97 字·dialogue 0.306（重写仅 18.9% 欠口）。"""
    lines = []
    single = (author_para or {}).get("single")
    para = (author_para or {}).get("para_mean")
    dia = (author_para or {}).get("dialogue")
    if single is not None and single < 0.5:
        # 作者写密实多句长段（《人生长恨》single 0.1788·段长 97 字）——通用「一段一句」让位
        para_txt = f"·段长均值约 {para:.0f} 字" if para else ""
        lines.append(
            f"- **段长契约（作者档第一权威·覆盖通用一段一句）**：本作者写**密实多句长段**"
            f"（单句独行仅 {single:.0%}{para_txt}）——非对话叙述段用逗号连缀的复合长句承载"
            f"信息（因果/让步/比喻/列举多句揉一段），**绝不一段一句拆成碎句**；段长贴作者基线"
            f"（别低于其 7 成{f'≈{para*0.7:.0f}字' if para else ''}），单句独行率压到 {single:.0%} 附近。\n")
    else:
        lines.append(
            "- **段长契约**：贴合作者风格 skill 规定的段长 / 单句独行节奏；skill 未规定时默认非对话段"
            "一段只收一个句末结束符（。！？……），看到一段堆 ≥2 句立刻拆段。\n")
    if dia is not None and dia > 0.25:
        # 作者对话占比可观（《人生长恨》对话占比 ~31%·实测重写仅 18.9% 欠口）——显著重申对话占比契约
        lines.append(
            f"- **对话占比契约（作者档第一权威）**：本作者**对话占比可观**（对话占比约 {dia:.0%}）——"
            f"多写角色交锋/对白推进剧情/潜台词博弈，**别写成纯叙述铺陈**；本 cluster 对话占比"
            f"贴 {dia:.0%}（叙述与对话交替·让人物用台词承载冲突与情绪）。\n")
    return "".join(lines)


def _build_hard_constraint_primacy_block(author_punct: dict = None,
                                         author_para: dict = None) -> str:
    """生成点近邻的「硬约束维 primacy 重述」段（SKILL_PRIMACY_MODE · 默认 active）。

    轻量重述（非整 prompt / 整 skill 复制 · 黑箱零成本）：点名 skill 中段最易衰减的硬约束维——
    段长契约 / 套话防线 / 对话格式 / 段首多样 / 情绪标点（作者基线感知）——贴生成点 RoPE 高位强调，
    对抗 IFScale 中段衰减。advisory 措辞（北极星⑤不硬锁 · 以作者风格档为第一权威，本段只是把
    "已在 skill 里写过的硬约束维"提到显著位置重申，不新增规则、不覆盖 skill）。

    off/shadow → ""（不注入 · 零回归）。
    """
    if _skill_primacy_mode() != "active":
        return ""
    block = (
        "## ⚙️ 硬约束维 primacy 重述（生成点近邻强调 · advisory · 不覆盖上方风格 skill）\n"
        "\n"
        "下面这些维度在长风格档里最容易被『读过即忘』（IFScale 中段衰减），写之前再对齐一遍——"
        "**以上方作者风格 skill 的具体规定为准**，本段只是把它们提到显著位置重申，不新增规则：\n"
        "\n"
        + _para_contract_line(author_para)
        + "- **套话防线（结构性禁用词）**：结构性 AI 套话（与此同时 / 值得一提的是 / 不仅如此 / 事实上）零容忍；"
        "工艺词按 C5 正向协议写，作者 skill signature 列了的是作者笔法、以 skill 为准。\n"
        "- **对话格式**：引号样式按作者风格档 golden_passages 的实际 codepoint——**默认中文弯引号 “…”"
        "（U+201C/U+201D）· 🔴 禁止默认直角引号「」（U+300C/U+300D·gen-model 常错误默认）**，除非作者档"
        "golden 明确用「」才用「」；对话独行、口癖停顿沿用 voice_pack，全篇统一不漂移。\n"
        "- **段首/动作多样**（防 AI 机械点名 + 塑料感）：段首主语（**任意**角色名/代词·非仅同一个）"
        "连续 ≤2 段，第 3 段换起头方式（省主语 / 动作 / 环境 / 对话起头）；同一肢体动作模板全篇 ≤5 次，"
        "**禁同义词换皮规避**（靠→歪→倒在椅背仍是同一动作）。\n"
        "- **段首句式骨架多样**（别让「前置长定语+的+主语后置」倒装霸占段首·如「摸出手机的陆参」"
        "「愣住的他」连用）：段首句法结构换着来，别全是同一倒装模具。\n"
        "- **强度副词克制**（别堆「极其 / 死死 / 毫无 / 猛地 / 狠狠」通胀强度）：强度靠具体动作/细节传"
        "（死死抓住→指节发白 · 极其愤怒→把杯子摔了），别用强度副词偷懒。\n"
        "- **对话标签疏化**（别每句「X说道 / X问」工艺单一）：双人对话定场后省标签·靠语气/动作节拍/"
        "内容辨说话人（标签变体也别换花样硬避重复=另一种 AI 腔）。"
    )
    # [2026-06-05] 情绪标点维（作者基线感知·只对情绪标点密的作者强调，严肃/measured 作者不注入避免误伤）：
    # 实证 flash 在全量 24KB prompt 下写成叙述向(感叹0.7 vs 小世界作者4.9)，！？被中段衰减埋没；
    # 同样的 flash 在简短 prompt 里"情绪标点拉满"显眼时感叹冲到~29。把它提到 primacy 高位才跟得到。
    if author_punct and (author_punct.get("excl", 0) >= 2 or author_punct.get("ques", 0) >= 3
                         or author_punct.get("ellipsis", 0) >= 3):
        block += (
            f"\n- **情绪标点（🔴 这位作者情绪标点很密：感叹≈{author_punct.get('excl', 0):.1f}/千 · "
            f"问号≈{author_punct.get('ques', 0):.1f}/千 · 省略≈{author_punct.get('ellipsis', 0):.1f}/千）**："
            "凡内心吐槽 / 惊呼 / 拍案 / 荒诞反问 / 反高潮拖音处，**该用 ！ ？ …… 就别用句号压平**——"
            "这位作者的节奏和喜感全靠这几个标点抖出来，别整段写成客观陈述句（这是最易丢的作者声纹）。"
        )
    return block


# 🔴 2026-06-28 伏笔明暗线隔离（防 gen_writer 直读 事件簇.json 泄露暗线）
def _sanitize_cluster_brief_foreshadowing(brief: dict, current_cluster_id) -> dict:
    """gen_writer 直读 事件簇.json 的 cluster dict 注入 writer prompt 前，对其 foreshadowing 字段做与
    build_manifest 同款的明暗线过滤——根治「绕过 build_manifest 的 _sanitize/_resolve、把未到触发的
    hidden_payoff 暗线秘密直接 json.dumps 进 writer prompt」泄露口（Agent A 揪出的最后一口）。

      · foreshadowing_to_plant（埋设侧）→ 只留 surface_clue·剥 hidden_payoff（写手当普通细节埋·不剧透）。
      · foreshadowing_to_callback（揭晓侧·若 cluster dict 带）→ 仅 trigger_cluster==当前块才暴露
        hidden_payoff + 注入 reveal_directive（该揭晓的·正常）；未到期剥离 hidden_payoff 防提前泄露。
      · scene_storyboard 的 beat（goal/conflict/turn/emotional_tone/plant_foreshadowing_surface 等）
        原样透传——已确认不含 hidden_payoff（安全）。
      · 🔴 2026-06-29 对白即行动：scene_storyboard[*].dialogue_objectives[*].what_unsaid 若涉未到期
        hidden 伏笔（objective 标 reveal_cluster 且未到期）→ 经 _bm_sanitize_dialogue_objectives 隔离门控
        剥 what_unsaid（与 build_manifest._collect_dialogue_objectives 同口径·单一真理源）·防 gemini 提前剧透。
        默认安全闸：objective 无 reveal_cluster 标记 → 原样透传（零行为变化）。

    返回浅拷贝（不改原 brief·原 dict 仍供 scope_summary/hard_constraints 等非密字段消费）。
    复用 build_manifest 纯函数为单一真理源（schema 演进读容错：旧格式纯字符串伏笔当 surface_clue·
    见其 docstring·非降级·北极星⑥）。
    """
    if not isinstance(brief, dict):
        return brief
    safe = dict(brief)
    if "foreshadowing_to_plant" in safe:
        safe["foreshadowing_to_plant"] = _bm_sanitize_fs_plant(safe.get("foreshadowing_to_plant"))
    if "foreshadowing_to_callback" in safe:
        safe["foreshadowing_to_callback"] = _bm_resolve_fs_callback(
            safe.get("foreshadowing_to_callback"), current_cluster_id)
    # 🔴 2026-06-29 对白即行动dialogue_objectives注入·闭合直读 scene_storyboard 的 what_unsaid 剧透口
    _sb = safe.get("scene_storyboard")
    if isinstance(_sb, list):
        _new_sb = []
        for _sc in _sb:
            if isinstance(_sc, dict) and isinstance(_sc.get("dialogue_objectives"), list):
                _sc2 = dict(_sc)
                _sc2["dialogue_objectives"] = _bm_sanitize_dialogue_objectives(
                    _sc.get("dialogue_objectives"), current_cluster_id)
                _new_sb.append(_sc2)
            else:
                _new_sb.append(_sc)
        safe["scene_storyboard"] = _new_sb
    return safe


# 🔴 2026-06-28 写手信息隔离（闭合直读人物卡泄露口 · Agent A 揪出的最大裸露口）
def _sanitize_character_cards_for_writer(cards_path: Path, current_cluster_id) -> str:
    """人物卡.json 注入 writer prompt 前，对每张卡跑 build_manifest._sanitize_character_card
    （单一真理源）字段级脱敏后 re-serialize——根治 gen_writer 原先 read_text(人物卡.json) 把整份
    原文（含未到 concealed_until_cluster 的 true_role / surface_role 反差 / ghost.wound reveal /
    _writer_hint『终卷揭密/false_hero/灰色合作』反指令 / knowledge.will_learn 未来知识 /
    voice_pack 秘密护栏 / offscreen 幕后意图）原样 json.dump 进 writer prompt 的隐藏身份泄露口。

    脱敏规则全由 _bm_sanitize_character_card 承载（与 build_manifest.active_character_cards 同口径）：
      · 未到揭密 cluster → 剥 true_role·role/propp_function 用 surface 等价替换·注 surface_subtext。
      · 到/越过 concealed_until_cluster → 解锁 true_role + reveal_directive（该揭晓的不漏付）。

    默认安全闸（不兼容不降级·零回归）：无 true_role/concealed/hidden 等显式标记的旧卡（今天几乎全部）
    一律原样透传零行为变化。只做「字段级脱敏」不做「角色集裁剪」——保留全部角色（voice_pack 是声纹复刻
    第一依据，按 active 过滤会丢后登场角色声纹），与原 char_card 全量注入对非密卡逐字节等价。

    边界：文件不存在 → 同 read_text 的 [WARN] 占位（默认安全）。JSON 破损 / schema 无 characters list
    → 退回原文（破损 JSON 无法被 build_manifest 加载、不构成可解析的 hidden 标记，零回归不更糟·告警留痕）。
    """
    if not cards_path.exists():
        return f"[WARN] 文件不存在: {cards_path}"
    raw = cards_path.read_text(encoding='utf-8')
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"⚠️ 人物卡.json 解析失败，无法字段级脱敏隐藏身份，退回原文注入: {cards_path}")
        return raw
    if not isinstance(data, dict) or not isinstance(data.get("characters"), list):
        return raw  # schema 非预期（无 characters list）→ 无可脱敏结构·退回原文（零回归）
    safe = dict(data)
    safe["characters"] = [
        _bm_sanitize_character_card(c, current_cluster_id) if isinstance(c, dict) else c
        for c in data["characters"]
    ]
    return json.dumps(safe, ensure_ascii=False, indent=2)


def build_prompt(project_root: Path, cluster_id: int, ch_start: int,
                 polish_view: dict | None = None) -> tuple:
    """组装 system + user prompt（v29 润色模式）

    v29 唯一模式 = 分段润色：polish_view（{idx, total, scene_text}）指定本次要润色的
    Claude 亲笔场景段。manifest / 风格档 / brief / 人物卡等全部 sections 照常注入
    （润色需要与写作同等的事实与风格上下文），prompt 尾部为「等体量润色指令 + 原文段」。
    各段 prompt 头部保持一致 → gemini 隐式前缀缓存生效（省 token）。
    """
    if not polish_view:
        raise ValueError("v29: build_prompt 需要 polish_view（分段润色是唯一模式）")
    db = project_root / '_数据库'

    # 读取核心资料
    # 2026-05-29 北极星复审 L2：去 load-time 截断（原 30000/25000 仍砍大文件，与
    # feedback_no_token_saving「全量传 LLM」+ 作者档第一权威冲突；诡异接待处 skill 34575 字被砍 9k+）。
    # 全量读——作者风格 skill 是写作第一权威，不得在 load 时截断。
    manifest_path = db / '.manifest' / f'ch_{ch_start:03d}.json'
    # v28 系统整改：优先读 compressed 版本（剥除 Claude agent 元数据，省~40% token）
    compressed_path = db / '.manifest' / f'ch_{ch_start:03d}_compressed.json'
    manifest = read_text(compressed_path) if compressed_path.exists() else read_text(manifest_path)

    # 预加载 manifest dict（消除 9 个 _build_*_section 各自重复读同一文件的冗余 IO）
    try:
        _manifest_dict = json.loads(Path(compressed_path if compressed_path.exists() else manifest_path).read_text(encoding='utf-8'))
    except Exception:
        _manifest_dict = {}

    style_fp_section = _build_style_fingerprint_section(manifest_path, _manifest_dict)
    rhythm_section = _build_rhythm_signature_section(manifest_path, _manifest_dict)
    decision_section = _build_decision_principles_section(manifest_path, _manifest_dict)
    genre_section = _build_genre_pack_section(manifest_path, _manifest_dict)
    knowledge_gap_section = _build_knowledge_gap_section(manifest_path, _manifest_dict)
    # 🔴 2026-06-29 角色信息差(per-character belief)：各场景各角色认知边界（生成层物理 masking·见 H7）
    belief_section = _build_belief_section(manifest_path, _manifest_dict)
    # 🔴 2026-06-29 场景级Appraisal Beat（心理 P0）：情绪余烬 + 本块情绪方向（appraisal-as-prose·见 system EBS 段）
    appraisal_section = _build_appraisal_section(manifest_path, _manifest_dict)
    narr_seq_section = _build_narrative_seq_section(manifest_path, _manifest_dict)
    golden_fewshot_section = _build_golden_fewshot_section(manifest_path, _manifest_dict)
    deep_dims_section = _build_deep_dims_section(manifest_path, _manifest_dict)
    rolling_anchor_section = _build_rolling_anchor_section(manifest_path, _manifest_dict)
    # R7 Batch-D（2026-06-20）：D7 叙事债务状态卡（advisory · 北极星⑤不硬锁）
    debt_ledger_section = _build_debt_ledger_section(manifest_path, _manifest_dict)
    # A3 前块结尾回响（2026-07-07·PlotPilot 移植）：manifest 有 prev_cluster_tail 才非空（advisory）
    prev_tail_echo_section = _build_prev_tail_echo_section(manifest_path, _manifest_dict)
    # A4 编辑手记（2026-07-08·PlotPilot 移植）：manifest 有 editor_note 才非空（advisory·可自由取舍）
    editor_note_section = _build_editor_note_section(manifest_path, _manifest_dict)

    # 风格 skill（全量，不截断）
    style_skill = read_text(db / '作者风格_skill.md')
    if not (style_skill or "").strip():
        logger.warning("⚠️🔴 作者风格_skill.md 缺失/空——writer 只有量化 JSON、缺作者笔法+golden 范例，"
              "极易跑偏成通用爽文（cluster_001 翻车根因）。请把风格库 skill_FINAL.md 复制为 "
              "<项目>/_数据库/作者风格_skill.md（/outline 漏拷的已知 bug）。")

    # 调研 cache：优先读取 manifest.event_cluster_context.research_ref.cache_path 绑定文件。
    # 只有旧项目缺 research_ref 时，才退回 cluster 专属文件 / 最新 inspiration 兼容路径。
    cache_text = _load_research_cache_for_cluster(db, cluster_id, _manifest_dict)

    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    progress = json.loads((db / '进度.json').read_text(encoding='utf-8'))
    cluster_blueprint = progress.get('cluster_blueprint', {})
    plans = []
    # cluster_id 是 int（如 6），blueprint key 可能是 "cluster_006"/"6"/"cluster_6"
    # → 两边归一化为 'cluster_NNN' 后匹配（修复类型不匹配导致 storyboard 注入失效）
    _norm_cid = cluster_lookup.normalize_cluster_id(cluster_id)
    if _norm_cid:
        # 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测 list(25)），
        # 裸 .items() 会 AttributeError 崩 writer 路径。先 normalize_blueprint 归一成 dict 再迭代。
        for _bp_key, _bp_val in cluster_lookup.normalize_blueprint(cluster_blueprint).items():
            if cluster_lookup.normalize_cluster_id(_bp_key) == _norm_cid:
                plans = _bp_val.get('scene_storyboard', [])
                break
    relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_start + 30]
    plan_text = json.dumps(relevant_plans, ensure_ascii=False, indent=2) if relevant_plans else "[]（v29 · Claude 亲笔草稿已按 cluster_brief.scene_storyboard 落实·本次任务是等体量润色）"

    # 人物卡（全量，不截断——含 voice_pack 是声纹复刻第一依据，截断 = 后登场角色声纹丢失）
    # 🔴 2026-06-28 写手信息隔离：注入前对每张卡跑 _sanitize_character_card 字段级脱敏（单一真理源·
    # 剥未到 concealed_until_cluster 的 true_role / false_hero / 灰色合作 / 未来知识等），绝不再 read_text 原文 dump。
    char_card = _sanitize_character_cards_for_writer(db / '人物卡.json', cluster_id)

    # 用户偏好（全量，不截断）
    pref = read_text(db / '用户偏好.json')

    # v22.gov.align.fix Gap T2-X: 读 事件簇.json 找当前 cluster 的 brief 注入 prompt
    # 之前 build_prompt 完全没读 事件簇.json，导致 cluster.scope_summary 硬约束未注入 → LLM 自由发挥跑偏 task
    cluster_brief = {}
    cluster_brief_text = ""
    cluster_hard_constraints_text = ""
    try:
        ec_path = db / '事件簇.json'
        if ec_path.exists():
            ec = json.loads(ec_path.read_text(encoding='utf-8'))
            for c in ec.get('clusters', []):
                # cluster_id 是 int，事件簇 c['cluster_id'] 是字符串 → 两边归一化后比对
                if cluster_lookup.normalize_cluster_id(c.get('cluster_id')) == cluster_lookup.normalize_cluster_id(cluster_id):
                    cluster_brief = c
                    break
            if cluster_brief:
                # 🔴 2026-06-28 伏笔明暗线隔离：dump 进 writer prompt 前先过滤——埋设侧只留 surface_clue
                # （剥 hidden_payoff）·揭晓侧仅 trigger_cluster 才暴露。绝不把整 cluster dict（含未到触发
                # 的 foreshadowing_to_plant.hidden_payoff）原样 json.dumps 给写手（原泄露口）。
                # 用 _safe_brief 只供「注入文本」；下面 scope_summary/hard_constraints
                # 等非密字段仍读原 cluster_brief（不受过滤影响）。
                _safe_brief = _sanitize_cluster_brief_foreshadowing(cluster_brief, cluster_id)
                # v29：各段全量注入 brief（prompt 头部跨段一致 → gemini 隐式前缀缓存吸收成本）
                cluster_brief_text = json.dumps(_safe_brief, ensure_ascii=False, indent=2)
                # 从 scope_summary 提取硬约束（≥/≤/百分比/角色数等）
                scope = cluster_brief.get('scope_summary', '')
                constraints = []
                if scope:
                    # 简单提硬约束关键词：「≥ N」「≤ N」「占比」「至少」「不少于」「角色」
                    import re as _re
                    for m in _re.finditer(r'(?:至少|不少于|≥)\s*(\d+)\s*([个名位章字]?)', scope):
                        n = m.group(1); unit = m.group(2) or '项'
                        constraints.append(f"- 至少 {n} {unit}（出自 scope_summary）")
                    for m in _re.finditer(r'(?:不超过|至多|≤)\s*(\d+)\s*([个名位章字]?)', scope):
                        n = m.group(1); unit = m.group(2) or '项'
                        constraints.append(f"- 至多 {n} {unit}（出自 scope_summary）")
                    for m in _re.finditer(r'(?:占比|比例)\s*[≥>]\s*(\d+)\s*%?', scope):
                        constraints.append(f"- 比例 ≥ {m.group(1)}%（出自 scope_summary）")
                    for m in _re.finditer(r'(?:占比|比例)\s*[≤<]\s*(\d+)\s*%?', scope):
                        constraints.append(f"- 比例 ≤ {m.group(1)}%（出自 scope_summary）")
                # 额外读 cluster_brief 的 hard_constraints 字段（v22.gov.align.fix 新 schema）
                for hc in cluster_brief.get('hard_constraints', []) or []:
                    if isinstance(hc, dict):
                        metric = hc.get('metric', '?')
                        if 'min' in hc:
                            constraints.append(f"- {metric} ≥ {hc['min']}（cluster.hard_constraints · 必遵守）")
                        if 'max' in hc:
                            constraints.append(f"- {metric} ≤ {hc['max']}（cluster.hard_constraints · 必遵守）")
                if constraints:
                    cluster_hard_constraints_text = "\n".join(constraints)
    except (json.JSONDecodeError, OSError) as e:
        logger.info(f" 事件簇.json 读取失败（不阻塞）: {e}")

    # 前一章末尾（用于衔接，如果 ch_start > 1）
    prev_ch_section = ""
    if ch_start > 1:
        prev_p = project_root / '章节' / f'第{ch_start-1:03d}章' / f'第{ch_start-1:03d}章.txt'
        if prev_p.exists():
            prev_text = prev_p.read_text(encoding='utf-8')
            prev_ch_section = "## 前一章末尾（衔接用）\n\n" + prev_text[-1500:]

    # L3 防御（feedback_no_screenplay_stage_directions_in_novels 等）：
    # 自动扫 memory/feedback_*.md，把 type=feedback 的全局规则注入 writer prompt 头部
    feedback_rules_text = _collect_feedback_rules()

    # 🎴 真实原文「语感种子」播种（P0 · env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量 · 真生效）：
    # 注 1-2 段作者真实原文当语感锚点，防长 cluster 中后段退化回通用 AI 腔。
    # 按 cluster.scope_summary 的风格/情绪寄存器选样（非题材匹配 · Catch Me 论文避坑），
    # 并带「只借语感起手势 · 绝不抄情节内容」避坑指令（防抄袭+防内容泄漏）。
    _scope_for_seed = cluster_brief.get('scope_summary', '') if cluster_brief else ''
    seed_section, seed_trace = snippet_seed.make_seed_block_for_writer(
        project_root, scope_text=_scope_for_seed)

    # 2026-05-29 北极星修复 [H3-write]：终极目标=写出和【该作者】风格一致的文章。
    # 故 system prompt 第一权威是「作者风格档(下方风格 skill)」，不是写死的通用爽文工艺。
    # 铁律分两层：① 常驻硬铁律(格式/世界观/穿帮防护·任何风格都不可破·不可被 skill 覆盖)；
    # ② 风格工艺默认基线(仅当作者 skill 未规定该维度时兜底·skill 规定了则以 skill 为准)。
    # 删除原写死「冷峻俯瞰」默认风 + 原 rule4「单章字数 2500-5000」(违反 cluster-first)。
    system = (feedback_rules_text + "\n\n") if feedback_rules_text else ""
    system += """你是长篇小说的写作引擎。**最高准则：复刻下方「风格 skill / 作者风格档」描述的那位作者的写法**——句式节奏、用词偏好、signature 笔法、情绪处理、对话风格都要像那位作者。你的任务是写一个故事块（cluster）的完整正文，覆盖多章。

# 第一权威 = 作者风格档（下方「风格 skill」段）
- 风格 skill 里规定的句长 / 段长 / 对话占比 / 签名词 / 开篇收束手法 = **第一权威**，凡 skill 写了的维度，**以 skill 为准**，下方「风格工艺默认基线」在该维度自动让位。
- 风格 skill 未规定的维度，才用下方默认基线兜底。
- 没有任何写死的「默认文风」——你的文风由作者风格档决定，不是某种预设爽文腔。

# 一、常驻硬铁律（格式/世界观/穿帮防护 · 任何风格都不可破 · skill 不能覆盖）

## H1. 真实世界时间 0 容忍
禁用 20XX 年 / 公元 / 月份名 / 星期几。用「沙盒第 N 周期」「寒月第十七日」「异常历 X 年」等（除非作者风格档/世界观明确是现实题材）。

## H2. 不写双标点
不写「，。」/「。，」等双标点错误。

## H3. AI 结构套话零容忍（结构性机器腔 · 与作者签名词无关）
禁用：与此同时 / 值得一提的是 / 不仅如此 / 事实上。这 4 个是结构性 AI 腔，任何作者都不会用，skill 不能放行。
另：「然而」高频转折 = 机器腔（偶用可，避免每段用「然而」起转折——与 CLAUDE.md 反 AI 腔基线一致）。
（注：工艺/签名词类归第二层 C5 正向协议——作者风格档列为签名笔法的以作者档为准。）

## H4. cluster 契约（user prompt 顶部「CLUSTER 硬约束」段如有）
scope_summary 描述的场景类型/角色构成是**剧情硬契约**（如"对白场景"应有足够角色 + 对话为主），不可跑偏成别的场景。这是「写对剧情」不是「写某种文风」。

## H5. 占位代号零泄漏（沉浸感硬铁律）
scene_storyboard / scope_summary / cluster_brief 等大纲材料里出现的「主角」「男主」「女主」「某角色」「XX」「反派」等**占位代号是给你看的写作指引**，**绝不能原样抄进正文**。正文里指代人物**只能**用：① 具体角色名（如「多林」），② 第三人称代词（他/她/它），③ 贴合身份的称谓（那个占卜师 / 穿灰外套的男人 / 守门人）。若某人物在本 cluster 尚未取名，自行用代词或身份称谓承接，**正文里出现「主角」二字即视为破例失败**。

## H6. 伏笔明暗线工艺（埋伏笔零剧透 · 沉浸感硬铁律）
# 🔴 2026-06-28 伏笔明暗线隔离
cluster_brief / manifest 给你的 `foreshadowing_to_plant`（要埋的伏笔）**只有明线 `surface_clue`（一个表面细节）**——它真正指向的暗线秘密**不会给你看**，这是故意的，防你提前剧透。
- **埋伏笔 = 把 `surface_clue` 当一个普通细节自然写进正文**：让它像随手带过的环境 / 物件 / 动作 / 对话细节，**绝不解释它暗示什么、有何深意、为何重要、日后会怎样**。读者此刻**不该察觉它是伏笔**。一旦你写出「他隐约觉得这枚钥匙不简单」「这个细节日后将……」「冥冥中似有深意」式的提示或心理强调，伏笔就废了。
- **只兑现/揭晓 manifest 或 cluster_brief 里 `reveal_directive`（或 `foreshadowing_to_callback` 带出的 `hidden_payoff`）明确要求揭晓的伏笔**：这些是到期该兑现的暗线，按 `reveal_directive` 把它揭穿 / 回收 / 兑现。
- **没有 `reveal_directive` 要求揭晓的伏笔一律只埋不揭**——你看不到某条伏笔的暗线含义就对了，照明线细节写，别自己脑补它的秘密再提前点破。
- **🔴 角色的隐藏身份/真实面目同理（写手信息隔离）**：人物卡的 `role` / `surface_role` 是你**当下能看到的表面身份**，就把它**当真**写——某个表面盟友实际是叛徒（false_hero）、某个老好人其实是幕后黑手、某人与反派暗中灰色合作，这些**真实身份（`true_role`）系统不会给你看**，是故意的。**绝不提前暗示/铺垫/点破任何角色的 true_role、反派身份、伪装或暗藏动机**（不写「他眼底闪过一丝阴鸷」「她的笑意里藏着别的东西」式提前定性）。只有当人物卡里出现该角色的 `reveal_directive`（到了 `concealed_until_cluster` 才解锁）明确要求揭晓时，才在本块把其真实身份揭穿/兑现。

## H7. 角色信息差（per-character belief · 物理 masking · 沉浸感硬铁律）
# 🔴 2026-06-29 角色信息差(per-character belief)
若下方给了「## 🧠 角色认知边界（per-character belief）」段（manifest `scene_character_knowledge`），**严格按各角色的认知边界写**：
- **每个角色只能基于「他自己知道的」（该场景该角色 `knows[]` 列出的事实）行动、说话、推理**。绝不让某个角色用他**在场没亲历、ledger 里没有**的信息——这是**物理 masking** 不是提醒：角色 A 不该知道的事即便另一个角色 B 知道，**也 ≠ A 知道**（扮猪吃老虎 / 信息差喜剧 / 悬念全靠这个）。
- **`must_not_reference[]` 列出的事实 = 该角色本场景【不知道】**（不知情 / 本块尚未获知）：该角色的言行**不得提及、不得暗示、不得基于其行动**。
- **`can_speak=false` 的 known 事实**：角色**知道但本场不能说出口**——只能体现在内心活动 / 行动暗示里，**绝不写进该角色的台词**。
- 没有该段时按常规写（默认安全·此规则不生效）。

# 二、风格工艺默认基线（仅当作者 skill 未规定该维度时兜底 · skill 规定了以 skill 为准）

## C1. voice 工艺 applies_to 边界（默认）
若 skill 未另规定：voice_pack 的极短句节奏只用于对话/笔注；叙述段 1-3 句/段为常态，段长 < 80 字时避免句号 ≥ 3 个的碎句堆叠。
（错误范式：「那只手不是他的。手大。粗。」正确范式：「那只手不是他的，手大而粗。」——但若作者风格档本身就是短句碎切流，以 skill 为准。）

## C2. 指示性名词节制（默认）
「那只手」「那个声音」等指示短语 5 段内同 token 避免 ≥ 4 次（除非 skill 标为刻意复沓手法）。

## C3. 段首主语多样（默认 · 全主语口径）
角色名/代词段首（池迟/他/她/苏挽/老钟 等**任意主语词，不限同一个**）避免连续 ≥3 段以主语起头——**即使主语在轮换（池迟→他→苏挽→老钟）也算机械点名单调**。连续 2 段主语起头后，第 3 段必须换：省主语 / 部位代指 / 动作起头 / 环境起头 / 物件起头 / 对话起头（除非 skill 标为排比手法）。

## C3b. 动作模板多样（默认 · 防换皮）
同一肢体动作模板（如「靠在椅背上」「盯着…看了两秒」「低头看手背」「嘴角动了一下」）全篇 ≤ 5 次，超出换**不同的具体动作**（敲桌沿 / 转笔 / 抠橡皮 / 脚尖蹭地 / 拨刘海 / 歪头看天花板 等）；**禁止同义词换皮**——靠→歪→倒在椅背仍是同一动作，要换真动作不换字面。

## C3c. 忌句子级「流水账作文感」（默认 · 句法节奏铁律）
**最易翻车的人机感来源**：连续「主语+动作」的句子级流水账——「他低头看手。他把手收回来。他转过身。他翻开书。他提起笔。」这是作文/舞台说明，**不是网文**。三条破法：
- **句子别清一色「谁做什么」起头**：句子级（不只段首）也要换起头——环境 / 状语 / 感官 / 对话 / 心理 / 评论 / 动作中段切入。连续 ≥3 句「主语+动作」就该断。
- **裸动作融进语境**：动作别单独成句罗列，让它附着在对话/心理/评论上（如「他**说着就**翻开了书」「书页翻到某处，**他指尖顿了顿**——那行字不该在这」），而非「他翻开书。他看见那行字。」
- **多用逗号连缀的复合长句承载信息**（因果/让步/对比），长短句交替而非匀速短句——**贴作者句长基线，别越写越碎**（弱模型最易退化成短句罗列；作者真实句长往往是你直觉的近 2 倍）。

## C4. 破折号节制（默认 · 作者档可豁免）
破折号 ≤ 8/千字（除非 skill 偏好高频破折号）；**若作者档破折号基线极低（几乎不用），更要克制**——破折号过载会把节奏砸成单一的「短促—强调」循环，用句号断句/逗号连缀替代。

## C5. 工艺词正向行为协议（默认兜底 · 作者档可豁免）
- 情绪落身体与动作、强情绪降一档写（反例「心中一凛」→正写「端着的杯子停在半空」）。
- 神态写整体姿态或不写，让对白传情绪（反例「嘴角勾起一抹冷笑」→正写「他往后靠了靠」）。
- 语气靠断句与动作节拍（反例「他缓缓地说」→正写「他说得很慢，每个字都咬得清楚」）。
- 推进转折直接断句；判断换可观察证据（反例「显然他在撒谎」→正写「他答得太快了」）。
**作者档 signature / golden_passages 惯用的工艺词是作者签名笔法，按作者档写**——复刻作者优先于通用协议。

# 元 anti-slop 防御（已知重犯模式 · 默认基线）

- **poetry-mode 短句三连**：禁用「第一遍 / 第二遍 / 第三遍」「那一瞬 / 那一瞬 / 他记着那一瞬」类 3+ 段独立短段含同短语
- **signature 短语暴涨**：voice signature 单章 ≤ 2 次，跨章不暴涨
- **元-vocab disclaimer**：「他没有这个词」「他不知道这是 X」类全 cluster ≤ 2 次
- **否定动作三件套**：「没说话/没出声/没接腔」单 cluster ≤ 25 次
- **章末抒情模板**：禁写「他不再是 X 的那个 X 了」类抒情收束
- **🆕 否定对照句式过用**：「不是 X——是 Y」「不是 X，是 Y」对照结构单 cluster ≤ 20 次——超出即从恐怖修辞退化成 AI 口头禅（cluster_002 实测 61 次翻车）；多用陈述/动作/反问替代
- **🆕 比喻/质感库单一化**：同一核心意象的喻体（如同化=「打磨过的石头」、某角色声纹标签「声音像放凉的粥」）单 cluster 同喻体 ≤ 4 次——换不同喻体别复读同一个，否则恐怖/角色辨识度被钝化成塑料感
- **🆕 动作环跨场景累计**：单一肢体动作模板（低头看 X / 扯拉链 / 转某道具）单 cluster 累计 ≤ 6 次（C3b 的跨场景强化版·别让配角沦为「单一道具机器」）
- **🆕 叙事扁平化(Narrative Flattening)**：LLM 倾向把高强度情绪压成中性平铺——强制情绪曲线有峰有谷，禁全 cluster 情绪匀速无波动。悲伤/愤怒/狂喜写到位别克制成"微微皱眉"
- **🆕 过早消解综合征(Premature Resolution)**（arXiv:2604.09854 LLM 第一弱点）：冲突/危机/悬念提出后 500 CJK 内就给出答案 = 过早消解——张力建立需要时间，读者需要「担心」的过程。提出问题→角色挣扎→部分进展→遗留未解 才是正确节奏
- **🆕 事件升级扁平(Escalation Flatness)**（StoryScope 实证：Claude 产出 flat event escalation）：cluster 内的事件 stakes 必须递增——后面发生的事比前面的后果更严重/更紧迫/更不可逆。如果 cluster 中段的 stakes 和开头一样 = 升级扁平
- **🆕 外貌清单式登场(Appearance Catalogue)**（StoryScope 实证：Gemini 默认外貌堆砌）：角色登场时如果前 3 句都在描述外貌（身高/发色/瞳色/穿着）= 清单式登场。正确：角色登场先做事/说话，外貌在互动中自然带出

# 三、描写工艺参考（advisory · 作者风格档规定了则以作者档为准 · 非硬约束）
# 2026-06-19 联网调研升级（StoryScope arXiv:2604.03136 / Tension Forecasting arXiv:2604.09854
# / AI Fiction Paradox arXiv:2603.13545 / Sanderson 2025 / sensory craft 2025）
# 2026-06-19 R1 描写归类升级（散点规则→可路由分类法：人物6型/环境4功能/战斗景别+速度+实虚/
#   内心戏3态FID/对话审讯+语域指纹 · arXiv 2409.16667 CCI · 2605.07102 SAGE · 2602.15851 ASG ·
#   2509.19595 ELENA · 2603.04969 MPCEval · 2606.13310 RogueAI · reedsy STEAL · wolfcrow 等）

## D1. 人物描写
- 首次出场用**标志性动作/细节**定角色（一个怪癖/一句口癖 > 一段外貌），不列清单式外貌
- 配角用最少笔墨立体化：一句话定性格 + 一个反差行为 > 大段铺陈
- 情绪走**行为/生理替代法**：手指攥白 > "他很紧张"；猛灌水 > "他很焦虑"
- 反派必须有**信念驱动**（局部正确性 / 共情点），不做纯恶工具人
- **🆕 禁外貌清单开场**（StoryScope 实证：Gemini 默认外貌堆砌式角色介绍）：角色登场先写他**在做什么/说什么**，外貌细节通过其他角色视线或互动动作自然带出（「她接过碗时他才注意到那双手很粗糙」> 罗列身高体重五官）
- **🆕 角色决策要有道德模糊**（StoryScope 实证：AI 角色决策道德非黑即白）：重要抉择不给「正确答案」，让角色在两个都有道理的选项间挣扎——读者看到的是真实的人，不是道德教科书
- **🆕 群戏焦点轮转**：3+ 角色同场时，每段聚焦 1 个角色的反应/动作，其他人用最少笔墨（一句话/一个动作）维持在场感，下段换焦点——防止变成点名报到
- **🆕 双层动机驱动**（2025 craft research）：重要角色要有「意识层欲望」和「潜意识层需求」的冲突——表面追求权力（意识），实际渴望被认可（潜意识）。这个冲突让角色的行为看起来矛盾但合理，驱动内在张力
- **🆕 关系网络不能全正面**（arXiv:2510.18932·1200 篇故事网络分析实证：LLM 角色关系严重偏向紧密正面）：不是所有角色都互相喜欢/信任——同盟中要有猜忌、同伴中要有摩擦、师徒要有分歧、朋友间也会有无法明说的芥蒂。**至少 1 对核心角色关系是复杂/紧张/有暗流的**，不写成全员友好互助会

- **🆕🗂️ 人物描写六类型分类法(路由器·散点→可路由)**：人物描写分6型——①外貌②动作③语言④心理⑤神态⑥侧面烘托(他人反应/环境映衬)。**单角色一次塑造跨≥2型**，按角色性质+场景路由主用2-3型并轮转，禁纯外貌清单(arXiv:2409.16667 CCI / reedsy STEAL)
- **🆕 侧面烘托(烘云托月·第6型落地)**：重要角色至少一处经**他人反应/已建立POV的感知过滤**呈现——旁人的惊惧/敬畏/算计反向定义角色分量与威胁度(王熙凤未见其人先闻其声)
- **🆕 出场感官先导(可选技法)**：重要角色出场可先用感官信号(脚步/影子/随身物/他人议论)制造悬念再揭真容——古风玄幻仙侠偏"先声夺人/威压先至"，都市偏动作或对话先行
- **🆕 生理线索去面部偏置**(arXiv:2509.19595 ELENA·对抗LLM facial bias)：写情绪生理反应优先**非面部信号**(手/指节/呼吸/喉咙/肠胃/后背/姿态/体温)，别每次都堆眉眼嘴角脸色

- **🆕 配角弧线缩影主角(分形回响·R2)**：设计重要配角弧线时让其形状缩影主角弧形(同形try-fail-转变的缩小版)形成回响;主题母题尽量在cluster/卷内触达个人/社会/普世三尺度(creatorshearth)

## D2. 环境描写
- 每个场景挑**1-2 个主感官**写透，不五感平均堆砌（恐怖压听觉+嗅觉，温暖压触觉）
- 环境描写必须**服务功能**（代入/情绪/伏笔），为写而写 = 臃肿
- 用**情景交融**替代直接写情绪：角色绝望时写阴雨枯枝 > 写"他很绝望"
- **🆕 主感官按场景类型路由**：恐怖→听觉(异响/寂静)+嗅觉(腐朽/铁锈)；浪漫→触觉(温度/质感)+嗅觉(体香/花香)；战斗→空间(距离/地形)+冲击(震动/碎裂)；日常→视觉(光线/色彩)+触觉(材质/温度)
- **🆕 环境是活的**（Sanderson 2025: Setting as Character）：环境不是静止背景布——它随剧情变化（打斗后地面碎裂/下雨后路滑/夜色加深视线受阻），环境变化反过来影响角色行动和情绪
- **🆕 受限感官倒逼**（2025 sensory craft：constraint-based）：写角色看不清的场景（雾/黑暗/伤后视线模糊）= 迫使你写听觉/触觉/嗅觉，比正常光照场景的感官层次更丰富
- **🆕 感官类型是作者指纹**（10 作者跨 150 章实证·嗅觉 CV=121%/味觉 CV=37%）：**嗅觉和味觉是差异最大的感官维度**——有的作者几乎不写嗅觉(0.27/千字)，有的高频使用(4.34/千字)。如果作者风格档记录了感官偏好，**按作者的感官比例来**；没有则默认视觉>听觉>触觉>嗅觉>味觉的通用梯度。禁止五感平均堆砌——选作者擅长的感官写透

- **🆕🗂️ 环境描写四型功能分类法(路由器)**：下笔前判定场景功能型——①**无关**(一句带过)②**功能型**(空间推动情节→先用地形topography把方位/出入口/可用物件block清楚再叙事)③**心理型**(POV心境投射·景随情移)④**象征型**(选可复现母题承载主题)。分**地形层(空间锚定)+氛围层(情绪呼应)**两步下笔(obp.0187 / Cuddon)
- **🆕 象征性环境随弧线演化**：为本卷选1个可复现环境母题(场所/天气/反复物件)随主角弧线阶段演化(衰败↔新生)承载主题，非每场景孤立写情绪(K.M.Weiland)

- **🆕 通感/移觉(跨感官转译·感官路由真空子域·R2)**：高情绪/高画面峰值处可跨感官借词(光线很吵/钟声是湿的/恐惧有铁锈味)·每cluster约1-3处并设上限——通感是修辞高光非常态,过用比不用更糟(arXiv:2110.09710 Inter-Sense·8000本小说实证)
- **🆕 听觉子域:静默作张力(R2)**：用"撤掉声音"制造对比——巨响后骤静/坦白前长寂/命中前微静默;背景静→单一锐响放大冲击。象声词节制(reedsy)

- **🆕 崇高场面二分(R3·写格局/异象)**：格局展开/异象/登神/见天道场景按**数学崇高**(尺度超感官·用渺小参照物对比)vs**力学崇高**(威力压迫·安全距离旁观)选路·留白不写尽(obscurity)·禁"气势磅礴/震撼无比/无比宏大"形容词堆砌(theccd / arXiv:2409.14853)

- **🆕 延迟解码(R6·危险/异象·恐怖悬疑规则怪谈)**：危险或异象登场先给主角视角的原始未命名感官印象(「细小木棍在空中乱飞」)·延迟数句才点破真名(「是箭」)·制造主观意识即时混乱与不可名状感·区别于出场感官先导(那管角色登场·这管危险/异象的感知-命名时间差)

- **🆕 规则块禁全直陈(R7·rule_anomaly 题材)**：规则怪谈/无限流写规则文本时**不能像说明书条目一字不漏告知所有边界**·必须留歧义/陷阱(『不可X·否则Y』搭配『或许/据说/不一定』式留疑)·让读者跟主角一起琢磨条款真意——半真半假是题材核心工艺·全直陈=工具书读感。仅 rule_anomaly 题材激活

- **🆕 新世界元素先体感后命名 first_encounter_sensory_first(R8 W4·L22 Shklovsky 1917 陌生化·Tolstoy 马视角·Suvin SF estrangement·诡秘 IP 拆书实证)**：novel-novel 元素 (异种/异象/异术/异物/秘境/规则名) 首次登场**禁直贴标签** (X 是 Y / 这就是 Y / 名叫 Y) 而无前置感官铺垫。正确顺序——①先用 1-2 句**未命名的感官印象**(看见/听见/嗅到 + 形容词 + 反应)『他看见空中飘着一缕半透明的丝线·一动不动·像挂在风里』·②再让角色发问/旁人介绍/主角脑补点名『后来他才知道·那是「魂线」』。让读者像 Tolstoy 那匹马看人类制度一样陌生化体验·而非直接接受概念标签。LitRPG/系统流/规则块题材本就直陈可豁免

- **🆕 起兴 scene-opener (R8 W4·L26 Project MUSE Mimesis and 興 + 朱熹比兴 + SCIRP 2017 武侠仙侠玄幻新书前 3 章 xing_ratio 0.68 vs 失败 0.31)**：中文叙事尤其武侠/仙侠/玄幻题材，**每个新场景的前 60-150 字应先借外部环境/景物意象起兴**(月、风、雨、雪、山、水、灯、钟、街、院……)，再自然过渡到角色心理/动作。**禁劈头就写「他很愤怒」「心中一凛」式情绪命名(tagged_opener)**；也避免直接进入对话动作的 bare_opener。三档：① xing_ok=前 60-150 字含意象不点情绪 ② bare_opener=直接动作/对话 ③ tagged_opener=情绪命名词。作者档 `scene_opener_profile.xing_ratio` 是基线；现代都市/职场/slice_of_life 题材天然低 xing 例外


## D3. 动作/战斗描写
- **句长控速**：快=短句连发(劈/砸/撕)，蓄力/恢宏=长句排比；同段内短句爆发+长句收尾=顿悟感
- 力量靠**命中反馈**：环境破坏/对手伤态/旁观者反应 > 形容词堆砌"无比强大"
- 动作场**做减法**：砍描写留五感+情绪=紧迫感，info-dump 杀节奏
- **🆕 反馈级联层次**：力量展示的说服力层次 = 环境破坏(地面龟裂/墙壁崩塌) > 对手伤态(骨折声/喷血方向) > 旁观者反应(倒退/失声) > 主角自身体感(手臂发麻/关节错位)——至少两层叠加，禁止只写「一拳把他打飞」
- **🆕 群战空间锚定**：多人混战时每 3-4 段重申一次空间关系（谁在哪/距离多远/遮蔽物位置），否则读者丢失方位感。用「双机位」技法——俯瞰全局(「三人呈三角对峙」) + 角色肩膀视角(「左侧树丛传来脚步」)交替
- **🆕 非战斗动作性格化**：日常动作泄露性格——谨慎的人怎么倒茶(先试温度)/暴躁的人怎么开门(推开不是拉开)/焦虑的人怎么坐椅子(坐不稳/反复换姿势)。不是「他喝了口水」而是「他把杯子转了两圈才喝」
- **🆕 战斗模式分档**（2025 craft research）：战斗不全是拳拳到肉——分3类写法：①**brawl**(肉搏)=短句爆裂+五感冲击+环境破坏②**tactical**(策略战)=推演+信息差+规则博弈+角色内心算计("他注意到对方左脚微跛")③**puzzle**(解谜/规则怪谈)=观察+推理+试错+顿悟。类型决定描写侧重（brawl 重感官，tactical 重心理，puzzle 重逻辑链），不混为一谈

- **🆕🗂️ 战斗景别调度(9种运镜·信息密度旋钮)**：禁全程单一景别=平板武打。按beat切——全景(建空间站位)→中景(招式表情)→决定一击近景+慢镜(放大手部/眼神)→cutaway(环境破坏/旁观)收尾。远景给空间、近景给质感(wolfcrow)
- **🆕🗂️ 战斗叙事速度四档(时长轴·正交于战斗模式)**：一场战斗内速度要变(慢-快-慢)——慢镜stretch(决定一击逐帧)/实时scene(核心对决)/蒙太奇summary(漫长群战压缩)/省略ellipsis(非关键交手跳过)(arXiv:2602.15851 Genette)
- **🆕🗂️ 实写vs虚写二分**：实写(工笔招式+技巧变化·用于传功/能力首秀/里程碑)；虚写(气势杀意+环境氛围·用于碾压速杀)。禁全场实写=招式流水账/全虚写=无具体感(金庸实写vs古龙虚写)
- **🆕🗂️ 战斗子类型(场景轴·正交于brawl/tactical/puzzle交互轴)**：单挑(高情绪近景慢镜)/群战(POV锚定+蒙太奇)/攻城(规模感+阵法)/追逐/潜行/斗法——按子类型路由节奏景别信息密度
- **🆕 追逐/逃亡工艺**：①长短句**反直觉**交替(非一味短句·长句沉浸逃者内心+短句冲击)②POV锚逃者内心驱动别逐帧旁白③追兵**分层升级**(单个→群体→不可阻挡)④给逃者具体生理缺陷做赌注(Marathon Man)

- **🆕🗂️ 疼痛感四维语法(R3·战斗/受伤/中毒/酷刑触发)**：写痛按**部位**(右肋第三根)+**质地**(刺/钝/灼/绞/搏动/电击·对应不同损伤)+**强度**(可升级)+**时间动态**(骤起/搏动节律)·接**行为级联**(打断说话/隧道视野/姿态崩塌)·禁"剧痛/疼得厉害/痛不欲生"泛化词(writershelpingwriters)

## D4. 情感/张力
- 张力**递进不跳跃**：微紧张→中张力→高潮→释放，情绪需要台阶不能直接从平静跳暴怒
- **潜台词 > 直白**：角色说的 ≠ 角色想的，对话要有层次感
- **留白**：暗示 > 明写的高明度场景（死亡/告别/背叛），写少反而给读者想象空间
- **场景以动作收尾**：不以抒情/内心独白收尾——场景结束给一个具体动作(关门/转身/放下东西)比"他心中五味杂陈"有力
- **🆕 禁过早消解冲突**（arXiv:2604.09854 实证：LLM 最大弱点）：冲突/悬念/危机提出后**必须 simmer**——不在同一场景/同一段落内解决。读者需要「担心」的时间。冲突提出→至少隔 1 个场景或 500+ CJK 才开始有解决迹象，紧迫问题也要写角色**挣扎尝试-失败-再试**而非一步到位
- **🆕 后段张力不塌方**（arXiv:2604.09854：LLM 后段张力仅人类 1/3）：cluster 的后 1/3 是张力最该升高的地方——不是收尾总结段。后段必须有新的 stakes 升级/信息揭示/反转，悬念峰值出现后至少保持 40% 基线不骤降
- **🆕 多尺度情绪协调**（arXiv:2603.13545：AI 缺多尺度情绪架构）：用词(词级：冷/热/尖/钝) → 句子(句级：长句蓄势/短句释放) → 场景(场景级：整场的情绪主调) → cluster(弧线级：从A情绪走向B情绪)——四个尺度方向一致时最有力（全部在升级紧张感），刻意逆反时最有张力（平静的话语里藏着杀意）
- **🆕 内心戏 burst+beat 节奏**（2025 craft research）：内心独白不写整段思辨——写「闪念碎片+外部动作」交替。危机时刻人脑不长篇大论，只有碎念/执念/感官闪回。模型：一句内心冲击→一个外部动作/场景→再一句内心→动作。连续 3 句以上纯内心独白无外部介入 = 节奏停滞（Narrative Flattening 内心版）
- **🆕 叙事因果双向感**（arXiv:2603.13545：AI 事件缺「意料之外情理之中」）：每个重大转折回头看必须有铺垫（伏笔/暗示/角色性格使然），但当下发生时读者不能完全预见——「意料之外 + 情理之中」是小说魔法的核心
- **🆕 禁无因之果**（ConStory-Bench arXiv:2603.05890·19 类一致性 bug 第一高发）：重大事件/转折/能力觉醒不能凭空出现——之前必须有铺垫（哪怕一句暗示/一个细节）。「突然他领悟了」❌ → 「他想起老师说过的那句话，指尖微微发烫——领悟了」✅
- **🆕 角色能力不遗忘**（ConStory-Bench·Forgotten Abilities）：角色已建立的能力/技能在面对可用它解决的困境时**必须被考虑**——即使最终选择不用（因代价/风险），也要在内心闪过或被提及。完全无视已有能力 = 读者觉得作者忘了

- **🆕🗂️ 内心戏三态渲染分类法**：心理呈现分三态——①心理叙述(远观概述心境·用于过渡)②直接独白(关键决断"他想/心说")③自由间接引语FID(叙述贴人物意识流·无引导标记·用于日常)。**禁全程单态**(尤禁全程"他想式"标记独白)(arXiv:2605.07102 SAGE / Cohn)
- **🆕 情绪颗粒度**：情绪优先用细分词(不甘/讪讪/悻悻/怅惘/悸动)或结构性呈现，少用"愤怒/悲伤/高兴/害怕"四大类粗标签直陈(SAGE Emotional Granularity)
- **🆕 情绪经角色个性化评估(appraisal)**：内心戏写"**为什么这件事对TA重要**"(勾连往事/价值观)而非贴情绪标签；同一事件不同角色因评估差异情绪应分化(arXiv:2508.09954 EBS)。**🔴 2026-06-29 若下方给了「## 🎭 场景级 Appraisal 情绪方向卡」段(manifest appraisal_directive)，按其逐条写：据 derived_emotion(情绪走向)+appraisal(为何感受·评价)+behavior_externalization(如何外化·动作/细节)落成 prose——情绪靠『事件→角色如何评价→外化成动作/细节』写，绝不写"他感到X/他很愤怒/心中一凛"式情绪词标签；余烬:上一块的强情绪不归零、本块开篇在其基础上延续/衰减**

- **🆕🗂️ 角色签名防御机制(R3·appraisal下游)**：wound-trigger场景角色用一致可识别的人物化防御(否认/投射/合理化/转移/反向形成/麻木)而非泛化"愤怒/害怕"·防御底层露出primary emotion(恐惧/羞耻/受伤)·不写心理也传创伤(lisahallwilson)

- **🆕🗂️ 心理距离四级变焦(R4·补内心戏三态的外部摄影机距离)**：场景可由远拉近——L1纯外部动作对话 / L2观察推断(似乎/像是) / L3报告内心(他想着) / L4自由间接FID(贴意识流)·禁相邻句 L1↔L4 跳变whiplash·变焦要过渡(Storm Writing School)
- **🆕🗂️ MRU反应单元微观时序(R5·Dwight Swain)**：①触发刺激/动机先落页面·反应在后(禁因果倒置「他大怒，因为刚听到噩耗」式先果后因)②单次反应按 情绪体感→不自主反射→深思动作→言语 排序(可省后段·不可倒序)·爽文直给反应则让位作者档

- **🆕 末世资源决策必扎余额(R7·apocalypse_survival 题材)**：写末世/丧尸/灾后生存时**每次关键决策(开火/补给/移动)前后给具体资源数字或余量描述**(『还剩三发』『水壶见底』『最后一格电』)·禁无限弹药/无限粮食/角色精神状态恒满血——账本式描写=末世感的根·活人比怪物可怕(真威胁来自其他幸存者)。仅 apocalypse_survival 题材激活

- **🆕 Proust 嗅觉/味觉触发闪回(R7·非自愿记忆引擎)**：闪回 beat 优先用**嗅觉/味觉触发**(桂花香/雨后泥土/烤面包/苦杏仁)·而非『他想起』『回忆涌上心头』式 hindsight tell。非自愿记忆五构(PubMed 2023 Proust effect)：①**共在的人**(气味唤起谁的在场)②**foodmaking**(吃/做食物的动作链)③**synesthesia**(气味→画面/声音/触觉跨感官)④**emotional reveries**(气味带出的情绪而非事件)⑤**scenery**(气味重建当时空间)。manifest cluster.olfactory_anchors 给的 trigger/memory_seed 优先用。advisory·作者档第一权威

- **🆕 Focalization Type×Facet 二轴(R7·FocalLens 2026/Bal/Rimmon-Kenan)**：四类聚焦人(zero零聚焦/internal内聚焦/external外聚焦/variable可变聚焦) × 三 facet(perceptual感知/psychological心理/ideological意识形态-价值评判)二轴矩阵。同焦点 facet 可解耦(看到的≠想的≠评判的)·但 **facet 切换需有意为之**——不要无意识在感知/心理/价值评判间随手跳。例如 internal+perceptual 段贴角色看·切到 internal+ideological 段贴角色评判·须有明显标记(段落分界/语态转换/聚焦人意识到正在评判)·禁同段无标记跳 facet whiplash。advisory·manifest focalization_matrix 给标准矩阵·作者档若规定聚焦偏好则以作者档为准

- **🆕 聚焦人感知边界三规则(R7 W2·Bal/Rimmon-Kenan)**：限知 POV 写作时聚焦人**不能越三道边界**——①**自体不可见**(focalizer 看不见自己的眼神/脸色/背影/瞳孔·镜面/水面例外要先建立)②**他人内心**(不能进未在聚焦的他人『心想/暗忖/盘算』·要靠表征推断『他的指节发白·像在权衡』)③**空间不在场**(不在场的远方场景禁详写·『与此同时在远处』式跳转 = 跳焦点 head-hopping)。与 R6 POV 主导者一致性正交：那个查谁主导本 scene·这个查主导者在自己 scene 里有没有越权。advisory·梦境/镜面/全知刻意越界可豁免

- **🆕 反派 fidelity·拒 safety-alignment 替代扁平化(R7 W2)**：写反派时**禁单维刻板**——『冷哼一声/嗤笑/狰狞/狂笑道/咆哮/不屑/恶狠狠/邪魅一笑』式直陈情绪是 RLHF 默认 substitution，反派真正的可怕在**操纵(manipulation)**(话术/共情陷阱/局部正确/合理化逻辑)。每个重要反派建立 voice_pack：**moral_level (L1 实用主义/L2 信念偏执/L3 系统理性/L4 共情型操纵者)** + **manipulation_signature**(他的标志性操纵手法：诉诸恐惧/诉诸归属/分而治之/扮受害者)·让反派说话时**听起来有道理**比让他怒吼可怕得多。advisory·爽文/喜剧夸张反派可豁免

## D5. 世界观/设定
- **冰山理论**：作者懂全部设定，但只 show 与当前剧情相关的 1/8，让读者脑补
- 设定通过**角色日常行为**自然展示，不 info-dump 解说（角色随手做的事 > 旁白解释规则）
- **🆕 硬/软体系展示法**（Sanderson 2025）：硬体系(有明确规则的修仙/异能)通过角色**使用和犯错**展示规则边界，不靠旁白解释；软体系(神秘力量/未知诅咒)保持**敬畏和神秘感**，展示结果不展示原理
- **🆕 设定塑造行为**（Sanderson: 深度 > 密度）：世界的地理/气候/资源决定社会结构，社会结构决定角色行为模式——不是「告诉读者这里很冷」，而是「角色说话时嘴里冒白气，穿三层皮袄走路都慢」。少量设定元素深挖互相影响 > 大量设定元素浅尝辄止

- **🆕 崇高维不可名状纪律(R7·cosmic_horror 题材)**：写克苏鲁/宇宙恐怖/高阶存在/异象/末法时**禁直白命名高阶存在的形态**(不写『一只巨大章鱼头的神』『XX 神的真身降临』)·靠『不该如此存在的』『违背几何的』『感官无法整合的』式间接逼近·主角看到时用**感官崩坏**(眼疾/呕吐/失语/耳鸣)代替正面描写。**理智衰退靠症状**(失眠/妄想/幻听/记忆错位/语言能力退化)不靠『他疯了』旁白。**知识即代价**：每次理解真相一步必付精神/生命/亲人代价。**宇宙尺度做底色**：频繁穿插宇宙尺度对照(『这块石头比太阳还古老』『它出现时人类祖先还是细菌』)·徒劳感是题材魅力。仅 cosmic_horror 题材激活

## D6. 对话工艺
- 每个角色有**独特语言节奏**：句长/口癖/用词层级/省略习惯——遮住名字也能认出谁在说话
- **潜台词 > 直白表达**：角色有说不出口的期待时，对话才有张力（表面聊天气，实际在试探）
- 对话标签**疏化**：90%+ 只用"说/问"，花哨标签(怒斥/哀叹)仅作点缀；用动作 beat 替代标签("他把杯子摔桌上" > "他愤怒地咆哮道")
- **对话不干净**：真人说话有停顿、打岔、半截话、答非所问——太流利太有逻辑 = AI 腔
- **禁 on-the-nose**：角色不把心里话说全，不在对白里讲故事/解释背景——读者讨厌角色当旁白机器
- **🆕 权力动态驱动对话**：地位不对等的对话（上下级/师徒/强弱）天然有张力——弱势方的每句话都在权衡代价，强势方的随口一句都是压力。让这种不对等**渗透进句式**（强势方句子短/断/命令式，弱势方句子长/绕/试探式）
- **🆕 关键词回声**（echoing technique）：角色 A 用的某个关键词/比喻被角色 B 在后面场景无意中重复 = 主题强化（「打碎」——A 说打碎规则，3 场景后 B 说打碎了一只碗，读者下意识联想）。不超过 2 次/cluster，多了刻意
- **🆕 对话四功能意识**：每段对话主要服务于 ①推剧情 ②揭性格 ③传情绪 ④交代信息 中的**一个**。同一段对话试图同时完成四个 = 信息过载、角色像百科全书。不知道这段对话为什么存在 → 删掉

- **🆕 争吵/冲突对话非对称**：张力来自**非对称互递**——禁双方同步对称升顶的平直争吵。要么一方加码另一方破防，要么转折点=一方突然降温不接招(arXiv:2509.04465)
- **🆕🗂️ 审讯/试探/隐瞒对话指纹**(破绽在"怎么说"不在"说什么")：隐瞒方=反问转移/模糊限定词/答句更短/引向枝节/回避细节；诚实方=锚定具体可验证细节；审讯者写他盯对方语气/回避/答句长短落差(arXiv:2606.13310 RogueAI)
- **🆕 语域随对象+情绪切换**：同角色对上级/下属/亲密用不同语域；激动时退回母语域/方言/口癖。身份连贯前提下保持弹性，别固化成单一语域(arXiv:2601.17277 PingPong)
- **🆕 权力上风动态转移**：地位不对等对话别一方从头碾压到尾，要有上风易位转折(弱势方抓把柄/强势方露怯)
- **🆕 群戏发言权不均**：3+人对话用"主导者(1-2人)+反应者(短反应)"结构，指称靠语境别机械点名，角色随场景松动语域不必每句贴人设(arXiv:2603.04969 MPCEval)

- **🆕 非偏好回应微结构(会话分析·R2)**：角色实施非偏好社会动作(拒绝/反对/否决/报坏消息)时,回合须成形为"延迟token(嗯…/那个…/呃)+缓冲hedge+account(解释为何不能照办)"(+可先肯定对方);禁裸句即答——裸句即答只留给同意/上位强势角色的碾压式回绝(PMC8504554·非偏好回应延迟561ms vs偏好269ms)

- **🆕🗂️ 对话自我修复(R3·会话分析·认知/欺骗/紧张微表征)**：高认知负荷/说谎/紧张时台词带同轮自纠(切断重启/找词停顿/中止改述)·映射心理态(找词=回忆吃力·中止改述=自我审查)·高张力对话零自修=AI扁平信号(Schegloff十操作)
- **🆕🗂️ 他启修复OIR三型(R3·误解/听岔序列)**：把理解障碍写成3轮序列(麻烦源→修复发起→完成)·按关系选发起型(open"啊?"=彻底懵 / restricted部分重复+疑问词=精准质问 / candidate"你是说X?"=拼真相节拍)·修AI"角色永远完美互懂"通病(Dingemanse OIR)

- **🆕🗂️ 对白即行动 Dialogue-as-Action(2026-06-29·McKee verbal action·治 on-the-nose)**：若 cluster_brief/manifest 的 scene 带 `dialogue_objectives`，照它演——**本场角色对白即行动：X(character) 想要 Y(wants)·用 Z(tactic·active verb 言语策略:试探/施压/回避/示弱/反问) 策略·被 W(obstacle) 阻挠**。对白是策略不是信息：每句台词都是为达成 wants 采取的动作，不是把目标/背景/设定念出来。**每句话底下压着未说出口的目标(what_unsaid)——潜文本只驱动表演、绝不写进正文；严禁把内心想法/情绪/目标直接说出口(透明原则·on-the-nose=说透目标=零潜台词=AI 腔)**。dialogue_act 是本句意图骨架(what)，措辞(how)你自由发挥。场景级软提示非逐句锁，爽文直球对喷可豁免 subtext(作者档第一权威)

## D7. 叙事节奏
- **快慢交替**：高密度动作场(短段/快节奏) 后必须有低密度喘息场(长段/反思/日常)——匀速 = 疲劳
- **信息密度控制**：每段/每场景承载 1 个核心信息点，不同时塞设定+伏笔+角色弧+世界观
- **show 关键时刻，tell 连接组织**：重要情感节拍/转折/揭示 → 展开细写；场景间过渡/时间推进 → 简笔带过
- **🆕 禁同场消解**（D4 reinforcement·arXiv:2604.09854 LLM 第一弱点）：一个场景内提出的问题/危机/悬念，**不在同一场景解决**。场景末读者带着未解的问题离开 = 翻页动力。「提出→挣扎→部分进展→新问题」才是节奏，不是「提出→解决→下一个」
- **🆕 时间结构多样化**（StoryScope 实证：AI 默认线性现在时）：不是每个场景都按时间顺序讲。适时用**闪回**（触发物→记忆片段→回到现在，300 字内收）、**时间跳跃**（「三天后」「入夜」简笔带过）、**预叙暗示**（「他不知道这是最后一次见到她」）——打破线性 = 时间层次感
- **🆕 多线编织**（StoryScope 实证：AI 偏好「整洁单轨」情节）：cluster 内至少 2 条叙事线交替推进——A 线(主矛盾) + B 线(副线/暗线/角色私事)，在场景切换点交织。B 线不是填充物，它最终要与 A 线**碰撞或呼应**
- **🆕 张力峰值留存**（arXiv:2604.09854：人类作者保留 52% 峰值 vs AI 仅 23%）：张力峰值（反转/揭示/危机）出现后，不立刻把情绪拉回平地——让余波荡漾（角色震惊后的沉默/旁人的窃窃私语/环境的异常安静），下一段才缓慢回落
- **🆕 叙事功能多样化**（Creative Convergence arXiv:2603.14430·100 部中文网文实证）：LLM 倾向机械复现「英雄出发→英雄战胜敌人→英雄凯旋」三段式模板。真实网文作者混用 6 种范式——战斗(冲突→胜负)、情感(触动→变化)、困难任务(挑战→完成)、冒险(探索→收获/危机)、扮猪吃虎(伪装→揭底→震惊)、日常(平凡互动→性格展示)。一个 cluster 内至少出现 2 种范式，不要全是战斗-胜利循环

- **🆕 留人杠杆优先级:好奇缺口>悬念>惊讶**：最强留人是"好奇缺口"(让读者想知道某个已知存在的答案)>悬念(担心结果)>惊讶(意外)。**惊讶过量为负效应**(频繁反转=疲劳失信任)，优先抛未解的具体问题留人

- **🆕 章末配比软目标 80:15:5(hook:cliffhanger:scene_end)**（R7 W2 Batch-E·任随作者档·advisory）：拟切点章末类型混搭，约 80% 用好奇缺口/未解问题型 hook、15% cliffhanger 强反转、5% scene_end 自然收束。**绝不连续 ≥3 章用 cliffhanger**——读者会失去对反转的信任（"惊讶过量为负效应"）。作者档若注明专属偏好（如纯爽文每章 cliffhanger / 文艺向多 scene_end）以作者档为准

- **🆕🗂️ 悬念三尺度同时在线(嵌套·R2)**：每cluster至少挂①一条细节性悬念(场景级·本块开可不收)②service至少一条阶段性悬念(卷线索)③不消解统领性悬念(全书终极问题)。三层映射cluster/卷/全书,某尺度长期真空=平铺直叙(Rowling三层悬念)
- **🆕🗂️ 误导/线索两分法(反转/悬疑cluster·R2)**：写反转时虚假线索(红鲱鱼)与真伏笔分开登记——每条假线索须在主情节有第二用途(过"删掉它主情节是否需调整"的可删除性公平测试·否则=读者受骗感);真线索用"紧跟大动作冲淡注意力"或"混入清单"伪装而非高亮(《写作》期刊反转工程化)

- **🆕 第一人称回溯 hindsight 签到(R7·Stanzel/Cohn·narrative_pov_mode=first_retro_*)**：第一人称回溯叙述(consonant 贴近 experiencing-self 或 dissonant 拉远 narrating-self)需要回顾视角定期签到——『回想起来…』『后来才明白…』『那时候我并不知道…』『多年后回头看…』式 narrating-self 评点至少每千字一次·让读者感知到 experiencing-self(当下我)和 narrating-self(回顾我)的距离。否则退化成第一人称当下时·失去回溯感。**与禁止未来知识泄露正交**:前者管不应有(hard_gate)·本者管应有不足(advisory)。仅 first_retro_consonant / first_retro_dissonant 模式激活

## D8. 喜剧/幽默工艺（R3·独立维度·按题材激活）
- **🆕🗂️ 喜剧引擎归类**：幽默非随机抖机灵·分型驱动——①**信息差喜剧**(角色不知读者知 / 主角独占硬信息·配角不降智)②**性格喜剧**(性格缺陷自然生笑·毒舌/社恐/轴)③**情境错位**(庄重场合出岔 / 身份错认)④**语言喜剧**(谐音/反差用词/一本正经胡说)⑤**节奏喜剧**(三段式抖包袱:铺垫-强化-翻转·callback回旋镖)。喜剧cluster混用≥2型·笑点锚在角色与情境逻辑(非旁白硬挠)

## D9. 时长比例工艺（R8 W4·L22 Genette《Narrative Discourse》五种持续模式）
- **🆕🗂️ 五型时长比 (scene/summary/ellipsis/pause/stretch)**：叙述时长 vs 故事时长比的根盘工艺指纹——① **scene 场景** 叙述时长≈故事时长 (对话/即时动作·LLM 默认偏好)；② **summary 概述** 多事件压缩 (『整整三天』『连日来』『接下来的几个月』)；③ **ellipsis 省略** 时间跳过 (『一晃数日』『翌日』『多年后』)；④ **pause 停顿** 故事时间冻结·叙述继续 (景物特写/心理停留/作者议论)；⑤ **stretch 拉伸/慢镜** 叙述时长 > 故事时长 (『一瞬间她想起』『一帧帧』『时间凝固』)
- **🆕 LLM 默认产 scene+summary 高占比·几乎为 0 的是 ellipsis/stretch/pause = 节奏单调**：每个长 cluster 都该有 ≥1 处 ellipsis 时间跳跃 + ≥1 处 stretch 慢镜定格·关键决断/受伤/顿悟瞬间适合 stretch (一瞬间脑海中…) · 长跨度过渡适合 ellipsis (一晃半月) · 场景间情绪沉淀适合 pause (景物特写)
- **🆕 作者档 duration_mix_baseline 优先**：作者风格档若有 `duration_mix_baseline` (scene_pct/summary_pct/ellipsis_pct/pause_pct/stretch_pct 的 mean/std)·**新 cluster 整体节奏向作者基线靠拢** (不要平均推到通用值)。作者签名 = 五型混搭节奏 = 比段长/句长更深的指纹
- **🆕 五型混搭 vs 平均**：禁同一 cluster 全 scene (流水账即时叙述) 或全 summary (报告体)·按场景切换 (打斗用 scene+stretch · 关键瞬间慢镜 · 大段时间用 summary 压缩 · 转章接续用 ellipsis · 章节呼吸点 pause 一段景物)

## D10. 元叙事越界预算 (R8 W4·L25 Pier Metalepsis LHN 2014·MasterClass·马良系统流 marker)

- **🆕🗂️ 看作者档 metalepsis_budget**：题材声明决定能不能写元叙事。`type` 四档：
  - `none`：爽文/玄幻/古风/历史默认——**禁任何元叙事 marker**(【系统】▶式标记 / 「亲爱的读者」「你以为」式破壁)
  - `rhetorical`：系统流/无限流/规则怪谈默认——只允许 `marker_style` 列表里的口头评点 / 【系统提示】式 cue，**禁本体越界**(角色不感知自己是故事人物)
  - `ontological`：元小说专用——允许角色越界感知叙述层，但每次切换后 **300 字内必须明确闭合**(回到正文动作 / 角色继续说话)
  - `mixed`：rhetorical + ontological 混用
- **🆕 target_per_cluster 软目标**：当前 cluster 实际 marker 数与 target 偏差控制在 ±50% 内。**绝不连续 5 段每段都【系统提示】插入**=滥用，读者疲劳
- **🆕 allowed_speakers 严格执行**：只允许 `allowed_speakers` 列表里的角色越界(常见值: system / narrator / protagonist)。其他角色破壁 → 视为身份漂浮
- **🆕 与 D12 narratee 关联防双计**：narratee 类越界(「亲爱的读者」)的称谓稳定性由 D12 管，本节只管 marker 总量预算

## D11. 苦难场景 EC vs PD 二相平衡 (R8 W4·L27 Nature Sci Rep 2025 EC/PD·Keen Theory of Narrative Empathy)

- **🆕🗂️ 苦难场景区分 Empathic Concern vs Personal Distress**：当场景标签 ∈ {suffering, grief, sacrifice, torment, desperation} 时——
  - **EC(共情关切)**：旁观者 / 同伴 / 主角的**对他人苦难的关切+采取行动**：伸手、上前、搀扶、守护、挡在前面、背起、抱起、相信、不会放弃、护住、为了某人而……
  - **PD(自我苦痛)**：旁观者 / 同伴 / 主角的**自我中心的恐慌+退缩**：颤抖、不敢看、捂住眼睛、瘫坐、崩溃、无能为力、僵住、心如死灰、麻木
- **🆕 ec_pd_ratio ≥ 0.4 软线**：PD 过载(只剩颤抖/不敢看/瘫坐)=苦难写成自怜剧本(Personal Distress dominant)·读者也跟着退缩。**至少同等比例**给 EC：在惨烈中**保留 agency 残留**(主角仍挣扎/保留尊严)、**旁观者关切动作**(伸手/守护/挡在前面)、**dignity 保留**(濒死时仍有姓氏/最后一句话/眼神交付)。荷马式英雄抗争 > 自怜独白
- **🆕 与 R7 Nummenmaa body map 协同**：body map 管「身体哪儿热/冷/紧」；EC/PD 管「面对他人苦难是关切还是退缩」——独立两维

## D12. 反讽 Discordance 4-cue + narratee 注册器 (R8 W4·L28 Booth Rhetoric of Irony·Phelan Ideal Narratee Poetics Today 2022)

- **🆕🗂️ 当作者档 ironic_voice_profile.stable_irony=true 时**：以下 4 类 discordance 是反讽密度的来源，**预算 discordance_target /千字**——
  1. **saying_doing 言行反差**：『嘴上说……心里却』『一边……一边』『口口声声……实际上』
  2. **style_fact 语体错配**：宏大词配琐碎事实(『英雄般地走进厨房买菜』『庄严宣告吃泡面』)
  3. **world_clash 世界观冲撞**：神圣词配世俗污渍 / 古风词配现代物
  4. **value_clash 价值观冲撞**：正面词反向使用『真是个好人！』(语境明显在骂) / 『天才操作』『多亏了你救命』
- **🆕 narratee_registry 一致性**：当 cluster 出现破壁式叙述者直接对受述者说话时，称谓**必须锁定 `narratee_registry.primary`**(如全篇『亲爱的读者』)·禁混用『诸位看官』『各位』『你』等。**min_consistency ≥ 0.8** = primary 至少占所有 narratee 称谓 80%。**allowed_addresses 之外的称谓一律不出现**
- **🆕 与 D10 metalepsis 关联**：narratee 越界(『亲爱的读者』)算 D10 的 narratee 类 marker，**称谓一致性单独由 D12 计数**——不双计但都要满足

# 输出格式（v29 分段润色模式）

**核心原则：你输出的是这一段初稿的润色全文——连续叙事片段，给后续 chapter-splitter 决定章节自然截断点的素材。**

**严禁预设章节分界**：
- ❌ 不要写「第 N 章 标题」/「第N章 标题」等章节标记
- ❌ 不要用「——」或其他分章分隔符把故事切开
- ❌ 不要写 Markdown 标题（# / ## 等）
- ❌ 不要写「以下是」/「故事开始」/「润色后」等元话语

**正确做法**：按初稿的场景推进原样润色。场景内的自然过渡（空行 / 时间标记句 / 视角切换句）保留结构，**不打章节标签**。splitter 后期会根据自然截断点切分章节并各自命名。

🔴 **你是润色引擎，不是对话助手**（reasoning/instruct 模型尤其注意）：
- 禁止停下来问我、禁止说「请审阅」「需要我继续吗」之类的话、禁止等我确认——润色全文必须在**这一次回复**里一次性给全。
- **不要输出任何 JSON / CHANGES 块**（自评与遥测由系统另行处理，你只交付润色正文）。
- 正文必须是**纯中文叙事**：严禁在正文里夹任何英文词（NPC/BUG/cluster/JSON 等流程词或技术词一律用中文表达），严禁任何流程说明/润色说明/元注释/对读者喊话（除非作者风格档本身要求破壁旁白）。
"""

    # v22.gov.align.fix Gap T2-X: cluster 硬约束段（顶部显著位）
    cluster_constraints_section = ""
    if cluster_brief_text:
        cluster_constraints_section = f"""## ⚠️ CLUSTER 硬约束（最高优先级 · 违反即重写）

cluster_brief 完整内容：
```json
{cluster_brief_text}
```

### 🎯 必遵守硬指标清单（从 cluster.scope_summary + hard_constraints 提取）

{cluster_hard_constraints_text if cluster_hard_constraints_text else '（本 cluster 无硬约束 · 按一般指南）'}

**写完正文必自查**：上述硬指标**任一**不达标 → 视为重大失败，自查 JSON 中明确标 `cluster_constraints_violated: true`。
**scope_summary 不是参考，是契约**：cluster.scope_summary 描述的场景类型（如"对白场景"）、角色数（如"≥3 角色"）、占比（如"对话占比 ≥60%"）等是**硬契约**，不是建议。

---

"""

    task_intro = f"""# 润色任务（v29 · Claude 亲笔草稿 → 你按作者风格档等体量重写）

本项目 cluster_{cluster_id:03d} 的正文初稿已由另一位写手按 scene_storyboard 逐场景写好。你的任务是把 prompt 末尾给出的**其中一段初稿**以作者本人的手笔**整体重写润色**。

**🎯 v29 润色纪律**：
- **情节走向、事件顺序、关键事实、对话信息量完全不变**——下方 manifest / cluster_brief / 人物卡提供的锁定事实、称谓、数值、道具持有链，一个字都不许改动语义。
- 语言、节奏、段落切分、对话腔调全面向作者风格档靠拢（数值契约表第一权威）。
- **等体量重写**：不许压缩省略情节，也不许注水扩写铺陈（具体字数带见 prompt 末尾）。
- 你**不知道**也不需要知道章数（章数与每章篇幅由 splitter 后期切分）。

⚠️ **重要提醒**：输出仍是**连续叙事**片段。**严禁**写「第 N 章 标题」/「——」分章符 / Markdown 标题 / 元话语。
"""

    # 种子段拼接（mode=off/shadow 时 seed_section 为空 → 不注入 · 零回归）
    seed_block = (seed_section + "\n\n") if seed_section else ""
    # 作者量化风格指纹段（PROFILE_INJECT_MODE=off/shadow 或无指纹时为空 → 不注入 · 零回归）
    style_fp_block = (style_fp_section + "\n\n") if style_fp_section else ""
    # 阶段1：节奏指纹段（与量化指纹并列贴生成点·空则零回归）
    rhythm_block = (rhythm_section + "\n\n") if rhythm_section else ""
    # 阶段2：决策原则段（与节奏指纹并列贴生成点·空则零回归）
    decision_block = (decision_section + "\n\n") if decision_section else ""
    # 阶段3：题材专属工艺段（与决策原则并列·空则零回归）
    genre_block = (genre_section + "\n\n") if genre_section else ""
    # 阶段D3：信息差主调段（与决策原则并列·序列骨·默认 shadow 时空 → 零回归）
    knowledge_gap_block = (knowledge_gap_section + "\n\n") if knowledge_gap_section else ""
    # 🔴 2026-06-29 角色信息差段（无 ledger / 无 participants → 空 → 不注入 · 零回归）
    belief_block = (belief_section + "\n\n") if belief_section else ""
    # 🔴 2026-06-29 场景级Appraisal Beat段（无 appraisal_beats → 空 → 不注入 · 零回归）
    appraisal_block = (appraisal_section + "\n\n") if appraisal_section else ""
    narr_seq_block = (narr_seq_section + "\n\n") if narr_seq_section else ""
    golden_fewshot_block = (golden_fewshot_section + "\n\n") if golden_fewshot_section else ""
    deep_dims_block = (deep_dims_section + "\n\n") if deep_dims_section else ""
    rolling_anchor_block = (rolling_anchor_section + "\n\n") if rolling_anchor_section else ""
    # R7 Batch-D：D7 叙事债务状态卡 block（advisory · 空则零回归）
    debt_ledger_block = (debt_ledger_section + "\n\n") if debt_ledger_section else ""
    # A3 前块结尾回响 block（cluster_001 / 无前块草稿 → 空 → 不注入 · 零回归）
    prev_tail_echo_block = (prev_tail_echo_section + "\n\n") if prev_tail_echo_section else ""
    # A4 编辑手记 block（manifest 无 editor_note → 空 → 不注入 · 零回归）
    editor_note_block = (editor_note_section + "\n\n") if editor_note_section else ""
    # 硬约束维 primacy 重述段（SKILL_PRIMACY_MODE=off/shadow 时为空 → 不注入 · 零回归）
    # 传作者情绪标点基线 → 情绪标点密的作者(搞笑流)在生成点近邻强调 ！？…（治 flash 全量 prompt 下写成叙述向）
    primacy_section = _build_hard_constraint_primacy_block(
        _author_emotive_punct(db), _author_para_dialogue(db))
    primacy_block = (primacy_section + "\n\n") if primacy_section else ""

    # ── 风格 skill 段（第一权威）+ 语感种子锚 ──
    style_skill_section = f"""## 风格 skill

{style_skill}"""

    # CTX_REORDER（P0 · 位置层北极星偏移修）：lost-in-the-middle / RoPE recency 实证——
    # context 中段注意力最弱（U 型），紧贴生成点（prompt 末尾）注意力最强。
    # 原版 join 把**第一权威风格 skill**落在中段最低注意力区，而紧贴生成点的是 manifest
    # 事实索引（非风格锚）= 位置偏移。active 模式把风格 skill + 语感种子锚移到 prev_ch 之后、
    # 「现在执行润色」之前的生成点近邻（RoPE 高位），manifest 事实索引留中段。
    # 正交于 D1-D9 / snippet 种子（那些管「注什么内容」）——本开关只管「注在哪个位置」。
    reorder_active = (_ctx_reorder_mode() == "active")
    if reorder_active:
        # 中段：cluster_blueprint + 人物卡 + 调研 cache + 用户偏好 + manifest（事实索引）
        # 风格 skill + seed 锚下沉到 prev_ch 之后、生成点之前。
        #
        # 第8轮同族补完（style_fp 下沉 + 硬约束 primacy）：第8轮 ctx 重排只下沉了风格 skill + seed 锚，
        # 量化指纹 style_fp_block 仍留在 prompt 顶部（task_intro 后），落在 lost-in-the-middle dead zone。
        # 量化数值约束（句长/段长/对话占比目标）比叙述性 skill 更怕稀释（4 源验证），现把 style_fp_block 也
        # 下沉到生成点近邻（RoPE 高位 · 风格锚区），紧跟风格 skill 之后；再补一段硬约束维 primacy 重述
        # （IFScale 实证：长 skill 中段硬约束维衰减）。三者都贴生成点，顺序：
        #   prev_ch → seed → 风格 skill → 量化指纹 → 硬约束 primacy → 生成点。
        prev_then_anchor = f"""{prev_ch_section}

{prev_tail_echo_block}{seed_block}{style_skill_section}

{style_fp_block}{rhythm_block}{decision_block}{genre_block}{knowledge_gap_block}{belief_block}{appraisal_block}{narr_seq_block}{rolling_anchor_block}{golden_fewshot_block}{deep_dims_block}{debt_ledger_block}{editor_note_block}{primacy_block}"""
        user = f"""{task_intro}
{cluster_constraints_section}## cluster_blueprint（必落 anchors）

```json
{plan_text}
```

## 人物卡（必读 voice_pack）

```json
{char_card}
```

## 调研 cache（写作前必读 synthesis）

{cache_text}

## 用户偏好

{pref}

## manifest（数据库索引）

{manifest}

{prev_then_anchor}

---

# 现在执行润色"""
    else:
        # off / shadow：原版 join 顺序（零回归回退路径）
        user = f"""{task_intro}
{cluster_constraints_section}{style_fp_block}{rhythm_block}{decision_block}{genre_block}{knowledge_gap_block}{belief_block}{appraisal_block}{narr_seq_block}{rolling_anchor_block}{golden_fewshot_block}{deep_dims_block}{debt_ledger_block}{editor_note_block}{seed_block}## cluster_blueprint（必落 anchors）

```json
{plan_text}
```

## 人物卡（必读 voice_pack）

```json
{char_card}
```

{style_skill_section}

## 调研 cache（写作前必读 synthesis）

{cache_text}

## 用户偏好

{pref}

## manifest（数据库索引）

{manifest}

{prev_ch_section}

{prev_tail_echo_block}{primacy_block}---

# 现在执行润色"""

    # v29 润色点尾部：等体量润色指令 + 本段 Claude 亲笔原文。
    # 上面全部 manifest / 风格 / brief / 人物卡 sections 原样保留——润色需要与写作
    # 同等的事实与风格上下文（锁定事实 / 伏笔隔离 / 声纹依据都在其中）。
    _pv_idx = int(polish_view.get('idx', 0))
    _pv_total = int(polish_view.get('total', 1))
    _pv_src = polish_view.get('scene_text', '')
    _pv_src_cjk = cio.count_cjk(_pv_src)
    polish_point_tail = f"""

---

# 润色任务（第 {_pv_idx + 1}/{_pv_total} 段）

下面是这个故事块**第 {_pv_idx + 1} 段（共 {_pv_total} 段）**的初稿。请你以上述作者风格档的手笔，把这一段**整体重写润色**：

- **情节走向、事件顺序、关键事实、对话信息量完全不变**——锁定事实（数值/称谓/道具持有）一个字都不许改动语义。
- 语言、节奏、段落切分、对话腔调全面向风格档靠拢（句长/段长/单句独行/标点分布以数值契约表为准）。
- **等体量重写**：这一段初稿约 {_pv_src_cjk} 个汉字，你的输出必须落在 {int(_pv_src_cjk * 0.85)}-{int(_pv_src_cjk * 1.3)} 个汉字之间——不许压缩省略情节，也不许注水扩写铺陈。
- 严禁 AI 套话与禁用词（见上方硬约束）；对话用中文弯引号；非对话段一段只一个句末结束符。
- 伏笔相关内容按初稿原样保留埋设深度——不解释、不点破、不加暗示。
- 只润色这一段；直接输出润色后的这一段正文全文，不要标题、不要解释、不要输出任何 JSON。

【第 {_pv_idx + 1} 段初稿】

{_pv_src}"""

    user += polish_point_tail

    return system, user, seed_trace


# ============ Gen-Model 调用（含 fallback 链） ============
def _build_cont_msg(cont_reason: str) -> str:
    """续写指令文案（openai / gemini 两协议共用 · DRY）。

    CTX_REORDER（P0 · 位置层北极星偏移修）：续写回合原本只有「接着写别重复」，风格 skill 落在最初
    那条 user（被推到中段 U 型最低注意力区），续写生成点近邻没风格约束 → recency 漂移。这里补一行
    精简风格锚（句长 / 对话格式 / 禁结构套话）贴生成点 RoPE 高位重申，防长草稿尾段风格崩塌（advisory）。
    """
    if cont_reason == "changes_only":
        # 🔴 2026-06-28 审计清理A类：changes_only 兜底不再列举 factual（locked_facts/伏笔/出场角色），
        # 只补创作期自评 self_eval/waivers + 确定性遥测；factual 状态由 Claude 读正文梳理 → apply_archive 回库。
        cont_msg = ("正文已经写完。现在请**只输出**这个故事块结尾的 CHANGES JSON 块"
                    "（用 ```json 围栏包裹），**不要再写任何正文、不要重复正文内容**。"
                    "JSON 只需包含 self_eval（本块创作自评 + applied_style）与 waivers（如有豁免）"
                    "+ 确定性遥测（word_count_cjk / paragraph_count 等）——不要自报角色 / 道具 / "
                    "locked_facts / 伏笔等剧情事实状态。")
    else:
        cont_msg = ("上一条回复因长度上限被截断了。请接着上文最后一个字继续往下写，"
                    "不要重复已经写过的内容、不要重新开头，直接续写后续正文"
                    "（如果正文已写完，就补上结尾的 CHANGES JSON 块）。")
    if _ctx_reorder_mode() == "active":
        cont_msg += (
            "\n\n续写仍须贴合作者风格档：① 句长 / 段长节奏沿用前文（别越写越碎或越堆长）；"
            "② 对话格式与前文一致（同款引号、对话独行）；"
            "③ 严禁结构性 AI 套话（与此同时 / 值得一提的是 / 不仅如此 / 事实上）。")
    return cont_msg


def _stream_once(client, profile, system: str, user: str, max_tokens: int,
                 prior_assistant: str | None = None, cont_reason: str = "length") -> tuple[str, "str | None"]:
    """单次 stream 生成，返回 (text, finish_reason)。

    2026-06-05 协议分发：profile.protocol == 'gemini' → 走原生 streamGenerateContent（隐式前缀缓存）；
    否则走 OpenAI /v1/chat/completions（client 已建好）。
    2026-05-30：捕获 finish_reason（命中 max_tokens 的截断别静默吞）。prior_assistant 非空 → 续写模式。
    cont_reason：'length'=截断续写；'changes_only'=只补 CHANGES。
    """
    # 2026-06-19：prompt 大小预检——超过 profile.max_prompt_chars 直接跳 fallback，不等 100s 超时
    _max_pc = getattr(profile, "max_prompt_chars", None)
    if _max_pc and (len(system) + len(user)) > _max_pc:
        raise PromptTooLargeError(
            f"prompt {len(system)+len(user)} chars > {profile.name}.max_prompt_chars={_max_pc}，跳 fallback")

    if getattr(profile, "protocol", "openai") == "gemini":
        return _stream_once_gemini(profile, system, user, max_tokens, prior_assistant, cont_reason)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    if prior_assistant:
        messages.append({"role": "assistant", "content": prior_assistant})
        messages.append({"role": "user", "content": _build_cont_msg(cont_reason)})
    _create_kw = dict(model=profile.model, messages=messages, max_tokens=max_tokens,
                      temperature=profile.temperature, stream=True)
    # reasoning 控制 extra_body（thinking_level=gemini 专有/reasoning_effort=OpenAI 标准·helper
    # 单一真理源·按 profile 配·防 thinking 暴走·elysiver 2026-06-16 实测 thinking_level 被忽略致暴走 500）。
    _wr_extra = reasoning_extra_body(profile)
    if _wr_extra:
        _create_kw["extra_body"] = _wr_extra
    try:
        import gen_throttle
        gen_throttle.wait()   # 限速端点（中转站 <15rpm）：请求前全局节流·默认关零回归
    except Exception:
        pass
    stream = client.chat.completions.create(**_create_kw)
    text = ""
    finish_reason = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        piece = getattr(choice.delta, 'content', None)
        if piece:
            text += piece
            sys.stderr.write(piece)
            sys.stderr.flush()
        if getattr(choice, 'finish_reason', None):
            finish_reason = choice.finish_reason
    return text, finish_reason


def _gemini_host(base_url: str) -> str:
    """从 OpenAI 风格 base_url(.../v1) 推 gemini 原生 host(去掉 /v1 尾)。"""
    return base_url.rsplit("/v1", 1)[0] if "/v1" in base_url else base_url.rstrip("/")


def _stream_once_gemini(profile, system: str, user: str, max_tokens: int,
                        prior_assistant: str | None = None, cont_reason: str = "length") -> tuple[str, "str | None"]:
    """gemini 原生协议 streamGenerateContent（SSE）· 稳定 system 放 systemInstruction → 隐式前缀缓存命中。

    返回 (text, finish_reason)，finish_reason 归一到 openai 口径（MAX_TOKENS→'length' 触发续写·其余→'stop'），
    上层截断续写逻辑零改动复用。缓存命中 cachedContentTokenCount 打到 stderr 可见。
    """
    import urllib.request

    host = _gemini_host(profile.base_url)
    url = f"{host}/v1beta/models/{profile.model}:streamGenerateContent?alt=sse&key={profile.api_key}"

    contents = [{"role": "user", "parts": [{"text": user}]}]
    if prior_assistant:
        contents.append({"role": "model", "parts": [{"text": prior_assistant}]})
        contents.append({"role": "user", "parts": [{"text": _build_cont_msg(cont_reason)}]})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},  # 稳定前缀(system+skill) → 跨调用隐式缓存
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": profile.temperature},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")

    text = ""
    finish_raw = None
    usage = {}
    # 🔴 BYOK 脱敏（对抗审查 must_fix#3）：gemini key 在 URL（?key=<KEY>），urlopen 的
    # HTTPError/URLError str() 会带整条 URL → 经 stderr → GUI LogBuffer → 界面。BYOK 路由
    # 真实用户 key 必须脱敏后再抛/打印。
    try:
        import gen_throttle
        gen_throttle.wait()   # 限速端点全局节流（gemini native path）
    except Exception:
        pass
    try:
        resp = urllib.request.urlopen(req, timeout=GEN_MODEL_TIMEOUT)
    except Exception as _e:
        try:
            from secrets_store import redact as _redact
        except Exception:
            def _redact(s):  # 兜底：至少截断 key=
                import re as _r
                return _r.sub(r"(key=)[^&\s]+", r"\1***", str(s))
        raise RuntimeError(f"gemini 请求失败: {_redact(str(_e))}") from None
    for raw in resp:  # 按行迭代 SSE（每事件一行 data: {json}）
        line = raw.decode("utf-8", "ignore").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for c0 in (d.get("candidates") or [])[:1]:
            for part in (c0.get("content", {}).get("parts") or []):
                if part.get("thought"):  # 跳过 reasoning thought 段（只要正文）
                    continue
                t = part.get("text")
                if t:
                    text += t
                    sys.stderr.write(t)
                    sys.stderr.flush()
            if c0.get("finishReason"):
                finish_raw = c0["finishReason"]
        if d.get("usageMetadata"):
            usage = d["usageMetadata"]

    cached = usage.get("cachedContentTokenCount")
    if cached:
        logger.info(f"\n[gen_writer][gemini] 🟢 缓存命中 cachedContentTokenCount={cached}"
              f"/{usage.get('promptTokenCount', '?')} prompt tokens（省 input 成本）")
    finish_reason = "length" if finish_raw == "MAX_TOKENS" else ("stop" if finish_raw else None)
    return text, finish_reason


# API 调用健壮性常量（2026-05-30 加固）
GEN_MODEL_TIMEOUT = 180.0  # 与 llm_transport.DEFAULT_TIMEOUT 对齐
GEN_MODEL_MAX_RETRIES = 3  # 同 profile 限流/超时的有限重试次数
GEN_MODEL_RETRY_BASE_DELAY = 2.0  # 指数退避基础秒数（2,4,8）


def _filter_creative_profiles(candidates):
    """🔴 2026-06-28：写正文禁 flash-tier 兜底（质量攸关）。

    根因：fallback 链 pro_preview→pro→flash·中间 gemini_pro 渠道持久 503 model_not_found·
    pro_preview 一旦瞬时 502 就直接掉到 flash → 静默用 flash(碎句·被淘汰差模型)写正文，
    违背「gen-model 锁定 pro」决策(memory project_genmodel_flash_locked)。实测 cluster_002 被 flash 写。
    改：写作候选剔除 model/name 含 'flash' 的 profile → pro 全挂则响亮 GenModelExhaustedError
    (主代理重试·等中转站恢复)，绝不静默降质。
    """
    filtered = [p for p in candidates
                if "flash" not in (getattr(p, "model", "") or "").lower()
                and "flash" not in (getattr(p, "name", "") or "").lower()]
    dropped = [getattr(p, "name", "?") for p in candidates if p not in filtered]
    if dropped:
        logger.info(f" [creative-guard] 写正文禁 flash 兜底 → 排除 {dropped}"
                    "（质量攸关·pro 全挂则响亮失败让主代理重试）")
    return filtered


def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   creative: bool = False, prior_assistant: str | None = None,
                   cont_reason: str = "length", return_finish: bool = False) -> tuple:
    """调当前 active profile；失败时按 fallback 链尝试。

    creative=True（写正文）→ 剔除 flash-tier 兜底，pro 全挂响亮失败（不静默降质 · 北极星：质量优先）。
    prior_assistant / cont_reason：透传 _stream_once 的续写机制（保留供长输出续写场景）。
      缺省 = 常规单发。
    return_finish=True → 返回 (full_text, used_profile, finish_reason)；
      缺省 False 返回 (full_text, used_profile)。

    抛 GenModelExhaustedError（active + 整条 fallback 链全失败）。

    2026-05-30 加固：
      · OpenAI client 显式 timeout 防止无限挂起。
      · RateLimitError / APITimeoutError 在**同 profile** 做有限指数退避重试（再降级 fallback），
        避免一次 429/超时就降级到次优模型。
      · HTTP 200 但零 content（内容过滤 / reasoning model 全进 reasoning_content / 空输出）
        视为失败 → 切下一 profile；全链皆空才 raise（杜绝写空草稿报成功）。
    """
    import time

    from openai import OpenAI

    try:
        from openai import APITimeoutError, RateLimitError
    except ImportError:  # 极旧 SDK 兜底（不应发生 · openai>=1.x 均有）
        APITimeoutError = RateLimitError = ()

    candidates = loader.get_callable_profiles()
    if creative:
        candidates = _filter_creative_profiles(candidates)
        if not candidates:
            raise GenModelExhaustedError(
                [("<creative-guard>", "pro-tier 全不可用且 flash 被禁(写正文质量攸关)·"
                  "疑中转站 502/503 故障·稍后重试")])
    failures: list[tuple[str, str]] = []

    for i, profile in enumerate(candidates):
        max_tokens, mt_source = resolve_max_tokens(profile)
        if i == 0:
            logger.info(f" 调用 active profile: {profile.name} "
                  f"({profile.model} @ {profile.base_url})")
            logger.info(f" max_tokens={max_tokens} (source: {mt_source})")
            logger.info(f" temperature={profile.temperature}")
        else:
            logger.info(f"\n[FALLBACK] -> {profile.name} ({profile.model})")

        logger.info(f" prompt size: system={len(system)} chars, user={len(user)} chars")

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url,
                        timeout=GEN_MODEL_TIMEOUT)
        full_text = ""
        try:
            # 同 profile 内：限流/超时做有限指数退避重试，其余异常立即降级 fallback
            attempt = 0
            while True:
                try:
                    full_text, finish_reason = _stream_once(
                        client, profile, system, user, max_tokens,
                        prior_assistant=prior_assistant, cont_reason=cont_reason)
                    break
                except (RateLimitError, APITimeoutError) as re_err:
                    attempt += 1
                    if attempt > GEN_MODEL_MAX_RETRIES:
                        raise  # 重试耗尽 → 落到外层 except → 降级 fallback
                    delay = GEN_MODEL_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.info(f"\n[gen_writer] ⚠️ {profile.name} 限流/超时 "
                          f"({type(re_err).__name__})，{delay:.0f}s 后同 profile 重试 "
                          f"{attempt}/{GEN_MODEL_MAX_RETRIES}…")
                    time.sleep(delay)
            # 截断检测 + 自动续写（finish_reason == "length" = 命中 max_tokens 被截断）
            cont_rounds = 0
            while finish_reason == "length" and cont_rounds < 3:
                cont_rounds += 1
                logger.info(f"\n[gen_writer] ⚠️ 输出截断(finish_reason=length)，自动续写第 {cont_rounds}/3 轮…")
                cont_text, finish_reason = _stream_once(
                    client, profile, system, user, max_tokens,
                    prior_assistant=(prior_assistant or "") + full_text)
                full_text += cont_text
            if finish_reason == "length":
                logger.info(f"\n[gen_writer] ⚠️ WARN 续写 {cont_rounds} 轮后仍可能未写完"
                      f"（草稿尾部/CHANGES 块可能不完整 · 下游 cjk 偏短检查兜底）")
        except Exception as e:
            reason = str(e)[:200]
            logger.info(f"\n[FALLBACK] {profile.name} 调用失败: {reason}")
            failures.append((profile.name, reason))
            continue  # 切下一个 profile

        # 空响应守卫：HTTP 200 但零 content（内容过滤 / reasoning model 全进 reasoning_content）
        # 视为失败，切下个 profile（与 except 路径对齐），杜绝写空草稿报成功。
        if not full_text.strip():
            reason = "返回空内容（HTTP 200 但零 content · 可能内容过滤/reasoning model 全进 reasoning_content）"
            logger.info(f"\n[FALLBACK] {profile.name} {reason}")
            failures.append((profile.name, reason))
            continue  # 切下一个 profile

        # 成功
        logger.info(f"\n[gen_writer] 接收完毕 ({len(full_text)} chars) via {profile.name}")
        if return_finish:
            return full_text, profile, finish_reason
        return full_text, profile

    # 全链失败
    raise GenModelExhaustedError(failures)


# ============ best-of-N：生成 N 稿 + 配对重排 + 综合择优 ============

# ── S9 非对称长度遥测分（LongWriter evaluation/eval_length.py 公式 · research round2 S9 · 2026-07-07）──
# 🔴 落点纪律：本分**只做遥测**——写进 best-of-N selection_trace 与 changes.json 遥测字段
# （供 learning_loop / BPR 训练当 reward 特征），绝不参与 select_best_draft 的择稿逻辑
# （纯 freestyle 契约 · 北极星⑤ · 回归锁 tests/test_best_of_n.py::test_E_no_signal_never_selects_by_cjk）。


def length_telemetry_band() -> tuple:
    """遥测带宽：与 cluster_length_band_scanner._band() 同源同口径（默认 [12000, 25000] ·
    env CLUSTER_LENGTH_BAND_OVERRIDE="min,max" 覆盖 · 单一真理源不各算各的）。"""
    import cluster_length_band_scanner as clbs
    lo, hi, _note = clbs._band()
    return lo, hi


def length_telemetry_score(cjk: int, band: tuple = None) -> float:
    """cluster 长度连续遥测分 0-100（LongWriter 非对称公式：偏短罚陡 /2 · 超长罚缓 /3）。

    带内 = 100；y < min → 100 * max(0, 1 - (min/y - 1)/2)；
    y > max → 100 * max(0, 1 - (y/max - 1)/3)；y <= 0 → 0。
    长度带 scanner 只能二值拒绝，本分把带外偏差量化成连续 reward 特征（gemini 偏短顽疾
    的训练信号）。仅遥测——不进择稿、不 hard_gate、不回流 writer prompt。
    """
    lo, hi = band if band else length_telemetry_band()
    y = float(cjk)
    if y <= 0:
        return 0.0
    if y < lo:
        return round(100.0 * max(0.0, 1.0 - (lo / y - 1.0) / 2.0), 2)
    if y > hi:
        return round(100.0 * max(0.0, 1.0 - (y / hi - 1.0) / 3.0), 2)
    return 100.0


# ============ v29 Claude 草稿发现与分段润色 ============
# （S8 deviation 多样性遥测随 best-of-N 家族一并清除 · v29 润色为确定性单发无 N 候选；
#   throughline_progress 自 2026-06-28 起由 novel-archivist 抽取回库 · writer 链不自报 ·
#   v29 创作自评 ending_type/ending_line 由 novel-writer agent 在 changes_claude.json 承载。）
#
# 架构（2026-07-11 用户定调「所有创作路线转向 Claude 自身创作内容 + gemini 润色」）：
#   step 2a  novel-writer agent（Claude 亲笔）逐场景写作 → claude_scenes/scene_*.txt
#            + cluster_<key>_draft_claude.txt（拼接审计基线）+ changes_claude.json（self_eval 草稿）
#   step 2b  本脚本：逐场景段调 gemini 按风格档等体量重写润色 → 拼接出终稿 cluster_<key>_draft.txt
# 实验依据（workspace/_temp_research/四组生成对比_20260711）：cluster 级 Claude 草稿+gemini 润色
# 双通道最优（嵌入 SFS 0.5625 第一/零禁用词/事实链零漂移）；万字整体润色三连败、分段 ±3% 守恒
# 一次成功 → 分段是万字润色唯一可行形态；多轮自我扩写=套话×10+设定漂移 → 从零生成路径已清除。

POLISH_CJK_LOW = 0.85   # 段级字数守恒带下限（压缩省略红线）
POLISH_CJK_HIGH = 1.30  # 上限（注水扩写红线·实验 gemini scene 级曾 +72% 超标）


def discover_claude_scenes(project_root: Path, cluster_id: int):
    """发现 novel-writer（step 2a）落盘的 Claude 亲笔场景稿 + self_eval 草稿。

    返回 ([(scene_filename, text), ...] 按文件名序, claude_changes_dict)。
    缺目录 / 无场景稿 / 场景稿过短 → FileNotFoundError（v29 required 前置 ·
    绝不回退 gen-model 从零生成 · 不兼容不降级）。
    """
    draft_dir = project_root / '章节' / f'cluster_{cluster_id:03d}_draft'
    scenes_dir = draft_dir / 'claude_scenes'
    if not scenes_dir.is_dir():
        raise FileNotFoundError(
            f"Claude 亲笔场景稿目录不存在: {scenes_dir} — v29 流程要求 novel-writer agent"
            f"（step 2a）先亲笔逐场景写作落盘 claude_scenes/scene_*.txt，再由本脚本分段润色。")
    files = sorted(scenes_dir.glob('scene_*.txt'))
    if not files:
        raise FileNotFoundError(f"{scenes_dir} 下无 scene_*.txt 场景稿")
    scene_files = []
    for f in files:
        t = f.read_text(encoding='utf-8').strip()
        if cio.count_cjk(t) < 200:
            raise FileNotFoundError(
                f"场景稿过短(<200 CJK): {f.name} — Claude 草稿必须每场景写透，禁止梗概占位")
        scene_files.append((f.name, t))
    changes_path = draft_dir / f'cluster_{cluster_id:03d}_changes_claude.json'
    claude_changes = {}
    if changes_path.exists():
        try:
            claude_changes = json.loads(changes_path.read_text(encoding='utf-8'))
        except Exception:
            logger.warning(f"[WARN] changes_claude.json 解析失败 — self_eval 以空对象继续")
            claude_changes = {}
    return scene_files, claude_changes


def polish_pipeline(loader: GenModelLoader, project_root: Path, cluster_id: int,
                    ch_start: int, scene_files: list):
    """逐场景段润色主流程。

    每段独立调 gemini（creative=True · 写正文禁 flash 兜底红线沿用）；
    段级字数守恒校验 [POLISH_CJK_LOW, POLISH_CJK_HIGH]，超界带明确字数指令重试 1 次，
    再超界取离守恒中心更近者并留痕（北极星⑤透明可审）。
    返回 (拼接正文, used_profile, polish_trace)。
    """
    polished, trace = [], []
    used_profile = None
    seed_trace = None
    total = len(scene_files)
    for i, (name, src) in enumerate(scene_files):
        src_cjk = cio.count_cjk(src)
        system, user, _seed = build_prompt(
            project_root, cluster_id, ch_start,
            polish_view={'idx': i, 'total': total, 'scene_text': src})
        if i == 0:
            seed_trace = _seed  # 语感种子留痕（北极星⑤透明可审 · 各段同源只记首段）
        logger.info(f"\n[polish] 场景 {i + 1}/{total} ({name}) src={src_cjk} CJK ...")
        reply, used_profile = call_gen_model(loader, system, user, creative=True)
        body = clean_polished_body(reply)
        out_cjk = cio.count_cjk(body)
        ratio = out_cjk / max(src_cjk, 1)
        retried = False
        if not (POLISH_CJK_LOW <= ratio <= POLISH_CJK_HIGH):
            retried = True
            logger.warning(f"[polish] {name} 守恒超界 ratio={ratio:.2f}"
                           f"（{src_cjk}→{out_cjk}）· 带字数指令重试 1 次")
            user2 = user + (
                f"\n\n【字数守恒警告】你上一版输出约 {out_cjk} 个汉字，超出守恒带。"
                f"润色是等体量重写：这一段的输出字数必须落在 "
                f"{int(src_cjk * POLISH_CJK_LOW)}-{int(src_cjk * POLISH_CJK_HIGH)} 个汉字之间——"
                f"不许压缩省略情节，也不许注水扩写。重新输出这一段的润色全文。")
            reply2, used_profile = call_gen_model(loader, system, user2, creative=True)
            body2 = clean_polished_body(reply2)
            out2 = cio.count_cjk(body2)
            if abs(out2 / max(src_cjk, 1) - 1.0) < abs(ratio - 1.0):
                body, out_cjk = body2, out2
                ratio = out_cjk / max(src_cjk, 1)
        polished.append(body)
        trace.append({'scene': name, 'src_cjk': src_cjk, 'out_cjk': out_cjk,
                      'ratio': round(ratio, 3), 'retried': retried})
        logger.info(f"[polish] {name}: {src_cjk}→{out_cjk} CJK (ratio={ratio:.2f})")
    return "\n\n".join(polished), used_profile, {
        'mode': 'per_scene_polish_v29', 'scenes': trace,
        'conservation_band': [POLISH_CJK_LOW, POLISH_CJK_HIGH],
        'snippet_seed': seed_trace or {'snippet_seed_mode': 'on', 'injected': False}}


# ============ 输出解析与保存 ============
def clean_polished_body(reply: str) -> str:
    """清洗 gemini 润色回复为纯正文。

    v29：润色回复不再携带 CHANGES JSON（self_eval 由 Claude step 2a 产、遥测由
    save_output 确定性补），但 reasoning 模型的元前言/尾注/英文自评漏出问题不变，
    原 split_text_and_changes 的全部剥离逻辑原样保留。误带的 ```json``` 块整块剥除。
    """
    # 误带 json 块防御：润色任务不要求 CHANGES，模型惯性输出的 json 块按元产物剥掉。
    json_matches = list(re.finditer(r'```json\s*\n(.*?)\n```', reply, re.DOTALL))
    if json_matches:
        last = json_matches[-1]
        body = reply[:last.start()].rstrip()
        logger.info(" [strip] 剥离润色回复误带的 ```json``` 块（v29 润色不产 CHANGES）")
    else:
        body = reply.strip()

    # 去掉可能的 "# 正文" 这类元标题
    body = re.sub(r'^#\s*(正文|cluster.*)\s*\n', '', body, flags=re.MULTILINE)

    # [2026-06-05] 剥离推理模型漏出的「创作说明/推理概要」元前言（正文前 + --- 分隔 · flash 等推理模型常见）：
    # 仅当 body 开头是含『概要/推理/创作说明/创作思路』的 markdown 标题、且后接 --- 分隔时才剥，避免误伤正文。
    if re.match(r'^\s*#{1,4}[^\n]*(概要|推理|创作说明|创作思路)[^\n]*\n', body):
        _sep = re.search(r'\n\s*-{3,}\s*\n', body[:2500])
        if _sep:
            body = body[_sep.end():].lstrip()
            logger.info(" [strip] 剥离模型漏出的『创作说明/推理概要』元前言（正文前 + --- 分隔）")

    # [2026-06-06] 剥离 reasoning/对话型模型（pro-preview 等）漏出的「破壁助手尾注」：
    # 正文末尾蹦出『请审阅。…请告诉我，我将为你输出…CHANGES JSON』类对读者喊话——
    # 当模型没产 ```json``` 块（改成问用户）时，这段元注释会整段漏进正文 body。按行从尾部剥。
    _meta_tail_pat = re.compile(
        r'请审阅|请告诉我|当你认为正文|我将为你输出|我会为你输出|等你确认|'
        r'准备好.{0,8}CHANGES|CHANGES\s*JSON|带有所有统计数据|伏笔回收状态')
    _lines = body.split('\n')
    _stripped_tail = False
    while _lines and (not _lines[-1].strip() or _meta_tail_pat.search(_lines[-1])):
        if _meta_tail_pat.search(_lines[-1]):
            _stripped_tail = True
        _lines.pop()
    if _stripped_tail:
        body = '\n'.join(_lines).rstrip()
        logger.info(" [strip] 剥离 reasoning 模型破壁助手尾注（请审阅/请告诉我/CHANGES JSON 类）")

    # 🔴 2026-06-28：剥离尾部英文元评论块（pro-preview 等写完正文后用英文自评『The narrative chunk
    # is written coherently...』漏进 body·实测钟楼弃儿 cluster_001 4707CJK 后接 1303 字符英文解说）。
    # 中文小说正文绝不以整段英文结尾 → 从尾部剥『ASCII 占比 >0.7 且 ≥20 字符』的元评论行（含其间空行）。
    # 安全边界：只剥连续尾部英文段·遇到首个中文主导行立停（不误伤正文中的英文引用/人名短串）。
    def _is_english_meta_line(s):
        s = s.strip()
        if len(s) < 20:
            return False
        ascii_ct = sum(1 for c in s if ord(c) < 128)
        return ascii_ct / max(len(s), 1) > 0.7
    _lines2 = body.split('\n')
    _stripped_en = False
    while _lines2 and (not _lines2[-1].strip() or _is_english_meta_line(_lines2[-1])):
        if _is_english_meta_line(_lines2[-1]):
            _stripped_en = True
        _lines2.pop()
    if _stripped_en:
        body = '\n'.join(_lines2).rstrip()
        logger.info(" [strip] 剥离模型尾部英文元评论块（The narrative.../All quantitative... 类自评·非正文）")

    # cluster 连续叙事模式：如果 gen-model 仍误带「第 N 章 标题」分章标记，stderr 警告
    # 不主动删除（让 splitter 决定怎么处理），只提示 prompt 没生效
    if re.search(r'^第\s*[一二三四五六七八九十百千\d]+\s*章\s', body, re.MULTILINE):
        logger.warning("[WARN] gen-model 输出含「第 N 章 标题」分章标记 — "
              "splitter 应忽略这些标记重新决定截断点。"
              "若反复出现，调高 prompt 强度或换 profile。")

    return body


def _read_author_rhythm(project_root: Path):
    """读作者风格档的句长/段长/单句独行基线（缺失/出错返回 (None,None,None)·纯防御不抛）。

    [2026-06-04 治本] 句法熔合/短段约束必须用**本项目作者**的真实基线，不能硬编码
    （旧 FUSE_MIN_MEAN_CJK=24 写死惊悚乐园的 31×0.77，对小世界 32.2 凑巧接近但原则错；
    且熔合无上限→越改越长过冲。长句≠长段：小世界=长句裹短段，句长32但段长35短段·单句独行0.79）。
    """
    p = project_root / "_数据库" / "作者风格.json"
    if not p.exists():
        return None, None, None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None, None
    q = d.get("quantitative", {})
    if not isinstance(q, dict):
        return None, None, None

    def _num(*paths):
        for path in paths:
            cur = q
            ok = True
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, (int, float)) and cur > 0:
                return float(cur)
        return None

    sent = _num(("sentence_length", "mean"))
    para = _num(("paragraph_length_chars", "mean"), ("paragraph_length", "mean_chars"))
    single = _num(("single_sentence_para_ratio",),
                  ("paragraph_length", "single_sentence_para_ratio_mean"))
    return sent, para, single


def enforce_short_paragraphs(body: str, author_para_mean: float = None, author_single: float = None) -> str:
    """[2026-06-04 治本] 长句裹短段：把过长的非对话段按句末切成短段，贴作者段长基线。

    根因：句法熔合只拉句长不管段长，小世界=长句裹短段（句长32/段长35短段/单句独行0.79），
    熔合后长句若多句挤一段→段长 56.5 远超作者 35.6（用户 2026-06-04 抓到）。本步按作者段长
    自适应切段：阈值 = max(作者段长×1.3, 45)；超阈值的非对话段按**句末**切成单句段
    （单长句保持完整·**绝不碰逗号**防切坏「非但…反而」/列举等关联结构）。
    北极星④：段落是格式层·只切段不改一字。对话/系统面板【】保护不切。作者基线缺失→阈值80（仅切egregious）。
    """
    import re as _re
    # [北极星⑤·对齐 _para_contract_line L546] 作者写密实多句长段(single<0.5)→通用一段一句让位·
    # 不在 post-processing 反向 tighten 打碎其签名复合段·relax-only(只放宽不收紧)。
    if author_single is not None and author_single < 0.5:
        logger.info(f" 短段约束跳过：作者密实多句长段(单句独行 {author_single:.0%}<0.5)→保留复合长段·不拆碎句(对齐段长契约)")
        return body
    base = author_para_mean if (author_para_mean and author_para_mean > 0) else 0
    threshold = max(base * 1.3, 45.0) if base else 80.0
    LQ = "“"

    def protected(s: str) -> bool:
        s = s.lstrip()
        return s.startswith(LQ) or s.startswith("”") or s.startswith("【") \
            or s.startswith("「")  # “ ” 【 「

    out, changed = [], 0
    for blk in body.split("\n\n"):
        para = blk.replace("\n", "")  # 合软换行回整段
        if not para.strip():
            continue
        if len(para) <= threshold or protected(para):
            out.append(para)
            continue
        sents = _re.findall(r"[^。！？…]*[。！？…]+", para)
        tail = para[sum(len(x) for x in sents):]
        if tail.strip():
            sents.append(tail)
        if len(sents) > 1:
            out.extend(s for s in sents if s.strip())
            changed += 1
        else:
            out.append(para)  # 单句长段·不切（防切坏语法·小世界长句允许）
    new_body = "\n\n".join(out)
    if changed:
        logger.info(f" 短段约束：切分 {changed} 个过长非对话段（阈值 {threshold:.0f} 字·作者段长基线 {base:.1f}）")
    return new_body


def save_output(project_root: Path, cluster_id: int, body: str, changes: dict,
                ch_start: int, used_profile: Profile,
                polish_trace: dict = None):
    """写 draft + changes.json

    cluster-first：ch_range 写 'TBD_by_splitter'（splitter 后期填）。
    changes 入参 = Claude step 2a 的 self_eval/waivers 草稿（changes_claude.json），
    本函数在其上合并确定性遥测（cjk / length_telemetry / polish 留痕）。
    polish_trace（v29）：per-scene 润色遥测（src/out cjk · 守恒 ratio · retried）·
      留 changes 透明可审（北极星⑤）。
    """
    # 空 body 守卫（2026-05-30 加固）：拒写空草稿并报错，避免 cjk=0 草稿入库还报成功。
    # 上游 call_gen_model 已对空响应切 fallback，此处是最后一道防线（含解析后正文为空的情况）。
    if not body.strip():
        raise ValueError(
            f"[gen_writer] 拒绝写入空草稿（cluster_{cluster_id:03d}）：解析后正文为空。"
            f"可能是 gen-model 返回空内容或全为 CHANGES JSON 无正文 — 请检查 profile 输出。"
        )

    # 2026-06-07 根治：草稿落地前确定性清洗 gen-model 原始输出的机械格式病
    # ① 整块逐字复制 ② 成对符号腰斩
    # （引号“”/【】/《》/（）被句末标点+换行劈开，质检长期只查引号没查【】，靠人读逐个逮）。
    # 纯文本、幂等、不调模型（draft_sanitizer.py）；失败仅告警不阻断落地。
    try:
        from draft_sanitizer import sanitize as _sanitize_draft
        body, _san_rep = _sanitize_draft(body)
        if _san_rep.get('dedup_segments_removed') or _san_rep.get('pair_merges'):
            logger.info(f" draft_sanitizer 清洗：整块去重 {_san_rep['dedup_segments_removed']} 段 · "
                  f"成对符号腰斩合并 {_san_rep['pair_merges']} 处")
    except Exception as _e:
        logger.warning(f"[WARN] draft_sanitizer 清洗跳过（{_e}）— 草稿原样落地")

    draft_dir = project_root / '章节' / f'cluster_{cluster_id:03d}_draft'
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f'cluster_{cluster_id:03d}_draft.txt'
    changes_path = draft_dir / f'cluster_{cluster_id:03d}_changes.json'

    # 2026-06-13 残余非原子写收编：草稿是 cluster 主轨核心产物，原子落盘（tmp+fsync+replace）。
    atomic_write_text(draft_path, body)

    # 补全 changes 元数据
    cjk = cio.count_cjk(body)  # v27 修复：统一 CJK 口径走 chapter_io（覆盖扩展 CJK）
    _lt_band = length_telemetry_band()  # S9 遥测带（与 cluster_length_band_scanner 同口径）
    ch_range_str = f'{ch_start}-TBD_by_splitter'

    # v27 P0 修复（schema 统一）：先把 LLM 输出的 changes 经 normalize_changes 归一·
    # 兼容三种布局（顶层 factual / 顶层 CHANGES / 顶层裸字段）·全部转 {factual, self_eval}·
    # 杜绝下游 audit_hub CHANGES_MISSING 误报 + waivers 读不到。
    changes = cio.normalize_changes(changes)
    se = changes['self_eval']
    se.setdefault('ecas_metadata', {})
    se['ecas_metadata'].update({
        'cluster_id': f'cluster_{cluster_id:03d}',
        'ch_start': ch_start,
        'ch_range': ch_range_str,
        'chapter_count_decided_by_splitter': True,
        'generated_by': 'novel-writer(claude 亲笔草稿) + gen_writer.py(gemini 分段润色)',
        'generated_by_profile': used_profile.name,
        'generated_by_model': used_profile.model,
        'generated_at': datetime.now().isoformat(),
        'cjk_actual': cjk,
        'writer_mode': 'claude_draft_gemini_polish_v29',
        # S9 非对称长度遥测分（LongWriter · 偏短/2 超长/3 · research round2 S9）：
        # 仅遥测字段供 learning_loop/BPR 当 reward 特征——不参与择稿/重写决策、不回流 writer prompt。
        'length_telemetry': {
            'score': length_telemetry_score(cjk, _lt_band),
            'band': list(_lt_band),
            'formula': 'longwriter_asymmetric(under/2, over/3)',
        },
    })
    # v29 润色留痕（per-scene 守恒遥测 · 北极星⑤透明可审）
    if polish_trace:
        se['ecas_metadata']['polish'] = polish_trace
    se.setdefault('waivers', [])
    se.setdefault('uncertainty_flags', [])
    changes.setdefault('schema_version', 'v2.cluster')
    # 2026-06-13 同批收编：changes.json 半截损坏 = 下游 audit_hub/split_cluster_changes 解析崩。
    atomic_write_text(changes_path,
                      json.dumps(changes, ensure_ascii=False, indent=2))

    logger.info(f"\n[gen_writer] 写出:")
    logger.info(f"  正文: {draft_path} ({cjk} CJK)")
    logger.info(f"  CHANGES: {changes_path}")
    logger.info(f"  profile: {used_profile.name} ({used_profile.model})")
    return draft_path, cjk


# ============ Scanner 自校验 ============
def run_scanners(draft_path: Path) -> dict:
    """跑两个 scanner 校验"""
    scanners = ['narrative_short_sentence_scanner.py', 'repeat_noun_density_scanner.py']
    results = {}
    scripts_dir = Path(__file__).parent
    for sc in scanners:
        sc_path = scripts_dir / sc
        if not sc_path.exists():
            results[sc] = {'verdict': 'SKIP', 'reason': 'scanner not found'}
            continue
        r = subprocess.run(
            [child_python(), str(sc_path), str(draft_path)],
            capture_output=True, timeout=120
        )
        stdout_text = (r.stdout or b"").decode("utf-8", errors="replace")
        stderr_text = (r.stderr or b"").decode("utf-8", errors="replace")
        try:
            d = json.loads(stdout_text)
            results[sc] = {'verdict': d.get('verdict'), 'violations_count': d.get('violations_count')}
        except Exception as e:
            logger.info(f"  [run_scanners] {sc} 输出 JSON 解析失败 ({e})·exit={r.returncode}·stderr_preview={stderr_text[:150]}")
            results[sc] = {'verdict': 'ERROR', 'stdout_preview': stdout_text[:300]}
    return results


# ============ 主入口 ============
def main():
    check_deps()
    parser = argparse.ArgumentParser(
        description='Gen-Model 润色引擎（v29：Claude 亲笔草稿 → gemini 分段润色）')
    parser.add_argument('--project', required=True, help='项目根路径')
    parser.add_argument('--cluster', type=int, required=True, help='cluster id（整数）')
    parser.add_argument('--dry-run', action='store_true', help='只输出首段润色 prompt，不调 API')
    parser.add_argument('--dialogue-orchestrator-mode', default='off',
                        choices=['off', 'shadow', 'active'],
                        help='[R19 W8 Batch-X·AdaMARP 多人对话编排] 默认 off·env 透传给 '
                             'dialogue_scene_manager（manifest 注入层·润色 prompt 同样消费 manifest）')
    args = parser.parse_args()
    if args.dialogue_orchestrator_mode:
        os.environ["DIALOGUE_ORCHESTRATOR_MODE"] = args.dialogue_orchestrator_mode

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        logger.error(f" 项目路径不存在: {project_root}")
        sys.exit(2)

    ch_start = _infer_cluster_start_ch(project_root, args.cluster)
    logger.info(f" [v29 claude_draft_gemini_polish] 推导 ch_start={ch_start} (cluster_{args.cluster:03d})")

    # 🔴 v29 required 前置：Claude 亲笔场景稿必须已就位（novel-writer agent step 2a 产出）。
    # 缺失 = 流程违规，[FATAL] 响亮失败，绝不回退到 gen-model 从零生成（不兼容不降级）。
    try:
        scene_files, claude_changes = discover_claude_scenes(project_root, args.cluster)
    except FileNotFoundError as e:
        sys.stderr.write(f"\n[FATAL gen_writer] {e}\n")
        sys.stderr.flush()
        sys.exit(2)
    logger.info(f" Claude 草稿：{len(scene_files)} 个场景稿 · "
                f"合计 {sum(cio.count_cjk(t) for _, t in scene_files)} CJK")

    # dry-run 模式不需要 active profile
    if args.dry_run:
        system, user, seed_trace = build_prompt(
            project_root, args.cluster, ch_start,
            polish_view={'idx': 0, 'total': len(scene_files),
                         'scene_text': scene_files[0][1]})
        logger.info("=== SYSTEM PROMPT ===")
        logger.debug(system)  # OK: print - dry-run 模式调试输出
        logger.info("\n=== USER PROMPT ===")
        logger.debug(user)  # OK: print - dry-run 模式调试输出
        logger.info(f"\n[dry-run] system={len(system)} chars / user={len(user)} chars")
        try:
            loader = GenModelLoader()
            p = loader.get_active_profile()
            logger.info(f"[dry-run] active profile: {p.name} ({p.model} @ {p.base_url})")
        except GenModelConfigError as e:
            logger.info(f"[dry-run] [WARN] active profile 未就绪: {e}")
        return

    # 加载 gen-model 配置
    try:
        loader = GenModelLoader()
        active = loader.get_active_profile()  # 校验 active 就绪
    except GenModelConfigError as e:
        logger.error(f" {e}")
        logger.info("  跑 python core/scripts/gen_model.py list / switch 修复")
        sys.exit(2)

    logger.info(f" 加载配置: {loader.env_path}")
    logger.info(f" active = {active.name}")
    # token ledger（一人公司·BYOK 用户看烧多少钱）：设账本路径·llm_transport 自动 append（已设则尊重）
    os.environ.setdefault("RUOYU_TOKEN_LEDGER",
                          str(project_root / "_数据库" / ".token_ledger.jsonl"))
    chain = loader.get_fallback_chain()
    if chain:
        logger.info(f" fallback chain = {','.join(chain)}")

    # v29 分段润色主流程：逐场景段调 gemini 按风格档重写（字数守恒校验+重试）→ 拼接。
    # 实验依据（2026-07-11 四组对比）：万字整体润色三连败（TransportEmpty×2+压缩），
    # 分段（≤6k CJK）±3% 守恒一次成功——分段是万字润色的唯一可行形态。
    try:
        body, used_profile, polish_trace = polish_pipeline(
            loader, project_root, args.cluster, ch_start, scene_files)
    except GenModelExhaustedError as e:
        # 🔴 fail-fast 走 stderr + flush（feedback_verify_stderr_not_exitcode）
        msg = f"\n[FATAL gen_writer] GenModelExhausted: {e}\n"
        sys.stderr.write(msg)
        sys.stderr.flush()
        sys.exit(3)

    # 短段约束（格式层·按作者段长切过长非对话段·不动 ！？）
    _auth_sent, _auth_para, _auth_single = _read_author_rhythm(project_root)
    logger.info(f" 作者节奏基线：句长={_auth_sent} 段长={_auth_para} 单句独行={_auth_single}")
    body = enforce_short_paragraphs(body, author_para_mean=_auth_para, author_single=_auth_single)

    # changes = Claude self_eval/waivers（step 2a 产）+ 本脚本确定性遥测（save_output 内合并）
    draft_path, cjk = save_output(project_root, args.cluster, body, claude_changes,
                                  ch_start, used_profile, polish_trace=polish_trace)

    logger.info(f"\n[gen_writer] 跑 scanner...")
    scan_results = run_scanners(draft_path)
    logger.info(f"\n=== Scanner Results ===")
    for sc, res in scan_results.items():
        logger.info(f"  {sc}: {res}")

    # 2026-06-19：scanner 结果自动反馈到本地知识库（MAPLE 闭环·experience→insight）
    try:
        import knowledge_collector as _kc
        _kc.collect_from_writing(project_root, f"cluster_{args.cluster:03d}", scan_results)
    except Exception:
        pass  # 知识库异常不影响主流程

    logger.info(f"\n[v29 claude_draft_gemini_polish] draft CJK={cjk} · splitter 后续决定章数与每章篇幅")


if __name__ == '__main__':
    main()
