#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_seam_scanner.py — 场景/段落衔接手法分布对账（cluster · advisory · 2026-05-31）

【治什么 · 闭环缺口】作者的「场景/段落衔接手法分布」已在蒸馏阶段被提取并写进
`作者风格_FINAL.json`（transition_method_distribution / connection_type_distribution
+ anti_patterns.never_transitions），**只教 writer prompt，却从未在正文里回查**——
writer 学没学会、有没有滑回「与此同时 / 紧接着」式 AI 套话连接，没有任何对账。
本 scanner 补上这条「输入教了，输出要查」的闭环。

【怎么测 · 纯规则零黑箱（北极星 5/6）】
  1. 切场景/段落边界 → 取每个「衔接点」的开头若干字。
  2. 用**可枚举的中文标志词**把衔接点归类到 9 个 canonical 类目：
       时间过渡 / 空间转场 / 因果连接 / 动作承接 / 钩子转场 /
       对话切场 / 心理过渡 / 分隔符过渡 / 硬切无过渡。
     另单列 ai_cliche_connector：作者蒸馏档 anti_patterns.never_transitions 里
     的「与此同时 / 紧接着 / 另一边 …」评书腔 AI 套话连接。
  3. 把正文衔接手法分布 vs 作者蒸馏衔接手法分布（归一占比）做对账：
       · L1 距离（各类目占比差绝对值之和）→ 总落差；
       · 命中作者 never_transitions 的 AI 套话连接 → 单独高亮（作者明确避免却出现）；
       · 作者主力类目（占比高）正文缺位 → 落差项。
  4. 落差超提示线 → advisory issue（绝不 hard_gate）。

【边界 · 北极星纪律】
  · 顾问非法官：issue code SEAM_DISTRIBUTION_DRIFT **永远 advisory · 绝不进
    audit_hub.HARD_GATE_CODES**——只提示「衔接手法分布偏离作者」，不替模型决定怎么衔接。
  · cluster 为单位：在 cluster 草稿层跑（多场景视野，衔接点天然更多更可统计）。
  · 不臃肿：纯 stdlib + 正则枚举标志词，不引 embedding/LLM/新系统；
    作者分布直接读已蒸馏的 作者风格_FINAL.json（缺则降级 never-list 单轨，不臆造）。
  · 与既做正交：段长（validate_style）/ 节奏谱（hook_strength）/ locked_fact 剧情连续
    都不盯「衔接手法类目分布」这一独立信号。
  · 影子并行 env SEAM_SCANNER_MODE：
      active（默认）：落差升 advisory issue 进顶层（仍 advisory · 绝不 hard_gate）。
      shadow：算全量分布挂 report · 顶层不报 warning · 不改 exit code（零回归）。
      off：完全跳过。
    非法值回退 active。

用法：
  python scene_seam_scanner.py <cluster_draft_path> [--style <作者风格_FINAL.json>] [--project <dir>]
退出码：0=干净/数据不足/shadow · 1=active 命中 advisory 落差 · 2=fatal（仅 CLI 参数错 / 文件缺失）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ISSUE_CODE = "SEAM_DISTRIBUTION_DRIFT"   # advisory 专用 · 绝不进 HARD_GATE_CODES

# ── 对账提示线（advisory 提示线 · 非判决线）。保守宽松：宁漏报不误伤作者 ─────
_L1_DRIFT_WARN = 0.90       # 正文 vs 作者衔接分布的 L1 距离 >= 此（满分 2.0）= 分布显著偏离
_MIN_SEAMS = 6              # 衔接点 < 此不判分布（样本太少趋势不稳 · cluster 通常远超）
_CLICHE_RATE_WARN = 0.15    # AI 套话连接占衔接点比例 >= 此 = 滑回评书腔（作者明确避免）

# ════════════════════════════════════════════════════════════════
# canonical 衔接手法类目 + 可枚举中文标志词（纯规则 · 不碰黑箱）
# 顺序即优先级：AI 套话 > 分隔符 > 钩子 > 因果 > 时间 > 空间 > 动作 > 对话 > 心理 > 硬切。
# ════════════════════════════════════════════════════════════════

# AI 套话连接（评书腔 · 作者蒸馏档 never_transitions 的固定集；命中=滑回套话）。
# 分两档（避免对真作者矫枉过正——真作者会把『与此同时/随后』作场景内时间副词合法使用）：
#   · HARD：纯评书腔/镜头语言过场词，正文无合法用法 → 段首前缀命中即判（任何位置）。
#   · DUAL：双用连接词（既可作场景内时间副词，也可被滥用成场景过场）→ 只有**紧跟场景
#     分隔符**（真被当过场词起新场景）才判，杜绝误伤场景内的合法时间副词。
CLICHE_HARD_MARKERS = (
    "话说回来", "话分两头", "且说", "却说", "再表", "暂且不表",
    "镜头一转", "镜头切换", "画面一转", "视角切到", "让我们把视线", "让我们把视角",
    "让我们回到", "回到现实", "话说",
)
CLICHE_DUAL_MARKERS = (
    "与此同时", "另一边", "紧接着", "随后", "接下来", "继而", "突然之间",
)
# 分类用全集（段首前缀归 ai_cliche_connector 类目时用 · 不区分档位只看是否套话词）。
AI_CLICHE_MARKERS = CLICHE_HARD_MARKERS + CLICHE_DUAL_MARKERS

# canonical 类目 → 标志词元组（除 ai_cliche / 硬切外，各类按可枚举词归类）。
_CATEGORY_MARKERS = {
    "分隔符过渡": ("……", "...", "* * *", "***", "———", "—————"),
    "钩子转场": ("忽然", "就在这时", "突然", "猛然", "陡然", "蓦然", "霎时", "刹那"),
    "因果连接": ("于是", "因此", "所以", "因为", "因而", "故而", "正因", "既然"),
    "时间过渡": ("次日", "翌日", "不久", "片刻", "转眼", "时间", "稍后", "良久",
                "半晌", "顷刻", "夜幕", "天色", "清晨", "傍晚", "黄昏", "三天后",
                "数日", "几日", "当晚"),
    "空间转场": ("来到", "走进", "走向", "出了", "进入", "踏进", "跨进", "回到",
                "赶往", "前往", "抵达", "穿过", "另一处", "另一头"),
    "动作承接": ("说着", "说罢", "说完", "话音", "闻言", "见状", "随即", "当下",
                "举步", "起身", "转身", "抬手", "伸手"),
    "对话切场": ("「", "“", "『", "\""),
    "心理过渡": ("想到", "心中", "心头", "暗忖", "暗道", "念头", "回过神",
                "一想到", "他知道", "她知道", "意识到"),
}

_PARA_SPLIT_RE = re.compile(r"\n\s*\n+|\n")
_SCENE_BREAK_RE = re.compile(r"\n\s*(?:[-—*＊]{3,}|\.{3,}|…{2,}|\* \* \*)\s*\n")
_CJK_RE = re.compile(r"[一-鿿]")


def _seam_head_window(para: str, n: int = 14) -> str:
    """取段首窗口（衔接判定只看段落开头若干字 · 衔接手法在段首落子）。"""
    return para.strip()[:n]


# 上下文相关类目：只有「紧跟场景分隔符」时才算衔接点（新场景以对话/独白开场）；
# 否则属场景内部的对话往复 / 心理流，不是『衔接手法』，绝不计入分布（避免矫枉过正）。
_CONTEXT_DEPENDENT_CATS = ("对话切场", "心理过渡")


def classify_seam_head(head: str, is_after_break: bool = False):
    """把一个段首窗口归类到 canonical 类目（纯规则 · 可枚举标志词 · 段首前缀匹配）。

    关键纪律（避免对真作者矫枉过正 · 把单位对齐到作者『场景衔接手法分布』）：
      · 标志词只在**段首前缀**位置才算衔接手法——「话说/却说」必须是段落开头的
        评书腔过场，正文中「这话说的」「那蛊却说」属普通叙述（含动词）不算。
      · 真·过渡标志词（时间/空间/因果/钩子/动作承接/AI套话/分隔符）开头的段 = 衔接点。
      · 对话/心理开头的段是**上下文相关**：只有紧跟场景分隔符（新场景开场）才算衔接点，
        否则是场景内对话往复/心理流，返回 None（不是衔接点 · 不污染对账）。
      · is_after_break=True 且无任何标志词 → 真·硬切无过渡；普通续写段无标志词 → None。

    返回类目名或 None（None=非衔接点）。"""
    if not head:
        return "硬切无过渡" if is_after_break else None
    # AI 套话连接优先（作者明确避免）——只认**段首前缀**，杜绝子串误伤。
    # HARD 档任何位置即判；DUAL 档（双用时间副词）只有紧跟场景分隔符才判（避免误伤合法用法）。
    for m in CLICHE_HARD_MARKERS:
        if head.startswith(m):
            return "ai_cliche_connector"
    if is_after_break:
        for m in CLICHE_DUAL_MARKERS:
            if head.startswith(m):
                return "ai_cliche_connector"
    win8 = head[:8]
    for cat, markers in _CATEGORY_MARKERS.items():
        matched = any(head.startswith(m) for m in markers if m)
        if not matched and cat in ("钩子转场", "因果连接", "时间过渡"):
            # 钩子/因果/时间类允许在段首前 8 字内出现（仍是「段落以过渡笔法起头」）。
            matched = any(m in win8 for m in markers)
        if matched:
            if cat in _CONTEXT_DEPENDENT_CATS and not is_after_break:
                return None   # 场景内对话/心理 · 非衔接手法
            return cat
    # 无任何过渡标志词：紧跟场景分隔符 → 真·硬切；否则非衔接点（普通续写段）。
    return "硬切无过渡" if is_after_break else None


def iter_seam_candidates(text: str):
    """切出所有「候选衔接点」：(段首窗口, 紧跟显式场景分隔符?)。

    显式场景分隔符（……/***/--- 单独成行）本身记一个分隔符过渡衔接点；其后第一段
    标记 is_after_break=True。每个有实质内容的段（CJK>=4）作为候选段首参与分类。"""
    blocks = _SCENE_BREAK_RE.split(text)
    out = []
    for bi, block in enumerate(blocks):
        after_break = bi > 0
        if after_break:
            # 分隔符本身就是一个明确的「分隔符过渡」衔接点。
            out.append(("……", False))
        paras = [p.strip() for p in _PARA_SPLIT_RE.split(block) if p.strip()]
        for pi, p in enumerate(paras):
            if len(_CJK_RE.findall(p)) >= 4:
                out.append((_seam_head_window(p), after_break and pi == 0))
    return out


def split_seam_points(text: str) -> list:
    """所有候选段首窗口（含分隔符虚拟点）—— detect_cliche 等只需段首文本时用。"""
    return [h for h, _ in iter_seam_candidates(text)]


# ════════════════════════════════════════════════════════════════
# 作者衔接手法分布读取（已蒸馏 作者风格_FINAL.json · 缺则降级 · 不臆造）
# ════════════════════════════════════════════════════════════════

# 蒸馏档原始类目标签 → 本 scanner canonical 类目的映射（覆盖两位真作者实测标签）。
_AUTHOR_LABEL_MAP = (
    ("空间硬切", "硬切无过渡"), ("硬切", "硬切无过渡"), ("无过渡", "硬切无过渡"),
    ("画外音", "心理过渡"), ("内心", "心理过渡"), ("独白", "心理过渡"), ("心理", "心理过渡"),
    ("行动承接", "动作承接"), ("动作", "动作承接"), ("微动作", "动作承接"),
    ("省略上线", "时间过渡"), ("省略", "时间过渡"), ("时间", "时间过渡"),
    ("空间", "空间转场"), ("走廊", "空间转场"), ("跳切", "空间转场"), ("传送", "空间转场"),
    ("对话", "对话切场"), ("录音", "对话切场"), ("话题承接", "对话切场"),
    ("钩子", "钩子转场"), ("悬念揭晓", "钩子转场"),
    ("因果", "因果连接"),
    ("分隔符", "分隔符过渡"),
    ("氛围", "心理过渡"), ("恐怖事件", "钩子转场"), ("系统", "空间转场"),
)


def _map_author_label(label: str):
    """把蒸馏档原始衔接标签映射到 canonical 类目（子串匹配 · 先到先得）。

    映射不到 → None（不强行归类 · 避免污染对账）。"""
    if not label or label == "其他":
        return None
    for key, canon in _AUTHOR_LABEL_MAP:
        if key in label:
            return canon
    return None


def load_author_seam_distribution(style: dict) -> dict:
    """从作者蒸馏 JSON 抽出衔接手法分布（归一占比）+ never 连接黑名单。

    兼容两位真作者两种 schema：
      · 惊悚乐园：style_profile.rhythm.transition_method_distribution（类目→计数）
                + narrative_continuity_template.connection_type_distribution。
      · 蛊真人：cross_chapter_diversity.transition_methods_distribution（细粒度 evidence→权重）。
    都映射到 canonical 类目后归一。never_transitions / never_scene_transition → 黑名单。"""
    raw_counts = defaultdict(float)
    sources = []

    ccd = style.get("cross_chapter_diversity") or {}
    sp_rhythm = (style.get("style_profile") or {}).get("rhythm") or {}
    nct = style.get("narrative_continuity_template") or {}

    for container, key in (
        (sp_rhythm, "transition_method_distribution"),
        (ccd, "transition_method_distribution"),
        (ccd, "transition_methods_distribution"),
        (nct, "connection_type_distribution"),
    ):
        dist = container.get(key)
        if isinstance(dist, dict) and dist:
            sources.append(key)
            for label, w in dist.items():
                try:
                    wf = float(w)
                except (TypeError, ValueError):
                    continue
                canon = _map_author_label(label)
                if canon:
                    raw_counts[canon] += wf

    total = sum(raw_counts.values())
    distribution = ({k: round(v / total, 4) for k, v in raw_counts.items()}
                    if total > 0 else {})

    # never 黑名单（作者明确避免的 AI 套话连接）。
    never = []
    ap = style.get("anti_patterns") or {}
    nt = ap.get("never_transitions")
    if isinstance(nt, list):
        never.extend(str(x) for x in nt)
    nst = ap.get("never_scene_transition")
    if isinstance(nst, dict):
        never.extend(str(x) for x in nst.keys())
    elif isinstance(nst, list):
        never.extend(str(x) for x in nst)

    return {
        "distribution": distribution,
        "never_connectors": never,
        "_sources": sources,
        "_total_weight": round(total, 3),
    }


def resolve_style_json(project_root, explicit):
    """定位作者蒸馏风格 JSON。降级链：① --style 显式；② 项目 _数据库/作者风格_FINAL.json
    或 作者风格.json；③ 用户偏好.json.style_baseline_data_path 指向的文件。缺 → None。"""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    if not project_root:
        return None
    db = project_root / "_数据库"
    for fn in ("作者风格_FINAL.json", "作者风格.json"):
        cand = db / fn
        if cand.is_file():
            return cand
    pref = db / "用户偏好.json"
    if pref.is_file():
        try:
            up = json.loads(pref.read_text(encoding="utf-8"))
            sp = up.get("style_baseline_data_path")
            if sp:
                cand = Path(sp)
                if cand.is_file():
                    return cand
        except Exception:
            pass
    return None


# ════════════════════════════════════════════════════════════════
# 对账核心（纯函数 · 可单测 · 无 IO）
# ════════════════════════════════════════════════════════════════

def scan_seam_distribution(text: str) -> dict:
    """统计正文衔接手法分布（归一占比 + 原始计数）。

    只把**真·衔接点**计入分布：紧跟场景分隔符的段（含硬切）或以过渡笔法起头的段；
    普通续写段（classify 返回 None）不计——与作者蒸馏档的『场景衔接手法分布』同单位，
    避免段落级噪声把『硬切无过渡』灌爆造成对真作者的矫枉过正。"""
    counts = defaultdict(int)
    for head, after_break in iter_seam_candidates(text):
        cat = "分隔符过渡" if head == "……" and not after_break else classify_seam_head(head, after_break)
        if cat is not None:
            counts[cat] += 1
    total = sum(counts.values())
    distribution = ({k: round(v / total, 4) for k, v in counts.items()}
                    if total > 0 else {})
    cliche_n = counts.get("ai_cliche_connector", 0)
    return {
        "seam_points": total,
        "counts": dict(counts),
        "distribution": distribution,
        "ai_cliche_count": cliche_n,
        "ai_cliche_rate": round(cliche_n / total, 4) if total else 0.0,
    }


def reconcile_distribution(text_dist: dict, author_dist: dict) -> dict:
    """正文 vs 作者衔接分布对账：L1 距离 + 缺位/过用类目。

    L1 距离 = Σ |text_pct(cat) - author_pct(cat)|（满分 2.0 · 0=完全一致）。"""
    cats = set(text_dist) | set(author_dist)
    per_cat = []
    l1 = 0.0
    for c in sorted(cats):
        t = text_dist.get(c, 0.0)
        a = author_dist.get(c, 0.0)
        d = abs(t - a)
        l1 += d
        per_cat.append({"category": c, "text_pct": round(t, 4),
                        "author_pct": round(a, 4), "delta": round(t - a, 4)})
    # 作者主力类目（占比 >=0.15）正文缺位（占比 < 一半）= 重点落差。
    missing = [p for p in per_cat
               if p["author_pct"] >= 0.15 and p["text_pct"] < p["author_pct"] * 0.5]
    # 正文过用类目（作者占比低却被正文堆叠）。
    overused = [p for p in per_cat
                if p["author_pct"] < 0.10 and p["text_pct"] >= 0.30]
    return {
        "l1_distance": round(l1, 4),
        "per_category": per_cat,
        "author_method_underused": missing,
        "method_overused": overused,
    }


def detect_cliche_connectors(text: str, never_connectors: list) -> dict:
    """检出正文里命中作者 never 黑名单的 AI 套话连接（作者明确避免却出现）。

    纪律（避免对真作者矫枉过正）：
      · 只认**段首前缀**命中——「话说/却说」必须是段落开头的评书腔过场词；正文中
        「这话说的」「那蛊却说」是普通动词短语，绝不误判。
      · HARD 档（纯评书腔/镜头语言）任何衔接点命中即判；
      · DUAL 档（与此同时/随后 等双用时间副词）只有**紧跟场景分隔符**作过场时才判——
        真作者把它们作场景内时间副词的合法用法不计入（实证 2 真作者过此校验）。"""
    # never 黑名单只用来确认作者确实声明避免（DUAL 在黑名单里才升格为可判）。
    declared = set()
    for entry in never_connectors:
        for m in AI_CLICHE_MARKERS:
            if m in entry:
                declared.add(m)
    hard = set(CLICHE_HARD_MARKERS) | (declared & set(CLICHE_HARD_MARKERS))
    dual = declared & set(CLICHE_DUAL_MARKERS)
    hits = []
    for head, after_break in iter_seam_candidates(text):
        cand = sorted(hard | (dual if after_break else set()), key=len, reverse=True)
        for m in cand:
            if head.startswith(m):       # 段首前缀 · 杜绝子串误伤
                tier = "hard" if m in CLICHE_HARD_MARKERS else "dual"
                hits.append({"connector": m, "tier": tier, "evidence": head})
                break
    return {"cliche_hits": hits, "cliche_hit_count": len(hits)}


# ════════════════════════════════════════════════════════════════
# 影子并行模式
# ════════════════════════════════════════════════════════════════

def _mode() -> str:
    """SEAM_SCANNER_MODE：默认 active（落差升 advisory issue · 仍永不 hard_gate）/ shadow / off。
    非法值回退 active。"""
    m = (os.environ.get("SEAM_SCANNER_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


def scan(draft_path: Path, style_path) -> dict:
    if not draft_path.exists():
        return {"_fatal": f"draft 不存在: {draft_path}"}
    text = draft_path.read_text(encoding="utf-8")
    mode = _mode()

    text_stats = scan_seam_distribution(text)

    author = None
    author_loaded = False
    if style_path and style_path.is_file():
        try:
            style = json.loads(style_path.read_text(encoding="utf-8"))
            author = load_author_seam_distribution(style)
            author_loaded = bool(author.get("distribution") or author.get("never_connectors"))
        except Exception as e:  # 作者档损坏 = 降级单轨，不报错（顾问制）
            author = {"_error": str(e), "distribution": {}, "never_connectors": []}

    reconcile = None
    cliche = None
    if author and author.get("distribution"):
        reconcile = reconcile_distribution(text_stats["distribution"], author["distribution"])
    if author and author.get("never_connectors") is not None:
        cliche = detect_cliche_connectors(text, author.get("never_connectors") or [])

    # ── advisory 判定（永远 advisory · 落差只提示）──────────────────────────
    # 分两层，避免对真作者矫枉过正（2 真作者实证）：
    #   · ACTIVE 可上浮层 = AI 套话连接轴（命中 / 占比）——纯规则段首前缀 + 双档过滤后，
    #     真作者 rate=0、hits=0，零误报；这是「教了输出不查」闭环的可靠信号。
    #   · NOTE 仅记录层 = 全分布 L1 落差——规则抽取 vs LLM 蒸馏标签的类目体系天然有损，
    #     真作者 L1 也高达 1.3-1.7，不可作判据；只挂 distribution_note 供人参考，永不上浮成 issue。
    issues = []
    enough = text_stats["seam_points"] >= _MIN_SEAMS

    if enough and text_stats["ai_cliche_rate"] >= _CLICHE_RATE_WARN:
        issues.append({
            "code": ISSUE_CODE,
            "gate_level": "advisory",
            "kind": "ai_cliche_connector_overuse",
            "ai_cliche_rate": text_stats["ai_cliche_rate"],
            "detail": (f"AI 套话连接占衔接点 {text_stats['ai_cliche_rate']:.0%}"
                       f"（>={_CLICHE_RATE_WARN:.0%}）·作者明确避免的评书腔"),
        })

    if cliche and cliche["cliche_hit_count"] > 0:
        issues.append({
            "code": ISSUE_CODE,
            "gate_level": "advisory",
            "kind": "never_connector_hit",
            "hit_count": cliche["cliche_hit_count"],
            "detail": f"命中作者 never 黑名单连接 {cliche['cliche_hit_count']} 处",
            "samples": cliche["cliche_hits"][:5],
        })

    # 全分布 L1 落差：仅记录（distribution_note），绝不上浮成 issue / warning（防矫枉过正）。
    distribution_note = None
    if enough and reconcile:
        distribution_note = {
            "kind": "distribution_drift",
            "l1_distance": reconcile["l1_distance"],
            "warn_line": _L1_DRIFT_WARN,
            "over_warn_line": reconcile["l1_distance"] >= _L1_DRIFT_WARN,
            "note": ("规则抽取 vs 蒸馏标签类目体系有损 · L1 仅供参考不作判据"
                     "（真作者 L1 亦达 1.3-1.7）"),
            "author_method_underused": reconcile["author_method_underused"][:5],
            "method_overused": reconcile["method_overused"][:5],
        }

    # shadow：算全量挂 report，但顶层不报 warning、不改 exit code（零回归）。
    surfaced = issues if mode == "active" else []
    warning = None
    if surfaced:
        warning = "；".join(i["detail"] for i in surfaced)

    return {
        "schema_version": "1.0",
        "scanner": "scene_seam_scanner",
        "gate_level": "advisory",          # 整 scanner 永远 advisory
        "cluster_mode": True,
        "mode": mode,
        "seam_points": text_stats["seam_points"],
        "enough_samples": enough,
        "text_distribution": text_stats["distribution"],
        "text_counts": text_stats["counts"],
        "ai_cliche_rate": text_stats["ai_cliche_rate"],
        "author_loaded": author_loaded,
        "author_distribution": (author or {}).get("distribution") if author else None,
        "author_sources": (author or {}).get("_sources") if author else None,
        "reconcile": reconcile,
        "distribution_note": distribution_note,   # 仅记录 · 永不上浮成 issue（防矫枉过正）
        "cliche_check": cliche,
        "issues": surfaced,                # active 才上浮；shadow 永远 []
        "shadow_issues": issues if mode == "shadow" else None,
        "warning": warning,
        "severity": "warning" if warning else "info",
    }


def main():
    ap = argparse.ArgumentParser(description="场景/段落衔接手法分布对账 scanner（advisory）")
    ap.add_argument("draft", help="cluster 草稿文件路径")
    ap.add_argument("--style", default=None, help="作者风格_FINAL.json 路径（缺则从 --project 推断）")
    ap.add_argument("--project", default=None, help="项目根（用于定位 _数据库/作者风格_FINAL.json）")
    args = ap.parse_args()

    draft = Path(args.draft).resolve()
    project = Path(args.project).resolve() if args.project else None
    style_path = resolve_style_json(project, args.style)

    report = scan(draft, style_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if "_fatal" in report:
        sys.exit(2)
    if report.get("warning"):   # 仅 active 命中才非 0（shadow 永远 warning=None）
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
