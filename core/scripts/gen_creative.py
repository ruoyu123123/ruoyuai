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


def build_volume_arc_prompt(*, selected_card: dict, cluster_count: int,
                            framework: str, rhythm: str,
                            author_block: str, research_text: str) -> tuple[str, str]:
    """卷级大纲生成 prompt（阶段2 创建书籍·解死锁①）。

    🔴 北极星⑤铁律：system 只给**脚手架 + 字段语义 + 非约束示例 + 作者档优先**，
    **绝不硬编码 phase/finale_signal/ME 数量的枚举硬约束**（惊悚乐园 schema 把模型推成
    流水账覆辙）。模型在作者档第一权威下自由产大势/卷arc/milestones，脚本只做脚手架+parse。
    cluster_count 是**软提示**（模型按大势节奏可微调），不是硬锁。
    """
    system = f"""你是顶尖网文大纲架构师。基于给定的灵感卡，设计一本长篇网文的**卷级大势骨架**。

{author_block}

# 输出一个 JSON 对象，顶层字段（这是脚手架，不是创作约束——字段怎么填由你按作者风格+故事逻辑自由决定）：

- `story_destiny`: {{"final_image": 全书终局定格画面, "thematic_resolution": 主题落点}}
  （大势已定：无论中途怎么折腾，方向收敛到这个固定终点）
- `_metadata`: {{"rhythm_profile": "{rhythm}", "narrative_framework": "{framework}",
  "cluster_count_per_volume": {cluster_count}}}（原样回填，别改）
- `volumes`: 卷数组。每卷 = 一个**阶段触发点**（一个阶段的结束 + 下一阶段开始）：
  {{"vol": 卷号, "title": 阶段/副本名, "phase": 该阶段世界位格/主角状态的一个词,
    "volume_core_conflict": 本阶段核心任务（解决即可收卷）,
    "volume_thread": 串起本卷所有小走向的那根线索,
    "volume_finale_signal": 换卷触发（核心任务解决 + 力量跃迁/舞台转移/反派更迭 任一）}}
- `major_events`: ME 池（大势牵引方向）。每个 ME = 一个 cluster = 一个**小故事走向**（mini-movie，
  禁止单 cluster 覆盖整阶段）：
  {{"id": "ME-V<卷号>-<序>", "volume": 所属卷号, "title": 小走向,
    "is_volume_finale": 是否本卷收尾ME, "stakes_delta": 相对前一小走向的强度增量（try-fail 递增）,
    "prerequisites": [], "physical_evidence": []}}
  每卷大约 {cluster_count} 个 ME（软提示·你按大势节奏可增减），每卷末个 ME 标 is_volume_finale=true。
- `cluster_001`: 第一个故事块的详细 brief（**只详化这一个**，后续 cluster 留涌现）：
  {{"narrative_mode": "in_medias_res"（黄金三章倒叙·首块固定）,
    "scope_summary": 这个故事块讲什么,
    "scene_storyboard": [4-5 个场景。倒叙排列：scene0=强冲突/灾难开场（200字内丢出核心悬念）、
      scene1=反转/揭底、scene2+=时间序回溯、最后接回开篇。每个场景 {{"scene": 序, "summary": 场景概要}}],
    "foreshadowing_to_plant": [本块要埋的伏笔]}}
- 可选 `free_notes`: 字符串，表达作者风格档独有、上面字段装不下的卷级判断（如惯用卷间钩子手法）。

# 铁律
1. 卷长 fluid——**绝不写 target_chapter_count / 章数**。章数由后续写作自然涌现。
2. 卷 = 阶段触发点（成长/副本更迭），cluster = 阶段内小走向。stakes 递增累积成整个阶段。
3. 大势已定：volumes 的方向必须收敛到 story_destiny.final_image。
4. 风格/题材/人物/走向**全部以上方作者风格档为第一权威**；档案没规定的维度才自由发挥。
只输出 JSON（```json 围栏包裹），不要任何解释文字。"""

    card_txt = json.dumps(selected_card, ensure_ascii=False, indent=2)
    user = f"""# 选中的灵感卡（据此展开全卷大势）
```json
{card_txt}
```
"""
    if research_text:
        user += f"\n# 调研背景（可参考）\n{research_text[:8000]}\n"
    user += (f"\n# 参数\n叙事框架：{framework}\n节奏档：{rhythm}\n"
             f"每卷故事块数（软提示）：{cluster_count}\n\n现在设计全卷大势骨架。")
    return system, user


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


# ============ volume_arc 卷级大纲生成（阶段2 创建书籍·走 llm_transport·四硬契约）============
def _emit_volume_arc_to_db(project_root: Path, data: dict, *,
                           rhythm: str = "", framework: str = "") -> tuple[Path, Path]:
    """把模型产出拆成 大势卡.json + 事件簇.json 原子落盘（确定性平铺·不靠模型写 schema 形状）。

    rhythm/framework：用户 pause 答案（CLI 透传）——确定性写 _metadata + 用户偏好.json
    （轮次4 契约审计抓出：cluster-write step6 data_flow <rhythm> 读 用户偏好.json.rhythm_
    profile·此前无任何 producer 写它 → 用户选「紧凑」被静默丢弃·splitter 永远收「标准」）。"""
    db = project_root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    # producer 补齐（轮次6 深检：同名字段多 consumer 源须**全**覆盖·轮次4 只补了半边）：
    #   用户偏好.json    → splitter（cluster-write step6 data_flow）
    #   叙事节拍器.json  → narrator_calibrate → writer manifest storyteller_directive
    #   进度.json        → finalize_book._read_rhythm_profile
    if rhythm:
        for fname, extra in (("用户偏好.json", {"narrative_framework": framework}),
                             ("叙事节拍器.json", {}),
                             ("进度.json", {})):
            fpath = db / fname
            doc = {}
            if fpath.exists():
                try:
                    doc = json.loads(fpath.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    doc = {}
            if not isinstance(doc, dict):
                continue
            doc["rhythm_profile"] = rhythm
            for k, v in extra.items():
                if v:
                    doc[k] = v
            fpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        # _metadata 确定性覆盖（不靠 LLM 自觉回写）
        md = data.setdefault("_metadata", {})
        if isinstance(md, dict):
            md["rhythm_profile"] = rhythm
            if framework:
                md["narrative_framework"] = framework
    # 大势卡.json：story_destiny + _metadata + volumes + major_events（+ 静态 schema 锚点）
    major = {
        "_schema": "major_events_v21_phase", "schema_version": "v27",
        "_doc": "卷=阶段触发点·ME池=本卷小走向候选(每ME=1cluster)·每ME标volume:N·卷末ME标"
                "is_volume_finale·卷长fluid不锁章。",
        "_metadata": data.get("_metadata", {}),
        "story_destiny": data.get("story_destiny", {}),
        "volumes": data.get("volumes", []),
        "major_events": [{**me, "status": me.get("status", "pending")}
                         for me in data.get("major_events", [])],
    }
    # 事件簇.json：只详化 clusters[0]=cluster_001（其余留涌现）
    c1 = data.get("cluster_001") or {}
    cluster = {
        "_schema": "event_clusters", "schema_version": "v2.cluster",
        "_doc": "只详化 cluster_001(黄金三章倒叙)·后续 cluster 留 cluster_emergence_engine 涌现。",
        "clusters": [{
            "cluster_id": "cluster_001",
            "status": "pending",   # step6 cluster_choice_apply 改 in_progress（注入-gate 白名单）
            "narrative_mode": c1.get("narrative_mode", "in_medias_res"),
            "scope_summary": c1.get("scope_summary", ""),
            "scene_storyboard": c1.get("scene_storyboard", []),
            "foreshadowing_to_plant": c1.get("foreshadowing_to_plant", []),
            "parent_me": c1.get("parent_me", "ME-V1-01"),
        }],
    }
    p_major = db / "大势卡.json"
    p_cluster = db / "事件簇.json"
    p_major.write_text(json.dumps(major, ensure_ascii=False, indent=2), encoding="utf-8")
    p_cluster.write_text(json.dumps(cluster, ensure_ascii=False, indent=2), encoding="utf-8")
    return p_major, p_cluster


def _run_volume_arc(args) -> int:
    """卷级大纲生成：四硬契约·走 llm_transport(双协议+截断续写+作者档注入)·block 失败非零退出。"""
    import llm_transport as lt
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from judge_runner import build_author_profile_block, AUTHOR_PROFILE_MISSING_GUARD

    project_root = Path(args.project) if args.project else None
    if not project_root:
        print("[ERROR] --mode volume_arc 需要 --project", file=sys.stderr)
        return 2
    selected_card = {}
    if args.selected_card and Path(args.selected_card).exists():
        try:
            raw = json.loads(Path(args.selected_card).read_text(encoding="utf-8"))
            # answer_artifact 形如 {"answer": <card>} 或直接 card
            selected_card = raw.get("answer", raw) if isinstance(raw, dict) else raw
        except (OSError, json.JSONDecodeError):
            pass

    # 契约1：作者档第一权威（project 的 + --style-ref 的双重兜底）
    author_block = build_author_profile_block(project_root)
    if not author_block and args.style_ref and Path(args.style_ref).exists():
        author_block = read_text(Path(args.style_ref), 30000)
    author_missing = not author_block
    if author_missing:
        author_block = AUTHOR_PROFILE_MISSING_GUARD

    research_text = read_text(Path(args.research) if args.research else None, 8000)
    system, user = build_volume_arc_prompt(
        selected_card=selected_card, cluster_count=args.cluster_count or 10,
        framework=args.framework or "自定义", rhythm=args.rhythm or "标准",
        author_block=author_block, research_text=research_text)

    if args.dry_run:
        print("=== SYSTEM ===\n" + system + "\n\n=== USER ===\n" + user)
        print(f"\n[dry-run] volume_arc system={len(system)}/user={len(user)} chars",
              file=sys.stderr)
        return 0

    # 契约2：截断走续写不整发重试；契约3：parse 彻底失败 block → 非零退出
    # 🔴 真机 e2e 抓修 2026-06-15：volume_arc 是建书单点 gen-model 调用·偶发返回非 JSON 或被
    # 中转站限速截断 → parse 失败。实测同 prompt 第一次炸第二次过(瞬时根因)。原「单次失败直接
    # block exit 1」逼用户手动 --resume——对 GUI 非技术用户建书致命(不懂 --resume)。加 parse-
    # 失败重试(≤MAX 次)让偶发抖动自愈·真确定性破损才 block(运行时自学习「失败必记录学习+
    # adaptive retry」精神·非干涉创作——结构破损是传输/格式问题不是创作判断)。
    MAX_VOL_ARC_TRIES = 3
    data = None
    last_diag = "(未尝试)"
    for attempt in range(1, MAX_VOL_ARC_TRIES + 1):
        try:
            result = lt.generate(
                GenModelLoader(), system, user, max_tokens=24000,
                response_format_json=True, cont_msg_builder=lt.default_cont_msg,
                label=f"gen_outline:volume_arc#{attempt}")
        except Exception as e:
            last_diag = f"gen-model 调用失败: {e}"
            print(f"[WARN] volume_arc {last_diag}（第 {attempt}/{MAX_VOL_ARC_TRIES} 次）",
                  file=sys.stderr)
            continue
        cand = lt.parse_json_loose(result.text)
        # 结构校验（顶层键存在·非内容——枚举不反向规训创作）
        missing = [k for k in ("story_destiny", "volumes", "major_events", "cluster_001")
                   if k not in cand]
        if not cand.get("_parse_failed") and not missing:
            data = cand
            break
        last_diag = (f"finish={getattr(result, 'finish_reason', None)} "
                     f"text_len={len(getattr(result, 'text', '') or '')} "
                     f"parse_failed={bool(cand.get('_parse_failed'))} missing={missing}")
        # 诊断 dump（最后一次 raw 可追溯·真机 e2e 抓修·区分截断 vs 非 JSON vs 缺键）
        try:
            dp = project_root / "_数据库" / ".wal" / "volume_arc_block_debug.txt"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(f"attempt={attempt}/{MAX_VOL_ARC_TRIES} {last_diag}\n"
                          f"--- raw gen-model text ---\n{result.text}", encoding="utf-8")
        except OSError:
            pass
        print(f"[WARN] volume_arc 输出结构破损·{last_diag}"
              f"（第 {attempt}/{MAX_VOL_ARC_TRIES} 次·重试中）", file=sys.stderr)
    if data is None:
        print(f"[ERROR] volume_arc {MAX_VOL_ARC_TRIES} 次重试后仍 block·{last_diag}"
              f"·raw 见 _数据库/.wal/volume_arc_block_debug.txt", file=sys.stderr)
        return 1
    if author_missing:
        data["_author_profile_missing"] = True

    if args.emit_to_db:
        pm, pc = _emit_volume_arc_to_db(project_root, data,
                                        rhythm=args.rhythm or "",
                                        framework=args.framework or "")
        print(f"[gen_creative][volume_arc] 大势卡 {len(data.get('volumes', []))} 卷 / "
              f"{len(data.get('major_events', []))} ME → {pm.name} + {pc.name}",
              file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


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
    """phase-3 修正反思：产 skill markdown。must_fix#5：关 response_format_json·跳 JSON parse·
    换『非空 + 含必备小节』文本校验（parse_json_loose 对 markdown 必误判 block）。"""
    import llm_transport as lt
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from judge_runner import build_author_profile_block, AUTHOR_PROFILE_MISSING_GUARD

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

    version = args.skill_version    # argparse default=1 兜底·`or 1` 会把合法 0(v0) 当 1（轮次8）
    system, user = build_distill_reflect_prompt(
        gap_text=gap_text, current_skill=current_skill,
        author_block=author_block, version=version)
    if args.dry_run:
        print("=== SYSTEM ===\n" + system + "\n\n=== USER ===\n" + user)
        return 0
    # must_fix#5：markdown 输出·**不**传 response_format_json·**不** parse_json_loose
    # 🔴 真机 e2e 同类加固 2026-06-15：与 volume_arc 同根（单点 gen-model 调用偶发空/缺小节·
    # 限速/抖动直接 block exit 1 逼用户 --resume·对 GUI 非技术用户蒸馏致命）。加重试≤3 次自愈·
    # 真破损才 block（运行时自学习「一处 incident 推广同类预防」·北极星⑤结构破损非创作判断）。
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
            print(f"[WARN] distill_reflect {last_diag}（第 {attempt}/{MAX_REFLECT_TRIES} 次）",
                  file=sys.stderr)
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
        print(f"[ERROR] distill_reflect {MAX_REFLECT_TRIES} 次重试后仍 block·{last_diag}",
              file=sys.stderr)
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
        description='Gen-Model 创意卡 / 角色样本 / 卷描述生成工具'
    )
    parser.add_argument('--mode', required=True,
                        choices=['brainstorm', 'outline_card', 'voice_sample',
                                 'volume_arc', 'world_entry', 'distill_reflect'])
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
    parser.add_argument('--volume', type=int, help='[volume_arc] 卷号（旧·未用）')
    parser.add_argument('--structure', help='[volume_arc] 卷骨架 JSON 路径（旧·未用）')
    parser.add_argument('--entry-id', help='[world_entry] 世界观条目 id')
    parser.add_argument('--keywords', help='[world_entry] 触发词逗号分隔')
    # volume_arc 卷级大纲生成（阶段2 创建书籍）
    parser.add_argument('--selected-card', help='[volume_arc] 选中灵感卡 JSON 路径')
    parser.add_argument('--cluster-count', type=int, help='[volume_arc] 每卷故事块数（软提示）')
    parser.add_argument('--framework', help='[volume_arc] 叙事框架')
    parser.add_argument('--rhythm', help='[volume_arc] 节奏档')
    parser.add_argument('--emit-to-db', action='store_true',
                        help='[volume_arc] 拆产出落 大势卡.json + 事件簇.json')
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

    elif args.mode == 'volume_arc':
        # 卷级大纲生成（阶段2 创建书籍·走 llm_transport·四硬契约·自带 emit/dry-run）
        sys.exit(_run_volume_arc(args))

    elif args.mode == 'distill_reflect':
        # 蒸馏 phase-3 修正反思（阶段3·产 skill markdown 非 JSON·must_fix#5）
        sys.exit(_run_distill_reflect(args))

    elif args.mode in ('voice_sample', 'world_entry'):
        print(f"[ERROR] mode '{args.mode}' 是 v2 placeholder，待实现", file=sys.stderr)
        print(f"  当前请用 Claude sub-agent 流程替代（distill-character / worldbuild）",
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
