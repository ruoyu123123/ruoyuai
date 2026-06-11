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
import snippet_seed  # noqa: E402 · 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量）


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
      · active（默认 / 空 / 非法值 · 2026-05-31 放量）：CoT-first 自解释升级开启——
        prompt 含「先分析后写」两段式指令，gen-model 先输出受控量化坐标分析再写正文；
        落盘正文 strip 掉分析段、meta 留 CoT 痕迹。token 预算阻碍已修（CoT ceiling 上调
        8000→12000 · 分析段额外预算 · 正文保底不被挤截断）。
      · off：旧 prompt 路径——直接出正文、无自解释段（显式关闭做 A/B 对照）。
      · on/1/true/cot → 归一为 active。

    只改 prompt 构造（确定性可测）· 不改复刻走 gen-model 的事实。
    """
    v = (os.environ.get("L3B_COT_FIRST_MODE") or "").strip().lower()
    if v == "off":
        return "off"
    return "active"  # 默认 active（2026-05-31 放量·空/非法值退默认开·只有显式 off 才关）


def strip_cot_analysis(text: str) -> tuple[str, str]:
    """从 gen-model 回复里剥离 CoT 量化坐标分析段，只保留正文供落盘/SFS。

    返回 (正文, cot_analysis_trace)：
      · 命中 COT_BODY_MARKER → 正文 = marker 之后；cot = marker 之前（含分析段，留痕）。
      · 未命中 marker（旧 prompt 路径 / 模型没遵守两段式）→ 正文 = 原文，cot = ""（不误删）。

    设计：CoT 是思考脚手架（CoTeX），不进 SFS 评分 / 不污染复刻产出，但 meta.json 留痕不黑箱。
    宽容解析（防模型把 marker 写成全角括号 / 加序号）：按出现的最后一个 body marker 切分。
    """
    # 容错全角/半角 body marker（gen-model 不稳定输出 [正文] 或 【正文】）
    for bm in (COT_BODY_MARKER, "【正文】"):
        idx = text.rfind(bm)
        if idx >= 0:
            cot = text[:idx].strip()
            body = text[idx + len(bm):].lstrip(" \t\r\n:：")
            return body, cot
    # 无 body marker 但有 analysis marker（全/半角）→ 按其后第一个独行 --- 分隔剥离
    # （refine 轮 gen-model 常写「【本段量化坐标分析】…---…正文」而漏 [正文] marker）
    for am in (COT_ANALYSIS_MARKER, "【本段量化坐标分析】"):
        aidx = text.find(am)
        if aidx >= 0:
            m = re.search(r"\n\s*-{3,}\s*\n", text[aidx:])
            if m:
                split = aidx + m.end()
                return text[split:].lstrip(" \t\r\n:："), text[:split].strip()
    return text, ""


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
    import httpx

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

        # 2026-06-07 修 stream 挂死：pie-xian 代理 reasoning 模型 stream 中途断连时，无 timeout 的
        # `for chunk in stream` 会无限等（实测卡死 43min 不报错不落盘）。设 read=180s → chunk 间隔超时
        # 抛 httpx.ReadTimeout → 下方 except 触发 fallback 链，而非僵死。
        client = OpenAI(
            api_key=profile.api_key, base_url=profile.base_url,
            timeout=httpx.Timeout(connect=15.0, read=180.0, write=15.0, pool=15.0),
            max_retries=2,
        )
        _create_kw = dict(
            model=profile.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=profile.temperature,
            stream=True,
        )
        # reasoning 模型（gemini-3.x pro-preview 等）thinking_level=LOW 回收 thinking 占用的输出预算给正文。
        # 对齐 gen_writer.py（L831-833）· 2026-06-07 修：不传时 thinking 默认 HIGH 吃光预算 →
        # 复刻字数严重偏短（pro 实测 2348 vs 原作 9000）→ 回灌 estimate_cluster_arc 钩子/场景粗估失真归 0。
        if getattr(profile, "thinking_level", None):
            _create_kw["extra_body"] = {"thinking_level": profile.thinking_level}

        # 🔴 轮次7 实测修：中转站瞬时 404/断流时立刻降级 → 撞死 fallback → exit 3。
        # 同 profile 先重试 2 次（指数退避·SDK max_retries 不覆盖 404/断流），耗尽才降级。
        # + gen_throttle.wait() 节流补齐（此前 sub-call 间零间隔·绕过全局限速）。
        full_text = None
        t0 = time.time()
        for attempt in range(3):
            try:
                try:
                    import gen_throttle
                    gen_throttle.wait()
                except ImportError:
                    pass
                buf = ""
                stream = client.chat.completions.create(**_create_kw)
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, 'content', None)
                    if piece:
                        buf += piece
                        sys.stderr.write(piece)
                        sys.stderr.flush()
                full_text = buf
                break
            except Exception as e:
                reason = str(e)[:200]
                if attempt < 2:
                    backoff = 8 * (attempt + 1)
                    print(f"\n{prefix}[RETRY {attempt + 1}/2] {profile.name} 瞬时失败"
                          f"（{backoff}s 后同 profile 重试）: {reason}", file=sys.stderr)
                    time.sleep(backoff)
                    continue
                print(f"\n{prefix}[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
                failures.append((profile.name, reason))
        if full_text is None:
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
        # + 补零(第055章)与不补零(第55章)两种章号命名（与 arc_aggregator/cluster_segmenter 不补零命名对齐）
        candidates = [
            project_root / "原文" / f"第{ch:03d}章.txt",
            project_root / "原文" / f"第{ch}章.txt",
            project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt",
            project_root / "章节" / f"第{ch}章" / f"第{ch}章.txt",
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


def subcall_max_tokens(target_words: int, cot_first: bool) -> int:
    """单 sub-call max_tokens 预算（CJK 字 ~1.5 tokens/字 + buffer）。

    🔴 2026-05-31 修阻碍（CoT-first 放量前置）：CoT-first 的「量化坐标分析段」与正文**共享**
    max_tokens 预算。旧码 cot 用 token_factor 2.6 但被 min(8000) 钳回 8000（与 legacy 同顶）→
    分析段一占，正文被挤截断（cluster 级长草稿尾部 + 收笔常缺）。修法：CoT 模式给分析段
    **额外预算**——ceiling 上调 ×1.5（8000→12000），保证正文落字空间 ≥ legacy。

    不变式（测试钉死）：相同 target_words 下 CoT 预算 ≥ legacy 预算（正文不被分析段挤截断）。
    """
    if cot_first:
        token_factor, ceiling = 2.6, 12000   # 分析段额外预算
    else:
        token_factor, ceiling = 2.0, 8000
    return min(ceiling, max(4000, int(target_words * token_factor)))


# ============ L3d · draft-level critic-refine + knockout（PerFine 式 · 2026-05-31）============
#
# 根因（L3b 自认 · memory reference-system-validation-method）：复刻是「得分→盲改 skill→再测」
# 乏力循环——**没有 draft 改稿、没有保最优**。一旦草稿出来就定稿，差距只能靠下一轮蒸 skill 修。
#
# 升级（PerFine · arxiv 2510.24469 · GEval +7-13% · 3-5 轮稳）：复刻出稿后——
#   ① critic LLM（同 gen-model profile）按 **tone / 词汇 / 句式 / topicality** 四类
#      出**结构化 feedback**，**直接改当前草稿**（draft-level · 不改 skill）；
#   ② 按 feedback 重写草稿；
#   ③ SFS 分（style_evaluator.evaluate）当**裁判**——透明、确定性、advisory（不进 hard_gate）；
#   ④ **knockout**：跨轮保留 SFS 更高分的草稿（refine 退化也不丢最优稿）；
#   循环 3-5 轮。
#
# 北极星纪律：
#   · critic 出的是 advisory feedback（不黑箱 · 落 meta.json 留痕 · 顾问非法官）；
#   · SFS 当裁判透明（确定性分数 · 不动 sfs_quick/grade 判决逻辑 · 不进 hard_gate）；
#   · critic 复用 active gen-model profile（复刻仍走 gen-model · 同栈）；
#   · critic prompt 引用作者**数值契约表**条目（顺带吸收 per-author rubric 精华 · 受控量化坐标）；
#   · 默认 active（用户：默认关闭写他干什么）· env DRAFT_REFINE_MODE=off 可关。

# critic 评的四类维度（PerFine rubric · 映射作者数值契约表 + 受控语言学坐标）
DRAFT_CRITIC_DIMENSIONS = ["tone", "vocabulary", "syntax", "topicality"]


def _draft_refine_mode() -> str:
    """读 env DRAFT_REFINE_MODE 决定复刻出稿后是否走 critic-refine + knockout。

    值（大小写不敏感）：
      · active（默认 / 空 / 非法值）：开启 draft-level critic-refine + knockout——
        草稿出来后 critic 出结构化 feedback 直接改稿 · SFS 当裁判 · knockout 保最优（3-5 轮）。
        （用户纪律：默认关闭写他干什么 · 默认全开真生效。）
      · off：关闭——复刻一次出稿即定稿（旧行为 · A/B 对照）。
      · on/1/true/refine → 归一为 active。

    只改「出稿后是否多轮精修 + 保最优」（确定性编排 · gen-model/SFS 调用可 mock 测试）·
    不改复刻走 gen-model 的事实 · 不动 SFS 判决逻辑。
    """
    v = (os.environ.get("DRAFT_REFINE_MODE") or "").strip().lower()
    if v == "off":
        return "off"
    return "active"


def _draft_refine_rounds() -> int:
    """读 env DRAFT_REFINE_ROUNDS（精修轮数 · PerFine 实证 3-5 轮稳 · 默认 3 · 钳 [1,5]）。"""
    raw = (os.environ.get("DRAFT_REFINE_ROUNDS") or "").strip()
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 3
    return max(1, min(5, n))


def _extract_contract_excerpt(style_skill_md: str, max_chars: int = 1400) -> str:
    """从 skill_FINAL.md 抽「数值契约表」块给 critic 当受控量化坐标 rubric（不另算 · 复用 L3c 注入块）。

    宽容解析：优先用 L3c 哨兵块（SENTINEL_BEGIN/END）；缺哨兵则抓「数值契约表」标题后片段；
    都没有则回退用 skill 头部（critic 仍能看见量化基线）。critic 据此点名 tone/词汇/句式/topicality
    对照——把 per-author rubric 精华（作者档第一权威）吸收进 critic 评分坐标。
    """
    begin = "L3C_CONTRACT_BEGIN"
    end = "L3C_CONTRACT_END"
    bi = style_skill_md.find(begin)
    ei = style_skill_md.find(end)
    if bi >= 0 and ei > bi:
        block = style_skill_md[bi:ei]
        return block[:max_chars]
    # 无哨兵：抓「数值契约表」等**标题**（必须是 markdown heading `#`/`##`，避免误中 frontmatter
    # description 里的「微调量化基线」这类一带而过的提及）。
    for title in ("数值契约表", "量化基线", "量化约束", "定量基线"):
        ti = style_skill_md.find(title)
        # 校验该位置确实是标题：往前找最近的换行，行首应是 # / ## / **
        if ti >= 0:
            line_start = style_skill_md.rfind("\n", 0, ti) + 1
            prefix = style_skill_md[line_start:ti].lstrip()
            if prefix.startswith("#") or prefix.startswith("**") or prefix == "":
                return style_skill_md[ti:ti + max_chars]
    # 无真标题：锚到第一个受控坐标关键词处取窗口（老 skill 量化基线散在正文 · 头部常是 frontmatter）。
    # 取最早出现的坐标，从其前 100 字开始截窗，保证 critic 看得见真实量化基线（不是 frontmatter 提及）。
    coord_positions = [style_skill_md.find(c) for c in ("句长", "段长", "单句独行", "标点", "虚词")]
    coord_positions = [p for p in coord_positions if p >= 0]
    if coord_positions:
        start = max(0, min(coord_positions) - 100)
        return style_skill_md[start:start + max_chars]
    # 都没有：回退 skill 头部（critic 仍看得见 skill 全貌）
    return style_skill_md[:max_chars]


def build_critic_prompt(draft_text: str, contract_excerpt: str,
                        ref_text: str = "") -> str:
    """critic LLM prompt：按 tone/词汇/句式/topicality 四类对当前草稿出**结构化 feedback**。

    PerFine 式：critic 不重写草稿，只产可执行的修改清单（每条点名维度 + 具体位置 + 怎么改），
    引用作者数值契约表条目当受控坐标（不准用「冷峻/华丽」感性词 · 感性词改意泄漏内容）。
    feedback 是 advisory（refine 步据此改稿 · 但 SFS 裁判才是判决）。
    """
    parts = [
        "你是一位严苛的中文小说**风格审稿人**（critic）。"
        "下面是一段「复刻草稿」+ 源作者的「数值契约表」。\n"
        "你的任务：按 4 类维度逐条诊断草稿**偏离作者风格**的地方，"
        "出**结构化、可直接执行的修改清单**（你不重写正文，只出清单）。",
        "# 源作者数值契约表（受控量化坐标 · 第一权威）\n\n" + contract_excerpt,
    ]
    if ref_text:
        parts.append("# 参考原文片段（仅作语感对照 · 不比情节）\n\n" + ref_text[:1500])
    parts.append("# 待诊断的复刻草稿\n\n" + draft_text[:8000])
    parts.append(
        "# 诊断维度（逐类给条目 · 每条点名「位置 + 偏离什么坐标 + 怎么改」）\n\n"
        "1. **tone（语气/情绪寄存器）**：草稿整体语气是否贴作者？情绪起伏节奏对不对？\n"
        "2. **vocabulary（词汇/虚词指纹）**：高频虚词分布是否对齐契约表 Top-N？"
        "有无 AI 套话 / 禁用词？用词丰富度够不够？\n"
        "3. **syntax（句式/段落节奏）**：句长均值+方差是否对齐契约表？"
        "单句独行占比、段长分位数对不对？长短句混搭够不够？\n"
        "4. **topicality（题材/具象度）**：场景具象、细节质感是否到位？有无空泛抽象？\n\n"
        "⚠️ 只准引用**可量化坐标**（句长/段长/单句独行/标点/虚词频率），"
        "**严禁**用「冷峻/华丽/大气/有张力」等感性形容词（无法核对 · 会泄漏改意）。\n"
        "⚠️ 每条修改清单要**具体到本草稿的段落/句子**，不要泛泛复述契约表。"
    )
    parts.append(
        "# 输出格式（严格按此 · 供下一步据此改稿）\n\n"
        "[tone]\n- <条目1>\n- <条目2>\n"
        "[vocabulary]\n- <条目1>\n...\n"
        "[syntax]\n- <条目1>\n...\n"
        "[topicality]\n- <条目1>\n...\n\n"
        "若某维度已贴合作者、无需改，该维度下写「- 已对齐」。"
    )
    return "\n\n".join(parts)


def build_refine_prompt(draft_text: str, critic_feedback: str,
                        contract_excerpt: str) -> str:
    """据 critic feedback **改写当前草稿**（draft-level · 非改 skill）。

    PerFine 式 refine：保持情节/角色/篇幅基本不变，只按修改清单调风格坐标 → 产改良稿。
    强调「只改风格不改故事骨架」（防 refine 把内容写飞 · 也防退化——退化由 knockout 兜底）。
    """
    return "\n\n".join([
        "你是一位极擅长按审稿意见**精修风格**的中文小说写作引擎。"
        "下面给你一段复刻草稿 + critic 的结构化修改清单 + 作者数值契约表。\n"
        "你的任务：**严格按修改清单改写草稿**，让它更贴作者风格坐标。",
        "# 改写硬纪律\n\n"
        "1. **只改风格，不改故事骨架**：角色、场景、情节走向、篇幅基本不变（±10% 字数）。\n"
        "2. **逐条落实修改清单**：每条 critic 意见都要在改写稿里有对应调整。\n"
        "3. **对齐数值契约表**：句长均值+方差 / 段长分位数 / 单句独行占比 / 虚词 Top-N。\n"
        "4. **0 AI 套话 / 0 禁用词**。\n"
        "5. **直接输出改写后的完整正文**（纯文本 · 无 markdown · 无章节标题 · 无解释 · 无「以下是」）。",
        "# 源作者数值契约表\n\n" + contract_excerpt,
        "# critic 修改清单（逐条落实）\n\n" + critic_feedback,
        "# 待改写的草稿\n\n" + draft_text,
    ])


def _score_draft_sfs(ref_texts: list[str], draft_text: str) -> float | None:
    """SFS 裁判（透明 · 确定性 · advisory）：调 style_evaluator.evaluate 取 sfs_quick。

    ref_texts 空 / 评估异常 → 返回 None（裁判不可用 → 调用方降级为「不淘汰、保首稿」· 不崩流程）。
    绝不改 style_evaluator 任何判决逻辑（顾问非法官 · 北极星⑤）· 不进 hard_gate。
    """
    if not ref_texts:
        return None
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import style_evaluator  # noqa: E402 · 延迟导入（避免无 numpy/scipy 环境时影响纯 prompt 测试）
        ref_arg = ref_texts if len(ref_texts) > 1 else ref_texts[0]
        report = style_evaluator.evaluate(ref_arg, draft_text)
        v = report.get("sfs_quick")
        return float(v) if v is not None else None
    except Exception as e:  # noqa: BLE001 · 裁判失败降级不崩复刻主流程
        print(f"[L3d · SFS 裁判] WARN 评分失败，降级保留当前最优稿: {str(e)[:160]}",
              file=sys.stderr)
        return None


def draft_refine_loop(
    loader: "GenModelLoader",
    system_prompt: str,
    initial_draft: str,
    style_skill_md: str,
    ref_texts: list[str],
    ref_excerpt: str = "",
    rounds: int | None = None,
    score_fn=None,
    call_fn=None,
) -> tuple[str, dict]:
    """draft-level critic-refine + knockout 主循环（PerFine 式 · 3-5 轮 · 保最优）。

    流程（每轮）：critic LLM 出结构化 feedback → refine LLM 据此改稿 → SFS 裁判评分 →
    **knockout 保 SFS 更高分的草稿**（候选退化则丢弃 · 保留当前 best）。

    依赖注入（测试用 · 不实跑 gen-model / SFS）：
      · call_fn(system, user, tag) -> str：替代 call_gen_model（critic + refine 两次 LLM 调用）。
      · score_fn(ref_texts, draft) -> float|None：替代 _score_draft_sfs（SFS 裁判）。
    生产默认：call_fn=call_gen_model 包装、score_fn=_score_draft_sfs。

    返回 (best_draft, trace)：trace 留每轮 critic feedback + 候选 SFS + knockout 决策（不黑箱）。
    """
    if rounds is None:
        rounds = _draft_refine_rounds()
    if call_fn is None:
        def call_fn(system, user, tag=""):
            reply, _prof, _el = call_gen_model(
                loader, system, user,
                default_max_tokens=8000, tag=tag)
            return reply
    if score_fn is None:
        score_fn = _score_draft_sfs

    contract_excerpt = _extract_contract_excerpt(style_skill_md)
    ref_for_critic = ref_texts[0] if ref_texts else ""

    best_draft = initial_draft
    best_score = score_fn(ref_texts, initial_draft)
    initial_score = best_score  # 首稿评分锚（不依赖任一轮成功 · 防 critic 首轮即失败缺键）
    rounds_trace: list[dict] = []

    for r in range(1, rounds + 1):
        # ① critic：对当前 best 草稿出结构化 feedback（advisory）
        critic_user = build_critic_prompt(best_draft, contract_excerpt, ref_for_critic)
        try:
            critic_feedback = clean_output(
                call_fn("你是严苛的中文小说风格审稿人，只出结构化修改清单，不重写正文。",
                        critic_user, tag=f"critic r{r}"))
        except GenModelExhaustedError as e:
            print(f"[L3d · critic r{r}] WARN critic 调用失败，提前结束精修循环: {e}",
                  file=sys.stderr)
            rounds_trace.append({"round": r, "stage": "critic", "error": str(e)[:160]})
            break

        # ② refine：据 feedback 改写草稿
        refine_user = build_refine_prompt(best_draft, critic_feedback, contract_excerpt)
        try:
            candidate = clean_output(
                call_fn(system_prompt, refine_user, tag=f"refine r{r}"))
            candidate, _cot = strip_cot_analysis(candidate)  # 兼容模型误带 CoT 标记
        except GenModelExhaustedError as e:
            print(f"[L3d · refine r{r}] WARN refine 调用失败，保留当前最优稿: {e}",
                  file=sys.stderr)
            rounds_trace.append({"round": r, "stage": "refine", "error": str(e)[:160]})
            break

        # ③ SFS 裁判：给候选评分
        cand_score = score_fn(ref_texts, candidate)

        # ④ knockout：候选分更高（或裁判不可用时不淘汰、首次接受候选）才替换 best
        accepted = _knockout_accept(best_score, cand_score)
        round_meta = {
            "round": r,
            "critic_feedback": critic_feedback,
            "candidate_cjk": cjk_count(candidate),
            "best_score_before": best_score,
            "candidate_score": cand_score,
            "accepted": accepted,
        }
        if accepted:
            best_draft = candidate
            # 裁判可用则更新 best_score；裁判不可用（None）时保持原 best_score（不污染后续比较）
            if cand_score is not None:
                best_score = cand_score
        round_meta["best_score_after"] = best_score
        rounds_trace.append(round_meta)
        print(f"[L3d · r{r}] critic→refine 完成 · 候选 SFS={cand_score} · "
              f"best={best_score} · {'采纳' if accepted else '淘汰(保最优)'}",
              file=sys.stderr)

    # initial_score = 首稿评分（记在 best_score_init · 不依赖第一轮成功，防 critic 首轮即失败时缺键）
    trace = {
        "draft_refine_enabled": True,
        "rounds_requested": rounds,
        "rounds_run": len(rounds_trace),
        "initial_score": initial_score,
        "final_best_score": best_score,
        "rounds": rounds_trace,
        "sfs_judge_available": best_score is not None
        or any(rt.get("candidate_score") is not None for rt in rounds_trace),
        "note": "PerFine 式 draft-level critic-refine + knockout · critic feedback=advisory(留痕不黑箱) · "
                "SFS 当裁判透明(确定性·不进 hard_gate) · 复刻仍走 gen-model",
    }
    return best_draft, trace


def _knockout_accept(best_score: float | None, cand_score: float | None) -> bool:
    """knockout 采纳判定（保最优纪律）：

    · 两者都有分 → 候选 ≥ best 才采纳（refine 退化丢弃 · 严格保最优）。
    · 候选无分（裁判对候选失败）、best 有分 → 不采纳（不拿没裁判背书的候选换掉有分 best）。
    · best 无分（首稿裁判失败）、候选有分 → 采纳（候选首次拿到裁判分）。
    · 两者都无分（裁判全程不可用）→ 采纳候选（降级行为：至少吃到 refine 的改稿；保守可改 False）。

    透明且确定性——纯函数，测试钉死 4 种组合。
    """
    if best_score is not None and cand_score is not None:
        return cand_score >= best_score
    if cand_score is None and best_score is not None:
        return False
    if best_score is None and cand_score is not None:
        return True
    return True  # 都无分：裁判不可用，吃 refine 改稿


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
    parser.add_argument("--draft-refine", choices=["active", "off"], default=None,
                        help="[L3d] draft-level critic-refine + knockout：active=出稿后 critic 出"
                             "结构化 feedback 直接改稿 · SFS 当裁判 · 跨轮保最优（3-5 轮 · 默认 active）；"
                             "off=一次出稿即定稿。缺省读 env DRAFT_REFINE_MODE（默认 active）。")
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

    # 2026-06-07 适配：reasoning 模型（profile 设 thinking_level）自带内部思考，外加 CoT-first
    # 「先分析后写」两段式会让它输出「量化坐标分析」元前言（不遵守 COT_BODY_MARKER → strip_cot_analysis
    # 剥不掉 → 泄漏进正文）+ 挤占正文 token 预算（pro-preview 实证：泄漏+字数崩 1895/18000）。
    # 故 reasoning 模型自动关 CoT-first，除非用户显式 --cot-first active。
    if cot_first and args.cot_first is None and getattr(active, "thinking_level", None):
        cot_first = False
        cot_mode = f"off(reasoning-auto·thinking_level={active.thinking_level})"
        system_prompt = REPLICATE_SYSTEM_PROMPT
        print(f"[L3b] 检测到 reasoning 模型({active.model})·自动关 CoT-first "
              f"→ {cot_mode}（防元前言泄漏+正文预算被挤·2026-06-07 适配）", file=sys.stderr)

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

    # 🎴 真实原文「语感种子」播种（P0 · env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量 · 真生效）：
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
        # max_tokens 预算（CoT-first ceiling 上调防分析段挤占正文 · 详见 subcall_max_tokens）
        max_tokens_this = subcall_max_tokens(target_words_this, cot_first)
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

    # ========== L3d · draft-level critic-refine + knockout（PerFine 式 · 默认 active） ==========
    # 草稿拼好后：critic 出结构化 feedback 直接改稿 → SFS 裁判评分 → knockout 保最优（3-5 轮）。
    # critic + refine 复用同 active gen-model profile（复刻仍走 gen-model）· SFS 当裁判透明。
    refine_mode = args.draft_refine if args.draft_refine is not None else _draft_refine_mode()
    # 2026-06-07 适配：reasoning 模型（thinking_level 非空）的 critic→refine 轮会把正文越改越短
    # （pro-preview 实证：初稿 ~4700 CJK → refine 3 轮砍到 1737；且其 SFS 在 refine 内算 None 致
    # knockout 无法择优、退化保最后一轮=最短）。故 reasoning 模型自动关 draft-refine，
    # 除非用户显式 --draft-refine active。
    if refine_mode == "active" and args.draft_refine is None and getattr(active, "thinking_level", None):
        refine_mode = "off"
        print(f"[L3d] 检测到 reasoning 模型({active.model})·自动关 draft-refine"
              f"（refine 轮缩写正文 + SFS None 致 knockout 失效·2026-06-07 适配）", file=sys.stderr)
    refine_trace: dict = {"draft_refine_enabled": False, "mode": refine_mode}
    if refine_mode == "active":
        # SFS 裁判用同 cluster 的真实原文当 ref（gather 的同源 ref_text 拆段；空则裁判降级保稿）
        ref_for_score = [ref_text] if ref_text else []
        print(f"[L3d] draft-refine = active · {_draft_refine_rounds()} 轮 critic→refine→SFS knockout",
              file=sys.stderr)
        refined_text, loop_trace = draft_refine_loop(
            loader, system_prompt,
            initial_draft=full_text,
            style_skill_md=style_skill_md,
            ref_texts=ref_for_score,
        )
        full_text = refined_text
        refine_trace = {"draft_refine_enabled": True, "mode": refine_mode, **loop_trace}
    else:
        print("[L3d] draft-refine = off · 一次出稿即定稿（对照）", file=sys.stderr)

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
        # L3d · draft-level critic-refine + knockout 痕迹（PerFine 式 · critic feedback + 每轮 SFS · 不黑箱）
        "draft_refine": refine_trace,
        "produced_by": "distill_replicate.py v3 · cluster mode · A' 半 cluster timeout 防御 · "
                       "L3b CoT-first 自解释 · 🎴 snippet-seed 播种 · L3d draft critic-refine+knockout",
    }
    output_path.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\n[OK · cluster] {args.cluster_ref}", file=sys.stderr)
    print(f"     输出: {output_path}", file=sys.stderr)
    print(f"     sub-calls: {len(subcall_plan)} 段", file=sys.stderr)
    print(f"     总字数: {cjk_count(full_text)} CJK (target ≈ {chapters_count * words_per_chapter})",
          file=sys.stderr)
    if refine_trace.get("draft_refine_enabled"):
        print(f"     draft-refine: {refine_trace.get('rounds_run')} 轮 · "
              f"SFS {refine_trace.get('initial_score')} → {refine_trace.get('final_best_score')}"
              f"（knockout 保最优）", file=sys.stderr)
    print(f"     总耗时: {total_elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
