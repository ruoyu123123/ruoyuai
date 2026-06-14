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
import re  # 2026-05-29 北极星复审 R1：模块级 re（_build_volume_convergence_anchor:1046 +
            # 旧 1801/1824 裸用 re. 但无模块 import → fluid 涌现 cluster_002+ 走 vol 反查分支 NameError
            # → 被 try 吞成 event_cluster mode:error → writer 丢 cluster context。补此根治）
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写
import cluster_lookup  # noqa: E402  2026-05-29 修：obtained_cluster 是 cluster_id 不是章号

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

# style_profile_extractor：L1a 升格 —— 把作者句长分位数升格为「写作时显式下发 writer 的
# 多维量化风格指纹（显式目标硬数字）」。env PROFILE_INJECT_MODE 默认 off（影子·接通不改默认 manifest）。
try:
    import style_profile_extractor as _style_profile_extractor
except ImportError:
    _style_profile_extractor = None


# ============ IO helpers ============

def _bp_items(prog):
    """2026-05-29 复审修复（SC-1）：cluster_blueprint 规范形态=dict（cluster_id->data）。
    城南项目实测是 list(25)（scene_storyboard 列表，各项无 cluster_id）→ 裸 .items() 直接崩。
    统一经 cluster_lookup.normalize_blueprint 归一为 dict 再 .items()；若该函数尚未落地
    （并行批次顺序问题），就地 isinstance 守卫兜底（list/非 dict → 返回空 dict 不崩）。
    """
    if not isinstance(prog, dict):
        return {}
    bp = prog.get("cluster_blueprint")
    try:
        norm = cluster_lookup.normalize_blueprint(prog)
        if isinstance(norm, dict):
            return norm
    except AttributeError:
        pass  # normalize_blueprint 尚未由 SC-1 owner 落地 → 走下方就地守卫
    except Exception:
        pass
    if isinstance(bp, dict):
        return bp
    if isinstance(bp, list):
        # list 项若带 cluster_id 则归一；城南这种各项无 cluster_id 的 → {}（不崩，退回 事件簇 fallback）
        out = {}
        for item in bp:
            if isinstance(item, dict):
                cid = item.get("cluster_id")
                if cid:
                    out[cid] = item
        return out
    return {}


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
        # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只用 故事块摘要
        "作者风格", "故事块摘要", "写作经验", "用户偏好",
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

    def current_scene(self) -> dict | None:
        prog = self.load("进度", {})

        # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
        # 2026-05-29 复审修复（SC-1）：经 _bp_items 归一（list 项目不崩）。
        for cluster_id, cluster_data in _bp_items(prog).items():
            if not isinstance(cluster_data, dict):
                continue
            for p in cluster_data.get("scene_storyboard", []):
                if p.get("ch") == self.ch:
                    return p
        # cluster_blueprint 缺失时，从 事件簇.json.clusters[N] 取雏形
        # 哲学：cluster mode 下章数由 splitter step 6 决定 · cluster_blueprint 不应被预先锁
        shijianji = self.load("事件簇", {})
        for cluster in shijianji.get("clusters", []):
            cr = cluster.get("chapter_range") or []
            if isinstance(cr, list) and len(cr) == 2 and cr[0] <= self.ch <= cr[1]:
                storyboard = cluster.get("scene_storyboard") or []
                if storyboard:
                    first_scene = storyboard[0] if storyboard else {}
                    return {
                        "ch": self.ch,
                        "vol": cluster.get("vol"),
                        "cluster": cluster.get("cluster_id"),
                        "title": ((cluster.get("title") or "") + " · 起首待 splitter 切定") if self.ch == cr[0] else (cluster.get("title") or ""),
                        "characters": first_scene.get("characters", []) or [
                            c for s in storyboard for c in (s.get("characters") or [])
                        ][:8],
                        "key_events": [s.get("title", "") for s in storyboard[:3]],
                        "scene_type": [first_scene.get("type", "悬疑")],
                        "goal": (cluster.get("scope_summary") or "")[:200],
                        "_fluid_fallback_from_event_cluster": True,
                        "_v26_note": "本 cluster_blueprint 由 build_manifest 从 事件簇.json fluid fallback 产生 · 真实切章由 step 6 splitter 决定",
                    }
        return None

    def volume_info(self) -> dict | None:
        prog = self.load("进度", {})
        for v in prog.get("volumes", []):
            lo, hi = v.get("chapter_range", [0, 0])
            if lo <= self.ch <= hi:
                return v
        return None

    def active_characters(self) -> list[str]:
        plan = self.current_scene()
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
            # 2026-05-29 复审复修 [M4]：到期判定消费三种来源（根治「永久 pending 永不到期」）：
            #  ① 旧 schema due_by（章号）；② due_by_cluster（归一 cluster → 起始章）；
            #  ③ pending：setup_cluster 起始章 + due_by_ch_offset。
            _due = False
            _due_by = p.get("due_by")
            if isinstance(_due_by, int) and not isinstance(_due_by, bool) and _due_by <= self.ch:
                _due = True
            else:
                _dbc = p.get("due_by_cluster")
                if isinstance(_dbc, str) and _dbc:
                    _rng = cluster_lookup.cluster_id_to_range(self.db, _dbc)
                    if _rng and self.ch >= int(_rng[0]):
                        _due = True
                elif p.get("due_by_pending_resolution"):
                    _setc = p.get("setup_cluster")
                    _rng = cluster_lookup.cluster_id_to_range(self.db, _setc) if _setc else None
                    if _rng and self.ch >= int(_rng[0]) + int(p.get("due_by_ch_offset", 20)):
                        _due = True
            if _due:
                tier = p.get("tier", 3)
                if tier == 1:
                    result["promises_tier1_due"].append(p)
                elif tier == 2:
                    result["promises_tier2_due"].append(p)

        for d in data.get("deadlines", []):
            _dl = d.get("deadline_ch")
            # 与 promises.due_by [M4] 同款防御：deadline_ch 可为 None（无固定截止，如循环类）→ 不判到期、不崩
            if d.get("status") == "pending" and isinstance(_dl, int) and not isinstance(_dl, bool) and _dl <= self.ch:
                result["deadlines_due"].append(d)

        for pl in data.get("pledges", []):
            if pl.get("status") == "active":
                result["active_pledges"].append(pl)

        for s in data.get("secrets", []):
            if s.get("status") == "hidden":
                result["hidden_secrets"].append(s)
                # v2 cluster 化修正（2026-05-28 · cluster_002 ch5 翻车 bug fix）：
                # reveal_at_cluster="cluster_005" 是 cluster ID 不是 chapter 号，
                # 必须比对当前 章 所属 cluster ID（通过 cluster_blueprint 反查），
                # 不能直接 int 数字（之前 bug：cluster_005 → 抽数字 5 → 与 ch=5 撞）
                rc = s.get("reveal_at_cluster")
                if isinstance(rc, str) and rc:
                    cur_cluster_id = self._current_cluster_id()
                    if cur_cluster_id and rc == cur_cluster_id:
                        result["reveal_this_ch"].append(s)
                elif s.get("reveal_at_pending_resolution"):
                    # 2026-05-29 复审复修 [M4]：未指定 reveal cluster → 读时解析
                    # established cluster 起始章 + reveal_at_ch_offset = 目标章；
                    # 当前章达到即揭晓（消费 save_state 写入的 pending 标记，根治「永不揭晓」）。
                    _est = s.get("established_cluster")
                    _rng = cluster_lookup.cluster_id_to_range(self.db, _est) if _est else None
                    if _rng and self.ch >= int(_rng[0]) + int(s.get("reveal_at_ch_offset", 50)):
                        result["reveal_this_ch"].append(s)
        return result

    def _current_cluster_id(self) -> str | None:
        """反查当前 ch 所属 cluster_id。
        2026-05-30 北极星复审：委托 cluster_lookup.ch_to_cluster_id（唯一权威·事件簇优先 +
        _pick_unambiguous 歧义处理），删除原 blueprint 优先的第二套实现——它与
        _collect_will_learn_due/_collect_secrets_to_reveal 用的 cluster_lookup 在 range 冲突项目
        （进度.blueprint 与事件簇 chapter_range 不一致）上分歧，致同一 manifest 的 SECRET_NOT_REVEALED
        hard_gate 计数与 writer 揭秘提示自相矛盾。北极星：cluster_lookup 是章号⇄cluster 唯一权威反查。"""
        return cluster_lookup.ch_to_cluster_id(self.db, self.ch)

    def world_keyword_hits(self) -> list[dict]:
        """世界观按关键词匹配（本章大纲命中哪些条目）。"""
        world = self.load("世界观", {})
        entries = world.get("entries", [])
        plan = self.current_scene() or {}
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
        plan = self.current_scene() or {}
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
        plan = self.current_scene() or {}
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
        # efficacy 闭环（2026-05-31）：active==False 的约束 = 注入后误报没降 → 已自动停注
        # （advisory 软停·learning_loop 标记·不硬删·北极星⑤）。此处不再注入下章 writer。
        for e in exp.get("failure_patterns", []):
            if e.get("active") is False:
                continue
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
            holder = it.get("holder") or ""  # 容忍缺 key 与显式 null（未获得道具 holder=null 合法·防 line425 `h in holder` 崩）
            # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 obtained_cluster
            # 2026-05-29 修：obtained_cluster 是 cluster_id，原代码抽出 cluster 序号直接和
            # 章号 self.ch 比是量纲错误。改用 cluster_id_to_range 取该 cluster 起始章 lo，
            # lo > self.ch（cluster 尚未开始）才跳过；反查不到 range 则不跳过（保守注入）。
            obtained_cluster = it.get("obtained_cluster", "cluster_999")
            _rng = cluster_lookup.cluster_id_to_range(self.db, obtained_cluster)
            if _rng is not None and _rng[0] > self.ch:
                continue
            # 持有者匹配出场角色 → 必须注入
            holder_hit = any(h in holder for h in active_ids)
            # 未回收的 chekhov 道具 → 也值得注入（契诃夫之枪）
            # 2026-05-29 修：原 `not confirmed_ch >= self.ch` 把已回收（confirmed_ch < self.ch）
            # 的道具误判为 live。正确语义：未设 confirmed_ch（0/None）或 confirmed_ch 在将来才算 live。
            cf = it.get("confirmed_ch") or 0
            chekhov_live = bool(it.get("chekhov")) and (cf == 0 or cf > self.ch)
            if holder_hit or chekhov_live:
                hits.append({
                    "id": it.get("id"), "name": it.get("name"),
                    "holder": holder, "chekhov": bool(it.get("chekhov")),
                })
        return hits

    def time_state(self) -> dict:
        """当前时间快照 + 本章是否命中时钟事件。

        2026-05-30 修（孤儿 #3 · clock/timeline producer/consumer 分歧）：
        world_clock_events 真实 schema 用 {date, event, vol[, cluster]}（见
        城南火葬场夜班/时间线.json + db_schema_validate.py 只 require `event` ·
        注释明写「world clock event 用 day/cluster 颗粒」）——全仓**无 producer 写
        `ch` 字段**。旧实现按废弃的 `e.get("ch") == self.ch` 过滤 → clock_events_this_ch
        恒空 → 时钟事件浮现机制死掉、时间线 must_read 永停 P1。

        2026-05-30 补全（batch6 #3 不完整修复 · 没调查没发言权）：3 个真实项目实测
        world_clock_events 是 **3 种 schema**，batch6 只认 `cluster`/`date` 字段名 →
        只覆盖城南（date），纵尸司/诡异因字段名不符仍孤儿（hits=0 被误当成功）：
          · 城南：{date, event, vol}             → date 颗粒（current_time.date）
          · 纵尸司：{day(整数日计数), event, impact} → day 颗粒（current_time.day）
          · 诡异：{absolute_time, cluster_revealed(="cluster_001 …"), event}
                  → cluster 颗粒（cluster_revealed）+ date 颗粒（absolute_time）
        改为 **tolerant 多 schema 字段别名兼容**（应对 AI 自由生成 schema 的通用策略 ·
        北极星②cluster 单位 · ③涟漪/大势驱动 · ⑤顾问层注入不碰 hard_gate 不干涉模型）：
          1. cluster 颗粒：event.cluster | event.cluster_revealed（诡异）
             经 cluster_lookup.normalize_cluster_id 归一（容忍 "cluster_001 (影印件…)"
             这类带后缀文本——regex 抽首个数字）比 本章所属 cluster_id
             （_current_cluster_id 唯一权威反查；反查不到时退回 current_time.cluster）
          2. date 颗粒：event.date | event.absolute_time（诡异）
             比 current_time.date | current_time.absolute_time
          3. day 颗粒：event.day（纵尸司整数日计数）比 current_time.day
          4. ch 颗粒：兼容仍带 `ch` 字段的旧数据（不破坏既有项目）
        颗粒优先级 cluster > date > day > ch（精准颗粒优先）。
        vol（卷）颗粒过粗——会把整卷每章都标命中——故意不用作 this_ch 命中。
        """
        data = self.load("时间线", {})
        current = data.get("current_time", {}) or {}
        # date 颗粒：current_time 的日期锚（兼容 date / absolute_time 别名）
        cur_date = current.get("date") or current.get("absolute_time")
        # day 颗粒：纵尸司用整数「第 N 日」计数（current_time.day == event.day）
        cur_day = current.get("day")
        # cluster 颗粒：优先 ch→cluster 权威反查，反查不到退回 current_time.cluster（诡异）
        cur_cid = self._current_cluster_id()
        cur_cid_norm = cluster_lookup.normalize_cluster_id(cur_cid) if cur_cid else None
        if cur_cid_norm is None:
            cur_cid_norm = cluster_lookup.normalize_cluster_id(current.get("cluster"))

        hits: list[dict] = []
        for e in data.get("world_clock_events", []) or []:
            if not isinstance(e, dict):
                continue
            matched_by = None
            # cluster 颗粒：cluster | cluster_revealed（诡异）字段别名
            ev_cluster = e.get("cluster") or e.get("cluster_revealed")
            ev_date = e.get("date") or e.get("absolute_time")  # date | absolute_time 别名
            ev_day = e.get("day")
            if (cur_cid_norm and ev_cluster is not None
                    and cluster_lookup.normalize_cluster_id(ev_cluster) == cur_cid_norm):
                matched_by = "cluster"
            elif cur_date and ev_date and ev_date == cur_date:
                matched_by = "date"
            elif cur_day is not None and ev_day is not None and ev_day == cur_day:
                matched_by = "day"
            elif e.get("ch") is not None and e.get("ch") == self.ch:
                matched_by = "ch"
            if matched_by:
                hit = dict(e)
                hit["_matched_by"] = matched_by
                hits.append(hit)
        return {
            "current_time": current,
            "current_cluster_id": cur_cid,
            "clock_events_this_ch": hits,
            "npc_schedules_count": len(data.get("npc_schedules", {})),
        }

    def scene_rule_for_chapter(self) -> dict | None:
        """基于本章 scene_type 抽取对应写作规则（合并多类型）。"""
        plan = self.current_scene() or {}
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
            # frozen-aware（狩猎修）：__file__ 在 PYZ 顶层·文件路径落空 → exe 下
            # 记忆层静默丢失（伤北极星）。模块已被 spec hiddenimports 收进 PYZ → 直接 import。
            import importlib
            try:
                ml = importlib.import_module("memory_layer")
            except ImportError:
                print("[build_manifest] WARN memory_layer 不可导入·记忆注入跳过",
                      file=sys.stderr)
                return []
            mem = ml.MemoryLayer(self.root, self.ch)
            plan = self.current_scene() or {}
            q = query or json.dumps(plan, ensure_ascii=False)[:200]
            return mem.search(q, top_k) if q else []
        except Exception:
            return []

    def rag_relevant_chapters(self, top_k: int = 3) -> list[dict]:
        """RAG检索：找到与当前章节最相关的历史章节片段。"""
        try:
            # frozen-aware（狩猎修·同 memory_layer）：PYZ 已收 → 直接 import。
            import importlib
            try:
                rag = importlib.import_module("rag_retriever")
            except ImportError:
                print("[build_manifest] WARN rag_retriever 不可导入·RAG 注入跳过",
                      file=sys.stderr)
                return []
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
        if not self.current_scene():
            fatal.append(f"cluster_blueprint 内 ch={self.ch} 不存在，大纲未覆盖本章")
        if not (self.db / "人物卡.json").exists():
            fatal.append("人物卡.json 不存在")
        if self.ch > 1 and self.previous_chapter_file() is None:
            fatal.append(f"上一章（第{self.ch-1}章）txt 文件未找到")

        plan = self.current_scene() or {}
        if not plan.get("characters"):
            warning.append("本章 characters 字段为空，将 fallback 到全量人物卡")
        if not plan.get("key_events") and not plan.get("summary"):
            warning.append("本章 key_events/summary 为空，大纲过于简略")
        if not plan.get("scene_type"):
            warning.append("本章 scene_type 未标注，跳过场景规则注入")

        # 2026-06-01 修：scene_storyboard 用 id 引用，旧版只比 name → id≠name 角色（如「乐园之声」
        # id≠name「乐园之声（广播）」）被误判新角色。改 id∪name 容错（减少误报方向·低回归风险）。
        _cards = self.load("人物卡", {}).get("characters", [])
        cards = {c.get("name") for c in _cards} | {c.get("id") for c in _cards}
        cards |= {a for c in _cards for a in (c.get("aliases") or [])}
        for n in plan.get("characters", []):
            if n not in cards:
                warning.append(f"出场角色「{n}」在人物卡中不存在（视为新角色）")
        return {"fatal": fatal, "warning": warning, "passed": len(fatal) == 0}


# ============ Manifest 生成 ============

def _collect_active_fate_events(scanner, chapter: int) -> dict:
    """v20 F5: 调 fate_engine evaluate 取本章应推进的大势事件。
    优先级 > cluster_blueprint（涌现式模式下 cluster_blueprint 可能为空）。
    """
    fate_path = scanner.root / "_数据库" / "大势卡.json"
    if not fate_path.exists():
        return {"mode": "strict", "active": [], "_note": "无大势卡，走传统 cluster_blueprint 模式"}
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
    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    # 2026-05-29 复审修复（SC-1）：经 _bp_items 归一（list 项目不崩）。
    ch_plan = {}
    for cid, cdata in _bp_items(progress).items():
        if not isinstance(cdata, dict):
            continue
        for sb in cdata.get("scene_storyboard", []):
            if sb.get("ch") == chapter:
                ch_plan = sb
                break
        if ch_plan:
            break
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

        # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 从 故事块摘要 + cluster_blueprint 读 chars
        chapter_chars = []
        # 优先读 故事块摘要.clusters[*].chapters[ch].characters
        summary = scanner.load("故事块摘要", {}) if hasattr(scanner, "load") else {}
        for cluster_entry in summary.get("clusters", []) if isinstance(summary, dict) else []:
            chs = cluster_entry.get("chapters", {}) if isinstance(cluster_entry, dict) else {}
            ch_data = chs.get(str(chapter)) if isinstance(chs, dict) else {}
            if isinstance(ch_data, dict) and ch_data.get("characters"):
                chapter_chars = ch_data["characters"]
                break
        # fallback 到 cluster_blueprint（2026-05-29 复审修复 SC-1：经 _bp_items 归一）
        if not chapter_chars:
            progress = scanner.load("进度", {}) if hasattr(scanner, "load") else {}
            for cid, cdata in _bp_items(progress).items():
                if not isinstance(cdata, dict):
                    continue
                for sb in cdata.get("scene_storyboard", []):
                    if sb.get("ch") == chapter:
                        chapter_chars = sb.get("characters", [])
                        break
                if chapter_chars:
                    break
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
            "_note": "writer step 0v 必读：must_reveal_count > 0 时本章必触发对应 reveal（physical_evidence 必出现）；NPC schedule 决定主角找他时的地点/状态。揭密后**必须**在 changes.factual.heart_events_revealed 报告 {event_id, evidence_appeared} —— save-state 据此把对应 heart_event 标 consumed（触发一次即消费，不再反复要求重揭）",
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


# 2026-05-29 复审修复（M16 · SC-6）：事件簇 cluster.status 中英文混用
# （进行中/已规划/未涌现/已完成 + done/in_progress/pending）。旧 filter 只认英文
# → 城南这种全中文 status 命中率 0 → event_cluster_context 永远 mode=off（注入丢失）。
# 这里把「该注入 context」的 status 中英文都收齐（active + 已落章语义）。
# 2026-05-29 复审复修（M16 二次修）：只收「真 active / 已落章」态，
# 排除 candidate 占位态（已规划/未涌现 + pending）——否则全占位项目（纵尸司）会命中占位块
# → 注入空 cluster_id 的伪 active cluster，且与 cluster_emergence_engine 的 candidate 排除集自相矛盾。
_EVENT_CLUSTER_ACTIVE_STATUSES = (
    "in_progress", "writer_done", "splitter_done", "done",
    "进行中", "已完成",
)


def _build_volume_convergence_anchor(scanner, cluster: dict) -> dict | None:
    """2026-05-29 北极星 P2 [M1-trend]：本卷「大势已定」的固定终点锚。

    「大势已定·每卷再怎么折腾最后方向一致」靠的是：让模型【始终看到本卷要收束到哪】，
    自己导航过去（软牵引/顾问，非硬契约——守原则5「不干涉模型判断」）。
    从 大势卡.json（有 final_image）+ 进度.json（key_milestones/ending_state）取本卷终点字段。
    """
    vol = cluster.get("vol")
    if vol is None:
        pm = cluster.get("parent_me") or ""
        m = re.search(r"V?(\d+)", str(pm))
        if m:
            vol = int(m.group(1))
    anchor = {}
    ds = scanner.load("大势卡", {}) or {}
    prog = scanner.load("进度", {}) or {}
    # 2026-05-30 北极星复审：合并大势卡 + 进度.json 两源——原「命中即 return、if not anchor 才回退」
    # 会因大势卡有占位 volume_arc 就短路，把进度.json 里真实的 ending_state/key_milestones（本卷真终点）
    # 挡在外，writer 拿到的收敛锚退化为占位串。兼容 final_image / ending_image 双字段名（大势卡实写 ending_image）。
    for src in (ds, prog):
        for v in (src.get("volumes") or []):
            if isinstance(v, dict) and v.get("vol") == vol:
                for k in ("ending_state", "key_milestones", "final_image",
                          "ending_image", "volume_arc", "core_conflict"):
                    if v.get(k) and k not in anchor:
                        anchor[k] = v[k]
                break
    if anchor.get("ending_image") and "final_image" not in anchor:
        anchor["final_image"] = anchor["ending_image"]
    if not anchor:
        return None
    anchor["_doc"] = ("大势已定：本卷无论小势（走向卡选择/涟漪）怎么折腾，最后都要收束到这里。"
                      "把它当方向锚——不限定你怎么写，但别让本块剧情偏离这个终点。")
    return anchor


def _collect_rolling_style_anchor(scanner, chapter: int) -> dict | None:
    """Rolling style anchor（2026-05-31 · 第 2 轮治 D 级长程文风退化 · 北极星①⑤⑥）。

    问题：长篇写到中后段，作者文风会**回归均值**退化成通用 LLM 腔（第 1 轮的静态开局 snippet
    是固定的，越写离它越远，锚不住）。本锚是**动态**的：每写下一个 cluster 时，从**本书已写的
    cluster 草稿里挑「离作者参考最近的 1-2 个片段」当锚**——用本书自己写得最像作者的段落
    重新锚定下一块，对抗回归均值。

    实现（纯 Python · 零 GPU · 复用 SFS · 顾问制软牵引）：
      1. 收集已写 cluster 草稿（cross_cluster_style_drift_scanner.collect_written_cluster_texts）。
      2. 定位作者原文参考（style_similarity_scanner.resolve_author_pool）。
         · 有参考 → 对每个已写 cluster 取头部代表片段，算 vs 作者参考的去题材 SFS，选 top-2 最贴。
         · 无参考 → 退化：选最近 1-2 个已写 cluster 头部片段（保证锚是「本书最新文风」· 不臆造作者）。
      3. 返回锚片段（截断到 ~600 CJK 控 prompt 体积）+ 说明。供 writer prompt 注入：
         「写下一块时，文风向这些本书已写得最贴作者的片段看齐」。
    全 advisory 软牵引（不限定写法 · 不 hard_gate · 守北极星⑤）。任何异常静默返回 None（不阻断 manifest）。
    """
    try:
        import sys as _sys
        _sd = str(Path(__file__).resolve().parent)
        if _sd not in _sys.path:
            _sys.path.insert(0, _sd)
        import cross_cluster_style_drift_scanner as ccsd  # type: ignore
        import style_similarity_scanner as _sss            # type: ignore
        import style_evaluator as _se                       # type: ignore
    except Exception:
        return None

    project_root = scanner.root
    try:
        written = ccsd.collect_written_cluster_texts(project_root, last_n=None)
    except Exception:
        written = []
    if not written:
        return None

    def _head_snippet(text: str, target_cjk: int = 600) -> str:
        """取片段头部约 target_cjk 汉字（在段落边界处收 · 控 prompt 体积 · 代表该 cluster 文风）。"""
        out, cjk = [], 0
        for para in text.split("\n"):
            out.append(para)
            cjk += _sss._cjk_count(para)
            if cjk >= target_cjk:
                break
        return "\n".join(out).strip()

    ref_text = None
    try:
        ref_text = ccsd.author_reference_text(project_root)
    except Exception:
        ref_text = None

    ranked: list[dict] = []
    if ref_text:
        # 有作者参考：按「头部片段 vs 作者参考」去题材 SFS 排序，选最贴的 top-2（动态最贴锚）
        for c in written:
            snip = _head_snippet(c["text"])
            if _sss._cjk_count(snip) < 200:
                continue
            try:
                sim = ccsd.style_similarity(ref_text, snip)
            except Exception:
                continue
            ranked.append({"cluster_id": c["cluster_id"], "similarity_to_author": sim, "snippet": snip})
        ranked.sort(key=lambda x: x["similarity_to_author"], reverse=True)
        selected = ranked[:2]
        anchor_basis = "vs_author_reference"
    else:
        # 无作者参考：退化为「最近 1-2 个已写 cluster」头部片段（本书最新文风当锚 · 不臆造作者）
        selected = []
        for c in written[-2:]:
            snip = _head_snippet(c["text"])
            if _sss._cjk_count(snip) >= 200:
                selected.append({"cluster_id": c["cluster_id"], "snippet": snip})
        anchor_basis = "recent_clusters_fallback"

    if not selected:
        return None
    return {
        "anchor_basis": anchor_basis,
        "author_pool_resolved": ref_text is not None,
        "anchors": selected,
        "_doc": ("Rolling style anchor（动态锚 · 软牵引）：下面是本书已写片段中**最贴作者文风**的"
                 "1-2 段（每写一块都重新挑选 · 对抗长篇文风回归均值退化成通用腔）。写下一个 cluster 时，"
                 "句长节奏 / 虚词标点 / 字组笔迹向它们看齐——这是顾问提示，不限定你写什么内容、不硬锁写法。"),
    }


def _collect_event_cluster_context(scanner, chapter: int) -> dict:
    """v23 ECAS: 注入本章所属事件簇的 context (cluster_id / brief / mid_checkpoints / foreshadowing)。
    writer 在 MODE=ecas 时必读此字段。
    决策树:
    1. 读 _数据库/事件簇.json 找 status in (active 词表 · 中英文都认) 且 chapter_range 包含 chapter 的 cluster
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
            if not isinstance(c, dict):
                continue
            cr = c.get("chapter_range") or []
            status = c.get("status")
            # 2026-05-29 复审修复（M16 · SC-6）：中英文 status 都认。
            if status in _EVENT_CLUSTER_ACTIVE_STATUSES:
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
                        "volume_convergence_anchor": _build_volume_convergence_anchor(scanner, c),
                        # 🆕 2026-06-03 卷=阶段触发点：透传卷级语义给 writer。
                        # is_volume_finale=True → 本 cluster 是卷末小走向 → writer 走高烈度转折(禁平稳收束)；
                        # stakes_delta 让 writer 知道相对前块的强度增量(避免同卷小走向平铺重复)。
                        "volume": c.get("volume"),
                        "is_volume_finale": bool(c.get("is_volume_finale")),
                        "stakes_delta": c.get("stakes_delta") or "",
                        "volume_finale_directive": (
                            "🔴 本 cluster = 卷末小走向(volume_finale)：收束本阶段/副本。"
                            "结尾必须高烈度转折(反派现身/真相揭露/主角力量或身份阶段跃迁)+强钩子，"
                            "禁止平稳收束(章末禁收束的卷尺度)。之后将换卷进入新阶段/新副本。"
                            if c.get("is_volume_finale") else None
                        ),
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
        # 2026-05-30 北极星③契约修复：真实项目用 stress_dimensions（纵尸司扁平 / 诡异嵌套）无标量
        # stress_level → 旧版 manifest level 恒 0 / is_high_stress 恒 false。改用 stress_evaluator.stress_view
        # 把 3 套 schema 聚合出统一标量视图（参照 fate_engine accessor 范式），引擎/manifest 共用一套读法。
        sys.path.insert(0, str(Path(__file__).parent))
        import stress_evaluator
        view = stress_evaluator.stress_view(s)
        level = view["stress_level"]
        threshold = view["stress_threshold_break"]
        max_v = view["stress_max"]
        coping = s.get("coping_mechanisms", {}).get("high_stress_behaviors", []) if isinstance(s.get("coping_mechanisms"), dict) else []
        # log 兼容 stress_log（v21/城南）/ stress_history（纵尸司/诡异）
        log_list = s.get("stress_log")
        if log_list is None:
            log_list = s.get("stress_history") or []
        recent_log = log_list[-3:]
        # 检查最近是否触发过 mental_break
        last_break = None
        for entry in reversed(log_list):
            if isinstance(entry, dict) and entry.get("trigger_type") == "mental_break_triggered":
                last_break = {"ch": entry.get("ch"), "card_label": entry.get("card_label")}
                break
        # 维度 schema：把各维度当前值也透传给 writer（哪个维度最逼近崩溃比单一标量更有指导性）
        dims_snapshot = None
        if view["mode"] == "dimensions":
            raw_dims = s.get("stress_dimensions") or {}
            dims_snapshot = {
                k: (v.get("current") if isinstance(v, dict) else v)
                for k, v in raw_dims.items() if not str(k).startswith("_")
            }
        return {
            "mode": "on",
            "schema_mode": view["mode"],
            "protagonist": s.get("protagonist") or s.get("protagonist_id"),
            "stress_level": level,
            "stress_threshold_break": threshold,
            "stress_pct": round(level / max_v, 2) if max_v > 0 else 0,
            "is_high_stress": level >= threshold * 0.75,
            "stress_dimensions": dims_snapshot,
            "coping_behaviors": coping if level >= threshold * 0.6 else [],
            "recent_log": recent_log,
            "last_mental_break": last_break,
            "persona_violations_to_avoid": [
                {"trait": t.get("trait"), "violation_kw": t.get("violation_keywords", [])[:3]}
                for t in view["traits"]
            ],
            "_note": "writer step 0t 必读（advisory·顾问非法官）：is_high_stress=true 时本章应自然带入至少 1 个 coping 行为；stress_dimensions 显示哪个维度最逼近崩溃；last_mental_break 后所有章节必受 card 永久效应约束",
        }
    except Exception as e:
        return {"mode": "error", "error": str(e)[:120]}


def _stage_for_cluster_v2(arc_data: dict, cluster_id: str | None) -> dict | None:
    """v2 schema（{"arcs": {name: {"stages": [{"id","name","description","active_cluster":[...]}]}}}）：
    优先用「本 cluster 命中哪个 stage 的 active_cluster」定位当前阶段；命中不到回退 current_stage 字段。
    返回 {stage_id, stage_name, description, _matched_by}；找不到返回 None。"""
    stages = arc_data.get("stages") or []
    norm_cur = cluster_lookup.normalize_cluster_id(cluster_id) if cluster_id else None
    # ① cluster 命中 active_cluster（最贴合「以 cluster 为单位」北极星）
    if norm_cur and isinstance(stages, list):
        for st in stages:
            if not isinstance(st, dict):
                continue
            actives = {cluster_lookup.normalize_cluster_id(a) for a in (st.get("active_cluster") or [])}
            if norm_cur in actives:
                return {"stage_id": st.get("id"), "stage_name": st.get("name"),
                        "description": st.get("description"), "_matched_by": "active_cluster"}
    # ② 回退：current_stage 字段反查 stage 详情
    cur_id = arc_data.get("current_stage")
    if cur_id and isinstance(stages, list):
        for st in stages:
            if isinstance(st, dict) and st.get("id") == cur_id:
                return {"stage_id": st.get("id"), "stage_name": st.get("name"),
                        "description": st.get("description"), "_matched_by": "current_stage_field"}
    if cur_id:
        return {"stage_id": cur_id, "stage_name": None, "description": None,
                "_matched_by": "current_stage_field_no_detail"}
    return None


def _collect_main_character_arc_stage(scanner, chapter: int) -> dict:
    """北极星①[#7]：注入「当前主角的弧线 current_stage + 简短上下文」给 writer。

    背景：character_arc_state.json 由 character_arc_update.py 每章 save-state 滚动写、
    cluster_emergence_engine 读它驱动下个 cluster 涌现——但 writer manifest 此前从不注入它，
    writer 写正文时看不到主角当前弧线阶段（贴合作者风格的角色塑造需要这个信息）。

    本字段为**内联内容**（非 must_read 指针 —— gen-model 看不到指针，参照 relevant_heuristics/
    world_state_snapshot 已内联的模式），且**只注入当前主角的当前阶段 + 简短描述**（不全量塞 stages，
    尊重 context 预算）。advisory（北极星⑤·顾问非法官）：writer 只需将其作为角色塑造软提示，不强制。

    兼容两套 schema：
      - v2 单层：{"arcs": {name: {"framework", "current_stage", "stages":[{"id","name","description","active_cluster":[...]}]}}}
      - 旧形态：{"characters": [{"id"/"name", "stages_by_chapter", "current_stage_at_ch": "ch:stage"}]}
    """
    arc_path = scanner.db / "character_arc_state.json"
    if not arc_path.exists():
        return {"mode": "off", "_note": "无 character_arc_state.json，未启用主角弧线系统"}
    arc = load_json(arc_path, None)
    if not isinstance(arc, dict):
        return {"mode": "error", "error": "character_arc_state.json 解析失败或非 dict"}

    # 主角名单（与全系统一致：人物卡.json role==主角；空则退当前场景出场角色第一名）
    cards = (scanner.load("人物卡", {}) or {}).get("characters", [])
    protagonists = [c.get("name") or c.get("id") for c in cards
                    if isinstance(c, dict) and c.get("role") == "主角"]
    protagonists = [p for p in protagonists if p]
    if not protagonists:
        protagonists = scanner.active_characters()[:1]

    current_cluster = scanner._current_cluster_id()
    out_chars: list[dict] = []

    arcs_map = arc.get("arcs")
    if isinstance(arcs_map, dict) and arcs_map:
        # v2 单层 schema。优先注入主角；主角不在 arc 表内时 fallback 注入弧线表第一个角色。
        names = [n for n in protagonists if n in arcs_map] or list(arcs_map.keys())[:1]
        for name in names:
            data = arcs_map.get(name)
            if not isinstance(data, dict):
                continue
            stage = _stage_for_cluster_v2(data, current_cluster)
            if stage is None:
                continue
            out_chars.append({
                "character": name,
                "framework": data.get("framework"),
                "current_stage_id": stage["stage_id"],
                "current_stage_name": stage["stage_name"],
                "stage_description": (stage["description"] or "")[:160] or None,
                "_resolved_by": stage["_matched_by"],
            })
    else:
        # 旧形态：characters 列表 + current_stage_at_ch="ch:stage"
        chars_list = arc.get("characters")
        if isinstance(chars_list, list):
            by_name = {(c.get("name") or c.get("id")): c for c in chars_list if isinstance(c, dict)}
            names = [n for n in protagonists if n in by_name] or list(by_name.keys())[:1]
            for name in names:
                data = by_name.get(name)
                if not isinstance(data, dict):
                    continue
                # current_stage_at_ch 格式 "ch:stage"，无则 stages_by_chapter 现算
                raw = data.get("current_stage_at_ch") or ""
                stage_id = raw.split(":", 1)[1] if ":" in raw else (data.get("current_stage") or None)
                if not stage_id:
                    sbc = data.get("stages_by_chapter") or {}
                    if sbc:
                        valid = sorted((int(k), v) for k, v in sbc.items() if str(k).isdigit() and int(k) <= chapter)
                        stage_id = valid[-1][1] if valid else "pre_start"
                if not stage_id:
                    continue
                out_chars.append({
                    "character": name,
                    "framework": data.get("framework"),
                    "current_stage_id": stage_id,
                    "current_stage_name": None,
                    "stage_description": (data.get("stage_description") or "")[:160] or None,
                    "_resolved_by": "current_stage_at_ch" if ":" in raw else "stages_by_chapter_computed",
                })

    if not out_chars:
        return {"mode": "off", "_note": "character_arc_state.json 内无可解析的主角弧线阶段"}

    return {
        "mode": "on",
        "current_cluster": current_cluster,
        "main_characters": out_chars,
        "gate_level": "advisory",
        "_note": ("writer 角色塑造软提示（advisory·北极星⑤顾问非法官·非硬约束）："
                  "本 cluster 主角正处于上列弧线阶段，台词/选择/内心活动应自然贴合该阶段的内在状态，"
                  "不要写成已跨入下一阶段或退回上一阶段；阶段推进由后续 cluster 涌现驱动。"),
    }


def _collect_storyteller_directive(scanner, chapter: int) -> dict:
    """v21 R1.2: 注入 storyteller 风格 + 近窗 adaptation 状态 + 下章建议。
    writer step 0s 据此微调本章 outcome 倾向（setback/win/neutral）。"""
    pacer_path = scanner.root / "_数据库" / "叙事节拍器.json"
    if not pacer_path.exists():
        return {"mode": "off", "_note": "无叙事节拍器.json，未启用 Storyteller 系统"}
    try:
        pacer = json.loads(pacer_path.read_text(encoding="utf-8"))
        # 2026-05-30 北极星③契约修复：真实项目用 framework/beats(纵尸司) 或 rhythm_profile/
        # beat_density_by_cluster(诡异)，旧版 manifest 只读 v21 storyteller_profile/adaptation_factor
        # → 这些字段是孤儿，writer 永远看不到 Save_the_Cat 节拍 / cluster 节奏密度。改用 narrator_view
        # 归一读法（引擎/manifest 共用），并按本 cluster 注入对应节拍 + 密度。
        sys.path.insert(0, str(Path(__file__).parent))
        import narrator_calibrate
        cluster_id = scanner._current_cluster_id()
        view = narrator_calibrate.narrator_view(pacer, cluster_id)
        af = view["adaptation_factor"]
        rec = view["narrator_recommendation"]
        return {
            "mode": "on",
            "profile": view["profile"],
            "current_phase": view["current_phase"],
            "since_phase_change_ch": view["since_phase_change_ch"],
            "framework": view["framework"],
            "current_cluster_beats": view["current_cluster_beats"],
            "rhythm_profile": view["rhythm_profile"],
            "current_cluster_density": view["current_cluster_density"],
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
            "_note": "writer step 0s 必读（advisory·顾问非法官）：current_cluster_beats 是本 cluster 该命中的 Save_the_Cat 节拍（软提示）；current_cluster_density 是节奏密度；target_outcome=setback 时本章必至少有 1 个真实挫败（资源损失/关系破裂/认知打击）；=win 时本章应有明确推进/收获；=auto 时按 cluster_blueprint 自由发挥",
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
                "since_cluster": t.get("since_cluster"),
                "expected_complete_cluster": t.get("expected_complete_cluster"),
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

        # 2026-05-29 北极星 P1：注入涟漪叙事后果（混合式·叙事 ripple 收集的因果）给 writer。
        # 这是「涟漪规则为核心·通过因果触发事件」落到写作的关键——writer 读到「已触发的因果链」
        # 后自行解读该呼应/推进什么（模型判断，非引擎硬塞）。不截断（feedback_no_token_saving）。
        narr_cons = [
            {"ch": nc.get("ch"), "text": nc.get("text", ""), "reason": nc.get("reason", "")}
            for nc in (world.get("narrative_consequences") or []) if isinstance(nc, dict) and nc.get("text")
        ]

        return {
            "mode": "fluid",
            "current_world_time": world.get("current_world_time"),
            "factions_state": factions,
            "active_npc_threads_top5": threads_compact,
            "active_threads_total": len(threads_raw),
            "emergent_opportunities_available": opps_available,
            "recent_ticks": recent_ticks,
            "recent_consequences": recent_cons,
            "ripple_narrative_consequences": narr_cons,
            "_note": "writer step 0q 必读：世界自转中，幕后角色一直在动；涟漪已触发的因果链见 "
                     "ripple_narrative_consequences——你来解读它该如何在本块剧情里发酵；呼应 ≥1 个 thread/opportunity/因果加分",
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
    """v19.6 G5: 用 cluster_blueprint.turning_point + threads_advance 作 query，
    从 .embeddings/chapter_*.json 做语义检索取 top_k 历史 chunk 给 writer。

    比固定 recent 5 章摘要更智能——本章是觉醒章，应该回忆爷爷纸条章节而不是吃饭章。
    """
    if chapter <= 1:
        return {"retrieved": [], "reason": "首章无历史"}

    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    progress = scanner.load("进度", {})
    query_parts = []
    all_scenes = []
    # 2026-05-29 复审修复（SC-1）：经 _bp_items 归一（list 项目不崩）。
    for cid, cdata in _bp_items(progress).items():
        if not isinstance(cdata, dict):
            continue
        for sb in cdata.get("scene_storyboard", []):
            all_scenes.append(sb)
    for cp in all_scenes:
        if cp.get("ch") == chapter:
            query_parts.append(cp.get("turning_point", ""))
            query_parts.append(cp.get("goal", ""))
            ta = cp.get("threads_advance", [])
            if isinstance(ta, list):
                query_parts.extend(ta)
            break
    query = " ".join(str(q) for q in query_parts if q)
    if not query:
        return {"retrieved": [], "reason": "本章 cluster_blueprint 无 query 信号"}

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

    # v2 cluster 化（2026-05-28）：纯 cluster 模式 · 只读 cluster_blueprint
    # 2026-05-29 复审修复（SC-1）：经 _bp_items 归一（list 项目不崩）。
    progress = scanner.load("进度", {})
    scene_types = set()
    for cid, cdata in _bp_items(progress).items():
        if not isinstance(cdata, dict):
            continue
        for cp in cdata.get("scene_storyboard", []):
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


def _collect_deep_writing_dims(scanner) -> dict:
    """L4 · 深层创作维度提示（D1-D3）—— 纯 prompt 注入，从源头降问题率。

    北极星⑤顾问非法官：本字段**只注入创作提示、绝不检测/判决**。没有 scanner、没有
    hard_gate、gate_level 永远 advisory。writer 看到提示可自由取舍（作者档第一权威）。

    三个深层维度（均有「作者档基线（有则优先）」+「通用提示（无基线时兜底）」两档）：
      D1 心理距离档位：Cohn 意识呈现三模式 + Gardner 四级 psychic distance 谱 +
         Deep POV 滤镜词删除提示（想 / 觉得 / 感到 / 意识到 / 看到 / 听到）。
         作者档基线：narrative_craft.narrative_distance_distribution + quantitative.inner_monologue_ratio。
      D2 visceral-first 情绪顺序：先生理本能反应 → 再认知 → 最后才命名情绪
         （呼应 CLAUDE.md「不写他感到愤怒，写他把杯子摔在地上」）。
         作者档基线：writing_techniques_b3_samples.dim25_psychology_technique。
      D3 动机可溯源 + 弧光铺垫：Ghost 过去创伤 → Lie 错误信念 → Want 剧情目标 vs
         Need 真相需求；避免动机透明化综合征（角色心理一览无余）。
         作者档基线：narrative_fingerprint.character_behavior_loops + character_depth_grade_distribution。

    仅注入提示文本与少量基线摘要（context 预算友好），不全量塞分布。
    """
    sd = {}
    style_path = scanner.root / "_数据库" / "作者风格.json"
    if style_path.exists():
        try:
            loaded = json.loads(style_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                sd = loaded
        except (json.JSONDecodeError, ValueError):
            sd = {}

    nc = sd.get("narrative_craft") if isinstance(sd.get("narrative_craft"), dict) else {}
    quant = sd.get("quantitative") if isinstance(sd.get("quantitative"), dict) else {}
    nf = sd.get("narrative_fingerprint") if isinstance(sd.get("narrative_fingerprint"), dict) else {}
    b3 = sd.get("writing_techniques_b3_samples") if isinstance(sd.get("writing_techniques_b3_samples"), dict) else {}

    # ---- D1 心理距离档位 ----
    d1 = {
        "label": "心理距离档位（psychic distance）",
        "tip": (
            "意识呈现三模式（Cohn）：① 直接引语『他想：完了』② 自由间接引语『完了，他确实完了』"
            "（叙述与人物意识融合，无引导词）③ 心理叙述『他知道大势已去』。"
            "心理距离谱（Gardner 四级）：远（全景白描）→ 中（角色视角概述）→ 近（贴着角色想）→ "
            "极近（直接进入意识流）。同一场景可滑动调焦，紧张段贴近、过渡段拉远。"
            "Deep POV 删滤镜词：贴近视角时删『想 / 觉得 / 感到 / 意识到 / 看到 / 听到』——"
            "不写『他看到门开了』，写『门开了』。"
        ),
    }
    ndd = nc.get("narrative_distance_distribution")
    imr = quant.get("inner_monologue_ratio")
    if isinstance(ndd, dict) and ndd:
        # 取占比最高的距离档位作为作者基线主调
        try:
            top = sorted(ndd.items(), key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) else 0, reverse=True)
            d1["author_baseline_distance_top"] = [k for k, _ in top[:3]]
        except Exception:
            pass
    if isinstance(imr, dict) and imr.get("mean") is not None:
        d1["author_inner_monologue_ratio_mean"] = imr.get("mean")
    d1["_baseline_source"] = (
        "作者档" if ("author_baseline_distance_top" in d1 or "author_inner_monologue_ratio_mean" in d1)
        else "通用（作者档未量化该维度）"
    )

    # ---- D2 visceral-first 情绪顺序 ----
    d2 = {
        "label": "visceral-first 情绪顺序",
        "tip": (
            "情绪三段式：先写生理本能反应（心跳 / 胃部收紧 / 手指发凉 / 呼吸不畅）→ 再写认知判断"
            "（意识到危险、想起某事）→ 最后才命名情绪（且尽量用动作替代命名）。"
            "不写『他感到愤怒』，写『他把杯子摔在地上』。情绪词是最后兜底，优先让身体和动作说话。"
        ),
    }
    psych = b3.get("dim25_psychology_technique")
    if isinstance(psych, list) and psych:
        first = psych[0]
        note = first.get("note") if isinstance(first, dict) else None
        if isinstance(note, str) and note:
            d2["author_psychology_sample"] = note[:160]
    d2["_baseline_source"] = "作者档" if "author_psychology_sample" in d2 else "通用（作者档未给心理技法样本）"

    # ---- D3 动机可溯源 + 弧光铺垫 ----
    d3 = {
        "label": "动机可溯源 + 弧光铺垫",
        "tip": (
            "角色动机分层：Ghost（过去创伤 / 旧伤痕）→ Lie（由 Ghost 长出的错误信念）→ "
            "Want（角色自以为想要的剧情目标）vs Need（角色真正需要面对的真相）。"
            "Want 推动情节、Need 推动弧光，二者常冲突。避免动机透明化综合征：不要一次把角色心理"
            "和盘托出，让动机靠行为 / 选择 / 矛盾决定逐步显形，读者自行拼图。"
        ),
    }
    loops = nf.get("character_behavior_loops")
    if isinstance(loops, dict) and loops:
        d3["author_behavior_loops"] = dict(list(loops.items())[:3])
    depth = nf.get("character_depth_grade_distribution")
    if isinstance(depth, dict) and depth:
        d3["author_depth_grade_distribution_top"] = dict(list(depth.items())[:3])
    d3["_baseline_source"] = (
        "作者档" if ("author_behavior_loops" in d3 or "author_depth_grade_distribution_top" in d3)
        else "通用（作者档未给行为环 / 深度分布）"
    )

    return {
        "_note": (
            "L4 深层创作维度提示（D1 心理距离 / D2 visceral-first 情绪 / D3 动机弧光）——"
            "纯创作提示，无检测无门禁。作者档若有对应基线则以基线为准（第一权威），否则用通用提示兜底。"
        ),
        "gate_level": "advisory",  # 北极星⑤：永远顾问，绝不 hard_gate
        "advisory_only": True,
        "D1_psychic_distance": d1,
        "D2_visceral_first_emotion": d2,
        "D3_motivation_arc": d3,
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
            # 2026-05-30 北极星复审：learn_at_cluster="cluster_005" 是 cluster ID 不是章号，
            # 必须反查当前章所属 cluster 再比对——禁止抽数字 5 当章号与 chapter 撞（北极星铁律）。
            lac = wl.get("learn_at_cluster")
            if not isinstance(lac, str) or not lac:
                continue
            cur_cid = cluster_lookup.ch_to_cluster_id(scanner.root, chapter)
            if cur_cid and cluster_lookup.normalize_cluster_id(lac) == cluster_lookup.normalize_cluster_id(cur_cid):
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
        # 2026-05-30 北极星复审：reveal_at_cluster 是 cluster ID 不是章号，反查当前章所属
        # cluster 比对——禁止抽数字当章号与 chapter 撞（北极星铁律，参照 _current_cluster_id）。
        rc = s.get("reveal_at_cluster")
        cur_cid = cluster_lookup.ch_to_cluster_id(scanner.root, chapter)
        reveal_now = (isinstance(rc, str) and rc and cur_cid
                      and cluster_lookup.normalize_cluster_id(rc) == cluster_lookup.normalize_cluster_id(cur_cid))
        if reveal_now or s.get("status") == "leaked":
            out.append({
                "id": s.get("id"),
                "secret": s.get("secret", "")[:60],
                "status": s.get("status", "hidden"),
                "reveal_at_cluster": s.get("reveal_at_cluster"),
                "known_by": s.get("known_by", []),
            })
    return out


def _ttr_fidelity_mode() -> str:
    """TTR_FIDELITY_MODE：词汇丰富度（TTR/hapax）目标注入 + SFS 打分双端开关。

    默认 active（2026-05-31 放量·治 LLM 系统性拉平词汇丰富度盲区 · 3 篇研究证 LLM imitation
    向 generic-median 回归 / GPT-4o lexical diversity 反转）。非法/空 → active；off → 关闭。
    advisory 边界（北极星⑤）：是顾问目标（writer 可校准偏离），绝不进 hard_gate。
    """
    import os as _os
    m = (_os.environ.get("TTR_FIDELITY_MODE") or "active").strip().lower()
    return m if m in ("active", "off") else "active"


def _extract_author_vocab_richness(quant: dict) -> dict | None:
    """从作者风格.json.quantitative 容错抽取作者 TTR / hapax 目标（数值剖面同款）。

    两套蒸馏 schema 容错（consumer tolerant · 北极星⑥）：
      · 惊悚乐园：quantitative.vocab_richness.{ttr_mean, ttr_std, hapax_mean, hapax_std}
      · 蛊真人  ：quantitative.vocabulary.{ttr, hapax_ratio}（可能为 null）
      · style_analyzer 原生：quantitative.vocabulary_richness.{type_token_ratio, hapax_ratio}
    返回 {"ttr": float|None, "hapax": float|None, "ttr_std": float|None, "hapax_std": float|None}
    （至少一个非 None 才返回；全空/全 null → None，不编造）。
    """
    if not isinstance(quant, dict):
        return None

    def _num(v):
        # LLM 脏数值（"约0.8"/"95%"/null）只接受真数值，否则 None（不崩不编造）
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        return None

    ttr = ttr_std = hapax = hapax_std = None
    # 候选桶按优先级：vocab_richness（惊悚乐园 mean/std）→ vocabulary（蛊真人）→ vocabulary_richness（原生）
    vr = quant.get("vocab_richness")
    if isinstance(vr, dict):
        ttr = _num(vr.get("ttr_mean")) if ttr is None else ttr
        ttr_std = _num(vr.get("ttr_std")) if ttr_std is None else ttr_std
        hapax = _num(vr.get("hapax_mean")) if hapax is None else hapax
        hapax_std = _num(vr.get("hapax_std")) if hapax_std is None else hapax_std
    voc = quant.get("vocabulary")
    if isinstance(voc, dict):
        ttr = _num(voc.get("ttr")) if ttr is None else ttr
        hapax = _num(voc.get("hapax_ratio")) if hapax is None else hapax
    vrn = quant.get("vocabulary_richness")
    if isinstance(vrn, dict):
        ttr = _num(vrn.get("type_token_ratio")) if ttr is None else ttr
        hapax = _num(vrn.get("hapax_ratio")) if hapax is None else hapax

    if ttr is None and hapax is None:
        return None
    return {"ttr": ttr, "ttr_std": ttr_std, "hapax": hapax, "hapax_std": hapax_std}


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
        # 2026-05-30 北极星复审：作者风格.json quantitative.*.mean 是 LLM 蒸馏产物，可能写成 "约20字"/
        # "40%"/"2:1" 等字符串 → 原直接算术/格式化抛 TypeError/ValueError，而 build_manifest 是
        # cluster-write step1 强制必跑，崩则中断整条写作流水线。_num 只接受真数值，非数值跳过该约束（不崩）。
        def _num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        dr_m = _num(quant.get("dialogue_ratio", {}).get("mean"))
        if dr_m is not None:
            hard_constraints.append(f"对话占比 ≥ {max(0.3, dr_m - 0.15):.0%}")
        sl = quant.get("sentence_length", {})
        sl_m = _num(sl.get("mean"))
        if sl_m is not None:
            sl_std = _num(sl.get("std"))
            sl_std = sl_std if sl_std is not None else 8
            hard_constraints.append(f"句长均值目标 {sl_m:.0f} 字（std ≥ {max(5, sl_std - 3):.0f}）")
        cw_m = _num(quant.get("chapter_words", {}).get("mean"))
        if cw_m is not None:
            low = max(2000, int(cw_m) - 500)
            high = int(cw_m) + 500
            hard_constraints.append(f"章节字数 {low}-{high}")
        punc = quant.get("punctuation_density_per_1000", {})
        cpr_raw = punc.get("comma_period_ratio")
        cpr = _num(cpr_raw.get("mean") if isinstance(cpr_raw, dict) else cpr_raw)
        if cpr is not None and cpr > 1.0:
            hard_constraints.append(f"逗句比 ≥ {max(1.0, cpr - 1.0):.1f}:1（长句用逗号连接）")

        # 词汇丰富度 TTR/hapax 目标（2026-05-31 · 治 LLM 系统性拉平词汇丰富度盲区 · advisory）。
        # LLM imitation 倾向向 generic-median 回归，用词反复趋同 → 显式下发作者 TTR/hapax 目标，
        # 让 writer 主动保持作者的用词多样度（数值剖面同款 · 顾问非硬锁 · 北极星⑤）。
        if _ttr_fidelity_mode() != "off":
            vrich = _extract_author_vocab_richness(quant)
            if vrich is not None:
                bits = []
                if vrich["ttr"] is not None:
                    # TTR std 给一档下限容差（≥ 作者均值 - 1σ，避免 writer 用词趋同拉平）
                    tol = vrich["ttr_std"] if vrich["ttr_std"] is not None else 0.03
                    lo = max(0.0, vrich["ttr"] - tol)
                    bits.append(f"词型/词次比(TTR) ≥ {lo:.2f}（作者均值 {vrich['ttr']:.2f}）")
                if vrich["hapax"] is not None:
                    tol_h = vrich["hapax_std"] if vrich["hapax_std"] is not None else 0.03
                    lo_h = max(0.0, vrich["hapax"] - tol_h)
                    bits.append(f"单现词占比(hapax) ≥ {lo_h:.2f}（作者均值 {vrich['hapax']:.2f}）")
                if bits:
                    hard_constraints.append(
                        "词汇丰富度（advisory · 别堆砌生僻词凑数）：" + "；".join(bits)
                        + " — 避免反复用同一批词，保持作者级用词多样度")

    return hard_constraints


def _collect_author_style_fingerprint(s: "DatabaseScanner") -> dict | None:
    """L1a 升格：作者量化风格指纹（显式下发 writer 的多维目标硬数字 · advisory）。

    实证（"Breaking the Imitation Game"）：把句长/段长/标点/虚词/对话密度/签名搭配等多维数值
    **显式告知** writer，比让模型自己看样本去悟更有效。本字段把 validate_style 里只用于 L1a
    评分阈值的作者句长分位数，升格成写作时下发的显式目标剖面（advisory · 非门禁非硬锁）。

    env PROFILE_INJECT_MODE（2026-05-31 放量 · 默认 active · 真生效下发 writer）：
      · active（默认 / 空 / 非法值）：算指纹并注入 manifest.author_style_fingerprint → writer
                     经 _build_style_fingerprint_section 显式消费多维量化目标。
      · shadow     ：算指纹并写到 _数据库/.style_fingerprint/ch_NNN.json + stderr 摘要，
                     但 **不注入返回值**（manifest 仍不含 → writer 看不到），供离线核对 / A-B 对照。
      · off        ：完全不算、返回 None —— manifest 不含本字段，writer 行为零回归（显式关闭做对照）。

    数据源：写作时不重扫原文（慢），走已蒸馏 作者风格.json.quantitative（复用 L1a 已算数值）。
    advisory 边界（北极星⑤）：指纹是顾问数值（writer 可校准偏离），绝不是 hard_gate。
    """
    import os as _os
    mode = (_os.environ.get("PROFILE_INJECT_MODE") or "active").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "active"  # 空/非法值 → 默认 active（2026-05-31 放量·只有显式 off 才关）
    if _style_profile_extractor is None or not s.has_style_profile():
        return None
    try:
        profile = s.load("作者风格", {})
        fp = _style_profile_extractor.build_style_fingerprint(profile=profile)
    except Exception as e:  # 顾问层失败绝不中断主流水线（build_manifest 是 cluster-write step1 必跑）
        print(f"[WARN] style_profile_extractor 失败: {e}", file=sys.stderr)
        return None
    if not fp or not fp.get("directives"):
        return None
    # shadow / active 都落盘一份供离线核对（不改判决）
    try:
        out_path = s.db / ".style_fingerprint" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(fp, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] author_style_fingerprint: {len(fp['directives'])} 条指令 "
              f"(source={fp.get('source')}, n={fp.get('n_chapters')}) — 不注入 manifest",
              file=sys.stderr)
        return None  # 影子：不注入 → 零回归
    return fp  # active：注入 writer


def _ablate_dimensions() -> set:
    """解析 env ABLATE_DIMENSIONS（R3 ABL-3 · 消融实验单维抑制 · 默认空=零回归）。

    leave-one-dimension-out 消融时显式 export 某维（如 ABLATE_DIMENSIONS=A3）→ 该维
    对应的注入 directive 被跳过（其余维不变），用于量化「抹掉该维后产出退化多少」。
    **默认空 → 完全零回归**（只有消融实验显式设才生效）。归一化：大小写/中文逗号/空白。

    维度 ↔ directive 映射（在此钉死避免编号歧义）：
      节奏组（_collect_author_rhythm_signature）：
        A1 = beat_transition_matrix（节拍转移主调）
        A2 = scene_turn_ratio（场景价值翻转率）
        A3 = tension_trajectory（张力后段保持度 + 情绪弧主形态）
        A4 = hook_type_distribution + hook_payoff_gap_median（钩子类型 + 兑现章距）
        A5 = propulsion_density（推进密度）
      决策/刻画组（_collect_author_decision_principles）：
        B1/B2/B3… = author_decision_principles 对应键（按 key 名匹配·大写归一）
        C1/C2/C3… = characterization_craft 对应键

    ⚠️ 北极星⑤：抹维只影响注入内容（全 advisory directives），**绝不**动 hard_gate。
    消融态非正式写作——设了非空值时往 stderr 打醒目告警。
    """
    import os as _os
    raw = (_os.environ.get("ABLATE_DIMENSIONS") or "").replace("，", ",")
    dims = {d.strip().upper() for d in raw.split(",") if d.strip()}
    if dims:
        print(f"[ABLATE · 消融态·非正式写作] ABLATE_DIMENSIONS={sorted(dims)} "
              f"— 对应维注入被抑制（仅消融实验用·默认应为空）", file=sys.stderr)
    return dims


def _collect_author_rhythm_signature(s: "DatabaseScanner") -> dict | None:
    """阶段1：作者叙事节奏指纹（序列级骨·directives 显式下发 writer · advisory）。

    把 consolidate 聚合的 narrative_rhythm（节拍转移矩阵/场景翻转率/张力后段保持度/
    钩子分布兑现间隔/推进密度）转成 writer 可执行的节奏指令——补段长/句长表层指纹
    抓不到的「写了这一拍之后写下一拍」的序列骨（呼应 Spoiler Alert「过早收束」诊断）。

    env RHYTHM_INJECT_MODE（2026-06-13 终验后切 active 放量·实测张力后段保持度 0.872→0.903
    朝作者 0.93 收敛、CV 0.158→0.314 翻倍·骨注入可量化生效）：
      · active（默认·已放量）：注入 manifest.author_rhythm_signature → writer 经
        _build_rhythm_signature_section 消费。
      · shadow（调试）：算+落盘 _数据库/.rhythm_signature/ch_NNN.json + stderr 摘要·不注入。
      · off：返回 None·零回归。
    advisory 边界（北极星⑤）：作者档实测节奏=第一权威·writer 可校准偏离·绝非 hard_gate。
    """
    import os as _os
    mode = (_os.environ.get("RHYTHM_INJECT_MODE") or "active").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "active"
    if not s.has_style_profile():
        return None
    try:
        profile = s.load("作者风格", {})
        nr = profile.get("narrative_rhythm") if isinstance(profile, dict) else None
    except Exception as e:
        print(f"[WARN] rhythm_signature 读取失败: {e}", file=sys.stderr)
        return None
    if not isinstance(nr, dict) or not nr:
        return None
    # R3 ABL-3：消融实验单维抑制（默认空 → 零回归·所有 if 'Ak' not in _ablate 守卫全过）。
    _ablate = _ablate_dimensions()
    directives: list[str] = []
    bt = nr.get("beat_transition_matrix") or {}
    if bt and "A1" not in _ablate:
        top = sorted(bt.items(), key=lambda kv: -kv[1].get("count", 0))[:3]
        directives.append("节拍转移主调（写了这拍接下拍的作者习惯）："
                          + "、".join(f"{k}({v.get('prob')})" for k, v in top))
    if nr.get("scene_turn_ratio") is not None and "A2" not in _ablate:
        directives.append(f"场景价值翻转率目标 {nr['scene_turn_ratio']:.0%}"
                          "（每个场景开收场极性应翻转·避免不 turn 的平铺伪事件）")
    tt = nr.get("tension_trajectory") or {}
    if tt.get("post_climax_retention") is not None and "A3" not in _ablate:
        directives.append(f"张力后段保持度目标 {tt['post_climax_retention']:.0%}"
                          "（爽点/高潮后别秒收·张力撑到收尾·防过早收束）")
    if tt.get("dominant_emotion_shape") and "A3" not in _ablate:
        directives.append(f"情绪弧主形态：{tt['dominant_emotion_shape']}（作者基线形态）")
    # D2-4：三向度张力机制配比 directive（独立 env D2_TENSION_TYPE_INJECT_MODE 默认 shadow·
    # G3-ENUMKAPPA 标注一致性 PASS 后才切 active·防注入未验证噪声·北极星⑥用数据定哪维注入）
    ttd = nr.get("tension_type_distribution") or {}
    if ttd and (_os.environ.get("D2_TENSION_TYPE_INJECT_MODE") or "shadow").strip().lower() == "active":
        parts = "、".join(f"{k}{v:.0%}" for k, v in list(ttd.items())[:3])
        directives.append(f"张力机制配比（读者信息差三向度·作者基线）：{parts}"
                          "（suspense=读者已知危险等它爆/curiosity=先抛结果勾读者想知道为什么/"
                          "surprise=withhold后反转打脸·别只会一种）")
    if nr.get("hook_type_distribution") and "A4" not in _ablate:
        hk = list(nr["hook_type_distribution"])[:3]
        directives.append("钩子类型偏好：" + "、".join(hk))
    if nr.get("hook_payoff_gap_median") is not None and "A4" not in _ablate:
        directives.append(f"悬念兑现章距中位 {nr['hook_payoff_gap_median']} 章"
                          "（埋了别立刻收也别永远不收·钩了必兑现）")
    if nr.get("propulsion_density") and "A5" not in _ablate:
        directives.append("推进密度基线：" + "、".join(list(nr["propulsion_density"])[:2]))
    # R3 ABL-3/ABL-5：死维负对照——ABLATE_RANDOM_FIELD=1 注入一条无意义随机 directive。
    # 消融自证用：抹真实有效维（如 A3）应退化、注入这条随机维应**无差异**（验统计层能
    # 分辨噪声 vs 真改进）。默认不设 → 不注入（零回归）。
    _random_field = False
    if (_os.environ.get("ABLATE_RANDOM_FIELD") or "").strip() in ("1", "true", "on"):
        import random as _rnd
        token = "".join(_rnd.Random(s.ch).choices("0123456789abcdef", k=8))
        directives.append(f"[消融负对照·无意义随机标记 {token}·writer 应忽略]")
        _random_field = True
        print(f"[ABLATE · 负对照] ABLATE_RANDOM_FIELD=1 注入随机 directive {token}"
              f"（死维对照·仅消融实验用）", file=sys.stderr)
    if not directives:
        return None
    payload = {"source": "narrative_rhythm", "directives": directives, "raw": nr,
               "_ablation_random": _random_field,
               "_doc": "作者叙事节奏指纹（序列级·advisory·作者档第一权威）"}
    try:
        out_path = s.db / ".rhythm_signature" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] author_rhythm_signature: {len(directives)} 条节奏指令 — 不注入 manifest",
              file=sys.stderr)
        return None
    return payload


def _collect_knowledge_gap_directives(s: "DatabaseScanner") -> dict | None:
    """D3：读者-角色知识差三态注入（env KNOWLEDGE_GAP_INJECT_MODE 默认 shadow·仿 rhythm·advisory）。

    读 consolidate 聚合的 knowledge_gap_profile（信息差三态占比 + 释放序列）→ writer 节奏指令。
    默认 shadow（R2:D3 切 active 前过标注一致性闸 G3 + 消融·区别于 rhythm 的 active）·off 零回归·active 注入。
    """
    import os as _os
    mode = (_os.environ.get("KNOWLEDGE_GAP_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"
    if not s.has_style_profile():
        return None
    try:
        profile = s.load("作者风格", {})
        kg = profile.get("knowledge_gap_profile") if isinstance(profile, dict) else None
    except Exception as e:
        print(f"[WARN] knowledge_gap 读取失败: {e}", file=sys.stderr)
        return None
    if not isinstance(kg, dict) or not kg:
        return None
    directives = []
    kgd = kg.get("knowledge_gap_distribution") or {}
    if kgd:
        parts = "、".join(f"{k}{v.get('pct', 0):.0%}" for k, v in list(kgd.items())[:3])
        ra = kg.get("reader_advantage_pct")
        tail = f"·读者优势型 {ra:.0%}）" if ra is not None else "）"
        directives.append(f"信息差主调（读者-角色知识差三态·作者基线）：{parts}"
                          "（reader_adv=读者优势/上帝视角虐心·reader_disadv=角色优势·double_blind=双盲悬疑"
                          + tail)
    rsd = kg.get("release_sequence_distribution") or {}
    if rsd:
        directives.append("信息释放节拍偏好：" + "、".join(list(rsd)[:3]))
    if not directives:
        return None
    payload = {"gate_level": "advisory", "advisory_only": True,
               "directives": directives, "raw": kg,
               "_doc": "作者信息差主调（读者-角色知识差·序列骨·advisory·作者档第一权威）"}
    try:
        out_path = s.db / ".knowledge_gap" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] knowledge_gap_signature: {len(directives)} 条信息差指令 — 不注入 manifest",
              file=sys.stderr)
        return None
    return payload


def _collect_author_decision_principles(s: "DatabaseScanner") -> dict | None:
    """阶段2：作者决策原则 + 人物刻画手法（纯 prompt 注入·零 scanner·永远 advisory）。

    骨③作者思维（道德滤镜/心理距离/留白）+ 骨②人物刻画手法——「作者在 X 情境倾向 Y」
    的决策原则，段长/句长表层抓不到。读 consolidate 合并的 author_decision_principles +
    characterization_craft（去重观察）。**零检测零误报**（照 _collect_deep_writing_dims
    哲学）——只给 writer 创作提示，绝不当 scanner 判决。

    env DECISION_INJECT_MODE 默认 active（2026-06-13 切 active 放量·确定性去重观察已够 writer 消费）：
      active=注入·shadow=算+落盘不注入（调试）·off=None。决策原则本质 author-specific·无作者档则 None（零回归）。
    """
    import os as _os
    mode = (_os.environ.get("DECISION_INJECT_MODE") or "active").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "active"
    if not s.has_style_profile():
        return None
    try:
        profile = s.load("作者风格", {})
        dec = profile.get("author_decision_principles") if isinstance(profile, dict) else None
        cha = profile.get("characterization_craft") if isinstance(profile, dict) else None
    except Exception as e:
        print(f"[WARN] decision_principles 读取失败: {e}", file=sys.stderr)
        return None
    if not (isinstance(dec, dict) and dec) and not (isinstance(cha, dict) and cha):
        return None
    # R3 ABL-3：消融实验剔单维（ABLATE_DIMENSIONS 含 B*/C* 时从 dec/cha payload 剔对应键·
    # 按 key 名大写归一匹配·默认空=零回归·不匹配则 no-op）。
    _ablate = _ablate_dimensions()
    if _ablate:
        if isinstance(dec, dict):
            dec = {k: v for k, v in dec.items() if str(k).strip().upper() not in _ablate}
        if isinstance(cha, dict):
            cha = {k: v for k, v in cha.items() if str(k).strip().upper() not in _ablate}
    # D7-3：cheat-sheet 独立 size 预算（按 vs_generic 信息量排序·累计字数到 cap 截断·
    # 防把生成点近邻挤爆稀释 skill）。env DECISION_TOKEN_CAP 可调（工程参数·待消融定数）。
    cheat = profile.get("author_decision_cheat_sheet") if isinstance(profile, dict) else None
    decision_token_cap = int(_os.environ.get("DECISION_TOKEN_CAP") or "600")
    budgeted_cheat: list = []
    if isinstance(cheat, list):
        ranked = sorted(cheat, key=lambda c: (bool(c.get("vs_generic")), len(c.get("vs_generic", ""))),
                        reverse=True)
        used = 0
        for c in ranked:
            s_len = (len(c.get("situation", "")) + len(c.get("author_choice", ""))
                     + len(c.get("vs_generic", "")))
            if used + s_len > decision_token_cap:
                break
            budgeted_cheat.append(c)
            used += s_len
    payload = {
        "gate_level": "advisory", "advisory_only": True,
        "author_decision_principles": dec or {},
        "characterization_craft": cha or {},
        "author_decision_cheat_sheet": budgeted_cheat,
        "_doc": "作者决策原则(道德滤镜/心理距离/留白)+人物刻画手法+紧凑决策 cheat-sheet(D7·size 预算后)·"
                "纯创作提示·零检测·作者档第一权威·北极星⑤顾问非法官",
    }
    try:
        out_path = s.db / ".decision_principles" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] author_decision_principles: {len(dec or {})} 决策 + "
              f"{len(cha or {})} 刻画维 — 不注入 manifest", file=sys.stderr)
        return None
    return payload


def _resolve_book_genre(s: "DatabaseScanner") -> str:
    """解析本书题材（题材自适应路由用）：作者档 genre_tags > 用户偏好 > 书名推断 > unknown。"""
    try:
        prof = s.load("作者风格", {})
        gt = prof.get("genre_tags") if isinstance(prof, dict) else None
        if isinstance(gt, list) and gt:
            return str(gt[0]).strip().lower()
    except Exception:
        pass
    try:
        pref = s.load("用户偏好", {})
        cp = pref.get("content_preferences") if isinstance(pref, dict) else None
        if isinstance(cp, dict) and cp.get("genre"):
            return str(cp["genre"]).strip().lower()
    except Exception:
        pass
    try:
        import cluster_segmenter as _cs
        return _cs._infer_genre_from_naming(s.root, s.root.name)
    except Exception:
        return "unknown"


def _collect_genre_pack_directives(s: "DatabaseScanner") -> dict | None:
    """阶段3：题材专属工艺提示（按 genre 路由·内容工艺层·纯 prompt 注入·全 advisory）。

    通用维度池(作者层)always-on；题材专属(甜宠糖虐/游戏向面板)按 genre 激活。
    现系统把题材层「爽点」当通用维度=写死爽文根因→本注入按 genre_dimension_packs 路由。
    unknown/无包 genre → None（退化纯通用池·零回归）。env GENRE_INJECT_MODE 默认 active（2026-06-13
    切 active 放量·unknown 题材天然 None 零回归·有专属包才注入）。
    """
    import os as _os
    mode = (_os.environ.get("GENRE_INJECT_MODE") or "active").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "active"
    genre = _resolve_book_genre(s)
    if not genre or genre == "unknown":
        return None
    try:
        import scaffold_genre_packs as _gp
        directives = _gp.get_writer_directives(genre)
    except Exception as e:
        print(f"[WARN] genre_pack 读取失败: {e}", file=sys.stderr)
        return None
    if not directives:
        return None
    payload = {"gate_level": "advisory", "advisory_only": True, "genre": genre,
               "directives": directives,
               "_doc": f"题材({genre})专属工艺提示·内容工艺层·按genre路由·advisory·hard_gate不随题材变"}
    try:
        out_path = s.db / ".genre_pack" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] genre_pack({genre}): {len(directives)} 条题材工艺 — 不注入 manifest",
              file=sys.stderr)
        return None
    return payload


def _collect_genre_baseline_diff(s: "DatabaseScanner") -> dict | None:
    """G6 P0：注入作者风格相对通用兜底基线的方向描述（更短/更留白）·advisory·三态。

    数据源=作者风格.json.quantitative.vs_generic_baseline（C4 蒸馏时已算·不重算）。
    env GENREBASE_INJECT_MODE 默认 shadow（北极星⑥：dump 不注入·经离线消融验证再切 active 放量）。
    只注入方向 label（更短/更留白）·不暴露精确 diff 值·payload 标 _authority=FALLBACK 让 writer 分清作者档第一权威。
    """
    import os as _os
    mode = (_os.environ.get("GENREBASE_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"
    style = s.load("作者风格", {})
    vg = (style.get("quantitative") or {}).get("vs_generic_baseline")
    if not isinstance(vg, dict) or not vg.get("dims"):
        return None
    lines = [d["label"] for d in vg["dims"].values() if isinstance(d, dict) and d.get("label")]
    if not lines:
        return None
    payload = {"gate_level": "advisory", "advisory_only": True,
               "_authority": vg.get("_authority"),
               "relative_style_directions": lines,
               "_doc": "相对通用网文兜底基线的风格方向（兜底非权威·作者档第一权威·advisory）"}
    try:
        out_path = s.db / ".genre_baseline" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] genre_baseline_diff: {len(lines)} 条相对方向 — 不注入 manifest", file=sys.stderr)
        return None
    return payload


def _collect_global_feedback_must_read() -> dict | None:
    """v19.3: 全局 MEMORY 跨项目 feedback 注入（高价值）→ must_read 条目 or None。

    从 C:/Users/<user>/.claude/projects/<harness_dir>/memory/ 取 feedback_*.md 摘要
    （Claude Code user data）。harness_dir = cwd 的 dirname 化形式
    （如 X:\\path\\to\\project → X--path-to-project）。

    🔴 frozen fallback（2026-06-13）：exe 用户机上开发机 memory 路径不存在 → 本注入
    此前整层静默为空。home miss/为空时改读随 exe 出货的汇编
    lessons/global_feedback_rules.md（frozen_util.resource_path 定位·dev=仓库根·
    汇编由 assemble_global_feedback_rules.py 产出）。home 路径优先（开发机行为不变）。
    """
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
            return {
                "path": "全局 MEMORY feedback (跨项目元教训)",
                "priority": "P1",
                "focus": "审查 12 条元教训摘要 + 命中本场景的展开细节",
                "reason": f"含 {len(digest)} 条跨项目元失败模式（catchphrase 单一化 / 章节衔接断层 / offscreen 消费链 / 词汇 vs 结构 anti-slop 等）",
                "digest": digest,
            }
    # frozen fallback：home memory miss/无 description 可抽 → bundle 汇编文件
    try:
        from frozen_util import resource_path as _res_path
        gfr = _res_path("core", "claude-home", "lessons", "global_feedback_rules.md")
        if not gfr.exists():
            return None
        # 汇编文件每条规则节 = "<!-- FEEDBACK_RULE: <fname> -->" 锚 + "> description: ..." 行
        digest = []
        cur_file = None
        for ln in gfr.read_text(encoding="utf-8").splitlines():
            m = re.match(r"<!--\s*FEEDBACK_RULE:\s*(\S+)\s*-->", ln)
            if m:
                cur_file = m.group(1)
                continue
            if cur_file and ln.startswith("> description:"):
                digest.append({"file": cur_file,
                               "desc": ln[len("> description:"):].strip()[:160]})
                cur_file = None
        if not digest:
            return None
        return {
            "path": "core/claude-home/lessons/global_feedback_rules.md (bundle 汇编 fallback)",
            "priority": "P1",
            "focus": f"审查 {len(digest)} 条元教训摘要 + 命中本场景的展开细节（全文见汇编文件）",
            "reason": f"开发机 memory 不可达（frozen exe 用户机）→ 读随 exe 出货的汇编·含 {len(digest)} 条跨项目元失败模式",
            "digest": digest,
            "files": [str(gfr)],
        }
    except Exception:
        return None


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
    current_scene_data = s.current_scene() or {}
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
        "focus": f"cluster_blueprint[ch={chapter}] + 本卷 volume_arc",
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
        scene_types = current_scene_data.get("scene_type", [])
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
        scene_types = current_scene_data.get("scene_type", []) or []
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

    # 故事块摘要：v17.5 P1.4 分级注入 — 最近 5 章 + 卷起始章 + 关键事件章
    # 2026-05-30 北极星复审：账本顶层无 chapters（结构为 clusters[].chapters）→ 原 .get("chapters",[])
    # 恒空 = 整段死代码、历史章摘要从不注入 writer。改从 clusters[].chapters 拍平并注入 ch。
    _summary_doc = s.load("故事块摘要", {})
    summaries = []
    for _c in _summary_doc.get("clusters", []):
        if not isinstance(_c, dict):
            continue
        for _k, _rec in (_c.get("chapters") or {}).items():
            if not isinstance(_rec, dict):
                continue
            try:
                _ch = int(_k)
            except (ValueError, TypeError):
                _ch = _rec.get("ch") or _rec.get("chapter") or 0
            summaries.append({**_rec, "ch": _ch})
    if summaries:
        # 1) 最近 5 章
        recent = [x for x in summaries if x.get("ch", x.get("chapter", 0)) < chapter][-5:]
        # 2) 卷起始章（每卷第一章）
        volumes_data = s.load("进度", {}).get("volumes", [])
        volume_starts_chs = {(v.get("chapter_range") or [0])[0] for v in volumes_data}
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
                "path": "_数据库/故事块摘要.json",
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
            "path": "_数据库/故事块摘要.json (RAG 检索)",
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
    # 🔴 frozen-aware（对抗审查 must_fix）：lessons 是只读系统资源，应从 bundle_root() 取
    # （frozen=_MEIPASS·dev=仓库根）——而非 project_root.parent.parent.parent（frozen 下项目
    # 在用户工作区不在 bundle，旧推算指错 → lessons 静默丢失 → 削弱北极星「风格一致」）。
    try:
        from frozen_util import bundle_root as _bundle_root
        _repo_root = _bundle_root()
    except Exception:
        _repo_root = project_root.parent.parent.parent
    # P1-8 缺漏修（2026-06-12）：frozen 下 MAPE-K 闭环断裂——self_heal_engine 把
    # runtime_lessons.md 写 user_data_dir()（%APPDATA%）·此处只读 bundle → exe 运行时
    # 学到的教训永远不被 writer/judge 看到。双根合并：bundle（出厂教训）+ 用户态（运行时学的）。
    lessons_roots = [_repo_root / "core" / "claude-home" / "lessons"]
    try:
        from frozen_util import user_data_dir as _udd
        _user_lessons = _udd() / "core" / "claude-home" / "lessons"
        if _user_lessons != lessons_roots[0]:
            lessons_roots.append(_user_lessons)
    except Exception:
        pass
    lessons_files = []
    _seen_lesson_names = set()
    for _lr in lessons_roots:
        if _lr.exists():
            for _lp in sorted(_lr.glob("*.md")):
                if _lp.name not in _seen_lesson_names:
                    _seen_lesson_names.add(_lp.name)
                    lessons_files.append(_lp)
    if lessons_files:
        must_read.append({
            "path": f"core/claude-home/lessons/（{len(lessons_files)} 个文件）",
            "priority": "P2",
            "focus": "writer/judge 启动前阅读：跨项目经验沉淀（含 §1-10 各类教训 + 运行时自学）",
            "reason": "避免重复历史错误",
            "files": [str(_lp) for _lp in lessons_files],
        })

    # v19.3: 全局 MEMORY 跨项目 feedback 注入（高价值）
    # 2026-06-13 提取为 _collect_global_feedback_must_read（frozen fallback：home memory
    # miss/为空 → bundle 汇编 lessons/global_feedback_rules.md·开发机行为不变）
    _fb_must_read = _collect_global_feedback_must_read()
    if _fb_must_read:
        must_read.append(_fb_must_read)

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
    # 2026-05-29 北极星修复 [F5]：DCAS 双章模式 v26 已废弃，v27 是 freestyle（writer 不知章数/字数，
    # splitter 按字数切）。dcas_enabled 恒 False —— 不再给 freestyle writer 注入「单章字数目标」类
    # DCAS 章级字段（与 gen_writer system prompt「writer 不知目标章数」铁律一致）。
    dcas_enabled = False
    _ = dcas_threshold  # 保留读取（兼容旧 prefs），但不再据此启用 DCAS

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
        # 2026-05-31 第 2 轮：rolling style anchor（动态锚 · 软牵引对抗长程文风退化 · 北极星①⑤⑥）。
        # 从本书已写片段里挑「最贴作者文风」的 1-2 段当下一块的动态锚（vs 第 1 轮静态开局 snippet）。
        "rolling_style_anchor": _collect_rolling_style_anchor(s, chapter),
        "storyteller_directive": _collect_storyteller_directive(s, chapter),
        "protagonist_stress": _collect_protagonist_stress(s, chapter),
        # [#7] 北极星①：主角弧线当前阶段（character_arc_state.json）内联注入 writer —— 此前只有
        # cluster_emergence 消费、writer 看不到；角色塑造贴合作者风格需要它。advisory（顾问非法官）。
        "main_character_arc_stage": _collect_main_character_arc_stage(s, chapter),
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
        # L4 北极星①+⑤：深层创作维度提示（D1 心理距离 / D2 visceral-first 情绪 / D3 动机弧光）——
        # 纯 prompt 注入从源头降问题率，无 scanner 无 hard_gate，全 advisory。作者档基线优先、否则通用兜底。
        "deep_writing_dims": _collect_deep_writing_dims(s),
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
        # L1a 升格：作者量化风格指纹（显式下发 writer 多维目标硬数字 · advisory）。
        # env PROFILE_INJECT_MODE 默认 active → 注入（真生效）；shadow → None（仅落盘+日志）；off → None（显式关闭）。
        "author_style_fingerprint": _collect_author_style_fingerprint(s),
        # 阶段1：作者叙事节奏指纹（序列级骨·env RHYTHM_INJECT_MODE 默认 active 放量·advisory）。
        "author_rhythm_signature": _collect_author_rhythm_signature(s),
        # D3：读者-角色知识差三态（信息差序列骨·env KNOWLEDGE_GAP_INJECT_MODE 默认 shadow·advisory）。
        "knowledge_gap_signature": _collect_knowledge_gap_directives(s),
        # 阶段2：作者决策原则+人物刻画手法（思维/刻画骨·env DECISION_INJECT_MODE 默认 active 放量·advisory）。
        "author_decision_principles": _collect_author_decision_principles(s),
        # 阶段3：题材专属工艺提示（按 genre 路由·env GENRE_INJECT_MODE 默认 active 放量·advisory·unknown→None）。
        "genre_pack_directives": _collect_genre_pack_directives(s),
        "genre_baseline_diff": _collect_genre_baseline_diff(s),
        "dcas_enabled": dcas_enabled,
        # F5：freestyle 不暴露每章字数目标（None），避免 writer 据此自切章；字数由 splitter 按范围切。
        "dcas_word_target": None,
        "has_pre_opening": has_pre_opening,
        "pre_opening_word_count": pre_opening_word_count,
        "writer_mode": "freestyle_v27",  # v27 北极星：freestyle 默认（与 gen_writer changes 的 writer_mode 一致）
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
    DYNAMIC（cacheable 30%）：每章必变的（cluster_blueprint/上章 changes/prev_judge_findings）

    agent prompt 设计：按 STATIC → SEMI_STATIC → DYNAMIC 顺序排放，Anthropic API 自动 detect prefix → 命中率最高。
    """
    return {
        "_doc": "v21 P1: Anthropic prompt caching 友好分层。agent prompt 顶部按本表顺序展示字段，可命中 prefix cache 节省 60-90% token 成本。",
        "STATIC_99_cacheable": [
            "distill_continuity_template",       # 蒸馏散文衔接模板
            "distill_voice_packs_reference",     # 原作角色风格 DNA
            "deep_writing_dims",                 # L4: D1 心理距离 / D2 visceral-first / D3 动机弧光（全书不变创作提示）
            "author_rhythm_signature",           # 阶段1: 作者叙事节奏指纹（序列级骨·全书不变）
            "author_decision_principles",        # 阶段2: 作者决策原则+人物刻画手法（思维/刻画骨·全书不变）
            "genre_pack_directives",             # 阶段3: 题材专属工艺提示（按 genre 路由·全书不变）
            "distill_golden_few_shot",           # 蒸馏 golden_passages
            "title_style",                       # v22.4dim N5: 章节标题命名指纹（全书不变）
            "naming_convention",                 # v22.4dim N5: 角色命名规范（全书不变）
            "main_character_arcs",               # v22.4dim Round 2: 原作主角 Stanford 6 维参考（全书不变）
            "position_effect_template",          # R2.3 双轴判定模板（全局常量）
            "_cache_layout",                     # 本字段自身（元数据）
        ],
        "SEMI_STATIC_90_cacheable_v22": [
            "arc_template",                      # v22.cluster: 本章所属 cluster 的 arc 模板（同 cluster 内 manifest 完全相同 → cache hit ratio ≈ cluster.chapters_count/总章数）
            "main_character_arc_stage",          # [#7]: 主角弧线当前阶段（active_cluster 命中 → 同 cluster 内不变）
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


def _infer_start_chapter(project_root: Path) -> int:
    """2026-05-29 复审修复（C1-b · SC-3）：章节号参数缺失/非数字时推断起首章。
    优先：事件簇.json 已落章 cluster 末章 + 1；兜底：章节/第NNN章 目录最大章号 + 1；都没有 → 1。
    """
    import re as _re
    db = project_root / "_数据库"
    # 1) 事件簇.json 已落章 cluster 末章 + 1
    landed_statuses = ("done", "已完成", "in_progress", "进行中",
                       "writer_done", "splitter_done", "writer_v2_rewriting", "done_writer_drafted")
    best_last = 0
    ec = load_json(db / "事件簇.json", {}) or {}
    if isinstance(ec, dict):
        for c in ec.get("clusters", []) or []:
            if not isinstance(c, dict):
                continue
            cr = c.get("chapter_range") or []
            if isinstance(cr, list) and len(cr) == 2 and isinstance(cr[1], int):
                if c.get("status") in landed_statuses or c.get("status") not in (
                        "candidate", "未涌现", "pending", "已规划", None):
                    best_last = max(best_last, cr[1])
    if best_last > 0:
        return best_last + 1
    # 2) 扫 章节/第NNN章 目录最大章号 + 1
    chap_dir = project_root / "章节"
    if chap_dir.exists():
        mx = 0
        for p in chap_dir.iterdir():
            if p.is_dir():
                m = _re.search(r"第\s*(\d+)\s*章", p.name)
                if m:
                    try:
                        mx = max(mx, int(m.group(1)))
                    except Exception:
                        continue
        if mx > 0:
            return mx + 1
    return 1


def main():
    if len(sys.argv) < 3:
        print("用法: python build_manifest.py <项目路径> <章节号>", file=sys.stderr)
        sys.exit(1)
    project_root = Path(sys.argv[1]).resolve()
    if not project_root.exists():
        print(f"项目路径不存在: {project_root}", file=sys.stderr)
        sys.exit(1)
    # 2026-05-29 复审修复（C1-b · SC-2）：start-ch 取空时上游会把空串传进来 → int("") ValueError
    # （Traceback exit>=3，被编排器判崩溃）。守卫：空/非数字 → 回退 _infer_start_chapter（扫已落
    # cluster 末章+1 / 章节目录最大章号+1，都没有 → 1），不崩。
    raw_ch = sys.argv[2].strip() if isinstance(sys.argv[2], str) else sys.argv[2]
    try:
        chapter = int(raw_ch)
    except (ValueError, TypeError):
        chapter = _infer_start_chapter(project_root)
        print(f"[WARN] 章节号参数无效（{raw_ch!r}）→ 回退推断起首章 = {chapter}", file=sys.stderr)

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
