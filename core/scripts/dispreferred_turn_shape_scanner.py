#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispreferred_turn_shape_scanner.py — 非偏好回应「裸拒绝」检测（advisory · cluster · 2026-06-20）

【缺口】会话分析 preference organization（arXiv PMC8504554）：真人在做「非偏好回应」
（拒绝/反对/否决/拒邀）时，turn 形状不是裸句即答——而是带【延迟 token + 缓冲 hedge + account
（解释/理由）】。实测非偏好回应延迟 561ms vs 偏好 269ms（停顿/「嗯」「那个」「呃」「可是」「抱歉」
前置 + 给理由）。AI 写对话常【裸句即否】=「不行。」「做不到。」张口就来，缺这层社会语用缓冲 →
机器人感。全库无 scanner 查这一维（对话 turn-shape 的语用自然度）。本 scanner 补检测端。

【做法 · 确定性可算半边】（北极星守卫：缓冲质量是语义判断·只做可算的「裸拒绝形状」密度）：
  1. 抽取对话引号内容（弯引号 U+201C/U+201D 或 「」）。
  2. 某条引号内容【去标点后开头前 4 字内】以 DISPREF_OPENER（不行/做不到/不同意/拒绝/没门…）
     开头 = 一次「非偏好回应」。
  3. 同条引号内容里若出现任一 BUFFER（嗯/那个/呃/可是/抱歉/我知道/恐怕…）= 有缓冲·不算裸。
  4. bare_count = 以 OPENER 开头且无 BUFFER 的对话条数；dispref_total = 以 OPENER 开头的对话条数；
     需 dispref_total >= 4（样本足）才判；bare_ratio = bare/dispref_total。
     bare_ratio > 阈值 → advisory「非偏好回应多为裸拒绝·建议加延迟/缓冲/account」。
  ⚠️ 只能测「显式开头标志词 + 同句缓冲词的缺失」·靠语气/上下文隐性缓冲的写法测不到 → 故意保守（宁可漏报）。

【北极星⑤ 顾问非法官】强势角色碾压式裸拒绝是合理风格选择·writer 有理由偏离 → 永远 advisory，code
  DISPREFERRED_TURN_BARE **绝不进 audit_hub.HARD_GATE_CODES**。
  env DISPREFERRED_TURN_SHAPE_MODE: off / shadow(默认·只记不判·零回归) / active。
  🔬 阈值 BARE_RATIO_FLOOR/MIN_DISPREF 保守占位（强势角色裸拒是合理风格·阈值须宽松防误伤）
     ·待金标准校准（真作者原文喂自身 PASS）。单向只报「裸拒绝过多」方向。

用法：python dispreferred_turn_shape_scanner.py <draft_path> [--project <root>] [--manifest m.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "DISPREFERRED_TURN_BARE"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 非偏好回应开头标志词（拒绝/反对/否决·去标点后引号内容开头前 4 字内）
# —— 长词在前（^锚定 alternation 左优先·避免「不」先于「不行」抢匹配）
DISPREF_OPENER = re.compile(
    r"^(?:想都别想|不可能|做不到|办不到|不同意|不行啊|不行|不能|拒绝|没门|休想|我不|别想)"
)
# 缓冲词（同条引号内容里出现任一 = 有延迟/hedge/account·不算裸拒绝·豁免）
BUFFER = re.compile(
    r"嗯|那个|呃|这个|唉|可是|只是|其实|抱歉|对不起|话是|我知道|我明白|不是我|要不|恐怕|怕是"
)
# 引号内容开头去标点（剥掉前导非 CJK/非字母数字字符·再判 OPENER）
_LEADING_PUNCT = re.compile(r"^[^一-鿿A-Za-z0-9]+")
# 对话引号（弯引号 U+201C/U+201D · 中文方角引号 「」）
_DIALOGUE_PATTERNS = (
    re.compile("“([^”]*)”"),   # “ … ”
    re.compile("「([^」]*)」"),                # 「 … 」
)

BARE_RATIO_FLOOR = 0.8   # 🔬 待金标准校准（真作者原文喂自身 PASS）：裸拒绝占比 > 此 = 缺语用缓冲偏多
MIN_DISPREF = 4          # 非偏好回应样本 < 此 不报（样本太少·conservative）
MIN_CJK = 500            # 草稿 < 此 跳过
SAMPLE_LIMIT = 6         # report 里裸拒绝样本上限

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    # 2026-06-20 金标准校准【保持 shadow】：5 真作者中主神/剑来 bare_ratio=1.0(仅因 dispref_total<4
    # 样本守卫未触发)——真作者本就写裸拒绝(强势角色常态·中文网文非缺陷)·前提弱潜在误报风险(同
    # group_dialogue)·故保持 shadow 不放量·env 显式 active 才上报。
    m = (os.environ.get("DISPREFERRED_TURN_SHAPE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _extract_dialogues(text: str) -> list:
    """抽取所有对话引号内容（弯引号 + 方角引号）。返回 [str]。"""
    out = []
    for pat in _DIALOGUE_PATTERNS:
        out.extend(pat.findall(text))
    return [d for d in out if d.strip()]


def _is_dispref_opener(content: str) -> bool:
    """去标点后开头是否以非偏好动作词开头（前 4 字内）。"""
    head = _LEADING_PUNCT.sub("", content)
    return bool(DISPREF_OPENER.match(head))


def detect_bare_dispreferred(text: str) -> dict:
    """检测对话里非偏好回应的「裸拒绝」（开头否决词 + 同句 0 缓冲）。

    返回 {dispref_total, bare_count, bare_ratio, samples[]}。
    """
    text = _strip_changes(text)
    dispref_total = 0
    bare = []
    for content in _extract_dialogues(text):
        if not _is_dispref_opener(content):
            continue
        dispref_total += 1
        if BUFFER.search(content):
            continue   # 含缓冲词·非裸拒绝
        bare.append(content.strip())
    bare_count = len(bare)
    ratio = (bare_count / dispref_total) if dispref_total else 0.0
    return {
        "dispref_total": dispref_total,
        "bare_count": bare_count,
        "bare_ratio": round(ratio, 3),
        "samples": [b[:40] for b in bare[:SAMPLE_LIMIT]],
    }


def scan(draft_path, project_root=None) -> dict:
    """非偏好回应裸拒绝检测。永远 advisory（北极星⑤）。"""
    mode = _mode()
    out = {"scanner": "dispreferred_turn_shape", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    result = detect_bare_dispreferred(draft)
    out["metrics"] = result
    out["dispref_total"] = result["dispref_total"]
    out["bare_count"] = result["bare_count"]
    out["bare_ratio"] = result["bare_ratio"]

    ratio = result["bare_ratio"]
    over = result["dispref_total"] >= MIN_DISPREF and ratio > BARE_RATIO_FLOOR
    if over:
        msg = (f"非偏好回应多为裸拒绝（{ratio:.0%} 的拒绝/否决张口即否·0 缓冲 > "
               f"{BARE_RATIO_FLOOR:.0%}·{result['bare_count']}/{result['dispref_total']} 条）。"
               f"建议加延迟/缓冲/account（嗯…/那个/可是/抱歉+理由）·"
               f"真人拒绝带停顿与解释（强势角色碾压式裸拒可豁免）")
        if mode == "active":
            out["violations"].append({
                "kind": "dispreferred_turn_bare", "severity": "minor",
                "message": msg, "bare_ratio": ratio,
                "sample_bare": result["samples"][:4],
                "_doc": "preference organization(arXiv PMC8504554)·非偏好回应延迟561ms vs偏好269ms·"
                        "缓冲是社会语用自然度·强势角色裸拒是创作选择→advisory 待裁决·"
                        "开头否决词+同句缓冲缺失是可算半边·真语用质量留 judge/作者"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（violations 空·零回归）
            print(f"[SHADOW] dispreferred_turn_shape: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="非偏好回应『裸拒绝』检测(advisory)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None, help="兼容 audit_hub 传参（预留作者档豁免基线）")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
