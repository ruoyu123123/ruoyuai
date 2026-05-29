#!/usr/bin/env python3
"""
gen_fixer.py — Gen-Model 修复/优化工具（OpenAI 兼容 /v1/chat/completions）

用当前 active gen-model profile 修复 reflector 发现的 issue / 微调主代理标记的问题段 / 字数扩写 /
checker 输出的违规精修。

六种模式：
  --mode comprehensive       综合修 reading-reflector R1/R2 报告里所有 issue
  --mode polish              主代理亲读后小幅微调（接受 --instructions 自由文本）
  --mode word-count          单章字数 < 2500 时扩写（保 ≥ 2500）
  --mode validator-repair    按 novel-validator-checker 输出 brief.json 精修违规段落
  --mode voice-fix           按 novel-voice-checker 输出 brief.json 修对话 voice 漂移
  --mode chapter-end-rewrite 按 brief.json 重写命中的物理章末段（切章后格式修复 · 2026-05-29）

用法示例：

  # 综合修复（读 R1 报告自动定位 issue）
  python core/scripts/gen_fixer.py \\
    --project "workspace/novels/<book>" \\
    --mode comprehensive \\
    --report-file <path-to-reflector-R1.json> \\
    --files 章节/第006章/第006章.txt 章节/第007章/第007章.txt

  # 主代理亲读后微调
  python core/scripts/gen_fixer.py \\
    --project "workspace/novels/<book>" \\
    --mode polish \\
    --files 章节/第010章/第010章.txt \\
    --instructions "段 145-157 妈妈名字念第N遍 poetry-mode 模板感重，合并成散文长句"

  # 字数扩写
  python core/scripts/gen_fixer.py \\
    --project "workspace/novels/<book>" \\
    --mode word-count \\
    --files 章节/第008章/第008章.txt \\
    --target-min 2500

  # validator brief 精修（Agent 拆分后新流程）
  python core/scripts/gen_fixer.py \\
    --project "workspace/novels/<book>" \\
    --mode validator-repair \\
    --brief 章节/_quality/validator_brief_ch_006.json

  # voice brief 精修
  python core/scripts/gen_fixer.py \\
    --project "workspace/novels/<book>" \\
    --mode voice-fix \\
    --brief 章节/_quality/voice_brief_ch_006.json

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
import chapter_io as cio  # noqa: E402 · CJK 计数权威口径（统一覆盖扩展 CJK）


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
        sys.exit(2)


# ============ Max tokens 解析（共享 gen_writer 风格） ============
def load_model_capabilities_cache() -> dict:
    cache_path = Path(__file__).parent.parent.parent / '.claude' / '.model_capabilities.json'
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def resolve_max_tokens(profile: Profile) -> tuple[int, str]:
    if profile.max_tokens is not None:
        return profile.max_tokens, 'profile_explicit'
    cache = load_model_capabilities_cache()
    caps = cache.get('model_capabilities', {}).get(profile.model)
    if caps:
        return caps.get('recommended_max_tokens_for_writing', 16000), f"cache:{caps.get('source','?')}"
    return 16000, 'default_fallback_16k'


# ============ 通用修复约束（所有 mode 共享） ============
# 2026-05-29 北极星 P4 [M2-dont]：原 14 条一刀切「硬约束」会把长句/独特文风作者的对话按
# 通用爽文短句规则改坏（修复引擎变风格同质化引擎）。按 H3-write 同思路分两层（守原则5）：
# ① 常驻硬约束（穿帮/质量/格式防护，任何风格不可破）；② 风格工艺默认基线（作者风格档规定了
# 对应维度则让位——修复绝不能把作者签名笔法改成通用爽文腔）。
COMMON_HARD_RULES = """# 修复约束

**第一权威 = 本项目作者风格档**（若 prompt 含「风格 skill」/ 该作者签名笔法）。下方「二、风格工艺
默认基线」凡作者风格档规定了对应维度的，**一律以作者为准**——修复只补穿帮/质量，绝不把作者
签名笔法（句长/碎句流/签名词等）改成通用爽文腔。

## 一、常驻硬约束（穿帮 / 质量 / 格式防护 · 任何风格都不可破）
1. **不引入「。XX」段首孤立句号**（双标点 bug）
2. **不引入 AI 结构套话**：与此同时 / 值得一提的是 / 不仅如此 / 事实上（结构性机器腔，任何作者都不用）
3. **不引入真实世界时间**：禁 20XX / 公元 / 月份名 / 星期几（除非作者世界观明确是现实题材）
4. **保留原文已有的强段不动**（情感顶峰 / 关键对话 / 标志性细节）
5. **整体字数不大幅波动**（除非 mode=word-count 显式扩写）
6. **不引入章末抒情收束模板**（「他不再是 X 的那个 X 了」类 · 章末倾向钩子）

## 二、风格工艺默认基线（作者风格档规定了对应维度 → 让位以作者为准；否则按此兜底）
- 短句堆叠 / 段长（叙述段每段 ≥2 逗号 / 段长<80 字时句号 ≤2）—— **短句碎切流作者除外**
- 指示性名词 5 段窗内同 token ≤3 —— 刻意复沓手法除外
- 段首主语连续 ≤3 段相同 —— 排比手法除外
- 破折号 ≤8/千字 —— 高频破折号作者除外
- poetry-mode 短句三连 / signature 短语暴涨(单章≤2) / 元-vocab disclaimer / 否定动作三件套 节制
- 工艺签名禁用词（顿时 / 仿佛 / 淡淡 / 似乎 / 此刻 / 缓缓地说 / 沉吟片刻 / 心中一凛 / 微微挑眉）
  —— **若作者风格档/golden_passages 表明是该作者签名笔法，则保留不改**（复刻作者优先于通用反 AI 腔）
"""


# ============ 修复 prompt 组装 ============
def build_comprehensive_prompt(files: list, report_data: dict, files_content: dict) -> tuple:
    """综合修：读 reflector 报告里的 issue，对所选章节做精准修复"""
    # v22.gov.align.notrunc 全局规则：不节省 token · 全量传 issues_text 给 LLM
    issues_text = json.dumps(report_data, ensure_ascii=False, indent=2)

    files_section = []
    for fp in files:
        files_section.append(f"## {fp}\n\n```\n{files_content[fp]}\n```")
    files_blob = '\n\n'.join(files_section)

    system = f"""你是长篇小说的修复引擎。

你的任务：根据 reader-first reflector 的 issue 报告，精准修复指定章节，**不要重写全章**，只改 issue 涉及的段落。

{COMMON_HARD_RULES}

# 输出格式

对每个需修改的章节，输出**完整修复后正文**（保留章节标题第一行 `第 N 章 标题`），用以下格式包裹：

```
===FILE: <相对路径>===
<完整正文>
===END===
```

多个章节就重复以上块。不要写解释、不要 markdown 标题。
末尾输出修复总结 JSON：
```json
{{
  "issues_addressed": ["RR_R2_001", ...],
  "files_modified": ["..."],
  "estimated_word_count_delta_per_file": {{"path": +123}}
}}
```
"""

    user = f"""# Reflector 报告（待修 issue 来源）

```json
{issues_text}
```

# 待修复章节文件

{files_blob}

# 修复任务

按 reflector 报告里的 issue（critical / major 优先，minor 可豁免）精准修复以上章节。

**禁令**：
- 不重写章节情节
- 不动 reflector 标记的强项段
- 不破坏角色 voice_pack
- 不引入新 anti-slop（违反上述「一、常驻硬约束」任一 = 修复失败；风格工艺基线遵作者档优先）

现在请输出修复后的章节内容（用 `===FILE: ... ===` 包裹）+ JSON 总结。"""
    return system, user


def build_polish_prompt(files: list, instructions: str, files_content: dict) -> tuple:
    """主代理亲读后微调：自由文本指令"""
    files_section = []
    for fp in files:
        files_section.append(f"## {fp}\n\n```\n{files_content[fp]}\n```")
    files_blob = '\n\n'.join(files_section)

    system = f"""你是长篇小说的微调引擎。

主代理亲读后发现具体问题，给你自由文本指令，请按指令精准修复。**不要扩大修改范围**。

{COMMON_HARD_RULES}

# 输出格式

```
===FILE: <相对路径>===
<完整正文>
===END===
```
末尾 JSON 总结同 comprehensive 模式。
"""

    user = f"""# 主代理指令

{instructions}

# 待修复章节

{files_blob}

按指令精准修复，不破坏其他段落，不引入新 anti-slop。
"""
    return system, user


def build_word_count_prompt(files: list, files_content: dict, target_min: int, target_max: int) -> tuple:
    """字数扩写：用户硬约束单章 2500-5000，低于下限 → 扩写"""
    files_section = []
    file_stats = []
    for fp in files:
        content = files_content[fp]
        cjk = cio.count_cjk(content)  # v27 修复：统一 CJK 口径（覆盖扩展 CJK）
        files_section.append(f"## {fp} (当前 {cjk} CJK)\n\n```\n{content}\n```")
        file_stats.append(f"- {fp}: {cjk} CJK, 缺 {target_min - cjk if cjk < target_min else 0} 字")
    files_blob = '\n\n'.join(files_section)
    stats_blob = '\n'.join(file_stats)

    system = f"""你是长篇小说的字数扩写引擎。

**任务**：把低于下限的章节扩写到 [{target_min}, {target_max}] CJK。

**绝对禁令**：
- 禁止注水（重复同一意思 / 加无关风景描写 / 堆形容词）
- 禁止扩写到 reflector 已标记的强项段
- 扩写必须是**真实信息密度**：新增的内容必须承担 事件 / 角色 / 设定 / 伏笔 / 内化思考 之一

# 推荐扩写方向
- 角色内化思考
- 场景物理细节（光影 / 触感 / 气味 / 物件状态）
- 三方观察（A 看 B 看 C 的视角链）
- 伏笔种子
- 时间推进的实感

{COMMON_HARD_RULES}

# 输出格式

```
===FILE: <相对路径>===
<完整正文>
===END===
```
末尾 JSON 总结：
```json
{{
  "files_expanded": [
    {{"path": "...", "before_cjk": 1500, "after_cjk": 2600, "added_sections": ["...", "..."]}}
  ]
}}
```
"""

    user = f"""# 当前字数状态

{stats_blob}

# 目标
所有章节扩写到 [{target_min}, {target_max}] CJK 范围。

# 待扩写章节

{files_blob}

扩写后请输出完整章节（保留章节标题第一行）。
"""
    return system, user


# ============ Brief 工具（validator-repair / voice-fix 共享） ============
def render_violations_table(violations: list[dict]) -> str:
    """把 brief.violations[] 渲染成 prompt 里可读的修复清单"""
    lines = []
    for i, v in enumerate(violations, 1):
        ls = v.get('line_start', '?')
        le = v.get('line_end', '?')
        issue = v.get('issue', '(未描述)')
        hint = v.get('fix_hint', '(无)')
        orig = (v.get('original') or '')[:200]
        lines.append(
            f"### 违规 #{i} · 行 {ls}-{le}\n"
            f"- issue: {issue}\n"
            f"- fix_hint: {hint}\n"
            f"- 原文片段: 「{orig}」"
        )
    return '\n\n'.join(lines)


def build_validator_repair_prompt(brief: dict, chapter_content: str) -> tuple:
    """validator-checker 输出 brief → 精修违规段落"""
    chapter_path = brief.get('chapter_path', '')
    violations = brief.get('violations', [])
    violations_table = render_violations_table(violations)

    system = f"""你是长篇小说的违规精修引擎。

novel-validator-checker agent 已读完章节并定位所有违规段落，把违规清单（含原文片段 + issue + fix_hint）交给你。
你的任务：**精确替换违规段落**，不动其他段落。

{COMMON_HARD_RULES}

# 修复原则
- 每条违规独立修复，按 fix_hint 给的方向走
- 不重写章节情节
- 不破坏角色 voice_pack
- 修复后段落必须**自然衔接**前后文（读起来像作者本人改的，不是 AI 补丁）
- 长度大致守恒（修复段不大幅膨胀/缩短，± 30% 内）

# 输出格式

输出**完整修复后正文**（保留章节标题第一行），用以下格式包裹：

```
===FILE: <章节相对路径>===
<完整正文>
===END===
```

末尾 JSON 总结：
```json
{{
  "violations_addressed": [1, 2, 3],
  "violations_skipped": [],
  "lines_modified_per_violation": {{"1": [42, 50], "2": [73, 81]}}
}}
```
"""

    user = f"""# 违规清单（novel-validator-checker 输出）

共 {len(violations)} 条违规：

{violations_table}

# 待修复章节

`{chapter_path}`

```
{chapter_content}
```

按违规清单精修，不引入新 anti-slop，输出完整修复后正文 + JSON 总结。
"""
    return system, user


def build_voice_fix_prompt(brief: dict, chapter_content: str) -> tuple:
    """voice-checker 输出 brief → 修对话 voice 漂移"""
    chapter_path = brief.get('chapter_path', '')
    violations = brief.get('violations', [])
    violations_table = render_violations_table(violations)

    system = f"""你是长篇小说的对话声纹修复引擎。

novel-voice-checker agent 已审查所有对话，定位 voice 漂移 / tone 不一致 / POV 违规段落，把违规清单交给你。
你的任务：**精确改对话**，保持角色音域一致。

# voice-fix 专项硬约束（**最高优先级**）
- **对话才修**：违规清单里的 issue 类型 voice_drift / tone_inconsistency / pov_violation 都涉及对话或对话上下文
- **角色音域核心**：每个角色的 voice_pack（rhythm / catchphrase / sentence_avg / banned_phrases / style_samples）是唯一裁判
- **不动情节**：对话改写不能改变意图、不能删信息
- **不引入新对话**：只改原对话，不加新对话
- **保持长度大致守恒**：每条对话改后长度 ± 30%

{COMMON_HARD_RULES}

# 输出格式

```
===FILE: <章节相对路径>===
<完整正文>
===END===
```

末尾 JSON 总结：
```json
{{
  "violations_addressed": [1, 2, 3],
  "voice_changes_per_violation": {{
    "1": {{"character": "...", "before": "...", "after": "..."}}
  }}
}}
```
"""

    user = f"""# Voice 违规清单（novel-voice-checker 输出）

共 {len(violations)} 条违规（issue 类型可能含 voice_drift / tone_inconsistency / pov_violation）：

{violations_table}

# 待修复章节

`{chapter_path}`

```
{chapter_content}
```

按违规清单精修对话，保持角色音域，不动情节，输出完整修复后正文 + JSON 总结。
"""
    return system, user


def build_chapter_end_rewrite_prompt(brief: dict, chapter_content: str) -> tuple:
    """章末 anchor 修复（2026-05-29 流程贯通 · 断点 4）。

    cluster-write.md:420 step6.4 — splitter 切章后，novel-validator-checker 发现某物理章
    章末翻车（剧本体过渡 / 文学过渡 / 无锚 cliffhanger / 抒情收束），出 brief 交给本 mode 重写。
    这是切章后的物理章格式修复（合法 · 不碰 cluster_draft 正文走向，只修章末段落）。

    brief schema 复用 validator-repair（version=1 / chapter_path / violations[]），
    violations 描述章末问题（issue + fix_hint + original 章末段）。
    """
    chapter_path = brief.get('chapter_path', '')
    violations = brief.get('violations', [])
    violations_table = render_violations_table(violations)
    # brief 可带 cluster 级章末守则提示（可选）
    end_guidance = brief.get('end_rules') or (
        "章末是钩子不是收束。禁剧本体「（镜头XX）」/ 文学过渡符（*、※）/ 听觉淡出 / "
        "收束抒情句（「他不再是…的那个…了」）。章末须留具体悬念锚点（角色 + 目标/截止/筹码 ≥2 项）。"
    )

    system = f"""你是长篇小说的章末修复引擎（chapter-end-rewrite）。

splitter 已把 cluster 草稿切成物理章，novel-validator-checker 发现某些物理章的**章末段落**翻车
（剧本体过渡 / 文学过渡符 / 听觉淡出 / 无锚 cliffhanger / 抒情收束）。
你的任务：**只重写章末命中段落**，把收束改成留悬念的钩子，不动章节主体情节。

# 章末守则（最高优先级）
{end_guidance}

{COMMON_HARD_RULES}

# 修复原则
- 只改 violations 指向的章末段落，章节前面主体一字不动
- 不改情节走向 / 不删信息 / 不引入下一章才该出现的内容
- 重写后章末是**悬念钩子**：留具体未决事项（谁要做什么 / 截止 / 筹码），不抒情不总结
- 长度大致守恒（章末段 ± 30%）

# 输出格式

输出**完整修复后正文**（保留章节标题第一行），用以下格式包裹：

```
===FILE: <章节相对路径>===
<完整正文>
===END===
```

末尾 JSON 总结：
```json
{{
  "violations_addressed": [1, 2],
  "chapter_end_before": "...",
  "chapter_end_after": "..."
}}
```
"""

    user = f"""# 章末违规清单（novel-validator-checker 输出）

共 {len(violations)} 条违规（均指向物理章章末段落）：

{violations_table}

# 待修复章节

`{chapter_path}`

```
{chapter_content}
```

按违规清单只重写章末段落，改成留悬念的钩子，不动章节主体，不引入新 anti-slop，输出完整修复后正文 + JSON 总结。
"""
    return system, user


# ============ Gen-Model 调用（含 fallback 链） ============
def call_gen_model(loader: GenModelLoader, system: str, user: str) -> tuple[str, Profile]:
    """调当前 active profile；失败时按 fallback 链尝试。

    返回 (full_text, used_profile)。
    抛 GenModelExhaustedError（active + 整条 fallback 链全失败）。
    """
    from openai import OpenAI

    candidates = loader.get_callable_profiles()
    failures: list[tuple[str, str]] = []

    for i, profile in enumerate(candidates):
        max_tokens, mt_source = resolve_max_tokens(profile)
        if i == 0:
            print(f"[gen_fixer] 调用 active profile: {profile.name} "
                  f"({profile.model} @ {profile.base_url})", file=sys.stderr)
            print(f"[gen_fixer] max_tokens={max_tokens} (source: {mt_source})", file=sys.stderr)
            print(f"[gen_fixer] temperature={profile.temperature}", file=sys.stderr)
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[gen_fixer] prompt size: system={len(system)} chars, user={len(user)} chars",
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
            print(f"\n[FALLBACK] {profile.name} 调用失败: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

        print(f"\n[gen_fixer] 接收完毕 ({len(full_text)} chars) via {profile.name}",
              file=sys.stderr)
        return full_text, profile

    raise GenModelExhaustedError(failures)


# ============ 输出解析 + 应用 ============
def parse_and_apply(reply: str, project_root: Path) -> tuple:
    """从返回提取 ===FILE: ... === 块，写到对应路径"""
    pattern = re.compile(r'===FILE:\s*([^=]+?)\s*===\s*\n(.*?)\n===END===', re.DOTALL)
    files_written = []
    for m in pattern.finditer(reply):
        rel_path = m.group(1).strip()
        content = m.group(2).rstrip() + '\n'
        target = Path(rel_path)
        if not target.is_absolute():
            target = project_root / rel_path
        # 安全：校验 target 仍在 project_root 内，防 ../../ 路径穿越逃逸项目目录
        root_resolved = project_root.resolve()
        target_resolved = target.resolve()
        try:
            target_resolved.relative_to(root_resolved)
        except ValueError:
            print(f"  [跳过·路径穿越] {rel_path} 解析到项目外 ({target_resolved})，忽略该块",
                  file=sys.stderr)
            continue
        target.write_text(content, encoding='utf-8')
        cjk = cio.count_cjk(content)  # v27 修复：统一 CJK 口径
        files_written.append({'path': str(target), 'cjk': cjk})
        print(f"  [写出] {target} ({cjk} CJK)", file=sys.stderr)

    json_match = re.search(r'```json\s*\n(.*?)\n```', reply, re.DOTALL)
    summary = {}
    if json_match:
        try:
            summary = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            summary = {'_raw_json_parse_error': json_match.group(1)[:500]}

    return files_written, summary


def run_scanners(file_paths: list) -> dict:
    """对每个修改后的章节跑 scanner"""
    scanners = ['narrative_short_sentence_scanner.py', 'repeat_noun_density_scanner.py']
    scripts_dir = Path(__file__).parent
    results = {}
    for fp in file_paths:
        results[fp] = {}
        for sc in scanners:
            sc_path = scripts_dir / sc
            if not sc_path.exists():
                results[fp][sc] = {'verdict': 'SKIP'}
                continue
            # v27 修复：'python' → sys.executable（防多版本解释器调错）
            r = subprocess.run([sys.executable, str(sc_path), fp],
                               capture_output=True, text=True, encoding='utf-8')
            try:
                d = json.loads(r.stdout)
                results[fp][sc] = {'verdict': d.get('verdict'),
                                   'violations_count': d.get('violations_count')}
            except Exception as e:
                # v27 修复：静默 except 加日志（debug 友好）
                print(f"  [gen_fixer] scanner {sc} 输出解析失败 ({e})·exit={r.returncode}",
                      file=sys.stderr)
                results[fp][sc] = {'verdict': 'ERROR', 'stdout': r.stdout[:200]}
    return results


# ============ 主入口 ============
def main():
    check_deps()
    parser = argparse.ArgumentParser(description='Gen-Model 修复/优化工具（OpenAI 兼容协议）')
    parser.add_argument('--project', required=True)
    parser.add_argument('--mode', required=True,
                        choices=['comprehensive', 'polish', 'word-count',
                                 'validator-repair', 'voice-fix',
                                 'chapter-end-rewrite'])
    parser.add_argument('--files', nargs='+',
                        help='[comprehensive/polish/word-count] 待修章节文件路径')
    parser.add_argument('--report-file', help='[comprehensive] reading-reflector R*.json 路径')
    parser.add_argument('--instructions', help='[polish] 主代理自由文本指令')
    parser.add_argument('--target-min', type=int, default=2500, help='[word-count] 单章下限')
    parser.add_argument('--target-max', type=int, default=5000, help='[word-count] 单章上限')
    parser.add_argument('--brief',
                        help='[validator-repair/voice-fix] checker agent 输出的 brief JSON 路径')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt 不调 API')
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[ERROR] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    # 根据 mode 准备输入
    # 2026-05-29 流程贯通（断点 4）：chapter-end-rewrite 与 validator-repair/voice-fix 同走 --brief 通道
    if args.mode in ('validator-repair', 'voice-fix', 'chapter-end-rewrite'):
        if not args.brief:
            print(f"[ERROR] --mode {args.mode} 需要 --brief <path>", file=sys.stderr)
            sys.exit(2)
        brief_path = Path(args.brief)
        if not brief_path.is_absolute():
            brief_path = project_root / args.brief
        if not brief_path.exists():
            print(f"[ERROR] brief 不存在: {brief_path}", file=sys.stderr)
            sys.exit(2)
        brief = json.loads(brief_path.read_text(encoding='utf-8'))
        # brief schema 验证
        if brief.get('version') != 1:
            print(f"[ERROR] brief schema version 不兼容: {brief.get('version')}, 期望 1",
                  file=sys.stderr)
            sys.exit(3)
        chapter_path = brief.get('chapter_path')
        if not chapter_path:
            print(f"[ERROR] brief 缺 chapter_path 字段", file=sys.stderr)
            sys.exit(3)
        target = Path(chapter_path)
        if not target.is_absolute():
            target = project_root / chapter_path
        if not target.exists():
            print(f"[ERROR] brief 指向的章节不存在: {target}", file=sys.stderr)
            sys.exit(2)
        chapter_content = target.read_text(encoding='utf-8')
        files_content = {chapter_path: chapter_content}
        args.files = [chapter_path]  # 让后续 scanner 等流程统一

        if args.mode == 'validator-repair':
            system, user = build_validator_repair_prompt(brief, chapter_content)
        elif args.mode == 'chapter-end-rewrite':
            system, user = build_chapter_end_rewrite_prompt(brief, chapter_content)
        else:  # voice-fix
            system, user = build_voice_fix_prompt(brief, chapter_content)
    else:
        # comprehensive / polish / word-count 走原 --files
        if not args.files:
            print(f"[ERROR] --mode {args.mode} 需要 --files", file=sys.stderr)
            sys.exit(2)
        files_content = {}
        for fp in args.files:
            target = Path(fp)
            if not target.is_absolute():
                target = project_root / fp
            if not target.exists():
                print(f"[ERROR] 文件不存在: {target}", file=sys.stderr)
                sys.exit(2)
            files_content[fp] = target.read_text(encoding='utf-8')

        if args.mode == 'comprehensive':
            if not args.report_file:
                print("[ERROR] --mode comprehensive 需要 --report-file", file=sys.stderr)
                sys.exit(2)
            report = json.loads(Path(args.report_file).read_text(encoding='utf-8'))
            system, user = build_comprehensive_prompt(args.files, report, files_content)
        elif args.mode == 'polish':
            if not args.instructions:
                print("[ERROR] --mode polish 需要 --instructions", file=sys.stderr)
                sys.exit(2)
            system, user = build_polish_prompt(args.files, args.instructions, files_content)
        elif args.mode == 'word-count':
            system, user = build_word_count_prompt(args.files, files_content,
                                                   args.target_min, args.target_max)

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

    # 加载 gen-model 配置
    try:
        loader = GenModelLoader()
        active = loader.get_active_profile()
    except GenModelConfigError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    print(f"[gen_fixer] active = {active.name}", file=sys.stderr)
    chain = loader.get_fallback_chain()
    if chain:
        print(f"[gen_fixer] fallback chain = {','.join(chain)}", file=sys.stderr)

    try:
        reply, used_profile = call_gen_model(loader, system, user)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(3)

    print(f"\n[gen_fixer] 解析并应用修改...", file=sys.stderr)
    files_written, summary = parse_and_apply(reply, project_root)
    if not files_written:
        print("[WARN] 未从返回中解析出 ===FILE: ... === 块", file=sys.stderr)
        # v27 修复：时间戳加 pid 防并发冲突（feedback: 秒级时间戳不够细）
        debug_path = project_root / '章节' / f'_quality/fixer_raw_output_{datetime.now().strftime("%H%M%S")}_{os.getpid()}.txt'
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(reply, encoding='utf-8')
        print(f"  原始输出已存: {debug_path}", file=sys.stderr)
        sys.exit(3)

    print(f"\n[gen_fixer] 跑 scanner 验证...", file=sys.stderr)
    scan_results = run_scanners([f['path'] for f in files_written])
    print(f"\n=== Scanner Results ===", file=sys.stderr)
    for fp, res in scan_results.items():
        print(f"  {Path(fp).name}:", file=sys.stderr)
        for sc, v in res.items():
            print(f"    {sc}: {v}", file=sys.stderr)

    # 写修复报告
    report_path = project_root / '章节' / f'_quality/fixer_report_{args.mode}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        'mode': args.mode,
        'used_profile': used_profile.name,
        'used_model': used_profile.model,
        'files_written': files_written,
        'gen_model_summary': summary,
        'scanner_results': scan_results,
        'timestamp': datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n[gen_fixer] 报告: {report_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
