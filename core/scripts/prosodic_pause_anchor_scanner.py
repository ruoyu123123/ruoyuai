#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prosodic_pause_anchor_scanner.py — prosodic 层级 pause anchor 信息密度 valley breathing
(R19 W8 Batch-Y·P2)

【缺口·2026-06-21·prosodic phrase hierarchy(Selkirk 1986/Beckman & Pierrehumbert 1986)】
散文 prosody 三层 pause:
  ① word-level    : 逗号/顿号(短停顿)
  ② phrase-level  : 分号/破折号(中停顿)
  ③ sentence-level: 句号/问号/感叹号(长停顿)
高密度信息(实体/动词/数字)若挤在长 phrase 内不留 valley(短-长 pause anchor 切)→ 读者
喘不过气. 本 scanner 测"anchor 处信息密度落差"——pause anchor 处前后 N 字内信息密度的
locally low(valley) → 节奏健康. 全高密度无 valley → 窒息感.

【2026-07-01 接入真模型】信息密度优先调用已训练部署的 surprisal_gpt2(经 nn_surprisal_bridge /
feature_cache 二选一)算句子级 + anchor 窗口级 mean_surprisal 代替信息密度打分；
RUOYU_NN_SURPRISAL 未开启/模型未完整命中时整体回退下面这套 marker 计数密度代理(不变)。

【输入】cluster 草稿(CLUSTER_MODE=1 env).

【探针】
  1. 按 sentence-level pause 切句·每句内按 phrase-level pause 再切.
  2. 对每 phrase-level pause anchor 计算前后 ±12 字字符密度 - 实体/数字/动作动词密度.
  3. info_density valley = anchor 处密度 < phrase 平均的 70%.
  4. valley_rate = anchor 中 valley 占比.
  5. valley_rate < FLOOR_VALLEY_RATE(默认 0.20) → PROSODIC_PAUSE_VALLEY_MISSING advisory.

【北极星⑤】顾问非法官·全 advisory·env PROSODIC_PAUSE_ANCHOR_MODE 默认 shadow·
  PROSODIC_PAUSE_VALLEY_MISSING 绝不 hard_gate.

【与既有 scanner 严格正交】
  - prose_rhythm_scanner    : 句长统计·正交(本=pause 层级)
  - syntactic_diversity     : POS n-gram 多样性·正交
  - validate_style.段长     : 段长粗粒度·正交
  - punctuation_per_1k_*    : 单标点密度·正交(本=anchor 处密度落差)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "PROSODIC_PAUSE_VALLEY_MISSING"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
MIN_CJK = 800
FLOOR_VALLEY_RATE = 0.20
ANCHOR_WINDOW = 12  # ±12 字

# 信息密度 marker: 数字/英文/动作动词种子/常见实体词尾
_INFO_DIGIT = re.compile(r"[0-9０-９]")
_INFO_LATIN = re.compile(r"[A-Za-z]")
_INFO_ACTION_VERBS = ("打", "跑", "冲", "抓", "推", "拉", "扑", "撞", "踢", "踩",
                      "斩", "刺", "挥", "击", "砸", "砍", "掀", "扯", "甩", "握")
_INFO_ENTITY_SUFFIX = ("先生", "小姐", "公子", "陛下", "大人", "教主", "宗主")

# pause 层级
_PAUSE_PHRASE = re.compile(r"[，、；——]")
_PAUSE_SENTENCE = re.compile(r"[。！？!?…]+")


def _mode() -> str:
    m = (os.environ.get("PROSODIC_PAUSE_ANCHOR_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _info_score(s: str) -> int:
    """单段字符串信息密度(粗 marker 计数)."""
    if not s:
        return 0
    score = 0
    score += len(_INFO_DIGIT.findall(s))
    score += len(_INFO_LATIN.findall(s))
    for v in _INFO_ACTION_VERBS:
        score += s.count(v)
    for suf in _INFO_ENTITY_SUFFIX:
        score += s.count(suf)
    return score


def _info_density(s: str) -> float:
    L = max(1, len(s.strip()))
    return _info_score(s) / L


def _split_sentences(text):
    parts = _PAUSE_SENTENCE.split(text)
    return [p for p in parts if p.strip()]


def _predict_density_batch(texts: list[str]) -> list[float | None]:
    """批量取 GPT-2 mean_surprisal 当局部信息密度代理。优先 FeatureStore(带缓存)→
    回退 nn_surprisal_bridge 直连·与 surprisal_scanner 同构。全不可用 → 全 None
    (调用方整体回退 marker 计数密度代理·不变)。"""
    if not texts:
        return []
    preds = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled as feature_store_enabled
        if feature_store_enabled():
            preds = FeatureStore.get().compute_surprisal_batch(texts)
    except Exception:  # noqa: BLE001 FeatureStore 故障 → 退 bridge，绝不影响 scanner
        preds = None
    if preds is None:
        try:
            import nn_surprisal_bridge as bridge
        except ImportError:
            return [None] * len(texts)
        preds = bridge.predict_batch(texts)
    if len(preds) != len(texts):
        return [None] * len(texts)
    return [(p.get("mean_surprisal") if p else None) for p in preds]


def _analyze_anchors_heuristic(text: str) -> dict:
    """对每句内 phrase 级 anchor 统计 info_density valley(marker 计数代理·真模型不可用兜底)."""
    sentences = _split_sentences(text)
    total_anchors = 0
    valley_anchors = 0
    anchor_samples = []
    for sent in sentences:
        if not sent.strip():
            continue
        # phrase 内总密度均值
        phrase_density = _info_density(sent)
        if phrase_density == 0:
            continue
        for m in _PAUSE_PHRASE.finditer(sent):
            idx = m.start()
            left = sent[max(0, idx - ANCHOR_WINDOW):idx]
            right = sent[idx + 1:idx + 1 + ANCHOR_WINDOW]
            window = left + right
            if len(window.strip()) < 3:
                continue
            local_density = _info_density(window)
            total_anchors += 1
            # valley: 局部密度 < 句子均值 * 0.70
            is_valley = local_density < phrase_density * 0.70
            if is_valley:
                valley_anchors += 1
            if len(anchor_samples) < 10:
                anchor_samples.append({
                    "char": m.group(),
                    "phrase_density": round(phrase_density, 4),
                    "local_density": round(local_density, 4),
                    "is_valley": is_valley,
                })
    valley_rate = (valley_anchors / total_anchors) if total_anchors else None
    return {
        "total_anchors": total_anchors,
        "valley_anchors": valley_anchors,
        "valley_rate": round(valley_rate, 4) if valley_rate is not None else None,
        "samples": anchor_samples,
        "source": "heuristic",
    }


def _analyze_anchors_model(text: str) -> dict | None:
    """真模型版：句子级 + anchor 窗口级批量算 GPT-2 mean_surprisal 代替 info_density。
    窗口切法/valley 判定跟启发式版本同构(仅把 _info_density 换成模型 surprisal)。
    任一句/窗口模型未命中 → None(调用方整体回退启发式版本·不变——避免部分 None 破坏均值语义)。"""
    sentences = [s for s in _split_sentences(text) if s.strip()]
    if not sentences:
        return None

    per_sentence_anchors = []
    for sent in sentences:
        anchors = []
        for m in _PAUSE_PHRASE.finditer(sent):
            idx = m.start()
            left = sent[max(0, idx - ANCHOR_WINDOW):idx]
            right = sent[idx + 1:idx + 1 + ANCHOR_WINDOW]
            window = left + right
            if len(window.strip()) < 3:
                continue
            anchors.append({"char": m.group(), "window": window})
        per_sentence_anchors.append(anchors)

    all_windows = [a["window"] for anchors in per_sentence_anchors for a in anchors]
    if not all_windows:
        return None  # 无 anchor·没必要跑模型(回退启发式会算出同样的 0 anchor 结果)

    densities = _predict_density_batch(sentences + all_windows)
    if len(densities) != len(sentences) + len(all_windows) or any(v is None for v in densities):
        return None  # 任一句/窗口未命中 → 整体回退启发式(避免部分 None 破坏均值语义)

    sent_density = densities[:len(sentences)]
    anchor_density = densities[len(sentences):]

    total_anchors = 0
    valley_anchors = 0
    anchor_samples = []
    cursor = 0
    for s_i, anchors in enumerate(per_sentence_anchors):
        if not anchors:
            continue
        phrase_density = sent_density[s_i]
        if phrase_density == 0:
            cursor += len(anchors)
            continue
        for a in anchors:
            local_density = anchor_density[cursor]
            cursor += 1
            total_anchors += 1
            is_valley = local_density < phrase_density * 0.70
            if is_valley:
                valley_anchors += 1
            if len(anchor_samples) < 10:
                anchor_samples.append({
                    "char": a["char"],
                    "phrase_density": round(phrase_density, 4),
                    "local_density": round(local_density, 4),
                    "is_valley": is_valley,
                })
    valley_rate = (valley_anchors / total_anchors) if total_anchors else None
    return {
        "total_anchors": total_anchors,
        "valley_anchors": valley_anchors,
        "valley_rate": round(valley_rate, 4) if valley_rate is not None else None,
        "samples": anchor_samples,
        "source": "model",
    }


def analyze_anchors(text: str) -> dict:
    """对每句内 phrase 级 anchor 统计 info_density valley。
    优先真模型(GPT-2 surprisal)·不可用/未完整命中 → 回退 marker 计数密度代理(不变)。"""
    result = _analyze_anchors_model(text)
    return result if result is not None else _analyze_anchors_heuristic(text)


def scan(draft_path) -> dict:
    mode = _mode()
    out = {"scanner": "prosodic_pause_anchor", "schema_version": "1.0",
           "mode": mode, "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        out["note"] = "off·skip"
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    if _cjk_count(text) < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    metrics = analyze_anchors(text)
    out["metrics"] = metrics
    out["source"] = metrics.get("source")
    vr = metrics["valley_rate"]
    if metrics["total_anchors"] < 20:
        out["note"] = "anchor 不足·skip 告警"
        return out
    findings = []
    if vr is not None and vr < FLOOR_VALLEY_RATE:
        findings.append(
            f"valley_rate {vr} < FLOOR {FLOOR_VALLEY_RATE}·anchor 处缺信息密度低谷·窒息感")

    if findings:
        msg = " · ".join(findings)
        if mode == "active":
            out["violations"].append({
                "kind": "prosodic_pause_anchor", "severity": "minor",
                "code": ISSUE_CODE, "message": msg, "metrics": metrics,
                "_doc": "R19 W8 Batch-Y·P2·prosodic 三层 pause hierarchy·advisory",
            })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] prosodic_pause_anchor[{ISSUE_CODE}]: {msg} — 不上报",
                  file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="R19 W8 Batch-Y·P2·prosodic 三层 pause hierarchy valley·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
