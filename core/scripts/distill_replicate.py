#!/usr/bin/env python3
"""
distill_replicate.py — 蒸馏 phase-2/phase-5 复刻测试专用工具

强制走 gen-model（OpenAI 兼容协议外部模型），不走 Claude Code sub-agent。
这是 distill-style v22.cluster.3 起的硬约束 —— 蒸馏闭环复刻必须用最终写作
要用的 gen-model 来测，否则 skill 在 Claude 上能跑出来不代表在 gen-model 上能跑出来。

业界依据：
- distill-style.md phase-2/phase-5：复刻测试是为了验证「skill 能不能让目标 LLM 模仿」
- gen_writer.py 正式写作走 gen-model（deepseek_v4_pro / pie_xian / 等），所以蒸馏闭环
  必须用同栈，否则 v0→v1 升级针对错的模型，等于无效迭代

用法：

  python core/scripts/distill_replicate.py \\
    --style-skill workspace/styles/惊悚乐园/skill_v0.md \\
    --type opening \\
    --output workspace/styles/惊悚乐园/复刻测试/v0_round1/test_opening_replica.txt \\
    [--ref-chapter workspace/styles/惊悚乐园/原文/第001章.txt] \\
    [--target-words 1200]

  --type 取值：opening / battle / psychology / dialogue / description / transition

输出：
- 复刻文本（纯 txt，UTF-8，无 markdown 标记）
- stderr 打印 metadata（实际调用的 profile、字数、耗时）

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


def read_text(p: Path | None, limit: int = None) -> str:
    if p is None or not p.exists():
        return ""
    t = p.read_text(encoding='utf-8')
    if limit and len(t) > limit:
        t = t[:limit] + f"\n... [truncated at {limit} chars]"
    return t


# ============ 复刻 type → 创作设定 ============
TYPE_SCENARIOS = {
    "opening": {
        "label": "开篇",
        "instruction": """场景：玩家登陆一个新的恐怖游戏剧本（不能是惊悚乐园）。主角是自创角色（不能复刻原文中已有的角色名）。

写「开篇章」前 1-3 段，约 1000-1200 字。
包含：登陆瞬间的感官 → 主角 POV 自语点评 → 环境快速扫描 → 章末小钩子。""",
    },
    "battle": {
        "label": "战斗",
        "instruction": """场景：双人小队遭遇一只规则诡异的怪物（你自己设计）。自创主角（不能用原文角色名）。

写战斗段约 1000-1200 字。
包含：怪物登场 → 主角试探 → 战术分析（带吐槽）→ 反转 / 绝杀 → 战后冷却。
节奏：三拍公式（拟声→长句→短句定性）或镜头切换（仰视→全景→特写）。战后冷却用日常动作降温。""",
    },
    "psychology": {
        "label": "心理 / 推理",
        "instruction": """场景：主角独处一室，桌上有 3 件物品 + 1 张纸条 + 1 具尸体，主角通过推理破解谜题。自创主角。

写心理 / 推理段约 1000-1200 字。
包含：观察物品 → 假设 1（自语吐槽）→ 假设 2（推翻）→ 假设 3（拼图）→ 灵光一刻 → 行动。
心理描写至少用 2 种：身体外显 / 排比吐槽式宣泄 / 第三人称冷评 / 行为暗示 / 他人视角吐槽。
推理可以用自语对话化（推理时把话说出口给自己听）—— 作者的高频技法。""",
    },
    "dialogue": {
        "label": "对话",
        "instruction": """场景：主角与一个 NPC 进行心理博弈式对话（信息攻防）。自创主角和 NPC。

写对话段约 1000-1200 字。对话密度 ≥ 40%。每句对话独立成段。
NPC 有自己的语气和动机，主角用真话说假话或假话说真话。""",
    },
    "description": {
        "label": "环境描写",
        "instruction": """场景：主角进入一个全新的恐怖空间（鬼屋 / 地下室 / 废墟 任选）。自创主角。

写环境探索段约 1000-1200 字。
环境描写技法：两字锚点定场 / 感官主导单点深入 / 对话间接展现 / 最小化具象 1-2 句收笔 / 氛围暗示不明说 —— 至少用 2 种结合。
不要堆砌形容词，用具体物件和五感。""",
    },
    "transition": {
        "label": "场景转换",
        "instruction": """场景：主角刚结束一场战斗，需要从战场过渡到下一个剧情节点。自创主角。

写场景转换段约 1000-1200 字。包含：战斗余韵 → 过渡（时间跳跃 / 空间跳转 / 情绪落差 / 拟声硬切 任选）→ 新场景开启。""",
    },
}


def build_replicate_prompt(style_skill_md: str, ref_chapter: str, scenario: dict,
                           target_words: int) -> tuple[str, str]:
    """组装复刻 prompt（system + user）"""
    system = """你是一位极擅长复刻特定作者风格的写作引擎。

主代理（Claude）已蒸馏了源作者的完整 skill（含 48 维度量化基线 / 反模式 / 黄金段落 / 衔接套路）。
你的任务：严格按 skill 复刻一段约目标字数的文本，用于 SFS（Style Fingerprint Similarity）评分对照。

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

    user_parts = []
    user_parts.append("# 源作者风格 skill（必须严格遵循）\n\n" + style_skill_md)
    if ref_chapter:
        user_parts.append("# 参考章节（仅作语感参考 · 不照抄情节 / 角色 / 设定）\n\n" + ref_chapter[:3000])
    user_parts.append(
        f"# 复刻任务\n\n"
        f"**类型**：{scenario['label']}\n\n"
        f"**设定**：{scenario['instruction']}\n\n"
        f"**目标字数**：约 {target_words} CJK 字（±10%）"
    )
    user_parts.append(
        "# 输出\n\n"
        "直接输出复刻正文（纯文本，无任何 markdown 标记，无章节标题，无解释）。"
    )
    return system, "\n\n".join(user_parts)


def call_gen_model(loader: GenModelLoader, system: str, user: str,
                   default_max_tokens: int = 4000) -> tuple[str, Profile, float]:
    """调当前 active profile；失败时按 fallback 链尝试。返回 (text, profile, elapsed_seconds)"""
    from openai import OpenAI

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []

    for i, profile in enumerate(candidates):
        max_tokens = resolve_max_tokens(profile, default=default_max_tokens)
        if i == 0:
            print(f"[distill_replicate] 调用 active: {profile.name} ({profile.model})",
                  file=sys.stderr)
            print(f"[distill_replicate] max_tokens={max_tokens}, temperature={profile.temperature}",
                  file=sys.stderr)
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[distill_replicate] prompt: system={len(system)} chars, user={len(user)} chars",
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
            print(f"\n[FALLBACK] {profile.name} 失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        elapsed = time.time() - t0
        print(f"\n[distill_replicate] 接收完毕 ({len(full_text)} chars, {elapsed:.1f}s) via {profile.name}",
              file=sys.stderr)
        return full_text, profile, elapsed

    raise GenModelExhaustedError(failures)


def clean_output(text: str) -> str:
    """清理 LLM 输出：去掉 markdown 包裹、前后引言、多余空行"""
    # 去掉 ``` 代码块标记
    text = re.sub(r"^```[a-z]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n```\s*$", "", text)
    # 去掉常见 LLM 引言（"以下是"/"这是" 开头的一行）
    text = re.sub(r"^(以下是|这是|这里是|下面是)[^\n]{0,40}[:：]\s*\n", "", text)
    return text.strip()


def cjk_count(text: str) -> int:
    return sum(1 for ch in text if '一' <= ch <= '鿿')


def main():
    check_deps()
    parser = argparse.ArgumentParser(
        description="蒸馏 phase-2/phase-5 复刻（强制 gen-model）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--style-skill", required=True,
                        help="作者风格 skill .md 路径（v0/v1/v2/...）")
    parser.add_argument("--type", required=True,
                        choices=list(TYPE_SCENARIOS.keys()),
                        help="复刻类型")
    parser.add_argument("--output", required=True,
                        help="输出 txt 路径")
    parser.add_argument("--ref-chapter",
                        help="参考章节 txt 路径（可选，作语感参考）")
    parser.add_argument("--target-words", type=int, default=1200,
                        help="目标 CJK 字数（默认 1200）")
    parser.add_argument("--profile",
                        help="覆盖 active profile（默认用 .env GEN_MODEL_ACTIVE）")
    args = parser.parse_args()

    style_skill = Path(args.style_skill)
    if not style_skill.exists():
        print(f"[ERROR] 风格 skill 不存在: {style_skill}", file=sys.stderr)
        sys.exit(2)

    style_skill_md = read_text(style_skill, limit=40000)
    ref_chapter_text = read_text(Path(args.ref_chapter)) if args.ref_chapter else ""

    scenario = TYPE_SCENARIOS[args.type]
    system, user = build_replicate_prompt(style_skill_md, ref_chapter_text, scenario,
                                          args.target_words)

    loader = GenModelLoader()
    if args.profile:
        # 临时覆盖 active
        loader._active_name_override = args.profile  # noqa
    try:
        active = loader.get_active_profile()
        print(f"[distill_replicate] active profile = {active.name} ({active.model})",
              file=sys.stderr)
    except GenModelConfigError as e:
        print(f"[ERROR] gen-model 配置错误: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        reply, used_profile, elapsed = call_gen_model(loader, system, user,
                                                     default_max_tokens=4000)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] 全部 profile 失败:\n{e}", file=sys.stderr)
        sys.exit(3)

    clean = clean_output(reply)
    chars = cjk_count(clean)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(clean, encoding='utf-8')

    # 写 metadata sidecar
    meta = {
        "type": args.type,
        "label": scenario["label"],
        "style_skill": str(style_skill),
        "ref_chapter": args.ref_chapter,
        "target_words": args.target_words,
        "actual_cjk_chars": chars,
        "profile_used": used_profile.name,
        "model_used": used_profile.model,
        "temperature": used_profile.temperature,
        "elapsed_seconds": round(elapsed, 1),
        "output_path": str(output_path),
        "produced_by": "distill_replicate.py (gen-model, not Claude sub-agent)",
    }
    meta_path = output_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\n[OK] 复刻完成", file=sys.stderr)
    print(f"     输出: {output_path}", file=sys.stderr)
    print(f"     metadata: {meta_path}", file=sys.stderr)
    print(f"     CJK 字数: {chars} (target ≈ {args.target_words})", file=sys.stderr)
    print(f"     profile: {used_profile.name} / {used_profile.model}", file=sys.stderr)
    print(f"     耗时: {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
