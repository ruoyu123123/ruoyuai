#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN角色网络/共指集成
"""character_network_extractor.py — 角色关系网络提取器（cluster 草稿 → 共现/对话关系图）。

【目标】从 cluster 草稿中自动提取角色共现/对话关系网络，辅助 archivist agent。

【后端降级链】
  · Renard 管线（pip install renard）→ 增强提取（图论 + NER + coref）
  · 纯规则实现（jieba + 正则 + 统计）→ 已够用·默认
  · 极简兜底（正则 + known_characters 列表）→ jieba 也没装时

【默认安全铁律（北极星⑤·零回归）】
  · RUOYU_CHARACTER_NETWORK != "1"（默认 off·门控未开）→ 返回空结果
  · Renard / jieba 缺 → 降级规则·绝不崩
  · 任何异常 → 返回空结果·不崩主流水线

Env 门控: RUOYU_CHARACTER_NETWORK（默认 off）

用法：python character_network_extractor.py <draft_path> [--project <root>] [--characters 张三,李四]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ── 常量 ────────────────────────────────────────────────────

# 对话归属正则（复用 character_distinctiveness_scanner 的模式）
_SAY_VERBS = (r"低声道|冷笑道|沉吟道|轻声道|说道|喊道|问道|笑道|喝道|怒道|叹道|"
              r"低声|冷笑|沉吟|轻声|说|道|喊|问|笑|喝|怒|叹")
# 前向归属：X说："..." / X道：「...」
_ATTR_FWD_RE = re.compile(
    r"([一-鿿]{1,4}?)(?:" + _SAY_VERBS + r")[：:]\s*[“”\"「]([^”\"」]*?)[”\"」]"
)
# 后向归属：「...」X说
_ATTR_POST_RE = re.compile(
    r"[“\"「]([^”\"」]*?)[”\"」]\s*([一-鿿]{1,4}?)(?:" + _SAY_VERBS + r")"
)
# 纯引号对话段（不带归属）
_DIALOGUE_RE = re.compile(r"[“\"「]([^”\"」]{2,})[”\"」]")
# 场景分隔符（连续空行 / 场景标记）
_SCENE_SPLIT_RE = re.compile(r"\n\s*\n\s*\n|\n\s*[—─]{3,}\s*\n|\n\s*\*{3,}\s*\n")
# changes 分隔符（去掉 changes 元数据）
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _empty_result(source: str = "disabled") -> dict:
    """门控关闭/异常时返回的空结果。"""
    return {
        "characters": [],
        "edges": [],
        "scene_participation": {},
        "centrality": {},
        "source": source,
    }


def enabled() -> bool:
    """门控总开关：RUOYU_CHARACTER_NETWORK=1。"""
    return os.environ.get("RUOYU_CHARACTER_NETWORK") == "1"


# ── 角色识别 ────────────────────────────────────────────────

def _load_known_characters(project_dir: str | None) -> list[str]:
    """从项目 _数据库/人物.json 或 人物卡.json 读取已知角色列表。"""
    if not project_dir:
        return []
    db = Path(project_dir) / "_数据库"
    for fname in ("人物.json", "人物卡.json"):
        p = db / fname
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        chars = obj.get("characters", [])
        names = []
        for c in chars:
            if isinstance(c, dict):
                name = c.get("name", "")
                if name:
                    names.append(name)
                for alias in c.get("name_aliases", []):
                    if alias:
                        names.append(alias)
            elif isinstance(c, str) and c:
                names.append(c)
        if names:
            return names
    return []


def _find_characters_regex(text: str, known: list[str]) -> list[str]:
    """用已知角色列表做正则匹配，返回在文本中实际出现的角色（按首次出现排序）。"""
    found = []
    for name in known:
        if name and re.search(re.escape(name), text):
            if name not in found:
                found.append(name)
    return found


def _find_characters_jieba(text: str) -> list[str]:
    """用 jieba 分词 + 人名词性标注 (nr) 提取人名。jieba 不可用 → 空列表。"""
    try:
        import jieba
        import jieba.posseg as pseg
    except ImportError:
        return []
    names = []
    for word, flag in pseg.cut(text[:50000]):  # 限制长度避免过慢
        if flag == "nr" and len(word) >= 2 and word not in names:
            names.append(word)
    return names


def _identify_characters(text: str, known_characters: list[str] | None,
                         project_dir: str | None) -> list[str]:
    """角色识别策略：known_characters 列表 > 项目数据库 > jieba NER。"""
    # 1. 显式传入
    if known_characters:
        found = _find_characters_regex(text, known_characters)
        if found:
            return found
    # 2. 从项目数据库读取
    db_chars = _load_known_characters(project_dir)
    if db_chars:
        found = _find_characters_regex(text, db_chars)
        if found:
            return found
    # 3. jieba NER 兜底
    return _find_characters_jieba(text)


# ── 场景分割 ────────────────────────────────────────────────

def _split_scenes(text: str) -> list[str]:
    """按连续空行 / 场景标记分割。至少返回 1 个场景。"""
    scenes = [s.strip() for s in _SCENE_SPLIT_RE.split(text) if s.strip()]
    return scenes if scenes else [text]


# ── 对话归属 ────────────────────────────────────────────────

def _attribute_dialogue(text: str, characters: list[str]) -> list[dict]:
    """提取对话段 + 归属说话者。

    归属策略：
    1. 前向/后向正则匹配（X说/道/喊："..."）
    2. 引号前最近的角色名（回扫 30 字）
    3. 无法归属 → speaker=None
    """
    attributed = []  # {speaker, content, start, end}
    claimed_spans = set()  # 已归属的 (start, end) 避免重复

    # 1. 前向归属
    for m in _ATTR_FWD_RE.finditer(text):
        name, content = m.group(1), m.group(2)
        if name in characters and content.strip():
            key = (m.start(2), m.end(2))
            if key not in claimed_spans:
                attributed.append({
                    "speaker": name, "content": content.strip(),
                    "start": m.start(2), "end": m.end(2),
                })
                claimed_spans.add(key)

    # 2. 后向归属
    for m in _ATTR_POST_RE.finditer(text):
        content, name = m.group(1), m.group(2)
        if name in characters and content.strip():
            key = (m.start(1), m.end(1))
            if key not in claimed_spans:
                attributed.append({
                    "speaker": name, "content": content.strip(),
                    "start": m.start(1), "end": m.end(1),
                })
                claimed_spans.add(key)

    # 3. 引号前最近角色名
    for m in _DIALOGUE_RE.finditer(text):
        content = m.group(1).strip()
        if not content:
            continue
        key = (m.start(1), m.end(1))
        if key in claimed_spans:
            continue
        # 回扫 30 字找最近角色名
        lookback = text[max(0, m.start() - 30):m.start()]
        speaker = None
        best_pos = -1
        for name in characters:
            idx = lookback.rfind(name)
            if idx >= 0 and idx > best_pos:
                best_pos = idx
                speaker = name
        attributed.append({
            "speaker": speaker, "content": content,
            "start": m.start(1), "end": m.end(1),
        })
        claimed_spans.add(key)

    attributed.sort(key=lambda x: x["start"])
    return attributed


# ── 共现/对话统计 ────────────────────────────────────────────

def _compute_cooccurrence(scenes: list[str], characters: list[str]) -> dict:
    """每场景统计角色出现 → 共现边。

    返回:
      scene_participation: {scene_idx: [char_names]}
      cooccurrence_edges: {(a, b): {"weight": int, "scenes": [idx]}}
    """
    scene_participation = {}
    edge_data = defaultdict(lambda: {"weight": 0, "scenes": []})

    for si, scene in enumerate(scenes):
        present = [c for c in characters if c in scene]
        if present:
            scene_participation[f"scene_{si}"] = present
        # 所有在同一场景中的角色对 → 共现
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                a, b = present[i], present[j]
                pair = (min(a, b), max(a, b))
                edge_data[pair]["weight"] += 1
                if si not in edge_data[pair]["scenes"]:
                    edge_data[pair]["scenes"].append(si)

    return {"scene_participation": scene_participation, "edges": edge_data}


def _compute_dialogue_interaction(dialogues: list[dict]) -> dict:
    """连续对话归属 → 对话交互边。A 说完紧接 B 说 → (A, B) +1。

    返回 {(a,b): {"weight": int, "scenes": []}（scenes 暂不填，需配合场景索引）。
    """
    edge_data = defaultdict(lambda: {"weight": 0, "scenes": []})
    prev_speaker = None
    for d in dialogues:
        speaker = d.get("speaker")
        if speaker and prev_speaker and speaker != prev_speaker:
            pair = (min(prev_speaker, speaker), max(prev_speaker, speaker))
            edge_data[pair]["weight"] += 1
        prev_speaker = speaker
    return edge_data


def _compute_centrality(characters: list[str], edges: list[dict]) -> dict:
    """简单度中心性：每个角色的边权重总和 / 最大值 → 归一化到 [0, 1]。"""
    degree = Counter()
    for e in edges:
        degree[e["source"]] += e["weight"]
        degree[e["target"]] += e["weight"]
    max_deg = max(degree.values()) if degree else 1
    return {c: round(degree.get(c, 0) / max_deg, 3) for c in characters}


# ── Renard 增强（可选） ────────────────────────────────────

def _try_renard(text: str, characters: list[str]) -> dict | None:
    """如果 Renard 库已安装，用 Renard 管线增强提取。失败 → None。"""
    try:
        import renard
        from renard.pipeline.core import Pipeline, PipelineStep
    except ImportError:
        return None
    except Exception:
        return None
    try:
        # Renard 中文管线（如果支持）
        pipe = renard.Pipeline(lang="zh")
        result = pipe(text)
        # 转换 Renard 输出到我们的格式
        chars = list(set(str(c) for c in result.characters)) if hasattr(result, "characters") else []
        edges = []
        if hasattr(result, "character_network") and result.character_network:
            import networkx as nx
            G = result.character_network
            for u, v, data in G.edges(data=True):
                edges.append({
                    "source": str(u), "target": str(v),
                    "type": "co_occurrence", "weight": data.get("weight", 1),
                    "scenes": [],
                })
        return {"characters": chars, "edges": edges, "source": "renard"}
    except Exception:
        return None


# ── VAD 情感标注（可选） ────────────────────────────────────

def _annotate_vad(edges: list[dict], scenes: list[str],
                  characters: list[str]) -> list[dict]:
    """如果 nn_vad_bridge 可用，对包含两角色的段落做 VAD → 标记关系情感色彩。"""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from nn_vad_bridge import enabled as vad_enabled, predict_batch
    except ImportError:
        return edges
    if not vad_enabled():
        return edges

    # 收集每对角色共现的文本
    pair_texts = {}
    for e in edges:
        a, b = e["source"], e["target"]
        pair_key = (min(a, b), max(a, b))
        if pair_key in pair_texts:
            continue
        # 找包含这对角色的场景
        texts = []
        for scene in scenes:
            if a in scene and b in scene:
                texts.append(scene[:500])  # 截断·VAD 不需要全文
        if texts:
            pair_texts[pair_key] = " ".join(texts[:3])  # 最多 3 段

    if not pair_texts:
        return edges

    keys = list(pair_texts.keys())
    texts = [pair_texts[k] for k in keys]
    vad_results = predict_batch(texts)

    vad_map = {}
    for k, v in zip(keys, vad_results):
        if v is not None:
            vad_map[k] = v

    for e in edges:
        pair_key = (min(e["source"], e["target"]), max(e["source"], e["target"]))
        if pair_key in vad_map:
            e["vad"] = vad_map[pair_key]

    return edges


# ── 主函数 ────────────────────────────────────────────────

def extract_character_network(
    draft_text: str,
    known_characters: list[str] | None = None,
    project_dir: str | None = None,
) -> dict:
    """从 cluster 草稿中提取角色关系网络。

    返回格式:
    {
      "characters": ["张三", "李四", ...],
      "edges": [{"source", "target", "type", "weight", "scenes"}, ...],
      "scene_participation": {"scene_0": ["张三", "李四"], ...},
      "centrality": {"张三": 0.8, ...},
      "source": "rule_based" | "renard" | "disabled"
    }
    """
    if not enabled():
        return _empty_result("disabled")

    try:
        return _extract_impl(draft_text, known_characters, project_dir)
    except Exception as e:  # noqa: BLE001 — 绝不崩主流水线
        print(f"[character_network_extractor] 异常·返回空结果："
              f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return _empty_result("error")


def _extract_impl(draft_text: str, known_characters: list[str] | None,
                  project_dir: str | None) -> dict:
    """实际提取逻辑（enabled 已确认）。"""
    text = _strip_changes(draft_text)
    if not text.strip():
        return _empty_result("empty_input")

    # 1. 识别角色
    characters = _identify_characters(text, known_characters, project_dir)
    if not characters:
        return _empty_result("no_characters_found")

    # 2. 尝试 Renard 增强
    renard_result = _try_renard(text, characters)
    if renard_result and renard_result.get("characters"):
        # 合并 Renard 发现的角色与已知角色
        all_chars = list(dict.fromkeys(characters + renard_result["characters"]))
        result = renard_result
        result["characters"] = all_chars
        result["centrality"] = _compute_centrality(all_chars, result["edges"])
        return result

    # 3. 纯规则提取
    scenes = _split_scenes(text)

    # 共现统计
    cooc = _compute_cooccurrence(scenes, characters)

    # 对话归属 + 对话交互
    dialogues = _attribute_dialogue(text, characters)
    dialogue_edges = _compute_dialogue_interaction(dialogues)

    # 合并边
    edges = []
    for (a, b), data in cooc["edges"].items():
        edges.append({
            "source": a, "target": b,
            "type": "co_occurrence",
            "weight": data["weight"],
            "scenes": data["scenes"],
        })
    for (a, b), data in dialogue_edges.items():
        edges.append({
            "source": a, "target": b,
            "type": "dialogue",
            "weight": data["weight"],
            "scenes": data["scenes"],
        })

    # VAD 情感标注（可选）
    edges = _annotate_vad(edges, scenes, characters)

    # 中心性
    centrality = _compute_centrality(characters, edges)

    return {
        "characters": characters,
        "edges": edges,
        "scene_participation": cooc["scene_participation"],
        "centrality": centrality,
        "dialogue_count": len(dialogues),
        "orphan_dialogues": sum(1 for d in dialogues if d["speaker"] is None),
        "source": "rule_based",
    }


# ── CLI ────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="角色关系网络提取器（cluster 草稿 → 共现/对话关系图）")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="项目根目录（读 _数据库/人物.json）")
    ap.add_argument("--characters", default=None,
                    help="已知角色列表（逗号分隔，如 张三,李四）")
    args = ap.parse_args()

    text = Path(args.draft_path).read_text(encoding="utf-8")
    known = args.characters.split(",") if args.characters else None
    result = extract_character_network(text, known, args.project)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
