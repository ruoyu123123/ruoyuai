#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""microdrama_intraep_beat_lattice.py — 单集内节拍栅格 · R24 W12 Batch-LL · P2

【缺口 · 微短剧 intra-episode beat lattice】
微短剧/短视频剧本节拍极致前置：0-3s 必须扔出动作动词+矛盾名词的钩点；
mid_15s 反转触发；tail_30s 钩子（next-episode 悬念）。
小说/长篇 freestyle 写法的「慢热铺垫」直接套用到短剧体 = 留人率塌方。
本 scanner 把 cluster 草稿按 6-9 char/sec 投影到时间栅格 0-3-15-30-60-90s，
检三档锚点是否到位。

【做法 · 确定性 · 零 LLM/零联网（占位 lexicon · _placeholder=true）】
  · 投影率 7 CJK/sec（业内估算·短剧体读速 6-9·取中位）
  · 时间桶 0-3s / 3-15s / 15-30s / 30-60s / 60-90s
  · head_3s 锚点 = 动作动词词典 + 矛盾名词词典各 ≥1 命中
  · mid_15s 锚点 = 反转触发词词典 ≥1 命中
  · tail_30s 锚点 = 钩子词典 ≥1 命中
  · 短剧体激活门控（manifest.format∈{shortdrama,microdrama,script} 或 env=active）
  · 长篇/小说默认 skip（不强行套）

【四 advisory · 全 advisory shadow】
  · MICRODRAMA_HEAD_3S_NO_ACTION       — 0-3s 动作动词/矛盾名词缺位
  · MICRODRAMA_MID_15S_NO_TWIST        — 3-15s 反转触发缺位
  · MICRODRAMA_TAIL_30S_NO_HOOK        — 60-90s 钩子缺位
  · MICRODRAMA_NOT_SHORTDRAMA_SKIP     — 非短剧体（info·门控未通过）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  MICRODRAMA_* 绝不进 audit_hub.HARD_GATE_CODES。

env MICRODRAMA_BEAT_LATTICE_MODE: off / shadow（默认） / active
用法: python microdrama_intraep_beat_lattice.py <draft> [--project <root>] [--format shortdrama]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE_HEAD_NO_ACTION = "MICRODRAMA_HEAD_3S_NO_ACTION"
ISSUE_CODE_MID_NO_TWIST = "MICRODRAMA_MID_15S_NO_TWIST"
ISSUE_CODE_TAIL_NO_HOOK = "MICRODRAMA_TAIL_30S_NO_HOOK"
ISSUE_CODE_NOT_SHORTDRAMA = "MICRODRAMA_NOT_SHORTDRAMA_SKIP"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_SHORTDRAMA_FORMATS = {"shortdrama", "microdrama", "script", "短剧", "微短剧"}

# 业内估算：短剧读速 6-9 CJK/sec·取中位 7
DEFAULT_CHAR_PER_SEC = 7.0
# 时间桶（秒）·栅格 0-3-15-30-60-90
TIME_GRID_SEC = [0, 3, 15, 30, 60, 90]

# 占位词典 _placeholder=true·真版 = 短剧爆款剧本聚类
_ACTION_VERBS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·短剧 head_3s 动作动词·占位",
    "_words": [
        "扇", "砸", "踹", "甩", "撕", "扔", "推", "抓", "拉", "拽",
        "摔", "撞", "锁", "扑", "跪", "跳", "夺", "捧", "塞", "怼",
        "捅", "划", "斩", "举", "压",
    ],
}
_CONFLICT_NOUNS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·短剧 head_3s 矛盾名词·占位",
    "_words": [
        "巴掌", "耳光", "婚书", "结婚证", "离婚协议", "亲子鉴定", "遗书",
        "刀", "血", "尸体", "录音", "证据", "假货", "小三", "渣男",
        "绿茶", "白莲", "私生子", "破产通知", "解约书",
    ],
}
_TWIST_TRIGGERS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·短剧 mid_15s 反转触发·占位",
    "_words": [
        "其实", "事实是", "真相是", "竟然", "原来", "没想到", "万万没想到",
        "谁知", "却不知", "翻脸", "反转", "拆穿", "揭穿", "曝光", "突然",
        "刹那", "陡然", "戛然",
    ],
}
_HOOK_TRIGGERS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-LL·短剧 tail_30s 钩子·占位",
    "_words": [
        "下集", "下一集", "未完待续", "且看", "且听", "明日", "下次",
        "等着", "敬请期待", "且看下回", "悬念", "卖关子",
        "万万没想到", "结果", "结局", "竟是",
    ],
}


def _mode() -> str:
    m = (os.environ.get("MICRODRAMA_BEAT_LATTICE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _detect_format(project_root, cli_format: str | None) -> str:
    """格式识别（CLI > manifest.format > 默认 prose）"""
    if cli_format:
        return cli_format.strip().lower()
    if not project_root:
        return "prose"
    try:
        upref = Path(project_root) / "_数据库" / "用户偏好.json"
        if upref.exists():
            data = json.loads(upref.read_text(encoding="utf-8"))
            f = data.get("format") or data.get("writing_mode") or ""
            if isinstance(f, str) and f.strip():
                return f.strip().lower()
    except (OSError, json.JSONDecodeError):
        pass
    return "prose"


def _is_shortdrama(fmt: str) -> bool:
    return fmt in _SHORTDRAMA_FORMATS


def _slice_by_time(text: str, char_per_sec: float = DEFAULT_CHAR_PER_SEC) -> dict:
    """按 CJK 字符投影到时间栅格·返回 {bucket: text}"""
    cjk_chars = [ch for ch in text if "一" <= ch <= "鿿"]
    n = len(cjk_chars)
    if n == 0:
        return {}
    out = {}
    for i in range(len(TIME_GRID_SEC) - 1):
        t0, t1 = TIME_GRID_SEC[i], TIME_GRID_SEC[i + 1]
        c0 = int(t0 * char_per_sec)
        c1 = int(t1 * char_per_sec)
        c1 = min(c1, n)
        if c0 >= n:
            out[f"{t0}_{t1}s"] = ""
            continue
        out[f"{t0}_{t1}s"] = "".join(cjk_chars[c0:c1])
    # 尾段（>90s）保留供 hook 检测
    last_t = TIME_GRID_SEC[-1]
    last_c = int(last_t * char_per_sec)
    if last_c < n:
        out["tail_after"] = "".join(cjk_chars[last_c:])
    else:
        out["tail_after"] = ""
    return out


def _count_hits(text: str, words: list) -> int:
    return sum(text.count(w) for w in words)


def scan(draft_path, project_root=None, cli_format=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "microdrama_intraep_beat_lattice", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": True,
    }
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < 100:
        out["note"] = "草稿太短·跳过"
        return out

    fmt = _detect_format(project_root, cli_format)
    out["format"] = fmt
    if not _is_shortdrama(fmt):
        # 非短剧体默认 skip
        if mode == "active":
            out["violations"].append({
                "kind": "microdrama_intraep_beat_lattice",
                "severity": "info",
                "code": ISSUE_CODE_NOT_SHORTDRAMA,
                "message": (f"format={fmt} 非短剧体·跳过节拍栅格"
                            "·shortdrama/microdrama/script 才激活"),
                "_doc": "R24 W12 Batch-LL·门控·advisory·绝不 hard_gate",
            })
        return out

    buckets = _slice_by_time(text)
    head_3s = buckets.get("0_3s", "")
    mid_15s = buckets.get("3_15s", "")
    tail_30s = buckets.get("60_90s", "") + buckets.get("tail_after", "")

    head_action = _count_hits(head_3s, _ACTION_VERBS["_words"])
    head_conflict = _count_hits(head_3s, _CONFLICT_NOUNS["_words"])
    mid_twist = _count_hits(mid_15s, _TWIST_TRIGGERS["_words"])
    tail_hook = _count_hits(tail_30s, _HOOK_TRIGGERS["_words"])

    out.update({
        "cjk": cjk,
        "char_per_sec": DEFAULT_CHAR_PER_SEC,
        "head_3s_cjk": _cjk_count(head_3s),
        "head_action_hits": head_action,
        "head_conflict_hits": head_conflict,
        "mid_twist_hits": mid_twist,
        "tail_hook_hits": tail_hook,
    })

    flags = []
    # head_3s 必须动作 + 矛盾名词双锚（任缺一）
    if head_3s and (head_action == 0 or head_conflict == 0):
        flags.append({
            "code": ISSUE_CODE_HEAD_NO_ACTION,
            "msg": (f"head_3s 缺锚点：动作动词={head_action} 矛盾名词={head_conflict}"
                    "·短剧体 0-3s 必须扔动作+矛盾"),
            "severity": "minor",
        })
    if mid_15s and mid_twist == 0:
        flags.append({
            "code": ISSUE_CODE_MID_NO_TWIST,
            "msg": "mid_15s 缺反转触发词·短剧体 15s 内必须给反转信号",
            "severity": "minor",
        })
    if tail_30s and tail_hook == 0:
        flags.append({
            "code": ISSUE_CODE_TAIL_NO_HOOK,
            "msg": "tail_30s 缺钩子·短剧体末尾必须留下集悬念",
            "severity": "minor",
        })

    if mode == "active":
        for f in flags:
            out["violations"].append({
                "kind": "microdrama_intraep_beat_lattice",
                "severity": f.get("severity", "minor"),
                "code": f["code"], "message": f["msg"],
                "_doc": "R24 W12 Batch-LL·短剧节拍栅格·advisory·绝不 hard_gate"})
        out["verdict"] = ("FAIL_MINOR"
                          if any(v["severity"] == "minor" for v in out["violations"])
                          else "PASS")
        out["warning"] = "·".join(f["msg"] for f in flags if f["severity"] == "minor") or None
    elif mode == "shadow":
        if flags:
            print("[SHADOW] microdrama_intraep_beat_lattice: "
                  + "·".join(f["msg"] for f in flags)
                  + " — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="微短剧节拍栅格 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--format", default=None,
                    help="CLI 覆盖 format (shortdrama/microdrama/script/prose)")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.format)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
