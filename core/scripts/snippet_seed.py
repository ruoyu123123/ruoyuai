#!/usr/bin/env python3
"""snippet_seed.py — 真实原文片段「语感种子」播种（P0 · 2026-05-31）

北极星①更好实现作者蒸馏仿写 / ⑤顾问非法官 / ⑥别过度复杂（纯 prompt + 纯 stdlib）。

机制（gen_writer 写作 + distill_replicate 复刻 共用）：
  生成时 prompt 额外注入 1-2 段**作者真实原文片段**当「语感种子」，
  让模型贴真实文本流形起笔，防长 cluster 中后段退化回通用 AI 腔
  （直击 reference-system-validation-method 记录的 cluster 级 D 级长文退化 + 段长崩塌）。

5 调研共识 + 两篇论文交叉印证（in-context style anchoring）。

🔴 关键避坑（Catch Me If You Can 论文实证）：
  片段按**风格 / 情绪相似**选，**不是题材相似**——题材相似选样反而降分
  （模型会去抄题材内容而非借语感）。故本模块选样打分**只看语言学坐标 + 情绪寄存器**，
  绝不按剧情关键词 / 题材名词匹配。

🔴 prompt 必须明确：「只借语感语调起手势 · 情节严格按 storyboard/brief 走 · 绝不抄原文情节内容」
  （防抄袭 + 防内容泄漏）。

🔴 影子纪律（env SNIPPET_SEED_MODE 默认 off）：
  默认 off → prompt 不注入种子段（不改默认生成行为 · 回归 0）。
  待 gen-model 实跑 A/B 验证 token 成本 + 效果后再放量 on/shadow→on。
  值（大小写不敏感）：
    · off（默认 / 空 / 非法值）：不注入种子段——零回归对照路径。
    · on / 1 / true / active：注入 1-2 段真实原文种子 + 避坑指令。
    · shadow：只构造种子段并落痕（供 A/B 复盘），但**不拼进 prompt**——
      影子先接通管道、量 token 成本，确认不改默认生成行为再放量 on。

只改 prompt 构造（确定性可测）· 不改 writer/复刻走 gen-model 的事实 ·
种子是「风格起手势」非「内容」· 纯 stdlib（无新重依赖）。
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path

# ============ 模式解析（影子纪律 · 默认 off）============

def snippet_seed_mode() -> str:
    """读 env SNIPPET_SEED_MODE 决定是否注入真实原文种子段。

    默认 off（影子纪律 · 空 / 非法值保守退默认行为 · 不静默开启未经实跑验证的升级）。
    on / 1 / true / active → "on"；shadow → "shadow"；其余 → "off"。
    """
    v = (os.environ.get("SNIPPET_SEED_MODE") or "").strip().lower()
    if v in ("on", "1", "true", "active"):
        return "on"
    if v == "shadow":
        return "shadow"
    return "off"


# ============ 原文池定位 ============

def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_originals_dir(project_root: Path) -> Path | None:
    """定位作者真实原文目录（writer 路径用）。

    优先级：
    1. 项目自带 原文/ （project_root/原文）
    2. _数据库/作者风格.json.style_source 指向的风格库 → 同级 原文/
       （style_source 形如 workspace/styles/惊悚乐园/skill_FINAL.md → 取其父目录 / 原文）
    3. None（无原文池 · 不注入种子 · 退默认行为）

    路径解析容忍相对路径（相对仓库根，从 project_root 向上找含 workspace 的根）。
    """
    own = project_root / "原文"
    if own.exists() and own.is_dir():
        return own

    db = project_root / "_数据库"
    style_json = db / "作者风格.json"
    if style_json.exists():
        data = _read_json(style_json)
        src = data.get("style_source") or data.get("style_baseline_data") or ""
        if src:
            cand = _resolve_style_src_to_originals(project_root, src)
            if cand and cand.exists() and cand.is_dir():
                return cand
    return None


def _resolve_style_src_to_originals(project_root: Path, src: str) -> Path | None:
    """把 style_source 路径（可能相对仓库根）解析成同级 原文/ 目录。"""
    src_path = Path(src)
    # 绝对路径直接用其父目录
    if src_path.is_absolute():
        return src_path.parent / "原文"
    # 相对路径：从 project_root 逐级向上找包含该相对路径的祖先
    for base in [project_root, *project_root.parents]:
        cand = (base / src_path).resolve()
        # 父目录即风格库根；同级 原文/
        originals = cand.parent / "原文"
        if originals.exists():
            return originals
    return None


# ============ 情绪 / 语言学寄存器打分（避坑核心：只看风格/情绪 · 不看题材）============

# 极简情绪寄存器词典（stdlib · 无外部依赖）。
# 只用于「片段情绪强度寄存器」近似——不做题材匹配（题材匹配=Catch Me 论文降分陷阱）。
_TENSION_LEXICON = {
    "怒", "吼", "杀", "血", "死", "痛", "撕", "砸", "冲", "扑", "斩", "劈",
    "惊", "怕", "颤", "嘶", "喊", "逃", "追", "爆", "裂", "崩", "急", "猛",
}
_CALM_LEXICON = {
    "静", "缓", "淡", "轻", "柔", "暖", "笑", "默", "想", "记", "忆", "望",
    "坐", "停", "沉", "凝", "叹", "幽", "微", "渐",
}

_SENT_SPLIT = re.compile(r"[。！？…\n]")


def _emotion_register(text: str) -> float:
    """估片段情绪寄存器（-1 平静 ↔ +1 紧张）· 纯词频近似 · 只为「风格相似」选样。"""
    if not text:
        return 0.0
    tension = sum(text.count(w) for w in _TENSION_LEXICON)
    calm = sum(text.count(w) for w in _CALM_LEXICON)
    total = tension + calm
    if total == 0:
        return 0.0
    return (tension - calm) / total


def _avg_sentence_len(text: str) -> float:
    """估平均句长（CJK 字 / 句）· 语言学坐标之一（句长节奏相似 = 风格相似）。"""
    sents = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sents:
        return 0.0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk / len(sents)


def _dialogue_ratio(text: str) -> float:
    """估对话占比（含中文左引号的行 / 总非空行）· 风格寄存器之一。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    dlg = sum(1 for ln in lines if "“" in ln or "「" in ln)
    return dlg / len(lines)


def style_distance(a_profile: dict, b_profile: dict) -> float:
    """两个片段 / 目标的风格寄存器距离（越小越相似）。

    维度：情绪寄存器 + 平均句长 + 对话占比——全是**语言学/风格坐标**，
    **绝不含题材/剧情关键词**（Catch Me 论文：题材相似选样反降分）。
    """
    de = abs(a_profile.get("emotion", 0.0) - b_profile.get("emotion", 0.0))  # 0..2
    # 句长归一（除以典型上限 40 字 · 钳到 [0,1]）
    dl = min(1.0, abs(a_profile.get("avg_sent_len", 0.0) - b_profile.get("avg_sent_len", 0.0)) / 40.0)
    dd = abs(a_profile.get("dialogue_ratio", 0.0) - b_profile.get("dialogue_ratio", 0.0))  # 0..1
    # 情绪权重最高（情绪寄存器是「风格相似」最强信号）
    return de * 1.0 + dl * 0.6 + dd * 0.6


def profile_text(text: str) -> dict:
    """抽片段的风格寄存器 profile（情绪 / 句长 / 对话占比）。"""
    return {
        "emotion": _emotion_register(text),
        "avg_sent_len": _avg_sentence_len(text),
        "dialogue_ratio": _dialogue_ratio(text),
    }


# ============ 片段抽取 ============

# 章首常见噪声（标题行 / 站点广告 / 章号），抽种子时跳过。
_NOISE_PAT = re.compile(
    r"(吾爱文学|www\.|http|更多精彩|请收藏|正文\s|第[一二三四五六七八九十百千\d]+[章节]\b)"
)


def _split_paragraphs(raw: str) -> list[str]:
    """切段：兼容两种作者原文排版。

    1. 空行分隔（蛊真人）：按 \\n\\n 切。
    2. 单换行 + 全角缩进（惊悚乐园 · 无空行）：blank-line 切只得 1 段时退回按单换行切。
    去掉全角缩进（　　/ 空格）。
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(paras) <= 2:  # blank-line 切失效（单换行排版）→ 退回逐行
        paras = [ln.strip("　 \t") for ln in raw.splitlines() if ln.strip()]
    return [p for p in paras if p]


def _extract_candidate_snippets(originals_dir: Path, max_files: int = 40,
                                min_cjk: int = 90, max_cjk: int = 360) -> list[str]:
    """从原文池抽候选片段：每章取若干「干净的多句连续段落块」。

    片段是**连续 2-4 段**的小块（带语感节奏），跳过标题/广告噪声。
    限 max_files 章扫描（防 688 章全扫拖慢 · 取样足够代表风格分布）。
    """
    candidates: list[str] = []
    files = sorted(originals_dir.glob("第*.txt"))[:max_files]
    for fp in files:
        try:
            raw = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        # 兼容空行分隔（蛊真人）/ 单换行排版（惊悚乐园）两种排版
        paras = _split_paragraphs(raw)
        # 滑窗取 2-4 段拼成片段
        buf: list[str] = []
        buf_cjk = 0
        for para in paras:
            if _NOISE_PAT.search(para):
                continue
            pc = sum(1 for ch in para if "一" <= ch <= "鿿")
            if pc == 0:
                continue
            buf.append(para)
            buf_cjk += pc
            if buf_cjk >= min_cjk:
                snippet = "\n".join(buf)
                if min_cjk <= buf_cjk <= max_cjk:
                    candidates.append(snippet)
                buf, buf_cjk = [], 0
                if len(candidates) >= 80:  # 候选池上限（足够选样 · 防内存）
                    return candidates
    return candidates


def select_snippets(originals_dir: Path, target_profile: dict,
                    n: int = 2, seed: int = 0) -> list[str]:
    """按风格/情绪相似度从原文池选 n 段种子（避坑：不按题材匹配）。

    target_profile：当前 cluster 目标场景的风格寄存器 profile（情绪 / 句长 / 对话占比）。
    选与 target 风格寄存器**最接近**的 n 段（style_distance 升序），保证语感同频。
    seed 仅用于同分稳定排序（不引入随机 · 确定性可测）。
    """
    cands = _extract_candidate_snippets(originals_dir)
    if not cands:
        return []
    scored = []
    for idx, snip in enumerate(cands):
        d = style_distance(profile_text(snip), target_profile)
        scored.append((d, idx, snip))
    scored.sort(key=lambda x: (x[0], (x[1] + seed) % len(cands)))
    return [s for _, _, s in scored[:max(1, n)]]


# ============ 种子 prompt 段构造（含避坑指令）============

# 避坑指令（Catch Me 论文 + 防抄袭 + 防内容泄漏）—— 任何注入路径都必带这段。
SNIPPET_AVOIDANCE_INSTRUCTION = (
    "⚠️ 这些片段**只用于借语感、语调、句式节奏的「起手势」**——"
    "学的是这位作者怎么遣词、断句、控制段落呼吸、处理情绪的笔触。\n"
    "🔴 **严禁**抄原文的情节 / 人物名 / 专有设定 / 任何具体内容。\n"
    "🔴 情节**严格**按上方 storyboard / cluster_brief 走，片段里的剧情与你要写的故事无关。\n"
    "🔴 你要复刻的是**语言风格**不是**题材内容**——这些片段是风格锚点，不是抄写范本。"
)

SNIPPET_SEED_HEADER = "## 🎴 语感种子（作者真实原文片段 · 仅借语感起手势 · 绝不抄内容）"


def build_seed_section(snippets: list[str]) -> str:
    """把选中的种子片段 + 避坑指令组装成 prompt 段。空片段 → 空串（不注入）。"""
    if not snippets:
        return ""
    parts = [SNIPPET_SEED_HEADER, "", SNIPPET_AVOIDANCE_INSTRUCTION, ""]
    for i, snip in enumerate(snippets, 1):
        parts.append(f"### 种子片段 {i}（语感参考 · 非内容）\n\n{snip}")
    return "\n".join(parts)


def build_target_profile(scope_text: str = "", ref_text: str = "",
                         emotion_hint: float | None = None) -> dict:
    """从当前 cluster 的 scope_summary / 参考文本估目标风格寄存器 profile。

    优先用 ref_text（同 cluster 真实原文首段，最贴近目标场景的风格寄存器）；
    否则用 scope_summary 文字的情绪寄存器近似。emotion_hint 显式覆盖情绪维度。
    """
    base_text = ref_text or scope_text
    prof = profile_text(base_text) if base_text else {
        "emotion": 0.0, "avg_sent_len": 0.0, "dialogue_ratio": 0.0
    }
    if emotion_hint is not None:
        prof["emotion"] = emotion_hint
    return prof


# ============ 一站式：给 gen_writer / distill_replicate 调 ============

def make_seed_block_for_writer(project_root: Path, scope_text: str = "",
                               n: int = 2) -> tuple[str, dict]:
    """gen_writer 用：定位原文池 → 按 scope 风格选种子 → 构造种子段。

    返回 (seed_section_text, trace_meta)。
    seed_section_text 为 "" 时调用方不注入（无原文池 / 选不到种子 / 模式 off）。
    trace_meta 供 changes.json 留痕（不黑箱 · 复盘可见用了哪几段、什么模式）。
    """
    mode = snippet_seed_mode()
    trace = {"snippet_seed_mode": mode, "snippets_used": 0,
             "originals_dir": None, "injected": False}
    if mode == "off":
        return "", trace
    originals = resolve_originals_dir(project_root)
    if originals is None:
        trace["reason"] = "no_originals_dir"
        return "", trace
    trace["originals_dir"] = str(originals)
    target = build_target_profile(scope_text=scope_text)
    snippets = select_snippets(originals, target, n=n)
    if not snippets:
        trace["reason"] = "no_candidate_snippets"
        return "", trace
    section = build_seed_section(snippets)
    trace["snippets_used"] = len(snippets)
    trace["snippet_cjk"] = [sum(1 for ch in s if "一" <= ch <= "鿿") for s in snippets]
    # shadow：构造但不注入（量成本 / A-B 复盘 · 不改默认生成行为）
    if mode == "shadow":
        trace["injected"] = False
        trace["shadow_section_chars"] = len(section)
        return "", trace
    trace["injected"] = True
    return section, trace


def make_seed_block_from_dir(originals_dir: Path | None, ref_text: str = "",
                             scope_text: str = "", n: int = 2) -> tuple[str, dict]:
    """distill_replicate 用：原文池已知（gather_cluster_ref_text 的同源目录），
    直接按 ref_text/scope 的风格寄存器选种子。

    返回 (seed_section_text, trace_meta)。语义同 make_seed_block_for_writer。
    """
    mode = snippet_seed_mode()
    trace = {"snippet_seed_mode": mode, "snippets_used": 0,
             "originals_dir": str(originals_dir) if originals_dir else None,
             "injected": False}
    if mode == "off":
        return "", trace
    if originals_dir is None or not originals_dir.exists():
        trace["reason"] = "no_originals_dir"
        return "", trace
    target = build_target_profile(scope_text=scope_text, ref_text=ref_text)
    snippets = select_snippets(originals_dir, target, n=n)
    if not snippets:
        trace["reason"] = "no_candidate_snippets"
        return "", trace
    section = build_seed_section(snippets)
    trace["snippets_used"] = len(snippets)
    trace["snippet_cjk"] = [sum(1 for ch in s if "一" <= ch <= "鿿") for s in snippets]
    if mode == "shadow":
        trace["shadow_section_chars"] = len(section)
        return "", trace
    trace["injected"] = True
    return section, trace
