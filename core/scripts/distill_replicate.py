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


def build_cluster_subcall_prompt(
    style_skill_md: str,
    ref_text: str,
    cluster_meta: dict,
    subcall_index: int,
    subcall_total: int,
    prev_tail: str,
    chapters_in_this_call: int,
    target_words: int,
) -> str:
    """cluster 模式的 sub-call prompt（每段都要看见 skill + 上一段尾部 anchor）"""
    parts = ["# 源作者风格 skill（必须严格遵循）\n\n" + style_skill_md]
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
    args = parser.parse_args()

    style_skill = Path(args.style_skill)
    if not style_skill.exists():
        print(f"[ERROR] 风格 skill 不存在: {style_skill}", file=sys.stderr)
        sys.exit(2)

    style_skill_md = read_text(style_skill, limit=40000)

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
        )
        # 单 call max_tokens 估算：CJK 字按 1.5 tokens/字算（含标点），加 buffer
        max_tokens_this = min(8000, max(4000, int(target_words_this * 2.0)))
        try:
            reply, used_profile, elapsed = call_gen_model(
                loader, REPLICATE_SYSTEM_PROMPT, user,
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
        full_text_parts.append(clean_piece)
        prev_tail = clean_piece
        total_elapsed += elapsed
        subcall_metas.append({
            "subcall_index": i,
            "chapters_in_call": chapters_in_call,
            "target_words": target_words_this,
            "actual_cjk_chars": cjk_count(clean_piece),
            "profile_used": used_profile.name,
            "elapsed_seconds": round(elapsed, 1),
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
        "produced_by": "distill_replicate.py v3 · cluster mode · A' 半 cluster timeout 防御",  # 2026-05-29 修：v2→v3 对齐文件头
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
