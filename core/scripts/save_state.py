#!/usr/bin/env python3
"""状态保存流水线的确定性入口。

CLI 子命令：
  --apply-cluster-changes <key>     整 cluster 落地 13 JSON + writer_truth_check 撒谎检测
  --git-commit-cluster <key>        1 cluster 1 commit
  --auto-post-reflect-cluster <key> cluster 级 learning_loop 三步链
  --build-cluster-summary <key>     富摘要预算写入 故事块摘要.json
  --detect-volume-boundary <key>    卷边界确定性检测（基于 ME 信号，仅报告）
  --apply-volume-summary <N>        卷级摘要回库（校验 source，幂等 upsert）

所有子命令都幂等（重复执行不产生副作用或破坏数据）。
确定性逻辑集中在此，AI 只负责：故事块摘要/写作反思/走向卡片。
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from frozen_util import child_python, scripts_dir  # frozen-aware path helpers
from datetime import datetime
from pathlib import Path

# 章节读写统一走 chapter_io。
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio
# 章号与 cluster_id 的反查统一走 cluster_lookup。
import cluster_lookup
# 伏笔状态与 payoff_scope 派生统一走 db_schema_validate。
import db_schema_validate as _dbsv
from log_util import get_logger

logger = get_logger(__name__)
# 2026-06-12 缺漏修复批次1 任务C【原子写改造·P1-1 数据损坏防护】：
# 写 _数据库/*.json 统一走 atomic_json.atomic_write_json（tmp 唯一名 + fsync + os.replace
# 原子语义——进程写一半被杀只残留 .tmp、绝不毁掉原文件）。此前 save_json 裸 write_text：
# 半截写 → 下个读者 json.JSONDecodeError → load_json 返回 default → 伏笔表/人物卡/
# 进度/地图/时间线/道具 整库静默清空。ImportError 时 save_json 使用同等 tmp+replace 写盘
# （照 cluster_choice_apply.py:89-96 范式）。
try:
    import atomic_json
except ImportError:  # 极端单文件执行环境缺 atomic_json；save_json 内执行同等 tmp+replace 写盘
    atomic_json = None


def _resolve_cluster(root: Path, ch: int) -> tuple[str, bool]:
    """把章号反查为真实 cluster_id。

    返回 (cluster_id, inferred)：
      - 反查命中 → (真实 cluster_id, False)
      - 反查不到 → 抛 RuntimeError，禁止按章号推断旧 cluster id。
    """
    cid = cluster_lookup.ch_to_cluster_id(root, ch)
    if cid:
        return cid, False
    raise RuntimeError(f"第{ch}章无法从事件簇/blueprint 反查 cluster_id")


def _require_cluster_id(cluster_key) -> str:
    """归一化外部传入的 cluster_key；非法值直接硬失败。"""
    cid = cluster_lookup.normalize_cluster_id(cluster_key)
    if not cid:
        raise RuntimeError(f"非法 cluster_key: {cluster_key!r}")
    return cid


# ═══════ 🔴 2026-07-07 S3 类级契约：LLM 抽取类载荷禁直接闭合终态 ═══════
# 借鉴 PlotPilot domain/evolution/reducer.py:97-108（ValueError("LLM cannot directly close
# narrative debts")·research/open_source_writing_systems_round2.md S3）。类级不变量：
#   · 伏笔/戏剧问题/债务类的「终态」（consumed/resolved/answered）只能由确定性入库层校验后写入。
#   · 抽取类载荷（writer changes.json / archivist archive.json）只许报 progress 级观察——携带
#     终态声明 → 入库层剥离 + stderr 显式警告 + WAL 留痕（terminal_state_stripped·不静默接受）。
#   · 终态唯一通路 = _apply_foreshadower_payoffs（伏笔 consumed）+ cmd_apply_dramatic_questions
#     （戏剧问题 answered），写入前校验目标状态 + 正文证据，不合格逐条拒绝（不整体崩），
#     拒绝明细进 WAL（record_terminal_contract）。
# 纪律：全部是写入层硬校验 + WAL 审计，不新增 hard_gate code（19 码三方一致不动）；
# suspended 被 terminal 回收仍为 logger 警示后照常落账（2026-07-06 三态迁移决定不变）。
TERMINAL_STATE_VALUES = frozenset({"consumed", "resolved", "answered"})
# 终态伴生字段（终态声明被剥离时一并剥·防残留半截终态）
_TERMINAL_COMPANION_KEYS = ("consumed_at_ch", "resolved_at_ch", "answered_at_ch",
                            "_consumed_by", "_resolved_by")


def strip_terminal_state_payload(payload, _path="$"):
    """递归剥离抽取类载荷（writer changes / archivist archive.json）中的终态声明。

    就地修改 payload，返回 (stripped_count, details)。只降级为 progress 级观察，不丢整条目：
      · dict["status"] ∈ TERMINAL_STATE_VALUES → 删 status（+伴生字段）
      · dict["resolved"] is True → 删 resolved（+伴生字段）
      · dict["terminal"] is True → 删 terminal
      · dict["kind"] / dict["type"] == "terminal" → 删该键（writer foreshadowing_paid 旧口径）
      · dict["answered"] 为非空 list（戏剧问题终态声明）→ 置 []
    不受影响（值不在剥离集）：角色 status(alive/dead)、ME status(completed)、
    secrets(hidden/revealed·明暗线隔离)、地点 status。
    """
    stripped = 0
    details = []
    if isinstance(payload, dict):
        st = payload.get("status")
        if isinstance(st, str) and st.strip().lower() in TERMINAL_STATE_VALUES:
            payload.pop("status", None)
            for k in _TERMINAL_COMPANION_KEYS:
                payload.pop(k, None)
            stripped += 1
            details.append({"path": _path, "field": "status", "value": st})
        if payload.get("resolved") is True:
            payload.pop("resolved", None)
            for k in _TERMINAL_COMPANION_KEYS:
                payload.pop(k, None)
            stripped += 1
            details.append({"path": _path, "field": "resolved", "value": True})
        if payload.get("terminal") is True:
            payload.pop("terminal", None)
            stripped += 1
            details.append({"path": _path, "field": "terminal", "value": True})
        for tk in ("kind", "type"):
            if payload.get(tk) == "terminal":
                payload.pop(tk, None)
                stripped += 1
                details.append({"path": _path, "field": tk, "value": "terminal"})
        ans = payload.get("answered")
        if isinstance(ans, list) and ans:
            payload["answered"] = []
            stripped += len(ans)
            details.append({"path": _path, "field": "answered",
                            "value": [a.get("qid") if isinstance(a, dict) else a for a in ans]})
        for k, v in list(payload.items()):
            s, d = strip_terminal_state_payload(v, f"{_path}.{k}")
            stripped += s
            details.extend(d)
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            s, d = strip_terminal_state_payload(v, f"{_path}[{i}]")
            stripped += s
            details.extend(d)
    return stripped, details


def record_terminal_contract(db, cid, section, payload, subkey=None):
    """S3 契约 WAL 审计留痕：.wal/<cid>_terminal_contract.json 按 section 记最新一次运行。

    sections: archive_strip（apply_archive 剥离计数）/ writer_changes_strip（按章 subkey）/
    foreshadower_payoff（终态转移 + 拒绝明细）/ dramatic_questions（answered 拒绝明细）。
    覆盖式写（同 section 反映最新运行·幂等 re-apply 不累积陈旧拒绝）。
    """
    p = Path(db) / ".wal" / f"{cid}_terminal_contract.json"
    doc = load_json(p, None)
    if not isinstance(doc, dict):
        doc = {"schema_version": 1, "cluster_id": cid, "sections": {}}
    sections = doc.setdefault("sections", {})
    if not isinstance(sections, dict):
        sections = doc["sections"] = {}
    payload = dict(payload)
    payload["at"] = datetime.now().isoformat(timespec="seconds")
    if subkey is not None:
        sec = sections.get(section)
        if not isinstance(sec, dict):
            sec = sections[section] = {}
        sec[str(subkey)] = payload
    else:
        sections[section] = payload
    save_json(p, doc)


# 🔴 2026-06-28 审计清理B类：伏笔 payoff terminal/progressive 分流助手已删除
# （原 _PAYOFF_*_WORDS / _word_surface_terminal / _classify_payoff_terminal /
# _load_foreshadower_maps）。它们只服务于 apply_changes 里「writer 自报 foreshadowing_planted/
# paid → 伏笔表」桥接——该桥接判为 B 类违规（消费侧读 writer 自报 factual 当权威状态源），已移除。
# 伏笔注册/兑现改走两条 Claude 权威路径：_register_brief_foreshadowings（读 outline brief 的
# foreshadowing_to_plant）+ _apply_foreshadower_payoffs（读 foreshadower JudgeReport·自带
# terminal→consumed / progressive→payoff_progress 分流 + score>0 门控）。


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
    atomic_json 不可导入时手写 tmp + os.replace（照 cluster_choice_apply.py:89-96 范式，
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


def _wal_dir(root: Path) -> Path:
    d = root / "_数据库" / ".wal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_required(args: list[str], *, cwd: Path | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    """运行 required 子命令；任何非 0、超时或启动异常都抛错。"""
    try:
        r = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"required command timeout after {timeout}s: {' '.join(args)}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"required command failed to start: {' '.join(args)} :: {exc}") from exc
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-500:]
        raise RuntimeError(f"required command rc={r.returncode}: {' '.join(args)} :: {tail}")
    return r


def _git_commit_marker_payload(norm_cid: str, chapters: list[int], msg: str) -> dict:
    return {
        "schema_version": 1,
        "cluster_id": norm_cid,
        "chapters": chapters,
        "commit_message": msg,
        "committed": True,
    }


def find_chapter_file(root: Path, ch: int) -> Path | None:
    """定位章节正文文件。v18：委托 chapter_io.find_body_file（兼容 4 布局 + 旧平铺）。"""
    return cio.find_body_file(root, ch)


# ============ WAL ============
# cluster WAL 由调度器创建；apply_changes/cmd_parse 直接写每章中间产物。


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
    """解析单章 CHANGES → .wal/第N章_parsed.json。返回状态码（0 成功 / 2 失败）。

    2026-05-29 复审修复 [M3]：原用 sys.exit 直接退进程，被 cluster 循环调用时单章失败
    会丢失汇总诊断。现返回状态码，由 cmd_apply_cluster_changes 汇总后统一硬停。
    """
    if not find_chapter_file(root, ch) and not cio.changes_path(root, ch).is_file():
        logger.error(f" 第{ch}章 正文/CHANGES 均未找到")
        return 2
    changes, strategy = parse_changes(root, ch)
    if changes is None:
        logger.info(f"[PARSE] CHANGES 解析失败（无 _changes.json 且旧稿无 CHANGES 段），需要回到 cluster 草稿层重跑")
        return 2
    out = root / "_数据库" / ".wal" / f"第{ch}章_parsed.json"
    save_json(out, {"strategy": strategy, "changes": changes})
    logger.info(f"[PARSE] 策略 {strategy} 成功 → {out.relative_to(root)}")
    return 0


# ============ 应用 CHANGES（结构化更新） ============

def _generate_patch(changes: dict, ch: int) -> list[dict]:
    """v16: 生成声明式JSON Patch（可审计/可回放/可撤销）。

    🔴 2026-06-28 审计清理B类：删除 writer 自报 factual（foreshadowing_actions /
    character_changes / character_movements / item_transfers / new_entities.characters）→ JSON
    的补丁生成。这些 cluster 级 factual 状态（角色/道具/关系/伏笔/locked_facts）改由
    novel-archivist 读正文产 archive.json → apply_archive.py 确定性回库（人物卡/角色池/道具/关系/
    事件簇.locked_facts），伏笔走 brief + foreshadower 两条 Claude 路径——save_state 不再消费
    writer changes.factual 当权威状态源。仅保留 time_advance（时间线·非 archive 域·无替代 producer）。
    """
    patches = []
    ta = changes.get("time_advance") or {}
    if ta:
        patches.append({
            "target": "时间线.json",
            "op": "append",
            "path": "/time_log",
            "value": {"ch": ch, "elapsed": ta.get("elapsed", ""), "key_events": ta.get("key_events", [])},
            "chapter": ch,
        })
    return patches


def apply_changes(root: Path, ch: int) -> int:
    """将 parsed 的 CHANGES 落地到对应 JSON。
    v16: 先生成声明式Patch文件（可审计/可回放），再执行实际修改。

    2026-05-29 复审修复 [M3]：原用 sys.exit(1) 直接退进程，被 cluster 循环调用时单章
    无 parsed 会丢失汇总诊断。现返回状态码（0 成功 / 2 无 parsed），
    由 cmd_apply_cluster_changes 汇总后统一硬停。
    """
    db = root / "_数据库"
    parsed_path = db / ".wal" / f"第{ch}章_parsed.json"
    parsed = load_json(parsed_path, {})
    changes = parsed.get("changes", {})
    if not changes:
        logger.info(f"[APPLY] 无 parsed CHANGES")
        return 2

    # 🔴 2026-07-07 S3 类级契约：writer 抽取类载荷禁携终态（详见 strip_terminal_state_payload）。
    # writer 只许报 progress 级观察——changes 携带伏笔/戏剧问题 consumed/resolved/answered/terminal
    # 终态声明 → 入库层剥离 + stderr 显式警告 + WAL 留痕（不静默接受）。
    ts_stripped, ts_details = strip_terminal_state_payload(changes)
    if ts_stripped:
        sys.stderr.write(
            f"[terminal-contract] WARNING: 第{ch}章 writer changes 携带 {ts_stripped} 处终态声明"
            f"（consumed/resolved/answered/terminal）——入库层已剥离降为 progress 级观察·"
            f"终态唯一通路=_apply_foreshadower_payoffs/cmd_apply_dramatic_questions\n")
        sys.stderr.flush()
        _cid_ch, _ = _resolve_cluster(root, ch)
        record_terminal_contract(db, _cid_ch, "writer_changes_strip",
                                 {"terminal_state_stripped": ts_stripped, "details": ts_details},
                                 subkey=f"ch{ch}")

    # v16: 生成并保存声明式Patch
    patches = _generate_patch(changes, ch)
    patch_path = db / ".wal" / f"第{ch}章_patch.json"
    save_json(patch_path, {"chapter": ch, "patch_count": len(patches), "patches": patches,
                           "generated_at": datetime.now().isoformat(timespec="seconds")})
    logger.info(f"[PATCH] 生成 {len(patches)} 条声明式补丁 → {patch_path.name}")

    summary = {"applied": [], "warnings": [], "patch_file": str(patch_path.name),
               # 🔴 S3 契约审计：本章 writer 载荷被剥离的终态声明计数（0=干净）
               "terminal_state_stripped": ts_stripped}

    # 🔴 2026-06-28 审计清理B类：删除 writer 自报 factual → 伏笔表 / 人物卡 的回库路径。
    #   · 伏笔表（promises/secrets/deadlines/pledges 各 status 生命周期）：原读 writer 的
    #     foreshadowing_actions / foreshadowing_planted / foreshadowing_paid（含 fs_auto_<hash>
    #     自动派 id）桥接——属 B 类违规（消费 writer 自报 factual 当权威状态源），全部移除。
    #     伏笔注册/兑现改走两条 Claude 权威路径（cmd_apply_cluster_changes 调度·读 outline brief +
    #     foreshadower JudgeReport）：_register_brief_foreshadowings + _apply_foreshadower_payoffs。
    #   · 人物卡（growth_arc / 新角色注册）：原读 writer 的 character_changes / new_entities.characters，
    #     现由 novel-archivist 读正文产 archive → apply_archive.py 写人物卡/角色池/state_log。
    # save_state 仅保留 time_advance（时间线）/ location_changes（地图地点）/ 进度推进——
    # 它们非 archive 域、无替代 producer，且不属「角色/道具/关系/伏笔/locked_facts」factual 状态。

    # --- 进度（completed+1, current+1）---
    # 2026-05-30 北极星复审：进度.json 损坏时禁止覆写，直接抛错让 cluster apply 硬停。
    prog_path = db / "进度.json"
    progress = None
    if prog_path.exists():
        try:
            progress = json.loads(prog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise RuntimeError("进度.json 损坏，请修复 JSON 后重试")
    else:
        raise RuntimeError("进度.json 不存在，请先初始化项目状态库")
    if not isinstance(progress, dict):
        raise RuntimeError("进度.json 顶层不是 object")
    progress["completed"] = max(progress.get("completed", 0), ch)
    progress["current"] = progress["completed"] + 1
    save_json(prog_path, progress)
    summary["applied"].append(f"进度: completed={progress['completed']}")

    # --- 地图（新地点状态）---
    # 🔴 2026-06-28 审计清理B类：删除 character_movements → 地图.character_positions 回库
    #   （消费 writer 自报 factual·角色位置属 archive 关系/状态域）。仅保留 location_changes
    #   （地点 status·非 archive 域、无替代 producer）。
    loc_data = load_json(db / "地图.json", {"locations": [], "character_positions": {}})
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
        src_cid, _src_inferred = _resolve_cluster(root, ch)
        if isinstance(tl.get("current_time"), dict) and ta.get("period"):
            tl["current_time"]["period"] = ta["period"]
            tl["current_time"].pop("chapter", None)
            tl["current_time"]["cluster"] = src_cid
            tl["current_time"]["chapter_in_cluster"] = ch
        # 🔴 2026-06-27 C11（cluster 级幂等去重）：time_advance 同样被 split_cluster_changes
        # 平铺进每章，逐章 apply 让 time_log 同一 elapsed/key_events 累积 N 条。按
        # (elapsed,key_events,_source_cluster) 去重——同 cluster 内同一时间推进只记一次。
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

    # 🔴 2026-06-28 审计清理B类：删除 item_transfers → 道具.json holder/status 回库
    #   （消费 writer 自报 factual·道具属 archive 域）。道具现由 novel-archivist 读正文产 archive
    #   → apply_archive.py 写 道具.json（建卡/holder/状态）。save_state 不再触碰 道具.json。

    # --- 输出 summary ---
    out = db / ".wal" / f"第{ch}章_applied.json"
    save_json(out, summary)
    logger.info(f"[APPLY] 完成 {len(summary['applied'])} 项变更"
          + (f", {len(summary['warnings'])} 条警告" if summary["warnings"] else ""))
    for w in summary["warnings"]:
        logger.info(f"  ⚠️ {w}")
    return 0


# ============ 报告（第10步）============
# 🔴 2026-05-29 流程贯通（断点 5 死代码清理）：cmd_report（章级报告）已删除。
# 与 cmd_report_cluster（见 CLI 段说明）一起下线 —— grep 确认无任何 plan/命令文档
# 调 save_state.py --report / --report-cluster（cluster-save-state 报告由其他步骤产出）。


# ============ CLI ============

def _get_cluster_chapter_range(project_root, cluster_key):
    """拿 cluster 的 chapter_range，展开 [ch1,...,chN]。

    🔴 2026-06-17 bug-hunt 修：改走 `cluster_lookup.cluster_id_to_range`（唯一权威反查·北极星①·
    可读 splitter 写回前的 blueprint 范围）。原实现只读 事件簇.json → blueprint-only 状态（事件簇缺 chapter_range·
    进度.json.cluster_blueprint 有）返 [] → cmd_apply_cluster_changes / cmd_git_commit_cluster
    FATAL exit2 卡死整条 cluster-save-state 管线（而兄弟脚本 evaluators 走 cluster_lookup 正常推进·
    三脚本权威源不一致）。对齐 evaluators。"""
    cid = _require_cluster_id(cluster_key)
    cr = cluster_lookup.cluster_id_to_range(project_root, cid)
    if cr and len(cr) == 2:
        return list(range(int(cr[0]), int(cr[1]) + 1))
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
    .truth_check_cluster）。本命令属于 required 状态验证：撒谎或检测异常都会阻断。
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
        elif r.returncode != 0:
            result["errors"].append((r.stderr or r.stdout or f"rc={r.returncode}")[:160])
        # 🔴 2026-06-27 SYS-3/C10（shadow）：从 stdout 抓声明-vs-正文 advisory 计数 → 列主代理待裁决。
        # SHADOW 决策：C10 corroboration/quarantine 先以 advisory 跑（只 surface 不延迟写账本），确认
        # 无假阳再 active（届时把 corroborated!=true 的 factual 项 defer-write 防脏账本）。
        _nt = re.search(r"SYS-3 申报兑现但正文 0 痕迹：(\d+)", r.stdout or "")
        _uc = re.search(r"FACTUAL_CLAIM_UNCORROBORATED（弱信号·advisory）：(\d+)", r.stdout or "")
        result["foreshadowing_no_trace"] = int(_nt.group(1)) if _nt else 0
        result["factual_uncorroborated"] = int(_uc.group(1)) if _uc else 0
    except Exception as e:
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
            raise RuntimeError("进度.json 不存在")
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
        if not isinstance(prog, dict):
            raise RuntimeError("进度.json 顶层不是 object")
        cid = _require_cluster_id(cluster_key)
        prog["current_cluster"] = cid
        # completed：取 max(现值, cluster 末章)
        if chapters:
            prog["completed"] = max(int(prog.get("completed", 0) or 0), max(chapters))
            prog["current"] = prog["completed"] + 1
        # book_title：占位时从项目目录名派生
        bt = str(prog.get("book_title") or "")
        if (not bt) or bt.startswith("<") or bt == "<书名>":
            prog["book_title"] = Path(root).name
        prog["last_updated"] = datetime.now().isoformat(timespec="seconds")
        save_json(prog_path, prog)
        logger.info(f"[进度回写] current_cluster={cid} completed={prog.get('completed')} book_title={prog['book_title']}")
    except Exception as e:
        raise RuntimeError(f"进度回写失败: {type(e).__name__}: {str(e)[:120]}") from e


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
        if not ec_path.exists():
            raise RuntimeError("事件簇.json 不存在")
        if not ds_path.exists():
            raise RuntimeError("大势卡.json 不存在")
        cid = _require_cluster_id(cluster_key)
        ec = load_json(ec_path, None)
        if not isinstance(ec, dict):
            raise RuntimeError("事件簇.json 缺失或损坏")
        cluster = next((c for c in ec.get("clusters", [])
                        if cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == cid
                        or str(c.get("cluster_id")) == cid), None)
        if not cluster:
            raise RuntimeError(f"事件簇.json 找不到 {cid}")
        me_id = cluster.get("parent_me") or cluster.get("me_id")
        if not me_id:
            raise RuntimeError(f"{cid} 缺 parent_me/me_id")
        adv = list(cluster.get("ME_to_advance") or [])
        if me_id not in adv:
            cluster["ME_to_advance"] = adv + [me_id]
            save_json(ec_path, ec)
        ds = load_json(ds_path, None)
        if not isinstance(ds, dict):
            raise RuntimeError("大势卡.json 缺失或损坏")
        major_events = ds.get("major_events")
        if not isinstance(major_events, list):
            major_events = ds.get("major_events_pool")
        if not isinstance(major_events, list):
            raise RuntimeError("大势卡.json 缺 major_events/major_events_pool list")
        changed = False
        found = False
        for m in major_events:
            mid = m.get("id") or m.get("me_id")
            if mid == me_id and m.get("status") != "completed":
                found = True
                m["status"] = "completed"
                m["completed_by_cluster"] = cid
                changed = True
            elif mid == me_id:
                found = True
        if not found:
            raise RuntimeError(f"大势卡.json 找不到 ME {me_id}")
        if changed:
            save_json(ds_path, ds)
            logger.info(f"[ME完成] {me_id} status=completed (by {cid})")
    except Exception as e:
        raise RuntimeError(f"ME完成标记失败: {type(e).__name__}: {str(e)[:120]}") from e


# 🔴 2026-06-28 审计清理B类：_persist_cluster_locked_facts 已删除。
# 它读 writer changes.factual.facts_locked/locked_facts 写 事件簇.clusters[].locked_facts——属 B
# 类违规（消费 writer 自报 factual 当权威状态源）。locked_facts 现由 novel-archivist 读正文产
# archive.json → apply_archive.py 的 apply_locked_facts 写 事件簇.clusters[].locked_facts（权威源 =
# Claude 读正文，非 writer 自报）。cmd_apply_cluster_changes 不再调用本函数。


def _register_brief_foreshadowings(root, cluster_key):
    """🔴 2026-06-28：把 事件簇.clusters[].foreshadowing_to_plant（outline-planner 规划·带 fs_id）
    注册进伏笔表.promises。治：writer 常漏报 brief 计划的伏笔（FS_014-017 实测不在伏笔表）→ 孤儿 payoff
    （foreshadower 判 terminal 却无对应 promise 可标 consumed）。brief 是 fs_id 权威来源（同 locked_facts
    哲学）。dedup by fs_id·不覆盖已存在（含已 consumed）。排在 foreshadower 桥接前，使新注册可被立即回收。
    2026-07-06 P1 三态生命周期：新条目写 status="open" + owner + payoff_scope（枚举权威见
    db_schema_validate.FORESHADOW_STATUS_ENUM·不再写 resolved bool）。
    """
    try:
        db = root / "_数据库"
        ec_path = db / "事件簇.json"
        if not ec_path.exists():
            raise RuntimeError("事件簇.json 不存在")
        cid = _require_cluster_id(cluster_key)
        ec = load_json(ec_path, None)
        if not isinstance(ec, dict):
            raise RuntimeError("事件簇.json 缺失或损坏")
        cluster = next((c for c in ec.get("clusters", [])
                        if cluster_lookup.normalize_cluster_id(c.get("cluster_id")) == cid
                        or str(c.get("cluster_id")) == cid), None)
        if not cluster:
            raise RuntimeError(f"事件簇.json 找不到 {cid}")
        ftp = cluster.get("foreshadowing_to_plant", []) or []
        fs_path = db / "伏笔表.json"
        fs = load_json(fs_path, None)
        if not isinstance(fs, dict):
            raise RuntimeError("伏笔表.json 缺失或损坏")
        promises = fs.setdefault("promises", [])
        if not isinstance(promises, list):
            raise RuntimeError("伏笔表.json.promises 不是 list")
        existing = {p.get("id") for p in promises if isinstance(p, dict)}
        added = 0
        for f in ftp:
            if not isinstance(f, dict):
                continue
            fid = f.get("id") or f.get("fs_id")
            if not fid or fid in existing:
                continue
            entry = {
                "id": fid, "setup_cluster": cid, "tier": f.get("tier", 3),
                "description": f.get("desc") or f.get("description") or "",
                "due_by_cluster": None, "status": "open", "owner": "writer",
                "due_by_pending_resolution": True, "_source": "brief",
            }
            entry["payoff_scope"] = _dbsv.derive_payoff_scope(entry)
            promises.append(entry)
            existing.add(fid)
            added += 1
        if added:
            save_json(fs_path, fs)
            logger.info(f"[brief-foreshadow] {cid} 注册 {added} 条 brief 规划伏笔 → 伏笔表")
    except Exception as e:
        raise RuntimeError(f"brief 规划伏笔注册失败: {type(e).__name__}: {str(e)[:120]}") from e


def _apply_foreshadower_payoffs(root, cluster_key):
    """🔴 2026-06-28：foreshadower JudgeReport 的 payoff 检测桥接到伏笔表回收落账。

    根因：apply 只处理 writer changes.foreshadowing_paid（模型常漏报·cluster_006 实测 paid=0），
    foreshadower 从正文检测到的 payoff（FS_012 paid_terminal）流不进伏笔表 → 伏笔恒不回收、
    foreshadow_rhythm 失衡。foreshadower 是正文 payoff 的权威检测器（比 writer 自报可靠）。
    桥接（2026-07-06 P1 三态生命周期）：读 cluster_<key>_foreshadower.json 的 payoff_scores ——
    verdict=paid_terminal+score>0 → status="consumed"（记 consumed_at_ch/_consumed_by）；
    paid_progressive → 记 payoff_progress（status 不变）。幂等：已 consumed 不后移。
    回收动作指向非 open 条目（suspended）→ logger 警示（可能是状态漂移·advisory·照常落账，
    scanner 侧对偶检查见 foreshadowing_handoff_scanner FORESHADOWING_PAYOFF_TARGET_NOT_OPEN）。
    本桥消费 cluster-save-state required foreshadower 报告；缺报告必须硬失败。

    🔴 2026-07-07 S3 类级契约（本函数 = 伏笔终态唯一通路·写入层硬校验·详见模块头契约段）：
      ① 终态转移目标必须存在且 status ∈ {open, suspended}——目标缺失（孤儿 payoff）或
        status 非法（未迁移三态/状态漂移）→ 拒绝该条转移 + stderr 警告（与
        FORESHADOWING_PAYOFF_TARGET_NOT_OPEN advisory 口径呼应·这里是写入层硬拒）；
        suspended 仍为警示后照常落账（2026-07-06 决定不变）；已 consumed = 幂等静默跳过（非拒绝）。
      ② 终态转移必须携正文证据：evidence/span/reason 至少一个非空（reason 即 foreshadower
        schema 的正文凭证字段）——缺证据 → 拒绝该条 + 警告。
      拒绝 = 该条不落库但不整体崩（其余条目照常）；拒绝明细进 WAL
      （.wal/<cid>_terminal_contract.json sections.foreshadower_payoff）。不新增 hard_gate code。
    """
    try:
        db = root / "_数据库"
        cid = _require_cluster_id(cluster_key)
        rpt = db / ".judge_reports" / f"{cid}_foreshadower.json"
        if not rpt.is_file():
            raise RuntimeError(f"{rpt.name} 不存在")
        report = load_json(rpt, None)
        if not isinstance(report, dict):
            raise RuntimeError(f"{rpt.name} 缺失或损坏")
        scores = (report.get("specific_findings") or {}).get("payoff_scores", []) or []
        if not isinstance(scores, list):
            raise RuntimeError("foreshadower payoff_scores 不是 list")
        if not scores:
            return
        fs_path = db / "伏笔表.json"
        fs = load_json(fs_path, None)
        if not isinstance(fs, dict):
            raise RuntimeError("伏笔表.json 缺失或损坏")
        by_id = {p.get("id"): p for p in fs.get("promises", []) if isinstance(p, dict)}
        chapters = _get_cluster_chapter_range(root, cluster_key)
        if not chapters:
            raise RuntimeError(f"{cid} 未找到 chapter_range")
        last_ch = max(chapters)
        changed = 0
        terminal_applied = 0
        progressive_recorded = 0
        rejections = []

        def _reject(fs_id, code, note):
            """🔴 S3 契约：拒绝单条终态转移（不落库·不整体崩）+ stderr 显式警告 + WAL 明细。"""
            rejections.append({"fs_id": fs_id, "code": code, "note": note})
            sys.stderr.write(f"[terminal-contract] WARNING: {cid} payoff→{fs_id} 拒绝终态转移"
                             f"（{code}·{note}·该条不落库·其余条目照常）\n")
            sys.stderr.flush()

        for it in scores:
            if not isinstance(it, dict):
                continue
            fs_id = it.get("fs_id")
            p = by_id.get(fs_id)
            try:
                score = int(it.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            if score <= 0:
                continue  # 谎报门控：声明 paid 但正文 0 痕迹 → 不落账（现行为不变·先于契约检查）
            verdict = str(it.get("verdict", ""))
            if it.get("terminal") is True or verdict == "paid_terminal":
                # 🔴 2026-07-07 S3 类级契约：终态唯一通路写入层硬校验（见 docstring ①②）
                if p is None:
                    _reject(fs_id, "target_missing", "伏笔表不存在该条目（孤儿 payoff）")
                    continue
                if p.get("status") == "consumed":
                    continue  # 幂等：已 consumed 不后移（re-apply 常态·非拒绝）
                status = p.get("status")
                if status not in ("open", "suspended"):
                    _reject(fs_id, "target_not_open",
                            f"status={status!r} 非 open/suspended（未迁移三态或状态漂移）")
                    continue
                evidence = str(it.get("evidence") or it.get("span") or it.get("reason") or "").strip()
                if not evidence:
                    _reject(fs_id, "evidence_missing", "缺 evidence/span/reason 正文证据")
                    continue
                if status == "suspended":
                    logger.warning(
                        f"[foreshadower-payoff] {p.get('id')} 处于 suspended 却被 terminal 回收"
                        "（回收动作指向非 open 条目·可能是状态漂移·advisory·照常落账）")
                p["status"] = "consumed"
                p["consumed_at_ch"] = last_ch
                p["_consumed_by"] = "foreshadower"
                changed += 1
                terminal_applied += 1
            elif "progressive" in verdict or it.get("terminal") is False:
                if p is None:
                    continue  # progressive 观察指向未注册 id → 注册时点常态·静默跳过（现行为不变）
                prog = p.setdefault("payoff_progress", [])
                if cid not in prog:
                    prog.append(cid)
                    changed += 1
                    progressive_recorded += 1
        if changed:
            save_json(fs_path, fs)
            logger.info(f"[foreshadower-payoff] {cid} 桥接 {changed} 条 payoff → 伏笔表"
                        "（terminal→consumed / progressive→progress）")
        # 🔴 S3 契约 WAL 审计留痕（每次运行覆盖最新·含空 rejections 便于核对）
        record_terminal_contract(db, cid, "foreshadower_payoff",
                                 {"checked": len(scores), "terminal_applied": terminal_applied,
                                  "progressive_recorded": progressive_recorded,
                                  "rejections": rejections})
    except Exception as e:
        raise RuntimeError(f"foreshadower payoff 回库失败: {type(e).__name__}: {str(e)[:120]}") from e


# ═══════ 🔴 2026-07-07 S10 递归卷级层级摘要（Ex3 摘要金字塔 + source 回溯）═══════
# 出处 research/open_source_writing_systems_round2.md S10：借鉴 Ex3-NovelWriter 的层级摘要
# 金字塔（para_group_sum → chapter_sum → chapter_group_sum → novel_summary·见
# external_repos/Ex3-NovelWriter/Extracting/get_summary.py）+ source 回溯（每级摘要必须
# 可追溯到聚合来源）。本系统落地为 cluster 摘要（既有）→ 卷级摘要（本段新增）：
#   · 卷边界检测只用既有信号（不另立）：大势卡 ME.volume（口径=_me_volume_of·与
#     cluster_emergence_engine._me_volume 一致）+ ME.status=="completed"（唯一维护者
#     = _mark_cluster_me_completed·step3）+ ME.completed_by_cluster（本卷 cluster 归属
#     权威登记）。卷闭合 = 该卷 ME 池非空且全部 completed。emergence 的
#     volume_transition_hint 是「临近」advisory（finale 未写完就亮），本段用的是
#     「已跨越」事实（finale ME completed ⊆ 全 completed），同源同口径不另立。
#   · 聚合归 Claude 系 agent（架构分工）：novel-summarizer MODE=volume 读本卷全部
#     cluster 摘要产 .wal/volume_<N>_summary.json（梳理非创作）。
#   · 回库唯一入口 = cmd_apply_volume_summary：结构键钉死（judge_required_keys 精神）
#     + source 必须恰好覆盖本卷全部 cluster_ids（缺=漏源·多=幻觉源·都拒）+ 幂等 upsert。
#   · 无卷边界 = 零行为变化；漏检自愈：pending 依据=闭合卷缺 volume_summaries 条目，
#     下个 cluster 的 detect 会重新亮起（不预设卷数·fluid）。

# apply 白名单可选键（结构键钉死之外允许透传的 agent 产物字段·类型不符即丢弃）
_VOLUME_SUMMARY_OPTIONAL_KEYS = (("emotional_peak", str), ("key_turning_points", list))


def _me_volume_of(me: dict):
    """ME 所属卷/阶段号。🔴 口径与 cluster_emergence_engine._me_volume 完全一致
    （回归锁 test_save_state_volume_summary.test_me_volume_parity_with_emergence_engine
    钉死双实现不漂移）：优先显式 `volume` 字段（int / 数字 str），否则从 id
    （ME-V<N>-xx）解析。不 import 引擎——避免把 emergence_transparency →
    world_evolution_engine 依赖链拖进每次 save_state 进程。"""
    v = me.get("volume")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    m = re.search(r"[Vv](\d+)", str(me.get("id") or me.get("me_id") or ""))
    return int(m.group(1)) if m else None


def _load_major_events(root: Path) -> list:
    """读大势卡 ME 池（与 _mark_cluster_me_completed 同优先序：major_events 先）。缺/坏硬失败。"""
    ds = load_json(root / "_数据库" / "大势卡.json", None)
    if not isinstance(ds, dict):
        raise RuntimeError("大势卡.json 缺失或损坏")
    pool = ds.get("major_events")
    if not isinstance(pool, list):
        pool = ds.get("major_events_pool")
    if not isinstance(pool, list):
        raise RuntimeError("大势卡.json 缺 major_events/major_events_pool list")
    return pool


def _closed_volumes(root: Path) -> dict:
    """从大势卡 ME 池算「已完结卷」→ {卷号: [本卷 cluster_ids 升序]}。

    卷闭合（既有信号·不另立）= 该卷 ME 池非空且全部 status=="completed"。本卷
    cluster_ids 取各 ME 的 completed_by_cluster（_mark_cluster_me_completed 写入的
    权威登记）。completed 却缺 completed_by_cluster（旧库/手改）→ 该卷源头不全、
    不可聚合，warning 后跳过（不误触发）。
    """
    by_vol: dict = {}
    for me in _load_major_events(root):
        if not isinstance(me, dict):
            continue
        v = _me_volume_of(me)
        if v is None:
            continue
        by_vol.setdefault(v, []).append(me)
    closed = {}
    for v, mes in sorted(by_vol.items()):
        if not mes or any(m.get("status") != "completed" for m in mes):
            continue
        cids = []
        incomplete = False
        for m in mes:
            cid = cluster_lookup.normalize_cluster_id(m.get("completed_by_cluster"))
            if not cid:
                incomplete = True
                logger.warning(f"[volume-boundary] 卷{v} ME {m.get('id') or m.get('me_id')} "
                               "status=completed 却缺 completed_by_cluster——该卷源头不全·不可聚合")
                break
            if cid not in cids:
                cids.append(cid)
        if incomplete:
            continue
        cids.sort(key=lambda c: cluster_lookup.cluster_num(c) or 0)
        closed[v] = cids
    return closed


def cmd_detect_volume_boundary(root: Path, cluster_key) -> int:
    """S10：卷边界确定性检测（report-only·无边界=零状态变化·恒 exit 0 除非状态库缺坏）。

    pending 卷 = 闭合卷（_closed_volumes）中 故事块摘要.volume_summaries 尚无条目的卷。
    产 .wal/<cid>_volume_boundary.json 供主代理决策：boundary=true → 追加 spawn
    novel-summarizer MODE=volume → --apply-volume-summary <N>；boundary=false → 本步结束。
    """
    try:
        cid = _require_cluster_id(cluster_key)
        doc = load_json(root / "_数据库" / "故事块摘要.json", None)
        if not isinstance(doc, dict):
            raise RuntimeError("故事块摘要.json 缺失或损坏")
        vs = doc.get("volume_summaries")
        have = set()
        if isinstance(vs, list):
            for e in vs:
                if isinstance(e, dict) and isinstance(e.get("volume"), int):
                    have.add(e["volume"])
        closed = _closed_volumes(root)
        pending = [{"volume": v, "cluster_ids": ids}
                   for v, ids in sorted(closed.items()) if v not in have]
        payload = {
            "schema_version": 1,
            "cluster_id": cid,
            "boundary": bool(pending),
            "volumes_pending": pending,
            "closed_volumes": sorted(closed),
            "volumes_summarized": sorted(have),
            "detected_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_json(_wal_dir(root) / f"{cid}_volume_boundary.json", payload)
        if pending:
            for p in pending:
                logger.info(f"[volume-boundary] 卷{p['volume']} 已完结（{len(p['cluster_ids'])} 个 "
                            f"cluster: {','.join(p['cluster_ids'])}）→ 条件子任务：spawn "
                            f"novel-summarizer MODE=volume → --apply-volume-summary {p['volume']}")
        else:
            logger.info(f"[volume-boundary] {cid} 无卷边界（零行为变化）")
        return 0
    except Exception as e:
        sys.stderr.write(f"[FATAL save_state] detect-volume-boundary: "
                         f"{type(e).__name__}: {str(e)[:160]}\n")
        sys.stderr.flush()
        return 2


def cmd_apply_volume_summary(root: Path, volume_n) -> int:
    """S10：卷级摘要确定性回库唯一入口（.wal/volume_<N>_summary.json → 故事块摘要.volume_summaries[]）。

    校验（拒即 exit 2·账本零写入）：
      ① 结构键钉死（judge_required_keys 精神）：volume(int·须==N) / summary(非空 str) /
        source(非空 list·逐一可归一化) / generated_at_cluster(可归一化 cluster id)。
      ② 卷必须真闭合（_closed_volumes 既有 ME 信号）——未完结卷拒绝回库（防误触发）。
      ③ source 回溯完整性：必须恰好覆盖本卷全部 cluster_ids（缺=漏源·多=幻觉源·都拒）。
      ④ 字数 300-500 CJK 仅 advisory warning（北极星⑤：结构 hard·内容不规训）。
    幂等 upsert：按 volume 唯一；同内容 re-apply 零写盘 churn；内容变化则替换条目。
    可选透传键白名单见 _VOLUME_SUMMARY_OPTIONAL_KEYS（类型不符即丢弃·未知键不入库）。
    """
    try:
        vol = int(volume_n)
        db = root / "_数据库"
        wal = db / ".wal" / f"volume_{vol}_summary.json"
        if not wal.is_file():
            raise RuntimeError(f"{wal.name} 不存在（须先 spawn novel-summarizer MODE=volume 产出）")
        prod = load_json(wal, None)
        if not isinstance(prod, dict):
            raise RuntimeError(f"{wal.name} 损坏（非 JSON object）")
        # ① 结构键钉死
        try:
            p_vol = int(prod.get("volume"))
        except (TypeError, ValueError):
            raise RuntimeError("产物缺合法 volume 整数键") from None
        if p_vol != vol:
            raise RuntimeError(f"产物 volume={p_vol} 与 --apply-volume-summary {vol} 不一致")
        summary_text = prod.get("summary")
        if not isinstance(summary_text, str) or not summary_text.strip():
            raise RuntimeError("产物缺非空 summary")
        raw_source = prod.get("source")
        if not isinstance(raw_source, list) or not raw_source:
            raise RuntimeError("产物缺非空 source list")
        gac = cluster_lookup.normalize_cluster_id(prod.get("generated_at_cluster"))
        if not gac:
            raise RuntimeError("产物缺合法 generated_at_cluster")
        source = []
        for s in raw_source:
            n = cluster_lookup.normalize_cluster_id(s)
            if not n:
                raise RuntimeError(f"source 含非法 cluster id: {s!r}")
            if n not in source:
                source.append(n)
        # ② 卷必须真闭合
        expected = _closed_volumes(root).get(vol)
        if not expected:
            raise RuntimeError(f"卷{vol} 未完结（该卷 ME 池非空且全部 status=completed 才是卷边界）"
                               "——拒绝回库")
        # ③ source 回溯完整性（恰好覆盖·缺/多都拒）
        missing = [c for c in expected if c not in source]
        extra = [c for c in source if c not in expected]
        if missing or extra:
            raise RuntimeError(f"source 与卷{vol} 实际 cluster 集不符："
                               f"missing={missing} extra={extra}")
        # ④ 字数 advisory
        cjk = sum(1 for c in summary_text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
        if not (300 <= cjk <= 500):
            logger.warning(f"[volume-summary] 卷{vol} summary CJK={cjk} 越出 300-500 契约"
                           "（advisory·照常落库）")
        entry = {
            "volume": vol,
            "summary": summary_text.strip(),
            "source": sorted(source, key=lambda c: cluster_lookup.cluster_num(c) or 0),
            "generated_at_cluster": gac,
        }
        for k, typ in _VOLUME_SUMMARY_OPTIONAL_KEYS:
            v_opt = prod.get(k)
            if isinstance(v_opt, typ) and v_opt:
                entry[k] = v_opt
        # ⑤ 幂等 upsert
        sum_path = db / "故事块摘要.json"
        doc = load_json(sum_path, None)
        if not isinstance(doc, dict):
            raise RuntimeError("故事块摘要.json 缺失或损坏")
        vs = doc.get("volume_summaries")
        if not isinstance(vs, list):
            vs = doc["volume_summaries"] = []
        idx = next((i for i, e in enumerate(vs)
                    if isinstance(e, dict) and e.get("volume") == vol), None)
        if idx is not None and {k: v for k, v in vs[idx].items() if k != "applied_at"} == entry:
            logger.info(f"[volume-summary] 卷{vol} 已存在同内容条目（幂等·零写盘）")
            return 0
        entry["applied_at"] = datetime.now().isoformat(timespec="seconds")
        if idx is not None:
            vs[idx] = entry
            logger.info(f"[volume-summary] 卷{vol} 条目已更新（upsert 替换·source {len(source)} "
                        f"cluster·CJK {cjk}）")
        else:
            vs.append(entry)
            vs.sort(key=lambda e: e.get("volume") if isinstance(e.get("volume"), int) else 0)
            logger.info(f"[volume-summary] 卷{vol} 卷级摘要回库 → 故事块摘要.volume_summaries"
                        f"（source {len(source)} cluster·CJK {cjk}）")
        save_json(sum_path, doc)
        return 0
    except Exception as e:
        sys.stderr.write(f"[FATAL save_state] apply-volume-summary: "
                         f"{type(e).__name__}: {str(e)[:200]}\n")
        sys.stderr.flush()
        return 2


# 🔴 2026-06-29 场景级Appraisal Beat(chain-of-emotion)
def cmd_apply_appraisal_beats(root, cluster_key):
    """🔴 2026-06-29 场景级 Appraisal Beat（chain-of-emotion）·summarizer 梳理产物确定性回库（零模型·幂等）。

    novel-summarizer 读整 cluster 正文（梳理非创作·禁占位词典浅扫）按 Scherer CPM/OCC 评价链推理产
    summary.appraisal_beats=[{cluster_id, scene_idx, focal_character, trigger_event, appraisal{6维},
    prospect, derived_emotion（自然语言·非情绪词标签）, behavior_externalization, vad_bin}]。本步把它
    确定性 append 进 叙事节拍器.json.appraisal_beats —— 把情绪从『prose 一句提示』升维为『结构化可追踪
    STATE』（SOTA arXiv:2309.05076 chain-of-emotion + CAPE arXiv:2410.14145）。

    🔴 只填 active cluster（fluid·北极星：不预设/回填别的 cluster）：beat 显式 cluster_id 归一后 != 本
    cluster 则跳过，其余强制归到本 cluster_id。

    幂等·去重：按 (cluster_id, scene_idx, focal_character) 去重——同一 scene 可有多个 focal 角色各一拍，
    故去重键在 cluster_id+scene_idx 之外补 focal_character（防同 scene 多角色 beat 被误并）。re-apply 同
    summary 不重复 append、不写盘 churn。

    summary、appraisal_beats 字段、叙事节拍器.json 都是本链路 required 产物。缺失/损坏即 return 2，
    防止 summarizer 漏产或状态库损坏被吞掉。空数组表示生产者明确判断本 cluster 无情绪拍，
    幂等通过。
    """
    try:
        db = root / "_数据库"
        cid = _require_cluster_id(cluster_key)
        summary_path = db / ".wal" / f"{cid}_summary.json"
        if not summary_path.is_file():
            logger.info(f"[appraisal-beats] {cid} summary.json 不存在（summarizer required 产物缺失）")
            return 2
        summ = load_json(summary_path, {})
        beats = summ.get("appraisal_beats") if isinstance(summ, dict) else None
        if not isinstance(beats, list):
            logger.info(f"[appraisal-beats] {cid} summary 缺 appraisal_beats list（required 字段缺失）")
            return 2
        if not beats:
            logger.info(f"[appraisal-beats] {cid} appraisal_beats=[]（生产者明确无情绪拍·幂等通过）")
            return 0

        # 🔴 2026-06-29 NN情绪VAD集成 — vad_bin 的 V/A 真模型重算（env RUOYU_NN_VAD 门控）。
        # 未启用时不参与；启用后即为 required enrichment，模型/feature store 失败直接 return 2。
        # D 维保留 summarizer 判断。
        nn_vad_by_id, _vad_bridge = {}, None
        import os as _os_vad
        if _os_vad.environ.get("RUOYU_NN_VAD") == "1":
            try:
                import nn_vad_bridge as _vad_bridge
                _bd = [b for b in beats if isinstance(b, dict)]
                _txts = [" ".join(str(b.get(k, "")) for k in
                                  ("trigger_event", "derived_emotion", "behavior_externalization")).strip()
                         for b in _bd]
                try:
                    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
                    from feature_cache import FeatureStore, enabled as _fs_enabled
                    _preds = FeatureStore.get().compute_vad_batch(_txts) if _fs_enabled() else _vad_bridge.predict_batch(_txts)
                except Exception:  # noqa: BLE001 feature store 失败 → 直连桥
                    _preds = _vad_bridge.predict_batch(_txts)
                for b, pr in zip(_bd, _preds):
                    if pr and pr.get("valence") is not None:
                        nn_vad_by_id[id(b)] = pr
                if nn_vad_by_id:
                    logger.info(f"[appraisal-beats] {cid} NN VAD 重算 {len(nn_vad_by_id)}/{len(_bd)} 拍 "
                                "vad_bin.V/A（D 保留 summarizer）")
            except Exception as e:  # noqa: BLE001
                logger.info(f"[appraisal-beats] NN VAD FATAL: {type(e).__name__}: {str(e)[:80]}")
                return 2

        pacer_path = db / "叙事节拍器.json"
        pacer = load_json(pacer_path, None)
        if not isinstance(pacer, dict):
            logger.info(f"[appraisal-beats] {cid} 叙事节拍器.json 缺/坏（required 状态库不可用）")
            return 2
        existing = pacer.get("appraisal_beats")
        if not isinstance(existing, list):
            existing = pacer["appraisal_beats"] = []
        seen = {(b.get("cluster_id"), b.get("scene_idx"), b.get("focal_character"))
                for b in existing if isinstance(b, dict)}
        added = 0
        for b in beats:
            if not isinstance(b, dict):
                continue
            # 只填 active cluster：显式标了别的 cluster → 跳过（fluid·不回填非本 cluster）
            b_cid_raw = b.get("cluster_id")
            if b_cid_raw:
                b_cid = _require_cluster_id(b_cid_raw)
                if b_cid != cid:
                    continue
            rec = dict(b)
            rec["cluster_id"] = cid  # 强制归到本 active cluster
            try:
                rec["scene_idx"] = int(b.get("scene_idx")) if b.get("scene_idx") is not None else None
            except (TypeError, ValueError):
                rec["scene_idx"] = None
            focal = rec.get("focal_character")
            if not focal:
                continue  # focal_character 必填（真角色 id）
            # 🔴 2026-06-29 NN情绪VAD集成 — 用模型 V/A 覆盖 summarizer 手判（D 保留）·命中才覆盖
            pr = nn_vad_by_id.get(id(b))
            if pr is not None and _vad_bridge is not None:
                _old_bin = rec.get("vad_bin") if isinstance(rec.get("vad_bin"), dict) else {}
                _nb = _vad_bridge.to_vad_bin(pr.get("valence"), pr.get("arousal"))
                rec["vad_bin"] = {"valence": _nb["valence"], "arousal": _nb["arousal"],
                                  "dominance": _old_bin.get("dominance"),
                                  "_source": "model_va+summarizer_d"}
            key = (cid, rec["scene_idx"], focal)
            if key in seen:
                continue
            existing.append(rec)
            seen.add(key)
            added += 1
        if added:
            save_json(pacer_path, pacer)
            logger.info(f"[appraisal-beats] {cid} 回填 {added} 拍 → 叙事节拍器.appraisal_beats"
                        "（chain-of-emotion·结构化 STATE）")
        else:
            logger.info(f"[appraisal-beats] {cid} 无新增（全已存在或非本 cluster·幂等）")
        return 0
    except Exception as e:
        logger.info(f"[appraisal-beats] FATAL: {type(e).__name__}: {str(e)[:120]}")
        return 2


# 🔴 2026-06-29 戏剧问题账本(PITQ/MDQ)
def cmd_apply_dramatic_questions(root, cluster_key):
    """🔴 2026-06-29 戏剧问题账本（PITQ/MDQ）·foreshadower 梳理产物确定性回库（零模型·幂等）。

    novel-foreshadower（伏笔⊂PITQ 的特例·最适合扩登记戏剧问题）读整 cluster 正文产 JudgeReport 的
    specific_findings.dramatic_questions = {raised:[{qid(全局唯一), question(具体二元PITQ非模糊悬念),
    scope:cluster|volume|series, raised_at_scene, expected_payoff_window:'N-M cluster',
    gap_type(可选·Sternberg 三态 suspense|curiosity|surprise·非法/缺省→None)}],
    answered:[{qid, answered_at_scene}]}。本步把它确定性 append 进 戏剧问题账本.json.clusters[<cid>]
    —— 读者粘性唯一宏观结构缺口（读者追读=想知道核心二元问题的答案·SOTA=Cambridge2026 PITQ +
    McKee MDQ + Loewenstein 信息缺口 + Zeigarnik 未完成张力）。

    🔴 只登记 active cluster（fluid·北极星：绝不预设 cluster_002+ 的问题）：JudgeReport 已是本 cluster
    范围·account 归到本 cid。
    幂等·去重：raised 按 qid 去重（qid 全局唯一）·answered 按 qid 去重（标对应 qid 闭合）。re-apply 同
    JudgeReport 不重复 append、不写盘 churn。
    foreshadower JudgeReport 与 dramatic_questions 字段是 required 产物。缺报告/缺字段/格式错即 return 2，
    防止 foreshadower 漏产被吞掉。raised/answered 均空表示生产者明确判断本 cluster 无戏剧问题变更，
    幂等通过。账本缺/坏仍从空骨架重建，因为这是本脚本拥有的辅助态文件。

    🔴 2026-07-07 S3 类级契约（answered = 戏剧问题终态转移·写入层硬校验·详见模块头契约段）：
      ① 已在其他 cluster 闭合的 qid → 拒绝重复终态转移 + stderr 警告（本 cluster 内重复走
        seen_answered 幂等静默跳过·不算拒绝）。qid 不在任何 raised ≠ 非法——跨 cluster 闭合/
        账本重建后 raised 可缺失（test_answered_cross_cluster_qid_registered 钉死该口径），
        只堵可确证的「非 open」。
      ② answered_at_scene（正文位置证据）必须可解析为 int——缺证据 → 拒绝该条 + 警告。
      拒绝 = 该条不落库但不整体崩；明细进 WAL（.wal/<cid>_terminal_contract.json
      sections.dramatic_questions）。不新增 hard_gate code。
    """
    try:
        db = root / "_数据库"
        cid = _require_cluster_id(cluster_key)
        rpt = db / ".judge_reports" / f"{cid}_foreshadower.json"
        if not rpt.is_file():
            logger.info(f"[dramatic-questions] {cid} foreshadower JudgeReport 不存在（required 产物缺失）")
            return 2
        sf = (load_json(rpt, {}) or {}).get("specific_findings") or {}
        dq = sf.get("dramatic_questions")
        if not isinstance(dq, dict):
            logger.info(f"[dramatic-questions] {cid} 缺 dramatic_questions dict（required 字段缺失）")
            return 2
        raised = dq.get("raised") if isinstance(dq.get("raised"), list) else []
        answered = dq.get("answered") if isinstance(dq.get("answered"), list) else []
        if not raised and not answered:
            ledger_path = db / "戏剧问题账本.json"
            ledger = load_json(ledger_path, None)
            if not isinstance(ledger, dict):
                ledger = {"schema_version": 1, "clusters": {}}
            clusters = ledger.get("clusters")
            if not isinstance(clusters, dict):
                clusters = ledger["clusters"] = {}
            clusters.setdefault(cid, {"raised": [], "answered": []})
            save_json(ledger_path, ledger)
            logger.info(f"[dramatic-questions] {cid} raised/answered 均空（生产者明确无戏剧问题变更·幂等通过）")
            return 0

        ledger_path = db / "戏剧问题账本.json"
        ledger = load_json(ledger_path, None)
        if not isinstance(ledger, dict):
            ledger = {"schema_version": 1, "clusters": {}}  # 辅助态文件·缺/坏从空骨架重建
        clusters = ledger.get("clusters")
        if not isinstance(clusters, dict):
            clusters = ledger["clusters"] = {}
        entry = clusters.get(cid)
        if not isinstance(entry, dict):
            entry = clusters[cid] = {"raised": [], "answered": []}
        e_raised = entry.get("raised")
        if not isinstance(e_raised, list):
            e_raised = entry["raised"] = []
        e_answered = entry.get("answered")
        if not isinstance(e_answered, list):
            e_answered = entry["answered"] = []

        seen_raised = {r.get("qid") for r in e_raised if isinstance(r, dict)}
        seen_answered = {a.get("qid") for a in e_answered if isinstance(a, dict)}
        # 🔴 S3 契约：其他 cluster 已闭合的 qid 索引（qid → 闭合处 cluster·重复闭合硬拒依据）
        answered_elsewhere = {}
        for _ocid, _oent in clusters.items():
            if _ocid == cid or not isinstance(_oent, dict):
                continue
            for _a in _oent.get("answered") or []:
                if isinstance(_a, dict) and _a.get("qid"):
                    answered_elsewhere.setdefault(_a["qid"], _ocid)
        dq_rejections = []
        added_r = added_a = 0
        for r in raised:
            if not isinstance(r, dict):
                continue
            qid = r.get("qid")
            if not qid or qid in seen_raised:
                continue  # qid 全局唯一·幂等去重
            scope = r.get("scope") if r.get("scope") in ("cluster", "volume", "series") else "cluster"
            try:
                ras = int(r.get("raised_at_scene")) if r.get("raised_at_scene") is not None else None
            except (TypeError, ValueError):
                ras = None
            # 🔴 2026-06-29 Sternberg 读者知识缺口三态：gap_type 随 raised 回库（白名单字段需显式带·
            # 否则被丢）·只钳到合法三态·非法/缺省 → None（默认安全·旧账本/慢热单一缺口合法·不报错）。
            gap_type = r.get("gap_type") if r.get("gap_type") in ("suspense", "curiosity", "surprise") else None
            e_raised.append({
                "qid": qid,
                "question": r.get("question") or "",
                "scope": scope,
                "raised_at_scene": ras,
                "expected_payoff_window": r.get("expected_payoff_window") or "",
                "gap_type": gap_type,
            })
            seen_raised.add(qid)
            added_r += 1
        for a in answered:
            if not isinstance(a, dict):
                continue
            qid = a.get("qid")
            if not qid or qid in seen_answered:
                continue  # 标对应 qid 闭合·幂等去重
            # 🔴 2026-07-07 S3 类级契约：answered 终态转移写入层硬校验（见 docstring ①②）
            if qid in answered_elsewhere:
                dq_rejections.append({"qid": qid, "code": "target_not_open",
                                      "note": f"已在 {answered_elsewhere[qid]} 闭合·拒绝重复终态转移"})
                sys.stderr.write(f"[terminal-contract] WARNING: {cid} 戏剧问题 {qid} 已在 "
                                 f"{answered_elsewhere[qid]} 闭合——拒绝重复 answered（该条不落库·其余照常）\n")
                sys.stderr.flush()
                continue
            try:
                aas = int(a.get("answered_at_scene")) if a.get("answered_at_scene") is not None else None
            except (TypeError, ValueError):
                aas = None
            if aas is None:
                dq_rejections.append({"qid": qid, "code": "evidence_missing",
                                      "note": "缺 answered_at_scene 正文位置证据"})
                sys.stderr.write(f"[terminal-contract] WARNING: {cid} 戏剧问题 {qid} answered 缺 "
                                 f"answered_at_scene 正文证据——拒绝终态转移（该条不落库·其余照常）\n")
                sys.stderr.flush()
                continue
            e_answered.append({"qid": qid, "answered_at_scene": aas})
            seen_answered.add(qid)
            added_a += 1

        # 🔴 S3 契约 WAL 审计留痕（每次运行覆盖最新·含空 rejections 便于核对）
        record_terminal_contract(db, cid, "dramatic_questions",
                                 {"raised_applied": added_r, "answered_applied": added_a,
                                  "rejections": dq_rejections})

        if added_r or added_a:
            ledger.setdefault("schema_version", 1)
            save_json(ledger_path, ledger)
            logger.info(f"[dramatic-questions] {cid} 回库 raised+{added_r} / answered+{added_a}"
                        " → 戏剧问题账本（PITQ/MDQ·读者粘性 STATE）")
        else:
            logger.info(f"[dramatic-questions] {cid} 无新增（全已存在·幂等）")
        return 0
    except Exception as e:
        logger.info(f"[dramatic-questions] FATAL: {type(e).__name__}: {str(e)[:120]}")
        return 2


def cmd_apply_cluster_changes(root, cluster_key):
    """v24 cluster 级 apply-changes：展开 cluster chapter_range，for each ch 调 apply_changes。

    2026-05-29 流程贯通（断点 5）：apply 后跑 writer_truth_check（撒谎检测）并入 summary。
    2026-07-05 收敛：本命令是 required 状态落库步骤，任一章 parse/apply 失败、truth-check
    撒谎或异常都必须阻断；summary 仍落盘用于诊断和断点恢复。
    """
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        # 🔴 2026-06-26 fail-fast 走 stderr+flush（同 gen_writer/gen_fixer 修法）
        sys.stderr.write(f"[FATAL save_state] cluster {cluster_key} 未找到 chapter_range\n")
        sys.stderr.flush()
        return 2

    per_chapter_status = []
    failed_chapters = []
    fatal_error = None
    try:
        logger.info(f"[cluster {cluster_key}] 展开 {len(chapters)} 章 → 逐章 apply-changes")
        for ch in chapters:
            logger.info(f"  → ch{ch}")
            # 单章失败（含未捕获异常）立即停止；禁止半截 cluster 继续做状态投影。
            try:
                prc = cmd_parse(root, ch)
                arc = apply_changes(root, ch) if prc == 0 else None
                status = {"ch": ch, "parse_rc": prc, "apply_rc": arc}
                if prc != 0 or (arc is not None and arc != 0):
                    failed_chapters.append(ch)
                    per_chapter_status.append(status)
                    logger.info(f"  🔴 ch{ch} parse/apply 失败（立即阻断）: {status}")
                    break
            except Exception as e:
                status = {"ch": ch, "error": f"{type(e).__name__}: {str(e)[:160]}"}
                failed_chapters.append(ch)
                per_chapter_status.append(status)
                logger.info(f"  🔴 ch{ch} apply 异常（立即阻断）: {status['error']}")
                break
            per_chapter_status.append(status)
        ok_count = len(chapters) - len(failed_chapters)
        logger.info(f"[OK] cluster {cluster_key} apply-changes 完成 {ok_count}/{len(chapters)} 章"
              + (f"（{len(failed_chapters)} 章失败: {failed_chapters}）" if failed_chapters else ""))

        if not failed_chapters:
            # 🔴 2026-06-27 P0：cluster 级回写 进度.json 元数据（current_cluster/book_title/completed）
            _writeback_cluster_progress(root, cluster_key, chapters)
            # 🔴 2026-06-28：标记 parent_me status=completed + 补 ME_to_advance（内容状态一致性）
            _mark_cluster_me_completed(root, cluster_key)
            # 🔴 2026-06-28 审计清理B类：原 _persist_cluster_locked_facts（读 writer factual 写
            #   事件簇.locked_facts）已删除——locked_facts 由 apply_archive.py 从 archive 写（Claude 权威）。
            # 🔴 2026-06-28：brief 规划伏笔注册伏笔表（排桥接前使可立即 resolve）
            _register_brief_foreshadowings(root, cluster_key)
            # 🔴 2026-06-28：foreshadower payoff 桥接伏笔表 resolution（foreshadower 跑完后 re-apply 生效）
            _apply_foreshadower_payoffs(root, cluster_key)

            # writer 撒谎检测（apply 落地后跑；撒谎/异常即阻断）
            # 🔴 2026-06-27 C11：cluster 级一次检测（opening 验首章 / ending 验末章 / anchors 验全拼接）
            truth = _run_writer_truth_check(root, chapters)
            if truth["lies_total"] > 0:
                logger.info(f"[truth-check] 🔴 检测到 {truth['lies_total']} 条撒谎"
                      f"（writer 声明与正文不符 · cluster 级）")
            else:
                logger.info(f"[truth-check] ✅ cluster {len(chapters)} 章无撒谎"
                      + (f" · 检测异常（已记录·{truth['errors']}）" if truth["errors"] else ""))
            # 🔴 2026-06-27 SYS-3/C10：声明-vs-正文弱信号列主代理待裁决（不写权威账本）
            _nt = truth.get("foreshadowing_no_trace", 0)
            _uc = truth.get("factual_uncorroborated", 0)
            if _nt or _uc:
                logger.info(f"[声明-vs-正文] 🟡 诊断信号：申报兑现但正文 0 痕迹 {_nt} 条 / "
                            f"factual 弱信号未印证 {_uc} 条（账本未写入·详见 故事块摘要.factual_corroboration）")
    except Exception as e:
        fatal_error = f"{type(e).__name__}: {str(e)[:160]}"
        logger.info(f"[apply-cluster] FATAL: 状态投影失败: {fatal_error}")
    finally:
        # 2026-05-29 复审修复 [M3]：truth-check + 写盘放 finally——任何异常后都落地 summary
        summary = {
            "cluster_id": cluster_key,
            "chapters": chapters,
            "applied_at": datetime.now().isoformat(timespec="seconds"),
            "per_chapter_status": per_chapter_status,
            "failed_chapters": failed_chapters,
            "writer_truth_check": locals().get("truth"),
            "fatal_error": fatal_error,
        }
        out = root / "_数据库" / ".wal" / f"{cluster_key}_apply_cluster.json"
        save_json(out, summary)
        logger.info(f"[apply-cluster] summary → {out.name}")
    if failed_chapters:
        logger.info(f"[apply-cluster] FATAL: {len(failed_chapters)} 章 parse/apply 失败: {failed_chapters}")
        return 2
    if fatal_error:
        return 2
    truth_result = locals().get("truth")
    if not isinstance(truth_result, dict):
        logger.info("[apply-cluster] FATAL: writer_truth_check 未执行")
        return 2
    if truth_result.get("errors"):
        logger.info(f"[apply-cluster] FATAL: writer_truth_check 异常: {truth_result['errors']}")
        return 2
    if truth_result.get("lies_total", 0) > 0:
        logger.info(f"[apply-cluster] FATAL: writer_truth_check 命中 {truth_result['lies_total']} 条撒谎")
        return 2
    return 0


def cmd_git_commit_cluster(root, cluster_key):
    """v24 cluster 级 git commit：1 个 cluster 1 个 commit"""
    norm_cid = _require_cluster_id(cluster_key)
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        # 🔴 2026-06-26 fail-fast 走 stderr+flush（同 gen_writer/gen_fixer 修法）
        sys.stderr.write(f"[FATAL save_state] cluster {cluster_key} 未找到 chapter_range\n")
        sys.stderr.flush()
        return 2
    marker = _wal_dir(root) / f"{norm_cid}_git_commit.json"
    import subprocess as _sp
    _cnum = f"{cluster_lookup.cluster_num(norm_cid):03d}"
    msg = f"feat(cluster-{_cnum}): {len(chapters)} 章 (ch{chapters[0]}-{chapters[-1]})"
    marker_payload = _git_commit_marker_payload(norm_cid, chapters, msg)
    try:
        _sp.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True, timeout=30)
        r = _sp.run(["git", "-C", str(root), "commit", "-m", msg],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode == 0:
            sha_r = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            check=True, timeout=10)
            sha = (sha_r.stdout or "").strip()
            save_json(marker, marker_payload)
            # marker 落在 .wal/（项目 .gitignore 惯例忽略 WAL 目录），但它是本 cluster 提交的
            # provenance 记录·代码本意就是要 amend 进提交 → 必须 -f 强制越过 gitignore（否则
            # 凡 .gitignore 含 _数据库/.wal/ 的书首次 cluster 提交必崩·数据提交已成功却 exit 2）。
            _sp.run(["git", "-C", str(root), "add", "-f", str(marker)], check=True, capture_output=True, timeout=30)
            amend = _sp.run(["git", "-C", str(root), "commit", "--amend", "--no-edit"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if amend.returncode != 0:
                raise RuntimeError((amend.stderr or amend.stdout or "")[:300])
            sha2 = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           check=True, timeout=10).stdout.strip()
            logger.info(f"[GIT] cluster_{cluster_key} 快照 {sha}: {msg}")
            logger.info(f"[GIT] marker → {marker.name} · amended HEAD {sha2}")
        else:
            combined = (r.stderr or r.stdout or "")
            if "nothing to commit" in combined.lower() or "working tree clean" in combined.lower():
                head_msg = _sp.run(["git", "-C", str(root), "log", "-1", "--pretty=%s"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   check=True, timeout=10).stdout.strip()
                if msg not in head_msg and f"cluster-{_cnum}" not in head_msg:
                    raise RuntimeError(f"无可提交内容，但 HEAD 不是本 cluster 提交: {head_msg}")
                sha = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              check=True, timeout=10).stdout.strip()
                existing_marker = load_json(marker, None)
                if existing_marker != marker_payload:
                    save_json(marker, marker_payload)
                    # 同上：.wal/ 被 gitignore，provenance marker 须 -f 强制入库
                    _sp.run(["git", "-C", str(root), "add", "-f", str(marker)], check=True, capture_output=True, timeout=30)
                    amend = _sp.run(["git", "-C", str(root), "commit", "--amend", "--no-edit"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                    if amend.returncode != 0:
                        raise RuntimeError((amend.stderr or amend.stdout or "")[:300])
                    sha = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  check=True, timeout=10).stdout.strip()
                logger.info(f"[GIT] {norm_cid} 已提交且工作区干净，幂等通过: {sha}")
            else:
                raise RuntimeError(combined[:300])
    except _sp.TimeoutExpired:
        logger.info(f"[GIT] cluster_{cluster_key} commit 超时 (>30s)")
        return 2
    except Exception as e:
        logger.info(f"[GIT] {e}")
        return 2
    return 0


def cmd_auto_post_reflect_cluster(root, cluster_key):
    """运行 cluster 级 learning_loop 三步链：
      1. learning_loop --merge-reflection .wal/<cluster_key>_reflection.json
      2. learning_loop --ingest .audit/cluster_<key>_audit.json（见 audit_hub.py:1399）
      3. learning_loop --scan-recurring
    退出码语义：返回 0 成功；reflection/audit/learning_loop/data_flywheel 是主链必需步骤，
    缺失或失败返回 2。
    """
    db = root / "_数据库"
    norm_cid = _require_cluster_id(cluster_key)

    learning_loop = scripts_dir() / "learning_loop.py"  # frozen-aware（狩猎修·exe下学习闭环静默不跑）
    if not learning_loop.is_file():
        logger.info(f"[auto-post-reflect-cluster] learning_loop.py 不存在")
        return 2

    refl_path = db / ".wal" / f"{norm_cid}_reflection.json"
    audit_path = db / ".audit" / f"{norm_cid}_audit.json"

    project_str = str(root)
    steps_ran = 0
    failures: list[str] = []

    # Step 1: merge cluster reflection → 写作经验.success/failure_patterns
    if refl_path.is_file():
        try:
            _run_required(
                [child_python(), str(learning_loop), project_str, "--merge-reflection",
                 refl_path.relative_to(root).as_posix()],
                timeout=180,
            )
            logger.info(f"[auto-post-reflect-cluster] step 1/3 merge-reflection OK ({refl_path.name})")
            steps_ran += 1
        except Exception as e:
            failures.append(f"merge-reflection: {e}")
            logger.info(f"[auto-post-reflect-cluster] step 1/3 merge-reflection FAIL: {e}")
    else:
        failures.append("cluster reflection 报告不存在")
        logger.info(f"[auto-post-reflect-cluster] step 1/3 缺失：cluster reflection 报告不存在"
              f"（找过 {norm_cid}_reflection.json 等）")

    # Step 2: ingest cluster audit → _recurrence_tracker / _waiver_tracker
    if audit_path.is_file():
        try:
            r = subprocess.run(
                [child_python(), str(learning_loop), project_str, "--ingest",
                 audit_path.relative_to(root).as_posix()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
            if r.returncode in (0, 1):  # 1 = 检测到复发问题，不是错
                logger.info(f"[auto-post-reflect-cluster] step 2/3 ingest OK ({audit_path.name}, rc={r.returncode})")
                steps_ran += 1
            else:
                raise RuntimeError((r.stderr or r.stdout or "")[:300])
        except Exception as e:
            failures.append(f"ingest: {e}")
            logger.info(f"[auto-post-reflect-cluster] step 2/3 ingest FAIL: {e}")
    else:
        failures.append("cluster audit 报告不存在")
        logger.info(f"[auto-post-reflect-cluster] step 2/3 缺失：cluster audit 报告不存在"
              f"（找过 {norm_cid}_audit.json 等）")

    # Step 3: scan-recurring → 跨 cluster 复发追踪 + tool_calibration_suggestions
    try:
        r = subprocess.run(
            [child_python(), str(learning_loop), project_str, "--scan-recurring"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        if r.returncode in (0, 1):
            logger.info(f"[auto-post-reflect-cluster] step 3/3 scan-recurring OK (rc={r.returncode})")
            steps_ran += 1
        else:
            raise RuntimeError((r.stderr or r.stdout or "")[:300])
    except Exception as e:
        logger.info(f"[auto-post-reflect-cluster] step 3/3 scan-recurring FAIL: {e}")
        failures.append(f"scan-recurring: {e}")

    # Step 4: DataFlywheel → cluster 训练样本池（paragraph/fix_pair/weak/strong/judge/checker/fixer/repair/reading/audit-meta labels）。
    # 创作入口默认打开 RUOYU_DATA_FLYWHEEL；主链里关闭即失败，避免 required 学习链变空跑。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "flywheel"))
        import data_collector as _data_collector
        fly = _data_collector.ClusterDataCollector(project_str, norm_cid).collect()
        if fly.get("skipped"):
            failures.append(f"data-flywheel skipped: {fly.get('reason')}")
            logger.info(f"[data-flywheel] {norm_cid} 缺失：{fly.get('reason')}")
        else:
            logger.info(f"[data-flywheel] {norm_cid} 训练样本收集 total={fly.get('total', 0)} "
                        f"paragraphs={fly.get('paragraphs', 0)} weak={fly.get('weak_labels', 0)} "
                        f"strong={fly.get('strong_labels', 0)} fixes={fly.get('fix_pairs', 0)} "
                        f"judge_reports={fly.get('judge_reports', 0)} "
                        f"checker_briefs={fly.get('checker_briefs', 0)} "
                        f"fixer_reports={fly.get('fixer_reports', 0)} "
                        f"repair_reports={fly.get('repair_reports', 0)} "
                        f"reading_reflections={fly.get('reading_reflections', 0)} "
                        f"audit_metadata={fly.get('audit_metadata', 0)}")
            steps_ran += 1
    except Exception as e:  # noqa: BLE001
        logger.info(f"[data-flywheel] {norm_cid} 收集失败: {type(e).__name__}: {str(e)[:160]}")
        failures.append(f"data-flywheel: {type(e).__name__}: {str(e)[:160]}")

    marker = _wal_dir(root) / f"{norm_cid}_post_reflect.json"
    save_json(marker, {
        "cluster_id": norm_cid,
        "steps_ran": steps_ran,
        "required_steps": 4,
        "failures": failures,
        "completed": not failures and steps_ran == 4,
    })
    if failures or steps_ran != 4:
        logger.info(f"[auto-post-reflect-cluster] FATAL {cluster_key}: 完成 {steps_ran}/4，失败 {failures}")
        return 2
    logger.info(f"[auto-post-reflect-cluster] {cluster_key} 完成 {steps_ran}/4 步 → {marker.name}")
    return 0


# 🔴 2026-05-29 流程贯通（断点 5 死代码清理）：cmd_report_cluster 已删除
# （依赖已删的 cmd_report，且无 plan/命令文档调 --report-cluster）。


def main():
    # 公共 CLI 只暴露 cluster 级子命令；章级函数由 cluster 命令内部复用。
    import sys as _sys_nn
    import nn_runtime_defaults
    _nn_on = nn_runtime_defaults.enable_creative_nn_defaults()
    if _nn_on:
        print(f"[nn] 创作 save_state 默认开启模型/可成长门控: {', '.join(_nn_on)}", file=_sys_nn.stderr)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "registry"))
        import model_registry as _model_registry
        _reg = _model_registry.sync_runtime_models()
        if _reg.get("count"):
            print(f"[model-registry] 同步运行模型 { _reg['count'] } 个", file=_sys_nn.stderr)
    except Exception as _e:  # noqa: BLE001
        print(f"[model-registry] FATAL: {type(_e).__name__}: {str(_e)[:120]}", file=_sys_nn.stderr)
        sys.exit(2)
    ap = argparse.ArgumentParser(description="save_state.py · cluster-only CLI")
    ap.add_argument("project", help="项目路径")
    # cluster-level subcommands（唯一 CLI 入口）
    ap.add_argument("--apply-cluster-changes", type=str, metavar="CLUSTER_KEY",
                    help="一次性应用整 cluster 的 changes（内部展开 ch_range）")
    ap.add_argument("--git-commit-cluster", type=str, metavar="CLUSTER_KEY",
                    help="1 cluster 1 commit · msg = feat(cluster-NNN): N 章 (chX-chY)")
    ap.add_argument("--auto-post-reflect-cluster", type=str, metavar="CLUSTER_KEY",
                    help="cluster 级 learning_loop")
    ap.add_argument("--build-cluster-summary", type=str, metavar="CLUSTER_KEY",
                    help="把整 cluster 的富摘要预算写入 故事块摘要.json")
    ap.add_argument("--apply-appraisal-beats", type=str, metavar="CLUSTER_KEY",
                    help="把 summarizer 产出的 appraisal_beats 确定性回填 叙事节拍器.json"
                         "（chain-of-emotion·只 active cluster·幂等·required STATE·未产则 exit2）")
    ap.add_argument("--apply-dramatic-questions", type=str, metavar="CLUSTER_KEY",
                    help="把 foreshadower JudgeReport 的 dramatic_questions 确定性回库"
                         " 戏剧问题账本.json（PITQ/MDQ·读者粘性·只 active cluster·按 qid 幂等去重·"
                         "required STATE·未产则 exit2）")
    ap.add_argument("--detect-volume-boundary", type=str, metavar="CLUSTER_KEY",
                    help="卷边界确定性检测（某卷 ME 池非空且全部 status=completed"
                         " 且 故事块摘要.volume_summaries 尚无该卷）→ 写 .wal/<cid>_volume_boundary"
                         ".json 供主代理条件 spawn novel-summarizer MODE=volume·无边界=零行为变化")
    ap.add_argument("--apply-volume-summary", type=str, metavar="VOLUME_N",
                    help="卷级摘要回库入口——读 .wal/volume_<N>_summary.json 校验"
                         "（结构键钉死 + source 必须恰好覆盖本卷全部 cluster_ids + 卷真闭合）"
                         "→ 幂等 upsert 故事块摘要.volume_summaries[]·不合格 exit2 零写入")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if not root.exists():
        logger.info(f"项目路径不存在: {root}")
        sys.exit(2)

    # 子命令返回码原样传播给调用方。
    rc = 0
    if args.apply_cluster_changes:
        rc = cmd_apply_cluster_changes(root, args.apply_cluster_changes)
    elif args.git_commit_cluster:
        rc = cmd_git_commit_cluster(root, args.git_commit_cluster)
    elif args.auto_post_reflect_cluster:
        rc = cmd_auto_post_reflect_cluster(root, args.auto_post_reflect_cluster)
    elif args.apply_appraisal_beats:
        rc = cmd_apply_appraisal_beats(root, args.apply_appraisal_beats)
    elif args.apply_dramatic_questions:
        rc = cmd_apply_dramatic_questions(root, args.apply_dramatic_questions)
    elif args.detect_volume_boundary:
        rc = cmd_detect_volume_boundary(root, args.detect_volume_boundary)
    elif args.apply_volume_summary:
        rc = cmd_apply_volume_summary(root, args.apply_volume_summary)
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
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
