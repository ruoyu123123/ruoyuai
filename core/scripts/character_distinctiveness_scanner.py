#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""character_distinctiveness_scanner.py — 跨角色 idiolect Gini 检测（advisory · cluster · 2026-06-20）

【缺口】R9 联网调研：character idiolect / inter-character distinctiveness（基于 Burrows-Δ /
Craig-Zeta 计量风格学）此前全系统：
  · R3/R4 cross_scene_voice_drift 查【同角色跨场景 voice drift】
  · 【跨角色 voice 区分度 Gini 零覆盖】——所有角色都"听起来一样"=voice collapse 问题
  没有任何 scanner 测。LLM 默认把所有角色写成统一旁白腔=对话失去人物感。
  本 scanner 用字符 3-gram bootstrap 算每对角色（A,B）的 idiolect distance·聚合成 Gini。

【做法 · 确定性纯统计（不依赖 LLM）】：
  1. 抽对话归属：扫描"{角色}{说/道/喊/低声/笑道}：「…」"或紧邻 dialogue 之前/后的归属名词·
     用 cluster manifest.dialogue_attribution（如有）或正则启发式（专名+说/道/问 ±20 字回扫）。
  2. 每角色累积 dialogue text·token 化为字符 3-gram 直方图。
  3. 角色 ≥ 2 个且每个角色 dialogue 字符 ≥ MIN_CHAR_TOKENS（80 CJK）才判（样本足）。
  4. 每对（A,B）计 character 3-gram cosine distance·bootstrap 重抽样 N=20 取 mean。
     转 distinctiveness_score = 1 - cosine_similarity（0=完全一致·1=完全不同）。
  5. 聚合 Gini 系数：取所有 pair distance 列表算 Gini（0=所有对等·1=极端集中）。
     【注：这里 Gini 度量的是 pairwise distance 分布的不均匀性·distance 越大且分布越均匀
      ＝声音区分度越好】。
     更直接：inter_character_voice_gini 取自 1 - mean(pair distances)·越大表示越塌缩。
  6. 作者档 character_voice_gini_baseline 第一权威·无→通用兜底 mean_distance < 0.25 报。

【北极星② / ⑤ 顾问非法官】配角戏分少、群像题材 baseline 不同·永远 advisory，
  code INTER_CHARACTER_VOICE_COLLAPSE **绝不进 HARD_GATE_CODES**。
  env CHARACTER_DISTINCTIVENESS_MODE: off / shadow(默认) / active。

用法：python character_distinctiveness_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ISSUE_CODE = "INTER_CHARACTER_VOICE_COLLAPSE"   # ⚠️ advisory · 绝不进 HARD_GATE_CODES
# 🔴 2026-06-29 NN风格声纹集成 — 声纹 NN 后端（env CHARACTER_VOICE_EMBED=1）的升级版 code。
# 出场角色对话两两 NN cosine 互距「撞声」→ CROSS_CHARACTER_VOICE_COLLISION（advisory）。
# = INTER_CHARACTER_VOICE_COLLAPSE 的声纹 NN 版（char-3gram 字面相似 → 真风格声纹相似）。
# ⚠️ advisory · 绝不进 HARD_GATE_CODES（北极星②/⑤ 顾问非法官）。
CROSS_CHARACTER_VOICE_COLLISION_CODE = "CROSS_CHARACTER_VOICE_COLLISION"

# 对话归属正则（专名 ± 1-2 字 + 引导词 + 冒号/引号）
# 启发式抓 "X说：" / "X道：" / "X笑道：" / "X低声道："
# 长 verb 优先（按字符长度降序），name 非贪婪，避免「李四怒」吞 verb 第一字
_SAY_VERBS = (r"低声道|冷笑道|沉吟道|轻声道|说道|喊道|问道|笑道|喝道|怒道|叹道|"
              r"低声|冷笑|沉吟|轻声|说|道|喊|问|笑|喝|怒|叹")
_ATTR_RE = re.compile(
    r"([一-鿿]{1,4}?)(?:" + _SAY_VERBS + r")[：:]\s*[\"“「]([^\"”」]+)[\"”」]"
)
# 反向：「…」+ X 说 形式
_ATTR_POST_RE = re.compile(
    r"[\"“「]([^\"”」]+)[\"”」]\s*([一-鿿]{1,4}?)(?:" + _SAY_VERBS + r")"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 500
MIN_CHAR_TOKENS = 40         # 每角色 dialogue 至少 40 CJK 才入统计（样本足）
MIN_CHARACTERS = 2           # 至少 2 个角色才能算 pair
BOOTSTRAP_N = 20             # bootstrap 重抽样次数
DEFAULT_MEAN_DISTANCE_FLOOR = 0.25  # mean pair distance < 此 → collapse（char-3gram 标度）
# 🔴 2026-06-29 NN风格声纹集成 — NN 声纹标度的 collapse floor（与 char-3gram 标度不同）。
# 诚实：char 声纹模型 cos_same 0.93-0.97 偏塌缩（绝对值中等·AUC 0.653 超基线 8 点但非高判别），
# 故 NN 标度的「异角色距离」天然偏小 → floor 取保守值（默认只在角色 NN 向量几乎重合才报·
# 不矫枉过正·北极星⑤）。作者档 nn_mean_distance_min 第一权威·env CHARACTER_VOICE_EMBED_FLOOR 可覆盖。
DEFAULT_NN_MEAN_DISTANCE_FLOOR = 0.05


def _mode() -> str:
    m = (os.environ.get("CHARACTER_DISTINCTIVENESS_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_baseline(project_root):
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    for fname in ("作者风格.json", "voice_pack.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get("character_voice_gini_baseline")
        if isinstance(v, dict):
            return v
    return None


def extract_dialogues_by_character(text: str) -> dict:
    """抽取角色 → dialogue text 列表。返回 {char_name: [text, ...]}。

    位置去重：同一引号内容只归一名角色（按 char_start 排序去重重叠区间），
    避免 forward + post 两条规则把同一段 dialogue 双计入。
    """
    text = _strip_changes(text)
    candidates = []  # (content_start, content_end, name, content)
    for m in _ATTR_RE.finditer(text):
        candidates.append((m.start(2), m.end(2), m.group(1), m.group(2)))
    for m in _ATTR_POST_RE.finditer(text):
        candidates.append((m.start(1), m.end(1), m.group(2), m.group(1)))
    # 按 start 排序·后到的若 start 落在已收 span 内则丢弃
    candidates.sort(key=lambda x: x[0])
    out = defaultdict(list)
    claimed = []   # list of (start, end)
    for s, e, name, content in candidates:
        if any(cs <= s < ce for cs, ce in claimed):
            continue
        out[name].append(content)
        claimed.append((s, e))
    return dict(out)


def _char_3gram_counter(s: str) -> Counter:
    """字符 3-gram 计数（仅 CJK 与基础标点）。"""
    s = re.sub(r"[\s]+", "", s)
    if len(s) < 3:
        return Counter()
    return Counter(s[i:i + 3] for i in range(len(s) - 2))


def _cosine_sim(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _bootstrap_distance(a_text: str, b_text: str, n: int = BOOTSTRAP_N,
                        seed: int = 42) -> float:
    """字符 3-gram bootstrap cosine distance（mean over N resamples）。

    短样本（< 300 grams）退直接 cosine 算（bootstrap 在小样本上方差>信号）；
    长样本走 3-gram 有放回 bootstrap·保留分布特征·结果取均值。
    """
    a_grams = [a_text[i:i + 3] for i in range(len(a_text) - 2)]
    b_grams = [b_text[i:i + 3] for i in range(len(b_text) - 2)]
    # 短样本：直接全量算（bootstrap 方差太大不可靠）
    if len(a_grams) < 300 or len(b_grams) < 300:
        ca = _char_3gram_counter(a_text)
        cb = _char_3gram_counter(b_text)
        return round(1.0 - _cosine_sim(ca, cb), 4)
    rng = random.Random(seed)
    sample_size = min(len(a_grams), len(b_grams), 500)
    distances = []
    for _ in range(n):
        sa = [a_grams[rng.randrange(len(a_grams))] for _ in range(sample_size)]
        sb = [b_grams[rng.randrange(len(b_grams))] for _ in range(sample_size)]
        ca = Counter(sa)
        cb = Counter(sb)
        distances.append(1.0 - _cosine_sim(ca, cb))
    return round(sum(distances) / len(distances), 4)


def _nn_pair_distances(bag: dict):
    """🔴 2026-06-29 NN风格声纹集成 — 角色声纹 NN 两两 cosine 互距（env CHARACTER_VOICE_EMBED=1）。

    一次 batch 编码所有角色累积台词（走 embedding_store venv subprocess 桥·**character** 模型）
    → 两两 distance = 1 - cosine。返回 (pairs, "ruoyu_style_char_nn") 或 **None**。

    🔴 默认安全：env 未开 / venv / 模型 / torch 缺 / 桥失败 / 条数不符 → 返回 None
       （调用方兜底 char-3gram bootstrap·绝不崩·零回归）。
    """
    if os.environ.get("CHARACTER_VOICE_EMBED") != "1":
        return None
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from embedding_store import ruoyu_style_encode_batch, cosine_similarity
    except Exception:
        return None
    names = sorted(bag.keys())
    try:
        embs = ruoyu_style_encode_batch([bag[n] for n in names], model="character")
    except Exception:
        return None
    if not embs or len(embs) != len(names):
        return None
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            d = round(1.0 - cosine_similarity(embs[i], embs[j]), 4)
            pairs.append({"a": names[i], "b": names[j], "distance": d})
    return pairs, "ruoyu_style_char_nn"


def _gini(values) -> float:
    """Gini 系数（0=完全均匀·1=完全集中）。values 必须非负。"""
    vals = sorted([float(v) for v in values if v is not None])
    n = len(vals)
    if n == 0:
        return 0.0
    cumulative = 0.0
    total = sum(vals)
    if total == 0:
        return 0.0
    for i, v in enumerate(vals, start=1):
        cumulative += (2 * i - n - 1) * v
    return round(cumulative / (n * total), 4)


def compute_distinctiveness(text: str) -> dict:
    """计算每角色 dialogue 后 pairwise distance + Gini。

    返回 {char_count, per_character: {name: total_chars},
          pair_distances: [{a, b, distance}],
          mean_pair_distance, inter_character_voice_gini}。
    """
    bag = extract_dialogues_by_character(text)
    # 过滤样本不足角色
    bag = {n: " ".join(ds) for n, ds in bag.items()}
    bag = {n: t for n, t in bag.items() if _cjk_count(t) >= MIN_CHAR_TOKENS}
    per_char = {n: _cjk_count(t) for n, t in bag.items()}
    if len(bag) < MIN_CHARACTERS:
        return {
            "char_count": len(bag),
            "per_character": per_char,
            "pair_distances": [],
            "mean_pair_distance": None,
            "inter_character_voice_gini": None,
            "distance_method": None,
            "note": "角色样本不足·不算",
        }
    names = sorted(bag.keys())
    # 🔴 2026-06-29 NN风格声纹集成 — CHARACTER_VOICE_EMBED=1 时优先声纹 NN 互距·桥失败兜底 char-3gram。
    nn = _nn_pair_distances(bag)
    if nn is not None:
        pairs, distance_method = nn
    else:
        pairs = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                d = _bootstrap_distance(bag[a], bag[b])
                pairs.append({"a": a, "b": b, "distance": d})
        distance_method = "char_3gram_bootstrap"
    distances = [p["distance"] for p in pairs]
    mean_d = round(sum(distances) / len(distances), 4) if distances else 0.0
    gini = _gini(distances)
    return {
        "char_count": len(bag),
        "per_character": per_char,
        "pair_distances": pairs,
        "mean_pair_distance": mean_d,
        "inter_character_voice_gini": gini,
        "distance_method": distance_method,
    }


def scan(draft_path, project_root=None) -> dict:
    """跨角色 distinctiveness Gini 检测。永远 advisory（北极星②/⑤）。"""
    mode = _mode()
    out = {"scanner": "character_distinctiveness", "schema_version": "1.0", "mode": mode,
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

    result = compute_distinctiveness(draft)
    out["char_count"] = result["char_count"]
    out["per_character"] = result["per_character"]
    out["pair_distances"] = result["pair_distances"][:10]
    out["mean_pair_distance"] = result["mean_pair_distance"]
    out["inter_character_voice_gini"] = result["inter_character_voice_gini"]
    # 🔴 2026-06-29 NN风格声纹集成 — NN 后端在用时升级 code → CROSS_CHARACTER_VOICE_COLLISION。
    distance_method = result.get("distance_method")
    out["distance_method"] = distance_method
    nn_mode = distance_method == "ruoyu_style_char_nn"
    out["code"] = CROSS_CHARACTER_VOICE_COLLISION_CODE if nn_mode else ISSUE_CODE
    if "note" in result:
        out["note"] = result["note"]
        return out

    baseline = _read_baseline(project_root)
    if nn_mode:
        # NN 声纹标度 floor：作者档 nn_mean_distance_min 第一权威 > env > 保守默认。
        if baseline and baseline.get("nn_mean_distance_min") is not None:
            floor = float(baseline["nn_mean_distance_min"])
            baseline_source = "author_profile_nn"
        else:
            env_floor = os.environ.get("CHARACTER_VOICE_EMBED_FLOOR")
            floor = float(env_floor) if env_floor else DEFAULT_NN_MEAN_DISTANCE_FLOOR
            baseline_source = "env" if env_floor else "default_nn_fallback"
    elif baseline:
        floor = float(baseline.get("mean_distance_min", DEFAULT_MEAN_DISTANCE_FLOOR))
        baseline_source = "author_profile"
    else:
        floor = DEFAULT_MEAN_DISTANCE_FLOOR
        baseline_source = "default_fallback"
    out["baseline_source"] = baseline_source
    out["thresholds"] = {"mean_distance_floor": floor}

    mean_d = result["mean_pair_distance"]
    over = mean_d is not None and mean_d < floor
    if over:
        method_note = ("声纹 NN 互距(ruoyu_style char 模型)" if nn_mode
                       else "字符 3-gram bootstrap(Burrows-Δ/Craig-Zeta stylometry)")
        msg = (f"跨角色 voice 区分度过低：mean pair distance {mean_d} < {floor}·"
               f"所有角色听起来过近(idiolect collapse·{method_note})·"
               f"建议为每角色加 distinctiveness_anchors(口癖/句末助词/语速)·"
               f"群像题材尤需角色声纹分离")
        if mode == "active":
            out["violations"].append({
                # NN 模式用新 kind/code·char-3gram 模式保持原 kind（兼容现有消费方）。
                "kind": ("cross_character_voice_collision" if nn_mode
                         else "inter_character_voice_collapse"),
                "code": out["code"],
                "gate_level": "advisory",
                "severity": "minor",
                "message": msg,
                "mean_pair_distance": mean_d,
                "inter_character_voice_gini": result["inter_character_voice_gini"],
                "char_count": result["char_count"],
                "distance_method": distance_method,
                "sample_pairs": result["pair_distances"][:4],
                "_doc": "声纹 NN/字符 3-gram·配角戏分少/独白驱动/同身份角色作者档可豁免→advisory"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] character_distinctiveness: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="跨角色 distinctiveness Gini 检测（advisory）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 character_voice_gini_baseline")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
