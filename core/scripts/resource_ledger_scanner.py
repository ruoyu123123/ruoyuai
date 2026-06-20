#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resource_ledger_scanner.py — 末世资源账本检测 (advisory · cluster · 2026-06-20)

【缺口】R7 联网调研(DiLouie/Cannibal Halfling/ScribeCount/TVTropes post-apoc): 末世/丧尸/灾后
生存题材核心『资源稀缺账本』此前零检测。LLM 默认无限弹药/无限粮食写法 = 末世感缺失。

【做法 · 确定性纯规则正则(不依赖 LLM)】:
  1. 关键资源词典(食物/水/弹药/医疗/燃料/电池/装甲)+ 余额修饰词典(剩/还有/见底/耗尽/最后/不足/
     仅剩/没有了/用完)。
  2. 决策动作词典(开火/射击/补给/吃/喝/服用/启动/驾驶/使用)。
  3. 计算:
     - resource_mention_density = 资源词命中数 / 千字
     - ledger_anchor_density = (资源词 + 余额修饰词 相邻 ≤8 字)命中数 / 千字 (即真『账本式』描写)
     - decision_count = 决策动作命中数
  4. genre_gated:仅 genre=apocalypse_survival 激活;其他题材 skip。
  5. 阈值:apocalypse_survival 题材 ledger_anchor_density < 0.5/千字 且 decision_count ≥ 3
     → advisory(资源账本缺位·决策无重量)。

【北极星② / ⑤ 顾问非法官】题材声明由作者档/用户偏好决定 · 资源账本是题材工艺 advisory ·
  code RESOURCE_LEDGER_THIN 绝不进 audit_hub.HARD_GATE_CODES 。
  env RESOURCE_LEDGER_MODE: off / shadow(默认·只记不判) / active 。

用法:python resource_ledger_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "RESOURCE_LEDGER_THIN"

# 关键资源类别(精选 · 末世核心)
RESOURCE_WORDS = re.compile(
    r"(子弹|弹药|弹匣|霰弹|手雷|燃料|汽油|柴油|油料|电池|电量|电力|"
    r"食物|粮食|罐头|压缩饼干|干粮|净水|清水|药品|绷带|抗生素|止血带|"
    r"急救包|防毒面具|滤芯|武器|防具|装甲|燃气|柴薪)"
)
# 余额/数量修饰词(必须与资源词靠近才算账本式描写)
LEDGER_QUALIFIERS = re.compile(
    r"(剩|还有|还剩|仅剩|见底|耗尽|用完|用光|没了|没有了|不足|短缺|稀缺|匮乏|"
    r"最后[一二三四五六七八九十几]|[一二三四五六七八九十几百千万0-9]{1,4}发|"
    r"[一二三四五六七八九十几百千万0-9]{1,4}包|半瓶|半箱|半袋|一格|两格|三格)"
)
# 决策动作(消耗资源的动作)
DECISION_VERBS = re.compile(
    r"(开火|射击|射出|扣动扳机|发射|投掷|吃了|吃下|喝了|喝下|服用|"
    r"装弹|换弹|补给|加油|充电|启动|驾驶|穿戴|包扎|注射)"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("RESOURCE_LEDGER_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _resolve_genre(project_root) -> str | None:
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for path, key in [(db / "作者风格.json", "genre_tags"),
                       (db / "用户偏好.json", "genre")]:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get(key)
        if isinstance(v, list) and v:
            return str(v[0]).strip().lower()
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def _count_ledger_anchors(text: str, max_gap: int = 8) -> int:
    """计算资源词与余额修饰词相邻 ≤max_gap 字的命中数(双向相邻都算)。"""
    res_hits = [(m.start(), m.end()) for m in RESOURCE_WORDS.finditer(text)]
    qual_hits = [(m.start(), m.end()) for m in LEDGER_QUALIFIERS.finditer(text)]
    anchors = 0
    used_quals = set()
    for rs, re_ in res_hits:
        for i, (qs, qe) in enumerate(qual_hits):
            if i in used_quals:
                continue
            gap_forward = qs - re_  # 资源词在前
            gap_backward = rs - qe   # 修饰词在前
            if 0 <= gap_forward <= max_gap or 0 <= gap_backward <= max_gap:
                anchors += 1
                used_quals.add(i)
                break
    return anchors


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "resource_ledger", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    genre = _resolve_genre(project_root)
    out["genre"] = genre
    if genre != "apocalypse_survival":
        out["note"] = f"非 apocalypse_survival 题材(genre={genre})·跳过"
        return out

    res_count = len(RESOURCE_WORDS.findall(draft))
    dec_count = len(DECISION_VERBS.findall(draft))
    anchors = _count_ledger_anchors(draft)
    per1k = cjk / 1000.0
    res_density = round(res_count / per1k, 2)
    anchor_density = round(anchors / per1k, 2)
    out["resource_mention_count"] = res_count
    out["resource_mention_density_per_1k"] = res_density
    out["ledger_anchor_count"] = anchors
    out["ledger_anchor_density_per_1k"] = anchor_density
    out["decision_count"] = dec_count

    ANCHOR_FLOOR = 0.5  # /千字
    DECISION_FLOOR = 3
    msg = None
    if anchor_density < ANCHOR_FLOOR and dec_count >= DECISION_FLOOR:
        msg = (f"末世资源账本偏薄:决策动作 {dec_count} 处但资源余额锚定仅 "
               f"{anchors} 处({anchor_density}/千字 < {ANCHOR_FLOOR})·"
               f"apocalypse_survival 题材决策须扎账本(剩多少/用掉多少)·"
               f"建议关键开火/补给/移动前后给具体资源数字或余量描述")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "resource_ledger", "severity": "minor",
                "message": msg,
                "anchor_density": anchor_density, "decision_count": dec_count,
                "thresholds": {"anchor_floor": ANCHOR_FLOOR, "decision_floor": DECISION_FLOOR},
                "_doc": "末世题材账本工艺·advisory 可豁免(序章铺垫/纯心理戏 cluster 不强求)·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] resource_ledger: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="末世资源账本检测(advisory · apocalypse_survival 题材)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 genre 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
