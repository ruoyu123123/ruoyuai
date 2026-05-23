"""input_sanitizer.py — 输入 prompt injection 防御（v21 P6.1 新增）

OWASP LLM01（2026 #1 风险）：prompt injection / jailbreak。

业界 PromptGuard / PromptArmor 平均 67% 命中。本脚本是轻量正则+关键词版，
适合扫"用户提供的外部内容"（蒸馏样本/调研 URL/上传文本/角色档输入）。

3 类常见 injection pattern：
- DIRECT_INSTRUCTION：直接命令式（"ignore previous"/"忽略上述指令"/"act as"）
- ROLE_HIJACK：角色劫持（"You are now"/"现在你是"/"forget you are"）
- BOUNDARY_BREAK：试图越界访问系统指令（"reveal your prompt"/"显示你的指令"）

输出：标记 risk 等级 + 高亮可疑行
退出码：0 健康 / 1 advisory / 2 warning / 3 risky 应拒绝
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 3 类 injection pattern
INJECTION_PATTERNS = {
    "DIRECT_INSTRUCTION": [
        r"ignore\s+(previous|above|prior|all)\s+(instructions?|prompts?)",
        r"忽略\s*(上述|以上|之前|所有)\s*(指令|提示|命令)",
        r"disregard\s+(your|the)\s+(prompt|instruction)",
        r"以下\s*指令\s*优先",
        r"new\s+instruction\s*:",
        r"system\s*:\s*",
        r"### system",
        r"\[SYSTEM\]",
    ],
    "ROLE_HIJACK": [
        r"you\s+are\s+now\s+(an?\s+)?",
        r"现在\s*你\s*是",
        r"forget\s+(you\s+are|your\s+role)",
        r"忘记\s*你\s*(是|的角色)",
        r"act\s+as\s+(?!if|though)",
        r"pretend\s+(to\s+be|you\s+are)",
        r"假装\s*(你是|成为)",
        r"roleplay\s+as",
    ],
    "BOUNDARY_BREAK": [
        r"reveal\s+(your|the)\s+(prompt|instruction|system)",
        r"显示\s*(你的|系统)\s*(指令|提示|prompt)",
        r"what\s+(are\s+)?your\s+(initial|original|system)\s+(instructions?|prompt)",
        r"output\s+(your|the)\s+system\s+prompt",
        r"输出\s*系统\s*提示",
        r"repeat\s+your\s+(instructions?|prompt)",
        r"打印\s*你的\s*指令",
        r"\[\[\s*end\s*system\s*\]\]",
        r"```\s*system\s*prompt",
    ],
}


def scan(text: str) -> dict:
    """返回 {category: [matched_patterns]} 字典 + total_hits + risk_level"""
    out = {cat: [] for cat in INJECTION_PATTERNS}
    if not text:
        return {"hits_by_category": out, "total_hits": 0, "risk_level": "clean", "matched_lines": []}
    matched_lines = []
    text_lower = text.lower()
    for cat, patterns in INJECTION_PATTERNS.items():
        for pat in patterns:
            for m in re.finditer(pat, text_lower):
                # 找到所在行
                line_start = text_lower.rfind("\n", 0, m.start()) + 1
                line_end = text_lower.find("\n", m.end())
                if line_end == -1:
                    line_end = len(text)
                line = text[line_start:line_end].strip()
                out[cat].append({
                    "pattern": pat,
                    "matched_text": text[m.start():m.end()],
                    "line_excerpt": line[:120],
                })
                matched_lines.append(line[:120])
                break  # 每条 pattern 命中 1 次即可

    total = sum(len(v) for v in out.values())
    # risk_level: 0=clean / 1=advisory(单个 hit) / 2=warning(2+) / 3=risky(3+ 或含 BOUNDARY_BREAK)
    if total == 0:
        risk = "clean"
    elif out["BOUNDARY_BREAK"] or total >= 3:
        risk = "risky"
    elif total >= 2:
        risk = "warning"
    else:
        risk = "advisory"

    return {
        "hits_by_category": out,
        "total_hits": total,
        "risk_level": risk,
        "matched_lines": matched_lines,
    }


def sanitize(text: str) -> str:
    """简单 sanitize：把识别到的可疑行包裹 [⚠️ POTENTIAL INJECTION] 标记，
    供下游 LLM 警觉但不直接删（保留语义）。"""
    if not text:
        return text
    result = scan(text)
    if result["risk_level"] == "clean":
        return text
    out = text
    for cat, hits in result["hits_by_category"].items():
        for h in hits:
            matched = h["matched_text"]
            # 替换为带警告标记的版本
            replacement = f"[⚠️ POTENTIAL_INJECTION_DETECTED_{cat}: {matched}]"
            out = out.replace(matched, replacement, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_file_or_text", help="文件路径，或前缀 'text:' 后接文本")
    ap.add_argument("--sanitize", action="store_true", help="输出 sanitized 版本")
    ap.add_argument("--json-output", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args()

    if args.input_file_or_text.startswith("text:"):
        text = args.input_file_or_text[5:]
        source = "<inline text>"
    else:
        p = Path(args.input_file_or_text)
        if not p.exists():
            print(f"[ERROR] 文件不存在: {p}", file=sys.stderr)
            sys.exit(2)
        text = p.read_text(encoding="utf-8")
        source = str(p)

    result = scan(text)
    result["source"] = source
    result["scan_ts"] = datetime.now().isoformat(timespec="seconds")

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[input_sanitizer] source={source}")
        print(f"  risk_level: {result['risk_level']}")
        print(f"  total_hits: {result['total_hits']}")
        for cat, hits in result["hits_by_category"].items():
            if hits:
                print(f"  {cat}: {len(hits)} 命中")
                for h in hits[:3]:
                    print(f"    - {h['matched_text']!r} in line: {h['line_excerpt']!r}")

    if args.sanitize:
        sanitized = sanitize(text)
        print("\n=== SANITIZED ===")
        print(sanitized[:1000])

    # exit code 映射
    risk_to_exit = {"clean": 0, "advisory": 1, "warning": 2, "risky": 3}
    sys.exit(risk_to_exit[result["risk_level"]])


if __name__ == "__main__":
    main()
