#!/usr/bin/env python3
"""
build_manifest.py — 章节注入清单生成器

用法：
  python build_manifest.py <项目路径> <章节号>
  例: python build_manifest.py "示例书名" 5

输出：
  <项目路径>/_数据库/.manifest/ch_<N>.json

设计原则（借鉴 OpenClaw AGENTS.md 模式）：
  - 所有条件判断在这里完成，不下放给 AI
  - 只输出「子代理该读什么 + 为什么读 + 读取优先级」
  - 不预组装 prompt 正文，让子代理用 Read 工具按需取
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写

# style_injector：从蒸馏库提取 cross_chapter_diversity / golden_passages
# 修复 v17.3 之前"蒸馏精细但写作粗糙"断层
try:
    from style_injector import build_directive as _build_style_directive
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from style_injector import build_directive as _build_style_directive
    except ImportError:
        _build_style_directive = None


# ============ IO helpers ============

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[WARN] {path.name} JSON 解析失败: {e}", file=sys.stderr)
        return default


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============ 数据库扫描器 ============

class DatabaseScanner:
    """扫描 _数据库/ 下所有 JSON，回答注入决策问题。"""

    # 当前版本已知会扫描的数据库文件（不在此列表的文件会进 unscanned 告警）
    KNOWN_DBS = {
        "进度", "人物卡", "伏笔表", "世界观", "事件表",
        "作者风格", "章纲摘要", "写作经验", "用户偏好",
        "关系", "道具", "时间线", "场景规则", "地图",
        # v17.6+ scanner 专用数据库（不注入 writer，但已知文件）
        "character_index", "beat_map", "character_arc_state",
        "knowledge_graph", "subplot_threads",
        # v20 涌现叙事 / v20.1 世界演化（由 fate_engine / world_evolution_engine 消费）
        "大势卡", "角色池", "世界状态", "涟漪规则",
        # v21 R1.1 显式 Clock 进度（clock_engine 消费）
        "时钟表",
        # v21 R1.2 Storyteller 风格 + Adaptation Factor（narrator_calibrate 消费）
        "叙事节拍器",
        # v21 R1.3 主角 Stress + Mental Break 卡（stress_evaluator 消费）
        "主角压力档",
        # v21 R1.4 永久 Aspect（角色烙印）+ 事件池命运抽签（fate_dice 消费）
        "角色烙印", "事件池",
        # v21 R1.5 群像档（heart_events + schedule + tier_unlocks）（relationship_evaluator 消费）
        "群像档",
        # v21 R2.1-R2.4 Hub + Moves + Position×Effect + Throughlines
        "枢纽场景", "角色行动表", "行动判定模板", "四线脉络",
        # G13 WebNovelBench 8 维评分映射（参考用，不注入 writer）
        "webnovel_bench_mapping",
        # v23 ECAS 故事块（cluster brief 由 build_manifest._collect_event_cluster_context 注入 manifest）
        "事件簇",
    }

    def __init__(self, project_root: Path, chapter: int):
        self.root = project_root
        self.db = project_root / "_数据库"
        self.ch = chapter
        self._cache: dict[str, any] = {}
        self._scanned: set[str] = set()  # 实际读过的 DB 名

    def load(self, name: str, default=None):
        if name not in self._cache:
            self._cache[name] = load_json(self.db / f"{name}.json", default)
            self._scanned.add(name)
        return self._cache[name]

    def _char_id_map(self) -> dict[str, str]:
        """name ↔ id 双向映射。"""
        if "_id_map" not in self._cache:
            m: dict[str, str] = {}
            for c in self.load("人物卡", {}).get("characters", []):
                cid = c.get("id")
                name = c.get("name")
                if cid and name:
                    m[name] = cid
                    m[cid] = name
            self._cache["_id_map"] = m
        return self._cache["_id_map"]

    # --- 基本信息 ---

    def chapter_plan(self) -> dict | None:
        prog = self.load("进度", {})
        plans = prog.get("chapter_plan", [])
        for p in plans:
            if p.get("ch") == self.ch or p.get("chapter") == self.ch:
                return p
        return None

    def volume_info(self) -> dict | None:
        prog = self.load("进度", {})
        for v in prog.get("volumes", []):
            lo, hi = v.get("chapter_range", [0, 0])
            if lo <= self.ch <= hi:
                return v
        return None

    def active_characters(self) -> list[str]:
        plan = self.chapter_plan()
        if plan:
            names = plan.get("characters") or plan.get("active_characters") or []
            if names:
                return names
        cards = self.load("人物卡", {}).get("characters", [])
        return [c.get("name") for c in cards if c.get("role") == "主角"]

    # --- 条件判断 ---

    def due_foreshadowing(self) -> dict:
        """返回本章到期的 4 类剧情约束。"""
        data = self.load("伏笔表", {})
        result = {
            "promises_tier1_due": [],
            "promises_tier2_due": [],
            "deadlines_due": [],
            "active_pledges": [],
            "hidden_secrets": [],
            "reveal_this_ch": [],
        }
        for p in data.get("promises", []):
            if p.get("resolved"):
                continue
            due_by = p.get("due_by", 999)
            if due_by <= self.ch:
                tier = p.get("tier", 3)
                if tier == 1:
                    result["promises_tier1_due"].append(p)
                elif tier == 2:
                    result["promises_tier2_due"].append(p)

        for d in data.get("deadlines", []):
            if d.get("status") == "pending" and d.get("deadline_ch", 999) <= self.ch:
                result["deadlines_due"].append(d)

        for pl in data.get("pledges", []):
            if pl.get("status") == "active":
                result["active_pledges"].append(pl)

        for s in data.get("secrets", []):
            if s.get("status") == "hidden":
                result["hidden_secrets"].append(s)
                if s.get("reveal_at_ch") == self.ch:
                    result["reveal_this_ch"].append(s)
        return result

    def world_keyword_hits(self) -> list[dict]:
        """世界观按关键词匹配（本章大纲命中哪些条目）。"""
        world = self.load("世界观", {})
        entries = world.get("entries", [])
        plan = self.chapter_plan() or {}
        haystack = " ".join(str(v) for v in plan.values() if isinstance(v, (str, list)))
        hits = []
        for e in entries:
            for kw in e.get("keywords", []):
                if kw in haystack:
                    hits.append({"id": e.get("id"), "keywords": e.get("keywords")})
                    break
        hits.sort(key=lambda x: -(x.get("priority", 0)))
        return hits

    def triggerable_events(self) -> list[dict]:
        events = self.load("事件表", {}).get("pending_events", [])
        plan = self.chapter_plan() or {}
        ctx = " ".join(str(v) for v in plan.values() if isinstance(v, (str, list)))
        return [e for e in events if e.get("trigger_condition", "") and
                any(k in ctx for k in e.get("trigger_keywords", []))]

    def has_style_profile(self) -> bool:
        return (self.db / "作者风格.json").exists()

    def has_golden_passages(self) -> bool:
        style = self.load("作者风格", {})
        return bool(style.get("golden_passages"))

    def experience_entries(self) -> list[dict]:
        """返回本章 scene_type 相关、confidence>=0.5 的经验条目。
        兼容两种结构：旧 entries / outline 模板的 success_patterns+failure_patterns。"""
        exp = self.load("写作经验", {})
        plan = self.chapter_plan() or {}
        scene_types = set(plan.get("scene_type", []) if isinstance(plan.get("scene_type"), list)
                          else [plan.get("scene_type")])
        scene_types.discard(None)
        out = []
        # 成功经验 + 旧 entries：按 scene_type 匹配；无 scene_types 标注 = 通用，总是收
        for e in list(exp.get("entries", [])) + exp.get("success_patterns", []):
            if e.get("confidence", 0) < 0.5:
                continue
            e_scenes = set(e.get("scene_types", []))
            if not e_scenes or not scene_types or e_scenes & scene_types:
                out.append(e)
        # 失败模式：对所有章节都该警示，无条件收（仅过滤低置信）
        for e in exp.get("failure_patterns", []):
            if e.get("confidence", 0) >= 0.5:
                out.append(e)
        return out

    def user_preferences(self) -> list[dict]:
        """兼容旧 preferences 字段 + outline 模板的 style/content/workflow 三分类。"""
        prefs = self.load("用户偏好", {})
        pools = (prefs.get("preferences", [])
                 + prefs.get("style_preferences", [])
                 + prefs.get("content_preferences", [])
                 + prefs.get("workflow_preferences", []))
        out = []
        for p in pools:
            if isinstance(p, str):
                out.append({"preference": p, "confidence": 1.0})
            elif isinstance(p, dict) and p.get("confidence", 1.0) >= 0.6:
                out.append(p)
        return out

    def user_preferences_v21(self) -> dict:
        """v21 UX1/UX2: 读 用户偏好.json 的完整 v21 schema 字段。
        如果用户跑过 /wizard 这里有 9 大组完整偏好；否则返回空 dict（agent 用 schema default）。"""
        prefs = self.load("用户偏好", {})
        v21_keys = [
            "_meta", "project_basics", "narrative_pacing", "narrative_structure",
            "character_psychology", "interactive_mode", "quality_control",
            "anti_slop_personal", "agent_model_routing", "advanced",
            # v23 扩展
            "narrative_style",  # POV / 时态 / narrator_voice (writer 第一硬约束)
            "ecas_config",      # ECAS 配置 (cluster_word_range / critical_events_use_opus 等)
        ]
        return {k: prefs[k] for k in v21_keys if k in prefs}

    # --- 新增 4 类扫描：关系/道具/时间线/场景规则 ---

    def relevant_relationships(self) -> list[dict]:
        """抽取出场角色两两之间的关系条目（有数值或非零强度的才返回）。"""
        data = self.load("关系", {"relationships": []})
        active = set(self.active_characters())
        id_map = self._char_id_map()
        # 把 active 名字映射成 id，两边都能匹配
        active_ids = {id_map.get(n, n) for n in active} | active
        hits = []
        for r in data.get("relationships", []):
            f, t = r.get("from"), r.get("to")
            if f in active_ids and t in active_ids:
                # 任何一个维度非零或为负，都算「值得注入」
                vals = [r.get("affinity", 0), r.get("trust", 0),
                        r.get("fear", 0), r.get("respect", 0)]
                if any(v != 0 for v in vals):
                    hits.append({
                        "from": f, "to": t, "type": r.get("type"),
                        "affinity": r.get("affinity", 0),
                        "trust": r.get("trust", 0),
                        "fear": r.get("fear", 0),
                        "respect": r.get("respect", 0),
                    })
        return hits

    def relevant_items(self) -> list[dict]:
        """出场角色持有的道具 + 本章之前登场的 chekhov 道具。"""
        data = self.load("道具", {"items": []})
        active = set(self.active_characters())
        id_map = self._char_id_map()
        active_ids = {id_map.get(n, n) for n in active} | active
        hits = []
        for it in data.get("items", []):
            holder = it.get("holder", "")
            obtained = it.get("obtained_ch", 999)
            if obtained > self.ch:
                continue
            # 持有者匹配出场角色 → 必须注入
            holder_hit = any(h in holder for h in active_ids)
            # 未回收的 chekhov 道具 → 也值得注入（契诃夫之枪）
            chekhov_live = it.get("chekhov") and not it.get("confirmed_ch", 0) >= self.ch
            if holder_hit or chekhov_live:
                hits.append({
                    "id": it.get("id"), "name": it.get("name"),
                    "holder": holder, "chekhov": bool(it.get("chekhov")),
                })
        return hits

    def time_state(self) -> dict:
        """当前时间快照 + 本章是否命中时钟事件。"""
        data = self.load("时间线", {})
        current = data.get("current_time", {})
        hits = [e for e in data.get("world_clock_events", [])
                if e.get("ch") == self.ch]
        return {
            "current_time": current,
            "clock_events_this_ch": hits,
            "npc_schedules_count": len(data.get("npc_schedules", {})),
        }

    def scene_rule_for_chapter(self) -> dict | None:
        """基于本章 scene_type 抽取对应写作规则（合并多类型）。"""
        plan = self.chapter_plan() or {}
        scene_types = plan.get("scene_type")
        if not scene_types:
            return None
        if not isinstance(scene_types, list):
            scene_types = [scene_types]
        rules = self.load("场景规则", {}).get("scene_types", {})
        matched = {st: rules[st] for st in scene_types if st in rules}
        if not matched:
            return None
        return {"scene_types": scene_types, "rules": matched}

    def character_positions(self) -> dict[str, str]:
        """返回出场角色当前位置。"""
        data = self.load("地图", {})
        positions = data.get("character_positions", {})
        active = set(self.active_characters())
        id_map = self._char_id_map()
        out: dict[str, str] = {}
        for name in active:
            key = id_map.get(name, name)
            loc = positions.get(key) or positions.get(name)
            if loc:
                out[name] = loc
        return out

    # --- Coverage 自检（新数据库自动告警） ---

    def coverage_report(self) -> dict:
        """返回 scanned / available / unscanned 三类清单。"""
        available = {p.stem for p in self.db.glob("*.json")
                     if not p.name.startswith(".")}
        known_but_missing = self.KNOWN_DBS - available
        unscanned = available - self.KNOWN_DBS
        return {
            "scanned": sorted(self._scanned),
            "available": sorted(available),
            "unscanned": sorted(unscanned),
            "known_but_missing": sorted(known_but_missing),
            "health": "ok" if not unscanned and not known_but_missing else "attention",
        }

    def previous_chapter_file(self) -> Path | None:
        """上一章的 txt 文件路径（首章返回 None）。v17.5 支持嵌套路径。"""
        if self.ch <= 1:
            return None
        return self._find_chapter_file(self.ch - 1)

    def _find_chapter_file(self, ch_num: int) -> Path | None:
        """v17.5 修复：支持 4 种布局
        1. 嵌套：章节/第NNN章/第NNN章*.txt（STRUCTURE.md 规范）
        2. 嵌套（无前导零）：章节/第N章/第N章*.txt
        3. 平铺：第NNN章*.txt（旧）
        4. 全局 rglob 兜底
        """
        # 1) 嵌套主路径
        for f in self.root.glob(f"章节/第{ch_num:03d}章/第{ch_num:03d}章*.txt"):
            return f
        for f in self.root.glob(f"章节/第{ch_num}章/第{ch_num}章*.txt"):
            return f
        # 2) 平铺布局
        for f in self.root.glob(f"第{ch_num:03d}章*.txt"):
            return f
        for f in self.root.glob(f"第{ch_num}章*.txt"):
            return f
        # 3) 旧 chapters/ 目录
        for f in self.root.glob(f"chapters/ch{ch_num:02d}*.txt"):
            return f
        # 4) rglob 兜底
        for f in self.root.rglob(f"第{ch_num:03d}章*.txt"):
            return f
        for f in self.root.rglob(f"第{ch_num}章*.txt"):
            return f
        return None

    def memory_search(self, query: str = "", top_k: int = 5) -> list[dict]:
        """三层记忆检索（v16·移植自Mem0/Letta概念）。"""
        try:
            import importlib.util
            ml_path = Path(__file__).parent / "memory_layer.py"
            if not ml_path.exists():
                return []
            spec = importlib.util.spec_from_file_location("memory_layer", ml_path)
            ml = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ml)
            mem = ml.MemoryLayer(self.root, self.ch)
            plan = self.chapter_plan() or {}
            q = query or json.dumps(plan, ensure_ascii=False)[:200]
            return mem.search(q, top_k) if q else []
        except Exception:
            return []

    def rag_relevant_chapters(self, top_k: int = 3) -> list[dict]:
        """RAG检索：找到与当前章节最相关的历史章节片段。"""
        try:
            import importlib.util
            rag_path = Path(__file__).parent / "rag_retriever.py"
            if not rag_path.exists():
                return []
            spec = importlib.util.spec_from_file_location("rag_retriever", rag_path)
            rag = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rag)
            return rag.retrieve_tfidf(self.root, self.ch, top_k)
        except Exception:
            return []

    def recent_chapter_openings(self, lookback: int = 3) -> list[dict]:
        """提取前 N 章的开头首行，用于反重复约束。
        v18：首行必须取自纯正文 —— 走 cio.read_body()，否则旧混合 txt 里
        若正文为空会读到 CHANGES JSON 首行，反重复约束失效。"""
        openings = []
        for prev_ch in range(max(1, self.ch - lookback), self.ch):
            try:
                body = cio.read_body(self.root, prev_ch)
            except FileNotFoundError:
                continue
            lines = body.splitlines()
            first_lines = [l.strip() for l in lines[:5] if l.strip()][:3]
            f = self._find_chapter_file(prev_ch)
            openings.append({
                "chapter": prev_ch,
                "file": f.name if f else f"第{prev_ch:03d}章.txt",
                "first_lines": first_lines,
            })
        return openings

    # --- 预检 ---

    def preflight(self) -> dict:
        """数据完整性扫描，返回 {fatal: [...], warning: [...]}。"""
        fatal, warning = [], []
        if not (self.db / "进度.json").exists():
            fatal.append("进度.json 不存在，执行 /outline 初始化")
            return {"fatal": fatal, "warning": warning, "passed": False}
        if not self.chapter_plan():
            fatal.append(f"chapter_plan[{self.ch}] 不存在，大纲未覆盖本章")
        if not (self.db / "人物卡.json").exists():
            fatal.append("人物卡.json 不存在")
        if self.ch > 1 and self.previous_chapter_file() is None:
            fatal.append(f"上一章（第{self.ch-1}章）txt 文件未找到")

        plan = self.chapter_plan() or {}
        if not plan.get("characters"):
            warning.append("本章 characters 字段为空，将 fallback 到全量人物卡")
        if not plan.get("key_events") and not plan.get("summary"):
            warning.append("本章 key_events/summary 为空，大纲过于简略")
        if not plan.get("scene_type"):
            warning.append("本章 scene_type 未标注，跳过场景规则注入")

        cards = {c.get("name") for c in self.load("人物卡", {}).get("characters", [])}
        for n in plan.get("characters", []):
            if n not in cards:
                warning.append(f"出场角色「{n}」在人物卡中不存在（视为新角色）")
        return {"fatal": fatal, "warning": warning, "passed": len(fatal) == 0}


# ============ Manifest 生成 ============

def _collect_active_fate_events(scanner, chapter: int) -> dict:
    """v20 F5: 调 fate_engine evaluate 取本章应推进的大势事件。
    优先级 > chapter_plan（涌现式模式下 chapter_plan 可能为空）。
    """
    fate_path = scanner.root / "_数据库" / "大势卡.json"
    if not fate_path.exists():
        return {"mode": "strict", "active": [], "_note": "无大势卡，走传统 chapter_plan 模式"}
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import fate_engine
        result = fate_engine.evaluate(scanner.root, chapter)
        drift_result = fate_engine.drift(scanner.root, chapter)
        return {
            "mode": "fluid",
            "active": result.get("active_fate_events", [])[:5],
            "overdue": drift_result.get("overdue_events", []),
            "total_scheduled": result.get("total_scheduled"),
            "total_completed": result.get("total_completed"),
            "_note": "鬼谷八荒式涌现叙事 - 本章应推进 active 中的 1-2 个事件",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_relevant_heuristics(scanner, chapter: int, top_k: int = 5) -> dict:
    """v22 SE2 ERL Heuristics 检索：按本章 context 检索 top-N 写作经验，
    避免全量塞导致 context rot。

    Context = scene_type + 主 POV + beat + 高频出场角色
    检索方式：关键词重叠（简化 BM25）+ 优先 active + 高 confidence + 高 usage_count
    """
    exp_path = scanner.root / "_数据库" / "写作经验.json"
    if not exp_path.exists():
        return {"mode": "off", "_note": "无写作经验.json"}
    try:
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
    except Exception:
        return {"mode": "error"}

    # 本章 context
    import re as _re
    progress = scanner.load("进度", {})
    ch_plan = next((c for c in progress.get("chapter_plan", []) if c.get("ch") == chapter), {}) if progress else {}
    scene_types = ch_plan.get("scene_type", [])
    if not isinstance(scene_types, list):
        scene_types = [scene_types]
    chars = ch_plan.get("characters", [])
    turning = ch_plan.get("turning_point", "")

    # context keywords
    ctx_kws = set()
    for s in scene_types:
        ctx_kws.update(_re.findall(r"[一-鿿]{2,4}", str(s))[:3])
    for c in chars[:3]:
        ctx_kws.add(c)
    ctx_kws.update(_re.findall(r"[一-鿿]{2,4}", turning)[:5])

    # 聚合所有 active patterns
    all_patterns = []
    for category in ["success_patterns", "failure_patterns"]:
        for p in exp.get(category, []) or []:
            if not isinstance(p, dict):
                continue
            if p.get("status") == "retired":
                continue
            all_patterns.append({**p, "_category": category})

    if not all_patterns:
        return {"mode": "on", "total_patterns": 0, "retrieved": []}

    # 打分：context 关键词重叠 + confidence + usage_count log
    import math
    def score(p):
        desc = (p.get("description", "") + " " + p.get("name", "") + " "
                + " ".join(p.get("keywords", []) or []))
        kw_hits = sum(1 for kw in ctx_kws if kw in desc)
        confidence = p.get("confidence", 0.5)
        usage = p.get("usage_count", 0)
        # 综合分：context match 主导 + confidence 加成 + usage log 加成
        return kw_hits * 2 + confidence + math.log(usage + 1)

    all_patterns.sort(key=score, reverse=True)
    top = all_patterns[:top_k]

    # 标记 usage（这次被检索 → 视为被参考）
    for t in top:
        pid = t.get("id") or t.get("name", "?")
        # 找原对象 +1 usage（注意：build_manifest 只读不写，这里只标记，由 reflector 实际累加）
        t["_retrieved_at_ch"] = chapter

    return {
        "mode": "on",
        "context_kws": list(ctx_kws)[:10],
        "total_patterns": len(all_patterns),
        "retrieved_count": len(top),
        "retrieved": [
            {
                "category": p.get("_category"),
                "id": p.get("id") or p.get("name", "?"),
                "name": p.get("name", "")[:60],
                "description": p.get("description", "")[:120],
                "confidence": p.get("confidence", 0.5),
                "version": p.get("version", 1),
            }
            for p in top
        ],
        "_note": "ERL heuristics：按本章 context 检索的 top-N 写作经验。writer 应优先消费此清单，而非读全量 写作经验.json",
    }


def _collect_user_preferences_v21(scanner) -> dict:
    """v21 UX1/UX2: 注入用户在 /wizard 中配置的偏好（覆盖系统默认）。
    writer / outline-planner / audit_hub / scanner 应优先消费此字段，缺失时用 schema default。
    """
    prefs = scanner.user_preferences_v21() if hasattr(scanner, "user_preferences_v21") else {}
    if not prefs:
        return {
            "mode": "off",
            "_note": "用户未跑 /wizard，全部用系统默认值。强烈推荐用户先跑 /wizard 配置偏好",
        }
    # 摘要重要字段方便 writer 快速消费
    summary = {}
    pb = prefs.get("project_basics", {}) or {}
    if pb:
        summary["chapter_target_words"] = pb.get("target_words_per_chapter")
        summary["dcas_mode"] = pb.get("dcas_dual_chapter_mode")
        summary["writer_mode"] = pb.get("writer_mode")
    np_ = prefs.get("narrative_pacing", {}) or {}
    if np_:
        summary["storyteller"] = np_.get("storyteller_profile")
        summary["setback_per_n_ch"] = np_.get("expected_setback_per_n_ch")
        summary["happy_ratio"] = np_.get("happy_vs_dark_ratio")
    cp = prefs.get("character_psychology", {}) or {}
    if cp:
        summary["stress_threshold"] = cp.get("stress_threshold_break")
        summary["allowed_break_cards"] = cp.get("allowed_mental_break_cards")
    im = prefs.get("interactive_mode", {}) or {}
    if im:
        summary["use_fate_cards"] = im.get("use_fate_cards")
        summary["fully_auto"] = im.get("fully_auto")
    qc = prefs.get("quality_control", {}) or {}
    if qc:
        summary["audit_mode"] = qc.get("audit_mode")
        summary["scan_intensity"] = qc.get("cross_chapter_scan_intensity")
    asp = prefs.get("anti_slop_personal", {}) or {}
    if asp:
        summary["user_banned_words"] = asp.get("user_banned_words", [])[:10]
        summary["user_preferred_phrases"] = asp.get("user_preferred_phrases", [])[:10]
    # v23 narrative_style: POV / 时态 / 叙事者声音 - writer 第一硬约束
    ns = prefs.get("narrative_style", {}) or {}
    if ns:
        summary["narrative_style"] = {
            "pov": ns.get("pov"),
            "pov_anchor_character": ns.get("pov_anchor_character"),
            "tense": ns.get("tense", "past"),
            "narrator_voice": ns.get("narrator_voice", "neutral"),
        }
    else:
        summary["narrative_style"] = {
            "pov": None,
            "_warning": "narrative_style 未配置 - writer 不准写，先报错让主代理补 wizard (v23 第一硬约束)"
        }
    # v23 ecas_config 直通
    ec = prefs.get("ecas_config", {}) or {}
    if ec:
        summary["ecas_enabled"] = ec.get("ecas_enabled", False)
        summary["ecas_critical_events_use_opus"] = ec.get("critical_events_use_opus", [])
    return {
        "mode": "on",
        "wizard_completed_at": (prefs.get("_meta") or {}).get("wizard_completed_at"),
        "summary": summary,
        "full_preferences": prefs,
        "_note": "writer 必读 summary，覆盖系统默认；narrative_style.pov 是第一硬约束；user_banned_words 必入 anti-slop；audit_mode 决定 hard_gate 严格度",
    }


def _collect_hub_directive(scanner, chapter: int) -> dict:
    """v21 R2.1: 注入枢纽场景信息 + 本章 hub 角色（depart/quest/return/idle）。
    writer step 0w 据此让章节有出发-冒险-返回的呼吸节奏。"""
    hubs_path = scanner.root / "_数据库" / "枢纽场景.json"
    if not hubs_path.exists():
        return {"mode": "off", "_note": "无枢纽场景.json，未启用 Hub 系统"}
    try:
        data = json.loads(hubs_path.read_text(encoding="utf-8"))
        hubs = data.get("hubs", [])
        # 只注入 hub 摘要 + anchor_props/routines（writer 需要）
        hub_summaries = []
        for h in hubs:
            hub_summaries.append({
                "hub_id": h.get("hub_id"),
                "label": h.get("label"),
                "type": h.get("type"),
                "anchor_props": (h.get("anchor_props") or [])[:5],
                "anchor_routines": (h.get("anchor_routines") or [])[:3],
            })
        targets = data.get("rhythm_targets", {})
        log = data.get("chapter_hub_log", [])
        recent = log[-5:] if log else []
        return {
            "mode": "on",
            "hubs": hub_summaries,
            "rhythm_targets": targets,
            "recent_chapter_roles": [{"ch": e.get("ch"), "hub_id": e.get("hub_id"), "role": e.get("role")} for e in recent],
            "_note": "writer step 0w：本章应标 hub_id + role(depart/quest/return/idle)；回 hub 时必带至少 1 个 anchor_props 或 anchor_routines",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_character_moves(scanner, chapter: int, active_chars: list) -> dict:
    """v21 R2.2: 注入本章涉及角色的 Moves 清单。"""
    moves_path = scanner.root / "_数据库" / "角色行动表.json"
    if not moves_path.exists():
        return {"mode": "off", "_note": "无角色行动表.json，未启用 Moves 系统"}
    try:
        data = json.loads(moves_path.read_text(encoding="utf-8"))
        chars = data.get("characters", {}) or {}
        out = {}
        for ch_name in active_chars:
            if ch_name in chars:
                out[ch_name] = chars[ch_name].get("moves", [])
        return {
            "mode": "on" if out else "no_active_char_with_moves",
            "moves_by_character": out,
            "_note": "writer：让角色行动时优先从 moves 抽 narrative_template，不要凭空发明动作。frequency_per_chapter 限制单 move 单章最多用几次",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_position_effect_template(scanner, chapter: int) -> dict:
    """v21 R2.3: 注入 position × effect 双轴判定模板（轻量）。"""
    template_path = scanner.root / "_数据库" / "行动判定模板.json"
    if not template_path.exists():
        return {"mode": "off"}
    try:
        data = json.loads(template_path.read_text(encoding="utf-8"))
        return {
            "mode": "on",
            "positions": list(data.get("positions", {}).keys()),
            "effects": list(data.get("effects", {}).keys()),
            "_note": "writer：本章关键行动场景必自评 position × effect → 写入 _changes.json.self_eval.position_effect_evals[]",
            "_full_matrix_at": str(template_path.relative_to(scanner.root.parent.parent)) if template_path.is_relative_to(scanner.root.parent.parent) else "_数据库/行动判定模板.json",
        }
    except Exception:
        return {"mode": "error"}


def _collect_throughlines(scanner, chapter: int) -> dict:
    """v21 R2.4: 注入 4 条 throughline 当前状态。"""
    th_path = scanner.root / "_数据库" / "四线脉络.json"
    if not th_path.exists():
        return {"mode": "off"}
    try:
        data = json.loads(th_path.read_text(encoding="utf-8"))
        th = data.get("throughlines", {})
        out = {}
        for k, v in th.items():
            out[k] = {
                "label": v.get("label"),
                "current_arc": v.get("current_arc"),
                "current_progress": v.get("current_progress") or v.get("current_stage"),
            }
        return {
            "mode": "on",
            "throughlines": out,
            "_note": "writer step 0x：本章必同步推进至少 2 条 throughline，避免连续 3 章只推 OS 不推 MC/IC/RS",
        }
    except Exception:
        return {"mode": "error"}


def _collect_ensemble_layer(scanner, chapter: int) -> dict:
    """v21 R1.5: 注入 NPC heart_events 待揭密 + schedule 时段提示 + tier_unlocks 档位语义。
    writer step 0v 据此让群像配角真的活着。"""
    ensemble_path = scanner.root / "_数据库" / "群像档.json"
    if not ensemble_path.exists():
        return {"mode": "off", "_note": "无群像档.json，未启用群像档系统"}
    try:
        ensemble = json.loads(ensemble_path.read_text(encoding="utf-8"))
        # 读已经评估过的 pending_reveals（如果有）
        pending_path = scanner.root / "_数据库" / ".ensemble_pending_reveals.json"
        pending_reveals = []
        if pending_path.exists():
            try:
                pending_data = json.loads(pending_path.read_text(encoding="utf-8"))
                pending_reveals = pending_data.get("pending_reveals", [])
            except Exception:
                pass

        # NPC schedule 提示（仅本章 chapter_plan.characters 涉及的 NPC）
        plan = scanner.load("章纲摘要", {}).get("chapter_plan", {}) if hasattr(scanner, "load") else {}
        chapter_chars = []
        if isinstance(plan, dict):
            ch_data = plan.get(str(chapter)) or plan.get(chapter) or {}
            if isinstance(ch_data, dict):
                chapter_chars = ch_data.get("characters", [])
        npc_schedule_hints = {}
        for npc in chapter_chars:
            if npc in (ensemble.get("characters") or {}):
                schedule = ensemble["characters"][npc].get("schedule", {})
                tier_unlocks = ensemble["characters"][npc].get("tier_unlocks", {})
                if schedule:
                    npc_schedule_hints[npc] = {"schedule": schedule, "tier_unlocks": tier_unlocks}

        return {
            "mode": "on",
            "pending_heart_event_reveals": pending_reveals,
            "must_reveal_count": len(pending_reveals),
            "npc_schedule_hints": npc_schedule_hints,
            "_note": "writer step 0v 必读：must_reveal_count > 0 时本章必触发对应 reveal（physical_evidence 必出现）；NPC schedule 决定主角找他时的地点/状态",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_active_aspects(scanner, chapter: int) -> dict:
    """v21 R1.4: 注入角色当前 active_aspects（永久身体/精神改造）。
    writer step 0u 必读 → 至少呼应 1 个 aspect；validate_chapter 扫 narrative_constraints 是否被遵守。"""
    aspects_path = scanner.root / "_数据库" / "角色烙印.json"
    if not aspects_path.exists():
        return {"mode": "off", "_note": "无角色烙印.json，未启用 Aspect 系统"}
    try:
        data = json.loads(aspects_path.read_text(encoding="utf-8"))
        out = {}
        for char, char_data in (data.get("characters") or {}).items():
            active = char_data.get("active_aspects", []) or []
            if active:
                out[char] = active
        return {
            "mode": "on",
            "characters_with_aspects": out,
            "total_active": sum(len(v) for v in out.values()),
            "_note": "writer step 0u 必读：每个 active aspect 的 narrative_constraints 必遵守；emotional_triggers 出现在场景时必触景生情",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_fate_dice_hint(scanner, chapter: int) -> dict:
    """v21 R1.4: 标记本章是否应该用命运抽签（emergent_opportunities 触发时）。
    主代理在 outline-planner 后决定是否调 fate_dice.py draw → 抽中事件写入此字段。"""
    pool_path = scanner.root / "_数据库" / "事件池.json"
    if not pool_path.exists():
        return {"mode": "off", "_note": "无事件池.json，未启用命运抽签"}
    return {
        "mode": "available",
        "_note": "writer step 0u-2：如本章 active_fate_events 为空且想埋铺垫钩子，主代理可调 fate_dice.py draw 从事件池抽 1，narrative_seed 写入本章一个具体场景",
        "draw_command": f"python core/scripts/fate_dice.py <project> draw {chapter} --scene-type <type> --pov <pov>",
    }


def _collect_research_cache_ref(scanner, chapter: int) -> dict:
    """v23 ECAS: 自动注入最近 N 份 .research_cache/*.md 路径 + Synthesis 段提取。
    writer / outline-planner 不依赖主代理传 prompt，自动看到最新调研 anchor。
    """
    cache_dir = scanner.root / "_数据库" / ".research_cache"
    if not cache_dir.exists():
        return {
            "mode": "off",
            "_note": "无 .research_cache 目录 — v17.7 调研先行规则未启动",
            "_warning": "v23 ECAS 必须调研先行，writer/outline-planner 不应在缺调研时启动"
        }
    files = sorted(cache_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if not files:
        return {"mode": "off", "_note": ".research_cache 空"}
    items = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            # 提取 Synthesis / Top-N 段（首 ≤ 800 字）
            synth_start = max(text.find("## Synthesis"), text.find("## Top"), text.find("## 综合"))
            synth = text[synth_start:synth_start + 800] if synth_start >= 0 else text[:600]
            items.append({
                "path": str(f.relative_to(scanner.root).as_posix()),
                "name": f.stem,
                "mtime": f.stat().st_mtime,
                "synthesis_preview": synth[:600]
            })
        except Exception:
            continue
    return {
        "mode": "on",
        "_doc": "writer/outline-planner 应优先引用本字段而非凭模型记忆生成内容",
        "_v17_7_rule": "调研先行: 每 cluster brief 前必跑 novel-researcher 写入 .research_cache",
        "latest_count": len(items),
        "items": items,
    }


def _collect_event_cluster_context(scanner, chapter: int) -> dict:
    """v23 ECAS: 注入本章所属事件簇的 context (cluster_id / brief / mid_checkpoints / foreshadowing)。
    writer 在 MODE=ecas 时必读此字段。
    决策树:
    1. 读 _数据库/事件簇.json 找 status in (pending, in_progress) 且 chapter_range 包含 chapter 的 cluster
    2. 找不到 → mode=off (本章是 DCAS/single 模式)
    3. 找到 → 提取 brief 全字段
    """
    clusters_path = scanner.root / "_数据库" / "事件簇.json"
    if not clusters_path.exists():
        return {"mode": "off", "_note": "无事件簇.json，本章按 DCAS/single 模式"}
    try:
        data = json.loads(clusters_path.read_text(encoding="utf-8"))
        clusters = data.get("clusters") or []
        if not clusters:
            return {"mode": "off", "_note": "事件簇.json 为空，本章按 DCAS/single 模式"}
        # 找匹配 chapter 的 cluster
        for c in clusters:
            cr = c.get("chapter_range") or []
            status = c.get("status")
            if status in ("pending", "in_progress", "writer_done", "splitter_done"):
                # pending cluster (未指定 chapter_range) 也算（writer 启动时本章 = first ch）
                if not cr or (len(cr) == 2 and cr[0] <= chapter <= cr[1]):
                    cluster_id_val = c.get("cluster_id") or ""
                    is_first_cluster = cluster_id_val.endswith("_001") or cluster_id_val == "cluster_001"
                    narrative_mode = c.get("narrative_mode") or ("in_medias_res" if is_first_cluster else "linear")
                    return {
                        "mode": "on",
                        "cluster_id": cluster_id_val,
                        "parent_me": c.get("parent_me"),
                        "scope_summary": c.get("scope_summary"),
                        "expected_word_range": c.get("expected_word_range"),
                        "scenes_estimated": c.get("scenes_estimated"),
                        "anchor_props": c.get("anchor_props") or [],
                        "foreshadowing_to_plant": c.get("foreshadowing_to_plant") or [],
                        "foreshadowing_to_callback": c.get("foreshadowing_to_callback") or [],
                        "mid_checkpoints": c.get("mid_checkpoints") or [3000, 6000, 9000],
                        "sub_summary_template": c.get("sub_summary_template") or "[场景 N] 关键事件 + 角色行动 + 伏笔进度（100 字内）",
                        "opus_recommended": c.get("opus_recommended", False),
                        "extended_thinking": c.get("extended_thinking", False),
                        "ME_to_advance": c.get("ME_to_advance") or [],
                        "throughline_focus": c.get("throughline_focus") or [],
                        "characters_focus": c.get("characters_focus") or [],
                        "hub_locations": c.get("hub_locations") or [],
                        "estimated_chapters": c.get("estimated_chapters", 4),
                        "cluster_position_hint": _infer_cluster_position(chapter, cr) if cr else "head",
                        "narrative_mode": narrative_mode,
                        "climax_hint_scene_index": c.get("climax_hint_scene_index"),
                        "_narrative_mode_doc": "in_medias_res = 黄金三章倒叙（cluster_001 默认开启 · 强冲突放最前）；linear = 时间序",
                        "_writer_hint": "MODE=ecas: 用此 brief 生成 8K-16K 字 cluster_draft，每 3000 字 self-audit，每场景生成 100 字 sub-summary"
                    }
        return {"mode": "off", "_note": f"无匹配 cluster (本章 {chapter} 不在任何 active cluster 范围)"}
    except Exception as e:
        return {"mode": "error", "_error": str(e)[:200]}


def _infer_cluster_position(chapter: int, chapter_range: list) -> str:
    """推断本章在 cluster 内的位置 (head/mid/tail/solo)"""
    if not chapter_range or len(chapter_range) != 2:
        return "solo"
    start, end = chapter_range
    if start == end:
        return "solo"
    if chapter == start:
        return "head"
    if chapter == end:
        return "tail"
    return "mid"


def _collect_protagonist_stress(scanner, chapter: int) -> dict:
    """v21 R1.3: 注入主角当前 stress + 高压时 coping 建议。
    writer step 0t 据此感知主角内心承压状态。"""
    stress_path = scanner.root / "_数据库" / "主角压力档.json"
    if not stress_path.exists():
        return {"mode": "off", "_note": "无主角压力档.json，未启用 Stress 系统"}
    try:
        s = json.loads(stress_path.read_text(encoding="utf-8"))
        level = s.get("stress_level", 0)
        threshold = s.get("stress_threshold_break", 8)
        max_v = s.get("stress_max", 10)
        coping = s.get("coping_mechanisms", {}).get("high_stress_behaviors", [])
        recent_log = (s.get("stress_log") or [])[-3:]
        # 检查最近是否触发过 mental_break
        last_break = None
        for entry in reversed(s.get("stress_log") or []):
            if entry.get("trigger_type") == "mental_break_triggered":
                last_break = {"ch": entry["ch"], "card_label": entry.get("card_label")}
                break
        return {
            "mode": "on",
            "protagonist": s.get("protagonist"),
            "stress_level": level,
            "stress_threshold_break": threshold,
            "stress_pct": round(level / max_v, 2) if max_v > 0 else 0,
            "is_high_stress": level >= threshold * 0.75,
            "coping_behaviors": coping if level >= threshold * 0.6 else [],
            "recent_log": recent_log,
            "last_mental_break": last_break,
            "persona_violations_to_avoid": [
                {"trait": t.get("trait"), "violation_kw": t.get("violation_keywords", [])[:3]}
                for t in (s.get("persona_violations_tracked", {}) or {}).get("core_traits", [])
            ],
            "_note": "writer step 0t 必读：is_high_stress=true 时本章应自然带入至少 1 个 coping 行为；last_mental_break 后所有章节必受 card 永久效应约束",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_storyteller_directive(scanner, chapter: int) -> dict:
    """v21 R1.2: 注入 storyteller 风格 + 近窗 adaptation 状态 + 下章建议。
    writer step 0s 据此微调本章 outcome 倾向（setback/win/neutral）。"""
    pacer_path = scanner.root / "_数据库" / "叙事节拍器.json"
    if not pacer_path.exists():
        return {"mode": "off", "_note": "无叙事节拍器.json，未启用 Storyteller 系统"}
    try:
        pacer = json.loads(pacer_path.read_text(encoding="utf-8"))
        af = pacer.get("adaptation_factor", {}) or {}
        rec = pacer.get("narrator_recommendation", {}) or {}
        return {
            "mode": "on",
            "profile": pacer.get("storyteller_profile", "cassandra"),
            "current_phase": pacer.get("current_pressure_phase", "rising"),
            "since_phase_change_ch": pacer.get("since_phase_change_ch"),
            "adaptation": {
                "expected_setback_per_n_ch": af.get("expected_setback_per_n_ch"),
                "current_setback_count_in_window": af.get("current_setback_count_in_window"),
                "current_win_streak": af.get("current_win_streak"),
                "current_loss_streak": af.get("current_loss_streak"),
            },
            "next_recommendation": {
                "target_outcome": rec.get("next_chapter_target_outcome", "auto"),
                "intensity_target": rec.get("next_chapter_intensity_target", "auto"),
                "reason": rec.get("_reason", ""),
            },
            "_note": "writer step 0s 必读：target_outcome=setback 时本章必至少有 1 个真实挫败（资源损失/关系破裂/认知打击）；=win 时本章应有明确推进/收获；=auto 时按 chapter_plan 自由发挥",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_active_clocks(scanner, chapter: int) -> dict:
    """v21 R1.1: 注入显式 Clock 进度系统快照。
    writer step 0r 据此感知「还差 N 章 X 事件就要发生」并把暗示自然埋进环境。"""
    clocks_path = scanner.root / "_数据库" / "时钟表.json"
    if not clocks_path.exists():
        return {"mode": "off", "_note": "无时钟表.json，未启用 Clock 进度系统"}
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import clock_engine
        r = clock_engine.list_active(scanner.root, chapter)
        if "error" in r:
            return {"mode": "error", "error": r["error"]}
        active = r.get("active_clocks", [])
        # 仅注入 visible_to_writer=True 的 clock
        visible = [c for c in active if c.get("visible_to_protagonist") is False or True]  # writer 可见所有 active clock
        return {
            "mode": "on",
            "active_clocks": visible,
            "total_active": len(visible),
            "urgent_count": sum(1 for c in visible if c.get("urgency") == "urgent"),
            "approaching_count": sum(1 for c in visible if c.get("urgency") == "approaching"),
            "_note": "writer step 0r 必读：urgent (≤2 章满格) 必埋暗示；approaching 视情况埋；normal 不强制",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_world_state_snapshot(scanner, chapter: int) -> dict:
    """v20.1 W4: 注入 鬼谷八荒式世界状态快照 + 本章可用机缘。

    writer step 0q 据此感知世界自转：
      - factions_state：5 大势力当前数值（power/stability/wealth）
      - active_npc_threads：幕后角色正在做什么（top 5 by priority）
      - emergent_opportunities：本章可用 + 即将过期的副线机缘
      - recent_ticks：最近 3 章世界变化日志
      - recent_consequences：最近 2 条因果记录
    """
    world_path = scanner.root / "_数据库" / "世界状态.json"
    if not world_path.exists():
        return {"mode": "off", "_note": "无世界状态.json，未启用世界演化"}
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import world_evolution_engine as wee
        world = wee.load_world(scanner.root)
        if world is None:
            return {"mode": "error", "error": "世界状态.json 解析失败"}

        # factions: 精简（去 leader/notes 节省 manifest 字符）
        factions = {}
        for name, f in (world.get("factions_state") or {}).items():
            factions[name] = {
                "power": f.get("power"),
                "stability": f.get("stability"),
                "wealth": f.get("wealth"),
                "current_focus": (f.get("current_focus") or "")[:50],
            }

        # threads: top 5 by priority
        threads_raw = world.get("active_npc_threads") or []
        threads = sorted(threads_raw, key=lambda t: -(t.get("_priority", 0)))[:5]
        threads_compact = [
            {
                "thread_id": t.get("thread_id"),
                "npc": t.get("npc_id"),
                "action": (t.get("current_action") or "")[:60],
                "since_ch": t.get("since_ch"),
                "expected_complete_ch": t.get("expected_complete_ch"),
                "visible_to_protagonist": t.get("visible_to_protagonist", False),
                "outcome_if_complete": (t.get("outcome_if_complete") or "")[:50],
                "_priority": t.get("_priority"),
            }
            for t in threads
        ]

        # emergent opportunities: 本章可用（trigger_ch <= ch <= expires_at_ch 且未消费）
        opps_raw = world.get("emergent_opportunities") or []
        opps_available = []
        for o in opps_raw:
            if o.get("consumed_by_writer") or o.get("status") == "expired":
                continue
            trig = o.get("trigger_ch", 0)
            exp = o.get("expires_at_ch", 9999)
            if trig <= chapter <= exp:
                opps_available.append({
                    "id": o.get("id"),
                    "type": o.get("type"),
                    "description": (o.get("description") or "")[:80],
                    "expires_in_chapters": exp - chapter,
                })

        # 最近 3 章 ticks log
        ticks_log = world.get("world_ticks_log") or []
        recent_ticks = ticks_log[-3:]

        # 最近 2 条 consequences
        cons = world.get("consequence_tracker") or {}
        cons_keys = [k for k in cons.keys() if k != "_doc"]
        recent_cons = []
        for k in cons_keys[-2:]:
            entry = cons[k]
            recent_cons.append({
                "key": k,
                "trigger": (entry.get("trigger") or "")[:60],
                "world_changes": (entry.get("world_changes") or [])[:2],
            })

        return {
            "mode": "fluid",
            "current_world_time": world.get("current_world_time"),
            "factions_state": factions,
            "active_npc_threads_top5": threads_compact,
            "active_threads_total": len(threads_raw),
            "emergent_opportunities_available": opps_available,
            "recent_ticks": recent_ticks,
            "recent_consequences": recent_cons,
            "_note": "writer step 0q 必读：世界自转中，幕后角色一直在动；本章如能呼应至少 1 个 thread/opportunity 加分",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _collect_offscreen_actions(scanner, chapter: int) -> list[dict]:
    """v19.2: 扫所有角色的 offscreen.actions，挑出 ch_range 覆盖本章且未 done 的 action。

    返回 [{character, action, result_expected, visible_to_protagonist, action_index}, ...]
    用于注入 manifest 让 writer 主动消费幕后行动。
    """
    out = []
    chars_data = scanner.characters_data if hasattr(scanner, 'characters_data') else None
    if not chars_data:
        # 从 scanner 缓存或重新加载
        cards_path = scanner.root / "_数据库" / "人物卡.json"
        if cards_path.exists():
            try:
                chars_data = json.loads(cards_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        else:
            return []

    for c in chars_data.get("characters", []):
        if c.get("role") == "主角":
            continue
        name = c.get("name") or c.get("id")
        offscreen = c.get("offscreen", {})
        actions = offscreen.get("actions", [])
        for idx, act in enumerate(actions):
            if act.get("done"):
                continue
            ch_range = act.get("ch_range", [])
            if len(ch_range) == 2 and ch_range[0] <= chapter <= ch_range[1]:
                out.append({
                    "character": name,
                    "character_id": c.get("id"),
                    "action_index": idx,
                    "action": act.get("action", ""),
                    "result_expected": act.get("result", ""),
                    "visible_to_protagonist": act.get("visible_to_protagonist", False),
                    "ch_range": ch_range,
                    "current_plan": offscreen.get("current_plan", ""),
                    "goals_short": (offscreen.get("goals", []) or [""])[0][:60],
                })
    return out


def _collect_reader_preferences(scanner) -> dict:
    """v19.6 G10: 读 _数据库/.reader_pref/<genre>_<date>.json 最新一份（过期 30 天自动报警）。"""
    pref_dir = scanner.root / "_数据库" / ".reader_pref"
    if not pref_dir.is_dir():
        return {"available": False, "reason": "无 reader_pref 数据，spawn researcher 采集"}
    files = sorted(pref_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"available": False, "reason": "无 reader_pref 文件"}
    try:
        d = json.loads(files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"available": False, "reason": "数据解析失败"}
    return {
        "available": True,
        "filled": d.get("filled", False),
        "genre": d.get("genre"),
        "collected_at": d.get("collected_at"),
        "expiry": d.get("expiry"),
        "structure": d.get("structure", {}),
    }


def _collect_selective_history(scanner, chapter: int, top_k: int = 3) -> dict:
    """v19.6 G5: 用 chapter_plan.turning_point + threads_advance 作 query，
    从 .embeddings/chapter_*.json 做语义检索取 top_k 历史 chunk 给 writer。

    比固定 recent 5 章摘要更智能——本章是觉醒章，应该回忆爷爷纸条章节而不是吃饭章。
    """
    if chapter <= 1:
        return {"retrieved": [], "reason": "首章无历史"}

    # 构造 query
    progress = scanner.load("进度", {"chapter_plan": []})
    query_parts = []
    for cp in progress.get("chapter_plan", []):
        if cp.get("ch") == chapter:
            query_parts.append(cp.get("turning_point", ""))
            query_parts.append(cp.get("goal", ""))
            ta = cp.get("threads_advance", [])
            if isinstance(ta, list):
                query_parts.extend(ta)
            break
    query = " ".join(str(q) for q in query_parts if q)
    if not query:
        return {"retrieved": [], "reason": "本章 chapter_plan 无 query 信号"}

    # 加载 embedding 模块
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import embedding_store
    except ImportError:
        return {"retrieved": [], "reason": "embedding_store 不可用"}

    query_emb = embedding_store.compute_embedding(query)

    # 扫历史章节 embedding，做语义检索
    emb_dir = scanner.root / "_数据库" / ".embeddings"
    if not emb_dir.is_dir():
        return {"retrieved": [], "reason": "embeddings 索引不存在"}

    import re as _re
    candidates = []
    for f in emb_dir.glob("chapter_*.json"):
        m = _re.match(r"chapter_(\d+)", f.stem)
        if not m:
            continue
        prev_ch = int(m.group(1))
        if prev_ch >= chapter:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue
        for chunk in data.get("chunks", []):
            sim = embedding_store.cosine_similarity(query_emb, chunk.get("embedding", []))
            candidates.append({
                "ch": prev_ch,
                "chunk_idx": chunk.get("idx"),
                "text_preview": chunk.get("text_preview", ""),
                "similarity": round(sim, 3),
            })
    # 取 top_k
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    top = candidates[:top_k]
    return {
        "_note": "按本章 turning_point + threads_advance 作 query 做语义检索，找前 N 章语义相近的片段。比固定 recent 摘要更智能。",
        "query": query[:100],
        "retrieved": top,
        "total_candidates": len(candidates),
    }


def _collect_golden_few_shot(scanner, chapter: int, per_type: int = 3) -> dict:
    """v19.6 G4: 蒸馏 golden_passages few-shot 化注入。
    根据本章 scene_type 选 3-5 段原作金句段塞 manifest。
    业界证据：few-shot 比 zero-shot 提升 23.5x（arxiv 2509.14543）。
    """
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}
    gp = sd.get("golden_passages", {})
    if not isinstance(gp, dict):
        return {}

    # 读本章 scene_type
    progress = scanner.load("进度", {"chapter_plan": []})
    scene_types = set()
    for cp in progress.get("chapter_plan", []):
        if cp.get("ch") == chapter:
            st = cp.get("scene_type", [])
            if isinstance(st, list):
                scene_types.update(st)
            elif st:
                scene_types.add(st)
            break

    # scene_type → golden_passages 类型映射
    scene_to_passages = {
        "日常": ["description_passages", "dialogue_passages"],
        "悬疑": ["psychology_passages", "description_passages"],
        "战斗": ["action_passages"],
        "情感": ["psychology_passages", "dialogue_passages"],
        "转折": ["transition_passages", "psychology_passages"],
    }
    keys_to_pick = set()
    for st in scene_types:
        keys_to_pick.update(scene_to_passages.get(st, []))
    # 必带 opening + ending
    keys_to_pick.add("opening_passages")
    keys_to_pick.add("ending_passages")

    # 每类取 per_type 段（取前 N 段或随机；这里取前 N 简化）
    few_shot = {}
    total_chars = 0
    BUDGET_CHARS = 6000  # 总预算 6KB 避免 manifest 膨胀
    for key in sorted(keys_to_pick):
        passages = gp.get(key, [])
        if not passages:
            continue
        picked = []
        for p in passages[:per_type]:
            if isinstance(p, dict):
                text = p.get("text") or p.get("passage") or p.get("content") or ""
            elif isinstance(p, str):
                text = p
            else:
                continue
            if not text:
                continue
            # 截断单段最长 800 字
            text = text[:800]
            if total_chars + len(text) > BUDGET_CHARS:
                break
            picked.append(text)
            total_chars += len(text)
        if picked:
            few_shot[key] = picked
    # v21 P3.1: 检查每类 ≥ 5 段（业界研究：< 5 段 LLM 风格回归均值）
    style_regression_warnings = []
    MIN_DEMOS = 5
    for key in keys_to_pick:
        total_available = len(gp.get(key, []) or [])
        if 0 < total_available < MIN_DEMOS:
            style_regression_warnings.append({
                "type": key,
                "available_count": total_available,
                "min_needed": MIN_DEMOS,
                "_research": "< 5 demos LLM style regresses to mean (arxiv 2025+)",
                "suggestion": f"golden_passages.{key} 仅 {total_available} 段（需 ≥ {MIN_DEMOS}）→ 风格回归均值风险，蒸馏时补",
            })
        elif total_available == 0:
            style_regression_warnings.append({
                "type": key,
                "available_count": 0,
                "min_needed": MIN_DEMOS,
                "suggestion": f"golden_passages.{key} 完全为空 → 本章 scene_type 命中此类型但无 few-shot 锚定",
            })

    return {
        "_note": "蒸馏库 golden_passages few-shot 段落。按本章 scene_type 选取，writer 应模仿这些段落的句法/节奏（非内容）。",
        "scene_types_matched": list(scene_types),
        "passages_by_type": few_shot,
        "total_chars": total_chars,
        "style_regression_warnings": style_regression_warnings,
    }


def _collect_distill_continuity(scanner) -> dict:
    """v19.5 对齐：蒸馏 narrative_continuity_template 注入。"""
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}
    nct = sd.get("narrative_continuity_template", {})
    if not isinstance(nct, dict):
        return {}
    return {
        "source": nct.get("source", ""),
        "universal_principles": nct.get("universal_transition_principles", [])[:8],
        "three_chapter_templates_count": len(nct.get("three_chapter_templates", [])),
    }


def _collect_title_style(scanner) -> dict:
    """v22.4dim N5：从风格库读 title_style.json，注入章节标题命名指纹。"""
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {"title_style_missing": True}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"title_style_missing": True}
    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return {"title_style_missing": True}

    # 推算 title_style.json 路径
    for parent in [scanner.root, *scanner.root.parents]:
        ts_file = parent / "workspace" / "styles" / work / "title_style.json"
        if ts_file.exists():
            try:
                return json.loads(ts_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                pass
    ts_file = Path.cwd() / "workspace" / "styles" / work / "title_style.json"
    if ts_file.exists():
        try:
            return json.loads(ts_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
    return {"title_style_missing": True, "reason": f"no title_style.json for work={work}"}


def _collect_main_character_arcs(scanner, top_k: int = 3) -> dict:
    """v22.4dim Round 2 应用：注入风格库主要角色 arc（importance TOP K · Stanford 6 维）。

    writer 写新作时如果用了某蒸馏风格，可以参考"原作主角是什么 Stanford 6 维 profile"
    给本作主角设计同样画像（如：高 A 主动型 vs 高 I 内省型）。
    """
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {"main_character_arcs_missing": True}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"main_character_arcs_missing": True}
    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return {"main_character_arcs_missing": True}

    arc_dir = None
    for parent in [scanner.root, *scanner.root.parents]:
        candidate = parent / "workspace" / "styles" / work / "character_arcs"
        if candidate.exists():
            arc_dir = candidate
            break
    if arc_dir is None:
        candidate = Path.cwd() / "workspace" / "styles" / work / "character_arcs"
        if candidate.exists():
            arc_dir = candidate
    if arc_dir is None or not arc_dir.exists():
        return {"main_character_arcs_missing": True, "reason": f"no character_arcs for {work}"}

    # 读所有角色 arc，按 importance 排序取 TOP K
    arcs = []
    for f in arc_dir.glob("*_emotion_arc.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            s6 = d.get("stanford_6_component", {})
            imp = s6.get("overall_importance", 0)
            arcs.append({
                "character": d.get("character"),
                "tier": s6.get("tier", "unknown"),
                "overall_importance": imp,
                "stanford_6": {
                    "N": s6.get("N_naming"), "C": s6.get("C_communication"),
                    "I": s6.get("I_interiority"), "A": s6.get("A_agency"),
                    "DC": s6.get("DC_direct_char"), "DN": s6.get("DN_description_by_narrator"),
                },
                "avg_actor_intensity": d.get("average_actor_intensity"),
                "avg_experiencer_intensity": d.get("average_experiencer_intensity"),
                "emotion_rhythm_actor": d.get("emotion_rhythm_pattern_actor"),
                "actor_top_emotions": d.get("actor_top_emotions", [])[:3],
                "experiencer_top_emotions": d.get("experiencer_top_emotions", [])[:3],
                "stage_transitions_count": len(d.get("stage_transitions", [])),
            })
        except (json.JSONDecodeError, ValueError):
            continue

    arcs.sort(key=lambda x: -(x["overall_importance"] or 0))
    return {
        "_doc": "v22.4dim 蒸馏库主要角色 arc TOP K（按 Stanford 6 维 importance 排序）· 仿写时参考原作主角画像",
        "top_k": top_k,
        "main_characters": arcs[:top_k],
    }


def _collect_naming_convention(scanner) -> dict:
    """v22.4dim N5：从风格库读 naming_convention.json，注入角色命名指纹。"""
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {"naming_convention_missing": True}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"naming_convention_missing": True}
    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return {"naming_convention_missing": True}

    for parent in [scanner.root, *scanner.root.parents]:
        nc_file = parent / "workspace" / "styles" / work / "naming_convention.json"
        if nc_file.exists():
            try:
                return json.loads(nc_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                pass
    nc_file = Path.cwd() / "workspace" / "styles" / work / "naming_convention.json"
    if nc_file.exists():
        try:
            return json.loads(nc_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
    return {"naming_convention_missing": True, "reason": f"no naming_convention.json for work={work}"}


def _collect_arc_template(scanner, chapter: int) -> dict:
    """v22.cluster：注入本章所属 cluster 的 arc 模板（情感曲线 / 节奏 / 高潮）。

    查找优先级（双轨）：
      1. cluster 主轨：读 cluster_index.json → 找包含本章的 cluster → 读 cluster_arc_<id>.json
      2. fixed10 副轨：旧固定 10 章公式（向后兼容）
      3. 全部 fallback 失败：返回 missing
    """
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {"arc_template_missing": True, "reason": "no _数据库/作者风格.json"}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"arc_template_missing": True, "reason": "style json invalid"}

    work = (sd.get("meta") or {}).get("work") or sd.get("work")
    if not work:
        return {"arc_template_missing": True, "reason": "no meta.work in style"}

    # 推算风格库根目录
    style_root = None
    for parent in [scanner.root, *scanner.root.parents]:
        candidate = parent / "workspace" / "styles" / work
        if candidate.exists():
            style_root = candidate
            break
        candidate2 = parent / "styles" / work
        if candidate2.exists():
            style_root = candidate2
            break
    if style_root is None:
        candidate = Path.cwd() / "workspace" / "styles" / work
        if candidate.exists():
            style_root = candidate
    if style_root is None or not style_root.exists():
        return {"arc_template_missing": True, "reason": f"style_root not found for work={work}"}

    arc_dir = style_root / "arc_templates"
    cluster_index_file = style_root / "cluster_index.json"

    # ① cluster 主轨
    if cluster_index_file.exists() and arc_dir.exists():
        try:
            ci = json.loads(cluster_index_file.read_text(encoding="utf-8"))
            for c in ci.get("clusters", []):
                rng = c.get("chapter_range", [])
                if len(rng) == 2 and rng[0] <= chapter <= rng[1]:
                    cid = c["cluster_id"]
                    arc_file = arc_dir / f"cluster_arc_{cid}.json"
                    if arc_file.exists():
                        return _build_arc_payload(arc_file, chapter, track="cluster")
        except (json.JSONDecodeError, ValueError):
            pass

    # ② fixed10 副轨
    if arc_dir.exists():
        arc_end = ((chapter - 1) // 10 + 1) * 10
        arc_file = arc_dir / f"arc_{arc_end:03d}.json"
        if not arc_file.exists():
            candidates = sorted(arc_dir.glob("arc_*.json"))
            candidates = [c for c in candidates if c.name != "arc_summary.json"]
            for f in candidates:
                m = re.match(r"arc_(\d+)\.json", f.name)
                if m and int(m.group(1)) >= chapter:
                    arc_file = f
                    break
        if arc_file.exists():
            return _build_arc_payload(arc_file, chapter, track="fixed10")

    return {"arc_template_missing": True,
            "reason": f"no cluster_index/arc files for work={work}; 请先跑 cluster_segmenter.py + arc_aggregator.py --all-clusters"}


def _build_arc_payload(arc_file: Path, chapter: int, track: str) -> dict:
    """从 arc JSON 文件构建 manifest 注入字段。"""
    try:
        arc_data = json.loads(arc_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"arc_template_missing": True, "reason": "arc json invalid"}

    arc_range = arc_data.get("chapter_range", "")
    if isinstance(arc_range, list) and len(arc_range) == 2:
        arc_start = arc_range[0]
        arc_range_str = f"ch{arc_range[0]}-{arc_range[1]}"
    else:
        m = re.match(r"ch(\d+)-(\d+)", str(arc_range))
        arc_start = int(m.group(1)) if m else 1
        arc_range_str = str(arc_range)
    chapter_index = chapter - arc_start

    emotion_curve = arc_data.get("emotion_curve_normalized") or []
    pacing_labels = arc_data.get("pacing_labels") or []
    expected_emo = emotion_curve[chapter_index] if 0 <= chapter_index < len(emotion_curve) else None
    expected_pacing = pacing_labels[chapter_index] if 0 <= chapter_index < len(pacing_labels) else None

    return {
        "track": track,                 # v22.cluster: 'cluster' (主) | 'fixed10' (副)
        "arc_id": arc_data.get("arc_id"),
        "cluster_id": arc_data.get("cluster_id"),
        "arc_chapter_range": arc_range_str,
        "this_chapter_index_in_arc": chapter_index,
        "this_chapter_expected_emotion": expected_emo,
        "this_chapter_expected_pacing": expected_pacing,
        "arc_climax_chapter_number": arc_data.get("climax_chapter_number"),
        "arc_structure_label": arc_data.get("arc_structure_label"),
        "matched_reagan_shape": arc_data.get("matched_reagan_shape"),
        "boundary_reason": arc_data.get("boundary_reason"),
        "emotion_curve_full": emotion_curve,
        "pacing_labels_full": pacing_labels,
        "_doc": (
            f"v22.cluster arc 模板（{track} 轨）：本章 ch{chapter} 在 {arc_data.get('arc_id')}（{arc_range_str}）"
            f"的第 {chapter_index + 1} 点。writer 应让本章情绪强度 ≈ {expected_emo}（± 0.15），节奏 = {expected_pacing}。"
            f"全 arc 形状: {arc_data.get('arc_structure_label')} ({arc_data.get('matched_reagan_shape')})；"
            f"arc 内高潮章: ch{arc_data.get('arc_climax_chapter_number')}."
        ),
    }


def _collect_distill_voice_refs(scanner) -> dict:
    """v19.5 对齐：蒸馏 character_voice_pack 作为参考模板（原作角色风格 DNA）。
    项目内新角色 voice_pack 是手工填的，缺少 dialogue_avg_chars / behavior_loop / special 等行为模板，
    把蒸馏库的原作角色 voice DNA 注入，让 writer 写新角色时借鉴行为模板。
    """
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if not style_path.exists():
        return {}
    try:
        sd = json.loads(style_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}
    cvp = sd.get("character_voice_pack", {})
    if not isinstance(cvp, dict):
        return {}
    # 只取前 3 个原作角色（避免 manifest 膨胀），每个角色取核心字段
    out = {}
    for name, data in list(cvp.items())[:3]:
        if not isinstance(data, dict):
            continue
        out[name] = {
            "dialogue_avg_chars": data.get("dialogue_avg_chars"),
            "dialogue_range": data.get("dialogue_range", "")[:80],
            "behavior_loop": data.get("behavior_loop", "")[:120],
            "special": data.get("special", "")[:120],
        }
    return {
        "_note": "蒸馏库原作角色 voice DNA 参考模板（schema：dialogue_avg_chars/behavior_loop/special）。写新角色对话时借鉴行为模板。",
        "references": out,
    }


def _collect_prev_judge_findings(scanner, chapter: int, lookback: int = 3) -> dict:
    """v19.3: 从前 lookback 章的 .judge_reports/ 抽 health_warnings + reasoning_trace 摘要，
    注入下章 manifest 让 writer 看到具体警告（不只是 grade 数字）。
    """
    if chapter <= 1:
        return {"prev_chapter_count": 0, "health_warnings": [], "key_findings": []}
    judge_dir = scanner.root / "_数据库" / ".judge_reports"
    if not judge_dir.is_dir():
        return {"prev_chapter_count": 0, "health_warnings": [], "key_findings": []}

    start_ch = max(1, chapter - lookback)
    health_warnings = []
    key_findings = []
    chapters_with_data = set()

    for ch in range(start_ch, chapter):
        for f in judge_dir.glob(f"ch_{ch:03d}_*.json"):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                continue
            judge_id = r.get("judge_id", "?")
            findings = r.get("specific_findings", {})
            # health_warnings（foreshadower 等给的"未来 X 章到期"）
            for hw in findings.get("health_warnings", []) if isinstance(findings, dict) else []:
                health_warnings.append({"from_ch": ch, "judge_id": judge_id, **hw})
            # uncertainty_flags
            for uf in r.get("uncertainty_flags", []):
                if isinstance(uf, str):
                    key_findings.append({"from_ch": ch, "judge_id": judge_id, "type": "uncertainty", "note": uf[:120]})
            chapters_with_data.add(ch)

    return {
        "prev_chapter_count": len(chapters_with_data),
        "lookback_range": f"ch{start_ch}-{chapter-1}",
        "health_warnings": health_warnings[:10],
        "key_findings": key_findings[:8],
    }


def _collect_active_relationships(scanner, active_chars: list[str]) -> list[dict]:
    """v19.2: 收集本章出场角色之间的关系数值。"""
    if not active_chars:
        return []
    rel_path = scanner.root / "_数据库" / "关系.json"
    if not rel_path.exists():
        return []
    try:
        data = json.loads(rel_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    active_set = set(active_chars)
    out = []
    for r in data.get("relationships", []):
        f, t = r.get("from"), r.get("to")
        if f in active_set or t in active_set:
            out.append({
                "from": f, "to": t,
                "affinity": r.get("affinity", 0),
                "trust": r.get("trust", 0),
                "fear": r.get("fear", 0),
                "respect": r.get("respect", 0),
                "notes": r.get("notes", "")[:60],
            })
    return out


def _collect_faction_standings(scanner) -> dict:
    rel_path = scanner.root / "_数据库" / "关系.json"
    if not rel_path.exists():
        return {}
    try:
        data = json.loads(rel_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("faction_standings", {})


def _collect_will_learn_due(scanner, chapter: int) -> list[dict]:
    """v19.2: 检查本章是否有 will_learn 条目到期（learn_at_ch == 本章）。"""
    cards_path = scanner.root / "_数据库" / "人物卡.json"
    if not cards_path.exists():
        return []
    try:
        data = json.loads(cards_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for c in data.get("characters", []):
        for wl in c.get("knowledge", {}).get("will_learn", []):
            if wl.get("learn_at_ch") == chapter:
                out.append({
                    "character": c.get("name") or c.get("id"),
                    "fact": wl.get("fact", ""),
                    "how": wl.get("how", ""),
                })
    return out


def _collect_secrets_to_reveal(scanner, chapter: int) -> list[dict]:
    """v19.2: 检查本章是否有 secrets 应揭露（reveal_at_ch == 本章）+ 已 leaked 应跟踪。"""
    fs_path = scanner.root / "_数据库" / "伏笔表.json"
    if not fs_path.exists():
        return []
    try:
        data = json.loads(fs_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for s in data.get("secrets", []):
        if s.get("reveal_at_ch") == chapter or s.get("status") == "leaked":
            out.append({
                "id": s.get("id"),
                "secret": s.get("secret", "")[:60],
                "status": s.get("status", "hidden"),
                "reveal_at_ch": s.get("reveal_at_ch"),
                "known_by": s.get("known_by", []),
            })
    return out


def _build_hard_constraints(
    s: DatabaseScanner,
    foreshadow_summary: dict[str, int],
) -> list[str]:
    """组装 hard_constraints 列表，含剧情约束 + 风格量化约束（v15 新增）。"""
    hard_constraints = [
        f"Tier-1 伏笔 {foreshadow_summary['tier1_due_count']} 条本章必须回收",
        f"secrets 本章必须揭露 {foreshadow_summary['must_reveal_this_ch']} 条",
        "locked_facts 零容忍（读人物卡.json 时请完整保留）",
        f"字数目标 {s.load('进度', {}).get('words_per_chapter', 3500)} 字",
        "章节开头反重复：检查 manifest.recent_openings，本章开头类型和焦点元素必须与前 2-3 章完全不同",
    ]

    # 风格量化约束（v15 新增）
    if s.has_style_profile():
        style = s.load("作者风格", {})
        quant = style.get("quantitative", {})
        dr = quant.get("dialogue_ratio", {})
        if dr.get("mean"):
            hard_constraints.append(
                f"对话占比 ≥ {max(0.3, dr['mean'] - 0.15):.0%}"
            )
        sl = quant.get("sentence_length", {})
        if sl.get("mean"):
            hard_constraints.append(
                f"句长均值目标 {sl['mean']:.0f} 字（std ≥ {max(5, sl.get('std', 8) - 3):.0f}）"
            )
        cw = quant.get("chapter_words", {})
        if cw.get("mean"):
            low = max(2000, int(cw["mean"]) - 500)
            high = int(cw["mean"]) + 500
            hard_constraints.append(f"章节字数 {low}-{high}")
        punc = quant.get("punctuation_density_per_1000", {})
        cpr_raw = punc.get("comma_period_ratio")
        cpr = cpr_raw.get("mean") if isinstance(cpr_raw, dict) else cpr_raw
        if cpr and cpr > 1.0:
            hard_constraints.append(
                f"逗句比 ≥ {max(1.0, cpr - 1.0):.1f}:1（长句用逗号连接）"
            )

    return hard_constraints


def build_manifest(project_root: Path, chapter: int) -> dict:
    s = DatabaseScanner(project_root, chapter)
    preflight = s.preflight()
    if not preflight["passed"]:
        return {
            "chapter": chapter,
            "project": project_root.name,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "preflight": preflight,
            "must_read": [],
            "instructions": "预检失败，不生成注入清单。修复 fatal 项后重跑。",
        }

    volume = s.volume_info()
    chapter_plan = s.chapter_plan() or {}
    due = s.due_foreshadowing()
    world_hits = s.world_keyword_hits()
    events = s.triggerable_events()
    active_chars = s.active_characters()
    prev_file = s.previous_chapter_file()
    recent_openings = s.recent_chapter_openings(lookback=3)
    rag_hits = s.rag_relevant_chapters(top_k=3)
    memory_hits = s.memory_search(top_k=3)
    rel_hits = s.relevant_relationships()
    item_hits = s.relevant_items()
    time_snap = s.time_state()
    scene_rule = s.scene_rule_for_chapter()
    positions_matched = s.character_positions()

    # must_read: 优先级 P0=必读 P1=强烈建议 P2=按需
    must_read = []

    must_read.append({
        "path": "_数据库/进度.json",
        "priority": "P0",
        "focus": f"chapter_plan[{chapter}] + 本卷 volume_arc",
        "reason": "本章蓝图与卷级定位",
    })
    must_read.append({
        "path": "_数据库/人物卡.json",
        "priority": "P0",
        "focus": f"角色 {active_chars} 的 locked_facts/voice_pack/knowledge",
        "reason": "角色说话风格 + 硬约束 + 知识边界",
    })
    if prev_file:
        must_read.append({
            "path": str(prev_file.relative_to(project_root)),
            "priority": "P0",
            "focus": "最后 500 字用于衔接；全文用于文风延续",
            "reason": "保持章节衔接与文风连贯",
        })

    # P1: 剧情约束
    foreshadow_summary = {
        "tier1_due_count": len(due["promises_tier1_due"]),
        "tier2_due_count": len(due["promises_tier2_due"]),
        "deadlines_due": len(due["deadlines_due"]),
        "active_pledges": len(due["active_pledges"]),
        "hidden_secrets": len(due["hidden_secrets"]),
        "must_reveal_this_ch": len(due["reveal_this_ch"]),
    }
    if any(foreshadow_summary.values()):
        must_read.append({
            "path": "_数据库/伏笔表.json",
            "priority": "P0" if due["promises_tier1_due"] or due["reveal_this_ch"] else "P1",
            "focus": "本章必须回收/触发的 4 类约束",
            "reason": f"Tier-1 到期 {foreshadow_summary['tier1_due_count']} 条，必须回收",
        })

    # P1: 时间线（始终激活，但 focus 依据扫描结果）
    time_focus = f"current_time = {time_snap['current_time'].get('period','')}/{time_snap['current_time'].get('season','')}"
    if time_snap["clock_events_this_ch"]:
        time_focus += f"；本章时钟事件 {[e.get('event') for e in time_snap['clock_events_this_ch']]}"
    must_read.append({
        "path": "_数据库/时间线.json",
        "priority": "P0" if time_snap["clock_events_this_ch"] else "P1",
        "focus": time_focus,
        "reason": "保证时间推进连贯" + ("；本章有时钟事件" if time_snap["clock_events_this_ch"] else ""),
    })
    must_read.append({
        "path": "_数据库/地图.json",
        "priority": "P1",
        "focus": f"character_positions 中 {active_chars} 的位置"
                 + (f"（已定位 {len(positions_matched)} 位）" if positions_matched else ""),
        "reason": "防止角色瞬移",
    })

    # P1: 角色关系（仅当有非零关系时注入）
    if rel_hits:
        must_read.append({
            "path": "_数据库/关系.json",
            "priority": "P0" if any(abs(r.get("affinity", 0)) >= 5 or abs(r.get("trust", 0)) >= 5
                                    for r in rel_hits) else "P1",
            "focus": f"出场角色两两关系：{[(r['from'], r['to'], r['type']) for r in rel_hits]}",
            "reason": f"{len(rel_hits)} 条出场角色间的关系条目，防止写出与数值冲突的互动",
        })

    # P1: 道具状态
    if item_hits:
        must_read.append({
            "path": "_数据库/道具.json",
            "priority": "P1",
            "focus": f"只读 id ∈ {[i['id'] for i in item_hits]}",
            "reason": f"{len(item_hits)} 件本章相关道具（含 {sum(1 for i in item_hits if i['chekhov'])} 件 chekhov）",
        })

    # P2: 按命中条件启用
    if s.has_style_profile():
        must_read.append({
            "path": "_数据库/作者风格.json",
            "priority": "P0",
            "focus": "style_profile + writing_rules + anti_patterns + must_have_per_chapter",
            "reason": "风格蒸馏结果，定量+硬性规则",
        })
    if s.has_golden_passages():
        scene_types = chapter_plan.get("scene_type", [])
        if not isinstance(scene_types, list):
            scene_types = [scene_types]
        must_read.append({
            "path": "_数据库/作者风格.json",
            "priority": "P1",
            "focus": f"golden_passages.{'/'.join(scene_types) or 'opening+ending'}（每类 ≤2 段）",
            "reason": "Few-Shot 风格锚定",
        })

    # v17.5 B3.1: skill 分级读取（避免 1455 行 skill_FINAL.md 全量灌入）
    skill_md = project_root / "_数据库" / "作者风格_skill.md"
    if skill_md.exists():
        scene_types = chapter_plan.get("scene_type", []) or []
        if not isinstance(scene_types, list):
            scene_types = [scene_types]
        # 基于 scene_type 推荐重点章节
        skill_sections_priority = ["二、叙事核心规则（硬规则）", "三、跨章多样性约束"]
        if any(s in str(scene_types) for s in ["开场", "出发"]):
            skill_sections_priority.append("2.4 开头规则")
        if any(s in str(scene_types) for s in ["结尾", "收束", "高潮"]):
            skill_sections_priority.append("2.5 结尾规则")
        if any(s in str(scene_types) for s in ["对话", "群戏"]):
            skill_sections_priority.append("五、角色声音约束")
        if any(s in str(scene_types) for s in ["战斗", "动作"]):
            skill_sections_priority.append("4.4 核心叙事技法")
        skill_sections_priority.append("六、禁词与配额词")  # 总是要看
        must_read.append({
            "path": "_数据库/作者风格_skill.md",
            "priority": "P0",
            "focus": f"按 grep -n 锚点 Read 以下段落（不读全文！）：{skill_sections_priority}。每段约 30-50 行；全文 1455 行不要 Read 整文件",
            "reason": "蒸馏库散文规则——分段读取避免 token 爆炸（v17.5 B3.1）",
        })

    if world_hits:
        must_read.append({
            "path": "_数据库/世界观.json",
            "priority": "P1",
            "focus": f"只读 entries 中 id ∈ {[h['id'] for h in world_hits[:5]]}",
            "reason": "本章大纲命中的世界观条目",
        })

    if events:
        must_read.append({
            "path": "_数据库/事件表.json",
            "priority": "P1",
            "focus": f"pending_events 中 id ∈ {[e.get('id') for e in events]}",
            "reason": "本章可触发事件",
        })

    if scene_rule:
        must_read.append({
            "path": "_数据库/场景规则.json",
            "priority": "P1",
            "focus": f"scene_types = {scene_rule['scene_types']}（本章命中 {len(scene_rule['rules'])} 条规则）",
            "reason": "本章场景写作规则（对话比例/节奏/焦点）",
        })

    exp_entries = s.experience_entries()
    if exp_entries:
        # v19.2: P2 → P1 升级，failure_patterns 累积教训不允许跳过
        fail_count = sum(1 for e in exp_entries if e.get("category") == "failure" or "avoid_by" in e)
        priority = "P1" if fail_count >= 3 else "P2"
        must_read.append({
            "path": "_数据库/写作经验.json",
            "priority": priority,
            "focus": f"必读 failure_patterns（{fail_count} 条 avoid_by 教训）+ 适用 success_patterns（{len(exp_entries)-fail_count} 条）",
            "reason": f"含 {fail_count} 条历史失败教训，跳过 = 重复犯错",
        })

    user_prefs = s.user_preferences()
    if user_prefs:
        must_read.append({
            "path": "_数据库/用户偏好.json",
            "priority": "P1",
            "focus": f"必读 confidence>=0.6 的条目（{len(user_prefs)} 条）",
            "reason": "用户口味是硬约束（风格基线 + 节奏偏好）",
        })

    # 章纲摘要：v17.5 P1.4 分级注入 — 最近 5 章 + 卷起始章 + 关键事件章
    summaries = s.load("章纲摘要", {}).get("chapters", [])
    if summaries:
        # 1) 最近 5 章
        recent = [x for x in summaries if x.get("ch", x.get("chapter", 0)) < chapter][-5:]
        # 2) 卷起始章（每卷第一章）
        volumes_data = s.load("进度", {}).get("volumes", [])
        volume_starts_chs = {v.get("chapter_range", [0])[0] for v in volumes_data}
        volume_starts = [x for x in summaries
                        if x.get("ch", x.get("chapter", 0)) in volume_starts_chs
                        and x.get("ch", x.get("chapter", 0)) < chapter
                        and x not in recent]
        # 3) 关键事件章（标 has_key_event 或 emotion_value 绝对值 >= 7）
        key_event_chs = [x for x in summaries
                        if (x.get("emotion_value", 0) and abs(x.get("emotion_value", 0)) >= 7)
                        or x.get("has_key_event", False)
                        and x not in recent and x not in volume_starts
                        and x.get("ch", x.get("chapter", 0)) < chapter]
        # 合并去重
        focus_chs = [r.get("ch") for r in recent]
        if volume_starts:
            focus_chs.append(f"卷首 {[v.get('ch') for v in volume_starts]}")
        if key_event_chs:
            focus_chs.append(f"关键事件 {[k.get('ch') for k in key_event_chs]}")
        if recent or volume_starts or key_event_chs:
            must_read.append({
                "path": "_数据库/章纲摘要.json",
                "priority": "P1",
                "focus": f"最近 5 章 + 卷首 + 关键事件章：{focus_chs}",
                "reason": (
                    f"前文走向（分级：最近 {len(recent)} 章 + 卷首 {len(volume_starts)} 章 + "
                    f"关键事件 {len(key_event_chs)} 章 — 防止 ch100+ 时 token 爆炸）"
                ),
            })

    # v17.5 C1: RAG 检索注入（长期记忆，对抗业界 65% memory drift）
    if rag_hits:
        rag_chs = [h.get("chapter") for h in rag_hits]
        must_read.append({
            "path": "_数据库/章纲摘要.json (RAG 检索)",
            "priority": "P0",
            "focus": (
                f"基于本章 plan TF-IDF 检索最相关 {len(rag_hits)} 章：{rag_chs}。"
                f"特别关注这些章的伏笔/角色状态/未回收钩子"
            ),
            "reason": (
                f"业界数据：ch1 details 到 ch8 被稀释；65% 企业 AI 失败 = memory drift。"
                f"RAG 帮你找到本章 plan 语义最相关的历史章节（top-{len(rag_hits)} 由 rag_retriever 计算）"
            ),
            "rag_hits": rag_hits,
        })

    # v17.5 P3.1: 风格库源↔副本 md5 校验
    style_sync_warning = None
    style_lib_path = project_root.parent.parent / "styles"
    if style_lib_path.exists():
        progress = s.load("进度", {})
        style_lib_name = progress.get("style_library_path", "").replace("workspace/styles/", "")
        if style_lib_name:
            import hashlib
            source = style_lib_path / style_lib_name / "作者风格_FINAL.json"
            copy = project_root / "_数据库" / "作者风格.json"
            if source.exists() and copy.exists():
                src_md5 = hashlib.md5(source.read_bytes()).hexdigest()[:8]
                cpy_md5 = hashlib.md5(copy.read_bytes()).hexdigest()[:8]
                if src_md5 != cpy_md5:
                    style_sync_warning = (
                        f"风格库源/副本 md5 不一致：源={src_md5} vs 副本={cpy_md5}。"
                        f"考虑 cp {source} {copy} 同步"
                    )
                    print(f"[WARN] {style_sync_warning}", file=sys.stderr)

    # v17.5 P3.2: lessons 注入（跨项目教训） · v19.3 升级
    lessons_root = project_root.parent.parent.parent / "core" / "claude-home" / "lessons"
    lessons_files = []
    if lessons_root.exists():
        lessons_files = sorted(lessons_root.glob("*.md"))
        if lessons_files:
            must_read.append({
                "path": f"core/claude-home/lessons/（{len(lessons_files)} 个文件）",
                "priority": "P2",
                "focus": "writer/judge 启动前阅读：跨项目经验沉淀（含 §1-10 各类教训）",
                "reason": f"避免重复历史错误",
                "files": [str(p.relative_to(project_root.parent.parent.parent)) for p in lessons_files],
            })

    # v19.3: 全局 MEMORY 跨项目 feedback 注入（高价值）
    # 从 C:/Users/<user>/.claude/projects/<harness_dir>/memory/ 取 feedback_*.md 摘要（Claude Code user data）
    # harness_dir = cwd 的 dirname 化形式（如 X:\path\to\project → X--path-to-project）
    import os as _os
    user_home = Path(_os.path.expanduser("~"))
    memory_files = []
    cand_root = user_home / ".claude" / "projects"
    if cand_root.is_dir():
        # 优先匹配当前 cwd 对应的 harness dir
        cwd = Path(_os.getcwd())
        # Windows: X:\path\to\project → X--path-to-project
        cwd_str = str(cwd).replace(":", "-").replace("\\", "-").replace("/", "-")
        # 候选 dirname 列表（优先精确匹配 cwd，然后挨个找含 feedback 的）
        preferred_names = [cwd_str, cwd_str.rstrip("-")]
        all_subs = sorted(cand_root.iterdir(), key=lambda p: p.name)
        # 先试精确匹配
        for name in preferred_names:
            target = cand_root / name
            if target.is_dir():
                mem = target / "memory"
                if mem.is_dir():
                    memory_files = sorted(mem.glob("feedback_*.md"))
                    break
        # 兜底：扫所有项目找到 feedback 数最多的
        if not memory_files:
            best_count = 0
            for sub in all_subs:
                mem = sub / "memory"
                if mem.is_dir():
                    files = list(mem.glob("feedback_*.md"))
                    if len(files) > best_count:
                        best_count = len(files)
                        memory_files = sorted(files)
    if memory_files:
        # 取前 12 条最新（按修改时间）
        memory_files = sorted(memory_files, key=lambda p: p.stat().st_mtime, reverse=True)[:12]
        # 抽 description 字段（frontmatter 第一行 description 后内容）
        digest = []
        for mp in memory_files:
            try:
                lines = mp.read_text(encoding="utf-8").splitlines()
                desc = ""
                for ln in lines[:8]:
                    if ln.startswith("description:"):
                        desc = ln[len("description:"):].strip()
                        break
                if desc:
                    digest.append({"file": mp.name, "desc": desc[:160]})
            except Exception:
                continue
        if digest:
            must_read.append({
                "path": "全局 MEMORY feedback (跨项目元教训)",
                "priority": "P1",
                "focus": "审查 12 条元教训摘要 + 命中本场景的展开细节",
                "reason": f"含 {len(digest)} 条跨项目元失败模式（catchphrase 单一化 / 章节衔接断层 / offscreen 消费链 / 词汇 vs 结构 anti-slop 等）",
                "digest": digest,
            })

    # v17.8 DCAS：检测前章是否切了 pre_opening 给本章
    pre_opening_path = project_root / "章节" / f"第{chapter:03d}章" / ".pre_opening.txt"
    has_pre_opening = pre_opening_path.exists()
    pre_opening_word_count = 0
    if has_pre_opening:
        try:
            pre_opening_word_count = len(pre_opening_path.read_text(encoding="utf-8").replace(" ", "").replace("\n", ""))
        except Exception:
            pass
        must_read.append({
            "path": f"章节/第{chapter:03d}章/.pre_opening.txt",
            "priority": "P0",
            "focus": (
                f"v17.8 DCAS：本章开头继承自上章 splitter 切割。"
                f"约 {pre_opening_word_count} 字。直接作为本章正文开头使用，不要重写。"
                f"在此基础上继续写 ~2000-3000 字"
            ),
            "reason": "DCAS 双章自然衔接——上章 splitter 已选最佳截断点，本章是延续",
        })

    # v17.8 DCAS：决策本章是否启用双章生成模式
    # 默认从 ch4 起启用（v17.8 commit 时间点后）
    # 用户偏好里可改 dcas_threshold
    user_prefs = s.load("用户偏好", {"preferences": []})
    prefs_list = user_prefs.get("preferences", []) if isinstance(user_prefs, dict) else []
    dcas_threshold = 4  # 默认 ch4 起
    for p in prefs_list:
        if p.get("key") == "dcas_threshold":
            dcas_threshold = p.get("value", 4)
    dcas_enabled = chapter >= dcas_threshold and not has_pre_opening
    # 如果本章本身有 pre_opening（说明是被前章切出来的），不启用 DCAS（不能"再切一次")
    # 只有"原章"启用 DCAS 生成 6000 字后切割

    # v17.3: style_directive 注入（蒸馏→写作落地强制契约）
    style_directive = None
    if s.has_style_profile() and _build_style_directive is not None:
        try:
            style_directive = _build_style_directive(project_root, chapter)
            # 同步写到 _数据库/.style_directive/ch_NNN.json 供 writer Read
            directive_path = project_root / "_数据库" / ".style_directive" / f"ch_{chapter:03d}.json"
            directive_path.parent.mkdir(parents=True, exist_ok=True)
            directive_path.write_text(
                json.dumps(style_directive, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            must_read.append({
                "path": f"_数据库/.style_directive/ch_{chapter:03d}.json",
                "priority": "P0",
                "focus": (
                    f"opening_type={style_directive['opening_type']} (avoid={style_directive['opening_avoid']}); "
                    f"ending_type={style_directive['ending_type']} (avoid={style_directive['ending_avoid']}); "
                    f"transitions={style_directive['transition_top3']}"
                ),
                "reason": "蒸馏库的硬性风格指令——本章必须显式应用，CHANGES.applied_style 中报告",
            })
        except Exception as e:
            print(f"[WARN] style_injector 失败: {e}", file=sys.stderr)

    # v15: 写后校验指令
    post_write_checks: list[dict] = [
        {
            "tool": "validate_chapter.py",
            "args": f'"{project_root}" {chapter}',
            "required": True,
        },
    ]
    if s.has_style_profile():
        post_write_checks.append({
            "tool": "validate_style.py",
            "args": f'"{{chapter_file}}" --style "{s.db / "作者风格.json"}" --strict',
            "required": False,  # 不阻塞主流程
        })

    # ============ 最终 manifest ============
    return {
        "chapter": chapter,
        "project": project_root.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "preflight": preflight,
        "volume": {
            "vol": volume.get("vol") if volume else None,
            "title": volume.get("title") if volume else None,
            "arc": volume.get("volume_arc") if volume else None,
            "ending_state": volume.get("ending_state") if volume else None,
        } if volume else None,
        "active_characters": active_chars,
        "active_offscreen_actions": _collect_offscreen_actions(s, chapter),
        "active_fate_events": _collect_active_fate_events(s, chapter),
        "world_state_snapshot": _collect_world_state_snapshot(s, chapter),
        "active_clocks": _collect_active_clocks(s, chapter),
        "research_cache_ref": _collect_research_cache_ref(s, chapter),
        "event_cluster_context": _collect_event_cluster_context(s, chapter),
        "storyteller_directive": _collect_storyteller_directive(s, chapter),
        "protagonist_stress": _collect_protagonist_stress(s, chapter),
        "active_aspects": _collect_active_aspects(s, chapter),
        "fate_dice_hint": _collect_fate_dice_hint(s, chapter),
        "ensemble_layer": _collect_ensemble_layer(s, chapter),
        "user_preferences_v21": _collect_user_preferences_v21(s),
        "relevant_heuristics": _collect_relevant_heuristics(s, chapter, top_k=5),
        "hub_directive": _collect_hub_directive(s, chapter),
        "character_moves": _collect_character_moves(s, chapter, active_chars),
        "position_effect_template": _collect_position_effect_template(s, chapter),
        "throughlines": _collect_throughlines(s, chapter),
        "distill_continuity_template": _collect_distill_continuity(s),
        "arc_template": _collect_arc_template(s, chapter),
        "title_style": _collect_title_style(s),                # v22.4dim N5: 章节标题命名指纹
        "naming_convention": _collect_naming_convention(s),     # v22.4dim N5: 角色命名规范
        "main_character_arcs": _collect_main_character_arcs(s, top_k=3),  # v22.4dim Round2: 原作主角 Stanford 6 维参考
        "distill_voice_packs_reference": _collect_distill_voice_refs(s),
        "distill_golden_few_shot": _collect_golden_few_shot(s, chapter),
        "selective_history_retrieval": _collect_selective_history(s, chapter, top_k=3),
        "reader_preferences": _collect_reader_preferences(s),
        "prev_judge_findings": _collect_prev_judge_findings(s, chapter, lookback=3),
        "active_relationships": _collect_active_relationships(s, active_chars),
        "faction_standings_snapshot": _collect_faction_standings(s),
        "will_learn_due_this_ch": _collect_will_learn_due(s, chapter),
        "pending_secrets_to_reveal": _collect_secrets_to_reveal(s, chapter),
        "foreshadowing_summary": foreshadow_summary,
        "world_keyword_hits": world_hits[:5],
        "triggerable_events": [e.get("id") for e in events],
        "relationships_loaded": rel_hits,
        "items_loaded": item_hits,
        "time_snapshot": time_snap,
        "scene_rule_matched": scene_rule,
        "character_positions": positions_matched,
        "recent_openings": recent_openings,
        "style_directive": style_directive,
        "dcas_enabled": dcas_enabled,
        "dcas_word_target": 6500 if dcas_enabled else (3000 - pre_opening_word_count if has_pre_opening else 3000),
        "has_pre_opening": has_pre_opening,
        "pre_opening_word_count": pre_opening_word_count,
        "writer_mode": "dcas" if dcas_enabled else "single",
        "rag_relevant_chapters": rag_hits,
        "memory_search_results": memory_hits,
        "database_coverage": s.coverage_report(),
        "must_read": must_read,
        "hard_constraints": _build_hard_constraints(s, foreshadow_summary),
        "post_write_checks": post_write_checks,
        "instructions_for_subagent": (
            "你是第 {ch} 章写作子代理。开写前严格按以下步骤操作：\n"
            "1. 逐项 Read must_read 中 priority=P0 的文件\n"
            "2. 再 Read priority=P1 的文件\n"
            "3. priority=P2 的按需读取（字数/注意力紧张时可跳过）\n"
            "4. **主动补读权**：写作中若发现 manifest 未覆盖但必要的信息"
            "（例如某个角色未列入出场但对话里被提及），主动 Read 对应 JSON\n"
            "5. 写初稿 → Bash 调用 validate_chapter.py 第{ch}章.txt\n"
            "6. 读 validate 报错 → Edit 修正 → 再 validate\n"
            "7. validate 通过才算完成，最多循环 3 轮"
        ).format(ch=chapter),
        # v21 P1 cache layout 分层（agent prompt 按此顺序展示可最大化 Anthropic prompt caching 命中）
        "_cache_layout": _build_cache_layout(),
        # v21 P2-3 LiM 缓解：关键约束在末尾再次摘要（头部 P0 详细 + 尾部 critical_summary 强调）
        "_critical_summary": _build_critical_summary(chapter, foreshadow_summary,
                                                    must_read, locals().get("foreshadow_summary", {})),
    }


def _build_cache_layout() -> dict:
    """v21 P1-1+P1-2: manifest cache 友好分层。

    STATIC（cacheable 99%）：跨章几乎不变的静态参考（蒸馏/常量模板/Propp）
    SEMI_STATIC（cacheable 70-80%）：本卷内变化的（character_arc/世界状态/事件池/角色池）
    DYNAMIC（cacheable 30%）：每章必变的（chapter_plan/上章 changes/prev_judge_findings）

    agent prompt 设计：按 STATIC → SEMI_STATIC → DYNAMIC 顺序排放，Anthropic API 自动 detect prefix → 命中率最高。
    """
    return {
        "_doc": "v21 P1: Anthropic prompt caching 友好分层。agent prompt 顶部按本表顺序展示字段，可命中 prefix cache 节省 60-90% token 成本。",
        "STATIC_99_cacheable": [
            "distill_continuity_template",       # 蒸馏散文衔接模板
            "distill_voice_packs_reference",     # 原作角色风格 DNA
            "distill_golden_few_shot",           # 蒸馏 golden_passages
            "title_style",                       # v22.4dim N5: 章节标题命名指纹（全书不变）
            "naming_convention",                 # v22.4dim N5: 角色命名规范（全书不变）
            "main_character_arcs",               # v22.4dim Round 2: 原作主角 Stanford 6 维参考（全书不变）
            "position_effect_template",          # R2.3 双轴判定模板（全局常量）
            "_cache_layout",                     # 本字段自身（元数据）
        ],
        "SEMI_STATIC_90_cacheable_v22": [
            "arc_template",                      # v22.cluster: 本章所属 cluster 的 arc 模板（同 cluster 内 manifest 完全相同 → cache hit ratio ≈ cluster.chapters_count/总章数）
        ],
        "SEMI_STATIC_70_cacheable": [
            "active_fate_events",                # 卷内大势事件池
            "world_state_snapshot",              # 世界数值（卷间慢变）
            "active_aspects",                    # 角色永久烙印（一旦获得永久）
            "throughlines",                      # 4 线弧光
            "ensemble_layer",                    # 群像档（NPC schedule 不变）
            "hub_directive",                     # 枢纽场景（卷间稳定）
            "character_moves",                   # 角色 moves（持久）
            "reader_preferences",                # 读者偏好（更新慢）
            "active_relationships",              # 关系数值（每章微变）
            "faction_standings_snapshot",        # 阵营立场
        ],
        "DYNAMIC_30_cacheable": [
            "chapter",                           # 当前章号
            "must_read",                         # 当前章需读文件清单（每章不同）
            "active_clocks",                     # urgent 状态每章变
            "storyteller_directive",             # next_target_outcome 每章变
            "protagonist_stress",                # stress 累计每章变
            "fate_dice_hint",                    # 本章抽签状态
            "active_offscreen_actions",          # 本章生效幕后
            "selective_history_retrieval",       # 本章 query 检索结果
            "prev_judge_findings",               # 上章 judge 发现
            "will_learn_due_this_ch",            # 本章必学知识
            "pending_secrets_to_reveal",         # 本章必揭秘
            "hard_constraints",                  # 本章硬约束（含本章伏笔到期）
            "post_write_checks",                 # 本章写后检查
            "_critical_summary",                 # 本章 LiM 关键摘要（动态）
        ],
        "instructions": (
            "Agent prompt 推荐结构：\n"
            "  [section A: system 指令]（100% cacheable）\n"
            "  [section B: STATIC_99 字段]（99% cacheable，跨章不变）\n"
            "  [section C: SEMI_STATIC_70 字段]（卷内不变）\n"
            "  [section D: DYNAMIC_30 字段]（本章动态）\n"
            "  [section E: 本章具体任务]\n"
            "Anthropic API 自动 detect 最长 prefix 并 cache。"
        ),
    }


def _build_critical_summary(chapter: int, foreshadow_summary: dict,
                             must_read: list, _extra: dict) -> dict:
    """v21 P2-3: Lost-in-the-Middle 缓解。manifest 关键字段头尾双放——本字段是末尾再次强调。

    研究：长 prompt 中间字段会被 LLM 忽略（Lost-in-the-Middle）。
    解：关键约束在头部 must_read.P0 详细列出，在末尾本字段再次摘要（关键词级），
    LLM 即便忽略中间，头尾必看。
    """
    p0_files = [m.get("path") for m in must_read if m.get("priority") == "P0"]
    return {
        "_doc": "v21 P2-3 LiM 缓解：本字段是关键约束的末尾强调，与头部 P0 详细描述形成头尾双放。",
        "this_chapter": chapter,
        "p0_files_dont_skip": p0_files,
        "critical_reminders": [
            "🚨 P0 文件必读不可跳",
            "🚨 hard_constraints 中所有 hard_gate 项必触发/必满足",
            "🚨 active_aspects 的 narrative_constraints 不可违反",
            "🚨 urgent clocks (remaining ≤ 2) 必埋暗示",
            "🚨 pending_heart_event_reveals 必触发",
            "🚨 storyteller next_target_outcome=setback 时必至少 1 个真实挫败",
            "🚨 prev_judge_findings 必消费（不能再犯同样错误）",
            f"🚨 本章伏笔到期：Tier-1={foreshadow_summary.get('tier1_due_count', 0)} / "
            f"必揭秘={foreshadow_summary.get('must_reveal_this_ch', 0)}",
        ],
    }


def main():
    if len(sys.argv) < 3:
        print("用法: python build_manifest.py <项目路径> <章节号>", file=sys.stderr)
        sys.exit(1)
    project_root = Path(sys.argv[1]).resolve()
    chapter = int(sys.argv[2])
    if not project_root.exists():
        print(f"项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(1)

    manifest = build_manifest(project_root, chapter)
    out_path = project_root / "_数据库" / ".manifest" / f"ch_{chapter:03d}.json"
    save_json(out_path, manifest)

    # v19.4: budget check - 防 manifest 字段累积膨胀导致 writer context 爆炸
    manifest_size = out_path.stat().st_size
    manifest_kb = manifest_size / 1024
    BUDGET_SOFT_KB = 50
    BUDGET_HARD_KB = 100
    if manifest_kb >= BUDGET_HARD_KB:
        print(f"[BUDGET-ERROR] manifest 体积 {manifest_kb:.1f}KB 超硬上限 {BUDGET_HARD_KB}KB", file=sys.stderr)
        print(f"  分级裁剪建议：①砍 P2 must_read 项 ②digest/focus 字段截短 ③recent_openings 仅保 1 章", file=sys.stderr)
    elif manifest_kb >= BUDGET_SOFT_KB:
        print(f"[BUDGET-WARN] manifest 体积 {manifest_kb:.1f}KB 接近软上限 {BUDGET_SOFT_KB}KB", file=sys.stderr)

    print(f"[OK] manifest 已生成: {out_path} ({manifest_kb:.1f}KB)")

    # v21 P2.1: 自动生成 compressed 版本（agent prompt 推荐用 compressed 省 token）
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import manifest_compress
        compressed = manifest_compress.compress(manifest)
        compressed_path = project_root / "_数据库" / ".manifest" / f"ch_{chapter:03d}_compressed.json"
        save_json(compressed_path, compressed)
        compressed_kb = compressed_path.stat().st_size / 1024
        savings_pct = (1 - compressed_kb / manifest_kb) * 100 if manifest_kb else 0
        print(f"[OK] compressed 版本: {compressed_path} ({compressed_kb:.1f}KB, 省 {savings_pct:.0f}%) — agent prompt 推荐用此版")
    except Exception as e:
        print(f"[WARN] manifest_compress 失败: {e}")

    if not manifest["preflight"]["passed"]:
        print("[FATAL] 预检失败:", file=sys.stderr)
        for f in manifest["preflight"]["fatal"]:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(2)
    if manifest["preflight"]["warning"]:
        print("[WARN]")
        for w in manifest["preflight"]["warning"]:
            print(f"  - {w}")

    # Coverage 自检
    cov = manifest.get("database_coverage", {})
    if cov.get("unscanned"):
        print(f"[COVERAGE] 发现未识别的数据库文件，build_manifest.py 未扫描:")
        for f in cov["unscanned"]:
            print(f"  - {f}.json  ← 新增文件？请更新 DatabaseScanner.KNOWN_DBS 并添加扫描逻辑")
    if cov.get("known_but_missing"):
        print(f"[COVERAGE] 已知数据库文件缺失（可能是项目刚初始化）:")
        for f in cov["known_but_missing"]:
            print(f"  - {f}.json")


if __name__ == "__main__":
    main()
