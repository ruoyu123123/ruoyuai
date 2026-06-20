#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opening_window_milestone_scanner.py — 番茄 3k/10k 开篇悬念-核心 stake 放置闸
(advisory · cluster_001 专用 · 2026-06-20 · R8 W4 Batch-J · L35)

【缺口】R8 W4 联网调研(知乎拆 30+本爆款 2025 番茄 + CSDN 番茄爽文方法论 +
Royal Road Wattpad 50% drop + WebNovelBench arXiv:2505.14818): 番茄/起点开篇 50%
读者在前 3000 CJK 流失·核心 stake 必须前 10000 CJK 落定。LLM 默认走"逐渐铺垫"
风格 → cluster_001 前 3000 CJK 没钩子、前 10000 CJK 没核心 stake。

【做法 · 确定性纯规则】:
  - M1 = 前 3000 CJK ambiguity_hook: suspense_focus 首次 surface 或模糊 2 解+ 标志
  - M2 = 前 10000 CJK core_stake: 主角 goal/失去成本/威胁标志

【与 R1 golden_three + R6 in_medias_res 协同】: golden_three 查"开场强度";
in_medias_res 查"反时序顺序"; 本 scanner 查"两个里程碑放置".

【北极星② / ⑤ 顾问非法官】严肃文学/IP 改编 override 通过 author_profile 跳过 ·
code OPENING_WINDOW_MILESTONE_THIN 绝不进 hard_gate 。仅 cluster_001 激活。
env OPENING_MILESTONE_MODE: off / shadow(默认) / active。

用法: python opening_window_milestone_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "OPENING_WINDOW_MILESTONE_THIN"

# M1 ambiguity_hook 标志: 悬念/伏笔/未解 + 模糊 2 解+
AMBIGUITY_MARKERS = re.compile(
    r"(究竟|到底|为什么|怎么会|怎么可能|难道|莫非|不知道|不明白|说不清|"
    r"看不清|看不出|猜不透|猜不出|搞不懂|搞不清|没有头绪|未解之谜|"
    r"奇怪的是|诡异|蹊跷|不对劲|不寻常|反常|异样|失踪|消失|不见了)")
SUSPENSE_FOCUS = re.compile(
    r"(谜|秘密|真相|线索|疑点|疑团|谜团|悬案|失踪|死因|凶手|真凶|内幕|"
    r"暗号|信物|遗物|密令|阴谋)")
# M2 core_stake 标志: 主角 goal + 失去成本 + 威胁
GOAL_MARKERS = re.compile(
    r"(必须|一定要|不得不|只能|唯有|不能|不可以|必死|要命|生死|存亡|"
    r"复仇|报仇|找回|救出|阻止|阻挡|赢得|夺回|找到|查清)")
LOSS_MARKERS = re.compile(
    r"(失去|消失|被夺走|没了|死了|死亡|被杀|遇害|遭遇|危险|绝境|"
    r"末日|毁灭|绝望|崩溃|破碎|灭族|灭门|家破人亡)")
THREAT_MARKERS = re.compile(
    r"(威胁|敌人|杀手|追杀|追捕|围剿|猎杀|围攻|针对|盯上|盯梢|算计|"
    r"陷害|布局|杀机|怨灵|恶鬼|凶险|来历不明)")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("OPENING_MILESTONE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_prefix(text: str, n: int) -> str:
    """返回 text 前 n CJK 字符所对应的子串(含中间标点/空格保留)。"""
    out = []
    cnt = 0
    for i, ch in enumerate(text):
        out.append(ch)
        if "一" <= ch <= "鿿":
            cnt += 1
            if cnt >= n:
                return "".join(out)
    return text


def _is_cluster_001(project_root, draft_path) -> bool:
    """检查当前是否处理 cluster_001 草稿。"""
    name = Path(draft_path).name
    if "cluster_001" in name or "cluster_1" in name.replace("cluster_01", "cluster_1"):
        return True
    # 退到 active cluster 读
    if project_root:
        ec = Path(project_root) / "_数据库" / "事件簇.json"
        if ec.exists():
            try:
                data = json.loads(ec.read_text(encoding="utf-8"))
                for c in data.get("clusters") or []:
                    if not isinstance(c, dict):
                        continue
                    if (c.get("status") or "").strip().lower() in {
                            "in_progress", "active"}:
                        cid = (c.get("cluster_id") or "").strip().lower()
                        return cid in {"cluster_001", "cluster_1", "cluster_01"}
            except (json.JSONDecodeError, OSError):
                pass
    return False


def _override_skip(project_root) -> bool:
    """严肃文学 / IP 改编 override → skip。"""
    if not project_root:
        return False
    ap = Path(project_root) / "_数据库" / "作者风格.json"
    if not ap.exists():
        return False
    try:
        obj = json.loads(ap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(obj, dict):
        return False
    prof = obj.get("opening_milestone_profile") or {}
    if prof.get("skip") is True:
        return True
    genre = (obj.get("genre") or "").strip().lower()
    return genre in {"literary", "serious_literature", "ip_adaptation"}


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "opening_window_milestone", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "warning": None, "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)

    if not _is_cluster_001(project_root, draft_path):
        out["note"] = "非 cluster_001·跳过(仅首块激活)"
        return out
    if _override_skip(project_root):
        out["note"] = "作者档 override(严肃文学/IP 改编)·跳过"
        return out

    win3k = _cjk_prefix(draft, 3000)
    win10k = _cjk_prefix(draft, 10000)

    m1_amb = len(AMBIGUITY_MARKERS.findall(win3k))
    m1_focus = len(SUSPENSE_FOCUS.findall(win3k))
    m1_hit = (m1_amb >= 2) or (m1_focus >= 1 and m1_amb >= 1)

    m2_goal = len(GOAL_MARKERS.findall(win10k))
    m2_loss = len(LOSS_MARKERS.findall(win10k))
    m2_threat = len(THREAT_MARKERS.findall(win10k))
    m2_hit = (m2_goal >= 2) or (m2_loss + m2_threat >= 2)

    out.update({
        "m1_window_cjk": 3000,
        "m1_ambiguity_count": m1_amb,
        "m1_focus_count": m1_focus,
        "m1_hit": m1_hit,
        "m2_window_cjk": 10000,
        "m2_goal_count": m2_goal,
        "m2_loss_count": m2_loss,
        "m2_threat_count": m2_threat,
        "m2_hit": m2_hit,
    })

    msgs = []
    if not m1_hit:
        msgs.append(f"M1 前 3000 CJK ambiguity_hook 缺位 (amb={m1_amb}/focus={m1_focus})·"
                    f"番茄/起点开篇 50% 流失阈前需明显悬念钩子")
    if not m2_hit:
        msgs.append(f"M2 前 10000 CJK core_stake 缺位 (goal={m2_goal}/loss={m2_loss}/"
                    f"threat={m2_threat})·主角 goal+ 失去成本/威胁应在 10k 内落地")
    msg = "·".join(msgs) if msgs else None
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "opening_window_milestone", "severity": "minor",
                "message": msg, "m1_hit": m1_hit, "m2_hit": m2_hit,
                "_doc": ("开篇里程碑是工艺 advisory · 严肃文学/IP 改编可 override · "
                         "与 R1 golden_three 协同 · 绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] opening_window_milestone: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="番茄 3k/10k 开篇里程碑闸(advisory · cluster_001)")
    ap.add_argument("draft_path", help="cluster_001 草稿路径")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
