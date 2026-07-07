#!/usr/bin/env python3
"""cluster_choice_apply.py — 把用户选定的下一 cluster brief 写入 事件簇.json（程序驱动 M3）

cluster-save-state step 11 停顿点之后的确定性数据管道：
    emergence 引擎产 candidates → outline-planner 详化 → 用户选 1 个（orchestrator
    pause_for_user 落 answer_artifact）→ 本脚本 upsert 进 事件簇.json.clusters[]。

北极星边界：本脚本零创作判断——候选内容由引擎+planner 运行时产生、用户做选择，
这里只做机械写入。status 写 "in_progress"（build_manifest._EVENT_CLUSTER_ACTIVE_STATUSES
白名单成员——memory feedback_build_manifest_cluster_brief_injection_gate 实证 "active"
不在白名单 → brief 注入静默 off → writer 偏短偏离，此处根治该坑）。

用法:
    python cluster_choice_apply.py <project_root> --next-key 002 \
        --choice _数据库/.wal/cluster_002_user_choice.json
退出码: 0 成功 / 1 输入缺失或格式错 / 2 致命
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _try_fix_ascii_quote_pollution(raw: str) -> tuple[str, int]:
    """🔴 2026-06-26 加（cluster_001 翻车 sediment）：LLM agent 写 JSON 时常把中文短语用
    ASCII 双引号包起来（如 `"利息回血"`），破坏 JSON 结构。

    检测：在 JSON 字符串值（即 `: "..."` 或 `, "..."` 后到对应 `"` 闭合）内部，把所有
    CJK 字符之间夹的 ASCII 双引号替换成中文左/右单角双引号。
    返回 (修复后文本, 替换次数)。
    """
    import re as _re
    # 只在"含中文 + 含 ASCII 双引号"的简单场景下尝试。把 `"<CJK>` 替换为 `『<CJK>`，
    # 把 `<CJK>"` 替换为 `<CJK>』`。保守做法避开 JSON 结构字符。
    fixed = raw
    # 把 "中文 改成 『中文（左角）
    fixed, n1 = _re.subn(r'"([一-鿿])', r'『\1', fixed)
    # 把 中文" 改成 中文』（右角）
    fixed, n2 = _re.subn(r'([一-鿿])"', r'\1』', fixed)
    return fixed, (n1 + n2)


def _load_choice_json_with_recovery(choice_path: Path) -> dict:
    """读 emergence.json / choice.json·首次失败时尝试 ASCII 引号修复后重试·仍失败抛原异常。"""
    raw = choice_path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        fixed, n = _try_fix_ascii_quote_pollution(raw)
        if n == 0:
            raise
        try:
            data = json.loads(fixed)
        except json.JSONDecodeError:
            raise e  # 修了仍坏 → 抛原始错给 main agent 看
        # 修复成功 → 把修后内容写回原文件 + 留 .raw 备份
        backup = choice_path.with_suffix(choice_path.suffix + ".raw")
        if not backup.exists():
            backup.write_text(raw, encoding="utf-8")
        choice_path.write_text(fixed, encoding="utf-8")
        sys.stderr.write(
            f"[cluster_choice_apply][AUTOFIX] {choice_path.name}: 修复 {n} 处 ASCII 引号污染"
            f"（备份 -> {backup.name}）\n")
        sys.stderr.flush()
        return data


def _compute_cluster_start_ch(project_root: Path, cid: str, clusters: list) -> int:
    """🔴 2026-06-27 P2-10: 计算本 cluster 在全局章号空间内的 start_ch。
    优先取前一 cluster 已回填 chapter_range 末 + 1；都没有则起始 1。供 _normalize_storyboard_ch 用。
    """
    start = 1
    for c in clusters:
        if not isinstance(c, dict) or c.get("cluster_id") == cid:
            continue
        cr = c.get("chapter_range") or []
        if isinstance(cr, list) and len(cr) == 2:
            try:
                start = max(start, int(cr[1]) + 1)
            except (TypeError, ValueError):
                continue
    return start


def _normalize_storyboard_ch(brief: dict, start: int) -> dict:
    """🔴 2026-06-27 P2-10: brief.scene_storyboard 字段规约归一化（agent prompt 反 ch 误用 sediment）。

    outline-planner agent prompt 此前允许 storyboard scene 写 `ch` 字段，agent 实际把它当 scene index
    用（0/1/2...），不是全局章号。下游 build_manifest.current_scene 按全局章号(start+i)反查 → cluster_002+
    永远找不到 → writer 丢 cluster context（cluster_003 写作翻车 sediment）。

    规则：若 storyboard scene 有 `ch` 且无 `scene_idx` → 把原 ch 落 `scene_idx`，强制覆盖 `ch` 为 start+i。
    保持 brief 其他字段不变·返回新 brief（不在原对象上原地改）。
    """
    if not isinstance(brief, dict):
        return brief
    sb = brief.get("scene_storyboard")
    if not isinstance(sb, list):
        return brief
    new_sb = []
    for i, s in enumerate(sb):
        if not isinstance(s, dict):
            new_sb.append(s)
            continue
        sc = dict(s)
        # 原 ch 误用 → 落 scene_idx
        if "ch" in sc and "scene_idx" not in sc:
            sc["scene_idx"] = sc["ch"]
        # 全局章号覆写（即便 agent 写对了 scene_idx，也保 ch 是全局章号）
        sc["ch"] = start + i
        new_sb.append(sc)
    out = dict(brief)
    out["scene_storyboard"] = new_sb
    return out


def apply_choice(project_root: Path, next_key: str, choice_path: Path) -> dict:
    """读 answer_artifact（{"answer": <chosen brief dict>}）→ upsert 事件簇.json。"""
    try:
        import cluster_lookup as cl
        cid = cl.normalize_cluster_id(next_key) or f"cluster_{next_key}"
    except ImportError:
        cid = next_key if str(next_key).startswith("cluster_") else f"cluster_{next_key}"

    if not choice_path.exists():
        raise FileNotFoundError(f"用户选择文件不存在: {choice_path}")
    payload = _load_choice_json_with_recovery(choice_path)
    # 兼容三种来源（真 outline e2e 抓出·brief 嵌套位置不同）：
    #  ① pause answer_artifact：{"step":n, "answer": <brief>}（cluster-save-state 走向卡）
    #  ② outline-planner judge：{mode, cluster_id, ..., cluster_brief: <brief>, free_notes}
    #     （emergence.json·真 brief 在 cluster_brief 键下·顶层是 judge 元数据）
    #  ③ brief dict 本身（有 scope_summary/scene_storyboard）
    # 🔴 2026-07-08 修（验证书 e2e 抓出）：①×② 组合态——outline plan step6.5 把 judge 整体
    # 包进 answer（{"answer": <emergence.json>}），原逻辑取 answer 后不再解 cluster_brief →
    # judge 元数据被当 brief 落库（真 brief 困在嵌套键·storyboard 丢失·blueprint 只写 1 占位
    # scene）。answer 解包后若仍是 judge 包装（含 cluster_brief dict），继续下钻取真 brief。
    brief = None
    if isinstance(payload, dict):
        if isinstance(payload.get("answer"), dict):
            brief = payload["answer"]
            if isinstance(brief.get("cluster_brief"), dict):
                brief = brief["cluster_brief"]
        elif isinstance(payload.get("cluster_brief"), dict):
            brief = payload["cluster_brief"]
        elif any(k in payload for k in ("scope_summary", "scene_storyboard")):
            brief = payload
    if not isinstance(brief, dict):
        raise ValueError(f"choice 文件非 brief（无 answer/cluster_brief 包裹 + 非 brief）: "
                         f"{choice_path}")

    brief = dict(brief)
    # 🔴 强制规范 id（轮次7 观察项）：涌现候选带 _candidate_N 后缀（cluster_002_candidate_1），
    # 保留原 id 会让 GUI 草稿路径(state.py 按 id 拼)与写作侧(规范 id)命名错位。next_key 即权威。
    brief["cluster_id"] = cid
    # build_manifest 白名单成员（"active" 不在白名单·实证坑）
    brief["status"] = "in_progress"
    brief.setdefault("narrative_mode", "linear")
    # v27 fluid：估章数/章区间由 splitter 切完回填，禁止预锁
    brief.pop("estimated_chapters", None)
    brief.pop("chapter_range", None)

    db = project_root / "_数据库"
    sj_path = db / "事件簇.json"
    data = {"clusters": []}
    if sj_path.exists():
        try:
            data = json.loads(sj_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError(f"事件簇.json 损坏，拒绝覆盖: {sj_path}")
    clusters = data.setdefault("clusters", [])
    if not isinstance(clusters, list):
        raise ValueError("事件簇.json.clusters 不是数组")

    # 🔴 2026-06-27 P2-10: 写 事件簇.json 前先归一化 brief.scene_storyboard 字段（ch 当 scene_idx 误用 sediment）。
    # 让 事件簇.json 主表的 storyboard.ch 也变全局章号、原值落 scene_idx，与 cluster_blueprint 形态一致。
    _start = _compute_cluster_start_ch(project_root, brief["cluster_id"], clusters)
    brief = _normalize_storyboard_ch(brief, _start)

    replaced = False
    for i, c in enumerate(clusters):
        if isinstance(c, dict) and c.get("cluster_id") == brief["cluster_id"]:
            merged = dict(c)
            merged.update(brief)
            clusters[i] = merged
            replaced = True
            break
    if not replaced:
        clusters.append(brief)

    try:
        from atomic_json import atomic_write_json
        atomic_write_json(sj_path, data)
    except ImportError:
        tmp = sj_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(sj_path)
    _write_blueprint(project_root, brief, clusters)
    # beat_map 是 cluster brief 的确定性派生产物。正式链路不能在这里静默丢掉结构
    # producer；旧书无 storyboard 时由 beat_map_update 返回 skipped，导入/写盘异常则暴露。
    import beat_map_update
    bm_res = beat_map_update.update(project_root, brief["cluster_id"])
    if bm_res.get("beats"):
        print(f"[cluster_choice_apply] beat_map.cluster_beats[{brief['cluster_id']}] "
              f"派生 {bm_res['beats']} beat（起承转合）", file=sys.stderr)
    elif brief.get("scene_storyboard"):
        raise RuntimeError(
            f"beat_map 派生失败：{brief['cluster_id']} 有 scene_storyboard 但未产 beats "
            f"({bm_res.get('skipped', 'unknown')})"
        )
    return {"cluster_id": brief["cluster_id"], "replaced": replaced,
            "status": brief["status"]}


def _write_blueprint(project_root: Path, brief: dict, clusters: list):
    """🔴 同步写 进度.json.cluster_blueprint（GUI 全旅程模拟点击抓出的 blocker）。

    build_manifest.current_scene 只认两条路：① blueprint scene.ch==本章 ② 事件簇
    chapter_range 覆盖本章。v27 fluid 明确 chapter_range 由 splitter 切完回填——②对新
    cluster 永不可达 → 程序驱动建书的产物 cluster-write step1 preflight 必 fatal。修：
    应用 brief 时把 scene_storyboard 转写进 blueprint·每 scene 给 ch 占位（start+i·
    对齐 Claude /outline 既有契约「cluster_001 填 ch1-N 占位」·真切章仍由 splitter 定）。"""
    cid = brief["cluster_id"]
    # start_ch：前一 cluster 已回填的 chapter_range 末+1（cluster_001 → 1）
    # 🔴 2026-06-27 P2-10: 委托 _compute_cluster_start_ch（与 apply_choice 同语义·避免漂移）。
    start = _compute_cluster_start_ch(project_root, cid, clusters)
    storyboard = []
    # 🔴 2026-06-27 P2-10: brief 已由 apply_choice 经 _normalize_storyboard_ch 归一化，
    # 这里只补 title fallback·绝不再做 ch↔scene_idx 双重归一（避免把 normalize 后的 ch 当 idx 二次 sediment）。
    for i, s in enumerate(brief.get("scene_storyboard") or []):
        sc = dict(s) if isinstance(s, dict) else {"summary": str(s)}
        # 兜底：apply_choice 已设 ch=start+i；此处仅守 ch 缺失场景（不经 apply_choice 直调本函数的旧调用方）
        if not isinstance(sc.get("ch"), int):
            sc["ch"] = start + i
        sc.setdefault("title", str(sc.get("summary") or sc.get("key_beats")
                                   or f"场景{i + 1}")[:30])
        storyboard.append(sc)
    if not storyboard:           # brief 无 storyboard 也要保 preflight 可过
        storyboard = [{"ch": start, "title": str(brief.get("scope_summary") or cid)[:30]}]
    prog_path = project_root / "_数据库" / "进度.json"
    prog = {}
    if prog_path.exists():
        try:
            prog = json.loads(prog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prog = {}
    bp = prog.setdefault("cluster_blueprint", {})
    if not isinstance(bp, dict):
        bp = {}
        prog["cluster_blueprint"] = bp
    bp[cid] = {
        "vol": brief.get("vol", 1),
        "narrative_mode": brief.get("narrative_mode", "linear"),
        "scope_summary": brief.get("scope_summary", ""),
        "foreshadowing_to_plant": brief.get("foreshadowing_to_plant", []),
        "scene_storyboard": storyboard,
        "_source": "cluster_choice_apply（程序驱动·ch 为占位·真切章由 splitter 定）",
    }
    try:
        from atomic_json import atomic_write_json
        atomic_write_json(prog_path, prog)
    except ImportError:
        tmp = prog_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(prog, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(prog_path)
    print(f"[cluster_choice_apply] blueprint 写入 {cid}（{len(storyboard)} scene·"
          f"ch 占位 {start}~{start + len(storyboard) - 1}）", file=sys.stderr)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="用户走向选择写回 事件簇.json")
    ap.add_argument("project", help="项目根路径")
    ap.add_argument("--next-key", required=True, help="下一 cluster key（如 002）")
    ap.add_argument("--choice", required=True, help="answer_artifact JSON 路径")
    args = ap.parse_args()

    root = Path(args.project)
    choice = Path(args.choice)
    if not choice.is_absolute():
        choice = root / choice
    try:
        result = apply_choice(root, args.next_key, choice)
    except (FileNotFoundError, ValueError) as e:
        print(f"[cluster_choice_apply] {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
