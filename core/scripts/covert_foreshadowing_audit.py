#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""covert_foreshadowing_audit.py — 隐性/显性伏笔 delivery_mode 占比 advisory
(advisory · cluster · 2026-06-20 R12 W6 Batch-Q · P2 · shadow)

【缺口】arxiv 2601.07033 CFPG + mystorydoctor Farland + writeseen + darlingaxe
reread test + laterpress dramatic irony 综合：网文 LLM 写伏笔默认全部 overt
(直白点名"这件事日后将带来麻烦")·缺隐性伏笔形态(buried/passing/objects/parallel)
五型分布·二刷线索可读性差。

【做法 · plant 形态扫描（不依赖 LLM）】
  1. 读 _数据库/foreshadowing.json 取本 cluster 已落子的 plant entries。
  2. 按 plant_text 形态字典匹配 → delivery_mode ∈ {overt/buried/passing/objects/parallel}
       overt: 直白宣告未来 / "之后 / 后来 / 将" 等显式前瞻锚词
       buried: 嵌在日常细节、心理活动里的暗示
       passing: 一笔带过、过场对话单语
       objects: 物件/道具描写承担伏笔功能
       parallel: 配角行为/平行场景映射主线
  3. 计算 covert_ratio = (buried+passing+objects+parallel) / total
  4. 作者档 author_covert_ratio_baseline.{p10,p50,p90} 第一权威；
     无作者档兜底 band 0.40-0.70（>=2 reread test SOP 推荐区间）
  5. ratio < band[0] → COVERT_FORESHADOWING_THIN advisory (overt 化)
     ratio > band[1] → COVERT_FORESHADOWING_OPAQUE advisory (二刷不可读)

【🔴 2026-07-03 zero_shot_prototype 模型优先路径】pre-filter（是否候选 plant）仍固定用
  _DELIVERY_LEXICON/classify_plant() 原判定；真 embedding 后端可用时桶位（5 类里选哪个）
  额外走 zero_shot_prototype.classify() 精化，置信达标 → 覆盖桶位（plant.classify_source=
  zero_shot_embedding）；否则/无真后端 → 100% 用原词典判定桶位（classify_source=lexicon，
  默认零回归）。classify_plant() 函数本体不变，继续当 fallback 唯一真理源。

【🔴 2026-07-03 Wave-4 性能层】scan() 内不再逐 plant 调 _classify_plant_mode() 触发子
  进程——先收集本次全部候选 plant（草稿行 + foreshadowing.json 两源合并），再用
  _classify_plants_batch() 一次交给 zero_shot_prototype.classify_batch()（单条
  _classify_plant_mode() 仍保留供单条场景调用，行为不变）。

【北极星】②④⑤ cluster 视野·作者档第一权威·advisory shadow·绝不 hard_gate
COVERT_FORESHADOWING_* 绝不进 audit_hub.HARD_GATE_CODES。

env COVERT_FORESHADOWING_MODE: off / shadow(默认) / active
用法：python covert_foreshadowing_audit.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODES = ("COVERT_FORESHADOWING_THIN", "COVERT_FORESHADOWING_OPAQUE")
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# delivery_mode 5 类锚词字典（占位·shadow 期统计·待金标准校准）
_DELIVERY_LEXICON = {
    "overt": re.compile(r"(日后|后来|不久之后|此后|之后|将|即将|预示|预感|预兆|"
                        r"不知道这会|没想到这|没想到将|多年后|从此以后)"),
    "buried": re.compile(r"(似乎|彷佛|仿佛|隐隐|约莫|无意中|偶然|顺手|"
                          r"不经意|下意识|心头|微微|淡淡|心里隐约)"),
    "passing": re.compile(r"(随口|顺嘴|顺口|一句|带过|插话|嘟囔|"
                           r"低声咕哝|嘀咕|话头一转|话锋一转)"),
    "objects": re.compile(r"(挂着|摆着|搁着|放着|藏在|压在|刻着|绣着|"
                           r"写着|印着|镶着|系着|怀里揣|袖中|案上|匣中)"),
    "parallel": re.compile(r"(街角|远处|另一边|另一头|窗外|墙外|楼下|楼上|"
                            r"邻桌|隔壁|对面|路过|擦肩|背景里|远远地)"),
}

# 🔴 2026-07-03 zero_shot_prototype 模型优先路径·5 类伏笔隐蔽度 embedding 原型例句
# （占位·3 条/类·待金标准校准·真后端不可用时 100% 走 classify_plant() 词典兜底）
_DELIVERY_MODE_PROTOTYPES = {
    "overt": ["日后这件事会带来大麻烦", "他心想，将来必有一场恶战",
              "多年后回想起来，这正是转折点"],
    "buried": ["她心头隐隐有些不安，说不清为什么", "他下意识摸了摸口袋，若有所思",
               "那一瞬间他仿佛想起了什么，又很快压下"],
    "passing": ["他随口提了一句旧事，便转开话题", "她顺嘴说了句谁也没在意的话",
                "对话中夹杂着一句无关紧要的嘀咕"],
    "objects": ["案上摆着一枚不起眼的旧玉佩", "墙角挂着一幅蒙尘的画像",
                "他怀里揣着一封没有拆开的信"],
    "parallel": ["街角另一头，两个陌生人低声交谈", "窗外远远传来一阵脚步声",
                 "隔壁桌的客人正谈论着一桩旧案"],
}


def _classify_plant_mode(text: str, fallback: str) -> "tuple[str, str]":
    """(delivery_mode, classify_source)。真后端优先用 zero_shot_prototype 分类·

    否则/置信不足 → 100% 用调用方传入的 fallback（各调用点自己的原词典判定结果，
    保证维持各自原有优先级顺序不被打乱，零回归）。"""
    if text:
        try:
            import zero_shot_prototype
            result = zero_shot_prototype.classify(text, _DELIVERY_MODE_PROTOTYPES, floor=0.5)
            if result is not None:
                return result["label"], result["source"]
        except Exception:
            pass
    return fallback, "lexicon"


def _classify_plants_batch(items: list) -> list:
    """items: [(text, fallback_bucket), ...] → [(delivery_mode, classify_source), ...]。

    🔴 2026-07-03 Wave-4：本次全部候选 plant 一次 classify_batch（取代逐条
    _classify_plant_mode 子进程调用）。真后端不可用/置信不足的条目 100% 用调用方传入的
    fallback（各调用点自己的原词典判定结果，零回归）。
    """
    if not items:
        return []
    texts = [t for t, _ in items]
    try:
        import zero_shot_prototype
        results = zero_shot_prototype.classify_batch(texts, _DELIVERY_MODE_PROTOTYPES, floor=0.5)
    except Exception:
        results = [None] * len(items)
    out = []
    for (_text, fallback), r in zip(items, results):
        if r is not None:
            out.append((r["label"], r["source"]))
        else:
            out.append((fallback, "lexicon"))
    return out


def _mode() -> str:
    m = (os.environ.get("COVERT_FORESHADOWING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_baseline(project_root):
    """读作者档 author_covert_ratio_baseline {p10,p50,p90}。无 → 兜底 band。"""
    fallback = {"p10": 0.40, "p50": 0.55, "p90": 0.70, "_source": "fallback"}
    if not project_root:
        return fallback
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return fallback
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(obj, dict):
        return fallback
    base = obj.get("author_covert_ratio_baseline")
    if isinstance(base, dict) and "p10" in base and "p90" in base:
        out = {"p10": float(base["p10"]), "p90": float(base["p90"]),
               "p50": float(base.get("p50", (base["p10"] + base["p90"]) / 2)),
               "_source": "author_profile"}
        return out
    return fallback


def classify_plant(text: str) -> str:
    """单条 plant 文本分桶。返回第一个命中桶；全无命中 → overt（默认显性）。"""
    if not text:
        return "overt"
    for bucket in ("buried", "passing", "objects", "parallel"):
        if _DELIVERY_LEXICON[bucket].search(text):
            return bucket
    if _DELIVERY_LEXICON["overt"].search(text):
        return "overt"
    return "overt"


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "covert_foreshadowing_audit", "schema_version": "1.0",
           "mode": mode, "gate_level": "advisory", "violations": [],
           "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    # 双源 plant 收集：(A) 正文行内匹配 (B) 项目级 foreshadowing.json plant entries
    # candidates: [(source, display_text(截80), classify_text(全文), fallback_bucket)]
    candidates = []
    for line in text.split("\n"):
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        matched_bucket = None
        for bucket, rx in _DELIVERY_LEXICON.items():
            if rx.search(stripped):
                matched_bucket = bucket
                break  # 首命中即止（pre-filter：至少命中一类才算候选 plant）
        if matched_bucket is None:
            continue
        # 🔴 2026-07-03 模型优先精化桶位：pre-filter 仍用原词典判定「是不是候选 plant」，
        # 桶位分类真后端可用时优先信模型（否则 100% 用 matched_bucket，零回归）
        candidates.append(("draft_line", stripped[:80], stripped, matched_bucket))

    fjson_path = None
    if project_root:
        fjson_path = Path(project_root) / "_数据库" / "foreshadowing.json"
        if fjson_path.exists():
            try:
                fobj = json.loads(fjson_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                fobj = None
            if isinstance(fobj, dict):
                entries = fobj.get("entries") or fobj.get("foreshadowings") or []
                if isinstance(entries, list):
                    for e in entries:
                        if not isinstance(e, dict):
                            continue
                        t = e.get("plant_text") or e.get("text") or ""
                        if not t:
                            continue
                        fallback_mode = classify_plant(str(t))
                        candidates.append(("foreshadowing_json", str(t)[:80], str(t), fallback_mode))

    # 🔴 2026-07-03 Wave-4：本次全部候选 plant 一次 classify_batch（取代逐条
    # _classify_plant_mode 子进程调用）。注意：变量名不可叫 mode——scan() 顶部
    # `mode = _mode()` 是函数级单一命名空间，重名会覆盖外层 mode 导致后面
    # if mode == "active" 失效。
    classified = _classify_plants_batch([(c[2], c[3]) for c in candidates])
    plants = [
        {"source": src, "text": disp, "delivery_mode": dmode, "classify_source": csrc}
        for (src, disp, _ctext, _fb), (dmode, csrc) in zip(candidates, classified)
    ]

    out["plant_total"] = len(plants)
    if not plants:
        out["note"] = "本 cluster 无可识别 plant·跳过"
        return out

    bucket_counts = {k: 0 for k in ("overt", "buried", "passing", "objects", "parallel")}
    for p in plants:
        bucket_counts[p["delivery_mode"]] = bucket_counts.get(p["delivery_mode"], 0) + 1
    total = len(plants)
    covert = total - bucket_counts["overt"]
    covert_ratio = round(covert / total, 4)
    out["bucket_counts"] = bucket_counts
    out["covert_ratio"] = covert_ratio

    band = _read_baseline(project_root)
    out["baseline"] = band

    flags = []
    if covert_ratio < band["p10"]:
        flags.append({"code": "COVERT_FORESHADOWING_THIN",
                      "msg": (f"covert_ratio={covert_ratio} < {band['p10']:.2f}·"
                              f"伏笔过度 overt 化（{bucket_counts['overt']}/{total} 显性）")})
    elif covert_ratio > band["p90"]:
        flags.append({"code": "COVERT_FORESHADOWING_OPAQUE",
                      "msg": (f"covert_ratio={covert_ratio} > {band['p90']:.2f}·"
                              f"过度隐晦·二刷不可读")})
    out["flags"] = flags

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "covert_foreshadowing", "severity": "minor",
                    "code": f["code"], "message": f["msg"],
                    "covert_ratio": covert_ratio, "baseline": band,
                    "_doc": "CFPG/Farland/reread test·advisory·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] covert_foreshadowing: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="隐显伏笔 delivery_mode 占比 advisory (shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
