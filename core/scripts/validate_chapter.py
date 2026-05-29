#!/usr/bin/env python3
"""
validate_chapter.py — 章节硬性校验器

用法：
  python validate_chapter.py <项目路径> <章节号>          # 人类可读报告
  python validate_chapter.py <项目路径> <章节号> --json   # 结构化 JSON 报告
  例: python validate_chapter.py "示例书名" 2

流程：
  1. 定位章节 txt
  2. 加载对应 manifest（必须已由 build_manifest.py 生成）
  3. 机械扫描 7 类硬约束
  4. 输出结构化错误（stdout）+ 非零退出码

设计原则：
  - 绝不依赖 LLM 判断
  - 错误信息必须指向具体段落/行号（子代理能直接 Edit）
  - 通过 = exit 0, 有错 = exit 1, 致命 = exit 2

【v18 --json 输出契约】（audit_hub 等下游靠这个结构化读，不再正则解析人类可读报告）
  {
    "schema_version": "1.0", "scanner": "validate_chapter",
    "chapter": <int>, "chapter_file": "章节/第NNN章/第NNN章.txt",
    "passed": <bool>,
    "summary": {"fatal": N, "error": N, "warning": N, "info": N, "total": N},
    "errors": [ {"code", "severity"(fatal/error/warning/info), "msg", "fix_hint"} ]
  }
  severity 四级：fatal/error 影响 passed 与退出码；warning 仅提示；info 是
  自动生成的旁注（如 PROPAGATION_DEBT_CREATED / UNKNOWN_CHARACTER_DETECTED），
  下游可忽略。errors[].code 是 validate_chapter 源头 emit 的稳定机器码（WC_TOO_SHORT /
  BANNED_WORD / FORESHADOWING_NOT_PAID / LOCKED_FACT_CONFLICT /
  FUTURE_KNOWLEDGE_LEAK / POV_HEAD_HOPPING / SECRET_NOT_REVEALED /
  CHARACTER_MISSING / FILE_NOT_FOUND / MANIFEST_MISSING / ...）——下游按 code
  路由即可，无需正则。人类可读输出（不带 --json）零回退。
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

# v18：统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio
# 2026-05-29 流程贯通（断点 1）：--cluster 入口靠章号⇄cluster_id 反查工具取章范围
import cluster_lookup


BANNED_WORDS = [
    "顿时", "紧锁", "显然", "似乎", "此刻", "淡淡",
    "心中一凛", "眼中闪过一丝", "微微挑眉", "仿佛",
    "嘴角勾起一抹", "深吸一口气", "缓缓地说", "沉吟片刻",
    "与此同时", "值得一提的是", "不仅如此", "然而", "事实上",
    "波涛汹涌", "不容置疑",
]


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_file(project_root: Path, ch: int) -> Path | None:
    """查找章节正文 txt。v18：委托 chapter_io.find_body_file（兼容 4 布局 + 旧平铺）。"""
    return cio.find_body_file(project_root, ch)


def check_word_count(body: str, target: int, min_: int, max_: int) -> list[dict]:
    n = cio.count_words(body)  # v18：统一字数口径
    # v2 cluster 化（2026-05-28）：cluster 视野字数阈值 8000-30000
    import os as _os
    # 2026-05-29 复审修复 [H9]：cluster 视野 8000 字下限是「整块草稿」语义，
    # 不能套到 splitter 切出来的单个分章（单章 3000-4500 CJK）。validate_cluster
    # 逐章 validate 时设 CLUSTER_PER_CHAPTER=1，此处回退到章级字数语义（保留传入
    # 的 min_/max_，即 进度.json.word_count_range 或默认 3000-5000），不套 8000 下限。
    if (_os.environ.get("CLUSTER_MODE") == "1"
            and _os.environ.get("CLUSTER_PER_CHAPTER") != "1"):
        min_, max_ = 8000, 30000
        target = 15000
    errs = []
    if n < min_:
        errs.append({
            "code": "WC_TOO_SHORT",
            "severity": "error",
            "msg": f"字数 {n} 低于下限 {min_}（目标 {target}），需要扩充 {min_ - n} 字",
            "fix_hint": "扩充对话铺垫/环境细节/角色反应，不加新剧情",
        })
    elif n > max_:
        errs.append({
            "code": "WC_TOO_LONG",
            "severity": "warning",
            "msg": f"字数 {n} 超过上限 {max_}，建议精简 {n - max_} 字",
            "fix_hint": "删冗余描写/重复强调，保留关键情节",
        })
    return errs


def check_banned_words(body: str) -> list[dict]:
    errs = []
    for w in BANNED_WORDS:
        count = body.count(w)
        if count == 0:
            continue
        # 定位首次出现
        idx = body.find(w)
        line_no = body[:idx].count("\n") + 1
        errs.append({
            "code": "BANNED_WORD",
            "severity": "error" if count >= 3 else "warning",
            "msg": f"禁用词「{w}」出现 {count} 次（首现第 {line_no} 行）",
            "fix_hint": f"用具体动作或人物对话替代「{w}」",
        })
    return errs


def check_changes_factual(factual: dict | None) -> tuple[list[dict], dict | None]:
    """校验 CHANGES 的 factual 段是否存在且非空。
    v18：CHANGES 已是独立 _changes.json，cio.read_changes 负责读取/旧稿兼容；
    本函数只判断 factual 段有没有内容。"""
    if not factual:
        return [{
            "code": "CHANGES_MISSING",
            "severity": "fatal",
            "msg": "缺少 第NNN章_changes.json 的 factual 段（v18），或旧混合稿无 CHANGES 段",
            "fix_hint": "确认 writer 已产出 第NNN章_changes.json，且 factual 段非空",
        }], None
    return [], factual


def check_tier1_foreshadowing(body: str, changes: dict, manifest: dict,
                              project_root: Path = None) -> list[dict]:
    """Tier-1 到期伏笔必须回收。

    兼容子代理漏写 category/type 字段的情况——通过 id 反查伏笔表推断。
    """
    errs = []
    summary = manifest.get("foreshadowing_summary", {})
    expected = summary.get("tier1_due_count", 0)
    if expected == 0:
        return errs

    # 先尝试严格模式
    strict_payoffs = [a for a in changes.get("foreshadowing_actions", [])
                      if a.get("category") == "promise" and a.get("type") == "payoff"
                      and a.get("tier") == 1]

    # 兼容模式：若子代理没写 category/type，用 id 反查伏笔表 + 正文出现来推断
    fs_data = {}
    if project_root is not None:
        fs_data = load_json(project_root / "_数据库" / "伏笔表.json", {})
    fs_by_id = {p.get("id"): p for p in fs_data.get("promises", [])}

    inferred_payoffs = []
    for a in changes.get("foreshadowing_actions", []):
        if a.get("category") and a.get("type"):
            continue  # 严格模式已处理
        fid = a.get("id")
        if not fid or fid not in fs_by_id:
            continue
        promise = fs_by_id[fid]
        if promise.get("tier") != 1:
            continue
        if promise.get("resolved"):
            continue
        # 出现在 CHANGES 中且对应 promise 描述的关键词在正文里出现 → 视为 payoff
        desc = promise.get("description", "")
        core_tokens = re.findall(r"[一-鿿A-Za-z0-9]{2,}", desc)[:3]
        if core_tokens and any(tok in body for tok in core_tokens):
            inferred_payoffs.append({"id": fid, "inferred": True})

    total_payoffs = len(strict_payoffs) + len(inferred_payoffs)
    if total_payoffs < expected:
        errs.append({
            "code": "FORESHADOWING_NOT_PAID",
            "severity": "error",
            "msg": f"Tier-1 到期伏笔应回收 {expected} 条，实际 {total_payoffs} 条"
                   f"（严格 {len(strict_payoffs)} + 推断 {len(inferred_payoffs)}）",
            "fix_hint": "在 CHANGES.foreshadowing_actions 中补完 category/type/tier 字段并添加 payoff 条目",
        })
    elif inferred_payoffs:
        errs.append({
            "code": "FORESHADOWING_FIELDS_INCOMPLETE",
            "severity": "warning",
            "msg": f"{len(inferred_payoffs)} 条 Tier-1 payoff 缺少 category/type/tier 字段（已推断）",
            "fix_hint": "在 CHANGES.foreshadowing_actions 每条都补齐 category/type/tier 字段",
        })
    return errs


def check_secret_reveal(body: str, changes: dict, manifest: dict) -> list[dict]:
    """本章该揭露的 secret 必须揭露。"""
    errs = []
    expected = manifest.get("foreshadowing_summary", {}).get("must_reveal_this_ch", 0)
    if expected == 0:
        return errs
    reveals = [a for a in changes.get("foreshadowing_actions", [])
               if a.get("category") == "secret" and a.get("type") == "reveal"]
    if len(reveals) < expected:
        errs.append({
            "code": "SECRET_NOT_REVEALED",
            "severity": "error",
            "msg": f"本章应揭露 {expected} 条 secret，实际 {len(reveals)} 条",
            "fix_hint": "安排揭露情节，在 CHANGES 追加 secret.reveal 条目",
        })
    return errs


def check_item_consistency(body: str, changes: dict, project_root: Path,
                           manifest: dict, chapter: int) -> list[dict]:
    """道具持有者一致性：
    正文提到道具名 →
      - 道具必须已在本章之前登场（obtained_ch <= chapter）
      - 持有者必须出场；如果不出场，必须在 CHANGES.item_transfers 中说明转移
    """
    errs = []
    items_data = load_json(project_root / "_数据库" / "道具.json", {"items": []})
    items = items_data.get("items", [])
    active = set(manifest.get("active_characters", []))
    id_map = {}
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    for c in cards:
        if c.get("id") and c.get("name"):
            id_map[c["id"]] = c["name"]
            id_map[c["name"]] = c["id"]

    transfers = {t.get("item"): t for t in changes.get("item_transfers", [])}

    for it in items:
        name = it.get("name", "")
        if not name or name not in body:
            continue
        # 1. 道具必须已登场
        # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 obtained_cluster
        # 2026-05-30 北极星复审：obtained_cluster 是 cluster ID 不是章号——反查起始章，登场 cluster 起始章
        # > 本章才算「道具尚未引入」（禁抽数字当章号，对齐 check_knowledge_leak 的 cluster_id_to_range）。
        oc = it.get("obtained_cluster", "cluster_999")
        _orng = cluster_lookup.cluster_id_to_range(project_root, oc) if isinstance(oc, str) else None
        obtained_lo = int(_orng[0]) if _orng and len(_orng) == 2 else None
        if obtained_lo is not None and obtained_lo > chapter:
            errs.append({
                "code": "ITEM_NOT_YET_INTRODUCED",
                "severity": "error",
                "msg": f"道具「{name}」首次登场在 {oc}（起始第 {obtained_lo} 章），但第 {chapter} 章正文已提及",
                "fix_hint": f"删除对「{name}」的提及，或在大纲中提前该道具的 obtained_ch",
            })
            continue
        # 2. 持有者出场检查（允许 transfers 作为豁免）
        holder = it.get("holder", "")
        if not holder:
            continue
        holder_names = {h.strip() for h in holder.replace("→", ",").split(",")}
        holder_name_set = set()
        for h in holder_names:
            holder_name_set.add(h)
            if h in id_map:
                holder_name_set.add(id_map[h])
        holder_on_stage = bool(holder_name_set & active)
        # 道具名不仅被提及，还要检查是否"被使用"（至少 2 次提及才算真正使用）
        used = body.count(name) >= 2
        if used and not holder_on_stage and name not in transfers:
            errs.append({
                "code": "ITEM_HOLDER_ABSENT",
                "severity": "warning",
                "msg": f"道具「{name}」持有者「{holder}」未出场，正文却多次使用（出现 {body.count(name)} 次）",
                "fix_hint": f"在 CHANGES.item_transfers 声明道具转移，或确认持有者上场",
            })
    return errs


def check_knowledge_leak(body: str, project_root: Path,
                        manifest: dict, chapter: int) -> list[dict]:
    """未来知识泄露检查：
    出场角色的 knowledge.will_learn 条目中，learn_at_ch > 本章的事实，
    不应在正文中被该角色说出/表现出知道。
    """
    errs = []
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    active = set(manifest.get("active_characters", []))
    for c in cards:
        name = c.get("name")
        if name not in active:
            continue
        k = c.get("knowledge", {})
        will_learn = k.get("will_learn", [])
        for wl in will_learn:
            # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 learn_at_cluster
            lac = wl.get("learn_at_cluster")
            if not isinstance(lac, str):
                continue
            # 2026-05-30 北极星复审：learn_at_cluster 是 cluster ID 不是章号——反查起始章，本章 < 起始章
            # 才算「未来知识」（禁抽数字当章号比，参照本文件 validate_cluster 已用 cluster_id_to_range）。
            _lrng = cluster_lookup.cluster_id_to_range(project_root, lac)
            learn_lo = int(_lrng[0]) if _lrng and len(_lrng) == 2 else None
            if learn_lo is None or chapter >= learn_lo:
                continue  # 反查不到（cluster 未涌现）→ 不误报；本章已进入学习 cluster → 可合法知道
            fact = wl.get("fact", "")
            if not fact:
                continue
            # 中文不分词，改用 2-gram 切分；命中 ≥3 个 2-gram 才算「知道」
            # 过滤掉通用停用词（的/了/在/是/这/那等）组成的 bigram
            STOPCHARS = set("的了在是这那和与及对以从为将他她它你我们")
            def bigrams(s: str) -> list[str]:
                s = re.sub(r"[^一-鿿A-Za-z0-9·]", "", s)
                return [s[i:i+2] for i in range(len(s)-1)
                        if not (s[i] in STOPCHARS and s[i+1] in STOPCHARS)]
            fact_bigrams = set(bigrams(fact))
            if len(fact_bigrams) < 3:
                continue  # fact 太短，不可靠
            for para in body.split("\n\n"):
                if name not in para:
                    continue
                para_bigrams = set(bigrams(para))
                hits = fact_bigrams & para_bigrams
                # 命中率 >= 50% 且绝对数 >= 3 才判定泄露
                if len(hits) >= 3 and len(hits) / len(fact_bigrams) >= 0.5:
                    errs.append({
                        "code": "FUTURE_KNOWLEDGE_LEAK",
                        "severity": "error",
                        "msg": f"角色「{name}」在第{chapter}章疑似提前知道 {lac}（起始第{learn_lo}章）才学到的事实:「{fact}」"
                               f"（bigram 命中 {len(hits)}/{len(fact_bigrams)}）",
                        "fix_hint": f"修改该段落避免「{name}」表露对此事的认知；或将大纲 learn_at_ch 提前",
                    })
                    break
    return errs


def check_time_jump(changes: dict, manifest: dict) -> list[dict]:
    """时间跳跃合理性：
    CHANGES.time_advance.elapsed 字段若存在跨日/跨季节表述，
    且未在 key_events 中说明，给警告。
    """
    errs = []
    ta = changes.get("time_advance", {}) or {}
    if not ta:
        return errs
    elapsed = ta.get("elapsed", "")
    if not elapsed:
        return errs
    # 疑似大跨度的关键词
    big_jump_keywords = [
        "几天", "数天", "一周", "两周", "几周", "数周",
        "半个月", "一个月", "两个月", "三个月", "几个月", "数月",
        "半年", "一年", "两年", "三年", "几年", "数年", "多年",
    ]
    hit = next((k for k in big_jump_keywords if k in elapsed), None)
    if not hit:
        return errs
    key_events = ta.get("key_events", []) or []
    if not key_events:
        errs.append({
            "code": "TIME_JUMP_UNEXPLAINED",
            "severity": "warning",
            "msg": f"本章时间跳跃「{elapsed}」较大，但 time_advance.key_events 为空",
            "fix_hint": "在 key_events 中补充跳跃期间的时间锚点（至少 1-2 件大事）",
        })
    return errs


def _flatten_locked_facts(facts) -> list[str]:
    """v17.5 C2：locked_facts 支持 list（旧）和 dict（新）两种格式。"""
    if isinstance(facts, list):
        return [str(x) for x in facts]
    if isinstance(facts, dict):
        out = []
        for k, v in facts.items():
            if isinstance(v, (list, tuple)):
                out.extend(str(x) for x in v)
            elif isinstance(v, dict):
                # 嵌套 dict 如 family.father — 摊平为 "family.father=克拉伦斯..."
                for sk, sv in v.items():
                    if isinstance(sv, (list, tuple)):
                        out.extend(str(x) for x in sv)
                    else:
                        out.append(f"{k}.{sk}={sv}")
            else:
                out.append(f"{k}={v}")
        return out
    return []


def check_locked_facts(body: str, project_root: Path, manifest: dict) -> list[dict]:
    """粗检：对每个出场主角，locked_facts 中的关键短语不应在正文中被否定/替换。

    v17.5 C2 升级：支持 list 和 dict 两种 schema。
    """
    errs = []
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    active = set(manifest.get("active_characters", []))
    for c in cards:
        if c.get("name") not in active:
            continue
        facts = _flatten_locked_facts(c.get("locked_facts", []))
        # 简单启发式：如果 locked_fact 里提到「左手」「灰色」等高辨识词，检查正文里有没有相反描述
        for fact in facts:
            # 抓取颜色/方位/职业关键词做对抗性检查
            m = re.search(r"(左手|右手|黑色|灰色|红色|蓝色|\d+岁|\d+厘米|\d+cm)", fact)
            if m:
                kw = m.group(1)
                # 颜色/方位替代检查
                opposites = {"左手": "右手", "右手": "左手", "黑色": "白色",
                             "灰色": "红色"}
                opp = opposites.get(kw)
                if opp and opp in body and kw not in body:
                    errs.append({
                        "code": "LOCKED_FACT_CONFLICT",
                        "severity": "error",
                        "msg": f"角色「{c.get('name')}」的 locked_fact 含「{kw}」，但正文只出现「{opp}」",
                        "fix_hint": f"恢复为「{kw}」或在 CHANGES 说明变化原因",
                    })
            # 年龄/数字检查：locked_fact 里有"21 岁"或 age=21，正文里有不同数字时告警
            age_m = re.search(r"(age=|岁:?\s*)?(\d{1,3})\s*岁", fact)
            if age_m:
                fact_age = int(age_m.group(2))
                # 检查正文是否提到角色名 + 不同年龄
                name = c.get("name", "")
                for m_body in re.finditer(rf"{re.escape(name)}[^\n。]{{0,20}}?(\d{{1,3}})\s*岁", body):
                    body_age = int(m_body.group(1))
                    if body_age != fact_age:
                        errs.append({
                            "code": "LOCKED_FACT_CONFLICT",
                            "severity": "error",
                            "msg": f"角色「{name}」locked_fact 年龄 {fact_age}，但正文说 {body_age} 岁",
                            "fix_hint": f"恢复为「{fact_age}岁」或在 CHANGES 说明（如时间跳跃）",
                        })
    return errs


def check_character_appearance(body: str, changes: dict, manifest: dict) -> list[dict]:
    """出场角色应在正文中至少被提及。"""
    errs = []
    active = manifest.get("active_characters", [])
    skipped = {s.get("name") for s in changes.get("skipped_characters", [])}
    for name in active:
        if name in skipped:
            continue
        if body.count(name) == 0:
            errs.append({
                "code": "CHARACTER_MISSING",
                "severity": "warning",
                "msg": f"大纲中出场角色「{name}」在正文未出现",
                "fix_hint": f"补充「{name}」的戏份，或在 CHANGES.skipped_characters 说明原因",
            })
        elif body.count(name) < 2 and name == active[0]:
            errs.append({
                "code": "PROTAGONIST_WEAK",
                "severity": "warning",
                "msg": f"主角「{name}」仅出现 {body.count(name)} 次，叙事力度不足",
                "fix_hint": "增加主角视角描写或对话",
            })
    return errs


def check_pov_leak(body: str, manifest: dict) -> list[dict]:
    """POV泄漏检测（v16·移植自外部工艺库）：检测视角跳跃和信息泄漏标记。"""
    errs = []
    pov_leak_patterns = [
        (r"他(?:心中|暗想|心想).{0,20}她(?:心中|暗想|心想)", "同段内两个角色的内心活动——视角跳跃"),
        (r"她(?:心中|暗想|心想).{0,20}他(?:心中|暗想|心想)", "同段内两个角色的内心活动——视角跳跃"),
    ]
    paras = body.split("\n\n")
    for i, para in enumerate(paras):
        for pattern, desc in pov_leak_patterns:
            if re.search(pattern, para):
                errs.append({
                    "code": "POV_HEAD_HOPPING",
                    "severity": "warning",
                    "msg": f"第{i+1}段疑似视角跳跃：{desc}",
                    "fix_hint": "同一段落内只展现一个角色的内心活动",
                })
                break
    return errs


def check_try_fail(changes: dict, manifest: dict) -> list[dict]:
    """因果转换检测（v16·移植自外部工艺库）：检查cluster_blueprint中的try_fail字段是否被正文兑现。"""
    errs = []
    plan = manifest.get("cluster_blueprint_entry") or {}
    if not plan:
        ch = manifest.get("chapter", 0)
        progress_plans = manifest.get("_cluster_blueprint_raw", [])
        for p in progress_plans:
            if p.get("ch") == ch:
                plan = p
                break
    try_fail = plan.get("try_fail", "")
    if try_fail and try_fail.strip():
        fa = changes.get("foreshadowing_actions", []) if changes else []
        cp = changes.get("conflict_progress", []) if changes else []
        if not fa and not cp:
            errs.append({
                "code": "TRY_FAIL_NOT_REFLECTED",
                "severity": "warning",
                "msg": f"大纲 try_fail 为「{try_fail[:50]}」但 CHANGES 无 conflict_progress 或 foreshadowing_actions",
                "fix_hint": "在 CHANGES.conflict_progress 中记录本章的尝试-失败-适应",
            })
    return errs


def check_propagation_debt_generation(changes: dict, project_root: Path) -> list[dict]:
    """传播负债自动生成（v16·移植自AI_NovelGenerator）：
    当CHANGES修改了角色状态，自动检测哪些关联数据需要更新。"""
    errs = []
    if not changes:
        return errs
    char_changes = changes.get("character_changes", [])
    new_debts = []
    for cc in char_changes:
        name = cc.get("name", "")
        field = cc.get("field", "")
        if field in ("状态", "立场", "阵营", "态度"):
            relations = load_json(project_root / "_数据库" / "关系.json", {})
            rels = relations.get("relationships", [])
            affected = [r for r in rels if name in str(r.get("from", "")) or name in str(r.get("to", ""))]
            if affected:
                new_debts.append({
                    "source": f"character_changes.{name}.{field}",
                    "target_collection": "关系",
                    "reason": f"角色「{name}」的{field}变化可能影响 {len(affected)} 条关系",
                    "status": "pending",
                })
    if new_debts:
        progress_path = project_root / "_数据库" / "进度.json"
        progress = load_json(progress_path, {})
        existing = progress.get("propagation_debt", [])
        # v17.5 P1.3 修复：基于 (source, target_collection) 去重，避免堆积相同条目
        existing_keys = {(d.get("source"), d.get("target_collection")) for d in existing
                        if d.get("status") == "pending"}
        truly_new = [d for d in new_debts
                    if (d["source"], d["target_collection"]) not in existing_keys]
        if truly_new:
            existing.extend(truly_new)
            progress["propagation_debt"] = existing
            progress_path.write_text(
                json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            errs.append({
                "code": "PROPAGATION_DEBT_CREATED",
                "severity": "info",
                "msg": f"自动生成 {len(truly_new)} 条传播负债（去重后 / 共 {len(new_debts)} 条候选）",
                "fix_hint": "在下次 save-state 深度维护时清偿，或运行 /reconcile",
            })
    return errs


def check_character_mentions(body: str, project_root: Path, chapter: int) -> list[dict]:
    """角色提及自动检测（v16·对齐NovelCrafter Codex）：
    扫描正文中所有角色名出现，检测未在人物卡中登记的角色。"""
    errs = []
    cards = load_json(project_root / "_数据库" / "人物卡.json", {})
    known_names = set()
    for c in cards.get("characters", []):
        name = c.get("name", "")
        if name:
            known_names.add(name)
        for alias in c.get("aliases", []):
            known_names.add(alias)

    cn_name_pattern = re.compile(r'["“「]([^"”」]+)["”」]\s*[一-鿿]{2,4}(?:说|道|问|答|笑|叹|喊|骂|嘀咕)')  # 2026-05-30 补弯引号
    speaker_pattern = re.compile(r'([一-鿿]{2,4})(?:说道?|道|问道?|答道?|笑道?|骂道?|喊道?|嘀咕|开口)')

    # v27 NER 收敛（feedback: UNKNOWN_CHARACTER 每 cluster 几十误报·fp 已积 250+）
    # 扩首字黑名单：代词 / 否定词 / 时态副词 / 程度副词 / 量词起首（不可能是中文人名首字）
    BAD_FIRST_CHARS = set("的了在是这那和与你我他她它们之就只也都还又再已便"
                          "不没否非无别莫勿"
                          "上下里外前后旁中"
                          "才刚很太极颇较挺真"
                          "有要会能可应该需想")
    # 整词黑名单（NER 误识别的常见动词短语 / 副词搭配 · 通用 · 非项目级 false_positives）
    VERB_PHRASE_BLACKLIST = {
        # X+知 / X+到 / X+见 类（误把动词宾语当人名）
        "知道", "不知", "我知", "你知", "他知", "她知", "都知", "也知", "已知", "得知",
        "看见", "看到", "听见", "听到", "想到", "见到", "感到", "察觉",
        # X+说 / X+问 / X+答 类（动词残段）
        "没说", "再说", "又说", "也说", "却说", "竟说",
        "没问", "再问", "又问", "也问", "追问",
        "没答", "没看", "没听", "没想", "没动",
        # 状语短语
        "为什", "什么", "怎么", "为何", "如何", "那么", "这么",
        # 否定结构（误检的 X 都是连接词）
        "并没", "也没", "还没", "都没", "却没",
        # 时态短语
        "已经", "曾经", "正在", "刚才", "刚刚", "马上", "立刻",
    }

    found_speakers = set()
    for m in speaker_pattern.finditer(body):
        name = m.group(1)
        if len(name) < 2:
            continue
        if name[0] in BAD_FIRST_CHARS:
            continue
        if name in VERB_PHRASE_BLACKLIST:
            continue
        # 含「不/没/又/也/已/再/还/才/刚」第二字时疑似动词短语
        if len(name) >= 2 and name[1] in "不没又也已再还才刚":
            continue
        found_speakers.add(name)

    unknown = found_speakers - known_names
    common_words = {"这时", "此时", "那人", "众人", "有人", "旁边", "对面", "身后", "其中",
                    "忽然", "突然", "随后", "终于", "这里", "一个", "几个", "三人",
                    "对方", "众生", "众僧", "众弟子"}
    unknown -= common_words

    ci = load_json(project_root / "_数据库" / "character_index.json", {})
    fp = set(ci.get("false_positives", []))
    unknown -= fp

    if unknown:
        errs.append({
            "code": "UNKNOWN_CHARACTER_DETECTED",
            "severity": "info",
            "msg": f"正文中检测到{len(unknown)}个未登记角色：{', '.join(sorted(unknown)[:5])}",
            "fix_hint": "如是新角色→在CHANGES.new_entities中声明；如是误检→忽略",
        })
    return errs


def check_hook_specificity(body: str) -> list[dict]:
    """章末钩子具体度检测（v16·移植自外部工艺库因果转换引擎）。"""
    errs = []
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    if len(lines) < 3:
        return errs
    tail = "\n".join(lines[-5:])
    summary_endings = ["一切都将", "从此以后", "就这样", "这一刻.*明白了", "他知道.*改变"]
    for pat in summary_endings:
        if re.search(pat, tail):
            errs.append({
                "code": "HOOK_SUMMARY_ENDING",
                "severity": "warning",
                "msg": f"章末疑似总结式收束（命中「{pat}」），缺少具体钩子",
                "fix_hint": "章末钩子需包含角色+目标/截止/筹码中至少2项，不要用感慨式收束",
            })
            break
    return errs


def check_dialogue_craft(body: str) -> list[dict]:
    """对白工艺检测（v16·移植自外部工艺库 prose_craft）。"""
    errs = []
    action_beats = ["按", "敲", "拽", "推", "转身", "偏头", "咬牙", "停顿",
                    "沉默", "目光", "皱眉", "点头", "摇头", "握", "攥",
                    "站起", "坐下", "后退", "摔", "塞", "抬眼", "移开"]
    lines = body.split("\n")
    consec_dialogue = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            consec_dialogue = 0
            continue
        has_quote = any(q in stripped for q in ['"', '“', '”', '「', '」'])  # 2026-05-30 补弯引号
        if has_quote:
            consec_dialogue += 1
        else:
            has_beat = any(ab in stripped for ab in action_beats)
            if has_beat:
                consec_dialogue = 0
            else:
                consec_dialogue = 0
        if consec_dialogue >= 5:
            line_no = lines.index(line) + 1
            errs.append({
                "code": "DIALOGUE_NO_BEATS",
                "severity": "warning",
                "msg": f"第{line_no}行附近连续{consec_dialogue}行纯对话无动作节拍",
                "fix_hint": "每1-3句对话后插入动作节拍（按/敲/转身/偏头/停顿等）",
            })
            consec_dialogue = 0
    long_quotes = re.findall(r'["“「]([^"”」]{80,})["”」]', body)  # 2026-05-30 补弯引号
    # v2 cluster 化（2026-05-28）：cluster 视野下仪式条文/残卷引文/角色独白合理存在，
    # 阈值从 >0 提到 >5（cluster 整块仪式段可能 3-5 处长引文属功能必要）。
    import os as _os
    _cluster_mode = _os.environ.get("CLUSTER_MODE") == "1"
    _threshold = 5 if _cluster_mode else 0
    if len(long_quotes) > _threshold:
        errs.append({
            "code": "LONG_MONOLOGUE",
            "severity": "warning",
            "msg": f"发现{len(long_quotes)}处长台词独白(>80字)，疑似信息投喂"
                   + (f"（cluster 视野阈值 >{_threshold}，仪式条文/残卷引文容忍）" if _cluster_mode else ""),
            "fix_hint": "拆成2-4次来回问答，混入回避/反问/转移话题",
        })
    return errs


def validate(project_root: Path, chapter: int) -> dict:
    ch_file = find_chapter_file(project_root, chapter)
    file_label = str(ch_file.relative_to(project_root)) if ch_file else f"第{chapter}章.txt"
    if ch_file is None:
        return {
            "passed": False,
            "fatal_count": 1, "error_count": 0, "warning_count": 0,
            "chapter_file": file_label,
            "errors": [{"code": "FILE_NOT_FOUND", "severity": "fatal",
                        "msg": f"第{chapter}章 txt 文件未找到"}],
        }

    manifest_path = project_root / "_数据库" / ".manifest" / f"ch_{chapter:03d}.json"
    manifest = load_json(manifest_path)
    if manifest is None:
        # 2026-05-29 复审修复 [H10]：cluster 视野逐章校验时，splitter 只为起首章建
        # manifest，非起首章必然没有 → 旧逻辑直接 MANIFEST_MISSING(fatal/hard_gate) 误报。
        # CLUSTER_PER_CHAPTER=1 时降级 manifest 依赖：用空 manifest 骨架继续跑机械扫描
        # （字数/禁用词/POV/对白工艺等不依赖 manifest 的检查照常），manifest 依赖型检查
        # （tier1 伏笔/secret/角色出场/道具/locked_fact/knowledge_leak）因 active_characters
        # 等字段为空自然短路，不再硬挡。整块 manifest 依赖由 cluster 级 audit 在起首章统一覆盖。
        import os as _os
        if _os.environ.get("CLUSTER_PER_CHAPTER") == "1":
            manifest = {}
        else:
            return {
                "passed": False,
                "fatal_count": 1, "error_count": 0, "warning_count": 0,
                "chapter_file": file_label,
                "errors": [{"code": "MANIFEST_MISSING", "severity": "fatal",
                            "msg": "manifest 未生成，先运行 build_manifest.py"}],
            }

    # v18：正文走 cio.read_body（纯正文），CHANGES 走 cio.read_changes（factual 段）
    body = cio.read_body(project_root, chapter)
    changes_data = cio.read_changes(project_root, chapter)
    factual = changes_data.get("factual") or None

    all_errs: list[dict] = []
    progress = load_json(project_root / "_数据库" / "进度.json", {})
    wc_range = progress.get("word_count_range", {})
    target = progress.get("words_per_chapter", 3500)
    all_errs += check_word_count(body, target,
                                 wc_range.get("min", 3000),
                                 wc_range.get("max", 5000))
    all_errs += check_banned_words(body)
    changes_errs, changes = check_changes_factual(factual)
    all_errs += changes_errs
    if changes is not None:
        all_errs += check_tier1_foreshadowing(body, changes, manifest, project_root)
        all_errs += check_secret_reveal(body, changes, manifest)
        all_errs += check_character_appearance(body, changes, manifest)
        all_errs += check_item_consistency(body, changes, project_root, manifest, chapter)
        all_errs += check_time_jump(changes, manifest)
    all_errs += check_locked_facts(body, project_root, manifest)
    all_errs += check_knowledge_leak(body, project_root, manifest, chapter)
    all_errs += check_pov_leak(body, manifest)
    all_errs += check_character_mentions(body, project_root, chapter)
    all_errs += check_hook_specificity(body)
    all_errs += check_dialogue_craft(body)
    if changes is not None:
        all_errs += check_try_fail(changes, manifest)
        all_errs += check_propagation_debt_generation(changes, project_root)

    fatal = [e for e in all_errs if e["severity"] == "fatal"]
    errors = [e for e in all_errs if e["severity"] == "error"]
    warnings = [e for e in all_errs if e["severity"] == "warning"]

    return {
        "passed": len(fatal) == 0 and len(errors) == 0,
        "fatal_count": len(fatal),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": all_errs,
        "chapter_file": str(ch_file.relative_to(project_root)),
    }


def format_report(result: dict) -> str:
    lines = []
    lines.append(f"══ 校验结果: {result['chapter_file']} ══")
    lines.append(f"致命 {result.get('fatal_count', 0)} | "
                 f"错误 {result.get('error_count', 0)} | "
                 f"警告 {result.get('warning_count', 0)}")
    lines.append("")
    for e in result["errors"]:
        icon = {"fatal": "🔴", "error": "🟠", "warning": "🟡"}.get(e["severity"], "•")
        lines.append(f"{icon} [{e['code']}] {e['msg']}")
        if e.get("fix_hint"):
            lines.append(f"   → {e['fix_hint']}")
    lines.append("")
    if result["passed"]:
        lines.append("✅ 通过")
    else:
        lines.append("❌ 未通过，请按 fix_hint 修正后重跑 validate")
    return "\n".join(lines)


def format_json(result: dict, chapter: int) -> str:
    """把 validate() 的返回 dict 归一化为 v18 --json 输出契约（见模块 docstring）。
    audit_hub 等下游 json.loads 后直接取 errors[]，靠 code 路由，不再正则解析。"""
    out = {
        "schema_version": "1.0",
        "scanner": "validate_chapter",
        "chapter": chapter,
        "chapter_file": result.get("chapter_file", ""),
        "passed": result.get("passed", False),
        "summary": {
            "fatal": result.get("fatal_count", 0),
            "error": result.get("error_count", 0),
            "warning": result.get("warning_count", 0),
            "info": sum(1 for e in result.get("errors", [])
                        if e.get("severity") == "info"),
            "total": len(result.get("errors", [])),
        },
        "errors": [
            {
                "code": e.get("code", ""),
                "severity": e.get("severity", "warning"),
                "msg": e.get("msg", ""),
                "fix_hint": e.get("fix_hint", ""),
            }
            for e in result.get("errors", [])
        ],
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


def validate_cluster(project_root: Path, cluster_key: str) -> dict:
    """2026-05-29 流程贯通 · cluster 视野校验入口（断点 1）。

    命令文档 cluster-save-state.md:132 调 `validate_chapter.py <项目> --cluster <key>`，
    但旧 main 只解析位置参 <项目><章节号>，--cluster 被当 flag 丢弃 → 实际只校验第 1 章。
    本函数用 cluster_lookup.cluster_id_to_range 取 cluster 章范围，对范围内每章设
    CLUSTER_MODE=1（cluster 视野字数/长引文阈值），逐章跑现有 hard_gate 逻辑后聚合。

    聚合契约与单章 validate() 一致：passed / *_count / errors / chapter_file。
    errors 每条加 _chapter 字段标明来自哪一章（下游按 code 路由不受影响）。
    """
    import os as _os
    rng = cluster_lookup.cluster_id_to_range(project_root, cluster_key)
    if not rng or len(rng) != 2:
        return {
            "passed": False,
            "fatal_count": 1, "error_count": 0, "warning_count": 0,
            "chapter_file": f"{cluster_key}（章范围未回填）",
            "errors": [{"code": "FILE_NOT_FOUND", "severity": "fatal",
                        "msg": f"cluster {cluster_key} 的 chapter_range 未在 进度.json/事件簇.json 找到"
                               "（splitter step 6 切完才回填，或 cluster_key 拼写错）"}],
        }

    chapters = list(range(rng[0], rng[1] + 1))
    prev_mode = _os.environ.get("CLUSTER_MODE")
    prev_per_ch = _os.environ.get("CLUSTER_PER_CHAPTER")
    _os.environ["CLUSTER_MODE"] = "1"  # cluster 视野语义（长引文阈值 >5 等保留）
    # 2026-05-29 复审修复 [H9][H10]：逐章 validate 走「单分章字数/manifest」语义，
    # 不套整块 8000 下限，也不对非起首章硬挡 MANIFEST_MISSING（splitter 只建起首章）。
    _os.environ["CLUSTER_PER_CHAPTER"] = "1"
    all_errs: list[dict] = []
    ch_files: list[str] = []
    try:
        for ch in chapters:
            r = validate(project_root, ch)
            cf = r.get("chapter_file", f"第{ch}章")
            ch_files.append(cf)
            for e in r.get("errors", []):
                e = dict(e)
                e["_chapter"] = ch
                all_errs.append(e)
    finally:
        if prev_mode is None:
            _os.environ.pop("CLUSTER_MODE", None)
        else:
            _os.environ["CLUSTER_MODE"] = prev_mode
        if prev_per_ch is None:
            _os.environ.pop("CLUSTER_PER_CHAPTER", None)
        else:
            _os.environ["CLUSTER_PER_CHAPTER"] = prev_per_ch

    fatal = [e for e in all_errs if e["severity"] == "fatal"]
    errors = [e for e in all_errs if e["severity"] == "error"]
    warnings = [e for e in all_errs if e["severity"] == "warning"]
    return {
        "passed": len(fatal) == 0 and len(errors) == 0,
        "fatal_count": len(fatal),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": all_errs,
        "chapter_file": f"{cluster_key} (ch{chapters[0]}-{chapters[-1]} · {len(chapters)} 章)",
    }


def main():
    args = sys.argv[1:]
    want_json = "--json" in args

    # 2026-05-29 流程贯通（断点 1）：--cluster <key> 整 cluster 视野校验入口
    # 与原位置参章级入口（向后兼容）并存。
    cluster_key = None
    if "--cluster" in args:
        ci = args.index("--cluster")
        if ci + 1 < len(args):
            cluster_key = args[ci + 1]
    if cluster_key:
        project_root = Path(import_cluster_project_arg(args)).resolve()
        result = validate_cluster(project_root, cluster_key)
        if want_json:
            # cluster 模式 chapter 字段用 -1 占位（聚合非单章）
            print(format_json(result, -1))
        else:
            print(format_report(result))
        if result.get("fatal_count", 0) > 0:
            sys.exit(2)
        if not result["passed"]:
            sys.exit(1)
        sys.exit(0)

    positional = [a for a in args if not a.startswith("--")]
    if len(positional) < 2:
        print("用法: python validate_chapter.py <项目路径> <章节号> [--json]",
              file=sys.stderr)
        print("  或: python validate_chapter.py <项目路径> --cluster <cluster_key> [--json]",
              file=sys.stderr)
        sys.exit(2)
    project_root = Path(positional[0]).resolve()
    try:
        chapter = int(positional[1])
    except ValueError:
        print(f"章节号必须是整数: {positional[1]}", file=sys.stderr)
        sys.exit(2)
    result = validate(project_root, chapter)

    # --json：结构化输出（下游解析）；不带：人类可读报告（零回退）
    if want_json:
        print(format_json(result, chapter))
    else:
        print(format_report(result))

    if result.get("fatal_count", 0) > 0:
        sys.exit(2)
    if not result["passed"]:
        sys.exit(1)
    sys.exit(0)


def import_cluster_project_arg(args: list[str]) -> str:
    """--cluster 模式下取项目路径：第一个非 -- 开头且不是紧跟 --cluster 的值的位置参。"""
    cluster_val = None
    if "--cluster" in args:
        ci = args.index("--cluster")
        if ci + 1 < len(args):
            cluster_val = args[ci + 1]
    for a in args:
        if a.startswith("--"):
            continue
        if a == cluster_val:
            continue
        return a
    print("用法: python validate_chapter.py <项目路径> --cluster <cluster_key> [--json]",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
