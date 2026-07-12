#!/usr/bin/env python3
"""gen_creative_volume_arc.py — volume_arc 卷级大纲生成（从 gen_creative.py 机械拆出·2026-07-07）

P2 分卷 chunk + WAL 断点续跑三阶段（借鉴 AI_NovelGenerator chunked blueprint resume·
research/open_source_writing_systems.md）：
  阶段A 全书骨架（story_destiny/volumes/cluster_001/world_seed）→ .wal/volume_arc_skeleton.json
  阶段B 逐卷 ME 池 chunk（schema 合法的部分产物）→ .wal/volume_arc_v<N>.json
  阶段C 全部卷完成后确定性合并（ME id 跨卷重复=硬报错不静默覆盖）→ 一把梭等价结构 → emit

公开 CLI 入口不变：`python core/scripts/gen_creative.py --mode volume_arc ...`
（gen_creative.main 的 volume_arc 分支调本模块 _run_volume_arc）。与其他 mode 共享的
工具函数（read_text）留在 gen_creative，由本模块 import。纯机械搬迁：不改逻辑 /
prompt 文本 / WAL 路径 / 失败语义。
"""
from __future__ import annotations
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gen_model_loader import GenModelLoader  # noqa: E402
from gen_creative import read_text  # noqa: E402  共享文件读取工具（多 mode 共用·留在 gen_creative）
from reference_pattern_extract import build_reference_patterns_block  # noqa: E402  P3 参考语料结构模式（条件注入）


# ═══════ MODE: volume_arc（卷级大纲·P2 分卷 chunk + WAL 断点续跑·2026-07-07）═══════
# 借鉴 AI_NovelGenerator chunked blueprint resume（research/open_source_writing_systems.md）：
#   阶段A 全书骨架（story_destiny/volumes/cluster_001/world_seed）→ .wal/volume_arc_skeleton.json
#   阶段B 逐卷 ME 池 chunk（schema 合法的部分产物）→ .wal/volume_arc_v<N>.json
#   阶段C 全部卷完成后确定性合并（ME id 跨卷重复=硬报错不静默覆盖）→ 一把梭等价结构 → emit
# 断点续跑：已存在且校验合法的 WAL 直接跳过（幂等）；损坏的重生成。
# 失败语义：单卷失败=整 step 失败（required 不降级），已完成卷 WAL 保留供续跑。
MAX_VOL_ARC_TRIES = 3                                # 单元（骨架/单卷）parse-失败重试上限
VOLUME_ARC_SKELETON_WAL = "volume_arc_skeleton.json"


def volume_arc_wal_name(vol_no: int) -> str:
    """单卷 ME 池 chunk 的 WAL 文件名（_数据库/.wal/ 下）。"""
    return f"volume_arc_v{vol_no}.json"


def build_volume_arc_skeleton_prompt(*, selected_card: dict, cluster_count: int,
                                     framework: str, rhythm: str,
                                     author_block: str, research_text: str,
                                     reference_patterns_block: str = "") -> tuple[str, str]:
    """卷级大纲**全书骨架**生成 prompt（阶段A·分卷 chunk 第一步·解「一把梭失败=全部重来」）。

    🔴 北极星⑤铁律：system 只给**脚手架 + 字段语义 + 非约束示例 + 作者档优先**，
    **绝不硬编码 phase/finale_signal 的枚举硬约束**（惊悚乐园 schema 把模型推成
    流水账覆辙）。模型在作者档第一权威下自由产大势/卷arc，脚本只做脚手架+parse。
    🔴 P2 分卷纪律：本次只产骨架——每卷 ME 池（major_events）由 build_volume_me_pool_prompt
    逐卷另行生成（WAL 断点续跑）；骨架输出里的 major_events 会被确定性丢弃
    （单一来源 = 卷 chunk WAL）。cluster_count 是**软提示**，不是硬锁。
    🔴 P3 参考语料结构基线（2026-07-07·借鉴 Ex3-NovelWriter Extracting）：
    reference_patterns_block 非空时注入一段「参考作品结构基线（advisory·可偏离）」——
    纯数字化结构参照（genre_storyline_patterns.json·不含任何原文句子），**非硬约束**
    （北极星⑤：大势/情节内容仍由模型按灵感卡自由创作，只给结构参照）。
    """
    ref_section = ""
    if reference_patterns_block:
        ref_section = (
            "\n# 参考作品结构基线（advisory·可偏离·非硬约束）\n"
            "下面是选定风格的参考作品的**纯统计结构指纹**（只有数字，不含任何原文内容）。"
            "它只是结构参照——卷怎么分、情节写什么仍由你按灵感卡自由创作，"
            "与本书题材不适配时尽管偏离：\n"
            f"{reference_patterns_block}\n")
    system = f"""你是顶尖网文大纲架构师。基于给定的灵感卡，设计一本长篇网文的**卷级大势骨架**。

🔴🔴 故事内容 vs 笔法 的权威分离（2026-06-28 W6 揪出污染 bug·必读）🔴🔴
- **故事内容**（题材/世界观/主角名/势力/情节走向/final_image）= **唯一来源是下方「选中的灵感卡」**。
- **作者风格档**（下方 author_block）= **只学笔法**（句长/段长/voice/signature/调性/对话风格/节奏）。
- 🔴 作者风格档里若出现任何**具体人名/地名/世界设定/情节示例**（如示例故事的角色、副本名），那是**笔法演示样本**，
  **绝对禁止**把它们当成本书的故事内容搬过来——本书的人物/世界/情节**只能**从灵感卡长出来。
  （实证翻车：诡秘风格档含「沙盒天道/燧明部/天道」示例·模型偷懒直接抄成大纲·完全无视了「钟楼守夜人」灵感卡。）

{author_block}
{ref_section}
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
- 🔴 **本次不输出 `major_events`**：每卷的 ME 池（小走向候选）之后**逐卷另行生成**（分卷 chunk +
  断点续跑）。你只需把每卷的 volume_core_conflict / volume_thread / volume_finale_signal
  写扎实，给逐卷 ME 池生成当锚。
- `cluster_001`: 第一个故事块的详细 brief（**只详化这一个**，后续 cluster 留涌现）：
  {{"narrative_mode": "in_medias_res"（黄金三章倒叙·首块固定）,
    "scope_summary": 这个故事块讲什么,
    "scene_storyboard": [4-5 个场景。倒叙排列：scene0=强冲突/灾难开场（200字内丢出核心悬念）、
      scene1=反转/揭底、scene2+=时间序回溯、最后接回开篇。每个场景 {{"scene": 序, "summary": 场景概要}}],
    "foreshadowing_to_plant": [本块要埋的伏笔],
    "research_ref": {{"cache_path": "_数据库/.research_cache/<本次灵感调研>.md",
      "anchors_used": ["本块实际采用的调研 anchor"],
      "research_topics": ["支撑本块的调研主题"],
      "researcher_confidence": 0.0-1.0}}}}
- 可选 `free_notes`: 字符串，表达作者风格档独有、上面字段装不下的卷级判断（如惯用卷间钩子手法）。
- 可选 `world_seed`: 世界演化的**最小初始条件**（只播 cluster_001 开场已存在的·后续留涌现，绝不预生成全书人物表）：
  {{"protagonist_state": {{"name": 主角名, "arc_stage": 开场阶段, "status": "alive"}},
    "factions_state": {{"<阵营key>": {{"name": 阵营名, "power": 0-100, "stability": 0-100, "wealth": 0-100, "current_focus": 当前动向}} (1-3 个核心阵营)}},
    "ripple_rules": [2-5 条 seed 因果规则·{{"id","trigger_type"(minor_event/fate_event/auto_tick),"trigger_match","ripples"}}],
    "characters": [主角 1 张·{{"id","name","role"}}],
    "relationships": [cluster_001 已可见的关系·{{"id","from","to","type"}}]}}
  （怎么填由你按故事自由决定；脚本只确定性 reshape，不规训枚举。可整体省略，由通用兜底播种器补。）

# 铁律
1. 卷长 fluid——**绝不写 target_chapter_count / 章数**。章数由后续写作自然涌现。
2. 卷 = 阶段触发点（成长/副本更迭），cluster = 阶段内小走向。stakes 递增累积成整个阶段。
3. 大势已定：volumes 的方向必须收敛到 story_destiny.final_image。
4. **笔法/语言风格**（句长/段长/voice/signature/调性）以上方作者风格档为第一权威；**故事内容**
   （题材/世界观/人物/情节/走向）以**选中的灵感卡**为唯一来源——绝不从风格档示例搬故事（见顶部🔴）。
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
             f"每卷故事块数（软提示·供你规划各卷密度）：{cluster_count}\n\n"
             f"现在设计全卷大势骨架（不含 major_events·ME 池逐卷另行生成）。")
    return system, user


def build_volume_me_pool_prompt(*, volume: dict, volumes_digest: str,
                                story_destiny: dict, cluster_count: int,
                                author_block: str, selected_card: dict,
                                prev_me_digest: str) -> tuple[str, str]:
    """单卷 ME 池生成 prompt（阶段B·每卷一次调用·产物落 volume_arc_v<N>.json WAL）。

    🔴 北极星⑤：脚手架非创作约束——每个 ME 讲什么、数量多少（软提示）由模型按故事逻辑
    自由决定；id 格式 / volume 标号 / 末个 is_volume_finale 是**结构契约**（合并去重 +
    emergence 卷内硬过滤的锚·C19 同源），非创作干涉。
    """
    vol_no = volume.get("vol")
    system = f"""你是顶尖网文大纲架构师。全书卷级骨架已定，现在为**第 {vol_no} 卷**设计本卷的 ME 池
（major_events·本卷「小故事走向」候选，每个 ME = 一个 cluster = 一个小走向）。

🔴 故事内容 vs 笔法 的权威分离（必读）：**故事内容**（人物/势力/情节走向）的唯一来源 = 下方
「选中的灵感卡 + 本卷骨架」；**作者风格档只学笔法**（调性/节奏/命名质感），风格档示例里的
人名/地名/情节**绝对禁止**搬进本书。

{author_block}

# 输出一个 JSON 对象（这是脚手架，不是创作约束——每个 ME 讲什么由你按故事逻辑自由决定）：

{{"volume": {vol_no},
  "major_events": [
    {{"id": "ME-V{vol_no}-<序>", "volume": {vol_no}, "title": 小走向,
      "is_volume_finale": 是否本卷收尾ME, "stakes_delta": 相对前一小走向的强度增量（try-fail 递增）,
      "prerequisites": [], "physical_evidence": []}}
  ]}}

# 铁律
1. 每个 ME = 一个小走向（mini-movie/sub-arc），**禁止单 ME 覆盖整卷/整副本**。
2. 本卷大约 {cluster_count} 个 ME（软提示·你按大势节奏可增减），**末个 ME 标 is_volume_finale=true**
   （卷末高烈度转折/强钩·禁平稳收束）。
3. 结构契约：id 用 ME-V{vol_no}-<序> 格式、volume 填 {vol_no}——绝不与其他卷的 ME 撞 id。
4. stakes_delta 逐 ME 递增累积，service 本卷 volume_thread、收敛到本卷 volume_finale_signal，
   全书方向收敛到 story_destiny.final_image（大势已定）。
5. 卷长 fluid——**绝不写 target_chapter_count / 章数**。章数由后续写作自然涌现。
只输出 JSON（```json 围栏包裹），不要任何解释文字。"""

    card_txt = json.dumps(selected_card, ensure_ascii=False, indent=2)
    vol_txt = json.dumps(volume, ensure_ascii=False, indent=2)
    sd_txt = json.dumps(story_destiny, ensure_ascii=False, indent=2)
    user = f"""# 选中的灵感卡（故事内容唯一来源）
```json
{card_txt}
```

# 全书终局（大势已定·所有卷方向收敛于此）
```json
{sd_txt}
```

# 全书卷级骨架（本卷在整体中的位置）
{volumes_digest}

# 本卷骨架（为它设计 ME 池）
```json
{vol_txt}
```

# 前面各卷已定的小走向（衔接·stakes 跨卷递增·id 不重复）
{prev_me_digest if prev_me_digest else "（本卷之前没有已定小走向）"}

现在输出第 {vol_no} 卷的 ME 池 JSON。"""
    return system, user


# ============ ME 池结构与引用验证 ============
def _normalize_me_pool(mes: list, project_root: Path | None = None) -> dict:
    """验证每个 ME 的稳定 id、卷号、prerequisite 和唯一卷末标记。"""
    if not isinstance(mes, list):
        raise ValueError("major_events 必须是 array")
    if not mes:
        raise ValueError("major_events 不能为空")
    ids = []
    by_volume: dict[int, list[dict]] = {}
    for index, event in enumerate(mes):
        if not isinstance(event, dict):
            raise ValueError(f"major_events[{index}] 必须是 object")
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError(f"major_events[{index}].id 不能为空")
        if event_id in ids:
            raise ValueError(f"major_events id 重复: {event_id}")
        ids.append(event_id)
        volume = event.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 1:
            raise ValueError(f"ME {event_id}.volume 必须是正整数")
        if not isinstance(event.get("is_volume_finale"), bool):
            raise ValueError(f"ME {event_id}.is_volume_finale 必须是 bool")
        prerequisites = event.get("prerequisites", [])
        if not isinstance(prerequisites, list) or any(not isinstance(p, str) for p in prerequisites):
            raise ValueError(f"ME {event_id}.prerequisites 必须是 string array")
        by_volume.setdefault(volume, []).append(event)
    id_set = set(ids)
    for event in mes:
        dangling = [p for p in event.get("prerequisites", []) if p not in id_set]
        if dangling:
            raise ValueError(f"ME {event['id']} 含悬空 prerequisites: {dangling}")
    for volume, events in sorted(by_volume.items()):
        finales = [event["id"] for event in events if event["is_volume_finale"]]
        if len(finales) != 1:
            raise ValueError(f"卷{volume} 必须恰有一个 is_volume_finale=true，实际 {finales}")
    return {"validated_events": len(mes), "volumes": sorted(by_volume)}


# ============ volume_arc 卷级大纲生成（阶段2 创建书籍·走 llm_transport·四硬契约）============
def _emit_world_seed_projection(db: Path, world_seed: dict) -> list[str]:
    """🔴 2026-06-27 C02：把模型 volume_arc 产出的 world_seed 创意投影确定性 reshape 落盘。

    模型自由产内容（北极星⑤·脚本只 reshape 不规训 schema 枚举），_emit 把它平铺进：
      · 世界状态.json   → protagonist_state(主角 arc 基线) + factions_state(1-3 核心阵营 power/stability/wealth)
      · 涟漪规则.json   → ripple_rules(2-5 条 seed·模型产的因果)
      · 人物卡.json     → characters(主角 1 张)
      · 关系.json       → relationships(cluster_001 可见)

    幂等：只在目标当前为空/骨架时写（不覆盖已有内容）。world_seed_init(step5.5) 补本投影没覆盖的。
    返回写入的文件名列表（供日志）。
    """
    written: list[str] = []
    if not isinstance(world_seed, dict) or not world_seed:
        return written

    def _load(name: str) -> dict:
        p = db / name
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return d if isinstance(d, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save(name: str, doc: dict):
        (db / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        if name not in written:
            written.append(name)

    # 1) 世界状态：protagonist_state + factions_state（仅当当前为空）
    prot = world_seed.get("protagonist_state") or world_seed.get("protagonist")
    factions = world_seed.get("factions_state") or world_seed.get("factions")
    if prot or factions:
        ws = _load("世界状态.json")
        ws.setdefault("current_world_time", {"ch": 0, "cluster": "cluster_001", "day": 1})
        if isinstance(prot, dict) and not ws.get("protagonist_state"):
            ws["protagonist_state"] = {**prot, "_seeded_by": "volume_arc_emit"}
        if isinstance(factions, dict) and not ws.get("factions_state"):
            fs = {}
            for k, v in factions.items():
                if isinstance(v, dict):
                    fs[k] = {"power": 50, "stability": 50, "wealth": 50, **v,
                             "_seeded_by": "volume_arc_emit"}
            if fs:
                ws["factions_state"] = fs
        # consequence_tracker 必须是 dict（engine setdefault+字符串键·list 会 TypeError）
        if not isinstance(ws.get("consequence_tracker"), dict):
            ws["consequence_tracker"] = {}
        ws.setdefault("active_npc_threads", [])
        ws.setdefault("emergent_opportunities", [])
        _save("世界状态.json", ws)

    # 2) 涟漪规则：seed ripple_rules（仅当当前为空）
    seed_rules = world_seed.get("ripple_rules") or world_seed.get("seed_rules")
    if isinstance(seed_rules, list) and seed_rules:
        rr = _load("涟漪规则.json")
        if not rr.get("ripple_rules"):
            rr["ripple_rules"] = [r for r in seed_rules if isinstance(r, dict)]
            _save("涟漪规则.json", rr)

    # 3) 人物卡：主角 1 张（仅当当前为空）
    chars = world_seed.get("characters")
    if isinstance(chars, list) and chars:
        cc = _load("人物卡.json")
        if not cc.get("characters"):
            cc["characters"] = [c for c in chars if isinstance(c, dict)]
            _save("人物卡.json", cc)

    # 4) 关系：cluster_001 可见（仅当当前为空）
    rels = world_seed.get("relationships")
    if isinstance(rels, list) and rels:
        rj = _load("关系.json")
        if not rj.get("relationships"):
            rj["relationships"] = [r for r in rels if isinstance(r, dict)]
            _save("关系.json", rj)

    return written


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
                         for me in data.get("major_events", []) if isinstance(me, dict)],
    }
    major["_validation"] = _normalize_me_pool(major["major_events"], project_root)
    # 事件簇.json：只详化 clusters[0]=cluster_001（其余留涌现）
    c1 = data.get("cluster_001") or {}
    research_ref = c1.get("research_ref") if isinstance(c1, dict) else None
    if not isinstance(research_ref, dict):
        research_ref = {
            "cache_path": "_数据库/.research_cache",
            "anchors_used": [],
            "research_topics": [],
            "researcher_confidence": 0,
            "_note": "cluster_001 未返回具体调研 cache；保留调研引用骨架，后续 outline/novel-researcher 可覆盖。",
        }
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
            "research_ref": research_ref,
        }],
    }
    p_major = db / "大势卡.json"
    p_cluster = db / "事件簇.json"
    p_major.write_text(json.dumps(major, ensure_ascii=False, indent=2), encoding="utf-8")
    p_cluster.write_text(json.dumps(cluster, ensure_ascii=False, indent=2), encoding="utf-8")
    # 🔴 2026-06-27 C02：模型 world_seed 创意投影 → 世界状态/涟漪规则/人物卡/关系（仅当为空·幂等）
    seeded = _emit_world_seed_projection(db, data.get("world_seed") or {})
    if seeded:
        print(f"[_emit_volume_arc][world_seed] 投影落盘: {', '.join(seeded)}", file=sys.stderr)
    return p_major, p_cluster


# ── volume_arc 分卷 chunk + WAL 断点续跑：确定性结构层（normalize / 合并 / 单元生成）──
def _read_json_or_none(p: Path):
    """读 JSON 文件；缺/坏（含编码破损）→ None（损坏 WAL 判定入口）。"""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _normalize_skeleton(cand) -> tuple:
    """骨架输出/WAL → 校验归一（返回 (dict|None, diag)·None=结构破损须重生成）。

    确定性结构修补（C19 同源·非创作干涉）：卷缺 vol 号 → 按位置回填；骨架里混产的
    major_events 一律丢弃（ME 池单一来源 = 卷 chunk WAL）。破损判定：缺顶层键 /
    volumes 空 / 卷号无法归一为唯一正整数。
    """
    if not isinstance(cand, dict) or cand.get("_parse_failed"):
        return None, "parse_failed/非dict"
    missing = [k for k in ("story_destiny", "volumes", "cluster_001") if k not in cand]
    if missing:
        return None, f"missing={missing}"
    if not isinstance(cand.get("story_destiny"), dict) or not isinstance(cand.get("cluster_001"), dict):
        return None, "story_destiny/cluster_001 非对象"
    raw_vols = cand.get("volumes")
    if not isinstance(raw_vols, list):
        return None, "volumes 非列表"
    vols = [v for v in raw_vols if isinstance(v, dict)]
    if not vols:
        return None, "volumes 空/无合法卷对象"
    for i, v in enumerate(vols):
        try:
            n = int(v.get("vol"))
            if n < 1:
                raise ValueError
        except (TypeError, ValueError):
            v["vol"] = i + 1     # 结构回填（按位置·非创作干涉）
        else:
            v["vol"] = n
    nums = [v["vol"] for v in vols]
    if len(set(nums)) != len(nums):
        return None, f"卷号重复: {nums}"
    cand["volumes"] = vols
    cand.pop("major_events", None)   # ME 池分卷生成·骨架混产的丢弃（单一来源=chunk WAL）
    return cand, ""


def _normalize_volume_chunk(cand, vol_no: int) -> tuple:
    """单卷 chunk 输出/WAL → {"volume": N, "major_events": [...]}（(dict|None, diag)）。

    确定性结构修补：ME 缺 volume → 回填本卷号；裸串/非 dict 元素过滤（与 emit 同款守卫）。
    破损判定（→ 重试/重生成）：无合法 ME / ME 缺 id（id 是合并去重锚）/ 同 chunk 内 id
    重复 / ME 显式 volume ≠ 本卷（卷号错位）。
    """
    if isinstance(cand, list):
        mes = cand
    elif isinstance(cand, dict):
        if cand.get("_parse_failed"):
            return None, "parse_failed"
        mes = cand.get("major_events")
    else:
        return None, "非 dict/list"
    if not isinstance(mes, list):
        return None, "缺 major_events 列表"
    out = [m for m in mes if isinstance(m, dict)]
    if not out:
        return None, "major_events 无合法 ME"
    seen = set()
    for m in out:
        mid = str(m.get("id") or "").strip()
        if not mid:
            return None, "ME 缺 id（合并去重锚·必须有）"
        if mid in seen:
            return None, f"chunk 内 ME id 重复: {mid}"
        seen.add(mid)
        mv = m.get("volume")
        if mv in (None, "", 0):
            m["volume"] = vol_no
        else:
            try:
                mv_i = int(mv)
            except (TypeError, ValueError):
                return None, f"ME {mid} volume 非整数: {mv!r}"
            if mv_i != vol_no:
                return None, f"ME {mid} 卷号错位: volume={mv_i} ≠ 本卷 {vol_no}"
            m["volume"] = mv_i
    return {"volume": vol_no, "major_events": out}, ""


def _merge_volume_arc_chunks(skeleton: dict, chunks: dict) -> tuple:
    """骨架 + 全部卷 chunk → 一把梭等价结构（确定性合并·卷号升序）。

    返回 (data, dup_report)。dup_report 非空 = ME id 跨卷重复——**硬报错**（调用方 block
    非零退出·绝不静默覆盖）。
    """
    data = {k: v for k, v in skeleton.items() if k not in ("major_events", "_wal_meta")}
    merged, first_vol, dups = [], {}, []
    for n in sorted(chunks):
        for me in chunks[n]["major_events"]:
            mid = str(me.get("id"))
            if mid in first_vol:
                dups.append({"id": mid, "first_vol": first_vol[mid], "dup_vol": n})
                continue
            first_vol[mid] = n
            merged.append(me)
    data["major_events"] = merged
    return data, dups


def _volumes_digest(volumes: list) -> str:
    """全卷骨架一行摘要（chunk prompt 用·让单卷生成知道自己在整体中的位置）。"""
    return "\n".join(
        f"- V{v.get('vol')}《{v.get('title', '')}》phase={v.get('phase', '')}"
        f"·核心任务={v.get('volume_core_conflict', '')}" for v in volumes)


def _prev_me_digest(chunks: dict, upto: int, limit_titles: int = 12) -> str:
    """已完成前卷的小走向摘要（chunk prompt 用·衔接 stakes 递增 + 防跨卷撞 id）。"""
    lines = []
    for n in sorted(k for k in chunks if k < upto):
        mes = chunks[n]["major_events"]
        titles = [str(m.get("title") or m.get("id")) for m in mes]
        fin = next((str(m.get("title") or m.get("id"))
                    for m in mes if m.get("is_volume_finale")), "")
        lines.append(f"- 第 {n} 卷已定 {len(mes)} 个小走向：{'、'.join(titles[:limit_titles])}"
                     + (f"（卷末收束：{fin}）" if fin else ""))
    return "\n".join(lines)


def _parse_volumes_arg(spec, vol_nos: list) -> tuple:
    """解析 --volumes "N"/"N-M"（内部调试参数·plan 不用）→ (目标卷集合, err)。空 → 全部卷。"""
    if not spec:
        return set(vol_nos), ""
    m = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", str(spec))
    if not m:
        return set(), f"--volumes 格式非法: {spec!r}（支持 N 或 N-M）"
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if lo > hi:
        lo, hi = hi, lo
    target = {n for n in vol_nos if lo <= n <= hi}
    if not target:
        return set(), f"--volumes {spec} 不命中任何卷（骨架卷号: {sorted(vol_nos)}）"
    return target, ""


def _dump_volume_arc_debug(project_root: Path, unit: str, attempt: int, diag: str, raw: str):
    """破损诊断 dump（失败必记录学习·非静默吞证据·保留最后一次 raw 可追溯）。"""
    try:
        dp = project_root / "_数据库" / ".wal" / "volume_arc_block_debug.txt"
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(f"unit={unit} attempt={attempt}/{MAX_VOL_ARC_TRIES} {diag}\n"
                      f"--- raw gen-model text ---\n{raw}", encoding="utf-8")
    except OSError:
        pass


def _gen_volume_arc_unit(lt, project_root: Path, *, unit: str, system: str, user: str,
                         normalize, max_tokens: int):
    """单元（骨架 skeleton / 单卷 v<N>）生成：契约2 截断走续写；契约3 parse 彻底失败 block。

    🔴 真机 e2e 抓修 2026-06-15：单点 gen-model 调用偶发非 JSON/被限速截断 → parse 失败·
    实测同 prompt 第一次炸第二次过（瞬时根因）。parse-失败重试 ≤MAX_VOL_ARC_TRIES 次让偶发
    抖动自愈·真确定性破损才 block（结构破损是传输/格式问题不是创作判断）。失败返 None。
    """
    last_diag = "(未尝试)"
    for attempt in range(1, MAX_VOL_ARC_TRIES + 1):
        try:
            result = lt.generate(
                GenModelLoader(), system, user, max_tokens=max_tokens,
                response_format_json=True, cont_msg_builder=lt.default_cont_msg,
                label=f"gen_outline:volume_arc:{unit}#{attempt}")
        except Exception as e:
            last_diag = f"gen-model 调用失败: {e}"
            print(f"[WARN] volume_arc[{unit}] {last_diag}（第 {attempt}/{MAX_VOL_ARC_TRIES} 次）")
            continue
        cand = lt.parse_json_loose(result.text)
        norm, diag = normalize(cand)
        if norm is not None:
            return norm
        last_diag = (f"finish={getattr(result, 'finish_reason', None)} "
                     f"text_len={len(getattr(result, 'text', '') or '')} {diag}")
        _dump_volume_arc_debug(project_root, unit, attempt, last_diag,
                               getattr(result, 'text', '') or "")
        print(f"[WARN] volume_arc[{unit}] 输出结构破损·{last_diag}"
              f"（第 {attempt}/{MAX_VOL_ARC_TRIES} 次·重试中）", file=sys.stderr)
    print(f"[ERROR] volume_arc[{unit}] {MAX_VOL_ARC_TRIES} 次重试后仍 block·{last_diag}"
          f"·raw 见 _数据库/.wal/volume_arc_block_debug.txt", file=sys.stderr)
    return None


def _run_volume_arc(args) -> int:
    """卷级大纲生成（P2 分卷 chunk + WAL 断点续跑）：四硬契约·走 llm_transport·block 非零退出。

    阶段A 全书骨架 → .wal/volume_arc_skeleton.json；阶段B 逐卷 ME 池 → .wal/volume_arc_v<N>.json
    （schema 合法的部分产物·合法 WAL 直接跳过·损坏重生成）；阶段C 全部卷完成后确定性合并
    （ME id 跨卷重复=硬报错）→ emit 大势卡.json + 事件簇.json（最终产物与一把梭结构等价）。
    单卷失败=整 step 失败（required 不降级），已完成卷 WAL 保留供续跑。
    """
    import llm_transport as lt
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # 作者风格档注入统一走共享模块。
    from author_profile_util import build_author_profile_block, AUTHOR_PROFILE_MISSING_GUARD

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
    cluster_count = args.cluster_count or 10
    framework = args.framework or "自定义"
    rhythm = args.rhythm or "标准"
    # P3：参考语料结构基线（artifact 存在才注入·advisory 可偏离·纯数字无原文）
    reference_patterns_block = build_reference_patterns_block(project_root)

    if args.dry_run:
        system, user = build_volume_arc_skeleton_prompt(
            selected_card=selected_card, cluster_count=cluster_count,
            framework=framework, rhythm=rhythm,
            author_block=author_block, research_text=research_text,
            reference_patterns_block=reference_patterns_block)
        print("=== SYSTEM (skeleton) ===\n" + system + "\n\n=== USER (skeleton) ===\n" + user)
        print(f"\n[dry-run] volume_arc skeleton system={len(system)}/user={len(user)} chars"
              f"·ME 池逐卷 chunk 另行生成（WAL: _数据库/.wal/volume_arc_v<N>.json）")
        return 0

    wal_dir = project_root / "_数据库" / ".wal"
    wal_dir.mkdir(parents=True, exist_ok=True)

    # ── 阶段A：全书骨架（WAL 续跑：合法直接复用·损坏重生成）──
    sk_path = wal_dir / VOLUME_ARC_SKELETON_WAL
    skeleton = None
    if sk_path.exists():
        norm, diag = _normalize_skeleton(_read_json_or_none(sk_path))
        if norm is not None:
            skeleton = norm
            print(f"[gen_creative][volume_arc] 复用骨架 WAL（{len(skeleton['volumes'])} 卷·续跑）")
        else:
            print(f"[WARN] volume_arc 骨架 WAL 损坏（{diag}）→ 重生成", file=sys.stderr)
    if skeleton is None:
        system, user = build_volume_arc_skeleton_prompt(
            selected_card=selected_card, cluster_count=cluster_count,
            framework=framework, rhythm=rhythm,
            author_block=author_block, research_text=research_text,
            reference_patterns_block=reference_patterns_block)
        skeleton = _gen_volume_arc_unit(lt, project_root, unit="skeleton",
                                        system=system, user=user,
                                        normalize=_normalize_skeleton, max_tokens=24000)
        if skeleton is None:
            return 1
        sk_path.write_text(json.dumps(
            {**skeleton, "_wal_meta": {"unit": "skeleton",
                                       "generated_at": datetime.now().isoformat()}},
            ensure_ascii=False, indent=2), encoding="utf-8")

    vol_nos = [v["vol"] for v in skeleton["volumes"]]
    target, err = _parse_volumes_arg(getattr(args, "volumes", None), vol_nos)
    if err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 2

    # ── 阶段B：逐卷 ME 池 chunk（合法 WAL 跳过·损坏重生成·单卷失败=整步失败）──
    chunks, reused, generated = {}, [], []
    for n in sorted(vol_nos):
        p = wal_dir / volume_arc_wal_name(n)
        if p.exists():
            norm, diag = _normalize_volume_chunk(_read_json_or_none(p), n)
            if norm is not None:
                chunks[n] = norm
                reused.append(n)
                continue
            print(f"[WARN] {p.name} 损坏（{diag}）→ 重生成", file=sys.stderr)
        if n not in target:
            continue    # --volumes 调试子集外·不生成（缺卷时后面不合并）
        volume = next(v for v in skeleton["volumes"] if v["vol"] == n)
        system, user = build_volume_me_pool_prompt(
            volume=volume, volumes_digest=_volumes_digest(skeleton["volumes"]),
            story_destiny=skeleton.get("story_destiny") or {},
            cluster_count=cluster_count, author_block=author_block,
            selected_card=selected_card, prev_me_digest=_prev_me_digest(chunks, n))
        norm = _gen_volume_arc_unit(
            lt, project_root, unit=f"v{n}", system=system, user=user,
            normalize=lambda cand, _n=n: _normalize_volume_chunk(cand, _n),
            max_tokens=12000)
        if norm is None:
            print(f"[ERROR] volume_arc 第 {n} 卷 ME 池生成失败 → 整步失败（required 不降级）·"
                  f"已完成卷 WAL 保留 {sorted(chunks)}·重跑自动续接", file=sys.stderr)
            return 1
        p.write_text(json.dumps(
            {**norm, "_wal_meta": {"unit": f"v{n}",
                                   "generated_at": datetime.now().isoformat()}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        chunks[n] = norm
        generated.append(n)

    missing_vols = [n for n in sorted(vol_nos) if n not in chunks]
    if missing_vols:
        # 只在 --volumes 调试子集下可达（全量路径缺卷已在生成失败处 return 1）
        print(f"[gen_creative][volume_arc] 部分完成 {len(chunks)}/{len(vol_nos)} 卷·"
              f"缺 {missing_vols}·未合并（--volumes 调试子集·去掉 --volumes 重跑续跑合并）")
        return 0

    # ── 阶段C：确定性合并（ME id 跨卷重复=硬报错·绝不静默覆盖）──
    data, dups = _merge_volume_arc_chunks(skeleton, chunks)
    if dups:
        for d in dups:
            print(f"[ERROR] volume_arc ME id 跨卷重复: {d['id']}"
                  f"（首现 v{d['first_vol']}·重复 v{d['dup_vol']}）", file=sys.stderr)
        for n in sorted({d["dup_vol"] for d in dups}):
            bad = wal_dir / volume_arc_wal_name(n)
            broken = wal_dir / (volume_arc_wal_name(n) + ".dup_broken")
            try:
                bad.replace(broken)
                print(f"[ERROR] 已隔离 {bad.name} → {broken.name}（重跑将重生成该卷）",
                      file=sys.stderr)
            except OSError:
                pass
        return 1

    if author_missing:
        data["_author_profile_missing"] = True

    if args.emit_to_db:
        pm, pc = _emit_volume_arc_to_db(project_root, data,
                                        rhythm=args.rhythm or "",
                                        framework=args.framework or "")
        print(f"[gen_creative][volume_arc] 大势卡 {len(data.get('volumes', []))} 卷 / "
              f"{len(data.get('major_events', []))} ME → {pm.name} + {pc.name}"
              f"（chunk 复用 {sorted(reused)}·新生成 {sorted(generated)}）")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
