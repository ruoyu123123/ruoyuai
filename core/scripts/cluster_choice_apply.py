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


def apply_choice(project_root: Path, next_key: str, choice_path: Path) -> dict:
    """读 answer_artifact（{"answer": <chosen brief dict>}）→ upsert 事件簇.json。"""
    try:
        import cluster_lookup as cl
        cid = cl.normalize_cluster_id(next_key) or f"cluster_{next_key}"
    except ImportError:
        cid = next_key if str(next_key).startswith("cluster_") else f"cluster_{next_key}"

    if not choice_path.exists():
        raise FileNotFoundError(f"用户选择文件不存在: {choice_path}")
    payload = json.loads(choice_path.read_text(encoding="utf-8"))
    # 兼容三种来源（真 outline e2e 抓出·brief 嵌套位置不同）：
    #  ① pause answer_artifact：{"step":n, "answer": <brief>}（cluster-save-state 走向卡）
    #  ② outline-planner judge：{mode, cluster_id, ..., cluster_brief: <brief>, free_notes}
    #     （emergence.json·真 brief 在 cluster_brief 键下·顶层是 judge 元数据）
    #  ③ brief dict 本身（有 scope_summary/scene_storyboard）
    brief = None
    if isinstance(payload, dict):
        if isinstance(payload.get("answer"), dict):
            brief = payload["answer"]
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
    start = 1
    for c in clusters:
        if not isinstance(c, dict) or c.get("cluster_id") == cid:
            continue
        cr = c.get("chapter_range") or []
        if isinstance(cr, list) and len(cr) == 2:
            start = max(start, int(cr[1]) + 1)
    storyboard = []
    for i, s in enumerate(brief.get("scene_storyboard") or []):
        sc = dict(s) if isinstance(s, dict) else {"summary": str(s)}
        sc.setdefault("ch", start + i)
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
