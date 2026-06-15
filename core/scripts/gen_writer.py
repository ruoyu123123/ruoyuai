#!/usr/bin/env python3
"""
gen_writer.py — Gen-Model 正文生成器（OpenAI 兼容协议 /v1/chat/completions）

v27 freestyle 模式（默认）：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 6
  → writer 不知道目标章数 · 按 cluster.scope_summary + scene_storyboard 自由发挥
  → 字数自然涌现（splitter 后期按 3000-4500/章 切，章数由内容决定）

v26 兼容模式（显式锁字数 · 仅用于回归测试）：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 3 \
    --chapter-start 11 --chapter-end 15 \
    --target-cjk 13000-22000

读取 manifest + 风格 skill + 调研 cache + cluster_brief + 7 项硬约束，
组装 prompt，调当前 active 的 gen-model profile（OpenAI 兼容），
失败时按 fallback 链尝试下一个 profile，
写出 cluster draft + changes.json + 自动跑 scanner 校验。

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
    Profile,
)
import chapter_io as cio  # noqa: E402 · CJK 计数 + changes schema 规范化权威口径
import cluster_lookup  # noqa: E402 · cluster_id 归一化（int 6 ↔ "cluster_006" ↔ "6"）
from atomic_json import atomic_write_text  # noqa: E402 · 2026-06-13 草稿/CHANGES 产物原子落盘（崩溃不留半截）
import snippet_seed  # noqa: E402 · 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量）


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
        print(f"[ERROR] 缺少依赖: {missing}", file=sys.stderr)
        print(f"  请运行: pip install {' '.join(missing)}", file=sys.stderr)
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


# ============ best-of-N 择优（写作端 · 非迭代规避同质化 · 2026-05-31）============
# 根因（本批任务说明 · arxiv 实证）：单稿直生 + av_judge 只事后单次诊断不回灌；self-refine
#   反复迭代会同质化（模型把自己的输出当锚反复收敛到同一坨）。best-of-N 走「N 稿并行生成
#   + 配对判别 + 综合择优」——selection（择优）≠ refine（迭代改），天然规避同质化。
# 怎么择优（advisory · 不黑箱 · 北极星⑤）：
#   ① SFS（style_evaluator.compute_style_only_sfs）= 统计指纹相似度（越高越像作者 · 透明可读）。
#   ② AV-judge 配对判别（av_judge.pairwise_drift_count）= 读者视角 4 维走味计数（越少越像）。
#   综合分 = SFS 归一 - 走味维度惩罚 → 排序取最高（SFS 当裁判透明 · AV-judge 只 select 不强判）。
# env BEST_OF_N：默认 2（active · N≥2 真生效）· 设 1 = 关（退回单稿直生 · 零回归逃生口）。
BEST_OF_N_DEFAULT = 2
BEST_OF_N_MAX = 5  # 上限防 token 失控（用户质量优先但不无限）

# [2026-06-04] freestyle 正文长度软下限（CJK）：低于此值且模型 finish=stop（写完但偏短）→ call_gen_model
# 触发 expand 续写兜底（展开剩余场景）。治 pro 等简洁倾向 reasoning 模型单 cluster 仅 ~3650 CJK 偏短问题。
# 软托底非硬锁（北极星⑤）：防注水——单轮续写增量 < FREESTYLE_EXPAND_MIN_GAIN 即停（模型没料别硬凑）。
FREESTYLE_MIN_CJK = 12000          # 健康区间 12000-25000 的下限
FREESTYLE_EXPAND_MAX_ROUNDS = 6    # expand 续写最多轮数（2026-06-06 4→6·治 pro 等简洁 reasoning 模型偏短·多续几轮逐场景写透）
FREESTYLE_EXPAND_MIN_GAIN = 400    # 单轮增量低于此 CJK → 停止兜底（2026-06-06 800→400·pro 单轮加得少但累积有效·别过早停）
# 综合分：每个走味维度的惩罚（满分 100 的 SFS 尺度上扣多少 · 4 维全走味最多扣 40）。
AV_DRIFT_PENALTY_PER_DIM = 10.0


def _best_of_n() -> int:
    """读 env BEST_OF_N：默认 2（active 放量 · N≥2 真生效）· 1=关 · 钳到 [1, BEST_OF_N_MAX]。

    空 / 非法值 → 默认 2（与「默认全开 active」纪律一致：用户说默认关掉写它干什么）。
    设 BEST_OF_N=1 是唯一的关闭口（退回单稿直生 · 零回归）。
    """
    raw = (os.environ.get("BEST_OF_N") or "").strip()
    if not raw:
        return BEST_OF_N_DEFAULT
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return BEST_OF_N_DEFAULT
    if n < 1:
        return 1
    return min(n, BEST_OF_N_MAX)


def _candidate_temperatures(base_temp: float, n: int) -> list[float]:
    """为 N 个候选生成**有差异**的 temperature（多样性 = best-of-N 价值来源 · 非迭代）。

    第一稿用 profile 原始 temperature（保持基线行为不变 · 单稿等价）；后续候选在其上加阶梯
    抖动（+0.1, +0.2, …），钳到 [0.2, 1.2] 合理区间。温度差异让 N 稿真的不同（避免 N 个一样的稿
    白烧 token），同时不偏离作者风格太远（小步抖动 · 非大跨度）。
    """
    temps = [base_temp]
    step = 0.1
    for i in range(1, n):
        t = base_temp + step * i
        # 钳到合理写作温度区间（过低=刻板 / 过高=发散跑偏）
        t = max(0.2, min(1.2, round(t, 2)))
        temps.append(t)
    return temps


# ============ v27 freestyle helpers ============
def _infer_cluster_start_ch(project_root: Path, cluster_id: int) -> int:
    """v27 freestyle：从事件簇.json + 已写章节推导 cluster 起始章号

    优先级：
    1. 事件簇.json.clusters[N].chapter_range[0]（v26 schema · 向后兼容）
    2. 事件簇.json.clusters[N].ch_start（v27 新 schema）
    3. 上一 cluster 末章 + 1（从 章节/ 目录扫）
    4. cluster_001 = 1 兜底
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
                    cr = c.get('chapter_range') or []
                    if cr and len(cr) >= 1:
                        return int(cr[0])
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


# ============ Prompt 组装 ============
def _collect_feedback_rules() -> str:
    """L3 防御：自动扫 ~/.claude/projects/.../memory/feedback_*.md，
    抽取 type=feedback 的全局规则段，注入 writer system prompt。

    设计目标：让 writer 在每段生成时都看到全局禁令（不依赖主代理记得）。
    抽取策略：取 lesson 文件的「## 规则」段（如有），否则取文件头部 1500 字。

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
                # 抽 "## 规则" 段或文件正文头部
                m = re.search(r"##\s*规则[\s\S]*?(?=\n##\s|\Z)", text)
                chunk = m.group(0) if m else text[text.find("---\n", 5) + 4:]
                chunk = chunk.strip()[:2500]
                if not chunk:
                    continue
                rules_chunks.append(f"### 来自 {f.stem}\n\n{chunk}")
        if not rules_chunks:
            return _collect_feedback_rules_bundle_fallback()
        header = "# 🔴 全局 feedback 规则（自动注入 · 来自 memory/feedback_*.md）\n\n"
        header += "以下是历史用户反馈沉淀的全局禁令，写作时**逐条遵守**。违反 = 出货后被打回 + lesson 复发。\n\n"
        return header + "\n\n---\n\n".join(rules_chunks)
    except Exception:
        return ""


def _collect_feedback_rules_bundle_fallback() -> str:
    """home memory miss/为空 → 读随 exe 出货的汇编 lessons/global_feedback_rules.md 全文。

    汇编文件由 assemble_global_feedback_rules.py 机械产出（自带「逐条遵守」header 框架行，
    与 home 路径注入形态等价）；frozen_util.resource_path 定位（frozen=_MEIPASS·dev=仓库根）。
    """
    try:
        from frozen_util import resource_path
        p = resource_path("core", "claude-home", "lessons", "global_feedback_rules.md")
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def read_text(p: Path, limit_chars: int = None) -> str:
    if not p.exists():
        return f"[WARN] 文件不存在: {p}"
    t = p.read_text(encoding='utf-8')
    if limit_chars and len(t) > limit_chars:
        t = t[:limit_chars] + f"\n... [truncated at {limit_chars} chars]"
    return t


def _build_style_fingerprint_section(manifest_path: Path) -> str:
    """从 manifest.author_style_fingerprint 抽显式量化指令拼成 writer prompt 段。

    L1a 升格消费（2026-05-31）：build_manifest 在 PROFILE_INJECT_MODE=active 下注入
    多维量化风格指纹（句长/段长/单句独行/标点/虚词/签名搭配 + 显式 directives 文案）。
    本函数把 directives 升到 prompt 前部醒目位置 —— 实证「显式数值目标」> 让模型看样本自己悟。

    返回值：
      · 指纹缺失 / 为 None（PROFILE_INJECT_MODE=off/shadow）/ 无 directives → ""（不注入·零回归）。
      · 有 directives → 拼成「作者量化风格指纹」段（advisory · 标注可校准偏离·北极星⑤不硬锁）。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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


def _build_rolling_anchor_section(manifest_path: Path) -> str:
    """从 manifest.rolling_style_anchor 抽动态文风锚片段拼 writer prompt 段（dead-zone → 生成点近邻升格）。

    返回值：
      · ROLLING_ANCHOR_INJECT_MODE != active / 字段缺/None / 无 anchors / snippet 全空 → ""（零回归）。
      · 有 anchors → 拼「本书文风动态锚」段（advisory 软牵引 · 北极星⑤不硬锁 · 每锚 snippet）。
    """
    if _rolling_anchor_inject_mode() != "active":
        return ""
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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


def _build_rhythm_signature_section(manifest_path: Path) -> str:
    """阶段1：从 manifest.author_rhythm_signature 抽序列级节奏指令拼 writer prompt 段。

    与量化指纹（句长/段长·节点静态属性）互补——这是「写了这一拍接下一拍」的序列骨
    （节拍转移/翻转率/张力后段保持度/钩子兑现）。RHYTHM_INJECT_MODE=off/shadow 或无
    指纹 → ""（不注入·零回归）。advisory·作者档第一权威·北极星⑤不硬锁。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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


def _build_knowledge_gap_section(manifest_path: Path) -> str:
    """阶段·D3：从 manifest.knowledge_gap_signature 抽读者-角色信息差三态指令拼 writer prompt 段。

    序列骨之一（与 rhythm 并列）——「读者比角色多知道还是少知道」是悬念/虐心/打脸的底层
    引擎（Sternberg 三态：reader_adv 上帝视角虐心 / reader_disadv 角色优势卖关子 /
    double_blind 双盲悬疑）。KNOWLEDGE_GAP_INJECT_MODE=off/shadow 或无指令 → ""（不注入·
    零回归·切 active 前须过 G3 标注一致性闸 + 消融）。advisory·作者档第一权威·北极星⑤不硬锁。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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


def _build_narrative_seq_section(manifest_path: Path) -> str:
    """#4：从 manifest.narrative_function_sequence 抽作者签名因果功能链拼 writer prompt 段（结构骨）。

    signature_bigrams（face_slap→gain_reward 等因果转移）比段长/句长表层指纹更深·让连续故事块功能
    转移贴作者签名节奏而非默认 LLM 高频模板（中文网文同质化结构层根因）。NARR_FUNC_SEQ_INJECT_MODE=
    off/shadow 或无指令（build_manifest 控字段 None）→ ""（不注入·零回归）。advisory·作者档第一权威·北极星⑤不硬锁。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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


def _build_genre_pack_section(manifest_path: Path) -> str:
    """阶段3：从 manifest.genre_pack_directives 拼题材专属工艺段（按 genre·advisory）。

    通用维度池(作者层)always-on；题材层(甜宠糖虐/游戏向面板)按 genre 激活。
    GENRE_INJECT_MODE=off/shadow 或 unknown genre → ""（退化纯通用·零回归）。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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


def _build_decision_principles_section(manifest_path: Path) -> str:
    """阶段2：从 manifest.author_decision_principles 拼作者思维/人物刻画骨段。

    骨③作者思维(道德滤镜/心理距离/留白)+骨②刻画手法——「作者在 X 情境倾向 Y」的决策
    原则·段长抓不到的骨。DECISION_INJECT_MODE=off/shadow 或无 → ""（零回归）。advisory。
    """
    if not manifest_path.exists():
        return ""
    try:
        m = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
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
    落在 U 型最低的中段，而紧贴生成点「现在请写正文」之前的是 manifest 事实索引
    （非风格锚）→ 位置层北极星偏移。

    active（默认）：把风格 skill + 语感种子锚移到 prev_ch 之后、「现在请写正文」之前
                    （生成点近邻 RoPE 高位）；manifest 事实索引留中段。
    off / shadow：保持原版 join 顺序（零回归回退路径）。
    """
    return (os.environ.get("CTX_REORDER_MODE") or "active").strip().lower()


def _skill_primacy_mode() -> str:
    """skill 硬约束 primacy 重排开关（env SKILL_PRIMACY_MODE · 默认 active · 第8轮同族补完）。

    IFScale 实证：长指令文档里的硬约束维（段长契约 / 禁用词 / 对话格式）在 skill **中段衰减**
    （指令越多、文档越长，中段那几条越容易被模型"读过即忘"）。现状第8轮 ctx 重排把整个风格 skill
    下沉到生成点近邻，但 skill 内部仍是一大段连续文本——里面的硬约束维没有被 **primacy 强调**，
    长 skill 中段那几条照样衰减。

    active（默认）：在生成点近邻（风格锚区）补一段**精简硬约束 primacy 重述**——只点名 3 个最易
                    衰减的硬约束维（段长契约 / 禁用词 / 对话格式），轻量重述非整 prompt 复制
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
    段长契约 / 禁用词 / 对话格式 / 段首多样 / 情绪标点（作者基线感知）——贴生成点 RoPE 高位强调，
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
        + "- **禁用词**：结构性 AI 套话（与此同时 / 值得一提的是 / 不仅如此 / 事实上）零容忍；"
        "工艺签名词以作者风格 skill 的 signature 为准（skill 列了就是作者笔法，没列就默认避免）。\n"
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


def build_prompt(project_root: Path, cluster_id: int, ch_start: int,
                 ch_end: int = None, target_cjk: str = None) -> tuple:
    """组装 system + user prompt

    v27 freestyle：ch_end/target_cjk 可缺省。
    - ch_end 缺 → user prompt 不暴露目标章数（writer 不知道目标章数）
    - target_cjk 缺 → 不注入「目标字数」段（让 AI 按 scope_summary 自由产出 · 自然涌现）
    - 每章字数硬范围（3000-4500）由 splitter 按字数切时执行；writer 不预设章数/每章字数（v27 freestyle · 原 system prompt 铁律 #4 已删）
    """
    db = project_root / '_数据库'
    freestyle = (ch_end is None)

    # 读取核心资料
    # 2026-05-29 北极星复审 L2：去 load-time 截断（原 30000/25000 仍砍大文件，与
    # feedback_no_token_saving「全量传 LLM」+ 作者档第一权威冲突；诡异接待处 skill 34575 字被砍 9k+）。
    # 全量读——作者风格 skill 是写作第一权威，不得在 load 时截断。
    manifest_path = db / '.manifest' / f'ch_{ch_start:03d}.json'
    manifest = read_text(manifest_path)

    # L1a 升格消费（2026-05-31）：build_manifest 在 env PROFILE_INJECT_MODE=active 下产出
    # manifest.author_style_fingerprint（多维量化目标硬数字 + 显式指令文案），但 writer 此前
    # 只把整 manifest 当 raw text 塞进 prompt 尾部「数据库索引」段 —— 量化指纹深埋 JSON 里
    # writer 难以识别为「写作目标」。这里**显式解析**该字段，单独拼成醒目的「作者量化风格指纹」
    # 段注在 prompt 前部（advisory · 北极星⑤不硬锁），让 writer 真消费多维数值目标。
    style_fp_section = _build_style_fingerprint_section(manifest_path)
    # 阶段1：作者叙事节奏指纹段（序列级骨·RHYTHM_INJECT_MODE=off/shadow 或无指纹时空 → 零回归）
    rhythm_section = _build_rhythm_signature_section(manifest_path)
    # 阶段2：作者决策原则+人物刻画手法段（思维/刻画骨·DECISION_INJECT_MODE 控制·空则零回归）
    decision_section = _build_decision_principles_section(manifest_path)
    # 阶段3：题材专属工艺段（按 genre·GENRE_INJECT_MODE 控制·unknown/空则零回归）
    genre_section = _build_genre_pack_section(manifest_path)
    # 阶段D3：作者信息差主调段（读者-角色知识差三态·KNOWLEDGE_GAP_INJECT_MODE 控制·默认 shadow 时空 → 零回归）
    knowledge_gap_section = _build_knowledge_gap_section(manifest_path)
    # #4：作者签名因果功能链段（结构骨·NARR_FUNC_SEQ_INJECT_MODE 控制·默认 shadow 时 build_manifest 字段 None → 空段零回归）
    narr_seq_section = _build_narrative_seq_section(manifest_path)
    # #3 升格：本书文风动态锚段（治 D 级长程退化·ROLLING_ANCHOR_INJECT_MODE 默认 shadow 时空 → 零回归）
    rolling_anchor_section = _build_rolling_anchor_section(manifest_path)

    # 风格 skill（全量，不截断）
    style_skill = read_text(db / '作者风格_skill.md')
    if not (style_skill or "").strip():
        print("[gen_writer] ⚠️🔴 作者风格_skill.md 缺失/空——writer 只有量化 JSON、缺作者笔法+golden 范例，"
              "极易跑偏成通用爽文（cluster_001 翻车根因）。请把风格库 skill_FINAL.md 复制为 "
              "<项目>/_数据库/作者风格_skill.md（/outline 漏拷的已知 bug）。", file=sys.stderr)

    # 调研 cache（找最新的）
    cache_dir = db / '.research_cache'
    cache_text = ""
    if cache_dir.exists():
        caches = sorted(cache_dir.glob(f'inspiration_cluster_{cluster_id:03d}_*.md'),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        if not caches:
            caches = sorted(cache_dir.glob('inspiration_*.md'),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if caches:
            cache_text = read_text(caches[0])  # 2026-05-30 北极星：去截断，全量传 LLM（feedback_no_token_saving）

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
    if freestyle:
        relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_start + 30]
    else:
        relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_end]
    plan_text = json.dumps(relevant_plans, ensure_ascii=False, indent=2) if relevant_plans else "[]（v27 freestyle · 完全按 cluster_brief.scene_storyboard 自由发挥）"

    # 人物卡（全量，不截断——含 voice_pack 是声纹复刻第一依据，截断 = 后登场角色声纹丢失）
    char_card = read_text(db / '人物卡.json')

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
                cluster_brief_text = json.dumps(cluster_brief, ensure_ascii=False, indent=2)
                # 从 scope_summary 提取硬约束（≥/≤/百分比/角色数等）
                scope = cluster_brief.get('scope_summary', '')
                ewr = cluster_brief.get('expected_word_range', {})
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
                # v27：freestyle 时跳过 cluster.expected_word_range（让 AI 自由发挥）
                # v26 兼容：显式传 ch_end + cluster.expected_word_range 存在时仍注入字数硬约束
                if (not freestyle) and ewr and ewr.get('min') and ewr.get('max'):
                    constraints.append(f"- 字数硬约束：{ewr['min']}-{ewr['max']} {ewr.get('unit', 'CJK_chars')}（cluster.expected_word_range · 必遵守，写完自查不达标即重写）")
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
        print(f"[gen_writer] 事件簇.json 读取失败（不阻塞）: {e}", file=sys.stderr)

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
    # 删除原写死「冷峻俯瞰」默认风 + 原 rule4「单章字数 2500-5000」(违反 freestyle「writer 不知章数」)。
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
（注：顿时/淡淡/仿佛/似乎/缓缓地说 等是「工艺/签名词」，归第二层——若作者风格档把它们列为签名笔法则允许。）

## H4. cluster 契约（user prompt 顶部「CLUSTER 硬约束」段如有）
scope_summary 描述的场景类型/角色构成是**剧情硬契约**（如"对白场景"应有足够角色 + 对话为主），不可跑偏成别的场景。这是「写对剧情」不是「写某种文风」。

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

## C4. 破折号节制（默认）
破折号 ≤ 8/千字（除非 skill 偏好高频破折号）。

## C5. 工艺/签名禁用词（默认 · 作者档可豁免）
默认避免：顿时 / 紧锁 / 显然 / 淡淡 / 此刻 / 仿佛 / 似乎 / 缓缓地说 / 沉吟片刻 / 心中一凛 / 微微挑眉 / 嘴角勾起一抹。
**但若作者风格档的 signature / golden_passages 表明该作者惯用其中某些词，则它们是作者签名笔法，不在此限**——复刻作者优先于通用反 AI 腔。

# 元 anti-slop 防御（已知重犯模式 · 默认基线）

- **poetry-mode 短句三连**：禁用「第一遍 / 第二遍 / 第三遍」「那一瞬 / 那一瞬 / 他记着那一瞬」类 3+ 段独立短段含同短语
- **signature 短语暴涨**：voice signature 单章 ≤ 2 次，跨章不暴涨
- **元-vocab disclaimer**：「他没有这个词」「他不知道这是 X」类全 cluster ≤ 2 次
- **否定动作三件套**：「没说话/没出声/没接腔」单 cluster ≤ 25 次
- **章末抒情模板**：禁写「他不再是 X 的那个 X 了」类抒情收束
- **🆕 否定对照句式过用**：「不是 X——是 Y」「不是 X，是 Y」对照结构单 cluster ≤ 20 次——超出即从恐怖修辞退化成 AI 口头禅（cluster_002 实测 61 次翻车）；多用陈述/动作/反问替代
- **🆕 比喻/质感库单一化**：同一核心意象的喻体（如同化=「打磨过的石头」、某角色声纹标签「声音像放凉的粥」）单 cluster 同喻体 ≤ 4 次——换不同喻体别复读同一个，否则恐怖/角色辨识度被钝化成塑料感
- **🆕 动作环跨场景累计**：单一肢体动作模板（低头看 X / 扯拉链 / 转某道具）单 cluster 累计 ≤ 6 次（C3b 的跨场景强化版·别让配角沦为「单一道具机器」）

# 输出格式（DCAS 模式 · 用户明确偏好）

**核心原则：你输出的是「一整块连续叙事正文」，给后续 chapter-splitter 决定章节自然截断点的素材。**

**严禁预设章节分界**：
- ❌ 不要写「第 N 章 标题」/「第N章 标题」等章节标记
- ❌ 不要用「——」或其他分章分隔符把故事切开
- ❌ 不要写 Markdown 标题（# / ## 等）
- ❌ 不要写「以下是」/「故事开始」等元话语

**正确做法**：把整个故事块当一篇长散文写。场景之间用**自然过渡**（空行 / 时间标记句 / 视角切换句）衔接，**不打章节标签**。splitter 后期会根据自然截断点（场景结束 / 时间跳跃 / POV 切换 / 情绪峰值回落）切分章节并各自命名。

为什么这样：writer（你）擅长连续叙事的内在节奏；splitter（另一个 agent）擅长判断章节边界。预设章节边界 = writer 为「章末必须有钩子」强行设计信息炸弹结尾 = 显得刻意。让 splitter 在你写完后选自然截断点 = 章节边界看起来像页面物理限制而非刻意叙事设计。

完成正文后，在【同一次回复里】紧接着直接输出 JSON 格式的 CHANGES 部分（用 ```json ... ``` 包裹）。

🔴 **你是写作引擎，不是对话助手**（reasoning/instruct 模型尤其注意）：
- 禁止停下来问我、禁止说「请审阅」「要不要我输出 CHANGES」「当你认为正文满足后请告诉我」「我将为你输出」之类的话、禁止等我确认——正文 + CHANGES JSON 必须在**这一次回复**里一次性给全，正文写完直接接 ```json``` 块。
- 正文必须是**纯中文叙事**：严禁在正文里夹任何英文词（argue/KPI/offer/NPC/BUG/cluster/fs/CHANGES/JSON 等流程词或技术词一律用中文表达，如 argue→argue 改说「跟…讲道理/对线」），严禁任何流程说明/创作概要/元注释/对读者喊话（除非作者风格档本身要求破壁旁白）。这些技术词只允许出现在 ```json CHANGES``` 块内部。
"""

    # v22.gov.align.fix Gap T2-X: cluster 硬约束段（顶部显著位）
    cluster_constraints_section = ""
    if cluster_brief_text:
        cluster_constraints_section = f"""## ⚠️ CLUSTER 硬约束（最高优先级 · 违反即重写）

cluster_brief 完整内容：
```json
{cluster_brief_text}
```

### 🎯 必遵守硬指标清单（从 cluster.scope_summary + expected_word_range + hard_constraints 提取）

{cluster_hard_constraints_text if cluster_hard_constraints_text else '（本 cluster 无硬约束 · 按一般指南）'}

**写完正文必自查**：上述硬指标**任一**不达标 → 视为重大失败，自查 JSON 中明确标 `cluster_constraints_violated: true`。
**scope_summary 不是参考，是契约**：cluster.scope_summary 描述的场景类型（如"对白场景"）、角色数（如"≥3 角色"）、占比（如"对话占比 ≥60%"）等是**硬契约**，不是建议。

---

"""

    # v27 freestyle vs v26 兼容：章数 + 目标字数描述
    if freestyle:
        task_intro = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**。

**🎯 v27 自由发挥模式**：
- 你**不知道**目标章数（章数由 splitter 后期按 3000-4500 CJK/章 自然切，章数由你写的内容多少决定）
- 不锁精确字数，但**这是一个完整的故事块（cluster），不是单章**——它由 scene_storyboard 里的**多个场景**构成，**每个场景都要充分展开**（动作 / 对话 / 环境 / 内心 / 冲突推进逐一到位，切忌一笔带过、切忌只写梗概或跳着叙述）。
- **健康篇幅 12000-25000 CJK 是软下限**：一个把所有场景都写透的多场景故事块，自然就落在这个体量。**如果你写到三五千字就觉得"讲完了"，几乎一定是场景展开得太简略**——回头逐个场景写够细节再继续，不要急着收尾。
- **专注做对的事**：把 cluster_brief.scope_summary + scene_storyboard 描述的**每一个场景**都充分展开 + 兑现 foreshadowing_to_plant，全部写透后再自然收尾。

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。
"""
    else:
        task_intro = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**（预计后续 splitter 切成 ch{ch_start}-ch{ch_end} 共 {ch_end - ch_start + 1} 章，但**你不要预先分章**）。

**目标字数**: {target_cjk} CJK（整块总字数；splitter 后每章自然落在 2500-5000）

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。
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
    narr_seq_block = (narr_seq_section + "\n\n") if narr_seq_section else ""
    rolling_anchor_block = (rolling_anchor_section + "\n\n") if rolling_anchor_section else ""
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
    # 「现在请写正文」之前的生成点近邻（RoPE 高位），manifest 事实索引留中段。
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

{seed_block}{style_skill_section}

{style_fp_block}{rhythm_block}{decision_block}{genre_block}{knowledge_gap_block}{narr_seq_block}{rolling_anchor_block}{primacy_block}"""
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

# 现在请写正文"""
    else:
        # off / shadow：原版 join 顺序（零回归回退路径）
        user = f"""{task_intro}
{cluster_constraints_section}{style_fp_block}{rhythm_block}{decision_block}{genre_block}{knowledge_gap_block}{narr_seq_block}{rolling_anchor_block}{seed_block}## cluster_blueprint（必落 anchors）

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

{primacy_block}---

# 现在请写正文"""

    # 生成点尾部（两个 join 分支共用 · 紧贴生成点的指令 + 自查项）
    gen_point_tail = f"""

{"按 7 项硬铁律 + 元 anti-slop 防御 · 完整覆盖 cluster_brief 的所有 scene_storyboard 自由发挥（章数由 splitter 后期切，你不必管）。" if freestyle else f"按 7 项硬铁律 + 元 anti-slop 防御，写 {ch_end - ch_start + 1} 章完整故事块。"}

**自查项**（写完后请在 CHANGES JSON 里自报）：
- word_count_cjk
- paragraph_count
- narrative_paras_with_short_sentence_overuse（≤ 0）
- max_demonstrative_noun_repeat_in_5para_window（≤ 3）
- paragraph_head_subject_3plus_streak_count（≤ 0）
- dash_count / dash_per_thousand（≤ 8）
- meta_vocab_disclaimer_count（≤ 2）
- negation_action_count（≤ 25）
- story_block_ch_range
- facts_locked
- foreshadowing_planted / foreshadowing_paid（**每条务必带 id 字段，引用 cluster_brief.foreshadowing_to_plant 或 manifest 待回收伏笔的真实 fs_id——save_state 据 id 更新伏笔表 resolved 状态；无对应 fs_id 的新伏笔可只给 desc**）

现在开始写。"""

    user += gen_point_tail

    return system, user, seed_trace


# ============ Gen-Model 调用（含 fallback 链） ============
def _build_cont_msg(cont_reason: str) -> str:
    """续写指令文案（openai / gemini 两协议共用 · DRY）。

    CTX_REORDER（P0 · 位置层北极星偏移修）：续写回合原本只有「接着写别重复」，风格 skill 落在最初
    那条 user（被推到中段 U 型最低注意力区），续写生成点近邻没风格约束 → recency 漂移。这里补一行
    精简风格锚（句长 / 对话格式 / 禁结构套话）贴生成点 RoPE 高位重申，防长草稿尾段风格崩塌（advisory）。
    """
    if cont_reason == "expand":
        cont_msg = ("【硬指标·你是写作引擎不是摘要器】这个故事块的健康篇幅是 12000-25000 字，你目前**写得远远不够**——"
                    "scene_storyboard 里一定还有场景没写到、或被一两句话草草带过。"
                    "请接着上文最后一个字继续往下写：**逐个对照 scene_storyboard 的每一幕，把还没写透的场景充分展开**——"
                    "每一幕都要落地完整的：具体动作细节、成段你来我往的对话（不是一句带过）、环境与五感描写、人物内心活动、"
                    "冲突的层层推进与小高潮。宁可把一幕写细写满、也绝不跳过或一笔带过任何一幕。"
                    "**不要重复已写内容、不要重新开头、不要提前收尾、不要写任何总结或概述**。"
                    "**先别写 CHANGES JSON**——等正文真正累积到 12000 字以上、scene_storyboard 每一幕都写透了，我再让你补。")
    elif cont_reason == "changes_only":
        cont_msg = ("正文已经写完。现在请**只输出**这个故事块结尾的 CHANGES JSON 块"
                    "（用 ```json 围栏包裹），**不要再写任何正文、不要重复正文内容**。"
                    "JSON 需包含 factual（locked_facts / foreshadowing_planted / foreshadowing_paid / "
                    "出场角色 等本故事块发生的事实变更）与 self_eval。")
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
    cont_reason：'length'=截断续写；'expand'=写完但太短续写；'changes_only'=只补 CHANGES。
    """
    if getattr(profile, "protocol", "openai") == "gemini":
        return _stream_once_gemini(profile, system, user, max_tokens, prior_assistant, cont_reason)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    if prior_assistant:
        messages.append({"role": "assistant", "content": prior_assistant})
        messages.append({"role": "user", "content": _build_cont_msg(cont_reason)})
    _create_kw = dict(model=profile.model, messages=messages, max_tokens=max_tokens,
                      temperature=profile.temperature, stream=True)
    # gemini-3.x reasoning 模型：thinking_level=LOW 回收 15-25k 输出预算给正文（治 pro 偏短·2026-06-06 联网调研）。
    if getattr(profile, "thinking_level", None):
        _create_kw["extra_body"] = {"thinking_level": profile.thinking_level}
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
    上层截断续写 / expand 逻辑零改动复用。缓存命中 cachedContentTokenCount 打到 stderr 可见。
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
        print(f"\n[gen_writer][gemini] 🟢 缓存命中 cachedContentTokenCount={cached}"
              f"/{usage.get('promptTokenCount', '?')} prompt tokens（省 input 成本）", file=sys.stderr)
    finish_reason = "length" if finish_raw == "MAX_TOKENS" else ("stop" if finish_raw else None)
    return text, finish_reason


# API 调用健壮性常量（2026-05-30 加固）
GEN_MODEL_TIMEOUT = 180.0  # 与 ai_wrapper.py:154 对齐
GEN_MODEL_MAX_RETRIES = 3  # 同 profile 限流/超时的有限重试次数
GEN_MODEL_RETRY_BASE_DELAY = 2.0  # 指数退避基础秒数（2,4,8）


def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   min_cjk: int | None = None) -> tuple[str, Profile]:
    """调当前 active profile；失败时按 fallback 链尝试。

    min_cjk：freestyle 正文长度软下限。设了 → 生成完（finish=stop）但正文 CJK < min_cjk 时，
    追加 expand 续写（展开剩余场景）兜底，治 pro 等简洁模型单 cluster 偏短。蒸馏复刻/ai_wrapper
    不传 → 行为零回归。

    返回 (full_text, used_profile)。
    抛 GenModelExhaustedError（active + 整条 fallback 链全失败）。

    2026-05-30 加固：
      · OpenAI client 显式 timeout（对齐 ai_wrapper）防止无限挂起。
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
    failures: list[tuple[str, str]] = []

    for i, profile in enumerate(candidates):
        max_tokens, mt_source = resolve_max_tokens(profile)
        if i == 0:
            print(f"[gen_writer] 调用 active profile: {profile.name} "
                  f"({profile.model} @ {profile.base_url})", file=sys.stderr)
            print(f"[gen_writer] max_tokens={max_tokens} (source: {mt_source})",
                  file=sys.stderr)
            print(f"[gen_writer] temperature={profile.temperature}", file=sys.stderr)
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[gen_writer] prompt size: system={len(system)} chars, user={len(user)} chars",
              file=sys.stderr)

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url,
                        timeout=GEN_MODEL_TIMEOUT)
        full_text = ""
        try:
            # 同 profile 内：限流/超时做有限指数退避重试，其余异常立即降级 fallback
            attempt = 0
            while True:
                try:
                    full_text, finish_reason = _stream_once(client, profile, system, user, max_tokens)
                    break
                except (RateLimitError, APITimeoutError) as re_err:
                    attempt += 1
                    if attempt > GEN_MODEL_MAX_RETRIES:
                        raise  # 重试耗尽 → 落到外层 except → 降级 fallback
                    delay = GEN_MODEL_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    print(f"\n[gen_writer] ⚠️ {profile.name} 限流/超时 "
                          f"({type(re_err).__name__})，{delay:.0f}s 后同 profile 重试 "
                          f"{attempt}/{GEN_MODEL_MAX_RETRIES}…", file=sys.stderr)
                    time.sleep(delay)
            # 截断检测 + 自动续写（finish_reason == "length" = 命中 max_tokens 被截断）
            cont_rounds = 0
            while finish_reason == "length" and cont_rounds < 3:
                cont_rounds += 1
                print(f"\n[gen_writer] ⚠️ 输出截断(finish_reason=length)，自动续写第 {cont_rounds}/3 轮…",
                      file=sys.stderr)
                cont_text, finish_reason = _stream_once(
                    client, profile, system, user, max_tokens, prior_assistant=full_text)
                full_text += cont_text
            if finish_reason == "length":
                print(f"\n[gen_writer] ⚠️ WARN 续写 {cont_rounds} 轮后仍可能未写完"
                      f"（草稿尾部/CHANGES 块可能不完整 · 下游 cjk 偏短检查兜底）", file=sys.stderr)
        except Exception as e:
            reason = str(e)[:200]
            print(f"\n[FALLBACK] {profile.name} 调用失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue  # 切下一个 profile

        # 空响应守卫：HTTP 200 但零 content（内容过滤 / reasoning model 全进 reasoning_content）
        # 视为失败，切下个 profile（与 except 路径对齐），杜绝写空草稿报成功。
        if not full_text.strip():
            reason = "返回空内容（HTTP 200 但零 content · 可能内容过滤/reasoning model 全进 reasoning_content）"
            print(f"\n[FALLBACK] {profile.name} {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue  # 切下一个 profile

        # [2026-06-04] 内容偏短兜底：pro 等简洁倾向 reasoning 模型常 finish=stop 但正文远不够 cluster
        # 体量（实测 pro 单 cluster 仅 3650 CJK vs 健康 12000-25000）。length 续写只管截断，这里对
        # 「写完了但太短」追加 expand 续写——把 scene_storyboard 没展开的场景写透。
        # 防注水：单轮增量 < FREESTYLE_EXPAND_MIN_GAIN 即停（模型没料别硬凑）· 最多 N 轮 · 软托底非硬锁。
        if min_cjk and full_text.strip():
            def _raw_split(rep):
                ms = list(re.finditer(r'```json\s*\n.*?\n```', rep, re.DOTALL))
                if ms:
                    return rep[:ms[-1].start()].rstrip(), rep[ms[-1].start():]
                return rep.strip(), ""
            accum_body, last_changes = _raw_split(full_text)
            rounds = 0
            while rounds < FREESTYLE_EXPAND_MAX_ROUNDS:
                body_cjk = cio.count_cjk(accum_body)
                if body_cjk >= min_cjk:
                    break
                rounds += 1
                print(f"\n[gen_writer] ⚠️ 正文 {body_cjk} CJK < 软下限 {min_cjk}，"
                      f"内容续写第 {rounds}/{FREESTYLE_EXPAND_MAX_ROUNDS} 轮（展开剩余场景·非截断）…",
                      file=sys.stderr)
                try:
                    cont_text, _fr = _stream_once(client, profile, system, user, max_tokens,
                                                  prior_assistant=accum_body, cont_reason="expand")
                except Exception as e:
                    print(f"[gen_writer] expand 续写第 {rounds} 轮失败（保留已有正文）: {str(e)[:150]}",
                          file=sys.stderr)
                    break
                cont_body, cont_changes = _raw_split(cont_text)
                inc = cio.count_cjk(cont_body)
                if cont_body.strip():
                    accum_body += "\n\n" + cont_body
                if cont_changes:
                    last_changes = cont_changes
                if inc < FREESTYLE_EXPAND_MIN_GAIN:
                    print(f"[gen_writer] 续写增量仅 {inc} CJK（模型已无更多内容）→ 停止兜底，避免注水",
                          file=sys.stderr)
                    break
            # expand 各轮被要求"先别写 CHANGES" → 收尾时若仍缺 CHANGES，追加一次"只补 CHANGES"请求
            # （否则下游 CHANGES_MISSING hard_gate）。仅在确实发生过 expand 续写时才补。
            if rounds > 0 and not last_changes:
                print("[gen_writer] expand 后缺 CHANGES JSON，追加一次补全请求…", file=sys.stderr)
                try:
                    chg_text, _cf = _stream_once(client, profile, system, user, max_tokens,
                                                 prior_assistant=accum_body, cont_reason="changes_only")
                    _, cc = _raw_split(chg_text)
                    if cc:
                        last_changes = cc
                    elif "{" in chg_text:
                        last_changes = "```json\n" + chg_text.strip() + "\n```"
                except Exception as e:
                    print(f"[gen_writer] 补 CHANGES 失败（下游 normalize 兜底）: {str(e)[:120]}",
                          file=sys.stderr)
            full_text = accum_body + ("\n\n" + last_changes if last_changes else "")
            print(f"\n[gen_writer] 内容兜底完成：正文 {cio.count_cjk(accum_body)} CJK（expand {rounds} 轮）"
                  f"· CHANGES={'有' if last_changes else '无'}", file=sys.stderr)

        # 成功
        print(f"\n[gen_writer] 接收完毕 ({len(full_text)} chars) via {profile.name}",
              file=sys.stderr)
        return full_text, profile

    # 全链失败
    raise GenModelExhaustedError(failures)


# ============ best-of-N：生成 N 稿 + 配对重排 + 综合择优 ============
def gather_author_ref_text(project_root: Path, max_chars: int = 6000) -> str:
    """定位作者真实原文当 SFS / AV-judge 的锚（best-of-N 择优用 · 找不到则空）。

    复用 snippet_seed.resolve_originals_dir 的同款原文池定位（项目自带 原文/ → 风格库 原文/），
    取若干章拼成参考文本（截到 max_chars 控 SFS / judge token）。找不到原文池 → 返回 ""，
    调用方据此优雅降级（无锚则跳 best-of-N · 退回单稿 · 不报错 · 不阻断写作）。
    """
    try:
        originals = snippet_seed.resolve_originals_dir(project_root)
    except Exception:
        originals = None
    if not originals or not originals.exists():
        return ""
    chunks: list[str] = []
    total = 0
    for p in sorted(originals.glob("*.txt")):
        try:
            t = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not t:
            continue
        chunks.append(t)
        total += len(t)
        if total >= max_chars:
            break
    ref = "\n\n".join(chunks)
    return ref[:max_chars] if len(ref) > max_chars else ref


def generate_n_drafts(loader: GenModelLoader, system: str, user: str,
                      n: int, min_cjk: int | None = None) -> list[dict]:
    """生成 N 个候选稿（temperature 阶梯抖动 · 非迭代 · 规避 self-refine 同质化）。

    每个候选独立调一次 call_gen_model（active→fallback 链复用 · 不另起调用栈）。
    通过临时改 candidate profile 的 temperature 让 N 稿真有差异（多样性 = best-of-N 价值）。
    单个候选生成失败（GenModelExhaustedError）→ 记录跳过，不中断其余候选；全失败由调用方 raise。

    返回 [{idx, reply, profile, temperature, error}]（error 非空 = 该候选生成失败被跳过）。
    """
    candidates = loader.get_callable_profiles()
    if not candidates:
        raise GenModelExhaustedError([("<none>", "no callable profiles")])
    base_temp = candidates[0].temperature
    temps = _candidate_temperatures(base_temp, n)
    drafts: list[dict] = []
    for i, temp in enumerate(temps):
        # 临时把 active profile 的 temperature 改成本候选温度（生成后还原 · 不污染 loader）。
        active0 = candidates[0]
        orig_temp = active0.temperature
        active0.temperature = temp
        print(f"\n[gen_writer][best-of-N] 生成候选 {i+1}/{n} (temperature={temp})",
              file=sys.stderr)
        try:
            reply, used_profile = call_gen_model(loader, system, user, min_cjk=min_cjk)
            drafts.append({"idx": i, "reply": reply, "profile": used_profile,
                           "temperature": temp, "error": None})
        except GenModelExhaustedError as e:
            print(f"[gen_writer][best-of-N] 候选 {i+1} 生成失败（跳过）: {str(e)[:150]}",
                  file=sys.stderr)
            drafts.append({"idx": i, "reply": None, "profile": None,
                           "temperature": temp, "error": str(e)[:200]})
        finally:
            active0.temperature = orig_temp
    return drafts


def score_candidate(body: str, author_ref: str, loader: GenModelLoader,
                    use_av_judge: bool = True) -> dict:
    """给单个候选稿打分（SFS 统计指纹 + AV-judge 配对走味计数 · 全 advisory · 透明可读）。

    · sfs：style_evaluator.compute_style_only_sfs(author_ref, body) → style_only_sfs（0-100，越高越像）。
      无 author_ref 或 style_evaluator 不可用 → sfs=None（调用方据此退化排序）。
    · av_drift_count：av_judge.pairwise_drift_count → 走味维度数（0-4，越少越像）。
      use_av_judge=False（无锚 / AV_JUDGE_MODE=off 等）或调用失败 → av_drift_count=None。
    · composite：综合分 = sfs - AV_DRIFT_PENALTY_PER_DIM * av_drift_count（缺项各自降级）。
      仅 sfs 缺 → composite=None（调用方按 av_drift_count 升序兜底）；仅 av 缺 → composite=sfs。

    全 advisory：任何子项失败都不抛错（写作流水线不被择优层中断 · 北极星⑤）。
    """
    result: dict = {"sfs": None, "sfs_subscores": None,
                    "av_drift_count": None, "av_drift_dims": [],
                    "composite": None, "errors": []}
    # ① SFS 统计指纹（透明裁判）
    if author_ref and author_ref.strip():
        try:
            import style_evaluator as se
            sfs = se.compute_style_only_sfs(author_ref, body)
            result["sfs"] = round(float(sfs.get("style_only_sfs")), 2)
            result["sfs_subscores"] = sfs.get("subscores")
        except Exception as e:  # noqa: BLE001 — 择优层不阻断写作
            result["errors"].append(f"sfs: {str(e)[:120]}")
    # ② AV-judge 配对走味计数（读者视角 · 只 select 不强判）
    if use_av_judge and author_ref and author_ref.strip():
        try:
            import av_judge as avj
            av = avj.pairwise_drift_count(loader, author_ref, body)
            if av.get("error") is None and av.get("drift_count") is not None:
                result["av_drift_count"] = av["drift_count"]
                result["av_drift_dims"] = av.get("drift_dims", [])
            elif av.get("error"):
                result["errors"].append(f"av_judge: {av['error'][:120]}")
        except Exception as e:  # noqa: BLE001 — 择优层不阻断写作
            result["errors"].append(f"av_judge: {str(e)[:120]}")
    # ③ 综合分
    sfs, avc = result["sfs"], result["av_drift_count"]
    if sfs is not None:
        penalty = AV_DRIFT_PENALTY_PER_DIM * avc if avc is not None else 0.0
        result["composite"] = round(sfs - penalty, 2)
    return result


def select_best_draft(scored: list[dict]) -> tuple[int, str]:
    """从打分后的候选里选最佳（综合分降序 · 缺 SFS 时按走味数升序兜底 · 确定性可测）。

    scored: [{idx, body, score: {composite, sfs, av_drift_count, ...}, error}]（已过滤生成失败的）。
    排序键（全候选可比 · 缺项一致降级）：
      1. composite 有值 → 用 composite 降序（最像作者排最前）。
      2. 全候选都没 composite（无 author_ref / SFS 全挂）→ 按 av_drift_count 升序（走味越少越好），
         av 也没有 → 字数兜底：优先 CJK 达标(>=FREESTYLE_MIN_CJK)候选里 idx 最小者（保留零回归：
         第一稿达标就退第一稿），全不达标退 CJK 最大者。治 best-of-N 丢 expand 达标稿落短稿的 bug。
    返回 (best_idx_in_list, reason)。reason 解释凭什么选（不黑箱 · 北极星⑤）。
    """
    if not scored:
        raise ValueError("无可选候选（全部生成失败）")
    if len(scored) == 1:
        return 0, "唯一候选（N=1 或仅 1 稿生成成功）"

    have_composite = [s for s in scored if s["score"].get("composite") is not None]
    if have_composite:
        best = max(range(len(scored)),
                   key=lambda i: (scored[i]["score"].get("composite") is not None,
                                  scored[i]["score"].get("composite") or float("-inf")))
        sc = scored[best]["score"]
        reason = (f"综合分最高 composite={sc.get('composite')} "
                  f"(SFS={sc.get('sfs')} · AV走味={sc.get('av_drift_count')})")
        return best, reason

    # 无 composite：按走味数升序兜底
    have_av = any(s["score"].get("av_drift_count") is not None for s in scored)
    if have_av:
        best = min(range(len(scored)),
                   key=lambda i: (scored[i]["score"].get("av_drift_count")
                                  if scored[i]["score"].get("av_drift_count") is not None
                                  else 99))
        return best, (f"无 SFS 锚 · 按 AV-judge 走味数升序选 "
                      f"(走味={scored[best]['score'].get('av_drift_count')})")
    # 全无打分信号（新书无作者池/SFS全挂）→ 字数兜底，不机械退 idx=0。
    # 根因：freestyle 各候选独立生成，有的触发 expand 补到达标、有的偏短；机械退 idx=0
    #   会把 expand 后的达标稿丢掉、落地短稿（实测 idx=0=4399 短 vs idx=1=15316 达标却选了 4399）。
    # 修：优先 CJK 达标(>=FREESTYLE_MIN_CJK)的候选里 idx 最小者（达标 + 保留零回归精神：
    #   第一稿达标就仍退第一稿）；全不达标 → 退 CJK 最大者（最接近健康区间）。
    qualified = [i for i in range(len(scored))
                 if scored[i].get("body_cjk", 0) >= FREESTYLE_MIN_CJK]
    if qualified:
        best = min(qualified)
        cjk0 = scored[0].get("body_cjk", 0)
        if best == 0:
            return 0, (f"无 SFS/AV 打分信号 · 第一稿 CJK={cjk0} 达标 · 退回第一稿（零回归保底）")
        return best, (f"无 SFS/AV 打分信号 · 第一稿 CJK={cjk0} 偏短(<{FREESTYLE_MIN_CJK}) · "
                      f"选首个达标候选 idx={scored[best]['idx']} CJK={scored[best].get('body_cjk')}（字数兜底）")
    # 全候选均偏短 → CJK 最大者（最接近健康区间 · 总比退 idx=0 的更短稿强）
    best = max(range(len(scored)), key=lambda i: scored[i].get("body_cjk", 0))
    return best, (f"无 SFS/AV 打分信号 · 全候选均偏短 · 选 CJK 最大 idx={scored[best]['idx']} "
                  f"CJK={scored[best].get('body_cjk')}（字数兜底）")


def best_of_n_pipeline(loader: GenModelLoader, system: str, user: str,
                       project_root: Path, n: int,
                       min_cjk: int | None = None) -> tuple[str, "Profile", dict]:
    """best-of-N 主流程：生成 N 稿 → 各自打分 → 综合择优 → 返回最佳稿。

    返回 (best_reply, best_profile, selection_trace)。
    selection_trace 记录每个候选的分数 + 选中理由（写进 changes 不黑箱 · 北极星⑤）。
    优雅降级：无 author_ref → 跳 SFS/AV-judge，仍生成 N 稿但按 idx 选第一稿（等价单稿 · 不报错）。
    """
    author_ref = gather_author_ref_text(project_root)
    use_av_judge = bool(author_ref.strip())
    if not author_ref.strip():
        print("[gen_writer][best-of-N] 未找到作者原文池 · 跳过 SFS/AV-judge 打分 "
              "（仍生成 N 稿但退回第一稿 · 优雅降级）", file=sys.stderr)

    drafts = generate_n_drafts(loader, system, user, n, min_cjk=min_cjk)
    ok_drafts = [d for d in drafts if d.get("error") is None and d.get("reply")]
    if not ok_drafts:
        # 全部候选生成失败 → 汇总 raise（与单稿全失败行为一致）
        raise GenModelExhaustedError(
            [("best_of_n", "; ".join(d.get("error", "?") for d in drafts) or "all empty")])

    scored: list[dict] = []
    for d in ok_drafts:
        body, _changes = split_text_and_changes(d["reply"])
        sc = score_candidate(body, author_ref, loader, use_av_judge=use_av_judge)
        scored.append({"idx": d["idx"], "reply": d["reply"], "profile": d["profile"],
                       "temperature": d["temperature"], "body_cjk": cio.count_cjk(body),
                       "score": sc, "error": None})
        print(f"[gen_writer][best-of-N] 候选 idx={d['idx']} temp={d['temperature']}: "
              f"SFS={sc['sfs']} AV走味={sc['av_drift_count']} composite={sc['composite']} "
              f"cjk={cio.count_cjk(body)}", file=sys.stderr)

    best_i, reason = select_best_draft(scored)
    best = scored[best_i]
    print(f"\n[gen_writer][best-of-N] ✅ 选中候选 idx={best['idx']} "
          f"(temp={best['temperature']}) — {reason}", file=sys.stderr)

    trace = {
        "best_of_n": n,
        "candidates_generated": len(drafts),
        "candidates_scored": len(scored),
        "author_ref_found": use_av_judge,
        "selected_idx": best["idx"],
        "selection_reason": reason,
        "candidates": [
            {"idx": s["idx"], "temperature": s["temperature"], "cjk": s["body_cjk"],
             "sfs": s["score"]["sfs"], "av_drift_count": s["score"]["av_drift_count"],
             "av_drift_dims": s["score"]["av_drift_dims"],
             "composite": s["score"]["composite"], "errors": s["score"]["errors"]}
            for s in scored
        ],
    }
    return best["reply"], best["profile"], trace


# ============ 输出解析与保存 ============
def split_text_and_changes(reply: str) -> tuple:
    """从返回拆出正文 + CHANGES JSON"""
    # 去掉 $ 末尾锚定（对齐 gen_fixer 写法）：LLM 在 json 块后多输出尾随文字也能匹配。
    # 用 finditer 取「最后一个」```json``` 块，正文 = 该块之前的内容。
    json_matches = list(re.finditer(r'```json\s*\n(.*?)\n```', reply, re.DOTALL))
    if json_matches:
        last = json_matches[-1]
        changes_json = last.group(1).strip()
        body = reply[:last.start()].rstrip()
    else:
        body = reply.strip()
        changes_json = "{}"

    # 去掉可能的 "# 正文" 这类元标题
    body = re.sub(r'^#\s*(正文|cluster.*)\s*\n', '', body, flags=re.MULTILINE)

    # [2026-06-05] 剥离推理模型漏出的「创作说明/推理概要」元前言（正文前 + --- 分隔 · flash 等推理模型常见）：
    # 仅当 body 开头是含『概要/推理/创作说明/创作思路』的 markdown 标题、且后接 --- 分隔时才剥，避免误伤正文。
    if re.match(r'^\s*#{1,4}[^\n]*(概要|推理|创作说明|创作思路)[^\n]*\n', body):
        _sep = re.search(r'\n\s*-{3,}\s*\n', body[:2500])
        if _sep:
            body = body[_sep.end():].lstrip()
            print("[gen_writer] [strip] 剥离模型漏出的『创作说明/推理概要』元前言（正文前 + --- 分隔）",
                  file=sys.stderr)

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
        print("[gen_writer] [strip] 剥离 reasoning 模型破壁助手尾注（请审阅/请告诉我/CHANGES JSON 类）",
              file=sys.stderr)

    # DCAS 模式（用户偏好）：如果 gen-model 仍误带「第 N 章 标题」分章标记，stderr 警告
    # 不主动删除（让 splitter 决定怎么处理），只提示 prompt 没生效
    if re.search(r'^第\s*[一二三四五六七八九十百千\d]+\s*章\s', body, re.MULTILINE):
        print("[gen_writer] [WARN] gen-model 输出含「第 N 章 标题」分章标记 — "
              "splitter 应忽略这些标记重新决定截断点。"
              "若反复出现，调高 prompt 强度或换 profile。", file=sys.stderr)

    try:
        changes_obj = json.loads(changes_json)
    except json.JSONDecodeError:
        changes_obj = {"_doc": "返回 CHANGES JSON 解析失败", "raw": changes_json[:2000]}
    return body, changes_obj


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
        print(f"[gen_writer] 短段约束跳过：作者密实多句长段(单句独行 {author_single:.0%}<0.5)→保留复合长段·不拆碎句(对齐段长契约)", file=sys.stderr)
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
        print(f"[gen_writer] 短段约束：切分 {changed} 个过长非对话段（阈值 {threshold:.0f} 字·作者段长基线 {base:.1f}）",
              file=sys.stderr)
    return new_body


def save_output(project_root: Path, cluster_id: int, body: str, changes: dict,
                ch_start: int, ch_end: int, used_profile: Profile,
                seed_trace: dict = None, best_of_n_trace: dict = None):
    """写 draft + changes.json

    v27 freestyle：ch_end=None 时 ch_range 写 'TBD_by_splitter'（splitter 后期填）。
    seed_trace：snippet_seed 播种痕迹（用了几段 / 哪个模式）· 留 changes 不黑箱（北极星⑤）。
    best_of_n_trace：best-of-N 择优痕迹（N 稿各自分数 + 选中理由）· 留 changes 透明可审（北极星⑤）。
    """
    # 空 body 守卫（2026-05-30 加固）：拒写空草稿并报错，避免 cjk=0 草稿入库还报成功。
    # 上游 call_gen_model 已对空响应切 fallback，此处是最后一道防线（含解析后正文为空的情况）。
    if not body.strip():
        raise ValueError(
            f"[gen_writer] 拒绝写入空草稿（cluster_{cluster_id:03d}）：解析后正文为空。"
            f"可能是 gen-model 返回空内容或全为 CHANGES JSON 无正文 — 请检查 profile 输出。"
        )

    # 2026-06-07 根治：草稿落地前确定性清洗 gen-model 原始输出的机械格式病
    # ① 整块逐字复制（freestyle expand 复制事故 RR_001）② 成对符号腰斩
    # （引号“”/【】/《》/（）被句末标点+换行劈开，质检长期只查引号没查【】，靠人读逐个逮）。
    # 纯文本、幂等、不调模型（draft_sanitizer.py）；失败仅告警不阻断落地。
    try:
        from draft_sanitizer import sanitize as _sanitize_draft
        body, _san_rep = _sanitize_draft(body)
        if _san_rep.get('dedup_segments_removed') or _san_rep.get('pair_merges'):
            print(f"[gen_writer] draft_sanitizer 清洗：整块去重 {_san_rep['dedup_segments_removed']} 段 · "
                  f"成对符号腰斩合并 {_san_rep['pair_merges']} 处", file=sys.stderr)
    except Exception as _e:
        print(f"[gen_writer] [WARN] draft_sanitizer 清洗跳过（{_e}）— 草稿原样落地", file=sys.stderr)

    draft_dir = project_root / '章节' / f'cluster_{cluster_id:03d}_draft'
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f'cluster_{cluster_id:03d}_draft.txt'
    changes_path = draft_dir / f'cluster_{cluster_id:03d}_changes.json'

    # 2026-06-13 残余非原子写收编：草稿是 cluster 主轨核心产物，原子落盘（tmp+fsync+replace）。
    atomic_write_text(draft_path, body)

    # 补全 changes 元数据
    cjk = cio.count_cjk(body)  # v27 修复：统一 CJK 口径走 chapter_io（覆盖扩展 CJK）
    freestyle = (ch_end is None)
    ch_range_str = f'{ch_start}-TBD_by_splitter' if freestyle else f'{ch_start}-{ch_end}'

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
        'chapter_count_decided_by_splitter': freestyle,
        'generated_by': 'gen_writer.py',
        'generated_by_profile': used_profile.name,
        'generated_by_model': used_profile.model,
        'generated_at': datetime.now().isoformat(),
        'cjk_actual': cjk,
        'writer_mode': 'freestyle_v27' if freestyle else 'locked_v26',
        'snippet_seed': seed_trace or {'snippet_seed_mode': 'on', 'injected': False},
        'best_of_n': best_of_n_trace or {'best_of_n': 1, 'note': '单稿直生（BEST_OF_N=1 或未启用）'},
    })
    se.setdefault('waivers', [])
    se.setdefault('uncertainty_flags', [])
    changes.setdefault('schema_version', 'v2.cluster')
    # 2026-06-13 同批收编：changes.json 半截损坏 = 下游 audit_hub/split_cluster_changes 解析崩。
    atomic_write_text(changes_path,
                      json.dumps(changes, ensure_ascii=False, indent=2))

    print(f"\n[gen_writer] 写出:", file=sys.stderr)
    print(f"  正文: {draft_path} ({cjk} CJK)", file=sys.stderr)
    print(f"  CHANGES: {changes_path}", file=sys.stderr)
    print(f"  profile: {used_profile.name} ({used_profile.model})", file=sys.stderr)
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
            capture_output=True, text=True, encoding='utf-8'
        )
        try:
            d = json.loads(r.stdout)
            results[sc] = {'verdict': d.get('verdict'), 'violations_count': d.get('violations_count')}
        except Exception as e:
            # v27 修复：静默 except 加日志（之前完全静默吞错·debug 困难）
            print(f"  [run_scanners] {sc} 输出 JSON 解析失败 ({e})·exit={r.returncode}·stderr_preview={(r.stderr or '')[:150]}",
                  file=sys.stderr)
            results[sc] = {'verdict': 'ERROR', 'stdout_preview': r.stdout[:300]}
    return results


# ============ 主入口 ============
def main():
    check_deps()
    parser = argparse.ArgumentParser(description='Gen-Model 正文生成器（OpenAI 兼容协议）')
    parser.add_argument('--project', required=True, help='项目根路径')
    parser.add_argument('--cluster', type=int, required=True, help='cluster id（整数）')
    parser.add_argument('--chapter-start', type=int, default=None,
                        help='[v27 optional] cluster 起始章号 · 缺省时从事件簇.json 推导（cluster_001=1 / 后续=上 cluster 末章+1）')
    parser.add_argument('--chapter-end', type=int, default=None,
                        help='[v27 deprecated optional] cluster 结束章号 · 缺省 = freestyle 模式（writer 不知章数 · splitter 按字数切）')
    parser.add_argument('--target-cjk', default=None,
                        help='[v27 optional] 整 cluster 目标字数 · 缺省 = freestyle（按 scope_summary 自然涌现）')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt，不调 API')
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[ERROR] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    # v27：ch_start 缺省 → 从事件簇.json + 已写章节推导
    ch_start = args.chapter_start
    if ch_start is None:
        ch_start = _infer_cluster_start_ch(project_root, args.cluster)
        print(f"[gen_writer] [v27 freestyle] 推导 ch_start={ch_start} (cluster_{args.cluster:03d})",
              file=sys.stderr)

    # 模式判断 + 提示
    if args.chapter_end is None:
        print(f"[gen_writer] [v27 freestyle 模式] writer 不知目标章数 · splitter 按字数切 · 章数自然涌现",
              file=sys.stderr)
    else:
        print(f"[gen_writer] [v26 兼容模式] ch_start={ch_start} ch_end={args.chapter_end} target_cjk={args.target_cjk}",
              file=sys.stderr)

    # dry-run 模式不需要 active profile
    if args.dry_run:
        system, user, seed_trace = build_prompt(project_root, args.cluster, ch_start,
                                                args.chapter_end, args.target_cjk)
        print("=== SYSTEM PROMPT ===")
        print(system)
        print("\n=== USER PROMPT ===")
        print(user)
        print(f"\n[dry-run] system={len(system)} chars / user={len(user)} chars",
              file=sys.stderr)
        print(f"[dry-run] snippet_seed: {seed_trace}", file=sys.stderr)
        # 显示当前 active profile 信息
        try:
            loader = GenModelLoader()
            p = loader.get_active_profile()
            print(f"[dry-run] active profile: {p.name} ({p.model} @ {p.base_url})",
                  file=sys.stderr)
        except GenModelConfigError as e:
            print(f"[dry-run] [WARN] active profile 未就绪: {e}", file=sys.stderr)
        return

    # 加载 gen-model 配置
    try:
        loader = GenModelLoader()
        active = loader.get_active_profile()  # 校验 active 就绪
    except GenModelConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        print("  跑 python core/scripts/gen_model.py list / switch 修复",
              file=sys.stderr)
        sys.exit(2)

    print(f"[gen_writer] 加载配置: {loader.env_path}", file=sys.stderr)
    print(f"[gen_writer] active = {active.name}", file=sys.stderr)
    # token ledger（一人公司·BYOK 用户看烧多少钱）：设账本路径·llm_transport 自动 append（已设则尊重）
    os.environ.setdefault("RUOYU_TOKEN_LEDGER",
                          str(project_root / "_数据库" / ".token_ledger.jsonl"))
    chain = loader.get_fallback_chain()
    if chain:
        print(f"[gen_writer] fallback chain = {','.join(chain)}", file=sys.stderr)

    system, user, seed_trace = build_prompt(project_root, args.cluster, ch_start,
                                            args.chapter_end, args.target_cjk)

    # best-of-N（默认 active · N≥2 真生效 · BEST_OF_N=1 退回单稿直生 · 2026-05-31）：
    # N 稿并行生成（temperature 阶梯抖动）→ SFS + AV-judge 配对判别打分 → 综合择优。
    # selection（择优）≠ refine（迭代）→ 天然规避 self-refine 同质化（arxiv 实证）。
    n = _best_of_n()
    # [2026-06-04] freestyle 才启用正文长度软下限兜底（locked v26 模式由 target_cjk 自己管）。
    # 治 pro 等简洁倾向模型单 cluster 偏短（实测 3650 vs 健康 12000-25000）。
    min_cjk = FREESTYLE_MIN_CJK if args.chapter_end is None else None
    if min_cjk:
        print(f"[gen_writer] [v27 freestyle] 正文长度软下限 min_cjk={min_cjk}（偏短→expand 续写兜底）",
              file=sys.stderr)
    best_of_n_trace = None
    try:
        if n >= 2:
            print(f"\n[gen_writer][best-of-N] BEST_OF_N={n} · 生成 {n} 稿配对重排择优",
                  file=sys.stderr)
            reply, used_profile, best_of_n_trace = best_of_n_pipeline(
                loader, system, user, project_root, n, min_cjk=min_cjk)
        else:
            print(f"[gen_writer][best-of-N] BEST_OF_N=1 · 单稿直生（已关闭择优）",
                  file=sys.stderr)
            reply, used_profile = call_gen_model(loader, system, user, min_cjk=min_cjk)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(3)

    body, changes = split_text_and_changes(reply)
    # [2026-06-05] 句法熔合已删（盲目拉长句长的 gen-model 编辑 pass·分不清流水账碎句 vs 反高潮 ！？ 短句·
    # 会削掉搞笑流命根子·实测 flash 裸输出感叹~29/千→熔合后 0.9·拖后腿）。只留短段约束（按作者段长切过长
    # 非对话段·格式层·不动 ！？）。流水账靠模型自身 + prose_rhythm / reading-reflector advisory 兜。
    if min_cjk is not None:
        _auth_sent, _auth_para, _auth_single = _read_author_rhythm(project_root)
        print(f"[gen_writer] 作者节奏基线：句长={_auth_sent} 段长={_auth_para} 单句独行={_auth_single}",
              file=sys.stderr)
        body = enforce_short_paragraphs(body, author_para_mean=_auth_para, author_single=_auth_single)
    draft_path, cjk = save_output(project_root, args.cluster, body, changes,
                                  ch_start, args.chapter_end, used_profile,
                                  seed_trace=seed_trace, best_of_n_trace=best_of_n_trace)

    print(f"\n[gen_writer] 跑 scanner...", file=sys.stderr)
    scan_results = run_scanners(draft_path)
    print(f"\n=== Scanner Results ===", file=sys.stderr)
    for sc, res in scan_results.items():
        print(f"  {sc}: {res}", file=sys.stderr)

    # 字数检查（v27 freestyle 无硬下限 · v26 兼容才校验目标）
    if args.target_cjk:
        try:
            target_min, target_max = map(int, args.target_cjk.split('-'))
            if cjk < target_min:
                print(f"\n[WARN] 字数 {cjk} < 目标下限 {target_min}", file=sys.stderr)
            elif cjk > target_max:
                print(f"\n[WARN] 字数 {cjk} > 目标上限 {target_max}", file=sys.stderr)
            else:
                print(f"\n[OK] 字数 {cjk} 在目标范围 [{target_min}, {target_max}]",
                      file=sys.stderr)
        except (ValueError, AttributeError):
            print(f"\n[gen_writer] target_cjk 解析失败，跳过字数校验: {args.target_cjk}",
                  file=sys.stderr)
    else:
        # v27 freestyle：软提示（splitter 健康区间）
        if cjk < 8000:
            print(f"\n[v27 freestyle] [HINT] cjk={cjk} 偏短 · 切 3 章可能不够（splitter 可能从下个 cluster 补料）",
                  file=sys.stderr)
        elif cjk > 30000:
            print(f"\n[v27 freestyle] [HINT] cjk={cjk} 偏长 · splitter 会切成 7+ 章",
                  file=sys.stderr)
        else:
            print(f"\n[v27 freestyle] [OK] cjk={cjk} 健康区间 8000-30000",
                  file=sys.stderr)


if __name__ == '__main__':
    main()
