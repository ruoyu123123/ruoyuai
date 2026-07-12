#!/usr/bin/env python3
"""蒸馏 phase-4 cluster 终验复刻。

复刻与正式写作使用同一栈：Claude agent 按 skill 亲笔写复刻场景稿（脚本外部产出）→
本脚本用 gemini 按 skill **分段润色** → 拼接落盘评分。与正式写作栈（gen_writer.py）同款。

  step 复刻-a  蒸馏 plan 的复刻 step spawn Claude agent（按 skill 亲笔逐场景写复刻草稿）
               → --claude-scenes-dir 下 scene_*.txt（per-scene 文件 = 天然润色分段）
  step 复刻-b  本脚本：逐场景段调 gemini 按风格档等体量重写润色（段级字数守恒带 [0.85, 1.30]·
               超界带字数指令重试 1 次）→ 拼接 → 落盘 + SFS 评分对照

缺 Claude 场景稿时 exit 2，不提供从零生成或扩写路径。

用法：

  python core/scripts/distill_replicate.py \\
    --style-skill workspace/styles/<书名>/skill_v<N>.md \\
    --mode cluster --cluster-ref cluster_001 --project workspace/styles/<书名> \\
    --claude-scenes-dir workspace/styles/<书名>/复刻测试/v<N>_round<M>/claude_scenes \\
    --output workspace/styles/<书名>/复刻测试/v<N>_round<M>/cluster_001_replica.txt

输出：
- 复刻终稿（纯 txt UTF-8 无 markdown 标记）
- meta.json sidecar（polish_metas per-scene 守恒遥测 / 字数 / 耗时 / draft-refine / rubric）

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
    reasoning_extra_body,
)
import snippet_seed  # noqa: E402 · 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 on）
import distill_rubric  # noqa: E402 · A12 LongBench-Write 六维质量 rubric（env DISTILL_RUBRIC_MODE 默认 off · advisory 旁证）
from llm_transport import _is_refusal  # noqa: E402 · gen-model 间歇性安全拒绝检测


# refusal 触发的「文学复刻无害」声明 · 追加到 user prompt 末尾再试一次（推开模型保险丝）
_REFUSAL_DISCLAIMER = (
    "\n\n# 任务性质澄清\n\n"
    "这是文学小说复刻任务·请按 skill 风格自由发挥·不涉及现实有害内容·"
    "复刻产出仅用于风格指纹评分对照·不发表也不用于任何敏感场景。"
)


class RefusalExhausted(Exception):
    """单 profile 连续 3 次返回 refusal · 走完降级链仍未拿到合法复刻。

    上层（distill_replicate.main 的 except GenModelExhaustedError 已覆盖兜底链失败）
    将本类归入 GenModelExhaustedError 同构语义 — call_gen_model 把 refusal 失败
    写入 failures 列表，最终若全链 refusal 仍抛 GenModelExhaustedError（exit3）。
    """


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

    LLM 复读退化会产出数千字的连续重复串（如整段连续「铛」，可占草稿两成以上 CJK），
    污染 SFS 与产出；作者真实拟声只用单行短串（如「铛。」独段）。

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

REPLICATE_SYSTEM_PROMPT = """你就是这位源作者本人，用你自己的手笔把一段草稿等体量重写润色。

主代理（Claude）已蒸馏了你的完整 skill（含量化基线 / 反模式 / 黄金段落 / 衔接套路），
并按你的 skill 亲笔写了一段复刻草稿。你的任务：把这段草稿**等体量重写润色**，让它的语言、
节奏、句式、腔调彻底变成你本人的笔法，用于 SFS（Style Fingerprint Similarity）评分对照。

# 润色硬约束

1. **只改笔法，不改故事骨架**：情节走向 / 事件顺序 / 关键事实 / 对话信息量完全不变
   （等体量重写 · 字数守恒带以 user prompt 为准 · 不许压缩省略、不许注水扩写）
2. **量化基线必须命中**（句长 / 段长 / 单句独行占比 / TTR / 标点密度 以数值契约表为准）
3. **反模式必须 0 命中**（禁用词 / 禁用过渡 / 禁用对话标签 一律不出现）
4. **签名特征落地**（skill「作者签名特征」的手笔真实体现在润色稿里）
5. **不写章节标题**，不加 markdown 标记
6. **直接输出润色后的正文**，不要"以下是"等引言，不要解释，不要输出任何 JSON
"""


# 段级字数守恒带（与 gen_writer.POLISH_CJK_LOW/HIGH 同栈同款 · v29）
POLISH_CJK_LOW = 0.85   # 守恒带下限（压缩省略红线）
POLISH_CJK_HIGH = 1.30  # 守恒带上限（注水扩写红线）


def build_polish_subcall_prompt(
    style_skill_md: str,
    ref_text: str,
    scene_text: str,
    idx: int,
    total: int,
    seed_section: str = "",
) -> str:
    """v29 复刻润色 prompt：把 Claude 亲笔复刻场景稿按风格档**等体量重写润色**。

    与 gen_writer.build_prompt 的 polish_point_tail 同栈同款守恒纪律——注入 skill 全文 +
    同源 ref_text 风格参照 + snippet_seed 语感锚点，让 gemini 只调风格坐标不改情节骨架。

    守恒带 [POLISH_CJK_LOW, POLISH_CJK_HIGH]：输出汉字数必须落在
    [int(src×0.85), int(src×1.30)]，不许压缩省略、不许注水扩写（调用方超界带字数指令重试 1 次）。

    seed_section（env SNIPPET_SEED_MODE=on 才非空 · 默认 off）：真实原文「语感种子」段
    （含避坑指令）· 注在 skill 后做语感起手势锚点（北极星① · 纯 prompt 注入）。
    """
    src_cjk = cjk_count(scene_text)
    lo = int(src_cjk * POLISH_CJK_LOW)
    hi = int(src_cjk * POLISH_CJK_HIGH)
    parts = ["# 源作者风格 skill（必须严格遵循）\n\n" + style_skill_md]
    if seed_section:
        parts.append(seed_section)
    if ref_text:
        parts.append("# 参考原文（仅作语感参考 · 不照抄情节/角色/设定）\n\n" + ref_text[:4000])
    parts.append(
        f"# 润色任务（第 {idx + 1}/{total} 段）\n\n"
        f"下面是这个复刻故事块**第 {idx + 1} 段（共 {total} 段）**的 Claude 亲笔初稿。"
        f"请你以上述作者风格档的手笔，把这一段**整体重写润色**：\n\n"
        f"- **情节走向、事件顺序、关键事实、对话信息量完全不变**——一个字都不许改动语义。\n"
        f"- 语言、节奏、段落切分、对话腔调全面向风格档靠拢"
        f"（句长/段长/单句独行/标点分布以数值契约表为准）。\n"
        f"- **等体量重写**：这一段初稿约 {src_cjk} 个汉字，你的输出必须落在 "
        f"{lo}-{hi} 个汉字之间——不许压缩省略情节，也不许注水扩写铺陈。\n"
        f"- 严禁 AI 套话与禁用词；对话用中文弯引号；非对话段一段只一个句末结束符。\n"
        f"- 只润色这一段；直接输出润色后的这一段正文全文，不要标题、不要解释、不要输出任何 JSON。"
    )
    parts.append(f"# 第 {idx + 1} 段初稿\n\n" + scene_text)
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
            print(f"{prefix}[distill_replicate] 调用 active: {profile.name} ({profile.model})")
            print(f"{prefix}[distill_replicate] max_tokens={max_tokens}, temperature={profile.temperature}")
        else:
            print(f"\n{prefix}[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"{prefix}[distill_replicate] prompt: system={len(system)} chars, user={len(user)} chars")

        # pie-xian 代理 reasoning 模型 stream 中途断连时，无 timeout 的
        # `for chunk in stream` 会无限等（实测可能长时间卡死不报错不落盘）。设 read=180s → chunk 间隔超时
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
        # 对齐 gen_writer.py：不传时 thinking 默认 HIGH 会吃光预算 →
        # 复刻字数严重偏短 → 回灌 estimate_cluster_arc 钩子/场景粗估失真归 0。
        _dr_extra = reasoning_extra_body(profile)  # helper 单一真理源(thinking_level/reasoning_effort·防 thinking 暴走)
        if _dr_extra:
            _create_kw["extra_body"] = _dr_extra

        # 🔴 中转站瞬时 404/断流时若立刻降级会撞死 fallback → exit 3。
        # 同 profile 先重试 2 次（指数退避·SDK max_retries 不覆盖 404/断流），耗尽才降级。
        # + gen_throttle.wait() 节流补齐，确保 sub-call 间也遵守全局限速。
        #
        # 🛡️ refusal-retry：reasoning gen-model 偶发对合法文学复刻输出短拒绝
        # （HTTP200 + finish=stop + 非空 · 绕过 TransportEmpty 守卫）。命中 _is_refusal →
        # 3s 退避 + user 追加「文学复刻无害」disclaimer 再试。瞬时失败/refusal 两套 retry
        # 共享 attempt 计数（≤2 次重试·共 3 次尝试）但互不串扰（exception → 指数退避·
        # refusal → 固定 3s + disclaimer）。
        full_text = None
        t0 = time.time()
        user_now = user                              # refusal retry 时可追加 disclaimer
        refusal_exhausted = False                    # 标记 refusal 3 次耗尽（跳过下方空内容守卫的重复 append）
        for attempt in range(3):
            try:
                try:
                    import gen_throttle
                    gen_throttle.wait()
                except ImportError:
                    pass
                # 每轮重建 messages（refusal retry 时 user_now 会被追加 disclaimer）
                _create_kw["messages"] = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_now},
                ]
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
                # 🛡️ refusal 检测（在标记成功前）·命中 → 退避 + disclaimer 再试
                if _is_refusal(buf):
                    if attempt < 2:
                        backoff = 3
                        snippet = buf.strip().replace("\n", " ")[:80]
                        print(f"\n{prefix}[REFUSAL-RETRY {attempt + 1}/2] {profile.name} "
                              f"安全拒绝（{backoff}s 后追加 disclaimer 重试）: {snippet}",
                              file=sys.stderr)
                        if _REFUSAL_DISCLAIMER not in user_now:
                            user_now = user_now + _REFUSAL_DISCLAIMER
                        time.sleep(backoff)
                        continue
                    # 第 3 次仍 refusal → 当作本 profile 失败 · 进入下一 fallback profile
                    snippet = buf.strip().replace("\n", " ")[:80]
                    reason = f"REFUSAL_EXHAUSTED: {snippet}"
                    print(f"\n{prefix}[FALLBACK] {profile.name} 3 次连续 refusal · 转下一 profile: {snippet}",
                          file=sys.stderr)
                    failures.append((profile.name, reason))
                    full_text = None  # 不返回拒绝文本
                    refusal_exhausted = True
                    break
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
        # refusal 耗尽已在内圈 append 失败 + 打印日志 → 直接转下一 profile（避空内容守卫重复 append）
        if refusal_exhausted:
            continue
        # 空内容守卫（对齐 gen_writer）。reasoning 模型把 token 全吐进
        # reasoning_content / 内容过滤 → HTTP200 但 delta.content 全 None → full_text=""（≠None），
        # 仅判断 `full_text is None` 抓不住这种情况 → 会把空串当成功返回 → 写**空复刻** + exit0 **假成功**。
        # 空内容必须触发 fallback 链 / 最终 GenModelExhaustedError（exit3），杜绝假成功。
        if not (full_text or "").strip():
            failures.append((profile.name, "返回空内容（HTTP200 零 content·疑 reasoning token 吃光）"))
            print(f"\n{prefix}[FALLBACK] {profile.name} 返回空内容·转下一 profile", file=sys.stderr)
            continue

        elapsed = time.time() - t0
        print(f"\n{prefix}[distill_replicate] 接收完毕 ({len(full_text)} chars, {elapsed:.1f}s) via {profile.name}")
        return full_text, profile, elapsed

    raise GenModelExhaustedError(failures)


# ============ cluster 模式辅助 ============

def discover_claude_scenes_dir(scenes_dir: Path) -> list[tuple[str, str]]:
    """枚举 Claude 亲笔复刻场景稿目录（--claude-scenes-dir）下的 scene_*.txt。

    v29 required 前置 · 语义对齐 gen_writer.discover_claude_scenes：目录缺失 / 无场景稿 /
    单场景稿 <200 CJK → stderr [ERROR] + exit 2（绝不回退 gen-model 从零生成 · 不兼容不降级）。
    返回 [(scene_filename, text), ...] 按文件名排序（per-scene 文件 = 天然润色分段）。
    """
    if not scenes_dir.is_dir():
        print(f"[ERROR] Claude 复刻场景稿目录不存在: {scenes_dir} — v29 复刻要求先由蒸馏 plan 的"
              f"复刻 step spawn Claude agent 按 skill 亲笔逐场景写复刻草稿落盘 scene_*.txt，"
              f"再由本脚本分段润色。", file=sys.stderr)
        sys.exit(2)
    files = sorted(scenes_dir.glob("scene_*.txt"))
    if not files:
        print(f"[ERROR] {scenes_dir} 下无 scene_*.txt 复刻场景稿", file=sys.stderr)
        sys.exit(2)
    scenes: list[tuple[str, str]] = []
    for f in files:
        t = f.read_text(encoding="utf-8").strip()
        if cjk_count(t) < 200:
            print(f"[ERROR] 复刻场景稿过短(<200 CJK): {f.name} — Claude 复刻草稿必须每场景写透，"
                  f"禁止梗概占位", file=sys.stderr)
            sys.exit(2)
        scenes.append((f.name, t))
    return scenes


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

    cluster_index.json 的主 key 是 chapter_range（2 元数组 [lo, hi]）；部分 producer
    只写扁平 chapter_start/chapter_end 或简写 ch_start/ch_end，读不到会导致
    gather_cluster_ref_text 返回空（参考原文没注入·复刻只靠 skill）。

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

    cluster_index.json 的主 key 是 estimated_words；只读 total_words/word_count
    会导致 meta.json 的 total_words_original 永远回退空。

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


# ============ L3d · draft-level critic-refine + knockout（PerFine 式）============
#
# 没有 draft 级改稿的复刻是「得分→盲改 skill→再测」乏力循环——草稿一出即定稿、不保最优，
# 差距只能靠下一轮蒸 skill 修。本节按 PerFine（arxiv 2510.24469 · GEval +7-13% · 3-5 轮稳）
# 在复刻出稿后——
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
#   · 默认 active（默认关闭的功能没人会主动打开）· env DRAFT_REFINE_MODE=off 可关。

# critic 评的四类维度（PerFine rubric · 映射作者数值契约表 + 受控语言学坐标）
DRAFT_CRITIC_DIMENSIONS = ["tone", "vocabulary", "syntax", "topicality"]


def _draft_refine_mode() -> str:
    """读 env DRAFT_REFINE_MODE 决定复刻出稿后是否走 critic-refine + knockout。

    值（大小写不敏感）：
      · active（默认 / 空 / 非法值）：开启 draft-level critic-refine + knockout——
        草稿出来后 critic 出结构化 feedback 直接改稿 · SFS 当裁判 · knockout 保最优（3-5 轮）。
        （默认关闭的功能没人会主动打开 · 默认全开真生效。）
      · off：关闭——复刻一次出稿即定稿（用于 A/B 对照）。
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
        print(f"[L3d · SFS 裁判] WARN 评分失败，降级保留当前最优稿: {str(e)[:160]}")
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
            print(f"[L3d · critic r{r}] WARN critic 调用失败，提前结束精修循环: {e}")
            rounds_trace.append({"round": r, "stage": "critic", "error": str(e)[:160]})
            break

        # ② refine：据 feedback 改写草稿
        refine_user = build_refine_prompt(best_draft, critic_feedback, contract_excerpt)
        try:
            candidate = clean_output(
                call_fn(system_prompt, refine_user, tag=f"refine r{r}"))
        except GenModelExhaustedError as e:
            print(f"[L3d · refine r{r}] WARN refine 调用失败，保留当前最优稿: {e}")
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
              f"best={best_score} · {'采纳' if accepted else '淘汰(保最优)'}")

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


# ============================================================
# 维度消融驱动骨架（experiment_gate · 待 gen-model API）
# ============================================================

def _ablation_record_dir(project_root: Path) -> Path:
    """消融实验逐 SFS 即时落盘目录（断点可查 · _数据库/.ablation/）。"""
    d = Path(project_root) / "_数据库" / ".ablation"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append_ablation_record(project_root: Path, dimension: str, arm: str,
                            seed: int, sfs):
    """把一次 (arm, seed) 的 SFS 即时 append 落盘 <dimension>_<arm>.json（断点可查）。

    落盘失败绝不崩实验（顾问非法官·消融只是 advisory 证据）—— IO 错误吞掉记 stderr。
    """
    try:
        rec_path = _ablation_record_dir(project_root) / f"{dimension}_{arm}.json"
        records = []
        if rec_path.exists():
            try:
                records = json.loads(rec_path.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    records = []
            except (json.JSONDecodeError, OSError):
                records = []
        records.append({
            "arm": arm, "seed": seed,
            "sfs": (round(float(sfs), 4) if sfs is not None else None),
            "ts": time.time(),
        })
        rec_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as e:  # noqa: BLE001 · 断点记录失败不崩实验
        print(f"[ablation] WARN 断点落盘失败（{dimension}_{arm} seed{seed}）: "
              f"{str(e)[:120]}", file=sys.stderr)


def _make_real_ablation_runner(project_root: Path, cluster_id: int,
                               ref_texts, dimension: str):
    """构造真实 subprocess runner（_run_one 缺省时用）：toggle env → build_manifest →
    gen_writer → 读草稿 → _score_draft_sfs。

    🔴 北极星④：消融跑 writer 路径（思维注入只在 gen_writer 经 build_manifest 生效），
    **不**改本文件的复刻 prompt。每次都重跑 build_manifest（带 env）让 ABLATE_* 生效。

    env 传法（subprocess 隔离·不污染父进程 os.environ）：
      · ABLATE_DIMENSIONS = ablate_dims（"" = baseline 全注入 / "<dim>" = 抹该维）
      · ABLATE_RANDOM_FIELD = "1" if random_field else ""（负对照注入随机 directive）

    鲁棒：subprocess 非 0 / 草稿读不到 / SFS None → 返回 None（该 seed 跳过·不崩整实验）。
    草稿目录备份/恢复由外层 run_dimension_ablation 的 finally 统一负责（避免污染正式草稿）。
    """
    import subprocess  # noqa: E402 · 仅真实 runner 路径用（单测全 mock 不触发）

    sys.path.insert(0, str(Path(__file__).parent))
    from frozen_util import child_python  # noqa: E402 · frozen 兼容解释器（禁写死 "python"）

    proj_str = str(project_root)
    bm_script = str(Path(__file__).parent / "build_manifest.py")
    gw_script = str(Path(__file__).parent / "gen_writer.py")
    ch_start = _ablation_infer_ch_start(project_root, cluster_id)
    draft_path = (project_root / "章节"
                  / f"cluster_{cluster_id:03d}_draft"
                  / f"cluster_{cluster_id:03d}_draft.txt")

    def _run_one(arm: str, seed: int, ablate_dims: str, random_field: bool):
        env = dict(os.environ)
        env["ABLATE_DIMENSIONS"] = ablate_dims or ""
        env["ABLATE_RANDOM_FIELD"] = "1" if random_field else ""
        py = child_python()
        try:
            # ① build_manifest（positional：项目路径 + 章节号）—— ABLATE_* env 在此生效
            r1 = subprocess.run(
                [py, bm_script, proj_str, str(ch_start)],
                env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
            if r1.returncode != 0:
                print(f"[ablation·{arm}·seed{seed}] build_manifest 非0退出 "
                      f"(rc={r1.returncode})·该 seed 跳过\n{(r1.stderr or '')[:400]}")
                return None
            # ② gen_writer（--project / --cluster int）—— 写到固定草稿路径
            r2 = subprocess.run(
                [py, gw_script, "--project", proj_str,
                 "--cluster", str(cluster_id)],
                env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900)
            if r2.returncode != 0:
                print(f"[ablation·{arm}·seed{seed}] gen_writer 非0退出 "
                      f"(rc={r2.returncode})·该 seed 跳过\n{(r2.stderr or '')[:400]}")
                return None
        except subprocess.TimeoutExpired as e:  # gen-model 挂住/超时·该 seed 跳过不卡死全实验
            print(f"[ablation·{arm}·seed{seed}] subprocess 超时（{e.timeout}s·gen-model 可能挂住）"
                  f"·该 seed 跳过", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001 · subprocess 异常该 seed 跳过不崩实验
            print(f"[ablation·{arm}·seed{seed}] subprocess 异常·该 seed 跳过: "
                  f"{str(e)[:200]}", file=sys.stderr)
            return None
        # ③ 读草稿 → SFS（同一把尺：同 _score_draft_sfs + 同 ref_texts）
        if not draft_path.exists():
            print(f"[ablation·{arm}·seed{seed}] 草稿读不到（{draft_path}）·该 seed 跳过")
            return None
        try:
            draft_text = draft_path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[ablation·{arm}·seed{seed}] 草稿读取失败·该 seed 跳过: "
                  f"{str(e)[:160]}", file=sys.stderr)
            return None
        return _score_draft_sfs(ref_texts, draft_text)

    return _run_one


def _ablation_infer_ch_start(project_root: Path, cluster_id: int) -> int:
    """推 cluster 起始章号（喂 build_manifest 的章节号 positional）。

    复用 gen_writer._infer_cluster_start_ch（事件簇.json.ch_start/chapter_range → 末章+1
    → cluster_001=1 兜底）·禁用 f"cluster_{ch:03d}" 机械拼接。导入失败兜底返 cluster_id
    （绝不崩 · 章号无效时 build_manifest 自身会回退 _infer_start_chapter）。
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import gen_writer  # noqa: E402 · 复用 ch_start 推导（同一权威·不重造）
        return int(gen_writer._infer_cluster_start_ch(Path(project_root), cluster_id))
    except Exception as e:  # noqa: BLE001 · 推导失败兜底（build_manifest 会再回退）
        print(f"[ablation] WARN ch_start 推导失败（cluster {cluster_id}）→ 兜底用 "
              f"cluster_id: {str(e)[:120]}", file=sys.stderr)
        return int(cluster_id)


def run_dimension_ablation(*, project, cluster_ref, skill_version, dimension,
                           n_seed=3, layer="surface", probe_noise_floor=0.0,
                           random_field_control=False, ref_texts=None,
                           _run_one=None):
    """维度消融驱动 + A1-A5 自证（leave-one-dim-out · experiment_gate 顶层编排）。

    🔴🔴🔴 北极星⑤⑥铁律（调用方必读）：本函数返回的一切（verdict / significant /
    effect_detail / entry）**纯 advisory 供人看**——
      · 北极星⑤（顾问非法官）：结果**绝不进 hard_gate**，调用方**绝不据消融结果自动改注入
        维度**（不据 verdict 把 ABLATE_DIMENSIONS 当决策开关自动 toggle）。只产 entry 给人裁。
      · 北极星⑥（实验数据非拍脑袋）：正对照（抹真维退化超门 effect_significant=True 且
        direction='ablation_degrades'）+ 负对照（随机字段无显著效应 negative_control_pass=
        True）**两条都过**，结论才可信；任一不过 → 视作 inconclusive，**绝不**据此动注入维度。

    ───────────────── 跑法（writer 路径·非改复刻 prompt）─────────────────
    本函数走 **writer 路径**（A1-A5 思维注入只在 gen_writer 经 build_manifest
    生效·distill_replicate 自身不消费思维注入）。消融驱动 = toggle build_manifest 的
    ABLATE_DIMENSIONS env 后跑 writer：
      · baseline_runs = N seed（ABLATE_DIMENSIONS 空·含该维注入）→ 各 SFS
      · ablated_runs  = N seed（ABLATE_DIMENSIONS=<dimension>·抹该维）→ 各 SFS
      · random_field_control=True → random_runs = N seed（ABLATE_RANDOM_FIELD=1·注入无意义
        随机 directive）→ 期望与 baseline 无显著差异（=统计层能分辨噪声·防假阳性）。

    ───────────────── 量纲铁律 ─────────────────
    🔴 baseline / ablated / random 三组用**同一把尺**（同 _score_draft_sfs + 同 ref_texts），
    **绝不**跨量纲比（不拿 author_baseline 的 judge 0-10 口径混进来）。

    参数：
      project: 项目根路径（含 _数据库/ + cluster_index）。
      cluster_ref: cluster_id（"cluster_001" / 1 / "auto_003"）—— 经 cluster_lookup 反查 int。
      skill_version / dimension: 标在 entry 上（skill 版本 / 被抹维度如 'A3'）。
      n_seed: 每 arm 跑几趟 seed（默认 3·样本须各 ≥2 才有 seed-level std）。
      layer / probe_noise_floor: 透传 effect_significance_detail（思维维传 av_judge 探针地板）。
      random_field_control: True → 跑负对照 arm。
      ref_texts: SFS 裁判的源作者参考文本 list（同一把尺·真实 runner 必传·否则 SFS 恒 None）。
      _run_one: **依赖注入**——单测传 mock 不实跑 gen-model。签名见下。缺省→构造真实 subprocess
                runner（备份草稿目录 → toggle env → build_manifest → gen_writer → 读草稿 → SFS
                → finally 恢复草稿目录）。

    _run_one(arm: str, seed: int, ablate_dims: str, random_field: bool) -> float|None
      arm ∈ {'baseline','ablated','random'}；返回该趟 SFS（None=该 seed 失败·被 filter 掉）。

    返回 dict（kind='ablation'）：
      {kind, dimension, cluster_ref, skill_version, baseline_sfs, ablated_sfs, effect_detail,
       n_seed_effective:{baseline,ablated[,random]},
       [random_field_sfs, random_field_detail, negative_control_pass], entry}
    """
    sys.path.insert(0, str(Path(__file__).parent))
    import distill_holdout  # noqa: E402 · 纯函数统计判据（已 ready·不重造）

    project_root = Path(project)

    # ── cluster_ref → cluster_id int（禁用 f"cluster_{ch:03d}" 机械拼接·走 cluster_lookup） ──
    import cluster_lookup  # noqa: E402
    cluster_id = cluster_lookup.cluster_num(cluster_ref)
    if cluster_id is None:
        raise ValueError(
            f"run_dimension_ablation：无法从 cluster_ref={cluster_ref!r} 解析 cluster_id"
            f"（禁用机械拼接·须走 cluster_lookup.cluster_num）")

    # ── _run_one 缺省 → 构造真实 subprocess runner（含草稿目录备份/恢复 finally） ──
    backup_dir = None
    draft_dir = (project_root / "章节" / f"cluster_{cluster_id:03d}_draft")
    if _run_one is None:
        import shutil  # noqa: E402 · 仅真实 runner 路径备份草稿目录（单测 mock 不触发）
        _run_one = _make_real_ablation_runner(
            project_root, cluster_id, ref_texts or [], dimension)
        # 备份正式草稿目录（消融会反复重写它）—— 全跑完 finally 恢复·绝不污染正式产物
        if draft_dir.exists():
            backup_dir = draft_dir.with_name(draft_dir.name + ".ablation_bak")
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir, ignore_errors=True)
                shutil.copytree(draft_dir, backup_dir)
            except OSError as e:
                print(f"[ablation] WARN 草稿目录备份失败（消融会改正式草稿·风险自负）: "
                      f"{str(e)[:160]}", file=sys.stderr)
                backup_dir = None

    def _collect(arm: str, ablate_dims: str, random_field: bool) -> list:
        """跑 n_seed 趟同 arm·即时落盘断点·filter 掉 None。"""
        runs = []
        for seed in range(n_seed):
            sfs = _run_one(arm, seed, ablate_dims, random_field)
            _append_ablation_record(project_root, dimension, arm, seed, sfs)
            if sfs is not None:
                runs.append(float(sfs))
        return runs

    try:
        baseline_runs = _collect("baseline", "", False)
        ablated_runs = _collect("ablated", dimension, False)

        effect_detail = distill_holdout.effect_significance_detail(
            baseline_runs, ablated_runs,
            probe_noise_floor=probe_noise_floor, layer=layer)
        entry = distill_holdout.build_ablation_entry(
            skill_version, dimension, cluster_ref,
            baseline_runs, ablated_runs,
            probe_noise_floor=probe_noise_floor, layer=layer)

        result = {
            "kind": "ablation",
            "dimension": dimension,
            "cluster_ref": cluster_ref,
            "skill_version": skill_version,
            "baseline_sfs": baseline_runs,
            "ablated_sfs": ablated_runs,
            "effect_detail": effect_detail,
            "n_seed_effective": {
                "baseline": len(baseline_runs),
                "ablated": len(ablated_runs),
            },
            "entry": entry,
            "_north_star_note": (
                "北极星⑤⑥：本结果纯 advisory 供人看·绝不进 hard_gate·调用方不得据此自动"
                "改注入维度。正对照(抹真维退化超门)+负对照(随机字段无差异)两条都过结论才可信。"
            ),
        }

        # ── 负对照（随机字段·验死维·防假阳性）·只在显式开启时跑 ──
        if random_field_control:
            random_runs = _collect("random", "", True)
            rf_detail = distill_holdout.effect_significance_detail(
                baseline_runs, random_runs,
                probe_noise_floor=probe_noise_floor, layer=layer)
            # 随机字段应**无**显著效应（统计层分得清噪声）→ negative_control_pass=True。
            # 缺键容错默认 True（保守：缺信息时不轻易宣称负对照失败·但样本不足时 detail 已
            # significant=False 故仍 pass·与 effect_significance_detail inconclusive 行为一致）。
            negative_control_pass = not rf_detail.get("effect_significant",
                                                      rf_detail.get("significant", False))
            result["random_field_sfs"] = random_runs
            result["random_field_detail"] = rf_detail
            result["negative_control_pass"] = bool(negative_control_pass)
            result["n_seed_effective"]["random"] = len(random_runs)

        return result
    finally:
        # ── 恢复正式草稿目录（绝不让消融污染正式产物） ──
        if backup_dir is not None and backup_dir.exists():
            import shutil  # noqa: E402
            try:
                if draft_dir.exists():
                    shutil.rmtree(draft_dir, ignore_errors=True)
                shutil.move(str(backup_dir), str(draft_dir))
            except OSError as e:
                print(f"[ablation] WARN 草稿目录恢复失败（备份留在 {backup_dir}）: "
                      f"{str(e)[:160]}", file=sys.stderr)


# ============ main ============

def main():
    check_deps()
    parser = argparse.ArgumentParser(
        description="蒸馏 phase-4 cluster 终验复刻（v29 · Claude 亲笔草稿 + gemini 分段润色 · 2026-07-11）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--style-skill", required=True,
                        help="作者风格 skill .md 路径（v0/v1/v2/...）")
    parser.add_argument("--mode", choices=["cluster"], default="cluster",
                        help="复刻颗粒度（cluster 单轨 · chapter 模式早已删除）")
    parser.add_argument("--output", required=True,
                        help="输出 txt 路径（复刻润色终稿）")
    parser.add_argument("--claude-scenes-dir", required=True,
                        help="Claude 复刻场景稿目录，含 scene_*.txt（由蒸馏 plan 的复刻 step "
                             "spawn Claude agent 先按 skill 亲笔逐场景写复刻草稿落盘）· v29 required 前置")

    # cluster 模式参数
    parser.add_argument("--cluster-ref",
                        help="[cluster 模式] cluster_id（如 cluster_001 / auto_003）· 供同源 ref_text + meta")
    parser.add_argument("--project",
                        help="[cluster 模式] 项目路径（含 cluster_index.json · 供同源 ref_text / snippet_seed）")

    # 通用
    parser.add_argument("--profile",
                        help="覆盖 active profile（默认用 .env GEN_MODEL_ACTIVE）")
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

    # v29：复刻 = Claude 亲笔草稿 + gemini 分段润色（同栈）· 唯一 system prompt = 润色语义。
    system_prompt = REPLICATE_SYSTEM_PROMPT

    loader = GenModelLoader()
    if args.profile:
        loader._active_name_override = args.profile  # noqa
    try:
        active = loader.get_active_profile()
        print(f"[distill_replicate] active profile = {active.name} ({active.model})")
    except GenModelConfigError as e:
        print(f"[ERROR] gen-model 配置错误: {e}", file=sys.stderr)
        sys.exit(2)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ========== cluster 模式（v29 · Claude 草稿 + gemini 分段润色） ==========
    if not args.cluster_ref or not args.project:
        print("[ERROR] --mode cluster 需要 --cluster-ref + --project", file=sys.stderr)
        sys.exit(2)

    project_root = Path(args.project)
    try:
        cluster_meta = load_cluster_meta(project_root, args.cluster_ref)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] cluster 元信息加载失败: {e}", file=sys.stderr)
        sys.exit(2)

    # 枚举 Claude 亲笔复刻场景稿；缺目录、无稿或单稿过短时 exit 2。
    scenes_dir = Path(args.claude_scenes_dir)
    scene_files = discover_claude_scenes_dir(scenes_dir)

    ref_text = gather_cluster_ref_text(project_root, cluster_meta)

    # 🎴 真实原文「语感种子」播种（env SNIPPET_SEED_MODE 默认 on）：从 cluster 同源原文池按
    # ref_text 风格/情绪寄存器选 1-2 段真实片段当语感锚点（北极星① · 纯 prompt 注入 · 留痕不黑箱）。
    _originals_dir = project_root / "原文"
    if not _originals_dir.exists():
        _originals_dir = None
    seed_section, seed_trace = snippet_seed.make_seed_block_from_dir(
        _originals_dir, ref_text=ref_text)
    print(f"[snippet_seed] {seed_trace}", file=sys.stderr)

    total = len(scene_files)
    src_total_cjk = sum(cjk_count(t) for _, t in scene_files)
    print(f"[cluster·v29] {args.cluster_ref} · {total} 个 Claude 场景稿 · 合计 {src_total_cjk} CJK 待润色")

    full_text_parts = []
    polish_metas = []
    total_elapsed = 0.0

    # v29 分段润色主流程：逐场景段调 gemini 按风格档等体量重写（守恒校验 + 超界重试 1 次）→ 拼接。
    for i, (name, src) in enumerate(scene_files):
        src_cjk = cjk_count(src)
        user = build_polish_subcall_prompt(
            style_skill_md, ref_text, src, i, total, seed_section=seed_section)
        # 润色 max_tokens 预算：等体量重写 · 守恒带上限对应 token（src×1.30 CJK ×2 + buffer）·
        # 生产态 profile.max_tokens(65536) 通常主导，此处仅 profile 未设 max_tokens 时的兜底。
        max_tokens_this = min(20000, max(4000, int(src_cjk * POLISH_CJK_HIGH * 2.0)))
        try:
            reply, used_profile, elapsed = call_gen_model(
                loader, system_prompt, user,
                default_max_tokens=max_tokens_this,
                tag=f"polish {i + 1}/{total}",
            )
        except GenModelExhaustedError as e:
            print(f"\n[ERROR] 场景 {i + 1}/{total} ({name}) 全部 profile 失败:\n{e}", file=sys.stderr)
            # 已润色的段落写到 .partial.txt 防丢
            if full_text_parts:
                partial = "\n\n".join(full_text_parts)
                output_path.with_suffix(".partial.txt").write_text(partial, encoding='utf-8')
                print(f"[recovery] 已写 .partial.txt 保留前 {i} 段产出", file=sys.stderr)
            sys.exit(3)

        body = clean_output(reply)
        out_cjk = cjk_count(body)
        ratio = out_cjk / max(src_cjk, 1)
        retried = False
        # 段级字数守恒校验：超界带明确字数指令重试 1 次，再超界取离守恒中心更近者（北极星⑤透明可审）。
        if not (POLISH_CJK_LOW <= ratio <= POLISH_CJK_HIGH):
            retried = True
            print(f"[polish] {name} 守恒超界 ratio={ratio:.2f}（{src_cjk}→{out_cjk}）· "
                  f"带字数指令重试 1 次", file=sys.stderr)
            user2 = user + (
                f"\n\n【字数守恒警告】你上一版输出约 {out_cjk} 个汉字，超出守恒带。"
                f"润色是等体量重写：这一段的输出字数必须落在 "
                f"{int(src_cjk * POLISH_CJK_LOW)}-{int(src_cjk * POLISH_CJK_HIGH)} 个汉字之间——"
                f"不许压缩省略情节，也不许注水扩写。重新输出这一段的润色全文。")
            try:
                reply2, used_profile, elapsed2 = call_gen_model(
                    loader, system_prompt, user2,
                    default_max_tokens=max_tokens_this,
                    tag=f"polish {i + 1}/{total} retry")
                elapsed += elapsed2
                body2 = clean_output(reply2)
                out2 = cjk_count(body2)
                if abs(out2 / max(src_cjk, 1) - 1.0) < abs(ratio - 1.0):
                    body, out_cjk = body2, out2
                    ratio = out_cjk / max(src_cjk, 1)
            except GenModelExhaustedError as e:
                print(f"[polish] {name} 守恒重试全 profile 失败 · 保留首版: {e}", file=sys.stderr)

        full_text_parts.append(body)
        total_elapsed += elapsed
        polish_metas.append({
            "scene": name,
            "src_cjk": src_cjk,
            "out_cjk": out_cjk,
            "ratio": round(ratio, 3),
            "retried": retried,
            "profile_used": used_profile.name,
            "elapsed_seconds": round(elapsed, 1),
        })
        print(f"[polish] {name}: {src_cjk}→{out_cjk} CJK (ratio={ratio:.2f})", file=sys.stderr)

    full_text = "\n\n".join(full_text_parts)

    # ========== L3d · draft-level critic-refine + knockout（PerFine 式 · 默认 active） ==========
    # 草稿拼好后：critic 出结构化 feedback 直接改稿 → SFS 裁判评分 → knockout 保最优（3-5 轮）。
    # critic + refine 复用同 active gen-model profile（复刻仍走 gen-model）· SFS 当裁判透明。
    refine_mode = args.draft_refine if args.draft_refine is not None else _draft_refine_mode()
    # reasoning 模型（thinking_level 非空）的 critic→refine 轮会把正文越改越短
    # （且其 SFS 在 refine 内算 None 致 knockout 无法择优、退化保最后一轮=最短）。
    # 故 reasoning 模型自动关 draft-refine，除非用户显式 --draft-refine active。
    if refine_mode == "active" and args.draft_refine is None and (getattr(active, "thinking_level", None) or getattr(active, "reasoning_effort", None)):
        refine_mode = "off"
        print(f"[L3d] 检测到 reasoning 模型({active.model})·自动关 draft-refine"
              f"（refine 轮缩写正文 + SFS None 致 knockout 失效·2026-06-07 适配）", file=sys.stderr)
    refine_trace: dict = {"draft_refine_enabled": False, "mode": refine_mode}
    if refine_mode == "active":
        # SFS 裁判用同 cluster 的真实原文当 ref（gather 的同源 ref_text 拆段；空则裁判降级保稿）
        ref_for_score = [ref_text] if ref_text else []
        print(f"[L3d] draft-refine = active · {_draft_refine_rounds()} 轮 critic→refine→SFS knockout")
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

    # ========== A12 · LongBench-Write 六维质量 rubric（env DISTILL_RUBRIC_MODE 默认 off） ==========
    # 复刻评估现状=纯统计 SFS（style_evaluator 确定性）→ rubric 按门控可选落地：开启时复用
    # 既有 call_gen_model 通道发 1 次 judge 调用（同栈 gen-model·不新增独立链路步骤），六维各
    # 1-5 分聚合 (mean-1)*25 归一 0-100，judge prompt 明示不考虑长度（质量与长度双轨分离）。
    # 缺任一维 → 整体作废重试（≤3）· 全失败诚实记 rubric_unavailable 不伪造分。
    # 🔴 北极星⑤：恒 advisory 旁证——写 meta.json 与 SFS 并列，不改任何闸门判据/verdict/exit。
    def _rubric_call(system: str, user: str) -> str:
        reply, _prof, _el = call_gen_model(
            loader, system, user, default_max_tokens=2000, tag="rubric六维")
        return reply

    rubric_report = distill_rubric.run_rubric_judge_gated(
        _rubric_call, full_text,
        task_brief=(f"按源作者风格 skill 复刻故事块 {args.cluster_ref}"
                    "（自创角色与场景·整块连续叙事·风格指纹对照用）"))

    meta = {
        "mode": "cluster",
        "writer_mode": "claude_draft_gemini_polish_v29",
        "cluster_id": args.cluster_ref,
        "cluster_meta": {
            "chapters_count": cluster_chapters_count(cluster_meta) or None,
            "chapter_start": cluster_chapter_bounds(cluster_meta)[0],
            "chapter_end": cluster_chapter_bounds(cluster_meta)[1],
            "boundary_reason": cluster_meta.get("boundary_reason"),
            "total_words_original": cluster_total_words(cluster_meta) or None,
        },
        "style_skill": str(style_skill),
        "project": str(project_root),
        "claude_scenes_dir": str(scenes_dir),
        "scene_count": total,
        # v29 per-scene 润色守恒遥测（替代 subcall_metas · {scene,src_cjk,out_cjk,ratio,retried} · 北极星⑤透明可审）
        "polish_metas": polish_metas,
        "conservation_band": [POLISH_CJK_LOW, POLISH_CJK_HIGH],
        "total_source_cjk_chars": src_total_cjk,
        "total_actual_cjk_chars": cjk_count(full_text),
        "total_elapsed_seconds": round(total_elapsed, 1),
        # 🎴 真实原文语感种子播种痕迹（默认 on · 留痕不黑箱）
        "snippet_seed": seed_trace,
        # L3d · draft-level critic-refine + knockout 痕迹（PerFine 式 · critic feedback + 每轮 SFS · 不黑箱）
        "draft_refine": refine_trace,
        # A12 · LongBench-Write 六维质量 rubric（advisory 旁证 · 与 SFS 并列 · 不改闸门判据）
        "rubric_sixdim": rubric_report,
        "produced_by": "distill_replicate.py v29 · Claude 亲笔草稿 + gemini 分段润色（同栈）· "
                       "段级字数守恒带 [0.85,1.30] · 🎴 snippet-seed 播种 · "
                       "L3d draft critic-refine+knockout · A12 LongBench-Write 六维 rubric"
                       "（DISTILL_RUBRIC_MODE 门控·advisory）",
    }
    output_path.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\n[OK · cluster · v29 polish] {args.cluster_ref}", file=sys.stderr)
    print(f"     输出: {output_path}", file=sys.stderr)
    print(f"     润色场景段: {total} 段", file=sys.stderr)
    print(f"     总字数: {cjk_count(full_text)} CJK（源 Claude 草稿 {src_total_cjk} CJK）")
    if refine_trace.get("draft_refine_enabled"):
        print(f"     draft-refine: {refine_trace.get('rounds_run')} 轮 · "
              f"SFS {refine_trace.get('initial_score')} → {refine_trace.get('final_best_score')}"
              f"（knockout 保最优）", file=sys.stderr)
    if rubric_report.get("status") == "ok":
        print(f"     rubric六维: {rubric_report['aggregate_0_100']}/100 "
              f"{rubric_report['scores']}（advisory 旁证·长度已剥离·SFS 仍唯一出货闸）",
              file=sys.stderr)
    elif rubric_report.get("status") == "rubric_unavailable":
        print(f"     rubric六维: rubric_unavailable（judge {rubric_report.get('attempts')} 次"
              f"缺维/非法·诚实记不伪造分）", file=sys.stderr)
    print(f"     总耗时: {total_elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
