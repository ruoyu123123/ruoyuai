#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue_sequence_expansion.py — CA Adjacency Pair Expansion Scanner
(advisory · cluster · 2026-06-20 · R8 W4 Batch-J · L30)

【缺口】R8 W4 联网调研(Schegloff Sequence Organization 2007 + UIN Malang 2024 + 起点
男频试探/谈判三五步扩展 vs 爽文 2 步塌缩): 真实对话不是 FPP→SPP 二步对答, 高手对话有
pre-sequence(先放话铺垫)+ insert-sequence(中段插问)+ post-sequence(后续追加)三类扩展。
LLM 默认走 FPP→SPP 二步对答 → 试探/谈判/审讯类对话扁平化。

【做法 · 确定性纯规则正则(不依赖 LLM)】:
  1. 切对话 turn: 提取每段对话(『...』/「...」中文双引号包裹的对白)。
  2. CN 触发词表识别扩展:
     - pre-sequence 触发: 『先说一件事』『先问你个问题』『等等』『等下』『先别急』『听我说』
     - insert-sequence 触发: 『你是说』『你的意思是』『再问一下』『再问一句』『确认一下』
     - post-sequence 触发: 『就这样?』『就这么定了』『那……行吧』『那好吧』『没了？』『就这？』
  3. expansion_ratio = expansion_turn_count / dialogue_turn_count
  4. 紧张/试探/谈判类 cluster manifest expected_min_ratio (默认 0.30) 缺位即 advisory。

【🔴 2026-07-03 zero_shot_prototype 模型优先路径】真 embedding 后端可用时每 turn 额外走
  zero_shot_prototype.classify() 语义分类 pre/insert/post，并入触发词表命中集合（并集补召回·
  不取代·不会让已命中的类别消失）；否则/无真后端 → 100% 沿用固定触发词表（默认零回归）。
  output.turn_classify_source 标注本次是否有 turn 被模型补召回。

【🔴 2026-07-03 Wave-4 性能层】scan() 内不再逐 turn 调 classify_turn() 触发子进程——改
  用 _classify_turns_batch() 把本次全部 turn 一次交给 zero_shot_prototype.classify_batch()
  （单条 classify_turn()/_classify_turn_kind() 仍保留供单 turn 场景调用，行为不变）。

【与 R6 OIR 正交】: OIR 查"非问句回答" (反类型回应), 本 scanner 查 turn 间扩展结构密度。

【北极星② / ⑤ 顾问非法官】对话类型由 manifest/cluster brief 声明 · 扩展密度是工艺
advisory · code DIALOGUE_SEQUENCE_EXPANSION_THIN 绝不进 audit_hub.HARD_GATE_CODES 。
env DIALOGUE_SEQ_EXPANSION_MODE: off / shadow(默认) / active。

用法: python dialogue_sequence_expansion.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "DIALOGUE_SEQUENCE_EXPANSION_THIN"

PRE_MARKERS = re.compile(
    r"(先说一件事|先问你个问题|先问个问题|先听我说|等等|等下|先别急|先停一下|"
    r"听我说|有件事|有句话|有个问题想问)")
INSERT_MARKERS = re.compile(
    r"(你是说|你的意思是|你意思是|再问一下|再问一句|确认一下|让我[再来]?确认|"
    r"我能不能[再]?问|我打断一下|插一句|插个嘴|等[一下下]?[等等]?)")
POST_MARKERS = re.compile(
    r"(就这样\??|就这么定了|那……?行吧|那好吧|没了\?|就这\?|就完了\?|"
    r"完了\?|没别的[了么吗]?|这就完[了吗]\?|再没别的[了吗]?)")

# 提取对话 turn (中文双引号包裹) — 容忍跨行
_QUOTE_RE = re.compile(r"[“「]([^“”「」]{1,300}?)[”」]", re.DOTALL)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 🔴 2026-07-03 zero_shot_prototype 模型优先路径·CA pre/insert/post-sequence embedding 原型例句
# （占位·3 条/类·待金标准校准·真后端不可用时 100% 走固定触发词表兜底）
_SEQ_KIND_PROTOTYPES = {
    "pre": ["先说一件事，你先别急", "等一下，我有句话想问你", "听我说完，这事没那么简单"],
    "insert": ["你是说他早就知道了？", "等等，你的意思是他背叛了我们？", "我能不能先确认一下这件事"],
    "post": ["就这样？没有别的了吗？", "那就这么定了，没别的事了", "完了？就这些？"],
}


def _classify_turn_kind(turn: str) -> "str | None":
    """真后端优先用 zero_shot_prototype 分类 turn 语义·否则 None（调用方不并入结果集）。"""
    try:
        import zero_shot_prototype
        result = zero_shot_prototype.classify(turn, _SEQ_KIND_PROTOTYPES, floor=0.5)
        if result is not None:
            return result["label"]
    except Exception:
        pass
    return None


def _classify_turns_batch(turns: list) -> list:
    """真后端优先批量分类全部 turn 语义（Wave-4：一次 classify_batch 取代逐 turn 子进程调用）。

    返回与 turns 等长的 label list（每项 str 或 None）。异常/无真后端 → 全 None
    （调用方 100% 回退正则词典判定，零回归）。数学结果与逐条调 _classify_turn_kind
    完全一致，只是省去 N-1 次重复子进程往返。
    """
    if not turns:
        return []
    try:
        import zero_shot_prototype
        results = zero_shot_prototype.classify_batch(turns, _SEQ_KIND_PROTOTYPES, floor=0.5)
        return [r["label"] if r is not None else None for r in results]
    except Exception:
        return [None] * len(turns)


def _mode() -> str:
    m = (os.environ.get("DIALOGUE_SEQ_EXPANSION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def extract_turns(text: str) -> list:
    """提取对话 turn 列表。"""
    return [m.group(1) for m in _QUOTE_RE.finditer(text)]


def _lexicon_turn_kinds(turn: str) -> set:
    """纯正则触发词表判定（fallback 唯一真理源）。返回 set ⊂ {pre, insert, post}。"""
    kinds = set()
    if PRE_MARKERS.search(turn):
        kinds.add("pre")
    if INSERT_MARKERS.search(turn):
        kinds.add("insert")
    if POST_MARKERS.search(turn):
        kinds.add("post")
    return kinds


def classify_turn(turn: str) -> set:
    """识别 turn 含哪几类扩展标志。返回 set ⊂ {pre, insert, post}。

    🔴 2026-07-03 模型优先：真后端可用时额外用 zero_shot_prototype 分类 turn 语义
    （pre/insert/post 三选一·置信达标才采信），并入正则命中集合（**并集不取代**——
    正则召回的类别永远保留，模型只补召回、不删除，默认无真后端时逐字节零回归）。
    """
    kinds = _lexicon_turn_kinds(turn)
    model_label = _classify_turn_kind(turn)
    if model_label is not None:
        kinds.add(model_label)
    return kinds


def _resolve_expected_min(project_root) -> float | None:
    """从作者档/cluster brief 读 expected_min_ratio。无 → None(走通用兜底)。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    # cluster brief 优先
    ec = db / "事件簇.json"
    if ec.exists():
        try:
            data = json.loads(ec.read_text(encoding="utf-8"))
            for c in data.get("clusters") or []:
                if not isinstance(c, dict):
                    continue
                if (c.get("status") or "").strip().lower() in {
                        "in_progress", "active", "进行中"}:
                    v = c.get("expected_dialogue_expansion_min")
                    if isinstance(v, (int, float)):
                        return float(v)
        except (json.JSONDecodeError, OSError):
            pass
    # 作者档兜底
    ap = db / "作者风格.json"
    if ap.exists():
        try:
            obj = json.loads(ap.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                prof = obj.get("dialogue_expansion_profile") or {}
                v = prof.get("expansion_ratio_baseline")
                if isinstance(v, (int, float)):
                    return float(v)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "dialogue_sequence_expansion", "schema_version": "1.0",
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
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    turns = extract_turns(draft)
    n_turns = len(turns)
    if n_turns < 4:
        out["note"] = f"对话 turn 太少 (n={n_turns})·跳过"
        out["dialogue_turn_count"] = n_turns
        return out

    # 🔴 2026-07-03 Wave-4：全部 turn 一次 classify_batch（取代逐 turn classify_turn 子进程调用）
    model_labels = _classify_turns_batch(turns)

    pre_count = 0
    insert_count = 0
    post_count = 0
    expansion_turns = 0
    model_boosted_turns = 0
    for t, model_label in zip(turns, model_labels):
        lex_k = _lexicon_turn_kinds(t)
        k = set(lex_k)
        if model_label is not None:
            k.add(model_label)  # 词典 ∪ 模型（真后端不可用时 k == lex_k）
        if k != lex_k:
            model_boosted_turns += 1
        if k:
            expansion_turns += 1
        if "pre" in k:
            pre_count += 1
        if "insert" in k:
            insert_count += 1
        if "post" in k:
            post_count += 1

    expansion_ratio = round(expansion_turns / n_turns, 3)
    pre_density = round(pre_count / n_turns, 3)
    insert_density = round(insert_count / n_turns, 3)
    post_density = round(post_count / n_turns, 3)
    out.update({
        "dialogue_turn_count": n_turns,
        "expansion_turn_count": expansion_turns,
        "expansion_ratio": expansion_ratio,
        "pre_seq_density": pre_density,
        "insert_seq_density": insert_density,
        "post_seq_density": post_density,
        "model_boosted_turns": model_boosted_turns,
        "turn_classify_source": ("zero_shot_embedding+lexicon" if model_boosted_turns
                                  else "lexicon"),
    })

    expected_min = _resolve_expected_min(project_root)
    # 通用兜底 0.10 (爽文低基线·不矫枉过正)
    floor = expected_min if expected_min is not None else 0.10
    out["expected_min_ratio"] = floor

    msg = None
    if expansion_ratio < floor:
        msg = (f"对话 turn 扩展密度偏低: expansion_ratio={expansion_ratio} < {floor}·"
               f"pre/insert/post={pre_density}/{insert_density}/{post_density}·"
               f"建议补 pre-sequence(先放话铺垫)/insert-sequence(中段插问)/"
               f"post-sequence(后续追加)·避免 FPP→SPP 二步扁平塌缩")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "dialogue_sequence_expansion", "severity": "minor",
                "message": msg, "expansion_ratio": expansion_ratio,
                "pre_density": pre_density, "insert_density": insert_density,
                "post_density": post_density, "floor": floor,
                "_doc": ("对话扩展是工艺 advisory·作者档/cluster brief 可调 expected_min·"
                         "爽文低基线天然合理·绝不 hard_gate")})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] dialogue_sequence_expansion: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="CA Adjacency Pair 扩展密度检测(advisory · cluster)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 expected_min_ratio")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
