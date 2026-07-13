#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cross_scene_voice_drift_scanner.py — cluster 内同角色跨场景 voice 漂移检测

本 scanner 是机械检测层，快速检出同角色在 cluster 不同 scene 的 voice 偏差
（句长 / catchphrase / banned_phrases）。

当 EMBED_BACKEND 非 hash 时，额外用嵌入 cosine 距离检测同角色跨场景 voice 漂移，
与统计指纹取 max。

输出 issue code: VOICE_DRIFT_CROSS_SCENE
gate_level: advisory（声音漂移 = 工艺类，writer 有理由可豁免）

用法：python cross_scene_voice_drift_scanner.py <project> <cluster_draft_path>
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict


def load(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def split_scenes(text: str, min_scene_len: int = 500) -> list[str]:
    """启发式切场景：按 \n---\n 或 \n\n\n 分隔；不够则按段数硬切。"""
    parts = re.split(r"\n---+\n|\n\n\n+", text)
    parts = [p.strip() for p in parts if p.strip() and len(p) >= min_scene_len]
    return parts if parts else [text]


# 对话正则需覆盖弯引号 U+201C/U+201D（项目正文实际用弯引号，非 ASCII "）——
# 遗漏会导致 scanner 抽不到对话、整体空转。（遵 feedback_dialogue_quote_unicode_distinction）
DIALOGUE_RE = re.compile('["“「『]([^"”」』\n]{1,300})["”」』]')
SPEAKER_PATTERN = re.compile(r'([一-鿿]{2,4})(?:说道?|道|问道?|答道?|笑道?|骂道?|喊道?|嘀咕|开口|说)')


def extract_dialogues_by_speaker(scene_text: str, known_aliases: dict[str, str]) -> dict:
    """从场景文本提取每个角色的对话样本。
    aliases: alias → canonical_name 映射"""
    by_speaker = defaultdict(list)
    lines = scene_text.split("\n")
    last_speaker = None
    for line in lines:
        # 先看本行有没有 speaker tag
        m = SPEAKER_PATTERN.search(line)
        if m:
            name = m.group(1)
            canonical = known_aliases.get(name, name)
            last_speaker = canonical
        # 抽对话
        for q in DIALOGUE_RE.findall(line):
            if last_speaker:
                by_speaker[last_speaker].append(q)
    return dict(by_speaker)


def compute_voice_metrics(dialogues: list[str]) -> dict:
    """对一组对话计算 voice 指纹。"""
    if not dialogues:
        return None
    total_chars = sum(len(d) for d in dialogues)
    return {
        "count": len(dialogues),
        "avg_len": total_chars / len(dialogues),
        "max_len": max(len(d) for d in dialogues),
        "has_ellipsis_ratio": sum(1 for d in dialogues if "…" in d or "..." in d) / len(dialogues),
        "has_question_ratio": sum(1 for d in dialogues if "?" in d or "？" in d) / len(dialogues),
    }


# ── L4·D4 voice 区分度 + D8 口癖一致性 ──────────
# 全 advisory · env VOICE_D4D8_MODE 默认 active（D4/D8 结果计入 warning/issues；shadow 时只记不判）
# 零依赖纯统计（不用 embedding · 避 hash backend 假语义）· 不含 D6情绪弧/D10情绪直陈
VOICE_TICS = ("啊", "呢", "吧", "嘛", "呗", "啦", "哈", "咯", "喔", "哦",
              "嗯", "唉", "哼", "咦", "嘞", "咧", "呐", "罢")


def _d4d8_mode() -> str:
    """VOICE_D4D8_MODE：默认 active（已用真实作者语料验证不会误判角色同质化·全advisory）· {shadow,active,off}· 非法回退 active。"""
    m = (os.environ.get("VOICE_D4D8_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


def _voice_fingerprint(dialogues: list[str]) -> dict | None:
    """角色对白聚合 voice 指纹（零依赖统计 · D4/D8 共用）。"""
    if not dialogues:
        return None
    n = len(dialogues)
    total_chars = sum(len(d) for d in dialogues) or 1
    tic_counts = {t: 0 for t in VOICE_TICS}
    for d in dialogues:
        for t in VOICE_TICS:
            tic_counts[t] += d.count(t)
    return {
        "avg_len": sum(len(d) for d in dialogues) / n,
        "ellipsis_ratio": sum(1 for d in dialogues if "…" in d or "..." in d) / n,
        "question_ratio": sum(1 for d in dialogues if "?" in d or "？" in d) / n,
        "exclaim_ratio": sum(1 for d in dialogues if "!" in d or "！" in d) / n,
        "tic_rate": {t: tic_counts[t] / total_chars * 1000 for t in VOICE_TICS},
        "_n": n,
    }


def _fingerprint_distance(a: dict, b: dict) -> float:
    """两角色 voice 指纹归一距离（0=同质 · 越大越区分）。"""
    dims = []
    m = (a["avg_len"] + b["avg_len"]) / 2 or 1
    dims.append(min(abs(a["avg_len"] - b["avg_len"]) / m, 1.0))
    for k in ("ellipsis_ratio", "question_ratio", "exclaim_ratio"):
        dims.append(abs(a[k] - b[k]))
    tic_l1 = sum(abs(a["tic_rate"][t] - b["tic_rate"][t]) for t in VOICE_TICS)
    dims.append(min(tic_l1 / 10.0, 1.0))
    return sum(dims) / len(dims)


def compute_d4_distinctiveness(char_all_dialogues: dict) -> dict:
    """D4：角色间 voice 区分度（两两距离均值过低=角色说话同质化 · advisory）。"""
    chars = [(name, _voice_fingerprint(qs)) for name, qs in char_all_dialogues.items()]
    chars = [(n, f) for n, f in chars if f and f["_n"] >= 3]
    if len(chars) < 2:
        return {"applicable": False, "reason": "少于2个有足够对白(≥3句)的角色"}
    pairs = []
    for i in range(len(chars)):
        for j in range(i + 1, len(chars)):
            d = _fingerprint_distance(chars[i][1], chars[j][1])
            pairs.append((chars[i][0], chars[j][0], round(d, 3)))
    dists = [p[2] for p in pairs]
    mean_dist = sum(dists) / len(dists)
    low_pairs = [{"a": a, "b": b, "dist": d} for a, b, d in pairs if d < 0.08]
    return {
        "applicable": True,
        "char_count": len(chars),
        "mean_pairwise_distance": round(mean_dist, 3),
        "low_distinctiveness_pairs": low_pairs[:5],
        "low_distinctiveness": mean_dist < 0.10,
    }


def compute_d8_tic_consistency(char_scene_tics: dict) -> dict:
    """D8：同角色跨场景口癖一致性（某场景显著口癖另一场景几乎消失=不一致 · advisory）。"""
    issues = []
    for name, scene_tics in char_scene_tics.items():
        valid = [(idx, tr) for idx, tr in scene_tics if tr]
        if len(valid) < 2:
            continue
        for t in VOICE_TICS:
            rates = [tr.get(t, 0.0) for _, tr in valid]
            mx, mn = max(rates), min(rates)
            if mx >= 2.0 and mn < 0.3:
                issues.append({"character": name, "tic": t,
                               "max_rate": round(mx, 2), "min_rate": round(mn, 2)})
    return {"inconsistent_tics": issues[:8], "count": len(issues)}


# ── P2·VOICE_COLLAPSE_CROSS_CHAR 跨角色 voice 坍缩 ──
# 「所有人说话一个味」= 声音坍缩 · 弱模型最典型盲点（D4 区分度已算两两距离·这里把
# 它升成一条带 code 的『待裁决项』供 audit_hub 透传给写作 agent）。
# 复用 compute_d4_distinctiveness 已抽好的逐角色句长/catchphrase 桶（机械特征·非 embedding）。
# 全 advisory · 同身份角色（兄弟/同帮派/群演）本就相似 → writer 有理由可豁免 ·
# 绝不进 HARD_GATE_CODES（_gate_level_for 不在白名单即降档）。
VOICE_COLLAPSE_CROSS_CHAR_CODE = "VOICE_COLLAPSE_CROSS_CHAR"


def compute_cross_char_voice_collapse(d4: dict | None) -> dict | None:
    """把 D4 两两距离结果蒸成一条 VOICE_COLLAPSE_CROSS_CHAR 待裁决项（声音坍缩）。

    入参直接吃 compute_d4_distinctiveness 的产物（零重复计算·共用机械桶）。
    判定：整体两两均距过低（mean<0.10·所有角色趋同）→ collapsed。
    返回 None 表示不适用（<2 个角色）或未坍缩（无 issue）。
    """
    if not d4 or not d4.get("applicable"):
        return None
    mean_dist = d4.get("mean_pairwise_distance")
    low_pairs = d4.get("low_distinctiveness_pairs", []) or []
    collapsed = bool(d4.get("low_distinctiveness"))
    if not collapsed:
        return None
    pair_hint = ""
    if low_pairs:
        p0 = low_pairs[0]
        pair_hint = f"（如「{p0.get('a','?')}」≈「{p0.get('b','?')}」距离{p0.get('dist','?')}）"
    return {
        "issue_code": VOICE_COLLAPSE_CROSS_CHAR_CODE,
        "gate_level": "advisory",
        "char_count": d4.get("char_count"),
        "mean_pairwise_distance": mean_dist,
        "collapsed_pairs": low_pairs[:5],
        "msg": (
            f"跨角色 voice 坍缩：{d4.get('char_count','?')} 个角色两两均距仅 {mean_dist}"
            f"（所有人说话一个味）{pair_hint}"
            "·若同身份角色（兄弟/同帮派/群演）本就相似可豁免"
        ),
    }


# ── embedding cosine 距离检测 voice 漂移 ──────────
def _has_semantic_embedding() -> bool:
    """真语义嵌入后端就绪（非 hash）才启 embedding voice 漂移。

    🔴 用 embedding_store.embedding_method() 作权威判定（单一真理源）·不自己重判 env：
      · EMBED_BACKEND=mstyle/local/ruoyu_style 但包/venv/模型缺 → embedding_store 已回退 hash →
        这里如实读到 hash → False（绝不拿 hash 假语义袋冒充真 voice 漂移·呼应 C1 风格余弦护栏）。
    """
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from embedding_store import embedding_method
        return not embedding_method().startswith("hash")
    except Exception:
        return False


# 🔬 待金标准校准：跨场景 embedding cosine 距离 > 此 = voice 漂移。默认 0.3（沿用
# embedding_store.compute_character_drift 章级 alert 阈值）·env CROSS_SCENE_EMBED_DRIFT_FLOOR 可覆盖。
# author 风格嵌入模型会把同作者文本映射得很近（同角色跨场景对白 cosine 距离天然偏小），
# 故 0.3 floor 对 author embedding 偏保守（宁可漏报不误报·北极星⑤）；真正校准需作者
# 基线 z-band（per-backend·待金标准）。当前默认保守·env 可临时下调验证。
DEFAULT_EMBED_DRIFT_FLOOR = 0.3


def _embed_drift_floor() -> float:
    """env CROSS_SCENE_EMBED_DRIFT_FLOOR 覆盖 > 默认 0.3。非法值回退默认。"""
    raw = os.environ.get("CROSS_SCENE_EMBED_DRIFT_FLOOR")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_EMBED_DRIFT_FLOOR


def _embedding_voice_drift(char_scene_dialogues: dict[str, list[tuple[int, list[str]]]]) -> list[dict]:
    """用嵌入 cosine 距离检测同角色跨场景 voice 漂移（distance > floor → advisory issue）。"""
    if not _has_semantic_embedding():
        return []
    try:
        from embedding_store import compute_embedding
    except ImportError:
        return []
    floor = _embed_drift_floor()
    issues = []
    for name, scene_entries in char_scene_dialogues.items():
        if len(scene_entries) < 2:
            continue
        scene_embeddings = []
        for scene_idx, dialogues in scene_entries:
            if not dialogues:
                continue
            combined = "\n".join(dialogues[:20])
            emb = compute_embedding(combined)
            if emb is not None:
                scene_embeddings.append((scene_idx, emb))
        if len(scene_embeddings) < 2:
            continue
        for i in range(len(scene_embeddings)):
            for j in range(i + 1, len(scene_embeddings)):
                s1_idx, e1 = scene_embeddings[i]
                s2_idx, e2 = scene_embeddings[j]
                if len(e1) != len(e2):
                    continue
                dot = sum(a * b for a, b in zip(e1, e2))
                n1 = sum(a * a for a in e1) ** 0.5
                n2 = sum(b * b for b in e2) ** 0.5
                if n1 == 0 or n2 == 0:
                    continue
                cos_dist = 1.0 - dot / (n1 * n2)
                if cos_dist > floor:
                    issues.append({
                        "character": name,
                        "scene_a": s1_idx,
                        "scene_b": s2_idx,
                        "embedding_distance": round(cos_dist, 3),
                        "embedding_drift_floor": floor,
                        "type": "embedding_voice_drift",
                    })
    return issues


def scan(project_root: Path, draft_path: Path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")
    scenes = split_scenes(text)

    # 加载人物卡 aliases
    cards = load(project_root / "_数据库" / "人物卡.json").get("characters", [])
    aliases = {}
    for c in cards:
        name = c.get("name", "")
        aliases[name] = name
        for a in c.get("aliases", []) or []:
            aliases[a] = name

    # 每个 scene 提取每个角色的对话
    scene_metrics = []  # [{scene_idx, by_speaker: {name: metrics}}]
    char_all_dialogues = defaultdict(list)   # D4: 角色 → 跨场景全部对白
    char_scene_tics = defaultdict(list)      # D8: 角色 → [(scene_idx, tic_rate)]
    for i, scene in enumerate(scenes):
        sd = extract_dialogues_by_speaker(scene, aliases)
        scene_metrics.append({
            "scene_idx": i,
            "by_speaker": {name: compute_voice_metrics(qs) for name, qs in sd.items() if qs},
        })
        for name, qs in sd.items():
            if qs:
                char_all_dialogues[name].extend(qs)
                fp = _voice_fingerprint(qs)
                char_scene_tics[name].append((i, fp["tic_rate"] if fp else {}))

    # 收集跨场景角色对话供嵌入比较
    char_scene_dialogues: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
    for i, scene in enumerate(scenes):
        sd = extract_dialogues_by_speaker(scene, aliases)
        for name, qs in sd.items():
            if qs:
                char_scene_dialogues[name].append((i, qs))

    # 跨场景检测每个角色的 voice 漂移
    drift_issues = []
    # 收集每个角色在所有场景的 metrics
    by_char = defaultdict(list)  # name → [(scene_idx, metrics)]
    for sm in scene_metrics:
        for name, met in sm["by_speaker"].items():
            if met:
                by_char[name].append((sm["scene_idx"], met))

    for name, entries in by_char.items():
        if len(entries) < 2:
            continue  # 出场 <2 场景，无法对比
        # 比较 avg_len 偏差（>50% 偏差视为漂移）
        lens = [e[1]["avg_len"] for e in entries]
        if max(lens) > 0:
            mean_len = sum(lens) / len(lens)
            for scene_idx, met in entries:
                deviation = abs(met["avg_len"] - mean_len) / max(mean_len, 1)
                if deviation > 0.5 and met["count"] >= 3:
                    drift_issues.append({
                        "character": name,
                        "scene_idx": scene_idx,
                        "avg_len_this_scene": round(met["avg_len"], 1),
                        "avg_len_cluster_mean": round(mean_len, 1),
                        "deviation_pct": round(deviation * 100, 1),
                        "sample_count": met["count"],
                        "type": "avg_dialogue_length_drift",
                    })

    # 嵌入 voice 漂移（与 5 维统计取 max=任一信号触发即报·合并入 drift_issues）
    # 默认安全：embedding 后端/桥任何失败 → 降级走统计（不崩·零回归·呼应 nn_vad_bridge 默认安全铁律）。
    embed_active = _has_semantic_embedding()
    embedding_drift = []
    if embed_active:
        try:
            embedding_drift = _embedding_voice_drift(dict(char_scene_dialogues))
        except Exception as e:  # noqa: BLE001 桥/编码任何异常 → 降级统计（不影响现有启发式）
            print(f"[cross_scene_voice_drift] embedding 路径降级: {str(e)[:120]}", file=sys.stderr)
            embedding_drift = []
    drift_issues.extend(embedding_drift)

    # D4/D8 · env VOICE_D4D8_MODE 默认 active（只挂字段不改 warning/exit 时为 shadow）
    mode = _d4d8_mode()
    d4 = compute_d4_distinctiveness(char_all_dialogues) if mode != "off" else None
    d8 = compute_d8_tic_consistency(char_scene_tics) if mode != "off" else None
    # P2：跨角色 voice 坍缩（VOICE_COLLAPSE_CROSS_CHAR）· 复用 d4 桶 · 同 mode 门控
    cross_char_collapse = (
        compute_cross_char_voice_collapse(d4) if mode != "off" else None
    )
    # warning 文案随是否含 embedding 漂移自适应（纯统计仍只述句长·零回归）
    _has_embed_drift = any(it.get("type") == "embedding_voice_drift" for it in drift_issues)
    _drift_desc = "句长偏差 >50% / embedding 距离" if _has_embed_drift else "句长偏差 >50%"
    base_warning = (
        f"⚠️ {len(drift_issues)} 处跨场景 voice 漂移嫌疑（{_drift_desc}）"
        if drift_issues else None
    )
    d4d8_warning = None
    if mode == "active":   # 仅 active 把 D4/D8 升进 advisory warning（shadow 只挂字段不改判决）
        bits = []
        if cross_char_collapse:   # P2：坍缩优先（更具体·带 code）
            bits.append(f"跨角色voice坍缩(两两均距{cross_char_collapse['mean_pairwise_distance']})")
        elif d4 and d4.get("low_distinctiveness"):
            bits.append(f"角色voice区分度低(两两均距{d4['mean_pairwise_distance']})")
        elif d4 and d4.get("low_distinctiveness_pairs"):
            bits.append(f"{len(d4['low_distinctiveness_pairs'])}对角色说话同质化")
        if d8 and d8.get("count"):
            bits.append(f"{d8['count']}处角色口癖跨场景不一致")
        if bits:
            d4d8_warning = "；".join(bits)
    final_warning = "；".join([w for w in (base_warning, d4d8_warning) if w]) or None

    # 顶层 issues[]：给 audit_hub / 任意消费方一个带 code 的可解析面（P2 坍缩项·
    # active 才升 issue·shadow/off 只挂字段不产 issue → 回归 0）。
    issues = []
    if mode == "active" and cross_char_collapse:
        issues.append({
            "code": cross_char_collapse["issue_code"],
            "gate_level": "advisory",      # 绝不 hard_gate（同身份角色本就相似）
            "severity": "warning",
            "msg": cross_char_collapse["msg"],
            "count": len(cross_char_collapse.get("collapsed_pairs", [])),
            "items": cross_char_collapse.get("collapsed_pairs", []),
        })

    return {
        "schema_version": "1.2",
        "scanner": "cross_scene_voice_drift_scanner",
        "gate_level": "advisory",
        "cluster_mode": True,
        "scenes_scanned": len(scenes),
        "characters_with_voice_sample": len(by_char),
        "drift_issues_count": len(drift_issues),
        "drift_issues": drift_issues[:10],
        "d4_voice_distinctiveness": d4,
        "d8_tic_consistency": d8,
        "cross_char_voice_collapse": cross_char_collapse,
        "d4d8_mode": mode,
        "issues": issues,
        # embedding voice 漂移可观测面（默认 hash → active False·零回归）
        "embed_backend_active": embed_active,
        "embedding_drift_count": len(embedding_drift),
        "warning": final_warning,
        "severity": "warning" if final_warning else "info",
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    draft = Path(args[1]).resolve()
    report = scan(project, draft)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)  # 与 locked_fact/pov/foreshadowing_handoff 兄弟 scanner 一致：数据缺失≠干净通过
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
