#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cluster_burst_type_predictor.py — 9 类爆点分类预测 (STRONG) · R24 W12 Batch-JJ · P0

【缺口 · 直播弹幕预测 9-class burst typology】
直播弹幕研究表明读者反应可分 9 类爆点：
laughter / shock / grief / anticipation / shipping / awe / critique / callback / meta。
若 cluster brief 写「应爆 shock」但末尾 80-200 CJK 触发面只有 anticipation 词
→ writer 没把爆点收敛到读者期待。

【做法 · 确定性 · 零 LLM/零联网（占位 lexicon · _placeholder=true）】
  · 维护 9 类爆点字典 _BURST_LEXICONS（占位关键词 · 真版用蒸馏聚类）
  · 从 cluster brief 读 intended_burst_type（无则跳过判定·返回 BURST_TYPE_NO_INTENT info）
  · 扫尾部 80-200 CJK 段（取末尾 N 段累积至窗口大小）
  · 对每类计算命中分（matched_keywords / window_cjk_kilo）
  · 缺面（intended_type 命中分 == 0）→ BURST_TYPE_NOT_DELIVERED advisory
  · 命中其他面更高 → BURST_TYPE_MISMATCH advisory（top1 ≠ intended）

【🔴 2026-07-03 zero_shot_prototype 模型优先路径】真 embedding 后端可用（EMBED_BACKEND≠hash）
  时，尾部窗口额外走 zero_shot_prototype.classify() 做 nearest-centroid 语义分类；置信达标
  （score≥0.5）→ 覆盖词典 top1（top1.source=zero_shot_embedding）；否则/无真后端 → 100%
  沿用词典 top1（top1.source=lexicon，默认零回归）。NOT_DELIVERED 判定仍固定读词典命中分。

【build_manifest 注入 intended_burst_type 字段·event_cluster_context 回灌】
  - mode=active 时给 writer prompt 注入 directive：尾部 80-200 CJK 收束到目标爆点类。

【四 advisory · 全 advisory shadow】
  · BURST_TYPE_NOT_DELIVERED   — intended_type 在尾部命中=0
  · BURST_TYPE_MISMATCH        — 尾部 top1 ≠ intended_type
  · BURST_TYPE_NO_INTENT       — cluster brief 缺 intended_burst_type（info）
  · BURST_TYPE_TAIL_FLAT       — 9 类全 0（info·info-dump 收束）

【北极星】②④⑤ 全 advisory · cluster · shadow 默认 · 绝不 hard_gate
  BURST_TYPE_* 绝不进 audit_hub.HARD_GATE_CODES。

env BURST_TYPE_MODE: off / shadow（默认） / active
用法: python cluster_burst_type_predictor.py <draft> [--project <root>] [--intended <type>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_lookup as cl  # noqa: E402 · 章号⇄cluster_id 唯一权威反查（北极星①·禁 endswith 模糊匹配）

ISSUE_CODE_NOT_DELIVERED = "BURST_TYPE_NOT_DELIVERED"
ISSUE_CODE_MISMATCH = "BURST_TYPE_MISMATCH"
ISSUE_CODE_NO_INTENT = "BURST_TYPE_NO_INTENT"
ISSUE_CODE_TAIL_FLAT = "BURST_TYPE_TAIL_FLAT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

TAIL_CJK_MIN = 80
TAIL_CJK_MAX = 200

# 9 类爆点字典（_placeholder=true · 真版用蒸馏阶段从弹幕语料聚类）
_BURST_LEXICONS = {
    "_placeholder": True,
    "_doc": "R24 W12 Batch-JJ·9 类爆点·占位关键词·真版用弹幕聚类",
    "laughter": [  # 笑点
        "笑", "哈哈", "扑哧", "噗", "好笑", "搞笑", "逗", "呆", "蠢萌",
        "捂嘴", "失声", "忍俊不禁",
    ],
    "shock": [  # 震惊
        "怔住", "震惊", "瞳孔", "倒吸", "心头一震", "如遭雷击", "僵在",
        "呆住", "炸裂", "石化", "猛地", "陡然", "竟然",
    ],
    "grief": [  # 悲伤
        "泪", "哭", "啜泣", "心碎", "悲", "断肠", "哽咽", "痛", "颤抖",
        "跪下", "嘶哑", "落泪", "湿润",
    ],
    "anticipation": [  # 期待 / 钩子
        "未完", "下次", "却不知", "等到", "或许", "如果", "明日", "下一刻",
        "即将", "悬念", "等着", "待会儿",
    ],
    "shipping": [  # 嗑 cp / 甜
        "心动", "脸红", "靠近", "拉手", "拥抱", "对视", "甜", "宠", "吻",
        "羞涩", "心跳", "悸动",
    ],
    "awe": [  # 燃 / 帅
        "霸气", "锋芒", "震慑", "睥睨", "气场", "锐利", "凛然", "压迫",
        "凌厉", "盖压", "睥睨", "执剑", "通天",
    ],
    "critique": [  # 反讽 / 吐槽
        "讽刺", "嗤", "冷笑", "嘲", "切", "呵", "看戏", "看你装", "装样",
        "假惺惺", "笑话", "可笑",
    ],
    "callback": [  # 回扣旧梗
        "原来", "果然", "之前", "那一日", "前文", "回想起", "想起当年",
        "若干年前", "终于明白",
    ],
    "meta": [  # 打破第四面墙
        "诸位", "看官", "读者", "且看", "且说", "且听", "话说", "看到这里",
        "你们", "诸君",
    ],
}

# 🔴 2026-07-03 zero_shot_prototype 模型优先路径·9 类爆点 embedding 原型例句
# （占位·3-5 条/类·待金标准校准·真后端不可用时 100% 走 _BURST_LEXICONS 词典兜底）
_BURST_TYPE_PROTOTYPES = {
    "laughter": ["她扑哧一声笑了出来，太逗了", "他忍俊不禁，捂嘴直笑",
                 "满堂哄笑，气氛一下子轻松起来"],
    "shock": ["他瞳孔一缩，整个人僵在原地", "心头猛地一震，怎么会是这样",
              "众人倒吸一口冷气，不敢置信"],
    "grief": ["她再也忍不住，泪水夺眶而出", "他跪在地上，哭得撕心裂肺",
              "一句话让全场陷入悲伤，无人说话"],
    "anticipation": ["他望着远方，不知下一步会发生什么", "谁也不知道明天等着他们的是什么",
                      "悬念留在这里，答案要等下次揭晓"],
    "shipping": ["两人对视一眼，脸颊微微发烫", "他轻轻握住她的手，心跳漏了一拍",
                 "她靠在他肩头，满心甜蜜"],
    "awe": ["他一剑劈出，气势镇压全场", "那股锋芒让所有人都低下了头",
            "霸道的气场笼罩四周，无人敢直视"],
    "critique": ["他冷笑一声，满是讽刺", "看你还能装到几时，众人窃笑",
                 "这话说得可笑，谁都看得出破绽"],
    "callback": ["原来一切早有伏笔，他终于想起当年的约定", "多年前的那句话，此刻终于应验",
                 "果然如传闻所说，一切都对上了"],
    "meta": ["诸位看官，且听我细细道来", "读者们，这段故事还没完呢",
             "话说到这里，各位应该猜到结局了"],
}


def _classify_burst_type_model(tail: str) -> "dict | None":
    """真后端优先用 zero_shot_prototype 分类尾部窗口·否则 None（调用方 100% 走词典 top1）。"""
    try:
        import zero_shot_prototype
        return zero_shot_prototype.classify(tail, _BURST_TYPE_PROTOTYPES, floor=0.5)
    except Exception:
        return None


def _mode() -> str:
    m = (os.environ.get("BURST_TYPE_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _take_tail(text: str, cjk_min: int = TAIL_CJK_MIN, cjk_max: int = TAIL_CJK_MAX) -> str:
    """从末尾取 80-200 CJK 区间·按段尾倒拼"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p and p.strip()]
    if not paragraphs:
        return ""
    accum = ""
    for p in reversed(paragraphs):
        accum = p + "\n" + accum
        if _cjk_count(accum) >= cjk_min:
            break
    # 钳到 cjk_max
    if _cjk_count(accum) > cjk_max:
        # 从末尾切回 cjk_max
        cjk_acc = 0
        cut_chars = []
        for ch in reversed(accum):
            cut_chars.append(ch)
            if "一" <= ch <= "鿿":
                cjk_acc += 1
            if cjk_acc >= cjk_max:
                break
        accum = "".join(reversed(cut_chars))
    return accum.strip()


def _score_burst_types(tail: str) -> dict:
    """对尾部窗口算每类命中分（matched/(cjk/1000)）"""
    cjk = _cjk_count(tail)
    if cjk == 0:
        return {k: 0.0 for k in _BURST_LEXICONS if not k.startswith("_")}
    out = {}
    for typ, words in _BURST_LEXICONS.items():
        if typ.startswith("_"):
            continue
        cnt = sum(tail.count(w) for w in words)
        # per kilo CJK
        out[typ] = round(cnt / max(cjk / 1000.0, 0.001), 3)
    return out


def _resolve_intended(project_root, cluster_key: str | None, cli_intended: str | None) -> str | None:
    """读 cluster brief 的 intended_burst_type。CLI 覆盖优先。"""
    if cli_intended:
        return cli_intended.strip().lower()
    if not project_root or not cluster_key:
        return None
    try:
        ec_path = Path(project_root) / "_数据库" / "事件簇.json"
        if not ec_path.exists():
            return None
        data = json.loads(ec_path.read_text(encoding="utf-8"))
        clusters = data.get("clusters", []) or []
        target = cl.normalize_cluster_id(cluster_key)
        for c in clusters:
            if not isinstance(c, dict):
                continue
            cid = c.get("cluster_id", "")
            if target and cl.normalize_cluster_id(cid) == target:  # 归一精确比对·禁 endswith 模糊
                t = c.get("intended_burst_type")
                if isinstance(t, str) and t.strip():
                    return t.strip().lower()
        return None
    except (OSError, json.JSONDecodeError):
        return None


def scan(draft_path, project_root=None, cluster_key=None, cli_intended=None) -> dict:
    mode = _mode()
    out = {
        "scanner": "cluster_burst_type_predictor", "schema_version": "1.0",
        "mode": mode, "gate_level": "advisory",
        "violations": [], "verdict": "PASS", "warning": None,
        "_placeholder": _BURST_LEXICONS.get("_placeholder", True),
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
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    intended = _resolve_intended(project_root, cluster_key, cli_intended)
    tail = _take_tail(text)
    scores = _score_burst_types(tail)
    # top1 / top3（词典兜底）
    sorted_pairs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top1_type, top1_score = sorted_pairs[0] if sorted_pairs else (None, 0.0)
    top1_source = "lexicon"
    # 🔴 2026-07-03 模型优先：真后端且分类置信达标 → 用模型 label 覆盖词典 top1
    model_result = _classify_burst_type_model(tail)
    if model_result is not None:
        top1_type, top1_score = model_result["label"], model_result["score"]
        top1_source = model_result["source"]
    out.update({
        "cjk": cjk,
        "tail_cjk": _cjk_count(tail),
        "intended_burst_type": intended,
        "scores": scores,
        "top1": {"type": top1_type, "score": top1_score, "source": top1_source},
    })

    flags = []
    # 全 0 → tail flat info
    all_zero = all(s == 0.0 for s in scores.values())
    if all_zero:
        flags.append({
            "code": ISSUE_CODE_TAIL_FLAT,
            "msg": "尾部 80-200 CJK 9 类爆点关键词全 0·疑似 info-dump 收束",
            "severity": "info",
        })
    if not intended:
        flags.append({
            "code": ISSUE_CODE_NO_INTENT,
            "msg": "cluster brief 缺 intended_burst_type·跳过爆点对账",
            "severity": "info",
        })
    else:
        intended_score = scores.get(intended, 0.0)
        if intended_score == 0.0 and not all_zero:
            flags.append({
                "code": ISSUE_CODE_NOT_DELIVERED,
                "msg": (f"intended_burst_type={intended} 在尾部命中=0"
                        f"·实际 top1={top1_type}({top1_score})"),
                "severity": "minor",
            })
        elif top1_type and top1_type != intended and top1_score > intended_score + 0.5:
            flags.append({
                "code": ISSUE_CODE_MISMATCH,
                "msg": (f"尾部 top1={top1_type}({top1_score}) ≠ intended={intended}"
                        f"({intended_score})·爆点收束错面"),
                "severity": "minor",
            })

    if flags:
        msg = "·".join(f["msg"] for f in flags)
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "cluster_burst_type_predictor",
                    "severity": f.get("severity", "minor"),
                    "code": f["code"], "message": f["msg"],
                    "_doc": "R24 W12 Batch-JJ·9 类爆点·advisory·绝不 hard_gate"})
            out["verdict"] = ("FAIL_MINOR"
                              if any(v["severity"] == "minor" for v in out["violations"])
                              else "PASS")
            out["warning"] = msg
        else:
            print(f"[SHADOW] cluster_burst_type_predictor: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="9 类爆点预测 advisory shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster", default=None,
                    help="cluster_key (e.g. 001)·读 brief.intended_burst_type")
    ap.add_argument("--intended", default=None,
                    help="CLI 覆盖 intended_burst_type")
    args = ap.parse_args()
    rep = scan(args.draft_path, args.project, args.cluster, args.intended)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
