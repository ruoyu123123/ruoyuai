#!/usr/bin/env python3
"""
save_state.py — 状态保存流水线的确定性部分

v26 cluster-only CLI 子命令（章级 --wal-* / --parse / --apply-changes / --git-commit /
--report 已随 chapter mode 废弃下线，章级函数仅作 cluster 函数内部组件复用）：
  --apply-cluster-changes <key>     整 cluster 落地 13 JSON + writer_truth_check 撒谎检测
  --git-commit-cluster <key>        1 cluster 1 commit
  --auto-post-reflect-cluster <key> cluster 级 learning_loop 三步链
  --build-cluster-summary <key>     富摘要预算写入 故事块摘要.json
  --ecas-checkpoint <key>           验证 cluster_draft 完整性 + checkpoint_data

所有子命令都幂等（重复执行不产生副作用或破坏数据）。
确定性逻辑集中在此，AI 只负责：故事块摘要/写作反思/走向卡片。
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from frozen_util import child_python, scripts_dir  # frozen-aware（M4·dev=no-op）
from datetime import datetime
from pathlib import Path

# v18：统一章节读写走 chapter_io，杜绝各脚本各自 split
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio
# 2026-05-29 修 章号当cluster号：章号 ⇄ cluster_id 反查走单一权威工具
import cluster_lookup
from log_util import get_logger

logger = get_logger(__name__)
# 2026-06-12 缺漏修复批次1 任务C【原子写改造·P1-1 数据损坏防护】：
# 写 _数据库/*.json 统一走 atomic_json.atomic_write_json（tmp 唯一名 + fsync + os.replace
# 原子语义——进程写一半被杀只残留 .tmp、绝不毁掉原文件）。此前 save_json 裸 write_text：
# 半截写 → 下个读者 json.JSONDecodeError → load_json 兜底成 default → 伏笔表/人物卡/
# 进度/地图/时间线/道具 整库静默清空。ImportError 兜底见 save_json
# （照 cluster_choice_apply.py:89-96 范式手写 tmp + replace）。
try:
    import atomic_json
except ImportError:  # 极端环境（脚本被单独拷走执行）缺 atomic_json → save_json 内降级
    atomic_json = None


def _resolve_cluster(root: Path, ch: int) -> tuple[str, bool]:
    """把章号反查为真实 cluster_id。

    返回 (cluster_id, inferred)：
      - 反查命中 → (真实 cluster_id, False)
      - 反查不到 → (normalize_cluster_id(ch) fallback, True)，调用方应给记录标
        `_cluster_inferred = True`（表示是按章号推断、不可信）。
    """
    cid = cluster_lookup.ch_to_cluster_id(root, ch)
    if cid:
        return cid, False
    return cluster_lookup.normalize_cluster_id(ch) or f"cluster_{ch:03d}", True


# 🔴 2026-06-27 SYS-2/SYS-3：伏笔 payoff terminal vs progressive 分流（治批量误标 resolved）
# 病灶：原 apply_changes 把每条 foreshadowing_paid 无差别当 terminal payoff（无条件 resolved=True），
# split_cluster_changes 把整 cluster 的 paid 平铺进每章 → 逐章 re-apply 放大成「统一盖末章 resolved」。
# 修：terminal（核心承诺彻底兑现 / Tier-1 finale 抵达）才标 resolved；progressive（推进/扩散/阶段
# 数值）只记 payoff_progress 保持 open。优先级：foreshadower.terminal > writer.kind > 词面默认。
_PAYOFF_TERMINAL_WORDS = ("回收", "兑现", "揭晓", "收束")
_PAYOFF_PROGRESSIVE_WORDS = ("推进", "扩散", "进展", "数值")


def _word_surface_terminal(desc: str) -> bool:
    """词面默认分类：含 terminal 词且不含 progressive 词 → True；其余（含 progressive / 同时命中 /
    皆无）一律保守 progressive(False)。措辞模糊时绝不轻易判 terminal（防误标 resolved）。"""
    if not desc:
        return False
    has_t = any(w in desc for w in _PAYOFF_TERMINAL_WORDS)
    has_p = any(w in desc for w in _PAYOFF_PROGRESSIVE_WORDS)
    return bool(has_t and not has_p)


def _classify_payoff_terminal(fid, writer_kind, desc, terminal_map) -> bool:
    """判某条 payoff 是否 terminal。优先级：① foreshadower 显式 terminal > ② writer self-eval
    kind > ③ 词面默认。terminal_map 缺该 fs / 字段 None → 降级到下一级信号。"""
    if fid in terminal_map and terminal_map[fid] is not None:
        return bool(terminal_map[fid])
    if writer_kind in ("terminal", "progressive"):
        return writer_kind == "terminal"
    return _word_surface_terminal(desc or "")


def _load_foreshadower_maps(root: Path, ch: int) -> tuple[dict, dict]:
    """读 ch 所属 cluster 的 foreshadower JudgeReport，建 (terminal_map, score_map)。

    🔴 2026-06-27 SYS-2/SYS-3：payoff_scores 是 specific_findings 下的嵌套 list（非顶层），
    按 fs_id 匹配抽 terminal（SYS-2 分流权威信号）+ score（SYS-3 score==0 门控：foreshadower
    判正文 0 痕迹 → 不标 resolved）。报告缺失 → 空 map（回退 writer.kind / 词面默认，行为不变）。"""
    terminal_map: dict = {}
    score_map: dict = {}
    try:
        cid, _ = _resolve_cluster(root, ch)
        rpt = root / "_数据库" / ".judge_reports" / f"{cid}_foreshadower.json"
        if not rpt.is_file():
            return terminal_map, score_map
        data = json.loads(rpt.read_text(encoding="utf-8"))
        for it in (data.get("specific_findings") or {}).get("payoff_scores", []) or []:
            if not isinstance(it, dict):
                continue
            fid = it.get("fs_id")
            if not fid:
                continue
            if "terminal" in it:
                terminal_map[fid] = it.get("terminal")
            if "score" in it:
                try:
                    score_map[fid] = int(it.get("score"))
                except (TypeError, ValueError):
                    pass
    except Exception:
        # 报告坏/缺 → 不阻断 apply，回退二级信号
        pass
    return terminal_map, score_map


# ============ IO ============

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data):
    """统一 JSON 落盘入口（2026-06-12 任务C 原子写改造·P1-1）。

    走 atomic_json.atomic_write_json：tmp 唯一名（pid+uuid）+ fsync + os.replace 原子替换。
    进程被杀只留 tmp 不毁原文件——本函数是伏笔表/人物卡/进度/地图/时间线/道具 等
    _数据库 JSON 的唯一写出口，半截写防护在此一处闭环。
    atomic_json 不可导入时兜底手写 tmp + os.replace（照 cluster_choice_apply.py:89-96 范式，
    tmp 名加 pid+uuid 防并发交错——不沿用固定 .json.tmp 反模式）。
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    if atomic_json is not None:
        atomic_json.atomic_write_json(p, data)
    else:
        import os as _os
        import uuid as _uuid
        tmp = p.parent / f".{p.name}.{_os.getpid()}.{_uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            _os.replace(str(tmp), str(p))
        finally:
            # 唯一 tmp 名 → 只清理本次自己的 tmp（replace 成功后已不存在·失败时不留垃圾）
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def find_chapter_file(root: Path, ch: int) -> Path | None:
    """定位章节正文文件。v18：委托 chapter_io.find_body_file（兼容 4 布局 + 旧平铺）。"""
    return cio.find_body_file(root, ch)


# ============ WAL ============
# 🔴 2026-05-29 流程贯通（断点 5 死代码清理）：章级 WAL 函数
# wal_path / cmd_wal_start / cmd_wal_step / cmd_wal_end 已删除。
# 依据：v26 chapter mode 废弃后 main() 无 --wal-* 入口（grep 确认无外部调用方），
# 章级 WAL 函数互相引用、无其他调用者。cluster WAL 由调度器 shell 直建（不走本脚本）。
# apply_changes / cmd_parse 写的 .wal/第N章_*.json 是中间产物文件，用直接路径，
# 与已删的 wal_path()（保存 _save_state.json）无关，不受影响。


# ============ 解析 CHANGES ============

def parse_changes(root: Path, ch: int) -> tuple[dict | None, str]:
    """读第 ch 章的 CHANGES，返回 (factual_dict_含applied_style, strategy)。
    v18：统一走 cio.read_changes()——v18 分离稿直接读 _changes.json，
    旧混合稿由 cio 兼容层自动 split。本脚本不再自己解析分隔符。
    """
    try:
        data = cio.read_changes(root, ch)
    except Exception:
        return None, "failed"
    factual = data.get("factual") or {}
    self_eval = data.get("self_eval") or {}
    if not factual and not self_eval:
        return None, "failed"
    # 合并 self_eval 的 applied_style 等字段（不覆盖 factual 同名键）
    result = dict(factual)
    for k, v in self_eval.items():
        result.setdefault(k, v)
    cp = cio.changes_path(root, ch)
    strategy = "v18_changes_json" if cp.is_file() else "missing"
    return result, strategy


def cmd_parse(root: Path, ch: int) -> int:
    """解析单章 CHANGES → .wal/第N章_parsed.json。返回状态码（0 成功 / 1 软失败 / 2 文件缺失）。

    2026-05-29 复审修复 [M3]：原用 sys.exit 直接退进程，被 cluster 循环调用时单章失败
    会整 cluster 中断。改为返回状态码，由 cmd_apply_cluster_changes 累计、单章失败不中断整 cluster。
    """
    if not find_chapter_file(root, ch) and not cio.changes_path(root, ch).is_file():
        logger.error(f" 第{ch}章 正文/CHANGES 均未找到")
        return 2
    changes, strategy = parse_changes(root, ch)
    if changes is None:
        logger.info(f"[PARSE] CHANGES 解析失败（无 _changes.json 且旧稿无 CHANGES 段），需要 AI agent 兜底")
        return 1
    out = root / "_数据库" / ".wal" / f"第{ch}章_parsed.json"
    save_json(out, {"strategy": strategy, "changes": changes})
    logger.info(f"[PARSE] 策略 {strategy} 成功 → {out.relative_to(root)}")
    return 0


# ============ 应用 CHANGES（结构化更新） ============

def _generate_patch(changes: dict, ch: int) -> list[dict]:
    """v16: 生成声明式JSON Patch（可审计/可回放/可撤销）。"""
    patches = []
    for act in changes.get("foreshadowing_actions", []):
        patches.append({
            "target": "伏笔表.json",
            "op": "add" if act.get("type") in ("setup", "raise", "make", "establish") else "update",
            "path": f"/{act.get('category', 'promise')}/{act.get('id', '')}",
            "value": act,
            "chapter": ch,
        })
    for cc in changes.get("character_changes", []):
        patches.append({
            "target": "人物卡.json",
            "op": "update",
            "path": f"/characters/{cc.get('name', '')}/growth_arc",
            "value": {"ch": ch, "field": cc.get("field"), "from": cc.get("from"), "to": cc.get("to")},
            "chapter": ch,
        })
    for mov in changes.get("character_movements", []):
        patches.append({
            "target": "地图.json",
            "op": "update",
            "path": f"/character_positions/{mov.get('character', '')}",
            "value": mov.get("to", ""),
            "chapter": ch,
        })
    ta = changes.get("time_advance") or {}
    if ta:
        patches.append({
            "target": "时间线.json",
            "op": "append",
            "path": "/time_log",
            "value": {"ch": ch, "elapsed": ta.get("elapsed", ""), "key_events": ta.get("key_events", [])},
            "chapter": ch,
        })
    for t in changes.get("item_transfers", []):
        patches.append({
            "target": "道具.json",
            "op": "update",
            "path": f"/items/{t.get('item', '')}/holder",
            "value": t.get("to", ""),
            "chapter": ch,
        })
    for ne in (changes.get("new_entities", {}) or {}).get("characters", []):
        patches.append({
            "target": "人物卡.json",
            "op": "add",
            "path": f"/characters/{ne.get('name', '')}",
            "value": ne,
            "chapter": ch,
        })
    return patches


def apply_changes(root: Path, ch: int) -> int:
    """将 parsed 的 CHANGES 落地到对应 JSON。
    v16: 先生成声明式Patch文件（可审计/可回放），再执行实际修改。

    2026-05-29 复审修复 [M3]：原用 sys.exit(1) 直接退进程，被 cluster 循环调用时单章
    无 parsed 会整 cluster 中断。改为返回状态码（0 成功 / 1 无 parsed 跳过），
    由 cmd_apply_cluster_changes 累计、单章失败不中断整 cluster。
    """
    db = root / "_数据库"
    parsed_path = db / ".wal" / f"第{ch}章_parsed.json"
    parsed = load_json(parsed_path, {})
    changes = parsed.get("changes", {})
    if not changes:
        logger.info(f"[APPLY] 无 parsed CHANGES，跳过")
        return 1

    # 2026-06-02 修：gen-model 常把 foreshadowing_planted/paid 报成自评摘要字符串
    # （如「3/3——fs_001/fs_002/fs_003 全部 planted」）而非结构化 list → 下游 (str or []) + list
    # 拼接崩 TypeError + for-in 逐字符遍历。统一 coerce 成 list（str/dict→[v]·None 保持·已是 list 不动）。
    for _fk in ("foreshadowing_planted", "foreshadowing_paid"):
        _v = changes.get(_fk)
        if _v is not None and not isinstance(_v, list):
            changes[_fk] = [_v]

    # v16: 生成并保存声明式Patch
    patches = _generate_patch(changes, ch)
    patch_path = db / ".wal" / f"第{ch}章_patch.json"
    save_json(patch_path, {"chapter": ch, "patch_count": len(patches), "patches": patches,
                           "generated_at": datetime.now().isoformat(timespec="seconds")})
    logger.info(f"[PATCH] 生成 {len(patches)} 条声明式补丁 → {patch_path.name}")

    summary = {"applied": [], "warnings": [], "patch_file": str(patch_path.name)}

    # --- 伏笔表（第4步）---
    fs = load_json(db / "伏笔表.json", {"promises": [], "deadlines": [],
                                        "pledges": [], "secrets": []})
    # 2026-05-30 北极星复审 A-1：writer（gen_writer prompt 自查项）实产 foreshadowing_planted/paid，
    # 但本函数原只读 foreshadowing_actions（结构化）→ 伏笔表 resolved/status 从不更新（数据流断裂）。
    # 桥接：从 planted/paid 构造 action；元素须为带 id 的 dict 才能精确更新伏笔表，
    # 纯描述串无 id → 记 warning（可见而非静默断裂）。
    #
    # 2026-05-30 [#6] 门控短路修复：原 `if not _fs_actions:` 让桥接只在 foreshadowing_actions
    # 为空时执行。真实项目 cluster_005 同时有 foreshadowing_actions（其他项）+ foreshadowing_paid →
    # 桥接被整体短路 → writer 明确申报兑现的伏笔（foreshadowing_paid）永不标 resolved，伏笔表停在
    # planted，下游 SECRET_NOT_REVEALED / FORESHADOWING_NOT_PAID 误判。改为**合并**：既处理显式
    # foreshadowing_actions，也始终把 planted/paid 桥接进 resolve，按 (category, type, id) 去重
    # （显式 action 已覆盖同一 fs → 不重复构造）。
    _fs_actions = list(changes.get("foreshadowing_actions") or [])
    _seen = {(a.get("category"), a.get("type"), a.get("id"))
             for a in _fs_actions if isinstance(a, dict)}
    _bridged_with_id = False  # planted/paid 里出现过带 id 的可桥接项
    for _p in changes.get("foreshadowing_planted", []) or []:
        if isinstance(_p, dict) and _p.get("id"):
            _bridged_with_id = True
            _key = (_p.get("category", "promise"), "setup", _p["id"])
            if _key not in _seen:
                _seen.add(_key)
                _fs_actions.append({"category": _p.get("category", "promise"), "type": "setup",
                                    "id": _p["id"], "tier": _p.get("tier", 3),
                                    "description": _p.get("desc") or _p.get("description", ""),
                                    "due_by_cluster": _p.get("due_by_cluster")})
    # 🔴 2026-06-27 SYS-2：writer self-eval foreshadowing_paid[].kind（terminal|progressive）作
    # foreshadower 缺字段时二级信号——按 fs_id 建 map 供 payoff 分流读（覆盖 bridged + dedup 两路径）。
    _writer_kind_map = {_p["id"]: _p.get("kind")
                        for _p in (changes.get("foreshadowing_paid") or [])
                        if isinstance(_p, dict) and _p.get("id") and _p.get("kind")}
    for _p in changes.get("foreshadowing_paid", []) or []:
        if isinstance(_p, dict) and _p.get("id"):
            _bridged_with_id = True
            _key = (_p.get("category", "promise"), "payoff", _p["id"])
            if _key not in _seen:
                _seen.add(_key)
                _fs_actions.append({"category": _p.get("category", "promise"), "type": "payoff",
                                    "id": _p["id"], "kind": _p.get("kind"),
                                    "description": _p.get("desc") or _p.get("description", "")})
    # 🔴 2026-06-28：planted 缺 fs_id 自动派确定性 id 并桥接入伏笔表（治 intake 死路径·模型/brief 常
    # 给纯 desc 无 id → 原仅 warning 丢数据 → 后续 cluster 无从回收·伏笔表恒空）。id=desc 的 sha1 前8位
    # → re-apply / split 平铺多章重复 apply 时（category,type,id）去重 + promises id 去重双重幂等。
    # 只对 planted(setup) 自动派——paid(payoff) 无法凭空兑现未知 fs。北极星：模型漏 id 不丢内容状态。
    import hashlib as _hashlib
    _auto_assigned_n = 0
    for _p in changes.get("foreshadowing_planted", []) or []:
        if isinstance(_p, dict):
            _desc = (_p.get("desc") or _p.get("description") or "").strip()
            _has_id = bool(_p.get("id"))
            _tier = _p.get("tier", 3)
        elif isinstance(_p, str):
            _desc, _has_id, _tier = _p.strip(), False, 3
        else:
            continue
        if _desc and not _has_id:
            _auto_id = "fs_auto_" + _hashlib.sha1(_desc.encode("utf-8")).hexdigest()[:8]
            _key = ("promise", "setup", _auto_id)
            if _key not in _seen:
                _seen.add(_key)
                _fs_actions.append({"category": "promise", "type": "setup", "id": _auto_id,
                                    "tier": _tier, "description": _desc, "due_by_cluster": None,
                                    "_auto_assigned": True})
                _auto_assigned_n += 1
    # planted/paid 全是无 id 描述串（既无显式 actions 也无可桥接 id）→ 记 warning（可见非静默断裂）
    _raw = (changes.get("foreshadowing_planted") or []) + (changes.get("foreshadowing_paid") or [])
    if _auto_assigned_n:
        summary["warnings"].append(
            f"foreshadowing_planted 有 {_auto_assigned_n} 条无 fs_id → 已自动派 fs_auto_<hash> 记入伏笔表"
            "（数据不丢·后续回收时 foreshadower 按 desc 匹配或人工绑 id）")
    if _raw and not _bridged_with_id and not changes.get("foreshadowing_actions") and not _auto_assigned_n:
        summary["warnings"].append(
            f"foreshadowing_planted/paid 共 {len(_raw)} 条为无 id 描述串 → 无法更新伏笔表 resolved/status；"
            "需 writer 报带 fs_id 的项（gen_writer prompt 已要求引用 cluster_brief fs_id）")
    # 🔴 2026-06-27 SYS-2/SYS-3：读本 cluster foreshadower JudgeReport → terminal/score map（payoff 分流权威信号）
    _terminal_map, _score_map = _load_foreshadower_maps(root, ch)
    for act in _fs_actions:
        cat = act.get("category")
        typ = act.get("type")
        fid = act.get("id")
        if cat == "promise":
            if typ == "setup":
                # v2 cluster 化（2026-05-28）：纯 cluster 模式
                # 2026-05-29 修 章号当cluster号：setup_cluster 由 ch 反查真实 cluster_id
                setup_cid, setup_inferred = _resolve_cluster(root, ch)
                # 2026-05-29 复审修复 [M4/SC-5]：due_by 不再用 ch+20 反查（未来 cluster 尚未
                # 涌现，反查必 None → fallback 拼出 cluster_NNN 是 SC-5 明禁的「章号当 cluster 号」）。
                # 改：优先用 writer 给的 due_by_cluster（归一化）；没给则存「章偏移语义」
                # due_by_ch_offset，cluster_id 留 None 待后续 cluster 涌现时回填。
                due_by_cluster = cluster_lookup.normalize_cluster_id(act.get("due_by_cluster")) \
                    if act.get("due_by_cluster") else None
                promise_rec = {
                    "id": fid, "setup_cluster": setup_cid, "tier": act.get("tier", 3),
                    "description": act.get("description", ""),
                    "due_by_cluster": due_by_cluster,  # None = 待 cluster 涌现后回填
                    "resolved": False,
                }
                # writer 未指定到期 cluster → 保留章偏移语义供后续回填
                if not due_by_cluster:
                    promise_rec["due_by_ch_offset"] = act.get("due_by_ch_offset", 20)
                    promise_rec["due_by_pending_resolution"] = True
                # setup_cluster 为按章号推断（反查不到） → 打不可信标记
                if setup_inferred:
                    promise_rec["_cluster_inferred"] = True
                # 2026-06-02 修：按 id 去重——同 id 已存在则跳过 append（防 re-apply / split v1 把整
                # factual 平铺到每章 → apply 5× → 同 id 累积重复·foreshadower 实测 fs_001 被写 6 套）
                if any(p.get("id") == fid for p in fs["promises"]):
                    summary["applied"].append(f"伏笔 setup(已存在·去重跳过): {fid}")
                else:
                    fs["promises"].append(promise_rec)
                    summary["applied"].append(f"伏笔 setup: {fid}")
            elif typ == "payoff":
                # 🔴 2026-06-27 SYS-2/SYS-3：terminal vs progressive 分流 + foreshadower score==0 门控
                for p in fs["promises"]:
                    if p.get("id") == fid:
                        # SYS-3：foreshadower 判 score==0（声明 paid 但正文 0 痕迹/谎报）→ 不标 resolved
                        if _score_map.get(fid) == 0:
                            summary["warnings"].append(
                                f"伏笔 payoff(score=0·foreshadower 判正文 0 痕迹·不标 resolved·待补写或撤回): {fid}")
                            break
                        _wk = _writer_kind_map.get(fid) or act.get("kind")
                        _is_terminal = _classify_payoff_terminal(
                            fid, _wk, act.get("description", ""), _terminal_map)
                        if _is_terminal:
                            # terminal：核心承诺彻底兑现 → 标 resolved（首次兑现章为准·re-apply 幂等不后移）
                            if not p.get("resolved"):
                                p["resolved"] = True
                                p["resolved_at_ch"] = ch
                            summary["applied"].append(f"伏笔 payoff(terminal): {fid}")
                        else:
                            # progressive：推进/扩散/阶段数值 → 不动 resolved，记 payoff_progress 保持 open；
                            # 按 (fs_id,ch) 去重（防 split 平铺逐章 + 多次 re-apply 累积 N 条）
                            prog = p.setdefault("payoff_progress", [])
                            if not any(isinstance(e, dict) and e.get("ch") == ch for e in prog):
                                prog.append({"ch": ch, "desc": act.get("description", "")})
                            p["last_advanced_at_ch"] = ch
                            summary["applied"].append(f"伏笔 payoff(progressive·保持 open): {fid}")
                        break
                else:
                    summary["warnings"].append(f"payoff 引用了不存在的伏笔: {fid}")
        elif cat == "deadline":
            if typ == "raise" and not any(d.get("id") == fid for d in fs["deadlines"]):
                fs["deadlines"].append({
                    "id": fid, "raised_ch": ch,
                    "description": act.get("description", ""),
                    "deadline_ch": act.get("deadline_ch", ch + 5),
                    "status": "pending",
                })
            elif typ in ("trigger", "miss"):
                for d in fs["deadlines"]:
                    if d.get("id") == fid:
                        d["status"] = "triggered" if typ == "trigger" else "missed"
                        break
        elif cat == "pledge":
            if typ == "make" and not any(pl.get("id") == fid for pl in fs["pledges"]):
                fs["pledges"].append({
                    "id": fid, "pledger": act.get("pledger", ""),
                    "raised_ch": ch, "pledge": act.get("description", ""),
                    "status": "active",
                })
            elif typ in ("fulfill", "break"):
                for pl in fs["pledges"]:
                    if pl.get("id") == fid:
                        pl["status"] = "fulfilled" if typ == "fulfill" else "broken"
                        break
        elif cat == "secret":
            if typ == "establish":
                # v2 cluster 化（2026-05-28）：纯 cluster 模式
                # 2026-05-29 修 章号当cluster号：established_cluster 由 ch 反查
                est_cid, est_inferred = _resolve_cluster(root, ch)
                # 2026-05-29 复审修复 [M4/SC-5]：reveal_at 不再用 ch+50 反查（未来 cluster
                # 尚未涌现，反查必 None → fallback 拼 cluster_NNN 是 SC-5 明禁的「章号当 cluster 号」）。
                # 改：优先用 writer 给的 reveal_at_cluster（归一化）；没给则存「章偏移语义」
                # reveal_at_ch_offset，cluster_id 留 None 待后续 cluster 涌现时回填。
                reveal_at_cluster = cluster_lookup.normalize_cluster_id(act.get("reveal_at_cluster")) \
                    if act.get("reveal_at_cluster") else None
                secret_rec = {
                    "id": fid,
                    "secret": act.get("description", ""),
                    "established_cluster": est_cid,
                    "reveal_at_cluster": reveal_at_cluster,  # None = 待 cluster 涌现后回填
                    "known_by": act.get("known_by", []),
                    "status": "hidden",
                }
                if not reveal_at_cluster:
                    secret_rec["reveal_at_ch_offset"] = act.get("reveal_at_ch_offset", 50)
                    secret_rec["reveal_at_pending_resolution"] = True
                if est_inferred:
                    secret_rec["_cluster_inferred"] = True
                # 2026-06-02 修：按 id 去重（同 promises·防 re-apply/平铺累积重复 secrets）
                if not any(s.get("id") == fid for s in fs["secrets"]):
                    fs["secrets"].append(secret_rec)
            elif typ == "reveal":
                for s in fs["secrets"]:
                    if s.get("id") == fid:
                        s["status"] = "revealed"
                        break
            elif typ == "leak":
                for s in fs["secrets"]:
                    if s.get("id") == fid:
                        s["status"] = "leaked"
                        s["known_by"] = list(set(s.get("known_by", []) + act.get("known_by", [])))
                        break
    save_json(db / "伏笔表.json", fs)

    # --- 人物卡 growth_arc（v16 Codex Progressions · 角色时间线变化追踪）---
    cards = load_json(db / "人物卡.json", {"characters": []})
    char_map = {c.get("name"): c for c in cards.get("characters", [])}
    # 🔴 2026-06-27 C11（cluster 级幂等去重）：split_cluster_changes v1 把整 cluster 的
    # character_changes 平铺进每章 _changes.json，cmd_apply_cluster_changes 对 N 章逐章 apply
    # → 同一逻辑成长事件被 append N 次（growth_arc 膨胀 N 倍）。按 (name,key_change,trigger,
    # _source_cluster) 去重：同 cluster 内同一成长事件只记一次（_source_cluster 由章号反查 cluster_id，
    # per-cluster 非全局——下个 cluster 真正产生的不同成长事件 cid 不同，仍合法 append）。
    for cc in changes.get("character_changes", []):
        name = cc.get("name", "")
        if name in char_map:
            char = char_map[name]
            src_cid, _src_inferred = _resolve_cluster(root, ch)
            key_change = f"{cc.get('field', '')}: {cc.get('from', '')} → {cc.get('to', '')}"
            trigger = cc.get("trigger", "")
            arc = char.setdefault("growth_arc", [])
            _dup = any(
                isinstance(e, dict)
                and e.get("key_change") == key_change
                and e.get("trigger", "") == trigger
                and e.get("_source_cluster") == src_cid
                for e in arc
            )
            if _dup:
                summary["applied"].append(f"角色弧(去重跳过): {name} {cc.get('field', '')}")
            else:
                arc.append({
                    "ch": ch,
                    "state": cc.get("to", ""),
                    "key_change": key_change,
                    "trigger": trigger,
                    "_source_cluster": src_cid,  # 🔴 2026-06-27 C11 幂等去重键
                })
                summary["applied"].append(f"角色弧: {name} ch{ch} {cc.get('field', '')}")
    # 新角色注册
    for ne in (changes.get("new_entities", {}) or {}).get("characters", []):
        ne_name = ne.get("name", "")
        if ne_name and ne_name not in char_map:
            # v2 cluster 化（2026-05-28）：纯 cluster 模式
            # 2026-05-29 修 章号当cluster号：first_appear_cluster / growth_arc.cluster 由 ch 反查
            appear_cid, appear_inferred = _resolve_cluster(root, ch)
            ne_rec = {
                "id": ne_name.lower().replace(" ", "_"),
                "name": ne_name,
                "role": ne.get("role", "配角"),
                "first_appear_cluster": appear_cid,
                "growth_arc": [{"cluster": appear_cid, "state": "初次登场", "key_change": "出场", "trigger": ""}],
            }
            if appear_inferred:
                ne_rec["_cluster_inferred"] = True
            cards["characters"].append(ne_rec)
            summary["applied"].append(f"新角色: {ne_name}")
    save_json(db / "人物卡.json", cards)

    # --- 进度（completed+1, current+1）---
    # 2026-05-30 北极星复审：进度.json 损坏时 load_json 静默返回 {}，下方覆写会清空 cluster_blueprint/
    # volumes/completed 等全部字段（进度库被静默清空）。损坏即跳过进度更新不覆写（文件不存在才合理建新）。
    prog_path = db / "进度.json"
    progress = None
    if prog_path.exists():
        try:
            progress = json.loads(prog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.info("[ERROR] 进度.json 损坏，跳过进度更新避免清空进度库（请修复 JSON 后重试）")
            summary["warnings"].append("进度.json 损坏，进度未更新")
    else:
        progress = {}
    if isinstance(progress, dict):
        progress["completed"] = max(progress.get("completed", 0), ch)
        progress["current"] = progress["completed"] + 1
        save_json(prog_path, progress)
        summary["applied"].append(f"进度: completed={progress['completed']}")

    # --- 地图（角色移动 + 新地点）---
    loc_data = load_json(db / "地图.json", {"locations": [], "character_positions": {}})
    for mov in changes.get("character_movements", []):
        c = mov.get("character")
        to = mov.get("to")
        if c and to:
            loc_data.setdefault("character_positions", {})[c] = to
            summary["applied"].append(f"位置: {c} → {to}")
    for lc in changes.get("location_changes", []):
        loc_id = lc.get("location_id")
        if loc_id:
            for l in loc_data.get("locations", []):
                if l.get("id") == loc_id:
                    l["status"] = lc.get("new_status", l.get("status"))
                    break
    save_json(db / "地图.json", loc_data)

    # --- 时间线 ---
    ta = changes.get("time_advance") or {}
    if ta:
        tl = load_json(db / "时间线.json", {})
        if isinstance(tl.get("current_time"), dict) and ta.get("period"):
            tl["current_time"]["period"] = ta["period"]
            tl["current_time"]["chapter"] = ch
        # 🔴 2026-06-27 C11（cluster 级幂等去重）：time_advance 同样被 split_cluster_changes
        # 平铺进每章，逐章 apply 让 time_log 同一 elapsed/key_events 累积 N 条。按
        # (elapsed,key_events,_source_cluster) 去重——同 cluster 内同一时间推进只记一次。
        src_cid, _src_inferred = _resolve_cluster(root, ch)
        elapsed = ta.get("elapsed", "")
        key_events = ta.get("key_events", [])
        log = tl.setdefault("time_log", [])
        _dup = any(
            isinstance(e, dict)
            and e.get("elapsed", "") == elapsed
            and e.get("key_events", []) == key_events
            and e.get("_source_cluster") == src_cid
            for e in log
        )
        if _dup:
            summary["applied"].append("时间线: 已推进(去重跳过)")
        else:
            log.append({
                "ch": ch, "elapsed": elapsed,
                "key_events": key_events,
                "_source_cluster": src_cid,  # 🔴 2026-06-27 C11 幂等去重键
            })
            summary["applied"].append("时间线: 已推进")
        save_json(db / "时间线.json", tl)

    # --- 道具（item_transfers）---
    items_data = load_json(db / "道具.json", {"items": []})
    for t in changes.get("item_transfers", []):
        for it in items_data.get("items", []):
            if it.get("name") == t.get("item") or it.get("id") == t.get("item"):
                it["holder"] = t.get("to", it.get("holder"))
                if t.get("new_status"):
                    it["status"] = t["new_status"]
                summary["applied"].append(f"道具: {it.get('name')} → {t.get('to')}")
                break
    save_json(db / "道具.json", items_data)

    # --- 输出 summary ---
    out = db / ".wal" / f"第{ch}章_applied.json"
    save_json(out, summary)
    logger.info(f"[APPLY] 完成 {len(summary['applied'])} 项变更"
          + (f", {len(summary['warnings'])} 条警告" if summary["warnings"] else ""))
    for w in summary["warnings"]:
        logger.info(f"  ⚠️ {w}")
    return 0


# ============ Git commit ============

def cmd_git_commit(root: Path, ch: int):
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("[GIT] git 不可用，跳过")
        return
    if not (root / ".git").exists():
        logger.info("[GIT] 非 git 仓库，跳过")
        return

    f = find_chapter_file(root, ch)
    if not f:
        logger.info(f"[GIT] 第{ch}章 txt 未找到")
        return

    # v18：正文经 cio 剥离 CHANGES，字数用统一口径
    body = cio.read_body(root, ch)
    words = cio.count_words(body)
    title_match = re.search(r"第\d+章[_·]?(.+?)\.txt", f.name)
    title = title_match.group(1) if title_match else ""
    # v27 修复：缩进/作用域 bug —— 原代码 prog/scenes 只在 if 块里定义，下面 for 循环
    # 在 if 块外引用导致 title 非空分支会 NameError。重构为 if 块内闭环。
    if not title:
        prog = load_json(root / "_数据库" / "进度.json", {})
        _all_scenes_save_state = []
        # 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测），
        # 裸 .items() 会 AttributeError 崩。先 normalize_blueprint 归一成 dict 再迭代。
        for cid, cdata in cluster_lookup.normalize_blueprint(prog).items():
            _all_scenes_save_state.extend(cdata.get("scene_storyboard", []))
        for cp in _all_scenes_save_state:
            if cp.get("ch") == ch:
                title = cp.get("title", "")
                break

    try:
        # 把正文 txt + 同目录 _changes.json（若存在）一并纳入快照
        add_paths = [str(f.relative_to(root)), "_数据库/"]
        cp_file = cio.changes_path(root, ch)
        if cp_file.is_file():
            add_paths.insert(1, str(cp_file.relative_to(root)))
        # v27 修复：git 操作加 timeout=30 防 session 阻塞（feedback: 大仓库 git add 可能卡几分钟）
        subprocess.run(["git", "add", *add_paths], cwd=root, check=True, timeout=30)
        msg = f"feat(ch-{ch}): {title} ({words}字)"
        subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True,
                       capture_output=True, timeout=30)
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=10)
        logger.info(f"[GIT] 快照 {result.stdout.strip()}: {msg}")
    except subprocess.TimeoutExpired:
        logger.info(f"[GIT] commit 超时 (>30s)·跳过本次快照·不阻断流水线")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="ignore")[:200]
        logger.info(f"[GIT] commit 失败: {err}")


# ============ 报告（第10步）============
# 🔴 2026-05-29 流程贯通（断点 5 死代码清理）：cmd_report（章级报告）已删除。
# 与 cmd_report_cluster（见 CLI 段说明）一起下线 —— grep 确认无任何 plan/命令文档
# 调 save_state.py --report / --report-cluster（cluster-save-state 报告由其他步骤产出）。


# ============ v22.6 自动 post-reflect 链 ============

def cmd_auto_post_reflect(root: Path, ch: int) -> None:
    """v22.6: save-state plan step 8 的 learning-loop 三步链自动化。

    解决断层：reflector agent 写报告 → 之前需主代理手动调 learning_loop --merge-reflection。
    主代理常忘 → 写作经验.json success_patterns/failure_patterns 一直 0。

    本命令自动跑：
    1. learning_loop --merge-reflection <reflector json>
    2. learning_loop --ingest <audit json>
    3. learning_loop --scan-recurring
    """
    import subprocess

    project_str = str(root)
    script_root = scripts_dir()   # frozen-aware（狩猎修·__file__在PYZ顶层）
    learning_loop = script_root / "learning_loop.py"

    reflector_json = root / "_数据库" / ".judge_reports" / f"ch_{ch:03d}_reflector.json"
    audit_json = root / "_数据库" / ".audit" / f"ch_{ch:03d}_audit.json"

    steps_ran = 0
    steps_skipped = 0

    # Step 1: merge reflector → 写作经验.success/failure_patterns
    if reflector_json.is_file():
        rel_path = reflector_json.relative_to(root).as_posix()
        r = subprocess.run(
            [child_python(), str(learning_loop), project_str, "--merge-reflection", rel_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180  # 2026-05-30 北极星：补 timeout 纪律（防 learning_loop 异常慢卡死流水线）
        )
        if r.returncode == 0:
            logger.info(f"[auto-post-reflect] step 1/3 merge-reflection OK")
            for line in (r.stdout or "").splitlines()[-3:]:
                logger.info(f"  {line}")
            steps_ran += 1
        else:
            logger.info(f"[auto-post-reflect] step 1/3 merge-reflection FAIL: {r.stderr[:200]}")
            steps_skipped += 1
    else:
        logger.info(f"[auto-post-reflect] step 1/3 跳过：reflector 报告不存在 ({reflector_json.name})")
        steps_skipped += 1

    # Step 2: ingest audit → _recurrence_tracker / _waiver_tracker
    if audit_json.is_file():
        rel_path = audit_json.relative_to(root).as_posix()
        r = subprocess.run(
            [child_python(), str(learning_loop), project_str, "--ingest", rel_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180  # 2026-05-30 北极星：补 timeout 纪律（防 learning_loop 异常慢卡死流水线）
        )
        if r.returncode in (0, 1):  # 1 = 检测到复发问题，不是错
            logger.info(f"[auto-post-reflect] step 2/3 ingest OK (rc={r.returncode})")
            for line in (r.stdout or "").splitlines()[-3:]:
                logger.info(f"  {line}")
            steps_ran += 1
        else:
            logger.info(f"[auto-post-reflect] step 2/3 ingest FAIL: {r.stderr[:200]}")
            steps_skipped += 1
    else:
        logger.info(f"[auto-post-reflect] step 2/3 跳过：audit 报告不存在 ({audit_json.name})")
        steps_skipped += 1

    # Step 3: scan-recurring → 跨章复发追踪 + tool_calibration_suggestions
    r = subprocess.run(
        [child_python(), str(learning_loop), project_str, "--scan-recurring"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180  # 2026-05-30 北极星：补 timeout 纪律
    )
    if r.returncode in (0, 1):
        logger.info(f"[auto-post-reflect] step 3/3 scan-recurring OK (rc={r.returncode})")
        for line in (r.stdout or "").splitlines()[-3:]:
            logger.info(f"  {line}")
        steps_ran += 1
    else:
        logger.info(f"[auto-post-reflect] step 3/3 scan-recurring FAIL: {r.stderr[:200]}")
        steps_skipped += 1

    logger.info(f"\n[auto-post-reflect] 完成 {steps_ran}/3 步（跳过 {steps_skipped}）")


# ============ v23 ECAS checkpoint ============

def _read_ecas_metadata(chg: dict) -> dict:
    """2026-05-29 复审修复 [M17]：ecas_metadata 三方位置不一致——
    schema 声明顶层 / gen_writer 写在 self_eval.ecas_metadata（见 gen_writer.py:587）/
    旧 reader 只读顶层 → 永远拿空 dict → checkpoint_data 永远空。
    本 helper 统一读：优先 self_eval.ecas_metadata（writer 实际位置），兜底顶层（schema 位置），
    两处都有则浅合并（self_eval 优先，反映 writer 真实输出）。读不到返回 {}。
    """
    if not isinstance(chg, dict):
        return {}
    top = chg.get("ecas_metadata") if isinstance(chg.get("ecas_metadata"), dict) else {}
    se = chg.get("self_eval") if isinstance(chg.get("self_eval"), dict) else {}
    nested = se.get("ecas_metadata") if isinstance(se.get("ecas_metadata"), dict) else {}
    # 顶层做底、self_eval 覆盖（writer 真实写入位置优先）
    merged = dict(top)
    merged.update(nested)
    return merged


def cmd_ecas_checkpoint(root: Path, cluster_id: str) -> int:
    """v23 ECAS: 验证 cluster_draft 完整性 (字数 / sub_summaries / mid_checkpoint_results)。

    输入: cluster_id (如 cluster_002)
    检查项:
    1. 读 章节/cluster_<id>_draft/cluster_<id>_draft.txt 验字数 in expected_word_range
    2. 读 章节/cluster_<id>_draft/cluster_<id>_changes.json.ecas_metadata.checkpoint_data
    3. 验所有 mid_checkpoint_results 字段完整 + passed
    4. 写 _数据库/.ecas_checkpoints/cluster_<id>_final.json (汇总)

    2026-05-29 复审修复 [L12]：freestyle（writer_mode=freestyle_v27 / chapter_count_decided_by_splitter）
    时字数由 writer 自由发挥、splitter 后期按字数切——硬卡 expected_word_range 与 freestyle 设计冲突，
    故 freestyle 时 [4000,20000] 等硬范围降级为 advisory（仅 warn 不 FAIL）。
    2026-05-29 复审修复 [H3/M20]：返回状态码（0 PASS / 1 FAIL）替代 sys.exit，供 main() 传播。
    """
    import json as _json

    cluster_dir = root / "章节" / f"{cluster_id}_draft"
    draft_path = cluster_dir / f"{cluster_id}_draft.txt"
    changes_path = cluster_dir / f"{cluster_id}_changes.json"
    clusters_path = root / "_数据库" / "事件簇.json"
    checkpoint_dir = root / "_数据库" / ".ecas_checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "cluster_id": cluster_id,
        "validated_at": datetime.now().isoformat(),
        "checks": {},
        "passed": True,
        "warnings": []
    }

    # 2026-05-29 复审修复 [M17/L12]：先读 changes.json 一次——既供 freestyle 判定（L12），
    # 也复用给 Check 2（避免二次读盘）。ecas_metadata 经 _read_ecas_metadata 兼容三方位置。
    chg = None
    ecas_meta = {}
    if changes_path.is_file():
        try:
            chg = _json.loads(changes_path.read_text(encoding="utf-8"))
            ecas_meta = _read_ecas_metadata(chg)
        except Exception:
            chg = None  # Check 2 会重新尝试并记录具体错误
    is_freestyle = (ecas_meta.get("writer_mode") == "freestyle_v27"
                    or bool(ecas_meta.get("chapter_count_decided_by_splitter")))
    result["checks"]["writer_mode"] = ecas_meta.get("writer_mode", "unknown")

    # Check 1: draft 存在 + 字数
    if not draft_path.is_file():
        result["passed"] = False
        result["checks"]["draft_exists"] = False
        result["warnings"].append(f"draft 文件不存在: {draft_path}")
    else:
        text = draft_path.read_text(encoding="utf-8")
        # v23.1: 中文字符数（CJK 统一汉字 + 扩展 A）— 与 writer / 用户偏好.target_words 一致
        # 旧算法 len(text 去 \n 空格) 把标点/英文也算进去导致虚高 20%+
        words = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
        result["checks"]["draft_exists"] = True
        result["checks"]["word_count"] = words
        result["checks"]["word_count_method"] = "CJK_chars_only_v23_1"

        # v23.2 L5: 字数 vs expected_word_range — JSON 读失败/字数不达标 = FAIL（不再 warn 静默通过）
        if clusters_path.is_file():
            try:
                cdata = _json.loads(clusters_path.read_text(encoding="utf-8"))
                cluster = next((c for c in cdata.get("clusters", []) if c.get("cluster_id") == cluster_id), None)
                if cluster:
                    wr = cluster.get("expected_word_range") or {}
                    wmin, wmax = wr.get("min", 4000), wr.get("max", 20000)
                    result["checks"]["expected_word_range"] = [wmin, wmax]
                    if words < wmin or words > wmax:
                        # 2026-05-29 复审修复 [L12]：freestyle 时字数硬卡降级为 advisory（不 FAIL）
                        if is_freestyle:
                            result["warnings"].append(
                                f"ADVISORY(freestyle): CJK 字数 {words} 在 expected_word_range "
                                f"[{wmin}, {wmax}] 之外——freestyle 由 splitter 按字数切，仅提示不卡")
                        else:
                            result["passed"] = False  # v23.2: 非 freestyle 仍 FAIL
                            result["warnings"].append(f"FAIL: CJK 字数 {words} 超出 expected_word_range [{wmin}, {wmax}]")
                    else:
                        result["checks"]["word_count_in_range"] = True
                else:
                    # cluster 找不到 = FAIL（防 brief 缺失静默通过）
                    result["passed"] = False
                    result["warnings"].append(f"FAIL: cluster_id {cluster_id} 在 事件簇.json 找不到")
            except Exception as e:
                # v23.2: JSON 读失败 = FAIL（不再静默通过）
                result["passed"] = False
                result["warnings"].append(f"FAIL: 读 事件簇.json 失败（fail-loud v23.2）: {e}")
        else:
            result["passed"] = False
            result["warnings"].append("FAIL: 事件簇.json 不存在 - ECAS cluster brief 缺失")

    # Check 2: changes.json + checkpoint_data
    if not changes_path.is_file():
        result["passed"] = False
        result["checks"]["changes_exists"] = False
        result["warnings"].append(f"changes.json 不存在: {changes_path}")
    else:
        try:
            if chg is None:
                chg = _json.loads(changes_path.read_text(encoding="utf-8"))
            # 2026-05-29 复审修复 [M17]：经 _read_ecas_metadata 兼容 self_eval/顶层两种位置
            ecas_meta = _read_ecas_metadata(chg)
            ckp_data = ecas_meta.get("checkpoint_data") or {}
            ckp_results = ckp_data.get("mid_checkpoint_results") or []
            result["checks"]["changes_exists"] = True
            result["checks"]["checkpoint_count"] = len(ckp_results)
            failed_ckp = [c for c in ckp_results if not c.get("passed")]
            result["checks"]["all_checkpoints_passed"] = len(failed_ckp) == 0
            if failed_ckp:
                result["warnings"].append(f"{len(failed_ckp)} 个 checkpoint 失败")
            # cluster_word_total vs ecas_meta
            cluster_wt = ecas_meta.get("cluster_word_total")
            if cluster_wt and "word_count" in result["checks"]:
                if abs(cluster_wt - result["checks"]["word_count"]) > 100:
                    result["warnings"].append(f"changes.cluster_word_total ({cluster_wt}) 与正文字数 ({result['checks']['word_count']}) 偏差 > 100")
        except Exception as e:
            result["passed"] = False
            result["warnings"].append(f"读 changes.json 失败: {e}")

    # Write final checkpoint summary
    # 2026-06-12 任务C：_数据库/.ecas_checkpoints/*.json 也是数据库 JSON，
    # 裸 write_text → 统一改走 save_json（原子写·格式与原 ensure_ascii=False/indent=2 一致）
    final_path = checkpoint_dir / f"{cluster_id}_final.json"
    save_json(final_path, result)
    logger.info(f"[ecas-checkpoint] {cluster_id}: {'PASS' if result['passed'] else 'FAIL'}")
    for k, v in result["checks"].items():
        logger.info(f"  {k}: {v}")
    if result["warnings"]:
        logger.info(f"  warnings: {len(result['warnings'])}")
        for w in result["warnings"]:
            logger.info(f"    - {w}")
    logger.info(f"  summary: {final_path.relative_to(root)}")
    # 2026-05-29 复审修复 [H3/M20]：返回状态码（1 FAIL / 0 PASS）替代 sys.exit，供 main() 传播
    return 1 if not result["passed"] else 0


# ============ CLI ============

def _get_cluster_chapter_range(project_root, cluster_key):
    """拿 cluster 的 chapter_range，展开 [ch1,...,chN]。

    🔴 2026-06-17 bug-hunt 修：改走 `cluster_lookup.cluster_id_to_range`（唯一权威反查·北极星①·
    享 blueprint 兜底）。原实现只读 事件簇.json 无兜底 → blueprint-only 状态（事件簇缺 chapter_range·
    进度.json.cluster_blueprint 有）返 [] → cmd_apply_cluster_changes / cmd_git_commit_cluster
    FATAL exit2 卡死整条 cluster-save-state 管线（而兄弟脚本 evaluators 走 cluster_lookup 正常推进·
    三脚本权威源不一致）。对齐 evaluators。"""
    try:
        import cluster_lookup as _cl
        cid = _cl.normalize_cluster_id(cluster_key) or \
            f"cluster_{str(cluster_key).replace('cluster_', '')}"
        cr = _cl.cluster_id_to_range(project_root, cid)
        if cr and len(cr) == 2:
            return list(range(int(cr[0]), int(cr[1]) + 1))
    except Exception:
        pass
    return []


def _run_writer_truth_check(root: Path, chapters: list[int]) -> dict:
    """2026-05-29 流程贯通（断点 5）：apply 后对整 cluster 跑 writer 撒谎检测。

    cluster-save-state.md:103 / plan:49 声称 --apply-cluster-changes 内部跑 writer_truth_check
    检测「writer 声明 X 但正文实际 Y」，但旧 cmd_apply_cluster_changes 从不调用 → 失效承诺。

    🔴 2026-06-27 C11：改 cluster 级一次调用（--cluster-chapters），取代旧逐章循环。
    根因：split_cluster_changes v1 把整 cluster 的 applied_style（opening_line/ending_line/
    anchors_hit 是 cluster 级一次性申报）平铺进每章 → 逐章验时 chapters[1..N] 的章首/章末
    各异、anchor 散落各章 → 海量假 opening_line/ending_line 不匹配 + 假 missing anchor。
    cluster 级：opening 只验首章 / ending 只验末章 / anchors 验全拼接 body（见 writer_truth_check
    .truth_check_cluster）。失败不中断流水线（记录即可），结果并入返回 summary。
    """
    wtc = scripts_dir() / "writer_truth_check.py"  # frozen: __file__在PYZ顶层·parent指_internal根（狩猎修）
    result = {"ran": 0, "lies_total": 0, "per_chapter": [], "errors": []}
    if not wtc.is_file():
        result["errors"].append("writer_truth_check.py 不存在")
        return result
    if not chapters:
        return result
    try:
        r = subprocess.run(
            [child_python(), str(wtc), str(root), "--cluster-chapters",
             ",".join(str(c) for c in chapters), "--write-back"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        # 退出码契约：0 通过 / 1 撒谎命中 / 2 致命
        result["ran"] = 1  # cluster 级一次调用（=1 次检测，覆盖 len(chapters) 章）
        result["chapters_checked"] = list(chapters)
        if r.returncode == 1:
            # 从 stdout 抓「[Total] cluster N 章 / 共 M 条撒谎」
            m = re.search(r"共\s*(\d+)\s*条撒谎", r.stdout or "")
            result["lies_total"] = int(m.group(1)) if m else 1
        elif r.returncode == 2:
            result["errors"].append((r.stderr or "")[:160])
        # 🔴 2026-06-27 SYS-3/C10（shadow）：从 stdout 抓声明-vs-正文 advisory 计数 → 列主代理待裁决。
        # SHADOW 决策：C10 corroboration/quarantine 先以 advisory 跑（只 surface 不延迟写账本），确认
        # 无假阳再 active（届时把 corroborated!=true 的 factual 项 defer-write 防脏账本）。
        _nt = re.search(r"SYS-3 申报兑现但正文 0 痕迹：(\d+)", r.stdout or "")
        _uc = re.search(r"FACTUAL_CLAIM_UNCORROBORATED（弱信号·advisory）：(\d+)", r.stdout or "")
        result["foreshadowing_no_trace"] = int(_nt.group(1)) if _nt else 0
        result["factual_uncorroborated"] = int(_uc.group(1)) if _uc else 0
    except Exception as e:
        # 失败不中断（记录即可）
        result["errors"].append(f"cluster truth-check: {type(e).__name__}: {str(e)[:120]}")
    return result


def _writeback_cluster_progress(root, cluster_key, chapters):
    """🔴 2026-06-27 P0(审计 sediment)：cluster 级回写 进度.json 元数据。

    根因：原 save_state 只在 per-chapter apply 写 `completed`(章数)·从不写 current_cluster/book_title·
    导致 4 次 save-state 后 current_cluster 仍卡 cluster_001、book_title 仍占位「<书名>」→ /continue
    误判从 cluster_001 续起、/export 拿占位书名。本 helper 在 cluster apply 后补齐这些字段。
    """
    try:
        prog_path = root / "_数据库" / "进度.json"
        if not prog_path.exists():
            return
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
        if not isinstance(prog, dict):
            return
        # current_cluster：规范化 id
        try:
            import cluster_lookup as _cl
            cid = _cl.normalize_cluster_id(cluster_key) or f"cluster_{str(cluster_key).replace('cluster_', '')}"
        except Exception:
            cid = f"cluster_{str(cluster_key).replace('cluster_', '')}"
        prog["current_cluster"] = cid
        # completed：取 max(现值, cluster 末章)
        if chapters:
            prog["completed"] = max(int(prog.get("completed", 0) or 0), max(chapters))
            prog["current"] = prog["completed"] + 1
        # book_title：仍占位则用项目目录名兜底
        bt = str(prog.get("book_title") or "")
        if (not bt) or bt.startswith("<") or bt == "<书名>":
            prog["book_title"] = Path(root).name
        prog["last_updated"] = datetime.now().isoformat(timespec="seconds")
        save_json(prog_path, prog)
        logger.info(f"[进度回写] current_cluster={cid} completed={prog.get('completed')} book_title={prog['book_title']}")
    except Exception as e:
        logger.info(f"[进度回写] 跳过(不阻断): {type(e).__name__}: {str(e)[:120]}")


def _mark_cluster_me_completed(root, cluster_key):
    """🔴 2026-06-28：cluster 写完 → 标记其 parent_me status=completed + 补 ME_to_advance。

    治内容状态一致性双 bug：① volume_arc 建的 cluster 只有 parent_me 无 ME_to_advance、choice_apply
    建的两者都有 → 两路 ME 映射字段不一致；② 大势卡 ME.status 永不从 pending 更新 → 卷进度/完成检测
    失真。emergence find_remaining_mes 以 ME.status=='completed' 为**权威排除信号**(line 66)，此处维护它，
    使 ME.status 成单一真理源（不再只靠 ME_to_advance 派生的 completed_mes）。幂等：已 completed 跳过。
    """
    try:
        db = root / "_数据库"
        ec_path = db / "事件簇.json"
        ds_path = db / "大势卡.json"
        if not ec_path.exists() or not ds_path.exists():
            return
        import cluster_lookup as _cl
        cid = _cl.normalize_cluster_id(cluster_key) or str(cluster_key)
        ec = load_json(ec_path, {})
        cluster = next((c for c in ec.get("clusters", [])
                        if _cl.normalize_cluster_id(c.get("cluster_id")) == cid
                        or str(c.get("cluster_id")) == cid), None)
        if not cluster:
            return
        me_id = cluster.get("parent_me") or cluster.get("me_id")
        if not me_id:
            return
        adv = list(cluster.get("ME_to_advance") or [])
        if me_id not in adv:
            cluster["ME_to_advance"] = adv + [me_id]
            save_json(ec_path, ec)
        ds = load_json(ds_path, {})
        changed = False
        for m in ds.get("major_events", []):
            mid = m.get("id") or m.get("me_id")
            if mid == me_id and m.get("status") != "completed":
                m["status"] = "completed"
                m["completed_by_cluster"] = cid
                changed = True
        if changed:
            save_json(ds_path, ds)
            logger.info(f"[ME完成] {me_id} status=completed (by {cid})")
    except Exception as e:
        logger.info(f"[ME完成] 跳过(不阻断): {type(e).__name__}: {str(e)[:120]}")


def _persist_cluster_locked_facts(root, cluster_key):
    """🔴 2026-06-28：把 writer changes.factual.facts_locked/locked_facts 写进 事件簇.clusters[].locked_facts。

    根因：save_state apply 原**完全不处理 locked_facts**（grep 零命中）→ writer 报的硬事实（cluster_002
    实测 4 条：霍华德左手/孩童骨头/瘸腿男孩失踪/遗嘱墨迹变淡）从不落地 → 后续 cluster 无从查矛盾、
    LOCKED_FACT_CROSS_SCENE 形同虚设。事件簇.clusters[].locked_facts 是 locked_fact_cross_scene_scanner
    + build_manifest 读取的权威源。兼容模型实用字段名 facts_locked（自查项名）与 locked_facts。dedup by fact。
    """
    try:
        db = root / "_数据库"
        ec_path = db / "事件簇.json"
        if not ec_path.exists():
            return
        import cluster_lookup as _cl
        cid = _cl.normalize_cluster_id(cluster_key) or str(cluster_key)
        key3 = str(cluster_key).replace("cluster_", "")
        chg_path = root / "章节" / f"cluster_{key3}_draft" / f"cluster_{key3}_changes.json"
        if not chg_path.exists():
            return
        chg = load_json(chg_path, {})
        fac = chg.get("factual", chg) if isinstance(chg, dict) else {}
        raw = fac.get("locked_facts") or fac.get("facts_locked") or []
        norm = []
        for lf in raw:
            if isinstance(lf, dict) and (lf.get("fact") or lf.get("description")):
                norm.append({"fact": lf.get("fact") or lf.get("description"),
                             "subject": lf.get("subject", ""), "_cluster": cid})
            elif isinstance(lf, str) and lf.strip():
                norm.append({"fact": lf.strip(), "subject": "", "_cluster": cid})
        if not norm:
            return
        ec = load_json(ec_path, {})
        cluster = next((c for c in ec.get("clusters", [])
                        if _cl.normalize_cluster_id(c.get("cluster_id")) == cid
                        or str(c.get("cluster_id")) == cid), None)
        if cluster is None:
            return
        existing = cluster.setdefault("locked_facts", [])
        seen = {(e.get("fact") if isinstance(e, dict) else e) for e in existing}
        added = 0
        for lf in norm:
            if lf["fact"] not in seen:
                existing.append(lf)
                seen.add(lf["fact"])
                added += 1
        if added:
            save_json(ec_path, ec)
            logger.info(f"[locked_facts] {cid} 落地 {added} 条硬事实 → 事件簇.clusters[].locked_facts")
    except Exception as e:
        logger.info(f"[locked_facts] 跳过(不阻断): {type(e).__name__}: {str(e)[:120]}")


def _apply_foreshadower_payoffs(root, cluster_key):
    """🔴 2026-06-28：foreshadower JudgeReport 的 payoff 检测桥接到伏笔表 resolution。

    根因：apply 只处理 writer changes.foreshadowing_paid（模型常漏报·cluster_006 实测 paid=0），
    foreshadower 从正文检测到的 payoff（FS_012 paid_terminal）流不进伏笔表 → 伏笔恒不 resolved、
    foreshadow_rhythm 失衡。foreshadower 是正文 payoff 的权威检测器（比 writer 自报可靠）。
    桥接：读 cluster_<key>_foreshadower.json 的 payoff_scores —— verdict=paid_terminal+score>0 →
    resolved=True；paid_progressive → 记 payoff_progress（不 resolved）。幂等：已 resolved 不后移。
    报告缺失（foreshadower 未跑）→ 静默跳过（apply 前调用是常态·foreshadower 跑完需再调本桥）。
    """
    try:
        db = root / "_数据库"
        import cluster_lookup as _cl
        cid = _cl.normalize_cluster_id(cluster_key) or str(cluster_key)
        rpt = db / ".judge_reports" / f"{cid}_foreshadower.json"
        if not rpt.is_file():
            return
        scores = (load_json(rpt, {}).get("specific_findings") or {}).get("payoff_scores", []) or []
        if not scores:
            return
        fs_path = db / "伏笔表.json"
        fs = load_json(fs_path, {})
        by_id = {p.get("id"): p for p in fs.get("promises", []) if isinstance(p, dict)}
        chapters = _get_cluster_chapter_range(root, cluster_key) or []
        last_ch = max(chapters) if chapters else None
        changed = 0
        for it in scores:
            if not isinstance(it, dict):
                continue
            p = by_id.get(it.get("fs_id"))
            try:
                score = int(it.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            if not p or score <= 0:
                continue
            verdict = str(it.get("verdict", ""))
            if it.get("terminal") is True or verdict == "paid_terminal":
                if not p.get("resolved"):
                    p["resolved"] = True
                    p["resolved_at_ch"] = last_ch
                    p["_resolved_by"] = "foreshadower"
                    changed += 1
            elif "progressive" in verdict or it.get("terminal") is False:
                prog = p.setdefault("payoff_progress", [])
                if cid not in prog:
                    prog.append(cid)
                    changed += 1
        if changed:
            save_json(fs_path, fs)
            logger.info(f"[foreshadower-payoff] {cid} 桥接 {changed} 条 payoff → 伏笔表"
                        "（terminal→resolved / progressive→progress）")
    except Exception as e:
        logger.info(f"[foreshadower-payoff] 跳过(不阻断): {type(e).__name__}: {str(e)[:120]}")


def cmd_apply_cluster_changes(root, cluster_key):
    """v24 cluster 级 apply-changes：展开 cluster chapter_range，for each ch 调 apply_changes。

    2026-05-29 流程贯通（断点 5）：apply 后跑 writer_truth_check（撒谎检测）并入 summary。
    2026-05-29 复审修复 [M3]：单章 parse/apply 失败不再 sys.exit 中断整 cluster——
    cmd_parse/apply_changes 改返回状态码，本函数逐章累计 per_chapter_status；truth-check + 写盘
    放 finally 保证任何单章异常后仍落地 summary。返回 0 成功 / 2 整 cluster 失败（无章）。
    """
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        # 🔴 2026-06-26 fail-fast 走 stderr+flush（同 gen_writer/gen_fixer 修法）
        sys.stderr.write(f"[FATAL save_state] cluster {cluster_key} 未找到 chapter_range\n")
        sys.stderr.flush()
        return 2

    per_chapter_status = []
    failed_chapters = []
    try:
        logger.info(f"[cluster {cluster_key}] 展开 {len(chapters)} 章 → 逐章 apply-changes")
        for ch in chapters:
            logger.info(f"  → ch{ch}")
            # 单章失败（含未捕获异常）记录后继续下一章，不中断整 cluster
            try:
                prc = cmd_parse(root, ch)
                arc = apply_changes(root, ch) if prc == 0 else None
                status = {"ch": ch, "parse_rc": prc, "apply_rc": arc}
                if prc != 0 or (arc is not None and arc != 0):
                    failed_chapters.append(ch)
            except Exception as e:
                status = {"ch": ch, "error": f"{type(e).__name__}: {str(e)[:160]}"}
                failed_chapters.append(ch)
                logger.info(f"  ⚠️ ch{ch} apply 异常（已记录·不中断）: {status['error']}")
            per_chapter_status.append(status)
        ok_count = len(chapters) - len(failed_chapters)
        logger.info(f"[OK] cluster {cluster_key} apply-changes 完成 {ok_count}/{len(chapters)} 章"
              + (f"（{len(failed_chapters)} 章失败: {failed_chapters}）" if failed_chapters else ""))

        # 🔴 2026-06-27 P0：cluster 级回写 进度.json 元数据（current_cluster/book_title/completed）
        _writeback_cluster_progress(root, cluster_key, chapters)
        # 🔴 2026-06-28：标记 parent_me status=completed + 补 ME_to_advance（内容状态一致性）
        _mark_cluster_me_completed(root, cluster_key)
        # 🔴 2026-06-28：locked_facts 落地 事件簇.clusters[]（后续 cluster 查矛盾的权威源）
        _persist_cluster_locked_facts(root, cluster_key)
        # 🔴 2026-06-28：foreshadower payoff 桥接伏笔表 resolution（foreshadower 跑完后 re-apply 生效）
        _apply_foreshadower_payoffs(root, cluster_key)

        # writer 撒谎检测（apply 落地后跑 · 失败不中断 · 结果并入 summary 写盘）
        # 🔴 2026-06-27 C11：cluster 级一次检测（opening 验首章 / ending 验末章 / anchors 验全拼接）
        truth = _run_writer_truth_check(root, chapters)
        if truth["lies_total"] > 0:
            logger.info(f"[truth-check] 🔴 检测到 {truth['lies_total']} 条撒谎"
                  f"（writer 声明与正文不符 · cluster 级）")
        else:
            logger.info(f"[truth-check] ✅ cluster {len(chapters)} 章无撒谎"
                  + (f" · 检测异常（已记录·{truth['errors']}）" if truth["errors"] else ""))
        # 🔴 2026-06-27 SYS-3/C10（shadow）：声明-vs-正文 advisory 列主代理待裁决（不延迟写账本）
        _nt = truth.get("foreshadowing_no_trace", 0)
        _uc = truth.get("factual_uncorroborated", 0)
        if _nt or _uc:
            logger.info(f"[声明-vs-正文 · shadow] 🟡 待裁决：申报兑现但正文 0 痕迹 {_nt} 条 / "
                        f"factual 弱信号未印证 {_uc} 条（advisory·账本未延迟写·详见 故事块摘要.factual_corroboration）")
    finally:
        # 2026-05-29 复审修复 [M3]：truth-check + 写盘放 finally——任何异常后都落地 summary
        summary = {
            "cluster_id": cluster_key,
            "chapters": chapters,
            "applied_at": datetime.now().isoformat(timespec="seconds"),
            "per_chapter_status": per_chapter_status,
            "failed_chapters": failed_chapters,
            "writer_truth_check": locals().get("truth"),
        }
        out = root / "_数据库" / ".wal" / f"{cluster_key}_apply_cluster.json"
        save_json(out, summary)
        logger.info(f"[apply-cluster] summary → {out.name}")
    # 全部章失败 = 严重（exit 2），否则成功（单章失败已记录·不影响 cluster 流水线推进）
    return 2 if (failed_chapters and len(failed_chapters) == len(chapters)) else 0


def cmd_git_commit_cluster(root, cluster_key):
    """v24 cluster 级 git commit：1 个 cluster 1 个 commit"""
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        # 🔴 2026-06-26 fail-fast 走 stderr+flush（同 gen_writer/gen_fixer 修法）
        sys.stderr.write(f"[FATAL save_state] cluster {cluster_key} 未找到 chapter_range\n")
        sys.stderr.flush()
        return 2
    # 复用 cmd_git_commit 但 commit msg 改 cluster 级
    # v27 修复：cluster 级 git 也加 timeout 防 session 阻塞
    import subprocess as _sp
    try:
        _sp.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True, timeout=30)
        # 2026-05-30 北极星复审：cluster_key 含 cluster_ 前缀 → 原 feat(cluster-{cluster_key}) 产
        # feat(cluster-cluster_002) 双前缀。抽纯数字对齐规范 feat(cluster-NNN)。
        _cnum = "".join(ch for ch in str(cluster_key) if ch.isdigit()) or str(cluster_key)
        msg = f"feat(cluster-{_cnum}): {len(chapters)} 章 (ch{chapters[0]}-{chapters[-1]})"
        r = _sp.run(["git", "-C", str(root), "commit", "-m", msg],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode == 0:
            sha = r.stdout.split()[1].strip("]")[:7] if r.stdout else "?"
            logger.info(f"[GIT] cluster_{cluster_key} 快照 {sha}: {msg}")
        else:
            logger.info(f"[GIT] {r.stderr[:200] or r.stdout[:200]}")
    except _sp.TimeoutExpired:
        logger.info(f"[GIT] cluster_{cluster_key} commit 超时 (>30s)·跳过本次快照·不阻断流水线")
    except Exception as e:
        logger.info(f"[GIT] {e}")
    # 2026-05-29 复审修复 [H3/M20]：git 失败不中断流水线（项目规则「失败不中断，仅记录」），
    # 故成功/记录后均返回 0（唯一 fatal 是缺 chapter_range，已 return 2）。
    return 0


def cmd_auto_post_reflect_cluster(root, cluster_key):
    """v24 cluster 级 auto-post-reflect：跑 learning_loop 三步链（merge-reflection / ingest / scan-recurring）。

    2026-05-29 复审修复 [H12]：旧实现逐章调 cmd_auto_post_reflect，找
    `.judge_reports/ch_NNN_reflector.json`——但该文件无 producer（章级 reflector 已随
    chapter mode 废弃），cluster reflector 实际写 `.wal/<cluster_key>_reflection.json`
    （见 plan_tracker.py:594 novel-reflector cluster 分支）。链路断裂导致
    写作经验.json success/failure_patterns 永远 0。

    改为 cluster 级直连：
      1. learning_loop --merge-reflection .wal/<cluster_key>_reflection.json
      2. learning_loop --ingest .audit/cluster_<key>_audit.json（见 audit_hub.py:1399）
      3. learning_loop --scan-recurring
    退出码语义（SC-2）：返回 0 成功；reflection/audit 缺失只是软跳过（不算崩溃）。
    """
    db = root / "_数据库"
    # cluster_key 可能带或不带 cluster_ 前缀，归一化用于文件名匹配
    norm_cid = cluster_lookup.normalize_cluster_id(cluster_key) or cluster_key
    raw = cluster_key.replace("cluster_", "") if str(cluster_key).startswith("cluster_") else cluster_key

    learning_loop = scripts_dir() / "learning_loop.py"  # frozen-aware（狩猎修·exe下学习闭环静默不跑）
    if not learning_loop.is_file():
        logger.info(f"[auto-post-reflect-cluster] learning_loop.py 不存在·跳过")
        return 0

    # cluster reflection 文件候选（plan_tracker novel-reflector 用 cstr_variants 命名）
    refl_candidates = [
        db / ".wal" / f"{norm_cid}_reflection.json",
        db / ".wal" / f"cluster_{raw}_reflection.json",
        db / ".wal" / f"{cluster_key}_reflection.json",
        db / ".wal" / f"ch_cluster_{raw}_reflection.json",
    ]
    refl_path = next((p for p in refl_candidates if p.is_file()), None)

    # cluster audit 文件候选（audit_hub.py:1399 写 cluster_<key>_audit.json）
    audit_candidates = [
        db / ".audit" / f"cluster_{raw}_audit.json",
        db / ".audit" / f"{norm_cid}_audit.json",
        db / ".audit" / f"{cluster_key}_audit.json",
    ]
    audit_path = next((p for p in audit_candidates if p.is_file()), None)

    project_str = str(root)
    steps_ran = 0
    steps_skipped = 0

    # Step 1: merge cluster reflection → 写作经验.success/failure_patterns
    if refl_path:
        r = subprocess.run(
            [child_python(), str(learning_loop), project_str, "--merge-reflection",
             refl_path.relative_to(root).as_posix()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,  # 2026-05-30 北极星：补 timeout 纪律
        )
        if r.returncode == 0:
            logger.info(f"[auto-post-reflect-cluster] step 1/3 merge-reflection OK ({refl_path.name})")
            steps_ran += 1
        else:
            logger.info(f"[auto-post-reflect-cluster] step 1/3 merge-reflection FAIL: {(r.stderr or '')[:200]}")
            steps_skipped += 1
    else:
        logger.info(f"[auto-post-reflect-cluster] step 1/3 跳过：cluster reflection 报告不存在"
              f"（找过 {norm_cid}_reflection.json 等）")
        steps_skipped += 1

    # Step 2: ingest cluster audit → _recurrence_tracker / _waiver_tracker
    if audit_path:
        r = subprocess.run(
            [child_python(), str(learning_loop), project_str, "--ingest",
             audit_path.relative_to(root).as_posix()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,  # 2026-05-30 北极星：补 timeout 纪律
        )
        if r.returncode in (0, 1):  # 1 = 检测到复发问题，不是错
            logger.info(f"[auto-post-reflect-cluster] step 2/3 ingest OK ({audit_path.name}, rc={r.returncode})")
            steps_ran += 1
        else:
            logger.info(f"[auto-post-reflect-cluster] step 2/3 ingest FAIL: {(r.stderr or '')[:200]}")
            steps_skipped += 1
    else:
        logger.info(f"[auto-post-reflect-cluster] step 2/3 跳过：cluster audit 报告不存在"
              f"（找过 cluster_{raw}_audit.json 等）")
        steps_skipped += 1

    # Step 3: scan-recurring → 跨 cluster 复发追踪 + tool_calibration_suggestions
    r = subprocess.run(
        [child_python(), str(learning_loop), project_str, "--scan-recurring"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,  # 2026-05-30 北极星：补 timeout 纪律
    )
    if r.returncode in (0, 1):
        logger.info(f"[auto-post-reflect-cluster] step 3/3 scan-recurring OK (rc={r.returncode})")
        steps_ran += 1
    else:
        logger.info(f"[auto-post-reflect-cluster] step 3/3 scan-recurring FAIL: {(r.stderr or '')[:200]}")
        steps_skipped += 1

    logger.info(f"[auto-post-reflect-cluster] {cluster_key} 完成 {steps_ran}/3 步（跳过 {steps_skipped}）")
    return 0


# 🔴 2026-05-29 流程贯通（断点 5 死代码清理）：cmd_report_cluster 已删除
# （依赖已删的 cmd_report，且无 plan/命令文档调 --report-cluster）。


def main():
    # 🔴 v26: chapter-level CLI 已彻底废弃移除（--wal-start/-step/-end/--parse/--apply-changes/
    # --git-commit/--report/--auto-post-reflect 全部下线）。
    # 🔴 2026-05-29 流程贯通（断点 5 死代码清理）：cmd_wal_* / cmd_report / cmd_report_cluster
    # 函数本体已删除（无调用方）。仍保留的章级函数（cmd_parse / apply_changes / cmd_git_commit /
    # cmd_auto_post_reflect）是被 cluster 函数内部按章迭代复用的底层组件，不是公共 CLI。
    # 外部一律走 --apply-cluster-changes / --git-commit-cluster / --auto-post-reflect-cluster /
    # --build-cluster-summary / --ecas-checkpoint。
    ap = argparse.ArgumentParser(
        description="save_state.py · v26 cluster-only CLI"
    )
    ap.add_argument("project", help="项目路径")
    # v24 cluster-level subcommands（唯一 CLI 入口）
    ap.add_argument("--ecas-checkpoint", type=str, metavar="CLUSTER_ID",
                    help="验证 cluster_draft 完整性 + checkpoint_data")
    ap.add_argument("--apply-cluster-changes", type=str, metavar="CLUSTER_KEY",
                    help="v24: 一次性应用整 cluster 的 changes（内部展开 ch_range for each ch apply）")
    ap.add_argument("--git-commit-cluster", type=str, metavar="CLUSTER_KEY",
                    help="v24: 1 cluster 1 commit · msg = feat(cluster-NNN): N 章 (chX-chY)")
    ap.add_argument("--auto-post-reflect-cluster", type=str, metavar="CLUSTER_KEY",
                    help="v24: cluster 级 learning_loop")
    # 🔴 2026-05-29 流程贯通（断点 5）：--report-cluster 已删（无 plan/命令调用方）
    ap.add_argument("--build-cluster-summary", type=str, metavar="CLUSTER_KEY",
                    help="v2 账本: 把整 cluster 的富摘要预算写入 故事块摘要.json（走 cluster_summary_builder）")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if not root.exists():
        logger.info(f"项目路径不存在: {root}")
        sys.exit(2)

    # 2026-05-29 复审修复 [H3/M20]：原 dispatch 裸调用 cmd_*_cluster 丢弃返回码——
    # cmd_apply_cluster_changes/cmd_ecas_checkpoint 返回 2(FATAL)/1(FAIL) 时进程仍 exit 0
    # （谎报成功）。改为 rc = cmd_xxx(...); sys.exit(rc if isinstance(rc, int) else 0)，
    # 覆盖 ecas-checkpoint / apply / git_commit / auto_post_reflect / build-summary 全分支。
    rc = 0
    if args.ecas_checkpoint:
        rc = cmd_ecas_checkpoint(root, args.ecas_checkpoint)
    elif args.apply_cluster_changes:
        rc = cmd_apply_cluster_changes(root, args.apply_cluster_changes)
    elif args.git_commit_cluster:
        rc = cmd_git_commit_cluster(root, args.git_commit_cluster)
    elif args.auto_post_reflect_cluster:
        rc = cmd_auto_post_reflect_cluster(root, args.auto_post_reflect_cluster)
    elif args.build_cluster_summary:
        import cluster_summary_builder
        _res = cluster_summary_builder.build_cluster_summary(root, args.build_cluster_summary)
        if not _res.get("ok"):
            logger.info(f"[FAIL] build-cluster-summary {_res.get('cluster_id')} :: {_res.get('error')}")
            sys.exit(1)
        logger.info(f"[OK] 账本写入 {_res['cluster_id']} · 填章 {_res['chapters_filled']} · 总CJK {_res['word_count']}")
        rc = 0
    else:
        ap.print_help()
        sys.exit(1)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
