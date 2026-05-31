#!/usr/bin/env python3
"""
distill_replicate.py — 蒸馏 phase-4 cluster 终验复刻（v3: cluster 单轨 · 2026-05-28）

强制走 gen-model（OpenAI 兼容协议外部模型），不走 Claude Code sub-agent。
v22.cluster.3 起的硬约束 —— 蒸馏闭环复刻必须用最终写作要用的 gen-model 来测。

v3 改动（2026-05-28 全系统 cluster 化方案 · memory feedback_full_system_cluster_centric）：
- ❌ 删除 chapter 模式（v3 cluster 单轨化 · chapter 中检违背 cluster 单轨原则）
- ❌ 删除单段 drill 模式（早已废弃）
- ✅ --mode cluster（故事块 4000-20000 字）：唯一终验路径
- ✅ cluster 模式拆 sub-call 防 timeout（沿用 cluster_segmenter A' 半 cluster 教训）

用法：

  python core/scripts/distill_replicate.py \\
    --style-skill workspace/styles/<书名>/skill_v<N>.md \\
    --mode cluster \\
    --cluster-ref cluster_001 \\
    --project workspace/styles/<书名> \\
    --output workspace/styles/<书名>/复刻测试/v<N>_round<M>/cluster_001_replica.txt

输出：
- 复刻文本（纯 txt UTF-8 无 markdown 标记）
- meta.json sidecar（调用 profile / sub-calls 数 / 字数 / 耗时）

配置：参见 .env GEN__<name>__* + GEN_MODEL_ACTIVE
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import (  # noqa: E402
    GenModelLoader,
    GenModelConfigError,
    GenModelExhaustedError,
    Profile,
)
import snippet_seed  # noqa: E402 · 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 off · 影子）


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
        sys.exit(2)


def resolve_max_tokens(profile: Profile, default: int = 4000) -> int:
    if profile.max_tokens is not None:
        return profile.max_tokens
    return default


def read_text(p: Path | None, limit: int | None = None) -> str:
    if p is None or not p.exists():
        return ""
    t = p.read_text(encoding='utf-8')
    if limit and len(t) > limit:
        t = t[:limit] + f"\n... [truncated at {limit} chars]"
    return t


def cjk_count(text: str) -> int:
    return sum(1 for ch in text if '一' <= ch <= '鿿')


def collapse_degenerate_runs(
    text: str,
    single_char_max_run: int = 30,
    short_str_max_repeat: int = 20,
    short_str_max_len: int = 4,
    keep_single: int = 10,
    keep_short: int = 10,
) -> tuple[str, list[dict]]:
    """检测并截断 LLM 复读退化串（连续重复单字 / 连续重复短串）。

    实测翻车：复刻含 3826 字连续「铛」串（占 27% CJK）→ LLM 复读退化 → 污染 SFS + 产出。
    作者真实拟声只用单行短串（如「铛。」独段），绝不会 3826 字。

    两类退化：
    1. 同一字连续 > single_char_max_run 次 → 保留 keep_single 个
    2. 同一短串（≤ short_str_max_len 字）连续重复 > short_str_max_repeat 次 → 保留 keep_short 个

    返回 (清洗后文本, 命中记录列表)。命中记录供调用方写 stderr WARN + meta sidecar。
    """
    hits: list[dict] = []

    # ── 第一类：同一字符连续超长 run（用 backreference 捕获 ≥ run+1 次）──
    def _collapse_char(m: "re.Match") -> str:
        ch = m.group(1)
        run_len = len(m.group(0))
        hits.append({"type": "single_char", "char": ch,
                     "run_length": run_len, "kept": keep_single})
        return ch * keep_single

    # (.) 捕获任一字符（含拟声字 / 标点 / 空白），\1{N,} 要求其后再连续重复 N 次以上
    # 即同字总连续 > single_char_max_run 才触发
    char_pat = re.compile(r"(.)\1{" + str(single_char_max_run) + r",}", flags=re.DOTALL)
    text = char_pat.sub(_collapse_char, text)

    # ── 第二类：同一短串（2..short_str_max_len 字）连续重复超量 ──
    # 从短到长匹配：优先用最小周期截断（「哈嘿哈嘿…」按周期 2 截，不当周期 4），
    # 截得更紧更可预测；真正的周期 3/4 串（如「哈嘿呼」复读）不匹配短周期 → 在自身 unit_len 命中。
    for unit_len in range(2, short_str_max_len + 1):
        def _collapse_short(m: "re.Match", _ul: int = unit_len) -> str:
            unit = m.group(1)
            repeat = len(m.group(0)) // _ul
            hits.append({"type": "short_str", "unit": unit,
                         "unit_len": _ul, "repeat": repeat, "kept": keep_short})
            return unit * keep_short

        # (单元){N,}：单元长度精确 unit_len，连续重复 > short_str_max_repeat 次才触发
        short_pat = re.compile(
            r"((?:.){" + str(unit_len) + r"})\1{" + str(short_str_max_repeat) + r",}",
            flags=re.DOTALL,
        )
        text = short_pat.sub(_collapse_short, text)

    return text, hits


def clean_output(text: str) -> str:
    """清理 LLM 输出：去掉 markdown 包裹、前后引言、退化复读串、多余空行"""
    text = re.sub(r"^```[a-z]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n```\s*$", "", text)
    text = re.sub(r"^(以下是|这是|这里是|下面是)[^\n]{0,40}[:：]\s*\n", "", text)
    text, degen_hits = collapse_degenerate_runs(text)
    for h in degen_hits:
        if h["type"] == "single_char":
            print(f"[WARN · degenerate] 检测到单字「{h['char']}」连续复读 {h['run_length']} 次 "
                  f"→ 截断保留 {h['kept']} 个（LLM 复读退化）", file=sys.stderr)
        else:
            print(f"[WARN · degenerate] 检测到短串「{h['unit']}」连续复读 {h['repeat']} 次 "
                  f"→ 截断保留 {h['kept']} 个（LLM 复读退化）", file=sys.stderr)
    return text.strip()


# ============ L3b · CoT-first 自解释（CoTeX + Register Analysis · 2026-05-31）============
#
# 根因：复刻是「得分→盲改 skill→再测」乏力循环 · LLM 直接写长文易段长崩塌（cluster 级 D 级）。
# 升级（CoTeX 自解释 + Register Analysis）：让 gen-model **先逐条点名「本段命中 skill 哪几条
# 量化坐标（句长/段长/单句独行/标点密度/虚词）」再写正文** · 用受控语言学坐标
# （不是「冷峻/华丽」感性词 · 后者改意泄漏内容）。CoTeX 实证 1K 样本 BLEU 68 vs 55。
#
# 北极星纪律：只改 prompt 构造（确定性可测）· 不改「复刻走 gen-model」的事实 ·
# 保留旧 prompt 路径对照（env L3B_COT_FIRST_MODE=off）· CoT 是思考脚手架，落盘正文不含分析段
# （strip 掉 · 但 meta.json 留 CoT 痕迹 · 不黑箱）。

# CoT 段与正文段的分隔标记（既给 LLM 看也供 strip_cot_analysis 解析）
COT_ANALYSIS_MARKER = "[本段量化坐标分析]"
COT_BODY_MARKER = "[正文]"

# 受控语言学坐标自解释指令（Register Analysis · 全部可量化坐标 · 严禁感性词）
COT_FIRST_DIRECTIVE = f"""# 🔬 先分析后写（CoT-first 自解释 · 必须两段式输出）

在写正文**之前**，先输出一段「量化坐标分析」，逐条点名本段将命中 skill 的哪几条**受控语言学坐标**。
只准用可量化坐标，**严禁**用「冷峻 / 华丽 / 大气 / 有张力」等感性形容词（感性词会泄漏改意、无法核对）。

## 第一段：{COT_ANALYSIS_MARKER}

逐条写明本段的目标量化坐标（引用 skill 中的具体数值 / 区间）：

1. **句长**：目标 mean ≈ ?（skill 区间）· std 是否高方差（长短句交错）
2. **段长**：平均段长 ? 字 · 单段是否 ≤ 上限 · 极短段（独句成段）占比 ?%
3. **单句独行占比**：目标 ?%（非对话段只 1 个句末结束符）
4. **标点密度**：逗号/句号比 ?（长句加逗号节奏）· 拟声独段 ? 处 · 引号独白 ? 处
5. **虚词/功能词节奏**：本段倚重哪些功能词做节奏（的/了/着/而/便/竟 等）· 避开哪些 AI 套话虚词
6. **签名特征落点**：本段命中 skill「作者签名特征」哪 ≥3 条（点名 + 一句话说怎么落到本段场景）

每条 1-2 行，**对准本段具体场景**（不是泛泛复述 skill）。

## 第二段：{COT_BODY_MARKER}

紧接着输出复刻正文，让正文**真实落到上面点名的坐标上**。
正文段从 {COT_BODY_MARKER} 标记后开始 · 纯文本无 markdown · 无章节标题 · 无解释。

⚠️ 必须严格按 {COT_ANALYSIS_MARKER} → {COT_BODY_MARKER} 两段顺序输出 · 不可只写正文跳过分析。
"""


def _l3b_cot_first_mode() -> str:
    """读 env L3B_COT_FIRST_MODE 决定复刻 prompt 是否走 CoT-first 自解释。

    值（大小写不敏感）：
      · off（默认）：旧 prompt 路径——直接出正文、无自解释段。影子纪律：与其他 5 个升级件
        (QUANTILE_BAND/L3A_BURSTINESS/L1B_SIMILARITY/PID_THRESHOLD/SFS_LLM_DEBIAS) 一致，
        默认不改复刻行为；CoT-first 待 gen-model 实跑闭环验证 token 预算(8000 ceiling 下
        分析段与正文共享预算·须确认不挤占截断)后再放量。
      · active：CoT-first 自解释升级开启——prompt 含「先分析后写」两段式指令，
        gen-model 先输出受控量化坐标分析再写正文；落盘正文 strip 掉分析段、meta 留 CoT 痕迹。
      · on/1/true/cot → 归一为 active；空/非法值 → off（保守默认·不静默开启）。

    只改 prompt 构造（确定性可测）· 不改复刻走 gen-model 的事实。
    """
    v = (os.environ.get("L3B_COT_FIRST_MODE") or "").strip().lower()
    if v in ("active", "on", "1", "true", "cot"):
        return "active"
    return "off"  # 默认 off（影子纪律·空/非法值保守退旧路径·CoT-first 待实跑验证后放量）


def strip_cot_analysis(text: str) -> tuple[str, str]:
    """从 gen-model 回复里剥离 CoT 量化坐标分析段，只保留正文供落盘/SFS。

    返回 (正文, cot_analysis_trace)：
      · 命中 COT_BODY_MARKER → 正文 = marker 之后；cot = marker 之前（含分析段，留痕）。
      · 未命中 marker（旧 prompt 路径 / 模型没遵守两段式）→ 正文 = 原文，cot = ""（不误删）。

    设计：CoT 是思考脚手架（CoTeX），不进 SFS 评分 / 不污染复刻产出，但 meta.json 留痕不黑箱。
    宽容解析（防模型把 marker 写成全角括号 / 加序号）：按出现的最后一个 body marker 切分。
    """
    idx = text.rfind(COT_BODY_MARKER)
    if idx < 0:
        return text, ""
    cot = text[:idx].strip()
    body = text[idx + len(COT_BODY_MARKER):].lstrip(" \t\r\n:：")
    return body, cot


# ============ Prompt 模板 ============

REPLICATE_SYSTEM_PROMPT = """你是一位极擅长复刻特定作者风格的写作引擎。

主代理（Claude）已蒸馏了源作者的完整 skill（含 48 维度量化基线 / 反模式 / 黄金段落 / 衔接套路）。
你的任务：严格按 skill 复刻指定颗粒度的文本，用于 SFS（Style Fingerprint Similarity）评分对照。

# 复刻硬约束

1. **量化基线必须命中**（句长 / 段长 / 单句独行占比 / TTR / 标点密度 等）
2. **反模式必须 0 命中**（禁用词 / 禁用过渡 / 禁用对话标签 一律不出现）
3. **签名特征至少命中 3 条**（skill 第 1 节"作者签名特征"中任选 3 条以上落到文中）
4. **黄金段落示例只参考语感，不照抄**（不复刻原文情节 / 角色名 / 专有设定）
5. **不写章节标题**，不加 markdown 标记
6. **直接输出正文**，不要"以下是"等引言，不要解释写作选择

# 创作要求

- 自创角色（不复刻 skill 中提及的任何原文角色名）
- 自创场景（不复刻原文情节）
- 必须有明确的开头 → 中段 → 收笔三拍
- 字数严格按要求（±10% 容忍）
"""


# L3b CoT-first 变体 system prompt：把第 6 条「直接输出正文」改为「先分析后写」两段式。
# 其余硬约束（量化基线 / 反模式 / 签名特征 / 不照抄 / 不写标题）原样保留——只追加自解释纪律。
REPLICATE_SYSTEM_PROMPT_COT = """你是一位极擅长复刻特定作者风格的写作引擎。

主代理（Claude）已蒸馏了源作者的完整 skill（含 48 维度量化基线 / 反模式 / 黄金段落 / 衔接套路）。
你的任务：严格按 skill 复刻指定颗粒度的文本，用于 SFS（Style Fingerprint Similarity）评分对照。

# 复刻硬约束

1. **量化基线必须命中**（句长 / 段长 / 单句独行占比 / TTR / 标点密度 等）
2. **反模式必须 0 命中**（禁用词 / 禁用过渡 / 禁用对话标签 一律不出现）
3. **签名特征至少命中 3 条**（skill 第 1 节"作者签名特征"中任选 3 条以上落到文中）
4. **黄金段落示例只参考语感，不照抄**（不复刻原文情节 / 角色名 / 专有设定）
5. **不写章节标题**，正文段不加 markdown 标记

# 🔬 CoT-first 自解释（本次复刻强制两段式 · 不是直接出正文）

6. **先分析后写**：先逐条点名本段命中 skill 哪几条**受控量化坐标**（句长 / 段长 / 单句独行 / 标点密度 / 虚词），
   再写正文。坐标必须可量化，**严禁**「冷峻 / 华丽」等感性词（感性词改意泄漏内容）。
   两段式输出格式详见 user prompt「先分析后写」节，按 [本段量化坐标分析] → [正文] 顺序输出。

# 创作要求

- 自创角色（不复刻 skill 中提及的任何原文角色名）
- 自创场景（不复刻原文情节）
- 必须有明确的开头 → 中段 → 收笔三拍
- 字数严格按要求（±10% 容忍）
"""


def build_cluster_subcall_prompt(
    style_skill_md: str,
    ref_text: str,
    cluster_meta: dict,
    subcall_index: int,
    subcall_total: int,
    prev_tail: str,
    chapters_in_this_call: int,
    target_words: int,
    cot_first: bool = False,
    seed_section: str = "",
) -> str:
    """cluster 模式的 sub-call prompt（每段都要看见 skill + 上一段尾部 anchor）。

    cot_first=True（L3b · env L3B_COT_FIRST_MODE=active 显式开启）：输出节换成「先分析后写」
    两段式（CoT-first 自解释 + 受控量化坐标）· 直击 cluster 级段长崩塌。
    cot_first=False（=off · 默认）：旧的「直接输出正文」节，零回归（影子纪律·待实跑验证后放量）。

    seed_section（P0 · env SNIPPET_SEED_MODE=on 才非空 · 默认 off）：真实原文「语感种子」段
    （含避坑指令）· 注在 skill 后做语感起手势锚点 · 防长文退化（北极星①·纯 prompt 注入）。
    """
    parts = ["# 源作者风格 skill（必须严格遵循）\n\n" + style_skill_md]
    if seed_section:
        parts.append(seed_section)
    if ref_text:
        parts.append("# 参考原文（仅作语感参考 · 不照抄情节/角色/设定）\n\n" + ref_text[:4000])
    parts.append(
        f"# 故事块复刻任务（第 {subcall_index}/{subcall_total} 段）\n\n"
        f"**颗粒度**：故事块（cluster），整块连续叙事\n"
        f"**cluster 元信息**：{cluster_meta.get('cluster_id', 'unknown')} · "
        f"原 cluster 总章数 {cluster_chapters_count(cluster_meta) or '?'} · "
        f"边界原因 {cluster_meta.get('boundary_reason', '?')}\n"
        f"**本段任务**：写 {chapters_in_this_call} 章份内容（约 {target_words} CJK 字 ±10%）\n"
    )
    if subcall_index > 1 and prev_tail:
        parts.append(
            f"# 上一段尾部（必须自然承接，不复述）\n\n{prev_tail[-800:]}"
        )
    if subcall_index == 1:
        parts.append("**段位置**：cluster 开头 · 自创角色与初始矛盾 · 含 1-2 个早期钩子")
    elif subcall_index == subcall_total:
        parts.append("**段位置**：cluster 收尾 · 推进到本块情节解决/转折 · 章末留 cliffhanger 或情绪余韵")
    else:
        parts.append("**段位置**：cluster 中段 · 推进矛盾 · 至少 1 次场景切换 · 至少 1 个新钩子")

    parts.append(
        "# 衔接要求\n\n"
        "- 整个 cluster N 段拼起来必须是**连贯**叙事（同角色、同场景线、同时间线）\n"
        "- 不分章节标题（splitter 端会处理）\n"
        "- 段内可有自然空行做场景过渡，但不要插入「***」分隔符"
    )
    if cot_first:
        parts.append(COT_FIRST_DIRECTIVE)
    else:
        parts.append("# 输出\n\n直接输出复刻正文（纯文本，无任何 markdown 标记，无章节标题，无解释）。")
    return "\n\n".join(parts)


# ============ gen-model 调用 ============

def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   default_max_tokens: int = 4000,
                   tag: str = "") -> tuple[str, Profile, float]:
    """调当前 active profile；失败按 fallback 链尝试。返回 (text, profile, elapsed_seconds)"""
    from openai import OpenAI

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []

    prefix = f"[{tag}] " if tag else ""

    for i, profile in enumerate(candidates):
        max_tokens = resolve_max_tokens(profile, default=default_max_tokens)
        if i == 0:
            print(f"{prefix}[distill_replicate] 调用 active: {profile.name} ({profile.model})",
                  file=sys.stderr)
            print(f"{prefix}[distill_replicate] max_tokens={max_tokens}, temperature={profile.temperature}",
                  file=sys.stderr)
        else:
            print(f"\n{prefix}[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"{prefix}[distill_replicate] prompt: system={len(system)} chars, user={len(user)} chars",
              file=sys.stderr)

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
        full_text = ""
        t0 = time.time()
        try:
            stream = client.chat.completions.create(
                model=profile.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=profile.temperature,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, 'content', None)
                if piece:
                    full_text += piece
                    sys.stderr.write(piece)
                    sys.stderr.flush()
        except Exception as e:
            reason = str(e)[:200]
            print(f"\n{prefix}[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        elapsed = time.time() - t0
        print(f"\n{prefix}[distill_replicate] 接收完毕 ({len(full_text)} chars, {elapsed:.1f}s) via {profile.name}",
              file=sys.stderr)
        return full_text, profile, elapsed

    raise GenModelExhaustedError(failures)


# ============ cluster 模式辅助 ============

def load_cluster_meta(project_root: Path, cluster_id: str) -> dict:
    """从 cluster_index.json 读指定 cluster 元信息"""
    idx_path = project_root / "cluster_index.json"
    if not idx_path.exists():
        raise FileNotFoundError(f"cluster_index.json 不存在: {idx_path} · 请先跑 cluster_segmenter.py")
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    for c in idx.get("clusters", []):
        if c.get("cluster_id") == cluster_id:
            return c
    raise ValueError(f"cluster_id={cluster_id} 在 cluster_index 中找不到")


def cluster_chapter_bounds(cluster_meta: dict) -> tuple[int | None, int | None]:
    """容忍多 schema 读 cluster 章节起止。

    契约不符根因（2026-05-30 系统验证发现）：cluster_index.json 实际 key 是
    chapter_range（2 元数组 [lo, hi]），但旧码只读 chapter_start/ch_start/chapter_end/ch_end
    → gather_cluster_ref_text 返回空（参考原文没注入·复刻只靠 skill）。

    兼容顺序：
    1. chapter_range: [lo, hi]（cluster_index.json / 事件簇.json 主 schema）
    2. chapter_start / chapter_end（扁平 key）
    3. ch_start / ch_end（简写 key）
    """
    rng = cluster_meta.get("chapter_range")
    if isinstance(rng, (list, tuple)) and len(rng) >= 2:
        try:
            return int(rng[0]), int(rng[1])
        except (TypeError, ValueError):
            pass
    ch_start = cluster_meta.get("chapter_start") or cluster_meta.get("ch_start")
    ch_end = cluster_meta.get("chapter_end") or cluster_meta.get("ch_end")
    if ch_start is None or ch_end is None:
        return None, None
    try:
        return int(ch_start), int(ch_end)
    except (TypeError, ValueError):
        return None, None


def cluster_total_words(cluster_meta: dict) -> int:
    """容忍多 schema 读 cluster 总字数。

    契约不符根因：cluster_index.json 实际 key 是 estimated_words，但旧码只读
    total_words/word_count → estimate_words_per_chapter 永远回退默认 3500。

    兼容顺序：estimated_words → total_words → word_count → words。
    """
    for key in ("estimated_words", "total_words", "word_count", "words"):
        v = cluster_meta.get(key)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


def cluster_chapters_count(cluster_meta: dict) -> int:
    """容忍多 schema 读 cluster 章数（chapters_count 主 key；缺则由 chapter_range 推算）。"""
    n = cluster_meta.get("chapters_count")
    if n:
        try:
            return int(n)
        except (TypeError, ValueError):
            pass
    lo, hi = cluster_chapter_bounds(cluster_meta)
    if lo is not None and hi is not None:
        return hi - lo + 1
    return 0


def gather_cluster_ref_text(project_root: Path, cluster_meta: dict, max_chars: int = 4000) -> str:
    """拼参考原文：cluster 内每章取首段（限总长 4000 字）"""
    ch_start, ch_end = cluster_chapter_bounds(cluster_meta)
    if ch_start is None or ch_end is None:
        return ""
    pieces = []
    total = 0
    for ch in range(int(ch_start), int(ch_end) + 1):
        # 兼容 原文/第NNN章.txt 和 章节/第NNN章/第NNN章.txt 两种布局
        candidates = [
            project_root / "原文" / f"第{ch:03d}章.txt",
            project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt",
        ]
        for cand in candidates:
            if cand.exists():
                t = cand.read_text(encoding="utf-8")
                excerpt = t[:600]
                pieces.append(f"## ch{ch} 首段\n{excerpt}")
                total += len(excerpt)
                break
        if total >= max_chars:
            break
    return "\n\n".join(pieces)[:max_chars]


def plan_cluster_subcalls(chapters_count: int, max_chapters_per_call: int = 3) -> list[int]:
    """把 cluster 拆成 sub-call 列表，每个 sub-call 写 N 章。
    沿用 cluster_segmenter A' 半 cluster 教训：每 call ≤ 3 章 / ≤ 5000 字 / wall-clock ≤ 10 min。
    返回每个 sub-call 写多少章的列表（和为 chapters_count）。
    """
    if chapters_count <= max_chapters_per_call:
        return [chapters_count]  # 单 call
    n_calls = (chapters_count + max_chapters_per_call - 1) // max_chapters_per_call
    base = chapters_count // n_calls
    rem = chapters_count % n_calls
    plan = [base + (1 if i < rem else 0) for i in range(n_calls)]
    return plan


def estimate_words_per_chapter(cluster_meta: dict) -> int:
    """估算每章字数（cluster 总字数 / 章数），默认 3500"""
    total_words = cluster_total_words(cluster_meta)
    n = cluster_chapters_count(cluster_meta)
    if total_words and n:
        return max(2500, min(5000, total_words // n))
    return 3500


# ============ main ============

def main():
    check_deps()
    parser = argparse.ArgumentParser(
        description="蒸馏 phase-4 cluster 终验复刻（v3 cluster 单轨化 · 2026-05-28 · chapter 模式 deprecated · 强制 gen-model）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--style-skill", required=True,
                        help="作者风格 skill .md 路径（v0/v1/v2/...）")
    parser.add_argument("--mode", choices=["cluster"], default="cluster",
                        help="复刻颗粒度（v3 cluster 化方案 2026-05-28 · chapter 模式已删除 · 唯 cluster）")
    parser.add_argument("--output", required=True,
                        help="输出 txt 路径")

    # 2026-05-29 修：删除残留的 chapter 模式参数 --chapter-ref / --target-words
    # （v3 已删 chapter 模式，--mode choices 只剩 cluster，这两个参数无人读取 = 死代码）

    # cluster 模式参数
    parser.add_argument("--cluster-ref",
                        help="[cluster 模式] cluster_id（如 cluster_001 / auto_003）")
    parser.add_argument("--project",
                        help="[cluster 模式] 项目路径（含 cluster_index.json）")
    parser.add_argument("--max-chapters-per-call", type=int, default=3,
                        help="[cluster 模式] 每 sub-call 最多写多少章（防 timeout · 默认 3）")

    # 通用
    parser.add_argument("--profile",
                        help="覆盖 active profile（默认用 .env GEN_MODEL_ACTIVE）")
    parser.add_argument("--cot-first", choices=["active", "off"], default=None,
                        help="[L3b] CoT-first 自解释复刻 prompt：active=先分析后写两段式，"
                             "off=旧直接出正文路径（默认）。缺省读 env L3B_COT_FIRST_MODE（默认 off）。")
    args = parser.parse_args()

    style_skill = Path(args.style_skill)
    if not style_skill.exists():
        print(f"[ERROR] 风格 skill 不存在: {style_skill}", file=sys.stderr)
        sys.exit(2)

    style_skill_md = read_text(style_skill, limit=40000)

    # L3b · CoT-first 模式解析：CLI --cot-first 优先，缺省读 env L3B_COT_FIRST_MODE（默认 off·影子纪律）
    cot_mode = args.cot_first if args.cot_first is not None else _l3b_cot_first_mode()
    cot_first = (cot_mode == "active")
    system_prompt = REPLICATE_SYSTEM_PROMPT_COT if cot_first else REPLICATE_SYSTEM_PROMPT
    print(f"[L3b] CoT-first 自解释复刻 = {cot_mode}"
          f"（{'先分析后写两段式 · 受控量化坐标' if cot_first else '旧直接出正文路径 · 对照'}）",
          file=sys.stderr)

    loader = GenModelLoader()
    if args.profile:
        loader._active_name_override = args.profile  # noqa
    try:
        active = loader.get_active_profile()
        print(f"[distill_replicate] active profile = {active.name} ({active.model})",
              file=sys.stderr)
    except GenModelConfigError as e:
        print(f"[ERROR] gen-model 配置错误: {e}", file=sys.stderr)
        sys.exit(2)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 2026-05-29 修：删除不可达的 `if args.mode == "chapter"` 死分支
    # （--mode choices=["cluster"]，argparse 已在解析阶段拒绝 chapter，此分支永不可达）

    # ========== cluster 模式（v3 唯一形态） ==========
    if not args.cluster_ref or not args.project:
        print("[ERROR] --mode cluster 需要 --cluster-ref + --project", file=sys.stderr)
        sys.exit(2)

    project_root = Path(args.project)
    try:
        cluster_meta = load_cluster_meta(project_root, args.cluster_ref)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] cluster 元信息加载失败: {e}", file=sys.stderr)
        sys.exit(2)

    chapters_count = cluster_chapters_count(cluster_meta)
    if chapters_count <= 0:
        print(f"[ERROR] cluster {args.cluster_ref} 章数无效: {chapters_count}", file=sys.stderr)
        sys.exit(2)

    words_per_chapter = estimate_words_per_chapter(cluster_meta)
    subcall_plan = plan_cluster_subcalls(chapters_count, args.max_chapters_per_call)
    ref_text = gather_cluster_ref_text(project_root, cluster_meta)

    # 🎴 真实原文「语感种子」播种（P0 · env SNIPPET_SEED_MODE 默认 off · 影子纪律 · 不改默认复刻）：
    # 复刻同栈：从 cluster 同源原文池按 ref_text 风格/情绪寄存器选 1-2 段真实片段当语感锚点，
    # 防 cluster 级长文退化（D 级）；带「只借语感起手势 · 绝不抄情节内容」避坑指令。
    _originals_dir = project_root / "原文"
    if not _originals_dir.exists():
        _originals_dir = None
    seed_section, seed_trace = snippet_seed.make_seed_block_from_dir(
        _originals_dir, ref_text=ref_text)
    print(f"[snippet_seed] {seed_trace}", file=sys.stderr)

    print(f"[cluster] {args.cluster_ref} · {chapters_count} 章 · {words_per_chapter} 字/章 估算",
          file=sys.stderr)
    print(f"[cluster] sub-call 计划: {subcall_plan}（共 {len(subcall_plan)} 段）",
          file=sys.stderr)

    full_text_parts = []
    subcall_metas = []
    total_elapsed = 0.0
    prev_tail = ""

    for i, chapters_in_call in enumerate(subcall_plan, start=1):
        target_words_this = chapters_in_call * words_per_chapter
        user = build_cluster_subcall_prompt(
            style_skill_md, ref_text, cluster_meta,
            subcall_index=i, subcall_total=len(subcall_plan),
            prev_tail=prev_tail,
            chapters_in_this_call=chapters_in_call,
            target_words=target_words_this,
            cot_first=cot_first,
            seed_section=seed_section,
        )
        # CoT-first 占额外 token（量化坐标分析段）→ 多留 buffer 防正文被截断
        # 单 call max_tokens 估算：CJK 字按 1.5 tokens/字算（含标点），加 buffer
        token_factor = 2.6 if cot_first else 2.0
        max_tokens_this = min(8000, max(4000, int(target_words_this * token_factor)))
        try:
            reply, used_profile, elapsed = call_gen_model(
                loader, system_prompt, user,
                default_max_tokens=max_tokens_this,
                tag=f"cluster {i}/{len(subcall_plan)}"
            )
        except GenModelExhaustedError as e:
            print(f"\n[ERROR] sub-call {i} 全部 profile 失败:\n{e}", file=sys.stderr)
            # 已成功的段落写到 .partial.txt 防丢
            if full_text_parts:
                partial = "\n\n".join(full_text_parts)
                output_path.with_suffix(".partial.txt").write_text(partial, encoding='utf-8')
                print(f"[recovery] 已写 .partial.txt 保留前 {i-1} 段产出", file=sys.stderr)
            sys.exit(3)

        clean_piece = clean_output(reply)
        # L3b：剥离 CoT 量化坐标分析段——只把正文落盘/送 SFS，分析段留 meta 痕迹（不黑箱）。
        body_piece, cot_trace = strip_cot_analysis(clean_piece)
        if cot_first:
            if cot_trace:
                print(f"[L3b · cluster {i}] CoT 分析段 {cjk_count(cot_trace)} CJK 已剥离落痕 · "
                      f"正文 {cjk_count(body_piece)} CJK", file=sys.stderr)
            else:
                print(f"[L3b · cluster {i}] WARN 模型未按两段式输出（无 [正文] 标记）· "
                      f"全文当正文处理", file=sys.stderr)
        full_text_parts.append(body_piece)
        prev_tail = body_piece
        total_elapsed += elapsed
        subcall_metas.append({
            "subcall_index": i,
            "chapters_in_call": chapters_in_call,
            "target_words": target_words_this,
            "actual_cjk_chars": cjk_count(body_piece),
            "profile_used": used_profile.name,
            "elapsed_seconds": round(elapsed, 1),
            # L3b CoT 痕迹：分析段全文（供复盘核对模型是否真按受控坐标自解释）+ 命中标志
            "cot_first": cot_first,
            "cot_analysis_present": bool(cot_trace),
            "cot_analysis_trace": cot_trace if cot_trace else None,
        })

    full_text = "\n\n".join(full_text_parts)
    output_path.write_text(full_text, encoding='utf-8')

    meta = {
        "mode": "cluster",
        "cluster_id": args.cluster_ref,
        "cluster_meta": {
            "chapters_count": chapters_count,
            "chapter_start": cluster_chapter_bounds(cluster_meta)[0],
            "chapter_end": cluster_chapter_bounds(cluster_meta)[1],
            "boundary_reason": cluster_meta.get("boundary_reason"),
            "total_words_original": cluster_total_words(cluster_meta) or None,
        },
        "style_skill": str(style_skill),
        "project": str(project_root),
        "subcall_plan": subcall_plan,
        "subcalls": subcall_metas,
        "total_target_words": chapters_count * words_per_chapter,
        "total_actual_cjk_chars": cjk_count(full_text),
        "total_elapsed_seconds": round(total_elapsed, 1),
        # L3b CoT-first 自解释痕迹（顶层 · 复盘可见用了哪个 prompt 路径 + 几段真自解释）
        "cot_first_mode": cot_mode,
        "cot_first_enabled": cot_first,
        "cot_analysis_subcalls": sum(1 for m in subcall_metas if m.get("cot_analysis_present")),
        # 🎴 真实原文语感种子播种痕迹（P0 · 默认 off · 留痕不黑箱）
        "snippet_seed": seed_trace,
        "produced_by": "distill_replicate.py v3 · cluster mode · A' 半 cluster timeout 防御 · L3b CoT-first 自解释 · 🎴 snippet-seed 播种",
    }
    output_path.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\n[OK · cluster] {args.cluster_ref}", file=sys.stderr)
    print(f"     输出: {output_path}", file=sys.stderr)
    print(f"     sub-calls: {len(subcall_plan)} 段", file=sys.stderr)
    print(f"     总字数: {cjk_count(full_text)} CJK (target ≈ {chapters_count * words_per_chapter})",
          file=sys.stderr)
    print(f"     总耗时: {total_elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
