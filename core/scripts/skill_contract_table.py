#!/usr/bin/env python3
"""skill_contract_table.py — L3c skill 双层结构化 + 数值契约表注入器（北极星①⑤ · 2026-05-31）。

根因（ZeroStylus 实证）：skill 是**长文描述**，长文鲁棒性弱（纯句级方法对长文仅 43% 胜率），
段级结构才是长文鲁棒性来源。本脚本对 skill 头部做两件确定性事：

  (a) 注入作者**数值契约表**（虚词 Top-N / 句长均值+方差 / 单句独行占比 / 段长分位数 [p5,p50,p95]
      / 标点分布 / 章字数 / 对话占比）—— 这些 L1a 已在 style_analyzer 算出并落进 作者风格_FINAL.json，
      契约表把它们提到 skill 最顶端，作为「作者档第一权威」的**强化**（北极星⑤ · 顾问非法官，
      契约表是数值参照不是硬门禁，仍由审核层各 scanner 做 advisory/hard_gate 裁决）。
  (b) 显式分**句级层**（句式/口癖/禁用词）+**段级层**（段落组织规律 / cliffhanger 落点 /
      POV 切换习惯）—— 两层 scaffold 给 LLM 长文写作可锚定的段级结构。

设计纪律：
  · 零依赖（纯 stdlib · 不引 numpy/jieba/transformers）。
  · schema 容忍（北极星「consumer tolerant」）：蛊真人用 sentence_length/chapter_chars/
    dialogue_ratio_pct/paragraph_count，惊悚乐园用 chapter_words/dialogue_ratio(分数)/
    paragraph_length.single_sentence_para_ratio_mean —— 两套 key 都吃，缺字段标「未蒸出」不编造。
  · 复用 L1a 已有分位数（paragraph_length_chars / punctuation_per_1k / function_words_per_1k
    的 [p5,p50,p95]），不重算。
  · 幂等注入：用 SENTINEL 标记块，重跑只替换块内、不重复堆叠。
  · 不碰 distill_replicate（L3b 在改）· 不改任何评分/判决逻辑（纯生成 + 文件注入）。

CLI：
  python skill_contract_table.py --style <作者风格_FINAL.json> [--render]      # 只打印契约表+双层 scaffold
  python skill_contract_table.py --style <...json> --skill <skill_vN.md> --inject  # 幂等注入到 skill 头部
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# ── 幂等注入哨兵（块内可重写、不重复堆叠）─────────────────────────────
SENTINEL_BEGIN = "<!-- L3C_CONTRACT_BEGIN · skill_contract_table.py 自动生成 · 勿手改块内 -->"
SENTINEL_END = "<!-- L3C_CONTRACT_END -->"

# 虚词契约表默认取 Top-N（按 p50 频率降序），保证头部紧凑可读
DEFAULT_FUNCTION_TOP_N = 10


# ══════════════════════════════════════════════════════════════════
# [1] schema 容忍的取数原语（蛊真人 / 惊悚乐园 两套 key 都吃）
# ══════════════════════════════════════════════════════════════════

def _q(style: dict) -> dict:
    """取 quantitative 块（容忍 顶层即 quantitative / 包了一层 两种）。"""
    if not isinstance(style, dict):
        return {}
    q = style.get("quantitative")
    if isinstance(q, dict):
        return q
    # 已经是 quantitative 自身
    return style if any(k in style for k in ("sentence_length", "paragraph_length_chars")) else {}


def _num(d, *keys):
    """从 dict d 里按 keys 顺序找第一个 number（容忍多 key 别名）。"""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _sentence_len(q: dict) -> dict:
    """句长均值 + 方差（章内 std 优先 intra_chapter_std_mean → sentence_length_std.mean）。"""
    sl = q.get("sentence_length") or {}
    mean = _num(sl, "mean")
    # 章内方差签名：惊悚乐园 sentence_length.intra_chapter_std_mean / 蛊真人 顶层 sentence_length_std.mean
    std = _num(sl, "intra_chapter_std_mean")
    if std is None:
        std = _num(q.get("sentence_length_std") or {}, "mean")
    return {"mean": mean, "std": std}


def _dialogue_ratio_pct(q: dict) -> float | None:
    """对话占比统一归一为**百分数**（蛊真人 dialogue_ratio_pct=25.4 / 惊悚乐园 dialogue_ratio.mean=0.27）。"""
    v = _num(q.get("dialogue_ratio_pct") or {}, "mean")
    if v is None:
        v = q.get("dialogue_ratio_pct")
        if isinstance(v, (int, float)):
            v = float(v)
        else:
            v = None
    if v is not None:
        return round(v, 2)
    frac = _num(q.get("dialogue_ratio") or {}, "mean")
    if frac is not None:
        return round(frac * 100, 2)
    return None


def _chapter_chars(q: dict) -> dict:
    """章字数 mean/std（蛊真人 chapter_chars / 惊悚乐园 chapter_words）。"""
    cc = q.get("chapter_chars") or q.get("chapter_words") or {}
    return {"mean": _num(cc, "mean"), "std": _num(cc, "std")}


def _single_sentence_ratio(q: dict) -> float | None:
    """单句独行占比（惊悚乐园 paragraph_length.single_sentence_para_ratio_mean·蛊真人未蒸出→None）。"""
    pl = q.get("paragraph_length") or {}
    return _num(pl, "single_sentence_para_ratio_mean")


def _para_quantiles(q: dict) -> dict | None:
    """段长分位数 [p5,p50,p95]（L1a 已算 paragraph_length_chars · 复用不重算）。"""
    plc = q.get("paragraph_length_chars")
    if not isinstance(plc, dict):
        return None
    p5, p50, p95 = plc.get("p5"), plc.get("p50"), plc.get("p95")
    if all(isinstance(x, (int, float)) for x in (p5, p50, p95)):
        return {"p5": round(float(p5), 1), "p50": round(float(p50), 1),
                "p95": round(float(p95), 1)}
    return None


def _punct_dist(q: dict) -> dict:
    """标点分布（逗句比 / 省略号 / 感叹号 / 问号 / 破折号·取 p50 或 mean）。"""
    p = q.get("punctuation_per_1k") or q.get("punctuation_density_per_1000") or {}
    out = {}
    for label, key in (("逗句比", "comma_period_ratio"), ("省略号", "ellipsis"),
                       ("感叹号", "exclamation"), ("问号", "question"), ("破折号", "dash")):
        cell = p.get(key)
        if isinstance(cell, dict):
            v = _num(cell, "p50", "mean")
        elif isinstance(cell, (int, float)):
            v = float(cell)
        else:
            v = None
        if v is not None:
            out[label] = round(v, 2)
    return out


def _function_top(q: dict, n: int = DEFAULT_FUNCTION_TOP_N) -> list[tuple[str, float]]:
    """虚词 Top-N（按 p50 频率降序 · 复用 L1a function_words_per_1k 分位数）。"""
    fw = q.get("function_words_per_1k") or q.get("function_word_fingerprint_per_1000") or {}
    items = []
    for word, cell in fw.items():
        if isinstance(cell, dict):
            v = _num(cell, "p50", "mean")
        elif isinstance(cell, (int, float)):
            v = float(cell)
        else:
            v = None
        if v is not None:
            items.append((word, round(v, 1)))
    items.sort(key=lambda kv: kv[1], reverse=True)
    return items[:n]


# ══════════════════════════════════════════════════════════════════
# [2] 数值契约表抽取（纯函数 · 可测）
# ══════════════════════════════════════════════════════════════════

def extract_contract_table(style: dict) -> dict:
    """从 作者风格_FINAL.json 抽数值契约表（schema 容忍 · 缺字段值为 None 不编造）。

    返回结构化 dict（render 与测试共用）：
      function_words_top : [(虚词, p50频率)] 降序 Top-N
      sentence_mean / sentence_std : 句长均值 / 章内方差
      single_sentence_ratio : 单句独行占比（0-1 · None=未蒸出）
      para_quantiles : {p5,p50,p95} 段长分位数（None=老档无）
      punctuation : {逗句比/省略号/...}
      dialogue_pct : 对话占比百分数
      chapter_mean / chapter_std : 章字数
    """
    q = _q(style)
    sl = _sentence_len(q)
    cc = _chapter_chars(q)
    return {
        "function_words_top": _function_top(q),
        "sentence_mean": sl["mean"],
        "sentence_std": sl["std"],
        "single_sentence_ratio": _single_sentence_ratio(q),
        "para_quantiles": _para_quantiles(q),
        "punctuation": _punct_dist(q),
        "dialogue_pct": _dialogue_ratio_pct(q),
        "chapter_mean": cc["mean"],
        "chapter_std": cc["std"],
    }


def _fmt(v, suffix="") -> str:
    """数值格式化（None → 「未蒸出」· 不编造）。"""
    if v is None:
        return "未蒸出"
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return f"{v}{suffix}"


# ══════════════════════════════════════════════════════════════════
# [3] 渲染：数值契约表 markdown 块
# ══════════════════════════════════════════════════════════════════

def render_contract_table_md(contract: dict) -> str:
    """渲染数值契约表 markdown（作者档第一权威的强化 · 顾问非法官）。"""
    lines = []
    lines.append("## 📊 作者数值契约表（第一权威 · 写作时对齐）")
    lines.append("")
    lines.append("> 本表数值来自 `作者风格_FINAL.json` 由 `style_analyzer.py` 程序化统计涌现，"
                 "**是写作对齐参照（顾问），不是硬门禁**（审核层各 scanner 仍按 advisory/hard_gate 裁决）。")
    lines.append("")
    lines.append("| 契约项 | 数值 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 句长均值 | {_fmt(contract['sentence_mean'], ' 字')} | 全书句长中心 |")
    lines.append(f"| 句长方差（章内 std）| {_fmt(contract['sentence_std'])} | 越大越是长短句强混搭签名 |")
    pq = contract["para_quantiles"]
    if pq:
        lines.append(f"| 段长分位数 [p5,p50,p95] | [{_fmt(pq['p5'])}, {_fmt(pq['p50'])}, "
                     f"{_fmt(pq['p95'])}] 字 | 覆盖作者真实 90% 段落区间（L1a band 同源）|")
    else:
        lines.append("| 段长分位数 [p5,p50,p95] | 未蒸出 | 老蒸馏档无分位数（建议重跑 L1a 补齐）|")
    ssr = contract["single_sentence_ratio"]
    lines.append(f"| 单句独行占比 | {_fmt(round(ssr * 100, 1) if ssr is not None else None, '%')} "
                 "| 节奏感来源（爽文节奏 ≥40%）|")
    lines.append(f"| 对话占比 | {_fmt(contract['dialogue_pct'], '%')} | 全章平均（战斗章低/对话章高）|")
    lines.append(f"| 章字数均值 | {_fmt(contract['chapter_mean'], ' 字')}"
                 f"（σ={_fmt(contract['chapter_std'])}）| 单章字数中心 |")
    # 标点分布
    punct = contract["punctuation"]
    if punct:
        cells = " · ".join(f"{k} {v}" for k, v in punct.items())
        lines.append(f"| 标点分布（per-1k）| {cells} | 逗句比高=长句基调 |")
    # 虚词 Top-N
    fw = contract["function_words_top"]
    if fw:
        cells = " · ".join(f"{w} {v}" for w, v in fw)
        lines.append(f"| 虚词指纹 Top-{len(fw)}（per-1k）| {cells} | 功能词频率指纹（作者区分度高）|")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# [4] 渲染：句级层 / 段级层 双层 scaffold
# ══════════════════════════════════════════════════════════════════

def _top_dist(d: dict, n: int = 5) -> list[tuple[str, float]]:
    """按占比降序取 Top-N（cross_chapter_diversity 分布字段用）。"""
    if not isinstance(d, dict):
        return []
    items = [(k, float(v)) for k, v in d.items() if isinstance(v, (int, float))]
    items.sort(key=lambda kv: kv[1], reverse=True)
    return items[:n]


def render_dual_layer_scaffold(style: dict) -> str:
    """渲染句级层/段级层双层结构 scaffold（长文鲁棒性来源 · 段级结构锚定）。

    段级层数据 hint 取自 cross_chapter_diversity（ending_type_distribution=cliffhanger 落点 /
    transition_method*=POV/转场习惯）· 缺则给通用占位提示（不编造数值）。
    """
    ccd = style.get("cross_chapter_diversity") or {}
    ending = (ccd.get("ending_type_distribution") or {})
    transitions = (ccd.get("transition_methods_distribution")
                   or ccd.get("transition_method_distribution") or {})

    lines = []
    lines.append("## 🧱 双层风格结构（句级 + 段级 · 长文鲁棒性骨架）")
    lines.append("")
    lines.append("> 长文复刻鲁棒性来自**段级结构**而非纯句级模仿。本节把风格拆两层，"
                 "写作时**先定段级骨架（段落组织/落点/POV）再填句级笔法**。")
    lines.append("")
    # ── 句级层 ──
    lines.append("### 句级层（句式 · 口癖 · 禁用词）")
    lines.append("")
    lines.append("- **句式节奏**：长短句混搭，章内句长方差越大越像作者（见数值契约表「句长方差」）。")
    lines.append("- **口癖 / 虚词指纹**：高频虚词按数值契约表 Top-N 自然分布，不堆砌也不清零。")
    lines.append("- **禁用词**：AI 结构套话硬毙（与此同时/值得一提的是/不仅如此）；"
                 "工艺签名词有作者档时按作者实际频率（不一刀切）。")
    lines.append("")
    # ── 段级层 ──
    lines.append("### 段级层（段落组织 · cliffhanger 落点 · POV 切换）")
    lines.append("")
    lines.append("- **段落组织规律**：段长对齐数值契约表分位数 [p5,p50,p95]；"
                 "单句独行占比对齐契约表（节奏呼吸感）。")
    # cliffhanger 落点
    top_end = _top_dist(ending, 5)
    if top_end:
        cells = " · ".join(f"{k}（{round(v * 100, 1)}%）" for k, v in top_end)
        lines.append(f"- **cliffhanger 落点（章末类型 Top-{len(top_end)}）**：{cells}。"
                     "严禁连续 ≥2 章同款落点。")
    else:
        lines.append("- **cliffhanger 落点**：章末是钩子非收束（信息炸弹/对话悬念/动作留白等）；"
                     "严禁连续 ≥2 章同款（蒸馏未提供分布 → 按通用钩子类型轮换）。")
    # POV / 转场
    top_tr = _top_dist(transitions, 5)
    if top_tr:
        cells = " · ".join(f"{k}（{round(v * 100, 1)}%）" for k, v in top_tr)
        lines.append(f"- **POV 切换 / 转场习惯（Top-{len(top_tr)}）**：{cells}。"
                     "POV 不切是默认，切换走作者高频转场手法。")
    else:
        lines.append("- **POV 切换 / 转场习惯**：POV 不切是默认；"
                     "需切时走作者高频转场手法（蒸馏未提供分布 → 按场景边界自然切）。")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# [5] 组装 header + 幂等注入
# ══════════════════════════════════════════════════════════════════

def build_skill_header(style: dict) -> str:
    """组装完整 skill 头部块（数值契约表 + 双层 scaffold），夹在哨兵之间。"""
    contract = extract_contract_table(style)
    body = (render_contract_table_md(contract) + "\n\n---\n\n"
            + render_dual_layer_scaffold(style))
    return f"{SENTINEL_BEGIN}\n\n{body}\n\n{SENTINEL_END}"


def has_contract_block(skill_md: str) -> bool:
    """skill 文本是否已含 L3c 契约块（幂等判定）。"""
    return SENTINEL_BEGIN in skill_md and SENTINEL_END in skill_md


def inject_header_into_skill(skill_md: str, header: str) -> str:
    """把 header 幂等注入 skill 文本。

    规则：
      · 已有哨兵块 → 原地替换块内（不重复堆叠）。
      · 无哨兵块 → 插入到 YAML front-matter（--- ... ---）之后、正文之前；
        无 front-matter → 插到文件最顶端。
    """
    if has_contract_block(skill_md):
        pre = skill_md.split(SENTINEL_BEGIN, 1)[0]
        post = skill_md.split(SENTINEL_END, 1)[1]
        return pre + header + post

    # 定位 front-matter 结束位置
    lines = skill_md.split("\n")
    insert_at = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                insert_at = i + 1
                break
    head = "\n".join(lines[:insert_at])
    tail = "\n".join(lines[insert_at:])
    block = "\n" + header + "\n"
    if head:
        return head + "\n" + block + "\n" + tail.lstrip("\n")
    return block.lstrip("\n") + "\n" + tail.lstrip("\n")


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L3c skill 数值契约表 + 双层结构注入器")
    ap.add_argument("--style", required=True, help="作者风格_FINAL.json 路径")
    ap.add_argument("--skill", help="skill_vN.md 路径（--inject 时必填）")
    ap.add_argument("--render", action="store_true", help="只打印 header（不写文件）")
    ap.add_argument("--inject", action="store_true", help="幂等注入到 --skill 头部")
    args = ap.parse_args(argv)

    style_path = Path(args.style)
    if not style_path.exists():
        print(f"[FATAL] 作者风格档不存在: {style_path}", file=sys.stderr)
        return 2
    style = json.loads(style_path.read_text(encoding="utf-8"))
    header = build_skill_header(style)

    if args.render or not args.inject:
        print(header)
        return 0

    if not args.skill:
        print("[FATAL] --inject 需配 --skill <skill_vN.md>", file=sys.stderr)
        return 2
    skill_path = Path(args.skill)
    if not skill_path.exists():
        print(f"[FATAL] skill 文件不存在: {skill_path}", file=sys.stderr)
        return 2
    original = skill_path.read_text(encoding="utf-8")
    updated = inject_header_into_skill(original, header)
    skill_path.write_text(updated, encoding="utf-8")
    action = "替换" if has_contract_block(original) else "注入"
    print(f"[OK] L3c 契约块已{action}到 {skill_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
