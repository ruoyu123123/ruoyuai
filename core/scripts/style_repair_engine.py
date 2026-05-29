#!/usr/bin/env python3
"""
style_repair_engine.py — 中文小说风格修复引擎

用法：
  python style_repair_engine.py <章节txt> --mode analyze    # 只分析不修改
  python style_repair_engine.py <章节txt> --mode fix        # 程序化修复（默认三遍迭代）
  python style_repair_engine.py <章节txt> --mode guide      # 生成 LLM 修复指令
  python style_repair_engine.py <章节txt> --mode fix --max-passes 1  # 退回单遍
  python style_repair_engine.py <章节txt> --mode fix --no-iterative  # 同上

可程序化修复的问题：
  - 短句合并（提升逗句比）
  - 拟声格式规范化
  - 对话标签修复
  - 禁用词替换

需要 LLM 修复的问题（生成修复指令）：
  - 对话密度不足
  - 叙述段落需改写为对话

【三遍迭代法（P1-5，参考 oh-story story-deslop）】
mode=fix 默认走 apply_fixes_iterative：最多跑 max_passes 遍（默认 3），每遍跑一次
apply_fixes（merge_short_sentences → fix_onomatopoeia → fix_banned_words →
fix_ai_tags），若本遍文本无变化（收敛）则提前停止。意义：
  - 修复可能产生级联（如 merge 后产生新的 banned word 命中、replacement 引入新
    可修模式），多遍直到稳定才算真正「修到底」
  - 收敛 = 机械层全稳定，剩余问题需 LLM/agent 介入（落入 audit_hub 的 still_unfixed）
  - audit_hub._apply_deterministic_fix 通过子进程调本工具——本工具内部迭代收敛后
    再返回，audit_hub 的「重跑校验复核」逻辑保持不变（零耦合）
"""
from __future__ import annotations
import json
import re
import sys
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from style_analyzer import analyze_text, count_chinese

SENTENCE_END = re.compile(r"[。]")
ONOMATOPOEIA = re.compile(r"^[一-鿿]{1,6}[—]+[！!]?\s*$")
# 2026-05-29 修：原 DIALOGUE_LINE = r'^"[^"]*"' + startswith('"') 只认 ASCII 双引号，
# 中文弯引号 “”（U+201C/U+201D）和 「」 对话行全部漏识别。与 style_analyzer 的
# _Q_OPEN/_Q_CLOSE codepoint 区分对齐：按左右引号分别配对，不混用。
_Q_OPEN = '"“「『'   # ASCII " / 左弯双引号 U+201C / 「 / 『
# 对话行：开引号开头 + 配对的对应闭引号（"…" / “…” / 「…」 / 『…』 各自配对）
DIALOGUE_LINE = re.compile(
    r'^("[^"]*"|“[^”]*”|「[^」]*」|『[^』]*』)'
)


def _starts_with_open_quote(s: str) -> bool:
    """2026-05-29 修：判断行是否以任一开引号起头（取代只认 ASCII " 的 startswith('"')）"""
    return bool(s) and s[0] in _Q_OPEN
BANNED_WORDS = {
    "顿时": ["忽然间", "一下子", "猛地"],
    "紧锁": ["皱起", "拧在一起", "收紧"],
    "显然": ["明摆着", "一眼看出", ""],
    "淡淡": ["随口", "不咸不淡", "平平地"],
    "心中一凛": ["后背一紧", "脊柱发凉", "瞳孔一缩"],
    "眼中闪过一丝": ["目光一动", "眼皮跳了跳", ""],
    "微微挑眉": ["眉头一动", "挑了下眼角", ""],
    "嘴角勾起一抹": ["嘴角歪了一下", "撇了撇嘴", ""],
    "深吸一口气": ["胸口起伏了一下", "鼻子吸了一口", ""],
    "缓缓地说": ["开口", "说", "慢慢开口"],
    "沉吟片刻": ["想了想", "顿了一下", "停了几秒"],
    "与此同时": ["", "这时候", "同一刻"],
    "值得一提的是": ["", "", ""],
    "不仅如此": ["而且", "再说", ""],
    "事实上": ["其实", "说白了", ""],
    "波涛汹涌": ["翻涌", "起伏", "震荡"],
    "不容置疑": ["没得商量", "板上钉钉", "铁了心"],
}
AI_TAGS = {
    "淡淡地说": "说",
    "缓缓地说": "开口",
    "微微一笑道": "笑了一声",
    "沉吟片刻后说道": "想了想",
}


def merge_short_sentences(text: str) -> str:
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped or ONOMATOPOEIA.match(stripped) or _starts_with_open_quote(stripped):  # 2026-05-29 修
            result.append(line)
            continue
        parts = SENTENCE_END.split(stripped)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            result.append(line)
            continue
        merged = []
        i = 0
        while i < len(parts):
            current = parts[i]
            cn = count_chinese(current)
            if cn <= 12 and i + 1 < len(parts):
                next_part = parts[i + 1]
                next_cn = count_chinese(next_part)
                if next_cn <= 15 and not _starts_with_open_quote(next_part):  # 2026-05-29 修
                    merged.append(current + "，" + next_part)
                    i += 2
                    continue
            merged.append(current)
            i += 1
        new_line = "。".join(merged)
        end_marks = ("。", "！", "？", "!", "?", "”", "」", "』")  # 2026-05-29 修：补中文闭引号
        if new_line and not new_line.endswith(end_marks):
            new_line += "。"
        result.append(new_line)
    return "\n".join(result)


def fix_onomatopoeia(text: str) -> str:
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        match = re.match(r"^([一-鿿]{1,6})([—]+)\s*$", stripped)
        if match:
            word, dashes = match.group(1), match.group(2)
            result.append(f"{word}{dashes}！")
            continue
        result.append(line)
    return "\n".join(result)


def fix_banned_words(text: str) -> tuple[str, list[dict]]:
    fixes = []
    for banned, replacements in BANNED_WORDS.items():
        replacement = replacements[0] if replacements[0] else ""
        if banned in text:
            count = text.count(banned)
            if replacement:
                text = text.replace(banned, replacement)
                fixes.append({"word": banned, "replacement": replacement, "count": count})
            else:
                fixes.append({"word": banned, "replacement": "(deleted)", "count": count, "note": "需手动确认删除位置"})
    return text, fixes


def fix_ai_tags(text: str) -> tuple[str, list[dict]]:
    fixes = []
    for tag, replacement in AI_TAGS.items():
        if tag in text:
            count = text.count(tag)
            text = text.replace(tag, replacement)
            fixes.append({"tag": tag, "replacement": replacement, "count": count})
    return text, fixes


def generate_dialogue_guide(text: str, profile: dict) -> list[dict]:
    dialogue_ratio = profile.get("dialogue_ratio", 0)
    target = 0.40
    if dialogue_ratio >= target:
        return []

    lines = text.split("\n")
    narrative_blocks = []
    current_block_start = -1
    current_block_lines = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_dialogue = _starts_with_open_quote(stripped) or ONOMATOPOEIA.match(stripped)  # 2026-05-29 修
        if is_dialogue:
            if current_block_lines >= 3:
                narrative_blocks.append({
                    "start_line": current_block_start + 1,
                    "end_line": i,
                    "length": current_block_lines,
                    "preview": lines[current_block_start].strip()[:50] if current_block_start >= 0 else "",
                })
            current_block_start = -1
            current_block_lines = 0
        else:
            if current_block_start == -1:
                current_block_start = i
            current_block_lines += 1

    if current_block_lines >= 3:
        narrative_blocks.append({
            "start_line": current_block_start + 1,
            "end_line": len(lines),
            "length": current_block_lines,
            "preview": lines[current_block_start].strip()[:50] if current_block_start >= 0 else "",
        })

    narrative_blocks.sort(key=lambda b: b["length"], reverse=True)

    guides = []
    deficit_chars = int(count_chinese(text) * (target - dialogue_ratio))
    for block in narrative_blocks[:5]:
        guides.append({
            "type": "dialogue_insertion",
            "location": f"第 {block['start_line']}-{block['end_line']} 行",
            "narrative_lines": block["length"],
            "preview": block["preview"],
            "instruction": f"这段 {block['length']} 行叙述过长。改写为：前 2-3 行保留叙述，中间插入角色对话（至少 3 轮对白），末尾用对话或动作收。目标：此段对话占比 ≥ 50%。",
        })
    if guides:
        guides.insert(0, {
            "type": "summary",
            "current_dialogue_ratio": f"{dialogue_ratio:.1%}",
            "target": f"{target:.0%}",
            "deficit_chars": deficit_chars,
            "instruction": f"当前对话 {dialogue_ratio:.1%}，需增加约 {deficit_chars} 字对话内容。以下是最长的叙述段落，优先改写为对话驱动。",
        })
    return guides


def analyze_repair_potential(text: str) -> dict:
    profile = analyze_text(text)
    issues = []
    repairs_programmatic = []
    repairs_llm = []

    dr = profile["dialogue_ratio"]
    if dr < 0.30:
        issues.append({"dim": "dialogue_ratio", "value": dr, "target": 0.40, "severity": "SEVERE"})
        repairs_llm.append("对话密度严重不足，需 LLM 改写叙述段为对话")

    cpr = profile["punctuation_density_per_1000"]["comma_period_ratio"]
    if cpr < 1.5:
        issues.append({"dim": "comma_period_ratio", "value": cpr, "target": 1.5, "severity": "SEVERE" if cpr < 0.8 else "MODERATE"})
        repairs_programmatic.append("短句合并（merge_short_sentences）")

    usr = profile["ultra_short_para_ratio"]
    if usr > 0.30:
        issues.append({"dim": "ultra_short_para_ratio", "value": usr, "target": 0.30, "severity": "MODERATE"})

    bw = profile["banned_word_hits"]
    if bw:
        issues.append({"dim": "banned_words", "value": len(bw), "target": 0, "severity": "HIGH"})
        repairs_programmatic.append("禁用词替换（fix_banned_words）")

    at = profile["ai_dialogue_tag_hits"]
    if at:
        issues.append({"dim": "ai_tags", "value": len(at), "target": 0, "severity": "HIGH"})
        repairs_programmatic.append("AI 对话标签替换（fix_ai_tags）")

    return {
        "profile_before": profile,
        "issues": issues,
        "repairs_programmatic": repairs_programmatic,
        "repairs_llm_needed": repairs_llm,
        "can_fix_programmatically": len(repairs_programmatic),
        "needs_llm": len(repairs_llm),
    }


def apply_fixes(text: str) -> tuple[str, dict]:
    """单遍机械修复：merge → onomatopoeia → banned → AI tags 四步顺序执行。"""
    log = {"steps": [], "banned_fixes": [], "tag_fixes": []}

    original = text
    text = merge_short_sentences(text)
    if text != original:
        log["steps"].append("merge_short_sentences")

    original = text
    text = fix_onomatopoeia(text)
    if text != original:
        log["steps"].append("fix_onomatopoeia")

    text, bfixes = fix_banned_words(text)
    if bfixes:
        log["steps"].append("fix_banned_words")
        log["banned_fixes"] = bfixes

    text, tfixes = fix_ai_tags(text)
    if tfixes:
        log["steps"].append("fix_ai_tags")
        log["tag_fixes"] = tfixes

    return text, log


def apply_fixes_iterative(text: str, max_passes: int = 3) -> tuple[str, list[dict]]:
    """三遍迭代修复（P1-5，参考 oh-story 三遍去 AI 法）。

    每遍跑一次 apply_fixes，若文本无变化（收敛）则提前停止。
    返回 (final_text, per_pass_logs)；per_pass_logs 列表，每项是一遍的 log dict
    扩展了 `pass` 编号 + `converged` 布尔标志。

    收敛意义：mechanical 层全稳定，剩余问题需 LLM/agent 介入。
    级联场景示例：merge_short_sentences 合并后产生新 banned word 命中，
    fix_banned_words 替换后引入的文本恰巧含另一个待替换词——迭代直到稳定。
    """
    per_pass: list[dict] = []
    prev_text = text
    for pass_num in range(1, max(1, max_passes) + 1):
        new_text, log = apply_fixes(prev_text)
        log["pass"] = pass_num
        log["converged"] = (new_text == prev_text)
        per_pass.append(log)
        if new_text == prev_text:
            break  # 提前收敛 —— 本遍无任何修改，再跑徒劳
        prev_text = new_text
    return prev_text, per_pass


def main():
    if len(sys.argv) < 2:
        print("用法: python style_repair_engine.py <文件> --mode analyze|fix|guide")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    mode = "analyze"
    output_path = None
    max_passes = 3       # P1-5：三遍迭代法的默认最大遍数
    iterative = True     # P1-5：fix 模式默认走迭代；--no-iterative 退回单遍
    for i in range(2, len(sys.argv)):
        if sys.argv[i] == "--mode" and i + 1 < len(sys.argv):
            mode = sys.argv[i + 1]
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_path = Path(sys.argv[i + 1])
        elif sys.argv[i] == "--max-passes" and i + 1 < len(sys.argv):
            try:
                max_passes = max(1, int(sys.argv[i + 1]))
            except ValueError:
                pass
        elif sys.argv[i] == "--no-iterative":
            iterative = False

    text = file_path.read_text(encoding="utf-8")

    if mode == "analyze":
        result = analyze_repair_potential(text)
        issues = result["issues"]
        print(f"风格修复分析: {file_path.name}")
        print(f"{'═' * 40}")
        for issue in issues:
            sev = issue["severity"]
            icon = "🔴" if sev == "SEVERE" else "🟡" if sev == "HIGH" else "🟠"
            print(f"  {icon} {issue['dim']}: {issue['value']:.2f} → 目标 {issue['target']}")
        print(f"\n程序化可修: {result['can_fix_programmatically']} 项")
        for r in result["repairs_programmatic"]:
            print(f"  ✅ {r}")
        print(f"需 LLM 修复: {result['needs_llm']} 项")
        for r in result["repairs_llm_needed"]:
            print(f"  🤖 {r}")

    elif mode == "fix":
        profile_before = analyze_text(text)
        # P1-5：默认走三遍迭代；--no-iterative 退回单遍
        if iterative:
            fixed_text, per_pass = apply_fixes_iterative(text, max_passes=max_passes)
        else:
            single_text, single_log = apply_fixes(text)
            fixed_text = single_text
            single_log["pass"] = 1
            single_log["converged"] = (single_text == text)
            per_pass = [single_log]
        profile_after = analyze_text(fixed_text)

        suffix = file_path.suffix
        out = output_path or file_path.with_name(file_path.stem + "_fixed" + suffix)
        out.write_text(fixed_text, encoding="utf-8")

        cpr_before = profile_before["punctuation_density_per_1000"]["comma_period_ratio"]
        cpr_after = profile_after["punctuation_density_per_1000"]["comma_period_ratio"]
        bw_before = len(profile_before["banned_word_hits"])
        bw_after = len(profile_after["banned_word_hits"])

        # 收敛信息：实际跑了几遍 / 是否提前收敛
        actual_passes = len(per_pass)
        converged_at_pass = next((p["pass"] for p in per_pass if p["converged"]), None)

        print(f"修复完成: {file_path.name} → {out.name}")
        print(f"  逗句比: {cpr_before:.2f} → {cpr_after:.2f}")
        print(f"  禁用词: {bw_before} → {bw_after}")
        print(f"  段落数: {profile_before['paragraph_count']} → {profile_after['paragraph_count']}")
        if iterative:
            print(f"  迭代:   跑 {actual_passes}/{max_passes} 遍，"
                  f"{'第 ' + str(converged_at_pass) + ' 遍收敛' if converged_at_pass else '未收敛（达最大遍数）'}")
            for p in per_pass:
                if p["steps"]:
                    print(f"    Pass {p['pass']}: {', '.join(p['steps'])}")
        else:
            print(f"  步骤: {', '.join(per_pass[0]['steps'])}")

        report = {
            "before": {"comma_period_ratio": cpr_before, "banned_words": bw_before},
            "after": {"comma_period_ratio": cpr_after, "banned_words": bw_after},
            "iterative": iterative,
            "max_passes": max_passes if iterative else 1,
            "actual_passes": actual_passes,
            "converged_at_pass": converged_at_pass,
            "per_pass": per_pass,
        }
        json_out = out.with_suffix(".repair.json")
        json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    elif mode == "guide":
        profile = analyze_text(text)
        guides = generate_dialogue_guide(text, profile)
        if not guides:
            print("对话密度已达标，无需修复。")
            return

        print(f"对话密度修复指令: {file_path.name}")
        print(f"{'═' * 50}")
        for g in guides:
            if g["type"] == "summary":
                print(f"\n📊 当前对话: {g['current_dialogue_ratio']} → 目标: {g['target']}")
                print(f"   需增加约 {g['deficit_chars']} 字对话")
                print(f"\n📝 改写建议（按叙述块长度排序）:")
            else:
                print(f"\n  ▸ {g['location']}（{g['narrative_lines']} 行叙述）")
                print(f"    预览: {g['preview']}")
                print(f"    指令: {g['instruction']}")

        if output_path:
            Path(output_path).write_text(
                json.dumps(guides, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n指令已保存: {output_path}")


if __name__ == "__main__":
    main()
