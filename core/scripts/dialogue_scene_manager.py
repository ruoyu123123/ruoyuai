#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue_scene_manager.py — AdaMARP 多人对话编排(R19 W8 Batch-X·P2)

【缺口·2026-06-21·ACL 2026 AdaMARP】
LLM 写多人场景(K≥3 active char)时常退化为「轮询发言+情景空洞」。AdaMARP
的解决方案是把 [Thought](Action)<<Environment>>Speech 四标签注入 turn-level
prompt(每 turn 80-300 字)·让 writer 显式分离思考/动作/环境/对话。

【激活条件】
  scene_type ∈ {confrontation, negotiation, group_debate, court_session,
                family_clash}
  且 active_chars ≥ 3
  且 gen_writer --dialogue-orchestrator-mode == "on"(默认 off)

【数据契约】
  _数据库/dialogue_turn_log.json
    {
      "schema_version": 1,
      "_placeholder": false,
      "scenes": [
        {
          "cluster_id": "cluster_003",
          "scene_idx": 2,
          "scene_type": "confrontation",
          "active_chars": ["小王", "李雷", "韩梅梅"],
          "turns": [
            {"turn_id": 1, "speaker": "小王",
             "thought": "...", "action": "...",
             "environment": "...", "speech": "...",
             "word_count": 162}
          ]
        }
      ]
    }

【writer 注入(gen_writer.py 调度)】
  - flag --dialogue-orchestrator-mode 默认 off
  - on 时 build_manifest 调 inject_orchestrator_prompt() 加四标签 prompt 段
  - active_chars 从角色池.json + cluster brief 推导
  - 编排日志 append-only 写 dialogue_turn_log.json

【与既有 scanner 严格正交】
  - hierarchical_planner   : 章节大计划·正交(本=turn 级)
  - antagonist_fidelity    : 反派一致性·正交
  - character_distinctiveness : 个体特征·正交
  - quotative              : 引语 marker·正交

【北极星⑤】顾问非法官·shadow code DIALOGUE_ORCHESTRATOR_DEGRADED·绝不 hard_gate。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ISSUE_CODE = "DIALOGUE_ORCHESTRATOR_DEGRADED"

ACTIVE_SCENE_TYPES = (
    "confrontation", "negotiation", "group_debate",
    "court_session", "family_clash",
)

MIN_ACTIVE_CHARS = 3
TURN_WORD_LO = 80
TURN_WORD_HI = 300

# 四标签 prompt 模板(写给 LLM 的格式说明)
ORCHESTRATOR_PROMPT_TEMPLATE = """[多人对话编排 · AdaMARP]
本场景为 {scene_type} · 活跃角色 {n_chars} 人：{chars_str}
请按四标签 turn 写法生成对话段：
  [Thought] 该角色当下内心想法（不出声）
  (Action) 该角色当下身体动作
  <<Environment>> 周围环境状态变化（光线/声音/气味/他人）
  Speech: 该角色实际说的话（"..."）

每个 turn 80-300 字。Speaker 轮换有节奏感（不要严格轮询）。
"""


def _mode() -> str:
    m = (os.environ.get("DIALOGUE_ORCHESTRATOR_MODE") or "off").strip().lower()
    return m if m in ("off", "shadow", "active") else "off"


def is_active(scene_type, active_chars) -> bool:
    """判断本场景是否激活四标签编排。"""
    if not scene_type or scene_type not in ACTIVE_SCENE_TYPES:
        return False
    if not active_chars or len(active_chars) < MIN_ACTIVE_CHARS:
        return False
    return True


def build_orchestrator_prompt(scene_type, active_chars) -> str:
    """返回注入 writer 的四标签 prompt 段。激活才返回非空。"""
    if not is_active(scene_type, active_chars):
        return ""
    chars_str = "/".join(active_chars)
    return ORCHESTRATOR_PROMPT_TEMPLATE.format(
        scene_type=scene_type,
        n_chars=len(active_chars),
        chars_str=chars_str,
    )


def inject_orchestrator_prompt(manifest, cluster_brief, characters):
    """gen_writer 调用点·把四标签 prompt 段注入 manifest['dialogue_orchestrator']。

    manifest: dict (build_manifest 输出)
    cluster_brief: dict (含 scene_storyboard[{scene_type, characters}, ...])
    characters: set (角色池.json core+emerged 角色名 · 🔴 2026-06-28 canonical)
    返回修改后的 manifest(原地 + 返回)。
    """
    if _mode() == "off":
        return manifest
    if not isinstance(manifest, dict):
        return manifest
    if not isinstance(cluster_brief, dict):
        return manifest
    storyboard = cluster_brief.get("scene_storyboard") or []
    if not isinstance(storyboard, list):
        return manifest
    activated = []
    for idx, sc in enumerate(storyboard):
        if not isinstance(sc, dict):
            continue
        st = sc.get("scene_type")
        chs = sc.get("characters") or sc.get("active_chars") or []
        if isinstance(chs, str):
            chs = [chs]
        # 与角色池.json 交集
        if characters:
            chs = [c for c in chs if c in characters]
        if is_active(st, chs):
            activated.append({
                "scene_idx": idx,
                "scene_type": st,
                "active_chars": list(chs)[:6],
                "prompt": build_orchestrator_prompt(st, chs),
            })
    if activated:
        manifest["dialogue_orchestrator"] = {
            "_doc": "R19 W8 Batch-X·四标签编排·AdaMARP",
            "mode": _mode(),
            "activated_scenes": activated,
            "turn_word_range": [TURN_WORD_LO, TURN_WORD_HI],
        }
    return manifest


def append_turn_log(project_root, cluster_id, scene_idx, scene_type,
                    active_chars, turns):
    """写 _数据库/dialogue_turn_log.json·append-only。

    turns: [{turn_id, speaker, thought, action, environment, speech, word_count}, ...]
    """
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    log_path = db / "dialogue_turn_log.json"
    if log_path.exists():
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    else:
        data = None
    if not isinstance(data, dict):
        data = {"schema_version": 1, "_placeholder": False, "scenes": []}
    data.setdefault("scenes", []).append({
        "cluster_id": cluster_id,
        "scene_idx": scene_idx,
        "scene_type": scene_type,
        "active_chars": list(active_chars or []),
        "turns": list(turns or []),
    })
    log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return log_path


def validate_turn(turn: dict) -> dict:
    """校验单 turn 四标签字段齐全 + word_count 在 [80, 300]·返回 issues。"""
    issues = []
    for key in ("speaker", "thought", "action", "environment", "speech"):
        if not turn.get(key):
            issues.append(f"missing_{key}")
    wc = turn.get("word_count")
    if isinstance(wc, int):
        if wc < TURN_WORD_LO:
            issues.append(f"turn_too_short({wc}<{TURN_WORD_LO})")
        elif wc > TURN_WORD_HI:
            issues.append(f"turn_too_long({wc}>{TURN_WORD_HI})")
    return {"turn_id": turn.get("turn_id"), "issues": issues}


def scan_turn_log(project_root) -> dict:
    """扫 dialogue_turn_log.json·汇总 turn 合规率·shadow 不上报。"""
    out = {"scanner": "dialogue_scene_manager", "schema_version": "1.0",
           "mode": _mode(), "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if _mode() == "off":
        out["note"] = "off"
        return out
    if not project_root:
        out["note"] = "no project"
        return out
    log_path = Path(project_root) / "_数据库" / "dialogue_turn_log.json"
    if not log_path.exists():
        out["note"] = "log_missing"
        return out
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        out["note"] = "log_parse_error"
        return out
    scenes = data.get("scenes") or []
    total_turns = 0
    issue_turns = 0
    for sc in scenes:
        for tn in sc.get("turns") or []:
            total_turns += 1
            v = validate_turn(tn)
            if v["issues"]:
                issue_turns += 1
    out["metrics"] = {
        "total_scenes": len(scenes),
        "total_turns": total_turns,
        "issue_turns": issue_turns,
    }
    if total_turns > 0 and issue_turns / total_turns > 0.30:
        msg = (f"四标签 turn 异常率 {issue_turns}/{total_turns}·>30% 阈值")
        if _mode() == "active":
            out["violations"].append({
                "kind": "dialogue_orchestrator_degraded",
                "severity": "minor",
                "code": ISSUE_CODE,
                "message": msg,
                "metrics": out["metrics"],
                "_doc": "R19 W8 Batch-X·AdaMARP 四标签 turn·advisory",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] dialogue_scene_manager: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="R19 W8 Batch-X 多人对话编排·四标签·shadow")
    ap.add_argument("--project", required=False, default=None)
    ap.add_argument("--draft", required=False, default=None,
                    help="(占位)·正式 turn log 由 writer 写入")
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan_turn_log(args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
