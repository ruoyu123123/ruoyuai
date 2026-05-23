#!/usr/bin/env python3
"""
gen_writer.py — Gen-Model 正文生成器（OpenAI 兼容协议 /v1/chat/completions）

用法：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 3 \
    --chapter-start 11 --chapter-end 15 \
    --target-cjk 13000-22000

读取 manifest + 风格 skill + 调研 cache + chapter_plan + 7 项硬约束，
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


# ============ Prompt 组装 ============
def read_text(p: Path, limit_chars: int = None) -> str:
    if not p.exists():
        return f"[WARN] 文件不存在: {p}"
    t = p.read_text(encoding='utf-8')
    if limit_chars and len(t) > limit_chars:
        t = t[:limit_chars] + f"\n... [truncated at {limit_chars} chars]"
    return t


def build_prompt(project_root: Path, cluster_id: int, ch_start: int, ch_end: int, target_cjk: str) -> tuple:
    """组装 system + user prompt"""
    db = project_root / '_数据库'

    # 读取核心资料
    manifest_path = db / '.manifest' / f'ch_{ch_start:03d}.json'
    manifest = read_text(manifest_path, 30000)

    # 风格 skill
    style_skill = read_text(db / '作者风格_skill.md', 25000)

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
            cache_text = read_text(caches[0], 15000)

    # chapter_plan ch_start..ch_end
    progress = json.loads((db / '进度.json').read_text(encoding='utf-8'))
    plans = progress.get('chapter_plan', [])
    relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_end]
    plan_text = json.dumps(relevant_plans, ensure_ascii=False, indent=2)

    # 人物卡
    char_card = read_text(db / '人物卡.json', 20000)

    # 用户偏好
    pref = read_text(db / '用户偏好.json', 5000)

    # 前一章末尾（用于衔接，如果 ch_start > 1）
    prev_ch_section = ""
    if ch_start > 1:
        prev_p = project_root / '章节' / f'第{ch_start-1:03d}章' / f'第{ch_start-1:03d}章.txt'
        if prev_p.exists():
            prev_text = prev_p.read_text(encoding='utf-8')
            prev_ch_section = "## 前一章末尾（衔接用）\n\n" + prev_text[-1500:]

    # 注：以下默认风格基线仅为示例（参考《饲养全人类》冷峻俯瞰群像风），
    # 实际项目请通过 /distill-style 蒸馏并由 manifest 注入对应 skill.md 覆盖。
    system = """你是长篇小说的写作引擎。默认按「冷峻俯瞰 / 群像 / 沙盒涌现」风格写作（可被项目 skill 覆盖）。
你的任务是写一个故事块（cluster）的完整正文，覆盖多章。

# 风格基线（默认 · 示例 · 可被项目 skill 覆盖）
- 冷峻俯瞰 / 群像 / 动作 > 情绪词
- 第三人称跟随时代主角过去时
- 章末可有第一人称运营方笔注（仅当 chapter_plan 标记 narrator_annotation 时）

# 7 项硬铁律（违反任一 = 硬违规）

## 1. voice 工艺 applies_to 边界
voice_pack sentence_avg=5字 **只用于对话和章末笔注**，叙述段必须：
- 每段 ≥ 2 个逗号
- 1-3 句话/段为常态
- 段长 < 80 字时句号 ≥ 3 个 = 硬违规

错误范式：「那只手不是他的。手大。粗。指头有些是弯的，有些是直的。」
正确范式：「那只手不是他的，手大而粗，指头有些是弯的，有些是直的。」

## 2. 指示性事物名词 5 段窗口 ≤ 3 次
「那只手」「那个声音」「那种热」「那东西」「那根木头」等 5 段内同 token 不许 ≥ 4 次。

## 3. 段首主语连续 ≥ 3 次同 = 硬违规
任意角色名或代词（他/她）段首连续 ≥3 段相同 = 硬违规。
替代：省主语 / 部位代指 / 动作起头 / 物件起头 / 场景起头。

## 4. 单章字数硬约束 2500-5000 CJK
切分后每章必须 ≥ 2500 且 ≤ 5000 CJK 字。剧情自然短 → 扩写次级内容到 2500+。

## 5. 破折号 ≤ 8/千字 + 不写双标点
不写「，」。 / 。， 双标点错误。
破折号节制使用。

## 6. 真实世界时间 0 容忍
禁用 20XX 年 / 公元 / 月份名 / 星期几。用「沙盒第 N 周期」「寒月第十七日」「异常历 X 年」等。

## 7. 禁用 AI 套话
与此同时 / 值得一提的是 / 不仅如此 / 然而 / 事实上 / 顿时 / 紧锁 / 显然 / 淡淡 / 此刻 / 仿佛 / 缓缓地说 / 沉吟片刻 / 心中一凛 / 微微挑眉 / 嘴角勾起一抹

# 元 anti-slop 防御（已知重犯模式）

- **poetry-mode 短句三连**：禁用「第一遍 / 第二遍 / 第三遍」「那一瞬 / 那一瞬 / 他记着那一瞬」类 3+ 段独立短段含同短语
- **signature 短语暴涨**：voice signature 单章 ≤ 2 次，跨章不暴涨
- **元-vocab disclaimer**：「他没有这个词」「他不知道这是 X」类全 cluster ≤ 2 次
- **否定动作三件套**：「没说话/没出声/没接腔」单 cluster ≤ 25 次
- **章末抒情模板**：禁写「他不再是 X 的那个 X 了」类抒情收束

# 输出格式（DCAS 模式 · 用户明确偏好）

**核心原则：你输出的是「一整块连续叙事正文」，给后续 chapter-splitter 决定章节自然截断点的素材。**

**严禁预设章节分界**：
- ❌ 不要写「第 N 章 标题」/「第N章 标题」等章节标记
- ❌ 不要用「——」或其他分章分隔符把故事切开
- ❌ 不要写 Markdown 标题（# / ## 等）
- ❌ 不要写「以下是」/「故事开始」等元话语

**正确做法**：把整个故事块当一篇长散文写。场景之间用**自然过渡**（空行 / 时间标记句 / 视角切换句）衔接，**不打章节标签**。splitter 后期会根据自然截断点（场景结束 / 时间跳跃 / POV 切换 / 情绪峰值回落）切分章节并各自命名。

为什么这样：writer（你）擅长连续叙事的内在节奏；splitter（另一个 agent）擅长判断章节边界。预设章节边界 = writer 为「章末必须有钩子」强行设计信息炸弹结尾 = 显得刻意。让 splitter 在你写完后选自然截断点 = 章节边界看起来像页面物理限制而非刻意叙事设计。

完成正文后再输出 JSON 格式的 CHANGES 部分（用 ```json ... ``` 包裹）。
"""

    user = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**（预计后续 splitter 切成 ch{ch_start}-ch{ch_end} 共 {ch_end - ch_start + 1} 章，但**你不要预先分章**）。

**目标字数**: {target_cjk} CJK（整块总字数；splitter 后每章自然落在 2500-5000）

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。

## chapter_plan（必落 anchors）

```json
{plan_text}
```

## 人物卡（必读 voice_pack）

```json
{char_card}
```

## 风格 skill

{style_skill[:8000]}

## 调研 cache（写作前必读 synthesis）

{cache_text}

## 用户偏好

{pref}

## manifest（数据库索引）

{manifest[:8000]}

{prev_ch_section}

---

# 现在请写正文

按 7 项硬铁律 + 元 anti-slop 防御，写 {ch_end - ch_start + 1} 章完整故事块。

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
- facts_locked / foreshadowing_planted / foreshadowing_paid

现在开始写。"""

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
            print(f"[gen_writer] 调用 active profile: {profile.name} "
                  f"({profile.model} @ {profile.base_url})", file=sys.stderr)
            print(f"[gen_writer] max_tokens={max_tokens} (source: {mt_source})",
                  file=sys.stderr)
            print(f"[gen_writer] temperature={profile.temperature}", file=sys.stderr)
        else:
            print(f"\n[FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        print(f"[gen_writer] prompt size: system={len(system)} chars, user={len(user)} chars",
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
            continue  # 切下一个 profile

        # 成功
        print(f"\n[gen_writer] 接收完毕 ({len(full_text)} chars) via {profile.name}",
              file=sys.stderr)
        return full_text, profile

    # 全链失败
    raise GenModelExhaustedError(failures)


# ============ 输出解析与保存 ============
def split_text_and_changes(reply: str) -> tuple:
    """从返回拆出正文 + CHANGES JSON"""
    json_match = re.search(r'```json\s*\n(.*?)\n```\s*$', reply, re.DOTALL)
    if json_match:
        changes_json = json_match.group(1).strip()
        body = reply[:json_match.start()].rstrip()
    else:
        body = reply.strip()
        changes_json = "{}"

    # 去掉可能的 "# 正文" 这类元标题
    body = re.sub(r'^#\s*(正文|cluster.*)\s*\n', '', body, flags=re.MULTILINE)

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


def save_output(project_root: Path, cluster_id: int, body: str, changes: dict,
                ch_start: int, ch_end: int, used_profile: Profile):
    """写 draft + changes.json"""
    draft_dir = project_root / '章节' / f'cluster_{cluster_id:03d}_draft'
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f'cluster_{cluster_id:03d}_draft.txt'
    changes_path = draft_dir / f'cluster_{cluster_id:03d}_changes.json'

    draft_path.write_text(body, encoding='utf-8')

    # 补全 changes 元数据
    cjk = len(re.findall(r'[一-鿿]', body))
    changes.setdefault('ecas_metadata', {})
    changes['ecas_metadata'].update({
        'cluster_id': f'cluster_{cluster_id:03d}',
        'ch_range': f'{ch_start}-{ch_end}',
        'generated_by': 'gen_writer.py',
        'generated_by_profile': used_profile.name,
        'generated_by_model': used_profile.model,
        'generated_at': datetime.now().isoformat(),
        'cjk_actual': cjk,
    })
    changes['schema_version'] = '1.0'
    changes_path.write_text(json.dumps(changes, ensure_ascii=False, indent=2),
                            encoding='utf-8')

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
            ['python', str(sc_path), str(draft_path)],
            capture_output=True, text=True, encoding='utf-8'
        )
        try:
            d = json.loads(r.stdout)
            results[sc] = {'verdict': d.get('verdict'), 'violations_count': d.get('violations_count')}
        except Exception:
            results[sc] = {'verdict': 'ERROR', 'stdout_preview': r.stdout[:300]}
    return results


# ============ 主入口 ============
def main():
    check_deps()
    parser = argparse.ArgumentParser(description='Gen-Model 正文生成器（OpenAI 兼容协议）')
    parser.add_argument('--project', required=True, help='项目根路径')
    parser.add_argument('--cluster', type=int, required=True, help='cluster id（整数）')
    parser.add_argument('--chapter-start', type=int, required=True)
    parser.add_argument('--chapter-end', type=int, required=True)
    parser.add_argument('--target-cjk', default='13000-22000', help='目标字数范围')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt，不调 API')
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[ERROR] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    # dry-run 模式不需要 active profile
    if args.dry_run:
        system, user = build_prompt(project_root, args.cluster, args.chapter_start,
                                    args.chapter_end, args.target_cjk)
        print("=== SYSTEM PROMPT ===")
        print(system)
        print("\n=== USER PROMPT ===")
        print(user)
        print(f"\n[dry-run] system={len(system)} chars / user={len(user)} chars",
              file=sys.stderr)
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
    chain = loader.get_fallback_chain()
    if chain:
        print(f"[gen_writer] fallback chain = {','.join(chain)}", file=sys.stderr)

    system, user = build_prompt(project_root, args.cluster, args.chapter_start,
                                args.chapter_end, args.target_cjk)

    try:
        reply, used_profile = call_gen_model(loader, system, user)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(3)

    body, changes = split_text_and_changes(reply)
    draft_path, cjk = save_output(project_root, args.cluster, body, changes,
                                  args.chapter_start, args.chapter_end, used_profile)

    print(f"\n[gen_writer] 跑 scanner...", file=sys.stderr)
    scan_results = run_scanners(draft_path)
    print(f"\n=== Scanner Results ===", file=sys.stderr)
    for sc, res in scan_results.items():
        print(f"  {sc}: {res}", file=sys.stderr)

    # 字数检查
    target_min, target_max = map(int, args.target_cjk.split('-'))
    if cjk < target_min:
        print(f"\n[WARN] 字数 {cjk} < 目标下限 {target_min}", file=sys.stderr)
    elif cjk > target_max:
        print(f"\n[WARN] 字数 {cjk} > 目标上限 {target_max}", file=sys.stderr)
    else:
        print(f"\n[OK] 字数 {cjk} 在目标范围 [{target_min}, {target_max}]",
              file=sys.stderr)


if __name__ == '__main__':
    main()
