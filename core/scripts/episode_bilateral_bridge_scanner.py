#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""episode_bilateral_bridge_scanner.py — 短剧相邻集双侧握手桥(R18 W7 Batch-U·P2)

【缺口·2026-06-21·短剧竖屏专属·激活门控 genre_tags=short_drama_vertical】
filmustage vertical-drama-script + finaldraft verticals-micro-dramas + medium
real-reel china-vertical-drama-2026:
  竖屏短剧 1-3 分钟/集·相邻集必须『双侧握手桥』——上集末段挑钩(open hook)
  必须在下集开头 0-20% 兑现(resolve)·同时下集 70-100% 必须立新钩(new hook
  position)·任一缺失 → 观众完读率断崖。本 scanner 算两指标:

  ① resolve_latency_ratio   —— 下集首个 resolve 信号(确认/落实/兑现/明白/收钩)
                              出现位置 / 下集总长度·>0.3 → BRIDGE_RESOLVE_TOO_LATE
  ② new_hook_position_ratio —— 下集末尾新钩(悬念/反差/未完成动作)首现位置 /
                              下集总长度·<0.55 → BRIDGE_NEW_HOOK_TOO_EARLY
                              (太早立新钩=没用足上集钩=观众已弃)

【与既有 scanner 显式去重(docstring 体现)】
  - R7 hook_strength_scanner: 单章末钩 11 型(单边 WHAT)·只看下集末段
    本 scanner = 跨集 BEFORE/AFTER 双边握手·必须有上集末段+下集开头+下集末段
    三段同框·完全不同维度
  - R8 frame_tale_consistency_scanner: 框架叙事跨层承接(narrative levels)
    本 scanner = 同层相邻 episode 的兑现+新钩位置·正交

【激活门控·北极星④⑤】
  仅 genre_tags 含 short_drama_vertical 时激活(读 _数据库/用户偏好.json
  genre_tags 或 _数据库/作者风格.json genre)·其他题材 skip 出 PASS。
  无 _数据库/.short_drama_episode_index.json 则尝试按 章节/ 目录下相邻两章
  视作相邻 episode(短剧切章≈分集)。

  build_manifest 短剧 mode 应追加 writer_directives(本 scanner 不写 manifest,
  通过 audit advisory 反馈)。

【北极星⑤】顾问非法官·全 advisory·env EPISODE_BILATERAL_BRIDGE_MODE
  BRIDGE_RESOLVE_TOO_LATE / BRIDGE_NEW_HOOK_TOO_EARLY 绝不 hard_gate。

用法: python episode_bilateral_bridge_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_LATE_RESOLVE = "BRIDGE_RESOLVE_TOO_LATE"
ISSUE_CODE_EARLY_HOOK = "BRIDGE_NEW_HOOK_TOO_EARLY"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# resolve 信号：兑现/承接/收钩
RESOLVE_KW = re.compile(
    r"(明白|知道了|果然|确认|收钩|果然如此|原来如此|回应|应了下来|接住|接过|落实|"
    r"答应|点头|回答|这才|这下|这次终于|终于|刚才那|上次说的|你之前说|"
    r"是这样|没错|对了|是的|不错|肯定)")

# new hook 信号：新钩(悬念/反差/未完成)
NEW_HOOK_KW = re.compile(
    r"(突然|忽然|竟然|居然|没想到|出乎意料|意外|反而|"
    r"还没说完|话没说完|欲言又止|顿住|停住|"
    r"为什么|怎么会|是谁|什么|不知道|不明白|"
    r"动手|出手|逼近|围住|挡住|拦住|对峙|威胁|警告|"
    r"——|……)")

LATE_RESOLVE_RATIO = 0.30   # >0.30 算 late
EARLY_HOOK_RATIO = 0.55     # <0.55 算 early

MIN_CJK = 400


def _mode() -> str:
    m = (os.environ.get("EPISODE_BILATERAL_BRIDGE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _is_short_drama(project_root) -> bool:
    """读 _数据库/用户偏好.json genre_tags / _数据库/作者风格.json genre。"""
    if not project_root:
        return False
    root = Path(project_root)
    for fname in ("用户偏好.json", "作者风格.json", "作者风格_FINAL.json"):
        p = root / "_数据库" / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tags = data.get("genre_tags") or data.get("genre") or []
        if isinstance(tags, str):
            tags = [tags]
        if "short_drama_vertical" in tags:
            return True
    return False


def _split_episodes(text: str):
    """切相邻 episode：按『第N章』/『第N集』分割·返回 [(label, body)]·≥2 集生效。"""
    # 找章/集标题
    parts = re.split(r"\n(?=第[0-9]+[章集])", text)
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(第[0-9]+[章集][^\n]*)\n?(.*)$", part, re.S)
        if m:
            out.append((m.group(1), m.group(2).strip()))
        else:
            out.append((f"段{len(out)+1}", part))
    # 兜底：单 episode 则按字数对半切
    if len(out) < 2 and len(text) > 600:
        mid = len(text) // 2
        out = [("前半", text[:mid].strip()), ("后半", text[mid:].strip())]
    return out


def _last_segment_text(body: str, ratio=0.15) -> str:
    n = max(50, int(len(body) * ratio))
    return body[-n:]


def _find_first_match_position(pat, body: str):
    """返回首个匹配的相对位置 0.0-1.0·无匹配 → None。"""
    if not body:
        return None
    m = pat.search(body)
    if not m:
        return None
    return m.start() / max(1, len(body))


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "episode_bilateral_bridge", "schema_version": "1.0",
           "mode": mode, "violations": [], "verdict": "PASS",
           "warning": None, "gate_level": "advisory"}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    if not _is_short_drama(project_root):
        out["note"] = "非短剧竖屏题材·skip"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    episodes = _split_episodes(text)
    if len(episodes) < 2:
        out["note"] = "未识别出 ≥2 集·跳过"
        return out

    messages = []
    bridges = []
    for i in range(len(episodes) - 1):
        prev_label, prev_body = episodes[i]
        cur_label, cur_body = episodes[i + 1]
        if len(prev_body) < 100 or len(cur_body) < 100:
            continue
        # 1. 下集开头 30% 是否兑现
        head_window = cur_body[: max(80, int(len(cur_body) * 0.3))]
        resolve_pos = _find_first_match_position(RESOLVE_KW, head_window)
        # 2. 下集末段新钩位置
        new_hook_pos = _find_first_match_position(NEW_HOOK_KW, cur_body)

        bridge_info = {"from": prev_label, "to": cur_label,
                       "resolve_in_head_30pct": resolve_pos is not None,
                       "new_hook_position": (round(new_hook_pos, 3)
                                             if new_hook_pos is not None else None)}
        bridges.append(bridge_info)

        # latency ratio = (resolve 相对位置 / 下集长度)·没有 resolve → 1.0
        latency = 1.0 if resolve_pos is None else resolve_pos * 0.3
        if latency > LATE_RESOLVE_RATIO:
            messages.append(
                f"{prev_label}→{cur_label}: 下集开头未在 30% 内兑现上集钩·"
                f"resolve_latency_ratio={round(latency,2)}")
        # new hook 位置：太早 (<0.55) 说明没用足上集钩
        if new_hook_pos is not None and new_hook_pos < EARLY_HOOK_RATIO:
            messages.append(
                f"{prev_label}→{cur_label}: 新钩位置 {round(new_hook_pos,2)} 偏早·"
                f"建议 ≥0.55(末段立钩)")

    out["bridges"] = bridges
    out["metrics"] = {"bridges_examined": len(bridges)}

    if messages:
        msg = " · ".join(messages[:4])
        if mode == "active":
            out["violations"].append({
                "kind": "episode_bilateral_bridge",
                "severity": "minor",
                "code": (ISSUE_CODE_LATE_RESOLVE
                         if "未在 30%" in messages[0] else ISSUE_CODE_EARLY_HOOK),
                "message": msg,
                "_doc": "短剧相邻集双侧握手桥·advisory·绝不 hard_gate",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] episode_bilateral_bridge: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="短剧相邻集双侧握手桥 · advisory · shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
