"""ai_wrapper.py — v22.gov 脚本输出 AI 二次分析层

为「死板规则脚本」加 AI 验证层，避免规则误判（如 cluster_segmenter 把强切类型
判断错、arc_aggregator 把情绪值算偏、naming_convention 把音译名误归 chinese）。

业界依据（详见 .research_cache/inspiration_hooks_reflect_aiwrap_*.md）：
- LLM-as-judge over deterministic output（业界 hybrid pipeline 标准）
- ReAct / Reflexion: 行动后反思 → 修正
- Voyager: 工具结果 LLM 二次验证

设计原则：
1. 脚本结果作为「初判」，AI 作为「复核」
2. AI 只能 **建议覆盖**（输出 override_recommendation），不能直接改原文件
3. 建议写到 `<script_output>.ai_review.json`，主代理可选采纳
4. fallback：gen-model 不可用 / API 失败 → 透传原始结果不阻塞

用法：
    # 方式 1: 包装已有脚本输出
    python ai_wrapper.py --input <脚本输出 JSON> --context <相关上下文> --task <任务描述>

    # 方式 2: 单文件 self-review
    python ai_wrapper.py --review-file <JSON 路径>

输出：
    <input>.ai_review.json
    {
      "original_verdict": <原脚本判断>,
      "ai_verdict": <AI 复核结论>,
      "agreement": "agree" | "disagree" | "partial",
      "confidence": 0-1,
      "override_recommendation": <如不同意，建议改为什么>,
      "reasoning": <AI 的推理过程 < 300 字>,
      "_meta": {...}
    }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def safe_load_gen_model_loader():
    """容错导入 gen_model_loader（无配置时 fallback 跳过 AI 复核）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gen_model_loader import GenModelLoader, GenModelConfigError
        return GenModelLoader, GenModelConfigError
    except Exception as e:
        print(f"[ai_wrapper] gen_model_loader unavailable: {e}", file=sys.stderr)
        return None, None


def build_review_prompt(original_data: dict, task: str, context: str = "") -> tuple[str, str]:
    """构造 LLM 二次复核 prompt。"""
    system = """你是一位「脚本输出复核员」。

你的工作：拿到一个**确定性脚本（rule-based / 启发式）**的输出 JSON，判断这个输出
**是否合理**。脚本可能：
- 用错关键词匹配（如把音译名误判成中文名）
- 用错阈值（如切分块太严或太松）
- 漏掉特例（如长尾章节、特殊场景）
- 字段映射错（如把 dict 当 string 处理）

你的输出 JSON：
```json
{
  "agreement": "agree | disagree | partial",
  "confidence": 0-1,
  "override_recommendation": {"...": "..."} | null,
  "reasoning": "< 300 字推理：哪里合理 / 哪里不合理 / 为什么"
}
```

**规则**：
- agree = 输出完全合理，无需改动
- disagree = 输出明显错误，必须改（含 override_recommendation 具体值）
- partial = 部分合理，建议补充某些字段
- 严格基于 JSON 内容判断，不要凭空推测
- 不确定时 confidence 标低（< 0.5）让主代理审"""

    user_parts = [f"# 任务描述\n{task}\n"]
    if context:
        user_parts.append(f"# 上下文\n{context[:2000]}\n")
    user_parts.append(f"# 原脚本输出 JSON\n```json\n{json.dumps(original_data, ensure_ascii=False, indent=2)[:8000]}\n```")
    user_parts.append("\n请按规定的 JSON 格式输出复核结论。")
    return system, "\n".join(user_parts)


def call_gen_model(system: str, user: str) -> dict | None:
    """调 gen-model 做 AI 复核。fallback 安全：失败返回 None。"""
    GenModelLoader, GenModelConfigError = safe_load_gen_model_loader()
    if not GenModelLoader:
        return None

    try:
        loader = GenModelLoader()
        profile = loader.get_active_profile()
    except Exception as e:
        print(f"[ai_wrapper] gen-model profile load failed: {e}", file=sys.stderr)
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url)
        resp = client.chat.completions.create(
            model=profile.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=2000,
            temperature=0.3,    # 复核任务低 temp 求稳
        )
        raw = resp.choices[0].message.content.strip()
        # 尝试解析 JSON
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group(0))
        return {"raw_text": raw, "_parse_failed": True}
    except Exception as e:
        print(f"[ai_wrapper] API call failed: {e}", file=sys.stderr)
        return None


def review_output(original_path: Path, task: str, context: str = "") -> dict:
    """复核单个脚本输出 JSON。"""
    if not original_path.exists():
        return {"error": f"file not found: {original_path}"}
    try:
        original = json.loads(original_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"invalid JSON: {e}"}

    system, user = build_review_prompt(original, task, context)
    ai_result = call_gen_model(system, user)

    review = {
        "original_path": str(original_path),
        "original_verdict_summary": _summarize_original(original),
        "ai_review_available": ai_result is not None,
        "task": task,
        "reviewed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if ai_result:
        review.update({
            "agreement": ai_result.get("agreement", "unknown"),
            "confidence": ai_result.get("confidence", 0),
            "override_recommendation": ai_result.get("override_recommendation"),
            "reasoning": ai_result.get("reasoning", ""),
            "_raw_ai": ai_result,
        })
    else:
        review.update({
            "agreement": "skipped",
            "confidence": 0,
            "reasoning": "AI 复核不可用（gen-model 未配置或调用失败），原始输出透传",
        })

    return review


def _summarize_original(data: dict) -> str:
    """给原脚本输出一个 ≤ 200 字摘要（供 AI 复核 prompt 用）。"""
    if not isinstance(data, dict):
        return str(data)[:200]
    keys = list(data.keys())[:10]
    summary_parts = []
    for k in keys:
        v = data[k]
        if isinstance(v, (int, float, str, bool)):
            summary_parts.append(f"{k}={str(v)[:30]}")
        elif isinstance(v, list):
            summary_parts.append(f"{k}=[len={len(v)}]")
        elif isinstance(v, dict):
            summary_parts.append(f"{k}={{keys={list(v.keys())[:5]}}}")
    return " | ".join(summary_parts)[:200]


def main():
    parser = argparse.ArgumentParser(description="ai_wrapper v22.gov · 脚本输出 AI 二次分析")
    parser.add_argument("--input", help="原脚本输出 JSON 路径")
    parser.add_argument("--review-file", help="同 --input（别名）")
    parser.add_argument("--context", default="", help="相关上下文文本（可选）")
    parser.add_argument("--context-file", help="从文件读上下文（可选）")
    parser.add_argument("--task", required=True, help="任务描述（告诉 AI 这个脚本在做什么）")
    parser.add_argument("--out", help="输出复核 JSON 路径（默认 <input>.ai_review.json）")
    args = parser.parse_args()

    input_path = Path(args.input or args.review_file or "").resolve()
    if not input_path or not str(input_path):
        print("[error] --input 或 --review-file 必填", file=sys.stderr)
        sys.exit(2)

    context = args.context
    if args.context_file:
        try:
            context = Path(args.context_file).read_text(encoding="utf-8")[:4000]
        except Exception:
            pass

    review = review_output(input_path, args.task, context)
    out_path = Path(args.out) if args.out else input_path.with_suffix(input_path.suffix + ".ai_review.json")
    out_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {out_path}")
    if "agreement" in review:
        print(f"     agreement={review['agreement']} confidence={review.get('confidence')}")
        if review.get("agreement") == "disagree":
            print(f"     ⚠️ AI 不同意 — 建议: {str(review.get('override_recommendation'))[:120]}")
        if review.get("reasoning"):
            print(f"     reasoning: {review['reasoning'][:150]}")


if __name__ == "__main__":
    main()
