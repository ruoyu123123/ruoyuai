#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indirect_characterization_ratio_scanner.py — 烘云托月 + 横云断山 · 金圣叹评点工艺现代化

【缺口 · R18 W7 Batch-T·P1 · 2026-06-21】金圣叹评水浒原文 + Plaks
《Four Masterworks of Ming Novel》+ 脂砚斋评红楼 + 余英时《红楼梦评点研究》：
  金圣叹评点提出两大工艺 ——
    ① 烘云托月（indirect characterization）：主角形象不由主角自述/直写显化，而由
       配角侧写、对话、反应衬托——『烘的是云，托的是月』。LLM 默认全程直写主角
       动作/心理，配角只是工具人，丢失中文小说传统衬托质感。
    ② 横云断山（inter-cluster tonal pendulum）：相邻 cluster 间冷热基调交替（hot→
       cold→hot），形成『山色为云所断』的节律。LLM 默认每个 cluster 同温同色，
       长篇变成单调匀速推进。

【与既有 scanner 显式去重】
  - R10 mass_reactor_scanner：单 cluster 内『众人议论/弹幕』≥3 句反应聚簇
    本 scanner = 配角对主角的【侧写描述】（不要求聚簇，单句也算）+ 跨 cluster 冷热
    交替 · 正交（mass_reactor 不区分指向，本 scanner 只算【指向主角】的侧写）
  - R10 拟声拟态：语言层 mimetic AA/ABB/ABAB 词形
    本 scanner = 叙事人称指向（不依赖词形）·正交
  - cross_scene_voice_drift：同角色跨场景 voice 漂移
    本 scanner = 跨 cluster 冷热基调（tonal）·不同维度

【两探针 · 确定性纯规则】
  ① indirect_ratio（单 cluster · 烘云托月）
     - 切场景 → 每场景：
       direct_protag_signal = 主角名字 + 动作动词（说/做/想/走/看/听）出现次数
       indirect_protag_signal = 配角名 + 视/看/望/盯/听/瞧 + 主角名 → 配角观察主角
                                or 配角对话引号内 + 提主角名 → 配角议论主角
     - ratio = indirect / (direct + indirect)
     - 作者档 indirect_characterization_baseline.mean+std ECDF z-band 优先
     - 兜底地板 0.10（极端纯直写）

  ② tonal_pendulum（cross-cluster 选用 · 横云断山）
     - 读 _数据库/.cross_chapter_scan/cluster_tonal_registry.json（若存在）
     - 每 cluster 写入 tone_score = +1 (hot) / -1 (cold) / 0 (mixed)
       hot 词汇：怒/吼/杀/血/震/裂/扑/撕/吼/嘶
       cold 词汇：静/凉/冷/默/沉/淡/缓/慢/雪/寂
     - 末 N=5 cluster 同 tone streak ≥ 4 → TONAL_PENDULUM_FLAT advisory
     - 单 cluster 模式不触发此探针（pendulum 必须跨 cluster）

【作者档第一权威】
  quantitative.indirect_characterization_baseline = {
    mean, std,
    tonal_alternation_target,    # 1.0 = 严格冷热交替 · 0.0 = 自由
    allow_monotone               # bool · 戏剧独白题材豁免
  }

【北极星⑤】顾问非法官·全 advisory·env INDIRECT_CHARACTERIZATION_MODE
  INDIRECT_CHARACTERIZATION_THIN / TONAL_PENDULUM_FLAT 绝不进 HARD_GATE_CODES。

用法: python indirect_characterization_ratio_scanner.py <draft> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_THIN = "INDIRECT_CHARACTERIZATION_THIN"
ISSUE_PENDULUM = "TONAL_PENDULUM_FLAT"

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

# 直写主角动作动词（高确定性 · 主角在场动作）
DIRECT_VERBS = ("说", "做", "想", "走", "看", "听", "笑", "叹", "答",
                "问", "喊", "跑", "握", "举", "落")
# 配角观察/议论主角的视觉/听觉动词
GAZE_VERBS = ("视", "望", "盯", "瞧", "凝视", "注视", "扫", "瞥")
HEAR_VERBS = ("听见", "听到", "听说")

# 基调词典 · 高确定性 · 不取多义/中性词
HOT_WORDS = ("怒", "吼", "杀", "血", "震", "裂", "扑", "撕", "嘶", "灼",
             "烈", "焰", "斩", "崩")
COLD_WORDS = ("静", "凉", "冷", "默", "沉", "淡", "雪", "寂", "幽", "霜",
              "冰", "薄", "凝霜", "寂寥")

SCENE_SPLIT = re.compile(r"\n\s*[*◇◆━─=]{3,}\s*\n|\n\s*场景[:：]\s*[^\n]*\n|"
                         r"\n\s*第[一二三四五六七八九十0-9]+幕[^\n]*\n")
DIALOGUE_SPAN = re.compile(r'["“「『][^"”」』\n]{0,300}["”」』]')

MIN_CJK = 500
FLOOR_INDIRECT_RATIO = 0.10
PENDULUM_STREAK_MAX = 4   # 末 5 cluster 中连续同 tone ≥4 → flat


def _mode() -> str:
    m = (os.environ.get("INDIRECT_CHARACTERIZATION_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _load_protagonist_and_cast(project_root):
    """返回 (protag_name, side_chars_set)"""
    protag = None
    side = set()
    if not project_root:
        return protag, side
    p = Path(project_root) / "_数据库" / "人物卡.json"
    if not p.exists():
        return protag, side
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return protag, side
    for c in (obj.get("characters", []) if isinstance(obj, dict) else []):
        if not isinstance(c, dict):
            continue
        nm = c.get("name", "")
        if not nm:
            continue
        role = c.get("role", "")
        if role == "主角" and protag is None:
            protag = nm
        else:
            side.add(nm)
            for a in (c.get("aliases") or []):
                if a:
                    side.add(a)
    return protag, side


def _load_baseline(project_root):
    """读 quantitative.indirect_characterization_baseline。"""
    out = {"mean": None, "std": None,
           "tonal_alternation_target": None,
           "allow_monotone": False,
           "from_author_profile": False}
    if not project_root:
        return out
    for fname in ("作者风格_FINAL.json", "作者风格.json"):
        p = Path(project_root) / "_数据库" / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        q = data.get("quantitative") or {}
        ic = q.get("indirect_characterization_baseline") or {}
        if isinstance(ic, dict):
            for k in ("mean", "std", "tonal_alternation_target"):
                v = ic.get(k)
                if isinstance(v, (int, float)):
                    out[k] = float(v)
                    out["from_author_profile"] = True
            out["allow_monotone"] = bool(ic.get("allow_monotone", False))
        break
    return out


def _split_scenes(text: str):
    parts = SCENE_SPLIT.split(text)
    return [p.strip() for p in parts if p and p.strip()] or [text]


def _count_direct_signals(scene_text, protag):
    """主角名后 ±8 CJK 内含 DIRECT_VERBS → 直写一次"""
    if not protag:
        return 0
    count = 0
    for m in re.finditer(re.escape(protag), scene_text):
        window = scene_text[m.end(): m.end() + 8]
        if any(v in window for v in DIRECT_VERBS):
            count += 1
    return count


def _count_indirect_signals(scene_text, protag, side_chars):
    """两类侧写：
       (a) 配角名 + GAZE/HEAR 动词 + 主角名（±10 CJK 窗口）
       (b) 对话引号 + 引号内含主角名（配角议论主角）
       返回 indirect 命中次数
    """
    if not protag or not side_chars:
        return 0
    count = 0
    # (a) 配角观察主角
    for side in side_chars:
        for m in re.finditer(re.escape(side), scene_text):
            window = scene_text[m.end(): m.end() + 14]
            if protag not in window:
                continue
            if any(v in window for v in GAZE_VERBS):
                count += 1
            elif any(v in window for v in HEAR_VERBS):
                count += 1
    # (b) 对话引号内提主角名（配角议论）
    for dlg in DIALOGUE_SPAN.findall(scene_text):
        if protag in dlg:
            count += 1
    return count


def _tone_score(text: str):
    """单 cluster 草稿 tone_score = (hot_hits - cold_hits) / kCJK"""
    hot = sum(text.count(w) for w in HOT_WORDS)
    cold = sum(text.count(w) for w in COLD_WORDS)
    cjk = max(_cjk_count(text), 1)
    diff = (hot - cold) / cjk * 1000.0
    if diff > 1.0:
        tag = "hot"
    elif diff < -1.0:
        tag = "cold"
    else:
        tag = "mixed"
    return diff, tag, hot, cold


def _read_tonal_registry(project_root):
    if not project_root:
        return None
    p = (Path(project_root) / "_数据库" / ".cross_chapter_scan"
         / "cluster_tonal_registry.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_tonal_registry(project_root, cluster_id, tone_tag, tone_diff):
    if not project_root or not cluster_id:
        return False
    base = Path(project_root) / "_数据库" / ".cross_chapter_scan"
    base.mkdir(parents=True, exist_ok=True)
    p = base / "cluster_tonal_registry.json"
    reg = {"_schema": 1, "entries": []}
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
                reg = obj
        except (OSError, json.JSONDecodeError):
            pass
    # 去重再 append
    reg["entries"] = [e for e in reg["entries"]
                      if not (isinstance(e, dict) and e.get("cluster_id") == cluster_id)]
    reg["entries"].append({"cluster_id": cluster_id,
                           "tone_tag": tone_tag,
                           "tone_diff": round(tone_diff, 3)})
    try:
        p.write_text(json.dumps(reg, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return True
    except OSError:
        return False


def _check_pendulum(registry, allow_monotone):
    """末 N=5 cluster · 连续同 tone streak ≥ PENDULUM_STREAK_MAX → flat"""
    if allow_monotone or not registry:
        return None
    entries = registry.get("entries", []) if isinstance(registry, dict) else []
    if len(entries) < PENDULUM_STREAK_MAX:
        return None
    tail = entries[-5:]
    tones = [e.get("tone_tag", "") for e in tail if isinstance(e, dict)]
    if not tones:
        return None
    # 计算最长同 tone streak
    longest = 1
    cur = 1
    for a, b in zip(tones, tones[1:]):
        if a == b and a != "mixed":
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 1
    if longest >= PENDULUM_STREAK_MAX:
        return {"streak": longest, "tones": tones}
    return None


def scan(draft_path, project_root=None, cluster_id=None, write_registry=False) -> dict:
    mode = _mode()
    out = {"scanner": "indirect_characterization_ratio", "schema_version": "1.0",
           "mode": mode, "codes": [ISSUE_THIN, ISSUE_PENDULUM],
           "gate_level": "advisory", "violations": [],
           "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        raw = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(raw)
    cjk = _cjk_count(text)
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    protag, side_chars = _load_protagonist_and_cast(project_root)
    baseline = _load_baseline(project_root)
    out["protagonist"] = protag
    out["side_char_count"] = len(side_chars)
    out["author_baseline"] = {
        "mean": baseline["mean"], "std": baseline["std"],
        "allow_monotone": baseline["allow_monotone"],
        "from_author_profile": baseline["from_author_profile"],
    }

    if not protag:
        out["note"] = "无主角声明·跳过 indirect 探针（北极星②）"
        return out

    scenes = _split_scenes(text)
    direct_total = 0
    indirect_total = 0
    for sc in scenes:
        direct_total += _count_direct_signals(sc, protag)
        indirect_total += _count_indirect_signals(sc, protag, side_chars)
    denom = direct_total + indirect_total
    ratio = (indirect_total / denom) if denom else None
    out["metrics"] = {
        "direct_signals": direct_total,
        "indirect_signals": indirect_total,
        "indirect_ratio": round(ratio, 3) if ratio is not None else None,
        "scenes": len(scenes),
        "total_cjk": cjk,
    }

    # tone 计算 & 写注册表
    diff, tag, hot, cold = _tone_score(text)
    out["metrics"]["tone_diff"] = round(diff, 3)
    out["metrics"]["tone_tag"] = tag
    out["metrics"]["hot_hits"] = hot
    out["metrics"]["cold_hits"] = cold
    if write_registry and project_root and cluster_id:
        _write_tonal_registry(project_root, cluster_id, tag, diff)

    messages = []

    # 探针 ① · indirect ratio
    if ratio is not None and denom >= 5:
        m, s = baseline["mean"], baseline["std"]
        if m is not None and s and s > 1e-6:
            z = (ratio - m) / s
            out["metrics"]["indirect_ratio_z"] = round(z, 2)
            if z <= -2.0:
                messages.append(
                    f"烘云托月比 {round(ratio,3)} 偏离作者基线 {round(m,3)}±{round(s,3)} "
                    f"{round(z,1)}σ·配角侧写主角过少·全程直写")
        elif ratio < FLOOR_INDIRECT_RATIO:
            messages.append(
                f"烘云托月比 {round(ratio,3)} < 通用地板 {FLOOR_INDIRECT_RATIO}"
                f"·配角缺侧写·建议增加配角观察/议论主角桥段")

    thin_msg = "·".join(messages)

    # 探针 ② · tonal pendulum
    pendulum_violation = None
    if not baseline["allow_monotone"]:
        registry = _read_tonal_registry(project_root)
        # 把当前 cluster 临时追加（即使没写）便于即时检测
        if registry and cluster_id:
            entries = registry.get("entries", []) if isinstance(registry, dict) else []
            if not any(e.get("cluster_id") == cluster_id
                       for e in entries if isinstance(e, dict)):
                entries = entries + [{"cluster_id": cluster_id,
                                      "tone_tag": tag,
                                      "tone_diff": round(diff, 3)}]
                registry = {"entries": entries}
        pendulum_violation = _check_pendulum(registry, baseline["allow_monotone"])

    if thin_msg and mode == "active":
        out["violations"].append({
            "kind": "indirect_characterization_thin", "severity": "minor",
            "code": ISSUE_THIN, "message": thin_msg,
            "metrics": out["metrics"],
            "_doc": "金圣叹烘云托月·advisory·绝不 hard_gate",
        })
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = thin_msg
    elif thin_msg:
        print(f"[SHADOW] indirect_characterization: {thin_msg} — 不上报",
              file=sys.stderr)

    if pendulum_violation:
        pmsg = (f"横云断山·末 5 cluster 同 tone streak={pendulum_violation['streak']} "
                f"({'·'.join(pendulum_violation['tones'])})·建议下 cluster 反向调温")
        if mode == "active":
            out["violations"].append({
                "kind": "tonal_pendulum_flat", "severity": "minor",
                "code": ISSUE_PENDULUM, "message": pmsg,
                "streak_info": pendulum_violation,
                "_doc": "金圣叹横云断山·advisory·绝不 hard_gate",
            })
            if out["verdict"] == "PASS":
                out["verdict"] = "FAIL_MINOR"
            out["warning"] = (out["warning"] + "·" if out["warning"]
                              else "") + pmsg
        else:
            print(f"[SHADOW] indirect_characterization (pendulum): {pmsg} — 不上报",
                  file=sys.stderr)

    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(
        description="烘云托月+横云断山·金圣叹评点工艺·advisory·shadow")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--cluster-id", default=None)
    ap.add_argument("--write-registry", action="store_true",
                    help="把本 cluster tone 写入跨章注册表")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args, _ = ap.parse_known_args()
    rep = scan(args.draft_path, args.project, args.cluster_id,
               write_registry=args.write_registry)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(1 if rep.get("warning") else 0)


if __name__ == "__main__":
    main()
