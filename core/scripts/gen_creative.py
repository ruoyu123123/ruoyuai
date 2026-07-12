#!/usr/bin/env python3
"""
gen_creative.py — Gen-Model 创意卡与卷描述生成工具

「含创意笔触」的输出由当前 active gen-model profile 生成。
Claude 主代理负责准备 brief（题材/调研缓存/角色骨架），调本工具生成正文段，再接收 JSON 展示给用户。

当前公开 CLI mode：

  --mode brainstorm    生成 N 张灵感卡（开书用，配合 /write 命令）                    [✓]
  --mode volume_arc    生成卷级大纲（分卷 chunk + WAL 断点续跑：骨架→逐卷 ME 池→确定性合并·配合 /outline·阶段2 建书·实现在 gen_creative_volume_arc.py） [✓]
  --mode distill_reflect  蒸馏 phase-3 修正反思·产 skill markdown（配合 /distill-style） [✓]

用法示例：

  # 灵感卡（开书）
  python core/scripts/gen_creative.py --mode brainstorm \\
    --topic "末世/沙盒/山海经" --count 3 \\
    --research <调研缓存 md 路径> \\
    --style-ref <风格 skill md 路径>

输出：JSON 到 stdout（默认）或 --out <path>。

配置：参见 .env 中 GEN__<name>__* 字段 + GEN_MODEL_ACTIVE。
管理：python core/scripts/gen_model.py list / switch / show
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import (  # noqa: E402
    GenModelLoader,
    GenModelConfigError,
    GenModelExhaustedError,
    Profile,
    reasoning_extra_body,
)


# ============ 依赖 / max_tokens ============
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


def load_model_capabilities_cache() -> dict:
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


def resolve_max_tokens(profile: Profile, default: int = 8000) -> tuple[int, str]:
    """创意卡输出短，默认 8K（writer/fixer 用 16K）"""
    if profile.max_tokens is not None:
        return profile.max_tokens, 'profile_explicit'
    cache = load_model_capabilities_cache()
    caps = cache.get('model_capabilities', {}).get(profile.model)
    if caps:
        return caps.get('recommended_max_tokens_for_writing', default), \
               f"cache:{caps.get('source','?')}"
    return default, f'default_fallback_{default}'


# ============ 文件读取工具 ============
def read_text(p: Path | None, limit: int = None) -> str:
    if p is None or not p.exists():
        return ""
    t = p.read_text(encoding='utf-8')
    if limit and len(t) > limit:
        t = t[:limit] + f"\n... [truncated at {limit} chars]"
    return t


# ============ MODE: brainstorm（灵感卡）============
def build_brainstorm_prompt(topic: str, count: int, research: str,
                            style_ref: str) -> tuple[str, str]:
    """主代理传：题材 + 调研缓存 + 风格基线 → gen-model 输出 N 张灵感卡正文段"""
    system = """你是长篇小说的灵感卡生成引擎。

主代理（Claude）已完成调研（联网搜热点 / 竞品 / 设定参考 / 命名规律），把调研缓存和题材方向交给你。
你的任务：生成 N 张差异化的灵感卡，每张卡都有自己的「钩子 + 卷骨架 + 卖点定位 + 风险点」。

# 灵感卡硬约束

1. **每张卡差异化要明显**（不是同一立意的微调，是真正三种走法）
2. **logline 50 字内**（一句话点题）
3. **核心机制 100 字内**（世界观底层 / 力量体系 / 关键设定）
4. **卷骨架 5-6 卷**（含每卷主角原型 / 时代象征 / 卷高潮事件）
5. **卖点定位**（贴市场哪条线，引用调研发现）
6. **风险点**（明确雷区 + 应对）
7. **必须引用调研 source**（每卡至少一条 URL 来源，从调研缓存中取）

# 输出格式

严格输出 JSON（无 markdown 标签包裹），schema：

{
  "version": 1,
  "topic": "...",
  "cards": [
    {
      "card_id": "A",
      "title": "卡片名（吸引人的短标题）",
      "logline": "一句话点题（≤50字）",
      "core_mechanism": "核心机制（≤100字）",
      "volume_skeleton": [
        {"vol": 1, "title": "...", "archetype": "...", "climax": "...", "duration_chapters": 80},
        ...
      ],
      "selling_point": "卖点定位（贴市场哪条线）",
      "risk": "风险点 + 应对",
      "source_refs": ["http://...", "http://..."]
    },
    ...
  ]
}

不要写解释、不要加引言、不要写「以下是」。直接输出 JSON。
"""

    user = f"""# 题材方向

{topic}

# 风格基线（从风格库 skill.md 读，决定 voice/调子 · v22.gov.align.notrunc 全量传）

{style_ref if style_ref else '（未提供风格基线，按通用文学叙事处理）'}

# 调研缓存（必读 synthesis 段；source URLs 用于 card.source_refs · v22.gov.align.notrunc 全量传）

{research if research else '（未提供调研，警告：模型记忆 ≠ 实时热点；尽量保守生成）'}

# 任务

为本题材生成 **{count}** 张差异化灵感卡。三张卡应代表完全不同的三种走法（例如：群像 / 神话 / 反向悬疑 三立意）。
按上方 JSON schema 输出，不要 markdown 包裹。
"""
    return system, user


def parse_brainstorm_output(reply: str) -> dict:
    """解析 brainstorm 输出 JSON"""
    return _parse_json_loose(reply, fallback={"version": 1, "cards": [], "_raw": reply[:2000]})


# ============ 共享：JSON 解析 ============
def _parse_json_loose(reply: str, fallback: dict) -> dict:
    """从返回中找 JSON 块。支持：纯 JSON / ```json ... ``` 包裹 / 末尾 JSON"""
    # 1. 尝试 ```json ... ``` 包裹
    m = re.search(r'```json\s*\n(.*?)\n```', reply, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 2. 尝试纯 JSON（首字符 { 或 [）
    stripped = reply.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    # 3. 找第一个 { 到最后一个 } 之间
    first = stripped.find('{')
    last = stripped.rfind('}')
    if first >= 0 and last > first:
        try:
            return json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            pass
    return fallback


# ============ Gen-Model 调用（含 fallback 链） ============
def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   default_max_tokens: int = 8000) -> tuple[str, Profile]:
    """调当前 active profile；失败时按 fallback 链尝试。"""
    from openai import OpenAI

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []

    for i, profile in enumerate(candidates):
        max_tokens, mt_source = resolve_max_tokens(profile, default=default_max_tokens)
        if i == 0:
            print(f"[gen_creative] 调用 active: {profile.name} ({profile.model})")
            print(f"[gen_creative] max_tokens={max_tokens} (source: {mt_source})")
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[gen_creative] prompt: system={len(system)} chars, user={len(user)} chars")

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
        full_text = ""
        _xb = reasoning_extra_body(profile)  # reasoning 控制·防 thinking 暴走(elysiver/pie-xian)
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
                **({"extra_body": _xb} if _xb else {}),
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
            print(f"\n[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        print(f"\n[gen_creative] 接收完毕 ({len(full_text)} chars) via {profile.name}")
        return full_text, profile

    raise GenModelExhaustedError(failures)


def build_distill_reflect_prompt(*, gap_text: str, current_skill: str,
                                 author_block: str, version: int) -> tuple[str, str]:
    """phase-3 修正反思 prompt：读 SFS 差距 → 产 skill v.N 文字约束（markdown）。

    gap_text 空 = **首版 v0 生成**（无 SFS 差距·从作者档 + surface 直接写初版 skill·破
    chicken-egg：复刻需 skill_v0·SFS 需复刻）。有 gap = 基于差距精化。"""
    is_v0 = not gap_text.strip()
    system = (
        "你是网文作者风格蒸馏专家。任务：产出/精化作者风格 skill（markdown 文字约束），"
        "让 gen-model 复刻更贴近该作者。\n\n"
        "🔴 铁律（守北极星⑤·不规训创作）：\n"
        "1. skill 是给**弱 gen-model** 看的可执行文字约束——越简单越好（skill 越复杂弱模型越乱）。\n"
        "2. 用**该作者的真实手法**描述（带原文证据），绝不套通用『多用短句』空话。\n"
        "3. 数值约束给**区间**（如句长均值 28-34），不给死值。\n"
        + ("4. 这是**首版 skill（v0）**：从作者风格档提炼最显著的笔法签名，全面但精炼。\n"
           if is_v0 else
           "4. 这是**精化版**：只针对差距大的维度补/改约束，差距小的别动（别过度约束）。\n")
        + "\n输出**纯 markdown**（无 JSON、无围栏标记），必须含这些小节标题：\n"
        "`## 句式与节奏` `## 段落与标点` `## 对话工艺` `## 描写与情绪` `## 反模式（绝不做）`\n"
        "每节 2-5 条可执行约束。")
    if is_v0:
        user = (
            f"## 作者风格档（第一权威·复刻目标）\n{author_block}\n\n"
            f"产出**首版 skill v{version}**（markdown·从作者档提炼笔法签名）：")
    else:
        user = (
            f"## 作者风格档（第一权威·复刻目标）\n{author_block}\n\n"
            f"## 当前 skill（v{version-1}）\n{current_skill or '（无）'}\n\n"
            f"## SFS 复刻差距报告（哪些维度复刻得不像作者·重点攻这些）\n{gap_text}\n\n"
            f"产出 skill v{version}（markdown·只攻差距维度·针对性补约束）：")
    return system, user


def _run_distill_reflect(args) -> int:
    """phase-3 修正反思：产 skill markdown。关 response_format_json·跳 JSON parse·
    换『非空 + 含必备小节』文本校验（parse_json_loose 对 markdown 必误判 block）。"""
    import llm_transport as lt
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # 作者风格档注入统一走共享模块。
    from author_profile_util import build_author_profile_block, AUTHOR_PROFILE_MISSING_GUARD

    project_root = Path(args.project) if args.project else None
    if not project_root:
        print("[ERROR] --mode distill_reflect 需要 --project", file=sys.stderr)
        return 2
    # gap-report 可空 = 首版 v0 生成（从作者档·破 chicken-egg：复刻需 skill_v0·SFS 需复刻）
    gap_text = read_text(Path(args.gap_report) if args.gap_report else None, 12000)
    current_skill = read_text(Path(args.current_skill) if args.current_skill else None, 20000)
    author_block = build_author_profile_block(project_root)
    if not author_block and args.style_ref and Path(args.style_ref).exists():
        author_block = read_text(Path(args.style_ref), 30000)
    author_missing = not author_block
    if author_missing:
        author_block = AUTHOR_PROFILE_MISSING_GUARD

    version = args.skill_version    # argparse default=1 兜底·`or 1` 会把合法 0(v0) 误当 1
    system, user = build_distill_reflect_prompt(
        gap_text=gap_text, current_skill=current_skill,
        author_block=author_block, version=version)
    if args.dry_run:
        print("=== SYSTEM ===\n" + system + "\n\n=== USER ===\n" + user)
        return 0
    # markdown 输出·**不**传 response_format_json·**不** parse_json_loose
    # 🔴 与 volume_arc 同根：单点 gen-model 调用偶发空/缺小节（限速/抖动），直接 block exit 1
    # 会中断整条蒸馏 plan。加重试≤3 次自愈·真破损才 block
    # （结构破损是传输/格式问题，不是创作判断，北极星⑤）。
    MAX_REFLECT_TRIES = 3
    required_sections = ("## 句式与节奏", "## 段落与标点", "## 对话工艺",
                         "## 描写与情绪", "## 反模式")
    md = None
    last_diag = "(未尝试)"
    for attempt in range(1, MAX_REFLECT_TRIES + 1):
        try:
            result = lt.generate(
                GenModelLoader(), system, user, max_tokens=12000,
                cont_msg_builder=lt.default_cont_msg, label=f"distill:reflect#{attempt}")
        except Exception as e:
            last_diag = f"gen-model 调用失败: {e}"
            print(f"[WARN] distill_reflect {last_diag}（第 {attempt}/{MAX_REFLECT_TRIES} 次）")
            continue
        cand = (result.text or "").strip()
        # 文本校验（非 JSON 顶层键）：非空 + 含必备小节（容 2 节缺失·过半缺=结构破损）
        missing = [s for s in required_sections if s not in cand]
        if len(cand) >= 200 and len(missing) <= 2:
            md = cand
            break
        last_diag = f"len={len(cand)} 缺小节 {missing}"
        print(f"[WARN] distill_reflect 输出结构破损·{last_diag}"
              f"（第 {attempt}/{MAX_REFLECT_TRIES} 次·重试中）", file=sys.stderr)
    if md is None:
        print(f"[ERROR] distill_reflect {MAX_REFLECT_TRIES} 次重试后仍 block·{last_diag}")
        return 1
    out = Path(args.out) if args.out else (project_root / f"skill_v{version}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"[gen_creative][distill_reflect] skill v{version} → {out}（{len(md)} 字"
          f"{'·作者档缺失' if author_missing else ''}）", file=sys.stderr)
    return 0


# ============ 主入口 ============
def main():
    # stdout/stderr UTF-8（Windows 默认 GBK·prompt/AUTHOR_PROFILE_MISSING_GUARD 含 ⚠/emoji
    # 直打 GBK 终端会 UnicodeEncodeError·与 frozen dispatch 同款·dev 直跑也防）
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    check_deps()
    parser = argparse.ArgumentParser(
        description='Gen-Model 创意卡与卷描述生成工具'
    )
    parser.add_argument('--mode', required=True,
                        choices=['brainstorm', 'volume_arc', 'distill_reflect'])
    parser.add_argument('--project', help='项目根路径（volume_arc 需要）')
    parser.add_argument('--out', help='输出 JSON 文件路径（默认 stdout）')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt 不调 API')

    # brainstorm 参数
    parser.add_argument('--topic', help='[brainstorm] 题材方向')
    parser.add_argument('--count', type=int, default=3, help='生成卡数（默认 3）')
    parser.add_argument('--research', help='[brainstorm] 调研缓存 md 路径')
    parser.add_argument('--style-ref', help='[brainstorm] 风格基线 skill.md 路径')

    # volume_arc 卷级大纲生成（阶段2 创建书籍）
    parser.add_argument('--selected-card', help='[volume_arc] 选中灵感卡 JSON 路径')
    parser.add_argument('--cluster-count', type=int, help='[volume_arc] 每卷故事块数（软提示）')
    parser.add_argument('--framework', help='[volume_arc] 叙事框架')
    parser.add_argument('--rhythm', help='[volume_arc] 节奏档')
    parser.add_argument('--emit-to-db', action='store_true',
                        help='[volume_arc] 拆产出落 大势卡.json + 事件簇.json')
    parser.add_argument('--volumes',
                        help='[volume_arc] 内部调试：只生成指定卷 chunk（"N" 或 "N-M"）·'
                             '不触发合并（plan 不用此参数·全量生成才合并落库）')
    # distill_reflect 参数（阶段3 phase-3 修正反思·产 skill markdown）
    parser.add_argument('--gap-report', help='[distill_reflect] style_evaluator SFS 差距报告 JSON')
    parser.add_argument('--current-skill', help='[distill_reflect] 当前 skill_vN.md 路径（可空=首版）')
    parser.add_argument('--skill-version', type=int, default=1,
                        help='[distill_reflect] 产出 skill 版本号')

    args = parser.parse_args()

    # 组装 prompt
    if args.mode == 'brainstorm':
        if not args.topic:
            print("[ERROR] --mode brainstorm 需要 --topic", file=sys.stderr)
            sys.exit(2)
        research_text = read_text(Path(args.research) if args.research else None, 30000)
        style_text = read_text(Path(args.style_ref) if args.style_ref else None, 10000)
        system, user = build_brainstorm_prompt(args.topic, args.count,
                                               research_text, style_text)
        parser_fn = parse_brainstorm_output

    elif args.mode == 'volume_arc':
        # 卷级大纲生成（阶段2 创建书籍·走 llm_transport·四硬契约·自带 emit/dry-run）
        # 实现在 gen_creative_volume_arc.py（P2 分卷 chunk 三阶段）
        from gen_creative_volume_arc import _run_volume_arc
        sys.exit(_run_volume_arc(args))

    elif args.mode == 'distill_reflect':
        # 蒸馏 phase-3 修正反思（阶段3·产 skill markdown 非 JSON）
        sys.exit(_run_distill_reflect(args))

    if args.dry_run:
        print("=== SYSTEM ===")
        print(system)
        print("\n=== USER ===")
        print(user)
        print(f"\n[dry-run] system={len(system)} chars / user={len(user)} chars")
        try:
            loader = GenModelLoader()
            p = loader.get_active_profile()
            print(f"[dry-run] active profile: {p.name} ({p.model} @ {p.base_url})")
        except GenModelConfigError as e:
            print(f"[dry-run] [WARN] active profile 未就绪: {e}", file=sys.stderr)
        return

    # 加载 gen-model
    try:
        loader = GenModelLoader()
        active = loader.get_active_profile()
    except GenModelConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    print(f"[gen_creative] active = {active.name}", file=sys.stderr)

    try:
        reply, used_profile = call_gen_model(loader, system, user)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(3)

    result = parser_fn(reply)
    result.setdefault('_meta', {})
    result['_meta'].update({
        'mode': args.mode,
        'generated_by_profile': used_profile.name,
        'generated_by_model': used_profile.model,
        'generated_at': datetime.now().isoformat(),
    })

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(output_json, encoding='utf-8')
        print(f"\n[gen_creative] 写出: {args.out}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == '__main__':
    main()
