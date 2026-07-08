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
import os  # R20 W9 Batch-CC P2 2026-06-21·ACW_MODE env 检测需 os.environ
import re  # 2026-05-29 北极星复审 R1：模块级 re（_build_volume_convergence_anchor:1046 +
            # 旧 1801/1824 裸用 re. 但无模块 import → fluid 涌现 cluster_002+ 走 vol 反查分支 NameError
            # → 被 try 吞成 event_cluster mode:error → writer 丢 cluster context。补此根治）
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写
import cluster_lookup  # noqa: E402  2026-05-29 修：obtained_cluster 是 cluster_id 不是章号
import manifest_budget  # noqa: E402  S1 分层 token 预算（记账始终开·裁剪仅触发既有硬守卫时）

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


def _has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 未设（默认 hash 袋·无真语义）→ False。只有配了真后端才返回 True。
    跟 topic_drift_scanner._has_real_embedding_backend 判断逻辑完全一致（各文件各自留一份）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


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
            # 🔴 2026-06-28 伏笔明暗线隔离：hidden_secrets 不再装秘密内容（写手见全部未揭晓秘密
            # = 提前剧透·gemini 易泄露）。保留空列表仅为向前兼容旧读取方（len()/迭代不崩），永远为空。
            "hidden_secrets": [],
            # 未到触发时机的 hidden secret 只计数·不给内容——让写手知"有未揭晓伏笔存在"防误删伏笔，
            # 但绝不把 secret/hidden_payoff 注入 manifest。
            "pending_secret_count": 0,
            "reveal_this_ch": [],
            # 🔴 2026-07-06 P1 三态生命周期：suspended（显式挂起延后）条目不催收——不进 tier due
            # 列表，只计数让写手知道存在被挂起的伏笔（防误删）。
            "promises_suspended_count": 0,
        }
        # 🔴 2026-07-06 P1 三态生命周期（status ∈ open/suspended/consumed·枚举权威
        # db_schema_validate.FORESHADOW_STATUS_ENUM）：consumed=已回收跳过；suspended=显式
        # 挂起延后·不催收只计数；open 走到期判定。resolved bool 已迁移删除（不留兼容读）。
        for p in data.get("promises", []):
            if p.get("status") == "consumed":
                continue
            if p.get("status") == "suspended":
                result["promises_suspended_count"] += 1
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

        # 🔴 2026-06-28 伏笔明暗线隔离：到揭晓时机（reveal_at_cluster==当前 cluster / pending 到期）
        # 的 secret 才放 reveal_this_ch（该揭晓·写手这一块兑现）；其余未到触发的只累加
        # pending_secret_count，内容（secret/hidden_payoff）绝不进 result（不被任何下游注入 manifest），
        # 根治写手提前见全部未揭晓秘密泄露。
        for sec in data.get("secrets", []):
            if sec.get("status") != "hidden":
                continue
            _revealing_now = False
            # v2 cluster 化修正（2026-05-28 · cluster_002 ch5 翻车 bug fix）：
            # reveal_at_cluster="cluster_005" 是 cluster ID 不是 chapter 号，
            # 必须比对当前 章 所属 cluster ID（通过 cluster_blueprint 反查），
            # 不能直接 int 数字（之前 bug：cluster_005 → 抽数字 5 → 与 ch=5 撞）
            rc = sec.get("reveal_at_cluster")
            if isinstance(rc, str) and rc:
                cur_cluster_id = self._current_cluster_id()
                if cur_cluster_id and rc == cur_cluster_id:
                    result["reveal_this_ch"].append(sec)
                    _revealing_now = True
            elif sec.get("reveal_at_pending_resolution"):
                # 2026-05-29 复审复修 [M4]：未指定 reveal cluster → 读时解析
                # established cluster 起始章 + reveal_at_ch_offset = 目标章；
                # 当前章达到即揭晓（消费 save_state 写入的 pending 标记，根治「永不揭晓」）。
                _est = sec.get("established_cluster")
                _rng = cluster_lookup.cluster_id_to_range(self.db, _est) if _est else None
                if _rng and self.ch >= int(_rng[0]) + int(sec.get("reveal_at_ch_offset", 50)):
                    result["reveal_this_ch"].append(sec)
                    _revealing_now = True
            if not _revealing_now:
                result["pending_secret_count"] += 1
        return result

    def _current_cluster_id(self) -> str | None:
        """反查当前 ch 所属 cluster_id。
        2026-05-30 北极星复审：委托 cluster_lookup.ch_to_cluster_id（唯一权威·事件簇优先 +
        _pick_unambiguous 歧义处理），删除原 blueprint 优先的第二套实现——它与
        _collect_will_learn_due/_collect_secrets_to_reveal 用的 cluster_lookup 在 range 冲突项目
        （进度.blueprint 与事件簇 chapter_range 不一致）上分歧，致同一 manifest 的 SECRET_NOT_REVEALED
        hard_gate 计数与 writer 揭秘提示自相矛盾。北极星：cluster_lookup 是章号⇄cluster 唯一权威反查。"""
        return cluster_lookup.ch_to_cluster_id(self.db, self.ch)

    def _event_cluster_by_id(self, cluster_id) -> dict | None:
        """🔴 2026-06-27 C15: 按 cluster_id 在 事件簇.json 里取该 cluster dict（归一比对·容错）。
        反查不到 / 入参非法 → None（调用方据此跳过契约校验，不误升 fatal）。
        归一经 cluster_lookup.normalize_cluster_id（唯一权威·容忍 cluster_6/cluster_006 等形态）。"""
        target = cluster_lookup.normalize_cluster_id(cluster_id)
        if target is None:
            return None
        for c in (self.load("事件簇", {}) or {}).get("clusters", []) or []:
            if isinstance(c, dict) and cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == target:
                return c
        return None

    def world_keyword_hits(self) -> list[dict]:
        """世界观按关键词匹配（本章大纲命中哪些条目）。
        🔴 2026-06-28 写手信息隔离（item 3b·堵直读绕过）：命中条目经 _resolve_world_entry 剥未到 reveal_cluster
        的 hidden_truth/hidden_rules 后内联（resolved），让写手用隔离后的 surface 设定·不必直读 raw 世界观.json。"""
        world = self.load("世界观", {})
        entries = world.get("entries", [])
        plan = self.current_scene() or {}
        haystack = " ".join(str(v) for v in plan.values() if isinstance(v, (str, list)))
        cur_cid = self._current_cluster_id()
        hits = []
        for e in entries:
            for kw in e.get("keywords", []):
                if kw in haystack:
                    resolved = _resolve_world_entry(e, cur_cid)
                    hits.append({
                        "id": e.get("id"), "keywords": e.get("keywords"),
                        "priority": e.get("priority", 0),
                        "resolved": resolved,  # 已剥未到期 hidden_truth/hidden_rules
                    })
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
        """抽取出场角色两两之间的关系条目（有数值或非零强度的才返回）。
        🔴 2026-06-28 写手信息隔离（item 2）：经 _sanitize_relationship 剥未到 reveal_cluster 的 hidden_intent
        （秘密议程）·明面 type/数值不动。must_read focus 只拼 from/to/type（均明面）→ 此处隔离后 focus 天然安全。"""
        data = self.load("关系", {"relationships": []})
        active = set(self.active_characters())
        id_map = self._char_id_map()
        # 把 active 名字映射成 id，两边都能匹配
        active_ids = {id_map.get(n, n) for n in active} | active
        cur_cid = self._current_cluster_id()
        hits = []
        for r in data.get("relationships", []):
            f, t = r.get("from"), r.get("to")
            if f in active_ids and t in active_ids:
                # 任何一个维度非零或为负，都算「值得注入」
                vals = [r.get("affinity", 0), r.get("trust", 0),
                        r.get("fear", 0), r.get("respect", 0)]
                if any(v != 0 for v in vals):
                    rs = _sanitize_relationship(r, cur_cid)
                    hit = {
                        "from": f, "to": t, "type": rs.get("type"),
                        "affinity": rs.get("affinity", 0),
                        "trust": rs.get("trust", 0),
                        "fear": rs.get("fear", 0),
                        "respect": rs.get("respect", 0),
                    }
                    if "hidden_intent" in rs:  # 仅到 reveal_cluster 时保留
                        hit["hidden_intent"] = rs["hidden_intent"]
                    if rs.get("reveal_directive"):
                        hit["reveal_directive"] = rs["reveal_directive"]
                    hits.append(hit)
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
        改为 cluster/date/day 三种正式颗粒：
          1. cluster 颗粒：event.cluster | event.cluster_revealed（诡异）
             经 cluster_lookup.normalize_cluster_id 归一（容忍 "cluster_001 (影印件…)"
             这类带后缀文本——regex 抽首个数字）比 本章所属 cluster_id
             （_current_cluster_id 唯一权威反查；反查不到时退回 current_time.cluster）
          2. date 颗粒：event.date | event.absolute_time（诡异）
             比 current_time.date | current_time.absolute_time
          3. day 颗粒：event.day（纵尸司整数日计数）比 current_time.day
        颗粒优先级 cluster > date > day（精准颗粒优先）。
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
                print("[build_manifest] WARN memory_layer 不可导入·记忆注入跳过")
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
                print("[build_manifest] WARN rag_retriever 不可导入·RAG 注入跳过")
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
            # 🔴 2026-06-26 加修复提示（cluster_001 写作翻车 sediment）：
            # outline plan step 6.5 漏跑时，cluster_blueprint 是空 dict，本错误无任何指引。
            # 现在自动检测 outline 残步，给具体修复命令。
            hint = ""
            try:
                # 🔴 2026-06-27 修（cluster_003 写作翻车 sediment）：
                # ① 找 emergence.json 用 .wal 里所有 cluster_*_emergence.json 取最新一个；
                # ② 检测 scene["ch"] 字段是否被错写成 scene index（0/1/2 而非全局章号）。
                wal = self.db / ".wal"
                emergence_files = sorted(wal.glob("cluster_*_emergence.json")) if wal.exists() else []
                latest = emergence_files[-1] if emergence_files else None
                # 检 cluster_blueprint 里 scene.ch 字段是不是 0-based scene index 命名冲突
                prog = self.load("进度", {})
                cluster_blueprint_check = ""
                for cid, cdata in _bp_items(prog).items():
                    if not isinstance(cdata, dict):
                        continue
                    sb = cdata.get("scene_storyboard", [])
                    chs = [p.get("ch") for p in sb if isinstance(p, dict) and p.get("ch") is not None]
                    if chs and max(chs) < 5:  # 0-based scene index 多半 < 5
                        cluster_blueprint_check += (
                            f"\n  → {cid} 的 scene_storyboard ch 字段是 0-based scene index"
                            f"(范围 {min(chs)}-{max(chs)})，应该是全局章号。"
                        )
                if cluster_blueprint_check:
                    hint = (
                        cluster_blueprint_check +
                        "\n     补救：重跑 py core/scripts/cluster_choice_apply.py（已修 ch 强制覆盖）"
                        " 或手改 进度.json.cluster_blueprint 里的 scene.ch 字段。"
                    )
                elif latest:
                    hint = (
                        f"\n  → 检测到 {latest.name} 已落地但 cluster_blueprint 没填。"
                        f"\n     补救：py core/scripts/cluster_choice_apply.py <项目路径> "
                        f"--next-key <对应 key> --choice {latest}"
                    )
                else:
                    hint = (
                        "\n  → outline plan 看起来没跑完。"
                        "\n     补救：py core/scripts/plan_tracker.py list | grep outline 看残步，"
                        "\n     或重跑 /outline。"
                    )
            except Exception:
                pass
            fatal.append(f"cluster_blueprint 内 ch={self.ch} 不存在，大纲未覆盖本章{hint}")
        if not (self.db / "人物卡.json").exists():
            fatal.append("人物卡.json 不存在")
        if self.ch > 1 and self.previous_chapter_file() is None:
            fatal.append(f"上一章（第{self.ch-1}章）txt 文件未找到")

        plan = self.current_scene() or {}

        # 🔴 2026-06-27 C15 BUILD-MANIFEST-INJECTION-CONTRACT：上游声明 active 却 brief 空 = 注入契约破损。
        # 取本章所属 cluster（cluster_lookup 唯一权威反查·禁机械拼接 f"cluster_{ch:03d}"·北极星①），
        # 若其 status∈active 但 scope_summary + scene_storyboard 双空 → fatal（穿帮层·writer 会拿空约束写偏）。
        # 🔴 fluid 回归锁：只 fire 在「status active yet brief 空」的契约矛盾；
        # ripple 早期空 / cluster_002+ 未涌现 / 新角色 / 源文件缺失 / 本章 characters 空 全保持 warning 不升 fatal。
        _c15_cid = cluster_lookup.ch_to_cluster_id(self.db, self.ch)
        _c15_cluster = self._event_cluster_by_id(_c15_cid) if _c15_cid else None
        if (_c15_cluster is not None
                and _c15_cluster.get("status") in _EVENT_CLUSTER_ACTIVE_STATUSES
                and _cluster_brief_empty(_c15_cluster)):
            _emit_empty_injection_signal("event_cluster_context", _c15_cid, self.ch)
            fatal.append(
                f"{_c15_cid} 声明 active（status={_c15_cluster.get('status')}）但 brief 内容为空"
                f"（scope_summary + scene_storyboard 全空·注入契约破损·writer 将拿空约束写偏）。"
                f"\n     补救：重跑 py core/scripts/cluster_choice_apply.py <项目路径> "
                f"--next-key {_c15_cid} --choice <对应 .wal/{_c15_cid}_emergence.json>，"
                f"或重跑 /outline step6.5 填该 cluster 的 scope_summary + scene_storyboard。"
            )

        if not plan.get("characters"):
            warning.append("本章 characters 字段为空，将 fallback 到全量人物卡")
            _emit_empty_injection_signal("characters", _c15_cid or "?", self.ch)  # 🔴 C15
        if not plan.get("key_events") and not plan.get("summary"):
            warning.append("本章 key_events/summary 为空，大纲过于简略")
            _emit_empty_injection_signal("key_events_summary", _c15_cid or "?", self.ch)  # 🔴 C15
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

def _load_fate_draw_overlay(scanner, chapter: int) -> dict | None:
    """Read the step1 fate-dice overlay produced before build_manifest.

    A malformed overlay is a pipeline contract error: auto_fate_draw is a formal
    producer in cluster-write step1, so build_manifest must not silently ignore a
    corrupt handoff.
    """
    overlay_path = scanner.root / "_数据库" / ".manifest" / f"ch_{chapter:03d}_fate_draw.json"
    if not overlay_path.exists():
        return None
    overlay = load_json(overlay_path, None)
    if not isinstance(overlay, dict):
        raise RuntimeError(f"{overlay_path} 顶层必须是对象")
    event = overlay.get("event")
    if not isinstance(event, dict) or not (event.get("event_id") or event.get("id")):
        raise RuntimeError(f"{overlay_path} 缺少有效 event.event_id")
    event = dict(event)
    event.setdefault("id", event.get("event_id"))
    event.setdefault("event_id", event.get("id"))
    event.setdefault("source", "fate_dice")
    return event


def _load_fate_draw_decision(scanner, chapter: int) -> dict | None:
    """Read auto_fate_draw's required decision artifact for step1 traceability."""
    path = scanner.root / "_数据库" / ".manifest" / f"ch_{chapter:03d}_fate_draw_decision.json"
    if not path.exists():
        return None
    decision = load_json(path, None)
    if not isinstance(decision, dict):
        raise RuntimeError(f"{path} 顶层必须是对象")
    if decision.get("_schema") != "fate_draw_decision_v1":
        raise RuntimeError(f"{path} _schema 必须是 fate_draw_decision_v1")
    status = decision.get("status")
    if status not in {"not_required", "drawn", "no_candidate"}:
        raise RuntimeError(f"{path} status 非法: {status!r}")
    reason = decision.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise RuntimeError(f"{path} 缺少 reason")
    return decision


def _dedupe_fate_events(events: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for event in events:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("id") or event.get("event_id") or "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(event)
    return out


def _collect_active_fate_events(scanner, chapter: int) -> dict:
    """Collect all fate events the writer must see for this chapter.

    The deterministic order is:
    1. fate_engine active major-event guidance, when 大势卡 exists.
    2. auto_fate_draw step1 overlay, when 事件池 has produced one.

    Both enter the same manifest field so writer only consumes one state path.
    """
    fate_path = scanner.root / "_数据库" / "大势卡.json"
    active = []
    overdue = []
    total_scheduled = None
    total_completed = None
    mode = "strict"
    note_parts = []
    try:
        if fate_path.exists():
            sys.path.insert(0, str(Path(__file__).parent))
            import fate_engine
            result = fate_engine.evaluate(scanner.root, chapter)
            if result.get("error"):
                raise RuntimeError(result["error"])
            drift_result = fate_engine.drift(scanner.root, chapter)
            if drift_result.get("error"):
                raise RuntimeError(drift_result["error"])
            active.extend(_strip_fate_downstream(e) for e in result.get("active_fate_events", [])[:5])
            overdue.extend(_strip_fate_downstream(e) for e in drift_result.get("overdue_events", []))
            total_scheduled = result.get("total_scheduled")
            total_completed = result.get("total_completed")
            mode = "fluid"
            note_parts.append("大势卡 active_fate_events 已注入")
        else:
            note_parts.append("无大势卡")

        overlay_event = _load_fate_draw_overlay(scanner, chapter)
        if overlay_event:
            active.append(_strip_fate_downstream(overlay_event))
            mode = "fluid"
            note_parts.append("命运抽签 overlay 已注入")

        active = _dedupe_fate_events(active)
        return {
            "mode": mode,
            "active": active,
            "overdue": overdue,
            "total_scheduled": total_scheduled,
            "total_completed": total_completed,
            "_note": "；".join(note_parts) + "；writer 应推进 active 中的 1-2 个事件" if active else "；".join(note_parts),
        }
    except Exception as e:
        raise RuntimeError(f"active_fate_events 生成失败: {e}") from e


# 🔴 2026-07-04 内容语义 embedding 路径（W6-C 迁移：风格模型→bge 内容模型·仅供
# _collect_relevant_heuristics 用——_collect_selective_history 消费磁盘预计算向量库、与生成
# 时的后端绑定，迁移属未来独立项，继续沿用上面 _has_real_embedding_backend，不在本次改动）。
def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available）。
    import 失败 → False（调用方回退关键词重叠计分）。
    """
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


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
            # 🔴 2026-06-27 C12: 带原对象引用 _orig，供检索命中后回写 usage_count（producer）。
            all_patterns.append({**p, "_category": category, "_orig": p})

    if not all_patterns:
        return {"mode": "on", "total_patterns": 0, "retrieved": [],
                # P2 typed 检索契约（2026-07-07）：零候选也保持段级 typed 字段齐全（消费方按契约读）。
                "source_type": "relevant_heuristics", "match_method": "none",
                "token_budget": {"budget_chars": 0, "actual_chars": 0, "truncated": False},
                "anti_copy": "reference-not-copy"}

    # 打分：context 关键词重叠 + confidence + usage_count log。
    # 🔴 2026-07-04 换轨内容语义嵌入 API：内容后端就绪（_content_backend_ready）时 kw_hits 换成
    # 「context 拼接文本 vs 经验条目 desc」内容语义余弦（同 _collect_selective_history 用法，
    # 但走内容而非风格后端）；不可用 → kw_hits 保持原关键词重叠计数，逐字节零回归。
    import math
    _query_emb = None
    _embed_mod = None
    if _content_backend_ready():
        _ctx_text = " ".join(sorted(ctx_kws)) or turning
        if _ctx_text.strip():
            try:
                import embedding_store as _embed_mod
                _query_emb = _embed_mod.compute_content_embedding(_ctx_text)
            except Exception:
                _query_emb = None
                _embed_mod = None

    def score(p):
        desc = (p.get("description", "") + " " + p.get("name", "") + " "
                + " ".join(p.get("keywords", []) or []))
        confidence = p.get("confidence", 0.5)
        usage = p.get("usage_count", 0)
        if _query_emb is not None and desc.strip():
            try:
                p_emb = _embed_mod.compute_content_embedding(desc)
                sim = _embed_mod.cosine_similarity(_query_emb, p_emb) if p_emb else 0.0
                # 金标准校准 2026-07-04：content_embed_separability_20260704 报告——bge 内容
                # 余弦值域远比原「风格后端 [-1,1] 假设」窄（content_relatedness 族相关≈0.52 /
                # 无关≈0.41，五类分布 p5-p95 落在约 0.30-0.68）。原 sim*5.0 直接缩放会把整个
                # 有效范围压缩到 kw_hits≈[1.5,3.4]——无论真相关还是真无关都是「中等分数」，
                # 反而丢了原关键词计数「不相关=0/强相关=高」的区分度。改用 [0.40, 0.65] 双点
                # 拉伸映射：0.40 锚 neg_cross_book p50(0.4136) 一带的「明显无关」→ 拉到 kw_hits≈0；
                # 0.65 锚 pos_adjacent p75-p95(0.5974-0.6836) 一带的「强相关」→ 拉到 kw_hits=5
                # （与原关键词命中计数上限对齐）；区间外钳位，不外推。
                kw_hits = max(0.0, min(1.0, (sim - 0.40) / (0.65 - 0.40))) * 5.0
            except Exception:
                kw_hits = sum(1 for kw in ctx_kws if kw in desc)
        else:
            kw_hits = sum(1 for kw in ctx_kws if kw in desc)
        # 综合分：context match 主导 + confidence 加成 + usage log 加成
        return kw_hits * 2 + confidence + math.log(usage + 1)

    # 🔴 2026-07-03 Wave-4：sort(key=score) 对 all_patterns 每项各调一次 score()，真后端下
    # 逐条 compute_content_embedding(desc) = N 次子进程调用。排序前一次性 prefetch 全部 desc
    # 灌缓存，其后 score() 内逐条 compute_content_embedding 全部命中缓存（query embed 已在
    # 循环外算过不重复）。
    if _query_emb is not None:
        _all_descs = [
            p.get("description", "") + " " + p.get("name", "") + " "
            + " ".join(p.get("keywords", []) or [])
            for p in all_patterns
        ]
        try:
            _embed_mod.prefetch_content_embeddings(_all_descs)
        except Exception:
            pass

    # P2 typed 检索契约（2026-07-07）：score 每条只算一次并缓存——sort 用缓存值（稳定排序 +
    # reverse=True 平手保序，与原 sort(key=score) 逐字节同序），检索分随 typed item 一起下发
    # （item.similarity = 该条检索综合分·非余弦，语义由 match_method 标注）。
    _scores = {id(p): score(p) for p in all_patterns}
    all_patterns.sort(key=lambda p: _scores[id(p)], reverse=True)
    top = all_patterns[:top_k]

    # usage_count producer：检索命中即对原对象 +1 写回 写作经验.json。
    # 这是学习闭环状态更新，不是可静默丢弃的旁路；写回失败必须暴露，避免
    # skill_evolver.promote 长期因 usage_count 无 producer 而空转。
    usage_dirty = False
    for t in top:
        t["_retrieved_at_ch"] = chapter
        orig = t.get("_orig")
        if isinstance(orig, dict):
            try:
                orig["usage_count"] = int(orig.get("usage_count", 0) or 0) + 1
            except (TypeError, ValueError):
                orig["usage_count"] = 1
            orig["last_retrieved_at_ch"] = chapter
            usage_dirty = True
    if usage_dirty:
        try:
            import atomic_json as _aj
            _aj.atomic_write_json(exp_path, exp)
        except ImportError:
            save_json(exp_path, exp)

    # P2 typed 检索契约（2026-07-07·借鉴 AI_NovelGenerator/PlotPilot 向量检索溯源，见
    # research/open_source_writing_systems.md「Vector retrieval provenance」）：
    # 每条检索结果带 source_type / source_id（category#id 确定性派生）/ similarity / match_method；
    # 段级带 token_budget（把既有 name[:60]/description[:120] 截断行为显式化——非新增截断，
    # 超上限时 truncated:true）+ anti_copy 指令（advisory·防 writer 照抄条目原句）。
    _seg_match_method = "embedding" if _query_emb is not None else "keyword"
    _name_cap, _desc_cap = 60, 120
    _actual_chars = sum(
        min(len(p.get("name", "")), _name_cap) + min(len(p.get("description", "")), _desc_cap)
        for p in top)
    _truncated = any(
        len(p.get("name", "")) > _name_cap or len(p.get("description", "")) > _desc_cap
        for p in top)
    return {
        "mode": "on",
        "source_type": "relevant_heuristics",
        "match_method": _seg_match_method,
        "context_kws": list(ctx_kws)[:10],
        "total_patterns": len(all_patterns),
        "retrieved_count": len(top),
        "retrieved": [
            {
                "source_type": "relevant_heuristics",
                "source_id": f"{p.get('_category', '?')}#{p.get('id') or p.get('name', '?')}",
                "similarity": round(float(_scores[id(p)]), 3),
                "match_method": _seg_match_method,
                "category": p.get("_category"),
                "id": p.get("id") or p.get("name", "?"),
                "name": p.get("name", "")[:_name_cap],
                "description": p.get("description", "")[:_desc_cap],
                "confidence": p.get("confidence", 0.5),
                "version": p.get("version", 1),
            }
            for p in top
        ],
        "token_budget": {
            "budget_chars": len(top) * (_name_cap + _desc_cap),
            "actual_chars": _actual_chars,
            "truncated": _truncated,
            "_note": "既有注入行为显式化：name 截 60 / description 截 120（原有截断上限·非新增截断）",
        },
        "anti_copy": "reference-not-copy",
        "_note": "ERL heuristics：按本章 context 检索的 top-N 写作经验。writer 应优先消费此清单，而非读全量 写作经验.json。"
                 "similarity=检索综合分（match_method=embedding 时由内容余弦拉伸驱动·keyword 时由关键词重叠驱动·非原始余弦）。"
                 "anti_copy=reference-not-copy：经验条目是写法参照，禁止照抄条目原句进正文（advisory）。",
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
        summary["use_direction_cards"] = im.get("use_direction_cards")
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
    """Report fate-dice step1 status for traceability.

    The draw itself is handled by auto_fate_draw before build_manifest.  This
    field is only a manifest trace, not a manual instruction to the main agent.
    """
    pool_path = scanner.root / "_数据库" / "事件池.json"
    if not pool_path.exists():
        return {"mode": "off", "_note": "无事件池.json，未启用命运抽签"}
    overlay_path = scanner.root / "_数据库" / ".manifest" / f"ch_{chapter:03d}_fate_draw.json"
    decision = _load_fate_draw_decision(scanner, chapter)
    if decision is None:
        raise RuntimeError(
            "事件池.json 存在，但缺少 auto_fate_draw decision artifact；"
            "cluster-write step1 未完成或未按正式链路执行")
    if overlay_path.exists():
        if decision.get("status") != "drawn":
            raise RuntimeError(
                f"{overlay_path} 存在，但 fate_draw_decision status={decision.get('status')!r}")
        return {
            "mode": "applied",
            "overlay": str(overlay_path),
            "decision": decision,
            "_note": "cluster-write step1 已自动抽签并通过 active_fate_events 注入 writer",
        }
    return {
        "mode": str(decision.get("status")),
        "decision": decision,
        "_note": "cluster-write step1 已执行 auto_fate_draw，当前章没有新增 active fate 事件",
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


# 🔴 2026-06-27 C15 BUILD-MANIFEST-INJECTION-CONTRACT
def _cluster_brief_empty(cluster) -> bool:
    """判定一个事件簇 cluster 的 brief 内容是否「全空」（注入契约破损判据）。

    brief 已填（返回 False · 不空）= scope_summary 非空 **或** scene_storyboard 含任一
    key_events / summary / goal / title 的场景。两者皆空才返回 True（→ fatal / contract_violation）。

    🔴 fluid 回归锁核心：scene_storyboard 已填 = 不空 = 绝不误拦（天灾继承法 cluster_006
    实战——已 in_progress 但 storyboard 非空 → 此函数返回 False → preflight passed=True）。
    只在「上游声明 active 却 scope+storyboard 双空」的契约矛盾（穿帮层）才返回 True。
    """
    if not isinstance(cluster, dict):
        return False
    if (cluster.get("scope_summary") or "").strip():
        return False
    for s in cluster.get("scene_storyboard") or []:
        if isinstance(s, dict) and (
            s.get("key_events") or s.get("summary") or s.get("goal") or s.get("title")
        ):
            return False
    return True


# 🔴 2026-06-27 C15: 空注入运行时指纹 —— 仿 incidents 指纹 script::empty_injection::<field>
def _emit_empty_injection_signal(field: str, cluster_key, chapter) -> None:
    """对「子系统注入内容为空」类事件写一行结构化 [RUNTIME] 信号到 stderr（不改 exit）。

    指纹 = build_manifest.py::empty_injection::<field>，与 self_heal_engine 的
    `signature` 计数口径对齐 → `self_heal --ingest` 复发计数（≥3 recurring）即可暴露
    「哪个 producer 系统性产空」。北极星边界：纯 advisory 信号，永不抛异常打断 manifest 生成。
    """
    try:
        sig = f"build_manifest.py::empty_injection::{field}"
        print(
            f"[RUNTIME] empty_injection signature={sig} "
            f"script=build_manifest.py field={field} "
            f"cluster={cluster_key or '?'} ch={chapter}",
            file=sys.stderr,
        )
    except Exception:
        pass


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
                # 🔴 2026-07-08 修（验证书 e2e 抓出）：v28 卷=阶段 schema 的卷层字段是
                # volume_core_conflict / volume_thread / volume_finale_signal
                # （gen_creative_volume_arc emit 实写），原键表只认旧名 → 新书（大势卡
                # 只有 v28 字段·进度.volumes 空）收敛锚静默 None（北极星③软牵引失效）。
                for k in ("ending_state", "key_milestones", "final_image",
                          "ending_image", "volume_arc", "core_conflict",
                          "volume_core_conflict", "volume_thread",
                          "volume_finale_signal"):
                    if v.get(k) and k not in anchor:
                        anchor[k] = v[k]
                break
    if anchor.get("ending_image") and "final_image" not in anchor:
        anchor["final_image"] = anchor["ending_image"]
    if anchor.get("volume_core_conflict") and "core_conflict" not in anchor:
        anchor["core_conflict"] = anchor["volume_core_conflict"]
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


# 🔴 2026-06-27 P1-08: cluster brief 用 helper · 取 motif advisory snapshot 的 dormant top-5 当回收建议。
def _collect_motif_callback_hints_for_cluster(scanner) -> list:
    # 容差读 scanner.db / scanner.root._数据库（兼容 mock scanner）
    db = getattr(scanner, "db", None)
    if db is None:
        root = getattr(scanner, "root", None)
        if root is None:
            return []
        db = Path(root) / "_数据库"
    snap_path = db / ".cross_chapter_scan" / "motif_advisory_snapshot.json"
    if not snap_path.exists():
        return []
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(snap, dict):
        return []
    dormant = snap.get("dormant_motifs") or []
    hints = []
    for it in dormant[:5]:
        if isinstance(it, dict):
            term = it.get("term")
            cat = it.get("category")
            last = it.get("last_cluster")
            if term:
                hints.append({
                    "term": term, "category": cat, "last_cluster": last,
                    "suggestion": f"上 cluster 已休眠的 motif『{term}』可在本块自然回收（advisory）",
                })
    return hints


# 🔴 2026-06-28 伏笔明暗线隔离（防 gemini 提前泄露暗线）·共享 schema：
# 伏笔条目 = {fs_id, surface_clue(明线·写手埋的细节·不解释意义),
#            hidden_payoff(暗线·真正指向的秘密·Claude 侧存·到 trigger_cluster 才注入),
#            trigger_cluster(揭晓 cluster), tier}。
def _sanitize_foreshadowing_to_plant(items):
    """埋设侧（plant）：写手埋伏笔时只见明线 surface_clue（当普通细节埋·不解释意义），
    剥离暗线 hidden_payoff（真正指向的秘密）——根治写手在埋设阶段就见全部未揭晓秘密提前剧透。

    schema 演进读容错（非降级·北极星⑥）：
      · 新格式 dict {fs_id, surface_clue, hidden_payoff, trigger_cluster, tier} → 去掉 hidden_payoff，
        其余字段保留；缺 surface_clue 时把旧字段 desc/description/clue 回填为 surface_clue。
      · 旧格式纯字符串 → 整条当 surface_clue。
    """
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"surface_clue": it})
            continue
        if isinstance(it, dict):
            entry = {k: v for k, v in it.items() if k != "hidden_payoff"}
            if "surface_clue" not in entry:
                _desc = it.get("desc") or it.get("description") or it.get("clue")
                if _desc:
                    entry["surface_clue"] = _desc
            out.append(entry)
            continue
        out.append(it)
    return out


def _resolve_foreshadowing_to_callback(items, current_cluster_id):
    """触发揭晓注入（callback）：callback = 到触发 cluster 该兑现的伏笔。

    entry.trigger_cluster == 当前 cluster（或未标 trigger_cluster·既然进了本块 callback 列表即视为到期）
    → 暴露 hidden_payoff（暗线·让写手这一块兑现）+ 注入 reveal_directive「现在揭晓/兑现 fs_id: hidden_payoff」；
    entry.trigger_cluster 明确指向别的 cluster（误列/未到期）→ 剥离 hidden_payoff 防提前泄露。
    旧格式纯字符串 / 无 hidden_payoff → 原样透传。"""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            out.append(it)
            continue
        tc = it.get("trigger_cluster")
        _due = (tc is None) or (
            cluster_lookup.normalize_cluster_id(tc)
            == cluster_lookup.normalize_cluster_id(current_cluster_id)
        )
        if _due:
            entry = dict(it)
            if entry.get("hidden_payoff"):
                _fid = entry.get("fs_id") or entry.get("id") or "本伏笔"
                entry["reveal_directive"] = (
                    f"🔴 现在揭晓/兑现 {_fid}：{entry['hidden_payoff']}"
                    "（本 cluster = trigger_cluster·写手须在本块把暗线兑现）"
                )
            out.append(entry)
        else:
            out.append({k: v for k, v in it.items() if k != "hidden_payoff"})
    return out


# ============================================================================
# 🔴 2026-06-28 写手信息隔离（防 gemini 在 build_manifest 注入层提前泄露未来/暗线秘密）
# ----------------------------------------------------------------------------
# 照抄已建伏笔范式（_sanitize_foreshadowing_to_plant / _resolve_foreshadowing_to_callback）：
# 每类子系统一个 _sanitize/_resolve/_soften 单一真理源·字段级剥离·到 reveal/trigger cluster 自动暴露。
#
# 北极星边界铁律（必守）：
#   · 延迟注入非删除（除 fate downstream 纯删）：到 trigger/reveal cluster 自动暴露 + reveal_directive。
#   · 字段级粒度：禁 blanket-strip 整字段·非秘密角色 role 不动·明面 type/surface_note 不动·纯工艺 anti_pattern 留。
#   · 默认安全闸：无 hidden_*/reveal_cluster/true_role 等显式标记的旧条目（今天几乎全部）一律原样透传零行为变化。
#   · 涟漪 surface 留（北极星②）：faction 公开动向/npc current_action/offscreen visible/数值 留·只隔离未来结果 + 幕后真实意图。
#   · 全 advisory·绝不新增 hard_gate·不进 audit_hub.HARD_GATE_CODES·不动北极星不变量回归锁。
# ============================================================================

_WRITER_HINT_SECRET_MARKERS = ("终卷揭密", "false_hero", "不可早暴露", "灰色合作")
_GHOST_REVEAL_KEYS = {"wound", "wound_reveal", "reveal", "true_wound", "hidden_payoff"}
_PROPP_SURFACE_MAP = {"false_hero": "ally"}  # false_hero → 表面盟友（authority/ally）·hero/mentor/helper 不动
_OFFSCREEN_FUTURE_KEYS = ("result_expected", "outcome_if_complete", "current_plan", "goals", "goals_short")


def _cluster_due(reveal_cluster, current_cluster_id):
    """当前 cluster 是否已到/越过 reveal_cluster（>= 语义·到了就一直暴露·秘密揭晓后不再回收）。
    无法解析（reveal 缺/畸形 或 current 缺）→ None，调用方据此走默认安全闸/保守剥离决策。"""
    rn = cluster_lookup.cluster_num(reveal_cluster)
    cn = cluster_lookup.cluster_num(current_cluster_id)
    if rn is None or cn is None:
        return None
    return cn >= rn


def _surface_propp(pf, card):
    """表面 propp_function：优先 producer 显式 surface_propp_function·否则 false_hero→ally·其余不动。"""
    sp = card.get("surface_propp_function")
    if sp is not None:
        return sp
    if isinstance(pf, str) and pf.strip().lower() in _PROPP_SURFACE_MAP:
        return _PROPP_SURFACE_MAP[pf.strip().lower()]
    return pf


def _sanitize_ghost(ghost):
    """ghost.wound reveal 部分剥离·留 surface_driver。默认安全闸：producer 未拆 surface_driver → 原样透传。"""
    if not isinstance(ghost, dict):
        return ghost
    if ghost.get("surface_driver") is None:
        return ghost  # 旧 ghost.wound 当普通背景·零行为变化
    return {k: v for k, v in ghost.items() if k not in _GHOST_REVEAL_KEYS}


def _sanitize_knowledge(know, current_cluster_id):
    """knowledge.will_learn（未来才知）隔离·doesnt_know_yet 本体保留（防 FUTURE_KNOWLEDGE_LEAK 必需）。
    will_learn 条目：无 learn_at_cluster 标记 → 透传（安全闸）；标记未到/畸形 → 剥（未来知识）；已到 → 留。"""
    if not isinstance(know, dict):
        return know
    wl = know.get("will_learn")
    if not isinstance(wl, list):
        return know
    kept = []
    for e in wl:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        lac = e.get("learn_at_cluster")
        if lac is None:
            kept.append(e)  # 无显式 reveal 标记 → 默认安全闸·透传
            continue
        if _cluster_due(lac, current_cluster_id):  # 已学/正学 → 留（已到知识合法·due-this-ch 另经 will_learn_due_this_ch 注入）
            kept.append(e)
        # due False/None（未来 或 标记畸形）→ 剥（未来才知）
    out = dict(know)
    out["will_learn"] = kept
    return out


def _sanitize_voice_pack(vp, current_cluster_id):
    """voice_pack.anti_patterns 含未来秘密的护栏文本剥·纯工艺 anti_pattern（口癖/句式禁忌）留（字段级）。
    只剥 producer 显式标记的秘密护栏（dict 带 reveals_secret/hidden_until_cluster/reveal_cluster）·纯字符串透传。"""
    if not isinstance(vp, dict):
        return vp
    aps = vp.get("anti_patterns")
    if not isinstance(aps, list):
        return vp
    kept = []
    for ap in aps:
        if isinstance(ap, dict):
            ruc = ap.get("hidden_until_cluster") or ap.get("reveal_cluster")
            if ap.get("reveals_secret") or ruc is not None:
                if ruc is not None and _cluster_due(ruc, current_cluster_id):
                    kept.append(ap)  # 已到揭晓 → 留
                continue  # 未到/无 reveal cluster → 剥（含秘密措辞的护栏）
        kept.append(ap)  # 纯字符串/纯工艺 anti_pattern → 留
    out = dict(vp)
    out["anti_patterns"] = kept
    return out


def _sanitize_character_card(card, current_cluster_id):
    """角色卡写手注入门控（item 1 + 6 + 8）·字段级剥离·默认安全闸（无 true_role/concealed/hidden 标记 → 原样透传）。
      · true_role/concealed_until_cluster 未到 → 剥 true_role·role/propp_function 用 surface 等价替换·注 surface_subtext；
        到 concealed_until_cluster → 解锁 true_role + reveal_directive。
      · ghost.wound 剥 reveal 部分留 surface_driver。
      · _writer_hint 含『终卷揭密/false_hero/不可早暴露/灰色合作』反指令整条剥（反指令进 prompt 必触发粉红大象）。
      · knowledge.will_learn 未来隔离·doesnt_know_yet 留。
      · voice_pack.anti_patterns 秘密措辞剥·纯工艺 anti_pattern 留。"""
    if not isinstance(card, dict):
        return card
    out = dict(card)
    # (1) 隐藏身份 true_role / concealed_until_cluster
    true_role = card.get("true_role")
    concealed_until = card.get("concealed_until_cluster")
    if true_role is not None or concealed_until is not None:
        due = _cluster_due(concealed_until, current_cluster_id) if concealed_until is not None else False
        if due:  # 到/越过揭密 cluster → 解锁真身份 + reveal 指令（写手该揭晓一定拿得到·不漏付）
            if true_role is not None:
                out["role"] = true_role
                nm = card.get("name") or card.get("id") or "此角色"
                out["reveal_directive"] = f"🔴 现在可揭晓 {nm} 的真实身份：{true_role}（本块起身份已揭晓）"
        else:  # 未到（含 due is None/False）→ 剥真身份·表面替换
            out.pop("true_role", None)
            sr = card.get("surface_role")
            if sr is not None:
                out["role"] = sr  # false_hero→authority/ally·hero/mentor/helper 不动（由 producer 给 surface_role 决定）
            out["propp_function"] = _surface_propp(card.get("propp_function"), card)
            out.setdefault("surface_subtext", "此角色比表面更复杂，可留白，勿过早定性")
    # (2) ghost.wound reveal 部分
    if isinstance(card.get("ghost"), dict):
        out["ghost"] = _sanitize_ghost(card["ghost"])
    # (3) _writer_hint 反指令（粉红大象）
    wh = card.get("_writer_hint")
    if isinstance(wh, str) and any(mk in wh for mk in _WRITER_HINT_SECRET_MARKERS):
        out.pop("_writer_hint", None)
    # (6) knowledge.will_learn 未来隔离·doesnt_know_yet 留
    if isinstance(card.get("knowledge"), dict):
        out["knowledge"] = _sanitize_knowledge(card["knowledge"], current_cluster_id)
    # (8) voice_pack.anti_patterns 秘密措辞剥
    if isinstance(card.get("voice_pack"), dict):
        out["voice_pack"] = _sanitize_voice_pack(card["voice_pack"], current_cluster_id)
    # (7·卡内联) offscreen 幕后未来结果/真实意图剥（active_character_cards 出口·与 _collect_offscreen_actions 同口径）
    if isinstance(card.get("offscreen"), dict):
        out["offscreen"] = _sanitize_card_offscreen(card["offscreen"])
    return out


# 🔴 2026-06-29 角色信息差(per-character belief)
def _belief_fact_content(facts_index, fact_id, fallback=None):
    """从 ledger.facts 索引解出 fact_id 的可读内容（content/summary 优先·缺则 fallback/fact_id 本身）。"""
    f = facts_index.get(fact_id) if isinstance(facts_index, dict) else None
    if isinstance(f, dict):
        return f.get("content") or f.get("summary") or fallback or fact_id
    if isinstance(f, str):
        return f
    return fallback or fact_id


def _sanitize_character_belief(ledger, scene, current_cluster_id):
    """🔴 2026-06-29 角色信息差(per-character belief)·生成层物理 masking（提案核心：重心在生成层注入非检测）。

    按 scene 的 participants 投射——为每个【在场】角色注入其认知边界：
      · knows[]：known_facts 中 learned_at_cluster <= current_cluster_id 的子集（复用 _cluster_due 门控原语·
        同 _sanitize_knowledge/_sanitize_character_card 范式·单一真理源）·携 can_speak（False=知道但本场不能说出口）。
      · must_not_reference[]：① unaware_of 的 fact（角色不知情）② known_facts 中 learned_at_cluster 未到期者
        （角色本块尚未获知·防 FUTURE_KNOWLEDGE_LEAK）——注负向指令『角色 X 不知道 fact_Y·本场 prose 中 X 不得
        提及/不得基于 Y 行动』。

    默认安全闸（不漏报·向后兼容·今天所有旧书无 ledger → 零行为变化）：ledger 空 / characters 空 /
      scene 无 participants → 返回 None（不注入任何约束）。全 advisory·绝不产 hard_gate。"""
    if not isinstance(ledger, dict) or not isinstance(scene, dict):
        return None
    chars_ledger = ledger.get("characters")
    if not isinstance(chars_ledger, dict) or not chars_ledger:
        return None  # 默认安全闸：ledger 空 → 不注入
    participants = scene.get("participants")
    if not isinstance(participants, list) or not participants:
        return None  # 默认安全闸：scene 无 participants → 不注入（向后兼容旧 storyboard）
    facts_index = ledger.get("facts") if isinstance(ledger.get("facts"), dict) else {}

    per_char = {}
    for cid in participants:
        cl = chars_ledger.get(cid)
        if not isinstance(cl, dict):
            continue
        knows, must_not = [], []
        for kf in (cl.get("known_facts") or []):
            if not isinstance(kf, dict):
                continue
            fid = kf.get("fact_id")
            content = kf.get("content") or _belief_fact_content(facts_index, fid)
            lac = kf.get("learned_at_cluster")
            if lac is None or _cluster_due(lac, current_cluster_id):
                # 已学（含无 learned_at_cluster 标记的安全闸·透传）→ knows
                knows.append({
                    "fact_id": fid,
                    "content": content,
                    "can_speak": kf.get("can_speak", True),
                })
            else:
                # 未到期（未来才学）→ 负向 masking（防 FUTURE_KNOWLEDGE_LEAK）
                must_not.append({
                    "fact_id": fid,
                    "content": content,
                    "reason": f"{cid} 本块尚未获知（learned_at_cluster={lac}）",
                })
        for fid in (cl.get("unaware_of") or []):
            must_not.append({
                "fact_id": fid,
                "content": _belief_fact_content(facts_index, fid),
                "reason": f"{cid} 不知情（unaware_of）",
            })
        if not knows and not must_not:
            continue
        per_char[cid] = {"knows": knows, "must_not_reference": must_not}

    if not per_char:
        return None

    return {
        "scene_index": scene.get("ch"),
        "focal_character": scene.get("focal_character"),
        "focalization_mode": scene.get("focalization_mode"),
        "knowledge_gap_mode": scene.get("knowledge_gap_mode"),
        "participants": list(participants),
        "characters": per_char,
        "_directive": (
            "🔴 角色信息差(per-character belief·物理 masking)：每个在场角色只能基于其 knows[] 里的事实"
            "行动/说话；can_speak=False 的事实=角色知道但本场不能说出口（只能内心/行动暗示·不得写进其台词）。"
            "must_not_reference[] 是该角色本场【不知道】的事实——prose 中该角色不得提及、不得基于其行动"
            "（扮猪吃老虎/信息差靠这个·角色 A 不该知道的事即便 B 知道≠A 知道）。"
        ),
    }


def _collect_scene_character_knowledge(scanner, current_cluster_id):
    """🔴 2026-06-29 角色信息差(per-character belief)·manifest 注入出口（单一真理源）。

    读持久化 _数据库/character_belief_ledger.json（Phase A/B 产）+ 当前 cluster 的 scene_storyboard，
    逐 scene 走 _sanitize_character_belief 投射各在场角色认知边界给 writer 做物理 masking。

    默认安全闸（向后兼容·零回归）：无 ledger / characters 空 / 反查不到 cluster / scene 无 participants
      → 返回 []（不注入·今天所有旧书无 ledger → 零行为变化）。全 advisory·绝不 hard_gate。"""
    ledger_path = scanner.root / "_数据库" / "character_belief_ledger.json"
    if not ledger_path.exists():
        return []
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(ledger, dict) or not ledger.get("characters"):
        return []
    cluster = scanner._event_cluster_by_id(current_cluster_id)
    if not isinstance(cluster, dict):
        return []
    out = []
    for idx, scene in enumerate(cluster.get("scene_storyboard") or []):
        if not isinstance(scene, dict):
            continue
        proj = _sanitize_character_belief(ledger, scene, current_cluster_id)
        if proj is not None:
            if proj.get("scene_index") is None:
                proj["scene_index"] = idx
            out.append(proj)
    return out


def _collect_active_character_cards(scanner, active_chars, current_cluster_id):
    """写手注入人物卡的出口（item 1）：注入出场角色 + 主角的【已隔离】卡（剥未到期隐藏身份/未来知识/秘密护栏）。
    防 gemini 在埋设/早期 cluster 见全部角色真身份提前定性。raw 人物卡.json 直读由 gen_writer 侧另治（北极星：本层只卡 build_manifest 出口）。"""
    cards_path = scanner.root / "_数据库" / "人物卡.json"
    if not cards_path.exists():
        return []
    try:
        data = json.loads(cards_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    active_set = set(active_chars or [])
    out = []
    for c in data.get("characters", []) or []:
        if not isinstance(c, dict):
            continue
        nm = c.get("name") or c.get("id")
        if active_set and nm not in active_set and c.get("role") != "主角":
            continue
        out.append(_sanitize_character_card(c, current_cluster_id))
    return out


# ──── 🔴 2026-06-29 actant链接通producer（Greimas 六 actant + cast 经济·manifest 注入出口·单一真理源）────
def _build_char_id_name_map(scanner):
    """人物卡 id→name 映射（actant/cast 命名空间归一·与 apply_archive.apply_actant_state 同源）。"""
    id2name = {}
    p = scanner.root / "_数据库" / "人物卡.json"
    if not p.exists():
        return id2name
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return id2name
    for c in (data.get("characters") or []) if isinstance(data, dict) else []:
        if isinstance(c, dict) and c.get("id") and c.get("name"):
            id2name[c["id"]] = c["name"]
    return id2name


def _canon_char_name(tok, id2name):
    """char_id → display name；已是 name / 尚未建卡 → 原样。命名空间统一到 name·对齐 cast_economy 的 known 集
    （人物卡 name）+ cluster_actant_ledger assignments（apply_archive 也存 name）→ 三者可比对。"""
    if not isinstance(tok, str) or not tok.strip():
        return None
    t = tok.strip()
    return id2name.get(t, t)


def _norm_actant_multi(names):
    """helper/opponent 归一：0→None · 1→str · 2+→list（去重保序）。

    1→str：让 actant_drift（_current_assignments 只认 str·list→None→误判 vacancy/不参与 drift）正常
      判位 + cast_economy role_split（`v==name`）也吃 str。
    2+→list：让 cast_economy composite（`isinstance(v,list) and len>=2`）点火。actant_drift 对 list 视
      None（多对手时 opponent vacancy 误报）——是两 scanner 既有 schema 张力·advisory shadow 低害·
      非本 producer 引入（北极星：只喂真数据·不改 scanner 逻辑）。"""
    seen, vals = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            vals.append(n)
    if not vals:
        return None
    return vals[0] if len(vals) == 1 else vals


def _collect_active_cast(scanner, active_chars, current_cluster_id):
    """🔴 2026-06-29 actant链接通producer：本 cluster 出场角色集（复用 scene participants + active_chars）。

    cast_economy_scanner 读 manifest.active_cast 算 introduce_burst（新引入 > intro_budget）+ role_split。
    命名空间 = display name（对齐 cast_economy known 集 = 人物卡 name + ledger assignments name）。
    默认安全闸：无 cluster / 无 participants 且 active_chars 空 → []（scanner 见空自跳过·向后兼容·零行为变化）。"""
    id2name = _build_char_id_name_map(scanner)
    names = set()
    cluster = scanner._event_cluster_by_id(current_cluster_id)
    if isinstance(cluster, dict):
        for scene in cluster.get("scene_storyboard") or []:
            if isinstance(scene, dict):
                for p in scene.get("participants") or []:
                    nm = _canon_char_name(p, id2name)
                    if nm:
                        names.add(nm)
    for c in active_chars or []:
        nm = _canon_char_name(c, id2name)
        if nm:
            names.add(nm)
    return sorted(names)


def _read_actant_ledger_entry(scanner, current_cluster_id):
    """读 cluster_actant_ledger.json 本 cluster 已回库 assignments（archivist post-hoc 权威·re-audit 路径）。"""
    p = scanner.root / "_数据库" / "cluster_actant_ledger.json"
    if not p.exists():
        return None
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(led, dict):
        return None
    target = cluster_lookup.normalize_cluster_id(current_cluster_id)
    for rec in led.get("clusters") or []:
        if not isinstance(rec, dict):
            continue
        if cluster_lookup.normalize_cluster_id(rec.get("cluster_id")) == target:
            a = rec.get("assignments")
            return a if isinstance(a, dict) and a else None
    return None


def _resolve_protagonist_name(scanner, id2name):
    """主角 name（subject 派生源）：角色弧线 role∈{protagonist,主角,主} → 人物卡 role∈{主角,protagonist} 兜底。"""
    arc_p = scanner.root / "_数据库" / "角色弧线.json"
    if arc_p.exists():
        try:
            arc = json.loads(arc_p.read_text(encoding="utf-8"))
            chars = arc.get("characters") if isinstance(arc, dict) else None
            if isinstance(chars, dict):
                for pid, info in chars.items():
                    if isinstance(info, dict) and info.get("role") in ("protagonist", "主角", "主"):
                        nm = _canon_char_name(pid, id2name)
                        if nm:
                            return nm
        except (OSError, json.JSONDecodeError):
            pass
    pc_p = scanner.root / "_数据库" / "人物卡.json"
    if pc_p.exists():
        try:
            pc = json.loads(pc_p.read_text(encoding="utf-8"))
            for c in (pc.get("characters") or []) if isinstance(pc, dict) else []:
                if isinstance(c, dict) and c.get("role") in ("主角", "protagonist") and c.get("name"):
                    return c["name"]
        except (OSError, json.JSONDecodeError):
            pass
    return None


def _cluster_seq_num(cid):
    """cluster id → 序号 int（定序用·无法解析 → None）。"""
    if cid is None:
        return None
    try:
        n = cluster_lookup.normalize_cluster_id(cid)
    except Exception:  # noqa: BLE001
        n = None
    m = re.search(r"(\d+)", n or str(cid))
    return int(m.group(1)) if m else None


def _resolve_standing_antagonists(scanner, current_cluster_id, id2name):
    """当前 standing 反派 names（opponent 派生源·减 vacancy 噪声）：反派轮替.json 中
    引入 ≤ 当前块 且 未在当前块之前被击败者。无 ledger / 定不到序 → 保守纳入未击败项。"""
    p = scanner.root / "_数据库" / "反派轮替.json"
    if not p.exists():
        return []
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cur = _cluster_seq_num(current_cluster_id)
    out, seen = [], set()
    for e in (led.get("entries") or []) if isinstance(led, dict) else []:
        if not isinstance(e, dict) or not e.get("antagonist_id"):
            continue
        intro = _cluster_seq_num(e.get("cluster_id"))
        defeat = _cluster_seq_num(e.get("defeat_cluster"))
        if cur is not None and intro is not None and intro > cur:
            continue  # 尚未引入
        if cur is not None and defeat is not None and defeat < cur:
            continue  # 当前块之前已被击败
        nm = _canon_char_name(e.get("antagonist_id"), id2name)
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def _collect_cluster_actant_state(scanner, current_cluster_id):
    """🔴 2026-06-29 actant链接通producer：本 cluster 六 actant 派分（actant_drift + cast_economy 读 manifest）。

    源优先：① cluster_actant_ledger 本 cluster 已回库条目（archivist 读正文 post-hoc·权威·re-audit 路径）；
    ② 派生（forward 流·本块尚未 archive 时）：subject=主角（角色弧线/人物卡）· opponent=当前 standing
    反派（反派轮替）· 其余位留空（不脑补·北极星②宁缺毋滥）。
    命名空间 = display name；helper/opponent 经 _norm_actant_multi（1→str 适配 actant_drift / 2+→list
    适配 cast_economy composite）。
    默认安全闸：无 ledger 条目且无主角无反派 → None（actant_drift/cast_economy 见空自跳过·无 actant 旧书零行为变化）。"""
    id2name = _build_char_id_name_map(scanner)
    entry = _read_actant_ledger_entry(scanner, current_cluster_id)
    if entry:  # ① 已回库（ledger 存单值·归一回 1→str/2+→list 适配双 scanner）
        state = {}
        for pos in ("subject", "object", "sender", "receiver"):
            nm = _canon_char_name(entry.get(pos), id2name)
            if nm:
                state[pos] = nm
        for pos in ("helper", "opponent"):
            v = entry.get(pos)
            lst = v if isinstance(v, list) else ([v] if v else [])
            norm = _norm_actant_multi([_canon_char_name(x, id2name) for x in lst])
            if norm is not None:
                state[pos] = norm
        return state or None
    # ② 派生
    subject = _resolve_protagonist_name(scanner, id2name)
    opp = _norm_actant_multi(_resolve_standing_antagonists(scanner, current_cluster_id, id2name))
    if not subject and opp is None:
        return None  # 默认安全：无可派生 → 不注入
    state = {}
    if subject:
        state["subject"] = subject
    if opp is not None:
        state["opponent"] = opp
    return state or None


def _sanitize_relationship(rel, current_cluster_id):
    """关系写手注入门控（item 2）：拆 type(明面留) + hidden_intent(秘密议程·reveal_cluster 未到→剥)；
    note 拆 surface_note(留) + hidden_note(到 hidden_note_reveal_cluster 才暴露)。默认安全闸：无 hidden_* → 原样透传。"""
    if not isinstance(rel, dict):
        return rel
    out = dict(rel)
    # hidden_intent 秘密议程（明面 type / 数值 不动）
    if "hidden_intent" in rel:
        if _cluster_due(rel.get("reveal_cluster"), current_cluster_id):
            f, t = rel.get("from"), rel.get("to")
            out["reveal_directive"] = f"🔴 现在可揭晓 {f}→{t} 的关系暗议程：{rel['hidden_intent']}"
        else:
            out.pop("hidden_intent", None)
    # hidden_note（到 hidden_note_reveal_cluster 才暴露）
    if "hidden_note" in rel:
        if not _cluster_due(rel.get("hidden_note_reveal_cluster"), current_cluster_id):
            out.pop("hidden_note", None)
    return out


def _sanitize_faction_focus(faction):
    """世界真相·阵营写手注入门控（item 3a）：hidden faction 剥真 current_focus 留 surface_focus·数值（涟漪 surface）always 留。
    默认安全闸：producer 未标 hidden 且未给 surface_focus/hidden_focus → 原样透传（current_focus 是公开动向）。"""
    if not isinstance(faction, dict):
        return faction
    if not faction.get("hidden") and faction.get("surface_focus") is None and faction.get("hidden_focus") is None:
        return faction
    out = dict(faction)
    sf = faction.get("surface_focus")
    out["current_focus"] = sf if sf is not None else ""
    out.pop("hidden_focus", None)
    return out


def _resolve_world_entry(entry, current_cluster_id):
    """世界真相·世界观条目写手注入门控（item 3b）：entry.hidden_truth/hidden_rules 到 reveal_cluster 才 merge（堵直读绕过）。
    默认安全闸：无 hidden_truth/hidden_rules → 原样透传。涟漪 surface（公开设定/数值）always 留。"""
    if not isinstance(entry, dict):
        return entry
    if entry.get("hidden_truth") is None and entry.get("hidden_rules") is None:
        return entry
    if _cluster_due(entry.get("reveal_cluster"), current_cluster_id):  # 到揭晓 → 保留 hidden_truth/hidden_rules + reveal 指令
        out = dict(entry)
        nm = entry.get("id") or entry.get("name") or "此设定"
        out["reveal_directive"] = f"🔴 现在可揭晓世界真相 {nm}：hidden_truth/hidden_rules 本块解锁"
        return out
    return {k: v for k, v in entry.items() if k not in ("hidden_truth", "hidden_rules")}


def _sanitize_clock_to_writer(clock):
    """时钟写手注入门控（item 4）：visible_to_writer==True 或 ticks>=max → 含 trigger_on_max·否则剥 trigger_on_max
    留 label/remaining/max/urgency。visible_to_writer 缺字段默认 True 向后兼容（clock_engine.list_active 将补此字段）。"""
    if not isinstance(clock, dict):
        return clock
    visible = clock.get("visible_to_writer")
    ticks, mx = clock.get("ticks"), clock.get("max")
    reached = isinstance(ticks, (int, float)) and isinstance(mx, (int, float)) and ticks >= mx
    show = (visible is None) or (visible is True) or reached
    if show:
        return clock
    return {k: v for k, v in clock.items() if k != "trigger_on_max"}


def _sanitize_offscreen(action):
    """幕后写手注入门控（item 7）：剥 result_expected/outcome_if_complete（未来结果）+ current_plan/goals（幕后真实意图）·
    留 visible action/current_action（涟漪 surface·北极星②）。"""
    if not isinstance(action, dict):
        return action
    return {k: v for k, v in action.items() if k not in _OFFSCREEN_FUTURE_KEYS}


def _strip_fate_downstream(event):
    """fate event 写手注入门控（item 5·纯删非延迟）：剥 downstream_unlocks（前向 ME 链·写手永不需要·
    下游 ME 进 active 时自然注入）。default-safe：无此字段 → 原样透传。"""
    if not isinstance(event, dict):
        return event
    return {k: v for k, v in event.items() if k != "downstream_unlocks"}


def _sanitize_card_offscreen(offscreen):
    """角色卡内联 offscreen 写手注入门控（item 7·active_character_cards 出口）：剥幕后未来结果 +
    真实意图（current_plan/goals + 每个 action 的 result/result_expected/outcome_if_complete）·留 visible action。"""
    if not isinstance(offscreen, dict):
        return offscreen
    out = {k: v for k, v in offscreen.items()
           if k not in ("current_plan", "goals", "result_expected", "outcome_if_complete")}
    acts = offscreen.get("actions")
    if isinstance(acts, list):
        out["actions"] = [
            ({k: v for k, v in a.items()
              if k not in ("result", "result_expected", "outcome_if_complete")}
             if isinstance(a, dict) else a)
            for a in acts
        ]
    return out


def _soften_convergence_anchor(anchor, current_cluster_id, total):
    """卷收敛锚降精（item 9·北极星③软牵引载体）：早期 cluster 把 final_image/ending_image 降精到软方向（不整删不 None）·
    volume_arc/core_conflict/key_milestones/泛化 gloss 始终给所有 cluster。无法判定位置 → 原样透传（默认安全闸）。"""
    if not isinstance(anchor, dict):
        return anchor
    idx = cluster_lookup.cluster_num(current_cluster_id)
    if idx is None or not isinstance(total, int) or total <= 1:
        return anchor
    if idx / total >= 0.5:  # 后半程 → 给精确终局画面
        return anchor
    out = dict(anchor)
    softened = False
    for k in ("final_image", "ending_image"):
        if out.get(k):
            out.pop(k, None)
            out[k + "_softened"] = (
                "（早期 cluster 降精·北极星③软牵引：本卷终局方向见 volume_arc/core_conflict/key_milestones，"
                "具体终局画面随涟漪涌现，暂不锁定细节）")
            softened = True
    if softened:
        out["_softened_for_early_cluster"] = True
    return out


# 🔴 2026-06-29 controlling_idea主控思想软注入（主题脊柱 P0·北极星③软牵引·骑 _soften_convergence_anchor 同轨降精）
def _build_controlling_idea_anchor(scanner, current_cluster_id, total):
    """从 大势卡.story_destiny.controlling_idea 读全书主控思想（主题价值命题）软注入 writer brief。

    schema（A agent 产·照此读·设计见 角色建模升级/主题_提案.json designs[0]）：
      大势卡.story_destiny.controlling_idea = {
        premise             一句价值论断（Egri premise·价值→冲突→结局因果）,
        controlling_idea    if/when/because 条件化版（McKee·把结局绑到角色行动）,
        moral_argument      对立价值轴（Truby·复用 thematic_argument_pairs）,
        designing_principle  主母题（绑 motif_recurrence_ledger seed）}

    降精逻辑（骑 _soften_convergence_anchor L1875 同轨·同款 idx/total>=0.5 阈值判定）：
      · 早期 cluster（idx/total < 0.5）：只给软主题方向（premise + 主母题）·
        留条件化论断/对立价值轴待近卷末显化（北极星③·绝不把大势收敛从软变硬）。
      · 后半程（idx/total >= 0.5）：显化全字段（补 controlling_idea + moral_argument·越近卷末越显化）。
      · 无法判定位置（cluster_num None / total<=1）：默认给软方向（安全闸·不超额泄露终局价值落点）。

    北极星⑤全 advisory：绝不向 writer 下『本章必须论证主题 X』硬指令·gemini 仍 freestyle·
      主题是软牵引卡·有具体到本块的理由可偏离（<300 字）。
    默认安全闸：无 story_destiny.controlling_idea（旧大势卡/旧书）→ 返回 None（不注入·零行为变化）。
    """
    ds = scanner.load("大势卡", {}) or {}
    sd = ds.get("story_destiny")
    if not isinstance(sd, dict):
        return None
    ci = sd.get("controlling_idea")
    if not isinstance(ci, dict):
        return None

    def _s(key):
        v = ci.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    premise = _s("premise")
    controlling = _s("controlling_idea")
    principle = _s("designing_principle")
    moral = ci.get("moral_argument") or None  # 对立价值轴·可为 list/dict/str·原样透传
    if not (premise or controlling or principle or moral):
        return None  # 四层全空 → 不注入（默认安全·爽文可极简但全空视为未填）

    # 骑 _soften_convergence_anchor 同轨判定位置（同款 idx/total>=0.5 阈值·北极星③软牵引降精）
    idx = cluster_lookup.cluster_num(current_cluster_id)
    late = (idx is not None and isinstance(total, int) and total > 1 and (idx / total) >= 0.5)

    card = {"gate_level": "advisory"}
    if premise:
        card["premise"] = premise
    if principle:
        card["designing_principle"] = principle  # 主母题·全程给（软方向锚）
    if late:
        # 后半程显化：补条件化论断 + 对立价值轴（越近卷末越显化）
        card["_phase"] = "explicit_late"
        if controlling:
            card["controlling_idea"] = controlling
        if moral:
            card["moral_argument"] = moral
        card["directive"] = (
            "🟢 本书主控思想(主题脊柱·advisory·北极星③软牵引·后半程显化)：\n"
            f"  · 价值命题：{premise or controlling or '（见 designing_principle）'}\n"
            "  · 已近卷末/全书后半程 → 本块角色关键选择可正面呼应或反讽性复杂化此命题\n"
            "    (McKee idea vs counter-idea 摆荡皆健康)·用行动论证非台词说教(Truby)。\n"
            "  · 软牵引主题卡非硬指令·绝不为论主题生硬插入·有具体到本块的理由可偏离(<300 字·北极星⑤)。"
        )
    else:
        # 早期降精：只给软方向（premise + 主母题）·条件化论断/对立价值轴待近卷末显化
        card["_phase"] = "soft_early"
        card["_softened_for_early_cluster"] = (
            "（早期 cluster 降精·北极星③软牵引：全书主题落点随涟漪涌现·暂只给软方向 premise + 主母题·"
            "条件化论断/对立价值轴近卷末再显化·绝不把大势收敛从软变硬）")
        card["directive"] = (
            "🟢 本书主控思想(主题脊柱·advisory·北极星③软牵引·早期软方向)：\n"
            f"  · 软主题方向：{premise or principle or '（按 designing_principle 自由发挥）'}\n"
            "  · 本块角色选择可自然呼应/复杂化此方向(早期埋张力即可·不必正面论证主题)。\n"
            "  · 软牵引提示非硬指令·gemini 自由发挥·有理由可偏离(<300 字·北极星⑤)。"
        )
    card["_doc"] = ("🔴 2026-06-29 controlling_idea主控思想软注入·全 advisory·绝不 hard_gate·"
                    "骑 _soften_convergence_anchor 同轨降精(早期软方向→卷末显化)")
    return card


# 🔴 2026-06-29 But-Therefore因果连接器+Swain场景骨架
_SWAIN_PROACTIVE_KEYS = ("goal", "conflict", "disaster")
_SWAIN_REACTIVE_KEYS = ("reaction", "dilemma", "decision")
_VALID_SCENE_TYPES = {"proactive_scene", "reactive_sequel"}
_VALID_LINK_TYPES = {"but", "therefore", "and_then"}
_VALID_RESULT_TYPES = {"yes_but", "no_and", "yes_and"}


def _collect_scene_causal_skeleton(cluster: dict) -> dict | None:
    """🔴 2026-06-29 But-Therefore因果连接器+Swain场景骨架（事件 P0·治流水账·涟漪微观可执行化）。

    透传 outline-planner(A agent) 在 scene_storyboard 每个 scene 标注的 Swain 场景骨架 + But-Therefore 衔接：
      · scene_type ∈ {proactive_scene|reactive_sequel}
      · proactive_scene → {goal, conflict, disaster}（Swain Scene = Goal-Conflict-Disaster）
      · reactive_sequel → {reaction, dilemma, decision}（Swain Sequel = Reaction-Dilemma-Decision）
      · link_to_prev ∈ {but|therefore|and_then}（相邻 beat 衔接类型·South Park But/Therefore 法则）
      · result_type ∈ {yes_but|no_and|yes_and}（Butcher Try-Fail·禁纯 yes 顺风局）
    proactive/reactive 子 beat 既认顶层字段，也认嵌套 proactive{}/reactive{} 子 dict。

    🔴 2026-06-29 scene_goal动机（心理 P0·design2 非冗余部分·补 arc-want 到 scene 的桥）：
    同步透传每个 scene 的 `scene_goal`（本场 POV 角色动作化临场目标·Stanislavski scene-objective·
    『此刻我想要什么』·治场景漂移·每场有目标驱动）。与 Swain scene_type/disaster/dilemma 正交并存
    （Swain 主体已由事件 P0 建·本字段不重复·只补「逐场景动机」这一非冗余维）。

    默认安全闸：所有 scene 都无任何这些字段（含 scene_goal）→ 返回 None（不注入·向后兼容旧 storyboard / 旧书·零行为变化）。
    advisory：场景骨架是参考模板非硬模具·writer 有具体理由可豁免（北极星⑤·绝不 hard_gate）。
    """
    if not isinstance(cluster, dict):
        return None
    storyboard = cluster.get("scene_storyboard")
    if not isinstance(storyboard, list) or not storyboard:
        return None
    skeleton: list[dict] = []
    has_any = False
    for idx, sc in enumerate(storyboard):
        if not isinstance(sc, dict):
            continue
        entry: dict = {"scene_index": idx}
        st = sc.get("scene_type")
        if isinstance(st, str) and st.strip().lower() in _VALID_SCENE_TYPES:
            entry["scene_type"] = st.strip().lower()
            has_any = True
        ltp = sc.get("link_to_prev")
        if isinstance(ltp, str) and ltp.strip().lower() in _VALID_LINK_TYPES:
            entry["link_to_prev"] = ltp.strip().lower()
            has_any = True
        rt = sc.get("result_type")
        if isinstance(rt, str) and rt.strip().lower() in _VALID_RESULT_TYPES:
            entry["result_type"] = rt.strip().lower()
            has_any = True
        # 🔴 2026-06-29 scene_goal动机（Stanislavski scene-objective·逐场景动机·治场景漂移）
        sg = sc.get("scene_goal")
        if isinstance(sg, str) and sg.strip():
            entry["scene_goal"] = sg.strip()
            has_any = True
        # Swain proactive / reactive 骨架字段（顶层或嵌 proactive/reactive 子 dict 都认）
        prox = sc.get("proactive") if isinstance(sc.get("proactive"), dict) else sc
        reac = sc.get("reactive") if isinstance(sc.get("reactive"), dict) else sc
        beats: dict = {}
        for k in _SWAIN_PROACTIVE_KEYS:
            v = prox.get(k)
            if isinstance(v, str) and v.strip():
                beats[k] = v.strip()
                has_any = True
        for k in _SWAIN_REACTIVE_KEYS:
            v = reac.get(k)
            if isinstance(v, str) and v.strip():
                beats[k] = v.strip()
                has_any = True
        if beats:
            entry["beats"] = beats
        skeleton.append(entry)
    if not has_any:
        return None  # 默认安全闸：无任何 Swain/But-Therefore 字段 → 不注入
    return {
        "scenes": skeleton,
        "directive": (
            "🟢 But-Therefore 因果连接器 + Swain 场景骨架(事件 P0·治流水账·涟漪微观)：\n"
            "  · 相邻 scene 必须用 but(冲突转折)/therefore(因果后果)衔接·\n"
            "    避免 and_then 平铺直叙(『然后…然后…』= 流水账无因果)。\n"
            "  · proactive_scene 走 Goal→Conflict→Disaster(目标→受阻→更糟)；\n"
            "    reactive_sequel 走 Reaction→Dilemma→Decision(情绪反应→两难→抉择)。\n"
            "  · result_type 禁纯 yes(顺风局)：用 yes_but(赢了但有代价)/no_and(输了且更糟)·\n"
            "    try-fail stakes 逐步递增。\n"
            "  · scene_goal(逐场景动机·Stanislavski scene-objective·『此刻 POV 角色想要什么』·动作化)：\n"
            "    每场让 POV 角色带明确临场目标驱动行动(治场景漂移·每场有目标)·目标受阻即冲突。\n"
            "  · 场景骨架是参考模板非硬模具·有具体到本场景的理由可偏离(<300 字·北极星⑤)。"
        ),
        "_doc": "🔴 2026-06-29 But-Therefore因果连接器+Swain场景骨架+scene_goal动机·advisory·绝不 hard_gate",
    }


# 🔴 2026-06-29 对白即行动dialogue_objectives注入（对话表达层 P1·McKee verbal action·治 on-the-nose）
# dialogue_act 意图骨架枚举（advisory 软提示·不强校验·outline-planner/A agent 自由标）
_VALID_DIALOGUE_ACTS = {"试探", "回避", "威胁", "让步", "反讽", "求证", "施压", "示弱"}


def _sanitize_dialogue_objectives(objectives, current_cluster_id):
    """🔴 2026-06-29 对白即行动 dialogue_objectives 写手注入门控（what_unsaid 防剧透）。

    隔离规则（对齐 _sanitize_knowledge / _sanitize_character_belief 未来知识隔离·单一真理源）：
      · objective 标了 reveal_cluster（producer 显式标 what_unsaid 涉未到期 hidden 伏笔）：
        - _cluster_due 为 True（到/越过揭晓点）→ 保留 what_unsaid；
        - 未到期 / 畸形 reveal_cluster（_cluster_due 非 True）→ 剥 what_unsaid（防 writer 提前把暗线说漏）。
      · 无 reveal_cluster 标记（默认安全闸·今天几乎全部）→ 原样透传（零行为变化）。
    其余字段（character/wants/tactic/obstacle/dialogue_act）一律保留——表达层言语策略·非秘密。
    """
    if not isinstance(objectives, list):
        return objectives
    out = []
    for o in objectives:
        if not isinstance(o, dict):
            out.append(o)
            continue
        rc = o.get("reveal_cluster")
        if rc is None:
            out.append(o)  # 默认安全闸：无 reveal_cluster 标记 → 原样透传（零行为变化）
            continue
        if _cluster_due(rc, current_cluster_id) is True:
            out.append(o)  # 到/越过揭晓点 → 保留 what_unsaid
        else:
            # 未到期 / 畸形 reveal_cluster → 剥 what_unsaid（保守隔离·防提前剧透）
            out.append({k: v for k, v in o.items() if k != "what_unsaid"})
    return out


def _collect_dialogue_objectives(cluster: dict, current_cluster_id=None) -> dict | None:
    """🔴 2026-06-29 对白即行动 dialogue_objectives 注入（对话表达层 P1·McKee《Dialogue: Art of Verbal Action》）。

    透传 outline-planner(A agent) 在 scene_storyboard 每个 scene 标注的 dialogue_objectives：
      [{character, wants(本场想从对方拿到什么), tactic(active verb 言语策略:试探/施压/回避/示弱/反问),
        obstacle(被谁/什么阻挠), dialogue_act(意图枚举), what_unsaid(压着不说的潜文本·只驱动表演·不写进正文)}]
    与 scene_causal_skeleton（事件 P0·Swain 场景骨架）平级并存——前者管「话怎么说(表达层)」·后者管「场景因果(事件层)」·正交。

    what_unsaid 隔离门控（防剧透·复用 _sanitize_dialogue_objectives 为单一真理源·与 gen_writer 直读路径同口径）：
      objective 标了 reveal_cluster 且未到期 → 剥 what_unsaid（防 writer 把未到期暗线说漏·对齐 _sanitize_character_belief）。

    默认安全闸：所有 scene 都无 dialogue_objectives → 返回 None（不注入·向后兼容旧 storyboard / 旧书·零行为变化）。
    advisory：对白即行动是场景级软提示·不强制每场都填·规划层只标意图(what)·措辞(how) writer 自由发挥
    （北极星④规划层管意图、创作层管表达 + ⑤不干涉创作·绝不 hard_gate）。
    """
    if not isinstance(cluster, dict):
        return None
    storyboard = cluster.get("scene_storyboard")
    if not isinstance(storyboard, list) or not storyboard:
        return None
    scenes: list[dict] = []
    has_any = False
    for idx, sc in enumerate(storyboard):
        if not isinstance(sc, dict):
            continue
        raw = sc.get("dialogue_objectives")
        if not isinstance(raw, list) or not raw:
            continue
        # 隔离门控（what_unsaid 涉未到期 hidden 伏笔 → 剥离·防剧透）
        safe = _sanitize_dialogue_objectives(raw, current_cluster_id)
        kept = [o for o in safe if isinstance(o, dict)]
        if kept:
            scenes.append({"scene_index": idx, "objectives": kept})
            has_any = True
    if not has_any:
        return None  # 默认安全闸：无任何 dialogue_objectives → 不注入
    return {
        "scenes": scenes,
        "directive": (
            "🟢 对白即行动 Dialogue-as-Action(对话表达层 P1·McKee verbal action·治 on-the-nose)：\n"
            "  · 本场每个角色对白即行动：X(character) 想要 Y(wants)·用 Z(tactic) 策略·被 W(obstacle) 阻挠。\n"
            "  · 对白是策略不是信息——每句台词是为达成 wants 采取的 active verb(试探/施压/回避/示弱/反问)·\n"
            "    不是把目标/背景/设定念出来。\n"
            "  · 每句话底下压着未说出口的目标(what_unsaid)——潜文本只驱动表演·**绝不写进正文**·\n"
            "    严禁把内心想法/情绪/目标直接说出口(透明原则·on-the-nose=说透目标=零潜台词=AI 腔)。\n"
            "  · dialogue_act(试探/回避/威胁/让步/反讽/求证/施压/示弱)是本句意图骨架·措辞(how)你自由发挥。\n"
            "  · 对白即行动是场景级软提示非逐句锁·爽文直球对喷场景可豁免 subtext(作者档第一权威·北极星⑤·<300 字理由)。"
        ),
        "_doc": "🔴 2026-06-29 对白即行动dialogue_objectives注入·advisory·绝不 hard_gate",
    }


def _collect_prose_scene_cards(cluster: dict) -> dict | None:
    """🔴 2026-07-05 prose_scene_cards 场景执行卡（借鉴分镜卡片，但转为小说写作读模型）。

    从 scene_storyboard 构建轻量 prose-first 场景卡，给 writer 一眼看到：
      · 本场标题/章节/角色焦点/场所；
      · 临场目标、阻力、转折压力；
      · 关键事件、感官锚点、dramatic_question。

    只读取小说表达层字段。即使上游 storyboard 混入 camera/shot/visual prompt 一类影视字段，
    本函数也不透传，避免写手把正文写成分镜或剧本体。无 scene_storyboard 时返回 None。
    """
    if not isinstance(cluster, dict):
        return None
    storyboard = cluster.get("scene_storyboard")
    if not isinstance(storyboard, list) or not storyboard:
        return None

    def text_from(obj: dict, keys: tuple[str, ...], limit: int = 180) -> str | None:
        for key in keys:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.strip().split())[:limit]
        return None

    def list_from(value, limit: int = 6, item_limit: int = 120) -> list[str]:
        if isinstance(value, str) and value.strip():
            return [" ".join(value.strip().split())[:item_limit]]
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = None
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                for key in ("name", "title", "event", "text", "summary", "surface_clue"):
                    if isinstance(item.get(key), str) and item.get(key).strip():
                        text = item.get(key)
                        break
            if isinstance(text, str) and text.strip():
                out.append(" ".join(text.strip().split())[:item_limit])
            if len(out) >= limit:
                break
        return out

    def sensory_from(scene: dict) -> list[str]:
        direct = list_from(
            scene.get("sensory_anchors")
            or scene.get("sensory_anchor")
            or scene.get("sensory_detail")
            or scene.get("atmosphere"),
            limit=4,
            item_limit=100,
        )
        if direct:
            return direct
        anchors = []
        for key in ("olfactory_anchor", "sound_anchor", "tactile_anchor", "visual_anchor"):
            value = text_from(scene, (key,), 100)
            if value:
                anchors.append(value)
        return anchors[:4]

    cards: list[dict] = []
    for idx, scene in enumerate(storyboard):
        if not isinstance(scene, dict):
            continue

        goal = text_from(scene, ("scene_goal", "goal", "objective"), 160)
        conflict = text_from(scene, ("conflict", "obstacle", "dilemma"), 160)
        turn = text_from(scene, ("disaster", "decision", "outcome", "result", "turning_point"), 160)
        question = text_from(scene, ("dramatic_question", "question"), 180)
        if not question and (goal or conflict):
            if goal and conflict:
                question = f"能否在{conflict}之下完成：{goal}？"
            elif goal:
                question = f"本场如何推进：{goal}？"
            else:
                question = f"本场阻力如何改变局面：{conflict}？"

        card: dict = {"scene_index": idx}
        title = text_from(scene, ("title", "scene_title", "name"), 100)
        if title:
            card["title"] = title
        if scene.get("ch") is not None:
            card["ch"] = scene.get("ch")
        focal = text_from(scene, ("focal_character", "pov_character", "viewpoint_character", "pov"), 80)
        if focal:
            card["focal_character"] = focal
        characters = list_from(scene.get("characters") or scene.get("character_focus"), limit=8, item_limit=60)
        if characters:
            card["characters"] = characters
        setting = text_from(scene, ("location", "setting", "place", "space"), 100)
        if setting:
            card["setting"] = setting
        if goal:
            card["scene_goal"] = goal
        if conflict:
            card["pressure"] = conflict
        if turn:
            card["turn"] = turn
        if question:
            card["dramatic_question"] = question
        events = list_from(scene.get("key_events") or scene.get("beats"), limit=6, item_limit=120)
        if events:
            card["key_events"] = events
        sensory = sensory_from(scene)
        if sensory:
            card["sensory_anchors"] = sensory

        if len(card) > 1:
            cards.append(card)

    if not cards:
        return None

    return {
        "cards": cards,
        "directive": (
            "🟢 Prose Scene Cards(小说场景执行卡·借鉴 storyboard 卡片但只服务正文写作)：\n"
            "  · 每张卡回答：谁在场、此刻想要什么、被什么阻挡、局面如何转向、读者要追问什么。\n"
            "  · 先写人物行动和因果压力，再让环境/感官锚点落地；不要把卡片字段机械写成段落标题。\n"
            "  · 这是写作读模型，不是影视分镜；只取 prose-first 字段，规划层给意图，表达层由 writer 自由完成。"
        ),
        "_doc": "🔴 2026-07-05 prose_scene_cards·storyboard卡片化借鉴·小说写作读模型·advisory",
    }


def _collect_event_cluster_context(scanner, chapter: int) -> dict:
    """v23 ECAS: 注入本章所属事件簇的 context (cluster_id / brief / mid_checkpoints / foreshadowing)。
    writer 在 MODE=ecas 时必读此字段。
    决策树:
    1. 读 _数据库/事件簇.json 找 status in (active 词表 · 中英文都认) 且 chapter_range 包含 chapter 的 cluster
    2. 找不到 → mode=off (本章是 非 cluster 模式)
    3. 找到 → 提取 brief 全字段
    """
    clusters_path = scanner.root / "_数据库" / "事件簇.json"
    if not clusters_path.exists():
        return {"mode": "off", "_note": "无事件簇.json，本章按 非 cluster 模式"}
    try:
        data = json.loads(clusters_path.read_text(encoding="utf-8"))
        clusters = data.get("clusters") or []
        if not clusters:
            return {"mode": "off", "_note": "事件簇.json 为空，本章按 非 cluster 模式"}
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
                    # 🔴 2026-06-27 C15: mode 细分——status active 命中后再校验 brief 是否真有内容。
                    # 上游声明 active 却 scope_summary + scene_storyboard 双空 → mode=contract_violation
                    # （不再静默注入空 brief 当 mode=on 让 writer 拿空约束写偏）。fluid 回归锁：
                    # scene_storyboard 已填即不空 → 照常走 mode=on（天灾继承法 cluster_006 实战）。
                    if _cluster_brief_empty(c):
                        _emit_empty_injection_signal(
                            "event_cluster_context", c.get("cluster_id") or "?", chapter)
                        return {
                            "mode": "contract_violation",
                            "cluster_id": c.get("cluster_id") or "",
                            "status": status,
                            "_error": (
                                f"cluster {c.get('cluster_id')} 声明 active（{status}）但 brief 内容为空"
                                f"（scope_summary + scene_storyboard 全空·注入契约破损）"),
                            "_hint": "重跑 py core/scripts/cluster_choice_apply.py 或 /outline step6.5 填 cluster brief",
                        }
                    cluster_id_val = c.get("cluster_id") or ""
                    is_first_cluster = cluster_id_val.endswith("_001") or cluster_id_val == "cluster_001"
                    narrative_mode = c.get("narrative_mode") or ("in_medias_res" if is_first_cluster else "linear")
                    # 🆕 R20 W9 Batch-AA P1: kishotenketsu_4act 起承转结无冲突模式合法化
                    # 由 cluster_emergence_engine 据 me.intent ∈ {healing/contemplative/iyashikei/zen} 标记
                    # build_manifest 注入四段 directive (ki / shō / ten / ketsu) 给 writer
                    # writer 据此走治愈/沉静节奏·末段不必强钩(hook_strength 在此模式降阈值)
                    _kishotenketsu_directive = None
                    if narrative_mode == "kishotenketsu_4act":
                        _kishotenketsu_directive = (
                            "🟢 本 cluster = 起承转结无冲突模式(kishōtenketsu_4act · iyashikei/治愈)：\n"
                            "  · 起(ki / 約25%)：稳态建立·人物处境+日常节奏·不抛悬念。\n"
                            "  · 承(shō / 約25%)：延展稳态·微变化(细节深化/季节流转/小动作堆叠)·仍无冲突。\n"
                            "  · 转(ten / 約25%)：横向偏离·一个不期而至的意外、感官事件或第三者介入·"
                            "非传统戏剧冲突·读者注意力被『侧推』而非『被钩』。\n"
                            "  · 结(ketsu / 約25%)：把『起承』与『转』收回成新稳态·留余味·"
                            "末段不必强钩(治愈节奏)·禁止强反转或 cliffhanger·允许平稳收束。"
                        )
                    # 🆕 R20 W9 Batch-CC P2 (2026-06-21): ACW Activity-Centric Writing directive
                    # 每段一个中心活动·防游走·env ACW_MODE=off/shadow/active(默认 shadow 不注入·active 注入)
                    # 与 kishotenketsu(段级节奏 macro)正交·ACW 是段级 micro 中心活动
                    _acw_mode = (os.environ.get("ACW_MODE") or "shadow").strip().lower()
                    _acw_directive = None
                    if _acw_mode == "active":
                        _acw_directive = (
                            "🟢 ACW Activity-Centric Writing 段中心活动指令(R20 Q3-Q4 id 21)：\n"
                            "  · 每段聚焦一个中心活动(动作链 / 对话回合 / 感官 sequence 三选一)·\n"
                            "    禁止单段内多个独立子动作堆叠(走到窗前→翻开账本→听到脚步声→想起昨天 = 游走感)。\n"
                            "  · 段首主语建立中心·段中所有句子服务于该中心·段末该中心收束或转移到下段。\n"
                            "  · 主语切换=换段·主语稳定=同段·防止段内频繁切换主语造成读者注意力分散。\n"
                            "  · 与 kishotenketsu macro 节奏正交·ACW 是段级 micro 中心活动一致性。"
                        )
                    # 🆕 R23 W11 Batch-GG P0 (2026-06-22): pace_carrier_window 钩子双侧 ±300 CJK 长记区指引
                    # neural PACE 框架·钩子两侧 300 CJK 内放长记设定/伏笔/物件 → 后续 cluster 召回率高
                    # env PACE_CARRIER_WINDOW_MODE=active 时注入 advisory directive
                    _pace_carrier_mode = (os.environ.get("PACE_CARRIER_WINDOW_MODE") or "shadow").strip().lower()
                    _pace_carrier_window = None
                    if _pace_carrier_mode == "active":
                        _pace_carrier_window = {
                            "radius_cjk": 300,
                            "directive": (
                                "🟢 PACE 长记 window 指令(R23 W11 Batch-GG·neural PACE 框架)：\n"
                                "  · 章末/段末钩子两侧 ±300 CJK 是读者长记最强 window·\n"
                                "    把设定/伏笔/物件锚点（locked_fact / foreshadowing_to_plant）优先放此 window·\n"
                                "    后续 cluster 召回率显著高于章中段 baseline。\n"
                                "  · 纯节奏型钩子（强情绪标点 + 短句独行 + 无 lexical anchor）合法·\n"
                                "    豁免 carrier 要求·不强制每钩必带 anchor。"
                            ),
                            "_doc": "R23 W11 P0·shadow→active 软提示·advisory·绝不 hard_gate",
                        }
                    # 🆕 R22 W10 Batch-DD P0 STRONG (2026-06-21): rhetorical_subset 按题材匹配高频辞格 subset
                    # 陈望道《修辞学发凡》38 格四类·题材 prior 注入 writer prompt 提示偏好辞格
                    # env RHETORICAL_BALANCE_MODE=active 时注入 advisory subset hint
                    _rhet_subset = None
                    _rhet_mode = (os.environ.get("RHETORICAL_BALANCE_MODE") or "shadow").strip().lower()
                    if _rhet_mode == "active":
                        try:
                            sys.path.insert(0, str(Path(__file__).parent))
                            import rhetorical_inventory as _ri
                            _book_genre = ""
                            try:
                                _book_genre = _resolve_book_genre(scanner) or ""
                            except Exception:
                                pass
                            _rhet_subset = _ri.match_subset_by_genre(_ri.load_inventory(), _book_genre)
                        except Exception:
                            _rhet_subset = None
                    # 🆕 R7 W2 (2026-06-20): narrative_pov_mode 五分类 (Stanzel/Cohn consonant-dissonant)
                    # first_present / first_retro_consonant / first_retro_dissonant / third_limited / third_omniscient
                    # 与 narrative_mode (in_medias_res/linear 时间序) 正交。cluster 优先 → 作者档兜底 → "third_limited" 默认。
                    _valid_pov_modes = {"first_present", "first_retro_consonant",
                                         "first_retro_dissonant", "third_limited", "third_omniscient"}
                    npm_raw = c.get("narrative_pov_mode")
                    if not (isinstance(npm_raw, str) and npm_raw.strip().lower() in _valid_pov_modes):
                        # 退作者档
                        try:
                            _ap = scanner.root / "_数据库" / "作者风格.json"
                            if _ap.exists():
                                _obj = json.loads(_ap.read_text(encoding="utf-8"))
                                if isinstance(_obj, dict):
                                    _v = _obj.get("narrative_pov_mode")
                                    if isinstance(_v, str) and _v.strip().lower() in _valid_pov_modes:
                                        npm_raw = _v.strip().lower()
                        except (json.JSONDecodeError, OSError):
                            pass
                    narrative_pov_mode = (npm_raw.strip().lower()
                                          if isinstance(npm_raw, str) and npm_raw.strip().lower() in _valid_pov_modes
                                          else "third_limited")
                    return {
                        "mode": "on",
                        "cluster_id": cluster_id_val,
                        "research_ref": c.get("research_ref") if isinstance(c.get("research_ref"), dict) else None,
                        "parent_me": c.get("parent_me"),
                        "scope_summary": c.get("scope_summary"),
                        "scenes_estimated": c.get("scenes_estimated"),
                        "anchor_props": c.get("anchor_props") or [],
                        # R7 W2 P1：Proust 嗅觉/味觉触发非自愿记忆/闪回锚（与 foreshadowing 槽并列·advisory）
                        # outline 阶段填写 cluster.olfactory_anchors=[{"trigger":"桂花香","memory_seed":"母亲的厨房","scene":N}...]
                        # writer 闪回 beat 优先用嗅觉/味觉触发非『他想起』式 hindsight tell（D4 advisory）。
                        "olfactory_anchors": c.get("olfactory_anchors") or [],
                        # 🔴 2026-06-28 伏笔明暗线隔离：埋设只注入明线 surface_clue（剥离暗线 hidden_payoff）；
                        # callback 到 trigger_cluster 才暴露 hidden_payoff + reveal 指令（让写手兑现）。
                        "foreshadowing_to_plant": _sanitize_foreshadowing_to_plant(c.get("foreshadowing_to_plant")),
                        "foreshadowing_to_callback": _resolve_foreshadowing_to_callback(
                            c.get("foreshadowing_to_callback"), cluster_id_val),
                        "mid_checkpoints": c.get("mid_checkpoints") or [3000, 6000, 9000],
                        "sub_summary_template": c.get("sub_summary_template") or "[场景 N] 关键事件 + 角色行动 + 伏笔进度（100 字内）",
                        "opus_recommended": c.get("opus_recommended", False),
                        "extended_thinking": c.get("extended_thinking", False),
                        "ME_to_advance": c.get("ME_to_advance") or [],
                        "throughline_focus": c.get("throughline_focus") or [],
                        "characters_focus": c.get("characters_focus") or [],
                        "hub_locations": c.get("hub_locations") or [],
                        "cluster_position_hint": _infer_cluster_position(chapter, cr) if cr else "head",
                        "narrative_mode": narrative_mode,
                        "narrative_pov_mode": narrative_pov_mode,
                        "climax_hint_scene_index": c.get("climax_hint_scene_index"),
                        # 🔴 2026-06-28 写手信息隔离（item 9）：早期 cluster 把收敛锚的 final_image/ending_image
                        # 降精到软方向（不整删不 None·北极星③软牵引载体）·volume_arc/core_conflict/milestones 始终全给。
                        "volume_convergence_anchor": _soften_convergence_anchor(
                            _build_volume_convergence_anchor(scanner, c), cluster_id_val, len(clusters)),
                        # 🔴 2026-06-29 controlling_idea主控思想软注入（主题脊柱 P0·北极星③软牵引·骑 _soften_convergence_anchor 同轨）：
                        # 读 大势卡.story_destiny.controlling_idea(A agent 产)·早期 cluster 给软主题方向·越近卷末越显化(同 convergence_anchor 降精逻辑)。
                        # 默认安全闸：无 controlling_idea(旧大势卡/旧书) → None(不注入·零行为变化)。全 advisory·绝不 hard_gate·不进 HARD_GATE_CODES。
                        "controlling_idea_anchor": _build_controlling_idea_anchor(
                            scanner, cluster_id_val, len(clusters)),
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
                        "kishotenketsu_directive": _kishotenketsu_directive,
                        "ACW_DIRECTIVE": _acw_directive,
                        # 🆕 R23 W11 Batch-GG P0: 钩子双侧 ±300 CJK 长记 window 指引
                        "pace_carrier_window": _pace_carrier_window,
                        # 🆕 R23 W11 Batch-HH P1 (2026-06-22): 信念更新意图 + LC-NE 边界锐度 + frisson lead window
                        # belief_update_intent: {preserve|update|None}·writer 据此决定末段是否要 PE 收束
                        # event_boundary_sharpness: {sharp|dull|default}·volume_finale 强制 sharp 由 emergence 注入
                        # frisson_lead_window: climax beat 前 1-2 段 200-400 CJK 节奏锐化指令（active 模式）
                        "belief_update_intent": c.get("belief_update_intent"),
                        "event_boundary_sharpness": (
                            "sharp" if c.get("is_volume_finale")
                            else (c.get("event_boundary_sharpness") or "default")
                        ),
                        "frisson_lead_window": (
                            {
                                "lead_cjk_range": [200, 400],
                                "directive": (
                                    "🟢 frisson 锐化指令(R23 W11 Batch-HH·neural frisson 时序前置)：\n"
                                    "  · climax beat 前 1-2 段（约 200-400 CJK）= frisson lead window·\n"
                                    "    在 lead window 做节奏锐化：短句独行 + 标点高潮 + 感官聚焦。\n"
                                    "  · climax 句本身相对略钝（情绪降落式）·让战栗在前置预期中累积。\n"
                                    "  · 无 climax beat 的 cluster 豁免（如治愈/iyashikei kishotenketsu 模式）。"
                                ),
                                "_doc": "R23 W11 Batch-HH·shadow→active 软提示·advisory·绝不 hard_gate",
                            }
                            if (os.environ.get("FRISSON_LEAD_MODE") or "shadow").strip().lower() == "active"
                            else None
                        ),
                        # 🆕 R24 W12 Batch-JJ P0 STRONG (2026-06-22): 9 类爆点 intended_burst_type
                        # event_cluster_context 回灌让 writer 看见目标爆点·尾部 80-200 CJK 收束
                        "intended_burst_type": c.get("intended_burst_type"),
                        "burst_type_directive": (
                            {
                                "intended": c.get("intended_burst_type"),
                                "tail_cjk_range": [80, 200],
                                "supported_types": [
                                    "laughter", "shock", "grief", "anticipation",
                                    "shipping", "awe", "critique", "callback", "meta"
                                ],
                                "directive": (
                                    "🟢 9 类爆点收束指令(R24 W12 Batch-JJ·直播弹幕预测)：\n"
                                    f"  · 本 cluster 目标爆点类 = {c.get('intended_burst_type') or '未设·按场景自由发挥'}\n"
                                    "  · 在 cluster 尾部 80-200 CJK 内把弹幕反应面收束到该类·\n"
                                    "    例：shock = 怔住/震惊/瞳孔/倒吸；laughter = 笑/扑哧/捂嘴；\n"
                                    "    grief = 泪/哭/哽咽；anticipation = 未完/下次/却不知；\n"
                                    "    shipping = 心动/脸红/对视；awe = 霸气/锋芒/睥睨；\n"
                                    "    critique = 嗤/冷笑/嘲；callback = 原来/果然/想起；\n"
                                    "    meta = 诸位/看官/且说。\n"
                                    "  · intended 未设时按场景自由·不强行套类。"
                                ),
                                "_doc": "R24 W12 Batch-JJ·shadow→active 软提示·advisory·绝不 hard_gate",
                            }
                            if (os.environ.get("BURST_TYPE_MODE") or "shadow").strip().lower() == "active"
                            else None
                        ),
                        # 🆕 R24 W12 Batch-KK P1 (2026-06-22): writer intent anchor 4 维盲意图卡
                        # build_manifest 注入·writer 在 step 2 必须按 4 字段写·
                        # step 6 后 agenda_drift_scanner 比对·任一<0.62 → advisory
                        "writer_intent_anchor": _load_writer_intent_anchor_for_manifest(
                            scanner.root, c.get("cluster_id", "")),
                        # 🆕 R24 W12 Batch-KK P1 (2026-06-22): author_signature_slots
                        # storyboard 级 [{slot_id, text, preserve_policy, anchor_hint}]
                        # writer 必须 MUST PRESERVE EXACTLY（verbatim/near_verbatim_punct_only）
                        # 草稿落地后 author_signature_preservation.py fuzzy match Lev≤5% 闸
                        "author_signature_slots": _collect_author_signature_slots(c),
                        "author_signature_directive": (
                            {
                                "policies": ["verbatim", "near_verbatim_punct_only"],
                                "directive": (
                                    "🟢 作者签名 slot 防篡改指令(R24 W12 Batch-KK)：\n"
                                    "  · MUST PRESERVE EXACTLY: storyboard.author_signature_slots[]\n"
                                    "    每 slot text 字段必须在草稿中按 policy 保留：\n"
                                    "      - verbatim: 逐字保留(Lev 距离 ≤5%)\n"
                                    "      - near_verbatim_punct_only: 仅允许标点级差异\n"
                                    "  · anchor_hint 指示 slot 应放置的场景/位置·writer 据此放置。"
                                ),
                                "_doc": "R24 W12 Batch-KK·shadow→active 软提示·advisory·绝不 hard_gate",
                            }
                            if (os.environ.get("AUTHOR_SIGNATURE_MODE") or "shadow").strip().lower() == "active"
                            else None
                        ),
                        # 🆕 R24 W12 Batch-JJ P1 (2026-06-22): Glaser 四杠杆 D9.3 advisory
                        "glaser_four_levers_directive": (
                            {
                                "levers": ["dramatize", "emotionalize",
                                           "personalize", "fictionalize"],
                                "directive": (
                                    "🟢 Glaser 四杠杆 D9.3 advisory(R24 W12 Batch-JJ·教育叙事)：\n"
                                    "  · info-dump 段（长段+无对话+抽象名词高密度）必须激活 ≥1 杠杆：\n"
                                    "    dramatize=动作动词；emotionalize=情绪词；\n"
                                    "    personalize=具名角色介入；fictionalize=具体物件/五感。\n"
                                    "  · 最便宜优先序：fictionalize > dramatize > emotionalize > personalize。"
                                ),
                                "_doc": "R24 W12 Batch-JJ·shadow→active 软提示·advisory·绝不 hard_gate",
                            }
                            if (os.environ.get("GLASER_LEVERS_MODE") or "shadow").strip().lower() == "active"
                            else None
                        ),
                        # 🆕 R22 W10 Batch-DD P0 STRONG: 题材 prior 推荐辞格 subset
                        "rhetorical_subset_hint": _rhet_subset,
                        # 🔴 2026-06-27 P1-08: cluster brief 注入 motif_callback_hints（上一 cluster 的 dormant motif 回收建议）。
                        # 数据源=motif_advisory_snapshot.json.dormant_motifs（top-5），由 motif_recurrence_ledger 落盘。
                        # advisory · 永不 hard_gate · 不存在时为空 list（守北极星⑤顾问非法官）。
                        "motif_callback_hints": _collect_motif_callback_hints_for_cluster(scanner),
                        # 🔴 2026-07-05 prose_scene_cards：借鉴 moyin-creator 的 storyboard/scene card 思路，
                        # 但转换为小说正文执行卡；白名单提取 prose 字段，忽略 camera/shot/visual prompt，防剧本体污染。
                        "prose_scene_cards": _collect_prose_scene_cards(c),
                        # 🔴 2026-06-29 But-Therefore因果连接器+Swain场景骨架（事件 P0·治流水账·涟漪微观可执行化）：
                        # 透传 outline-planner 在 scene_storyboard 标注的 scene_type/proactive/reactive/link_to_prev/result_type，
                        # 并注入『相邻 scene 须 but/therefore 衔接·避免 and_then 平铺·结果禁纯 yes』指令给 writer。
                        # 默认安全闸：scene 无这些字段 → None（不注入·向后兼容旧 storyboard / 旧书）。advisory 永不 hard_gate。
                        "scene_causal_skeleton": _collect_scene_causal_skeleton(c),
                        # 🔴 2026-06-29 对白即行动dialogue_objectives注入（对话表达层 P1·McKee verbal action·治 on-the-nose）：
                        # 透传 scene_storyboard 每场的 dialogue_objectives(character/wants/tactic/obstacle/dialogue_act/what_unsaid)
                        # + 注入『对白是策略不是信息·严禁把内心想法/情绪/目标直接说出口(透明原则)』指令给 writer。
                        # what_unsaid 涉未到期 hidden 伏笔(标 reveal_cluster) → _sanitize_dialogue_objectives 隔离门控剥离防剧透。
                        # 默认安全闸：scene 无 dialogue_objectives → None（不注入·向后兼容旧 storyboard / 旧书）。advisory 永不 hard_gate。
                        "dialogue_objectives": _collect_dialogue_objectives(c, cluster_id_val),
                        "_narrative_mode_doc": ("in_medias_res = 黄金三章倒叙（cluster_001 默认开启 · 强冲突放最前）；"
                                                "linear = 时间序；kishotenketsu_4act = 起承转结无冲突(治愈/iyashikei)"),
                        "_narrative_pov_mode_doc": ("R7 W2 五分类(Stanzel/Cohn): "
                                                     "first_present(第一人称当下时)/"
                                                     "first_retro_consonant(第一人称回溯·贴近 experiencing-self)/"
                                                     "first_retro_dissonant(第一人称回溯·拉远 narrating-self 评点)/"
                                                     "third_limited(第三人称有限)/third_omniscient(第三人称全知)。"
                                                     "first_retro_* 模式触发 firstperson_retro_self_gap_scanner advisory"),
                        "_writer_hint": "MODE=ecas: 用此 brief 生成 8K-16K 字 cluster_draft，每 3000 字 self-audit，每场景生成 100 字 sub-summary"
                    }
        return {"mode": "off", "_note": f"无匹配 cluster (本章 {chapter} 不在任何 active cluster 范围)"}
    except Exception as e:
        return {"mode": "error", "_error": str(e)[:200]}


def _load_writer_intent_anchor_for_manifest(project_root, cluster_id: str) -> dict | None:
    """R24 W12 Batch-KK · 读 writer_intent_anchor（盲意图卡 SHA-256 锁定）。

    cluster_id 形如 cluster_001 / cluster_无脸者_001 — 提取末段 key 反查。
    SHA-256 校验失败 → None（safer than 注入篡改值）。
    """
    if not cluster_id:
        return None
    # 提取 cluster_key（末段数字 / token）
    key = str(cluster_id).split("_")[-1] if cluster_id else ""
    if not key:
        return None
    try:
        import writer_intent_anchor as wia
        a = wia.load_anchor(project_root, key)
        if not a:
            return None
        return {
            "want": a.get("want", ""),
            "antagonist": a.get("antagonist", ""),
            "stake": a.get("stake", ""),
            "tone_word": a.get("tone_word", ""),
            "_sha256": a.get("_sha256", ""),
            "_directive": (
                "🟢 4 维盲意图卡(R24 W12 Batch-KK·反算法议程占领)：\n"
                f"  · want={a.get('want', '')}\n"
                f"  · antagonist={a.get('antagonist', '')}\n"
                f"  · stake={a.get('stake', '')}\n"
                f"  · tone_word={a.get('tone_word', '')}\n"
                "  · 写作过程不得偏离·step 6 后 agenda_drift_scanner 比对。"
            ),
        }
    except Exception:
        return None


def _collect_author_signature_slots(cluster: dict) -> list[dict]:
    """R24 W12 Batch-KK · 从 storyboard / cluster 顶层收集 author_signature_slots。

    格式：[{slot_id, text, preserve_policy: verbatim|near_verbatim_punct_only, anchor_hint}]
    供 writer 在指定位置逐字保留；author_signature_preservation.py 后置校验。
    """
    slots = []
    if not isinstance(cluster, dict):
        return slots
    for sb in cluster.get("scene_storyboard", []) or []:
        if not isinstance(sb, dict):
            continue
        for s in sb.get("author_signature_slots", []) or []:
            if isinstance(s, dict) and s.get("text"):
                slots.append({
                    "slot_id": s.get("slot_id") or "unnamed",
                    "text": s.get("text"),
                    "preserve_policy": s.get("preserve_policy") or "verbatim",
                    "anchor_hint": s.get("anchor_hint") or "",
                    "scene_ch": sb.get("ch"),
                })
    for s in cluster.get("author_signature_slots", []) or []:
        if isinstance(s, dict) and s.get("text"):
            slots.append({
                "slot_id": s.get("slot_id") or "unnamed",
                "text": s.get("text"),
                "preserve_policy": s.get("preserve_policy") or "verbatim",
                "anchor_hint": s.get("anchor_hint") or "",
                "scene_ch": None,
            })
    return slots


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

    背景：character_arc_state.json 由 character_arc_update.py 在 cluster-save-state 中滚动写、
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


# 🔴 2026-06-29 场景级Appraisal Beat注入（心理 P0·情绪余烬 + 本块方向·上限防 prompt 膨胀）
_APPRAISAL_RESIDUE_MAX = 2   # 注入的历史 cluster 情绪余烬条数（延续上块强情绪）
_APPRAISAL_PLANNED_MAX = 4   # 注入的本 active cluster 已规划 beat 条数
# appraisal 6 维评价子字段 → 中文标签（渲染「为何感受」摘要·只渲染语义值·不造情绪词标签）
_APPRAISAL_DIM_LABELS = {
    "relevance": "相关性",
    "congruence": "合意性",
    "certainty": "确定性",
    "coping_potential": "应对力",
    "accountability": "归因",
    "agency": "归因",
    "norm_compat": "规范契合",
}


def _appraisal_beat_to_lines(b: dict, *, residue: bool) -> list[str]:
    """把一条 appraisal_beat 渲染成 advisory 方向卡文字（为何感受 + 情绪走向 + 如何外化）。

    🔴 北极星⑤纪律：只渲染 derived_emotion(自然语言推理·非标签) + appraisal 评价(为何) +
    behavior_externalization(如何外化·动作非情绪词)，**绝不由本函数造『他很愤怒/心中一凛』式情绪词标签**。
    """
    focal = (str(b.get("focal_character") or "")).strip() or "（本场 POV 角色）"
    trig = (str(b.get("trigger_event") or "")).strip()
    de = (str(b.get("derived_emotion") or "")).strip()
    bx = (str(b.get("behavior_externalization") or "")).strip()
    ap = b.get("appraisal") if isinstance(b.get("appraisal"), dict) else {}
    why_parts = []
    for k, label in _APPRAISAL_DIM_LABELS.items():
        v = ap.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        why_parts.append(f"{label}={str(v).strip()}")
    pros = b.get("prospect") if isinstance(b.get("prospect"), dict) else {}
    ptype = (str(pros.get("type") or "")).strip()
    presolved = (str(pros.get("resolved_to") or "")).strip()
    tag = "情绪余烬（上块延续·不归零）" if residue else "本拍情绪方向"
    head = f"- 【{tag}】{focal}"
    if trig:
        head += f"｜触发：{trig}"
    lines = [head]
    if why_parts:
        lines.append(f"    · 为何（评价 appraisal·不贴情绪标签）：{'·'.join(why_parts)}")
    if ptype:
        pl = f"    · 预期（prospect）：{ptype}"
        if presolved:
            pl += f" → {presolved}"
        lines.append(pl)
    if de:
        lines.append(f"    · 情绪走向（derived_emotion·写成评价+反应不写标签）：{de}")
    if bx:
        lines.append(f"    · 如何外化（写成动作/细节·非情绪词）：{bx}")
    return lines


def _collect_appraisal_directive(scanner, current_cluster_id) -> dict | None:
    """🔴 2026-06-29 场景级Appraisal Beat注入（心理 P0·chain-of-emotion 两步法·治情绪贴标签/逐场景重置）。

    读 叙事节拍器.json 的 appraisal_beats[]（A agent 由 Claude 梳理读正文回填·schema：
      {cluster_id, scene_idx, focal_character, trigger_event,
       appraisal:{relevance,congruence,certainty,coping_potential,accountability,norm_compat},
       prospect:{type,resolved_to}, derived_emotion(自然语言·非情绪词标签),
       behavior_externalization(外化成动作/细节), vad_bin}）。

    注入两部分 advisory 情绪方向卡：
      ① 情绪余烬（emotion residue）：上一/历史 cluster 末尾强情绪 beat → 本块开篇情绪不归零延续/衰减
      ② 本 active cluster 已规划的 beat → 这一拍 focal_character 情绪应往哪走 + 为何 + 如何外化

    🔴 北极星⑤纪律：只注入「为何感受（appraisal 评价）+ 如何外化（behavior_externalization·动作非情绪词）+
       derived_emotion 方向」，**绝不注入『他很愤怒/心中一凛』式情绪词标签**（否则退化反 AI 腔堆砌·撞禁用词）。
       全 advisory·绝不 hard_gate（情绪是创作判断）。只排 active cluster·守 fluid 涌现（不预设 cluster_002+）。
    默认安全闸：无叙事节拍器.json / 无 appraisal_beats / 空 → None（不注入·向后兼容旧书·零行为变化）。
    """
    pacer_path = scanner.root / "_数据库" / "叙事节拍器.json"
    if not pacer_path.exists():
        return None
    try:
        pacer = json.loads(pacer_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    beats = pacer.get("appraisal_beats") if isinstance(pacer, dict) else None
    if not isinstance(beats, list) or not beats:
        return None
    try:
        cur_num = cluster_lookup.cluster_num(current_cluster_id) if current_cluster_id else None
    except Exception:
        cur_num = None
    # cur 无法定位序号 → 无法判定『历史 vs 未来』，默认安全不注入（守 fluid·不泄露未来块情绪）
    if cur_num is None:
        return None
    current_beats: list[dict] = []
    prior_beats: list[dict] = []
    for b in beats:
        if not isinstance(b, dict):
            continue
        try:
            bnum = cluster_lookup.cluster_num(b.get("cluster_id")) if b.get("cluster_id") else None
        except Exception:
            bnum = None
        if bnum is None:
            continue
        if bnum == cur_num:
            current_beats.append(b)        # 本 active cluster 已规划 beat
        elif bnum < cur_num:
            prior_beats.append(b)          # 历史 cluster → 情绪余烬候选
        # bnum > cur_num（未来块）→ 跳过：绝不注入未来块情绪（守 fluid 涌现·不预设/不泄露）
    # ① 情绪余烬：取最近 N 条历史 beat（按出现顺序末尾·延续上块强情绪）
    residue = prior_beats[-_APPRAISAL_RESIDUE_MAX:] if prior_beats else []
    # ② 本块已规划 beat（advisory 方向·限量防 prompt 膨胀·守 feedback_writer_prompt_bloat）
    planned = current_beats[:_APPRAISAL_PLANNED_MAX]
    if not residue and not planned:
        return None
    lines: list[str] = [
        "🟢 场景级 Appraisal 情绪方向卡（心理 P0·chain-of-emotion·advisory·顾问非法官）：",
        "  · 情绪靠『事件 → 角色如何评价(appraisal) → 外化成动作/细节』写(appraisal-as-prose)，",
        "    **不写『他感到X / 他很愤怒 / 心中一凛』式情绪词标签**（贴标签 = 反 AI 腔·撞禁用词）。",
        "  · 余烬：上一块的强情绪不归零，本块开篇情绪在其基础上延续 / 自然衰减。",
    ]
    for b in residue:
        lines.extend(_appraisal_beat_to_lines(b, residue=True))
    for b in planned:
        lines.extend(_appraisal_beat_to_lines(b, residue=False))
    return {
        "mode": "on",
        "gate_level": "advisory",
        "residue_count": len(residue),
        "planned_count": len(planned),
        "directive": "\n".join(lines),
        "_doc": "🔴 2026-06-29 场景级Appraisal Beat注入·心理 P0·advisory·绝不 hard_gate",
    }


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
        # 🔴 2026-06-28 写手信息隔离（item 4）：修恒真 bug——原
        # `c.get("visible_to_protagonist") is False or True` 永远 True 且读错字段（visible_to_protagonist）。
        # 改：所有 active clock 仍注入（writer 需感知倒计时存在），但经 _sanitize_clock_to_writer 按
        # visible_to_writer（缺则默认 True 向后兼容·clock_engine.list_active 将补此字段）或 ticks>=max 决定
        # 是否含 trigger_on_max（满格触发的事件结果·未到不该让写手提前知道具体后果）。
        visible = [_sanitize_clock_to_writer(c) for c in active]
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
        # 🔴 2026-06-28 写手信息隔离（item 3a）：hidden faction 经 _sanitize_faction_focus 剥真 current_focus
        # 留 surface_focus·数值（power/stability/wealth = 涟漪 surface·北极星②）always 留。默认安全闸：无 hidden 标记原样。
        factions = {}
        for name, f in (world.get("factions_state") or {}).items():
            fs = _sanitize_faction_focus(f)
            factions[name] = {
                "power": f.get("power"),
                "stability": f.get("stability"),
                "wealth": f.get("wealth"),
                "current_focus": (fs.get("current_focus") or "")[:50],
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
                # 🔴 2026-06-28 写手信息隔离（item 7）：剥 outcome_if_complete（幕后线程完成后的未来结果）·
                # 留 current_action（公开动向·涟漪 surface·北极星②）。写手只知 NPC 正在做什么·不预知结局。
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


# 🔴 2026-06-29 环境location回喂闭环
_SENSORY_PREFIX_CN = {"smell": "嗅", "sound": "听", "touch": "触", "light": "光"}


def _collect_location_atmosphere(scanner, chapter: int) -> dict | None:
    """🔴 2026-06-29 环境location回喂闭环（环境 P0·闭合 location_atmosphere_registry feed-forward）。

    系统已由 location_signature_consistency.update_registry 增量维护
    _数据库/location_atmosphere_registry.json（{location_name:{signature_sensory_motifs:[嗅/听/触/光],...}}），
    但 build_manifest 此前**零消费**——纯写入只做事后漂移检测·从不正向告诉写手该地点有什么签名感官。
    本 collector 把已学到的地点感官签名【正向回喂】writer（advisory 素材池·非硬约束）。

    定位：经 current_scene().location / 当前 cluster.hub_locations / character_positions 三路收集候选地点名，
    与 registry key 做双向 substring 匹配。
    默认安全闸：无 registry / 定位不到地点 / 命中地点无 signature → 返回 None（不注入·向后兼容旧书·零行为变化）。
    豁免：尊重作者档 location_atmosphere_override.allow_drift_locations（季节/灾后/时间推移合法漂移不锁）。
    advisory：永不 hard_gate（北极星⑤·正向素材池非填空题）。
    """
    reg = scanner.load("location_atmosphere_registry", {})
    if not isinstance(reg, dict) or not reg:
        return None

    # allow_drift 豁免（作者档第一权威）
    allow_drift = set()
    ap = scanner.load("作者风格", {})
    if isinstance(ap, dict):
        ovr = ap.get("location_atmosphere_override") or {}
        if isinstance(ovr, dict):
            for loc in (ovr.get("allow_drift_locations") or []):
                if isinstance(loc, str) and loc.strip():
                    allow_drift.add(loc.strip())

    # ── 定位当前场景地点候选 ──
    candidates: set[str] = set()
    scene = scanner.current_scene() or {}
    if isinstance(scene, dict):
        for key in ("location", "place", "scene_location", "setting"):
            v = scene.get(key)
            if isinstance(v, str) and v.strip():
                candidates.add(v.strip())
    # 当前 cluster 的 hub_locations
    try:
        for cl in (scanner.load("事件簇", {}) or {}).get("clusters", []) or []:
            if not isinstance(cl, dict):
                continue
            cr = cl.get("chapter_range") or []
            if isinstance(cr, list) and len(cr) == 2 and cr[0] <= chapter <= cr[1]:
                for hl in (cl.get("hub_locations") or []):
                    if isinstance(hl, str) and hl.strip():
                        candidates.add(hl.strip())
    except Exception:
        pass
    # 出场角色当前位置
    try:
        for loc in scanner.character_positions().values():
            if isinstance(loc, str) and loc.strip():
                candidates.add(loc.strip())
    except Exception:
        pass

    if not candidates:
        return None  # 默认安全闸：定位不到地点 → 不注入

    # ── registry key 与候选双向 substring 匹配 ──
    matched: list[dict] = []
    seen = set()
    for loc, entry in reg.items():
        if not isinstance(loc, str) or not isinstance(entry, dict):
            continue
        if loc in allow_drift or loc in seen:
            continue
        if not any(loc == c or loc in c or c in loc for c in candidates):
            continue
        sig = entry.get("signature_sensory_motifs") or []
        sig = [m for m in sig if isinstance(m, str) and m.strip()]
        if not sig:
            continue
        seen.add(loc)
        matched.append({
            "location": loc,
            "signature_sensory_motifs": sig,
            "occurrences": entry.get("occurrences"),
        })

    if not matched:
        return None  # 默认安全闸：无命中地点或命中地点无 signature → 不注入

    return {
        "locations": matched,
        "directive": (
            "🟢 地点签名感官回喂(环境 P0·setting-as-character·正向素材池)：\n"
            "  · 本场景登场地点的签名感官见 locations[].signature_sensory_motifs"
            "（前缀 smell=嗅/sound=听/touch=触/light=光·此前 cluster 已为该地点建立的恒定质感）。\n"
            "  · 重访地点延续其签名感官(读者潜意识认地点)·但这是【素材池非填空题】：\n"
            "    选 1-2 个主感官写透·不要把 5 感全堆上(写得精 > 写得多)。\n"
            "  · 季节/灾后/时间推移导致的合法漂移由作者档 allow_drift 豁免·环境是活的不锁死。"
        ),
        "_doc": "🔴 2026-06-29 环境location回喂闭环·advisory·正向素材池非硬约束·绝不 hard_gate",
    }


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
                # 🔴 2026-06-28 写手信息隔离（item 7）：_sanitize_offscreen 剥 result_expected/outcome_if_complete
                # （未来结果）+ current_plan/goals_short（幕后真实意图）·留 visible action（涟漪 surface·北极星②）。
                out.append(_sanitize_offscreen({
                    "character": name,
                    "character_id": c.get("id"),
                    "action_index": idx,
                    "action": act.get("action", ""),
                    "result_expected": act.get("result", ""),
                    "visible_to_protagonist": act.get("visible_to_protagonist", False),
                    "ch_range": ch_range,
                    "current_plan": offscreen.get("current_plan", ""),
                    "goals_short": (offscreen.get("goals", []) or [""])[0][:60],
                }))
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

    P2 typed 检索契约（2026-07-07·借鉴 AI_NovelGenerator/PlotPilot 向量检索溯源，见
    research/open_source_writing_systems.md「Vector retrieval provenance」）——命中路径返回：
      段级：source_type="selective_history" / match_method="embedding:<EMBED_BACKEND 名>" /
            token_budget（本段无段级截断→budget=text_preview 索引期既有 80 字上限×条数、
            actual=实际字符数、truncated:false）/ anti_copy="reference-not-copy"（advisory·
            防 writer 照抄近邻正文原句）。
      条级：source_id="chNNN#idx"（ch+chunk_idx 确定性派生）/ similarity（余弦）/
            match_method / recency_distance（当前章−来源章）+ 原 ch/chunk_idx/text_preview。
    skip 路径（首章/无 query 信号/无真后端/无索引）返回 {"retrieved": [], "reason": ...}
    形态**逐字节不变**——「无真语义就不伪装语义」纪律保持。
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

    # A6-1 query 扩展（2026-07-08·检索三段式·rag_retriever 单一实现）：brief 实体×属性
    # 组合词组前置拼入 query（确定性拼装零 LLM）。无 brief 信号 → 零变化。
    try:
        import rag_retriever as _rag_a6
        _expansion = _rag_a6.expand_query_from_brief(scanner.root, chapter)
    except Exception:
        _expansion = []
    if _expansion:
        query = " ".join(_expansion) + " " + query

    # 加载 embedding 模块
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import embedding_store
    except ImportError:
        return {"retrieved": [], "reason": "embedding_store 不可用"}

    # 🔴 2026-07-02 bug fix：此前本函数裸用 compute_embedding/cosine_similarity 且无门控——
    # 默认 EMBED_BACKEND（hash md5 ngram 袋）算出的"相似度"是纯噪声，却会静默冒充语义检索结果
    # 喂给 writer。本函数无非语义回退路径，无真后端时直接跳过整段语义检索、诚实返回空
    # （同 topic_drift_scanner「无真语义就不伪装语义」纪律）。
    if not _has_real_embedding_backend():
        return {"retrieved": [], "reason": "无真 embedding 后端（EMBED_BACKEND 未设/=hash）"
                                            "·hash 假嵌入不可当语义检索用·跳过"}

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

    # P2 typed 检索契约：match_method 记真实后端名（EMBED_BACKEND；未设但有 GEN_EMBED__ profile
    # 时门控已放行 → 记 gen_embed_profile）；source_id 由 ch+chunk_idx 确定性派生。
    _backend = os.environ.get("EMBED_BACKEND", "").strip().lower()
    _match_method = f"embedding:{_backend or 'gen_embed_profile'}"
    typed_top = []
    for item in top:
        _idx = item.get("chunk_idx")
        typed_top.append({
            "source_type": "selective_history",
            "source_id": f"ch{item['ch']:03d}#{_idx if _idx is not None else '?'}",
            "similarity": item["similarity"],
            "match_method": _match_method,
            "recency_distance": chapter - item["ch"],
            "ch": item["ch"],
            "chunk_idx": _idx,
            "text_preview": item["text_preview"],
        })
    # A6-2/3（2026-07-08·检索三段式）：块距防复读标签 + 用途启发式分类 → 每条 usage_hint
    # （advisory·rag_retriever 单一实现·cluster 反查失败时只打用途分类不臆造块距）。
    try:
        import rag_retriever as _rag_a6h
        _rag_a6h.annotate_usage_hints(scanner.root, chapter, typed_top)
    except Exception:
        pass
    _actual_chars = sum(len(str(t["text_preview"])) for t in typed_top)
    return {
        "_note": "按本章 turning_point + threads_advance（+A6 brief 实体×属性扩展词组）作 query 做语义检索，找前 N 章语义相近的片段。比固定 recent 摘要更智能。"
                 "每条 usage_hint=块距防复读标签（NEAR_ECHO_RISK/PARAPHRASE/OK）+用途分类（advisory）。"
                 "anti_copy=reference-not-copy：检索片段只作前情事实/连贯性参照，禁止照抄近邻正文原句进本章（advisory）。",
        "source_type": "selective_history",
        "match_method": _match_method,
        "query": query[:100],
        "retrieved": typed_top,
        "total_candidates": len(candidates),
        "token_budget": {
            "budget_chars": len(typed_top) * 80,
            "actual_chars": _actual_chars,
            "truncated": False,
            "_note": "本段无段级截断（truncated 恒 false）；80=embedding_store 索引构建期 "
                     "text_preview 既有上限（上游行为显式化·非本段新增截断）。query 回显截前 "
                     "100 字仅为显示，检索用完整 query。",
        },
        "anti_copy": "reference-not-copy",
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


def _collect_active_relationships(scanner, active_chars: list[str], current_cluster_id=None) -> list[dict]:
    """v19.2: 收集本章出场角色之间的关系数值。
    🔴 2026-06-28 写手信息隔离（item 2）：① 先修 note/notes 字段名 bug——producer 两种字段名都见过，
    旧代码只读 `notes` → 半数关系 note 丢失；改 surface_note/note/notes 三名都认。② 经 _sanitize_relationship
    剥未到 reveal_cluster 的 hidden_intent + 未到 hidden_note_reveal_cluster 的 hidden_note。明面 type/surface_note 留。"""
    if not active_chars:
        return []
    rel_path = scanner.root / "_数据库" / "关系.json"
    if not rel_path.exists():
        return []
    try:
        data = json.loads(rel_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if current_cluster_id is None:
        try:
            current_cluster_id = cluster_lookup.ch_to_cluster_id(scanner.root, getattr(scanner, "ch", None))
        except Exception:
            current_cluster_id = None
    active_set = set(active_chars)
    out = []
    for r in data.get("relationships", []):
        f, t = r.get("from"), r.get("to")
        if f in active_set or t in active_set:
            rs = _sanitize_relationship(r, current_cluster_id)
            # note/notes 字段名 bug 修复：surface_note > note > notes 三名都认（明面 note·always 留）
            surface = (rs.get("surface_note") or rs.get("note") or rs.get("notes") or "")[:60]
            item = {
                "from": f, "to": t, "type": rs.get("type"),
                "affinity": rs.get("affinity", 0),
                "trust": rs.get("trust", 0),
                "fear": rs.get("fear", 0),
                "respect": rs.get("respect", 0),
                "surface_note": surface,
                "notes": surface,  # 向后兼容旧消费方键名
            }
            if "hidden_note" in rs:  # 仅到 hidden_note_reveal_cluster 时保留
                item["hidden_note"] = (rs["hidden_note"] or "")[:60]
            if "hidden_intent" in rs:
                item["hidden_intent"] = rs["hidden_intent"]
            if rs.get("reveal_directive"):
                item["reveal_directive"] = rs["reveal_directive"]
            out.append(item)
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


def _parse_payoff_deadline(window: str, raised_num: int) -> int:
    """从 expected_payoff_window('N-M cluster' / 'N cluster' / 'cluster_NNN') 解析兑现死线 cluster num。

    取窗口上界（最晚兑现点）；无法解析 → 返回一个大数（视为不紧迫）。
    'N-M cluster' = 相对 raised 的偏移窗口 → deadline = raised_num + M。
    'cluster_NNN' = 绝对 cluster → deadline = NNN。
    """
    if not window:
        return 10**6
    w = str(window).strip()
    abs_m = re.search(r"cluster[_\-]?0*(\d+)", w)
    if abs_m:
        try:
            return int(abs_m.group(1))
        except ValueError:
            return 10**6
    nums = [int(x) for x in re.findall(r"\d+", w)]
    if not nums:
        return 10**6
    return raised_num + max(nums)


def _collect_open_dramatic_questions(scanner, current_cluster_id) -> dict | None:
    """🔴 2026-06-29 戏剧问题账本(PITQ/MDQ)：当前悬而未决的核心问题软注入 writer manifest（advisory）。

    读 _数据库/戏剧问题账本.json{clusters:{<cid>:{raised:[{qid,question,scope,raised_at_scene,
    expected_payoff_window}],answered:[{qid,answered_at_scene}]}}}·累计到 current_cluster（含）算
    open_questions = raised(qid) − answered(qid)·按兑现紧迫度（expected_payoff_window 死线 + staleness）
    排序 → 软提示 writer『当前悬而未决的核心问题:X(读者想知道答案)·本块可推进/部分揭示』。

    理论 Cambridge 2026 PITQ / McKee MDQ / Loewenstein 信息缺口·复用 bremond next_recommended →
    brief 注入成熟通道范式。**默认安全**：无账本 / 无 current_cluster_id / 无 open → None（不注入·
    零行为变化·向后兼容）。全 advisory·绝不 hard_gate（北极星⑤·慢热文学可少钩·作者档第一权威）。
    """
    if not current_cluster_id:
        return None
    cur_num = cluster_lookup.cluster_num(current_cluster_id)
    if cur_num is None:
        return None
    ledger_path = scanner.root / "_数据库" / "戏剧问题账本.json"
    if not ledger_path.exists():
        return None
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    clusters = ledger.get("clusters") if isinstance(ledger, dict) else None
    if not isinstance(clusters, dict):
        return None

    raised_first: dict = {}   # qid -> {raised_at_num, question, scope, expected_payoff_window}
    answered_qids: set = set()
    for cid, payload in clusters.items():
        cnum = cluster_lookup.cluster_num(cid)
        if cnum is None or cnum > cur_num or not isinstance(payload, dict):
            continue
        for r in payload.get("raised") or []:
            if not isinstance(r, dict):
                continue
            qid = r.get("qid")
            if not qid:
                continue
            qid = str(qid)
            if qid not in raised_first or cnum < raised_first[qid]["raised_at_num"]:
                gt = r.get("gap_type")
                raised_first[qid] = {
                    "raised_at_num": cnum,
                    "question": str(r.get("question") or ""),
                    "scope": str(r.get("scope") or "cluster"),
                    "expected_payoff_window": str(r.get("expected_payoff_window") or ""),
                    # 🔴 2026-06-29 Sternberg 三态：只认合法值·非法/缺省 → None（默认安全）
                    "gap_type": gt if gt in ("suspense", "curiosity", "surprise") else None,
                }
        for a in payload.get("answered") or []:
            if isinstance(a, dict) and a.get("qid"):
                answered_qids.add(str(a["qid"]))

    open_qs = []
    for qid, info in raised_first.items():
        if qid in answered_qids:
            continue
        deadline = _parse_payoff_deadline(info["expected_payoff_window"], info["raised_at_num"])
        open_qs.append({
            "qid": qid,
            "question": info["question"],
            "scope": info["scope"],
            "raised_at_cluster": f"cluster_{info['raised_at_num']:03d}",
            "expected_payoff_window": info["expected_payoff_window"],
            "gap_type": info.get("gap_type"),
            "staleness": cur_num - info["raised_at_num"],
            "_deadline": deadline,
        })
    if not open_qs:
        return None  # 默认安全：无 open question → 不注入

    # 紧迫度排序：兑现死线近者优先；同死线按 staleness 大者优先
    open_qs.sort(key=lambda q: (q["_deadline"], -q["staleness"]))
    top = open_qs[:5]
    for q in top:
        q.pop("_deadline", None)

    # 🔴 2026-06-29 Sternberg 读者知识缺口三态分布（统计全部 open 问题·缺 gap_type 不计·默认安全）。
    gap_type_distribution: dict = {}
    for q in open_qs:
        gt = q.get("gap_type")
        if gt in ("suspense", "curiosity", "surprise"):
            gap_type_distribution[gt] = gap_type_distribution.get(gt, 0) + 1
    typed_total = sum(gap_type_distribution.values())
    gap_note = ""
    # 仅当 ≥3 个带 gap_type 的 open 问题且全用一种缺口 → 软提示三态混合（绝不替 writer 选类型·北极星⑤）。
    if typed_total >= 3 and len(gap_type_distribution) == 1:
        only_type = next(iter(gap_type_distribution))
        gap_note = (f"（当前悬念全是『{only_type}』型知识缺口·读者张力维度单一·"
                    f"可顺势搭配另一种缺口让追读更立体：suspense 拉未来/curiosity 钩过去/surprise 骤然揭示·"
                    f"这是软提示·作者档第一权威）")

    primary = top[0]["question"][:50] or top[0]["qid"]
    return {
        "_doc": ("🔴 2026-06-29 戏剧问题账本 PITQ/MDQ·当前悬而未决核心问题软注入（advisory·绝不 hard_gate）。"
                 "读者追读 = 想知道答案（Cambridge 2026 PITQ / McKee MDQ / Loewenstein 信息缺口）。"
                 "gap_type_distribution = Sternberg 读者知识缺口三态分布（suspense 未来未披露/"
                 "curiosity 过去未解/surprise 未预期揭示·三态混合=张力工具·只软提示不替 writer 选）。"),
        "gate_level": "advisory",
        "current_cluster_id": current_cluster_id,
        "open_count": len(open_qs),
        "open_questions": top,
        "gap_type_distribution": gap_type_distribution,
        "directive": (
            f"当前悬而未决的核心问题（读者想知道答案）：{primary}。"
            f"本 cluster 可推进 / 部分揭示这些问题（哪怕给一点新线索），维持追读拉力；"
            f"也可适度收束 1 个旧问题再开新坑（避免只开不闭的 Zeigarnik 反面）。{gap_note}"
            f"慢热/严肃文学可少钩·作者档第一权威·这是软提示非硬约束。"),
    }


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
    """组装 hard_constraints 列表：剧情约束(Tier-1回收/secrets/locked_facts·真硬约束) +
    风格量化约束(对话占比/句长/字数/逗句比/TTR·全标 advisory·作者档第一权威·可校准偏离·
    北极星⑤)。2026-06-15 审计修(Workflow confirmed)：风格量化项原无 advisory 标记被
    critical_summary 框「必满足」=违北极星⑤把 advisory 当 hard_gate；对话占比 0.3 地板覆盖
    低对话作者基线=违北极星⑤(c)机械覆盖作者档；freestyle_v27 仍注入每章字数=违北极星④。
    全标 advisory 前缀让 writer 区分(critical_summary 只框 hard_gate 项)·对话地板改 relax-only。"""
    hard_constraints = [
        f"Tier-1 伏笔 {foreshadow_summary['tier1_due_count']} 条本章必须回收",
        f"secrets 本章必须揭露 {foreshadow_summary['must_reveal_this_ch']} 条",
        "locked_facts 零容忍（读人物卡.json 时请完整保留）",
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
            hard_constraints.append(f"（advisory·作者档第一权威·可校准偏离）对话占比 ≥ {max(0.0, dr_m - 0.15):.0%}")
        # #2轮穷尽核查纠偏：dialogue_ratio 含引号化内心独白系统性高估(真机惊悚 1000 章 0.32 vs 纯对话 0.21)
        # → 补纯对话占比 advisory 旁注(保留上方 dr 注入向后兼容·此为真实角色对白基线·防 writer 被高估值误导多写对话)
        dor_m = _num(quant.get("dialogue_only_ratio", {}).get("mean"))
        if dor_m is not None:
            hard_constraints.append(f"（advisory·纯对话占比·剔除引号化独白·作者真实口语基线）纯角色对白占比≈{dor_m:.0%}"
                                    f"（上方『对话占比』含引号化内心独白偏高·此为真实角色对白占比·二者并存参考）")
        sl = quant.get("sentence_length", {})
        sl_m = _num(sl.get("mean"))
        if sl_m is not None:
            sl_std = _num(sl.get("std"))
            sl_std = sl_std if sl_std is not None else 8
            hard_constraints.append(f"（advisory·作者档第一权威·可校准偏离）句长均值目标 {sl_m:.0f} 字（std ≥ {max(5, sl_std - 3):.0f}）")
        cw_m = _num(quant.get("chapter_words", {}).get("mean"))
        if cw_m is not None:
            low = max(2000, int(cw_m) - 500)
            high = int(cw_m) + 500
            hard_constraints.append(f"（advisory·freestyle_v27 由 splitter 按字数切·仅参考）章节字数 {low}-{high}")
        punc = quant.get("punctuation_density_per_1000", {})
        cpr_raw = punc.get("comma_period_ratio")
        cpr = _num(cpr_raw.get("mean") if isinstance(cpr_raw, dict) else cpr_raw)
        if cpr is not None and cpr > 1.0:
            hard_constraints.append(f"（advisory·作者档第一权威·可校准偏离）逗句比 ≥ {max(1.0, cpr - 1.0):.1f}:1（长句用逗号连接）")

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
              f"(source={fp.get('source')}, n={fp.get('n_chapters')}) — 不注入 manifest")
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
        print(f"[SHADOW] author_rhythm_signature: {len(directives)} 条节奏指令 — 不注入 manifest")
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
        print(f"[SHADOW] knowledge_gap_signature: {len(directives)} 条信息差指令 — 不注入 manifest")
        return None
    return payload


def _collect_narrative_function_sequence(s: "DatabaseScanner") -> dict | None:
    """#4（2026-06-16 穷尽核查）：作者签名因果功能链注入（env NARR_FUNC_SEQ_INJECT_MODE 默认 shadow·仿 D3·advisory）。

    读 consolidate 聚合的 narrative_function_sequence（signature_bigrams 因果功能链如 face_slap→gain_reward·
    比 Save-the-Cat 通用节拍更深·作者专属·distill-style.md 自述中文网文同质化结构层根因）→ writer 结构骨指令：
    让连续故事块的功能转移贴近作者签名节奏·而非默认 LLM 高频模板。默认 shadow（结构骨注入效果需 gen-model
    A/B + 标注一致性·先影子·区别于 rhythm 的 active）·off 零回归·active 注入。confidence<8 章不稳仅参考。
    """
    import os as _os
    mode = (_os.environ.get("NARR_FUNC_SEQ_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"
    if not s.has_style_profile():
        return None
    try:
        profile = s.load("作者风格", {})
        nfs = profile.get("narrative_function_sequence") if isinstance(profile, dict) else None
    except Exception as e:
        print(f"[WARN] narrative_function_sequence 读取失败: {e}", file=sys.stderr)
        return None
    if not isinstance(nfs, dict) or not nfs:
        return None
    bigrams = nfs.get("signature_bigrams") or []
    top = [b.get("seq") for b in bigrams[:3] if isinstance(b, dict) and b.get("seq")]
    if not top:
        return None
    conf = nfs.get("confidence", "low")
    directives = [
        f"作者签名叙事功能链（结构骨·confidence={conf}）：" + "、".join(top)
        + "（连续故事块的功能转移向作者签名节奏看齐·非默认 LLM 高频模板·中文网文同质化结构层根因·"
        "advisory 软提示不限定写什么内容）"
    ]
    payload = {"gate_level": "advisory", "advisory_only": True,
               "directives": directives, "raw": nfs,
               "_doc": "作者签名因果功能链（叙事功能序列·结构骨·advisory·作者档第一权威·confidence<8 章不稳仅参考）"}
    try:
        out_path = s.db / ".narr_func_seq" / f"ch_{s.ch:03d}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if mode == "shadow":
        print(f"[SHADOW] narrative_function_sequence: {len(directives)} 条结构骨指令 — 不注入 manifest")
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
        print(f"[SHADOW] genre_pack({genre}): {len(directives)} 条题材工艺 — 不注入 manifest")
        return None
    # 2026-06-19：从本地知识库补充题材相关知识（随蒸馏/调研自动积累）
    try:
        import knowledge_store as _ks
        kb_items = _ks.query_for_genre(genre, limit=5)
        if kb_items:
            payload["knowledge_base_context"] = [item["content"] for item in kb_items]
            payload["_kb_count"] = len(kb_items)
    except Exception:
        pass  # 知识库不存在或异常不影响主流程
    return payload


def _collect_emotion_body_topography_hint(s: "DatabaseScanner") -> dict | None:
    """R7 W2 P1：Nummenmaa 情绪身体地图 topography hint 注入（PNAS 2014 Bodily Maps of Emotions）。

    给 writer 一个『情绪标签 → 身体部位高强度激活区』小词典（8 基本情绪 + 6 复杂情绪），
    让生理线索描写有据可循（避免全堆面部表情·配合 physio_cue_diversity_scanner facial_bias 检测闭环）。

    北极星②让位作者档：作者档若规定 physio_cue 部位偏好（quantitative.physio_cue_distribution
    或 narrative_craft.physio_preference）则不注入或仅当 fallback。

    env EMOTION_TOPOGRAPHY_INJECT_MODE 默认 shadow（北极星⑥：先影子·待 gen-model 草稿验证再放量）。
    advisory · 永不 hard_gate。
    """
    import os as _os
    mode = (_os.environ.get("EMOTION_TOPOGRAPHY_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"

    # 作者档优先豁免：作者档若已规定 physio 部位偏好，则不注入（让位作者档·北极星②）
    authority_override = False
    if s.has_style_profile():
        try:
            sd = s.load("作者风格", {})
            quant = sd.get("quantitative") if isinstance(sd, dict) else None
            nc = sd.get("narrative_craft") if isinstance(sd, dict) else None
            if isinstance(quant, dict) and isinstance(quant.get("physio_cue_distribution"), dict):
                authority_override = True
            elif isinstance(nc, dict) and nc.get("physio_preference"):
                authority_override = True
        except Exception:
            pass

    # PNAS 2014 Nummenmaa 表 1 抽取 · 高强度激活区简化（每情绪 2-4 个核心部位）
    topography = {
        # 8 基本情绪
        "anger":     ["头部/额头充血", "胸口紧绷", "双手握拳/手臂"],
        "fear":      ["胸口紧缩", "胃部收紧", "四肢冷却"],
        "disgust":   ["喉部反胃", "嘴角下沉/上腹"],
        "happiness": ["全身轻盈", "胸口暖意发散", "脸颊"],
        "sadness":   ["胸口沉重", "喉部哽塞", "四肢无力"],
        "surprise":  ["头部/眼部张大", "胸口短促"],
        "neutral":   ["体感弱"],
        "anxiety":   ["胸口紧绷", "胃部翻搅", "双手发凉/出汗"],
        # 6 复杂情绪
        "love":      ["全身暖意", "胸口扩张", "脸颊"],
        "depression":["全身沉降", "胸口塌陷", "四肢冰冷"],
        "contempt":  ["上半身收紧", "脸部轻微"],
        "pride":     ["胸口挺起", "头部上抬"],
        "shame":     ["全身收缩", "脸部发烫", "胸口闷"],
        "envy":      ["胸口紧绷", "胃部"],
        "guilt":     ["胸口闷", "胃部下沉"],
    }

    payload = {
        "gate_level": "advisory",
        "advisory_only": True,
        "_source": "Nummenmaa et al. PNAS 2014 Bodily Maps of Emotions",
        "_authority": "FALLBACK" if authority_override else "GENERAL",
        "topography_lookup": topography,
        "_doc": (
            "Nummenmaa 情绪身体地图（PNAS 2014）：14 情绪 → 身体高强度激活部位。"
            "writer 写生理线索时按本表选部位（避免全堆面部表情·配合 physio_cue_diversity 检测闭环）。"
            "作者档若规定 physio 部位偏好则以作者档为准（_authority=FALLBACK 时让位）。"
        ),
    }
    if authority_override:
        payload["_note"] = "作者档已规定 physio 部位偏好 → 本字段降为 fallback 兜底"
    if mode == "shadow":
        print(f"[SHADOW] emotion_body_topography: {len(topography)} 情绪 — 不注入 manifest", file=sys.stderr)
        return None
    return payload


def _collect_focalization_matrix(s: "DatabaseScanner", chapter: int) -> dict | None:
    """R7 W2 P1：Focalization Type×Facet 二轴矩阵注入（arxiv 2604.14456 FocalLens 2026 /
    Bal/Rimmon-Kenan Living Handbook of Narratology）。

    Type 轴=四类聚焦人（零聚焦/内聚焦/外聚焦/可变聚焦）；Facet 轴=三 facet
    （perceptual 感知 / psychological 心理 / ideological 意识形态-价值评判）。
    writer D4 advisory：同焦点 facet 可解耦，但 facet 切换需有意为之（不要随手在感知/价值评判间无意识切换）。

    本字段不依赖 cluster brief 字段（不强制 outline 阶段就写明 facet）—— 只注入一个
    标准矩阵 + advisory 提示。后续可由 cluster brief 的 `focalization_facet` 字段精细覆盖。

    env FOCALIZATION_INJECT_MODE 默认 shadow（北极星⑥：先影子·待 gen-model 草稿验证再放量）。
    advisory · 永不 hard_gate。
    """
    import os as _os
    mode = (_os.environ.get("FOCALIZATION_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"

    matrix = {
        "types": {
            "zero":     "零聚焦：叙事者无所不知，可进入任意角色意识",
            "internal": "内聚焦：固定贴某角色，只能呈现该角色的感知/心理（POV 主角常态）",
            "external": "外聚焦：摄影机视角，只呈现外部可见行为，禁入意识",
            "variable": "可变聚焦：在多角色间切换内聚焦（须有明显 transition 标记）",
        },
        "facets": {
            "perceptual":    "感知 facet：聚焦人看见/听见/嗅到/触到的世界",
            "psychological": "心理 facet：聚焦人的内心活动/情绪/思考",
            "ideological":   "意识形态 facet：聚焦人的价值评判/道德立场（隐式贯穿叙述声音）",
        },
        "advisory": (
            "Type×Facet 二轴可独立组合：例如 internal+perceptual 时贴角色看，"
            "internal+psychological 时贴角色想，internal+ideological 时贴角色评判。"
            "同焦点的不同 facet 可解耦（看到的≠想的≠评判的），但 facet 切换需有意为之（不要无意识跳）。"
        ),
    }

    payload = {
        "gate_level": "advisory",
        "advisory_only": True,
        "_source": "arxiv 2604.14456 FocalLens 2026 + Bal/Rimmon-Kenan Living Handbook of Narratology",
        "matrix": matrix,
        "_doc": (
            "Focalization Type×Facet 二轴矩阵：聚焦类型(zero/internal/external/variable) "
            "× 三 facet(perceptual/psychological/ideological)。"
            "advisory · writer D4 提示·作者档若规定聚焦偏好则以作者档为准。"
        ),
    }
    if mode == "shadow":
        print(f"[SHADOW] focalization_matrix: 4 types × 3 facets — 不注入 manifest", file=sys.stderr)
        return None
    return payload


def _collect_debt_ledger_snapshot(s: "DatabaseScanner") -> dict | None:
    """R7 Batch-D（2026-06-20）：叙事债务账本 snapshot 注入。

    数据源=cluster-save-state step 9 跑 cross_cluster_narrative_debt_ledger_aggregate 写的
    `_数据库/.cross_chapter_scan/narrative_debt_snapshot.json`（book/volume open_debt + advisory_codes）。

    env NARRATIVE_DEBT_INJECT_MODE 默认 shadow（北极星⑥：先影子）。
    advisory · 永不 hard_gate · 缺文件 → None（零回归）。
    """
    import os as _os
    mode = (_os.environ.get("NARRATIVE_DEBT_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"
    snap_path = s.db / ".cross_chapter_scan" / "narrative_debt_snapshot.json"
    if not snap_path.exists():
        return None
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(snap, dict):
        return None
    payload = {
        "gate_level": "advisory",
        "advisory_only": True,
        "_doc": ("叙事债务账本（book/volume open_debt + flow）·建议 writer D7 偿还设计点·"
                 "advisory · cluster-save-state step 9 写"),
        "book": snap.get("book"),
        "volumes": snap.get("volumes"),
        "advisory_codes": snap.get("advisory_codes", []),
    }
    if mode == "shadow":
        print(f"[SHADOW] debt_ledger_snapshot: open_debt={payload.get('book', {}).get('open_debt')} — 不注入 manifest",
              file=sys.stderr)
        return None
    return payload


def _collect_sagging_middle_snapshot(s: "DatabaseScanner") -> dict | None:
    """R7 Batch-D（2026-06-20）：Sagging Middle snapshot 注入（needs_midpoint_bomb）。

    数据源=cluster-save-state step 9 跑 cross_cluster_sagging_middle_aggregate 写的
    `_数据库/.cross_chapter_scan/sagging_middle_snapshot.json`。

    env SAGGING_MIDDLE_INJECT_MODE 默认 shadow。advisory · 永不 hard_gate。
    """
    import os as _os
    mode = (_os.environ.get("SAGGING_MIDDLE_INJECT_MODE") or "shadow").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "shadow"
    snap_path = s.db / ".cross_chapter_scan" / "sagging_middle_snapshot.json"
    if not snap_path.exists():
        return None
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(snap, dict):
        return None
    payload = {
        "gate_level": "advisory",
        "advisory_only": True,
        "_doc": ("Sagging Middle 中段塌陷 snapshot·needs_midpoint_bomb=True 建议下个 cluster 插入"
                 "重大反转/角色死亡/秘密揭露·advisory·cluster-save-state step 9 写"),
        "needs_midpoint_bomb": bool(snap.get("needs_midpoint_bomb", False)),
        "hit_signals": snap.get("hit_signals", 0),
        "middle_cluster_ids": snap.get("middle_cluster_ids", []),
        "advisory_codes": snap.get("advisory_codes", []),
    }
    if mode == "shadow":
        print(f"[SHADOW] sagging_middle_snapshot: needs_bomb={payload['needs_midpoint_bomb']} "
              f"hit_signals={payload['hit_signals']} — 不注入 manifest", file=sys.stderr)
        return None
    return payload


# 🔴 2026-06-27 P1-08：注入 motif advisory snapshot（payoff_due / dormant / over_saturated 三态各 top-3）。
# 数据源=motif_recurrence_ledger 在 _数据库/.cross_chapter_scan/motif_advisory_snapshot.json 落盘。
# advisory · 永不 hard_gate · 缺文件 → None（零回归 · 北极星⑤顾问非法官）。
def _collect_motif_advisory(s: "DatabaseScanner") -> dict | None:
    import os as _os
    mode = (_os.environ.get("MOTIF_ADVISORY_INJECT_MODE") or "active").strip().lower()
    if mode == "off":
        return None
    if mode not in ("shadow", "active"):
        mode = "active"
    snap_path = s.db / ".cross_chapter_scan" / "motif_advisory_snapshot.json"
    if not snap_path.exists():
        return None
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(snap, dict):
        return None
    # 兼容 ledger snapshot 三态字段（直接来自 motif_recurrence_ledger 的 sort 已截 top-10）。
    payoff_due_raw = snap.get("payoff_due_motifs") or snap.get("recurring_unchanged_motifs") or []
    dormant_raw = snap.get("dormant_motifs") or []
    saturated_raw = snap.get("over_saturated_motifs") or []
    def _slim(items, n=3):
        out = []
        for it in items[:n]:
            if isinstance(it, dict):
                out.append({k: it.get(k) for k in ("term", "category", "last_cluster", "total")
                            if it.get(k) is not None})
        return out
    payload = {
        "gate_level": "advisory",
        "advisory_only": True,
        "_doc": ("motif advisory snapshot · payoff_due/dormant/over_saturated 三态各 top-3·"
                 "writer 优先回收 dormant + 节制 over_saturated · 不进 hard_gate（顾问非法官）"),
        "payoff_due": _slim(payoff_due_raw),
        "dormant": _slim(dormant_raw),
        "over_saturated": _slim(saturated_raw),
        "n_clusters": snap.get("n_clusters"),
        "advisory_codes": snap.get("advisory_codes", []),
    }
    if mode == "shadow":
        try:
            print(f"[SHADOW] motif_advisory: dormant={len(payload['dormant'])} "
                  f"over_sat={len(payload['over_saturated'])} — 不注入 manifest",
                  file=sys.stderr)
        except Exception:
            pass
        return None
    return payload


def _collect_volume_summaries_digest(s: "DatabaseScanner") -> list | None:
    """S10 消费端（2026-07-07·Ex3 摘要金字塔）：已闭合卷的卷级摘要内联注入（T3 长程记忆）。

    数据源=故事块摘要.volume_summaries[]（生产端 save_state --apply-volume-summary·apply 硬校验
    保证只有真闭合卷入账，故这里无需判当前卷——有条目即历史卷）。金字塔语义：writer 对历史卷
    读这一条 300-500 字卷摘要（换粒度），对当前卷仍走逐 cluster 摘要/manifest 既有通道（细粒度）。
    volume_summaries 缺失/空 = 老项目常态 → 返回 None 键不注入（零行为变化）。
    """
    doc = s.load("故事块摘要", {})
    vols = doc.get("volume_summaries")
    if not isinstance(vols, list) or not vols:
        return None
    out = []
    for v in vols:
        if not isinstance(v, dict) or not v.get("summary"):
            continue
        src = v.get("source") or []
        out.append({
            "volume": v.get("volume"),
            "summary": v.get("summary"),
            "clusters": f"{src[0]}~{src[-1]}" if src else "",
        })
    return out or None


# ============ A3 前块结尾偏重注入 + A14 近期活跃实体 LRU（2026-07-07 二轮移植 A 批） ============

_PREV_TAIL_CJK_CHAR_RE = re.compile(r"[一-鿿㐀-䶿]")  # 与 chapter_io.count_cjk 同字符类


def _prev_tail_target_cjk() -> int:
    """A3 末尾截取目标 CJK 字数（默认 800·env PREV_TAIL_CJK 可调·非法值回默认）。"""
    raw = (os.environ.get("PREV_TAIL_CJK") or "").strip()
    try:
        v = int(raw) if raw else 800
    except ValueError:
        v = 800
    return max(v, 1)


def _tail_by_cjk(text: str, target_cjk: int) -> str:
    """取 text 末尾约 target_cjk 个 CJK 字的原文（只多不少·对齐到段首防半句起头）。

    从末尾向前数 CJK 字到 target 后，再向前对齐到最近换行（整段保留——PlotPilot
    「章末完整保留提升连贯」的段落粒度实现）。全文不足 target → 全量返回。
    """
    cnt = 0
    pos = 0
    for i in range(len(text) - 1, -1, -1):
        if _PREV_TAIL_CJK_CHAR_RE.match(text[i]):
            cnt += 1
            if cnt >= target_cjk:
                pos = i
                break
    if cnt < target_cjk:
        return text.strip()
    nl = text.rfind("\n", 0, pos)
    return text[nl + 1:].strip() if nl != -1 else text.strip()


def _resolve_prev_cluster_id(s: "DatabaseScanner", current_cluster_id) -> str | None:
    """cluster_lookup 权威反查前块 cluster_id（禁机械拼接·北极星①）。

    ① 主路径：当前 cluster 章区间起点的前一章 → ch_to_cluster_id 权威归属
      （cluster_lookup 双向 roundtrip·容忍编号不连续/重排）。
    ② 兜底：事件簇.json.clusters 列表序取当前项的前一项（fluid 涌现顺序权威·
      新 cluster 尚未回填 chapter_range 时 range 反查必空的常态路径）。
    cluster_001 / 解析失败 → None（无前块·键不注入）。
    """
    cur_norm = cluster_lookup.normalize_cluster_id(current_cluster_id)
    if cur_norm is None:
        return None
    if (cluster_lookup.cluster_num(cur_norm) or 0) <= 1:
        return None  # cluster_001 无前块
    prev = None
    rng = cluster_lookup.cluster_id_to_range(s.root, cur_norm)
    if (isinstance(rng, (list, tuple)) and len(rng) == 2
            and isinstance(rng[0], int) and rng[0] > 1):
        prev = cluster_lookup.ch_to_cluster_id(s.root, rng[0] - 1)
    if not prev:
        clusters = (s.load("事件簇", {}) or {}).get("clusters") or []
        for i, c in enumerate(clusters):
            if (isinstance(c, dict)
                    and cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == cur_norm):
                if i > 0 and isinstance(clusters[i - 1], dict):
                    prev = cluster_lookup.normalize_cluster_id(clusters[i - 1].get("cluster_id"))
                break
    prev = cluster_lookup.normalize_cluster_id(prev)
    if prev is None or prev == cur_norm:
        return None
    return prev


def _collect_prev_cluster_tail(s: "DatabaseScanner", current_cluster_id) -> dict | None:
    """A3 前块结尾偏重注入（2026-07-07·PlotPilot recent_chapter_context.py:7-73 移植）。

    cluster_002+ 时读上一 cluster 草稿（章节/cluster_<prev>_draft/cluster_<prev>_draft.txt）
    末尾 N 字原文（默认 800 CJK·env PREV_TAIL_CJK 可调）。PlotPilot 实证「章末完整保留
    提升连贯」——跨 cluster 断裂感的真实痛点在开篇丢失前块结尾的悬念钩子/情感余韵/场景状态。
    writer 侧回响指令见 gen_writer._build_prev_tail_echo_section（advisory·北极星⑤不硬锁）。
    cluster_001 无前块 / 前块草稿不存在（断点恢复异态）→ None（键不注入·零变化）。
    """
    prev_cid = _resolve_prev_cluster_id(s, current_cluster_id)
    if not prev_cid:
        return None
    draft_path = s.root / "章节" / f"{prev_cid}_draft" / f"{prev_cid}_draft.txt"
    if not draft_path.is_file():
        return None
    try:
        text = draft_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        return None
    tail = _tail_by_cjk(text, _prev_tail_target_cjk())
    if not tail:
        return None
    return {
        "_doc": ("A3 前块结尾偏重注入（PlotPilot recent_chapter_context 移植）：上一 cluster "
                 "草稿末尾原文完整保留——本块开篇应对其悬念钩子/情感余韵/场景状态有所回响"
                 "（advisory·回响方式 writer 自由决定·不必逐句衔接）。"),
        "source_cluster": prev_cid,
        "tail_text": tail,
        "tail_cjk": cio.count_cjk(tail),
    }


def _collect_recently_active_entities(s: "DatabaseScanner", active_chars,
                                      current_cluster_id, lookback: int = 3,
                                      cap: int = 15) -> dict | None:
    """A14 近期活跃实体 LRU 兜底（2026-07-07·Ex3 Entity_info Recent_Visit 移植）。

    从 故事块摘要.json 最近 lookback 个历史 cluster 条目**确定性**抽取出场实体简表
    （账本已有字段 characters/char_mention_counts/summary·零 LLM 调用）：每实体一行
    = 名字 + 最后出场 cluster + 一句话状态（最后出场章的账本摘要截断）。与 scene_storyboard
    白名单角色去重（白名单已注入全卡·不重复列）；上限 cap 行防膨胀（最近出场优先保留）。
    用途：freestyle 带出计划外配角时的前置防漂移参考（advisory·非出场名单硬锁——此前只能
    靠 UNKNOWN_CHARACTER 事后拦）。无数据 → None（键不注入·零变化）。
    """
    doc = s.load("故事块摘要", {}) or {}
    clusters = doc.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        return None
    cur_num = cluster_lookup.cluster_num(current_cluster_id) if current_cluster_id else None
    recs = []
    for c in clusters:
        if not isinstance(c, dict):
            continue
        n = cluster_lookup.cluster_num(c.get("cluster_id"))
        if n is None:
            continue
        if cur_num is not None and n >= cur_num:
            continue  # 只看当前块之前的历史条目
        recs.append((n, c))
    recs.sort(key=lambda t: t[0])
    recs = recs[-lookback:]
    if not recs:
        return None
    id2name = s._char_id_map()
    # 白名单 = 已注入全卡的不重复列：active_chars + 当前 cluster 全 storyboard 出场角色
    whitelist: set[str] = set()
    for nm in active_chars or []:
        if isinstance(nm, str) and nm.strip():
            whitelist.add(nm.strip())
    cur_cluster = s._event_cluster_by_id(current_cluster_id) if current_cluster_id else None
    if isinstance(cur_cluster, dict):
        for scene in cur_cluster.get("scene_storyboard") or []:
            if not isinstance(scene, dict):
                continue
            for nm in scene.get("characters") or []:
                if isinstance(nm, str) and nm.strip():
                    whitelist.add(nm.strip())
    whitelist |= {id2name[w] for w in list(whitelist) if w in id2name}

    def _ch_num(k) -> int:
        try:
            return int(k)
        except (TypeError, ValueError):
            return 0

    # LRU：按 (cluster, ch) 升序遍历·同名后出现覆盖 = 最后出场为准（确定性）
    seen: dict[str, dict] = {}
    for n, c in recs:
        cid = cluster_lookup.normalize_cluster_id(c.get("cluster_id"))
        chapters = c.get("chapters") if isinstance(c.get("chapters"), dict) else {}
        for ch_key, rec in sorted(chapters.items(), key=lambda kv: _ch_num(kv[0])):
            if not isinstance(rec, dict):
                continue
            names = rec.get("characters")
            if not isinstance(names, list) or not names:
                names = list((rec.get("char_mention_counts") or {}).keys())
            hint = (rec.get("summary") or "").strip().replace("\n", " ")
            if len(hint) > 60:
                hint = hint[:60] + "…"
            for nm in names:
                if not isinstance(nm, str) or not nm.strip():
                    continue
                canon = id2name.get(nm.strip(), nm.strip())
                entry = {"name": canon, "last_seen_cluster": cid,
                         "_rank": (n, _ch_num(ch_key))}
                if hint:
                    entry["status_hint"] = hint
                seen[canon] = entry
    rows = [v for k, v in seen.items() if k not in whitelist]
    if not rows:
        return None
    rows.sort(key=lambda r: r["_rank"], reverse=True)  # 最近出场优先保留
    rows = rows[:cap]
    for r in rows:
        r.pop("_rank", None)
    return {
        "_doc": (f"A14 近期活跃实体 LRU 兜底（Ex3 Recent_Visit 移植）：最近 {lookback} 个历史 "
                 "cluster 出场实体简表（摘要账本确定性抽取·零 LLM）。已与 storyboard 白名单"
                 "角色去重（那些已注入全卡）。计划外配角写前防漂移参考——若正文自然带出这些"
                 "实体，保持其最后已知状态一致（advisory·非出场名单硬锁）。"),
        "entities": rows,
    }


def _scene_gate_context(s: "DatabaseScanner", current_cluster_id) -> dict | None:
    """A11 DeepLore 多维门控上下文（2026-07-08·sillytavern-DeepLore 移植·
    research/open_source_writing_systems_round2.md A11）。

    当前 cluster brief 的 scene_storyboard + characters_focus + hub_locations 聚合出
    「本块出场角色集合 + 地点集合」（角色 id↔name 双形态都认）。聚合不到任何场景信息
    → None = 调用方不过滤零变化（保守闸：宁多注入不误删）。
    """
    cluster = s._event_cluster_by_id(current_cluster_id) if current_cluster_id else None
    if not isinstance(cluster, dict):
        return None
    chars: set[str] = set()
    locs: set[str] = set()

    def _add(pool: set, v):
        if isinstance(v, str) and v.strip():
            pool.add(v.strip())

    for v in cluster.get("characters_focus") or []:
        _add(chars, v)
    for sc in cluster.get("scene_storyboard") or []:
        if not isinstance(sc, dict):
            continue
        for v in sc.get("characters") or []:
            _add(chars, v)
        for v in sc.get("participants") or []:
            _add(chars, v)
        _add(chars, sc.get("focal_character"))
        _add(locs, sc.get("location"))
    for v in cluster.get("hub_locations") or []:
        _add(locs, v)
    id2name = s._char_id_map()
    chars |= {id2name[c] for c in list(chars) if c in id2name}
    if not chars and not locs:
        return None
    return {"characters": chars, "locations": locs}


def _scene_gate_world_hits(s: "DatabaseScanner", world_hits, current_cluster_id):
    """A11 DeepLore scene 维度门控过滤：世界观词条清单（world_keyword_hits）按「本块出场
    角色/地点」维度门控——词条明确点名了已知角色/地点、却与本块角色/地点零交集 → 不注入
    （确定性 substring 匹配·零 LLM）。34 子系统越写越厚时的注入密度解法。

    词条清单型注入段现状摸底（2026-07-08）：world_keyword_hits 是唯一未按场景门控的词条
    清单段（keyword 命中 plan 即注入·词条可点名与本块无关的角色/地点）；location_atmosphere
    天生已按 scene 候选地点三路匹配（_collect_location_atmosphere）；triggerable_events /
    relevant_items / relevant_relationships 已分别按 plan 关键词 / 出场角色门控——均无需二次门控。

    保守闸（零变化路径）：env MANIFEST_SCENE_GATING=off / 无场景信息（_scene_gate_context
    返回 None）/ 词条不点名任何已知实体（通用设定条目）→ 不过滤。
    只判有上下文的维度：本块无地点信息时不按地点裁、无角色信息时不按角色裁。
    返回 (kept_hits, gating_report|None)；report 只在真有过滤时产（META 元数据·可审计）。
    """
    mode = (os.environ.get("MANIFEST_SCENE_GATING") or "on").strip().lower()
    if mode in ("off", "0", "false") or not world_hits:
        return world_hits, None
    ctx = _scene_gate_context(s, current_cluster_id)
    if ctx is None:
        return world_hits, None
    universe_chars = {k for k in s._char_id_map() if isinstance(k, str) and len(k) >= 2}
    universe_locs: set[str] = set()
    map_data = s.load("地图", {}) or {}
    for v in (map_data.get("character_positions") or {}).values():
        if isinstance(v, str) and len(v.strip()) >= 2:
            universe_locs.add(v.strip())
    for loc in map_data.get("locations") or []:
        if isinstance(loc, str):
            nm = loc
        elif isinstance(loc, dict):
            nm = loc.get("name") or loc.get("id") or ""
        else:
            continue
        if isinstance(nm, str) and len(nm.strip()) >= 2:
            universe_locs.add(nm.strip())
    reg = s.load("location_atmosphere_registry", {}) or {}
    if isinstance(reg, dict):
        universe_locs |= {k.strip() for k in reg if isinstance(k, str) and len(k.strip()) >= 2}
    # 本块自己的角色/地点不当「无关证据」（词条点名本块实体=直接相关）
    kept, dropped = [], []
    for h in world_hits:
        blob = json.dumps(h, ensure_ascii=False)
        ref_chars = {n for n in universe_chars if n in blob}
        ref_locs = {n for n in universe_locs if n in blob}
        judged = False
        keep = False
        if ref_chars and ctx["characters"]:
            judged = True
            keep = bool(ref_chars & ctx["characters"])
        if not keep and ref_locs and ctx["locations"]:
            judged = True
            keep = any(rl == c or rl in c or c in rl
                       for rl in ref_locs for c in ctx["locations"])
        if judged and not keep:
            dropped.append(h.get("id"))
        else:
            kept.append(h)  # 未点名实体的通用词条 / 未判定维度 → 保守保留
    if not dropped:
        return world_hits, None
    report = {
        "_doc": ("A11 DeepLore scene 维度门控：世界观词条点名了已知角色/地点但与本块出场"
                 "角色/地点零交集 → 不注入（确定性匹配·env MANIFEST_SCENE_GATING=off 可关·"
                 "被滤词条以 世界观.json 原文件为准·META 审计元数据）。"),
        "gated_section": "world_keyword_hits",
        "dropped_entry_ids": dropped,
        "kept_count": len(kept),
        "scene_characters": sorted(ctx["characters"]),
        "scene_locations": sorted(ctx["locations"]),
    }
    return kept, report


def _collect_pre_write_gate_digest(s: "DatabaseScanner", current_cluster_id) -> dict | None:
    """A2 遗留清偿（2026-07-08·写前 Evolution Gate 报告摘要注入）。

    读 .wal/cluster_<key>_pre_write_gate.json 的 waived[]/warnings[]——有内容才注入。
    让 writer 看到「已声明豁免的叙事手法」上下文（死人以回忆/幻觉登场、毁物以残片再现等
    创作声明），写作时有意识把豁免当叙事手法落笔，而非当穿帮回避；warnings（重复事件嫌疑等）
    提示避免复写。manifest_budget 归 T0（契约类）。
    无报告 / waived+warnings 双空 → None（键不注入·零变化）。
    """
    if not current_cluster_id:
        return None
    path = s.db / ".wal" / f"{current_cluster_id}_pre_write_gate.json"
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(report, dict):
        return None

    def _slim(items, with_waiver: bool) -> list[dict]:
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            row = {"type": it.get("type"), "target": it.get("target"),
                   "detail": str(it.get("detail") or "")[:200]}
            if with_waiver:
                wb = it.get("waived_by") or {}
                if isinstance(wb, dict):
                    row["waiver_reason"] = str(wb.get("reason") or "")[:300]
            out.append(row)
        return out

    waived = _slim(report.get("waived"), True)
    warnings = _slim(report.get("warnings"), False)
    if not waived and not warnings:
        return None
    return {
        "_doc": ("A2 写前 Evolution Gate 摘要（pre_write_gate.py 产·完整报告见 "
                 f"_数据库/.wal/{current_cluster_id}_pre_write_gate.json）："
                 "waived=brief 已声明豁免放行的写前拦截项——writer 应把 waiver_reason 当"
                 "叙事手法有意识落笔（如亡者只以回忆/幻觉出场）；warnings=写前留痕注意项"
                 "（如与已完成事件高词面重叠·避免复写）。advisory·非 hard_gate。"),
        "cluster_id": report.get("cluster_id") or current_cluster_id,
        "verdict": report.get("verdict"),
        "waived": waived,
        "warnings": warnings,
    }


# ============ A4 编辑手记（2026-07-08·二轮移植·PlotPilot 结构槽坍缩为自然语言） ============
# 业界源 PlotPilot context_budget_allocator.py:475-489：「一段自然语言比 8 个 === 分隔符
# 更容易被 LLM 融入创作」。双视图纪律：原始结构块**全部保留**（scanner/审计仍消费结构化
# 数据），editor_note 只是给 writer 的人话视图。零 LLM 纯模板填充·同输入同字节（确定性）。

_EDITOR_NOTE_MIN_CHARS = 200
_EDITOR_NOTE_MAX_CHARS = 400

_EDITOR_NOTE_OPENING = "【编辑手记】开写前把案头的材料翻了一遍，几句闲话放在这里，供你顺手参考："

# 软措辞收尾池：首条恒在场（「不必强求”是 A4 的灵魂措辞）；其余按长度下限依序补足。
_EDITOR_NOTE_CLOSERS = (
    "以上都是软建议——本块如果顺手可以推进，如果合适可以呼应，不必强求，更不必逐条完成。",
    "要是这些线头和你正在写的场景相互别扭，以你的场景和作者风格档为准，手记让路。",
    "这份手记只是把散在各库里的线头拢到一起，省得你来回翻找；写作的判断权始终在你手里。",
    "祝本块写得顺，收尾时留个让人想追下去的钩子就更好了——当然，这也只是顺口一提。",
)


def _en_snip(text, limit: int) -> str:
    """手记素材片段清洗：折叠空白 + 定长截断（截断补省略号·确定性）。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t


def _editor_note_materials(manifest: dict) -> tuple[list[str], list[str]]:
    """从已组装 manifest 的结构块确定性提炼手记素材句（人话软措辞）。

    素材源（与结构块一一对应·块缺席/off/error/空 → 该句不产）：
      foreshadowing_summary（到期伏笔计数）/ open_dramatic_questions（悬置戏剧问题）/
      protagonist_stress（主角承压）/ main_character_arc_stage（弧线阶段）/
      world_state_snapshot（近期涟漪后果）/ event_cluster_context.volume_convergence_anchor（卷收敛锚）。
    返回 (sentences, sources)——两列表同序（sources 供审计追溯素材来源）。
    """
    sentences: list[str] = []
    sources: list[str] = []

    # ① 到期伏笔（foreshadowing_summary 计数——内容级契约仍走 hard_constraints，这里只是提醒）
    fs = manifest.get("foreshadowing_summary")
    if isinstance(fs, dict):
        parts = []
        t1 = fs.get("tier1_due_count")
        if isinstance(t1, int) and t1 > 0:
            parts.append(f"{t1} 处伏笔到了该收的窗口")
        rv = fs.get("must_reveal_this_ch")
        if isinstance(rv, int) and rv > 0:
            parts.append(f"{rv} 个秘密等着揭晓")
        dl = fs.get("deadlines_due")
        if isinstance(dl, int) and dl > 0:
            parts.append(f"{dl} 条期限正在逼近")
        if parts:
            sentences.append("翻了下伏笔账：" + "、".join(parts) +
                             "——如果剧情顺手可以兑现一两处，收不完也别硬塞。")
            sources.append("foreshadowing_summary")

    # ② 悬置戏剧问题（open_dramatic_questions 首问）
    odq = manifest.get("open_dramatic_questions")
    if isinstance(odq, dict):
        qs = odq.get("open_questions")
        q0 = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else None
        q_text = _en_snip((q0 or {}).get("question"), 40)
        if q_text:
            cnt = odq.get("open_count")
            extra = (f"（悬着的问题一共 {cnt} 个）"
                     if isinstance(cnt, int) and cnt > 1 else "")
            sentences.append(f"读者眼下最想知道的是「{q_text}」{extra}，"
                             "写到相关处如果能顺手给一点新线索就更好，不给也行。")
            sources.append("open_dramatic_questions")

    # ③ 主角承压（protagonist_stress·只在真高压时说一嘴，不替模型渲染）
    ps = manifest.get("protagonist_stress")
    if isinstance(ps, dict) and ps.get("mode") == "on" and ps.get("is_high_stress"):
        level = ps.get("stress_level")
        threshold = ps.get("stress_threshold_break")
        num = (f"（压力已到 {level}/{threshold}）"
               if isinstance(level, (int, float)) and isinstance(threshold, (int, float))
               else "")
        sentences.append(f"主角这会儿承压不轻{num}，写到位时让这份紧绷自然露出来一点就好，不用刻意渲染。")
        sources.append("protagonist_stress")

    # ④ 主角弧线阶段（main_character_arc_stage 首个主角）
    arc = manifest.get("main_character_arc_stage")
    if isinstance(arc, dict) and arc.get("mode") == "on":
        mcs = arc.get("main_characters")
        mc = mcs[0] if isinstance(mcs, list) and mcs and isinstance(mcs[0], dict) else None
        if mc:
            name = _en_snip(mc.get("character"), 12)
            stage = _en_snip(mc.get("current_stage_name") or mc.get("current_stage_id"), 20)
            if name and stage:
                desc = _en_snip(mc.get("stage_description"), 40)
                mid = f"（{desc}）" if desc else ""
                sentences.append(f"{name}的成长线正走到「{stage}」这一段{mid}，"
                                 "台词和选择贴着这个阶段来，自然就对。")
                sources.append("main_character_arc_stage")

    # ⑤ 近期涟漪后果（world_state_snapshot.ripple_narrative_consequences 最新一条）
    ws = manifest.get("world_state_snapshot")
    if isinstance(ws, dict) and ws.get("mode") not in (None, "off", "error"):
        ncs = ws.get("ripple_narrative_consequences")
        last = ncs[-1] if isinstance(ncs, list) and ncs and isinstance(ncs[-1], dict) else None
        nc_text = _en_snip((last or {}).get("text"), 50)
        if nc_text:
            sentences.append(f"世界那头还有前文的因果在发酵——{nc_text}。"
                             "如果合适可以让它的余波在本块露个影，不合适就先放着。")
            sources.append("world_state_snapshot")

    # ⑥ 卷收敛锚（event_cluster_context.volume_convergence_anchor·大势方向收尾）
    ecc = manifest.get("event_cluster_context")
    if isinstance(ecc, dict):
        anchor = ecc.get("volume_convergence_anchor")
        if isinstance(anchor, dict):
            direction = None
            for k in ("core_conflict", "volume_arc"):
                v = anchor.get(k)
                if isinstance(v, str) and v.strip():
                    direction = _en_snip(v, 40)
                    break
            if direction:
                sentences.append(f"最后提一句本卷的大方向：「{direction}」。"
                                 "小势怎么折腾随你，方向别丢就行。")
                sources.append("volume_convergence_anchor")

    return sentences, sources


def _build_editor_note(manifest: dict) -> dict | None:
    """A4 编辑手记：把分散结构槽确定性坍缩成一段 200-400 字自然语言手记（advisory）。

    · 双视图：只读 manifest 已组装结构块（不动原块）——scanner/审计消费结构块，
      writer 多得一份人话视图（PlotPilot「一段自然语言比 8 个分隔符更易融入创作」）。
    · 软措辞：「如果合适可以推进，不必强求」恒在场（北极星⑤·顾问非法官）。
    · 长度带 [200, 400]：不足 → 依序补软措辞收尾句；超出 → 从尾部整句裁素材
      （至少保 1 句·单句仍超带则带内硬裁）。
    · 素材全空 / 拼装异常 → None（键不注入·零变化）。零 LLM 纯模板填充·同输入同字节。
    """
    try:
        sentences, sources = _editor_note_materials(manifest)
    except Exception:
        return None  # 手记生成失败 → 键不注入（不阻断 manifest）
    if not sentences:
        return None  # 素材全空 → 键不注入
    kept = list(sentences)
    closers = [_EDITOR_NOTE_CLOSERS[0]]  # 「不必强求」软措辞恒在场

    def _compose() -> str:
        return _EDITOR_NOTE_OPENING + "".join(kept) + "".join(closers)

    note = _compose()
    # 超上限 → 从尾部整句裁素材（至少保 1 句）
    while len(note) > _EDITOR_NOTE_MAX_CHARS and len(kept) > 1:
        kept.pop()
        sources = sources[:len(kept)]
        note = _compose()
    if len(note) > _EDITOR_NOTE_MAX_CHARS:
        budget = _EDITOR_NOTE_MAX_CHARS - len(_EDITOR_NOTE_OPENING) - len(closers[0]) - 1
        kept = [kept[0][:max(budget, 1)].rstrip() + "…"]
        note = _compose()
    # 不足下限 → 依序补收尾句（收尾池设计上保证 ≥1 素材句时可达下限）
    for closer in _EDITOR_NOTE_CLOSERS[1:]:
        if len(note) >= _EDITOR_NOTE_MIN_CHARS:
            break
        closers.append(closer)
        note = _compose()
    return {
        "_doc": ("A4 编辑手记（PlotPilot 结构槽坍缩为自然语言·2026-07-08）：把到期伏笔/"
                 "悬置戏剧问题/主角压力与弧线阶段/近期涟漪后果/卷收敛锚等结构块，确定性模板"
                 "拼装成一段 200-400 字人话手记给 writer。双视图：原始结构块全部保留给 "
                 "scanner/审计，本段只是人话视图。全 advisory·软措辞·可自由取舍·绝不 hard_gate。"),
        "gate_level": "advisory",
        "note": note,
        "note_chars": len(note),
        "sources": sources,
    }


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


# 🔴 2026-06-27 P0-05：子系统消费层级 audit·遍历 KNOWN_DBS 列每件的 consumed_by/status，写入 manifest 末尾。
# 用途：让 writer/judge 一眼看清哪些子系统是 live direct_inject、哪些是 via_scanner/via_engine、
# 哪些是 deferred（如 webnovel_bench_mapping 等 G13 评估器未启用）。骨架 _doc.consumption 是
# 单一真理源（subsystem_skeletons.json）；本函数兼容缺 consumption（旧骨架）→ status=unknown。
def _collect_subsystem_consumption_audit(s: "DatabaseScanner") -> dict:
    try:
        from frozen_util import bundle_root as _bundle_root
        skel_path = _bundle_root() / "core" / "claude-home" / "templates" / "subsystem_skeletons.json"
    except Exception:
        skel_path = Path(__file__).resolve().parent.parent / "claude-home" / "templates" / "subsystem_skeletons.json"
    skeletons = {}
    try:
        if skel_path.is_file():
            sk = json.loads(skel_path.read_text(encoding="utf-8"))
            skeletons = sk.get("skeletons", {}) or {}
    except (OSError, json.JSONDecodeError):
        skeletons = {}
    audit = []
    for name in sorted(DatabaseScanner.KNOWN_DBS):
        skel = skeletons.get(name) or {}
        cons = skel.get("consumption") if isinstance(skel, dict) else None
        if isinstance(cons, dict):
            entry = {
                "name": name,
                "consumed_by": list(cons.get("by") or []),
                "layer": cons.get("layer") or "unknown",
                "status": cons.get("status") or "unknown",
            }
            if cons.get("reason"):
                entry["reason"] = cons["reason"]
        else:
            entry = {"name": name, "consumed_by": [], "layer": "unknown", "status": "unknown"}
        audit.append(entry)
    return {
        "_doc": ("子系统消费层级 audit·骨架 _doc.consumption 是单一真理源·"
                 "status: live=运行时消费 / deferred=保留待启用 / unknown=骨架未标注（应补）"),
        "subsystems": audit,
        "_summary": {
            "live": sum(1 for a in audit if a["status"] == "live"),
            "deferred": sum(1 for a in audit if a["status"] == "deferred"),
            "unknown": sum(1 for a in audit if a["status"] == "unknown"),
        },
    }


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
        # 解析每条规则节 → {file, desc}。兼容两种汇编形态：
        #   · 降噪后（当前·2026-06-18）：节头 "## feedback-<slug>" + 首条正文行作 desc
        #   · 旧 assemble 输出：可选 "<!-- FEEDBACK_RULE: <fname> -->" 锚 + "> description:" 行
        lines = gfr.read_text(encoding="utf-8").splitlines()
        digest = []
        pending_fname = None
        i = 0
        while i < len(lines):
            ln = lines[i]
            mk = re.match(r"<!--\s*FEEDBACK_RULE:\s*(\S+)\s*-->", ln.strip())
            if mk:
                pending_fname = mk.group(1)
                i += 1
                continue
            head = re.match(r"##\s+(feedback[-_][\w-]+)", ln.strip())
            if head:
                slug = head.group(1)
                fname = pending_fname or (slug.replace("-", "_") + ".md")
                pending_fname = None
                desc = ""
                j = i + 1
                while j < len(lines):
                    c = lines[j].strip()
                    if c.startswith("## ") or re.match(r"<!--\s*FEEDBACK_RULE:", c):
                        break
                    if c.startswith("> description:"):
                        desc = c[len("> description:"):].strip()
                        break
                    if c and c != "---" and not c.startswith("<!--"):
                        desc = c
                        break
                    j += 1
                if desc:
                    digest.append({"file": fname, "desc": desc[:160]})
            i += 1
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
    # 🔴 2026-06-28 写手信息隔离：当前章所属 cluster_id（写手注入门控的 reveal/trigger 判定基准·唯一权威反查）。
    current_cluster_id = s._current_cluster_id()
    # A11 DeepLore scene 维度门控（2026-07-08）：世界观词条清单按本块出场角色/地点过滤
    # （must_read 与 manifest 段同源同过滤·env MANIFEST_SCENE_GATING 默认 on·匹配不到
    # 场景信息 = 不过滤零变化）。
    world_hits, _scene_gating_report = _scene_gate_world_hits(s, world_hits, current_cluster_id)
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
        # 🔴 2026-06-28 伏笔明暗线隔离：hidden_secrets 现仅为「未揭晓伏笔数量」计数（不含内容），
        # 与 pending_secret_count 同义（向后兼容旧消费方的键名 + 显式新键名并存）。
        "hidden_secrets": due.get("pending_secret_count", 0),
        "pending_secret_count": due.get("pending_secret_count", 0),
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

    # v15: 写后校验指令（cluster-only）
    post_write_checks: list[dict] = [
        {
            "tool": "audit_hub.py",
            "args": f'"{project_root}" --mode cluster --cluster-id {current_cluster_id or ""}',
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
    manifest = {
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
        # 🔴 2026-06-28 写手信息隔离（item 1）：出场角色 + 主角的【已隔离】人物卡（剥未到期隐藏身份 true_role/
        # 未来知识 will_learn/秘密护栏 anti_patterns·留 surface_role/doesnt_know_yet/工艺 anti_pattern）。
        "active_character_cards": _collect_active_character_cards(s, active_chars, current_cluster_id),
        # 🔴 2026-06-29 角色信息差(per-character belief)：按 scene participants 投射各在场角色认知边界
        # （knows[] = learned<=current 的 fact · must_not_reference[] = unaware_of + 未到期 fact 的负向 masking）。
        # 让 writer 按各角色受限认知写·物理 masking 防角色用不该知道的知识穿帮（扮猪吃老虎/信息差）。
        # 默认安全闸：无 character_belief_ledger.json / 无 participants → []（向后兼容·零行为变化）。全 advisory。
        "scene_character_knowledge": _collect_scene_character_knowledge(s, current_cluster_id),
        # 🔴 2026-06-29 actant链接通producer（Greimas 六 actant + cast 经济·此前 build_manifest 零产 →
        # actant_drift_scanner / cast_economy_scanner 整条死码）。active_cast=本 cluster 出场角色集（scene
        # participants + active_chars·display name）·cluster_actant_state=六 actant 派分（ledger 已回库优先·
        # 否则 subject=主角/opponent=standing 反派派生）。全 advisory shadow·两 code 簇绝不进 HARD_GATE_CODES。
        # 默认安全闸：无可派生 → []/None（scanner 见空自跳过·无 actant 旧书零行为变化·向后兼容）。
        "active_cast": _collect_active_cast(s, active_chars, current_cluster_id),
        "cluster_actant_state": _collect_cluster_actant_state(s, current_cluster_id),
        "active_offscreen_actions": _collect_offscreen_actions(s, chapter),
        "active_fate_events": _collect_active_fate_events(s, chapter),
        "world_state_snapshot": _collect_world_state_snapshot(s, chapter),
        # 🔴 2026-06-29 环境location回喂闭环（环境 P0）：把 location_atmosphere_registry 已学到的地点签名感官
        # 正向回喂 writer（advisory 素材池·非硬约束）。默认安全闸：无 registry / 定位不到 → None（向后兼容）。
        "location_atmosphere": _collect_location_atmosphere(s, chapter),
        "active_clocks": _collect_active_clocks(s, chapter),
        "research_cache_ref": _collect_research_cache_ref(s, chapter),
        "event_cluster_context": _collect_event_cluster_context(s, chapter),
        # 2026-05-31 第 2 轮：rolling style anchor（动态锚 · 软牵引对抗长程文风退化 · 北极星①⑤⑥）。
        # 从本书已写片段里挑「最贴作者文风」的 1-2 段当下一块的动态锚（vs 第 1 轮静态开局 snippet）。
        "rolling_style_anchor": _collect_rolling_style_anchor(s, chapter),
        "storyteller_directive": _collect_storyteller_directive(s, chapter),
        # 🔴 2026-06-29 场景级Appraisal Beat注入（心理 P0·情绪余烬 + 本块情绪方向·appraisal-as-prose·advisory）：
        # 注入「这一拍 focal_character 情绪往哪走(derived_emotion 方向) + 为何(appraisal 评价) +
        # 如何外化(behavior_externalization·动作非情绪词)」+ 上块情绪余烬不归零延续。
        # 默认安全闸：无 叙事节拍器.appraisal_beats → None（不注入·零行为变化）。全 advisory·绝不 hard_gate。
        "appraisal_directive": _collect_appraisal_directive(s, current_cluster_id),
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
        "active_relationships": _collect_active_relationships(s, active_chars, current_cluster_id),
        "faction_standings_snapshot": _collect_faction_standings(s),
        "will_learn_due_this_ch": _collect_will_learn_due(s, chapter),
        "pending_secrets_to_reveal": _collect_secrets_to_reveal(s, chapter),
        # 🔴 2026-06-29 戏剧问题账本(PITQ/MDQ)：当前悬而未决的核心问题软注入（advisory·读者追读拉力）。
        # 读 戏剧问题账本.json 算 open_questions(raised−answered·累计到本 cluster)·软提示 writer 推进/部分揭示。
        # 默认安全闸：无账本 / 无 open → None（不注入·零行为变化·向后兼容）。全 advisory·绝不 hard_gate。
        "open_dramatic_questions": _collect_open_dramatic_questions(s, current_cluster_id),
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
        # #4：作者签名因果功能链（结构骨·env NARR_FUNC_SEQ_INJECT_MODE 默认 shadow·advisory·中文网文同质化结构层根因）。
        "narrative_function_sequence": _collect_narrative_function_sequence(s),
        # 阶段2：作者决策原则+人物刻画手法（思维/刻画骨·env DECISION_INJECT_MODE 默认 active 放量·advisory）。
        "author_decision_principles": _collect_author_decision_principles(s),
        # 阶段3：题材专属工艺提示（按 genre 路由·env GENRE_INJECT_MODE 默认 active 放量·advisory·unknown→None）。
        "genre_pack_directives": _collect_genre_pack_directives(s),
        # R7 W2 P1：Nummenmaa 情绪身体地图 topography hint（PNAS 2014·env EMOTION_TOPOGRAPHY_INJECT_MODE 默认 shadow·advisory·作者档优先）。
        "emotion_body_topography_hint": _collect_emotion_body_topography_hint(s),
        # R7 W2 P1：Focalization Type×Facet 二轴矩阵（FocalLens 2026·env FOCALIZATION_INJECT_MODE 默认 shadow·advisory）。
        "focalization_matrix": _collect_focalization_matrix(s, chapter),
        # R7 Batch-D（2026-06-20）：叙事债务账本 snapshot（book/volume/scene stock+flow·env NARRATIVE_DEBT_INJECT_MODE 默认 shadow·advisory·cluster-save-state step 9 写）。
        "debt_ledger_snapshot": _collect_debt_ledger_snapshot(s),
        # R7 Batch-D（2026-06-20）：Sagging Middle 中段塌陷 snapshot（needs_midpoint_bomb·env SAGGING_MIDDLE_INJECT_MODE 默认 shadow·advisory）。
        "sagging_middle_snapshot": _collect_sagging_middle_snapshot(s),
        # 🔴 2026-06-27 P1-08: motif advisory snapshot 注入 manifest（payoff_due/dormant/over_saturated 三态 top-3）。
        "motif_recurrence_directive": _collect_motif_advisory(s),
        "genre_baseline_diff": _collect_genre_baseline_diff(s),
        "volume_summaries_digest": _collect_volume_summaries_digest(s),
        "writer_mode": "freestyle_v27",
        "rag_relevant_chapters": rag_hits,
        "memory_search_results": memory_hits,
        "database_coverage": s.coverage_report(),
        "must_read": must_read,
        "hard_constraints": _build_hard_constraints(s, foreshadow_summary),
        "post_write_checks": post_write_checks,
        "instructions_for_subagent": (
            "你是 cluster 写作子代理，物理章号仅用于切章定位。开写前严格按以下步骤操作：\n"
            "1. 逐项 Read must_read 中 priority=P0 的文件\n"
            "2. 再 Read priority=P1 的文件\n"
            "3. priority=P2 的按需读取（字数/注意力紧张时可跳过）\n"
            "4. **主动补读权**：写作中若发现 manifest 未覆盖但必要的信息"
            "（例如某个角色未列入出场但对话里被提及），主动 Read 对应 JSON\n"
            "5. 写完整 cluster 草稿 → 运行 post_write_checks 中的 cluster 质量检查\n"
            "6. 读检查报告 → Edit 修正 → 再运行 cluster 质量检查\n"
            "7. cluster 质量检查通过才算完成，最多循环 3 轮"
        ),
        # v21 P1 cache layout 分层（agent prompt 按此顺序展示可最大化 Anthropic prompt caching 命中）
        "_cache_layout": _build_cache_layout(),
        # v21 P2-3 LiM 缓解：关键约束在末尾再次摘要（头部 P0 详细 + 尾部 critical_summary 强调）
        "_critical_summary": _build_critical_summary(chapter, foreshadow_summary,
                                                    must_read, locals().get("foreshadow_summary", {})),
        # 🔴 2026-06-27 P0-05：子系统消费层级 audit（骨架 _doc.consumption 单一真理源·遍历 KNOWN_DBS·writer/judge 一眼看清 live/deferred/unknown）。
        "_subsystem_consumption_audit": _collect_subsystem_consumption_audit(s),
    }
    # A3 前块结尾偏重注入（2026-07-07·PlotPilot recent_chapter_context）：cluster_002+ 注入
    # 上一 cluster 草稿末尾原文（默认 800 CJK·env PREV_TAIL_CJK 可调·T1 创作载荷）。
    # cluster_001 / 前块草稿缺失（断点恢复异态）→ 键不注入（零变化）。
    _prev_tail = _collect_prev_cluster_tail(s, current_cluster_id)
    if _prev_tail:
        manifest["prev_cluster_tail"] = _prev_tail
    # A14 近期活跃实体 LRU 兜底（2026-07-07·Ex3 Recent_Visit）：最近 3 个历史 cluster 出场
    # 实体简表（账本确定性抽取·白名单去重·上限 15 行·T2 状态库）。无数据 → 键不注入。
    _recent_ents = _collect_recently_active_entities(s, active_chars, current_cluster_id)
    if _recent_ents:
        manifest["recently_active_entities"] = _recent_ents
    # A2 遗留清偿（2026-07-08）：写前 Evolution Gate 报告摘要（.wal/<key>_pre_write_gate.json
    # 的 waived/warnings·有内容才注入·T0 契约类）——writer 看到「已声明豁免的叙事手法」上下文。
    _gate_digest = _collect_pre_write_gate_digest(s, current_cluster_id)
    if _gate_digest:
        manifest["pre_write_gate_digest"] = _gate_digest
    # A11 门控审计元数据（2026-07-08）：真有词条被滤才注入（META·不参与预算与裁剪）。
    if _scene_gating_report:
        manifest["_scene_gating"] = _scene_gating_report
    # A4 编辑手记（2026-07-08·PlotPilot「一段自然语言比 8 个分隔符更易融入创作」）：
    # 把已组装 manifest 的结构块（伏笔计数/悬置问题/主角压力与弧线/涟漪后果/卷收敛锚）
    # 确定性坍缩成一段 200-400 字人话手记（双视图·结构块原样保留给 scanner/审计·T1）。
    # 素材全空 / 拼装异常 → 键不注入（零变化）。零 LLM 纯模板填充·同输入同字节。
    _editor_note = _build_editor_note(manifest)
    if _editor_note:
        manifest["editor_note"] = _editor_note
    # S1 分层 token 预算（2026-07-07·PlotPilot context_budget_allocator 移植）：
    # 记账（budget_report 元数据）始终附加；分层裁剪（T3 留 5% 地板 → T2 → T1·T0 自身 40% 硬上限）
    # 仅在体积 >= 既有 v19.4 硬守卫（BUDGET_HARD_KB·env MANIFEST_BUDGET_* 可覆盖）时生效——
    # 不触发守卫 = 所有注入段逐字节不变（不新增任何截断触发条件）。
    return manifest_budget.apply_budget(manifest)


def _build_cache_layout() -> dict:
    """v21 P1-1+P1-2: manifest cache 友好分层。

    STATIC（cacheable 99%）：跨章几乎不变的静态参考（蒸馏/常量模板/Propp）
    SEMI_STATIC（cacheable 70-80%）：本卷内变化的（character_arc/世界状态/事件池/角色池）
    DYNAMIC（cacheable 30%）：每章必变的（cluster_blueprint/上章 changes/prev_judge_findings）

    agent prompt 设计：按 STATIC → SEMI_STATIC → DYNAMIC 顺序排放，Anthropic API 自动 detect prefix → 命中率最高。

    🔴 M1 cache 铁律（记忆调研 2026-06-15·前瞻约束·test_m1_cache_layout 守卫）：
    B1 importance-aware 重排（伏笔 tier→importance / last_seen / recurrence 等动态字段）**只准动
    DYNAMIC_30 段**，绝不重排 STATIC/SEMI——后者 prefix 字节稳定才命中 cache（省 60-90% token），
    动态重排会破 prefix → 命中率暴跌 → 成本反升。B1 重排落点（selective_history_retrieval /
    pending_secrets_to_reveal / hard_constraints）本就在 DYNAMIC，零行为改动；新增 importance 字段
    务必落 DYNAMIC（守卫测试会拦外溢）。
    """
    return {
        "_doc": "v21 P1: Anthropic prompt caching 友好分层。agent prompt 顶部按本表顺序展示字段，可命中 prefix cache 节省 60-90% token 成本。",
        "STATIC_99_cacheable": [
            "distill_continuity_template",       # 蒸馏散文衔接模板
            "distill_voice_packs_reference",     # 原作角色风格 DNA
            "deep_writing_dims",                 # L4: D1 心理距离 / D2 visceral-first / D3 动机弧光（全书不变创作提示）
            "author_rhythm_signature",           # 阶段1: 作者叙事节奏指纹（序列级骨·全书不变）
            "author_decision_principles",        # 阶段2: 作者决策原则+人物刻画手法（思维/刻画骨·全书不变）
            "narrative_function_sequence",       # #4: 作者签名因果功能链（结构骨·全书不变·M1 cache 铁律归 STATIC）
            "genre_pack_directives",             # 阶段3: 题材专属工艺提示（按 genre 路由·全书不变）
            "emotion_body_topography_hint",      # R7 W2: Nummenmaa 情绪身体地图（静态·全书不变·advisory）
            "focalization_matrix",               # R7 W2: Focalization Type×Facet 二轴矩阵（静态·全书不变·advisory）
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
            "active_character_cards",            # 🔴 写手信息隔离：已隔离的出场角色卡（卷内慢变·隐藏身份到揭密 cluster 才变）
            "scene_character_knowledge",         # 🔴 2026-06-29 角色信息差：per-scene 各角色认知边界（同 cluster 内不变·卷内慢变）
            "active_fate_events",                # 卷内大势事件池
            "world_state_snapshot",              # 世界数值（卷间慢变）
            "location_atmosphere",               # 🔴 2026-06-29 地点签名感官回喂（同 cluster 内地点稳定·卷内慢变）

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
            "appraisal_directive",               # 🔴 2026-06-29 场景级Appraisal Beat：情绪余烬+本块方向（每 cluster 变·advisory）
            "protagonist_stress",                # stress 累计每章变
            "fate_dice_hint",                    # 本章抽签状态
            "active_offscreen_actions",          # 本章生效幕后
            "selective_history_retrieval",       # 本章 query 检索结果
            "prev_judge_findings",               # 上章 judge 发现
            "will_learn_due_this_ch",            # 本章必学知识
            "pending_secrets_to_reveal",         # 本章必揭秘
            "open_dramatic_questions",           # 🔴 2026-06-29 戏剧问题账本：当前悬而未决核心问题（每 cluster 变·advisory）

            "hard_constraints",                  # 本章硬约束（含本章伏笔到期）
            "post_write_checks",                 # 本章写后检查
            "_critical_summary",                 # 本章 LiM 关键摘要（动态）
            "debt_ledger_snapshot",              # R7 Batch-D: 叙事债务账本 snapshot（cluster-save-state 后每 cluster 变）
            "sagging_middle_snapshot",           # R7 Batch-D: Sagging Middle 中段塌陷 snapshot（needs_midpoint_bomb 动态）
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
        print(f"[BUDGET-ERROR] manifest 体积 {manifest_kb:.1f}KB 超硬上限 {BUDGET_HARD_KB}KB"
              "（S1 分层裁剪已尽力·T0/META 底座仍超限，检查 hard_constraints/must_read 是否失控膨胀）",
              file=sys.stderr)
    elif manifest_kb >= BUDGET_SOFT_KB:
        print(f"[BUDGET-WARN] manifest 体积 {manifest_kb:.1f}KB 接近软上限 {BUDGET_SOFT_KB}KB", file=sys.stderr)

    # S1 分层预算观测（budget_report 由 build_manifest 内 apply_budget 附加）
    _br = manifest.get("budget_report") or {}
    _clog = _br.get("compression_log") or []
    if _clog:
        print(f"[BUDGET-TRIM] S1 分层裁剪生效：{len(_clog)} 条（T3→T2→T1·T0 上限 "
              f"{(_br.get('budget') or {}).get('t0_max_ratio', 0.4):.0%}），详见 budget_report.compression_log")
        for _e in _clog[:10]:
            print(f"  - [{_e.get('tier')}] {_e.get('section')} {_e.get('action')} "
                  f"{_e.get('before_bytes')}→{_e.get('after_bytes')}B")
    if _br.get("constraint_share_warning"):
        print(f"[BUDGET-ADVISORY] {_br['constraint_share_warning']}")

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
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
