"""chapter_splitter.py — DCAS 章节自动截断（v18 接入 chapter_io）

【设计修正】v17.8 原把 splitter 设计成 LLM agent 是过度设计——
7 维度评分全是确定性规则，不需要 LLM 语言理解。改为纯 Python 脚本：
  - 不依赖 agent 加载
  - 确定性、可单元测试
  - 速度快（毫秒级）

读 writer 生成的 6000+ 字双章草稿，自动评分候选截断点，切成 ch + ch+1 pre_opening。

【v18 改动】正文/数据已分离：
  - 草稿正文从 cio.read_body() 读（v18 纯正文稿 / 旧混合稿通吃）
  - 截断后只重写正文 txt（cio.write_body）——CHANGES 全部归属 ch，splitter 不碰
  - pre_opening 仍写到 第(N+1)章/.pre_opening.txt

用法:
    python chapter_splitter.py <项目路径> <章节号> [--target 3000] [--tolerance 500] [--dry-run]

退出码: 0 成功 / 1 草稿不足 / 2 致命错误
"""

import sys
import re
import json
from pathlib import Path

# v18：统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio


def strip_title(body: str):
    """分离章节标题行和正文。返回 (title_line, content)。"""
    lines = body.split("\n")
    title = ""
    rest_start = 0
    for i, ln in enumerate(lines):
        if re.match(r"^第\d+章", ln.strip()):
            title = ln
            rest_start = i + 1
            break
    content = "\n".join(lines[rest_start:]).lstrip("\n")
    return title, content


def split_paragraphs_with_offset(content: str):
    """切段，返回 [(para_text, char_offset_at_end, cumulative_word_count)]."""
    paras = []
    cumulative = 0
    offset = 0
    for chunk in re.split(r"(\n\s*\n)", content):
        if chunk.strip() == "" and "\n" in chunk:
            offset += len(chunk)
            continue
        if not chunk.strip():
            offset += len(chunk)
            continue
        wc = len(chunk.replace(" ", "").replace("\n", ""))
        cumulative += wc
        offset += len(chunk)
        paras.append({
            "text": chunk.strip(),
            "char_offset_end": offset,
            "cumulative_word_count": cumulative,
        })
    return paras


# ============ 7 维度评分 ============

ONOMATOPOEIA = re.compile(r"(咯|啪|嗒|哒|轰|咚|哗|砰|咳|噗|滋|嘎|吱|咔)——")
DASH_END = re.compile(r"[—…]\s*$")
SCENE_BREAK_START = re.compile(r"^(——|\*\s*\*|\* \* \*)")
CONTINUE_ACTION = re.compile(r"^[^\n]{0,8}(回头|抬头|睁开|醒来|想起|站起|转身)")
CHAR_NAMES = ["克莱", "莫顿", "艾尔莎", "伊森", "老亨利", "七夜伯爵"]
DIALOGUE_OPEN = re.compile(r'["「]')
DIALOGUE_CLOSE = re.compile(r'["」]')
PSYCH_KW = re.compile(r"(他想|她想|他记得|他觉得|他不知)")
REVEAL_KW = re.compile(r"(原来|真相是|其实是|竟然是)")


def score_split_point(paras, idx, target, tolerance):
    """对"在 paras[idx] 之后切割"评分。idx 是切断点前最后一段的下标。"""
    if idx < 0 or idx >= len(paras):
        return None
    before = paras[idx]
    after = paras[idx + 1] if idx + 1 < len(paras) else None
    wc = before["cumulative_word_count"]

    # 字数接近度
    score = 0
    reasons = []
    if target - 300 <= wc <= target + 300:
        score += 5
        reasons.append(f"字数 {wc} 在 ±300 (+5)")
    elif target - tolerance <= wc <= target + tolerance:
        score += 3
        reasons.append(f"字数 {wc} 在 ±{tolerance} (+3)")
    else:
        return None  # 字数太偏，不进候选

    before_text = before["text"]
    # 钩子张力
    last_sentence = before_text.split("。")[-1] or before_text.split("。")[-2] if "。" in before_text else before_text
    if ONOMATOPOEIA.search(before_text[-30:]):
        score += 3
        reasons.append("末尾拟声 (+3)")
    if DASH_END.search(before_text):
        score += 3
        reasons.append("末尾破折号/省略号 (+3)")
    if len(before_text.split("\n")[-1]) <= 12:
        score += 2
        reasons.append("末段独立短句 (+2)")

    # 场景边界 + 接续
    if after:
        after_text = after["text"]
        if SCENE_BREAK_START.search(after_text):
            score += 5
            reasons.append("后段场景分隔符 (+5)")
        if CONTINUE_ACTION.search(after_text):
            score += 3
            reasons.append("后段接续动作 (+3)")
        for name in CHAR_NAMES:
            if after_text.startswith(name):
                score += 2
                reasons.append(f"后段角色名'{name}'开头 (+2)")
                break

    # avoid 项
    # 对话引号未闭（before 段引号开闭不平衡）
    opens = len(DIALOGUE_OPEN.findall(before_text))
    closes = len(DIALOGUE_CLOSE.findall(before_text))
    if opens != closes:
        score -= 50
        reasons.append("⚠️ 对话引号未闭 (-50)")
    # 破折号中间
    if before_text.rstrip().endswith("——") and after and not after["text"].startswith(("他", "她", "克莱")):
        score -= 30
        reasons.append("⚠️ 破折号悬空 (-30)")
    # 心理戏中间（before 末段是心理 + after 首段也是心理）
    if PSYCH_KW.search(before_text[-50:]) and after and PSYCH_KW.search(after["text"][:50]):
        score -= 10
        reasons.append("⚠️ 心理戏中间 (-10)")
    # 后段直接揭谜底
    if after and REVEAL_KW.search(after["text"][:60]):
        score -= 5
        reasons.append("⚠️ 后段直接揭谜底 (-5)")

    return {
        "para_idx": idx,
        "char_offset": before["char_offset_end"],
        "word_count_before": wc,
        "score": score,
        "reasons": reasons,
    }


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    ch = int(args[1])
    target = 3000
    tolerance = 500
    dry_run = "--dry-run" in args
    # v24 黄金三章倒叙模式参数
    narrative_mode = "linear"
    for i, a in enumerate(args):
        if a == "--target" and i + 1 < len(args):
            target = int(args[i + 1])
        if a == "--tolerance" and i + 1 < len(args):
            tolerance = int(args[i + 1])
        if a == "--narrative-mode" and i + 1 < len(args):
            narrative_mode = args[i + 1]  # linear | in_medias_res

    # v24 黄金三章倒叙模式说明（实际重组在 novel-chapter-splitter agent ECAS 模式实现，
    # 本 DCAS 脚本仅暴露参数接口，DCAS 双章模式不做倒叙重组）
    if narrative_mode == "in_medias_res":
        print(f"[INFO] narrative_mode=in_medias_res 已识别 ·"
              f" DCAS 双章模式不实现倒叙重组，建议改用 ECAS 模式 + novel-chapter-splitter agent",
              file=sys.stderr)

    # 找草稿正文文件（v18：cio 兼容 4 布局 + 旧平铺）
    draft_path = cio.find_body_file(project_root, ch)
    if not draft_path:
        print(f"[FATAL] 找不到 ch{ch} 草稿", file=sys.stderr)
        sys.exit(2)

    # v18：正文经 cio 读取（纯正文稿直接读，旧混合稿自动剥离 CHANGES 段）
    body = cio.read_body(project_root, ch)
    title, content = strip_title(body)
    total_wc = cio.count_words(content)

    if total_wc < target + tolerance:
        print(f"[FATAL] 草稿仅 {total_wc} 字 < {target}+{tolerance}，不足双章长度。"
              f"不切割——可能 writer 没按 DCAS 模式生成。", file=sys.stderr)
        sys.exit(1)

    paras = split_paragraphs_with_offset(content)
    candidates = []
    for idx in range(len(paras) - 1):
        r = score_split_point(paras, idx, target, tolerance)
        if r:
            candidates.append(r)

    if not candidates:
        print(f"[FATAL] 无合格候选截断点（字数都偏离 {target}±{tolerance}）", file=sys.stderr)
        sys.exit(1)

    # v17.8 修正：score 降序 + 字数接近 target 做 tie-break（避免并列）
    candidates.sort(key=lambda x: (-x["score"], abs(x["word_count_before"] - target)))
    top1 = candidates[0]
    top2 = candidates[1] if len(candidates) > 1 else None
    # uncertainty 改判：score 相同但已用字数 tie-break → 不算 uncertain
    uncertainty = bool(top2 and top1["score"] == top2["score"]
                       and abs(top1["word_count_before"] - target) == abs(top2["word_count_before"] - target))

    # 切割
    split_idx = top1["para_idx"]
    before_paras = paras[:split_idx + 1]
    after_paras = paras[split_idx + 1:]
    before_content = "\n\n".join(p["text"] for p in before_paras)
    after_content = "\n\n".join(p["text"] for p in after_paras)

    # v18：正文文件只放纯正文（title + 截断前内容）；CHANGES 全部归属 ch，
    # 留在 第NNN章_changes.json，splitter 不切、不动它。
    ch_body = f"{title}\n\n{before_content}" if title else before_content
    pre_opening = after_content

    report = {
        "schema_version": "1.0",
        "scanner": "chapter_splitter",
        "current_chapter": ch,
        "next_chapter": ch + 1,
        "draft_total_words": total_wc,
        "candidates_count": len(candidates),
        "chosen_split_point": {
            "para_idx": top1["para_idx"],
            "word_count_before": top1["word_count_before"],
            "word_count_after": total_wc - top1["word_count_before"],
            "score": top1["score"],
            "reasons": top1["reasons"],
        },
        "top3_candidates": [
            {"para_idx": c["para_idx"], "wc_before": c["word_count_before"], "score": c["score"]}
            for c in candidates[:3]
        ],
        "uncertainty_flag": bool(uncertainty),
        "dry_run": dry_run,
    }
    # v17.8 修正：草稿远超 2×target 时，pre_opening 会偏长
    pre_opening_wc = total_wc - top1["word_count_before"]
    if pre_opening_wc > target + tolerance:
        report["pre_opening_oversized"] = True
        report["pre_opening_word_count"] = pre_opening_wc
        report["note"] = (
            f"pre_opening {pre_opening_wc} 字 > {target}+{tolerance}。"
            f"writer 在 DCAS 模式写超了（草稿 {total_wc} 字 vs target ~6500）。"
            f"ch{ch+1} 启动时可直接用 pre_opening 作为完整章节，或再次 DCAS 切割"
        )

    if not dry_run:
        # 写 ch 正文（截断前 · 纯正文）——cio.write_body 落到标准嵌套路径
        written_body = cio.write_body(project_root, ch, ch_body)
        # 写 ch+1 pre_opening
        next_dir = project_root / "章节" / f"第{ch+1:03d}章"
        next_dir.mkdir(parents=True, exist_ok=True)
        pre_path = next_dir / ".pre_opening.txt"
        pre_path.write_text(pre_opening, encoding="utf-8")
        report["files_written"] = [
            str(written_body.relative_to(project_root)),
            str(pre_path.relative_to(project_root)),
        ]

    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
