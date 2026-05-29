#!/usr/bin/env python3
"""
gen_creative.py — Gen-Model 创意卡 / 角色样本 / 卷描述生成工具

把「含创意笔触」的输出从 Claude 主代理迁到当前 active gen-model profile。
Claude 主代理负责准备 brief（题材/调研缓存/角色骨架），调本工具生成正文段，再接收 JSON 展示给用户。

五种 mode（v1 实现 2 个，其他 3 个 placeholder）：

  --mode brainstorm    生成 N 张灵感卡（开书用，配合 /write 命令）                    [v1 ✓]
  --mode outline_card  生成下一章 N 张走向卡（每 save-state 后展示）                 [v1 ✓]
  --mode voice_sample  生成角色 voice_pack.style_samples（配合 /distill-character）  [v2 TODO]
  --mode volume_arc    生成卷的 arc / 大事件创意描述（配合 /outline）                 [v2 TODO]
  --mode world_entry   生成世界观条目 content（世界观子系统，经 /db 或 cluster-save-state） [v2 TODO]

用法示例：

  # 灵感卡（开书）
  python core/scripts/gen_creative.py --mode brainstorm \\
    --topic "末世/沙盒/山海经" --count 3 \\
    --research <调研缓存 md 路径> \\
    --style-ref <风格 skill md 路径>

  # 走向卡（每 save-state 后）
  python core/scripts/gen_creative.py --mode outline_card \\
    --project "workspace/novels/<book>" \\
    --next-chapter 5 --count 2 \\
    --skeleton <Claude 主代理列好的卡片骨架 JSON 路径>

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


# ============ MODE: outline_card（走向卡）============
def build_outline_card_prompt(skeleton: dict, count: int, hint: str,
                              project_context: str) -> tuple[str, str]:
    """主代理列好卡片骨架 JSON（结构性字段），gen-model 填正文段"""
    skeleton_text = json.dumps(skeleton, ensure_ascii=False, indent=2)[:8000]

    system = """你是长篇小说的走向卡正文填充引擎。

主代理（Claude）已根据当前章节状态列出 N 张走向卡的**结构性骨架**（path_id / 前置事件 / 解锁体系 / 关键角色 等），
但**含创意笔触的字段（hook / scene_anchor / cliffhanger / emotion_anchor / description）留空**给你填。

你的任务：为每张走向卡填充创意笔触字段，输出完整卡片 JSON。

# 走向卡硬约束

1. **保留骨架所有结构字段**（path_id / prerequisites / unlocks / characters 等），不要改
2. **只填创意字段**（hook / scene_anchor / cliffhanger / emotion_anchor / description / one_liner_summary）
3. **创意字段必须紧扣骨架**（hook 反映 path_id 选择的差异点，cliffhanger 暗示 unlocks 的解锁）
4. **每张卡差异化**（不同 path_id = 不同立意，不要写成同一卡的措辞变种）
5. **冷峻观察者 voice**（默认基线 · 示例参考《饲养全人类》；项目蒸馏后由 manifest 注入对应 voice 覆盖）
6. **字数控制**：hook ≤ 40 字，scene_anchor ≤ 80 字，cliffhanger ≤ 30 字，description ≤ 150 字

# 输出格式

严格输出 JSON：

{
  "version": 1,
  "for_chapter": <next_chapter_number>,
  "cards": [
    {
      "card_id": "A",
      "...所有骨架字段（原样保留）...": "...",
      "hook": "开篇钩子（紧扣 path 差异点）",
      "scene_anchor": "本章核心场景描述",
      "cliffhanger": "章末悬念（暗示 unlocks）",
      "emotion_anchor": "情绪锚点",
      "description": "整卡的整体描述（≤150字）",
      "one_liner_summary": "一句话总结（≤30字，用户选卡用）"
    },
    ...
  ]
}

不要写解释，直接输出 JSON。
"""

    user = f"""# 项目上下文

{project_context[:4000]}

# 卡片骨架（主代理已列结构，你只填创意字段）

```json
{skeleton_text}
```

# 用户/主代理指令（可选 hint）

{hint or '（无）'}

# 任务

为 **{count}** 张卡填充 hook / scene_anchor / cliffhanger / emotion_anchor / description / one_liner_summary 字段。
保留所有骨架结构字段不动，输出完整卡片 JSON。
"""
    return system, user


def parse_outline_card_output(reply: str) -> dict:
    return _parse_json_loose(reply, fallback={"version": 1, "cards": [], "_raw": reply[:2000]})


# ============ MODE: voice_sample / volume_arc / world_entry（v2 placeholder） ============
def build_voice_sample_prompt(character_id: str, history_quotes: str,
                              count: int) -> tuple[str, str]:
    """v2 TODO: 根据角色历史对话生成 style_samples"""
    raise NotImplementedError(
        "mode 'voice_sample' 待实现 (v2)；"
        "目前请用 /distill-character agent 蒸馏现有对话"
    )


def build_volume_arc_prompt(volume_n: int, structure: dict,
                            project_context: str) -> tuple[str, str]:
    """v2 TODO: 生成卷 arc 创意描述"""
    raise NotImplementedError(
        "mode 'volume_arc' 待实现 (v2)；"
        "目前请用主代理在 /outline 流程中手动起 brief，或日后用本 mode 自动化"
    )


def build_world_entry_prompt(entry_id: str, keywords: list,
                             project_context: str) -> tuple[str, str]:
    """v2 TODO: 生成世界观条目 content"""
    raise NotImplementedError(
        "mode 'world_entry' 待实现 (v2)；"
        "目前请用主代理在 /worldbuild 流程中手动起"
    )


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
            print(f"[gen_creative] 调用 active: {profile.name} ({profile.model})",
                  file=sys.stderr)
            print(f"[gen_creative] max_tokens={max_tokens} (source: {mt_source})",
                  file=sys.stderr)
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[gen_creative] prompt: system={len(system)} chars, user={len(user)} chars",
              file=sys.stderr)

        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
        full_text = ""
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
            print(f"\n[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        print(f"\n[gen_creative] 接收完毕 ({len(full_text)} chars) via {profile.name}",
              file=sys.stderr)
        return full_text, profile

    raise GenModelExhaustedError(failures)


# ============ 主入口 ============
def main():
    check_deps()
    parser = argparse.ArgumentParser(
        description='Gen-Model 创意卡 / 角色样本 / 卷描述生成工具'
    )
    parser.add_argument('--mode', required=True,
                        choices=['brainstorm', 'outline_card', 'voice_sample',
                                 'volume_arc', 'world_entry'])
    parser.add_argument('--project', help='项目根路径（outline_card/voice_sample/'
                                          'volume_arc/world_entry 需要）')
    parser.add_argument('--out', help='输出 JSON 文件路径（默认 stdout）')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt 不调 API')

    # brainstorm 参数
    parser.add_argument('--topic', help='[brainstorm] 题材方向')
    parser.add_argument('--count', type=int, default=3, help='生成卡数（默认 3）')
    parser.add_argument('--research', help='[brainstorm/outline_card] 调研缓存 md 路径')
    parser.add_argument('--style-ref', help='[brainstorm] 风格基线 skill.md 路径')

    # outline_card 参数
    parser.add_argument('--next-chapter', type=int, help='[outline_card] 下一章号')
    parser.add_argument('--skeleton', help='[outline_card] 卡片骨架 JSON 路径')
    parser.add_argument('--hint', help='[outline_card] 用户/主代理可选指令')

    # voice_sample / volume_arc / world_entry 参数（v2）
    parser.add_argument('--character', help='[voice_sample] 角色 id')
    parser.add_argument('--volume', type=int, help='[volume_arc] 卷号')
    parser.add_argument('--structure', help='[volume_arc] 卷骨架 JSON 路径')
    parser.add_argument('--entry-id', help='[world_entry] 世界观条目 id')
    parser.add_argument('--keywords', help='[world_entry] 触发词逗号分隔')

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

    elif args.mode == 'outline_card':
        if not args.skeleton:
            print("[ERROR] --mode outline_card 需要 --skeleton", file=sys.stderr)
            sys.exit(2)
        skeleton = json.loads(Path(args.skeleton).read_text(encoding='utf-8'))
        project_context = ""
        if args.project:
            pr = Path(args.project)
            if (pr / "大纲.md").exists():
                project_context = read_text(pr / "大纲.md", 8000)
        system, user = build_outline_card_prompt(skeleton, args.count,
                                                 args.hint or "", project_context)
        parser_fn = parse_outline_card_output

    elif args.mode in ('voice_sample', 'volume_arc', 'world_entry'):
        print(f"[ERROR] mode '{args.mode}' 是 v2 placeholder，待实现", file=sys.stderr)
        print(f"  当前请用 Claude sub-agent 流程替代（distill-character / outline / worldbuild）",
              file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        print("=== SYSTEM ===")
        print(system)
        print("\n=== USER ===")
        print(user)
        print(f"\n[dry-run] system={len(system)} chars / user={len(user)} chars",
              file=sys.stderr)
        try:
            loader = GenModelLoader()
            p = loader.get_active_profile()
            print(f"[dry-run] active profile: {p.name} ({p.model} @ {p.base_url})",
                  file=sys.stderr)
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
