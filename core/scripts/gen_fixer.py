#!/usr/bin/env python3
"""
gen_fixer.py — Gen-Model 修复/优化工具（OpenAI 兼容 /v1/chat/completions）

用当前 active gen-model profile 修复 reflector 发现的 issue / 微调主代理标记的问题段 / 字数扩写 /
checker 输出的违规精修。

五种模式：
  --mode comprehensive       综合修 reading-reflector R1/R2 报告里所有 issue
  --mode polish              主代理亲读后小幅微调（接受 --instructions 自由文本）
  --mode word-count          单章字数 < 2500 时扩写（保 ≥ 2500）
  --mode validator-repair    按 novel-validator-checker 输出 brief.json 精修违规段落
  --mode voice-fix           按 novel-voice-checker 输出 brief.json 修对话 voice 漂移

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


# ============ 通用修复硬约束（所有 mode 共享） ============
COMMON_HARD_RULES = """# 修复硬约束（所有 mode 共享，禁止违反）

1. **不引入「。XX」段首孤立句号**（双标点 bug）
2. **不引入新短句堆叠**（叙述段每段必 ≥ 2 个逗号 / 1-3 句话 / 段长 < 80 字时句号 ≤ 2）
3. **不引入新指示性名词高重复**（「那只手」「那个声音」「那种热」等 5 段窗内同 token ≤ 3）
4. **不引入新段首主语 3+ 连击**（任意角色名 / 「他」/「她」段首连续 ≥3 段相同 = 硬违规）
5. **不引入新 AI 套话**：与此同时 / 顿时 / 仿佛 / 缓缓地说 / 沉吟片刻 / 此刻 / 淡淡 / 显然 / 似乎 / 心中一凛 / 微微挑眉
6. **不引入新破折号超阈**（≤ 8/千字）
7. **不引入 poetry-mode 短句三连**：禁用「第一遍/第二遍/第三遍」「那一瞬/那一瞬/他记着那一瞬」类 3+ 段独立短段含同短语
8. **不引入 signature 短语暴涨**：voice signature 单章 ≤ 2 次
9. **不引入元-vocab disclaimer 滥用**：「他没有这个词」「他不知道这是 X」类全 cluster ≤ 2 次
10. **不引入否定动作三件套滥用**：「没说话/没出声/没接腔」单 cluster ≤ 25 次
11. **不引入章末抒情模板**：禁写「他不再是 X 的那个 X 了」类
12. **不引入真实世界时间**：禁 20XX / 公元 / 月份名 / 星期几
13. **保留原文已有的强段不动**（情感顶峰 / 关键对话 / 标志性细节）
14. **整体字数不大幅波动**（除非 mode=word-count 显式扩写）
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
- 不引入新 anti-slop（违反 14 条硬约束任一 = 修复失败）

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
                                 'validator-repair', 'voice-fix'])
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
    if args.mode in ('validator-repair', 'voice-fix'):
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
