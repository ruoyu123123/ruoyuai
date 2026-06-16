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


def build_review_prompt(original_data: dict, task: str, context: str = "", input_type: str = "json") -> tuple[str, str]:
    """构造 LLM 二次复核 prompt。v22.gov.align.fix: input_type=text 时不包 JSON 块。"""
    system = """你是一位「输出复核员」。

你的工作：拿到一个**待复核内容**（可能是脚本 JSON 输出 / 章节正文 txt / 评估报告等），
根据 task 描述判断这个内容**是否合理 / 是否符合期望**。

复核场景示例：
- 脚本输出 JSON：判断字段值合理性（如把音译名误判成中文名 / 阈值切太严太松）
- 章节正文 txt：判断是否符合作者风格 / 有无 AI 套话 / 句段是否合规
- 评估报告 JSON：判断 issue 列表是真问题还是 false positive

**根据 task 描述的具体复核目标**给出判断，不要拘泥于"input 必须是 JSON"。

**⚠️ 输出格式硬约束**：

只输出**单一 JSON 对象**，**不要**输出任何推理过程 / thinking trace / 自然语言开场白。
**第一个字符必须是 `{`**，**最后一个字符必须是 `}`**。

JSON schema：
```json
{
  "agreement": "agree | disagree | partial",
  "confidence": 0-1,
  "override_recommendation": {"...": "..."} | null,
  "reasoning": "推理：哪里合理 / 哪里不合理 / 为什么"
}
```

**规则**：
- agree = 输出完全合理，无需改动
- disagree = 输出明显错误，必须改（含 override_recommendation 具体值）
- partial = 部分合理，建议补充某些字段
- 严格基于 JSON 内容判断，不要凭空推测
- 不确定时 confidence 标低（< 0.5）让主代理审

**再次强调**：你的回复必须**只**是上述 JSON。如果你有思维链，写到 reasoning 字段里，
不要写在 JSON 外面。reasoning model 也必须把思考过程包装进 reasoning 字段。"""

    # v22.gov.align.notrunc 全局规则：不节省 token · 完整传 input/context
    # 详见 memory feedback-no-token-saving
    user_parts = [f"# 任务描述\n{task}\n"]
    if context:
        user_parts.append(f"# 上下文\n{context}\n")
    if input_type == "text":
        # 文本输入（如章节正文 txt）— 不包 JSON 块
        content = original_data.get("content", "") if isinstance(original_data, dict) else str(original_data)
        user_parts.append(f"# 待复核文本（来自 {original_data.get('_file_path', '?')}）\n{content}")
    else:
        # JSON 输入（如脚本输出报告）
        raw_json = json.dumps(original_data, ensure_ascii=False, indent=2)
        user_parts.append(f"# 原脚本输出 JSON\n```json\n{raw_json}\n```")
    user_parts.append("\n请按规定的 JSON 格式输出复核结论。")
    return system, "\n".join(user_parts)


def call_gen_model(system: str, user: str, profile_lock_path: str | None = None) -> dict | None:
    """调 gen-model 做 AI 复核。fallback 安全：失败返回 None。

    v22.gov.align: 若 profile_lock_path 指向存在的 JSON（含 profile_name），
    校验当前 active profile = lock 中 profile，否则拒绝执行（防 train-test skew）。
    """
    GenModelLoader, GenModelConfigError = safe_load_gen_model_loader()
    if not GenModelLoader:
        return None

    try:
        loader = GenModelLoader()
        profile = loader.get_active_profile()
    except Exception as e:
        print(f"[ai_wrapper] gen-model profile load failed: {e}", file=sys.stderr)
        return None

    # v22.gov.align: profile_lock 强制校验
    if profile_lock_path:
        lock_file = Path(profile_lock_path)
        if lock_file.exists():
            try:
                lock = json.loads(lock_file.read_text(encoding="utf-8"))
                # 兼容 gen_model.py show 输出格式（profile_name 或 active 或顶层 name）
                lock_name = lock.get("profile_name") or lock.get("active") or lock.get("name")
                if lock_name and lock_name != profile.name:
                    msg = (
                        f"❌ [ai_wrapper profile_lock] 当前 active profile='{profile.name}' "
                        f"≠ lock 中 '{lock_name}'（path={profile_lock_path}）。"
                        f"\n请先 `python core/scripts/gen_model.py switch {lock_name}` 切回锁定的 profile，"
                        f"或主代理审阅后用 `python core/scripts/gen_model.py switch <name>` 显式换 + 升 round。"
                        f"\n业界依据：freeze the harness (Arize) · prompt 微差致 76 准确率点波动 (arxiv 2509.01790)。"
                    )
                    print(msg, file=sys.stderr)
                    return {"_profile_lock_violation": True, "lock_name": lock_name, "active_name": profile.name}
            except (json.JSONDecodeError, OSError) as e:
                print(f"[ai_wrapper] profile_lock 读取失败（继续 active profile）: {e}", file=sys.stderr)

    try:
        from openai import OpenAI
        from gen_model_loader import reasoning_extra_body
        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url, timeout=180.0)
        # v22.gov.align.fix: 用 response_format 强制 JSON（OpenAI 兼容 · reasoning model 友好）
        kwargs = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 8000,         # reasoning model 可能 reasoning 段占很多 token
            "temperature": 0.3,
        }
        _xb = reasoning_extra_body(profile)  # reasoning 控制·防 thinking 暴走(elysiver/pie-xian)
        if _xb:
            kwargs["extra_body"] = _xb
        try:
            kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
        except Exception as fmt_err:
            # 部分 provider 不支持 response_format → 退回不强制
            print(f"[ai_wrapper] response_format=json_object 不支持，退回普通模式: {fmt_err}", file=sys.stderr)
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content.strip()
        # 尝试解析 JSON · 加 fallback: 找最后一个 {} 块（reasoning model 可能前面有 thinking）
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                # JSON 嵌套破损 → 找最外层 {} 配对
                pass
        return {"raw_text": raw, "_parse_failed": True}
    except Exception as e:
        print(f"[ai_wrapper] API call failed: {e}", file=sys.stderr)
        return None


def review_output(original_path: Path, task: str, context: str = "",
                  profile_lock_path: str | None = None) -> dict:
    """复核单个脚本输出（支持 JSON 或纯文本如 txt 正文）。

    v22.gov.align.fix: txt input 也要支持（gen_writer 产出 cluster_NNN_draft.txt 不是 JSON）
    """
    if not original_path.exists():
        return {"error": f"file not found: {original_path}"}
    raw = original_path.read_text(encoding="utf-8")
    # 尝试 JSON 解析（脚本输出常见）；失败 → 当纯文本处理（章节正文常见）
    try:
        original = json.loads(raw)
        input_type = "json"
    except (json.JSONDecodeError, ValueError):
        original = {"_input_type": "text", "_file_path": str(original_path), "content": raw}
        input_type = "text"

    system, user = build_review_prompt(original, task, context, input_type=input_type)
    ai_result = call_gen_model(system, user, profile_lock_path=profile_lock_path)
    # v22.gov.align: profile lock 违规直接返回错误
    if isinstance(ai_result, dict) and ai_result.get("_profile_lock_violation"):
        return {
            "error": "profile_lock_violation",
            "active_profile": ai_result.get("active_name"),
            "locked_profile": ai_result.get("lock_name"),
            "fix": "运行 `python core/scripts/gen_model.py switch <locked_name>` 后重试",
        }

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
    parser.add_argument("--profile-lock", help="v22.gov.align profile 锁定文件路径（如 <TEST_ROOT>/_gen_model_profile_locked.json）。当前 active != lock 时拒绝执行")
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

    review = review_output(input_path, args.task, context, profile_lock_path=args.profile_lock)
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
