#!/usr/bin/env python3
"""
gen_writer.py — Gen-Model 正文生成器（OpenAI 兼容协议 /v1/chat/completions）

v27 freestyle 模式（默认）：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 6
  → writer 不知道目标章数 · 按 cluster.scope_summary + scene_storyboard 自由发挥
  → 字数自然涌现（splitter 后期按 3000-4500/章 切，章数由内容决定）

v26 兼容模式（显式锁字数 · 仅用于回归测试）：
  python gen_writer.py \
    --project "workspace/novels/<book>" \
    --cluster 3 \
    --chapter-start 11 --chapter-end 15 \
    --target-cjk 13000-22000

读取 manifest + 风格 skill + 调研 cache + cluster_brief + 7 项硬约束，
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


# ============ v27 freestyle helpers ============
def _infer_cluster_start_ch(project_root: Path, cluster_id: int) -> int:
    """v27 freestyle：从事件簇.json + 已写章节推导 cluster 起始章号

    优先级：
    1. 事件簇.json.clusters[N].chapter_range[0]（v26 schema · 向后兼容）
    2. 事件簇.json.clusters[N].ch_start（v27 新 schema）
    3. 上一 cluster 末章 + 1（从 章节/ 目录扫）
    4. cluster_001 = 1 兜底
    """
    db = project_root / '_数据库'
    ec_path = db / '事件簇.json'
    if ec_path.exists():
        try:
            ec = json.loads(ec_path.read_text(encoding='utf-8'))
            for c in ec.get('clusters', []):
                cid_raw = c.get('cluster_id', '')
                m = re.search(r'(\d+)', str(cid_raw))
                if m and int(m.group(1)) == cluster_id:
                    if c.get('ch_start'):
                        return int(c['ch_start'])
                    cr = c.get('chapter_range') or []
                    if cr and len(cr) >= 1:
                        return int(cr[0])
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    # 兜底：扫 章节/第NNN章/ 找最大章号 + 1
    chapters_dir = project_root / '章节'
    max_ch = 0
    if chapters_dir.exists():
        for p in chapters_dir.iterdir():
            m = re.match(r'第(\d+)章', p.name)
            if m:
                max_ch = max(max_ch, int(m.group(1)))
    if cluster_id == 1:
        return 1
    return max_ch + 1 if max_ch > 0 else 1


# ============ Prompt 组装 ============
def read_text(p: Path, limit_chars: int = None) -> str:
    if not p.exists():
        return f"[WARN] 文件不存在: {p}"
    t = p.read_text(encoding='utf-8')
    if limit_chars and len(t) > limit_chars:
        t = t[:limit_chars] + f"\n... [truncated at {limit_chars} chars]"
    return t


def build_prompt(project_root: Path, cluster_id: int, ch_start: int,
                 ch_end: int = None, target_cjk: str = None) -> tuple:
    """组装 system + user prompt

    v27 freestyle：ch_end/target_cjk 可缺省。
    - ch_end 缺 → user prompt 不暴露目标章数（writer 不知道目标章数）
    - target_cjk 缺 → 不注入「目标字数」段（让 AI 按 scope_summary 自由产出 · 自然涌现）
    - 单章 2500-5000 CJK 字数硬约束（铁律 #4）保留（splitter 切分时用，writer 写时不用预设章数）
    """
    db = project_root / '_数据库'
    freestyle = (ch_end is None)

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

    # chapter_plan：v26 兼容 = ch_start..ch_end · v27 freestyle = 不取（直接走 cluster_brief.scene_storyboard）
    progress = json.loads((db / '进度.json').read_text(encoding='utf-8'))
    plans = progress.get('chapter_plan', [])
    if freestyle:
        # v27：不限定章范围 · 取 ch_start 起所有 ≤ ch_start+30 的 plan 作为软提示
        # 章数由 cluster.scope_summary + writer 自由发挥决定
        relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_start + 30]
    else:
        relevant_plans = [p for p in plans if ch_start <= p.get('ch', 0) <= ch_end]
    plan_text = json.dumps(relevant_plans, ensure_ascii=False, indent=2) if relevant_plans else "[]（v27 freestyle · 完全按 cluster_brief.scene_storyboard 自由发挥）"

    # 人物卡
    char_card = read_text(db / '人物卡.json', 20000)

    # 用户偏好
    pref = read_text(db / '用户偏好.json', 5000)

    # v22.gov.align.fix Gap T2-X: 读 事件簇.json 找当前 cluster 的 brief 注入 prompt
    # 之前 build_prompt 完全没读 事件簇.json，导致 cluster.scope_summary 硬约束未注入 → LLM 自由发挥跑偏 task
    cluster_brief = {}
    cluster_brief_text = ""
    cluster_hard_constraints_text = ""
    try:
        ec_path = db / '事件簇.json'
        if ec_path.exists():
            ec = json.loads(ec_path.read_text(encoding='utf-8'))
            for c in ec.get('clusters', []):
                if c.get('cluster_id') == cluster_id:
                    cluster_brief = c
                    break
            if cluster_brief:
                cluster_brief_text = json.dumps(cluster_brief, ensure_ascii=False, indent=2)
                # 从 scope_summary 提取硬约束（≥/≤/百分比/角色数等）
                scope = cluster_brief.get('scope_summary', '')
                ewr = cluster_brief.get('expected_word_range', {})
                constraints = []
                if scope:
                    # 简单提硬约束关键词：「≥ N」「≤ N」「占比」「至少」「不少于」「角色」
                    import re as _re
                    for m in _re.finditer(r'(?:至少|不少于|≥)\s*(\d+)\s*([个名位章字]?)', scope):
                        n = m.group(1); unit = m.group(2) or '项'
                        constraints.append(f"- 至少 {n} {unit}（出自 scope_summary）")
                    for m in _re.finditer(r'(?:不超过|至多|≤)\s*(\d+)\s*([个名位章字]?)', scope):
                        n = m.group(1); unit = m.group(2) or '项'
                        constraints.append(f"- 至多 {n} {unit}（出自 scope_summary）")
                    for m in _re.finditer(r'(?:占比|比例)\s*[≥>]\s*(\d+)\s*%?', scope):
                        constraints.append(f"- 比例 ≥ {m.group(1)}%（出自 scope_summary）")
                    for m in _re.finditer(r'(?:占比|比例)\s*[≤<]\s*(\d+)\s*%?', scope):
                        constraints.append(f"- 比例 ≤ {m.group(1)}%（出自 scope_summary）")
                # v27：freestyle 时跳过 cluster.expected_word_range（让 AI 自由发挥）
                # v26 兼容：显式传 ch_end + cluster.expected_word_range 存在时仍注入字数硬约束
                if (not freestyle) and ewr and ewr.get('min') and ewr.get('max'):
                    constraints.append(f"- 字数硬约束：{ewr['min']}-{ewr['max']} {ewr.get('unit', 'CJK_chars')}（cluster.expected_word_range · 必遵守，写完自查不达标即重写）")
                # 额外读 cluster_brief 的 hard_constraints 字段（v22.gov.align.fix 新 schema）
                for hc in cluster_brief.get('hard_constraints', []) or []:
                    if isinstance(hc, dict):
                        metric = hc.get('metric', '?')
                        if 'min' in hc:
                            constraints.append(f"- {metric} ≥ {hc['min']}（cluster.hard_constraints · 必遵守）")
                        if 'max' in hc:
                            constraints.append(f"- {metric} ≤ {hc['max']}（cluster.hard_constraints · 必遵守）")
                if constraints:
                    cluster_hard_constraints_text = "\n".join(constraints)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[gen_writer] 事件簇.json 读取失败（不阻塞）: {e}", file=sys.stderr)

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

## 8. 【v22.gov.align.fix】CLUSTER 硬约束零容忍
user prompt 顶部「CLUSTER 硬约束」段（如有）的硬指标（角色数 / 占比 / 字数范围）**违反即硬违规**，必须重写。
特别是 `scope_summary` 描述的场景类型（如"对白场景"必有 ≥3 角色 + ≥60% 对话占比）= **硬契约**不是建议。
**测试发现**（test2 对白场景两 model 都跑成调查叙事场景）：scope_summary 不强调 = LLM 自由发挥跑偏。本条铁律消除该 gap。

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

    # v22.gov.align.fix Gap T2-X: cluster 硬约束段（顶部显著位）
    cluster_constraints_section = ""
    if cluster_brief_text:
        cluster_constraints_section = f"""## ⚠️ CLUSTER 硬约束（最高优先级 · 违反即重写）

cluster_brief 完整内容：
```json
{cluster_brief_text}
```

### 🎯 必遵守硬指标清单（从 cluster.scope_summary + expected_word_range + hard_constraints 提取）

{cluster_hard_constraints_text if cluster_hard_constraints_text else '（本 cluster 无硬约束 · 按一般指南）'}

**写完正文必自查**：上述硬指标**任一**不达标 → 视为重大失败，自查 JSON 中明确标 `cluster_constraints_violated: true`。
**scope_summary 不是参考，是契约**：cluster.scope_summary 描述的场景类型（如"对白场景"）、角色数（如"≥3 角色"）、占比（如"对话占比 ≥60%"）等是**硬契约**，不是建议。

---

"""

    # v27 freestyle vs v26 兼容：章数 + 目标字数描述
    if freestyle:
        task_intro = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**。

**🎯 v27 自由发挥模式**：
- 你**不知道**目标章数（章数由 splitter 后期按 3000-4500 CJK/章 自然切，章数由你写的内容多少决定）
- 你**不知道**目标总字数（按 cluster.scope_summary + scene_storyboard 写够即可，不必凑字数）
- **专注做对的事**：写完 cluster_brief.scope_summary 描述的所有场景 + 兑现 foreshadowing_to_plant
- 自然结尾即止 — 写完 cluster 主线就停（一般 12000-25000 CJK 是健康范围，不强求）

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。
"""
    else:
        task_intro = f"""# 写作任务

为本项目 cluster_{cluster_id:03d} 写**一整块连续叙事**（预计后续 splitter 切成 ch{ch_start}-ch{ch_end} 共 {ch_end - ch_start + 1} 章，但**你不要预先分章**）。

**目标字数**: {target_cjk} CJK（整块总字数；splitter 后每章自然落在 2500-5000）

⚠️ **重要提醒**：你输出的是**一整块叙事**，不是分好章的成品。**严禁**写「第 N 章 标题」/「——」分章符。把整个故事块当一篇长散文写，场景之间自然过渡。
"""

    user = f"""{task_intro}
{cluster_constraints_section}## chapter_plan（必落 anchors）

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

{"按 7 项硬铁律 + 元 anti-slop 防御 · 完整覆盖 cluster_brief 的所有 scene_storyboard 自由发挥（章数由 splitter 后期切，你不必管）。" if freestyle else f"按 7 项硬铁律 + 元 anti-slop 防御，写 {ch_end - ch_start + 1} 章完整故事块。"}

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
    """写 draft + changes.json

    v27 freestyle：ch_end=None 时 ch_range 写 'TBD_by_splitter'（splitter 后期填）。
    """
    draft_dir = project_root / '章节' / f'cluster_{cluster_id:03d}_draft'
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f'cluster_{cluster_id:03d}_draft.txt'
    changes_path = draft_dir / f'cluster_{cluster_id:03d}_changes.json'

    draft_path.write_text(body, encoding='utf-8')

    # 补全 changes 元数据
    cjk = len(re.findall(r'[一-鿿]', body))
    freestyle = (ch_end is None)
    ch_range_str = f'{ch_start}-TBD_by_splitter' if freestyle else f'{ch_start}-{ch_end}'
    changes.setdefault('ecas_metadata', {})
    changes['ecas_metadata'].update({
        'cluster_id': f'cluster_{cluster_id:03d}',
        'ch_start': ch_start,
        'ch_range': ch_range_str,
        'chapter_count_decided_by_splitter': freestyle,
        'generated_by': 'gen_writer.py',
        'generated_by_profile': used_profile.name,
        'generated_by_model': used_profile.model,
        'generated_at': datetime.now().isoformat(),
        'cjk_actual': cjk,
        'writer_mode': 'freestyle_v27' if freestyle else 'locked_v26',
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
    parser.add_argument('--chapter-start', type=int, default=None,
                        help='[v27 optional] cluster 起始章号 · 缺省时从事件簇.json 推导（cluster_001=1 / 后续=上 cluster 末章+1）')
    parser.add_argument('--chapter-end', type=int, default=None,
                        help='[v27 deprecated optional] cluster 结束章号 · 缺省 = freestyle 模式（writer 不知章数 · splitter 按字数切）')
    parser.add_argument('--target-cjk', default=None,
                        help='[v27 optional] 整 cluster 目标字数 · 缺省 = freestyle（按 scope_summary 自然涌现）')
    parser.add_argument('--dry-run', action='store_true', help='只输出 prompt，不调 API')
    args = parser.parse_args()

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"[ERROR] 项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(2)

    # v27：ch_start 缺省 → 从事件簇.json + 已写章节推导
    ch_start = args.chapter_start
    if ch_start is None:
        ch_start = _infer_cluster_start_ch(project_root, args.cluster)
        print(f"[gen_writer] [v27 freestyle] 推导 ch_start={ch_start} (cluster_{args.cluster:03d})",
              file=sys.stderr)

    # 模式判断 + 提示
    if args.chapter_end is None:
        print(f"[gen_writer] [v27 freestyle 模式] writer 不知目标章数 · splitter 按字数切 · 章数自然涌现",
              file=sys.stderr)
    else:
        print(f"[gen_writer] [v26 兼容模式] ch_start={ch_start} ch_end={args.chapter_end} target_cjk={args.target_cjk}",
              file=sys.stderr)

    # dry-run 模式不需要 active profile
    if args.dry_run:
        system, user = build_prompt(project_root, args.cluster, ch_start,
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

    system, user = build_prompt(project_root, args.cluster, ch_start,
                                args.chapter_end, args.target_cjk)

    try:
        reply, used_profile = call_gen_model(loader, system, user)
    except GenModelExhaustedError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(3)

    body, changes = split_text_and_changes(reply)
    draft_path, cjk = save_output(project_root, args.cluster, body, changes,
                                  ch_start, args.chapter_end, used_profile)

    print(f"\n[gen_writer] 跑 scanner...", file=sys.stderr)
    scan_results = run_scanners(draft_path)
    print(f"\n=== Scanner Results ===", file=sys.stderr)
    for sc, res in scan_results.items():
        print(f"  {sc}: {res}", file=sys.stderr)

    # 字数检查（v27 freestyle 无硬下限 · v26 兼容才校验目标）
    if args.target_cjk:
        try:
            target_min, target_max = map(int, args.target_cjk.split('-'))
            if cjk < target_min:
                print(f"\n[WARN] 字数 {cjk} < 目标下限 {target_min}", file=sys.stderr)
            elif cjk > target_max:
                print(f"\n[WARN] 字数 {cjk} > 目标上限 {target_max}", file=sys.stderr)
            else:
                print(f"\n[OK] 字数 {cjk} 在目标范围 [{target_min}, {target_max}]",
                      file=sys.stderr)
        except (ValueError, AttributeError):
            print(f"\n[gen_writer] target_cjk 解析失败，跳过字数校验: {args.target_cjk}",
                  file=sys.stderr)
    else:
        # v27 freestyle：软提示（splitter 健康区间）
        if cjk < 8000:
            print(f"\n[v27 freestyle] [HINT] cjk={cjk} 偏短 · 切 3 章可能不够（splitter 可能从下个 cluster 补料）",
                  file=sys.stderr)
        elif cjk > 30000:
            print(f"\n[v27 freestyle] [HINT] cjk={cjk} 偏长 · splitter 会切成 7+ 章",
                  file=sys.stderr)
        else:
            print(f"\n[v27 freestyle] [OK] cjk={cjk} 健康区间 8000-30000",
                  file=sys.stderr)


if __name__ == '__main__':
    main()
