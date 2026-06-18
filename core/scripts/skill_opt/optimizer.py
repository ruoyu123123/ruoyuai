"""skill_opt.optimizer — 独立 LLM 看 trajectory → 输出 ≤4 条 patch JSON

业界源 (arXiv 2605.23904 §3.2 + Algorithm 1):
- optimizer 与 target 分离 (论文用 GPT-5.5,同款 target-matched 可 recover 56-74%)
- 看 trajectory minibatch (默认 size=8)
- 输出 patch ≤ L_t (textual learning rate=4)
- prompt 头部 prepend reject_buffer (避免重蹈)

本实现复用 gen_model_loader (默认 active profile · 同栈写作 gen-model)。
返回 patches list[dict],下游 patch_applier 消费。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Sequence

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from gen_model_loader import get_default_loader  # noqa: E402
from llm_transport import stream_once, parse_json_loose, TransportError  # noqa: E402

from . import reject_buffer as _reject_buffer  # noqa: E402


OPTIMIZER_SYSTEM = """你是 SkillOpt 编辑器。

你的任务: 看一批 rollout 轨迹 (每条 = skill 文本 × cluster × reward × judge 反馈),
找出能让 reward 提升的 skill.md 编辑建议。

【严格输出规则】
1. 输出 ≤ {max_patches} 条 JSON patch,封装在 ```json 围栏内。
2. patch 三类:
   - add     : 新增一段
   - delete  : 删一段 (anchor + old 必填)
   - replace : 改一段 (anchor + old + new 必填)
3. anchor = 段首前 40 字符精确匹配,用于定位。
4. 不输出整篇 skill 重写。不解释为什么。只给 patch JSON。

【北极星纪律】
- 优化对象=作者风格档 (=第一权威),只压缩冗余 / 修破损口径,不引入新规则。
- 不动量化指纹 (句长/段长/标点基线) 这些 SLOW_UPDATE 受保护段 (上下文会标 [PROTECTED])。
- 失败的编辑方向会在 [REJECT_BUFFER] 段给你,避免重蹈。

【输出格式 (严格)】
```json
{{
  "patches": [
    {{"op": "replace", "anchor": "## 段名 前 40 字", "old": "<完整旧段>", "new": "<完整新段>"}},
    {{"op": "delete",  "anchor": "## 冗余段 前 40 字", "old": "<完整段>"}},
    {{"op": "add",     "after_anchor": "## 锚段 前 40 字", "new": "<新段>"}}
  ]
}}
```

不要任何 markdown 标题/解释/前言/总结,只要 JSON。
"""


def _format_minibatch(trajectories: Sequence[dict], max_preview: int = 400) -> str:
    """把 trajectory minibatch 格式化进 optimizer prompt。

    每条:cluster_id + reward + components + replica 前 max_preview 字。
    """
    lines = ["[ROLLOUT MINIBATCH]"]
    for i, t in enumerate(trajectories, 1):
        cid = t.get("cluster_id", "?")
        r = t.get("reward", 0.0)
        comp = t.get("components", {})
        # 试着读复刻文本前 400 字
        preview = ""
        rp = t.get("replica_path")
        if rp:
            p = Path(rp)
            if p.exists():
                try:
                    preview = p.read_text(encoding="utf-8", errors="replace")[:max_preview]
                except Exception:
                    preview = "<读取失败>"
        lines.append(
            f"--- rollout {i} · cluster={cid} · reward={r:.4f} ---\n"
            f"  signals: audit={comp.get('audit_pass')} reading={comp.get('reading_pass')} "
            f"voice={comp.get('voice_clean')} truth={comp.get('truth_clean')}\n"
            f"  replica_preview: {preview}\n"
        )
    return "\n".join(lines)


def _build_user_prompt(
    skill_text: str,
    trajectories: Sequence[dict],
    protected_sections: Sequence[str] = (),
    rejects: Sequence[dict] = (),
    skill_version: str = "v?",
) -> str:
    """组装 optimizer 的 user prompt。"""
    parts = []

    # 1. reject buffer (论文要求 prepend 头部)
    if rejects:
        parts.append(_reject_buffer.format_for_prompt(rejects))

    # 2. 当前 skill (含 PROTECTED 标记)
    parts.append(f"[CURRENT SKILL · version={skill_version}]")
    if protected_sections:
        parts.append(
            "[PROTECTED 段: 以下段名不可改,只能改其他段]\n  - "
            + "\n  - ".join(protected_sections)
        )
    parts.append("```markdown\n" + skill_text + "\n```")

    # 3. trajectory minibatch
    parts.append(_format_minibatch(trajectories))

    # 4. 任务指令
    parts.append(
        "[任务] 基于以上 rollout,输出 ≤4 条 patch JSON 让下一轮 reward 提升。\n"
        "只输出 ```json {\"patches\": [...]} ```,不输出其他文字。"
    )

    return "\n\n".join(parts)


def _extract_patches(reply: str) -> list[dict]:
    """从 LLM 回复抽 patches list。

    优先级:
    1. ```json {...} ``` 围栏内 JSON
    2. 第一个 { ... } 大括号块
    3. parse_json_loose 兜底
    """
    # 先找 ```json ... ```
    m = re.search(r"```json\s*\n(.*?)\n```", reply, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            patches = data.get("patches", [])
            if isinstance(patches, list):
                return patches
        except json.JSONDecodeError:
            pass

    # 兜底 parse_json_loose
    data = parse_json_loose(reply, fallback={"patches": []})
    patches = data.get("patches", [])
    return patches if isinstance(patches, list) else []


def propose_patches(
    skill_text: str,
    trajectories: Sequence[dict],
    protected_sections: Sequence[str] = (),
    rejects: Sequence[dict] = (),
    skill_version: str = "v?",
    max_patches: int = 4,
    max_tokens: int = 8000,
    temperature: float = 0.3,
    profile=None,
) -> tuple[list[dict], str]:
    """调 LLM 出 patch 建议。

    Args:
        skill_text: 当前 skill.md 全文
        trajectories: 一个 minibatch (典型 size=8)
        protected_sections: SLOW_UPDATE 段名 (LLM 不许改)
        rejects: 本 epoch 已积累的 reject (反哺 prompt 头)
        skill_version: 标记当前 skill 版本号 (写日志)
        max_patches: 论文 L_t=4
        max_tokens: optimizer 输出预算
        temperature: 偏保守 (论文未公开值,经验 0.2-0.5)
        profile: 可注入测试用 fake profile

    Returns:
        (patches, raw_reply): patches 是 ≤max_patches 条 patch dict 列表
    """
    if profile is None:
        profile = get_default_loader().get_active_profile()

    system = OPTIMIZER_SYSTEM.format(max_patches=max_patches)
    user = _build_user_prompt(
        skill_text=skill_text,
        trajectories=trajectories,
        protected_sections=protected_sections,
        rejects=rejects,
        skill_version=skill_version,
    )

    try:
        reply, _finish = stream_once(
            profile=profile,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format_json=True,
        )
    except TransportError as e:
        # 失败 → 空 patch (上层把这轮当 no-op)
        return [], f"<TransportError: {e}>"

    patches = _extract_patches(reply)
    # 硬截断
    if len(patches) > max_patches:
        patches = patches[:max_patches]
    return patches, reply
