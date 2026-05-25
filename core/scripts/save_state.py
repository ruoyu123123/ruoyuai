#!/usr/bin/env python3
"""
save_state.py — 状态保存流水线的确定性部分

子命令：
  --wal-start <ch>       开始 WAL 日志
  --wal-step <ch> <n>    标记第 n 步完成
  --wal-end <ch>         标记完成并清理
  --parse <ch>           读第 ch 章 CHANGES（统一走 chapter_io，v18 分离稿/旧混合稿通吃）
  --apply-changes <ch>   将 CHANGES 落地到 13 个 JSON（第2/4/5步核心）
  --git-commit <ch>      章节快照 Git commit
  --report <ch>          输出第10步报告

所有子命令都幂等（重复执行不产生副作用或破坏数据）。
确定性逻辑集中在此，AI 只负责：章纲摘要/写作反思/走向卡片。
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# v18：统一章节读写走 chapter_io，杜绝各脚本各自 split
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio


# ============ IO ============

def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_chapter_file(root: Path, ch: int) -> Path | None:
    """定位章节正文文件。v18：委托 chapter_io.find_body_file（兼容 4 布局 + 旧平铺）。"""
    return cio.find_body_file(root, ch)


# ============ WAL ============

def wal_path(root: Path, ch: int) -> Path:
    # zero-pad 命名，与 plan 模板 expected_outputs 对齐
    p = root / "_数据库" / ".wal" / f"第{ch:03d}章_save_state.json"
    if not p.exists():
        # 兼容历史原长命名
        legacy = root / "_数据库" / ".wal" / f"第{ch}章_save_state.json"
        if legacy.exists():
            return legacy
    return p


def cmd_wal_start(root: Path, ch: int):
    p = wal_path(root, ch)
    save_json(p, {
        "chapter": ch,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "current_step": 0,
        "completed_steps": [],
        "status": "in_progress",
    })
    print(f"[WAL] 已开启 {p.relative_to(root)}")


def cmd_wal_step(root: Path, ch: int, step: int):
    p = wal_path(root, ch)
    d = load_json(p, {})
    d["current_step"] = step
    completed = d.setdefault("completed_steps", [])
    if step not in completed:
        completed.append(step)
    save_json(p, d)


def cmd_wal_end(root: Path, ch: int):
    # 标记 wal_end 时间戳并保留文件（plan-end 校验 + /continue 断点恢复依赖此文件）
    p = wal_path(root, ch)
    if p.exists():
        d = load_json(p, {})
        d["status"] = "completed"
        d["ended_at"] = datetime.now().isoformat(timespec="seconds")
        save_json(p, d)
        print(f"[WAL] 已完成（{p.name} 标记 status=completed，保留供 plan-end 校验）")
    else:
        print(f"[WAL] 未找到 {p.name}，跳过")


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
    strategy = "v18_changes_json" if cp.is_file() else "v18_legacy_compat"
    return result, strategy


def cmd_parse(root: Path, ch: int):
    if not find_chapter_file(root, ch) and not cio.changes_path(root, ch).is_file():
        print(f"[ERROR] 第{ch}章 正文/CHANGES 均未找到", file=sys.stderr)
        sys.exit(2)
    changes, strategy = parse_changes(root, ch)
    if changes is None:
        print(f"[PARSE] CHANGES 解析失败（无 _changes.json 且旧稿无 CHANGES 段），需要 AI agent 兜底",
              file=sys.stderr)
        sys.exit(1)
    out = root / "_数据库" / ".wal" / f"第{ch}章_parsed.json"
    save_json(out, {"strategy": strategy, "changes": changes})
    print(f"[PARSE] 策略 {strategy} 成功 → {out.relative_to(root)}")


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


def apply_changes(root: Path, ch: int):
    """将 parsed 的 CHANGES 落地到对应 JSON。
    v16: 先生成声明式Patch文件（可审计/可回放），再执行实际修改。
    """
    db = root / "_数据库"
    parsed_path = db / ".wal" / f"第{ch}章_parsed.json"
    parsed = load_json(parsed_path, {})
    changes = parsed.get("changes", {})
    if not changes:
        print(f"[APPLY] 无 parsed CHANGES，跳过", file=sys.stderr)
        sys.exit(1)

    # v16: 生成并保存声明式Patch
    patches = _generate_patch(changes, ch)
    patch_path = db / ".wal" / f"第{ch}章_patch.json"
    save_json(patch_path, {"chapter": ch, "patch_count": len(patches), "patches": patches,
                           "generated_at": datetime.now().isoformat(timespec="seconds")})
    print(f"[PATCH] 生成 {len(patches)} 条声明式补丁 → {patch_path.name}")

    summary = {"applied": [], "warnings": [], "patch_file": str(patch_path.name)}

    # --- 伏笔表（第4步）---
    fs = load_json(db / "伏笔表.json", {"promises": [], "deadlines": [],
                                        "pledges": [], "secrets": []})
    for act in changes.get("foreshadowing_actions", []):
        cat = act.get("category")
        typ = act.get("type")
        fid = act.get("id")
        if cat == "promise":
            if typ == "setup":
                fs["promises"].append({
                    "id": fid, "setup_ch": ch, "tier": act.get("tier", 3),
                    "description": act.get("description", ""),
                    "due_by": act.get("due_by", ch + 20),
                    "resolved": False,
                })
                summary["applied"].append(f"伏笔 setup: {fid}")
            elif typ == "payoff":
                for p in fs["promises"]:
                    if p.get("id") == fid:
                        p["resolved"] = True
                        p["resolved_at_ch"] = ch
                        summary["applied"].append(f"伏笔 payoff: {fid}")
                        break
                else:
                    summary["warnings"].append(f"payoff 引用了不存在的伏笔: {fid}")
        elif cat == "deadline":
            if typ == "raise":
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
            if typ == "make":
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
                fs["secrets"].append({
                    "id": fid,
                    "secret": act.get("description", ""),
                    "established_ch": ch,
                    "reveal_at_ch": act.get("reveal_at_ch", ch + 50),
                    "known_by": act.get("known_by", []),
                    "status": "hidden",
                })
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
    for cc in changes.get("character_changes", []):
        name = cc.get("name", "")
        if name in char_map:
            char = char_map[name]
            char.setdefault("growth_arc", []).append({
                "ch": ch,
                "state": cc.get("to", ""),
                "key_change": f"{cc.get('field', '')}: {cc.get('from', '')} → {cc.get('to', '')}",
                "trigger": cc.get("trigger", ""),
            })
            summary["applied"].append(f"角色弧: {name} ch{ch} {cc.get('field', '')}")
    # 新角色注册
    for ne in (changes.get("new_entities", {}) or {}).get("characters", []):
        ne_name = ne.get("name", "")
        if ne_name and ne_name not in char_map:
            cards["characters"].append({
                "id": ne_name.lower().replace(" ", "_"),
                "name": ne_name,
                "role": ne.get("role", "配角"),
                "first_appear_ch": ch,
                "growth_arc": [{"ch": ch, "state": "初次登场", "key_change": "出场", "trigger": ""}],
            })
            summary["applied"].append(f"新角色: {ne_name}")
    save_json(db / "人物卡.json", cards)

    # --- 进度（completed+1, current+1）---
    progress = load_json(db / "进度.json", {})
    progress["completed"] = max(progress.get("completed", 0), ch)
    progress["current"] = progress["completed"] + 1
    save_json(db / "进度.json", progress)
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
        if "current_time" in tl and ta.get("period"):
            tl["current_time"]["period"] = ta["period"]
            tl["current_time"]["chapter"] = ch
        tl.setdefault("time_log", []).append({
            "ch": ch, "elapsed": ta.get("elapsed", ""),
            "key_events": ta.get("key_events", []),
        })
        save_json(db / "时间线.json", tl)
        summary["applied"].append("时间线: 已推进")

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
    print(f"[APPLY] 完成 {len(summary['applied'])} 项变更"
          + (f", {len(summary['warnings'])} 条警告" if summary["warnings"] else ""))
    for w in summary["warnings"]:
        print(f"  ⚠️ {w}")


# ============ Git commit ============

def cmd_git_commit(root: Path, ch: int):
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[GIT] git 不可用，跳过")
        return
    if not (root / ".git").exists():
        print("[GIT] 非 git 仓库，跳过")
        return

    f = find_chapter_file(root, ch)
    if not f:
        print(f"[GIT] 第{ch}章 txt 未找到", file=sys.stderr)
        return

    # v18：正文经 cio 剥离 CHANGES，字数用统一口径
    body = cio.read_body(root, ch)
    words = cio.count_words(body)
    title_match = re.search(r"第\d+章[_·]?(.+?)\.txt", f.name)
    title = title_match.group(1) if title_match else ""
    if not title:
        prog = load_json(root / "_数据库" / "进度.json", {})
        for cp in prog.get("chapter_plan", []):
            if cp.get("ch") == ch:
                title = cp.get("title", "")
                break

    try:
        # 把正文 txt + 同目录 _changes.json（若存在）一并纳入快照
        add_paths = [str(f.relative_to(root)), "_数据库/"]
        cp_file = cio.changes_path(root, ch)
        if cp_file.is_file():
            add_paths.insert(1, str(cp_file.relative_to(root)))
        subprocess.run(["git", "add", *add_paths], cwd=root, check=True)
        msg = f"feat(ch-{ch}): {title} ({words}字)"
        subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True,
                       capture_output=True)
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=root, capture_output=True, text=True, check=True)
        print(f"[GIT] 快照 {result.stdout.strip()}: {msg}")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="ignore")[:200]
        print(f"[GIT] commit 失败: {err}", file=sys.stderr)


# ============ 报告（第10步）============

def cmd_report(root: Path, ch: int):
    db = root / "_数据库"
    applied = load_json(db / ".wal" / f"第{ch}章_applied.json", {})
    progress = load_json(db / "进度.json", {})

    print(f"\n💾 状态已保存 — 第{ch}章\n")
    print(f"  进度：{progress.get('completed', 0)}/{progress.get('total_chapters', '?')}")
    print(f"  本章变更：{len(applied.get('applied', []))} 项")
    if applied.get("warnings"):
        print(f"  ⚠️ 警告：{len(applied['warnings'])} 条")
        for w in applied["warnings"][:3]:
            print(f"    - {w}")


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
    script_root = Path(__file__).parent
    learning_loop = script_root / "learning_loop.py"

    reflector_json = root / "_数据库" / ".judge_reports" / f"ch_{ch:03d}_reflector.json"
    audit_json = root / "_数据库" / ".audit" / f"ch_{ch:03d}_audit.json"

    steps_ran = 0
    steps_skipped = 0

    # Step 1: merge reflector → 写作经验.success/failure_patterns
    if reflector_json.is_file():
        rel_path = reflector_json.relative_to(root).as_posix()
        r = subprocess.run(
            [sys.executable, str(learning_loop), project_str, "--merge-reflection", rel_path],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print(f"[auto-post-reflect] step 1/3 merge-reflection OK")
            for line in (r.stdout or "").splitlines()[-3:]:
                print(f"  {line}")
            steps_ran += 1
        else:
            print(f"[auto-post-reflect] step 1/3 merge-reflection FAIL: {r.stderr[:200]}")
            steps_skipped += 1
    else:
        print(f"[auto-post-reflect] step 1/3 跳过：reflector 报告不存在 ({reflector_json.name})")
        steps_skipped += 1

    # Step 2: ingest audit → _recurrence_tracker / _waiver_tracker
    if audit_json.is_file():
        rel_path = audit_json.relative_to(root).as_posix()
        r = subprocess.run(
            [sys.executable, str(learning_loop), project_str, "--ingest", rel_path],
            capture_output=True, text=True
        )
        if r.returncode in (0, 1):  # 1 = 检测到复发问题，不是错
            print(f"[auto-post-reflect] step 2/3 ingest OK (rc={r.returncode})")
            for line in (r.stdout or "").splitlines()[-3:]:
                print(f"  {line}")
            steps_ran += 1
        else:
            print(f"[auto-post-reflect] step 2/3 ingest FAIL: {r.stderr[:200]}")
            steps_skipped += 1
    else:
        print(f"[auto-post-reflect] step 2/3 跳过：audit 报告不存在 ({audit_json.name})")
        steps_skipped += 1

    # Step 3: scan-recurring → 跨章复发追踪 + tool_calibration_suggestions
    r = subprocess.run(
        [sys.executable, str(learning_loop), project_str, "--scan-recurring"],
        capture_output=True, text=True
    )
    if r.returncode in (0, 1):
        print(f"[auto-post-reflect] step 3/3 scan-recurring OK (rc={r.returncode})")
        for line in (r.stdout or "").splitlines()[-3:]:
            print(f"  {line}")
        steps_ran += 1
    else:
        print(f"[auto-post-reflect] step 3/3 scan-recurring FAIL: {r.stderr[:200]}")
        steps_skipped += 1

    print(f"\n[auto-post-reflect] 完成 {steps_ran}/3 步（跳过 {steps_skipped}）")


# ============ v23 ECAS checkpoint ============

def cmd_ecas_checkpoint(root: Path, cluster_id: str) -> None:
    """v23 ECAS: 验证 cluster_draft 完整性 (字数 / sub_summaries / mid_checkpoint_results)。

    输入: cluster_id (如 cluster_002)
    检查项:
    1. 读 章节/cluster_<id>_draft/cluster_<id>_draft.txt 验字数 in expected_word_range
    2. 读 章节/cluster_<id>_draft/cluster_<id>_changes.json.ecas_metadata.checkpoint_data
    3. 验所有 mid_checkpoint_results 字段完整 + passed
    4. 写 _数据库/.ecas_checkpoints/cluster_<id>_final.json (汇总)
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
                        result["passed"] = False  # v23.2: FAIL 不再 warn
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
            chg = _json.loads(changes_path.read_text(encoding="utf-8"))
            ecas_meta = chg.get("ecas_metadata") or {}
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
    final_path = checkpoint_dir / f"{cluster_id}_final.json"
    final_path.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ecas-checkpoint] {cluster_id}: {'PASS' if result['passed'] else 'FAIL'}")
    for k, v in result["checks"].items():
        print(f"  {k}: {v}")
    if result["warnings"]:
        print(f"  warnings: {len(result['warnings'])}")
        for w in result["warnings"]:
            print(f"    - {w}")
    print(f"  summary: {final_path.relative_to(root)}")
    if not result["passed"]:
        sys.exit(1)


# ============ CLI ============

def _get_cluster_chapter_range(project_root, cluster_key):
    """从 事件簇.json 拿 cluster 的 chapter_range，展开 [ch1,...,chN]"""
    import json as _j
    p = project_root / "_数据库" / "事件簇.json"
    if not p.exists():
        return []
    try:
        data = _j.loads(p.read_text(encoding="utf-8"))
        for c in data.get("clusters", []):
            cid = c.get("cluster_id", "")
            if cid == cluster_key or cid.replace("cluster_", "") == cluster_key.replace("cluster_", ""):
                cr = c.get("chapter_range")
                if isinstance(cr, list) and len(cr) == 2:
                    return list(range(cr[0], cr[1] + 1))
    except Exception:
        pass
    return []


def cmd_apply_cluster_changes(root, cluster_key):
    """v24 cluster 级 apply-changes：展开 cluster chapter_range，for each ch 调 apply_changes"""
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        print(f"[FATAL] cluster {cluster_key} 未找到 chapter_range", file=sys.stderr)
        return 2
    print(f"[cluster {cluster_key}] 展开 {len(chapters)} 章 → 逐章 apply-changes")
    for ch in chapters:
        print(f"  → ch{ch}")
        cmd_parse(root, ch)
        apply_changes(root, ch)
    print(f"[OK] cluster {cluster_key} apply-changes 完成 {len(chapters)} 章")


def cmd_git_commit_cluster(root, cluster_key):
    """v24 cluster 级 git commit：1 个 cluster 1 个 commit"""
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        print(f"[FATAL] cluster {cluster_key} 未找到 chapter_range", file=sys.stderr)
        return 2
    # 复用 cmd_git_commit 但 commit msg 改 cluster 级
    import subprocess as _sp
    try:
        _sp.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        msg = f"feat(cluster-{cluster_key}): {len(chapters)} 章 (ch{chapters[0]}-{chapters[-1]})"
        r = _sp.run(["git", "-C", str(root), "commit", "-m", msg],
                    capture_output=True, text=True)
        if r.returncode == 0:
            sha = r.stdout.split()[1].strip("]")[:7] if r.stdout else "?"
            print(f"[GIT] cluster_{cluster_key} 快照 {sha}: {msg}")
        else:
            print(f"[GIT] {r.stderr[:200] or r.stdout[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[GIT] {e}", file=sys.stderr)


def cmd_auto_post_reflect_cluster(root, cluster_key):
    """v24 cluster 级 auto-post-reflect：展开 chapter_range，for each ch 调 auto-post-reflect"""
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        print(f"[FATAL] cluster {cluster_key} 未找到 chapter_range", file=sys.stderr)
        return 2
    for ch in chapters:
        cmd_auto_post_reflect(root, ch)


def cmd_report_cluster(root, cluster_key):
    """v24 cluster 级 report"""
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        print(f"[FATAL] cluster {cluster_key} 未找到 chapter_range", file=sys.stderr)
        return 2
    print(f"[cluster {cluster_key}] 章节范围 ch{chapters[0]}-{chapters[-1]} ({len(chapters)} 章)")
    for ch in chapters:
        cmd_report(root, ch)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目路径")
    # chapter-level subcommands
    ap.add_argument("--wal-start", type=int, metavar="CH")
    ap.add_argument("--wal-step", nargs=2, type=int, metavar=("CH", "STEP"))
    ap.add_argument("--wal-end", type=int, metavar="CH")
    ap.add_argument("--parse", type=int, metavar="CH")
    ap.add_argument("--apply-changes", type=int, metavar="CH")
    ap.add_argument("--git-commit", type=int, metavar="CH")
    ap.add_argument("--report", type=int, metavar="CH")
    ap.add_argument("--auto-post-reflect", type=int, metavar="CH",
                    help="一键跑 learning_loop 三步链 (merge-reflection + ingest + scan-recurring)")
    ap.add_argument("--ecas-checkpoint", type=str, metavar="CLUSTER_ID",
                    help="验证 cluster_draft 完整性 + checkpoint_data")
    # v24 cluster-level subcommands
    ap.add_argument("--apply-cluster-changes", type=str, metavar="CLUSTER_KEY",
                    help="v24: 一次性应用整 cluster 的 changes（内部展开 ch_range for each ch apply）")
    ap.add_argument("--git-commit-cluster", type=str, metavar="CLUSTER_KEY",
                    help="v24: 1 cluster 1 commit · msg = feat(cluster-NNN): N 章 (chX-chY)")
    ap.add_argument("--auto-post-reflect-cluster", type=str, metavar="CLUSTER_KEY",
                    help="v24: cluster 级 learning_loop")
    ap.add_argument("--report-cluster", type=str, metavar="CLUSTER_KEY",
                    help="v24: cluster 级报告")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if not root.exists():
        print(f"项目路径不存在: {root}", file=sys.stderr)
        sys.exit(2)

    if args.wal_start: cmd_wal_start(root, args.wal_start)
    elif args.wal_step: cmd_wal_step(root, args.wal_step[0], args.wal_step[1])
    elif args.wal_end: cmd_wal_end(root, args.wal_end)
    elif args.parse: cmd_parse(root, args.parse)
    elif args.apply_changes: apply_changes(root, args.apply_changes)
    elif args.git_commit: cmd_git_commit(root, args.git_commit)
    elif args.report: cmd_report(root, args.report)
    elif args.auto_post_reflect: cmd_auto_post_reflect(root, args.auto_post_reflect)
    elif args.ecas_checkpoint: cmd_ecas_checkpoint(root, args.ecas_checkpoint)
    elif args.apply_cluster_changes: cmd_apply_cluster_changes(root, args.apply_cluster_changes)
    elif args.git_commit_cluster: cmd_git_commit_cluster(root, args.git_commit_cluster)
    elif args.auto_post_reflect_cluster: cmd_auto_post_reflect_cluster(root, args.auto_post_reflect_cluster)
    elif args.report_cluster: cmd_report_cluster(root, args.report_cluster)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
